import copy
import json
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from game.game import CatanGame
from game.network_actions import (
    NetworkActionError,
    apply_game_command,
    build_game_command_options,
)
from game.network_protocol import build_state_snapshot
from game.network_replay import NetworkReplayError, NetworkReplayStore
from game.network_view import NetworkViewError, parse_state_snapshot
from game.persistence import SaveGameError, load_game, save_game, serialize_game
from game.resources import ResourceType


@pytest.fixture
def game_factory():
    created = []

    def create(*, player_count=2, ai_player_count=0):
        pygame.init()
        pygame.display.set_mode((1, 1))
        game = CatanGame(
            board_seed=812,
            ai_player_count=ai_player_count,
            ai_action_delay_ms=0,
        )
        game.ai_player_count = ai_player_count
        game.configure_players(player_count, reset_logs=False)
        game.start_main_phase()
        game.dice_rolled = True
        created.append(game)
        return game

    yield create

    for game in created:
        game.audio.stop()
    pygame.quit()


def give_from_bank(game, player, resource, amount=1):
    assert game.bank.withdraw(resource, amount)
    player.add_resource(resource, amount)


def prepare_or_offer(game, *, broadcast=False):
    assert game.start_domestic_trade()
    if broadcast:
        assert game.select_domestic_trade_broadcast()
    else:
        assert game.select_domestic_trade_partner(1)
    assert game.adjust_domestic_trade_resource("give", ResourceType.WOOD, 1)
    assert game.adjust_domestic_trade_resource("receive", ResourceType.ORE, 1)
    assert game.adjust_domestic_trade_resource("receive", ResourceType.WHEAT, 1)
    assert game.set_domestic_trade_receive_operator("or")


def option_args(options, command):
    return [option["args"] for option in options if option["command"] == command]


def test_or_acceptance_transfers_only_the_selected_receive_branch(game_factory):
    game = game_factory()
    active, partner = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    give_from_bank(game, partner, ResourceType.ORE)
    give_from_bank(game, partner, ResourceType.WHEAT)

    prepare_or_offer(game)
    valid, message = game.validate_domestic_trade_terms()
    assert valid, message
    assert "または" in game.get_domestic_trade_summary()
    assert game.submit_domestic_trade_offer()
    assert game.reveal_domestic_trade_response()

    assert game.can_execute_domestic_trade() is False
    assert game.can_execute_domestic_trade(ResourceType.ORE) is True
    assert game.can_execute_domestic_trade(ResourceType.WHEAT) is True
    assert game.accept_domestic_trade(ResourceType.WHEAT)

    assert active.resources[ResourceType.WOOD] == 0
    assert active.resources[ResourceType.WHEAT] == 1
    assert active.resources[ResourceType.ORE] == 0
    assert partner.resources[ResourceType.WOOD] == 1
    assert partner.resources[ResourceType.WHEAT] == 0
    assert partner.resources[ResourceType.ORE] == 1


def test_or_requires_two_receive_resources_and_an_explicit_valid_choice(game_factory):
    game = game_factory()
    active, partner = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    give_from_bank(game, partner, ResourceType.ORE)

    assert game.start_domestic_trade()
    assert game.select_domestic_trade_partner(1)
    assert game.adjust_domestic_trade_resource("give", ResourceType.WOOD, 1)
    assert game.adjust_domestic_trade_resource("receive", ResourceType.ORE, 1)
    assert game.set_domestic_trade_receive_operator("or")

    valid, message = game.validate_domestic_trade_terms()
    assert valid is False
    assert "2種類" in message
    assert game.submit_domestic_trade_offer() is False


def test_broadcast_restores_original_or_terms_after_rejected_counter(game_factory):
    game = game_factory(player_count=3)
    active, first, second = game.players
    give_from_bank(game, active, ResourceType.WOOD, 2)
    for responder in (first, second):
        give_from_bank(game, responder, ResourceType.ORE)
        give_from_bank(game, responder, ResourceType.WHEAT)

    prepare_or_offer(game, broadcast=True)
    assert game.submit_domestic_trade_offer()
    assert game.reveal_domestic_trade_response()
    assert game.begin_domestic_trade_counter()
    assert game.set_domestic_trade_edit_side("give")
    assert game.adjust_domestic_trade_resource("give", ResourceType.WOOD, 1)
    assert game.submit_domestic_trade_offer()
    assert game.reveal_domestic_trade_response()
    assert game.reject_domestic_trade(active, "変更条件を拒否しました")

    assert game.domestic_trade_partner is second
    assert game.domestic_trade_receive_operator == "or"
    assert game.domestic_trade_give[ResourceType.WOOD] == 1
    assert game.domestic_trade_receive[ResourceType.ORE] == 1
    assert game.domestic_trade_receive[ResourceType.WHEAT] == 1
    assert game.reveal_domestic_trade_response()
    assert game.accept_domestic_trade(ResourceType.ORE)
    assert active.resources[ResourceType.ORE] == 1
    assert active.resources[ResourceType.WHEAT] == 0


