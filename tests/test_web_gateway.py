import json
import threading

import pytest

import game.network_protocol as network_protocol
from game.network_protocol import NETWORK_PROTOCOL_VERSION
from game.lan_controller import LanServerController, OutboundMessage
from game.network_protocol import build_game_command
from game.web_gateway import (
    MAX_PENDING_WEB_EVENTS,
    WebGateway,
    WebGatewayError,
    WebRateLimits,
)


def message(message_type, **payload):
    return {
        "type": message_type,
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        **payload,
    }


def create_room(gateway, token, *, name="Host"):
    events = gateway.handle(
        token,
        message(
            "create_room",
            display_name=name,
            settings={
                "player_count": 2,
                "victory_target": 5,
                "board_mode": "constrained",
                "board_seed": 4242,
            },
        ),
    )
    welcome = next(event for event in events if event["type"] == "session_welcome")
    return welcome["room_code"], events


def join_room(gateway, token, room_code, *, name="Guest", role="player"):
    return gateway.handle(
        token,
        message(
            "join_room",
            room_code=room_code,
            display_name=name,
            role=role,
        ),
    )


def test_browser_sessions_broadcast_lobby_and_restore_durable_state():
    gateway = WebGateway()
    host = gateway.open_session()
    guest = gateway.open_session()

    room_code, created = create_room(gateway, host)
    joined = join_room(gateway, guest, room_code)
    host_updates = gateway.poll(host)

    initial_welcome = next(
        event for event in created if event["type"] == "session_welcome"
    )
    guest_welcome = next(
        event for event in joined if event["type"] == "session_welcome"
    )
    assert initial_welcome["reconnect_token"] is None
    assert guest_welcome["reconnect_token"] is None
    host_credential = gateway.room_resume_credential(host)
    guest_credential = gateway.room_resume_credential(guest)
    assert host_credential is not None
    assert guest_credential is not None
    assert host_credential.room_code == room_code
    assert guest_credential.room_code == room_code
    assert host_credential.reconnect_token != guest_credential.reconnect_token
    assert host_credential.reconnect_token not in json.dumps(created)
    assert guest_credential.reconnect_token not in json.dumps(joined)
    assert (
        next(event for event in host_updates if event["type"] == "lobby_snapshot")[
            "lobby"
        ]["members"][1]["display_name"]
        == "Guest"
    )

    restored = gateway.bootstrap(host)
    assert [event["type"] for event in restored] == [
        "session_welcome",
        "lobby_snapshot",
    ]
    assert restored[0]["room_code"] == room_code
    assert restored[0]["reconnect_token"] is None
    assert host_credential.reconnect_token not in json.dumps(restored)
    assert restored[1]["lobby"]["revision"] >= 2


def test_browser_resume_bearer_is_redacted_from_every_event_surface():
    gateway = WebGateway()
    token = gateway.open_session()

    _room_code, created = create_room(gateway, token)
    credential = gateway.room_resume_credential(token)

    assert credential is not None
    assert all(
        event.get("reconnect_token") is None
        for event in created
        if event.get("type") == "session_welcome"
    )
    assert credential.reconnect_token not in json.dumps(created)
    assert credential.reconnect_token not in json.dumps(gateway.bootstrap(token))
    assert credential.reconnect_token not in json.dumps(gateway.poll(token))

    assert gateway.handle(token, message("leave_room")) == ()
    assert gateway.room_resume_credential(token) is None


def test_invite_only_room_uses_a_server_generated_hidden_passphrase(monkeypatch):
    gateway = WebGateway()
    host = gateway.open_session(client_key="127.0.0.1")
    guest = gateway.open_session()
    hidden_passphrase = "A" * 43
    monkeypatch.setattr(
        "game.web_gateway.secrets.token_urlsafe",
        lambda size: hidden_passphrase if size == 32 else "unexpected",
    )

    created = gateway.handle(
        host,
        message(
            "create_room",
            display_name="Invite Host",
            settings={
                "player_count": 2,
                "victory_target": 5,
                "board_mode": "constrained",
                "board_seed": 4242,
            },
            invite_only=True,
        ),
        client_key="127.0.0.1",
        protected_room_access_allowed=True,
    )
    welcome = next(
        event for event in created if event["type"] == "session_welcome"
    )
    lobby = next(
        event["lobby"] for event in created if event["type"] == "lobby_snapshot"
    )

    assert lobby["access"] == {"passphrase_required": True}
    assert hidden_passphrase not in json.dumps(created, ensure_ascii=False)
    assert hidden_passphrase not in json.dumps(
        gateway.bootstrap(host, client_key="127.0.0.1"),
        ensure_ascii=False,
    )
    assert hidden_passphrase not in repr(gateway._sessions[host])

    rejected = gateway.handle(
        guest,
        message(
            "join_room",
            room_code=welcome["room_code"],
            display_name="Guest",
            role="player",
        ),
        protected_room_access_allowed=True,
    )
    error = next(event for event in rejected if event["type"] == "request_error")
    assert error["code"] == "authentication_failed"
    assert hidden_passphrase not in json.dumps(rejected, ensure_ascii=False)


