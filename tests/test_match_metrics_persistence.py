import json
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from game.game import CatanGame
from game.match_metrics import MatchMetrics, serialize_match_metrics
from game.persistence import SaveGameError, restore_game, serialize_game


@pytest.fixture
def game():
    pygame.init()
    pygame.display.set_mode((1, 1))
    instance = CatanGame(
        board_seed=7373,
        ai_player_count=1,
        ai_action_delay_ms=0,
    )
    instance.configure_players(2, reset_logs=False)
    yield instance
    instance.audio.stop()
    pygame.quit()


def populated_metrics(game):
    metrics = MatchMetrics()
    human, cpu = game.players
    metrics.register_player("seat-0", human.name)
    metrics.register_player("seat-1", cpu.name)
    metrics.record_build("seat-0", "settlement", count=2)
    metrics.record_build("seat-0", "road", count=3)
    metrics.record_build("seat-1", "city")
    metrics.record_domestic_trade("seat-0", "seat-1")
    metrics.record_bank_trade("seat-1", count=2)
    metrics.record_production("seat-0", actual_units=5, expected_units=4.5)
    metrics.record_point_checkpoint(
        "main_started",
        {"seat-0": 2, "seat-1": 2},
        replay_frame_index=3,
    )
    metrics.record_important_event(
        "最長交易路",
        f"{human.name} が獲得",
        replay_frame_index=12,
    )
    return metrics


def test_match_metrics_round_trip_through_json(game):
    game.match_metrics = populated_metrics(game)
    expected = serialize_match_metrics(game.match_metrics)

    document = json.loads(json.dumps(serialize_game(game), ensure_ascii=False))
    game.match_metrics = MatchMetrics()
    restore_game(game, document, runtime_side_effects=False)

    assert serialize_match_metrics(game.match_metrics) == expected


def test_legacy_save_without_match_metrics_restores_empty_tracker(game):
    game.match_metrics = populated_metrics(game)
    legacy_document = serialize_game(game)
    legacy_document.pop("match_metrics")

    restore_game(game, legacy_document, runtime_side_effects=False)

    assert isinstance(game.match_metrics, MatchMetrics)
    assert game.match_metrics.players == ()
    assert game.match_metrics.point_checkpoints == ()
    assert game.match_metrics.important_events == ()


def test_serialize_without_match_metrics_attribute_uses_empty_document(game):
    if hasattr(game, "match_metrics"):
        del game.match_metrics

    document = serialize_game(game)

    assert document["match_metrics"] == MatchMetrics().to_dict()


def test_invalid_match_metrics_document_is_wrapped_as_save_game_error(game):
    document = serialize_game(game)
    document["match_metrics"] = {
        "format": "not-catan-match-metrics",
        "version": 1,
    }

    with pytest.raises(SaveGameError, match="対局メトリクス"):
        restore_game(game, document, runtime_side_effects=False)


def test_invalid_match_metrics_tracker_is_wrapped_on_serialize(game):
    game.match_metrics = object()

    with pytest.raises(SaveGameError, match="対局メトリクス"):
        serialize_game(game)
