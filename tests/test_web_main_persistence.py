from pathlib import Path

import pytest

import web_main as web_main_module


class FakeServer:
    server_address = ("127.0.0.1", 8765)

    def __init__(self, events, *, failure=None):
        self.events = events
        self.failure = failure

    def serve_forever(self, *, poll_interval):
        self.events.append(("serve", poll_interval))
        if self.failure is not None:
            raise self.failure
        raise KeyboardInterrupt

    def server_close(self):
        self.events.append("server.close")


def test_state_key_requires_explicit_state_database(monkeypatch, capsys):
    monkeypatch.setattr(
        web_main_module,
        "SQLiteRoomAuthorityStore",
        lambda *_args, **_kwargs: pytest.fail("store must not be initialized"),
    )

    with pytest.raises(SystemExit) as stopped:
        web_main_module.main(["--state-key", "authority.key"])

    assert stopped.value.code == 2
    assert "--state-keyは--state-db" in capsys.readouterr().err


def test_rate_limit_key_requires_explicit_database(monkeypatch, capsys):
    monkeypatch.setattr(
        web_main_module,
        "SQLiteSharedRateLimitStore",
        lambda *_args, **_kwargs: pytest.fail("store must not be initialized"),
    )

    with pytest.raises(SystemExit) as stopped:
        web_main_module.main(["--rate-limit-key", "limits.key"])

    assert stopped.value.code == 2
    assert "--rate-limit-keyは--rate-limit-db" in capsys.readouterr().err


def test_replay_key_requires_explicit_database(monkeypatch, capsys):
    monkeypatch.setattr(
        web_main_module,
        "SQLiteNetworkReplayStore",
        lambda *_args, **_kwargs: pytest.fail("store must not be initialized"),
    )

    with pytest.raises(SystemExit) as stopped:
        web_main_module.main(["--replay-key", "replay.key"])

    assert stopped.value.code == 2
    assert "--replay-keyは--replay-db" in capsys.readouterr().err


def test_replay_database_requires_authority_database(monkeypatch, capsys):
    monkeypatch.setattr(
        web_main_module,
        "SQLiteNetworkReplayStore",
        lambda *_args, **_kwargs: pytest.fail("store must not be initialized"),
    )

    with pytest.raises(SystemExit) as stopped:
        web_main_module.main(["--replay-db", "replay.sqlite3"])

    assert stopped.value.code == 2
    assert "--replay-dbは--state-db" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--state-db", "shared.sqlite3", "--rate-limit-db", "shared.sqlite3"],
        [
            "--state-db",
            "storage/../shared.sqlite3",
            "--rate-limit-db",
            "shared.sqlite3",
        ],
        [
            "--state-db",
            "authority.sqlite3",
            "--state-key",
            "shared.key",
            "--rate-limit-db",
            "limits.sqlite3",
            "--rate-limit-key",
            "shared.key",
        ],
    ],
)
def test_authority_and_rate_limit_storage_paths_must_not_overlap(
    monkeypatch,
    capsys,
    arguments,
):
    monkeypatch.setattr(
        web_main_module,
        "SQLiteRoomAuthorityStore",
        lambda *_args, **_kwargs: pytest.fail("store must not be initialized"),
    )
    monkeypatch.setattr(
        web_main_module,
        "SQLiteSharedRateLimitStore",
        lambda *_args, **_kwargs: pytest.fail("store must not be initialized"),
    )

    with pytest.raises(SystemExit) as stopped:
        web_main_module.main(arguments)

    assert stopped.value.code == 2
    assert "それぞれ別のpath" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        [
            "--state-db",
            "shared.sqlite3",
            "--replay-db",
            "shared.sqlite3",
        ],
        [
            "--state-db",
            "authority.sqlite3",
            "--state-key",
            "replay.sqlite3.key",
            "--replay-db",
            "replay.sqlite3",
        ],
        [
            "--state-db",
            "authority.sqlite3",
            "--replay-db",
            "replay.sqlite3",
            "--replay-key",
            "limits.sqlite3",
            "--rate-limit-db",
            "limits.sqlite3",
        ],
    ],
)
def test_replay_storage_paths_must_not_overlap_any_persistent_store(
    monkeypatch,
    capsys,
    arguments,
):
    monkeypatch.setattr(
        web_main_module,
        "SQLiteNetworkReplayStore",
        lambda *_args, **_kwargs: pytest.fail("store must not be initialized"),
    )

    with pytest.raises(SystemExit) as stopped:
        web_main_module.main(arguments)

    assert stopped.value.code == 2
    assert "それぞれ別のpath" in capsys.readouterr().err


