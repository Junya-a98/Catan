from http.client import HTTPConnection
import json
import threading

from game.lan_controller import LanServerController
from game.network_protocol import NETWORK_PROTOCOL_VERSION
from game.server_state import SQLiteRoomAuthorityStore
from game.web_gateway import WebGateway
from game.web_server import create_web_server


def _request(server, method, path, *, body=None, headers=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        return response, payload
    finally:
        connection.close()


def _response_cookie(response, name):
    prefix = f"{name}="
    for key, value in response.getheaders():
        if key.lower() == "set-cookie" and value.startswith(prefix):
            return value.split(";", 1)[0]
    return None


def _serve(gateway):
    server = create_web_server("127.0.0.1", 0, gateway=gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _browser_session(server):
    response, payload = _request(server, "POST", "/api/session")
    assert response.status == 200
    assert json.loads(payload)["events"] == []
    cookie = _response_cookie(response, "catan_web_session")
    assert cookie is not None
    return cookie


def _member_id(controller, gateway, browser_cookie):
    browser_token = browser_cookie.partition("=")[2]
    connection_id = gateway._sessions[browser_token].connection_id
    return controller._sessions[connection_id].member_id


def test_http_only_resume_cookie_restores_persisted_member_after_server_restart(
    tmp_path,
):
    database = tmp_path / "room-authority.sqlite3"
    key = tmp_path / "room-authority.key"

    store_a = SQLiteRoomAuthorityStore(database, key_path=key)
    controller_a = LanServerController(state_store=store_a)
    gateway_a = WebGateway(controller=controller_a)
    server_a, thread_a = _serve(gateway_a)
    try:
        browser_a = _browser_session(server_a)
        create = {
            "type": "create_room",
            "protocol_version": NETWORK_PROTOCOL_VERSION,
            "display_name": "Restart Host",
            "settings": {
                "player_count": 2,
                "victory_target": 10,
                "board_mode": "constrained",
                "board_seed": 4242,
            },
        }
        response, payload = _request(
            server_a,
            "POST",
            "/api/message",
            body=json.dumps(create),
            headers={
                "Content-Type": "application/json",
                "Cookie": browser_a,
            },
        )
        assert response.status == 200
        created = json.loads(payload)["events"]
        welcome_a = next(
            event for event in created if event["type"] == "session_welcome"
        )
        assert welcome_a["reconnect_token"] is None
        resume_cookie = _response_cookie(response, "catan_room_resume")
        assert resume_cookie is not None
        member_id_a = _member_id(controller_a, gateway_a, browser_a)
    finally:
        _stop(server_a, thread_a)
        store_a.close()

    store_b = SQLiteRoomAuthorityStore(database, key_path=key)
    controller_b = LanServerController(state_store=store_b)
    gateway_b = WebGateway(controller=controller_b)
    server_b, thread_b = _serve(gateway_b)
    try:
        browser_b = _browser_session(server_b)
        response, payload = _request(
            server_b,
            "POST",
            "/api/resume",
            headers={
                "Cookie": f"{browser_b}; {resume_cookie}",
                "Origin": f"http://127.0.0.1:{server_b.server_port}",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert response.status == 200
        restored = json.loads(payload)["events"]
        welcome_b = next(
            event for event in restored if event["type"] == "session_welcome"
        )
        lobby_b = next(
            event["lobby"] for event in restored if event["type"] == "lobby_snapshot"
        )
        assert welcome_b["room_code"] == welcome_a["room_code"]
        assert welcome_b["role"] == "host"
        assert welcome_b["seat_index"] == 0
        assert welcome_b["next_sequence"] == 0
        assert welcome_b["reconnect_token"] is None
        assert _member_id(controller_b, gateway_b, browser_b) == member_id_a
        assert lobby_b["members"][0]["display_name"] == "Restart Host"
        assert lobby_b["members"][0]["connected"] is True
        rotated_cookie = _response_cookie(response, "catan_room_resume")
        assert rotated_cookie is not None
        assert rotated_cookie != resume_cookie
        assert "reconnect_token\": \"" not in payload.decode("utf-8")
    finally:
        _stop(server_b, thread_b)
        store_b.close()


def test_previous_cookie_survives_restart_during_rotation_grace(tmp_path):
    database = tmp_path / "room-authority.sqlite3"
    key = tmp_path / "room-authority.key"

    store_a = SQLiteRoomAuthorityStore(database, key_path=key)
    controller_a = LanServerController(state_store=store_a)
    gateway_a = WebGateway(controller=controller_a)
    server_a, thread_a = _serve(gateway_a)
    try:
        browser_a = _browser_session(server_a)
        create = {
            "type": "create_room",
            "protocol_version": NETWORK_PROTOCOL_VERSION,
            "display_name": "Grace Host",
            "settings": {
                "player_count": 2,
                "victory_target": 10,
                "board_mode": "constrained",
                "board_seed": 4242,
            },
        }
        response, payload = _request(
            server_a,
            "POST",
            "/api/message",
            body=json.dumps(create),
            headers={
                "Content-Type": "application/json",
                "Cookie": browser_a,
            },
        )
        assert response.status == 200
        welcome_a = next(
            event
            for event in json.loads(payload)["events"]
            if event["type"] == "session_welcome"
        )
        previous_cookie = _response_cookie(response, "catan_room_resume")
        assert previous_cookie is not None
        member_id_a = _member_id(controller_a, gateway_a, browser_a)

        response, _payload = _request(
            server_a,
            "DELETE",
            "/api/session",
            headers={"Cookie": f"{browser_a}; {previous_cookie}"},
        )
        assert response.status == 200
        rotating_browser = _browser_session(server_a)
        response, payload = _request(
            server_a,
            "POST",
            "/api/resume",
            headers={
                "Cookie": f"{rotating_browser}; {previous_cookie}",
                "Origin": f"http://127.0.0.1:{server_a.server_port}",
            },
        )
        assert response.status == 200
        current_cookie = _response_cookie(response, "catan_room_resume")
        assert current_cookie is not None
        assert current_cookie != previous_cookie
        assert previous_cookie.partition("=")[2] not in payload.decode("utf-8")
        assert current_cookie.partition("=")[2] not in payload.decode("utf-8")
    finally:
        _stop(server_a, thread_a)
        store_a.close()

    # The replacement response above is treated as lost.  After restart the
    # browser still has only the previous cookie; its original absolute grace
    # deadline must have been included in durable room authority.
    store_b = SQLiteRoomAuthorityStore(database, key_path=key)
    controller_b = LanServerController(state_store=store_b)
    gateway_b = WebGateway(controller=controller_b)
    server_b, thread_b = _serve(gateway_b)
    try:
        browser_b = _browser_session(server_b)
        response, payload = _request(
            server_b,
            "POST",
            "/api/resume",
            headers={
                "Cookie": f"{browser_b}; {previous_cookie}",
                "Origin": f"http://127.0.0.1:{server_b.server_port}",
            },
        )
        document = json.loads(payload)
        welcome_b = next(
            event
            for event in document["events"]
            if event["type"] == "session_welcome"
        )
        retried_cookie = _response_cookie(response, "catan_room_resume")

        assert response.status == 200
        assert welcome_b["room_code"] == welcome_a["room_code"]
        assert welcome_b["seat_index"] == welcome_a["seat_index"] == 0
        assert _member_id(controller_b, gateway_b, browser_b) == member_id_a
        assert retried_cookie is not None
        assert retried_cookie not in {previous_cookie, current_cookie}
        serialized = payload.decode("utf-8")
        assert previous_cookie.partition("=")[2] not in serialized
        assert current_cookie.partition("=")[2] not in serialized
        assert retried_cookie.partition("=")[2] not in serialized
    finally:
        _stop(server_b, thread_b)
        store_b.close()
