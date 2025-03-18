import pygame
import math
import random
from game.constants import SCREEN_WIDTH, SCREEN_HEIGHT, HEX_RADIUS, COLORS
from game.hex_tile import HexTile
from game.node import Node
from game.resources import ResourceType

class GameBoard:
    def __init__(self):
        # 街道、タイル、ノード、盗賊タイルの初期化
        self.roads = []  # 街道(Road)を保持するリスト
        self.tiles = []
        self.nodes = []
        self.robber_tile = None
        self.setup_board()

    def setup_board(self):
        # リソースのリスト作成（砂漠1枚、その他は各3～4枚）
        resources = [ResourceType.DESERT]
        for resource in [res for res in ResourceType if res != ResourceType.DESERT]:
            if resource in (ResourceType.BRICK, ResourceType.ORE):
                resources.extend([resource] * 3)
            else:
                resources.extend([resource] * 4)
        random.shuffle(resources)
        
        # 数字トークン（2～12, 7を除く）
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

        # 1リング目（6枚）
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
                
        # 2リング目（12枚）
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
            num = None if resource == ResourceType.DESERT else (numbers.pop(0) if numbers else None)
            tile = HexTile(x, y, resource, num)
            self.tiles.append(tile)
            if resource == ResourceType.DESERT and self.robber_tile is None:
                self.robber_tile = tile

        # 各タイルの頂点(Node)を作成・共有する
        self._create_nodes_for_tiles()

    def _create_nodes_for_tiles(self):
        def get_hex_corners(cx, cy, radius):
            corners = []
            for i in range(6):
                angle_deg = 60 * i - 30
                angle_rad = math.pi / 180 * angle_deg
                corner_x = cx + radius * math.cos(angle_rad)
                corner_y = cy + radius * math.sin(angle_rad)
                corners.append((corner_x, corner_y))
            return corners
        
        for tile in self.tiles:
            corners = get_hex_corners(tile.x, tile.y, HEX_RADIUS)
            tile.corners = []
            for cx, cy in corners:
                node = self.find_or_create_node(cx, cy)
                tile.corners.append(node)
                if tile not in node.tiles:
                    node.tiles.append(tile)

    def find_or_create_node(self, x, y, threshold=10):
        for node in self.nodes:
            dist = math.hypot(node.x - x, node.y - y)
            if dist < threshold:
                return node
        new_node = Node(x, y)
        self.nodes.append(new_node)
        return new_node

    def draw(self, screen):
        # 1) タイルを描画
        for tile in self.tiles:
            tile.draw(screen, robber_tile=self.robber_tile)
        # 2) 街道を描画（プレイヤーの色で線を引く）
        for road in self.roads:
            pygame.draw.line(
                screen,
                road.owner.color,
                (road.node1.x, road.node1.y),
                (road.node2.x, road.node2.y),
                8  # 線の太さ
            )
        # 3) 建物（開拓地など）を描画
        for node in self.nodes:
            if node.building is not None:
                color = node.building.owner.color
                pygame.draw.circle(screen, color, (int(node.x), int(node.y)), 8)
                pygame.draw.circle(screen, COLORS["BLACK"], (int(node.x), int(node.y)), 8, 1)

    def move_robber_to(self, tile: HexTile):
        self.robber_tile = tile

    def get_tiles_with_number(self, dice_number):
        return [t for t in self.tiles if t.number == dice_number]
