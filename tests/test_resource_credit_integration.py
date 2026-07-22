import copy

import pytest

from game.building import Building, BuildingType
from game.game import CatanGame
from game.match_result import build_match_result
from game.network_actions import (
    NetworkActionError,
    apply_game_command,
    build_game_command_options,
)
from game.network_protocol import build_state_snapshot
from game.network_replay import NetworkReplayStore
from game.network_view import NetworkViewError, parse_state_snapshot
from game.persistence import restore_game, serialize_game
from game.resources import ResourceType
from game.variant import VariantConfig


def _game(*, seed=93_001, ai_players=0):
    game = CatanGame(
        board_seed=seed,
        variant_config=VariantConfig.credit(),
        ai_player_count=ai_players,
        headless=True,
    )
    game.ai_player_count = ai_players
    game.configure_players(3, reset_logs=False, schedule_ai=False)
    game.start_main_phase()
    game.dice_rolled = True
    return game


def _grant(game, player, resource, amount=1):
    assert game.bank.withdraw(resource, amount)
    player.add_resource(resource, amount)


def _give_public_points(game, player, count=2):
    nodes = [node for node in game.board.nodes if node.building is None][:count]
    for node in nodes:
        node.building = Building(player, BuildingType.SETTLEMENT)


def test_borrow_and_active_repayment_are_atomic_and_one_action_per_turn():
    game = _game()
    try:
        player = game.players[0]
        _give_public_points(game, player)
        assert game.borrow_resource_credit(player, ResourceType.WOOD)
        loan = game.get_resource_credit_loan(player)
        assert loan.borrowed_resource is ResourceType.WOOD
        assert loan.due_turn == 3
        assert player.resources[ResourceType.WOOD] == 1
        assert game.bank.available(ResourceType.WOOD) == 18
        assert game.get_player_public_victory_points(player) == 1
        assert game.match_metrics.important_events[-1].title.endswith("資源を借入")
        assert game.credit_action_taken_this_turn is True
        assert not game.can_repay_resource_credit(player)

        game.reset_turn_state()
        game.dice_rolled = True
        _grant(game, player, ResourceType.SHEEP)
        assert game.repay_resource_credit(
            player,
            loan.loan_id,
            loan.revision,
            {ResourceType.WOOD: 1, ResourceType.SHEEP: 1},
        )
        assert game.get_resource_credit_loan(player) is None
        assert player.total_resource_count() == 0
        assert game.bank.available(ResourceType.WOOD) == 19
        assert game.bank.available(ResourceType.SHEEP) == 19
        assert game.get_player_public_victory_points(player) == 2
        assert game.match_metrics.important_events[-1].title.endswith("ローンを返済")
        assert game.credit_action_taken_this_turn is True
        assert not game.can_borrow_resource_credit(player, ResourceType.ORE)
    finally:
        game.audio.stop()


def test_due_turn_remains_repayable_until_own_end_boundary_then_delinquent():
    game = _game(seed=93_002)
    try:
        player = game.players[0]
        _give_public_points(game, player)
        assert game.borrow_resource_credit(player, ResourceType.WHEAT)
        for expected in (1, 2, 3):
            updated = game.advance_resource_credit_turn()
            assert updated == ()
            assert game.variant_state.public["completed_turns"] == expected
            assert game.get_resource_credit_loan(player).status == "active"

        game.credit_action_taken_this_turn = False
        game.dice_rolled = True
        _grant(game, player, ResourceType.ORE)
        loan = game.get_resource_credit_loan(player)
        assert game.can_repay_resource_credit(
            player,
            {ResourceType.WHEAT: 1, ResourceType.ORE: 1},
        )

        # If the borrower ends that due turn without paying, the next boundary
        # converts once to a three-card generic debt and never forces cards.
        player.remove_resource(ResourceType.ORE)
        game.bank.deposit(ResourceType.ORE)
        updated = game.advance_resource_credit_turn()
        assert len(updated) == 1
        loan = game.get_resource_credit_loan(player)
        assert loan.status == "delinquent"
        assert loan.remaining_cards == 3
        assert game.get_player_public_victory_points(player) == 0
        assert game.match_metrics.important_events[-1].title.endswith("ローンが延滞")
        assert game.advance_resource_credit_turn() == ()
        assert game.get_resource_credit_loan(player).revision == loan.revision
    finally:
        game.audio.stop()


