"""Pure domain model for a small trusted-LAN CATAN lobby.

The module deliberately contains no sockets, threads, Pygame objects, or JSON
transport code.  A future desktop server or Web API can therefore own one
``LobbyRoom`` and translate commands/snapshots without duplicating lobby rules.

Reconnect credentials are returned exactly once when a member joins.  The room
retains only a SHA-256 digest of each credential and public snapshots never
contain credentials, credential hashes, member IDs, or transport connection
IDs.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
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

from game.custom_map import CustomMapError, CustomMapSpec
from game.house_rules import HouseRules


__all__ = (
    "DEFAULT_SEAT_RESERVATION_SECONDS",
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
MAX_SAFE_BOARD_SEED = (1 << 53) - 1
RECONNECT_TOKEN_BYTES = 32
_ROOM_CODE_PATTERN = re.compile(
    rf"^[{re.escape(ROOM_CODE_ALPHABET)}]{{{ROOM_CODE_LENGTH}}}$"
)
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,}$")


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
    custom_map: CustomMapSpec | Mapping[str, Any] | None = None
    house_rules: HouseRules | Mapping[str, Any] | None = None

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

        custom_map = self.custom_map
        if isinstance(custom_map, Mapping):
            try:
                custom_map = CustomMapSpec.from_document(custom_map)
            except CustomMapError as exc:
                raise LobbyValidationError("custom_map is invalid") from exc
        elif custom_map is not None and not isinstance(custom_map, CustomMapSpec):
            raise LobbyValidationError(
                "custom_map must be a validated map document"
            )
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

        # Never retain caller-owned mutable documents inside a room.  LAN
        # snapshots and the authority factory now share immutable domain
        # values validated at the trust boundary.
        object.__setattr__(self, "custom_map", custom_map)
        object.__setattr__(self, "house_rules", house_rules)

    def to_public_dict(self) -> dict[str, Any]:
        """Return the JSON-safe settings shared with every lobby viewer."""

        public = {
            "player_count": self.player_count,
            "victory_target": self.victory_target,
            "board_mode": self.board_mode,
            "board_seed": self.board_seed,
        }
        if self.custom_map is not None:
            public["custom_map"] = self.custom_map.to_document()
        if self.house_rules != HouseRules.standard():
            public["house_rules"] = self.house_rules.to_document()
        return public


@dataclass(frozen=True)
class MembershipGrant:
    """Private response returned to a client after joining or reconnecting.

    ``reconnect_token`` is populated for a new membership and ``None`` after a
    reconnect because the client already possesses the credential.  This value
    must be sent only to that client and must never be broadcast.
    """

    member_id: str
    role: MemberRole
    seat: Optional[int]
    reconnect_token: Optional[str]
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
    ) -> None:
        self._validate_room_code(room_code)
        if not isinstance(settings, RoomSettings):
            raise LobbyValidationError("settings must be RoomSettings")
        if not callable(clock):
            raise LobbyValidationError("clock must be callable")
        if not callable(token_bytes_generator):
            raise LobbyValidationError("token_bytes_generator must be callable")
        if (
            isinstance(reservation_seconds, bool)
            or not isinstance(reservation_seconds, (int, float))
            or not math.isfinite(float(reservation_seconds))
            or reservation_seconds <= 0
        ):
            raise LobbyValidationError("reservation_seconds must be positive")

        self.room_code = room_code
        self.settings = settings
        self.phase = RoomPhase.WAITING
        self.revision = 0
        self._clock = clock
        self._token_bytes_generator = token_bytes_generator
        self._reservation_seconds = float(reservation_seconds)
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
        room = cls(
            room_code=code,
            settings=settings,
            clock=clock,
            token_bytes_generator=token_bytes_generator,
            reservation_seconds=reservation_seconds,
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

        return len(self._seated_members()) >= self.settings.player_count

    @property
    def can_start(self) -> bool:
        """Whether all configured seats are occupied, connected, and ready."""

        seated = self._seated_members()
        return bool(
            self.phase is RoomPhase.WAITING
            and len(seated) == self.settings.player_count
            and all(member.connected and member.ready for member in seated)
            and self._host_member_id in self._members
        )

    def join_player(self, *, display_name: str, connection_id: str) -> MembershipGrant:
        """Join the lowest free player seat, respecting active reservations."""

        if self.phase is not RoomPhase.WAITING:
            raise LobbyStateError("players cannot join after the match starts")
        self.prune_expired()
        if self.is_full:
            raise LobbyCapacityError("the room has no free player seats")
        occupied = {member.seat for member in self._seated_members()}
        seat = next(
            seat
            for seat in range(1, self.settings.player_count + 1)
            if seat not in occupied
        )
        return self._add_member(
            display_name=display_name,
            role=MemberRole.PLAYER,
            seat=seat,
            connection_id=connection_id,
        )

    def join_spectator(
        self, *, display_name: str, connection_id: str
    ) -> MembershipGrant:
        """Join without consuming a player seat, before or after match start."""

        self.prune_expired()
        return self._add_member(
            display_name=display_name,
            role=MemberRole.SPECTATOR,
            seat=None,
            connection_id=connection_id,
        )

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
        cannot be displaced with its token.
        """

        self._validate_connection_id(connection_id)
        if connection_id in self._member_by_connection:
            raise LobbyStateError("connection_id is already attached")
        self.prune_expired()
        token_hash = self._validated_token_hash(reconnect_token)
        member_id = self._find_member_id_by_token_hash(token_hash)
        if member_id is None:
            raise LobbyAuthenticationError("invalid or expired reconnect token")
        member = self._members.get(member_id)
        if member is None:
            raise LobbyAuthenticationError("invalid or expired reconnect token")
        if member.connected:
            raise LobbyStateError("the member is already connected")
        if (
            member.reserved_until is not None
            and member.reserved_until <= self._now()
        ):
            raise LobbyAuthenticationError("invalid or expired reconnect token")
        member.connection_id = connection_id
        member.connected = True
        member.reserved_until = None
        self._member_by_connection[connection_id] = member.member_id
        self._promote_host_if_needed()
        self._advance_revision()
        return MembershipGrant(
            member_id=member.member_id,
            role=member.role,
            seat=member.seat,
            reconnect_token=None,
            revision=self.revision,
        )

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
            and not (
                self.phase is RoomPhase.STARTED and member.seat is not None
            )
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
        ordered = sorted(
            self._members.values(),
            key=lambda member: (
                member.seat is None,
                member.seat if member.seat is not None else member.joined_order,
            ),
        )
        for member in ordered:
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
        return {
            "room_code": self.room_code,
            "revision": self.revision,
            "phase": self.phase.value,
            "settings": self.settings.to_public_dict(),
            "full": self.is_full,
            "can_start": self.can_start,
            "members": members,
            "player_members": sum(member.seat is not None for member in ordered),
            "spectators": sum(
                member.role is MemberRole.SPECTATOR for member in ordered
            ),
        }

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
        ):
            raise LobbyValidationError("display_name is already in use")

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
        self._member_by_token_hash.pop(member.reconnect_token_hash, None)
        self._members.pop(member.member_id, None)
        if self._host_member_id == member.member_id:
            self._host_member_id = None

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
