"""Pure authority domain for optional resource-credit loans.

The domain deliberately knows nothing about ``Player``, ``CatanGame``, the
bank implementation, networking, or presentation.  It owns immutable public
loan state and emits exact bank/player resource work for an integration layer
to commit atomically with a state replacement.

V1 rules are intentionally small and deterministic:

* a borrower receives exactly one bank resource;
* the due boundary is the end of the borrower's next turn, represented by
  ``player_count`` additional completed turns;
* an active loan is repaid atomically with one card of the borrowed resource
  plus any one additional resource card;
* at the due boundary an unpaid loan becomes delinquent once and changes to a
  generic three-card liability;
* delinquent liabilities may be repaid partially; and
* each borrower may have at most one open loan.

Delinquency never forces payment or blocks turn progression.  Its pressure is
the public victory-point modifier exposed by :class:`ResourceLoan` instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any

from game.bank import RESOURCE_TYPES
from game.resource_ledger import MAX_RESOURCE_COUNT
from game.resources import ResourceType


RESOURCE_CREDIT_FORMAT = "catan-resource-credit"
RESOURCE_CREDIT_VERSION = 1

LOAN_ACTIVE = "active"
LOAN_DELINQUENT = "delinquent"

CREDIT_BORROW = "borrow"
CREDIT_REPAY = "repay"
CREDIT_ADVANCE = "advance"

BANK_TO_PLAYER = "bank_to_player"
PLAYER_TO_BANK = "player_to_bank"

ACTIVE_REPAYMENT_CARDS = 2
DELINQUENT_INITIAL_CARDS = 3
ACTIVE_VP_MODIFIER = -1
DELINQUENT_VP_MODIFIER = -2

MIN_PLAYER_COUNT = 2
MAX_PLAYER_COUNT = 4
MAX_PLAYER_INDEX = MAX_PLAYER_COUNT - 1
MAX_OPEN_LOANS = MAX_PLAYER_COUNT
MAX_TURN = 2_147_483_647
MAX_REVISION = 2_147_483_647
MAX_LOAN_SEQUENCE = 999_999_999
EXHAUSTED_NEXT_SEQUENCE = MAX_LOAN_SEQUENCE + 1

_LOAN_STATUSES = frozenset({LOAN_ACTIVE, LOAN_DELINQUENT})
_CREDIT_OPERATIONS = frozenset({CREDIT_BORROW, CREDIT_REPAY, CREDIT_ADVANCE})
_RESOURCE_OPERATIONS = frozenset({BANK_TO_PLAYER, PLAYER_TO_BANK})
_DOCUMENT_KEYS = frozenset({"format", "version", "next_sequence", "open_loans"})
_LOAN_DOCUMENT_KEYS = frozenset(
    {
        "loan_id",
        "borrower_index",
        "borrowed_resource",
        "opened_turn",
        "due_turn",
        "status",
        "remaining_cards",
        "revision",
    }
)
_RESOURCE_SET = frozenset(RESOURCE_TYPES)
_RESOURCE_NAMES = frozenset(resource.name for resource in RESOURCE_TYPES)
_LOAN_ID_PATTERN = re.compile(r"loan-[0-9]{9}\Z")


class ResourceCreditError(ValueError):
    """Raised when credit input, state, or a mutation plan is malformed."""


class CreditConflictError(ResourceCreditError):
    """Raised when a loan revision or optimistic plan is stale."""


class CreditPermissionError(ResourceCreditError):
    """Raised when an actor cannot perform a requested loan operation."""


class CreditCapacityError(ResourceCreditError):
    """Raised when the loan book or its stable-ID sequence is exhausted."""


@dataclass(frozen=True)
class ResourceLoan:
    """One immutable public resource liability."""

    loan_id: str
    borrower_index: int
    borrowed_resource: ResourceType
    opened_turn: int
    due_turn: int
    status: str = LOAN_ACTIVE
    remaining_cards: int = ACTIVE_REPAYMENT_CARDS
    revision: int = 1

    def __post_init__(self) -> None:
        _validate_loan_id(self.loan_id)
        _validate_player_index(self.borrower_index, label="borrower_index")
        _validate_resource(self.borrowed_resource, label="borrowed_resource")
        opened_turn = _validate_turn(self.opened_turn, label="opened_turn")
        due_turn = _validate_turn(self.due_turn, label="due_turn")
        due_after = due_turn - opened_turn
        if not MIN_PLAYER_COUNT <= due_after <= MAX_PLAYER_COUNT:
            raise ResourceCreditError(
                "返済期限は借入から2〜4完了手番後にしてください。"
            )
        if type(self.status) is not str or self.status not in _LOAN_STATUSES:
            raise ResourceCreditError("未対応のローン状態です。")
        remaining = _validate_int_range(
            self.remaining_cards,
            label="remaining_cards",
            minimum=1,
            maximum=DELINQUENT_INITIAL_CARDS,
        )
        if self.status == LOAN_ACTIVE and remaining != ACTIVE_REPAYMENT_CARDS:
            raise ResourceCreditError(
                f"activeローンの残債は{ACTIVE_REPAYMENT_CARDS}枚です。"
            )
        _validate_int_range(
            self.revision,
            label="revision",
            minimum=1,
            maximum=MAX_REVISION,
        )

    @property
    def public_vp_modifier(self) -> int:
        """Return the public score adjustment while this loan remains open."""

        if self.status == LOAN_ACTIVE:
            return ACTIVE_VP_MODIFIER
        return DELINQUENT_VP_MODIFIER

    @property
    def public_vp_penalty(self) -> int:
        """Return the same liability as a positive penalty magnitude."""

        return -self.public_vp_modifier

    def is_due(self, completed_turns: int) -> bool:
        return _validate_turn(completed_turns, label="completed_turns") >= self.due_turn

    def to_document(self) -> dict[str, Any]:
        return {
            "loan_id": self.loan_id,
            "borrower_index": self.borrower_index,
            "borrowed_resource": self.borrowed_resource.name,
            "opened_turn": self.opened_turn,
            "due_turn": self.due_turn,
            "status": self.status,
            "remaining_cards": self.remaining_cards,
            "revision": self.revision,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> ResourceLoan:
        _expect_exact_keys(document, _LOAN_DOCUMENT_KEYS, "公開ローン")
        return cls(
            loan_id=document["loan_id"],
            borrower_index=document["borrower_index"],
            borrowed_resource=_resource_from_document(
                document["borrowed_resource"],
                label="borrowed_resource",
            ),
            opened_turn=document["opened_turn"],
            due_turn=document["due_turn"],
            status=document["status"],
            remaining_cards=document["remaining_cards"],
            revision=document["revision"],
        )


@dataclass(frozen=True)
class CreditResourceMutation:
    """Exact bank/player resource movement to commit with a credit plan."""

    operation: str
    player_index: int
    bundle: Mapping[ResourceType, int]

    def __post_init__(self) -> None:
        if type(self.operation) is not str or self.operation not in _RESOURCE_OPERATIONS:
            raise ResourceCreditError("未対応の信用資源操作です。")
        _validate_player_index(self.player_index, label="player_index")
        bundle = _canonical_bundle(self.bundle, label="resource_mutation.bundle")
        maximum = 1 if self.operation == BANK_TO_PLAYER else DELINQUENT_INITIAL_CARDS
        if _bundle_total(bundle) > maximum:
            raise ResourceCreditError(
                f"{self.operation}の資源移動は合計{maximum}枚以下です。"
            )
        if self.operation == BANK_TO_PLAYER and _bundle_total(bundle) != 1:
            raise ResourceCreditError("借入で銀行から受け取れる資源は1枚です。")
        object.__setattr__(self, "bundle", MappingProxyType(bundle))


@dataclass(frozen=True)
class CreditMutationPlan:
    """Process-local optimistic plan produced only by :class:`CreditBook`."""

    operation: str
    base_fingerprint: str
    actor_index: int | None
    current_turn: int
    created_loan: ResourceLoan | None
    updated_loans: tuple[ResourceLoan, ...]
    removed_loans: tuple[ResourceLoan, ...]
    resource_mutations: tuple[CreditResourceMutation, ...]

    def __post_init__(self) -> None:
        if type(self.operation) is not str or self.operation not in _CREDIT_OPERATIONS:
            raise ResourceCreditError("未対応の信用操作です。")
        if type(self.base_fingerprint) is not str:
            raise ResourceCreditError("base_fingerprintは文字列で指定してください。")
        if self.actor_index is not None:
            _validate_player_index(self.actor_index, label="actor_index")
        _validate_turn(self.current_turn, label="current_turn")
        if self.created_loan is not None and type(self.created_loan) is not ResourceLoan:
            raise ResourceCreditError("created_loanにはResourceLoanを指定してください。")
        _validate_immutable_tuple(
            self.updated_loans,
            item_type=ResourceLoan,
            label="updated_loans",
        )
        _validate_immutable_tuple(
            self.removed_loans,
            item_type=ResourceLoan,
            label="removed_loans",
        )
        _validate_immutable_tuple(
            self.resource_mutations,
            item_type=CreditResourceMutation,
            label="resource_mutations",
        )


@dataclass(frozen=True)
class CreditMutationResult:
    """Candidate authority state and all-or-nothing bank/player resource work."""

    book: CreditBook
    operation: str
    created_loan: ResourceLoan | None
    updated_loans: tuple[ResourceLoan, ...]
    removed_loans: tuple[ResourceLoan, ...]
    resource_mutations: tuple[CreditResourceMutation, ...]

    def __post_init__(self) -> None:
        if type(self.book) is not CreditBook:
            raise ResourceCreditError("bookにはCreditBookを指定してください。")
        if type(self.operation) is not str or self.operation not in _CREDIT_OPERATIONS:
            raise ResourceCreditError("未対応の信用操作です。")
        if self.created_loan is not None and type(self.created_loan) is not ResourceLoan:
            raise ResourceCreditError("created_loanにはResourceLoanを指定してください。")
        _validate_immutable_tuple(
            self.updated_loans,
            item_type=ResourceLoan,
            label="updated_loans",
        )
        _validate_immutable_tuple(
            self.removed_loans,
            item_type=ResourceLoan,
            label="removed_loans",
        )
        _validate_immutable_tuple(
            self.resource_mutations,
            item_type=CreditResourceMutation,
            label="resource_mutations",
        )


class CreditBook:
    """Immutable authority snapshot for all currently open resource loans."""

    __slots__ = ("_next_sequence", "_loans")

    def __init__(
        self,
        *,
        next_sequence: int = 0,
        open_loans: tuple[ResourceLoan, ...] | list[ResourceLoan] = (),
    ) -> None:
        self._next_sequence = _validate_next_sequence(next_sequence)
        if not isinstance(open_loans, (tuple, list)):
            raise ResourceCreditError("open_loansはローン配列で指定してください。")
        if len(open_loans) > MAX_OPEN_LOANS:
            raise CreditCapacityError(
                f"公開ローンは全体で{MAX_OPEN_LOANS}件以下です。"
            )

        loans: dict[str, ResourceLoan] = {}
        borrowers: set[int] = set()
        previous_id: str | None = None
        for loan in open_loans:
            if type(loan) is not ResourceLoan:
                raise ResourceCreditError(
                    "open_loansにはResourceLoanだけを指定してください。"
                )
            if previous_id is not None and loan.loan_id <= previous_id:
                raise ResourceCreditError(
                    "open_loansは重複のないloan_id昇順で指定してください。"
                )
            sequence = _sequence_from_loan_id(loan.loan_id)
            if sequence >= self._next_sequence:
                raise ResourceCreditError(
                    "公開ローンのsequenceはnext_sequence未満にしてください。"
                )
            if loan.borrower_index in borrowers:
                raise CreditCapacityError("1人が同時に持てる公開ローンは1件です。")
            loans[loan.loan_id] = loan
            borrowers.add(loan.borrower_index)
            previous_id = loan.loan_id
        self._loans = loans

    @property
    def next_sequence(self) -> int:
        return self._next_sequence

    @property
    def open_loans(self) -> tuple[ResourceLoan, ...]:
        return tuple(self._loans[loan_id] for loan_id in sorted(self._loans))

    def get_loan(self, loan_id: str) -> ResourceLoan | None:
        return self._loans.get(_validate_loan_id(loan_id))

    def get_loan_for_borrower(self, borrower_index: int) -> ResourceLoan | None:
        borrower_index = _validate_player_index(
            borrower_index,
            label="borrower_index",
        )
        return next(
            (
                loan
                for loan in self.open_loans
                if loan.borrower_index == borrower_index
            ),
            None,
        )

    def public_vp_modifier(self, borrower_index: int) -> int:
        loan = self.get_loan_for_borrower(borrower_index)
        return 0 if loan is None else loan.public_vp_modifier

    def plan_borrow(
        self,
        *,
        borrower_index: int,
        borrowed_resource: ResourceType,
        current_turn: int,
        player_count: int,
    ) -> CreditMutationPlan:
        """Prepare a one-card bank grant and its public liability."""

        borrower_index = _validate_player_index(
            borrower_index,
            label="borrower_index",
        )
        borrowed_resource = _validate_resource(
            borrowed_resource,
            label="borrowed_resource",
        )
        current_turn = _validate_turn(current_turn, label="current_turn")
        player_count = _validate_int_range(
            player_count,
            label="player_count",
            minimum=MIN_PLAYER_COUNT,
            maximum=MAX_PLAYER_COUNT,
        )
        if borrower_index >= player_count:
            raise ResourceCreditError("借り手は参加人数内の席にしてください。")
        if current_turn > MAX_TURN - player_count:
            raise ResourceCreditError("返済期限が完了手番上限を超えます。")
        if self.get_loan_for_borrower(borrower_index) is not None:
            raise CreditCapacityError("未返済ローンがある間は追加借入できません。")
        if len(self._loans) >= MAX_OPEN_LOANS:
            raise CreditCapacityError(
                f"公開ローンは全体で{MAX_OPEN_LOANS}件までです。"
            )
        if self._next_sequence >= EXHAUSTED_NEXT_SEQUENCE:
            raise CreditCapacityError("ローンIDをこれ以上発行できません。")

        loan = ResourceLoan(
            loan_id=_loan_id_for_sequence(self._next_sequence),
            borrower_index=borrower_index,
            borrowed_resource=borrowed_resource,
            opened_turn=current_turn,
            due_turn=current_turn + player_count,
        )
        grant = CreditResourceMutation(
            operation=BANK_TO_PLAYER,
            player_index=borrower_index,
            bundle={borrowed_resource: 1},
        )
        return CreditMutationPlan(
            operation=CREDIT_BORROW,
            base_fingerprint=self.fingerprint(),
            actor_index=borrower_index,
            current_turn=current_turn,
            created_loan=loan,
            updated_loans=(),
            removed_loans=(),
            resource_mutations=(grant,),
        )

    def plan_repay(
        self,
        *,
        borrower_index: int,
        loan_id: str,
        expected_revision: int,
        payment: Mapping[ResourceType, int],
        current_turn: int,
    ) -> CreditMutationPlan:
        """Prepare an active atomic payment or delinquent partial payment."""

        borrower_index = _validate_player_index(
            borrower_index,
            label="borrower_index",
        )
        current_turn = _validate_turn(current_turn, label="current_turn")
        loan = self._require_loan(loan_id, expected_revision)
        if loan.borrower_index != borrower_index:
            raise CreditPermissionError("ローンを返済できるのは借り手本人だけです。")
        canonical_payment = _canonical_bundle(payment, label="payment")
        payment_total = _bundle_total(canonical_payment)

        if loan.status == LOAN_ACTIVE:
            # The borrower may still settle during the turn whose completed-
            # turn counter equals ``due_turn``.  Authority calls
            # ``plan_advance`` at that turn's end, so only a state that has
            # slipped past the deadline without advancing is rejected here.
            if current_turn > loan.due_turn:
                raise CreditConflictError(
                    "返済期限に達したローンは延滞更新後に返済してください。"
                )
            if (
                payment_total != ACTIVE_REPAYMENT_CARDS
                or canonical_payment.get(loan.borrowed_resource, 0) < 1
            ):
                raise ResourceCreditError(
                    "通常返済は借りた資源1枚と任意の追加資源1枚を同時に支払います。"
                )
            updated_loans: tuple[ResourceLoan, ...] = ()
            removed_loans = (loan,)
        else:
            if payment_total > loan.remaining_cards:
                raise ResourceCreditError("延滞残債を超える返済はできません。")
            remaining = loan.remaining_cards - payment_total
            if remaining == 0:
                updated_loans = ()
                removed_loans = (loan,)
            else:
                updated_loans = (
                    _with_remaining(
                        loan,
                        remaining_cards=remaining,
                        revision=_increment_revision(loan.revision),
                    ),
                )
                removed_loans = ()

        repayment = CreditResourceMutation(
            operation=PLAYER_TO_BANK,
            player_index=borrower_index,
            bundle=canonical_payment,
        )
        return CreditMutationPlan(
            operation=CREDIT_REPAY,
            base_fingerprint=self.fingerprint(),
            actor_index=borrower_index,
            current_turn=current_turn,
            created_loan=None,
            updated_loans=updated_loans,
            removed_loans=removed_loans,
            resource_mutations=(repayment,),
        )

    def plan_advance(self, *, current_turn: int) -> CreditMutationPlan:
        """Prepare one idempotent due-boundary transition batch.

        No resource work is emitted: unpaid loans remain playable and receive
        only the stronger public score liability.
        """

        current_turn = _validate_turn(current_turn, label="current_turn")
        updated = tuple(
            _with_delinquent(loan)
            for loan in self.open_loans
            if loan.status == LOAN_ACTIVE and loan.is_due(current_turn)
        )
        return CreditMutationPlan(
            operation=CREDIT_ADVANCE,
            base_fingerprint=self.fingerprint(),
            actor_index=None,
            current_turn=current_turn,
            created_loan=None,
            updated_loans=updated,
            removed_loans=(),
            resource_mutations=(),
        )

    def apply(self, plan: CreditMutationPlan) -> CreditMutationResult:
        """Validate and functionally apply one authority-produced plan."""

        if type(plan) is not CreditMutationPlan:
            raise ResourceCreditError("CreditMutationPlanを指定してください。")
        if type(plan.operation) is not str or plan.operation not in _CREDIT_OPERATIONS:
            raise ResourceCreditError("未対応の信用操作です。")
        if plan.base_fingerprint != self.fingerprint():
            raise CreditConflictError("信用状態が更新されたため再試行してください。")

        expected = self._rebuild_plan(plan)
        if plan != expected:
            raise CreditConflictError("信用操作planが現在状態と一致しません。")

        loans = dict(self._loans)
        next_sequence = self._next_sequence
        if plan.operation == CREDIT_BORROW:
            assert plan.created_loan is not None
            loans[plan.created_loan.loan_id] = plan.created_loan
            next_sequence += 1
        elif plan.operation == CREDIT_REPAY:
            for removed in plan.removed_loans:
                del loans[removed.loan_id]
            for updated in plan.updated_loans:
                loans[updated.loan_id] = updated
        else:
            for updated in plan.updated_loans:
                loans[updated.loan_id] = updated

        ordered = tuple(loans[loan_id] for loan_id in sorted(loans))
        if next_sequence == self._next_sequence and ordered == self.open_loans:
            next_book = self
        else:
            next_book = CreditBook(next_sequence=next_sequence, open_loans=ordered)
        return CreditMutationResult(
            book=next_book,
            operation=plan.operation,
            created_loan=plan.created_loan,
            updated_loans=plan.updated_loans,
            removed_loans=plan.removed_loans,
            resource_mutations=plan.resource_mutations,
        )

    def _rebuild_plan(self, plan: CreditMutationPlan) -> CreditMutationPlan:
        if plan.operation == CREDIT_BORROW:
            loan = plan.created_loan
            if loan is None:
                raise CreditConflictError("作成対象のローンがありません。")
            return self.plan_borrow(
                borrower_index=loan.borrower_index,
                borrowed_resource=loan.borrowed_resource,
                current_turn=loan.opened_turn,
                player_count=loan.due_turn - loan.opened_turn,
            )

        if plan.operation == CREDIT_REPAY:
            if (
                plan.actor_index is None
                or len(plan.resource_mutations) != 1
                or (not plan.updated_loans and not plan.removed_loans)
            ):
                raise CreditConflictError("返済対象のローンがありません。")
            target = (
                plan.updated_loans[0]
                if plan.updated_loans
                else plan.removed_loans[0]
            )
            current = self._loans.get(target.loan_id)
            if current is None:
                raise CreditConflictError("指定した公開ローンは存在しません。")
            return self.plan_repay(
                borrower_index=plan.actor_index,
                loan_id=current.loan_id,
                expected_revision=current.revision,
                payment=plan.resource_mutations[0].bundle,
                current_turn=plan.current_turn,
            )

        if plan.operation == CREDIT_ADVANCE:
            return self.plan_advance(current_turn=plan.current_turn)
        raise ResourceCreditError("未対応の信用操作です。")

    def _require_loan(self, loan_id: str, expected_revision: int) -> ResourceLoan:
        loan_id = _validate_loan_id(loan_id)
        expected_revision = _validate_int_range(
            expected_revision,
            label="expected_revision",
            minimum=1,
            maximum=MAX_REVISION,
        )
        loan = self._loans.get(loan_id)
        if loan is None:
            raise CreditConflictError("指定した公開ローンは存在しません。")
        if loan.revision != expected_revision:
            raise CreditConflictError("公開ローンのrevisionが更新されています。")
        return loan

    def to_document(self) -> dict[str, Any]:
        return {
            "format": RESOURCE_CREDIT_FORMAT,
            "version": RESOURCE_CREDIT_VERSION,
            "next_sequence": self._next_sequence,
            "open_loans": [loan.to_document() for loan in self.open_loans],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> CreditBook:
        """Restore an exact canonical v1 public authority state."""

        _expect_exact_keys(document, _DOCUMENT_KEYS, "公開信用台帳")
        if document["format"] != RESOURCE_CREDIT_FORMAT:
            raise ResourceCreditError("公開信用台帳formatが不正です。")
        if (
            type(document["version"]) is not int
            or document["version"] != RESOURCE_CREDIT_VERSION
        ):
            raise ResourceCreditError(
                f"公開信用台帳versionは{RESOURCE_CREDIT_VERSION}で指定してください。"
            )
        raw_loans = document["open_loans"]
        if not isinstance(raw_loans, list):
            raise ResourceCreditError("open_loansは配列で指定してください。")
        return cls(
            next_sequence=document["next_sequence"],
            open_loans=tuple(ResourceLoan.from_document(item) for item in raw_loans),
        )

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_document(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _with_delinquent(loan: ResourceLoan) -> ResourceLoan:
    return ResourceLoan(
        loan_id=loan.loan_id,
        borrower_index=loan.borrower_index,
        borrowed_resource=loan.borrowed_resource,
        opened_turn=loan.opened_turn,
        due_turn=loan.due_turn,
        status=LOAN_DELINQUENT,
        remaining_cards=DELINQUENT_INITIAL_CARDS,
        revision=_increment_revision(loan.revision),
    )


def _with_remaining(
    loan: ResourceLoan,
    *,
    remaining_cards: int,
    revision: int,
) -> ResourceLoan:
    return ResourceLoan(
        loan_id=loan.loan_id,
        borrower_index=loan.borrower_index,
        borrowed_resource=loan.borrowed_resource,
        opened_turn=loan.opened_turn,
        due_turn=loan.due_turn,
        status=LOAN_DELINQUENT,
        remaining_cards=remaining_cards,
        revision=revision,
    )


def _canonical_bundle(
    bundle: Mapping[ResourceType, int],
    *,
    label: str,
) -> dict[ResourceType, int]:
    if not isinstance(bundle, Mapping) or not bundle:
        raise ResourceCreditError(f"{label}は空でない資源mapで指定してください。")
    if len(bundle) > len(RESOURCE_TYPES) or any(
        type(resource) is not ResourceType or resource not in _RESOURCE_SET
        for resource in bundle
    ):
        raise ResourceCreditError(f"{label}には生産可能な5資源だけを指定してください。")
    return {
        resource: _validate_int_range(
            bundle[resource],
            label=f"{label}.{resource.name}",
            minimum=1,
            maximum=MAX_RESOURCE_COUNT,
        )
        for resource in RESOURCE_TYPES
        if resource in bundle
    }


def _bundle_total(bundle: Mapping[ResourceType, int]) -> int:
    return sum(bundle.values())


def _resource_from_document(value: Any, *, label: str) -> ResourceType:
    if type(value) is not str or value not in _RESOURCE_NAMES:
        raise ResourceCreditError(f"{label}は生産可能な資源名で指定してください。")
    return next(resource for resource in RESOURCE_TYPES if resource.name == value)


def _validate_resource(value: Any, *, label: str) -> ResourceType:
    if type(value) is not ResourceType or value not in _RESOURCE_SET:
        raise ResourceCreditError(f"{label}は生産可能な資源で指定してください。")
    return value


def _validate_player_index(value: Any, *, label: str) -> int:
    return _validate_int_range(
        value,
        label=label,
        minimum=0,
        maximum=MAX_PLAYER_INDEX,
    )


def _validate_turn(value: Any, *, label: str) -> int:
    return _validate_int_range(value, label=label, minimum=0, maximum=MAX_TURN)


def _validate_int_range(
    value: Any,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ResourceCreditError(
            f"{label}は{minimum}〜{maximum}の整数で指定してください。"
        )
    return value


def _validate_loan_id(value: Any) -> str:
    if type(value) is not str or _LOAN_ID_PATTERN.fullmatch(value) is None:
        raise ResourceCreditError("ローンIDが不正です。")
    return value


def _validate_next_sequence(value: Any) -> int:
    return _validate_int_range(
        value,
        label="next_sequence",
        minimum=0,
        maximum=EXHAUSTED_NEXT_SEQUENCE,
    )


def _sequence_from_loan_id(loan_id: str) -> int:
    return int(_validate_loan_id(loan_id).removeprefix("loan-"))


def _loan_id_for_sequence(sequence: int) -> str:
    _validate_int_range(
        sequence,
        label="loan sequence",
        minimum=0,
        maximum=MAX_LOAN_SEQUENCE,
    )
    return f"loan-{sequence:09d}"


def _increment_revision(revision: int) -> int:
    revision = _validate_int_range(
        revision,
        label="revision",
        minimum=1,
        maximum=MAX_REVISION,
    )
    if revision >= MAX_REVISION:
        raise CreditCapacityError("ローンrevisionをこれ以上更新できません。")
    return revision + 1


def _expect_exact_keys(
    document: Mapping[str, Any],
    expected: frozenset[str],
    label: str,
) -> None:
    if not isinstance(document, Mapping) or set(document) != expected:
        raise ResourceCreditError(
            f"{label}のキーは{', '.join(sorted(expected))}だけにしてください。"
        )


def _validate_immutable_tuple(
    value: Any,
    *,
    item_type: type,
    label: str,
) -> None:
    if type(value) is not tuple or any(type(item) is not item_type for item in value):
        raise ResourceCreditError(
            f"{label}は{item_type.__name__}のtupleで指定してください。"
        )
