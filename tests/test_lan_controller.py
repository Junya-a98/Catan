import json
import random

import pytest

from game.custom_map import CustomMapSpec
from game.development_cards import DevelopmentCardType
from game.game_board import GameBoard
from game.house_rules import HouseRules
from game.lan_controller import LanControllerError, LanServerController
from game.network_actions import NetworkActionError
from game.network_protocol import (
    NETWORK_PROTOCOL_VERSION,
    build_game_command,
    build_state_snapshot,
)
from game.resources import ResourceType


def message(message_type, **payload):
    return {
        "type": message_type,
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        **payload,
    }


def create_room(
    controller,
    connection="host",
    *,
    players=2,
    ai_players=0,
    ai_personality_mode="standard",
):
    settings = {
        "player_count": players,
        "victory_target": 5,
        "board_mode": "constrained",
        "board_seed": 4242,
    }
    if ai_players or ai_personality_mode != "standard":
        settings.update(
            ai_player_count=ai_players,
            ai_personality_mode=ai_personality_mode,
        )
    outbound = controller.handle(
        connection,
        message(
            "create_room",
            display_name="Host",
            settings=settings,
        ),
    )
    welcome = next(
        item.message for item in outbound if item.message["type"] == "session_welcome"
    )
    return welcome["room_code"], welcome["reconnect_token"], outbound


def test_frontier_room_replaces_submitted_seed_and_masks_authority_seed(monkeypatch):
    monkeypatch.setattr(
        "game.lan_controller.secrets.randbits",
        lambda bits: 999_999 if bits == 52 else 111,
    )
    controller = LanServerController()
    outbound = controller.handle(
        "host",
        message(
            "create_room",
            display_name="Host",
            settings={
                "player_count": 2,
                "victory_target": 5,
                "board_mode": "constrained",
                "board_seed": 4242,
                "variant": {
                    "version": 1,
                    "kind": "frontier",
                    "options": {
                        "initial_radius": 1,
                        "reveal_rule": "road_adjacent_v1",
                    },
                },
            },
        ),
    )
    lobby_message = next(
        item.message for item in outbound if item.message["type"] == "lobby_snapshot"
    )
    lobby = lobby_message["lobby"]
    room_code = lobby["room_code"]

    assert lobby["settings"]["board_seed"] == 0
    assert controller._rooms[room_code].lobby.settings.board_seed == 999_999


def join_player(controller, room_code, connection="guest", name="Guest"):
    outbound = controller.handle(
        connection,
        message(
            "join_room",
            room_code=room_code,
            display_name=name,
            role="player",
        ),
    )
    return next(
        item.message for item in outbound if item.message["type"] == "session_welcome"
    ), outbound


def ready_and_start(controller, room_code):
    controller.handle("host", message("set_ready", ready=True))
    controller.handle("guest", message("set_ready", ready=True))
    outbound = controller.handle("host", message("start_game"))
    assert any(item.message["type"] == "state_snapshot" for item in outbound)
    return outbound


def test_create_join_ready_start_and_viewer_specific_snapshots():
    controller = LanServerController()
    room_code, host_token, created = create_room(controller)
    guest_welcome, joined = join_player(controller, room_code)
    spectator = controller.handle(
        "viewer",
        message(
            "join_room",
            room_code=room_code,
            display_name="Viewer",
            role="spectator",
        ),
    )

    assert host_token
    assert guest_welcome["seat_index"] == 1
    assert all(
        host_token not in json.dumps(item.message)
        for item in created
        if item.message["type"] == "lobby_snapshot"
    )
    assert any(item.message["type"] == "lobby_snapshot" for item in joined)
    assert (
        next(
            item.message
            for item in spectator
            if item.message["type"] == "session_welcome"
        )["seat_index"]
        is None
    )

    ready_and_start(controller, room_code)
    host = controller.snapshot_for_connection("host")
    guest = controller.snapshot_for_connection("guest")
    viewer = controller.snapshot_for_connection("viewer")

    assert host["viewer_player_index"] == 0
    assert guest["viewer_player_index"] == 1
    assert viewer["viewer_player_index"] is None
    assert host["state"]["players"][0]["resources"] is not None
    assert host["state"]["players"][1]["resources"] is None
    assert all(player["resources"] is None for player in viewer["state"]["players"])
    assert host["command_options"] == [{"command": "roll_dice", "args": {}}]
    assert guest["command_options"] == []
    assert viewer["command_options"] == []


