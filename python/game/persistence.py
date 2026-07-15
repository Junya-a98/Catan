import json
import os
from pathlib import Path
import random

from game.assets import PROJECT_ROOT
from game.ai import AI_SPEED_OPTIONS
from game.ai_personality import (
    normalize_ai_personality,
    normalize_ai_personality_mode,
)
from game.bank import BANK_RESOURCE_COUNT, RESOURCE_TYPES
from game.building import Building, BuildingType
from game.constants import (
    MAX_VICTORY_POINT_TARGET,
    MIN_VICTORY_POINT_TARGET,
    WINNING_VICTORY_POINTS,
)
from game.custom_map import CustomMapError, CustomMapSpec
from game.development_cards import DevelopmentCardType
from game.game_board import GameBoard
from game.house_rules import HouseRules
from game.match_metrics import (
    MatchMetrics,
    MatchMetricsError,
    restore_match_metrics,
    serialize_match_metrics,
)
from game.resources import ResourceType
from game.road import Road
from game.variant import VariantConfig
from game.variant_state import VariantState, VariantStateError


SAVE_FORMAT = "catan-local-save"
SAVE_VERSION = 1
DEFAULT_SAVE_PATH = PROJECT_ROOT / "saves" / "quicksave.json"


class SaveGameError(ValueError):
    pass


SUPPORTED_SPECIAL_PHASES = {
    None,
    "player_handoff",
    "discard",
    "move_robber",
    "steal",
    "year_of_plenty",
    "monopoly",
    "road_building",
    "bank_trade_give",
    "bank_trade_receive",
    "domestic_trade_partner",
    "domestic_trade_edit",
    "domestic_trade_handoff",
    "domestic_trade_response",
    "domestic_trade_counter_handoff",
    "domestic_trade_counter_response",
}


def _resource_map_to_json(values):
    return {
        resource_type.name: int(values.get(resource_type, 0))
        for resource_type in RESOURCE_TYPES
    }


def _resource_map_from_json(values, *, label):
    if not isinstance(values, dict):
        raise SaveGameError(f"{label} の資源データが不正です。")
    result = {}
    for resource_type in RESOURCE_TYPES:
        value = values.get(resource_type.name, 0)
        if not isinstance(value, int) or value < 0:
            raise SaveGameError(f"{label} の {resource_type.name} が不正です。")
        result[resource_type] = value
    return result


def _card_map_to_json(values):
    return {card_type.name: int(amount) for card_type, amount in values.items()}


def _card_map_from_json(values, *, label):
    if not isinstance(values, dict):
        raise SaveGameError(f"{label} の発展カードデータが不正です。")
    result = {}
    for card_type in (
        DevelopmentCardType.KNIGHT,
        DevelopmentCardType.ROAD_BUILDING,
        DevelopmentCardType.YEAR_OF_PLENTY,
        DevelopmentCardType.MONOPOLY,
    ):
        amount = values.get(card_type.name, 0)
        if not isinstance(amount, int) or amount < 0:
            raise SaveGameError(f"{label} の {card_type.name} が不正です。")
        result[card_type] = amount
    return result


def _optional_index(value, objects, *, label):
    if value is None:
        return None
    try:
        return objects.index(value)
    except ValueError as exc:
        raise SaveGameError(f"{label} を保存できません。") from exc


def _restore_ref(index, objects, *, label, allow_none=True):
    if index is None and allow_none:
        return None
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or not 0 <= index < len(objects)
    ):
        raise SaveGameError(f"{label} の参照先が不正です。")
    return objects[index]


def _restore_refs(indices, objects, *, label):
    if not isinstance(indices, list):
        raise SaveGameError(f"{label} が配列ではありません。")
    return [
        _restore_ref(index, objects, label=label, allow_none=False)
        for index in indices
    ]


def _serialize_match_metrics(game):
    """Return a valid metrics document, including for legacy test doubles."""

    metrics = getattr(game, "match_metrics", None)
    if metrics is None:
        metrics = MatchMetrics()
    try:
        return serialize_match_metrics(metrics)
    except (MatchMetricsError, TypeError, ValueError) as exc:
        raise SaveGameError("対局メトリクスを保存できません。") from exc


def _restore_match_metrics(data):
    """Restore optional v1 metrics while keeping old saves compatible."""

    if "match_metrics" not in data:
        return MatchMetrics()
    try:
        return restore_match_metrics(data["match_metrics"])
    except (MatchMetricsError, TypeError, ValueError) as exc:
        raise SaveGameError("対局メトリクスが不正です。") from exc


def _house_rules_for_game(game):
    house_rules = getattr(game, "house_rules", None)
    if house_rules is None:
        return HouseRules.standard()
    if not isinstance(house_rules, HouseRules):
        raise SaveGameError("ハウスルール設定を保存できません。")
    return house_rules


