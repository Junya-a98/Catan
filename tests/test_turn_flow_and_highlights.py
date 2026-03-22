import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from game.building import Building
from game.game import CatanGame
from game.game_board import GameBoard
from game.player import Player
from game.resources import ResourceType
from game.road import Road


def find_node_path(board, edge_count):
    adjacency = {}
    for node1, node2 in board.edges:
        adjacency.setdefault(node1, []).append(node2)
        adjacency.setdefault(node2, []).append(node1)

    def dfs(node, path):
        if len(path) - 1 == edge_count:
            return path
        for next_node in adjacency.get(node, []):
            if next_node in path:
                continue
            result = dfs(next_node, path + [next_node])
            if result is not None:
                return result
        return None

    for start_node in board.nodes:
        result = dfs(start_node, [start_node])
        if result is not None:
            return result
    raise AssertionError(f"edge_count={edge_count} の単純パスが見つかりません")


def edge_key(edge):
    node1, node2 = edge
    return tuple(sorted((id(node1), id(node2))))


def test_get_player_longest_road_length_counts_simple_chain():
    board = GameBoard()
    player = Player("RoadRunner", (255, 0, 0))
    game = CatanGame.__new__(CatanGame)
    game.board = board

    path = find_node_path(board, 5)
    board.roads = [
        Road(player, path[index], path[index + 1])
        for index in range(5)
    ]

    assert game.get_player_longest_road_length(player) == 5


def test_get_player_longest_road_length_respects_blocking_enemy_building():
    board = GameBoard()
    player = Player("Builder", (255, 0, 0))
    opponent = Player("Blocker", (0, 0, 255))
    game = CatanGame.__new__(CatanGame)
    game.board = board

    path = find_node_path(board, 3)
    board.roads = [
        Road(player, path[index], path[index + 1])
        for index in range(3)
    ]
    path[2].building = Building(opponent)

    assert game.get_player_longest_road_length(player) == 2


def test_start_robber_phase_prompts_discards_for_large_hands():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        player1, player2 = game.turn_order[:2]
        player1.resources[ResourceType.WOOD] = 8
        player2.resources[ResourceType.SHEEP] = 6

        game.start_robber_phase()

        assert game.special_phase == "discard"
        assert game.discard_player == player1
        assert game.discard_remaining == 4
    finally:
        game.audio.stop()
        pygame.quit()


def test_relocate_robber_steals_from_single_target():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        current_player, victim = game.turn_order[:2]
        target_tile = next(tile for tile in game.board.tiles if tile != game.board.robber_tile)
        target_tile.corners[0].building = Building(victim)
        victim.resources[ResourceType.WOOD] = 1

        game.relocate_robber(target_tile)

        assert game.board.robber_tile == target_tile
        assert current_player.resources[ResourceType.WOOD] == 1
        assert victim.resources[ResourceType.WOOD] == 0
        assert game.special_phase is None
    finally:
        game.audio.stop()
        pygame.quit()


def test_road_mode_highlights_only_edges_connected_to_player_network():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        player = game.get_current_player()
        player.add_resource(ResourceType.WOOD)
        player.add_resource(ResourceType.BRICK)
        node = game.board.nodes[0]
        node.building = Building(player)
        game.dice_rolled = True
        game.action_mode = "road"

        highlights = game.get_board_highlight_data()["edge_highlights"]
        expected_edges = [edge for edge in game.board.edges if node in edge]

        assert {edge_key(edge) for edge in highlights} == {edge_key(edge) for edge in expected_edges}
    finally:
        game.audio.stop()
        pygame.quit()


def test_build_buttons_show_only_roll_before_dice():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()

        buttons = game.build_buttons()
        actions = [button.action for button in buttons]

        assert actions == ["roll_dice"]
        assert buttons[0].highlighted is True
    finally:
        game.audio.stop()
        pygame.quit()


def test_build_buttons_hide_unavailable_development_actions():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        game.dice_rolled = True

        actions = {button.action for button in game.build_buttons()}

        assert actions == {
            "mode_road",
            "mode_settlement",
            "mode_city",
            "buy_dev",
            "bank_trade",
            "end_turn",
        }
    finally:
        game.audio.stop()
        pygame.quit()


def test_build_buttons_highlight_currently_actionable_actions():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        player = game.get_current_player()
        node = game.board.nodes[0]
        node.building = Building(player)
        player.add_resource(ResourceType.WOOD)
        player.add_resource(ResourceType.BRICK)
        player.add_resource(ResourceType.SHEEP)
        player.add_resource(ResourceType.WHEAT, 2)
        player.add_resource(ResourceType.ORE, 3)
        game.dice_rolled = True

        buttons = {button.action: button for button in game.build_buttons()}

        assert buttons["mode_road"].highlighted is True
        assert buttons["mode_city"].highlighted is True
        assert buttons["buy_dev"].highlighted is True
        assert buttons["end_turn"].highlighted is False
    finally:
        game.audio.stop()
        pygame.quit()
