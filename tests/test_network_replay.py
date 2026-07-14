import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from game.network_replay import (
    NETWORK_REPLAY_FORMAT,
    NetworkReplayError,
    NetworkReplayStore,
)


PRIVATE_FIELDS = (
    "resources",
    "development_cards",
    "new_development_cards",
    "victory_point_cards",
)


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


def player(name, resource_count, *, private=True):
    return {
        "name": name,
        "resources": {"WOOD": resource_count} if private else None,
        "resource_total": resource_count,
        "development_cards": {"KNIGHT": 1} if private else None,
        "new_development_cards": {"VICTORY_POINT": 1} if private else None,
        "victory_point_cards": 1 if private else None,
    }


def snapshot(
    revision,
    viewer,
    *,
    title=None,
    finished=False,
    leaky_player=None,
    trade_editor=None,
    trade_give=0,
):
    players = [
        player("Alice", revision + 2, private=viewer == 0),
        player("Bob", revision + 4, private=viewer == 1),
    ]
    if leaky_player is not None:
        players[leaky_player]["resources"] = {"WOOD": 99}
    return {
        "type": "state_snapshot",
        "protocol_version": 1,
        "revision": revision,
        "viewer_player_index": viewer,
        "board_manifest": {"format": "catan-board-manifest", "revision": revision},
        "command_options": [{"command": "roll_dice", "args": {}}],
        "state": {
            "players": players,
            "phase": {
                "name": "finished" if finished else "main",
                "special_phase": (
                    "domestic_trade_edit" if trade_editor is not None else None
                ),
            },
            "domestic_trade": {
                "editor": trade_editor,
                "give": {"WOOD": trade_give},
                "receive": {"BRICK": trade_give},
            },
            "development_deck": {"remaining": 20},
            "match_metrics": {
                "players": [{"player_id": "seat-1"}, {"player_id": "seat-2"}],
                "important_events": [{"sequence": 99, "title": "large history"}],
                "point_checkpoints": [{"sequence": 99, "points": {}}],
            },
            "history": {
                "latest_event": {
                    "title": title or f"操作 {revision}",
                    "detail": "",
                },
                "log_messages": [f"log {index}" for index in range(20)],
                "public_gain_history": {"Alice": [{"WOOD": 1}]},
                "turn_summary_entries": [str(index) for index in range(10)],
            },
        },
    }


def metrics(*, events=(), checkpoints=()):
    return {
        "important_events": [
            {"sequence": sequence, "title": title} for sequence, title in events
        ],
        "point_checkpoints": [
            {
                "sequence": sequence,
                "semantic_event": label,
                "points": {"seat-1": sequence + 2, "seat-2": 2},
            }
            for sequence, label in checkpoints
        ],
    }


def result_document():
    return {
        "format": "catan-match-result",
        "version": 1,
        "source": "match_metrics",
        "completed": True,
        "board": {"mode": "constrained", "seed": 42},
        "victory_target": 5,
        "winner": {"seat": 1, "name": "Alice"},
        "standings": [
            {
                "rank": 1,
                "seat": 1,
                "name": "Alice",
                "victory_points": 5,
                "builds": {"roads": 3, "settlements": 2, "cities": 1},
                "trades": {"bank": 2, "domestic": 1},
                "luck_index": 112.5,
                "resources": {"WOOD": 99},
            },
            {
                "rank": 2,
                "seat": 2,
                "name": "Bob",
                "victory_points": 3,
                "builds": {"roads": 2, "settlements": 2, "cities": 0},
                "trades": {"bank": 1, "domestic": 1},
                "luck_index": 88.0,
            },
        ],
        "vp_progression": [
            {
                "sequence": 0,
                "replay_frame_index": 999,
                "elapsed_ms": None,
                "label": "開拓地",
                "scores": [
                    {"seat": 1, "victory_points": 2},
                    {"seat": 2, "victory_points": 2},
                ],
            },
            {
                "sequence": 1,
                "replay_frame_index": 999,
                "elapsed_ms": None,
                "label": "勝利",
                "scores": [
                    {"seat": 1, "victory_points": 5},
                    {"seat": 2, "victory_points": 3},
                ],
            },
        ],
        "timeline_unit": "イベント",
        "important_events": [
            {
                "sequence": 0,
                "replay_frame_index": 999,
                "elapsed_ms": None,
                "category": "build",
                "title": "Aliceが開拓地を建設",
                "detail": "",
                "level": "info",
            },
            {
                "sequence": 1,
                "replay_frame_index": 999,
                "elapsed_ms": None,
                "category": "victory",
                "title": "Aliceの勝利",
                "detail": "5 VP",
                "level": "success",
            },
        ],
        "replay": {"available": False, "frame_count": 0},
        "private_resources": {"Alice": {"WOOD": 99}},
    }


