import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from game.match_result import (
    MATCH_RESULT_FORMAT,
    MATCH_RESULT_VERSION,
    MatchResultError,
    build_match_result,
)


def _player(name, *, is_ai=False, personality="standard", victory_cards=0, knights=0):
    return {
        "name": name,
        "is_ai": is_ai,
        "ai_personality": personality,
        "victory_point_cards": victory_cards,
        "played_knights": knights,
    }


def _snapshot(
    *,
    buildings=(),
    roads=(),
    winner=None,
    longest=None,
    largest=None,
    event_title="ゲーム準備中",
    event_detail="",
    player1_cards=0,
):
    return {
        "format": "catan-local-save",
        "version": 1,
        "rules": {"victory_point_target": 6},
        "board": {
            "mode": "constrained",
            "seed": 314159,
            "buildings": list(buildings),
            "roads": list(roads),
        },
        "players": [
            _player(
                "Alice",
                is_ai=True,
                personality="expansion",
                victory_cards=player1_cards,
            ),
            _player("Bob", knights=4),
        ],
        "phase": {
            "winner": winner,
            "longest_road_owner": longest,
            "largest_army_owner": largest,
        },
        "history": {
            "latest_event": {
                "title": event_title,
                "detail": event_detail,
                "level": "success",
            }
        },
    }


def _replay_document():
    initial = _snapshot()
    settlement = _snapshot(
        buildings=(
            {"owner": 0, "type": "settlement"},
            {"owner": 1, "type": "settlement"},
        ),
        roads=(
            {"owner": 0},
            {"owner": 1},
        ),
        event_title="Aliceが開拓地を建設",
    )
    bank_trade = copy.deepcopy(settlement)
    bank_trade["history"]["latest_event"]["title"] = "Aliceが銀行交易"
    domestic_trade = copy.deepcopy(settlement)
    domestic_trade["history"]["latest_event"]["title"] = "AliceとBobの交易成立"
    final = _snapshot(
        buildings=(
            {"owner": 0, "type": "city"},
            {"owner": 0, "type": "settlement"},
            {"owner": 1, "type": "settlement"},
            {"owner": 1, "type": "settlement"},
        ),
        roads=(
            {"owner": 0},
            {"owner": 0},
            {"owner": 1},
        ),
        winner=0,
        longest=0,
        largest=1,
        event_title="Aliceの勝利",
        event_detail="6 VPに到達しました",
        player1_cards=1,
    )
    snapshots = (initial, settlement, bank_trade, domestic_trade, final)
    labels = (
        "ゲーム準備中",
        "Aliceが開拓地を建設",
        "Aliceが銀行交易",
        "AliceとBobの交易成立",
        "Aliceの勝利",
    )
    return {
        "format": "catan-local-replay",
        "version": 1,
        "metadata": {
            "board_mode": "constrained",
            "board_seed": 314159,
            "victory_point_target": 6,
        },
        "frames": [
            {
                "sequence": index,
                "elapsed_ms": index * 250,
                "label": labels[index],
                "snapshot": snapshot,
            }
            for index, snapshot in enumerate(snapshots)
        ],
    }


