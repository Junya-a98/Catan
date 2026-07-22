from dataclasses import FrozenInstanceError, replace
import json

import pytest

from game.resources import ResourceType
from game.resource_credit import (
    ACTIVE_REPAYMENT_CARDS,
    ACTIVE_VP_MODIFIER,
    BANK_TO_PLAYER,
    CREDIT_ADVANCE,
    CREDIT_BORROW,
    CREDIT_REPAY,
    DELINQUENT_INITIAL_CARDS,
    DELINQUENT_VP_MODIFIER,
    EXHAUSTED_NEXT_SEQUENCE,
    LOAN_ACTIVE,
    LOAN_DELINQUENT,
    MAX_OPEN_LOANS,
    MAX_REVISION,
    MAX_TURN,
    PLAYER_TO_BANK,
    RESOURCE_CREDIT_FORMAT,
    RESOURCE_CREDIT_VERSION,
    CreditBook,
    CreditCapacityError,
    CreditConflictError,
    CreditPermissionError,
    CreditResourceMutation,
    ResourceCreditError,
    ResourceLoan,
)


WOOD = ResourceType.WOOD
SHEEP = ResourceType.SHEEP
WHEAT = ResourceType.WHEAT
BRICK = ResourceType.BRICK
ORE = ResourceType.ORE


def borrow(
    book,
    *,
    borrower=0,
    resource=WOOD,
    turn=0,
    player_count=4,
):
    plan = book.plan_borrow(
        borrower_index=borrower,
        borrowed_resource=resource,
        current_turn=turn,
        player_count=player_count,
    )
    result = book.apply(plan)
    return result.book, result


def make_delinquent(
    *,
    borrower=0,
    resource=WOOD,
    turn=0,
    player_count=4,
):
    book, created = borrow(
        CreditBook(),
        borrower=borrower,
        resource=resource,
        turn=turn,
        player_count=player_count,
    )
    due = created.created_loan.due_turn
    result = book.apply(book.plan_advance(current_turn=due))
    return result.book, result.book.open_loans[0]


def canonical_document():
    book, _ = borrow(
        CreditBook(),
        borrower=0,
        resource=ORE,
        turn=8,
        player_count=3,
    )
    book = book.apply(book.plan_advance(current_turn=11)).book
    return book.to_document()


def test_empty_book_has_exact_versioned_deterministic_document():
    book = CreditBook()

    assert book.next_sequence == 0
    assert book.open_loans == ()
    assert book.to_document() == {
        "format": RESOURCE_CREDIT_FORMAT,
        "version": RESOURCE_CREDIT_VERSION,
        "next_sequence": 0,
        "open_loans": [],
    }
    assert json.loads(book.canonical_json()) == book.to_document()
    assert book.fingerprint() == CreditBook().fingerprint()


@pytest.mark.parametrize("resource", [WOOD, SHEEP, WHEAT, BRICK, ORE])
def test_borrow_is_functional_and_grants_exactly_one_bank_resource(resource):
    book = CreditBook()
    plan = book.plan_borrow(
        borrower_index=2,
        borrowed_resource=resource,
        current_turn=7,
        player_count=4,
    )

    assert plan.operation == CREDIT_BORROW
    assert plan.actor_index == 2
    assert plan.current_turn == 7
    assert plan.created_loan == ResourceLoan(
        loan_id="loan-000000000",
        borrower_index=2,
        borrowed_resource=resource,
        opened_turn=7,
        due_turn=11,
    )
    assert plan.updated_loans == ()
    assert plan.removed_loans == ()
    assert len(plan.resource_mutations) == 1
    grant = plan.resource_mutations[0]
    assert grant.operation == BANK_TO_PLAYER
    assert grant.player_index == 2
    assert dict(grant.bundle) == {resource: 1}
    assert book.open_loans == ()

    result = book.apply(plan)
    assert result.book.next_sequence == 1
    assert result.book.open_loans == (plan.created_loan,)
    assert result.operation == CREDIT_BORROW
    assert result.resource_mutations == (grant,)
    assert book.next_sequence == 0


def test_due_boundary_is_after_exactly_player_count_completed_turns():
    for player_count in (2, 3, 4):
        plan = CreditBook().plan_borrow(
            borrower_index=0,
            borrowed_resource=WOOD,
            current_turn=15,
            player_count=player_count,
        )
        assert plan.created_loan.opened_turn == 15
        assert plan.created_loan.due_turn == 15 + player_count
        assert not plan.created_loan.is_due(15 + player_count - 1)
        assert plan.created_loan.is_due(15 + player_count)


