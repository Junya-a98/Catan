"""Durable exact rolling-window limits for the Web authority boundary.

The store deliberately persists neither IP addresses nor bearer/session values.
Each caller-provided subject is canonicalised and transformed with a keyed
HMAC-SHA256 domain that includes its scope.  SQLite therefore contains only a
public rule scope, an unlinkable 32-byte digest, and event timestamps.

One ``consume_many`` call is a single ``BEGIN IMMEDIATE`` transaction.  Either
every requested bucket receives one event or no bucket does, so HTTP and
WebSocket workers sharing the same file cannot oversubscribe a limit.  This is
appropriate for restart persistence and multiple processes on one host.  It is
not a multi-host/network-filesystem substitute for a service such as Redis.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
import ipaddress
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import threading
from typing import Iterator, Sequence
import unicodedata


SHARED_RATE_LIMIT_SCHEMA_VERSION = 1
SHARED_RATE_LIMIT_KEY_BYTES = 32
SHARED_RATE_LIMIT_DIGEST_BYTES = hashlib.sha256().digest_size
DEFAULT_MAX_RATE_LIMIT_EVENTS = 250_000
MAX_BUCKETS_PER_CONSUME = 32
MAX_RATE_LIMIT_EVENTS = 1_000_000
MAX_RATE_LIMIT_MAXIMUM = 100_000
MAX_RATE_LIMIT_SCOPE_CHARACTERS = 64
MAX_RATE_LIMIT_SUBJECT_CHARACTERS = 1_024
MAX_RATE_LIMIT_SUBJECT_UTF8_BYTES = 4_096
MIN_RATE_LIMIT_WINDOW_SECONDS = 1.0
MAX_RATE_LIMIT_WINDOW_SECONDS = 24 * 60 * 60.0
MAX_CLOCK_ROLLBACK_SECONDS = 5.0

# Absolute UTC milliseconds.  Rejecting tiny values prevents an accidental
# monotonic clock from producing a database that cannot survive restart.
MIN_TIMESTAMP_MS = 946_684_800_000  # 2000-01-01T00:00:00Z
MAX_TIMESTAMP_MS = 253_402_300_799_999  # 9999-12-31T23:59:59.999Z
_MAX_WINDOW_MS = math.ceil(MAX_RATE_LIMIT_WINDOW_SECONDS * 1_000)
_MAX_CLOCK_ROLLBACK_MS = math.ceil(MAX_CLOCK_ROLLBACK_SECONDS * 1_000)

_SCOPE_PATTERN = re.compile(r"[a-z][a-z0-9_.:-]{0,63}\Z")
_SUBJECT_MAC_DOMAIN = b"catan-shared-rate-limit/subject/v1\0"
_KEY_CHECK_DOMAIN = b"catan-shared-rate-limit/key-check/v1\0"
_META_SCHEMA_VERSION = "schema_version"
_META_KEY_CHECK = "key_check"
_META_LAST_CLOCK_MS = "last_clock_ms"
_META_WINDOW_MS = "window_ms"
_EXPECTED_META_KEYS = frozenset(
    {
        _META_SCHEMA_VERSION,
        _META_KEY_CHECK,
        _META_LAST_CLOCK_MS,
        _META_WINDOW_MS,
    }
)

_CREATE_META_SQL = """
CREATE TABLE rate_limit_meta (
    key TEXT PRIMARY KEY NOT NULL,
    value BLOB NOT NULL
) WITHOUT ROWID
"""
_CREATE_EVENT_SQL = f"""
CREATE TABLE rate_limit_event (
    event_id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL CHECK (length(scope) BETWEEN 1 AND {MAX_RATE_LIMIT_SCOPE_CHARACTERS}),
    subject_digest BLOB NOT NULL CHECK (length(subject_digest) = {SHARED_RATE_LIMIT_DIGEST_BYTES}),
    occurred_at_ms INTEGER NOT NULL CHECK (
        occurred_at_ms BETWEEN {MIN_TIMESTAMP_MS} AND {MAX_TIMESTAMP_MS}
    )
)
"""
_CREATE_BUCKET_INDEX_SQL = """
CREATE INDEX rate_limit_bucket_idx
ON rate_limit_event (scope, subject_digest, occurred_at_ms, event_id)
"""
_CREATE_TIME_INDEX_SQL = """
CREATE INDEX rate_limit_time_idx ON rate_limit_event (occurred_at_ms)
"""


class SharedRateLimitError(RuntimeError):
    """Base class for safe shared-rate-limit failures."""


class SharedRateLimitValidationError(SharedRateLimitError, ValueError):
    """Raised when a caller supplies an invalid or unbounded value."""


class SharedRateLimitIntegrityError(SharedRateLimitError):
    """Raised for a wrong key, unsafe file, corruption, or schema drift."""


class SharedRateLimitClosedError(SharedRateLimitError):
    """Raised after close or when an inherited handle is used after fork."""


class SharedRateLimitCapacityError(SharedRateLimitError):
    """Raised when the bounded persistent event store is full."""


@dataclass(frozen=True)
class RateLimitBucket:
    """One exact rolling-window counter request.

    ``subject`` is intentionally absent from ``repr`` because it can be an IP,
    account ID, room code, or bearer token.  It is never written to SQLite.
    """

    scope: str
    subject: str = field(repr=False)
    maximum: int

    def __post_init__(self) -> None:
        _validated_scope(self.scope)
        _canonical_subject(self.subject)
        if (
            isinstance(self.maximum, bool)
            or not isinstance(self.maximum, int)
            or not 1 <= self.maximum <= MAX_RATE_LIMIT_MAXIMUM
        ):
            raise SharedRateLimitValidationError(
                f"maximum must be 1..{MAX_RATE_LIMIT_MAXIMUM}"
            )


@dataclass(frozen=True)
class RateLimitDecision:
    """Result of atomically checking one ordered group of buckets."""

    allowed: bool
    blocked_index: int | None
    retry_after_seconds: int | None

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise SharedRateLimitValidationError("allowed must be a boolean")
        if self.allowed:
            if self.blocked_index is not None or self.retry_after_seconds is not None:
                raise SharedRateLimitValidationError(
                    "an allowed decision cannot contain block metadata"
                )
            return
        if (
            isinstance(self.blocked_index, bool)
            or not isinstance(self.blocked_index, int)
            or self.blocked_index < 0
        ):
            raise SharedRateLimitValidationError(
                "a blocked decision requires a non-negative bucket index"
            )
        if (
            isinstance(self.retry_after_seconds, bool)
            or not isinstance(self.retry_after_seconds, int)
            or self.retry_after_seconds < 1
        ):
            raise SharedRateLimitValidationError(
                "a blocked decision requires a positive retry duration"
            )


@dataclass(frozen=True)
class _PreparedBucket:
    index: int
    scope: str
    subject_digest: bytes
    maximum: int


class SQLiteSharedRateLimitStore:
    """Exact rolling-window events in a private, process-shareable SQLite DB."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        key_path: str | Path | None = None,
        busy_timeout_ms: int = 5_000,
        max_events: int = DEFAULT_MAX_RATE_LIMIT_EVENTS,
    ) -> None:
        database = _validated_path(database_path, label="database path")
        key = (
            _validated_path(key_path, label="key path")
            if key_path is not None
            else database.with_suffix(database.suffix + ".key")
        )
        if os.path.abspath(database) == os.path.abspath(key):
            raise SharedRateLimitValidationError(
                "database and rate-limit key must use separate files"
            )
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or not 1 <= busy_timeout_ms <= 60_000
        ):
            raise SharedRateLimitValidationError("busy_timeout_ms must be 1..60000")
        if (
            isinstance(max_events, bool)
            or not isinstance(max_events, int)
            or not 1 <= max_events <= MAX_RATE_LIMIT_EVENTS
        ):
            raise SharedRateLimitValidationError(
                f"max_events must be 1..{MAX_RATE_LIMIT_EVENTS}"
            )

        _prepare_private_directory(database.parent)
        _prepare_private_directory(key.parent)
        database_preexisted = database.exists()
        _reject_unsafe_existing_file(database, label="database")
        _reject_unsafe_existing_file(key, label="rate-limit key")
        for suffix in ("-wal", "-shm"):
            _reject_unsafe_existing_file(Path(f"{database}{suffix}"), label="sidecar")

        self.database_path = database
        self.key_path = key
        self.max_events = max_events
        self._key = _load_or_create_key(
            key,
            database_preexisted=database_preexisted,
        )
        if not database_preexisted:
            _create_private_file(database)

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
            self._verify_all_events()
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

    def __enter__(self) -> SQLiteSharedRateLimitStore:
        self._require_open()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if os.getpid() != self._pid:
                raise SharedRateLimitClosedError(
                    "rate-limit store cannot be reused after process fork"
                )
            self._closed = True
            connection, self._connection = self._connection, None
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error as exc:
                    raise SharedRateLimitError(
                        "rate-limit store could not close cleanly"
                    ) from exc
            self._harden_storage_files()

    def consume_many(
        self,
        buckets: Sequence[RateLimitBucket],
        *,
        now: float,
        window_seconds: float,
    ) -> RateLimitDecision:
        """Atomically consume one event from every bucket when all are below max.

        The input order is stable.  If several buckets are blocked, the result
        selects the one with the longest retry duration (then the lowest input
        index), so waiting the reported duration is sufficient for all buckets
        that were blocked by the same observed state.
        """

        timestamp_ms = _validated_now_ms(now)
        window_ms = _validated_window_ms(window_seconds)
        prepared = self._prepare_buckets(buckets)
        with self._lock, self._database_operation():
            connection = self._require_open()
            with self._transaction(connection):
                effective_now_ms = self._advance_clock(connection, timestamp_ms)
                bound_window_ms = self._bind_window(connection, window_ms)
                connection.execute(
                    "DELETE FROM rate_limit_event WHERE occurred_at_ms <= ?",
                    (effective_now_ms - bound_window_ms,),
                )
                blocked: list[tuple[int, int]] = []
                cutoff_ms = effective_now_ms - window_ms
                for bucket in prepared:
                    row = connection.execute(
                        """
                        SELECT COUNT(*) AS event_count,
                               MIN(occurred_at_ms) AS oldest_ms
                        FROM rate_limit_event
                        WHERE scope = ? AND subject_digest = ?
                              AND occurred_at_ms > ?
                        """,
                        (bucket.scope, bucket.subject_digest, cutoff_ms),
                    ).fetchone()
                    if row is None:
                        raise SharedRateLimitIntegrityError(
                            "rate-limit bucket could not be read"
                        )
                    count = row["event_count"]
                    oldest_ms = row["oldest_ms"]
                    if (
                        isinstance(count, bool)
                        or not isinstance(count, int)
                        or count < 0
                    ):
                        raise SharedRateLimitIntegrityError(
                            "rate-limit bucket count is invalid"
                        )
                    if count < bucket.maximum:
                        continue
                    if (
                        isinstance(oldest_ms, bool)
                        or not isinstance(oldest_ms, int)
                        or not MIN_TIMESTAMP_MS <= oldest_ms <= MAX_TIMESTAMP_MS
                    ):
                        raise SharedRateLimitIntegrityError(
                            "rate-limit bucket timestamp is invalid"
                        )
                    retry_ms = max(1, oldest_ms + window_ms - effective_now_ms)
                    retry_seconds = max(1, math.ceil(retry_ms / 1_000))
                    blocked.append((bucket.index, retry_seconds))

                if blocked:
                    blocked_index, retry_after = min(
                        blocked,
                        key=lambda item: (-item[1], item[0]),
                    )
                    return RateLimitDecision(False, blocked_index, retry_after)

                count_row = connection.execute(
                    "SELECT COUNT(*) AS event_count FROM rate_limit_event"
                ).fetchone()
                if count_row is None or type(count_row["event_count"]) is not int:
                    raise SharedRateLimitIntegrityError(
                        "rate-limit event count is invalid"
                    )
                if count_row["event_count"] + len(prepared) > self.max_events:
                    raise SharedRateLimitCapacityError(
                        "rate-limit event capacity has been reached"
                    )
                connection.executemany(
                    """
                    INSERT INTO rate_limit_event (
                        scope, subject_digest, occurred_at_ms
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        (bucket.scope, bucket.subject_digest, effective_now_ms)
                        for bucket in prepared
                    ),
                )
                return RateLimitDecision(True, None, None)

    def _prepare_buckets(
        self,
        buckets: Sequence[RateLimitBucket],
    ) -> tuple[_PreparedBucket, ...]:
        if isinstance(buckets, (str, bytes, bytearray)) or not isinstance(
            buckets, Sequence
        ):
            raise SharedRateLimitValidationError("buckets must be a sequence")
        if not 1 <= len(buckets) <= MAX_BUCKETS_PER_CONSUME:
            raise SharedRateLimitValidationError(
                f"buckets must contain 1..{MAX_BUCKETS_PER_CONSUME} entries"
            )
        prepared: list[_PreparedBucket] = []
        identities: set[tuple[str, bytes]] = set()
        for index, bucket in enumerate(buckets):
            if not isinstance(bucket, RateLimitBucket):
                raise SharedRateLimitValidationError(
                    "every bucket must be a RateLimitBucket"
                )
            scope = _validated_scope(bucket.scope)
            subject = _canonical_subject(bucket.subject)
            digest = _subject_digest(self._key, scope=scope, subject=subject)
            identity = (scope, digest)
            if identity in identities:
                raise SharedRateLimitValidationError(
                    "duplicate scope and subject buckets are not allowed"
                )
            identities.add(identity)
            prepared.append(_PreparedBucket(index, scope, digest, bucket.maximum))
        return tuple(prepared)

    @staticmethod
    def _bind_window(connection: sqlite3.Connection, requested_ms: int) -> int:
        """Bind one DB to the gateway's single shared rolling-window length."""

        row = connection.execute(
            "SELECT value FROM rate_limit_meta WHERE key = ?",
            (_META_WINDOW_MS,),
        ).fetchone()
        if row is None:
            raise SharedRateLimitIntegrityError("rate-limit window metadata is missing")
        stored_ms = _parse_meta_window(row["value"])
        if stored_ms == 0:
            connection.execute(
                "UPDATE rate_limit_meta SET value = ? WHERE key = ?",
                (str(requested_ms).encode("ascii"), _META_WINDOW_MS),
            )
            return requested_ms
        if stored_ms != requested_ms:
            raise SharedRateLimitValidationError(
                "window_seconds does not match the persistent store configuration"
            )
        return stored_ms

    def _advance_clock(
        self,
        connection: sqlite3.Connection,
        observed_ms: int,
    ) -> int:
        row = connection.execute(
            "SELECT value FROM rate_limit_meta WHERE key = ?",
            (_META_LAST_CLOCK_MS,),
        ).fetchone()
        if row is None:
            raise SharedRateLimitIntegrityError("rate-limit clock metadata is missing")
        previous_ms = _parse_meta_timestamp(row["value"])
        if observed_ms < previous_ms - _MAX_CLOCK_ROLLBACK_MS:
            raise SharedRateLimitIntegrityError(
                "system clock moved backwards beyond the safe tolerance"
            )
        effective_ms = max(observed_ms, previous_ms)
        connection.execute(
            "UPDATE rate_limit_meta SET value = ? WHERE key = ?",
            (str(effective_ms).encode("ascii"), _META_LAST_CLOCK_MS),
        )
        return effective_ms

    def _configure_connection(self, busy_timeout_ms: int) -> None:
        connection = self._require_open()
        try:
            connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(journal_mode).lower() != "wal":
                raise SharedRateLimitIntegrityError(
                    "rate-limit database could not enable WAL"
                )
            connection.execute("PRAGMA synchronous = FULL")
            if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise SharedRateLimitIntegrityError(
                    "rate-limit database could not enable full sync"
                )
        except sqlite3.Error as exc:
            raise SharedRateLimitIntegrityError(
                "rate-limit database could not be configured"
            ) from exc

    def _initialize_or_verify_schema(self) -> None:
        connection = self._require_open()
        try:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check is None or quick_check[0] != "ok":
                raise SharedRateLimitIntegrityError(
                    "rate-limit database integrity check failed"
                )
            objects = self._schema_objects(connection)
            if not objects:
                with self._transaction(connection):
                    connection.execute(_CREATE_META_SQL)
                    connection.execute(_CREATE_EVENT_SQL)
                    connection.execute(_CREATE_BUCKET_INDEX_SQL)
                    connection.execute(_CREATE_TIME_INDEX_SQL)
                    connection.executemany(
                        "INSERT INTO rate_limit_meta (key, value) VALUES (?, ?)",
                        (
                            (
                                _META_SCHEMA_VERSION,
                                str(SHARED_RATE_LIMIT_SCHEMA_VERSION).encode("ascii"),
                            ),
                            (_META_KEY_CHECK, _key_check(self._key)),
                            (_META_LAST_CLOCK_MS, b"0"),
                            (_META_WINDOW_MS, b"0"),
                        ),
                    )
                self._verify_schema_shape(connection)
                _fsync_directory(self.database_path.parent)
                return
            self._verify_schema_shape(connection)
            metadata = {
                row["key"]: _strict_blob(row["value"])
                for row in connection.execute("SELECT key, value FROM rate_limit_meta")
            }
            if set(metadata) != _EXPECTED_META_KEYS:
                raise SharedRateLimitIntegrityError(
                    "rate-limit database metadata is invalid"
                )
            if metadata[_META_SCHEMA_VERSION] != str(
                SHARED_RATE_LIMIT_SCHEMA_VERSION
            ).encode("ascii"):
                raise SharedRateLimitIntegrityError(
                    "rate-limit database schema version is unsupported"
                )
            if not hmac.compare_digest(
                metadata[_META_KEY_CHECK],
                _key_check(self._key),
            ):
                raise SharedRateLimitIntegrityError(
                    "rate-limit database key or integrity is invalid"
                )
            _parse_meta_timestamp(metadata[_META_LAST_CLOCK_MS])
            _parse_meta_window(metadata[_META_WINDOW_MS])
        except SharedRateLimitError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise SharedRateLimitIntegrityError(
                "rate-limit database schema is invalid"
            ) from exc

    @staticmethod
    def _schema_objects(
        connection: sqlite3.Connection,
    ) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT type, name, tbl_name FROM sqlite_schema
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        )

    def _verify_schema_shape(self, connection: sqlite3.Connection) -> None:
        expected_objects = (
            ("index", "rate_limit_bucket_idx", "rate_limit_event"),
            ("index", "rate_limit_time_idx", "rate_limit_event"),
            ("table", "rate_limit_event", "rate_limit_event"),
            ("table", "rate_limit_meta", "rate_limit_meta"),
        )
        if self._schema_objects(connection) != expected_objects:
            raise SharedRateLimitIntegrityError(
                "rate-limit database contains unsupported schema objects"
            )
        expected_sql = {
            "rate_limit_meta": _CREATE_META_SQL,
            "rate_limit_event": _CREATE_EVENT_SQL,
            "rate_limit_bucket_idx": _CREATE_BUCKET_INDEX_SQL,
            "rate_limit_time_idx": _CREATE_TIME_INDEX_SQL,
        }
        for object_name, definition in expected_sql.items():
            row = connection.execute(
                "SELECT sql FROM sqlite_schema WHERE name = ?",
                (object_name,),
            ).fetchone()
            if (
                row is None
                or type(row["sql"]) is not str
                or _normalised_schema_sql(row["sql"])
                != _normalised_schema_sql(definition)
            ):
                raise SharedRateLimitIntegrityError(
                    "rate-limit database schema definition is unsupported"
                )

    def _verify_all_events(self) -> None:
        connection = self._require_open()
        try:
            rows = connection.execute(
                """
                SELECT event_id, scope, subject_digest, occurred_at_ms
                FROM rate_limit_event ORDER BY event_id
                """
            ).fetchall()
            if len(rows) > self.max_events:
                raise SharedRateLimitIntegrityError(
                    "rate-limit database exceeds its event capacity"
                )
            for row in rows:
                if type(row["event_id"]) is not int or row["event_id"] < 1:
                    raise SharedRateLimitIntegrityError(
                        "rate-limit event identity is invalid"
                    )
                _validated_scope(row["scope"])
                digest = _strict_blob(row["subject_digest"])
                if len(digest) != SHARED_RATE_LIMIT_DIGEST_BYTES:
                    raise SharedRateLimitIntegrityError(
                        "rate-limit subject digest is invalid"
                    )
                timestamp = row["occurred_at_ms"]
                if (
                    type(timestamp) is not int
                    or not MIN_TIMESTAMP_MS <= timestamp <= MAX_TIMESTAMP_MS
                ):
                    raise SharedRateLimitIntegrityError(
                        "rate-limit event timestamp is invalid"
                    )
        except SharedRateLimitError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise SharedRateLimitIntegrityError(
                "rate-limit database events could not be verified"
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
        except SharedRateLimitError:
            raise
        except sqlite3.Error as exc:
            raise SharedRateLimitError("rate-limit database operation failed") from exc

    def _require_open(self) -> sqlite3.Connection:
        if self._closed or self._connection is None:
            raise SharedRateLimitClosedError("rate-limit store is closed")
        if os.getpid() != self._pid:
            raise SharedRateLimitClosedError(
                "rate-limit store cannot be reused after process fork"
            )
        return self._connection

    def _harden_storage_files(self) -> None:
        _harden_file(self.database_path)
        _harden_file(self.key_path)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.database_path}{suffix}")
            if sidecar.exists():
                _harden_file(sidecar)


