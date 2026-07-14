import concurrent.futures
import json
import multiprocessing
import random

import pytest

from game.self_play import (
    ACTION_COUNT_KEYS,
    SUPPORTED_AI_PERSONALITIES,
    SelfPlayWorkerError,
    _MatchJob,
    _prepare_game,
    _resolve_worker_count,
    _run_match_job,
    _validate_completed_state,
    normalise_personalities,
    parse_personality_lineup,
    run_batch,
    run_match,
)


def test_self_play_is_reproducible_and_returns_structured_results():
    first = run_match(
        match_seed=31415,
        board_seed=2718,
        player_count=4,
        victory_target=5,
    )
    second = run_match(
        match_seed=31415,
        board_seed=2718,
        player_count=4,
        victory_target=5,
    )

    assert first == second
    assert first.completed is True
    assert first.reason == "victory"
    assert first.winner_seat in (1, 2, 3, 4)
    assert first.starting_player_seat == first.turn_order[0]
    assert sorted(first.turn_order) == [1, 2, 3, 4]
    assert first.turns == sum(first.dice_counts.values())
    assert set(first.dice_counts) == set(range(2, 13))
    assert len(first.players) == 4
    assert sum(player.won for player in first.players) == 1
    assert first.validation_errors == ()
    assert tuple(player.personality for player in first.players) == (
        "standard",
        "expansion",
        "trader",
        "disruptor",
    )
    assert all(set(player.action_counts) == set(ACTION_COUNT_KEYS) for player in first.players)
    assert all(
        player.action_counts["knights_used"] == player.played_knights
        for player in first.players
    )
    assert all(
        player.action_counts["domestic_trades_completed"]
        <= player.action_counts["domestic_trade_offers"]
        for player in first.players
    )
    assert all(player.initial_pips > 0 for player in first.players)
    assert all(1 <= player.initial_resource_diversity <= 5 for player in first.players)
    assert json.loads(json.dumps(first.to_dict()))["winner_seat"] == first.winner_seat


def test_self_play_restores_the_callers_global_random_state():
    random.seed(8675309)
    expected = [random.random() for _ in range(4)]
    random.seed(8675309)

    run_match(
        match_seed=7,
        board_seed=8,
        player_count=3,
        victory_target=5,
        max_turns=1,
    )

    assert [random.random() for _ in range(4)] == expected


def test_self_play_reports_a_turn_limit_without_hanging():
    result = run_match(
        match_seed=99,
        board_seed=100,
        victory_target=10,
        max_turns=1,
    )

    assert result.completed is False
    assert result.reason == "turn_limit"
    assert result.winner_seat is None
    assert result.turns == 1
    assert sum(result.dice_counts.values()) == 1


def test_batch_average_excludes_incomplete_matches():
    result = run_batch(
        match_seeds=(5, 6),
        board_mode="fully_random",
        victory_target=10,
        max_turns=1,
    )

    assert result.completed_games == 0
    assert result.average_turns == 0.0


def test_batch_can_hold_a_board_seed_fixed_across_match_seeds():
    progress = []
    result = run_batch(
        match_seeds=(40, 41),
        board_seed=2026,
        board_mode="fully_random",
        victory_target=5,
        progress=lambda index, total, match: progress.append(
            (index, total, match.match_seed)
        ),
    )

    assert result.game_count == 2
    assert result.completed_games == 2
    assert result.board_seed == 2026
    assert [match.match_seed for match in result.matches] == [40, 41]
    assert {match.board_seed for match in result.matches} == {2026}
    assert sum(result.win_counts.values()) == 2
    assert progress == [(1, 2, 40), (2, 2, 41)]
    json.dumps(result.to_dict())


def test_batch_rotates_personalities_across_seats_deterministically():
    first = run_batch(
        match_seeds=(10, 11, 12, 13),
        player_count=4,
        victory_target=10,
        max_turns=1,
    )
    second = run_batch(
        match_seeds=(10, 11, 12, 13),
        player_count=4,
        victory_target=10,
        max_turns=1,
    )

    assert first == second
    lineups = [
        tuple(player.personality for player in match.players)
        for match in first.matches
    ]
    assert lineups == [
        ("standard", "expansion", "trader", "disruptor"),
        ("expansion", "trader", "disruptor", "standard"),
        ("trader", "disruptor", "standard", "expansion"),
        ("disruptor", "standard", "expansion", "trader"),
    ]
    for seat in range(4):
        assert {lineup[seat] for lineup in lineups} == set(SUPPORTED_AI_PERSONALITIES)


def test_custom_personality_lineup_allows_mirror_matches_and_rotates():
    result = run_batch(
        match_seeds=(20, 21),
        player_count=2,
        personalities=("trader", "trader"),
        victory_target=10,
        max_turns=1,
    )

    assert result.personality_lineup == ("trader", "trader")
    assert all(
        tuple(player.personality for player in match.players) == ("trader", "trader")
        for match in result.matches
    )


