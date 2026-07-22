import base64
import copy
import hashlib

import pytest

import game.friend_invitation as friend_invitation_module
from game.friend_invitation import (
    FRIEND_INVITATION_FORMAT,
    FRIEND_INVITATION_VERSION,
    LEGACY_FRIEND_INVITATION_VERSION,
    MAX_FRIEND_INVITATIONS,
    MAX_PENDING_CLAIMS_PER_FRIEND_INVITATION,
    FriendInvitationAuthenticationError,
    FriendInvitationBook,
    FriendInvitationCapacityError,
    FriendInvitationError,
    FriendInvitationNotFoundError,
)


ROOM_ID = "12345678123456781234567812345678"


class TokenBytes:
    def __init__(self, start=0):
        self.calls = start

    def __call__(self, size):
        self.calls += 1
        return self.calls.to_bytes(size, "big")


def make_book():
    return FriendInvitationBook(
        ROOM_ID,
        token_bytes_generator=TokenBytes(),
        claim_token_bytes_generator=TokenBytes(start=100),
    )


def test_issue_exposes_plaintext_once_but_authority_stores_only_digest_and_scope():
    book = make_book()
    grant = book.issue("player", now_ms=1_000_000, ttl_seconds=300)

    authority = book.to_authority_document()
    record = authority["invitations"][0]
    assert authority == {
        "format": FRIEND_INVITATION_FORMAT,
        "version": FRIEND_INVITATION_VERSION,
        "room_id": ROOM_ID,
        "invitations": [record],
    }
    assert record == {
        "token_digest": hashlib.sha256(grant.token.encode("ascii")).hexdigest(),
        "role": "player",
        "issued_at_ms": 1_000_000,
        "expires_at_ms": 1_300_000,
        "claim_token_digests": [],
    }
    assert grant.invitation_id == base64.urlsafe_b64encode(
        bytes.fromhex(record["token_digest"])[:16]
    ).rstrip(b"=").decode("ascii")
    assert len(grant.invitation_id) == 22
    assert grant.token not in repr(grant)
    assert grant.token not in repr(book.__dict__)
    assert grant.token not in str(authority)


def test_claim_is_persisted_as_domain_separated_digest_and_round_trips():
    book = make_book()
    grant = book.issue("player", now_ms=1_000_000, ttl_seconds=300)
    claim_grant = book.begin_claim(grant.token, now_ms=1_000_001)

    authority = book.to_authority_document()
    claim_digest = authority["invitations"][0]["claim_token_digests"][0]
    ordinary_digest = hashlib.sha256(
        claim_grant.claim_token.encode("ascii")
    ).hexdigest()
    assert (
        claim_digest
        == hashlib.sha256(
            b"catan-friend-claim-v1\0" + claim_grant.claim_token.encode("ascii")
        ).hexdigest()
    )
    assert claim_digest != ordinary_digest
    assert claim_grant.claim_token not in str(authority)
    assert claim_grant.claim_token not in repr(claim_grant)

    restored = FriendInvitationBook.from_authority_document(authority)
    assert (
        restored.inspect_claim(claim_grant.claim_token, now_ms=1_000_002).invitation_id
        == grant.invitation_id
    )
    assert restored.to_authority_document() == authority


def test_claims_share_expiry_and_first_consumer_invalidates_all_bearers():
    book = make_book()
    invitation = book.issue("spectator", now_ms=10_000, ttl_seconds=300)
    first = book.begin_claim(invitation.token, now_ms=10_001)
    second = book.begin_claim(invitation.token, now_ms=10_002)

    assert book.inspect_claim(first.claim_token, now_ms=309_999).role == "spectator"
    with pytest.raises(FriendInvitationAuthenticationError):
        book.inspect_claim(first.claim_token, now_ms=310_000)

    consumed = book.consume_claim(first.claim_token, now_ms=20_000)
    assert consumed.invitation_id == invitation.invitation_id
    for bearer, inspect in (
        (invitation.token, book.inspect),
        (first.claim_token, book.inspect_claim),
        (second.claim_token, book.inspect_claim),
    ):
        with pytest.raises(FriendInvitationAuthenticationError):
            inspect(bearer, now_ms=20_000)


