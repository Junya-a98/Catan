"""Durable, tamper-evident room-authority blobs for one server process.

This module deliberately knows nothing about lobbies, games, HTTP, or sockets.
Callers give it one complete JSON authority document per room and use the
generation number as a compare-and-swap cursor.  Keeping the whole authority
document in one SQLite row prevents game, RNG, lobby, and command cursors from
being committed independently in later controller integration.

The database contains private game state and credential digests.  It is kept
outside the Web static root with owner-only permissions.  A separate 256-bit
key authenticates both the database identity and every room row; the key is
never stored in SQLite.  HMAC provides integrity, not encryption at rest.

This is intentionally a single-process store.  A process-local re-entrant lock
serialises its shared SQLite connection, while generation CAS still detects an
unexpected writer.  Multi-process room ownership requires leases and fencing
and belongs to a later Internet-server stage.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
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
from typing import Any, Iterator
import uuid


SERVER_STATE_SCHEMA_VERSION = 1
SERVER_STATE_FORMAT = "catan-server-room-authority"
SERVER_STATE_KEY_BYTES = 32
SERVER_STATE_MAC_BYTES = hashlib.sha256().digest_size
MAX_AUTHORITY_JSON_BYTES = 8 * 1024 * 1024
MAX_AUTHORITY_DEPTH = 64
MAX_AUTHORITY_ITEMS = 250_000
MAX_AUTHORITY_CONTAINER_ITEMS = 100_000
MAX_AUTHORITY_STRING_CHARACTERS = 1_048_576
MAX_AUTHORITY_INTEGER = (1 << 63) - 1
MAX_STORED_ROOMS = 256
MAX_GENERATION = (1 << 63) - 1
MAX_TIMESTAMP_MS = 253_402_300_799_999

_ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_ROOM_CODE_PATTERN = re.compile(
    rf"[{re.escape(_ROOM_CODE_ALPHABET)}]{{6}}\Z"
)
_MAC_DOMAIN = b"catan-server-state/room/v1\0"
_KEY_CHECK_DOMAIN = b"catan-server-state/key-check/v1\0"
_META_SCHEMA_VERSION = "schema_version"
_META_KEY_CHECK = "key_check"
_EXPECTED_META_KEYS = frozenset({_META_SCHEMA_VERSION, _META_KEY_CHECK})

_CREATE_META_SQL = """
CREATE TABLE server_meta (
    key TEXT PRIMARY KEY NOT NULL,
    value BLOB NOT NULL
) WITHOUT ROWID
"""
_CREATE_ROOMS_SQL = """
CREATE TABLE room_authority (
    room_id TEXT PRIMARY KEY NOT NULL,
    room_code TEXT NOT NULL UNIQUE,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
    expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms > updated_at_ms),
    authority_json BLOB NOT NULL,
    mac BLOB NOT NULL CHECK (length(mac) = 32)
) WITHOUT ROWID
"""
_ROOM_COLUMNS = (
    "room_id, room_code, generation, updated_at_ms, expires_at_ms, "
    "authority_json, mac"
)
_EXPECTED_TABLE_INFO = {
    "server_meta": (
        (0, "key", "TEXT", 1, None, 1),
        (1, "value", "BLOB", 1, None, 0),
    ),
    "room_authority": (
        (0, "room_id", "TEXT", 1, None, 1),
        (1, "room_code", "TEXT", 1, None, 0),
        (2, "generation", "INTEGER", 1, None, 0),
        (3, "updated_at_ms", "INTEGER", 1, None, 0),
        (4, "expires_at_ms", "INTEGER", 1, None, 0),
        (5, "authority_json", "BLOB", 1, None, 0),
        (6, "mac", "BLOB", 1, None, 0),
    ),
}
_EXPECTED_INDEX_SIGNATURES = {
    "server_meta": frozenset({(True, "pk", False, ("key",))}),
    "room_authority": frozenset(
        {
            (True, "pk", False, ("room_id",)),
            (True, "u", False, ("room_code",)),
        }
    ),
}
_EXPECTED_TABLE_SQL = {
    "server_meta": _CREATE_META_SQL,
    "room_authority": _CREATE_ROOMS_SQL,
}


class ServerStateError(RuntimeError):
    """Base class for safe server-state failures."""


class ServerStateValidationError(ServerStateError, ValueError):
    """Raised when caller input is not a bounded canonical authority value."""


class ServerStateIntegrityError(ServerStateError):
    """Raised for a wrong key, tampering, corruption, or unsupported schema."""


class ServerStateConflictError(ServerStateError):
    """Raised when room identity or generation compare-and-swap does not match."""


class ServerStateClosedError(ServerStateError):
    """Raised after the store is closed or inherited across a process fork."""


@dataclass(frozen=True)
class RoomAuthorityMetadata:
    """Non-secret identity and lifecycle metadata for one stored room."""

    room_id: str
    room_code: str
    generation: int
    updated_at_ms: int
    expires_at_ms: int


@dataclass(frozen=True)
class RoomAuthorityRecord(RoomAuthorityMetadata):
    """A verified room document.

    ``repr`` deliberately omits the authority object because it may contain
    hidden tiles, hands, development cards, and credential digests.
    """

    authority: dict[str, Any] = field(repr=False, compare=True)

    @property
    def metadata(self) -> RoomAuthorityMetadata:
        return RoomAuthorityMetadata(
            room_id=self.room_id,
            room_code=self.room_code,
            generation=self.generation,
            updated_at_ms=self.updated_at_ms,
            expires_at_ms=self.expires_at_ms,
        )


class SQLiteRoomAuthorityStore:
    """Store complete room authority documents in a local SQLite database."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        key_path: str | Path | None = None,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        database = _validated_path(database_path, label="database path")
        key = (
            _validated_path(key_path, label="key path")
            if key_path is not None
            else database.with_suffix(database.suffix + ".key")
        )
        if os.path.abspath(database) == os.path.abspath(key):
            raise ServerStateValidationError(
                "database and authority key must use separate files"
            )
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or not 1 <= busy_timeout_ms <= 60_000
        ):
            raise ServerStateValidationError("busy_timeout_ms must be 1..60000")

        _prepare_private_directory(database.parent)
        _prepare_private_directory(key.parent)
        database_preexisted = database.exists()
        _reject_unsafe_existing_file(database, label="database")
        _reject_unsafe_existing_file(key, label="authority key")
        self.database_path = database
        self.key_path = key
        self._key = _load_or_create_key(
            key,
            database_preexisted=database_preexisted,
        )
        self._lock = threading.RLock()
        self._pid = os.getpid()
        self._closed = False
        self._connection: sqlite3.Connection | None = None

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
            self._verify_all_rows()
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

    def __enter__(self) -> SQLiteRoomAuthorityStore:
        self._require_open()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

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
                    raise ServerStateError(
                        "room authority store could not close cleanly"
                    ) from exc
            self._harden_storage_files()

    def create_room(
        self,
        *,
        room_code: str,
        authority: dict[str, Any],
        updated_at_ms: int,
        expires_at_ms: int,
        room_id: str | None = None,
    ) -> RoomAuthorityRecord:
        """Insert generation 1 for a new room and return the verified record."""

        code = _validated_room_code(room_code)
        identifier = _validated_room_id(room_id or str(uuid.uuid4()))
        updated, expires = _validated_times(updated_at_ms, expires_at_ms)
        payload = _canonical_authority_json(authority)
        generation = 1
        mac = _room_mac(
            self._key,
            room_id=identifier,
            room_code=code,
            generation=generation,
            updated_at_ms=updated,
            expires_at_ms=expires,
            payload=payload,
        )
        with self._lock, self._database_operation():
            connection = self._require_open()
            try:
                with self._transaction(connection):
                    count = connection.execute(
                        "SELECT COUNT(*) FROM room_authority"
                    ).fetchone()[0]
                    if count >= MAX_STORED_ROOMS:
                        raise ServerStateConflictError(
                            "room authority store has reached its room limit"
                        )
                    connection.execute(
                        """
                        INSERT INTO room_authority (
                            room_id, room_code, generation, updated_at_ms,
                            expires_at_ms, authority_json, mac
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            identifier,
                            code,
                            generation,
                            updated,
                            expires,
                            payload,
                            mac,
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise ServerStateConflictError(
                    "room identity is already present"
                ) from exc
        return RoomAuthorityRecord(
            room_id=identifier,
            room_code=code,
            generation=generation,
            updated_at_ms=updated,
            expires_at_ms=expires,
            authority=_parse_authority_json(payload),
        )

    def update_room(
        self,
        room_id: str,
        *,
        expected_generation: int,
        authority: dict[str, Any],
        updated_at_ms: int,
        expires_at_ms: int,
    ) -> RoomAuthorityRecord:
        """Atomically replace one room only if its generation still matches."""

        identifier = _validated_room_id(room_id)
        expected = _validated_generation(expected_generation)
        if expected >= MAX_GENERATION:
            raise ServerStateValidationError("room generation cannot advance")
        updated, expires = _validated_times(updated_at_ms, expires_at_ms)
        payload = _canonical_authority_json(authority)
        with self._lock, self._database_operation():
            connection = self._require_open()
            with self._transaction(connection):
                row = connection.execute(
                    f"SELECT {_ROOM_COLUMNS} FROM room_authority WHERE room_id = ?",
                    (identifier,),
                ).fetchone()
                if row is None:
                    raise ServerStateConflictError(
                        "room generation does not match"
                    )
                current = self._record_from_row(row)
                if current.generation != expected:
                    raise ServerStateConflictError(
                        "room generation does not match"
                    )
                generation = expected + 1
                mac = _room_mac(
                    self._key,
                    room_id=identifier,
                    room_code=current.room_code,
                    generation=generation,
                    updated_at_ms=updated,
                    expires_at_ms=expires,
                    payload=payload,
                )
                cursor = connection.execute(
                    """
                    UPDATE room_authority
                    SET generation = ?, updated_at_ms = ?, expires_at_ms = ?,
                        authority_json = ?, mac = ?
                    WHERE room_id = ? AND generation = ?
                    """,
                    (
                        generation,
                        updated,
                        expires,
                        payload,
                        mac,
                        identifier,
                        expected,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ServerStateConflictError(
                        "room generation does not match"
                    )
        return RoomAuthorityRecord(
            room_id=identifier,
            room_code=current.room_code,
            generation=generation,
            updated_at_ms=updated,
            expires_at_ms=expires,
            authority=_parse_authority_json(payload),
        )

    def get_room(self, room_id: str) -> RoomAuthorityRecord | None:
        identifier = _validated_room_id(room_id)
        with self._lock, self._database_operation():
            connection = self._require_open()
            row = connection.execute(
                f"SELECT {_ROOM_COLUMNS} FROM room_authority WHERE room_id = ?",
                (identifier,),
            ).fetchone()
            return None if row is None else self._record_from_row(row)

    def get_room_by_code(self, room_code: str) -> RoomAuthorityRecord | None:
        code = _validated_room_code(room_code)
        with self._lock, self._database_operation():
            connection = self._require_open()
            row = connection.execute(
                f"SELECT {_ROOM_COLUMNS} FROM room_authority WHERE room_code = ?",
                (code,),
            ).fetchone()
            return None if row is None else self._record_from_row(row)

    def list_rooms(self) -> tuple[RoomAuthorityMetadata, ...]:
        """List verified room metadata in stable public-code order."""

        with self._lock, self._database_operation():
            connection = self._require_open()
            rows = connection.execute(
                f"SELECT {_ROOM_COLUMNS} FROM room_authority ORDER BY room_code"
            ).fetchall()
            return tuple(self._record_from_row(row).metadata for row in rows)

    def delete_room(
        self,
        room_id: str,
        *,
        expected_generation: int | None = None,
    ) -> bool:
        """Delete one verified room, optionally guarded by generation CAS."""

        identifier = _validated_room_id(room_id)
        expected = (
            None
            if expected_generation is None
            else _validated_generation(expected_generation)
        )
        with self._lock, self._database_operation():
            connection = self._require_open()
            with self._transaction(connection):
                row = connection.execute(
                    f"SELECT {_ROOM_COLUMNS} FROM room_authority WHERE room_id = ?",
                    (identifier,),
                ).fetchone()
                if row is None:
                    return False
                current = self._record_from_row(row)
                if expected is not None and current.generation != expected:
                    raise ServerStateConflictError(
                        "room generation does not match"
                    )
                cursor = connection.execute(
                    "DELETE FROM room_authority WHERE room_id = ? AND generation = ?",
                    (identifier, current.generation),
                )
                if cursor.rowcount != 1:
                    raise ServerStateConflictError(
                        "room generation does not match"
                    )
                return True

    def delete_expired(self, now_ms: int) -> tuple[str, ...]:
        """Atomically verify and delete every room expired at ``now_ms``."""

        now = _validated_timestamp(now_ms, label="expiry cutoff")
        with self._lock, self._database_operation():
            connection = self._require_open()
            with self._transaction(connection):
                rows = connection.execute(
                    f"""
                    SELECT {_ROOM_COLUMNS}
                    FROM room_authority
                    WHERE expires_at_ms <= ?
                    ORDER BY room_code
                    """,
                    (now,),
                ).fetchall()
                records = tuple(self._record_from_row(row) for row in rows)
                if not records:
                    return ()
                identifiers = tuple(record.room_id for record in records)
                placeholders = ",".join("?" for _ in identifiers)
                cursor = connection.execute(
                    f"DELETE FROM room_authority WHERE room_id IN ({placeholders})",
                    identifiers,
                )
                if cursor.rowcount != len(identifiers):
                    raise ServerStateConflictError(
                        "expired room set changed during deletion"
                    )
                return identifiers

    def _configure_connection(self, busy_timeout_ms: int) -> None:
        connection = self._require_open()
        try:
            connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            journal_mode = connection.execute(
                "PRAGMA journal_mode = WAL"
            ).fetchone()[0]
            if str(journal_mode).lower() != "wal":
                raise ServerStateIntegrityError(
                    "room authority database could not enable WAL"
                )
            connection.execute("PRAGMA synchronous = FULL")
            synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
            if synchronous != 2:
                raise ServerStateIntegrityError(
                    "room authority database could not enable full sync"
                )
        except sqlite3.Error as exc:
            raise ServerStateIntegrityError(
                "room authority database could not be configured"
            ) from exc

    def _initialize_or_verify_schema(self) -> None:
        connection = self._require_open()
        try:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check is None or quick_check[0] != "ok":
                raise ServerStateIntegrityError(
                    "room authority database integrity check failed"
                )
            external_objects = tuple(
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT type, name, tbl_name FROM sqlite_schema
                    WHERE name NOT LIKE 'sqlite_%'
                    ORDER BY type, name
                    """
                )
            )
            if not external_objects:
                with self._transaction(connection):
                    connection.execute(_CREATE_META_SQL)
                    connection.execute(_CREATE_ROOMS_SQL)
                    connection.executemany(
                        "INSERT INTO server_meta (key, value) VALUES (?, ?)",
                        (
                            (
                                _META_SCHEMA_VERSION,
                                str(SERVER_STATE_SCHEMA_VERSION).encode("ascii"),
                            ),
                            (_META_KEY_CHECK, _key_check(self._key)),
                        ),
                    )
                self._verify_schema_shape()
                _fsync_directory(self.database_path.parent)
                return
            if external_objects != (
                ("table", "room_authority", "room_authority"),
                ("table", "server_meta", "server_meta"),
            ):
                raise ServerStateIntegrityError(
                    "room authority database schema is unsupported"
                )
            self._verify_schema_shape()
            metadata = {
                row["key"]: bytes(row["value"])
                for row in connection.execute(
                    "SELECT key, value FROM server_meta"
                )
            }
            if set(metadata) != _EXPECTED_META_KEYS:
                raise ServerStateIntegrityError(
                    "room authority database metadata is invalid"
                )
            if metadata[_META_SCHEMA_VERSION] != str(
                SERVER_STATE_SCHEMA_VERSION
            ).encode("ascii"):
                raise ServerStateIntegrityError(
                    "room authority database schema version is unsupported"
                )
            if not hmac.compare_digest(
                metadata[_META_KEY_CHECK],
                _key_check(self._key),
            ):
                raise ServerStateIntegrityError(
                    "room authority database key or integrity is invalid"
                )
        except ServerStateError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise ServerStateIntegrityError(
                "room authority database schema is invalid"
            ) from exc

    def _verify_schema_shape(self) -> None:
        """Reject column/index drift and every user-defined schema object."""

        connection = self._require_open()
        external_objects = tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT type, name, tbl_name
                FROM sqlite_schema
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        )
        if external_objects != (
            ("table", "room_authority", "room_authority"),
            ("table", "server_meta", "server_meta"),
        ):
            raise ServerStateIntegrityError(
                "room authority database contains unsupported schema objects"
            )
        for table_name, expected_columns in _EXPECTED_TABLE_INFO.items():
            schema_row = connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
            if (
                schema_row is None
                or _normalised_schema_sql(schema_row[0])
                != _normalised_schema_sql(_EXPECTED_TABLE_SQL[table_name])
            ):
                raise ServerStateIntegrityError(
                    "room authority database table definition is unsupported"
                )
            columns = tuple(
                tuple(row)
                for row in connection.execute(f'PRAGMA table_info("{table_name}")')
            )
            if columns != expected_columns:
                raise ServerStateIntegrityError(
                    "room authority database columns are unsupported"
                )
            signatures = set()
            for index_row in connection.execute(
                f'PRAGMA index_list("{table_name}")'
            ):
                index_name = index_row[1]
                quoted_index = str(index_name).replace('"', '""')
                index_columns = tuple(
                    row[2]
                    for row in connection.execute(
                        f'PRAGMA index_info("{quoted_index}")'
                    )
                )
                signatures.add(
                    (
                        index_row[2] == 1,
                        index_row[3],
                        index_row[4] == 1,
                        index_columns,
                    )
                )
            if frozenset(signatures) != _EXPECTED_INDEX_SIGNATURES[table_name]:
                raise ServerStateIntegrityError(
                    "room authority database indexes are unsupported"
                )

    def _verify_all_rows(self) -> None:
        connection = self._require_open()
        try:
            rows = connection.execute(
                f"SELECT {_ROOM_COLUMNS} FROM room_authority ORDER BY room_code"
            ).fetchall()
            if len(rows) > MAX_STORED_ROOMS:
                raise ServerStateIntegrityError(
                    "room authority database exceeds its room limit"
                )
            for row in rows:
                self._record_from_row(row)
        except ServerStateError:
            raise
        except sqlite3.Error as exc:
            raise ServerStateIntegrityError(
                "room authority rows could not be verified"
            ) from exc

    def _record_from_row(self, row: sqlite3.Row) -> RoomAuthorityRecord:
        try:
            identifier = _validated_room_id(row["room_id"])
            code = _validated_room_code(row["room_code"])
            generation = _validated_generation(row["generation"])
            updated, expires = _validated_times(
                row["updated_at_ms"],
                row["expires_at_ms"],
            )
            raw_payload = row["authority_json"]
            payload = (
                raw_payload.tobytes()
                if isinstance(raw_payload, memoryview)
                else raw_payload
            )
            raw_mac = row["mac"]
            mac = raw_mac.tobytes() if isinstance(raw_mac, memoryview) else raw_mac
            if (
                type(payload) is not bytes
                or not 2 <= len(payload) <= MAX_AUTHORITY_JSON_BYTES
                or type(mac) is not bytes
                or len(mac) != SERVER_STATE_MAC_BYTES
            ):
                raise ServerStateIntegrityError(
                    "room authority row has invalid binary fields"
                )
            expected_mac = _room_mac(
                self._key,
                room_id=identifier,
                room_code=code,
                generation=generation,
                updated_at_ms=updated,
                expires_at_ms=expires,
                payload=payload,
            )
            if not hmac.compare_digest(mac, expected_mac):
                raise ServerStateIntegrityError(
                    "room authority database key or integrity is invalid"
                )
            authority = _parse_authority_json(payload)
            return RoomAuthorityRecord(
                room_id=identifier,
                room_code=code,
                generation=generation,
                updated_at_ms=updated,
                expires_at_ms=expires,
                authority=authority,
            )
        except ServerStateError:
            raise
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ServerStateIntegrityError(
                "room authority row is invalid"
            ) from exc

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
        except ServerStateError:
            raise
        except sqlite3.Error as exc:
            raise ServerStateError(
                "room authority database operation failed"
            ) from exc

    def _require_open(self) -> sqlite3.Connection:
        if self._closed or self._connection is None:
            raise ServerStateClosedError("room authority store is closed")
        if os.getpid() != self._pid:
            raise ServerStateClosedError(
                "room authority store cannot be reused after process fork"
            )
        return self._connection

    def _harden_storage_files(self) -> None:
        _harden_file(self.database_path)
        _harden_file(self.key_path)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.database_path}{suffix}")
            if sidecar.exists():
                _harden_file(sidecar)


