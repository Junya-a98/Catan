import json
import os
import struct
from copy import deepcopy

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

import game.network_protocol as network_protocol
from game.game import CatanGame
from game.building import Building, BuildingType
from game.custom_map import CustomMapSpec
from game.game_board import GameBoard
from game.network_protocol import (
    MAX_GAME_COMMAND_NAME_LENGTH,
    MAX_GAME_COMMAND_STRING_LENGTH,
    MAX_FRAME_BYTES,
    MAX_LIVE_MATCH_EVENTS,
    MAX_SNAPSHOT_LOG_MESSAGES,
    FrameDecoder,
    NetworkProtocolError,
    build_action_request,
    build_board_reference_index,
    build_game_command,
    build_state_snapshot,
    encode_frame,
)
from game.resources import ResourceType
from game.road import Road


@pytest.fixture
def game():
    pygame.init()
    pygame.display.set_mode((1, 1))
    instance = CatanGame(board_seed=9090, ai_player_count=0)
    instance.configure_players(3, reset_logs=False)
    assert instance.bank.withdraw(ResourceType.WOOD, 2)
    instance.players[0].add_resource(ResourceType.WOOD, 2)
    assert instance.bank.withdraw(ResourceType.ORE, 3)
    instance.players[1].add_resource(ResourceType.ORE, 3)
    instance.players[0].victory_point_cards = 1
    yield instance
    instance.audio.stop()
    pygame.quit()


def test_state_snapshot_reveals_only_the_viewers_private_cards(game):
    snapshot = build_state_snapshot(game, viewer_player_index=0, revision=7)
    viewer, opponent, third = snapshot["state"]["players"]

    assert snapshot["revision"] == 7
    assert viewer["resources"]["WOOD"] == 2
    assert viewer["victory_point_cards"] == 1
    assert opponent["resources"] is None
    assert opponent["resource_total"] == 3
    assert opponent["development_cards"] is None
    assert opponent["victory_point_cards"] is None
    assert third["resources"] is None
    assert snapshot["state"]["development_deck"] == {"remaining": 25}


def test_unsubmitted_domestic_trade_draft_is_visible_only_to_its_editor(game):
    game.phase = "main"
    game.initial_dice_phase = False
    game.dice_rolled = True
    game.special_phase = "domestic_trade_edit"
    game.domestic_trade_partner = game.players[1]
    game.domestic_trade_editor = game.players[0]
    game.domestic_trade_give[ResourceType.WOOD] = 1
    game.domestic_trade_receive[ResourceType.ORE] = 1

    editor = build_state_snapshot(game, viewer_player_index=0)
    opponent = build_state_snapshot(game, viewer_player_index=1)
    spectator = build_state_snapshot(game, viewer_player_index=None)

    assert editor["state"]["domestic_trade"]["give"]["WOOD"] == 1
    assert editor["state"]["domestic_trade"]["receive"]["ORE"] == 1
    for hidden in (opponent, spectator):
        trade = hidden["state"]["domestic_trade"]
        assert sum(trade["give"].values()) == 0
        assert sum(trade["receive"].values()) == 0


def test_spectator_snapshot_hides_every_players_private_cards(game):
    snapshot = build_state_snapshot(game, viewer_player_index=None)

    assert all(player["resources"] is None for player in snapshot["state"]["players"])
    assert [player["resource_total"] for player in snapshot["state"]["players"]] == [2, 3, 0]


def test_variant_state_is_public_only_for_players_and_spectators(
    game, monkeypatch
):
    private_sentinel = "VARIANT_PRIVATE_SENTINEL_MUST_NOT_CROSS_NETWORK"
    authoritative = network_protocol.serialize_game(game)
    authoritative["variant_state"]["private"] = {
        "hidden_history": [private_sentinel]
    }
    expected_public = {
        key: deepcopy(value)
        for key, value in authoritative["variant_state"].items()
        if key != "private"
    }
    monkeypatch.setattr(
        network_protocol,
        "serialize_game",
        lambda _game: deepcopy(authoritative),
    )

    for viewer in (0, None):
        snapshot = build_state_snapshot(
            game,
            viewer_player_index=viewer,
            revision=8,
        )
        assert snapshot["state"]["variant_state"] == expected_public
        assert "private" not in snapshot["state"]["variant_state"]
        assert private_sentinel not in json.dumps(
            snapshot,
            ensure_ascii=False,
            allow_nan=False,
        )

    # Projection must not mutate the authoritative full save document.
    assert authoritative["variant_state"]["private"] == {
        "hidden_history": [private_sentinel]
    }


