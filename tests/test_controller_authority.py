from copy import deepcopy
import random

import pytest

from game.controller_authority import (
    CONTROLLER_AUTHORITY_FORMAT,
    CONTROLLER_AUTHORITY_VERSION,
    CommandRecordAuthority,
    CommandStateAuthority,
    ControllerAuthorityError,
    ControllerRoomAuthority,
    MatchAuthority,
    decode_controller_room_authority,
    encode_controller_room_authority,
)
from game.friend_invitation import (
    FRIEND_INVITATION_VERSION,
    LEGACY_FRIEND_INVITATION_VERSION,
    FriendInvitationBook,
)
from game.network_protocol import NETWORK_PROTOCOL_VERSION


def result(sequence, *, accepted=True, revision=3):
    document = {
        "type": "game_command_result",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "sequence": sequence,
        "accepted": accepted,
        "revision": revision,
        "code": None,
        "message": None,
    }
    if not accepted:
        document.update(code="stale_revision", message="最新状態で再試行してください。")
    return document


def authority(*, with_match=True):
    lobby = {"format": "test-lobby", "version": 1, "private": {"token": "hash"}}
    if not with_match:
        return ControllerRoomAuthority(lobby=lobby)
    command_state = CommandStateAuthority(
        "member-1",
        2,
        (
            CommandRecordAuthority(0, "a" * 64, result(0)),
            CommandRecordAuthority(1, "b" * 64, result(1, accepted=False)),
        ),
    )
    return ControllerRoomAuthority(
        lobby=lobby,
        match=MatchAuthority(
            match_seed=123456789,
            game_revision=3,
            random_state=random.Random(86712347).getstate(),
            game={"format": "test-game", "version": 1},
            command_states=(command_state,),
        ),
    )


def test_waiting_room_round_trip_has_exact_detached_schema():
    source = authority(with_match=False)
    document = encode_controller_room_authority(source)

    assert document == {
        "format": CONTROLLER_AUTHORITY_FORMAT,
        "version": CONTROLLER_AUTHORITY_VERSION,
        "lobby": source.lobby,
        "match": None,
        "friend_invitations": source.friend_invitations,
    }
    restored = decode_controller_room_authority(document)
    assert restored == source
    document["lobby"]["private"]["token"] = "changed"
    assert restored.lobby["private"]["token"] == "hash"


def test_live_match_round_trip_preserves_rng_game_and_command_window():
    source = authority()
    document = encode_controller_room_authority(source)
    restored = decode_controller_room_authority(document)

    assert restored == source
    assert document["match"]["match_seed"] == f"{123456789:064x}"
    assert document["match"]["command_states"][0]["member_id"] == "member-1"
    before = random.Random()
    before.setstate(source.match.random_state)
    after = random.Random()
    after.setstate(restored.match.random_state)
    assert [before.random() for _ in range(20)] == [after.random() for _ in range(20)]


@pytest.mark.parametrize(
    "key", ("format", "version", "lobby", "match", "friend_invitations")
)
def test_decode_rejects_missing_and_extra_top_level_keys(key):
    document = encode_controller_room_authority(authority())
    document.pop(key)
    with pytest.raises(ControllerAuthorityError):
        decode_controller_room_authority(document)

    extra = encode_controller_room_authority(authority())
    extra["extra"] = True
    with pytest.raises(ControllerAuthorityError):
        decode_controller_room_authority(extra)


def test_exact_legacy_v1_document_decodes_without_invitation_authority():
    current = encode_controller_room_authority(authority())
    legacy = {
        key: deepcopy(value)
        for key, value in current.items()
        if key != "friend_invitations"
    }
    legacy["version"] = 1

    restored = decode_controller_room_authority(legacy)
    assert restored.lobby == authority().lobby
    assert restored.match is not None
    assert restored.friend_invitations is None


