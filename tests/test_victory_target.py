import json
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from game.game import CatanGame
from game.persistence import SaveGameError, load_game, save_game, serialize_game


@pytest.fixture
def game(tmp_path):
    pygame.init()
    pygame.display.set_mode((1, 1))
    instance = CatanGame(board_seed=5150)
    instance.replay_dir = tmp_path / "replays"
    yield instance
    instance.audio.stop()
    pygame.quit()


def test_initial_setup_controls_victory_target_with_five_to_fifteen_range(game):
    assert game.victory_point_target == 10
    buttons = {button.action: button for button in game.build_buttons()}
    assert "（10）" in buttons["victory_target_decrease"].label
    assert "（10）" in buttons["victory_target_increase"].label

    for _ in range(10):
        game.handle_button_action("victory_target_increase")

    assert game.victory_point_target == 15
    buttons = {button.action: button for button in game.build_buttons()}
    assert buttons["victory_target_increase"].enabled is False

    for _ in range(20):
        game.handle_button_action("victory_target_decrease")

    assert game.victory_point_target == 5
    buttons = {button.action: button for button in game.build_buttons()}
    assert buttons["victory_target_decrease"].enabled is False
    assert ("勝利条件", "5 VP") in game.get_pre_game_board_summary()["rows"]
    assert "勝利5点" in game.get_side_panel_guidance()[1]


def test_victory_target_cannot_change_after_initial_dice_starts(game):
    game.start_main_phase()

    game.handle_button_action("victory_target_decrease")

    assert game.victory_point_target == 10


def test_winner_check_uses_configured_victory_target(game):
    game.victory_point_target = 5
    game.start_main_phase()
    player = game.get_current_player()
    player.victory_point_cards = 4

    game.check_for_winner(player)
    assert game.winner is None

    player.victory_point_cards = 5
    game.check_for_winner(player)

    assert game.winner is player
    assert game.phase == "finished"


def test_victory_target_round_trips_and_old_save_defaults_to_ten(game, tmp_path):
    game.victory_point_target = 14
    save_path = save_game(game, tmp_path / "target.json")

    game.victory_point_target = 5
    load_game(game, save_path)
    assert game.victory_point_target == 14
    assert serialize_game(game)["rules"]["victory_point_target"] == 14

    legacy_data = serialize_game(game)
    legacy_data.pop("rules")
    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(json.dumps(legacy_data), encoding="utf-8")

    game.victory_point_target = 15
    load_game(game, legacy_path)
    assert game.victory_point_target == 10


def test_out_of_range_saved_victory_target_is_rejected(game, tmp_path):
    save_path = save_game(game, tmp_path / "invalid-target.json")
    data = json.loads(save_path.read_text(encoding="utf-8"))
    data["rules"]["victory_point_target"] = 16
    save_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SaveGameError, match="勝利点"):
        load_game(game, save_path)