@pytest.mark.parametrize("invite_only", [None, 1, "true", []])
def test_invite_only_requires_an_exact_boolean(invite_only):
    gateway = WebGateway()
    host = gateway.open_session(client_key="127.0.0.1")

    with pytest.raises(WebGatewayError) as invalid:
        gateway.handle(
            host,
            message(
                "create_room",
                display_name="Host",
                settings={
                    "player_count": 2,
                    "victory_target": 5,
                    "board_mode": "constrained",
                    "board_seed": 4242,
                },
                invite_only=invite_only,
            ),
            client_key="127.0.0.1",
            protected_room_access_allowed=True,
        )

    assert invalid.value.code == "invalid_request"
    assert gateway.controller.room_codes == ()


def test_invite_only_rejects_ambiguous_passphrase_and_insecure_transport():
    gateway = WebGateway()
    host = gateway.open_session(client_key="192.168.50.10")
    create = message(
        "create_room",
        display_name="Host",
        settings={
            "player_count": 2,
            "victory_target": 5,
            "board_mode": "constrained",
            "board_seed": 4242,
        },
        invite_only=True,
    )

    with pytest.raises(WebGatewayError) as insecure:
        gateway.handle(
            host,
            create,
            client_key="192.168.50.10",
            protected_room_access_allowed=False,
        )
    assert insecure.value.status == 403
    assert insecure.value.code == "secure_transport_required"
    assert gateway.controller.room_codes == ()

    create["passphrase"] = "separate client supplied passphrase"
    with pytest.raises(WebGatewayError) as ambiguous:
        gateway.handle(
            host,
            create,
            client_key="192.168.50.10",
            protected_room_access_allowed=True,
        )
    assert ambiguous.value.code == "invalid_request"
    assert gateway.controller.room_codes == ()


def test_invite_only_false_is_removed_before_open_room_creation():
    gateway = WebGateway()
    host = gateway.open_session()

    events = gateway.handle(
        host,
        message(
            "create_room",
            display_name="Open Host",
            settings={
                "player_count": 2,
                "victory_target": 5,
                "board_mode": "constrained",
                "board_seed": 4242,
            },
            invite_only=False,
        ),
    )

    lobby = next(
        event["lobby"] for event in events if event["type"] == "lobby_snapshot"
    )
    assert lobby["access"] == {"passphrase_required": False}


def test_friend_invitation_claim_is_server_side_and_role_is_authoritative():
    gateway = WebGateway()
    host = gateway.open_session()
    room_code, issued_events = create_room(gateway, host)
    gateway.poll(host)

    issued = gateway.issue_friend_invitation(
        host,
        role="player",
        protected_room_access_allowed=True,
    )
    invite_token = issued["token"]
    assert issued["room_code"] == room_code
    assert issued["role"] == "player"
    assert issued["expires_at_ms"] > issued["issued_at_ms"]
    assert invite_token not in json.dumps(issued_events, ensure_ascii=False)
    assert invite_token not in json.dumps(
        gateway.bootstrap(host),
        ensure_ascii=False,
    )
    assert invite_token not in json.dumps(
        gateway.poll(host),
        ensure_ascii=False,
    )

    guest = gateway.open_session(client_key="127.0.0.2")
    claimed = gateway.claim_friend_invitation(
        guest,
        room_code=room_code,
        invite_token=invite_token,
        client_key="127.0.0.2",
        protected_room_access_allowed=True,
    )
    assert claimed == {
        "room_code": room_code,
        "room_id": issued["room_id"],
        "role": "player",
        "issued_at_ms": issued["issued_at_ms"],
        "expires_at_ms": issued["expires_at_ms"],
    }
    assert invite_token not in json.dumps(claimed, ensure_ascii=False)
    assert invite_token not in repr(gateway._sessions[guest])
    assert gateway.bootstrap(guest, client_key="127.0.0.2") == ()

    joined = gateway.handle(
        guest,
        message(
            "join_room",
            room_code=room_code,
            display_name="Invited Guest",
        ),
        client_key="127.0.0.2",
        protected_room_access_allowed=True,
    )
    welcome = next(
        event for event in joined if event["type"] == "session_welcome"
    )
    assert welcome["role"] == "player"
    assert welcome["seat_index"] == 1
    assert welcome["reconnect_token"] is None
    assert gateway._sessions[guest].pending_friend_invitation is None
    assert invite_token not in json.dumps(joined, ensure_ascii=False)


