import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from game.game import CatanGame


def test_initial_dice_tie_triggers_reroll_for_tied_players_only():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.configure_players(4, reset_logs=False)

        for value in (8, 8, 6, 5):
            game.resolve_initial_key_roll(value)

        assert game.initial_dice_phase is True
        assert [player.name for player in game.initial_dice_contenders] == ["Player1", "Player2"]
        assert game.initial_player_index == 0

        game.resolve_initial_key_roll(4)
        game.resolve_initial_key_roll(9)

        assert game.initial_dice_phase is False
        assert [player.name for player in game.turn_order] == ["Player2", "Player3", "Player4", "Player1"]
    finally:
        game.audio.stop()
        pygame.quit()


def test_initial_dice_ignores_lower_place_ties_once_starting_player_is_known():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.configure_players(4, reset_logs=False)

        for value in (8, 8, 6, 6):
            game.resolve_initial_key_roll(value)

        assert [player.name for player in game.initial_dice_contenders] == ["Player1", "Player2"]

        game.resolve_initial_key_roll(5)
        game.resolve_initial_key_roll(3)

        assert game.initial_dice_phase is False
        assert [player.name for player in game.turn_order] == ["Player1", "Player2", "Player3", "Player4"]
    finally:
        game.audio.stop()
        pygame.quit()
