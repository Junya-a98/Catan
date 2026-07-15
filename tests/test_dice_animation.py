import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from game.dice_animation import RESULT_HOLD_MS, DiceAnimationOverlay
from game.dice import roll_two_dice
from game.game import CatanGame
from game.hex_tile import get_token_pip_count


def test_dice_animation_overlay_builds_two_d6_faces():
    pygame.init()
    pygame.display.set_mode((1, 1))
    try:
        overlay = DiceAnimationOverlay()
        assert overlay.available is True
        assert len(overlay.roll_frames) == 24
        assert len(overlay.face_images) == 6
        assert overlay.shadow_image is not None
    finally:
        pygame.quit()


def test_dice_animation_overlay_keeps_two_dice_result_values():
    pygame.init()
    pygame.display.set_mode((1, 1))
    try:
        overlay = DiceAnimationOverlay()
        overlay.start((3, 4), "ダイスロール")

        assert overlay.result_values == (3, 4)
        assert overlay.result_total == 7
    finally:
        pygame.quit()


def test_roll_two_dice_returns_two_d6_values():
    left, right = roll_two_dice()

    assert 1 <= left <= 6
    assert 1 <= right <= 6


def test_number_token_pips_match_catan_probabilities():
    assert get_token_pip_count(6) == 5
    assert get_token_pip_count(8) == 5
    assert get_token_pip_count(5) == 4
    assert get_token_pip_count(2) == 1


def test_main_roll_waits_for_animation_before_marking_dice_rolled():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        game.handle_roll_dice()

        assert game.has_active_dice_animation() is True
        assert game.dice_rolled is False

        game.dice_overlay.state = "result"
        game.dice_overlay.result_started_at = pygame.time.get_ticks() - RESULT_HOLD_MS - 1
        game.update_dice_animation()

        assert game.pending_dice_context is None
        assert game.dice_rolled is True
        assert game.last_dice_pair == game.dice_overlay.result_values
    finally:
        game.audio.stop()
        pygame.quit()


def test_headless_roll_keeps_exact_pair_for_remote_animation(monkeypatch):
    game = CatanGame(headless=True)
    try:
        game.start_main_phase()
        monkeypatch.setattr("game.game.roll_two_dice", lambda: (2, 6))

        game.handle_roll_dice()

        assert game.dice_rolled is True
        assert game.last_dice_pair == (2, 6)
    finally:
        game.audio.stop()
