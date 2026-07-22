"""Dependency-free local HTTP adapter and static host for the browser client."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
import secrets
import socket
import ssl
import threading
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from game.web_gateway import WEB_API_VERSION, WebGateway, WebGatewayError
from game.websocket_transport import (
    WebSocketConnection,
    WebSocketEOF,
    WebSocketHandshakeError,
    WebSocketProtocolError,
    validate_websocket_handshake,
)


DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8765
MAX_WEB_REQUEST_BYTES = 512 * 1024
WEB_SESSION_COOKIE = "catan_web_session"
WEB_ROOM_RESUME_COOKIE = "catan_room_resume"
WEB_FRIEND_CLAIM_COOKIE = "catan_friend_claim"
WEB_ROOM_RESUME_COOKIE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_WEB_COOKIE_HEADER_BYTES = 8 * 1024
WEBSOCKET_EVENT_PUSH_SECONDS = 0.2

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WEB_STATIC_ROOT = _PROJECT_ROOT / "web"
_STATIC_ROUTES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/audio.js": ("audio.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/board/ocean.webp": ("assets/board/ocean.webp", "image/webp"),
    "/assets/board/frontier-fog.webp": (
        "assets/board/frontier-fog.webp",
        "image/webp",
    ),
    "/assets/board/terrain-brick.webp": (
        "assets/board/terrain-brick.webp",
        "image/webp",
    ),
    "/assets/board/terrain-desert.webp": (
        "assets/board/terrain-desert.webp",
        "image/webp",
    ),
    "/assets/board/terrain-ore.webp": (
        "assets/board/terrain-ore.webp",
        "image/webp",
    ),
    "/assets/board/terrain-sheep.webp": (
        "assets/board/terrain-sheep.webp",
        "image/webp",
    ),
    "/assets/board/terrain-wheat.webp": (
        "assets/board/terrain-wheat.webp",
        "image/webp",
    ),
    "/assets/board/terrain-wood.webp": (
        "assets/board/terrain-wood.webp",
        "image/webp",
    ),
}
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
_DNS_LABEL_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_WEB_SESSION_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{20,128}\Z")
_WEB_ROOM_RESUME_VALUE_PATTERN = re.compile(
    r"v1\.([ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{6})\.([A-Za-z0-9_-]{43,128})\Z"
)
_WEB_FRIEND_CLAIM_VALUE_PATTERN = re.compile(
    r"v1\.([ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{6})\.([A-Za-z0-9_-]{43})\Z"
)
_WEBSOCKET_FORBIDDEN_MEMBERSHIP_MESSAGES = frozenset(
    {
        "create_room",
        "join_room",
        "reconnect_room",
        "leave_room",
        "issue_friend_invitation",
        "claim_friend_invitation",
    }
)
_DEFINITIVE_RESUME_ERROR_CODES = frozenset(
    {"authentication_failed", "room_not_found"}
)
_LOOPBACK_WEB_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_TRUSTED_LAN_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
    )
)
_TAILSCALE_DEVICE_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)
_TAILSCALE_RESERVED_NETWORKS = (
    ipaddress.ip_network("100.100.0.0/24"),
    ipaddress.ip_network("100.100.100.0/24"),
    ipaddress.ip_network("100.115.92.0/23"),
    ipaddress.ip_network("100.101.102.103/32"),
    ipaddress.ip_network("fd7a:115c:a1e0::53/128"),
)
_TAILSCALE_DNS_SUFFIX = ".ts.net"


class CatanWebServer(ThreadingHTTPServer):
    """A local/LAN Web server sharing one authoritative gateway."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int] = (DEFAULT_WEB_HOST, DEFAULT_WEB_PORT),
        *,
        gateway: WebGateway | None = None,
        static_root: str | Path = DEFAULT_WEB_STATIC_ROOT,
        lan_mode: bool = False,
        friends_vpn_mode: bool = False,
        allowed_hosts: Iterable[str] = (),
        tls_certfile: str | Path | None = None,
        tls_keyfile: str | Path | None = None,
    ) -> None:
        host, port = server_address
        if type(lan_mode) is not bool:
            raise ValueError("lan_mode must be a boolean")
        if type(friends_vpn_mode) is not bool:
            raise ValueError("friends_vpn_mode must be a boolean")
        if friends_vpn_mode and (tls_certfile is None or tls_keyfile is None):
            raise ValueError("friends VPN mode requires TLS certificate and key")
        canonical_allowed_hosts = _validate_server_exposure(
            host,
            lan_mode=lan_mode,
            friends_vpn_mode=friends_vpn_mode,
            allowed_hosts=allowed_hosts,
        )
        tls_context = _build_server_tls_context(tls_certfile, tls_keyfile)
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 0 <= port <= 65535
        ):
            raise ValueError("port must be 0..65535")
        root = Path(static_root).resolve()
        for filename, _content_type in _STATIC_ROUTES.values():
            if not (root / filename).is_file():
                raise ValueError(f"Web asset is missing: {filename}")
        self.gateway = gateway or WebGateway()
        self.static_root = root
        self.lan_mode = lan_mode
        self.friends_vpn_mode = friends_vpn_mode
        self.allowed_hosts = canonical_allowed_hosts
        self.tls_enabled = tls_context is not None
        self.transport_scheme = "https" if self.tls_enabled else "http"
        self.websocket_scheme = "wss" if self.tls_enabled else "ws"
        self._tls_context = tls_context
        try:
            bind_address = ipaddress.ip_address(host)
        except ValueError:
            bind_address = None
        if bind_address is not None and bind_address.version == 6:
            self.address_family = socket.AF_INET6
        self._websocket_lock = threading.RLock()
        self._websocket_peers: dict[
            str,
            tuple[WebSocketConnection, threading.Event],
        ] = {}
        super().__init__((host, port), CatanWebRequestHandler)

    def get_request(self):
        """Reject peers outside the selected network before the TLS handshake."""

        connection, client_address = super().get_request()
        peer = str(client_address[0])
        if self.friends_vpn_mode and not _is_tailscale_peer(peer):
            connection.close()
            raise OSError("connection source is outside the friends VPN boundary")
        if self.lan_mode and not _is_trusted_lan_peer(peer):
            connection.close()
            raise OSError("connection source is outside the trusted LAN boundary")
        if self._tls_context is not None:
            try:
                connection = self._tls_context.wrap_socket(
                    connection,
                    server_side=True,
                )
            except Exception:
                connection.close()
                raise
        return connection, client_address

    def service_actions(self) -> None:
        """Keep authoritative AI moving even when browser timers are throttled."""

        try:
            self.gateway.maintain()
        except Exception:
            # Maintenance is best-effort; request handlers remain available
            # and will retry it under the same serialized gateway lock.
            return

    def activate_websocket(
        self,
        token: str,
        connection: WebSocketConnection,
        stop_push: threading.Event,
    ) -> None:
        """Make one socket the sole event consumer for a browser session."""

        with self._websocket_lock:
            previous = self._websocket_peers.get(token)
            self._websocket_peers[token] = (connection, stop_push)
            if previous is None or previous[0] is connection:
                return
            previous_connection, previous_stop = previous
            previous_stop.set()
            try:
                previous_connection.send_close(1001, "superseded")
            except (WebSocketEOF, OSError):
                pass

    def deactivate_websocket(
        self,
        token: str,
        connection: WebSocketConnection,
    ) -> None:
        """Forget a socket only when it is still the active generation."""

        with self._websocket_lock:
            current = self._websocket_peers.get(token)
            if current is not None and current[0] is connection:
                self._websocket_peers.pop(token, None)

    def websocket_is_active(
        self,
        token: str,
        connection: WebSocketConnection,
    ) -> bool:
        with self._websocket_lock:
            current = self._websocket_peers.get(token)
            return current is not None and current[0] is connection

    def push_websocket_events(
        self,
        token: str,
        connection: WebSocketConnection,
        client_key: str,
    ) -> bool:
        """Atomically drain and deliver events to the active socket only."""

        with self._websocket_lock:
            if not self.websocket_is_active(token, connection):
                return False
            events = self.gateway.poll(token, client_key=client_key)
            if events:
                connection.send_json(
                    {
                        "api_version": WEB_API_VERSION,
                        "kind": "push",
                        "events": list(events),
                    }
                )
            return True

    def handle_websocket_message(
        self,
        token: str,
        connection: WebSocketConnection,
        message: dict[str, Any],
        client_key: str,
    ) -> bool:
        """Handle and reply without allowing a reconnect to steal the result."""

        with self._websocket_lock:
            if not self.websocket_is_active(token, connection):
                return False
            if message.get("type") in _WEBSOCKET_FORBIDDEN_MEMBERSHIP_MESSAGES:
                raise WebGatewayError(
                    "http_required",
                    "部屋の作成・参加・退出は安全なHTTP経路で実行してください。",
                    status=409,
                )
            events = self.gateway.handle(
                token,
                message,
                client_key=client_key,
                protected_room_access_allowed=(
                    self.protected_room_access_allowed(client_key)
                ),
            )
            connection.send_json(
                {
                    "api_version": WEB_API_VERSION,
                    "kind": "response",
                    "events": list(events),
                }
            )
            return True

    def send_websocket_error(
        self,
        token: str,
        connection: WebSocketConnection,
        error: WebGatewayError,
    ) -> bool:
        with self._websocket_lock:
            if not self.websocket_is_active(token, connection):
                return False
            connection.send_json(
                {
                    "api_version": WEB_API_VERSION,
                    "kind": "response",
                    "error": self.public_error_document(error),
                }
            )
            return True

    def protected_room_access_allowed(self, client_key: str) -> bool:
        """Trust only TLS or the TCP peer's loopback address for credentials."""

        if self.tls_enabled:
            return True
        try:
            return ipaddress.ip_address(client_key).is_loopback
        except ValueError:
            return False

    @property
    def access_profile(self) -> str:
        if self.friends_vpn_mode:
            return "friends_vpn"
        if self.lan_mode:
            return "trusted_lan"
        return "loopback"

    def enforce_access_profile(self, message: dict[str, Any]) -> dict[str, Any]:
        """Keep the friends-VPN profile invite-only at the HTTP boundary.

        The client cannot weaken this policy by omitting or changing a UI field.
        New rooms receive an authority-owned invitation credential.  Direct
        code/passphrase joins remain available in loopback and LAN profiles,
        but this profile accepts only a previously claimed role-bound invite.
        """

        if not self.friends_vpn_mode:
            return message
        message_type = message.get("type")
        if message_type == "create_room":
            if "passphrase" in message:
                raise WebGatewayError(
                    "friends_invitation_required",
                    "友人VPNでは期限付き招待専用の部屋だけを作成できます。",
                    status=403,
                )
            secured = dict(message)
            secured["invite_only"] = True
            return secured
        if message_type == "join_room" and (
            "role" in message or "passphrase" in message
        ):
            raise WebGatewayError(
                "friends_invitation_required",
                "友人VPNではホストが発行した期限付き招待から参加してください。",
                status=403,
            )
        return message

    @staticmethod
    def public_error_document(error: WebGatewayError) -> dict[str, Any]:
        document: dict[str, Any] = {
            "code": error.code,
            "message": str(error),
        }
        if error.retry_after_seconds is not None:
            document["retry_after_seconds"] = error.retry_after_seconds
        return document


