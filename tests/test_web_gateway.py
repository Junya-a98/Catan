import json

import pytest

import game.network_protocol as network_protocol
from game.network_protocol import NETWORK_PROTOCOL_VERSION
from game.lan_controller import LanServerController, OutboundMessage
from game.network_protocol import build_game_command
from game.web_gateway import (
    MAX_PENDING_WEB_EVENTS,
    WebGateway,
    WebGatewayError,
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

    assert any(event["type"] == "session_welcome" for event in created)
    assert any(event["type"] == "session_welcome" for event in joined)
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
    assert restored[1]["lobby"]["revision"] >= 2


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
