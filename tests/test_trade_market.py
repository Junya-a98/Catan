from dataclasses import replace
import json

import pytest

from game.resource_ledger import MAX_RESOURCE_COUNT
from game.resources import ResourceType
from game.trade_market import (
    LEDGER_CONSUME,
    LEDGER_RELEASE,
    LEDGER_RESERVE,
    MARKET_CANCEL,
    MARKET_CREATE,
    MARKET_EXPIRE,
    MARKET_FILL,
    MAX_OPEN_ORDERS,
    MAX_OPEN_ORDERS_PER_SELLER,
    MAX_ORDER_TTL,
    MIN_ORDER_TTL,
    MarketCapacityError,
    MarketConflictError,
    MarketPermissionError,
    TRADE_MARKET_FORMAT,
    TRADE_MARKET_VERSION,
    TradeMarket,
    TradeMarketError,
)


WOOD = ResourceType.WOOD
SHEEP = ResourceType.SHEEP
WHEAT = ResourceType.WHEAT
BRICK = ResourceType.BRICK
ORE = ResourceType.ORE


def create_order(
    market,
    *,
    seller=0,
    offer=None,
    wanted=None,
    turn=0,
    ttl=3,
):
    plan = market.plan_create(
        seller_index=seller,
        offer=offer or {WOOD: 1},
        wanted=wanted or {ORE: 1},
        current_turn=turn,
        ttl=ttl,
    )
    result = market.apply(plan)
    return result.market, result


def test_empty_market_has_exact_versioned_deterministic_document():
    market = TradeMarket()

    assert market.next_sequence == 0
    assert market.open_orders == ()
    assert market.to_document() == {
        "format": TRADE_MARKET_FORMAT,
        "version": TRADE_MARKET_VERSION,
        "next_sequence": 0,
        "open_orders": [],
    }
    assert json.loads(market.canonical_json()) == market.to_document()
    assert market.fingerprint() == TradeMarket().fingerprint()


def test_create_is_functional_and_returns_one_ledger_reservation_plan():
    market = TradeMarket()
    plan = market.plan_create(
        seller_index=2,
        offer={WOOD: 2, BRICK: 1},
        wanted={ORE: 1},
        current_turn=7,
        ttl=4,
    )

    assert plan.operation == MARKET_CREATE
    assert plan.created_order.order_id == "market-000000000"
    assert plan.created_order.seller_index == 2
    assert dict(plan.created_order.offer) == {WOOD: 2, BRICK: 1}
    assert dict(plan.created_order.wanted) == {ORE: 1}
    assert plan.created_order.created_turn == 7
    assert plan.created_order.expires_turn == 11
    assert plan.created_order.revision == 1
    assert plan.removed_orders == ()
    assert plan.transfers == ()
    assert len(plan.ledger_mutations) == 1
    reservation = plan.ledger_mutations[0]
    assert reservation.operation == LEDGER_RESERVE
    assert reservation.player_index == 2
    assert reservation.reservation_id == "market:market-000000000"
    assert dict(reservation.bundle) == {WOOD: 2, BRICK: 1}

    # Planning and applying never mutate the source snapshot.
    assert market.to_document()["open_orders"] == []
    result = market.apply(plan)
    assert result.operation == MARKET_CREATE
    assert result.created_order == plan.created_order
    assert result.market.next_sequence == 1
    assert result.market.open_orders == (plan.created_order,)
    assert market.next_sequence == 0

    next_plan = result.market.plan_create(
        seller_index=1,
        offer={SHEEP: 1},
        wanted={WHEAT: 1},
        current_turn=8,
        ttl=1,
    )
    assert next_plan.created_order.order_id == "market-000000001"


def test_order_bundles_are_copied_canonical_and_immutable():
    offer = {BRICK: 1, WOOD: 2}
    wanted = {ORE: 1}
    plan = TradeMarket().plan_create(
        seller_index=0,
        offer=offer,
        wanted=wanted,
        current_turn=0,
        ttl=1,
    )
    offer[WOOD] = 19
    wanted[ORE] = 19

    assert plan.created_order.to_document()["offer"] == {"WOOD": 2, "BRICK": 1}
    assert plan.created_order.to_document()["wanted"] == {"ORE": 1}
    with pytest.raises(TypeError):
        plan.created_order.offer[WOOD] = 3