def test_friend_invitation_tamper_expiry_and_transport_fail_closed():
    now_ms = [1_800_000_000_000]
    controller = LanServerController(wall_clock_ms=lambda: now_ms[0])
    gateway = WebGateway(controller=controller)
    host = gateway.open_session()
    room_code, _events = create_room(gateway, host)
    issued = gateway.issue_friend_invitation(
        host,
        role="spectator",
        protected_room_access_allowed=True,
    )
    token = issued["token"]
    tampered = f"{token[:-1]}{'A' if token[-1] != 'A' else 'B'}"
    guest = gateway.open_session(client_key="127.0.0.2")

    for candidate in (tampered, "short", token):
        if candidate == token:
            now_ms[0] = issued["expires_at_ms"]
        with pytest.raises(WebGatewayError) as invalid:
            gateway.claim_friend_invitation(
                guest,
                room_code=room_code,
                invite_token=candidate,
                client_key="127.0.0.2",
                protected_room_access_allowed=True,
            )
        assert invalid.value.status == 403
        assert invalid.value.code == "authentication_failed"
        assert candidate not in str(invalid.value)

    with pytest.raises(WebGatewayError) as insecure:
        gateway.claim_friend_invitation(
            guest,
            room_code=room_code,
            invite_token=token,
            client_key="127.0.0.2",
            protected_room_access_allowed=False,
        )
    assert insecure.value.code == "secure_transport_required"


def test_only_host_can_issue_and_manual_join_clears_a_pending_invitation():
    gateway = WebGateway()
    host = gateway.open_session()
    room_code, _events = create_room(gateway, host)
    invitation = gateway.issue_friend_invitation(
        host,
        role="spectator",
        protected_room_access_allowed=True,
    )
    guest = gateway.open_session(client_key="127.0.0.2")
    gateway.claim_friend_invitation(
        guest,
        room_code=room_code,
        invite_token=invitation["token"],
        client_key="127.0.0.2",
        protected_room_access_allowed=True,
    )

    # Supplying an ordinary role explicitly cancels the claimed capability
    # and follows the pre-existing room-code join path.
    ordinary = gateway.handle(
        guest,
        message(
            "join_room",
            room_code=room_code,
            display_name="Manual Guest",
            role="player",
        ),
        client_key="127.0.0.2",
        protected_room_access_allowed=True,
    )
    welcome = next(
        event for event in ordinary if event["type"] == "session_welcome"
    )
    assert welcome["role"] == "player"
    assert gateway._sessions[guest].pending_friend_invitation is None

    with pytest.raises(WebGatewayError) as forbidden:
        gateway.issue_friend_invitation(
            guest,
            role="spectator",
            client_key="127.0.0.2",
            protected_room_access_allowed=True,
        )
    assert forbidden.value.status == 403
    assert forbidden.value.code == "forbidden"


def test_host_can_list_and_revoke_invitations_without_exposing_bearers():
    gateway = WebGateway()
    host = gateway.open_session()
    room_code, _events = create_room(gateway, host)
    player_invite = gateway.issue_friend_invitation(
        host,
        role="player",
        protected_room_access_allowed=True,
    )
    spectator_invite = gateway.issue_friend_invitation(
        host,
        role="spectator",
        protected_room_access_allowed=True,
    )

    assert len(player_invite["invitation_id"]) == 22
    active = gateway.list_friend_invitations(
        host,
        protected_room_access_allowed=True,
    )
    assert {item["invitation_id"] for item in active} == {
        player_invite["invitation_id"],
        spectator_invite["invitation_id"],
    }
    assert all(
        set(item)
        == {
            "invitation_id",
            "room_code",
            "role",
            "issued_at_ms",
            "expires_at_ms",
        }
        and item["room_code"] == room_code
        for item in active
    )
    serialized = json.dumps(active, ensure_ascii=False)
    assert player_invite["token"] not in serialized
    assert spectator_invite["token"] not in serialized
    assert "token" not in serialized
    assert "digest" not in serialized
    assert "room_id" not in serialized

    revoked = gateway.revoke_friend_invitation(
        host,
        invitation_id=player_invite["invitation_id"],
        protected_room_access_allowed=True,
    )
    assert revoked == {
        "revoked_count": 1,
        "invitations": (
            next(
                item
                for item in active
                if item["invitation_id"] == spectator_invite["invitation_id"]
            ),
        ),
    }
    assert player_invite["token"] not in json.dumps(revoked, ensure_ascii=False)

    probe = gateway.open_session(client_key="127.0.0.2")
    with pytest.raises(WebGatewayError) as denied:
        gateway.claim_friend_invitation(
            probe,
            room_code=room_code,
            invite_token=player_invite["token"],
            client_key="127.0.0.2",
            protected_room_access_allowed=True,
        )
    assert denied.value.code == "authentication_failed"

    revoked_all = gateway.revoke_all_friend_invitations(
        host,
        protected_room_access_allowed=True,
    )
    assert revoked_all == {"revoked_count": 1, "invitations": ()}
    assert gateway.list_friend_invitations(
        host,
        protected_room_access_allowed=True,
    ) == ()


