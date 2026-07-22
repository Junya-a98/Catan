import copy

import pytest

from game.game import CatanGame
from game.network_actions import (
    NetworkActionError,
    apply_game_command,
    build_game_command_options,
)
from game.network_protocol import build_state_snapshot
from game.network_view import parse_state_snapshot
from game.persistence import SaveGameError, restore_game, serialize_game
from game.resources import ResourceType
from game.variant import VariantConfig
from game.variant_state import VariantState, VariantStateError


def _game(*, seed=82_001, ai_players=0, ttl=4):
    game = CatanGame(
        board_seed=seed,
        variant_config=VariantConfig.trade2(order_ttl_turns=ttl),
        ai_player_count=ai_players,
        headless=True,
    )
    game.configure_players(2, reset_logs=False, schedule_ai=False)
    game.start_main_phase()
    game.dice_rolled = True
    return game


def _grant(game, player, resource, amount):
    assert game.bank.withdraw(resource, amount)
    player.add_resource(resource, amount)


def _create_wood_for_ore(game, *, wood=2):
    seller = game.players[0]
    _grant(game, seller, ResourceType.WOOD, wood)
    assert game.create_trade_market_order(
        seller,
        {ResourceType.WOOD: wood},
        {ResourceType.ORE: 1},
    )
    return game.get_trade_market_orders()[0]


def test_trade2_config_and_state_are_strict_and_keep_sequence_private():
    config = VariantConfig.trade2()
    assert config.to_document() == {
        "version": 1,
        "kind": "trade2",
        "options": {
            "catalog": "standing_market_v1",
            "order_ttl_turns": 4,
        },
    }
    state = VariantState.initial(config)
    assert state.public == {
        "catalog": "standing_market_v1",
        "completed_turns": 0,
        "orders": (),
    }
    assert "private" not in state.to_public_document()

    with pytest.raises(ValueError, match="trade2 options"):
        VariantConfig.from_document(
            {
                "version": 1,
                "kind": "trade2",
                "options": {"catalog": "standing_market_v1"},
            }
        )
    tampered = state.to_document()
    tampered["public"]["unknown"] = True
    with pytest.raises(VariantStateError, match="trade2"):
        VariantState.from_document(tampered, config=config)


def test_create_reserves_offer_and_snapshot_shows_only_available_hand():
    game = _game()
    try:
        order = _create_wood_for_ore(game)
        seller = game.players[0]
        assert seller.total_resource_count() == 2
        assert seller.available_resource_total() == 0
        assert seller.resource_ledger.reservations_map() == {
            order.reservation_id: {ResourceType.WOOD: 2}
        }

        owner = build_state_snapshot(game, viewer_player_index=0, revision=3)
        spectator = build_state_snapshot(game, viewer_player_index=None, revision=3)
        owner_player = owner["state"]["players"][0]
        assert owner_player["resource_total"] == 2
        assert sum(owner_player["resources"].values()) == 0
        assert spectator["state"]["players"][0]["resources"] is None
        assert "private" not in owner["state"]["variant_state"]
        assert "next_sequence" not in str(owner["state"]["variant_state"])
        assert "market:market-" not in str(owner)
        assert parse_state_snapshot(owner).players[0].resource_total == 2
    finally:
        game.audio.stop()


def test_fill_is_exact_atomic_and_records_both_players_trade_metric():
    game = _game(seed=82_002)
    try:
        order = _create_wood_for_ore(game)
        seller, buyer = game.players
        _grant(game, buyer, ResourceType.ORE, 1)
        game.current_player_index = 1

        assert game.fill_trade_market_order(buyer, order.order_id, order.revision)
        assert game.get_trade_market_orders() == ()
        assert seller.resources[ResourceType.ORE] == 1
        assert buyer.resources[ResourceType.WOOD] == 2
        assert seller.reserved_resource_total() == 0
        assert game.match_metrics.player("seat-1").domestic_trades == 1
        assert game.match_metrics.player("seat-2").domestic_trades == 1
    finally:
        game.audio.stop()


def test_cancel_and_four_completed_turn_expiry_release_escrow():
    cancel_game = _game(seed=82_003)
    expiry_game = _game(seed=82_004)
    try:
        cancelled = _create_wood_for_ore(cancel_game)
        seller = cancel_game.players[0]
        assert cancel_game.cancel_trade_market_order(
            seller,
            cancelled.order_id,
            cancelled.revision,
        )
        assert seller.available_resource_count(ResourceType.WOOD) == 2

        expiring = _create_wood_for_ore(expiry_game)
        for completed in range(1, 5):
            if expiry_game.special_phase == "player_handoff":
                assert expiry_game.reveal_player_handoff()
            expiry_game.dice_rolled = True
            expiry_game.finish_current_turn()
            assert expiry_game.variant_state.public["completed_turns"] == completed
            assert bool(expiry_game.get_trade_market_orders()) == (completed < 4)
        assert expiring.expires_turn == 4
        assert expiry_game.players[0].available_resource_count(ResourceType.WOOD) == 2
    finally:
        cancel_game.audio.stop()
        expiry_game.audio.stop()


