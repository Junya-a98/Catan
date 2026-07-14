import json
import random

import pytest

from game.building import Building
from game.development_cards import DevelopmentCardType
from game.game import CatanGame
from game.network_actions import (
    MAX_GAME_COMMAND_OPTIONS,
    NetworkActionError,
    advance_network_handoffs,
    apply_game_command,
    build_game_command_options,
    resolve_active_actor_index,
)
from game.network_protocol import build_board_reference_index
from game.persistence import serialize_game
from game.resources import ResourceType
from game.road import Road


@pytest.fixture
def game():
    instance = CatanGame(board_seed=90210, ai_player_count=0, headless=True)
    instance.configure_players(3, reset_logs=False)
    return instance


def _target_id(game, value, kind):
    references = build_board_reference_index(game)[kind]
    if kind == "edge":
        value_key = {id(value[0]), id(value[1])}
        return next(
            target_id
            for target_id, edge in references.items()
            if {id(edge[0]), id(edge[1])} == value_key
        )
    return next(
        target_id
        for target_id, target in references.items()
        if target is value
    )


def _give_from_bank(game, player, resource, amount=1):
    assert game.bank.withdraw(resource, amount)
    player.add_resource(resource, amount)


def _command_args(options, command):
    return [option["args"] for option in options if option["command"] == command]


def _command_names(options):
    return {option["command"] for option in options}


def test_session_seat_is_the_only_actor_authority(game):
    with pytest.raises(NetworkActionError) as spectator_error:
        apply_game_command(game, None, "roll_dice")
    assert spectator_error.value.code == "spectator_forbidden"

    with pytest.raises(NetworkActionError) as wrong_seat_error:
        apply_game_command(game, 1, "roll_dice")
    assert wrong_seat_error.value.code == "not_active_player"

    # A client cannot smuggle a different seat into an otherwise empty command.
    with pytest.raises(NetworkActionError) as spoof_error:
        apply_game_command(game, 0, "roll_dice", {"seat_index": 1})
    assert spoof_error.value.code == "invalid_args"
    assert game.initial_dice_histories[game.players[0].name] == []


def test_initial_dice_and_placement_resolve_the_current_actor_and_stable_targets(game):
    assert resolve_active_actor_index(game) == 0
    assert apply_game_command(game, 0, "roll_dice") is True
    assert resolve_active_actor_index(game) == 1

    game.initial_dice_phase = False
    game.initial_placement_order = list(game.players)
    game.initial_player_index = 0
    game.initial_placement_counts = {player.name: 0 for player in game.players}
    player = game.players[0]
    node = game.get_initial_settlement_candidates()[0]

    assert apply_game_command(
        game,
        0,
        "initial_place",
        {"target": _target_id(game, node, "node")},
    )
    assert node.building.owner is player
    assert game.waiting_for_road is True

    edge = game.get_initial_road_candidates(player)[0]
    board_edge = next(
        candidate
        for candidate in game.board.edges
        if {id(candidate[0]), id(candidate[1])} == {id(edge[0]), id(edge[1])}
    )
    assert apply_game_command(
        game,
        0,
        "initial_place",
        {"target": _target_id(game, board_edge, "edge")},
    )
    assert any(road.owner is player for road in game.board.roads)


@pytest.mark.parametrize(
    "target",
    ["node-999", "node-01", "edge-000", {"x": 10, "y": 20}],
)
def test_initial_placement_rejects_invalid_or_wrong_kind_targets(game, target):
    game.initial_dice_phase = False
    game.initial_placement_order = list(game.players)
    game.initial_player_index = 0

    with pytest.raises(NetworkActionError) as error:
        apply_game_command(game, 0, "initial_place", {"target": target})

    assert error.value.code == "invalid_target"
    assert all(node.building is None for node in game.board.nodes)


