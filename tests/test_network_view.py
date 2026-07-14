import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from game.building import Building, BuildingType
from game.development_cards import DevelopmentCardType
from game.game import CatanGame
from game.network_protocol import build_state_snapshot
from game import network_view as network_view_module
from game.network_view import (
    MAX_BOARD_MANIFEST_BYTES,
    MAX_NODES,
    MAX_VIEW_LOG_MESSAGES,
    NetworkViewError,
    parse_network_view,
    parse_state_snapshot,
)
from game.resources import ResourceType
from game.road import Road


@pytest.fixture
def authority_game():
    game = CatanGame(board_seed=9090, ai_player_count=0, headless=True)
    game.configure_players(3, reset_logs=False)
    game.phase = "main"
    game.turn_order = [game.players[1], game.players[2], game.players[0]]
    game.current_player_index = 0
    game.dice_rolled = True
    game.action_mode = "road"
    game.special_phase = None

    game.players[0].resources[ResourceType.WOOD] = 2
    game.players[0].resources[ResourceType.ORE] = 1
    game.players[0].development_cards[DevelopmentCardType.KNIGHT] = 1
    game.players[0].new_development_cards[DevelopmentCardType.MONOPOLY] = 1
    game.players[0].victory_point_cards = 1

    game.board.nodes[0].building = Building(game.players[0], BuildingType.SETTLEMENT)
    game.board.nodes[8].building = Building(game.players[1], BuildingType.CITY)
    game.board.roads.append(Road(game.players[2], *game.board.edges[0]))
    game.longest_road_owner = game.players[0]
    game.longest_road_length = 5
    game.largest_army_owner = game.players[1]
    game.largest_army_size = 3
    game.log_messages = ["開始", "Player1が街道を建設"]
    return game


@pytest.fixture
def player_snapshot(authority_game):
    return build_state_snapshot(
        authority_game,
        viewer_player_index=0,
        revision=7,
    )


def test_parses_authority_snapshot_without_restoring_a_game(player_snapshot):
    view = parse_network_view(player_snapshot)

    assert view.revision == 7
    assert view.viewer_seat == 1
    assert view.phase == "main"
    assert view.current_actor_seat == 2
    assert view.current_actor.name == "Player2"
    assert view.action_mode == "road"
    assert view.dice_rolled is True
    assert [player.public_vp for player in view.players] == [3, 4, 0]
    assert view.players[0].resource_total == 3
    assert view.players[0].resources.to_dict() == {
        "WOOD": 2,
        "SHEEP": 0,
        "WHEAT": 0,
        "BRICK": 0,
        "ORE": 1,
    }
    assert view.players[0].development_card_total == 3
    assert view.players[0].development_cards["KNIGHT"] == 1
    assert view.players[0].new_development_cards["MONOPOLY"] == 1
    assert view.players[0].victory_point_cards == 1
    assert view.players[1].resources is None
    assert view.players[1].development_cards is None
    assert view.logs == ("開始", "Player1が街道を建設")


def test_parses_extended_public_match_context_as_immutable_dtos(authority_game):
    authority_game.victory_point_target = 12
    authority_game.bank.resources[ResourceType.WOOD] = 11
    authority_game.development_deck = authority_game.development_deck[:17]
    authority_game.initial_dice_phase = False
    authority_game.waiting_for_road = True
    authority_game.discard_remaining = 4
    authority_game.resource_selection_remaining = 1
    authority_game.free_roads_remaining = 2
    authority_game.bank_trade_give_resource = ResourceType.BRICK

    viewer = authority_game.players[0]
    viewer.piece_pattern = 3
    viewer.marker = "★"
    viewer.roads_remaining = 12
    viewer.settlements_remaining = 4
    viewer.cities_remaining = 3
    viewer.played_knights = 2
    authority_game.players[1].played_knights = 3

    authority_game.domestic_trade_partner = authority_game.players[1]
    authority_game.domestic_trade_editor = authority_game.players[2]
    authority_game.domestic_trade_give[ResourceType.WOOD] = 1
    authority_game.domestic_trade_receive[ResourceType.ORE] = 2
    authority_game.domestic_trade_edit_side = "receive"
    authority_game.domestic_trade_is_counter = True
    authority_game.domestic_trade_is_broadcast = True

    view = parse_network_view(
        build_state_snapshot(authority_game, viewer_player_index=0, revision=9)
    )

    assert view.victory_target == 12
    assert view.bank_resources.to_dict() == {
        "WOOD": 11,
        "SHEEP": 19,
        "WHEAT": 19,
        "BRICK": 19,
        "ORE": 19,
    }
    assert view.development_deck_remaining == 17
    assert view.initial_dice_phase is False
    assert view.waiting_for_road is True
    assert view.discard_remaining == 4
    assert view.resource_selection_remaining == 1
    assert view.free_roads_remaining == 2
    assert view.bank_trade_give_resource == "BRICK"
    assert view.longest_road_owner_seat == 1
    assert view.longest_road_length == 5
    assert view.largest_army_owner_seat == 2
    assert view.largest_army_size == 3

    parsed_viewer = view.players[0]
    assert parsed_viewer.piece_pattern == 3
    assert parsed_viewer.marker == "★"
    assert parsed_viewer.roads_remaining == 12
    assert parsed_viewer.settlements_remaining == 4
    assert parsed_viewer.cities_remaining == 3
    assert parsed_viewer.played_knights == 2
    assert view.players[1].played_knights == 3
    assert view.players[1].resources is None

    trade = view.domestic_trade
    assert trade.partner_seat == 2
    assert trade.editor_seat == 3
    assert trade.give["WOOD"] == 1
    assert trade.receive["ORE"] == 2
    assert trade.edit_side == "receive"
    assert trade.is_counter is True
    assert trade.is_broadcast is True

    with pytest.raises(TypeError):
        view.bank_resources["WOOD"] = 19
    with pytest.raises(TypeError):
        trade.give["WOOD"] = 2
    with pytest.raises(FrozenInstanceError):
        trade.edit_side = "give"


