from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import stat
import threading

import pytest

import game.shared_rate_limit as shared_rate_limit
from game.shared_rate_limit import (
    RateLimitBucket,
    RateLimitDecision,
    SQLiteSharedRateLimitStore,
    SharedRateLimitCapacityError,
    SharedRateLimitClosedError,
    SharedRateLimitIntegrityError,
    SharedRateLimitValidationError,
)


BASE_TIME = 1_800_000_000.0


def private_directory(tmp_path: Path, name: str = "rate-limit") -> Path:
    directory = tmp_path / name
    directory.mkdir(mode=0o700)
    os.chmod(directory, 0o700)
    return directory


def bucket(
    subject: str = "203.0.113.42",
    *,
    scope: str = "web.session.ip",
    maximum: int = 2,
) -> RateLimitBucket:
    return RateLimitBucket(scope, subject, maximum)


def consume(
    store: SQLiteSharedRateLimitStore,
    buckets=(bucket(),),
    *,
    now: float = BASE_TIME,
    window_seconds: float = 60,
) -> RateLimitDecision:
    return store.consume_many(
        buckets,
        now=now,
        window_seconds=window_seconds,
    )


def test_exact_window_persists_across_restart_and_expires_at_boundary(tmp_path):
    directory = private_directory(tmp_path)
    database = directory / "limits.sqlite3"
    first = SQLiteSharedRateLimitStore(database)
    one = (bucket(maximum=1),)

    assert consume(first, one).allowed is True
    first.close()

    restored = SQLiteSharedRateLimitStore(database)
    blocked = consume(restored, one, now=BASE_TIME + 59.001)
    assert blocked == RateLimitDecision(False, 0, 1)
    assert consume(restored, one, now=BASE_TIME + 60).allowed is True
    restored.close()

    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(restored.key_path.stat().st_mode) == 0o600


def test_retry_uses_exact_oldest_event_and_only_allows_at_boundary(tmp_path):
    store = SQLiteSharedRateLimitStore(private_directory(tmp_path) / "limits.sqlite3")
    two = (bucket(maximum=2),)

    assert consume(store, two, now=BASE_TIME, window_seconds=10).allowed
    assert consume(store, two, now=BASE_TIME + 1, window_seconds=10).allowed
    assert consume(store, two, now=BASE_TIME + 2, window_seconds=10) == (
        RateLimitDecision(False, 0, 8)
    )
    assert consume(store, two, now=BASE_TIME + 9.999, window_seconds=10) == (
        RateLimitDecision(False, 0, 1)
    )
    assert consume(store, two, now=BASE_TIME + 10, window_seconds=10).allowed
    store.close()


def test_multiple_buckets_are_all_or_none_and_longest_retry_is_reported(tmp_path):
    store = SQLiteSharedRateLimitStore(private_directory(tmp_path) / "limits.sqlite3")
    first = bucket("client-a", scope="web.message.session", maximum=1)
    second = bucket("room-a", scope="web.room.code", maximum=1)

    assert consume(store, (first,), now=BASE_TIME).allowed
    # Give the second bucket a newer accepted event, hence a longer retry.
    assert consume(store, (second,), now=BASE_TIME + 10).allowed
    blocked = consume(store, (first, second), now=BASE_TIME + 20)
    assert blocked == RateLimitDecision(False, 1, 50)

    # A denied group inserted no event into either bucket.  The second event
    # expires at +70 and can be consumed immediately at that boundary.
    assert consume(store, (second,), now=BASE_TIME + 70).allowed
    store.close()


def test_duplicate_canonical_bucket_is_rejected_before_writing(tmp_path):
    store = SQLiteSharedRateLimitStore(private_directory(tmp_path) / "limits.sqlite3")
    with pytest.raises(SharedRateLimitValidationError, match="duplicate"):
        consume(
            store,
            (
                bucket("192.0.2.7", maximum=2),
                bucket("::ffff:192.0.2.7", maximum=2),
            ),
        )

    assert consume(store, (bucket("192.0.2.7", maximum=1),)).allowed
    store.close()


def test_two_open_handles_atomically_share_one_capacity(tmp_path):
    database = private_directory(tmp_path) / "limits.sqlite3"
    first = SQLiteSharedRateLimitStore(database)
    second = SQLiteSharedRateLimitStore(database)
    shared_bucket = (bucket("concurrent-client", maximum=5),)
    barrier = threading.Barrier(12)

    def attempt(index: int) -> bool:
        barrier.wait(timeout=5)
        selected = first if index % 2 == 0 else second
        return consume(selected, shared_bucket).allowed

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = tuple(executor.map(attempt, range(12)))

    assert sum(outcomes) == 5
    assert outcomes.count(False) == 7
    first.close()
    second.close()


