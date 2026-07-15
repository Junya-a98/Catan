import copy
import json

import pytest

from game.game import CatanGame
from game.persistence import SaveGameError, restore_game, serialize_game
from game.variant import VariantConfig
from game.variant_state import VariantState


@pytest.fixture
def game():
    instance = CatanGame(board_seed=70123, ai_player_count=1, headless=True)
    instance.configure_players(2, reset_logs=False)
    yield instance
    instance.audio.stop()


def test_game_owns_state_bound_to_its_config_and_full_save_round_trips(game):
    expected_state = VariantState.initial(game.variant_config)
    document = serialize_game(game)

    assert game.variant_state == expected_state
    assert document["variant_state"] == expected_state.to_document()
    assert "private" in document["variant_state"]
    assert json.loads(json.dumps(document))["variant_state"] == (
        expected_state.to_document()
    )

    game.variant_state = VariantState.standard()
    restore_game(game, copy.deepcopy(document), runtime_side_effects=False)

    assert game.variant_config == VariantConfig.standard()
    assert game.variant_state == expected_state
    assert serialize_game(game) == document


def test_save_without_config_or_runtime_state_restores_legacy_standard(game):
    legacy = serialize_game(game)
    del legacy["rules"]["variant"]
    del legacy["variant_state"]

    restore_game(game, legacy, runtime_side_effects=False)

    assert game.variant_config == VariantConfig.standard()
    assert game.variant_state == VariantState.standard()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document: document["variant_state"].__setitem__(
            "config_fingerprint", "0" * 64
        ),
        lambda document: document["variant_state"].pop("private"),
        lambda document: document["variant_state"]["private"].__setitem__(
            "future_deck", ["rain"]
        ),
        lambda document: document["variant_state"].__setitem__(
            "unexpected", True
        ),
    ],
)
def test_invalid_runtime_state_is_rejected_before_restore_mutates_game(
    game,
    mutate,
):
    before = serialize_game(game)
    invalid = copy.deepcopy(before)
    mutate(invalid)

    with pytest.raises(SaveGameError, match="variant state"):
        restore_game(game, invalid, runtime_side_effects=False)

    assert serialize_game(game) == before


def test_serialize_rejects_state_that_does_not_match_config(game):
    game.variant_state = VariantState(config_fingerprint="0" * 64)

    with pytest.raises(SaveGameError, match="一致"):
        serialize_game(game)

    game.variant_state = VariantState.standard()
    game.variant_state = {"public": {}, "private": {}}
    with pytest.raises(SaveGameError, match="保存"):
        serialize_game(game)