def test_discard_phase_allows_only_the_discarding_seat(game):
    game.phase = "main"
    game.current_player_index = 0
    game.special_phase = "discard"
    game.discard_player = game.players[1]
    game.discard_remaining = 1
    _give_from_bank(game, game.players[1], ResourceType.WOOD)

    assert resolve_active_actor_index(game) == 1
    with pytest.raises(NetworkActionError) as error:
        apply_game_command(
            game, 0, "select_resource", {"resource": "WOOD"}
        )
    assert error.value.code == "not_active_player"

    assert apply_game_command(
        game, 1, "select_resource", {"resource": "WOOD"}
    )
    assert game.players[1].resources[ResourceType.WOOD] == 0


def test_normal_turn_and_robber_commands_use_authoritative_actor_and_manifest_id(game):
    game.phase = "main"
    game.current_player_index = 2
    game.special_phase = None
    assert resolve_active_actor_index(game) == 2

    game.current_player_index = 0
    game.special_phase = "move_robber"
    game.robber_tile_candidates = [
        tile for tile in game.board.tiles if tile is not game.board.robber_tile
    ]
    target_tile = game.robber_tile_candidates[0]
    target_id = _target_id(game, target_tile, "tile")
    assert target_id.startswith("tile-")

    assert apply_game_command(
        game, 0, "move_robber", {"target": target_id}
    )
    assert game.board.robber_tile is target_tile


def test_legal_semantic_build_uses_edge_id_without_screen_coordinates(game):
    game.phase = "main"
    game.current_player_index = 0
    game.dice_rolled = True
    player = game.players[0]
    anchor = game.board.nodes[0]
    anchor.building = Building(player)
    player.settlements_remaining -= 1
    _give_from_bank(game, player, ResourceType.WOOD)
    _give_from_bank(game, player, ResourceType.BRICK)
    edge = game.get_buildable_road_edges(player)[0]
    board_edge = next(
        candidate
        for candidate in game.board.edges
        if {id(candidate[0]), id(candidate[1])} == {id(edge[0]), id(edge[1])}
    )

    assert apply_game_command(
        game,
        0,
        "build",
        {
            "piece": "road",
            "target": _target_id(game, board_edge, "edge"),
        },
    )
    assert len(game.board.roads) == 1
    assert game.board.roads[0].owner is player


def test_domestic_trade_actor_switches_and_network_handoff_is_automatic(game):
    game.phase = "main"
    game.current_player_index = 0
    game.dice_rolled = True
    active, partner = game.players[:2]
    _give_from_bank(game, active, ResourceType.WOOD)
    _give_from_bank(game, partner, ResourceType.ORE)

    assert apply_game_command(game, 0, "start_domestic_trade")
    assert apply_game_command(
        game, 0, "trade_partner", {"seat_index": 1}
    )
    assert apply_game_command(
        game,
        0,
        "trade_adjust",
        {"side": "give", "resource": "WOOD", "delta": 1},
    )
    assert apply_game_command(
        game,
        0,
        "trade_adjust",
        {"side": "receive", "resource": "ORE", "delta": 1},
    )
    assert apply_game_command(game, 0, "trade_submit")

    # The pass-and-play reveal gate must not block an isolated LAN client.
    assert game.special_phase == "domestic_trade_response"
    assert resolve_active_actor_index(game) == 1
    with pytest.raises(NetworkActionError) as error:
        apply_game_command(game, 0, "trade_accept")
    assert error.value.code == "not_active_player"

    assert apply_game_command(game, 1, "trade_accept")
    assert active.resources[ResourceType.ORE] == 1
    assert partner.resources[ResourceType.WOOD] == 1
    assert game.special_phase is None


def test_network_handoff_helper_clears_local_player_gate(game):
    game.phase = "main"
    game.current_player_index = 1
    game.begin_player_handoff(game.players[1], context="手番")

    assert resolve_active_actor_index(game) == 1
    assert advance_network_handoffs(game) is True
    assert game.special_phase is None


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("roll_dice", []),
        ("roll_dice", {"extra": True}),
        ("build", {"piece": "road", "target": "edge-000", "x": 10}),
        ("trade_adjust", {"side": "give", "resource": "WOOD", "delta": True}),
        ("use_development", {"card": "victory_point"}),
    ],
)
def test_commands_strictly_reject_bad_types_and_extra_fields(game, command, args):
    with pytest.raises(NetworkActionError) as error:
        apply_game_command(game, 0, command, args)

    assert error.value.code in {"invalid_args", "action_not_allowed"}