def test_ai_evaluates_or_branches_individually_and_selects_one(game_factory, monkeypatch):
    game = game_factory(ai_player_count=1)
    active, cpu = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    give_from_bank(game, cpu, ResourceType.ORE)
    give_from_bank(game, cpu, ResourceType.WHEAT)

    def evaluate(_player, *, incoming, outgoing):
        return "accept" if outgoing.get(ResourceType.WHEAT, 0) else "reject"

    monkeypatch.setattr(game.ai, "evaluate_domestic_trade", evaluate)
    prepare_or_offer(game)
    assert game.submit_domestic_trade_offer()

    assert game.special_phase is None
    assert active.resources[ResourceType.WHEAT] == 1
    assert active.resources[ResourceType.ORE] == 0
    assert cpu.resources[ResourceType.ORE] == 1
    assert cpu.resources[ResourceType.WHEAT] == 0


def test_or_trade_round_trips_and_legacy_operator_defaults_to_and(
    game_factory,
    tmp_path,
):
    game = game_factory()
    active, partner = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    give_from_bank(game, partner, ResourceType.ORE)
    give_from_bank(game, partner, ResourceType.WHEAT)
    prepare_or_offer(game)
    assert game.submit_domestic_trade_offer()
    assert game.reveal_domestic_trade_response()
    expected = serialize_game(game)

    path = save_game(game, tmp_path / "or-trade.json")
    game.restart_game(randomize_seed=True)
    load_game(game, path)
    assert serialize_game(game) == expected
    assert game.domestic_trade_receive_operator == "or"
    assert game.accept_domestic_trade(ResourceType.ORE)

    legacy = serialize_game(game)
    legacy["domestic_trade"].pop("receive_operator")
    legacy["domestic_trade"].pop("broadcast_receive_operator")
    legacy_path = tmp_path / "legacy-operator.json"
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
    load_game(game, legacy_path)
    assert game.domestic_trade_receive_operator == "and"
    assert game.domestic_trade_broadcast_receive_operator == "and"


def test_restore_rejects_invalid_receive_operator(game_factory, tmp_path):
    game = game_factory()
    document = serialize_game(game)
    document["domestic_trade"]["receive_operator"] = "xor"
    path = tmp_path / "bad-operator.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(SaveGameError, match="結合方法"):
        load_game(game, path)


def test_network_commands_offer_operator_toggle_and_payable_or_accept_branch(
    game_factory,
):
    game = game_factory()
    active, partner = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    give_from_bank(game, partner, ResourceType.WHEAT)

    assert apply_game_command(game, 0, "start_domestic_trade")
    assert apply_game_command(game, 0, "trade_partner", {"seat_index": 1})
    edit_options = build_game_command_options(game, 0)
    assert {"operator": "or"} in option_args(
        edit_options,
        "trade_receive_operator",
    )
    for side, resource in (
        ("give", "WOOD"),
        ("receive", "ORE"),
        ("receive", "WHEAT"),
    ):
        assert apply_game_command(
            game,
            0,
            "trade_adjust",
            {"side": side, "resource": resource, "delta": 1},
        )
    assert apply_game_command(
        game,
        0,
        "trade_receive_operator",
        {"operator": "or"},
    )
    assert apply_game_command(game, 0, "trade_submit")

    response_options = build_game_command_options(game, 1)
    assert option_args(response_options, "trade_accept") == [
        {"resource": "WHEAT"}
    ]
    with pytest.raises(NetworkActionError) as missing_choice:
        apply_game_command(game, 1, "trade_accept", {})
    assert missing_choice.value.code == "invalid_args"
    with pytest.raises(NetworkActionError) as unpaid_choice:
        apply_game_command(game, 1, "trade_accept", {"resource": "ORE"})
    assert unpaid_choice.value.code == "action_not_allowed"
    assert apply_game_command(
        game,
        1,
        "trade_accept",
        {"resource": "WHEAT"},
    )
    assert active.resources[ResourceType.WHEAT] == 1


def test_draft_or_operator_is_private_in_live_and_replay_snapshots(game_factory):
    game = game_factory()
    active, partner = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    give_from_bank(game, partner, ResourceType.WHEAT)
    prepare_or_offer(game)

    editor = build_state_snapshot(game, viewer_player_index=0, revision=5)
    other = build_state_snapshot(game, viewer_player_index=1, revision=5)
    spectator = build_state_snapshot(game, viewer_player_index=None, revision=5)
    assert editor["state"]["domestic_trade"]["receive_operator"] == "or"
    for hidden in (other, spectator):
        trade = hidden["state"]["domestic_trade"]
        assert trade["receive_operator"] == "and"
        assert sum(trade["give"].values()) == 0
        assert sum(trade["receive"].values()) == 0

    store = NetworkReplayStore()
    store.record_snapshot("ORSAFE", spectator)
    leaked = copy.deepcopy(spectator)
    leaked["state"]["domestic_trade"]["receive_operator"] = "or"
    leaked["revision"] = 6
    with pytest.raises(NetworkReplayError) as error:
        store.record_snapshot("ORLEAK", leaked)
    assert error.value.code == "private_state_leak"


def test_network_view_exposes_valid_operator_and_rejects_unknown_value(game_factory):
    game = game_factory()
    active, partner = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    give_from_bank(game, partner, ResourceType.WHEAT)
    prepare_or_offer(game)
    snapshot = build_state_snapshot(game, viewer_player_index=0, revision=2)

    view = parse_state_snapshot(snapshot)
    assert view.domestic_trade.receive_operator == "or"

    malformed = copy.deepcopy(snapshot)
    malformed["state"]["domestic_trade"]["receive_operator"] = "xor"
    with pytest.raises(NetworkViewError, match="receive_operator"):
        parse_state_snapshot(malformed)
