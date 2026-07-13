import math
import time

import pygame
import pytest

from game.building import Building, BuildingType
from game.constants import BOARD_CENTER_X, BOARD_CENTER_Y, LOG_PANEL_WIDTH, SIDE_PANEL_X
from game.game_board import (
    HARBOR_BADGE_GAP,
    HARBOR_ROAD_CLEARANCE,
    GameBoard,
    _load_font,
)
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


def harbor_font():
    pygame.font.init()
    return _load_font(17)


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


@pytest.mark.parametrize("mode", ("constrained", "fully_random"))
@pytest.mark.parametrize("seed", (0, 7, 23))
def test_all_harbor_badges_avoid_roads_buildings_and_each_other(mode, seed):
    board = GameBoard(mode=mode, seed=seed)
    placements = board._layout_harbor_badges(harbor_font())
    safe_area = board._get_harbor_safe_badge_area()
    progress_header_rect = pygame.Rect(
        LOG_PANEL_WIDTH + 34,
        14,
        SIDE_PANEL_X - LOG_PANEL_WIDTH - 54,
        88,
    )

    assert len(placements) == len(board.harbors) == 9
    assert [harbor for harbor, _, _ in placements] == board.harbors
    assert board.get_harbor_badge_layout(harbor_font()) == tuple(
        (harbor, rect) for harbor, _, rect in placements
    )

    visual_rects = []
    for harbor, _, badge_rect in placements:
        visual_rect = board.get_harbor_badge_visual_rect(badge_rect)
        assert safe_area.contains(badge_rect)
        assert visual_rect.bottom <= 586
        assert badge_rect.right <= SIDE_PANEL_X - 12
        assert not board.get_harbor_badge_visual_rect(badge_rect).colliderect(
            progress_header_rect
        )
        assert all(
            not board.harbor_badge_overlaps_tile(badge_rect, tile)
            for tile in board.tiles
        )

        for node in (harbor.node1, harbor.node2):
            building_rect = board.get_harbor_building_exclusion_rect(node)
            assert not visual_rect.colliderect(building_rect)

        road_probe = visual_rect.inflate(
            HARBOR_ROAD_CLEARANCE * 2,
            HARBOR_ROAD_CLEARANCE * 2,
        )
        assert not road_probe.clipline(
            (round(harbor.node1.x), round(harbor.node1.y)),
            (round(harbor.node2.x), round(harbor.node2.y)),
        )

        connector_path = board._get_harbor_connector_path(harbor, badge_rect)
        for node in (harbor.node1, harbor.node2):
            building_rect = board.get_harbor_building_exclusion_rect(node)
            assert not building_rect.clipline(connector_path[1], connector_path[2])

        assert all(
            not visual_rect.inflate(
                HARBOR_BADGE_GAP * 2,
                HARBOR_BADGE_GAP * 2,
            ).colliderect(previous)
            for previous in visual_rects
        )
        visual_rects.append(visual_rect)

    assert board._layout_harbor_badges(harbor_font()) is placements
    board._harbor_badge_layout_cache = None
    repeated = board._layout_harbor_badges(harbor_font())
    assert [rect for _, _, rect in repeated] == [rect for _, _, rect in placements]


def test_harbor_badge_pixels_do_not_cover_road_or_city_pixels():
    board = GameBoard(seed=31)
    placements = board._layout_harbor_badges(harbor_font())
    harbor_layer = pygame.Surface((1200, 800), pygame.SRCALPHA)
    tile_layer = pygame.Surface((1200, 800), pygame.SRCALPHA)
    player = Player("HarborOwner", (211, 61, 52))

    board._draw_harbors(harbor_layer)
    for tile in board.tiles:
        pygame.draw.polygon(
            tile_layer,
            (255, 255, 255, 255),
            [(round(node.x), round(node.y)) for node in tile.corners],
        )
        pygame.draw.circle(
            tile_layer,
            (255, 255, 255, 255),
            (round(tile.x), round(tile.y)),
            24,
        )
    for harbor, _, badge_rect in placements:
        piece_layer = pygame.Surface((1200, 800), pygame.SRCALPHA)
        Road(player, harbor.node1, harbor.node2).draw(piece_layer)
        Building(player, BuildingType.CITY).draw(
            piece_layer,
            (harbor.node1.x, harbor.node1.y),
        )
        Building(player, BuildingType.CITY).draw(
            piece_layer,
            (harbor.node2.x, harbor.node2.y),
        )
        visual_rect = board.get_harbor_badge_visual_rect(badge_rect)
        badge_mask = pygame.mask.from_surface(harbor_layer.subsurface(visual_rect))
        piece_mask = pygame.mask.from_surface(piece_layer.subsurface(visual_rect))
        tile_mask = pygame.mask.from_surface(tile_layer.subsurface(visual_rect))
        assert badge_mask.overlap(piece_mask, (0, 0)) is None
        assert badge_mask.overlap(tile_mask, (0, 0)) is None


def test_right_edge_harbor_fallback_stays_outside_number_tokens():
    board = GameBoard(seed=3)
    player = Player("RightEdge", (211, 61, 52))
    harbor = max(
        board.harbors,
        key=lambda item: (item.node1.x + item.node2.x) / 2,
    )
    board.roads.append(Road(player, harbor.node1, harbor.node2))
    harbor.node1.building = Building(player, BuildingType.CITY)
    harbor.node2.building = Building(player)

    badge_by_harbor = dict(board.get_harbor_badge_layout(harbor_font()))
    badge_rect = badge_by_harbor[harbor]

    assert badge_rect.right <= SIDE_PANEL_X - 12
    assert all(
        not board.harbor_badge_overlaps_tile(badge_rect, tile)
        for tile in board.tiles
    )
    assert all(
        not board.get_harbor_badge_visual_rect(badge_rect).colliderect(
            board.get_harbor_building_exclusion_rect(node)
        )
        for node in (harbor.node1, harbor.node2)
    )


