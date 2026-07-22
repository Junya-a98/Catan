"""Cross-feature authority and privacy coverage for ``grand_campaign_v1``."""

from __future__ import annotations

import copy
import json

import pytest

from game.forecast_events import (
    CAMPAIGN_FORECAST_CATALOG_ID,
    FORECAST_CATALOG_ID,
    FORECAST_EVENTS_KIND,
    HARBOR_BLOCKADE_EVENT_ID,
)
from game.frontier import EXPANDED_FRONTIER_CATALOG, FRONTIER_KIND
from game.game import CatanGame
from game.grand_campaign import HarborBlockadePlan, create_harbor_blockade_plan
from game.lan_lobby import RoomSettings
from game.lan_controller import LanServerController
from game.network_protocol import (
    NETWORK_PROTOCOL_VERSION,
    build_board_reference_index,
    build_state_snapshot,
)
from game.network_replay import NetworkReplayStore
from game.network_view import NetworkViewError, parse_state_snapshot
from game.persistence import SaveGameError, restore_game, serialize_game
from game.self_play import run_match
from game.variant import (
    COMPOSITE_EVENTS_ECONOMY_CATALOG,
    COMPOSITE_GRAND_CAMPAIGN_CATALOG,
    COMPOSITE_VARIANT_KIND,
    CREDIT_CATALOG,
    CREDIT_VARIANT_KIND,
    TRADE2_AUCTION_CATALOG,
    TRADE2_VARIANT_KIND,
    VariantConfig,
)
from game.variant_state import VariantState, VariantStateError


BLOCKADE_FIRST_SEED = f"{1:064x}"


def _game(seed=97_001):
    game = CatanGame(
        board_seed=seed,
        variant_config=VariantConfig.composite_grand_campaign(),
        headless=True,
    )
    game.configure_players(3, reset_logs=False, schedule_ai=False)
    game.turn_order = list(game.players)
    return game


def _frontier_state(game):
    state = game.get_variant_component_state(FRONTIER_KIND)
    assert state is not None
    return state


def _reveal_harbor(game, harbor_id):
    harbor = build_board_reference_index(game)["harbor"][harbor_id]
    adjacent = game.board.get_edge_adjacent_tiles((harbor.node1, harbor.node2))
    frontier = _frontier_state(game)
    next_frontier, revealed = frontier.reveal_frontier_tiles(
        [tile.axial for tile in adjacent]
    )
    assert revealed
    game.replace_variant_component_state(FRONTIER_KIND, next_frontier)
    assert harbor_id in game.get_public_harbor_ids()


def _force_blockade_forecast(game):
    config = game.get_variant_component_config(FORECAST_EVENTS_KIND)
    assert config.options["catalog"] == CAMPAIGN_FORECAST_CATALOG_ID
    forecast = VariantState.initial(
        config,
        deck_seed=BLOCKADE_FIRST_SEED,
        forecast_harbor_ids=list(game.get_public_harbor_ids() or ()),
    )
    assert forecast.next_forecast_event_id() == HARBOR_BLOCKADE_EVENT_ID
    game.replace_variant_component_state(FORECAST_EVENTS_KIND, forecast)


def _forecast_public(game):
    return game.variant_state.to_public_document()["public"]["components"][
        FORECAST_EVENTS_KIND
    ]


def _announced_plan(game):
    return HarborBlockadePlan.from_public_document(
        _forecast_public(game)["forecast"]["parameters"]["campaign_plan"]
    )


def test_grand_campaign_is_one_fixed_hidden_37_tile_composite():
    config = VariantConfig.composite_grand_campaign()

    assert config.to_document() == {
        "version": 1,
        "kind": COMPOSITE_VARIANT_KIND,
        "options": {"catalog": COMPOSITE_GRAND_CAMPAIGN_CATALOG},
    }
    assert VariantConfig.from_document(config.to_document()) == config
    assert config.board_topology_id() == EXPANDED_FRONTIER_CATALOG
    assert config.uses_hidden_board() is True
    assert config.component_config(FORECAST_EVENTS_KIND).options["catalog"] == (
        CAMPAIGN_FORECAST_CATALOG_ID
    )
    assert config.component_config(FRONTIER_KIND) == VariantConfig.frontier_expanded()
    assert config.component_config(TRADE2_VARIANT_KIND).options["catalog"] == (
        TRADE2_AUCTION_CATALOG
    )
    assert config.component_config(CREDIT_VARIANT_KIND).options["catalog"] == (
        CREDIT_CATALOG
    )

    for options in (
        {"catalog": COMPOSITE_GRAND_CAMPAIGN_CATALOG, "components": []},
        {"catalog": COMPOSITE_GRAND_CAMPAIGN_CATALOG, "frontier": {}},
        {"catalog": "grand_campaign_v2"},
    ):
        with pytest.raises(ValueError, match="composite"):
            VariantConfig(kind=COMPOSITE_VARIANT_KIND, options=options)
    with pytest.raises(ValueError, match="forecast_events"):
        VariantConfig.forecast_events(
            catalog=CAMPAIGN_FORECAST_CATALOG_ID,
            forecast_lead_turns=3,
        )

    # Registering the campaign must not reinterpret the established bundle or
    # direct core-v2 forecast identity.
    assert VariantConfig.composite_events_economy().options["catalog"] == (
        COMPOSITE_EVENTS_ECONOMY_CATALOG
    )
    assert VariantConfig.composite_events_economy().fingerprint() == (
        "f2a4dd9cb51ec1da93ae23e855015b0f4667acf188a1b7cfe1c340b6aea3d101"
    )
    assert VariantConfig.forecast_events().options["catalog"] == FORECAST_CATALOG_ID


