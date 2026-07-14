from collections import Counter

import pytest

from game.building import Building
from game.custom_map import CustomMapSpec
from game.development_cards import DevelopmentCardType
from game.game import CatanGame
from game.game_board import GameBoard
from game.house_rules import HouseRules
from game.resources import ResourceType


def _board_signature(board):
    return (
        tuple(
            (tile.axial, tile.resource_type, tile.number)
            for tile in sorted(board.tiles, key=lambda tile: tile.axial)
        ),
        tuple(harbor.resource_type for harbor in board.harbors),
    )


def _different_tiles(spec):
    first = spec.tiles[0]
    second = next(
        tile
        for tile in spec.tiles[1:]
        if (tile.resource, tile.number) != (first.resource, first.number)
    )
    return first, second


def test_game_board_custom_mode_rebuilds_exact_layout_and_standard_topology():
    generated = GameBoard(mode="fully_random", seed=86712347)
    spec = CustomMapSpec.from_board(generated, name="統合テスト島")
    first, second = _different_tiles(spec)
    edited = spec.swap_tiles(first.axial, second.axial).swap_harbors(0, 1)

    board = GameBoard(mode="custom", seed=999, custom_map=edited)

    assert CustomMapSpec.from_board(board).fingerprint == edited.fingerprint
    assert (
        len(board.tiles),
        len(board.nodes),
        len(board.edges),
        len(board.harbors),
    ) == (
        19,
        54,
        72,
        9,
    )
    assert board.robber_tile.resource_type is ResourceType.DESERT
    with pytest.raises(ValueError, match="requires"):
        GameBoard(mode="custom", seed=1)
    with pytest.raises(ValueError, match="only valid"):
        GameBoard(mode="constrained", seed=1, custom_map=edited)


def test_pre_game_editor_is_transactional_until_apply_and_cancel_discards_draft():
    game = CatanGame(board_seed=417, headless=True)
    original = _board_signature(game.board)

    assert game.open_pre_game_settings() is True
    first, second = _different_tiles(game.pre_game_draft_map)
    assert game.handle_pre_game_tile_target(first.axial) is True
    assert game.handle_pre_game_tile_target(second.axial) is True
    assert game.pre_game_draft_board_mode == "custom"
    assert _board_signature(game.board) == original

    assert game.close_pre_game_settings() is True
    assert game.board_mode == "constrained"
    assert game.custom_map_spec is None
    assert _board_signature(game.board) == original


def test_pre_game_editor_applies_custom_map_rules_and_filtered_deck_together():
    game = CatanGame(board_seed=90210, headless=True)
    assert game.open_pre_game_settings() is True
    first, second = _different_tiles(game.pre_game_draft_map)
    game.handle_pre_game_tile_target(first.axial)
    game.handle_pre_game_tile_target(second.axial)
    expected_fingerprint = game.pre_game_draft_map.fingerprint
    game.handle_pre_game_settings_action("toggle_bank_3_to_1")
    game.handle_pre_game_settings_action("toggle_skip_discard")
    game.handle_pre_game_settings_action("toggle_dev_monopoly")

    assert game.apply_pre_game_settings_draft() is True

    assert game.pre_game_settings_open is False
    assert game.board_mode == "custom"
    assert game.custom_map_spec.fingerprint == expected_fingerprint
    assert CustomMapSpec.from_board(game.board).fingerprint == expected_fingerprint
    assert game.house_rules.bank_trade_3_to_1 is True
    assert game.house_rules.skip_discard_on_seven is True
    assert DevelopmentCardType.MONOPOLY in game.house_rules.disabled_development_cards
    assert DevelopmentCardType.MONOPOLY not in game.development_deck
    assert len(game.development_deck) == 23


def test_house_rule_bank_trade_is_three_to_one_without_weakening_two_to_one_port():
    game = CatanGame(
        board_seed=501,
        house_rules=HouseRules(bank_trade_3_to_1=True),
        headless=True,
    )
    player = game.players[0]

    assert set(game.get_trade_rates(player).values()) == {3}

    resource_harbor = next(
        harbor for harbor in game.board.harbors if harbor.resource_type is not None
    )
    resource_harbor.node1.building = Building(player)
    rates = game.get_trade_rates(player)

    assert rates[resource_harbor.resource_type] == 2
    assert Counter(rates.values()) == Counter({3: 4, 2: 1})


def test_skip_discard_on_seven_moves_directly_to_robber():
    game = CatanGame(
        board_seed=73,
        house_rules=HouseRules(skip_discard_on_seven=True),
        headless=True,
    )
    game.start_main_phase()
    player = game.get_current_player()
    player.add_resource(ResourceType.WOOD, 8)

    game.start_robber_phase()

    assert game.discard_queue == []
    assert game.discard_player is None
    assert game.special_phase == "move_robber"


def test_standard_house_rules_remain_the_constructor_default():
    game = CatanGame(board_seed=73, headless=True)

    assert game.house_rules == HouseRules.standard()
    assert len(game.development_deck) == 25
    assert Counter(game.development_deck) == Counter(
        {
            DevelopmentCardType.KNIGHT: 14,
            DevelopmentCardType.VICTORY_POINT: 5,
            DevelopmentCardType.ROAD_BUILDING: 2,
            DevelopmentCardType.YEAR_OF_PLENTY: 2,
            DevelopmentCardType.MONOPOLY: 2,
        }
    )