def test_spectator_model_has_totals_but_no_private_breakdown(authority_game):
    snapshot = build_state_snapshot(
        authority_game,
        viewer_player_index=None,
        revision=8,
    )

    view = parse_state_snapshot(snapshot)

    assert view.viewer_seat is None
    assert [player.resource_total for player in view.players] == [3, 0, 0]
    assert all(player.resources is None for player in view.players)
    assert all(player.development_cards is None for player in view.players)
    assert all(player.victory_point_cards is None for player in view.players)


def test_command_options_are_not_parsed_into_the_public_view(player_snapshot):
    snapshot = deepcopy(player_snapshot)
    snapshot["state"]["command_options"] = {
        "legal_target_ids": ["edge-0"],
        "server_only": {"secret": "do not copy"},
    }

    view = parse_network_view(snapshot)

    assert not hasattr(view, "command_options")
    assert "command_options" not in vars(view)


def test_board_dtos_have_stable_immutable_id_and_coordinate_lookups(player_snapshot):
    view = parse_network_view(player_snapshot)
    board = view.board

    assert len(board.tiles) == 19
    assert len(board.nodes) == 54
    assert len(board.edges) == 72
    assert len(board.harbors) == 9
    assert len(board.position_by_id) == 19 + 54 + 72 + 9
    first_node = board.nodes[0]
    first_edge = board.edges[0]
    assert board.node_by_id[first_node.target_id] is first_node
    assert board.position_for(first_node.target_id) == first_node.position
    start, end = board.edge_segment(first_edge.target_id)
    assert start == board.node_by_id[first_edge.node_ids[0]].position
    assert end == board.node_by_id[first_edge.node_ids[1]].position
    midpoint = board.position_for(first_edge.target_id)
    assert midpoint.x == pytest.approx((start.x + end.x) / 2)
    assert midpoint.y == pytest.approx((start.y + end.y) / 2)
    assert sum(tile.robber for tile in board.tiles) == 1
    assert any(node.building is not None for node in board.nodes)
    assert any(edge.road is not None for edge in board.edges)
    assert not hasattr(view, "legal_target_ids")
    assert not hasattr(board, "legal_target_ids")

    with pytest.raises(KeyError):
        board.position_for("node-9999")
    with pytest.raises(TypeError):
        board.node_by_id["node-9999"] = first_node
    with pytest.raises(TypeError):
        view.player_by_seat[9] = view.players[0]
    with pytest.raises(TypeError):
        view.players[0].resources["WOOD"] = 99
    with pytest.raises(FrozenInstanceError):
        view.revision = 99


@pytest.mark.parametrize(
    ("field", "leaked_value"),
    [
        ("resources", {"WOOD": 0}),
        ("development_cards", {}),
        ("new_development_cards", {}),
        ("victory_point_cards", 0),
    ],
)
def test_rejects_any_nonviewer_private_field_leak(
    player_snapshot,
    field,
    leaked_value,
):
    snapshot = deepcopy(player_snapshot)
    snapshot["state"]["players"][1][field] = leaked_value

    with pytest.raises(NetworkViewError, match="private"):
        parse_network_view(snapshot)


