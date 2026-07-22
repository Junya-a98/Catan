"""Durable, append-oriented storage for viewer-safe network replays.

``NetworkReplayStore`` deliberately keeps its archive in memory.  This module
adds a local SQLite durability boundary without changing that public replay
contract.  Each retained revision is stored in its own immutable,
HMAC-authenticated row; small room metadata and a final public result live in a
separate authenticated row.  The authentication key is stored in an
owner-only file next to (but never inside) the database.

HMAC detects accidental corruption and offline modification.  It does not
encrypt replay snapshots.  Callers must keep the database outside the Web
static root and treat it as private server state.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import struct
import threading
import time
from typing import Any
import uuid

from game.match_result import build_match_result
from game.network_replay import (
    DEFAULT_MAX_NETWORK_REPLAY_FRAMES,
    DEFAULT_MAX_NETWORK_REPLAY_ROOMS,
    MAX_NETWORK_REPLAY_AUTHORITY_BYTES,
    MAX_NETWORK_REPLAY_FRAMES,
    MAX_NETWORK_REPLAY_ROOMS,
    MAX_NETWORK_REPLAY_REVISION,
    NetworkReplayError,
    NetworkReplayStore,
)


NETWORK_REPLAY_STORE_SCHEMA_VERSION = 1
NETWORK_REPLAY_STORE_KEY_BYTES = 32
NETWORK_REPLAY_STORE_MAC_BYTES = hashlib.sha256().digest_size
DEFAULT_MAX_NETWORK_REPLAY_BYTES = 128 * 1024 * 1024
MAX_NETWORK_REPLAY_BYTES = 512 * 1024 * 1024
DEFAULT_NETWORK_REPLAY_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_NETWORK_REPLAY_TTL_SECONDS = 366 * 24 * 60 * 60
MAX_NETWORK_REPLAY_TIMESTAMP_MS = 253_402_300_799_999
MAX_NETWORK_REPLAY_GENERATION = (1 << 63) - 1

_ROOM_CODE_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_META_SCHEMA_VERSION = "schema_version"
_META_KEY_CHECK = "key_check"
_EXPECTED_META_KEYS = frozenset({_META_SCHEMA_VERSION, _META_KEY_CHECK})
_KEY_CHECK_DOMAIN = b"catan-network-replay-store/key-check/v1\0"
_ROOM_MAC_DOMAIN = b"catan-network-replay-store/room/v1\0"
_FRAME_MAC_DOMAIN = b"catan-network-replay-store/frame/v1\0"
_NONE_BLOB_MARKER = b"\xff"

_CREATE_META_SQL = """
CREATE TABLE replay_meta (
    key TEXT PRIMARY KEY NOT NULL,
    value BLOB NOT NULL
) WITHOUT ROWID
"""
_CREATE_ROOMS_SQL = """
CREATE TABLE replay_rooms (
    room_id TEXT PRIMARY KEY NOT NULL,
    room_code TEXT NOT NULL UNIQUE,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
    expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms > updated_at_ms),
    truncated INTEGER NOT NULL CHECK (truncated IN (0, 1)),
    first_revision INTEGER,
    latest_revision INTEGER,
    event_revisions_json BLOB NOT NULL,
    checkpoint_revisions_json BLOB NOT NULL,
    final_result_json BLOB,
    payload_bytes INTEGER NOT NULL CHECK (payload_bytes >= 0),
    mac BLOB NOT NULL CHECK (length(mac) = 32),
    CHECK ((first_revision IS NULL) = (latest_revision IS NULL)),
    CHECK (first_revision IS NULL OR (
        first_revision >= 0 AND latest_revision >= first_revision
    ))
) WITHOUT ROWID
"""
_CREATE_FRAMES_SQL = """
CREATE TABLE replay_frames (
    room_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    elapsed_ms INTEGER NOT NULL CHECK (elapsed_ms >= 0),
    label TEXT NOT NULL,
    frame_json BLOB NOT NULL,
    payload_bytes INTEGER NOT NULL CHECK (payload_bytes >= 2),
    mac BLOB NOT NULL CHECK (length(mac) = 32),
    PRIMARY KEY (room_id, revision),
    FOREIGN KEY (room_id) REFERENCES replay_rooms(room_id) ON DELETE CASCADE
) WITHOUT ROWID
"""
_ROOM_COLUMNS = (
    "room_id, room_code, generation, updated_at_ms, expires_at_ms, truncated, "
    "first_revision, latest_revision, event_revisions_json, "
    "checkpoint_revisions_json, final_result_json, payload_bytes, mac"
)
_FRAME_COLUMNS = (
    "room_id, revision, elapsed_ms, label, frame_json, payload_bytes, mac"
)
_EXPECTED_TABLE_SQL = {
    "replay_frames": _CREATE_FRAMES_SQL,
    "replay_meta": _CREATE_META_SQL,
    "replay_rooms": _CREATE_ROOMS_SQL,
}
_EXPECTED_TABLE_INFO = {
    "replay_meta": (
        (0, "key", "TEXT", 1, None, 1),
        (1, "value", "BLOB", 1, None, 0),
    ),
    "replay_rooms": (
        (0, "room_id", "TEXT", 1, None, 1),
        (1, "room_code", "TEXT", 1, None, 0),
        (2, "generation", "INTEGER", 1, None, 0),
        (3, "updated_at_ms", "INTEGER", 1, None, 0),
        (4, "expires_at_ms", "INTEGER", 1, None, 0),
        (5, "truncated", "INTEGER", 1, None, 0),
        (6, "first_revision", "INTEGER", 0, None, 0),
        (7, "latest_revision", "INTEGER", 0, None, 0),
        (8, "event_revisions_json", "BLOB", 1, None, 0),
        (9, "checkpoint_revisions_json", "BLOB", 1, None, 0),
        (10, "final_result_json", "BLOB", 0, None, 0),
        (11, "payload_bytes", "INTEGER", 1, None, 0),
        (12, "mac", "BLOB", 1, None, 0),
    ),
    "replay_frames": (
        (0, "room_id", "TEXT", 1, None, 1),
        (1, "revision", "INTEGER", 1, None, 2),
        (2, "elapsed_ms", "INTEGER", 1, None, 0),
        (3, "label", "TEXT", 1, None, 0),
        (4, "frame_json", "BLOB", 1, None, 0),
        (5, "payload_bytes", "INTEGER", 1, None, 0),
        (6, "mac", "BLOB", 1, None, 0),
    ),
}
_EXPECTED_INDEX_SIGNATURES = {
    "replay_meta": frozenset({(True, "pk", False, ("key",))}),
    "replay_rooms": frozenset(
        {
            (True, "pk", False, ("room_id",)),
            (True, "u", False, ("room_code",)),
        }
    ),
    "replay_frames": frozenset(
        {(True, "pk", False, ("room_id", "revision"))}
    ),
}


class NetworkReplayPersistenceError(RuntimeError):
    """Base class for durable replay failures."""


class NetworkReplayPersistenceValidationError(
    NetworkReplayPersistenceError,
    ValueError,
):
    """Raised for invalid paths, limits, bindings, or oversized payloads."""


class NetworkReplayPersistenceIntegrityError(NetworkReplayPersistenceError):
    """Raised for a wrong key, tampering, corruption, or schema drift."""


class NetworkReplayPersistenceConflictError(NetworkReplayPersistenceError):
    """Raised for a stale writer, replay fork, or room identity conflict."""


class NetworkReplayPersistenceClosedError(NetworkReplayPersistenceError):
    """Raised after close or when a connection crosses a process fork."""


class SQLiteNetworkReplayStore:
    """Persist a :class:`NetworkReplayStore` in append-oriented SQLite rows.

    ``bind_room`` must be called with the authoritative stable ``room_id``
    before the first capture.  A room code can be reused by the lobby, so the
    stable ID is the database identity and prevents a new match from seeing an
    older match's replay.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        key_path: str | Path | None = None,
        max_frames: int = DEFAULT_MAX_NETWORK_REPLAY_FRAMES,
        max_rooms: int = DEFAULT_MAX_NETWORK_REPLAY_ROOMS,
        max_bytes: int = DEFAULT_MAX_NETWORK_REPLAY_BYTES,
        ttl_seconds: int = DEFAULT_NETWORK_REPLAY_TTL_SECONDS,
        busy_timeout_ms: int = 5_000,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        snapshot_builder: Callable[..., dict[str, Any]] | None = None,
        result_builder: Callable[..., dict[str, Any]] = build_match_result,
    ) -> None:
        database = _validated_path(database_path, label="database path")
        key = (
            _validated_path(key_path, label="key path")
            if key_path is not None
            else database.with_suffix(database.suffix + ".key")
        )
        if os.path.abspath(database) == os.path.abspath(key):
            raise NetworkReplayPersistenceValidationError(
                "database and replay key must use separate files"
            )
        if (
            type(max_frames) is not int
            or not 1 <= max_frames <= MAX_NETWORK_REPLAY_FRAMES
        ):
            raise NetworkReplayPersistenceValidationError(
                f"max_frames must be 1..{MAX_NETWORK_REPLAY_FRAMES}"
            )
        if (
            type(max_rooms) is not int
            or not 1 <= max_rooms <= MAX_NETWORK_REPLAY_ROOMS
        ):
            raise NetworkReplayPersistenceValidationError(
                f"max_rooms must be 1..{MAX_NETWORK_REPLAY_ROOMS}"
            )
        if (
            type(max_bytes) is not int
            or not 1 <= max_bytes <= MAX_NETWORK_REPLAY_BYTES
        ):
            raise NetworkReplayPersistenceValidationError(
                f"max_bytes must be 1..{MAX_NETWORK_REPLAY_BYTES}"
            )
        if (
            type(ttl_seconds) is not int
            or not 1 <= ttl_seconds <= MAX_NETWORK_REPLAY_TTL_SECONDS
        ):
            raise NetworkReplayPersistenceValidationError(
                f"ttl_seconds must be 1..{MAX_NETWORK_REPLAY_TTL_SECONDS}"
            )
        if (
            type(busy_timeout_ms) is not int
            or not 1 <= busy_timeout_ms <= 60_000
        ):
            raise NetworkReplayPersistenceValidationError(
                "busy_timeout_ms must be 1..60000"
            )
        if not callable(clock) or not callable(wall_clock):
            raise NetworkReplayPersistenceValidationError(
                "clock and wall_clock must be callable"
            )
        if snapshot_builder is not None and not callable(snapshot_builder):
            raise NetworkReplayPersistenceValidationError(
                "snapshot_builder must be callable"
            )
        if not callable(result_builder):
            raise NetworkReplayPersistenceValidationError(
                "result_builder must be callable"
            )

        _prepare_private_directory(database.parent)
        _prepare_private_directory(key.parent)
        database_preexisted = database.exists()
        _reject_unsafe_existing_file(database, label="database")
        _reject_unsafe_existing_file(key, label="replay key")

        self.database_path = database
        self.key_path = key
        self.max_frames = max_frames
        self.max_rooms = max_rooms
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._wall_clock = wall_clock
        self._snapshot_builder = snapshot_builder
        self._result_builder = result_builder
        self._key = _load_or_create_key(
            key,
            database_preexisted=database_preexisted,
        )
        self._lock = threading.RLock()
        self._pid = os.getpid()
        self._closed = False
        self._poisoned = False
        self._connection: sqlite3.Connection | None = None
        self._memory = self._new_memory_store()
        self._bindings: dict[str, str] = {}
        self._generations: dict[str, int] = {}

        try:
            connection = sqlite3.connect(
                str(database),
                timeout=busy_timeout_ms / 1_000,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            self._connection = connection
            self._configure_connection(busy_timeout_ms)
            _harden_file(database)
            self._initialize_or_verify_schema()
            self._reload_locked(prune=True)
            self._harden_storage_files()
        except Exception:
            if self._connection is not None:
                try:
                    self._connection.close()
                except sqlite3.Error:
                    pass
            self._connection = None
            self._closed = True
            raise

    def __enter__(self) -> SQLiteNetworkReplayStore:
        self._require_open()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(database_path={str(self.database_path)!r}, "
            f"max_frames={self.max_frames}, max_rooms={self.max_rooms}, "
            f"max_bytes={self.max_bytes}, ttl_seconds={self.ttl_seconds})"
        )

    @property
    def room_codes(self) -> tuple[str, ...]:
        with self._lock:
            self._expire_locked()
            return self._memory.room_codes

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            connection, self._connection = self._connection, None
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error as exc:
                    raise NetworkReplayPersistenceError(
                        "network replay store could not close cleanly"
                    ) from exc
            self._harden_storage_files()

    def bind_room(self, room_code: str, room_id: str) -> None:
        """Bind a public code to its stable authority ID before capturing.

        Reusing a public code for a different room atomically removes the old
        archive.  It can therefore never be exposed through the new room.
        """

        code = _validated_room_code(room_code)
        identifier = _validated_room_id(room_id)
        with self._lock, self._database_operation():
            connection = self._require_open()
            self._expire_locked()
            # The controller deliberately binds before both reads and writes.
            # In the normal single-process ownership model this fast path
            # avoids turning every replay read into a SQLite write/reload.
            if self._bindings.get(code) == identifier:
                return
            now = self._now_ms()
            with self._transaction(connection):
                by_id = connection.execute(
                    f"SELECT {_ROOM_COLUMNS} FROM replay_rooms WHERE room_id = ?",
                    (identifier,),
                ).fetchone()
                by_code = connection.execute(
                    f"SELECT {_ROOM_COLUMNS} FROM replay_rooms WHERE room_code = ?",
                    (code,),
                ).fetchone()
                if by_id is not None:
                    id_record = self._decode_room_row(by_id)
                    if id_record["room_code"] != code:
                        raise NetworkReplayPersistenceConflictError(
                            "room_id is already bound to a different room code"
                        )
                if by_code is not None:
                    code_record = self._decode_room_row(by_code)
                    if code_record["room_id"] != identifier:
                        self._verify_room_frames_locked(code_record)
                        connection.execute(
                            "DELETE FROM replay_rooms WHERE room_id = ?",
                            (code_record["room_id"],),
                        )
                        by_code = None

                if by_id is not None:
                    generation = id_record["generation"]
                    if generation >= MAX_NETWORK_REPLAY_GENERATION:
                        raise NetworkReplayPersistenceValidationError(
                            "replay generation cannot advance"
                        )
                    updated = max(now, id_record["updated_at_ms"])
                    expires = _expiry_ms(updated, self.ttl_seconds)
                    new_generation = generation + 1
                    values = dict(id_record)
                    values.update(
                        generation=new_generation,
                        updated_at_ms=updated,
                        expires_at_ms=expires,
                    )
                    mac = _room_mac(self._key, values)
                    cursor = connection.execute(
                        """
                        UPDATE replay_rooms
                        SET generation = ?, updated_at_ms = ?, expires_at_ms = ?,
                            mac = ?
                        WHERE room_id = ? AND generation = ?
                        """,
                        (
                            new_generation,
                            updated,
                            expires,
                            mac,
                            identifier,
                            generation,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise NetworkReplayPersistenceConflictError(
                            "replay generation does not match"
                        )
                else:
                    self._make_room_space_locked(connection, protected=identifier)
                    events = _canonical_json([])
                    checkpoints = _canonical_json([])
                    values = {
                        "room_id": identifier,
                        "room_code": code,
                        "generation": 1,
                        "updated_at_ms": now,
                        "expires_at_ms": _expiry_ms(now, self.ttl_seconds),
                        "truncated": 0,
                        "first_revision": None,
                        "latest_revision": None,
                        "event_revisions_json": events,
                        "checkpoint_revisions_json": checkpoints,
                        "final_result_json": None,
                        "payload_bytes": len(events) + len(checkpoints),
                    }
                    connection.execute(
                        """
                        INSERT INTO replay_rooms (
                            room_id, room_code, generation, updated_at_ms,
                            expires_at_ms, truncated, first_revision,
                            latest_revision, event_revisions_json,
                            checkpoint_revisions_json, final_result_json,
                            payload_bytes, mac
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (*_room_values(values), _room_mac(self._key, values)),
                    )
            self._reload_locked(prune=True)

    def latest_revision(self, room_code: str) -> int | None:
        with self._lock:
            self._expire_locked()
            return self._memory.latest_revision(room_code)

    def export_room_authority(self, room_code: str) -> dict[str, Any]:
        with self._lock:
            self._expire_locked()
            return self._memory.export_room_authority(room_code)

    def import_room_authority(self, document: Mapping[str, Any]) -> None:
        if not isinstance(document, Mapping):
            raise NetworkReplayError(
                "invalid_authority", "リプレイ権威文書が不正です。"
            )
        room_code = document.get("room_code")
        with self._lock:
            self._expire_locked()
            self._require_binding(room_code)
            self._mutate_and_persist(
                room_code,
                lambda: self._memory.import_room_authority(document),
            )

    def capture_game(
        self,
        room_code: str,
        game: Any,
        *,
        revision: int,
        label: str | None = None,
    ) -> None:
        with self._lock:
            self._expire_locked()
            self._require_binding(room_code)
            self._mutate_and_persist(
                room_code,
                lambda: self._memory.capture_game(
                    room_code,
                    game,
                    revision=revision,
                    label=label,
                ),
            )

    def capture_restored_game(
        self,
        room_code: str,
        game: Any,
        *,
        revision: int,
    ) -> None:
        with self._lock:
            self._expire_locked()
            self._require_binding(room_code)
            self._mutate_and_persist(
                room_code,
                lambda: self._memory.capture_restored_game(
                    room_code,
                    game,
                    revision=revision,
                ),
            )

    def record_snapshot(
        self,
        room_code: str,
        snapshot: Mapping[str, Any],
        *,
        label: str | None = None,
        metrics: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._expire_locked()
            self._require_binding(room_code)
            self._mutate_and_persist(
                room_code,
                lambda: self._memory.record_snapshot(
                    room_code,
                    snapshot,
                    label=label,
                    metrics=metrics,
                    result=result,
                ),
            )

    def record_revision(
        self,
        room_code: str,
        *,
        revision: int,
        snapshots: Mapping[int | None, Mapping[str, Any]],
        label: str | None = None,
        metrics: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._expire_locked()
            self._require_binding(room_code)
            self._mutate_and_persist(
                room_code,
                lambda: self._memory.record_revision(
                    room_code,
                    revision=revision,
                    snapshots=snapshots,
                    label=label,
                    metrics=metrics,
                    result=result,
                ),
            )

    def frame_payload(
        self,
        room_code: str,
        *,
        viewer_player_index: int | None,
        frame_index: int,
    ) -> dict[str, Any]:
        with self._lock:
            self._expire_locked()
            return self._memory.frame_payload(
                room_code,
                viewer_player_index=viewer_player_index,
                frame_index=frame_index,
            )

    def result_payload(
        self,
        room_code: str,
        *,
        viewer_player_index: int | None,
    ) -> dict[str, Any]:
        with self._lock:
            self._expire_locked()
            return self._memory.result_payload(
                room_code,
                viewer_player_index=viewer_player_index,
            )

    def discard_room(self, room_code: str) -> bool:
        code = _validated_room_code(room_code)
        with self._lock, self._database_operation():
            connection = self._require_open()
            self._expire_locked()
            with self._transaction(connection):
                row = connection.execute(
                    f"SELECT {_ROOM_COLUMNS} FROM replay_rooms WHERE room_code = ?",
                    (code,),
                ).fetchone()
                if row is None:
                    return False
                record = self._decode_room_row(row)
                self._verify_room_frames_locked(record)
                cursor = connection.execute(
                    "DELETE FROM replay_rooms WHERE room_id = ? AND generation = ?",
                    (record["room_id"], record["generation"]),
                )
                if cursor.rowcount != 1:
                    raise NetworkReplayPersistenceConflictError(
                        "replay generation does not match"
                    )
            self._reload_locked(prune=True)
            return True

    def verify_integrity(self) -> None:
        """Verify every authenticated row and canonical replay document."""

        with self._lock:
            self._require_open()
            self._load_database_locked()

    def _mutate_and_persist(
        self,
        room_code: str,
        operation: Callable[[], None],
    ) -> None:
        self._require_open()
        operation()
        try:
            authority = self._memory.export_room_authority(room_code)
            self._persist_authority_locked(authority)
            self._reload_locked(prune=True)
        except Exception as original:
            try:
                self._reload_locked(prune=False)
            except Exception as recovery:
                self._poisoned = True
                raise NetworkReplayPersistenceIntegrityError(
                    "durable replay state became uncertain"
                ) from recovery
            raise original

    def _persist_authority_locked(self, authority: dict[str, Any]) -> None:
        code = _validated_room_code(authority.get("room_code"))
        identifier = self._require_binding(code)
        canonical = self._canonical_authority(authority)
        frames = canonical["frames"]
        frame_payloads = {
            frame["revision"]: _canonical_json(frame) for frame in frames
        }
        events = _canonical_json(canonical["event_revisions"])
        checkpoints = _canonical_json(canonical["checkpoint_revisions"])
        result = (
            None
            if canonical["final_result"] is None
            else _canonical_json(canonical["final_result"])
        )
        room_payload_bytes = len(events) + len(checkpoints) + len(result or b"")
        frame_bytes = sum(len(payload) for payload in frame_payloads.values())
        if room_payload_bytes + frame_bytes > self.max_bytes:
            raise NetworkReplayPersistenceValidationError(
                "one replay exceeds max_bytes"
            )

        with self._database_operation():
            connection = self._require_open()
            now = self._now_ms()
            with self._transaction(connection):
                row = connection.execute(
                    f"SELECT {_ROOM_COLUMNS} FROM replay_rooms WHERE room_code = ?",
                    (code,),
                ).fetchone()
                if row is None:
                    raise NetworkReplayPersistenceConflictError(
                        "room replay binding disappeared"
                    )
                old_record = self._decode_room_row(row)
                if old_record["room_id"] != identifier:
                    raise NetworkReplayPersistenceConflictError(
                        "room replay binding changed"
                    )
                expected_generation = self._generations.get(identifier)
                if old_record["generation"] != expected_generation:
                    raise NetworkReplayPersistenceConflictError(
                        "replay generation does not match"
                    )
                old_authority = self._load_authority_locked(old_record)
                self._validate_evolution(old_authority, canonical)
                if old_record["generation"] >= MAX_NETWORK_REPLAY_GENERATION:
                    raise NetworkReplayPersistenceValidationError(
                        "replay generation cannot advance"
                    )

                old_revisions = {
                    frame["revision"]
                    for frame in (old_authority or {}).get("frames", [])
                }
                new_revisions = set(frame_payloads)
                for revision in sorted(old_revisions - new_revisions):
                    connection.execute(
                        "DELETE FROM replay_frames WHERE room_id = ? AND revision = ?",
                        (identifier, revision),
                    )
                old_frame_map = {
                    frame["revision"]: frame
                    for frame in (old_authority or {}).get("frames", [])
                }
                for frame in frames:
                    revision = frame["revision"]
                    payload = frame_payloads[revision]
                    if revision not in old_frame_map:
                        values = {
                            "room_id": identifier,
                            "revision": revision,
                            "elapsed_ms": frame["elapsed_ms"],
                            "label": frame["label"],
                            "frame_json": payload,
                            "payload_bytes": len(payload),
                        }
                        connection.execute(
                            """
                            INSERT INTO replay_frames (
                                room_id, revision, elapsed_ms, label,
                                frame_json, payload_bytes, mac
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (*_frame_values(values), _frame_mac(self._key, values)),
                        )
                    elif frame != old_frame_map[revision]:
                        self._assert_same_revision_merge(
                            old_frame_map[revision], frame
                        )
                        values = {
                            "room_id": identifier,
                            "revision": revision,
                            "elapsed_ms": frame["elapsed_ms"],
                            "label": frame["label"],
                            "frame_json": payload,
                            "payload_bytes": len(payload),
                        }
                        cursor = connection.execute(
                            """
                            UPDATE replay_frames
                            SET elapsed_ms = ?, label = ?, frame_json = ?,
                                payload_bytes = ?, mac = ?
                            WHERE room_id = ? AND revision = ?
                            """,
                            (
                                frame["elapsed_ms"],
                                frame["label"],
                                payload,
                                len(payload),
                                _frame_mac(self._key, values),
                                identifier,
                                revision,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise NetworkReplayPersistenceConflictError(
                                "replay frame changed concurrently"
                            )

                generation = old_record["generation"] + 1
                updated = max(now, old_record["updated_at_ms"])
                values = {
                    "room_id": identifier,
                    "room_code": code,
                    "generation": generation,
                    "updated_at_ms": updated,
                    "expires_at_ms": _expiry_ms(updated, self.ttl_seconds),
                    "truncated": int(canonical["truncated"]),
                    "first_revision": frames[0]["revision"],
                    "latest_revision": frames[-1]["revision"],
                    "event_revisions_json": events,
                    "checkpoint_revisions_json": checkpoints,
                    "final_result_json": result,
                    "payload_bytes": room_payload_bytes + frame_bytes,
                }
                cursor = connection.execute(
                    """
                    UPDATE replay_rooms
                    SET generation = ?, updated_at_ms = ?, expires_at_ms = ?,
                        truncated = ?, first_revision = ?, latest_revision = ?,
                        event_revisions_json = ?,
                        checkpoint_revisions_json = ?, final_result_json = ?,
                        payload_bytes = ?, mac = ?
                    WHERE room_id = ? AND generation = ?
                    """,
                    (
                        generation,
                        updated,
                        values["expires_at_ms"],
                        values["truncated"],
                        values["first_revision"],
                        values["latest_revision"],
                        events,
                        checkpoints,
                        result,
                        values["payload_bytes"],
                        _room_mac(self._key, values),
                        identifier,
                        old_record["generation"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise NetworkReplayPersistenceConflictError(
                        "replay generation does not match"
                    )
                self._prune_bytes_locked(connection, protected=identifier)

    def _validate_evolution(
        self,
        old: dict[str, Any] | None,
        new: dict[str, Any],
    ) -> None:
        if old is None:
            return
        old_frames = old["frames"]
        new_frames = new["frames"]
        old_latest = old_frames[-1]["revision"]
        new_latest = new_frames[-1]["revision"]
        if new_latest < old_latest:
            raise NetworkReplayPersistenceConflictError(
                "replay revision cannot move backwards"
            )
        old_by_revision = {frame["revision"]: frame for frame in old_frames}
        new_by_revision = {frame["revision"]: frame for frame in new_frames}
        for revision in set(old_by_revision).intersection(new_by_revision):
            if old_by_revision[revision] == new_by_revision[revision]:
                continue
            if revision != old_latest or revision != new_latest:
                raise NetworkReplayPersistenceConflictError(
                    "persisted replay history forked"
                )
            self._assert_same_revision_merge(
                old_by_revision[revision], new_by_revision[revision]
            )
        removed = [
            revision for revision in old_by_revision if revision not in new_by_revision
        ]
        if removed:
            kept = [
                revision for revision in old_by_revision if revision in new_by_revision
            ]
            if (
                not new["truncated"]
                or (kept and max(removed) >= min(kept))
                or (not kept and new_latest <= old_latest)
            ):
                raise NetworkReplayPersistenceConflictError(
                    "replay history cannot remove a non-prefix revision"
                )
        inserted = [
            revision for revision in new_by_revision if revision not in old_by_revision
        ]
        if any(revision <= old_latest for revision in inserted):
            raise NetworkReplayPersistenceConflictError(
                "replay history cannot insert an old revision"
            )
        if old["truncated"] and not new["truncated"]:
            raise NetworkReplayPersistenceConflictError(
                "truncated replay history cannot become complete"
            )
        for field in ("event_revisions", "checkpoint_revisions"):
            old_values = {
                row["sequence"]: row["revision"] for row in old[field]
            }
            new_values = {
                row["sequence"]: row["revision"] for row in new[field]
            }
            if any(new_values.get(key) != value for key, value in old_values.items()):
                raise NetworkReplayPersistenceConflictError(
                    "replay metric history forked"
                )
        if old["final_result"] is not None and old["final_result"] != new[
            "final_result"
        ]:
            raise NetworkReplayPersistenceConflictError(
                "final replay result is immutable"
            )

    def _assert_same_revision_merge(
        self,
        old: Mapping[str, Any],
        new: Mapping[str, Any],
    ) -> None:
        if (
            old.get("revision") != new.get("revision")
            or old.get("elapsed_ms") != new.get("elapsed_ms")
        ):
            raise NetworkReplayPersistenceConflictError(
                "same-revision replay metadata forked"
            )
        old_snapshots = {
            item["viewer_player_index"]: item["snapshot"]
            for item in old.get("snapshots", [])
        }
        new_snapshots = {
            item["viewer_player_index"]: item["snapshot"]
            for item in new.get("snapshots", [])
        }
        if any(
            new_snapshots.get(viewer) != snapshot
            for viewer, snapshot in old_snapshots.items()
        ):
            raise NetworkReplayPersistenceConflictError(
                "same-revision replay snapshot forked"
            )

    def _canonical_authority(self, authority: Mapping[str, Any]) -> dict[str, Any]:
        temporary = self._new_memory_store(max_rooms=1)
        temporary.import_room_authority(authority)
        return temporary.export_room_authority(authority["room_code"])

    def _new_memory_store(self, *, max_rooms: int | None = None) -> NetworkReplayStore:
        return NetworkReplayStore(
            max_frames=self.max_frames,
            max_rooms=self.max_rooms if max_rooms is None else max_rooms,
            clock=self._clock,
            snapshot_builder=self._snapshot_builder,
            result_builder=self._result_builder,
        )

    def _require_binding(self, room_code: Any) -> str:
        code = _validated_room_code(room_code)
        self._require_open()
        try:
            return self._bindings[code]
        except KeyError as exc:
            raise NetworkReplayPersistenceValidationError(
                "room must be bound to its stable room_id before capture"
            ) from exc

    def _reload_locked(self, *, prune: bool) -> None:
        self._require_open()
        if prune:
            self._prune_database_locked()
        memory, bindings, generations = self._load_database_locked()
        self._memory = memory
        self._bindings = bindings
        self._generations = generations

    def _load_database_locked(
        self,
    ) -> tuple[NetworkReplayStore, dict[str, str], dict[str, int]]:
        connection = self._require_open()
        try:
            rows = connection.execute(
                f"SELECT {_ROOM_COLUMNS} FROM replay_rooms "
                "ORDER BY updated_at_ms, room_id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise NetworkReplayPersistenceIntegrityError(
                "network replay rows could not be read"
            ) from exc
        if len(rows) > MAX_NETWORK_REPLAY_ROOMS:
            raise NetworkReplayPersistenceIntegrityError(
                "network replay database exceeds the hard room limit"
            )
        memory = self._new_memory_store()
        bindings: dict[str, str] = {}
        generations: dict[str, int] = {}
        total_bytes = 0
        for row in rows:
            record = self._decode_room_row(row)
            if record["room_code"] in bindings:
                raise NetworkReplayPersistenceIntegrityError(
                    "network replay room code is duplicated"
                )
            bindings[record["room_code"]] = record["room_id"]
            generations[record["room_id"]] = record["generation"]
            total_bytes += record["payload_bytes"]
            authority = self._load_authority_locked(record)
            if authority is not None:
                try:
                    memory.import_room_authority(authority)
                except NetworkReplayError as exc:
                    raise NetworkReplayPersistenceIntegrityError(
                        "persisted replay authority is invalid"
                    ) from exc
        if len(rows) > self.max_rooms or total_bytes > self.max_bytes:
            raise NetworkReplayPersistenceIntegrityError(
                "network replay database exceeds configured bounds"
            )
        return memory, bindings, generations

    def _load_authority_locked(
        self,
        record: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        connection = self._require_open()
        try:
            rows = connection.execute(
                f"SELECT {_FRAME_COLUMNS} FROM replay_frames "
                "WHERE room_id = ? ORDER BY revision",
                (record["room_id"],),
            ).fetchall()
        except sqlite3.Error as exc:
            raise NetworkReplayPersistenceIntegrityError(
                "network replay frame rows could not be read"
            ) from exc
        frames = [self._decode_frame_row(row) for row in rows]
        if record["first_revision"] is None:
            if frames:
                raise NetworkReplayPersistenceIntegrityError(
                    "unstarted replay binding contains frames"
                )
            return None
        if (
            not frames
            or frames[0]["revision"] != record["first_revision"]
            or frames[-1]["revision"] != record["latest_revision"]
            or sum(len(_canonical_json(frame)) for frame in frames)
            + len(record["event_revisions_json"])
            + len(record["checkpoint_revisions_json"])
            + len(record["final_result_json"] or b"")
            != record["payload_bytes"]
        ):
            raise NetworkReplayPersistenceIntegrityError(
                "network replay room/frame metadata is inconsistent"
            )
        authority = {
            "format": "catan-network-replay-authority",
            "version": 1,
            "room_code": record["room_code"],
            "truncated": bool(record["truncated"]),
            "frames": frames,
            "event_revisions": _parse_json_blob(
                record["event_revisions_json"], label="event revisions"
            ),
            "checkpoint_revisions": _parse_json_blob(
                record["checkpoint_revisions_json"],
                label="checkpoint revisions",
            ),
            "final_result": (
                None
                if record["final_result_json"] is None
                else _parse_json_blob(
                    record["final_result_json"], label="final result"
                )
            ),
        }
        try:
            return self._canonical_authority(authority)
        except (NetworkReplayError, KeyError, TypeError, ValueError) as exc:
            raise NetworkReplayPersistenceIntegrityError(
                "persisted replay authority is invalid"
            ) from exc

    def _verify_room_frames_locked(self, record: Mapping[str, Any]) -> None:
        self._load_authority_locked(record)

    def _decode_room_row(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            result = {
                "room_id": _validated_room_id(row["room_id"]),
                "room_code": _validated_room_code(row["room_code"]),
                "generation": _validated_generation(row["generation"]),
                "updated_at_ms": _validated_timestamp(
                    row["updated_at_ms"], label="updated_at_ms"
                ),
                "expires_at_ms": _validated_timestamp(
                    row["expires_at_ms"], label="expires_at_ms"
                ),
                "truncated": _validated_bit(row["truncated"], "truncated"),
                "first_revision": _optional_revision(row["first_revision"]),
                "latest_revision": _optional_revision(row["latest_revision"]),
                "event_revisions_json": _blob(
                    row["event_revisions_json"], label="event revisions"
                ),
                "checkpoint_revisions_json": _blob(
                    row["checkpoint_revisions_json"],
                    label="checkpoint revisions",
                ),
                "final_result_json": _optional_blob(
                    row["final_result_json"], label="final result"
                ),
                "payload_bytes": _validated_payload_bytes(row["payload_bytes"]),
            }
            if result["expires_at_ms"] <= result["updated_at_ms"]:
                raise ValueError("invalid expiry")
            if (result["first_revision"] is None) != (
                result["latest_revision"] is None
            ):
                raise ValueError("incomplete revision pair")
            if (
                result["first_revision"] is not None
                and result["latest_revision"] < result["first_revision"]
            ):
                raise ValueError("reversed revision pair")
            expected_metadata_bytes = (
                len(result["event_revisions_json"])
                + len(result["checkpoint_revisions_json"])
                + len(result["final_result_json"] or b"")
            )
            if result["payload_bytes"] < expected_metadata_bytes:
                raise ValueError("payload byte count is too small")
            mac = _blob(row["mac"], label="room MAC")
            if len(mac) != NETWORK_REPLAY_STORE_MAC_BYTES or not hmac.compare_digest(
                mac, _room_mac(self._key, result)
            ):
                raise NetworkReplayPersistenceIntegrityError(
                    "network replay database key or integrity is invalid"
                )
            _parse_json_blob(
                result["event_revisions_json"], label="event revisions"
            )
            _parse_json_blob(
                result["checkpoint_revisions_json"],
                label="checkpoint revisions",
            )
            if result["final_result_json"] is not None:
                _parse_json_blob(result["final_result_json"], label="final result")
            return result
        except NetworkReplayPersistenceError:
            raise
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise NetworkReplayPersistenceIntegrityError(
                "network replay room row is invalid"
            ) from exc

    def _decode_frame_row(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            values = {
                "room_id": _validated_room_id(row["room_id"]),
                "revision": _validated_revision(row["revision"]),
                "elapsed_ms": _validated_counter(
                    row["elapsed_ms"], label="elapsed_ms"
                ),
                "label": _validated_label(row["label"]),
                "frame_json": _blob(row["frame_json"], label="frame"),
                "payload_bytes": _validated_payload_bytes(row["payload_bytes"]),
            }
            if values["payload_bytes"] != len(values["frame_json"]):
                raise ValueError("frame byte count differs")
            mac = _blob(row["mac"], label="frame MAC")
            if len(mac) != NETWORK_REPLAY_STORE_MAC_BYTES or not hmac.compare_digest(
                mac, _frame_mac(self._key, values)
            ):
                raise NetworkReplayPersistenceIntegrityError(
                    "network replay database key or integrity is invalid"
                )
            frame = _parse_json_blob(values["frame_json"], label="frame")
            if (
                type(frame) is not dict
                or frame.get("revision") != values["revision"]
                or frame.get("elapsed_ms") != values["elapsed_ms"]
                or frame.get("label") != values["label"]
            ):
                raise ValueError("frame columns differ from payload")
            return frame
        except NetworkReplayPersistenceError:
            raise
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise NetworkReplayPersistenceIntegrityError(
                "network replay frame row is invalid"
            ) from exc

    def _expire_locked(self) -> None:
        self._require_open()
        connection = self._connection
        assert connection is not None
        now = self._now_ms()
        with self._database_operation():
            rows = connection.execute(
                f"SELECT {_ROOM_COLUMNS} FROM replay_rooms "
                "WHERE expires_at_ms <= ? ORDER BY room_id",
                (now,),
            ).fetchall()
            if not rows:
                return
            with self._transaction(connection):
                for row in rows:
                    record = self._decode_room_row(row)
                    self._verify_room_frames_locked(record)
                    connection.execute(
                        "DELETE FROM replay_rooms "
                        "WHERE room_id = ? AND generation = ?",
                        (record["room_id"], record["generation"]),
                    )
        self._reload_locked(prune=False)

    def _prune_database_locked(self) -> None:
        connection = self._require_open()
        self._expire_locked()
        with self._transaction(connection):
            while connection.execute(
                "SELECT COUNT(*) FROM replay_rooms"
            ).fetchone()[0] > self.max_rooms:
                self._evict_one_locked(connection, protected=None)
            self._prune_bytes_locked(connection, protected=None)

    def _make_room_space_locked(
        self,
        connection: sqlite3.Connection,
        *,
        protected: str,
    ) -> None:
        while connection.execute(
            "SELECT COUNT(*) FROM replay_rooms"
        ).fetchone()[0] >= self.max_rooms:
            self._evict_one_locked(connection, protected=protected)

    def _prune_bytes_locked(
        self,
        connection: sqlite3.Connection,
        *,
        protected: str | None,
    ) -> None:
        while True:
            rows = connection.execute(
                f"SELECT {_ROOM_COLUMNS} FROM replay_rooms "
                "ORDER BY updated_at_ms, room_id"
            ).fetchall()
            records = [self._decode_room_row(row) for row in rows]
            if sum(record["payload_bytes"] for record in records) <= self.max_bytes:
                return
            if not any(record["room_id"] != protected for record in records):
                raise NetworkReplayPersistenceValidationError(
                    "one replay exceeds max_bytes"
                )
            self._evict_one_locked(connection, protected=protected)

    def _evict_one_locked(
        self,
        connection: sqlite3.Connection,
        *,
        protected: str | None,
    ) -> None:
        rows = connection.execute(
            f"SELECT {_ROOM_COLUMNS} FROM replay_rooms "
            "WHERE room_id != COALESCE(?, '') "
            "ORDER BY (final_result_json IS NOT NULL), updated_at_ms, room_id",
            (protected,),
        ).fetchall()
        if not rows:
            raise NetworkReplayPersistenceConflictError(
                "no replay room can be evicted"
            )
        record = self._decode_room_row(rows[0])
        self._verify_room_frames_locked(record)
        connection.execute(
            "DELETE FROM replay_rooms WHERE room_id = ? AND generation = ?",
            (record["room_id"], record["generation"]),
        )

    def _configure_connection(self, busy_timeout_ms: int) -> None:
        connection = self._require_open()
        try:
            connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise NetworkReplayPersistenceIntegrityError(
                    "network replay database could not enable WAL"
                )
            connection.execute("PRAGMA synchronous = FULL")
            if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise NetworkReplayPersistenceIntegrityError(
                    "network replay database could not enable full sync"
                )
        except sqlite3.Error as exc:
            raise NetworkReplayPersistenceIntegrityError(
                "network replay database could not be configured"
            ) from exc

    def _initialize_or_verify_schema(self) -> None:
        connection = self._require_open()
        try:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check is None or quick_check[0] != "ok":
                raise NetworkReplayPersistenceIntegrityError(
                    "network replay database integrity check failed"
                )
            objects = tuple(
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT type, name, tbl_name FROM sqlite_schema
                    WHERE name NOT LIKE 'sqlite_%'
                    ORDER BY type, name
                    """
                )
            )
            expected_objects = (
                ("table", "replay_frames", "replay_frames"),
                ("table", "replay_meta", "replay_meta"),
                ("table", "replay_rooms", "replay_rooms"),
            )
            if not objects:
                with self._transaction(connection):
                    connection.execute(_CREATE_META_SQL)
                    connection.execute(_CREATE_ROOMS_SQL)
                    connection.execute(_CREATE_FRAMES_SQL)
                    connection.executemany(
                        "INSERT INTO replay_meta (key, value) VALUES (?, ?)",
                        (
                            (
                                _META_SCHEMA_VERSION,
                                str(NETWORK_REPLAY_STORE_SCHEMA_VERSION).encode(
                                    "ascii"
                                ),
                            ),
                            (_META_KEY_CHECK, _key_check(self._key)),
                        ),
                    )
                self._verify_schema_shape()
                _fsync_directory(self.database_path.parent)
                return
            if objects != expected_objects:
                raise NetworkReplayPersistenceIntegrityError(
                    "network replay database schema is unsupported"
                )
            self._verify_schema_shape()
            metadata = {
                row["key"]: bytes(row["value"])
                for row in connection.execute("SELECT key, value FROM replay_meta")
            }
            if set(metadata) != _EXPECTED_META_KEYS:
                raise NetworkReplayPersistenceIntegrityError(
                    "network replay database metadata is invalid"
                )
            if metadata[_META_SCHEMA_VERSION] != str(
                NETWORK_REPLAY_STORE_SCHEMA_VERSION
            ).encode("ascii"):
                raise NetworkReplayPersistenceIntegrityError(
                    "network replay database schema version is unsupported"
                )
            if not hmac.compare_digest(
                metadata[_META_KEY_CHECK], _key_check(self._key)
            ):
                raise NetworkReplayPersistenceIntegrityError(
                    "network replay database key or integrity is invalid"
                )
        except NetworkReplayPersistenceError:
            raise
        except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
            raise NetworkReplayPersistenceIntegrityError(
                "network replay database schema is invalid"
            ) from exc

    def _verify_schema_shape(self) -> None:
        connection = self._require_open()
        for table_name, expected_columns in _EXPECTED_TABLE_INFO.items():
            row = connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
            if (
                row is None
                or _normalised_schema_sql(row[0])
                != _normalised_schema_sql(_EXPECTED_TABLE_SQL[table_name])
            ):
                raise NetworkReplayPersistenceIntegrityError(
                    "network replay database table definition is unsupported"
                )
            columns = tuple(
                tuple(item)
                for item in connection.execute(f'PRAGMA table_info("{table_name}")')
            )
            if columns != expected_columns:
                raise NetworkReplayPersistenceIntegrityError(
                    "network replay database columns are unsupported"
                )
            signatures = set()
            for index in connection.execute(f'PRAGMA index_list("{table_name}")'):
                name = str(index[1]).replace('"', '""')
                names = tuple(
                    item[2]
                    for item in connection.execute(f'PRAGMA index_info("{name}")')
                )
                signatures.add((index[2] == 1, index[3], index[4] == 1, names))
            if frozenset(signatures) != _EXPECTED_INDEX_SIGNATURES[table_name]:
                raise NetworkReplayPersistenceIntegrityError(
                    "network replay database indexes are unsupported"
                )

    @contextmanager
    def _transaction(self, connection: sqlite3.Connection) -> Iterator[None]:
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        finally:
            self._harden_storage_files()

    @contextmanager
    def _database_operation(self) -> Iterator[None]:
        try:
            yield
        except NetworkReplayPersistenceError:
            raise
        except sqlite3.IntegrityError as exc:
            raise NetworkReplayPersistenceConflictError(
                "network replay database constraint failed"
            ) from exc
        except sqlite3.Error as exc:
            raise NetworkReplayPersistenceError(
                "network replay database operation failed"
            ) from exc

    def _require_open(self) -> sqlite3.Connection:
        if self._closed or self._connection is None or self._poisoned:
            raise NetworkReplayPersistenceClosedError(
                "network replay store is closed"
            )
        if os.getpid() != self._pid:
            raise NetworkReplayPersistenceClosedError(
                "network replay store cannot be reused after process fork"
            )
        return self._connection

    def _now_ms(self) -> int:
        try:
            value = float(self._wall_clock())
        except (TypeError, ValueError, OverflowError) as exc:
            raise NetworkReplayPersistenceValidationError(
                "wall clock is invalid"
            ) from exc
        if not math.isfinite(value) or value < 0:
            raise NetworkReplayPersistenceValidationError(
                "wall clock is invalid"
            )
        milliseconds = int(value * 1_000)
        if milliseconds > MAX_NETWORK_REPLAY_TIMESTAMP_MS:
            raise NetworkReplayPersistenceValidationError(
                "wall clock is out of range"
            )
        return milliseconds

    def _harden_storage_files(self) -> None:
        _harden_file(self.database_path)
        _harden_file(self.key_path)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.database_path}{suffix}")
            if sidecar.exists():
                _harden_file(sidecar)


def _validated_path(value: str | Path | None, *, label: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise NetworkReplayPersistenceValidationError(
            f"{label} must be a filesystem path"
        )
    path = Path(value).expanduser()
    if not str(path) or path.name in {"", ".", ".."}:
        raise NetworkReplayPersistenceValidationError(f"{label} must name a file")
    return path.absolute()


def _prepare_private_directory(path: Path) -> None:
    try:
        if not path.exists():
            path.mkdir(mode=0o700, parents=True, exist_ok=False)
        details = path.lstat()
        if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
            raise NetworkReplayPersistenceValidationError(
                "network replay parent must be a real directory"
            )
        if hasattr(os, "getuid") and details.st_uid != os.getuid():
            raise NetworkReplayPersistenceValidationError(
                "network replay parent must be owned by this user"
            )
        if stat.S_IMODE(details.st_mode) != 0o700:
            raise NetworkReplayPersistenceValidationError(
                "network replay parent permissions must be 0700"
            )
    except NetworkReplayPersistenceError:
        raise
    except OSError as exc:
        raise NetworkReplayPersistenceError(
            "network replay private directory could not be prepared"
        ) from exc


def _reject_unsafe_existing_file(path: Path, *, label: str) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise NetworkReplayPersistenceError(f"{label} could not be inspected") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise NetworkReplayPersistenceValidationError(
            f"{label} must be a regular file"
        )


def _load_or_create_key(path: Path, *, database_preexisted: bool) -> bytes:
    if not path.exists():
        if database_preexisted:
            raise NetworkReplayPersistenceIntegrityError(
                "existing network replay database is missing its key"
            )
        material = secrets.token_bytes(NETWORK_REPLAY_STORE_KEY_BYTES)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
            try:
                offset = 0
                while offset < len(material):
                    offset += os.write(descriptor, material[offset:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(path, 0o600)
            _fsync_directory(path.parent)
            return material
        except FileExistsError:
            pass
        except OSError as exc:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise NetworkReplayPersistenceError(
                "network replay key could not be created"
            ) from exc

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode):
                raise NetworkReplayPersistenceValidationError(
                    "network replay key must be a regular file"
                )
            material = os.read(descriptor, NETWORK_REPLAY_STORE_KEY_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(material) != NETWORK_REPLAY_STORE_KEY_BYTES:
            raise NetworkReplayPersistenceIntegrityError(
                "network replay key has an invalid length"
            )
        os.chmod(path, 0o600)
        return material
    except NetworkReplayPersistenceError:
        raise
    except OSError as exc:
        raise NetworkReplayPersistenceError(
            "network replay key could not be read"
        ) from exc


def _harden_file(path: Path) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise NetworkReplayPersistenceError(
            "network replay storage permissions could not be inspected"
        ) from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise NetworkReplayPersistenceIntegrityError(
            "network replay storage path is not a regular file"
        )
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise NetworkReplayPersistenceError(
            "network replay storage permissions could not be hardened"
        ) from exc


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise NetworkReplayPersistenceError(
            "network replay directory could not be synchronized"
        ) from exc


def _validated_room_code(value: Any) -> str:
    if type(value) is not str or _ROOM_CODE_PATTERN.fullmatch(value) is None:
        raise NetworkReplayPersistenceValidationError("room_code is invalid")
    return value


def _validated_room_id(value: Any) -> str:
    if type(value) is not str:
        raise NetworkReplayPersistenceValidationError(
            "room_id must be a canonical UUID"
        )
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise NetworkReplayPersistenceValidationError(
            "room_id must be a canonical UUID"
        ) from exc
    if str(parsed) != value or parsed.version != 4:
        raise NetworkReplayPersistenceValidationError(
            "room_id must be a canonical UUIDv4"
        )
    return value


def _validated_generation(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= MAX_NETWORK_REPLAY_GENERATION:
        raise ValueError("generation is invalid")
    return value


def _validated_timestamp(value: Any, *, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_NETWORK_REPLAY_TIMESTAMP_MS:
        raise ValueError(f"{label} is invalid")
    return value


def _validated_revision(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_NETWORK_REPLAY_REVISION:
        raise ValueError("revision is invalid")
    return value


def _optional_revision(value: Any) -> int | None:
    return None if value is None else _validated_revision(value)


def _validated_counter(value: Any, *, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_NETWORK_REPLAY_TIMESTAMP_MS:
        raise ValueError(f"{label} is invalid")
    return value


def _validated_bit(value: Any, label: str) -> int:
    if type(value) is not int or value not in (0, 1):
        raise ValueError(f"{label} is invalid")
    return value


def _validated_label(value: Any) -> str:
    if type(value) is not str or len(value) > 160:
        raise ValueError("label is invalid")
    return value


def _validated_payload_bytes(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_NETWORK_REPLAY_BYTES:
        raise ValueError("payload_bytes is invalid")
    return value


def _expiry_ms(updated_at_ms: int, ttl_seconds: int) -> int:
    expires = updated_at_ms + ttl_seconds * 1_000
    if expires > MAX_NETWORK_REPLAY_TIMESTAMP_MS:
        raise NetworkReplayPersistenceValidationError("replay expiry is out of range")
    return expires


def _blob(value: Any, *, label: str) -> bytes:
    result = value.tobytes() if isinstance(value, memoryview) else value
    if type(result) is not bytes or not 2 <= len(result) <= MAX_NETWORK_REPLAY_BYTES:
        raise ValueError(f"{label} blob is invalid")
    return result


def _optional_blob(value: Any, *, label: str) -> bytes | None:
    return None if value is None else _blob(value, label=label)


def _canonical_json(value: Any) -> bytes:
    _validate_json(value, depth=0, budget=[1_000_000], active=set())
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError, OverflowError) as exc:
        raise NetworkReplayPersistenceValidationError(
            "network replay value is not canonical JSON"
        ) from exc
    if not 2 <= len(payload) <= MAX_NETWORK_REPLAY_AUTHORITY_BYTES:
        raise NetworkReplayPersistenceValidationError(
            "network replay JSON exceeds the size limit"
        )
    return payload


def _validate_json(
    value: Any,
    *,
    depth: int,
    budget: list[int],
    active: set[int],
) -> None:
    budget[0] -= 1
    if budget[0] < 0 or depth > 96:
        raise NetworkReplayPersistenceValidationError(
            "network replay JSON is too complex"
        )
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > MAX_NETWORK_REPLAY_REVISION:
            raise NetworkReplayPersistenceValidationError(
                "network replay integer is out of range"
            )
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise NetworkReplayPersistenceValidationError(
                "network replay number must be finite"
            )
        return
    if type(value) is str:
        if len(value) > 1_048_576:
            raise NetworkReplayPersistenceValidationError(
                "network replay string is too long"
            )
        value.encode("utf-8", errors="strict")
        return
    if type(value) not in (dict, list):
        raise NetworkReplayPersistenceValidationError(
            "network replay contains a non-JSON value"
        )
    identity = id(value)
    if identity in active:
        raise NetworkReplayPersistenceValidationError(
            "network replay JSON contains a cycle"
        )
    active.add(identity)
    try:
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise NetworkReplayPersistenceValidationError(
                        "network replay JSON keys must be strings"
                    )
                _validate_json(
                    item,
                    depth=depth + 1,
                    budget=budget,
                    active=active,
                )
        else:
            for item in value:
                _validate_json(
                    item,
                    depth=depth + 1,
                    budget=budget,
                    active=active,
                )
    finally:
        active.remove(identity)


def _parse_json_blob(payload: bytes, *, label: str) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
        if not hmac.compare_digest(_canonical_json(value), payload):
            raise ValueError("JSON is not canonical")
        return value
    except NetworkReplayPersistenceValidationError as exc:
        raise NetworkReplayPersistenceIntegrityError(
            f"persisted {label} JSON is invalid"
        ) from exc
    except NetworkReplayPersistenceIntegrityError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise NetworkReplayPersistenceIntegrityError(
            f"persisted {label} JSON is invalid"
        ) from exc


def _mac_parts(key: bytes, domain: bytes, *parts: bytes) -> bytes:
    digest = hmac.new(key, domain, hashlib.sha256)
    for part in parts:
        digest.update(struct.pack(">Q", len(part)))
        digest.update(part)
    return digest.digest()


def _integer_bytes(value: int | None) -> bytes:
    return b"-" if value is None else str(value).encode("ascii")


def _room_mac(key: bytes, values: Mapping[str, Any]) -> bytes:
    final_result = values["final_result_json"]
    return _mac_parts(
        key,
        _ROOM_MAC_DOMAIN,
        values["room_id"].encode("ascii"),
        values["room_code"].encode("ascii"),
        _integer_bytes(values["generation"]),
        _integer_bytes(values["updated_at_ms"]),
        _integer_bytes(values["expires_at_ms"]),
        _integer_bytes(values["truncated"]),
        _integer_bytes(values["first_revision"]),
        _integer_bytes(values["latest_revision"]),
        values["event_revisions_json"],
        values["checkpoint_revisions_json"],
        _NONE_BLOB_MARKER if final_result is None else final_result,
        _integer_bytes(values["payload_bytes"]),
    )


def _frame_mac(key: bytes, values: Mapping[str, Any]) -> bytes:
    return _mac_parts(
        key,
        _FRAME_MAC_DOMAIN,
        values["room_id"].encode("ascii"),
        _integer_bytes(values["revision"]),
        _integer_bytes(values["elapsed_ms"]),
        values["label"].encode("utf-8"),
        values["frame_json"],
        _integer_bytes(values["payload_bytes"]),
    )


def _key_check(key: bytes) -> bytes:
    return hmac.new(key, _KEY_CHECK_DOMAIN, hashlib.sha256).digest()


def _room_values(values: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        values[key]
        for key in (
            "room_id",
            "room_code",
            "generation",
            "updated_at_ms",
            "expires_at_ms",
            "truncated",
            "first_revision",
            "latest_revision",
            "event_revisions_json",
            "checkpoint_revisions_json",
            "final_result_json",
            "payload_bytes",
        )
    )


def _frame_values(values: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        values[key]
        for key in (
            "room_id",
            "revision",
            "elapsed_ms",
            "label",
            "frame_json",
            "payload_bytes",
        )
    )


def _normalised_schema_sql(value: Any) -> str:
    if type(value) is not str:
        raise NetworkReplayPersistenceIntegrityError(
            "network replay table definition is invalid"
        )
    return " ".join(value.casefold().split())


__all__ = (
    "DEFAULT_MAX_NETWORK_REPLAY_BYTES",
    "DEFAULT_NETWORK_REPLAY_TTL_SECONDS",
    "MAX_NETWORK_REPLAY_BYTES",
    "MAX_NETWORK_REPLAY_TTL_SECONDS",
    "NETWORK_REPLAY_STORE_KEY_BYTES",
    "NETWORK_REPLAY_STORE_SCHEMA_VERSION",
    "NetworkReplayPersistenceClosedError",
    "NetworkReplayPersistenceConflictError",
    "NetworkReplayPersistenceError",
    "NetworkReplayPersistenceIntegrityError",
    "NetworkReplayPersistenceValidationError",
    "SQLiteNetworkReplayStore",
)