def test_initial_blockade_has_no_public_harbor_and_skips_without_an_effect():
    config = VariantConfig.composite_grand_campaign()
    state = VariantState.initial(
        config,
        deck_seed=BLOCKADE_FIRST_SEED,
        frontier_robber_axial=(0, 0),
        forecast_harbor_ids=[],
    )
    initial_frontier = copy.deepcopy(
        state.to_document()["public"]["components"][FRONTIER_KIND]
    )
    plan = HarborBlockadePlan.from_public_document(
        state.to_public_document()["public"]["components"][FORECAST_EVENTS_KIND][
            "forecast"
        ]["parameters"]["campaign_plan"]
    )

    assert plan.skipped is True
    for expected_clock in (1, 2):
        state, update = state.advance_composite_turn(
            config,
            player_count=3,
            revealed_harbor_ids=[],
        )
        assert state.public["completed_turns"] == expected_clock
        assert (
            state.to_document()["public"]["components"][FRONTIER_KIND]
            == initial_frontier
        )
        for timed_kind in (
            FORECAST_EVENTS_KIND,
            TRADE2_VARIANT_KIND,
            CREDIT_VARIANT_KIND,
        ):
            assert (
                state.public["components"][timed_kind]["completed_turns"]
                == expected_clock
            )
    assert update.forecast.skipped_event_id == HARBOR_BLOCKADE_EVENT_ID
    assert update.forecast.activated_event_id == HARBOR_BLOCKADE_EVENT_ID
    assert state.public["components"][FORECAST_EVENTS_KIND]["active_effects"] == ()


def test_blockade_targets_only_announced_public_pool_and_later_discovery_is_immutable():
    game = _game()
    try:
        assert game.get_public_harbor_ids() == ()
        all_harbors = tuple(build_board_reference_index(game)["harbor"])
        _reveal_harbor(game, all_harbors[0])
        announced_pool = game.get_public_harbor_ids()
        assert announced_pool
        _force_blockade_forecast(game)
        announced = _announced_plan(game)
        announcement_document = announced.to_public_document()
        assert set(announced.eligible_harbor_ids) == set(announced_pool)
        assert announced.target_harbor_id in announced_pool

        later_harbor = next(
            harbor_id for harbor_id in all_harbors if harbor_id not in announced_pool
        )
        _reveal_harbor(game, later_harbor)
        assert set(game.get_public_harbor_ids()) > set(announced_pool)
        assert _announced_plan(game).to_public_document() == announcement_document

        game.advance_variant_turn_boundary()
        game.advance_variant_turn_boundary()
        effect = game.get_active_forecast_effect(HARBOR_BLOCKADE_EVENT_ID)
        assert effect is not None
        active_plan_document = _forecast_public(game)["active_effects"][0][
            "parameters"
        ]["campaign_plan"]
        active_plan = HarborBlockadePlan.from_public_document(active_plan_document)
        assert active_plan.to_public_document() == announcement_document
        target = game.get_forecast_harbor(active_plan.target_harbor_id)
        assert target is not None
        assert game.is_forecast_harbor_blocked(target)
        assert later_harbor not in active_plan.eligible_harbor_ids
    finally:
        game.audio.stop()


def test_game_logs_an_explicit_skip_and_never_activates_empty_blockade():
    game = _game(seed=97_002)
    try:
        game.add_log = game.log_messages.append
        _force_blockade_forecast(game)
        game.advance_variant_turn_boundary()
        game.advance_variant_turn_boundary()

        assert game.get_active_forecast_effect(HARBOR_BLOCKADE_EVENT_ID) is None
        assert any("イベント見送り — 港湾封鎖" in line for line in game.log_messages)
        assert "公開済みの交換所がなかった" in game.latest_event["detail"]
    finally:
        game.audio.stop()


