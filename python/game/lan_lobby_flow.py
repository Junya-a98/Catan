"""Transport- and Pygame-independent orchestration for the LAN lobby UI.

``LanLobbyFlow`` sits between a presentation layer and ``LanClientSession``.
It accepts the stable string actions exported by ``lan_lobby_display`` without
importing Pygame, owns connection lifecycle, and publishes a frozen display
state that the renderer can copy into its own DTO.

Potentially blocking socket connects always run on a daemon worker.  Socket
events, lobby state, and parsed game snapshots are applied only when
``update`` is called by the application thread.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import ipaddress
import math
import queue
import re
import socket
import threading
import time
from types import MappingProxyType
from typing import Any, Protocol
import unicodedata

from game.network_view import (
    NetworkGameView,
    NetworkViewError,
    parse_state_snapshot,
)


# These values deliberately mirror ``game.lan_lobby_display``.  Keeping the
# strings here avoids importing Pygame into orchestration and future Web code.
ACTION_CLOSE = "lobby_close"
ACTION_BACK = "lobby_back"
ACTION_MODE_CREATE = "lobby_mode_create"
ACTION_MODE_JOIN = "lobby_mode_join"
ACTION_MODE_SPECTATE = "lobby_mode_spectate"
ACTION_INPUT_NAME = "lobby_input_name"
ACTION_INPUT_ADDRESS = "lobby_input_address"
ACTION_INPUT_ROOM_CODE = "lobby_input_room_code"
ACTION_SPECTATOR_TOGGLE = "lobby_spectator_toggle"
ACTION_CREATE_ROOM = "lobby_create_room"
ACTION_JOIN_ROOM = "lobby_join_room"
ACTION_COPY_ROOM_CODE = "lobby_copy_room_code"
ACTION_TOGGLE_READY = "lobby_toggle_ready"
ACTION_START_MATCH = "lobby_start_match"
ACTION_LEAVE_ROOM = "lobby_leave_room"
ACTION_RECONNECT = "lobby_reconnect"

LAN_LOBBY_MODES = frozenset({"home", "create", "join", "connected", "disconnected"})
INPUT_ACTIONS = frozenset(
    {ACTION_INPUT_NAME, ACTION_INPUT_ADDRESS, ACTION_INPUT_ROOM_CODE}
)

_ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_ROOM_CODE_PATTERN = re.compile(rf"[{_ROOM_CODE_ALPHABET}]{{6}}\Z")
_HOST_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_MAX_ADDRESS_LENGTH = 260
_MAX_COMMAND_OPTIONS = 512
_MAX_COMMAND_OPTION_DEPTH = 8
_MAX_COMMAND_OPTION_ITEMS = 8_192
_MAX_COMMAND_OPTION_CONTAINER_ITEMS = 128
_MAX_COMMAND_OPTION_TEXT = 256
_MAX_SAFE_JSON_INTEGER = (1 << 53) - 1
_MAX_LOBBY_SNAPSHOT_ITEMS = 4_096
_MAX_LOBBY_MEMBERS = 128


class LanEndpointError(ValueError):
    """Raised when a LAN endpoint is not an explicit safe ``host:port``."""


class _ClientSession(Protocol):
    room_code: str | None
    role: str | None
    seat_index: int | None
    reconnect_token: str | None
    lobby: Mapping[str, Any] | None
    game_snapshot: Mapping[str, Any] | None
    game_revision: int | None
    last_error: Mapping[str, Any] | None

    @property
    def is_connected(self) -> bool: ...

    @property
    def is_synchronized(self) -> bool: ...

    def connect(self, host: str, port: int, *, timeout: float = 5.0) -> None: ...

    def create_room(self, display_name: str, **settings: Any) -> None: ...

    def join_room(
        self,
        room_code: str,
        display_name: str,
        *,
        spectator: bool = False,
    ) -> None: ...

    def reconnect_room(
        self,
        room_code: str | None = None,
        reconnect_token: str | None = None,
    ) -> None: ...

    def set_ready(self, ready: bool = True) -> None: ...

    def start_game(self) -> None: ...

    def leave_room(self) -> None: ...

    def send_game_command(
        self,
        command: str,
        args: Mapping[str, Any] | None = None,
    ) -> int: ...

    def poll(self, *, limit: int = 100) -> list[Any]: ...

    def close(self) -> None: ...


class _ServerRuntime(Protocol):
    @property
    def address(self) -> tuple[str, int]: ...

    def start(self) -> tuple[str, int]: ...

    def pump(self, *, event_limit: int = 200) -> int: ...

    def stop(self) -> None: ...


SessionFactory = Callable[[], _ClientSession]
RuntimeFactory = Callable[[str, int], _ServerRuntime]
RoomSettingsProvider = Callable[[], Mapping[str, Any]]
ClipboardCallback = Callable[[str], None]
AdvertisedHostResolver = Callable[[], str]


@dataclass(frozen=True)
class LanLobbyFlowDisplayState:
    """Pygame-free immutable counterpart of ``LanLobbyDisplayState``."""

    mode: str = "home"
    name: str = ""
    address: str = ""
    room_code: str = ""
    spectator: bool = False
    connecting: bool = False
    error: str = ""
    lobby_snapshot: Mapping[str, Any] | None = None
    local_role: str | None = None
    local_seat: int | None = None
    focused_field: str | None = None


@dataclass(frozen=True)
class _ConnectionResult:
    generation: int
    kind: str
    session: _ClientSession | None = None
    runtime: _ServerRuntime | None = None
    endpoint: tuple[str, int] | None = None
    advertised_endpoint: tuple[str, int] | None = None
    error: str = ""


def parse_lan_endpoint(value: str) -> tuple[str, int]:
    """Parse an explicit IPv4/hostname endpoint and reject ambiguous input.

    IPv6 is intentionally rejected because the current TCP transport is IPv4.
    A port is mandatory and must be written as ASCII decimal in ``1..65535``.
    """

    if not isinstance(value, str):
        raise LanEndpointError("接続先は host:port 形式の文字列で指定してください。")
    endpoint = value.strip()
    if not endpoint or len(endpoint) > _MAX_ADDRESS_LENGTH:
        raise LanEndpointError("接続先は host:port 形式で指定してください。")
    if endpoint.count(":") != 1:
        raise LanEndpointError("接続先はIPv4またはhostnameの host:port 形式です。")
    host, port_text = endpoint.split(":", 1)
    if not host or not port_text or any(char.isspace() for char in endpoint):
        raise LanEndpointError("hostとportを空欄にできません。")
    if not port_text.isascii() or not port_text.isdecimal():
        raise LanEndpointError("portは1から65535の半角数字で指定してください。")
    port = int(port_text)
    if not 1 <= port <= 65_535:
        raise LanEndpointError("portは1から65535の範囲で指定してください。")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if all(char.isdecimal() or char == "." for char in host):
            raise LanEndpointError("IPv4 addressが不正です。") from None
        _validate_hostname(host)
        return host.lower(), port
    if address.version != 4:
        raise LanEndpointError("現在のLAN対戦はIPv4だけに対応しています。")
    return str(address), port


def _validate_hostname(host: str) -> None:
    if not host.isascii() or len(host) > 253 or host.endswith("."):
        raise LanEndpointError("hostnameが不正です。")
    labels = host.split(".")
    if any(not _HOST_LABEL_PATTERN.fullmatch(label) for label in labels):
        raise LanEndpointError("hostnameが不正です。")


class LanLobbyFlow:
    """Application-thread state machine for create/join/reconnect lobby flows."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        runtime_factory: RuntimeFactory | None = None,
        room_settings_provider: RoomSettingsProvider | None = None,
        clipboard_callback: ClipboardCallback | None = None,
        advertised_host_resolver: AdvertisedHostResolver | None = None,
        clock: Callable[[], float] | None = None,
        connect_timeout: float = 5.0,
        default_name: str = "Player",
        default_address: str = "127.0.0.1:47624",
    ) -> None:
        if (
            isinstance(connect_timeout, bool)
            or not isinstance(connect_timeout, (int, float))
            or not 0 < float(connect_timeout) <= 30
        ):
            raise ValueError("connect_timeout must be within (0, 30]")
        self._session_factory = session_factory or _default_session_factory
        self._runtime_factory = runtime_factory or _default_runtime_factory
        self._room_settings_provider = room_settings_provider or _default_room_settings
        self._clipboard_callback = clipboard_callback
        self._advertised_host_resolver = (
            advertised_host_resolver or _default_advertised_host
        )
        self._clock = clock or time.monotonic
        if not callable(self._clock):
            raise TypeError("clock must be callable")
        self._connect_timeout = float(connect_timeout)

        self._open = False
        self._closed = False
        self._mode = "home"
        self._name = _normalise_name_input(default_name)
        self._address = _normalise_address_input(default_address)
        self._room_code = ""
        self._spectator = False
        self._connecting = False
        self._error = ""
        self._focused_field: str | None = None

        self._session: _ClientSession | None = None
        self._runtime: _ServerRuntime | None = None
        self._endpoint: tuple[str, int] | None = None
        self._advertised_address: str | None = None
        self._reconnect_token: str | None = None
        self._local_role: str | None = None
        self._local_seat: int | None = None
        self._lobby_snapshot: Mapping[str, Any] | None = None
        self._latest_game_view: NetworkGameView | None = None
        self._latest_command_options: tuple[Mapping[str, Any], ...] = ()
        self._last_snapshot_marker: tuple[int | None, int] | None = None
        self._awaiting_welcome = False
        self._welcome_deadline: float | None = None
        self._connection_kind: str | None = None

        self._generation = 0
        self._connection_results: queue.SimpleQueue[_ConnectionResult] = (
            queue.SimpleQueue()
        )
        self._connection_thread: threading.Thread | None = None
        self._connection_cancel: threading.Event | None = None

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def connecting(self) -> bool:
        return self._connecting

    @property
    def is_connected(self) -> bool:
        return self._session is not None and _session_is_connected(self._session)

    @property
    def command_pending(self) -> bool:
        if (
            self._session is not None
            and self._latest_game_view is not None
            and not bool(getattr(self._session, "is_synchronized", True))
        ):
            return True
        pending = getattr(self._session, "pending_commands", None)
        return isinstance(pending, Mapping) and bool(pending)

    @property
    def latest_game_view(self) -> NetworkGameView | None:
        return self._latest_game_view

    @property
    def match_active(self) -> bool:
        return self._latest_game_view is not None

    @property
    def latest_command_options(self) -> tuple[Mapping[str, Any], ...]:
        """Raw, immutable authority-advertised commands from the latest view."""

        return self._latest_command_options

    @property
    def display_state(self) -> LanLobbyFlowDisplayState:
        display_address = self._address
        if self._mode == "connected" and self._local_role == "host":
            display_address = self._advertised_address or display_address
        return LanLobbyFlowDisplayState(
            mode=self._mode,
            name=self._name,
            address=display_address,
            room_code=self._room_code,
            spectator=self._spectator,
            connecting=self._connecting,
            error=self._error,
            # Return a detached copy so dataclasses.asdict can bridge this DTO
            # into LanLobbyDisplayState without exposing mutable flow internals.
            lobby_snapshot=_deep_thaw(self._lobby_snapshot),
            local_role=self._local_role,
            local_seat=self._local_seat,
            focused_field=self._focused_field,
        )

    def open(self) -> None:
        """Show the flow at its current safe screen."""

        if self._closed:
            raise RuntimeError("closed LAN lobby flow cannot be reopened")
        self._open = True
        if self._mode not in LAN_LOBBY_MODES:
            self._mode = "home"

    def set_input(self, action: str, value: str) -> None:
        """Replace one form field without requiring keyboard/Pygame objects."""

        if action == ACTION_INPUT_NAME:
            self._name = _normalise_name_input(value)
        elif action == ACTION_INPUT_ADDRESS:
            self._address = _normalise_address_input(value)
        elif action == ACTION_INPUT_ROOM_CODE:
            self._room_code = _normalise_room_code_input(value)
        else:
            raise ValueError("unknown LAN lobby input action")

    def append_text(self, text: str) -> bool:
        """Append text to the focused field; useful for any presentation layer."""

        if not isinstance(text, str) or self._focused_field not in INPUT_ACTIONS:
            return False
        current = self._input_value(self._focused_field)
        self.set_input(self._focused_field, current + text)
        return True

    def backspace(self) -> bool:
        if self._focused_field not in INPUT_ACTIONS:
            return False
        current = self._input_value(self._focused_field)
        self.set_input(self._focused_field, current[:-1])
        return True

    def submit(self) -> bool:
        if self._mode == "create":
            return self.create_room()
        if self._mode == "join":
            return self.join_room()
        return False

    def handle_action(self, action: str) -> bool:
        """Apply one stable action emitted by the LAN lobby presentation."""

        if action == ACTION_CLOSE:
            self.leave_room(close_overlay=True)
            return True
        if action == ACTION_BACK:
            if self._mode in ("create", "join") and self._connecting:
                self.leave(close_overlay=False)
                return True
            if self._mode in ("create", "join"):
                self._mode = "home"
                self._focused_field = None
                self._error = ""
                return True
            if self._mode == "disconnected" and not self._connecting:
                self.leave(close_overlay=True)
                return True
            return False
        if action == ACTION_MODE_CREATE and self._mode == "home":
            self._mode = "create"
            self._spectator = False
            self._error = ""
            return True
        if action in (ACTION_MODE_JOIN, ACTION_MODE_SPECTATE) and self._mode == "home":
            self._mode = "join"
            self._spectator = action == ACTION_MODE_SPECTATE
            self._error = ""
            return True
        if action in INPUT_ACTIONS:
            return self._focus_input(action)
        if action == ACTION_SPECTATOR_TOGGLE and self._mode == "join":
            if self._connecting:
                return False
            self._spectator = not self._spectator
            return True
        if action == ACTION_CREATE_ROOM:
            return self.create_room()
        if action == ACTION_JOIN_ROOM:
            return self.join_room()
        if action == ACTION_COPY_ROOM_CODE:
            return self.copy_room_code()
        if action == ACTION_TOGGLE_READY:
            return self.toggle_ready()
        if action == ACTION_START_MATCH:
            return self.start_match()
        if action == ACTION_LEAVE_ROOM:
            self.leave_room(close_overlay=False)
            return True
        if action == ACTION_RECONNECT:
            return self.reconnect()
        return False

    def create_room(self) -> bool:
        if self._mode != "create" or self._connecting:
            return False
        try:
            endpoint = parse_lan_endpoint(self._address)
            name = _validated_submit_name(self._name)
            settings = _validated_room_settings(self._room_settings_provider())
        except (TypeError, ValueError) as exc:
            self._error = str(exc)
            return False
        self._begin_connection(
            "create",
            endpoint,
            name=name,
            settings=settings,
        )
        return True

    def join_room(self) -> bool:
        if self._mode != "join" or self._connecting:
            return False
        try:
            endpoint = parse_lan_endpoint(self._address)
            name = _validated_submit_name(self._name)
            room_code = _validated_room_code(self._room_code)
        except (TypeError, ValueError) as exc:
            self._error = str(exc)
            return False
        self._begin_connection(
            "join",
            endpoint,
            name=name,
            room_code=room_code,
            spectator=self._spectator,
        )
        return True

    def reconnect(self) -> bool:
        if self._mode != "disconnected" or self._connecting:
            return False
        if not self._endpoint or not self._room_code or not self._reconnect_token:
            self._error = "再接続に必要な参加コードまたはtokenがありません。"
            return False
        self._begin_connection(
            "reconnect",
            self._endpoint,
            room_code=self._room_code,
            reconnect_token=self._reconnect_token,
        )
        return True

    def copy_room_code(self) -> bool:
        if not self._room_code:
            self._error = "コピーできる参加コードがありません。"
            return False
        if self._clipboard_callback is None:
            self._error = "クリップボード機能を利用できません。"
            return False
        try:
            self._clipboard_callback(self._room_code)
        except Exception as exc:  # UI callback errors are non-fatal.
            self._error = f"参加コードをコピーできませんでした: {exc}"
            return False
        self._error = ""
        return True

    def toggle_ready(self) -> bool:
        session = self._session
        if self._mode != "connected" or self._local_role not in ("host", "player"):
            return False
        if session is None or self._connecting:
            return False
        ready = not self._local_ready()
        try:
            _clear_last_error(session)
            session.set_ready(ready)
        except Exception as exc:
            self._error = f"準備状態を変更できませんでした: {exc}"
            return False
        self._error = ""
        return True

    def start_match(self) -> bool:
        session = self._session
        if self._mode != "connected" or self._local_role != "host":
            return False
        if session is None or self._connecting:
            return False
        try:
            _clear_last_error(session)
            session.start_game()
        except Exception as exc:
            self._error = f"対局を開始できませんでした: {exc}"
            return False
        self._error = ""
        return True

    def send_game_command(
        self,
        command: str,
        args: Mapping[str, Any] | None = None,
    ) -> bool:
        """Send one authority-advertised match command without blocking UI."""

        session = self._session
        if (
            session is None
            or self._latest_game_view is None
            or self._mode != "connected"
            or self.command_pending
        ):
            return False
        session_revision = getattr(session, "game_revision", None)
        if (
            type(session_revision) is int
            and self._latest_game_view.revision != session_revision
        ):
            self._latest_command_options = ()
            self._error = "最新の盤面を受信するまで操作できません。"
            return False
        try:
            _clear_last_error(session)
            session.send_game_command(command, args)
        except Exception as exc:
            self._error = f"操作を送信できませんでした: {exc}"
            return False
        self._error = ""
        return True

    def update(self) -> None:
        """Apply worker results, pump host authority, then poll the client."""

        self._drain_connection_results()
        runtime = self._runtime
        if runtime is not None:
            try:
                runtime.pump()
            except Exception as exc:
                self._error = f"LANホストを継続できませんでした: {exc}"
                _safe_stop(runtime)
                self._runtime = None
                self._fail_or_disconnect(self._error)
                return

        session = self._session
        if session is None:
            return
        try:
            events = session.poll()
        except Exception as exc:
            self._fail_or_disconnect(f"LAN接続を確認できませんでした: {exc}")
            return
        if self._consume_events(events):
            return
        self._sync_session_state(session)
        if self._welcome_has_expired():
            self._error = "LANサーバーから参加確認が届きませんでした。"
            self._fail_welcome()
            return
        if not _session_is_connected(session):
            self._fail_or_disconnect("ホストとの接続が切れました。")

    def leave_room(self, *, close_overlay: bool = False) -> None:
        """Notify the authority of an intentional departure, then clean up."""

        session = self._session
        if session is not None and _session_is_connected(session):
            try:
                session.leave_room()
            except Exception:
                # Closing the transport still informs the authority through
                # its reconnect-reserving disconnect path.
                pass
        self.leave(close_overlay=close_overlay)

    def leave(self, *, close_overlay: bool = False) -> None:
        """Release all owned resources without assuming room membership."""

        self._cancel_pending_connection()
        self._generation += 1
        session, self._session = self._session, None
        runtime, self._runtime = self._runtime, None
        _safe_close(session)
        _safe_stop(runtime)
        self._connecting = False
        self._awaiting_welcome = False
        self._welcome_deadline = None
        self._connection_kind = None
        self._endpoint = None
        self._advertised_address = None
        self._reconnect_token = None
        self._room_code = ""
        self._local_role = None
        self._local_seat = None
        self._lobby_snapshot = None
        self._latest_game_view = None
        self._latest_command_options = ()
        self._last_snapshot_marker = None
        self._focused_field = None
        self._error = ""
        self._mode = "home"
        if close_overlay:
            self._open = False

    def close(self) -> None:
        """Permanently close the flow; safe to call more than once."""

        if self._closed:
            return
        self.leave_room(close_overlay=True)
        self._closed = True

    def _begin_connection(
        self,
        kind: str,
        endpoint: tuple[str, int],
        **payload: Any,
    ) -> None:
        self._cancel_pending_connection()
        if kind == "reconnect":
            # The preserved view remains useful while reconnecting, but its
            # authority-advertised actions are stale until a fresh snapshot.
            self._latest_command_options = ()
        self._generation += 1
        generation = self._generation
        cancel = threading.Event()
        self._connection_cancel = cancel
        self._connecting = True
        self._awaiting_welcome = False
        self._welcome_deadline = None
        self._connection_kind = kind
        self._focused_field = None
        self._error = ""

        thread = threading.Thread(
            target=self._connection_worker,
            args=(generation, cancel, kind, endpoint, payload),
            name=f"catan-lan-{kind}",
            daemon=True,
        )
        self._connection_thread = thread
        thread.start()

    def _connection_worker(
        self,
        generation: int,
        cancel: threading.Event,
        kind: str,
        endpoint: tuple[str, int],
        payload: Mapping[str, Any],
    ) -> None:
        session: _ClientSession | None = None
        runtime: _ServerRuntime | None = None
        connect_endpoint = endpoint
        advertised_endpoint = endpoint
        try:
            if kind == "create":
                runtime = self._runtime_factory(*endpoint)
                bound = runtime.start()
                if bound is None:
                    bound = runtime.address
                bound_host, bound_port = _validated_bound_address(bound)
                connect_endpoint = ("127.0.0.1", bound_port)
                advertised_host = bound_host
                if bound_host in ("0.0.0.0", ""):
                    advertised_host = _safe_advertised_host(
                        self._advertised_host_resolver
                    )
                advertised_endpoint = (
                    advertised_host,
                    bound_port,
                )
            if cancel.is_set():
                _safe_stop(runtime)
                return

            session = self._session_factory()
            session.connect(
                *connect_endpoint,
                timeout=self._connect_timeout,
            )
            if cancel.is_set():
                _safe_close(session)
                _safe_stop(runtime)
                return

            if kind == "create":
                session.create_room(payload["name"], **payload["settings"])
            elif kind == "join":
                session.join_room(
                    payload["room_code"],
                    payload["name"],
                    spectator=bool(payload["spectator"]),
                )
            elif kind == "reconnect":
                session.reconnect_room(
                    payload["room_code"],
                    payload["reconnect_token"],
                )
            else:  # Defensive guard for future internal callers.
                raise ValueError(f"unsupported connection kind: {kind}")
        except Exception as exc:
            _safe_close(session)
            _safe_stop(runtime)
            if not cancel.is_set():
                self._connection_results.put(
                    _ConnectionResult(
                        generation,
                        kind,
                        endpoint=connect_endpoint,
                        advertised_endpoint=advertised_endpoint,
                        error=str(exc) or exc.__class__.__name__,
                    )
                )
            return

        if cancel.is_set():
            _safe_close(session)
            _safe_stop(runtime)
            return
        self._connection_results.put(
            _ConnectionResult(
                generation,
                kind,
                session=session,
                runtime=runtime,
                endpoint=connect_endpoint,
                advertised_endpoint=advertised_endpoint,
            )
        )

    def _drain_connection_results(self) -> None:
        while True:
            try:
                result = self._connection_results.get_nowait()
            except queue.Empty:
                return
            if result.generation != self._generation or self._closed:
                _safe_close(result.session)
                _safe_stop(result.runtime)
                continue
            self._connection_thread = None
            self._connection_cancel = None
            if result.error:
                self._connecting = False
                self._awaiting_welcome = False
                self._welcome_deadline = None
                if result.kind == "reconnect":
                    self._mode = "disconnected"
                self._error = f"LAN接続に失敗しました: {result.error}"
                continue

            if result.kind != "reconnect":
                _safe_close(self._session)
            self._session = result.session
            if result.runtime is not None:
                _safe_stop(self._runtime)
                self._runtime = result.runtime
            self._endpoint = result.endpoint
            if result.kind == "create" and result.advertised_endpoint is not None:
                host, port = result.advertised_endpoint
                self._advertised_address = f"{host}:{port}"
            self._awaiting_welcome = True
            self._welcome_deadline = self._now() + self._connect_timeout
            # The TCP connect completed, but remain in a connecting form until
            # session_welcome proves room membership.
            self._connecting = True

    def _consume_events(self, events: Any) -> bool:
        disconnected_detail: str | None = None
        for event in events or ():
            kind = _event_value(event, "kind")
            detail = str(_event_value(event, "detail") or "")
            if kind == "disconnected":
                disconnected_detail = detail or "ホストとの接続が切れました。"
                continue
            if kind == "protocol_error":
                self._error = detail or "LANメッセージを読み取れませんでした。"
                continue
            if kind != "message":
                continue
            message = _event_value(event, "message")
            if not isinstance(message, Mapping):
                continue
            message_type = message.get("type")
            if message_type == "session_welcome":
                self._apply_welcome(message)
            elif message_type == "lobby_snapshot":
                lobby = message.get("lobby")
                if isinstance(lobby, Mapping):
                    try:
                        self._lobby_snapshot = _copy_lobby_snapshot(lobby)
                        self._sync_local_role_from_lobby()
                    except (TypeError, ValueError, RecursionError):
                        self._error = "ロビー情報を安全に読み取れませんでした。"
            elif message_type == "state_snapshot":
                self._apply_state_snapshot(message)
            elif message_type == "room_closed":
                self._apply_room_closed(message)
                return True
            elif message_type == "request_error":
                self._apply_request_error(message)
                if self._awaiting_welcome:
                    self._fail_welcome()
                    return True
        if disconnected_detail is not None:
            self._fail_or_disconnect(disconnected_detail)
            return True
        return False

    def _apply_welcome(self, message: Mapping[str, Any]) -> None:
        room_code = message.get("room_code")
        role = message.get("role")
        seat_index = message.get("seat_index")
        token = message.get("reconnect_token")
        if isinstance(room_code, str):
            self._room_code = room_code
        if role in ("host", "player", "spectator"):
            self._local_role = role
        if type(seat_index) is int and 0 <= seat_index <= 3:
            self._local_seat = seat_index + 1
        elif seat_index is None:
            self._local_seat = None
        if isinstance(token, str) and token:
            self._reconnect_token = token
        self._mode = "connected"
        self._connecting = False
        self._awaiting_welcome = False
        self._welcome_deadline = None
        self._error = ""

    def _apply_request_error(self, message: Mapping[str, Any]) -> None:
        detail = message.get("message")
        self._error = (
            detail
            if isinstance(detail, str) and detail
            else "LANサーバーが操作を受け付けませんでした。"
        )

    def _apply_room_closed(self, message: Mapping[str, Any]) -> None:
        detail = message.get("message")
        detail = (
            detail
            if isinstance(detail, str) and detail
            else "LAN対戦が終了しました。"
        )
        self.leave(close_overlay=False)
        self._error = detail

    def _apply_state_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        revision = snapshot.get("revision")
        marker = (revision if type(revision) is int else None, id(snapshot))
        if marker == self._last_snapshot_marker:
            return
        self._last_snapshot_marker = marker
        try:
            view = parse_state_snapshot(snapshot)
        except NetworkViewError as exc:
            self._latest_game_view = None
            self._latest_command_options = ()
            self._error = f"対局状態を安全に表示できません: {exc}"
            return
        self._latest_game_view = view
        self._latest_command_options = _copy_command_options(
            snapshot.get("command_options")
        )
        self._error = ""

    def _sync_session_state(self, session: _ClientSession) -> None:
        room_code = getattr(session, "room_code", None)
        role = getattr(session, "role", None)
        seat_index = getattr(session, "seat_index", None)
        token = getattr(session, "reconnect_token", None)
        lobby = getattr(session, "lobby", None)
        snapshot = getattr(session, "game_snapshot", None)
        if isinstance(room_code, str) and room_code:
            self._room_code = room_code
        if role in ("host", "player", "spectator"):
            self._local_role = role
        if type(seat_index) is int and 0 <= seat_index <= 3:
            self._local_seat = seat_index + 1
        elif role == "spectator":
            self._local_seat = None
        if isinstance(token, str) and token:
            self._reconnect_token = token
        if isinstance(lobby, Mapping):
            try:
                self._lobby_snapshot = _copy_lobby_snapshot(lobby)
                self._sync_local_role_from_lobby()
            except (TypeError, ValueError, RecursionError):
                self._error = "ロビー情報を安全に読み取れませんでした。"
        if isinstance(snapshot, Mapping):
            self._apply_state_snapshot(snapshot)
        if (
            self._awaiting_welcome
            and isinstance(room_code, str)
            and room_code
            and role in ("host", "player", "spectator")
        ):
            self._mode = "connected"
            self._connecting = False
            self._awaiting_welcome = False
            self._welcome_deadline = None

        last_error = getattr(session, "last_error", None)
        if isinstance(last_error, Mapping):
            self._apply_request_error(last_error)
            if self._awaiting_welcome:
                self._fail_welcome()

    def _fail_welcome(self) -> None:
        session, self._session = self._session, None
        _safe_close(session)
        if self._connection_kind == "create":
            runtime, self._runtime = self._runtime, None
            _safe_stop(runtime)
            self._mode = "create"
        elif self._connection_kind == "join":
            self._mode = "join"
        else:
            self._mode = "disconnected"
        self._connecting = False
        self._awaiting_welcome = False
        self._welcome_deadline = None

    def _handle_disconnect(self, detail: str) -> None:
        session, self._session = self._session, None
        if session is not None:
            room_code = getattr(session, "room_code", None)
            token = getattr(session, "reconnect_token", None)
            if isinstance(room_code, str) and room_code:
                self._room_code = room_code
            if isinstance(token, str) and token:
                self._reconnect_token = token
        _safe_close(session)
        self._connecting = False
        self._awaiting_welcome = False
        self._welcome_deadline = None
        self._focused_field = None
        self._latest_command_options = ()
        self._mode = "disconnected"
        self._error = detail or "ホストとの接続が切れました。"

    def _fail_or_disconnect(self, detail: str) -> None:
        if self._awaiting_welcome:
            self._error = detail
            self._fail_welcome()
            return
        self._handle_disconnect(detail)

    def _focus_input(self, action: str) -> bool:
        if self._connecting or self._mode not in ("create", "join"):
            return False
        if action == ACTION_INPUT_ROOM_CODE and self._mode != "join":
            return False
        self._focused_field = action
        return True

    def _input_value(self, action: str) -> str:
        if action == ACTION_INPUT_NAME:
            return self._name
        if action == ACTION_INPUT_ADDRESS:
            return self._address
        if action == ACTION_INPUT_ROOM_CODE:
            return self._room_code
        raise ValueError("unknown LAN lobby input action")

    def _local_ready(self) -> bool:
        if self._local_seat is None or self._lobby_snapshot is None:
            return False
        members = self._lobby_snapshot.get("members", ())
        if not isinstance(members, (tuple, list)):
            return False
        for member in members:
            if isinstance(member, Mapping) and member.get("seat") == self._local_seat:
                return bool(member.get("ready", False))
        return False

    def _sync_local_role_from_lobby(self) -> None:
        """Apply host promotion from the latest authoritative lobby snapshot."""

        if self._local_seat is None or self._lobby_snapshot is None:
            return
        members = self._lobby_snapshot.get("members", ())
        if not isinstance(members, (tuple, list)):
            return
        for member in members:
            if not isinstance(member, Mapping):
                continue
            if member.get("seat") != self._local_seat:
                continue
            role = member.get("role")
            if role in ("host", "player"):
                self._local_role = role
            return

    def _cancel_pending_connection(self) -> None:
        if self._connection_cancel is not None:
            self._connection_cancel.set()
        self._connection_cancel = None
        self._connection_thread = None

    def _welcome_has_expired(self) -> bool:
        return bool(
            self._awaiting_welcome
            and self._welcome_deadline is not None
            and self._now() >= self._welcome_deadline
        )

    def _now(self) -> float:
        try:
            value = self._clock()
        except Exception as exc:
            raise RuntimeError("LAN lobby clock failed") from exc
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise RuntimeError("LAN lobby clock returned an invalid value")
        return float(value)


