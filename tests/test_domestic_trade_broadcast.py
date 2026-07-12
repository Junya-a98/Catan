import json
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from game.game import CatanGame
from game.persistence import SaveGameError, load_game, save_game, serialize_game
from game.resources import ResourceType


@pytest.fixture
def game_factory():
    created_games = []

    def create(*, player_count=3, ai_player_count=0):
        pygame.init()
        pygame.display.set_mode((1, 1))
        game = CatanGame(
            board_seed=707,
            ai_player_count=ai_player_count,
            ai_action_delay_ms=0,
        )
        # CatanGame initially creates a two-player table and therefore clamps the
        # requested count. Restore the requested value before resizing the table.
        game.ai_player_count = ai_player_count
        game.configure_players(player_count, reset_logs=False)
        game.start_main_phase()
        game.dice_rolled = True
        created_games.append(game)
        return game

    yield create

    for game in created_games:
        game.audio.stop()
    pygame.quit()


def give_from_bank(game, player, resource_type, amount=1):
    assert game.bank.withdraw(resource_type, amount)
    player.add_resource(resource_type, amount)


def begin_broadcast(game, *, give=ResourceType.WOOD, receive=ResourceType.SHEEP):
    assert game.start_domestic_trade()
    actions = {button.action for button in game.build_buttons()}
    assert "domestic_trade_broadcast" in actions
    assert any(action.startswith("domestic_trade_partner_") for action in actions)
    assert game.select_domestic_trade_broadcast()
    assert game.adjust_domestic_trade_resource("give", give, 1)
    assert game.adjust_domestic_trade_resource("receive", receive, 1)


def test_broadcast_moves_to_next_human_after_rejection_and_uses_first_acceptance(
    game_factory,
):
    game = game_factory()
    active, first, second = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    give_from_bank(game, first, ResourceType.SHEEP)
    give_from_bank(game, second, ResourceType.SHEEP)
    bank_before = dict(game.bank.resources)

    begin_broadcast(game)
    assert game.submit_domestic_trade_offer()
    assert game.domestic_trade_partner is first
    assert game.special_phase == "domestic_trade_handoff"
    assert game.get_domestic_trade_broadcast_progress() == "全員募集 1/2"

    assert game.reveal_domestic_trade_response()
    assert game.reject_domestic_trade(first)
    assert game.domestic_trade_partner is second
    assert game.special_phase == "domestic_trade_handoff"
    assert game.get_domestic_trade_broadcast_progress() == "全員募集 2/2"

    assert game.reveal_domestic_trade_response()
    assert game.accept_domestic_trade()
    assert active.resources[ResourceType.WOOD] == 0
    assert active.resources[ResourceType.SHEEP] == 1
    assert first.resources[ResourceType.SHEEP] == 1
    assert second.resources[ResourceType.WOOD] == 1
    assert second.resources[ResourceType.SHEEP] == 0
    assert game.bank.resources == bank_before
    assert game.special_phase == "player_handoff"
    assert game.handoff_player is active


def test_broadcast_skips_rejecting_ai_and_trades_with_next_ai(
    game_factory,
    monkeypatch,
):
    game = game_factory(ai_player_count=2)
    active, first_cpu, second_cpu = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    give_from_bank(game, first_cpu, ResourceType.SHEEP)
    give_from_bank(game, second_cpu, ResourceType.SHEEP)

    def evaluate(responder, *, incoming, outgoing):
        return "reject" if responder is first_cpu else "accept"

    monkeypatch.setattr(game.ai, "evaluate_domestic_trade", evaluate)

    begin_broadcast(game)
    assert game.submit_domestic_trade_offer()

    assert game.special_phase is None
    assert active.resources[ResourceType.SHEEP] == 1
    assert first_cpu.resources[ResourceType.SHEEP] == 1
    assert first_cpu.resources[ResourceType.WOOD] == 0
    assert second_cpu.resources[ResourceType.SHEEP] == 0
    assert second_cpu.resources[ResourceType.WOOD] == 1
    assert "交易成立" in game.latest_event["title"]


def test_rejected_counter_restores_original_offer_for_the_next_responder(
    game_factory,
):
    game = game_factory()
    active, first, second = game.players
    give_from_bank(game, active, ResourceType.WOOD, 2)
    give_from_bank(game, first, ResourceType.SHEEP)
    give_from_bank(game, second, ResourceType.SHEEP)

    begin_broadcast(game)
    assert game.submit_domestic_trade_offer()
    assert game.reveal_domestic_trade_response()
    assert game.begin_domestic_trade_counter()
    assert game.set_domestic_trade_edit_side("give")
    assert game.adjust_domestic_trade_resource("give", ResourceType.WOOD, 1)
    assert game.domestic_trade_give[ResourceType.WOOD] == 2
    assert game.submit_domestic_trade_offer()
    assert game.special_phase == "domestic_trade_counter_handoff"

    assert game.reveal_domestic_trade_response()
    assert game.reject_domestic_trade(active, "変更条件を拒否しました")
    assert game.domestic_trade_partner is second
    assert game.special_phase == "domestic_trade_handoff"
    assert game.domestic_trade_is_counter is False
    assert game.domestic_trade_give[ResourceType.WOOD] == 1
    assert game.domestic_trade_receive[ResourceType.SHEEP] == 1


