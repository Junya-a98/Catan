import socket
import threading
import time

import pytest

from game.custom_map import CustomMapSpec
from game.game_board import GameBoard
from game.house_rules import HouseRules
from game.lan_runtime import LanClientSession, LanServerRuntime
from game.lan_transport import LanTransportError, LanTransportEvent
from game.network_protocol import (
    NETWORK_PROTOCOL_VERSION,
    FrameDecoder,
    build_game_command,
)


def _wait(runtime, clients, predicate, *, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runtime.pump()
        for client in clients:
            client.poll()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("timed out waiting for LAN state")


def test_runtime_create_join_start_command_and_reconnect_round_trip():
    runtime = LanServerRuntime("127.0.0.1", 0)
    host = LanClientSession()
    guest = LanClientSession()
    returned = None
    try:
        runtime.start()
        host.connect(*runtime.address)
        guest.connect(*runtime.address)
        _wait(runtime, [host, guest], lambda: host.is_connected and guest.is_connected)

        host.create_room(
            "Host",
            player_count=2,
            victory_target=5,
            board_seed=86712347,
        )
        _wait(runtime, [host, guest], lambda: host.room_code is not None)
        guest.join_room(host.room_code, "Guest")
        _wait(
            runtime,
            [host, guest],
            lambda: guest.seat_index == 1
            and host.lobby is not None
            and host.lobby["player_members"] == 2,
        )
        guest_token = guest.reconnect_token
        room_code = host.room_code

        host.set_ready()
        guest.set_ready()
        _wait(
            runtime,
            [host, guest],
            lambda: host.lobby is not None and host.lobby["can_start"],
        )
        host.start_game()
        _wait(
            runtime,
            [host, guest],
            lambda: host.game_revision == guest.game_revision == 0,
        )

        sequence = host.send_game_command("roll_dice")
        _wait(
            runtime,
            [host, guest],
            lambda: sequence in host.command_results
            and host.game_revision == guest.game_revision == 1,
        )
        assert host.command_results[sequence]["accepted"] is True
        assert host.game_snapshot["viewer_player_index"] == 0
        assert guest.game_snapshot["viewer_player_index"] == 1

        guest.close()
        _wait(
            runtime,
            [host],
            lambda: any(
                member["seat"] == 2 and not member["connected"]
                for member in host.lobby["members"]
            ),
        )
        returned = LanClientSession()
        returned.connect(*runtime.address)
        returned.reconnect_room(room_code, guest_token)
        _wait(
            runtime,
            [host, returned],
            lambda: returned.seat_index == 1 and returned.game_revision == 1,
        )
        assert returned.reconnect_token == guest_token
        assert returned.next_sequence == 0
    finally:
        host.close()
        guest.close()
        if returned is not None:
            returned.close()
        runtime.stop()


def test_runtime_explicit_leave_releases_seat_and_promotes_remaining_player():
    runtime = LanServerRuntime("127.0.0.1", 0)
    host = LanClientSession()
    guest = LanClientSession()
    replacement = LanClientSession()
    try:
        runtime.start()
        host.connect(*runtime.address)
        guest.connect(*runtime.address)
        replacement.connect(*runtime.address)
        _wait(
            runtime,
            [host, guest, replacement],
            lambda: host.is_connected
            and guest.is_connected
            and replacement.is_connected,
        )
        host.create_room("Host", player_count=2, victory_target=5)
        _wait(runtime, [host, guest], lambda: host.room_code is not None)
        room_code = host.room_code
        guest.join_room(room_code, "Guest")
        _wait(
            runtime,
            [host, guest],
            lambda: guest.seat_index == 1
            and host.lobby is not None
            and host.lobby["player_members"] == 2,
        )

        host.leave_room()
        _wait(
            runtime,
            [host, guest],
            lambda: guest.lobby is not None
            and guest.lobby["player_members"] == 1
            and guest.lobby["members"][0]["role"] == "host",
        )
        assert guest.role == "host"
        replacement.join_room(room_code, "Replacement")
        _wait(
            runtime,
            [guest, replacement],
            lambda: replacement.seat_index == 0
            and guest.lobby is not None
            and guest.lobby["player_members"] == 2,
        )

        seats = {
            member["display_name"]: member["seat"]
            for member in guest.lobby["members"]
        }
        assert seats == {"Replacement": 1, "Guest": 2}
    finally:
        host.close()
        guest.close()
        replacement.close()
        runtime.stop()


def test_runtime_run_forever_stops_cleanly():
    runtime = LanServerRuntime("127.0.0.1", 0)
    stop = threading.Event()
    thread = threading.Thread(
        target=runtime.run_forever,
        args=(stop,),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not runtime.transport.is_running:
        time.sleep(0.005)
    assert runtime.transport.is_running

    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert not runtime.transport.is_running


def test_surrogate_ping_does_not_stop_runtime():
    runtime = LanServerRuntime("127.0.0.1", 0)
    attacker = None
    try:
        runtime.start()
        attacker = socket.create_connection(runtime.address, timeout=1)
        attacker.settimeout(0.02)
        decoder = FrameDecoder()

        def send_raw(payload):
            attacker.sendall(len(payload).to_bytes(4, "big") + payload)

        def wait_for(message_type):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                runtime.pump()
                try:
                    data = attacker.recv(64 * 1024)
                except socket.timeout:
                    time.sleep(0.002)
                    continue
                for received in decoder.feed(data):
                    if received["type"] == message_type:
                        return received
            raise AssertionError(f"timed out waiting for {message_type}")

        send_raw(
            b'{"type":"ping","protocol_version":1,"nonce":"\\ud800"}'
        )
        rejected = wait_for("request_error")
        assert rejected["code"] == "invalid_request"
        assert runtime.transport.is_running

        send_raw(b'{"type":"ping","protocol_version":1,"nonce":"healthy"}')
        pong = wait_for("pong")
        assert pong["nonce"] == "healthy"
        assert runtime.transport.is_running
    finally:
        if attacker is not None:
            attacker.close()
        runtime.stop()


def test_session_welcome_reconciles_to_authoritative_sequence_after_reconnect():
    session = LanClientSession()
    session.next_sequence = 2
    session.pending_commands[0] = {"type": "game_command"}
    session.transport.events.put(
        LanTransportEvent(
            "message",
            message={
                "type": "session_welcome",
                "protocol_version": NETWORK_PROTOCOL_VERSION,
                "room_code": "ABC234",
                "role": "host",
                "seat_index": 0,
                "reconnect_token": None,
                "lobby_revision": 4,
                "next_sequence": 0,
            },
        )
    )

    session.poll()

    assert session.next_sequence == 0
    assert 0 in session.pending_commands


def test_reconnect_blocks_commands_until_welcome_and_fresh_snapshot():
    class StubTransport:
        def __init__(self):
            self.incoming = []
            self.sent = []
            self.is_connected = True

        def send(self, message):
            self.sent.append(message)

        def poll(self, *, limit=100):
            result = self.incoming[:limit]
            del self.incoming[:limit]
            return result

        def close(self):
            self.is_connected = False

    transport = StubTransport()
    session = LanClientSession(transport=transport)
    session.pending_commands[0] = build_game_command(
        sequence=0,
        expected_revision=9,
        command="roll_dice",
    )
    session.next_sequence = 1
    session.role = "host"
    session.seat_index = 0
    session.game_revision = 9
    session._session_welcome_received = True
    session._session_synchronized = True

    session.reconnect_room("ABC234", "private-token")
    assert transport.sent[-1]["type"] == "reconnect_room"
    with pytest.raises(LanTransportError, match="synchronization"):
        session.send_game_command("roll_dice")
    with pytest.raises(LanTransportError, match="synchronization"):
        session.resend_game_command(0)
    assert session.next_sequence == 1

    transport.incoming.append(
        LanTransportEvent(
            "message",
            message={
                "type": "session_welcome",
                "protocol_version": NETWORK_PROTOCOL_VERSION,
                "room_code": "ABC234",
                "role": "host",
                "seat_index": 0,
                "reconnect_token": None,
                "lobby_revision": 4,
                "next_sequence": 0,
            },
        )
    )
    session.poll()
    with pytest.raises(LanTransportError, match="synchronization"):
        session.resend_game_command(0)

    transport.incoming.append(
        LanTransportEvent(
            "message",
            message={
                "type": "state_snapshot",
                "protocol_version": NETWORK_PROTOCOL_VERSION,
                "revision": 9,
            },
        )
    )
    session.poll()
    session.resend_game_command(0)
    assert transport.sent[-1] == session.pending_commands[0]

    transport.incoming.append(
        LanTransportEvent(
            "message",
            message={
                "type": "game_command_result",
                "protocol_version": NETWORK_PROTOCOL_VERSION,
                "sequence": 0,
                "accepted": True,
                "revision": 10,
                "code": None,
                "message": None,
            },
        )
    )
    session.poll()
    assert session.next_sequence == 1
    assert session.game_revision == 9
    assert session.is_synchronized is False
    with pytest.raises(LanTransportError, match="synchronization"):
        session.send_game_command("roll_dice")

    transport.incoming.append(
        LanTransportEvent(
            "message",
            message={
                "type": "state_snapshot",
                "protocol_version": NETWORK_PROTOCOL_VERSION,
                "revision": 10,
            },
        )
    )
    session.poll()
    assert session.game_revision == 10
    assert session.is_synchronized is True
    assert session.send_game_command("roll_dice") == 1


def test_spectator_session_cannot_enqueue_game_commands():
    session = LanClientSession()
    session.role = "spectator"
    session.game_revision = 0
    session._session_synchronized = True

    with pytest.raises(LanTransportError, match="spectators"):
        session.send_game_command("roll_dice")

    assert session.pending_commands == {}
    assert session.next_sequence == 0


def test_client_explicit_leave_uses_versioned_room_command():
    class StubTransport:
        is_connected = True

        def __init__(self):
            self.sent = []

        def send(self, message):
            self.sent.append(message)

        def close(self):
            self.is_connected = False

    transport = StubTransport()
    session = LanClientSession(transport=transport)

    session.leave_room()

    assert transport.sent == [
        {
            "type": "leave_room",
            "protocol_version": NETWORK_PROTOCOL_VERSION,
        }
    ]


def test_client_create_room_canonicalizes_optional_custom_settings():
    class StubTransport:
        is_connected = True

        def __init__(self):
            self.sent = []

        def send(self, message):
            self.sent.append(message)

    custom_map = CustomMapSpec.from_board(GameBoard(seed=1414))
    house_rules = HouseRules(bank_trade_3_to_1=True)
    transport = StubTransport()
    session = LanClientSession(transport=transport)

    session.create_room(
        "Host",
        player_count=2,
        victory_target=8,
        board_mode="custom",
        board_seed=1414,
        custom_map=custom_map,
        house_rules=house_rules,
    )

    assert transport.sent == [
        {
            "type": "create_room",
            "protocol_version": NETWORK_PROTOCOL_VERSION,
            "display_name": "Host",
            "settings": {
                "player_count": 2,
                "victory_target": 8,
                "board_mode": "custom",
                "board_seed": 1414,
                "custom_map": custom_map.to_document(),
                "house_rules": house_rules.to_document(),
            },
        }
    ]
