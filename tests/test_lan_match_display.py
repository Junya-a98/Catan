import ast
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pygame
import pytest

from game.building import Building, BuildingType
from game.game import CatanGame
from game.lan_match_display import (
    BUILD_PIECES,
    LanMatchDisplayState,
    build_lan_match_layout,
    draw_lan_match_display,
    hit_test_lan_match_display,
)
from game.network_protocol import build_state_snapshot
from game.network_view import parse_network_view
from game.resources import ResourceType
from game.road import Road


@pytest.fixture(scope="module", autouse=True)
def pygame_runtime():
    pygame.init()
    pygame.display.set_mode((1, 1))
    yield
    pygame.quit()


@pytest.fixture
def match_game():
    game = CatanGame(board_seed=9090, ai_player_count=0, headless=True)
    game.configure_players(4, reset_logs=False)
    game.phase = "main"
    game.current_player_index = 0
    game.dice_rolled = True
    game.initial_dice_phase = False
    game.log_messages = [
        "Player1のダイス: 8",
        "Player2が木を1枚獲得しました。",
        "Player1が街道を建設しました。",
    ]
    player = game.players[0]
    player.resources[ResourceType.WOOD] = 2
    player.resources[ResourceType.BRICK] = 1
    player.piece_pattern = 3
    game.board.nodes[0].building = Building(player, BuildingType.SETTLEMENT)
    road_edge = next(edge for edge in game.board.edges if game.board.nodes[0] in edge)
    game.board.roads.append(Road(player, *road_edge))
    return game


@pytest.fixture
def match_view(match_game):
    return parse_network_view(
        build_state_snapshot(match_game, viewer_player_index=0, revision=14)
    )


def _option(command, **args):
    return {"command": command, "args": args}


def _signature(command, args):
    return command, json.dumps(dict(args), sort_keys=True)


def _main_options(view):
    edge_ids = [edge.target_id for edge in view.board.edges[:3]]
    node_ids = [node.target_id for node in view.board.nodes[:4]]
    return [
        *[_option("build", piece="road", target=target) for target in edge_ids],
        *[
            _option("build", piece="settlement", target=target)
            for target in node_ids[:2]
        ],
        *[_option("build", piece="city", target=target) for target in node_ids[2:]],
        _option("buy_development"),
        _option("start_bank_trade"),
        _option("start_domestic_trade"),
        _option("use_development", card="knight"),
        _option("use_development", card="road_building"),
        _option("use_development", card="year_of_plenty"),
        _option("use_development", card="monopoly"),
        _option("end_turn"),
    ]


def _domestic_edit_options():
    options = [_option("trade_edit_side", side="receive")]
    for side in ("give", "receive"):
        for resource in ("WOOD", "SHEEP", "WHEAT", "BRICK", "ORE"):
            options.append(
                _option(
                    "trade_adjust",
                    side=side,
                    resource=resource,
                    delta=1,
                )
            )
            options.append(
                _option(
                    "trade_adjust",
                    side=side,
                    resource=resource,
                    delta=-1,
                )
            )
    options.extend((_option("trade_submit"), _option("cancel")))
    return options


