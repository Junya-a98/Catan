import json

import pytest

from game.bank import RESOURCE_TYPES
from game.resource_ledger import (
    MAX_RESERVATION_ID_LENGTH,
    MAX_RESERVATIONS,
    MAX_RESOURCE_COUNT,
    RESOURCE_LEDGER_FORMAT,
    RESOURCE_LEDGER_VERSION,
    RemovalResult,
    ResourceLedger,
    ResourceLedgerError,
)
from game.resources import ResourceType


def resources(**counts):
    return {resource: counts.get(resource.name, 0) for resource in RESOURCE_TYPES}


def test_maps_use_external_totals_and_return_detached_five_resource_copies():
    owned = resources(WOOD=3, SHEEP=2)
    ledger = ResourceLedger(owned)

    assert ledger.owned_map() == owned
    assert ledger.reserved_map() == resources()
    assert ledger.available_map() == owned
    assert not ledger.has_reservations

    owned[ResourceType.WOOD] += 1
    assert ledger.owned_count(ResourceType.WOOD) == 4

    returned = ledger.owned_map()
    returned[ResourceType.WOOD] = 0
    assert ledger.owned_count(ResourceType.WOOD) == 4


def test_reserve_release_and_consume_keep_total_ownership_semantics():
    owned = resources(WOOD=4, SHEEP=2)
    ledger = ResourceLedger(owned)
    bundle = {ResourceType.WOOD: 2, ResourceType.SHEEP: 1}

    assert ledger.reserve("offer-1", bundle)
    assert ledger.has_reservations
    assert ledger.owned_map() == resources(WOOD=4, SHEEP=2)
    assert ledger.reserved_map() == resources(WOOD=2, SHEEP=1)
    assert ledger.available_map() == resources(WOOD=2, SHEEP=1)
    assert not ledger.reserve("offer-1", {ResourceType.WOOD: 1})
    assert not ledger.reserve("offer-2", {ResourceType.WOOD: 3})

    returned = ledger.release("offer-1")
    assert returned == bundle
    assert ledger.release("offer-1") is None
    returned[ResourceType.WOOD] = 99
    assert ledger.reserved_map() == resources()
    assert owned == resources(WOOD=4, SHEEP=2)

    assert ledger.reserve("offer-3", bundle)
    consumed = ledger.consume("offer-3")
    assert consumed == bundle
    assert ledger.consume("offer-3") is None
    assert owned == resources(WOOD=2, SHEEP=1)
    assert not ledger.has_reservations


def test_spend_available_is_atomic_and_cannot_spend_reserved_cards():
    owned = resources(WOOD=3, BRICK=2)
    ledger = ResourceLedger(owned)
    assert ledger.reserve("road-stock", {ResourceType.WOOD: 2})

    before = ledger.to_document(), dict(owned)
    assert not ledger.spend_available({ResourceType.WOOD: 2})
    assert (ledger.to_document(), dict(owned)) == before

    assert ledger.spend_available({ResourceType.WOOD: 1, ResourceType.BRICK: 2})
    assert owned == resources(WOOD=2)
    assert ledger.reserved_count(ResourceType.WOOD) == 2
    assert ledger.available_count(ResourceType.WOOD) == 0


def test_forced_loss_cancels_whole_relevant_reservations_in_sorted_id_order():
    owned = resources(WOOD=4, SHEEP=3)
    ledger = ResourceLedger(owned)
    assert ledger.reserve("z-last", {ResourceType.WOOD: 1})
    assert ledger.reserve(
        "a-first",
        {ResourceType.WOOD: 1, ResourceType.SHEEP: 1},
    )
    assert ledger.reserve("b-unrelated", {ResourceType.SHEEP: 1})
    assert ledger.reserve("c-second", {ResourceType.WOOD: 1})

    result = ledger.remove_owned(ResourceType.WOOD, 3)

    assert result == RemovalResult(
        resource=ResourceType.WOOD,
        amount=3,
        cancelled_reservation_ids=("a-first", "c-second"),
    )
    assert result.canceled_reservation_ids == ("a-first", "c-second")
    assert owned == resources(WOOD=1, SHEEP=3)
    # Multi-resource a-first is cancelled whole, while unrelated and the later
    # z-last reservation remain funded.
    assert ledger.reservations_map() == {
        "b-unrelated": {ResourceType.SHEEP: 1},
        "z-last": {ResourceType.WOOD: 1},
    }
    assert ledger.reserved_map() == resources(WOOD=1, SHEEP=1)
    assert ledger.available_map() == resources(SHEEP=2)


