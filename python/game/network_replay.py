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
import re
import threading
import time
from typing import Any

from game.match_result import build_match_result


NETWORK_REPLAY_FORMAT = "catan-network-replay"
NETWORK_REPLAY_VERSION = 1
DEFAULT_MAX_NETWORK_REPLAY_FRAMES = 512
MAX_NETWORK_REPLAY_FRAMES = 5_000
DEFAULT_MAX_NETWORK_REPLAY_ROOMS = 64
MAX_NETWORK_REPLAY_ROOMS = 256
MAX_NETWORK_REPLAY_VIEWERS = 16
MAX_NETWORK_REPLAY_LABEL_LENGTH = 160

_ROOM_CODE_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_PRIVATE_PLAYER_FIELDS = (
    "resources",
    "development_cards",
    "new_development_cards",
    "victory_point_cards",
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
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
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
    for index, raw_player in enumerate(players):
        if not isinstance(raw_player, Mapping):
            raise NetworkReplayError("invalid_snapshot", "プレイヤー状態が不正です。")
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
    return standing


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
    "NETWORK_REPLAY_FORMAT",
    "NETWORK_REPLAY_VERSION",
    "NetworkReplayError",
    "NetworkReplayStore",
)
