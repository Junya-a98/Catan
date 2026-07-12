import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from game.building import Building
from game.development_cards import DevelopmentCardType
from game.game import CatanGame
from game.resources import BUILD_COSTS


def create_game():
    pygame.init()
    pygame.display.set_mode((1, 1))
    return CatanGame(board_seed=303)


def close_game(game):
    game.audio.stop()
    pygame.quit()


def test_victory_point_cards_stay_out_of_public_score_until_game_ends():
    game = create_game()
    try:
        player = game.players[0]
        player.victory_point_cards = 2

        assert game.get_points_by_player()[player.name] == 0

        game.phase = "finished"
        assert game.get_points_by_player()[player.name] == 2
    finally:
        close_game(game)


def test_robber_may_target_an_adjacent_player_with_an_empty_hand():
    game = create_game()
    try:
        game.start_main_phase()
        current_player, victim = game.turn_order[:2]
        tile = next(tile for tile in game.board.tiles if tile is not game.board.robber_tile)
        tile.corners[0].building = Building(victim)

        targets = game.get_robber_target_players(tile)

        assert victim in targets
        assert current_player not in targets
    finally:
        close_game(game)


def test_development_purchase_log_keeps_card_type_private():
    game = create_game()
    try:
        game.start_main_phase()
        player = game.get_current_player()
        game.dice_rolled = True
        game.development_deck = [DevelopmentCardType.KNIGHT]
        for resource_type, amount in BUILD_COSTS["development"].items():
            game.bank.withdraw(resource_type, amount)
            player.add_resource(resource_type, amount)

        game.buy_development_card()

        assert any("発展カードを1枚購入" in message for message in game.log_messages)
        assert all("騎士" not in message for message in game.log_messages)
    finally:
        close_game(game)


def test_player_already_at_ten_points_wins_at_start_of_their_turn_without_rolling():
    game = create_game()
    try:
        game.start_main_phase()
        next_player = game.turn_order[1]
        next_player.victory_point_cards = 10
        game.dice_rolled = True

        game.finish_current_turn()

        assert game.winner is next_player
        assert game.phase == "finished"
    finally:
        close_game(game)