def test_default_cli_remains_in_memory_and_does_not_construct_store(monkeypatch):
    events = []
    recorded = {}
    monkeypatch.setattr(
        web_main_module,
        "SQLiteRoomAuthorityStore",
        lambda *_args, **_kwargs: pytest.fail("default must remain in-memory"),
    )
    monkeypatch.setattr(
        web_main_module,
        "SQLiteNetworkReplayStore",
        lambda *_args, **_kwargs: pytest.fail("default must remain in-memory"),
    )

    def fake_create_web_server(host, port, **kwargs):
        recorded.update(host=host, port=port, kwargs=kwargs)
        return FakeServer(events)

    monkeypatch.setattr(web_main_module, "create_web_server", fake_create_web_server)

    assert web_main_module.main(["--port", "8765"]) == 0
    assert recorded["kwargs"] == {
        "lan_mode": False,
        "allowed_hosts": [],
        "tls_certfile": None,
        "tls_keyfile": None,
    }
    assert events == [("serve", 0.2), "server.close"]


@pytest.mark.parametrize(
    ("arguments", "expected_key"),
    [
        (["--state-db", "authority.sqlite3"], None),
        (
            [
                "--state-db",
                "authority.sqlite3",
                "--state-key",
                "authority.key",
            ],
            "authority.key",
        ),
    ],
)
def test_opt_in_persistence_injects_one_store_into_controller_and_gateway(
    monkeypatch,
    capsys,
    arguments,
    expected_key,
):
    events = []
    recorded = {}

    class FakeStore:
        def __init__(self, database_path, *, key_path=None):
            recorded["database_path"] = database_path
            recorded["key_path"] = key_path
            events.append("store.open")

        def close(self):
            events.append("store.close")

    class FakeController:
        def __init__(self, *, state_store):
            recorded["controller_store"] = state_store
            events.append("controller.open")

    class FakeGateway:
        def __init__(self, *, controller):
            recorded["gateway_controller"] = controller
            events.append("gateway.open")

    def fake_create_web_server(host, port, **kwargs):
        recorded.update(host=host, port=port, server_kwargs=kwargs)
        events.append("server.open")
        return FakeServer(events)

    monkeypatch.setattr(web_main_module, "SQLiteRoomAuthorityStore", FakeStore)
    monkeypatch.setattr(web_main_module, "LanServerController", FakeController)
    monkeypatch.setattr(web_main_module, "WebGateway", FakeGateway)
    monkeypatch.setattr(web_main_module, "create_web_server", fake_create_web_server)

    assert web_main_module.main(arguments) == 0
    assert recorded["database_path"] == "authority.sqlite3"
    assert recorded["key_path"] == expected_key
    assert recorded["controller_store"] is not None
    assert recorded["gateway_controller"] is not None
    assert recorded["server_kwargs"]["gateway"] is not None
    assert events == [
        "store.open",
        "controller.open",
        "gateway.open",
        "server.open",
        ("serve", 0.2),
        "server.close",
        "store.close",
    ]
    assert "再起動復元を有効化" in capsys.readouterr().out


