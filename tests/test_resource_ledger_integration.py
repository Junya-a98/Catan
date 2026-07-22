import copy
import json

import pytest

from game.bank import RESOURCE_TYPES
from game.building import Building
from game.game import CatanGame
from game.network_protocol import build_state_snapshot, encode_frame
from game.network_replay import NetworkReplayStore
from game.persistence import SaveGameError, restore_game, serialize_game
from game.resources import BUILD_COSTS, ResourceType


def _game(seed=73_119):
    game = CatanGame(
        board_seed=seed,
        ai_player_count=0,
        headless=True,
    )
    game.configure_players(2, reset_logs=False, schedule_ai=False)
    return game


def _grant(game, player, resource_type, amount):
    assert game.bank.withdraw(resource_type, amount)
    player.add_resource(resource_type, amount)


def _resource_bundle(**counts):
    return {
        resource: counts.get(resource.name, 0)
        for resource in RESOURCE_TYPES
    }


def test_reserved_cards_cannot_fund_build_bank_or_domestic_trade():
    game = _game()
    try:
        game.start_main_phase()
        game.dice_rolled = True
        player, partner = game.players
        _grant(game, player, ResourceType.WOOD, 4)
        _grant(game, player, ResourceType.BRICK, 1)
        _grant(game, partner, ResourceType.SHEEP, 1)

        # Give the player a real connection so affordability, rather than
        # topology, is what removes every paid-road candidate.
        anchor = next(node for node in game.board.nodes if node.building is None)
        anchor.building = Building(player)
        player.settlements_remaining -= 1
        assert game.get_buildable_road_edges(
            player,
            require_affordability=False,
        )

        assert player.reserve_resources(
            "market-wood",
            {ResourceType.WOOD: 4},
        )
        assert player.total_resource_count() == 5
        assert player.available_resource_count(ResourceType.WOOD) == 0
        assert not player.can_afford(BUILD_COSTS["road"])
        assert game.get_buildable_road_edges(player) == []

        bank_before = dict(game.bank.resources)
        assert not game.has_bank_trade_option(player)
        game.start_bank_trade()
        assert game.special_phase is None
        assert game.bank.resources == bank_before

        game.domestic_trade_partner = partner
        game.domestic_trade_give = _resource_bundle(WOOD=1)
        game.domestic_trade_receive = _resource_bundle(SHEEP=1)
        game.domestic_trade_receive_operator = "and"
        assert not game.player_can_pay_bundle(
            player,
            game.domestic_trade_give,
        )
        assert not game.can_execute_domestic_trade()
        assert not game.execute_domestic_trade()
        assert player.resources[ResourceType.WOOD] == 4
        assert partner.resources[ResourceType.SHEEP] == 1
    finally:
        game.audio.stop()


def test_seven_counts_total_and_discard_cancels_reservations_by_sorted_id():
    game = _game(seed=73_120)
    try:
        game.start_main_phase()
        player = game.get_current_player()
        _grant(game, player, ResourceType.WOOD, 8)

        # Insert in reverse order.  Forced losses must still cancel a-first
        # before z-last once the two unreserved cards have been exhausted.
        assert player.reserve_resources(
            "z-last",
            {ResourceType.WOOD: 4},
        )
        assert player.reserve_resources(
            "a-first",
            {ResourceType.WOOD: 2},
        )
        assert player.total_resource_count() == 8
        assert player.available_resource_total() == 2

        game.start_robber_phase(with_discard=True)

        assert game.discard_player is player
        assert game.discard_remaining == 4
        assert game.special_phase == "discard"

        for _ in range(3):
            game.discard_resource(ResourceType.WOOD)

        assert player.resource_ledger.reservations_map() == {
            "z-last": {ResourceType.WOOD: 4},
        }
        game.discard_resource(ResourceType.WOOD)

        assert player.total_resource_count() == 4
        assert player.available_resource_total() == 0
        assert player.resource_ledger.reservations_map() == {
            "z-last": {ResourceType.WOOD: 4},
        }
        assert game.bank.available(ResourceType.WOOD) == 15
        assert game.discard_remaining == 0
    finally:
        game.audio.stop()


def test_robbery_and_monopoly_take_reserved_cards_from_total_ownership():
    game = _game(seed=73_121)
    try:
        game.start_main_phase()
        thief, victim = game.players

        _grant(game, victim, ResourceType.WOOD, 1)
        assert victim.reserve_resources(
            "robbery-target",
            {ResourceType.WOOD: 1},
        )

        assert game.steal_random_resource(victim) is ResourceType.WOOD
        assert victim.resources[ResourceType.WOOD] == 0
        assert not victim.resource_ledger.has_reservations
        assert thief.resources[ResourceType.WOOD] == 1

        _grant(game, victim, ResourceType.SHEEP, 3)
        assert victim.reserve_resources(
            "z-monopoly",
            {ResourceType.SHEEP: 2},
        )
        assert victim.reserve_resources(
            "a-monopoly",
            {ResourceType.SHEEP: 1},
        )
        assert victim.available_resource_count(ResourceType.SHEEP) == 0

        game.special_phase = "monopoly"
        game.handle_resource_selection(ResourceType.SHEEP)

        assert game.special_phase is None
        assert victim.resources[ResourceType.SHEEP] == 0
        assert not victim.resource_ledger.has_reservations
        assert thief.resources[ResourceType.SHEEP] == 3
    finally:
        game.audio.stop()


