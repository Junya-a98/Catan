import pygame
import random
import math
from enum import Enum

# === 定数定義 ===
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 600
HEX_RADIUS = 50
WINDOW_TITLE = "カタン風ゲーム"

# 色の定義
COLORS = {
    "BLACK": (0, 0, 0),
    "WHITE": (255, 255, 255),
    "RED": (255, 0, 0),
    "GREEN": (0, 255, 0),
    "BLUE": (0, 0, 255),
    "YELLOW": (255, 255, 0),
    "BROWN": (139, 69, 19),
    "ORANGE": (255, 165, 0),
    "GRAY": (128, 128, 128),
    "WHEAT": (245, 222, 179)
}

# 資源の種類
class ResourceType(Enum):
    WOOD = 1
    SHEEP = 2
    WHEAT = 3
    BRICK = 4
    ORE = 5
    DESERT = 6

# 資源タイプごとの色
RESOURCE_COLORS = {
    ResourceType.WOOD: COLORS["GREEN"],
    ResourceType.SHEEP: COLORS["WHITE"],
    ResourceType.WHEAT: COLORS["YELLOW"],
    ResourceType.BRICK: COLORS["RED"],
    ResourceType.ORE: COLORS["GRAY"],
    ResourceType.DESERT: COLORS["WHEAT"]
}

# --- Playerクラス ---
class Player:
    def __init__(self, name, color):
        self.name = name
        self.color = color  # 建物表示用の色
        # 各資源の所持数
        self.resources = {
            ResourceType.WOOD: 0,
            ResourceType.SHEEP: 0,
            ResourceType.WHEAT: 0,
            ResourceType.BRICK: 0,
            ResourceType.ORE: 0
        }
    
    def add_resource(self, resource_type, amount=1):
        if resource_type in self.resources:
            self.resources[resource_type] += amount
    
    def __str__(self):
        # プレイヤー情報(名前・資源数)の文字列化
        res_str = ", ".join([f"{r.name}:{self.resources[r]}" for r in self.resources])
        return f"Player({self.name}) - {res_str}"

# --- 建物を表す簡易クラス(今回は開拓地のみ) ---
class Building:
    def __init__(self, owner: Player):
        self.owner = owner
        # 都市や道などの拡張が必要ならプロパティ追加

# --- 六角形タイルの頂点(角)を表すクラス ---
class Node:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.building = None  # 建物が置かれていなければNone
        self.tiles = []       # このノードに接しているタイル(HexTile)のリスト

# --- 六角形タイルクラス ---
class HexTile:
    def __init__(self, x, y, resource_type, number=None):
        self.x = x
        self.y = y
        self.resource_type = resource_type
        self.number = number
        self.corners = []  # このタイルを囲むノード(Node)を保持

    def draw(self, screen, robber_tile=None):
        # 六角形の頂点座標を計算
        vertices = []
        for i in range(6):
            angle_deg = 60 * i - 30
            angle_rad = math.pi / 180 * angle_deg
            vertex_x = self.x + HEX_RADIUS * math.cos(angle_rad)
            vertex_y = self.y + HEX_RADIUS * math.sin(angle_rad)
            vertices.append((vertex_x, vertex_y))
        
        # タイル本体の描画
        pygame.draw.polygon(screen, RESOURCE_COLORS[self.resource_type], vertices)
        pygame.draw.polygon(screen, COLORS["BLACK"], vertices, 2)
        
        # 数字トークン(砂漠でない場合のみ)
        if self.number is not None and self.resource_type != ResourceType.DESERT:
            font = pygame.font.SysFont(None, 30)
            text = font.render(str(self.number), True, COLORS["BLACK"])
            text_rect = text.get_rect(center=(self.x, self.y))
            # 白円で背景を作り、その上に数字を描画
            pygame.draw.circle(screen, COLORS["WHITE"], (self.x, self.y), 20)
            pygame.draw.circle(screen, COLORS["BLACK"], (self.x, self.y), 20, 1)
            screen.blit(text, text_rect)

        # 盗賊表示(黒円)
        if robber_tile is self:
            pygame.draw.circle(screen, COLORS["BLACK"], (self.x, self.y), 10)

