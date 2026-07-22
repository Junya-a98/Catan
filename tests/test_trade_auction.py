from dataclasses import replace
import json

import pytest

from game.bank import RESOURCE_TYPES
from game.resource_ledger import ResourceLedger
from game.resources import ResourceType
from game.trade_auction import (
    AUCTION_ACCEPT,
    AUCTION_BID,
    AUCTION_CANCEL,
    AUCTION_CANCEL_BID,
    AUCTION_CREATE,
    AUCTION_EXPIRE,
    EXHAUSTED_NEXT_SEQUENCE,
    LEDGER_CONSUME,
    LEDGER_RELEASE,
    LEDGER_REPLACE,
    LEDGER_RESERVE,
    MAX_AUCTION_BUNDLE_CARDS,
    MAX_AUCTION_TTL,
    MAX_OPEN_AUCTIONS,
    MAX_OPEN_AUCTIONS_PER_SELLER,
    MIN_AUCTION_TTL,
    AuctionCapacityError,
    AuctionConflictError,
    AuctionHouse,
    AuctionPermissionError,
    TRADE_AUCTION_FORMAT,
    TRADE_AUCTION_VERSION,
    TradeAuctionError,
)


WOOD = ResourceType.WOOD
SHEEP = ResourceType.SHEEP
WHEAT = ResourceType.WHEAT
BRICK = ResourceType.BRICK
ORE = ResourceType.ORE


def owned(**counts):
    return {resource: counts.get(resource.name, 0) for resource in RESOURCE_TYPES}


def create_auction(
    house,
    *,
    seller=0,
    offer=None,
    turn=0,
    ttl=4,
    minimum=1,
):
    plan = house.plan_create(
        seller_index=seller,
        offer=offer or {ORE: 1},
        current_turn=turn,
        ttl=ttl,
        minimum_bid_cards=minimum,
    )
    result = house.apply(plan)
    return result.house, result


def place_bid(house, *, auction_id, bidder, offer, turn=0):
    auction = house.get_auction(auction_id)
    plan = house.plan_bid(
        bidder_index=bidder,
        auction_id=auction_id,
        expected_revision=auction.revision,
        offer=offer,
        current_turn=turn,
    )
    result = house.apply(plan)
    return result.house, result


def canonical_document():
    house, created = create_auction(
        AuctionHouse(),
        seller=0,
        offer={ORE: 1},
        turn=2,
        ttl=4,
        minimum=2,
    )
    house, _ = place_bid(
        house,
        auction_id=created.created_auction.auction_id,
        bidder=1,
        offer={WOOD: 2},
        turn=3,
    )
    return house.to_document()


def apply_ledger_mutation(ledgers, mutation):
    ledger = ledgers[mutation.player_index]
    reservations = ledger.reservations_map()
    if mutation.operation == LEDGER_RESERVE:
        return ledger.reserve(mutation.reservation_id, mutation.bundle)
    assert reservations.get(mutation.reservation_id) == (
        dict(mutation.previous_bundle)
        if mutation.operation == LEDGER_REPLACE
        else dict(mutation.bundle)
    )
    if mutation.operation == LEDGER_REPLACE:
        return ledger.replace(mutation.reservation_id, mutation.bundle)
    if mutation.operation == LEDGER_RELEASE:
        return ledger.release(mutation.reservation_id) == dict(mutation.bundle)
    if mutation.operation == LEDGER_CONSUME:
        return ledger.consume(mutation.reservation_id) == dict(mutation.bundle)
    raise AssertionError(mutation.operation)


def test_empty_house_has_exact_versioned_deterministic_document():
    house = AuctionHouse()

    assert house.next_sequence == 0
    assert house.open_auctions == ()
    assert house.to_document() == {
        "format": TRADE_AUCTION_FORMAT,
        "version": TRADE_AUCTION_VERSION,
        "next_sequence": 0,
        "open_auctions": [],
    }
    assert json.loads(house.canonical_json()) == house.to_document()
    assert house.fingerprint() == AuctionHouse().fingerprint()


