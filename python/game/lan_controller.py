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
from dataclasses import dataclass, field, replace
import hashlib
import json
import random
import secrets
import threading
import time
from typing import Any, Callable, Mapping

from game.ai_personality import DISRUPTOR, EXPANSION, MIXED, TRADER
from game.controller_authority import (
    CommandRecordAuthority,
    CommandStateAuthority,
    ControllerAuthorityError,
    ControllerRoomAuthority,
    MatchAuthority,
    decode_controller_room_authority,
    encode_controller_room_authority,
)
from game.friend_invitation import (
    FRIEND_INVITATION_ROLES,
    FriendInvitationAuthenticationError,
    FriendInvitationBook,
    FriendInvitationCapacityError,
    FriendInvitationClaim,
    FriendInvitationError,
    FriendInvitationGrant,
    FriendInvitationNotFoundError,
    FriendInvitationSummary,
)
from game.game import CatanGame
from game.lan_lobby import (
    DEFAULT_RESTART_GRACE_SECONDS,
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
from game.network_replay import NetworkReplayStore
from game.network_protocol import (
    NETWORK_PROTOCOL_VERSION,
    NetworkProtocolError,
    build_game_command,
    build_state_snapshot,
)
from game.persistence import restore_game, serialize_game
from game.room_access import RoomAccessError
from game.server_state import (
    RoomAuthorityRecord,
    SQLiteRoomAuthorityStore,
)
from game.variant import variant_uses_hidden_board


MAX_ROOMS = 32
MAX_COMMAND_RECORDS = 128
MAX_CONNECTION_ID_LENGTH = 128
MAX_AI_STEPS_PER_TICK = 8
DEFAULT_AI_STEPS_PER_TICK = 1
DEFAULT_WAITING_ROOM_TTL_MS = 24 * 60 * 60 * 1000
DEFAULT_LIVE_ROOM_TTL_MS = 7 * 24 * 60 * 60 * 1000
MAX_ROOM_TTL_MS = 30 * 24 * 60 * 60 * 1000
MAX_WALL_CLOCK_MS = 253_402_300_799_999
_RANDOM_LOCK = threading.RLock()
_MIXED_AI_PERSONALITIES = (EXPANSION, TRADER, DISRUPTOR)
_MIXED_AI_SEED_SALT = 0x4D49584544
_STATE_STORE_METHODS = (
    "create_room",
    "update_room",
    "get_room",
    "list_rooms",
    "delete_room",
    "delete_expired",
)


class LanControllerError(ValueError):
    """Expected request failure with a stable wire error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ControllerPersistenceError(RuntimeError):
    """Raised when durable room authority cannot be trusted or restored."""


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
    friend_invitations: FriendInvitationBook = field(
        default_factory=FriendInvitationBook.create,
        repr=False,
    )
    match_seed: int = field(
        default_factory=lambda: secrets.randbits(256),
        repr=False,
    )
    game: Any = None
    game_revision: int = 0
    random_state: object | None = None
    command_states: dict[str, _CommandState] = field(default_factory=dict)
    authority_room_id: str | None = None
    authority_generation: int | None = None
    authority_expires_at_ms: int | None = None
    replay_readable: bool = False
    replay_blocked: bool = False


def _default_game_factory(
    settings: RoomSettings,
    player_names: tuple[str, ...],
) -> CatanGame:
    game = CatanGame(
        board_mode=settings.board_mode,
        board_seed=settings.board_seed,
        custom_map=settings.custom_map,
        house_rules=settings.house_rules,
        variant_config=settings.variant,
        ai_player_count=settings.ai_player_count,
        ai_personality_mode=settings.ai_personality_mode,
        headless=True,
    )
    game.player_palette = [
        (display_name, color)
        for display_name, (_default_name, color) in zip(
            player_names,
            game.player_palette,
        )
    ]
    # The constructor initially configures two seats and therefore clamps the
    # requested count.  Restore the room setting before rebuilding all seats.
    game.ai_player_count = settings.ai_player_count
    game.configure_players(
        settings.player_count,
        reset_logs=False,
        schedule_ai=False,
        reset_replay=False,
    )
    game.victory_point_target = settings.victory_target
    game.network_mode = True
    game.reset_match_metrics()
    game.clear_log()
    game.add_log("LAN対戦を開始しました。")
    return game


def _assign_private_mixed_ai_personalities(
    game: Any,
    settings: RoomSettings,
    match_seed: int,
) -> None:
    """Assign a stable secret mixed lineup without changing match RNG."""

    if settings.ai_personality_mode != MIXED:
        return
    ai_players = [
        player
        for player in getattr(game, "players", ())
        if getattr(player, "is_ai", False)
    ]
    personalities = list(_MIXED_AI_PERSONALITIES)
    random.Random(match_seed ^ _MIXED_AI_SEED_SALT).shuffle(personalities)
    for player, personality in zip(ai_players, personalities):
        player.ai_personality = personality


def _default_ai_stepper(game: Any) -> bool:
    ai = getattr(game, "ai", None)
    step = getattr(ai, "step", None)
    return bool(callable(step) and step(game))


def _default_wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


class LanServerController:
    """Authoritative multi-room state machine for a trusted LAN server."""

    def __init__(
        self,
        *,
        game_factory: Callable[
            [RoomSettings, tuple[str, ...]], Any
        ] = _default_game_factory,
        command_applier: Callable[
            [Any, int | None, str, dict[str, Any] | None], bool
        ] = apply_game_command,
        snapshot_builder: Callable[..., dict[str, Any]] = build_state_snapshot,
        ai_stepper: Callable[[Any], bool] = _default_ai_stepper,
        ai_steps_per_tick: int = DEFAULT_AI_STEPS_PER_TICK,
        replay_store: NetworkReplayStore | None = None,
        room_limit: int = MAX_ROOMS,
        state_store: SQLiteRoomAuthorityStore | None = None,
        wall_clock_ms: Callable[[], int] = _default_wall_clock_ms,
        lobby_clock: Callable[[], float] = time.monotonic,
        waiting_room_ttl_ms: int = DEFAULT_WAITING_ROOM_TTL_MS,
        live_room_ttl_ms: int = DEFAULT_LIVE_ROOM_TTL_MS,
        restart_grace_seconds: float = DEFAULT_RESTART_GRACE_SECONDS,
    ) -> None:
        if (
            not callable(game_factory)
            or not callable(command_applier)
            or not callable(snapshot_builder)
            or not callable(ai_stepper)
        ):
            raise ValueError("controller dependencies must be callable")
        if replay_store is not None and not all(
            callable(getattr(replay_store, method, None))
            for method in (
                "capture_game",
                "frame_payload",
                "result_payload",
                "discard_room",
            )
        ):
            raise ValueError("replay_store does not implement the replay contract")
        if (
            type(ai_steps_per_tick) is not int
            or not 1 <= ai_steps_per_tick <= MAX_AI_STEPS_PER_TICK
        ):
            raise ValueError(f"ai_steps_per_tick must be 1..{MAX_AI_STEPS_PER_TICK}")
        if type(room_limit) is not int or not 1 <= room_limit <= 256:
            raise ValueError("room_limit must be 1..256")
        if state_store is not None and not all(
            callable(getattr(state_store, method, None))
            for method in _STATE_STORE_METHODS
        ):
            raise ValueError("state_store does not implement the authority contract")
        if not callable(wall_clock_ms) or not callable(lobby_clock):
            raise ValueError("persistence clocks must be callable")
        for label, value in (
            ("waiting_room_ttl_ms", waiting_room_ttl_ms),
            ("live_room_ttl_ms", live_room_ttl_ms),
        ):
            if type(value) is not int or not 1 <= value <= MAX_ROOM_TTL_MS:
                raise ValueError(f"{label} must be 1..{MAX_ROOM_TTL_MS}")
        if (
            isinstance(restart_grace_seconds, bool)
            or not isinstance(restart_grace_seconds, (int, float))
            or not 0 < float(restart_grace_seconds) <= 7 * 24 * 60 * 60
        ):
            raise ValueError("restart_grace_seconds must be a bounded duration")
        self._game_factory = game_factory
        self._command_applier = command_applier
        self._snapshot_builder = snapshot_builder
        self._ai_stepper = ai_stepper
        self._ai_steps_per_tick = ai_steps_per_tick
        self._replay_store = (
            NetworkReplayStore() if replay_store is None else replay_store
        )
        self._room_limit = room_limit
        self._state_store = state_store
        self._wall_clock_ms = wall_clock_ms
        self._lobby_clock = lobby_clock
        self._waiting_room_ttl_ms = waiting_room_ttl_ms
        self._live_room_ttl_ms = live_room_ttl_ms
        self._restart_grace_seconds = float(restart_grace_seconds)
        self._persistence_failed = False
        self._rooms: dict[str, _RoomContext] = {}
        self._sessions: dict[str, _Session] = {}
        if self._state_store is not None:
            self._restore_persisted_rooms()

    @property
    def room_codes(self) -> tuple[str, ...]:
        return tuple(sorted(self._rooms))

    def _now_ms(self) -> int:
        value = self._wall_clock_ms()
        if type(value) is not int or not 0 <= value <= MAX_WALL_CLOCK_MS:
            raise ControllerPersistenceError("authority wall clock is invalid")
        return value

    @staticmethod
    def _command_authorities(
        states: Mapping[str, _CommandState],
    ) -> tuple[CommandStateAuthority, ...]:
        return tuple(
            CommandStateAuthority(
                member_id=member_id,
                next_sequence=state.next_sequence,
                records=tuple(
                    CommandRecordAuthority(
                        sequence=sequence,
                        fingerprint=record.fingerprint,
                        response=dict(record.response),
                    )
                    for sequence, record in state.records.items()
                ),
            )
            for member_id, state in sorted(states.items())
        )

    def _authority_document(
        self,
        context: _RoomContext,
        *,
        wall_clock_ms: int,
    ) -> dict[str, Any]:
        lobby_document = context.lobby.to_authority_document(
            wall_clock_ms=wall_clock_ms
        )
        match = None
        if context.game is not None:
            if context.random_state is None:
                raise ControllerAuthorityError("live room has no random state")
            match = MatchAuthority(
                match_seed=context.match_seed,
                game_revision=context.game_revision,
                random_state=context.random_state,
                game=serialize_game(context.game),
                command_states=self._command_authorities(context.command_states),
            )
        elif context.command_states:
            raise ControllerAuthorityError("waiting room has command state")
        return encode_controller_room_authority(
            ControllerRoomAuthority(
                lobby=lobby_document,
                match=match,
                friend_invitations=(
                    context.friend_invitations.to_authority_document()
                ),
            )
        )

    def _room_expiry_ms(self, context: _RoomContext, now_ms: int) -> int:
        ttl = (
            self._live_room_ttl_ms
            if context.lobby.phase is RoomPhase.STARTED
            else self._waiting_room_ttl_ms
        )
        expires_at_ms = now_ms + ttl
        if expires_at_ms > MAX_WALL_CLOCK_MS:
            raise ControllerPersistenceError("authority expiry is out of range")
        return expires_at_ms

    def _persistence_unavailable(self) -> LanControllerError:
        return LanControllerError(
            "persistence_unavailable",
            "対局状態を安全に保存できません。サーバーを再起動して再接続してください。",
        )

    def _fail_persistence(self, exc: Exception) -> LanControllerError:
        self._persistence_failed = True
        return self._persistence_unavailable()

    def _persist_new_context(self, context: _RoomContext) -> None:
        if self._state_store is None:
            return
        try:
            now_ms = self._now_ms()
            record = self._state_store.create_room(
                room_code=context.lobby.room_code,
                authority=self._authority_document(
                    context,
                    wall_clock_ms=now_ms,
                ),
                updated_at_ms=now_ms,
                expires_at_ms=self._room_expiry_ms(context, now_ms),
            )
        except Exception as exc:
            raise self._fail_persistence(exc) from exc
        context.authority_room_id = record.room_id
        context.authority_generation = record.generation
        context.authority_expires_at_ms = record.expires_at_ms

    def _persist_existing_context(
        self,
        context: _RoomContext,
        *,
        preserve_expires_at_ms: int | None = None,
    ) -> None:
        if self._state_store is None:
            return
        room_id = context.authority_room_id
        generation = context.authority_generation
        if room_id is None or generation is None:
            raise self._fail_persistence(
                ControllerPersistenceError("room authority identity is missing")
            )
        try:
            now_ms = self._now_ms()
            expires_at_ms = (
                self._room_expiry_ms(context, now_ms)
                if preserve_expires_at_ms is None
                else preserve_expires_at_ms
            )
            record = self._state_store.update_room(
                room_id,
                expected_generation=generation,
                authority=self._authority_document(
                    context,
                    wall_clock_ms=now_ms,
                ),
                updated_at_ms=now_ms,
                expires_at_ms=expires_at_ms,
            )
        except Exception as exc:
            raise self._fail_persistence(exc) from exc
        context.authority_generation = record.generation
        context.authority_expires_at_ms = record.expires_at_ms

    def _delete_persisted_context(self, context: _RoomContext) -> None:
        if self._state_store is None:
            return
        room_id = context.authority_room_id
        generation = context.authority_generation
        if room_id is None or generation is None:
            raise self._fail_persistence(
                ControllerPersistenceError("room authority identity is missing")
            )
        try:
            deleted = self._state_store.delete_room(
                room_id,
                expected_generation=generation,
            )
            if deleted is not True:
                raise ControllerPersistenceError("room authority is missing")
        except Exception as exc:
            raise self._fail_persistence(exc) from exc

    def _restore_persisted_rooms(self) -> None:
        assert self._state_store is not None
        try:
            now_ms = self._now_ms()
            self._state_store.delete_expired(now_ms)
            metadata = self._state_store.list_rooms()
            if len(metadata) > self._room_limit:
                raise ControllerPersistenceError(
                    "persisted room count exceeds the configured limit"
                )
            restored: dict[str, _RoomContext] = {}
            for item in metadata:
                record = self._state_store.get_room(item.room_id)
                if record is None or record.metadata != item:
                    raise ControllerPersistenceError(
                        "persisted room metadata changed during restore"
                    )
                context = self._context_from_record(record, wall_clock_ms=now_ms)
                code = context.lobby.room_code
                if record.room_code != code or code in restored:
                    raise ControllerPersistenceError(
                        "persisted room identity is inconsistent"
                    )
                context.lobby.prune_expired()
                if not context.lobby.has_members:
                    if not self._state_store.delete_room(
                        record.room_id,
                        expected_generation=record.generation,
                    ):
                        raise ControllerPersistenceError(
                            "empty persisted room could not be deleted"
                        )
                    continue
                normalized_authority = self._authority_document(
                    context,
                    wall_clock_ms=now_ms,
                )
                reservation_deadlines = tuple(
                    member["reservation_expires_at_ms"]
                    for member in normalized_authority["lobby"]["members"]
                    if member["reservation_expires_at_ms"] is not None
                )
                normalized_expiry = max(
                    (record.expires_at_ms, *reservation_deadlines)
                )
                normalized = self._state_store.update_room(
                    record.room_id,
                    expected_generation=record.generation,
                    authority=normalized_authority,
                    updated_at_ms=now_ms,
                    expires_at_ms=normalized_expiry,
                )
                context.authority_generation = normalized.generation
                context.authority_expires_at_ms = normalized.expires_at_ms
                restored[code] = context
            self._rooms = restored
            for context in restored.values():
                self._reconcile_restored_replay(context)
        except Exception as exc:
            self._rooms = {}
            self._sessions = {}
            raise ControllerPersistenceError(
                "persisted room authority could not be restored"
            ) from exc

    def _context_from_record(
        self,
        record: RoomAuthorityRecord,
        *,
        wall_clock_ms: int,
    ) -> _RoomContext:
        authority = decode_controller_room_authority(record.authority)
        lobby = LobbyRoom.from_authority_document(
            authority.lobby,
            wall_clock_ms=wall_clock_ms,
            clock=self._lobby_clock,
            restart_grace_seconds=self._restart_grace_seconds,
        )
        friend_invitations = (
            FriendInvitationBook.create()
            if authority.friend_invitations is None
            else FriendInvitationBook.from_authority_document(
                authority.friend_invitations
            )
        )
        context = _RoomContext(
            lobby=lobby,
            friend_invitations=friend_invitations,
            authority_room_id=record.room_id,
            authority_generation=record.generation,
            authority_expires_at_ms=record.expires_at_ms,
        )
        match = authority.match
        if match is None:
            if lobby.phase is not RoomPhase.WAITING:
                raise ControllerPersistenceError(
                    "started lobby has no persisted match"
                )
            return context
        if lobby.phase is not RoomPhase.STARTED:
            raise ControllerPersistenceError("waiting lobby contains a match")

        player_names = self._player_names(lobby)
        with _RANDOM_LOCK:
            caller_state = random.getstate()
            try:
                random.seed(match.match_seed)
                game = self._game_factory(lobby.settings, player_names)
                _assign_private_mixed_ai_personalities(
                    game,
                    lobby.settings,
                    match.match_seed,
                )
                restore_game(game, match.game, runtime_side_effects=False)
            finally:
                random.setstate(caller_state)
        self._validate_restored_game_contract(game, lobby.settings)
        if serialize_game(game) != match.game:
            raise ControllerPersistenceError(
                "persisted game is not canonical after restore"
            )
        stored_names = tuple(player["name"] for player in match.game["players"])
        if stored_names != player_names:
            raise ControllerPersistenceError(
                "persisted game players do not match the lobby"
            )
        seated_member_ids = {
            member["member_id"]
            for member in authority.lobby["members"]
            if member["seat"] is not None
        }
        command_states: dict[str, _CommandState] = {}
        for state in match.command_states:
            if state.member_id not in seated_member_ids:
                raise ControllerPersistenceError(
                    "persisted command owner is not a player"
                )
            command_states[state.member_id] = _CommandState(
                next_sequence=state.next_sequence,
                records=OrderedDict(
                    (
                        record.sequence,
                        _CommandRecord(
                            fingerprint=record.fingerprint,
                            response=dict(record.response),
                        ),
                    )
                    for record in state.records
                ),
            )
        context.match_seed = match.match_seed
        context.game = game
        context.game_revision = match.game_revision
        context.random_state = match.random_state
        context.command_states = command_states
        return context

    @staticmethod
    def _validate_restored_game_contract(game: Any, settings: RoomSettings) -> None:
        """Ensure a valid save cannot override the public room contract."""

        players = tuple(getattr(game, "players", ()))
        expected_ai = (False,) * (
            settings.player_count - settings.ai_player_count
        ) + (True,) * settings.ai_player_count
        actual_ai = tuple(bool(getattr(player, "is_ai", False)) for player in players)
        if (
            len(players) != settings.player_count
            or actual_ai != expected_ai
            or getattr(game, "victory_point_target", None)
            != settings.victory_target
            or getattr(game, "board_mode", None) != settings.board_mode
            or getattr(game, "board_seed", None) != settings.board_seed
            or getattr(game, "custom_map_spec", None) != settings.custom_map
            or getattr(game, "house_rules", None) != settings.house_rules
            or getattr(game, "variant_config", None) != settings.variant
            or getattr(game, "ai_player_count", None) != settings.ai_player_count
            or getattr(game, "ai_personality_mode", None)
            != settings.ai_personality_mode
        ):
            raise ControllerPersistenceError(
                "persisted game does not match the room settings"
            )

    def handle(
        self,
        connection_id: str | int,
        message: Mapping[str, Any],
        *,
        protected_room_access_allowed: bool = False,
        rotate_reconnect_token: bool = False,
    ) -> tuple[OutboundMessage, ...]:
        """Handle one decoded request and return messages to send."""

        connection_key = self._connection_key(connection_id)
        try:
            if type(protected_room_access_allowed) is not bool:
                raise LanControllerError(
                    "invalid_transport_context",
                    "通信の保護状態を確認できません。",
                )
            if type(rotate_reconnect_token) is not bool:
                raise LanControllerError(
                    "invalid_transport_context",
                    "再接続資格の更新状態を確認できません。",
                )
            message_type = self._validate_envelope(message)
            if rotate_reconnect_token and message_type != "reconnect_room":
                raise LanControllerError(
                    "invalid_transport_context",
                    "再接続資格の更新対象が不正です。",
                )
            if self._persistence_failed and message_type != "ping":
                raise self._persistence_unavailable()
            if message_type == "create_room":
                return self._create_room(
                    connection_key,
                    message,
                    protected_room_access_allowed=protected_room_access_allowed,
                )
            if message_type == "join_room":
                return self._join_room(
                    connection_key,
                    message,
                    protected_room_access_allowed=protected_room_access_allowed,
                )
            if message_type == "reconnect_room":
                return self._reconnect_room(
                    connection_key,
                    message,
                    rotate_reconnect_token=rotate_reconnect_token,
                )
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
            raise LanControllerError(
                "unsupported_message", "未対応のLANメッセージです。"
            )
        except (LanControllerError, LobbyError, NetworkProtocolError) as exc:
            code, detail = self._public_error(exc)
            outbound = [
                OutboundMessage(
                    connection_key,
                    self._wire("request_error", code=code, message=detail),
                )
            ]
            if code == "persistence_unavailable" and self._persistence_failed:
                outbound.extend(self._fence_all_rooms())
            return tuple(outbound)
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
        session = self._sessions.get(connection_key)
        if session is None:
            return ()
        if self._persistence_failed:
            self._sessions.pop(connection_key, None)
            return ()
        context = self._rooms.get(session.room_code)
        if context is None:
            self._sessions.pop(connection_key, None)
            return ()
        lobby_before = deepcopy(context.lobby)
        try:
            context.lobby.disconnect(connection_key)
        except LobbyError:
            self._sessions.pop(connection_key, None)
            return ()
        try:
            outbound = self._broadcast_lobby(context)
            self._persist_existing_context(context)
        except LanControllerError:
            context.lobby = lobby_before
            return self._fence_all_rooms()
        self._sessions.pop(connection_key, None)
        return outbound

    def tick(self) -> tuple[OutboundMessage, ...]:
        """Maintain rooms and advance authoritative AI by bounded steps."""

        if self._persistence_failed:
            return ()
        outbound = []
        try:
            for room_code, context in tuple(self._rooms.items()):
                invitations_before = deepcopy(context.friend_invitations)
                if context.friend_invitations.prune_expired(
                    now_ms=self._now_ms()
                ):
                    try:
                        self._persist_existing_context(
                            context,
                            preserve_expires_at_ms=(
                                context.authority_expires_at_ms
                            ),
                        )
                    except Exception:
                        context.friend_invitations = invitations_before
                        raise
                if context.lobby.has_expired_player_reservation():
                    closed = self._wire(
                        "room_closed",
                        code="player_reconnect_expired",
                        message=(
                            "プレイヤーの再接続期限が切れたため、LAN対戦を終了しました。"
                        ),
                    )
                    sessions = self._room_sessions(room_code)
                    self._delete_persisted_context(context)
                    outbound.extend(
                        OutboundMessage(session.connection_id, closed)
                        for session in sessions
                    )
                    for session in sessions:
                        self._sessions.pop(session.connection_id, None)
                    self._rooms.pop(room_code, None)
                    self._discard_replay(room_code)
                    continue
                lobby_before = deepcopy(context.lobby)
                pruned = context.lobby.prune_expired()
                if pruned:
                    if not context.lobby.has_members:
                        self._delete_persisted_context(context)
                        self._rooms.pop(room_code, None)
                        for session in self._room_sessions(room_code):
                            self._sessions.pop(session.connection_id, None)
                        self._discard_replay(room_code)
                        continue
                    try:
                        snapshots = self._broadcast_lobby(context)
                        self._persist_existing_context(context)
                    except Exception:
                        context.lobby = lobby_before
                        raise
                    outbound.extend(snapshots)
                outbound.extend(self._advance_ai(context))
        except LanControllerError:
            return (*outbound, *self._fence_all_rooms())
        return tuple(outbound)

    def snapshot_for_connection(self, connection_id: str | int) -> dict[str, Any]:
        """Return the latest viewer-filtered game snapshot for diagnostics/UI."""

        session = self._require_session(self._connection_key(connection_id))
        context = self._rooms[session.room_code]
        if context.game is None:
            raise LanControllerError(
                "game_not_started", "対局はまだ開始されていません。"
            )
        return self._snapshot_for(context, session)

    def replay_frame_for_connection(
        self,
        connection_id: str | int,
        frame_index: int,
    ) -> dict[str, Any]:
        """Return one read-only replay frame for the authenticated viewer."""

        session = self._require_session(self._connection_key(connection_id))
        context = self._rooms[session.room_code]
        if context.game is None:
            raise LanControllerError(
                "game_not_started", "対局はまだ開始されていません。"
            )
        self._bind_replay_for_read(context)
        return self._replay_store.frame_payload(
            session.room_code,
            viewer_player_index=session.seat_index,
            frame_index=frame_index,
        )

    def match_result_for_connection(
        self,
        connection_id: str | int,
    ) -> dict[str, Any]:
        """Return the public result and replay manifest for one room member."""

        session = self._require_session(self._connection_key(connection_id))
        context = self._rooms[session.room_code]
        if context.game is None:
            raise LanControllerError(
                "game_not_started", "対局はまだ開始されていません。"
            )
        self._bind_replay_for_read(context)
        return self._replay_store.result_payload(
            session.room_code,
            viewer_player_index=session.seat_index,
        )

    def issue_friend_invitation(
        self,
        connection_id: str | int,
        *,
        role: str,
        ttl_seconds: int,
    ) -> FriendInvitationGrant:
        """Issue one private, role-scoped bearer for the connected host."""

        connection_key = self._connection_key(connection_id)
        context: _RoomContext | None = None
        invitations_before: FriendInvitationBook | None = None
        try:
            if self._persistence_failed:
                raise self._persistence_unavailable()
            session = self._require_session(connection_key)
            context = self._rooms[session.room_code]
            context.lobby.require_host(connection_key)
            if role not in FRIEND_INVITATION_ROLES:
                raise LanControllerError(
                    "invalid_request", "招待の参加方法が不正です。"
                )
            if role == MemberRole.PLAYER.value:
                if context.lobby.phase is not RoomPhase.WAITING:
                    raise LanControllerError(
                        "invalid_state",
                        "対局開始後はプレイヤーを招待できません。",
                    )
                if context.lobby.is_full:
                    raise LanControllerError(
                        "room_full", "プレイヤー席が満員です。"
                    )
            invitations_before = deepcopy(context.friend_invitations)
            grant = context.friend_invitations.issue(
                role,
                now_ms=self._now_ms(),
                ttl_seconds=ttl_seconds,
            )
            self._persist_existing_context(context)
            return grant
        except (LobbyError, FriendInvitationError) as exc:
            if context is not None and invitations_before is not None:
                context.friend_invitations = invitations_before
            code, detail = self._public_error(exc)
            raise LanControllerError(code, detail) from exc
        except Exception:
            if context is not None and invitations_before is not None:
                context.friend_invitations = invitations_before
            raise

    def inspect_friend_invitation(
        self,
        room_code: str,
        invite_token: object,
    ) -> FriendInvitationClaim:
        """Return proven non-secret invite scope without consuming the bearer."""

        try:
            if self._persistence_failed:
                raise self._persistence_unavailable()
            context = self._room(room_code)
            return context.friend_invitations.inspect(
                invite_token,
                now_ms=self._now_ms(),
            )
        except FriendInvitationAuthenticationError as exc:
            raise LanControllerError(
                "authentication_failed", "認証情報を確認できませんでした。"
            ) from exc
        except LanControllerError as exc:
            if exc.code == "room_not_found":
                raise LanControllerError(
                    "authentication_failed", "認証情報を確認できませんでした。"
                ) from exc
            raise
        except FriendInvitationError as exc:
            raise LanControllerError(
                "invalid_request", "招待情報が不正です。"
            ) from exc

    def list_friend_invitations(
        self,
        connection_id: str | int,
    ) -> tuple[FriendInvitationSummary, ...]:
        """Return the host's active token-free invitations in canonical order."""

        connection_key = self._connection_key(connection_id)
        context: _RoomContext | None = None
        invitations_before: FriendInvitationBook | None = None
        try:
            if self._persistence_failed:
                raise self._persistence_unavailable()
            session = self._require_session(connection_key)
            context = self._rooms[session.room_code]
            context.lobby.require_host(connection_key)
            invitations_before = deepcopy(context.friend_invitations)
            count_before = context.friend_invitations.invitation_count
            invitations = context.friend_invitations.list_active(
                now_ms=self._now_ms()
            )
            if context.friend_invitations.invitation_count != count_before:
                self._persist_existing_context(
                    context,
                    preserve_expires_at_ms=context.authority_expires_at_ms,
                )
            return invitations
        except (LobbyError, FriendInvitationError) as exc:
            if context is not None and invitations_before is not None:
                context.friend_invitations = invitations_before
            code, detail = self._public_error(exc)
            raise LanControllerError(code, detail) from exc
        except Exception:
            if context is not None and invitations_before is not None:
                context.friend_invitations = invitations_before
            raise

    def revoke_friend_invitation(
        self,
        connection_id: str | int,
        *,
        invitation_id: str,
    ) -> FriendInvitationSummary:
        """Atomically revoke one unused invitation for the connected host."""

        connection_key = self._connection_key(connection_id)
        context: _RoomContext | None = None
        invitations_before: FriendInvitationBook | None = None
        try:
            if self._persistence_failed:
                raise self._persistence_unavailable()
            session = self._require_session(connection_key)
            context = self._rooms[session.room_code]
            context.lobby.require_host(connection_key)
            invitations_before = deepcopy(context.friend_invitations)
            revoked = context.friend_invitations.revoke(
                invitation_id,
                now_ms=self._now_ms(),
            )
            self._persist_existing_context(
                context,
                preserve_expires_at_ms=context.authority_expires_at_ms,
            )
            return revoked
        except (LobbyError, FriendInvitationError) as exc:
            if context is not None and invitations_before is not None:
                context.friend_invitations = invitations_before
            code, detail = self._public_error(exc)
            raise LanControllerError(code, detail) from exc
        except Exception:
            if context is not None and invitations_before is not None:
                context.friend_invitations = invitations_before
            raise

    def revoke_all_friend_invitations(
        self,
        connection_id: str | int,
    ) -> int:
        """Atomically revoke every unused invitation for the connected host."""

        connection_key = self._connection_key(connection_id)
        context: _RoomContext | None = None
        invitations_before: FriendInvitationBook | None = None
        try:
            if self._persistence_failed:
                raise self._persistence_unavailable()
            session = self._require_session(connection_key)
            context = self._rooms[session.room_code]
            context.lobby.require_host(connection_key)
            invitations_before = deepcopy(context.friend_invitations)
            count_before = context.friend_invitations.invitation_count
            revoked_count = context.friend_invitations.revoke_all(
                now_ms=self._now_ms()
            )
            if count_before:
                self._persist_existing_context(
                    context,
                    preserve_expires_at_ms=context.authority_expires_at_ms,
                )
            return revoked_count
        except (LobbyError, FriendInvitationError) as exc:
            if context is not None and invitations_before is not None:
                context.friend_invitations = invitations_before
            code, detail = self._public_error(exc)
            raise LanControllerError(code, detail) from exc
        except Exception:
            if context is not None and invitations_before is not None:
                context.friend_invitations = invitations_before
            raise

    def join_room_with_friend_invitation(
        self,
        connection_id: str | int,
        *,
        room_code: str,
        display_name: str,
        invite_token: object,
        expected_room_id: str,
    ) -> tuple[OutboundMessage, ...]:
        """Atomically consume a proven invite and create its server-owned role."""

        connection_key = self._connection_key(connection_id)
        context: _RoomContext | None = None
        lobby_before: LobbyRoom | None = None
        invitations_before: FriendInvitationBook | None = None
        try:
            if self._persistence_failed:
                raise self._persistence_unavailable()
            self._require_unattached(connection_key)
            try:
                context = self._room(room_code)
            except LanControllerError as exc:
                if exc.code == "room_not_found":
                    raise FriendInvitationAuthenticationError(
                        "friend invitation could not be verified"
                    ) from exc
                raise
            claim = context.friend_invitations.inspect(
                invite_token,
                now_ms=self._now_ms(),
            )
            if (
                type(expected_room_id) is not str
                or not secrets.compare_digest(claim.room_id, expected_room_id)
            ):
                raise FriendInvitationAuthenticationError(
                    "friend invitation could not be verified"
                )
            lobby_before = deepcopy(context.lobby)
            invitations_before = deepcopy(context.friend_invitations)
            consumed = context.friend_invitations.consume(
                invite_token,
                now_ms=self._now_ms(),
            )
            if consumed.role == MemberRole.PLAYER.value:
                grant = context.lobby.join_player_authorized(
                    display_name=display_name,
                    connection_id=connection_key,
                )
            elif consumed.role == MemberRole.SPECTATOR.value:
                grant = context.lobby.join_spectator_authorized(
                    display_name=display_name,
                    connection_id=connection_key,
                )
            else:  # pragma: no cover - domain validation establishes this.
                raise FriendInvitationError("friend invitation role is invalid")
            self._attach(connection_key, context.lobby.room_code, grant)
            outbound = self._welcome_and_broadcast(
                context,
                connection_key,
                grant,
            )
            self._persist_existing_context(context)
            return outbound
        except (LobbyError, FriendInvitationError) as exc:
            self._sessions.pop(connection_key, None)
            if context is not None and lobby_before is not None:
                context.lobby = lobby_before
            if context is not None and invitations_before is not None:
                context.friend_invitations = invitations_before
            code, detail = self._public_error(exc)
            raise LanControllerError(code, detail) from exc
        except Exception:
            self._sessions.pop(connection_key, None)
            if context is not None and lobby_before is not None:
                context.lobby = lobby_before
            if context is not None and invitations_before is not None:
                context.friend_invitations = invitations_before
            raise

    def _create_room(
        self,
        connection_id: str,
        message: Mapping[str, Any],
        *,
        protected_room_access_allowed: bool,
    ) -> tuple[OutboundMessage, ...]:
        fields = ["type", "protocol_version", "display_name", "settings"]
        if "passphrase" in message:
            fields.append("passphrase")
        self._expect_fields(message, *fields)
        if "passphrase" in message and not protected_room_access_allowed:
            raise LanControllerError(
                "secure_transport_required",
                "部屋パスフレーズは保護された通信または同一端末でのみ利用できます。",
            )
        self._require_unattached(connection_id)
        if len(self._rooms) >= self._room_limit:
            raise LanControllerError(
                "server_full", "このLANサーバーは部屋数の上限です。"
            )
        raw_settings = message["settings"]
        if type(raw_settings) is not dict:
            raise LanControllerError("invalid_request", "settingsが不正です。")
        required_settings = {
            "player_count",
            "victory_target",
            "board_mode",
            "board_seed",
        }
        optional_settings = {
            "ai_player_count",
            "ai_personality_mode",
            "custom_map",
            "house_rules",
            "variant",
        }
        if not required_settings.issubset(raw_settings) or not set(
            raw_settings
        ).issubset(required_settings | optional_settings):
            raise LanControllerError(
                "invalid_request",
                "settingsのfieldが不正です。",
            )
        settings = RoomSettings(**raw_settings)
        if variant_uses_hidden_board(settings.variant):
            # The submitted seed must not let any participant reconstruct the
            # fogged terrain.  Keep the authority seed secret for this mode.
            settings = replace(settings, board_seed=secrets.randbits(52))
        code = self._new_room_code()
        try:
            lobby, grant = LobbyRoom.create(
                settings,
                host_name=message["display_name"],
                connection_id=connection_id,
                clock=self._lobby_clock,
                code_generator=lambda: code,
                passphrase=message.get("passphrase"),
            )
        except RoomAccessError as exc:
            raise LanControllerError(
                "invalid_passphrase",
                "部屋パスフレーズは推測されにくい15〜64文字で指定してください。",
            ) from exc
        context = _RoomContext(lobby=lobby)
        self._rooms[code] = context
        self._attach(connection_id, code, grant)
        try:
            outbound = self._welcome_and_broadcast(context, connection_id, grant)
            self._persist_new_context(context)
            return outbound
        except Exception:
            self._sessions.pop(connection_id, None)
            self._rooms.pop(code, None)
            raise

    def _join_room(
        self,
        connection_id: str,
        message: Mapping[str, Any],
        *,
        protected_room_access_allowed: bool,
    ) -> tuple[OutboundMessage, ...]:
        fields = [
            "type",
            "protocol_version",
            "room_code",
            "display_name",
            "role",
        ]
        if "passphrase" in message:
            fields.append("passphrase")
        self._expect_fields(message, *fields)
        self._require_unattached(connection_id)
        context = self._room(message["room_code"])
        if (
            "passphrase" in message or context.lobby.passphrase_required
        ) and not protected_room_access_allowed:
            raise LanControllerError(
                "secure_transport_required",
                "部屋パスフレーズは保護された通信または同一端末でのみ利用できます。",
            )
        lobby_before = deepcopy(context.lobby)
        role = message["role"]
        if role == MemberRole.PLAYER.value:
            grant = context.lobby.join_player(
                display_name=message["display_name"],
                connection_id=connection_id,
                passphrase=message.get("passphrase"),
            )
        elif role == MemberRole.SPECTATOR.value:
            grant = context.lobby.join_spectator(
                display_name=message["display_name"],
                connection_id=connection_id,
                passphrase=message.get("passphrase"),
            )
        else:
            raise LanControllerError("invalid_request", "参加roleが不正です。")
        self._attach(connection_id, context.lobby.room_code, grant)
        try:
            outbound = self._welcome_and_broadcast(context, connection_id, grant)
            self._persist_existing_context(context)
            return outbound
        except Exception:
            self._sessions.pop(connection_id, None)
            context.lobby = lobby_before
            raise

    def _reconnect_room(
        self,
        connection_id: str,
        message: Mapping[str, Any],
        *,
        rotate_reconnect_token: bool = False,
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
        try:
            reconnect = (
                context.lobby.reconnect_rotating
                if rotate_reconnect_token
                else context.lobby.reconnect
            )
            grant = reconnect(
                reconnect_token=message["reconnect_token"],
                connection_id=connection_id,
            )
            self._attach(connection_id, context.lobby.room_code, grant)
            outbound = self._welcome_and_broadcast(context, connection_id, grant)
            self._persist_existing_context(context)
            return outbound
        except Exception:
            self._sessions.pop(connection_id, None)
            context.lobby = lobby_before
            raise

    def confirm_reconnect_token(
        self,
        connection_id: str | int,
        room_code: str,
        reconnect_token: str,
    ) -> tuple[OutboundMessage, ...]:
        """Confirm one trusted Web cookie and revoke its predecessor.

        This is intentionally not a wire message.  It uses the same durable
        room CAS and rollback/fencing semantics as other controller mutations.
        """

        connection_key = self._connection_key(connection_id)
        context: _RoomContext | None = None
        lobby_before: LobbyRoom | None = None
        try:
            if self._persistence_failed:
                raise self._persistence_unavailable()
            session = self._require_session(connection_key)
            if session.room_code != room_code:
                raise LobbyAuthenticationError(
                    "invalid or expired reconnect token"
                )
            context = self._rooms[session.room_code]
            lobby_before = deepcopy(context.lobby)
            changed = context.lobby.confirm_reconnect_rotation(
                connection_id=connection_key,
                reconnect_token=reconnect_token,
            )
            if changed:
                self._persist_existing_context(context)
            return (
                OutboundMessage(
                    connection_key,
                    self._wire("resume_confirmed"),
                ),
            )
        except (LanControllerError, LobbyError, NetworkProtocolError) as exc:
            if context is not None and lobby_before is not None:
                context.lobby = lobby_before
            code, detail = self._public_error(exc)
            outbound = [
                OutboundMessage(
                    connection_key,
                    self._wire("request_error", code=code, message=detail),
                )
            ]
            if code == "persistence_unavailable" and self._persistence_failed:
                outbound.extend(self._fence_all_rooms())
            return tuple(outbound)
        except Exception:
            if context is not None and lobby_before is not None:
                context.lobby = lobby_before
            return (
                OutboundMessage(
                    connection_key,
                    self._wire(
                        "request_error",
                        code="internal_error",
                        message="再接続情報を確認できませんでした。",
                    ),
                ),
            )

    def _set_ready(
        self,
        connection_id: str,
        message: Mapping[str, Any],
    ) -> tuple[OutboundMessage, ...]:
        self._expect_fields(message, "type", "protocol_version", "ready")
        session = self._require_session(connection_id)
        context = self._rooms[session.room_code]
        lobby_before = deepcopy(context.lobby)
        revision_before = context.lobby.revision
        try:
            context.lobby.set_ready(connection_id, message["ready"])
            outbound = self._broadcast_lobby(context)
            if context.lobby.revision != revision_before:
                self._persist_existing_context(context)
            return outbound
        except Exception:
            context.lobby = lobby_before
            raise

    def _leave_room(
        self,
        connection_id: str,
        message: Mapping[str, Any],
    ) -> tuple[OutboundMessage, ...]:
        """Remove an intentional lobby departure without reserving its seat."""

        self._expect_fields(message, "type", "protocol_version")
        session = self._require_session(connection_id)
        context = self._rooms[session.room_code]
        if context.lobby.phase is RoomPhase.STARTED and session.seat_index is not None:
            closed = self._wire(
                "room_closed",
                code="player_left",
                message="プレイヤーが退出したため、LAN対戦を終了しました。",
            )
            sessions = self._room_sessions(session.room_code)
            self._delete_persisted_context(context)
            for room_session in sessions:
                self._sessions.pop(room_session.connection_id, None)
            self._rooms.pop(session.room_code, None)
            self._discard_replay(session.room_code)
            return tuple(
                OutboundMessage(room_session.connection_id, closed)
                for room_session in sessions
            )
        lobby_before = deepcopy(context.lobby)
        try:
            context.lobby.leave(connection_id)
            if not context.lobby.has_members:
                self._delete_persisted_context(context)
                outbound: tuple[OutboundMessage, ...] = ()
            else:
                outbound = self._broadcast_lobby(context)
                self._persist_existing_context(context)
        except Exception:
            context.lobby = lobby_before
            raise
        self._sessions.pop(connection_id, None)
        if not context.lobby.has_members:
            self._rooms.pop(session.room_code, None)
            self._discard_replay(session.room_code)
        return outbound

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
                    _assign_private_mixed_ai_personalities(
                        game,
                        context.lobby.settings,
                        context.match_seed,
                    )
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
        lobby_before = deepcopy(context.lobby)
        game_before = context.game
        random_state_before = context.random_state
        revision_before = context.game_revision
        try:
            context.lobby.start(connection_id)
            context.game = game
            context.random_state = match_random_state
            context.game_revision = 0
            outbound = list(self._broadcast_lobby(context))
            outbound.extend(game_snapshots)
            self._persist_existing_context(context)
        except Exception:
            context.lobby = lobby_before
            context.game = game_before
            context.random_state = random_state_before
            context.game_revision = revision_before
            raise
        self._capture_replay(context)
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
            raise LanControllerError(
                "game_not_started", "対局はまだ開始されていません。"
            )
        seat = context.lobby.require_player_seat(connection_id) - 1
        command_state_existed = session.member_id in context.command_states
        command_state = context.command_states.setdefault(
            session.member_id,
            _CommandState(),
        )
        command_state_before = deepcopy(command_state)

        def restore_command_cursor() -> None:
            if command_state_existed:
                context.command_states[session.member_id] = deepcopy(
                    command_state_before
                )
            else:
                context.command_states.pop(session.member_id, None)

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
            try:
                self._persist_existing_context(context)
            except Exception:
                restore_command_cursor()
                raise
            return self._result_with_latest_snapshot(
                context,
                session,
                response,
            )

        random_state_before = context.random_state
        revision_before = context.game_revision
        try:
            game_state_before = serialize_game(context.game)
        except Exception as exc:
            if self._state_store is not None:
                restore_command_cursor()
                self._persistence_failed = True
                raise self._persistence_unavailable() from exc
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
            if not rollback_ok and self._state_store is not None:
                restore_command_cursor()
                self._persistence_failed = True
                raise self._persistence_unavailable()
            response = self._command_result(
                sequence,
                accepted=False,
                revision=context.game_revision,
                code=code,
                message=detail,
            )
            self._remember_command(command_state, sequence, fingerprint, response)
            try:
                self._persist_existing_context(context)
            except Exception:
                restore_command_cursor()
                raise
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
        except Exception as exc:
            rollback_ok = self._rollback_game(
                context,
                game_state_before,
                random_state_before,
            )
            if not rollback_ok and self._state_store is not None:
                restore_command_cursor()
                self._persistence_failed = True
                raise self._persistence_unavailable() from exc
            response = self._command_result(
                sequence,
                accepted=False,
                revision=context.game_revision,
                code="internal_error",
                message="最新状態を安全に配信できなかったため操作を取り消しました。",
            )
            self._remember_command(command_state, sequence, fingerprint, response)
            try:
                self._persist_existing_context(context)
            except Exception:
                restore_command_cursor()
                raise
            return (OutboundMessage(connection_id, response),)

        context.random_state = applied_random_state
        context.game_revision = next_revision
        response = self._command_result(
            sequence,
            accepted=True,
            revision=context.game_revision,
        )
        self._remember_command(command_state, sequence, fingerprint, response)
        try:
            self._persist_existing_context(context)
        except Exception:
            rollback_ok = self._rollback_game(
                context,
                game_state_before,
                random_state_before,
            )
            context.game_revision = revision_before
            restore_command_cursor()
            if not rollback_ok:
                self._persistence_failed = True
            raise
        self._capture_replay(context)
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
                    command_state.next_sequence if command_state is not None else 0
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

    def _advance_ai(self, context: _RoomContext) -> tuple[OutboundMessage, ...]:
        """Apply a small, observable AI slice without exposing process RNG.

        The default is one decision per server tick so LAN and browser clients
        can display the AI's latest status/event instead of jumping over an
        entire turn.  The configurable upper bound exists for headless tests
        and future accelerated spectator modes.
        """

        outbound: list[OutboundMessage] = []
        for _step_index in range(self._ai_steps_per_tick):
            game = context.game
            if game is None or not self._is_ai_action_pending(game):
                break
            random_state_before = context.random_state
            revision_before = context.game_revision
            try:
                game_state_before = serialize_game(game)
            except Exception as exc:
                if self._state_store is not None:
                    self._persistence_failed = True
                    raise self._persistence_unavailable() from exc
                break

            changed = False
            applied_random_state = random_state_before
            with _RANDOM_LOCK:
                caller_state = random.getstate()
                try:
                    if random_state_before is not None:
                        random.setstate(random_state_before)
                    try:
                        changed = self._ai_stepper(game) is True
                    except Exception:
                        changed = False
                    else:
                        if changed:
                            applied_random_state = random.getstate()
                finally:
                    random.setstate(caller_state)

            if not changed:
                rollback_ok = self._rollback_game(
                    context,
                    game_state_before,
                    random_state_before,
                )
                if not rollback_ok and self._state_store is not None:
                    self._persistence_failed = True
                    raise self._persistence_unavailable()
                break

            next_revision = context.game_revision + 1
            try:
                snapshots = self._snapshot_messages_for_game(
                    context,
                    game,
                    revision=next_revision,
                )
            except Exception as exc:
                rollback_ok = self._rollback_game(
                    context,
                    game_state_before,
                    random_state_before,
                )
                if not rollback_ok and self._state_store is not None:
                    self._persistence_failed = True
                    raise self._persistence_unavailable() from exc
                break
            context.random_state = applied_random_state
            context.game_revision = next_revision
            try:
                self._persist_existing_context(context)
            except Exception:
                rollback_ok = self._rollback_game(
                    context,
                    game_state_before,
                    random_state_before,
                )
                context.game_revision = revision_before
                if not rollback_ok:
                    self._persistence_failed = True
                raise
            self._capture_replay(context)
            outbound.extend(snapshots)
        return tuple(outbound)

    def _capture_replay(
        self,
        context: _RoomContext,
        *,
        restored: bool = False,
    ) -> bool:
        """Best-effort archive capture that can never roll back live play."""

        if context.game is None or context.replay_blocked:
            return False
        try:
            self._bind_replay_context(context)
            capture = (
                getattr(self._replay_store, "capture_restored_game", None)
                if restored
                else self._replay_store.capture_game
            )
            if not callable(capture):
                return False
            capture(
                context.lobby.room_code,
                context.game,
                revision=context.game_revision,
            )
        except Exception:
            # If this room has never established a replay that matches its
            # current authority, later revisions must not be appended to a
            # stale same-code archive and accidentally make it readable.
            if not context.replay_readable:
                context.replay_blocked = True
            return False
        context.replay_readable = True
        return True

    def _reconcile_restored_replay(self, context: _RoomContext) -> None:
        """Reconcile independently persisted game and replay authorities.

        A replay is optional, but a stale viewer-specific archive must never
        become readable through a current membership.  Rooms start disabled
        and are enabled only after a successful same-state refresh or restart
        boundary capture.  A waiting room with frames is an impossible pairing
        and remains replay-blocked for its lifetime.  A replay ahead of game
        authority is a stronger durability violation and aborts startup.
        """

        context.replay_readable = False
        context.replay_blocked = False
        try:
            self._bind_replay_context(context)
            latest_revision = getattr(
                self._replay_store,
                "latest_revision",
                None,
            )
            archived_revision = (
                latest_revision(context.lobby.room_code)
                if callable(latest_revision)
                else None
            )
        except Exception:
            context.replay_blocked = True
            return

        if context.game is None:
            if archived_revision is not None:
                context.replay_blocked = True
            return
        if (
            archived_revision is not None
            and archived_revision > context.game_revision
        ):
            raise ControllerPersistenceError(
                "persisted replay is ahead of room authority"
            )
        self._capture_replay(context, restored=True)

    def _bind_replay_context(self, context: _RoomContext) -> None:
        """Bind a durable archive to the room's non-reusable authority ID.

        In-memory replay stores need only the short room code and therefore do
        not implement ``bind_room``.  A durable adapter must bind the code to
        the stable authority row ID before reading or appending frames so a
        later room that happens to reuse the same six-character code cannot
        inherit another match's private replay variants.
        """

        bind_room = getattr(self._replay_store, "bind_room", None)
        if not callable(bind_room):
            return
        room_id = context.authority_room_id
        if not isinstance(room_id, str) or not room_id:
            raise ControllerPersistenceError(
                "durable replay requires a persisted room identity"
            )
        bind_room(context.lobby.room_code, room_id)

    def _bind_replay_for_read(self, context: _RoomContext) -> None:
        """Fail closed before reading an archive through a reusable room code."""

        if context.replay_blocked or not context.replay_readable:
            raise LanControllerError(
                "replay_unavailable",
                "リプレイを安全に読み込めませんでした。",
            )
        try:
            self._bind_replay_context(context)
        except Exception as exc:
            raise LanControllerError(
                "replay_unavailable",
                "リプレイを安全に読み込めませんでした。",
            ) from exc

    def _discard_replay(self, room_code: str) -> None:
        try:
            self._replay_store.discard_room(room_code)
        except Exception:
            pass

    def _fence_all_rooms(self) -> tuple[OutboundMessage, ...]:
        """Stop every in-memory room after an uncertain durable commit."""

        closed = self._wire(
            "room_closed",
            code="persistence_unavailable",
            message=(
                "対局状態を安全に保存できません。サーバー再起動後に"
                "再接続してください。"
            ),
        )
        sessions = tuple(
            sorted(self._sessions.values(), key=lambda item: item.connection_id)
        )
        room_codes = tuple(self._rooms)
        self._sessions.clear()
        self._rooms.clear()
        for room_code in room_codes:
            self._discard_replay(room_code)
        return tuple(
            OutboundMessage(session.connection_id, closed) for session in sessions
        )

    @staticmethod
    def _is_ai_action_pending(game: Any) -> bool:
        is_locked = getattr(game, "is_ai_input_locked", None)
        if not callable(is_locked):
            return False
        try:
            return is_locked() is True
        except Exception:
            return False

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
                secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH)
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
            raise LanControllerError(
                "already_joined", "この接続は既に部屋へ参加しています。"
            )

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
            raise LanControllerError(
                "invalid_request", "LANメッセージはobjectで指定してください。"
            )
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
        if isinstance(exc, FriendInvitationAuthenticationError):
            return "authentication_failed", "認証情報を確認できませんでした。"
        if isinstance(exc, FriendInvitationCapacityError):
            return "invite_limit", "有効な招待が上限に達しています。"
        if isinstance(exc, FriendInvitationNotFoundError):
            return "invitation_not_found", "招待を確認できませんでした。"
        if isinstance(exc, FriendInvitationError):
            return "invalid_request", "招待情報が不正です。"
        if isinstance(exc, LobbyAuthenticationError):
            return "authentication_failed", "認証情報を確認できませんでした。"
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