def test_command_options_are_empty_without_authority_or_after_finish(game):
    assert build_game_command_options(game, None) == []
    assert build_game_command_options(game, -1) == []
    assert build_game_command_options(game, True) == []
    assert build_game_command_options(game, 1) == []

    game.players[0].is_ai = True
    assert build_game_command_options(game, 0) == []
    game.players[0].is_ai = False

    game.phase = "finished"
    assert build_game_command_options(game, 0) == []


def test_initial_command_options_use_stable_targets_for_settlement_and_road(game):
    assert build_game_command_options(game, 0) == [
        {"command": "roll_dice", "args": {}}
    ]

    game.initial_dice_phase = False
    game.initial_placement_order = list(game.players)
    game.initial_player_index = 0
    settlement_candidates = game.get_initial_settlement_candidates()
    options = build_game_command_options(game, 0)
    assert _command_names(options) == {"initial_place"}
    assert {args["target"] for args in _command_args(options, "initial_place")} == {
        _target_id(game, node, "node") for node in settlement_candidates
    }

    player = game.players[0]
    node = settlement_candidates[0]
    node.building = Building(player)
    game.last_settlement_node = node
    game.waiting_for_road = True
    road_candidates = game.get_initial_road_candidates(player)
    options = build_game_command_options(game, 0)
    assert {args["target"] for args in _command_args(options, "initial_place")} == {
        _target_id(game, edge, "edge") for edge in road_candidates
    }


def test_main_command_options_cover_roll_build_trade_development_and_end(game):
    game.phase = "main"
    game.current_player_index = 0
    game.dice_rolled = False
    player = game.players[0]
    for card_type in (
        DevelopmentCardType.KNIGHT,
        DevelopmentCardType.YEAR_OF_PLENTY,
        DevelopmentCardType.MONOPOLY,
    ):
        player.development_cards[card_type] = 1

    before_roll = build_game_command_options(game, 0)
    assert _command_names(before_roll) == {"roll_dice", "use_development"}
    assert {args["card"] for args in _command_args(before_roll, "use_development")} == {
        "knight",
        "year_of_plenty",
        "monopoly",
    }

    game.dice_rolled = True
    for resource in player.resources:
        _give_from_bank(game, player, resource, 6)
    _give_from_bank(game, game.players[1], ResourceType.ORE)

    anchor = game.board.nodes[0]
    anchor.building = Building(player)
    player.settlements_remaining -= 1
    first_edge = next(edge for edge in game.board.edges if anchor in edge)
    neighbor = first_edge[1] if first_edge[0] is anchor else first_edge[0]
    second_edge = next(
        edge
        for edge in game.board.edges
        if neighbor in edge and anchor not in edge
    )
    game.board.roads.extend(
        (Road(player, *first_edge), Road(player, *second_edge))
    )
    player.roads_remaining -= 2
    player.development_cards[DevelopmentCardType.ROAD_BUILDING] = 1

    options = build_game_command_options(game, 0)
    command_names = _command_names(options)
    assert {
        "build",
        "buy_development",
        "start_bank_trade",
        "start_domestic_trade",
        "use_development",
        "end_turn",
    } <= command_names
    assert {args["piece"] for args in _command_args(options, "build")} == {
        "road",
        "settlement",
        "city",
    }
    assert {args["card"] for args in _command_args(options, "use_development")} == {
        "knight",
        "road_building",
        "year_of_plenty",
        "monopoly",
    }


def test_year_of_plenty_option_requires_bank_stock(game):
    game.phase = "main"
    game.current_player_index = 0
    player = game.players[0]
    player.development_cards[DevelopmentCardType.YEAR_OF_PLENTY] = 1
    for resource in player.resources:
        game.bank.resources[resource] = 0

    cards = {
        args["card"]
        for args in _command_args(
            build_game_command_options(game, 0),
            "use_development",
        )
    }
    assert "year_of_plenty" not in cards


