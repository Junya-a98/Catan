import copy
import json
import random

import pytest

from game.forecast_events import (
    DEFAULT_FORECAST_OPTIONS,
    EVENT_CATALOG,
    FORECAST_CATALOG_ID,
    SHEEP_DROUGHT_EVENT_ID,
    WHEAT_HARVEST_EVENT_ID,
    ForecastEventError,
    canonical_forecast_options,
    validate_forecast_documents,
    validate_forecast_public,
)
from game.variant import FORECAST_EVENTS_KIND, VARIANT_CONFIG_VERSION, VariantConfig
from game.variant_state import VariantState, VariantStateError


DECK_SEED = "1" * 64
OTHER_DECK_SEED = "2" * 64
FORECAST_DOCUMENT = {
    "version": VARIANT_CONFIG_VERSION,
    "kind": FORECAST_EVENTS_KIND,
    "options": {
        "catalog": FORECAST_CATALOG_ID,
        "forecast_lead_turns": 2,
        "event_interval_turns": 6,
    },
}


def _config() -> VariantConfig:
    return VariantConfig.forecast_events()


def _initial_state(seed: str = DECK_SEED) -> VariantState:
    return VariantState.initial(_config(), deck_seed=seed)


def _advance(
    state: VariantState,
    count: int,
    *,
    player_count: int = 3,
) -> tuple[VariantState, list]:
    updates = []
    for _ in range(count):
        state, update = state.advance_forecast_turn(
            _config(),
            player_count=player_count,
        )
        updates.append(update)
    return state, updates


def test_forecast_config_is_canonical_strict_immutable_and_json_safe():
    source_options = dict(DEFAULT_FORECAST_OPTIONS)
    source = {
        "version": VARIANT_CONFIG_VERSION,
        "kind": FORECAST_EVENTS_KIND,
        "options": source_options,
    }
    config = VariantConfig.from_document(source)

    assert config == VariantConfig.forecast_events()
    assert config.to_document() == FORECAST_DOCUMENT
    assert json.loads(config.canonical_json()) == FORECAST_DOCUMENT
    assert config.fingerprint() == VariantConfig.from_document(
        copy.deepcopy(FORECAST_DOCUMENT)
    ).fingerprint()

    source_options["forecast_lead_turns"] = 9
    returned = config.to_document()
    returned["options"]["forecast_lead_turns"] = 7
    assert config.to_document() == FORECAST_DOCUMENT
    with pytest.raises(TypeError):
        config.options["forecast_lead_turns"] = 3


@pytest.mark.parametrize(
    "options",
    [
        {},
        {**dict(DEFAULT_FORECAST_OPTIONS), "extra": True},
        {
            key: value
            for key, value in DEFAULT_FORECAST_OPTIONS.items()
            if key != "catalog"
        },
        {**dict(DEFAULT_FORECAST_OPTIONS), "catalog": "future_v2"},
        {**dict(DEFAULT_FORECAST_OPTIONS), "forecast_lead_turns": True},
        {**dict(DEFAULT_FORECAST_OPTIONS), "forecast_lead_turns": 0},
        {**dict(DEFAULT_FORECAST_OPTIONS), "forecast_lead_turns": 13},
        {**dict(DEFAULT_FORECAST_OPTIONS), "event_interval_turns": False},
        {**dict(DEFAULT_FORECAST_OPTIONS), "event_interval_turns": 3},
        {**dict(DEFAULT_FORECAST_OPTIONS), "event_interval_turns": 41},
        {
            **dict(DEFAULT_FORECAST_OPTIONS),
            "forecast_lead_turns": 4,
            "event_interval_turns": 4,
        },
        [],
    ],
)
def test_forecast_options_require_the_exact_bounded_schema(options):
    with pytest.raises(ForecastEventError):
        canonical_forecast_options(options)
    with pytest.raises(ValueError):
        VariantConfig(
            kind=FORECAST_EVENTS_KIND,
            options=options,
        )


