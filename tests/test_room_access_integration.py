import json
import time

import pytest

from game.lan_controller import LanServerController
from game.lan_lobby import (
    LobbyAuthenticationError,
    LobbyRoom,
    RoomSettings,
)
from game.lan_runtime import LanClientSession, LanServerRuntime, _peer_is_loopback
from game.lan_transport import LanTransportError
from game.network_protocol import NETWORK_PROTOCOL_VERSION


PASSPHRASE = "Harbor-Catan-2026-safe"


class DeterministicTokenBytes:
    def __init__(self):
        self.calls = 0

    def __call__(self, size):
        self.calls += 1
        return bytes([self.calls]) * size


def _message(message_type, **payload):
    return {
        "type": message_type,
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        **payload,
    }


def _settings():
    return {
        "player_count": 3,
        "victory_target": 10,
        "board_mode": "constrained",
        "board_seed": 73,
    }


def _create_protected(controller, *, connection_id="host"):
    outbound = controller.handle(
        connection_id,
        _message(
            "create_room",
            display_name="Host",
            settings=_settings(),
            passphrase=PASSPHRASE,
        ),
        protected_room_access_allowed=True,
    )
    welcome = next(
        item.message for item in outbound if item.message["type"] == "session_welcome"
    )
    lobby = next(
        item.message["lobby"]
        for item in outbound
        if item.message["type"] == "lobby_snapshot"
    )
    return welcome, lobby, outbound


def _error(outbound):
    assert len(outbound) == 1
    assert outbound[0].message["type"] == "request_error"
    return outbound[0].message


def _wait(runtime, clients, predicate, *, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runtime.pump()
        for client in clients:
            client.poll()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("timed out waiting for protected LAN state")


def test_lobby_derives_at_create_boundary_and_projects_only_boolean():
    room, _host = LobbyRoom.create(
        RoomSettings(**_settings()),
        host_name="Host",
        connection_id="host",
        code_generator=lambda: "ABC234",
        token_bytes_generator=DeterministicTokenBytes(),
        passphrase=PASSPHRASE,
    )

    snapshot = room.public_snapshot()
    encoded = json.dumps(snapshot, ensure_ascii=False)
    authority_repr = repr(room.__dict__)
    assert snapshot["access"] == {"passphrase_required": True}
    assert set(snapshot["access"]) == {"passphrase_required"}
    assert PASSPHRASE not in encoded
    assert "salt" not in encoded
    assert "digest" not in encoded
    assert PASSPHRASE not in authority_repr
    assert "_salt" not in authority_repr
    assert "_digest" not in authority_repr


def test_lobby_rejects_missing_wrong_and_malformed_before_mutating_membership():
    room, _host = LobbyRoom.create(
        RoomSettings(**_settings()),
        host_name="Host",
        connection_id="host",
        code_generator=lambda: "ABC234",
        token_bytes_generator=DeterministicTokenBytes(),
        passphrase=PASSPHRASE,
    )
    revision = room.revision
    public = room.public_snapshot()

    for candidate in (None, "incorrect-passphrase-2026", {"not": "text"}):
        with pytest.raises(LobbyAuthenticationError):
            room.join_player(
                display_name="Guest",
                connection_id=f"guest-{type(candidate).__name__}",
                passphrase=candidate,
            )
        assert room.revision == revision
        assert room.public_snapshot() == public


def test_lobby_accepts_player_and_spectator_with_matching_nfc_passphrase():
    decomposed = "Cafe\u0301-Catan-room-2026"
    room, _host = LobbyRoom.create(
        RoomSettings(**_settings()),
        host_name="Host",
        connection_id="host",
        code_generator=lambda: "ABC234",
        token_bytes_generator=DeterministicTokenBytes(),
        passphrase=decomposed,
    )

    player = room.join_player(
        display_name="Guest",
        connection_id="guest",
        passphrase="Café-Catan-room-2026",
    )
    spectator = room.join_spectator(
        display_name="Viewer",
        connection_id="viewer",
        passphrase=decomposed,
    )
    assert player.seat == 2
    assert spectator.seat is None


def test_controller_preserves_open_room_wire_and_marks_public_access():
    controller = LanServerController()
    outbound = controller.handle(
        "host",
        _message(
            "create_room",
            display_name="Host",
            settings=_settings(),
        ),
    )

    assert any(item.message["type"] == "session_welcome" for item in outbound)
    lobby = next(
        item.message["lobby"]
        for item in outbound
        if item.message["type"] == "lobby_snapshot"
    )
    assert lobby["access"] == {"passphrase_required": False}


def test_controller_requires_transport_capability_before_accepting_plaintext():
    controller = LanServerController()
    rejected = controller.handle(
        "host",
        _message(
            "create_room",
            display_name="Host",
            settings=_settings(),
            passphrase=PASSPHRASE,
        ),
    )

    assert _error(rejected)["code"] == "secure_transport_required"
    assert controller.room_codes == ()


def test_controller_protected_join_has_generic_authentication_error_and_no_leak():
    controller = LanServerController()
    welcome, lobby, _created = _create_protected(controller)
    room_code = welcome["room_code"]
    assert lobby["access"] == {"passphrase_required": True}

    for index, candidate in enumerate((None, "incorrect-passphrase-2026"), start=1):
        payload = {
            "room_code": room_code,
            "display_name": f"Guest{index}",
            "role": "player",
        }
        if candidate is not None:
            payload["passphrase"] = candidate
        rejected = controller.handle(
            f"guest-{index}",
            _message("join_room", **payload),
            protected_room_access_allowed=True,
        )
        error = _error(rejected)
        assert error["code"] == "authentication_failed"
        assert candidate is None or candidate not in json.dumps(error)

    joined = controller.handle(
        "guest-ok",
        _message(
            "join_room",
            room_code=room_code,
            display_name="Guest",
            role="player",
            passphrase=PASSPHRASE,
        ),
        protected_room_access_allowed=True,
    )
    assert any(item.message["type"] == "session_welcome" for item in joined)


def test_protected_join_rejects_insecure_transport_even_when_secret_is_correct():
    controller = LanServerController()
    welcome, _lobby, _created = _create_protected(controller)

    rejected = controller.handle(
        "guest",
        _message(
            "join_room",
            room_code=welcome["room_code"],
            display_name="Guest",
            role="player",
            passphrase=PASSPHRASE,
        ),
    )
    assert _error(rejected)["code"] == "secure_transport_required"


def test_reconnect_token_bypasses_passphrase_without_transport_capability():
    controller = LanServerController()
    welcome, _lobby, _created = _create_protected(controller)
    room_code = welcome["room_code"]
    joined = controller.handle(
        "guest",
        _message(
            "join_room",
            room_code=room_code,
            display_name="Guest",
            role="player",
            passphrase=PASSPHRASE,
        ),
        protected_room_access_allowed=True,
    )
    guest_welcome = next(
        item.message for item in joined if item.message["type"] == "session_welcome"
    )
    controller.disconnect("guest")

    reconnected = controller.handle(
        "guest-returned",
        _message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=guest_welcome["reconnect_token"],
        ),
    )
    returned_welcome = next(
        item.message
        for item in reconnected
        if item.message["type"] == "session_welcome"
    )
    assert returned_welcome["seat_index"] == 1
    assert returned_welcome["reconnect_token"] is None