def test_capacity_error_rolls_back_the_complete_group(tmp_path):
    store = SQLiteSharedRateLimitStore(
        private_directory(tmp_path) / "limits.sqlite3",
        max_events=1,
    )
    first = bucket("a", scope="web.message.session", maximum=10)
    second = bucket("b", scope="web.message.session", maximum=10)

    with pytest.raises(SharedRateLimitCapacityError):
        consume(store, (first, second))
    assert consume(store, (first,)).allowed
    store.close()


def test_store_binds_one_window_across_handles_and_prunes_abandoned_subjects(
    tmp_path,
):
    database = private_directory(tmp_path) / "limits.sqlite3"
    first = SQLiteSharedRateLimitStore(database, max_events=2)
    second = SQLiteSharedRateLimitStore(database, max_events=2)

    assert consume(
        first,
        (bucket("abandoned-a", maximum=10),),
        now=BASE_TIME,
        window_seconds=60,
    ).allowed
    assert consume(
        second,
        (bucket("abandoned-b", maximum=10),),
        now=BASE_TIME,
        window_seconds=60,
    ).allowed
    with pytest.raises(SharedRateLimitValidationError, match="store configuration"):
        consume(
            second,
            (bucket("wrong-window", maximum=10),),
            now=BASE_TIME + 1,
            window_seconds=10,
        )

    # At the exact boundary, global pruning uses the bound 60-second window,
    # not the module's 24-hour validation maximum.  Abandoned subjects cannot
    # pin the two-row capacity for a day.
    assert consume(
        first,
        (bucket("fresh-client", maximum=10),),
        now=BASE_TIME + 60,
        window_seconds=60,
    ).allowed
    connection = sqlite3.connect(database)
    try:
        assert (
            connection.execute("SELECT COUNT(*) FROM rate_limit_event").fetchone()[0]
            == 1
        )
    finally:
        connection.close()
    first.close()
    second.close()


def test_raw_subjects_never_reach_database_key_or_repr(tmp_path):
    directory = private_directory(tmp_path)
    database = directory / "limits.sqlite3"
    raw_ip = "198.51.100.177"
    raw_token = "bearer-session-token-that-must-remain-private"
    store = SQLiteSharedRateLimitStore(database)
    buckets = (
        bucket(raw_ip, scope="web.message.ip", maximum=10),
        bucket(raw_token, scope="web.message.session", maximum=10),
    )

    assert raw_ip not in repr(buckets[0])
    assert raw_token not in repr(buckets[1])
    assert consume(store, buckets).allowed
    store.close()

    stored_bytes = b"".join(
        path.read_bytes()
        for path in directory.iterdir()
        if path.is_file() and not path.is_symlink()
    )
    assert raw_ip.encode("utf-8") not in stored_bytes
    assert raw_token.encode("utf-8") not in stored_bytes

    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT scope, subject_digest FROM rate_limit_event ORDER BY event_id"
        ).fetchall()
    finally:
        connection.close()
    assert [row[0] for row in rows] == [
        "web.message.ip",
        "web.message.session",
    ]
    assert all(type(row[1]) is bytes and len(row[1]) == 32 for row in rows)


def test_wrong_key_is_rejected_without_exposing_identity(tmp_path):
    directory = private_directory(tmp_path)
    database = directory / "limits.sqlite3"
    store = SQLiteSharedRateLimitStore(database)
    assert consume(store, (bucket("private-account-id", maximum=1),)).allowed
    store.close()

    wrong_key = directory / "wrong.key"
    wrong_key.write_bytes(b"x" * 32)
    os.chmod(wrong_key, 0o600)
    with pytest.raises(
        SharedRateLimitIntegrityError, match="key or integrity"
    ) as error:
        SQLiteSharedRateLimitStore(database, key_path=wrong_key)
    assert "private-account-id" not in str(error.value)


def test_schema_drift_is_rejected(tmp_path):
    directory = private_directory(tmp_path)
    database = directory / "limits.sqlite3"
    store = SQLiteSharedRateLimitStore(database)
    store.close()

    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE attacker_data (value TEXT)")
        connection.commit()
    finally:
        connection.close()
    os.chmod(database, 0o600)

    with pytest.raises(SharedRateLimitIntegrityError, match="schema"):
        SQLiteSharedRateLimitStore(database)