def test_replay_summary_builds_rankings_timeline_trade_counts_and_jump_events():
    replay = _replay_document()

    result = build_match_result(replay)

    assert result["format"] == MATCH_RESULT_FORMAT
    assert result["version"] == MATCH_RESULT_VERSION
    assert result["source"] == "replay"
    assert result["completed"] is True
    assert result["board"] == {"mode": "constrained", "seed": 314159}
    assert result["victory_target"] == 6
    assert result["winner"] == {"seat": 1, "name": "Alice"}
    assert result["replay"] == {"available": True, "frame_count": 5}

    alice, bob = result["standings"]
    assert alice == {
        "rank": 1,
        "seat": 1,
        "name": "Alice",
        "color": None,
        "is_ai": True,
        "personality": "expansion",
        "victory_points": 6,
        "winner": True,
        "roads": 2,
        "settlements": 1,
        "cities": 1,
        "played_knights": 0,
        "longest_road": True,
        "largest_army": False,
        "trades": {"bank": 1, "domestic": 1},
        "builds": {"roads": 2, "settlements": 2, "cities": 1},
        "luck_index": None,
        "vp_breakdown": {
            "settlements": {"count": 1, "points": 1},
            "cities": {"count": 1, "points": 2},
            "longest_road": {"awarded": True, "points": 2},
            "largest_army": {"awarded": False, "points": 0},
            "victory_point_cards": {"count": 1, "points": 1},
            "total": 6,
        },
    }
    assert bob["rank"] == 2
    assert bob["victory_points"] == 4
    assert bob["largest_army"] is True
    assert bob["vp_breakdown"] == {
        "settlements": {"count": 2, "points": 2},
        "cities": {"count": 0, "points": 0},
        "longest_road": {"awarded": False, "points": 0},
        "largest_army": {"awarded": True, "points": 2},
        "victory_point_cards": {"count": 0, "points": 0},
        "total": 4,
    }
    assert bob["trades"] == {"bank": 0, "domestic": 1}

    assert [entry["replay_frame_index"] for entry in result["vp_progression"]] == [0, 1, 4]
    assert result["vp_progression"][-1]["scores"] == [
        {"seat": 1, "victory_points": 6},
        {"seat": 2, "victory_points": 4},
    ]
    events = {event["title"]: event for event in result["important_events"]}
    assert events["Aliceが開拓地を建設"]["replay_frame_index"] == 1
    assert events["Aliceが銀行交易"]["category"] == "trade"
    assert events["AliceとBobの交易成立"]["replay_frame_index"] == 3
    assert events["Aliceの勝利"]["category"] == "victory"

    json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_structured_match_metrics_override_inferred_values_without_mutation():
    replay = _replay_document()
    replay_before = copy.deepcopy(replay)
    metrics = {
        "format": "catan-match-metrics",
        "version": 1,
        "completed": True,
        "winner_seat": 2,
        "winner_name": "Bob",
        "players": [
            {
                "player_id": "seat-1",
                "display_name": "Alice",
                "victory_points": 8,
                "roads_built": 9,
                "settlements_built": 4,
                "cities_built": 2,
                "luck_index": 1.25,
                "bank_trades": 7,
                "domestic_trades": 4,
            },
            {
                "player_id": "seat-2",
                "display_name": "Bob",
                "victory_points": 9,
                "cities_built": 3,
            },
        ],
        "point_checkpoints": [
            {
                "replay_frame_index": 3,
                "semantic_event": "計測済み更新",
                "points": {"seat-1": 8, "seat-2": 9},
            }
        ],
        "important_events": [
            {
                "frame_index": 3,
                "category": "turning_point",
                "title": "逆転",
                "detail": "Bobが首位へ",
            }
        ],
    }
    game = SimpleNamespace(match_metrics=metrics, replay_archive=replay)

    result = build_match_result(game)

    assert result["source"] == "match_metrics"
    assert result["winner"] == {"seat": 2, "name": "Bob"}
    assert [row["seat"] for row in result["standings"]] == [2, 1]
    alice = next(row for row in result["standings"] if row["seat"] == 1)
    assert alice["victory_points"] == 8
    assert alice["roads"] == 2
    assert alice["builds"] == {"roads": 9, "settlements": 4, "cities": 2}
    assert alice["trades"] == {"bank": 7, "domestic": 4}
    assert alice["luck_index"] == 1.25
    assert result["vp_progression"] == [
        {
            "sequence": 0,
            "replay_frame_index": 3,
            "elapsed_ms": None,
            "label": "計測済み更新",
            "scores": [
                {"seat": 1, "victory_points": 8},
                {"seat": 2, "victory_points": 9},
            ],
        }
    ]
    assert result["important_events"][0]["replay_frame_index"] == 3
    assert replay == replay_before
    assert game.match_metrics is metrics


