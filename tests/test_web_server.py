from http.client import HTTPConnection
import json
import socket
import threading

import pytest

from game.network_protocol import NETWORK_PROTOCOL_VERSION, build_game_command
import game.web_server as web_server_module
from game.web_server import create_web_server
from game.websocket_transport import (
    WebSocketOpcode,
    encode_websocket_frame,
    read_websocket_frame,
)
import web_main as web_main_module
from web_main import main as web_main


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


@pytest.fixture
def lan_web_server():
    server = create_web_server(
        "0.0.0.0",
        0,
        lan_mode=True,
        allowed_hosts=(
            "192.168.50.20",
            "catan-box.local",
            "fd00::20",
        ),
    )
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


def response_cookie(response, name):
    prefix = f"{name}="
    for key, value in response.getheaders():
        if key.lower() == "set-cookie" and value.startswith(prefix):
            return value
    return None


def same_origin(server):
    return f"http://127.0.0.1:{server.server_port}"


def create_http_room(server, cookie, *, name="Browser Host", settings=None):
    create = {
        "type": "create_room",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "display_name": name,
        "settings": settings
        or {
            "player_count": 2,
            "victory_target": 7,
            "board_mode": "fully_random",
            "board_seed": 86712347,
        },
    }
    response, payload = request(
        server,
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
    return response, payload, welcome


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
        "transport": {"http": "http", "websocket": "ws"},
        "access_profile": "loopback",
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
    response, payload, welcome = create_http_room(web_server, cookie)
    assert len(welcome["room_code"]) == 6
    assert welcome["reconnect_token"] is None
    resume_cookie = response_cookie(response, "catan_room_resume")
    assert resume_cookie is not None
    assert "HttpOnly" in resume_cookie

    assert "SameSite=Strict" in resume_cookie
    assert "Max-Age=604800" in resume_cookie
    session_token = cookie.partition("=")[2]
    credential = web_server.gateway.room_resume_credential(
        session_token,
        client_key="127.0.0.1",
    )
    assert credential is not None
    assert credential.reconnect_token not in payload.decode("utf-8")

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


def test_http_only_room_cookie_resumes_the_same_reserved_seat(web_server):
    original_session = session_cookie(web_server)
    create_response, _payload, original_welcome = create_http_room(
        web_server,
        original_session,
    )
    resume_set_cookie = response_cookie(create_response, "catan_room_resume")
    assert resume_set_cookie is not None
    resume_cookie = resume_set_cookie.split(";", 1)[0]

    response, payload = request(
        web_server,
        "DELETE",
        "/api/session",
        headers={"Cookie": f"{original_session}; {resume_cookie}"},
    )
    assert response.status == 200
    assert json.loads(payload)["closed"] is True
    # Closing a transport reserves the seat; it must not destroy the room
    # recovery cookie needed by the replacement browser session.
    assert response_cookie(response, "catan_room_resume") is None

    replacement_session = session_cookie(web_server)
    response, payload = request(
        web_server,
        "POST",
        "/api/resume",
        headers={
            "Cookie": f"{replacement_session}; {resume_cookie}",
            "Origin": same_origin(web_server),
            "Sec-Fetch-Site": "same-origin",
        },
    )
    document = json.loads(payload)
    assert response.status == 200
    resumed = next(
        event for event in document["events"] if event["type"] == "session_welcome"
    )
    assert resumed["room_code"] == original_welcome["room_code"]
    assert resumed["seat_index"] == original_welcome["seat_index"]
    assert resumed["reconnect_token"] is None
    refreshed = response_cookie(response, "catan_room_resume")
    assert refreshed is not None
    assert refreshed.split(";", 1)[0] != resume_cookie
    assert "HttpOnly" in refreshed


def test_lost_rotation_response_can_retry_old_cookie_after_disconnect(web_server):
    original_session = session_cookie(web_server)
    create_response, _payload, original_welcome = create_http_room(
        web_server,
        original_session,
    )
    old_cookie = response_cookie(
        create_response,
        "catan_room_resume",
    ).split(";", 1)[0]
    old_secret = old_cookie.partition("=")[2]
    response, _payload = request(
        web_server,
        "DELETE",
        "/api/session",
        headers={"Cookie": f"{original_session}; {old_cookie}"},
    )
    assert response.status == 200

    first_retry_session = session_cookie(web_server)
    response, first_payload = request(
        web_server,
        "POST",
        "/api/resume",
        headers={
            "Cookie": f"{first_retry_session}; {old_cookie}",
            "Origin": same_origin(web_server),
        },
    )
    first_rotated = response_cookie(
        response,
        "catan_room_resume",
    ).split(";", 1)[0]
    first_secret = first_rotated.partition("=")[2]
    assert response.status == 200
    assert first_rotated != old_cookie
    assert old_secret not in first_payload.decode("utf-8")
    assert first_secret not in first_payload.decode("utf-8")

    # The first response is treated as lost: disconnect while the client still
    # possesses only old_cookie, then retry that previous credential.
    response, _payload = request(
        web_server,
        "DELETE",
        "/api/session",
        headers={"Cookie": f"{first_retry_session}; {old_cookie}"},
    )
    assert response.status == 200
    second_retry_session = session_cookie(web_server)
    response, second_payload = request(
        web_server,
        "POST",
        "/api/resume",
        headers={
            "Cookie": f"{second_retry_session}; {old_cookie}",
            "Origin": same_origin(web_server),
        },
    )
    second_document = json.loads(second_payload)
    second_rotated = response_cookie(
        response,
        "catan_room_resume",
    ).split(";", 1)[0]
    second_secret = second_rotated.partition("=")[2]
    resumed = next(
        event
        for event in second_document["events"]
        if event["type"] == "session_welcome"
    )

    assert response.status == 200
    assert resumed["room_code"] == original_welcome["room_code"]
    assert resumed["seat_index"] == original_welcome["seat_index"]
    assert second_rotated not in {old_cookie, first_rotated}
    assert old_secret not in second_payload.decode("utf-8")
    assert first_secret not in second_payload.decode("utf-8")
    assert second_secret not in second_payload.decode("utf-8")


def test_session_endpoint_reissues_a_pending_rotated_cookie(web_server):
    original_session = session_cookie(web_server)
    create_response, _payload, _welcome = create_http_room(
        web_server,
        original_session,
    )
    old_cookie = response_cookie(
        create_response,
        "catan_room_resume",
    ).split(";", 1)[0]
    response, _payload = request(
        web_server,
        "DELETE",
        "/api/session",
        headers={"Cookie": f"{original_session}; {old_cookie}"},
    )
    assert response.status == 200

    replacement_session = session_cookie(web_server)
    response, resume_payload = request(
        web_server,
        "POST",
        "/api/resume",
        headers={
            "Cookie": f"{replacement_session}; {old_cookie}",
            "Origin": same_origin(web_server),
        },
    )
    rotated_cookie = response_cookie(
        response,
        "catan_room_resume",
    ).split(";", 1)[0]
    rotated_secret = rotated_cookie.partition("=")[2]
    assert response.status == 200
    assert rotated_cookie != old_cookie
    assert rotated_secret not in resume_payload.decode("utf-8")

    # Retrying session bootstrap after a lost response must re-deliver the
    # already-committed replacement, without rotating a second time.
    response, bootstrap_payload = request(
        web_server,
        "POST",
        "/api/session",
        headers={"Cookie": f"{replacement_session}; {old_cookie}"},
    )
    reissued = response_cookie(response, "catan_room_resume")
    assert response.status == 200
    assert reissued is not None
    assert reissued.split(";", 1)[0] == rotated_cookie
    assert rotated_secret not in bootstrap_payload.decode("utf-8")
    assert all(
        event.get("reconnect_token") is None
        for event in json.loads(bootstrap_payload)["events"]
        if event.get("type") == "session_welcome"
    )


def test_confirming_rotation_revokes_previous_and_is_idempotent(web_server):
    original_session = session_cookie(web_server)
    create_response, _payload, _welcome = create_http_room(
        web_server,
        original_session,
    )
    old_cookie = response_cookie(
        create_response,
        "catan_room_resume",
    ).split(";", 1)[0]
    response, _payload = request(
        web_server,
        "DELETE",
        "/api/session",
        headers={"Cookie": f"{original_session}; {old_cookie}"},
    )
    assert response.status == 200

    connected_session = session_cookie(web_server)
    response, _payload = request(
        web_server,
        "POST",
        "/api/resume",
        headers={
            "Cookie": f"{connected_session}; {old_cookie}",
            "Origin": same_origin(web_server),
        },
    )
    current_cookie = response_cookie(
        response,
        "catan_room_resume",
    ).split(";", 1)[0]
    assert current_cookie != old_cookie

    response, payload = request(
        web_server,
        "POST",
        "/api/resume/confirm",
        headers={
            "Cookie": f"{connected_session}; {old_cookie}",
            "Origin": same_origin(web_server),
        },
    )
    rejected_previous = json.loads(payload)
    assert response.status == 200
    assert rejected_previous["confirmed"] is False
    assert rejected_previous["events"][0]["code"] == "authentication_failed"

    response, payload = request(
        web_server,
        "POST",
        "/api/resume/confirm",
        headers={
            "Cookie": f"{connected_session}; {current_cookie}",
            "Origin": same_origin(web_server),
        },
    )
    assert response.status == 200
    assert json.loads(payload) == {
        "api_version": 1,
        "confirmed": True,
        "events": [],
    }

    # Confirming an already-confirmed current credential is a successful no-op.
    response, payload = request(
        web_server,
        "POST",
        "/api/resume/confirm",
        headers={
            "Cookie": f"{connected_session}; {current_cookie}",
            "Origin": same_origin(web_server),
        },
    )
    assert response.status == 200
    assert json.loads(payload)["confirmed"] is True

    response, _payload = request(
        web_server,
        "DELETE",
        "/api/session",
        headers={"Cookie": f"{connected_session}; {current_cookie}"},
    )
    assert response.status == 200
    retry_session = session_cookie(web_server)

    response, payload = request(
        web_server,
        "POST",
        "/api/resume",
        headers={
            "Cookie": f"{retry_session}; {old_cookie}",
            "Origin": same_origin(web_server),
        },
    )
    assert response.status == 200
    assert next(
        event
        for event in json.loads(payload)["events"]
        if event["type"] == "request_error"
    )["code"] == "authentication_failed"

    response, payload = request(
        web_server,
        "POST",
        "/api/resume",
        headers={
            "Cookie": f"{retry_session}; {current_cookie}",
            "Origin": same_origin(web_server),
        },
    )
    assert response.status == 200
    assert any(
        event["type"] == "session_welcome"
        for event in json.loads(payload)["events"]
    )
    assert response_cookie(response, "catan_room_resume").split(";", 1)[0] not in {
        old_cookie,
        current_cookie,
    }


@pytest.mark.parametrize("path", ["/api/resume", "/api/resume/confirm"])
def test_resume_mutations_require_an_explicit_same_origin(web_server, path):
    cookie = session_cookie(web_server)

    response, payload = request(
        web_server,
        "POST",
        path,
        headers={"Cookie": cookie},
    )

    assert response.status == 403
    assert json.loads(payload)["error"]["code"] == "cross_site_request"


def test_http_join_sets_a_private_room_resume_cookie_without_json_bearer(web_server):
    host_cookie = session_cookie(web_server)
    _response, _payload, host_welcome = create_http_room(web_server, host_cookie)
    guest_cookie = session_cookie(web_server)
    join = {
        "type": "join_room",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "room_code": host_welcome["room_code"],
        "display_name": "Guest",
        "role": "player",
    }

    response, payload = request(
        web_server,
        "POST",
        "/api/message",
        body=json.dumps(join),
        headers={"Content-Type": "application/json", "Cookie": guest_cookie},
    )

    document = json.loads(payload)
    assert response.status == 200
    welcome = next(
        event for event in document["events"] if event["type"] == "session_welcome"
    )
    assert welcome["reconnect_token"] is None
    resume_cookie = response_cookie(response, "catan_room_resume")
    assert resume_cookie is not None
    assert "HttpOnly" in resume_cookie
    assert "SameSite=Strict" in resume_cookie
    guest_token = guest_cookie.partition("=")[2]
    credential = web_server.gateway.room_resume_credential(
        guest_token,
        client_key="127.0.0.1",
    )
    assert credential is not None
    assert credential.reconnect_token not in payload.decode("utf-8")


def test_http_friend_invitation_claim_and_join_keep_bearer_server_side(web_server):
    host_cookie = session_cookie(web_server)
    _response, _payload, host_welcome = create_http_room(web_server, host_cookie)
    origin = same_origin(web_server)

    response, payload = request(
        web_server,
        "POST",
        "/api/invitations",
        body=json.dumps({"role": "player"}),
        headers={
            "Content-Type": "application/json",
            "Cookie": host_cookie,
            "Origin": origin,
            "Sec-Fetch-Site": "same-origin",
        },
    )
    issued = json.loads(payload)["invitation"]
    invite_token = issued["token"]
    assert response.status == 200
    assert response.getheader("Cache-Control") == "no-store"
    assert issued["room_code"] == host_welcome["room_code"]
    assert issued["role"] == "player"
    assert issued["expires_at_ms"] > issued["issued_at_ms"]
    assert len(invite_token) == 43

    guest_cookie = session_cookie(web_server)
    response, claim_payload = request(
        web_server,
        "POST",
        "/api/invitations/claim",
        body=json.dumps(
            {
                "room_code": host_welcome["room_code"],
                "token": invite_token,
            }
        ),
        headers={
            "Content-Type": "application/json",
            "Cookie": guest_cookie,
            "Origin": origin,
            "Sec-Fetch-Site": "same-origin",
        },
    )
    claim = json.loads(claim_payload)["invitation"]
    assert response.status == 200
    assert claim == {
        key: issued[key]
        for key in ("room_code", "role", "issued_at_ms", "expires_at_ms")
    }
    assert invite_token not in claim_payload.decode("utf-8")
    guest_session_token = guest_cookie.partition("=")[2]
    assert invite_token not in repr(
        web_server.gateway._sessions[guest_session_token]
    )

    join = {
        "type": "join_room",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "room_code": host_welcome["room_code"],
        "display_name": "Invited Browser",
        # The inspected server-side claim owns the role.  JavaScript must not
        # supply a role that could drift from the capability scope.
    }
    response, join_payload = request(
        web_server,
        "POST",
        "/api/message",
        body=json.dumps(join),
        headers={
            "Content-Type": "application/json",
            "Cookie": guest_cookie,
            "Origin": origin,
            "Sec-Fetch-Site": "same-origin",
        },
    )
    joined = json.loads(join_payload)["events"]
    welcome = next(
        event for event in joined if event["type"] == "session_welcome"
    )
    assert response.status == 200
    assert welcome["role"] == "player"
    assert welcome["seat_index"] == 1
    assert welcome["reconnect_token"] is None
    assert invite_token not in join_payload.decode("utf-8")
    resume_cookie = response_cookie(response, "catan_room_resume")
    assert resume_cookie is not None
    assert "HttpOnly" in resume_cookie

    replay_cookie = session_cookie(web_server)
    response, replay_payload = request(
        web_server,
        "POST",
        "/api/invitations/claim",
        body=json.dumps(
            {
                "room_code": host_welcome["room_code"],
                "token": invite_token,
            }
        ),
        headers={
            "Content-Type": "application/json",
            "Cookie": replay_cookie,
            "Origin": origin,
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status == 403
    assert json.loads(replay_payload)["error"]["code"] == "authentication_failed"
    assert invite_token not in replay_payload.decode("utf-8")


def test_http_host_can_list_revoke_and_revoke_all_friend_invitations(web_server):
    host_cookie = session_cookie(web_server)
    _response, _payload, host_welcome = create_http_room(web_server, host_cookie)
    origin = same_origin(web_server)
    common_headers = {
        "Content-Type": "application/json",
        "Cookie": host_cookie,
        "Origin": origin,
        "Sec-Fetch-Site": "same-origin",
    }

    issued = []
    for role in ("player", "spectator"):
        response, payload = request(
            web_server,
            "POST",
            "/api/invitations",
            body=json.dumps({"role": role}),
            headers=common_headers,
        )
        assert response.status == 200
        invitation = json.loads(payload)["invitation"]
        assert len(invitation["invitation_id"]) == 22
        issued.append(invitation)

    response, payload = request(
        web_server,
        "POST",
        "/api/invitations/list",
        body="{}",
        headers=common_headers,
    )
    assert response.status == 200
    assert response.getheader("Cache-Control") == "no-store"
    listed = json.loads(payload)["invitations"]
    assert {item["invitation_id"] for item in listed} == {
        invitation["invitation_id"] for invitation in issued
    }
    assert all(
        set(item)
        == {
            "invitation_id",
            "room_code",
            "role",
            "issued_at_ms",
            "expires_at_ms",
        }
        and item["room_code"] == host_welcome["room_code"]
        for item in listed
    )
    listed_payload = payload.decode("utf-8")
    assert all(invitation["token"] not in listed_payload for invitation in issued)
    assert "token_digest" not in listed_payload
    assert "room_id" not in listed_payload

    response, payload = request(
        web_server,
        "DELETE",
        "/api/invitations",
        body=json.dumps({"invitation_id": issued[0]["invitation_id"]}),
        headers=common_headers,
    )
    revoked = json.loads(payload)
    assert response.status == 200
    assert response.getheader("Cache-Control") == "no-store"
    assert revoked["revoked_count"] == 1
    assert [item["invitation_id"] for item in revoked["invitations"]] == [
        issued[1]["invitation_id"]
    ]
    assert all(invitation["token"] not in payload.decode("utf-8") for invitation in issued)

    probe_cookie = session_cookie(web_server)
    response, claim_payload = request(
        web_server,
        "POST",
        "/api/invitations/claim",
        body=json.dumps(
            {
                "room_code": host_welcome["room_code"],
                "token": issued[0]["token"],
            }
        ),
        headers={
            "Content-Type": "application/json",
            "Cookie": probe_cookie,
            "Origin": origin,
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status == 403
    assert json.loads(claim_payload)["error"]["code"] == "authentication_failed"

    response, payload = request(
        web_server,
        "DELETE",
        "/api/invitations",
        body=json.dumps({"all": True}),
        headers=common_headers,
    )
    assert response.status == 200
    assert json.loads(payload) == {
        "api_version": 1,
        "revoked_count": 1,
        "invitations": [],
    }

    response, payload = request(
        web_server,
        "POST",
        "/api/invitations/list",
        body="{}",
        headers=common_headers,
    )
    assert response.status == 200
    assert json.loads(payload) == {"api_version": 1, "invitations": []}


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("POST", "/api/invitations", {"role": "player"}),
        ("POST", "/api/invitations/list", {}),
        ("DELETE", "/api/invitations", {"all": True}),
        (
            "POST",
            "/api/invitations/claim",
            {"room_code": "ABC234", "token": "A" * 43},
        ),
    ],
)
def test_friend_invitation_endpoints_require_explicit_same_origin(
    web_server,
    method,
    path,
    body,
):
    cookie = session_cookie(web_server)

    for headers in (
        {"Content-Type": "application/json", "Cookie": cookie},
        {
            "Content-Type": "application/json",
            "Cookie": cookie,
            "Origin": "https://attacker.invalid",
            "Sec-Fetch-Site": "cross-site",
        },
    ):
        response, payload = request(
            web_server,
            method,
            path,
            body=json.dumps(body),
            headers=headers,
        )
        assert response.status == 403
        assert json.loads(payload)["error"]["code"] == "cross_site_request"


def test_friend_invitation_http_schema_transport_and_authority_fail_closed(
    web_server,
    monkeypatch,
):
    host_cookie = session_cookie(web_server)
    _response, _payload, host_welcome = create_http_room(web_server, host_cookie)
    origin = same_origin(web_server)

    response, payload = request(
        web_server,
        "POST",
        "/api/invitations",
        body=json.dumps({"role": "host"}),
        headers={
            "Content-Type": "application/json",
            "Cookie": host_cookie,
            "Origin": origin,
        },
    )
    assert response.status == 400
    assert json.loads(payload)["error"]["code"] == "invalid_request"

    response, payload = request(
        web_server,
        "POST",
        "/api/invitations",
        body=json.dumps({"role": "player", "ttl_seconds": 999999}),
        headers={
            "Content-Type": "application/json",
            "Cookie": host_cookie,
            "Origin": origin,
        },
    )
    assert response.status == 400
    assert json.loads(payload)["error"]["code"] == "invalid_request"

    monkeypatch.setattr(
        web_server,
        "protected_room_access_allowed",
        lambda _client_key: False,
    )
    response, payload = request(
        web_server,
        "POST",
        "/api/invitations/claim",
        body=json.dumps(
            {"room_code": host_welcome["room_code"], "token": "A" * 43}
        ),
        headers={
            "Content-Type": "application/json",
            "Cookie": host_cookie,
            "Origin": origin,
        },
    )
    assert response.status == 403
    assert json.loads(payload)["error"]["code"] == "secure_transport_required"


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("POST", "/api/invitations/list", {"page": 1}),
        ("DELETE", "/api/invitations", {}),
        ("DELETE", "/api/invitations", {"all": False}),
        (
            "DELETE",
            "/api/invitations",
            {"all": True, "invitation_id": "A" * 22},
        ),
        (
            "DELETE",
            "/api/invitations",
            {"invitation_id": "A" * 22, "extra": True},
        ),
    ],
)
def test_invitation_management_http_schema_is_exact(
    web_server,
    method,
    path,
    body,
):
    host_cookie = session_cookie(web_server)
    create_http_room(web_server, host_cookie)
    response, payload = request(
        web_server,
        method,
        path,
        body=json.dumps(body),
        headers={
            "Content-Type": "application/json",
            "Cookie": host_cookie,
            "Origin": same_origin(web_server),
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status == 400
    assert json.loads(payload)["error"]["code"] == "invalid_request"


def test_invitation_management_http_is_host_only_and_not_found_is_generic(
    web_server,
):
    host_cookie = session_cookie(web_server)
    _response, _payload, welcome = create_http_room(web_server, host_cookie)
    origin = same_origin(web_server)

    guest_cookie = session_cookie(web_server)
    response, payload = request(
        web_server,
        "POST",
        "/api/message",
        body=json.dumps(
            {
                "type": "join_room",
                "protocol_version": NETWORK_PROTOCOL_VERSION,
                "room_code": welcome["room_code"],
                "display_name": "Viewer",
                "role": "spectator",
            }
        ),
        headers={
            "Content-Type": "application/json",
            "Cookie": guest_cookie,
            "Origin": origin,
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status == 200
    assert any(
        event["type"] == "session_welcome"
        for event in json.loads(payload)["events"]
    )

    response, payload = request(
        web_server,
        "POST",
        "/api/invitations/list",
        body="{}",
        headers={
            "Content-Type": "application/json",
            "Cookie": guest_cookie,
            "Origin": origin,
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status == 403
    assert json.loads(payload)["error"]["code"] == "forbidden"

    unknown_id = "A" * 22
    response, payload = request(
        web_server,
        "DELETE",
        "/api/invitations",
        body=json.dumps({"invitation_id": unknown_id}),
        headers={
            "Content-Type": "application/json",
            "Cookie": host_cookie,
            "Origin": origin,
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status == 404
    error = json.loads(payload)["error"]
    assert error["code"] == "invitation_not_found"
    assert unknown_id not in payload.decode("utf-8")


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("POST", "/api/invitations/list", {}),
        ("DELETE", "/api/invitations", {"all": True}),
    ],
)
def test_invitation_management_requires_loopback_or_https(
    web_server,
    monkeypatch,
    method,
    path,
    body,
):
    host_cookie = session_cookie(web_server)
    create_http_room(web_server, host_cookie)
    monkeypatch.setattr(
        web_server,
        "protected_room_access_allowed",
        lambda _client_key: False,
    )
    response, payload = request(
        web_server,
        method,
        path,
        body=json.dumps(body),
        headers={
            "Content-Type": "application/json",
            "Cookie": host_cookie,
            "Origin": same_origin(web_server),
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status == 403
    assert json.loads(payload)["error"]["code"] == "secure_transport_required"


def test_normal_http_join_cannot_supply_a_raw_friend_invitation(web_server):
    host_cookie = session_cookie(web_server)
    _response, _payload, host_welcome = create_http_room(web_server, host_cookie)
    guest_cookie = session_cookie(web_server)
    raw_invite = "A" * 43
    join = {
        "type": "join_room",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "room_code": host_welcome["room_code"],
        "display_name": "Bearer Probe",
        "role": "player",
        "invite_token": raw_invite,
    }

    response, payload = request(
        web_server,
        "POST",
        "/api/message",
        body=json.dumps(join),
        headers={
            "Content-Type": "application/json",
            "Cookie": guest_cookie,
            "Origin": same_origin(web_server),
        },
    )

    assert response.status == 200
    error = next(
        event
        for event in json.loads(payload)["events"]
        if event["type"] == "request_error"
    )
    assert error["code"] == "invalid_request"
    assert raw_invite not in payload.decode("utf-8")


def test_http_message_cannot_supply_a_javascript_readable_reconnect_bearer(web_server):
    cookie = session_cookie(web_server)
    reconnect = {
        "type": "reconnect_room",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "room_code": "ABC234",
        "reconnect_token": "A" * 43,
    }

    response, payload = request(
        web_server,
        "POST",
        "/api/message",
        body=json.dumps(reconnect),
        headers={"Content-Type": "application/json", "Cookie": cookie},
    )

    assert response.status == 403
    assert json.loads(payload)["error"]["code"] == "resume_cookie_required"


@pytest.mark.parametrize(
    "resume_value",
    [
        "broken",
        "v1.ABC234.short",
        "v1.IIIIII." + "A" * 43,
    ],
)
def test_malformed_room_resume_cookie_is_cleared(web_server, resume_value):
    cookie = session_cookie(web_server)
    response, payload = request(
        web_server,
        "POST",
        "/api/resume",
        headers={
            "Cookie": f"{cookie}; catan_room_resume={resume_value}",
            "Origin": same_origin(web_server),
        },
    )

    assert response.status == 200
    assert json.loads(payload)["events"] == []
    expired = response_cookie(response, "catan_room_resume")
    assert expired is not None
    assert "Max-Age=0" in expired


def test_duplicate_room_resume_cookie_is_rejected_and_cleared(web_server):
    cookie = session_cookie(web_server)
    credential = "v1.ABC234." + "A" * 43
    response, payload = request(
        web_server,
        "POST",
        "/api/resume",
        headers={
            "Cookie": (
                f"{cookie}; catan_room_resume={credential}; "
                f"catan_room_resume={credential}"
            ),
            "Origin": same_origin(web_server),
        },
    )

    assert response.status == 200
    assert json.loads(payload)["events"] == []
    assert "Max-Age=0" in response_cookie(response, "catan_room_resume")


def test_oversized_cookie_header_is_rejected_before_resume(web_server):
    cookie = session_cookie(web_server)
    response, payload = request(
        web_server,
        "POST",
        "/api/resume",
        headers={
            "Cookie": f"{cookie}; padding={'x' * 8200}",
            "Origin": same_origin(web_server),
        },
    )

    assert response.status == 401
    assert json.loads(payload)["error"]["code"] == "session_required"


def test_definitively_invalid_resume_cookie_is_cleared(web_server):
    original_session = session_cookie(web_server)
    _response, _payload, welcome = create_http_room(web_server, original_session)
    replacement_session = session_cookie(web_server)
    invalid = f"v1.{welcome['room_code']}.{'A' * 43}"

    response, payload = request(
        web_server,
        "POST",
        "/api/resume",
        headers={
            "Cookie": f"{replacement_session}; catan_room_resume={invalid}",
            "Origin": same_origin(web_server),
        },
    )

    assert response.status == 200
    error = next(
        event for event in json.loads(payload)["events"]
        if event["type"] == "request_error"
    )
    assert error["code"] == "authentication_failed"
    assert "Max-Age=0" in response_cookie(response, "catan_room_resume")


def test_already_connected_resume_keeps_valid_cookie_for_later_recovery(web_server):
    original_session = session_cookie(web_server)
    create_response, _payload, _welcome = create_http_room(
        web_server,
        original_session,
    )
    resume_cookie = response_cookie(
        create_response,
        "catan_room_resume",
    ).split(";", 1)[0]
    replacement_session = session_cookie(web_server)

    response, payload = request(
        web_server,
        "POST",
        "/api/resume",
        headers={
            "Cookie": f"{replacement_session}; {resume_cookie}",
            "Origin": same_origin(web_server),
        },
    )

    assert response.status == 200
    error = next(
        event for event in json.loads(payload)["events"]
        if event["type"] == "request_error"
    )
    assert error["code"] == "invalid_state"
    assert response_cookie(response, "catan_room_resume") is None


def test_delete_resume_explicitly_clears_only_room_recovery(web_server):
    cookie = session_cookie(web_server)
    create_response, _payload, _welcome = create_http_room(web_server, cookie)
    resume_cookie = response_cookie(
        create_response,
        "catan_room_resume",
    ).split(";", 1)[0]

    response, payload = request(
        web_server,
        "DELETE",
        "/api/resume",
        headers={"Cookie": f"{cookie}; {resume_cookie}"},
    )

    assert response.status == 200
    assert json.loads(payload) == {"api_version": 1, "cleared": True}
    expired = response_cookie(response, "catan_room_resume")
    assert expired is not None
    assert "HttpOnly" in expired
    assert "SameSite=Strict" in expired
    assert "Max-Age=0" in expired

    response, payload = request(
        web_server,
        "GET",
        "/api/events",
        headers={"Cookie": cookie},
    )
    assert response.status == 200
    assert "events" in json.loads(payload)


def test_loopback_http_accepts_protected_room_without_exposing_credential(web_server):
    cookie = session_cookie(web_server)
    passphrase = "correct horse battery staple"
    create = {
        "type": "create_room",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "display_name": "Protected Host",
        "settings": {
            "player_count": 2,
            "victory_target": 7,
            "board_mode": "constrained",
            "board_seed": 86712347,
        },
        "passphrase": passphrase,
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
    lobby = next(
        event["lobby"]
        for event in document["events"]
        if event["type"] == "lobby_snapshot"
    )
    assert lobby["access"] == {"passphrase_required": True}
    assert passphrase not in payload.decode("utf-8")


def test_room_access_rate_limit_is_authoritative_over_http(web_server):
    cookie = session_cookie(web_server)
    join = {
        "type": "join_room",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "display_name": "Probe",
        "room_code": "ABC234",
        "role": "player",
    }
    for _ in range(5):
        response, payload = request(
            web_server,
            "POST",
            "/api/message",
            body=json.dumps(join),
            headers={"Content-Type": "application/json", "Cookie": cookie},
        )
        assert response.status == 200
        assert json.loads(payload)["events"][0]["code"] == "room_not_found"

    response, payload = request(
        web_server,
        "POST",
        "/api/message",
        body=json.dumps(join),
        headers={"Content-Type": "application/json", "Cookie": cookie},
    )
    document = json.loads(payload)
    assert response.status == 429
    assert document["error"]["code"] == "room_access_rate_limited"
    assert document["error"]["retry_after_seconds"] >= 1
    assert response.getheader("Retry-After") == str(
        document["error"]["retry_after_seconds"]
    )


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

    response, payload = request(
        web_server,
        "POST",
        "/api/session",
        headers={"Sec-Fetch-Site": "same-site"},
    )
    assert response.status == 403
    assert json.loads(payload)["error"]["code"] == "cross_site_request"


def test_websocket_requires_an_explicit_same_origin_header(web_server):
    cookie = session_cookie(web_server)
    response, payload = request(
        web_server,
        "GET",
        "/api/socket",
        headers={"Cookie": cookie},
    )
    assert response.status == 403
    assert json.loads(payload)["error"]["code"] == "cross_site_request"


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.0.0.1:1",
        "attacker@127.0.0.1",
        "localhost.invalid",
    ],
)
def test_host_header_requires_exact_allowed_authority(web_server, host):
    response, payload = request(
        web_server,
        "GET",
        "/api/health",
        headers={"Host": host},
    )
    assert response.status == 400
    assert json.loads(payload)["error"]["code"] == "invalid_host"


@pytest.mark.parametrize(
    "host",
    [
        "192.168.50.20:{port}",
        "catan-box.local:{port}",
        "CATAN-BOX.LOCAL:{port}",
        "[fd00::20]:{port}",
        "[FD00:0:0:0:0:0:0:20]:{port}",
    ],
)
def test_lan_host_authority_normalizes_ipv4_ipv6_and_dns(
    lan_web_server,
    host,
):
    response, payload = request(
        lan_web_server,
        "GET",
        "/api/health",
        headers={"Host": host.format(port=lan_web_server.server_port)},
    )

    assert response.status == 200
    assert json.loads(payload) == {
        "api_version": 1,
        "status": "ok",
        "transport": {"http": "http", "websocket": "ws"},
        "access_profile": "trusted_lan",
    }


def test_lan_session_cookie_remains_http_only_without_secure(lan_web_server):
    authority = f"catan-box.local:{lan_web_server.server_port}"
    response, payload = request(
        lan_web_server,
        "POST",
        "/api/session",
        headers={
            "Host": authority,
            "Origin": f"http://{authority}",
            "Sec-Fetch-Site": "same-origin",
        },
    )

    assert response.status == 200
    assert json.loads(payload)["api_version"] == 1
    cookie = response.getheader("Set-Cookie")
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert "Secure" not in cookie


def test_lan_origin_must_match_request_host_not_just_allowlist(lan_web_server):
    response, payload = request(
        lan_web_server,
        "POST",
        "/api/session",
        headers={
            "Host": f"192.168.50.20:{lan_web_server.server_port}",
            "Origin": f"http://catan-box.local:{lan_web_server.server_port}",
        },
    )

    assert response.status == 403
    assert json.loads(payload)["error"]["code"] == "cross_site_request"


def _raw_http_status(server, request_text):
    peer = socket.create_connection(("127.0.0.1", server.server_port), timeout=3)
    reader = peer.makefile("rb")
    try:
        peer.sendall(request_text.encode("ascii"))
        return reader.readline()
    finally:
        reader.close()
        peer.close()


def test_duplicate_host_header_is_rejected(web_server):
    authority = f"127.0.0.1:{web_server.server_port}"
    status = _raw_http_status(
        web_server,
        (
            "GET /api/health HTTP/1.1\r\n"
            f"Host: {authority}\r\n"
            f"Host: {authority}\r\n"
            "Connection: close\r\n\r\n"
        ),
    )

    assert status.startswith(b"HTTP/1.1 400")


def test_duplicate_origin_header_is_rejected(web_server):
    authority = f"127.0.0.1:{web_server.server_port}"
    status = _raw_http_status(
        web_server,
        (
            "POST /api/session HTTP/1.1\r\n"
            f"Host: {authority}\r\n"
            f"Origin: http://{authority}\r\n"
            f"Origin: http://{authority}\r\n"
            "Content-Length: 0\r\n"
            "Connection: close\r\n\r\n"
        ),
    )

    assert status.startswith(b"HTTP/1.1 403")


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

        membership_messages = [
            {
                "type": "create_room",
                "protocol_version": NETWORK_PROTOCOL_VERSION,
                "display_name": "Socket Host",
                "settings": {
                    "player_count": 2,
                    "victory_target": 5,
                    "board_mode": "constrained",
                    "board_seed": 4242,
                },
            },
            {
                "type": "join_room",
                "protocol_version": NETWORK_PROTOCOL_VERSION,
                "room_code": "ABC234",
                "display_name": "Socket Guest",
                "role": "player",
            },
            {
                "type": "reconnect_room",
                "protocol_version": NETWORK_PROTOCOL_VERSION,
                "room_code": "ABC234",
                "reconnect_token": "A" * 43,
            },
            {
                "type": "leave_room",
                "protocol_version": NETWORK_PROTOCOL_VERSION,
            },
        ]
        for index, membership_message in enumerate(membership_messages):
            peer.sendall(
                encode_websocket_frame(
                    json.dumps(membership_message).encode("utf-8"),
                    opcode=WebSocketOpcode.TEXT,
                    masking_key=index.to_bytes(4, "big"),
                )
            )
            blocked = json.loads(
                read_websocket_frame(reader, require_mask=False).payload
            )
            assert blocked["kind"] == "response"
            assert blocked["error"]["code"] == "http_required"
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


def test_websocket_pushes_ai_progress_without_client_heartbeat(web_server):
    cookie = session_cookie(web_server)
    create_http_room(
        web_server,
        cookie,
        name="Host",
        settings={
            "player_count": 2,
            "ai_player_count": 1,
            "ai_personality_mode": "mixed",
            "victory_target": 10,
            "board_mode": "constrained",
            "board_seed": 4242,
        },
    )
    old_peer = socket.create_connection(
        ("127.0.0.1", web_server.server_port),
        timeout=4,
    )
    old_reader = old_peer.makefile("rb")
    peer = socket.create_connection(("127.0.0.1", web_server.server_port), timeout=4)
    reader = peer.makefile("rb")

    def upgrade(connection, connection_reader):
        connection.sendall(
            (
                "GET /api/socket HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{web_server.server_port}\r\n"
                "Connection: Upgrade\r\n"
                "Upgrade: websocket\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                f"Origin: http://127.0.0.1:{web_server.server_port}\r\n"
                f"Cookie: {cookie}\r\n\r\n"
            ).encode("ascii")
        )
        while connection_reader.readline() != b"\r\n":
            pass
        bootstrap = read_websocket_frame(connection_reader, require_mask=False)
        assert json.loads(bootstrap.payload)["kind"] == "bootstrap"

    def exchange(document):
        peer.sendall(
            encode_websocket_frame(
                json.dumps(document).encode("utf-8"),
                opcode=WebSocketOpcode.TEXT,
                masking_key=b"test",
            )
        )
        frame = read_websocket_frame(reader, require_mask=False)
        return json.loads(frame.payload)

    try:
        upgrade(old_peer, old_reader)
        upgrade(peer, reader)
        superseded = read_websocket_frame(old_reader, require_mask=False)
        assert superseded.opcode is WebSocketOpcode.CLOSE

        assert (
            exchange(
                {
                    "type": "set_ready",
                    "protocol_version": NETWORK_PROTOCOL_VERSION,
                    "ready": True,
                }
            )["kind"]
            == "response"
        )
        started = exchange(
            {
                "type": "start_game",
                "protocol_version": NETWORK_PROTOCOL_VERSION,
            }
        )
        assert started["kind"] == "response"

        rolled = exchange(
            build_game_command(
                sequence=0,
                expected_revision=0,
                command="roll_dice",
            )
        )
        assert rolled["kind"] == "response"
        assert (
            max(
                event["revision"]
                for event in rolled["events"]
                if event["type"] == "state_snapshot"
            )
            == 1
        )

        # Send no heartbeat or other client message.  The server must still
        # deliver the AI's next authoritative revision over the open socket.
        pushed = json.loads(read_websocket_frame(reader, require_mask=False).payload)
        assert pushed["kind"] == "push"
        ai_snapshots = [
            event for event in pushed["events"] if event["type"] == "state_snapshot"
        ]
        assert ai_snapshots
        assert max(event["revision"] for event in ai_snapshots) >= 2
    finally:
        old_reader.close()
        old_peer.close()
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


def test_non_loopback_bind_is_allowed_only_in_explicit_lan_mode():
    server = create_web_server(
        "0.0.0.0",
        0,
        lan_mode=True,
        allowed_hosts=("192.168.50.20",),
    )
    try:
        assert server.lan_mode is True
        assert "192.168.50.20" in server.allowed_hosts
        assert "0.0.0.0" not in server.allowed_hosts
    finally:
        server.server_close()


def test_room_access_transport_capability_uses_tcp_peer_and_tls_only(web_server):
    assert web_server.protected_room_access_allowed("127.0.0.1") is True
    assert web_server.protected_room_access_allowed("::1") is True
    assert web_server.protected_room_access_allowed("192.168.50.20") is False
    assert web_server.protected_room_access_allowed("catan-box.local") is False

    web_server.tls_enabled = True
    assert web_server.protected_room_access_allowed("192.168.50.20") is True


@pytest.mark.parametrize(
    "allowed_hosts",
    [
        (),
        ("127.0.0.1",),
        ("localhost", "::1"),
    ],
)
def test_lan_mode_requires_an_explicit_non_loopback_allowed_host(allowed_hosts):
    with pytest.raises(ValueError, match="allowed-host"):
        create_web_server(
            "127.0.0.1",
            0,
            lan_mode=True,
            allowed_hosts=allowed_hosts,
        )


@pytest.mark.parametrize(
    "allowed_host",
    [
        "Example.local",
        "example.local.",
        "http://example.local",
        "example.local:8765",
        "[2001:db8::20]",
        "2001:0db8:0:0:0:0:0:20",
        "0.0.0.0",
        "::",
        "192.168.001.20",
        "-invalid.local",
        "catan_box.local",
        "カタン.local",
    ],
)
def test_lan_allowed_host_requires_strict_canonical_host_only_value(allowed_host):
    with pytest.raises(ValueError, match="allowed host"):
        create_web_server(
            "127.0.0.1",
            0,
            lan_mode=True,
            allowed_hosts=(allowed_host,),
        )


def test_lan_allowed_hosts_reject_duplicates():
    with pytest.raises(ValueError, match="duplicate allowed host"):
        create_web_server(
            "127.0.0.1",
            0,
            lan_mode=True,
            allowed_hosts=("catan-box.local", "catan-box.local"),
        )


@pytest.mark.parametrize(
    "allowed_host",
    [
        "8.8.8.8",
        "100.64.0.10",
        "192.0.2.10",
        "2001:db8::20",
        "2606:4700:4700::1111",
        "example.com",
        "catan.example.com",
        "catan.internal",
    ],
)
def test_lan_allowed_host_rejects_public_or_ambiguous_networks(allowed_host):
    with pytest.raises(ValueError, match="allowed host"):
        create_web_server(
            "127.0.0.1",
            0,
            lan_mode=True,
            allowed_hosts=(allowed_host,),
        )


def test_lan_bind_rejects_a_global_interface_address_before_binding():
    with pytest.raises(ValueError, match="bind host.*trusted LAN"):
        create_web_server(
            "8.8.8.8",
            0,
            lan_mode=True,
            allowed_hosts=("192.168.50.20",),
        )


@pytest.mark.parametrize(
    ("peer", "trusted"),
    [
        ("127.0.0.1", True),
        ("192.168.1.25", True),
        ("172.16.0.5", True),
        ("10.20.30.40", True),
        ("169.254.2.3", True),
        ("fd00::25", True),
        ("fe80::25%en0", True),
        ("::ffff:192.168.1.25", True),
        ("8.8.8.8", False),
        ("100.64.0.10", False),
        ("192.0.2.10", False),
        ("2001:db8::25", False),
        ("2606:4700:4700::1111", False),
        ("not-an-address", False),
    ],
)
def test_lan_peer_boundary_is_explicit(peer, trusted):
    assert web_server_module._is_trusted_lan_peer(peer) is trusted


def test_allowed_hosts_cannot_silently_enable_lan_mode():
    with pytest.raises(ValueError, match="requires lan_mode"):
        create_web_server(
            "127.0.0.1",
            0,
            allowed_hosts=("catan-box.local",),
        )


def test_tls_requires_a_complete_loadable_certificate_pair():
    with pytest.raises(ValueError, match="both a certificate and a private key"):
        create_web_server("127.0.0.1", 0, tls_certfile="server-cert.pem")

    with pytest.raises(ValueError, match="could not be loaded"):
        create_web_server(
            "127.0.0.1",
            0,
            tls_certfile="missing-cert.pem",
            tls_keyfile="missing-key.pem",
        )


def test_tls_transport_uses_https_origin_wss_health_and_secure_cookie(monkeypatch):
    class FakeTlsContext:
        def wrap_socket(self, sock, *, server_side):
            assert server_side is True
            return sock

    monkeypatch.setattr(
        web_server_module,
        "_build_server_tls_context",
        lambda certfile, keyfile: FakeTlsContext(),
    )
    server = create_web_server(
        "127.0.0.1",
        0,
        tls_certfile="server-cert.pem",
        tls_keyfile="server-key.pem",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response, payload = request(server, "GET", "/api/health")
        assert response.status == 200
        assert json.loads(payload) == {
            "api_version": 1,
            "status": "ok",
            "transport": {"http": "https", "websocket": "wss"},
            "access_profile": "loopback",
        }

        authority = f"127.0.0.1:{server.server_port}"
        response, _payload = request(
            server,
            "POST",
            "/api/session",
            headers={"Host": authority, "Origin": f"https://{authority}"},
        )
        assert response.status == 200
        cookie = response.getheader("Set-Cookie")
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie
        assert "Secure" in cookie

        response, payload = request(
            server,
            "POST",
            "/api/session",
            headers={"Host": authority, "Origin": f"http://{authority}"},
        )
        assert response.status == 403
        assert json.loads(payload)["error"]["code"] == "cross_site_request"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--internet-public"],
        [
            "--internet-public",
            "--tls-cert",
            "server-cert.pem",
            "--tls-key",
            "server-key.pem",
        ],
    ],
)
def test_internet_public_flag_fails_closed_until_security_profile_exists(
    capsys,
    arguments,
):
    with pytest.raises(SystemExit) as stopped:
        web_main(arguments)

    assert stopped.value.code == 2
    error = capsys.readouterr().err
    assert "Internet公開はまだ有効化できません" in error
    assert "TLS/WSS" in error
    assert "account認証" in error


def test_cli_rejects_public_bind_with_a_safe_error(capsys):
    with pytest.raises(SystemExit) as stopped:
        web_main(["--host", "0.0.0.0", "--port", "0"])

    assert stopped.value.code == 2
    assert "loopback" in capsys.readouterr().err


def test_cli_requires_lan_allowed_host_and_lan_flag(capsys):
    with pytest.raises(SystemExit) as stopped:
        web_main(["--lan"])
    assert stopped.value.code == 2
    assert "allowed-host" in capsys.readouterr().err

    with pytest.raises(SystemExit) as stopped:
        web_main(["--allowed-host", "192.168.50.20"])
    assert stopped.value.code == 2
    assert "--lan" in capsys.readouterr().err


def test_cli_requires_tls_certificate_and_key_together(capsys):
    with pytest.raises(SystemExit) as stopped:
        web_main(["--tls-cert", "server-cert.pem"])
    assert stopped.value.code == 2
    assert "--tls-certと--tls-key" in capsys.readouterr().err


def test_cli_starts_explicit_lan_mode_with_safe_warning(monkeypatch, capsys):
    recorded = {}

    class FakeServer:
        server_address = ("0.0.0.0", 8765)

        def serve_forever(self, *, poll_interval):
            recorded["poll_interval"] = poll_interval
            raise KeyboardInterrupt

        def server_close(self):
            recorded["closed"] = True

    def fake_create_web_server(host, port, **kwargs):
        recorded.update(host=host, port=port, kwargs=kwargs)
        return FakeServer()

    monkeypatch.setattr(
        web_main_module,
        "create_web_server",
        fake_create_web_server,
    )

    assert (
        web_main(
            [
                "--host",
                "0.0.0.0",
                "--port",
                "8765",
                "--lan",
                "--allowed-host",
                "192.168.50.20",
            ]
        )
        == 0
    )
    assert recorded["kwargs"] == {
        "lan_mode": True,
        "allowed_hosts": ["192.168.50.20"],
        "tls_certfile": None,
        "tls_keyfile": None,
    }
    assert recorded["closed"] is True
    output = capsys.readouterr().out
    assert "http://192.168.50.20:8765/" in output
    assert "信頼できるLAN専用" in output
    assert "Internetへportを公開しない" in output


def test_cli_starts_opt_in_tls_and_prints_https_wss_warning(monkeypatch, capsys):
    recorded = {}

    class FakeServer:
        server_address = ("127.0.0.1", 8765)

        def serve_forever(self, *, poll_interval):
            recorded["poll_interval"] = poll_interval
            raise KeyboardInterrupt

        def server_close(self):
            recorded["closed"] = True

    def fake_create_web_server(host, port, **kwargs):
        recorded.update(host=host, port=port, kwargs=kwargs)
        return FakeServer()

    monkeypatch.setattr(
        web_main_module,
        "create_web_server",
        fake_create_web_server,
    )

    assert (
        web_main(
            [
                "--tls-cert",
                "server-cert.pem",
                "--tls-key",
                "server-key.pem",
            ]
        )
        == 0
    )
    assert recorded["kwargs"] == {
        "lan_mode": False,
        "allowed_hosts": [],
        "tls_certfile": "server-cert.pem",
        "tls_keyfile": "server-key.pem",
    }
    assert recorded["closed"] is True
    output = capsys.readouterr().out
    assert "https://127.0.0.1:8765/" in output
    assert "HTTPS / WSSを有効化" in output
    assert "Internetへportを公開しない" in output