def _normalise_name_input(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("name input must be a string")
    return value[:32]


def _normalise_address_input(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("address input must be a string")
    return value[:_MAX_ADDRESS_LENGTH]


def _normalise_room_code_input(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("room-code input must be a string")
    return value.upper()[:6]


def _validated_submit_name(value: str) -> str:
    name = value.strip()
    if not 1 <= len(name) <= 32 or any(
        unicodedata.category(char).startswith("C") for char in name
    ):
        raise ValueError("表示名は制御文字を含まない1〜32文字で指定してください。")
    return name


def _validated_room_code(value: str) -> str:
    code = value.strip().upper()
    if not _ROOM_CODE_PATTERN.fullmatch(code):
        raise ValueError("参加コードは表示された6文字で指定してください。")
    return code


def _validated_room_settings(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("room settings provider must return a mapping")
    expected = {"player_count", "victory_target", "board_mode", "board_seed"}
    if set(value) != expected:
        raise ValueError("部屋設定の項目が不足または過剰です。")
    player_count = value["player_count"]
    victory_target = value["victory_target"]
    board_mode = value["board_mode"]
    board_seed = value["board_seed"]
    if type(player_count) is not int or not 2 <= player_count <= 4:
        raise ValueError("player_countは2〜4で指定してください。")
    if type(victory_target) is not int or not 5 <= victory_target <= 15:
        raise ValueError("victory_targetは5〜15で指定してください。")
    if board_mode not in ("constrained", "fully_random"):
        raise ValueError("board_modeが不正です。")
    if type(board_seed) is not int or abs(board_seed) > _MAX_SAFE_JSON_INTEGER:
        raise ValueError("board_seedは安全な範囲の整数で指定してください。")
    return {
        "player_count": player_count,
        "victory_target": victory_target,
        "board_mode": board_mode,
        "board_seed": board_seed,
    }


def _validated_bound_address(value: Any) -> tuple[str, int]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError("LAN host did not return a bound address")
    host, port = value
    if not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65_535:
        raise ValueError("LAN host returned an invalid bound address")
    return host, port


def _event_value(event: Any, key: str) -> Any:
    if isinstance(event, Mapping):
        return event.get(key)
    return getattr(event, key, None)


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_deep_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return {_deep_thaw(item) for item in value}
    return value


def _copy_lobby_snapshot(value: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen = _freeze_command_option_value(
        value,
        depth=0,
        budget=[_MAX_LOBBY_SNAPSHOT_ITEMS],
        active=set(),
    )
    if not isinstance(frozen, Mapping):
        raise ValueError("lobby snapshot must be an object")
    _validate_lobby_snapshot(frozen)
    return frozen


def _validate_lobby_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Reject malformed peer data before it reaches the Pygame renderer."""

    required = {
        "room_code",
        "revision",
        "phase",
        "settings",
        "can_start",
        "members",
    }
    allowed = required | {
        "full",
        "player_members",
        "spectators",
    }
    if not required.issubset(snapshot) or not set(snapshot).issubset(allowed):
        raise ValueError("lobby snapshot fields are invalid")
    room_code = snapshot["room_code"]
    if not isinstance(room_code, str) or not _ROOM_CODE_PATTERN.fullmatch(room_code):
        raise ValueError("lobby room code is invalid")
    revision = snapshot["revision"]
    if (
        type(revision) is not int
        or not 0 <= revision <= _MAX_SAFE_JSON_INTEGER
    ):
        raise ValueError("lobby revision is invalid")
    if snapshot["phase"] not in ("waiting", "started"):
        raise ValueError("lobby phase is invalid")
    settings = snapshot["settings"]
    if not isinstance(settings, Mapping):
        raise ValueError("lobby settings are invalid")
    validated_settings = _validated_room_settings(settings)
    if type(snapshot["can_start"]) is not bool:
        raise ValueError("lobby can_start is invalid")
    if "full" in snapshot and type(snapshot["full"]) is not bool:
        raise ValueError("lobby full is invalid")

    members = snapshot["members"]
    if not isinstance(members, (tuple, list)) or len(members) > _MAX_LOBBY_MEMBERS:
        raise ValueError("lobby members are invalid")
    occupied_seats: set[int] = set()
    host_count = 0
    spectator_count = 0
    for member in members:
        if not isinstance(member, Mapping):
            raise ValueError("lobby member is invalid")
        _validate_lobby_member(
            member,
            player_count=validated_settings["player_count"],
            occupied_seats=occupied_seats,
        )
        if member["role"] == "host":
            host_count += 1
        elif member["role"] == "spectator":
            spectator_count += 1
    if host_count > 1:
        raise ValueError("lobby host count is invalid")
    player_count = len(occupied_seats)
    _validate_optional_snapshot_count(snapshot, "player_members", player_count)
    _validate_optional_snapshot_count(snapshot, "spectators", spectator_count)


def _validate_lobby_member(
    member: Mapping[str, Any],
    *,
    player_count: int,
    occupied_seats: set[int],
) -> None:
    required = {"display_name", "role", "seat", "connected", "ready"}
    allowed = required | {"reservation_seconds_remaining"}
    if not required.issubset(member) or not set(member).issubset(allowed):
        raise ValueError("lobby member fields are invalid")
    display_name = member["display_name"]
    if (
        not isinstance(display_name, str)
        or _validated_submit_name(display_name) != display_name
    ):
        raise ValueError("lobby display name is invalid")
    role = member["role"]
    seat = member["seat"]
    if role == "spectator":
        if seat is not None:
            raise ValueError("spectator seat is invalid")
    elif role in ("host", "player"):
        if type(seat) is not int or not 1 <= seat <= player_count:
            raise ValueError("player seat is invalid")
        if seat in occupied_seats:
            raise ValueError("player seats must be unique")
        occupied_seats.add(seat)
    else:
        raise ValueError("lobby member role is invalid")
    if type(member["connected"]) is not bool or type(member["ready"]) is not bool:
        raise ValueError("lobby member flags are invalid")
    remaining = member.get("reservation_seconds_remaining")
    if remaining is not None and (
        isinstance(remaining, bool)
        or not isinstance(remaining, (int, float))
        or not math.isfinite(float(remaining))
        or not 0 <= float(remaining) <= 86_400
    ):
        raise ValueError("lobby reservation is invalid")


def _validate_optional_snapshot_count(
    snapshot: Mapping[str, Any],
    key: str,
    expected: int,
) -> None:
    if key not in snapshot:
        return
    value = snapshot[key]
    if type(value) is not int or value != expected:
        raise ValueError(f"lobby {key} is invalid")


def _copy_command_options(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    copied = []
    budget = [_MAX_COMMAND_OPTION_ITEMS]
    for option in value[:_MAX_COMMAND_OPTIONS]:
        if not isinstance(option, Mapping):
            continue
        try:
            frozen = _freeze_command_option_value(
                option,
                depth=0,
                budget=budget,
                active=set(),
            )
        except (TypeError, ValueError, RecursionError):
            continue
        if isinstance(frozen, Mapping):
            copied.append(frozen)
    return tuple(copied)


def _freeze_command_option_value(
    value: Any,
    *,
    depth: int,
    budget: list[int],
    active: set[int],
) -> Any:
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("command option item budget exceeded")
    if depth > _MAX_COMMAND_OPTION_DEPTH:
        raise ValueError("command option nesting is too deep")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("command option number must be finite")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_COMMAND_OPTION_TEXT:
            raise ValueError("command option text is too long")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_COMMAND_OPTION_CONTAINER_ITEMS:
            raise ValueError("command option object is too large")
        identity = id(value)
        if identity in active:
            raise ValueError("command option contains a cycle")
        active.add(identity)
        try:
            result = {}
            for key, item in value.items():
                if not isinstance(key, str) or len(key) > 64:
                    raise ValueError("command option key is invalid")
                result[key] = _freeze_command_option_value(
                    item,
                    depth=depth + 1,
                    budget=budget,
                    active=active,
                )
            return MappingProxyType(result)
        finally:
            active.remove(identity)
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_COMMAND_OPTION_CONTAINER_ITEMS:
            raise ValueError("command option array is too large")
        identity = id(value)
        if identity in active:
            raise ValueError("command option contains a cycle")
        active.add(identity)
        try:
            return tuple(
                _freeze_command_option_value(
                    item,
                    depth=depth + 1,
                    budget=budget,
                    active=active,
                )
                for item in value
            )
        finally:
            active.remove(identity)
    raise TypeError("command option contains a non-JSON value")


def _safe_advertised_host(resolver: AdvertisedHostResolver) -> str:
    try:
        candidate = resolver()
        address = ipaddress.ip_address(candidate)
    except (OSError, TypeError, ValueError):
        return "127.0.0.1"
    if address.version != 4 or address.is_unspecified:
        return "127.0.0.1"
    return str(address)


def _session_is_connected(session: _ClientSession) -> bool:
    try:
        return bool(session.is_connected)
    except Exception:
        return False


def _clear_last_error(session: _ClientSession) -> None:
    try:
        session.last_error = None
    except (AttributeError, TypeError):
        pass


def _safe_close(session: _ClientSession | None) -> None:
    if session is None:
        return
    try:
        session.close()
    except Exception:
        pass


def _safe_stop(runtime: _ServerRuntime | None) -> None:
    if runtime is None:
        return
    try:
        runtime.stop()
    except Exception:
        pass


def _default_session_factory() -> _ClientSession:
    from game.lan_runtime import LanClientSession

    return LanClientSession()


def _default_runtime_factory(host: str, port: int) -> _ServerRuntime:
    from game.lan_runtime import LanServerRuntime

    return LanServerRuntime(host, port)


def _default_advertised_host() -> str:
    """Best-effort local IPv4 discovery without sending application data."""

    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect selects a route locally; it does not perform a handshake.
        udp.connect(("192.0.2.1", 9))
        candidate = udp.getsockname()[0]
        if candidate and candidate != "0.0.0.0":
            return candidate
    except OSError:
        pass
    finally:
        udp.close()

    try:
        for item in socket.getaddrinfo(
            socket.gethostname(),
            None,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        ):
            candidate = item[4][0]
            address = ipaddress.ip_address(candidate)
            if address.version == 4 and not address.is_unspecified:
                return str(address)
    except (OSError, ValueError):
        pass
    return "127.0.0.1"


def _default_room_settings() -> Mapping[str, Any]:
    return {
        "player_count": 4,
        "victory_target": 10,
        "board_mode": "constrained",
        "board_seed": 0,
    }


__all__ = (
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
    "INPUT_ACTIONS",
    "LAN_LOBBY_MODES",
    "LanEndpointError",
    "LanLobbyFlow",
    "LanLobbyFlowDisplayState",
    "parse_lan_endpoint",
)
