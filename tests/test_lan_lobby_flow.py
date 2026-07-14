from collections import deque
from dataclasses import asdict, FrozenInstanceError
import threading
import time

import pytest

from game import lan_lobby_display as display
from game.building import Building
from game.game import CatanGame
from game.lan_lobby_flow import (
    ACTION_BACK,
    ACTION_CLOSE,
    ACTION_COPY_ROOM_CODE,
    ACTION_CREATE_ROOM,
    ACTION_INPUT_ADDRESS,
    ACTION_INPUT_NAME,
    ACTION_INPUT_ROOM_CODE,
    ACTION_JOIN_ROOM,
    ACTION_LEAVE_ROOM,
    ACTION_MODE_CREATE,
    ACTION_MODE_JOIN,
    ACTION_MODE_SPECTATE,
    ACTION_RECONNECT,
    ACTION_SPECTATOR_TOGGLE,
    ACTION_START_MATCH,
    ACTION_TOGGLE_READY,
    LanEndpointError,
    LanLobbyFlow,
    parse_lan_endpoint,
)
from game.network_protocol import build_state_snapshot
from game.network_view import NetworkGameView


FLOW_ACTION_NAMES = (
    "ACTION_BACK",
    "ACTION_CLOSE",
    "ACTION_COPY_ROOM_CODE",
    "ACTION_CREATE_ROOM",
    "ACTION_INPUT_ADDRESS",
    "ACTION_INPUT_NAME",
    "ACTION_INPUT_ROOM_CODE",
    "ACTION_JOIN_ROOM",
    "ACTION_LEAVE_ROOM",
    "ACTION_MODE_CREATE",
    "ACTION_MODE_JOIN",
    "ACTION_MODE_SPECTATE",
    "ACTION_RECONNECT",
    "ACTION_SPECTATOR_TOGGLE",
    "ACTION_START_MATCH",
    "ACTION_TOGGLE_READY",
)


class FakeSession:
    def __init__(self, *, events=(), order=None, connect_gate=None, connect_error=None):
        self.events = deque(events)
        self.order = order
        self.connect_gate = connect_gate
        self.connect_error = connect_error
        self.connect_calls = []
        self.create_calls = []
        self.join_calls = []
        self.reconnect_calls = []
        self.ready_calls = []
        self.start_calls = 0
        self.leave_calls = 0
        self.game_command_calls = []
        self.pending_commands = {}
        self.next_sequence = 0
        self.poll_calls = 0
        self.close_calls = 0
        self.room_code = None
        self.role = None
        self.seat_index = None
        self.reconnect_token = None
        self.lobby = None
        self.game_snapshot = None
        self.last_error = None
        self.is_connected = False
        self.is_synchronized = True

    def connect(self, host, port, *, timeout=5.0):
        self.connect_calls.append((host, port, timeout))
        if self.connect_gate is not None:
            self.connect_gate.wait(timeout=2)
        if self.connect_error is not None:
            raise self.connect_error
        self.is_connected = True

    def create_room(self, display_name, **settings):
        self.create_calls.append((display_name, settings))

    def join_room(self, room_code, display_name, *, spectator=False):
        self.join_calls.append((room_code, display_name, spectator))

    def reconnect_room(self, room_code=None, reconnect_token=None):
        self.reconnect_calls.append((room_code, reconnect_token))

    def set_ready(self, ready=True):
        self.ready_calls.append(ready)

    def start_game(self):
        self.start_calls += 1

    def leave_room(self):
        self.leave_calls += 1

    def send_game_command(self, command, args=None):
        sequence = self.next_sequence
        self.next_sequence += 1
        copied_args = dict(args or {})
        self.game_command_calls.append((command, copied_args))
        self.pending_commands[sequence] = {
            "command": command,
            "args": copied_args,
        }
        return sequence

    def poll(self, *, limit=100):
        self.poll_calls += 1
        if self.order is not None:
            self.order.append("client.poll")
        result = []
        while self.events and len(result) < limit:
            result.append(self.events.popleft())
        return result

    def close(self):
        self.close_calls += 1
        self.is_connected = False


