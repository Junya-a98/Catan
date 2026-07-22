"""Pure, bounded room-passphrase authority domain.

New credentials use a runtime-independent PBKDF2-HMAC-SHA256 v2 document.
The older scrypt v1 document remains strictly parseable and verifiable on
runtimes that provide :func:`hashlib.scrypt`; it is never silently re-labelled
or weakened.  Plaintext is normalised and derived only inside constructor and
verification boundaries.  Network/public projections expose only whether a
passphrase is required; salt, digest, and KDF parameters remain authority-only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import hmac
import re
import secrets
import unicodedata
from typing import Any


# Legacy v1 constants remain public so persisted scrypt documents retain their
# exact meaning.  Every newly-created room uses the explicit v2 constants.
ROOM_ACCESS_VERSION = 1
ROOM_ACCESS_ALGORITHM = "scrypt"
ROOM_ACCESS_CURRENT_VERSION = 2
ROOM_ACCESS_CURRENT_ALGORITHM = "pbkdf2-hmac-sha256"
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024
PBKDF2_ITERATIONS = 600_000
PBKDF2_DKLEN = 32
ROOM_ACCESS_SALT_BYTES = 16
LEGACY_MIN_PASSPHRASE_CHARACTERS = 8
MIN_PASSPHRASE_CHARACTERS = 15
MAX_PASSPHRASE_CHARACTERS = 64
MAX_PASSPHRASE_UTF8_BYTES = 256

_AUTHORITY_DOCUMENT_KEYS = frozenset(
    {"version", "algorithm", "parameters", "salt", "digest"}
)
_SCRYPT_PARAMETER_KEYS = frozenset({"n", "r", "p", "dklen"})
_FIXED_SCRYPT_PARAMETERS = {
    "n": SCRYPT_N,
    "r": SCRYPT_R,
    "p": SCRYPT_P,
    "dklen": SCRYPT_DKLEN,
}
_PBKDF2_PARAMETER_KEYS = frozenset({"iterations", "dklen"})
_FIXED_PBKDF2_PARAMETERS = {
    "iterations": PBKDF2_ITERATIONS,
    "dklen": PBKDF2_DKLEN,
}
_PUBLIC_DOCUMENT_KEYS = frozenset({"passphrase_required"})
_LOWER_HEX_RE = re.compile(r"[0-9a-f]+\Z")
_EXACT_BLOCKED_PASSPHRASES = frozenset(
    {
        "123456789012345",
        "letmeinletmeinletmein",
        "password123456",
        "passwordpassword",
        "qwertyqwertyqwerty",
    }
)


class RoomAccessError(ValueError):
    """Raised when passphrase input or authority state is malformed."""


def _normalize_room_passphrase(value: str, *, minimum_characters: int) -> str:
    if type(value) is not str:
        raise RoomAccessError("room passphrase must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if not minimum_characters <= len(normalized) <= MAX_PASSPHRASE_CHARACTERS:
        raise RoomAccessError(
            "room passphrase must contain "
            f"{minimum_characters}..{MAX_PASSPHRASE_CHARACTERS} characters"
        )
    if normalized.isspace():
        raise RoomAccessError("room passphrase must not be whitespace-only")
    if any(unicodedata.category(character) == "Cc" for character in normalized):
        raise RoomAccessError("room passphrase must not contain control characters")
    try:
        encoded = normalized.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise RoomAccessError("room passphrase must be valid Unicode") from exc
    if len(encoded) > MAX_PASSPHRASE_UTF8_BYTES:
        raise RoomAccessError(
            "room passphrase UTF-8 representation exceeds the byte limit"
        )
    return normalized


def normalize_room_passphrase(value: str) -> str:
    """Return strict NFC input for a newly-created single-factor credential."""

    normalized = _normalize_room_passphrase(
        value,
        minimum_characters=MIN_PASSPHRASE_CHARACTERS,
    )
    if normalized.casefold() in _EXACT_BLOCKED_PASSPHRASES:
        raise RoomAccessError("room passphrase is too common")
    return normalized


def _derive_scrypt_digest(normalized: str, salt: bytes) -> bytes:
    scrypt = getattr(hashlib, "scrypt", None)
    if scrypt is None:
        raise RoomAccessError(
            "this Python runtime cannot verify legacy hashlib.scrypt credentials"
        )
    return scrypt(
        normalized.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM,
        dklen=SCRYPT_DKLEN,
    )


def _derive_pbkdf2_digest(normalized: str, salt: bytes) -> bytes:
    pbkdf2_hmac = getattr(hashlib, "pbkdf2_hmac", None)
    if pbkdf2_hmac is None:  # pragma: no cover - required by supported Python.
        raise RoomAccessError("this Python runtime does not provide PBKDF2-HMAC")
    return pbkdf2_hmac(
        "sha256",
        normalized.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
        dklen=PBKDF2_DKLEN,
    )


def _strict_hex(value: Any, *, byte_length: int, label: str) -> bytes:
    if type(value) is not str or len(value) != byte_length * 2:
        raise RoomAccessError(f"{label} has an invalid length or type")
    if _LOWER_HEX_RE.fullmatch(value) is None:
        raise RoomAccessError(f"{label} must be lowercase hexadecimal")
    return bytes.fromhex(value)


@dataclass(frozen=True)
class RoomPassphraseCredential:
    """Immutable authority-only credential with no plaintext field."""

    _salt: bytes = field(repr=False)
    _digest: bytes = field(repr=False)
    _version: int = field(default=ROOM_ACCESS_CURRENT_VERSION, repr=False)
    _algorithm: str = field(default=ROOM_ACCESS_CURRENT_ALGORITHM, repr=False)

    def __post_init__(self) -> None:
        if type(self._salt) is not bytes or len(self._salt) != ROOM_ACCESS_SALT_BYTES:
            raise RoomAccessError("room passphrase salt is invalid")
        if type(self._digest) is not bytes or len(self._digest) != PBKDF2_DKLEN:
            raise RoomAccessError("room passphrase digest is invalid")
        if (self._version, self._algorithm) not in {
            (ROOM_ACCESS_VERSION, ROOM_ACCESS_ALGORITHM),
            (ROOM_ACCESS_CURRENT_VERSION, ROOM_ACCESS_CURRENT_ALGORITHM),
        }:
            raise RoomAccessError("room passphrase credential scheme is unsupported")

    @classmethod
    def create(cls, passphrase: str) -> RoomPassphraseCredential:
        """Create a portable PBKDF2 v2 credential with a random salt."""

        normalized = normalize_room_passphrase(passphrase)
        salt = secrets.token_bytes(ROOM_ACCESS_SALT_BYTES)
        return cls(salt, _derive_pbkdf2_digest(normalized, salt))

    @classmethod
    def create_for_test(
        cls,
        passphrase: str,
        *,
        fixed_salt: bytes,
    ) -> RoomPassphraseCredential:
        """Create a deterministic credential for tests only.

        The deliberately explicit name and keyword prevent a caller from
        accidentally injecting a predictable salt through the production API.
        """

        normalized = normalize_room_passphrase(passphrase)
        if type(fixed_salt) is not bytes or len(fixed_salt) != ROOM_ACCESS_SALT_BYTES:
            raise RoomAccessError("test salt is invalid")
        return cls(fixed_salt, _derive_pbkdf2_digest(normalized, fixed_salt))

    @classmethod
    def create_legacy_scrypt_for_test(
        cls,
        passphrase: str,
        *,
        fixed_salt: bytes,
    ) -> RoomPassphraseCredential:
        """Create a deterministic legacy v1 document for migration tests only."""

        normalized = _normalize_room_passphrase(
            passphrase,
            minimum_characters=LEGACY_MIN_PASSPHRASE_CHARACTERS,
        )
        if type(fixed_salt) is not bytes or len(fixed_salt) != ROOM_ACCESS_SALT_BYTES:
            raise RoomAccessError("test salt is invalid")
        return cls(
            fixed_salt,
            _derive_scrypt_digest(normalized, fixed_salt),
            ROOM_ACCESS_VERSION,
            ROOM_ACCESS_ALGORITHM,
        )

    def verify(self, candidate: object) -> bool:
        """Return whether candidate matches, with a bounded KDF on bad input too."""

        valid_input = True
        try:
            # Only v1 documents may predate the 15-character creation minimum.
            normalized = _normalize_room_passphrase(
                candidate,  # type: ignore[arg-type]
                minimum_characters=(
                    LEGACY_MIN_PASSPHRASE_CHARACTERS
                    if self._version == ROOM_ACCESS_VERSION
                    else MIN_PASSPHRASE_CHARACTERS
                ),
            )
        except RoomAccessError:
            normalized = "x" * MIN_PASSPHRASE_CHARACTERS
            valid_input = False
        try:
            if self._version == ROOM_ACCESS_VERSION:
                candidate_digest = _derive_scrypt_digest(normalized, self._salt)
            else:
                candidate_digest = _derive_pbkdf2_digest(normalized, self._salt)
        except RoomAccessError:
            # A legacy document on a runtime without scrypt must fail closed.
            return False
        matched = hmac.compare_digest(self._digest, candidate_digest)
        return valid_input and matched

    def to_authority_document(self) -> dict[str, Any]:
        """Return a fresh exact-schema authority document."""

        parameters = (
            _FIXED_SCRYPT_PARAMETERS
            if self._version == ROOM_ACCESS_VERSION
            else _FIXED_PBKDF2_PARAMETERS
        )
        return {
            "version": self._version,
            "algorithm": self._algorithm,
            "parameters": dict(parameters),
            "salt": self._salt.hex(),
            "digest": self._digest.hex(),
        }

    @classmethod
    def from_authority_document(
        cls,
        document: Mapping[str, Any],
    ) -> RoomPassphraseCredential:
        """Parse only the exact bounded v1 scrypt or v2 PBKDF2 schema."""

        if not isinstance(document, Mapping) or set(document) != _AUTHORITY_DOCUMENT_KEYS:
            raise RoomAccessError("room access authority document has invalid keys")
        version = document["version"]
        algorithm = document["algorithm"]
        if type(version) is not int:
            raise RoomAccessError("room access authority version is unsupported")
        if type(algorithm) is not str:
            raise RoomAccessError("room access algorithm is unsupported")
        parameters = document["parameters"]
        if (version, algorithm) == (ROOM_ACCESS_VERSION, ROOM_ACCESS_ALGORITHM):
            expected_parameters = _FIXED_SCRYPT_PARAMETERS
            parameter_keys = _SCRYPT_PARAMETER_KEYS
        elif (version, algorithm) == (
            ROOM_ACCESS_CURRENT_VERSION,
            ROOM_ACCESS_CURRENT_ALGORITHM,
        ):
            expected_parameters = _FIXED_PBKDF2_PARAMETERS
            parameter_keys = _PBKDF2_PARAMETER_KEYS
        else:
            raise RoomAccessError("room access credential scheme is unsupported")
        if not isinstance(parameters, Mapping) or set(parameters) != parameter_keys:
            raise RoomAccessError("room access KDF parameters have invalid keys")
        if any(
            type(parameters[key]) is not int
            or parameters[key] != expected
            for key, expected in expected_parameters.items()
        ):
            raise RoomAccessError("room access KDF parameters are unsupported")
        salt = _strict_hex(
            document["salt"],
            byte_length=ROOM_ACCESS_SALT_BYTES,
            label="room access salt",
        )
        digest = _strict_hex(
            document["digest"],
            byte_length=SCRYPT_DKLEN,
            label="room access digest",
        )
        return cls(salt, digest, version, algorithm)


@dataclass(frozen=True)
class RoomAccessPolicy:
    """Optional passphrase policy for one authority-owned room."""

    credential: RoomPassphraseCredential | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.credential is not None and not isinstance(
            self.credential,
            RoomPassphraseCredential,
        ):
            raise RoomAccessError("room access credential is invalid")

    @classmethod
    def open(cls) -> RoomAccessPolicy:
        return cls()

    @classmethod
    def protected(cls, passphrase: str) -> RoomAccessPolicy:
        return cls(RoomPassphraseCredential.create(passphrase))

    @classmethod
    def protected_for_test(
        cls,
        passphrase: str,
        *,
        fixed_salt: bytes,
    ) -> RoomAccessPolicy:
        return cls(
            RoomPassphraseCredential.create_for_test(
                passphrase,
                fixed_salt=fixed_salt,
            )
        )

    @property
    def passphrase_required(self) -> bool:
        return self.credential is not None

    def verify(self, candidate: object = None) -> bool:
        if self.credential is None:
            return True
        return self.credential.verify(candidate)

    def to_authority_document(self) -> dict[str, Any] | None:
        """Return ``None`` for an open room, or the strict credential document."""

        if self.credential is None:
            return None
        return self.credential.to_authority_document()

    @classmethod
    def from_authority_document(
        cls,
        document: Mapping[str, Any] | None,
    ) -> RoomAccessPolicy:
        if document is None:
            return cls.open()
        return cls(RoomPassphraseCredential.from_authority_document(document))

    def to_public_document(self) -> dict[str, bool]:
        document = {"passphrase_required": self.passphrase_required}
        if set(document) != _PUBLIC_DOCUMENT_KEYS:  # pragma: no cover - invariant.
            raise AssertionError("room access public schema drift")
        return document


__all__ = (
    "LEGACY_MIN_PASSPHRASE_CHARACTERS",
    "MAX_PASSPHRASE_CHARACTERS",
    "MAX_PASSPHRASE_UTF8_BYTES",
    "MIN_PASSPHRASE_CHARACTERS",
    "PBKDF2_DKLEN",
    "PBKDF2_ITERATIONS",
    "ROOM_ACCESS_ALGORITHM",
    "ROOM_ACCESS_CURRENT_ALGORITHM",
    "ROOM_ACCESS_CURRENT_VERSION",
    "ROOM_ACCESS_SALT_BYTES",
    "ROOM_ACCESS_VERSION",
    "RoomAccessError",
    "RoomAccessPolicy",
    "RoomPassphraseCredential",
    "SCRYPT_DKLEN",
    "SCRYPT_N",
    "SCRYPT_P",
    "SCRYPT_R",
    "normalize_room_passphrase",
)
