"""Strict JSON codec for the authoritative MT19937 random state.

This module intentionally does not use pickle.  It supports only CPython's
currently audited ``random.Random`` state version 3 and rejects a future
runtime version until the schema has been reviewed explicitly.
"""

from __future__ import annotations

import math
import random
from typing import Any


RANDOM_STATE_FORMAT = "catan-python-random-state"
RANDOM_STATE_CODEC_VERSION = 1
RANDOM_STATE_GENERATOR = "MT19937"
SUPPORTED_PYTHON_STATE_VERSION = 3
MT19937_STATE_WORD_COUNT = 624
MT19937_INTERNAL_TUPLE_COUNT = MT19937_STATE_WORD_COUNT + 1
MT19937_WORD_MAX = (1 << 32) - 1
MT19937_INDEX_MAX = MT19937_STATE_WORD_COUNT

_DOCUMENT_KEYS = frozenset(
    {
        "format",
        "version",
        "generator",
        "python_state_version",
        "state_words",
        "index",
        "gauss_next",
    }
)


class RandomStateError(ValueError):
    """Raised when a random state or its JSON document is unsupported."""


def _require_supported_runtime() -> None:
    runtime_version = getattr(random.Random, "VERSION", None)
    if (
        type(runtime_version) is not int
        or runtime_version != SUPPORTED_PYTHON_STATE_VERSION
    ):
        raise RandomStateError(
            "Python random state version changed; codec review is required"
        )


def _validated_word(value: object, *, position: int) -> int:
    if type(value) is not int or not 0 <= value <= MT19937_WORD_MAX:
        raise RandomStateError(f"MT19937 state word {position} is invalid")
    return value


def _validated_index(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MT19937_INDEX_MAX:
        raise RandomStateError("MT19937 state index is invalid")
    return value


def _validated_gauss_next(value: object) -> float | None:
    if value is None:
        return None
    if type(value) is not float or not math.isfinite(value):
        raise RandomStateError("Gaussian cache must be null or a finite float")
    return value


def _validated_runtime_state(
    state: object,
) -> tuple[int, tuple[int, ...], float | None]:
    _require_supported_runtime()
    if type(state) is not tuple or len(state) != 3:
        raise RandomStateError("Python random state must be an exact 3-item tuple")
    python_version, internal, gauss_next = state
    if (
        type(python_version) is not int
        or python_version != SUPPORTED_PYTHON_STATE_VERSION
    ):
        raise RandomStateError("Python random state version is unsupported")
    if (
        type(internal) is not tuple
        or len(internal) != MT19937_INTERNAL_TUPLE_COUNT
    ):
        raise RandomStateError("MT19937 internal state must contain exactly 625 items")
    words = tuple(
        _validated_word(value, position=position)
        for position, value in enumerate(internal[:MT19937_STATE_WORD_COUNT])
    )
    index = _validated_index(internal[-1])
    cache = _validated_gauss_next(gauss_next)
    return python_version, (*words, index), cache


def encode_random_state(state: object) -> dict[str, Any]:
    """Encode one audited ``random.Random.getstate()`` value as plain JSON data."""

    python_version, internal, gauss_next = _validated_runtime_state(state)
    return {
        "format": RANDOM_STATE_FORMAT,
        "version": RANDOM_STATE_CODEC_VERSION,
        "generator": RANDOM_STATE_GENERATOR,
        "python_state_version": python_version,
        "state_words": list(internal[:MT19937_STATE_WORD_COUNT]),
        "index": internal[-1],
        "gauss_next": gauss_next,
    }


def decode_random_state(
    document: object,
) -> tuple[int, tuple[int, ...], float | None]:
    """Decode an exact-schema JSON object into a validated runtime state tuple."""

    _require_supported_runtime()
    if type(document) is not dict or set(document) != _DOCUMENT_KEYS:
        raise RandomStateError("random state document has invalid keys or type")
    if (
        type(document["format"]) is not str
        or document["format"] != RANDOM_STATE_FORMAT
    ):
        raise RandomStateError("random state format is unsupported")
    if (
        type(document["version"]) is not int
        or document["version"] != RANDOM_STATE_CODEC_VERSION
    ):
        raise RandomStateError("random state codec version is unsupported")
    if (
        type(document["generator"]) is not str
        or document["generator"] != RANDOM_STATE_GENERATOR
    ):
        raise RandomStateError("random state generator is unsupported")
    python_version = document["python_state_version"]
    if (
        type(python_version) is not int
        or python_version != SUPPORTED_PYTHON_STATE_VERSION
    ):
        raise RandomStateError("Python random state version is unsupported")
    state_words = document["state_words"]
    if type(state_words) is not list or len(state_words) != MT19937_STATE_WORD_COUNT:
        raise RandomStateError("MT19937 state_words must contain exactly 624 items")
    words = tuple(
        _validated_word(value, position=position)
        for position, value in enumerate(state_words)
    )
    index = _validated_index(document["index"])
    gauss_next = _validated_gauss_next(document["gauss_next"])
    state = (python_version, (*words, index), gauss_next)
    # Keep the runtime shape check in one place and fail explicitly if CPython
    # changes a tuple invariant without changing Random.VERSION.
    return _validated_runtime_state(state)


__all__ = (
    "MT19937_INDEX_MAX",
    "MT19937_INTERNAL_TUPLE_COUNT",
    "MT19937_STATE_WORD_COUNT",
    "MT19937_WORD_MAX",
    "RANDOM_STATE_CODEC_VERSION",
    "RANDOM_STATE_FORMAT",
    "RANDOM_STATE_GENERATOR",
    "RandomStateError",
    "SUPPORTED_PYTHON_STATE_VERSION",
    "decode_random_state",
    "encode_random_state",
)
