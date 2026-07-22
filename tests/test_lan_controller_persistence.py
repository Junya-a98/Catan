from copy import deepcopy

import pytest

from game.lan_controller import (
    ControllerPersistenceError,
    LanServerController,
)
from game.network_protocol import NETWORK_PROTOCOL_VERSION, build_game_command
from game.persistence import serialize_game
from game.server_state import SQLiteRoomAuthorityStore


WALL_CLOCK_MS = 1_800_000_000_000
LOBBY_CLOCK = 1_000.0


def message(message_type, **payload):
    return {
        "type": message_type,
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        **payload,
    }


def settings(*, ai_players=0):
    return {
        "player_count": 2,
        "victory_target": 10,
        "board_mode": "constrained",
        "board_seed": 86712347,
        "ai_player_count": ai_players,
    }


def controller(store, *, wall_clock_ms=WALL_CLOCK_MS, **kwargs):
    return LanServerController(
        state_store=store,
        wall_clock_ms=lambda: wall_clock_ms,
        lobby_clock=lambda: LOBBY_CLOCK,
        **kwargs,
    )


def create_room(instance, *, ai_players=0):
    outbound = instance.handle(
        "host",
        message(
            "create_room",
            display_name="Host",
            settings=settings(ai_players=ai_players),
        ),
    )
    welcome = next(
        item.message for item in outbound if item.message["type"] == "session_welcome"
    )
    return welcome["room_code"], welcome["reconnect_token"]


def join_ready_and_start(instance, room_code):
    joined = instance.handle(
        "guest",
        message(
            "join_room",
            room_code=room_code,
            display_name="Guest",
            role="player",
        ),
    )
    guest_welcome = next(
        item.message for item in joined if item.message["type"] == "session_welcome"
    )
    instance.handle("host", message("set_ready", ready=True))
    instance.handle("guest", message("set_ready", ready=True))
    started = instance.handle("host", message("start_game"))
    snapshot = next(
        item.message for item in started if item.message["type"] == "state_snapshot"
    )
    return guest_welcome["reconnect_token"], snapshot


class FailingUpdateStore:
    def __init__(self, inner):
        self.inner = inner
        self.fail_updates = False

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def update_room(self, *args, **kwargs):
        if self.fail_updates:
            raise RuntimeError("simulated durable commit failure")
        return self.inner.update_room(*args, **kwargs)


def test_restore_normalization_grants_restart_grace_only_once(tmp_path):
    database = tmp_path / "authority.sqlite3"
    key = tmp_path / "authority.key"
    store_a = SQLiteRoomAuthorityStore(database, key_path=key)
    first = controller(store_a)
    room_code, _token = create_room(first)
    initial = store_a.get_room_by_code(room_code)
    assert initial.authority["lobby"]["members"][0]["was_connected"] is True
    store_a.close()

    store_b = SQLiteRoomAuthorityStore(database, key_path=key)
    controller(store_b, wall_clock_ms=WALL_CLOCK_MS + 1_000)
    normalized = store_b.get_room_by_code(room_code)
    member = normalized.authority["lobby"]["members"][0]
    first_deadline = member["reservation_expires_at_ms"]
    assert member["was_connected"] is False
    assert first_deadline == WALL_CLOCK_MS + 1_000 + 120_000
    store_b.close()

    store_c = SQLiteRoomAuthorityStore(database, key_path=key)
    controller(store_c, wall_clock_ms=WALL_CLOCK_MS + 2_000)
    normalized_again = store_c.get_room_by_code(room_code)
    member_again = normalized_again.authority["lobby"]["members"][0]
    assert member_again["was_connected"] is False
    assert member_again["reservation_expires_at_ms"] == first_deadline
    store_c.close()


def test_restore_extends_row_ttl_to_cover_promised_restart_grace(tmp_path):
    database = tmp_path / "authority.sqlite3"
    key = tmp_path / "authority.key"
    store_a = SQLiteRoomAuthorityStore(database, key_path=key)
    first = controller(store_a, waiting_room_ttl_ms=500)
    room_code, _token = create_room(first)
    store_a.close()

    store_b = SQLiteRoomAuthorityStore(database, key_path=key)
    controller(
        store_b,
        wall_clock_ms=WALL_CLOCK_MS + 100,
        waiting_room_ttl_ms=500,
    )
    normalized = store_b.get_room_by_code(room_code)
    member_deadline = normalized.authority["lobby"]["members"][0][
        "reservation_expires_at_ms"
    ]
    assert normalized.expires_at_ms >= member_deadline
    store_b.close()

    store_c = SQLiteRoomAuthorityStore(database, key_path=key)
    restored = controller(
        store_c,
        wall_clock_ms=WALL_CLOCK_MS + 501,
        waiting_room_ttl_ms=500,
    )
    assert restored.room_codes == (room_code,)
    store_c.close()


