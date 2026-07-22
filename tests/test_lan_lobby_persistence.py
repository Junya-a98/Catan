import copy
import json

import pytest

from game.lan_lobby import (
    LOBBY_AUTHORITY_FORMAT,
    LOBBY_AUTHORITY_VERSION,
    MAX_LOBBY_MEMBERS,
    LobbyAuthenticationError,
    LobbyRoom,
    LobbyValidationError,
    RoomPhase,
    RoomSettings,
)
from game.variant import VariantConfig


class FakeClock:
    def __init__(self, value: float) -> None:
        self.value = float(value)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class DeterministicTokenBytes:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, size: int) -> bytes:
        self.calls += 1
        return bytes([self.calls]) * size


def _make_room(
    *,
    clock: FakeClock,
    protected: bool = False,
) -> tuple[LobbyRoom, object, object]:
    room, host = LobbyRoom.create(
        RoomSettings(
            player_count=3,
            victory_target=12,
            board_seed=86712347,
            variant=VariantConfig.frontier(),
        ),
        host_name="Host",
        connection_id="connection-host-secret",
        clock=clock,
        code_generator=lambda: "ABC234",
        token_bytes_generator=DeterministicTokenBytes(),
        passphrase="correct horse island battery" if protected else None,
    )
    guest = room.join_player(
        display_name="Guest",
        connection_id="connection-guest-secret",
        passphrase="correct horse island battery" if protected else None,
    )
    return room, host, guest


def _member(document: dict, member_id: str) -> dict:
    return next(item for item in document["members"] if item["member_id"] == member_id)


def _legacy_v1_authority_fixture() -> dict:
    """An exact pre-rotation authority document, not derived from v2 export."""

    return {
        "format": "catan-lobby-authority",
        "version": 1,
        "saved_at_ms": 1_000_000,
        "room_code": "ABC234",
        "settings": {
            "player_count": 2,
            "victory_target": 10,
            "board_mode": "constrained",
            "board_seed": 73,
            "ai_player_count": 0,
            "ai_personality_mode": "standard",
            "custom_map": None,
            "house_rules": {
                "bank_trade_3_to_1": False,
                "skip_discard_on_seven": False,
                "disabled_development_cards": [],
            },
            "variant": {"version": 1, "kind": "standard", "options": {}},
        },
        "phase": "waiting",
        "revision": 1,
        "reservation_seconds": 120.0,
        "member_sequence": 1,
        "host_member_id": "member-1",
        "access_policy": None,
        "members": [
            {
                "member_id": "member-1",
                "display_name": "Host",
                "role": "host",
                "seat": 1,
                "reconnect_token_hash": (
                    "56d5fa7333f6d747db42c239407e5da4c32f4c79f35d092b134fd35a402d9c5c"
                ),
                "was_connected": True,
                "ready": False,
                "joined_order": 1,
                "reservation_expires_at_ms": None,
            }
        ],
    }


def test_exact_v1_authority_fixture_imports_and_migrates_to_canonical_v2():
    legacy = _legacy_v1_authority_fixture()
    original = copy.deepcopy(legacy)
    restored = LobbyRoom.from_authority_document(
        legacy,
        wall_clock_ms=1_010_000,
        clock=FakeClock(500.0),
        restart_grace_seconds=45.0,
    )

    migrated = restored.to_authority_document(wall_clock_ms=1_010_000)
    expected = copy.deepcopy(legacy)
    expected["version"] = LOBBY_AUTHORITY_VERSION
    expected["saved_at_ms"] = 1_010_000
    expected_member = expected["members"][0]
    expected_member["previous_reconnect_token_hash"] = None
    expected_member["previous_reconnect_token_expires_at_ms"] = None
    expected_member["was_connected"] = False
    expected_member["reservation_expires_at_ms"] = 1_055_000

    assert legacy == original
    assert migrated == expected


