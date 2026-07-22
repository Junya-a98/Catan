import copy
import json

import pytest

from game.forecast_events import FORECAST_EVENTS_KIND
from game.game import CatanGame
from game.network_actions import build_game_command_options
from game.network_protocol import build_state_snapshot
from game.network_replay import NetworkReplayStore
from game.network_view import NetworkViewError, parse_state_snapshot
from game.persistence import restore_game, serialize_game
from game.resources import ResourceType
from game.self_play import run_match
from game.variant import CREDIT_VARIANT_KIND, TRADE2_VARIANT_KIND, VariantConfig


def _game(seed=96_001):
    game = CatanGame(
        board_seed=seed,
        variant_config=VariantConfig.composite_events_economy(),
        headless=True,
    )
    game.configure_players(3, reset_logs=False, schedule_ai=False)
    game.start_main_phase()
    game.dice_rolled = True
    return game


def _grant(game, player, resource, amount=1):
    assert game.bank.withdraw(resource, amount)
    player.add_resource(resource, amount)


def _component_clocks(game):
    return {
        kind: game.get_variant_component_state(kind).public["completed_turns"]
        for kind in (
            FORECAST_EVENTS_KIND,
            TRADE2_VARIANT_KIND,
            CREDIT_VARIANT_KIND,
        )
    }


def test_composite_cross_feature_clock_save_snapshot_and_expiry():
    game = _game()
    restored = CatanGame(board_seed=96_002, headless=True)
    try:
        seller, bidder, _third = game.players
        _grant(game, seller, ResourceType.WOOD)
        _grant(game, seller, ResourceType.BRICK)
        _grant(game, bidder, ResourceType.ORE)
        _grant(game, bidder, ResourceType.SHEEP)

        assert game.borrow_resource_credit(seller, ResourceType.WHEAT)
        assert game.create_trade_market_order(
            seller,
            {ResourceType.WOOD: 1},
            {ResourceType.ORE: 1},
        )
        assert game.create_trade_auction(
            seller,
            {ResourceType.BRICK: 1},
            1,
        )
        auction = game.get_trade_auctions()[0]
        assert game.bid_trade_auction(
            bidder,
            auction.auction_id,
            auction.revision,
            {ResourceType.SHEEP: 1},
        )

        commands = {
            option["command"] for option in build_game_command_options(game, 0)
        }
        assert {"market_cancel", "auction_cancel"} <= commands
        assert game.get_resource_credit_loan(seller) is not None
        assert game.is_forecast_variant()

        snapshot = build_state_snapshot(game, viewer_player_index=0, revision=7)
        public_variant = snapshot["state"]["variant_state"]
        encoded = json.dumps(public_variant, ensure_ascii=False)
        assert public_variant["kind"] == "composite"
        assert "private" not in public_variant
        assert "deck_seed" not in encoded
        assert "next_sequence" not in encoded
        assert snapshot["state"]["players"][0]["resources"]["WOOD"] == 0
        view = parse_state_snapshot(snapshot)
        assert view.players[0].credit_vp_modifier == -1
        tampered_snapshot = copy.deepcopy(snapshot)
        tampered_credit = tampered_snapshot["state"]["variant_state"]["public"][
            "components"
        ][CREDIT_VARIANT_KIND]
        tampered_credit["completed_turns"] = 1
        with pytest.raises(NetworkViewError, match="component clock"):
            parse_state_snapshot(tampered_snapshot)
        replay_store = NetworkReplayStore(max_frames=2)
        replay_store.capture_game("COMP01", game, revision=7)
        replay_variant = replay_store.frame_payload(
            "COMP01",
            viewer_player_index=0,
            frame_index=0,
        )["snapshot"]["state"]["variant_state"]
        assert replay_variant == public_variant
        assert "private" not in json.dumps(replay_variant, ensure_ascii=False)

        saved = serialize_game(game)
        restore_game(restored, copy.deepcopy(saved), runtime_side_effects=False)
        assert restored.variant_config == game.variant_config
        assert restored.variant_state == game.variant_state
        assert len(restored.get_trade_market_orders()) == 1
        assert len(restored.get_trade_auctions()) == 1
        assert restored.get_resource_credit_loan(restored.players[0]) is not None
        # Headless presentation suppresses log output; capture it explicitly
        # so one-boundary side effects can be counted.
        restored.add_log = restored.log_messages.append

        for expected_clock in range(1, 5):
            previous = copy.deepcopy(restored.variant_state.to_document())
            restored.advance_variant_turn_boundary()
            assert restored.variant_state.public["completed_turns"] == expected_clock
            assert _component_clocks(restored) == {
                FORECAST_EVENTS_KIND: expected_clock,
                TRADE2_VARIANT_KIND: expected_clock,
                CREDIT_VARIANT_KIND: expected_clock,
            }
            # Every published state is strict; no sibling can remain at N.
            assert restored.variant_state.to_document() != previous

        assert restored.get_trade_market_orders() == ()
        assert restored.get_trade_auctions() == ()
        assert restored.players[0].reserved_resource_total() == 0
        assert restored.players[1].reserved_resource_total() == 0
        assert restored.get_resource_credit_loan(restored.players[0]).status == (
            "delinquent"
        )
        assert sum("常設市場: 出品1件が期限切れ" in line for line in restored.log_messages) == 1
        assert sum("公開競売: 1件が期限切れ" in line for line in restored.log_messages) == 1
        assert sum("ローンが延滞" in event.title for event in restored.match_metrics.important_events) == 1
    finally:
        game.audio.stop()
        restored.audio.stop()


