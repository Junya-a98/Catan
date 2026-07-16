import copy

import pytest

from game.frontier import (
    DEFAULT_FRONTIER_OPTIONS,
    FRONTIER_KIND,
    FrontierError,
    INITIAL_CORE_AXIALS,
    create_initial_frontier_documents,
    reveal_frontier_tiles,
    validate_frontier_documents,
    validate_frontier_public,
)
from game.variant import VariantConfig
from game.variant_state import VariantState, VariantStateError


def test_frontier_config_is_canonical_and_strict():
    config = VariantConfig.frontier()

    assert config.kind == FRONTIER_KIND
    assert config.to_document() == {
        "version": 1,
        "kind": FRONTIER_KIND,
        "options": DEFAULT_FRONTIER_OPTIONS,
    }
    with pytest.raises(ValueError):
        VariantConfig(kind=FRONTIER_KIND, options={})
    with pytest.raises(ValueError):
        VariantConfig(
            kind=FRONTIER_KIND,
            options={**DEFAULT_FRONTIER_OPTIONS, "future": True},
        )


def test_initial_state_reveals_center_ring_and_outer_desert_without_leaking_more():
    public, private = create_initial_frontier_documents(
        DEFAULT_FRONTIER_OPTIONS,
        robber_axial=(2, -1),
    )

    assert len(INITIAL_CORE_AXIALS) == 7
    assert len(public["revealed_tiles"]) == 8
    assert "2,-1" in public["revealed_tiles"]
    assert public["discovery_count"] == 0
    validate_frontier_documents(public, private)


def test_reveal_is_sorted_idempotent_and_tracks_discovery_count():
    public, private = create_initial_frontier_documents(
        DEFAULT_FRONTIER_OPTIONS,
        robber_axial=(0, 0),
    )

    public, private, revealed = reveal_frontier_tiles(
        public,
        private,
        [(2, -1), (1, -2), (0, 0)],
    )
    assert revealed == ((1, -2), (2, -1))
    assert public["discovery_count"] == 2
    unchanged_public, unchanged_private, repeated = reveal_frontier_tiles(
        public,
        private,
        [(2, -1)],
    )
    assert repeated == ()
    assert unchanged_public == public
    assert unchanged_private == private


@pytest.mark.parametrize(
    "public",
    [
        {},
        {"revealed_tiles": [], "discovery_count": 0},
        {"revealed_tiles": ["0,0"], "discovery_count": 0},
        {
            "revealed_tiles": ["0,0", "0,0"],
            "discovery_count": 0,
        },
    ],
)
def test_public_frontier_document_rejects_malformed_projection(public):
    with pytest.raises(FrontierError):
        validate_frontier_public(public)


def test_variant_state_public_projection_keeps_reveals_but_omits_private_state():
    config = VariantConfig.frontier()
    state = VariantState.initial(config, frontier_robber_axial=(0, 0))
    projected = state.to_public_document()

    assert "private" not in projected
    restored = VariantState.from_public_document(copy.deepcopy(projected), config=config)
    assert restored.is_frontier_tile_revealed((0, 0))
    assert not restored.is_frontier_tile_revealed((2, -1))
    with pytest.raises(VariantStateError):
        restored.to_document()


def test_frontier_initial_state_requires_the_authority_robber_coordinate():
    with pytest.raises(VariantStateError, match="盗賊"):
        VariantState.initial(VariantConfig.frontier())