def test_invitation_management_is_host_only_transport_bound_and_rate_limited():
    limits = WebRateLimits(messages_per_session=3)
    gateway = WebGateway(rate_limits=limits)
    host = gateway.open_session()
    room_code, _events = create_room(gateway, host)
    guest = gateway.open_session(client_key="127.0.0.2")
    gateway.handle(
        guest,
        message(
            "join_room",
            room_code=room_code,
            display_name="Viewer",
            role="spectator",
        ),
        client_key="127.0.0.2",
    )

    with pytest.raises(WebGatewayError) as non_host:
        gateway.list_friend_invitations(
            guest,
            client_key="127.0.0.2",
            protected_room_access_allowed=True,
        )
    assert non_host.value.code == "forbidden"

    with pytest.raises(WebGatewayError) as insecure:
        gateway.revoke_all_friend_invitations(
            host,
            protected_room_access_allowed=False,
        )
    assert insecure.value.code == "secure_transport_required"

    invitation = gateway.issue_friend_invitation(
        host,
        role="spectator",
        protected_room_access_allowed=True,
    )
    gateway.list_friend_invitations(
        host,
        protected_room_access_allowed=True,
    )
    with pytest.raises(WebGatewayError) as limited:
        gateway.revoke_friend_invitation(
            host,
            invitation_id=invitation["invitation_id"],
            protected_room_access_allowed=True,
        )
    assert limited.value.status == 429
    assert limited.value.code == "message_rate_limited"
    assert gateway.controller.inspect_friend_invitation(
        room_code,
        invitation["token"],
    ).invitation_id == invitation["invitation_id"]


def test_one_use_friend_invitation_has_one_concurrent_join_winner():
    gateway = WebGateway()
    host = gateway.open_session()
    room_code, _events = create_room(gateway, host)
    issued = gateway.issue_friend_invitation(
        host,
        role="spectator",
        protected_room_access_allowed=True,
    )
    guests = [
        gateway.open_session(client_key=f"127.0.0.{index}")
        for index in (2, 3)
    ]
    for index, guest in enumerate(guests, start=2):
        gateway.claim_friend_invitation(
            guest,
            room_code=room_code,
            invite_token=issued["token"],
            client_key=f"127.0.0.{index}",
            protected_room_access_allowed=True,
        )

    barrier = threading.Barrier(3)
    outcomes = []

    def join(index, guest):
        barrier.wait()
        try:
            events = gateway.handle(
                guest,
                message(
                    "join_room",
                    room_code=room_code,
                    display_name=f"Spectator {index}",
                ),
                client_key=f"127.0.0.{index}",
                protected_room_access_allowed=True,
            )
        except WebGatewayError as exc:
            outcomes.append(("error", exc.code))
        else:
            outcomes.append(
                (
                    "welcome",
                    next(
                        event["role"]
                        for event in events
                        if event["type"] == "session_welcome"
                    ),
                )
            )

    threads = [
        threading.Thread(target=join, args=(index, guest))
        for index, guest in zip((2, 3), guests)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=3)

    assert sorted(outcomes) == [
        ("error", "authentication_failed"),
        ("welcome", "spectator"),
    ]


def test_web_resume_rotates_without_exposing_old_or_new_bearer():
    gateway = WebGateway()
    original_session = gateway.open_session()
    room_code, _created = create_room(gateway, original_session)
    original = gateway.room_resume_credential(original_session)
    assert original is not None
    original_token = original.reconnect_token

    assert gateway.close_session(original_session)
    resumed_session = gateway.open_session()
    events = gateway.resume_from_cookie(
        resumed_session,
        room_code=room_code,
        reconnect_token=original_token,
    )
    rotated = gateway.room_resume_credential(resumed_session)

    assert rotated is not None
    assert rotated.reconnect_token != original_token
    assert "reconnect_token=" not in repr(rotated)
    assert original_token not in repr(rotated)
    assert rotated.reconnect_token not in repr(rotated)
    serialized = json.dumps(events)
    bootstrap = json.dumps(gateway.bootstrap(resumed_session))
    assert original_token not in serialized
    assert rotated.reconnect_token not in serialized
    assert original_token not in bootstrap
    assert rotated.reconnect_token not in bootstrap
    assert next(
        event for event in events if event["type"] == "session_welcome"
    )["reconnect_token"] is None


