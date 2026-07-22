from dataclasses import FrozenInstanceError
import copy
import hashlib
import hmac
import json

import pytest

from game.room_access import (
    MAX_PASSPHRASE_CHARACTERS,
    MAX_PASSPHRASE_UTF8_BYTES,
    MIN_PASSPHRASE_CHARACTERS,
    PBKDF2_DKLEN,
    PBKDF2_ITERATIONS,
    ROOM_ACCESS_CURRENT_ALGORITHM,
    ROOM_ACCESS_CURRENT_VERSION,
    ROOM_ACCESS_SALT_BYTES,
    RoomAccessError,
    RoomAccessPolicy,
    RoomPassphraseCredential,
    SCRYPT_DKLEN,
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    normalize_room_passphrase,
)


FIXED_SALT = bytes(range(ROOM_ACCESS_SALT_BYTES))
PASSPHRASE = "港町-Catan-2026-secure"
REQUIRES_SCRYPT = pytest.mark.skipif(
    not hasattr(hashlib, "scrypt"),
    reason="runtime does not provide stdlib hashlib.scrypt",
)


def _credential():
    return RoomPassphraseCredential.create_for_test(
        PASSPHRASE,
        fixed_salt=FIXED_SALT,
    )


def _authority_document():
    return {
        "version": ROOM_ACCESS_CURRENT_VERSION,
        "algorithm": ROOM_ACCESS_CURRENT_ALGORITHM,
        "parameters": {"iterations": PBKDF2_ITERATIONS, "dklen": PBKDF2_DKLEN},
        "salt": FIXED_SALT.hex(),
        "digest": (b"fixed-authority-digest".ljust(PBKDF2_DKLEN, b"\0")).hex(),
    }


def test_fixed_pbkdf2_credential_verifies_without_retaining_plaintext(monkeypatch):
    credential = _credential()
    expected = hashlib.pbkdf2_hmac(
        "sha256",
        PASSPHRASE.encode("utf-8"),
        FIXED_SALT,
        PBKDF2_ITERATIONS,
        dklen=PBKDF2_DKLEN,
    )
    document = credential.to_authority_document()

    assert bytes.fromhex(document["digest"]) == expected
    assert credential.verify(PASSPHRASE)
    assert not credential.verify("港町-Catan-2027")
    assert PASSPHRASE not in repr(credential)
    assert PASSPHRASE not in json.dumps(document, ensure_ascii=False)

    calls = []
    original_compare = hmac.compare_digest

    def recording_compare(left, right):
        calls.append((left, right))
        return original_compare(left, right)

    monkeypatch.setattr(hmac, "compare_digest", recording_compare)
    assert credential.verify(PASSPHRASE)
    assert len(calls) == 1


def test_production_creation_uses_fresh_secret_salts():
    first = RoomPassphraseCredential.create(PASSPHRASE)
    second = RoomPassphraseCredential.create(PASSPHRASE)

    assert first.to_authority_document()["salt"] != second.to_authority_document()[
        "salt"
    ]
    assert first.to_authority_document()["digest"] != second.to_authority_document()[
        "digest"
    ]
    assert first.verify(PASSPHRASE)
    assert second.verify(PASSPHRASE)


def test_nfc_equivalent_inputs_share_one_credential():
    decomposed = "Cafe\u0301-Catan-secure"
    composed = "Café-Catan-secure"
    credential = RoomPassphraseCredential.create_for_test(
        decomposed,
        fixed_salt=FIXED_SALT,
    )

    assert normalize_room_passphrase(decomposed) == composed
    assert credential.verify(composed)
    assert credential.verify(decomposed)


@pytest.mark.parametrize(
    "value, message",
    [
        (None, "string"),
        (12345678, "string"),
        ("a" * (MIN_PASSPHRASE_CHARACTERS - 1), "characters"),
        ("a" * (MAX_PASSPHRASE_CHARACTERS + 1), "characters"),
        (" " * MIN_PASSPHRASE_CHARACTERS, "whitespace"),
        ("valid-passphrase\n", "control"),
        ("valid\x00-passphrase", "control"),
        ("\ud800" + "a" * 14, "Unicode"),
    ],
)
def test_passphrase_validation_is_strict(value, message):
    with pytest.raises(RoomAccessError, match=message):
        normalize_room_passphrase(value)


def test_character_and_utf8_limits_are_explicit():
    value = "🔐" * MAX_PASSPHRASE_CHARACTERS
    normalized = normalize_room_passphrase(value)

    assert len(normalized) == MAX_PASSPHRASE_CHARACTERS
    assert len(normalized.encode("utf-8")) == MAX_PASSPHRASE_UTF8_BYTES


@pytest.mark.parametrize(
    "value",
    [
        "123456789012345",
        "PASSWORDPASSWORD",
        "qwertyqwertyqwerty",
    ],
)
def test_new_credentials_reject_exact_common_passphrases(value):
    with pytest.raises(RoomAccessError, match="common"):
        RoomPassphraseCredential.create_for_test(value, fixed_salt=FIXED_SALT)


