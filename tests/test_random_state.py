import copy
import json
import math
import random

import pytest

from game.random_state import (
    MT19937_INDEX_MAX,
    MT19937_STATE_WORD_COUNT,
    MT19937_WORD_MAX,
    RANDOM_STATE_CODEC_VERSION,
    RANDOM_STATE_FORMAT,
    RANDOM_STATE_GENERATOR,
    RandomStateError,
    SUPPORTED_PYTHON_STATE_VERSION,
    decode_random_state,
    encode_random_state,
)


EXPECTED_KEYS = {
    "format",
    "version",
    "generator",
    "python_state_version",
    "state_words",
    "index",
    "gauss_next",
}


@pytest.fixture
def runtime_state():
    return random.Random(86712347).getstate()


@pytest.fixture
def document(runtime_state):
    return encode_random_state(runtime_state)


def test_runtime_version_is_explicitly_pinned():
    assert SUPPORTED_PYTHON_STATE_VERSION == 3
    assert random.Random.VERSION == SUPPORTED_PYTHON_STATE_VERSION


def test_encode_uses_exact_detached_json_schema(runtime_state):
    document = encode_random_state(runtime_state)

    assert type(document) is dict
    assert set(document) == EXPECTED_KEYS
    assert document["format"] == RANDOM_STATE_FORMAT
    assert document["version"] == RANDOM_STATE_CODEC_VERSION
    assert document["generator"] == RANDOM_STATE_GENERATOR
    assert document["python_state_version"] == SUPPORTED_PYTHON_STATE_VERSION
    assert type(document["state_words"]) is list
    assert len(document["state_words"]) == MT19937_STATE_WORD_COUNT
    assert 0 <= document["index"] <= MT19937_INDEX_MAX
    assert json.loads(json.dumps(document, allow_nan=False)) == document

    document["state_words"][0] = 0
    assert runtime_state[1][0] != document["state_words"][0]


def test_decode_restores_exact_tuple_shape(runtime_state, document):
    restored = decode_random_state(document)

    assert restored == runtime_state
    assert type(restored) is tuple
    assert type(restored[1]) is tuple
    assert len(restored[1]) == MT19937_STATE_WORD_COUNT + 1


def test_round_trip_continues_the_identical_random_sequence():
    authority = random.Random(424242)
    for _ in range(37):
        authority.random()
    restored = random.Random()
    restored.setstate(decode_random_state(encode_random_state(authority.getstate())))

    assert [authority.getrandbits(32) for _ in range(100)] == [
        restored.getrandbits(32) for _ in range(100)
    ]
    assert [authority.randrange(-5000, 5001) for _ in range(100)] == [
        restored.randrange(-5000, 5001) for _ in range(100)
    ]


def test_round_trip_preserves_finite_gaussian_cache_and_following_sequence():
    authority = random.Random(20260719)
    authority.gauss(10.0, 2.5)
    state = authority.getstate()
    assert type(state[2]) is float and math.isfinite(state[2])
    document = encode_random_state(state)
    restored = random.Random()
    restored.setstate(decode_random_state(document))

    assert restored.gauss(10.0, 2.5) == authority.gauss(10.0, 2.5)
    assert [restored.random() for _ in range(20)] == [
        authority.random() for _ in range(20)
    ]


@pytest.mark.parametrize("key", sorted(EXPECTED_KEYS))
def test_decode_rejects_every_missing_key(document, key):
    malformed = copy.deepcopy(document)
    malformed.pop(key)

    with pytest.raises(RandomStateError):
        decode_random_state(malformed)


def test_decode_rejects_extra_key_and_non_exact_object(document):
    extra = copy.deepcopy(document)
    extra["extra"] = True

    with pytest.raises(RandomStateError):
        decode_random_state(extra)
    with pytest.raises(RandomStateError):
        decode_random_state(tuple(document.items()))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format", "python-random-state"),
        ("format", None),
        ("version", True),
        ("version", 0),
        ("version", 2),
        ("generator", "PCG64"),
        ("generator", None),
        ("python_state_version", True),
        ("python_state_version", 2),
        ("python_state_version", 4),
    ],
)
def test_decode_rejects_schema_identity_and_version_drift(document, field, value):
    malformed = copy.deepcopy(document)
    malformed[field] = value

    with pytest.raises(RandomStateError):
        decode_random_state(malformed)