def test_lost_web_rotation_can_retry_previous_token_after_disconnect():
    gateway = WebGateway()
    original_session = gateway.open_session()
    room_code, _created = create_room(gateway, original_session)
    original = gateway.room_resume_credential(original_session)
    assert original is not None
    original_token = original.reconnect_token

    assert gateway.close_session(original_session)
    first_retry = gateway.open_session()
    first_events = gateway.resume_from_cookie(
        first_retry,
        room_code=room_code,
        reconnect_token=original_token,
    )
    first_rotation = gateway.room_resume_credential(first_retry)
    assert first_rotation is not None
    assert first_rotation.reconnect_token != original_token

    # Simulate losing the HTTP response that carried first_rotation.  The
    # browser therefore disconnects with only its previous cookie available.
    assert gateway.close_session(first_retry)
    second_retry = gateway.open_session()
    second_events = gateway.resume_from_cookie(
        second_retry,
        room_code=room_code,
        reconnect_token=original_token,
    )
    second_rotation = gateway.room_resume_credential(second_retry)

    assert second_rotation is not None
    assert second_rotation.reconnect_token not in {
        original_token,
        first_rotation.reconnect_token,
    }
    assert next(
        event for event in first_events if event["type"] == "session_welcome"
    )["seat_index"] == 0
    assert next(
        event for event in second_events if event["type"] == "session_welcome"
    )["seat_index"] == 0


def test_lan_style_gateway_reconnect_keeps_the_confirmed_token_stable():
    gateway = WebGateway()
    original_session = gateway.open_session()
    room_code, _created = create_room(gateway, original_session)
    credential = gateway.room_resume_credential(original_session)
    assert credential is not None
    reconnect_token = credential.reconnect_token

    assert gateway.close_session(original_session)
    first_reconnect = gateway.open_session()
    first_events = gateway.handle(
        first_reconnect,
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=reconnect_token,
        ),
    )
    assert gateway.room_resume_credential(first_reconnect) is None
    assert next(
        event for event in first_events if event["type"] == "session_welcome"
    )["seat_index"] == 0

    # The ordinary controller/wire path remains compatible with LAN clients:
    # unlike resume_from_cookie(), it does not rotate the confirmed bearer.
    assert gateway.close_session(first_reconnect)
    second_reconnect = gateway.open_session()
    second_events = gateway.handle(
        second_reconnect,
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=reconnect_token,
        ),
    )
    assert next(
        event for event in second_events if event["type"] == "session_welcome"
    )["seat_index"] == 0


def test_browser_session_is_bound_to_its_server_observed_client():
    gateway = WebGateway()
    token = gateway.open_session(client_key="192.0.2.10")

    assert gateway.has_session(token, client_key="192.0.2.10")
    assert not gateway.has_session(token, client_key="192.0.2.11")
    with pytest.raises(WebGatewayError) as stolen:
        gateway.poll(token, client_key="192.0.2.11")
    assert stolen.value.status == 401
    assert stolen.value.code == "session_expired"
    assert not gateway.close_session(token, client_key="192.0.2.11")
    assert gateway.close_session(token, client_key="192.0.2.10")


def test_rate_limits_sessions_messages_and_room_attempts_across_transports():
    now = [100.0]
    limits = WebRateLimits(
        window_seconds=10,
        session_creations_per_client=2,
        messages_per_session=2,
        heartbeats_per_session=2,
        room_attempts_per_client=1,
    )
    gateway = WebGateway(clock=lambda: now[0], rate_limits=limits)
    first = gateway.open_session(client_key="192.0.2.20")
    gateway.open_session(client_key="192.0.2.20")

    # Restoring an existing token is not a new session creation.
    assert (
        gateway.open_session(first, client_key="192.0.2.20")
        == first
    )
    with pytest.raises(WebGatewayError) as session_limited:
        gateway.open_session(client_key="192.0.2.20")
    assert session_limited.value.status == 429
    assert session_limited.value.code == "session_rate_limited"

    gateway.handle(first, message("unknown"), client_key="192.0.2.20")
    gateway.handle(first, message("unknown"), client_key="192.0.2.20")
    with pytest.raises(WebGatewayError) as message_limited:
        gateway.handle(
            first,
            message("unknown"),
            client_key="192.0.2.20",
        )
    assert message_limited.value.status == 429
    assert message_limited.value.code == "message_rate_limited"

    heartbeat_session = gateway.open_session(client_key="192.0.2.22")
    gateway.handle(
        heartbeat_session,
        message("ping", nonce="one"),
        client_key="192.0.2.22",
    )
    gateway.handle(
        heartbeat_session,
        message("ping", nonce="two"),
        client_key="192.0.2.22",
    )
    with pytest.raises(WebGatewayError) as heartbeat_limited:
        gateway.handle(
            heartbeat_session,
            message("ping", nonce="three"),
            client_key="192.0.2.22",
        )
    assert heartbeat_limited.value.code == "message_rate_limited"

    other_client_session = gateway.open_session(client_key="192.0.2.21")
    with pytest.raises(WebGatewayError) as room_limited:
        gateway.handle(
            other_client_session,
            message(
                "join_room",
                room_code="ABC234",
                display_name="Probe",
                role="player",
            ),
            client_key="192.0.2.21",
        )
        gateway.handle(
            other_client_session,
            message(
                "join_room",
                room_code="ABC235",
                display_name="Probe",
                role="player",
            ),
            client_key="192.0.2.21",
        )
    assert room_limited.value.status == 429
    assert room_limited.value.code == "room_rate_limited"

    now[0] += 10.1
    assert gateway.open_session(client_key="192.0.2.20")


