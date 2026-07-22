import copy

import pytest

from game.game import CatanGame
from game.network_actions import (
    NetworkActionError,
    apply_game_command,
    build_game_command_options,
)
from game.persistence import SaveGameError, restore_game, serialize_game
from game.resources import ResourceType
from game.variant import VariantConfig
from game.variant_state import VariantState


def _game(*, seed=91_001, ai_players=0, ttl=4):
    game = CatanGame(
        board_seed=seed,
        variant_config=VariantConfig.trade2_auction(
            auction_ttl_turns=ttl,
        ),
        ai_player_count=ai_players,
        headless=True,
    )
    game.configure_players(3, reset_logs=False, schedule_ai=False)
    game.start_main_phase()
    game.dice_rolled = True
    return game


def _grant(game, player, resource, amount=1):
    assert game.bank.withdraw(resource, amount)
    player.add_resource(resource, amount)


def _open_auction(game, resource=ResourceType.WOOD):
    seller = game.players[0]
    _grant(game, seller, resource)
    assert game.create_trade_auction(seller, {resource: 1}, 1)
    return game.get_trade_auctions()[0]


def test_trade2_auction_config_keeps_old_catalog_compatible_and_sequence_private():
    old = VariantConfig.trade2()
    config = VariantConfig.trade2_auction(auction_ttl_turns=3)
    assert old.options["catalog"] == "standing_market_v1"
    assert config.to_document() == {
        "version": 1,
        "kind": "trade2",
        "options": {
            "catalog": "market_auction_v1",
            "order_ttl_turns": 4,
            "auction_ttl_turns": 3,
        },
    }
    state = VariantState.initial(config)
    assert state.public["auctions"] == ()
    public_document = state.to_public_document()
    assert "private" not in public_document
    assert "next_auction_sequence" not in str(public_document)


def test_public_auction_lifecycle_is_atomic_and_releases_losing_bid():
    game = _game()
    try:
        auction = _open_auction(game)
        seller, winner, loser = game.players
        _grant(game, winner, ResourceType.ORE)
        _grant(game, loser, ResourceType.SHEEP)
        assert game.bid_trade_auction(
            winner,
            auction.auction_id,
            auction.revision,
            {ResourceType.ORE: 1},
        )
        auction = game.get_trade_auctions()[0]
        assert game.bid_trade_auction(
            loser,
            auction.auction_id,
            auction.revision,
            {ResourceType.SHEEP: 1},
        )
        auction = game.get_trade_auctions()[0]
        assert winner.available_resource_total() == 0
        assert loser.available_resource_total() == 0

        assert game.accept_trade_auction(
            seller,
            auction.auction_id,
            auction.revision,
            1,
        )
        assert game.get_trade_auctions() == ()
        assert seller.resources[ResourceType.ORE] == 1
        assert winner.resources[ResourceType.WOOD] == 1
        assert loser.resources[ResourceType.SHEEP] == 1
        assert loser.reserved_resource_total() == 0
        assert game.match_metrics.player("seat-1").domestic_trades == 1
        assert game.match_metrics.player("seat-2").domestic_trades == 1
    finally:
        game.audio.stop()


def test_authenticated_non_active_player_can_bid_and_cancel_but_not_sell():
    game = _game(seed=91_002)
    try:
        auction = _open_auction(game)
        bidder = game.players[1]
        _grant(game, bidder, ResourceType.ORE)
        options = build_game_command_options(game, 1)
        assert [option["command"] for option in options] == ["auction_bid"]

        assert apply_game_command(
            game,
            1,
            "auction_bid",
            {
                "auction_id": auction.auction_id,
                "revision": auction.revision,
                "offer": {"ORE": 1},
            },
        )
        auction = game.get_trade_auctions()[0]
        commands = {
            option["command"] for option in build_game_command_options(game, 1)
        }
        assert commands == {"auction_bid", "auction_cancel_bid"}
        assert apply_game_command(
            game,
            1,
            "auction_cancel_bid",
            {
                "auction_id": auction.auction_id,
                "revision": auction.revision,
            },
        )
        assert bidder.available_resource_count(ResourceType.ORE) == 1
        with pytest.raises(NetworkActionError, match="現在"):
            apply_game_command(
                game,
                1,
                "auction_create",
                {"offer": {"ORE": 1}, "minimum_bid_cards": 1},
            )
    finally:
        game.audio.stop()