def test_authenticated_replay_frames_track_revisions_without_private_leaks():
    controller = LanServerController()
    room_code, _host_token, _created = create_room(controller)
    join_player(controller, room_code)
    controller.handle(
        "viewer",
        message(
            "join_room",
            room_code=room_code,
            display_name="Viewer",
            role="spectator",
        ),
    )
    ready_and_start(controller, room_code)

    initial_host = controller.replay_frame_for_connection("host", 0)
    initial_viewer = controller.replay_frame_for_connection("viewer", 0)
    assert initial_host["read_only"] is True
    assert initial_host["controls"]["revision"] == 0
    assert initial_host["snapshot"]["command_options"] == []
    assert initial_host["snapshot"]["state"]["players"][0]["resources"] is not None
    assert initial_host["snapshot"]["state"]["players"][1]["resources"] is None
    assert all(
        player["resources"] is None
        for player in initial_viewer["snapshot"]["state"]["players"]
    )

    controller.handle(
        "host",
        build_game_command(
            sequence=0,
            expected_revision=0,
            command="roll_dice",
        ),
    )
    latest = controller.replay_frame_for_connection("host", 1)
    assert latest["controls"]["frame_count"] == 2
    assert latest["controls"]["revision"] == 1
    with pytest.raises(LanControllerError):
        controller.replay_frame_for_connection("not-joined", 0)


def test_ai_only_opponents_fill_lobby_and_advance_as_authoritative_seats():
    controller = LanServerController()
    room_code, _host_token, created = create_room(
        controller,
        players=3,
        ai_players=2,
        ai_personality_mode="mixed",
    )
    lobby = next(
        item.message["lobby"]
        for item in created
        if item.message["type"] == "lobby_snapshot"
    )

    assert lobby["full"] is True
    assert lobby["can_start"] is False
    assert lobby["settings"]["ai_player_count"] == 2
    assert [member["display_name"] for member in lobby["members"]] == [
        "Host",
        "CPU1",
        "CPU2",
    ]
    assert [member.get("ai_personality") for member in lobby["members"]] == [
        None,
        "expansion",
        "trader",
    ]
    full = controller.handle(
        "late",
        message(
            "join_room",
            room_code=room_code,
            display_name="Late",
            role="player",
        ),
    )[0].message
    assert full["type"] == "request_error"
    assert full["code"] == "room_full"

    controller.handle("host", message("set_ready", ready=True))
    started = controller.handle("host", message("start_game"))
    assert any(item.message["type"] == "state_snapshot" for item in started)
    game = controller._rooms[room_code].game
    assert [player.is_ai for player in game.players] == [False, True, True]
    assert [player.name for player in game.players] == ["Host", "CPU1", "CPU2"]
    assert [player.ai_personality for player in game.players] == [
        "standard",
        "expansion",
        "trader",
    ]

    host_roll = controller.handle(
        "host",
        build_game_command(
            sequence=0,
            expected_revision=0,
            command="roll_dice",
        ),
    )
    assert host_roll[0].message["accepted"] is True
    before_tick_rng = random.getstate()

    advanced = controller.tick()

    ai_snapshot = next(
        item.message
        for item in advanced
        if item.connection_id == "host" and item.message["type"] == "state_snapshot"
    )
    assert ai_snapshot["revision"] == 2
    assert ai_snapshot["state"]["ai"]["status"]["player_name"] == "CPU1"
    assert ai_snapshot["state"]["ai"]["status"]["title"] == "初期ダイス"
    assert ai_snapshot["state"]["players"][1]["resources"] is None
    assert ai_snapshot["command_options"] == []
    assert random.getstate() == before_tick_rng
    replay = controller.replay_frame_for_connection("host", 2)
    assert replay["controls"]["frame_count"] == 3
    assert replay["controls"]["revision"] == 2


