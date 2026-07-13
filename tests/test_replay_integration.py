import copy
import os
import random

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from game.game import CatanGame
from game.persistence import serialize_game
from game.resources import ResourceType


@pytest.fixture
def game():
    pygame.init()
    pygame.display.set_mode((1, 1))
    instance = CatanGame(board_seed=9191, ai_player_count=1, ai_action_delay_ms=1500)
    instance.configure_players(2, reset_logs=False)
    yield instance
    instance.audio.stop()
    pygame.quit()


def test_semantic_event_capture_is_deferred_until_state_is_stable(game):
    assert len(game.replay_recorder.frames) == 1

    game.record_event("検証イベント", "状態更新後に記録")
    game.pending_dice_context = "main"
    game.pending_dice_roll = 8

    assert game.flush_replay_capture() is False
    assert len(game.replay_recorder.frames) == 1

    game.pending_dice_context = None
    game.pending_dice_roll = None
    assert game.flush_replay_capture() is True
    assert len(game.replay_recorder.frames) == 2
    assert game.replay_recorder.frames[-1].label == "検証イベント"


def test_initial_roll_locks_setup_without_erasing_replay_history(game):
    game.configure_players(3, reset_logs=False)
    game.resolve_initial_key_roll(8)
    assert game.flush_replay_capture() is True
    frame_count = len(game.replay_recorder.frames)
    target = game.victory_point_target

    game.cycle_ai_speed()

    assert len(game.replay_recorder.frames) == frame_count
    assert game.replay_recorder.frames[0].snapshot["initial"]["player_index"] == 0
    assert game.replay_recorder.frames[-1].label == "初期ダイス"
    assert game.adjust_victory_point_target(1) is False
    assert game.victory_point_target == target

    buttons = {button.action: button for button in game.build_buttons()}
    assert buttons["player_count_3"].enabled is False
    assert buttons["board_mode_fully_random"].enabled is False
    assert buttons["seed_randomize"].enabled is False
    assert buttons["ai_count_cycle"].enabled is False
    assert buttons["victory_target_increase"].enabled is False
    assert buttons["ai_speed_cycle"].enabled is True


def test_replay_is_read_only_and_exit_restores_live_state_and_runtime(game, tmp_path):
    player = game.players[0]
    assert game.bank.withdraw(ResourceType.WOOD, 1)
    player.add_resource(ResourceType.WOOD, 1)
    game.record_event("木を獲得", "木 +1", actor=player)
    assert game.flush_replay_capture() is True
    archive = game.replay_recorder.archive()

    game.phase = "finished"
    game.winner = player
    live_state = serialize_game(game)
    live_random_state = random.getstate()
    game.ai_next_action_at = pygame.time.get_ticks() + 5_000
    live_frame_count = len(game.replay_recorder.frames)
    game.quick_save_path = tmp_path / "must-not-save.json"

    assert game.start_replay(archive) is True
    assert game.replay_mode is True
    assert game.phase == "initial"
    assert game.players[0].resources[ResourceType.WOOD] == 0

    ai_calls = []
    game.ai.step = lambda current_game: ai_calls.append(current_game) or True
    assert game.toggle_replay_playback() is True
    game.replay_next_frame_at = 0
    game.update()
    assert game.replay_index == 1
    assert game.players[0].resources[ResourceType.WOOD] == 1
    assert ai_calls == []

    assert game.handle_global_ui_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F5)
    ) is True
    assert not game.quick_save_path.exists()

    assert game.replay_reveal_all is False
    game.handle_replay_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_v))
    assert game.replay_reveal_all is True

    game.handle_replay_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_h))
    assert game.show_help_panel is True
    assert game.show_replay_frame(0) is True
    assert game.show_help_panel is True

    assert game.exit_replay() is True
    assert game.replay_mode is False
    assert serialize_game(game) == live_state
    assert random.getstate() == live_random_state
    assert game.ai_next_action_at > pygame.time.get_ticks()
    assert len(game.replay_recorder.frames) == live_frame_count


def test_victory_auto_saves_replay_and_finished_screen_can_open_it(game, tmp_path):
    game.replay_dir = tmp_path / "replays"
    game.start_main_phase()
    player = game.get_current_player()
    player.victory_point_cards = game.victory_point_target

    game.check_for_winner(player)

    assert game.phase == "finished"
    assert game.latest_replay_path is not None
    assert game.latest_replay_path.exists()
    assert game.replay_archive is not None
    assert game.replay_archive.frames[-1].label == f"{player.name}の勝利"
    replay_button = next(
        button for button in game.build_buttons() if button.action == "replay_open"
    )
    assert replay_button.enabled is True

    assert game.start_replay() is True
    assert game.replay_mode is True
    game.render()
    assert game.exit_replay() is True
    assert game.phase == "finished"


def test_autoplay_stops_safely_on_a_semantically_broken_frame(game):
    player = game.players[0]
    game.record_event("正常フレーム", actor=player)
    assert game.flush_replay_capture() is True
    archive = copy.deepcopy(game.replay_recorder.archive())
    archive.frames[1].snapshot["bank"]["WOOD"] = 18

    game.phase = "finished"
    game.winner = player
    live_state = serialize_game(game)

    assert game.start_replay(archive) is True
    assert game.toggle_replay_playback() is True
    game.replay_next_frame_at = 0
    game.update()

    assert game.replay_index == 0
    assert game.replay_playing is False
    assert game.phase == "initial"
    assert game.exit_replay() is True
    assert serialize_game(game) == live_state


def test_initial_screen_finds_and_opens_latest_completed_replay(game, tmp_path):
    replay_dir = tmp_path / "replays"
    saved_path = game.replay_recorder.save(replay_dir=replay_dir)
    game.replay_dir = replay_dir
    game.latest_replay_path = None
    game.refresh_latest_replay_path()

    buttons = game.build_buttons()
    replay_button = next(button for button in buttons if button.action == "replay_open")

    assert game.latest_replay_path == saved_path
    assert replay_button.enabled is True
    assert game.start_replay() is True
    assert game.replay_mode is True
    assert game.exit_replay() is True
    assert game.phase == "initial"
