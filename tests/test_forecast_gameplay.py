import json
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest

from game.building import Building
from game.forecast_events import (
    SHEEP_DROUGHT_EVENT_ID,
    WHEAT_HARVEST_EVENT_ID,
)
from game.game import CatanGame
from game.network_protocol import build_state_snapshot
from game.persistence import SaveGameError, restore_game, serialize_game
from game.resources import ResourceType
from game.variant import VariantConfig
from game.variant_state import VariantState


DETERMINISTIC_DECK_SEED = "1" * 64


@pytest.fixture
def forecast_game():
    game = CatanGame(
        board_seed=28071,
        variant_config=VariantConfig.forecast_events(),
        headless=True,
    )
    game.configure_players(3, reset_logs=False)
    game.variant_state = VariantState.initial(
        game.variant_config,
        deck_seed=DETERMINISTIC_DECK_SEED,
    )
    yield game
    game.audio.stop()


def _advance_forecast(game, turns):
    for _ in range(turns):
        game.advance_forecast_event_turn()


def _finish_turn(game):
    game.dice_rolled = True
    game.finish_current_turn()
    if game.special_phase == "player_handoff":
        assert game.reveal_player_handoff() is True


def _isolate_producing_tile(game, resource_type, *, number=5):
    for node in game.board.nodes:
        node.building = None
    for tile in game.board.tiles:
        tile.number = None

    tile = next(
        tile
        for tile in game.board.tiles
        if tile.resource_type is resource_type
    )
    tile.number = number
    tile.corners[0].building = Building(game.players[0])
    game.board.robber_tile = next(
        tile
        for tile in game.board.tiles
        if tile.resource_type is ResourceType.DESERT
    )
    return tile, game.players[0]


def test_initial_forecast_is_announced_and_activates_after_completed_turns(
    forecast_game,
):
    game = forecast_game

    assert game.get_next_forecast_event_id() == WHEAT_HARVEST_EVENT_ID
    assert game.variant_state.public["completed_turns"] == 0

    game.start_main_phase()

    assert game.latest_event["title"] == "イベント予告: 豊作"
    assert "あと2手番で発動" in game.latest_event["detail"]

    _finish_turn(game)
    assert game.variant_state.public["completed_turns"] == 1
    assert not game.is_forecast_event_active(WHEAT_HARVEST_EVENT_ID)

    _finish_turn(game)
    assert game.variant_state.public["completed_turns"] == 2
    assert game.is_forecast_event_active(WHEAT_HARVEST_EVENT_ID)
    assert game.latest_event["title"] == "イベント発動: 豊作"
    assert game.get_next_forecast_event_id() == SHEEP_DROUGHT_EVENT_ID


def test_wheat_harvest_adds_bonus_only_after_official_base_production(
    forecast_game,
):
    game = forecast_game
    _advance_forecast(game, 2)
    tile, player = _isolate_producing_tile(game, ResourceType.WHEAT)
    game.bank.resources[ResourceType.WHEAT] = 2

    summaries = game.distribute_resources(tile.number)

    assert player.resources[ResourceType.WHEAT] == 2
    assert game.bank.available(ResourceType.WHEAT) == 0
    assert game.last_resource_distribution[player.name] == {
        ResourceType.WHEAT: 2
    }
    assert any("麦 +1（豊作）" in summary for summary in summaries)
    assert not game.is_forecast_event_active(WHEAT_HARVEST_EVENT_ID)


def test_wheat_harvest_keeps_base_production_when_bank_cannot_pay_bonus(
    forecast_game,
):
    game = forecast_game
    _advance_forecast(game, 2)
    tile, player = _isolate_producing_tile(game, ResourceType.WHEAT)
    game.bank.resources[ResourceType.WHEAT] = 1

    summaries = game.distribute_resources(tile.number)

    assert player.resources[ResourceType.WHEAT] == 1
    assert game.bank.available(ResourceType.WHEAT) == 0
    assert game.last_resource_distribution[player.name] == {
        ResourceType.WHEAT: 1
    }
    assert all("豊作" not in summary for summary in summaries)
    assert not game.is_forecast_event_active(WHEAT_HARVEST_EVENT_ID)


def test_sheep_drought_suppresses_production_for_one_round_then_expires(
    forecast_game,
):
    game = forecast_game
    _advance_forecast(game, 2)
    assert game.get_next_forecast_event_id() == SHEEP_DROUGHT_EVENT_ID

    while not game.is_forecast_event_active(SHEEP_DROUGHT_EVENT_ID):
        _advance_forecast(game, 1)

    tile, player = _isolate_producing_tile(game, ResourceType.SHEEP)
    bank_before = game.bank.available(ResourceType.SHEEP)

    assert game.distribute_resources(tile.number) == []
    assert player.resources[ResourceType.SHEEP] == 0
    assert game.bank.available(ResourceType.SHEEP) == bank_before

    _advance_forecast(game, len(game.turn_order) - 1)
    assert game.is_forecast_event_active(SHEEP_DROUGHT_EVENT_ID)
    _advance_forecast(game, 1)
    assert not game.is_forecast_event_active(SHEEP_DROUGHT_EVENT_ID)

    game.distribute_resources(tile.number)
    assert player.resources[ResourceType.SHEEP] == 1
    assert game.bank.available(ResourceType.SHEEP) == bank_before - 1


def test_forecast_save_round_trips_but_network_snapshot_hides_private_deck(
    forecast_game,
):
    game = forecast_game
    _advance_forecast(game, 2)
    saved = serialize_game(game)

    assert saved["rules"]["variant"] == game.variant_config.to_document()
    assert saved["variant_state"]["private"]["deck_seed"] == (
        DETERMINISTIC_DECK_SEED
    )
    assert "draw_pile" in saved["variant_state"]["private"]

    snapshot = build_state_snapshot(
        game,
        viewer_player_index=0,
        revision=9,
    )
    projected = snapshot["state"]["variant_state"]
    assert "private" not in projected
    assert projected["public"] == saved["variant_state"]["public"]
    encoded_snapshot = json.dumps(snapshot, ensure_ascii=False)
    assert DETERMINISTIC_DECK_SEED not in encoded_snapshot
    assert "deck_seed" not in encoded_snapshot
    assert "draw_pile" not in encoded_snapshot
    assert "discard_pile" not in encoded_snapshot

    restored = CatanGame(board_seed=1, headless=True)
    try:
        restore_game(restored, saved, runtime_side_effects=False)
        assert restored.variant_config == game.variant_config
        assert restored.variant_state == game.variant_state
        assert serialize_game(restored) == saved
    finally:
        restored.audio.stop()


def test_forecast_save_rejects_a_missing_authority_runtime_state(forecast_game):
    forecast_game.variant_state = None

    with pytest.raises(SaveGameError, match="variant state"):
        serialize_game(forecast_game)
