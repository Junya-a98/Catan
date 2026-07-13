import json
import random

from game.self_play import _prepare_game, _validate_completed_state, run_batch, run_match


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
    assert all(player.personality == "standard" for player in first.players)
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
