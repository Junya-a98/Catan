import json

from game.lan_controller import LanServerController
from game.network_protocol import NETWORK_PROTOCOL_VERSION, build_game_command
from game.server_state import SQLiteRoomAuthorityStore
from game.web_gateway import WebGateway


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
