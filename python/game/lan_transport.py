"""Small framed-TCP transports for trusted local-area-network play.

The transport deliberately knows nothing about rooms, players, or game rules.
It only moves versioned messages from :mod:`game.network_protocol` between a
background socket thread and a bounded queue owned by the application.  This
keeps socket callbacks away from the Pygame/rules thread and also makes the
same lobby/authority layer usable from a future WebSocket adapter.

This module is for a trusted LAN.  It does not provide encryption, Internet
authentication, NAT traversal, or public matchmaking.
"""

from __future__ import annotations

from dataclasses import dataclass
import queue
import socket
import threading
from typing import Any, Mapping

from game.network_protocol import FrameDecoder, NetworkProtocolError, encode_frame


DEFAULT_LAN_HOST = "0.0.0.0"
DEFAULT_LAN_PORT = 0
MAX_LAN_CONNECTIONS = 16
MAX_PENDING_EVENTS = 1_024
RECEIVE_CHUNK_BYTES = 64 * 1024
SOCKET_POLL_SECONDS = 0.25


class LanTransportError(RuntimeError):
    """Raised when a LAN socket cannot be started or used safely."""


@dataclass(frozen=True)
class LanTransportEvent:
    """One immutable event delivered to the application thread."""

    kind: str
    connection_id: int | None = None
    message: dict[str, Any] | None = None
    peer: tuple[str, int] | None = None
    detail: str = ""


@dataclass
class _ServerConnection:
    sock: socket.socket
    peer: tuple[str, int]
    send_lock: threading.Lock
    thread: threading.Thread | None = None


def _validated_endpoint(host: str, port: int) -> tuple[str, int]:
    if not isinstance(host, str) or not host.strip() or len(host) > 255:
        raise ValueError("host must be a non-empty string")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    return host.strip(), port