def test_opt_in_shared_rate_limit_injects_store_and_closes_it(
    monkeypatch,
    capsys,
):
    events = []
    recorded = {}

    class FakeRateLimitStore:
        def __init__(self, database_path, *, key_path=None):
            recorded["database_path"] = database_path
            recorded["key_path"] = key_path
            events.append("limits.open")

        def close(self):
            events.append("limits.close")

    class FakeController:
        def __init__(self):
            events.append("controller.open")

    class FakeGateway:
        def __init__(self, *, controller, shared_rate_limit_store):
            recorded["controller"] = controller
            recorded["rate_limit_store"] = shared_rate_limit_store
            events.append("gateway.open")

    def fake_create_web_server(host, port, **kwargs):
        recorded.update(host=host, port=port, server_kwargs=kwargs)
        events.append("server.open")
        return FakeServer(events)

    monkeypatch.setattr(
        web_main_module,
        "SQLiteSharedRateLimitStore",
        FakeRateLimitStore,
    )
    monkeypatch.setattr(web_main_module, "LanServerController", FakeController)
    monkeypatch.setattr(web_main_module, "WebGateway", FakeGateway)
    monkeypatch.setattr(web_main_module, "create_web_server", fake_create_web_server)

    assert web_main_module.main(
        [
            "--rate-limit-db",
            "limits.sqlite3",
            "--rate-limit-key",
            "limits.key",
        ]
    ) == 0
    assert recorded["database_path"] == "limits.sqlite3"
    assert recorded["key_path"] == "limits.key"
    assert recorded["rate_limit_store"] is not None
    assert recorded["server_kwargs"]["gateway"] is not None
    assert events == [
        "limits.open",
        "controller.open",
        "gateway.open",
        "server.open",
        ("serve", 0.2),
        "server.close",
        "limits.close",
    ]
    assert "共有回数制限を有効化" in capsys.readouterr().out


def test_room_authority_and_shared_limits_can_run_together(monkeypatch):
    events = []
    recorded = {}

    class FakeStateStore:
        def __init__(self, *_args, **_kwargs):
            events.append("state.open")

        def close(self):
            events.append("state.close")

    class FakeRateLimitStore:
        def __init__(self, *_args, **_kwargs):
            events.append("limits.open")

        def close(self):
            events.append("limits.close")

    class FakeController:
        def __init__(self, *, state_store):
            recorded["state_store"] = state_store

    class FakeGateway:
        def __init__(self, *, controller, shared_rate_limit_store):
            recorded["controller"] = controller
            recorded["rate_limit_store"] = shared_rate_limit_store

    monkeypatch.setattr(web_main_module, "SQLiteRoomAuthorityStore", FakeStateStore)
    monkeypatch.setattr(
        web_main_module,
        "SQLiteSharedRateLimitStore",
        FakeRateLimitStore,
    )
    monkeypatch.setattr(web_main_module, "LanServerController", FakeController)
    monkeypatch.setattr(web_main_module, "WebGateway", FakeGateway)
    monkeypatch.setattr(
        web_main_module,
        "create_web_server",
        lambda *_args, **_kwargs: FakeServer(events),
    )

    assert web_main_module.main(
        [
            "--state-db",
            "authority.sqlite3",
            "--rate-limit-db",
            "limits.sqlite3",
        ]
    ) == 0
    assert recorded["state_store"] is not None
    assert recorded["rate_limit_store"] is not None
    assert events == [
        "state.open",
        "limits.open",
        ("serve", 0.2),
        "server.close",
        "limits.close",
        "state.close",
    ]


