import json
import os
import random

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest

from game.building import Building
from game.forecast_events import (
    BANDIT_RAID_EVENT_ID,
    CONSTRUCTION_BOOM_EVENT_ID,
    EARTHQUAKE_EVENT_ID,
    HARBOR_BLOCKADE_EVENT_ID,
    MERCHANT_FESTIVAL_EVENT_ID,
)
from game.game import CatanGame
from game.network_protocol import build_board_reference_index, build_state_snapshot
from game.network_replay import NetworkReplayStore
from game.persistence import restore_game, serialize_game
from game.road import Road
from game.resources import ResourceType
from game.variant import VariantConfig
from game.variant_state import VariantState


DECK_SEED = "1" * 64


@pytest.fixture
def forecast_v2_game():
    game = CatanGame(
        board_seed=82_401,
        variant_config=VariantConfig.forecast_events(),
        headless=True,
    )
    game.configure_players(2, reset_logs=False)
    game.variant_state = VariantState.initial(
        game.variant_config,
        deck_seed=DECK_SEED,
    )
    yield game
    game.audio.stop()


def _activate(game, event_id):
    for _cycle in range(8):
        next_id = game.get_next_forecast_event_id()
        remaining = game.get_next_forecast_turns_remaining()
        if next_id == event_id:
            parameters = game.get_next_forecast_parameters()
            for _ in range(remaining):
                game.advance_forecast_event_turn()
            return parameters
        for _ in range(remaining):
            game.advance_forecast_event_turn()
    raise AssertionError(f"event did not activate: {event_id}")


def _clear_board(game):
    game.board.roads.clear()
    for node in game.board.nodes:
        node.building = None


def _give_from_bank(game, player, resource_type, amount=1):
    assert game.give_resource_from_bank(player, resource_type, amount) == amount


def _total_resource_cards(game):
    return game.bank.total_cards() + sum(
        player.total_resource_count() for player in game.players
    )


def _active_effect(public, event_id):
    return next(
        effect
        for effect in public["active_effects"]
        if effect["event_id"] == event_id
    )


def _blocked_manifest_ids(snapshot, collection):
    return {
        item["id"]
        for item in snapshot["board_manifest"][collection]
        if item["forecast_blocked"]
    }


def test_harbor_blockade_disables_only_the_announced_harbor_for_two_turns(
    forecast_v2_game,
):
    game = forecast_v2_game
    while game.get_next_forecast_event_id() != HARBOR_BLOCKADE_EVENT_ID:
        for _ in range(game.get_next_forecast_turns_remaining()):
            game.advance_forecast_event_turn()
    parameters = game.get_next_forecast_parameters()
    harbor = game.get_forecast_harbor(parameters["harbor_id"])
    assert harbor is not None

    player = game.players[0]
    _clear_board(game)
    harbor.node1.building = Building(player)
    before = game.get_trade_rates(player)
    affected_resource = harbor.resource_type or ResourceType.WOOD
    assert before[affected_resource] == harbor.trade_rate

    for _ in range(game.get_next_forecast_turns_remaining()):
        game.advance_forecast_event_turn()
    assert game.is_forecast_harbor_blocked(harbor)
    assert game.get_trade_rates(player)[affected_resource] == 4

    game.advance_forecast_event_turn()
    assert game.is_forecast_harbor_blocked(harbor)
    game.advance_forecast_event_turn()
    assert not game.is_forecast_harbor_blocked(harbor)
    assert game.get_trade_rates(player)[affected_resource] == harbor.trade_rate
    assert game.latest_event["title"] == "イベント終了: 港湾封鎖"


def test_construction_boom_uses_one_optimal_discount_and_only_on_paid_success(
    forecast_v2_game,
):
    game = forecast_v2_game
    _activate(game, CONSTRUCTION_BOOM_EVENT_ID)
    assert game.is_forecast_event_active(CONSTRUCTION_BOOM_EVENT_ID)

    player = game.players[0]
    _clear_board(game)
    anchor, _other = game.board.edges[0]
    anchor.building = Building(player)
    _give_from_bank(game, player, ResourceType.BRICK)
    edge = game.get_buildable_road_edges(player)[0]
    brick_before = game.bank.available(ResourceType.BRICK)

    game.build_road(((edge[0].x + edge[1].x) / 2, (edge[0].y + edge[1].y) / 2))

    assert len(game.board.roads) == 1
    assert player.resources[ResourceType.BRICK] == 0
    assert player.resources[ResourceType.WOOD] == 0
    assert game.bank.available(ResourceType.BRICK) == brick_before + 1
    assert not game.is_forecast_event_active(CONSTRUCTION_BOOM_EVENT_ID)
    assert "建設ブーム適用" in game.latest_event["detail"]


