import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from game import game as game_module
from game.game import CatanGame
from game.log_display import player_card_role_label
from game.resources import ResourceType


@pytest.fixture
def game_factory():
    created_games = []

    def create(*, player_count=2, ai_player_count=0):
        pygame.init()
        pygame.display.set_mode((1, 1))
        game = CatanGame(
            board_seed=919,
            ai_player_count=ai_player_count,
            ai_action_delay_ms=0,
        )
        game.configure_players(player_count, reset_logs=False)
        created_games.append(game)
        return game

    yield create

    for game in created_games:
        game.audio.stop()
    pygame.quit()


def give_from_bank(game, player, resource_type, amount):
    assert game.bank.withdraw(resource_type, amount)
    player.add_resource(resource_type, amount)


def test_mixed_ai_player_card_uses_generic_role_until_result(game_factory):
    game = game_factory(ai_player_count=1)
    cpu = game.players[-1]
    cpu.ai_personality = "disruptor"

    assert player_card_role_label(cpu) == "妨害"
    assert player_card_role_label(cpu, hide_ai_personalities=True) == "AI"

    game.ai_personality_mode = "mixed"
    captured = {}

    def capture_resources(*args, **kwargs):
        captured["hide_ai_personalities"] = kwargs["hide_ai_personalities"]

    original_draw_resource_counts = game_module.draw_resource_counts
    game_module.draw_resource_counts = capture_resources
    try:
        game.render()
    finally:
        game_module.draw_resource_counts = original_draw_resource_counts

    assert captured == {"hide_ai_personalities": True}


def test_normal_human_turn_handoff_hides_private_panels_until_reveal(game_factory, monkeypatch):
    game = game_factory()
    game.start_main_phase()
    game.dice_rolled = True
    previous_player, next_player = game.players
    give_from_bank(game, next_player, ResourceType.ORE, 2)

    game.finish_current_turn()

    assert game.get_current_player() is next_player
    assert game.special_phase == "player_handoff"
    assert game.handoff_player is next_player
    assert game.get_actionable_button_actions(next_player) == {"player_handoff_reveal"}
    assert game.get_phase_tracker_data()[0] == "プレイヤー交代"
    assert game.get_progress_header_data()["title"] == f"{next_player.name}へ画面を渡してください"

    captured = {}

    def capture_resources(*args, **kwargs):
        captured["visible_player"] = kwargs["visible_player"]

    def capture_side_panel(*args, **kwargs):
        captured["panel_player"] = args[5]

    monkeypatch.setattr(game_module, "draw_resource_counts", capture_resources)
    monkeypatch.setattr(game_module, "draw_side_panel", capture_side_panel)
    for draw_name in (
        "draw_ocean_background",
        "draw_board_highlights",
        "draw_log",
        "draw_help_panel",
        "draw_progress_header",
        "draw_transient_message",
    ):
        monkeypatch.setattr(game_module, draw_name, lambda *args, **kwargs: None)
    monkeypatch.setattr(game.board, "draw", lambda *args, **kwargs: None)
    monkeypatch.setattr(pygame.display, "flip", lambda: None)

    game.render()
    assert captured == {"visible_player": None, "panel_player": None}

    assert game.reveal_player_handoff()
    assert game.special_phase is None
    assert game.handoff_player is None
    game.render()
    assert captured == {"visible_player": next_player, "panel_player": next_player}
    assert previous_player is not next_player


def test_initial_placement_switch_uses_general_handoff(game_factory):
    game = game_factory()
    first_player, second_player = game.players
    game.initial_dice_phase = False
    game.initial_placement_order = [first_player, second_player]
    game.initial_placement_counts = {
        first_player.name: 1,
        second_player.name: 0,
    }
    game.initial_round = 1
    game.initial_player_index = 0

    game.advance_initial_phase(first_player)

    assert game.initial_player_index == 1
    assert game.special_phase == "player_handoff"
    assert game.handoff_player is second_player
    assert game.handoff_context == "初期配置"
    assert game.reveal_player_handoff()
    assert game.special_phase is None


