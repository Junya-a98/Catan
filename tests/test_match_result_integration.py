import pytest

from game.building import Building
from game.game import CatanGame
from game.hex_tile import get_token_pip_count
from game.resources import ResourceType


@pytest.fixture
def game():
    return CatanGame(
        board_seed=8128,
        ai_player_count=0,
        ai_action_delay_ms=0,
        headless=True,
    )


def test_configuring_players_starts_clean_public_match_metrics(game):
    game.match_metrics.record_build("seat-1", "road")

    game.configure_players(3, reset_logs=False)

    assert [row.player_id for row in game.match_metrics.players] == [
        "seat-1",
        "seat-2",
        "seat-3",
    ]
    assert all(row.roads_built == 0 for row in game.match_metrics.players)
    assert game.match_result is None


def test_initial_placement_records_both_piece_types(game):
    player = game.players[0]
    game.initial_dice_phase = False
    game.initial_placement_order = [player, game.players[1], game.players[1], player]
    game.initial_player_index = 0
    node = game.get_initial_settlement_candidates()[0]

    game.handle_initial_placement((node.x, node.y))
    _, road_target = game.get_initial_road_candidates(player)[0]
    game.handle_initial_placement((road_target.x, road_target.y))

    metrics = game.match_metrics.player("seat-1")
    assert metrics.settlements_built == 1
    assert metrics.roads_built == 1


def test_dice_luck_uses_probability_and_ignores_bank_inventory(game):
    player = game.players[0]
    tile = next(
        candidate
        for candidate in game.board.tiles
        if candidate.number is not None and candidate is not game.board.robber_tile
    )
    node = tile.corners[0]
    node.building = Building(player)
    expected_per_roll = sum(
        get_token_pip_count(adjacent.number) / 36.0
        for adjacent in node.tiles
        if adjacent is not game.board.robber_tile
    )
    actual_for_roll = sum(
        adjacent.number == tile.number
        for adjacent in node.tiles
        if adjacent is not game.board.robber_tile
    )
    for resource_type in game.bank.resources:
        game.bank.resources[resource_type] = 0

    game.record_dice_luck(tile.number)
    game.record_dice_luck(7)

    metrics = game.match_metrics.player("seat-1")
    assert metrics.actual_production_units == actual_for_roll
    assert metrics.expected_production_units == pytest.approx(expected_per_roll * 2)
    assert metrics.luck_index == pytest.approx(
        actual_for_roll / (expected_per_roll * 2) * 100
    )


def test_successful_trades_are_counted_for_the_result(game):
    active, partner = game.players
    game.phase = "main"
    game.turn_order = list(game.players)
    game.current_player_index = 0
    active.add_resource(ResourceType.WOOD)
    partner.add_resource(ResourceType.SHEEP)
    game.domestic_trade_partner = partner
    game.domestic_trade_give[ResourceType.WOOD] = 1
    game.domestic_trade_receive[ResourceType.SHEEP] = 1
    game.special_phase = "domestic_trade_response"

    assert game.execute_domestic_trade() is True
    assert game.match_metrics.player("seat-1").domestic_trades == 1
    assert game.match_metrics.player("seat-2").domestic_trades == 1

    rate = game.get_trade_rates(active)[ResourceType.WOOD]
    active.add_resource(ResourceType.WOOD, rate)
    game.bank_trade_give_resource = ResourceType.WOOD
    game.special_phase = "bank_trade_receive"
    game.select_bank_trade_resource(ResourceType.ORE)

    assert game.match_metrics.player("seat-1").bank_trades == 1


def test_finished_headless_match_builds_web_safe_result_payload(game):
    game.start_main_phase()
    winner = game.get_current_player()
    winner.victory_point_cards = game.victory_point_target

    game.check_for_winner(winner)

    assert game.phase == "finished"
    assert game.match_result["format"] == "catan-match-result"
    assert game.match_result["winner"]["name"] == winner.name
    assert game.match_result["standings"][0]["victory_points"] >= game.victory_point_target
    assert game.match_result["replay"]["available"] is False
    assert game.match_result["timeline_unit"] == "イベント"
    assert [
        item["sequence"] for item in game.match_result["vp_progression"]
    ] == list(range(len(game.match_result["vp_progression"])))
    assert len(game.match_result["vp_progression"]) >= 2
    assert all(
        item["replay_frame_index"] is None
        for item in game.match_result["vp_progression"]
    )
