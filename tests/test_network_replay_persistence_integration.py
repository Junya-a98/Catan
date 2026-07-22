from __future__ import annotations

import pytest

from game.lan_controller import (
    ControllerPersistenceError,
    LanControllerError,
    LanServerController,
)
from game.network_protocol import NETWORK_PROTOCOL_VERSION, build_game_command
from game.network_replay import NetworkReplayStore
from game.network_replay_store import SQLiteNetworkReplayStore
from game.server_state import SQLiteRoomAuthorityStore


WALL_CLOCK_MS = 1_800_000_000_000
LOBBY_CLOCK = 1_000.0


def message(message_type, **payload):
    return {
        "type": message_type,
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        **payload,
    }


def settings():
    return {
        "player_count": 2,
        "victory_target": 10,
        "board_mode": "constrained",
        "board_seed": 86712347,
        "ai_player_count": 0,
    }


def controller(state_store, replay_store, *, wall_clock_ms=WALL_CLOCK_MS):
    return LanServerController(
        state_store=state_store,
        replay_store=replay_store,
        wall_clock_ms=lambda: wall_clock_ms,
        lobby_clock=lambda: LOBBY_CLOCK,
    )


def welcome(outbound):
    return next(
        item.message for item in outbound if item.message["type"] == "session_welcome"
    )


def create_started_room(instance):
    created = instance.handle(
        "host",
        message("create_room", display_name="Host", settings=settings()),
    )
    host_welcome = welcome(created)
    room_code = host_welcome["room_code"]
    joined = instance.handle(
        "guest",
        message(
            "join_room",
            room_code=room_code,
            display_name="Guest",
            role="player",
        ),
    )
    viewer_joined = instance.handle(
        "viewer",
        message(
            "join_room",
            room_code=room_code,
            display_name="Viewer",
            role="spectator",
        ),
    )
    instance.handle("host", message("set_ready", ready=True))
    instance.handle("guest", message("set_ready", ready=True))
    instance.handle("host", message("start_game"))
    return (
        room_code,
        host_welcome["reconnect_token"],
        welcome(joined)["reconnect_token"],
        welcome(viewer_joined)["reconnect_token"],
    )


def reconnect(instance, connection_id, room_code, token):
    restored = instance.handle(
        connection_id,
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=token,
        ),
    )
    return welcome(restored)


def start_restored_waiting_room(instance, room_code):
    instance.handle(
        "guest",
        message(
            "join_room",
            room_code=room_code,
            display_name="Guest",
            role="player",
        ),
    )
    instance.handle("host-restored", message("set_ready", ready=True))
    instance.handle("guest", message("set_ready", ready=True))
    instance.handle("host-restored", message("start_game"))


class FailingRevisionReplay:
    def __init__(self, inner, *fail_revisions):
        self.inner = inner
        self.fail_revisions = set(fail_revisions)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def capture_game(self, room_code, game, *, revision, label=None):
        if revision in self.fail_revisions:
            raise RuntimeError("simulated replay write failure")
        return self.inner.capture_game(
            room_code,
            game,
            revision=revision,
            label=label,
        )


def test_restart_preserves_full_player_and_spectator_replay_history(tmp_path):
    state_path = tmp_path / "authority.sqlite3"
    replay_path = tmp_path / "network-replay.sqlite3"
    state_a = SQLiteRoomAuthorityStore(state_path)
    replay_a = SQLiteNetworkReplayStore(replay_path)
    first = controller(state_a, replay_a)
    room_code, host_token, _guest_token, viewer_token = create_started_room(first)
    accepted = first.handle(
        "host",
        build_game_command(
            sequence=0,
            expected_revision=0,
            command="roll_dice",
        ),
    )
    assert accepted[0].message["accepted"] is True
    before = replay_a.export_room_authority(room_code)
    assert [frame["revision"] for frame in before["frames"]] == [0, 1]
    state_a.close()
    replay_a.close()

    state_b = SQLiteRoomAuthorityStore(state_path)
    replay_b = SQLiteNetworkReplayStore(replay_path)
    try:
        second = controller(
            state_b,
            replay_b,
            wall_clock_ms=WALL_CLOCK_MS + 1_000,
        )
        reconnect(second, "host-restored", room_code, host_token)
        reconnect(second, "viewer-restored", room_code, viewer_token)

        after = replay_b.export_room_authority(room_code)
        assert [frame["revision"] for frame in after["frames"]] == [0, 1]
        assert [frame["label"] for frame in after["frames"]] == [
            frame["label"] for frame in before["frames"]
        ]
        assert [frame["elapsed_ms"] for frame in after["frames"]] == [
            frame["elapsed_ms"] for frame in before["frames"]
        ]
        assert after["truncated"] is False

        host_frame = second.replay_frame_for_connection("host-restored", 0)
        viewer_frame = second.replay_frame_for_connection("viewer-restored", 0)
        assert host_frame["controls"]["frame_count"] == 2
        assert host_frame["snapshot"]["state"]["players"][0]["resources"] is not None
        assert host_frame["snapshot"]["state"]["players"][1]["resources"] is None
        assert all(
            player["resources"] is None
            for player in viewer_frame["snapshot"]["state"]["players"]
        )
    finally:
        replay_b.close()
        state_b.close()


