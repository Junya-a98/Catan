"""Pygame-independent match summaries for result screens and web clients.

The public :func:`build_match_result` function only reads its inputs.  It can
summarise a live game, a replay archive/document, or both, without restoring a
replay frame into the game.  This keeps result generation safe for the local
UI and makes the returned, versioned dictionary suitable for JSON transport.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


MATCH_RESULT_FORMAT = "catan-match-result"
MATCH_RESULT_VERSION = 1

__all__ = (
    "MATCH_RESULT_FORMAT",
    "MATCH_RESULT_VERSION",
    "MatchResultError",
    "build_match_result",
)


class MatchResultError(ValueError):
    """Raised when neither a game nor a usable replay was supplied."""


def build_match_result(game: Any = None, *, replay: Any = None) -> dict[str, Any]:
    """Build a versioned, JSON-safe summary without mutating ``game``.

    ``replay`` accepts either ``ReplayArchive``-like objects or their persisted
    document dictionaries.  For convenience, an archive may also be passed as
    the first positional argument.  When a future game supplies structured
    ``match_metrics``, its standings, timeline, events, trade counts, and luck
    indices override values inferred from snapshots.
    """

    if replay is None and _looks_like_replay(game):
        replay, game = game, None
    if replay is None and game is not None:
        replay = _replay_from_game(game)

    frames = _normalise_frames(replay)
    if game is None and not frames:
        raise MatchResultError("対局結果にはゲーム状態またはリプレイが必要です。")

    final_snapshot = frames[-1]["snapshot"] if frames else None
    metrics = _match_metrics(game, replay, final_snapshot)

    if final_snapshot is not None:
        standings = _standings_from_snapshot(final_snapshot)
    else:
        standings = _standings_from_game(game)
    if frames:
        _apply_replay_trade_counts(standings, frames)
    standings = _apply_metric_standings(standings, metrics)
    _fill_cumulative_build_fallbacks(standings)

    winner_seat = _winner_seat(metrics, final_snapshot, game, standings)
    winner_name = _winner_name(metrics, winner_seat, standings)
    completed_value = _metric_value(metrics, "completed")
    completed = (
        bool(completed_value)
        if completed_value is not None
        else winner_seat is not None
    )
    if completed:
        _attach_vp_breakdowns(standings)
    standings = _rank_standings(standings, winner_seat)

    metric_timeline = _metric_value(
        metrics, "vp_progression", "score_timeline", "point_checkpoints"
    )
    if metric_timeline is not None:
        vp_progression = _normalise_metric_timeline(
            metric_timeline, standings, metrics
        )
    else:
        vp_progression = []
    timeline_from_metrics = bool(vp_progression)
    if not vp_progression:
        vp_progression = _timeline_from_frames(frames)
    if not vp_progression and standings:
        vp_progression = [_current_score_entry(standings)]

    metric_events = _metric_value(metrics, "important_events", "events")
    if metric_events is not None:
        important_events = _normalise_metric_events(metric_events)
    else:
        important_events = []
    if not important_events:
        important_events = _events_from_frames(frames)
    if not important_events:
        important_events = _events_from_game(game)

    board_mode, board_seed = _board_identity(final_snapshot, game, replay)
    victory_target = _victory_target(final_snapshot, game, replay, metrics)
    source = "match_metrics" if metrics else ("replay" if frames else "game")
    return {
        "format": MATCH_RESULT_FORMAT,
        "version": MATCH_RESULT_VERSION,
        "source": source,
        "completed": completed,
        "board": {"mode": board_mode, "seed": board_seed},
        "victory_target": victory_target,
        "winner": (
            {"seat": winner_seat, "name": winner_name}
            if winner_seat is not None
            else None
        ),
        "standings": standings,
        "vp_progression": vp_progression,
        "timeline_unit": "イベント" if timeline_from_metrics else "フレーム",
        "important_events": important_events,
        "replay": {
            "available": bool(frames),
            "frame_count": len(frames),
        },
    }


def _looks_like_replay(value: Any) -> bool:
    if isinstance(value, Mapping):
        return isinstance(value.get("frames"), Sequence)
    return value is not None and hasattr(value, "frames") and not hasattr(value, "players")


def _replay_from_game(game: Any) -> Any:
    archive = getattr(game, "replay_archive", None)
    if archive is not None:
        return archive
    recorder = getattr(game, "replay_recorder", None)
    if recorder is not None and getattr(recorder, "frames", None):
        return recorder
    return None


def _normalise_frames(replay: Any) -> list[dict[str, Any]]:
    if replay is None:
        return []
    raw_frames = replay.get("frames", ()) if isinstance(replay, Mapping) else getattr(replay, "frames", ())
    if isinstance(raw_frames, (str, bytes)) or not isinstance(raw_frames, Sequence):
        return []

    frames = []
    for index, raw_frame in enumerate(raw_frames):
        if isinstance(raw_frame, Mapping):
            snapshot = raw_frame.get("snapshot")
            sequence = raw_frame.get("sequence", index)
            elapsed_ms = raw_frame.get("elapsed_ms", 0)
            label = raw_frame.get("label", "")
        else:
            snapshot = getattr(raw_frame, "snapshot", None)
            sequence = getattr(raw_frame, "sequence", index)
            elapsed_ms = getattr(raw_frame, "elapsed_ms", 0)
            label = getattr(raw_frame, "label", "")
        if not isinstance(snapshot, Mapping):
            continue
        frames.append(
            {
                "replay_frame_index": index,
                "sequence": _integer(sequence, index, minimum=0),
                "elapsed_ms": _integer(elapsed_ms, 0, minimum=0),
                "label": _text(label),
                "snapshot": snapshot,
            }
        )
    return frames


def _match_metrics(game: Any, replay: Any, snapshot: Any) -> Mapping[str, Any]:
    candidates = []
    if game is not None:
        candidates.append(getattr(game, "match_metrics", None))
    metadata = replay.get("metadata") if isinstance(replay, Mapping) else getattr(replay, "metadata", None)
    if isinstance(metadata, Mapping):
        candidates.append(metadata.get("match_metrics"))
    if isinstance(snapshot, Mapping):
        candidates.append(snapshot.get("match_metrics"))

    for candidate in candidates:
        if callable(candidate):
            try:
                candidate = candidate()
            except TypeError:
                continue
        if not isinstance(candidate, Mapping):
            to_dict = getattr(candidate, "to_dict", None)
            if callable(to_dict):
                candidate = to_dict()
        if isinstance(candidate, Mapping):
            return candidate
    return {}


def _standings_from_snapshot(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_players = snapshot.get("players")
    if not isinstance(raw_players, Sequence) or isinstance(raw_players, (str, bytes)):
        return []
    board = snapshot.get("board") if isinstance(snapshot.get("board"), Mapping) else {}
    phase = snapshot.get("phase") if isinstance(snapshot.get("phase"), Mapping) else {}
    roads = board.get("roads") if isinstance(board.get("roads"), Sequence) else ()
    buildings = board.get("buildings") if isinstance(board.get("buildings"), Sequence) else ()

    road_counts = [0] * len(raw_players)
    settlement_counts = [0] * len(raw_players)
    city_counts = [0] * len(raw_players)
    building_points = [0] * len(raw_players)
    for road in roads:
        if not isinstance(road, Mapping):
            continue
        owner = _valid_player_index(road.get("owner"), len(raw_players))
        if owner is not None:
            road_counts[owner] += 1
    for building in buildings:
        if not isinstance(building, Mapping):
            continue
        owner = _valid_player_index(building.get("owner"), len(raw_players))
        if owner is None:
            continue
        building_type = _text(building.get("type", "settlement")).lower()
        if building_type == "city":
            city_counts[owner] += 1
            building_points[owner] += 2
        else:
            settlement_counts[owner] += 1
            building_points[owner] += 1

    longest_owner = _valid_player_index(phase.get("longest_road_owner"), len(raw_players))
    army_owner = _valid_player_index(phase.get("largest_army_owner"), len(raw_players))
    standings = []
    for index, raw_player in enumerate(raw_players):
        player = raw_player if isinstance(raw_player, Mapping) else {}
        points = building_points[index] + _integer(player.get("victory_point_cards"), 0, minimum=0)
        if index == longest_owner:
            points += 2
        if index == army_owner:
            points += 2
        standings.append(
            _standing(
                seat=index + 1,
                name=player.get("name", f"Player{index + 1}"),
                color=player.get("color"),
                is_ai=player.get("is_ai", False),
                personality=player.get("ai_personality", "standard"),
                victory_points=points,
                roads=road_counts[index],
                settlements=settlement_counts[index],
                cities=city_counts[index],
                played_knights=player.get("played_knights", 0),
                longest_road=index == longest_owner,
                largest_army=index == army_owner,
            )
        )
    return standings


def _standings_from_game(game: Any) -> list[dict[str, Any]]:
    players = list(getattr(game, "players", ()) or ())
    board = getattr(game, "board", None)
    roads = list(getattr(board, "roads", ()) or ())
    nodes = list(getattr(board, "nodes", ()) or ())
    get_points = getattr(game, "get_player_victory_points", None)
    standings = []
    for index, player in enumerate(players):
        player_roads = sum(getattr(road, "owner", None) is player for road in roads)
        settlements = 0
        cities = 0
        building_points = 0
        for node in nodes:
            building = getattr(node, "building", None)
            if building is None or getattr(building, "owner", None) is not player:
                continue
            raw_building_type = getattr(building, "building_type", None)
            building_type = getattr(raw_building_type, "value", raw_building_type)
            if _text(building_type).lower() == "city":
                cities += 1
                building_points += 2
            else:
                settlements += 1
                building_points += 1
        if not nodes:
            settlements = max(0, 5 - _integer(getattr(player, "settlements_remaining", 5), 5))
            cities = max(0, 4 - _integer(getattr(player, "cities_remaining", 4), 4))
            building_points = settlements + cities * 2
        if not roads:
            player_roads = max(0, 15 - _integer(getattr(player, "roads_remaining", 15), 15))

        if callable(get_points):
            try:
                points = _integer(get_points(player), 0, minimum=0)
            except Exception:
                points = _fallback_live_points(game, player, building_points)
        else:
            points = _fallback_live_points(game, player, building_points)
        standings.append(
            _standing(
                seat=index + 1,
                name=getattr(player, "name", f"Player{index + 1}"),
                color=getattr(player, "color", None),
                is_ai=getattr(player, "is_ai", False),
                personality=getattr(player, "ai_personality", "standard"),
                victory_points=points,
                roads=player_roads,
                settlements=settlements,
                cities=cities,
                played_knights=getattr(player, "played_knights", 0),
                longest_road=getattr(game, "longest_road_owner", None) is player,
                largest_army=getattr(game, "largest_army_owner", None) is player,
            )
        )
    return standings


def _fallback_live_points(game: Any, player: Any, building_points: int) -> int:
    points = building_points + _integer(getattr(player, "victory_point_cards", 0), 0, minimum=0)
    if getattr(game, "longest_road_owner", None) is player:
        points += 2
    if getattr(game, "largest_army_owner", None) is player:
        points += 2
    return points


def _standing(
    *,
    seat: Any,
    name: Any,
    color: Any = None,
    is_ai: Any,
    personality: Any,
    victory_points: Any,
    roads: Any,
    settlements: Any,
    cities: Any,
    played_knights: Any = 0,
    longest_road: Any = False,
    largest_army: Any = False,
    bank_trades: Any = None,
    domestic_trades: Any = None,
    luck_index: Any = None,
) -> dict[str, Any]:
    return {
        "rank": 0,
        "seat": _integer(seat, 1, minimum=1),
        "name": _text(name),
        "color": _colour(color),
        "is_ai": bool(is_ai),
        "personality": _text(personality or "standard"),
        "victory_points": _integer(victory_points, 0, minimum=0),
        "winner": False,
        "roads": _integer(roads, 0, minimum=0),
        "settlements": _integer(settlements, 0, minimum=0),
        "cities": _integer(cities, 0, minimum=0),
        "played_knights": _integer(played_knights, 0, minimum=0),
        "longest_road": bool(longest_road),
        "largest_army": bool(largest_army),
        "trades": {
            "bank": _optional_integer(bank_trades, minimum=0),
            "domestic": _optional_integer(domestic_trades, minimum=0),
        },
        "builds": {
            "roads": None,
            "settlements": None,
            "cities": None,
        },
        "luck_index": _optional_number(luck_index),
    }


def _apply_metric_standings(
    standings: list[dict[str, Any]], metrics: Mapping[str, Any]
) -> list[dict[str, Any]]:
    raw_rows = _metric_value(metrics, "standings", "players")
    rows = _metric_rows(raw_rows)
    if not rows:
        return standings

    by_seat = {row["seat"]: row for row in standings}
    by_name = _unique_standings_by_name(standings)
    for fallback_seat, raw in enumerate(rows, start=1):
        if not isinstance(raw, Mapping):
            continue
        seat = _optional_integer(raw.get("seat"), minimum=1)
        if seat is None:
            seat = _seat_from_player_id(raw.get("player_id"))
        name = _text(_first(raw, "name", "display_name", default=""))
        target = by_seat.get(seat) if seat is not None else None
        target = target or by_name.get(name)
        if target is None:
            target = by_seat.get(fallback_seat)
        if target is None:
            target = _standing(
                seat=seat or fallback_seat,
                name=name or f"Player{seat or fallback_seat}",
                color=raw.get("color"),
                is_ai=raw.get("is_ai", False),
                personality=raw.get("personality", raw.get("ai_personality", "standard")),
                victory_points=0,
                roads=0,
                settlements=0,
                cities=0,
            )
            standings.append(target)
            by_seat[target["seat"]] = target
            by_name[target["name"]] = target

        action_counts = raw.get("action_counts") if isinstance(raw.get("action_counts"), Mapping) else {}
        values = {
            "name": _first(raw, "name", "display_name"),
            "color": raw.get("color"),
            "is_ai": raw.get("is_ai"),
            "personality": raw.get("personality", raw.get("ai_personality")),
            "victory_points": _first(raw, "victory_points", "vp", "points"),
            "roads": raw.get("roads"),
            "settlements": raw.get("settlements"),
            "cities": raw.get("cities"),
            "played_knights": raw.get("played_knights"),
            "longest_road": raw.get("longest_road"),
            "largest_army": raw.get("largest_army"),
            "luck_index": _first(raw, "luck_index", "luck"),
        }
        for key, value in values.items():
            if value is None:
                continue
            if key in {"victory_points", "roads", "settlements", "cities", "played_knights"}:
                target[key] = _integer(value, target[key], minimum=0)
            elif key in {"is_ai", "longest_road", "largest_army"}:
                target[key] = bool(value)
            elif key == "color":
                target[key] = _colour(value)
            elif key == "luck_index":
                target[key] = _optional_number(value)
            else:
                target[key] = _text(value)
        bank_trades = _first(
            raw, "bank_trades", default=action_counts.get("bank_trades")
        )
        if bank_trades is not None:
            target["trades"]["bank"] = _optional_integer(bank_trades, minimum=0)
        domestic_trades = _first(
            raw,
            "domestic_trades",
            "domestic_trades_completed",
            default=action_counts.get("domestic_trades_completed"),
        )
        if domestic_trades is not None:
            target["trades"]["domestic"] = _optional_integer(
                domestic_trades, minimum=0
            )
        for source, destination in (
            ("roads_built", "roads"),
            ("settlements_built", "settlements"),
            ("cities_built", "cities"),
        ):
            if raw.get(source) is not None:
                target["builds"][destination] = _optional_integer(
                    raw[source], minimum=0
                )
    return standings


def _apply_replay_trade_counts(
    standings: list[dict[str, Any]], frames: list[dict[str, Any]]
) -> None:
    """Fill trade participation counts from semantic replay labels."""

    by_name = {row["name"]: row for row in standings}
    for row in standings:
        row["trades"] = {"bank": 0, "domestic": 0}
    for frame in frames:
        snapshot = frame["snapshot"]
        history = snapshot.get("history") if isinstance(snapshot, Mapping) else None
        latest = history.get("latest_event") if isinstance(history, Mapping) else None
        title = _text(
            latest.get("title")
            if isinstance(latest, Mapping) and latest.get("title")
            else frame["label"]
        )
        if "が銀行交易" in title:
            actor_name = title.split("が銀行交易", 1)[0]
            if actor_name in by_name:
                by_name[actor_name]["trades"]["bank"] += 1
        if "の交易成立" in title:
            participants = title.split("の交易成立", 1)[0].split("と")
            for name in participants:
                row = by_name.get(name)
                if row is not None:
                    row["trades"]["domestic"] += 1


def _fill_cumulative_build_fallbacks(standings: list[dict[str, Any]]) -> None:
    """Approximate cumulative builds for old matches without metric counters.

    The base game has no piece destruction.  Every city therefore represents
    one earlier settlement build, while every road still on the board was
    built exactly once.
    """

    for row in standings:
        builds = row["builds"]
        if builds["roads"] is None:
            builds["roads"] = row["roads"]
        if builds["settlements"] is None:
            builds["settlements"] = row["settlements"] + row["cities"]
        if builds["cities"] is None:
            builds["cities"] = row["cities"]


def _attach_vp_breakdowns(standings: list[dict[str, Any]]) -> None:
    """Expose the complete score composition only in the post-match result.

    ``settlements`` and ``cities`` are the pieces currently on the board, not
    the cumulative build counters.  The remaining points are victory-point
    cards: the base game and the forecast-events variant have no other hidden
    score source.  A redacted legacy snapshot without an authoritative final
    total cannot reconstruct cards that were never recorded, so it safely
    reports only the points present in that source.
    """

    for row in standings:
        settlement_count = _integer(row.get("settlements"), 0, minimum=0)
        city_count = _integer(row.get("cities"), 0, minimum=0)
        settlement_points = settlement_count
        city_points = city_count * 2
        longest_road_points = 2 if row.get("longest_road") else 0
        largest_army_points = 2 if row.get("largest_army") else 0
        public_points = (
            settlement_points
            + city_points
            + longest_road_points
            + largest_army_points
        )
        total = _integer(row.get("victory_points"), public_points, minimum=0)
        if public_points > total:
            # Metric adapters are deliberately permissive, but publishing a
            # mathematically impossible breakdown is worse than omitting the
            # optional explanation for that malformed legacy row.
            row.pop("vp_breakdown", None)
            continue
        victory_point_cards = max(0, total - public_points)
        row["vp_breakdown"] = {
            "settlements": {
                "count": settlement_count,
                "points": settlement_points,
            },
            "cities": {
                "count": city_count,
                "points": city_points,
            },
            "longest_road": {
                "awarded": bool(row.get("longest_road")),
                "points": longest_road_points,
            },
            "largest_army": {
                "awarded": bool(row.get("largest_army")),
                "points": largest_army_points,
            },
            "victory_point_cards": {
                "count": victory_point_cards,
                "points": victory_point_cards,
            },
            "total": total,
        }


def _metric_rows(value: Any) -> list[Any]:
    if isinstance(value, Mapping):
        rows = []
        for key, raw in value.items():
            if isinstance(raw, Mapping):
                row = dict(raw)
                if "seat" not in row:
                    parsed_seat = _optional_integer(key, minimum=1)
                    if parsed_seat is not None:
                        row["seat"] = parsed_seat
                    else:
                        row.setdefault("name", key)
                rows.append(row)
        return rows
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _seat_from_player_id(value: Any) -> int | None:
    player_id = _text(value)
    for prefix in ("seat-", "seat_", "seat:"):
        if player_id.startswith(prefix):
            return _optional_integer(player_id[len(prefix) :], minimum=1)
    return None


def _rank_standings(
    standings: list[dict[str, Any]], winner_seat: int | None
) -> list[dict[str, Any]]:
    ordered = sorted(
        standings,
        key=lambda row: (
            row["seat"] != winner_seat,
            -row["victory_points"],
            row["seat"],
        ),
    )
    previous_points = None
    previous_rank = 0
    for position, row in enumerate(ordered, start=1):
        if row["victory_points"] != previous_points:
            previous_rank = position
            previous_points = row["victory_points"]
        row["rank"] = previous_rank
        row["winner"] = row["seat"] == winner_seat
    return ordered


def _winner_seat(
    metrics: Mapping[str, Any],
    snapshot: Any,
    game: Any,
    standings: list[dict[str, Any]],
) -> int | None:
    metric_winner = _metric_value(metrics, "winner_seat")
    if metric_winner is None:
        winner = _metric_value(metrics, "winner")
        if isinstance(winner, Mapping):
            metric_winner = winner.get("seat")
        elif isinstance(winner, int) and not isinstance(winner, bool):
            metric_winner = winner
    seat = _optional_integer(metric_winner, minimum=1)
    if seat is not None:
        return seat

    for row in _metric_rows(_metric_value(metrics, "standings", "players")):
        if isinstance(row, Mapping) and bool(_first(row, "winner", "won", default=False)):
            seat = _optional_integer(row.get("seat"), minimum=1)
            if seat is None:
                seat = _seat_from_player_id(row.get("player_id"))
            if seat is not None:
                return seat
            name = _text(row.get("name", ""))
            match = next((item for item in standings if item["name"] == name), None)
            if match:
                return match["seat"]

    if isinstance(snapshot, Mapping):
        phase = snapshot.get("phase")
        players = snapshot.get("players")
        if isinstance(phase, Mapping) and isinstance(players, Sequence):
            index = _valid_player_index(phase.get("winner"), len(players))
            if index is not None:
                return index + 1

    if game is not None:
        winner = getattr(game, "winner", None)
        for index, player in enumerate(getattr(game, "players", ()) or (), start=1):
            if player is winner:
                return index

    return None


def _winner_name(
    metrics: Mapping[str, Any],
    winner_seat: int | None,
    standings: list[dict[str, Any]],
) -> str | None:
    value = _metric_value(metrics, "winner_name")
    if value is None:
        winner = _metric_value(metrics, "winner")
        if isinstance(winner, Mapping):
            value = winner.get("name")
    if value is not None:
        return _text(value)
    row = next((item for item in standings if item["seat"] == winner_seat), None)
    return row["name"] if row else None


def _timeline_from_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline = []
    previous_scores = None
    for frame in frames:
        standings = _standings_from_snapshot(frame["snapshot"])
        scores = [
            {"seat": row["seat"], "victory_points": row["victory_points"]}
            for row in standings
        ]
        score_signature = tuple((row["seat"], row["victory_points"]) for row in scores)
        if previous_scores is not None and score_signature == previous_scores:
            continue
        timeline.append(
            {
                "sequence": frame["sequence"],
                "replay_frame_index": frame["replay_frame_index"],
                "elapsed_ms": frame["elapsed_ms"],
                "label": frame["label"],
                "scores": scores,
            }
        )
        previous_scores = score_signature
    return timeline


def _current_score_entry(standings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "sequence": 0,
        "replay_frame_index": None,
        "elapsed_ms": None,
        "label": "現在の勝利点",
        "scores": [
            {"seat": row["seat"], "victory_points": row["victory_points"]}
            for row in sorted(standings, key=lambda item: item["seat"])
        ],
    }


def _normalise_metric_timeline(
    raw_timeline: Any,
    standings: list[dict[str, Any]],
    metrics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(raw_timeline, Sequence) or isinstance(raw_timeline, (str, bytes)):
        return []
    identity_to_seat = _metric_identity_to_seat(metrics, standings)
    timeline = []
    for raw in raw_timeline:
        if not isinstance(raw, Mapping):
            continue
        raw_scores = _first(raw, "scores", "victory_points", "points", default={})
        scores = _normalise_scores(raw_scores, identity_to_seat)
        if not scores:
            continue
        timeline.append(
            {
                "sequence": _integer(raw.get("sequence"), len(timeline), minimum=0),
                "replay_frame_index": _optional_integer(
                    _first(raw, "replay_frame_index", "frame_index"), minimum=0
                ),
                "elapsed_ms": _optional_integer(raw.get("elapsed_ms"), minimum=0),
                "label": _text(
                    _first(raw, "label", "semantic_event", default="勝利点更新")
                ),
                "scores": scores,
            }
        )
    return timeline


def _normalise_scores(
    raw_scores: Any, identity_to_seat: Mapping[str, int]
) -> list[dict[str, int]]:
    scores = []
    if isinstance(raw_scores, Mapping):
        for identity, points in raw_scores.items():
            seat = _optional_integer(identity, minimum=1)
            if seat is None:
                seat = identity_to_seat.get(_text(identity))
            if seat is not None:
                scores.append({"seat": seat, "victory_points": _integer(points, 0, minimum=0)})
    elif isinstance(raw_scores, Sequence) and not isinstance(raw_scores, (str, bytes)):
        for raw in raw_scores:
            if not isinstance(raw, Mapping):
                continue
            seat = _optional_integer(raw.get("seat"), minimum=1)
            if seat is None:
                identity = _first(raw, "name", "display_name", "player_id", default="")
                seat = identity_to_seat.get(_text(identity))
            points = _first(raw, "victory_points", "vp", "points")
            if seat is not None and points is not None:
                scores.append({"seat": seat, "victory_points": _integer(points, 0, minimum=0)})
    return sorted(scores, key=lambda item: item["seat"])


def _metric_identity_to_seat(
    metrics: Mapping[str, Any], standings: list[dict[str, Any]]
) -> dict[str, int]:
    unique_names = _unique_standings_by_name(standings)
    identities = {name: row["seat"] for name, row in unique_names.items()}
    for row in standings:
        identities[str(row["seat"])] = row["seat"]
        identities[f"seat-{row['seat']}"] = row["seat"]
        identities[f"seat_{row['seat']}"] = row["seat"]
    by_name = {name: row["seat"] for name, row in unique_names.items()}
    for fallback_seat, raw in enumerate(
        _metric_rows(_metric_value(metrics, "standings", "players")), start=1
    ):
        if not isinstance(raw, Mapping):
            continue
        display_name = _text(_first(raw, "display_name", "name", default=""))
        seat = _optional_integer(raw.get("seat"), minimum=1)
        if seat is None:
            seat = _seat_from_player_id(raw.get("player_id"))
        if seat is None:
            seat = by_name.get(display_name, fallback_seat)
        player_id = _text(raw.get("player_id", ""))
        if player_id:
            identities[player_id] = seat
        if display_name:
            identities[display_name] = seat
    return identities


def _unique_standings_by_name(
    standings: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    rows_by_name: dict[str, dict[str, Any] | None] = {}
    for row in standings:
        name = row["name"]
        rows_by_name[name] = row if name not in rows_by_name else None
    return {
        name: row
        for name, row in rows_by_name.items()
        if row is not None
    }


def _events_from_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    previous_scores = None
    for frame in frames:
        snapshot = frame["snapshot"]
        history = snapshot.get("history") if isinstance(snapshot, Mapping) else None
        latest = history.get("latest_event") if isinstance(history, Mapping) else None
        latest = latest if isinstance(latest, Mapping) else {}
        title = _text(latest.get("title") or frame["label"])
        detail = _text(latest.get("detail", ""))
        level = _text(latest.get("level", "info"))
        standings = _standings_from_snapshot(snapshot)
        scores = tuple((row["seat"], row["victory_points"]) for row in standings)
        score_changed = previous_scores is not None and scores != previous_scores
        category = _event_category(title, detail, score_changed=score_changed)
        previous_scores = scores
        if category is None:
            continue
        events.append(
            {
                "sequence": frame["sequence"],
                "replay_frame_index": frame["replay_frame_index"],
                "elapsed_ms": frame["elapsed_ms"],
                "category": category,
                "title": title,
                "detail": detail,
                "level": level,
            }
        )
    return events


def _events_from_game(game: Any) -> list[dict[str, Any]]:
    if game is None:
        return []
    latest = getattr(game, "latest_event", None)
    latest = latest if isinstance(latest, Mapping) else {}
    title = _text(latest.get("title", ""))
    detail = _text(latest.get("detail", ""))
    category = _event_category(title, detail, score_changed=False)
    if category is None:
        return []
    return [
        {
            "sequence": 0,
            "replay_frame_index": None,
            "elapsed_ms": None,
            "category": category,
            "title": title,
            "detail": detail,
            "level": _text(latest.get("level", "info")),
        }
    ]


def _normalise_metric_events(raw_events: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)):
        return []
    events = []
    for raw in raw_events:
        if not isinstance(raw, Mapping):
            continue
        title = _text(raw.get("title", raw.get("label", "")))
        detail = _text(raw.get("detail", ""))
        category = _text(raw.get("category", "")) or _event_category(
            title, detail, score_changed=False
        )
        events.append(
            {
                "sequence": _integer(raw.get("sequence"), len(events), minimum=0),
                "replay_frame_index": _optional_integer(
                    _first(raw, "replay_frame_index", "frame_index"), minimum=0
                ),
                "elapsed_ms": _optional_integer(raw.get("elapsed_ms"), minimum=0),
                "category": category or "event",
                "title": title,
                "detail": detail,
                "level": _text(raw.get("level", "info")),
            }
        )
    return events


def _event_category(title: str, detail: str, *, score_changed: bool) -> str | None:
    text = f"{title} {detail}"
    categories = (
        ("victory", ("勝利", "ゲーム終了")),
        ("award", ("最長交易路", "最大騎士力")),
        ("trade", ("交易成立", "銀行交易")),
        ("robber", ("盗賊", "略奪", "騎士を使用")),
        ("development", ("独占を使用", "収穫を使用", "街道建設を使用", "発展カード")),
        ("build", ("開拓地を", "都市へ", "街道を")),
        ("milestone", ("通常ゲームを開始",)),
    )
    for category, keywords in categories:
        if any(keyword in text for keyword in keywords):
            return category
    if score_changed:
        return "score"
    return None


def _board_identity(snapshot: Any, game: Any, replay: Any) -> tuple[str | None, int | None]:
    mode = seed = None
    if isinstance(snapshot, Mapping):
        board = snapshot.get("board")
        if isinstance(board, Mapping):
            mode = board.get("mode")
            seed = board.get("seed")
    metadata = replay.get("metadata") if isinstance(replay, Mapping) else getattr(replay, "metadata", None)
    if isinstance(metadata, Mapping):
        mode = mode if mode is not None else metadata.get("board_mode")
        seed = seed if seed is not None else metadata.get("board_seed")
    if game is not None:
        mode = mode if mode is not None else getattr(game, "board_mode", None)
        seed = seed if seed is not None else getattr(game, "board_seed", None)
    return (_text(mode) if mode is not None else None, _optional_integer(seed))


def _victory_target(snapshot: Any, game: Any, replay: Any, metrics: Mapping[str, Any]) -> int | None:
    value = _metric_value(metrics, "victory_target", "victory_point_target")
    if value is None and isinstance(snapshot, Mapping):
        rules = snapshot.get("rules")
        if isinstance(rules, Mapping):
            value = rules.get("victory_point_target")
    metadata = replay.get("metadata") if isinstance(replay, Mapping) else getattr(replay, "metadata", None)
    if value is None and isinstance(metadata, Mapping):
        value = metadata.get("victory_point_target")
    if value is None and game is not None:
        value = getattr(game, "victory_point_target", None)
    return _optional_integer(value, minimum=1)


def _metric_value(metrics: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in metrics:
            return metrics[key]
    return None


def _first(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _valid_player_index(value: Any, player_count: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value < player_count else None


def _integer(value: Any, default: int = 0, *, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        return default
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if minimum is not None and result < minimum:
        return default
    return result


def _optional_integer(value: Any, *, minimum: int | None = None) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if minimum is not None and result < minimum:
        return None
    return result


def _optional_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def _colour(value: Any) -> list[int] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    if len(value) < 3:
        return None
    channels = []
    for raw_channel in value[:3]:
        if isinstance(raw_channel, bool):
            return None
        try:
            channel = int(raw_channel)
        except (TypeError, ValueError, OverflowError):
            return None
        channels.append(max(0, min(255, channel)))
    return channels


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