def test_snapshot_save_restore_and_replay_preserve_campaign_privacy():
    game = _game(seed=97_003)
    restored = CatanGame(headless=True)
    try:
        first_harbor = next(iter(build_board_reference_index(game)["harbor"]))
        _reveal_harbor(game, first_harbor)
        _force_blockade_forecast(game)

        snapshot = build_state_snapshot(game, viewer_player_index=0, revision=9)
        encoded_snapshot = json.dumps(snapshot, ensure_ascii=False)
        assert len(snapshot["board_manifest"]["tiles"]) == 37
        assert snapshot["state"]["board"]["seed"] == 0
        assert {item["id"] for item in snapshot["board_manifest"]["harbors"]} == set(
            game.get_public_harbor_ids()
        )
        assert "deck_seed" not in encoded_snapshot
        assert "next_sequence" not in encoded_snapshot
        assert "next_auction_sequence" not in encoded_snapshot
        assert parse_state_snapshot(snapshot).variant_state.public["catalog"] == (
            COMPOSITE_GRAND_CAMPAIGN_CATALOG
        )

        store = NetworkReplayStore(max_frames=2)
        store.capture_game("GRAND1", game, revision=9)
        replay = store.frame_payload(
            "GRAND1",
            viewer_player_index=0,
            frame_index=0,
        )["snapshot"]
        encoded_replay = json.dumps(replay, ensure_ascii=False)
        assert "deck_seed" not in encoded_replay
        assert "next_sequence" not in encoded_replay
        assert len(replay["board_manifest"]["tiles"]) == 37

        saved = serialize_game(game)
        restore_game(restored, copy.deepcopy(saved), runtime_side_effects=False)
        assert restored.variant_config == game.variant_config
        assert restored.variant_state == game.variant_state
        assert restored.get_public_harbor_ids() == game.get_public_harbor_ids()
        assert len(restored.board.tiles) == 37
    finally:
        game.audio.stop()
        restored.audio.stop()


def test_network_and_restore_reject_a_seed_valid_plan_that_names_a_hidden_harbor():
    game = _game(seed=97_004)
    restored = CatanGame(headless=True)
    try:
        first_harbor = next(iter(build_board_reference_index(game)["harbor"]))
        _reveal_harbor(game, first_harbor)
        _force_blockade_forecast(game)
        public_ids = set(game.get_public_harbor_ids())
        hidden_id = next(
            harbor_id
            for harbor_id in build_board_reference_index(game)["harbor"]
            if harbor_id not in public_ids
        )
        saved = serialize_game(game)
        forecast_public = saved["variant_state"]["public"]["components"][
            FORECAST_EVENTS_KIND
        ]
        forecast_private = saved["variant_state"]["private"]["components"][
            FORECAST_EVENTS_KIND
        ]
        forged = create_harbor_blockade_plan(
            sorted((*public_ids, hidden_id)),
            secret_seed=forecast_private["deck_seed"],
            resolution_number=forecast_public["resolved_count"],
        ).to_public_document()
        forecast_public["forecast"]["parameters"]["campaign_plan"] = forged

        with pytest.raises(SaveGameError, match="未公開交換所"):
            restore_game(restored, saved, runtime_side_effects=False)

        snapshot = build_state_snapshot(game, viewer_player_index=0)
        snapshot["state"]["variant_state"]["public"]["components"][
            FORECAST_EVENTS_KIND
        ]["forecast"]["parameters"]["campaign_plan"] = forged
        with pytest.raises(NetworkViewError, match="hidden harbor"):
            parse_state_snapshot(snapshot)
    finally:
        game.audio.stop()
        restored.audio.stop()


def test_grand_component_schema_and_clock_tampering_are_strictly_rejected():
    config = VariantConfig.composite_grand_campaign()
    state = VariantState.initial(
        config,
        deck_seed=BLOCKADE_FIRST_SEED,
        frontier_robber_axial=(0, 0),
        forecast_harbor_ids=[],
    )
    full = state.to_document()

    missing_frontier = copy.deepcopy(full)
    del missing_frontier["public"]["components"][FRONTIER_KIND]
    with pytest.raises(VariantStateError, match="components"):
        VariantState.from_document(missing_frontier, config=config)

    frontier_clock = copy.deepcopy(full)
    frontier_clock["public"]["components"][FRONTIER_KIND]["completed_turns"] = 0
    with pytest.raises(VariantStateError):
        VariantState.from_document(frontier_clock, config=config)

    timed_clock = copy.deepcopy(full)
    timed_clock["public"]["components"][CREDIT_VARIANT_KIND][
        "completed_turns"
    ] = 1
    with pytest.raises(VariantStateError, match="完了手番"):
        VariantState.from_document(timed_clock, config=config)

    public = state.to_public_document()
    assert "private" not in public
    projection = VariantState.from_public_document(public, config=config)
    assert projection.to_public_document() == public


