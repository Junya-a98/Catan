import base64
import hashlib
import json
import re

import pytest

from game.custom_map import CustomMapSpec
from game.development_cards import DevelopmentCardType
from game.game_board import GameBoard
from game.house_rules import HouseRules
from game.lan_lobby import (
    DEFAULT_SEAT_RESERVATION_SECONDS,
    MAX_LOBBY_MEMBERS,
    LobbyAuthenticationError,
    LobbyCapacityError,
    LobbyPermissionError,
    LobbyRoom,
    LobbyStateError,
    LobbyValidationError,
    MemberRole,
    RoomPhase,
    RoomSettings,
)
from game.variant import VariantConfig


class FakeClock:
    def __init__(self, value=1000.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class DeterministicTokenBytes:
    def __init__(self):
        self.calls = 0

    def __call__(self, size):
        self.calls += 1
        return bytes([self.calls]) * size


def make_room(*, player_count=3, reservation_seconds=120.0):
    clock = FakeClock()
    tokens = DeterministicTokenBytes()
    room, host = LobbyRoom.create(
        RoomSettings(
            player_count=player_count,
            victory_target=10,
            board_mode="constrained",
            board_seed=86712347,
        ),
        host_name="Host",
        connection_id="conn-host",
        clock=clock,
        code_generator=lambda: "ABC234",
        token_bytes_generator=tokens,
        reservation_seconds=reservation_seconds,
    )
    return room, host, clock, tokens


@pytest.mark.parametrize(
    "kwargs",
    [
        {"player_count": 1},
        {"player_count": 5},
        {"player_count": True},
        {"victory_target": 4},
        {"victory_target": 16},
        {"victory_target": False},
        {"board_mode": "balanced"},
        {"board_mode": "<script>"},
        {"board_seed": "42"},
        {"board_seed": True},
        {"board_seed": 1 << 53},
        {"board_seed": -(1 << 53)},
        {"ai_player_count": -1},
        {"ai_player_count": 4},
        {"ai_player_count": True},
        {"ai_personality_mode": "builder"},
        {"ai_personality_mode": "unknown"},
        {"ai_personality_mode": None},
    ],
)
def test_room_settings_reject_invalid_values(kwargs):
    with pytest.raises(LobbyValidationError):
        RoomSettings(**kwargs)


def test_room_settings_are_json_safe_and_allow_both_modes_and_integer_seeds():
    settings = RoomSettings(
        player_count=2,
        victory_target=5,
        board_mode="fully_random",
        board_seed=-42,
    )

    assert settings.to_public_dict() == {
        "player_count": 2,
        "victory_target": 5,
        "board_mode": "fully_random",
        "board_seed": -42,
        "ai_player_count": 0,
        "ai_personality_mode": "standard",
        "variant": VariantConfig.standard().to_document(),
    }
    assert json.loads(json.dumps(settings.to_public_dict()))["board_seed"] == -42


def test_ai_seats_are_public_ready_members_and_only_humans_consume_connections():
    settings = RoomSettings(
        player_count=4,
        victory_target=10,
        board_mode="constrained",
        board_seed=73,
        ai_player_count=2,
        ai_personality_mode="mixed",
    )
    room, _host = LobbyRoom.create(
        settings,
        host_name="Host",
        connection_id="host",
        code_generator=lambda: "ABC234",
        token_bytes_generator=DeterministicTokenBytes(),
    )

    first = room.public_snapshot()
    assert first["settings"]["ai_player_count"] == 2
    assert first["settings"]["ai_personality_mode"] == "mixed"
    assert first["player_members"] == 3
    assert first["full"] is False
    assert first["can_start"] is False
    assert first["members"][1:] == [
        {
            "display_name": "CPU1",
            "role": "player",
            "seat": 3,
            "connected": True,
            "ready": True,
            "reservation_seconds_remaining": None,
            "is_ai": True,
            "ai_personality": None,
        },
        {
            "display_name": "CPU2",
            "role": "player",
            "seat": 4,
            "connected": True,
            "ready": True,
            "reservation_seconds_remaining": None,
            "is_ai": True,
            "ai_personality": None,
        },
    ]

    human = room.join_player(display_name="Friend", connection_id="friend")
    assert human.seat == 2
    assert room.is_full is True
    with pytest.raises(LobbyCapacityError):
        room.join_player(display_name="Too Late", connection_id="late")

    room.set_ready("host")
    room.set_ready("friend")
    assert room.can_start is True


def test_ai_display_names_are_reserved_from_human_members():
    settings = RoomSettings(player_count=2, ai_player_count=1)

    with pytest.raises(LobbyValidationError, match="display_name"):
        LobbyRoom.create(
            settings,
            host_name="cpu1",
            connection_id="host",
            code_generator=lambda: "ABC234",
            token_bytes_generator=DeterministicTokenBytes(),
        )


def test_room_settings_canonicalize_custom_map_and_house_rule_documents():
    custom_map = CustomMapSpec.from_board(GameBoard(seed=73))
    house_rules = HouseRules(
        bank_trade_3_to_1=True,
        disabled_development_cards=frozenset({DevelopmentCardType.MONOPOLY}),
    )
    map_document = custom_map.to_document()
    rules_document = house_rules.to_document()

    settings = RoomSettings(
        player_count=2,
        victory_target=9,
        board_mode="custom",
        board_seed=73,
        custom_map=map_document,
        house_rules=rules_document,
    )
    map_document["tiles"][0]["resource"] = "DESERT"
    rules_document["bank_trade_3_to_1"] = False

    assert settings.custom_map == custom_map
    assert settings.house_rules == house_rules
    public = settings.to_public_dict()
    assert public["custom_map"] == custom_map.to_document()
    assert public["house_rules"] == house_rules.to_document()
    assert json.loads(json.dumps(public)) == public


@pytest.mark.parametrize(
    "kwargs",
    [
        {"board_mode": "custom"},
        {
            "board_mode": "constrained",
            "custom_map": CustomMapSpec.from_board(GameBoard(seed=4)),
        },
        {"house_rules": {}},
        {"house_rules": {"bank_trade_3_to_1": True}},
        {"custom_map": {"format": "not-a-custom-map"}},
    ],
)
def test_room_settings_reject_incomplete_or_cross_mode_custom_settings(kwargs):
    with pytest.raises(LobbyValidationError):
        RoomSettings(**kwargs)


def test_room_code_and_reconnect_tokens_use_safe_production_shapes():
    room, host, _clock, tokens = make_room()

    assert room.room_code == "ABC234"
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{6}", room.room_code)
    assert host.role is MemberRole.HOST
    assert host.seat == 1
    assert host.revision == room.revision == 1
    assert host.reconnect_token is not None
    padded = host.reconnect_token + "=" * (-len(host.reconnect_token) % 4)
    assert len(base64.urlsafe_b64decode(padded)) == 32
    assert tokens.calls == 1

    member = room._members[host.member_id]
    assert len(member.reconnect_token_hash) == 64
    assert member.reconnect_token_hash != host.reconnect_token
    assert host.reconnect_token not in repr(room.__dict__)


def test_default_generators_produce_safe_code_and_256_bit_token():
    room, host = LobbyRoom.create(
        RoomSettings(),
        host_name="Host",
        connection_id="host",
    )

    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{6}", room.room_code)
    padded = host.reconnect_token + "=" * (-len(host.reconnect_token) % 4)
    assert len(base64.urlsafe_b64decode(padded)) >= 32


@pytest.mark.parametrize("code", ["abc234", "ABC123", "ABCDO2", "ABCDE", "ABC2345"])
def test_injected_room_code_must_be_six_unambiguous_characters(code):
    with pytest.raises(LobbyValidationError, match="room code"):
        LobbyRoom.create(
            RoomSettings(),
            host_name="Host",
            connection_id="host",
            code_generator=lambda: code,
            token_bytes_generator=DeterministicTokenBytes(),
        )


def test_player_capacity_counts_reserved_seats_but_never_spectators():
    room, _host, _clock, _tokens = make_room(player_count=2)
    player = room.join_player(display_name="Player", connection_id="conn-player")
    spectator = room.join_spectator(display_name="Viewer", connection_id="conn-viewer")

    assert player.seat == 2
    assert spectator.role is MemberRole.SPECTATOR
    assert spectator.seat is None
    assert room.is_full is True
    snapshot = room.public_snapshot()
    assert snapshot["full"] is True
    assert snapshot["player_members"] == 2
    assert snapshot["spectators"] == 1
    assert [member["role"] for member in snapshot["members"]] == [
        "host",
        "player",
        "spectator",
    ]

    with pytest.raises(LobbyCapacityError):
        room.join_player(display_name="Too Late", connection_id="conn-late")


def test_lobby_member_limit_accepts_exact_boundary_and_rejects_atomically():
    room, _host, _clock, tokens = make_room(player_count=2)
    for index in range(MAX_LOBBY_MEMBERS - 1):
        room.join_spectator(
            display_name=f"Viewer {index}",
            connection_id=f"conn-viewer-{index}",
        )

    assert len(room._members) == MAX_LOBBY_MEMBERS
    assert room.public_snapshot()["spectators"] == MAX_LOBBY_MEMBERS - 1
    revision_before = room.revision
    token_calls_before = tokens.calls
    authority_before = room.to_authority_document(wall_clock_ms=1_000_000)

    attempts = (
        lambda: room.join_spectator(
            display_name="Viewer Over Limit",
            connection_id="conn-viewer-over-limit",
        ),
        lambda: room.join_player(
            display_name="Player Over Limit",
            connection_id="conn-player-over-limit",
        ),
    )
    for attempt in attempts:
        with pytest.raises(LobbyCapacityError, match="membership limit"):
            attempt()
        assert room.revision == revision_before
        assert tokens.calls == token_calls_before
        assert (
            room.to_authority_document(wall_clock_ms=1_000_000)
            == authority_before
        )


def test_only_host_can_start_after_every_seat_is_connected_and_ready():
    room, host, _clock, _tokens = make_room(player_count=3)
    second = room.join_player(display_name="Second", connection_id="conn-second")
    third = room.join_player(display_name="Third", connection_id="conn-third")

    assert room.can_start is False
    room.set_ready("conn-host")
    room.set_ready("conn-second")
    with pytest.raises(LobbyStateError, match="connected and ready"):
        room.start("conn-host")
    room.set_ready("conn-third")
    assert room.can_start is True

    with pytest.raises(LobbyPermissionError, match="only the host"):
        room.start("conn-second")
    revision = room.start("conn-host")

    assert revision == room.revision
    assert room.phase is RoomPhase.STARTED
    assert room.require_player_seat("conn-host") == host.seat == 1
    assert room.require_player_seat("conn-second") == second.seat == 2
    assert room.require_player_seat("conn-third") == third.seat == 3
    with pytest.raises(LobbyStateError):
        room.start("conn-host")
    with pytest.raises(LobbyStateError):
        room.join_player(display_name="Late", connection_id="conn-late")


def test_spectator_has_no_seat_ready_state_or_game_action_authority():
    room, _host, _clock, _tokens = make_room(player_count=2)
    room.join_player(display_name="Player", connection_id="conn-player")
    spectator = room.join_spectator(display_name="Viewer", connection_id="conn-viewer")

    with pytest.raises(LobbyPermissionError):
        room.set_ready("conn-viewer")
    room.set_ready("conn-host")
    room.set_ready("conn-player")
    room.start("conn-host")
    with pytest.raises(LobbyPermissionError, match="Spectators|spectators"):
        room.require_player_seat("conn-viewer")

    late_viewer = room.join_spectator(
        display_name="Late Viewer", connection_id="conn-late-viewer"
    )
    assert spectator.seat is None
    assert late_viewer.seat is None


def test_reconnect_token_restores_same_member_seat_and_ready_state():
    room, _host, clock, _tokens = make_room(player_count=2)
    player = room.join_player(display_name="Player", connection_id="conn-old")
    room.set_ready("conn-old")
    revision_before_disconnect = room.revision

    deadline = room.disconnect("conn-old")

    assert deadline == clock.value + DEFAULT_SEAT_RESERVATION_SECONDS
    assert room.revision == revision_before_disconnect + 1
    assert room.is_full is True
    assert room.can_start is False
    disconnected = next(
        member
        for member in room.public_snapshot()["members"]
        if member["display_name"] == "Player"
    )
    assert disconnected["connected"] is False
    assert disconnected["ready"] is True
    assert disconnected["reservation_seconds_remaining"] == 120.0

    restored = room.reconnect(
        reconnect_token=player.reconnect_token,
        connection_id="conn-new",
    )

    assert restored.member_id == player.member_id
    assert restored.seat == player.seat == 2
    assert restored.role is player.role
    assert restored.reconnect_token is None
    assert room.public_snapshot()["members"][1]["ready"] is True
    with pytest.raises(LobbyAuthenticationError):
        room.disconnect("conn-old")
    with pytest.raises(LobbyStateError, match="already connected"):
        room.reconnect(
            reconnect_token=player.reconnect_token,
            connection_id="conn-hijack",
        )


def test_rotating_reconnect_replaces_current_and_retains_presented_token_as_previous():
    room, _host, clock, tokens = make_room(player_count=2)
    player = room.join_player(display_name="Player", connection_id="conn-old")
    original_token = player.reconnect_token
    room.disconnect("conn-old")
    revision_before = room.revision

    rotated = room.reconnect_rotating(
        reconnect_token=original_token,
        connection_id="conn-new",
        previous_token_grace_seconds=30.0,
    )

    assert rotated.member_id == player.member_id
    assert rotated.seat == player.seat
    assert rotated.reconnect_token is not None
    assert rotated.reconnect_token != original_token
    assert rotated.revision == room.revision == revision_before + 1
    member = room._members[player.member_id]
    assert (
        member.reconnect_token_hash
        == hashlib.sha256(rotated.reconnect_token.encode("ascii")).hexdigest()
    )
    assert (
        member.previous_reconnect_token_hash
        == hashlib.sha256(original_token.encode("ascii")).hexdigest()
    )
    assert member.previous_reconnect_token_expires_at == clock.value + 30.0
    assert tokens.calls == 3


def test_previous_token_rotation_retry_keeps_original_absolute_grace_deadline():
    room, _host, clock, _tokens = make_room(player_count=2)
    player = room.join_player(display_name="Player", connection_id="conn-old")
    original_token = player.reconnect_token
    room.disconnect("conn-old")
    first = room.reconnect_rotating(
        reconnect_token=original_token,
        connection_id="conn-first",
        previous_token_grace_seconds=30.0,
    )
    original_deadline = room._members[
        player.member_id
    ].previous_reconnect_token_expires_at
    room.disconnect("conn-first")
    clock.advance(10.0)

    retry = room.reconnect_rotating(
        reconnect_token=original_token,
        connection_id="conn-retry",
        previous_token_grace_seconds=300.0,
    )

    assert retry.reconnect_token is not None
    assert retry.reconnect_token != first.reconnect_token
    member = room._members[player.member_id]
    assert (
        member.previous_reconnect_token_hash
        == hashlib.sha256(original_token.encode("ascii")).hexdigest()
    )
    assert member.previous_reconnect_token_expires_at == original_deadline
    assert member.previous_reconnect_token_expires_at == clock.value + 20.0


def test_expired_previous_rotation_token_is_rejected_while_current_token_works():
    room, _host, clock, _tokens = make_room(player_count=2)
    player = room.join_player(display_name="Player", connection_id="conn-old")
    original_token = player.reconnect_token
    room.disconnect("conn-old")
    rotated = room.reconnect_rotating(
        reconnect_token=original_token,
        connection_id="conn-first",
        previous_token_grace_seconds=5.0,
    )
    room.disconnect("conn-first")
    clock.advance(5.0)

    with pytest.raises(LobbyAuthenticationError, match="expired reconnect token"):
        room.reconnect_rotating(
            reconnect_token=original_token,
            connection_id="conn-expired",
        )

    current = room.reconnect_rotating(
        reconnect_token=rotated.reconnect_token,
        connection_id="conn-current",
        previous_token_grace_seconds=7.0,
    )
    assert current.member_id == player.member_id
    assert current.reconnect_token not in {None, rotated.reconnect_token}


def test_confirming_current_rotation_revokes_previous_and_is_idempotent():
    room, _host, _clock, _tokens = make_room(player_count=2)
    player = room.join_player(display_name="Player", connection_id="conn-old")
    original_token = player.reconnect_token
    room.disconnect("conn-old")
    rotated = room.reconnect_rotating(
        reconnect_token=original_token,
        connection_id="conn-new",
    )
    revision_before = room.revision

    assert (
        room.confirm_reconnect_rotation(
            connection_id="conn-new",
            reconnect_token=rotated.reconnect_token,
        )
        is True
    )
    assert room.revision == revision_before
    member = room._members[player.member_id]
    assert member.previous_reconnect_token_hash is None
    assert member.previous_reconnect_token_expires_at is None
    assert (
        room.confirm_reconnect_rotation(
            connection_id="conn-new",
            reconnect_token=rotated.reconnect_token,
        )
        is False
    )
    assert room.revision == revision_before

    room.disconnect("conn-new")
    with pytest.raises(LobbyAuthenticationError, match="expired reconnect token"):
        room.reconnect_rotating(
            reconnect_token=original_token,
            connection_id="conn-previous",
        )


def test_rotation_confirmation_rejects_previous_token_without_mutating_state():
    room, _host, _clock, _tokens = make_room(player_count=2)
    player = room.join_player(display_name="Player", connection_id="conn-old")
    original_token = player.reconnect_token
    room.disconnect("conn-old")
    rotated = room.reconnect_rotating(
        reconnect_token=original_token,
        connection_id="conn-new",
    )
    member = room._members[player.member_id]
    previous_hash = member.previous_reconnect_token_hash
    previous_deadline = member.previous_reconnect_token_expires_at

    with pytest.raises(LobbyAuthenticationError, match="expired reconnect token"):
        room.confirm_reconnect_rotation(
            connection_id="conn-new",
            reconnect_token=original_token,
        )

    assert member.previous_reconnect_token_hash == previous_hash
    assert member.previous_reconnect_token_expires_at == previous_deadline
    assert (
        room.confirm_reconnect_rotation(
            connection_id="conn-new",
            reconnect_token=rotated.reconnect_token,
        )
        is True
    )


def test_lan_reconnect_keeps_the_original_token_stable_across_repeated_disconnects():
    room, _host, clock, tokens = make_room(player_count=2)
    player = room.join_player(display_name="Player", connection_id="conn-old")
    original_token = player.reconnect_token
    room.disconnect("conn-old")

    first = room.reconnect(
        reconnect_token=original_token,
        connection_id="conn-first",
    )
    room.disconnect("conn-first")
    clock.advance(1.0)
    second = room.reconnect(
        reconnect_token=original_token,
        connection_id="conn-second",
    )

    assert first.reconnect_token is None
    assert second.reconnect_token is None
    member = room._members[player.member_id]
    assert (
        member.reconnect_token_hash
        == hashlib.sha256(original_token.encode("ascii")).hexdigest()
    )
    assert member.previous_reconnect_token_hash is None
    assert member.previous_reconnect_token_expires_at is None
    assert tokens.calls == 2


def test_expired_reservations_are_pruned_once_and_token_cannot_reconnect():
    room, _host, clock, _tokens = make_room(player_count=3)
    second = room.join_player(display_name="Second", connection_id="conn-second")
    third = room.join_player(display_name="Third", connection_id="conn-third")
    room.disconnect("conn-second")
    room.disconnect("conn-third")
    revision = room.revision

    clock.advance(119.999)
    assert room.prune_expired() == ()
    assert room.revision == revision
    assert room.is_full is True

    clock.advance(0.001)
    removed = room.prune_expired()

    assert removed == (second.member_id, third.member_id)
    assert room.revision == revision + 1
    assert room.is_full is False
    with pytest.raises(LobbyAuthenticationError, match="expired"):
        room.reconnect(
            reconnect_token=second.reconnect_token,
            connection_id="conn-returned",
        )
    replacement = room.join_player(
        display_name="Replacement", connection_id="conn-replacement"
    )
    assert replacement.seat == 2


def test_host_leave_promotes_connected_player_and_room_can_still_start():
    room, _host, _clock, _tokens = make_room(player_count=2)
    room.join_player(display_name="Second", connection_id="conn-second")
    revision = room.revision

    room.leave("conn-host")

    assert room.revision == revision + 1
    promoted = room.public_snapshot()["members"][0]
    assert promoted["display_name"] == "Second"
    assert promoted["role"] == "host"
    assert promoted["seat"] == 2
    room.join_player(display_name="Replacement", connection_id="conn-replacement")
    room.set_ready("conn-second")
    room.set_ready("conn-replacement")
    room.start("conn-second")
    assert room.phase is RoomPhase.STARTED


def test_expired_host_reservation_promotes_lowest_connected_player():
    room, host, clock, _tokens = make_room(player_count=2)
    room.join_player(display_name="Second", connection_id="conn-second")
    room.disconnect("conn-host")
    clock.advance(DEFAULT_SEAT_RESERVATION_SECONDS)

    assert room.prune_expired() == (host.member_id,)
    promoted = room.public_snapshot()["members"][0]
    assert promoted["display_name"] == "Second"
    assert promoted["role"] == "host"
    with pytest.raises(LobbyAuthenticationError, match="expired"):
        room.reconnect(
            reconnect_token=host.reconnect_token,
            connection_id="conn-returned",
        )


def test_first_player_joining_spectator_only_room_becomes_host():
    room, host, clock, _tokens = make_room(player_count=2)
    room.join_spectator(display_name="Viewer", connection_id="conn-viewer")
    room.disconnect("conn-host")
    clock.advance(DEFAULT_SEAT_RESERVATION_SECONDS)
    assert room.prune_expired() == (host.member_id,)

    joined = room.join_player(
        display_name="New Host",
        connection_id="conn-new-host",
    )

    assert joined.role is MemberRole.HOST
    member = next(
        member
        for member in room.public_snapshot()["members"]
        if member["display_name"] == "New Host"
    )
    assert member["role"] == "host"


def test_started_player_expiry_is_preserved_as_match_abort_marker():
    room, _host, clock, _tokens = make_room(player_count=2)
    player = room.join_player(display_name="Second", connection_id="conn-second")
    room.set_ready("conn-host")
    room.set_ready("conn-second")
    room.start("conn-host")
    room.disconnect("conn-second")
    clock.advance(DEFAULT_SEAT_RESERVATION_SECONDS)

    assert room.has_expired_player_reservation() is True
    assert room.prune_expired() == ()
    with pytest.raises(LobbyAuthenticationError, match="expired"):
        room.reconnect(
            reconnect_token=player.reconnect_token,
            connection_id="conn-returned",
        )
    with pytest.raises(LobbyStateError, match="cannot leave"):
        room.leave("conn-host")


def test_revision_only_advances_for_actual_state_changes():
    room, _host, _clock, _tokens = make_room(player_count=2)
    revisions = [room.revision]
    room.join_player(display_name="Player", connection_id="conn-player")
    revisions.append(room.revision)
    room.join_spectator(display_name="Viewer", connection_id="conn-viewer")
    revisions.append(room.revision)
    room.set_ready("conn-host", True)
    revisions.append(room.revision)
    room.set_ready("conn-host", True)
    assert room.revision == revisions[-1]
    room.public_snapshot()
    room.prune_expired()
    assert room.revision == revisions[-1]
    with pytest.raises(LobbyPermissionError):
        room.set_ready("conn-viewer")
    assert room.revision == revisions[-1]
    room.disconnect("conn-viewer")
    revisions.append(room.revision)

    assert revisions == sorted(set(revisions))
    assert all(new == old + 1 for old, new in zip(revisions, revisions[1:]))


def test_public_snapshot_never_leaks_credentials_hashes_or_internal_ids():
    room, host, _clock, _tokens = make_room(player_count=2)
    player = room.join_player(display_name="Player", connection_id="conn-secret")
    room.disconnect("conn-secret")

    snapshot = room.public_snapshot()
    encoded = json.dumps(snapshot, sort_keys=True)
    forbidden_key_parts = ("token", "hash", "connection", "member_id")

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                assert not any(part in key.casefold() for part in forbidden_key_parts)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(snapshot)
    assert host.reconnect_token not in encoded
    assert player.reconnect_token not in encoded
    assert "conn-secret" not in encoded
    assert player.member_id not in encoded


def test_invalid_token_material_names_and_connections_fail_safely():
    with pytest.raises(LobbyValidationError, match="32 bytes"):
        LobbyRoom.create(
            RoomSettings(),
            host_name="Host",
            connection_id="host",
            code_generator=lambda: "ABC234",
            token_bytes_generator=lambda _size: b"short",
        )

    room, _host, _clock, _tokens = make_room(player_count=2)
    with pytest.raises(LobbyValidationError):
        room.join_player(display_name="Bad\nName", connection_id="other")
    with pytest.raises(LobbyValidationError):
        room.join_player(display_name="Player", connection_id="")
    with pytest.raises(LobbyValidationError, match="already in use"):
        room.join_player(display_name="host", connection_id="other")
    with pytest.raises(LobbyAuthenticationError):
        room.reconnect(reconnect_token="too-short", connection_id="new")


def test_custom_reservation_and_spectator_reconnect_are_deterministic():
    room, _host, clock, _tokens = make_room(
        player_count=2,
        reservation_seconds=15,
    )
    spectator = room.join_spectator(display_name="Viewer", connection_id="viewer-old")
    assert room.disconnect("viewer-old") == clock.value + 15
    clock.advance(14)

    restored = room.reconnect(
        reconnect_token=spectator.reconnect_token,
        connection_id="viewer-new",
    )

    assert restored.member_id == spectator.member_id
    assert restored.role is MemberRole.SPECTATOR
    assert restored.seat is None
