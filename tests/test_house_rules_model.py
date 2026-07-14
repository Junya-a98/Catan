from dataclasses import FrozenInstanceError
import hashlib
import json
import re

import pytest

from game.development_cards import DevelopmentCardType
from game.house_rules import HouseRules


STANDARD_DOCUMENT = {
    "bank_trade_3_to_1": False,
    "skip_discard_on_seven": False,
    "disabled_development_cards": [],
}


def test_standard_rules_are_immutable_and_have_no_active_variants():
    rules = HouseRules.standard()

    assert rules == HouseRules()
    assert rules.to_document() == STANDARD_DOCUMENT
    assert rules.compact_label() == "なし（標準）"
    assert rules.disabled_development_cards == frozenset()
    with pytest.raises(FrozenInstanceError):
        rules.bank_trade_3_to_1 = True


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"bank_trade_3_to_1": 1}, "bank_trade_3_to_1"),
        ({"skip_discard_on_seven": 0}, "skip_discard_on_seven"),
        ({"disabled_development_cards": set()}, "frozenset"),
        ({"disabled_development_cards": frozenset({"MONOPOLY"})}, "発展カード"),
    ],
)
def test_constructor_rejects_mutable_or_wrongly_typed_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        HouseRules(**kwargs)


def test_document_round_trip_uses_stable_enum_order_and_defensive_copies():
    rules = HouseRules(
        bank_trade_3_to_1=True,
        skip_discard_on_seven=True,
        disabled_development_cards=frozenset(
            {
                DevelopmentCardType.VICTORY_POINT,
                DevelopmentCardType.KNIGHT,
                DevelopmentCardType.MONOPOLY,
            }
        ),
    )

    document = rules.to_document()

    assert document == {
        "bank_trade_3_to_1": True,
        "skip_discard_on_seven": True,
        "disabled_development_cards": [
            "KNIGHT",
            "MONOPOLY",
            "VICTORY_POINT",
        ],
    }
    assert HouseRules.from_document(document) == rules
    document["disabled_development_cards"].append("ROAD_BUILDING")
    assert DevelopmentCardType.ROAD_BUILDING not in rules.disabled_development_cards


def test_missing_legacy_document_restores_standard_rules():
    assert HouseRules.from_document(None) == HouseRules.standard()


@pytest.mark.parametrize(
    "document",
    [
        {},
        {
            **STANDARD_DOCUMENT,
            "future_rule": False,
        },
        {
            "bank_trade_3_to_1": False,
            "skip_discard_on_seven": False,
        },
        [],
        "standard",
    ],
)
def test_present_document_requires_the_exact_schema(document):
    with pytest.raises(ValueError):
        HouseRules.from_document(document)


@pytest.mark.parametrize(
    "field,value",
    [
        ("bank_trade_3_to_1", 1),
        ("bank_trade_3_to_1", "false"),
        ("skip_discard_on_seven", 0),
        ("skip_discard_on_seven", None),
        ("disabled_development_cards", ("MONOPOLY",)),
        ("disabled_development_cards", "MONOPOLY"),
    ],
)
def test_document_rejects_wrong_field_types(field, value):
    document = {**STANDARD_DOCUMENT, field: value}

    with pytest.raises(ValueError):
        HouseRules.from_document(document)


@pytest.mark.parametrize(
    "disabled_cards",
    [
        ["UNKNOWN"],
        ["monopoly"],
        [DevelopmentCardType.MONOPOLY],
        ["MONOPOLY", "MONOPOLY"],
        ["MONOPOLY"] * (len(DevelopmentCardType) + 1),
    ],
)
def test_document_rejects_unknown_non_string_or_duplicate_card_names(
    disabled_cards,
):
    document = {
        **STANDARD_DOCUMENT,
        "disabled_development_cards": disabled_cards,
    }

    with pytest.raises(ValueError):
        HouseRules.from_document(document)


def test_toggle_development_card_returns_a_new_value_and_can_restore_standard():
    standard = HouseRules.standard()

    disabled = standard.toggle_development_card(DevelopmentCardType.MONOPOLY)
    restored = disabled.toggle_development_card(DevelopmentCardType.MONOPOLY)

    assert standard.disabled_development_cards == frozenset()
    assert disabled.disabled_development_cards == frozenset(
        {DevelopmentCardType.MONOPOLY}
    )
    assert disabled.compact_label() == "禁止:独占"
    assert restored == standard
    with pytest.raises(ValueError, match="発展カード"):
        standard.toggle_development_card("MONOPOLY")


def test_compact_label_keeps_multiple_active_rules_short_and_deterministic():
    rules = HouseRules(
        bank_trade_3_to_1=True,
        skip_discard_on_seven=True,
        disabled_development_cards=frozenset(
            {
                DevelopmentCardType.MONOPOLY,
                DevelopmentCardType.YEAR_OF_PLENTY,
            }
        ),
    )

    assert rules.compact_label() == "銀行3:1 / 7捨て札なし / 発展2種禁止"


def test_fingerprint_is_canonical_sha256_and_changes_with_every_rule_kind():
    standard = HouseRules.standard()
    canonical = json.dumps(
        STANDARD_DOCUMENT,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    assert standard.fingerprint() == hashlib.sha256(canonical).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", standard.fingerprint())

    variants = {
        HouseRules(bank_trade_3_to_1=True).fingerprint(),
        HouseRules(skip_discard_on_seven=True).fingerprint(),
        HouseRules(
            disabled_development_cards=frozenset({DevelopmentCardType.MONOPOLY})
        ).fingerprint(),
    }
    assert standard.fingerprint() not in variants
    assert len(variants) == 3


def test_document_card_order_does_not_change_rule_identity():
    first = HouseRules.from_document(
        {
            **STANDARD_DOCUMENT,
            "disabled_development_cards": ["MONOPOLY", "KNIGHT"],
        }
    )
    second = HouseRules.from_document(
        {
            **STANDARD_DOCUMENT,
            "disabled_development_cards": ["KNIGHT", "MONOPOLY"],
        }
    )

    assert first == second
    assert first.to_document() == second.to_document()
    assert first.fingerprint() == second.fingerprint()