def test_v2_previous_token_deadline_round_trips_without_restart_extension_and_expires():
    source_clock = FakeClock(100.0)
    room, _host, guest = _make_room(clock=source_clock)
    original_token = guest.reconnect_token
    room.disconnect("connection-guest-secret")
    rotated = room.reconnect_rotating(
        reconnect_token=original_token,
        connection_id="connection-guest-rotated",
        previous_token_grace_seconds=30.0,
    )

    authority = room.to_authority_document(wall_clock_ms=1_000_000)
    assert (
        _member(authority, guest.member_id)["previous_reconnect_token_expires_at_ms"]
        == 1_030_000
    )

    first = LobbyRoom.from_authority_document(
        authority,
        wall_clock_ms=1_010_000,
        clock=FakeClock(500.0),
        restart_grace_seconds=60.0,
    )
    first_round_trip = first.to_authority_document(wall_clock_ms=1_010_000)
    first_guest = _member(first_round_trip, guest.member_id)
    assert first_guest["previous_reconnect_token_expires_at_ms"] == 1_030_000
    assert first_guest["reservation_expires_at_ms"] == 1_070_000

    second = LobbyRoom.from_authority_document(
        first_round_trip,
        wall_clock_ms=1_020_000,
        clock=FakeClock(900.0),
        restart_grace_seconds=600.0,
    )
    second_round_trip = second.to_authority_document(wall_clock_ms=1_020_000)
    second_guest = _member(second_round_trip, guest.member_id)
    assert second_guest["previous_reconnect_token_expires_at_ms"] == 1_030_000
    assert second_guest["reservation_expires_at_ms"] == 1_070_000

    final_clock = FakeClock(1_300.0)
    final = LobbyRoom.from_authority_document(
        second_round_trip,
        wall_clock_ms=1_030_000,
        clock=final_clock,
        restart_grace_seconds=600.0,
    )
    final_authority = final.to_authority_document(wall_clock_ms=1_030_000)
    final_guest = _member(final_authority, guest.member_id)
    assert final_guest["previous_reconnect_token_hash"] is None
    assert final_guest["previous_reconnect_token_expires_at_ms"] is None
    with pytest.raises(LobbyAuthenticationError, match="expired reconnect token"):
        final.reconnect_rotating(
            reconnect_token=original_token,
            connection_id="connection-expired-previous",
        )
    current = final.reconnect(
        reconnect_token=rotated.reconnect_token,
        connection_id="connection-current",
    )
    assert current.member_id == guest.member_id


def test_authority_export_is_exact_unredacted_and_excludes_live_connections():
    clock = FakeClock(100.0)
    room, host, guest = _make_room(clock=clock, protected=True)
    room.set_ready("connection-host-secret")
    room.set_ready("connection-guest-secret")
    room.disconnect("connection-guest-secret")

    authority = room.to_authority_document(wall_clock_ms=1_000_000)
    public = room.public_snapshot()

    assert authority["format"] == LOBBY_AUTHORITY_FORMAT
    assert authority["version"] == LOBBY_AUTHORITY_VERSION
    assert authority["settings"]["board_seed"] == 86712347
    assert public["settings"]["board_seed"] == 0
    assert authority["phase"] == RoomPhase.WAITING.value
    assert authority["access_policy"]["salt"]
    assert authority["access_policy"]["digest"]
    assert _member(authority, guest.member_id)["reservation_expires_at_ms"] == 1_120_000
    assert _member(authority, host.member_id)["reservation_expires_at_ms"] is None

    encoded_authority = json.dumps(authority, sort_keys=True)
    encoded_public = json.dumps(public, sort_keys=True)
    assert "connection-host-secret" not in encoded_authority
    assert "connection-guest-secret" not in encoded_authority
    assert "connection_id" not in encoded_authority
    assert host.reconnect_token not in encoded_authority
    assert guest.reconnect_token not in encoded_authority
    assert (
        _member(authority, host.member_id)["reconnect_token_hash"] in encoded_authority
    )
    assert host.member_id not in encoded_public
    assert guest.member_id not in encoded_public
    assert "reconnect_token" not in encoded_public
    assert authority["access_policy"]["salt"] not in encoded_public
    assert authority["access_policy"]["digest"] not in encoded_public


def test_import_restores_same_identity_and_seat_only_through_reconnect_token():
    source_clock = FakeClock(100.0)
    room, host, guest = _make_room(clock=source_clock, protected=True)
    room.set_ready("connection-guest-secret")
    room.disconnect("connection-guest-secret")
    source_clock.advance(10)
    authority = json.loads(
        json.dumps(room.to_authority_document(wall_clock_ms=1_000_000))
    )

    restored_clock = FakeClock(500.0)
    restored = LobbyRoom.from_authority_document(
        authority,
        wall_clock_ms=1_010_000,
        clock=restored_clock,
        token_bytes_generator=DeterministicTokenBytes(),
        restart_grace_seconds=45.0,
    )

    assert restored.room_code == room.room_code
    assert restored.revision == room.revision
    assert restored.phase is RoomPhase.WAITING
    assert restored.passphrase_required is True
    assert restored.public_snapshot()["can_start"] is False
    assert all(
        not item["connected"]
        for item in restored.public_snapshot()["members"]
        if not item.get("is_ai")
    )

    guest_grant = restored.reconnect(
        reconnect_token=guest.reconnect_token,
        connection_id="connection-guest-after-restart",
    )
    host_grant = restored.reconnect(
        reconnect_token=host.reconnect_token,
        connection_id="connection-host-after-restart",
    )
    assert guest_grant.member_id == guest.member_id
    assert guest_grant.seat == guest.seat == 2
    assert guest_grant.reconnect_token is None
    assert host_grant.member_id == host.member_id
    assert host_grant.seat == host.seat == 1
    assert (
        next(
            item
            for item in restored.public_snapshot()["members"]
            if item["display_name"] == "Guest"
        )["ready"]
        is True
    )


