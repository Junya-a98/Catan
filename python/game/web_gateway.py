"""Thread-safe browser-session adapter for the authoritative game controller.

The LAN controller already owns room membership, reconnect credentials,
viewer-specific privacy, and exactly-once game commands.  This module adds the
small amount of transport state a browser needs without duplicating any game
rules.  HTTP polling is intentionally the first adapter; a future WebSocket
handler can call the same :class:`WebGateway` methods.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
import secrets
import threading
import time
from typing import Any, Callable, Mapping

from game.lan_controller import LanServerController, OutboundMessage


WEB_API_VERSION = 1
MAX_WEB_SESSIONS = 64
MAX_PENDING_WEB_EVENTS = 48
DEFAULT_WEB_SESSION_IDLE_SECONDS = 6 * 60 * 60

_COALESCED_EVENT_TYPES = frozenset({"lobby_snapshot", "state_snapshot"})
_BOOTSTRAP_EVENT_TYPES = (
    "session_welcome",
    "lobby_snapshot",
    "state_snapshot",
    "room_closed",
)


class WebGatewayError(ValueError):
    """Safe error exposed by the local Web transport."""

    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class _BrowserSession:
    token: str
    connection_id: str
    created_at: float
    last_seen_at: float
    pending: deque[dict[str, Any]] = field(default_factory=deque)
    latest: dict[str, dict[str, Any]] = field(default_factory=dict)


class WebGateway:
    """Adapt browser cookies and event queues to ``LanServerController``.

    All controller access is serialized under one re-entrant lock.  This is
    required because ``ThreadingHTTPServer`` may handle two players at once,
    while a room mutation and its viewer-specific snapshots must stay atomic.
    """

    def __init__(
        self,
        *,
        controller: LanServerController | None = None,
        session_limit: int = MAX_WEB_SESSIONS,
        idle_seconds: float = DEFAULT_WEB_SESSION_IDLE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(session_limit) is not int or not 1 <= session_limit <= 512:
            raise ValueError("session_limit must be 1..512")
        if (
            isinstance(idle_seconds, bool)
            or not isinstance(idle_seconds, (int, float))
            or not 30 <= idle_seconds <= 7 * 24 * 60 * 60
        ):
            raise ValueError("idle_seconds must be 30 seconds..7 days")
        if not callable(clock):
            raise ValueError("clock must be callable")
        self.controller = controller or LanServerController()
        self.session_limit = session_limit
        self.idle_seconds = float(idle_seconds)
        self._clock = clock
        self._sessions: dict[str, _BrowserSession] = {}
        self._connection_tokens: dict[str, str] = {}
        self._lock = threading.RLock()
        self._last_tick = float(clock())

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def open_session(self, existing_token: str | None = None) -> str:
        """Return an existing live token or create a new browser transport."""

        with self._lock:
            now = float(self._clock())
            self._maintain(now)
            if isinstance(existing_token, str):
                existing = self._sessions.get(existing_token)
                if existing is not None:
                    existing.last_seen_at = now
                    return existing.token
            if len(self._sessions) >= self.session_limit:
                raise WebGatewayError(
                    "server_full",
                    "Webセッション数が上限に達しています。",
                    status=503,
                )
            while True:
                token = secrets.token_urlsafe(32)
                if token not in self._sessions:
                    break
            while True:
                connection_id = f"web-{secrets.token_hex(16)}"
                if connection_id not in self._connection_tokens:
                    break
            session = _BrowserSession(
                token=token,
                connection_id=connection_id,
                created_at=now,
                last_seen_at=now,
            )
            self._sessions[token] = session
            self._connection_tokens[connection_id] = token
            return token

    def has_session(self, token: str | None) -> bool:
        with self._lock:
            return isinstance(token, str) and token in self._sessions

    def bootstrap(self, token: str) -> tuple[dict[str, Any], ...]:
        """Return the latest durable room/game events after a page refresh."""

        with self._lock:
            session = self._require_session(token)
            session.last_seen_at = float(self._clock())
            return tuple(
                deepcopy(session.latest[event_type])
                for event_type in _BOOTSTRAP_EVENT_TYPES
                if event_type in session.latest
            )

    def handle(
        self,
        token: str,
        message: Mapping[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        """Apply one browser message and drain events for that browser."""

        if not isinstance(message, Mapping):
            raise WebGatewayError(
                "invalid_request", "messageはobjectで指定してください。"
            )
        with self._lock:
            now = float(self._clock())
            self._maintain(now)
            session = self._require_session(token)
            session.last_seen_at = now
            outbound = self.controller.handle(
                session.connection_id,
                deepcopy(dict(message)),
            )
            self._dispatch(outbound)
            if message.get("type") == "leave_room" and not any(
                item.connection_id == session.connection_id
                and item.message.get("type") == "request_error"
                for item in outbound
            ):
                # A waiting-room departure intentionally has no direct reply.
                # Forget durable snapshots so a later page refresh cannot
                # resurrect the room the browser has just left.  Pending
                # events are still drained below (including room_closed).
                session.latest.clear()
            return self._drain(session)

    def poll(self, token: str) -> tuple[dict[str, Any], ...]:
        """Run maintenance and drain events queued for one browser."""

        with self._lock:
            now = float(self._clock())
            self._maintain(now)
            session = self._require_session(token)
            session.last_seen_at = now
            return self._drain(session)

    def close_session(self, token: str) -> bool:
        """Disconnect a browser while preserving the controller reservation."""

        with self._lock:
            session = self._sessions.pop(token, None)
            if session is None:
                return False
            self._connection_tokens.pop(session.connection_id, None)
            self._dispatch(self.controller.disconnect(session.connection_id))
            return True

    def _maintain(self, now: float) -> None:
        expired = [
            token
            for token, session in self._sessions.items()
            if now - session.last_seen_at >= self.idle_seconds
        ]
        for token in expired:
            self.close_session(token)
        if now - self._last_tick >= 1.0:
            self._dispatch(self.controller.tick())
            self._last_tick = now

    def _dispatch(self, outbound: tuple[OutboundMessage, ...]) -> None:
        for item in outbound:
            token = self._connection_tokens.get(item.connection_id)
            if token is None:
                continue
            session = self._sessions.get(token)
            if session is None:
                continue
            message = deepcopy(item.message)
            message_type = message.get("type")
            if isinstance(message_type, str):
                if message_type == "session_welcome":
                    # Joining a room starts a new durable browser view.  This
                    # also removes a room_closed event from an earlier match.
                    session.latest.clear()
                if message_type in _BOOTSTRAP_EVENT_TYPES:
                    session.latest[message_type] = deepcopy(message)
                if message_type == "room_closed":
                    session.latest.pop("lobby_snapshot", None)
                    session.latest.pop("state_snapshot", None)
            if message_type in _COALESCED_EVENT_TYPES:
                session.pending = deque(
                    event
                    for event in session.pending
                    if event.get("type") != message_type
                )
            session.pending.append(message)
            self._bound_pending(session)

    @staticmethod
    def _bound_pending(session: _BrowserSession) -> None:
        while len(session.pending) > MAX_PENDING_WEB_EVENTS:
            removable = next(
                (
                    index
                    for index, event in enumerate(session.pending)
                    if event.get("type") in _COALESCED_EVENT_TYPES
                ),
                0,
            )
            del session.pending[removable]

    @staticmethod
    def _drain(session: _BrowserSession) -> tuple[dict[str, Any], ...]:
        events = tuple(deepcopy(event) for event in session.pending)
        session.pending.clear()
        return events

    def _require_session(self, token: str) -> _BrowserSession:
        if not isinstance(token, str):
            raise WebGatewayError(
                "session_required",
                "Webセッションを開始してください。",
                status=401,
            )
        session = self._sessions.get(token)
        if session is None:
            raise WebGatewayError(
                "session_expired",
                "Webセッションの有効期限が切れました。",
                status=401,
            )
        return session


__all__ = (
    "DEFAULT_WEB_SESSION_IDLE_SECONDS",
    "MAX_PENDING_WEB_EVENTS",
    "MAX_WEB_SESSIONS",
    "WEB_API_VERSION",
    "WebGateway",
    "WebGatewayError",
)
