"""Transport-independent authoritative LAN room controller.

``LanServerController`` consumes decoded JSON messages plus an authenticated
transport connection id and returns explicit outbound messages.  It owns room
membership, per-member exactly-once command sequencing, isolated game RNG
state, and viewer-filtered snapshots.  TCP is only one adapter; a future
WebSocket server can use the same controller unchanged.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import random
import secrets
import threading
from typing import Any, Callable, Mapping

from game.game import CatanGame
from game.lan_lobby import (
    ROOM_CODE_ALPHABET,
    ROOM_CODE_LENGTH,
    LobbyAuthenticationError,
    LobbyCapacityError,
    LobbyError,
    LobbyPermissionError,
    LobbyRoom,
    LobbyStateError,
    LobbyValidationError,
    MemberRole,
    MembershipGrant,
    RoomPhase,
    RoomSettings,
)
from game.network_actions import (
    NetworkActionError,
    apply_game_command,
    build_game_command_options,
)
from game.network_protocol import (
    NETWORK_PROTOCOL_VERSION,
    NetworkProtocolError,
    build_game_command,
    build_state_snapshot,
)
from game.persistence import restore_game, serialize_game


MAX_ROOMS = 32
MAX_COMMAND_RECORDS = 128
MAX_CONNECTION_ID_LENGTH = 128
_RANDOM_LOCK = threading.RLock()


class LanControllerError(ValueError):
    """Expected request failure with a stable wire error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OutboundMessage:
    """One message destined for one transport connection."""

    connection_id: str
    message: dict[str, Any]


@dataclass
class _CommandRecord:
    fingerprint: str
    response: dict[str, Any]


@dataclass
class _CommandState:
    next_sequence: int = 0
    records: OrderedDict[int, _CommandRecord] = field(default_factory=OrderedDict)


@dataclass
class _Session:
    connection_id: str
    room_code: str
    member_id: str
    role: MemberRole
    seat_index: int | None


@dataclass
class _RoomContext:
    lobby: LobbyRoom
    match_seed: int = field(
        default_factory=lambda: secrets.randbits(256),
        repr=False,
    )
    game: Any = None
    game_revision: int = 0
    random_state: object | None = None
    command_states: dict[str, _CommandState] = field(default_factory=dict)


def _default_game_factory(
    settings: RoomSettings,
    player_names: tuple[str, ...],
) -> CatanGame:
    game = CatanGame(
        board_mode=settings.board_mode,
        board_seed=settings.board_seed,
        custom_map=settings.custom_map,
        house_rules=settings.house_rules,
        ai_player_count=0,
        headless=True,
    )
    game.player_palette = [
        (display_name, color)
        for display_name, (_default_name, color) in zip(
            player_names,
            game.player_palette,
        )
    ]
    game.configure_players(
        settings.player_count,
        reset_logs=False,
        schedule_ai=False,
        reset_replay=False,
    )
    for player in game.players:
        player.is_ai = False
    game.victory_point_target = settings.victory_target
    game.network_mode = True
    game.reset_match_metrics()
    game.clear_log()
    game.add_log("LAN対戦を開始しました。")
    return game


