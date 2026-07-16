"""Dependency-free local HTTP adapter and static host for the browser client."""

from __future__ import annotations

from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
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


class CatanWebServer(ThreadingHTTPServer):
    """A loopback-only Web server sharing one authoritative gateway."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int] = (DEFAULT_WEB_HOST, DEFAULT_WEB_PORT),
        *,
        gateway: WebGateway | None = None,
        static_root: str | Path = DEFAULT_WEB_STATIC_ROOT,
    ) -> None:
        host, port = server_address
        _validate_loopback_host(host)
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
        self.allowed_hosts = frozenset({"127.0.0.1", "localhost", "::1", host})
        super().__init__((host, port), CatanWebRequestHandler)

    def service_actions(self) -> None:
        """Keep authoritative AI moving even when browser timers are throttled."""

        try:
            self.gateway.maintain()
        except Exception:
            # Maintenance is best-effort; request handlers remain available
            # and will retry it under the same serialized gateway lock.
            return


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
                        "sessions": self.catan_server.gateway.session_count,
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
            if path == "/api/events" and method == "GET":
                token = self._required_session_token()
                events = self.catan_server.gateway.poll(token)
                self._event_response(events)
                return
            if path == "/api/message" and method == "POST":
                token = self._required_session_token()
                message = self._read_json_object()
                events = self.catan_server.gateway.handle(token, message)
                self._event_response(events)
                return
            self._json_error(
                HTTPStatus.NOT_FOUND,
                "not_found",
                "ページまたはAPIが見つかりません。",
            )
        except WebGatewayError as exc:
            self._json_error(exc.status, exc.code, str(exc))
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
        token = self.catan_server.gateway.open_session(existing)
        events = self.catan_server.gateway.bootstrap(token)
        cookie = f"{WEB_SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict"
        self._json_response(
            HTTPStatus.OK,
            {
                "api_version": WEB_API_VERSION,
                "events": list(events),
            },
            extra_headers=(("Set-Cookie", cookie),),
        )

    def _handle_websocket(self) -> None:
        """Upgrade one authenticated browser session to bounded JSON frames."""

        if not self._valid_request_origin():
            self._json_error(
                HTTPStatus.FORBIDDEN,
                "cross_site_request",
                "同一originから接続してください。",
            )
            return
        token = self._required_session_token()
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

        socket_connection = WebSocketConnection(self.rfile, self.wfile)
        socket_connection.send_json(
            {
                "api_version": WEB_API_VERSION,
                "kind": "bootstrap",
                "events": list(self.catan_server.gateway.bootstrap(token)),
            }
        )
        while True:
            try:
                event = socket_connection.receive()
                if event.kind != "message":
                    if socket_connection.handle_control(event):
                        return
                    continue
                events = self.catan_server.gateway.handle(token, event.message or {})
                socket_connection.send_json(
                    {
                        "api_version": WEB_API_VERSION,
                        "kind": "response",
                        "events": list(events),
                    }
                )
            except WebSocketProtocolError as exc:
                socket_connection.send_protocol_error(exc)
                return
            except WebGatewayError as exc:
                try:
                    socket_connection.send_json(
                        {
                            "api_version": WEB_API_VERSION,
                            "kind": "response",
                            "error": {"code": exc.code, "message": str(exc)},
                        }
                    )
                except (WebSocketEOF, OSError):
                    return
                if exc.status == HTTPStatus.UNAUTHORIZED:
                    return
                continue
            except (WebSocketEOF, BrokenPipeError, ConnectionResetError, OSError):
                return

    def _end_session(self) -> None:
        token = self._session_token()
        if token is not None:
            self.catan_server.gateway.close_session(token)
        self._json_response(
            HTTPStatus.OK,
            {"api_version": WEB_API_VERSION, "closed": True},
            extra_headers=(
                (
                    "Set-Cookie",
                    f"{WEB_SESSION_COOKIE}=; Path=/; HttpOnly; "
                    "SameSite=Strict; Max-Age=0",
                ),
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

    def _event_response(self, events: tuple[dict[str, Any], ...]) -> None:
        self._json_response(
            HTTPStatus.OK,
            {"api_version": WEB_API_VERSION, "events": list(events)},
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
        return token

    def _session_token(self) -> str | None:
        raw_cookie = self.headers.get("Cookie")
        if not raw_cookie:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except Exception:
            return None
        morsel = cookie.get(WEB_SESSION_COOKIE)
        if morsel is None:
            return None
        value = morsel.value
        return value if 20 <= len(value) <= 128 else None

    def _valid_host_header(self) -> bool:
        host_header = self.headers.get("Host", "")
        if not host_header or any(character.isspace() for character in host_header):
            return False
        try:
            hostname = urlsplit(f"//{host_header}").hostname
        except ValueError:
            return False
        return hostname in self.catan_server.allowed_hosts

    def _valid_request_origin(self) -> bool:
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        if (
            parsed.scheme != "http"
            or parsed.hostname not in self.catan_server.allowed_hosts
        ):
            return False
        origin_port = parsed.port or 80
        return origin_port == self.catan_server.server_port

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

    def _json_error(self, status: int, code: str, message: str) -> None:
        self._json_response(
            status,
            {
                "api_version": WEB_API_VERSION,
                "error": {"code": code, "message": message},
            },
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


def create_web_server(
    host: str = DEFAULT_WEB_HOST,
    port: int = DEFAULT_WEB_PORT,
    *,
    gateway: WebGateway | None = None,
    static_root: str | Path = DEFAULT_WEB_STATIC_ROOT,
) -> CatanWebServer:
    return CatanWebServer(
        (host, port),
        gateway=gateway,
        static_root=static_root,
    )


__all__ = (
    "CatanWebServer",
    "DEFAULT_WEB_HOST",
    "DEFAULT_WEB_PORT",
    "DEFAULT_WEB_STATIC_ROOT",
    "MAX_WEB_REQUEST_BYTES",
    "WEB_SESSION_COOKIE",
    "create_web_server",
)
