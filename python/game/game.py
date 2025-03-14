import pygame
import random
import math
from game.constants import SCREEN_WIDTH, SCREEN_HEIGHT, COLORS, WINDOW_TITLE
from game.game_board import GameBoard
from game.player import Player
from game.building import Building
from game.dice import roll_dice
from game.resources import ResourceType

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
        self.current_player_index = 0

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
                        self.move_robber()
                    else:
                        self.distribute_resources(dice_roll)

            elif event.type == pygame.MOUSEBUTTONDOWN:
                # 左クリックで建物配置
                if event.button == 1:
                    mx, my = pygame.mouse.get_pos()
                    self.place_building(mx, my)

    def move_robber(self):
        target_tile = random.choice(self.board.tiles)
        self.board.move_robber_to(target_tile)
        print(f"盗賊を ({target_tile.x}, {target_tile.y}) に移動しました。")

    def distribute_resources(self, dice_roll):
        """
        ダイスの目に合致するタイルの資源を配布する。
        ただし、盗賊がいるタイルは生産しない。
        """
        tiles = self.board.get_tiles_with_number(dice_roll)
        for tile in tiles:
            if tile == self.board.robber_tile:
                print(f"盗賊がいるタイル({tile.resource_type})は資源を生産しません。")
                continue

            resource_type = tile.resource_type
            if resource_type == ResourceType.DESERT:
                continue

            for node in tile.corners:
                if node.building is not None:
                    owner = node.building.owner
                    owner.add_resource(resource_type, amount=1)
                    print(f"{owner.name} が {resource_type.name} を1つ獲得！")
        for p in self.players:
            print(p)

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
                print(f"{current_player.name} がノード({closest_node.x:.1f}, {closest_node.y:.1f})に開拓地を建設しました。")
                self.current_player_index = (self.current_player_index + 1) % len(self.players)
            else:
                print("そこには既に建物があります。")

    def update(self):
        # フレーム毎の更新処理（必要に応じて追加）
        pass

    def render(self):
        self.screen.fill(COLORS["BLUE"])  # 背景色：海の青
        self.board.draw(self.screen)
        pygame.display.flip()

    def run(self):
        while self.running:
            self.handle_events()
            self.update()
            self.render()
            self.clock.tick(60)
        
        pygame.quit()