def test_free_road_does_not_consume_construction_boom(forecast_v2_game):
    game = forecast_v2_game
    _activate(game, CONSTRUCTION_BOOM_EVENT_ID)
    player = game.players[0]
    _clear_board(game)
    anchor, _other = game.board.edges[0]
    anchor.building = Building(player)
    edge = game.get_buildable_road_edges(player, require_affordability=False)[0]
    game.special_phase = "road_building"
    game.free_roads_remaining = 1

    game.handle_free_road_build_click(
        ((edge[0].x + edge[1].x) / 2, (edge[0].y + edge[1].y) / 2)
    )

    assert len(game.board.roads) == 1
    assert game.is_forecast_event_active(CONSTRUCTION_BOOM_EVENT_ID)


@pytest.mark.parametrize("available_bonus_cards", [0, 1, 2])
def test_merchant_festival_is_fair_and_preserves_all_resource_cards(
    forecast_v2_game,
    available_bonus_cards,
):
    game = forecast_v2_game
    _activate(game, MERCHANT_FESTIVAL_EVENT_ID)
    active, partner = game.players
    for player in game.players:
        for resource_type in player.resources:
            player.resources[resource_type] = 0
    _give_from_bank(game, active, ResourceType.WOOD)
    _give_from_bank(game, partner, ResourceType.ORE)
    for resource_type in game.bank.resources:
        game.bank.resources[resource_type] = 0
    game.bank.resources[ResourceType.WHEAT] = available_bonus_cards

    game.phase = "main"
    game.turn_order = game.players.copy()
    game.current_player_index = 0
    game.special_phase = "domestic_trade_response"
    game.domestic_trade_partner = partner
    game.domestic_trade_give = {
        resource_type: int(resource_type is ResourceType.WOOD)
        for resource_type in active.resources
    }
    game.domestic_trade_receive = {
        resource_type: int(resource_type is ResourceType.ORE)
        for resource_type in active.resources
    }
    game.domestic_trade_receive_operator = "and"
    total_before = _total_resource_cards(game)
    random.seed(510 + available_bonus_cards)

    assert game.accept_domestic_trade() is True

    assert _total_resource_cards(game) == total_before
    expected_hand_size = 2 if available_bonus_cards >= 2 else 1
    assert active.total_resource_count() == expected_hand_size
    assert partner.total_resource_count() == expected_hand_size
    if available_bonus_cards >= 2:
        assert game.bank.available(ResourceType.WHEAT) == 0
    else:
        assert game.bank.available(ResourceType.WHEAT) == available_bonus_cards


def test_bandit_raid_moves_without_discard_or_theft_and_consumes_immediately(
    forecast_v2_game,
):
    game = forecast_v2_game
    while game.get_next_forecast_event_id() != BANDIT_RAID_EVENT_ID:
        for _ in range(game.get_next_forecast_turns_remaining()):
            game.advance_forecast_event_turn()
    target_number = game.get_next_forecast_parameters()["target_number"]
    candidates = [tile for tile in game.board.tiles if tile.number == target_number]
    assert candidates
    target = candidates[0]
    _clear_board(game)
    target.corners[0].building = Building(game.players[1])
    game.board.robber_tile = next(tile for tile in game.board.tiles if tile is not target)
    special_phase_before = game.special_phase
    resources_before = [dict(player.resources) for player in game.players]

    for _ in range(game.get_next_forecast_turns_remaining()):
        game.advance_forecast_event_turn()

    assert game.board.robber_tile is target
    assert [dict(player.resources) for player in game.players] == resources_before
    assert game.special_phase == special_phase_before
    assert not game.is_forecast_event_active(BANDIT_RAID_EVENT_ID)
    assert game.latest_event["title"] == "山賊襲来を解決"


def test_earthquake_keeps_road_piece_but_breaks_connections_for_one_round(
    forecast_v2_game,
):
    game = forecast_v2_game
    while game.get_next_forecast_event_id() != EARTHQUAKE_EVENT_ID:
        for _ in range(game.get_next_forecast_turns_remaining()):
            game.advance_forecast_event_turn()
    sector = game.get_next_forecast_parameters()["sector"]
    edge = next(
        candidate
        for candidate in game.board.edges
        if game.get_forecast_edge_sector(candidate) == sector
    )
    player = game.players[0]
    _clear_board(game)
    road = Road(player, edge[0], edge[1])
    game.board.roads.append(road)
    assert game.is_road_usable(road)
    assert game.player_has_road_touching_node(player, edge[1])

    for _ in range(game.get_next_forecast_turns_remaining()):
        game.advance_forecast_event_turn()

    assert road in game.board.roads
    assert not game.is_road_usable(road)
    assert not game.player_has_road_touching_node(player, edge[1])
    assert game.get_player_longest_road_length(player) == 0

    game.advance_forecast_event_turn()
    assert not game.is_road_usable(road)
    game.advance_forecast_event_turn()
    assert game.is_road_usable(road)
    assert game.player_has_road_touching_node(player, edge[1])
    assert game.get_player_longest_road_length(player) == 1


