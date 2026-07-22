from http.client import HTTPConnection
import json
import threading

import pytest

from game.network_protocol import NETWORK_PROTOCOL_VERSION
from game.web_server import create_web_server
import game.web_server as web_server_module
import web_main as web_main_module
from web_main import main as web_main


TAILSCALE_IPV4 = "100.101.1.2"
TAILSCALE_IPV6 = "fd7a:115c:a1e0:1234::2"
TAILSCALE_HOSTNAME = "catan.tail1234.ts.net"


def _request(server, method, path, *, body=None, headers=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        return response, json.loads(payload)
    finally:
        connection.close()


def _session_cookie(server):
    response, document = _request(server, "POST", "/api/session")
    assert response.status == 200
    assert document["api_version"] == 1
    return response.getheader("Set-Cookie").split(";", 1)[0]


def _origin(server):
    return f"https://127.0.0.1:{server.server_port}"


def _room_message(*, invite_only=False, passphrase=None):
    message = {
        "type": "create_room",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "display_name": "Host",
        "settings": {
            "player_count": 2,
            "victory_target": 7,
            "board_mode": "fully_random",
            "board_seed": 86712347,
        },
        "invite_only": invite_only,
    }
    if passphrase is not None:
        message["passphrase"] = passphrase
    return message


@pytest.mark.parametrize(
    ("peer", "trusted"),
    [
        (TAILSCALE_IPV4, True),
        (TAILSCALE_IPV6, True),
        ("::ffff:100.101.1.2", True),
        ("100.100.100.100", False),
        ("100.115.92.1", False),
        ("100.101.102.103", False),
        ("fd7a:115c:a1e0::53", False),
        ("100.63.255.255", False),
        ("100.128.0.1", False),
        ("192.168.1.20", False),
        ("8.8.8.8", False),
        ("not-an-address", False),
    ],
)
def test_friends_vpn_peer_boundary_is_tailscale_specific(peer, trusted):
    assert web_server_module._is_tailscale_peer(peer) is trusted


@pytest.mark.parametrize("bind_host", [TAILSCALE_IPV4, TAILSCALE_IPV6])
def test_friends_vpn_exposure_accepts_exact_tailscale_bind_and_dns_name(
    bind_host,
):
    allowed = web_server_module._validate_server_exposure(
        bind_host,
        lan_mode=False,
        friends_vpn_mode=True,
        allowed_hosts=(TAILSCALE_HOSTNAME,),
    )
    assert allowed == frozenset({TAILSCALE_HOSTNAME})


@pytest.mark.parametrize(
    "bind_host",
    [
        "0.0.0.0",
        "::",
        "127.0.0.1",
        "192.168.1.20",
        "100.100.100.100",
        "100.115.92.1",
        "fd7a:115c:a1e0::53",
        "8.8.8.8",
        TAILSCALE_HOSTNAME,
    ],
)
def test_friends_vpn_rejects_wildcard_non_overlay_and_reserved_binds(bind_host):
    with pytest.raises(ValueError, match="friends VPN bind host"):
        web_server_module._validate_server_exposure(
            bind_host,
            lan_mode=False,
            friends_vpn_mode=True,
            allowed_hosts=(TAILSCALE_HOSTNAME,),
        )


@pytest.mark.parametrize(
    "allowed_hosts",
    [
        (),
        (TAILSCALE_HOSTNAME, "second.tail1234.ts.net"),
        ("catan.local",),
        ("tail1234.ts.net",),
        ("Catan.tail1234.ts.net",),
        (TAILSCALE_IPV4,),
    ],
)
def test_friends_vpn_requires_one_canonical_certificate_hostname(allowed_hosts):
    with pytest.raises(ValueError, match="friends VPN|canonical form"):
        web_server_module._validate_server_exposure(
            TAILSCALE_IPV4,
            lan_mode=False,
            friends_vpn_mode=True,
            allowed_hosts=allowed_hosts,
        )


def test_friends_vpn_and_lan_profiles_are_mutually_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        web_server_module._validate_server_exposure(
            TAILSCALE_IPV4,
            lan_mode=True,
            friends_vpn_mode=True,
            allowed_hosts=(TAILSCALE_HOSTNAME,),
        )


def test_friends_vpn_server_requires_tls_before_binding():
    with pytest.raises(ValueError, match="requires TLS"):
        create_web_server(
            TAILSCALE_IPV4,
            0,
            friends_vpn_mode=True,
            allowed_hosts=(TAILSCALE_HOSTNAME,),
        )


def test_friends_vpn_http_boundary_forces_invite_only_and_blocks_direct_join(
    monkeypatch,
):
    # Bind the test harness to loopback while simulating an already verified
    # overlay connection.  Construction validation is covered independently.
    server = create_web_server("127.0.0.1", 0)
    server.friends_vpn_mode = True
    server.tls_enabled = True
    server.transport_scheme = "https"
    server.websocket_scheme = "wss"
    monkeypatch.setattr(web_server_module, "_is_tailscale_peer", lambda _peer: True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host_cookie = _session_cookie(server)
        response, document = _request(
            server,
            "POST",
            "/api/message",
            body=json.dumps(_room_message(invite_only=False)),
            headers={
                "Content-Type": "application/json",
                "Cookie": host_cookie,
                "Origin": _origin(server),
            },
        )
        assert response.status == 200
        welcome = next(
            event
            for event in document["events"]
            if event["type"] == "session_welcome"
        )
        lobby = next(
            event["lobby"]
            for event in document["events"]
            if event["type"] == "lobby_snapshot"
        )
        assert lobby["access"] == {"passphrase_required": True}

        response, invitation_document = _request(
            server,
            "POST",
            "/api/invitations",
            body=json.dumps({"role": "player"}),
            headers={
                "Content-Type": "application/json",
                "Cookie": host_cookie,
                "Origin": _origin(server),
            },
        )
        assert response.status == 200
        invitation = invitation_document["invitation"]
        assert invitation["room_code"] == welcome["room_code"]

        friend_cookie = _session_cookie(server)
        direct_join = {
            "type": "join_room",
            "protocol_version": NETWORK_PROTOCOL_VERSION,
            "room_code": welcome["room_code"],
            "display_name": "Friend",
            "role": "player",
        }
        response, rejected = _request(
            server,
            "POST",
            "/api/message",
            body=json.dumps(direct_join),
            headers={
                "Content-Type": "application/json",
                "Cookie": friend_cookie,
                "Origin": _origin(server),
            },
        )
        assert response.status == 403
        assert rejected["error"]["code"] == "friends_invitation_required"

        unclaimed_join = dict(direct_join)
        unclaimed_join.pop("role")
        response, unclaimed = _request(
            server,
            "POST",
            "/api/message",
            body=json.dumps(unclaimed_join),
            headers={
                "Content-Type": "application/json",
                "Cookie": friend_cookie,
                "Origin": _origin(server),
            },
        )
        assert response.status == 200
        assert not any(
            event["type"] == "session_welcome" for event in unclaimed["events"]
        )
        assert any(
            event["type"] == "request_error" for event in unclaimed["events"]
        )

        response, claimed = _request(
            server,
            "POST",
            "/api/invitations/claim",
            body=json.dumps(
                {
                    "room_code": welcome["room_code"],
                    "token": invitation["token"],
                }
            ),
            headers={
                "Content-Type": "application/json",
                "Cookie": friend_cookie,
                "Origin": _origin(server),
            },
        )
        assert response.status == 200
        assert claimed["invitation"]["role"] == "player"

        invite_join = dict(direct_join)
        invite_join.pop("role")
        response, joined = _request(
            server,
            "POST",
            "/api/message",
            body=json.dumps(invite_join),
            headers={
                "Content-Type": "application/json",
                "Cookie": friend_cookie,
                "Origin": _origin(server),
            },
        )
        assert response.status == 200
        assert any(
            event["type"] == "session_welcome" for event in joined["events"]
        )

        response, health = _request(server, "GET", "/api/health")
        assert response.status == 200
        assert health["access_profile"] == "friends_vpn"
        assert health["transport"] == {"http": "https", "websocket": "wss"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_friends_vpn_rejects_passphrase_room_instead_of_silently_rewriting(
    monkeypatch,
):
    server = create_web_server("127.0.0.1", 0)
    server.friends_vpn_mode = True
    server.tls_enabled = True
    server.transport_scheme = "https"
    monkeypatch.setattr(web_server_module, "_is_tailscale_peer", lambda _peer: True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cookie = _session_cookie(server)
        response, document = _request(
            server,
            "POST",
            "/api/message",
            body=json.dumps(
                _room_message(
                    invite_only=True,
                    passphrase="friends should not share this passphrase",
                )
            ),
            headers={
                "Content-Type": "application/json",
                "Cookie": cookie,
                "Origin": _origin(server),
            },
        )
        assert response.status == 403
        assert document["error"]["code"] == "friends_invitation_required"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_friends_vpn_keeps_membership_messages_off_websocket():
    assert {"create_room", "join_room"} <= (
        web_server_module._WEBSOCKET_FORBIDDEN_MEMBERSHIP_MESSAGES
    )


def test_cli_starts_explicit_friends_vpn_profile(monkeypatch, capsys):
    recorded = {}

    class FakeServer:
        server_address = (TAILSCALE_IPV4, 8765)

        def serve_forever(self, *, poll_interval):
            recorded["poll_interval"] = poll_interval
            raise KeyboardInterrupt

        def server_close(self):
            recorded["closed"] = True

    def fake_create_web_server(host, port, **kwargs):
        recorded.update(host=host, port=port, kwargs=kwargs)
        return FakeServer()

    monkeypatch.setattr(web_main_module, "create_web_server", fake_create_web_server)

    assert (
        web_main(
            [
                "--host",
                TAILSCALE_IPV4,
                "--friends-vpn",
                "--allowed-host",
                TAILSCALE_HOSTNAME,
                "--tls-cert",
                "/safe/catan.crt",
                "--tls-key",
                "/safe/catan.key",
            ]
        )
        == 0
    )
    assert recorded["kwargs"] == {
        "lan_mode": False,
        "friends_vpn_mode": True,
        "allowed_hosts": [TAILSCALE_HOSTNAME],
        "tls_certfile": "/safe/catan.crt",
        "tls_keyfile": "/safe/catan.key",
    }
    assert recorded["closed"] is True
    output = capsys.readouterr().out
    assert f"https://{TAILSCALE_HOSTNAME}:8765/" in output
    assert "友人VPN専用" in output
    assert "Funnel" in output
    assert "port開放は有効にしない" in output


@pytest.mark.parametrize(
    "arguments",
    [
        ["--friends-vpn"],
        ["--lan", "--friends-vpn"],
        ["--internet-public", "--friends-vpn"],
    ],
)
def test_cli_friends_vpn_profile_fails_closed_on_incomplete_or_public_mode(
    capsys,
    arguments,
):
    with pytest.raises(SystemExit) as stopped:
        web_main(arguments)
    assert stopped.value.code == 2
    error = capsys.readouterr().err
    if "--internet-public" in arguments:
        assert "Internet公開はまだ有効化できません" in error
    elif "--lan" in arguments:
        assert "同時に指定できません" in error
    else:
        assert "--tls-certと--tls-key" in error
