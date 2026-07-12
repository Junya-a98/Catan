import json
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from game.building import Building, BuildingType
from game.game import CatanGame
from game.persistence import SaveGameError, load_game, save_game, serialize_game
from game.resources import ResourceType
from game.road import Road


@pytest.fixture
def game():
    pygame.init()
    pygame.display.set_mode((1, 1))
    instance = CatanGame(board_seed=8181, ai_player_count=1, ai_action_delay_ms=1250)
    instance.configure_players(2, reset_logs=False)
    yield instance
    instance.audio.stop()
    pygame.quit()


def withdraw_to_player(game, player, resource_type, amount):
    assert game.bank.withdraw(resource_type, amount)
    player.add_resource(resource_type, amount)


def prepare_complex_main_state(game):
    human, cpu = game.players
    game.start_main_phase()
    game.dice_rolled = True

    human_node = game.board.nodes[0]
    cpu_node = game.board.nodes[12]
    human_node.building = Building(human)
    human.settlements_remaining -= 1
    cpu_node.building = Building(cpu, BuildingType.CITY)
    cpu.cities_remaining -= 1

    human_edge = game.board.edges[0]
    cpu_edge = next(edge for edge in game.board.edges if edge != human_edge)
    game.board.roads.append(Road(human, *human_edge))
    human.roads_remaining -= 1
    game.board.roads.append(Road(cpu, *cpu_edge))
    cpu.roads_remaining -= 1

    withdraw_to_player(game, human, ResourceType.WOOD, 2)
    withdraw_to_player(game, human, ResourceType.BRICK, 1)
    withdraw_to_player(game, cpu, ResourceType.SHEEP, 2)
    withdraw_to_player(game, cpu, ResourceType.ORE, 1)

    card = game.development_deck.pop()
    cpu.add_development_card(card, available=False)
    game.board.robber_tile = game.board.tiles[3]
    game.special_phase = "domestic_trade_edit"
    game.domestic_trade_partner = cpu
    game.domestic_trade_editor = human
    game.domestic_trade_give[ResourceType.WOOD] = 1
    game.domestic_trade_receive[ResourceType.SHEEP] = 1
    game.record_public_gain(cpu, {ResourceType.SHEEP: 1}, "出目9")
    game.last_resource_distribution = {
        cpu.name: {ResourceType.SHEEP: 1},
    }
    game.ai_status = {
        "player_name": cpu.name,
        "title": "交易を検討",
        "detail": "都市に必要な資源を優先",
    }
    game.show_log_panel = True
    game.log_scroll_offset = 1
    game.add_log("保存前の確認ログ")


def test_complex_game_state_round_trips_through_json(game, tmp_path):
    prepare_complex_main_state(game)
    expected = serialize_game(game)
    save_path = tmp_path / "nested" / "save.json"

    saved_path = save_game(game, save_path)
    game.restart_game(randomize_seed=True)
    load_game(game, saved_path)

    assert serialize_game(game) == expected
    assert game.special_phase == "domestic_trade_edit"
    assert game.domestic_trade_partner is game.players[1]
    assert game.board.robber_tile is game.board.tiles[3]
    assert saved_path.exists()
    assert not saved_path.with_suffix(".json.tmp").exists()


def test_quick_save_and_load_restore_resources_with_feedback(game, tmp_path):
    human = game.players[0]
    withdraw_to_player(game, human, ResourceType.WHEAT, 2)
    game.quick_save_path = tmp_path / "quicksave.json"

    assert game.quick_save() is True
    human.resources[ResourceType.WHEAT] = 0
    game.bank.resources[ResourceType.WHEAT] = 19

    assert game.quick_load() is True
    assert game.players[0].resources[ResourceType.WHEAT] == 2
    assert game.bank.resources[ResourceType.WHEAT] == 17
    assert "読み込みました" in game.get_active_feedback().text


def test_f5_and_f9_global_shortcuts_save_and_restore(game, tmp_path):
    game.quick_save_path = tmp_path / "keyboard-save.json"
    human = game.players[0]
    withdraw_to_player(game, human, ResourceType.ORE, 1)

    assert game.handle_global_ui_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F5)
    ) is True
    human.resources[ResourceType.ORE] = 0
    game.bank.resources[ResourceType.ORE] = 19
    assert game.handle_global_ui_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F9)
    ) is True

    assert game.players[0].resources[ResourceType.ORE] == 1
    assert game.bank.resources[ResourceType.ORE] == 18


def test_save_is_rejected_during_dice_animation(game, tmp_path):
    game.start_dice_animation("initial", (3, 4), "Player1", "初期ダイス")

    with pytest.raises(SaveGameError, match="ダイス演出中"):
        save_game(game, tmp_path / "save.json")


def test_corrupt_save_rolls_back_without_changing_current_game(game, tmp_path):
    withdraw_to_player(game, game.players[0], ResourceType.BRICK, 2)
    before = serialize_game(game)
    save_path = save_game(game, tmp_path / "save.json")
    data = json.loads(save_path.read_text(encoding="utf-8"))
    data["version"] = 999
    save_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SaveGameError, match="バージョン"):
        load_game(game, save_path)

    assert serialize_game(game) == before


def test_tampered_resource_total_is_rejected_and_rolled_back(game, tmp_path):
    before = serialize_game(game)
    save_path = save_game(game, tmp_path / "save.json")
    data = json.loads(save_path.read_text(encoding="utf-8"))
    data["bank"]["WOOD"] = 18
    save_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SaveGameError, match="総数"):
        load_game(game, save_path)

    assert serialize_game(game) == before
