from http.client import HTTPConnection
import json
import threading

import pytest

from game.network_protocol import NETWORK_PROTOCOL_VERSION
from game.web_server import create_web_server


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


def test_static_client_and_health_have_strict_security_headers(web_server):
    response, payload = request(web_server, "GET", "/")
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/html; charset=utf-8"
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert response.getheader("X-Frame-Options") == "DENY"
    assert "script-src 'self'" in response.getheader("Content-Security-Policy")
    assert "Catan Web" in payload.decode("utf-8")

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


def test_non_loopback_bind_is_rejected():
    with pytest.raises(ValueError, match="loopback"):
        create_web_server("0.0.0.0", 0)