def test_rejects_missing_or_inconsistent_viewer_private_fields(player_snapshot):
    missing = deepcopy(player_snapshot)
    missing["state"]["players"][0]["resources"] = None
    with pytest.raises(NetworkViewError, match="viewer-private"):
        parse_network_view(missing)

    resource_mismatch = deepcopy(player_snapshot)
    resource_mismatch["state"]["players"][0]["resource_total"] += 1
    with pytest.raises(NetworkViewError, match="resource_total"):
        parse_network_view(resource_mismatch)

    card_mismatch = deepcopy(player_snapshot)
    card_mismatch["state"]["players"][0]["development_card_total"] += 1
    with pytest.raises(NetworkViewError, match="development_card_total"):
        parse_network_view(card_mismatch)

    opponent_schema = deepcopy(player_snapshot)
    del opponent_schema["state"]["players"][1]["resources"]
    with pytest.raises(NetworkViewError, match="visibility"):
        parse_network_view(opponent_schema)


def test_logs_are_validated_and_reduced_to_a_bounded_tail(player_snapshot):
    snapshot = deepcopy(player_snapshot)
    snapshot["state"]["history"]["log_messages"] = [
        f"log-{index}" for index in range(MAX_VIEW_LOG_MESSAGES + 15)
    ]

    view = parse_network_view(snapshot)

    assert len(view.logs) == MAX_VIEW_LOG_MESSAGES
    assert view.logs[0] == "log-15"
    assert view.logs[-1] == f"log-{MAX_VIEW_LOG_MESSAGES + 14}"

    invalid = deepcopy(player_snapshot)
    invalid["state"]["history"]["log_messages"] = ["safe", 42]
    with pytest.raises(NetworkViewError, match="log_messages"):
        parse_network_view(invalid)


def _duplicate_node_id(snapshot):
    nodes = snapshot["board_manifest"]["nodes"]
    nodes[1]["id"] = nodes[0]["id"]


def _unknown_edge_reference(snapshot):
    snapshot["board_manifest"]["nodes"][0]["edge_ids"][0] = "edge-9999"


def _nonfinite_coordinate(snapshot):
    snapshot["board_manifest"]["nodes"][0]["position"]["x"] = float("nan")


def _bad_owner(snapshot):
    building_node = next(
        node for node in snapshot["board_manifest"]["nodes"] if node["building"]
    )
    building_node["building"]["owner_player_index"] = 3


def _second_robber(snapshot):
    tile = next(
        tile for tile in snapshot["board_manifest"]["tiles"] if not tile["robber"]
    )
    tile["robber"] = True


def _inverted_edge_perimeter(snapshot):
    edge = snapshot["board_manifest"]["edges"][0]
    edge["perimeter"] = not edge["perimeter"]


def _bad_harbor_reference(snapshot):
    snapshot["board_manifest"]["harbors"][0]["edge_id"] = "edge-9999"


def _duplicate_node_coordinate(snapshot):
    nodes = snapshot["board_manifest"]["nodes"]
    nodes[1]["position"] = dict(nodes[0]["position"])


def _node_lists_unrelated_edge(snapshot):
    manifest = snapshot["board_manifest"]
    node = manifest["nodes"][0]
    unrelated = next(
        edge for edge in manifest["edges"] if node["id"] not in edge["node_ids"]
    )
    node["edge_ids"][0] = unrelated["id"]


def _duplicate_edge_endpoints(snapshot):
    edges = snapshot["board_manifest"]["edges"]
    edges[1]["node_ids"] = list(edges[0]["node_ids"])


@pytest.mark.parametrize(
    "mutator",
    [
        _duplicate_node_id,
        _unknown_edge_reference,
        _nonfinite_coordinate,
        _bad_owner,
        _second_robber,
        _inverted_edge_perimeter,
        _bad_harbor_reference,
        _duplicate_node_coordinate,
        _node_lists_unrelated_edge,
        _duplicate_edge_endpoints,
    ],
)
def test_rejects_bad_ids_references_coordinates_owners_and_robber(
    player_snapshot,
    mutator,
):
    snapshot = deepcopy(player_snapshot)
    mutator(snapshot)

    with pytest.raises(NetworkViewError):
        parse_network_view(snapshot)


