"""Opaque, one-use invitations for a private friends-only room.

The invitation bearer is returned only once.  Authority state stores its
SHA-256 digest, exact room instance, role, and bounded wall-clock lifetime.
Keeping the book independent from HTTP makes issuing, inspecting, consuming,
and persistence rollback testable without exposing transport details.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping
from typing import Any, Callable
import uuid


FRIEND_INVITATION_FORMAT = "catan-friend-invitations"
FRIEND_INVITATION_VERSION = 2
LEGACY_FRIEND_INVITATION_VERSION = 1
FRIEND_INVITATION_TOKEN_BYTES = 32
FRIEND_INVITATION_CLAIM_TOKEN_BYTES = 32
FRIEND_INVITATION_ID_BYTES = 16
MIN_FRIEND_INVITATION_TTL_SECONDS = 5 * 60
MAX_FRIEND_INVITATION_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_FRIEND_INVITATIONS = 32
MAX_PENDING_CLAIMS_PER_FRIEND_INVITATION = 8
MAX_FRIEND_INVITATION_TIMESTAMP_MS = 253_402_300_799_999
FRIEND_INVITATION_ROLES = frozenset({"player", "spectator"})

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_INVITATION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{22}\Z")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_ROOM_INSTANCE_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
_AUTHORITY_KEYS = frozenset({"format", "version", "room_id", "invitations"})
_INVITATION_KEYS_V1 = frozenset(
    {"token_digest", "role", "issued_at_ms", "expires_at_ms"}
)
_INVITATION_KEYS_V2 = frozenset(
    {
        "token_digest",
        "role",
        "issued_at_ms",
        "expires_at_ms",
        "claim_token_digests",
    }
)


class FriendInvitationError(ValueError):
    """Base class for expected invitation-domain failures."""


class FriendInvitationAuthenticationError(FriendInvitationError):
    """Raised when an opaque invitation is missing, invalid, used, or expired."""


class FriendInvitationCapacityError(FriendInvitationError):
    """Raised when a room already owns the maximum active invitation set."""


class FriendInvitationClaimCapacityError(FriendInvitationAuthenticationError):
    """Raised generically when one invitation has too many pending claims."""


@dataclass(frozen=True)
class FriendInvitationSummary:
    """Token-free metadata a host may use to manage an invitation."""

    invitation_id: str
    role: str
    issued_at_ms: int
    expires_at_ms: int


@dataclass(frozen=True)
class FriendInvitationClaim(FriendInvitationSummary):
    """Public metadata proven by an invitation bearer."""

    room_id: str


class FriendInvitationNotFoundError(FriendInvitationError):
    """Raised when a host management identifier cannot be resolved."""


@dataclass(frozen=True)
class FriendInvitationGrant(FriendInvitationClaim):
    """Private one-time result returned only to the issuing room owner."""

    token: str = field(repr=False, compare=True)


@dataclass(frozen=True)
class FriendInvitationClaimGrant(FriendInvitationClaim):
    """Restart-safe claim bearer returned only at the claiming boundary."""

    claim_token: str = field(repr=False, compare=True)


@dataclass(frozen=True)
class _Invitation:
    token_digest: str = field(repr=False)
    role: str
    issued_at_ms: int
    expires_at_ms: int
    claim_token_digests: tuple[str, ...] = field(default=(), repr=False)


class FriendInvitationBook:
    """Bounded invitation authority for one immutable room instance."""

    def __init__(
        self,
        room_id: str,
        *,
        token_bytes_generator: Callable[[int], bytes] = secrets.token_bytes,
        claim_token_bytes_generator: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        self.room_id = _validated_room_id(room_id)
        if not callable(token_bytes_generator):
            raise FriendInvitationError("token_bytes_generator must be callable")
        if not callable(claim_token_bytes_generator):
            raise FriendInvitationError("claim_token_bytes_generator must be callable")
        self._token_bytes_generator = token_bytes_generator
        self._claim_token_bytes_generator = claim_token_bytes_generator
        self._invitations: dict[str, _Invitation] = {}

    @classmethod
    def create(
        cls,
        *,
        room_id_generator: Callable[[], object] = uuid.uuid4,
        token_bytes_generator: Callable[[int], bytes] = secrets.token_bytes,
        claim_token_bytes_generator: Callable[[int], bytes] = secrets.token_bytes,
    ) -> FriendInvitationBook:
        if not callable(room_id_generator):
            raise FriendInvitationError("room_id_generator must be callable")
        generated = room_id_generator()
        room_id = generated.hex if isinstance(generated, uuid.UUID) else generated
        return cls(
            _validated_room_id(room_id),
            token_bytes_generator=token_bytes_generator,
            claim_token_bytes_generator=claim_token_bytes_generator,
        )

    @property
    def invitation_count(self) -> int:
        return len(self._invitations)

    def issue(
        self,
        role: str,
        *,
        now_ms: int,
        ttl_seconds: int,
    ) -> FriendInvitationGrant:
        role = _validated_role(role)
        now_ms = _validated_timestamp(now_ms, "now_ms")
        ttl_seconds = _validated_ttl(ttl_seconds)
        self.prune_expired(now_ms=now_ms)
        if len(self._invitations) >= MAX_FRIEND_INVITATIONS:
            raise FriendInvitationCapacityError(
                "the room has too many active friend invitations"
            )
        expires_at_ms = now_ms + ttl_seconds * 1000
        if expires_at_ms > MAX_FRIEND_INVITATION_TIMESTAMP_MS:
            raise FriendInvitationError("invitation expiry is out of range")
        for _attempt in range(8):
            material = self._token_bytes_generator(FRIEND_INVITATION_TOKEN_BYTES)
            if (
                type(material) is not bytes
                or len(material) < FRIEND_INVITATION_TOKEN_BYTES
            ):
                raise FriendInvitationError(
                    "token_bytes_generator must return at least 32 bytes"
                )
            token = (
                base64.urlsafe_b64encode(material[:FRIEND_INVITATION_TOKEN_BYTES])
                .rstrip(b"=")
                .decode("ascii")
            )
            digest = _hash_token(token)
            invitation_id = _invitation_id_from_digest(digest)
            if (
                not self._credential_digest_exists(digest)
                and self._find_by_claim_digest(_hash_claim_token(token)) is None
                and self._find_by_invitation_id(invitation_id) is None
            ):
                break
        else:
            raise FriendInvitationError("could not generate a unique invitation")
        invitation = _Invitation(
            token_digest=digest,
            role=role,
            issued_at_ms=now_ms,
            expires_at_ms=expires_at_ms,
        )
        self._invitations[digest] = invitation
        return FriendInvitationGrant(
            invitation_id=invitation_id,
            room_id=self.room_id,
            role=role,
            issued_at_ms=now_ms,
            expires_at_ms=expires_at_ms,
            token=token,
        )

    def inspect(self, token: object, *, now_ms: int) -> FriendInvitationClaim:
        """Validate without consuming; all bearer failures share one error."""

        now_ms = _validated_timestamp(now_ms, "now_ms")
        digest = _validated_token_digest(token)
        invitation = self._find_invitation(digest)
        if invitation is None or invitation.expires_at_ms <= now_ms:
            raise FriendInvitationAuthenticationError(
                "friend invitation could not be verified"
            )
        return FriendInvitationClaim(
            invitation_id=_invitation_id_from_digest(invitation.token_digest),
            room_id=self.room_id,
            role=invitation.role,
            issued_at_ms=invitation.issued_at_ms,
            expires_at_ms=invitation.expires_at_ms,
        )

    def consume(self, token: object, *, now_ms: int) -> FriendInvitationClaim:
        """Consume exactly one valid invitation and return its proven scope."""

        claim = self.inspect(token, now_ms=now_ms)
        digest = _validated_token_digest(token)
        invitation = self._find_invitation(digest)
        if invitation is None:  # pragma: no cover - inspect established this.
            raise FriendInvitationAuthenticationError(
                "friend invitation could not be verified"
            )
        self._invitations.pop(invitation.token_digest, None)
        return claim

    def begin_claim(
        self,
        token: object,
        *,
        now_ms: int,
    ) -> FriendInvitationClaimGrant:
        """Exchange an invite bearer for a restart-safe short-lived claim.

        The original bearer is validated but deliberately not consumed.  This
        permits a small number of browsers to have followed the same shared
        link while retaining the invitation's one-successful-join semantics.
        Only the SHA-256 digest of the generated claim is retained.
        """

        claim = self.inspect(token, now_ms=now_ms)
        invitation = self._find_invitation(_validated_token_digest(token))
        if invitation is None:  # pragma: no cover - inspect established this.
            raise FriendInvitationAuthenticationError(
                "friend invitation could not be verified"
            )
        if (
            len(invitation.claim_token_digests)
            >= MAX_PENDING_CLAIMS_PER_FRIEND_INVITATION
        ):
            raise FriendInvitationClaimCapacityError(
                "friend invitation could not be verified"
            )
        for _attempt in range(8):
            material = self._claim_token_bytes_generator(
                FRIEND_INVITATION_CLAIM_TOKEN_BYTES
            )
            if (
                type(material) is not bytes
                or len(material) < FRIEND_INVITATION_CLAIM_TOKEN_BYTES
            ):
                raise FriendInvitationError(
                    "claim_token_bytes_generator must return at least 32 bytes"
                )
            claim_token = (
                base64.urlsafe_b64encode(material[:FRIEND_INVITATION_CLAIM_TOKEN_BYTES])
                .rstrip(b"=")
                .decode("ascii")
            )
            claim_digest = _hash_claim_token(claim_token)
            if (
                not self._credential_digest_exists(claim_digest)
                and self._find_invitation(_hash_token(claim_token)) is None
            ):
                break
        else:
            raise FriendInvitationError(
                "could not generate a unique friend invitation claim"
            )
        replacement = _Invitation(
            token_digest=invitation.token_digest,
            role=invitation.role,
            issued_at_ms=invitation.issued_at_ms,
            expires_at_ms=invitation.expires_at_ms,
            claim_token_digests=tuple(
                sorted((*invitation.claim_token_digests, claim_digest))
            ),
        )
        self._invitations[invitation.token_digest] = replacement
        return FriendInvitationClaimGrant(
            invitation_id=claim.invitation_id,
            room_id=claim.room_id,
            role=claim.role,
            issued_at_ms=claim.issued_at_ms,
            expires_at_ms=claim.expires_at_ms,
            claim_token=claim_token,
        )

    def inspect_claim(
        self,
        claim_token: object,
        *,
        now_ms: int,
    ) -> FriendInvitationClaim:
        """Validate one persisted claim without consuming its invitation."""

        now_ms = _validated_timestamp(now_ms, "now_ms")
        digest = _validated_claim_token_digest(claim_token)
        invitation = self._find_by_claim_digest(digest)
        if invitation is None or invitation.expires_at_ms <= now_ms:
            raise FriendInvitationAuthenticationError(
                "friend invitation could not be verified"
            )
        return FriendInvitationClaim(
            invitation_id=_invitation_id_from_digest(invitation.token_digest),
            room_id=self.room_id,
            role=invitation.role,
            issued_at_ms=invitation.issued_at_ms,
            expires_at_ms=invitation.expires_at_ms,
        )

    def consume_claim(
        self,
        claim_token: object,
        *,
        now_ms: int,
    ) -> FriendInvitationClaim:
        """Consume the parent invitation and invalidate every sibling claim."""

        claim = self.inspect_claim(claim_token, now_ms=now_ms)
        invitation = self._find_by_claim_digest(
            _validated_claim_token_digest(claim_token)
        )
        if invitation is None:  # pragma: no cover - inspect established this.
            raise FriendInvitationAuthenticationError(
                "friend invitation could not be verified"
            )
        self._invitations.pop(invitation.token_digest, None)
        return claim

    def release_claim(
        self,
        claim_token: object,
        *,
        now_ms: int,
    ) -> FriendInvitationClaim:
        """Remove one abandoned claim while leaving its invitation usable."""

        claim = self.inspect_claim(claim_token, now_ms=now_ms)
        digest = _validated_claim_token_digest(claim_token)
        invitation = self._find_by_claim_digest(digest)
        if invitation is None:  # pragma: no cover - inspect established this.
            raise FriendInvitationAuthenticationError(
                "friend invitation could not be verified"
            )
        replacement = _Invitation(
            token_digest=invitation.token_digest,
            role=invitation.role,
            issued_at_ms=invitation.issued_at_ms,
            expires_at_ms=invitation.expires_at_ms,
            claim_token_digests=tuple(
                item
                for item in invitation.claim_token_digests
                if not hmac.compare_digest(item, digest)
            ),
        )
        self._invitations[invitation.token_digest] = replacement
        return claim

    def list_active(self, *, now_ms: int) -> tuple[FriendInvitationSummary, ...]:
        """Prune expired entries and return canonical token-free metadata."""

        self.prune_expired(now_ms=now_ms)
        return tuple(
            self._summary(invitation)
            for invitation in sorted(
                self._invitations.values(),
                key=lambda item: _invitation_id_from_digest(item.token_digest),
            )
        )

    def revoke(
        self,
        invitation_id: object,
        *,
        now_ms: int,
    ) -> FriendInvitationSummary:
        """Revoke one active invitation by its non-secret management id."""

        now_ms = _validated_timestamp(now_ms, "now_ms")
        identifier = _validated_invitation_id(invitation_id)
        self.prune_expired(now_ms=now_ms)
        invitation = self._find_by_invitation_id(identifier)
        if invitation is None:
            raise FriendInvitationNotFoundError("friend invitation could not be found")
        self._invitations.pop(invitation.token_digest, None)
        return self._summary(invitation)

    def revoke_all(self, *, now_ms: int) -> int:
        """Revoke all active invitations and return their count."""

        self.prune_expired(now_ms=now_ms)
        revoked_count = len(self._invitations)
        self._invitations.clear()
        return revoked_count

    def prune_expired(self, *, now_ms: int) -> int:
        now_ms = _validated_timestamp(now_ms, "now_ms")
        expired = tuple(
            digest
            for digest, invitation in self._invitations.items()
            if invitation.expires_at_ms <= now_ms
        )
        for digest in expired:
            self._invitations.pop(digest, None)
        return len(expired)

    def to_authority_document(self) -> dict[str, Any]:
        return {
            "format": FRIEND_INVITATION_FORMAT,
            "version": FRIEND_INVITATION_VERSION,
            "room_id": self.room_id,
            "invitations": [
                {
                    "token_digest": invitation.token_digest,
                    "role": invitation.role,
                    "issued_at_ms": invitation.issued_at_ms,
                    "expires_at_ms": invitation.expires_at_ms,
                    "claim_token_digests": list(invitation.claim_token_digests),
                }
                for invitation in sorted(
                    self._invitations.values(),
                    key=lambda item: item.token_digest,
                )
            ],
        }

    @classmethod
    def from_authority_document(
        cls,
        document: Mapping[str, Any],
        *,
        token_bytes_generator: Callable[[int], bytes] = secrets.token_bytes,
        claim_token_bytes_generator: Callable[[int], bytes] = secrets.token_bytes,
    ) -> FriendInvitationBook:
        if type(document) is not dict or set(document) != _AUTHORITY_KEYS:
            raise FriendInvitationError(
                "friend invitation authority document has invalid keys"
            )
        if document["format"] != FRIEND_INVITATION_FORMAT:
            raise FriendInvitationError(
                "friend invitation authority format is unsupported"
            )
        version = document["version"]
        if type(version) is not int or version not in {
            LEGACY_FRIEND_INVITATION_VERSION,
            FRIEND_INVITATION_VERSION,
        }:
            raise FriendInvitationError(
                "friend invitation authority version is unsupported"
            )
        raw_invitations = document["invitations"]
        if (
            type(raw_invitations) is not list
            or len(raw_invitations) > MAX_FRIEND_INVITATIONS
        ):
            raise FriendInvitationError(
                "friend invitation authority collection is invalid"
            )
        book = cls(
            _validated_room_id(document["room_id"]),
            token_bytes_generator=token_bytes_generator,
            claim_token_bytes_generator=claim_token_bytes_generator,
        )
        previous_digest: str | None = None
        invitation_ids: set[str] = set()
        invitation_digests: set[str] = set()
        global_claim_digests: set[str] = set()
        for raw in raw_invitations:
            expected_keys = (
                _INVITATION_KEYS_V1
                if version == LEGACY_FRIEND_INVITATION_VERSION
                else _INVITATION_KEYS_V2
            )
            if type(raw) is not dict or set(raw) != expected_keys:
                raise FriendInvitationError(
                    "friend invitation authority record has invalid keys"
                )
            digest = raw["token_digest"]
            if type(digest) is not str or _DIGEST_PATTERN.fullmatch(digest) is None:
                raise FriendInvitationError("friend invitation digest is invalid")
            if previous_digest is not None and digest <= previous_digest:
                raise FriendInvitationError(
                    "friend invitation authority is not canonically ordered"
                )
            invitation_id = _invitation_id_from_digest(digest)
            if invitation_id in invitation_ids:
                raise FriendInvitationError(
                    "friend invitation management id is duplicated"
                )
            if digest in global_claim_digests:
                raise FriendInvitationError(
                    "friend invitation credential digest is duplicated"
                )
            issued_at_ms = _validated_timestamp(raw["issued_at_ms"], "issued_at_ms")
            expires_at_ms = _validated_timestamp(raw["expires_at_ms"], "expires_at_ms")
            lifetime_ms = expires_at_ms - issued_at_ms
            if not (
                MIN_FRIEND_INVITATION_TTL_SECONDS * 1000
                <= lifetime_ms
                <= MAX_FRIEND_INVITATION_TTL_SECONDS * 1000
            ):
                raise FriendInvitationError(
                    "friend invitation authority lifetime is invalid"
                )
            raw_claim_digests = (
                []
                if version == LEGACY_FRIEND_INVITATION_VERSION
                else raw["claim_token_digests"]
            )
            if (
                type(raw_claim_digests) is not list
                or len(raw_claim_digests) > MAX_PENDING_CLAIMS_PER_FRIEND_INVITATION
            ):
                raise FriendInvitationError(
                    "friend invitation claim collection is invalid"
                )
            claim_digests: list[str] = []
            previous_claim_digest: str | None = None
            for claim_digest in raw_claim_digests:
                if (
                    type(claim_digest) is not str
                    or _DIGEST_PATTERN.fullmatch(claim_digest) is None
                ):
                    raise FriendInvitationError(
                        "friend invitation claim digest is invalid"
                    )
                if (
                    previous_claim_digest is not None
                    and claim_digest <= previous_claim_digest
                ):
                    raise FriendInvitationError(
                        "friend invitation claims are not canonically ordered"
                    )
                if (
                    claim_digest in global_claim_digests
                    or claim_digest in invitation_digests
                    or claim_digest == digest
                ):
                    raise FriendInvitationError(
                        "friend invitation credential digest is duplicated"
                    )
                claim_digests.append(claim_digest)
                global_claim_digests.add(claim_digest)
                previous_claim_digest = claim_digest
            invitation = _Invitation(
                token_digest=digest,
                role=_validated_role(raw["role"]),
                issued_at_ms=issued_at_ms,
                expires_at_ms=expires_at_ms,
                claim_token_digests=tuple(claim_digests),
            )
            book._invitations[digest] = invitation
            invitation_ids.add(invitation_id)
            invitation_digests.add(digest)
            previous_digest = digest
        if invitation_digests.intersection(global_claim_digests):
            raise FriendInvitationError(
                "friend invitation credential digest is duplicated"
            )
        return book

    def _find_invitation(self, candidate_digest: str) -> _Invitation | None:
        # The collection is bounded to 32 records.  A constant-time digest
        # comparison avoids making a valid bearer distinguishable by prefix.
        for digest, invitation in self._invitations.items():
            if hmac.compare_digest(digest, candidate_digest):
                return invitation
        return None

    def _find_by_invitation_id(self, candidate_id: str) -> _Invitation | None:
        # Management identifiers are non-secret, but bounded constant-time
        # comparisons keep their lookup behaviour uniform with bearer lookup.
        for invitation in self._invitations.values():
            invitation_id = _invitation_id_from_digest(invitation.token_digest)
            if hmac.compare_digest(invitation_id, candidate_id):
                return invitation
        return None

    def _find_by_claim_digest(
        self,
        candidate_digest: str,
    ) -> _Invitation | None:
        # Scan every bounded slot and compare every digest in constant time.
        # Do not terminate on a match; this keeps valid claims indistinguishable
        # by their parent record position.
        matched: _Invitation | None = None
        for invitation in self._invitations.values():
            for digest in invitation.claim_token_digests:
                if hmac.compare_digest(digest, candidate_digest):
                    matched = invitation
        return matched

    def _credential_digest_exists(self, candidate_digest: str) -> bool:
        found = False
        for invitation in self._invitations.values():
            found = (
                hmac.compare_digest(
                    invitation.token_digest,
                    candidate_digest,
                )
                or found
            )
            for digest in invitation.claim_token_digests:
                found = hmac.compare_digest(digest, candidate_digest) or found
        return found

    @staticmethod
    def _summary(invitation: _Invitation) -> FriendInvitationSummary:
        return FriendInvitationSummary(
            invitation_id=_invitation_id_from_digest(invitation.token_digest),
            role=invitation.role,
            issued_at_ms=invitation.issued_at_ms,
            expires_at_ms=invitation.expires_at_ms,
        )


def _validated_room_id(value: object) -> str:
    if type(value) is not str or _ROOM_INSTANCE_ID_PATTERN.fullmatch(value) is None:
        raise FriendInvitationError("room_id must be a canonical UUID hex value")
    try:
        parsed = uuid.UUID(hex=value)
    except (ValueError, AttributeError) as exc:  # pragma: no cover - regex guards.
        raise FriendInvitationError("room_id is invalid") from exc
    if parsed.hex != value:
        raise FriendInvitationError("room_id is not canonical")
    return value


def _validated_role(value: object) -> str:
    if type(value) is not str or value not in FRIEND_INVITATION_ROLES:
        raise FriendInvitationError("friend invitation role is invalid")
    return value


def _validated_timestamp(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_FRIEND_INVITATION_TIMESTAMP_MS:
        raise FriendInvitationError(f"{label} is invalid")
    return value


def _validated_ttl(value: object) -> int:
    if (
        type(value) is not int
        or not MIN_FRIEND_INVITATION_TTL_SECONDS
        <= value
        <= MAX_FRIEND_INVITATION_TTL_SECONDS
    ):
        raise FriendInvitationError("friend invitation ttl_seconds must be 300..604800")
    return value


def _validated_token_digest(value: object) -> str:
    if type(value) is not str or _TOKEN_PATTERN.fullmatch(value) is None:
        # Hash a fixed dummy value so malformed and unknown credentials follow
        # the same bounded work shape before the generic failure is raised.
        _hash_token("A" * 43)
        raise FriendInvitationAuthenticationError(
            "friend invitation could not be verified"
        )
    return _hash_token(value)


def _validated_claim_token_digest(value: object) -> str:
    if type(value) is not str or _TOKEN_PATTERN.fullmatch(value) is None:
        _hash_claim_token("A" * 43)
        raise FriendInvitationAuthenticationError(
            "friend invitation could not be verified"
        )
    return _hash_claim_token(value)


def _validated_invitation_id(value: object) -> str:
    if type(value) is not str or _INVITATION_ID_PATTERN.fullmatch(value) is None:
        # Match the bounded lookup shape used for a valid-but-unknown id.
        hmac.compare_digest("A" * 22, "B" * 22)
        raise FriendInvitationNotFoundError("friend invitation could not be found")
    return value


def _invitation_id_from_digest(digest: str) -> str:
    """Return the stable 128-bit public id without exposing the full digest."""

    return (
        base64.urlsafe_b64encode(bytes.fromhex(digest)[:FRIEND_INVITATION_ID_BYTES])
        .rstrip(b"=")
        .decode("ascii")
    )


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _hash_claim_token(token: str) -> str:
    return hashlib.sha256(
        b"catan-friend-claim-v1\0" + token.encode("ascii")
    ).hexdigest()


__all__ = (
    "FRIEND_INVITATION_FORMAT",
    "FRIEND_INVITATION_CLAIM_TOKEN_BYTES",
    "FRIEND_INVITATION_ID_BYTES",
    "FRIEND_INVITATION_ROLES",
    "FRIEND_INVITATION_TOKEN_BYTES",
    "FRIEND_INVITATION_VERSION",
    "LEGACY_FRIEND_INVITATION_VERSION",
    "MAX_FRIEND_INVITATIONS",
    "MAX_PENDING_CLAIMS_PER_FRIEND_INVITATION",
    "MAX_FRIEND_INVITATION_TTL_SECONDS",
    "MIN_FRIEND_INVITATION_TTL_SECONDS",
    "FriendInvitationAuthenticationError",
    "FriendInvitationBook",
    "FriendInvitationCapacityError",
    "FriendInvitationClaim",
    "FriendInvitationClaimCapacityError",
    "FriendInvitationClaimGrant",
    "FriendInvitationError",
    "FriendInvitationGrant",
    "FriendInvitationNotFoundError",
    "FriendInvitationSummary",
)