def test_authority_document_round_trip_is_exact_and_fixed_cost():
    credential = _credential()
    document = credential.to_authority_document()

    assert document == {
        "version": 2,
        "algorithm": "pbkdf2-hmac-sha256",
        "parameters": {
            "iterations": 600000,
            "dklen": 32,
        },
        "salt": FIXED_SALT.hex(),
        "digest": credential.to_authority_document()["digest"],
    }
    restored = RoomPassphraseCredential.from_authority_document(document)
    assert restored == credential
    assert restored.verify(PASSPHRASE)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"extra": True}),
        lambda value: value.pop("digest"),
        lambda value: value.update({"version": True}),
        lambda value: value.update({"version": 3}),
        lambda value: value.update({"algorithm": "scrypt"}),
        lambda value: value.update({"parameters": {"iterations": 1, "dklen": 32}}),
        lambda value: value["parameters"].update({"extra": 1}),
        lambda value: value.update({"salt": "00"}),
        lambda value: value.update({"salt": value["salt"].upper()}),
        lambda value: value.update({"digest": "00"}),
        lambda value: value.update({"digest": value["digest"].upper()}),
        lambda value: value.update({"digest": b"0" * 64}),
    ],
)
def test_authority_document_rejects_tamper_and_amplification(mutate):
    document = copy.deepcopy(_authority_document())
    mutate(document)

    with pytest.raises(RoomAccessError):
        RoomPassphraseCredential.from_authority_document(document)


def test_optional_policy_public_projection_never_leaks_authority_material():
    open_policy = RoomAccessPolicy.open()
    protected = RoomAccessPolicy.protected_for_test(
        PASSPHRASE,
        fixed_salt=FIXED_SALT,
    )

    assert open_policy.verify()
    assert open_policy.verify("ignored-value")
    assert open_policy.to_authority_document() is None
    assert open_policy.to_public_document() == {"passphrase_required": False}
    assert protected.verify(PASSPHRASE)
    assert not protected.verify(None)
    assert protected.to_public_document() == {"passphrase_required": True}
    public_json = json.dumps(protected.to_public_document())
    authority_json = json.dumps(protected.to_authority_document())
    assert "salt" not in public_json
    assert "digest" not in public_json
    assert PASSPHRASE not in public_json
    assert PASSPHRASE not in authority_json
    assert RoomAccessPolicy.from_authority_document(
        protected.to_authority_document()
    ) == protected
    assert RoomAccessPolicy.from_authority_document(None) == open_policy


def test_credentials_are_immutable_and_documents_are_detached():
    credential = _credential()
    policy = RoomAccessPolicy(credential)
    document = credential.to_authority_document()
    public = policy.to_public_document()

    with pytest.raises(FrozenInstanceError):
        credential._salt = b"x" * ROOM_ACCESS_SALT_BYTES
    with pytest.raises(FrozenInstanceError):
        policy.credential = None
    document["parameters"]["iterations"] = 2
    document["salt"] = "00" * ROOM_ACCESS_SALT_BYTES
    public["passphrase_required"] = False
    assert credential.verify(PASSPHRASE)
    assert credential.to_authority_document()["parameters"]["iterations"] == (
        PBKDF2_ITERATIONS
    )
    assert credential.to_authority_document()["salt"] == FIXED_SALT.hex()
    assert policy.to_public_document() == {"passphrase_required": True}


def test_test_only_salt_api_rejects_wrong_type_and_length():
    with pytest.raises(RoomAccessError, match="test salt"):
        RoomPassphraseCredential.create_for_test(
            PASSPHRASE,
            fixed_salt=b"short",
        )
    with pytest.raises(RoomAccessError, match="test salt"):
        RoomPassphraseCredential.create_for_test(
            PASSPHRASE,
            fixed_salt=bytearray(FIXED_SALT),
        )


def test_missing_stdlib_scrypt_fails_closed_for_v1_but_v2_remains_available(
    monkeypatch,
):
    legacy_document = {
        "version": 1,
        "algorithm": "scrypt",
        "parameters": {"n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "dklen": SCRYPT_DKLEN},
        "salt": FIXED_SALT.hex(),
        "digest": (b"legacy-digest".ljust(SCRYPT_DKLEN, b"\0")).hex(),
    }
    legacy = RoomPassphraseCredential.from_authority_document(legacy_document)
    monkeypatch.delattr(hashlib, "scrypt", raising=False)

    assert legacy.verify(PASSPHRASE) is False
    current = RoomPassphraseCredential.create_for_test(
        PASSPHRASE,
        fixed_salt=FIXED_SALT,
    )
    assert current.verify(PASSPHRASE)


@REQUIRES_SCRYPT
def test_legacy_v1_scrypt_document_retains_exact_scheme_and_verifies():
    legacy_passphrase = "legacy42"
    credential = RoomPassphraseCredential.create_legacy_scrypt_for_test(
        legacy_passphrase,
        fixed_salt=FIXED_SALT,
    )

    assert credential.to_authority_document() == {
        "version": 1,
        "algorithm": "scrypt",
        "parameters": {"n": 32768, "r": 8, "p": 1, "dklen": 32},
        "salt": FIXED_SALT.hex(),
        "digest": credential.to_authority_document()["digest"],
    }
    assert credential.verify(legacy_passphrase)