class FakeRuntime:
    def __init__(self, host, port, *, bound, order=None):
        self.requested = (host, port)
        self.bound = bound
        self.order = order
        self.start_calls = 0
        self.pump_calls = 0
        self.stop_calls = 0

    @property
    def address(self):
        return self.bound

    def start(self):
        self.start_calls += 1
        return self.bound

    def pump(self, *, event_limit=200):
        self.pump_calls += 1
        if self.order is not None:
            self.order.append("server.pump")
        return 0

    def stop(self):
        self.stop_calls += 1


def message(message_type, **payload):
    return {
        "kind": "message",
        "message": {"type": message_type, "protocol_version": 1, **payload},
    }


def lobby_snapshot(*, ready=False, can_start=False):
    return {
        "room_code": "ABC234",
        "revision": 2,
        "phase": "waiting",
        "settings": {
            "player_count": 2,
            "victory_target": 8,
            "board_mode": "constrained",
            "board_seed": 77,
        },
        "can_start": can_start,
        "members": [
            {
                "display_name": "Host",
                "role": "host",
                "seat": 1,
                "connected": True,
                "ready": ready,
            }
        ],
        "spectators": 0,
    }


def welcome(*, role="host", seat_index=0, token="secret-token"):
    return message(
        "session_welcome",
        room_code="ABC234",
        role=role,
        seat_index=seat_index,
        reconnect_token=token,
        lobby_revision=1,
        next_sequence=0,
    )


def wait_for(flow, predicate, *, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        flow.update()
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("timed out waiting for LAN flow")


def settings():
    return {
        "player_count": 2,
        "victory_target": 8,
        "board_mode": "constrained",
        "board_seed": 77,
    }


def test_action_values_remain_compatible_without_importing_display_in_flow():
    import game.lan_lobby_flow as flow_module

    for name in FLOW_ACTION_NAMES:
        assert getattr(flow_module, name) == getattr(display, name)
    assert "pygame" not in flow_module.__dict__
    assert "lan_lobby_display" not in flow_module.__dict__


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("127.0.0.1:47624", ("127.0.0.1", 47624)),
        (" 0.0.0.0:1 ", ("0.0.0.0", 1)),
        ("LOCALHOST:65535", ("localhost", 65535)),
        ("catan-host.local:8080", ("catan-host.local", 8080)),
    ],
)
def test_parse_lan_endpoint_accepts_only_explicit_ipv4_or_hostname(raw, expected):
    assert parse_lan_endpoint(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "localhost",
        "localhost:",
        ":47624",
        "host name:47624",
        "-host:47624",
        "host-:47624",
        "host..local:47624",
        "127.0.0.1:0",
        "127.0.0.1:65536",
        "127.0.0.1:+80",
        "127.0.0.1:８０",
        "999.999.999.999:47624",
        "1234:47624",
        "[::1]:47624",
        "::1:47624",
    ],
)
def test_parse_lan_endpoint_rejects_ambiguous_or_unsafe_values(raw):
    with pytest.raises(LanEndpointError):
        parse_lan_endpoint(raw)


def test_create_host_is_async_advertises_lan_address_and_pumps_before_poll():
    order = []
    session = FakeSession(
        order=order,
        events=(
            welcome(),
            message("lobby_snapshot", lobby=lobby_snapshot()),
        ),
    )
    runtimes = []

    def runtime_factory(host, port):
        runtime = FakeRuntime(
            host,
            port,
            bound=("0.0.0.0", 51234),
            order=order,
        )
        runtimes.append(runtime)
        return runtime

    copied = []
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        runtime_factory=runtime_factory,
        room_settings_provider=settings,
        clipboard_callback=copied.append,
        advertised_host_resolver=lambda: "192.168.1.44",
        default_name="Host",
        default_address="0.0.0.0:47624",
    )
    flow.open()
    assert flow.handle_action(ACTION_MODE_CREATE)
    assert flow.handle_action(ACTION_CREATE_ROOM)
    assert flow.connecting

    wait_for(flow, lambda: flow.mode == "connected")

    runtime = runtimes[0]
    assert runtime.requested == ("0.0.0.0", 47624)
    assert session.connect_calls == [("127.0.0.1", 51234, 5.0)]
    assert session.create_calls == [("Host", settings())]
    assert flow.display_state.address == "192.168.1.44:51234"
    assert flow.display_state.room_code == "ABC234"
    assert flow.display_state.local_role == "host"
    assert flow.display_state.local_seat == 1
    assert order.index("server.pump") < order.index("client.poll")

    assert flow.handle_action(ACTION_COPY_ROOM_CODE)
    assert copied == ["ABC234"]
    assert flow.handle_action(ACTION_TOGGLE_READY)
    assert session.ready_calls == [True]
    assert flow.handle_action(ACTION_START_MATCH)
    assert session.start_calls == 1

    flow.leave()
    flow.leave()
    assert session.close_calls == 1
    assert runtime.stop_calls == 1