@pytest.mark.parametrize(
    ("personalities", "player_count", "error"),
    [
        (("standard",), 2, ValueError),
        (("standard", "unknown"), 2, ValueError),
        ("standard,trader", 2, TypeError),
        (("standard", 1), 2, TypeError),
    ],
)
def test_personality_lineup_validation(personalities, player_count, error):
    with pytest.raises(error):
        normalise_personalities(personalities, player_count)


def test_personality_cli_parser_trims_names_and_rejects_empty_values():
    assert parse_personality_lineup("standard, trader") == ("standard", "trader")
    with pytest.raises(ValueError):
        parse_personality_lineup("standard,")


def test_self_play_integrity_check_detects_piece_count_corruption():
    caller_state = random.getstate()
    try:
        game = _prepare_game(
            match_seed=1,
            board_seed=2,
            board_mode="fully_random",
            player_count=3,
            victory_target=5,
        )
        game.players[0].roads_remaining -= 1

        assert "CPU1: road pieces" in _validate_completed_state(game)
    finally:
        random.setstate(caller_state)


def test_headless_player_renaming_keeps_stable_metric_seats_and_names():
    caller_state = random.getstate()
    try:
        game = _prepare_game(
            match_seed=11,
            board_seed=22,
            board_mode="constrained",
            player_count=4,
            victory_target=5,
        )

        assert [row.player_id for row in game.match_metrics.players] == [
            "seat-1",
            "seat-2",
            "seat-3",
            "seat-4",
        ]
        assert [row.display_name for row in game.match_metrics.players] == [
            "CPU1",
            "CPU2",
            "CPU3",
            "CPU4",
        ]
    finally:
        random.setstate(caller_state)


def test_parallel_batch_matches_sequential_order_and_preserves_parent_random_state():
    options = {
        "match_seeds": (301, 302, 303),
        "board_mode": "fully_random",
        "player_count": 4,
        "victory_target": 10,
        "max_turns": 1,
    }
    sequential = run_batch(**options, workers=1)
    progress = []
    random.seed(24680)
    parent_state = random.getstate()

    parallel = run_batch(
        **options,
        workers=2,
        progress=lambda index, total, match: progress.append(
            (index, total, match.match_seed)
        ),
    )

    assert parallel.matches == sequential.matches
    assert parallel.win_counts == sequential.win_counts
    assert parallel.average_turns == sequential.average_turns
    assert parallel.worker_count == 2
    assert sequential.worker_count == 1
    assert progress == [(1, 3, 301), (2, 3, 302), (3, 3, 303)]
    assert random.getstate() == parent_state
    assert parallel.to_dict()["worker_count"] == 2


def test_worker_count_validation_and_auto_cap(monkeypatch):
    monkeypatch.setattr("game.self_play.os.cpu_count", lambda: 64)

    assert _resolve_worker_count(0, 100) == 8
    assert _resolve_worker_count(0, 3) == 3
    assert _resolve_worker_count(32, 2) == 2
    assert _resolve_worker_count(1, 20) == 1

    for workers in (-1, 33):
        with pytest.raises(ValueError):
            _resolve_worker_count(workers, 2)
    for workers in (True, 1.5, "2"):
        with pytest.raises(TypeError):
            _resolve_worker_count(workers, 2)


def test_single_match_does_not_create_a_process_pool(monkeypatch):
    def fail_if_created(*_args, **_kwargs):
        raise AssertionError("one effective worker must not create a process pool")

    monkeypatch.setattr(
        "game.self_play.concurrent.futures.ProcessPoolExecutor",
        fail_if_created,
    )
    result = run_batch(
        match_seeds=(404,),
        victory_target=10,
        max_turns=1,
        workers=32,
    )

    assert result.worker_count == 1
    assert [match.match_seed for match in result.matches] == [404]


def test_spawn_worker_wraps_failure_with_match_identity():
    job = _MatchJob(
        index=7,
        match_seed=505,
        board_seed=606,
        board_mode="not-a-mode",
        player_count=2,
        victory_target=10,
        max_turns=1,
        max_action_steps=10,
        personalities=("standard", "trader"),
    )
    context = multiprocessing.get_context("spawn")

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=1,
        mp_context=context,
    ) as executor:
        future = executor.submit(_run_match_job, job)
        with pytest.raises(SelfPlayWorkerError) as caught:
            future.result()

    assert caught.value.match_index == 7
    assert caught.value.match_seed == 505
    assert "unsupported board mode" in caught.value.detail
    assert "match 7" in str(caught.value)
    assert "seed=505" in str(caught.value)