def test_expired_room_is_deleted_before_controller_restore(tmp_path):
    database = tmp_path / "authority.sqlite3"
    key = tmp_path / "authority.key"
    store_a = SQLiteRoomAuthorityStore(database, key_path=key)
    first = controller(store_a, waiting_room_ttl_ms=1_000)
    room_code, _token = create_room(first)
    assert store_a.get_room_by_code(room_code) is not None
    store_a.close()

    store_b = SQLiteRoomAuthorityStore(database, key_path=key)
    restored = controller(
        store_b,
        wall_clock_ms=WALL_CLOCK_MS + 1_000,
        waiting_room_ttl_ms=1_000,
    )
    assert restored.room_codes == ()
    assert store_b.list_rooms() == ()
    store_b.close()


def test_semantically_invalid_but_authenticated_authority_fails_startup(tmp_path):
    database = tmp_path / "authority.sqlite3"
    key = tmp_path / "authority.key"
    store = SQLiteRoomAuthorityStore(database, key_path=key)
    first = controller(store)
    room_code, _token = create_room(first)
    record = store.get_room_by_code(room_code)
    corrupted = deepcopy(record.authority)
    corrupted["lobby"]["phase"] = "started"
    store.update_room(
        record.room_id,
        expected_generation=record.generation,
        authority=corrupted,
        updated_at_ms=WALL_CLOCK_MS + 1,
        expires_at_ms=record.expires_at_ms,
    )

    with pytest.raises(ControllerPersistenceError):
        controller(store, wall_clock_ms=WALL_CLOCK_MS + 2)
    store.close()


def test_started_game_contract_must_match_authenticated_lobby_settings(tmp_path):
    database = tmp_path / "authority.sqlite3"
    key = tmp_path / "authority.key"
    store = SQLiteRoomAuthorityStore(database, key_path=key)
    first = controller(store)
    room_code, _token = create_room(first)
    join_ready_and_start(first, room_code)
    record = store.get_room_by_code(room_code)
    corrupted = deepcopy(record.authority)
    corrupted["lobby"]["settings"]["victory_target"] = 11
    store.update_room(
        record.room_id,
        expected_generation=record.generation,
        authority=corrupted,
        updated_at_ms=WALL_CLOCK_MS + 1,
        expires_at_ms=record.expires_at_ms,
    )

    with pytest.raises(ControllerPersistenceError):
        controller(store, wall_clock_ms=WALL_CLOCK_MS + 2)
    store.close()


def test_ready_commit_failure_rolls_back_and_latches_controller(tmp_path):
    inner = SQLiteRoomAuthorityStore(tmp_path / "authority.sqlite3")
    fault = FailingUpdateStore(inner)
    instance = controller(fault)
    room_code, _token = create_room(instance)
    context = instance._rooms[room_code]
    revision_before = context.lobby.revision
    generation_before = inner.get_room_by_code(room_code).generation

    fault.fail_updates = True
    failed = instance.handle("host", message("set_ready", ready=True))
    error = failed[0].message
    assert error["type"] == "request_error"
    assert error["code"] == "persistence_unavailable"
    assert context.lobby.revision == revision_before
    assert context.lobby.public_snapshot()["members"][0]["ready"] is False
    assert inner.get_room_by_code(room_code).generation == generation_before
    assert instance.room_codes == ()

    fenced = instance.handle("host", message("set_ready", ready=True))[0].message
    assert fenced["code"] == "persistence_unavailable"
    inner.close()