def test_reconnect_never_accepts_an_extra_passphrase_field():
    controller = LanServerController()
    welcome, _lobby, _created = _create_protected(controller)

    rejected = controller.handle(
        "attacker",
        _message(
            "reconnect_room",
            room_code=welcome["room_code"],
            reconnect_token=welcome["reconnect_token"],
            passphrase=PASSPHRASE,
        ),
        protected_room_access_allowed=True,
    )
    assert _error(rejected)["code"] == "invalid_request"


def test_client_emits_passphrase_only_at_create_and_join_boundaries():
    class StubTransport:
        is_connected = True
        peer = ("127.0.0.1", 8000)

        def __init__(self):
            self.sent = []

        def send(self, message):
            self.sent.append(message)

    transport = StubTransport()
    session = LanClientSession(transport=transport)
    session.create_room("Host", player_count=3, passphrase=PASSPHRASE)
    session.join_room("ABC234", "Guest", passphrase=PASSPHRASE)

    assert transport.sent[0]["passphrase"] == PASSPHRASE
    assert transport.sent[1]["passphrase"] == PASSPHRASE
    assert "passphrase" not in session.__dict__


def test_client_never_sends_passphrase_over_remote_raw_tcp():
    class RemoteStubTransport:
        is_connected = True
        peer = ("192.168.1.20", 8000)

        def __init__(self):
            self.sent = []

        def send(self, message):
            self.sent.append(message)

    transport = RemoteStubTransport()
    session = LanClientSession(transport=transport)

    with pytest.raises(LanTransportError, match="HTTPS/WSS"):
        session.create_room("Host", player_count=3, passphrase=PASSPHRASE)
    with pytest.raises(LanTransportError, match="HTTPS/WSS"):
        session.join_room("ABC234", "Guest", passphrase=PASSPHRASE)
    assert transport.sent == []


def test_loopback_raw_tcp_can_create_and_join_protected_room_end_to_end():
    runtime = LanServerRuntime("127.0.0.1", 0)
    host = LanClientSession()
    guest = LanClientSession()
    try:
        runtime.start()
        host.connect(*runtime.address)
        guest.connect(*runtime.address)
        _wait(runtime, [host, guest], lambda: host.is_connected and guest.is_connected)
        host.create_room("Host", player_count=2, passphrase=PASSPHRASE)
        _wait(
            runtime,
            [host, guest],
            lambda: host.room_code is not None and host.lobby is not None,
        )
        assert host.lobby["access"] == {"passphrase_required": True}

        guest.join_room(
            host.room_code,
            "Guest",
            passphrase="incorrect-passphrase-2026",
        )
        _wait(
            runtime,
            [host, guest],
            lambda: guest.last_error is not None,
        )
        assert guest.last_error["code"] == "authentication_failed"
        guest.join_room(host.room_code, "Guest", passphrase=PASSPHRASE)
        _wait(
            runtime,
            [host, guest],
            lambda: guest.seat_index == 1 and guest.lobby is not None,
        )
        assert guest.lobby["access"] == {"passphrase_required": True}
    finally:
        host.close()
        guest.close()
        runtime.stop()


@pytest.mark.parametrize(
    ("peer", "expected"),
    [
        (("127.0.0.1", 8000), True),
        (("127.255.255.254", 8000), True),
        (("192.168.1.20", 8000), False),
        (("203.0.113.7", 8000), False),
        (("not-an-ip", 8000), False),
        (None, False),
    ],
)
def test_raw_tcp_room_access_capability_uses_actual_loopback_peer(peer, expected):
    assert _peer_is_loopback(peer) is expected
