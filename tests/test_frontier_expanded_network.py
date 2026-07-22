"""End-to-end Web/LAN privacy coverage for the expanded frontier catalog."""

from game.frontier import EXPANDED_FRONTIER_CATALOG
from game.lan_controller import LanServerController
from game.network_protocol import NETWORK_PROTOCOL_VERSION
from game.web_gateway import WebGateway


def _message(message_type, **payload):
    return {
        "type": message_type,
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        **payload,
    }


def _event(events, message_type):
    return next(event for event in events if event["type"] == message_type)


def _assert_expanded_frontier_snapshot(snapshot):
    manifest = snapshot["board_manifest"]
    assert manifest["seed"] == 0
    assert len(manifest["tiles"]) == 37
    assert len(manifest["nodes"]) == 96
    assert len(manifest["edges"]) == 132

    revealed = [tile for tile in manifest["tiles"] if tile["revealed"]]
    hidden = [tile for tile in manifest["tiles"] if not tile["revealed"]]
    assert len(revealed) == 7
    assert len(hidden) == 30
    assert all(
        tile["resource"] == "UNKNOWN"
        and tile["number"] is None
        and tile["robber"] is False
        for tile in hidden
    )

    state = snapshot["state"]
    assert state["board"]["seed"] == 0
    assert "private" not in state["variant_state"]
    assert state["variant_state"]["public"] == {
        "catalog": EXPANDED_FRONTIER_CATALOG,
        "revealed_tiles": [
            "0,-1",
            "1,-1",
            "-1,0",
            "0,0",
            "1,0",
            "-1,1",
            "0,1",
        ],
        "discovery_count": 0,
    }


def test_expanded_frontier_survives_web_room_start_reconnect_and_replay():
    """The browser adapter must never expose pre-generated fog contents."""

    gateway = WebGateway(controller=LanServerController())
    host_session = gateway.open_session()
    guest_session = gateway.open_session()

    created = gateway.handle(
        host_session,
        _message(
            "create_room",
            display_name="Host",
            settings={
                "player_count": 2,
                "victory_target": 5,
                "board_mode": "constrained",
                # This browser-supplied seed must be replaced and redacted.
                "board_seed": 4242,
                "variant": {
                    "version": 1,
                    "kind": "frontier",
                    "options": {
                        "catalog": EXPANDED_FRONTIER_CATALOG,
                        "initial_radius": 1,
                        "reveal_rule": "road_adjacent_v1",
                    },
                },
            },
        ),
    )
    welcome = _event(created, "session_welcome")
    room_code = welcome["room_code"]
    resume_credential = gateway.room_resume_credential(host_session)
    assert resume_credential is not None
    reconnect_token = resume_credential.reconnect_token
    assert welcome["reconnect_token"] is None
    lobby = _event(created, "lobby_snapshot")["lobby"]
    assert lobby["settings"]["board_seed"] == 0
    assert lobby["settings"]["variant"]["options"]["catalog"] == (
        EXPANDED_FRONTIER_CATALOG
    )

    gateway.handle(
        guest_session,
        _message(
            "join_room",
            room_code=room_code,
            display_name="Guest",
            role="player",
        ),
    )
    gateway.handle(host_session, _message("set_ready", ready=True))
    gateway.handle(guest_session, _message("set_ready", ready=True))
    started = gateway.handle(host_session, _message("start_game"))
    _assert_expanded_frontier_snapshot(_event(started, "state_snapshot"))

    replayed = gateway.handle(
        host_session,
        _message("replay_frame_request", index=0),
    )
    frame = _event(replayed, "network_replay_frame")
    assert frame["read_only"] is True
    _assert_expanded_frontier_snapshot(frame["snapshot"])

    assert gateway.close_session(host_session) is True
    reconnected_session = gateway.open_session()
    reconnected = gateway.handle(
        reconnected_session,
        _message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=reconnect_token,
        ),
    )
    assert _event(reconnected, "session_welcome")["seat_index"] == 0
    _assert_expanded_frontier_snapshot(_event(reconnected, "state_snapshot"))

    replayed_after_reconnect = gateway.handle(
        reconnected_session,
        _message("replay_frame_request", index=0),
    )
    _assert_expanded_frontier_snapshot(
        _event(replayed_after_reconnect, "network_replay_frame")["snapshot"]
    )