def test_protected_room_transport_capability_is_trusted_and_public_view_is_boolean_only():
    gateway = WebGateway()
    host = gateway.open_session(client_key="127.0.0.1")
    create = message(
        "create_room",
        display_name="Protected Host",
        settings={
            "player_count": 2,
            "victory_target": 5,
            "board_mode": "constrained",
            "board_seed": 4242,
        },
        passphrase="correct horse battery staple",
    )

    blocked = gateway.handle(
        host,
        create,
        client_key="127.0.0.1",
        protected_room_access_allowed=False,
    )
    assert blocked[0]["type"] == "request_error"
    assert blocked[0]["code"] == "secure_transport_required"

    created = gateway.handle(
        host,
        create,
        client_key="127.0.0.1",
        protected_room_access_allowed=True,
    )
    lobby = next(event["lobby"] for event in created if event["type"] == "lobby_snapshot")
    assert lobby["access"] == {"passphrase_required": True}
    serialized = json.dumps(created, ensure_ascii=False)
    assert "correct horse battery staple" not in serialized
    assert "passphrase_hash" not in serialized

    attacker = gateway.open_session(client_key="127.0.0.2")
    crafted = gateway.handle(
        attacker,
        {
            **create,
            "protected_room_access_allowed": True,
        },
        client_key="127.0.0.2",
        protected_room_access_allowed=True,
    )
    assert crafted[0]["type"] == "request_error"
    assert crafted[0]["code"] == "invalid_request"


def test_missing_passphrase_joins_use_strict_pre_kdf_limit_and_publish_retry_time():
    now = [100.0]
    limits = WebRateLimits(
        window_seconds=10,
        session_creations_per_client=20,
        messages_per_session=20,
        heartbeats_per_session=20,
        room_attempts_per_client=20,
        protected_room_attempts_per_client=2,
        protected_room_attempts_global=20,
    )
    gateway = WebGateway(clock=lambda: now[0], rate_limits=limits)
    host = gateway.open_session(client_key="127.0.0.1")
    create = message(
        "create_room",
        display_name="Host",
        settings={
            "player_count": 2,
            "victory_target": 5,
            "board_mode": "constrained",
            "board_seed": 4242,
        },
        passphrase="fifteen characters minimum",
    )
    created = gateway.handle(
        host,
        create,
        client_key="127.0.0.1",
        protected_room_access_allowed=True,
    )
    room_code = next(
        event["room_code"] for event in created if event["type"] == "session_welcome"
    )
    guest = gateway.open_session(client_key="127.0.0.2")
    join = message(
        "join_room",
        room_code=room_code,
        display_name="Guest",
        role="player",
    )

    for _ in range(2):
        denied = gateway.handle(
            guest,
            join,
            client_key="127.0.0.2",
            protected_room_access_allowed=True,
        )
        assert denied[0]["code"] == "authentication_failed"
    with pytest.raises(WebGatewayError) as limited:
        gateway.handle(
            guest,
            join,
            client_key="127.0.0.2",
            protected_room_access_allowed=True,
        )
    assert limited.value.status == 429
    assert limited.value.code == "room_access_rate_limited"
    assert limited.value.retry_after_seconds == 10

    now[0] += 10.1
    retried = gateway.handle(
        guest,
        join,
        client_key="127.0.0.2",
        protected_room_access_allowed=True,
    )
    assert retried[0]["code"] == "authentication_failed"


