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
HARBOR_BADGE_OUTWARD_DISTANCES = (72, 84, 96, 108, 120, 60, 132)
HARBOR_BADGE_TANGENT_OFFSETS = (0, -64, 64, -92, 92, -124, 124, -156, 156, -188, 188)
HARBOR_BADGE_GRID_STEP = 24
HARBOR_BADGE_REFINEMENT_STEP = 8
HARBOR_BADGE_GAP = 6
HARBOR_BADGE_SHADOW_OFFSET = (3, 4)
HARBOR_BADGE_PADDING = (30, 16)
HARBOR_BADGE_COMPACT_PADDING = (24, 8)
HARBOR_BADGE_CANDIDATE_LIMIT = 160
HARBOR_LAYOUT_SEARCH_LIMIT = 2000
HARBOR_BADGE_SAFE_TOP = 112
# Player cards begin at y=586.  The badge shadow extends four pixels below its
# body, so keep the body at or above 582 as well.
HARBOR_BADGE_SAFE_BOTTOM = SCREEN_HEIGHT - 218
HARBOR_BUILDING_CLEARANCE = 6
HARBOR_ROAD_CLEARANCE = 13
HARBOR_TILE_CLEARANCE = 0
HARBOR_CONNECTOR_LEAD_DISTANCE = 38


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
        self._harbor_badge_layout_cache = None
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

    @staticmethod
    def _get_harbor_safe_badge_area():
        return pygame.Rect(
            LOG_PANEL_WIDTH + 20,
            HARBOR_BADGE_SAFE_TOP,
            SIDE_PANEL_X - LOG_PANEL_WIDTH - 32,
            HARBOR_BADGE_SAFE_BOTTOM - HARBOR_BADGE_SAFE_TOP,
        )

    @staticmethod
    def _get_harbor_edge_geometry(harbor):
        start = (float(harbor.node1.x), float(harbor.node1.y))
        end = (float(harbor.node2.x), float(harbor.node2.y))
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = max(1.0, math.hypot(dx, dy))
        axis = (dx / length, dy / length)
        midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)

        # A perimeter edge has two normals. Select the one facing the water,
        # rather than the radial direction from the board center. The latter is
        # noticeably inaccurate at diagonal coastlines and used to push badges
        # back over their road/building positions when a screen edge clamped it.
        outward = (-axis[1], axis[0])
        radial = (midpoint[0] - BOARD_CENTER_X, midpoint[1] - BOARD_CENTER_Y)
        if outward[0] * radial[0] + outward[1] * radial[1] < 0:
            outward = (-outward[0], -outward[1])
        return midpoint, axis, outward, length

    @staticmethod
    def _get_harbor_building_exclusion_rect(node):
        # The city is the larger of the two pieces (roughly 36 x 34 including
        # outline/shadow). Reserve a little water around it so label and piece
        # silhouettes remain visually separate even with antialiasing.
        center_x = round(node.x)
        center_y = round(node.y)
        half_width = 18 + HARBOR_BUILDING_CLEARANCE
        half_height = 17 + HARBOR_BUILDING_CLEARANCE
        return pygame.Rect(
            center_x - half_width,
            center_y - half_height,
            half_width * 2,
            half_height * 2,
        )

    @staticmethod
    def _get_harbor_badge_visual_rect(badge_rect):
        return badge_rect.union(badge_rect.move(*HARBOR_BADGE_SHADOW_OFFSET))

    def get_harbor_badge_layout(self, font=None):
        """Expose stable harbor-to-badge rectangles for UI/tests."""
        if font is None:
            font = _load_font(17)
        return tuple(
            (harbor, badge_rect.copy())
            for harbor, _, badge_rect in self._layout_harbor_badges(font)
        )

    def get_harbor_building_exclusion_rect(self, node):
        """Return the city-sized piece clearance reserved around a node."""
        return self._get_harbor_building_exclusion_rect(node)

    def get_harbor_badge_visual_rect(self, badge_rect):
        """Return the badge bounds including its drop shadow."""
        return self._get_harbor_badge_visual_rect(badge_rect)

    @staticmethod
    def _rect_overlaps_circle(rect, center, radius):
        nearest_x = max(rect.left, min(float(center[0]), rect.right))
        nearest_y = max(rect.top, min(float(center[1]), rect.bottom))
        return (
            math.hypot(float(center[0]) - nearest_x, float(center[1]) - nearest_y)
            < radius
        )

    def harbor_badge_overlaps_tile(self, badge_rect, tile):
        """Whether a badge enters a tile's circumcircle and token area."""
        visual_rect = self._get_harbor_badge_visual_rect(badge_rect)
        return self._rect_overlaps_circle(
            visual_rect,
            (tile.x, tile.y),
            HEX_RADIUS + HARBOR_TILE_CLEARANCE,
        )

    def _get_harbor_protected_nodes(self, harbor):
        protected = [harbor.node1, harbor.node2]
        protected_ids = {id(harbor.node1), id(harbor.node2)}
        perimeter_node_ids = {
            id(node)
            for edge in self.perimeter_edges
            for node in edge
        }
        for node in self.nodes:
            if (
                node.building is None
                or id(node) in protected_ids
                or id(node) not in perimeter_node_ids
            ):
                continue
            protected.append(node)
            protected_ids.add(id(node))
        return protected

    def _get_harbor_protected_edges(self, harbor):
        protected = [(harbor.node1, harbor.node2)]
        protected_keys = {frozenset((id(harbor.node1), id(harbor.node2)))}
        perimeter_keys = {
            frozenset((id(node1), id(node2)))
            for node1, node2 in self.perimeter_edges
        }
        for road in self.roads:
            key = frozenset((id(road.node1), id(road.node2)))
            if key in protected_keys or key not in perimeter_keys:
                continue
            protected.append((road.node1, road.node2))
            protected_keys.add(key)
        return protected

    def _get_harbor_clearance_context(self, harbor):
        building_rects = tuple(
            self._get_harbor_building_exclusion_rect(node)
            for node in self._get_harbor_protected_nodes(harbor)
        )
        road_lines = tuple(
            (
                (round(node1.x), round(node1.y)),
                (round(node2.x), round(node2.y)),
            )
            for node1, node2 in self._get_harbor_protected_edges(harbor)
        )
        return {
            "building_rects": building_rects,
            "road_lines": road_lines,
            "tile_centers": tuple((tile.x, tile.y) for tile in self.tiles),
            "own_building_rects": (
                self._get_harbor_building_exclusion_rect(harbor.node1),
                self._get_harbor_building_exclusion_rect(harbor.node2),
            ),
        }

    def _get_harbor_connector_path(self, harbor, badge_rect):
        midpoint, _, outward, _ = self._get_harbor_edge_geometry(harbor)

        def outward_point(distance):
            return (
                round(midpoint[0] + outward[0] * distance),
                round(midpoint[1] + outward[1] * distance),
            )

        # The short outward lead clears both endpoint pieces before a badge has
        # to slide tangentially along a clipped screen edge.
        return (
            outward_point(20),
            outward_point(HARBOR_CONNECTOR_LEAD_DISTANCE),
            badge_rect.center,
        )

    def _harbor_badge_conflict_score(
        self,
        badge_rect,
        harbor,
        occupied_badges,
        clearance_context=None,
    ):
        if clearance_context is None:
            clearance_context = self._get_harbor_clearance_context(harbor)
        visual_rect = self._get_harbor_badge_visual_rect(badge_rect)
        building_rects = clearance_context["building_rects"]
        building_overlap = sum(
            visual_rect.clip(building_rect).width * visual_rect.clip(building_rect).height
            for building_rect in building_rects
            if visual_rect.colliderect(building_rect)
        )

        road_probe = visual_rect.inflate(
            HARBOR_ROAD_CLEARANCE * 2,
            HARBOR_ROAD_CLEARANCE * 2,
        )
        road_conflicts = sum(
            bool(
                road_probe.clipline(line_start, line_end)
            )
            for line_start, line_end in clearance_context["road_lines"]
        )
        tile_conflicts = sum(
            self._rect_overlaps_circle(
                visual_rect,
                tile_center,
                HEX_RADIUS + HARBOR_TILE_CLEARANCE,
            )
            for tile_center in clearance_context["tile_centers"]
        )

        badge_probe = visual_rect.inflate(HARBOR_BADGE_GAP * 2, HARBOR_BADGE_GAP * 2)
        badge_overlap = 0
        for occupied in occupied_badges:
            occupied_visual = self._get_harbor_badge_visual_rect(occupied)
            if not badge_probe.colliderect(occupied_visual):
                continue
            overlap = badge_probe.clip(occupied_visual)
            badge_overlap += overlap.width * overlap.height

        # Only the diagonal leg needs testing: the first connector leg runs
        # straight out through water. This prevents a fallback label from
        # routing its sign line back through either of its own endpoint pieces.
        connector_path = self._get_harbor_connector_path(harbor, badge_rect)
        connector_conflicts = sum(
            bool(building_rect.clipline(connector_path[1], connector_path[2]))
            for building_rect in clearance_context["own_building_rects"]
        )

        conflict_count = (
            (building_overlap > 0)
            + road_conflicts
            + tile_conflicts
            + (badge_overlap > 0)
            + connector_conflicts
        )
        overlap_score = (
            building_overlap
            + badge_overlap
            + road_conflicts * 1000
            + tile_conflicts * 1000
            + connector_conflicts * 1000
        )
        return int(conflict_count), overlap_score

    def _harbor_badge_is_clear(
        self,
        badge_rect,
        harbor,
        clearance_context=None,
    ):
        """Fast zero-conflict check used while building candidate sets.

        The scoring variant above intentionally measures every collision so it
        can rank an imperfect fallback.  Candidate generation only needs a
        yes/no answer, however, and dense late-game coasts produce thousands of
        probes.  Returning as soon as one obstacle is found keeps the refined
        search inexpensive without changing its clearance rules.
        """
        if clearance_context is None:
            clearance_context = self._get_harbor_clearance_context(harbor)
        visual_rect = self._get_harbor_badge_visual_rect(badge_rect)

        if any(
            visual_rect.colliderect(building_rect)
            for building_rect in clearance_context["building_rects"]
        ):
            return False

        road_probe = visual_rect.inflate(
            HARBOR_ROAD_CLEARANCE * 2,
            HARBOR_ROAD_CLEARANCE * 2,
        )
        if any(
            road_probe.clipline(line_start, line_end)
            for line_start, line_end in clearance_context["road_lines"]
        ):
            return False

        if any(
            self._rect_overlaps_circle(
                visual_rect,
                tile_center,
                HEX_RADIUS + HARBOR_TILE_CLEARANCE,
            )
            for tile_center in clearance_context["tile_centers"]
        ):
            return False

        connector_path = self._get_harbor_connector_path(harbor, badge_rect)
        return not any(
            building_rect.clipline(connector_path[1], connector_path[2])
            for building_rect in clearance_context["own_building_rects"]
        )

    def _iter_harbor_badge_candidates(self, badge_rect, harbor):
        safe_badge_area = self._get_harbor_safe_badge_area()
        midpoint, axis, outward, _ = self._get_harbor_edge_geometry(harbor)
        preferred_center = (
            midpoint[0] + outward[0] * HARBOR_BADGE_OUTWARD_DISTANCES[0],
            midpoint[1] + outward[1] * HARBOR_BADGE_OUTWARD_DISTANCES[0],
        )
        seen = set()

        for outward_distance in HARBOR_BADGE_OUTWARD_DISTANCES:
            for tangent_offset in HARBOR_BADGE_TANGENT_OFFSETS:
                candidate = badge_rect.copy()
                candidate.center = (
                    round(
                        midpoint[0]
                        + outward[0] * outward_distance
                        + axis[0] * tangent_offset
                    ),
                    round(
                        midpoint[1]
                        + outward[1] * outward_distance
                        + axis[1] * tangent_offset
                    ),
                )
                candidate.clamp_ip(safe_badge_area)
                key = tuple(candidate)
                if key in seen:
                    continue
                seen.add(key)
                yield candidate

        # Dense late-game coastlines can occupy several local candidates. A
        # deterministic water-area grid is a last resort; nearest positions to
        # the preferred outward point are considered first.
        max_left = safe_badge_area.right - badge_rect.width
        max_top = safe_badge_area.bottom - badge_rect.height
        lefts = list(range(safe_badge_area.left, max_left + 1, HARBOR_BADGE_GRID_STEP))
        tops = list(range(safe_badge_area.top, max_top + 1, HARBOR_BADGE_GRID_STEP))
        if not lefts or lefts[-1] != max_left:
            lefts.append(max_left)
        if not tops or tops[-1] != max_top:
            tops.append(max_top)
        grid_candidates = []
        for top in tops:
            for left in lefts:
                candidate = badge_rect.copy()
                candidate.topleft = (left, top)
                distance = (
                    (candidate.centerx - preferred_center[0]) ** 2
                    + (candidate.centery - preferred_center[1]) ** 2
                )
                grid_candidates.append((distance, top, left, candidate))
        grid_candidates.sort(key=lambda item: item[:3])
        for _, _, _, candidate in grid_candidates:
            key = tuple(candidate)
            if key in seen:
                continue
            seen.add(key)
            yield candidate

    def _get_refined_harbor_badge_candidates(
        self,
        badge_rect,
        harbor,
        clearance_context,
    ):
        """Return a deterministic 8 px refinement of the normal candidates.

        Normal boards are solved from the cheaper 24 px grid.  A coast filled
        with roads and spacing-valid cities can leave narrow water lanes whose
        valid top-left coordinates fall between those grid points.  Offsetting
        every coarse point by -8/0/+8 covers every residue of that same grid at
        8 px resolution, while avoiding an expensive full-screen pixel scan.
        """
        safe_badge_area = self._get_harbor_safe_badge_area()
        offsets = (
            (0, 0),
            (-HARBOR_BADGE_REFINEMENT_STEP, 0),
            (HARBOR_BADGE_REFINEMENT_STEP, 0),
            (0, -HARBOR_BADGE_REFINEMENT_STEP),
            (0, HARBOR_BADGE_REFINEMENT_STEP),
            (-HARBOR_BADGE_REFINEMENT_STEP, -HARBOR_BADGE_REFINEMENT_STEP),
            (HARBOR_BADGE_REFINEMENT_STEP, -HARBOR_BADGE_REFINEMENT_STEP),
            (-HARBOR_BADGE_REFINEMENT_STEP, HARBOR_BADGE_REFINEMENT_STEP),
            (HARBOR_BADGE_REFINEMENT_STEP, HARBOR_BADGE_REFINEMENT_STEP),
        )
        candidates = []
        seen = set()
        for coarse_candidate in self._iter_harbor_badge_candidates(
            badge_rect,
            harbor,
        ):
            for offset_x, offset_y in offsets:
                candidate = coarse_candidate.move(offset_x, offset_y)
                candidate.clamp_ip(safe_badge_area)
                key = tuple(candidate)
                if key in seen:
                    continue
                seen.add(key)
                if self._harbor_badge_is_clear(
                    candidate,
                    harbor,
                    clearance_context,
                ):
                    candidates.append(candidate)
        return candidates

    def _get_harbor_badge_rect(
        self,
        text_surface,
        position,
        *,
        harbor=None,
        occupied_badges=(),
    ):
        safe_badge_area = self._get_harbor_safe_badge_area()
        badge_rect = text_surface.get_rect()
        badge_rect.inflate_ip(*HARBOR_BADGE_PADDING)
        badge_rect.center = (int(position[0]), int(position[1]))
        badge_rect.clamp_ip(safe_badge_area)
        if harbor is None:
            return badge_rect

        clearance_context = self._get_harbor_clearance_context(harbor)
        for candidate in self._iter_harbor_badge_candidates(badge_rect, harbor):
            conflict_count, _ = self._harbor_badge_conflict_score(
                candidate,
                harbor,
                occupied_badges,
                clearance_context,
            )
            if conflict_count == 0:
                return candidate
        raise RuntimeError("No collision-free harbor badge position exists")

    def _layout_harbor_badges(self, font):
        perimeter_keys = {
            frozenset((id(node1), id(node2)))
            for node1, node2 in self.perimeter_edges
        }
        perimeter_node_ids = {
            id(node)
            for edge in self.perimeter_edges
            for node in edge
        }
        cache_key = (
            id(font),
            tuple((id(harbor), harbor.label) for harbor in self.harbors),
            tuple(
                (id(road.node1), id(road.node2))
                for road in self.roads
                if frozenset((id(road.node1), id(road.node2))) in perimeter_keys
            ),
            tuple(
                id(node)
                for node in self.nodes
                if node.building is not None and id(node) in perimeter_node_ids
            ),
        )
        if (
            self._harbor_badge_layout_cache is not None
            and self._harbor_badge_layout_cache[0] == cache_key
        ):
            return self._harbor_badge_layout_cache[1]

        specs = []
        candidate_lists = []
        for harbor in self.harbors:
            text_surface = font.render(harbor.label, True, COLORS["BLACK"])
            midpoint, _, outward, _ = self._get_harbor_edge_geometry(harbor)
            preferred_position = (
                midpoint[0] + outward[0] * HARBOR_BADGE_OUTWARD_DISTANCES[0],
                midpoint[1] + outward[1] * HARBOR_BADGE_OUTWARD_DISTANCES[0],
            )
            base_rect = text_surface.get_rect().inflate(*HARBOR_BADGE_PADDING)
            base_rect.center = (int(preferred_position[0]), int(preferred_position[1]))
            base_rect.clamp_ip(self._get_harbor_safe_badge_area())
            candidates = []
            clearance_context = self._get_harbor_clearance_context(harbor)
            for candidate in self._iter_harbor_badge_candidates(base_rect, harbor):
                if not self._harbor_badge_is_clear(
                    candidate,
                    harbor,
                    clearance_context,
                ):
                    continue
                candidates.append(candidate)
                if len(candidates) >= HARBOR_BADGE_CANDIDATE_LIMIT:
                    break
            specs.append((harbor, text_surface, base_rect))
            candidate_lists.append(candidates)

        solved_rects = self._solve_harbor_badge_candidates(candidate_lists)
        if solved_rects is None:
            # A late-game coast can leave valid water lanes between the normal
            # 24 px grid points.  Refine only after the inexpensive, visually
            # preferred layout has failed, so ordinary boards do not move.
            refined_candidate_lists = []
            compact_specs = []
            for harbor, text_surface, _ in specs:
                midpoint, _, outward, _ = self._get_harbor_edge_geometry(harbor)
                preferred_position = (
                    midpoint[0] + outward[0] * HARBOR_BADGE_OUTWARD_DISTANCES[0],
                    midpoint[1] + outward[1] * HARBOR_BADGE_OUTWARD_DISTANCES[0],
                )
                compact_rect = text_surface.get_rect().inflate(
                    *HARBOR_BADGE_COMPACT_PADDING
                )
                compact_rect.center = (
                    int(preferred_position[0]),
                    int(preferred_position[1]),
                )
                compact_rect.clamp_ip(self._get_harbor_safe_badge_area())
                clearance_context = self._get_harbor_clearance_context(harbor)
                refined_candidate_lists.append(
                    self._get_refined_harbor_badge_candidates(
                        compact_rect,
                        harbor,
                        clearance_context,
                    )
                )
                compact_specs.append((harbor, text_surface, compact_rect))
            solved_rects = self._find_harbor_badge_candidate_assignment(
                refined_candidate_lists
            )
            if solved_rects is not None:
                specs = compact_specs
        if solved_rects is None:
            # Never degrade to an overlapping placement.  Raising makes an
            # impossible future screen layout explicit instead of covering
            # roads, cities, tiles, another harbor, or the player cards.
            raise RuntimeError("No collision-free harbor badge layout exists")

        placements = tuple(
            (harbor, text_surface, badge_rect)
            for (harbor, text_surface, _), badge_rect in zip(specs, solved_rects)
        )
        self._harbor_badge_layout_cache = (cache_key, placements)
        return placements

    def _solve_harbor_badge_candidates(self, candidate_lists):
        if any(not candidates for candidates in candidate_lists):
            return None

        search_order = sorted(
            range(len(candidate_lists)),
            key=lambda index: (
                len(candidate_lists[index]),
                -candidate_lists[index][0].width,
                index,
            ),
        )
        selected = {}
        best_selected = None
        best_cost = None
        visited = 0

        def overlaps_selected(candidate):
            candidate_visual = self._get_harbor_badge_visual_rect(candidate)
            candidate_probe = candidate_visual.inflate(
                HARBOR_BADGE_GAP * 2,
                HARBOR_BADGE_GAP * 2,
            )
            return any(
                candidate_probe.colliderect(
                    self._get_harbor_badge_visual_rect(selected_rect)
                )
                for selected_rect in selected.values()
            )

        def search(depth, cost):
            nonlocal best_cost, best_selected, visited
            visited += 1
            if visited > HARBOR_LAYOUT_SEARCH_LIMIT:
                return
            if best_cost is not None and cost >= best_cost:
                return
            if depth == len(search_order):
                best_cost = cost
                best_selected = dict(selected)
                return

            harbor_index = search_order[depth]
            for candidate_rank, candidate in enumerate(candidate_lists[harbor_index]):
                next_cost = cost + candidate_rank
                if best_cost is not None and next_cost >= best_cost:
                    break
                if overlaps_selected(candidate):
                    continue
                selected[harbor_index] = candidate
                search(depth + 1, next_cost)
                del selected[harbor_index]

        search(0, 0)
        if best_selected is None:
            return None
        return [best_selected[index] for index in range(len(candidate_lists))]

    def _find_harbor_badge_candidate_assignment(self, candidate_lists):
        """Find the first deterministic collision-free refined assignment.

        Dynamic minimum-remaining-values ordering avoids the large search tree
        that a fixed harbor order creates on a crowded coastline.  Candidate
        lists themselves retain preference order, so ties are deterministic.
        """
        if any(not candidates for candidates in candidate_lists):
            return None

        selected = {}

        def badges_overlap(candidate, other):
            candidate_visual = self._get_harbor_badge_visual_rect(candidate)
            candidate_probe = candidate_visual.inflate(
                HARBOR_BADGE_GAP * 2,
                HARBOR_BADGE_GAP * 2,
            )
            return candidate_probe.colliderect(
                self._get_harbor_badge_visual_rect(other)
            )

        def is_compatible(candidate):
            return all(
                not badges_overlap(candidate, selected_rect)
                for selected_rect in selected.values()
            )

        def search(remaining):
            if not remaining:
                return dict(selected)

            viable = {
                index: [
                    candidate
                    for candidate in candidate_lists[index]
                    if is_compatible(candidate)
                ]
                for index in remaining
            }
            if any(not candidates for candidates in viable.values()):
                return None
            harbor_index = min(
                remaining,
                key=lambda index: (len(viable[index]), index),
            )
            next_remaining = tuple(
                index for index in remaining if index != harbor_index
            )
            for candidate in viable[harbor_index]:
                selected[harbor_index] = candidate
                if all(
                    any(is_compatible(other) for other in viable[index])
                    for index in next_remaining
                ):
                    solution = search(next_remaining)
                    if solution is not None:
                        return solution
                del selected[harbor_index]
            return None

        solution = search(tuple(range(len(candidate_lists))))
        if solution is None:
            return None
        return [solution[index] for index in range(len(candidate_lists))]

    @staticmethod
    def _draw_harbor_dock(screen, harbor):
        """Draw a compact pier outside the playable coastal edge.

        Roads may legally occupy the harbor's coastal edge, so the dock must not
        use that edge as a wooden crossbeam.  Two short piers start just offshore
        and meet a smaller outward crossbeam; the returned point is where the
        harbor sign connector should begin.
        """
        midpoint, axis, outward, length = GameBoard._get_harbor_edge_geometry(harbor)

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
        placements = self._layout_harbor_badges(font)

        # Draw every dock/connector first. Placement geometry keeps badges away
        # from pieces; this second pass additionally prevents one long fallback
        # connector from printing across a neighboring label's text.
        for harbor, _, badge_rect in placements:
            connector_start = self._draw_harbor_dock(screen, harbor)
            connector_path = self._get_harbor_connector_path(harbor, badge_rect)
            # Keep the dock's actual pixel endpoint in sync with the shared
            # geometry helper used during collision testing.
            connector_path = (connector_start, *connector_path[1:])
            shadow_path = tuple((point[0] + 2, point[1] + 3) for point in connector_path)
            pygame.draw.lines(
                screen,
                (56, 48, 39),
                False,
                shadow_path,
                4,
            )
            pygame.draw.lines(
                screen,
                (218, 188, 136),
                False,
                connector_path,
                2,
            )

        for harbor, text_surface, badge_rect in placements:
            shadow_rect = badge_rect.move(*HARBOR_BADGE_SHADOW_OFFSET)
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