def test_all_persistent_stores_are_injected_and_closed_in_reverse_order(
    monkeypatch,
    capsys,
):
    events = []
    recorded = {}

    class FakeStateStore:
        def __init__(self, database_path, *, key_path=None):
            recorded["state_args"] = (database_path, key_path)
            events.append("state.open")

        def close(self):
            events.append("state.close")

    class FakeReplayStore:
        def __init__(self, database_path, *, key_path=None):
            recorded["replay_args"] = (database_path, key_path)
            events.append("replay.open")

        def close(self):
            events.append("replay.close")

    class FakeRateLimitStore:
        def __init__(self, database_path, *, key_path=None):
            recorded["rate_args"] = (database_path, key_path)
            events.append("limits.open")

        def close(self):
            events.append("limits.close")

    class FakeController:
        def __init__(self, *, state_store, replay_store):
            recorded["controller_state_store"] = state_store
            recorded["controller_replay_store"] = replay_store
            events.append("controller.open")

    class FakeGateway:
        def __init__(self, *, controller, shared_rate_limit_store):
            recorded["gateway_controller"] = controller
            recorded["gateway_rate_store"] = shared_rate_limit_store
            events.append("gateway.open")

    def fake_create_web_server(host, port, **kwargs):
        recorded["server_gateway"] = kwargs["gateway"]
        events.append("server.open")
        return FakeServer(events)

    monkeypatch.setattr(web_main_module, "SQLiteRoomAuthorityStore", FakeStateStore)
    monkeypatch.setattr(web_main_module, "SQLiteNetworkReplayStore", FakeReplayStore)
    monkeypatch.setattr(
        web_main_module,
        "SQLiteSharedRateLimitStore",
        FakeRateLimitStore,
    )
    monkeypatch.setattr(web_main_module, "LanServerController", FakeController)
    monkeypatch.setattr(web_main_module, "WebGateway", FakeGateway)
    monkeypatch.setattr(web_main_module, "create_web_server", fake_create_web_server)

    assert web_main_module.main(
        [
            "--state-db",
            "authority.sqlite3",
            "--state-key",
            "authority.key",
            "--replay-db",
            "replay.sqlite3",
            "--replay-key",
            "replay.key",
            "--rate-limit-db",
            "limits.sqlite3",
            "--rate-limit-key",
            "limits.key",
        ]
    ) == 0
    assert recorded["state_args"] == ("authority.sqlite3", "authority.key")
    assert recorded["replay_args"] == ("replay.sqlite3", "replay.key")
    assert recorded["rate_args"] == ("limits.sqlite3", "limits.key")
    assert recorded["controller_state_store"] is not None
    assert recorded["controller_replay_store"] is not None
    assert recorded["gateway_rate_store"] is not None
    assert recorded["server_gateway"] is not None
    assert events == [
        "state.open",
        "replay.open",
        "limits.open",
        "controller.open",
        "gateway.open",
        "server.open",
        ("serve", 0.2),
        "server.close",
        "limits.close",
        "replay.close",
        "state.close",
    ]
    output = capsys.readouterr().out
    assert "対局状態の再起動復元を有効化" in output
    assert "ネットワークリプレイの再起動復元を有効化" in output
    assert "共有回数制限を有効化" in output


def test_replay_store_delegates_default_key_creation(monkeypatch):
    recorded = {}

    class FakeStateStore:
        def __init__(self, *_args, **_kwargs):
            pass

        def close(self):
            pass

    class FakeReplayStore:
        def __init__(self, database_path, *, key_path=None):
            recorded["database_path"] = database_path
            recorded["key_path"] = key_path

        def close(self):
            pass

    class FakeController:
        def __init__(self, *, state_store, replay_store):
            assert state_store is not None
            assert replay_store is not None

    class FakeGateway:
        def __init__(self, *, controller):
            assert controller is not None

    monkeypatch.setattr(web_main_module, "SQLiteRoomAuthorityStore", FakeStateStore)
    monkeypatch.setattr(web_main_module, "SQLiteNetworkReplayStore", FakeReplayStore)
    monkeypatch.setattr(web_main_module, "LanServerController", FakeController)
    monkeypatch.setattr(web_main_module, "WebGateway", FakeGateway)
    monkeypatch.setattr(
        web_main_module,
        "create_web_server",
        lambda *_args, **_kwargs: FakeServer([]),
    )

    assert web_main_module.main(
        [
            "--state-db",
            "authority.sqlite3",
            "--replay-db",
            "replay.sqlite3",
        ]
    ) == 0
    assert recorded == {
        "database_path": "replay.sqlite3",
        "key_path": None,
    }


