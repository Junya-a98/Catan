import math

import pygame

from game.building import Building
from game.constants import BOARD_CENTER_X, BOARD_CENTER_Y, SIDE_PANEL_X
from game.game_board import GameBoard
from game.player import Player
from game.resources import ResourceType
from game.road import Road


def coastline_samples(harbor):
    return [
        (
            round(harbor.node1.x + (harbor.node2.x - harbor.node1.x) * fraction),
            round(harbor.node1.y + (harbor.node2.y - harbor.node1.y) * fraction),
        )
        for fraction in (0.20, 0.35, 0.50, 0.65, 0.80)
    ]


def test_board_uses_standard_tile_and_edge_counts():
    board = GameBoard(seed=0)

    assert len(board.tiles) == 19
    assert len(board.nodes) == 54
    assert len(board.perimeter_edges) == 30
    assert len(board.harbors) == 9


def test_specific_harbor_grants_two_to_one_trade():
    board = GameBoard(seed=1)
    player = Player("Tester", (255, 0, 0))

    harbor = next(harbor for harbor in board.harbors if harbor.resource_type is not None)
    harbor.node1.building = Building(player)

    trade_rates = board.get_player_trade_rates(player)

    assert trade_rates[harbor.resource_type] == 2


def test_generic_harbor_grants_three_to_one_trade():
    board = GameBoard(seed=2)
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


def test_constrained_board_avoids_adjacent_six_and_eight_tokens():
    for seed in range(10):
        board = GameBoard(mode="constrained", seed=seed)
        adjacency = board._get_tile_adjacency()

        for tile in board.tiles:
            if tile.number not in (6, 8):
                continue
            for neighbor in adjacency[tile]:
                assert neighbor.number not in (6, 8)


def test_constrained_board_spreads_high_numbers_across_resources():
    for seed in range(10):
        board = GameBoard(mode="constrained", seed=seed)
        high_number_counts = board.get_resource_high_number_counts()

        assert max(high_number_counts.values()) <= 1


def test_constrained_board_avoids_matching_resource_harbor_with_adjacent_six_or_eight():
    for seed in range(10):
        board = GameBoard(mode="constrained", seed=seed)

        for harbor in board.harbors:
            assert board.harbor_matches_high_value_tile(harbor) is False


def test_seeded_board_generation_is_reproducible():
    board1 = GameBoard(mode="constrained", seed=17)
    board2 = GameBoard(mode="constrained", seed=17)

    tiles1 = [
        (tile.axial, tile.resource_type.name, tile.number)
        for tile in sorted(board1.tiles, key=lambda tile: tile.axial)
    ]
    tiles2 = [
        (tile.axial, tile.resource_type.name, tile.number)
        for tile in sorted(board2.tiles, key=lambda tile: tile.axial)
    ]
    harbors1 = [
        (harbor.label, round(harbor.node1.x, 1), round(harbor.node1.y, 1), round(harbor.node2.x, 1), round(harbor.node2.y, 1))
        for harbor in board1.harbors
    ]
    harbors2 = [
        (harbor.label, round(harbor.node1.x, 1), round(harbor.node1.y, 1), round(harbor.node2.x, 1), round(harbor.node2.y, 1))
        for harbor in board2.harbors
    ]

    assert tiles1 == tiles2
    assert harbors1 == harbors2


def test_fully_random_board_generation_is_still_seed_reproducible():
    board1 = GameBoard(mode="fully_random", seed=23)
    board2 = GameBoard(mode="fully_random", seed=23)

    tiles1 = [
        (tile.axial, tile.resource_type.name, tile.number)
        for tile in sorted(board1.tiles, key=lambda tile: tile.axial)
    ]
    tiles2 = [
        (tile.axial, tile.resource_type.name, tile.number)
        for tile in sorted(board2.tiles, key=lambda tile: tile.axial)
    ]
    harbors1 = [harbor.label for harbor in board1.harbors]
    harbors2 = [harbor.label for harbor in board2.harbors]

    assert tiles1 == tiles2
    assert harbors1 == harbors2


def test_official_random_board_also_avoids_adjacent_six_and_eight_tokens():
    for seed in range(10):
        board = GameBoard(mode="fully_random", seed=seed)
        adjacency = board._get_tile_adjacency()

        for tile in board.tiles:
            if tile.number not in (6, 8):
                continue
            assert all(neighbor.number not in (6, 8) for neighbor in adjacency[tile])


def test_harbor_badge_is_clamped_before_the_side_panel():
    board = GameBoard(seed=3)
    badge = pygame.Surface((92, 24))

    rect = board._get_harbor_badge_rect(badge, (SIDE_PANEL_X + 80, 300))

    assert rect.right <= SIDE_PANEL_X - 12


def test_harbor_dock_stays_outside_the_playable_coastal_edge():
    board = GameBoard(seed=4)
    for harbor in board.harbors:
        surface = pygame.Surface((1200, 800), pygame.SRCALPHA)
        connector_start = board._draw_harbor_dock(surface, harbor)
        midpoint = (
            round((harbor.node1.x + harbor.node2.x) / 2),
            round((harbor.node1.y + harbor.node2.y) / 2),
        )

        assert all(surface.get_at(point).a == 0 for point in coastline_samples(harbor))
        assert surface.get_at(connector_start).a > 0
        connector_distance = math.hypot(
            connector_start[0] - BOARD_CENTER_X,
            connector_start[1] - BOARD_CENTER_Y,
        )
        coastline_distance = math.hypot(
            midpoint[0] - BOARD_CENTER_X,
            midpoint[1] - BOARD_CENTER_Y,
        )
        assert connector_distance > coastline_distance


def test_harbor_dock_does_not_obscure_a_road_on_its_coastal_edge():
    board = GameBoard(seed=4)
    player = Player("RoadOwner", (211, 61, 52))

    for harbor in board.harbors:
        surface = pygame.Surface((1200, 800), pygame.SRCALPHA)
        reference = pygame.Surface((1200, 800), pygame.SRCALPHA)
        board._draw_harbor_dock(surface, harbor)
        road = Road(player, harbor.node1, harbor.node2)
        road.draw(reference)
        road.draw(surface)

        assert all(
            surface.get_at(point) == reference.get_at(point)
            for point in coastline_samples(harbor)
        )
