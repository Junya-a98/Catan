import pytest

from game.network_protocol import NETWORK_PROTOCOL_VERSION
from game.shared_rate_limit import (
    RateLimitDecision,
    SharedRateLimitError,
    SQLiteSharedRateLimitStore,
)
from game.web_gateway import WebGateway, WebGatewayError, WebRateLimits


def _message(message_type, **payload):
    return {
        "type": message_type,
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        **payload,
    }


def _paths(tmp_path):
    return tmp_path / "limits.sqlite3", tmp_path / "limits.key"


def test_session_creation_limit_survives_gateway_and_store_restart(tmp_path):
    database, key = _paths(tmp_path)
    now = [1_800_000_000.0]
    limits = WebRateLimits(
        window_seconds=10,
        session_creations_per_client=2,
    )
    store_a = SQLiteSharedRateLimitStore(database, key_path=key)
    try:
        gateway_a = WebGateway(
            clock=lambda: 100.0,
            rate_limits=limits,
            shared_rate_limit_store=store_a,
            rate_limit_clock=lambda: now[0],
        )
        gateway_a.open_session(client_key="192.0.2.44")
        gateway_a.open_session(client_key="192.0.2.44")
    finally:
        store_a.close()

    store_b = SQLiteSharedRateLimitStore(database, key_path=key)
    try:
        gateway_b = WebGateway(
            clock=lambda: 200.0,
            rate_limits=limits,
            shared_rate_limit_store=store_b,
            rate_limit_clock=lambda: now[0],
        )
        with pytest.raises(WebGatewayError) as limited:
            gateway_b.open_session(client_key="192.0.2.44")
        assert limited.value.status == 429
        assert limited.value.code == "session_rate_limited"
        assert limited.value.retry_after_seconds == 10

        now[0] += 10.0
        assert gateway_b.open_session(client_key="192.0.2.44")
    finally:
        store_b.close()


def test_shared_action_buckets_are_charged_atomically(tmp_path):
    database, key = _paths(tmp_path)
    now = [1_800_000_000.0]
    limits = WebRateLimits(
        window_seconds=10,
        session_creations_per_client=10,
        messages_per_session=2,
        heartbeats_per_session=10,
        room_attempts_per_client=1,
        protected_room_attempts_per_client=10,
        protected_room_attempts_global=10,
    )
    with SQLiteSharedRateLimitStore(database, key_path=key) as store:
        gateway = WebGateway(
            clock=lambda: 100.0,
            rate_limits=limits,
            shared_rate_limit_store=store,
            rate_limit_clock=lambda: now[0],
        )
        token = gateway.open_session(client_key="192.0.2.45")
        join = _message(
            "join_room",
            room_code="ABC234",
            display_name="Probe",
            role="player",
        )
        first = gateway.handle(token, join, client_key="192.0.2.45")
        assert first[0]["type"] == "request_error"

        with pytest.raises(WebGatewayError) as room_limited:
            gateway.handle(token, join, client_key="192.0.2.45")
        assert room_limited.value.code == "room_rate_limited"

        # The rejected multi-bucket charge did not consume the session's
        # message budget, so one ordinary request still fits.
        gateway.handle(token, _message("unknown"), client_key="192.0.2.45")
        with pytest.raises(WebGatewayError) as message_limited:
            gateway.handle(token, _message("unknown"), client_key="192.0.2.45")
        assert message_limited.value.code == "message_rate_limited"


def test_shared_limiter_failure_stops_before_controller_mutation():
    class FailingStore:
        def __init__(self):
            self.calls = 0

        def consume_many(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return RateLimitDecision(
                    allowed=True,
                    blocked_index=None,
                    retry_after_seconds=None,
                )
            raise SharedRateLimitError("private backend detail")

    class RecordingController:
        def __init__(self):
            self.handle_calls = 0

        def handle(self, *_args, **_kwargs):
            self.handle_calls += 1
            return ()

        def tick(self):
            return ()

        def disconnect(self, _connection_id):
            return ()

    controller = RecordingController()
    gateway = WebGateway(
        controller=controller,
        shared_rate_limit_store=FailingStore(),
        rate_limit_clock=lambda: 1_800_000_000.0,
    )
    token = gateway.open_session(client_key="192.0.2.46")

    with pytest.raises(WebGatewayError) as unavailable:
        gateway.handle(token, _message("unknown"), client_key="192.0.2.46")

    assert unavailable.value.status == 503
    assert unavailable.value.code == "rate_limit_unavailable"
    assert "private backend detail" not in str(unavailable.value)
    assert controller.handle_calls == 0


def test_shared_limit_stops_invitation_revocation_before_authority_mutation(
    tmp_path,
):
    database, key = _paths(tmp_path)
    limits = WebRateLimits(
        window_seconds=10,
        session_creations_per_client=10,
        messages_per_session=2,
    )
    with SQLiteSharedRateLimitStore(database, key_path=key) as store:
        gateway = WebGateway(
            rate_limits=limits,
            shared_rate_limit_store=store,
            rate_limit_clock=lambda: 1_800_000_000.0,
        )
        host = gateway.open_session(client_key="127.0.0.1")
        created = gateway.handle(
            host,
            _message(
                "create_room",
                display_name="Host",
                settings={
                    "player_count": 2,
                    "victory_target": 5,
                    "board_mode": "constrained",
                    "board_seed": 4242,
                },
            ),
            client_key="127.0.0.1",
        )
        room_code = next(
            event["room_code"]
            for event in created
            if event["type"] == "session_welcome"
        )
        invitation = gateway.issue_friend_invitation(
            host,
            role="spectator",
            client_key="127.0.0.1",
            protected_room_access_allowed=True,
        )

        with pytest.raises(WebGatewayError) as limited:
            gateway.revoke_friend_invitation(
                host,
                invitation_id=invitation["invitation_id"],
                client_key="127.0.0.1",
                protected_room_access_allowed=True,
            )
        assert limited.value.status == 429
        assert limited.value.code == "message_rate_limited"
        assert gateway.controller.inspect_friend_invitation(
            room_code,
            invitation["token"],
        ).invitation_id == invitation["invitation_id"]