def test_ids_are_stable_monotonic_and_not_reused_after_repayment():
    book, first = borrow(CreditBook(), borrower=0, player_count=2)
    loan = first.created_loan
    paid = book.plan_repay(
        borrower_index=0,
        loan_id=loan.loan_id,
        expected_revision=loan.revision,
        payment={WOOD: 1, BRICK: 1},
        current_turn=1,
    )
    book = book.apply(paid).book
    book, second = borrow(book, borrower=0, resource=ORE, turn=1, player_count=2)

    assert first.created_loan.loan_id == "loan-000000000"
    assert second.created_loan.loan_id == "loan-000000001"
    assert book.next_sequence == 2


def test_one_open_loan_per_borrower_including_delinquent():
    book, _ = borrow(CreditBook(), borrower=1, player_count=3)
    before = book.to_document()
    with pytest.raises(CreditCapacityError, match="追加借入"):
        book.plan_borrow(
            borrower_index=1,
            borrowed_resource=ORE,
            current_turn=1,
            player_count=3,
        )
    assert book.to_document() == before

    book = book.apply(book.plan_advance(current_turn=3)).book
    with pytest.raises(CreditCapacityError, match="追加借入"):
        book.plan_borrow(
            borrower_index=1,
            borrowed_resource=ORE,
            current_turn=3,
            player_count=3,
        )


def test_all_four_borrowers_can_hold_one_loan_and_global_capacity_is_exact():
    book = CreditBook()
    for borrower_index in range(MAX_OPEN_LOANS):
        book, _ = borrow(
            book,
            borrower=borrower_index,
            turn=borrower_index,
            player_count=4,
        )
    assert len(book.open_loans) == MAX_OPEN_LOANS
    assert [loan.borrower_index for loan in book.open_loans] == [0, 1, 2, 3]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("borrower_index", True),
        ("borrower_index", -1),
        ("borrower_index", 4),
        ("borrowed_resource", ResourceType.DESERT),
        ("borrowed_resource", "WOOD"),
        ("current_turn", True),
        ("current_turn", -1),
        ("current_turn", 1.0),
        ("player_count", True),
        ("player_count", 1),
        ("player_count", 5),
        ("player_count", 3.0),
    ],
)
def test_borrow_rejects_invalid_fields_without_mutation(field, value):
    kwargs = {
        "borrower_index": 0,
        "borrowed_resource": WOOD,
        "current_turn": 0,
        "player_count": 4,
    }
    kwargs[field] = value
    book = CreditBook()
    with pytest.raises(ResourceCreditError):
        book.plan_borrow(**kwargs)
    assert book == book
    assert book.open_loans == ()


def test_borrower_must_be_an_actual_seat_for_player_count():
    with pytest.raises(ResourceCreditError, match="参加人数内"):
        CreditBook().plan_borrow(
            borrower_index=2,
            borrowed_resource=WOOD,
            current_turn=0,
            player_count=2,
        )


def test_borrow_rejects_due_turn_overflow_and_exhausted_ids():
    with pytest.raises(ResourceCreditError, match="上限"):
        CreditBook().plan_borrow(
            borrower_index=0,
            borrowed_resource=WOOD,
            current_turn=MAX_TURN - 1,
            player_count=2,
        )
    exhausted = CreditBook(next_sequence=EXHAUSTED_NEXT_SEQUENCE)
    with pytest.raises(CreditCapacityError, match="ID"):
        exhausted.plan_borrow(
            borrower_index=0,
            borrowed_resource=WOOD,
            current_turn=0,
            player_count=2,
        )


