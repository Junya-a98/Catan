import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
import uuid

import pytest

from game.server_state import (
    RoomAuthorityMetadata,
    SERVER_STATE_KEY_BYTES,
    SQLiteRoomAuthorityStore,
    ServerStateClosedError,
    ServerStateConflictError,
    ServerStateIntegrityError,
    ServerStateValidationError,
)


ROOM_ID = "26e318a8-17a0-4c27-8bfd-2940e5e7d96b"


def authority_document(*, turn=1):
    return {
        "format": "test-room-authority",
        "version": 1,
        "lobby": {
            "phase": "waiting",
            "members": [
                {
                    "member_id": "member-1",
                    "reconnect_token_hash": "a" * 64,
                }
            ],
        },
        "match": None,
        "turn": turn,
    }


def new_store(tmp_path, **kwargs):
    state_dir = tmp_path / "private-state"
    database = state_dir / "authority.sqlite3"
    return SQLiteRoomAuthorityStore(database, **kwargs)


def test_store_creates_private_wal_database_and_separate_key(tmp_path):
    store = new_store(tmp_path)
    try:
        database = store.database_path
        key = store.key_path
        assert database != key
        assert key.read_bytes() and len(key.read_bytes()) == SERVER_STATE_KEY_BYTES
        assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(database.stat().st_mode) == 0o600
        assert stat.S_IMODE(key.stat().st_mode) == 0o600
        assert store._connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert store._connection.execute("PRAGMA synchronous").fetchone()[0] == 2

        store.create_room(
            room_id=ROOM_ID,
            room_code="ABC234",
            authority=authority_document(),
            updated_at_ms=1_000,
            expires_at_ms=2_000,
        )
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{database}{suffix}")
            if sidecar.exists():
                assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
    finally:
        store.close()


def test_create_read_list_update_and_delete_round_trip(tmp_path):
    store = new_store(tmp_path)
    source = authority_document(turn=1)
    created = store.create_room(
        room_id=ROOM_ID,
        room_code="ABC234",
        authority=source,
        updated_at_ms=1_000,
        expires_at_ms=10_000,
    )

    assert created.room_id == ROOM_ID
    assert created.room_code == "ABC234"
    assert created.generation == 1
    assert created.authority == source
    assert "authority" not in repr(created)
    assert store.list_rooms() == (
        RoomAuthorityMetadata(
            room_id=ROOM_ID,
            room_code="ABC234",
            generation=1,
            updated_at_ms=1_000,
            expires_at_ms=10_000,
        ),
    )

    # Neither caller-owned input nor returned documents alias persisted state.
    source["turn"] = 99
    created.authority["turn"] = 98
    assert store.get_room(ROOM_ID).authority["turn"] == 1
    by_code = store.get_room_by_code("ABC234")
    assert by_code is not None and by_code.room_id == ROOM_ID

    updated = store.update_room(
        ROOM_ID,
        expected_generation=1,
        authority=authority_document(turn=2),
        updated_at_ms=2_000,
        expires_at_ms=12_000,
    )
    assert updated.generation == 2
    assert updated.authority["turn"] == 2
    assert store.get_room(ROOM_ID).generation == 2

    assert store.delete_room(ROOM_ID, expected_generation=2)
    assert not store.delete_room(ROOM_ID)
    assert store.get_room(ROOM_ID) is None
    assert store.get_room_by_code("ABC234") is None
    store.close()


def test_generated_room_id_is_canonical_uuid4(tmp_path):
    with new_store(tmp_path) as store:
        created = store.create_room(
            room_code="ABC234",
            authority=authority_document(),
            updated_at_ms=1,
            expires_at_ms=2,
        )
        parsed = uuid.UUID(created.room_id)
        assert str(parsed) == created.room_id
        assert parsed.version == 4