def test_release_claim_keeps_invitation_and_sibling_claim_usable():
    book = make_book()
    invitation = book.issue("player", now_ms=1_000, ttl_seconds=300)
    released = book.begin_claim(invitation.token, now_ms=1_001)
    sibling = book.begin_claim(invitation.token, now_ms=1_002)

    assert (
        book.release_claim(released.claim_token, now_ms=2_000).invitation_id
        == invitation.invitation_id
    )
    with pytest.raises(FriendInvitationAuthenticationError):
        book.inspect_claim(released.claim_token, now_ms=2_000)
    assert book.inspect_claim(sibling.claim_token, now_ms=2_000).role == "player"
    assert book.inspect(invitation.token, now_ms=2_000).role == "player"


def test_claim_capacity_and_invalid_generators_fail_closed_without_mutation():
    book = make_book()
    invitation = book.issue("player", now_ms=1_000, ttl_seconds=300)
    for index in range(MAX_PENDING_CLAIMS_PER_FRIEND_INVITATION):
        book.begin_claim(invitation.token, now_ms=1_001 + index)
    before = copy.deepcopy(book.to_authority_document())
    with pytest.raises(FriendInvitationAuthenticationError):
        book.begin_claim(invitation.token, now_ms=2_000)
    assert book.to_authority_document() == before

    invalid = FriendInvitationBook(
        ROOM_ID,
        token_bytes_generator=TokenBytes(),
        claim_token_bytes_generator=lambda _size: b"short",
    )
    raw = invalid.issue("player", now_ms=1_000, ttl_seconds=300)
    with pytest.raises(FriendInvitationError):
        invalid.begin_claim(raw.token, now_ms=1_001)
    assert (
        invalid.to_authority_document()["invitations"][0]["claim_token_digests"] == []
    )


def test_v1_authority_migrates_to_canonical_v2_with_empty_claims():
    book = make_book()
    invitation = book.issue("player", now_ms=1_000, ttl_seconds=300)
    legacy = book.to_authority_document()
    legacy["version"] = LEGACY_FRIEND_INVITATION_VERSION
    legacy["invitations"][0].pop("claim_token_digests")

    restored = FriendInvitationBook.from_authority_document(legacy)

    assert restored.inspect(invitation.token, now_ms=2_000).role == "player"
    migrated = restored.to_authority_document()
    assert migrated["version"] == FRIEND_INVITATION_VERSION
    assert migrated["invitations"][0]["claim_token_digests"] == []


def test_claim_generator_retries_raw_and_claim_credential_collisions():
    claim_bytes = TokenBytes()
    book = FriendInvitationBook(
        ROOM_ID,
        token_bytes_generator=TokenBytes(),
        claim_token_bytes_generator=claim_bytes,
    )
    invitation = book.issue("player", now_ms=1_000, ttl_seconds=300)

    first = book.begin_claim(invitation.token, now_ms=1_001)
    second = book.begin_claim(invitation.token, now_ms=1_002)

    # claim material 1 collides textually with the raw invite, then the second
    # claim retries material 2 before accepting material 3.
    assert claim_bytes.calls == 3
    assert first.claim_token != invitation.token
    assert second.claim_token not in {invitation.token, first.claim_token}