def test_rejects_manifest_mode_seed_bounds_collection_and_byte_limit(player_snapshot):
    mode = deepcopy(player_snapshot)
    mode["board_manifest"]["mode"] = "fully_random"
    with pytest.raises(NetworkViewError, match="mode/seed"):
        parse_network_view(mode)

    seed = deepcopy(player_snapshot)
    seed["board_manifest"]["seed"] += 1
    with pytest.raises(NetworkViewError, match="mode/seed"):
        parse_network_view(seed)

    bounds = deepcopy(player_snapshot)
    bounds["board_manifest"]["coordinate_space"]["bounds"]["min_x"] += 1
    with pytest.raises(NetworkViewError, match="bounds"):
        parse_network_view(bounds)

    too_many = deepcopy(player_snapshot)
    original = too_many["board_manifest"]["nodes"][0]
    too_many["board_manifest"]["nodes"] = [
        {**deepcopy(original), "id": f"node-{index}"} for index in range(MAX_NODES + 1)
    ]
    with pytest.raises(NetworkViewError, match="nodes"):
        parse_network_view(too_many)

    oversized = deepcopy(player_snapshot)
    oversized["board_manifest"]["padding"] = "x" * MAX_BOARD_MANIFEST_BYTES
    with pytest.raises(NetworkViewError, match="size limit"):
        parse_network_view(oversized)


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("rules", "victory_point_target"), True),
        (("bank", "WOOD"), 20),
        (("development_deck", "remaining"), 26),
        (("initial", "dice_phase"), 1),
        (("initial", "waiting_for_road"), None),
        (("special", "discard_remaining"), -1),
        (("special", "resource_selection_remaining"), 3),
        (("special", "free_roads_remaining"), 3),
        (("special", "bank_trade_give_resource"), "DESERT"),
        (("phase", "longest_road_owner"), 3),
        (("phase", "longest_road_length"), 16),
        (("phase", "largest_army_owner"), True),
        (("phase", "largest_army_size"), 15),
        (("players", 0, "piece_pattern"), 4),
        (("players", 0, "marker"), "\n"),
        (("players", 0, "roads_remaining"), 16),
        (("players", 0, "settlements_remaining"), 6),
        (("players", 0, "cities_remaining"), 5),
        (("players", 0, "played_knights"), 15),
        (("domestic_trade", "partner"), 3),
        (("domestic_trade", "editor"), True),
        (("domestic_trade", "give", "WOOD"), 20),
        (("domestic_trade", "edit_side"), "other"),
        (("domestic_trade", "is_counter"), 1),
        (("domestic_trade", "is_broadcast"), None),
    ],
    ids=lambda value: ".".join(map(str, value)) if isinstance(value, tuple) else None,
)
def test_rejects_invalid_extended_public_context(
    player_snapshot,
    path,
    invalid_value,
):
    snapshot = deepcopy(player_snapshot)
    target = snapshot["state"]
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = invalid_value

    with pytest.raises(NetworkViewError):
        parse_network_view(snapshot)


def test_rejects_domestic_trade_with_same_resource_on_both_sides(player_snapshot):
    snapshot = deepcopy(player_snapshot)
    snapshot["state"]["domestic_trade"]["give"]["WOOD"] = 1
    snapshot["state"]["domestic_trade"]["receive"]["WOOD"] = 1

    with pytest.raises(NetworkViewError, match="give and receive"):
        parse_network_view(snapshot)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda snapshot: snapshot.update(type="other"),
        lambda snapshot: snapshot.update(protocol_version=2),
        lambda snapshot: snapshot.update(revision=True),
        lambda snapshot: snapshot.update(viewer_player_index=3),
        lambda snapshot: snapshot["state"].update(format="other"),
        lambda snapshot: snapshot["state"]["phase"].update(turn_order=[0, 0, 2]),
        lambda snapshot: snapshot["state"]["phase"].update(current_player_index=True),
    ],
)
def test_rejects_invalid_envelope_player_and_phase_fields(player_snapshot, mutation):
    snapshot = deepcopy(player_snapshot)
    mutation(snapshot)

    with pytest.raises(NetworkViewError):
        parse_network_view(snapshot)


def test_requires_manifest_at_envelope_root_and_discards_unlisted_state(
    player_snapshot,
):
    nested = deepcopy(player_snapshot)
    nested["state"]["board_manifest"] = nested.pop("board_manifest")
    with pytest.raises(NetworkViewError, match="board_manifest"):
        parse_network_view(nested)

    snapshot = deepcopy(player_snapshot)
    snapshot["state"]["private_server_extension"] = {"secret": "must not enter DTO"}
    snapshot["legal_target_ids"] = ["edge-0"]
    view = parse_network_view(snapshot)
    assert not hasattr(view, "private_server_extension")
    assert not hasattr(view, "legal_target_ids")

    missing_history = deepcopy(player_snapshot)
    del missing_history["state"]["history"]
    with pytest.raises(NetworkViewError, match="history"):
        parse_network_view(missing_history)


def test_network_view_module_has_no_pygame_or_game_runtime_imports():
    source = Path(network_view_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert not any(name == "pygame" or name.startswith("game.") for name in imported)
    assert "restore_game(" not in source
    assert "CatanGame(" not in source
    assert callable(parse_network_view)
