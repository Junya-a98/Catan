import pytest

from game import game as game_module
from game.game import CatanGame


def test_headless_game_skips_interactive_backends_and_replay(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("interactive backend must not be created")

    monkeypatch.setattr(game_module.pygame, "init", forbidden)
    monkeypatch.setattr(game_module.pygame.display, "set_mode", forbidden)
    monkeypatch.setattr(game_module, "GameAudio", forbidden)
    monkeypatch.setattr(game_module, "DiceAnimationOverlay", forbidden)
    monkeypatch.setattr(game_module, "ReplayRecorder", forbidden)

    game = CatanGame(board_mode="fully_random", board_seed=123, headless=True)

    assert game.headless is True
    assert game.screen is None
    assert game.clock is None
    assert game.buttons == []
    assert game.log_messages == []
    assert game.replay_recorder is None
    assert game.latest_replay_path is None


def test_headless_game_resolves_ai_dice_without_animation():
    game = CatanGame(board_mode="fully_random", board_seed=456, headless=True)
    game.players[0].is_ai = True
    game.turn_order = game.players.copy()
    game.start_main_phase()

    assert game.ai.step(game) is True

    assert game.dice_rolled is True
    assert game.pending_dice_context is None
    assert game.pending_dice_roll is None
    assert game.has_active_dice_animation() is False


def test_headless_game_rejects_interactive_loop_and_render():
    game = CatanGame(board_mode="fully_random", board_seed=789, headless=True)

    with pytest.raises(RuntimeError, match="描画"):
        game.render()
    with pytest.raises(RuntimeError, match="対話ループ"):
        game.run()