@pytest.mark.parametrize(
    ("offer", "wanted"),
    [
        ({}, {ORE: 1}),
        ({WOOD: 1}, {}),
        ({WOOD: 1}, {WOOD: 2}),
        ({ResourceType.DESERT: 1}, {ORE: 1}),
        ({"WOOD": 1}, {ORE: 1}),
        ({WOOD: True}, {ORE: 1}),
        ({WOOD: False}, {ORE: 1}),
        ({WOOD: 0}, {ORE: 1}),
        ({WOOD: -1}, {ORE: 1}),
        ({WOOD: 1.0}, {ORE: 1}),
        ({WOOD: MAX_RESOURCE_COUNT + 1}, {ORE: 1}),
    ],
)
def test_create_rejects_invalid_or_self_cancelling_bundles(offer, wanted):
    market = TradeMarket()
    before = market.to_document()

    with pytest.raises(TradeMarketError):
        market.plan_create(
            seller_index=0,
            offer=offer,
            wanted=wanted,
            current_turn=0,
            ttl=1,
        )

    assert market.to_document() == before


@pytest.mark.parametrize("ttl", [True, False, 0, -1, 1.0, MAX_ORDER_TTL + 1])
def test_ttl_is_strictly_one_through_eight(ttl):
    with pytest.raises(TradeMarketError):
        TradeMarket().plan_create(
            seller_index=0,
            offer={WOOD: 1},
            wanted={ORE: 1},
            current_turn=0,
            ttl=ttl,
        )


def test_minimum_and_maximum_ttl_are_accepted():
    first = TradeMarket().plan_create(
        seller_index=0,
        offer={WOOD: 1},
        wanted={ORE: 1},
        current_turn=2,
        ttl=MIN_ORDER_TTL,
    )
    last = TradeMarket().plan_create(
        seller_index=0,
        offer={WOOD: 1},
        wanted={ORE: 1},
        current_turn=2,
        ttl=MAX_ORDER_TTL,
    )
    assert first.created_order.expires_turn == 3
    assert last.created_order.expires_turn == 10


def test_per_seller_and_global_capacity_are_enforced_without_mutation():
    market = TradeMarket()
    for seller in range(4):
        for offset in range(MAX_OPEN_ORDERS_PER_SELLER):
            market, _ = create_order(
                market,
                seller=seller,
                offer={WOOD: 1},
                wanted={ORE: 1},
                turn=offset,
            )

    assert len(market.open_orders) == MAX_OPEN_ORDERS
    before = market.to_document()
    with pytest.raises(MarketCapacityError):
        market.plan_create(
            seller_index=0,
            offer={SHEEP: 1},
            wanted={WHEAT: 1},
            current_turn=5,
            ttl=2,
        )
    assert market.to_document() == before

    one_seller = TradeMarket()
    for offset in range(MAX_OPEN_ORDERS_PER_SELLER):
        one_seller, _ = create_order(one_seller, seller=0, turn=offset)
    with pytest.raises(MarketCapacityError):
        one_seller.plan_create(
            seller_index=0,
            offer={SHEEP: 1},
            wanted={WHEAT: 1},
            current_turn=5,
            ttl=2,
        )


def test_cancel_requires_seller_and_matching_revision_then_releases_reservation():
    market, created = create_order(market=TradeMarket(), seller=1)
    order = created.created_order
    before = market.to_document()

    with pytest.raises(MarketPermissionError):
        market.plan_cancel(
            requester_index=2,
            order_id=order.order_id,
            expected_revision=1,
        )
    with pytest.raises(MarketConflictError):
        market.plan_cancel(
            requester_index=1,
            order_id=order.order_id,
            expected_revision=2,
        )
    assert market.to_document() == before

    plan = market.plan_cancel(
        requester_index=1,
        order_id=order.order_id,
        expected_revision=1,
    )
    assert plan.operation == MARKET_CANCEL
    assert plan.removed_orders == (order,)
    assert plan.ledger_mutations[0].operation == LEDGER_RELEASE
    assert plan.transfers == ()

    result = market.apply(plan)
    assert result.market.open_orders == ()
    assert result.market.next_sequence == 1
    assert market.open_orders == (order,)


