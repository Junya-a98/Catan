import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from game.dice_animation import RESULT_HOLD_MS, DiceAnimationOverlay
from game.game import CatanGame


def test_dice_animation_overlay_loads_assets_from_zip():
    pygame.init()
    pygame.display.set_mode((1, 1))
    try:
        overlay = DiceAnimationOverlay()
        assert overlay.available is True
        assert len(overlay.roll_frames) == 24
        assert len(overlay.face_images) == 12
        assert overlay.shadow_image is not None
    finally:
        pygame.quit()


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
    finally:
        game.audio.stop()
        pygame.quit()
