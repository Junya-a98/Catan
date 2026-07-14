import queue
import threading
import time

import pytest

from game.lan_transport import (
    LanClientTransport,
    LanServerTransport,
    LanTransportError,
)
from game.network_protocol import NETWORK_PROTOCOL_VERSION


def _wait_for_event(transport, kind, *, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.001, deadline - time.monotonic())
        try:
            event = transport.events.get(timeout=remaining)
        except queue.Empty:
            break
        if event.kind == kind:
            return event
    raise AssertionError(f"timed out waiting for {kind}")


@pytest.fixture
def loopback_pair():
    server = LanServerTransport("127.0.0.1", 0)
    server.start()
    client = LanClientTransport()
    client.connect(*server.address)
    connected = _wait_for_event(server, "connected")
    _wait_for_event(client, "connected")
    yield server, client, connected.connection_id
    client.close()
    server.stop()


def test_loopback_moves_complete_framed_messages_in_both_directions(loopback_pair):
    server, client, connection_id = loopback_pair
    client_message = {
        "type": "ping",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "nonce": "client",
    }
    server_message = {
        "type": "pong",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "nonce": "server",
    }

    client.send(client_message)
    received = _wait_for_event(server, "message")
    assert received.connection_id == connection_id
    assert received.message == client_message

    server.send(connection_id, server_message)
    received = _wait_for_event(client, "message")
    assert received.message == server_message


def test_server_broadcasts_to_a_stable_connection_snapshot():
    server = LanServerTransport("127.0.0.1", 0)
    clients = [LanClientTransport(), LanClientTransport()]
    try:
        server.start()
        for client in clients:
            client.connect(*server.address)
        connection_ids = {
            _wait_for_event(server, "connected").connection_id,
            _wait_for_event(server, "connected").connection_id,
        }
        for client in clients:
            _wait_for_event(client, "connected")

        message = {
            "type": "lobby_snapshot",
            "protocol_version": NETWORK_PROTOCOL_VERSION,
            "revision": 3,
        }
        assert server.broadcast(message) == ()
        assert set(server.connection_ids) == connection_ids
        assert [_wait_for_event(client, "message").message for client in clients] == [
            message,
            message,
        ]
    finally:
        for client in clients:
            client.close()
        server.stop()


def test_invalid_protocol_frame_disconnects_only_that_client(loopback_pair):
    server, client, _connection_id = loopback_pair

    client.send({"type": "ping", "protocol_version": 999})

    protocol_error = _wait_for_event(server, "protocol_error")
    disconnected = _wait_for_event(server, "disconnected")
    assert "version" in protocol_error.detail
    assert disconnected.connection_id == protocol_error.connection_id
    _wait_for_event(client, "disconnected")


def test_server_reserves_disconnect_event_when_regular_queue_is_full():
    server = LanServerTransport(
        "127.0.0.1",
        0,
        max_connections=1,
        event_queue_size=1,
    )
    client = LanClientTransport()
    try:
        server.start()
        client.connect(*server.address)

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and server.events.qsize() < 1:
            time.sleep(0.005)
        assert server.events.qsize() == 1

        client.send(
            {
                "type": "ping",
                "protocol_version": NETWORK_PROTOCOL_VERSION,
                "nonce": "overflow",
            }
        )
        while time.monotonic() < deadline and server.events.qsize() < 2:
            time.sleep(0.005)

        events = server.poll(limit=3)
        assert [event.kind for event in events] == ["connected", "disconnected"]
        assert events[1].connection_id == events[0].connection_id
        assert events[1].detail == "application event queue is full"
    finally:
        client.close()
        server.stop()


def test_stopping_server_closes_clients_and_is_idempotent(loopback_pair):
    server, client, _connection_id = loopback_pair

    server.stop()
    _wait_for_event(client, "disconnected")
    server.stop()
    assert not server.is_running
    assert not client.is_connected


def test_transport_rejects_invalid_endpoints_and_sends_while_disconnected():
    with pytest.raises(ValueError):
        LanServerTransport("", 1)
    with pytest.raises(ValueError):
        LanServerTransport("127.0.0.1", True)

    client = LanClientTransport()
    with pytest.raises(LanTransportError, match="not connected"):
        client.send(
            {
                "type": "ping",
                "protocol_version": NETWORK_PROTOCOL_VERSION,
            }
        )


def test_obsolete_client_receive_loop_cannot_close_replacement_connection():
    release_old = threading.Event()

    class DelayedSocket:
        def recv(self, _size):
            release_old.wait(timeout=1)
            raise OSError("old socket closed")

        def shutdown(self, _how):
            return None

        def close(self):
            return None

    client = LanClientTransport()
    old_socket = DelayedSocket()
    replacement_socket = DelayedSocket()
    client._socket = old_socket
    client._running.set()
    thread = threading.Thread(
        target=client._receive_loop,
        args=(old_socket, ("127.0.0.1", 1)),
        daemon=True,
    )
    thread.start()

    with client._state_lock:
        client._socket = replacement_socket
    release_old.set()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert client._socket is replacement_socket
    assert client.is_connected
    assert client.poll() == []
    client.close()
