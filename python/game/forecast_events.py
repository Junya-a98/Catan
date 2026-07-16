"""Pure domain model for the forecast-events match variant.

The catalog contains declarations only.  Saves retain stable event IDs and a
private draw pile; executable callbacks and presentation text never cross the
persistence or network boundary.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import random
import re
from types import MappingProxyType
from typing import Any


FORECAST_EVENTS_KIND = "forecast_events"
FORECAST_CATALOG_ID = "core_v1"
WHEAT_HARVEST_EVENT_ID = "wheat_harvest_v1"
SHEEP_DROUGHT_EVENT_ID = "sheep_drought_v1"

DEFAULT_FORECAST_OPTIONS = MappingProxyType(
    {
        "catalog": FORECAST_CATALOG_ID,
        "forecast_lead_turns": 2,
        "event_interval_turns": 6,
    }
)
_OPTION_KEYS = frozenset(DEFAULT_FORECAST_OPTIONS)
_PUBLIC_KEYS = frozenset(
    {"completed_turns", "forecast", "active_effects", "resolved_count"}
)
_FORECAST_KEYS = frozenset(
    {"event_id", "announced_turn", "resolve_turn"}
)
_ACTIVE_KEYS = frozenset(
    {"event_id", "started_turn", "expires_turn"}
)
_PRIVATE_KEYS = frozenset(
    {"deck_seed", "deck_cycle", "draw_pile", "discard_pile"}
)
_SEED_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_MAX_SAFE_INTEGER = 9_007_199_254_740_991


class ForecastEventError(ValueError):
    """Raised when forecast configuration or runtime state is malformed."""


@dataclass(frozen=True)
class ForecastEventDefinition:
    event_id: str
    title: str
    description: str
    active_description: str
    duration: str


@dataclass(frozen=True)
class ForecastTurnUpdate:
    completed_turns: int
    activated_event_id: str | None = None
    announced_event_id: str | None = None
    refreshed_event_id: str | None = None
    expired_event_ids: tuple[str, ...] = ()


EVENT_CATALOG = MappingProxyType(
    {
        WHEAT_HARVEST_EVENT_ID: ForecastEventDefinition(
            event_id=WHEAT_HARVEST_EVENT_ID,
            title="豊作",
            description=(
                "次に麦が通常生産されたとき、銀行在庫に余裕があれば"
                "生産対象の各プレイヤーへ麦を1枚追加します。"
            ),
            active_description="次の麦生産に追加ボーナス",
            duration="until_triggered",
        ),
        SHEEP_DROUGHT_EVENT_ID: ForecastEventDefinition(
            event_id=SHEEP_DROUGHT_EVENT_ID,
            title="大干ばつ",
            description=(
                "発動から全員が1手番を終えるまで、羊タイルは"
                "資源を生産しません。"
            ),
            active_description="羊タイルの生産停止",
            duration="one_round",
        ),
    }
)

# A small shuffle bag keeps both events equally frequent.  The order is
# generated from the authority-only seed and never included in public state.
_CORE_DECK = (
    WHEAT_HARVEST_EVENT_ID,
    SHEEP_DROUGHT_EVENT_ID,
    WHEAT_HARVEST_EVENT_ID,
    SHEEP_DROUGHT_EVENT_ID,
)


def canonical_forecast_options(options: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a fresh canonical options document."""

    if not isinstance(options, Mapping) or set(options) != _OPTION_KEYS:
        raise ForecastEventError("forecast_events options の項目が不正です。")
    if options.get("catalog") != FORECAST_CATALOG_ID:
        raise ForecastEventError("未対応のforecast event catalogです。")
    lead = _integer(
        options.get("forecast_lead_turns"),
        "forecast_lead_turns",
        minimum=1,
        maximum=12,
    )
    interval = _integer(
        options.get("event_interval_turns"),
        "event_interval_turns",
        minimum=4,
        maximum=40,
    )
    if lead >= interval:
        raise ForecastEventError("予告手番数はイベント間隔より短くしてください。")
    return {
        "catalog": FORECAST_CATALOG_ID,
        "forecast_lead_turns": lead,
        "event_interval_turns": interval,
    }