def test_capture_game_builds_every_seat_and_spectator_without_private_leaks():
    calls = []

    def build_snapshot(game, *, viewer_player_index, revision):
        calls.append(viewer_player_index)
        return snapshot(
            revision,
            viewer_player_index,
            title="Aliceの勝利",
            finished=True,
        )

    game = SimpleNamespace(
        players=[object(), object()],
        phase="finished",
        match_metrics=metrics(events=((0, "Aliceの勝利"),), checkpoints=((0, "勝利"),)),
        match_result=result_document(),
    )
    store = NetworkReplayStore(
        snapshot_builder=build_snapshot,
        result_builder=lambda current_game: current_game.match_result,
    )

    store.capture_game("ABC123", game, revision=7)

    assert calls == [None, 0, 1]
    own = store.frame_payload("ABC123", viewer_player_index=0, frame_index=0)
    other = own["snapshot"]["state"]["players"][1]
    assert own["read_only"] is True
    assert own["snapshot"]["command_options"] == []
    assert own["snapshot"]["state"]["players"][0]["resources"] == {"WOOD": 9}
    assert all(other[field] is None for field in PRIVATE_FIELDS)

    spectator = store.frame_payload("ABC123", viewer_player_index=None, frame_index=0)
    assert all(
        player_state["resources"] is None
        for player_state in spectator["snapshot"]["state"]["players"]
    )
    assert spectator["controls"] == {
        "frame_count": 1,
        "frame_index": 0,
        "revision": 7,
        "elapsed_ms": 0,
        "label": "Aliceの勝利",
        "can_previous": False,
        "can_next": False,
    }


def test_default_capture_reuses_network_snapshot_privacy():
    from game.game import CatanGame

    game = CatanGame(
        board_seed=1234,
        ai_player_count=0,
        ai_action_delay_ms=0,
        headless=True,
    )
    resource = next(iter(game.players[0].resources))
    game.players[0].resources[resource] = 3
    game.players[1].resources[resource] = 4
    store = NetworkReplayStore(max_frames=2)

    store.capture_game("LIVE01", game, revision=0)

    player = store.frame_payload("LIVE01", viewer_player_index=0, frame_index=0)
    spectator = store.frame_payload("LIVE01", viewer_player_index=None, frame_index=0)
    assert player["snapshot"]["state"]["players"][0]["resources"] is not None
    assert player["snapshot"]["state"]["players"][1]["resources"] is None
    assert all(
        row["resources"] is None for row in spectator["snapshot"]["state"]["players"]
    )
    assert (
        player["snapshot"]["board_manifest"] == spectator["snapshot"]["board_manifest"]
    )

    # A final result is rebuilt from authority; the desktop UI cache is not a
    # prerequisite for browser results.
    game.phase = "finished"
    game.winner = game.players[0]
    game.match_result = None
    store.capture_game("LIVE01", game, revision=1)
    result = store.result_payload("LIVE01", viewer_player_index=0)["result"]
    assert result["completed"] is True
    assert result["winner"] == {"seat": 1, "name": game.players[0].name}
    assert result["replay"] == {"available": True, "frame_count": 2}


def test_bounded_history_relinks_events_and_vp_progression_to_retained_frames():
    clock = Clock()
    store = NetworkReplayStore(max_frames=2, clock=clock)
    source_result = result_document()
    source_before = copy.deepcopy(source_result)

    store.record_snapshot(
        "ROOM42",
        snapshot(0, 0, title="対局開始"),
        metrics=metrics(
            events=((0, "Aliceが開拓地を建設"),),
            checkpoints=((0, "開拓地"),),
        ),
    )
    clock.value += 0.5
    store.record_snapshot(
        "ROOM42",
        snapshot(1, 0, title="中盤"),
        metrics=metrics(
            events=((0, "Aliceが開拓地を建設"),),
            checkpoints=((0, "開拓地"),),
        ),
    )
    clock.value += 0.75
    store.record_snapshot(
        "ROOM42",
        snapshot(2, 0, title="Aliceの勝利", finished=True),
        metrics=metrics(
            events=(
                (0, "Aliceが開拓地を建設"),
                (1, "Aliceの勝利"),
            ),
            checkpoints=((0, "開拓地"), (1, "勝利")),
        ),
        result=source_result,
    )

    first = store.frame_payload("ROOM42", viewer_player_index=0, frame_index=0)
    last = store.frame_payload("ROOM42", viewer_player_index=0, frame_index=1)
    assert first["controls"]["revision"] == 1
    assert first["controls"]["elapsed_ms"] == 500
    assert last["controls"] == {
        "frame_count": 2,
        "frame_index": 1,
        "revision": 2,
        "elapsed_ms": 1_250,
        "label": "Aliceの勝利",
        "can_previous": True,
        "can_next": False,
    }

    payload = store.result_payload("ROOM42", viewer_player_index=0)
    assert payload["format"] == NETWORK_REPLAY_FORMAT
    assert payload["replay"] == {
        "available": True,
        "frame_count": 2,
        "truncated": True,
        "first_revision": 1,
        "last_revision": 2,
        "initial_frame_index": 0,
        "final_frame_index": 1,
    }
    result = payload["result"]
    # Event/checkpoint zero belonged to evicted revision zero.  It must not
    # accidentally jump to the new frame zero after the window shifts.
    assert [event["replay_frame_index"] for event in result["important_events"]] == [
        None,
        1,
    ]
    assert [point["replay_frame_index"] for point in result["vp_progression"]] == [
        None,
        1,
    ]
    assert result["important_events"][1]["elapsed_ms"] == 1_250
    assert result["replay"] == {"available": True, "frame_count": 2}
    assert "private_resources" not in result
    assert "resources" not in result["standings"][0]
    assert source_result == source_before
    json.dumps(payload, ensure_ascii=False, allow_nan=False)