def _validated_scope(value: object) -> str:
    if type(value) is not str or _SCOPE_PATTERN.fullmatch(value) is None:
        raise SharedRateLimitValidationError("scope must be canonical lowercase ASCII")
    return value


def _canonical_subject(value: object) -> str:
    if type(value) is not str or not value:
        raise SharedRateLimitValidationError("subject must be a non-empty string")
    if value != value.strip() or any(
        unicodedata.category(character) == "Cc" for character in value
    ):
        raise SharedRateLimitValidationError(
            "subject must not contain surrounding whitespace or control characters"
        )
    canonical = unicodedata.normalize("NFC", value)
    if len(canonical) > MAX_RATE_LIMIT_SUBJECT_CHARACTERS:
        raise SharedRateLimitValidationError("subject exceeds the character limit")
    try:
        encoded = canonical.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise SharedRateLimitValidationError("subject must be valid Unicode") from exc
    if len(encoded) > MAX_RATE_LIMIT_SUBJECT_UTF8_BYTES:
        raise SharedRateLimitValidationError("subject exceeds the UTF-8 byte limit")

    address_candidate = canonical
    if "%" in canonical:
        base, _separator, zone = canonical.partition("%")
        if zone:
            try:
                ipaddress.ip_address(base)
            except ValueError:
                pass
            else:
                address_candidate = base
    try:
        address = ipaddress.ip_address(address_candidate)
    except ValueError:
        return canonical
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.compressed.lower()