def test_rotating_reconnect_commit_failure_preserves_original_credential(tmp_path):
    database = tmp_path / "authority.sqlite3"
    key = tmp_path / "authority.key"
    inner = SQLiteRoomAuthorityStore(database, key_path=key)
    fault = FailingUpdateStore(inner)
    first = controller(fault)
    room_code, original_token = create_room(first)
    first.disconnect("host")
    context = first._rooms[room_code]
    authority_before = deepcopy(inner.get_room_by_code(room_code))

    fault.fail_updates = True
    failed = first.handle(
        "host-web-resume",
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=original_token,
        ),
        rotate_reconnect_token=True,
    )

    assert failed[0].message["type"] == "request_error"
    assert failed[0].message["code"] == "persistence_unavailable"
    assert all(item.message["type"] != "session_welcome" for item in failed)
    assert all("reconnect_token" not in item.message for item in failed)
    durable_after = inner.get_room_by_code(room_code)
    assert durable_after.generation == authority_before.generation
    assert durable_after.authority == authority_before.authority
    assert (
        context.lobby.to_authority_document(wall_clock_ms=WALL_CLOCK_MS)
        == authority_before.authority["lobby"]
    )
    assert first.room_codes == ()
    inner.close()

    reopened = SQLiteRoomAuthorityStore(database, key_path=key)
    second = controller(reopened, wall_clock_ms=WALL_CLOCK_MS + 1_000)
    recovered = second.handle(
        "host-after-restart",
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=original_token,
        ),
    )
    welcome = next(
        item.message
        for item in recovered
        if item.message["type"] == "session_welcome"
    )
    assert welcome["seat_index"] == 0
    assert welcome["reconnect_token"] is None
    reopened.close()


def test_reconnect_confirmation_commit_failure_preserves_both_credentials(
    tmp_path,
):
    database = tmp_path / "authority.sqlite3"
    key = tmp_path / "authority.key"
    inner = SQLiteRoomAuthorityStore(database, key_path=key)
    fault = FailingUpdateStore(inner)
    first = controller(fault)
    room_code, previous_token = create_room(first)
    first.disconnect("host")
    rotated = first.handle(
        "host-web-resume",
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=previous_token,
        ),
        rotate_reconnect_token=True,
    )
    rotated_welcome = next(
        item.message
        for item in rotated
        if item.message["type"] == "session_welcome"
    )
    current_token = rotated_welcome["reconnect_token"]
    assert current_token not in {None, previous_token}
    context = first._rooms[room_code]
    authority_before = deepcopy(inner.get_room_by_code(room_code))
    persisted_member = authority_before.authority["lobby"]["members"][0]
    assert persisted_member["previous_reconnect_token_hash"] is not None
    assert persisted_member["previous_reconnect_token_expires_at_ms"] is not None

    fault.fail_updates = True
    failed = first.confirm_reconnect_token(
        "host-web-resume",
        room_code,
        current_token,
    )

    assert failed[0].message["type"] == "request_error"
    assert failed[0].message["code"] == "persistence_unavailable"
    assert all(item.message["type"] != "resume_confirmed" for item in failed)
    durable_after = inner.get_room_by_code(room_code)
    assert durable_after.generation == authority_before.generation
    assert durable_after.authority == authority_before.authority
    assert (
        context.lobby.to_authority_document(wall_clock_ms=WALL_CLOCK_MS)
        == authority_before.authority["lobby"]
    )
    assert first.room_codes == ()
    inner.close()

    reopened = SQLiteRoomAuthorityStore(database, key_path=key)
    second = controller(reopened, wall_clock_ms=WALL_CLOCK_MS + 1_000)
    current_reconnect = second.handle(
        "host-current",
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=current_token,
        ),
    )
    current_welcome = next(
        item.message
        for item in current_reconnect
        if item.message["type"] == "session_welcome"
    )
    assert current_welcome["seat_index"] == 0
    assert current_welcome["reconnect_token"] is None
    second.disconnect("host-current")

    previous_reconnect = second.handle(
        "host-previous",
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=previous_token,
        ),
        rotate_reconnect_token=True,
    )
    previous_welcome = next(
        item.message
        for item in previous_reconnect
        if item.message["type"] == "session_welcome"
    )
    assert previous_welcome["seat_index"] == 0
    assert previous_welcome["reconnect_token"] not in {
        None,
        previous_token,
        current_token,
    }
    reopened.close()