# --- ボード全体を管理するクラス ---
class GameBoard:
    def __init__(self):
        self.tiles = []
        self.nodes = []  # 全ノードを保持
        self.robber_tile = None
        self.setup_board()

    def setup_board(self):
        # リソースタイプのリストを用意 (砂漠1、その他 3～4枚)
        resources = [ResourceType.DESERT]
        for resource in [res for res in ResourceType if res != ResourceType.DESERT]:
            if resource in (ResourceType.BRICK, ResourceType.ORE):
                resources.extend([resource] * 3)
            else:
                resources.extend([resource] * 4)

        random.shuffle(resources)
        
        # 数字トークン(2~12, 7を除く)
        numbers = [2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12]
        random.shuffle(numbers)
        
        center_x, center_y = SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2

        # 中心タイル
        resource = resources.pop(0)
        number = None if resource == ResourceType.DESERT else numbers.pop(0)
        center_tile = HexTile(center_x, center_y, resource, number)
        self.tiles.append(center_tile)
        if resource == ResourceType.DESERT:
            self.robber_tile = center_tile

        # 1リング目(6枚)
        radius = HEX_RADIUS * 1.75
        for i in range(6):
            angle_deg = 60 * i
            angle_rad = math.pi / 180 * angle_deg
            x = center_x + radius * math.cos(angle_rad)
            y = center_y + radius * math.sin(angle_rad)
            
            resource = resources.pop(0)
            number = None if resource == ResourceType.DESERT else numbers.pop(0)
            tile = HexTile(x, y, resource, number)
            self.tiles.append(tile)
            if resource == ResourceType.DESERT and self.robber_tile is None:
                self.robber_tile = tile

        # 2リング目(12枚)
        for i in range(12):
            angle_deg = 30 * i
            angle_rad = math.pi / 180 * angle_deg
            if angle_deg % 60 == 0:
                ring_radius = HEX_RADIUS * 3.5
            else:
                ring_radius = HEX_RADIUS * 3.0

            x = center_x + ring_radius * math.cos(angle_rad)
            y = center_y + ring_radius * math.sin(angle_rad)
            
            resource = resources.pop(0)
            num = None if resource == ResourceType.DESERT else numbers.pop(0) if numbers else None
            tile = HexTile(x, y, resource, num)
            self.tiles.append(tile)
            if resource == ResourceType.DESERT and self.robber_tile is None:
                self.robber_tile = tile

        # タイルの頂点(Node)を作成・共有する
        self._create_nodes_for_tiles()

    def _create_nodes_for_tiles(self):
        """
        各タイルの6頂点をノードとして生成/共有する。
        """
        def get_hex_corners(cx, cy, radius):
            corners = []
            for i in range(6):
                angle_deg = 60*i - 30
                angle_rad = math.pi / 180 * angle_deg
                corner_x = cx + radius * math.cos(angle_rad)
                corner_y = cy + radius * math.sin(angle_rad)
                corners.append((corner_x, corner_y))
            return corners
        
        for tile in self.tiles:
            corners = get_hex_corners(tile.x, tile.y, HEX_RADIUS)
            tile.corners = []
            for cx, cy in corners:
                # 近い位置のNodeを探す(重複防止用)
                node = self.find_or_create_node(cx, cy)
                # タイルとノードを相互に関連付け
                tile.corners.append(node)
                if tile not in node.tiles:
                    node.tiles.append(tile)

    def find_or_create_node(self, x, y, threshold=10):
        """
        既存のノードと近い座標ならそれを返し、
        なければ新規に作って返す。
        """
        for node in self.nodes:
            dist = math.hypot(node.x - x, node.y - y)
            if dist < threshold:
                return node
        # 新規ノードを作成
        new_node = Node(x, y)
        self.nodes.append(new_node)
        return new_node

    def draw(self, screen):
        # タイルを先に描画
        for tile in self.tiles:
            tile.draw(screen, robber_tile=self.robber_tile)

        # ノード上の建物を描画(開拓地)
        for node in self.nodes:
            if node.building is not None:
                # 建物オーナーの色で小さな円を描画
                color = node.building.owner.color
                pygame.draw.circle(screen, color, (node.x, node.y), 8)
                pygame.draw.circle(screen, COLORS["BLACK"], (node.x, node.y), 8, 1)

    def move_robber_to(self, tile: HexTile):
        self.robber_tile = tile

    def get_tiles_with_number(self, dice_number):
        return [t for t in self.tiles if t.number == dice_number]