def test_forced_loss_uses_unreserved_cards_first_and_failure_is_atomic():
    owned = resources(ORE=3)
    ledger = ResourceLedger(owned)
    assert ledger.reserve("city", {ResourceType.ORE: 2})

    result = ledger.remove_owned(ResourceType.ORE, 1)
    assert result == RemovalResult(ResourceType.ORE, 1)
    assert ledger.has_reservations
    assert owned == resources(ORE=2)

    before = ledger.to_document(), dict(owned)
    assert ledger.remove_owned(ResourceType.ORE, 3) is None
    assert (ledger.to_document(), dict(owned)) == before


@pytest.mark.parametrize(
    "reservation_id",
    [
        "",
        " leading",
        "under score",
        "_leading",
        "日本語",
        "x" * (MAX_RESERVATION_ID_LENGTH + 1),
        True,
        7,
        None,
    ],
)
def test_reservation_ids_are_strict(reservation_id):
    ledger = ResourceLedger(resources(WOOD=2))
    before = ledger.to_document()
    with pytest.raises(ResourceLedgerError):
        ledger.reserve(reservation_id, {ResourceType.WOOD: 1})
    assert ledger.to_document() == before


@pytest.mark.parametrize(
    "bundle",
    [
        {},
        [],
        None,
        {ResourceType.DESERT: 1},
        {"WOOD": 1},
        {ResourceType.WOOD: True},
        {ResourceType.WOOD: False},
        {ResourceType.WOOD: 0},
        {ResourceType.WOOD: -1},
        {ResourceType.WOOD: 1.0},
        {ResourceType.WOOD: MAX_RESOURCE_COUNT + 1},
    ],
)
def test_bundles_are_strict_and_bool_is_not_an_integer(bundle):
    ledger = ResourceLedger(resources(WOOD=3))
    before = ledger.to_document(), ledger.owned_map()
    with pytest.raises(ResourceLedgerError):
        ledger.reserve("offer", bundle)
    assert (ledger.to_document(), ledger.owned_map()) == before


@pytest.mark.parametrize(
    ("resource", "amount"),
    [
        (ResourceType.DESERT, 1),
        ("WOOD", 1),
        (ResourceType.WOOD, True),
        (ResourceType.WOOD, 0),
        (ResourceType.WOOD, -1),
        (ResourceType.WOOD, 1.0),
        (ResourceType.WOOD, MAX_RESOURCE_COUNT + 1),
    ],
)
def test_forced_loss_rejects_bad_types_without_mutation(resource, amount):
    owned = resources(WOOD=3)
    ledger = ResourceLedger(owned)
    assert ledger.reserve("offer", {ResourceType.WOOD: 1})
    before = ledger.to_document(), dict(owned)
    with pytest.raises(ResourceLedgerError):
        ledger.remove_owned(resource, amount)
    assert (ledger.to_document(), dict(owned)) == before


def test_reservation_count_has_a_hard_upper_bound():
    owned = resources(
        **{resource.name: MAX_RESOURCE_COUNT for resource in RESOURCE_TYPES}
    )
    ledger = ResourceLedger(owned)
    for index in range(MAX_RESERVATIONS):
        resource = RESOURCE_TYPES[index % len(RESOURCE_TYPES)]
        assert ledger.reserve(f"r-{index:02d}", {resource: 1})

    assert not ledger.reserve("overflow", {ResourceType.WOOD: 1})
    assert len(ledger.reservations_map()) == MAX_RESERVATIONS


def test_versioned_document_round_trip_is_exact_sorted_and_json_safe():
    owned = resources(WOOD=5, SHEEP=3, ORE=2)
    ledger = ResourceLedger(owned)
    assert ledger.reserve("z-offer", {ResourceType.ORE: 1})
    assert ledger.reserve(
        "a-offer",
        {ResourceType.WOOD: 2, ResourceType.SHEEP: 1},
    )

    document = ledger.to_document()
    assert document == {
        "format": RESOURCE_LEDGER_FORMAT,
        "version": RESOURCE_LEDGER_VERSION,
        "reservations": [
            {
                "id": "a-offer",
                "bundle": {"WOOD": 2, "SHEEP": 1},
            },
            {"id": "z-offer", "bundle": {"ORE": 1}},
        ],
    }
    decoded = json.loads(json.dumps(document, allow_nan=False))
    restored_resources = dict(owned)
    restored = ResourceLedger.from_document(restored_resources, decoded)
    assert restored.to_document() == document
    assert restored.reservations_map() == ledger.reservations_map()
    assert restored_resources == owned
    assert restored.canonical_json() == ledger.canonical_json()

    document["reservations"][0]["bundle"]["WOOD"] = 19
    assert ledger.reserved_count(ResourceType.WOOD) == 2