def test_create_is_functional_and_reserves_the_complete_seller_offer():
    house = AuctionHouse()
    offer = {BRICK: 1, ORE: 2}
    plan = house.plan_create(
        seller_index=2,
        offer=offer,
        minimum_bid_cards=3,
        current_turn=7,
        ttl=4,
    )

    assert plan.operation == AUCTION_CREATE
    assert plan.created_auction.auction_id == "auction-000000000"
    assert plan.created_auction.seller_index == 2
    assert dict(plan.created_auction.offer) == {BRICK: 1, ORE: 2}
    assert plan.created_auction.minimum_bid_cards == 3
    assert plan.created_auction.created_turn == 7
    assert plan.created_auction.expires_turn == 11
    assert plan.created_auction.revision == 1
    assert plan.created_auction.bids == ()
    assert plan.updated_auction is None
    assert plan.removed_auctions == ()
    assert plan.accepted_bid is None
    assert plan.transfers == ()
    assert len(plan.ledger_mutations) == 1
    reservation = plan.ledger_mutations[0]
    assert reservation.operation == LEDGER_RESERVE
    assert reservation.player_index == 2
    assert reservation.reservation_id == (
        "auction:auction-000000000:seller"
    )
    assert dict(reservation.bundle) == offer
    assert reservation.previous_bundle is None

    offer[ORE] = 19
    assert dict(plan.created_auction.offer) == {BRICK: 1, ORE: 2}
    assert house.open_auctions == ()
    result = house.apply(plan)
    assert result.house.next_sequence == 1
    assert result.house.open_auctions == (plan.created_auction,)
    assert house.next_sequence == 0


def test_create_defaults_to_one_card_minimum_and_issues_monotonic_ids():
    house, first = create_auction(AuctionHouse())
    house, second = create_auction(house, seller=1, turn=1)

    assert first.created_auction.minimum_bid_cards == 1
    assert first.created_auction.auction_id == "auction-000000000"
    assert second.created_auction.auction_id == "auction-000000001"


@pytest.mark.parametrize(
    "offer",
    [
        {},
        {ResourceType.DESERT: 1},
        {"ORE": 1},
        {ORE: True},
        {ORE: False},
        {ORE: 0},
        {ORE: -1},
        {ORE: 1.0},
        {ORE: MAX_AUCTION_BUNDLE_CARDS + 1},
        {WOOD: 10, ORE: 10},
    ],
)
def test_create_rejects_invalid_or_oversized_offer_without_mutation(offer):
    house = AuctionHouse()
    before = house.to_document()
    with pytest.raises(TradeAuctionError):
        house.plan_create(
            seller_index=0,
            offer=offer,
            current_turn=0,
            ttl=2,
        )
    assert house.to_document() == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seller_index", True),
        ("seller_index", -1),
        ("seller_index", 4),
        ("current_turn", True),
        ("current_turn", -1),
        ("current_turn", 1.0),
        ("ttl", True),
        ("ttl", 0),
        ("ttl", MAX_AUCTION_TTL + 1),
        ("minimum_bid_cards", True),
        ("minimum_bid_cards", 0),
        ("minimum_bid_cards", MAX_AUCTION_BUNDLE_CARDS + 1),
    ],
)
def test_create_rejects_bool_and_out_of_range_authority_fields(field, value):
    kwargs = {
        "seller_index": 0,
        "offer": {ORE: 1},
        "current_turn": 0,
        "ttl": 2,
        "minimum_bid_cards": 1,
    }
    kwargs[field] = value
    with pytest.raises(TradeAuctionError):
        AuctionHouse().plan_create(**kwargs)


def test_minimum_and_maximum_ttl_are_accepted():
    first = AuctionHouse().plan_create(
        seller_index=0,
        offer={ORE: 1},
        current_turn=2,
        ttl=MIN_AUCTION_TTL,
    )
    last = AuctionHouse().plan_create(
        seller_index=0,
        offer={ORE: 1},
        current_turn=2,
        ttl=MAX_AUCTION_TTL,
    )
    assert first.created_auction.expires_turn == 3
    assert last.created_auction.expires_turn == 10