# --- ダイスを振る ---
def roll_dice():
    return random.randint(1, 6) + random.randint(1, 6)

# --- メインゲームクラス ---
class CatanGame:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption(WINDOW_TITLE)
        self.clock = pygame.time.Clock()
        self.board = GameBoard()
        self.running = True

        # プレイヤー2人の例
        self.players = [
            Player("Player1", COLORS["RED"]),
            Player("Player2", COLORS["BLUE"])
        ]
        self.current_player_index = 0  # 建物配置の際、どのプレイヤーの番か

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_SPACE:
                    # スペースキーでダイスを振る
                    dice_roll = roll_dice()
                    print(f"ダイスの目: {dice_roll}")
                    if dice_roll == 7:
                        # 7が出た場合は盗賊を移動(ランダム)
                        self.move_robber()
                    else:
                        # 資源を分配
                        self.distribute_resources(dice_roll)

            elif event.type == pygame.MOUSEBUTTONDOWN:
                # 左クリックで建物を配置
                if event.button == 1:  
                    mx, my = pygame.mouse.get_pos()
                    self.place_building(mx, my)

    def move_robber(self):
        target_tile = random.choice(self.board.tiles)
        self.board.move_robber_to(target_tile)
        print(f"盗賊を ({target_tile.x}, {target_tile.y}) に移動しました。")

    def distribute_resources(self, dice_roll):
        """
        ダイス目に合うタイルの資源を配る。
        ただし盗賊がいるタイルは生産しない。
        """
        tiles = self.board.get_tiles_with_number(dice_roll)
        for tile in tiles:
            if tile == self.board.robber_tile:
                print(f"盗賊がいるタイル({tile.resource_type})は資源を生産しません。")
                continue

            # タイルの資源タイプを取得
            resource_type = tile.resource_type
            if resource_type == ResourceType.DESERT:
                continue  # 砂漠なら何もしない

            # タイルの各ノードに建物があるかをチェック
            for node in tile.corners:
                if node.building is not None:
                    owner = node.building.owner
                    owner.add_resource(resource_type, amount=1)
                    print(f"{owner.name} が {resource_type.name} を1つ獲得！")

        # デバッグ: プレイヤーの所持資源を表示
        for p in self.players:
            print(p)

    def place_building(self, mx, my):
        """
        クリック座標に最も近いNodeを探し、そこに建物が無ければ建設する。
        """
        # 最も近いNodeを探す
        closest_node = None
        min_dist = float('inf')
        for node in self.board.nodes:
            dist = math.hypot(node.x - mx, node.y - my)
            if dist < min_dist:
                min_dist = dist
                closest_node = node
        
        # 一定距離内にノードがあれば建設(簡易チェック)
        if closest_node and min_dist < 20:
            if closest_node.building is None:
                current_player = self.players[self.current_player_index]
                closest_node.building = Building(current_player)
                print(f"{current_player.name} がノード({closest_node.x:.1f}, {closest_node.y:.1f})に開拓地を建設しました。")
                # 順番を次のプレイヤーに回す(単純に切り替え)
                self.current_player_index = (self.current_player_index + 1) % len(self.players)
            else:
                print("そこには既に建物があります。")

    def update(self):
        # 毎フレームの更新処理があればここに
        pass

    def render(self):
        self.screen.fill(COLORS["BLUE"])  # 背景色(海の青)
        self.board.draw(self.screen)
        pygame.display.flip()

    def run(self):
        while self.running:
            self.handle_events()
            self.update()
            self.render()
            self.clock.tick(60)
        
        pygame.quit()

# --- 実行 ---
if __name__ == "__main__":
    game = CatanGame()
    game.run()
