"""Presentation-independent metrics for a single Catan match.

The tracker only accepts JSON-compatible identifiers and scalar values.  It
therefore has no dependency on Pygame, the desktop game object, or concrete
network transports and can be shared by desktop results, headless analysis,
LAN clients, and a future web client.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import math
from typing import Any


MATCH_METRICS_FORMAT = "catan-match-metrics"
MATCH_METRICS_VERSION = 1

MAX_PLAYER_ID_LENGTH = 128
MAX_DISPLAY_NAME_LENGTH = 160
MAX_EVENT_NAME_LENGTH = 160
MAX_EVENT_DETAIL_LENGTH = 2_000
MAX_COUNTER_VALUE = 10_000_000

_BUILD_COUNTERS = {
    "road": "roads_built",
    "settlement": "settlements_built",
    "city": "cities_built",
}


class MatchMetricsError(ValueError):
    """Raised when match metric input cannot be safely represented."""


@dataclass
class PlayerMatchMetrics:
    """Cumulative public metrics for one stable player identifier."""

    player_id: str
    display_name: str
    roads_built: int = 0
    settlements_built: int = 0
    cities_built: int = 0
    domestic_trades: int = 0
    bank_trades: int = 0
    actual_production_units: int = 0
    expected_production_units: float = 0.0

    @property
    def luck_index(self) -> float:
        """Return production luck with expectation represented by ``100``.

        Before a player has any expected production, ``100`` is the neutral
        and finite value.  This also safely handles malformed historic match
        situations where an actual unit was recorded before its expectation.
        """

        if self.expected_production_units == 0:
            return 100.0
        return self.actual_production_units / self.expected_production_units * 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "display_name": self.display_name,
            "roads_built": self.roads_built,
            "settlements_built": self.settlements_built,
            "cities_built": self.cities_built,
            "domestic_trades": self.domestic_trades,
            "bank_trades": self.bank_trades,
            "actual_production_units": self.actual_production_units,
            "expected_production_units": self.expected_production_units,
            "luck_index": self.luck_index,
        }


@dataclass(frozen=True)
class PointCheckpoint:
    """Victory-point totals captured at a meaningful game event."""

    sequence: int
    semantic_event: str
    detail: str
    points: dict[str, int]
    replay_frame_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "semantic_event": self.semantic_event,
            "detail": self.detail,
            "points": dict(self.points),
            "replay_frame_index": self.replay_frame_index,
        }


@dataclass(frozen=True)
class ImportantMatchEvent:
    """A result-screen highlight optionally linked to a replay frame."""

    sequence: int
    title: str
    detail: str
    replay_frame_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "title": self.title,
            "detail": self.detail,
            "replay_frame_index": self.replay_frame_index,
        }


class MatchMetrics:
    """Accumulate deterministic, serializable metrics for one match."""

    def __init__(self) -> None:
        self._players: dict[str, PlayerMatchMetrics] = {}
        self._point_checkpoints: list[PointCheckpoint] = []
        self._important_events: list[ImportantMatchEvent] = []

    @property
    def players(self) -> tuple[PlayerMatchMetrics, ...]:
        """Return snapshots so callers cannot mutate tracker state directly."""

        return tuple(replace(player) for player in self._players.values())

    @property
    def point_checkpoints(self) -> tuple[PointCheckpoint, ...]:
        return tuple(
            replace(checkpoint, points=dict(checkpoint.points))
            for checkpoint in self._point_checkpoints
        )

    @property
    def important_events(self) -> tuple[ImportantMatchEvent, ...]:
        return tuple(self._important_events)

    def register_player(
        self,
        player_id: str,
        display_name: str | None = None,
    ) -> PlayerMatchMetrics:
        """Register a player idempotently and return its current snapshot."""

        has_explicit_display_name = display_name is not None
        player_id = _validated_text(
            player_id,
            field="player_id",
            maximum=MAX_PLAYER_ID_LENGTH,
            allow_empty=False,
        )
        if display_name is None:
            display_name = player_id
        display_name = _validated_text(
            display_name,
            field="display_name",
            maximum=MAX_DISPLAY_NAME_LENGTH,
            allow_empty=False,
        )
        player = self._players.get(player_id)
        if player is None:
            player = PlayerMatchMetrics(player_id, display_name)
            self._players[player_id] = player
        elif has_explicit_display_name:
            player.display_name = display_name
        return replace(player)

    def player(self, player_id: str) -> PlayerMatchMetrics:
        """Return one player's metrics without exposing mutable state."""

        player_id = _validated_text(
            player_id,
            field="player_id",
            maximum=MAX_PLAYER_ID_LENGTH,
            allow_empty=False,
        )
        try:
            return replace(self._players[player_id])
        except KeyError as error:
            raise MatchMetricsError(f"unknown player_id: {player_id}") from error

    get_player = player

    def record_build(self, player_id: str, building: str, *, count: int = 1) -> None:
        """Record cumulative construction; city upgrades do not erase settlements."""

        if building not in _BUILD_COUNTERS:
            supported = ", ".join(_BUILD_COUNTERS)
            raise MatchMetricsError(
                f"building must be one of {supported}: {building!r}"
            )
        count = _validated_counter(count, field="count")
        player = self._ensure_player(player_id)
        counter_name = _BUILD_COUNTERS[building]
        _increment_counter(player, counter_name, count)

    def record_domestic_trade(
        self,
        player_id: str,
        partner_id: str | None = None,
        *,
        count: int = 1,
    ) -> None:
        """Record a player trade for both participants when a partner is supplied."""

        count = _validated_counter(count, field="count")
        primary_id = _validated_player_id(player_id)
        participant_ids = [primary_id]
        if partner_id is not None:
            partner_id = _validated_player_id(partner_id)
            if partner_id != primary_id:
                participant_ids.append(partner_id)
        players = [self._ensure_player(participant_id) for participant_id in participant_ids]
        for player in players:
            _validated_increment(player, "domestic_trades", count)
        for player in players:
            player.domestic_trades += count

    def record_bank_trade(self, player_id: str, *, count: int = 1) -> None:
        count = _validated_counter(count, field="count")
        player = self._ensure_player(player_id)
        _increment_counter(player, "bank_trades", count)

    def record_production(
        self,
        player_id: str,
        *,
        actual_units: int,
        expected_units: int | float,
    ) -> None:
        """Record rolled production and its statistical expectation.

        ``actual_units`` means units activated by the dice and buildings after
        applying the robber, but before any shared-bank shortage.  This keeps
        the luck index about dice outcomes rather than bank availability.
        """

        actual_units = _validated_counter(actual_units, field="actual_units")
        expected_units = _validated_expected_units(expected_units)
        player = self._ensure_player(player_id)
        _validated_increment(player, "actual_production_units", actual_units)
        new_expected = player.expected_production_units + expected_units
        if not math.isfinite(new_expected) or new_expected > MAX_COUNTER_VALUE:
            raise MatchMetricsError(
                f"expected_production_units must not exceed {MAX_COUNTER_VALUE}"
            )
        player.actual_production_units += actual_units
        player.expected_production_units = new_expected

    def record_point_checkpoint(
        self,
        semantic_event: str,
        points: Mapping[str, int],
        *,
        detail: str = "",
        replay_frame_index: int | None = None,
    ) -> PointCheckpoint:
        """Capture a complete or partial point table at a semantic event."""

        semantic_event = _validated_text(
            semantic_event,
            field="semantic_event",
            maximum=MAX_EVENT_NAME_LENGTH,
            allow_empty=False,
        )
        detail = _validated_text(
            detail,
            field="detail",
            maximum=MAX_EVENT_DETAIL_LENGTH,
            allow_empty=True,
        )
        frame_index = _validated_frame_index(replay_frame_index)
        normalised_points = _validated_points(points)
        for player_id in normalised_points:
            self._ensure_player(player_id)
        checkpoint = PointCheckpoint(
            sequence=len(self._point_checkpoints),
            semantic_event=semantic_event,
            detail=detail,
            points=normalised_points,
            replay_frame_index=frame_index,
        )
        self._point_checkpoints.append(checkpoint)
        return replace(checkpoint, points=dict(checkpoint.points))

    def record_important_event(
        self,
        title: str,
        detail: str = "",
        *,
        replay_frame_index: int | None = None,
    ) -> ImportantMatchEvent:
        title = _validated_text(
            title,
            field="title",
            maximum=MAX_EVENT_NAME_LENGTH,
            allow_empty=False,
        )
        detail = _validated_text(
            detail,
            field="detail",
            maximum=MAX_EVENT_DETAIL_LENGTH,
            allow_empty=True,
        )
        event = ImportantMatchEvent(
            sequence=len(self._important_events),
            title=title,
            detail=detail,
            replay_frame_index=_validated_frame_index(replay_frame_index),
        )
        self._important_events.append(event)
        return event

    def to_dict(self) -> dict[str, Any]:
        """Return a fresh JSON-safe document."""

        return {
            "format": MATCH_METRICS_FORMAT,
            "version": MATCH_METRICS_VERSION,
            "players": [player.to_dict() for player in self._players.values()],
            "point_checkpoints": [
                checkpoint.to_dict() for checkpoint in self._point_checkpoints
            ],
            "important_events": [event.to_dict() for event in self._important_events],
        }

    serialize = to_dict

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> MatchMetrics:
        """Restore a tracker, defaulting absent historic sections to empty."""

        if not isinstance(document, Mapping):
            raise MatchMetricsError("match metrics document must be an object")
        _validate_document_header(document)
        tracker = cls()

        raw_players = document.get("players", [])
        if not isinstance(raw_players, (list, tuple)):
            raise MatchMetricsError("players must be an array")
        for raw_player in raw_players:
            tracker._restore_player(raw_player)

        raw_checkpoints = document.get("point_checkpoints", [])
        if not isinstance(raw_checkpoints, (list, tuple)):
            raise MatchMetricsError("point_checkpoints must be an array")
        for expected_sequence, raw_checkpoint in enumerate(raw_checkpoints):
            tracker._restore_checkpoint(raw_checkpoint, expected_sequence)

        raw_events = document.get("important_events", [])
        if not isinstance(raw_events, (list, tuple)):
            raise MatchMetricsError("important_events must be an array")
        for expected_sequence, raw_event in enumerate(raw_events):
            tracker._restore_important_event(raw_event, expected_sequence)
        return tracker

    restore = from_dict

    def _ensure_player(self, player_id: str) -> PlayerMatchMetrics:
        player_id = _validated_player_id(player_id)
        player = self._players.get(player_id)
        if player is None:
            player = PlayerMatchMetrics(player_id, player_id)
            self._players[player_id] = player
        return player

    def _restore_player(self, raw_player: Any) -> None:
        if not isinstance(raw_player, Mapping):
            raise MatchMetricsError("each player metric must be an object")
        player_id = _validated_player_id(raw_player.get("player_id"))
        if player_id in self._players:
            raise MatchMetricsError(f"duplicate player_id: {player_id}")
        display_name = raw_player.get("display_name", player_id)
        self.register_player(player_id, display_name)
        player = self._players[player_id]
        for counter_name in (
            "roads_built",
            "settlements_built",
            "cities_built",
            "domestic_trades",
            "bank_trades",
            "actual_production_units",
        ):
            setattr(
                player,
                counter_name,
                _validated_counter(raw_player.get(counter_name, 0), field=counter_name),
            )
        player.expected_production_units = _validated_expected_units(
            raw_player.get("expected_production_units", 0)
        )
        if "luck_index" in raw_player:
            _validated_derived_luck(raw_player["luck_index"])

    def _restore_checkpoint(self, raw_checkpoint: Any, expected_sequence: int) -> None:
        if not isinstance(raw_checkpoint, Mapping):
            raise MatchMetricsError("each point checkpoint must be an object")
        _validate_sequence(raw_checkpoint.get("sequence", expected_sequence), expected_sequence)
        self.record_point_checkpoint(
            raw_checkpoint.get("semantic_event"),
            raw_checkpoint.get("points", {}),
            detail=raw_checkpoint.get("detail", ""),
            replay_frame_index=raw_checkpoint.get("replay_frame_index"),
        )

    def _restore_important_event(self, raw_event: Any, expected_sequence: int) -> None:
        if not isinstance(raw_event, Mapping):
            raise MatchMetricsError("each important event must be an object")
        _validate_sequence(raw_event.get("sequence", expected_sequence), expected_sequence)
        self.record_important_event(
            raw_event.get("title"),
            raw_event.get("detail", ""),
            replay_frame_index=raw_event.get("replay_frame_index"),
        )


