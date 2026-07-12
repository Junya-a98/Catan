import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from game.building import Building
from game.game import CatanGame
from game.harbor import Harbor
from game.player import Player
from game.resources import ResourceType


def board_signature(board):
    tiles = [
        (tile.axial, tile.resource_type.name, tile.number)
        for tile in sorted(board.tiles, key=lambda tile: tile.axial)
    ]
    harbors = [
        (
            harbor.label,
            round(harbor.node1.x, 1),
            round(harbor.node1.y, 1),
            round(harbor.node2.x, 1),
            round(harbor.node2.y, 1),
        )
        for harbor in board.harbors
    ]
    return tiles, harbors


def test_resource_harbor_label_uses_resource_glyph():
    harbor = Harbor(object(), object(), 2, ResourceType.ORE)

    assert harbor.label == "鉄 2:1"


def test_build_preview_lists_missing_resources():
    game = CatanGame.__new__(CatanGame)
    game.development_deck = [object()]
    player = Player("Tester", (255, 0, 0))

    previews = game.get_build_affordability(player)
    road_preview = next(item for item in previews if item["label"] == "街道")
    settlement_preview = next(item for item in previews if item["label"] == "開拓地")

    assert road_preview["available"] is False
    assert road_preview["detail"] == "不足: 木1 土1"
    assert settlement_preview["detail"] == "不足: 木1 土1 羊1 麦1"


def test_build_preview_reports_supply_shortages_before_resource_shortages():
    game = CatanGame.__new__(CatanGame)
    game.development_deck = []
    player = Player("Tester", (255, 0, 0))
    player.roads_remaining = 0

    previews = game.get_build_affordability(player)
    road_preview = next(item for item in previews if item["label"] == "街道")
    development_preview = next(item for item in previews if item["label"] == "発展")

    assert road_preview["detail"] == "在庫なし"
    assert development_preview["detail"] == "山札なし"


def test_side_panel_guidance_prompts_roll_before_dice():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()

        guidance = game.get_side_panel_guidance()

        assert guidance[0] == "次: ダイスを振る"
    finally:
        game.audio.stop()
        pygame.quit()


def test_action_mode_guidance_explains_road_block_reason():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        game.dice_rolled = True
        game.action_mode = "road"

        guidance = game.get_side_panel_guidance()

        assert guidance[0] == "街道不可"
        assert "不足" in guidance[1]
    finally:
        game.audio.stop()
        pygame.quit()


def test_action_mode_guidance_points_to_highlighted_candidates():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        player = game.get_current_player()
        game.board.nodes[0].building = Building(player)
        player.add_resource(ResourceType.WOOD)
        player.add_resource(ResourceType.BRICK)
        game.dice_rolled = True
        game.action_mode = "road"

        guidance = game.get_side_panel_guidance()

        assert guidance[0].startswith("次: 光っている辺を選ぶ")
    finally:
        game.audio.stop()
        pygame.quit()


def test_initial_setup_buttons_include_board_configuration_controls():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        actions = {button.action for button in game.build_buttons()}

        assert {
            "board_mode_constrained",
            "board_mode_fully_random",
            "seed_input_focus",
            "seed_apply",
            "seed_randomize",
            "ai_count_cycle",
        }.issubset(actions)
    finally:
        game.audio.stop()
        pygame.quit()


def test_seed_apply_rebuilds_reproducible_board_from_ui_state():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.board_seed_text = "42"
        game.handle_button_action("seed_apply")
        first_signature = board_signature(game.board)

        game.handle_button_action("seed_randomize")
        game.board_seed_text = "42"
        game.handle_button_action("seed_apply")
        second_signature = board_signature(game.board)

        assert first_signature == second_signature
        assert game.board.seed == 42
    finally:
        game.audio.stop()
        pygame.quit()


def test_board_mode_buttons_switch_generation_mode():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.handle_button_action("board_mode_fully_random")
        assert game.board_mode == "fully_random"
        assert game.board.mode == "fully_random"

        game.handle_button_action("board_mode_constrained")
        assert game.board_mode == "constrained"
        assert game.board.mode == "constrained"
    finally:
        game.audio.stop()
        pygame.quit()


def test_pre_game_board_summary_describes_current_mode_and_seed():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame(board_mode="constrained", board_seed=77)
    try:
        summary = game.get_pre_game_board_summary()

        assert summary["rows"][0] == ("現在の mode", "制約付き")
        assert summary["rows"][1] == ("現在の seed", "77")
        assert "6/8" in summary["description"]
    finally:
        game.audio.stop()
        pygame.quit()


def test_seed_input_button_shows_cursor_and_focus_state():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame(board_seed=19)
    original_get_ticks = pygame.time.get_ticks
    try:
        pygame.time.get_ticks = lambda: 0
        game.seed_input_active = True

        buttons = {button.action: button for button in game.build_buttons()}

        assert buttons["seed_input_focus"].selected is True
        assert buttons["seed_input_focus"].label.endswith("|")
    finally:
        pygame.time.get_ticks = original_get_ticks
        game.audio.stop()
        pygame.quit()


def test_invalid_main_phase_click_surfaces_feedback_message():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        game.dice_rolled = True

        game.handle_main_phase_click((0, 0))

        feedback = game.get_active_feedback()
        assert feedback is not None
        assert "行動未選択" in feedback.text
        assert feedback.level == "error"
    finally:
        game.audio.stop()
        pygame.quit()


def test_set_action_mode_reports_reason_when_action_is_not_available():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        game.dice_rolled = True

        game.set_action_mode("road")

        feedback = game.get_active_feedback()
        assert feedback is not None
        assert "不足" in feedback.text
        assert game.action_mode is None
    finally:
        game.audio.stop()
        pygame.quit()


def test_progress_header_keeps_actor_and_next_step_visible():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()

        header = game.get_progress_header_data()

        assert header["title"] == "あなたの手番 — Player1"
        assert header["instruction"] == "次: ダイスを振る"
        assert header["steps"][0].state == "active"
        assert header["is_ai"] is False

        game.get_current_player().is_ai = True
        ai_header = game.get_progress_header_data()
        assert ai_header["title"] == "Player1の手番"
        assert ai_header["is_ai"] is True
    finally:
        game.audio.stop()
        pygame.quit()


def test_turn_change_keeps_history_and_exposes_completed_turn_summary():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        game.dice_rolled = True
        game.add_log("確認用の過去イベント")
        game.record_event("Player1のダイス: 8", "木 +2", actor=game.get_current_player())

        game.finish_current_turn()

        assert "確認用の過去イベント" in game.log_messages
        assert game.latest_event["title"] == "Player1の手番終了"
        assert "木 +2" in game.latest_event["detail"]
        assert game.turn_summary_entries == []
    finally:
        game.audio.stop()
        pygame.quit()