class LanServerController:
    """Authoritative multi-room state machine for a trusted LAN server."""

    def __init__(
        self,
        *,
        game_factory: Callable[[RoomSettings, tuple[str, ...]], Any] = _default_game_factory,
        command_applier: Callable[[Any, int | None, str, dict[str, Any] | None], bool] = apply_game_command,
        snapshot_builder: Callable[..., dict[str, Any]] = build_state_snapshot,
        room_limit: int = MAX_ROOMS,
    ) -> None:
        if not callable(game_factory) or not callable(command_applier) or not callable(snapshot_builder):
            raise ValueError("controller dependencies must be callable")
        if type(room_limit) is not int or not 1 <= room_limit <= 256:
            raise ValueError("room_limit must be 1..256")
        self._game_factory = game_factory
        self._command_applier = command_applier
        self._snapshot_builder = snapshot_builder
        self._room_limit = room_limit
        self._rooms: dict[str, _RoomContext] = {}
        self._sessions: dict[str, _Session] = {}

    @property
    def room_codes(self) -> tuple[str, ...]:
        return tuple(sorted(self._rooms))

    def handle(
        self,
        connection_id: str | int,
        message: Mapping[str, Any],
    ) -> tuple[OutboundMessage, ...]:
        """Handle one decoded request and return messages to send."""

        connection_key = self._connection_key(connection_id)
        try:
            message_type = self._validate_envelope(message)
            if message_type == "create_room":
                return self._create_room(connection_key, message)
            if message_type == "join_room":
                return self._join_room(connection_key, message)
            if message_type == "reconnect_room":
                return self._reconnect_room(connection_key, message)
            if message_type == "leave_room":
                return self._leave_room(connection_key, message)
            if message_type == "set_ready":
                return self._set_ready(connection_key, message)
            if message_type == "start_game":
                return self._start_game(connection_key, message)
            if message_type == "game_command":
                return self._game_command(connection_key, message)
            if message_type == "ping":
                self._expect_fields(message, "type", "protocol_version", "nonce")
                return (
                    OutboundMessage(
                        connection_key,
                        self._wire("pong", nonce=self._safe_nonce(message["nonce"])),
                    ),
                )
            raise LanControllerError("unsupported_message", "未対応のLANメッセージです。")
        except (LanControllerError, LobbyError, NetworkProtocolError) as exc:
            code, detail = self._public_error(exc)
            return (
                OutboundMessage(
                    connection_key,
                    self._wire("request_error", code=code, message=detail),
                ),
            )
        except Exception:
            # Transport-facing errors must neither terminate the server loop nor
            # expose implementation details to an untrusted peer.  Mutating
            # operations perform their own rollback before reaching this guard.
            return (
                OutboundMessage(
                    connection_key,
                    self._wire(
                        "request_error",
                        code="internal_error",
                        message="LANサーバーで操作を処理できませんでした。",
                    ),
                ),
            )

    def disconnect(
        self,
        connection_id: str | int,
    ) -> tuple[OutboundMessage, ...]:
        """Detach a transport while preserving its reconnect reservation."""

        connection_key = self._connection_key(connection_id)
        session = self._sessions.pop(connection_key, None)
        if session is None:
            return ()
        context = self._rooms.get(session.room_code)
        if context is None:
            return ()
        try:
            context.lobby.disconnect(connection_key)
        except LobbyError:
            return ()
        return self._broadcast_lobby(context)

    def tick(self) -> tuple[OutboundMessage, ...]:
        """Prune expired reservations and broadcast rooms that changed."""

        outbound = []
        for room_code, context in tuple(self._rooms.items()):
            if context.lobby.has_expired_player_reservation():
                closed = self._wire(
                    "room_closed",
                    code="player_reconnect_expired",
                    message=(
                        "プレイヤーの再接続期限が切れたため、LAN対戦を終了しました。"
                    ),
                )
                sessions = self._room_sessions(room_code)
                outbound.extend(
                    OutboundMessage(session.connection_id, closed)
                    for session in sessions
                )
                for session in sessions:
                    self._sessions.pop(session.connection_id, None)
                self._rooms.pop(room_code, None)
                continue
            if not context.lobby.prune_expired():
                continue
            if not context.lobby.public_snapshot()["members"]:
                self._rooms.pop(room_code, None)
                for session in self._room_sessions(room_code):
                    self._sessions.pop(session.connection_id, None)
                continue
            outbound.extend(self._broadcast_lobby(context))
        return tuple(outbound)

    def snapshot_for_connection(self, connection_id: str | int) -> dict[str, Any]:
        """Return the latest viewer-filtered game snapshot for diagnostics/UI."""

        session = self._require_session(self._connection_key(connection_id))
        context = self._rooms[session.room_code]
        if context.game is None:
            raise LanControllerError("game_not_started", "対局はまだ開始されていません。")
        return self._snapshot_for(context, session)

    def _create_room(
        self,
        connection_id: str,
        message: Mapping[str, Any],
    ) -> tuple[OutboundMessage, ...]:
        self._expect_fields(
            message,
            "type",
            "protocol_version",
            "display_name",
            "settings",
        )
        self._require_unattached(connection_id)
        if len(self._rooms) >= self._room_limit:
            raise LanControllerError("server_full", "このLANサーバーは部屋数の上限です。")
        raw_settings = message["settings"]
        if type(raw_settings) is not dict:
            raise LanControllerError("invalid_request", "settingsが不正です。")
        required_settings = {
            "player_count",
            "victory_target",
            "board_mode",
            "board_seed",
        }
        optional_settings = {"custom_map", "house_rules"}
        if not required_settings.issubset(raw_settings) or not set(
            raw_settings
        ).issubset(required_settings | optional_settings):
            raise LanControllerError(
                "invalid_request",
                "settingsのfieldが不正です。",
            )
        settings = RoomSettings(**raw_settings)
        code = self._new_room_code()
        lobby, grant = LobbyRoom.create(
            settings,
            host_name=message["display_name"],
            connection_id=connection_id,
            code_generator=lambda: code,
        )
        context = _RoomContext(lobby=lobby)
        self._rooms[code] = context
        self._attach(connection_id, code, grant)
        try:
            return self._welcome_and_broadcast(context, connection_id, grant)
        except Exception:
            self._sessions.pop(connection_id, None)
            self._rooms.pop(code, None)
            raise

    def _join_room(
        self,
        connection_id: str,
        message: Mapping[str, Any],
    ) -> tuple[OutboundMessage, ...]:
        self._expect_fields(
            message,
            "type",
            "protocol_version",
            "room_code",
            "display_name",
            "role",
        )
        self._require_unattached(connection_id)
        context = self._room(message["room_code"])
        lobby_before = deepcopy(context.lobby)
        role = message["role"]
        if role == MemberRole.PLAYER.value:
            grant = context.lobby.join_player(
                display_name=message["display_name"],
                connection_id=connection_id,
            )
        elif role == MemberRole.SPECTATOR.value:
            grant = context.lobby.join_spectator(
                display_name=message["display_name"],
                connection_id=connection_id,
            )
        else:
            raise LanControllerError("invalid_request", "参加roleが不正です。")
        self._attach(connection_id, context.lobby.room_code, grant)
        try:
            return self._welcome_and_broadcast(context, connection_id, grant)
        except Exception:
            self._sessions.pop(connection_id, None)
            context.lobby = lobby_before
            raise

    def _reconnect_room(
        self,
        connection_id: str,
        message: Mapping[str, Any],
    ) -> tuple[OutboundMessage, ...]:
        self._expect_fields(
            message,
            "type",
            "protocol_version",
            "room_code",
            "reconnect_token",
        )
        self._require_unattached(connection_id)
        context = self._room(message["room_code"])
        lobby_before = deepcopy(context.lobby)
        grant = context.lobby.reconnect(
            reconnect_token=message["reconnect_token"],
            connection_id=connection_id,
        )
        self._attach(connection_id, context.lobby.room_code, grant)
        try:
            return self._welcome_and_broadcast(context, connection_id, grant)
        except Exception:
            self._sessions.pop(connection_id, None)
            context.lobby = lobby_before
            raise

    def _set_ready(
        self,
        connection_id: str,
        message: Mapping[str, Any],
    ) -> tuple[OutboundMessage, ...]:
        self._expect_fields(message, "type", "protocol_version", "ready")
        session = self._require_session(connection_id)
        context = self._rooms[session.room_code]
        context.lobby.set_ready(connection_id, message["ready"])
        return self._broadcast_lobby(context)

    def _leave_room(
        self,
        connection_id: str,
        message: Mapping[str, Any],
    ) -> tuple[OutboundMessage, ...]:
        """Remove an intentional lobby departure without reserving its seat."""

        self._expect_fields(message, "type", "protocol_version")
        session = self._require_session(connection_id)
        context = self._rooms[session.room_code]
        if (
            context.lobby.phase is RoomPhase.STARTED
            and session.seat_index is not None
        ):
            closed = self._wire(
                "room_closed",
                code="player_left",
                message="プレイヤーが退出したため、LAN対戦を終了しました。",
            )
            sessions = self._room_sessions(session.room_code)
            for room_session in sessions:
                self._sessions.pop(room_session.connection_id, None)
            self._rooms.pop(session.room_code, None)
            return tuple(
                OutboundMessage(room_session.connection_id, closed)
                for room_session in sessions
            )
        context.lobby.leave(connection_id)
        self._sessions.pop(connection_id, None)
        if not context.lobby.public_snapshot()["members"]:
            self._rooms.pop(session.room_code, None)
            return ()
        return self._broadcast_lobby(context)

    def _start_game(
        self,
        connection_id: str,
        message: Mapping[str, Any],
    ) -> tuple[OutboundMessage, ...]:
        self._expect_fields(message, "type", "protocol_version")
        session = self._require_session(connection_id)
        context = self._rooms[session.room_code]
        context.lobby.validate_start(connection_id)
        player_names = self._player_names(context.lobby)
        try:
            with _RANDOM_LOCK:
                caller_state = random.getstate()
                try:
                    random.seed(context.match_seed)
                    game = self._game_factory(context.lobby.settings, player_names)
                    match_random_state = random.getstate()
                finally:
                    random.setstate(caller_state)
            game_snapshots = self._snapshot_messages_for_game(
                context,
                game,
                revision=0,
            )
        except Exception as exc:
            raise LanControllerError(
                "internal_error",
                "対局を開始できませんでした。設定を確認して再試行してください。",
            ) from exc

        # Commit only after construction and every viewer snapshot succeeds.
        # This prevents a factory/snapshot exception from leaving a STARTED
        # lobby with no usable authoritative game.
        context.lobby.start(connection_id)
        context.game = game
        context.random_state = match_random_state
        context.game_revision = 0
        outbound = list(self._broadcast_lobby(context))
        outbound.extend(game_snapshots)
        return tuple(outbound)

    def _game_command(
        self,
        connection_id: str,
        message: Mapping[str, Any],
    ) -> tuple[OutboundMessage, ...]:
        self._expect_fields(
            message,
            "type",
            "protocol_version",
            "sequence",
            "expected_revision",
            "command",
            "args",
        )
        canonical = build_game_command(
            sequence=message["sequence"],
            expected_revision=message["expected_revision"],
            command=message["command"],
            args=message["args"],
        )
        if dict(message) != canonical:
            raise LanControllerError("invalid_request", "game_commandが不正です。")
        session = self._require_session(connection_id)
        context = self._rooms[session.room_code]
        if context.game is None:
            raise LanControllerError("game_not_started", "対局はまだ開始されていません。")
        seat = context.lobby.require_player_seat(connection_id) - 1
        command_state = context.command_states.setdefault(
            session.member_id,
            _CommandState(),
        )
        fingerprint = self._command_fingerprint(canonical)
        sequence = canonical["sequence"]
        previous = command_state.records.get(sequence)
        if previous is not None:
            if previous.fingerprint != fingerprint:
                return self._result_with_latest_snapshot(
                    context,
                    session,
                    self._command_result(
                        sequence,
                        accepted=False,
                        revision=context.game_revision,
                        code="sequence_conflict",
                        message="同じsequenceに異なる操作が送られました。",
                    ),
                )
            return self._result_with_latest_snapshot(
                context,
                session,
                dict(previous.response),
            )
        if sequence != command_state.next_sequence:
            code = (
                "sequence_expired"
                if sequence < command_state.next_sequence
                else "sequence_gap"
            )
            return self._result_with_latest_snapshot(
                context,
                session,
                self._command_result(
                    sequence,
                    accepted=False,
                    revision=context.game_revision,
                    code=code,
                    message="操作sequenceが現在のsessionと一致しません。",
                ),
            )

        if canonical["expected_revision"] != context.game_revision:
            response = self._command_result(
                sequence,
                accepted=False,
                revision=context.game_revision,
                code="stale_revision",
                message="盤面が更新されています。最新状態で操作し直してください。",
            )
            self._remember_command(command_state, sequence, fingerprint, response)
            return self._result_with_latest_snapshot(
                context,
                session,
                response,
            )

        random_state_before = context.random_state
        try:
            game_state_before = serialize_game(context.game)
        except Exception:
            response = self._command_result(
                sequence,
                accepted=False,
                revision=context.game_revision,
                code="internal_error",
                message="対局状態を確認できなかったため操作を中止しました。",
            )
            self._remember_command(command_state, sequence, fingerprint, response)
            return (OutboundMessage(connection_id, response),)

        action_error: NetworkActionError | None = None
        internal_error = False
        applied_random_state = random_state_before
        with _RANDOM_LOCK:
            caller_state = random.getstate()
            try:
                if random_state_before is not None:
                    random.setstate(random_state_before)
                try:
                    self._command_applier(
                        context.game,
                        seat,
                        canonical["command"],
                        canonical["args"],
                    )
                except NetworkActionError as exc:
                    action_error = exc
                except Exception:
                    internal_error = True
                else:
                    applied_random_state = random.getstate()
            finally:
                random.setstate(caller_state)

        if action_error is not None or internal_error:
            rollback_ok = self._rollback_game(
                context,
                game_state_before,
                random_state_before,
            )
            if action_error is not None and rollback_ok:
                code = action_error.code
                detail = str(action_error)
            else:
                code = "internal_error"
                detail = "操作を安全に適用できなかったため取り消しました。"
            response = self._command_result(
                sequence,
                accepted=False,
                revision=context.game_revision,
                code=code,
                message=detail,
            )
            self._remember_command(command_state, sequence, fingerprint, response)
            return self._result_with_latest_snapshot(
                context,
                session,
                response,
            )

        next_revision = context.game_revision + 1
        try:
            game_snapshots = self._snapshot_messages_for_game(
                context,
                context.game,
                revision=next_revision,
            )
        except Exception:
            self._rollback_game(
                context,
                game_state_before,
                random_state_before,
            )
            response = self._command_result(
                sequence,
                accepted=False,
                revision=context.game_revision,
                code="internal_error",
                message="最新状態を安全に配信できなかったため操作を取り消しました。",
            )
            self._remember_command(command_state, sequence, fingerprint, response)
            return (OutboundMessage(connection_id, response),)

        context.random_state = applied_random_state
        context.game_revision = next_revision
        response = self._command_result(
            sequence,
            accepted=True,
            revision=context.game_revision,
        )
        self._remember_command(command_state, sequence, fingerprint, response)
        outbound = [OutboundMessage(connection_id, response)]
        outbound.extend(game_snapshots)
        return tuple(outbound)

    def _welcome_and_broadcast(
        self,
        context: _RoomContext,
        connection_id: str,
        grant: MembershipGrant,
    ) -> tuple[OutboundMessage, ...]:
        command_state = context.command_states.get(grant.member_id)
        welcome = OutboundMessage(
            connection_id,
            self._wire(
                "session_welcome",
                room_code=context.lobby.room_code,
                role=grant.role.value,
                seat_index=grant.seat - 1 if grant.seat is not None else None,
                reconnect_token=grant.reconnect_token,
                lobby_revision=context.lobby.revision,
                next_sequence=(
                    command_state.next_sequence
                    if command_state is not None
                    else 0
                ),
            ),
        )
        outbound = [welcome, *self._broadcast_lobby(context)]
        session = self._sessions[connection_id]
        if context.game is not None:
            outbound.append(
                OutboundMessage(connection_id, self._snapshot_for(context, session))
            )
        return tuple(outbound)

    def _broadcast_lobby(self, context: _RoomContext) -> tuple[OutboundMessage, ...]:
        message = self._wire("lobby_snapshot", lobby=context.lobby.public_snapshot())
        return tuple(
            OutboundMessage(session.connection_id, message)
            for session in self._room_sessions(context.lobby.room_code)
        )

    def _broadcast_game(self, context: _RoomContext) -> tuple[OutboundMessage, ...]:
        return self._snapshot_messages_for_game(
            context,
            context.game,
            revision=context.game_revision,
        )

    def _snapshot_messages_for_game(
        self,
        context: _RoomContext,
        game: Any,
        *,
        revision: int,
    ) -> tuple[OutboundMessage, ...]:
        return tuple(
            OutboundMessage(
                session.connection_id,
                self._build_snapshot_for_session(
                    game,
                    session,
                    revision=revision,
                ),
            )
            for session in self._room_sessions(context.lobby.room_code)
        )

    def _result_with_latest_snapshot(
        self,
        context: _RoomContext,
        session: _Session,
        response: dict[str, Any],
    ) -> tuple[OutboundMessage, ...]:
        outbound = [OutboundMessage(session.connection_id, response)]
        if context.game is not None:
            try:
                outbound.append(
                    OutboundMessage(
                        session.connection_id,
                        self._snapshot_for(context, session),
                    )
                )
            except Exception:
                # The command result still lets the client reconcile its
                # sequence.  A reconnect will request a fresh snapshot.
                pass
        return tuple(outbound)

    @staticmethod
    def _rollback_game(
        context: _RoomContext,
        game_state: dict[str, Any],
        random_state: object | None,
    ) -> bool:
        try:
            with _RANDOM_LOCK:
                caller_state = random.getstate()
                try:
                    restore_game(
                        context.game,
                        game_state,
                        runtime_side_effects=False,
                    )
                finally:
                    random.setstate(caller_state)
        except Exception:
            # Never continue serving a partially restored authority object.
            context.game = None
            context.random_state = None
            return False
        context.random_state = random_state
        return True

    def _snapshot_for(self, context: _RoomContext, session: _Session) -> dict[str, Any]:
        return self._build_snapshot_for_session(
            context.game,
            session,
            revision=context.game_revision,
        )

    def _build_snapshot_for_session(
        self,
        game: Any,
        session: _Session,
        *,
        revision: int,
    ) -> dict[str, Any]:
        snapshot = self._snapshot_builder(
            game,
            viewer_player_index=session.seat_index,
            revision=revision,
        )
        if type(snapshot) is not dict:
            raise NetworkProtocolError("state snapshot must be an object")
        result = dict(snapshot)
        result["command_options"] = build_game_command_options(
            game,
            session.seat_index,
        )
        return result

    def _attach(
        self,
        connection_id: str,
        room_code: str,
        grant: MembershipGrant,
    ) -> None:
        self._sessions[connection_id] = _Session(
            connection_id=connection_id,
            room_code=room_code,
            member_id=grant.member_id,
            role=grant.role,
            seat_index=grant.seat - 1 if grant.seat is not None else None,
        )

    def _room_sessions(self, room_code: str) -> tuple[_Session, ...]:
        return tuple(
            sorted(
                (
                    session
                    for session in self._sessions.values()
                    if session.room_code == room_code
                ),
                key=lambda session: session.connection_id,
            )
        )

    def _new_room_code(self) -> str:
        for _ in range(32):
            code = "".join(
                secrets.choice(ROOM_CODE_ALPHABET)
                for _ in range(ROOM_CODE_LENGTH)
            )
            if code not in self._rooms:
                return code
        raise LanControllerError("server_busy", "ルームコードを生成できませんでした。")

    def _room(self, code: Any) -> _RoomContext:
        if type(code) is not str:
            raise LanControllerError("room_not_found", "部屋が見つかりません。")
        context = self._rooms.get(code)
        if context is None:
            raise LanControllerError("room_not_found", "部屋が見つかりません。")
        return context

    def _require_session(self, connection_id: str) -> _Session:
        session = self._sessions.get(connection_id)
        if session is None:
            raise LanControllerError("not_joined", "先にLAN部屋へ参加してください。")
        return session

    def _require_unattached(self, connection_id: str) -> None:
        if connection_id in self._sessions:
            raise LanControllerError("already_joined", "この接続は既に部屋へ参加しています。")

    @staticmethod
    def _player_names(lobby: LobbyRoom) -> tuple[str, ...]:
        members = [
            member
            for member in lobby.public_snapshot()["members"]
            if member["seat"] is not None
        ]
        members.sort(key=lambda member: member["seat"])
        return tuple(member["display_name"] for member in members)

    @staticmethod
    def _remember_command(
        state: _CommandState,
        sequence: int,
        fingerprint: str,
        response: dict[str, Any],
    ) -> None:
        state.records[sequence] = _CommandRecord(fingerprint, dict(response))
        state.next_sequence += 1
        while len(state.records) > MAX_COMMAND_RECORDS:
            state.records.popitem(last=False)

    @staticmethod
    def _command_fingerprint(message: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            message,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(canonical).hexdigest()

    @classmethod
    def _command_result(
        cls,
        sequence: int,
        *,
        accepted: bool,
        revision: int,
        code: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        return cls._wire(
            "game_command_result",
            sequence=sequence,
            accepted=accepted,
            revision=revision,
            code=code,
            message=message,
        )

    @staticmethod
    def _wire(message_type: str, **payload: Any) -> dict[str, Any]:
        return {
            "type": message_type,
            "protocol_version": NETWORK_PROTOCOL_VERSION,
            **payload,
        }

    @staticmethod
    def _validate_envelope(message: Mapping[str, Any]) -> str:
        if type(message) is not dict:
            raise LanControllerError("invalid_request", "LANメッセージはobjectで指定してください。")
        protocol_version = message.get("protocol_version")
        if (
            type(protocol_version) is not int
            or protocol_version != NETWORK_PROTOCOL_VERSION
        ):
            raise LanControllerError("version_mismatch", "通信versionが一致しません。")
        message_type = message.get("type")
        if type(message_type) is not str or not message_type:
            raise LanControllerError("invalid_request", "message typeが不正です。")
        return message_type

    @staticmethod
    def _expect_fields(message: Mapping[str, Any], *fields: str) -> None:
        if set(message) != set(fields):
            raise LanControllerError("invalid_request", "メッセージfieldが不正です。")

    @staticmethod
    def _connection_key(connection_id: str | int) -> str:
        if isinstance(connection_id, bool) or not isinstance(connection_id, (str, int)):
            raise LanControllerError("invalid_connection", "接続IDが不正です。")
        value = str(connection_id)
        if not 1 <= len(value) <= MAX_CONNECTION_ID_LENGTH:
            raise LanControllerError("invalid_connection", "接続IDが不正です。")
        return value

    @staticmethod
    def _safe_nonce(value: Any) -> str | int:
        if type(value) is int and 0 <= value <= 9_007_199_254_740_991:
            return value
        if type(value) is str and len(value) <= 64:
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                pass
            else:
                return value
        raise LanControllerError("invalid_request", "ping nonceが不正です。")

    @staticmethod
    def _public_error(exc: Exception) -> tuple[str, str]:
        if isinstance(exc, LanControllerError):
            return exc.code, str(exc)
        if isinstance(exc, LobbyAuthenticationError):
            return "authentication_failed", "再接続情報を確認できませんでした。"
        if isinstance(exc, LobbyCapacityError):
            return "room_full", "プレイヤー席が満員です。"
        if isinstance(exc, LobbyPermissionError):
            return "forbidden", str(exc)
        if isinstance(exc, LobbyStateError):
            return "invalid_state", str(exc)
        if isinstance(exc, LobbyValidationError):
            return "invalid_request", str(exc)
        return "invalid_request", "LANメッセージが不正です。"


__all__ = (
    "LanControllerError",
    "LanServerController",
    "OutboundMessage",
)