def test_ai_tick_step_limit_is_bounded_and_each_decision_has_a_revision():
    controller = LanServerController(ai_steps_per_tick=2)
    room_code, _host_token, _created = create_room(
        controller,
        players=3,
        ai_players=2,
    )
    controller.handle("host", message("set_ready", ready=True))
    controller.handle("host", message("start_game"))
    controller.handle(
        "host",
        build_game_command(
            sequence=0,
            expected_revision=0,
            command="roll_dice",
        ),
    )

    outbound = controller.tick()
    revisions = [
        item.message["revision"]
        for item in outbound
        if item.connection_id == "host" and item.message["type"] == "state_snapshot"
    ]

    assert revisions == [2, 3]
    assert controller.snapshot_for_connection("host")["revision"] == 3


def test_failed_ai_step_rolls_back_game_room_rng_and_global_rng():
    def failing_ai_step(game):
        game.bank.resources[ResourceType.WOOD] -= 1
        game.players[1].resources[ResourceType.WOOD] += 1
        random.random()
        raise RuntimeError("private AI failure")

    controller = LanServerController(ai_stepper=failing_ai_step)
    room_code, _host_token, _created = create_room(
        controller,
        players=2,
        ai_players=1,
    )
    controller.handle("host", message("set_ready", ready=True))
    controller.handle("host", message("start_game"))
    controller.handle(
        "host",
        build_game_command(
            sequence=0,
            expected_revision=0,
            command="roll_dice",
        ),
    )
    snapshot_before = controller.snapshot_for_connection("host")
    room_rng_before = controller._rooms[room_code].random_state
    caller_rng_before = random.getstate()

    assert controller.tick() == ()
    assert controller.snapshot_for_connection("host") == snapshot_before
    assert controller._rooms[room_code].random_state == room_rng_before
    assert random.getstate() == caller_rng_before


def test_custom_room_settings_reach_authority_and_public_board_identity():
    custom_map = CustomMapSpec.from_board(GameBoard(seed=8765))
    house_rules = HouseRules(
        skip_discard_on_seven=True,
        disabled_development_cards=frozenset({DevelopmentCardType.YEAR_OF_PLENTY}),
    )
    controller = LanServerController()
    created = controller.handle(
        "host",
        message(
            "create_room",
            display_name="Host",
            settings={
                "player_count": 2,
                "victory_target": 8,
                "board_mode": "custom",
                "board_seed": 8765,
                "custom_map": custom_map.to_document(),
                "house_rules": house_rules.to_document(),
            },
        ),
    )
    welcome = next(
        item.message for item in created if item.message["type"] == "session_welcome"
    )
    lobby = next(
        item.message["lobby"]
        for item in created
        if item.message["type"] == "lobby_snapshot"
    )
    assert lobby["settings"]["custom_map"] == custom_map.to_document()
    assert lobby["settings"]["house_rules"] == house_rules.to_document()

    join_player(controller, welcome["room_code"])
    started = ready_and_start(controller, welcome["room_code"])
    snapshot = next(
        item.message
        for item in started
        if item.connection_id == "host" and item.message["type"] == "state_snapshot"
    )

    assert snapshot["state"]["board"]["mode"] == "custom"
    assert (
        snapshot["board_manifest"]["custom_map_fingerprint"] == custom_map.fingerprint
    )
    assert snapshot["state"]["rules"]["house_rules"] == (house_rules.to_document())


@pytest.mark.parametrize(
    "settings_patch",
    [
        {"board_mode": "custom"},
        {"house_rules": {}},
        {"unexpected": True},
    ],
)
def test_create_room_rejects_malformed_custom_setting_boundaries(settings_patch):
    settings = {
        "player_count": 2,
        "victory_target": 8,
        "board_mode": "constrained",
        "board_seed": 8765,
        **settings_patch,
    }

    outbound = LanServerController().handle(
        "host",
        message(
            "create_room",
            display_name="Host",
            settings=settings,
        ),
    )

    assert [item.message["type"] for item in outbound] == ["request_error"]
    assert outbound[0].message["code"] == "invalid_request"


