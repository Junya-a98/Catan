import json

import pytest

from game.lan_controller import LanServerController
from game.network_protocol import NETWORK_PROTOCOL_VERSION, build_game_command
from game.server_state import SQLiteRoomAuthorityStore
from game.web_gateway import WebGateway, WebGatewayError


PASSPHRASE = "persistent protected harbor room"
WALL_CLOCK_MS = 1_800_000_000_000
LOBBY_CLOCK = 1_000.0


def _message(message_type, **payload):
    return {
        "type": message_type,
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        **payload,
    }


def _new_controller(store, *, wall_clock_ms=WALL_CLOCK_MS):
    return LanServerController(
        state_store=store,
        wall_clock_ms=lambda: wall_clock_ms,
        lobby_clock=lambda: LOBBY_CLOCK,
    )


def _new_gateway(controller):
    return WebGateway(controller=controller, clock=lambda: LOBBY_CLOCK)


def _session_welcome(events):
    return next(event for event in events if event["type"] == "session_welcome")


def _lobby_snapshot(events):
    return next(
        event["lobby"] for event in events if event["type"] == "lobby_snapshot"
    )


def _state_snapshot(events):
    return next(event for event in events if event["type"] == "state_snapshot")


def _member_id(gateway, controller, browser_token):
    connection_id = gateway._sessions[browser_token].connection_id
    return controller._sessions[connection_id].member_id


def _paths(tmp_path):
    return tmp_path / "authority.sqlite3", tmp_path / "authority.key"


def test_friend_claim_resumes_in_a_new_gateway_and_consumes_once(tmp_path):
    database, key = _paths(tmp_path)
    store_a = SQLiteRoomAuthorityStore(database, key_path=key)
    try:
        controller_a = _new_controller(store_a)
        gateway_a = _new_gateway(controller_a)
        host = gateway_a.open_session(client_key="127.0.0.1")
        created = gateway_a.handle(
            host,
            _message(
                "create_room",
                display_name="Persistent Host",
                settings={
                    "player_count": 2,
                    "victory_target": 10,
                    "board_mode": "constrained",
                    "board_seed": 4242,
                },
            ),
            client_key="127.0.0.1",
        )
        room_code = _session_welcome(created)["room_code"]
        invitation = gateway_a.issue_friend_invitation(
            host,
            role="player",
            client_key="127.0.0.1",
            protected_room_access_allowed=True,
        )
        guest = gateway_a.open_session(client_key="127.0.0.2")
        claimed = gateway_a.claim_friend_invitation(
            guest,
            room_code=room_code,
            invite_token=invitation["token"],
            client_key="127.0.0.2",
            protected_room_access_allowed=True,
        )
        credential = gateway_a.friend_invitation_claim_credential(
            guest,
            client_key="127.0.0.2",
        )
        assert credential is not None
        claim_token = credential.claim_token
        assert claim_token not in json.dumps(claimed)
        assert invitation["token"] not in repr(gateway_a._sessions[guest])
        assert claim_token not in repr(gateway_a._sessions[guest])
    finally:
        store_a.close()

    store_b = SQLiteRoomAuthorityStore(database, key_path=key)
    try:
        controller_b = _new_controller(store_b, wall_clock_ms=WALL_CLOCK_MS + 1_000)
        gateway_b = _new_gateway(controller_b)
        guest_b = gateway_b.open_session(client_key="127.0.0.2")
        restored = gateway_b.resume_friend_invitation_claim(
            guest_b,
            room_code=room_code,
            claim_token=claim_token,
            client_key="127.0.0.2",
            protected_room_access_allowed=True,
        )
        assert restored == claimed
        assert claim_token not in json.dumps(restored)

        joined = gateway_b.handle(
            guest_b,
            _message(
                "join_room",
                room_code=room_code,
                display_name="Restarted Guest",
            ),
            client_key="127.0.0.2",
            protected_room_access_allowed=True,
        )
        assert _session_welcome(joined)["seat_index"] == 1
        assert gateway_b.friend_invitation_claim_credential(
            guest_b,
            client_key="127.0.0.2",
        ) is None

        replay = gateway_b.open_session(client_key="127.0.0.3")
        with pytest.raises(WebGatewayError) as consumed:
            gateway_b.resume_friend_invitation_claim(
                replay,
                room_code=room_code,
                claim_token=claim_token,
                client_key="127.0.0.3",
                protected_room_access_allowed=True,
            )
        assert getattr(consumed.value, "code", None) == "authentication_failed"
    finally:
        store_b.close()


