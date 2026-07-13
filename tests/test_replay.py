import json
import os
import random
from collections import Counter
from datetime import datetime, timezone

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

import game.replay as replay_module
from game.game import CatanGame
from game.persistence import serialize_game
from game.replay import (
    REPLAY_FORMAT,
    REPLAY_VISIBILITY,
    REPLAY_VERSION,
    ReplayError,
    ReplayRecorder,
    compact_game_snapshot,
    find_latest_replay,
    load_replay,
    restore_replay_frame,
    save_replay,
)
from game.resources import ResourceType


@pytest.fixture
def game():
    pygame.init()
    pygame.display.set_mode((1, 1))
    instance = CatanGame(board_seed=5150, ai_player_count=1)
    instance.configure_players(2, reset_logs=False)
    yield instance
    instance.audio.stop()
    pygame.quit()


def give_resource(game, resource_type, amount=1):
    player = game.players[0]
    assert game.bank.withdraw(resource_type, amount)
    player.add_resource(resource_type, amount)


def test_replay_is_versioned_compact_atomic_and_latest_is_discoverable(
    game,
    tmp_path,
):
    game.log_messages.extend(f"長い履歴 {index}" for index in range(200))
    original_deck = list(game.development_deck)
    recorder = ReplayRecorder(metadata={"title": "確認用"})
    recorder.capture(game, label="開始", elapsed_ms=0)
    give_resource(game, ResourceType.WOOD)
    recorder.capture(game, label="木を獲得", elapsed_ms=250)

    replay_dir = tmp_path / "replays"
    timestamp = datetime(2026, 7, 13, 3, 4, 5, 123456, tzinfo=timezone.utc)
    first_path = recorder.save(replay_dir=replay_dir, now=timestamp)

    assert first_path.parent == replay_dir
    assert first_path.name == "catan-replay-20260713T030405123456Z.json"
    assert not list(replay_dir.glob("*.tmp"))

    document = json.loads(first_path.read_text(encoding="utf-8"))
    assert document["format"] == REPLAY_FORMAT
    assert document["version"] == REPLAY_VERSION
    assert document["visibility"] == REPLAY_VISIBILITY
    assert document["metadata"]["board_seed"] == 5150
    assert len(document["frames"]) == 2
    assert all(
        frame["snapshot"]["history"]["log_messages"] == []
        for frame in document["frames"]
    )
    assert all(
        frame["snapshot"]["history"]["public_gain_history"] == {}
        for frame in document["frames"]
    )
    stored_deck = document["frames"][0]["snapshot"]["development_deck"]
    assert stored_deck == sorted(stored_deck)
    assert Counter(stored_deck) == Counter(card.name for card in original_deck)

    second_path = save_replay(
        recorder.archive(),
        replay_dir=replay_dir,
        now=timestamp,
    )
    assert second_path.name == "catan-replay-20260713T030405123456Z-0001.json"
    os.utime(first_path, ns=(1, 1))
    os.utime(second_path, ns=(2, 2))
    (replay_dir / ".unfinished.tmp").write_text("partial", encoding="utf-8")

    assert find_latest_replay(replay_dir) == second_path


def test_load_validates_frames_without_changing_current_game_and_can_restore(
    game,
    tmp_path,
):
    recorder = ReplayRecorder()
    recorder.capture(game, label="初期状態", elapsed_ms=0)
    give_resource(game, ResourceType.BRICK, 2)
    recorder.capture(game, label="レンガを獲得", elapsed_ms=100)
    replay_path = recorder.save(tmp_path / "replay.json")

    give_resource(game, ResourceType.WHEAT)
    game.log_messages.append("検証前の現在状態")
    game.feedback.show("検証後も残る通知", now_ms=10, duration_ms=9999)
    feedback_before = game.feedback._current
    game.ai_next_action_at = 424_242
    random.seed(90817)
    random_state_before = random.getstate()
    before_validation = serialize_game(game)

    replay = load_replay(replay_path, validation_game=game)

    assert serialize_game(game) == before_validation
    assert game.feedback._current == feedback_before
    assert game.ai_next_action_at == 424_242
    assert random.getstate() == random_state_before
    restored = restore_replay_frame(game, replay, 1)
    assert restored.label == "レンガを獲得"
    assert compact_game_snapshot(game) == replay.frames[1].snapshot
    assert game.ai_next_action_at == 424_242
    assert random.getstate() == random_state_before


def test_structural_load_does_not_restore_every_frame(game, tmp_path, monkeypatch):
    recorder = ReplayRecorder(max_frames=40)
    for elapsed_ms in range(40):
        recorder.capture(game, label=f"frame-{elapsed_ms}", elapsed_ms=elapsed_ms)
    replay_path = recorder.save(tmp_path / "many-frames.json")

    def unexpected_restore(*args, **kwargs):
        raise AssertionError("structural load must not restore frames")

    monkeypatch.setattr(replay_module, "restore_game", unexpected_restore)

    replay = load_replay(replay_path)
    assert len(replay.frames) == 40