def test_replay_initialization_failure_closes_authority_and_hides_detail(
    monkeypatch,
    capsys,
):
    events = []
    secret = "private-replay-database-and-key"

    class FakeStateStore:
        def __init__(self, *_args, **_kwargs):
            events.append("state.open")

        def close(self):
            events.append("state.close")

    def failing_replay_store(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(
        web_main_module,
        "SQLiteRoomAuthorityStore",
        FakeStateStore,
    )
    monkeypatch.setattr(
        web_main_module,
        "SQLiteNetworkReplayStore",
        failing_replay_store,
    )

    with pytest.raises(SystemExit) as stopped:
        web_main_module.main(
            [
                "--state-db",
                "authority.sqlite3",
                "--replay-db",
                "replay.sqlite3",
            ]
        )

    assert stopped.value.code == 2
    assert events == ["state.open", "state.close"]
    error = capsys.readouterr().err
    assert "永続状態を初期化できませんでした" in error
    assert secret not in error


def test_controller_failure_closes_replay_then_authority_and_hides_detail(
    monkeypatch,
    capsys,
):
    events = []
    secret = "private-controller-replay-detail"

    class FakeStateStore:
        def __init__(self, *_args, **_kwargs):
            events.append("state.open")

        def close(self):
            events.append("state.close")

    class FakeReplayStore:
        def __init__(self, *_args, **_kwargs):
            events.append("replay.open")

        def close(self):
            events.append("replay.close")

    class FailingController:
        def __init__(self, *, state_store, replay_store):
            assert state_store is not None
            assert replay_store is not None
            raise RuntimeError(secret)

    monkeypatch.setattr(web_main_module, "SQLiteRoomAuthorityStore", FakeStateStore)
    monkeypatch.setattr(web_main_module, "SQLiteNetworkReplayStore", FakeReplayStore)
    monkeypatch.setattr(web_main_module, "LanServerController", FailingController)

    with pytest.raises(SystemExit) as stopped:
        web_main_module.main(
            [
                "--state-db",
                "authority.sqlite3",
                "--replay-db",
                "replay.sqlite3",
            ]
        )

    assert stopped.value.code == 2
    assert events == [
        "state.open",
        "replay.open",
        "replay.close",
        "state.close",
    ]
    assert secret not in capsys.readouterr().err


def test_store_initialization_failure_uses_generic_error_without_secret(
    monkeypatch,
    capsys,
):
    secret = "do-not-print-this-key-material"
    server_called = False

    def failing_store(*_args, **_kwargs):
        raise RuntimeError(secret)

    def fake_create_web_server(*_args, **_kwargs):
        nonlocal server_called
        server_called = True
        return None

    monkeypatch.setattr(web_main_module, "SQLiteRoomAuthorityStore", failing_store)
    monkeypatch.setattr(web_main_module, "create_web_server", fake_create_web_server)

    with pytest.raises(SystemExit) as stopped:
        web_main_module.main(["--state-db", "authority.sqlite3"])

    assert stopped.value.code == 2
    error = capsys.readouterr().err
    assert "永続状態を初期化できませんでした" in error
    assert secret not in error
    assert server_called is False


def test_controller_initialization_failure_closes_store_and_hides_detail(
    monkeypatch,
    capsys,
):
    events = []
    secret = "private-database-path-and-key"

    class FakeStore:
        def __init__(self, *_args, **_kwargs):
            events.append("store.open")

        def close(self):
            events.append("store.close")

    class FailingController:
        def __init__(self, *, state_store):
            assert state_store is not None
            raise RuntimeError(secret)

    monkeypatch.setattr(web_main_module, "SQLiteRoomAuthorityStore", FakeStore)
    monkeypatch.setattr(web_main_module, "LanServerController", FailingController)

    with pytest.raises(SystemExit) as stopped:
        web_main_module.main(["--state-db", "authority.sqlite3"])

    assert stopped.value.code == 2
    assert events == ["store.open", "store.close"]
    assert secret not in capsys.readouterr().err


def test_rate_limit_initialization_failure_closes_authority_store_and_hides_detail(
    monkeypatch,
    capsys,
):
    events = []
    secret = "private-rate-limit-database-and-key"

    class FakeStateStore:
        def __init__(self, *_args, **_kwargs):
            events.append("state.open")

        def close(self):
            events.append("state.close")

    def failing_rate_limit_store(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(
        web_main_module,
        "SQLiteRoomAuthorityStore",
        FakeStateStore,
    )
    monkeypatch.setattr(
        web_main_module,
        "SQLiteSharedRateLimitStore",
        failing_rate_limit_store,
    )

    with pytest.raises(SystemExit) as stopped:
        web_main_module.main(
            [
                "--state-db",
                "authority.sqlite3",
                "--rate-limit-db",
                "limits.sqlite3",
            ]
        )

    assert stopped.value.code == 2
    assert events == ["state.open", "state.close"]
    assert secret not in capsys.readouterr().err


def test_server_creation_failure_closes_initialized_store(monkeypatch, capsys):
    events = []

    class FakeStore:
        def __init__(self, *_args, **_kwargs):
            events.append("store.open")

        def close(self):
            events.append("store.close")

    class FakeController:
        def __init__(self, *, state_store):
            self.state_store = state_store

    class FakeGateway:
        def __init__(self, *, controller):
            self.controller = controller

    monkeypatch.setattr(web_main_module, "SQLiteRoomAuthorityStore", FakeStore)
    monkeypatch.setattr(web_main_module, "LanServerController", FakeController)
    monkeypatch.setattr(web_main_module, "WebGateway", FakeGateway)
    monkeypatch.setattr(
        web_main_module,
        "create_web_server",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad bind")),
    )

    with pytest.raises(SystemExit) as stopped:
        web_main_module.main(["--state-db", "authority.sqlite3"])

    assert stopped.value.code == 2
    assert events == ["store.open", "store.close"]
    assert "bad bind" in capsys.readouterr().err


def test_unexpected_server_failure_still_closes_server_then_store(monkeypatch):
    events = []

    class FakeStore:
        def __init__(self, *_args, **_kwargs):
            events.append("store.open")

        def close(self):
            events.append("store.close")

    class FakeController:
        def __init__(self, *, state_store):
            self.state_store = state_store

    class FakeGateway:
        def __init__(self, *, controller):
            self.controller = controller

    monkeypatch.setattr(web_main_module, "SQLiteRoomAuthorityStore", FakeStore)
    monkeypatch.setattr(web_main_module, "LanServerController", FakeController)
    monkeypatch.setattr(web_main_module, "WebGateway", FakeGateway)
    monkeypatch.setattr(
        web_main_module,
        "create_web_server",
        lambda *_args, **_kwargs: FakeServer(
            events,
            failure=RuntimeError("server failed"),
        ),
    )

    with pytest.raises(RuntimeError, match="server failed"):
        web_main_module.main(["--state-db", "authority.sqlite3"])
    assert events[-2:] == ["server.close", "store.close"]


def test_store_close_failure_is_reported_without_exception_detail(
    monkeypatch,
    capsys,
    tmp_path,
):
    secret = str(Path(tmp_path) / "private.key")

    class FakeStore:
        def __init__(self, *_args, **_kwargs):
            pass

        def close(self):
            raise RuntimeError(secret)

    class FakeController:
        def __init__(self, *, state_store):
            self.state_store = state_store

    class FakeGateway:
        def __init__(self, *, controller):
            self.controller = controller

    monkeypatch.setattr(web_main_module, "SQLiteRoomAuthorityStore", FakeStore)
    monkeypatch.setattr(web_main_module, "LanServerController", FakeController)
    monkeypatch.setattr(web_main_module, "WebGateway", FakeGateway)
    monkeypatch.setattr(
        web_main_module,
        "create_web_server",
        lambda *_args, **_kwargs: FakeServer([]),
    )

    assert web_main_module.main(["--state-db", "authority.sqlite3"]) == 0
    error = capsys.readouterr().err
    assert "永続状態を安全に終了できませんでした" in error
    assert secret not in error


@pytest.mark.parametrize("failure_stage", ["create", "run", "close"])
def test_replay_and_authority_close_across_server_failures(
    monkeypatch,
    failure_stage,
):
    events = []

    class FakeStateStore:
        def __init__(self, *_args, **_kwargs):
            events.append("state.open")

        def close(self):
            events.append("state.close")

    class FakeReplayStore:
        def __init__(self, *_args, **_kwargs):
            events.append("replay.open")

        def close(self):
            events.append("replay.close")

    class FakeController:
        def __init__(self, *, state_store, replay_store):
            assert state_store is not None
            assert replay_store is not None
            events.append("controller.open")

    class FakeGateway:
        def __init__(self, *, controller):
            assert controller is not None
            events.append("gateway.open")

    class FailingServer:
        server_address = ("127.0.0.1", 8765)

        def serve_forever(self, *, poll_interval):
            events.append(("serve", poll_interval))
            if failure_stage == "run":
                raise RuntimeError("run failed")
            raise KeyboardInterrupt

        def server_close(self):
            events.append("server.close")
            if failure_stage == "close":
                raise RuntimeError("close failed")

    def fake_create_web_server(*_args, **_kwargs):
        events.append("server.open")
        if failure_stage == "create":
            raise RuntimeError("create failed")
        return FailingServer()

    monkeypatch.setattr(web_main_module, "SQLiteRoomAuthorityStore", FakeStateStore)
    monkeypatch.setattr(web_main_module, "SQLiteNetworkReplayStore", FakeReplayStore)
    monkeypatch.setattr(web_main_module, "LanServerController", FakeController)
    monkeypatch.setattr(web_main_module, "WebGateway", FakeGateway)
    monkeypatch.setattr(web_main_module, "create_web_server", fake_create_web_server)

    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        web_main_module.main(
            [
                "--state-db",
                "authority.sqlite3",
                "--replay-db",
                "replay.sqlite3",
            ]
        )

    assert events[-2:] == ["replay.close", "state.close"]


def test_replay_close_failure_is_generic_and_does_not_skip_authority_close(
    monkeypatch,
    capsys,
    tmp_path,
):
    events = []
    secret = str(Path(tmp_path) / "private-replay.key")

    class FakeStateStore:
        def __init__(self, *_args, **_kwargs):
            events.append("state.open")

        def close(self):
            events.append("state.close")

    class FakeReplayStore:
        def __init__(self, *_args, **_kwargs):
            events.append("replay.open")

        def close(self):
            events.append("replay.close")
            raise RuntimeError(secret)

    class FakeController:
        def __init__(self, *, state_store, replay_store):
            assert state_store is not None
            assert replay_store is not None

    class FakeGateway:
        def __init__(self, *, controller):
            assert controller is not None

    monkeypatch.setattr(web_main_module, "SQLiteRoomAuthorityStore", FakeStateStore)
    monkeypatch.setattr(web_main_module, "SQLiteNetworkReplayStore", FakeReplayStore)
    monkeypatch.setattr(web_main_module, "LanServerController", FakeController)
    monkeypatch.setattr(web_main_module, "WebGateway", FakeGateway)
    monkeypatch.setattr(
        web_main_module,
        "create_web_server",
        lambda *_args, **_kwargs: FakeServer(events),
    )

    assert web_main_module.main(
        [
            "--state-db",
            "authority.sqlite3",
            "--replay-db",
            "replay.sqlite3",
        ]
    ) == 0
    assert events[-3:] == ["server.close", "replay.close", "state.close"]
    error = capsys.readouterr().err
    assert "ネットワークリプレイを安全に終了できませんでした" in error
    assert secret not in error