def _variant_config_for_game(game):
    variant_config = getattr(game, "variant_config", None)
    if variant_config is None:
        return VariantConfig.standard()
    if not isinstance(variant_config, VariantConfig):
        raise SaveGameError("variant設定を保存できません。")
    return variant_config


def _variant_state_for_game(game, variant_config):
    variant_state = getattr(game, "variant_state", None)
    if variant_state is None:
        return VariantState.initial(variant_config)
    if not isinstance(variant_state, VariantState):
        raise SaveGameError("variant stateを保存できません。")
    try:
        variant_state.validate_config(variant_config)
    except VariantStateError as exc:
        raise SaveGameError("variant stateと設定が一致しません。") from exc
    return variant_state


def _custom_map_for_game(game):
    if getattr(game, "board_mode", None) != "custom":
        return None
    custom_map = getattr(game, "custom_map_spec", None)
    if not isinstance(custom_map, CustomMapSpec):
        raise SaveGameError("カスタム盤面設定を保存できません。")
    try:
        board_map = CustomMapSpec.from_board(game.board, name=custom_map.name)
    except (CustomMapError, TypeError, ValueError) as exc:
        raise SaveGameError("カスタム盤面設定を保存できません。") from exc
    if board_map.fingerprint != custom_map.fingerprint:
        raise SaveGameError("カスタム盤面設定と現在の盤面が一致しません。")
    return custom_map


