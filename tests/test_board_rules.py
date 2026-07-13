from game.board_rules import BoardHighlightState, BoardRules
from game.game_board import GameBoard
from game.player import Player
from game.road import Road


def edge_key(edge):
    node1, node2 = edge
    return tuple(sorted((id(node1), id(node2))))


def test_board_rules_requires_connected_road_for_main_settlement():
    board = GameBoard(seed=3)
    rules = BoardRules(board)
    player = Player("Builder", (255, 0, 0))
    node = board.nodes[0]

    can_build, message = rules.can_place_main_settlement(player, node)
    assert can_build is False
    assert "街道" in message

    adjacent = rules.get_adjacent_nodes(node)[0]
    board.roads.append(Road(player, node, adjacent))

    can_build, message = rules.can_place_main_settlement(player, node)
    assert can_build is True
    assert message == ""


def test_adjacent_nodes_have_a_stable_coordinate_order():
    board = GameBoard(mode="fully_random", seed=33)
    rules = BoardRules(board)
    node = max(board.nodes, key=lambda candidate: len(candidate.tiles))

    coordinates = [
        (candidate.y, candidate.x)
        for candidate in rules.get_adjacent_nodes(node)
    ]

    assert coordinates == sorted(coordinates)
    assert coordinates == [
        (candidate.y, candidate.x)
        for candidate in rules.get_adjacent_nodes(node)
    ]


def test_board_rules_returns_initial_road_highlights_from_last_settlement():
    board = GameBoard(seed=4)
    rules = BoardRules(board)
    player = Player("Starter", (255, 0, 0))
    last_settlement_node = board.nodes[0]

    highlights = rules.get_board_highlights(
        BoardHighlightState(
            phase="initial",
            initial_dice_phase=False,
            waiting_for_road=True,
            special_phase=None,
            action_mode=None,
            winner_present=False,
            has_active_dice_animation=False,
            initial_placement_player=player,
            last_settlement_node=last_settlement_node,
        )
    )

    expected_edges = [
        (last_settlement_node, adjacent_node)
        for adjacent_node in rules.get_adjacent_nodes(last_settlement_node)
    ]

    assert {edge_key(edge) for edge in highlights["edge_highlights"]} == {
        edge_key(edge) for edge in expected_edges
    }
