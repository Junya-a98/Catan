import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from game.building import Building, BuildingType
from game.game import CatanGame
from game.resources import BUILD_COSTS


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
