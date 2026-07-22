import json
import os
import sqlite3
import stat
import uuid

import pytest

from game.network_replay import NetworkReplayError
from game.network_replay_store import (
    NETWORK_REPLAY_STORE_KEY_BYTES,
    NetworkReplayPersistenceClosedError,
    NetworkReplayPersistenceConflictError,
    NetworkReplayPersistenceError,
    NetworkReplayPersistenceIntegrityError,
    NetworkReplayPersistenceValidationError,
    SQLiteNetworkReplayStore,
)


ROOM_ID = "26e318a8-17a0-4c27-8bfd-2940e5e7d96b"
OTHER_ROOM_ID = "e3e8f9d7-e677-4d61-ab66-7c45782dd97c"


class Clock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


def player(name, count, *, private):
    return {
        "name": name,
        "resources": {"WOOD": count} if private else None,
        "resource_total": count,
        "development_cards": {"KNIGHT": 1} if private else None,
        "new_development_cards": {} if private else None,
        "victory_point_cards": 0 if private else None,
    }


def snapshot(revision, viewer, *, marker=None, finished=False):
    return {
        "type": "state_snapshot",
        "protocol_version": 1,
        "revision": revision,
        "viewer_player_index": viewer,
        "board_manifest": {
            "format": "test-board",
            "revision": revision,
            "marker": marker,
        },
        "command_options": [{"command": "roll_dice", "args": {}}],
        "state": {
            "players": [
                player("Alice", revision + 1, private=viewer == 0),
                player("Bob", revision + 2, private=viewer == 1),
            ],
            "phase": {
                "name": "finished" if finished else "main",
                "special_phase": None,
            },
            "domestic_trade": {},
            "development_deck": {"remaining": 20},
            "match_metrics": {
                "important_events": [],
                "point_checkpoints": [],
            },
            "history": {
                "latest_event": {"title": f"操作 {revision}"},
                "log_messages": [],
                "public_gain_history": {},
                "turn_summary_entries": [],
            },
        },
    }


def result_document():
    return {
        "format": "catan-match-result",
        "version": 1,
        "source": "match_metrics",
        "completed": True,
        "board": {"mode": "standard", "seed": 42},
        "victory_target": 10,
        "winner": {"seat": 1, "name": "Alice"},
        "standings": [],
        "vp_progression": [],
        "timeline_unit": "イベント",
        "important_events": [],
        "replay": {"available": False, "frame_count": 0},
    }


def store_path(tmp_path):
    directory = tmp_path / "private-replays"
    directory.mkdir(mode=0o700, parents=True)
    os.chmod(directory, 0o700)
    return directory / "network-replays.sqlite3"


def new_store(tmp_path, **kwargs):
    return SQLiteNetworkReplayStore(store_path(tmp_path), **kwargs)


def bind_and_record(store, *, code="ABC123", room_id=ROOM_ID, revision=0):
    store.bind_room(code, room_id)
    store.record_snapshot(code, snapshot(revision, 0))