def test_same_revision_merges_viewers_and_returns_defensive_copies():
    store = NetworkReplayStore()
    first = snapshot(3, 0, title="Player 0")
    second = snapshot(3, 1, title="Player 1")

    store.record_snapshot("MERGE1", first)
    store.record_snapshot("MERGE1", second)

    player0 = store.frame_payload("MERGE1", viewer_player_index=0, frame_index=0)
    player1 = store.frame_payload("MERGE1", viewer_player_index=1, frame_index=0)
    assert player0["controls"]["frame_count"] == 1
    assert player1["snapshot"]["state"]["players"][1]["resources"] == {"WOOD": 7}
    player0["snapshot"]["state"]["players"][0]["resources"]["WOOD"] = 999
    again = store.frame_payload("MERGE1", viewer_player_index=0, frame_index=0)
    assert again["snapshot"]["state"]["players"][0]["resources"] == {"WOOD": 5}
    assert again["snapshot"]["state"]["history"]["log_messages"] == []
    assert again["snapshot"]["state"]["history"]["public_gain_history"] == {}
    assert again["snapshot"]["state"]["history"]["turn_summary_entries"] == [
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
    ]
    assert again["snapshot"]["state"]["match_metrics"]["important_events"] == []


def test_player_snapshot_derives_safe_spectator_and_hides_trade_draft():
    store = NetworkReplayStore()
    editor = snapshot(4, 0, trade_editor=0, trade_give=2)

    store.record_snapshot("TRADE1", editor)

    own = store.frame_payload("TRADE1", viewer_player_index=0, frame_index=0)
    viewer = store.frame_payload("TRADE1", viewer_player_index=None, frame_index=0)
    assert own["snapshot"]["state"]["domestic_trade"]["give"] == {"WOOD": 2}
    assert viewer["snapshot"]["state"]["domestic_trade"]["give"] == {"WOOD": 0}
    assert viewer["snapshot"]["state"]["domestic_trade"]["receive"] == {"BRICK": 0}


def test_rejects_private_leak_stale_revision_and_unavailable_result():
    store = NetworkReplayStore()
    with pytest.raises(NetworkReplayError) as leaked:
        store.record_snapshot("SAFE01", snapshot(0, None, leaky_player=1))
    assert leaked.value.code == "private_state_leak"

    with pytest.raises(NetworkReplayError) as trade_leak:
        store.record_snapshot(
            "SAFE02",
            snapshot(0, None, trade_editor=0, trade_give=2),
        )
    assert trade_leak.value.code == "private_state_leak"

    store.record_snapshot("SAFE01", snapshot(2, 0))
    with pytest.raises(NetworkReplayError) as stale:
        store.record_snapshot("SAFE01", snapshot(1, 0))
    assert stale.value.code == "stale_revision"

    with pytest.raises(NetworkReplayError) as unfinished:
        store.result_payload("SAFE01", viewer_player_index=0)
    assert unfinished.value.code == "match_not_finished"

    with pytest.raises(NetworkReplayError) as frame:
        store.frame_payload("SAFE01", viewer_player_index=1, frame_index=0)
    assert frame.value.code == "viewer_history_unavailable"


def test_room_limit_is_bounded_and_discard_removes_private_history():
    store = NetworkReplayStore(max_rooms=2)
    for room in ("ROOM01", "ROOM02", "ROOM03"):
        store.record_snapshot(room, snapshot(0, 0))

    assert store.room_codes == ("ROOM02", "ROOM03")
    assert store.discard_room("ROOM02") is True
    assert store.discard_room("ROOM02") is False
    with pytest.raises(NetworkReplayError) as missing:
        store.frame_payload("ROOM02", viewer_player_index=0, frame_index=0)
    assert missing.value.code == "replay_not_found"


def test_transport_independent_module_import_does_not_load_pygame():
    project_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project_root / "python")

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import game.network_replay; assert 'pygame' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
