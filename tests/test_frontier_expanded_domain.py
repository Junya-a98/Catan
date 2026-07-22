import copy

import pytest

from game.frontier import (
    DEFAULT_FRONTIER_OPTIONS,
    EXPANDED_AXIALS,
    EXPANDED_FRONTIER_CATALOG,
    EXPANDED_FRONTIER_OPTIONS,
    FrontierError,
    INITIAL_CORE_AXIALS,
    create_initial_frontier_documents,
    reveal_frontier_tiles,
    validate_frontier_documents,
    validate_frontier_public,
)
from game.variant import VariantConfig
from game.variant_state import VariantState, VariantStateError


LEGACY_FRONTIER_FINGERPRINT = (
    "778efcae7452dec555ac4a07dcf33874774fbd92e123d221736f7d058d9797c2"
)
EXPANDED_FRONTIER_FINGERPRINT = (
    "a1d838b56dbc374df804574d2adc41dc86db8df73860239cab876d299ebf29dc"
)


def test_legacy_frontier_document_and_identity_are_unchanged():
    config = VariantConfig.frontier()

    assert config.to_document() == {
        "version": 1,
        "kind": "frontier",
        "options": {
            "initial_radius": 1,
            "reveal_rule": "road_adjacent_v1",
        },
    }
    assert config.fingerprint() == LEGACY_FRONTIER_FINGERPRINT
    public, private = create_initial_frontier_documents(
        DEFAULT_FRONTIER_OPTIONS,
        robber_axial=(2, -1),
    )
    assert set(public) == {"revealed_tiles", "discovery_count"}
    assert len(public["revealed_tiles"]) == 8
    validate_frontier_documents(public, private, options=config.options)


def test_expanded_config_has_an_explicit_catalog_and_stable_identity():
    config = VariantConfig.frontier_expanded()

    assert config.to_document() == {
        "version": 1,
        "kind": "frontier",
        "options": EXPANDED_FRONTIER_OPTIONS,
    }
    assert config.fingerprint() == EXPANDED_FRONTIER_FINGERPRINT
    assert len(EXPANDED_AXIALS) == 37


def test_expanded_options_reject_noncanonical_or_unknown_catalogs():
    with pytest.raises(ValueError):
        VariantConfig(
            kind="frontier",
            options={**DEFAULT_FRONTIER_OPTIONS, "catalog": "standard_19_v1"},
        )
    with pytest.raises(ValueError):
        VariantConfig(
            kind="frontier",
            options={**EXPANDED_FRONTIER_OPTIONS, "catalog": "outer_ring_61_v1"},
        )


def test_expanded_initial_state_reveals_exactly_the_center_seven():
    public, private = create_initial_frontier_documents(
        EXPANDED_FRONTIER_OPTIONS,
        robber_axial=(0, 0),
    )

    assert public == {
        "catalog": EXPANDED_FRONTIER_CATALOG,
        "revealed_tiles": [
            "0,-1",
            "1,-1",
            "-1,0",
            "0,0",
            "1,0",
            "-1,1",
            "0,1",
        ],
        "discovery_count": 0,
    }
    assert private == {"initial_revealed_tiles": public["revealed_tiles"]}
    assert len(INITIAL_CORE_AXIALS) == 7
    validate_frontier_documents(
        public,
        private,
        options=VariantConfig.frontier_expanded().options,
    )

    with pytest.raises(FrontierError, match="中央"):
        create_initial_frontier_documents(
            EXPANDED_FRONTIER_OPTIONS,
            robber_axial=(2, -1),
        )


def test_expanded_reveal_accepts_radius_three_and_rejects_beyond_it():
    public, private = create_initial_frontier_documents(
        EXPANDED_FRONTIER_OPTIONS,
        robber_axial=(0, 0),
    )
    public, private, revealed = reveal_frontier_tiles(
        public,
        private,
        [(3, -2), (-3, 1), (0, 0)],
    )

    assert revealed == ((3, -2), (-3, 1))
    assert public["catalog"] == EXPANDED_FRONTIER_CATALOG
    assert public["discovery_count"] == 2
    validate_frontier_documents(public, private)

    with pytest.raises(FrontierError, match="catalog外"):
        reveal_frontier_tiles(public, private, [(4, -2)])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda public: public.pop("catalog"),
        lambda public: public.__setitem__("catalog", "standard_19_v1"),
        lambda public: public.__setitem__("future", True),
        lambda public: public["revealed_tiles"].append("4,-2"),
        lambda public: public.__setitem__("discovery_count", 1),
    ],
)
def test_expanded_public_schema_and_axial_bounds_are_strict(mutate):
    public, _private = create_initial_frontier_documents(
        EXPANDED_FRONTIER_OPTIONS,
        robber_axial=(0, 0),
    )
    mutate(public)

    with pytest.raises(FrontierError):
        validate_frontier_public(public, options=EXPANDED_FRONTIER_OPTIONS)


@pytest.mark.parametrize(
    "private",
    [
        {},
        {"initial_revealed_tiles": ["0,0"]},
        {
            "initial_revealed_tiles": [
                "0,-1",
                "1,-1",
                "-1,0",
                "0,0",
                "1,0",
                "-1,1",
                "0,1",
            ],
            "catalog": EXPANDED_FRONTIER_CATALOG,
        },
    ],
)
def test_expanded_private_schema_is_strict(private):
    public, _ = create_initial_frontier_documents(
        EXPANDED_FRONTIER_OPTIONS,
        robber_axial=(0, 0),
    )
    with pytest.raises(FrontierError):
        validate_frontier_documents(public, private)


def test_variant_state_rejects_legacy_and_expanded_catalog_mismatch():
    legacy_config = VariantConfig.frontier()
    expanded_config = VariantConfig.frontier_expanded()
    legacy = VariantState.initial(legacy_config, frontier_robber_axial=(0, 0))
    expanded = VariantState.initial(expanded_config, frontier_robber_axial=(0, 0))

    forged_legacy = copy.deepcopy(legacy.to_document())
    forged_legacy["config_fingerprint"] = expanded_config.fingerprint()
    with pytest.raises(VariantStateError, match="catalog"):
        VariantState.from_document(forged_legacy, config=expanded_config)

    forged_expanded = copy.deepcopy(expanded.to_document())
    forged_expanded["config_fingerprint"] = legacy_config.fingerprint()
    with pytest.raises(VariantStateError, match="catalog"):
        VariantState.from_document(forged_expanded, config=legacy_config)


def test_expanded_public_projection_round_trips_without_private_state():
    config = VariantConfig.frontier_expanded()
    state = VariantState.initial(config, frontier_robber_axial=(0, 0))
    projection = state.to_public_document()

    assert projection["public"]["catalog"] == EXPANDED_FRONTIER_CATALOG
    assert "private" not in projection
    restored = VariantState.from_public_document(
        copy.deepcopy(projection),
        config=config,
    )
    assert restored.is_frontier_tile_revealed((0, 0))
    assert not restored.is_frontier_tile_revealed((3, -2))
    with pytest.raises(VariantStateError):
        restored.to_document()