def test_command_options_follow_authoritative_actor_after_each_revision():
    controller = LanServerController()
    room_code, _host_token, _created = create_room(controller)
    join_player(controller, room_code)
    ready_and_start(controller, room_code)

    command = build_game_command(
        sequence=0,
        expected_revision=0,
        command="roll_dice",
    )
    outbound = controller.handle("host", command)
    host_snapshot = next(
        item.message
        for item in outbound
        if item.connection_id == "host" and item.message["type"] == "state_snapshot"
    )
    guest_snapshot = next(
        item.message
        for item in outbound
        if item.connection_id == "guest" and item.message["type"] == "state_snapshot"
    )

    assert host_snapshot["revision"] == 1
    assert host_snapshot["command_options"] == []
    assert guest_snapshot["command_options"] == [{"command": "roll_dice", "args": {}}]


def test_only_host_can_start_and_all_seats_must_be_ready():
    controller = LanServerController()
    room_code, _token, _outbound = create_room(controller)
    join_player(controller, room_code)

    not_host = controller.handle("guest", message("start_game"))[0].message
    assert not_host["type"] == "request_error"
    assert not_host["code"] == "forbidden"

    controller.handle("host", message("set_ready", ready=True))
    not_ready = controller.handle("host", message("start_game"))[0].message
    assert not_ready["code"] == "invalid_state"


def test_disconnect_and_reconnect_preserve_seat_and_command_identity():
    controller = LanServerController()
    room_code, _host_token, _created = create_room(controller)
    welcome, _joined = join_player(controller, room_code)
    token = welcome["reconnect_token"]

    disconnected = controller.disconnect("guest")
    assert any(item.message["type"] == "lobby_snapshot" for item in disconnected)
    reconnected = controller.handle(
        "guest-new",
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=token,
        ),
    )
    new_welcome = next(
        item.message
        for item in reconnected
        if item.message["type"] == "session_welcome"
    )
    assert new_welcome["seat_index"] == 1
    assert new_welcome["reconnect_token"] is None

    hijack = controller.handle(
        "attacker",
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=token,
        ),
    )[0].message
    assert (
        hijack["code"] == "authentication_failed" or hijack["code"] == "invalid_state"
    )


def test_reconnect_restores_consumed_command_sequence_and_duplicate_result():
    controller = LanServerController()
    room_code, host_token, _created = create_room(controller)
    join_player(controller, room_code)
    ready_and_start(controller, room_code)
    command = build_game_command(
        sequence=0,
        expected_revision=0,
        command="roll_dice",
    )
    accepted = controller.handle("host", command)[0].message

    controller.disconnect("host")
    reconnected = controller.handle(
        "host-new",
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=host_token,
        ),
    )
    welcome = next(
        item.message
        for item in reconnected
        if item.message["type"] == "session_welcome"
    )
    duplicate = controller.handle("host-new", command)

    assert welcome["next_sequence"] == 1
    assert duplicate[0].message == accepted
    assert duplicate[1].message["revision"] == 1


def test_spectator_and_out_of_turn_commands_are_rejected_without_state_change():
    controller = LanServerController()
    room_code, _host_token, _created = create_room(controller)
    join_player(controller, room_code)
    controller.handle(
        "viewer",
        message(
            "join_room",
            room_code=room_code,
            display_name="Viewer",
            role="spectator",
        ),
    )
    ready_and_start(controller, room_code)

    command = build_game_command(
        sequence=0,
        expected_revision=0,
        command="roll_dice",
    )
    spectator = controller.handle("viewer", command)[0].message
    guest = controller.handle("guest", command)[0].message

    assert spectator["type"] == "request_error"
    assert spectator["code"] == "forbidden"
    assert guest["type"] == "game_command_result"
    assert guest["accepted"] is False
    assert guest["code"] == "not_active_player"
    assert controller.snapshot_for_connection("host")["revision"] == 0