def test_explicit_deck_seed_is_deterministic_and_does_not_use_global_rng():
    random.seed(1)
    first = _initial_state()
    random.seed(999_999)
    second = _initial_state()
    different = _initial_state(OTHER_DECK_SEED)

    assert first.to_document() == second.to_document()
    assert first.to_document() != different.to_document()
    assert first.next_forecast_event_id() == WHEAT_HARVEST_EVENT_ID
    assert different.next_forecast_event_id() == SHEEP_DROUGHT_EVENT_ID
    assert first.to_document()["private"] == {
        "deck_seed": DECK_SEED,
        "deck_cycle": 0,
        "draw_pile": [
            SHEEP_DROUGHT_EVENT_ID,
            WHEAT_HARVEST_EVENT_ID,
            SHEEP_DROUGHT_EVENT_ID,
        ],
        "discard_pile": [],
    }


def test_implicit_deck_seed_does_not_advance_the_shared_game_rng():
    random.seed(51_501)
    before = random.getstate()

    first = VariantState.initial(_config())
    after = random.getstate()
    second = VariantState.initial(_config())

    assert after == before
    assert random.getstate() == before
    assert first == second


def test_catalog_and_public_forecast_use_stable_ids_not_executable_values():
    state = _initial_state()
    public = state.to_public_document()["public"]

    assert set(EVENT_CATALOG) == {
        WHEAT_HARVEST_EVENT_ID,
        SHEEP_DROUGHT_EVENT_ID,
    }
    assert public["forecast"]["event_id"] in EVENT_CATALOG
    assert all(
        isinstance(value, (str, int, list, dict)) or value is None
        for value in public.values()
    )
    json.dumps(public, allow_nan=False)


def test_public_projection_omits_private_deck_and_remains_deeply_immutable():
    state = _initial_state()
    public_document = state.to_public_document()
    encoded = json.dumps(public_document, ensure_ascii=False)

    assert "private" not in public_document
    assert "deck_seed" not in encoded
    assert "draw_pile" not in encoded
    projection = VariantState.from_public_document(
        copy.deepcopy(public_document),
        config=_config(),
    )
    assert projection.to_public_document() == public_document
    with pytest.raises(VariantStateError):
        projection.to_document()
    with pytest.raises(TypeError):
        projection.public["completed_turns"] = 1
    with pytest.raises(TypeError):
        projection.public["forecast"]["event_id"] = SHEEP_DROUGHT_EVENT_ID
    with pytest.raises(AttributeError):
        projection.public["active_effects"].append({})

    public_document["public"]["forecast"]["event_id"] = SHEEP_DROUGHT_EVENT_ID
    assert projection.next_forecast_event_id() == WHEAT_HARVEST_EVENT_ID


def test_events_activate_on_schedule_and_one_round_effect_expires():
    state = _initial_state()
    state, updates = _advance(state, 2, player_count=3)

    assert updates[0].completed_turns == 1
    assert updates[0].activated_event_id is None
    assert updates[1].activated_event_id == WHEAT_HARVEST_EVENT_ID
    assert updates[1].announced_event_id == SHEEP_DROUGHT_EVENT_ID
    assert state.active_forecast_event_ids() == (WHEAT_HARVEST_EVENT_ID,)
    assert state.to_document()["public"]["forecast"] == {
        "event_id": SHEEP_DROUGHT_EVENT_ID,
        "announced_turn": 2,
        "resolve_turn": 8,
    }

    state, consumed = state.consume_forecast_effect(WHEAT_HARVEST_EVENT_ID)
    assert consumed is True
    state, updates = _advance(state, 6, player_count=3)
    assert updates[-1].completed_turns == 8
    assert updates[-1].activated_event_id == SHEEP_DROUGHT_EVENT_ID
    assert state.active_forecast_event_ids() == (SHEEP_DROUGHT_EVENT_ID,)
    drought = state.to_document()["public"]["active_effects"][0]
    assert drought == {
        "event_id": SHEEP_DROUGHT_EVENT_ID,
        "started_turn": 8,
        "expires_turn": 11,
    }

    state, updates = _advance(state, 3, player_count=3)
    assert state.active_forecast_event_ids() == ()
    assert updates[-1].completed_turns == 11
    assert updates[-1].expired_event_ids == (SHEEP_DROUGHT_EVENT_ID,)


