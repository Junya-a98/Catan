from http.client import HTTPConnection
import json
import socket
import threading

import pytest

from game.network_protocol import NETWORK_PROTOCOL_VERSION
from game.web_server import create_web_server
from game.websocket_transport import (
    WebSocketOpcode,
    encode_websocket_frame,
    read_websocket_frame,
)


@pytest.fixture
def web_server():
    server = create_web_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(server, method, path, *, body=None, headers=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        return response, payload
    finally:
        connection.close()


def session_cookie(server):
    response, payload = request(server, "POST", "/api/session")
    assert response.status == 200
    assert json.loads(payload)["api_version"] == 1
    cookie = response.getheader("Set-Cookie")
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    return cookie.split(";", 1)[0]


def test_server_service_loop_runs_gateway_maintenance():
    class Gateway:
        session_count = 0

        def __init__(self):
            self.calls = 0

        def maintain(self):
            self.calls += 1

    gateway = Gateway()
    server = create_web_server("127.0.0.1", 0, gateway=gateway)
    try:
        server.service_actions()
    finally:
        server.server_close()
    assert gateway.calls == 1


def test_static_client_and_health_have_strict_security_headers(web_server):
    response, payload = request(web_server, "GET", "/")
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/html; charset=utf-8"
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert response.getheader("X-Frame-Options") == "DENY"
    assert "script-src 'self'" in response.getheader("Content-Security-Policy")
    document = payload.decode("utf-8")
    assert "Catan Web" in document
    assert document.index('<script src="/audio.js" defer>') < document.index(
        '<script src="/app.js" defer>'
    )
    assert 'id="audio-sfx-toggle"' in document
    assert 'id="audio-bgm-toggle"' in document

    response, payload = request(web_server, "GET", "/api/health")
    assert response.status == 200
    assert json.loads(payload) == {
        "api_version": 1,
        "status": "ok",
        "sessions": 0,
    }

    response, payload = request(web_server, "HEAD", "/app.js")
    assert response.status == 200
    assert payload == b""


def test_audio_module_is_served_only_from_exact_script_route(web_server):
    response, payload = request(web_server, "GET", "/audio.js")
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/javascript; charset=utf-8"
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert "script-src 'self'" in response.getheader("Content-Security-Policy")
    assert b"window.CatanAudio" in payload

    content_length = response.getheader("Content-Length")
    response, head_payload = request(web_server, "HEAD", "/audio.js")
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/javascript; charset=utf-8"
    assert response.getheader("Content-Length") == content_length
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert head_payload == b""


@pytest.mark.parametrize(
    "path",
    [
        "/audio",
        "/audio.js/extra",
        "/assets/audio.js",
        "/%61udio.js",
    ],
)
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_unknown_audio_script_paths_are_rejected(web_server, method, path):
    response, payload = request(web_server, method, path)
    assert response.status == 404
    assert response.getheader("Content-Type") == "application/json; charset=utf-8"
    if method == "HEAD":
        assert payload == b""
    else:
        assert json.loads(payload)["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "path",
    [
        "/assets/board/ocean.webp",
        "/assets/board/frontier-fog.webp",
        "/assets/board/terrain-brick.webp",
        "/assets/board/terrain-desert.webp",
        "/assets/board/terrain-ore.webp",
        "/assets/board/terrain-sheep.webp",
        "/assets/board/terrain-wheat.webp",
        "/assets/board/terrain-wood.webp",
    ],
)
def test_board_webp_assets_are_served_from_exact_whitelist(web_server, path):
    response, payload = request(web_server, "GET", path)
    assert response.status == 200
    assert response.getheader("Content-Type") == "image/webp"
    assert response.getheader("Content-Length") == str(len(payload))
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert "img-src 'self' data:" in response.getheader("Content-Security-Policy")
    assert payload.startswith(b"RIFF")
    assert payload[8:12] == b"WEBP"

    response, payload = request(web_server, "HEAD", path)
    assert response.status == 200
    assert response.getheader("Content-Type") == "image/webp"
    assert int(response.getheader("Content-Length")) > 0
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert payload == b""


@pytest.mark.parametrize(
    "path",
    [
        "/assets/board/unknown.webp",
        "/assets/board/README.md",
        "/assets/board/../app.js",
        "/assets/board/%2e%2e/app.js",
    ],
)
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_board_asset_route_rejects_paths_outside_exact_whitelist(
    web_server, method, path
):
    response, payload = request(web_server, method, path)
    assert response.status == 404
    assert response.getheader("Content-Type") == "application/json; charset=utf-8"
    if method == "HEAD":
        assert payload == b""
    else:
        assert json.loads(payload)["error"]["code"] == "not_found"


def test_http_session_can_create_room_and_restore_events(web_server):
    cookie = session_cookie(web_server)
    create = {
        "type": "create_room",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "display_name": "Browser Host",
        "settings": {
            "player_count": 2,
            "victory_target": 7,
            "board_mode": "fully_random",
            "board_seed": 86712347,
        },
    }
    response, payload = request(
        web_server,
        "POST",
        "/api/message",
        body=json.dumps(create),
        headers={"Content-Type": "application/json", "Cookie": cookie},
    )
    document = json.loads(payload)
    assert response.status == 200
    welcome = next(
        event for event in document["events"] if event["type"] == "session_welcome"
    )
    assert len(welcome["room_code"]) == 6

    response, payload = request(
        web_server,
        "POST",
        "/api/session",
        headers={"Cookie": cookie},
    )
    restored = json.loads(payload)["events"]
    assert response.status == 200
    assert [event["type"] for event in restored] == [
        "session_welcome",
        "lobby_snapshot",
    ]


def test_two_http_sessions_receive_authoritative_lobby_broadcast(web_server):
    host_cookie = session_cookie(web_server)
    guest_cookie = session_cookie(web_server)
    create = {
        "type": "create_room",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "display_name": "Host",
        "settings": {
            "player_count": 2,
            "victory_target": 5,
            "board_mode": "constrained",
            "board_seed": 42,
        },
    }
    _response, payload = request(
        web_server,
        "POST",
        "/api/message",
        body=json.dumps(create),
        headers={"Content-Type": "application/json", "Cookie": host_cookie},
    )
    room_code = next(
        event
        for event in json.loads(payload)["events"]
        if event["type"] == "session_welcome"
    )["room_code"]
    join = {
        "type": "join_room",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "display_name": "Guest",
        "room_code": room_code,
        "role": "player",
    }
    response, payload = request(
        web_server,
        "POST",
        "/api/message",
        body=json.dumps(join),
        headers={"Content-Type": "application/json", "Cookie": guest_cookie},
    )
    assert response.status == 200
    assert any(
        event["type"] == "session_welcome" for event in json.loads(payload)["events"]
    )

    response, payload = request(
        web_server,
        "GET",
        "/api/events",
        headers={"Cookie": host_cookie},
    )
    lobby = next(
        event
        for event in json.loads(payload)["events"]
        if event["type"] == "lobby_snapshot"
    )["lobby"]
    assert response.status == 200
    assert [member["display_name"] for member in lobby["members"]] == [
        "Host",
        "Guest",
    ]


@pytest.mark.parametrize(
    ("body", "headers", "status", "code"),
    [
        ("{}", {}, 415, "unsupported_media_type"),
        ("{bad json", {"Content-Type": "application/json"}, 400, "invalid_json"),
        ("[]", {"Content-Type": "application/json"}, 400, "invalid_request"),
    ],
)
def test_message_endpoint_rejects_unsafe_documents(
    web_server, body, headers, status, code
):
    cookie = session_cookie(web_server)
    response, payload = request(
        web_server,
        "POST",
        "/api/message",
        body=body,
        headers={**headers, "Cookie": cookie},
    )
    assert response.status == status
    assert json.loads(payload)["error"]["code"] == code


def test_cross_origin_mutation_and_missing_session_are_rejected(web_server):
    response, payload = request(web_server, "GET", "/api/events")
    assert response.status == 401
    assert json.loads(payload)["error"]["code"] == "session_required"

    response, payload = request(
        web_server,
        "POST",
        "/api/session",
        headers={"Origin": "https://attacker.invalid"},
    )
    assert response.status == 403
    assert json.loads(payload)["error"]["code"] == "cross_site_request"


def test_deleting_session_expires_cookie_and_transport(web_server):
    cookie = session_cookie(web_server)
    response, payload = request(
        web_server,
        "DELETE",
        "/api/session",
        headers={"Cookie": cookie},
    )
    assert response.status == 200
    assert json.loads(payload)["closed"] is True
    assert "Max-Age=0" in response.getheader("Set-Cookie")

    response, payload = request(
        web_server,
        "GET",
        "/api/events",
        headers={"Cookie": cookie},
    )
    assert response.status == 401
    assert json.loads(payload)["error"]["code"] == "session_expired"


def test_authenticated_websocket_bootstrap_and_gateway_roundtrip(web_server):
    cookie = session_cookie(web_server)
    peer = socket.create_connection(("127.0.0.1", web_server.server_port), timeout=3)
    reader = peer.makefile("rb")
    try:
        request_bytes = (
            "GET /api/socket HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{web_server.server_port}\r\n"
            "Connection: Upgrade\r\n"
            "Upgrade: websocket\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            f"Origin: http://127.0.0.1:{web_server.server_port}\r\n"
            f"Cookie: {cookie}\r\n\r\n"
        ).encode("ascii")
        peer.sendall(request_bytes)
        response_lines = []
        while True:
            line = reader.readline()
            assert line
            response_lines.append(line)
            if line == b"\r\n":
                break
        assert response_lines[0].startswith(b"HTTP/1.1 101")
        assert any(
            line.lower().startswith(b"sec-websocket-accept:") for line in response_lines
        )

        bootstrap = read_websocket_frame(reader, require_mask=False)
        assert json.loads(bootstrap.payload) == {
            "api_version": 1,
            "kind": "bootstrap",
            "events": [],
        }

        ping = {
            "type": "ping",
            "protocol_version": NETWORK_PROTOCOL_VERSION,
            "nonce": "browser-heartbeat",
        }
        peer.sendall(
            encode_websocket_frame(
                json.dumps(ping).encode("utf-8"),
                opcode=WebSocketOpcode.TEXT,
                masking_key=b"test",
            )
        )
        response = read_websocket_frame(reader, require_mask=False)
        response_document = json.loads(response.payload)
        assert response_document["kind"] == "response"
        events = response_document["events"]
        assert events == [
            {
                "type": "pong",
                "protocol_version": NETWORK_PROTOCOL_VERSION,
                "nonce": "browser-heartbeat",
            }
        ]
        peer.sendall(
            encode_websocket_frame(
                (1000).to_bytes(2, "big"),
                opcode=WebSocketOpcode.CLOSE,
                masking_key=b"done",
            )
        )
        close = read_websocket_frame(reader, require_mask=False)
        assert close.opcode is WebSocketOpcode.CLOSE
    finally:
        reader.close()
        peer.close()


def test_websocket_rejects_cross_origin_before_upgrade(web_server):
    cookie = session_cookie(web_server)
    peer = socket.create_connection(("127.0.0.1", web_server.server_port), timeout=3)
    reader = peer.makefile("rb")
    try:
        peer.sendall(
            (
                "GET /api/socket HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{web_server.server_port}\r\n"
                "Connection: Upgrade\r\n"
                "Upgrade: websocket\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                "Origin: https://attacker.invalid\r\n"
                f"Cookie: {cookie}\r\n\r\n"
            ).encode("ascii")
        )
        status = reader.readline()
        headers = {}
        while True:
            line = reader.readline()
            if line == b"\r\n":
                break
            key, value = line.decode("ascii").split(":", 1)
            headers[key.lower()] = value.strip()
        body = reader.read(int(headers["content-length"]))
        assert status.startswith(b"HTTP/1.1 403")
        assert json.loads(body)["error"]["code"] == "cross_site_request"
    finally:
        reader.close()
        peer.close()


def test_non_loopback_bind_is_rejected():
    with pytest.raises(ValueError, match="loopback"):
        create_web_server("0.0.0.0", 0)