def _copy_message(message: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(message, Mapping):
        raise LanTransportError("LAN message must be an object")
    # ``encode_frame`` performs the authoritative JSON/type/size validation.
    return dict(message)


class LanServerTransport:
    """Threaded IPv4 TCP listener with queue-based message delivery."""

    def __init__(
        self,
        host: str = DEFAULT_LAN_HOST,
        port: int = DEFAULT_LAN_PORT,
        *,
        max_connections: int = MAX_LAN_CONNECTIONS,
        event_queue_size: int = MAX_PENDING_EVENTS,
    ) -> None:
        self._host, self._port = _validated_endpoint(host, port)
        if (
            isinstance(max_connections, bool)
            or not isinstance(max_connections, int)
            or not 1 <= max_connections <= 128
        ):
            raise ValueError("max_connections must be between 1 and 128")
        if (
            isinstance(event_queue_size, bool)
            or not isinstance(event_queue_size, int)
            or event_queue_size <= 0
        ):
            raise ValueError("event_queue_size must be positive")
        self._max_connections = max_connections
        self._regular_event_limit = event_queue_size
        self.events: queue.Queue[LanTransportEvent] = queue.Queue(
            maxsize=event_queue_size + max_connections
        )
        self._event_lock = threading.Lock()
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._connections: dict[int, _ServerConnection] = {}
        self._connections_lock = threading.RLock()
        self._next_connection_id = 1
        self._running = threading.Event()

    @property
    def address(self) -> tuple[str, int]:
        """Return the bound address after :meth:`start`."""

        listener = self._listener
        if listener is None:
            raise LanTransportError("LAN server is not running")
        host, port = listener.getsockname()[:2]
        return str(host), int(port)

    @property
    def connection_ids(self) -> tuple[int, ...]:
        with self._connections_lock:
            return tuple(sorted(self._connections))

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def start(self) -> tuple[str, int]:
        """Bind and start accepting clients; idempotent while running."""

        if self._running.is_set():
            return self.address
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self._host, self._port))
            listener.listen(self._max_connections)
            listener.settimeout(SOCKET_POLL_SECONDS)
        except OSError as exc:
            listener.close()
            raise LanTransportError(f"LAN server could not start: {exc}") from exc
        self._listener = listener
        self._running.set()
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="catan-lan-accept",
            daemon=True,
        )
        self._accept_thread.start()
        return self.address

    def poll(self, *, limit: int = 100) -> list[LanTransportEvent]:
        """Drain at most ``limit`` events without blocking."""

        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be positive")
        result = []
        for _ in range(limit):
            try:
                result.append(self.events.get_nowait())
            except queue.Empty:
                break
        return result

    def send(self, connection_id: int, message: Mapping[str, Any]) -> None:
        """Send one complete frame to a connected client."""

        payload = encode_frame(_copy_message(message))
        with self._connections_lock:
            connection = self._connections.get(connection_id)
        if connection is None:
            raise LanTransportError("LAN connection is no longer available")
        try:
            with connection.send_lock:
                connection.sock.sendall(payload)
        except OSError as exc:
            self.close_connection(connection_id)
            raise LanTransportError(f"LAN send failed: {exc}") from exc

    def broadcast(
        self,
        message: Mapping[str, Any],
        *,
        connection_ids: tuple[int, ...] | list[int] | None = None,
    ) -> tuple[int, ...]:
        """Send to a stable connection snapshot and return failed ids."""

        targets = tuple(connection_ids) if connection_ids is not None else self.connection_ids
        failed = []
        for connection_id in targets:
            try:
                self.send(connection_id, message)
            except LanTransportError:
                failed.append(connection_id)
        return tuple(failed)

    def close_connection(self, connection_id: int) -> bool:
        with self._connections_lock:
            connection = self._connections.pop(connection_id, None)
        if connection is None:
            return False
        _close_socket(connection.sock)
        return True

    def stop(self) -> None:
        """Stop accepting and close all clients without leaking threads."""

        if not self._running.is_set() and self._listener is None:
            return
        self._running.clear()
        listener, self._listener = self._listener, None
        if listener is not None:
            _close_socket(listener)
        with self._connections_lock:
            connections = list(self._connections.values())
            self._connections.clear()
        for connection in connections:
            _close_socket(connection.sock)
        current = threading.current_thread()
        if self._accept_thread is not None and self._accept_thread is not current:
            self._accept_thread.join(timeout=1.5)
        for connection in connections:
            if connection.thread is not None and connection.thread is not current:
                connection.thread.join(timeout=1.5)
        self._accept_thread = None

    def _emit(self, event: LanTransportEvent) -> bool:
        with self._event_lock:
            if self.events.qsize() >= self._regular_event_limit:
                return False
            try:
                self.events.put_nowait(event)
                return True
            except queue.Full:
                return False

    def _emit_terminal(self, event: LanTransportEvent) -> bool:
        """Use the per-connection reserve for a terminal event."""

        with self._event_lock:
            try:
                self.events.put_nowait(event)
                return True
            except queue.Full:
                return False

    def _accept_loop(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while self._running.is_set():
            try:
                client, peer = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self._running.is_set():
                _close_socket(client)
                break
            client.settimeout(SOCKET_POLL_SECONDS)
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._connections_lock:
                if not self._running.is_set():
                    _close_socket(client)
                    break
                if len(self._connections) >= self._max_connections:
                    _close_socket(client)
                    continue
                connection_id = self._next_connection_id
                self._next_connection_id += 1
                connection = _ServerConnection(
                    sock=client,
                    peer=(str(peer[0]), int(peer[1])),
                    send_lock=threading.Lock(),
                )
                self._connections[connection_id] = connection
            thread = threading.Thread(
                target=self._receive_loop,
                args=(connection_id, connection),
                name=f"catan-lan-peer-{connection_id}",
                daemon=True,
            )
            connection.thread = thread
            thread.start()

    def _receive_loop(
        self,
        connection_id: int,
        connection: _ServerConnection,
    ) -> None:
        decoder = FrameDecoder()
        connected_emitted = self._emit(
            LanTransportEvent(
                "connected",
                connection_id=connection_id,
                peer=connection.peer,
            )
        )
        detail = ""
        if connected_emitted:
            while self._running.is_set():
                try:
                    data = connection.sock.recv(RECEIVE_CHUNK_BYTES)
                except socket.timeout:
                    continue
                except OSError as exc:
                    detail = str(exc)
                    break
                if not data:
                    break
                try:
                    messages = decoder.feed(data)
                except NetworkProtocolError as exc:
                    detail = str(exc)
                    self._emit(
                        LanTransportEvent(
                            "protocol_error",
                            connection_id=connection_id,
                            peer=connection.peer,
                            detail=detail,
                        )
                    )
                    break
                for message in messages:
                    if not self._emit(
                        LanTransportEvent(
                            "message",
                            connection_id=connection_id,
                            message=message,
                            peer=connection.peer,
                        )
                    ):
                        detail = "application event queue is full"
                        break
                if detail:
                    break
        elif not detail:
            detail = "application event queue is full"

        with self._connections_lock:
            current = self._connections.get(connection_id)
            if current is connection:
                del self._connections[connection_id]
        _close_socket(connection.sock)
        if connected_emitted:
            self._emit_terminal(
                LanTransportEvent(
                    "disconnected",
                    connection_id=connection_id,
                    peer=connection.peer,
                    detail=detail,
                )
            )

    def __enter__(self) -> LanServerTransport:
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.stop()


class LanClientTransport:
    """One framed TCP client with reconnect-friendly queue semantics."""

    def __init__(self, *, event_queue_size: int = MAX_PENDING_EVENTS) -> None:
        if (
            isinstance(event_queue_size, bool)
            or not isinstance(event_queue_size, int)
            or event_queue_size <= 0
        ):
            raise ValueError("event_queue_size must be positive")
        self._regular_event_limit = event_queue_size
        self.events: queue.Queue[LanTransportEvent] = queue.Queue(
            maxsize=event_queue_size + 1
        )
        self._event_lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._receive_thread: threading.Thread | None = None
        self._running = threading.Event()
        self._peer: tuple[str, int] | None = None

    @property
    def is_connected(self) -> bool:
        with self._state_lock:
            return self._running.is_set() and self._socket is not None

    def connect(self, host: str, port: int, *, timeout: float = 5.0) -> None:
        host, port = _validated_endpoint(host, port)
        if port == 0:
            raise ValueError("client port must not be zero")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be positive")
        with self._state_lock:
            if self._running.is_set() or self._socket is not None:
                raise LanTransportError("LAN client is already connected")
            # Events from a completed connection belong to its old session and
            # must not be applied after reconnecting this transport instance.
            while True:
                try:
                    self.events.get_nowait()
                except queue.Empty:
                    break
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(float(timeout))
                sock.connect((host, port))
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(SOCKET_POLL_SECONDS)
            except OSError as exc:
                sock.close()
                raise LanTransportError(f"LAN connection failed: {exc}") from exc
            peer = sock.getpeername()
            self._socket = sock
            self._peer = (str(peer[0]), int(peer[1]))
            self._running.set()
            self._receive_thread = threading.Thread(
                target=self._receive_loop,
                args=(sock, self._peer),
                name="catan-lan-client",
                daemon=True,
            )
            self._receive_thread.start()
            self._emit(LanTransportEvent("connected", peer=self._peer))

    def send(self, message: Mapping[str, Any]) -> None:
        payload = encode_frame(_copy_message(message))
        with self._state_lock:
            sock = self._socket
            if sock is None or not self._running.is_set():
                raise LanTransportError("LAN client is not connected")
        try:
            with self._send_lock:
                sock.sendall(payload)
        except OSError as exc:
            self.close()
            raise LanTransportError(f"LAN send failed: {exc}") from exc

    def poll(self, *, limit: int = 100) -> list[LanTransportEvent]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be positive")
        result = []
        for _ in range(limit):
            try:
                result.append(self.events.get_nowait())
            except queue.Empty:
                break
        return result

    def close(self) -> None:
        with self._state_lock:
            self._running.clear()
            sock, self._socket = self._socket, None
            thread, self._receive_thread = self._receive_thread, None
        if sock is not None:
            _close_socket(sock)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)

    def _emit(self, event: LanTransportEvent) -> bool:
        with self._event_lock:
            if self.events.qsize() >= self._regular_event_limit:
                return False
            try:
                self.events.put_nowait(event)
                return True
            except queue.Full:
                return False

    def _emit_terminal(self, event: LanTransportEvent) -> bool:
        with self._event_lock:
            try:
                self.events.put_nowait(event)
                return True
            except queue.Full:
                return False

    def _receive_loop(
        self,
        sock: socket.socket,
        peer: tuple[str, int],
    ) -> None:
        decoder = FrameDecoder()
        detail = ""
        while self._running.is_set():
            with self._state_lock:
                if self._socket is not sock:
                    break
            try:
                data = sock.recv(RECEIVE_CHUNK_BYTES)
            except socket.timeout:
                continue
            except OSError as exc:
                detail = str(exc)
                break
            if not data:
                break
            with self._state_lock:
                if self._socket is not sock:
                    break
            try:
                messages = decoder.feed(data)
            except NetworkProtocolError as exc:
                detail = str(exc)
                self._emit(LanTransportEvent("protocol_error", peer=peer, detail=detail))
                break
            for message in messages:
                if not self._emit(
                    LanTransportEvent("message", message=message, peer=peer)
                ):
                    detail = "application event queue is full"
                    break
            if detail:
                break
        # ``close`` may time out joining this thread and a new connection can
        # already be active.  An obsolete receive loop must never clear or
        # close that replacement socket, nor emit a stale disconnect for it.
        with self._state_lock:
            is_current = self._socket is sock
            if is_current:
                self._running.clear()
                self._socket = None
        _close_socket(sock)
        if is_current:
            self._emit_terminal(
                LanTransportEvent("disconnected", peer=peer, detail=detail)
            )

    def __enter__(self) -> LanClientTransport:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


def _close_socket(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


__all__ = (
    "DEFAULT_LAN_HOST",
    "DEFAULT_LAN_PORT",
    "LanClientTransport",
    "LanServerTransport",
    "LanTransportError",
    "LanTransportEvent",
    "MAX_LAN_CONNECTIONS",
)