def test_harvest_consumption_is_idempotent_and_preserves_private_deck():
    state, _updates = _advance(_initial_state(), 2)
    private_before = state.to_document()["private"]

    consumed_state, consumed = state.consume_forecast_effect(
        WHEAT_HARVEST_EVENT_ID
    )
    unchanged_state, consumed_again = consumed_state.consume_forecast_effect(
        WHEAT_HARVEST_EVENT_ID
    )

    assert consumed is True
    assert consumed_again is False
    assert consumed_state.active_forecast_event_ids() == ()
    assert consumed_state.to_document()["private"] == private_before
    assert unchanged_state is consumed_state


def test_an_unconsumed_repeated_harvest_refreshes_without_stacking():
    state, updates = _advance(_initial_state(), 14, player_count=3)

    assert updates[-1].activated_event_id == WHEAT_HARVEST_EVENT_ID
    assert updates[-1].refreshed_event_id == WHEAT_HARVEST_EVENT_ID
    assert state.active_forecast_event_ids() == (WHEAT_HARVEST_EVENT_ID,)
    assert state.to_document()["public"]["active_effects"] == [
        {
            "event_id": WHEAT_HARVEST_EVENT_ID,
            "started_turn": 14,
            "expires_turn": None,
        }
    ]


def test_full_and_public_documents_round_trip_through_json():
    state, _updates = _advance(_initial_state(), 2)
    full_document = json.loads(
        json.dumps(state.to_document(), ensure_ascii=False, allow_nan=False)
    )
    public_document = json.loads(
        json.dumps(state.to_public_document(), ensure_ascii=False, allow_nan=False)
    )

    restored = VariantState.from_document(full_document, config=_config())
    projected = VariantState.from_public_document(
        public_document,
        config=_config(),
    )
    assert restored == state
    assert restored.to_public_document() == public_document
    assert projected.to_public_document() == public_document

    full_document["private"]["draw_pile"].clear()
    public_document["public"]["active_effects"].clear()
    assert restored == state
    assert projected.to_public_document() == state.to_public_document()


@pytest.mark.parametrize(
    "tamper",
    [
        lambda document: document["public"]["forecast"].update(
            event_id="unknown_event_v1"
        ),
        lambda document: document["public"].update(unexpected=True),
        lambda document: document["private"].update(deck_seed="not-a-seed"),
        lambda document: document["private"]["draw_pile"].pop(),
        lambda document: document["private"]["draw_pile"].__setitem__(
            0, WHEAT_HARVEST_EVENT_ID
        ),
        lambda document: document["public"].update(resolved_count=1),
        lambda document: document["public"]["forecast"].update(
            resolve_turn=999_999
        ),
        lambda document: document["private"]["draw_pile"].__setitem__(
            slice(0, 2),
            reversed(document["private"]["draw_pile"][:2]),
        ),
    ],
)
def test_tampered_full_documents_are_rejected(tamper):
    document = _initial_state().to_document()
    tamper(document)

    with pytest.raises(VariantStateError):
        VariantState.from_document(document, config=_config())


def test_public_documents_reject_private_leaks_and_mismatched_identity():
    state = _initial_state()
    leaked = state.to_public_document()
    leaked["private"] = state.to_document()["private"]

    with pytest.raises(VariantStateError):
        VariantState.from_public_document(leaked, config=_config())
    with pytest.raises(VariantStateError, match="runtime state"):
        VariantState.from_document(None, config=_config())
    with pytest.raises(VariantStateError, match="fingerprint"):
        VariantState.from_document(
            state.to_document(),
            config=VariantConfig.forecast_events(event_interval_turns=7),
        )


def test_low_level_validators_reject_unknown_ids_and_private_deck_mismatch():
    document = _initial_state().to_document()
    public = document["public"]
    private = document["private"]
    validate_forecast_public(public)
    validate_forecast_documents(public, private)

    unknown_public = copy.deepcopy(public)
    unknown_public["forecast"]["event_id"] = "future_event_v2"
    with pytest.raises(ForecastEventError):
        validate_forecast_public(unknown_public)

    mismatched_private = copy.deepcopy(private)
    mismatched_private["draw_pile"][0] = WHEAT_HARVEST_EVENT_ID
    with pytest.raises(ForecastEventError):
        validate_forecast_documents(public, mismatched_private)