def test_duplicate_command_is_exactly_once_and_conflicts_are_rejected():
    controller = LanServerController()
    room_code, _host_token, _created = create_room(controller)
    join_player(controller, room_code)
    ready_and_start(controller, room_code)
    original_random_state = random.getstate()

    command = build_game_command(
        sequence=0,
        expected_revision=0,
        command="roll_dice",
    )
    first = controller.handle("host", command)
    first_result = first[0].message
    snapshot_after = controller.snapshot_for_connection("host")
    duplicate = controller.handle("host", command)

    assert first_result["accepted"] is True
    assert first_result["revision"] == 1
    assert len(duplicate) == 2
    assert duplicate[0].message == first_result
    assert duplicate[1].message["type"] == "state_snapshot"
    assert controller.snapshot_for_connection("host") == snapshot_after
    assert random.getstate() == original_random_state

    conflict = dict(command)
    conflict["command"] = "cancel"
    rejected = controller.handle("host", conflict)[0].message
    assert rejected["code"] == "sequence_conflict"


def test_stale_revision_is_cached_and_requires_a_new_sequence():
    controller = LanServerController()
    room_code, _host_token, _created = create_room(controller)
    join_player(controller, room_code)
    ready_and_start(controller, room_code)
    controller.handle(
        "host",
        build_game_command(sequence=0, expected_revision=0, command="roll_dice"),
    )

    stale = build_game_command(
        sequence=1,
        expected_revision=0,
        command="cancel",
    )
    result = controller.handle("host", stale)[0].message
    duplicate = controller.handle("host", stale)[0].message

    assert result["accepted"] is False
    assert result["code"] == "stale_revision"
    assert result == duplicate
    gap = controller.handle(
        "host",
        build_game_command(sequence=3, expected_revision=1, command="cancel"),
    )[0].message
    assert gap["code"] == "sequence_gap"


def test_request_validation_rejects_spoof_fields_unknown_rooms_and_bad_versions():
    controller = LanServerController()
    room_code, _token, _created = create_room(controller)
    join_player(controller, room_code)

    spoofed = build_game_command(
        sequence=0,
        expected_revision=0,
        command="roll_dice",
    )
    spoofed["player_index"] = 0
    assert controller.handle("guest", spoofed)[0].message["code"] == "invalid_request"

    missing = controller.handle(
        "new",
        message(
            "join_room",
            room_code="ZZZ999",
            display_name="Lost",
            role="player",
        ),
    )[0].message
    assert missing["code"] == "room_not_found"

    wrong_version = controller.handle(
        "new",
        {"type": "ping", "protocol_version": 999, "nonce": "x"},
    )[0].message
    assert wrong_version["code"] == "version_mismatch"

    boolean_version = controller.handle(
        "new",
        {"type": "ping", "protocol_version": True, "nonce": "x"},
    )[0].message
    assert boolean_version["code"] == "version_mismatch"

    surrogate_nonce = controller.handle(
        "new",
        {"type": "ping", "protocol_version": 1, "nonce": "\ud800"},
    )[0].message
    assert surrogate_nonce["type"] == "request_error"
    assert surrogate_nonce["code"] == "invalid_request"


def test_match_rng_uses_private_entropy_instead_of_public_board_seed(monkeypatch):
    private_seeds = iter(
        (
            (1 << 255) + 12_345,
            (1 << 254) + 67_890,
        )
    )
    monkeypatch.setattr(
        "game.lan_controller.secrets.randbits",
        lambda bits: next(private_seeds) if bits == 256 else 0,
    )
    controllers = (LanServerController(), LanServerController())
    started_messages = []
    caller_rng_before = random.getstate()

    for controller in controllers:
        room_code, _token, _created = create_room(controller)
        join_player(controller, room_code)
        started_messages.append(ready_and_start(controller, room_code))

    contexts = [
        controller._rooms[controller.room_codes[0]] for controller in controllers
    ]
    assert contexts[0].match_seed != contexts[1].match_seed
    assert contexts[0].random_state != contexts[1].random_state
    assert (
        controllers[0].snapshot_for_connection("host")["board_manifest"]
        == controllers[1].snapshot_for_connection("host")["board_manifest"]
    )
    encoded_messages = json.dumps(
        [item.message for outbound in started_messages for item in outbound]
    )
    assert all(str(context.match_seed) not in encoded_messages for context in contexts)
    assert random.getstate() == caller_rng_before