def test_trusted_seek_can_skip_revalidating_the_whole_archive(
    game,
    monkeypatch,
):
    recorder = ReplayRecorder()
    recorder.capture(game, label="開始", elapsed_ms=0)
    archive = recorder.archive()

    def unexpected_archive_validation(*args, **kwargs):
        raise AssertionError("trusted archive should not be revalidated")

    monkeypatch.setattr(
        replay_module,
        "_validate_archive",
        unexpected_archive_validation,
    )

    frame = restore_replay_frame(
        game,
        archive,
        0,
        validate_archive=False,
    )
    assert frame.label == "開始"


def test_tampered_frame_is_rejected_and_validation_game_is_rolled_back(
    game,
    tmp_path,
):
    recorder = ReplayRecorder()
    recorder.capture(game, elapsed_ms=0)
    replay_path = recorder.save(tmp_path / "tampered.json")
    document = json.loads(replay_path.read_text(encoding="utf-8"))
    document["frames"][0]["snapshot"]["bank"]["WOOD"] = 18
    replay_path.write_text(
        json.dumps(document, ensure_ascii=False),
        encoding="utf-8",
    )
    before = serialize_game(game)

    with pytest.raises(ReplayError, match="フレーム 0"):
        load_replay(replay_path, validation_game=game)

    assert serialize_game(game) == before


def test_frame_count_timing_and_sequence_are_bounded_and_validated(game, tmp_path):
    recorder = ReplayRecorder(max_frames=2)
    recorder.capture(game, elapsed_ms=0)
    recorder.capture(game, elapsed_ms=1)

    with pytest.raises(ReplayError, match="最大 2 フレーム"):
        recorder.capture(game, elapsed_ms=2)

    replacement = recorder.capture(
        game,
        label="終了",
        elapsed_ms=2,
        replace_last_if_full=True,
    )
    assert [frame.label for frame in recorder.frames] == ["ゲーム準備中", "終了"]
    assert replacement.sequence == 1

    replay_path = recorder.save(tmp_path / "bad-order.json")
    document = json.loads(replay_path.read_text(encoding="utf-8"))
    document["frames"][1]["sequence"] = 0
    replay_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ReplayError, match="フレーム順"):
        load_replay(replay_path)

    with pytest.raises(ReplayError, match="前のフレーム以上"):
        other = ReplayRecorder()
        other.capture(game, elapsed_ms=10)
        other.capture(game, elapsed_ms=9)


def test_frames_from_different_matches_cannot_be_mixed(game, tmp_path):
    recorder = ReplayRecorder()
    recorder.capture(game, label="開始", elapsed_ms=0)
    recorder.capture(game, label="続行", elapsed_ms=1)
    replay_path = recorder.save(tmp_path / "mixed.json")
    document = json.loads(replay_path.read_text(encoding="utf-8"))
    document["frames"][1]["snapshot"]["board"]["seed"] += 1
    replay_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ReplayError, match="異なる対局"):
        load_replay(replay_path)

    pending = ReplayRecorder()
    game.pending_dice_context = "main"
    with pytest.raises(ReplayError, match="ダイス演出中"):
        pending.capture(game, elapsed_ms=0)
    with pytest.raises(ReplayError, match="移動できません"):
        restore_replay_frame(game, recorder.archive(), 0)


@pytest.mark.parametrize(
    ("field", "expected_message"),
    (
        ("replay_version", "リプレイバージョン"),
        ("sequence", "フレーム順"),
        ("snapshot_version", "セーブ形式"),
    ),
)
def test_boolean_integer_fields_are_rejected(
    game,
    tmp_path,
    field,
    expected_message,
):
    recorder = ReplayRecorder()
    recorder.capture(game, elapsed_ms=0)
    document = recorder.archive().to_document()
    if field == "replay_version":
        document["version"] = True
    elif field == "sequence":
        document["frames"][0]["sequence"] = False
    else:
        document["frames"][0]["snapshot"]["version"] = True
    target = tmp_path / f"{field}.json"
    target.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ReplayError, match=expected_message):
        load_replay(target)


def test_load_reads_only_the_bounded_payload(game, tmp_path, monkeypatch):
    target = tmp_path / "oversized.json"
    target.write_bytes(b"x" * 33)
    monkeypatch.setattr(replay_module, "MAX_REPLAY_FILE_BYTES", 32)

    with pytest.raises(ReplayError, match="ファイルサイズ"):
        load_replay(target)


def test_failed_atomic_replace_removes_temporary_file(
    game,
    tmp_path,
    monkeypatch,
):
    recorder = ReplayRecorder()
    recorder.capture(game, elapsed_ms=0)
    target = tmp_path / "replays" / "failed.json"

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("game.replay.os.replace", fail_replace)

    with pytest.raises(ReplayError, match="保存に失敗"):
        recorder.save(target)

    assert not target.exists()
    assert not list(target.parent.glob("*.tmp"))