def test_fill_is_exact_all_or_nothing_and_describes_both_resource_movements():
    market, created = create_order(
        TradeMarket(),
        seller=0,
        offer={WOOD: 2, BRICK: 1},
        wanted={WHEAT: 1, ORE: 1},
        turn=4,
        ttl=3,
    )
    order = created.created_order
    plan = market.plan_fill(
        buyer_index=3,
        order_id=order.order_id,
        expected_revision=1,
        current_turn=6,
    )

    assert plan.operation == MARKET_FILL
    assert plan.removed_orders == (order,)
    assert len(plan.ledger_mutations) == 1
    assert plan.ledger_mutations[0].operation == LEDGER_CONSUME
    assert dict(plan.ledger_mutations[0].bundle) == {WOOD: 2, BRICK: 1}
    assert len(plan.transfers) == 2
    seller_to_buyer, buyer_to_seller = plan.transfers
    assert (seller_to_buyer.from_player_index, seller_to_buyer.to_player_index) == (
        0,
        3,
    )
    assert dict(seller_to_buyer.bundle) == {WOOD: 2, BRICK: 1}
    assert seller_to_buyer.source_reservation_id == order.reservation_id
    assert (buyer_to_seller.from_player_index, buyer_to_seller.to_player_index) == (
        3,
        0,
    )
    assert dict(buyer_to_seller.bundle) == {WHEAT: 1, ORE: 1}
    assert buyer_to_seller.source_reservation_id is None

    result = market.apply(plan)
    assert result.market.open_orders == ()
    assert result.transfers == plan.transfers


def test_fill_rejects_self_purchase_expiry_unknown_order_and_bad_revision():
    market, created = create_order(TradeMarket(), seller=2, turn=8, ttl=2)
    order = created.created_order
    before = market.to_document()

    with pytest.raises(MarketPermissionError):
        market.plan_fill(
            buyer_index=2,
            order_id=order.order_id,
            expected_revision=1,
            current_turn=8,
        )
    with pytest.raises(MarketConflictError):
        market.plan_fill(
            buyer_index=1,
            order_id=order.order_id,
            expected_revision=2,
            current_turn=8,
        )
    with pytest.raises(MarketConflictError):
        market.plan_fill(
            buyer_index=1,
            order_id="market-000000999",
            expected_revision=1,
            current_turn=8,
        )
    with pytest.raises(MarketConflictError):
        market.plan_fill(
            buyer_index=1,
            order_id=order.order_id,
            expected_revision=1,
            current_turn=10,
        )
    assert market.to_document() == before


def test_expire_removes_all_due_orders_in_id_order_and_releases_each_reservation():
    market, first = create_order(TradeMarket(), seller=2, turn=0, ttl=2)
    market, second = create_order(market, seller=0, turn=1, ttl=1)
    market, third = create_order(market, seller=3, turn=1, ttl=3)

    plan = market.plan_expire(current_turn=2)
    assert plan.operation == MARKET_EXPIRE
    assert plan.removed_orders == (first.created_order, second.created_order)
    assert [item.operation for item in plan.ledger_mutations] == [
        LEDGER_RELEASE,
        LEDGER_RELEASE,
    ]
    assert [item.player_index for item in plan.ledger_mutations] == [2, 0]

    result = market.apply(plan)
    assert result.market.open_orders == (third.created_order,)
    assert result.market.next_sequence == 3

    noop = result.market.plan_expire(current_turn=2)
    noop_result = result.market.apply(noop)
    assert noop.removed_orders == ()
    assert noop_result.market is result.market


def test_stale_plans_and_tampered_plans_are_rejected_atomically():
    original = TradeMarket()
    first_plan = original.plan_create(
        seller_index=0,
        offer={WOOD: 1},
        wanted={ORE: 1},
        current_turn=0,
        ttl=2,
    )
    advanced = original.apply(first_plan).market
    before = advanced.to_document()

    with pytest.raises(MarketConflictError):
        advanced.apply(first_plan)
    assert advanced.to_document() == before

    cancel = advanced.plan_cancel(
        requester_index=0,
        order_id=first_plan.created_order.order_id,
        expected_revision=1,
    )
    tampered = replace(cancel, ledger_mutations=())
    with pytest.raises(MarketConflictError):
        advanced.apply(tampered)
    assert advanced.to_document() == before


