"""Bounded, viewer-safe match history for authoritative network games.

The store is deliberately independent of HTTP, WebSocket, and TCP.  A server
captures an authoritative game after each committed revision and later asks
for a frame using the viewer seat from its authenticated session.  Snapshot
filtering is delegated to :func:`game.network_protocol.build_state_snapshot`,
so replay privacy cannot drift away from live-network privacy.

The archive is in-memory and read-only to clients.  It is intended to back the
browser result/replay UI; persisted local replays remain the responsibility of
``game.replay``.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
import re
import threading
import time
from typing import Any

from game.ai_personality import AI_PERSONALITY_PROFILES, MIXED
from game.match_result import (
    MATCH_RESULT_FORMAT,
    MATCH_RESULT_VERSION,
    build_match_result,
)


NETWORK_REPLAY_FORMAT = "catan-network-replay"
NETWORK_REPLAY_VERSION = 1
NETWORK_REPLAY_AUTHORITY_FORMAT = "catan-network-replay-authority"
NETWORK_REPLAY_AUTHORITY_VERSION = 1
DEFAULT_MAX_NETWORK_REPLAY_FRAMES = 512
MAX_NETWORK_REPLAY_FRAMES = 5_000
DEFAULT_MAX_NETWORK_REPLAY_ROOMS = 64
MAX_NETWORK_REPLAY_ROOMS = 256
MAX_NETWORK_REPLAY_VIEWERS = 16
MAX_NETWORK_REPLAY_LABEL_LENGTH = 160
MAX_NETWORK_REPLAY_REVISION = (1 << 63) - 1
MAX_NETWORK_REPLAY_ELAPSED_MS = 10 * 366 * 24 * 60 * 60 * 1_000
MAX_NETWORK_REPLAY_METRIC_REVISIONS = 100_000
MAX_NETWORK_REPLAY_AUTHORITY_BYTES = 256 * 1024 * 1024

_AUTHORITY_KEYS = frozenset(
    {
        "format",
        "version",
        "room_code",
        "truncated",
        "frames",
        "event_revisions",
        "checkpoint_revisions",
        "final_result",
    }
)
_AUTHORITY_FRAME_KEYS = frozenset({"revision", "elapsed_ms", "label", "snapshots"})
_AUTHORITY_SNAPSHOT_KEYS = frozenset({"viewer_player_index", "snapshot"})
_AUTHORITY_METRIC_REVISION_KEYS = frozenset({"sequence", "revision"})
_ARCHIVED_SNAPSHOT_KEYS = frozenset(
    {
        "type",
        "protocol_version",
        "revision",
        "viewer_player_index",
        "board_manifest",
        "command_options",
        "state",
    }
)
_PUBLIC_SNAPSHOT_STATE_FIELDS = frozenset(
    {
        "format",
        "version",
        "rules",
        "variant_state",
        "match_metrics",
        "board",
        "players",
        "bank",
        "development_deck",
        "phase",
        "initial",
        "special",
        "domestic_trade",
        "history",
        "ai",
        "ui",
    }
)
_PUBLIC_PLAYER_FIELDS = frozenset(
    {
        "name",
        "color",
        "is_ai",
        "ai_personality",
        "piece_pattern",
        "marker",
        "resources",
        "resource_total",
        "roads_remaining",
        "settlements_remaining",
        "cities_remaining",
        "played_knights",
        "victory_point_cards",
        "development_cards",
        "new_development_cards",
        "development_card_total",
    }
)

_ROOM_CODE_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_PRIVATE_PLAYER_FIELDS = (
    "resources",
    "development_cards",
    "new_development_cards",
    "victory_point_cards",
)
_AUTHORITY_ONLY_PLAYER_FIELDS = ("resource_ledger",)


def _mixed_ai_identity_patterns(
    players: Sequence[Any],
) -> tuple[re.Pattern[str], ...]:
    aliases = sorted(
        {
            value
            for profile in AI_PERSONALITY_PROFILES.values()
            for value in (profile.key, profile.label)
            if value
        },
        key=len,
        reverse=True,
    )
    alias_pattern = "|".join(re.escape(alias) for alias in aliases)
    return tuple(
        re.compile(
            rf"{re.escape(name)}(?:（|\()\s*(?:{alias_pattern})(?:AI)?\s*(?:）|\))"
        )
        for player in players
        if isinstance(player, Mapping)
        and player.get("is_ai")
        and isinstance((name := player.get("name")), str)
        and name
    )


def _contains_mixed_ai_identity(
    value: Any,
    patterns: Sequence[re.Pattern[str]],
) -> bool:
    if isinstance(value, str):
        return any(pattern.search(value) is not None for pattern in patterns)
    if isinstance(value, Mapping):
        return any(
            _contains_mixed_ai_identity(item, patterns) for item in value.values()
        )
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return any(_contains_mixed_ai_identity(item, patterns) for item in value)
    return False


_PUBLIC_VARIANT_STATE_FIELDS = frozenset(
    {"format", "version", "kind", "config_fingerprint", "public"}
)
_PUBLIC_RESULT_FIELDS = frozenset(
    {
        "format",
        "version",
        "source",
        "completed",
        "board",
        "victory_target",
        "winner",
        "standings",
        "vp_progression",
        "timeline_unit",
        "important_events",
        "replay",
    }
)
_PUBLIC_STANDING_FIELDS = frozenset(
    {
        "rank",
        "seat",
        "name",
        "color",
        "is_ai",
        "personality",
        "victory_points",
        "winner",
        "roads",
        "settlements",
        "cities",
        "played_knights",
        "longest_road",
        "largest_army",
        "vp_breakdown",
        "trades",
        "builds",
        "luck_index",
    }
)
_PUBLIC_TIMELINE_FIELDS = frozenset(
    {
        "sequence",
        "replay_frame_index",
        "elapsed_ms",
        "label",
        "scores",
    }
)
_PUBLIC_EVENT_FIELDS = frozenset(
    {
        "sequence",
        "replay_frame_index",
        "elapsed_ms",
        "category",
        "title",
        "detail",
        "level",
    }
)


class NetworkReplayError(ValueError):
    """Expected archive failure carrying a stable integration error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class _NetworkFrame:
    revision: int
    elapsed_ms: int
    label: str
    snapshots: dict[int | None, dict[str, Any]]


@dataclass
class _RoomArchive:
    started_at: float
    frames: list[_NetworkFrame] = field(default_factory=list)
    truncated: bool = False
    event_revisions: dict[int, int] = field(default_factory=dict)
    checkpoint_revisions: dict[int, int] = field(default_factory=dict)
    final_result: dict[str, Any] | None = None