def test_advertised_host_resolver_failure_falls_back_to_loopback():
    session = FakeSession(events=(welcome(),))
    runtime = FakeRuntime("0.0.0.0", 47624, bound=("0.0.0.0", 50001))

    def broken_resolver():
        raise OSError("no route")

    flow = LanLobbyFlow(
        session_factory=lambda: session,
        runtime_factory=lambda _host, _port: runtime,
        room_settings_provider=settings,
        advertised_host_resolver=broken_resolver,
        default_name="Host",
        default_address="0.0.0.0:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_CREATE)
    flow.handle_action(ACTION_CREATE_ROOM)
    wait_for(flow, lambda: flow.mode == "connected")

    assert flow.display_state.address == "127.0.0.1:50001"


def test_join_and_spectate_use_remote_endpoint_without_creating_runtime():
    session = FakeSession(
        events=(
            welcome(role="spectator", seat_index=None),
            message("lobby_snapshot", lobby=lobby_snapshot()),
        )
    )
    runtime_calls = []
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        runtime_factory=lambda *args: runtime_calls.append(args),
        room_settings_provider=settings,
        default_name="Viewer",
        default_address="192.168.1.20:47624",
    )
    flow.open()
    assert flow.handle_action(ACTION_MODE_SPECTATE)
    flow.set_input(ACTION_INPUT_ROOM_CODE, "abc234")
    assert flow.display_state.spectator is True
    assert flow.handle_action(ACTION_JOIN_ROOM)

    wait_for(flow, lambda: flow.mode == "connected")

    assert runtime_calls == []
    assert session.connect_calls == [("192.168.1.20", 47624, 5.0)]
    assert session.join_calls == [("ABC234", "Viewer", True)]
    assert flow.display_state.local_role == "spectator"
    assert flow.display_state.local_seat is None
    assert flow.handle_action(ACTION_TOGGLE_READY) is False
    assert flow.handle_action(ACTION_START_MATCH) is False


def test_connect_worker_is_daemon_and_never_blocks_action_thread():
    gate = threading.Event()
    session = FakeSession(connect_gate=gate)
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        room_settings_provider=settings,
        default_name="Guest",
        default_address="127.0.0.1:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_JOIN)
    flow.set_input(ACTION_INPUT_ROOM_CODE, "ABC234")

    started = time.monotonic()
    assert flow.handle_action(ACTION_JOIN_ROOM)
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    assert flow.connecting is True
    worker = next(
        thread for thread in threading.enumerate() if thread.name == "catan-lan-join"
    )
    assert worker.daemon is True

    assert flow.handle_action(ACTION_BACK)
    assert flow.mode == "home"
    gate.set()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and session.close_calls == 0:
        time.sleep(0.001)
    assert session.close_calls == 1