@pytest.mark.parametrize("forced_loss", ["discard", "robbery", "monopoly"])
def test_forced_loss_cancels_all_orders_before_removing_resources(forced_loss):
    game = _game(seed=82_010 + len(forced_loss))
    try:
        order = _create_wood_for_ore(game)
        seller, other = game.players
        if forced_loss == "discard":
            game.special_phase = "discard"
            game.discard_player = seller
            game.discard_remaining = 1
            game.discard_resource(ResourceType.WOOD)
        elif forced_loss == "robbery":
            game.current_player_index = 1
            game.steal_random_resource(seller)
        else:
            game.current_player_index = 1
            game.special_phase = "monopoly"
            game.handle_resource_selection(ResourceType.WOOD)

        assert order not in game.get_trade_market_orders()
        assert seller.reserved_resource_total() == 0
        assert other.resources[ResourceType.WOOD] >= (1 if forced_loss != "discard" else 0)
    finally:
        game.audio.stop()


def test_save_restore_cross_validates_public_orders_and_private_escrow():
    game = _game(seed=82_020)
    restored = _game(seed=1)
    try:
        _create_wood_for_ore(game)
        document = serialize_game(game)
        restore_game(restored, document, runtime_side_effects=False)
        assert len(restored.get_trade_market_orders()) == 1
        assert restored.players[0].reserved_resource_total() == 2

        orphan_order = copy.deepcopy(document)
        orphan_order["players"][0].pop("resource_ledger")
        with pytest.raises(SaveGameError, match="市場注文と予約資源"):
            restore_game(restored, orphan_order, runtime_side_effects=False)

        orphan_escrow = copy.deepcopy(document)
        orphan_escrow["variant_state"]["public"]["orders"] = []
        with pytest.raises(SaveGameError, match="市場注文と予約資源"):
            restore_game(restored, orphan_escrow, runtime_side_effects=False)
    finally:
        game.audio.stop()
        restored.audio.stop()


def test_network_market_commands_are_authorized_and_revision_bound():
    game = _game(seed=82_030)
    try:
        seller, buyer = game.players
        _grant(game, seller, ResourceType.WOOD, 1)
        _grant(game, buyer, ResourceType.ORE, 1)
        options = build_game_command_options(game, 0)
        assert {option["command"] for option in options} >= {"market_create"}

        with pytest.raises(NetworkActionError) as wrong_seat:
            apply_game_command(
                game,
                1,
                "market_create",
                {"offer": {"WOOD": 1}, "wanted": {"ORE": 1}},
            )
        assert wrong_seat.value.code == "not_active_player"

        assert apply_game_command(
            game,
            0,
            "market_create",
            {"offer": {"WOOD": 1}, "wanted": {"ORE": 1}},
        )
        order = game.get_trade_market_orders()[0]
        game.current_player_index = 1
        fill = next(
            option
            for option in build_game_command_options(game, 1)
            if option["command"] == "market_fill"
        )
        assert fill["args"] == {
            "order_id": order.order_id,
            "revision": order.revision,
        }
        assert apply_game_command(game, 1, "market_fill", fill["args"])
        with pytest.raises(NetworkActionError):
            apply_game_command(game, 1, "market_fill", fill["args"])
    finally:
        game.audio.stop()


def test_ai_fills_a_public_order_that_reduces_its_building_shortage():
    game = _game(seed=82_040, ai_players=1)
    try:
        human, ai_player = game.players
        _grant(game, human, ResourceType.ORE, 1)
        _grant(game, ai_player, ResourceType.WOOD, 1)
        assert game.create_trade_market_order(
            human,
            {ResourceType.ORE: 1},
            {ResourceType.WOOD: 1},
        )
        game.current_player_index = 1
        game.dice_rolled = True

        assert game.ai.step(game)
        assert game.get_trade_market_orders() == ()
        assert ai_player.resources[ResourceType.ORE] == 1
        assert game.ai_status["title"] == "常設市場で購入"
    finally:
        game.audio.stop()


def test_ai_lists_one_surplus_resource_for_a_missing_building_resource():
    game = _game(seed=82_041, ai_players=1)
    try:
        _human, ai_player = game.players
        _grant(game, ai_player, ResourceType.ORE, 1)
        game.current_player_index = 1
        game.dice_rolled = True

        assert game.ai.step(game)
        orders = game.get_trade_market_orders()
        assert len(orders) == 1
        assert orders[0].seller_index == 1
        assert dict(orders[0].offer) == {ResourceType.ORE: 1}
        assert sum(orders[0].wanted.values()) == 1
        assert ai_player.available_resource_total() == 0
        assert game.ai_status["title"] == "常設市場へ出品"
    finally:
        game.audio.stop()
