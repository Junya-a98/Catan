"""Strict persisted authority document for one LAN/Web room.

The SQLite layer authenticates a bounded JSON object but intentionally knows
nothing about game semantics.  This module defines the controller-owned part
of that object: the lobby authority document, optional live game save, the
isolated random state, and the exactly-once command cursor/cache.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Any

from game.friend_invitation import (
    FriendInvitationBook,
    FriendInvitationError,
)
from game.network_protocol import NETWORK_PROTOCOL_VERSION
from game.random_state import (
    RandomStateError,
    decode_random_state,
    encode_random_state,
)


CONTROLLER_AUTHORITY_FORMAT = "catan-controller-room-authority"
CONTROLLER_AUTHORITY_VERSION = 2
_LEGACY_CONTROLLER_AUTHORITY_VERSION = 1
MAX_AUTHORITY_SEQUENCE = (1 << 63) - 1
MAX_COMMAND_RECORDS = 128
MAX_COMMAND_STATES = 4
MAX_COMMAND_ERROR_MESSAGE_CHARACTERS = 512

_TOP_LEVEL_KEYS_V1 = frozenset({"format", "version", "lobby", "match"})
_TOP_LEVEL_KEYS_V2 = frozenset(
    {"format", "version", "lobby", "match", "friend_invitations"}
)
_MATCH_KEYS = frozenset(
    {
        "match_seed",
        "game_revision",
        "random_state",
        "game",
        "command_states",
    }
)
_COMMAND_STATE_KEYS = frozenset({"member_id", "next_sequence", "records"})
_COMMAND_RECORD_KEYS = frozenset({"sequence", "fingerprint", "response"})
_COMMAND_RESPONSE_KEYS = frozenset(
    {
        "type",
        "protocol_version",
        "sequence",
        "accepted",
        "revision",
        "code",
        "message",
    }
)
_MATCH_SEED_RE = re.compile(r"[0-9a-f]{64}\Z")
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}\Z")
_MEMBER_ID_RE = re.compile(r"member-[1-9][0-9]{0,8}\Z")
_ERROR_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class ControllerAuthorityError(ValueError):
    """Raised when persisted controller authority is malformed."""


@dataclass(frozen=True)
class CommandRecordAuthority:
    sequence: int
    fingerprint: str
    response: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class CommandStateAuthority:
    member_id: str
    next_sequence: int
    records: tuple[CommandRecordAuthority, ...] = ()


@dataclass(frozen=True)
class MatchAuthority:
    match_seed: int = field(repr=False)
    game_revision: int
    random_state: object = field(repr=False)
    game: dict[str, Any] = field(repr=False)
    command_states: tuple[CommandStateAuthority, ...] = ()


@dataclass(frozen=True)
class ControllerRoomAuthority:
    lobby: dict[str, Any] = field(repr=False)
    match: MatchAuthority | None = field(default=None, repr=False)
    friend_invitations: dict[str, Any] | None = field(
        default_factory=lambda: FriendInvitationBook.create().to_authority_document(),
        repr=False,
    )


def encode_controller_room_authority(
    authority: ControllerRoomAuthority,
) -> dict[str, Any]:
    """Return a detached exact-schema JSON document."""

    if not isinstance(authority, ControllerRoomAuthority):
        raise ControllerAuthorityError("controller authority value is invalid")
    if type(authority.lobby) is not dict:
        raise ControllerAuthorityError("lobby authority must be an object")
    document: dict[str, Any] = {
        "format": CONTROLLER_AUTHORITY_FORMAT,
        "version": CONTROLLER_AUTHORITY_VERSION,
        "lobby": deepcopy(authority.lobby),
        "match": None,
        "friend_invitations": _validated_friend_invitations(
            authority.friend_invitations
        ),
    }
    if authority.match is not None:
        document["match"] = _encode_match(authority.match)
    # Decode the freshly-built document as one final invariant check.  This
    # catches malformed response caches supplied by controller test doubles.
    decode_controller_room_authority(document)
    return document


def decode_controller_room_authority(document: object) -> ControllerRoomAuthority:
    """Parse one exact controller document into detached typed values."""

    if type(document) is not dict:
        raise ControllerAuthorityError("controller authority has invalid keys")
    version = document.get("version")
    if type(version) is not int or version not in {
        _LEGACY_CONTROLLER_AUTHORITY_VERSION,
        CONTROLLER_AUTHORITY_VERSION,
    }:
        raise ControllerAuthorityError("controller authority version is unsupported")
    expected_keys = (
        _TOP_LEVEL_KEYS_V1
        if version == _LEGACY_CONTROLLER_AUTHORITY_VERSION
        else _TOP_LEVEL_KEYS_V2
    )
    if set(document) != expected_keys:
        raise ControllerAuthorityError("controller authority has invalid keys")
    if document["format"] != CONTROLLER_AUTHORITY_FORMAT:
        raise ControllerAuthorityError("controller authority format is unsupported")
    lobby = document["lobby"]
    if type(lobby) is not dict:
        raise ControllerAuthorityError("lobby authority must be an object")
    raw_match = document["match"]
    match = None if raw_match is None else _decode_match(raw_match)
    friend_invitations = (
        None
        if version == _LEGACY_CONTROLLER_AUTHORITY_VERSION
        else _validated_friend_invitations(document["friend_invitations"])
    )
    return ControllerRoomAuthority(
        lobby=deepcopy(lobby),
        match=match,
        friend_invitations=friend_invitations,
    )


def _validated_friend_invitations(document: object) -> dict[str, Any]:
    if type(document) is not dict:
        raise ControllerAuthorityError(
            "friend invitation authority must be an object"
        )
    try:
        book = FriendInvitationBook.from_authority_document(document)
    except FriendInvitationError as exc:
        raise ControllerAuthorityError(
            "friend invitation authority is invalid"
        ) from exc
    return deepcopy(book.to_authority_document())


def _encode_match(match: MatchAuthority) -> dict[str, Any]:
    if not isinstance(match, MatchAuthority):
        raise ControllerAuthorityError("match authority value is invalid")
    seed = _validated_match_seed(match.match_seed)
    revision = _validated_counter(match.game_revision, label="game revision")
    if type(match.game) is not dict:
        raise ControllerAuthorityError("game authority must be an object")
    try:
        random_document = encode_random_state(match.random_state)
    except RandomStateError as exc:
        raise ControllerAuthorityError("match random state is invalid") from exc
    command_states = _validated_command_states(
        match.command_states,
        max_revision=revision,
    )
    return {
        "match_seed": f"{seed:064x}",
        "game_revision": revision,
        "random_state": random_document,
        "game": deepcopy(match.game),
        "command_states": [
            {
                "member_id": state.member_id,
                "next_sequence": state.next_sequence,
                "records": [
                    {
                        "sequence": record.sequence,
                        "fingerprint": record.fingerprint,
                        "response": deepcopy(record.response),
                    }
                    for record in state.records
                ],
            }
            for state in command_states
        ],
    }


def _decode_match(document: object) -> MatchAuthority:
    if type(document) is not dict or set(document) != _MATCH_KEYS:
        raise ControllerAuthorityError("match authority has invalid keys")
    raw_seed = document["match_seed"]
    if type(raw_seed) is not str or _MATCH_SEED_RE.fullmatch(raw_seed) is None:
        raise ControllerAuthorityError("match seed is invalid")
    seed = _validated_match_seed(int(raw_seed, 16))
    revision = _validated_counter(document["game_revision"], label="game revision")
    try:
        random_state = decode_random_state(document["random_state"])
    except RandomStateError as exc:
        raise ControllerAuthorityError("match random state is invalid") from exc
    game = document["game"]
    if type(game) is not dict:
        raise ControllerAuthorityError("game authority must be an object")
    raw_states = document["command_states"]
    if type(raw_states) is not list or len(raw_states) > MAX_COMMAND_STATES:
        raise ControllerAuthorityError("command state collection is invalid")
    states = tuple(_decode_command_state(item) for item in raw_states)
    states = _validated_command_states(states, max_revision=revision)
    return MatchAuthority(
        match_seed=seed,
        game_revision=revision,
        random_state=random_state,
        game=deepcopy(game),
        command_states=states,
    )


def _validated_command_states(
    states: object,
    *,
    max_revision: int,
) -> tuple[CommandStateAuthority, ...]:
    if type(states) is not tuple or len(states) > MAX_COMMAND_STATES:
        raise ControllerAuthorityError("command states must be a bounded tuple")
    checked: list[CommandStateAuthority] = []
    seen: set[str] = set()
    for state in states:
        if not isinstance(state, CommandStateAuthority):
            raise ControllerAuthorityError("command state value is invalid")
        member_id = _validated_member_id(state.member_id)
        if member_id in seen:
            raise ControllerAuthorityError("command state member is duplicated")
        seen.add(member_id)
        next_sequence = _validated_counter(
            state.next_sequence,
            label="next command sequence",
        )
        if type(state.records) is not tuple or len(state.records) > MAX_COMMAND_RECORDS:
            raise ControllerAuthorityError("command records must be a bounded tuple")
        expected_count = min(next_sequence, MAX_COMMAND_RECORDS)
        if len(state.records) != expected_count:
            raise ControllerAuthorityError("command record window is incomplete")
        first_sequence = next_sequence - expected_count
        records: list[CommandRecordAuthority] = []
        for offset, record in enumerate(state.records):
            if not isinstance(record, CommandRecordAuthority):
                raise ControllerAuthorityError("command record value is invalid")
            sequence = _validated_counter(record.sequence, label="command sequence")
            if sequence != first_sequence + offset:
                raise ControllerAuthorityError("command record sequence is not contiguous")
            fingerprint = record.fingerprint
            if (
                type(fingerprint) is not str
                or _FINGERPRINT_RE.fullmatch(fingerprint) is None
            ):
                raise ControllerAuthorityError("command fingerprint is invalid")
            response = _validated_command_response(record.response, sequence=sequence)
            if response["revision"] > max_revision:
                raise ControllerAuthorityError(
                    "command response revision is in the future"
                )
            records.append(CommandRecordAuthority(sequence, fingerprint, response))
        checked.append(CommandStateAuthority(member_id, next_sequence, tuple(records)))
    checked.sort(key=lambda state: state.member_id)
    if tuple(state.member_id for state in checked) != tuple(
        state.member_id for state in states
    ):
        raise ControllerAuthorityError("command states are not canonically ordered")
    return tuple(checked)


def _decode_command_state(document: object) -> CommandStateAuthority:
    if type(document) is not dict or set(document) != _COMMAND_STATE_KEYS:
        raise ControllerAuthorityError("command state document has invalid keys")
    member_id = _validated_member_id(document["member_id"])
    next_sequence = _validated_counter(
        document["next_sequence"],
        label="next command sequence",
    )
    raw_records = document["records"]
    if type(raw_records) is not list or len(raw_records) > MAX_COMMAND_RECORDS:
        raise ControllerAuthorityError("command record collection is invalid")
    records = tuple(_decode_command_record(item) for item in raw_records)
    return CommandStateAuthority(member_id, next_sequence, records)


def _decode_command_record(document: object) -> CommandRecordAuthority:
    if type(document) is not dict or set(document) != _COMMAND_RECORD_KEYS:
        raise ControllerAuthorityError("command record document has invalid keys")
    sequence = _validated_counter(document["sequence"], label="command sequence")
    fingerprint = document["fingerprint"]
    if type(fingerprint) is not str or _FINGERPRINT_RE.fullmatch(fingerprint) is None:
        raise ControllerAuthorityError("command fingerprint is invalid")
    response = _validated_command_response(document["response"], sequence=sequence)
    return CommandRecordAuthority(sequence, fingerprint, response)


def _validated_command_response(response: object, *, sequence: int) -> dict[str, Any]:
    if type(response) is not dict:
        raise ControllerAuthorityError("command response must be an object")
    accepted = response.get("accepted")
    if type(accepted) is not bool or set(response) != _COMMAND_RESPONSE_KEYS:
        raise ControllerAuthorityError("command response has invalid keys")
    if response["type"] != "game_command_result":
        raise ControllerAuthorityError("command response type is invalid")
    if (
        type(response["protocol_version"]) is not int
        or response["protocol_version"] != NETWORK_PROTOCOL_VERSION
    ):
        raise ControllerAuthorityError("command response protocol is invalid")
    if response["sequence"] != sequence:
        raise ControllerAuthorityError("command response sequence is invalid")
    _validated_counter(response["revision"], label="command response revision")
    if accepted:
        if response["code"] is not None or response["message"] is not None:
            raise ControllerAuthorityError(
                "accepted command response contains an error"
            )
    else:
        code = response["code"]
        message = response["message"]
        if type(code) is not str or _ERROR_CODE_RE.fullmatch(code) is None:
            raise ControllerAuthorityError("command response code is invalid")
        if (
            type(message) is not str
            or not message
            or len(message) > MAX_COMMAND_ERROR_MESSAGE_CHARACTERS
            or any(ord(character) < 32 and character not in "\t\n" for character in message)
        ):
            raise ControllerAuthorityError("command response message is invalid")
    return deepcopy(response)


def _validated_member_id(value: object) -> str:
    if type(value) is not str or _MEMBER_ID_RE.fullmatch(value) is None:
        raise ControllerAuthorityError("command state member_id is invalid")
    return value


def _validated_match_seed(value: object) -> int:
    if type(value) is not int or not 0 <= value < (1 << 256):
        raise ControllerAuthorityError("match seed is invalid")
    return value


def _validated_counter(value: object, *, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_AUTHORITY_SEQUENCE:
        raise ControllerAuthorityError(f"{label} is invalid")
    return value


__all__ = (
    "CONTROLLER_AUTHORITY_FORMAT",
    "CONTROLLER_AUTHORITY_VERSION",
    "CommandRecordAuthority",
    "CommandStateAuthority",
    "ControllerAuthorityError",
    "ControllerRoomAuthority",
    "MAX_AUTHORITY_SEQUENCE",
    "MAX_COMMAND_RECORDS",
    "MAX_COMMAND_STATES",
    "MatchAuthority",
    "decode_controller_room_authority",
    "encode_controller_room_authority",
)
