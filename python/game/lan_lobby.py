"""Pure domain model for a small trusted-LAN CATAN lobby.

The module deliberately contains no sockets, threads, Pygame objects, or JSON
transport code.  A future desktop server or Web API can therefore own one
``LobbyRoom`` and translate commands/snapshots without duplicating lobby rules.

Reconnect credentials are returned only through private membership grants.
The room retains only SHA-256 digests, supports a short loss-safe Web rotation
window, and public snapshots never contain credentials, credential hashes,
member IDs, or transport connection IDs.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import hmac
import math
import re
import secrets
import time
from collections.abc import Mapping
from typing import Any, Callable, Optional
import unicodedata

from game.ai_personality import (
    AI_PERSONALITY_MODES,
    DISRUPTOR,
    EXPANSION,
    MIXED,
    STANDARD,
    TRADER,
)
from game.custom_map import CustomMapError, CustomMapSpec
from game.house_rules import HouseRules
from game.room_access import RoomAccessError, RoomAccessPolicy
from game.variant import VariantConfig, variant_uses_hidden_board


__all__ = (
    "DEFAULT_RESTART_GRACE_SECONDS",
    "DEFAULT_RECONNECT_ROTATION_GRACE_SECONDS",
    "DEFAULT_SEAT_RESERVATION_SECONDS",
    "LOBBY_AUTHORITY_FORMAT",
    "LOBBY_AUTHORITY_VERSION",
    "MAX_LOBBY_MEMBERS",
    "MemberRole",
    "MembershipGrant",
    "LobbyAuthenticationError",
    "LobbyCapacityError",
    "LobbyError",
    "LobbyPermissionError",
    "LobbyRoom",
    "LobbyStateError",
    "LobbyValidationError",
    "RoomPhase",
    "RoomSettings",
)


ROOM_CODE_LENGTH = 6
ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DEFAULT_SEAT_RESERVATION_SECONDS = 120.0
DEFAULT_RESTART_GRACE_SECONDS = 120.0
DEFAULT_RECONNECT_ROTATION_GRACE_SECONDS = 120.0
LOBBY_AUTHORITY_FORMAT = "catan-lobby-authority"
LOBBY_AUTHORITY_VERSION = 2
_LEGACY_LOBBY_AUTHORITY_VERSION = 1
MAX_LOBBY_MEMBERS = 64
MAX_SAFE_BOARD_SEED = (1 << 53) - 1
RECONNECT_TOKEN_BYTES = 32
_ROOM_CODE_PATTERN = re.compile(
    rf"^[{re.escape(ROOM_CODE_ALPHABET)}]{{{ROOM_CODE_LENGTH}}}$"
)
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,}$")
_TOKEN_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MEMBER_ID_PATTERN = re.compile(r"^member-([1-9][0-9]*)$")
_ROOM_SETTINGS_AUTHORITY_KEYS = frozenset(
    {
        "player_count",
        "victory_target",
        "board_mode",
        "board_seed",
        "ai_player_count",
        "ai_personality_mode",
        "custom_map",
        "house_rules",
        "variant",
    }
)
_LOBBY_AUTHORITY_KEYS = frozenset(
    {
        "format",
        "version",
        "saved_at_ms",
        "room_code",
        "settings",
        "phase",
        "revision",
        "reservation_seconds",
        "member_sequence",
        "host_member_id",
        "access_policy",
        "members",
    }
)
_MEMBER_AUTHORITY_KEYS_V1 = frozenset(
    {
        "member_id",
        "display_name",
        "role",
        "seat",
        "reconnect_token_hash",
        "was_connected",
        "ready",
        "joined_order",
        "reservation_expires_at_ms",
    }
)
_MEMBER_AUTHORITY_KEYS_V2 = frozenset(
    {
        *_MEMBER_AUTHORITY_KEYS_V1,
        "previous_reconnect_token_hash",
        "previous_reconnect_token_expires_at_ms",
    }
)


class LobbyError(Exception):
    """Base class for expected lobby-domain errors."""


class LobbyValidationError(LobbyError, ValueError):
    """Raised when settings or command input is invalid."""


class LobbyAuthenticationError(LobbyError):
    """Raised when a reconnect credential is invalid or expired."""


class LobbyPermissionError(LobbyError):
    """Raised when a member's role does not permit an operation."""


class LobbyCapacityError(LobbyError):
    """Raised when every configured player seat is occupied or reserved."""


class LobbyStateError(LobbyError):
    """Raised when an operation is not valid in the room's current phase."""


class MemberRole(str, Enum):
    HOST = "host"
    PLAYER = "player"
    SPECTATOR = "spectator"


class RoomPhase(str, Enum):
    WAITING = "waiting"
    STARTED = "started"


