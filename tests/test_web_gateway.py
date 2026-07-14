import pytest

from game.network_protocol import NETWORK_PROTOCOL_VERSION
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


def test_started_match_snapshots_keep_each_viewers_private_hand_private():
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
