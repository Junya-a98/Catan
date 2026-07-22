import copy
import json

import pytest

from game.forecast_events import FORECAST_EVENTS_KIND
from game.frontier import FRONTIER_KIND
from game.resources import ResourceType
from game.variant import (
    COMPOSITE_EVENTS_ECONOMY_CATALOG,
    COMPOSITE_VARIANT_KIND,
    CREDIT_VARIANT_KIND,
    TRADE2_VARIANT_KIND,
    VariantConfig,
    variant_board_topology,
    variant_uses_hidden_board,
)
from game.variant_state import VariantState, VariantStateError


DECK_SEED = "1" * 64
COMPONENT_KINDS = (
    FORECAST_EVENTS_KIND,
    TRADE2_VARIANT_KIND,
    CREDIT_VARIANT_KIND,
)


def _state() -> tuple[VariantConfig, VariantState]:
    config = VariantConfig.composite_events_economy()
    return config, VariantState.initial(config, deck_seed=DECK_SEED)


def test_composite_config_is_one_exact_fixed_catalog():
    config = VariantConfig.composite_events_economy()

    assert config.to_document() == {
        "version": 1,
        "kind": COMPOSITE_VARIANT_KIND,
        "options": {"catalog": COMPOSITE_EVENTS_ECONOMY_CATALOG},
    }
    assert VariantConfig.from_document(config.to_document()) == config
    assert config.fingerprint() == (
        "f2a4dd9cb51ec1da93ae23e855015b0f4667acf188a1b7cfe1c340b6aea3d101"
    )

    for options in (
        {},
        {"catalog": "future_campaign_v2"},
        {
            "catalog": COMPOSITE_EVENTS_ECONOMY_CATALOG,
            "components": [FORECAST_EVENTS_KIND, TRADE2_VARIANT_KIND],
        },
        {
            "catalog": COMPOSITE_EVENTS_ECONOMY_CATALOG,
            "forecast_options": {"event_interval_turns": 4},
        },
    ):
        with pytest.raises(ValueError, match="composite"):
            VariantConfig(kind=COMPOSITE_VARIANT_KIND, options=options)


def test_component_api_is_fixed_and_also_works_for_direct_variants():
    composite = VariantConfig.composite_events_economy()

    assert composite.component_config(FORECAST_EVENTS_KIND) == (
        VariantConfig.forecast_events(
            catalog="core_v2",
            forecast_lead_turns=2,
            event_interval_turns=6,
        )
    )
    assert composite.component_config(TRADE2_VARIANT_KIND) == (
        VariantConfig.trade2_auction(
            order_ttl_turns=4,
            auction_ttl_turns=4,
        )
    )
    assert composite.component_config(CREDIT_VARIANT_KIND) == VariantConfig.credit()
    assert all(composite.has_component(kind) for kind in COMPONENT_KINDS)
    assert not composite.has_component(FRONTIER_KIND)
    assert not composite.has_component(COMPOSITE_VARIANT_KIND)
    assert composite.component_config(FRONTIER_KIND) is None

    for direct in (
        VariantConfig.standard(),
        VariantConfig.forecast_events(),
        VariantConfig.frontier(),
        VariantConfig.trade2_auction(),
        VariantConfig.credit(),
    ):
        assert direct.component_config(direct.kind) is direct
        assert direct.has_component(direct.kind)
        assert direct.component_config("not-present") is None


def test_topology_and_hidden_board_helpers_keep_composite_on_standard_19():
    composite = VariantConfig.composite_events_economy()
    expanded = VariantConfig.frontier_expanded()

    assert composite.board_topology_id() == "standard_19_v1"
    assert not composite.uses_hidden_board()
    assert variant_board_topology(composite) == "standard_19_v1"
    assert not variant_uses_hidden_board(composite)
    assert expanded.board_topology_id() == "outer_ring_37_v1"
    assert expanded.uses_hidden_board()
    assert variant_board_topology(expanded) == "outer_ring_37_v1"
    assert variant_uses_hidden_board(expanded)


def test_existing_config_documents_and_fingerprints_are_unchanged():
    expected = {
        "standard": "04cc11be5f8e64f5b1c17c42a3a00de97d9d44d7fcf8bf185d2c3a291cd9e4ee",
        "forecast": "c01a92ee5fa882815ba2a210d25c5501a3a828ae025ffffa85116a006bbe6091",
        "frontier": "778efcae7452dec555ac4a07dcf33874774fbd92e123d221736f7d058d9797c2",
        "frontier_expanded": "a1d838b56dbc374df804574d2adc41dc86db8df73860239cab876d299ebf29dc",
        "trade2": "efdd0b3659b1ec54a1d8acde2d2835a3ccc265bdcfc447000a50556f7385a7ee",
        "trade2_auction": "b3a134ead313c04e67395d7fc473599d63f000131707a0ac65a37df2429f5619",
        "credit": "91f2ea9d7213fc6e4d0932d15e7ebc9b46be3b7d3a94141465c1b3e32e507623",
    }
    configs = {
        "standard": VariantConfig.standard(),
        "forecast": VariantConfig.forecast_events(),
        "frontier": VariantConfig.frontier(),
        "frontier_expanded": VariantConfig.frontier_expanded(),
        "trade2": VariantConfig.trade2(),
        "trade2_auction": VariantConfig.trade2_auction(),
        "credit": VariantConfig.credit(),
    }

    assert {name: config.fingerprint() for name, config in configs.items()} == expected
    for config in configs.values():
        assert VariantConfig.from_document(config.to_document()) == config


