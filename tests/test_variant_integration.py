import copy
import json
import random

import pytest

from game.game import CatanGame
from game.lan_controller import LanServerController
from game.lan_lobby import LobbyValidationError, RoomSettings
from game.network_protocol import NETWORK_PROTOCOL_VERSION, build_state_snapshot
from game.persistence import SaveGameError, restore_game, serialize_game
from game.replay import ReplayError, ReplayRecorder, load_replay
from game.variant import VariantConfig


STANDARD = VariantConfig.standard()
STANDARD_DOCUMENT = STANDARD.to_document()


def _message(message_type, **payload):
    return {
        "type": message_type,
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        **payload,
    }


@pytest.fixture
def game():
    instance = CatanGame(board_seed=90210, ai_player_count=1, headless=True)
    instance.configure_players(2, reset_logs=False)
    yield instance
    instance.audio.stop()


def test_room_settings_publish_canonical_variant_and_reject_invalid_input():
    settings = RoomSettings(variant=copy.deepcopy(STANDARD_DOCUMENT))

    assert settings.variant == STANDARD
    assert settings.to_public_dict()["variant"] == STANDARD_DOCUMENT

    with pytest.raises(LobbyValidationError, match="variant"):
        RoomSettings(
            variant={"version": 1, "kind": "frontier", "options": {}}
        )


def test_standard_constructor_is_a_no_op_for_deterministic_game_state():
    random.seed(418)
    implicit = CatanGame(board_seed=5150, headless=True)
    random.seed(418)
    explicit = CatanGame(
        board_seed=5150,
        variant_config=VariantConfig.standard(),
        headless=True,
    )
    try:
        assert implicit.variant_config == explicit.variant_config == STANDARD
        assert serialize_game(implicit) == serialize_game(explicit)
    finally:
        implicit.audio.stop()
        explicit.audio.stop()


def test_lobby_factory_and_network_snapshot_keep_the_standard_variant():
    controller = LanServerController()
    created = controller.handle(
        "host",
        _message(
            "create_room",
            display_name="Host",
            settings={
                "player_count": 2,
                "victory_target": 5,
                "board_mode": "constrained",
                "board_seed": 4242,
                "ai_player_count": 1,
                "variant": copy.deepcopy(STANDARD_DOCUMENT),
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
    assert lobby["settings"]["variant"] == STANDARD_DOCUMENT

    controller.handle("host", _message("set_ready", ready=True))
    controller.handle("host", _message("start_game"))
    snapshot = controller.snapshot_for_connection("host")

    assert welcome["room_code"]
    assert snapshot["state"]["rules"]["variant"] == STANDARD_DOCUMENT


def test_save_restore_and_network_snapshot_support_legacy_default(game):
    document = serialize_game(game)
    assert document["rules"]["variant"] == STANDARD_DOCUMENT
    assert build_state_snapshot(game)["state"]["rules"]["variant"] == (
        STANDARD_DOCUMENT
    )

    legacy = copy.deepcopy(document)
    del legacy["rules"]["variant"]
    game.variant_config = STANDARD
    restore_game(game, legacy, runtime_side_effects=False)
    assert game.variant_config == STANDARD


def test_invalid_saved_variant_is_rejected_before_mutating_the_game(game):
    before = serialize_game(game)
    invalid = copy.deepcopy(before)
    invalid["rules"]["variant"]["kind"] = "trade2"

    with pytest.raises(SaveGameError, match="variant"):
        restore_game(game, invalid, runtime_side_effects=False)

    assert serialize_game(game) == before


def test_replay_identity_includes_variant_and_loads_legacy_standard(game, tmp_path):
    recorder = ReplayRecorder()
    recorder.capture(game, elapsed_ms=0)
    document = recorder.archive().to_document()

    assert document["metadata"]["variant_fingerprint"] == STANDARD.fingerprint()
    assert document["frames"][0]["snapshot"]["rules"]["variant"] == (
        STANDARD_DOCUMENT
    )

    tampered = copy.deepcopy(document)
    tampered["metadata"]["variant_fingerprint"] = "0" * 64
    tampered_path = tmp_path / "tampered-variant.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ReplayError, match="メタデータ"):
        load_replay(tampered_path)

    tampered_state = copy.deepcopy(document)
    tampered_state["frames"][0]["snapshot"]["variant_state"][
        "config_fingerprint"
    ] = "0" * 64
    tampered_state_path = tmp_path / "tampered-variant-state.json"
    tampered_state_path.write_text(json.dumps(tampered_state), encoding="utf-8")
    with pytest.raises(ReplayError, match="variant state"):
        load_replay(tampered_state_path)

    legacy = copy.deepcopy(document)
    del legacy["metadata"]["variant_fingerprint"]
    del legacy["frames"][0]["snapshot"]["rules"]["variant"]
    del legacy["frames"][0]["snapshot"]["variant_state"]
    legacy_path = tmp_path / "legacy-standard.json"
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
    assert load_replay(legacy_path).frames[0].sequence == 0