def test_disconnected_deadline_keeps_original_wall_time_across_restart():
    source_clock = FakeClock(100.0)
    room, _host, guest = _make_room(clock=source_clock)
    room.disconnect("connection-guest-secret")
    source_clock.advance(50)
    authority = room.to_authority_document(wall_clock_ms=1_000_000)
    assert _member(authority, guest.member_id)["reservation_expires_at_ms"] == 1_070_000

    restored_clock = FakeClock(500.0)
    restored = LobbyRoom.from_authority_document(
        authority,
        wall_clock_ms=1_020_000,
        clock=restored_clock,
        restart_grace_seconds=600.0,
    )
    round_trip = restored.to_authority_document(wall_clock_ms=1_020_000)

    # The configured 600-second restart grace applies only to formerly-live
    # members; Guest still owns the exact original absolute deadline.
    assert (
        _member(round_trip, guest.member_id)["reservation_expires_at_ms"] == 1_070_000
    )
    restored_clock.advance(50.001)
    with pytest.raises(LobbyAuthenticationError):
        restored.reconnect(
            reconnect_token=guest.reconnect_token,
            connection_id="too-late",
        )


def test_only_previously_connected_member_gets_one_restart_grace_period():
    source_clock = FakeClock(10.0)
    room, host, guest = _make_room(clock=source_clock)
    room.disconnect("connection-guest-secret")
    authority = room.to_authority_document(wall_clock_ms=2_000_000)

    first_clock = FakeClock(100.0)
    first_restore = LobbyRoom.from_authority_document(
        authority,
        wall_clock_ms=2_010_000,
        clock=first_clock,
        restart_grace_seconds=30.0,
    )
    persisted_again = first_restore.to_authority_document(wall_clock_ms=2_010_000)
    assert _member(persisted_again, host.member_id)["was_connected"] is False
    assert (
        _member(persisted_again, host.member_id)["reservation_expires_at_ms"]
        == 2_040_000
    )
    assert (
        _member(persisted_again, guest.member_id)["reservation_expires_at_ms"]
        == 2_120_000
    )

    second_clock = FakeClock(900.0)
    second_restore = LobbyRoom.from_authority_document(
        persisted_again,
        wall_clock_ms=2_020_000,
        clock=second_clock,
        restart_grace_seconds=300.0,
    )
    persisted_third = second_restore.to_authority_document(wall_clock_ms=2_020_000)
    assert (
        _member(persisted_third, host.member_id)["reservation_expires_at_ms"]
        == 2_040_000
    )
    assert (
        _member(persisted_third, guest.member_id)["reservation_expires_at_ms"]
        == 2_120_000
    )


def test_phase_revision_host_ready_and_access_policy_round_trip():
    clock = FakeClock(100.0)
    room, host, guest = _make_room(clock=clock)
    room.set_ready("connection-host-secret")
    room.set_ready("connection-guest-secret")
    spectator = room.join_spectator(
        display_name="Viewer",
        connection_id="connection-viewer-secret",
    )
    # A third human seat is still missing, so fill it before starting.
    third = room.join_player(
        display_name="Third",
        connection_id="connection-third-secret",
    )
    room.set_ready("connection-third-secret")
    room.start("connection-host-secret")
    room.disconnect("connection-guest-secret")
    expected_revision = room.revision

    restored = LobbyRoom.from_authority_document(
        room.to_authority_document(wall_clock_ms=3_000_000),
        wall_clock_ms=3_001_000,
        clock=FakeClock(900.0),
    )
    restored_authority = restored.to_authority_document(wall_clock_ms=3_001_000)

    assert restored.phase is RoomPhase.STARTED
    assert restored.revision == expected_revision
    assert restored_authority["host_member_id"] == host.member_id
    assert _member(restored_authority, guest.member_id)["ready"] is True
    assert _member(restored_authority, third.member_id)["ready"] is True
    assert _member(restored_authority, spectator.member_id)["seat"] is None
    assert restored.has_expired_player_reservation() is False


