import pygame

from game.building import Building, BuildingType
from game.node import Node
from game.player import Player
from game.road import Road


def _transparent_surface():
    return pygame.Surface((100, 80), pygame.SRCALPHA)


def test_road_draws_a_dimensional_plank_around_the_edge():
    surface = _transparent_surface()
    player = Player("Red", (235, 55, 48))
    road = Road(player, Node(20, 40), Node(80, 40))

    road.draw(surface)

    bounds = surface.get_bounding_rect()
    assert bounds.width >= 50
    assert bounds.height >= 17
    assert surface.get_at((50, 40))[:3] != (0, 0, 0)
    assert surface.get_at((50, 34))[:3] != surface.get_at((50, 44))[:3]
    assert surface.get_at((20, 40)).a == 0
    assert surface.get_at((80, 40)).a == 0


def test_city_piece_has_a_larger_distinct_silhouette_than_settlement():
    player = Player("Blue", (45, 100, 220))
    settlement_surface = _transparent_surface()
    city_surface = _transparent_surface()

    Building(player).draw(settlement_surface, (50, 40))
    Building(player, BuildingType.CITY).draw(city_surface, (50, 40))

    settlement_bounds = settlement_surface.get_bounding_rect()
    city_bounds = city_surface.get_bounding_rect()
    assert city_bounds.width > settlement_bounds.width
    assert city_bounds.height >= settlement_bounds.height
    assert city_surface.get_at((62, 29)).a > 0
    assert settlement_surface.get_at((62, 29)).a == 0


def test_player_piece_patterns_remain_distinguishable_with_the_same_color():
    rendered_roads = []
    for pattern in range(4):
        surface = _transparent_surface()
        player = Player("Owner", (80, 160, 210), piece_pattern=pattern)
        Road(player, Node(20, 40), Node(80, 40)).draw(surface)
        rendered_roads.append(pygame.image.tostring(surface, "RGBA"))

    assert len(set(rendered_roads)) == 4
