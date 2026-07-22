import copy
from types import SimpleNamespace

import pytest

from game.game import CatanGame
from game.persistence import (
    SaveGameError,
    _validate_restored_resource_credit,
    restore_game,
    serialize_game,
)
from game.resource_credit import LOAN_ACTIVE, LOAN_DELINQUENT
from game.resources import ResourceType
from game.variant import CREDIT_CATALOG, VariantConfig
from game.variant_state import VariantState, VariantStateError


def _borrow(
    state: VariantState,
    config: VariantConfig,
    *,
    borrower_index: int = 0,
    player_count: int = 2,
) -> VariantState:
    book = state.credit_book()
    plan = book.plan_borrow(
        borrower_index=borrower_index,
        borrowed_resource=ResourceType.WOOD,
        current_turn=state.public["completed_turns"],
        player_count=player_count,
    )
    state, result = state.apply_credit_plan(config, plan)
    assert result.created_loan is not None
    return state


def test_credit_config_is_exact_and_existing_fingerprints_stay_stable():
    config = VariantConfig.credit()

    assert config.to_document() == {
        "version": 1,
        "kind": "credit",
        "options": {"catalog": CREDIT_CATALOG},
    }
    assert VariantConfig.from_document(config.to_document()) == config
    assert {
        "standard": VariantConfig.standard().fingerprint(),
        "forecast": VariantConfig.forecast_events().fingerprint(),
        "frontier": VariantConfig.frontier().fingerprint(),
        "frontier_expanded": VariantConfig.frontier_expanded().fingerprint(),
        "trade2": VariantConfig.trade2().fingerprint(),
        "trade2_auction": VariantConfig.trade2_auction().fingerprint(),
    } == {
        "standard": "04cc11be5f8e64f5b1c17c42a3a00de97d9d44d7fcf8bf185d2c3a291cd9e4ee",
        "forecast": "c01a92ee5fa882815ba2a210d25c5501a3a828ae025ffffa85116a006bbe6091",
        "frontier": "778efcae7452dec555ac4a07dcf33874774fbd92e123d221736f7d058d9797c2",
        "frontier_expanded": "a1d838b56dbc374df804574d2adc41dc86db8df73860239cab876d299ebf29dc",
        "trade2": "efdd0b3659b1ec54a1d8acde2d2835a3ccc265bdcfc447000a50556f7385a7ee",
        "trade2_auction": "b3a134ead313c04e67395d7fc473599d63f000131707a0ac65a37df2429f5619",
    }

    for options in ({}, {"catalog": "future"}, {"catalog": CREDIT_CATALOG, "x": 1}):
        with pytest.raises(ValueError, match="credit"):
            VariantConfig(kind="credit", options=options)


def test_credit_state_keeps_sequence_private_and_round_trips_strictly():
    config = VariantConfig.credit()
    state = VariantState.initial(config)

    assert state.public == {
        "catalog": CREDIT_CATALOG,
        "completed_turns": 0,
        "loans": (),
    }
    assert state.private == {"next_sequence": 0}
    assert "private" not in state.to_public_document()
    assert "next_sequence" not in str(state.to_public_document())
    assert VariantState.from_document(state.to_document(), config=config) == state

    projection = VariantState.from_public_document(
        state.to_public_document(),
        config=config,
    )
    assert projection.to_public_document() == state.to_public_document()
    with pytest.raises(VariantStateError, match="完全保存"):
        projection.to_document()

    for section, key, value in (
        ("public", "unexpected", True),
        ("private", "unexpected", True),
        ("private", "next_sequence", True),
    ):
        malformed = state.to_document()
        malformed[section][key] = value
        with pytest.raises(VariantStateError, match="credit"):
            VariantState.from_document(malformed, config=config)