@dataclass(frozen=True)
class RoomSettings:
    """Validated, transport-independent match settings for one room."""

    player_count: int = 4
    victory_target: int = 10
    board_mode: str = "constrained"
    board_seed: int = 0
    ai_player_count: int = 0
    ai_personality_mode: str = STANDARD
    custom_map: CustomMapSpec | Mapping[str, Any] | None = None
    house_rules: HouseRules | Mapping[str, Any] | None = None
    variant: VariantConfig | Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _bounded_int(self.player_count, "player_count", minimum=2, maximum=4)
        _bounded_int(self.victory_target, "victory_target", minimum=5, maximum=15)
        if self.board_mode not in ("constrained", "fully_random", "custom"):
            raise LobbyValidationError(
                "board_mode must be 'constrained', 'fully_random', or 'custom'"
            )
        if isinstance(self.board_seed, bool) or not isinstance(self.board_seed, int):
            raise LobbyValidationError("board_seed must be an integer")
        if abs(self.board_seed) > MAX_SAFE_BOARD_SEED:
            raise LobbyValidationError("board_seed must be a JSON-safe integer")
        _bounded_int(
            self.ai_player_count,
            "ai_player_count",
            minimum=0,
            maximum=self.player_count - 1,
        )
        if (
            not isinstance(self.ai_personality_mode, str)
            or self.ai_personality_mode not in AI_PERSONALITY_MODES
        ):
            raise LobbyValidationError(
                "ai_personality_mode must be standard, mixed, expansion, trader, or disruptor"
            )

        custom_map = self.custom_map
        if isinstance(custom_map, Mapping):
            try:
                custom_map = CustomMapSpec.from_document(custom_map)
            except CustomMapError as exc:
                raise LobbyValidationError("custom_map is invalid") from exc
        elif custom_map is not None and not isinstance(custom_map, CustomMapSpec):
            raise LobbyValidationError("custom_map must be a validated map document")
        if self.board_mode == "custom":
            if custom_map is None:
                raise LobbyValidationError("custom board mode requires custom_map")
        elif custom_map is not None:
            raise LobbyValidationError("custom_map is only valid in custom mode")

        house_rules = self.house_rules
        if isinstance(house_rules, Mapping) or house_rules is None:
            try:
                house_rules = HouseRules.from_document(house_rules)
            except ValueError as exc:
                raise LobbyValidationError("house_rules is invalid") from exc
        elif not isinstance(house_rules, HouseRules):
            raise LobbyValidationError(
                "house_rules must be a validated house-rule document"
            )

        variant = self.variant
        if isinstance(variant, Mapping) or variant is None:
            try:
                variant = VariantConfig.from_document(variant)
            except ValueError as exc:
                raise LobbyValidationError("variant is invalid") from exc
        elif not isinstance(variant, VariantConfig):
            raise LobbyValidationError("variant must be a validated variant document")
        if variant_uses_hidden_board(variant) and self.board_mode == "custom":
            raise LobbyValidationError(
                "frontier variant is available only on generated boards"
            )

        # Never retain caller-owned mutable documents inside a room.  LAN
        # snapshots and the authority factory now share immutable domain
        # values validated at the trust boundary.
        object.__setattr__(self, "custom_map", custom_map)
        object.__setattr__(self, "house_rules", house_rules)
        object.__setattr__(self, "variant", variant)

    def to_public_dict(self) -> dict[str, Any]:
        """Return the JSON-safe settings shared with every lobby viewer."""

        public = {
            "player_count": self.player_count,
            "victory_target": self.victory_target,
            "board_mode": self.board_mode,
            "board_seed": (
                0 if variant_uses_hidden_board(self.variant) else self.board_seed
            ),
            "ai_player_count": self.ai_player_count,
            "ai_personality_mode": self.ai_personality_mode,
            "variant": self.variant.to_document(),
        }
        if self.custom_map is not None:
            public["custom_map"] = self.custom_map.to_document()
        if self.house_rules != HouseRules.standard():
            public["house_rules"] = self.house_rules.to_document()
        return public

    def to_authority_document(self) -> dict[str, Any]:
        """Return the exact, unredacted settings persisted by the authority."""

        return {
            "player_count": self.player_count,
            "victory_target": self.victory_target,
            "board_mode": self.board_mode,
            "board_seed": self.board_seed,
            "ai_player_count": self.ai_player_count,
            "ai_personality_mode": self.ai_personality_mode,
            "custom_map": (
                None if self.custom_map is None else self.custom_map.to_document()
            ),
            "house_rules": self.house_rules.to_document(),
            "variant": self.variant.to_document(),
        }

    @classmethod
    def from_authority_document(
        cls,
        document: Mapping[str, Any],
    ) -> RoomSettings:
        """Parse only the complete authority settings schema."""

        if type(document) is not dict or set(document) != _ROOM_SETTINGS_AUTHORITY_KEYS:
            raise LobbyValidationError(
                "room settings authority document has invalid keys"
            )
        try:
            settings = cls(
                player_count=document["player_count"],
                victory_target=document["victory_target"],
                board_mode=document["board_mode"],
                board_seed=document["board_seed"],
                ai_player_count=document["ai_player_count"],
                ai_personality_mode=document["ai_personality_mode"],
                custom_map=document["custom_map"],
                house_rules=document["house_rules"],
                variant=document["variant"],
            )
        except LobbyValidationError:
            raise
        except (TypeError, ValueError) as exc:
            raise LobbyValidationError(
                "room settings authority document is invalid"
            ) from exc
        if settings.to_authority_document() != document:
            raise LobbyValidationError(
                "room settings authority document is not canonical"
            )
        return settings


@dataclass(frozen=True)
class MembershipGrant:
    """Private response returned to a client after joining or reconnecting.

    ``reconnect_token`` is populated for a new membership and for the trusted
    Web rotation flow.  A normal LAN reconnect returns ``None`` because that
    client keeps its existing credential.  This value must be sent only to the
    authenticated transport adapter and must never be broadcast.
    """

    member_id: str
    role: MemberRole
    seat: Optional[int]
    reconnect_token: Optional[str] = field(repr=False)
    revision: int


@dataclass
class _Member:
    member_id: str
    display_name: str
    role: MemberRole
    seat: Optional[int]
    reconnect_token_hash: str
    connection_id: Optional[str]
    connected: bool
    ready: bool
    joined_order: int
    reserved_until: Optional[float] = None
    previous_reconnect_token_hash: Optional[str] = None
    previous_reconnect_token_expires_at: Optional[float] = None