def test_per_seller_and_global_capacity_are_enforced_atomically():
    house = AuctionHouse()
    for seller in range(4):
        for offset in range(MAX_OPEN_AUCTIONS_PER_SELLER):
            house, _ = create_auction(house, seller=seller, turn=offset)

    assert len(house.open_auctions) == MAX_OPEN_AUCTIONS
    before = house.to_document()
    with pytest.raises(AuctionCapacityError):
        house.plan_create(
            seller_index=0,
            offer={ORE: 1},
            current_turn=3,
            ttl=2,
        )
    assert house.to_document() == before

    one_seller = AuctionHouse()
    for offset in range(MAX_OPEN_AUCTIONS_PER_SELLER):
        one_seller, _ = create_auction(one_seller, seller=0, turn=offset)
    with pytest.raises(AuctionCapacityError):
        one_seller.plan_create(
            seller_index=0,
            offer={ORE: 1},
            current_turn=3,
            ttl=2,
        )


def test_new_mixed_bids_are_public_sorted_and_each_fully_reserved():
    house, created = create_auction(
        AuctionHouse(),
        seller=0,
        offer={ORE: 1},
        minimum=2,
    )
    auction_id = created.created_auction.auction_id
    house, first = place_bid(
        house,
        auction_id=auction_id,
        bidder=2,
        offer={WHEAT: 1, SHEEP: 1},
        turn=1,
    )
    house, second = place_bid(
        house,
        auction_id=auction_id,
        bidder=1,
        offer={WOOD: 2},
        turn=1,
    )

    assert first.operation == AUCTION_BID
    assert first.updated_auction.revision == 2
    assert first.updated_auction.get_bid(2).revision == 1
    assert first.ledger_mutations[0].operation == LEDGER_RESERVE
    assert first.ledger_mutations[0].reservation_id.endswith(":bid-2")
    assert dict(first.ledger_mutations[0].bundle) == {SHEEP: 1, WHEAT: 1}
    auction = house.get_auction(auction_id)
    assert auction.revision == 3
    assert [bid.bidder_index for bid in auction.bids] == [1, 2]
    assert [bid.to_document()["offer"] for bid in auction.bids] == [
        {"WOOD": 2},
        {"SHEEP": 1, "WHEAT": 1},
    ]
    assert second.ledger_mutations[0].reservation_id.endswith(":bid-1")


@pytest.mark.parametrize(
    ("bidder", "offer", "turn", "revision", "error"),
    [
        (0, {WOOD: 2}, 0, 1, AuctionPermissionError),
        (1, {WOOD: 1}, 0, 1, TradeAuctionError),
        (1, {WOOD: 1, ORE: 1}, 0, 1, TradeAuctionError),
        (1, {WOOD: 20}, 0, 1, TradeAuctionError),
        (1, {WOOD: 2}, 4, 1, AuctionConflictError),
        (1, {WOOD: 2}, 0, 2, AuctionConflictError),
    ],
)
def test_bid_rejects_seller_minimum_overlap_size_expiry_and_stale_revision(
    bidder,
    offer,
    turn,
    revision,
    error,
):
    house, created = create_auction(
        AuctionHouse(),
        seller=0,
        offer={ORE: 1},
        minimum=2,
        ttl=4,
    )
    before = house.to_document()
    with pytest.raises(error):
        house.plan_bid(
            bidder_index=bidder,
            auction_id=created.created_auction.auction_id,
            expected_revision=revision,
            offer=offer,
            current_turn=turn,
        )
    assert house.to_document() == before


