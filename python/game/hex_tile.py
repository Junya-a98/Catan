import pygame
import math
from game.constants import HEX_RADIUS, COLORS
from game.resources import RESOURCE_COLORS, ResourceType

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
        
        # 数字トークン（砂漠でない場合のみ）
        if self.number is not None and self.resource_type != ResourceType.DESERT:
            font = pygame.font.SysFont(None, 30)
            text = font.render(str(self.number), True, COLORS["BLACK"])
            text_rect = text.get_rect(center=(self.x, self.y))
            # 白円を背景にして数字を描画
            pygame.draw.circle(screen, COLORS["WHITE"], (self.x, self.y), 20)
            pygame.draw.circle(screen, COLORS["BLACK"], (self.x, self.y), 20, 1)
            screen.blit(text, text_rect)

        # 盗賊の表示（黒円）
        if robber_tile is self:
            pygame.draw.circle(screen, COLORS["BLACK"], (self.x, self.y), 10)
