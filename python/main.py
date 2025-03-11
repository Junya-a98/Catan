import pygame
import random
import math
from enum import Enum

# 定数
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

# 資源タイプの色
RESOURCE_COLORS = {
    ResourceType.WOOD: COLORS["GREEN"],
    ResourceType.SHEEP: COLORS["WHITE"],
    ResourceType.WHEAT: COLORS["YELLOW"],
    ResourceType.BRICK: COLORS["RED"],
    ResourceType.ORE: COLORS["GRAY"],
    ResourceType.DESERT: COLORS["WHEAT"]
}

# 六角形タイルクラス
class HexTile:
    def __init__(self, x, y, resource_type, number=None):
        self.x = x
        self.y = y
        self.resource_type = resource_type
        self.number = number
        
    def draw(self, screen):
        # 六角形の頂点を計算
        vertices = []
        for i in range(6):
            angle_deg = 60 * i - 30
            angle_rad = math.pi / 180 * angle_deg
            vertex_x = self.x + HEX_RADIUS * math.cos(angle_rad)
            vertex_y = self.y + HEX_RADIUS * math.sin(angle_rad)
            vertices.append((vertex_x, vertex_y))
        
        # 六角形を描画
        pygame.draw.polygon(screen, RESOURCE_COLORS[self.resource_type], vertices)
        pygame.draw.polygon(screen, COLORS["BLACK"], vertices, 2)
        
        # 数字を描画（砂漠以外）
        if self.number is not None and self.resource_type != ResourceType.DESERT:
            font = pygame.font.SysFont(None, 30)
            text = font.render(str(self.number), True, COLORS["BLACK"])
            text_rect = text.get_rect(center=(self.x, self.y))
            
            # 数字の背景を白い円で描画
            pygame.draw.circle(screen, COLORS["WHITE"], (self.x, self.y), 20)
            pygame.draw.circle(screen, COLORS["BLACK"], (self.x, self.y), 20, 1)
            screen.blit(text, text_rect)

# ゲームボードクラス
class GameBoard:
    def __init__(self):
        self.tiles = []
        self.setup_board()
        
    def setup_board(self):
        # リソースタイプのリスト（砂漠1枚、その他各3枚）
        resources = [ResourceType.DESERT]
        for resource in [res for res in ResourceType if res != ResourceType.DESERT]:
            if resource == ResourceType.BRICK or resource == ResourceType.ORE:
                resources.extend([resource] * 3)
            else:
                resources.extend([resource] * 4)
       
         # デバッグ用にリソースの内容を出力
        print("DEBUG: resources(before shuffle) =", resources)
        # リソースの順番をシャッフル
        random.shuffle(resources)
        
        # 数字トークンのリスト (2-12, 7を除く)
        numbers = [2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12]
        random.shuffle(numbers)
        
        # ボード中央を原点として配置
        center_x, center_y = SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2
        
        # 中心タイルから外側に向かって配置
        # まず中心タイル
        resource = resources.pop(0)
        number = None if resource == ResourceType.DESERT else numbers.pop(0)
        self.tiles.append(HexTile(center_x, center_y, resource, number))
        
        # 中心から外側の最初のリング
        radius = HEX_RADIUS * 1.75
        for i in range(6):
            angle_deg = 60 * i
            angle_rad = math.pi / 180 * angle_deg
            x = center_x + radius * math.cos(angle_rad)
            y = center_y + radius * math.sin(angle_rad)
            
            resource = resources.pop(0)
            number = None if resource == ResourceType.DESERT else numbers.pop(0)
            self.tiles.append(HexTile(x, y, resource, number))

        # 2つ目のリング（オプション）- 本格的なカタンにはもう一つ外側のリングがあります
        for i in range(12):
            angle_deg = 30 * i
            angle_rad = math.pi / 180 * angle_deg
                # 角度が60°の倍数なら半径を変える
            if angle_deg % 60 == 0:
                # たとえば「ちょうど良い」値を別途指定
                radius = HEX_RADIUS * 3.5
            else:
                radius = HEX_RADIUS * 3.0

            x = center_x + radius * math.cos(angle_rad)
            y = center_y + radius * math.sin(angle_rad)
            
            
            resource = resources.pop(0)
            number = None if resource == ResourceType.DESERT else numbers.pop(0) if numbers else None
            self.tiles.append(HexTile(x, y, resource, number))
               
    def draw(self, screen):
        for tile in self.tiles:
            tile.draw(screen)

# ダイスを振る関数
def roll_dice():
    return random.randint(1, 6) + random.randint(1, 6)

# メインゲームクラス
class CatanGame:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption(WINDOW_TITLE)
        self.clock = pygame.time.Clock()
        self.board = GameBoard()
        self.running = True
        
    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_SPACE:
                    # スペースキーでダイスを振る
                    dice_roll = roll_dice()
                    print(f"ダイスの目: {dice_roll}")
                
    def update(self):
        # ゲーム状態の更新
        pass
        
    def render(self):
        self.screen.fill(COLORS["BLUE"])  # 背景を海の青色に
        self.board.draw(self.screen)
        pygame.display.flip()
        
    def run(self):
        while self.running:
            self.handle_events()
            self.update()
            self.render()
            self.clock.tick(60)
        
        pygame.quit()

# ゲームを実行
if __name__ == "__main__":
    game = CatanGame()
    game.run()