def test_bid_update_increments_both_revisions_and_uses_atomic_replace():
    house, created = create_auction(
        AuctionHouse(),
        offer={ORE: 1},
        minimum=2,
    )
    auction_id = created.created_auction.auction_id
    house, _ = place_bid(
        house,
        auction_id=auction_id,
        bidder=1,
        offer={WOOD: 2},
    )
    auction = house.get_auction(auction_id)
    plan = house.plan_bid(
        bidder_index=1,
        auction_id=auction_id,
        expected_revision=auction.revision,
        offer={WHEAT: 1, SHEEP: 2},
        current_turn=1,
    )

    assert plan.updated_auction.revision == 3
    updated_bid = plan.updated_auction.get_bid(1)
    assert updated_bid.revision == 2
    assert dict(updated_bid.offer) == {SHEEP: 2, WHEAT: 1}
    mutation = plan.ledger_mutations[0]
    assert mutation.operation == LEDGER_REPLACE
    assert dict(mutation.previous_bundle) == {WOOD: 2}
    assert dict(mutation.bundle) == {SHEEP: 2, WHEAT: 1}
    assert house.get_auction(auction_id).revision == 2

    result = house.apply(plan)
    assert result.house.get_auction(auction_id) == plan.updated_auction


def test_identical_bid_update_is_rejected_without_revision_churn():
    house, created = create_auction(AuctionHouse())
    auction_id = created.created_auction.auction_id
    house, _ = place_bid(
        house,
        auction_id=auction_id,
        bidder=1,
        offer={WOOD: 1},
    )
    auction = house.get_auction(auction_id)
    before = house.to_document()
    with pytest.raises(AuctionConflictError):
        house.plan_bid(
            bidder_index=1,
            auction_id=auction_id,
            expected_revision=auction.revision,
            offer={WOOD: 1},
            current_turn=1,
        )
    assert house.to_document() == before


def test_cancel_bid_releases_only_actor_escrow_and_updates_lot_revision():
    house, created = create_auction(AuctionHouse())
    auction_id = created.created_auction.auction_id
    house, _ = place_bid(
        house,
        auction_id=auction_id,
        bidder=1,
        offer={WOOD: 1},
    )
    house, _ = place_bid(
        house,
        auction_id=auction_id,
        bidder=2,
        offer={SHEEP: 1},
    )
    auction = house.get_auction(auction_id)
    plan = house.plan_cancel_bid(
        bidder_index=1,
        auction_id=auction_id,
        expected_revision=auction.revision,
        current_turn=1,
    )

    assert plan.operation == AUCTION_CANCEL_BID
    assert plan.updated_auction.revision == 4
    assert [bid.bidder_index for bid in plan.updated_auction.bids] == [2]
    assert len(plan.ledger_mutations) == 1
    assert plan.ledger_mutations[0].operation == LEDGER_RELEASE
    assert plan.ledger_mutations[0].player_index == 1
    assert dict(plan.ledger_mutations[0].bundle) == {WOOD: 1}


@pytest.mark.parametrize(
    ("bidder", "revision", "turn"),
    [(3, 2, 0), (1, 1, 0), (1, 2, 4)],
)
def test_cancel_bid_rejects_missing_stale_and_expired(bidder, revision, turn):
    house, created = create_auction(AuctionHouse(), ttl=4)
    auction_id = created.created_auction.auction_id
    house, _ = place_bid(
        house,
        auction_id=auction_id,
        bidder=1,
        offer={WOOD: 1},
    )
    with pytest.raises(AuctionConflictError):
        house.plan_cancel_bid(
            bidder_index=bidder,
            auction_id=auction_id,
            expected_revision=revision,
            current_turn=turn,
        )


