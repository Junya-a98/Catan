import random

from game.building import Building
from game.game_board import GameBoard
from game.player import Player
from game.resources import ResourceType


def test_board_uses_standard_tile_and_edge_counts():
    random.seed(0)
    board = GameBoard()

    assert len(board.tiles) == 19
    assert len(board.nodes) == 54
    assert len(board.perimeter_edges) == 30
    assert len(board.harbors) == 9


def test_specific_harbor_grants_two_to_one_trade():
    random.seed(1)
    board = GameBoard()
    player = Player("Tester", (255, 0, 0))

    harbor = next(harbor for harbor in board.harbors if harbor.resource_type is not None)
    harbor.node1.building = Building(player)

    trade_rates = board.get_player_trade_rates(player)

    assert trade_rates[harbor.resource_type] == 2


def test_generic_harbor_grants_three_to_one_trade():
    random.seed(2)
    board = GameBoard()
    player = Player("Tester", (255, 0, 0))

    harbor = next(harbor for harbor in board.harbors if harbor.resource_type is None)
    harbor.node2.building = Building(player)

    trade_rates = board.get_player_trade_rates(player)

    for resource_type in (
        ResourceType.WOOD,
        ResourceType.SHEEP,
        ResourceType.WHEAT,
        ResourceType.BRICK,
        ResourceType.ORE,
    ):
        assert trade_rates[resource_type] <= 3