def test_broadcast_ends_cleanly_when_every_ai_rejects(game_factory, monkeypatch):
    game = game_factory(ai_player_count=2)
    active, first_cpu, second_cpu = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    give_from_bank(game, first_cpu, ResourceType.SHEEP)
    give_from_bank(game, second_cpu, ResourceType.SHEEP)
    resources_before = [dict(player.resources) for player in game.players]

    monkeypatch.setattr(
        game.ai,
        "evaluate_domestic_trade",
        lambda *args, **kwargs: "reject",
    )

    begin_broadcast(game)
    assert game.submit_domestic_trade_offer()

    assert game.special_phase is None
    assert [dict(player.resources) for player in game.players] == resources_before
    assert game.latest_event["title"].endswith("交易募集は不成立")
    assert any("全員が交易募集を拒否" in message for message in game.log_messages)


def test_cancel_before_revealing_response_is_attributed_to_current_viewer(
    game_factory,
):
    game = game_factory()
    active, first, second = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    give_from_bank(game, first, ResourceType.SHEEP)
    give_from_bank(game, second, ResourceType.SHEEP)
    begin_broadcast(game)
    assert game.submit_domestic_trade_offer()

    assert game.special_phase == "domestic_trade_handoff"
    assert game.cancel_domestic_trade()

    assert game.special_phase is None
    assert game.log_messages[-1] == f"{active.name} が国内交易を終了しました。"


def test_broadcast_response_round_trips_with_privacy_state(game_factory, tmp_path):
    game = game_factory()
    active, first, second = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    give_from_bank(game, first, ResourceType.SHEEP)
    give_from_bank(game, second, ResourceType.SHEEP)

    begin_broadcast(game)
    assert game.submit_domestic_trade_offer()
    assert game.reveal_domestic_trade_response()
    expected = serialize_game(game)

    path = save_game(game, tmp_path / "broadcast.json")
    game.restart_game(randomize_seed=True)
    load_game(game, path)

    assert serialize_game(game) == expected
    assert game.domestic_trade_partner is game.players[1]
    assert game.domestic_trade_broadcast_viewer is game.players[1]
    assert game.accept_domestic_trade()
    assert game.special_phase == "player_handoff"
    assert game.handoff_player is game.players[0]


def test_legacy_direct_trade_save_defaults_new_broadcast_fields(
    game_factory,
    tmp_path,
):
    game = game_factory(player_count=2)
    active, partner = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    give_from_bank(game, partner, ResourceType.SHEEP)
    assert game.start_domestic_trade()
    assert game.select_domestic_trade_partner(1)
    assert game.adjust_domestic_trade_resource("give", ResourceType.WOOD, 1)
    assert game.adjust_domestic_trade_resource("receive", ResourceType.SHEEP, 1)

    data = serialize_game(game)
    for key in (
        "is_broadcast",
        "broadcast_responders",
        "broadcast_index",
        "broadcast_give",
        "broadcast_receive",
        "broadcast_viewer",
    ):
        data["domestic_trade"].pop(key)
    path = tmp_path / "legacy-direct.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    load_game(game, path)

    assert game.domestic_trade_is_broadcast is False
    assert game.domestic_trade_broadcast_responders == []
    assert game.domestic_trade_broadcast_index == -1
    assert game.domestic_trade_partner is game.players[1]


def test_tampered_broadcast_responder_order_is_rejected(game_factory, tmp_path):
    game = game_factory(player_count=4)
    active, *responders = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    for responder in responders:
        give_from_bank(game, responder, ResourceType.SHEEP)
    begin_broadcast(game)

    data = serialize_game(game)
    data["domestic_trade"]["broadcast_responders"] = [3]
    path = tmp_path / "skipped-responders.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SaveGameError, match="回答順"):
        load_game(game, path)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("broadcast_viewer", 0),
        ("broadcast_viewer", True),
        ("broadcast_index", -1),
        ("broadcast_index", True),
    ),
)
def test_tampered_broadcast_privacy_state_is_rejected(
    game_factory,
    tmp_path,
    field,
    value,
):
    game = game_factory()
    active, first, second = game.players
    give_from_bank(game, active, ResourceType.WOOD)
    give_from_bank(game, first, ResourceType.SHEEP)
    give_from_bank(game, second, ResourceType.SHEEP)
    begin_broadcast(game)
    assert game.submit_domestic_trade_offer()
    assert game.reveal_domestic_trade_response()

    data = serialize_game(game)
    data["domestic_trade"][field] = value
    path = tmp_path / f"tampered-{field}.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SaveGameError, match="募集"):
        load_game(game, path)