def test_database_key_and_parent_symlinks_are_rejected(tmp_path):
    directory = private_directory(tmp_path)
    target = directory / "target"
    target.write_bytes(b"not a database")
    os.chmod(target, 0o600)
    database_link = directory / "limits.sqlite3"
    database_link.symlink_to(target)

    with pytest.raises(SharedRateLimitIntegrityError, match="regular file"):
        SQLiteSharedRateLimitStore(database_link)

    key_directory = private_directory(tmp_path, "linked-key")
    key_target = key_directory / "target.key"
    key_target.write_bytes(b"k" * 32)
    os.chmod(key_target, 0o600)
    key_link = key_directory / "limits.key"
    key_link.symlink_to(key_target)
    with pytest.raises(SharedRateLimitIntegrityError, match="regular file"):
        SQLiteSharedRateLimitStore(
            key_directory / "limits.sqlite3",
            key_path=key_link,
        )

    real_parent = private_directory(tmp_path, "real-parent")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(SharedRateLimitIntegrityError, match="real directory"):
        SQLiteSharedRateLimitStore(linked_parent / "limits.sqlite3")


def test_unsafe_existing_permissions_are_rejected(tmp_path):
    directory = private_directory(tmp_path)
    database = directory / "limits.sqlite3"
    database.write_bytes(b"")
    os.chmod(database, 0o644)

    with pytest.raises(SharedRateLimitIntegrityError, match="0600"):
        SQLiteSharedRateLimitStore(database)

    key_directory = private_directory(tmp_path, "bad-key")
    key = key_directory / "limits.key"
    key.write_bytes(b"k" * 32)
    os.chmod(key, 0o644)
    with pytest.raises(SharedRateLimitIntegrityError, match="0600"):
        SQLiteSharedRateLimitStore(
            key_directory / "limits.sqlite3",
            key_path=key,
        )

    other_directory = private_directory(tmp_path, "bad-parent")
    os.chmod(other_directory, 0o755)
    with pytest.raises(SharedRateLimitIntegrityError, match="0700"):
        SQLiteSharedRateLimitStore(other_directory / "limits.sqlite3")


def test_small_clock_rollback_clamps_and_large_rollback_fails_closed(tmp_path):
    store = SQLiteSharedRateLimitStore(private_directory(tmp_path) / "limits.sqlite3")
    one = (bucket("clock-client", maximum=1),)
    assert consume(store, one, now=BASE_TIME).allowed

    clamped = consume(store, one, now=BASE_TIME - 1)
    assert clamped == RateLimitDecision(False, 0, 60)
    with pytest.raises(SharedRateLimitIntegrityError, match="clock moved backwards"):
        consume(store, one, now=BASE_TIME - 10)

    # The rejected observation did not clear or rewrite the accepted event.
    assert consume(store, one, now=BASE_TIME + 60).allowed
    store.close()


def test_closed_and_fork_inherited_handles_fail_safely(tmp_path, monkeypatch):
    store = SQLiteSharedRateLimitStore(private_directory(tmp_path) / "limits.sqlite3")
    original_pid = os.getpid()
    monkeypatch.setattr(shared_rate_limit.os, "getpid", lambda: original_pid + 1)

    with pytest.raises(SharedRateLimitClosedError, match="fork"):
        consume(store)
    with pytest.raises(SharedRateLimitClosedError, match="fork"):
        store.close()

    monkeypatch.undo()
    store.close()
    store.close()
    with pytest.raises(SharedRateLimitClosedError, match="closed"):
        consume(store)


@pytest.mark.parametrize(
    "buckets,now,window",
    [
        ((), BASE_TIME, 60),
        (("not-a-bucket",), BASE_TIME, 60),
        ((bucket(),), 100.0, 60),  # monotonic-looking, not absolute wall time
        ((bucket(),), float("nan"), 60),
        ((bucket(),), BASE_TIME, 0),
        ((bucket(),), BASE_TIME, 86_401),
    ],
)
def test_consume_input_is_strict_and_bounded(tmp_path, buckets, now, window):
    store = SQLiteSharedRateLimitStore(private_directory(tmp_path) / "limits.sqlite3")
    with pytest.raises(SharedRateLimitValidationError):
        consume(store, buckets, now=now, window_seconds=window)
    store.close()


@pytest.mark.parametrize(
    "scope,subject,maximum",
    [
        ("Uppercase", "subject", 1),
        ("web message", "subject", 1),
        ("web.message", " subject", 1),
        ("web.message", "subject\n", 1),
        ("web.message", "", 1),
        ("web.message", "subject", True),
        ("web.message", "subject", 0),
    ],
)
def test_bucket_input_is_strict(scope, subject, maximum):
    with pytest.raises(SharedRateLimitValidationError):
        RateLimitBucket(scope, subject, maximum)
