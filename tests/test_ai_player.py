import os
import random

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from game import game as game_module
from game.ai_personality import get_ai_personality_profile
from game.building import Building, BuildingType
from game.game import CatanGame
from game.resources import BUILD_COSTS, ResourceType


def create_game(ai_player_count=1):
    pygame.init()
    pygame.display.set_mode((1, 1))
    return CatanGame(board_seed=202, ai_player_count=ai_player_count, ai_action_delay_ms=0)


def close_game(game):
    game.audio.stop()
    pygame.quit()


def test_ai_configuration_marks_last_players_as_cpu():
    game = create_game(ai_player_count=1)
    try:
        assert game.players[0].is_ai is False
        assert game.players[1].is_ai is True
        assert game.players[1].name == "CPU1"

        game.configure_players(4, reset_logs=False)
        game.set_ai_player_count(2)

        assert [player.is_ai for player in game.players] == [False, False, True, True]
        assert [player.name for player in game.players[-2:]] == ["CPU1", "CPU2"]
    finally:
        close_game(game)


def test_mixed_ai_mode_assigns_secret_distinct_personalities_and_can_be_cycled(
    monkeypatch,
):
    private_seeds = iter((0, 1))
    monkeypatch.setattr(game_module.secrets, "randbits", lambda _bits: next(private_seeds))
    game = create_game(ai_player_count=1)
    try:
        assert game.set_ai_personality_mode("mixed") is True
        game.configure_players(4, reset_logs=False)
        game.set_ai_player_count(3)

        assert [player.ai_personality for player in game.players] == [
            "standard",
            "expansion",
            "disruptor",
            "trader",
        ]
        gameplay_random_state = random.getstate()
        game.assign_ai_personalities()
        assert random.getstate() == gameplay_random_state
        first_cpu = game.players[1]
        assert game.get_player_ai_personality_label(first_cpu) == (
            get_ai_personality_profile(first_cpu.ai_personality).label
        )
        assert game.get_public_player_ai_personality_label(first_cpu) == "性格非公開"
        game.set_ai_status(first_cpu, "配置候補を比較", log=True)
        assert game.log_messages[-1] == (
            "CPU1（性格非公開）の判断: 配置候補を比較"
        )
        buttons = {button.action: button for button in game.build_buttons()}
        assert buttons["ai_personality_cycle"].label == "性格 混合"

        game.handle_button_action("ai_personality_cycle")

        assert game.ai_personality_mode == "expansion"
        assert all(
            player.ai_personality == "expansion"
            for player in game.players
            if player.is_ai
        )
        assert "AI 3人（拡大重視）" in game.get_board_configuration_summary()

        assert game.set_ai_personality_mode("mixed") is True
        previous_lineup = [
            player.ai_personality for player in game.players if player.is_ai
        ]
        game.restart_game(randomize_seed=False)
        assert [
            player.ai_personality for player in game.players if player.is_ai
        ] == ["trader", "disruptor", "expansion"]
        assert [
            player.ai_personality for player in game.players if player.is_ai
        ] != previous_lineup
    finally:
        close_game(game)


def test_ai_personality_mode_is_locked_after_initial_roll_starts():
    game = create_game(ai_player_count=1)
    try:
        game.initial_dice_histories[game.players[0].name].append(6)

        assert game.set_ai_personality_mode("disruptor") is False
        assert game.ai_personality_mode == "standard"
    finally:
        close_game(game)


def test_ai_places_legal_initial_settlement_and_road():
    game = create_game(ai_player_count=1)
    try:
        human, cpu = game.players
        game.turn_order = [human, cpu]
        game.initial_dice_phase = False
        game.initial_placement_order = [human, cpu]
        game.initial_player_index = 1
        game.initial_placement_counts = {human.name: 1, cpu.name: 0}

        assert game.ai.step(game) is True
        assert game.waiting_for_road is True
        assert any(node.building is not None and node.building.owner is cpu for node in game.board.nodes)

        assert game.ai.step(game) is True
        assert any(road.owner is cpu for road in game.board.roads)
        assert game.initial_round == 2
    finally:
        close_game(game)