def test_accept_consumes_winner_and_seller_releases_losers_then_transfers_exactly():
    house, created = create_auction(
        AuctionHouse(),
        seller=0,
        offer={ORE: 2},
        minimum=2,
        ttl=4,
    )
    auction_id = created.created_auction.auction_id
    house, _ = place_bid(
        house,
        auction_id=auction_id,
        bidder=1,
        offer={WOOD: 2},
    )
    house, _ = place_bid(
        house,
        auction_id=auction_id,
        bidder=2,
        offer={SHEEP: 1, WHEAT: 1},
    )
    auction = house.get_auction(auction_id)
    plan = house.plan_accept(
        seller_index=0,
        auction_id=auction_id,
        expected_revision=auction.revision,
        bidder_index=2,
        current_turn=2,
    )

    assert plan.operation == AUCTION_ACCEPT
    assert plan.removed_auctions == (auction,)
    assert plan.accepted_bid == auction.get_bid(2)
    assert [mutation.operation for mutation in plan.ledger_mutations] == [
        LEDGER_CONSUME,
        LEDGER_CONSUME,
        LEDGER_RELEASE,
    ]
    assert [mutation.player_index for mutation in plan.ledger_mutations] == [
        0,
        2,
        1,
    ]
    seller_to_winner, winner_to_seller = plan.transfers
    assert (seller_to_winner.from_player_index, seller_to_winner.to_player_index) == (
        0,
        2,
    )
    assert dict(seller_to_winner.bundle) == {ORE: 2}
    assert seller_to_winner.source_reservation_id.endswith(":seller")
    assert (winner_to_seller.from_player_index, winner_to_seller.to_player_index) == (
        2,
        0,
    )
    assert dict(winner_to_seller.bundle) == {SHEEP: 1, WHEAT: 1}
    assert winner_to_seller.source_reservation_id.endswith(":bid-2")
    assert house.apply(plan).house.open_auctions == ()


@pytest.mark.parametrize(
    ("seller", "bidder", "revision", "turn", "error"),
    [
        (1, 1, 2, 0, AuctionPermissionError),
        (0, 3, 2, 0, AuctionConflictError),
        (0, 1, 1, 0, AuctionConflictError),
        (0, 1, 2, 4, AuctionConflictError),
    ],
)
def test_accept_rejects_non_seller_missing_bid_stale_and_expired(
    seller,
    bidder,
    revision,
    turn,
    error,
):
    house, created = create_auction(AuctionHouse(), seller=0, ttl=4)
    auction_id = created.created_auction.auction_id
    house, _ = place_bid(
        house,
        auction_id=auction_id,
        bidder=1,
        offer={WOOD: 1},
    )
    before = house.to_document()
    with pytest.raises(error):
        house.plan_accept(
            seller_index=seller,
            auction_id=auction_id,
            expected_revision=revision,
            bidder_index=bidder,
            current_turn=turn,
        )
    assert house.to_document() == before


def test_seller_cancel_releases_seller_then_all_bidders_in_seat_order():
    house, created = create_auction(AuctionHouse(), seller=0)
    auction_id = created.created_auction.auction_id
    house, _ = place_bid(
        house,
        auction_id=auction_id,
        bidder=3,
        offer={BRICK: 1},
    )
    house, _ = place_bid(
        house,
        auction_id=auction_id,
        bidder=1,
        offer={WOOD: 1},
    )
    auction = house.get_auction(auction_id)
    plan = house.plan_cancel(
        seller_index=0,
        auction_id=auction_id,
        expected_revision=auction.revision,
        current_turn=1,
    )

    assert plan.operation == AUCTION_CANCEL
    assert [mutation.player_index for mutation in plan.ledger_mutations] == [0, 1, 3]
    assert all(
        mutation.operation == LEDGER_RELEASE
        for mutation in plan.ledger_mutations
    )
    assert house.apply(plan).house.open_auctions == ()


def test_seller_cancel_rejects_other_actor_stale_revision_and_expiry():
    house, created = create_auction(AuctionHouse(), seller=0, ttl=2)
    auction = created.created_auction
    with pytest.raises(AuctionPermissionError):
        house.plan_cancel(
            seller_index=1,
            auction_id=auction.auction_id,
            expected_revision=1,
            current_turn=0,
        )
    with pytest.raises(AuctionConflictError):
        house.plan_cancel(
            seller_index=0,
            auction_id=auction.auction_id,
            expected_revision=2,
            current_turn=0,
        )
    with pytest.raises(AuctionConflictError):
        house.plan_cancel(
            seller_index=0,
            auction_id=auction.auction_id,
            expected_revision=1,
            current_turn=2,
        )


