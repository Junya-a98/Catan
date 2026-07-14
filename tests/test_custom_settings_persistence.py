import copy
import json
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from game.custom_map import CustomMapSpec
from game.development_cards import DevelopmentCardType
from game.game import CatanGame
from game.house_rules import HouseRules
from game.persistence import SaveGameError, restore_game, serialize_game
from game.replay import ReplayError, ReplayRecorder, load_replay


@pytest.fixture
def game():
    pygame.init()
    pygame.display.set_mode((1, 1))
    instance = CatanGame(board_seed=90210, ai_player_count=1)
    instance.configure_players(2, reset_logs=False)
    yield instance
    instance.audio.stop()
    pygame.quit()


def _use_custom_map(game):
    custom_map = CustomMapSpec.from_board(game.board, name="保存境界テスト")
    game.board_mode = "custom"
    game.custom_map_spec = custom_map
    return custom_map


def _different_valid_map(custom_map):
    first = custom_map.tiles[0]
    second = next(
        tile
        for tile in custom_map.tiles[1:]
        if (tile.resource, tile.number) != (first.resource, first.number)
    )
    return custom_map.swap_tiles(first.axial, second.axial)


def test_nonstandard_house_rules_round_trip_and_legacy_defaults(game):
    selected = HouseRules(
        bank_trade_3_to_1=True,
        skip_discard_on_seven=True,
        disabled_development_cards=frozenset(
            {
                DevelopmentCardType.MONOPOLY,
                DevelopmentCardType.VICTORY_POINT,
            }
        ),
    )
    game.house_rules = selected
    document = serialize_game(game)

    assert document["rules"]["house_rules"] == selected.to_document()
    game.house_rules = HouseRules.standard()
    restore_game(game, copy.deepcopy(document), runtime_side_effects=False)
    assert game.house_rules == selected

    legacy_document = serialize_game(game)
    legacy_document["rules"].pop("house_rules")
    restore_game(game, legacy_document, runtime_side_effects=False)
    assert game.house_rules == HouseRules.standard()


def test_invalid_house_rules_are_rejected_before_restore(game):
    before = serialize_game(game)
    document = copy.deepcopy(before)
    document["rules"]["house_rules"] = {
        "bank_trade_3_to_1": True,
        "skip_discard_on_seven": False,
        "disabled_development_cards": ["NOT_A_CARD"],
    }

    with pytest.raises(SaveGameError, match="ハウスルール"):
        restore_game(game, document, runtime_side_effects=False)

    assert serialize_game(game) == before


def test_custom_save_contains_complete_map_and_matching_fingerprint(game):
    custom_map = _use_custom_map(game)

    document = serialize_game(game)

    assert document["board"]["custom_map"] == custom_map.to_document()
    assert document["board"]["custom_map_fingerprint"] == custom_map.fingerprint
    restore_game(game, copy.deepcopy(document), runtime_side_effects=False)
    assert game.board.mode == "custom"
    assert game.custom_map_spec == custom_map
    assert serialize_game(game) == document


@pytest.mark.parametrize("missing_field", ["custom_map", "custom_map_fingerprint"])
def test_custom_save_requires_both_map_fields(game, missing_field):
    _use_custom_map(game)
    document = serialize_game(game)
    del document["board"][missing_field]

    with pytest.raises(SaveGameError, match="完全な設定"):
        restore_game(game, document, runtime_side_effects=False)


def test_custom_save_rejects_map_fingerprint_tampering(game):
    custom_map = _use_custom_map(game)
    document = serialize_game(game)
    document["board"]["custom_map"] = _different_valid_map(
        custom_map
    ).to_document()

    with pytest.raises(SaveGameError, match="識別子"):
        restore_game(game, document, runtime_side_effects=False)


def test_generated_save_rejects_hidden_custom_map_fields(game):
    custom_map = CustomMapSpec.from_board(game.board)
    document = serialize_game(game)
    document["board"]["custom_map"] = custom_map.to_document()
    document["board"]["custom_map_fingerprint"] = custom_map.fingerprint

    with pytest.raises(SaveGameError, match="混在"):
        restore_game(game, document, runtime_side_effects=False)


def test_replay_identity_includes_house_rules_and_supports_legacy(game, tmp_path):
    selected = HouseRules(bank_trade_3_to_1=True)
    game.house_rules = selected
    recorder = ReplayRecorder()
    recorder.capture(game, elapsed_ms=0)
    path = recorder.save(tmp_path / "house-rules.json")
    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["metadata"]["house_rules_fingerprint"] == selected.fingerprint()
    document["metadata"]["house_rules_fingerprint"] = "0" * 64
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ReplayError, match="メタデータ"):
        load_replay(path)

    game.house_rules = HouseRules.standard()
    legacy = ReplayRecorder()
    legacy.capture(game, elapsed_ms=0)
    legacy_document = legacy.archive().to_document()
    del legacy_document["metadata"]["house_rules_fingerprint"]
    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(json.dumps(legacy_document), encoding="utf-8")
    assert load_replay(legacy_path).frames[0].sequence == 0


def test_replay_identity_validates_custom_map_document_and_metadata(game, tmp_path):
    custom_map = _use_custom_map(game)
    recorder = ReplayRecorder()
    recorder.capture(game, elapsed_ms=0)
    path = recorder.save(tmp_path / "custom-map.json")
    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["metadata"]["custom_map_fingerprint"] == custom_map.fingerprint
    assert (
        document["frames"][0]["snapshot"]["board"]["custom_map_fingerprint"]
        == custom_map.fingerprint
    )
    assert load_replay(path, validation_game=game).frames[0].sequence == 0

    document["frames"][0]["snapshot"]["board"]["custom_map"] = (
        _different_valid_map(custom_map).to_document()
    )
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ReplayError, match="識別子"):
        load_replay(path)


def test_replay_rejects_generated_metadata_with_custom_fingerprint(game, tmp_path):
    recorder = ReplayRecorder()
    recorder.capture(game, elapsed_ms=0)
    document = recorder.archive().to_document()
    document["metadata"]["custom_map_fingerprint"] = "0" * 64
    path = tmp_path / "mixed-settings.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ReplayError, match="混在"):
        load_replay(path)