def test_variant_state_public_projection_fails_closed_on_invalid_public_data(
    game, monkeypatch
):
    authoritative = network_protocol.serialize_game(game)
    authoritative["variant_state"]["public"] = {"unexpected": True}
    monkeypatch.setattr(
        network_protocol,
        "serialize_game",
        lambda _game: deepcopy(authoritative),
    )

    with pytest.raises(NetworkProtocolError, match="variant_state"):
        build_state_snapshot(game, viewer_player_index=0)


def test_frame_decoder_handles_fragmented_and_concatenated_messages():
    first = build_action_request(
        player_index=1,
        sequence=4,
        action="button",
        payload={"action": "roll_dice"},
    )
    second = build_action_request(
        player_index=1,
        sequence=5,
        action="board_click",
        payload={"x": 10, "y": 20},
    )
    encoded = encode_frame(first) + encode_frame(second)
    decoder = FrameDecoder()

    messages = []
    for split in (encoded[:3], encoded[3:17], encoded[17:41], encoded[41:]):
        messages.extend(decoder.feed(split))

    assert messages == [first, second]


def test_decoder_rejects_oversized_frame_before_reading_payload():
    decoder = FrameDecoder()
    header = struct.pack("!I", MAX_FRAME_BYTES + 1)

    with pytest.raises(NetworkProtocolError, match="許容サイズ"):
        decoder.feed(header)


def test_action_request_rejects_unknown_action_type():
    with pytest.raises(NetworkProtocolError, match="未対応"):
        build_action_request(
            player_index=0,
            sequence=1,
            action="execute_python",
        )


def test_live_snapshot_removes_secret_vp_checkpoints_for_players_and_spectators(game):
    game.phase = "main"
    game.match_metrics.record_point_checkpoint(
        "秘密VPを含む得点更新",
        {
            "seat-1": game.get_player_victory_points(game.players[0]),
            "seat-2": game.get_player_victory_points(game.players[1]),
            "seat-3": game.get_player_victory_points(game.players[2]),
        },
    )
    assert game.match_metrics.point_checkpoints[-1].points["seat-1"] == 1

    player_snapshot = build_state_snapshot(game, viewer_player_index=0)
    spectator_snapshot = build_state_snapshot(game, viewer_player_index=None)

    assert player_snapshot["state"]["players"][0]["victory_point_cards"] == 1
    assert player_snapshot["state"]["match_metrics"]["point_checkpoints"] == []
    assert spectator_snapshot["state"]["match_metrics"]["point_checkpoints"] == []
    assert all(
        player["victory_point_cards"] is None
        for player in spectator_snapshot["state"]["players"]
    )


def test_finished_snapshot_keeps_result_vp_checkpoints(game):
    game.phase = "finished"
    game.winner = game.players[0]
    game.match_metrics.record_point_checkpoint(
        "最終得点",
        {"seat-1": 10, "seat-2": 7, "seat-3": 6},
    )

    snapshot = build_state_snapshot(game, viewer_player_index=None)

    assert snapshot["state"]["match_metrics"]["point_checkpoints"][-1]["points"] == {
        "seat-1": 10,
        "seat-2": 7,
        "seat-3": 6,
    }


def test_snapshot_bounds_live_logs_and_public_metric_events(game):
    game.phase = "main"
    game.log_messages = [
        f"network-log-{index}"
        for index in range(MAX_SNAPSHOT_LOG_MESSAGES + 25)
    ]
    game.log_scroll_offset = 99
    for index in range(MAX_LIVE_MATCH_EVENTS + 15):
        game.match_metrics.record_important_event(f"event-{index}")

    snapshot = build_state_snapshot(game, viewer_player_index=None)
    history = snapshot["state"]["history"]
    events = snapshot["state"]["match_metrics"]["important_events"]

    assert len(history["log_messages"]) == MAX_SNAPSHOT_LOG_MESSAGES
    assert history["log_messages"][0] == "network-log-25"
    assert history["log_messages"][-1] == (
        f"network-log-{MAX_SNAPSHOT_LOG_MESSAGES + 24}"
    )
    assert snapshot["state"]["ui"]["log_scroll_offset"] == 0
    assert len(events) == MAX_LIVE_MATCH_EVENTS
    assert events[0]["title"] == "event-15"


