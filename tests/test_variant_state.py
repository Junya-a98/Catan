import copy
import json
import pickle

import pytest

from game.variant import VariantConfig
from game.variant_state import (
    VARIANT_STATE_FORMAT,
    VARIANT_STATE_VERSION,
    VariantState,
    VariantStateError,
)


CONFIG = VariantConfig.standard()
FINGERPRINT = CONFIG.fingerprint()
FULL_DOCUMENT = {
    "format": VARIANT_STATE_FORMAT,
    "version": VARIANT_STATE_VERSION,
    "kind": "standard",
    "config_fingerprint": FINGERPRINT,
    "public": {},
    "private": {},
}
PUBLIC_DOCUMENT = {
    key: copy.deepcopy(value)
    for key, value in FULL_DOCUMENT.items()
    if key != "private"
}


def _full_document(**updates):
    document = copy.deepcopy(FULL_DOCUMENT)
    document.update(updates)
    return document


def test_standard_state_has_strict_full_and_public_documents():
    state = VariantState.initial(CONFIG)

    assert state == VariantState.standard()
    assert state.to_document() == FULL_DOCUMENT
    assert state.to_public_document() == PUBLIC_DOCUMENT
    assert "private" not in state.to_public_document()
    assert VariantState.from_document(
        copy.deepcopy(FULL_DOCUMENT),
        config=CONFIG,
    ) == state
    assert VariantState.from_public_document(
        copy.deepcopy(PUBLIC_DOCUMENT),
        config=CONFIG,
    ) == state

    full = state.to_document()
    public = state.to_public_document()
    full["private"]["leak"] = True
    public["public"]["changed"] = True
    assert state.to_document() == FULL_DOCUMENT


def test_state_is_immutable_copy_safe_pickle_safe_and_json_safe():
    state = VariantState.standard()

    with pytest.raises(TypeError):
        state.public["changed"] = True
    with pytest.raises(TypeError):
        state.private["changed"] = True
    assert copy.deepcopy(state) is state
    assert pickle.loads(pickle.dumps(state)) == state
    assert json.loads(json.dumps(state.to_document())) == FULL_DOCUMENT


def test_missing_legacy_state_is_initialized_from_config():
    assert VariantState.from_document(None, config=CONFIG) == VariantState.initial(
        CONFIG
    )
    assert VariantState.from_document(None) == VariantState.standard()


@pytest.mark.parametrize(
    "document",
    [
        [],
        {},
        {key: value for key, value in FULL_DOCUMENT.items() if key != "private"},
        {**FULL_DOCUMENT, "unexpected": True},
        _full_document(format="other-format"),
        _full_document(version=True),
        _full_document(version=2),
        _full_document(kind="forecast_events"),
        _full_document(config_fingerprint="A" * 64),
        _full_document(config_fingerprint="0" * 63),
        _full_document(public=[]),
        _full_document(public={"event": "rain"}),
        _full_document(private=[]),
        _full_document(private={"deck": ["rain"]}),
    ],
)
def test_full_state_document_is_strictly_validated(document):
    with pytest.raises(VariantStateError):
        VariantState.from_document(document, config=CONFIG)


@pytest.mark.parametrize(
    "document",
    [
        FULL_DOCUMENT,
        {},
        {key: value for key, value in PUBLIC_DOCUMENT.items() if key != "public"},
        {**PUBLIC_DOCUMENT, "unexpected": True},
    ],
)
def test_public_state_document_requires_exactly_five_keys(document):
    with pytest.raises(VariantStateError):
        VariantState.from_public_document(document, config=CONFIG)


def test_state_must_match_the_exact_config_identity():
    mismatched = VariantState(config_fingerprint="0" * 64)

    with pytest.raises(VariantStateError, match="fingerprint"):
        mismatched.validate_config(CONFIG)
    with pytest.raises(VariantStateError, match="fingerprint"):
        VariantState.from_document(mismatched.to_document(), config=CONFIG)
    with pytest.raises(VariantStateError, match="variant設定"):
        VariantState.from_document(None, config="standard")