@pytest.mark.parametrize(
    "payment",
    [
        {WOOD: 2},
        {WOOD: 1, SHEEP: 1},
        {WOOD: 1, WHEAT: 1},
        {WOOD: 1, BRICK: 1},
        {WOOD: 1, ORE: 1},
    ],
)
def test_active_repayment_is_atomic_borrowed_card_plus_any_one_card(payment):
    book, created = borrow(CreditBook(), resource=WOOD, player_count=4)
    loan = created.created_loan
    plan = book.plan_repay(
        borrower_index=0,
        loan_id=loan.loan_id,
        expected_revision=1,
        payment=payment,
        current_turn=3,
    )

    assert plan.operation == CREDIT_REPAY
    assert plan.actor_index == 0
    assert plan.created_loan is None
    assert plan.updated_loans == ()
    assert plan.removed_loans == (loan,)
    assert len(plan.resource_mutations) == 1
    repayment = plan.resource_mutations[0]
    assert repayment.operation == PLAYER_TO_BANK
    assert repayment.player_index == 0
    assert dict(repayment.bundle) == payment

    result = book.apply(plan)
    assert result.book.open_loans == ()
    assert result.removed_loans == (loan,)
    assert book.open_loans == (loan,)


@pytest.mark.parametrize(
    "payment",
    [
        {},
        {SHEEP: 2},
        {WOOD: 1},
        {WOOD: 3},
        {WOOD: 1, SHEEP: 2},
        {ResourceType.DESERT: 1, WOOD: 1},
        {WOOD: True, SHEEP: 1},
        {WOOD: 1.0, SHEEP: 1},
        {"WOOD": 1, SHEEP: 1},
    ],
)
def test_active_repayment_rejects_noncanonical_or_wrong_payment(payment):
    book, created = borrow(CreditBook(), resource=WOOD, player_count=4)
    before = book.to_document()
    with pytest.raises(ResourceCreditError):
        book.plan_repay(
            borrower_index=0,
            loan_id=created.created_loan.loan_id,
            expected_revision=1,
            payment=payment,
            current_turn=1,
        )
    assert book.to_document() == before


def test_only_borrower_can_repay_and_revision_is_optimistic():
    book, created = borrow(CreditBook(), borrower=1, player_count=3)
    loan = created.created_loan
    with pytest.raises(CreditPermissionError):
        book.plan_repay(
            borrower_index=0,
            loan_id=loan.loan_id,
            expected_revision=1,
            payment={WOOD: 2},
            current_turn=1,
        )
    with pytest.raises(CreditConflictError, match="revision"):
        book.plan_repay(
            borrower_index=1,
            loan_id=loan.loan_id,
            expected_revision=2,
            payment={WOOD: 2},
            current_turn=1,
        )
    with pytest.raises(CreditConflictError, match="存在"):
        book.plan_repay(
            borrower_index=1,
            loan_id="loan-000000999",
            expected_revision=1,
            payment={WOOD: 2},
            current_turn=1,
        )


def test_active_repayment_is_allowed_during_due_turn_but_not_after_it():
    book, created = borrow(CreditBook(), player_count=2)
    loan = created.created_loan
    plan = book.plan_repay(
        borrower_index=0,
        loan_id=loan.loan_id,
        expected_revision=1,
        payment={WOOD: 2},
        current_turn=loan.due_turn,
    )
    assert plan.removed_loans == (loan,)

    with pytest.raises(CreditConflictError, match="延滞更新"):
        book.plan_repay(
            borrower_index=0,
            loan_id=loan.loan_id,
            expected_revision=1,
            payment={WOOD: 2},
            current_turn=loan.due_turn + 1,
        )


def test_advance_converts_at_boundary_once_without_forcing_resource_payment():
    book, created = borrow(
        CreditBook(),
        borrower=2,
        resource=WHEAT,
        turn=5,
        player_count=3,
    )
    loan = created.created_loan

    early_plan = book.plan_advance(current_turn=7)
    assert early_plan.operation == CREDIT_ADVANCE
    assert early_plan.actor_index is None
    assert early_plan.updated_loans == ()
    assert early_plan.resource_mutations == ()
    assert book.apply(early_plan).book is book

    due_plan = book.plan_advance(current_turn=8)
    assert due_plan.resource_mutations == ()
    assert due_plan.removed_loans == ()
    assert len(due_plan.updated_loans) == 1
    delinquent = due_plan.updated_loans[0]
    assert delinquent.loan_id == loan.loan_id
    assert delinquent.status == LOAN_DELINQUENT
    assert delinquent.remaining_cards == DELINQUENT_INITIAL_CARDS
    assert delinquent.revision == 2
    assert delinquent.public_vp_modifier == DELINQUENT_VP_MODIFIER

    book = book.apply(due_plan).book
    repeated = book.plan_advance(current_turn=100)
    assert repeated.updated_loans == ()
    assert repeated.resource_mutations == ()
    assert book.apply(repeated).book is book
    assert book.open_loans[0].revision == 2