def test_ai_upgrades_best_available_settlement_to_city():
    game = create_game(ai_player_count=1)
    try:
        human, cpu = game.players
        game.turn_order = [cpu, human]
        game.start_main_phase()
        node = game.board.nodes[0]
        node.building = Building(cpu)
        for resource_type, amount in BUILD_COSTS["city"].items():
            game.bank.withdraw(resource_type, amount)
            cpu.add_resource(resource_type, amount)
        game.dice_rolled = True

        assert game.ai.step(game) is True

        assert node.building.building_type == BuildingType.CITY
        assert cpu.cities_remaining == 3
    finally:
        close_game(game)


def test_ai_rolls_and_ends_a_turn_when_no_action_is_available():
    game = create_game(ai_player_count=1)
    try:
        human, cpu = game.players
        game.turn_order = [cpu, human]
        game.start_main_phase()

        assert game.ai.step(game) is True
        assert game.pending_dice_context == "main"

        game.reset_pending_dice_state()
        game.dice_rolled = True
        assert game.ai.step(game) is True

        assert game.get_current_player() is human
        assert game.dice_rolled is False
    finally:
        close_game(game)


def test_ai_targets_the_publicly_likely_supplier_for_domestic_trade():
    game = create_game(ai_player_count=1)
    try:
        game.configure_players(3, reset_logs=False)
        first_human, likely_supplier, cpu = game.players
        ore_node = next(
            node
            for node in game.board.nodes
            if any(tile.resource_type == ResourceType.ORE for tile in node.tiles)
        )
        non_ore_node = next(
            node
            for node in game.board.nodes
            if node is not ore_node
            and all(tile.resource_type != ResourceType.ORE for tile in node.tiles)
        )
        non_ore_node.building = Building(first_human)
        ore_node.building = Building(likely_supplier)

        for resource_type, amount in {
            ResourceType.WOOD: 1,
            ResourceType.WHEAT: 2,
            ResourceType.ORE: 2,
        }.items():
            assert game.bank.withdraw(resource_type, amount)
            cpu.add_resource(resource_type, amount)
        assert game.bank.withdraw(ResourceType.BRICK, 2)
        first_human.add_resource(ResourceType.BRICK)
        likely_supplier.add_resource(ResourceType.BRICK)

        partner, give, receive = game.ai._choose_domestic_trade(
            game,
            cpu,
            goals=("city",),
        )

        assert partner is likely_supplier
        assert give == {ResourceType.WOOD: 1}
        assert receive == {ResourceType.ORE: 1}
    finally:
        close_game(game)


def test_ai_uses_recent_public_distribution_without_reading_hidden_hand_types():
    game = create_game(ai_player_count=1)
    try:
        game.configure_players(3, reset_logs=False)
        first_human, recent_supplier, cpu = game.players
        for resource_type, amount in {
            ResourceType.WOOD: 1,
            ResourceType.WHEAT: 2,
            ResourceType.ORE: 2,
        }.items():
            assert game.bank.withdraw(resource_type, amount)
            cpu.add_resource(resource_type, amount)
        assert game.bank.withdraw(ResourceType.BRICK, 2)
        first_human.add_resource(ResourceType.BRICK)
        recent_supplier.add_resource(ResourceType.BRICK)
        game.last_resource_distribution = {
            recent_supplier.name: {ResourceType.ORE: 1},
        }

        first_choice = game.ai._choose_domestic_trade(
            game,
            cpu,
            goals=("city",),
        )
        first_human.resources[ResourceType.BRICK] = 0
        first_human.resources[ResourceType.SHEEP] = 1
        recent_supplier.resources[ResourceType.BRICK] = 0
        recent_supplier.resources[ResourceType.WOOD] = 1
        second_choice = game.ai._choose_domestic_trade(
            game,
            cpu,
            goals=("city",),
        )

        assert first_choice[0] is recent_supplier
        assert second_choice[0] is recent_supplier
        assert first_choice[1:] == second_choice[1:]
    finally:
        close_game(game)
