import json
import copy

from game.building import Building
from game.game import CatanGame
from game.network_protocol import build_board_manifest, build_state_snapshot
from game.network_view import parse_network_view
from game.persistence import restore_game, serialize_game
from game.road import Road
from game.variant import VariantConfig


def _frontier_game(seed=86712347):
    return CatanGame(
        board_seed=seed,
        variant_config=VariantConfig.frontier(),
        ai_player_count=0,
        headless=True,
    )


def test_frontier_snapshot_masks_tiles_harbors_and_authority_seed():
    game = _frontier_game()
    snapshot = build_state_snapshot(game, viewer_player_index=0)
    manifest = snapshot["board_manifest"]

    assert manifest["seed"] == 0
    assert snapshot["state"]["board"]["seed"] == 0
    assert sum(tile["revealed"] for tile in manifest["tiles"]) in (7, 8)
    hidden = [tile for tile in manifest["tiles"] if not tile["revealed"]]
    assert hidden
    assert all(
        tile["resource"] == "UNKNOWN"
        and tile["number"] is None
        and tile["robber"] is False
        for tile in hidden
    )
    assert len(manifest["harbors"]) < len(game.board.harbors)
    hidden_resource = next(
        tile.resource_type.name
        for tile in game.board.tiles
        if not game.is_frontier_tile_revealed(tile)
    )
    hidden_axial = next(
        tile.axial
        for tile in game.board.tiles
        if not game.is_frontier_tile_revealed(tile)
        and tile.resource_type.name == hidden_resource
    )
    manifest_tile = next(
        tile
        for tile in manifest["tiles"]
        if (tile["axial"]["q"], tile["axial"]["r"]) == hidden_axial
    )
    assert hidden_resource not in json.dumps(manifest_tile)
    view = parse_network_view(snapshot)
    assert view.board.seed == 0
    assert any(not tile.revealed and tile.resource == "UNKNOWN" for tile in view.board.tiles)


def test_road_discovery_reveals_real_tile_and_is_idempotent():
    game = _frontier_game()
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

    assert revealed
    assert game.variant_state.public["discovery_count"] == len(revealed)
    assert game.reveal_frontier_from_road(road) == ()
    manifest = build_board_manifest(game)
    public_axials = {
        (tile["axial"]["q"], tile["axial"]["r"]): tile
        for tile in manifest["tiles"]
    }
    for tile in revealed:
        assert public_axials[tile.axial]["resource"] == tile.resource_type.name


def test_hidden_tiles_do_not_produce_and_hidden_harbors_do_not_trade():
    game = _frontier_game()
    player = game.players[0]
    tile = next(
        tile
        for tile in game.board.tiles
        if not game.is_frontier_tile_revealed(tile)
        and tile.number is not None
        and tile is not game.board.robber_tile
    )
    tile.corners[0].building = Building(player)

    game.distribute_resources(tile.number)
    assert player.resources[tile.resource_type] == 0

    hidden_harbor = next(
        harbor
        for harbor in game.board.harbors
        if not game.is_frontier_harbor_revealed(harbor)
    )
    hidden_harbor.node1.building = Building(player)
    assert set(game.get_trade_rates(player).values()) == {4}

    adjacent = game.board.get_edge_adjacent_tiles(
        (hidden_harbor.node1, hidden_harbor.node2)
    )
    game.variant_state, _ = game.variant_state.reveal_frontier_tiles(
        [candidate.axial for candidate in adjacent]
    )
    rates = game.get_trade_rates(player)
    if hidden_harbor.resource_type is None:
        assert set(rates.values()) == {3}
    else:
        assert rates[hidden_harbor.resource_type] == 2


def test_frontier_road_candidates_stay_on_the_public_exploration_boundary():
    game = _frontier_game()
    hidden_edge = next(
        edge
        for edge in game.board.edges
        if not game.frontier_edge_is_reachable(edge)
    )

    assert game.get_frontier_edge_discovery_count(hidden_edge) > 0
    assert game.can_place_road(game.players[0], *hidden_edge)[0] is False


def test_frontier_full_save_restores_public_progress_and_secret_board_seed():
    game = _frontier_game()
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
    restored = CatanGame(board_seed=1, headless=True)

    restore_game(restored, copy.deepcopy(saved), runtime_side_effects=False)

    assert restored.variant_config == VariantConfig.frontier()
    assert restored.variant_state == game.variant_state
    assert restored.board_seed == game.board_seed
    assert serialize_game(restored) == saved