def test_maximum_lobby_members_persist_and_restore_at_exact_boundary():
    clock = FakeClock(100.0)
    room, _host = LobbyRoom.create(
        RoomSettings(player_count=2),
        host_name="Host",
        connection_id="connection-host",
        clock=clock,
        code_generator=lambda: "ABC234",
        token_bytes_generator=DeterministicTokenBytes(),
    )
    for index in range(MAX_LOBBY_MEMBERS - 1):
        room.join_spectator(
            display_name=f"Viewer {index}",
            connection_id=f"connection-viewer-{index}",
        )

    authority = room.to_authority_document(wall_clock_ms=1_000_000)
    assert len(authority["members"]) == MAX_LOBBY_MEMBERS

    restored = LobbyRoom.from_authority_document(
        authority,
        wall_clock_ms=1_001_000,
        clock=FakeClock(500.0),
    )
    restored_authority = restored.to_authority_document(wall_clock_ms=1_001_000)
    assert len(restored_authority["members"]) == MAX_LOBBY_MEMBERS
    assert restored.public_snapshot()["spectators"] == MAX_LOBBY_MEMBERS - 1

    oversized = copy.deepcopy(authority)
    oversized["members"].append(copy.deepcopy(oversized["members"][-1]))
    with pytest.raises(LobbyValidationError, match="members are invalid"):
        LobbyRoom.from_authority_document(
            oversized,
            wall_clock_ms=1_001_000,
            clock=FakeClock(500.0),
        )


def _valid_two_member_document() -> dict:
    clock = FakeClock(100.0)
    room, _host, _guest = _make_room(clock=clock)
    room.disconnect("connection-guest-secret")
    return room.to_authority_document(wall_clock_ms=1_000_000)


def _mutate(document: dict, case: str) -> None:
    members = document["members"]
    if case == "missing root key":
        document.pop("phase")
    elif case == "extra root key":
        document["connection_id"] = "must-not-exist"
    elif case == "wrong format":
        document["format"] = "other"
    elif case == "bool version":
        document["version"] = True
    elif case == "future save clock":
        document["saved_at_ms"] = 1_000_001
    elif case == "partial settings":
        document["settings"].pop("variant")
    elif case == "extra member key":
        members[0]["connection_id"] = "must-not-exist"
    elif case == "live member deadline":
        members[0]["reservation_expires_at_ms"] = 1_120_000
    elif case == "disconnected member without deadline":
        members[1]["reservation_expires_at_ms"] = None
    elif case == "duplicate token hash":
        members[1]["reconnect_token_hash"] = members[0]["reconnect_token_hash"]
    elif case == "previous hash without deadline":
        members[0]["previous_reconnect_token_hash"] = "a" * 64
    elif case == "previous deadline without hash":
        members[0]["previous_reconnect_token_expires_at_ms"] = 1_010_000
    elif case == "malformed previous hash":
        members[0]["previous_reconnect_token_hash"] = "not-a-token-hash"
        members[0]["previous_reconnect_token_expires_at_ms"] = 1_010_000
    elif case == "previous equals current":
        members[0]["previous_reconnect_token_hash"] = members[0]["reconnect_token_hash"]
        members[0]["previous_reconnect_token_expires_at_ms"] = 1_010_000
    elif case == "previous collides with another current":
        members[0]["previous_reconnect_token_hash"] = members[1]["reconnect_token_hash"]
        members[0]["previous_reconnect_token_expires_at_ms"] = 1_010_000
    elif case == "duplicate seat":
        members[1]["seat"] = members[0]["seat"]
    elif case == "duplicate display name":
        members[1]["display_name"] = members[0]["display_name"].lower()
    elif case == "noncanonical member id":
        members[1]["member_id"] = "member-99"
    elif case == "host mismatch":
        document["host_member_id"] = members[1]["member_id"]
    elif case == "invalid access authority":
        document["access_policy"] = {"digest": "0" * 64}
    elif case == "bool revision":
        document["revision"] = False
    else:  # pragma: no cover - keeps the table exhaustive.
        raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    [
        "missing root key",
        "extra root key",
        "wrong format",
        "bool version",
        "future save clock",
        "partial settings",
        "extra member key",
        "live member deadline",
        "disconnected member without deadline",
        "duplicate token hash",
        "previous hash without deadline",
        "previous deadline without hash",
        "malformed previous hash",
        "previous equals current",
        "previous collides with another current",
        "duplicate seat",
        "duplicate display name",
        "noncanonical member id",
        "host mismatch",
        "invalid access authority",
        "bool revision",
    ],
)
def test_authority_import_rejects_malformed_exact_schema(case):
    document = copy.deepcopy(_valid_two_member_document())
    _mutate(document, case)

    with pytest.raises(LobbyValidationError):
        LobbyRoom.from_authority_document(
            document,
            wall_clock_ms=1_000_000,
            clock=FakeClock(200.0),
        )


@pytest.mark.parametrize("value", [True, -1, 1.5, "1000", 1 << 53])
def test_authority_wall_clock_requires_non_negative_json_safe_milliseconds(value):
    room, _host, _guest = _make_room(clock=FakeClock(100.0))

    with pytest.raises(LobbyValidationError):
        room.to_authority_document(wall_clock_ms=value)