def test_failed_command_is_non_consuming_and_recovers_after_restart(tmp_path):
    database = tmp_path / "authority.sqlite3"
    key = tmp_path / "authority.key"
    inner = SQLiteRoomAuthorityStore(database, key_path=key)
    fault = FailingUpdateStore(inner)
    first = controller(fault)
    room_code, host_token = create_room(first)
    _guest_token, initial = join_ready_and_start(first, room_code)
    command = build_game_command(
        sequence=0,
        expected_revision=initial["revision"],
        command="roll_dice",
    )
    context = first._rooms[room_code]
    game_before = serialize_game(context.game)

    fault.fail_updates = True
    failed = first.handle("host", command)[0].message
    assert failed["type"] == "request_error"
    assert failed["code"] == "persistence_unavailable"
    assert context.game_revision == 0
    assert context.command_states == {}
    assert serialize_game(context.game) == game_before
    assert first.room_codes == ()
    inner.close()

    reopened = SQLiteRoomAuthorityStore(database, key_path=key)
    second = controller(reopened, wall_clock_ms=WALL_CLOCK_MS + 1_000)
    reconnected = second.handle(
        "host-after-restart",
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=host_token,
        ),
    )
    welcome = next(
        item.message
        for item in reconnected
        if item.message["type"] == "session_welcome"
    )
    assert welcome["next_sequence"] == 0
    assert second.snapshot_for_connection("host-after-restart")["revision"] == 0

    accepted = second.handle("host-after-restart", command)[0].message
    assert accepted["type"] == "game_command_result"
    assert accepted["accepted"] is True
    assert accepted["revision"] == 1
    reopened.close()


def test_persisted_duplicate_command_is_returned_without_second_mutation(tmp_path):
    database = tmp_path / "authority.sqlite3"
    key = tmp_path / "authority.key"
    store_a = SQLiteRoomAuthorityStore(database, key_path=key)
    first = controller(store_a)
    room_code, host_token = create_room(first)
    _guest_token, initial = join_ready_and_start(first, room_code)
    command = build_game_command(
        sequence=0,
        expected_revision=initial["revision"],
        command="roll_dice",
    )
    accepted = first.handle("host", command)[0].message
    assert accepted["accepted"] is True
    state_after = deepcopy(first.snapshot_for_connection("host")["state"])
    store_a.close()

    store_b = SQLiteRoomAuthorityStore(database, key_path=key)
    second = controller(store_b, wall_clock_ms=WALL_CLOCK_MS + 1_000)
    second.handle(
        "host-after-restart",
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=host_token,
        ),
    )
    generation_before = store_b.get_room_by_code(room_code).generation
    duplicate = second.handle("host-after-restart", command)[0].message

    assert duplicate == accepted
    assert second.snapshot_for_connection("host-after-restart")["state"] == state_after
    assert store_b.get_room_by_code(room_code).generation == generation_before
    store_b.close()


def test_ai_commit_failure_rolls_back_and_fences_live_rooms(tmp_path):
    database = tmp_path / "authority.sqlite3"
    key = tmp_path / "authority.key"
    inner = SQLiteRoomAuthorityStore(database, key_path=key)
    fault = FailingUpdateStore(inner)

    def ai_stepper(game):
        game.ai_paused = not game.ai_paused
        return True

    instance = controller(fault, ai_stepper=ai_stepper)
    room_code, _host_token = create_room(instance, ai_players=1)
    instance.handle("host", message("set_ready", ready=True))
    started = instance.handle("host", message("start_game"))
    assert any(item.message["type"] == "state_snapshot" for item in started)
    context = instance._rooms[room_code]
    paused_before = context.game.ai_paused
    random_before = context.random_state
    context.game.is_ai_input_locked = lambda: True

    fault.fail_updates = True
    closed = instance.tick()

    assert closed
    assert {item.message["type"] for item in closed} == {"room_closed"}
    assert {item.message["code"] for item in closed} == {
        "persistence_unavailable"
    }
    assert instance.room_codes == ()
    assert context.game_revision == 0
    assert context.game.ai_paused is paused_before
    assert context.random_state == random_before
    inner.close()

    reopened = SQLiteRoomAuthorityStore(database, key_path=key)
    restored = controller(reopened, wall_clock_ms=WALL_CLOCK_MS + 1_000)
    restored_context = restored._rooms[room_code]
    assert restored_context.game_revision == 0
    assert restored_context.game.ai_paused is paused_before
    assert restored_context.random_state == random_before
    reopened.close()
