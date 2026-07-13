"""Versioned, bounded replay archives built from normal save snapshots.

The module deliberately has no dependency on :mod:`game.game`, so the game can
integrate it without creating an import cycle.  A caller records snapshots at
meaningful state transitions and may validate an archive either against a
scratch game or against the current game (which is restored afterwards).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Dict, Optional, Tuple, Union

from game.assets import PROJECT_ROOT
from game.persistence import (
    SAVE_FORMAT,
    SAVE_VERSION,
    restore_game,
    serialize_game,
)


__all__ = (
    "DEFAULT_MAX_REPLAY_FRAMES",
    "DEFAULT_REPLAY_DIR",
    "MAX_REPLAY_FRAMES",
    "REPLAY_FORMAT",
    "REPLAY_VISIBILITY",
    "REPLAY_VERSION",
    "ReplayArchive",
    "ReplayError",
    "ReplayFrame",
    "ReplayRecorder",
    "compact_game_snapshot",
    "find_latest_replay",
    "load_replay",
    "restore_replay_frame",
    "save_replay",
    "timestamped_replay_path",
)


REPLAY_FORMAT = "catan-local-replay"
REPLAY_VERSION = 1
REPLAY_VISIBILITY = "private-full-state"
DEFAULT_REPLAY_DIR = PROJECT_ROOT / "replays"
REPLAY_FILENAME_PREFIX = "catan-replay-"

DEFAULT_MAX_REPLAY_FRAMES = 1_000
MAX_REPLAY_FRAMES = 5_000
MAX_REPLAY_FILE_BYTES = 16 * 1024 * 1024
MAX_REPLAY_FRAME_BYTES = 256 * 1024
MAX_REPLAY_METADATA_BYTES = 16 * 1024
MAX_FRAME_LABEL_LENGTH = 160
MAX_REPLAY_ELAPSED_MS = 31 * 24 * 60 * 60 * 1_000


class ReplayError(ValueError):
    """Raised when a replay cannot be recorded, saved, loaded, or restored."""


@dataclass(frozen=True)
class ReplayFrame:
    sequence: int
    elapsed_ms: int
    label: str
    snapshot: Dict[str, Any]


@dataclass(frozen=True)
class ReplayArchive:
    created_at: str
    metadata: Dict[str, Any]
    frames: Tuple[ReplayFrame, ...]

    @property
    def visibility(self) -> str:
        return REPLAY_VISIBILITY

    def frame(self, index: int) -> ReplayFrame:
        if isinstance(index, bool) or not isinstance(index, int):
            raise ReplayError("リプレイのフレーム番号が不正です。")
        if not 0 <= index < len(self.frames):
            raise ReplayError("リプレイのフレーム番号が範囲外です。")
        return self.frames[index]

    def to_document(self) -> Dict[str, Any]:
        return _archive_to_document(self)


class ReplayRecorder:
    """Collect compact snapshots and turn them into a replay archive."""

    def __init__(
        self,
        *,
        max_frames: int = DEFAULT_MAX_REPLAY_FRAMES,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[datetime] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            isinstance(max_frames, bool)
            or not isinstance(max_frames, int)
            or not 1 <= max_frames <= MAX_REPLAY_FRAMES
        ):
            raise ReplayError(
                f"最大フレーム数は 1〜{MAX_REPLAY_FRAMES} で指定してください。"
            )
        if not callable(clock):
            raise ReplayError("リプレイ用の時計が不正です。")

        self.max_frames = max_frames
        self._metadata = _validated_metadata(metadata or {})
        self._created_at = _format_datetime(created_at or datetime.now(timezone.utc))
        self._clock = clock
        self._started_at = float(clock())
        self._frames = []

    @property
    def frames(self) -> Tuple[ReplayFrame, ...]:
        return tuple(self._frames)

    def capture(
        self,
        game: Any,
        *,
        label: Optional[str] = None,
        elapsed_ms: Optional[int] = None,
        replace_last_if_full: bool = False,
    ) -> ReplayFrame:
        if not isinstance(replace_last_if_full, bool):
            raise ReplayError("末尾フレーム置換設定が不正です。")
        replace_last = len(self._frames) >= self.max_frames
        if replace_last and not replace_last_if_full:
            raise ReplayError(
                f"リプレイは最大 {self.max_frames} フレームまで記録できます。"
            )
        if _has_unresolved_dice(game):
            raise ReplayError(
                "ダイス演出中は復元可能なリプレイフレームを記録できません。"
            )

        if elapsed_ms is None:
            elapsed_ms = max(0, round((float(self._clock()) - self._started_at) * 1_000))
        _validate_elapsed_ms(elapsed_ms)
        if self._frames and elapsed_ms < self._frames[-1].elapsed_ms:
            raise ReplayError("フレームの経過時間は前のフレーム以上にしてください。")

        if label is None:
            latest_event = getattr(game, "latest_event", {})
            label = latest_event.get("title", "") if isinstance(latest_event, dict) else ""
        label = _validated_label(label)

        self._fill_game_metadata(game)
        snapshot = compact_game_snapshot(game)
        if replace_last:
            self._frames.pop()
        frame = ReplayFrame(
            sequence=len(self._frames),
            elapsed_ms=elapsed_ms,
            label=label,
            snapshot=snapshot,
        )
        self._frames.append(frame)
        return frame

    def archive(self) -> ReplayArchive:
        if not self._frames:
            raise ReplayError("空のリプレイは保存できません。")
        return ReplayArchive(
            created_at=self._created_at,
            metadata=copy.deepcopy(self._metadata),
            frames=tuple(copy.deepcopy(self._frames)),
        )

    def save(
        self,
        path: Optional[Union[str, Path]] = None,
        *,
        replay_dir: Union[str, Path] = DEFAULT_REPLAY_DIR,
        now: Optional[datetime] = None,
    ) -> Path:
        return save_replay(self.archive(), path, replay_dir=replay_dir, now=now)

    def _fill_game_metadata(self, game: Any) -> None:
        players = getattr(game, "players", ())
        defaults = {
            "board_mode": getattr(game, "board_mode", ""),
            "board_seed": getattr(game, "board_seed", None),
            "victory_point_target": getattr(game, "victory_point_target", None),
            "players": [
                {
                    "name": str(getattr(player, "name", "")),
                    "is_ai": bool(getattr(player, "is_ai", False)),
                }
                for player in players
            ],
            "snapshot_history": "latest-event-only",
            "development_deck_order": "canonicalized",
        }
        candidate = copy.deepcopy(self._metadata)
        for key, value in defaults.items():
            candidate.setdefault(key, value)
        self._metadata = _validated_metadata(candidate)


def compact_game_snapshot(game: Any) -> Dict[str, Any]:
    """Serialize a game while omitting history that would grow every frame.

    ``serialize_game`` returns a complete persistence snapshot, including the
    full event log.  Repeating that ever-growing list in every replay frame
    makes the archive quadratic in the number of turns.  A replay keeps the
    current event, bounded turn summary, and current distribution instead. The
    undealt development-card deck is sorted so its future draw order is not
    disclosed, while the exact remaining card counts stay restorable.
    """

    snapshot = serialize_game(game)
    development_deck = snapshot.get("development_deck")
    if isinstance(development_deck, list):
        snapshot["development_deck"] = sorted(development_deck)
    history = snapshot.get("history", {})
    if not isinstance(history, dict):
        history = {}
    snapshot["history"] = {
        "log_messages": [],
        "latest_event": copy.deepcopy(history.get("latest_event", {})),
        "turn_summary_entries": list(history.get("turn_summary_entries", []))[-6:],
        "public_gain_history": {},
        "last_resource_distribution": copy.deepcopy(
            history.get("last_resource_distribution", {})
        ),
    }
    return snapshot


def timestamped_replay_path(
    replay_dir: Union[str, Path] = DEFAULT_REPLAY_DIR,
    *,
    now: Optional[datetime] = None,
) -> Path:
    directory = Path(replay_dir)
    stamp = _normalise_datetime(now or datetime.now(timezone.utc))
    timestamp = stamp.strftime("%Y%m%dT%H%M%S%fZ")
    base = directory / f"{REPLAY_FILENAME_PREFIX}{timestamp}.json"
    if not base.exists():
        return base
    for suffix in range(1, 10_000):
        candidate = directory / (
            f"{REPLAY_FILENAME_PREFIX}{timestamp}-{suffix:04d}.json"
        )
        if not candidate.exists():
            return candidate
    raise ReplayError("同じ時刻のリプレイ保存先を確保できませんでした。")


def save_replay(
    replay: ReplayArchive,
    path: Optional[Union[str, Path]] = None,
    *,
    replay_dir: Union[str, Path] = DEFAULT_REPLAY_DIR,
    now: Optional[datetime] = None,
) -> Path:
    if not isinstance(replay, ReplayArchive):
        raise ReplayError("保存するリプレイデータが不正です。")
    document = _archive_to_document(replay)
    _validate_document(document)
    try:
        payload = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ReplayError(f"リプレイをJSONへ変換できません: {exc}") from exc
    if len(payload.encode("utf-8")) > MAX_REPLAY_FILE_BYTES:
        raise ReplayError("リプレイのファイルサイズが上限を超えています。")

    target = (
        Path(path)
        if path is not None
        else timestamped_replay_path(replay_dir, now=now)
    )
    temp_path = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    except OSError as exc:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise ReplayError(f"リプレイの保存に失敗しました: {exc}") from exc
    return target


def load_replay(
    path: Union[str, Path],
    *,
    validation_game: Optional[Any] = None,
    game_factory: Optional[Callable[[], Any]] = None,
) -> ReplayArchive:
    """Load a replay and optionally validate every frame via ``restore_game``.

    Pass ``game_factory`` to validate on a fresh scratch game.  Alternatively,
    pass ``validation_game``; its persisted state is restored even when a frame
    is invalid.  Supplying neither performs bounded structural validation only.
    """

    if validation_game is not None and game_factory is not None:
        raise ReplayError("検証用ゲームと生成関数は同時に指定できません。")
    target = Path(path)
    try:
        with target.open("rb") as handle:
            payload = handle.read(MAX_REPLAY_FILE_BYTES + 1)
    except FileNotFoundError as exc:
        raise ReplayError("リプレイデータが見つかりません。") from exc
    except OSError as exc:
        raise ReplayError(f"リプレイの読み込みに失敗しました: {exc}") from exc
    if len(payload) > MAX_REPLAY_FILE_BYTES:
        raise ReplayError("リプレイのファイルサイズが上限を超えています。")
    try:
        document = json.loads(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReplayError(f"リプレイの読み込みに失敗しました: {exc}") from exc

    _validate_document(document)
    archive = _document_to_archive(document)
    if game_factory is not None:
        try:
            scratch_game = game_factory()
        except Exception as exc:
            raise ReplayError(f"検証用ゲームを作成できません: {exc}") from exc
        try:
            validate_replay(archive, scratch_game)
        finally:
            _close_scratch_game(scratch_game)
    elif validation_game is not None:
        validate_replay(archive, validation_game)
    return archive


def validate_replay(replay: ReplayArchive, game: Any) -> bool:
    """Restore every frame for semantic validation, then restore ``game``."""

    _validate_archive(replay)
    runtime_state = _capture_runtime_state(game)
    try:
        backup = serialize_game(game)
    except Exception as exc:
        raise ReplayError(f"検証前のゲーム状態を退避できません: {exc}") from exc

    failure = None
    failure_sequence = None
    rollback_failure = None
    try:
        for frame in replay.frames:
            try:
                restore_game(
                    game,
                    copy.deepcopy(frame.snapshot),
                    runtime_side_effects=False,
                )
            except Exception as exc:
                failure = exc
                failure_sequence = frame.sequence
                break
    finally:
        try:
            restore_game(game, backup, runtime_side_effects=False)
        except Exception as exc:
            rollback_failure = exc
        finally:
            _restore_runtime_state(game, runtime_state)

    if rollback_failure is not None:
        raise ReplayError(
            "リプレイ検証後に元のゲーム状態を復元できません: "
            f"{rollback_failure}"
        ) from rollback_failure

    if failure is not None:
        raise ReplayError(
            f"リプレイのフレーム {failure_sequence} が不正です: {failure}"
        ) from failure
    return True


def restore_replay_frame(
    game: Any,
    replay: ReplayArchive,
    frame_index: int,
    *,
    validate_archive: bool = True,
) -> ReplayFrame:
    """Apply one frame, rolling back if restoration fails."""

    if not isinstance(validate_archive, bool):
        raise ReplayError("リプレイ検証設定が不正です。")
    if validate_archive:
        _validate_archive(replay)
    elif not isinstance(replay, ReplayArchive):
        raise ReplayError("リプレイデータの形式が不正です。")
    frame = replay.frame(frame_index)
    if _has_unresolved_dice(game):
        raise ReplayError(
            "ダイス結果の解決中はリプレイフレームへ移動できません。"
        )
    runtime_state = _capture_runtime_state(game)
    try:
        backup = serialize_game(game)
    except Exception as exc:
        raise ReplayError(f"現在のゲーム状態を退避できません: {exc}") from exc
    try:
        restore_game(
            game,
            copy.deepcopy(frame.snapshot),
            runtime_side_effects=False,
        )
    except Exception as exc:
        rollback_failure = None
        try:
            restore_game(game, backup, runtime_side_effects=False)
        except Exception as rollback_exc:
            rollback_failure = rollback_exc
        finally:
            _restore_runtime_state(game, runtime_state)
        if rollback_failure is not None:
            raise ReplayError(
                f"フレーム復元失敗後に元の状態へ戻せません: {rollback_failure}"
            ) from rollback_failure
        raise ReplayError(
            f"リプレイのフレーム {frame.sequence} を復元できません: {exc}"
        ) from exc
    return frame


def find_latest_replay(
    replay_dir: Union[str, Path] = DEFAULT_REPLAY_DIR,
) -> Optional[Path]:
    """Return the newest completed replay file, ignoring atomic temp files."""

    directory = Path(replay_dir)
    if not directory.exists():
        return None
    try:
        candidates = [
            path
            for path in directory.glob(f"{REPLAY_FILENAME_PREFIX}*.json")
            if path.is_file()
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))
    except OSError as exc:
        raise ReplayError(f"最新リプレイを確認できません: {exc}") from exc


def _archive_to_document(replay: ReplayArchive) -> Dict[str, Any]:
    return {
        "format": REPLAY_FORMAT,
        "version": REPLAY_VERSION,
        "visibility": REPLAY_VISIBILITY,
        "created_at": replay.created_at,
        "metadata": copy.deepcopy(replay.metadata),
        "frames": [
            {
                "sequence": frame.sequence,
                "elapsed_ms": frame.elapsed_ms,
                "label": frame.label,
                "snapshot": copy.deepcopy(frame.snapshot),
            }
            for frame in replay.frames
        ],
    }


def _document_to_archive(document: Dict[str, Any]) -> ReplayArchive:
    return ReplayArchive(
        created_at=document["created_at"],
        metadata=copy.deepcopy(document["metadata"]),
        frames=tuple(
            ReplayFrame(
                sequence=frame["sequence"],
                elapsed_ms=frame["elapsed_ms"],
                label=frame["label"],
                snapshot=copy.deepcopy(frame["snapshot"]),
            )
            for frame in document["frames"]
        ),
    )


def _validate_archive(replay: ReplayArchive) -> None:
    if not isinstance(replay, ReplayArchive):
        raise ReplayError("リプレイデータの形式が不正です。")
    _validate_document(_archive_to_document(replay))


def _validate_document(document: Any) -> None:
    if not isinstance(document, dict):
        raise ReplayError("リプレイデータの形式が不正です。")
    if document.get("format") != REPLAY_FORMAT:
        raise ReplayError("このゲームのリプレイデータではありません。")
    version = document.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != REPLAY_VERSION
    ):
        raise ReplayError(
            f"未対応のリプレイバージョンです: {version}"
        )
    if document.get("visibility") != REPLAY_VISIBILITY:
        raise ReplayError("リプレイの公開範囲ラベルが不正です。")
    _validate_created_at(document.get("created_at"))
    metadata = _validated_metadata(document.get("metadata"))

    frames = document.get("frames")
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_REPLAY_FRAMES:
        raise ReplayError(
            f"リプレイのフレーム数は 1〜{MAX_REPLAY_FRAMES} である必要があります。"
        )
    previous_elapsed = -1
    expected_identity = None
    for sequence, frame in enumerate(frames):
        if not isinstance(frame, dict):
            raise ReplayError(f"リプレイのフレーム {sequence} が不正です。")
        stored_sequence = frame.get("sequence")
        if (
            isinstance(stored_sequence, bool)
            or not isinstance(stored_sequence, int)
            or stored_sequence != sequence
        ):
            raise ReplayError("リプレイのフレーム順が不正です。")
        elapsed_ms = frame.get("elapsed_ms")
        _validate_elapsed_ms(elapsed_ms)
        if elapsed_ms < previous_elapsed:
            raise ReplayError("リプレイの経過時間順が不正です。")
        previous_elapsed = elapsed_ms
        _validated_label(frame.get("label"))
        snapshot = frame.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ReplayError(f"リプレイのフレーム {sequence} に状態がありません。")
        _validate_snapshot_size(snapshot, sequence)
        snapshot_version = snapshot.get("version")
        if (
            snapshot.get("format") != SAVE_FORMAT
            or isinstance(snapshot_version, bool)
            or not isinstance(snapshot_version, int)
            or snapshot_version != SAVE_VERSION
        ):
            raise ReplayError(
                f"リプレイのフレーム {sequence} のセーブ形式が不正です。"
            )
        identity = _snapshot_identity(snapshot, sequence)
        if expected_identity is None:
            expected_identity = identity
        elif identity != expected_identity:
            raise ReplayError("異なる対局のフレームが混在しています。")

    if _metadata_identity(metadata) != expected_identity:
        raise ReplayError("リプレイのメタデータと対局状態が一致しません。")


def _snapshot_identity(snapshot: Dict[str, Any], sequence: int) -> Tuple[Any, ...]:
    board = snapshot.get("board")
    rules = snapshot.get("rules")
    players = snapshot.get("players")
    if not isinstance(board, dict) or not isinstance(rules, dict) or not isinstance(players, list):
        raise ReplayError(f"リプレイのフレーム {sequence} の対局情報が不正です。")
    board_mode = board.get("mode")
    board_seed = board.get("seed")
    victory_target = rules.get("victory_point_target")
    if (
        board_mode not in ("constrained", "fully_random")
        or isinstance(board_seed, bool)
        or not isinstance(board_seed, int)
        or isinstance(victory_target, bool)
        or not isinstance(victory_target, int)
    ):
        raise ReplayError(f"リプレイのフレーム {sequence} の対局設定が不正です。")
    seats = []
    for player in players:
        if (
            not isinstance(player, dict)
            or not isinstance(player.get("name"), str)
            or not isinstance(player.get("is_ai"), bool)
        ):
            raise ReplayError(f"リプレイのフレーム {sequence} の参加者情報が不正です。")
        seats.append((player["name"], player["is_ai"]))
    return board_mode, board_seed, victory_target, tuple(seats)


def _metadata_identity(metadata: Dict[str, Any]) -> Tuple[Any, ...]:
    players = metadata.get("players")
    if not isinstance(players, list):
        raise ReplayError("リプレイの参加者メタデータが不正です。")
    seats = []
    for player in players:
        if (
            not isinstance(player, dict)
            or not isinstance(player.get("name"), str)
            or not isinstance(player.get("is_ai"), bool)
        ):
            raise ReplayError("リプレイの参加者メタデータが不正です。")
        seats.append((player["name"], player["is_ai"]))
    return (
        metadata.get("board_mode"),
        metadata.get("board_seed"),
        metadata.get("victory_point_target"),
        tuple(seats),
    )


def _validated_metadata(metadata: Any) -> Dict[str, Any]:
    if not isinstance(metadata, dict) or not all(
        isinstance(key, str) for key in metadata
    ):
        raise ReplayError("リプレイのメタデータが不正です。")
    try:
        encoded = json.dumps(
            metadata,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ReplayError(f"リプレイのメタデータが不正です: {exc}") from exc
    if len(encoded) > MAX_REPLAY_METADATA_BYTES:
        raise ReplayError("リプレイのメタデータが大きすぎます。")
    return copy.deepcopy(metadata)


def _validate_snapshot_size(snapshot: Dict[str, Any], sequence: int) -> None:
    try:
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ReplayError(
            f"リプレイのフレーム {sequence} の状態が不正です: {exc}"
        ) from exc
    if len(encoded) > MAX_REPLAY_FRAME_BYTES:
        raise ReplayError(
            f"リプレイのフレーム {sequence} がサイズ上限を超えています。"
        )


def _validated_label(label: Any) -> str:
    if not isinstance(label, str):
        raise ReplayError("リプレイのフレーム名が不正です。")
    if len(label) > MAX_FRAME_LABEL_LENGTH:
        raise ReplayError(
            f"フレーム名は {MAX_FRAME_LABEL_LENGTH} 文字以内にしてください。"
        )
    return label


def _validate_elapsed_ms(elapsed_ms: Any) -> None:
    if (
        isinstance(elapsed_ms, bool)
        or not isinstance(elapsed_ms, int)
        or not 0 <= elapsed_ms <= MAX_REPLAY_ELAPSED_MS
    ):
        raise ReplayError("リプレイの経過時間が不正です。")


def _normalise_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ReplayError("リプレイの日時が不正です。")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return _normalise_datetime(value).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _validate_created_at(value: Any) -> None:
    if not isinstance(value, str) or len(value) > 64:
        raise ReplayError("リプレイの作成日時が不正です。")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReplayError("リプレイの作成日時が不正です。") from exc
    if parsed.tzinfo is None:
        raise ReplayError("リプレイの作成日時にタイムゾーンがありません。")


def _capture_runtime_state(game: Any) -> Dict[str, Any]:
    state = {
        "attributes": {},
        "dice_overlay": {},
        "feedback": None,
        "has_feedback": False,
    }
    for name in (
        "ai_next_action_at",
        "seed_input_active",
        "pending_dice_context",
        "pending_dice_roll",
        "pending_dice_player_name",
    ):
        if hasattr(game, name):
            state["attributes"][name] = copy.deepcopy(getattr(game, name))

    feedback = getattr(game, "feedback", None)
    if feedback is not None and hasattr(feedback, "_current"):
        state["has_feedback"] = True
        state["feedback"] = copy.deepcopy(feedback._current)

    overlay = getattr(game, "dice_overlay", None)
    if overlay is not None:
        for name in (
            "state",
            "result_values",
            "result_total",
            "title",
            "subtitle",
            "roll_started_at",
            "result_started_at",
        ):
            if hasattr(overlay, name):
                state["dice_overlay"][name] = copy.deepcopy(
                    getattr(overlay, name)
                )
    return state


def _has_unresolved_dice(game: Any) -> bool:
    has_animation = getattr(game, "has_active_dice_animation", None)
    dice_overlay = getattr(game, "dice_overlay", None)
    return bool(
        (callable(has_animation) and has_animation())
        or getattr(game, "pending_dice_context", None) is not None
        or getattr(game, "pending_dice_roll", None) is not None
        or getattr(dice_overlay, "is_active", False)
    )


def _restore_runtime_state(game: Any, state: Dict[str, Any]) -> None:
    for name, value in state["attributes"].items():
        setattr(game, name, value)
    feedback = getattr(game, "feedback", None)
    if state["has_feedback"] and feedback is not None:
        feedback._current = state["feedback"]
    overlay = getattr(game, "dice_overlay", None)
    if overlay is not None:
        for name, value in state["dice_overlay"].items():
            setattr(overlay, name, value)


def _close_scratch_game(game: Any) -> None:
    """Release resources owned by a game created solely for validation."""

    close = getattr(game, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass
        return
    audio = getattr(game, "audio", None)
    stop_audio = getattr(audio, "stop", None)
    if callable(stop_audio):
        try:
            stop_audio()
        except Exception:
            pass
