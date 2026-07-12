import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from game.building import Building
from game.development_cards import DevelopmentCardType
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
            "domestic_trade",
            "end_turn",
        }
    finally:
        game.audio.stop()
        pygame.quit()


def test_build_buttons_enable_actions_and_emphasize_turn_end():
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

        assert buttons["mode_road"].enabled is True
        assert buttons["mode_city"].enabled is True
        assert buttons["buy_dev"].enabled is True
        assert buttons["mode_road"].highlighted is False
        assert buttons["mode_city"].highlighted is False
        assert buttons["buy_dev"].highlighted is False
        assert buttons["end_turn"].highlighted is True
    finally:
        game.audio.stop()
        pygame.quit()


def test_road_building_card_is_not_consumed_without_pieces_or_legal_placement():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        player = game.get_current_player()
        player.development_cards[DevelopmentCardType.ROAD_BUILDING] = 1

        player.roads_remaining = 0
        game.use_road_building_card()

        assert player.development_cards[DevelopmentCardType.ROAD_BUILDING] == 1
        assert game.development_card_used_this_turn is False
        assert game.special_phase is None

        player.roads_remaining = 15
        game.use_road_building_card()

        assert player.development_cards[DevelopmentCardType.ROAD_BUILDING] == 1
        assert game.development_card_used_this_turn is False
        assert game.special_phase is None
    finally:
        game.audio.stop()
        pygame.quit()


def test_road_building_must_place_both_roads_when_legal():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        player = game.get_current_player()
        game.board.nodes[0].building = Building(player)
        player.development_cards[DevelopmentCardType.ROAD_BUILDING] = 1

        game.use_road_building_card()

        assert game.special_phase == "road_building"
        assert game.build_buttons() == []
        assert game.complete_road_building_phase() is False
        assert game.special_phase == "road_building"
        assert game.free_roads_remaining == 2
        assert "残りの街道" in game.get_active_feedback().text

        first_edge = game.get_buildable_road_edges(
            player,
            require_affordability=False,
        )[0]
        game.handle_free_road_build_click(
            (
                (first_edge[0].x + first_edge[1].x) / 2,
                (first_edge[0].y + first_edge[1].y) / 2,
            )
        )
        assert game.special_phase == "road_building"
        assert game.free_roads_remaining == 1

        second_edge = game.get_buildable_road_edges(
            player,
            require_affordability=False,
        )[0]
        game.handle_free_road_build_click(
            (
                (second_edge[0].x + second_edge[1].x) / 2,
                (second_edge[0].y + second_edge[1].y) / 2,
            )
        )

        assert game.special_phase is None
        assert game.free_roads_remaining == 0
        assert player.roads_remaining == 13
        assert len([road for road in game.board.roads if road.owner is player]) == 2
    finally:
        game.audio.stop()
        pygame.quit()


def test_road_building_places_one_road_when_only_one_piece_remains():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        player = game.get_current_player()
        game.board.nodes[0].building = Building(player)
        player.roads_remaining = 1
        player.development_cards[DevelopmentCardType.ROAD_BUILDING] = 1

        game.use_road_building_card()

        assert game.special_phase == "road_building"
        assert game.free_roads_remaining == 1
        edge = game.get_buildable_road_edges(
            player,
            require_affordability=False,
        )[0]
        game.handle_free_road_build_click(
            (
                (edge[0].x + edge[1].x) / 2,
                (edge[0].y + edge[1].y) / 2,
            )
        )

        assert game.special_phase is None
        assert game.free_roads_remaining == 0
        assert player.roads_remaining == 0
    finally:
        game.audio.stop()
        pygame.quit()


def test_knight_before_roll_blocks_dice_until_robber_move_is_completed():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        player = game.get_current_player()
        player.development_cards[DevelopmentCardType.KNIGHT] = 1
        target_tile = next(tile for tile in game.board.tiles if tile != game.board.robber_tile)

        game.use_knight_card()

        assert game.dice_rolled is False
        assert game.special_phase == "move_robber"

        game.handle_roll_dice()

        assert game.has_active_dice_animation() is False
        assert game.pending_dice_context is None
        assert "特殊処理" in game.get_active_feedback().text

        game.handle_main_phase_click((target_tile.x, target_tile.y))

        assert game.board.robber_tile == target_tile
        assert game.special_phase is None
        assert game.dice_rolled is False
    finally:
        game.audio.stop()
        pygame.quit()


def test_build_buttons_show_playable_development_cards_before_dice():
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame()
    try:
        game.start_main_phase()
        player = game.get_current_player()
        game.board.nodes[0].building = Building(player)
        for card_type in (
            DevelopmentCardType.KNIGHT,
            DevelopmentCardType.ROAD_BUILDING,
            DevelopmentCardType.YEAR_OF_PLENTY,
            DevelopmentCardType.MONOPOLY,
        ):
            player.development_cards[card_type] = 1

        buttons = {button.action: button for button in game.build_buttons()}

        assert set(buttons) == {
            "roll_dice",
            "use_knight",
            "use_road_building",
            "use_year_of_plenty",
            "use_monopoly",
        }
        assert all(button.enabled for button in buttons.values())
        assert buttons["roll_dice"].highlighted is True
        assert all(
            not button.highlighted
            for action, button in buttons.items()
            if action != "roll_dice"
        )
    finally:
        game.audio.stop()
        pygame.quit()