def test_discard_switches_privately_and_public_log_hides_resource_type(game_factory):
    game = game_factory()
    game.start_main_phase()
    first_player, second_player = game.players
    give_from_bank(game, first_player, ResourceType.WOOD, 8)
    give_from_bank(game, second_player, ResourceType.WOOD, 8)

    game.start_robber_phase(with_discard=True)

    assert game.special_phase == "discard"
    assert game.discard_player is first_player
    game.discard_resource(ResourceType.WOOD)
    assert "資源を1枚捨てました" in game.log_messages[-1]
    assert "木" not in game.log_messages[-1]
    assert "木を捨てました" in game.get_active_feedback().text

    for _ in range(3):
        game.discard_resource(ResourceType.WOOD)

    assert game.special_phase == "player_handoff"
    assert game.handoff_player is second_player
    assert game.handoff_return_phase == "discard"
    assert game.get_active_feedback() is None
    assert game.reveal_player_handoff()
    assert game.special_phase == "discard"
    assert game.discard_player is second_player

    for _ in range(4):
        game.discard_resource(ResourceType.WOOD)

    assert game.special_phase == "player_handoff"
    assert game.handoff_player is first_player
    assert game.handoff_return_phase == "move_robber"
    assert game.reveal_player_handoff()
    assert game.special_phase == "move_robber"


def test_special_phase_tracker_takes_priority_before_dice_roll(game_factory):
    game = game_factory()
    game.start_main_phase()
    assert game.dice_rolled is False

    game.begin_robber_move_phase()
    title, subtitle, steps = game.get_phase_tracker_data()

    assert title == "ターン進行"
    assert subtitle == "特殊処理を完了"
    assert [step.state for step in steps] == ["complete", "active", "pending"]
    assert steps[1].label == "特殊"


def test_build_preview_reports_legal_candidate_shortages_after_costs_are_met(game_factory):
    game = game_factory()
    game.start_main_phase()
    player = game.get_current_player()
    for resource_type, amount in {
        ResourceType.WOOD: 1,
        ResourceType.BRICK: 1,
        ResourceType.SHEEP: 1,
        ResourceType.WHEAT: 2,
        ResourceType.ORE: 3,
    }.items():
        give_from_bank(game, player, resource_type, amount)

    previews = {
        preview["label"]: preview
        for preview in game.get_build_affordability(player)
    }

    assert previews["街道"] == {
        "label": "街道",
        "available": False,
        "detail": "接続先なし",
    }
    assert previews["開拓地"] == {
        "label": "開拓地",
        "available": False,
        "detail": "建設候補なし",
    }
    assert previews["都市"] == {
        "label": "都市",
        "available": False,
        "detail": "対象開拓地なし",
    }


def test_zero_card_domestic_trade_partner_is_not_actionable(game_factory):
    game = game_factory(player_count=3)
    game.start_main_phase()
    game.dice_rolled = True
    active_player, empty_player, eligible_player = game.players
    give_from_bank(game, active_player, ResourceType.WOOD, 1)
    give_from_bank(game, eligible_player, ResourceType.SHEEP, 1)

    assert game.start_domestic_trade()
    actions = game.get_actionable_button_actions(active_player)
    button_actions = {button.action for button in game.build_buttons()}

    assert "domestic_trade_partner_1" not in actions
    assert "domestic_trade_partner_1" not in button_actions
    assert "domestic_trade_partner_2" in actions
    assert "domestic_trade_partner_2" in button_actions
    assert game.select_domestic_trade_partner(1) is False
    assert empty_player.name in game.get_active_feedback().text


def test_ai_domestic_offer_does_not_depend_on_partner_resource_types(game_factory):
    game = game_factory()
    active_player, partner = game.players
    active_player.add_resource(ResourceType.WOOD, 3)
    active_player.add_resource(ResourceType.SHEEP, 3)

    partner.add_resource(ResourceType.WOOD, 3)
    first_choice = game.ai._choose_domestic_trade(game, active_player)

    partner.resources[ResourceType.WOOD] = 0
    partner.resources[ResourceType.SHEEP] = 3
    second_choice = game.ai._choose_domestic_trade(game, active_player)

    assert first_choice is not None
    assert second_choice is not None
    assert first_choice[0] is partner
    assert second_choice[0] is partner
    assert first_choice[1:] == second_choice[1:]
    assert first_choice[1] == {ResourceType.WOOD: 1}


def test_ai_monopoly_choice_does_not_depend_on_opponent_hand(game_factory):
    game = game_factory()
    active_player, opponent = game.players

    opponent.add_resource(ResourceType.WOOD, 5)
    first_choice = game.ai._choose_monopoly_resource(game, active_player)

    opponent.resources[ResourceType.WOOD] = 0
    opponent.resources[ResourceType.ORE] = 5
    second_choice = game.ai._choose_monopoly_resource(game, active_player)

    assert first_choice == second_choice
