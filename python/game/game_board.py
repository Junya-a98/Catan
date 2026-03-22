import math
import random

import pygame

from game.building import BuildingType
from game.constants import BOARD_CENTER_X, BOARD_CENTER_Y, COLORS, HEX_RADIUS
from game.harbor import Harbor
from game.hex_tile import HexTile
from game.node import Node
from game.resources import ResourceType


FONT_PATH = "Noto_Sans_JP/NotoSansJP-VariableFont_wght.ttf"


def _load_font(size):
    try:
        return pygame.font.Font(FONT_PATH, size)
    except Exception:
        return pygame.font.Font(None, size)


class GameBoard:
    def __init__(self):
        self.roads = []
        self.tiles = []
        self.nodes = []
        self.edges = []
        self.perimeter_edges = []
        self.harbors = []
        self.robber_tile = None
        self.setup_board()

    def setup_board(self):
        resources = [ResourceType.DESERT]
        for resource in [res for res in ResourceType if res != ResourceType.DESERT]:
            if resource in (ResourceType.BRICK, ResourceType.ORE):
                resources.extend([resource] * 3)
            else:
                resources.extend([resource] * 4)
        random.shuffle(resources)

        numbers = [2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12]
        random.shuffle(numbers)

        axial_coords = []
        radius = 2
        for q in range(-radius, radius + 1):
            r_min = max(-radius, -q - radius)
            r_max = min(radius, -q + radius)
            for r in range(r_min, r_max + 1):
                axial_coords.append((q, r))
        axial_coords.sort(key=lambda coord: (coord[1], coord[0]))

        for q, r in axial_coords:
            x, y = self.axial_to_pixel(q, r)
            resource = resources.pop(0)
            number = None if resource == ResourceType.DESERT else numbers.pop(0)
            tile = HexTile(x, y, resource, number)
            tile.axial = (q, r)
            self.tiles.append(tile)
            if resource == ResourceType.DESERT:
                self.robber_tile = tile

        self._create_nodes_for_tiles()
        self._create_edges()
        self._create_harbors()

    def axial_to_pixel(self, q, r):
        x = BOARD_CENTER_X + math.sqrt(3) * HEX_RADIUS * (q + r / 2)
        y = BOARD_CENTER_Y + 1.5 * HEX_RADIUS * r
        return x, y

    def _get_hex_corners(self, cx, cy, radius):
        corners = []
        for i in range(6):
            angle_deg = 60 * i - 30
            angle_rad = math.pi / 180 * angle_deg
            corner_x = cx + radius * math.cos(angle_rad)
            corner_y = cy + radius * math.sin(angle_rad)
            corners.append((corner_x, corner_y))
        return corners

    def _create_nodes_for_tiles(self):
        for tile in self.tiles:
            corners = self._get_hex_corners(tile.x, tile.y, HEX_RADIUS)
            tile.corners = []
            for cx, cy in corners:
                node = self.find_or_create_node(cx, cy)
                tile.corners.append(node)
                if tile not in node.tiles:
                    node.tiles.append(tile)

    def find_or_create_node(self, x, y, threshold=4):
        for node in self.nodes:
            dist = math.hypot(node.x - x, node.y - y)
            if dist < threshold:
                return node
        new_node = Node(x, y)
        self.nodes.append(new_node)
        return new_node

    def _create_edges(self):
        unique_edges = {}
        edge_counts = {}
        for tile in self.tiles:
            for index, node1 in enumerate(tile.corners):
                node2 = tile.corners[(index + 1) % len(tile.corners)]
                key = tuple(sorted((id(node1), id(node2))))
                unique_edges[key] = (node1, node2)
                edge_counts[key] = edge_counts.get(key, 0) + 1
        self.edges = list(unique_edges.values())
        self.perimeter_edges = [
            unique_edges[key]
            for key, count in edge_counts.items()
            if count == 1
        ]

    def _create_harbors(self):
        harbor_types = [
            None,
            None,
            None,
            None,
            ResourceType.WOOD,
            ResourceType.SHEEP,
            ResourceType.WHEAT,
            ResourceType.BRICK,
            ResourceType.ORE,
        ]
        random.shuffle(harbor_types)

        sorted_edges = sorted(
            self.perimeter_edges,
            key=lambda edge: math.atan2(
                (edge[0].y + edge[1].y) / 2 - BOARD_CENTER_Y,
                (edge[0].x + edge[1].x) / 2 - BOARD_CENTER_X,
            ),
        )

        used_indices = []
        edge_count = len(sorted_edges)
        for index in range(9):
            candidate = round(index * edge_count / 9)
            if candidate in used_indices:
                candidate = (candidate + 1) % edge_count
            used_indices.append(candidate)

        self.harbors = []
        for harbor_type, edge_index in zip(harbor_types, sorted(used_indices)):
            node1, node2 = sorted_edges[edge_index]
            harbor = Harbor(node1, node2, 3 if harbor_type is None else 2, harbor_type)
            self.harbors.append(harbor)
            node1.harbors.append(harbor)
            node2.harbors.append(harbor)

    def get_player_trade_rates(self, player):
        rates = {
            ResourceType.WOOD: 4,
            ResourceType.SHEEP: 4,
            ResourceType.WHEAT: 4,
            ResourceType.BRICK: 4,
            ResourceType.ORE: 4,
        }
        seen_harbors = set()
        for node in self.nodes:
            if node.building is None or node.building.owner != player:
                continue
            for harbor in node.harbors:
                harbor_id = id(harbor)
                if harbor_id in seen_harbors:
                    continue
                seen_harbors.add(harbor_id)
                if harbor.resource_type is None:
                    for resource_type in rates:
                        rates[resource_type] = min(rates[resource_type], harbor.trade_rate)
                else:
                    rates[harbor.resource_type] = min(rates[harbor.resource_type], harbor.trade_rate)
        return rates

    def has_edge(self, node1, node2):
        target_key = tuple(sorted((id(node1), id(node2))))
        return any(
            tuple(sorted((id(edge_node1), id(edge_node2)))) == target_key
            for edge_node1, edge_node2 in self.edges
        )

    def _draw_harbors(self, screen):
        font = _load_font(17)
        for harbor in self.harbors:
            midpoint_x = (harbor.node1.x + harbor.node2.x) / 2
            midpoint_y = (harbor.node1.y + harbor.node2.y) / 2
            dx = midpoint_x - BOARD_CENTER_X
            dy = midpoint_y - BOARD_CENTER_Y
            length = max(1.0, math.hypot(dx, dy))
            offset_x = dx / length * 34
            offset_y = dy / length * 34
            label_x = midpoint_x + offset_x
            label_y = midpoint_y + offset_y

            pygame.draw.line(
                screen,
                (180, 210, 230),
                (midpoint_x, midpoint_y),
                (label_x, label_y),
                2,
            )
            label = harbor.label
            text_surface = font.render(label, True, COLORS["BLACK"])
            badge_rect = text_surface.get_rect()
            badge_rect.inflate_ip(18, 12)
            badge_rect.center = (int(label_x), int(label_y))
            pygame.draw.rect(screen, (228, 240, 246), badge_rect, border_radius=10)
            pygame.draw.rect(screen, (70, 95, 116), badge_rect, 1, border_radius=10)
            screen.blit(text_surface, text_surface.get_rect(center=badge_rect.center))

    def draw(self, screen):
        for tile in self.tiles:
            tile.draw(screen, robber_tile=self.robber_tile)

        self._draw_harbors(screen)

        for road in self.roads:
            pygame.draw.line(
                screen,
                road.owner.color,
                (road.node1.x, road.node1.y),
                (road.node2.x, road.node2.y),
                8,
            )

        for node in self.nodes:
            if node.building is None:
                continue
            color = node.building.owner.color
            center = (int(node.x), int(node.y))
            if node.building.building_type == BuildingType.CITY:
                rect = pygame.Rect(0, 0, 20, 20)
                rect.center = center
                pygame.draw.rect(screen, color, rect, border_radius=4)
                pygame.draw.rect(screen, COLORS["BLACK"], rect, 2, border_radius=4)
            else:
                pygame.draw.circle(screen, color, center, 8)
                pygame.draw.circle(screen, COLORS["BLACK"], center, 8, 1)

    def move_robber_to(self, tile: HexTile):
        self.robber_tile = tile

    def get_tiles_with_number(self, dice_number):
        return [tile for tile in self.tiles if tile.number == dice_number]
