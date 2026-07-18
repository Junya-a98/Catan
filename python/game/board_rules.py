import math
from dataclasses import dataclass
from typing import Optional, Sequence

from game.building import BuildingType
from game.resources import BUILD_COSTS


@dataclass(frozen=True)
class BoardHighlightState:
    phase: str
    initial_dice_phase: bool
    waiting_for_road: bool
    special_phase: Optional[str]
    action_mode: Optional[str]
    winner_present: bool
    has_active_dice_animation: bool
    current_player: object = None
    initial_placement_player: object = None
    robber_tile_candidates: Sequence = ()
    robber_target_players: Sequence = ()
    last_settlement_node: object = None


class BoardRules:
    def __init__(self, board, *, road_is_usable=None):
        self.board = board
        self.road_is_usable = road_is_usable or (lambda _road: True)

    def set_board(self, board):
        self.board = board

    def set_road_is_usable(self, predicate):
        self.road_is_usable = predicate or (lambda _road: True)

    def find_closest_node(self, mx, my, candidates=None):
        nodes = candidates if candidates is not None else self.board.nodes
        closest_node = None
        min_dist = float("inf")
        for node in nodes:
            dist = math.hypot(node.x - mx, node.y - my)
            if dist < min_dist:
                min_dist = dist
                closest_node = node
        return closest_node, min_dist

    def find_closest_edge(self, mx, my, candidates=None):
        edges = candidates if candidates is not None else self.board.edges
        closest_edge = None
        min_dist = float("inf")
        for node1, node2 in edges:
            midpoint_x = (node1.x + node2.x) / 2
            midpoint_y = (node1.y + node2.y) / 2
            dist = math.hypot(midpoint_x - mx, midpoint_y - my)
            if dist < min_dist:
                min_dist = dist
                closest_edge = (node1, node2)
        return closest_edge, min_dist

    def find_closest_tile(self, mx, my, candidates=None):
        tiles = candidates if candidates is not None else self.board.tiles
        closest_tile = None
        min_dist = float("inf")
        for tile in tiles:
            dist = math.hypot(tile.x - mx, tile.y - my)
            if dist < min_dist:
                min_dist = dist
                closest_tile = tile
        return closest_tile, min_dist

    def get_adjacent_nodes(self, node):
        adjacent = set()
        for tile in node.tiles:
            if node not in tile.corners:
                continue
            node_index = tile.corners.index(node)
            adjacent.add(tile.corners[(node_index - 1) % len(tile.corners)])
            adjacent.add(tile.corners[(node_index + 1) % len(tile.corners)])
        # Sets are useful for de-duplication, but their iteration order is not a
        # gameplay rule.  A coordinate order keeps AI tie-breaks reproducible
        # across processes and Python hash seeds.
        return sorted(adjacent, key=lambda candidate: (candidate.y, candidate.x))

    def road_exists_between(self, node1, node2):
        return any({road.node1, road.node2} == {node1, node2} for road in self.board.roads)

    def player_has_road_touching_node(self, player, node):
        return any(
            road.owner == player
            and road.touches(node)
            and self.road_is_usable(road)
            for road in self.board.roads
        )

    def is_spacing_rule_satisfied(self, node):
        if node.building is not None:
            return False
        return all(adjacent_node.building is None for adjacent_node in self.get_adjacent_nodes(node))

    def can_place_initial_settlement(self, node):
        if not self.is_spacing_rule_satisfied(node):
            if node.building is not None:
                return False, "そのノードには既に建物が存在します。"
            return False, "間隔ルールにより、隣接する交差点の近くには建てられません。"
        return True, ""

    def can_place_main_settlement(self, player, node):
        if not self.is_spacing_rule_satisfied(node):
            if node.building is not None:
                return False, "そのノードには既に建物が存在します。"
            return False, "間隔ルールにより、隣接する交差点の近くには建てられません。"
        if not self.player_has_road_touching_node(player, node):
            return False, "開拓地は自分の街道が接続している交差点にのみ建設できます。"
        return True, ""

    def can_use_node_for_road_connection(self, player, node):
        if node.building is not None:
            return node.building.owner == player
        return self.player_has_road_touching_node(player, node)

    def can_place_road(self, player, node1, node2):
        if not self.board.has_edge(node1, node2):
            return False, "その場所には街道を敷設できません。"
        if self.road_exists_between(node1, node2):
            return False, "その辺には既に街道があります。"
        if self.can_use_node_for_road_connection(player, node1):
            return True, ""
        if self.can_use_node_for_road_connection(player, node2):
            return True, ""
        return False, "街道は自分の開拓地・都市、または既存の街道につなげて建設してください。"

    def can_upgrade_to_city(self, player, node):
        if node.building is None:
            return False, "そこには建物がありません。"
        if node.building.owner != player:
            return False, "自分の開拓地のみ都市にアップグレードできます。"
        if node.building.building_type != BuildingType.SETTLEMENT:
            return False, "その建物はすでに都市です。"
        return True, ""

    def get_buildable_road_edges(self, player, require_affordability=True):
        if player is None or player.roads_remaining <= 0:
            return []
        if require_affordability and not player.can_afford(BUILD_COSTS["road"]):
            return []
        return [
            (node1, node2)
            for node1, node2 in self.board.edges
            if self.can_place_road(player, node1, node2)[0]
        ]

    def get_buildable_settlement_nodes(self, player):
        if player is None or player.settlements_remaining <= 0:
            return []
        if not player.can_afford(BUILD_COSTS["settlement"]):
            return []
        return [
            node
            for node in self.board.nodes
            if self.can_place_main_settlement(player, node)[0]
        ]

    def get_buildable_city_nodes(self, player):
        if player is None or player.cities_remaining <= 0:
            return []
        if not player.can_afford(BUILD_COSTS["city"]):
            return []
        return [
            node
            for node in self.board.nodes
            if self.can_upgrade_to_city(player, node)[0]
        ]

    def get_initial_settlement_candidates(self):
        return [
            node
            for node in self.board.nodes
            if self.can_place_initial_settlement(node)[0]
        ]

    def get_initial_road_candidates(self, player, last_settlement_node):
        if player is None or last_settlement_node is None or player.roads_remaining <= 0:
            return []
        return [
            (last_settlement_node, adjacent_node)
            for adjacent_node in self.get_adjacent_nodes(last_settlement_node)
            if not self.road_exists_between(last_settlement_node, adjacent_node)
        ]

    def get_steal_target_nodes(self, robber_target_players):
        if self.board.robber_tile is None:
            return []
        target_players = set(robber_target_players)
        return [
            node
            for node in self.board.robber_tile.corners
            if node.building is not None and node.building.owner in target_players
        ]

    def get_board_highlights(self, state: BoardHighlightState):
        highlights = {
            "settlement_nodes": [],
            "city_nodes": [],
            "target_nodes": [],
            "edge_highlights": [],
            "tile_highlights": [],
        }

        if state.has_active_dice_animation:
            return highlights

        if state.phase == "initial":
            if state.initial_dice_phase or state.initial_placement_player is None:
                return highlights
            if state.waiting_for_road:
                highlights["edge_highlights"] = self.get_initial_road_candidates(
                    state.initial_placement_player,
                    state.last_settlement_node,
                )
            else:
                highlights["settlement_nodes"] = self.get_initial_settlement_candidates()
            return highlights

        if state.phase != "main" or state.winner_present:
            return highlights

        current_player = state.current_player
        if state.special_phase == "move_robber":
            highlights["tile_highlights"] = list(state.robber_tile_candidates)
        elif state.special_phase == "steal":
            highlights["target_nodes"] = self.get_steal_target_nodes(state.robber_target_players)
        elif state.special_phase == "road_building":
            highlights["edge_highlights"] = self.get_buildable_road_edges(current_player, require_affordability=False)
        elif state.action_mode == "road":
            highlights["edge_highlights"] = self.get_buildable_road_edges(current_player)
        elif state.action_mode == "settlement":
            highlights["settlement_nodes"] = self.get_buildable_settlement_nodes(current_player)
        elif state.action_mode == "city":
            highlights["city_nodes"] = self.get_buildable_city_nodes(current_player)

        return highlights
