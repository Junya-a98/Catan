import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from game.building import Building, BuildingType
from game.development_cards import DevelopmentCardType
from game.game import CatanGame
from game.log_display import get_log_slice
from game.resources import ResourceType


@pytest.fixture
def game_factory():
    games = []

    def create(*, player_count=2, ai_player_count=1, delay=0):
        pygame.init()
        pygame.display.set_mode((1, 1))
        game = CatanGame(
            board_seed=707,
            ai_player_count=ai_player_count,
            ai_action_delay_ms=delay,
        )
        game.configure_players(player_count, reset_logs=False)
        games.append(game)
        return game

    yield create

    for game in games:
        game.audio.stop()
    pygame.quit()


def test_log_slice_can_reach_the_first_entry_from_a_long_history():
    messages = [f"event-{index}" for index in range(120)]

    start, end, latest = get_log_slice(messages, 0)
    oldest_start, oldest_end, oldest = get_log_slice(messages, len(messages) - 1)

    assert (start, end) == (40, 120)
    assert latest[-1] == "event-119"
    assert (oldest_start, oldest_end) == (0, 1)
    assert oldest == ["event-0"]


def test_log_panel_is_collapsed_by_default_and_preserves_scroll_position(game_factory):
    game = game_factory(ai_player_count=0)
    assert game.show_log_panel is False

    for index in range(30):
        game.add_log(f"history-{index}")
    game.toggle_log_panel()
    game.scroll_log(12)
    game.add_log("new-event")

    assert game.show_log_panel is True
    assert game.log_scroll_offset == 13

    game.log_scroll_offset = len(game.log_messages) - 1
    _, _, oldest = get_log_slice(game.log_messages, game.log_scroll_offset)
    assert oldest == [game.log_messages[0]]

    game.toggle_log_panel()
    assert game.show_log_panel is False
    assert game.log_scroll_offset == 0


def test_ai_speed_button_remains_available_while_ai_input_is_locked(game_factory):
    game = game_factory(delay=1250)
    human, cpu = game.players
    game.turn_order = [cpu, human]
    game.start_main_phase()

    buttons = game.build_buttons()

    assert [button.action for button in buttons] == ["ai_speed_cycle"]
    assert "標準" in buttons[0].label

    game.cycle_ai_speed()
    assert game.get_ai_speed_label() == "高速"
    game.cycle_ai_speed()
    assert game.get_ai_speed_label() == "一時停止"
    assert game.ai_paused is True


def test_ai_discard_does_not_render_buttons_that_reveal_its_hand(game_factory):
    game = game_factory()
    human, cpu = game.players
    cpu.add_resource(ResourceType.WOOD, 4)
    cpu.add_resource(ResourceType.ORE, 4)
    game.start_main_phase()
    game.special_phase = "discard"
    game.discard_player = cpu
    game.discard_remaining = 4

    buttons = game.build_buttons()

    assert [button.action for button in buttons] == ["ai_speed_cycle"]
    assert all("×" not in button.label for button in buttons)
    assert game.get_current_player() is human


def test_trade_partner_summary_uses_public_history_and_board_production(game_factory):
    game = game_factory()
    _, cpu = game.players
    node = next(node for node in game.board.nodes if node.tiles)
    node.building = Building(cpu)
    cpu.add_resource(ResourceType.WOOD, 2)
    game.record_public_gain(cpu, {ResourceType.WOOD: 1}, "出目8")

    summary = game.get_trade_partner_public_summary(cpu)

    assert "手札2枚" in summary
    assert "生産" in summary
    assert "木1（出目8）" in summary


def test_ai_trade_rejects_an_offer_that_breaks_a_ready_settlement(game_factory):
    game = game_factory()
    _, cpu = game.players
    for resource_type in (
        ResourceType.WOOD,
        ResourceType.BRICK,
        ResourceType.SHEEP,
        ResourceType.WHEAT,
    ):
        cpu.add_resource(resource_type)

    decision = game.ai.evaluate_domestic_trade(
        cpu,
        incoming={ResourceType.ORE: 1},
        outgoing={ResourceType.WOOD: 1},
    )

    assert decision == "reject"


def test_ai_steals_from_the_public_score_leader_before_a_richer_player(game_factory):
    game = game_factory(player_count=3, ai_player_count=1)
    human, leader, cpu = game.players
    game.turn_order = [cpu, human, leader]
    game.start_main_phase()
    leader_node = game.board.nodes[0]
    leader_node.building = Building(leader, BuildingType.CITY)
    leader.add_resource(ResourceType.WHEAT, 1)
    human.add_resource(ResourceType.WOOD, 5)
    game.special_phase = "steal"
    game.robber_target_players = [human, leader]

    assert game.ai.step(game) is True

    assert leader.total_resource_count() == 0
    assert human.total_resource_count() == 5


def test_ai_uses_bank_trade_to_complete_a_city_before_buying_development(game_factory):
    game = game_factory()
    human, cpu = game.players
    game.turn_order = [cpu, human]
    game.start_main_phase()
    node = game.board.nodes[0]
    node.building = Building(cpu)
    for resource_type, amount in {
        ResourceType.WOOD: 4,
        ResourceType.SHEEP: 1,
        ResourceType.WHEAT: 2,
        ResourceType.ORE: 2,
    }.items():
        assert game.bank.withdraw(resource_type, amount)
        cpu.add_resource(resource_type, amount)
    game.dice_rolled = True

    assert game.ai.step(game) is True
    assert cpu.resources[ResourceType.ORE] == 3
    assert cpu.resources[ResourceType.WOOD] == 0
    assert node.building.building_type == BuildingType.SETTLEMENT

    assert game.ai.step(game) is True
    assert node.building.building_type == BuildingType.CITY


def test_ai_saves_knight_until_it_has_a_tactical_reason(game_factory):
    game = game_factory()
    human, cpu = game.players
    game.turn_order = [cpu, human]
    game.start_main_phase()
    game.dice_rolled = True
    cpu.development_cards[DevelopmentCardType.KNIGHT] = 1

    assert game.ai.step(game) is True

    assert cpu.development_cards[DevelopmentCardType.KNIGHT] == 1
    assert cpu.played_knights == 0
    assert game.get_current_player() is human