def test_harbor_blockade_save_lan_and_replay_preserve_public_transition(
    forecast_v2_game,
):
    game = forecast_v2_game
    parameters = _activate(game, HARBOR_BLOCKADE_EVENT_ID)
    target_harbor_id = parameters["harbor_id"]
    saved = serialize_game(game)

    active_snapshot = build_state_snapshot(
        game,
        viewer_player_index=None,
        revision=40,
    )
    active_public = active_snapshot["state"]["variant_state"]["public"]
    active_effect = _active_effect(active_public, HARBOR_BLOCKADE_EVENT_ID)
    assert active_effect["parameters"] == {"harbor_id": target_harbor_id}
    assert _blocked_manifest_ids(active_snapshot, "harbors") == {
        target_harbor_id
    }
    assert _blocked_manifest_ids(active_snapshot, "edges") == set()
    assert "private" not in active_snapshot["state"]["variant_state"]
    encoded_snapshot = json.dumps(active_snapshot, ensure_ascii=False)
    assert DECK_SEED not in encoded_snapshot
    assert "draw_pile" not in encoded_snapshot

    restored = CatanGame(board_seed=1, headless=True)
    try:
        restore_game(restored, saved, runtime_side_effects=False)
        restored_snapshot = build_state_snapshot(
            restored,
            viewer_player_index=0,
            revision=40,
        )
        assert restored.variant_state == game.variant_state
        assert restored_snapshot["state"]["variant_state"]["public"] == (
            active_public
        )
        assert _blocked_manifest_ids(restored_snapshot, "harbors") == {
            target_harbor_id
        }
    finally:
        restored.audio.stop()

    replay = NetworkReplayStore(max_frames=3)
    replay.capture_game("FCV2HB", game, revision=40)
    game.advance_forecast_event_turn()
    replay.capture_game("FCV2HB", game, revision=41)
    game.advance_forecast_event_turn()
    replay.capture_game("FCV2HB", game, revision=42)

    for frame_index in (0, 1):
        frame = replay.frame_payload(
            "FCV2HB",
            viewer_player_index=None,
            frame_index=frame_index,
        )
        assert _blocked_manifest_ids(frame["snapshot"], "harbors") == {
            target_harbor_id
        }
        public = frame["snapshot"]["state"]["variant_state"]["public"]
        assert _active_effect(public, HARBOR_BLOCKADE_EVENT_ID)[
            "parameters"
        ] == {"harbor_id": target_harbor_id}

    expired_frame = replay.frame_payload(
        "FCV2HB",
        viewer_player_index=None,
        frame_index=2,
    )
    assert _blocked_manifest_ids(expired_frame["snapshot"], "harbors") == set()
    assert all(
        effect["event_id"] != HARBOR_BLOCKADE_EVENT_ID
        for effect in expired_frame["snapshot"]["state"]["variant_state"][
            "public"
        ]["active_effects"]
    )


def test_earthquake_save_restore_and_manifest_keep_roads_but_publish_blockage(
    forecast_v2_game,
):
    game = forecast_v2_game
    parameters = _activate(game, EARTHQUAKE_EVENT_ID)
    sector = parameters["sector"]
    edge_references = build_board_reference_index(game)["edge"]
    expected_blocked_ids = {
        edge_id
        for edge_id, edge in edge_references.items()
        if game.get_forecast_edge_sector(edge) == sector
    }
    assert expected_blocked_ids

    blocked_edge_id = min(expected_blocked_ids)
    blocked_edge = edge_references[blocked_edge_id]
    road = Road(game.players[0], *blocked_edge)
    game.board.roads.append(road)
    game.players[0].roads_remaining -= 1
    assert not game.is_road_usable(road)

    saved = serialize_game(game)
    snapshot = build_state_snapshot(
        game,
        viewer_player_index=None,
        revision=60,
    )
    public = snapshot["state"]["variant_state"]["public"]
    assert _active_effect(public, EARTHQUAKE_EVENT_ID)["parameters"] == {
        "sector": sector
    }
    assert _blocked_manifest_ids(snapshot, "edges") == expected_blocked_ids
    assert _blocked_manifest_ids(snapshot, "harbors") == set()
    blocked_manifest_edge = next(
        edge
        for edge in snapshot["board_manifest"]["edges"]
        if edge["id"] == blocked_edge_id
    )
    assert blocked_manifest_edge["road"] == {"owner_player_index": 0}

    restored = CatanGame(board_seed=1, headless=True)
    try:
        restore_game(restored, saved, runtime_side_effects=False)
        restored_snapshot = build_state_snapshot(
            restored,
            viewer_player_index=None,
            revision=60,
        )
        assert restored.variant_state == game.variant_state
        assert len(restored.board.roads) == 1
        assert not restored.is_road_usable(restored.board.roads[0])
        assert _blocked_manifest_ids(
            restored_snapshot,
            "edges",
        ) == expected_blocked_ids

        restored.advance_forecast_event_turn()
        assert _blocked_manifest_ids(
            build_state_snapshot(
                restored,
                viewer_player_index=None,
                revision=61,
            ),
            "edges",
        ) == expected_blocked_ids
        restored.advance_forecast_event_turn()
        expired_snapshot = build_state_snapshot(
            restored,
            viewer_player_index=None,
            revision=62,
        )
        assert _blocked_manifest_ids(expired_snapshot, "edges") == set()
        assert restored.is_road_usable(restored.board.roads[0])
    finally:
        restored.audio.stop()