# The explicit name is useful at integration sites; the short name keeps result
# and persistence code pleasant to read.
MatchMetricsTracker = MatchMetrics


def serialize_match_metrics(metrics: MatchMetrics) -> dict[str, Any]:
    if not isinstance(metrics, MatchMetrics):
        raise TypeError("metrics must be a MatchMetrics instance")
    return metrics.to_dict()


def restore_match_metrics(document: Mapping[str, Any]) -> MatchMetrics:
    return MatchMetrics.from_dict(document)


def _validated_player_id(value: Any) -> str:
    return _validated_text(
        value,
        field="player_id",
        maximum=MAX_PLAYER_ID_LENGTH,
        allow_empty=False,
    )


def _validated_text(
    value: Any,
    *,
    field: str,
    maximum: int,
    allow_empty: bool,
) -> str:
    if not isinstance(value, str):
        raise MatchMetricsError(f"{field} must be a string")
    if not allow_empty and not value:
        raise MatchMetricsError(f"{field} must not be empty")
    if len(value) > maximum:
        raise MatchMetricsError(f"{field} must be at most {maximum} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise MatchMetricsError(f"{field} contains control characters")
    return value


def _validated_counter(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MatchMetricsError(f"{field} must be an integer")
    if not 0 <= value <= MAX_COUNTER_VALUE:
        raise MatchMetricsError(
            f"{field} must be between 0 and {MAX_COUNTER_VALUE}"
        )
    return value


def _validated_expected_units(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MatchMetricsError("expected_production_units must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise MatchMetricsError("expected_production_units must be finite")
    if not 0 <= value <= MAX_COUNTER_VALUE:
        raise MatchMetricsError(
            "expected_production_units must be between "
            f"0 and {MAX_COUNTER_VALUE}"
        )
    return value


def _validated_derived_luck(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MatchMetricsError("luck_index must be a number")
    if not math.isfinite(float(value)) or value < 0:
        raise MatchMetricsError("luck_index must be a finite non-negative number")


def _validated_frame_index(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MatchMetricsError("replay_frame_index must be a non-negative integer")
    if value > MAX_COUNTER_VALUE:
        raise MatchMetricsError(
            f"replay_frame_index must not exceed {MAX_COUNTER_VALUE}"
        )
    return value


def _validated_points(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise MatchMetricsError("points must be an object")
    points: dict[str, int] = {}
    for raw_player_id, raw_points in value.items():
        player_id = _validated_player_id(raw_player_id)
        points[player_id] = _validated_counter(raw_points, field="points")
    return points


def _validate_document_header(document: Mapping[str, Any]) -> None:
    if "format" in document and document["format"] != MATCH_METRICS_FORMAT:
        raise MatchMetricsError("unsupported match metrics format")
    if "version" not in document:
        return
    version = document["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise MatchMetricsError("match metrics version must be an integer")
    if version != MATCH_METRICS_VERSION:
        raise MatchMetricsError(f"unsupported match metrics version: {version}")


def _validate_sequence(value: Any, expected: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MatchMetricsError("sequence must be an integer")
    if value != expected:
        raise MatchMetricsError(
            f"sequence must be contiguous (expected {expected}, got {value})"
        )


def _increment_counter(player: PlayerMatchMetrics, field: str, amount: int) -> None:
    _validated_increment(player, field, amount)
    setattr(player, field, getattr(player, field) + amount)


def _validated_increment(
    player: PlayerMatchMetrics,
    field: str,
    amount: int,
) -> None:
    new_value = getattr(player, field) + amount
    if new_value > MAX_COUNTER_VALUE:
        raise MatchMetricsError(f"{field} must not exceed {MAX_COUNTER_VALUE}")


__all__ = (
    "ImportantMatchEvent",
    "MATCH_METRICS_FORMAT",
    "MATCH_METRICS_VERSION",
    "MatchMetrics",
    "MatchMetricsError",
    "MatchMetricsTracker",
    "PlayerMatchMetrics",
    "PointCheckpoint",
    "restore_match_metrics",
    "serialize_match_metrics",
)