class CatanWebRequestHandler(BaseHTTPRequestHandler):
    """Strict same-origin routes for the local browser MVP."""

    protocol_version = "HTTP/1.1"
    server_version = "CatanWeb/1"
    sys_version = ""

    @property
    def catan_server(self) -> CatanWebServer:
        return self.server  # type: ignore[return-value]

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        try:
            if self.catan_server.friends_vpn_mode and not _is_tailscale_peer(
                self._client_key()
            ):
                self._json_error(
                    HTTPStatus.FORBIDDEN,
                    "untrusted_network",
                    "認証済みの友人VPN端末から接続してください。",
                )
                return
            if self.catan_server.lan_mode and not _is_trusted_lan_peer(
                self._client_key()
            ):
                self._json_error(
                    HTTPStatus.FORBIDDEN,
                    "untrusted_network",
                    "信頼済みLAN内から接続してください。",
                )
                return
            if not self._valid_host_header():
                self._json_error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_host",
                    "Host headerが不正です。",
                )
                return
            path = urlsplit(self.path).path
            if method in {"POST", "DELETE"} and not self._valid_request_origin():
                self._json_error(
                    HTTPStatus.FORBIDDEN,
                    "cross_site_request",
                    "同一originから操作してください。",
                )
                return
            if method in {"GET", "HEAD"} and path in _STATIC_ROUTES:
                self._serve_static(path, head_only=method == "HEAD")
                return
            if path == "/api/health" and method == "GET":
                self._json_response(
                    HTTPStatus.OK,
                    {
                        "api_version": WEB_API_VERSION,
                        "status": "ok",
                        "transport": {
                            "http": self.catan_server.transport_scheme,
                            "websocket": self.catan_server.websocket_scheme,
                        },
                        "access_profile": self.catan_server.access_profile,
                    },
                )
                return
            if path == "/api/socket" and method == "GET":
                self._handle_websocket()
                return
            if path == "/api/session" and method == "POST":
                self._start_session()
                return
            if path == "/api/session" and method == "DELETE":
                self._end_session()
                return
            if path == "/api/invitations" and method == "POST":
                if not self._valid_request_origin(require_origin=True):
                    self._json_error(
                        HTTPStatus.FORBIDDEN,
                        "cross_site_request",
                        "同一originから操作してください。",
                    )
                    return
                self._issue_friend_invitation()
                return
            if path == "/api/invitations/list" and method == "POST":
                if not self._valid_request_origin(require_origin=True):
                    self._json_error(
                        HTTPStatus.FORBIDDEN,
                        "cross_site_request",
                        "同一originから操作してください。",
                    )
                    return
                self._list_friend_invitations()
                return
            if path == "/api/invitations" and method == "DELETE":
                if not self._valid_request_origin(require_origin=True):
                    self._json_error(
                        HTTPStatus.FORBIDDEN,
                        "cross_site_request",
                        "同一originから操作してください。",
                    )
                    return
                self._revoke_friend_invitations()
                return
            if path == "/api/invitations/claim" and method == "POST":
                if not self._valid_request_origin(require_origin=True):
                    self._json_error(
                        HTTPStatus.FORBIDDEN,
                        "cross_site_request",
                        "同一originから操作してください。",
                    )
                    return
                self._claim_friend_invitation()
                return
            if path == "/api/invitations/resume" and method == "POST":
                if not self._valid_request_origin(require_origin=True):
                    self._json_error(
                        HTTPStatus.FORBIDDEN,
                        "cross_site_request",
                        "同一originから操作してください。",
                    )
                    return
                self._resume_friend_invitation()
                return
            if path == "/api/invitations/claim" and method == "DELETE":
                if not self._valid_request_origin(require_origin=True):
                    self._json_error(
                        HTTPStatus.FORBIDDEN,
                        "cross_site_request",
                        "同一originから操作してください。",
                    )
                    return
                self._clear_friend_invitation_claim()
                return
            if path == "/api/resume" and method == "POST":
                if not self._valid_request_origin(require_origin=True):
                    self._json_error(
                        HTTPStatus.FORBIDDEN,
                        "cross_site_request",
                        "同一originから操作してください。",
                    )
                    return
                self._resume_room()
                return
            if path == "/api/resume/confirm" and method == "POST":
                if not self._valid_request_origin(require_origin=True):
                    self._json_error(
                        HTTPStatus.FORBIDDEN,
                        "cross_site_request",
                        "同一originから操作してください。",
                    )
                    return
                self._confirm_room_resume()
                return
            if path == "/api/resume" and method == "DELETE":
                self._clear_room_resume()
                return
            if path == "/api/events" and method == "GET":
                token = self._required_session_token()
                events = self.catan_server.gateway.poll(
                    token,
                    client_key=self._client_key(),
                )
                self._event_response(events)
                return
            if path == "/api/message" and method == "POST":
                token = self._required_session_token()
                message = self._read_json_object()
                message = self.catan_server.enforce_access_profile(message)
                if message.get("type") == "reconnect_room":
                    raise WebGatewayError(
                        "resume_cookie_required",
                        "Web版の再接続はHttpOnly復帰Cookieから行ってください。",
                        status=403,
                    )
                claim_cookie, claim_cookie_malformed = (
                    self._friend_invitation_claim_credential()
                )
                claim_join_shape = (
                    message.get("type") == "join_room"
                    and "role" not in message
                )
                if claim_join_shape and claim_cookie_malformed:
                    self._json_error(
                        HTTPStatus.FORBIDDEN,
                        "authentication_failed",
                        "招待情報を確認できませんでした。",
                        extra_headers=(
                            ("Set-Cookie", self._expired_friend_claim_cookie()),
                        ),
                    )
                    return
                joining_from_claim = claim_join_shape and claim_cookie is not None
                if joining_from_claim:
                    assert claim_cookie is not None
                    claim_room_code, claim_token = claim_cookie
                    try:
                        pending_claim = (
                            self.catan_server.gateway.friend_invitation_claim_credential(
                                token,
                                client_key=self._client_key(),
                            )
                        )
                        if (
                            pending_claim is None
                            or pending_claim.room_code != claim_room_code
                            or not secrets.compare_digest(
                                pending_claim.claim_token,
                                claim_token,
                            )
                        ):
                            self.catan_server.gateway.resume_friend_invitation_claim(
                                token,
                                room_code=claim_room_code,
                                claim_token=claim_token,
                                client_key=self._client_key(),
                                protected_room_access_allowed=(
                                    self.catan_server.protected_room_access_allowed(
                                        self._client_key()
                                    )
                                ),
                            )
                    except WebGatewayError as exc:
                        if exc.code not in _DEFINITIVE_RESUME_ERROR_CODES:
                            raise
                        self._json_error(
                            exc.status,
                            exc.code,
                            str(exc),
                            retry_after_seconds=exc.retry_after_seconds,
                            extra_headers=(
                                ("Set-Cookie", self._expired_friend_claim_cookie()),
                            ),
                        )
                        return
                    finally:
                        claim_token = None
                try:
                    events = self.catan_server.gateway.handle(
                        token,
                        message,
                        client_key=self._client_key(),
                        protected_room_access_allowed=(
                            self.catan_server.protected_room_access_allowed(
                                self._client_key()
                            )
                        ),
                    )
                except WebGatewayError as exc:
                    if joining_from_claim and exc.code in _DEFINITIVE_RESUME_ERROR_CODES:
                        self.catan_server.gateway.clear_friend_invitation_claim(
                            token,
                            client_key=self._client_key(),
                        )
                        self._json_error(
                            exc.status,
                            exc.code,
                            str(exc),
                            retry_after_seconds=exc.retry_after_seconds,
                            extra_headers=(
                                ("Set-Cookie", self._expired_friend_claim_cookie()),
                            ),
                        )
                        return
                    raise
                message_type = message.get("type")
                extra_headers: tuple[tuple[str, str], ...] = (
                    (("Set-Cookie", self._expired_friend_claim_cookie()),)
                    if (
                        message_type == "join_room"
                        and (claim_cookie is not None or claim_cookie_malformed)
                    )
                    else ()
                )
                if message_type in {"create_room", "join_room"} and any(
                    event.get("type") == "session_welcome" for event in events
                ):
                    credential = self.catan_server.gateway.room_resume_credential(
                        token,
                        client_key=self._client_key(),
                    )
                    if credential is not None:
                        extra_headers = (
                            *extra_headers,
                            (
                                "Set-Cookie",
                                self._room_resume_cookie(
                                    credential.room_code,
                                    credential.reconnect_token,
                                ),
                            ),
                        )
                elif message_type == "leave_room" and not any(
                    event.get("type") == "request_error" for event in events
                ):
                    extra_headers = (
                        *extra_headers,
                        ("Set-Cookie", self._expired_resume_cookie()),
                    )
                self._event_response(events, extra_headers=extra_headers)
                return
            self._json_error(
                HTTPStatus.NOT_FOUND,
                "not_found",
                "ページまたはAPIが見つかりません。",
            )
        except WebGatewayError as exc:
            self._json_error(
                exc.status,
                exc.code,
                str(exc),
                retry_after_seconds=exc.retry_after_seconds,
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self._json_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "Webサーバーで処理できませんでした。",
            )

    def _start_session(self) -> None:
        existing = self._session_token()
        client_key = self._client_key()
        token = self.catan_server.gateway.open_session(
            existing,
            client_key=client_key,
        )
        events = self.catan_server.gateway.bootstrap(
            token,
            client_key=client_key,
        )
        secure = "; Secure" if self.catan_server.tls_enabled else ""
        cookie = (
            f"{WEB_SESSION_COOKIE}={token}; Path=/; HttpOnly; "
            f"SameSite=Strict{secure}"
        )
        extra_headers: list[tuple[str, str]] = [("Set-Cookie", cookie)]
        credential = self.catan_server.gateway.room_resume_credential(
            token,
            client_key=client_key,
        )
        if credential is not None:
            # Reissue a replacement token retained by the authenticated
            # process-local session.  This recovers when an earlier resume
            # response was lost after its authority transaction committed.
            extra_headers.append(
                (
                    "Set-Cookie",
                    self._room_resume_cookie(
                        credential.room_code,
                        credential.reconnect_token,
                    ),
                )
            )
        self._json_response(
            HTTPStatus.OK,
            {
                "api_version": WEB_API_VERSION,
                "events": list(events),
            },
            extra_headers=extra_headers,
        )

    def _issue_friend_invitation(self) -> None:
        token = self._required_session_token()
        protected = self.catan_server.protected_room_access_allowed(
            self._client_key()
        )
        if not protected:
            raise WebGatewayError(
                "secure_transport_required",
                "友人招待はHTTPSまたは同一端末で利用してください。",
                status=403,
            )
        document = self._read_json_object()
        if set(document) != {"role"}:
            raise WebGatewayError(
                "invalid_request",
                "招待発行のfieldが不正です。",
            )
        invitation = self.catan_server.gateway.issue_friend_invitation(
            token,
            role=document["role"],
            client_key=self._client_key(),
            protected_room_access_allowed=protected,
        )
        self._json_response(
            HTTPStatus.OK,
            {
                "api_version": WEB_API_VERSION,
                "invitation": {
                    key: invitation[key]
                    for key in (
                        "token",
                        "invitation_id",
                        "room_code",
                        "role",
                        "issued_at_ms",
                        "expires_at_ms",
                    )
                },
            },
        )

    def _list_friend_invitations(self) -> None:
        token = self._required_session_token()
        protected = self.catan_server.protected_room_access_allowed(
            self._client_key()
        )
        if not protected:
            raise WebGatewayError(
                "secure_transport_required",
                "友人招待はHTTPSまたは同一端末で利用してください。",
                status=403,
            )
        document = self._read_json_object()
        if document:
            raise WebGatewayError(
                "invalid_request",
                "招待一覧のfieldが不正です。",
            )
        invitations = self.catan_server.gateway.list_friend_invitations(
            token,
            client_key=self._client_key(),
            protected_room_access_allowed=protected,
        )
        self._json_response(
            HTTPStatus.OK,
            {
                "api_version": WEB_API_VERSION,
                "invitations": list(invitations),
            },
        )

    def _revoke_friend_invitations(self) -> None:
        token = self._required_session_token()
        protected = self.catan_server.protected_room_access_allowed(
            self._client_key()
        )
        if not protected:
            raise WebGatewayError(
                "secure_transport_required",
                "友人招待はHTTPSまたは同一端末で利用してください。",
                status=403,
            )
        document = self._read_json_object()
        fields = set(document)
        if fields == {"invitation_id"}:
            result = self.catan_server.gateway.revoke_friend_invitation(
                token,
                invitation_id=document["invitation_id"],
                client_key=self._client_key(),
                protected_room_access_allowed=protected,
            )
        elif fields == {"all"} and document["all"] is True:
            result = self.catan_server.gateway.revoke_all_friend_invitations(
                token,
                client_key=self._client_key(),
                protected_room_access_allowed=protected,
            )
        else:
            raise WebGatewayError(
                "invalid_request",
                "招待取消のfieldが不正です。",
            )
        self._json_response(
            HTTPStatus.OK,
            {
                "api_version": WEB_API_VERSION,
                "revoked_count": result["revoked_count"],
                "invitations": list(result["invitations"]),
            },
        )

    def _claim_friend_invitation(self) -> None:
        token = self._required_session_token()
        protected = self.catan_server.protected_room_access_allowed(
            self._client_key()
        )
        if not protected:
            raise WebGatewayError(
                "secure_transport_required",
                "友人招待はHTTPSまたは同一端末で利用してください。",
                status=403,
            )
        document = self._read_json_object()
        if set(document) != {"room_code", "token"}:
            raise WebGatewayError(
                "invalid_request",
                "招待確認のfieldが不正です。",
            )
        room_code = document["room_code"]
        invite_token = document.pop("token")
        try:
            invitation = self.catan_server.gateway.claim_friend_invitation(
                token,
                room_code=room_code,
                invite_token=invite_token,
                client_key=self._client_key(),
                protected_room_access_allowed=protected,
            )
        finally:
            invite_token = None
            document.clear()
        credential = self.catan_server.gateway.friend_invitation_claim_credential(
            token,
            client_key=self._client_key(),
        )
        if credential is None or credential.room_code != invitation["room_code"]:
            raise WebGatewayError(
                "claim_cookie_failed",
                "招待の復帰情報を安全に保存できませんでした。",
                status=500,
            )
        self._json_response(
            HTTPStatus.OK,
            {
                "api_version": WEB_API_VERSION,
                "invitation": {
                    key: invitation[key]
                    for key in (
                        "room_code",
                        "role",
                        "issued_at_ms",
                        "expires_at_ms",
                    )
                },
            },
            extra_headers=(
                (
                    "Set-Cookie",
                    self._friend_claim_cookie(
                        credential.room_code,
                        credential.claim_token,
                        credential.expires_at_ms,
                    ),
                ),
            ),
        )

    def _resume_friend_invitation(self) -> None:
        """Restore an unconsumed invitation claim after a process restart."""

        protected = self.catan_server.protected_room_access_allowed(
            self._client_key()
        )
        if not protected:
            raise WebGatewayError(
                "secure_transport_required",
                "友人招待はHTTPSまたは同一端末で利用してください。",
                status=403,
            )
        document = self._read_json_object()
        if document:
            raise WebGatewayError(
                "invalid_request",
                "招待復帰のfieldが不正です。",
            )
        parsed, malformed = self._friend_invitation_claim_credential()
        if malformed:
            self._json_response(
                HTTPStatus.OK,
                {"api_version": WEB_API_VERSION, "invitation": None},
                extra_headers=(
                    ("Set-Cookie", self._expired_friend_claim_cookie()),
                ),
            )
            return
        token = self._required_session_token()
        if parsed is None:
            self._json_response(
                HTTPStatus.OK,
                {"api_version": WEB_API_VERSION, "invitation": None},
            )
            return
        room_code, claim_token = parsed
        try:
            invitation = self.catan_server.gateway.resume_friend_invitation_claim(
                token,
                room_code=room_code,
                claim_token=claim_token,
                client_key=self._client_key(),
                protected_room_access_allowed=protected,
            )
        except WebGatewayError as exc:
            if exc.code not in _DEFINITIVE_RESUME_ERROR_CODES:
                raise
            self._json_response(
                HTTPStatus.OK,
                {"api_version": WEB_API_VERSION, "invitation": None},
                extra_headers=(
                    ("Set-Cookie", self._expired_friend_claim_cookie()),
                ),
            )
            return
        finally:
            claim_token = None
        self._json_response(
            HTTPStatus.OK,
            {
                "api_version": WEB_API_VERSION,
                "invitation": {
                    key: invitation[key]
                    for key in (
                        "room_code",
                        "role",
                        "issued_at_ms",
                        "expires_at_ms",
                    )
                },
            },
        )

    def _clear_friend_invitation_claim(self) -> None:
        token = self._required_session_token()
        protected = self.catan_server.protected_room_access_allowed(
            self._client_key()
        )
        if not protected:
            raise WebGatewayError(
                "secure_transport_required",
                "友人招待はHTTPSまたは同一端末で利用してください。",
                status=403,
            )
        document = self._read_json_object()
        if document:
            raise WebGatewayError(
                "invalid_request",
                "招待破棄のfieldが不正です。",
            )
        parsed, _malformed = self._friend_invitation_claim_credential()
        if parsed is None:
            self.catan_server.gateway.clear_friend_invitation_claim(
                token,
                client_key=self._client_key(),
            )
        else:
            room_code, claim_token = parsed
            try:
                self.catan_server.gateway.release_friend_invitation_claim(
                    token,
                    room_code=room_code,
                    claim_token=claim_token,
                    client_key=self._client_key(),
                    protected_room_access_allowed=protected,
                )
            finally:
                claim_token = None
        self._json_response(
            HTTPStatus.OK,
            {
                "api_version": WEB_API_VERSION,
                "cleared": True,
            },
            extra_headers=(
                ("Set-Cookie", self._expired_friend_claim_cookie()),
            ),
        )

    def _resume_room(self) -> None:
        """Reconnect from the server-readable room cookie without a JSON bearer."""

        token = self._required_session_token()
        parsed, malformed = self._room_resume_credential()
        if parsed is None:
            headers = (
                (("Set-Cookie", self._expired_resume_cookie()),)
                if malformed
                else ()
            )
            self._event_response((), extra_headers=headers)
            return
        room_code, reconnect_token = parsed
        pending = self.catan_server.gateway.room_resume_credential(
            token,
            client_key=self._client_key(),
        )
        if pending is not None and pending.room_code == room_code:
            # The controller already committed this resume, but its HTTP
            # response may have been lost.  Re-deliver the private cookie and
            # durable bootstrap without attempting to displace the live member.
            recovered = self.catan_server.gateway.recover_pending_room_resume(
                token,
                room_code=room_code,
                client_key=self._client_key(),
            )
            if recovered is None:
                raise WebGatewayError(
                    "resume_rotation_failed",
                    "再接続情報を安全に再送できませんでした。",
                    status=409,
                )
            self._event_response(
                recovered,
                extra_headers=(
                    (
                        "Set-Cookie",
                        self._room_resume_cookie(
                            pending.room_code,
                            pending.reconnect_token,
                        ),
                    ),
                ),
            )
            return
        events = self.catan_server.gateway.resume_from_cookie(
            token,
            room_code=room_code,
            reconnect_token=reconnect_token,
            client_key=self._client_key(),
            protected_room_access_allowed=(
                self.catan_server.protected_room_access_allowed(self._client_key())
            ),
        )
        error_codes = {
            event.get("code")
            for event in events
            if event.get("type") == "request_error"
        }
        if error_codes & _DEFINITIVE_RESUME_ERROR_CODES:
            extra_headers = (("Set-Cookie", self._expired_resume_cookie()),)
        elif any(event.get("type") == "session_welcome" for event in events):
            rotated = self.catan_server.gateway.room_resume_credential(
                token,
                client_key=self._client_key(),
            )
            if rotated is None or rotated.room_code != room_code:
                raise WebGatewayError(
                    "resume_rotation_failed",
                    "再接続情報を安全に更新できませんでした。",
                    status=500,
                )
            extra_headers = (
                (
                    "Set-Cookie",
                    self._room_resume_cookie(
                        rotated.room_code,
                        rotated.reconnect_token,
                    ),
                ),
            )
        else:
            # Rate limits, persistence outages and an already-connected member
            # are transient.  Preserve the bearer rather than destroying a
            # legitimate recovery path shared by another tab.
            extra_headers = ()
        self._event_response(events, extra_headers=extra_headers)

    def _confirm_room_resume(self) -> None:
        """Revoke the previous token only after the new cookie is received."""

        token = self._required_session_token()
        parsed, malformed = self._room_resume_credential()
        if parsed is None:
            headers = (
                (("Set-Cookie", self._expired_resume_cookie()),)
                if malformed
                else ()
            )
            self._json_response(
                HTTPStatus.OK,
                {
                    "api_version": WEB_API_VERSION,
                    "confirmed": False,
                    "events": [],
                },
                extra_headers=headers,
            )
            return
        room_code, reconnect_token = parsed
        events = self.catan_server.gateway.confirm_room_resume(
            token,
            room_code=room_code,
            reconnect_token=reconnect_token,
            client_key=self._client_key(),
        )
        confirmed = any(
            event.get("type") == "resume_confirmed" for event in events
        )
        public_events = [
            event for event in events if event.get("type") != "resume_confirmed"
        ]
        self._json_response(
            HTTPStatus.OK,
            {
                "api_version": WEB_API_VERSION,
                "confirmed": confirmed,
                "events": public_events,
            },
        )

    def _clear_room_resume(self) -> None:
        token = self._required_session_token()
        self.catan_server.gateway.clear_room_resume_credential(
            token,
            client_key=self._client_key(),
        )
        self._json_response(
            HTTPStatus.OK,
            {"api_version": WEB_API_VERSION, "cleared": True},
            extra_headers=(("Set-Cookie", self._expired_resume_cookie()),),
        )

    def _handle_websocket(self) -> None:
        """Upgrade one authenticated browser session to bounded JSON frames."""

        if not self._valid_request_origin(require_origin=True):
            self._json_error(
                HTTPStatus.FORBIDDEN,
                "cross_site_request",
                "同一originから接続してください。",
            )
            return
        token = self._required_session_token()
        client_key = self._client_key()
        try:
            handshake = validate_websocket_handshake(
                self.headers,
                method=self.command,
            )
        except WebSocketHandshakeError as exc:
            self._json_response(
                exc.status,
                {
                    "api_version": WEB_API_VERSION,
                    "error": {"code": exc.code, "message": str(exc)},
                },
                extra_headers=exc.response_headers,
            )
            return

        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self._security_headers()
        for key, value in handshake.response_headers:
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True

        stop_push = threading.Event()
        socket_connection = WebSocketConnection(self.rfile, self.wfile)
        push_thread = None
        self.catan_server.activate_websocket(token, socket_connection, stop_push)
        try:
            socket_connection.send_json(
                {
                    "api_version": WEB_API_VERSION,
                    "kind": "bootstrap",
                    "events": list(
                        self.catan_server.gateway.bootstrap(
                            token,
                            client_key=client_key,
                        )
                    ),
                }
            )
            push_thread = threading.Thread(
                target=self._push_websocket_events,
                args=(token, socket_connection, stop_push, client_key),
                daemon=True,
            )
            push_thread.start()
            while not stop_push.is_set():
                try:
                    event = socket_connection.receive()
                    if not self.catan_server.websocket_is_active(
                        token,
                        socket_connection,
                    ):
                        return
                    if event.kind != "message":
                        if socket_connection.handle_control(event):
                            return
                        continue
                    if not self.catan_server.handle_websocket_message(
                        token,
                        socket_connection,
                        event.message or {},
                        client_key,
                    ):
                        return
                except WebSocketProtocolError as exc:
                    socket_connection.send_protocol_error(exc)
                    return
                except WebGatewayError as exc:
                    try:
                        if not self.catan_server.send_websocket_error(
                            token,
                            socket_connection,
                            exc,
                        ):
                            return
                    except (WebSocketEOF, OSError):
                        return
                    if exc.status == HTTPStatus.UNAUTHORIZED:
                        return
                    continue
                except (
                    WebSocketEOF,
                    BrokenPipeError,
                    ConnectionResetError,
                    OSError,
                ):
                    return
        finally:
            stop_push.set()
            self.catan_server.deactivate_websocket(token, socket_connection)
            if push_thread is not None:
                push_thread.join(timeout=WEBSOCKET_EVENT_PUSH_SECONDS * 2)

    def _push_websocket_events(
        self,
        token: str,
        socket_connection: WebSocketConnection,
        stop_push: threading.Event,
        client_key: str,
    ) -> None:
        """Push queued AI/broadcast events without relying on browser timers."""

        while not stop_push.wait(WEBSOCKET_EVENT_PUSH_SECONDS):
            try:
                if not self.catan_server.push_websocket_events(
                    token,
                    socket_connection,
                    client_key,
                ):
                    stop_push.set()
                    return
            except (WebGatewayError, WebSocketEOF, OSError):
                stop_push.set()
                return

    def _end_session(self) -> None:
        token = self._session_token()
        if token is not None:
            parsed, _malformed = self._friend_invitation_claim_credential()
            if parsed is not None and self.catan_server.gateway.has_session(
                token,
                client_key=self._client_key(),
            ):
                room_code, claim_token = parsed
                try:
                    self.catan_server.gateway.release_friend_invitation_claim(
                        token,
                        room_code=room_code,
                        claim_token=claim_token,
                        client_key=self._client_key(),
                        protected_room_access_allowed=(
                            self.catan_server.protected_room_access_allowed(
                                self._client_key()
                            )
                        ),
                    )
                except WebGatewayError:
                    # Closing the browser session is authoritative here; its
                    # bounded claim will still expire on the domain deadline.
                    pass
                finally:
                    claim_token = None
            self.catan_server.gateway.close_session(
                token,
                client_key=self._client_key(),
            )
        self._json_response(
            HTTPStatus.OK,
            {"api_version": WEB_API_VERSION, "closed": True},
            extra_headers=(
                (
                    "Set-Cookie",
                    f"{WEB_SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict"
                    f"{'; Secure' if self.catan_server.tls_enabled else ''}; "
                    "Max-Age=0",
                ),
                ("Set-Cookie", self._expired_friend_claim_cookie()),
            ),
        )

    def _serve_static(self, path: str, *, head_only: bool) -> None:
        filename, content_type = _STATIC_ROUTES[path]
        payload = (self.catan_server.static_root / filename).read_bytes()
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def _event_response(
        self,
        events: tuple[dict[str, Any], ...],
        *,
        extra_headers: Iterable[tuple[str, str]] = (),
    ) -> None:
        self._json_response(
            HTTPStatus.OK,
            {"api_version": WEB_API_VERSION, "events": list(events)},
            extra_headers=extra_headers,
        )

    def _read_json_object(self) -> dict[str, Any]:
        content_type = self.headers.get_content_type()
        if content_type != "application/json":
            raise WebGatewayError(
                "unsupported_media_type",
                "Content-Typeはapplication/jsonで指定してください。",
                status=415,
            )
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except ValueError as exc:
            raise WebGatewayError(
                "invalid_request", "Content-Lengthが不正です。"
            ) from exc
        if not 0 < length <= MAX_WEB_REQUEST_BYTES:
            raise WebGatewayError(
                "request_too_large"
                if length > MAX_WEB_REQUEST_BYTES
                else "invalid_request",
                "JSON bodyのサイズが不正です。",
                status=413 if length > MAX_WEB_REQUEST_BYTES else 400,
            )
        payload = self.rfile.read(length)
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebGatewayError("invalid_json", "JSON bodyが不正です。") from exc
        if type(document) is not dict:
            raise WebGatewayError(
                "invalid_request", "JSON bodyはobjectで指定してください。"
            )
        return document

    def _required_session_token(self) -> str:
        token = self._session_token()
        if token is None:
            raise WebGatewayError(
                "session_required",
                "Webセッションを開始してください。",
                status=401,
            )
        if not self.catan_server.gateway.has_session(
            token,
            client_key=self._client_key(),
        ):
            raise WebGatewayError(
                "session_expired",
                "Webセッションの有効期限が切れました。",
                status=401,
            )
        return token

    def _client_key(self) -> str:
        """Use the TCP peer identity and never trust forwarded IP headers."""

        return str(self.client_address[0])

    def _session_token(self) -> str | None:
        value, malformed = self._strict_cookie_value(
            WEB_SESSION_COOKIE,
            _WEB_SESSION_TOKEN_PATTERN,
        )
        return None if malformed else value

    def _room_resume_credential(self) -> tuple[tuple[str, str] | None, bool]:
        value, malformed = self._strict_cookie_value(
            WEB_ROOM_RESUME_COOKIE,
            _WEB_ROOM_RESUME_VALUE_PATTERN,
        )
        if value is None:
            return None, malformed
        match = _WEB_ROOM_RESUME_VALUE_PATTERN.fullmatch(value)
        if match is None:  # Defensive: strict parser already checked this.
            return None, True
        return (match.group(1), match.group(2)), False

    def _friend_invitation_claim_credential(
        self,
    ) -> tuple[tuple[str, str] | None, bool]:
        value, malformed = self._strict_cookie_value(
            WEB_FRIEND_CLAIM_COOKIE,
            _WEB_FRIEND_CLAIM_VALUE_PATTERN,
        )
        if value is None:
            return None, malformed
        match = _WEB_FRIEND_CLAIM_VALUE_PATTERN.fullmatch(value)
        if match is None:  # Defensive: strict parser already checked this.
            return None, True
        return (match.group(1), match.group(2)), False

    def _strict_cookie_value(
        self,
        name: str,
        pattern: re.Pattern[str],
    ) -> tuple[str | None, bool]:
        """Read one unambiguous, bounded host cookie without last-wins parsing."""

        headers = self.headers.get_all("Cookie", [])
        if not headers:
            return None, False
        if len(headers) != 1:
            return None, True
        raw = headers[0]
        try:
            raw_bytes = raw.encode("ascii")
        except UnicodeEncodeError:
            return None, True
        if len(raw_bytes) > MAX_WEB_COOKIE_HEADER_BYTES or any(
            byte < 0x20 or byte == 0x7F for byte in raw_bytes
        ):
            return None, True
        matches: list[str] = []
        for component in raw.split(";"):
            candidate = component.strip()
            cookie_name, separator, value = candidate.partition("=")
            if separator and cookie_name == name:
                matches.append(value)
        if not matches:
            return None, False
        if len(matches) != 1 or pattern.fullmatch(matches[0]) is None:
            return None, True
        return matches[0], False

    def _room_resume_cookie(self, room_code: str, reconnect_token: str) -> str:
        value = f"v1.{room_code}.{reconnect_token}"
        if _WEB_ROOM_RESUME_VALUE_PATTERN.fullmatch(value) is None:
            raise WebGatewayError(
                "invalid_resume_credential",
                "再接続情報をCookieへ保存できませんでした。",
                status=500,
            )
        secure = "; Secure" if self.catan_server.tls_enabled else ""
        return (
            f"{WEB_ROOM_RESUME_COOKIE}={value}; Path=/; HttpOnly; "
            f"SameSite=Strict{secure}; Max-Age={WEB_ROOM_RESUME_COOKIE_MAX_AGE_SECONDS}"
        )

    def _expired_resume_cookie(self) -> str:
        secure = "; Secure" if self.catan_server.tls_enabled else ""
        return (
            f"{WEB_ROOM_RESUME_COOKIE}=; Path=/; HttpOnly; SameSite=Strict"
            f"{secure}; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
        )

    def _friend_claim_cookie(
        self,
        room_code: str,
        claim_token: str,
        expires_at_ms: int,
    ) -> str:
        value = f"v1.{room_code}.{claim_token}"
        if _WEB_FRIEND_CLAIM_VALUE_PATTERN.fullmatch(value) is None:
            raise WebGatewayError(
                "invalid_claim_credential",
                "招待の復帰情報をCookieへ保存できませんでした。",
                status=500,
            )
        if type(expires_at_ms) is not int:
            raise WebGatewayError(
                "invalid_claim_credential",
                "招待の復帰期限を確認できませんでした。",
                status=500,
            )
        remaining_ms = expires_at_ms - int(time.time() * 1000)
        if remaining_ms <= 0:
            raise WebGatewayError(
                "authentication_failed",
                "招待情報を確認できませんでした。",
                status=403,
            )
        # A ceiling keeps a just-issued cookie from losing nearly a full
        # second.  Domain validation remains authoritative at the exact expiry.
        max_age = min(
            WEB_ROOM_RESUME_COOKIE_MAX_AGE_SECONDS,
            max(1, (remaining_ms + 999) // 1000),
        )
        secure = "; Secure" if self.catan_server.tls_enabled else ""
        return (
            f"{WEB_FRIEND_CLAIM_COOKIE}={value}; Path=/api; HttpOnly; "
            f"SameSite=Strict{secure}; Max-Age={max_age}"
        )

    def _expired_friend_claim_cookie(self) -> str:
        secure = "; Secure" if self.catan_server.tls_enabled else ""
        return (
            f"{WEB_FRIEND_CLAIM_COOKIE}=; Path=/api; HttpOnly; SameSite=Strict"
            f"{secure}; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
        )

    def _valid_host_header(self) -> bool:
        host_values = self.headers.get_all("Host", [])
        if len(host_values) != 1:
            return False
        host_header = host_values[0]
        if not host_header or any(character.isspace() for character in host_header):
            return False
        try:
            parsed = urlsplit(f"//{host_header}")
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            return False
        try:
            canonical_hostname = _canonicalize_web_host(hostname)
        except ValueError:
            return False
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or canonical_hostname not in self.catan_server.allowed_hosts
        ):
            return False
        default_port = 443 if self.catan_server.tls_enabled else 80
        effective_port = default_port if port is None else port
        return effective_port == self.catan_server.server_port

    def _valid_request_origin(self, *, require_origin: bool = False) -> bool:
        fetch_values = self.headers.get_all("Sec-Fetch-Site", [])
        if len(fetch_values) > 1:
            return False
        fetch_site = fetch_values[0] if fetch_values else None
        if fetch_site is not None and fetch_site not in {"same-origin", "none"}:
            return False
        origin_values = self.headers.get_all("Origin", [])
        if len(origin_values) > 1:
            return False
        origin = origin_values[0] if origin_values else None
        if origin is None:
            return not require_origin
        try:
            parsed = urlsplit(origin)
            default_port = 443 if self.catan_server.tls_enabled else 80
            origin_port = parsed.port or default_port
        except ValueError:
            return False
        try:
            origin_hostname = _canonicalize_web_host(parsed.hostname)
            request_hostname = self._canonical_request_hostname()
        except ValueError:
            return False
        if (
            parsed.scheme != self.catan_server.transport_scheme
            or origin_hostname not in self.catan_server.allowed_hosts
            or origin_hostname != request_hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return False
        return origin_port == self.catan_server.server_port

    def _canonical_request_hostname(self) -> str:
        host_values = self.headers.get_all("Host", [])
        if len(host_values) != 1:
            raise ValueError("Host header must be unique")
        parsed = urlsplit(f"//{host_values[0]}")
        return _canonicalize_web_host(parsed.hostname)

    def _json_response(
        self,
        status: int,
        document: dict[str, Any],
        *,
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        payload = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for key, value in extra_headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _json_error(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retry_after_seconds: int | None = None,
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        error: dict[str, Any] = {"code": code, "message": message}
        headers = extra_headers
        if retry_after_seconds is not None:
            error["retry_after_seconds"] = retry_after_seconds
            headers = (*headers, ("Retry-After", str(retry_after_seconds)))
        self._json_response(
            status,
            {
                "api_version": WEB_API_VERSION,
                "error": error,
            },
            extra_headers=headers,
        )

    def _security_headers(self) -> None:
        self.send_header("Content-Security-Policy", _CONTENT_SECURITY_POLICY)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")

    def log_message(self, _format: str, *_args: Any) -> None:
        # The CLI prints one startup URL.  Per-request access logs would expose
        # room codes in terminals and make tests noisy.
        return


def _validate_loopback_host(host: str) -> None:
    if not isinstance(host, str) or not host:
        raise ValueError("host must be a loopback address")
    if host == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("host must be localhost or a loopback IP address") from exc
    if not address.is_loopback:
        raise ValueError("the Web MVP binds to loopback only")


def _build_server_tls_context(
    certfile: str | Path | None,
    keyfile: str | Path | None,
) -> ssl.SSLContext | None:
    """Load one explicit server certificate/key pair, or keep plain HTTP.

    TLS is a transport option for loopback and trusted-LAN use.  It does not
    relax the bind/Host/Origin boundary and does not enable the reserved
    Internet-public profile.
    """

    if (certfile is None) != (keyfile is None):
        raise ValueError("TLS requires both a certificate and a private key")
    if certfile is None:
        return None
    if not isinstance(certfile, (str, Path)) or not isinstance(keyfile, (str, Path)):
        raise ValueError("TLS certificate and private key must be file paths")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    try:
        context.load_cert_chain(
            certfile=str(Path(certfile)),
            keyfile=str(Path(keyfile)),
        )
    except (OSError, ssl.SSLError) as exc:
        raise ValueError("TLS certificate/private key could not be loaded") from exc
    return context


def _canonicalize_web_host(value: object) -> str:
    """Return one unambiguous host-only authority name."""

    if type(value) is not str or not value:
        raise ValueError("allowed host must be a non-empty string")
    if (
        value != value.strip()
        or any(character.isspace() for character in value)
        or any(character in value for character in "/@[]%")
    ):
        raise ValueError("allowed host must contain only a hostname or IP address")
    try:
        return ipaddress.ip_address(value).compressed.lower()
    except ValueError:
        pass
    if not value.isascii() or len(value) > 253 or value.endswith("."):
        raise ValueError("allowed host must be a canonical ASCII hostname")
    canonical = value.lower()
    labels = canonical.split(".")
    if not labels or any(
        _DNS_LABEL_PATTERN.fullmatch(label) is None for label in labels
    ):
        raise ValueError("allowed host must be a canonical ASCII hostname")
    if len(labels) > 1 and all(label.isdigit() for label in labels):
        raise ValueError("allowed host is not a canonical IP address")
    return canonical


def _is_loopback_web_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_trusted_lan_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback:
        return True
    return any(
        address.version == network.version and address in network
        for network in _TRUSTED_LAN_NETWORKS
    )


def _is_trusted_lan_peer(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    unscoped = value.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(unscoped)
    except ValueError:
        return False
    return _is_trusted_lan_address(address)


def _is_tailscale_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if any(
        address.version == network.version and address in network
        for network in _TAILSCALE_RESERVED_NETWORKS
    ):
        return False
    return any(
        address.version == network.version and address in network
        for network in _TAILSCALE_DEVICE_NETWORKS
    )


def _is_tailscale_peer(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    unscoped = value.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(unscoped)
    except ValueError:
        return False
    return _is_tailscale_address(address)


def _canonical_allowed_hosts(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("allowed_hosts must be an iterable of host names")
    try:
        supplied = tuple(values)
    except TypeError as exc:
        raise ValueError("allowed_hosts must be an iterable of host names") from exc
    canonical_hosts: list[str] = []
    seen: set[str] = set()
    for value in supplied:
        canonical = _canonicalize_web_host(value)
        if value != canonical:
            raise ValueError(f"allowed host must use canonical form: {canonical}")
        try:
            address = ipaddress.ip_address(canonical)
        except ValueError:
            address = None
        if address is not None:
            if address.is_unspecified or address.is_multicast:
                raise ValueError("allowed host must be an address clients can use")
            if not _is_trusted_lan_address(address):
                raise ValueError("allowed host must be a trusted LAN address")
        elif canonical != "localhost" and not canonical.endswith(".local"):
            raise ValueError("allowed host DNS name must end in .local")
        if canonical in seen:
            raise ValueError(f"duplicate allowed host: {canonical}")
        seen.add(canonical)
        canonical_hosts.append(canonical)
    return tuple(canonical_hosts)


def _canonical_friends_vpn_host(values: Iterable[str]) -> str:
    if isinstance(values, (str, bytes)):
        raise ValueError("allowed_hosts must be an iterable of host names")
    try:
        supplied = tuple(values)
    except TypeError as exc:
        raise ValueError("allowed_hosts must be an iterable of host names") from exc
    if len(supplied) != 1:
        raise ValueError("friends VPN mode requires exactly one --allowed-host")
    value = supplied[0]
    canonical = _canonicalize_web_host(value)
    if value != canonical:
        raise ValueError(f"allowed host must use canonical form: {canonical}")
    try:
        ipaddress.ip_address(canonical)
    except ValueError:
        labels = canonical.split(".")
        if not canonical.endswith(_TAILSCALE_DNS_SUFFIX) or len(labels) < 4:
            raise ValueError(
                "friends VPN allowed host must be a full Tailscale .ts.net name"
            )
    else:
        raise ValueError(
            "friends VPN allowed host must be the certificate's .ts.net name"
        )
    return canonical


def _validate_server_exposure(
    host: str,
    *,
    lan_mode: bool,
    friends_vpn_mode: bool,
    allowed_hosts: Iterable[str],
) -> frozenset[str]:
    if lan_mode and friends_vpn_mode:
        raise ValueError("lan_mode and friends_vpn_mode are mutually exclusive")
    if friends_vpn_mode:
        canonical_bind_host = _canonicalize_web_host(host)
        if host != canonical_bind_host:
            raise ValueError(f"host must use canonical form: {canonical_bind_host}")
        try:
            bind_address = ipaddress.ip_address(canonical_bind_host)
        except ValueError as exc:
            raise ValueError(
                "friends VPN bind host must be a Tailscale IP address"
            ) from exc
        if not _is_tailscale_address(bind_address):
            raise ValueError(
                "friends VPN bind host must be an assignable Tailscale IP address"
            )
        allowed_host = _canonical_friends_vpn_host(allowed_hosts)
        return frozenset((allowed_host,))
    if not lan_mode:
        supplied = _canonical_allowed_hosts(allowed_hosts)
        if supplied:
            raise ValueError("allowed_hosts requires lan_mode")
        _validate_loopback_host(host)
        return frozenset((*_LOOPBACK_WEB_HOSTS, _canonicalize_web_host(host)))

    canonical_bind_host = _canonicalize_web_host(host)
    if host != canonical_bind_host:
        raise ValueError(f"host must use canonical form: {canonical_bind_host}")
    if canonical_bind_host != "localhost":
        try:
            bind_address = ipaddress.ip_address(canonical_bind_host)
        except ValueError as exc:
            raise ValueError("LAN bind host must be an IP address") from exc
        if not bind_address.is_unspecified and not _is_trusted_lan_address(
            bind_address
        ):
            raise ValueError("LAN bind host must be a trusted LAN address")
    supplied = _canonical_allowed_hosts(allowed_hosts)
    if not supplied:
        raise ValueError("LAN mode requires at least one --allowed-host")
    if not any(not _is_loopback_web_host(value) for value in supplied):
        raise ValueError("LAN mode requires a non-loopback --allowed-host")
    return frozenset((*_LOOPBACK_WEB_HOSTS, *supplied))


def create_web_server(
    host: str = DEFAULT_WEB_HOST,
    port: int = DEFAULT_WEB_PORT,
    *,
    gateway: WebGateway | None = None,
    static_root: str | Path = DEFAULT_WEB_STATIC_ROOT,
    lan_mode: bool = False,
    friends_vpn_mode: bool = False,
    allowed_hosts: Iterable[str] = (),
    tls_certfile: str | Path | None = None,
    tls_keyfile: str | Path | None = None,
) -> CatanWebServer:
    return CatanWebServer(
        (host, port),
        gateway=gateway,
        static_root=static_root,
        lan_mode=lan_mode,
        friends_vpn_mode=friends_vpn_mode,
        allowed_hosts=allowed_hosts,
        tls_certfile=tls_certfile,
        tls_keyfile=tls_keyfile,
    )


__all__ = (
    "CatanWebServer",
    "DEFAULT_WEB_HOST",
    "DEFAULT_WEB_PORT",
    "DEFAULT_WEB_STATIC_ROOT",
    "MAX_WEB_REQUEST_BYTES",
    "WEB_FRIEND_CLAIM_COOKIE",
    "WEB_ROOM_RESUME_COOKIE",
    "WEB_ROOM_RESUME_COOKIE_MAX_AGE_SECONDS",
    "WEB_SESSION_COOKIE",
    "create_web_server",
)