def test_active_and_non_active_players_cannot_bid_before_the_roll():
    game = _game(seed=91_007)
    try:
        auction = _open_auction(game)
        for player in game.players[:2]:
            _grant(game, player, ResourceType.ORE)
        game.dice_rolled = False

        assert [
            option["command"] for option in build_game_command_options(game, 0)
        ] == ["roll_dice"]
        assert build_game_command_options(game, 1) == []
        for seat_index in (0, 1):
            with pytest.raises(NetworkActionError):
                apply_game_command(
                    game,
                    seat_index,
                    "auction_bid",
                    {
                        "auction_id": auction.auction_id,
                        "revision": auction.revision,
                        "offer": {"ORE": 1},
                    },
                )
    finally:
        game.audio.stop()


def test_expiry_and_forced_loss_release_every_auction_reservation():
    expiry_game = _game(seed=91_003, ttl=1)
    forced_game = _game(seed=91_004)
    try:
        auction = _open_auction(expiry_game)
        bidder = expiry_game.players[1]
        _grant(expiry_game, bidder, ResourceType.ORE)
        assert expiry_game.bid_trade_auction(
            bidder,
            auction.auction_id,
            auction.revision,
            {ResourceType.ORE: 1},
        )
        expiry_game.advance_trade_market_turn()
        assert expiry_game.get_trade_auctions() == ()
        assert all(player.reserved_resource_total() == 0 for player in expiry_game.players)

        auction = _open_auction(forced_game)
        bidder = forced_game.players[1]
        _grant(forced_game, bidder, ResourceType.ORE)
        assert forced_game.bid_trade_auction(
            bidder,
            auction.auction_id,
            auction.revision,
            {ResourceType.ORE: 1},
        )
        assert forced_game.cancel_all_trade_market_orders(bidder, reason="略奪") == 1
        assert bidder.available_resource_count(ResourceType.ORE) == 1
        assert forced_game.get_trade_auctions()[0].bids == ()
    finally:
        expiry_game.audio.stop()
        forced_game.audio.stop()


def test_save_restore_cross_validates_all_auction_escrows():
    game = _game(seed=91_005)
    restored = _game(seed=1)
    try:
        auction = _open_auction(game)
        bidder = game.players[1]
        _grant(game, bidder, ResourceType.ORE)
        assert game.bid_trade_auction(
            bidder,
            auction.auction_id,
            auction.revision,
            {ResourceType.ORE: 1},
        )
        document = serialize_game(game)
        restore_game(restored, document, runtime_side_effects=False)
        assert len(restored.get_trade_auctions()[0].bids) == 1

        missing_escrow = copy.deepcopy(document)
        missing_escrow["players"][1].pop("resource_ledger")
        with pytest.raises(SaveGameError, match="市場注文と予約資源"):
            restore_game(restored, missing_escrow, runtime_side_effects=False)

        orphan_escrow = copy.deepcopy(document)
        orphan_escrow["variant_state"]["public"]["auctions"] = []
        with pytest.raises(SaveGameError, match="市場注文と予約資源"):
            restore_game(restored, orphan_escrow, runtime_side_effects=False)

        unknown_auction_reservation = copy.deepcopy(document)
        unknown_auction_reservation["players"][0]["resource_ledger"][
            "reservations"
        ][0]["id"] = "auction:orphan"
        with pytest.raises(SaveGameError, match="市場注文と予約資源"):
            restore_game(
                restored,
                unknown_auction_reservation,
                runtime_side_effects=False,
            )

        mismatched_ttl = copy.deepcopy(document)
        mismatched_ttl["variant_state"]["public"]["auctions"][0][
            "expires_turn"
        ] += 1
        with pytest.raises(SaveGameError, match="variant state"):
            restore_game(restored, mismatched_ttl, runtime_side_effects=False)
    finally:
        game.audio.stop()
        restored.audio.stop()


def test_ai_responds_to_a_useful_human_auction_without_hidden_information():
    game = _game(seed=91_006, ai_players=1)
    try:
        seller = game.players[0]
        ai = game.players[-1]
        _grant(game, seller, ResourceType.WHEAT)
        _grant(game, ai, ResourceType.SHEEP)
        assert game.create_trade_auction(seller, {ResourceType.WHEAT: 1}, 1)
        auction = game.get_trade_auctions()[0]
        ai_bid = auction.get_bid(game.players.index(ai))
        assert ai_bid is not None
        assert ai_bid.offer == {ResourceType.SHEEP: 1}
    finally:
        game.audio.stop()