def test_stale_generation_cannot_change_or_delete_room(tmp_path):
    with new_store(tmp_path) as store:
        store.create_room(
            room_id=ROOM_ID,
            room_code="ABC234",
            authority=authority_document(turn=1),
            updated_at_ms=1_000,
            expires_at_ms=10_000,
        )

        with pytest.raises(ServerStateConflictError, match="generation"):
            store.update_room(
                ROOM_ID,
                expected_generation=2,
                authority=authority_document(turn=2),
                updated_at_ms=2_000,
                expires_at_ms=12_000,
            )
        with pytest.raises(ServerStateConflictError, match="generation"):
            store.delete_room(ROOM_ID, expected_generation=2)

        unchanged = store.get_room(ROOM_ID)
        assert unchanged is not None
        assert unchanged.generation == 1
        assert unchanged.authority["turn"] == 1


def test_process_lock_serializes_two_compare_and_swap_writers(tmp_path):
    store = new_store(tmp_path)
    store.create_room(
        room_id=ROOM_ID,
        room_code="ABC234",
        authority=authority_document(turn=1),
        updated_at_ms=1_000,
        expires_at_ms=10_000,
    )
    barrier = threading.Barrier(3)
    outcomes = []

    def writer(turn):
        barrier.wait()
        try:
            record = store.update_room(
                ROOM_ID,
                expected_generation=1,
                authority=authority_document(turn=turn),
                updated_at_ms=2_000 + turn,
                expires_at_ms=20_000 + turn,
            )
        except ServerStateConflictError:
            outcomes.append("conflict")
        else:
            outcomes.append(record.authority["turn"])

    threads = [
        threading.Thread(target=writer, args=(2,)),
        threading.Thread(target=writer, args=(3,)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert len(outcomes) == 2
    assert outcomes.count("conflict") == 1
    winner = next(value for value in outcomes if value != "conflict")
    final = store.get_room(ROOM_ID)
    assert final is not None
    assert final.generation == 2
    assert final.authority["turn"] == winner
    store.close()


def test_room_identity_and_code_are_unique(tmp_path):
    with new_store(tmp_path) as store:
        store.create_room(
            room_id=ROOM_ID,
            room_code="ABC234",
            authority=authority_document(),
            updated_at_ms=1,
            expires_at_ms=10,
        )
        with pytest.raises(ServerStateConflictError):
            store.create_room(
                room_id=ROOM_ID,
                room_code="DEF567",
                authority=authority_document(),
                updated_at_ms=1,
                expires_at_ms=10,
            )
        with pytest.raises(ServerStateConflictError):
            store.create_room(
                room_id="e3e8f9d7-e677-4d61-ab66-7c45782dd97c",
                room_code="ABC234",
                authority=authority_document(),
                updated_at_ms=1,
                expires_at_ms=10,
            )
        assert len(store.list_rooms()) == 1


def test_delete_expired_is_atomic_sorted_and_inclusive(tmp_path):
    with new_store(tmp_path) as store:
        rooms = (
            ("47a00321-12b9-4c3f-8dab-8e7a43470d86", "ZZZ999", 200),
            ("a76e27cc-312c-44af-99af-83e26c340d48", "ABC234", 250),
            ("920293be-b6d9-4db4-a5cc-d40c81c17ad0", "DEF567", 251),
        )
        for room_id, code, expiry in rooms:
            store.create_room(
                room_id=room_id,
                room_code=code,
                authority=authority_document(),
                updated_at_ms=100,
                expires_at_ms=expiry,
            )

        assert store.delete_expired(250) == (rooms[1][0], rooms[0][0])
        assert [item.room_code for item in store.list_rooms()] == ["DEF567"]
        assert store.delete_expired(250) == ()
        assert store.delete_expired(251) == (rooms[2][0],)


@pytest.mark.parametrize(
    "authority",
    [
        [],
        {"tuple": (1, 2)},
        {"set": {1, 2}},
        {"nan": float("nan")},
        {"infinity": float("inf")},
        {"huge": 1 << 80},
        {1: "non-string-key"},
    ],
)
def test_authority_input_must_be_a_strict_bounded_json_object(
    tmp_path,
    authority,
):
    with new_store(tmp_path) as store:
        with pytest.raises(ServerStateValidationError, match="authority"):
            store.create_room(
                room_code="ABC234",
                authority=authority,
                updated_at_ms=1,
                expires_at_ms=2,
            )


def test_authority_cycles_and_excessive_depth_are_rejected(tmp_path):
    with new_store(tmp_path) as store:
        cyclic = {}
        cyclic["self"] = cyclic
        with pytest.raises(ServerStateValidationError, match="cycle"):
            store.create_room(
                room_code="ABC234",
                authority=cyclic,
                updated_at_ms=1,
                expires_at_ms=2,
            )

        root = {}
        cursor = root
        for _ in range(70):
            child = {}
            cursor["child"] = child
            cursor = child
        with pytest.raises(ServerStateValidationError, match="nesting"):
            store.create_room(
                room_code="DEF567",
                authority=root,
                updated_at_ms=1,
                expires_at_ms=2,
            )


@pytest.mark.parametrize(
    ("room_id", "room_code", "updated", "expires"),
    [
        ("not-a-uuid", "ABC234", 1, 2),
        (ROOM_ID.upper(), "ABC234", 1, 2),
        (ROOM_ID, "abc234", 1, 2),
        (ROOM_ID, "ABC01I", 1, 2),
        (ROOM_ID, "ABC234", True, 2),
        (ROOM_ID, "ABC234", 2, 2),
        (ROOM_ID, "ABC234", 3, 2),
    ],
)
def test_room_metadata_is_strict(tmp_path, room_id, room_code, updated, expires):
    with new_store(tmp_path) as store:
        with pytest.raises(ServerStateValidationError):
            store.create_room(
                room_id=room_id,
                room_code=room_code,
                authority=authority_document(),
                updated_at_ms=updated,
                expires_at_ms=expires,
            )


def test_wrong_key_fails_closed_even_before_any_room_is_read(tmp_path):
    store = new_store(tmp_path)
    store.create_room(
        room_id=ROOM_ID,
        room_code="ABC234",
        authority=authority_document(),
        updated_at_ms=1,
        expires_at_ms=2,
    )
    database, key = store.database_path, store.key_path
    store.close()
    key.write_bytes(os.urandom(SERVER_STATE_KEY_BYTES))

    with pytest.raises(ServerStateIntegrityError, match="key or integrity"):
        SQLiteRoomAuthorityStore(database, key_path=key)


def test_missing_or_truncated_key_for_existing_database_fails_closed(tmp_path):
    store = new_store(tmp_path)
    database, key = store.database_path, store.key_path
    store.close()
    key.unlink()
    with pytest.raises(ServerStateIntegrityError, match="missing its key"):
        SQLiteRoomAuthorityStore(database, key_path=key)

    key.write_bytes(b"short")
    with pytest.raises(ServerStateIntegrityError, match="invalid length"):
        SQLiteRoomAuthorityStore(database, key_path=key)


@pytest.mark.parametrize("column", ["authority_json", "generation", "room_code"])
def test_tampered_room_payload_or_metadata_fails_closed_on_open(tmp_path, column):
    store = new_store(tmp_path)
    store.create_room(
        room_id=ROOM_ID,
        room_code="ABC234",
        authority=authority_document(),
        updated_at_ms=1,
        expires_at_ms=2,
    )
    database, key = store.database_path, store.key_path
    store.close()
    connection = sqlite3.connect(database)
    try:
        if column == "authority_json":
            connection.execute(
                "UPDATE room_authority SET authority_json = ?",
                (b'{"turn":999}',),
            )
        elif column == "generation":
            connection.execute(
                "UPDATE room_authority SET generation = 2"
            )
        else:
            connection.execute(
                "UPDATE room_authority SET room_code = 'DEF567'"
            )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ServerStateIntegrityError, match="key or integrity"):
        SQLiteRoomAuthorityStore(database, key_path=key)


def test_tampering_is_detected_by_reads_while_store_is_open(tmp_path):
    store = new_store(tmp_path)
    store.create_room(
        room_id=ROOM_ID,
        room_code="ABC234",
        authority=authority_document(),
        updated_at_ms=1,
        expires_at_ms=2,
    )
    connection = sqlite3.connect(store.database_path)
    try:
        connection.execute(
            "UPDATE room_authority SET authority_json = ?",
            (b"{}",),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ServerStateIntegrityError):
        store.list_rooms()
    store.close()


def test_payload_is_canonical_utf8_json_and_not_an_object_serialization(tmp_path):
    with new_store(tmp_path) as store:
        store.create_room(
            room_id=ROOM_ID,
            room_code="ABC234",
            authority={"日本語": "盤面", "a": [True, None, 1.25]},
            updated_at_ms=1,
            expires_at_ms=2,
        )
        row = store._connection.execute(
            "SELECT authority_json FROM room_authority"
        ).fetchone()
        payload = bytes(row[0])
        assert payload.startswith(b"{")
        assert json.loads(payload.decode("utf-8")) == {
            "a": [True, None, 1.25],
            "日本語": "盤面",
        }
        assert payload == json.dumps(
            json.loads(payload),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


def test_unknown_database_schema_fails_closed(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    os.chmod(state_dir, 0o700)
    database = state_dir / "authority.sqlite3"
    key = state_dir / "authority.key"
    key.write_bytes(os.urandom(SERVER_STATE_KEY_BYTES))
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ServerStateIntegrityError, match="schema"):
        SQLiteRoomAuthorityStore(database, key_path=key)


def test_unsafe_existing_parent_is_rejected_without_permission_mutation(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    os.chmod(shared, 0o755)
    database = shared / "authority.sqlite3"
    with pytest.raises(ServerStateValidationError, match="0700"):
        SQLiteRoomAuthorityStore(database)
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert not database.exists()
    assert not database.with_suffix(".sqlite3.key").exists()


def test_existing_file_permissions_are_hardened_and_symlinks_are_rejected(tmp_path):
    store = new_store(tmp_path)
    database, key = store.database_path, store.key_path
    store.close()
    os.chmod(database, 0o644)
    os.chmod(key, 0o644)
    reopened = SQLiteRoomAuthorityStore(database, key_path=key)
    try:
        assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(database.stat().st_mode) == 0o600
        assert stat.S_IMODE(key.stat().st_mode) == 0o600
    finally:
        reopened.close()

    symlink_dir = tmp_path / "symlink-state"
    symlink_dir.mkdir(mode=0o700)
    os.chmod(symlink_dir, 0o700)
    symlink_database = symlink_dir / "authority.sqlite3"
    symlink_key = symlink_dir / "authority.key"
    symlink_database.symlink_to(database)
    symlink_key.write_bytes(os.urandom(SERVER_STATE_KEY_BYTES))
    with pytest.raises(ServerStateValidationError, match="regular file"):
        SQLiteRoomAuthorityStore(symlink_database, key_path=symlink_key)


@pytest.mark.parametrize(
    "schema_sql",
    [
        "CREATE VIEW room_codes AS SELECT room_code FROM room_authority",
        "CREATE INDEX room_expiry ON room_authority(expires_at_ms)",
        "ALTER TABLE room_authority ADD COLUMN unexpected TEXT",
        (
            "CREATE TRIGGER room_touch AFTER UPDATE ON room_authority "
            "BEGIN SELECT 1; END"
        ),
    ],
)
def test_extra_schema_objects_or_columns_fail_closed(tmp_path, schema_sql):
    store = new_store(tmp_path)
    database, key = store.database_path, store.key_path
    store.close()
    connection = sqlite3.connect(database)
    try:
        connection.execute(schema_sql)
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(
        ServerStateIntegrityError,
        match="schema|columns|definition",
    ):
        SQLiteRoomAuthorityStore(database, key_path=key)


def test_closed_store_rejects_operations(tmp_path):
    store = new_store(tmp_path)
    store.close()
    store.close()
    with pytest.raises(ServerStateClosedError):
        store.list_rooms()