def test_failed_started_spectator_join_is_atomic_and_retryable():
    fail_spectator_snapshot = False

    def snapshot_builder(*args, **kwargs):
        if fail_spectator_snapshot and kwargs["viewer_player_index"] is None:
            raise RuntimeError("spectator snapshot failed")
        return build_state_snapshot(*args, **kwargs)

    controller = LanServerController(snapshot_builder=snapshot_builder)
    room_code, _token, _created = create_room(controller)
    join_player(controller, room_code)
    ready_and_start(controller, room_code)
    context = controller._rooms[room_code]
    lobby_before = context.lobby.public_snapshot()
    fail_spectator_snapshot = True

    failed = controller.handle(
        "viewer",
        message(
            "join_room",
            room_code=room_code,
            display_name="Viewer",
            role="spectator",
        ),
    )[0].message

    assert failed["code"] == "internal_error"
    assert context.lobby.public_snapshot() == lobby_before
    assert "viewer" not in controller._sessions

    fail_spectator_snapshot = False
    retried = controller.handle(
        "viewer",
        message(
            "join_room",
            room_code=room_code,
            display_name="Viewer",
            role="spectator",
        ),
    )
    assert any(item.message["type"] == "session_welcome" for item in retried)


def test_failed_reconnect_snapshot_restores_reservation_and_sequence():
    fail_host_snapshot = False

    def snapshot_builder(*args, **kwargs):
        if fail_host_snapshot and kwargs["viewer_player_index"] == 0:
            raise RuntimeError("reconnect snapshot failed")
        return build_state_snapshot(*args, **kwargs)

    controller = LanServerController(snapshot_builder=snapshot_builder)
    room_code, host_token, _created = create_room(controller)
    join_player(controller, room_code)
    ready_and_start(controller, room_code)
    controller.handle(
        "host",
        build_game_command(sequence=0, expected_revision=0, command="roll_dice"),
    )
    controller.disconnect("host")
    context = controller._rooms[room_code]
    lobby_revision = context.lobby.revision
    host_member = next(
        member
        for member in context.lobby._members.values()
        if member.role.value == "host"
    )
    reservation_deadline = host_member.reserved_until
    fail_host_snapshot = True

    failed = controller.handle(
        "host-returned",
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=host_token,
        ),
    )[0].message

    restored_host = next(
        member
        for member in context.lobby._members.values()
        if member.role.value == "host"
    )
    assert failed["code"] == "internal_error"
    assert "host-returned" not in controller._sessions
    assert restored_host.connected is False
    assert restored_host.reserved_until == reservation_deadline
    assert context.lobby.revision == lobby_revision

    fail_host_snapshot = False
    retried = controller.handle(
        "host-returned",
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=host_token,
        ),
    )
    welcome = next(
        item.message for item in retried if item.message["type"] == "session_welcome"
    )
    assert welcome["seat_index"] == 0
    assert welcome["next_sequence"] == 1
    assert any(
        item.message["type"] == "state_snapshot" and item.message["revision"] == 1
        for item in retried
    )


def test_expired_empty_room_releases_capacity():
    controller = LanServerController(room_limit=1)
    room_code, _token, _created = create_room(controller)
    controller._rooms[room_code].lobby._reservation_seconds = -1.0

    controller.disconnect("host")
    controller.tick()

    assert room_code not in controller.room_codes
    replacement_code, _replacement_token, _outbound = create_room(
        controller,
        connection="replacement-host",
    )
    assert replacement_code in controller.room_codes