def test_harbor_badge_safe_area_starts_below_the_progress_header():
    board = GameBoard(seed=3)
    progress_header_rect = pygame.Rect(
        LOG_PANEL_WIDTH + 34,
        14,
        SIDE_PANEL_X - LOG_PANEL_WIDTH - 54,
        88,
    )

    assert board._get_harbor_safe_badge_area().top >= progress_header_rect.bottom + 10
    assert all(
        not board.get_harbor_badge_visual_rect(badge_rect).colliderect(
            progress_header_rect
        )
        for _, badge_rect in board.get_harbor_badge_layout(harbor_font())
    )


@pytest.mark.parametrize(
    ("seed", "city_offset"),
    ((0, 0), (0, 1), (7, 0), (9, 1), (23, 0), (45, 0)),
)
def test_dense_late_game_coast_keeps_every_harbor_badge_collision_free(
    seed,
    city_offset,
):
    board = GameBoard(seed=seed)
    players = [
        Player(f"P{index}", (40 + index * 40, 80, 120))
        for index in range(4)
    ]
    adjacency = {}
    for node1, node2 in board.perimeter_edges:
        adjacency.setdefault(node1, []).append(node2)
        adjacency.setdefault(node2, []).append(node1)
    assert len(adjacency) == 30
    assert all(len(neighbors) == 2 for neighbors in adjacency.values())

    start = next(iter(adjacency))
    cycle = [start]
    previous = None
    current = start
    while True:
        neighbors = adjacency[current]
        next_node = neighbors[0] if neighbors[0] is not previous else neighbors[1]
        if next_node is start:
            break
        cycle.append(next_node)
        previous, current = current, next_node
    assert len(cycle) == 30

    cycle_edges = list(zip(cycle, cycle[1:] + cycle[:1]))
    road_owner_indexes = [0] * 8 + [1] * 8 + [2] * 7 + [3] * 7
    board.roads = [
        Road(players[owner_index], node1, node2)
        for owner_index, (node1, node2) in zip(
            road_owner_indexes,
            cycle_edges,
        )
    ]
    occupied_nodes = cycle[city_offset::2]
    for cycle_index in range(city_offset, len(cycle), 2):
        node = cycle[cycle_index]
        owner = players[road_owner_indexes[cycle_index]]
        node.building = Building(
            owner,
            BuildingType.CITY,
        )

    # This is a reachable inventory state: buildings obey the distance rule,
    # every city touches an owner road, and all pieces are within inventory.
    assert len(occupied_nodes) == 15
    assert all(
        not board.has_edge(node, other)
        for index, node in enumerate(occupied_nodes)
        for other in occupied_nodes[:index]
    )
    assert max(
        sum(node.building.owner is player for node in occupied_nodes)
        for player in players
    ) <= 4
    assert max(
        sum(road.owner is player for road in board.roads)
        for player in players
    ) <= 15
    assert all(
        any(road.owner is node.building.owner and road.touches(node) for road in board.roads)
        for node in occupied_nodes
    )

    font = harbor_font()
    started_at = time.perf_counter()
    placements = board._layout_harbor_badges(font)
    elapsed = time.perf_counter() - started_at

    assert elapsed < 1.5
    assert len(placements) == 9
    safe_area = board._get_harbor_safe_badge_area()
    visual_rects = []
    for harbor, _, badge_rect in placements:
        visual_rect = board.get_harbor_badge_visual_rect(badge_rect)
        assert safe_area.contains(badge_rect)
        # The player cards start at y=586; include the four-pixel shadow.
        assert visual_rect.bottom <= 586
        assert all(
            not visual_rect.colliderect(
                board.get_harbor_building_exclusion_rect(node)
            )
            for node in occupied_nodes
        )
        assert all(
            not board.harbor_badge_overlaps_tile(badge_rect, tile)
            for tile in board.tiles
        )
        road_probe = visual_rect.inflate(
            HARBOR_ROAD_CLEARANCE * 2,
            HARBOR_ROAD_CLEARANCE * 2,
        )
        assert all(
            not road_probe.clipline(
                (round(road.node1.x), round(road.node1.y)),
                (round(road.node2.x), round(road.node2.y)),
            )
            for road in board.roads
        )
        assert all(
            not visual_rect.inflate(
                HARBOR_BADGE_GAP * 2,
                HARBOR_BADGE_GAP * 2,
            ).colliderect(previous)
            for previous in visual_rects
        )
        assert board._harbor_badge_conflict_score(
            badge_rect,
            harbor,
            [rect for _, _, rect in placements[: len(visual_rects)]],
        ) == (0, 0)
        visual_rects.append(visual_rect)

    # Cached and freshly recomputed layouts are both stable.
    assert board._layout_harbor_badges(font) is placements
    expected_rects = [rect for _, _, rect in placements]
    board._harbor_badge_layout_cache = None
    repeated = board._layout_harbor_badges(font)
    assert [rect for _, _, rect in repeated] == expected_rects


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