def test_nonempty_ledger_save_round_trip_and_legacy_field_absence():
    game = _game(seed=73_122)
    restored = _game(seed=1)
    legacy_restored = _game(seed=2)
    try:
        player = game.players[0]
        _grant(game, player, ResourceType.WOOD, 3)
        _grant(game, player, ResourceType.SHEEP, 2)
        assert player.reserve_resources(
            "z-offer",
            {ResourceType.SHEEP: 1},
        )
        assert player.reserve_resources(
            "a-offer",
            {ResourceType.WOOD: 2},
        )

        saved = serialize_game(game)
        encoded = json.loads(json.dumps(saved, allow_nan=False))
        assert encoded["players"][0]["resource_ledger"]["reservations"] == [
            {"id": "a-offer", "bundle": {"WOOD": 2}},
            {"id": "z-offer", "bundle": {"SHEEP": 1}},
        ]

        restore_game(restored, copy.deepcopy(encoded), runtime_side_effects=False)
        assert serialize_game(restored) == saved
        assert restored.players[0].resource_ledger.reservations_map() == (
            player.resource_ledger.reservations_map()
        )

        legacy = copy.deepcopy(encoded)
        legacy["players"][0].pop("resource_ledger")
        restore_game(
            legacy_restored,
            legacy,
            runtime_side_effects=False,
        )
        assert not legacy_restored.players[0].resource_ledger.has_reservations
        assert legacy_restored.players[0].resources == player.resources
        assert "resource_ledger" not in serialize_game(legacy_restored)["players"][0]
    finally:
        game.audio.stop()
        restored.audio.stop()
        legacy_restored.audio.stop()


def test_restore_rejects_reservations_greater_than_total_owned_cards():
    game = _game(seed=73_123)
    target = _game(seed=3)
    try:
        player = game.players[0]
        _grant(game, player, ResourceType.ORE, 2)
        assert player.reserve_resources(
            "city-stock",
            {ResourceType.ORE: 1},
        )
        tampered = serialize_game(game)
        tampered["players"][0]["resource_ledger"]["reservations"][0][
            "bundle"
        ]["ORE"] = 3

        with pytest.raises(SaveGameError, match="資源予約"):
            restore_game(target, tampered, runtime_side_effects=False)
    finally:
        game.audio.stop()
        target.audio.stop()


def test_network_and_replay_hide_ledger_and_ids_but_keep_total_hand_count():
    game = _game(seed=73_124)
    try:
        owner = game.players[0]
        _grant(game, owner, ResourceType.WOOD, 3)
        _grant(game, owner, ResourceType.SHEEP, 1)
        secret_id = "private-reservation-42"
        assert owner.reserve_resources(
            secret_id,
            {ResourceType.WOOD: 2, ResourceType.SHEEP: 1},
        )

        snapshots = [
            build_state_snapshot(game, viewer_player_index=0, revision=5),
            build_state_snapshot(game, viewer_player_index=1, revision=5),
            build_state_snapshot(game, viewer_player_index=None, revision=5),
        ]
        own, opponent, spectator = snapshots
        assert own["state"]["players"][0]["resources"] == {
            "WOOD": 3,
            "SHEEP": 1,
            "WHEAT": 0,
            "BRICK": 0,
            "ORE": 0,
        }
        assert opponent["state"]["players"][0]["resources"] is None
        assert spectator["state"]["players"][0]["resources"] is None
        assert [
            snapshot["state"]["players"][0]["resource_total"]
            for snapshot in snapshots
        ] == [4, 4, 4]

        replay = NetworkReplayStore()
        replay.capture_game("LEDGER1", game, revision=5)
        replay_frames = [
            replay.frame_payload(
                "LEDGER1",
                viewer_player_index=viewer,
                frame_index=0,
            )
            for viewer in (0, 1, None)
        ]

        for document in (*snapshots, *replay_frames):
            encoded = encode_frame(document)
            assert b"resource_ledger" not in encoded
            assert secret_id.encode("ascii") not in encoded
    finally:
        game.audio.stop()


def test_ordinary_save_omits_empty_ledger_field():
    game = _game(seed=73_125)
    try:
        _grant(game, game.players[0], ResourceType.BRICK, 1)
        document = serialize_game(game)

        assert all(
            "resource_ledger" not in player
            for player in document["players"]
        )
    finally:
        game.audio.stop()