def test_replay_write_failure_keeps_game_commit_and_restart_marks_gap(tmp_path):
    state_path = tmp_path / "authority.sqlite3"
    replay_path = tmp_path / "network-replay.sqlite3"
    state_a = SQLiteRoomAuthorityStore(state_path)
    durable_replay = SQLiteNetworkReplayStore(replay_path)
    replay_fault = FailingRevisionReplay(durable_replay, 1)
    first = controller(state_a, replay_fault)
    room_code, host_token, _guest_token, _viewer_token = create_started_room(first)

    accepted = first.handle(
        "host",
        build_game_command(
            sequence=0,
            expected_revision=0,
            command="roll_dice",
        ),
    )

    assert accepted[0].message["accepted"] is True
    assert first._rooms[room_code].game_revision == 1
    assert state_a.get_room_by_code(room_code).authority["match"]["game_revision"] == 1
    assert durable_replay.latest_revision(room_code) == 0
    state_a.close()
    durable_replay.close()

    state_b = SQLiteRoomAuthorityStore(state_path)
    replay_b = SQLiteNetworkReplayStore(replay_path)
    try:
        second = controller(
            state_b,
            replay_b,
            wall_clock_ms=WALL_CLOCK_MS + 1_000,
        )
        reconnect(second, "host-restored", room_code, host_token)
        archive = replay_b.export_room_authority(room_code)
        assert [frame["revision"] for frame in archive["frames"]] == [0, 1]
        assert archive["frames"][-1]["label"] == "サーバー再起動から再開"
        assert archive["truncated"] is True
    finally:
        replay_b.close()
        state_b.close()


def test_replay_ahead_of_game_authority_fails_controller_startup(tmp_path):
    state_path = tmp_path / "authority.sqlite3"
    replay_path = tmp_path / "network-replay.sqlite3"
    state_a = SQLiteRoomAuthorityStore(state_path)
    replay_a = SQLiteNetworkReplayStore(replay_path)
    first = controller(state_a, replay_a)
    room_code, _host_token, _guest_token, _viewer_token = create_started_room(first)
    replay_a.capture_game(room_code, first._rooms[room_code].game, revision=1)
    assert replay_a.latest_revision(room_code) == 1
    state_a.close()
    replay_a.close()

    state_b = SQLiteRoomAuthorityStore(state_path)
    replay_b = SQLiteNetworkReplayStore(replay_path)
    try:
        with pytest.raises(ControllerPersistenceError):
            controller(
                state_b,
                replay_b,
                wall_clock_ms=WALL_CLOCK_MS + 1_000,
            )
    finally:
        replay_b.close()
        state_b.close()


def test_waiting_room_cannot_read_any_stale_replay_payload(tmp_path):
    state = SQLiteRoomAuthorityStore(tmp_path / "authority.sqlite3")

    class StaleReplay:
        def __init__(self):
            self.frame_reads = 0

        def capture_game(self, *_args, **_kwargs):
            return None

        def frame_payload(self, *_args, **_kwargs):
            self.frame_reads += 1
            return {"private": "stale replay from a reused code"}

        def result_payload(self, *_args, **_kwargs):
            return {"private": "stale replay from a reused code"}

        def discard_room(self, *_args, **_kwargs):
            return False

    replay = StaleReplay()
    instance = controller(state, replay)
    instance.handle(
        "host",
        message("create_room", display_name="Host", settings=settings()),
    )

    with pytest.raises(LanControllerError) as stopped:
        instance.replay_frame_for_connection("host", 0)

    assert stopped.value.code == "game_not_started"
    assert replay.frame_reads == 0
    state.close()