def test_display_boundary_has_no_game_or_transport_dependency():
    source_path = (
        Path(__file__).resolve().parents[1]
        / "python"
        / "game"
        / "lan_match_display.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert "game.game" not in imports
    assert "game.lan_transport" not in imports
    assert "game.lan_runtime" not in imports
    assert "game.lan_controller" not in imports


def test_display_state_is_frozen_and_validates_view(match_view):
    state = LanMatchDisplayState(match_view)
    with pytest.raises(FrozenInstanceError):
        state.connected = False
    with pytest.raises(ValueError):
        LanMatchDisplayState(match_view, selected_build_piece="ship")
    with pytest.raises(TypeError):
        LanMatchDisplayState(object())


@pytest.mark.parametrize("size", [(1200, 800), (1280, 720), (1920, 1280)])
@pytest.mark.parametrize("option_factory", [_main_options, lambda _view: _domestic_edit_options()])
def test_responsive_layout_contains_regions_controls_players_and_harbors(
    size,
    option_factory,
    match_view,
):
    state = LanMatchDisplayState(
        match_view,
        option_factory(match_view),
        selected_build_piece="road",
        room_code="ABC234",
    )
    layout = build_lan_match_layout(size, state)

    assert layout.screen_rect.contains(layout.header_rect)
    assert layout.screen_rect.contains(layout.board_rect)
    assert layout.screen_rect.contains(layout.side_rect)
    assert layout.screen_rect.contains(layout.players_rect)
    assert not layout.header_rect.colliderect(layout.board_rect)
    assert not layout.header_rect.colliderect(layout.side_rect)
    assert not layout.board_rect.colliderect(layout.side_rect)
    assert not layout.board_rect.colliderect(layout.players_rect)
    assert not layout.side_rect.colliderect(layout.players_rect)
    assert layout.side_rect.contains(layout.guidance_rect)
    assert layout.side_rect.contains(layout.action_rect)
    assert layout.side_rect.contains(layout.log_rect)
    assert not layout.guidance_rect.colliderect(layout.action_rect)
    assert not layout.action_rect.colliderect(layout.log_rect)

    for control in layout.controls:
        assert layout.action_rect.contains(control.rect), control.action_id
    for index, first in enumerate(layout.controls):
        for second in layout.controls[index + 1 :]:
            assert not first.rect.colliderect(second.rect), (
                first.action_id,
                second.action_id,
            )

    assert [player.seat for player in layout.players] == [1, 2, 3, 4]
    for player in layout.players:
        assert layout.players_rect.contains(player.rect)
    for index, first in enumerate(layout.players):
        for second in layout.players[index + 1 :]:
            assert not first.rect.colliderect(second.rect)

    assert len(layout.harbors) == 9
    for harbor in layout.harbors:
        assert layout.board_rect.contains(harbor.rect)
    for index, first in enumerate(layout.harbors):
        for second in layout.harbors[index + 1 :]:
            assert not first.rect.colliderect(second.rect), (
                first.harbor_id,
                second.harbor_id,
            )


@pytest.mark.parametrize("size", [(1200, 800), (1280, 720), (1920, 1280)])
def test_compact_harbors_do_not_overlap_their_road_edges(size, match_view):
    layout = build_lan_match_layout(size, LanMatchDisplayState(match_view))

    for harbor_layout in layout.harbors:
        harbor = match_view.board.harbor_by_id[harbor_layout.harbor_id]
        start, end = match_view.board.edge_segment(harbor.edge_id)
        screen_start = layout.transform.point(start)
        screen_end = layout.transform.point(end)
        road_bounds = pygame.Rect(
            min(screen_start[0], screen_end[0]),
            min(screen_start[1], screen_end[1]),
            max(1, abs(screen_start[0] - screen_end[0])),
            max(1, abs(screen_start[1] - screen_end[1])),
        ).inflate(12, 12)
        assert not harbor_layout.rect.colliderect(road_bounds), harbor.target_id


def test_controls_and_targets_are_backed_only_by_server_options(match_view):
    options = _main_options(match_view)
    layout = build_lan_match_layout(
        (1200, 800),
        LanMatchDisplayState(
            match_view,
            options,
            selected_build_piece="road",
        ),
    )
    issued = {
        _signature(option["command"], option["args"]) for option in options
    }

    for control in layout.controls:
        assert control.enabled is True
        if control.kind == "command":
            assert _signature(control.command, control.args) in issued
        else:
            assert control.build_piece in BUILD_PIECES
            assert any(
                option["command"] == "build"
                and option["args"]["piece"] == control.build_piece
                for option in options
            )
    assert layout.board_targets
    for target in layout.board_targets:
        assert _signature(target.command, target.args) in issued
        assert set(target.args) <= {"piece", "target"}
        assert "x" not in target.args and "y" not in target.args


def test_build_piece_selection_filters_exact_stable_id_targets(match_view):
    options = _main_options(match_view)
    no_selection = build_lan_match_layout(
        (1200, 800),
        LanMatchDisplayState(match_view, options),
    )
    assert no_selection.board_targets == ()

    road_layout = build_lan_match_layout(
        (1200, 800),
        LanMatchDisplayState(match_view, options, selected_build_piece="road"),
    )
    piece_control = next(
        control
        for control in road_layout.controls
        if control.build_piece == "settlement"
    )
    selection = hit_test_lan_match_display(road_layout, piece_control.rect.center)
    assert selection.kind == "select_build_piece"
    assert selection.build_piece == "settlement"
    assert selection.command is None

    expected = {
        option["args"]["target"]
        for option in options
        if option["command"] == "build" and option["args"]["piece"] == "road"
    }
    assert {target.target_id for target in road_layout.board_targets} == expected
    first = road_layout.board_targets[0]
    hit = hit_test_lan_match_display(road_layout, first.center)
    assert hit.kind == "command"
    assert hit.command == "build"
    assert dict(hit.args) == dict(first.args)
    assert hit.args["target"].startswith("edge-")


@pytest.mark.parametrize(
    ("command", "target_kind"),
    [
        ("initial_place", "node"),
        ("initial_place", "edge"),
        ("move_robber", "tile"),
    ],
)
def test_initial_placement_and_robber_board_hits_are_semantic(
    command,
    target_kind,
    match_view,
):
    target_id = {
        "node": match_view.board.nodes[3].target_id,
        "edge": match_view.board.edges[4].target_id,
        "tile": match_view.board.tiles[5].target_id,
    }[target_kind]
    layout = build_lan_match_layout(
        (1280, 720),
        LanMatchDisplayState(match_view, [_option(command, target=target_id)]),
    )

    assert len(layout.board_targets) == 1
    target = layout.board_targets[0]
    assert target.target_kind == target_kind
    hit = hit_test_lan_match_display(layout, target.center)
    assert hit.kind == "command"
    assert hit.command == command
    assert dict(hit.args) == {"target": target_id}


def test_side_controls_cover_resource_player_bank_development_and_trade_flow(match_view):
    options = [
        _option("roll_dice"),
        *[_option("select_resource", resource=resource) for resource in ("WOOD", "ORE")],
        _option("steal", seat_index=1),
        _option("trade_partner", seat_index=2),
        _option("trade_broadcast"),
        _option("trade_edit_side", side="receive"),
        _option("trade_adjust", side="give", resource="WOOD", delta=1),
        _option("trade_adjust", side="receive", resource="ORE", delta=-1),
        _option("trade_submit"),
        _option("trade_reveal"),
        _option("trade_accept"),
        _option("trade_counter"),
        _option("trade_reject"),
        _option("use_development", card="knight"),
        _option("finish_road_building"),
        _option("cancel"),
        _option("end_turn"),
    ]
    layout = build_lan_match_layout(
        (1280, 720),
        LanMatchDisplayState(match_view, options),
    )
    expected = {_signature(option["command"], option["args"]) for option in options}
    actual = {
        _signature(control.command, control.args)
        for control in layout.controls
        if control.kind == "command"
    }

    assert actual == expected
    for control in layout.controls:
        hit = hit_test_lan_match_display(layout, control.rect.center)
        assert hit.kind == "command"
        assert _signature(hit.command, hit.args) in expected


def test_full_domestic_trade_editor_grid_fits_and_every_adjustment_is_clickable(match_view):
    trade_view = replace(match_view, special_phase="domestic_trade_edit")
    options = _domestic_edit_options()
    layout = build_lan_match_layout(
        (1280, 720),
        LanMatchDisplayState(trade_view, options),
    )

    assert len(layout.controls) == len(options)
    assert all(layout.action_rect.contains(control.rect) for control in layout.controls)
    adjustments = [
        control for control in layout.controls if control.command == "trade_adjust"
    ]
    assert len(adjustments) == 20
    submit = next(
        control for control in layout.controls if control.command == "trade_submit"
    )
    assert submit.rect.width > adjustments[0].rect.width
    for control in adjustments:
        hit = hit_test_lan_match_display(layout, control.rect.center)
        assert hit.command == "trade_adjust"
        assert dict(hit.args) == dict(control.args)


def test_spectators_are_read_only_even_if_stale_options_are_passed(match_game):
    spectator = parse_network_view(
        build_state_snapshot(match_game, viewer_player_index=None, revision=15)
    )
    options = [
        _option("roll_dice"),
        _option("build", piece="road", target=spectator.board.edges[0].target_id),
    ]
    layout = build_lan_match_layout(
        (1200, 800),
        LanMatchDisplayState(spectator, options),
    )

    assert layout.controls == ()
    assert layout.board_targets == ()
    assert hit_test_lan_match_display(layout, layout.board_rect.center) is None


def test_disconnected_state_disables_controls_and_board_hits(match_view):
    options = [
        _option("roll_dice"),
        _option("move_robber", target=match_view.board.tiles[0].target_id),
    ]
    layout = build_lan_match_layout(
        (1200, 800),
        LanMatchDisplayState(
            match_view,
            options,
            connected=False,
            error="ホストへの接続が切れました。",
        ),
    )

    assert layout.board_targets == ()
    assert len(layout.controls) == 1
    assert layout.controls[0].enabled is False
    assert hit_test_lan_match_display(layout, layout.controls[0].rect.center) is None


def test_invalid_or_duplicate_options_fail_closed_without_crashing(match_view):
    options = [
        None,
        {"command": [], "args": {}},
        {"command": "roll_dice", "args": {}},
        {"command": "roll_dice", "args": {}},
        {"command": "debug_win", "args": {}},
        {"command": "end_turn", "args": {"x": 10.5}},
        {"command": "build", "args": {"piece": "road", "target": "edge-9999"}},
    ]
    layout = build_lan_match_layout(
        (1200, 800),
        LanMatchDisplayState(match_view, options),
    )

    assert [control.command for control in layout.controls] == ["roll_dice"]
    assert layout.board_targets == ()


def test_layout_is_deterministic_for_identical_read_model_and_options(match_view):
    state = LanMatchDisplayState(
        match_view,
        _main_options(match_view),
        selected_build_piece="city",
        room_code="ABC234",
    )

    assert build_lan_match_layout((1200, 800), state) == build_lan_match_layout(
        (1200, 800), state
    )


@pytest.mark.parametrize("size", [(1200, 800), (1280, 720), (1920, 1280)])
def test_render_smoke_draws_board_pieces_private_hand_logs_and_controls(
    size,
    match_view,
):
    surface = pygame.Surface(size)
    surface.fill((0, 0, 0))
    state = LanMatchDisplayState(
        match_view,
        _main_options(match_view),
        selected_build_piece="road",
        room_code="ABC234",
    )

    layout = draw_lan_match_display(surface, state)

    assert layout.screen_rect.size == size
    assert surface.get_at((0, 0))[:3] != (0, 0, 0)
    assert surface.get_at(layout.header_rect.center)[:3] != (0, 0, 0)
    assert surface.get_at(layout.side_rect.center)[:3] != (0, 0, 0)
    assert surface.get_at(layout.players[0].rect.center)[:3] != (0, 0, 0)
    road = next(edge for edge in match_view.board.edges if edge.road is not None)
    road_center = layout.transform.point(match_view.board.position_for(road.target_id))
    assert surface.get_at(road_center)[:3] != (44, 91, 128)


def test_render_smoke_handles_spectator_and_disconnect(match_game):
    spectator = parse_network_view(
        build_state_snapshot(match_game, viewer_player_index=None, revision=16)
    )
    surface = pygame.Surface((1200, 800))

    spectator_layout = draw_lan_match_display(
        surface,
        LanMatchDisplayState(spectator, room_code="ABC234"),
    )
    assert spectator_layout.controls == ()

    disconnected_layout = draw_lan_match_display(
        surface,
        LanMatchDisplayState(
            spectator,
            connected=False,
            error="再接続しています。",
        ),
    )
    assert disconnected_layout.controls == ()
