"""Pure authority domain for the v1 persistent domestic-trade market.

The market intentionally knows nothing about ``Player`` or ``CatanGame``.
It validates and versions public orders, then emits resource-ledger mutations
and transfers for the authority integration layer to execute.  State updates
are functional: :meth:`TradeMarket.apply` either rejects a stale/invalid plan
or returns a new complete market state, leaving the original untouched.

V1 orders are exact, all-or-nothing AND offers.  Partial fills, alternatives,
and deferred contracts belong to later protocol versions.
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


TRADE_MARKET_FORMAT = "catan-trade-market"
TRADE_MARKET_VERSION = 1
MAX_OPEN_ORDERS_PER_SELLER = 4
MAX_OPEN_ORDERS = 16
MIN_ORDER_TTL = 1
MAX_ORDER_TTL = 8
MAX_PLAYER_INDEX = 3
MAX_TURN = 2_147_483_647
MAX_REVISION = 2_147_483_647
MAX_ORDER_SEQUENCE = 999_999_999
EXHAUSTED_NEXT_SEQUENCE = MAX_ORDER_SEQUENCE + 1

LEDGER_RESERVE = "reserve"
LEDGER_RELEASE = "release"
LEDGER_CONSUME = "consume"

MARKET_CREATE = "create"
MARKET_CANCEL = "cancel"
MARKET_EXPIRE = "expire"
MARKET_FILL = "fill"

_MARKET_OPERATIONS = frozenset(
    {MARKET_CREATE, MARKET_CANCEL, MARKET_EXPIRE, MARKET_FILL}
)
_LEDGER_OPERATIONS = frozenset(
    {LEDGER_RESERVE, LEDGER_RELEASE, LEDGER_CONSUME}
)
_DOCUMENT_KEYS = frozenset(
    {"format", "version", "next_sequence", "open_orders"}
)
_ORDER_DOCUMENT_KEYS = frozenset(
    {
        "order_id",
        "seller_index",
        "offer",
        "wanted",
        "created_turn",
        "expires_turn",
        "revision",
    }
)
_RESOURCE_SET = frozenset(RESOURCE_TYPES)
_RESOURCE_NAMES = frozenset(resource.name for resource in RESOURCE_TYPES)
_ORDER_ID_PATTERN = re.compile(r"market-[0-9]{9}\Z")
_RESERVATION_ID_PATTERN = re.compile(r"market:market-[0-9]{9}\Z")


class TradeMarketError(ValueError):
    """Raised when market input, state, or a mutation plan is invalid."""


class MarketConflictError(TradeMarketError):
    """Raised when a plan is stale or its target is no longer open."""


class MarketPermissionError(TradeMarketError):
    """Raised when an actor is not allowed to perform an operation."""


class MarketCapacityError(TradeMarketError):
    """Raised when an open-order capacity limit has been reached."""


@dataclass(frozen=True)
class MarketOrder:
    """One immutable exact-fill order published by a seller."""

    order_id: str
    seller_index: int
    offer: Mapping[ResourceType, int]
    wanted: Mapping[ResourceType, int]
    created_turn: int
    expires_turn: int
    revision: int = 1

    def __post_init__(self) -> None:
        _validate_order_id(self.order_id)
        _validate_player_index(self.seller_index, label="seller_index")
        offer = _canonical_bundle(self.offer, label="offer")
        wanted = _canonical_bundle(self.wanted, label="wanted")
        if set(offer).intersection(wanted):
            raise TradeMarketError("同じ資源をofferとwantedの両側に指定できません。")
        created_turn = _validate_turn(self.created_turn, label="created_turn")
        expires_turn = _validate_turn(self.expires_turn, label="expires_turn")
        ttl = expires_turn - created_turn
        if not MIN_ORDER_TTL <= ttl <= MAX_ORDER_TTL:
            raise TradeMarketError(
                f"注文期限は作成から {MIN_ORDER_TTL}〜{MAX_ORDER_TTL} 手番後にしてください。"
            )
        _validate_int_range(
            self.revision,
            label="revision",
            minimum=1,
            maximum=MAX_REVISION,
        )
        object.__setattr__(self, "offer", MappingProxyType(offer))
        object.__setattr__(self, "wanted", MappingProxyType(wanted))

    @property
    def reservation_id(self) -> str:
        """Stable ID used for the seller's ``ResourceLedger`` reservation."""

        return f"market:{self.order_id}"

    def is_expired(self, current_turn: int) -> bool:
        """Return whether the order is closed at the start of ``current_turn``."""

        return _validate_turn(current_turn, label="current_turn") >= self.expires_turn

    def to_document(self) -> dict[str, Any]:
        """Return a fresh canonical JSON-safe public order document."""

        return {
            "order_id": self.order_id,
            "seller_index": self.seller_index,
            "offer": _bundle_to_document(self.offer),
            "wanted": _bundle_to_document(self.wanted),
            "created_turn": self.created_turn,
            "expires_turn": self.expires_turn,
            "revision": self.revision,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> MarketOrder:
        _expect_exact_keys(document, _ORDER_DOCUMENT_KEYS, "公開注文")
        return cls(
            order_id=document["order_id"],
            seller_index=document["seller_index"],
            offer=_bundle_from_document(document["offer"], label="offer"),
            wanted=_bundle_from_document(document["wanted"], label="wanted"),
            created_turn=document["created_turn"],
            expires_turn=document["expires_turn"],
            revision=document["revision"],
        )


@dataclass(frozen=True)
class LedgerMutation:
    """Instruction for one player's existing ``ResourceLedger``."""

    operation: str
    player_index: int
    reservation_id: str
    bundle: Mapping[ResourceType, int]

    def __post_init__(self) -> None:
        if type(self.operation) is not str or self.operation not in _LEDGER_OPERATIONS:
            raise TradeMarketError("未対応の資源台帳操作です。")
        _validate_player_index(self.player_index, label="player_index")
        if (
            type(self.reservation_id) is not str
            or _RESERVATION_ID_PATTERN.fullmatch(self.reservation_id) is None
        ):
            raise TradeMarketError("市場予約IDが不正です。")
        object.__setattr__(
            self,
            "bundle",
            MappingProxyType(_canonical_bundle(self.bundle, label="bundle")),
        )


@dataclass(frozen=True)
class ResourceTransfer:
    """Exact resource movement the integration layer must perform."""

    from_player_index: int
    to_player_index: int
    bundle: Mapping[ResourceType, int]
    source_reservation_id: str | None = None

    def __post_init__(self) -> None:
        _validate_player_index(self.from_player_index, label="from_player_index")
        _validate_player_index(self.to_player_index, label="to_player_index")
        if self.from_player_index == self.to_player_index:
            raise TradeMarketError("資源移動元と移動先は別プレイヤーにしてください。")
        if self.source_reservation_id is not None and (
            type(self.source_reservation_id) is not str
            or _RESERVATION_ID_PATTERN.fullmatch(self.source_reservation_id) is None
        ):
            raise TradeMarketError("移動元の市場予約IDが不正です。")
        object.__setattr__(
            self,
            "bundle",
            MappingProxyType(_canonical_bundle(self.bundle, label="bundle")),
        )


@dataclass(frozen=True)
class MarketMutationPlan:
    """Authority-produced optimistic plan for one all-or-nothing mutation.

    Plans are process-local values, not network documents.  ``base_fingerprint``
    prevents a decision prepared against one market snapshot from being
    applied to a different snapshot.
    """

    operation: str
    base_fingerprint: str
    actor_index: int | None
    current_turn: int | None
    created_order: MarketOrder | None
    removed_orders: tuple[MarketOrder, ...]
    ledger_mutations: tuple[LedgerMutation, ...]
    transfers: tuple[ResourceTransfer, ...]


@dataclass(frozen=True)
class MarketMutationResult:
    """New authority state plus the exact resource work to commit with it."""

    market: TradeMarket
    operation: str
    created_order: MarketOrder | None
    removed_orders: tuple[MarketOrder, ...]
    ledger_mutations: tuple[LedgerMutation, ...]
    transfers: tuple[ResourceTransfer, ...]


class TradeMarket:
    """Immutable-snapshot authority state for open persistent trade orders."""

    __slots__ = ("_next_sequence", "_orders")

    def __init__(
        self,
        *,
        next_sequence: int = 0,
        open_orders: tuple[MarketOrder, ...] | list[MarketOrder] = (),
    ) -> None:
        self._next_sequence = _validate_next_sequence(next_sequence)
        if not isinstance(open_orders, (tuple, list)):
            raise TradeMarketError("open_ordersは注文配列で指定してください。")
        if len(open_orders) > MAX_OPEN_ORDERS:
            raise MarketCapacityError(
                f"公開注文は全体で {MAX_OPEN_ORDERS} 件以下にしてください。"
            )

        orders: dict[str, MarketOrder] = {}
        seller_counts: dict[int, int] = {}
        previous_id: str | None = None
        for order in open_orders:
            if type(order) is not MarketOrder:
                raise TradeMarketError("open_ordersにはMarketOrderだけを指定してください。")
            if previous_id is not None and order.order_id <= previous_id:
                raise TradeMarketError(
                    "open_ordersは重複のないorder_id昇順で指定してください。"
                )
            sequence = _sequence_from_order_id(order.order_id)
            if sequence >= self._next_sequence:
                raise TradeMarketError(
                    "公開注文のsequenceはnext_sequence未満にしてください。"
                )
            orders[order.order_id] = order
            seller_counts[order.seller_index] = (
                seller_counts.get(order.seller_index, 0) + 1
            )
            if seller_counts[order.seller_index] > MAX_OPEN_ORDERS_PER_SELLER:
                raise MarketCapacityError(
                    f"1人の公開注文は {MAX_OPEN_ORDERS_PER_SELLER} 件以下です。"
                )
            previous_id = order.order_id
        self._orders = orders

    @property
    def next_sequence(self) -> int:
        return self._next_sequence

    @property
    def open_orders(self) -> tuple[MarketOrder, ...]:
        return tuple(self._orders[order_id] for order_id in sorted(self._orders))

    def get_order(self, order_id: str) -> MarketOrder | None:
        validated_id = _validate_order_id(order_id)
        return self._orders.get(validated_id)

    def plan_create(
        self,
        *,
        seller_index: int,
        offer: Mapping[ResourceType, int],
        wanted: Mapping[ResourceType, int],
        current_turn: int,
        ttl: int,
    ) -> MarketMutationPlan:
        """Prepare one exact-fill order and its seller reservation."""

        seller_index = _validate_player_index(
            seller_index,
            label="seller_index",
        )
        current_turn = _validate_turn(current_turn, label="current_turn")
        ttl = _validate_int_range(
            ttl,
            label="ttl",
            minimum=MIN_ORDER_TTL,
            maximum=MAX_ORDER_TTL,
        )
        if current_turn > MAX_TURN - ttl:
            raise TradeMarketError("注文期限が手番上限を超えます。")
        if len(self._orders) >= MAX_OPEN_ORDERS:
            raise MarketCapacityError(
                f"公開注文は全体で {MAX_OPEN_ORDERS} 件までです。"
            )
        if sum(
            order.seller_index == seller_index for order in self._orders.values()
        ) >= MAX_OPEN_ORDERS_PER_SELLER:
            raise MarketCapacityError(
                f"1人の公開注文は {MAX_OPEN_ORDERS_PER_SELLER} 件までです。"
            )
        if self._next_sequence >= EXHAUSTED_NEXT_SEQUENCE:
            raise MarketCapacityError("市場注文IDをこれ以上発行できません。")

        order = MarketOrder(
            order_id=_order_id_for_sequence(self._next_sequence),
            seller_index=seller_index,
            offer=offer,
            wanted=wanted,
            created_turn=current_turn,
            expires_turn=current_turn + ttl,
            revision=1,
        )
        reservation = LedgerMutation(
            operation=LEDGER_RESERVE,
            player_index=seller_index,
            reservation_id=order.reservation_id,
            bundle=order.offer,
        )
        return MarketMutationPlan(
            operation=MARKET_CREATE,
            base_fingerprint=self.fingerprint(),
            actor_index=seller_index,
            current_turn=current_turn,
            created_order=order,
            removed_orders=(),
            ledger_mutations=(reservation,),
            transfers=(),
        )

    def plan_cancel(
        self,
        *,
        requester_index: int,
        order_id: str,
        expected_revision: int,
    ) -> MarketMutationPlan:
        """Prepare a seller-authorized cancellation and reservation release."""

        requester_index = _validate_player_index(
            requester_index,
            label="requester_index",
        )
        order = self._require_order(order_id, expected_revision)
        if order.seller_index != requester_index:
            raise MarketPermissionError("注文を取り消せるのは出品者だけです。")
        release = LedgerMutation(
            operation=LEDGER_RELEASE,
            player_index=order.seller_index,
            reservation_id=order.reservation_id,
            bundle=order.offer,
        )
        return MarketMutationPlan(
            operation=MARKET_CANCEL,
            base_fingerprint=self.fingerprint(),
            actor_index=requester_index,
            current_turn=None,
            created_order=None,
            removed_orders=(order,),
            ledger_mutations=(release,),
            transfers=(),
        )

    def plan_fill(
        self,
        *,
        buyer_index: int,
        order_id: str,
        expected_revision: int,
        current_turn: int,
    ) -> MarketMutationPlan:
        """Prepare an exact fill; available-resource checks remain external."""

        buyer_index = _validate_player_index(buyer_index, label="buyer_index")
        current_turn = _validate_turn(current_turn, label="current_turn")
        order = self._require_order(order_id, expected_revision)
        if order.seller_index == buyer_index:
            raise MarketPermissionError("自分の公開注文を購入することはできません。")
        if order.is_expired(current_turn):
            raise MarketConflictError("期限切れの公開注文は購入できません。")

        consume = LedgerMutation(
            operation=LEDGER_CONSUME,
            player_index=order.seller_index,
            reservation_id=order.reservation_id,
            bundle=order.offer,
        )
        transfers = (
            ResourceTransfer(
                from_player_index=order.seller_index,
                to_player_index=buyer_index,
                bundle=order.offer,
                source_reservation_id=order.reservation_id,
            ),
            ResourceTransfer(
                from_player_index=buyer_index,
                to_player_index=order.seller_index,
                bundle=order.wanted,
            ),
        )
        return MarketMutationPlan(
            operation=MARKET_FILL,
            base_fingerprint=self.fingerprint(),
            actor_index=buyer_index,
            current_turn=current_turn,
            created_order=None,
            removed_orders=(order,),
            ledger_mutations=(consume,),
            transfers=transfers,
        )

    def plan_expire(self, *, current_turn: int) -> MarketMutationPlan:
        """Prepare deterministic batch expiry at the start of a turn."""

        current_turn = _validate_turn(current_turn, label="current_turn")
        expired = tuple(
            order for order in self.open_orders if order.is_expired(current_turn)
        )
        releases = tuple(
            LedgerMutation(
                operation=LEDGER_RELEASE,
                player_index=order.seller_index,
                reservation_id=order.reservation_id,
                bundle=order.offer,
            )
            for order in expired
        )
        return MarketMutationPlan(
            operation=MARKET_EXPIRE,
            base_fingerprint=self.fingerprint(),
            actor_index=None,
            current_turn=current_turn,
            created_order=None,
            removed_orders=expired,
            ledger_mutations=releases,
            transfers=(),
        )

    def apply(self, plan: MarketMutationPlan) -> MarketMutationResult:
        """Validate and functionally apply one authority-produced plan.

        The returned ``market`` is the candidate to publish together with the
        listed ledger mutations/transfers.  On every error ``self`` is left
        unchanged, so the integration layer can validate its resource side
        before replacing the authoritative market reference.
        """

        if type(plan) is not MarketMutationPlan:
            raise TradeMarketError("MarketMutationPlanを指定してください。")
        if type(plan.operation) is not str or plan.operation not in _MARKET_OPERATIONS:
            raise TradeMarketError("未対応の市場操作です。")
        if plan.base_fingerprint != self.fingerprint():
            raise MarketConflictError("市場状態が更新されたため操作を再試行してください。")

        expected = self._rebuild_plan(plan)
        if plan != expected:
            raise MarketConflictError("市場操作planが現在状態と一致しません。")

        orders = dict(self._orders)
        next_sequence = self._next_sequence
        if plan.operation == MARKET_CREATE:
            assert plan.created_order is not None
            orders[plan.created_order.order_id] = plan.created_order
            next_sequence += 1
        else:
            for order in plan.removed_orders:
                del orders[order.order_id]

        ordered = tuple(orders[order_id] for order_id in sorted(orders))
        if next_sequence == self._next_sequence and ordered == self.open_orders:
            next_market = self
        else:
            next_market = TradeMarket(
                next_sequence=next_sequence,
                open_orders=ordered,
            )
        return MarketMutationResult(
            market=next_market,
            operation=plan.operation,
            created_order=plan.created_order,
            removed_orders=plan.removed_orders,
            ledger_mutations=plan.ledger_mutations,
            transfers=plan.transfers,
        )

    def _rebuild_plan(self, plan: MarketMutationPlan) -> MarketMutationPlan:
        if plan.operation == MARKET_CREATE:
            order = plan.created_order
            if order is None:
                raise MarketConflictError("作成対象の注文がありません。")
            return self.plan_create(
                seller_index=order.seller_index,
                offer=order.offer,
                wanted=order.wanted,
                current_turn=order.created_turn,
                ttl=order.expires_turn - order.created_turn,
            )
        if len(plan.removed_orders) > 0:
            order = plan.removed_orders[0]
        else:
            order = None
        if plan.operation == MARKET_CANCEL:
            if order is None or plan.actor_index is None:
                raise MarketConflictError("取消対象の注文がありません。")
            return self.plan_cancel(
                requester_index=plan.actor_index,
                order_id=order.order_id,
                expected_revision=order.revision,
            )
        if plan.operation == MARKET_FILL:
            if (
                order is None
                or plan.actor_index is None
                or plan.current_turn is None
            ):
                raise MarketConflictError("購入対象の注文がありません。")
            return self.plan_fill(
                buyer_index=plan.actor_index,
                order_id=order.order_id,
                expected_revision=order.revision,
                current_turn=plan.current_turn,
            )
        if plan.operation == MARKET_EXPIRE:
            if plan.current_turn is None:
                raise MarketConflictError("期限判定手番がありません。")
            return self.plan_expire(current_turn=plan.current_turn)
        raise TradeMarketError("未対応の市場操作です。")

    def _require_order(self, order_id: str, expected_revision: int) -> MarketOrder:
        order_id = _validate_order_id(order_id)
        expected_revision = _validate_int_range(
            expected_revision,
            label="expected_revision",
            minimum=1,
            maximum=MAX_REVISION,
        )
        order = self._orders.get(order_id)
        if order is None:
            raise MarketConflictError("指定した公開注文は存在しません。")
        if order.revision != expected_revision:
            raise MarketConflictError("公開注文のrevisionが更新されています。")
        return order

    def to_document(self) -> dict[str, Any]:
        """Return the deterministic authority document."""

        return {
            "format": TRADE_MARKET_FORMAT,
            "version": TRADE_MARKET_VERSION,
            "next_sequence": self._next_sequence,
            "open_orders": [order.to_document() for order in self.open_orders],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> TradeMarket:
        """Restore an exact v1 authority state, rejecting forward data."""

        _expect_exact_keys(document, _DOCUMENT_KEYS, "常設市場")
        if document["format"] != TRADE_MARKET_FORMAT:
            raise TradeMarketError("常設市場formatが不正です。")
        if (
            type(document["version"]) is not int
            or document["version"] != TRADE_MARKET_VERSION
        ):
            raise TradeMarketError(
                f"常設市場versionは {TRADE_MARKET_VERSION} で指定してください。"
            )
        raw_orders = document["open_orders"]
        if not isinstance(raw_orders, list):
            raise TradeMarketError("open_ordersは配列で指定してください。")
        orders = tuple(MarketOrder.from_document(item) for item in raw_orders)
        return cls(
            next_sequence=document["next_sequence"],
            open_orders=orders,
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


def _canonical_bundle(
    bundle: Mapping[ResourceType, int],
    *,
    label: str,
) -> dict[ResourceType, int]:
    if not isinstance(bundle, Mapping) or not bundle:
        raise TradeMarketError(f"{label}は空でない資源mapで指定してください。")
    if len(bundle) > len(RESOURCE_TYPES) or any(
        type(resource) is not ResourceType or resource not in _RESOURCE_SET
        for resource in bundle
    ):
        raise TradeMarketError(
            f"{label}には生産可能な5資源だけを指定してください。"
        )
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


def _bundle_to_document(bundle: Mapping[ResourceType, int]) -> dict[str, int]:
    canonical = _canonical_bundle(bundle, label="bundle")
    return {
        resource.name: canonical[resource]
        for resource in RESOURCE_TYPES
        if resource in canonical
    }


def _bundle_from_document(value: Any, *, label: str) -> dict[ResourceType, int]:
    if not isinstance(value, Mapping) or not value:
        raise TradeMarketError(f"{label}は空でないobjectにしてください。")
    if len(value) > len(RESOURCE_TYPES) or any(
        type(name) is not str or name not in _RESOURCE_NAMES for name in value
    ):
        raise TradeMarketError(
            f"{label}には生産可能な5資源だけを指定してください。"
        )
    return {
        resource: _validate_int_range(
            value[resource.name],
            label=f"{label}.{resource.name}",
            minimum=1,
            maximum=MAX_RESOURCE_COUNT,
        )
        for resource in RESOURCE_TYPES
        if resource.name in value
    }


def _validate_player_index(value: Any, *, label: str) -> int:
    return _validate_int_range(
        value,
        label=label,
        minimum=0,
        maximum=MAX_PLAYER_INDEX,
    )


def _validate_turn(value: Any, *, label: str) -> int:
    return _validate_int_range(value, label=label, minimum=0, maximum=MAX_TURN)


def _validate_next_sequence(value: Any) -> int:
    return _validate_int_range(
        value,
        label="next_sequence",
        minimum=0,
        maximum=EXHAUSTED_NEXT_SEQUENCE,
    )


def _validate_int_range(
    value: Any,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise TradeMarketError(
            f"{label}は {minimum} 以上 {maximum} 以下の整数で指定してください。"
        )
    return value


def _order_id_for_sequence(sequence: int) -> str:
    _validate_int_range(
        sequence,
        label="order sequence",
        minimum=0,
        maximum=MAX_ORDER_SEQUENCE,
    )
    return f"market-{sequence:09d}"


def _validate_order_id(value: Any) -> str:
    if type(value) is not str or _ORDER_ID_PATTERN.fullmatch(value) is None:
        raise TradeMarketError("order_idは安全な市場発行IDで指定してください。")
    return value


def _sequence_from_order_id(order_id: str) -> int:
    validated = _validate_order_id(order_id)
    return int(validated.removeprefix("market-"))


def _expect_exact_keys(value: Any, expected: frozenset[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise TradeMarketError(f"{label}の項目が不正です。")


__all__ = (
    "EXHAUSTED_NEXT_SEQUENCE",
    "LEDGER_CONSUME",
    "LEDGER_RELEASE",
    "LEDGER_RESERVE",
    "MARKET_CANCEL",
    "MARKET_CREATE",
    "MARKET_EXPIRE",
    "MARKET_FILL",
    "MAX_OPEN_ORDERS",
    "MAX_OPEN_ORDERS_PER_SELLER",
    "MAX_ORDER_TTL",
    "MIN_ORDER_TTL",
    "MarketCapacityError",
    "MarketConflictError",
    "MarketMutationPlan",
    "MarketMutationResult",
    "MarketOrder",
    "MarketPermissionError",
    "ResourceTransfer",
    "TRADE_MARKET_FORMAT",
    "TRADE_MARKET_VERSION",
    "TradeMarket",
    "TradeMarketError",
    "LedgerMutation",
)