def test_composite_headless_ai_can_use_combined_capabilities_without_stalling():
    result = run_match(
        match_seed=96_100,
        player_count=3,
        victory_target=6,
        max_turns=30,
        max_action_steps=4_000,
        variant_config=VariantConfig.composite_events_economy(),
    )

    assert result.reason in {"victory", "turn_limit"}
    assert result.reason != "stalled"
    assert result.action_steps < 4_000
    assert result.validation_errors == ()


def test_composite_boundary_rolls_back_all_escrow_before_clock_publication(
    monkeypatch,
):
    game = _game(seed=96_003)
    try:
        seller, bidder, _third = game.players
        _grant(game, seller, ResourceType.WOOD)
        _grant(game, seller, ResourceType.BRICK)
        _grant(game, bidder, ResourceType.SHEEP)
        assert game.create_trade_market_order(
            seller,
            {ResourceType.WOOD: 1},
            {ResourceType.ORE: 1},
        )
        assert game.create_trade_auction(
            seller,
            {ResourceType.BRICK: 1},
            1,
        )
        auction = game.get_trade_auctions()[0]
        assert game.bid_trade_auction(
            bidder,
            auction.auction_id,
            auction.revision,
            {ResourceType.SHEEP: 1},
        )
        for _ in range(3):
            game.advance_variant_turn_boundary()

        state_before = copy.deepcopy(game.variant_state.to_document())
        resources_before = [dict(player.resources) for player in game.players]
        ledgers_before = [
            player.resource_ledger.to_document() for player in game.players
        ]
        original_release = bidder.release_reserved_resources

        def release_then_report_failure(reservation_id):
            original_release(reservation_id)
            return {}

        monkeypatch.setattr(
            bidder,
            "release_reserved_resources",
            release_then_report_failure,
        )
        with pytest.raises(RuntimeError, match="予約解放"):
            game.advance_variant_turn_boundary()

        assert game.variant_state.to_document() == state_before
        assert [dict(player.resources) for player in game.players] == resources_before
        assert [
            player.resource_ledger.to_document() for player in game.players
        ] == ledgers_before
        assert game.variant_state.public["completed_turns"] == 3
    finally:
        game.audio.stop()