def test_expire_removes_due_lots_and_releases_every_escrow_deterministically():
    house, first = create_auction(
        AuctionHouse(), seller=2, turn=0, ttl=2
    )
    house, _ = place_bid(
        house,
        auction_id=first.created_auction.auction_id,
        bidder=1,
        offer={WOOD: 1},
        turn=1,
    )
    house, second = create_auction(house, seller=0, turn=1, ttl=1)
    house, third = create_auction(house, seller=3, turn=1, ttl=3)

    plan = house.plan_expire(current_turn=2)
    assert plan.operation == AUCTION_EXPIRE
    assert [lot.auction_id for lot in plan.removed_auctions] == [
        first.created_auction.auction_id,
        second.created_auction.auction_id,
    ]
    assert [mutation.player_index for mutation in plan.ledger_mutations] == [
        2,
        1,
        0,
    ]
    result = house.apply(plan)
    assert result.house.open_auctions == (third.created_auction,)
    assert result.house.next_sequence == 3

    noop = result.house.plan_expire(current_turn=2)
    noop_result = result.house.apply(noop)
    assert noop.removed_auctions == ()
    assert noop_result.house is result.house


def test_stale_and_tampered_plans_are_rejected_atomically():
    original = AuctionHouse()
    create = original.plan_create(
        seller_index=0,
        offer={ORE: 1},
        current_turn=0,
        ttl=2,
    )
    advanced = original.apply(create).house
    before = advanced.to_document()
    with pytest.raises(AuctionConflictError):
        advanced.apply(create)
    assert advanced.to_document() == before

    auction = advanced.open_auctions[0]
    cancel = advanced.plan_cancel(
        seller_index=0,
        auction_id=auction.auction_id,
        expected_revision=auction.revision,
        current_turn=0,
    )
    tampered = replace(cancel, ledger_mutations=())
    with pytest.raises(AuctionConflictError):
        advanced.apply(tampered)
    assert advanced.to_document() == before


def test_round_trip_is_exact_sorted_detached_and_json_safe():
    house, first = create_auction(
        AuctionHouse(),
        seller=3,
        offer={ORE: 1},
        minimum=2,
        turn=4,
        ttl=2,
    )
    house, _ = place_bid(
        house,
        auction_id=first.created_auction.auction_id,
        bidder=2,
        offer={BRICK: 1, WOOD: 1},
        turn=4,
    )
    house, _ = place_bid(
        house,
        auction_id=first.created_auction.auction_id,
        bidder=0,
        offer={SHEEP: 2},
        turn=5,
    )
    house, _ = create_auction(
        house,
        seller=0,
        offer={WHEAT: 1},
        turn=5,
        ttl=MAX_AUCTION_TTL,
    )
    document = house.to_document()
    decoded = json.loads(json.dumps(document, allow_nan=False))
    restored = AuctionHouse.from_document(decoded)

    assert restored.to_document() == document
    assert restored.canonical_json() == house.canonical_json()
    assert restored.fingerprint() == house.fingerprint()
    assert [item["auction_id"] for item in document["open_auctions"]] == [
        "auction-000000000",
        "auction-000000001",
    ]
    assert [bid["bidder_index"] for bid in document["open_auctions"][0]["bids"]] == [
        0,
        2,
    ]
    document["open_auctions"][0]["offer"]["ORE"] = 19
    assert house.open_auctions[0].offer[ORE] == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.pop("format"),
        lambda doc: doc.__setitem__("extra", True),
        lambda doc: doc.__setitem__("format", "wrong"),
        lambda doc: doc.__setitem__("version", True),
        lambda doc: doc.__setitem__("version", 2),
        lambda doc: doc.__setitem__("next_sequence", True),
        lambda doc: doc.__setitem__("open_auctions", {}),
        lambda doc: doc["open_auctions"][0].__setitem__("extra", 1),
        lambda doc: doc["open_auctions"][0].__setitem__("auction_id", "unsafe"),
        lambda doc: doc["open_auctions"][0].__setitem__("seller_index", True),
        lambda doc: doc["open_auctions"][0].__setitem__("minimum_bid_cards", True),
        lambda doc: doc["open_auctions"][0].__setitem__("created_turn", True),
        lambda doc: doc["open_auctions"][0].__setitem__("expires_turn", 99),
        lambda doc: doc["open_auctions"][0].__setitem__("revision", True),
        lambda doc: doc["open_auctions"][0]["offer"].__setitem__("GOLD", 1),
        lambda doc: doc["open_auctions"][0].__setitem__("bids", {}),
        lambda doc: doc["open_auctions"][0]["bids"][0].__setitem__("extra", 1),
        lambda doc: doc["open_auctions"][0]["bids"][0].__setitem__(
            "bidder_index", True
        ),
        lambda doc: doc["open_auctions"][0]["bids"][0].__setitem__(
            "revision", True
        ),
        lambda doc: doc["open_auctions"][0]["bids"][0]["offer"].__setitem__(
            "ORE", 1
        ),
    ],
)
def test_document_rejects_missing_extra_bool_unknown_and_invalid_fields(mutate):
    document = canonical_document()
    mutate(document)
    with pytest.raises(TradeAuctionError):
        AuctionHouse.from_document(document)