def test_v2_authority_rejects_noncanonical_or_ambiguous_claim_digests():
    book = make_book()
    first = book.issue("player", now_ms=1_000, ttl_seconds=300)
    second = book.issue("spectator", now_ms=2_000, ttl_seconds=600)
    book.begin_claim(first.token, now_ms=3_000)
    book.begin_claim(first.token, now_ms=3_001)
    book.begin_claim(second.token, now_ms=3_002)
    authority = book.to_authority_document()
    first_index = next(
        index
        for index, record in enumerate(authority["invitations"])
        if record["token_digest"]
        == hashlib.sha256(first.token.encode("ascii")).hexdigest()
    )
    second_index = 1 - first_index
    first_record = authority["invitations"][first_index]
    second_record = authority["invitations"][second_index]

    cases = []
    reversed_claims = copy.deepcopy(authority)
    reversed_claims["invitations"][first_index]["claim_token_digests"].reverse()
    cases.append(reversed_claims)
    duplicate_across_records = copy.deepcopy(authority)
    duplicate_across_records["invitations"][second_index]["claim_token_digests"] = [
        duplicate_across_records["invitations"][first_index]["claim_token_digests"][0]
    ]
    cases.append(duplicate_across_records)
    collides_with_invite = copy.deepcopy(authority)
    collides_with_invite["invitations"][first_index]["claim_token_digests"] = [
        second_record["token_digest"]
    ]
    cases.append(collides_with_invite)
    too_many = copy.deepcopy(authority)
    too_many["invitations"][first_index]["claim_token_digests"] = [
        f"{index:064x}" for index in range(MAX_PENDING_CLAIMS_PER_FRIEND_INVITATION + 1)
    ]
    cases.append(too_many)
    malformed = copy.deepcopy(authority)
    malformed["invitations"][first_index]["claim_token_digests"] = ["not-a-digest"]
    cases.append(malformed)
    missing = copy.deepcopy(authority)
    missing["invitations"][first_index].pop("claim_token_digests")
    cases.append(missing)

    assert first_record["claim_token_digests"]
    for document in cases:
        with pytest.raises(FriendInvitationError):
            FriendInvitationBook.from_authority_document(document)


def test_active_list_is_canonical_token_free_and_prunes_expired():
    book = make_book()
    expired = book.issue("player", now_ms=1_000, ttl_seconds=300)
    active = book.issue("spectator", now_ms=2_000, ttl_seconds=600)

    listed = book.list_active(now_ms=301_000)

    assert listed == tuple(sorted(listed, key=lambda item: item.invitation_id))
    assert [item.invitation_id for item in listed] == [active.invitation_id]
    assert all(not hasattr(item, "token") for item in listed)
    assert expired.token not in repr(listed)
    assert book.invitation_count == 1


def test_revoke_one_and_all_are_exact_and_bearers_remain_generic():
    book = make_book()
    first = book.issue("player", now_ms=1_000, ttl_seconds=300)
    second = book.issue("spectator", now_ms=2_000, ttl_seconds=600)

    revoked = book.revoke(first.invitation_id, now_ms=3_000)
    assert revoked.invitation_id == first.invitation_id
    assert revoked.role == "player"
    with pytest.raises(FriendInvitationAuthenticationError):
        book.inspect(first.token, now_ms=3_000)
    assert book.inspect(second.token, now_ms=3_000).role == "spectator"

    for unknown in ("A" * 22, "bad", None):
        with pytest.raises(FriendInvitationNotFoundError):
            book.revoke(unknown, now_ms=3_000)

    assert book.revoke_all(now_ms=3_000) == 1
    assert book.revoke_all(now_ms=3_000) == 0
    with pytest.raises(FriendInvitationAuthenticationError):
        book.inspect(second.token, now_ms=3_000)


def test_revoke_all_prunes_expired_before_counting_active_entries():
    book = make_book()
    book.issue("player", now_ms=1_000, ttl_seconds=300)
    book.issue("spectator", now_ms=2_000, ttl_seconds=600)

    assert book.revoke_all(now_ms=301_000) == 1
    assert book.invitation_count == 0


def test_issue_retries_a_management_id_collision(monkeypatch):
    book = make_book()
    digests = iter(
        (
            "00" * 16 + "11" * 16,
            "00" * 16 + "22" * 16,
            "01" * 16 + "33" * 16,
        )
    )
    monkeypatch.setattr(
        friend_invitation_module,
        "_hash_token",
        lambda _token: next(digests),
    )

    first = book.issue("player", now_ms=1_000, ttl_seconds=300)
    second = book.issue("spectator", now_ms=1_000, ttl_seconds=300)

    assert first.invitation_id != second.invitation_id
    assert book.invitation_count == 2


