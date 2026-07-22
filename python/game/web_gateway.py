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
import math
import secrets
import threading
import time
from typing import Any, Callable, Mapping

from game.lan_controller import (
    LanControllerError,
    LanServerController,
    OutboundMessage,
)
from game.network_replay import NetworkReplayError
from game.shared_rate_limit import (
    RateLimitBucket,
    SharedRateLimitError,
)


WEB_API_VERSION = 1
MAX_WEB_SESSIONS = 64
MAX_PENDING_WEB_EVENTS = 48
DEFAULT_WEB_SESSION_IDLE_SECONDS = 6 * 60 * 60
DEFAULT_FRIEND_INVITATION_TTL_SECONDS = 60 * 60

_COALESCED_EVENT_TYPES = frozenset(
    {
        "lobby_snapshot",
        "state_snapshot",
        "network_match_result",
        "network_result_unavailable",
    }
)
_BOOTSTRAP_EVENT_TYPES = (
    "session_welcome",
    "lobby_snapshot",
    "state_snapshot",
    "network_match_result",
    "network_result_unavailable",
    "room_closed",
)


class WebGatewayError(ValueError):
    """Safe error exposed by the local Web transport."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        retry_after_seconds: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after_seconds = retry_after_seconds


@dataclass
class _BrowserSession:
    token: str
    connection_id: str
    client_key: str
    created_at: float
    last_seen_at: float
    pending: deque[dict[str, Any]] = field(default_factory=deque)
    latest: dict[str, dict[str, Any]] = field(default_factory=dict)
    message_times: deque[float] = field(default_factory=deque)
    heartbeat_times: deque[float] = field(default_factory=deque)
    room_resume: _RoomResumeCredential | None = None
    pending_friend_invitation: _PendingFriendInvitation | None = None


@dataclass(frozen=True)
class _RoomResumeCredential:
    """Server-only copy of the lobby reconnect bearer for one browser.

    The value deliberately has a redacted ``repr``.  It may be read only by
    the HTTP adapter while constructing an HttpOnly cookie and must never be
    inserted into a browser event.
    """

    room_code: str
    reconnect_token: str = field(repr=False)


@dataclass(frozen=True)
class _PendingFriendInvitation:
    """One restart-safe claim held only behind a browser's HttpOnly session.

    The original invitation bearer is exchanged before this object is made.
    Only the separately generated claim capability may remain process-local;
    neither bearer may be reflected into events, bootstrap state, or a
    diagnostic representation.  Its public scope is copied separately so a
    later join cannot substitute another room instance or member role.
    """

    room_code: str
    room_id: str
    role: str
    issued_at_ms: int
    expires_at_ms: int
    claim_token: str = field(repr=False)


@dataclass(frozen=True)
class WebRateLimits:
    """Small in-process abuse limits shared by HTTP and WebSocket traffic.

    These limits are deliberately enforced in the gateway instead of only in
    the HTTP handler.  A client therefore cannot bypass them by switching
    between polling and WebSocket transports.  They are a first defensive
    layer, not a replacement for an Internet-facing proxy/account limiter.
    """

    window_seconds: float = 60.0
    session_creations_per_client: int = 12
    messages_per_session: int = 180
    heartbeats_per_session: int = 300
    room_attempts_per_client: int = 12
    protected_room_attempts_per_client: int = 5
    protected_room_attempts_global: int = 30

    def __post_init__(self) -> None:
        if (
            isinstance(self.window_seconds, bool)
            or not isinstance(self.window_seconds, (int, float))
            or not 1 <= self.window_seconds <= 60 * 60
        ):
            raise ValueError("window_seconds must be 1..3600")
        for name in (
            "session_creations_per_client",
            "messages_per_session",
            "heartbeats_per_session",
            "room_attempts_per_client",
            "protected_room_attempts_per_client",
            "protected_room_attempts_global",
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 100_000:
                raise ValueError(f"{name} must be 1..100000")


@dataclass(frozen=True)
class _RateLimitRule:
    """One private gateway policy mapped onto a shared anonymous bucket."""

    bucket: RateLimitBucket
    code: str
    message: str


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
        rate_limits: WebRateLimits | None = None,
        shared_rate_limit_store: Any | None = None,
        rate_limit_clock: Callable[[], float] | None = None,
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
        if rate_limits is not None and not isinstance(rate_limits, WebRateLimits):
            raise ValueError("rate_limits must be WebRateLimits")
        if shared_rate_limit_store is not None and not callable(
            getattr(shared_rate_limit_store, "consume_many", None)
        ):
            raise ValueError("shared_rate_limit_store must implement consume_many")
        if rate_limit_clock is not None and not callable(rate_limit_clock):
            raise ValueError("rate_limit_clock must be callable")
        self.controller = controller or LanServerController()
        self.session_limit = session_limit
        self.idle_seconds = float(idle_seconds)
        self._clock = clock
        self.rate_limits = rate_limits or WebRateLimits()
        self._shared_rate_limit_store = shared_rate_limit_store
        self._rate_limit_clock = rate_limit_clock or time.time
        self._sessions: dict[str, _BrowserSession] = {}
        self._connection_tokens: dict[str, str] = {}
        self._session_creation_times: dict[str, deque[float]] = {}
        self._room_attempt_times: dict[str, deque[float]] = {}
        self._protected_room_attempt_times: dict[str, deque[float]] = {}
        self._protected_room_attempt_times_global: deque[float] = deque()
        self._lock = threading.RLock()
        self._last_tick = float(clock())

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def open_session(
        self,
        existing_token: str | None = None,
        *,
        client_key: str | None = None,
    ) -> str:
        """Return an existing live token or create a new browser transport."""

        with self._lock:
            now = float(self._clock())
            self._maintain(now)
            normalized_client = self._normalize_client_key(client_key)
            if isinstance(existing_token, str):
                existing = self._sessions.get(existing_token)
                if (
                    existing is not None
                    and secrets.compare_digest(
                        existing.client_key,
                        normalized_client,
                    )
                ):
                    existing.last_seen_at = now
                    return existing.token
            self._consume_session_creation_limit(normalized_client, now)
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
                client_key=normalized_client,
                created_at=now,
                last_seen_at=now,
            )
            self._sessions[token] = session
            self._connection_tokens[connection_id] = token
            return token

    def has_session(
        self,
        token: str | None,
        *,
        client_key: str | None = None,
    ) -> bool:
        with self._lock:
            if not isinstance(token, str):
                return False
            session = self._sessions.get(token)
            if session is None:
                return False
            return secrets.compare_digest(
                session.client_key,
                self._normalize_client_key(client_key),
            )

    def bootstrap(
        self,
        token: str,
        *,
        client_key: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Return the latest durable room/game events after a page refresh."""

        with self._lock:
            session = self._require_session(token, client_key)
            session.last_seen_at = float(self._clock())
            return tuple(
                deepcopy(session.latest[event_type])
                for event_type in _BOOTSTRAP_EVENT_TYPES
                if event_type in session.latest
            )

    def room_resume_credential(
        self,
        token: str,
        *,
        client_key: str | None = None,
    ) -> _RoomResumeCredential | None:
        """Return the credential captured for an HttpOnly response cookie.

        This is an internal transport boundary, not a browser API.  Keeping
        the value attached to the authenticated process-local session lets an
        HTTP create/join response install the cookie without reflecting the
        bearer through JSON or WebSocket frames.
        """

        with self._lock:
            session = self._require_session(token, client_key)
            credential = session.room_resume
            if credential is None:
                return None
            return _RoomResumeCredential(
                room_code=credential.room_code,
                reconnect_token=credential.reconnect_token,
            )

    def clear_room_resume_credential(
        self,
        token: str,
        *,
        client_key: str | None = None,
    ) -> None:
        """Forget the process-local copy after an intentional room leave."""

        with self._lock:
            session = self._require_session(token, client_key)
            session.room_resume = None

    def issue_friend_invitation(
        self,
        token: str,
        *,
        role: str,
        client_key: str | None = None,
        protected_room_access_allowed: bool = False,
    ) -> dict[str, Any]:
        """Issue one short-lived, one-use capability for the current host."""

        self._require_protected_invitation_transport(
            protected_room_access_allowed
        )
        if type(role) is not str or role not in {"player", "spectator"}:
            raise WebGatewayError(
                "invalid_request",
                "招待roleが不正です。",
            )
        with self._lock:
            session = self._invitation_session(
                token,
                client_key=client_key,
                action="issue_friend_invitation",
            )
            try:
                grant = self.controller.issue_friend_invitation(
                    session.connection_id,
                    role=role,
                    ttl_seconds=DEFAULT_FRIEND_INVITATION_TTL_SECONDS,
                )
            except LanControllerError as exc:
                raise self._controller_web_error(exc) from exc
            return {
                "token": grant.token,
                "invitation_id": grant.invitation_id,
                "room_code": self._session_room_code(session),
                "room_id": grant.room_id,
                "role": grant.role,
                "issued_at_ms": grant.issued_at_ms,
                "expires_at_ms": grant.expires_at_ms,
            }

    def list_friend_invitations(
        self,
        token: str,
        *,
        client_key: str | None = None,
        protected_room_access_allowed: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        """Return active token-free invitation metadata to the current host."""

        self._require_protected_invitation_transport(
            protected_room_access_allowed
        )
        with self._lock:
            session = self._invitation_session(
                token,
                client_key=client_key,
                action="list_friend_invitations",
            )
            try:
                summaries = self.controller.list_friend_invitations(
                    session.connection_id
                )
            except LanControllerError as exc:
                raise self._controller_web_error(exc) from exc
            room_code = self._session_room_code(session)
            return tuple(
                self._public_friend_invitation_summary(summary, room_code)
                for summary in summaries
            )

    def revoke_friend_invitation(
        self,
        token: str,
        *,
        invitation_id: object,
        client_key: str | None = None,
        protected_room_access_allowed: bool = False,
    ) -> dict[str, Any]:
        """Revoke one host-managed invite and return its remaining siblings."""

        self._require_protected_invitation_transport(
            protected_room_access_allowed
        )
        with self._lock:
            session = self._invitation_session(
                token,
                client_key=client_key,
                action="revoke_friend_invitation",
            )
            try:
                # Capture the canonical set before mutation so a successful
                # revoke never needs a second fallible persistence operation
                # merely to construct its HTTP acknowledgement.
                before = self.controller.list_friend_invitations(
                    session.connection_id
                )
                revoked = self.controller.revoke_friend_invitation(
                    session.connection_id,
                    invitation_id=invitation_id,
                )
            except LanControllerError as exc:
                raise self._controller_web_error(exc) from exc
            room_code = self._session_room_code(session)
            return {
                "revoked_count": 1,
                "invitations": tuple(
                    self._public_friend_invitation_summary(summary, room_code)
                    for summary in before
                    if summary.invitation_id != revoked.invitation_id
                ),
            }

    def revoke_all_friend_invitations(
        self,
        token: str,
        *,
        client_key: str | None = None,
        protected_room_access_allowed: bool = False,
    ) -> dict[str, Any]:
        """Atomically revoke every active invitation owned by the host."""

        self._require_protected_invitation_transport(
            protected_room_access_allowed
        )
        with self._lock:
            session = self._invitation_session(
                token,
                client_key=client_key,
                action="revoke_all_friend_invitations",
            )
            try:
                revoked_count = self.controller.revoke_all_friend_invitations(
                    session.connection_id
                )
            except LanControllerError as exc:
                raise self._controller_web_error(exc) from exc
            return {
                "revoked_count": revoked_count,
                "invitations": (),
            }

    def claim_friend_invitation(
        self,
        token: str,
        *,
        room_code: str,
        invite_token: str,
        client_key: str | None = None,
        protected_room_access_allowed: bool = False,
    ) -> dict[str, Any]:
        """Exchange a raw invite for a restart-safe claim capability."""

        self._require_protected_invitation_transport(
            protected_room_access_allowed
        )
        with self._lock:
            session = self._invitation_session(
                token,
                client_key=client_key,
                action="claim_friend_invitation",
            )
            self._require_unattached_invitation_session(session)
            try:
                claim = self.controller.begin_friend_invitation_claim(
                    room_code,
                    invite_token,
                )
            except LanControllerError as exc:
                # A claim is a bearer-authentication boundary.  Do not reveal
                # whether its room, token, expiry, or one-use state was wrong.
                if exc.code in {"authentication_failed", "room_not_found"}:
                    raise WebGatewayError(
                        "authentication_failed",
                        "招待情報を確認できませんでした。",
                        status=403,
                    ) from exc
                raise self._controller_web_error(exc) from exc
            pending = _PendingFriendInvitation(
                room_code=room_code,
                room_id=claim.room_id,
                role=claim.role,
                issued_at_ms=claim.issued_at_ms,
                expires_at_ms=claim.expires_at_ms,
                claim_token=claim.claim_token,
            )
            session.pending_friend_invitation = pending
            return self._public_friend_invitation(pending)

    def friend_invitation_claim_credential(
        self,
        token: str,
        *,
        client_key: str | None = None,
    ) -> _PendingFriendInvitation | None:
        """Return a private copy for the HTTP-only claim cookie boundary."""

        with self._lock:
            session = self._require_session(token, client_key)
            pending = session.pending_friend_invitation
            if pending is None:
                return None
            return _PendingFriendInvitation(
                room_code=pending.room_code,
                room_id=pending.room_id,
                role=pending.role,
                issued_at_ms=pending.issued_at_ms,
                expires_at_ms=pending.expires_at_ms,
                claim_token=pending.claim_token,
            )

    def resume_friend_invitation_claim(
        self,
        token: str,
        *,
        room_code: str,
        claim_token: str,
        client_key: str | None = None,
        protected_room_access_allowed: bool = False,
    ) -> dict[str, Any]:
        """Restore one pending invite from its persistent HttpOnly bearer."""

        self._require_protected_invitation_transport(
            protected_room_access_allowed
        )
        with self._lock:
            session = self._invitation_session(
                token,
                client_key=client_key,
                action="resume_friend_invitation_claim",
            )
            self._require_unattached_invitation_session(session)
            try:
                claim = self.controller.inspect_friend_invitation_claim(
                    room_code,
                    claim_token,
                )
            except LanControllerError as exc:
                if exc.code in {"authentication_failed", "room_not_found"}:
                    session.pending_friend_invitation = None
                    raise WebGatewayError(
                        "authentication_failed",
                        "招待情報を確認できませんでした。",
                        status=403,
                    ) from exc
                raise self._controller_web_error(exc) from exc
            pending = _PendingFriendInvitation(
                room_code=room_code,
                room_id=claim.room_id,
                role=claim.role,
                issued_at_ms=claim.issued_at_ms,
                expires_at_ms=claim.expires_at_ms,
                claim_token=claim_token,
            )
            session.pending_friend_invitation = pending
            return self._public_friend_invitation(pending)

    def clear_friend_invitation_claim(
        self,
        token: str,
        *,
        client_key: str | None = None,
    ) -> None:
        """Forget an inspected capability without consuming room authority."""

        with self._lock:
            session = self._require_session(token, client_key)
            session.pending_friend_invitation = None

    def release_friend_invitation_claim(
        self,
        token: str,
        *,
        room_code: str,
        claim_token: str,
        client_key: str | None = None,
        protected_room_access_allowed: bool = False,
    ) -> bool:
        """Release one persistent claim while retaining its parent invite."""

        self._require_protected_invitation_transport(
            protected_room_access_allowed
        )
        with self._lock:
            session = self._invitation_session(
                token,
                client_key=client_key,
                action="release_friend_invitation_claim",
            )
            try:
                self.controller.release_friend_invitation_claim(
                    room_code,
                    claim_token,
                )
            except LanControllerError as exc:
                if exc.code in {"authentication_failed", "room_not_found"}:
                    session.pending_friend_invitation = None
                    return False
                raise self._controller_web_error(exc) from exc
            session.pending_friend_invitation = None
            return True

    def handle(
        self,
        token: str,
        message: Mapping[str, Any],
        *,
        client_key: str | None = None,
        protected_room_access_allowed: bool = False,
        _rotate_reconnect_token: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        """Apply one browser message and drain events for that browser."""

        if not isinstance(message, Mapping):
            raise WebGatewayError(
                "invalid_request", "messageはobjectで指定してください。"
            )
        if type(protected_room_access_allowed) is not bool:
            raise WebGatewayError(
                "invalid_transport_context",
                "通信の保護状態を確認できません。",
            )
        if type(_rotate_reconnect_token) is not bool:
            raise WebGatewayError(
                "invalid_transport_context",
                "再接続資格の更新状態を確認できません。",
            )
        with self._lock:
            now = float(self._clock())
            self._maintain(now)
            session = self._require_session(token, client_key)
            session.last_seen_at = now
            if self._shared_rate_limit_store is not None:
                self._consume_shared_message_limits(session, message)
            else:
                self._consume_message_limit(session, message, now)
            if (
                self._shared_rate_limit_store is None
                and self._is_protected_room_attempt(message)
            ):
                self._consume_client_limit(
                    self._protected_room_attempt_times,
                    session.client_key,
                    now,
                    self.rate_limits.protected_room_attempts_per_client,
                    code="room_access_rate_limited",
                    message=(
                        "入室保護の試行回数が多すぎます。表示された時間を待って"
                        "から再試行してください。"
                    ),
                )
                self._consume_window(
                    self._protected_room_attempt_times_global,
                    now,
                    self.rate_limits.protected_room_attempts_global,
                    code="room_access_rate_limited",
                    message=(
                        "入室保護の試行が集中しています。表示された時間を待って"
                        "から再試行してください。"
                    ),
                )
            if message.get("type") == "replay_frame_request":
                self._validate_replay_frame_request(message)
                try:
                    frame = self.controller.replay_frame_for_connection(
                        session.connection_id,
                        message["index"],
                    )
                except (LanControllerError, NetworkReplayError) as exc:
                    code = exc.code
                    raise WebGatewayError(code, str(exc)) from exc
                except Exception as exc:
                    raise WebGatewayError(
                        "replay_unavailable",
                        "リプレイを読み込めませんでした。",
                    ) from exc
                pending = self._drain(session)
                return (*pending, deepcopy(frame))
            controller_message = self._prepare_controller_message(
                message,
                protected_room_access_allowed=protected_room_access_allowed,
            )
            controller_options: dict[str, Any] = {
                "protected_room_access_allowed": protected_room_access_allowed,
            }
            if _rotate_reconnect_token:
                controller_options["rotate_reconnect_token"] = True
            pending_invitation = session.pending_friend_invitation
            joining_with_invitation = (
                controller_message.get("type") == "join_room"
                and pending_invitation is not None
                and "role" not in controller_message
            )
            if (
                controller_message.get("type") == "join_room"
                and pending_invitation is not None
                and "role" in controller_message
            ):
                # Supplying an ordinary role is an explicit return to the
                # existing room-code/passphrase flow.
                session.pending_friend_invitation = None
                pending_invitation = None
            if joining_with_invitation:
                assert pending_invitation is not None
                outbound = self._join_with_pending_friend_invitation(
                    session,
                    controller_message,
                    pending_invitation,
                )
            else:
                outbound = self.controller.handle(
                    session.connection_id,
                    controller_message,
                    **controller_options,
                )
            self._dispatch(outbound)
            if joining_with_invitation and any(
                item.connection_id == session.connection_id
                and item.message.get("type") == "session_welcome"
                for item in outbound
            ):
                session.pending_friend_invitation = None
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
                session.room_resume = None
            return self._drain(session)

    @staticmethod
    def _prepare_controller_message(
        message: Mapping[str, Any],
        *,
        protected_room_access_allowed: bool,
    ) -> dict[str, Any]:
        """Remove Web-only room policy before crossing the controller boundary.

        An invite-only room reuses the existing, persisted passphrase gate with
        a high-entropy server-generated secret.  The secret is never accepted
        from JavaScript, retained by the gateway, or reflected through an
        event; future invitation redemption is the only Web admission path.
        """

        controller_message = deepcopy(dict(message))
        if message.get("type") != "create_room" or "invite_only" not in message:
            return controller_message
        invite_only = message["invite_only"]
        if type(invite_only) is not bool:
            raise WebGatewayError(
                "invalid_request",
                "invite_onlyはbooleanで指定してください。",
            )
        if "passphrase" in message:
            raise WebGatewayError(
                "invalid_request",
                "招待専用と入室パスフレーズは同時に指定できません。",
            )
        controller_message.pop("invite_only", None)
        if not invite_only:
            return controller_message
        if not protected_room_access_allowed:
            raise WebGatewayError(
                "secure_transport_required",
                "招待専用の部屋はHTTPSまたは同一端末で作成してください。",
                status=403,
            )
        controller_message["passphrase"] = secrets.token_urlsafe(32)
        return controller_message

    def _invitation_session(
        self,
        token: str,
        *,
        client_key: str | None,
        action: str,
    ) -> _BrowserSession:
        now = float(self._clock())
        self._maintain(now)
        session = self._require_session(token, client_key)
        session.last_seen_at = now
        limit_message = {
            "type": action,
            "protocol_version": WEB_API_VERSION,
        }
        if self._shared_rate_limit_store is not None:
            self._consume_shared_message_limits(session, limit_message)
        else:
            self._consume_message_limit(session, limit_message, now)
            if self._is_protected_room_attempt(limit_message):
                self._consume_client_limit(
                    self._protected_room_attempt_times,
                    session.client_key,
                    now,
                    self.rate_limits.protected_room_attempts_per_client,
                    code="room_access_rate_limited",
                    message=(
                        "入室保護の試行回数が多すぎます。表示された時間を待って"
                        "から再試行してください。"
                    ),
                )
                self._consume_window(
                    self._protected_room_attempt_times_global,
                    now,
                    self.rate_limits.protected_room_attempts_global,
                    code="room_access_rate_limited",
                    message=(
                        "入室保護の試行が集中しています。表示された時間を待って"
                        "から再試行してください。"
                    ),
                )
        return session

    def _join_with_pending_friend_invitation(
        self,
        session: _BrowserSession,
        message: Mapping[str, Any],
        pending: _PendingFriendInvitation,
    ) -> tuple[OutboundMessage, ...]:
        expected_fields = {
            "type",
            "protocol_version",
            "room_code",
            "display_name",
        }
        if type(message) is not dict or set(message) != expected_fields:
            raise WebGatewayError(
                "invalid_request",
                "招待参加のfieldが不正です。",
            )
        if message.get("protocol_version") != WEB_API_VERSION:
            raise WebGatewayError(
                "version_mismatch",
                "通信versionが一致しません。",
            )
        if message.get("room_code") != pending.room_code:
            raise WebGatewayError(
                "authentication_failed",
                "招待情報を確認できませんでした。",
                status=403,
            )
        try:
            return self.controller.join_room_with_friend_claim(
                session.connection_id,
                room_code=pending.room_code,
                display_name=message["display_name"],
                claim_token=pending.claim_token,
                expected_room_id=pending.room_id,
            )
        except LanControllerError as exc:
            if exc.code in {"authentication_failed", "room_not_found"}:
                session.pending_friend_invitation = None
                raise WebGatewayError(
                    "authentication_failed",
                    "招待情報を確認できませんでした。",
                    status=403,
                ) from exc
            raise self._controller_web_error(exc) from exc

    @staticmethod
    def _require_protected_invitation_transport(allowed: bool) -> None:
        if type(allowed) is not bool:
            raise WebGatewayError(
                "invalid_transport_context",
                "通信の保護状態を確認できません。",
            )
        if not allowed:
            raise WebGatewayError(
                "secure_transport_required",
                "友人招待はHTTPSまたは同一端末で利用してください。",
                status=403,
            )

    @staticmethod
    def _session_room_code(session: _BrowserSession) -> str:
        welcome = session.latest.get("session_welcome")
        room_code = welcome.get("room_code") if isinstance(welcome, dict) else None
        if type(room_code) is not str:
            raise WebGatewayError(
                "invalid_state",
                "参加中の部屋を確認できませんでした。",
                status=409,
            )
        return room_code

    @staticmethod
    def _require_unattached_invitation_session(session: _BrowserSession) -> None:
        """Never let an invite claim replace an established membership."""

        if "session_welcome" in session.latest:
            raise WebGatewayError(
                "invalid_state",
                "参加中の部屋を退出してから招待を確認してください。",
                status=409,
            )

    @staticmethod
    def _public_friend_invitation(
        pending: _PendingFriendInvitation,
    ) -> dict[str, Any]:
        return {
            "room_code": pending.room_code,
            "room_id": pending.room_id,
            "role": pending.role,
            "issued_at_ms": pending.issued_at_ms,
            "expires_at_ms": pending.expires_at_ms,
        }

    @staticmethod
    def _public_friend_invitation_summary(
        summary: Any,
        room_code: str,
    ) -> dict[str, Any]:
        """Copy only the host-management allowlist across the Web boundary."""

        return {
            "invitation_id": summary.invitation_id,
            "room_code": room_code,
            "role": summary.role,
            "issued_at_ms": summary.issued_at_ms,
            "expires_at_ms": summary.expires_at_ms,
        }

    @staticmethod
    def _controller_web_error(error: LanControllerError) -> WebGatewayError:
        status = {
            "authentication_failed": 403,
            "forbidden": 403,
            "room_not_found": 404,
            "room_full": 409,
            "invalid_state": 409,
            "persistence_unavailable": 503,
            "invitation_not_found": 404,
        }.get(error.code, 400)
        return WebGatewayError(error.code, str(error), status=status)

    def resume_from_cookie(
        self,
        token: str,
        *,
        room_code: str,
        reconnect_token: str,
        client_key: str | None = None,
        protected_room_access_allowed: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        """Use the trusted Web rotation path while charging normal limits."""

        return self.handle(
            token,
            {
                "type": "reconnect_room",
                "protocol_version": WEB_API_VERSION,
                "room_code": room_code,
                "reconnect_token": reconnect_token,
            },
            client_key=client_key,
            protected_room_access_allowed=protected_room_access_allowed,
            _rotate_reconnect_token=True,
        )

    def recover_pending_room_resume(
        self,
        token: str,
        *,
        room_code: str,
        client_key: str | None = None,
    ) -> tuple[dict[str, Any], ...] | None:
        """Replay a committed resume whose private HTTP response was lost."""

        with self._lock:
            now = float(self._clock())
            self._maintain(now)
            session = self._require_session(token, client_key)
            credential = session.room_resume
            if credential is None or credential.room_code != room_code:
                return None
            session.last_seen_at = now
            limit_message = {
                "type": "reconnect_room",
                "protocol_version": WEB_API_VERSION,
                "room_code": room_code,
                "reconnect_token": "server-held",
            }
            if self._shared_rate_limit_store is not None:
                self._consume_shared_message_limits(session, limit_message)
            else:
                self._consume_message_limit(session, limit_message, now)
            return tuple(
                deepcopy(session.latest[event_type])
                for event_type in _BOOTSTRAP_EVENT_TYPES
                if event_type in session.latest
            )

    def confirm_room_resume(
        self,
        token: str,
        *,
        room_code: str,
        reconnect_token: str,
        client_key: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Confirm a delivered HttpOnly token without exposing it as a message."""

        with self._lock:
            now = float(self._clock())
            self._maintain(now)
            session = self._require_session(token, client_key)
            session.last_seen_at = now
            limit_message = {
                "type": "confirm_reconnect",
                "protocol_version": WEB_API_VERSION,
            }
            if self._shared_rate_limit_store is not None:
                self._consume_shared_message_limits(session, limit_message)
            else:
                self._consume_message_limit(session, limit_message, now)
            outbound = self.controller.confirm_reconnect_token(
                session.connection_id,
                room_code,
                reconnect_token,
            )
            self._dispatch(outbound)
            events = self._drain(session)
            if any(event.get("type") == "resume_confirmed" for event in events):
                session.room_resume = None
            return events

    def poll(
        self,
        token: str,
        *,
        client_key: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Run maintenance and drain events queued for one browser."""

        with self._lock:
            now = float(self._clock())
            self._maintain(now)
            session = self._require_session(token, client_key)
            session.last_seen_at = now
            return self._drain(session)

    def maintain(self) -> None:
        """Advance expiry and AI work from the server loop, without a client poll."""

        with self._lock:
            self._maintain(float(self._clock()))

    def close_session(
        self,
        token: str,
        *,
        client_key: str | None = None,
    ) -> bool:
        """Disconnect a browser while preserving the controller reservation."""

        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return False
            if not secrets.compare_digest(
                session.client_key,
                self._normalize_client_key(client_key),
            ):
                return False
            return self._close_session_unchecked(token)

    def _close_session_unchecked(self, token: str) -> bool:
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
            self._close_session_unchecked(token)
        self._prune_client_limits(now)
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
            self._enqueue(session, message)
            if (
                message.get("type") == "state_snapshot"
                and message.get("state", {}).get("phase", {}).get("name") == "finished"
            ):
                try:
                    result = self.controller.match_result_for_connection(
                        item.connection_id
                    )
                except Exception:
                    # Result/replay is a read-only enhancement.  A capture
                    # failure must never interrupt authoritative live state.
                    self._enqueue(
                        session,
                        {
                            "type": "network_result_unavailable",
                            "protocol_version": WEB_API_VERSION,
                            "message": "対局結果とリプレイを読み込めませんでした。",
                        },
                    )
                    continue
                self._enqueue(session, deepcopy(result))

    def _enqueue(self, session: _BrowserSession, message: dict[str, Any]) -> None:
        """Store one already-routed event with coalescing and bootstrap state."""

        message_type = message.get("type")
        if isinstance(message_type, str):
            if message_type == "session_welcome":
                session.pending_friend_invitation = None
                reconnect_token = message.get("reconnect_token")
                room_code = message.get("room_code")
                if isinstance(reconnect_token, str) and isinstance(room_code, str):
                    session.room_resume = _RoomResumeCredential(
                        room_code=room_code,
                        reconnect_token=reconnect_token,
                    )
                # Browser JavaScript never receives the bearer.  This applies
                # to direct responses, pending events, WebSocket frames and
                # reload bootstrap alike; only the HTTP cookie adapter may
                # read the server-side copy captured above.
                message["reconnect_token"] = None
                # Joining a room starts a new durable browser view.  This
                # also removes a room_closed event from an earlier match.
                session.latest.clear()
            if message_type == "network_match_result":
                session.latest.pop("network_result_unavailable", None)
                session.pending = deque(
                    event
                    for event in session.pending
                    if event.get("type") != "network_result_unavailable"
                )
            elif message_type == "network_result_unavailable":
                session.latest.pop("network_match_result", None)
            if message_type in _BOOTSTRAP_EVENT_TYPES:
                durable_message = deepcopy(message)
                session.latest[message_type] = durable_message
            if message_type == "game_command_result":
                self._advance_bootstrap_sequence(session, message)
            if message_type == "room_closed":
                session.latest.pop("lobby_snapshot", None)
                session.latest.pop("state_snapshot", None)
                session.latest.pop("network_match_result", None)
                session.latest.pop("network_result_unavailable", None)
        if message_type in _COALESCED_EVENT_TYPES:
            session.pending = deque(
                event for event in session.pending if event.get("type") != message_type
            )
        session.pending.append(message)
        self._bound_pending(session)

    @staticmethod
    def _advance_bootstrap_sequence(
        session: _BrowserSession,
        message: Mapping[str, Any],
    ) -> None:
        """Keep reload bootstrap aligned with the controller command cursor."""

        welcome = session.latest.get("session_welcome")
        sequence = message.get("sequence")
        if not isinstance(welcome, dict) or isinstance(sequence, bool) or not isinstance(
            sequence, int
        ):
            return
        does_not_consume = {
            "sequence_conflict",
            "sequence_expired",
            "sequence_gap",
        }
        if not message.get("accepted") and message.get("code") in does_not_consume:
            return
        current = welcome.get("next_sequence", 0)
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            current = 0
        welcome["next_sequence"] = max(current, sequence + 1)

    @staticmethod
    def _validate_replay_frame_request(message: Mapping[str, Any]) -> None:
        if type(message) is not dict or set(message) != {
            "type",
            "protocol_version",
            "index",
        }:
            raise WebGatewayError(
                "invalid_request",
                "replay_frame_requestが不正です。",
            )
        if message.get("protocol_version") != WEB_API_VERSION:
            raise WebGatewayError(
                "version_mismatch",
                "通信versionが一致しません。",
            )
        index = message.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise WebGatewayError(
                "invalid_frame",
                "リプレイのフレーム番号が不正です。",
            )

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

    def _consume_session_creation_limit(self, client_key: str, now: float) -> None:
        code = "session_rate_limited"
        message = "Webセッションの作成回数が多すぎます。しばらく待ってください。"
        if self._shared_rate_limit_store is not None:
            self._consume_shared_limits(
                (
                    _RateLimitRule(
                        bucket=RateLimitBucket(
                            scope="web.session.create.client",
                            subject=client_key,
                            maximum=self.rate_limits.session_creations_per_client,
                        ),
                        code=code,
                        message=message,
                    ),
                )
            )
            return
        self._consume_client_limit(
            self._session_creation_times,
            client_key,
            now,
            self.rate_limits.session_creations_per_client,
            code=code,
            message=message,
        )

    def _consume_shared_message_limits(
        self,
        session: _BrowserSession,
        message: Mapping[str, Any],
    ) -> None:
        """Charge every semantic abuse budget in one durable transaction."""

        if message.get("type") == "ping":
            rules = [
                _RateLimitRule(
                    bucket=RateLimitBucket(
                        scope="web.heartbeat.session",
                        subject=session.token,
                        maximum=self.rate_limits.heartbeats_per_session,
                    ),
                    code="message_rate_limited",
                    message=(
                        "通信確認の送信回数が多すぎます。しばらく待ってください。"
                    ),
                )
            ]
        else:
            rules = [
                _RateLimitRule(
                    bucket=RateLimitBucket(
                        scope="web.message.session",
                        subject=session.token,
                        maximum=self.rate_limits.messages_per_session,
                    ),
                    code="message_rate_limited",
                    message="操作の送信回数が多すぎます。しばらく待ってください。",
                )
            ]
        if message.get("type") in {
            "create_room",
            "join_room",
            "reconnect_room",
            "claim_friend_invitation",
            "resume_friend_invitation_claim",
            "release_friend_invitation_claim",
        }:
            rules.append(
                _RateLimitRule(
                    bucket=RateLimitBucket(
                        scope="web.room.attempt.client",
                        subject=session.client_key,
                        maximum=self.rate_limits.room_attempts_per_client,
                    ),
                    code="room_rate_limited",
                    message=(
                        "部屋への参加試行が多すぎます。しばらく待ってください。"
                    ),
                )
            )
        if self._is_protected_room_attempt(message):
            protected_message = (
                "入室保護の試行回数が多すぎます。表示された時間を待って"
                "から再試行してください。"
            )
            rules.extend(
                (
                    _RateLimitRule(
                        bucket=RateLimitBucket(
                            scope="web.room.protected.client",
                            subject=session.client_key,
                            maximum=(
                                self.rate_limits.protected_room_attempts_per_client
                            ),
                        ),
                        code="room_access_rate_limited",
                        message=protected_message,
                    ),
                    _RateLimitRule(
                        bucket=RateLimitBucket(
                            scope="web.room.protected.global",
                            subject="all-clients",
                            maximum=self.rate_limits.protected_room_attempts_global,
                        ),
                        code="room_access_rate_limited",
                        message=(
                            "入室保護の試行が集中しています。表示された時間を待って"
                            "から再試行してください。"
                        ),
                    ),
                )
            )
        self._consume_shared_limits(tuple(rules))

    def _consume_shared_limits(self, rules: tuple[_RateLimitRule, ...]) -> None:
        store = self._shared_rate_limit_store
        if store is None:
            raise RuntimeError("shared rate limit store is unavailable")
        try:
            decision = store.consume_many(
                tuple(rule.bucket for rule in rules),
                now=float(self._rate_limit_clock()),
                window_seconds=float(self.rate_limits.window_seconds),
            )
        except SharedRateLimitError as exc:
            raise WebGatewayError(
                "rate_limit_unavailable",
                "アクセス制限を確認できません。しばらく待ってください。",
                status=503,
            ) from exc
        except Exception as exc:
            # A configured shared limiter is a security boundary.  Never fall
            # back to process-local counters if its backend becomes uncertain.
            raise WebGatewayError(
                "rate_limit_unavailable",
                "アクセス制限を確認できません。しばらく待ってください。",
                status=503,
            ) from exc
        if decision.allowed:
            return
        blocked_index = decision.blocked_index
        retry_after_seconds = decision.retry_after_seconds
        if (
            type(blocked_index) is not int
            or not 0 <= blocked_index < len(rules)
            or type(retry_after_seconds) is not int
            or retry_after_seconds < 1
        ):
            raise WebGatewayError(
                "rate_limit_unavailable",
                "アクセス制限を確認できません。しばらく待ってください。",
                status=503,
            )
        rule = rules[blocked_index]
        raise WebGatewayError(
            rule.code,
            rule.message,
            status=429,
            retry_after_seconds=retry_after_seconds,
        )

    def _consume_message_limit(
        self,
        session: _BrowserSession,
        message: Mapping[str, Any],
        now: float,
    ) -> None:
        if message.get("type") == "ping":
            self._consume_window(
                session.heartbeat_times,
                now,
                self.rate_limits.heartbeats_per_session,
                code="message_rate_limited",
                message="通信確認の送信回数が多すぎます。しばらく待ってください。",
            )
            return
        self._consume_window(
            session.message_times,
            now,
            self.rate_limits.messages_per_session,
            code="message_rate_limited",
            message="操作の送信回数が多すぎます。しばらく待ってください。",
        )
        if message.get("type") in {
            "create_room",
            "join_room",
            "reconnect_room",
            "claim_friend_invitation",
            "resume_friend_invitation_claim",
            "release_friend_invitation_claim",
        }:
            self._consume_client_limit(
                self._room_attempt_times,
                session.client_key,
                now,
                self.rate_limits.room_attempts_per_client,
                code="room_rate_limited",
                message="部屋への参加試行が多すぎます。しばらく待ってください。",
            )

    def _consume_client_limit(
        self,
        store: dict[str, deque[float]],
        client_key: str,
        now: float,
        maximum: int,
        *,
        code: str,
        message: str,
    ) -> None:
        timestamps = store.setdefault(client_key, deque())
        self._consume_window(
            timestamps,
            now,
            maximum,
            code=code,
            message=message,
        )

    def _consume_window(
        self,
        timestamps: deque[float],
        now: float,
        maximum: int,
        *,
        code: str,
        message: str,
    ) -> None:
        cutoff = now - float(self.rate_limits.window_seconds)
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()
        if len(timestamps) >= maximum:
            retry_after_seconds = max(
                1,
                math.ceil(float(self.rate_limits.window_seconds) - (now - timestamps[0])),
            )
            raise WebGatewayError(
                code,
                message,
                status=429,
                retry_after_seconds=retry_after_seconds,
            )
        timestamps.append(now)

    def _prune_client_limits(self, now: float) -> None:
        cutoff = now - float(self.rate_limits.window_seconds)
        for store in (
            self._session_creation_times,
            self._room_attempt_times,
            self._protected_room_attempt_times,
        ):
            for client_key, timestamps in tuple(store.items()):
                while timestamps and timestamps[0] <= cutoff:
                    timestamps.popleft()
                if not timestamps:
                    store.pop(client_key, None)
        while (
            self._protected_room_attempt_times_global
            and self._protected_room_attempt_times_global[0] <= cutoff
        ):
            self._protected_room_attempt_times_global.popleft()

    @staticmethod
    def _is_protected_room_attempt(message: Mapping[str, Any]) -> bool:
        message_type = message.get("type")
        return message_type in {
            "join_room",
            "claim_friend_invitation",
            "resume_friend_invitation_claim",
            "release_friend_invitation_claim",
        } or (
            message_type == "create_room"
            and ("passphrase" in message or message.get("invite_only") is True)
        )

    @staticmethod
    def _normalize_client_key(client_key: str | None) -> str:
        if client_key is None:
            return "embedded"
        if (
            not isinstance(client_key, str)
            or not client_key
            or len(client_key) > 128
            or any(character.isspace() for character in client_key)
        ):
            raise WebGatewayError(
                "invalid_client",
                "接続元を確認できませんでした。",
                status=400,
            )
        return client_key

    def _require_session(
        self,
        token: str,
        client_key: str | None = None,
    ) -> _BrowserSession:
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
        if not secrets.compare_digest(
            session.client_key,
            self._normalize_client_key(client_key),
        ):
            # Do not reveal whether a bearer cookie belongs to a different
            # peer.  A new browser session can still use its separate room
            # reconnect token to reclaim the reserved seat.
            raise WebGatewayError(
                "session_expired",
                "Webセッションの有効期限が切れました。",
                status=401,
            )
        return session


__all__ = (
    "DEFAULT_FRIEND_INVITATION_TTL_SECONDS",
    "DEFAULT_WEB_SESSION_IDLE_SECONDS",
    "MAX_PENDING_WEB_EVENTS",
    "MAX_WEB_SESSIONS",
    "WEB_API_VERSION",
    "WebGateway",
    "WebGatewayError",
    "WebRateLimits",
)