def test_creates_private_separate_key_wal_database_and_hides_key_from_repr(tmp_path):
    store = new_store(tmp_path)
    try:
        assert store.database_path != store.key_path
        assert len(store.key_path.read_bytes()) == NETWORK_REPLAY_STORE_KEY_BYTES
        assert stat.S_IMODE(store.database_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(store.database_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.key_path.stat().st_mode) == 0o600
        assert store._connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert store._connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert store.key_path.read_bytes().hex() not in repr(store)
        assert "_key" not in repr(store)
    finally:
        store.close()


def test_append_rows_are_canonical_authenticated_and_survive_restart(tmp_path):
    clock = Clock()
    path = store_path(tmp_path)
    store = SQLiteNetworkReplayStore(path, clock=clock)
    bind_and_record(store)
    first_payload = bytes(
        store._connection.execute(
            "SELECT frame_json FROM replay_frames WHERE revision = 0"
        ).fetchone()[0]
    )
    clock.value += 0.25
    store.record_snapshot("ABC123", snapshot(1, 0))

    rows = store._connection.execute(
        "SELECT revision, frame_json FROM replay_frames ORDER BY revision"
    ).fetchall()
    assert [row[0] for row in rows] == [0, 1]
    assert bytes(rows[0][1]) == first_payload
    for row in rows:
        payload = bytes(row[1])
        assert payload == json.dumps(
            json.loads(payload),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    generation = store._connection.execute(
        "SELECT generation FROM replay_rooms"
    ).fetchone()[0]
    assert generation == 3  # bind + two durable revisions
    store.close()

    reopened = SQLiteNetworkReplayStore(path, clock=clock)
    try:
        assert reopened.latest_revision("ABC123") == 1
        first = reopened.frame_payload(
            "ABC123", viewer_player_index=0, frame_index=0
        )
        assert first["controls"]["revision"] == 0
        assert first["snapshot"]["state"]["players"][0]["resources"] == {
            "WOOD": 1
        }
        assert "reconnect" not in json.dumps(first, ensure_ascii=False).casefold()
    finally:
        reopened.close()


def test_final_public_result_is_bounded_metadata_and_survives_restart(tmp_path):
    path = store_path(tmp_path)
    store = SQLiteNetworkReplayStore(path)
    store.bind_room("ABC123", ROOM_ID)
    store.record_snapshot(
        "ABC123",
        snapshot(4, 0, finished=True),
        result=result_document(),
    )
    assert store._connection.execute(
        "SELECT final_result_json IS NOT NULL FROM replay_rooms"
    ).fetchone()[0] == 1
    store.close()

    with SQLiteNetworkReplayStore(path) as reopened:
        payload = reopened.result_payload("ABC123", viewer_player_index=0)
        assert payload["result"]["winner"] == {"seat": 1, "name": "Alice"}
        assert payload["replay"]["last_revision"] == 4


def test_same_revision_can_add_viewer_but_cannot_fork_existing_snapshot(tmp_path):
    with new_store(tmp_path) as store:
        bind_and_record(store, revision=3)
        with pytest.raises(
            NetworkReplayPersistenceConflictError, match="forked"
        ):
            store.record_snapshot("ABC123", snapshot(3, 0, marker="fork"))

        unchanged = store.frame_payload(
            "ABC123", viewer_player_index=0, frame_index=0
        )
        assert unchanged["snapshot"]["board_manifest"]["marker"] is None

        store.record_snapshot("ABC123", snapshot(3, 1))
        assert (
            store.frame_payload("ABC123", viewer_player_index=1, frame_index=0)[
                "snapshot"
            ]["state"]["players"][1]["resources"]
            == {"WOOD": 5}
        )


def test_repeated_identical_binding_is_a_read_only_fast_path(tmp_path):
    with new_store(tmp_path) as store:
        store.bind_room("ABC123", ROOM_ID)
        generation = store._connection.execute(
            "SELECT generation FROM replay_rooms"
        ).fetchone()[0]
        store.bind_room("ABC123", ROOM_ID)
        assert store._connection.execute(
            "SELECT generation FROM replay_rooms"
        ).fetchone()[0] == generation


def test_stale_parallel_writer_is_rejected_and_recovers_to_durable_truth(tmp_path):
    path = store_path(tmp_path)
    first = SQLiteNetworkReplayStore(path)
    bind_and_record(first)
    second = SQLiteNetworkReplayStore(path, key_path=first.key_path)
    try:
        first.record_snapshot("ABC123", snapshot(1, 0, marker="winner"))
        with pytest.raises(
            NetworkReplayPersistenceConflictError, match="generation"
        ):
            second.record_snapshot("ABC123", snapshot(1, 0, marker="loser"))
        recovered = second.frame_payload(
            "ABC123", viewer_player_index=0, frame_index=1
        )
        assert recovered["snapshot"]["board_manifest"]["marker"] == "winner"
    finally:
        first.close()
        second.close()


def test_failed_revision_is_not_kept_in_memory_and_next_gap_is_truncated(tmp_path):
    with new_store(tmp_path) as store:
        bind_and_record(store, revision=0)
        persist = store._persist_authority_locked

        def fail(_authority):
            raise NetworkReplayPersistenceError("simulated disk failure")

        store._persist_authority_locked = fail
        with pytest.raises(NetworkReplayPersistenceError, match="simulated"):
            store.record_snapshot("ABC123", snapshot(1, 0))
        assert store.latest_revision("ABC123") == 0

        store._persist_authority_locked = persist
        store.record_snapshot("ABC123", snapshot(2, 0))
        authority = store.export_room_authority("ABC123")
        assert [frame["revision"] for frame in authority["frames"]] == [0, 2]
        assert authority["truncated"] is True


def test_room_code_reuse_cannot_inherit_previous_room_replay(tmp_path):
    with new_store(tmp_path) as store:
        bind_and_record(store)
        assert store.latest_revision("ABC123") == 0

        store.bind_room("ABC123", OTHER_ROOM_ID)
        assert store.latest_revision("ABC123") is None
        with pytest.raises(NetworkReplayError) as missing:
            store.frame_payload("ABC123", viewer_player_index=0, frame_index=0)
        assert missing.value.code == "replay_not_found"

        store.record_snapshot("ABC123", snapshot(8, 0, marker="new-room"))
        assert store.latest_revision("ABC123") == 8
        assert store._connection.execute(
            "SELECT room_id FROM replay_rooms WHERE room_code = 'ABC123'"
        ).fetchone()[0] == OTHER_ROOM_ID
        assert store._connection.execute(
            "SELECT COUNT(*) FROM replay_frames"
        ).fetchone()[0] == 1


def test_discard_removes_archive_and_current_stable_binding(tmp_path):
    with new_store(tmp_path) as store:
        bind_and_record(store)
        assert store.discard_room("ABC123") is True
        assert store.discard_room("ABC123") is False
        assert store.latest_revision("ABC123") is None
        assert store._connection.execute(
            "SELECT COUNT(*) FROM replay_rooms"
        ).fetchone()[0] == 0
        with pytest.raises(
            NetworkReplayPersistenceValidationError, match="must be bound"
        ):
            store.record_snapshot("ABC123", snapshot(1, 0))


def test_unbound_capture_and_room_id_rebinding_fail_closed(tmp_path):
    with new_store(tmp_path) as store:
        with pytest.raises(
            NetworkReplayPersistenceValidationError, match="must be bound"
        ):
            store.record_snapshot("ABC123", snapshot(0, 0))
        store.bind_room("ABC123", ROOM_ID)
        with pytest.raises(
            NetworkReplayPersistenceConflictError, match="different room code"
        ):
            store.bind_room("XYZ999", ROOM_ID)


def test_max_frames_rooms_and_ttl_are_enforced_durably(tmp_path):
    wall = Clock(1_000.0)
    path = store_path(tmp_path)
    store = SQLiteNetworkReplayStore(
        path,
        max_frames=2,
        max_rooms=2,
        ttl_seconds=10,
        wall_clock=wall,
    )
    bind_and_record(store, revision=0)
    store.record_snapshot("ABC123", snapshot(1, 0))
    store.record_snapshot("ABC123", snapshot(2, 0))
    assert store._connection.execute(
        "SELECT COUNT(*) FROM replay_frames"
    ).fetchone()[0] == 2
    assert store.export_room_authority("ABC123")["truncated"] is True

    bind_and_record(store, code="DEF456", room_id=OTHER_ROOM_ID)
    third_id = str(uuid.uuid4())
    bind_and_record(store, code="GHI789", room_id=third_id)
    assert len(store.room_codes) == 2
    assert store.latest_revision("ABC123") is None

    wall.value += 11
    assert store.room_codes == ()
    assert store._connection.execute(
        "SELECT COUNT(*) FROM replay_rooms"
    ).fetchone()[0] == 0
    store.close()

    with SQLiteNetworkReplayStore(
        path,
        max_frames=2,
        max_rooms=2,
        ttl_seconds=10,
        wall_clock=wall,
    ) as reopened:
        assert reopened.room_codes == ()


def test_max_bytes_evicts_other_room_and_rejects_oversized_single_room(tmp_path):
    path = store_path(tmp_path)
    with SQLiteNetworkReplayStore(path, max_bytes=2_500) as store:
        bind_and_record(store, code="ABC123", room_id=ROOM_ID)
        bind_and_record(store, code="DEF456", room_id=OTHER_ROOM_ID)
        assert store.room_codes == ("DEF456",)
        assert store._connection.execute(
            "SELECT SUM(payload_bytes) FROM replay_rooms"
        ).fetchone()[0] <= 2_500

    other_path = tmp_path / "oversized" / "private-replays"
    other_path.mkdir(mode=0o700, parents=True)
    with SQLiteNetworkReplayStore(
        other_path / "replays.sqlite3", max_bytes=1_000
    ) as store:
        store.bind_room("ABC123", ROOM_ID)
        with pytest.raises(
            NetworkReplayPersistenceValidationError, match="exceeds max_bytes"
        ):
            store.record_snapshot("ABC123", snapshot(0, 0))
        assert store.latest_revision("ABC123") is None


def test_wrong_missing_or_truncated_key_fails_before_replay_load(tmp_path):
    path = store_path(tmp_path)
    store = SQLiteNetworkReplayStore(path)
    bind_and_record(store)
    key = store.key_path
    store.close()

    key.write_bytes(os.urandom(NETWORK_REPLAY_STORE_KEY_BYTES))
    with pytest.raises(
        NetworkReplayPersistenceIntegrityError, match="key or integrity"
    ):
        SQLiteNetworkReplayStore(path, key_path=key)

    key.unlink()
    with pytest.raises(
        NetworkReplayPersistenceIntegrityError, match="missing its key"
    ):
        SQLiteNetworkReplayStore(path, key_path=key)
    key.write_bytes(b"short")
    with pytest.raises(
        NetworkReplayPersistenceIntegrityError, match="invalid length"
    ):
        SQLiteNetworkReplayStore(path, key_path=key)


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("replay_frames", "frame_json", b"{}"),
        ("replay_frames", "revision", 9),
        ("replay_rooms", "generation", 99),
        ("replay_rooms", "room_code", "XYZ999"),
    ],
)
def test_tampered_frame_or_room_row_fails_closed_on_open(
    tmp_path, table, column, value
):
    path = store_path(tmp_path)
    store = SQLiteNetworkReplayStore(path)
    bind_and_record(store)
    key = store.key_path
    store.close()
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"UPDATE {table} SET {column} = ?", (value,))
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(NetworkReplayPersistenceIntegrityError):
        SQLiteNetworkReplayStore(path, key_path=key)