def test_round_trip_is_exact_sorted_detached_and_json_safe():
    market, _ = create_order(
        TradeMarket(),
        seller=3,
        offer={BRICK: 1, WOOD: 2},
        wanted={ORE: 1},
        turn=4,
        ttl=2,
    )
    market, _ = create_order(
        market,
        seller=0,
        offer={SHEEP: 1},
        wanted={WHEAT: 1},
        turn=5,
        ttl=8,
    )
    document = market.to_document()
    decoded = json.loads(json.dumps(document, allow_nan=False))
    restored = TradeMarket.from_document(decoded)

    assert restored.to_document() == document
    assert restored.canonical_json() == market.canonical_json()
    assert restored.fingerprint() == market.fingerprint()
    assert [item["order_id"] for item in document["open_orders"]] == [
        "market-000000000",
        "market-000000001",
    ]
    assert document["open_orders"][0]["offer"] == {"WOOD": 2, "BRICK": 1}

    document["open_orders"][0]["offer"]["WOOD"] = 19
    assert market.open_orders[0].offer[WOOD] == 2


def canonical_document():
    market, _ = create_order(TradeMarket(), seller=0, turn=1, ttl=2)
    return market.to_document()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.pop("format"),
        lambda doc: doc.__setitem__("extra", True),
        lambda doc: doc.__setitem__("format", "wrong"),
        lambda doc: doc.__setitem__("version", True),
        lambda doc: doc.__setitem__("version", 2),
        lambda doc: doc.__setitem__("next_sequence", True),
        lambda doc: doc.__setitem__("open_orders", {}),
        lambda doc: doc["open_orders"][0].__setitem__("extra", 1),
        lambda doc: doc["open_orders"][0].__setitem__("order_id", "unsafe id"),
        lambda doc: doc["open_orders"][0].__setitem__("seller_index", True),
        lambda doc: doc["open_orders"][0].__setitem__("created_turn", True),
        lambda doc: doc["open_orders"][0].__setitem__("expires_turn", 20),
        lambda doc: doc["open_orders"][0].__setitem__("revision", True),
        lambda doc: doc["open_orders"][0]["offer"].__setitem__("GOLD", 1),
        lambda doc: doc["open_orders"][0]["offer"].__setitem__("WOOD", True),
        lambda doc: doc["open_orders"][0].__setitem__("wanted", {"WOOD": 1}),
    ],
)
def test_document_rejects_missing_extra_bool_unknown_and_invalid_fields(mutate):
    document = canonical_document()
    mutate(document)
    with pytest.raises(TradeMarketError):
        TradeMarket.from_document(document)


def test_document_rejects_duplicate_unsorted_future_and_over_capacity_orders():
    document = canonical_document()
    duplicate = json.loads(json.dumps(document))
    duplicate["open_orders"].append(duplicate["open_orders"][0])
    with pytest.raises(TradeMarketError):
        TradeMarket.from_document(duplicate)

    two_orders, _ = create_order(
        TradeMarket.from_document(document),
        seller=1,
        turn=2,
    )
    unsorted = two_orders.to_document()
    unsorted["open_orders"].reverse()
    with pytest.raises(TradeMarketError):
        TradeMarket.from_document(unsorted)

    future = canonical_document()
    future["next_sequence"] = 0
    with pytest.raises(TradeMarketError):
        TradeMarket.from_document(future)

    too_many = TradeMarket().to_document()
    source = canonical_document()["open_orders"][0]
    too_many["next_sequence"] = MAX_OPEN_ORDERS + 1
    too_many["open_orders"] = []
    for sequence in range(MAX_OPEN_ORDERS + 1):
        order = json.loads(json.dumps(source))
        order["order_id"] = f"market-{sequence:09d}"
        order["seller_index"] = sequence % 4
        too_many["open_orders"].append(order)
    with pytest.raises(MarketCapacityError):
        TradeMarket.from_document(too_many)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seller_index", True),
        ("seller_index", -1),
        ("seller_index", 4),
        ("current_turn", True),
        ("current_turn", -1),
        ("current_turn", 1.0),
    ],
)
def test_create_rejects_bool_and_out_of_range_authority_fields(field, value):
    kwargs = {
        "seller_index": 0,
        "offer": {WOOD: 1},
        "wanted": {ORE: 1},
        "current_turn": 0,
        "ttl": 2,
    }
    kwargs[field] = value
    with pytest.raises(TradeMarketError):
        TradeMarket().plan_create(**kwargs)