def test_end_turn_boundary_advances_credit_and_marks_overdue_after_next_own_turn():
    game = _game(seed=93_010, ai_players=2)
    try:
        borrower = game.players[0]
        assert game.borrow_resource_credit(borrower, ResourceType.WHEAT)
        for expected in (1, 2, 3):
            game.dice_rolled = True
            game.finish_current_turn()
            assert game.variant_state.public["completed_turns"] == expected
            assert game.get_resource_credit_loan(borrower).status == "active"
        assert game.get_current_player() is borrower

        game.dice_rolled = True
        game.finish_current_turn()
        assert game.variant_state.public["completed_turns"] == 4
        assert game.get_resource_credit_loan(borrower).status == "delinquent"
    finally:
        game.audio.stop()


def test_delinquent_partial_repayment_uses_only_available_unreserved_cards():
    game = _game(seed=93_003)
    try:
        player = game.players[0]
        assert game.borrow_resource_credit(player, ResourceType.BRICK)
        for _ in range(4):
            game.advance_resource_credit_turn()
        game.credit_action_taken_this_turn = False
        game.dice_rolled = True
        _grant(game, player, ResourceType.SHEEP, 2)
        loan = game.get_resource_credit_loan(player)
        assert game.repay_resource_credit(
            player,
            loan.loan_id,
            loan.revision,
            {ResourceType.SHEEP: 2},
        )
        loan = game.get_resource_credit_loan(player)
        assert loan.remaining_cards == 1
        assert loan.revision == 3
        assert game.get_credit_vp_modifier(player) == -2
    finally:
        game.audio.stop()


def test_repayment_rejects_reserved_cards_and_spends_only_available_cards():
    game = _game(seed=93_012)
    try:
        player = game.players[0]
        assert game.borrow_resource_credit(player, ResourceType.WOOD)
        game.reset_turn_state()
        game.dice_rolled = True
        _grant(game, player, ResourceType.SHEEP, 2)
        reservation_id = "credit-test:escrow"
        assert player.reserve_resources(
            reservation_id,
            {ResourceType.SHEEP: 2},
        )
        loan = game.get_resource_credit_loan(player)
        payment = {ResourceType.WOOD: 1, ResourceType.SHEEP: 1}
        state_before = copy.deepcopy(game.variant_state.to_document())
        resources_before = dict(player.resources)
        ledger_before = player.resource_ledger.to_document()

        assert not game.can_repay_resource_credit(player, payment)
        assert not game.repay_resource_credit(
            player,
            loan.loan_id,
            loan.revision,
            payment,
        )
        assert game.variant_state.to_document() == state_before
        assert player.resources == resources_before
        assert player.resource_ledger.to_document() == ledger_before

        assert player.resource_ledger.replace(
            reservation_id,
            {ResourceType.SHEEP: 1},
        )
        assert game.can_repay_resource_credit(player, payment)
        assert game.repay_resource_credit(
            player,
            loan.loan_id,
            loan.revision,
            payment,
        )
        assert game.get_resource_credit_loan(player) is None
        assert player.resources[ResourceType.SHEEP] == 1
        assert player.resource_ledger.reservations_map() == {
            reservation_id: {ResourceType.SHEEP: 1}
        }
    finally:
        game.audio.stop()


def test_borrow_rolls_back_bank_player_and_credit_state_after_partial_failure(
    monkeypatch,
):
    game = _game(seed=93_013)
    try:
        player = game.players[0]
        state_before = copy.deepcopy(game.variant_state.to_document())
        bank_before = dict(game.bank.resources)
        resources_before = dict(player.resources)
        ledger_before = player.resource_ledger.to_document()
        original_add = player.add_resource

        def add_then_fail(resource_type, amount=1):
            original_add(resource_type, amount)
            raise RuntimeError("injected borrower write failure")

        monkeypatch.setattr(player, "add_resource", add_then_fail)
        assert not game.borrow_resource_credit(player, ResourceType.ORE)
        assert game.variant_state.to_document() == state_before
        assert game.bank.resources == bank_before
        assert player.resources == resources_before
        assert player.resource_ledger.to_document() == ledger_before
        assert game.credit_action_taken_this_turn is False
    finally:
        game.audio.stop()