def _valid_fingerprint(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def _custom_map_from_board_document(board):
    mode = board.get("mode")
    has_document = "custom_map" in board
    has_fingerprint = "custom_map_fingerprint" in board
    if mode != "custom":
        if has_document or has_fingerprint:
            raise SaveGameError("生成盤面にカスタムマップ設定が混在しています。")
        return None
    if not has_document or not has_fingerprint:
        raise SaveGameError("カスタム盤面の完全な設定がありません。")
    fingerprint = board["custom_map_fingerprint"]
    if not _valid_fingerprint(fingerprint):
        raise SaveGameError("カスタム盤面の識別子が不正です。")
    try:
        custom_map = CustomMapSpec.from_document(board["custom_map"])
    except (CustomMapError, TypeError, ValueError) as exc:
        raise SaveGameError("カスタム盤面設定が不正です。") from exc
    if custom_map.fingerprint != fingerprint:
        raise SaveGameError("カスタム盤面の内容と識別子が一致しません。")
    return custom_map


def serialize_game(game):
    players = list(game.players)
    nodes = list(game.board.nodes)
    tiles = list(game.board.tiles)
    player_indices = {player: index for index, player in enumerate(players)}
    node_indices = {node: index for index, node in enumerate(nodes)}
    tile_indices = {tile: index for index, tile in enumerate(tiles)}

    buildings = []
    for node in nodes:
        if node.building is None:
            continue
        buildings.append(
            {
                "node": node_indices[node],
                "owner": player_indices[node.building.owner],
                "type": node.building.building_type.value,
            }
        )

    roads = [
        {
            "owner": player_indices[road.owner],
            "node1": node_indices[road.node1],
            "node2": node_indices[road.node2],
        }
        for road in game.board.roads
    ]

    latest_event = dict(game.latest_event)
    if "color" in latest_event:
        latest_event["color"] = list(latest_event["color"])

    house_rules = _house_rules_for_game(game)
    variant_config = _variant_config_for_game(game)
    variant_state = _variant_state_for_game(game, variant_config)
    rules_document = {
        "victory_point_target": int(game.victory_point_target),
        "variant": variant_config.to_document(),
    }
    if house_rules != HouseRules.standard():
        rules_document["house_rules"] = house_rules.to_document()

    board_document = {
        "mode": game.board_mode,
        "seed": game.board_seed,
        "robber_tile": tile_indices.get(game.board.robber_tile),
        "buildings": buildings,
        "roads": roads,
    }
    custom_map = _custom_map_for_game(game)
    if custom_map is not None:
        board_document["custom_map"] = custom_map.to_document()
        board_document["custom_map_fingerprint"] = custom_map.fingerprint

    return {
        "format": SAVE_FORMAT,
        "version": SAVE_VERSION,
        "rules": rules_document,
        "variant_state": variant_state.to_document(),
        "match_metrics": _serialize_match_metrics(game),
        "board": board_document,
        "players": [
            {
                "name": player.name,
                "color": list(player.color),
                "is_ai": bool(player.is_ai),
                "ai_personality": normalize_ai_personality(
                    getattr(player, "ai_personality", "standard")
                ),
                "piece_pattern": int(player.piece_pattern),
                "marker": player.marker,
                "resources": _resource_map_to_json(player.resources),
                "roads_remaining": int(player.roads_remaining),
                "settlements_remaining": int(player.settlements_remaining),
                "cities_remaining": int(player.cities_remaining),
                "played_knights": int(player.played_knights),
                "victory_point_cards": int(player.victory_point_cards),
                "development_cards": _card_map_to_json(player.development_cards),
                "new_development_cards": _card_map_to_json(player.new_development_cards),
            }
            for player in players
        ],
        "bank": _resource_map_to_json(game.bank.resources),
        "development_deck": [card_type.name for card_type in game.development_deck],
        "phase": {
            "name": game.phase,
            "current_player_index": int(game.current_player_index),
            "turn_order": [player_indices[player] for player in game.turn_order],
            "dice_rolled": bool(game.dice_rolled),
            "last_dice_pair": (
                list(game.last_dice_pair)
                if getattr(game, "last_dice_pair", None) is not None
                else None
            ),
            "action_mode": game.action_mode,
            "development_card_used_this_turn": bool(game.development_card_used_this_turn),
            "special_phase": game.special_phase,
            "winner": _optional_index(game.winner, players, label="勝者"),
            "longest_road_owner": _optional_index(
                game.longest_road_owner,
                players,
                label="最長交易路",
            ),
            "longest_road_length": int(game.longest_road_length),
            "largest_army_owner": _optional_index(
                game.largest_army_owner,
                players,
                label="最大騎士力",
            ),
            "largest_army_size": int(game.largest_army_size),
        },
        "initial": {
            "dice_phase": bool(game.initial_dice_phase),
            "dice_results": dict(game.initial_dice_results),
            "dice_histories": {
                name: list(values)
                for name, values in game.initial_dice_histories.items()
            },
            "dice_contenders": [player_indices[player] for player in game.initial_dice_contenders],
            "placement_order": [player_indices[player] for player in game.initial_placement_order],
            "placement_counts": dict(game.initial_placement_counts),
            "round": int(game.initial_round),
            "player_index": int(game.initial_player_index),
            "waiting_for_road": bool(game.waiting_for_road),
            "last_settlement_node": _optional_index(
                game.last_settlement_node,
                nodes,
                label="初期開拓地",
            ),
        },
        "special": {
            "discard_queue": [player_indices[player] for player in game.discard_queue],
            "discard_player": _optional_index(game.discard_player, players, label="捨て札"),
            "discard_remaining": int(game.discard_remaining),
            "robber_tile_candidates": [tile_indices[tile] for tile in game.robber_tile_candidates],
            "robber_target_players": [player_indices[player] for player in game.robber_target_players],
            "resource_selection_remaining": int(game.resource_selection_remaining),
            "free_roads_remaining": int(game.free_roads_remaining),
            "bank_trade_give_resource": (
                game.bank_trade_give_resource.name
                if game.bank_trade_give_resource is not None
                else None
            ),
            "handoff_player": _optional_index(game.handoff_player, players, label="画面交代"),
            "handoff_return_phase": game.handoff_return_phase,
            "handoff_context": game.handoff_context,
        },
        "domestic_trade": {
            "partner": _optional_index(game.domestic_trade_partner, players, label="交渉相手"),
            "give": _resource_map_to_json(game.domestic_trade_give),
            "receive": _resource_map_to_json(game.domestic_trade_receive),
            "edit_side": game.domestic_trade_edit_side,
            "editor": _optional_index(game.domestic_trade_editor, players, label="交渉編集者"),
            "is_counter": bool(game.domestic_trade_is_counter),
            "is_broadcast": bool(game.domestic_trade_is_broadcast),
            "broadcast_responders": [
                player_indices[player]
                for player in game.domestic_trade_broadcast_responders
            ],
            "broadcast_index": int(game.domestic_trade_broadcast_index),
            "broadcast_give": _resource_map_to_json(
                game.domestic_trade_broadcast_give
            ),
            "broadcast_receive": _resource_map_to_json(
                game.domestic_trade_broadcast_receive
            ),
            "broadcast_viewer": _optional_index(
                game.domestic_trade_broadcast_viewer,
                players,
                label="募集画面の閲覧者",
            ),
        },
        "history": {
            "log_messages": list(game.log_messages),
            "latest_event": latest_event,
            "turn_summary_entries": list(game.turn_summary_entries),
            "public_gain_history": game.public_gain_history,
            "last_resource_distribution": {
                name: _resource_map_to_json(bundle)
                for name, bundle in game.last_resource_distribution.items()
            },
        },
        "ai": {
            "player_count": int(game.ai_player_count),
            "personality_mode": normalize_ai_personality_mode(
                getattr(game, "ai_personality_mode", "standard")
            ),
            "action_delay_ms": int(game.ai_action_delay_ms),
            "speed_index": int(game.ai_speed_index),
            "paused": bool(game.ai_paused),
            "status": dict(game.ai_status),
            "domestic_trade_attempted": bool(game.ai_domestic_trade_attempted),
        },
        "ui": {
            "show_help_panel": bool(game.show_help_panel),
            "show_log_panel": bool(game.show_log_panel),
            "log_scroll_offset": int(game.log_scroll_offset),
        },
    }


def _validate_save_header(data):
    if not isinstance(data, dict):
        raise SaveGameError("セーブデータの形式が不正です。")
    if data.get("format") != SAVE_FORMAT:
        raise SaveGameError("このゲームのセーブデータではありません。")
    if data.get("version") != SAVE_VERSION:
        raise SaveGameError(
            f"未対応のセーブバージョンです: {data.get('version')}"
        )
    board = data.get("board")
    players = data.get("players")
    if not isinstance(board, dict) or board.get("mode") not in (
        "constrained",
        "fully_random",
        "custom",
    ):
        raise SaveGameError("盤面設定が不正です。")
    if isinstance(board.get("seed"), bool) or not isinstance(board.get("seed"), int):
        raise SaveGameError("盤面seedが不正です。")
    _custom_map_from_board_document(board)
    if not isinstance(players, list) or not 2 <= len(players) <= 4:
        raise SaveGameError("プレイヤー数が不正です。")


def restore_game(game, data, *, runtime_side_effects=True):
    _validate_save_header(data)
    restored_match_metrics = _restore_match_metrics(data)
    board_data = data["board"]
    custom_map = _custom_map_from_board_document(board_data)
    player_data = data["players"]
    rules_data = data.get("rules", {})
    if not isinstance(rules_data, dict):
        raise SaveGameError("ルール設定が不正です。")
    try:
        house_rules = HouseRules.from_document(rules_data.get("house_rules"))
    except (TypeError, ValueError) as exc:
        raise SaveGameError("ハウスルール設定が不正です。") from exc
    try:
        variant_config = VariantConfig.from_document(rules_data.get("variant"))
    except (TypeError, ValueError) as exc:
        raise SaveGameError("variant設定が不正です。") from exc
    try:
        variant_state = VariantState.from_document(
            data.get("variant_state"),
            config=variant_config,
        )
    except (TypeError, ValueError) as exc:
        raise SaveGameError("variant stateが不正です。") from exc
    victory_point_target = rules_data.get(
        "victory_point_target",
        WINNING_VICTORY_POINTS,
    )
    if (
        not isinstance(victory_point_target, int)
        or not MIN_VICTORY_POINT_TARGET <= victory_point_target <= MAX_VICTORY_POINT_TARGET
    ):
        raise SaveGameError("勝利点の目標値が不正です。")
    game.victory_point_target = victory_point_target
    game.house_rules = house_rules
    game.variant_config = variant_config
    game.variant_state = variant_state

    game.board_mode = board_data["mode"]
    game.board_seed = board_data["seed"]
    game.board_seed_text = str(game.board_seed)
    game.custom_map_spec = custom_map
    if custom_map is None:
        game.board = GameBoard(mode=game.board_mode, seed=game.board_seed)
    else:
        game.board = GameBoard(
            mode=game.board_mode,
            seed=game.board_seed,
            custom_map=custom_map,
        )
    game.get_board_rules().set_board(game.board)

    ai_data = data.get("ai", {})
    ai_player_count = ai_data.get("player_count", 0)
    if not isinstance(ai_player_count, int) or not 0 <= ai_player_count < len(player_data):
        raise SaveGameError("AIプレイヤー数が不正です。")
    game.ai_player_count = ai_player_count
    game.ai_personality_mode = normalize_ai_personality_mode(
        ai_data.get("personality_mode", "standard")
    )
    # Rebuilding the player objects creates a shuffled placeholder development
    # deck.  A replay seek must not consume the process-wide RNG or arm the AI
    # timer, because historical frames are strictly read-only.
    random_state = random.getstate() if not runtime_side_effects else None
    try:
        game.configure_players(
            len(player_data),
            reset_logs=False,
            schedule_ai=runtime_side_effects,
            reset_replay=False,
        )
    finally:
        if random_state is not None:
            random.setstate(random_state)

    for index, saved_player in enumerate(player_data):
        if not isinstance(saved_player, dict):
            raise SaveGameError("プレイヤーデータが不正です。")
        player = game.players[index]
        player.name = str(saved_player.get("name", player.name))
        color = saved_player.get("color", list(player.color))
        if not isinstance(color, list) or len(color) < 3:
            raise SaveGameError("プレイヤー色が不正です。")
        player.color = tuple(int(channel) for channel in color[:3])
        if any(channel < 0 or channel > 255 for channel in player.color):
            raise SaveGameError("プレイヤー色の範囲が不正です。")
        player.is_ai = bool(saved_player.get("is_ai", False))
        player.ai_personality = normalize_ai_personality(
            saved_player.get("ai_personality", "standard")
        )
        player.piece_pattern = int(saved_player.get("piece_pattern", index))
        player.marker = str(saved_player.get("marker", player.marker))
        player.resources = _resource_map_from_json(
            saved_player.get("resources"),
            label=player.name,
        )
        for field in (
            "roads_remaining",
            "settlements_remaining",
            "cities_remaining",
            "played_knights",
            "victory_point_cards",
        ):
            value = saved_player.get(field)
            if not isinstance(value, int) or value < 0:
                raise SaveGameError(f"{player.name} の {field} が不正です。")
            setattr(player, field, value)
        player.development_cards = _card_map_from_json(
            saved_player.get("development_cards"),
            label=player.name,
        )
        player.new_development_cards = _card_map_from_json(
            saved_player.get("new_development_cards"),
            label=player.name,
        )

    players = game.players
    nodes = game.board.nodes
    tiles = game.board.tiles
    phase_data = data.get("phase", {})
    initial_data = data.get("initial", {})
    special_data = data.get("special", {})
    trade_data = data.get("domestic_trade", {})
    history_data = data.get("history", {})
    ui_data = data.get("ui", {})

    game.board.roads = []
    occupied_edges = set()
    for saved_road in board_data.get("roads", []):
        owner = _restore_ref(saved_road.get("owner"), players, label="街道所有者", allow_none=False)
        node1 = _restore_ref(saved_road.get("node1"), nodes, label="街道端点", allow_none=False)
        node2 = _restore_ref(saved_road.get("node2"), nodes, label="街道端点", allow_none=False)
        edge_key = tuple(sorted((nodes.index(node1), nodes.index(node2))))
        if edge_key in occupied_edges or not game.board.has_edge(node1, node2):
            raise SaveGameError("街道配置が不正です。")
        occupied_edges.add(edge_key)
        game.board.roads.append(Road(owner, node1, node2))

    for saved_building in board_data.get("buildings", []):
        node = _restore_ref(saved_building.get("node"), nodes, label="建物位置", allow_none=False)
        owner = _restore_ref(saved_building.get("owner"), players, label="建物所有者", allow_none=False)
        if node.building is not None:
            raise SaveGameError("同じ交差点に複数の建物があります。")
        try:
            building_type = BuildingType(saved_building.get("type"))
        except ValueError as exc:
            raise SaveGameError("建物種類が不正です。") from exc
        node.building = Building(owner, building_type)

    game.board.robber_tile = _restore_ref(
        board_data.get("robber_tile"),
        tiles,
        label="盗賊位置",
        allow_none=False,
    )

    game.bank.resources = _resource_map_from_json(data.get("bank"), label="銀行")
    deck = data.get("development_deck")
    if not isinstance(deck, list):
        raise SaveGameError("発展カード山札が不正です。")
    try:
        game.development_deck = [DevelopmentCardType[name] for name in deck]
    except (KeyError, TypeError) as exc:
        raise SaveGameError("発展カード山札に不明なカードがあります。") from exc

    game.phase = phase_data.get("name")
    if game.phase not in ("initial", "main", "finished"):
        raise SaveGameError("ゲームフェーズが不正です。")
    game.turn_order = _restore_refs(phase_data.get("turn_order"), players, label="手番順")
    if sorted(game.turn_order, key=id) != sorted(players, key=id):
        raise SaveGameError("手番順に重複または欠落があります。")
    game.current_player_index = phase_data.get("current_player_index")
    if not isinstance(game.current_player_index, int) or not 0 <= game.current_player_index < len(players):
        raise SaveGameError("現在プレイヤー位置が不正です。")
    game.dice_rolled = bool(phase_data.get("dice_rolled", False))
    last_dice_pair = phase_data.get("last_dice_pair")
    if last_dice_pair is None:
        game.last_dice_pair = None
    elif (
        isinstance(last_dice_pair, list)
        and len(last_dice_pair) == 2
        and all(
            isinstance(value, int)
            and not isinstance(value, bool)
            and 1 <= value <= 6
            for value in last_dice_pair
        )
    ):
        game.last_dice_pair = tuple(last_dice_pair)
    else:
        raise SaveGameError("直近のダイス結果が不正です。")
    game.action_mode = phase_data.get("action_mode")
    if game.action_mode not in (None, "road", "settlement", "city"):
        raise SaveGameError("建設選択状態が不正です。")
    game.development_card_used_this_turn = bool(
        phase_data.get("development_card_used_this_turn", False)
    )
    game.special_phase = phase_data.get("special_phase")
    if game.special_phase not in SUPPORTED_SPECIAL_PHASES:
        raise SaveGameError("特殊フェーズが不正です。")
    game.winner = _restore_ref(phase_data.get("winner"), players, label="勝者")
    game.longest_road_owner = _restore_ref(
        phase_data.get("longest_road_owner"),
        players,
        label="最長交易路",
    )
    game.longest_road_length = int(phase_data.get("longest_road_length", 0))
    game.largest_army_owner = _restore_ref(
        phase_data.get("largest_army_owner"),
        players,
        label="最大騎士力",
    )
    game.largest_army_size = int(phase_data.get("largest_army_size", 0))

    game.initial_dice_phase = bool(initial_data.get("dice_phase", False))
    game.initial_dice_results = dict(initial_data.get("dice_results", {}))
    game.initial_dice_histories = {
        str(name): list(values)
        for name, values in initial_data.get("dice_histories", {}).items()
    }
    game.initial_dice_contenders = _restore_refs(
        initial_data.get("dice_contenders", []),
        players,
        label="初期ダイス対象",
    )
    game.initial_placement_order = _restore_refs(
        initial_data.get("placement_order", []),
        players,
        label="初期配置順",
    )
    game.initial_placement_counts = {
        str(name): int(value)
        for name, value in initial_data.get("placement_counts", {}).items()
    }
    game.initial_round = int(initial_data.get("round", 1))
    game.initial_player_index = int(initial_data.get("player_index", 0))
    game.waiting_for_road = bool(initial_data.get("waiting_for_road", False))
    game.last_settlement_node = _restore_ref(
        initial_data.get("last_settlement_node"),
        nodes,
        label="初期開拓地",
    )

    game.discard_queue = _restore_refs(
        special_data.get("discard_queue", []),
        players,
        label="捨て札順",
    )
    game.discard_player = _restore_ref(
        special_data.get("discard_player"),
        players,
        label="捨て札プレイヤー",
    )
    game.discard_remaining = int(special_data.get("discard_remaining", 0))
    game.robber_tile_candidates = _restore_refs(
        special_data.get("robber_tile_candidates", []),
        tiles,
        label="盗賊候補",
    )
    game.robber_target_players = _restore_refs(
        special_data.get("robber_target_players", []),
        players,
        label="略奪対象",
    )
    game.resource_selection_remaining = int(
        special_data.get("resource_selection_remaining", 0)
    )
    game.free_roads_remaining = int(special_data.get("free_roads_remaining", 0))
    bank_trade_resource = special_data.get("bank_trade_give_resource")
    try:
        game.bank_trade_give_resource = (
            ResourceType[bank_trade_resource]
            if bank_trade_resource is not None
            else None
        )
    except KeyError as exc:
        raise SaveGameError("銀行交易の資源種類が不正です。") from exc
    game.handoff_player = _restore_ref(
        special_data.get("handoff_player"),
        players,
        label="画面交代",
    )
    game.handoff_return_phase = special_data.get("handoff_return_phase")
    game.handoff_context = str(special_data.get("handoff_context", ""))

    game.domestic_trade_partner = _restore_ref(
        trade_data.get("partner"),
        players,
        label="交渉相手",
    )
    game.domestic_trade_give = _resource_map_from_json(
        trade_data.get("give", {}),
        label="交渉提示",
    )
    game.domestic_trade_receive = _resource_map_from_json(
        trade_data.get("receive", {}),
        label="交渉要求",
    )
    game.domestic_trade_edit_side = trade_data.get("edit_side", "give")
    if game.domestic_trade_edit_side not in ("give", "receive"):
        raise SaveGameError("交渉編集方向が不正です。")
    game.domestic_trade_editor = _restore_ref(
        trade_data.get("editor"),
        players,
        label="交渉編集者",
    )
    game.domestic_trade_is_counter = bool(trade_data.get("is_counter", False))
    game.domestic_trade_is_broadcast = bool(
        trade_data.get("is_broadcast", False)
    )
    game.domestic_trade_broadcast_responders = _restore_refs(
        trade_data.get("broadcast_responders", []),
        players,
        label="交易募集の回答順",
    )
    game.domestic_trade_broadcast_index = trade_data.get(
        "broadcast_index",
        -1,
    )
    if not isinstance(game.domestic_trade_broadcast_index, int):
        raise SaveGameError("交易募集の回答位置が不正です。")
    game.domestic_trade_broadcast_give = _resource_map_from_json(
        trade_data.get("broadcast_give", {}),
        label="交易募集の提示",
    )
    game.domestic_trade_broadcast_receive = _resource_map_from_json(
        trade_data.get("broadcast_receive", {}),
        label="交易募集の要求",
    )
    game.domestic_trade_broadcast_viewer = _restore_ref(
        trade_data.get("broadcast_viewer"),
        players,
        label="募集画面の閲覧者",
    )

    log_messages = history_data.get("log_messages", [])
    if not isinstance(log_messages, list) or not all(isinstance(item, str) for item in log_messages):
        raise SaveGameError("イベント履歴が不正です。")
    game.log_messages = list(log_messages)
    latest_event = history_data.get("latest_event", {})
    if not isinstance(latest_event, dict):
        raise SaveGameError("直前イベントが不正です。")
    game.latest_event = dict(latest_event)
    if "color" in game.latest_event:
        game.latest_event["color"] = tuple(game.latest_event["color"])
    game.turn_summary_entries = list(history_data.get("turn_summary_entries", []))
    public_gain_history = history_data.get("public_gain_history", {})
    if not isinstance(public_gain_history, dict):
        raise SaveGameError("公開獲得履歴が不正です。")
    game.public_gain_history = public_gain_history
    game.last_resource_distribution = {
        str(name): _resource_map_from_json(bundle, label="直前資源配布")
        for name, bundle in history_data.get("last_resource_distribution", {}).items()
    }
    game.match_metrics = restored_match_metrics
    game.match_result = None
    game.result_display_layout = None
    game.result_selected_event_index = 0

    game.ai_action_delay_ms = int(ai_data.get("action_delay_ms", game.ai_action_delay_ms))
    if game.ai_action_delay_ms < 0:
        raise SaveGameError("AI待機時間が不正です。")
    game.ai_speed_index = int(ai_data.get("speed_index", game.ai_speed_index))
    if not 0 <= game.ai_speed_index < len(AI_SPEED_OPTIONS):
        raise SaveGameError("AI速度設定が不正です。")
    game.ai_paused = bool(ai_data.get("paused", False))
    ai_status = ai_data.get("status", {})
    game.ai_status = dict(ai_status) if isinstance(ai_status, dict) else {}
    game.ai_domestic_trade_attempted = bool(
        ai_data.get("domestic_trade_attempted", False)
    )

    game.show_help_panel = bool(ui_data.get("show_help_panel", False))
    game.show_log_panel = bool(ui_data.get("show_log_panel", False))
    game.log_scroll_offset = max(0, int(ui_data.get("log_scroll_offset", 0)))
    game.log_scroll_offset = min(
        game.log_scroll_offset,
        max(0, len(game.log_messages) - 1),
    )
    game.seed_input_active = False
    game.feedback.clear()
    game.reset_pending_dice_state()
    game.buttons = game.build_buttons()

    _validate_restored_game(game)
    if runtime_side_effects:
        game.schedule_ai_action()


def _validate_restored_game(game):
    _validate_restored_domestic_trade(game)
    if sum(player.is_ai for player in game.players) != game.ai_player_count:
        raise SaveGameError("AIプレイヤー数と席設定が一致しません。")
    if game.phase == "initial":
        active_order = (
            game.initial_dice_contenders
            if game.initial_dice_phase
            else game.initial_placement_order
        )
        if not active_order or not 0 <= game.initial_player_index < len(active_order):
            raise SaveGameError("初期フェーズの進行位置が不正です。")
    if game.phase == "finished" and game.winner is None:
        raise SaveGameError("終了済みゲームに勝者が設定されていません。")

    for resource_type in RESOURCE_TYPES:
        total = game.bank.available(resource_type) + sum(
            player.resources[resource_type]
            for player in game.players
        )
        if total != BANK_RESOURCE_COUNT:
            raise SaveGameError(
                f"{resource_type.name} の総数が {BANK_RESOURCE_COUNT} 枚ではありません。"
            )

    for player in game.players:
        road_count = sum(road.owner is player for road in game.board.roads)
        settlement_count = sum(
            node.building is not None
            and node.building.owner is player
            and node.building.building_type == BuildingType.SETTLEMENT
            for node in game.board.nodes
        )
        city_count = sum(
            node.building is not None
            and node.building.owner is player
            and node.building.building_type == BuildingType.CITY
            for node in game.board.nodes
        )
        if player.roads_remaining + road_count != 15:
            raise SaveGameError(f"{player.name} の街道コマ数が不正です。")
        if player.settlements_remaining + settlement_count != 5:
            raise SaveGameError(f"{player.name} の開拓地コマ数が不正です。")
        if player.cities_remaining + city_count != 4:
            raise SaveGameError(f"{player.name} の都市コマ数が不正です。")


def _validate_restored_domestic_trade(game):
    """Reject inconsistent solicitation state before it can bypass hot-seat privacy."""

    responders = game.domestic_trade_broadcast_responders
    responder_index = game.domestic_trade_broadcast_index
    base_give = game.domestic_trade_broadcast_give
    base_receive = game.domestic_trade_broadcast_receive
    viewer = game.domestic_trade_broadcast_viewer

    if not game.domestic_trade_is_broadcast:
        if (
            responders
            or responder_index != -1
            or any(base_give.values())
            or any(base_receive.values())
            or viewer is not None
        ):
            raise SaveGameError("交易募集の状態が不正です。")
        return

    active_player = game.get_current_player()
    expected_responders = game.get_domestic_trade_eligible_partners(active_player)
    if (
        game.phase != "main"
        or not game.dice_rolled
        or not responders
        or responders != expected_responders
        or isinstance(responder_index, bool)
        or not isinstance(responder_index, int)
        or not -1 <= responder_index < len(responders)
    ):
        raise SaveGameError("交易募集の回答順が不正です。")

    partner = game.domestic_trade_partner
    editor = game.domestic_trade_editor
    is_counter = game.domestic_trade_is_counter
    special_phase = game.special_phase

    if responder_index == -1:
        if (
            special_phase != "domestic_trade_edit"
            or partner is not None
            or is_counter
            or editor is not active_player
            or viewer is not active_player
            or any(base_give.values())
            or any(base_receive.values())
        ):
            raise SaveGameError("交易募集の編集状態が不正です。")
        return

    if partner is not responders[responder_index]:
        raise SaveGameError("交易募集の現在回答者が不正です。")
    if (
        sum(base_give.values()) <= 0
        or sum(base_receive.values()) <= 0
        or any(
            base_give[resource_type] > 0
            and base_receive[resource_type] > 0
            for resource_type in RESOURCE_TYPES
        )
        or not game.player_can_pay_bundle(active_player, base_give)
    ):
        raise SaveGameError("交易募集の基本条件が不正です。")

    if not is_counter and (
        game.domestic_trade_give != base_give
        or game.domestic_trade_receive != base_receive
    ):
        raise SaveGameError("交易募集の提示条件が不正です。")

    previous_human_viewers = [active_player] + [
        candidate
        for candidate in responders[:responder_index]
        if not candidate.is_ai
    ]
    if special_phase == "domestic_trade_handoff":
        valid = (
            not partner.is_ai
            and not is_counter
            and editor is active_player
            and viewer in previous_human_viewers
        )
    elif special_phase == "domestic_trade_response":
        valid = (
            not partner.is_ai
            and not is_counter
            and editor is active_player
            and viewer is partner
        )
    elif special_phase == "domestic_trade_edit":
        valid = (
            not partner.is_ai
            and is_counter
            and editor is partner
            and viewer is partner
        )
    elif special_phase == "domestic_trade_counter_handoff":
        valid = (
            not partner.is_ai
            and is_counter
            and editor is partner
            and viewer is partner
        )
    elif special_phase == "domestic_trade_counter_response":
        expected_editor = active_player if partner.is_ai else partner
        valid = (
            is_counter
            and editor is expected_editor
            and viewer is active_player
        )
    elif special_phase == "player_handoff":
        valid = (
            partner.is_ai
            and is_counter
            and editor is active_player
            and game.handoff_player is active_player
            and game.handoff_return_phase == "domestic_trade_counter_response"
            and viewer in previous_human_viewers[1:]
        )
    else:
        valid = False

    if not valid:
        raise SaveGameError("交易募集の画面交代状態が不正です。")

    if is_counter and not game.player_can_pay_bundle(
        partner,
        game.domestic_trade_receive,
    ):
        raise SaveGameError("交易募集の条件変更が回答者の手札を超えています。")


def save_game(game, path=DEFAULT_SAVE_PATH):
    if game.has_active_dice_animation():
        raise SaveGameError("ダイス演出中は保存できません。演出終了後にもう一度お試しください。")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_suffix(target.suffix + ".tmp")
    data = serialize_game(game)
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    except (OSError, TypeError) as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise SaveGameError(f"保存に失敗しました: {exc}") from exc
    return target


def load_game(game, path=DEFAULT_SAVE_PATH):
    target = Path(path)
    if not target.exists():
        raise SaveGameError("セーブデータがありません。先にF5で保存してください。")
    backup = serialize_game(game)
    try:
        with target.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        restore_game(game, data)
    except (OSError, json.JSONDecodeError, SaveGameError, TypeError, ValueError) as exc:
        try:
            restore_game(game, backup)
        except Exception:
            pass
        if isinstance(exc, SaveGameError):
            raise
        raise SaveGameError(f"読み込みに失敗しました: {exc}") from exc
    return target