def test_advance_batches_only_due_active_loans_in_stable_id_order():
    book = CreditBook()
    book, _ = borrow(book, borrower=0, turn=0, player_count=2)
    book, _ = borrow(book, borrower=1, turn=0, player_count=4)
    book, _ = borrow(book, borrower=2, turn=1, player_count=3)

    first = book.apply(book.plan_advance(current_turn=2)).book
    assert [loan.status for loan in first.open_loans] == [
        LOAN_DELINQUENT,
        LOAN_ACTIVE,
        LOAN_ACTIVE,
    ]
    second_plan = first.plan_advance(current_turn=4)
    assert [loan.loan_id for loan in second_plan.updated_loans] == [
        "loan-000000001",
        "loan-000000002",
    ]
    second = first.apply(second_plan).book
    assert all(loan.status == LOAN_DELINQUENT for loan in second.open_loans)


@pytest.mark.parametrize(
    ("payment", "remaining"),
    [
        ({WOOD: 1}, 2),
        ({SHEEP: 1, ORE: 1}, 1),
        ({WHEAT: 2}, 1),
    ],
)
def test_delinquent_repayment_is_generic_and_may_be_partial(payment, remaining):
    book, loan = make_delinquent(resource=WOOD)
    plan = book.plan_repay(
        borrower_index=0,
        loan_id=loan.loan_id,
        expected_revision=loan.revision,
        payment=payment,
        current_turn=99,
    )

    assert plan.removed_loans == ()
    assert len(plan.updated_loans) == 1
    updated = plan.updated_loans[0]
    assert updated.status == LOAN_DELINQUENT
    assert updated.remaining_cards == remaining
    assert updated.revision == loan.revision + 1
    assert dict(plan.resource_mutations[0].bundle) == payment
    result = book.apply(plan)
    assert result.book.open_loans == (updated,)


@pytest.mark.parametrize(
    "payment",
    [
        {WOOD: 3},
        {WOOD: 1, SHEEP: 1, ORE: 1},
    ],
)
def test_delinquent_full_repayment_closes_the_liability(payment):
    book, loan = make_delinquent(resource=BRICK)
    plan = book.plan_repay(
        borrower_index=0,
        loan_id=loan.loan_id,
        expected_revision=2,
        payment=payment,
        current_turn=loan.due_turn,
    )
    assert plan.updated_loans == ()
    assert plan.removed_loans == (loan,)
    assert book.apply(plan).book.open_loans == ()


def test_delinquent_can_be_repaid_over_three_separate_actions_without_softlock():
    book, loan = make_delinquent()
    for expected_remaining in (2, 1):
        plan = book.plan_repay(
            borrower_index=0,
            loan_id=loan.loan_id,
            expected_revision=loan.revision,
            payment={ORE: 1},
            current_turn=loan.due_turn + 100,
        )
        book = book.apply(plan).book
        loan = book.open_loans[0]
        assert loan.remaining_cards == expected_remaining
        assert loan.public_vp_modifier == DELINQUENT_VP_MODIFIER

    plan = book.plan_repay(
        borrower_index=0,
        loan_id=loan.loan_id,
        expected_revision=loan.revision,
        payment={SHEEP: 1},
        current_turn=loan.due_turn + 200,
    )
    assert book.apply(plan).book.open_loans == ()


def test_delinquent_overpayment_is_rejected_atomically():
    book, loan = make_delinquent()
    first = book.plan_repay(
        borrower_index=0,
        loan_id=loan.loan_id,
        expected_revision=2,
        payment={WOOD: 2},
        current_turn=loan.due_turn,
    )
    book = book.apply(first).book
    loan = book.open_loans[0]
    before = book.to_document()
    with pytest.raises(ResourceCreditError, match="超える"):
        book.plan_repay(
            borrower_index=0,
            loan_id=loan.loan_id,
            expected_revision=loan.revision,
            payment={WOOD: 2},
            current_turn=loan.due_turn + 1,
        )
    assert book.to_document() == before