def test_repayment_rolls_back_bank_player_and_credit_state_after_partial_failure(
    monkeypatch,
):
    game = _game(seed=93_014)
    try:
        player = game.players[0]
        assert game.borrow_resource_credit(player, ResourceType.WOOD)
        game.reset_turn_state()
        game.dice_rolled = True
        _grant(game, player, ResourceType.SHEEP)
        loan = game.get_resource_credit_loan(player)
        state_before = copy.deepcopy(game.variant_state.to_document())
        bank_before = dict(game.bank.resources)
        resources_before = dict(player.resources)
        ledger_before = player.resource_ledger.to_document()
        original_deposit = game.bank.deposit_cost

        def deposit_then_fail(cost):
            original_deposit(cost)
            raise RuntimeError("injected bank write failure")

        monkeypatch.setattr(game.bank, "deposit_cost", deposit_then_fail)
        assert not game.repay_resource_credit(
            player,
            loan.loan_id,
            loan.revision,
            {ResourceType.WOOD: 1, ResourceType.SHEEP: 1},
        )
        assert game.variant_state.to_document() == state_before
        assert game.bank.resources == bank_before
        assert player.resources == resources_before
        assert player.resource_ledger.to_document() == ledger_before
        assert game.credit_action_taken_this_turn is False
    finally:
        game.audio.stop()


def test_network_credit_commands_are_active_seat_only_and_revision_bound():
    game = _game(seed=93_004)
    try:
        borrow_options = [
            option
            for option in build_game_command_options(game, 0)
            if option["command"] == "credit_borrow"
        ]
        assert [option["args"]["resource"] for option in borrow_options] == [
            resource.name for resource in ResourceType if resource is not ResourceType.DESERT
        ]
        with pytest.raises(NetworkActionError) as off_turn:
            apply_game_command(
                game,
                1,
                "credit_borrow",
                {"resource": "WOOD"},
            )
        assert off_turn.value.code == "not_active_player"

        assert apply_game_command(
            game,
            0,
            "credit_borrow",
            {"resource": "WOOD"},
        )
        loan = game.get_resource_credit_loan(game.players[0])
        game.reset_turn_state()
        game.dice_rolled = True
        _grant(game, game.players[0], ResourceType.SHEEP)
        repay = [
            option
            for option in build_game_command_options(game, 0)
            if option["command"] == "credit_repay"
        ]
        assert repay == [
            {
                "command": "credit_repay",
                "args": {"loan_id": loan.loan_id, "revision": loan.revision},
            }
        ]
        before = copy.deepcopy(game.variant_state.to_document())
        with pytest.raises(NetworkActionError):
            apply_game_command(
                game,
                0,
                "credit_repay",
                {
                    "loan_id": loan.loan_id,
                    "revision": loan.revision + 1,
                    "payment": {"WOOD": 1, "SHEEP": 1},
                },
            )
        assert game.variant_state.to_document() == before
        assert apply_game_command(
            game,
            0,
            "credit_repay",
            {
                "loan_id": loan.loan_id,
                "revision": loan.revision,
                "payment": {"WOOD": 1, "SHEEP": 1},
            },
        )
    finally:
        game.audio.stop()


def test_credit_rejects_pre_roll_dice_animation_and_ai_seat_spoof():
    game = _game(seed=93_008)
    ai_game = _game(seed=93_009, ai_players=2)
    try:
        game.dice_rolled = False
        assert not any(
            option["command"].startswith("credit_")
            for option in build_game_command_options(game, 0)
        )
        with pytest.raises(NetworkActionError):
            apply_game_command(
                game,
                0,
                "credit_borrow",
                {"resource": "WOOD"},
            )

        game.dice_rolled = True
        game.has_active_dice_animation = lambda: True
        assert not any(
            option["command"].startswith("credit_")
            for option in build_game_command_options(game, 0)
        )
        with pytest.raises(NetworkActionError):
            apply_game_command(
                game,
                0,
                "credit_borrow",
                {"resource": "WOOD"},
            )

        ai_game.current_player_index = 1
        with pytest.raises(NetworkActionError) as spoof:
            apply_game_command(
                ai_game,
                1,
                "credit_borrow",
                {"resource": "WOOD"},
            )
        assert spoof.value.code == "seat_not_controllable"
    finally:
        game.audio.stop()
        ai_game.audio.stop()


