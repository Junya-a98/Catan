import math
import random

import pygame

from game.assets import get_font
from game.constants import (
    BOARD_CENTER_X,
    BOARD_CENTER_Y,
    COLORS,
    HEX_RADIUS,
    LOG_PANEL_WIDTH,
    SCREEN_HEIGHT,
    SIDE_PANEL_X,
)
from game.harbor import Harbor
from game.hex_tile import HexTile, get_token_pip_count
from game.node import Node
from game.resources import RESOURCE_COLORS, ResourceType


HEX_DIRECTIONS = (
    (1, 0),
    (1, -1),
    (0, -1),
    (-1, 0),
    (-1, 1),
    (0, 1),
)
CONSTRAINED_NUMBER_ATTEMPTS = 8000
CONSTRAINED_HARBOR_ATTEMPTS = 500


def _load_font(size):
    return get_font(size)


class GameBoard:
    def __init__(self, mode="constrained", seed=None):
        if mode == "balanced":
            mode = "constrained"
        if mode not in ("constrained", "fully_random"):
            raise ValueError(f"Unsupported board mode: {mode}")

        self.mode = mode
        self.seed = seed
        self.rng = random.Random(seed)
        self.roads = []
        self.tiles = []
        self.nodes = []
        self.edges = []
        self.perimeter_edges = []
        self.harbors = []
        self.robber_tile = None
        self.setup_board()

    def setup_board(self):
        resources = self._build_resource_pool()
        self.rng.shuffle(resources)

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
            tile = HexTile(x, y, resource, None)
            tile.axial = (q, r)
            self.tiles.append(tile)
            if resource == ResourceType.DESERT:
                self.robber_tile = tile

        self._assign_numbers()
        self._create_nodes_for_tiles()
        self._create_edges()
        self._create_harbors()

    def _build_resource_pool(self):
        resources = [ResourceType.DESERT]
        for resource in [res for res in ResourceType if res != ResourceType.DESERT]:
            if resource in (ResourceType.BRICK, ResourceType.ORE):
                resources.extend([resource] * 3)
            else:
                resources.extend([resource] * 4)
        return resources

    def _build_number_pool(self):
        return [2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12]

    def _assign_numbers(self):
        land_tiles = [tile for tile in self.tiles if tile.resource_type != ResourceType.DESERT]
        numbers = self._build_number_pool()
        adjacency = self._get_tile_adjacency()

        if self.mode == "fully_random":
            candidate = numbers[:]
            for _ in range(CONSTRAINED_NUMBER_ATTEMPTS):
                self.rng.shuffle(candidate)
                assignment = {tile: number for tile, number in zip(land_tiles, candidate)}
                if not self._has_adjacent_red_numbers(assignment, adjacency):
                    break
            for tile, number in zip(land_tiles, candidate):
                tile.number = number
            return

        best_numbers = None
        best_score = None

        for _ in range(CONSTRAINED_NUMBER_ATTEMPTS):
            candidate = numbers[:]
            self.rng.shuffle(candidate)
            assignment = {tile: number for tile, number in zip(land_tiles, candidate)}
            score = self._score_number_assignment(assignment, adjacency)
            if best_score is None or score < best_score:
                best_score = score
                best_numbers = candidate[:]
            if score == 0:
                best_numbers = candidate[:]
                break

        for tile, number in zip(land_tiles, best_numbers):
            tile.number = number

    def _has_adjacent_red_numbers(self, assignment, adjacency):
        for tile, number in assignment.items():
            if number not in (6, 8):
                continue
            if any(assignment.get(neighbor) in (6, 8) for neighbor in adjacency[tile]):
                return True
        return False

    def _get_tile_adjacency(self):
        by_coord = {tile.axial: tile for tile in self.tiles}
        adjacency = {tile: [] for tile in self.tiles}
        for tile in self.tiles:
            q, r = tile.axial
            for dq, dr in HEX_DIRECTIONS:
                neighbor = by_coord.get((q + dq, r + dr))
                if neighbor is not None:
                    adjacency[tile].append(neighbor)
        return adjacency

    def _score_number_assignment(self, assignment, adjacency):
        high_numbers = {6, 8}
        score = 0

        for tile, number in assignment.items():
            if number not in high_numbers:
                continue
            for neighbor in adjacency[tile]:
                neighbor_number = assignment.get(neighbor)
                if neighbor_number in high_numbers and tile.axial < neighbor.axial:
                    score += 1000

        high_number_resources = {}
        for tile, number in assignment.items():
            if number in high_numbers:
                high_number_resources[tile.resource_type] = high_number_resources.get(tile.resource_type, 0) + 1
        for count in high_number_resources.values():
            if count > 1:
                score += (count - 1) * 1800

        resource_pips = {}
        for tile, number in assignment.items():
            resource_pips.setdefault(tile.resource_type, []).append(get_token_pip_count(number))

        total_pips = sum(get_token_pip_count(number) for number in assignment.values())
        average_pips_per_tile = total_pips / max(1, len(assignment))
        for resource_type, pip_values in resource_pips.items():
            expected_total = len(pip_values) * average_pips_per_tile
            actual_total = sum(pip_values)
            score += int((actual_total - expected_total) ** 2 * 5)

            strongest_two = sorted(pip_values, reverse=True)[:2]
            if sum(strongest_two) > 8:
                score += (sum(strongest_two) - 8) * 36
            if pip_values.count(5) > 1:
                score += (pip_values.count(5) - 1) * 1800

        for tile, number in assignment.items():
            for neighbor in adjacency[tile]:
                if tile.axial >= neighbor.axial or neighbor not in assignment:
                    continue
                combined_pips = get_token_pip_count(number) + get_token_pip_count(assignment[neighbor])
                if combined_pips > 8:
                    score += (combined_pips - 8) * 4

        return score

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

        selected_edges = [sorted_edges[index] for index in sorted(used_indices)]
        harbor_types = self._select_harbor_types(harbor_types, selected_edges)

        self.harbors = []
        for harbor_type, (node1, node2) in zip(harbor_types, selected_edges):
            harbor = Harbor(node1, node2, 3 if harbor_type is None else 2, harbor_type)
            self.harbors.append(harbor)
            node1.harbors.append(harbor)
            node2.harbors.append(harbor)

    def _select_harbor_types(self, harbor_types, selected_edges):
        if self.mode == "fully_random":
            self.rng.shuffle(harbor_types)
            return harbor_types

        best_layout = None
        best_score = None
        for _ in range(CONSTRAINED_HARBOR_ATTEMPTS):
            candidate = harbor_types[:]
            self.rng.shuffle(candidate)
            score = self._score_harbor_assignment(candidate, selected_edges)
            if best_score is None or score < best_score:
                best_score = score
                best_layout = candidate[:]
            if score == 0:
                break
        return best_layout

    def _score_harbor_assignment(self, harbor_types, selected_edges):
        score = 0
        for harbor_type, edge in zip(harbor_types, selected_edges):
            adjacent_tiles = self.get_edge_adjacent_tiles(edge)
            if harbor_type is None:
                generic_pips = sum(
                    get_token_pip_count(tile.number)
                    for tile in adjacent_tiles
                    if tile.number is not None
                )
                if generic_pips > 8:
                    score += (generic_pips - 8) * 2
                continue

            matching_tiles = [
                tile
                for tile in adjacent_tiles
                if tile.resource_type == harbor_type and tile.number is not None
            ]
            match_pips = sum(get_token_pip_count(tile.number) for tile in matching_tiles)
            score += match_pips * 24
            score += sum(900 for tile in matching_tiles if tile.number in (6, 8))

        return score

    def get_edge_adjacent_tiles(self, edge):
        node1, node2 = edge
        adjacent_tiles = []
        for tile in node1.tiles:
            if tile in node2.tiles and tile.resource_type != ResourceType.DESERT:
                adjacent_tiles.append(tile)
        return adjacent_tiles

    def get_resource_high_number_counts(self):
        counts = {
            ResourceType.WOOD: 0,
            ResourceType.SHEEP: 0,
            ResourceType.WHEAT: 0,
            ResourceType.BRICK: 0,
            ResourceType.ORE: 0,
        }
        for tile in self.tiles:
            if tile.resource_type == ResourceType.DESERT or tile.number not in (6, 8):
                continue
            counts[tile.resource_type] += 1
        return counts

    def get_resource_pip_totals(self):
        totals = {
            ResourceType.WOOD: 0,
            ResourceType.SHEEP: 0,
            ResourceType.WHEAT: 0,
            ResourceType.BRICK: 0,
            ResourceType.ORE: 0,
        }
        for tile in self.tiles:
            if tile.resource_type == ResourceType.DESERT or tile.number is None:
                continue
            totals[tile.resource_type] += get_token_pip_count(tile.number)
        return totals

    def harbor_matches_high_value_tile(self, harbor):
        if harbor.resource_type is None:
            return False
        return any(
            tile.resource_type == harbor.resource_type and tile.number in (6, 8)
            for tile in self.get_edge_adjacent_tiles((harbor.node1, harbor.node2))
        )

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

    def _get_harbor_badge_rect(self, text_surface, position):
        safe_badge_area = pygame.Rect(
            LOG_PANEL_WIDTH + 20,
            12,
            SIDE_PANEL_X - LOG_PANEL_WIDTH - 32,
            SCREEN_HEIGHT - 226,
        )
        badge_rect = text_surface.get_rect()
        badge_rect.inflate_ip(30, 16)
        badge_rect.center = (int(position[0]), int(position[1]))
        badge_rect.clamp_ip(safe_badge_area)
        return badge_rect

    @staticmethod
    def _draw_harbor_dock(screen, harbor):
        """Draw a compact pier outside the playable coastal edge.

        Roads may legally occupy the harbor's coastal edge, so the dock must not
        use that edge as a wooden crossbeam.  Two short piers start just offshore
        and meet a smaller outward crossbeam; the returned point is where the
        harbor sign connector should begin.
        """
        start = (float(harbor.node1.x), float(harbor.node1.y))
        end = (float(harbor.node2.x), float(harbor.node2.y))
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = max(1.0, math.hypot(dx, dy))
        axis = (dx / length, dy / length)
        midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)

        # Pick the edge normal that points away from the island center.
        outward = (-axis[1], axis[0])
        radial = (midpoint[0] - BOARD_CENTER_X, midpoint[1] - BOARD_CENTER_Y)
        if outward[0] * radial[0] + outward[1] * radial[1] < 0:
            outward = (-outward[0], -outward[1])

        half_span = min(12.0, length * 0.24)
        shore_gap = 7.0
        pier_length = 13.0

        def local_point(along, away):
            return (
                round(midpoint[0] + axis[0] * along + outward[0] * away),
                round(midpoint[1] + axis[1] * along + outward[1] * away),
            )

        inner_points = (
            local_point(-half_span, shore_gap),
            local_point(half_span, shore_gap),
        )
        outer_points = (
            local_point(-half_span, shore_gap + pier_length),
            local_point(half_span, shore_gap + pier_length),
        )
        shadow_shift = (outward[0] * 2 + axis[0], outward[1] * 2 + axis[1])

        def shifted(point, amount=1.0):
            return (
                round(point[0] + shadow_shift[0] * amount),
                round(point[1] + shadow_shift[1] * amount),
            )

        # Perpendicular pier arms sit entirely outside the road-bearing edge.
        for along, inner, outer in zip(
            (-half_span, half_span),
            inner_points,
            outer_points,
        ):
            pygame.draw.line(screen, (37, 31, 27), shifted(inner), shifted(outer), 9)
            pygame.draw.line(screen, (70, 49, 33), inner, outer, 8)
            pygame.draw.line(screen, (158, 105, 59), inner, outer, 5)
            highlight_start = local_point(
                along - 1.2,
                shore_gap + 1.5,
            )
            highlight_end = local_point(
                along - 1.2,
                shore_gap + pier_length - 1.5,
            )
            pygame.draw.line(screen, (230, 176, 103), highlight_start, highlight_end, 1)

        # The crossbeam is offshore rather than on top of a possible road.
        pygame.draw.line(
            screen,
            (37, 31, 27),
            shifted(outer_points[0]),
            shifted(outer_points[1]),
            10,
        )
        pygame.draw.line(screen, (70, 49, 33), outer_points[0], outer_points[1], 9)
        pygame.draw.line(screen, (158, 105, 59), outer_points[0], outer_points[1], 6)
        crossbeam_highlight = tuple(
            local_point(along, shore_gap + pier_length - 1.5)
            for along in (-half_span + 1.5, half_span - 1.5)
        )
        pygame.draw.line(screen, (232, 180, 108), *crossbeam_highlight, 1)

        for point in outer_points:
            pygame.draw.circle(screen, (52, 39, 30), shifted(point, 0.7), 5)
            pygame.draw.circle(screen, (177, 118, 66), point, 4)
            post_highlight = (round(point[0] - 1), round(point[1] - 1))
            pygame.draw.circle(screen, (235, 187, 118), post_highlight, 2)

        return local_point(0, shore_gap + pier_length)

    def _draw_harbors(self, screen):
        font = _load_font(17)
        for harbor in self.harbors:
            connector_start = self._draw_harbor_dock(screen, harbor)
            midpoint_x = (harbor.node1.x + harbor.node2.x) / 2
            midpoint_y = (harbor.node1.y + harbor.node2.y) / 2
            dx = midpoint_x - BOARD_CENTER_X
            dy = midpoint_y - BOARD_CENTER_Y
            length = max(1.0, math.hypot(dx, dy))
            offset_x = dx / length * 48
            offset_y = dy / length * 48
            label_x = midpoint_x + offset_x
            label_y = midpoint_y + offset_y

            label = harbor.label
            text_surface = font.render(label, True, COLORS["BLACK"])
            badge_rect = self._get_harbor_badge_rect(text_surface, (label_x, label_y))
            label_x, label_y = badge_rect.center

            pygame.draw.line(
                screen,
                (56, 48, 39),
                (connector_start[0] + 2, connector_start[1] + 3),
                (round(label_x + 2), round(label_y + 3)),
                4,
            )
            pygame.draw.line(
                screen,
                (218, 188, 136),
                connector_start,
                (round(label_x), round(label_y)),
                2,
            )

            shadow_rect = badge_rect.move(3, 4)
            pygame.draw.rect(screen, (43, 35, 29), shadow_rect, border_radius=8)
            pygame.draw.rect(screen, (218, 181, 119), badge_rect, border_radius=8)
            inner_rect = badge_rect.inflate(-4, -4)
            pygame.draw.rect(screen, (238, 211, 159), inner_rect, border_radius=6)
            pygame.draw.line(
                screen,
                (255, 236, 193),
                (inner_rect.left + 7, inner_rect.top + 3),
                (inner_rect.right - 7, inner_rect.top + 3),
                2,
            )
            pygame.draw.rect(screen, (83, 58, 39), badge_rect, 2, border_radius=8)

            accent_color = (95, 145, 178)
            if harbor.resource_type is not None:
                raw_color = RESOURCE_COLORS[harbor.resource_type]
                accent_color = tuple((channel * 2 + 170) // 3 for channel in raw_color)
            accent_rect = pygame.Rect(
                badge_rect.left + 5,
                badge_rect.top + 5,
                7,
                badge_rect.height - 10,
            )
            pygame.draw.rect(screen, accent_color, accent_rect, border_radius=3)
            pygame.draw.rect(screen, (73, 57, 43), accent_rect, 1, border_radius=3)
            screen.blit(text_surface, text_surface.get_rect(center=badge_rect.center))

    def draw(self, screen):
        for tile in self.tiles:
            tile.draw(screen, robber_tile=self.robber_tile)

        self._draw_harbors(screen)

        for road in self.roads:
            road.draw(screen)

        for node in self.nodes:
            if node.building is None:
                continue
            node.building.draw(screen, (node.x, node.y))

    def move_robber_to(self, tile: HexTile):
        self.robber_tile = tile

    def get_tiles_with_number(self, dice_number):
        return [tile for tile in self.tiles if tile.number == dice_number]
