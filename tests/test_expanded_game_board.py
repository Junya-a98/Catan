from collections import Counter

import pygame
import pytest

from game.assets import clear_font_cache
from game.constants import HEX_RADIUS, LOG_PANEL_WIDTH, SIDE_PANEL_X
from game.game_board import (
    OUTER_RING_HEX_RADIUS,
    OUTER_RING_TOPOLOGY_ID,
    STANDARD_TOPOLOGY_ID,
    GameBoard,
    _load_font,
)
from game.resources import ResourceType
from game.ui import _get_hex_vertices


STANDARD_NUMBER_POOL = [
    2,
    3,
    3,
    4,
    4,
    5,
    5,
    6,
    6,
    8,
    8,
    9,
    9,
    10,
    10,
    11,
    11,
    12,
]


def expanded_board(*, mode="constrained", seed=86712347):
    return GameBoard(
        mode=mode,
        seed=seed,
        topology_id=OUTER_RING_TOPOLOGY_ID,
    )


def board_signature(board):
    return (
        [
            (tile.axial, tile.resource_type, tile.number, tile.x, tile.y)
            for tile in board.tiles
        ],
        [
            (
                harbor.label,
                harbor.node1.x,
                harbor.node1.y,
                harbor.node2.x,
                harbor.node2.y,
            )
            for harbor in board.harbors
        ],
    )


def test_default_topology_remains_the_exact_standard_board():
    implicit = GameBoard(mode="constrained", seed=4217)
    explicit = GameBoard(
        mode="constrained",
        seed=4217,
        topology_id=STANDARD_TOPOLOGY_ID,
    )

    assert implicit.topology_id == STANDARD_TOPOLOGY_ID
    assert implicit.board_radius == 2
    assert implicit.hex_radius == HEX_RADIUS
    assert board_signature(implicit) == board_signature(explicit)
    assert (len(implicit.tiles), len(implicit.nodes), len(implicit.edges)) == (
        19,
        54,
        72,
    )


def test_outer_ring_topology_builds_complete_stable_geometry():
    board = expanded_board()

    assert board.topology_id == OUTER_RING_TOPOLOGY_ID
    assert board.board_radius == 3
    assert board.hex_radius == OUTER_RING_HEX_RADIUS == 35
    assert len(board.tiles) == 37
    assert len(board.nodes) == 96
    assert len(board.edges) == 132
    assert len(board.perimeter_edges) == 42
    assert len(board.harbors) == 12
    assert len({tile.axial for tile in board.tiles}) == 37
    assert all(tile.radius == board.hex_radius for tile in board.tiles)


@pytest.mark.parametrize("mode", ("constrained", "fully_random"))
def test_outer_ring_uses_versioned_resource_and_number_pools(mode):
    board = expanded_board(mode=mode)
    resource_counts = Counter(tile.resource_type for tile in board.tiles)
    number_counts = Counter(
        tile.number for tile in board.tiles if tile.number is not None
    )

    assert resource_counts == Counter(
        {
            ResourceType.DESERT: 1,
            ResourceType.WOOD: 8,
            ResourceType.SHEEP: 8,
            ResourceType.WHEAT: 8,
            ResourceType.BRICK: 6,
            ResourceType.ORE: 6,
        }
    )
    assert number_counts == Counter(STANDARD_NUMBER_POOL * 2)
    assert board.robber_tile.axial == (0, 0)
    assert board.robber_tile.resource_type is ResourceType.DESERT
    assert board.robber_tile.number is None


def test_outer_ring_constrained_numbers_keep_red_tokens_apart():
    for seed in range(5):
        board = expanded_board(seed=seed)
        adjacency = board._get_tile_adjacency()
        for tile in board.tiles:
            if tile.number not in (6, 8):
                continue
            assert all(neighbor.number not in (6, 8) for neighbor in adjacency[tile])


def test_outer_ring_is_seed_reproducible_and_harbors_use_unique_coastal_edges():
    first = expanded_board(seed=90210)
    second = expanded_board(seed=90210)
    perimeter = {
        frozenset((node1, node2)) for node1, node2 in first.perimeter_edges
    }
    harbor_edges = {
        frozenset((harbor.node1, harbor.node2)) for harbor in first.harbors
    }

    assert board_signature(first) == board_signature(second)
    assert len(harbor_edges) == 12
    assert harbor_edges <= perimeter


def test_outer_ring_piece_geometry_stays_between_desktop_side_panels():
    board = expanded_board()
    all_corners = [node for tile in board.tiles for node in tile.corners]

    assert min(node.x for node in all_corners) >= LOG_PANEL_WIDTH + 20
    assert max(node.x for node in all_corners) <= SIDE_PANEL_X - 20
    assert _get_hex_vertices(board.tiles[0]) == [
        (int(node.x), int(node.y)) for node in board.tiles[0].corners
    ]


def test_outer_ring_harbor_layout_uses_each_tiles_own_radius():
    clear_font_cache()
    pygame.init()
    pygame.display.set_mode((1, 1))
    try:
        board = expanded_board(seed=7)
        placements = board.get_harbor_badge_layout(_load_font(17))
        surface = pygame.Surface((1200, 800), pygame.SRCALPHA)

        assert len(placements) == 12
        assert all(
            not board.harbor_badge_overlaps_tile(badge_rect, tile)
            for _, badge_rect in placements
            for tile in board.tiles
        )
        board.draw(surface)
    finally:
        pygame.quit()
        clear_font_cache()


def test_unknown_topology_is_rejected():
    with pytest.raises(ValueError, match="topology"):
        GameBoard(topology_id="future_topology")