def test_board_manifest_is_complete_public_json_and_uses_stable_references(game):
    building_node = game.board.nodes[0]
    building_node.building = Building(game.players[1], BuildingType.CITY)
    road_nodes = game.board.edges[0]
    game.board.roads.append(Road(game.players[0], *road_nodes))

    manifest = build_state_snapshot(game, viewer_player_index=None)["board_manifest"]

    assert manifest["format"] == "catan-board-manifest"
    assert manifest["version"] == 1
    assert len(manifest["tiles"]) == 19
    assert len(manifest["nodes"]) == len(game.board.nodes)
    assert len(manifest["edges"]) == len(game.board.edges)
    assert len(manifest["harbors"]) == 9
    assert sum(tile["robber"] for tile in manifest["tiles"]) == 1
    assert all(tile["resource"] in ResourceType.__members__ for tile in manifest["tiles"])
    assert any(
        node["building"] == {"owner_player_index": 1, "type": "city"}
        for node in manifest["nodes"]
    )
    assert any(
        edge["road"] == {"owner_player_index": 0}
        for edge in manifest["edges"]
    )

    tile_ids = {tile["id"] for tile in manifest["tiles"]}
    node_ids = {node["id"] for node in manifest["nodes"]}
    edge_ids = {edge["id"] for edge in manifest["edges"]}
    harbor_ids = {harbor["id"] for harbor in manifest["harbors"]}
    assert len(tile_ids) == len(manifest["tiles"])
    assert len(node_ids) == len(manifest["nodes"])
    assert len(edge_ids) == len(manifest["edges"])
    assert len(harbor_ids) == len(manifest["harbors"])
    assert all(set(tile["corner_node_ids"]) <= node_ids for tile in manifest["tiles"])
    assert all(set(node["adjacent_tile_ids"]) <= tile_ids for node in manifest["nodes"])
    assert all(set(node["adjacent_node_ids"]) <= node_ids for node in manifest["nodes"])
    assert all(set(node["edge_ids"]) <= edge_ids for node in manifest["nodes"])
    assert all(set(node["harbor_ids"]) <= harbor_ids for node in manifest["nodes"])
    assert all(set(edge["node_ids"]) <= node_ids for edge in manifest["edges"])
    assert all(set(edge["adjacent_tile_ids"]) <= tile_ids for edge in manifest["edges"])
    assert all(
        edge["harbor_id"] is None or edge["harbor_id"] in harbor_ids
        for edge in manifest["edges"]
    )
    assert all(harbor["edge_id"] in edge_ids for harbor in manifest["harbors"])

    encoded = json.dumps(manifest, ensure_ascii=False, allow_nan=False)
    assert "development_cards" not in encoded
    assert "new_development_cards" not in encoded
    assert "victory_point_cards" not in encoded
    assert "resources" not in encoded


def test_board_manifest_is_deterministic_for_the_same_board_seed(game):
    other = CatanGame(board_seed=9090, ai_player_count=0, headless=True)
    other.configure_players(3, reset_logs=False)

    first = build_state_snapshot(game, viewer_player_index=None)["board_manifest"]
    second = build_state_snapshot(other, viewer_player_index=None)["board_manifest"]

    assert first == second


def test_custom_board_manifest_publishes_verified_map_identity_only_for_custom_mode(
    game,
):
    custom_map = CustomMapSpec.from_board(GameBoard(seed=151))
    custom_game = CatanGame(
        board_mode="custom",
        board_seed=151,
        custom_map=custom_map,
        ai_player_count=0,
        headless=True,
    )
    custom_game.configure_players(2, reset_logs=False)

    snapshot = build_state_snapshot(custom_game, viewer_player_index=None)

    assert snapshot["board_manifest"]["mode"] == "custom"
    assert (
        snapshot["board_manifest"]["custom_map_fingerprint"]
        == custom_map.fingerprint
    )
    assert (
        snapshot["state"]["board"]["custom_map_fingerprint"]
        == custom_map.fingerprint
    )
    assert "custom_map" not in snapshot["state"]["board"]
    assert "custom_map_fingerprint" not in build_state_snapshot(
        game,
        viewer_player_index=None,
    )["board_manifest"]


