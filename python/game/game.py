import math
import random

import pygame

from game.audio import GameAudio
from game.building import Building, BuildingType
from game.constants import COLORS, SCREEN_HEIGHT, SCREEN_WIDTH, WINDOW_TITLE
from game.dice_animation import DiceAnimationOverlay
from game.development_cards import (
    DEVELOPMENT_CARD_LABELS,
    DevelopmentCardType,
    create_development_deck,
)
from game.dice import roll_dice
from game.game_board import GameBoard
from game.log_display import draw_log, draw_resource_counts
from game.player import Player
from game.resources import BUILD_COSTS, ResourceType
from game.road import Road
from game.ui import RESOURCE_LABELS, UIButton, draw_side_panel


class CatanGame:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption(WINDOW_TITLE)
        self.clock = pygame.time.Clock()
        self.board = GameBoard()
        self.running = True
        self.audio = GameAudio()
        self.dice_overlay = DiceAnimationOverlay()
        self.audio.start_bgm()

        self.player_palette = [
            ("Player1", COLORS["RED"]),
            ("Player2", COLORS["BLUE"]),
            ("Player3", COLORS["ORANGE"]),
            ("Player4", COLORS["CYAN"]),
        ]
        self.players = []
        self.turn_order = []
        self.buttons = []

        self.phase = "initial"  # "initial", "main", "finished"
        self.initial_dice_phase = True
        self.initial_dice_results = {}
        self.initial_dice_histories = {}
        self.initial_dice_contenders = []
        self.initial_dice_pending_groups = []
        self.initial_placement_order = []
        self.initial_placement_counts = {}
        self.initial_round = 1
        self.initial_player_index = 0
        self.waiting_for_road = False
        self.last_settlement_node = None

        self.current_player_index = 0
        self.dice_rolled = False
        self.action_mode = None
        self.development_card_used_this_turn = False
        self.development_deck = create_development_deck()
        self.special_phase = None
        self.discard_queue = []
        self.discard_player = None
        self.discard_remaining = 0
        self.robber_tile_candidates = []
        self.robber_target_players = []
        self.resource_selection_remaining = 0
        self.free_roads_remaining = 0
        self.bank_trade_give_resource = None
        self.winner = None
        self.longest_road_owner = None
        self.longest_road_length = 0
        self.largest_army_owner = None
        self.largest_army_size = 0

        self.log_messages = []
        self.pending_dice_context = None
        self.pending_dice_roll = None
        self.pending_dice_player_name = ""
        self.configure_players(2, reset_logs=False)
        self.add_log("ゲーム開始: 初期配置フェーズです。")
        self.add_log("プレイヤー数は 2/3/4 キーまたは右のボタンで変更できます。")
        self.add_log("各プレイヤーはスペースキーでダイスを振って、配置順を決定してください。")

    def add_log(self, message):
        self.log_messages.append(message)
        self.log_messages = self.log_messages[-200:]
        print(message)

    def play_sound(self, sound_name):
        self.audio.play(sound_name)

    def clear_log(self):
        self.log_messages = []

    def configure_players(self, player_count, reset_logs=True):
        self.players = [
            Player(name, color)
            for name, color in self.player_palette[:player_count]
        ]
        self.turn_order = self.players.copy()
        self.initial_dice_phase = True
        self.initial_dice_results = {}
        self.initial_dice_histories = {player.name: [] for player in self.players}
        self.initial_dice_contenders = self.players.copy()
        self.initial_dice_pending_groups = []
        self.initial_placement_order = []
        self.initial_placement_counts = {player.name: 0 for player in self.players}
        self.initial_round = 1
        self.initial_player_index = 0
        self.waiting_for_road = False
        self.last_settlement_node = None
        self.current_player_index = 0
        self.dice_rolled = False
        self.action_mode = None
        self.development_card_used_this_turn = False
        self.special_phase = None
        self.discard_queue = []
        self.discard_player = None
        self.discard_remaining = 0
        self.robber_tile_candidates = []
        self.robber_target_players = []
        self.resource_selection_remaining = 0
        self.free_roads_remaining = 0
        self.bank_trade_give_resource = None
        self.winner = None
        self.longest_road_owner = None
        self.longest_road_length = 0
        self.largest_army_owner = None
        self.largest_army_size = 0
        self.pending_dice_context = None
        self.pending_dice_roll = None
        self.pending_dice_player_name = ""
        self.dice_overlay.state = "idle"
        self.phase = "initial"
        self.development_deck = create_development_deck()
        self.buttons = self.build_buttons()
        if reset_logs:
            self.clear_log()
            self.add_log(f"{player_count} 人プレイに設定しました。")
            self.add_log("各プレイヤーはスペースキーでダイスを振って、配置順を決定してください。")

    def get_current_player(self):
        if not self.turn_order:
            return None
        return self.turn_order[self.current_player_index]

    def get_player_victory_points(self, player):
        points = 0
        for node in self.board.nodes:
            if node.building is not None and node.building.owner == player:
                points += node.building.victory_points
        points += player.victory_point_cards
        if self.longest_road_owner == player:
            points += 2
        if self.largest_army_owner == player:
            points += 2
        return points

    def get_points_by_player(self):
        return {player.name: self.get_player_victory_points(player) for player in self.players}

    def get_discard_key_map(self):
        return {
            pygame.K_1: ResourceType.WOOD,
            pygame.K_KP1: ResourceType.WOOD,
            pygame.K_2: ResourceType.SHEEP,
            pygame.K_KP2: ResourceType.SHEEP,
            pygame.K_3: ResourceType.WHEAT,
            pygame.K_KP3: ResourceType.WHEAT,
            pygame.K_4: ResourceType.BRICK,
            pygame.K_KP4: ResourceType.BRICK,
            pygame.K_5: ResourceType.ORE,
            pygame.K_KP5: ResourceType.ORE,
        }

    def get_development_card_counts(self):
        return {
            player.name: {
                "knight": player.development_cards[DevelopmentCardType.KNIGHT],
                "road_building": player.development_cards[DevelopmentCardType.ROAD_BUILDING],
                "year_of_plenty": player.development_cards[DevelopmentCardType.YEAR_OF_PLENTY],
                "monopoly": player.development_cards[DevelopmentCardType.MONOPOLY],
                "victory_point": player.victory_point_cards,
                "new_cards": sum(player.new_development_cards.values()),
                "played_knights": player.played_knights,
            }
            for player in self.players
        }

    def get_current_player_development_summary(self):
        player = self.get_current_player()
        if player is None:
            return ""
        parts = [
            f"K:{player.development_cards[DevelopmentCardType.KNIGHT]}",
            f"B:{player.development_cards[DevelopmentCardType.ROAD_BUILDING]}",
            f"Y:{player.development_cards[DevelopmentCardType.YEAR_OF_PLENTY]}",
            f"M:{player.development_cards[DevelopmentCardType.MONOPOLY]}",
            f"VP:{player.victory_point_cards}",
        ]
        if sum(player.new_development_cards.values()) > 0:
            parts.append(f"新規:{sum(player.new_development_cards.values())}")
        return " ".join(parts)

    def get_point_breakdown(self, player):
        settlement_points = 0
        city_points = 0
        for node in self.board.nodes:
            if node.building is None or node.building.owner != player:
                continue
            if node.building.building_type == BuildingType.CITY:
                city_points += 2
            else:
                settlement_points += 1
        return {
            "settlement": settlement_points,
            "city": city_points,
            "longest_road": 2 if self.longest_road_owner == player else 0,
            "largest_army": 2 if self.largest_army_owner == player else 0,
            "vp_card": player.victory_point_cards,
        }

    def get_all_point_breakdowns(self):
        return {player.name: self.get_point_breakdown(player) for player in self.players}

    def get_missing_resources(self, player, cost):
        missing = []
        for resource_type, required in cost.items():
            shortage = required - player.resources.get(resource_type, 0)
            if shortage > 0:
                missing.append(f"{RESOURCE_LABELS[resource_type]}{shortage}")
        return missing

    def get_build_preview(self, label, player, cost, supply_available=True, unavailable_reason="在庫なし"):
        if not supply_available:
            return {"label": label, "available": False, "detail": unavailable_reason}

        missing = self.get_missing_resources(player, cost)
        if missing:
            return {"label": label, "available": False, "detail": f"不足: {' '.join(missing)}"}

        return {"label": label, "available": True, "detail": "建設可"}

    def get_build_affordability(self, player):
        if player is None:
            return []
        return [
            self.get_build_preview("街道", player, BUILD_COSTS["road"], player.roads_remaining > 0),
            self.get_build_preview("開拓地", player, BUILD_COSTS["settlement"], player.settlements_remaining > 0),
            self.get_build_preview("都市", player, BUILD_COSTS["city"], player.cities_remaining > 0),
            self.get_build_preview("発展", player, BUILD_COSTS["development"], bool(self.development_deck), "山札なし"),
        ]

    def get_trade_rates(self, player):
        if player is None:
            return {}
        return self.board.get_player_trade_rates(player)

    def get_initial_dice_history_text(self, player):
        return " -> ".join(str(value) for value in self.initial_dice_histories.get(player.name, []))

    def has_active_dice_animation(self):
        return self.pending_dice_context is not None and self.dice_overlay.is_active

    def start_dice_animation(self, context, dice_roll, player_name, title):
        self.pending_dice_context = context
        self.pending_dice_roll = dice_roll
        self.pending_dice_player_name = player_name
        self.play_sound("dice")
        subtitle = f"{player_name} が振っています" if player_name else ""
        self.dice_overlay.start(dice_roll, title, subtitle)

    def update_dice_animation(self):
        if self.pending_dice_context is None:
            return
        if not self.dice_overlay.update(pygame.time.get_ticks()):
            return

        context = self.pending_dice_context
        dice_roll = self.pending_dice_roll
        self.pending_dice_context = None
        self.pending_dice_roll = None
        self.pending_dice_player_name = ""

        if context == "initial":
            self.resolve_initial_key_roll(dice_roll)
        elif context == "main":
            self.resolve_main_dice_roll(dice_roll)

    def build_buttons(self):
        buttons = []
        base_x = 866
        base_y = 86
        button_width = 146
        button_height = 38
        gap_x = 12
        gap_y = 10

        def add(action, label, row, col, enabled=True, selected=False):
            rect = pygame.Rect(
                base_x + col * (button_width + gap_x),
                base_y + row * (button_height + gap_y),
                button_width,
                button_height,
            )
            buttons.append(UIButton(action, label, rect, enabled=enabled, selected=selected))

        def add_custom(action, label, x, y, width, height, enabled=True, selected=False):
            rect = pygame.Rect(x, y, width, height)
            buttons.append(UIButton(action, label, rect, enabled=enabled, selected=selected))

        current_player = self.get_current_player()
        if self.phase == "initial" and self.initial_dice_phase:
            add("player_count_2", "2人", 0, 0, selected=len(self.players) == 2)
            add("player_count_3", "3人", 0, 1, selected=len(self.players) == 3)
            add("player_count_4", "4人", 1, 0, selected=len(self.players) == 4)
            add("initial_roll", "初期ダイス", 1, 1, enabled=bool(self.players))
            return buttons
        if self.phase != "main" or self.winner is not None:
            return buttons

        add("roll_dice", "ダイス", 0, 0, enabled=self.phase == "main" and not self.dice_rolled and self.winner is None)
        add("end_turn", "手番終了", 0, 1, enabled=self.phase == "main" and self.dice_rolled and self.special_phase is None)
        add("mode_road", "街道", 1, 0, enabled=self.phase == "main" and self.dice_rolled and self.special_phase is None, selected=self.action_mode == "road")
        add("mode_settlement", "開拓地", 1, 1, enabled=self.phase == "main" and self.dice_rolled and self.special_phase is None, selected=self.action_mode == "settlement")
        add("mode_city", "都市", 2, 0, enabled=self.phase == "main" and self.dice_rolled and self.special_phase is None, selected=self.action_mode == "city")
        add("buy_dev", "発展購入", 2, 1, enabled=self.phase == "main" and self.dice_rolled and self.special_phase is None)
        add("bank_trade", "銀行交易", 3, 0, enabled=self.phase == "main" and self.dice_rolled and self.special_phase is None)
        add("cancel_action", "取消", 3, 1, enabled=self.phase == "main" and (self.action_mode is not None or self.special_phase in ("bank_trade_give", "bank_trade_receive")))
        add("use_knight", "騎士", 4, 0, enabled=current_player is not None and self.phase == "main")
        add("use_road_building", "街道建設", 4, 1, enabled=current_player is not None and self.phase == "main")
        add("use_year_of_plenty", "収穫", 5, 0, enabled=current_player is not None and self.phase == "main")
        add("use_monopoly", "独占", 5, 1, enabled=current_player is not None and self.phase == "main")

        if self.special_phase in ("bank_trade_give", "bank_trade_receive"):
            selector_x = base_x
            selector_y = base_y + 6 * (button_height + gap_y)
            selector_width = 94
            selector_height = 34
            selector_gap_x = 8
            selector_gap_y = 8
            for index, resource_type in enumerate(
                [ResourceType.WOOD, ResourceType.SHEEP, ResourceType.WHEAT, ResourceType.BRICK, ResourceType.ORE]
            ):
                col = index % 3
                row = index // 3
                selected = self.bank_trade_give_resource == resource_type
                label = RESOURCE_LABELS[resource_type]
                add_custom(
                    f"trade_resource_{resource_type.name}",
                    label,
                    selector_x + col * (selector_width + selector_gap_x),
                    selector_y + row * (selector_height + selector_gap_y),
                    selector_width,
                    selector_height,
                    enabled=True,
                    selected=selected and self.special_phase == "bank_trade_receive",
                )

        return buttons

    def find_clicked_button(self, pos):
        for button in self.buttons:
            if button.rect.collidepoint(pos) and button.enabled:
                return button
        return None

    def start_bank_trade(self):
        if self.phase != "main" or self.winner is not None:
            return
        if not self.dice_rolled:
            self.add_log("交易はダイスを振った後に行ってください。")
            return
        if self.special_phase is not None:
            self.add_log("進行中の特殊処理を先に完了してください。")
            return
        self.action_mode = None
        self.special_phase = "bank_trade_give"
        self.bank_trade_give_resource = None
        rates = self.get_trade_rates(self.get_current_player())
        rate_text = ", ".join(f"{RESOURCE_LABELS[r]}{rate}:1" for r, rate in rates.items())
        self.add_log(f"銀行交易: 先に支払う資源を選んでください。 {rate_text}")

    def select_bank_trade_resource(self, resource_type):
        player = self.get_current_player()
        if player is None:
            return
        trade_rates = self.get_trade_rates(player)

        if self.special_phase == "bank_trade_give":
            required = trade_rates[resource_type]
            if player.resources[resource_type] < required:
                self.add_log(f"{RESOURCE_LABELS[resource_type]} が不足しています。必要枚数は {required} 枚です。")
                return
            self.bank_trade_give_resource = resource_type
            self.special_phase = "bank_trade_receive"
            self.add_log(
                f"{RESOURCE_LABELS[resource_type]} を {required} 枚支払います。受け取りたい資源を選んでください。"
            )
            return

        if self.special_phase == "bank_trade_receive":
            if resource_type == self.bank_trade_give_resource:
                self.add_log("同じ資源には交換できません。")
                return
            give_resource = self.bank_trade_give_resource
            required = trade_rates[give_resource]
            player.remove_resource(give_resource, required)
            player.add_resource(resource_type)
            self.play_sound("card")
            self.add_log(
                f"{player.name} が銀行交易: {RESOURCE_LABELS[give_resource]} {required} 枚を"
                f" {RESOURCE_LABELS[resource_type]} 1枚に交換しました。"
            )
            self.special_phase = None
            self.bank_trade_give_resource = None

    def cancel_selection(self):
        if self.special_phase in ("bank_trade_give", "bank_trade_receive"):
            self.special_phase = None
            self.bank_trade_give_resource = None
            self.add_log("銀行交易をキャンセルしました。")
            return
        self.action_mode = None
        self.add_log("行動選択をキャンセルしました。")

    def handle_button_action(self, action):
        if action.startswith("player_count_"):
            if self.initial_dice_phase:
                self.configure_players(int(action.rsplit("_", 1)[1]))
            return
        if action == "initial_roll":
            self.handle_initial_key_roll()
            return
        if action == "roll_dice":
            self.handle_roll_dice()
            return
        if action == "end_turn":
            self.finish_current_turn()
            return
        if action == "mode_road":
            self.set_action_mode("road")
            return
        if action == "mode_settlement":
            self.set_action_mode("settlement")
            return
        if action == "mode_city":
            self.set_action_mode("city")
            return
        if action == "buy_dev":
            self.buy_development_card()
            return
        if action == "bank_trade":
            self.start_bank_trade()
            return
        if action == "cancel_action":
            self.cancel_selection()
            return
        if action == "use_knight":
            self.use_knight_card()
            return
        if action == "use_road_building":
            self.use_road_building_card()
            return
        if action == "use_year_of_plenty":
            self.use_year_of_plenty_card()
            return
        if action == "use_monopoly":
            self.use_monopoly_card()
            return
        if action.startswith("trade_resource_"):
            resource_name = action.removeprefix("trade_resource_")
            self.select_bank_trade_resource(ResourceType[resource_name])

    def find_closest_node(self, mx, my, candidates=None):
        nodes = candidates if candidates is not None else self.board.nodes
        closest_node = None
        min_dist = float("inf")
        for node in nodes:
            dist = math.hypot(node.x - mx, node.y - my)
            if dist < min_dist:
                min_dist = dist
                closest_node = node
        return closest_node, min_dist

    def find_closest_edge(self, mx, my, candidates=None):
        edges = candidates if candidates is not None else self.board.edges
        closest_edge = None
        min_dist = float("inf")
        for node1, node2 in edges:
            midpoint_x = (node1.x + node2.x) / 2
            midpoint_y = (node1.y + node2.y) / 2
            dist = math.hypot(midpoint_x - mx, midpoint_y - my)
            if dist < min_dist:
                min_dist = dist
                closest_edge = (node1, node2)
        return closest_edge, min_dist

    def get_adjacent_nodes(self, node):
        adjacent = set()
        for tile in node.tiles:
            if node not in tile.corners:
                continue
            node_index = tile.corners.index(node)
            adjacent.add(tile.corners[(node_index - 1) % len(tile.corners)])
            adjacent.add(tile.corners[(node_index + 1) % len(tile.corners)])
        return list(adjacent)

    def find_closest_tile(self, mx, my, candidates=None):
        tiles = candidates if candidates is not None else self.board.tiles
        closest_tile = None
        min_dist = float("inf")
        for tile in tiles:
            dist = math.hypot(tile.x - mx, tile.y - my)
            if dist < min_dist:
                min_dist = dist
                closest_tile = tile
        return closest_tile, min_dist

    def road_exists_between(self, node1, node2):
        return any({road.node1, road.node2} == {node1, node2} for road in self.board.roads)

    def is_spacing_rule_satisfied(self, node):
        if node.building is not None:
            return False
        return all(adjacent_node.building is None for adjacent_node in self.get_adjacent_nodes(node))

    def player_has_road_touching_node(self, player, node):
        return any(road.owner == player and road.touches(node) for road in self.board.roads)

    def can_place_initial_settlement(self, node):
        if not self.is_spacing_rule_satisfied(node):
            if node.building is not None:
                return False, "そのノードには既に建物が存在します。"
            return False, "間隔ルールにより、隣接する交差点の近くには建てられません。"
        return True, ""

    def can_place_main_settlement(self, player, node):
        if not self.is_spacing_rule_satisfied(node):
            if node.building is not None:
                return False, "そのノードには既に建物が存在します。"
            return False, "間隔ルールにより、隣接する交差点の近くには建てられません。"
        if not self.player_has_road_touching_node(player, node):
            return False, "開拓地は自分の街道が接続している交差点にのみ建設できます。"
        return True, ""

    def can_use_node_for_road_connection(self, player, node):
        if node.building is not None:
            return node.building.owner == player
        return self.player_has_road_touching_node(player, node)

    def can_place_road(self, player, node1, node2):
        if not self.board.has_edge(node1, node2):
            return False, "その場所には街道を敷設できません。"
        if self.road_exists_between(node1, node2):
            return False, "その辺には既に街道があります。"
        if self.can_use_node_for_road_connection(player, node1):
            return True, ""
        if self.can_use_node_for_road_connection(player, node2):
            return True, ""
        return False, "街道は自分の開拓地・都市、または既存の街道につなげて建設してください。"

    def can_upgrade_to_city(self, player, node):
        if node.building is None:
            return False, "そこには建物がありません。"
        if node.building.owner != player:
            return False, "自分の開拓地のみ都市にアップグレードできます。"
        if node.building.building_type != BuildingType.SETTLEMENT:
            return False, "その建物はすでに都市です。"
        return True, ""

    def start_robber_phase(self, with_discard=True):
        self.action_mode = None
        self.robber_target_players = []
        self.resource_selection_remaining = 0
        self.free_roads_remaining = 0

        players_to_discard = []
        if with_discard:
            players_to_discard = [
                player for player in self.turn_order if player.total_resource_count() > 7
            ]
        self.discard_queue = players_to_discard
        self.discard_player = None
        self.discard_remaining = 0

        if self.discard_queue:
            self.special_phase = "discard"
            self.advance_discard_phase()
            return

        self.begin_robber_move_phase()

    def advance_discard_phase(self):
        if not self.discard_queue:
            self.begin_robber_move_phase()
            return

        self.discard_player = self.discard_queue.pop(0)
        self.discard_remaining = self.discard_player.total_resource_count() // 2
        if self.discard_remaining <= 0:
            self.advance_discard_phase()
            return

        self.add_log(
            f"{self.discard_player.name} は {self.discard_remaining} 枚捨ててください。"
            " 1:木 2:羊 3:麦 4:土 5:鉄"
        )

    def discard_resource(self, resource_type):
        if self.special_phase != "discard" or self.discard_player is None:
            return

        if self.discard_player.resources.get(resource_type, 0) <= 0:
            self.add_log(f"{self.discard_player.name} は {resource_type.name} を持っていません。")
            return

        self.discard_player.remove_resource(resource_type)
        self.discard_remaining -= 1
        self.add_log(
            f"{self.discard_player.name} が {resource_type.name} を捨てました。"
            f" 残り {self.discard_remaining} 枚"
        )

        if self.discard_remaining == 0:
            self.add_log(f"{self.discard_player.name} の捨て札が完了しました。")
            self.discard_player = None
            self.advance_discard_phase()

    def begin_robber_move_phase(self):
        self.special_phase = "move_robber"
        self.discard_player = None
        self.discard_remaining = 0
        self.robber_tile_candidates = [
            tile for tile in self.board.tiles if tile != self.board.robber_tile
        ]
        self.add_log("盗賊を移動してください。現在いる地形には置けません。")

    def get_robber_target_players(self, tile):
        targets = []
        for node in tile.corners:
            if node.building is None:
                continue
            owner = node.building.owner
            if owner == self.get_current_player():
                continue
            if owner.total_resource_count() <= 0:
                continue
            if owner not in targets:
                targets.append(owner)
        return targets

    def relocate_robber(self, tile):
        self.board.move_robber_to(tile)
        self.play_sound("robber")
        self.add_log(f"盗賊を ({tile.x}, {tile.y}) に移動しました。")
        target_players = self.get_robber_target_players(tile)

        if not target_players:
            self.complete_robber_phase()
            return

        if len(target_players) == 1:
            self.steal_random_resource(target_players[0])
            self.complete_robber_phase()
            return

        self.special_phase = "steal"
        self.robber_target_players = target_players
        target_names = ", ".join(player.name for player in target_players)
        self.add_log(f"略奪対象を選んでください: {target_names}")

    def handle_robber_move_click(self, pos):
        mx, my = pos
        tile, min_dist = self.find_closest_tile(mx, my, self.robber_tile_candidates)
        if tile is None or min_dist >= 45:
            self.add_log("盗賊を移動したい地形の中央付近をクリックしてください。")
            return
        self.relocate_robber(tile)

    def handle_robber_target_click(self, pos):
        mx, my = pos
        candidate_nodes = [
            node
            for node in self.board.robber_tile.corners
            if node.building is not None and node.building.owner in self.robber_target_players
        ]
        closest_node, min_dist = self.find_closest_node(mx, my, candidate_nodes)
        if closest_node is None or min_dist >= 20:
            self.add_log("略奪したい相手の建物をクリックしてください。")
            return

        self.steal_random_resource(closest_node.building.owner)
        self.complete_robber_phase()

    def steal_random_resource(self, victim):
        current_player = self.get_current_player()
        available_resources = [
            resource
            for resource, amount in victim.resources.items()
            for _ in range(amount)
        ]
        if not available_resources:
            self.add_log(f"{victim.name} は資源を持っていないため、略奪できません。")
            return

        stolen_resource = random.choice(available_resources)
        victim.remove_resource(stolen_resource)
        current_player.add_resource(stolen_resource)
        self.play_sound("robber")
        self.add_log(f"{current_player.name} が {victim.name} から {stolen_resource.name} を1枚盗みました。")

    def complete_robber_phase(self):
        self.special_phase = None
        self.discard_queue = []
        self.discard_player = None
        self.discard_remaining = 0
        self.robber_tile_candidates = []
        self.robber_target_players = []
        self.add_log("盗賊フェイズ完了。引き続き手番を続けてください。")

    def has_legal_road_placement(self, player):
        for node1, node2 in self.board.edges:
            can_place, _ = self.can_place_road(player, node1, node2)
            if can_place:
                return True
        return False

    def buy_development_card(self):
        if self.phase != "main" or self.winner is not None:
            return
        if self.special_phase is not None:
            self.add_log("先に進行中の特殊処理を完了してください。")
            return
        if not self.dice_rolled:
            self.add_log("発展カードの購入はダイスを振った後に行ってください。")
            return
        if not self.development_deck:
            self.add_log("発展カードの山札がありません。")
            return

        current_player = self.get_current_player()
        if not current_player.can_afford(BUILD_COSTS["development"]):
            self.add_log("資源不足: 発展カードには鉄・羊・麦が1枚ずつ必要です。")
            return

        current_player.spend_resources(BUILD_COSTS["development"])
        card_type = self.development_deck.pop()
        current_player.add_development_card(card_type, available=False)
        self.play_sound("card")
        self.add_log(
            f"{current_player.name} が発展カードを購入: {DEVELOPMENT_CARD_LABELS[card_type]}"
            f"（残り {len(self.development_deck)} 枚）"
        )

        if card_type == DevelopmentCardType.VICTORY_POINT:
            self.add_log("勝利点カードは即座に得点に反映されます。")
        else:
            self.add_log("購入した発展カードは次の自分の手番から使用できます。")
        self.check_for_winner(current_player)

    def can_use_development_card(self, player, card_type):
        if self.phase != "main" or self.winner is not None:
            return False, "いまは発展カードを使えません。"
        if self.special_phase is not None:
            return False, "進行中の特殊処理が終わってから使ってください。"
        if self.development_card_used_this_turn:
            return False, "発展カードは1ターンに1枚までです。"
        if not player.has_playable_development_card(card_type):
            return False, f"{DEVELOPMENT_CARD_LABELS[card_type]} を持っていません。"
        return True, ""

    def use_knight_card(self):
        player = self.get_current_player()
        can_use, message = self.can_use_development_card(player, DevelopmentCardType.KNIGHT)
        if not can_use:
            self.add_log(message)
            return

        player.use_development_card(DevelopmentCardType.KNIGHT)
        player.played_knights += 1
        self.development_card_used_this_turn = True
        self.play_sound("card")
        self.add_log(f"{player.name} が騎士カードを使用しました。")
        self.update_largest_army()
        self.check_for_winner(player)
        if self.phase != "finished":
            self.start_robber_phase(with_discard=False)

    def use_year_of_plenty_card(self):
        player = self.get_current_player()
        can_use, message = self.can_use_development_card(player, DevelopmentCardType.YEAR_OF_PLENTY)
        if not can_use:
            self.add_log(message)
            return

        player.use_development_card(DevelopmentCardType.YEAR_OF_PLENTY)
        self.development_card_used_this_turn = True
        self.special_phase = "year_of_plenty"
        self.resource_selection_remaining = 2
        self.play_sound("card")
        self.add_log(f"{player.name} が収穫カードを使用しました。 2枚選んでください。1:木 2:羊 3:麦 4:土 5:鉄")

    def use_monopoly_card(self):
        player = self.get_current_player()
        can_use, message = self.can_use_development_card(player, DevelopmentCardType.MONOPOLY)
        if not can_use:
            self.add_log(message)
            return

        player.use_development_card(DevelopmentCardType.MONOPOLY)
        self.development_card_used_this_turn = True
        self.special_phase = "monopoly"
        self.play_sound("card")
        self.add_log(f"{player.name} が独占カードを使用しました。 資源を選んでください。1:木 2:羊 3:麦 4:土 5:鉄")

    def use_road_building_card(self):
        player = self.get_current_player()
        can_use, message = self.can_use_development_card(player, DevelopmentCardType.ROAD_BUILDING)
        if not can_use:
            self.add_log(message)
            return

        player.use_development_card(DevelopmentCardType.ROAD_BUILDING)
        self.development_card_used_this_turn = True
        self.free_roads_remaining = min(2, player.roads_remaining)
        if self.free_roads_remaining <= 0:
            self.add_log(f"{player.name} は街道コマがないため、街道建設カードの効果を使えません。")
            return
        if not self.has_legal_road_placement(player):
            self.free_roads_remaining = 0
            self.add_log(f"{player.name} は配置可能な街道がないため、街道建設カードの効果を使えません。")
            return

        self.special_phase = "road_building"
        self.play_sound("card")
        self.add_log(
            f"{player.name} が街道建設カードを使用しました。"
            f" 無料の街道を {self.free_roads_remaining} 本配置できます。"
        )

    def complete_road_building_phase(self):
        self.special_phase = None
        self.free_roads_remaining = 0
        self.add_log("街道建設カードの処理が完了しました。")
        self.check_for_winner(self.get_current_player())

    def handle_resource_selection(self, resource_type):
        player = self.get_current_player()
        if self.special_phase == "year_of_plenty":
            player.add_resource(resource_type)
            self.resource_selection_remaining -= 1
            self.add_log(
                f"{player.name} が {resource_type.name} を獲得しました。"
                f" 残り {self.resource_selection_remaining} 枚選択"
            )
            if self.resource_selection_remaining == 0:
                self.special_phase = None
                self.add_log("収穫カードの処理が完了しました。")
            return

        if self.special_phase == "monopoly":
            total_taken = 0
            for other_player in self.players:
                if other_player == player:
                    continue
                amount = other_player.resources.get(resource_type, 0)
                if amount <= 0:
                    continue
                other_player.resources[resource_type] = 0
                player.add_resource(resource_type, amount)
                total_taken += amount
            self.special_phase = None
            self.add_log(
                f"{player.name} が独占カードで {resource_type.name} を {total_taken} 枚獲得しました。"
            )

    def handle_free_road_build_click(self, pos):
        current_player = self.get_current_player()
        if self.free_roads_remaining <= 0:
            self.complete_road_building_phase()
            return

        mx, my = pos
        closest_edge, min_dist = self.find_closest_edge(mx, my)
        if closest_edge is None or min_dist >= 18:
            self.add_log("無料の街道を置きたい辺の中央付近をクリックしてください。")
            return

        node1, node2 = closest_edge
        can_place, message = self.can_place_road(current_player, node1, node2)
        if not can_place:
            self.add_log(message)
            return

        current_player.roads_remaining -= 1
        self.board.roads.append(Road(current_player, node1, node2))
        self.free_roads_remaining -= 1
        self.play_sound("road")
        self.add_log(
            f"{current_player.name} が無料の街道を配置しました。"
            f" 残り {self.free_roads_remaining} 本"
        )
        self.update_longest_road()
        self.check_for_winner(current_player)
        if self.phase == "finished":
            return

        if self.free_roads_remaining <= 0 or not self.has_legal_road_placement(current_player):
            self.complete_road_building_phase()

    def update_largest_army(self):
        previous_owner = self.largest_army_owner
        max_knights = max((player.played_knights for player in self.players), default=0)

        if max_knights < 3:
            self.largest_army_owner = None
            self.largest_army_size = 0
            return

        candidates = [player for player in self.players if player.played_knights == max_knights]
        if self.largest_army_owner in candidates:
            self.largest_army_size = max_knights
            return

        if len(candidates) == 1:
            self.largest_army_owner = candidates[0]
            self.largest_army_size = max_knights
            if previous_owner != candidates[0]:
                self.add_log(f"最大騎士力: {candidates[0].name} が獲得 ({max_knights} 枚)")
            return

        self.largest_army_owner = None
        self.largest_army_size = max_knights

    def grant_initial_resources(self, player, settlement_node):
        gained_resources = []
        for tile in settlement_node.tiles:
            if tile.resource_type == ResourceType.DESERT:
                continue
            player.add_resource(tile.resource_type)
            gained_resources.append(tile.resource_type.name)

        if gained_resources:
            self.add_log(f"{player.name} は初期資源を獲得: {', '.join(gained_resources)}")
        else:
            self.add_log(f"{player.name} の2回目の開拓地は砂漠に隣接しています。")

    def handle_initial_key_roll(self):
        if not self.initial_dice_phase or not self.players or self.has_active_dice_animation():
            return
        current_player = self.initial_dice_contenders[self.initial_player_index]
        dice_roll = roll_dice()
        self.start_dice_animation("initial", dice_roll, current_player.name, "初期ダイス")

    def resolve_initial_key_roll(self, dice_roll):
        current_player = self.initial_dice_contenders[self.initial_player_index]
        self.initial_dice_results[current_player.name] = dice_roll
        self.initial_dice_histories[current_player.name].append(dice_roll)
        self.add_log(f"{current_player.name} の初期ダイスの目: {dice_roll}")
        self.initial_player_index += 1

        if self.initial_player_index < len(self.initial_dice_contenders):
            return

        self.resolve_initial_dice_round()

    def resolve_initial_dice_round(self):
        score_groups = {}
        for player in self.initial_dice_contenders:
            score = self.initial_dice_results[player.name]
            score_groups.setdefault(score, []).append(player)

        tied_groups = [
            score_groups[score]
            for score in sorted(score_groups.keys(), reverse=True)
            if len(score_groups[score]) > 1
        ]
        remaining_groups = self.initial_dice_pending_groups[1:] if self.initial_dice_pending_groups else []
        self.initial_dice_pending_groups = tied_groups + remaining_groups

        if self.initial_dice_pending_groups:
            tied_group = self.initial_dice_pending_groups[0]
            tied_score = self.initial_dice_histories[tied_group[0].name][-1]
            tied_names = ", ".join(player.name for player in tied_group)
            self.add_log(f"同点: {tied_names} が {tied_score} で並びました。再ロールします。")
            self.initial_dice_contenders = tied_group
            self.initial_dice_results = {}
            self.initial_player_index = 0
            self.add_log(f"次は {self.initial_dice_contenders[0].name} の再ロールです。")
            return

        self.finalize_initial_dice_order()

    def finalize_initial_dice_order(self):
        self.turn_order = sorted(
            self.players,
            key=lambda player: tuple(self.initial_dice_histories[player.name]),
            reverse=True,
        )
        self.initial_placement_order = self.turn_order.copy()
        self.clear_log()
        self.add_log("初期配置順（第1ラウンド）:")
        for index, player in enumerate(self.initial_placement_order, start=1):
            self.add_log(
                f"{index}: {player.name} (ダイス: {self.get_initial_dice_history_text(player)})"
            )
        self.initial_dice_phase = False
        self.initial_player_index = 0
        self.initial_dice_contenders = []
        self.initial_dice_pending_groups = []
        self.add_log("初期ダイスが完了しました。")
        self.add_log("マウスクリックまたはスペースキーで建物・街道の配置を行ってください。")

    def handle_roll_dice(self):
        if self.phase != "main" or self.winner is not None or self.has_active_dice_animation():
            return
        if self.dice_rolled:
            self.add_log("このターンはすでにダイスを振っています。")
            return
        dice_roll = roll_dice()
        current_player = self.get_current_player()
        player_name = current_player.name if current_player is not None else ""
        self.start_dice_animation("main", dice_roll, player_name, "ダイスロール")

    def resolve_main_dice_roll(self, dice_roll):
        self.clear_log()
        self.add_log(f"ダイスの目: {dice_roll}")
        if dice_roll == 7:
            self.start_robber_phase()
        else:
            self.distribute_resources(dice_roll)
            self.add_log("D=発展カード購入, T=銀行交易, R/S/C=建設, Enter=手番終了 で行動してください。")
        self.dice_rolled = True

    def advance_initial_phase(self, current_player):
        self.add_log(f"{current_player.name} の初期配置が完了しました。")

        if all(count >= 2 for count in self.initial_placement_counts.values()):
            self.start_main_phase()
            return

        if self.initial_round == 1 and all(
            count >= 1 for count in self.initial_placement_counts.values()
        ):
            self.initial_round = 2
            self.initial_placement_order = list(reversed(self.turn_order))
            self.initial_player_index = 0
            self.clear_log()
            self.add_log("初期配置フェーズ 第2ラウンド開始（逆順）")
            self.add_log(f"次は {self.initial_placement_order[0].name} の配置です。")
            self.add_log("2回目の開拓地では隣接するタイルの資源を獲得します。")
            return

        self.initial_player_index += 1
        next_player = self.initial_placement_order[self.initial_player_index]
        self.add_log(f"次は {next_player.name} の配置です。")

    def start_main_phase(self):
        self.phase = "main"
        self.current_player_index = 0
        self.dice_rolled = False
        self.waiting_for_road = False
        self.last_settlement_node = None
        self.action_mode = None
        self.development_card_used_this_turn = False
        self.special_phase = None
        self.discard_queue = []
        self.discard_player = None
        self.discard_remaining = 0
        self.robber_tile_candidates = []
        self.robber_target_players = []
        self.resource_selection_remaining = 0
        self.free_roads_remaining = 0
        self.pending_dice_context = None
        self.pending_dice_roll = None
        self.pending_dice_player_name = ""
        self.dice_overlay.state = "idle"
        self.clear_log()
        first_player = self.get_current_player()
        self.add_log("初期配置フェーズ完了。通常フェーズを開始します。")
        self.add_log(f"最初の手番: {first_player.name}")
        self.add_log("スペースキーでダイスを振ってください。発展カードは K/B/Y/M、銀行交易は T です。")

    def handle_initial_placement(self, pos):
        mx, my = pos
        current_player = self.initial_placement_order[self.initial_player_index]

        if not self.waiting_for_road:
            closest_node, min_dist = self.find_closest_node(mx, my)
            if not closest_node or min_dist >= 20:
                self.add_log("有効なノードが見つかりませんでした。")
                return

            can_place, message = self.can_place_initial_settlement(closest_node)
            if not can_place:
                self.add_log(message)
                return
            if current_player.settlements_remaining <= 0:
                self.add_log("開拓地コマが残っていません。")
                return

            current_player.settlements_remaining -= 1
            closest_node.building = Building(current_player)
            self.play_sound("build")
            self.add_log(
                f"{current_player.name} が ({closest_node.x:.1f}, {closest_node.y:.1f}) に"
                f"開拓地を配置 (Round {self.initial_round})"
            )
            if self.initial_placement_counts[current_player.name] == 1:
                self.grant_initial_resources(current_player, closest_node)

            self.last_settlement_node = closest_node
            self.waiting_for_road = True
            self.add_log("続けて隣接する辺に街道を配置してください。")
            return

        adjacent_nodes = self.get_adjacent_nodes(self.last_settlement_node)
        candidate_node, min_dist = self.find_closest_node(mx, my, adjacent_nodes)
        if not candidate_node or min_dist >= 20:
            self.add_log("有効な隣接ノードが選択されませんでした。")
            return
        if self.road_exists_between(self.last_settlement_node, candidate_node):
            self.add_log("その辺には既に街道があります。")
            return
        if current_player.roads_remaining <= 0:
            self.add_log("街道コマが残っていません。")
            return

        current_player.roads_remaining -= 1
        new_road = Road(current_player, self.last_settlement_node, candidate_node)
        self.board.roads.append(new_road)
        self.play_sound("road")
        self.add_log(
            f"{current_player.name} が ({self.last_settlement_node.x:.1f}, {self.last_settlement_node.y:.1f}) から"
            f" ({candidate_node.x:.1f}, {candidate_node.y:.1f}) に街道を配置 (Round {self.initial_round})"
        )
        self.initial_placement_counts[current_player.name] += 1
        self.waiting_for_road = False
        self.last_settlement_node = None
        self.update_longest_road()
        self.advance_initial_phase(current_player)

    def set_action_mode(self, action_mode):
        if self.phase != "main" or self.winner is not None:
            return
        if self.special_phase is not None:
            self.add_log("進行中の特殊処理を完了してください。")
            return
        if not self.dice_rolled:
            self.add_log("先にスペースキーでダイスを振ってください。")
            return
        self.action_mode = action_mode
        action_messages = {
            "road": "街道モード: 六角形の辺の中央付近をクリックしてください。",
            "settlement": "開拓地モード: 建設したい交差点をクリックしてください。",
            "city": "都市モード: 自分の開拓地をクリックしてください。",
        }
        self.add_log(action_messages[action_mode])

    def finish_current_turn(self):
        if self.winner is not None:
            return
        if self.special_phase is not None:
            self.add_log("盗賊の処理が終わるまで手番を終了できません。")
            return
        if not self.dice_rolled:
            self.add_log("まだダイスを振っていません。")
            return

        current_player = self.get_current_player()
        current_player.activate_new_development_cards()
        self.action_mode = None
        self.development_card_used_this_turn = False
        self.dice_rolled = False
        self.resource_selection_remaining = 0
        self.free_roads_remaining = 0
        self.current_player_index = (self.current_player_index + 1) % len(self.turn_order)
        self.clear_log()
        self.add_log(f"{self.get_current_player().name} の手番です。")
        self.add_log("スペースキーでダイスを振ってください。発展カードは K/B/Y/M、銀行交易は T です。")

    def build_settlement(self, pos):
        current_player = self.get_current_player()
        if current_player.settlements_remaining <= 0:
            self.add_log("開拓地コマが残っていません。")
            return
        if not current_player.can_afford(BUILD_COSTS["settlement"]):
            self.add_log("資源不足: 開拓地には木・土・羊・麦が1枚ずつ必要です。")
            return

        mx, my = pos
        closest_node, min_dist = self.find_closest_node(mx, my)
        if not closest_node or min_dist >= 20:
            self.add_log("有効なノードが見つかりませんでした。")
            return

        can_place, message = self.can_place_main_settlement(current_player, closest_node)
        if not can_place:
            self.add_log(message)
            return

        current_player.spend_resources(BUILD_COSTS["settlement"])
        current_player.settlements_remaining -= 1
        closest_node.building = Building(current_player)
        self.action_mode = None
        self.play_sound("build")
        self.add_log(f"{current_player.name} が開拓地を建設しました。")
        self.update_longest_road()
        self.check_for_winner(current_player)

    def build_city(self, pos):
        current_player = self.get_current_player()
        if current_player.cities_remaining <= 0:
            self.add_log("都市コマが残っていません。")
            return
        if not current_player.can_afford(BUILD_COSTS["city"]):
            self.add_log("資源不足: 都市には鉄3枚と麦2枚が必要です。")
            return

        mx, my = pos
        closest_node, min_dist = self.find_closest_node(mx, my)
        if not closest_node or min_dist >= 20:
            self.add_log("有効なノードが見つかりませんでした。")
            return

        can_upgrade, message = self.can_upgrade_to_city(current_player, closest_node)
        if not can_upgrade:
            self.add_log(message)
            return

        current_player.spend_resources(BUILD_COSTS["city"])
        current_player.cities_remaining -= 1
        current_player.settlements_remaining += 1
        closest_node.building.upgrade_to_city()
        self.action_mode = None
        self.play_sound("build")
        self.add_log(f"{current_player.name} が都市にアップグレードしました。")
        self.check_for_winner(current_player)

    def build_road(self, pos):
        current_player = self.get_current_player()
        if current_player.roads_remaining <= 0:
            self.add_log("街道コマが残っていません。")
            return
        if not current_player.can_afford(BUILD_COSTS["road"]):
            self.add_log("資源不足: 街道には木1枚と土1枚が必要です。")
            return

        mx, my = pos
        closest_edge, min_dist = self.find_closest_edge(mx, my)
        if closest_edge is None or min_dist >= 18:
            self.add_log("街道を置きたい辺の中央付近をクリックしてください。")
            return

        node1, node2 = closest_edge
        can_place, message = self.can_place_road(current_player, node1, node2)
        if not can_place:
            self.add_log(message)
            return

        current_player.spend_resources(BUILD_COSTS["road"])
        current_player.roads_remaining -= 1
        self.board.roads.append(Road(current_player, node1, node2))
        self.action_mode = None
        self.play_sound("road")
        self.add_log(f"{current_player.name} が街道を建設しました。")
        self.update_longest_road()
        self.check_for_winner(current_player)

    def handle_main_phase_click(self, pos):
        if not self.dice_rolled:
            if self.special_phase != "road_building":
                self.add_log("先にダイスを振ってください。")
                return
        if self.special_phase == "move_robber":
            self.handle_robber_move_click(pos)
            return
        if self.special_phase == "steal":
            self.handle_robber_target_click(pos)
            return
        if self.special_phase == "road_building":
            self.handle_free_road_build_click(pos)
            return
        if self.special_phase is not None:
            self.add_log("先に盗賊の処理を完了してください。")
            return
        if self.action_mode is None:
            self.add_log("R=街道, S=開拓地, C=都市 を押して行動を選んでください。")
            return
        if self.action_mode == "settlement":
            self.build_settlement(pos)
        elif self.action_mode == "city":
            self.build_city(pos)
        elif self.action_mode == "road":
            self.build_road(pos)

    def get_player_longest_road_length(self, player):
        player_roads = [road for road in self.board.roads if road.owner == player]
        if not player_roads:
            return 0

        adjacency = {}
        for road in player_roads:
            adjacency.setdefault(road.node1, []).append(road)
            adjacency.setdefault(road.node2, []).append(road)

        def dfs(node, used_road_ids):
            if node.building is not None and node.building.owner != player:
                return 0

            best = 0
            for road in adjacency.get(node, []):
                road_id = id(road)
                if road_id in used_road_ids:
                    continue
                next_node = road.other_node(node)
                if next_node is None:
                    continue
                best = max(best, 1 + dfs(next_node, used_road_ids | {road_id}))
            return best

        best_length = 0
        for road in player_roads:
            road_id = id(road)
            best_length = max(best_length, 1 + dfs(road.node1, {road_id}))
            best_length = max(best_length, 1 + dfs(road.node2, {road_id}))
        return best_length

    def update_longest_road(self):
        previous_owner = self.longest_road_owner
        lengths = {player: self.get_player_longest_road_length(player) for player in self.players}
        max_length = max(lengths.values(), default=0)

        if max_length < 5:
            self.longest_road_owner = None
            self.longest_road_length = 0
            return

        candidates = [player for player, length in lengths.items() if length == max_length]
        if self.longest_road_owner in candidates:
            self.longest_road_length = max_length
            return

        if len(candidates) == 1:
            self.longest_road_owner = candidates[0]
            self.longest_road_length = max_length
            if previous_owner != candidates[0]:
                self.add_log(f"最長交易路: {candidates[0].name} が獲得 ({max_length} 本)")
            return

        self.longest_road_owner = None
        self.longest_road_length = max_length

    def check_for_winner(self, player):
        if self.phase != "main":
            return
        points = self.get_player_victory_points(player)
        if points >= 10:
            self.winner = player
            self.phase = "finished"
            self.action_mode = None
            self.special_phase = None
            self.play_sound("victory")
            self.clear_log()
            self.add_log(f"{player.name} が {points} 点に到達し、勝利しました。")
            self.add_log("ウィンドウを閉じるまで盤面を表示しています。")

    def handle_events(self):
        self.buttons = self.build_buttons()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
                continue

            if self.phase == "finished":
                continue

            if self.has_active_dice_animation():
                continue

            if self.phase == "initial":
                if self.initial_dice_phase:
                    if event.type == pygame.KEYDOWN and event.key in (pygame.K_2, pygame.K_3, pygame.K_4):
                        self.configure_players(int(event.unicode))
                    elif event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                        self.handle_initial_key_roll()
                    elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        clicked_button = self.find_clicked_button(event.pos)
                        if clicked_button is not None:
                            self.handle_button_action(clicked_button.action)
                else:
                    if (
                        event.type == pygame.MOUSEBUTTONDOWN
                        and event.button == 1
                    ) or (
                        event.type == pygame.KEYDOWN
                        and event.key == pygame.K_SPACE
                    ):
                        self.handle_initial_placement(pygame.mouse.get_pos())
                continue

            if self.special_phase == "discard":
                if event.type == pygame.KEYDOWN:
                    resource_type = self.get_discard_key_map().get(event.key)
                    if resource_type is not None:
                        self.discard_resource(resource_type)
                    else:
                        self.add_log("捨て札は 1:木 2:羊 3:麦 4:土 5:鉄 で選んでください。")
                continue

            if self.special_phase in ("year_of_plenty", "monopoly"):
                if event.type == pygame.KEYDOWN:
                    resource_type = self.get_discard_key_map().get(event.key)
                    if resource_type is not None:
                        self.handle_resource_selection(resource_type)
                    else:
                        self.add_log("資源選択は 1:木 2:羊 3:麦 4:土 5:鉄 で指定してください。")
                continue

            if self.special_phase in ("bank_trade_give", "bank_trade_receive"):
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.cancel_selection()
                        continue
                    resource_type = self.get_discard_key_map().get(event.key)
                    if resource_type is not None:
                        self.select_bank_trade_resource(resource_type)
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    clicked_button = self.find_clicked_button(event.pos)
                    if clicked_button is not None:
                        self.handle_button_action(clicked_button.action)
                continue

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_SPACE:
                    self.handle_roll_dice()
                elif event.key == pygame.K_d:
                    self.buy_development_card()
                elif event.key == pygame.K_t:
                    self.start_bank_trade()
                elif event.key == pygame.K_k:
                    self.use_knight_card()
                elif event.key == pygame.K_b:
                    self.use_road_building_card()
                elif event.key == pygame.K_y:
                    self.use_year_of_plenty_card()
                elif event.key == pygame.K_m:
                    self.use_monopoly_card()
                elif event.key == pygame.K_r:
                    self.set_action_mode("road")
                elif event.key == pygame.K_s:
                    self.set_action_mode("settlement")
                elif event.key == pygame.K_c:
                    self.set_action_mode("city")
                elif event.key == pygame.K_RETURN:
                    self.finish_current_turn()
                elif event.key == pygame.K_ESCAPE:
                    if self.special_phase == "road_building":
                        self.complete_road_building_phase()
                        continue
                    self.cancel_selection()
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                clicked_button = self.find_clicked_button(event.pos)
                if clicked_button is not None:
                    self.handle_button_action(clicked_button.action)
                    continue
                self.handle_main_phase_click(pygame.mouse.get_pos())

    def distribute_resources(self, dice_roll):
        tiles = self.board.get_tiles_with_number(dice_roll)
        for tile in tiles:
            if tile == self.board.robber_tile:
                self.add_log(f"盗賊がいるタイル({tile.resource_type.name})は資源を生産しません。")
                continue
            for node in tile.corners:
                if node.building is not None:
                    owner = node.building.owner
                    owner.add_resource(tile.resource_type, node.building.resource_multiplier)
                    gain = node.building.resource_multiplier
                    self.add_log(f"{owner.name} が {tile.resource_type.name} を {gain} 枚獲得しました。")
        for player in self.players:
            self.add_log(str(player))

    def update(self):
        self.update_dice_animation()
        self.buttons = self.build_buttons()

    def render(self):
        self.screen.fill(COLORS["BACKGROUND"])
        self.board.draw(self.screen)
        draw_log(self.screen, self.log_messages)
        draw_resource_counts(
            self.screen,
            self.players,
            points_by_player=self.get_points_by_player(),
            longest_road_owner=self.longest_road_owner,
            largest_army_owner=self.largest_army_owner,
        )
        panel_title = "操作パネル"
        panel_subtitle = ""
        if self.phase == "initial" and self.initial_dice_phase:
            panel_title = "初期設定"
            panel_subtitle = "2/3/4人を選び、初期ダイスを開始"
        elif self.phase == "initial":
            panel_title = "初期配置"
            current_player = self.initial_placement_order[self.initial_player_index]
            if self.waiting_for_road:
                panel_subtitle = f"{current_player.name}: 開拓地に隣接する辺へ街道を配置"
            else:
                panel_subtitle = f"{current_player.name}: 開拓地を配置 (Round {self.initial_round})"
        elif self.phase in ("main", "finished"):
            current_player = self.get_current_player()
            if current_player is not None:
                panel_subtitle = f"現在の手番: {current_player.name}"
                if self.special_phase == "discard" and self.discard_player is not None:
                    panel_subtitle = f"捨て札中: {self.discard_player.name} 残り {self.discard_remaining}"
                elif self.special_phase == "move_robber":
                    panel_subtitle = f"{current_player.name}: 盗賊の移動先を選択"
                elif self.special_phase == "steal":
                    panel_subtitle = f"{current_player.name}: 略奪対象を選択"
                elif self.special_phase == "bank_trade_give":
                    panel_subtitle = "銀行交易: 支払う資源を選択"
                elif self.special_phase == "bank_trade_receive":
                    panel_subtitle = "銀行交易: 受け取る資源を選択"
                elif self.special_phase == "year_of_plenty":
                    panel_subtitle = "収穫: 1-5 で資源を2枚選択"
                elif self.special_phase == "monopoly":
                    panel_subtitle = "独占: 1-5 で資源種類を指定"
                elif self.special_phase == "road_building":
                    panel_subtitle = f"街道建設: 残り {self.free_roads_remaining} 本"
        draw_side_panel(
            self.screen,
            panel_title,
            panel_subtitle,
            self.get_current_player() if self.phase == "main" else None,
            self.players,
            self.buttons,
            self.get_points_by_player(),
            self.get_all_point_breakdowns(),
            self.get_trade_rates(self.get_current_player()) if self.phase == "main" else {},
            self.get_current_player_development_summary() if self.phase == "main" else "",
            len(self.development_deck),
            self.get_build_affordability(self.get_current_player()) if self.phase == "main" and self.get_current_player() else [],
        )
        if self.dice_overlay.is_active:
            self.dice_overlay.draw(self.screen)
        pygame.display.flip()

    def run(self):
        while self.running:
            self.handle_events()
            self.update()
            self.render()
            self.clock.tick(60)
        self.audio.stop()
        pygame.quit()


if __name__ == "__main__":
    game = CatanGame()
    game.run()
