import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from game.building import Building
from game.game import CatanGame
from game.resources import ResourceType
from game.road import Road


def create_main_game(*, ai_player_count=0, player_count=2):
    pygame.init()
    pygame.display.set_mode((1, 1))
    game = CatanGame(board_seed=303, ai_player_count=ai_player_count, ai_action_delay_ms=0)
    game.configure_players(player_count, reset_logs=False)
    game.start_main_phase()
    game.dice_rolled = True
    return game


def give_from_bank(game, player, resource_type, amount=1):
    assert game.bank.withdraw(resource_type, amount)
    player.add_resource(resource_type, amount)


def close_game(game):
    game.audio.stop()
    pygame.quit()


def prepare_offer(game, give_resource, receive_resource):
    assert game.start_domestic_trade()
    partner = game.players[1]
    assert game.select_domestic_trade_partner(game.players.index(partner))
    assert game.adjust_domestic_trade_resource("give", give_resource, 1)
    assert game.adjust_domestic_trade_resource("receive", receive_resource, 1)
    return partner


def test_human_trade_uses_private_handoff_and_preserves_bank():
    game = create_main_game()
    try:
        active, partner = game.players
        give_from_bank(game, active, ResourceType.WOOD)
        give_from_bank(game, partner, ResourceType.SHEEP)
        bank_before = dict(game.bank.resources)

        prepare_offer(game, ResourceType.WOOD, ResourceType.SHEEP)
        assert game.submit_domestic_trade_offer()
        assert game.special_phase == "domestic_trade_handoff"
        assert game.get_domestic_trade_actor() is partner

        assert game.reveal_domestic_trade_response()
        assert game.special_phase == "domestic_trade_response"
        assert game.accept_domestic_trade()

        assert active.resources[ResourceType.WOOD] == 0
        assert active.resources[ResourceType.SHEEP] == 1
        assert partner.resources[ResourceType.WOOD] == 1
        assert partner.resources[ResourceType.SHEEP] == 0
        assert game.bank.resources == bank_before
        assert game.special_phase == "player_handoff"
        assert game.handoff_player is active
        assert game.reveal_player_handoff()
        assert game.special_phase is None
    finally:
        close_game(game)


def test_domestic_trade_is_available_only_after_dice_resolution():
    game = create_main_game()
    try:
        active, partner = game.players
        give_from_bank(game, active, ResourceType.WOOD)
        give_from_bank(game, partner, ResourceType.SHEEP)
        game.dice_rolled = False

        assert game.start_domestic_trade() is False
        assert game.special_phase is None
        assert "ダイス" in game.get_active_feedback().text

        game.dice_rolled = True
        assert game.start_domestic_trade() is True
    finally:
        close_game(game)


def test_domestic_trade_rejects_gifts_and_same_resource_on_both_sides():
    game = create_main_game()
    try:
        active, partner = game.players
        give_from_bank(game, active, ResourceType.WOOD, 2)
        give_from_bank(game, partner, ResourceType.SHEEP)

        assert game.start_domestic_trade()
        assert game.select_domestic_trade_partner(1)
        assert game.adjust_domestic_trade_resource("give", ResourceType.WOOD, 1)
        assert game.submit_domestic_trade_offer() is False
        assert "双方" in game.get_active_feedback().text

        assert game.adjust_domestic_trade_resource("receive", ResourceType.WOOD, 1) is False
        assert game.domestic_trade_receive[ResourceType.WOOD] == 0
    finally:
        close_game(game)


def test_human_counter_offer_returns_to_active_player_for_confirmation():
    game = create_main_game()
    try:
        active, partner = game.players
        give_from_bank(game, active, ResourceType.WOOD, 2)
        give_from_bank(game, partner, ResourceType.SHEEP)

        prepare_offer(game, ResourceType.WOOD, ResourceType.SHEEP)
        assert game.submit_domestic_trade_offer()
        assert game.reveal_domestic_trade_response()
        assert game.begin_domestic_trade_counter()
        assert game.domestic_trade_editor is partner

        game.set_domestic_trade_edit_side("give")
        assert game.adjust_domestic_trade_resource("give", ResourceType.WOOD, 1)
        assert game.submit_domestic_trade_offer()
        assert game.special_phase == "domestic_trade_counter_handoff"
        assert game.get_domestic_trade_actor() is active

        assert game.reveal_domestic_trade_response()
        assert game.accept_domestic_trade()
        assert active.resources[ResourceType.WOOD] == 0
        assert active.resources[ResourceType.SHEEP] == 1
        assert partner.resources[ResourceType.WOOD] == 2
    finally:
        close_game(game)


def test_ai_partner_can_accept_a_useful_offer():
    game = create_main_game(ai_player_count=1)
    try:
        active, cpu = game.players
        give_from_bank(game, active, ResourceType.WHEAT)
        give_from_bank(game, cpu, ResourceType.WOOD)

        prepare_offer(game, ResourceType.WHEAT, ResourceType.WOOD)
        assert game.submit_domestic_trade_offer()

        assert game.special_phase is None
        assert active.resources[ResourceType.WOOD] == 1
        assert cpu.resources[ResourceType.WHEAT] == 1
        assert "交易成立" in game.latest_event["title"]
    finally:
        close_game(game)


def test_ai_partner_can_return_a_counter_offer_without_revealing_its_hand():
    game = create_main_game(ai_player_count=1)
    try:
        active, cpu = game.players
        give_from_bank(game, active, ResourceType.WOOD, 2)
        give_from_bank(game, cpu, ResourceType.WOOD)
        give_from_bank(game, cpu, ResourceType.WHEAT)

        prepare_offer(game, ResourceType.WOOD, ResourceType.WHEAT)
        assert game.submit_domestic_trade_offer()

        assert game.special_phase == "domestic_trade_counter_response"
        assert game.domestic_trade_give[ResourceType.WOOD] == 2
        assert game.domestic_trade_receive[ResourceType.WHEAT] == 1
        assert "条件変更" in game.latest_event["title"]
    finally:
        close_game(game)


def test_different_players_may_meet_at_an_empty_intersection_but_not_share_an_edge():
    game = create_main_game()
    try:
        red, blue = game.players
        center = next(node for node in game.board.nodes if len(game.get_adjacent_nodes(node)) >= 3)
        adjacent = game.get_adjacent_nodes(center)[:3]
        game.board.roads.append(Road(red, center, adjacent[0]))
        game.board.roads.append(Road(blue, center, adjacent[1]))

        can_meet, _ = game.can_place_road(blue, center, adjacent[2])
        same_edge, same_edge_message = game.can_place_road(blue, center, adjacent[0])

        assert can_meet is True
        assert same_edge is False
        assert "既に街道" in same_edge_message

        center.building = Building(red)
        game.board.roads.pop()
        blocked, blocked_message = game.can_place_road(blue, center, adjacent[2])
        assert blocked is False
        assert "自分" in blocked_message
    finally:
        close_game(game)