def test_replay_archive_objects_are_supported_without_importing_replay_module():
    document = _replay_document()
    archive = SimpleNamespace(
        metadata=document["metadata"],
        frames=tuple(SimpleNamespace(**frame) for frame in document["frames"]),
    )

    result = build_match_result(replay=archive)

    assert result["source"] == "replay"
    assert result["winner"] == {"seat": 1, "name": "Alice"}
    assert result["important_events"][-1]["replay_frame_index"] == 4


def test_unfinished_results_never_reveal_victory_point_card_breakdowns():
    replay = {
        "frames": [
            {
                "sequence": 0,
                "snapshot": _snapshot(player1_cards=2),
            }
        ]
    }

    result = build_match_result(replay)

    assert result["completed"] is False
    assert all("vp_breakdown" not in row for row in result["standings"])


def test_inconsistent_legacy_total_omits_impossible_breakdown():
    replay = _replay_document()
    metrics = {
        "completed": True,
        "winner_seat": 1,
        "players": [{"seat": 1, "victory_points": 1}],
    }
    game = SimpleNamespace(match_metrics=metrics, replay_archive=replay)

    result = build_match_result(game)

    alice = next(row for row in result["standings"] if row["seat"] == 1)
    assert alice["victory_points"] == 1
    assert "vp_breakdown" not in alice


def test_live_game_fallback_needs_no_pygame_or_replay_and_is_read_only():
    first = SimpleNamespace(
        name="Human",
        is_ai=False,
        ai_personality="standard",
        victory_point_cards=0,
        played_knights=0,
        roads_remaining=12,
        settlements_remaining=3,
        cities_remaining=4,
    )
    second = SimpleNamespace(
        name="CPU",
        is_ai=True,
        ai_personality="disruptor",
        victory_point_cards=2,
        played_knights=3,
        roads_remaining=11,
        settlements_remaining=2,
        cities_remaining=3,
    )
    game = SimpleNamespace(
        players=[first, second],
        board=SimpleNamespace(roads=[], nodes=[]),
        winner=second,
        longest_road_owner=first,
        largest_army_owner=second,
        victory_point_target=9,
        board_mode="fully_random",
        board_seed=99,
        replay_archive=None,
        replay_recorder=None,
        latest_event={
            "title": "CPUの勝利",
            "detail": "9 VPに到達しました",
            "level": "success",
        },
    )
    before = copy.deepcopy(game.__dict__)

    result = build_match_result(game)

    assert result["source"] == "game"
    assert result["winner"] == {"seat": 2, "name": "CPU"}
    cpu = result["standings"][0]
    assert cpu["victory_points"] == 9  # 3 settlements + city + 2 hidden + army
    assert (cpu["roads"], cpu["settlements"], cpu["cities"]) == (4, 3, 1)
    assert cpu["vp_breakdown"] == {
        "settlements": {"count": 3, "points": 3},
        "cities": {"count": 1, "points": 2},
        "longest_road": {"awarded": False, "points": 0},
        "largest_army": {"awarded": True, "points": 2},
        "victory_point_cards": {"count": 2, "points": 2},
        "total": 9,
    }
    assert result["vp_progression"][0]["replay_frame_index"] is None
    assert result["important_events"][0]["replay_frame_index"] is None
    assert game.__dict__ == before


def test_module_import_does_not_import_pygame():
    project_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(project_root / "python")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import game.match_result; assert 'pygame' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr


def test_missing_or_unusable_sources_are_rejected():
    with pytest.raises(MatchResultError, match="ゲーム状態またはリプレイ"):
        build_match_result()
    with pytest.raises(MatchResultError, match="ゲーム状態またはリプレイ"):
        build_match_result({"frames": [{"snapshot": None}]})
