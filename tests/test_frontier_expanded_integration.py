import copy

from game.game import CatanGame
from game.network_protocol import build_board_manifest, build_state_snapshot
from game.persistence import restore_game, serialize_game
from game.road import Road
from game.variant import VariantConfig


def _expanded_game(seed=71_991):
    return CatanGame(
        board_seed=seed,
        variant_config=VariantConfig.frontier_expanded(),
        ai_player_count=0,
        headless=True,
    )


def _manifest_identity(manifest):
    return {
        "tiles": [
            (tile["id"], tile["axial"]["q"], tile["axial"]["r"])
            for tile in manifest["tiles"]
        ],
        "nodes": [
            (node["id"], node["position"]["x"], node["position"]["y"])
            for node in manifest["nodes"]
        ],
        "edges": [
            (edge["id"], tuple(edge["node_ids"]))
            for edge in manifest["edges"]
        ],
    }


def test_expanded_frontier_masks_all_outer_content_and_seed():
    game = _expanded_game()
    try:
        snapshot = build_state_snapshot(game, viewer_player_index=0)
        manifest = snapshot["board_manifest"]

        assert game.board.topology_id == "outer_ring_37_v1"
        assert len(manifest["tiles"]) == 37
        assert len(manifest["nodes"]) == 96
        assert len(manifest["edges"]) == 132
        assert manifest["seed"] == 0
        assert snapshot["state"]["board"]["seed"] == 0
        assert sum(tile["revealed"] for tile in manifest["tiles"]) == 7
        hidden = [tile for tile in manifest["tiles"] if not tile["revealed"]]
        assert len(hidden) == 30
        assert all(
            tile["resource"] == "UNKNOWN"
            and tile["number"] is None
            and tile["robber"] is False
            for tile in hidden
        )
        assert manifest["harbors"] == []
        assert snapshot["state"]["variant_state"]["public"]["catalog"] == (
            "outer_ring_37_v1"
        )
        assert "private" not in snapshot["state"]["variant_state"]
    finally:
        game.audio.stop()


def test_expanded_reveal_keeps_every_board_target_id_stable():
    game = _expanded_game()
    try:
        before = build_board_manifest(game)
        edge = next(
            edge
            for edge in game.board.edges
            if game.frontier_edge_is_reachable(edge)
            and game.get_frontier_edge_discovery_count(edge) > 0
        )
        road = Road(game.players[0], *edge)
        game.players[0].roads_remaining -= 1
        game.board.roads.append(road)

        revealed = game.reveal_frontier_from_road(road)
        after = build_board_manifest(game)

        assert revealed
        assert _manifest_identity(after) == _manifest_identity(before)
        assert sum(tile["revealed"] for tile in after["tiles"]) == 7 + len(revealed)
        revealed_axials = {tile.axial for tile in revealed}
        assert all(
            tile["resource"] != "UNKNOWN"
            for tile in after["tiles"]
            if (tile["axial"]["q"], tile["axial"]["r"]) in revealed_axials
        )
    finally:
        game.audio.stop()


def test_expanded_frontier_save_restore_rebuilds_the_same_topology():
    game = _expanded_game(seed=88_172)
    restored = CatanGame(board_seed=1, headless=True)
    try:
        edge = next(
            edge
            for edge in game.board.edges
            if game.frontier_edge_is_reachable(edge)
            and game.get_frontier_edge_discovery_count(edge) > 0
        )
        road = Road(game.players[0], *edge)
        game.players[0].roads_remaining -= 1
        game.board.roads.append(road)
        game.reveal_frontier_from_road(road)
        saved = serialize_game(game)

        restore_game(restored, copy.deepcopy(saved), runtime_side_effects=False)

        assert restored.variant_config == VariantConfig.frontier_expanded()
        assert restored.board.topology_id == "outer_ring_37_v1"
        assert (len(restored.board.tiles), len(restored.board.nodes)) == (37, 96)
        assert len(restored.board.edges) == 132
        assert restored.variant_state == game.variant_state
        assert _manifest_identity(build_board_manifest(restored)) == (
            _manifest_identity(build_board_manifest(game))
        )
        assert serialize_game(restored) == saved
    finally:
        game.audio.stop()
        restored.audio.stop()


def test_legacy_frontier_still_rebuilds_the_original_board():
    game = CatanGame(
        board_seed=71_991,
        variant_config=VariantConfig.frontier(),
        headless=True,
    )
    try:
        assert game.board.topology_id == "standard_19_v1"
        assert (len(game.board.tiles), len(game.board.nodes), len(game.board.edges)) == (
            19,
            54,
            72,
        )
        assert "catalog" not in game.variant_state.public
    finally:
        game.audio.stop()