def test_public_vp_liability_is_visible_and_disappears_only_when_closed():
    book, created = borrow(CreditBook(), borrower=3, resource=ORE, player_count=4)
    loan = created.created_loan
    assert loan.public_vp_modifier == ACTIVE_VP_MODIFIER
    assert loan.public_vp_penalty == 1
    assert book.public_vp_modifier(3) == ACTIVE_VP_MODIFIER
    assert book.public_vp_modifier(0) == 0

    book = book.apply(book.plan_advance(current_turn=loan.due_turn)).book
    loan = book.open_loans[0]
    assert loan.public_vp_modifier == DELINQUENT_VP_MODIFIER
    assert loan.public_vp_penalty == 2
    assert book.public_vp_modifier(3) == DELINQUENT_VP_MODIFIER

    paid = book.plan_repay(
        borrower_index=3,
        loan_id=loan.loan_id,
        expected_revision=loan.revision,
        payment={WOOD: 3},
        current_turn=loan.due_turn,
    )
    book = book.apply(paid).book
    assert book.public_vp_modifier(3) == 0


def test_plans_and_results_are_frozen_and_contain_only_immutable_collections():
    book = CreditBook()
    plan = book.plan_borrow(
        borrower_index=0,
        borrowed_resource=WOOD,
        current_turn=0,
        player_count=2,
    )
    result = book.apply(plan)

    with pytest.raises(FrozenInstanceError):
        plan.operation = CREDIT_REPAY
    with pytest.raises(FrozenInstanceError):
        result.operation = CREDIT_REPAY
    with pytest.raises(TypeError):
        plan.resource_mutations[0].bundle[WOOD] = 9
    with pytest.raises(ResourceCreditError, match="tuple"):
        replace(plan, updated_loans=[])
    assert type(plan.updated_loans) is tuple
    assert type(plan.removed_loans) is tuple
    assert type(plan.resource_mutations) is tuple


def test_stale_and_tampered_plans_are_rejected_without_mutation():
    book = CreditBook()
    plan = book.plan_borrow(
        borrower_index=0,
        borrowed_resource=WOOD,
        current_turn=0,
        player_count=2,
    )
    changed, _ = borrow(book, borrower=1, resource=ORE, player_count=2)
    before = changed.to_document()
    with pytest.raises(CreditConflictError, match="更新"):
        changed.apply(plan)
    assert changed.to_document() == before

    tampered = replace(
        plan,
        resource_mutations=(
            CreditResourceMutation(
                operation=BANK_TO_PLAYER,
                player_index=0,
                bundle={ORE: 1},
            ),
        ),
    )
    with pytest.raises(CreditConflictError, match="一致"):
        book.apply(tampered)
    assert book.open_loans == ()


def test_apply_rejects_non_plan_and_unknown_operation():
    with pytest.raises(ResourceCreditError, match="Plan"):
        CreditBook().apply(object())
    plan = CreditBook().plan_advance(current_turn=0)
    with pytest.raises(ResourceCreditError, match="未対応"):
        CreditBook().apply(replace(plan, operation="forged"))


def test_exact_json_round_trip_preserves_fingerprint_and_is_detached():
    document = canonical_document()
    restored = CreditBook.from_document(document)

    assert restored.to_document() == document
    assert restored.fingerprint() == CreditBook.from_document(document).fingerprint()
    encoded = restored.canonical_json()
    assert encoded == CreditBook.from_document(json.loads(encoded)).canonical_json()

    document["open_loans"][0]["remaining_cards"] = 1
    assert restored.open_loans[0].remaining_cards == DELINQUENT_INITIAL_CARDS


@pytest.mark.parametrize("extra", ["extra", "loans", "schema"])
def test_top_level_document_rejects_unknown_and_missing_keys(extra):
    base = canonical_document()
    with pytest.raises(ResourceCreditError, match="キー"):
        CreditBook.from_document({**base, extra: 1})
    missing = dict(base)
    missing.pop("next_sequence")
    with pytest.raises(ResourceCreditError, match="キー"):
        CreditBook.from_document(missing)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format", "wrong"),
        ("version", 2),
        ("version", True),
        ("next_sequence", True),
        ("next_sequence", -1),
        ("next_sequence", EXHAUSTED_NEXT_SEQUENCE + 1),
        ("open_loans", {}),
    ],
)
def test_top_level_document_rejects_invalid_authority_fields(field, value):
    document = canonical_document()
    document[field] = value
    with pytest.raises(ResourceCreditError):
        CreditBook.from_document(document)