def _subject_digest(key: bytes, *, scope: str, subject: str) -> bytes:
    scope_bytes = scope.encode("ascii")
    subject_bytes = subject.encode("utf-8")
    payload = (
        _SUBJECT_MAC_DOMAIN
        + len(scope_bytes).to_bytes(2, "big")
        + scope_bytes
        + len(subject_bytes).to_bytes(4, "big")
        + subject_bytes
    )
    return hmac.new(key, payload, hashlib.sha256).digest()


def _validated_now_ms(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SharedRateLimitValidationError("now must be an absolute Unix timestamp")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise SharedRateLimitValidationError("now must be finite")
    milliseconds = math.floor(numeric * 1_000)
    if not MIN_TIMESTAMP_MS <= milliseconds <= MAX_TIMESTAMP_MS:
        raise SharedRateLimitValidationError(
            "now must be an absolute Unix timestamp in the supported range"
        )
    return milliseconds


def _validated_window_ms(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SharedRateLimitValidationError("window_seconds must be numeric")
    numeric = float(value)
    if (
        not math.isfinite(numeric)
        or not MIN_RATE_LIMIT_WINDOW_SECONDS <= numeric <= MAX_RATE_LIMIT_WINDOW_SECONDS
    ):
        raise SharedRateLimitValidationError(
            "window_seconds is outside the supported range"
        )
    return math.ceil(numeric * 1_000)


def _validated_path(value: str | Path | None, *, label: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise SharedRateLimitValidationError(f"{label} must be a filesystem path")
    path = Path(value).expanduser()
    if not str(path) or path.name in {"", ".", ".."}:
        raise SharedRateLimitValidationError(f"{label} must name a file")
    return path.absolute()


def _prepare_private_directory(path: Path) -> None:
    try:
        if not path.exists():
            path.mkdir(mode=0o700, parents=True, exist_ok=False)
        details = path.lstat()
        if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
            raise SharedRateLimitIntegrityError(
                "rate-limit parent must be a real directory"
            )
        if hasattr(os, "getuid") and details.st_uid != os.getuid():
            raise SharedRateLimitIntegrityError(
                "rate-limit parent must be owned by this user"
            )
        if stat.S_IMODE(details.st_mode) != 0o700:
            raise SharedRateLimitIntegrityError(
                "rate-limit parent permissions must be 0700"
            )
    except SharedRateLimitError:
        raise
    except OSError as exc:
        raise SharedRateLimitError(
            "rate-limit private directory could not be prepared"
        ) from exc


def _reject_unsafe_existing_file(path: Path, *, label: str) -> None:
    try:
        if not path.exists() and not path.is_symlink():
            return
        details = path.lstat()
        if not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode):
            raise SharedRateLimitIntegrityError(
                f"existing {label} must be a regular file"
            )
        if hasattr(os, "getuid") and details.st_uid != os.getuid():
            raise SharedRateLimitIntegrityError(
                f"existing {label} must be owned by this user"
            )
        if stat.S_IMODE(details.st_mode) != 0o600:
            raise SharedRateLimitIntegrityError(
                f"existing {label} permissions must be 0600"
            )
    except SharedRateLimitError:
        raise
    except OSError as exc:
        raise SharedRateLimitError(f"existing {label} could not be inspected") from exc


def _create_private_file(path: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        os.close(descriptor)
        _harden_file(path)
        _fsync_directory(path.parent)
    except FileExistsError as exc:
        raise SharedRateLimitIntegrityError(
            "rate-limit file appeared during creation"
        ) from exc
    except OSError as exc:
        raise SharedRateLimitError("rate-limit file could not be created") from exc


def _load_or_create_key(path: Path, *, database_preexisted: bool) -> bytes:
    if not path.exists():
        if database_preexisted:
            raise SharedRateLimitIntegrityError(
                "rate-limit key is missing for an existing database"
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        key = secrets.token_bytes(SHARED_RATE_LIMIT_KEY_BYTES)
        try:
            descriptor = os.open(path, flags, 0o600)
            try:
                written = os.write(descriptor, key)
                if written != len(key):
                    raise OSError("short key write")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _harden_file(path)
            _fsync_directory(path.parent)
            return key
        except FileExistsError as exc:
            raise SharedRateLimitIntegrityError(
                "rate-limit key appeared during creation"
            ) from exc
        except OSError as exc:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise SharedRateLimitError("rate-limit key could not be created") from exc

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode):
                raise SharedRateLimitIntegrityError(
                    "rate-limit key must be a regular file"
                )
            key = os.read(descriptor, SHARED_RATE_LIMIT_KEY_BYTES + 1)
        finally:
            os.close(descriptor)
    except SharedRateLimitError:
        raise
    except OSError as exc:
        raise SharedRateLimitError("rate-limit key could not be read") from exc
    if len(key) != SHARED_RATE_LIMIT_KEY_BYTES:
        raise SharedRateLimitIntegrityError("rate-limit key has an invalid length")
    return key


def _harden_file(path: Path) -> None:
    try:
        details = path.lstat()
        if not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode):
            raise SharedRateLimitIntegrityError(
                "rate-limit storage must remain a regular file"
            )
        if hasattr(os, "getuid") and details.st_uid != os.getuid():
            raise SharedRateLimitIntegrityError(
                "rate-limit storage must remain owned by this user"
            )
        os.chmod(path, 0o600, follow_symlinks=False)
    except SharedRateLimitError:
        raise
    except OSError as exc:
        raise SharedRateLimitError("rate-limit storage could not be secured") from exc


def _key_check(key: bytes) -> bytes:
    return hmac.new(key, _KEY_CHECK_DOMAIN, hashlib.sha256).digest()


def _strict_blob(value: object) -> bytes:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if type(value) is not bytes:
        raise SharedRateLimitIntegrityError(
            "rate-limit database contains a non-binary value"
        )
    return value


def _parse_meta_timestamp(value: object) -> int:
    raw = _strict_blob(value)
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SharedRateLimitIntegrityError(
            "rate-limit clock metadata is invalid"
        ) from exc
    if not text.isdigit() or (len(text) > 1 and text.startswith("0")):
        raise SharedRateLimitIntegrityError("rate-limit clock metadata is invalid")
    timestamp = int(text)
    if timestamp != 0 and not MIN_TIMESTAMP_MS <= timestamp <= MAX_TIMESTAMP_MS:
        raise SharedRateLimitIntegrityError("rate-limit clock metadata is out of range")
    return timestamp


def _parse_meta_window(value: object) -> int:
    raw = _strict_blob(value)
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SharedRateLimitIntegrityError(
            "rate-limit window metadata is invalid"
        ) from exc
    if not text.isdigit() or (len(text) > 1 and text.startswith("0")):
        raise SharedRateLimitIntegrityError("rate-limit window metadata is invalid")
    window_ms = int(text)
    if window_ms != 0 and not (
        math.ceil(MIN_RATE_LIMIT_WINDOW_SECONDS * 1_000) <= window_ms <= _MAX_WINDOW_MS
    ):
        raise SharedRateLimitIntegrityError(
            "rate-limit window metadata is out of range"
        )
    return window_ms


def _normalised_schema_sql(value: str) -> str:
    return " ".join(value.replace("\n", " ").split()).strip().lower()


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise SharedRateLimitError(
            "rate-limit directory could not be synchronized"
        ) from exc


__all__ = (
    "DEFAULT_MAX_RATE_LIMIT_EVENTS",
    "MAX_BUCKETS_PER_CONSUME",
    "MAX_CLOCK_ROLLBACK_SECONDS",
    "MAX_RATE_LIMIT_EVENTS",
    "MAX_RATE_LIMIT_MAXIMUM",
    "MAX_RATE_LIMIT_WINDOW_SECONDS",
    "MIN_RATE_LIMIT_WINDOW_SECONDS",
    "RateLimitBucket",
    "RateLimitDecision",
    "SHARED_RATE_LIMIT_SCHEMA_VERSION",
    "SQLiteSharedRateLimitStore",
    "SharedRateLimitCapacityError",
    "SharedRateLimitClosedError",
    "SharedRateLimitError",
    "SharedRateLimitIntegrityError",
    "SharedRateLimitValidationError",
)