def test_started_match_snapshots_keep_each_viewers_private_hand_private(monkeypatch):
    private_sentinel = "VARIANT_PRIVATE_SENTINEL_MUST_NOT_ENTER_WEB_EVENTS"
    original_serialize_game = network_protocol.serialize_game

    def serialize_with_private_variant_state(game):
        document = original_serialize_game(game)
        document["variant_state"]["private"] = {
            "server_history": [private_sentinel]
        }
        return document

    monkeypatch.setattr(
        network_protocol,
        "serialize_game",
        serialize_with_private_variant_state,
    )
    gateway = WebGateway()
    host = gateway.open_session()
    guest = gateway.open_session()
    viewer = gateway.open_session()
    room_code, _ = create_room(gateway, host)
    join_room(gateway, guest, room_code)
    join_room(gateway, viewer, room_code, name="Viewer", role="spectator")
    gateway.poll(host)
    gateway.poll(guest)

    gateway.handle(host, message("set_ready", ready=True))
    gateway.handle(guest, message("set_ready", ready=True))
    started = gateway.handle(host, message("start_game"))
    guest_events = gateway.poll(guest)
    viewer_events = gateway.poll(viewer)

    host_state = next(event for event in started if event["type"] == "state_snapshot")
    guest_state = next(
        event for event in guest_events if event["type"] == "state_snapshot"
    )
    viewer_state = next(
        event for event in viewer_events if event["type"] == "state_snapshot"
    )
    assert host_state["viewer_player_index"] == 0
    assert guest_state["viewer_player_index"] == 1
    assert viewer_state["viewer_player_index"] is None
    assert host_state["state"]["players"][0]["resources"] is not None
    assert host_state["state"]["players"][1]["resources"] is None
    assert guest_state["state"]["players"][0]["resources"] is None
    assert guest_state["state"]["players"][1]["resources"] is not None
    assert all(
        player["resources"] is None for player in viewer_state["state"]["players"]
    )
    assert host_state["command_options"] == [{"command": "roll_dice", "args": {}}]
    assert guest_state["command_options"] == []
    assert viewer_state["command_options"] == []

    bootstrap_state = next(
        event
        for event in gateway.bootstrap(host)
        if event["type"] == "state_snapshot"
    )
    replay_frame = gateway.handle(
        host,
        message("replay_frame_request", index=0),
    )[-1]
    for envelope in (
        host_state,
        guest_state,
        viewer_state,
        bootstrap_state,
        replay_frame,
    ):
        encoded = json.dumps(envelope, ensure_ascii=False, allow_nan=False)
        assert private_sentinel not in encoded
        snapshot = envelope.get("snapshot", envelope)
        variant_state = snapshot["state"]["variant_state"]
        assert "private" not in variant_state
        assert set(variant_state) == {
            "format",
            "version",
            "kind",
            "config_fingerprint",
            "public",
        }


def test_reload_bootstrap_keeps_the_consumed_game_command_sequence():
    gateway = WebGateway()
    host = gateway.open_session()
    guest = gateway.open_session()
    room_code, _ = create_room(gateway, host)
    join_room(gateway, guest, room_code)
    gateway.poll(host)
    gateway.poll(guest)
    gateway.handle(host, message("set_ready", ready=True))
    gateway.handle(guest, message("set_ready", ready=True))
    started = gateway.handle(host, message("start_game"))
    state = next(event for event in started if event["type"] == "state_snapshot")

    result = gateway.handle(
        host,
        build_game_command(
            sequence=0,
            expected_revision=state["revision"],
            command="roll_dice",
        ),
    )

    assert next(
        event for event in result if event["type"] == "game_command_result"
    )["accepted"] is True
    welcome = next(
        event for event in gateway.bootstrap(host) if event["type"] == "session_welcome"
    )
    assert welcome["next_sequence"] == 1