def test_nested_document_requires_exact_keys_and_resource_enum_name():
    document = canonical_document()
    loan = document["open_loans"][0]
    loan["unknown"] = 1
    with pytest.raises(ResourceCreditError, match="キー"):
        CreditBook.from_document(document)

    document = canonical_document()
    document["open_loans"][0]["borrowed_resource"] = "DESERT"
    with pytest.raises(ResourceCreditError, match="資源名"):
        CreditBook.from_document(document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("loan_id", "loan-1"),
        ("borrower_index", True),
        ("borrower_index", 4),
        ("opened_turn", True),
        ("opened_turn", -1),
        ("due_turn", 8),
        ("status", "closed"),
        ("remaining_cards", True),
        ("remaining_cards", 0),
        ("remaining_cards", 4),
        ("revision", True),
        ("revision", 0),
    ],
)
def test_nested_document_rejects_invalid_loan_fields(field, value):
    document = canonical_document()
    document["open_loans"][0][field] = value
    with pytest.raises(ResourceCreditError):
        CreditBook.from_document(document)


def test_constructor_rejects_out_of_order_ids_duplicate_borrowers_and_future_ids():
    first = ResourceLoan("loan-000000000", 0, WOOD, 0, 2)
    second = ResourceLoan("loan-000000001", 1, ORE, 0, 2)
    with pytest.raises(ResourceCreditError, match="昇順"):
        CreditBook(next_sequence=2, open_loans=(second, first))
    duplicate_borrower = ResourceLoan("loan-000000001", 0, ORE, 0, 2)
    with pytest.raises(CreditCapacityError, match="1件"):
        CreditBook(next_sequence=2, open_loans=(first, duplicate_borrower))
    with pytest.raises(ResourceCreditError, match="next_sequence未満"):
        CreditBook(next_sequence=1, open_loans=(second,))


def test_resource_loan_enforces_state_specific_remaining_cards():
    with pytest.raises(ResourceCreditError, match="active"):
        ResourceLoan(
            "loan-000000000",
            0,
            WOOD,
            0,
            2,
            status=LOAN_ACTIVE,
            remaining_cards=1,
        )
    for remaining in (1, 2, 3):
        loan = ResourceLoan(
            "loan-000000000",
            0,
            WOOD,
            0,
            2,
            status=LOAN_DELINQUENT,
            remaining_cards=remaining,
            revision=2,
        )
        assert loan.remaining_cards == remaining


@pytest.mark.parametrize(
    "mutation",
    [
        lambda: CreditResourceMutation("unknown", 0, {WOOD: 1}),
        lambda: CreditResourceMutation(BANK_TO_PLAYER, True, {WOOD: 1}),
        lambda: CreditResourceMutation(BANK_TO_PLAYER, 0, {WOOD: 2}),
        lambda: CreditResourceMutation(BANK_TO_PLAYER, 0, {}),
        lambda: CreditResourceMutation(PLAYER_TO_BANK, 0, {WOOD: 4}),
        lambda: CreditResourceMutation(PLAYER_TO_BANK, 0, {ResourceType.DESERT: 1}),
    ],
)
def test_resource_mutation_rejects_malformed_or_impossible_work(mutation):
    with pytest.raises(ResourceCreditError):
        mutation()


def test_revision_exhaustion_is_rejected_before_delinquent_update():
    loan = ResourceLoan(
        "loan-000000000",
        0,
        WOOD,
        0,
        2,
        status=LOAN_ACTIVE,
        remaining_cards=ACTIVE_REPAYMENT_CARDS,
        revision=MAX_REVISION,
    )
    book = CreditBook(next_sequence=1, open_loans=(loan,))
    with pytest.raises(CreditCapacityError, match="revision"):
        book.plan_advance(current_turn=2)


def test_open_delinquent_liability_never_forces_a_resource_mutation_or_turn_block():
    book, loan = make_delinquent()
    for current_turn in (loan.due_turn, loan.due_turn + 1, MAX_TURN):
        plan = book.plan_advance(current_turn=current_turn)
        assert plan.updated_loans == ()
        assert plan.removed_loans == ()
        assert plan.resource_mutations == ()
        assert book.apply(plan).book is book
    assert book.open_loans == (loan,)
    assert book.public_vp_modifier(0) == DELINQUENT_VP_MODIFIER