def test_live_tampering_is_detected_by_explicit_integrity_check(tmp_path):
    store = new_store(tmp_path)
    bind_and_record(store)
    connection = sqlite3.connect(store.database_path)
    try:
        connection.execute("UPDATE replay_frames SET frame_json = ?", (b"{}",))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(NetworkReplayPersistenceIntegrityError):
        store.verify_integrity()
    store.close()


@pytest.mark.parametrize(
    "schema_sql",
    [
        "CREATE VIEW replay_codes AS SELECT room_code FROM replay_rooms",
        "CREATE INDEX replay_expiry ON replay_rooms(expires_at_ms)",
        "ALTER TABLE replay_rooms ADD COLUMN unexpected TEXT",
        (
            "CREATE TRIGGER replay_touch AFTER UPDATE ON replay_rooms "
            "BEGIN SELECT 1; END"
        ),
    ],
)
def test_schema_drift_fails_closed(tmp_path, schema_sql):
    path = store_path(tmp_path)
    store = SQLiteNetworkReplayStore(path)
    key = store.key_path
    store.close()
    connection = sqlite3.connect(path)
    try:
        connection.execute(schema_sql)
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(NetworkReplayPersistenceIntegrityError, match="schema|table"):
        SQLiteNetworkReplayStore(path, key_path=key)


def test_closed_and_fork_inherited_instances_fail_closed(tmp_path):
    store = new_store(tmp_path)
    store.close()
    with pytest.raises(NetworkReplayPersistenceClosedError, match="closed"):
        store.bind_room("ABC123", ROOM_ID)

    forked = new_store(tmp_path / "fork")
    forked._pid -= 1
    with pytest.raises(NetworkReplayPersistenceClosedError, match="fork"):
        _ = forked.room_codes
    forked._pid = os.getpid()
    forked.close()


def test_unsafe_parent_same_key_path_and_invalid_bounds_are_rejected(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    os.chmod(shared, 0o755)
    with pytest.raises(NetworkReplayPersistenceValidationError, match="0700"):
        SQLiteNetworkReplayStore(shared / "replays.sqlite3")

    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    path = private / "same"
    with pytest.raises(NetworkReplayPersistenceValidationError, match="separate"):
        SQLiteNetworkReplayStore(path, key_path=path)
    with pytest.raises(NetworkReplayPersistenceValidationError, match="max_bytes"):
        SQLiteNetworkReplayStore(private / "bad.sqlite3", max_bytes=0)