def _validated_path(value: str | Path | None, *, label: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise ServerStateValidationError(f"{label} must be a filesystem path")
    path = Path(value).expanduser()
    if not str(path) or path.name in {"", ".", ".."}:
        raise ServerStateValidationError(f"{label} must name a file")
    return path.absolute()


def _prepare_private_directory(path: Path) -> None:
    try:
        created = not path.exists()
        if created:
            path.mkdir(mode=0o700, parents=True, exist_ok=False)
        details = path.lstat()
        if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
            raise ServerStateValidationError(
                "room authority parent must be a real directory"
            )
        if hasattr(os, "getuid") and details.st_uid != os.getuid():
            raise ServerStateValidationError(
                "room authority parent must be owned by this user"
            )
        if stat.S_IMODE(details.st_mode) != 0o700:
            raise ServerStateValidationError(
                "room authority parent permissions must be 0700"
            )
    except ServerStateError:
        raise
    except OSError as exc:
        raise ServerStateError(
            "room authority private directory could not be prepared"
        ) from exc


def _reject_unsafe_existing_file(path: Path, *, label: str) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ServerStateError(f"{label} could not be inspected") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ServerStateValidationError(f"{label} must be a regular file")


def _load_or_create_key(path: Path, *, database_preexisted: bool) -> bytes:
    if not path.exists():
        if database_preexisted:
            raise ServerStateIntegrityError(
                "existing room authority database is missing its key"
            )
        material = secrets.token_bytes(SERVER_STATE_KEY_BYTES)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
            try:
                written = 0
                while written < len(material):
                    written += os.write(descriptor, material[written:])
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
            raise ServerStateError(
                "room authority key could not be created"
            ) from exc

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode):
                raise ServerStateValidationError(
                    "authority key must be a regular file"
                )
            material = os.read(descriptor, SERVER_STATE_KEY_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(material) != SERVER_STATE_KEY_BYTES:
            raise ServerStateIntegrityError(
                "room authority key has an invalid length"
            )
        os.chmod(path, 0o600)
        return material
    except ServerStateError:
        raise
    except OSError as exc:
        raise ServerStateError("room authority key could not be read") from exc


def _harden_file(path: Path) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ServerStateError(
            "room authority storage permissions could not be inspected"
        ) from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ServerStateIntegrityError(
            "room authority storage path is not a regular file"
        )
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise ServerStateError(
            "room authority storage permissions could not be hardened"
        ) from exc


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ServerStateError(
            "room authority directory could not be synchronized"
        ) from exc


def _validated_room_id(value: Any) -> str:
    if type(value) is not str:
        raise ServerStateValidationError("room_id must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ServerStateValidationError(
            "room_id must be a canonical UUID"
        ) from exc
    if str(parsed) != value or parsed.version != 4:
        raise ServerStateValidationError("room_id must be a canonical UUIDv4")
    return value


def _validated_room_code(value: Any) -> str:
    if type(value) is not str or _ROOM_CODE_PATTERN.fullmatch(value) is None:
        raise ServerStateValidationError("room_code is invalid")
    return value


def _validated_generation(value: Any) -> int:
    if (
        type(value) is not int
        or not 1 <= value <= MAX_GENERATION
    ):
        raise ServerStateValidationError("room generation is invalid")
    return value


def _validated_timestamp(value: Any, *, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_TIMESTAMP_MS:
        raise ServerStateValidationError(f"{label} is invalid")
    return value


def _validated_times(updated_at_ms: Any, expires_at_ms: Any) -> tuple[int, int]:
    updated = _validated_timestamp(updated_at_ms, label="updated_at_ms")
    expires = _validated_timestamp(expires_at_ms, label="expires_at_ms")
    if expires <= updated:
        raise ServerStateValidationError(
            "expires_at_ms must be later than updated_at_ms"
        )
    return updated, expires


def _canonical_authority_json(authority: Any) -> bytes:
    if type(authority) is not dict:
        raise ServerStateValidationError(
            "room authority must be a strict JSON object"
        )
    _validate_json_value(authority, depth=0, budget=[MAX_AUTHORITY_ITEMS], active=set())
    try:
        payload = json.dumps(
            authority,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ServerStateValidationError(
            "room authority is not valid JSON"
        ) from exc
    if not 2 <= len(payload) <= MAX_AUTHORITY_JSON_BYTES:
        raise ServerStateValidationError(
            "room authority JSON exceeds the size limit"
        )
    return payload


def _validate_json_value(
    value: Any,
    *,
    depth: int,
    budget: list[int],
    active: set[int],
) -> None:
    budget[0] -= 1
    if budget[0] < 0:
        raise ServerStateValidationError("room authority has too many items")
    if depth > MAX_AUTHORITY_DEPTH:
        raise ServerStateValidationError("room authority nesting is too deep")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > MAX_AUTHORITY_INTEGER:
            raise ServerStateValidationError(
                "room authority integer is out of range"
            )
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ServerStateValidationError(
                "room authority number must be finite"
            )
        return
    if type(value) is str:
        if len(value) > MAX_AUTHORITY_STRING_CHARACTERS:
            raise ServerStateValidationError(
                "room authority string is too long"
            )
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ServerStateValidationError(
                "room authority string is invalid Unicode"
            ) from exc
        return
    if type(value) not in (dict, list):
        raise ServerStateValidationError(
            "room authority contains a non-JSON value"
        )
    if len(value) > MAX_AUTHORITY_CONTAINER_ITEMS:
        raise ServerStateValidationError(
            "room authority container is too large"
        )
    identity = id(value)
    if identity in active:
        raise ServerStateValidationError("room authority contains a cycle")
    active.add(identity)
    try:
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise ServerStateValidationError(
                        "room authority object keys must be strings"
                    )
                _validate_json_value(
                    item,
                    depth=depth + 1,
                    budget=budget,
                    active=active,
                )
        else:
            for item in value:
                _validate_json_value(
                    item,
                    depth=depth + 1,
                    budget=budget,
                    active=active,
                )
    finally:
        active.remove(identity)


def _parse_authority_json(payload: bytes) -> dict[str, Any]:
    if not 2 <= len(payload) <= MAX_AUTHORITY_JSON_BYTES:
        raise ServerStateIntegrityError(
            "room authority JSON has an invalid size"
        )

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    def parse_integer(value: str) -> int:
        if len(value.lstrip("-")) > 19:
            raise ValueError("JSON integer is too long")
        result = int(value)
        if abs(result) > MAX_AUTHORITY_INTEGER:
            raise ValueError("JSON integer is out of range")
        return result

    def parse_number(value: str) -> float:
        if len(value) > 128:
            raise ValueError("JSON number is too long")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("JSON number must be finite")
        return result

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("utf-8", errors="strict"),
            parse_constant=reject_constant,
            parse_int=parse_integer,
            parse_float=parse_number,
            object_pairs_hook=unique_object,
        )
        if type(document) is not dict:
            raise ValueError("authority root is not an object")
        _validate_json_value(
            document,
            depth=0,
            budget=[MAX_AUTHORITY_ITEMS],
            active=set(),
        )
        if not hmac.compare_digest(_canonical_authority_json(document), payload):
            raise ValueError("authority JSON is not canonical")
        return document
    except ServerStateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ServerStateIntegrityError(
            "room authority JSON is invalid"
        ) from exc


def _room_mac(
    key: bytes,
    *,
    room_id: str,
    room_code: str,
    generation: int,
    updated_at_ms: int,
    expires_at_ms: int,
    payload: bytes,
) -> bytes:
    identifier = uuid.UUID(room_id).bytes
    code = room_code.encode("ascii")
    header = struct.pack(
        ">16s6sQQQQ",
        identifier,
        code,
        generation,
        updated_at_ms,
        expires_at_ms,
        len(payload),
    )
    return hmac.new(key, _MAC_DOMAIN + header + payload, hashlib.sha256).digest()


def _key_check(key: bytes) -> bytes:
    return hmac.new(key, _KEY_CHECK_DOMAIN, hashlib.sha256).digest()


def _normalised_schema_sql(value: Any) -> str:
    if type(value) is not str:
        raise ServerStateIntegrityError(
            "room authority database table definition is invalid"
        )
    return " ".join(value.casefold().split())


__all__ = (
    "MAX_AUTHORITY_JSON_BYTES",
    "MAX_STORED_ROOMS",
    "RoomAuthorityMetadata",
    "RoomAuthorityRecord",
    "SERVER_STATE_FORMAT",
    "SERVER_STATE_KEY_BYTES",
    "SERVER_STATE_SCHEMA_VERSION",
    "SQLiteRoomAuthorityStore",
    "ServerStateClosedError",
    "ServerStateConflictError",
    "ServerStateError",
    "ServerStateIntegrityError",
    "ServerStateValidationError",
)