@pytest.mark.parametrize(
    ("special_phase", "expected_resources"),
    [
        ("discard", {"WOOD", "SHEEP"}),
        ("year_of_plenty", {"WOOD", "SHEEP", "WHEAT", "BRICK", "ORE"}),
        ("monopoly", {"WOOD", "SHEEP", "WHEAT", "BRICK", "ORE"}),
    ],
)
def test_resource_phase_command_options_are_exact(
    game,
    special_phase,
    expected_resources,
):
    game.phase = "main"
    game.current_player_index = 0
    game.special_phase = special_phase
    if special_phase == "discard":
        game.discard_player = game.players[0]
        game.discard_remaining = 2
        _give_from_bank(game, game.players[0], ResourceType.WOOD)
        _give_from_bank(game, game.players[0], ResourceType.SHEEP)

    options = build_game_command_options(game, 0)
    assert _command_names(options) == {"select_resource"}
    assert {
        args["resource"] for args in _command_args(options, "select_resource")
    } == expected_resources


def test_robber_command_options_use_tile_ids_and_sorted_seat_indices(game):
    game.phase = "main"
    game.current_player_index = 0
    game.special_phase = "move_robber"
    game.robber_tile_candidates = [game.board.tiles[3], game.board.tiles[1]]
    options = build_game_command_options(game, 0)
    assert _command_args(options, "move_robber") == [
        {"target": _target_id(game, game.board.tiles[1], "tile")},
        {"target": _target_id(game, game.board.tiles[3], "tile")},
    ]

    game.special_phase = "steal"
    game.robber_target_players = [game.players[2], game.players[1]]
    options = build_game_command_options(game, 0)
    assert _command_args(options, "steal") == [
        {"seat_index": 1},
        {"seat_index": 2},
    ]


def test_bank_trade_command_options_follow_hand_and_bank_stock(game):
    game.phase = "main"
    game.current_player_index = 0
    player = game.players[0]
    _give_from_bank(game, player, ResourceType.WOOD, 4)
    game.special_phase = "bank_trade_give"

    options = build_game_command_options(game, 0)
    assert _command_args(options, "select_resource") == [
        {"resource": "WOOD"}
    ]
    assert "cancel" in _command_names(options)

    game.special_phase = "bank_trade_receive"
    game.bank_trade_give_resource = ResourceType.WOOD
    for resource in player.resources:
        game.bank.resources[resource] = 0
    game.bank.resources[ResourceType.SHEEP] = 1
    options = build_game_command_options(game, 0)
    assert _command_args(options, "select_resource") == [
        {"resource": "SHEEP"}
    ]
    assert "cancel" in _command_names(options)

    player.resources[ResourceType.WOOD] = 3
    assert build_game_command_options(game, 0) == [
        {"command": "cancel", "args": {}}
    ]


def test_road_building_options_require_placing_remaining_legal_roads(game):
    game.phase = "main"
    game.current_player_index = 0
    game.special_phase = "road_building"
    game.free_roads_remaining = 2
    player = game.players[0]
    anchor = game.board.nodes[0]
    anchor.building = Building(player)
    player.settlements_remaining -= 1

    candidates = game.get_buildable_road_edges(
        player,
        require_affordability=False,
    )
    options = build_game_command_options(game, 0)
    assert _command_names(options) == {"build"}
    assert {args["target"] for args in _command_args(options, "build")} == {
        _target_id(game, edge, "edge") for edge in candidates
    }

    player.roads_remaining = 0
    assert build_game_command_options(game, 0) == [
        {"command": "finish_road_building", "args": {}}
    ]