class NetworkReplayStore:
    """Keep bounded per-room revisions and produce JSON-ready UI payloads.

    ``viewer_player_index`` must always come from the authenticated controller
    session.  It must never be copied from a browser request.  ``None`` means a
    spectator, whose snapshots contain no private hand or development-card
    information.
    """

    def __init__(
        self,
        *,
        max_frames: int = DEFAULT_MAX_NETWORK_REPLAY_FRAMES,
        max_rooms: int = DEFAULT_MAX_NETWORK_REPLAY_ROOMS,
        clock: Callable[[], float] = time.monotonic,
        snapshot_builder: Callable[..., dict[str, Any]] | None = None,
        result_builder: Callable[..., dict[str, Any]] = build_match_result,
    ) -> None:
        if (
            type(max_frames) is not int
            or not 1 <= max_frames <= MAX_NETWORK_REPLAY_FRAMES
        ):
            raise ValueError(f"max_frames must be 1..{MAX_NETWORK_REPLAY_FRAMES}")
        if type(max_rooms) is not int or not 1 <= max_rooms <= MAX_NETWORK_REPLAY_ROOMS:
            raise ValueError(f"max_rooms must be 1..{MAX_NETWORK_REPLAY_ROOMS}")
        if not callable(clock):
            raise ValueError("clock must be callable")
        if snapshot_builder is not None and not callable(snapshot_builder):
            raise ValueError("snapshot_builder must be callable")
        if not callable(result_builder):
            raise ValueError("result_builder must be callable")
        self.max_frames = max_frames
        self.max_rooms = max_rooms
        self._clock = clock
        self._snapshot_builder = snapshot_builder
        self._result_builder = result_builder
        self._rooms: OrderedDict[str, _RoomArchive] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def room_codes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._rooms)

    def latest_revision(self, room_code: str) -> int | None:
        """Return the newest retained revision, or ``None`` for no archive.

        This deliberately does not update the room's LRU position.  A caller
        may use it while reconciling independently persisted game and replay
        authority without making an observation look like replay activity.
        """

        room_code = _validated_room_code(room_code)
        with self._lock:
            archive = self._rooms.get(room_code)
            if archive is None or not archive.frames:
                return None
            return archive.frames[-1].revision

    def export_room_authority(self, room_code: str) -> dict[str, Any]:
        """Return a detached, exact-schema authority document for one room.

        Viewer variants are encoded as a sorted list rather than an object so
        the spectator's ``None`` identity cannot be confused with a JSON key.
        The freshly encoded document is decoded again before it leaves the
        store.  That last defensive pass prevents accidental in-memory
        mutation from persisting private or non-canonical data.
        """

        room_code = _validated_room_code(room_code)
        with self._lock:
            archive = self._room(room_code)
            frames = deepcopy(archive.frames)
            event_revisions = dict(archive.event_revisions)
            checkpoint_revisions = dict(archive.checkpoint_revisions)
            final_result = _authority_result_document(
                archive.final_result,
                frame_count=len(frames),
            )
            truncated = archive.truncated or _frames_have_missing_history(frames)

        document = {
            "format": NETWORK_REPLAY_AUTHORITY_FORMAT,
            "version": NETWORK_REPLAY_AUTHORITY_VERSION,
            "room_code": room_code,
            "truncated": truncated,
            "frames": [_frame_authority_document(frame) for frame in frames],
            "event_revisions": _metric_revision_authority(event_revisions),
            "checkpoint_revisions": _metric_revision_authority(checkpoint_revisions),
            "final_result": final_result,
        }
        # Validation is intentionally outside the store lock.  It works from
        # detached values and may serialize a sizeable finished replay.
        _decode_room_authority(
            document,
            max_frames=self.max_frames,
            now=float(self._clock()),
        )
        return deepcopy(document)

    def import_room_authority(self, document: Mapping[str, Any]) -> None:
        """Atomically insert or replace one exact replay authority document.

        Parsing, privacy checks, canonical-order checks, and bounds checks all
        finish before the live store is touched.  A rejected document cannot
        partially replace an existing archive.
        """

        archive, room_code = _decode_room_authority(
            document,
            max_frames=self.max_frames,
            now=float(self._clock()),
        )
        with self._lock:
            if room_code not in self._rooms:
                self._make_room_space()
            self._rooms[room_code] = archive
            self._rooms.move_to_end(room_code)

    def capture_game(
        self,
        room_code: str,
        game: Any,
        *,
        revision: int,
        label: str | None = None,
    ) -> None:
        """Capture every seat plus a spectator from one authoritative game.

        All snapshots and the optional result are constructed before the store
        mutates, so a failed snapshot leaves the previous replay intact.
        """

        room_code = _validated_room_code(room_code)
        revision = _validated_revision(revision)
        players = list(getattr(game, "players", ()) or ())
        if not 1 <= len(players) <= MAX_NETWORK_REPLAY_VIEWERS:
            raise NetworkReplayError(
                "invalid_game",
                "対局のプレイヤー数がリプレイ範囲外です。",
            )
        builder = self._snapshot_builder or _default_snapshot_builder
        snapshots = {
            viewer: builder(
                game,
                viewer_player_index=viewer,
                revision=revision,
            )
            for viewer in (None, *range(len(players)))
        }
        metrics = _metrics_document(getattr(game, "match_metrics", None))
        result = None
        if getattr(game, "phase", None) == "finished":
            # Rebuild from the authoritative state instead of trusting a UI
            # cache that may have been produced before the final command.
            result = self._result_builder(game)
        self.record_revision(
            room_code,
            revision=revision,
            snapshots=snapshots,
            label=label,
            metrics=metrics,
            result=result,
        )

    def capture_restored_game(
        self,
        room_code: str,
        game: Any,
        *,
        revision: int,
    ) -> None:
        """Reconcile a restored game with its independently saved replay.

        Equal revisions preserve imported history and labels.  If game
        authority is ahead, one explicit restart boundary is appended and the
        archive is marked truncated; if replay is ahead, restoration fails
        closed.  Historical cumulative metrics are never attached to the
        boundary because doing so would create false result links.
        """

        room_code = _validated_room_code(room_code)
        revision = _validated_revision(revision)
        players = list(getattr(game, "players", ()) or ())
        if not 1 <= len(players) <= MAX_NETWORK_REPLAY_VIEWERS:
            raise NetworkReplayError(
                "invalid_game",
                "対局のプレイヤー数がリプレイ範囲外です。",
            )
        builder = self._snapshot_builder or _default_snapshot_builder
        snapshots = {
            viewer: builder(
                game,
                viewer_player_index=viewer,
                revision=revision,
            )
            for viewer in (None, *range(len(players)))
        }
        result = (
            self._result_builder(game)
            if getattr(game, "phase", None) == "finished"
            else None
        )
        with self._lock:
            archive = self._rooms.get(room_code)
            replay_revision = (
                archive.frames[-1].revision
                if archive is not None and archive.frames
                else None
            )
            if replay_revision is not None and replay_revision > revision:
                raise NetworkReplayError(
                    "replay_ahead",
                    "リプレイrevisionが復元した対局より先行しています。",
                )

            # At equal revisions the imported archive already describes the
            # restored commit.  Refresh its viewer snapshots/final result but
            # retain the full history, elapsed time, label, and link maps.
            equal_revision = replay_revision == revision
            self.record_revision(
                room_code,
                revision=revision,
                snapshots=snapshots,
                label=None if equal_revision else "サーバー再起動から再開",
                metrics={},
                result=result,
            )
            if result is None:
                # The restored game authority is definitive.  Do not retain a
                # contradictory completed-result cache from replay storage.
                self._room(room_code).final_result = None
            if replay_revision is None:
                if revision > 0:
                    self._room(room_code).truncated = True
            elif replay_revision < revision:
                # The game authority committed further than the replay
                # archive.  Never pretend those missing revisions are present.
                self._room(room_code).truncated = True

    def record_snapshot(
        self,
        room_code: str,
        snapshot: Mapping[str, Any],
        *,
        label: str | None = None,
        metrics: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        """Merge one already-filtered controller snapshot into its revision.

        This adapter is convenient for transports that only see outbound
        messages.  ``capture_game`` is preferred because it records every seat
        even while a player is temporarily disconnected.
        """

        if not isinstance(snapshot, Mapping):
            raise NetworkReplayError(
                "invalid_snapshot", "リプレイスナップショットが不正です。"
            )
        self.record_revision(
            room_code,
            revision=snapshot.get("revision"),
            snapshots={snapshot.get("viewer_player_index"): snapshot},
            label=label,
            metrics=metrics,
            result=result,
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
        """Atomically append or merge a revision of viewer-filtered states."""

        room_code = _validated_room_code(room_code)
        revision = _validated_revision(revision)
        normalised = _normalise_snapshots(snapshots, revision)
        _share_board_manifest(normalised, previous=None)
        label = _validated_label(label) if label is not None else None
        metrics_document = _metrics_document(metrics)
        result_document = _result_document(result)
        now = float(self._clock())

        with self._lock:
            archive = self._rooms.get(room_code)
            if archive is None:
                self._make_room_space()
                archive = _RoomArchive(started_at=now)
                self._rooms[room_code] = archive
            elif archive.frames and revision < archive.frames[-1].revision:
                raise NetworkReplayError(
                    "stale_revision",
                    "現在より古いrevisionはリプレイへ追加できません。",
                )

            derived_label = label or _snapshot_label(next(iter(normalised.values())))
            if archive.frames and revision == archive.frames[-1].revision:
                frame = archive.frames[-1]
                _share_board_manifest(normalised, previous=frame.snapshots)
                frame.snapshots.update(normalised)
                if label is not None:
                    frame.label = label
                elif not frame.label and derived_label:
                    frame.label = derived_label
            else:
                previous_elapsed = (
                    archive.frames[-1].elapsed_ms if archive.frames else 0
                )
                elapsed_ms = max(
                    previous_elapsed,
                    round(max(0.0, now - archive.started_at) * 1_000),
                )
                previous_snapshots = (
                    archive.frames[-1].snapshots if archive.frames else None
                )
                if (not archive.frames and revision > 0) or (
                    archive.frames and revision != archive.frames[-1].revision + 1
                ):
                    archive.truncated = True
                _share_board_manifest(normalised, previous=previous_snapshots)
                frame = _NetworkFrame(
                    revision=revision,
                    elapsed_ms=elapsed_ms,
                    label=derived_label or _fallback_label(revision),
                    snapshots=normalised,
                )
                archive.frames.append(frame)
                if len(archive.frames) > self.max_frames:
                    archive.truncated = True
                    del archive.frames[: len(archive.frames) - self.max_frames]

            _remember_metric_revisions(archive, metrics_document, revision)
            if result_document is not None:
                archive.final_result = result_document
            self._rooms.move_to_end(room_code)

    def frame_payload(
        self,
        room_code: str,
        *,
        viewer_player_index: int | None,
        frame_index: int,
    ) -> dict[str, Any]:
        """Return one immutable, viewer-specific replay frame for a UI."""

        room_code = _validated_room_code(room_code)
        viewer = _validated_viewer(viewer_player_index)
        if isinstance(frame_index, bool) or not isinstance(frame_index, int):
            raise NetworkReplayError(
                "invalid_frame", "リプレイのフレーム番号が不正です。"
            )
        with self._lock:
            archive = self._room(room_code)
            if not 0 <= frame_index < len(archive.frames):
                raise NetworkReplayError(
                    "frame_out_of_range",
                    "リプレイのフレーム番号が範囲外です。",
                )
            frame = archive.frames[frame_index]
            try:
                snapshot = frame.snapshots[viewer]
            except KeyError as exc:
                raise NetworkReplayError(
                    "viewer_history_unavailable",
                    "この閲覧者用のリプレイ履歴を利用できません。",
                ) from exc
            return {
                "type": "network_replay_frame",
                "format": NETWORK_REPLAY_FORMAT,
                "version": NETWORK_REPLAY_VERSION,
                "room_code": room_code,
                "viewer_player_index": viewer,
                "read_only": True,
                "controls": _controls(archive.frames, frame_index),
                "snapshot": deepcopy(snapshot),
            }

    def result_payload(
        self,
        room_code: str,
        *,
        viewer_player_index: int | None,
    ) -> dict[str, Any]:
        """Return the completed result with links into retained replay frames."""

        room_code = _validated_room_code(room_code)
        viewer = _validated_viewer(viewer_player_index)
        with self._lock:
            archive = self._room(room_code)
            if archive.final_result is None:
                raise NetworkReplayError(
                    "match_not_finished", "対局結果はまだ確定していません。"
                )
            if not any(viewer in frame.snapshots for frame in archive.frames):
                raise NetworkReplayError(
                    "viewer_history_unavailable",
                    "この閲覧者用のリプレイ履歴を利用できません。",
                )
            result = _linked_result(archive)
            return {
                "type": "network_match_result",
                "format": NETWORK_REPLAY_FORMAT,
                "version": NETWORK_REPLAY_VERSION,
                "room_code": room_code,
                "viewer_player_index": viewer,
                "result": result,
                "replay": {
                    "available": bool(archive.frames),
                    "frame_count": len(archive.frames),
                    "truncated": archive.truncated,
                    "first_revision": (
                        archive.frames[0].revision if archive.frames else None
                    ),
                    "last_revision": (
                        archive.frames[-1].revision if archive.frames else None
                    ),
                    "initial_frame_index": 0 if archive.frames else None,
                    "final_frame_index": (
                        len(archive.frames) - 1 if archive.frames else None
                    ),
                },
            }

    def discard_room(self, room_code: str) -> bool:
        """Forget a room, including all private replay variants."""

        room_code = _validated_room_code(room_code)
        with self._lock:
            return self._rooms.pop(room_code, None) is not None

    def _room(self, room_code: str) -> _RoomArchive:
        try:
            return self._rooms[room_code]
        except KeyError as exc:
            raise NetworkReplayError(
                "replay_not_found", "この部屋のリプレイはありません。"
            ) from exc

    def _make_room_space(self) -> None:
        if len(self._rooms) < self.max_rooms:
            return
        # Result archives are the most useful to retain.  Prefer replacing the
        # oldest unfinished orphan; otherwise evict the least recently used
        # completed room.  Archive loss must never block an authoritative game.
        candidate = next(
            (
                code
                for code, archive in self._rooms.items()
                if archive.final_result is None
            ),
            next(iter(self._rooms)),
        )
        self._rooms.pop(candidate, None)


def _frame_authority_document(frame: _NetworkFrame) -> dict[str, Any]:
    return {
        "revision": frame.revision,
        "elapsed_ms": frame.elapsed_ms,
        "label": frame.label,
        "snapshots": [
            {
                "viewer_player_index": viewer,
                "snapshot": deepcopy(frame.snapshots[viewer]),
            }
            for viewer in sorted(frame.snapshots, key=_viewer_sort_key)
        ],
    }


def _metric_revision_authority(values: Mapping[int, int]) -> list[dict[str, int]]:
    return [
        {"sequence": sequence, "revision": values[sequence]}
        for sequence in sorted(values)
    ]


def _authority_result_document(
    value: Any,
    *,
    frame_count: int,
) -> dict[str, Any] | None:
    result = _result_document(value)
    if result is None:
        return None
    result["replay"] = {"available": frame_count > 0, "frame_count": frame_count}
    for field_name in ("important_events", "vp_progression"):
        rows = result.get(field_name)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                # Metric revision maps are the sole persisted source for these
                # links.  Cached UI indices become dishonest after truncation.
                row["replay_frame_index"] = None
                row["elapsed_ms"] = None
    return result


def _decode_room_authority(
    document: Any,
    *,
    max_frames: int,
    now: float,
) -> tuple[_RoomArchive, str]:
    """Validate and detach one persisted room archive without side effects."""

    if type(document) is not dict or set(document) != _AUTHORITY_KEYS:
        raise NetworkReplayError(
            "invalid_authority", "リプレイ権威文書の項目が不正です。"
        )
    if document["format"] != NETWORK_REPLAY_AUTHORITY_FORMAT:
        raise NetworkReplayError(
            "unsupported_authority", "リプレイ権威文書の形式に対応していません。"
        )
    if (
        type(document["version"]) is not int
        or document["version"] != NETWORK_REPLAY_AUTHORITY_VERSION
    ):
        raise NetworkReplayError(
            "unsupported_authority", "リプレイ権威文書のversionに対応していません。"
        )
    _assert_canonical_json_document(document)
    room_code = _validated_room_code(document["room_code"])
    truncated = document["truncated"]
    if type(truncated) is not bool:
        raise NetworkReplayError(
            "invalid_authority", "リプレイのtruncated値が不正です。"
        )
    raw_frames = document["frames"]
    if (
        type(raw_frames) is not list
        or not raw_frames
        or len(raw_frames) > max_frames
        or len(raw_frames) > MAX_NETWORK_REPLAY_FRAMES
    ):
        raise NetworkReplayError(
            "invalid_authority", "リプレイフレーム数が範囲外です。"
        )

    frames: list[_NetworkFrame] = []
    previous_snapshots: Mapping[int | None, dict[str, Any]] | None = None
    for raw_frame in raw_frames:
        frame = _decode_authority_frame(
            raw_frame,
            previous=frames[-1] if frames else None,
            previous_snapshots=previous_snapshots,
        )
        frames.append(frame)
        previous_snapshots = frame.snapshots

    missing_history = _frames_have_missing_history(frames)
    if missing_history and not truncated:
        raise NetworkReplayError(
            "invalid_authority",
            "欠落revisionを含むリプレイはtruncatedでなければなりません。",
        )
    retained_revisions = {frame.revision for frame in frames}
    last_revision = frames[-1].revision
    event_revisions = _decode_metric_revisions(
        document["event_revisions"],
        field_name="event_revisions",
        last_revision=last_revision,
        retained_revisions=retained_revisions,
        truncated=truncated,
    )
    checkpoint_revisions = _decode_metric_revisions(
        document["checkpoint_revisions"],
        field_name="checkpoint_revisions",
        last_revision=last_revision,
        retained_revisions=retained_revisions,
        truncated=truncated,
    )
    final_result = _decode_authority_result(
        document["final_result"],
        frame_count=len(frames),
        final_frame=frames[-1],
    )
    if not math.isfinite(now):
        raise NetworkReplayError("invalid_authority", "リプレイ時計が不正です。")
    archive = _RoomArchive(
        started_at=now - frames[-1].elapsed_ms / 1_000,
        frames=frames,
        truncated=truncated,
        event_revisions=event_revisions,
        checkpoint_revisions=checkpoint_revisions,
        final_result=final_result,
    )
    return archive, room_code


def _decode_authority_frame(
    document: Any,
    *,
    previous: _NetworkFrame | None,
    previous_snapshots: Mapping[int | None, dict[str, Any]] | None,
) -> _NetworkFrame:
    if type(document) is not dict or set(document) != _AUTHORITY_FRAME_KEYS:
        raise NetworkReplayError(
            "invalid_authority", "リプレイフレームの項目が不正です。"
        )
    revision = _validated_revision(document["revision"])
    elapsed_ms = _validated_authority_counter(
        document["elapsed_ms"],
        label="elapsed_ms",
        maximum=MAX_NETWORK_REPLAY_ELAPSED_MS,
    )
    label = _validated_label(document["label"])
    if label != document["label"]:
        raise NetworkReplayError(
            "invalid_authority", "リプレイラベルが正規形ではありません。"
        )
    if previous is not None:
        if revision <= previous.revision:
            raise NetworkReplayError(
                "invalid_authority", "リプレイrevisionが昇順ではありません。"
            )
        if elapsed_ms < previous.elapsed_ms:
            raise NetworkReplayError(
                "invalid_authority", "リプレイ経過時間が昇順ではありません。"
            )

    raw_entries = document["snapshots"]
    if (
        type(raw_entries) is not list
        or not raw_entries
        or len(raw_entries) > MAX_NETWORK_REPLAY_VIEWERS + 1
    ):
        raise NetworkReplayError(
            "invalid_authority", "閲覧者スナップショット数が範囲外です。"
        )
    snapshots: dict[int | None, Mapping[str, Any]] = {}
    ordered_viewers: list[int | None] = []
    for entry in raw_entries:
        if type(entry) is not dict or set(entry) != _AUTHORITY_SNAPSHOT_KEYS:
            raise NetworkReplayError(
                "invalid_authority", "閲覧者スナップショットの項目が不正です。"
            )
        viewer = _validated_viewer(entry["viewer_player_index"])
        if viewer in snapshots:
            raise NetworkReplayError(
                "invalid_authority", "閲覧者スナップショットが重複しています。"
            )
        snapshots[viewer] = entry["snapshot"]
        ordered_viewers.append(viewer)
    expected_order = sorted(ordered_viewers, key=_viewer_sort_key)
    if ordered_viewers != expected_order or None not in snapshots:
        raise NetworkReplayError(
            "invalid_authority",
            "閲覧者スナップショットの順序またはspectatorが不正です。",
        )

    normalised = _normalise_snapshots(snapshots, revision)
    if set(normalised) != set(snapshots) or any(
        normalised[viewer] != snapshots[viewer] for viewer in snapshots
    ):
        raise NetworkReplayError(
            "invalid_authority",
            "リプレイスナップショットが正規形ではありません。",
        )
    for snapshot in normalised.values():
        _assert_authority_snapshot_shape(snapshot)
    _assert_viewer_snapshots_consistent(normalised)
    _share_board_manifest(normalised, previous=previous_snapshots)
    return _NetworkFrame(
        revision=revision,
        elapsed_ms=elapsed_ms,
        label=label,
        snapshots=normalised,
    )


def _decode_metric_revisions(
    document: Any,
    *,
    field_name: str,
    last_revision: int,
    retained_revisions: set[int],
    truncated: bool,
) -> dict[int, int]:
    if (
        type(document) is not list
        or len(document) > MAX_NETWORK_REPLAY_METRIC_REVISIONS
    ):
        raise NetworkReplayError("invalid_authority", f"{field_name}が範囲外です。")
    result: dict[int, int] = {}
    previous_sequence = -1
    for item in document:
        if type(item) is not dict or set(item) != _AUTHORITY_METRIC_REVISION_KEYS:
            raise NetworkReplayError(
                "invalid_authority", f"{field_name}の項目が不正です。"
            )
        sequence = _validated_authority_counter(
            item["sequence"],
            label=f"{field_name}.sequence",
            maximum=MAX_NETWORK_REPLAY_REVISION,
        )
        revision = _validated_revision(item["revision"])
        if sequence <= previous_sequence:
            raise NetworkReplayError(
                "invalid_authority", f"{field_name}が昇順ではありません。"
            )
        if revision > last_revision or (
            revision not in retained_revisions and not truncated
        ):
            raise NetworkReplayError(
                "invalid_authority", f"{field_name}のrevisionが不正です。"
            )
        result[sequence] = revision
        previous_sequence = sequence
    return result


def _decode_authority_result(
    document: Any,
    *,
    frame_count: int,
    final_frame: _NetworkFrame,
) -> dict[str, Any] | None:
    if document is None:
        return None
    if type(document) is not dict or set(document) != _PUBLIC_RESULT_FIELDS:
        raise NetworkReplayError("invalid_authority", "確定結果の項目が不正です。")
    result = _authority_result_document(document, frame_count=frame_count)
    if result != document:
        raise NetworkReplayError(
            "invalid_authority", "確定結果が公開正規形ではありません。"
        )
    if (
        result.get("format") != MATCH_RESULT_FORMAT
        or type(result.get("version")) is not int
        or result["version"] != MATCH_RESULT_VERSION
        or result.get("completed") is not True
    ):
        raise NetworkReplayError("invalid_authority", "確定結果の形式が不正です。")
    _assert_authority_result_shape(result, frame_count=frame_count)
    spectator_state = final_frame.snapshots[None].get("state")
    phase = (
        spectator_state.get("phase") if isinstance(spectator_state, Mapping) else None
    )
    if not isinstance(phase, Mapping) or phase.get("name") != "finished":
        raise NetworkReplayError(
            "invalid_authority", "未完了フレームに確定結果は保存できません。"
        )
    return result


def _assert_authority_result_shape(
    result: Mapping[str, Any],
    *,
    frame_count: int,
) -> None:
    board = result.get("board")
    if (
        type(board) is not dict
        or set(board) != {"mode", "seed"}
        or type(board.get("mode")) is not str
        or type(board.get("seed")) is not int
    ):
        raise NetworkReplayError("invalid_authority", "確定結果の盤面情報が不正です。")
    if (
        type(result.get("source")) is not str
        or type(result.get("victory_target")) is not int
        or result["victory_target"] < 1
        or type(result.get("timeline_unit")) is not str
    ):
        raise NetworkReplayError("invalid_authority", "確定結果の基本情報が不正です。")
    winner = result.get("winner")
    if winner is not None and (
        type(winner) is not dict
        or set(winner) != {"seat", "name"}
        or type(winner.get("seat")) is not int
        or winner["seat"] < 1
        or type(winner.get("name")) is not str
    ):
        raise NetworkReplayError("invalid_authority", "確定結果の勝者情報が不正です。")
    replay = result.get("replay")
    if replay != {"available": True, "frame_count": frame_count}:
        raise NetworkReplayError(
            "invalid_authority", "確定結果のリプレイ情報が不正です。"
        )

    standings = result.get("standings")
    if type(standings) is not list or len(standings) > MAX_NETWORK_REPLAY_VIEWERS:
        raise NetworkReplayError("invalid_authority", "確定結果の順位表が不正です。")
    for row in standings:
        if type(row) is not dict:
            raise NetworkReplayError(
                "invalid_authority", "確定結果の順位項目が不正です。"
            )
        for key in ("rank", "seat", "victory_points"):
            if key in row and (
                type(row[key]) is not int
                or row[key] < (1 if key != "victory_points" else 0)
            ):
                raise NetworkReplayError(
                    "invalid_authority", "確定結果の順位数値が不正です。"
                )
        if "name" in row and type(row["name"]) is not str:
            raise NetworkReplayError(
                "invalid_authority", "確定結果のプレイヤー名が不正です。"
            )
        for key in ("trades", "builds", "vp_breakdown"):
            if key in row and type(row[key]) is not dict:
                raise NetworkReplayError(
                    "invalid_authority", "確定結果の集計項目が不正です。"
                )

    for field_name in ("vp_progression", "important_events"):
        rows = result.get(field_name)
        if (
            type(rows) is not list
            or len(rows) > MAX_NETWORK_REPLAY_METRIC_REVISIONS
            or any(type(row) is not dict for row in rows)
        ):
            raise NetworkReplayError(
                "invalid_authority", f"確定結果の{field_name}が不正です。"
            )
    for point in result["vp_progression"]:
        scores = point.get("scores")
        if (
            type(scores) is not list
            or len(scores) > MAX_NETWORK_REPLAY_VIEWERS
            or any(type(score) is not dict for score in scores)
        ):
            raise NetworkReplayError(
                "invalid_authority", "確定結果の勝利点推移が不正です。"
            )
    for event in result["important_events"]:
        for key in ("category", "title", "detail", "level"):
            if key in event and type(event[key]) is not str:
                raise NetworkReplayError(
                    "invalid_authority", "確定結果のイベント項目が不正です。"
                )


def _assert_canonical_json_document(document: dict[str, Any]) -> None:
    def visit(value: Any) -> None:
        if value is None or type(value) in {bool, int, str}:
            return
        if type(value) is float:
            if math.isfinite(value):
                return
            raise NetworkReplayError(
                "invalid_authority", "リプレイ権威文書に非有限数があります。"
            )
        if type(value) is list:
            for item in value:
                visit(item)
            return
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise NetworkReplayError(
                        "invalid_authority",
                        "リプレイ権威文書のJSON keyが不正です。",
                    )
                visit(item)
            return
        raise NetworkReplayError(
            "invalid_authority", "リプレイ権威文書がJSON正規形ではありません。"
        )

    visit(document)
    try:
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise NetworkReplayError(
            "invalid_authority", "リプレイ権威文書をJSON化できません。"
        ) from exc
    if len(encoded) > MAX_NETWORK_REPLAY_AUTHORITY_BYTES:
        raise NetworkReplayError(
            "invalid_authority", "リプレイ権威文書が大きすぎます。"
        )


def _validated_authority_counter(value: Any, *, label: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise NetworkReplayError("invalid_authority", f"{label}が範囲外です。")
    return value


def _viewer_sort_key(viewer: int | None) -> int:
    return -1 if viewer is None else viewer


def _frames_have_missing_history(frames: Sequence[_NetworkFrame]) -> bool:
    if not frames:
        return False
    if frames[0].revision != 0:
        return True
    return any(
        current.revision != previous.revision + 1
        for previous, current in zip(frames, frames[1:])
    )


def _assert_viewer_snapshots_consistent(
    snapshots: Mapping[int | None, dict[str, Any]],
) -> None:
    spectator = snapshots.get(None)
    if spectator is None:
        raise NetworkReplayError(
            "invalid_authority", "spectatorスナップショットがありません。"
        )
    for viewer, snapshot in snapshots.items():
        if viewer is None:
            continue
        if _spectator_snapshot(snapshot) != spectator:
            raise NetworkReplayError(
                "invalid_snapshot",
                "閲覧者間で公開スナップショットが一致しません。",
            )


def _assert_authority_snapshot_shape(snapshot: Mapping[str, Any]) -> None:
    if type(snapshot) is not dict or set(snapshot) != _ARCHIVED_SNAPSHOT_KEYS:
        raise NetworkReplayError(
            "invalid_authority", "保存スナップショットの項目が不正です。"
        )
    if (
        type(snapshot.get("protocol_version")) is not int
        or snapshot["protocol_version"] < 1
        or type(snapshot.get("board_manifest")) is not dict
        or snapshot.get("command_options") != []
    ):
        raise NetworkReplayError(
            "invalid_authority", "保存スナップショットの公開形式が不正です。"
        )
    state = snapshot.get("state")
    if type(state) is not dict or not set(state) <= _PUBLIC_SNAPSHOT_STATE_FIELDS:
        raise NetworkReplayError(
            "invalid_authority", "保存ゲーム状態に未定義の項目があります。"
        )
    players = state.get("players")
    if type(players) is not list:
        raise NetworkReplayError("invalid_authority", "保存プレイヤー状態が不正です。")
    for player in players:
        if type(player) is not dict or not set(player) <= _PUBLIC_PLAYER_FIELDS:
            raise NetworkReplayError(
                "invalid_authority", "保存プレイヤー状態に未定義の項目があります。"
            )


def _default_snapshot_builder(game: Any, **kwargs: Any) -> dict[str, Any]:
    # Lazy import keeps this archive module usable by result-analysis tooling
    # without importing Pygame's board implementation at module import time.
    from game.network_protocol import build_state_snapshot

    return build_state_snapshot(game, **kwargs)


def _validated_room_code(value: Any) -> str:
    if not isinstance(value, str) or _ROOM_CODE_PATTERN.fullmatch(value) is None:
        raise NetworkReplayError("invalid_room", "部屋コードが不正です。")
    return value


def _validated_revision(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_NETWORK_REPLAY_REVISION
    ):
        raise NetworkReplayError("invalid_revision", "revisionが不正です。")
    return value


def _validated_viewer(value: Any) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value < MAX_NETWORK_REPLAY_VIEWERS
    ):
        raise NetworkReplayError("invalid_viewer", "閲覧プレイヤー番号が不正です。")
    return value


def _validated_label(value: Any) -> str:
    if not isinstance(value, str):
        raise NetworkReplayError("invalid_label", "リプレイラベルが不正です。")
    value = " ".join(value.split())
    if len(value) > MAX_NETWORK_REPLAY_LABEL_LENGTH:
        raise NetworkReplayError("invalid_label", "リプレイラベルが長すぎます。")
    return value


def _normalise_snapshots(
    snapshots: Mapping[int | None, Mapping[str, Any]], revision: int
) -> dict[int | None, dict[str, Any]]:
    if not isinstance(snapshots, Mapping) or not snapshots:
        raise NetworkReplayError(
            "invalid_snapshot", "リプレイスナップショットが必要です。"
        )
    normalised: dict[int | None, dict[str, Any]] = {}
    player_count = None
    for raw_viewer, raw_snapshot in snapshots.items():
        viewer = _validated_viewer(raw_viewer)
        if not isinstance(raw_snapshot, Mapping):
            raise NetworkReplayError(
                "invalid_snapshot", "リプレイスナップショットが不正です。"
            )
        snapshot = deepcopy(dict(raw_snapshot))
        if (
            snapshot.get("type") != "state_snapshot"
            or snapshot.get("revision") != revision
            or snapshot.get("viewer_player_index") != viewer
        ):
            raise NetworkReplayError(
                "invalid_snapshot",
                "スナップショットのrevisionまたは閲覧者が一致しません。",
            )
        state = snapshot.get("state")
        players = state.get("players") if isinstance(state, Mapping) else None
        if (
            not isinstance(players, Sequence)
            or isinstance(players, (str, bytes))
            or not players
        ):
            raise NetworkReplayError("invalid_snapshot", "プレイヤー状態が不正です。")
        if player_count is None:
            player_count = len(players)
        elif player_count != len(players):
            raise NetworkReplayError(
                "invalid_snapshot", "閲覧者間でプレイヤー数が一致しません。"
            )
        if viewer is not None and viewer >= len(players):
            raise NetworkReplayError(
                "invalid_viewer", "閲覧プレイヤー番号が範囲外です。"
            )
        _assert_snapshot_privacy(state, players, viewer)
        snapshot["command_options"] = []
        _compact_snapshot_history(snapshot)
        normalised[viewer] = snapshot

    if None not in normalised:
        # A spectator history can always be safely derived by redacting one
        # already filtered player snapshot.  The reverse operation is never
        # attempted because private cards cannot be reconstructed safely.
        normalised[None] = _spectator_snapshot(next(iter(normalised.values())))
    return normalised


def _assert_snapshot_privacy(
    state: Mapping[str, Any],
    players: Sequence[Any],
    viewer_player_index: int | None,
) -> None:
    if "variant_state" in state:
        variant_state = state["variant_state"]
        if not isinstance(variant_state, Mapping):
            raise NetworkReplayError("invalid_snapshot", "variant_stateが不正です。")
        if "private" in variant_state:
            raise NetworkReplayError(
                "private_state_leak",
                "variantの非公開状態を含むスナップショットは保存できません。",
            )
        if set(variant_state) != _PUBLIC_VARIANT_STATE_FIELDS:
            raise NetworkReplayError(
                "invalid_snapshot", "variant_stateの公開文書が不正です。"
            )

    ai_state = state.get("ai")
    mixed_personalities_are_private = (
        isinstance(ai_state, Mapping) and ai_state.get("personality_mode") == MIXED
    )

    for index, raw_player in enumerate(players):
        if not isinstance(raw_player, Mapping):
            raise NetworkReplayError("invalid_snapshot", "プレイヤー状態が不正です。")
        if any(
            raw_player.get(field) is not None for field in _AUTHORITY_ONLY_PLAYER_FIELDS
        ):
            raise NetworkReplayError(
                "private_state_leak",
                "権威サーバー専用の資源予約情報を含むスナップショットは保存できません。",
            )
        if (
            mixed_personalities_are_private
            and raw_player.get("is_ai")
            and raw_player.get("ai_personality") is not None
        ):
            raise NetworkReplayError(
                "private_state_leak",
                "混合AIの非公開性格を含むスナップショットは保存できません。",
            )
        if index == viewer_player_index:
            continue
        leaked = [
            field
            for field in _PRIVATE_PLAYER_FIELDS
            if raw_player.get(field) is not None
        ]
        if leaked:
            raise NetworkReplayError(
                "private_state_leak",
                "他プレイヤーの非公開情報を含むスナップショットは保存できません。",
            )

    if mixed_personalities_are_private:
        ai_status = ai_state.get("status")
        if isinstance(ai_status, Mapping) and ai_status.get("personality") is not None:
            raise NetworkReplayError(
                "private_state_leak",
                "混合AIの非公開性格を含む実況状態は保存できません。",
            )
        if _contains_mixed_ai_identity(
            state,
            _mixed_ai_identity_patterns(players),
        ):
            raise NetworkReplayError(
                "private_state_leak",
                "混合AIの非公開性格を含む表示文は保存できません。",
            )

    development_deck = state.get("development_deck")
    if not isinstance(development_deck, Mapping) or set(development_deck) != {
        "remaining"
    }:
        raise NetworkReplayError(
            "private_state_leak",
            "未配布の発展カード順を含むスナップショットは保存できません。",
        )

    phase = state.get("phase")
    domestic_trade = state.get("domestic_trade")
    if not (
        isinstance(phase, Mapping)
        and phase.get("special_phase") == "domestic_trade_edit"
        and isinstance(domestic_trade, Mapping)
        and viewer_player_index != domestic_trade.get("editor")
    ):
        return
    for bundle_name in ("give", "receive"):
        bundle = domestic_trade.get(bundle_name)
        if isinstance(bundle, Mapping) and any(value != 0 for value in bundle.values()):
            raise NetworkReplayError(
                "private_state_leak",
                "提示前の交易条件を含むスナップショットは保存できません。",
            )
    if domestic_trade.get("receive_operator", "and") != "and":
        raise NetworkReplayError(
            "private_state_leak",
            "提示前のOR交易条件を含むスナップショットは保存できません。",
        )


def _spectator_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(snapshot))
    result["viewer_player_index"] = None
    result["command_options"] = []
    state = result.get("state")
    players = state.get("players") if isinstance(state, Mapping) else ()
    for player in players:
        if not isinstance(player, dict):
            continue
        for private_field in _PRIVATE_PLAYER_FIELDS:
            player[private_field] = None
    domestic_trade = state.get("domestic_trade") if isinstance(state, Mapping) else None
    phase = state.get("phase") if isinstance(state, Mapping) else None
    if (
        isinstance(domestic_trade, dict)
        and isinstance(phase, Mapping)
        and phase.get("special_phase") == "domestic_trade_edit"
    ):
        for field in ("give", "receive"):
            bundle = domestic_trade.get(field)
            if isinstance(bundle, dict):
                domestic_trade[field] = {key: 0 for key in bundle}
        domestic_trade["receive_operator"] = "and"
    return result


def _compact_snapshot_history(snapshot: dict[str, Any]) -> None:
    state = snapshot.get("state")
    if not isinstance(state, dict):
        return
    metrics = state.get("match_metrics")
    if isinstance(metrics, dict):
        metrics["point_checkpoints"] = []
        metrics["important_events"] = []
    history = state.get("history")
    if isinstance(history, dict):
        history["log_messages"] = []
        history["public_gain_history"] = {}
        entries = history.get("turn_summary_entries")
        if isinstance(entries, list):
            history["turn_summary_entries"] = entries[-6:]


def _metrics_document(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return deepcopy(dict(value))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        document = to_dict()
        if isinstance(document, Mapping):
            return deepcopy(dict(document))
    raise NetworkReplayError("invalid_metrics", "対局メトリクスが不正です。")


def _result_document(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise NetworkReplayError("invalid_result", "対局結果が不正です。")
    document = {
        key: deepcopy(item)
        for key, item in value.items()
        if key in _PUBLIC_RESULT_FIELDS
    }
    if not document.get("completed"):
        raise NetworkReplayError("invalid_result", "未完了の対局結果は確定できません。")
    for field_name, allowed_fields in (
        ("board", {"mode", "seed"}),
        ("winner", {"seat", "name"}),
        ("replay", {"available", "frame_count"}),
    ):
        nested = document.get(field_name)
        if isinstance(nested, Mapping):
            document[field_name] = {
                key: deepcopy(item)
                for key, item in nested.items()
                if key in allowed_fields
            }
    standings = document.get("standings")
    if isinstance(standings, list):
        document["standings"] = [
            _public_standing(row) for row in standings if isinstance(row, Mapping)
        ]
    progression = document.get("vp_progression")
    if isinstance(progression, list):
        document["vp_progression"] = [
            _public_timeline_entry(entry)
            for entry in progression
            if isinstance(entry, Mapping)
        ]
    events = document.get("important_events")
    if isinstance(events, list):
        document["important_events"] = [
            {
                key: deepcopy(item)
                for key, item in event.items()
                if key in _PUBLIC_EVENT_FIELDS
            }
            for event in events
            if isinstance(event, Mapping)
        ]
    return document


def _public_standing(value: Mapping[str, Any]) -> dict[str, Any]:
    standing = {
        key: deepcopy(item)
        for key, item in value.items()
        if key in _PUBLIC_STANDING_FIELDS
    }
    trades = standing.get("trades")
    if isinstance(trades, Mapping):
        standing["trades"] = {
            key: deepcopy(item)
            for key, item in trades.items()
            if key in {"bank", "domestic"}
        }
    builds = standing.get("builds")
    if isinstance(builds, Mapping):
        standing["builds"] = {
            key: deepcopy(item)
            for key, item in builds.items()
            if key in {"roads", "settlements", "cities"}
        }
    breakdown = standing.get("vp_breakdown")
    if isinstance(breakdown, Mapping):
        standing["vp_breakdown"] = _public_vp_breakdown(breakdown)
    return standing


def _public_vp_breakdown(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in (
        "settlements",
        "cities",
        "longest_road",
        "largest_army",
        "debt_penalty",
        "victory_point_cards",
    ):
        component = value.get(key)
        if not isinstance(component, Mapping):
            continue
        allowed = {"count", "points"}
        if key in {"longest_road", "largest_army"}:
            allowed = {"awarded", "points"}
        elif key == "debt_penalty":
            allowed = {"count", "status", "points"}
        result[key] = {
            nested_key: deepcopy(item)
            for nested_key, item in component.items()
            if nested_key in allowed
        }
    if "total" in value:
        result["total"] = deepcopy(value["total"])
    return result


def _public_timeline_entry(value: Mapping[str, Any]) -> dict[str, Any]:
    entry = {
        key: deepcopy(item)
        for key, item in value.items()
        if key in _PUBLIC_TIMELINE_FIELDS
    }
    scores = entry.get("scores")
    if isinstance(scores, list):
        entry["scores"] = [
            {
                key: deepcopy(item)
                for key, item in score.items()
                if key in {"seat", "victory_points"}
            }
            for score in scores
            if isinstance(score, Mapping)
        ]
    return entry


def _share_board_manifest(
    snapshots: Mapping[int | None, dict[str, Any]],
    *,
    previous: Mapping[int | None, dict[str, Any]] | None,
) -> None:
    """Share identical public board documents across private viewer variants."""

    manifests = [snapshot.get("board_manifest") for snapshot in snapshots.values()]
    if not manifests or not isinstance(manifests[0], Mapping):
        raise NetworkReplayError("invalid_snapshot", "公開盤面manifestがありません。")
    canonical = manifests[0]
    if any(manifest != canonical for manifest in manifests[1:]):
        raise NetworkReplayError(
            "invalid_snapshot", "閲覧者間で公開盤面manifestが一致しません。"
        )
    if previous:
        previous_manifest = next(iter(previous.values())).get("board_manifest")
        if previous_manifest == canonical:
            canonical = previous_manifest
    for snapshot in snapshots.values():
        snapshot["board_manifest"] = canonical


def _remember_metric_revisions(
    archive: _RoomArchive, metrics: Mapping[str, Any], revision: int
) -> None:
    for metric_field, destination in (
        ("important_events", archive.event_revisions),
        ("point_checkpoints", archive.checkpoint_revisions),
    ):
        values = metrics.get(metric_field)
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        for fallback_sequence, item in enumerate(values):
            if not isinstance(item, Mapping):
                continue
            sequence = item.get("sequence", fallback_sequence)
            if (
                isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or sequence < 0
            ):
                continue
            destination.setdefault(sequence, revision)


def _linked_result(archive: _RoomArchive) -> dict[str, Any]:
    result = deepcopy(archive.final_result)
    revision_indices = {
        frame.revision: index for index, frame in enumerate(archive.frames)
    }
    elapsed_by_revision = {frame.revision: frame.elapsed_ms for frame in archive.frames}
    for result_field, revisions in (
        ("important_events", archive.event_revisions),
        ("vp_progression", archive.checkpoint_revisions),
    ):
        values = result.get(result_field)
        if not isinstance(values, list):
            continue
        for fallback_sequence, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            sequence = item.get("sequence", fallback_sequence)
            revision = revisions.get(sequence)
            item["replay_frame_index"] = revision_indices.get(revision)
            item["elapsed_ms"] = elapsed_by_revision.get(revision)
    result["replay"] = {
        "available": bool(archive.frames),
        "frame_count": len(archive.frames),
    }
    return result


def _snapshot_label(snapshot: Mapping[str, Any]) -> str:
    state = snapshot.get("state")
    history = state.get("history") if isinstance(state, Mapping) else None
    latest = history.get("latest_event") if isinstance(history, Mapping) else None
    title = latest.get("title") if isinstance(latest, Mapping) else None
    return _validated_label(title) if isinstance(title, str) else ""


def _fallback_label(revision: int) -> str:
    return "対局開始" if revision == 0 else f"操作 {revision}"


def _controls(frames: Sequence[_NetworkFrame], frame_index: int) -> dict[str, Any]:
    frame = frames[frame_index]
    return {
        "frame_count": len(frames),
        "frame_index": frame_index,
        "revision": frame.revision,
        "elapsed_ms": frame.elapsed_ms,
        "label": frame.label,
        "can_previous": frame_index > 0,
        "can_next": frame_index + 1 < len(frames),
    }


__all__ = (
    "DEFAULT_MAX_NETWORK_REPLAY_FRAMES",
    "DEFAULT_MAX_NETWORK_REPLAY_ROOMS",
    "MAX_NETWORK_REPLAY_FRAMES",
    "MAX_NETWORK_REPLAY_ROOMS",
    "MAX_NETWORK_REPLAY_AUTHORITY_BYTES",
    "MAX_NETWORK_REPLAY_ELAPSED_MS",
    "MAX_NETWORK_REPLAY_METRIC_REVISIONS",
    "MAX_NETWORK_REPLAY_LABEL_LENGTH",
    "MAX_NETWORK_REPLAY_REVISION",
    "NETWORK_REPLAY_AUTHORITY_FORMAT",
    "NETWORK_REPLAY_AUTHORITY_VERSION",
    "NETWORK_REPLAY_FORMAT",
    "NETWORK_REPLAY_VERSION",
    "MAX_NETWORK_REPLAY_VIEWERS",
    "NetworkReplayError",
    "NetworkReplayStore",
)