def test_credit_adapter_is_clock_bound_and_hides_monotonic_sequence():
    config = VariantConfig.credit()
    state = VariantState.initial(config)
    stale_clock_plan = state.credit_book().plan_borrow(
        borrower_index=0,
        borrowed_resource=ResourceType.ORE,
        current_turn=1,
        player_count=2,
    )
    with pytest.raises(VariantStateError, match="完了手番"):
        state.apply_credit_plan(config, stale_clock_plan)

    state = _borrow(state, config)
    loan = state.credit_book().open_loans[0]
    assert loan.status == LOAN_ACTIVE
    assert state.private == {"next_sequence": 1}
    assert state.to_public_document()["public"]["loans"] == [loan.to_document()]
    assert "next_sequence" not in str(state.to_public_document())

    forged = state.to_document()
    forged["private"]["next_sequence"] = 0
    with pytest.raises(VariantStateError, match="credit"):
        VariantState.from_document(forged, config=config)


def test_due_loan_stays_active_during_next_own_turn_then_becomes_delinquent():
    config = VariantConfig.credit()
    state = _borrow(VariantState.initial(config), config, player_count=2)
    loan = state.credit_book().open_loans[0]
    assert loan.due_turn == 2

    state, first = state.advance_credit_turn(config)
    assert state.public["completed_turns"] == 1
    assert first.updated_loans == ()
    state, second = state.advance_credit_turn(config)
    assert state.public["completed_turns"] == loan.due_turn
    assert second.updated_loans == ()
    assert state.credit_book().open_loans[0].status == LOAN_ACTIVE

    due_plan = state.credit_book().plan_repay(
        borrower_index=0,
        loan_id=loan.loan_id,
        expected_revision=loan.revision,
        payment={ResourceType.WOOD: 2},
        current_turn=loan.due_turn,
    )
    repaid_state, repaid = state.apply_credit_plan(config, due_plan)
    assert repaid.removed_loans == (loan,)
    assert repaid_state.credit_book().open_loans == ()

    state, expired = state.advance_credit_turn(config)
    assert state.public["completed_turns"] == loan.due_turn + 1
    assert len(expired.updated_loans) == 1
    assert state.credit_book().open_loans[0].status == LOAN_DELINQUENT


def test_public_loan_clock_and_match_seat_cross_validation_are_strict():
    config = VariantConfig.credit()
    state = _borrow(
        VariantState.initial(config),
        config,
        borrower_index=2,
        player_count=3,
    )
    dummy = SimpleNamespace(
        variant_config=config,
        variant_state=state,
        players=[object(), object()],
    )

    with pytest.raises(SaveGameError, match="参加席"):
        _validate_restored_resource_credit(dummy)

    wrong_term = _borrow(
        VariantState.initial(config),
        config,
        borrower_index=0,
        player_count=3,
    )
    dummy.variant_state = wrong_term
    with pytest.raises(SaveGameError, match="返済期限"):
        _validate_restored_resource_credit(dummy)

    valid = _borrow(VariantState.initial(config), config, player_count=2)
    dummy.variant_state = valid
    _validate_restored_resource_credit(dummy)

    impossible_clock = valid.to_document()
    impossible_clock["public"]["completed_turns"] = 3
    with pytest.raises(VariantStateError, match="active"):
        VariantState.from_document(impossible_clock, config=config)


def test_credit_turn_action_flag_round_trips_and_legacy_defaults_false():
    game = CatanGame(board_seed=91_002, headless=True)
    restored = CatanGame(board_seed=91_003, headless=True)
    try:
        game.configure_players(2, reset_logs=False, schedule_ai=False)
        restored.configure_players(2, reset_logs=False, schedule_ai=False)
        game.credit_action_taken_this_turn = True
        document = serialize_game(game)

        restore_game(restored, copy.deepcopy(document), runtime_side_effects=False)
        assert restored.credit_action_taken_this_turn is True

        legacy = copy.deepcopy(document)
        legacy["phase"].pop("credit_action_taken_this_turn")
        restore_game(restored, legacy, runtime_side_effects=False)
        assert restored.credit_action_taken_this_turn is False
    finally:
        game.audio.stop()
        restored.audio.stop()