def test_web_ai_finish_emits_result_and_authenticated_replay_frames():
    now = [100.0]

    def finish_ai_turn(game):
        game.phase = "finished"
        game.winner = game.players[0]
        return True

    controller = LanServerController(ai_stepper=finish_ai_turn)
    gateway = WebGateway(controller=controller, clock=lambda: now[0])
    host = gateway.open_session()
    created = gateway.handle(
        host,
        message(
            "create_room",
            display_name="Host",
            settings={
                "player_count": 2,
                "ai_player_count": 1,
                "ai_personality_mode": "expansion",
                "victory_target": 5,
                "board_mode": "constrained",
                "board_seed": 4242,
            },
        ),
    )
    lobby = next(
        event["lobby"] for event in created if event["type"] == "lobby_snapshot"
    )
    assert lobby["members"][1]["is_ai"] is True
    assert lobby["members"][1]["ai_personality"] == "expansion"

    gateway.handle(host, message("set_ready", ready=True))
    gateway.handle(host, message("start_game"))
    gateway.handle(
        host,
        build_game_command(
            sequence=0,
            expected_revision=0,
            command="roll_dice",
        ),
    )
    now[0] += 1.1
    gateway.maintain()
    finished = gateway.poll(host)

    snapshot = next(event for event in finished if event["type"] == "state_snapshot")
    result = next(
        event for event in finished if event["type"] == "network_match_result"
    )
    assert snapshot["state"]["phase"]["name"] == "finished"
    assert result["result"]["completed"] is True
    assert result["result"]["winner"] == {"seat": 1, "name": "Host"}
    assert result["replay"]["frame_count"] == 3

    replay = gateway.handle(
        host,
        message("replay_frame_request", index=2),
    )[-1]
    assert replay["type"] == "network_replay_frame"
    assert replay["viewer_player_index"] == 0
    assert replay["snapshot"]["command_options"] == []

    with pytest.raises(WebGatewayError) as spoofed:
        gateway.handle(
            host,
            message(
                "replay_frame_request",
                index=0,
                viewer_player_index=1,
            ),
        )
    assert spoofed.value.code == "invalid_request"


def test_result_capture_failure_is_publicly_reported_without_internal_details(
    monkeypatch,
):
    gateway = WebGateway()
    token = gateway.open_session()
    session = gateway._sessions[token]

    def fail_result(_connection_id):
        raise RuntimeError("private replay implementation detail")

    monkeypatch.setattr(
        gateway.controller,
        "match_result_for_connection",
        fail_result,
    )
    gateway._dispatch(
        (
            OutboundMessage(
                session.connection_id,
                {
                    "type": "state_snapshot",
                    "protocol_version": 1,
                    "revision": 99,
                    "state": {"phase": {"name": "finished"}},
                },
            ),
        )
    )

    events = gateway.poll(token)
    unavailable = next(
        event for event in events if event["type"] == "network_result_unavailable"
    )
    assert "private replay implementation detail" not in unavailable["message"]


def test_successful_leave_forgets_bootstrap_but_errors_do_not():
    gateway = WebGateway()
    token = gateway.open_session()
    create_room(gateway, token)

    failed = gateway.handle(token, message("leave_room", unexpected=True))
    assert failed[0]["type"] == "request_error"
    assert gateway.bootstrap(token)

    assert gateway.handle(token, message("leave_room")) == ()
    assert gateway.bootstrap(token) == ()


def test_new_room_welcome_replaces_closed_match_bootstrap():
    gateway = WebGateway()
    host = gateway.open_session()
    guest = gateway.open_session()
    room_code, _ = create_room(gateway, host)
    join_room(gateway, guest, room_code)
    gateway.poll(host)
    gateway.handle(host, message("set_ready", ready=True))
    gateway.handle(guest, message("set_ready", ready=True))
    gateway.handle(host, message("start_game"))
    gateway.poll(guest)

    closed = gateway.handle(guest, message("leave_room"))
    assert any(event["type"] == "room_closed" for event in closed)
    assert gateway.bootstrap(guest) == ()
    assert any(event["type"] == "room_closed" for event in gateway.poll(host))

    replacement_code, _ = create_room(gateway, guest, name="New Host")
    restored = gateway.bootstrap(guest)
    assert replacement_code != room_code
    assert [event["type"] for event in restored] == [
        "session_welcome",
        "lobby_snapshot",
    ]


def test_pending_snapshots_are_coalesced_and_events_are_bounded():
    gateway = WebGateway()
    host = gateway.open_session()
    guest = gateway.open_session()
    room_code, _ = create_room(gateway, host)
    join_room(gateway, guest, room_code)
    gateway.poll(host)

    for ready in [True, False] * (MAX_PENDING_WEB_EVENTS + 4):
        gateway.handle(host, message("set_ready", ready=ready))

    guest_events = gateway.poll(guest)
    lobby_events = [
        event for event in guest_events if event["type"] == "lobby_snapshot"
    ]
    assert len(guest_events) <= MAX_PENDING_WEB_EVENTS
    assert len(lobby_events) == 1
    assert lobby_events[0]["lobby"]["members"][0]["ready"] is False


def test_expired_and_unknown_sessions_are_rejected():
    now = [10.0]
    gateway = WebGateway(idle_seconds=30, clock=lambda: now[0])
    token = gateway.open_session()
    now[0] += 31

    with pytest.raises(WebGatewayError, match="有効期限") as expired:
        gateway.poll(token)
    assert expired.value.status == 401
    assert not gateway.has_session(token)

    with pytest.raises(WebGatewayError) as missing:
        gateway.bootstrap("unknown")
    assert missing.value.code == "session_expired"