def test_board_manifest_and_command_reference_index_share_the_same_ids(game):
    references = build_board_reference_index(game)
    manifest = build_state_snapshot(game, viewer_player_index=None)["board_manifest"]

    assert {item["id"] for item in manifest["nodes"]} == set(references["node"])
    assert {item["id"] for item in manifest["edges"]} == set(references["edge"])
    assert {item["id"] for item in manifest["tiles"]} == set(references["tile"])
    assert {item["id"] for item in manifest["harbors"]} == set(
        references["harbor"]
    )
    assert all(target_id == f"node-{index}" for index, target_id in enumerate(references["node"]))
    assert all(target_id == f"edge-{index}" for index, target_id in enumerate(references["edge"]))


def test_game_command_is_semantic_bounded_and_defensively_copied():
    args = {"target_id": "edge-017", "options": [1, True, None]}
    command = build_game_command(
        sequence=4,
        expected_revision=12,
        command="build_road",
        args=args,
    )
    args["target_id"] = "edge-999"
    args["options"].append("changed")

    assert command == {
        "type": "game_command",
        "protocol_version": 1,
        "sequence": 4,
        "expected_revision": 12,
        "command": "build_road",
        "args": {"target_id": "edge-017", "options": [1, True, None]},
    }
    assert "player_index" not in command
    assert FrameDecoder().feed(encode_frame(command)) == [command]


@pytest.mark.parametrize("field", ["sequence", "expected_revision"])
@pytest.mark.parametrize("value", [True, -1, 1.5, "1"])
def test_game_command_rejects_invalid_integer_fields(field, value):
    values = {
        "sequence": 1,
        "expected_revision": 2,
        "command": "roll_dice",
    }
    values[field] = value

    with pytest.raises(NetworkProtocolError):
        build_game_command(**values)


def test_game_command_rejects_malformed_identifier_non_object_and_unsafe_args():
    for invalid_command in (
        "",
        "ExecutePython",
        "execute-python",
        "x" * (MAX_GAME_COMMAND_NAME_LENGTH + 1),
        1,
    ):
        with pytest.raises(NetworkProtocolError, match="command"):
            build_game_command(
                sequence=1,
                expected_revision=0,
                command=invalid_command,
            )
    with pytest.raises(NetworkProtocolError, match="args"):
        build_game_command(
            sequence=1,
            expected_revision=0,
            command="roll_dice",
            args=[],
        )
    for unsafe_args in (
        {"number": float("nan")},
        {"number": float("inf")},
        {"value": object()},
        {"text": "x" * (MAX_GAME_COMMAND_STRING_LENGTH + 1)},
        {"chunks": ["x" * MAX_GAME_COMMAND_STRING_LENGTH] * 17},
    ):
        with pytest.raises(NetworkProtocolError, match="args"):
            build_game_command(
                sequence=1,
                expected_revision=0,
                command="roll_dice",
                args=unsafe_args,
            )

    circular = {}
    circular["self"] = circular
    with pytest.raises(NetworkProtocolError, match="循環"):
        build_game_command(
            sequence=1,
            expected_revision=0,
            command="roll_dice",
            args=circular,
        )


def test_snapshot_and_legacy_action_reject_boolean_integer_fields(game):
    with pytest.raises(NetworkProtocolError, match="revision"):
        build_state_snapshot(game, revision=True)
    with pytest.raises(NetworkProtocolError, match="閲覧"):
        build_state_snapshot(game, viewer_player_index=True)
    with pytest.raises(NetworkProtocolError, match="プレイヤー"):
        build_action_request(player_index=True, sequence=1, action="button")
    with pytest.raises(NetworkProtocolError, match="sequence"):
        build_action_request(player_index=0, sequence=True, action="button")


def test_frames_reject_non_finite_json_numbers_on_encode_and_decode():
    with pytest.raises(NetworkProtocolError, match="JSON"):
        encode_frame({"protocol_version": 1, "value": float("nan")})

    raw = b'{"protocol_version":1,"value":NaN}'
    frame = struct.pack("!I", len(raw)) + raw
    with pytest.raises(NetworkProtocolError, match="受信JSON"):
        FrameDecoder().feed(frame)

    boolean_version = b'{"protocol_version":true}'
    frame = struct.pack("!I", len(boolean_version)) + boolean_version
    with pytest.raises(NetworkProtocolError, match="version"):
        FrameDecoder().feed(frame)
