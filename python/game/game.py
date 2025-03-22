import pygame
import random
import math
from game.constants import SCREEN_WIDTH, SCREEN_HEIGHT, COLORS, WINDOW_TITLE
from game.game_board import GameBoard
from game.player import Player
from game.building import Building
from game.dice import roll_dice
from game.resources import ResourceType
from game.road import Road
from game.log_display import draw_log, draw_resource_counts, draw_current_turn

# 簡易的な建物クラス（必要に応じて別ファイルに分割）
class Building:
    def __init__(self, owner: Player):
        self.owner = owner

def roll_dice():
    return random.randint(1, 6) + random.randint(1, 6)

class CatanGame:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption(WINDOW_TITLE)
        self.clock = pygame.time.Clock()
        self.board = GameBoard()
        self.running = True

        # 例として2人のプレイヤーを作成
        self.players = [
            Player("Player1", COLORS["RED"]),
            Player("Player2", COLORS["BLUE"])
        ]
        # 初期配置フェーズ用の状態管理
        self.phase = "initial"  # "initial": 初期配置フェーズ, "main": 通常フェーズ
        self.initial_dice_phase = True     # 各プレイヤーの初期ダイスをまだ管理中かどうか
        self.initial_dice_results = {}     # {プレイヤー名: ダイスの目} を記録
        self.initial_placement_order = []  # ダイスの目で決まった配置順（第1ラウンド用）
        self.initial_round = 1             # 1または2：初期配置のラウンド番号
        self.initial_player_index = 0      # 現在の初期配置対象プレイヤーのインデックス
        self.waiting_for_road = False      # 開拓地配置後、次は街道配置待ち
        self.last_settlement_node = None   # 直前に配置した開拓地のノード

        # 通常フェーズの状態管理
        self.current_player_index = 0      # 通常フェーズ用のプレイヤー順
        self.dice_rolled = False           # 現在のターンでダイスを振ったかどうか

        # ログ用リストの初期化
        self.log_messages = []
        self.add_log("ゲーム開始: 初期配置フェーズです。")
        self.add_log("各プレイヤーはスペースキーでダイスを振って、配置順を決定してください。")

    def add_log(self, message):
        self.log_messages.append(message)
        print(message)  # コンソール出力（任意）

    def clear_log(self):
        self.log_messages = []

    def handle_initial_placement(self, pos):
        mx, my = pos
        if not self.waiting_for_road:
            # --- 開拓地の配置 ---
            closest_node = None
            min_dist = float('inf')
            for node in self.board.nodes:
                dist = math.hypot(node.x - mx, node.y - my)
                if dist < min_dist:
                    min_dist = dist
                    closest_node = node
            if closest_node and min_dist < 20:
                if closest_node.building is None:
                    current_player = self.initial_placement_order[self.initial_player_index]
                    closest_node.building = Building(current_player)
                    self.add_log(f"{current_player.name} が ({closest_node.x:.1f}, {closest_node.y:.1f}) に開拓地を配置 (Round {self.initial_round})")
                    self.last_settlement_node = closest_node
                    self.waiting_for_road = True  # 次は街道配置待ち
                else:
                    self.add_log("そのノードには既に建物が存在します。")
        else:
            # --- 街道の配置 ---
            adjacent_nodes = self.get_adjacent_nodes(self.last_settlement_node)
            candidate_node = None
            min_dist = float('inf')
            for node in adjacent_nodes:
                dist = math.hypot(node.x - mx, node.y - my)
                if dist < min_dist:
                    min_dist = dist
                    candidate_node = node
            if candidate_node and min_dist < 20:
                current_player = self.initial_placement_order[self.initial_player_index]
                new_road = Road(current_player, self.last_settlement_node, candidate_node)
                self.board.roads.append(new_road)
                self.add_log(f"{current_player.name} が ({self.last_settlement_node.x:.1f}, {self.last_settlement_node.y:.1f}) から"
                             f" ({candidate_node.x:.1f}, {candidate_node.y:.1f}) に街道を配置 (Round {self.initial_round})")
                self.waiting_for_road = False
                self.last_settlement_node = None
                self.initial_player_index += 1
                if self.initial_player_index >= len(self.initial_placement_order):
                    if self.initial_round == 1:
                        self.initial_round = 2
                        self.initial_placement_order.reverse()
                        self.initial_player_index = 0
                        self.add_log("初期配置フェーズ 第2ラウンド開始（逆順）")
                        self.clear_log()
                    else:
                        self.add_log("初期配置フェーズ完了")
                        self.phase = "main"
                        self.clear_log()
            else:
                self.add_log("有効な隣接ノードが選択されませんでした。")

    def get_adjacent_nodes(self, node):
        adjacent = set()
        for tile in node.tiles:
            for n in tile.corners:
                if n != node:
                    adjacent.add(n)
        return list(adjacent)

    def place_building(self, mx, my):
        closest_node = None
        min_dist = float('inf')
        for node in self.board.nodes:
            dist = math.hypot(node.x - mx, node.y - my)
            if dist < min_dist:
                min_dist = dist
                closest_node = node
        if closest_node and min_dist < 20:
            if closest_node.building is None:
                current_player = self.players[self.current_player_index]
                closest_node.building = Building(current_player)
                self.add_log(f"{current_player.name} がノード({closest_node.x:.1f}, {closest_node.y:.1f})に開拓地を建設しました。")
                self.current_player_index = (self.current_player_index + 1) % len(self.players)
                # 次の手番に移るので、ダイス未振り状態に戻す
                self.dice_rolled = False
            else:
                self.add_log("そこには既に建物があります。")
        else:
            self.add_log("有効なノードが見つかりませんでした。")

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False

            # 初期配置フェーズの処理
            elif self.phase == "initial":
                if self.initial_dice_phase:
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                        current_player = self.players[self.initial_player_index]
                        dice_roll = roll_dice()
                        self.initial_dice_results[current_player.name] = dice_roll
                        self.add_log(f"{current_player.name} の初期ダイスの目: {dice_roll}")
                        self.initial_player_index += 1
                        if self.initial_player_index >= len(self.players):
                            self.initial_placement_order = sorted(
                                self.players,
                                key=lambda p: self.initial_dice_results[p.name],
                                reverse=True
                            )
                            self.clear_log()
                            self.add_log("初期配置順（第1ラウンド）:")
                            for i, p in enumerate(self.initial_placement_order):
                                self.add_log(f"{i+1}: {p.name} (ダイス: {self.initial_dice_results[p.name]})")
                            self.initial_dice_phase = False
                            self.initial_player_index = 0
                            self.add_log("初期ダイスが完了しました。")
                            self.add_log("マウスクリックまたはスペースキーで建物・街道の配置を行ってください。")
                else:
                    if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1) or \
                       (event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE):
                        self.handle_initial_placement(pygame.mouse.get_pos())

            # 通常フェーズの処理
            else:
                if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                    if not self.dice_rolled:
                        dice_roll = roll_dice()
                        self.clear_log()
                        self.add_log(f"ダイスの目: {dice_roll}")
                        if dice_roll == 7:
                            self.move_robber()
                        else:
                            self.distribute_resources(dice_roll)
                        self.dice_rolled = True
                        current_player = self.players[self.current_player_index]
                        self.add_log(f"現在の手番: {current_player.name}（建物を配置してください）")
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if self.dice_rolled:
                        mx, my = pygame.mouse.get_pos()
                        self.place_building(mx, my)
                    else:
                        self.add_log("ダイスを振ってから建物を配置してください。")

    def move_robber(self):
        target_tile = random.choice(self.board.tiles)
        self.board.move_robber_to(target_tile)
        self.add_log(f"盗賊を ({target_tile.x}, {target_tile.y}) に移動しました。")

    def distribute_resources(self, dice_roll):
        tiles = self.board.get_tiles_with_number(dice_roll)
        for tile in tiles:
            if tile == self.board.robber_tile:
                self.add_log(f"盗賊がいるタイル({tile.resource_type})は資源を生産しません。")
                continue
            for node in tile.corners:
                if node.building is not None:
                    owner = node.building.owner
                    owner.add_resource(tile.resource_type)
                    self.add_log(f"{owner.name} が {tile.resource_type.name} を獲得しました。")
        for p in self.players:
            self.add_log(str(p))

    def update(self):
        pass

    def render(self):
        self.screen.fill(COLORS["BLUE"])
        self.board.draw(self.screen)
        draw_log(self.screen, self.log_messages)
        draw_resource_counts(self.screen, self.players)
        if self.phase == "main":
            draw_current_turn(self.screen, self.players, self.current_player_index)
        pygame.display.flip()

    def run(self):
        while self.running:
            self.handle_events()
            self.update()
            self.render()
            self.clock.tick(60)
        pygame.quit()

if __name__ == "__main__":
    game = CatanGame()
    game.run()