def test_protected_waiting_room_reconnects_through_reopened_store(tmp_path):
    database, key = _paths(tmp_path)
    store_a = SQLiteRoomAuthorityStore(database, key_path=key)
    try:
        controller_a = _new_controller(store_a)
        gateway_a = _new_gateway(controller_a)
        host_a = gateway_a.open_session(client_key="127.0.0.1")
        created = gateway_a.handle(
            host_a,
            _message(
                "create_room",
                display_name="Persistent Host",
                settings={
                    "player_count": 2,
                    "victory_target": 10,
                    "board_mode": "constrained",
                    "board_seed": 4242,
                },
                passphrase=PASSPHRASE,
            ),
            client_key="127.0.0.1",
            protected_room_access_allowed=True,
        )
        welcome_a = _session_welcome(created)
        lobby_a = _lobby_snapshot(created)
        room_code = welcome_a["room_code"]
        resume_credential = gateway_a.room_resume_credential(
            host_a,
            client_key="127.0.0.1",
        )
        assert resume_credential is not None
        reconnect_token = resume_credential.reconnect_token
        assert welcome_a["reconnect_token"] is None
        member_id_a = _member_id(gateway_a, controller_a, host_a)
        assert lobby_a["access"] == {"passphrase_required": True}
        assert welcome_a["next_sequence"] == 0
    finally:
        store_a.close()

    assert PASSPHRASE.encode("utf-8") not in database.read_bytes()
    store_b = SQLiteRoomAuthorityStore(database, key_path=key)
    try:
        controller_b = _new_controller(store_b, wall_clock_ms=WALL_CLOCK_MS + 1_000)
        gateway_b = _new_gateway(controller_b)
        host_b = gateway_b.open_session(client_key="127.0.0.1")
        restored = gateway_b.handle(
            host_b,
            _message(
                "reconnect_room",
                room_code=room_code,
                reconnect_token=reconnect_token,
            ),
            client_key="127.0.0.1",
            # Reconnect authenticates with its bearer token and deliberately
            # does not retransmit the room passphrase.
            protected_room_access_allowed=False,
        )

        welcome_b = _session_welcome(restored)
        lobby_b = _lobby_snapshot(restored)
        assert welcome_b == {
            "type": "session_welcome",
            "protocol_version": NETWORK_PROTOCOL_VERSION,
            "room_code": room_code,
            "role": "host",
            "seat_index": 0,
            "reconnect_token": None,
            "lobby_revision": lobby_b["revision"],
            "next_sequence": 0,
        }
        assert _member_id(gateway_b, controller_b, host_b) == member_id_a
        assert lobby_b["phase"] == "waiting"
        assert lobby_b["access"] == {"passphrase_required": True}
        assert lobby_b["members"][0]["display_name"] == "Persistent Host"
        assert lobby_b["members"][0]["connected"] is True
        assert PASSPHRASE not in json.dumps(restored, ensure_ascii=False)
    finally:
        store_b.close()


def test_started_real_game_restores_member_sequence_revision_and_state(tmp_path):
    database, key = _paths(tmp_path)
    store_a = SQLiteRoomAuthorityStore(database, key_path=key)
    try:
        controller_a = _new_controller(store_a)
        gateway_a = _new_gateway(controller_a)
        host_a = gateway_a.open_session(client_key="127.0.0.1")
        guest_a = gateway_a.open_session(client_key="127.0.0.2")
        created = gateway_a.handle(
            host_a,
            _message(
                "create_room",
                display_name="Host",
                settings={
                    "player_count": 2,
                    "victory_target": 10,
                    "board_mode": "constrained",
                    "board_seed": 86712347,
                },
            ),
            client_key="127.0.0.1",
        )
        host_welcome = _session_welcome(created)
        room_code = host_welcome["room_code"]
        resume_credential = gateway_a.room_resume_credential(
            host_a,
            client_key="127.0.0.1",
        )
        assert resume_credential is not None
        reconnect_token = resume_credential.reconnect_token
        assert host_welcome["reconnect_token"] is None
        joined = gateway_a.handle(
            guest_a,
            _message(
                "join_room",
                room_code=room_code,
                display_name="Guest",
                role="player",
            ),
            client_key="127.0.0.2",
        )
        assert _session_welcome(joined)["seat_index"] == 1
        gateway_a.poll(host_a, client_key="127.0.0.1")
        gateway_a.poll(guest_a, client_key="127.0.0.2")
        gateway_a.handle(
            host_a,
            _message("set_ready", ready=True),
            client_key="127.0.0.1",
        )
        gateway_a.handle(
            guest_a,
            _message("set_ready", ready=True),
            client_key="127.0.0.2",
        )
        started = gateway_a.handle(
            host_a,
            _message("start_game"),
            client_key="127.0.0.1",
        )
        initial_state = _state_snapshot(started)
        assert initial_state["revision"] == 0
        rolled = gateway_a.handle(
            host_a,
            build_game_command(
                sequence=0,
                expected_revision=initial_state["revision"],
                command="roll_dice",
            ),
            client_key="127.0.0.1",
        )
        result = next(
            event for event in rolled if event["type"] == "game_command_result"
        )
        state_a = _state_snapshot(rolled)
        assert result["accepted"] is True
        assert result["revision"] == state_a["revision"] == 1
        member_id_a = _member_id(gateway_a, controller_a, host_a)
        assert _session_welcome(
            gateway_a.bootstrap(host_a, client_key="127.0.0.1")
        )["next_sequence"] == 1
    finally:
        store_a.close()

    store_b = SQLiteRoomAuthorityStore(database, key_path=key)
    try:
        controller_b = _new_controller(store_b, wall_clock_ms=WALL_CLOCK_MS + 1_000)
        gateway_b = _new_gateway(controller_b)
        host_b = gateway_b.open_session(client_key="127.0.0.1")
        restored = gateway_b.handle(
            host_b,
            _message(
                "reconnect_room",
                room_code=room_code,
                reconnect_token=reconnect_token,
            ),
            client_key="127.0.0.1",
        )

        welcome_b = _session_welcome(restored)
        lobby_b = _lobby_snapshot(restored)
        state_b = _state_snapshot(restored)
        assert welcome_b["room_code"] == room_code
        assert welcome_b["seat_index"] == 0
        assert welcome_b["reconnect_token"] is None
        assert welcome_b["next_sequence"] == 1
        assert _member_id(gateway_b, controller_b, host_b) == member_id_a
        assert lobby_b["phase"] == "started"
        assert state_b["revision"] == state_a["revision"] == 1
        assert state_b["viewer_player_index"] == 0
        assert state_b["state"] == state_a["state"]
        assert state_b["command_options"] == state_a["command_options"]
    finally:
        store_b.close()