def test_network_view_applies_public_penalty_without_revealing_vp_cards():
    game = _game(seed=93_005)
    try:
        player = game.players[0]
        _give_public_points(game, player)
        player.victory_point_cards = 2
        assert game.borrow_resource_credit(player, ResourceType.ORE)
        snapshot = build_state_snapshot(
            game,
            viewer_player_index=1,
            revision=4,
        )
        view = parse_state_snapshot(snapshot)
        assert view.players[0].public_vp == 1
        assert view.players[0].credit_vp_modifier == -1
        assert view.players[0].victory_point_cards is None

        tampered = copy.deepcopy(snapshot)
        tampered["state"]["variant_state"]["public"]["loans"].append(
            copy.deepcopy(
                tampered["state"]["variant_state"]["public"]["loans"][0]
            )
        )
        with pytest.raises(NetworkViewError, match="sorted unique|duplicate"):
            parse_state_snapshot(tampered)

        wrong_term = copy.deepcopy(snapshot)
        wrong_loan = wrong_term["state"]["variant_state"]["public"]["loans"][0]
        wrong_loan["due_turn"] = wrong_loan["opened_turn"] + 2
        with pytest.raises(NetworkViewError, match="player count"):
            parse_state_snapshot(wrong_term)

        expired_active = copy.deepcopy(snapshot)
        expired_public = expired_active["state"]["variant_state"]["public"]
        expired_public["completed_turns"] = expired_public["loans"][0]["due_turn"] + 1
        with pytest.raises(NetworkViewError, match="active.*past"):
            parse_state_snapshot(expired_active)

        early_delinquent = copy.deepcopy(snapshot)
        early_loan = early_delinquent["state"]["variant_state"]["public"]["loans"][0]
        early_loan["status"] = "delinquent"
        early_loan["remaining_cards"] = 3
        with pytest.raises(NetworkViewError, match="delinquent.*not past"):
            parse_state_snapshot(early_delinquent)
    finally:
        game.audio.stop()


@pytest.mark.parametrize(
    ("expected_status", "completed_turns"),
    (("active", 0), ("delinquent", 4)),
)
def test_active_and_delinquent_loans_round_trip_through_full_save_restore(
    expected_status,
    completed_turns,
):
    game = _game(seed=93_015 + completed_turns, ai_players=2)
    restored = CatanGame(board_seed=94_015 + completed_turns, headless=True)
    try:
        player = game.players[0]
        assert game.borrow_resource_credit(player, ResourceType.BRICK)
        for _ in range(completed_turns):
            game.dice_rolled = True
            game.finish_current_turn()
        document = serialize_game(game)

        restore_game(restored, copy.deepcopy(document), runtime_side_effects=False)

        assert restored.variant_config == game.variant_config
        assert restored.variant_state.to_document() == game.variant_state.to_document()
        restored_loan = restored.get_resource_credit_loan(restored.players[0])
        assert restored_loan is not None
        assert restored_loan.status == expected_status
        assert restored.bank.resources == game.bank.resources
        assert restored.players[0].resources == game.players[0].resources
        assert (
            restored.credit_action_taken_this_turn
            is game.credit_action_taken_this_turn
        )
    finally:
        game.audio.stop()
        restored.audio.stop()


def test_network_replay_exposes_public_debt_without_authority_private_state():
    game = _game(seed=93_020)
    try:
        borrower = game.players[0]
        assert game.borrow_resource_credit(borrower, ResourceType.ORE)
        loan_document = game.get_resource_credit_loan(borrower).to_document()
        store = NetworkReplayStore()
        store.capture_game("CREDIT1", game, revision=7, label="資源を借入")

        spectator = store.frame_payload(
            "CREDIT1",
            viewer_player_index=None,
            frame_index=0,
        )["snapshot"]
        variant_state = spectator["state"]["variant_state"]
        assert set(variant_state) == {
            "format",
            "version",
            "kind",
            "config_fingerprint",
            "public",
        }
        assert variant_state["public"]["loans"] == [loan_document]
        assert "private" not in variant_state
        assert "next_sequence" not in repr(variant_state)
        assert all(
            player["resources"] is None
            and "resource_ledger" not in player
            for player in spectator["state"]["players"]
        )

        borrower_frame = store.frame_payload(
            "CREDIT1",
            viewer_player_index=0,
            frame_index=0,
        )["snapshot"]
        assert borrower_frame["state"]["players"][0]["resources"]["ORE"] == 1
        assert borrower_frame["state"]["players"][1]["resources"] is None
        assert borrower_frame["command_options"] == []
    finally:
        game.audio.stop()