def test_campaign_schedule_and_active_plan_tampering_are_rejected():
    config = VariantConfig.composite_grand_campaign()
    state = VariantState.initial(
        config,
        deck_seed=BLOCKADE_FIRST_SEED,
        frontier_robber_axial=(0, 0),
        forecast_harbor_ids=["harbor-1"],
    )
    for _ in range(2):
        state, _update = state.advance_composite_turn(
            config,
            player_count=3,
            revealed_harbor_ids=["harbor-1"],
        )
    full = state.to_document()
    active = full["public"]["components"][FORECAST_EVENTS_KIND][
        "active_effects"
    ][0]

    wrong_started = copy.deepcopy(full)
    wrong_started["public"]["components"][FORECAST_EVENTS_KIND][
        "active_effects"
    ][0]["started_turn"] += 6
    with pytest.raises(VariantStateError):
        VariantState.from_document(wrong_started, config=config)

    skip_active = copy.deepcopy(full)
    forecast_private = skip_active["private"]["components"][FORECAST_EVENTS_KIND]
    active_resolution = active["parameters"]["campaign_plan"]["resolution_number"]
    skip_active["public"]["components"][FORECAST_EVENTS_KIND][
        "active_effects"
    ][0]["parameters"]["campaign_plan"] = create_harbor_blockade_plan(
        [],
        secret_seed=forecast_private["deck_seed"],
        resolution_number=active_resolution,
    ).to_public_document()
    with pytest.raises(VariantStateError):
        VariantState.from_document(skip_active, config=config)

    game = _game(seed=97_005)
    try:
        _force_blockade_forecast(game)
        snapshot = build_state_snapshot(game, viewer_player_index=0)
        campaign_forecast = snapshot["state"]["variant_state"]["public"][
            "components"
        ][FORECAST_EVENTS_KIND]["forecast"]
        campaign_forecast["resolve_turn"] = 3
        with pytest.raises(NetworkViewError, match="fixed schedule"):
            parse_state_snapshot(snapshot)

        clock_snapshot = build_state_snapshot(game, viewer_player_index=0)
        clock_snapshot["state"]["variant_state"]["public"]["components"][
            FORECAST_EVENTS_KIND
        ]["completed_turns"] = 1
        with pytest.raises(NetworkViewError, match="component clock"):
            parse_state_snapshot(clock_snapshot)
    finally:
        game.audio.stop()


def test_lan_settings_accept_only_the_fixed_grand_campaign_document_and_mask_seed():
    config = VariantConfig.composite_grand_campaign()
    settings = RoomSettings(
        player_count=3,
        board_seed=123_456,
        variant=config.to_document(),
    )

    assert settings.variant == config
    assert settings.to_public_dict()["board_seed"] == 0
    assert settings.to_public_dict()["variant"] == config.to_document()

    invalid = config.to_document()
    invalid["options"]["components"] = [FRONTIER_KIND]
    with pytest.raises(ValueError):
        RoomSettings(player_count=3, variant=invalid)

    controller = LanServerController()
    outbound = controller.handle(
        "host",
        {
            "type": "create_room",
            "protocol_version": NETWORK_PROTOCOL_VERSION,
            "display_name": "Host",
            "settings": {
                "player_count": 2,
                "victory_target": 5,
                "board_mode": "constrained",
                "board_seed": 999,
                "ai_player_count": 1,
                "variant": config.to_document(),
            },
        },
    )
    lobby = next(
        item.message["lobby"]
        for item in outbound
        if item.message["type"] == "lobby_snapshot"
    )
    assert lobby["settings"]["variant"] == config.to_document()
    assert lobby["settings"]["board_seed"] == 0


def test_grand_campaign_headless_ai_does_not_stall():
    result = run_match(
        match_seed=97_100,
        player_count=3,
        victory_target=5,
        max_turns=8,
        max_action_steps=2_000,
        variant_config=VariantConfig.composite_grand_campaign(),
    )

    assert result.reason in {"victory", "turn_limit"}
    assert result.reason != "stalled"
    assert result.action_steps < 2_000
    assert result.validation_errors == ()
