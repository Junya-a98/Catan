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

        #通常フェーズの状態管理
        self.current_player_index = 0  # これを追加

        # ログ用リストの初期化
        self.log_messages = []
        self.add_log("ゲーム開始: 初期配置フェーズです。")
        self.add_log("各プレイヤーはスペースキーでダイスを振って、配置順を決定してください。")

    def add_log(self, message):
        self.log_messages.append(message)
        # コンソールにも出力（任意）
        print(message)
    
    #ログの削除
    def clear_log(self):
        self.log_messages = [] 
    
    def draw_log(self):
        # 日本語フォントの読み込み
        font_path = "Noto_Sans_JP/NotoSansJP-VariableFont_wght.ttf"  # フォントファイルのパス
        font_size = 20
        font = pygame.font.Font(font_path, font_size)
        y = 10  # ログ描画の開始 Y 座標
        for message in self.log_messages:
            text_surface = font.render(message, True, (255, 255, 255))
            self.screen.blit(text_surface, (10, y))
            y += 24
    
    def draw_resource_counts(self):
        try:
            font = pygame.font.Font("Noto_Sans_JP/NotoSansJP-VariableFont_wght.ttf", 24)
        except Exception as e:
            print("フォント読み込み失敗:", e)
            font = pygame.font.Font(None, 24)
        
        margin = 10
        # 各プレイヤー分の行の高さ（フォントサイズ + 5ピクセルのスペース）
        line_height = 24 + 5
        # 画面下部から、プレイヤー数分の行を描画するための開始 y 座標
        start_y = self.screen.get_height() - margin - (line_height * len(self.players))
        
        for player in self.players:
            # プレイヤーの資源情報を文字列に整形（例："Player1: WOOD:3, SHEEP:2, ..."）
            resource_str = ", ".join([f"{res.name}:{count}" for res, count in player.resources.items()])
            text = f"{player.name}: {resource_str}"
            text_surface = font.render(text, True, (255, 255, 255))  # 白色で描画
            # 右下に表示するため、x 座標は画面幅から余白とテキスト幅を引いた値
            x = self.screen.get_width() - margin - text_surface.get_width()
            self.screen.blit(text_surface, (x, start_y))
            start_y += line_height

    
    
        
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
                            # 全員がダイスを振ったので、ダイスの目が大きい順に配置順を決定
                            self.initial_placement_order = sorted(
                                self.players,
                                key=lambda p: self.initial_dice_results[p.name],
                                reverse=True
                            )
                            #過去ログの削除
                            self.clear_log() 

                            self.add_log("初期配置順（第1ラウンド）:")
                            for i, p in enumerate(self.initial_placement_order):
                                self.add_log(f"{i+1}: {p.name} (ダイス: {self.initial_dice_results[p.name]})")
                            self.initial_dice_phase = False
                            self.initial_player_index = 0
                            self.add_log("初期ダイスが完了しました。")
                            self.add_log("マウスクリックまたはスペースキーで建物・街道の配置を行ってください。")
                else:
                    # 初期ダイスフェーズが完了しているなら、マウスクリックまたはスペースキーで配置処理を呼び出す
                    if (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1) or \
                       (event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE):
                        self.handle_initial_placement(pygame.mouse.get_pos())

            # 通常フェーズの処理
            else:
                if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                    dice_roll = roll_dice()
                    self.clear_log()
                    self.add_log(f"ダイスの目: {dice_roll}")
                    if dice_roll == 7:
                        self.move_robber()
                    else:
                        self.distribute_resources(dice_roll)
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = pygame.mouse.get_pos()
                    self.place_building(mx, my)

    def place_building(self, mx, my):
        """
        クリックした座標に最も近い Node を探し、建物がなければ配置する。
        """
        closest_node = None
        min_dist = float('inf')
        for node in self.board.nodes:
            dist = math.hypot(node.x - mx, node.y - my)
            if dist < min_dist:
                min_dist = dist
                closest_node = node  

        # 一定距離内にノードがあれば建設（簡易チェック）
        if closest_node and min_dist < 20:
            if closest_node.building is None:
                current_player = self.players[self.current_player_index]
                closest_node.building = Building(current_player)
                self.add_log(f"{current_player.name} がノード({closest_node.x:.1f}, {closest_node.y:.1f})に開拓地を建設しました。")
                self.current_player_index = (self.current_player_index + 1) % len(self.players)
            else:
                self.add_log("そこには既に建物があります。")

    def handle_initial_placement(self, pos):
        """
        初期配置フェーズでのクリック処理
         - 最初のクリックで開拓地（建物）を配置
         - 次のクリックで、直前の開拓地に隣接するノードを選び、街道(Road)を配置
        """
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
                    self.waiting_for_road = True  # 次は街道配置待ちへ
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
                        # 第1ラウンド終了：第2ラウンドは配置順を逆にする
                        self.initial_round = 2
                        self.initial_placement_order.reverse()
                        self.initial_player_index = 0
                        self.add_log("初期配置フェーズ 第2ラウンド開始（逆順）")
                        #過去ログの削除
                        self.clear_log()
                    else:
                        self.add_log("初期配置フェーズ完了")
                        self.phase = "main"
                        
            else:
                self.add_log("有効な隣接ノードが選択されませんでした。")

    def get_adjacent_nodes(self, node):
        """
        指定ノードと隣接するノードを返す
         - 同じタイルに属する他のノードを候補とする
        """
        adjacent = set()
        for tile in node.tiles:
            for n in tile.corners:
                if n != node:
                    adjacent.add(n)
        return list(adjacent)

    def update(self):
        # フレーム毎の更新処理（必要に応じて追加）
        pass

    def render(self):
        self.screen.fill(COLORS["BLUE"])  # 背景色：海の青
        self.board.draw(self.screen)
        self.draw_log()
        self.draw_resource_counts() # 右下に資源情報を描画
        pygame.display.flip()

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

        for player in self.players:
            print(player)

    def run(self):
        while self.running:
            self.handle_events()
            self.update()
            self.render()
            self.clock.tick(60)
        pygame.quit()
