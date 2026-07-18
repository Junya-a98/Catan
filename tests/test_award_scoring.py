import pytest

from game.game import CatanGame
from game.match_result import build_match_result
from game.network_protocol import build_state_snapshot
from game.road import Road
from game.variant import VariantConfig


def _find_node_path(board, edge_count):
    adjacency = {}
    for node1, node2 in board.edges:
        adjacency.setdefault(node1, []).append(node2)
        adjacency.setdefault(node2, []).append(node1)

    def search(node, path):
        if len(path) - 1 == edge_count:
            return path
        for next_node in adjacency.get(node, ()):  # pragma: no branch - tiny board
            if next_node in path:
                continue
            result = search(next_node, path + [next_node])
            if result is not None:
                return result
        return None

    for start in board.nodes:
        result = search(start, [start])
        if result is not None:
            return result
    raise AssertionError("5本の単純な街道経路が見つかりません")


@pytest.mark.parametrize(
    "variant_config",
    [VariantConfig.standard(), VariantConfig.forecast_events()],
    ids=["standard", "forecast-events"],
)
def test_longest_road_and_largest_army_awards_score_and_serialize_in_all_modes(
    variant_config,
):
    game = CatanGame(
        board_seed=271828,
        variant_config=variant_config,
        headless=True,
    )
    try:
        owner = game.players[0]
        path = _find_node_path(game.board, 5)
        game.board.roads = [
            Road(owner, path[index], path[index + 1]) for index in range(5)
        ]
        owner.played_knights = 3

        game.update_longest_road()
        assert game.latest_event["title"] == f"{owner.name}が最長交易路を獲得"
        game.update_largest_army()
        assert game.latest_event["title"] == f"{owner.name}が最大騎士力を獲得"

        assert game.longest_road_owner is owner
        assert game.longest_road_length == 5
        assert game.largest_army_owner is owner
        assert game.largest_army_size == 3
        assert game.get_player_victory_points(owner) == 4

        snapshot = build_state_snapshot(game, viewer_player_index=0)
        assert snapshot["state"]["phase"]["longest_road_owner"] == 0
        assert snapshot["state"]["phase"]["longest_road_length"] == 5
        assert snapshot["state"]["phase"]["largest_army_owner"] == 0
        assert snapshot["state"]["phase"]["largest_army_size"] == 3

        game.phase = "finished"
        game.winner = owner
        result = build_match_result(game)
        standing = next(row for row in result["standings"] if row["seat"] == 1)
        assert standing["victory_points"] == 4
        assert standing["vp_breakdown"]["longest_road"] == {
            "awarded": True,
            "points": 2,
        }
        assert standing["vp_breakdown"]["largest_army"] == {
            "awarded": True,
            "points": 2,
        }
    finally:
        game.audio.stop()