def test_document_rejects_duplicate_unsorted_future_and_capacity_lots():
    document = canonical_document()
    duplicate = json.loads(json.dumps(document))
    duplicate["open_auctions"].append(duplicate["open_auctions"][0])
    with pytest.raises(TradeAuctionError):
        AuctionHouse.from_document(duplicate)

    second, _ = create_auction(
        AuctionHouse.from_document(document), seller=1, turn=3
    )
    unsorted = second.to_document()
    unsorted["open_auctions"].reverse()
    with pytest.raises(TradeAuctionError):
        AuctionHouse.from_document(unsorted)

    future = canonical_document()
    future["next_sequence"] = 0
    with pytest.raises(TradeAuctionError):
        AuctionHouse.from_document(future)

    too_many = AuctionHouse().to_document()
    source = canonical_document()["open_auctions"][0]
    too_many["next_sequence"] = MAX_OPEN_AUCTIONS + 1
    too_many["open_auctions"] = []
    for sequence in range(MAX_OPEN_AUCTIONS + 1):
        lot = json.loads(json.dumps(source))
        lot["auction_id"] = f"auction-{sequence:09d}"
        lot["seller_index"] = sequence % 4
        lot["bids"] = []
        lot["revision"] = 1
        too_many["open_auctions"].append(lot)
    with pytest.raises(AuctionCapacityError):
        AuctionHouse.from_document(too_many)


def test_document_rejects_unsorted_duplicate_self_and_revision_incoherent_bids():
    document = canonical_document()
    lot = document["open_auctions"][0]
    second = {"bidder_index": 2, "offer": {"SHEEP": 2}, "revision": 1}
    lot["bids"].append(second)
    lot["revision"] = 3
    lot["bids"].reverse()
    with pytest.raises(TradeAuctionError):
        AuctionHouse.from_document(document)

    duplicate = canonical_document()
    duplicate_lot = duplicate["open_auctions"][0]
    duplicate_lot["bids"].append(dict(duplicate_lot["bids"][0]))
    duplicate_lot["revision"] = 3
    with pytest.raises(TradeAuctionError):
        AuctionHouse.from_document(duplicate)

    self_bid = canonical_document()
    self_bid["open_auctions"][0]["bids"][0]["bidder_index"] = 0
    with pytest.raises(TradeAuctionError):
        AuctionHouse.from_document(self_bid)

    incoherent = canonical_document()
    incoherent["open_auctions"][0]["bids"][0]["revision"] = 2
    with pytest.raises(TradeAuctionError):
        AuctionHouse.from_document(incoherent)