def canonical_document():
    return {
        "format": RESOURCE_LEDGER_FORMAT,
        "version": RESOURCE_LEDGER_VERSION,
        "reservations": [
            {"id": "a", "bundle": {"WOOD": 1}},
            {"id": "b", "bundle": {"SHEEP": 1}},
        ],
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.pop("format"),
        lambda doc: doc.__setitem__("extra", True),
        lambda doc: doc.__setitem__("format", "wrong"),
        lambda doc: doc.__setitem__("version", True),
        lambda doc: doc.__setitem__("version", 2),
        lambda doc: doc.__setitem__("reservations", {}),
        lambda doc: doc["reservations"][0].__setitem__("extra", 1),
        lambda doc: doc["reservations"][0].pop("bundle"),
        lambda doc: doc["reservations"][0].__setitem__("id", True),
        lambda doc: doc["reservations"][1].__setitem__("id", "a"),
        lambda doc: doc["reservations"].reverse(),
        lambda doc: doc["reservations"][0].__setitem__("bundle", {}),
        lambda doc: doc["reservations"][0].__setitem__("bundle", {"DESERT": 1}),
        lambda doc: doc["reservations"][0].__setitem__("bundle", {"WOOD": True}),
        lambda doc: doc["reservations"][0].__setitem__("bundle", {"WOOD": 0}),
        lambda doc: doc["reservations"][0].__setitem__("bundle", {"WOOD": 20}),
    ],
)
def test_malformed_documents_are_rejected_without_touching_totals(mutate):
    document = canonical_document()
    mutate(document)
    owned = resources(WOOD=2, SHEEP=2)
    before = dict(owned)
    with pytest.raises(ResourceLedgerError):
        ResourceLedger.from_document(owned, document)
    assert owned == before


def test_document_rejects_over_reserved_and_too_many_entries():
    over_reserved = canonical_document()
    over_reserved["reservations"][0]["bundle"]["WOOD"] = 2
    with pytest.raises(ResourceLedgerError):
        ResourceLedger.from_document(resources(WOOD=1, SHEEP=1), over_reserved)

    too_many = {
        "format": RESOURCE_LEDGER_FORMAT,
        "version": RESOURCE_LEDGER_VERSION,
        "reservations": [
            {"id": f"r-{index:02d}", "bundle": {"WOOD": 1}}
            for index in range(MAX_RESERVATIONS + 1)
        ],
    }
    with pytest.raises(ResourceLedgerError):
        ResourceLedger.from_document(
            resources(
                **{resource.name: MAX_RESOURCE_COUNT for resource in RESOURCE_TYPES}
            ),
            too_many,
        )


@pytest.mark.parametrize(
    "owned",
    [
        {},
        [],
        {resource: 0 for resource in RESOURCE_TYPES[:-1]},
        {**resources(), ResourceType.DESERT: 0},
        {**resources(), "WOOD": 0},
        {**resources(), ResourceType.WOOD: True},
        {**resources(), ResourceType.WOOD: -1},
        {**resources(), ResourceType.WOOD: MAX_RESOURCE_COUNT + 1},
    ],
)
def test_owned_resource_map_is_exact_bounded_and_rejects_bool(owned):
    with pytest.raises(ResourceLedgerError):
        ResourceLedger(owned)


def test_external_total_corruption_is_detected_before_any_operation():
    owned = resources(WOOD=2)
    ledger = ResourceLedger(owned)
    assert ledger.reserve("offer", {ResourceType.WOOD: 1})
    owned[ResourceType.WOOD] = 0

    with pytest.raises(ResourceLedgerError):
        ledger.available_map()
    with pytest.raises(ResourceLedgerError):
        ledger.consume("offer")
    owned[ResourceType.WOOD] = 2
    assert ledger.reservations_map() == {
        "offer": {ResourceType.WOOD: 1},
    }