class LobbyRoom:
    """Authoritative in-memory state machine for one LAN lobby.

    Construct rooms with :meth:`create`.  Mutating methods require the opaque
    transport ``connection_id`` supplied when a client joined.  Every actual
    state change increments ``revision`` exactly once, allowing transports to
    discard stale snapshots.
    """

    def __init__(
        self,
        *,
        room_code: str,
        settings: RoomSettings,
        clock: Callable[[], float],
        token_bytes_generator: Callable[[int], bytes],
        reservation_seconds: float,
        access_policy: RoomAccessPolicy,
    ) -> None:
        self._validate_room_code(room_code)
        if not isinstance(settings, RoomSettings):
            raise LobbyValidationError("settings must be RoomSettings")
        if not callable(clock):
            raise LobbyValidationError("clock must be callable")
        if not callable(token_bytes_generator):
            raise LobbyValidationError("token_bytes_generator must be callable")
        if not isinstance(access_policy, RoomAccessPolicy):
            raise LobbyValidationError("access_policy must be RoomAccessPolicy")
        reservation_seconds = _bounded_positive_seconds(
            reservation_seconds,
            "reservation_seconds",
        )

        self.room_code = room_code
        self.settings = settings
        self.phase = RoomPhase.WAITING
        self.revision = 0
        self._clock = clock
        self._token_bytes_generator = token_bytes_generator
        self._reservation_seconds = reservation_seconds
        self._access_policy = access_policy
        self._members: dict[str, _Member] = {}
        self._member_by_connection: dict[str, str] = {}
        self._member_by_token_hash: dict[str, str] = {}
        self._member_sequence = 0
        self._host_member_id: Optional[str] = None

    @classmethod
    def create(
        cls,
        settings: RoomSettings,
        *,
        host_name: str,
        connection_id: str,
        clock: Callable[[], float] = time.monotonic,
        code_generator: Optional[Callable[[], str]] = None,
        token_bytes_generator: Callable[[int], bytes] = secrets.token_bytes,
        reservation_seconds: float = DEFAULT_SEAT_RESERVATION_SECONDS,
        passphrase: str | None = None,
    ) -> tuple[LobbyRoom, MembershipGrant]:
        """Create a room and occupy seat 1 with its host.

        ``clock``, ``code_generator`` and ``token_bytes_generator`` are
        injectable so tests and a persistence layer can reproduce transitions
        deterministically.  Production defaults use a monotonic clock and the
        ``secrets`` module.
        """

        if code_generator is not None and not callable(code_generator):
            raise LobbyValidationError("code_generator must be callable")
        code = (
            code_generator()
            if code_generator is not None
            else "".join(
                secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH)
            )
        )
        # Derive immediately at the create boundary.  The room never owns the
        # caller's plaintext and the public settings expose only a boolean.
        access_policy = (
            RoomAccessPolicy.open()
            if passphrase is None
            else RoomAccessPolicy.protected(passphrase)
        )
        room = cls(
            room_code=code,
            settings=settings,
            clock=clock,
            token_bytes_generator=token_bytes_generator,
            reservation_seconds=reservation_seconds,
            access_policy=access_policy,
        )
        grant = room._add_member(
            display_name=host_name,
            role=MemberRole.HOST,
            seat=1,
            connection_id=connection_id,
        )
        room._host_member_id = grant.member_id
        return room, grant

    @property
    def is_full(self) -> bool:
        """Whether every player seat is occupied, including reservations."""

        return len(self._seated_members()) >= self.human_player_count

    @property
    def human_player_count(self) -> int:
        """Number of transport-backed seats after reserving the AI lineup."""

        return self.settings.player_count - self.settings.ai_player_count

    @property
    def has_members(self) -> bool:
        """Whether the room still has a real player or spectator membership."""

        return bool(self._members)

    @property
    def passphrase_required(self) -> bool:
        """Whether new player and spectator memberships require a credential."""

        return self._access_policy.passphrase_required

    @property
    def can_start(self) -> bool:
        """Whether all configured seats are occupied, connected, and ready."""

        seated = self._seated_members()
        return bool(
            self.phase is RoomPhase.WAITING
            and len(seated) == self.human_player_count
            and all(member.connected and member.ready for member in seated)
            and self._host_member_id in self._members
        )

    def join_player(
        self,
        *,
        display_name: str,
        connection_id: str,
        passphrase: object = None,
    ) -> MembershipGrant:
        """Join the lowest free player seat, respecting active reservations."""

        self._require_room_access(passphrase)
        if self.phase is not RoomPhase.WAITING:
            raise LobbyStateError("players cannot join after the match starts")
        self.prune_expired()
        if self.is_full:
            raise LobbyCapacityError("the room has no free player seats")
        occupied = {member.seat for member in self._seated_members()}
        seat = next(
            seat
            for seat in range(1, self.human_player_count + 1)
            if seat not in occupied
        )
        return self._add_member(
            display_name=display_name,
            role=MemberRole.PLAYER,
            seat=seat,
            connection_id=connection_id,
        )

    def join_player_authorized(
        self,
        *,
        display_name: str,
        connection_id: str,
    ) -> MembershipGrant:
        """Join after a controller has verified a separate room authority.

        This deliberately bypasses only the passphrase check.  Phase, capacity,
        display-name, seat, and connection invariants remain identical to a
        normal player join.
        """

        if self.phase is not RoomPhase.WAITING:
            raise LobbyStateError("players cannot join after the match starts")
        self.prune_expired()
        if self.is_full:
            raise LobbyCapacityError("the room has no free player seats")
        occupied = {member.seat for member in self._seated_members()}
        seat = next(
            seat
            for seat in range(1, self.human_player_count + 1)
            if seat not in occupied
        )
        return self._add_member(
            display_name=display_name,
            role=MemberRole.PLAYER,
            seat=seat,
            connection_id=connection_id,
        )

    def join_spectator(
        self,
        *,
        display_name: str,
        connection_id: str,
        passphrase: object = None,
    ) -> MembershipGrant:
        """Join without consuming a player seat, before or after match start."""

        self._require_room_access(passphrase)
        self.prune_expired()
        return self._add_member(
            display_name=display_name,
            role=MemberRole.SPECTATOR,
            seat=None,
            connection_id=connection_id,
        )

    def join_spectator_authorized(
        self,
        *,
        display_name: str,
        connection_id: str,
    ) -> MembershipGrant:
        """Join as a spectator after separate authority verification."""

        self.prune_expired()
        return self._add_member(
            display_name=display_name,
            role=MemberRole.SPECTATOR,
            seat=None,
            connection_id=connection_id,
        )

    def require_host(self, connection_id: str) -> None:
        """Authorize a connected room owner without changing lobby state."""

        member = self._connected_member(connection_id)
        if (
            member.member_id != self._host_member_id
            or member.role is not MemberRole.HOST
        ):
            raise LobbyPermissionError("only the host can manage invitations")

    def set_ready(self, connection_id: str, ready: bool = True) -> None:
        """Set a connected host/player ready flag; spectators cannot ready."""

        if self.phase is not RoomPhase.WAITING:
            raise LobbyStateError("ready state cannot change after match start")
        if not isinstance(ready, bool):
            raise LobbyValidationError("ready must be a boolean")
        member = self._connected_member(connection_id)
        if member.role is MemberRole.SPECTATOR:
            raise LobbyPermissionError("spectators do not own a player seat")
        if member.ready == ready:
            return
        member.ready = ready
        self._advance_revision()

    def start(self, connection_id: str) -> int:
        """Start once, and only when the connected host sees all seats ready."""

        self.validate_start(connection_id)
        self.phase = RoomPhase.STARTED
        self._advance_revision()
        return self.revision

    def validate_start(self, connection_id: str) -> None:
        """Authorize start without mutating the lobby.

        The authoritative controller uses this preflight before constructing a
        game so a factory or snapshot failure cannot strand the lobby in the
        started phase without a usable match.
        """

        if self.phase is not RoomPhase.WAITING:
            raise LobbyStateError("the match has already started")
        member = self._connected_member(connection_id)
        if (
            member.member_id != self._host_member_id
            or member.role is not MemberRole.HOST
        ):
            raise LobbyPermissionError("only the host can start the match")
        if not self.can_start:
            raise LobbyStateError("all player seats must be connected and ready")

    def disconnect(self, connection_id: str) -> float:
        """Disconnect a member and reserve its identity/seat for 120s by default.

        The returned value is the monotonic reservation deadline.  The value is
        for server scheduling only and is not included in public snapshots.
        """

        member = self._connected_member(connection_id)
        now = self._now()
        del self._member_by_connection[connection_id]
        member.connection_id = None
        member.connected = False
        member.reserved_until = now + self._reservation_seconds
        self._advance_revision()
        return member.reserved_until

    def reconnect(self, *, reconnect_token: str, connection_id: str) -> MembershipGrant:
        """Rebind a disconnected, unexpired member to a new connection.

        The stable ``member_id``, role and seat are preserved.  A live member
        cannot be displaced with its token.  This LAN-compatible path accepts
        only the confirmed current token and never rotates it.
        """

        member, _token_hash, _used_previous, _now = self._reconnect_candidate(
            reconnect_token=reconnect_token,
            connection_id=connection_id,
            allow_previous=False,
        )
        self._bind_reconnected_member(member, connection_id)
        return MembershipGrant(
            member_id=member.member_id,
            role=member.role,
            seat=member.seat,
            reconnect_token=None,
            revision=self.revision,
        )

    def reconnect_rotating(
        self,
        *,
        reconnect_token: str,
        connection_id: str,
        previous_token_grace_seconds: float = (
            DEFAULT_RECONNECT_ROTATION_GRACE_SECONDS
        ),
    ) -> MembershipGrant:
        """Rebind a Web member and issue a loss-safe replacement credential.

        The token actually presented remains valid for a short bounded grace
        period while the newly generated token is installed as an HttpOnly
        cookie.  If the response is lost, the old cookie can retry.  A retry
        made with that previous token keeps its original absolute deadline so
        repeated requests cannot extend a stolen credential indefinitely.
        """

        grace = _bounded_positive_seconds(
            previous_token_grace_seconds,
            "previous_token_grace_seconds",
        )
        member, token_hash, used_previous, now = self._reconnect_candidate(
            reconnect_token=reconnect_token,
            connection_id=connection_id,
            allow_previous=True,
        )
        new_token, new_token_hash = self._new_reconnect_token()
        previous_expiry = (
            member.previous_reconnect_token_expires_at
            if used_previous
            else now + grace
        )
        if previous_expiry is None or previous_expiry <= now:
            raise LobbyAuthenticationError("invalid or expired reconnect token")

        self._remove_member_token_indexes(member)
        member.reconnect_token_hash = new_token_hash
        member.previous_reconnect_token_hash = token_hash
        member.previous_reconnect_token_expires_at = previous_expiry
        self._member_by_token_hash[new_token_hash] = member.member_id
        self._member_by_token_hash[token_hash] = member.member_id
        self._bind_reconnected_member(member, connection_id)
        return MembershipGrant(
            member_id=member.member_id,
            role=member.role,
            seat=member.seat,
            reconnect_token=new_token,
            revision=self.revision,
        )

    def confirm_reconnect_rotation(
        self,
        *,
        connection_id: str,
        reconnect_token: str,
    ) -> bool:
        """Confirm receipt of the current token and revoke its predecessor.

        Returns whether private credential state changed.  Repeating a
        confirmation with the already-confirmed current token is an
        authenticated no-op.  Public lobby revision is intentionally unchanged
        because no public state changed; the controller still persists the
        private mutation with its authority generation CAS.
        """

        member = self._connected_member(connection_id)
        token_hash = self._validated_token_hash(reconnect_token)
        if not hmac.compare_digest(member.reconnect_token_hash, token_hash):
            raise LobbyAuthenticationError("invalid or expired reconnect token")
        previous_hash = member.previous_reconnect_token_hash
        if previous_hash is None:
            if member.previous_reconnect_token_expires_at is not None:
                raise LobbyStateError("reconnect rotation state is inconsistent")
            return False
        self._member_by_token_hash.pop(previous_hash, None)
        member.previous_reconnect_token_hash = None
        member.previous_reconnect_token_expires_at = None
        return True

    def leave(self, connection_id: str) -> None:
        """Immediately remove a connected member without a reservation."""

        member = self._connected_member(connection_id)
        if self.phase is RoomPhase.STARTED and member.seat is not None:
            raise LobbyStateError(
                "players cannot leave a started match; disconnect to preserve reconnect"
            )
        self._remove_member(member)
        self._promote_host_if_needed()
        self._advance_revision()

    def has_expired_player_reservation(self) -> bool:
        """Whether a started match has lost a seated player past reconnect grace."""

        if self.phase is not RoomPhase.STARTED:
            return False
        now = self._now()
        return any(
            member.seat is not None
            and not member.connected
            and member.reserved_until is not None
            and member.reserved_until <= now
            for member in self._members.values()
        )

    def prune_expired(self) -> tuple[str, ...]:
        """Remove expired disconnected memberships and return their member IDs.

        All removals in one call form a single revision, which keeps snapshot
        revisions monotonic without making them depend on member iteration.
        A seated member of a started match remains as an abort marker so the
        controller can close that match for every peer instead of silently
        leaving an impossible turn behind.
        """

        now = self._now()
        expired = [
            member
            for member in self._members.values()
            if not member.connected
            and member.reserved_until is not None
            and member.reserved_until <= now
            and not (self.phase is RoomPhase.STARTED and member.seat is not None)
        ]
        if not expired:
            return ()
        expired.sort(key=lambda member: member.joined_order)
        for member in expired:
            self._remove_member(member)
        self._promote_host_if_needed()
        self._advance_revision()
        return tuple(member.member_id for member in expired)

    def require_player_seat(self, connection_id: str) -> int:
        """Authorize a game action and return its one-based player seat.

        Network adapters should call this before accepting an action request.
        Spectators can receive snapshots but can never obtain an actionable
        seat.
        """

        if self.phase is not RoomPhase.STARTED:
            raise LobbyStateError("game actions are unavailable before start")
        member = self._connected_member(connection_id)
        if member.role is MemberRole.SPECTATOR or member.seat is None:
            raise LobbyPermissionError("spectators cannot perform game actions")
        return member.seat

    def public_snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe view with no credentials or transport identifiers."""

        now = self._now()
        members = []
        ordered_members = sorted(
            self._members.values(),
            key=lambda member: (
                member.seat is None,
                member.seat if member.seat is not None else member.joined_order,
            ),
        )
        for member in ordered_members:
            reservation_remaining = None
            if not member.connected and member.reserved_until is not None:
                reservation_remaining = max(0.0, member.reserved_until - now)
            members.append(
                {
                    "display_name": member.display_name,
                    "role": member.role.value,
                    "seat": member.seat,
                    "connected": member.connected,
                    "ready": member.ready if member.seat is not None else False,
                    "reservation_seconds_remaining": reservation_remaining,
                }
            )
        spectators = [member for member in members if member["seat"] is None]
        human_players = [member for member in members if member["seat"] is not None]
        ai_members = self._public_ai_members()
        members = (
            sorted(
                [*human_players, *ai_members],
                key=lambda member: member["seat"],
            )
            + spectators
        )
        return {
            "room_code": self.room_code,
            "revision": self.revision,
            "phase": self.phase.value,
            "settings": self.settings.to_public_dict(),
            "access": self._access_policy.to_public_document(),
            "full": self.is_full,
            "can_start": self.can_start,
            "members": members,
            "player_members": len(human_players) + len(ai_members),
            "spectators": sum(
                member.role is MemberRole.SPECTATOR for member in ordered_members
            ),
        }

    def to_authority_document(self, *, wall_clock_ms: int) -> dict[str, Any]:
        """Export exact restart state without serialising live connections.

        Monotonic reservation deadlines cannot survive a process restart, so
        the caller supplies its current wall clock and receives absolute
        millisecond deadlines.  Reconnect tokens remain one-way SHA-256
        digests; plaintext tokens and transport connection IDs are never
        present in this document.
        """

        wall_clock_ms = _bounded_timestamp_ms(wall_clock_ms, "wall_clock_ms")
        monotonic_now = self._now()
        members: list[dict[str, Any]] = []
        for member in sorted(
            self._members.values(), key=lambda item: item.joined_order
        ):
            expires_at_ms: int | None = None
            previous_hash = member.previous_reconnect_token_hash
            previous_expires_at = member.previous_reconnect_token_expires_at
            if (previous_hash is None) != (previous_expires_at is None):
                raise LobbyStateError("reconnect rotation state is inconsistent")
            previous_expires_at_ms: int | None = None
            if (
                previous_hash is not None
                and previous_expires_at is not None
                and previous_expires_at > monotonic_now
            ):
                previous_expires_at_ms = _deadline_to_wall_clock_ms(
                    previous_expires_at,
                    monotonic_now=monotonic_now,
                    wall_clock_ms=wall_clock_ms,
                )
            if member.connected:
                if member.connection_id is None or member.reserved_until is not None:
                    raise LobbyStateError("connected member state is inconsistent")
            else:
                if member.connection_id is not None or member.reserved_until is None:
                    raise LobbyStateError("disconnected member state is inconsistent")
                expires_at_ms = _deadline_to_wall_clock_ms(
                    member.reserved_until,
                    monotonic_now=monotonic_now,
                    wall_clock_ms=wall_clock_ms,
                )
            members.append(
                {
                    "member_id": member.member_id,
                    "display_name": member.display_name,
                    "role": member.role.value,
                    "seat": member.seat,
                    "reconnect_token_hash": member.reconnect_token_hash,
                    "previous_reconnect_token_hash": (
                        previous_hash
                        if previous_expires_at_ms is not None
                        else None
                    ),
                    "previous_reconnect_token_expires_at_ms": (
                        previous_expires_at_ms
                    ),
                    "was_connected": member.connected,
                    "ready": member.ready,
                    "joined_order": member.joined_order,
                    "reservation_expires_at_ms": expires_at_ms,
                }
            )
        return {
            "format": LOBBY_AUTHORITY_FORMAT,
            "version": LOBBY_AUTHORITY_VERSION,
            "saved_at_ms": wall_clock_ms,
            "room_code": self.room_code,
            "settings": self.settings.to_authority_document(),
            "phase": self.phase.value,
            "revision": self.revision,
            "reservation_seconds": self._reservation_seconds,
            "member_sequence": self._member_sequence,
            "host_member_id": self._host_member_id,
            "access_policy": self._access_policy.to_authority_document(),
            "members": members,
        }

    @classmethod
    def from_authority_document(
        cls,
        document: Mapping[str, Any],
        *,
        wall_clock_ms: int,
        clock: Callable[[], float] = time.monotonic,
        token_bytes_generator: Callable[[int], bytes] = secrets.token_bytes,
        restart_grace_seconds: float = DEFAULT_RESTART_GRACE_SECONDS,
    ) -> LobbyRoom:
        """Restore a room fail-closed, with every transport disconnected.

        Members that were already disconnected retain their original absolute
        expiry.  Only members that were live when the authority snapshot was
        taken receive a fresh restart grace period.  Once restored, those
        members are persisted as disconnected too, so repeated restarts cannot
        renew the grace indefinitely.
        """

        if type(document) is not dict or set(document) != _LOBBY_AUTHORITY_KEYS:
            raise LobbyValidationError("lobby authority document has invalid keys")
        if document["format"] != LOBBY_AUTHORITY_FORMAT:
            raise LobbyValidationError("lobby authority format is unsupported")
        authority_version = document["version"]
        if type(authority_version) is not int or authority_version not in {
            _LEGACY_LOBBY_AUTHORITY_VERSION,
            LOBBY_AUTHORITY_VERSION,
        }:
            raise LobbyValidationError("lobby authority version is unsupported")
        saved_at_ms = _bounded_timestamp_ms(document["saved_at_ms"], "saved_at_ms")
        wall_clock_ms = _bounded_timestamp_ms(wall_clock_ms, "wall_clock_ms")
        # A document from the future indicates a broken or rolled-back wall
        # clock.  Extending existing reservations in that situation would be
        # unsafe, so restoration fails closed.
        if saved_at_ms > wall_clock_ms:
            raise LobbyValidationError("lobby authority wall clock moved backwards")

        settings = RoomSettings.from_authority_document(document["settings"])
        phase = _parse_room_phase(document["phase"])
        revision = _bounded_authority_int(document["revision"], "revision")
        member_sequence = _bounded_authority_int(
            document["member_sequence"], "member_sequence"
        )
        reservation_seconds = _bounded_positive_seconds(
            document["reservation_seconds"], "reservation_seconds"
        )
        restart_grace_seconds = _bounded_positive_seconds(
            restart_grace_seconds, "restart_grace_seconds"
        )
        try:
            access_policy = RoomAccessPolicy.from_authority_document(
                document["access_policy"]
            )
        except RoomAccessError as exc:
            raise LobbyValidationError(
                "room access authority document is invalid"
            ) from exc

        host_member_id = document["host_member_id"]
        if host_member_id is not None and type(host_member_id) is not str:
            raise LobbyValidationError("host_member_id is invalid")
        raw_members = document["members"]
        if type(raw_members) is not list or len(raw_members) > MAX_LOBBY_MEMBERS:
            raise LobbyValidationError("lobby authority members are invalid")
        if not callable(clock):
            raise LobbyValidationError("clock must be callable")
        if not callable(token_bytes_generator):
            raise LobbyValidationError("token_bytes_generator must be callable")

        room = cls(
            room_code=document["room_code"],
            settings=settings,
            clock=clock,
            token_bytes_generator=token_bytes_generator,
            reservation_seconds=reservation_seconds,
            access_policy=access_policy,
        )
        monotonic_now = room._now()
        member_ids: set[str] = set()
        token_hashes: set[str] = set()
        joined_orders: set[int] = set()
        seats: set[int] = set()
        folded_names: set[str] = set()
        host_roles: set[str] = set()

        for index, raw_member in enumerate(raw_members):
            member = _restore_member(
                raw_member,
                index=index,
                authority_version=authority_version,
                saved_at_ms=saved_at_ms,
                wall_clock_ms=wall_clock_ms,
                monotonic_now=monotonic_now,
                restart_grace_seconds=restart_grace_seconds,
                human_player_count=room.human_player_count,
            )
            member_number_match = _MEMBER_ID_PATTERN.fullmatch(member.member_id)
            if (
                member_number_match is None
                or int(member_number_match.group(1)) != member.joined_order
            ):
                raise LobbyValidationError(
                    "member_id and joined_order are inconsistent"
                )
            folded_name = member.display_name.casefold()
            member_token_hashes = {member.reconnect_token_hash}
            if member.previous_reconnect_token_hash is not None:
                member_token_hashes.add(member.previous_reconnect_token_hash)
            if (
                member.member_id in member_ids
                or bool(member_token_hashes & token_hashes)
                or len(member_token_hashes)
                != 1 + (member.previous_reconnect_token_hash is not None)
                or member.joined_order in joined_orders
                or folded_name in folded_names
                or (member.seat is not None and member.seat in seats)
            ):
                raise LobbyValidationError(
                    "lobby authority member identity is duplicated"
                )
            if folded_name in {
                ai_member["display_name"].casefold()
                for ai_member in room._public_ai_members()
            }:
                raise LobbyValidationError("display_name conflicts with an AI member")
            if member.role is MemberRole.HOST:
                host_roles.add(member.member_id)
            member_ids.add(member.member_id)
            token_hashes.update(member_token_hashes)
            joined_orders.add(member.joined_order)
            folded_names.add(folded_name)
            if member.seat is not None:
                seats.add(member.seat)
            room._members[member.member_id] = member
            room._member_by_token_hash[member.reconnect_token_hash] = member.member_id
            if member.previous_reconnect_token_hash is not None:
                room._member_by_token_hash[
                    member.previous_reconnect_token_hash
                ] = member.member_id

        if member_sequence < max(joined_orders, default=0):
            raise LobbyValidationError("member_sequence precedes a restored member")
        if revision < member_sequence:
            raise LobbyValidationError("revision precedes the restored member sequence")
        if host_member_id is None:
            if host_roles:
                raise LobbyValidationError("host role exists without host_member_id")
        elif host_member_id not in member_ids or host_roles != {host_member_id}:
            raise LobbyValidationError("host_member_id does not identify the sole host")
        if phase is RoomPhase.STARTED and (
            host_member_id is None
            or len(seats) != room.human_player_count
            or any(
                member.seat is not None and not member.ready
                for member in room._members.values()
            )
        ):
            raise LobbyValidationError("started lobby authority state is incomplete")

        room.phase = phase
        room.revision = revision
        room._member_sequence = member_sequence
        room._host_member_id = host_member_id
        # Transport identifiers deliberately start empty; reconnect() is the
        # only route that can bind a restored identity to a live connection.
        room._member_by_connection = {}
        return room

    def _public_ai_members(self) -> list[dict[str, Any]]:
        personalities = self._ai_personality_lineup()
        hide_lineup = self.settings.ai_personality_mode == MIXED
        return [
            {
                "display_name": f"CPU{cpu_index}",
                "role": MemberRole.PLAYER.value,
                "seat": seat,
                "connected": True,
                "ready": True,
                "reservation_seconds_remaining": None,
                "is_ai": True,
                # The selected mode is public, but a mixed lineup is meant to
                # be inferred from play and revealed only in the match result.
                "ai_personality": None if hide_lineup else personality,
            }
            for cpu_index, (seat, personality) in enumerate(
                zip(
                    range(self.human_player_count + 1, self.settings.player_count + 1),
                    personalities,
                ),
                start=1,
            )
        ]

    def _ai_personality_lineup(self) -> tuple[str, ...]:
        count = self.settings.ai_player_count
        if self.settings.ai_personality_mode == MIXED:
            mixed = (EXPANSION, TRADER, DISRUPTOR)
            return tuple(mixed[index % len(mixed)] for index in range(count))
        return (self.settings.ai_personality_mode,) * count

    def _require_room_access(self, candidate: object) -> None:
        """Authorize a new membership without revealing why verification failed."""

        if not self._access_policy.verify(candidate):
            raise LobbyAuthenticationError("room access could not be verified")

    def _add_member(
        self,
        *,
        display_name: str,
        role: MemberRole,
        seat: Optional[int],
        connection_id: str,
    ) -> MembershipGrant:
        display_name = self._validate_display_name(display_name)
        self._validate_connection_id(connection_id)
        if connection_id in self._member_by_connection:
            raise LobbyStateError("connection_id is already attached")
        if any(
            member.display_name.casefold() == display_name.casefold()
            for member in self._members.values()
        ) or display_name.casefold() in {
            member["display_name"].casefold() for member in self._public_ai_members()
        }:
            raise LobbyValidationError("display_name is already in use")
        if len(self._members) >= MAX_LOBBY_MEMBERS:
            raise LobbyCapacityError("the room membership limit has been reached")

        token, token_hash = self._new_reconnect_token()
        self._member_sequence += 1
        member = _Member(
            member_id=f"member-{self._member_sequence}",
            display_name=display_name,
            role=role,
            seat=seat,
            reconnect_token_hash=token_hash,
            connection_id=connection_id,
            connected=True,
            ready=False,
            joined_order=self._member_sequence,
        )
        self._members[member.member_id] = member
        self._member_by_connection[connection_id] = member.member_id
        self._member_by_token_hash[token_hash] = member.member_id
        self._promote_host_if_needed()
        self._advance_revision()
        return MembershipGrant(
            member_id=member.member_id,
            role=member.role,
            seat=member.seat,
            reconnect_token=token,
            revision=self.revision,
        )

    def _new_reconnect_token(self) -> tuple[str, str]:
        for _attempt in range(8):
            material = self._token_bytes_generator(RECONNECT_TOKEN_BYTES)
            if not isinstance(material, bytes) or len(material) < RECONNECT_TOKEN_BYTES:
                raise LobbyValidationError(
                    "token_bytes_generator must return at least 32 bytes"
                )
            token = base64.urlsafe_b64encode(material).rstrip(b"=").decode("ascii")
            token_hash = self._hash_token(token)
            if token_hash not in self._member_by_token_hash:
                return token, token_hash
        raise LobbyStateError("could not generate a unique reconnect token")

    def _reconnect_candidate(
        self,
        *,
        reconnect_token: str,
        connection_id: str,
        allow_previous: bool,
    ) -> tuple[_Member, str, bool, float]:
        self._validate_connection_id(connection_id)
        if connection_id in self._member_by_connection:
            raise LobbyStateError("connection_id is already attached")
        self.prune_expired()
        now = self._now()
        token_hash = self._validated_token_hash(reconnect_token)
        member_id = self._find_member_id_by_token_hash(token_hash)
        member = self._members.get(member_id) if member_id is not None else None
        if member is None:
            raise LobbyAuthenticationError("invalid or expired reconnect token")
        is_current = hmac.compare_digest(member.reconnect_token_hash, token_hash)
        previous_hash = member.previous_reconnect_token_hash
        previous_expiry = member.previous_reconnect_token_expires_at
        is_previous = (
            allow_previous
            and previous_hash is not None
            and previous_expiry is not None
            and previous_expiry > now
            and hmac.compare_digest(previous_hash, token_hash)
        )
        if not is_current and not is_previous:
            raise LobbyAuthenticationError("invalid or expired reconnect token")
        if member.connected:
            raise LobbyStateError("the member is already connected")
        if member.reserved_until is not None and member.reserved_until <= now:
            raise LobbyAuthenticationError("invalid or expired reconnect token")
        return member, token_hash, is_previous, now

    def _bind_reconnected_member(self, member: _Member, connection_id: str) -> None:
        member.connection_id = connection_id
        member.connected = True
        member.reserved_until = None
        self._member_by_connection[connection_id] = member.member_id
        self._promote_host_if_needed()
        self._advance_revision()

    def _validated_token_hash(self, token: str) -> str:
        if not isinstance(token, str) or not _TOKEN_PATTERN.fullmatch(token):
            raise LobbyAuthenticationError("invalid or expired reconnect token")
        return self._hash_token(token)

    def _find_member_id_by_token_hash(self, candidate: str) -> Optional[str]:
        for token_hash, member_id in self._member_by_token_hash.items():
            if hmac.compare_digest(token_hash, candidate):
                return member_id
        return None

    def _connected_member(self, connection_id: str) -> _Member:
        self._validate_connection_id(connection_id)
        member_id = self._member_by_connection.get(connection_id)
        member = self._members.get(member_id) if member_id is not None else None
        if member is None or not member.connected:
            raise LobbyAuthenticationError("connection is not an active lobby member")
        return member

    def _remove_member(self, member: _Member) -> None:
        if member.connection_id is not None:
            self._member_by_connection.pop(member.connection_id, None)
        self._remove_member_token_indexes(member)
        self._members.pop(member.member_id, None)
        if self._host_member_id == member.member_id:
            self._host_member_id = None

    def _remove_member_token_indexes(self, member: _Member) -> None:
        self._member_by_token_hash.pop(member.reconnect_token_hash, None)
        previous_hash = member.previous_reconnect_token_hash
        if previous_hash is not None:
            self._member_by_token_hash.pop(previous_hash, None)

    def _promote_host_if_needed(self) -> None:
        """Give host authority to the lowest connected player when necessary."""

        if self._host_member_id in self._members:
            return
        candidates = sorted(
            (
                member
                for member in self._members.values()
                if member.connected and member.seat is not None
            ),
            key=lambda member: (
                member.seat if member.seat is not None else 10_000,
                member.joined_order,
            ),
        )
        if not candidates:
            return
        promoted = candidates[0]
        promoted.role = MemberRole.HOST
        self._host_member_id = promoted.member_id

    def _seated_members(self) -> list[_Member]:
        return [member for member in self._members.values() if member.seat is not None]

    def _advance_revision(self) -> None:
        self.revision += 1

    def _now(self) -> float:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise LobbyStateError("clock must return a number")
        value = float(value)
        if not math.isfinite(value):
            raise LobbyStateError("clock must return a finite number")
        return value

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    @staticmethod
    def _validate_room_code(room_code: str) -> None:
        if not isinstance(room_code, str) or not _ROOM_CODE_PATTERN.fullmatch(
            room_code
        ):
            raise LobbyValidationError(
                "room code must be six unambiguous uppercase letters/digits"
            )

    @staticmethod
    def _validate_connection_id(connection_id: str) -> None:
        if (
            not isinstance(connection_id, str)
            or not 1 <= len(connection_id) <= 128
            or any(unicodedata.category(char).startswith("C") for char in connection_id)
        ):
            raise LobbyValidationError("connection_id is invalid")

    @staticmethod
    def _validate_display_name(display_name: str) -> str:
        if not isinstance(display_name, str):
            raise LobbyValidationError("display_name must be a string")
        display_name = display_name.strip()
        if not 1 <= len(display_name) <= 32 or any(
            unicodedata.category(char).startswith("C") for char in display_name
        ):
            raise LobbyValidationError("display_name is invalid")
        return display_name


def _bounded_int(value: Any, label: str, *, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise LobbyValidationError(f"{label} must be {minimum}..{maximum}")
    return value


def _bounded_authority_int(value: Any, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_BOARD_SEED:
        raise LobbyValidationError(f"{label} must be a non-negative JSON-safe integer")
    return value


def _bounded_timestamp_ms(value: Any, label: str) -> int:
    return _bounded_authority_int(value, label)


def _bounded_positive_seconds(value: Any, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 < float(value) <= 7 * 24 * 60 * 60
    ):
        raise LobbyValidationError(f"{label} must be a bounded positive duration")
    return float(value)


def _deadline_to_wall_clock_ms(
    deadline: float,
    *,
    monotonic_now: float,
    wall_clock_ms: int,
) -> int:
    offset_ms = round((deadline - monotonic_now) * 1000)
    expires_at_ms = wall_clock_ms + offset_ms
    return _bounded_timestamp_ms(expires_at_ms, "reservation_expires_at_ms")


def _parse_room_phase(value: Any) -> RoomPhase:
    if type(value) is not str:
        raise LobbyValidationError("phase is invalid")
    try:
        return RoomPhase(value)
    except ValueError as exc:
        raise LobbyValidationError("phase is invalid") from exc


def _restore_member(
    document: Any,
    *,
    index: int,
    authority_version: int,
    saved_at_ms: int,
    wall_clock_ms: int,
    monotonic_now: float,
    restart_grace_seconds: float,
    human_player_count: int,
) -> _Member:
    label = f"members[{index}]"
    expected_keys = (
        _MEMBER_AUTHORITY_KEYS_V1
        if authority_version == _LEGACY_LOBBY_AUTHORITY_VERSION
        else _MEMBER_AUTHORITY_KEYS_V2
    )
    if type(document) is not dict or set(document) != expected_keys:
        raise LobbyValidationError(f"{label} has invalid keys")
    member_id = document["member_id"]
    if type(member_id) is not str:
        raise LobbyValidationError(f"{label}.member_id is invalid")
    raw_display_name = document["display_name"]
    display_name = LobbyRoom._validate_display_name(raw_display_name)
    if display_name != raw_display_name:
        raise LobbyValidationError(f"{label}.display_name is not canonical")
    role_value = document["role"]
    if type(role_value) is not str:
        raise LobbyValidationError(f"{label}.role is invalid")
    try:
        role = MemberRole(role_value)
    except ValueError as exc:
        raise LobbyValidationError(f"{label}.role is invalid") from exc
    seat = document["seat"]
    if role is MemberRole.SPECTATOR:
        if seat is not None:
            raise LobbyValidationError(f"{label}.seat is invalid for a spectator")
    elif type(seat) is not int or not 1 <= seat <= human_player_count:
        raise LobbyValidationError(f"{label}.seat is invalid for a player")
    token_hash = document["reconnect_token_hash"]
    if type(token_hash) is not str or _TOKEN_HASH_PATTERN.fullmatch(token_hash) is None:
        raise LobbyValidationError(f"{label}.reconnect_token_hash is invalid")
    previous_token_hash: str | None = None
    previous_token_expires_at: float | None = None
    if authority_version == LOBBY_AUTHORITY_VERSION:
        raw_previous_hash = document["previous_reconnect_token_hash"]
        raw_previous_expiry = document[
            "previous_reconnect_token_expires_at_ms"
        ]
        if (raw_previous_hash is None) != (raw_previous_expiry is None):
            raise LobbyValidationError(
                f"{label} previous reconnect fields are incomplete"
            )
        if raw_previous_hash is not None:
            if (
                type(raw_previous_hash) is not str
                or _TOKEN_HASH_PATTERN.fullmatch(raw_previous_hash) is None
                or hmac.compare_digest(token_hash, raw_previous_hash)
            ):
                raise LobbyValidationError(
                    f"{label}.previous_reconnect_token_hash is invalid"
                )
            previous_expiry_ms = _bounded_timestamp_ms(
                raw_previous_expiry,
                "previous_reconnect_token_expires_at_ms",
            )
            if previous_expiry_ms <= saved_at_ms:
                raise LobbyValidationError(
                    f"{label} previous reconnect deadline is not canonical"
                )
            if previous_expiry_ms > wall_clock_ms:
                previous_token_hash = raw_previous_hash
                previous_token_expires_at = monotonic_now + (
                    previous_expiry_ms - wall_clock_ms
                ) / 1000.0
    was_connected = document["was_connected"]
    ready = document["ready"]
    if type(was_connected) is not bool or type(ready) is not bool:
        raise LobbyValidationError(f"{label} connection or ready state is invalid")
    if role is MemberRole.SPECTATOR and ready:
        raise LobbyValidationError(f"{label}.ready is invalid for a spectator")
    joined_order = _bounded_authority_int(document["joined_order"], "joined_order")
    if joined_order == 0:
        raise LobbyValidationError(f"{label}.joined_order must be positive")

    expires_at_ms = document["reservation_expires_at_ms"]
    if was_connected:
        if expires_at_ms is not None:
            raise LobbyValidationError(f"{label} live member must not have a deadline")
        reserved_until = monotonic_now + restart_grace_seconds
    else:
        expires_at_ms = _bounded_timestamp_ms(
            expires_at_ms, "reservation_expires_at_ms"
        )
        reserved_until = monotonic_now + (expires_at_ms - wall_clock_ms) / 1000.0

    return _Member(
        member_id=member_id,
        display_name=display_name,
        role=role,
        seat=seat,
        reconnect_token_hash=token_hash,
        connection_id=None,
        connected=False,
        ready=ready,
        joined_order=joined_order,
        reserved_until=reserved_until,
        previous_reconnect_token_hash=previous_token_hash,
        previous_reconnect_token_expires_at=previous_token_expires_at,
    )