@pytest.mark.parametrize(
    "state_words",
    [
        (),
        [0] * (MT19937_STATE_WORD_COUNT - 1),
        [0] * (MT19937_STATE_WORD_COUNT + 1),
        [0] * (MT19937_STATE_WORD_COUNT * 16),
    ],
)
def test_decode_rejects_wrong_or_oversized_state_word_collections(
    document,
    state_words,
):
    malformed = copy.deepcopy(document)
    malformed["state_words"] = state_words

    with pytest.raises(RandomStateError):
        decode_random_state(malformed)


@pytest.mark.parametrize(
    "value",
    [True, False, -1, MT19937_WORD_MAX + 1, 1.0, "1", None],
)
def test_decode_rejects_invalid_word_types_and_ranges(document, value):
    malformed = copy.deepcopy(document)
    malformed["state_words"][317] = value

    with pytest.raises(RandomStateError):
        decode_random_state(malformed)


@pytest.mark.parametrize(
    "value",
    [True, False, -1, MT19937_INDEX_MAX + 1, 1.0, "1", None],
)
def test_decode_rejects_invalid_index_types_and_ranges(document, value):
    malformed = copy.deepcopy(document)
    malformed["index"] = value

    with pytest.raises(RandomStateError):
        decode_random_state(malformed)


@pytest.mark.parametrize(
    "value",
    [True, False, 0, 1, float("nan"), float("inf"), -float("inf"), "0.5", []],
)
def test_decode_rejects_non_finite_or_non_float_gaussian_cache(document, value):
    malformed = copy.deepcopy(document)
    malformed["gauss_next"] = value

    with pytest.raises(RandomStateError):
        decode_random_state(malformed)


@pytest.mark.parametrize("value", [None, 0.0, -0.0, 1.25, -999.5])
def test_decode_accepts_only_json_safe_gaussian_cache_values(document, value):
    candidate = copy.deepcopy(document)
    candidate["gauss_next"] = value

    assert decode_random_state(candidate)[2] == value


def _replace_internal(state, *, position, value):
    internal = list(state[1])
    internal[position] = value
    return (state[0], tuple(internal), state[2])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda state: list(state),
        lambda state: state[:2],
        lambda state: (True, state[1], state[2]),
        lambda state: (2, state[1], state[2]),
        lambda state: (4, state[1], state[2]),
        lambda state: (state[0], list(state[1]), state[2]),
        lambda state: (state[0], state[1][:-1], state[2]),
        lambda state: (state[0], (*state[1], 0), state[2]),
        lambda state: _replace_internal(state, position=0, value=True),
        lambda state: _replace_internal(state, position=0, value=-1),
        lambda state: _replace_internal(
            state,
            position=0,
            value=MT19937_WORD_MAX + 1,
        ),
        lambda state: _replace_internal(state, position=-1, value=True),
        lambda state: _replace_internal(
            state,
            position=-1,
            value=MT19937_INDEX_MAX + 1,
        ),
        lambda state: (state[0], state[1], True),
        lambda state: (state[0], state[1], float("nan")),
    ],
)
def test_encode_rejects_malformed_runtime_state(runtime_state, mutate):
    with pytest.raises(RandomStateError):
        encode_random_state(mutate(runtime_state))


def test_word_and_index_boundaries_are_explicitly_supported(document):
    candidate = copy.deepcopy(document)
    candidate["state_words"][0] = 0
    candidate["state_words"][1] = MT19937_WORD_MAX
    candidate["index"] = MT19937_INDEX_MAX

    restored = decode_random_state(candidate)
    assert restored[1][0] == 0
    assert restored[1][1] == MT19937_WORD_MAX
    assert restored[1][-1] == MT19937_INDEX_MAX


def test_future_runtime_version_is_rejected_until_codec_review(
    monkeypatch,
    runtime_state,
    document,
):
    monkeypatch.setattr(random.Random, "VERSION", 4)

    with pytest.raises(RandomStateError, match="codec review"):
        encode_random_state(runtime_state)
    with pytest.raises(RandomStateError, match="codec review"):
        decode_random_state(document)