def create_initial_forecast_documents(
    options: Mapping[str, Any],
    *,
    deck_seed: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create the first public forecast and its authority-private deck."""

    canonical = canonical_forecast_options(options)
    if deck_seed is None:
        # Derive an independent deterministic seed without consuming the
        # shared RNG used by dice, robber theft and the development deck.
        # Headless simulations that seed ``random`` therefore remain exactly
        # reproducible, while the unrevealed event order stays server-only.
        random_state = repr(random.getstate()).encode("utf-8")
        deck_seed = hashlib.sha256(
            b"catan:forecast-events:core-v1\0" + random_state
        ).hexdigest()
    _validate_seed(deck_seed)
    draw_pile = list(_deck_for_cycle(deck_seed, 0))
    event_id = draw_pile.pop(0)
    public = {
        "completed_turns": 0,
        "forecast": {
            "event_id": event_id,
            "announced_turn": 0,
            "resolve_turn": canonical["forecast_lead_turns"],
        },
        "active_effects": [],
        "resolved_count": 0,
    }
    private = {
        "deck_seed": deck_seed,
        "deck_cycle": 0,
        "draw_pile": draw_pile,
        "discard_pile": [],
    }
    validate_forecast_documents(public, private)
    validate_forecast_schedule(public, canonical)
    return public, private


def validate_forecast_public(public: Mapping[str, Any]) -> None:
    """Validate the complete viewer-safe runtime document."""

    if not isinstance(public, Mapping) or set(public) != _PUBLIC_KEYS:
        raise ForecastEventError("forecast public state の項目が不正です。")
    completed = _integer(
        public.get("completed_turns"),
        "completed_turns",
        minimum=0,
    )
    resolved_count = _integer(
        public.get("resolved_count"),
        "resolved_count",
        minimum=0,
    )
    forecast = public.get("forecast")
    if not isinstance(forecast, Mapping) or set(forecast) != _FORECAST_KEYS:
        raise ForecastEventError("forecast state が不正です。")
    event_id = _event_id(forecast.get("event_id"))
    announced = _integer(
        forecast.get("announced_turn"),
        "announced_turn",
        minimum=0,
    )
    resolves = _integer(
        forecast.get("resolve_turn"),
        "resolve_turn",
        minimum=1,
    )
    if announced > completed or resolves <= completed or resolves <= announced:
        raise ForecastEventError("イベント予告の手番関係が不正です。")

    active = public.get("active_effects")
    if not _is_sequence(active) or len(active) > len(EVENT_CATALOG):
        raise ForecastEventError("active_effects が不正です。")
    active_ids = []
    for item in active:
        if not isinstance(item, Mapping) or set(item) != _ACTIVE_KEYS:
            raise ForecastEventError("active effect の項目が不正です。")
        active_id = _event_id(item.get("event_id"))
        started = _integer(
            item.get("started_turn"),
            "started_turn",
            minimum=0,
        )
        if started > completed:
            raise ForecastEventError("active effect の開始手番が不正です。")
        expires = item.get("expires_turn")
        definition = EVENT_CATALOG[active_id]
        if definition.duration == "until_triggered":
            if expires is not None:
                raise ForecastEventError("豊作の期限指定が不正です。")
        else:
            expires = _integer(expires, "expires_turn", minimum=1)
            if expires <= completed:
                raise ForecastEventError("active effect の期限が切れています。")
        active_ids.append(active_id)
    if len(active_ids) != len(set(active_ids)):
        raise ForecastEventError("同じevent effectが重複しています。")
    if resolved_count < len(active_ids):
        raise ForecastEventError("resolved_count がactive effect数より小さいです。")
    # Touch the forecast ID after all structural checks so unknown IDs always
    # produce the same bounded domain error.
    _event_id(event_id)


def validate_forecast_documents(
    public: Mapping[str, Any],
    private: Mapping[str, Any],
) -> None:
    """Validate a full authority state and its private shuffle bag."""

    validate_forecast_public(public)
    if not isinstance(private, Mapping) or set(private) != _PRIVATE_KEYS:
        raise ForecastEventError("forecast private state の項目が不正です。")
    seed = private.get("deck_seed")
    _validate_seed(seed)
    cycle = _integer(private.get("deck_cycle"), "deck_cycle", minimum=0)
    draw_pile = private.get("draw_pile")
    discard_pile = private.get("discard_pile")
    if not _is_sequence(draw_pile) or not _is_sequence(discard_pile):
        raise ForecastEventError("forecast event deck が不正です。")
    if len(draw_pile) + len(discard_pile) != len(_CORE_DECK) - 1:
        raise ForecastEventError("forecast event deck の枚数が不正です。")
    for event_id in (*draw_pile, *discard_pile):
        _event_id(event_id)
    forecast_id = public["forecast"]["event_id"]
    if Counter((*draw_pile, *discard_pile, forecast_id)) != Counter(_CORE_DECK):
        raise ForecastEventError("forecast event deck の構成が不正です。")
    actual_order = tuple((*discard_pile, forecast_id, *draw_pile))
    if actual_order != _deck_for_cycle(seed, cycle):
        raise ForecastEventError("forecast event deck の順序が不正です。")
    expected_resolved = cycle * len(_CORE_DECK) + len(discard_pile)
    if public["resolved_count"] != expected_resolved:
        raise ForecastEventError("forecast event deck の進行位置が不正です。")


def validate_forecast_schedule(
    public: Mapping[str, Any],
    options: Mapping[str, Any],
) -> None:
    """Bind a runtime schedule to its canonical match configuration."""

    validate_forecast_public(public)
    canonical = canonical_forecast_options(options)
    resolved_count = public["resolved_count"]
    lead = canonical["forecast_lead_turns"]
    interval = canonical["event_interval_turns"]
    if resolved_count == 0:
        expected_announced = 0
        expected_resolve = lead
    else:
        expected_announced = lead + (resolved_count - 1) * interval
        expected_resolve = lead + resolved_count * interval
    forecast = public["forecast"]
    if (
        forecast["announced_turn"] != expected_announced
        or forecast["resolve_turn"] != expected_resolve
        or expected_resolve > _MAX_SAFE_INTEGER
    ):
        raise ForecastEventError("イベント予告が設定された周期と一致しません。")
    for effect in public["active_effects"]:
        started = effect["started_turn"]
        if started < lead or (started - lead) % interval != 0:
            raise ForecastEventError("active effectの開始手番が周期と一致しません。")


def advance_forecast_documents(
    public: Mapping[str, Any],
    private: Mapping[str, Any],
    options: Mapping[str, Any],
    *,
    player_count: int,
) -> tuple[dict[str, Any], dict[str, Any], ForecastTurnUpdate]:
    """Advance one completed main turn and resolve due event transitions."""

    canonical = canonical_forecast_options(options)
    validate_forecast_documents(public, private)
    validate_forecast_schedule(public, canonical)
    player_count = _integer(player_count, "player_count", minimum=2, maximum=4)
    next_public = {
        "completed_turns": public["completed_turns"],
        "forecast": dict(public["forecast"]),
        "active_effects": [
            dict(effect) for effect in public["active_effects"]
        ],
        "resolved_count": public["resolved_count"],
    }
    next_private = {
        "deck_seed": private["deck_seed"],
        "deck_cycle": private["deck_cycle"],
        "draw_pile": list(private["draw_pile"]),
        "discard_pile": list(private["discard_pile"]),
    }
    completed = next_public["completed_turns"] + 1
    next_public["completed_turns"] = completed

    retained_effects = []
    expired = []
    for effect in next_public["active_effects"]:
        expires = effect["expires_turn"]
        if expires is not None and expires <= completed:
            expired.append(effect["event_id"])
        else:
            retained_effects.append(effect)
    next_public["active_effects"] = retained_effects

    activated = None
    announced = None
    refreshed = None
    forecast = next_public["forecast"]
    if forecast["resolve_turn"] == completed:
        activated = forecast["event_id"]
        if any(
            effect["event_id"] == activated
            for effect in next_public["active_effects"]
        ):
            refreshed = activated
        next_public["active_effects"] = [
            effect
            for effect in next_public["active_effects"]
            if effect["event_id"] != activated
        ]
        expires_turn = (
            None
            if EVENT_CATALOG[activated].duration == "until_triggered"
            else completed + player_count
        )
        next_public["active_effects"].append(
            {
                "event_id": activated,
                "started_turn": completed,
                "expires_turn": expires_turn,
            }
        )
        next_public["resolved_count"] += 1
        next_private["discard_pile"].append(activated)
        if not next_private["draw_pile"]:
            next_private["deck_cycle"] += 1
            next_private["draw_pile"] = list(
                _deck_for_cycle(
                    next_private["deck_seed"],
                    next_private["deck_cycle"],
                )
            )
            next_private["discard_pile"] = []
        announced = next_private["draw_pile"].pop(0)
        next_public["forecast"] = {
            "event_id": announced,
            "announced_turn": completed,
            "resolve_turn": completed + canonical["event_interval_turns"],
        }

    validate_forecast_documents(next_public, next_private)
    validate_forecast_schedule(next_public, canonical)
    return (
        next_public,
        next_private,
        ForecastTurnUpdate(
            completed_turns=completed,
            activated_event_id=activated,
            announced_event_id=announced,
            refreshed_event_id=refreshed,
            expired_event_ids=tuple(expired),
        ),
    )


def consume_active_effect(
    public: Mapping[str, Any],
    event_id: str,
) -> tuple[dict[str, Any], bool]:
    """Remove one public effect after its trigger has been resolved."""

    validate_forecast_public(public)
    event_id = _event_id(event_id)
    # ``VariantState`` freezes nested mappings with ``MappingProxyType``.
    # Build a fresh JSON document explicitly instead of asking ``deepcopy``
    # to pickle those read-only wrappers.
    next_public = {
        "completed_turns": public["completed_turns"],
        "forecast": dict(public["forecast"]),
        "active_effects": [
            dict(effect)
            for effect in public["active_effects"]
            if effect["event_id"] != event_id
        ],
        "resolved_count": public["resolved_count"],
    }
    consumed = len(next_public["active_effects"]) != len(public["active_effects"])
    validate_forecast_public(next_public)
    return next_public, consumed


def active_event_ids(public: Mapping[str, Any]) -> tuple[str, ...]:
    validate_forecast_public(public)
    return tuple(effect["event_id"] for effect in public["active_effects"])


def forecast_event_id(public: Mapping[str, Any]) -> str:
    validate_forecast_public(public)
    return public["forecast"]["event_id"]


def event_definition(event_id: str) -> ForecastEventDefinition:
    return EVENT_CATALOG[_event_id(event_id)]


def _deck_for_cycle(
    seed: str,
    cycle: int,
) -> tuple[str, ...]:
    # core_v1 has two equally frequent effects.  Pick the secret starting
    # effect from the seed, then alternate them.  This avoids duplicate events
    # replacing an unresolved copy and also avoids repeats across cycle edges.
    # The same four-card order is reused in later cycles; changing this policy
    # requires a new catalog ID so existing saves remain reproducible.
    _integer(cycle, "deck_cycle", minimum=0)
    cards = list(enumerate(_CORE_DECK))
    cards.sort(
        key=lambda item: hashlib.sha256(
            f"{seed}:0:{item[0]}:{item[1]}".encode("ascii")
        ).digest()
    )
    first = cards[0][1]
    second = next(event_id for event_id in EVENT_CATALOG if event_id != first)
    return first, second, first, second


def _validate_seed(value: Any) -> None:
    if not isinstance(value, str) or _SEED_PATTERN.fullmatch(value) is None:
        raise ForecastEventError("forecast deck seed が不正です。")


def _event_id(value: Any) -> str:
    if not isinstance(value, str) or value not in EVENT_CATALOG:
        raise ForecastEventError("未知のforecast event IDです。")
    return value


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int = _MAX_SAFE_INTEGER,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ForecastEventError(f"{label}が不正です。")
    return value


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


__all__ = (
    "DEFAULT_FORECAST_OPTIONS",
    "EVENT_CATALOG",
    "FORECAST_CATALOG_ID",
    "FORECAST_EVENTS_KIND",
    "ForecastEventDefinition",
    "ForecastEventError",
    "ForecastTurnUpdate",
    "SHEEP_DROUGHT_EVENT_ID",
    "WHEAT_HARVEST_EVENT_ID",
    "active_event_ids",
    "advance_forecast_documents",
    "canonical_forecast_options",
    "consume_active_effect",
    "create_initial_forecast_documents",
    "event_definition",
    "forecast_event_id",
    "validate_forecast_documents",
    "validate_forecast_public",
    "validate_forecast_schedule",
)