def test_composite_state_nests_exact_child_documents_and_hides_all_private_data():
    config, state = _state()
    full = state.to_document()
    public = state.to_public_document()

    assert full["public"]["catalog"] == COMPOSITE_EVENTS_ECONOMY_CATALOG
    assert full["public"]["completed_turns"] == 0
    assert tuple(full["public"]["components"]) == COMPONENT_KINDS
    assert tuple(full["private"]["components"]) == COMPONENT_KINDS
    assert "private" not in public
    assert "deck_seed" not in json.dumps(public)
    assert "next_sequence" not in json.dumps(public)

    for kind in COMPONENT_KINDS:
        child = state.component_state(kind)
        assert child is not None
        assert child.public["completed_turns"] == state.public["completed_turns"]
        assert child.to_document()["public"] == full["public"]["components"][kind]
        assert child.to_document()["private"] == full["private"]["components"][kind]

    assert VariantState.from_document(full, config=config) == state
    projection = VariantState.from_public_document(public, config=config)
    assert projection.to_public_document() == public
    for kind in COMPONENT_KINDS:
        child_projection = projection.component_state(kind)
        assert child_projection is not None
        assert child_projection.to_public_document()["public"] == (
            public["public"]["components"][kind]
        )
        with pytest.raises(VariantStateError, match="完全保存"):
            child_projection.to_document()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document: document["public"].update({"unexpected": True}),
        lambda document: document["public"]["components"].pop(CREDIT_VARIANT_KIND),
        lambda document: document["public"]["components"].update(
            {FRONTIER_KIND: {}}
        ),
        lambda document: document["private"].update({"unexpected": True}),
        lambda document: document["private"]["components"].pop(TRADE2_VARIANT_KIND),
        lambda document: document["public"]["components"][TRADE2_VARIANT_KIND].update(
            {"private": {"next_sequence": 99}}
        ),
        lambda document: document["private"]["components"][
            FORECAST_EVENTS_KIND
        ].update({"unexpected": True}),
        lambda document: document["public"].update({"completed_turns": 1}),
        lambda document: document["public"]["components"][
            CREDIT_VARIANT_KIND
        ].update({"completed_turns": 1}),
    ],
)
def test_composite_state_rejects_outer_nested_and_clock_tampering(mutate):
    config, state = _state()
    document = state.to_document()
    mutate(document)

    with pytest.raises(VariantStateError, match="composite"):
        VariantState.from_document(document, config=config)


def test_component_adapter_changes_only_target_and_rejects_clock_drift():
    config, state = _state()
    trade_config = config.component_config(TRADE2_VARIANT_KIND)
    trade = state.component_state(TRADE2_VARIANT_KIND)
    assert trade_config is not None
    assert trade is not None

    plan = trade.trade_market().plan_create(
        seller_index=0,
        offer={ResourceType.WOOD: 1},
        wanted={ResourceType.BRICK: 1},
        current_turn=0,
        ttl=4,
    )
    changed_trade, _result = trade.apply_trade_market_plan(trade_config, plan)

    before = state.to_document()
    replaced = state.replace_component_state(
        config,
        TRADE2_VARIANT_KIND,
        changed_trade,
    )
    after = replaced.to_document()

    assert after["public"]["components"][TRADE2_VARIANT_KIND] != (
        before["public"]["components"][TRADE2_VARIANT_KIND]
    )
    for untouched in (FORECAST_EVENTS_KIND, CREDIT_VARIANT_KIND):
        assert json.dumps(
            after["public"]["components"][untouched],
            ensure_ascii=False,
            separators=(",", ":"),
        ) == json.dumps(
            before["public"]["components"][untouched],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        assert json.dumps(
            after["private"]["components"][untouched],
            ensure_ascii=False,
            separators=(",", ":"),
        ) == json.dumps(
            before["private"]["components"][untouched],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    credit_config = config.component_config(CREDIT_VARIANT_KIND)
    credit = state.component_state(CREDIT_VARIANT_KIND)
    assert credit_config is not None
    assert credit is not None
    advanced_credit, _ = credit.advance_credit_turn(credit_config)
    with pytest.raises(VariantStateError, match="完了手番"):
        state.with_component_state(config, CREDIT_VARIANT_KIND, advanced_credit)


def test_component_adapter_is_natural_for_a_direct_variant():
    config = VariantConfig.trade2_auction()
    state = VariantState.initial(config)

    assert state.component_state(TRADE2_VARIANT_KIND) is state
    assert state.component_state(CREDIT_VARIANT_KIND) is None
    assert state.with_component_state(config, TRADE2_VARIANT_KIND, state) is state


def test_public_projection_rejects_nested_private_component_data():
    config, state = _state()
    public = copy.deepcopy(state.to_public_document())
    public["public"]["components"][CREDIT_VARIANT_KIND]["private"] = {
        "next_sequence": 4
    }

    with pytest.raises(VariantStateError, match="composite"):
        VariantState.from_public_document(public, config=config)