def test_result_breakdown_separates_public_debt_from_secret_vp_cards():
    game = _game(seed=93_006)
    try:
        player = game.players[0]
        _give_public_points(game, player)
        player.victory_point_cards = 2
        assert game.borrow_resource_credit(player, ResourceType.WOOD)
        game.winner = player
        game.phase = "finished"
        result = build_match_result(game)
        row = next(item for item in result["standings"] if item["seat"] == 1)
        assert row["victory_points"] == 3
        assert row["vp_breakdown"]["victory_point_cards"] == {
            "count": 2,
            "points": 2,
        }
        assert row["vp_breakdown"]["debt_penalty"] == {
            "count": 1,
            "status": "active",
            "points": -1,
        }
        assert row["vp_breakdown"]["total"] == 3

        replay_result = build_match_result(
            {
                "frames": [
                    {
                        "index": 0,
                        "timestamp_ms": 0,
                        "label": "Hostの勝利",
                        "snapshot": serialize_game(game),
                    }
                ]
            }
        )
        replay_row = next(
            item for item in replay_result["standings"] if item["seat"] == 1
        )
        assert replay_row["vp_breakdown"]["debt_penalty"]["points"] == -1
        assert replay_row["vp_breakdown"]["victory_point_cards"]["count"] == 2
    finally:
        game.audio.stop()


@pytest.mark.parametrize(
    "variant_config",
    (
        VariantConfig.composite_events_economy(),
        VariantConfig.composite_grand_campaign(),
    ),
    ids=("events_economy_v1", "grand_campaign_v1"),
)
def test_composite_result_and_replay_include_public_credit_penalty(
    variant_config,
):
    game = CatanGame(
        board_seed=93_008,
        variant_config=variant_config,
        headless=True,
    )
    game.configure_players(3, reset_logs=False, schedule_ai=False)
    game.start_main_phase()
    game.dice_rolled = True
    try:
        player = game.players[0]
        _give_public_points(game, player)
        player.victory_point_cards = 2
        assert game.borrow_resource_credit(player, ResourceType.WOOD)
        game.winner = player
        game.phase = "finished"

        live_result = build_match_result(game)
        live_row = next(
            item for item in live_result["standings"] if item["seat"] == 1
        )
        assert live_row["vp_breakdown"]["debt_penalty"] == {
            "count": 1,
            "status": "active",
            "points": -1,
        }
        assert live_row["vp_breakdown"]["total"] == 3

        authority_snapshot = serialize_game(game)
        public_snapshot = build_state_snapshot(
            game,
            viewer_player_index=0,
        )["state"]
        assert "private" in authority_snapshot["variant_state"]
        assert "private" not in public_snapshot["variant_state"]
        for replay_snapshot in (authority_snapshot, public_snapshot):
            replay_result = build_match_result(
                {
                    "frames": [
                        {
                            "sequence": 0,
                            "elapsed_ms": 0,
                            "label": "Hostの勝利",
                            "snapshot": replay_snapshot,
                        }
                    ]
                }
            )
            replay_row = next(
                item for item in replay_result["standings"] if item["seat"] == 1
            )
            assert replay_row["vp_breakdown"] == live_row["vp_breakdown"]
            assert replay_row["victory_points"] == 3
            assert "next_sequence" not in repr(replay_result)
    finally:
        game.audio.stop()


def test_ai_prioritises_repayment_and_does_not_credit_loop():
    game = _game(seed=93_007, ai_players=2)
    try:
        ai = game.players[1]
        game.current_player_index = 1
        assert game.get_current_player() is ai
        assert game.borrow_resource_credit(ai, ResourceType.WOOD)
        game.reset_turn_state()
        game.dice_rolled = True
        _grant(game, ai, ResourceType.SHEEP)
        assert game.ai.step(game)
        assert game.get_resource_credit_loan(ai) is None
        assert game.credit_action_taken_this_turn is True
        assert not game.can_borrow_resource_credit(ai)
        assert game.ai_status["title"] == "ローンを優先返済"
    finally:
        game.audio.stop()


def test_ai_borrows_only_one_missing_goal_card_and_avoids_near_win_penalty():
    game = _game(seed=93_011, ai_players=2)
    try:
        ai = game.players[1]
        game.current_player_index = 1
        for resource in (
            ResourceType.SHEEP,
            ResourceType.WHEAT,
            ResourceType.BRICK,
        ):
            _grant(game, ai, resource)
        assert game.ai._choose_resource_credit_borrow(game, ai) is ResourceType.WOOD

        game.victory_point_target = 2
        assert game.ai._choose_resource_credit_borrow(game, ai) is None
    finally:
        game.audio.stop()