def test_domestic_trade_partner_and_edit_options_are_actionable(game):
    game.phase = "main"
    game.current_player_index = 0
    game.dice_rolled = True
    active, partner, other = game.players
    _give_from_bank(game, active, ResourceType.WOOD)
    _give_from_bank(game, partner, ResourceType.ORE)
    _give_from_bank(game, other, ResourceType.WHEAT)
    assert game.start_domestic_trade()

    options = build_game_command_options(game, 0)
    assert _command_args(options, "trade_partner") == [
        {"seat_index": 1},
        {"seat_index": 2},
    ]
    assert {"trade_broadcast", "cancel"} <= _command_names(options)

    assert game.select_domestic_trade_partner(1)
    options = build_game_command_options(game, 0)
    assert _command_args(options, "trade_edit_side") == [
        {"side": "receive"}
    ]
    assert {"side": "give", "resource": "WOOD", "delta": 1} in _command_args(
        options,
        "trade_adjust",
    )
    assert _command_args(options, "trade_submit") == []

    assert game.adjust_domestic_trade_resource(
        "give",
        ResourceType.WOOD,
        1,
    )
    assert game.adjust_domestic_trade_resource(
        "receive",
        ResourceType.ORE,
        1,
    )
    options = build_game_command_options(game, 0)
    assert _command_args(options, "trade_submit") == [{}]
    assert {"side": "give", "resource": "WOOD", "delta": -1} in _command_args(
        options,
        "trade_adjust",
    )
    assert {"side": "receive", "resource": "ORE", "delta": -1} in _command_args(
        options,
        "trade_adjust",
    )


@pytest.mark.parametrize(
    ("special_phase", "seat_index", "expected"),
    [
        (
            "domestic_trade_handoff",
            1,
            {"trade_reveal", "cancel"},
        ),
        (
            "domestic_trade_response",
            1,
            {"trade_accept", "trade_counter", "trade_reject", "cancel"},
        ),
        (
            "domestic_trade_counter_response",
            0,
            {"trade_accept", "trade_reject", "cancel"},
        ),
    ],
)
def test_domestic_trade_response_command_options(
    game,
    special_phase,
    seat_index,
    expected,
):
    game.phase = "main"
    game.current_player_index = 0
    active, partner = game.players[:2]
    _give_from_bank(game, active, ResourceType.WOOD)
    _give_from_bank(game, partner, ResourceType.ORE)
    game.domestic_trade_partner = partner
    game.domestic_trade_give[ResourceType.WOOD] = 1
    game.domestic_trade_receive[ResourceType.ORE] = 1
    game.special_phase = special_phase

    assert _command_names(build_game_command_options(game, seat_index)) == expected
    assert build_game_command_options(game, 2) == []


def test_command_options_are_deterministic_bounded_json_safe_and_read_only(
    game,
    monkeypatch,
):
    before_state = serialize_game(game)
    before_random = random.getstate()

    def fail_if_applied(*_args, **_kwargs):
        raise AssertionError("option discovery must not apply speculative commands")

    monkeypatch.setattr(
        "game.network_actions.apply_game_command",
        fail_if_applied,
    )
    first = build_game_command_options(game, 0)
    second = build_game_command_options(game, 0)

    assert first == second
    assert len(first) <= MAX_GAME_COMMAND_OPTIONS
    assert len(first) == len(
        {
            (option["command"], json.dumps(option["args"], sort_keys=True))
            for option in first
        }
    )
    assert json.loads(json.dumps(first, allow_nan=False)) == first
    assert serialize_game(game) == before_state
    assert random.getstate() == before_random


def test_standard_board_option_space_fits_safety_bound_with_large_margin(game):
    # At most all edges, both node-based build kinds, the resource-edit grid,
    # and a generous fixed set of phase buttons can coexist on this board.
    conservative_standard_upper_bound = (
        len(game.board.edges)
        + 2 * len(game.board.nodes)
        + 4 * len(game.players[0].resources)
        + 32
    )
    assert (len(game.board.edges), len(game.board.nodes)) == (72, 54)
    assert conservative_standard_upper_bound == 232
    assert conservative_standard_upper_bound < MAX_GAME_COMMAND_OPTIONS
