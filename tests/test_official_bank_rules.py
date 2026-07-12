import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from game.bank import BANK_RESOURCE_COUNT, RESOURCE_TYPES
from game.building import Building, BuildingType
from game.development_cards import DevelopmentCardType
from game.game import CatanGame
from game.resources import BUILD_COSTS, ResourceType


def create_game():
    pygame.init()
    pygame.display.set_mode((1, 1))
    return CatanGame(board_seed=101)


def close_game(game):
    game.audio.stop()
    pygame.quit()


def test_bank_starts_with_nineteen_cards_of_each_resource():
    game = create_game()
    try:
        assert all(game.bank.available(resource_type) == BANK_RESOURCE_COUNT for resource_type in RESOURCE_TYPES)
    finally:
        close_game(game)


def test_paid_build_cost_returns_resource_cards_to_bank():
    game = create_game()
    try:
        player = game.players[0]
        for resource_type, amount in BUILD_COSTS["road"].items():
            assert game.bank.withdraw(resource_type, amount)
            player.add_resource(resource_type, amount)

        assert game.pay_resource_cost(player, BUILD_COSTS["road"]) is True

        assert all(
            game.bank.available(resource_type) == BANK_RESOURCE_COUNT
            for resource_type in BUILD_COSTS["road"]
        )
    finally:
        close_game(game)


def test_resource_shortage_gives_nobody_when_multiple_players_are_owed():
    game = create_game()
    try:
        game.start_main_phase()
        tile = next(tile for tile in game.board.tiles if tile.number is not None)
        player1, player2 = game.players[:2]
        tile.corners[0].building = Building(player1)
        tile.corners[2].building = Building(player2)
        game.bank.resources[tile.resource_type] = 1

        game.distribute_resources(tile.number)

        assert player1.resources[tile.resource_type] == 0
        assert player2.resources[tile.resource_type] == 0
        assert game.bank.available(tile.resource_type) == 1
    finally:
        close_game(game)


def test_resource_shortage_gives_remaining_cards_to_the_only_affected_player():
    game = create_game()
    try:
        game.start_main_phase()
        tile = next(tile for tile in game.board.tiles if tile.number is not None)
        player = game.players[0]
        tile.corners[0].building = Building(player, BuildingType.CITY)
        game.bank.resources[tile.resource_type] = 1

        game.distribute_resources(tile.number)

        assert player.resources[tile.resource_type] == 1
        assert game.bank.available(tile.resource_type) == 0
    finally:
        close_game(game)


def test_discard_and_maritime_trade_return_cards_to_the_bank():
    game = create_game()
    try:
        game.start_main_phase()
        player = game.get_current_player()
        game.bank.withdraw(ResourceType.WOOD, 8)
        player.add_resource(ResourceType.WOOD, 8)
        game.start_robber_phase()

        game.discard_resource(ResourceType.WOOD)

        assert game.bank.available(ResourceType.WOOD) == 12

        game.special_phase = None
        game.dice_rolled = True
        game.start_bank_trade()
        game.select_bank_trade_resource(ResourceType.WOOD)
        game.select_bank_trade_resource(ResourceType.SHEEP)

        assert game.bank.available(ResourceType.WOOD) == 16
        assert game.bank.available(ResourceType.SHEEP) == 18
    finally:
        close_game(game)


def test_year_of_plenty_draws_from_finite_bank_supply():
    game = create_game()
    try:
        game.start_main_phase()
        player = game.get_current_player()
        player.development_cards[DevelopmentCardType.YEAR_OF_PLENTY] = 1

        game.use_year_of_plenty_card()
        game.handle_resource_selection(ResourceType.ORE)
        game.handle_resource_selection(ResourceType.ORE)

        assert player.resources[ResourceType.ORE] == 2
        assert game.bank.available(ResourceType.ORE) == 17
        assert game.special_phase is None
    finally:
        close_game(game)
