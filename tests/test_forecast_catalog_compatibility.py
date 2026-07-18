import copy
import json
import os
import random

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest

from game.forecast_events import (
    BANDIT_RAID_EVENT_ID,
    CONSTRUCTION_BOOM_EVENT_ID,
    EARTHQUAKE_EVENT_ID,
    EVENT_CATALOG,
    HARBOR_BLOCKADE_EVENT_ID,
    MERCHANT_FESTIVAL_EVENT_ID,
    SHEEP_DROUGHT_EVENT_ID,
    WHEAT_HARVEST_EVENT_ID,
    validate_forecast_public,
)
from game.game import CatanGame
from game.network_protocol import build_state_snapshot
from game.network_replay import NetworkReplayStore
from game.persistence import restore_game, serialize_game
from game.variant import VariantConfig
from game.variant_state import VariantState


LEGACY_CORE_V1_CONFIG_DOCUMENT = {
    "version": 1,
    "kind": "forecast_events",
    "options": {
        "catalog": "core_v1",
        "forecast_lead_turns": 2,
        "event_interval_turns": 6,
    },
}
LEGACY_DECK_SEED = "1" * 64
CORE_V2_EVENT_IDS = {
    WHEAT_HARVEST_EVENT_ID,
    SHEEP_DROUGHT_EVENT_ID,
    HARBOR_BLOCKADE_EVENT_ID,
    CONSTRUCTION_BOOM_EVENT_ID,
    MERCHANT_FESTIVAL_EVENT_ID,
    BANDIT_RAID_EVENT_ID,
    EARTHQUAKE_EVENT_ID,
}


def _legacy_core_v1_config():
    return VariantConfig.from_document(LEGACY_CORE_V1_CONFIG_DOCUMENT)


def _legacy_core_v1_game():
    config = _legacy_core_v1_config()
    game = CatanGame(
        board_seed=41_001,
        variant_config=config,
        ai_player_count=1,
        ai_action_delay_ms=0,
        headless=True,
    )
    game.configure_players(2, reset_logs=False)
    game.variant_state = VariantState.initial(
        config,
        deck_seed=LEGACY_DECK_SEED,
    )
    return game


def test_core_v1_identity_and_deck_remain_loadable_after_catalog_expansion():
    """Adding a new catalog must not reinterpret an existing core_v1 save."""

    config = _legacy_core_v1_config()
    state = VariantState.initial(config, deck_seed=LEGACY_DECK_SEED)
    document = state.to_document()

    assert config.to_document() == LEGACY_CORE_V1_CONFIG_DOCUMENT
    assert document["public"] == {
        "completed_turns": 0,
        "forecast": {
            "event_id": "wheat_harvest_v1",
            "announced_turn": 0,
            "resolve_turn": 2,
        },
        "active_effects": [],
        "resolved_count": 0,
    }
    assert document["private"] == {
        "deck_seed": LEGACY_DECK_SEED,
        "deck_cycle": 0,
        "draw_pile": [
            "sheep_drought_v1",
            "wheat_harvest_v1",
            "sheep_drought_v1",
        ],
        "discard_pile": [],
    }
    assert VariantState.from_document(document, config=config) == state


def test_core_v1_save_network_and_replay_boundaries_survive_catalog_expansion():
    game = _legacy_core_v1_game()
    restored = CatanGame(board_seed=1, headless=True)
    try:
        game.advance_forecast_event_turn()
        game.advance_forecast_event_turn()
        saved = serialize_game(game)
        encoded_save = json.dumps(saved, ensure_ascii=False, allow_nan=False)

        restore_game(restored, saved, runtime_side_effects=False)
        assert restored.variant_config.to_document() == (
            LEGACY_CORE_V1_CONFIG_DOCUMENT
        )
        assert restored.variant_state == game.variant_state
        assert serialize_game(restored) == saved
        assert LEGACY_DECK_SEED in encoded_save

        snapshot = build_state_snapshot(
            game,
            viewer_player_index=0,
            revision=7,
        )
        encoded_snapshot = json.dumps(
            snapshot,
            ensure_ascii=False,
            allow_nan=False,
        )
        assert snapshot["state"]["variant_state"]["public"] == (
            saved["variant_state"]["public"]
        )
        assert "private" not in snapshot["state"]["variant_state"]
        assert LEGACY_DECK_SEED not in encoded_snapshot
        assert "draw_pile" not in encoded_snapshot
        assert "discard_pile" not in encoded_snapshot

        replay = NetworkReplayStore(max_frames=2)
        replay.capture_game("FCV101", game, revision=7)
        for viewer in (0, None):
            frame = replay.frame_payload(
                "FCV101",
                viewer_player_index=viewer,
                frame_index=0,
            )
            encoded_frame = json.dumps(
                frame,
                ensure_ascii=False,
                allow_nan=False,
            )
            assert frame["snapshot"]["state"]["variant_state"]["public"] == (
                saved["variant_state"]["public"]
            )
            assert "private" not in frame["snapshot"]["state"]["variant_state"]
            assert LEGACY_DECK_SEED not in encoded_frame
            assert "draw_pile" not in encoded_frame
            assert "discard_pile" not in encoded_frame
    finally:
        restored.audio.stop()
        game.audio.stop()