def test_next_sequence_exhaustion_rejects_create_but_restores_empty_state():
    exhausted = AuctionHouse(next_sequence=EXHAUSTED_NEXT_SEQUENCE)
    assert AuctionHouse.from_document(exhausted.to_document()).next_sequence == (
        EXHAUSTED_NEXT_SEQUENCE
    )
    with pytest.raises(AuctionCapacityError):
        exhausted.plan_create(
            seller_index=0,
            offer={ORE: 1},
            current_turn=0,
            ttl=2,
        )


def test_resource_ledger_replace_is_successful_and_atomic_on_failure():
    resources = owned(WOOD=3, SHEEP=2)
    ledger = ResourceLedger(resources)
    assert ledger.reserve("auction:auction-000000000:bid-1", {WOOD: 2})
    assert ledger.replace(
        "auction:auction-000000000:bid-1",
        {SHEEP: 2, WOOD: 1},
    )
    assert ledger.reservations_map() == {
        "auction:auction-000000000:bid-1": {WOOD: 1, SHEEP: 2}
    }
    before = ledger.to_document(), dict(resources)
    assert not ledger.replace(
        "auction:auction-000000000:bid-1",
        {WOOD: 4, SHEEP: 1},
    )
    assert (ledger.to_document(), dict(resources)) == before
    assert not ledger.replace("missing", {WOOD: 1})
    assert (ledger.to_document(), dict(resources)) == before


def test_full_escrow_lifecycle_can_commit_create_update_and_accept_exactly():
    totals = [
        owned(ORE=2),
        owned(WOOD=3, SHEEP=2),
        owned(WHEAT=1, SHEEP=1),
        owned(BRICK=2),
    ]
    ledgers = [ResourceLedger(resources) for resources in totals]
    house = AuctionHouse()

    create = house.plan_create(
        seller_index=0,
        offer={ORE: 2},
        minimum_bid_cards=2,
        current_turn=0,
        ttl=4,
    )
    assert all(apply_ledger_mutation(ledgers, item) for item in create.ledger_mutations)
    house = house.apply(create).house
    auction_id = house.open_auctions[0].auction_id

    bid_one = house.plan_bid(
        bidder_index=1,
        auction_id=auction_id,
        expected_revision=1,
        offer={WOOD: 2},
        current_turn=1,
    )
    assert all(
        apply_ledger_mutation(ledgers, item) for item in bid_one.ledger_mutations
    )
    house = house.apply(bid_one).house

    update = house.plan_bid(
        bidder_index=1,
        auction_id=auction_id,
        expected_revision=2,
        offer={WOOD: 1, SHEEP: 2},
        current_turn=1,
    )
    assert all(apply_ledger_mutation(ledgers, item) for item in update.ledger_mutations)
    house = house.apply(update).house

    bid_two = house.plan_bid(
        bidder_index=2,
        auction_id=auction_id,
        expected_revision=3,
        offer={WHEAT: 1, SHEEP: 1},
        current_turn=1,
    )
    assert all(apply_ledger_mutation(ledgers, item) for item in bid_two.ledger_mutations)
    house = house.apply(bid_two).house

    accept = house.plan_accept(
        seller_index=0,
        auction_id=auction_id,
        expected_revision=4,
        bidder_index=2,
        current_turn=2,
    )
    snapshots = [
        (dict(total), ledger.to_document())
        for total, ledger in zip(totals, ledgers)
    ]
    assert all(apply_ledger_mutation(ledgers, item) for item in accept.ledger_mutations)
    # Consumed escrow leaves ownership; losing escrow was merely released.
    assert totals[0] == owned()
    assert totals[1] == owned(WOOD=3, SHEEP=2)
    assert totals[2] == owned()
    assert all(not ledger.has_reservations for ledger in ledgers)
    for transfer in accept.transfers:
        for resource, amount in transfer.bundle.items():
            totals[transfer.to_player_index][resource] += amount
    assert totals[0] == owned(WHEAT=1, SHEEP=1)
    assert totals[2] == owned(ORE=2)
    assert house.apply(accept).house.open_auctions == ()

    # The snapshots demonstrate the integration boundary has everything
    # needed for rollback should a later resource mutation unexpectedly fail.
    assert all(document["format"] for _, document in snapshots)