def test_nested_invitation_v1_is_canonically_migrated_without_controller_bump():
    book = FriendInvitationBook("12345678123456781234567812345678")
    book.issue("player", now_ms=1_000, ttl_seconds=300)
    invitation_authority = book.to_authority_document()
    invitation_authority["version"] = LEGACY_FRIEND_INVITATION_VERSION
    invitation_authority["invitations"][0].pop("claim_token_digests")
    document = encode_controller_room_authority(
        ControllerRoomAuthority(
            lobby=authority(with_match=False).lobby,
            friend_invitations=invitation_authority,
        )
    )

    assert document["version"] == CONTROLLER_AUTHORITY_VERSION
    assert document["friend_invitations"]["version"] == FRIEND_INVITATION_VERSION
    assert document["friend_invitations"]["invitations"][0]["claim_token_digests"] == []
    restored = decode_controller_room_authority(document)
    assert restored.friend_invitations == document["friend_invitations"]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("match_seed",), "0" * 63),
        (("match_seed",), "G" * 64),
        (("game_revision",), True),
        (("game_revision",), -1),
        (("game",), []),
        (("command_states",), {}),
    ],
)
def test_decode_rejects_malformed_match_fields(path, value):
    document = encode_controller_room_authority(authority())
    document["match"][path[0]] = value
    with pytest.raises(ControllerAuthorityError):
        decode_controller_room_authority(document)


def test_command_records_must_be_complete_contiguous_and_canonical():
    base = encode_controller_room_authority(authority())
    cases = []
    missing = deepcopy(base)
    missing["match"]["command_states"][0]["records"].pop()
    cases.append(missing)
    gap = deepcopy(base)
    gap["match"]["command_states"][0]["records"][1]["sequence"] = 7
    cases.append(gap)
    mismatch = deepcopy(base)
    mismatch["match"]["command_states"][0]["records"][0]["response"]["sequence"] = 1
    cases.append(mismatch)
    duplicate = deepcopy(base)
    duplicate["match"]["command_states"].append(
        deepcopy(duplicate["match"]["command_states"][0])
    )
    cases.append(duplicate)

    for document in cases:
        with pytest.raises(ControllerAuthorityError):
            decode_controller_room_authority(document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("member_id", "member-0"),
        ("member_id", "member-01"),
        ("next_sequence", True),
        ("next_sequence", -1),
    ],
)
def test_decode_rejects_invalid_command_state_identity_and_cursor(field, value):
    document = encode_controller_room_authority(authority())
    document["match"]["command_states"][0][field] = value
    with pytest.raises(ControllerAuthorityError):
        decode_controller_room_authority(document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fingerprint", "A" * 64),
        ("fingerprint", "a" * 63),
        ("response", []),
    ],
)
def test_decode_rejects_invalid_command_record_fields(field, value):
    document = encode_controller_room_authority(authority())
    document["match"]["command_states"][0]["records"][0][field] = value
    with pytest.raises(ControllerAuthorityError):
        decode_controller_room_authority(document)


def test_decode_rejects_accepted_response_with_error_fields_and_rejected_without_them():
    accepted_extra = encode_controller_room_authority(authority())
    accepted = accepted_extra["match"]["command_states"][0]["records"][0]["response"]
    accepted.update(code="error", message="error")
    rejected_missing = encode_controller_room_authority(authority())
    rejected = rejected_missing["match"]["command_states"][0]["records"][1]["response"]
    rejected["message"] = None

    with pytest.raises(ControllerAuthorityError):
        decode_controller_room_authority(accepted_extra)
    with pytest.raises(ControllerAuthorityError):
        decode_controller_room_authority(rejected_missing)


def test_decode_rejects_cached_response_from_a_future_game_revision():
    document = encode_controller_room_authority(authority())
    document["match"]["command_states"][0]["records"][0]["response"]["revision"] = 4

    with pytest.raises(ControllerAuthorityError):
        decode_controller_room_authority(document)


def test_encode_rejects_unsorted_or_incomplete_command_states():
    state2 = CommandStateAuthority("member-2", 0, ())
    state1 = CommandStateAuthority("member-1", 0, ())
    match = MatchAuthority(
        match_seed=1,
        game_revision=0,
        random_state=random.Random(1).getstate(),
        game={},
        command_states=(state2, state1),
    )
    with pytest.raises(ControllerAuthorityError):
        encode_controller_room_authority(ControllerRoomAuthority({}, match))

    incomplete = CommandStateAuthority(
        "member-1",
        1,
        (),
    )
    with pytest.raises(ControllerAuthorityError):
        encode_controller_room_authority(
            ControllerRoomAuthority(
                {},
                MatchAuthority(
                    match_seed=1,
                    game_revision=0,
                    random_state=random.Random(1).getstate(),
                    game={},
                    command_states=(incomplete,),
                ),
            )
        )