def test_durable_binding_failure_blocks_replay_read_instead_of_falling_back(
    tmp_path,
):
    state = SQLiteRoomAuthorityStore(tmp_path / "authority.sqlite3")

    class BindFaultReplay:
        def __init__(self):
            self.inner = NetworkReplayStore()
            self.fail_binding = False

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def bind_room(self, _room_code, _room_id):
            if self.fail_binding:
                raise RuntimeError("simulated replay binding failure")

    replay = BindFaultReplay()
    instance = controller(state, replay)
    create_started_room(instance)
    replay.fail_binding = True

    with pytest.raises(LanControllerError) as stopped:
        instance.replay_frame_for_connection("host", 0)

    assert stopped.value.code == "replay_unavailable"
    assert "simulated" not in str(stopped.value)
    state.close()


def test_waiting_authority_with_stale_frames_stays_blocked_after_game_start(
    tmp_path,
):
    state_path = tmp_path / "authority.sqlite3"
    replay_path = tmp_path / "network-replay.sqlite3"
    state_a = SQLiteRoomAuthorityStore(state_path)
    replay_a = SQLiteNetworkReplayStore(replay_path)
    first = controller(state_a, replay_a)
    created = first.handle(
        "host",
        message("create_room", display_name="New Host", settings=settings()),
    )
    host_welcome = welcome(created)
    room_code = host_welcome["room_code"]
    context = first._rooms[room_code]
    stale_game = first._game_factory(
        context.lobby.settings,
        ("Old Host", "Old Guest"),
    )
    replay_a.bind_room(room_code, context.authority_room_id)
    replay_a.capture_game(room_code, stale_game, revision=0)
    state_a.close()
    replay_a.close()

    state_b = SQLiteRoomAuthorityStore(state_path)
    replay_b = SQLiteNetworkReplayStore(replay_path)
    try:
        second = controller(
            state_b,
            replay_b,
            wall_clock_ms=WALL_CLOCK_MS + 1_000,
        )
        reconnect(
            second,
            "host-restored",
            room_code,
            host_welcome["reconnect_token"],
        )
        start_restored_waiting_room(second, room_code)

        with pytest.raises(LanControllerError) as stopped:
            second.replay_frame_for_connection("host-restored", 0)

        assert stopped.value.code == "replay_unavailable"
        assert second._rooms[room_code].replay_blocked is True
    finally:
        replay_b.close()
        state_b.close()


def test_equal_revision_replay_fork_is_disabled_after_restore(tmp_path):
    state_path = tmp_path / "authority.sqlite3"
    replay_path = tmp_path / "network-replay.sqlite3"
    state_a = SQLiteRoomAuthorityStore(state_path)
    replay_a = SQLiteNetworkReplayStore(replay_path)
    first = controller(state_a, replay_a)
    room_code, host_token, _guest_token, _viewer_token = create_started_room(first)
    context = first._rooms[room_code]
    stale_game = first._game_factory(
        context.lobby.settings,
        ("Old Host", "Old Guest"),
    )
    assert replay_a.discard_room(room_code) is True
    replay_a.bind_room(room_code, context.authority_room_id)
    replay_a.capture_game(room_code, stale_game, revision=0)
    state_a.close()
    replay_a.close()

    state_b = SQLiteRoomAuthorityStore(state_path)
    replay_b = SQLiteNetworkReplayStore(replay_path)
    try:
        second = controller(
            state_b,
            replay_b,
            wall_clock_ms=WALL_CLOCK_MS + 1_000,
        )
        reconnect(second, "host-restored", room_code, host_token)

        with pytest.raises(LanControllerError) as stopped:
            second.replay_frame_for_connection("host-restored", 0)

        assert stopped.value.code == "replay_unavailable"
        assert second._rooms[room_code].replay_blocked is True
    finally:
        replay_b.close()
        state_b.close()
