import os
import struct

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from game.game import CatanGame
from game.network_protocol import (
    MAX_FRAME_BYTES,
    FrameDecoder,
    NetworkProtocolError,
    build_action_request,
    build_state_snapshot,
    encode_frame,
)
from game.resources import ResourceType


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


def test_spectator_snapshot_hides_every_players_private_cards(game):
    snapshot = build_state_snapshot(game, viewer_player_index=None)

    assert all(player["resources"] is None for player in snapshot["state"]["players"])
    assert [player["resource_total"] for player in snapshot["state"]["players"]] == [2, 3, 0]


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