def test_disconnect_and_reconnect_preserve_endpoint_code_and_token():
    first = FakeSession(events=(welcome(role="player", seat_index=1),))
    second = FakeSession(events=(welcome(role="player", seat_index=1, token=None),))
    sessions = deque((first, second))
    flow = LanLobbyFlow(
        session_factory=sessions.popleft,
        room_settings_provider=settings,
        default_name="Guest",
        default_address="192.168.1.20:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_JOIN)
    flow.set_input(ACTION_INPUT_ROOM_CODE, "ABC234")
    flow.handle_action(ACTION_JOIN_ROOM)
    wait_for(flow, lambda: flow.mode == "connected")

    authority = CatanGame(board_seed=9090, ai_player_count=0, headless=True)
    authority.configure_players(2, reset_logs=False)
    snapshot = build_state_snapshot(
        authority,
        viewer_player_index=1,
        revision=3,
    )
    snapshot["command_options"] = [
        {"command": "roll_dice", "args": {}},
    ]
    first.game_snapshot = snapshot
    flow.update()
    assert [
        option["command"] for option in flow.latest_command_options
    ] == ["roll_dice"]

    first.events.append({"kind": "disconnected", "detail": "cable removed"})
    flow.update()
    assert flow.mode == "disconnected"
    assert flow.display_state.room_code == "ABC234"
    assert flow.latest_command_options == ()

    assert flow.handle_action(ACTION_RECONNECT)
    wait_for(flow, lambda: flow.mode == "connected")

    assert second.connect_calls == [("192.168.1.20", 47624, 5.0)]
    assert second.reconnect_calls == [("ABC234", "secret-token")]
    assert flow.display_state.local_seat == 2


def test_reconnect_does_not_succeed_without_a_fresh_authoritative_welcome():
    now = [20.0]
    first = FakeSession(events=(welcome(role="player", seat_index=1),))
    second = FakeSession()
    sessions = deque((first, second))
    flow = LanLobbyFlow(
        session_factory=sessions.popleft,
        room_settings_provider=settings,
        clock=lambda: now[0],
        connect_timeout=0.5,
        default_name="Guest",
        default_address="192.168.1.20:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_JOIN)
    flow.set_input(ACTION_INPUT_ROOM_CODE, "ABC234")
    flow.handle_action(ACTION_JOIN_ROOM)
    wait_for(flow, lambda: flow.mode == "connected")
    first.events.append({"kind": "disconnected", "detail": "lost"})
    flow.update()

    flow.handle_action(ACTION_RECONNECT)
    wait_for(flow, lambda: second.poll_calls > 0)
    assert flow.mode == "disconnected"
    assert flow.connecting is True

    now[0] = 20.51
    flow.update()
    assert flow.mode == "disconnected"
    assert flow.connecting is False
    assert second.close_calls == 1
    assert second.reconnect_calls == [("ABC234", "secret-token")]


def test_handshake_error_returns_to_form_and_cleans_partial_resources():
    session = FakeSession(
        events=(
            message(
                "request_error",
                code="room_not_found",
                message="部屋が見つかりません。",
            ),
        )
    )
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        room_settings_provider=settings,
        default_name="Guest",
        default_address="127.0.0.1:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_JOIN)
    flow.set_input(ACTION_INPUT_ROOM_CODE, "ABC234")
    flow.handle_action(ACTION_JOIN_ROOM)
    wait_for(flow, lambda: not flow.connecting)

    assert flow.mode == "join"
    assert "見つかりません" in flow.display_state.error
    assert session.close_calls == 1


def test_create_welcome_error_stops_host_runtime_exactly_once():
    session = FakeSession(
        events=(
            message(
                "request_error",
                code="invalid_request",
                message="部屋設定が不正です。",
            ),
        )
    )
    runtime = FakeRuntime("0.0.0.0", 47624, bound=("0.0.0.0", 50010))
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        runtime_factory=lambda _host, _port: runtime,
        room_settings_provider=settings,
        advertised_host_resolver=lambda: "192.168.1.3",
        default_name="Host",
        default_address="0.0.0.0:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_CREATE)
    flow.handle_action(ACTION_CREATE_ROOM)
    wait_for(flow, lambda: not flow.connecting)

    assert flow.mode == "create"
    assert flow.display_state.address == "0.0.0.0:47624"
    assert session.close_calls == 1
    assert runtime.stop_calls == 1
    flow.close()
    assert runtime.stop_calls == 1


def test_welcome_deadline_closes_session_and_host_then_returns_to_create_form():
    now = [10.0]
    session = FakeSession()
    runtime = FakeRuntime("0.0.0.0", 47624, bound=("0.0.0.0", 50011))
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        runtime_factory=lambda _host, _port: runtime,
        room_settings_provider=settings,
        advertised_host_resolver=lambda: "192.168.1.4",
        clock=lambda: now[0],
        connect_timeout=0.5,
        default_name="Host",
        default_address="0.0.0.0:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_CREATE)
    flow.handle_action(ACTION_CREATE_ROOM)
    wait_for(flow, lambda: runtime.pump_calls > 0)
    assert flow.connecting is True

    now[0] = 10.51
    flow.update()

    assert flow.connecting is False
    assert flow.mode == "create"
    assert "参加確認" in flow.display_state.error
    assert session.close_calls == 1
    assert runtime.stop_calls == 1


def test_display_state_is_frozen_and_snapshot_copy_is_safe_for_display_bridge():
    session = FakeSession(
        events=(
            welcome(),
            message("lobby_snapshot", lobby=lobby_snapshot()),
        )
    )
    runtime = FakeRuntime("0.0.0.0", 47624, bound=("0.0.0.0", 47624))
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        runtime_factory=lambda _host, _port: runtime,
        room_settings_provider=settings,
        advertised_host_resolver=lambda: "192.168.1.2",
        default_name="Host",
        default_address="0.0.0.0:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_CREATE)
    flow.handle_action(ACTION_CREATE_ROOM)
    wait_for(flow, lambda: flow.mode == "connected")
    state = flow.display_state

    with pytest.raises(FrozenInstanceError):
        state.mode = "home"
    state.lobby_snapshot["room_code"] = "ZZZ999"
    state.lobby_snapshot["settings"]["board_seed"] = 1
    assert flow.display_state.lobby_snapshot["room_code"] == "ABC234"
    assert flow.display_state.lobby_snapshot["settings"]["board_seed"] == 77
    display_kwargs = asdict(flow.display_state)
    assert display_kwargs["lobby_snapshot"]["room_code"] == "ABC234"
    assert display.LanLobbyDisplayState(**display_kwargs).local_seat == 1


def test_state_snapshot_becomes_safe_view_and_preserves_raw_command_options():
    authority = CatanGame(board_seed=9090, ai_player_count=0, headless=True)
    authority.configure_players(2, reset_logs=False)
    authority.phase = "main"
    authority.turn_order = list(authority.players)
    authority.board.nodes[0].building = Building(authority.players[0])
    snapshot = build_state_snapshot(authority, viewer_player_index=0, revision=3)
    deeply_nested = "leaf"
    for _ in range(32):
        deeply_nested = [deeply_nested]
    snapshot["command_options"] = [
        {"command": "too_deep", "args": {"nested": deeply_nested}},
        {"command": "roll_dice", "args": {}},
        {
            "command": "future_action",
            "args": {"target": "node-1"},
            "future_field": [1, 2],
        },
    ]
    session = FakeSession(
        events=(
            welcome(role="player", seat_index=0),
            {"kind": "message", "message": snapshot},
        )
    )
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        room_settings_provider=settings,
        default_name="Guest",
        default_address="127.0.0.1:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_JOIN)
    flow.set_input(ACTION_INPUT_ROOM_CODE, "ABC234")
    flow.handle_action(ACTION_JOIN_ROOM)
    wait_for(flow, lambda: flow.match_active)

    assert isinstance(flow.latest_game_view, NetworkGameView)
    assert flow.latest_game_view.revision == 3
    assert [option["command"] for option in flow.latest_command_options] == [
        "roll_dice",
        "future_action",
    ]
    assert flow.latest_command_options[1]["future_field"] == (1, 2)
    with pytest.raises(TypeError):
        flow.latest_command_options[0]["command"] = "tampered"


def test_deep_or_cyclic_lobby_snapshot_is_rejected_without_crashing_flow():
    deeply_nested = {"leaf": True}
    for _ in range(32):
        deeply_nested = {"nested": deeply_nested}
    session = FakeSession(
        events=(
            welcome(role="player", seat_index=0),
            message("lobby_snapshot", lobby=deeply_nested),
        )
    )
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        room_settings_provider=settings,
        default_name="Guest",
        default_address="127.0.0.1:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_JOIN)
    flow.set_input(ACTION_INPUT_ROOM_CODE, "ABC234")
    flow.handle_action(ACTION_JOIN_ROOM)
    wait_for(flow, lambda: flow.mode == "connected")

    assert "安全に" in flow.display_state.error
    assert flow.display_state.lobby_snapshot is None


@pytest.mark.parametrize("members", [None, 1, "players", {"seat": 1}])
def test_malformed_lobby_member_collection_is_rejected(members):
    malformed = lobby_snapshot()
    malformed["members"] = members
    session = FakeSession(
        events=(
            welcome(role="player", seat_index=0),
            message("lobby_snapshot", lobby=malformed),
        )
    )
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        room_settings_provider=settings,
        default_name="Guest",
        default_address="127.0.0.1:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_JOIN)
    flow.set_input(ACTION_INPUT_ROOM_CODE, "ABC234")
    flow.handle_action(ACTION_JOIN_ROOM)
    wait_for(flow, lambda: flow.mode == "connected")

    assert "安全に" in flow.display_state.error
    assert flow.display_state.lobby_snapshot is None


def test_connected_match_command_is_sent_once_until_pending_command_clears():
    authority = CatanGame(board_seed=9191, ai_player_count=0, headless=True)
    authority.configure_players(2, reset_logs=False)
    authority.phase = "main"
    authority.turn_order = list(authority.players)
    snapshot = build_state_snapshot(authority, viewer_player_index=0, revision=4)
    snapshot["command_options"] = [
        {"command": "roll_dice", "args": {}},
    ]
    session = FakeSession(
        events=(
            welcome(role="player", seat_index=0),
            {"kind": "message", "message": snapshot},
        )
    )
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        room_settings_provider=settings,
        default_name="Guest",
        default_address="127.0.0.1:47624",
    )

    assert flow.is_connected is False
    assert flow.command_pending is False
    assert flow.send_game_command("roll_dice", {}) is False

    flow.open()
    flow.handle_action(ACTION_MODE_JOIN)
    flow.set_input(ACTION_INPUT_ROOM_CODE, "ABC234")
    flow.handle_action(ACTION_JOIN_ROOM)
    wait_for(flow, lambda: flow.match_active)

    assert flow.is_connected is True
    assert flow.command_pending is False
    assert flow.send_game_command("roll_dice", {}) is True
    assert session.game_command_calls == [("roll_dice", {})]
    assert flow.command_pending is True

    # The UI cannot enqueue a second command while authority acknowledgement
    # for the first sequence is outstanding.
    assert flow.send_game_command("end_turn", None) is False
    assert session.game_command_calls == [("roll_dice", {})]

    session.pending_commands.clear()
    assert flow.command_pending is False
    assert flow.send_game_command("end_turn") is True
    assert session.game_command_calls == [
        ("roll_dice", {}),
        ("end_turn", {}),
    ]


def test_flow_locks_commands_between_result_and_matching_snapshot():
    authority = CatanGame(board_seed=9191, ai_player_count=0, headless=True)
    authority.configure_players(2, reset_logs=False)
    authority.phase = "main"
    authority.turn_order = list(authority.players)
    snapshot = build_state_snapshot(authority, viewer_player_index=0, revision=4)
    snapshot["command_options"] = [{"command": "roll_dice", "args": {}}]
    session = FakeSession(
        events=(
            welcome(role="player", seat_index=0),
            {"kind": "message", "message": snapshot},
        )
    )
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        room_settings_provider=settings,
        default_name="Guest",
        default_address="127.0.0.1:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_JOIN)
    flow.set_input(ACTION_INPUT_ROOM_CODE, "ABC234")
    flow.handle_action(ACTION_JOIN_ROOM)
    wait_for(flow, lambda: flow.match_active)

    session.is_synchronized = False
    assert flow.command_pending is True
    assert flow.send_game_command("roll_dice", {}) is False
    session.is_synchronized = True
    assert flow.command_pending is False


def test_newer_malformed_snapshot_clears_old_view_and_command_options():
    authority = CatanGame(board_seed=9191, ai_player_count=0, headless=True)
    authority.configure_players(2, reset_logs=False)
    authority.phase = "main"
    authority.turn_order = list(authority.players)
    valid = build_state_snapshot(authority, viewer_player_index=0, revision=4)
    valid["command_options"] = [{"command": "roll_dice", "args": {}}]
    malformed = {
        "type": "state_snapshot",
        "protocol_version": 1,
        "revision": 5,
        "command_options": [{"command": "end_turn", "args": {}}],
    }
    session = FakeSession(
        events=(
            welcome(role="player", seat_index=0),
            {"kind": "message", "message": valid},
            {"kind": "message", "message": malformed},
        )
    )
    session.game_revision = 5
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        room_settings_provider=settings,
        default_name="Guest",
        default_address="127.0.0.1:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_JOIN)
    flow.set_input(ACTION_INPUT_ROOM_CODE, "ABC234")
    flow.handle_action(ACTION_JOIN_ROOM)
    wait_for(flow, lambda: "安全に表示" in flow.display_state.error)

    assert flow.latest_game_view is None
    assert flow.latest_command_options == ()
    assert flow.send_game_command("end_turn", {}) is False
    assert session.game_command_calls == []


def test_explicit_leave_notifies_authority_and_host_promotion_updates_role():
    promoted = lobby_snapshot()
    promoted["members"] = [
        {
            "display_name": "Guest",
            "role": "host",
            "seat": 2,
            "connected": True,
            "ready": False,
        }
    ]
    session = FakeSession(
        events=(
            welcome(role="player", seat_index=1),
            message("lobby_snapshot", lobby=promoted),
        )
    )
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        room_settings_provider=settings,
        default_name="Guest",
        default_address="127.0.0.1:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_JOIN)
    flow.set_input(ACTION_INPUT_ROOM_CODE, "ABC234")
    flow.handle_action(ACTION_JOIN_ROOM)
    wait_for(flow, lambda: flow.mode == "connected")

    assert flow.display_state.local_role == "host"
    assert flow.handle_action(ACTION_LEAVE_ROOM)
    assert session.leave_calls == 1
    assert session.close_calls == 1
    assert flow.mode == "home"


def test_room_closed_event_ends_match_and_keeps_public_reason():
    session = FakeSession(
        events=(
            welcome(role="player", seat_index=0),
            message("lobby_snapshot", lobby=lobby_snapshot()),
            message(
                "room_closed",
                code="player_reconnect_expired",
                message="再接続期限が切れました。",
            ),
        )
    )
    flow = LanLobbyFlow(
        session_factory=lambda: session,
        room_settings_provider=settings,
        default_name="Guest",
        default_address="127.0.0.1:47624",
    )
    flow.open()
    flow.handle_action(ACTION_MODE_JOIN)
    flow.set_input(ACTION_INPUT_ROOM_CODE, "ABC234")
    flow.handle_action(ACTION_JOIN_ROOM)
    wait_for(flow, lambda: flow.mode == "home")

    assert session.close_calls == 1
    assert flow.display_state.error == "再接続期限が切れました。"


def test_form_navigation_inputs_and_permanent_close_are_transport_free():
    flow = LanLobbyFlow(room_settings_provider=settings)
    flow.open()
    assert flow.handle_action(ACTION_MODE_JOIN)
    assert flow.handle_action(ACTION_INPUT_NAME)
    flow.set_input(ACTION_INPUT_NAME, "Alice")
    assert flow.append_text(" A")
    assert flow.backspace()
    assert flow.display_state.name == "Alice "
    assert flow.handle_action(ACTION_INPUT_ADDRESS)
    assert flow.handle_action(ACTION_INPUT_ROOM_CODE)
    assert flow.handle_action(ACTION_SPECTATOR_TOGGLE)
    assert flow.handle_action(ACTION_BACK)
    assert flow.mode == "home"
    assert flow.handle_action(ACTION_CLOSE)
    assert flow.is_open is False

    flow.close()
    flow.close()
    with pytest.raises(RuntimeError):
        flow.open()

    # Explicit leave keeps the overlay open and returns to its home screen.
    other = LanLobbyFlow(room_settings_provider=settings)
    other.open()
    assert other.handle_action(ACTION_LEAVE_ROOM)
    assert other.is_open is True
    assert other.mode == "home"
