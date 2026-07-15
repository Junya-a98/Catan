import copy
import json
import pickle

import pytest

from game.variant import VARIANT_CONFIG_VERSION, VariantConfig


STANDARD_DOCUMENT = {
    "version": VARIANT_CONFIG_VERSION,
    "kind": "standard",
    "options": {},
}


def test_standard_document_is_canonical_immutable_and_stable():
    source = {
        "options": {},
        "kind": "standard",
        "version": VARIANT_CONFIG_VERSION,
    }
    config = VariantConfig.from_document(source)

    assert config == VariantConfig.standard()
    assert config.to_document() == STANDARD_DOCUMENT
    assert config.canonical_json() == (
        '{"kind":"standard","options":{},"version":1}'
    )
    assert len(config.fingerprint()) == 64
    assert config.fingerprint() == VariantConfig.from_document(
        copy.deepcopy(STANDARD_DOCUMENT)
    ).fingerprint()
    assert copy.deepcopy(config) is config
    assert pickle.loads(pickle.dumps(config)) == config

    source["kind"] = "changed-after-parse"
    returned = config.to_document()
    returned["options"]["unexpected"] = True
    assert config.to_document() == STANDARD_DOCUMENT
    with pytest.raises(TypeError):
        config.options["unexpected"] = True


def test_missing_document_is_the_legacy_standard_default():
    assert VariantConfig.from_document(None) == VariantConfig.standard()


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"version": 1, "kind": "standard"},
        {"version": 1, "kind": "standard", "options": {}, "extra": True},
        {"version": True, "kind": "standard", "options": {}},
        {"version": 2, "kind": "standard", "options": {}},
        {"version": 1, "kind": "forecast_events", "options": {}},
        {"version": 1, "kind": 1, "options": {}},
        {"version": 1, "kind": "standard", "options": []},
        {"version": 1, "kind": "standard", "options": {"future": True}},
        [],
    ],
)
def test_present_documents_are_strictly_validated(document):
    with pytest.raises(ValueError):
        VariantConfig.from_document(document)


def test_document_and_fingerprint_are_json_safe():
    config = VariantConfig.standard()
    encoded = json.dumps(config.to_document(), allow_nan=False)

    assert json.loads(encoded) == STANDARD_DOCUMENT
    assert config.fingerprint() == VariantConfig.standard().fingerprint()