def test_core_v2_cycles_every_event_with_public_json_safe_parameters():
    config = VariantConfig.forecast_events()
    random.seed(81_221)
    random_state_before = random.getstate()
    state = VariantState.initial(config, deck_seed=LEGACY_DECK_SEED)
    seen = set()

    while len(seen) < len(CORE_V2_EVENT_IDS):
        forecast_before = copy.deepcopy(state.to_document()["public"]["forecast"])
        turns_until_activation = (
            forecast_before["resolve_turn"]
            - state.public["completed_turns"]
        )
        update = None
        for _ in range(turns_until_activation):
            state, update = state.advance_forecast_turn(config, player_count=3)

        assert update is not None
        assert update.activated_event_id == forecast_before["event_id"]
        effect = next(
            item
            for item in state.to_document()["public"]["active_effects"]
            if item["event_id"] == update.activated_event_id
        )
        assert effect["parameters"] == forecast_before["parameters"]
        seen.add(update.activated_event_id)

        full_document = json.loads(
            json.dumps(
                state.to_document(),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        public_document = json.loads(
            json.dumps(
                state.to_public_document(),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        assert "private" not in public_document
        assert LEGACY_DECK_SEED not in json.dumps(public_document)
        assert VariantState.from_document(full_document, config=config) == state
        assert (
            VariantState.from_public_document(public_document, config=config)
            .to_public_document()
            == public_document
        )

        # Gameplay consumes instant and one-shot effects after resolving them.
        # Doing the same here keeps this pure state-machine walk independent of
        # unrelated game hooks while still checking each activation payload.
        state, consumed = state.consume_forecast_effect(
            update.activated_event_id
        )
        assert consumed is True

    assert seen == CORE_V2_EVENT_IDS == set(EVENT_CATALOG)
    assert random.getstate() == random_state_before


def test_core_v2_public_parameters_are_strict_and_catalog_bound():
    config = VariantConfig.forecast_events()
    state = VariantState.initial(config, deck_seed=LEGACY_DECK_SEED)
    public = state.to_public_document()["public"]

    unexpected_parameter = copy.deepcopy(public)
    unexpected_parameter["forecast"]["parameters"]["authority_only"] = True
    with pytest.raises(ValueError):
        validate_forecast_public(unexpected_parameter)

    legacy_config = _legacy_core_v1_config()
    forged_as_legacy = state.to_document()
    forged_as_legacy["config_fingerprint"] = legacy_config.fingerprint()
    with pytest.raises(ValueError):
        VariantState.from_document(forged_as_legacy, config=legacy_config)


def test_core_v2_network_and_replay_expose_only_announced_parameters():
    game = CatanGame(
        board_seed=41_002,
        variant_config=VariantConfig.forecast_events(),
        ai_player_count=1,
        ai_action_delay_ms=0,
        headless=True,
    )
    game.configure_players(2, reset_logs=False)
    game.variant_state = VariantState.initial(
        game.variant_config,
        deck_seed=LEGACY_DECK_SEED,
    )
    try:
        saved = serialize_game(game)
        public = saved["variant_state"]["public"]
        snapshot = build_state_snapshot(
            game,
            viewer_player_index=None,
            revision=1,
        )
        encoded_snapshot = json.dumps(
            snapshot,
            ensure_ascii=False,
            allow_nan=False,
        )

        assert snapshot["state"]["variant_state"]["public"] == public
        assert "parameters" in public["forecast"]
        assert "private" not in snapshot["state"]["variant_state"]
        assert LEGACY_DECK_SEED not in encoded_snapshot
        assert "draw_pile" not in encoded_snapshot
        assert "discard_pile" not in encoded_snapshot

        replay = NetworkReplayStore(max_frames=1)
        replay.capture_game("FCV201", game, revision=1)
        frame = replay.frame_payload(
            "FCV201",
            viewer_player_index=None,
            frame_index=0,
        )
        encoded_frame = json.dumps(
            frame,
            ensure_ascii=False,
            allow_nan=False,
        )
        assert frame["snapshot"]["state"]["variant_state"]["public"] == public
        assert LEGACY_DECK_SEED not in encoded_frame
        assert "draw_pile" not in encoded_frame
        assert "discard_pile" not in encoded_frame
    finally:
        game.audio.stop()