def test_inspect_is_read_only_and_consume_is_exactly_once():
    book = make_book()
    grant = book.issue("spectator", now_ms=10_000, ttl_seconds=600)
    before = copy.deepcopy(book.to_authority_document())

    claim = book.inspect(grant.token, now_ms=20_000)
    assert claim.room_id == ROOM_ID
    assert claim.role == "spectator"
    assert book.to_authority_document() == before

    consumed = book.consume(grant.token, now_ms=20_000)
    assert consumed == claim
    assert book.invitation_count == 0
    with pytest.raises(FriendInvitationAuthenticationError):
        book.consume(grant.token, now_ms=20_000)


def test_expiry_is_fail_closed_and_pruned_at_the_exact_deadline():
    book = make_book()
    grant = book.issue("player", now_ms=2_000_000, ttl_seconds=300)
    assert book.inspect(grant.token, now_ms=2_299_999).role == "player"
    with pytest.raises(FriendInvitationAuthenticationError):
        book.inspect(grant.token, now_ms=2_300_000)
    assert book.prune_expired(now_ms=2_300_000) == 1
    assert book.prune_expired(now_ms=2_300_000) == 0


def test_book_enforces_bounded_role_ttl_and_active_capacity():
    book = make_book()
    for index in range(MAX_FRIEND_INVITATIONS):
        book.issue(
            "player" if index % 2 == 0 else "spectator",
            now_ms=1_000,
            ttl_seconds=300,
        )
    with pytest.raises(FriendInvitationCapacityError):
        book.issue("player", now_ms=1_000, ttl_seconds=300)
    for bad_role in ("host", "", None):
        with pytest.raises(FriendInvitationError):
            make_book().issue(bad_role, now_ms=1_000, ttl_seconds=300)
    for bad_ttl in (299, 604801, True, 300.0):
        with pytest.raises(FriendInvitationError):
            make_book().issue("player", now_ms=1_000, ttl_seconds=bad_ttl)


def test_authority_round_trip_is_exact_canonical_and_rejects_tampering():
    book = make_book()
    first = book.issue("player", now_ms=1_000, ttl_seconds=300)
    second = book.issue("spectator", now_ms=2_000, ttl_seconds=600)
    authority = book.to_authority_document()
    restored = FriendInvitationBook.from_authority_document(authority)

    assert restored.to_authority_document() == authority
    assert restored.inspect(first.token, now_ms=3_000).role == "player"
    assert restored.inspect(second.token, now_ms=3_000).role == "spectator"

    cases = []
    extra = copy.deepcopy(authority)
    extra["extra"] = True
    cases.append(extra)
    duplicate = copy.deepcopy(authority)
    duplicate["invitations"][1]["token_digest"] = duplicate["invitations"][0][
        "token_digest"
    ]
    duplicate["invitations"].sort(key=lambda item: item["token_digest"])
    cases.append(duplicate)
    bad_lifetime = copy.deepcopy(authority)
    bad_lifetime["invitations"][0]["expires_at_ms"] = (
        bad_lifetime["invitations"][0]["issued_at_ms"] + 299_999
    )
    cases.append(bad_lifetime)
    reversed_records = copy.deepcopy(authority)
    reversed_records["invitations"].reverse()
    cases.append(reversed_records)
    colliding_management_ids = copy.deepcopy(authority)
    colliding_management_ids["invitations"][0]["token_digest"] = "00" * 16 + "11" * 16
    colliding_management_ids["invitations"][1]["token_digest"] = "00" * 16 + "22" * 16
    colliding_management_ids["invitations"].sort(key=lambda item: item["token_digest"])
    cases.append(colliding_management_ids)

    for document in cases:
        with pytest.raises(FriendInvitationError):
            FriendInvitationBook.from_authority_document(document)


def test_room_instance_id_is_part_of_claim_and_authority():
    first = make_book()
    token = first.issue("player", now_ms=0, ttl_seconds=300).token
    second = FriendInvitationBook(
        "87654321876543218765432187654321",
        token_bytes_generator=TokenBytes(),
    )

    assert first.inspect(token, now_ms=1).room_id == ROOM_ID
    with pytest.raises(FriendInvitationAuthenticationError):
        second.inspect(token, now_ms=1)