def test_explicit_waiting_room_leave_releases_seat_and_promotes_guest():
    controller = LanServerController()
    room_code, _token, _created = create_room(controller)
    join_player(controller, room_code)

    outbound = controller.handle("host", message("leave_room"))

    assert "host" not in controller._sessions
    snapshot = next(item.message["lobby"] for item in outbound)
    assert snapshot["members"] == [
        {
            "display_name": "Guest",
            "role": "host",
            "seat": 2,
            "connected": True,
            "ready": False,
            "reservation_seconds_remaining": None,
        }
    ]
    replacement, _ = join_player(
        controller,
        room_code,
        connection="replacement",
        name="Replacement",
    )
    assert replacement["seat_index"] == 0


def test_started_player_explicit_leave_closes_match_for_every_peer():
    controller = LanServerController()
    room_code, _token, _created = create_room(controller)
    join_player(controller, room_code)
    ready_and_start(controller, room_code)

    outbound = controller.handle("guest", message("leave_room"))

    assert {item.connection_id for item in outbound} == {"host", "guest"}
    assert all(item.message["type"] == "room_closed" for item in outbound)
    assert all(item.message["code"] == "player_left" for item in outbound)
    assert room_code not in controller.room_codes
    assert "guest" not in controller._sessions
    assert "host" not in controller._sessions


def test_started_match_closes_for_every_peer_when_reconnect_grace_expires():
    controller = LanServerController()
    room_code, _token, _created = create_room(controller)
    join_player(controller, room_code)
    ready_and_start(controller, room_code)
    controller._rooms[room_code].lobby._reservation_seconds = -1.0
    controller.disconnect("guest")

    outbound = controller.tick()

    closed = [item for item in outbound if item.message["type"] == "room_closed"]
    assert [item.connection_id for item in closed] == ["host"]
    assert closed[0].message["code"] == "player_reconnect_expired"
    assert room_code not in controller.room_codes
    assert "host" not in controller._sessions


def test_start_failure_does_not_commit_lobby_phase_and_can_be_retried():
    attempts = 0

    def flaky_snapshot(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("private implementation failure")
        return build_state_snapshot(*args, **kwargs)

    controller = LanServerController(snapshot_builder=flaky_snapshot)
    room_code, _host_token, _created = create_room(controller)
    join_player(controller, room_code)
    controller.handle("host", message("set_ready", ready=True))
    controller.handle("guest", message("set_ready", ready=True))

    failed = controller.handle("host", message("start_game"))[0].message
    assert failed["type"] == "request_error"
    assert failed["code"] == "internal_error"
    assert "private implementation failure" not in json.dumps(failed)

    retried = controller.handle("host", message("start_game"))
    assert any(item.message["type"] == "state_snapshot" for item in retried)


@pytest.mark.parametrize(
    ("raised", "expected_code"),
    (
        (
            NetworkActionError("forced_rejection", "意図的な拒否です。"),
            "forced_rejection",
        ),
        (RuntimeError("private command failure"), "internal_error"),
    ),
)
def test_failed_command_rolls_back_game_and_room_rng(raised, expected_code):
    def mutating_applier(game, _seat, _command, _args):
        game.bank.resources[ResourceType.WOOD] -= 1
        game.players[0].resources[ResourceType.WOOD] += 1
        random.random()
        raise raised

    controller = LanServerController(command_applier=mutating_applier)
    room_code, _host_token, _created = create_room(controller)
    join_player(controller, room_code)
    ready_and_start(controller, room_code)
    before = controller.snapshot_for_connection("host")
    room_rng_before = controller._rooms[room_code].random_state
    caller_rng_before = random.getstate()

    result = controller.handle(
        "host",
        build_game_command(sequence=0, expected_revision=0, command="roll_dice"),
    )[0].message

    assert result["accepted"] is False
    assert result["code"] == expected_code
    assert "private command failure" not in json.dumps(result)
    assert controller.snapshot_for_connection("host") == before
    assert controller._rooms[room_code].random_state == room_rng_before
    assert random.getstate() == caller_rng_before


def test_invalid_connection_lookup_is_reported_without_mutating_rooms():
    controller = LanServerController()
    with pytest.raises(LanControllerError):
        controller.snapshot_for_connection("missing")
    assert controller.disconnect("missing") == ()
