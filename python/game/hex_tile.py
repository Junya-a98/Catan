import pygame
import math

from game.assets import get_font
from game.constants import HEX_RADIUS, COLORS
from game.resources import RESOURCE_COLORS, ResourceType
from game.tile_art import get_tile_surface


TOKEN_PIP_COUNTS = {
    2: 1,
    3: 2,
    4: 3,
    5: 4,
    6: 5,
    8: 5,
    9: 4,
    10: 3,
    11: 2,
    12: 1,
}


def get_token_pip_count(number):
    return TOKEN_PIP_COUNTS.get(number, 0)


def _scaled(value, radius, *, minimum=1):
    return max(minimum, round(value * radius / HEX_RADIUS))


def _draw_number_token(screen, center, number, radius=HEX_RADIUS):
    center_x, center_y = int(center[0]), int(center[1])
    token_radius = _scaled(24, radius)
    strong_number = number in (6, 8)
    text_color = (180, 42, 42) if strong_number else (30, 30, 26)
    ring_color = (166, 48, 38) if strong_number else (63, 57, 45)

    pygame.draw.circle(
        screen,
        (35, 34, 29),
        (center_x + _scaled(2, radius), center_y + _scaled(3, radius)),
        token_radius + 1,
    )
    pygame.draw.circle(screen, (249, 245, 222), (center_x, center_y), token_radius)
    pygame.draw.circle(
        screen,
        ring_color,
        (center_x, center_y),
        token_radius,
        _scaled(3, radius),
    )
    arc_radius = _scaled(19, radius)
    pygame.draw.arc(
        screen,
        (255, 255, 248),
        pygame.Rect(
            center_x - arc_radius,
            center_y - arc_radius,
            arc_radius * 2,
            arc_radius * 2,
        ),
        math.radians(205),
        math.radians(330),
        _scaled(2, radius),
    )

    font = get_font(_scaled(30, radius, minimum=18), bold=True)
    text = font.render(str(number), True, text_color)
    screen.blit(
        text,
        text.get_rect(center=(center_x, center_y - _scaled(6, radius))),
    )

    pip_count = get_token_pip_count(number)
    pip_y = center_y + _scaled(12, radius)
    pip_spacing = _scaled(7, radius)
    pip_start_x = center_x - ((pip_count - 1) * pip_spacing) / 2
    for index in range(pip_count):
        pip_x = int(pip_start_x + index * pip_spacing)
        pygame.draw.circle(
            screen,
            text_color,
            (pip_x, pip_y),
            _scaled(3, radius),
        )


def _draw_robber(screen, center, radius=HEX_RADIUS):
    """Draw a pawn-shaped robber that stays recognizable on every terrain."""
    center_x, center_y = int(center[0]), int(center[1])
    outline = (23, 20, 18)
    body = (43, 42, 40)
    edge = COLORS["WARNING"]

    robber_width = _scaled(38, radius)
    pygame.draw.ellipse(
        screen,
        (35, 29, 23),
        (
            center_x - robber_width // 2,
            center_y + _scaled(14, radius),
            robber_width,
            _scaled(12, radius),
        ),
    )
    body_points = [
        (center_x - _scaled(11, radius), center_y - _scaled(3, radius)),
        (center_x + _scaled(11, radius), center_y - _scaled(3, radius)),
        (center_x + _scaled(15, radius), center_y + _scaled(17, radius)),
        (center_x - _scaled(15, radius), center_y + _scaled(17, radius)),
    ]
    pygame.draw.polygon(screen, outline, body_points)
    pygame.draw.polygon(screen, body, body_points, 0)
    pygame.draw.polygon(screen, edge, body_points, _scaled(2, radius))
    head_center = (center_x, center_y - _scaled(10, radius))
    pygame.draw.circle(screen, outline, head_center, _scaled(12, radius))
    pygame.draw.circle(screen, body, head_center, _scaled(10, radius))
    pygame.draw.circle(
        screen,
        edge,
        head_center,
        _scaled(10, radius),
        _scaled(2, radius),
    )
    pygame.draw.circle(
        screen,
        (105, 104, 100),
        (center_x - _scaled(3, radius), center_y - _scaled(13, radius)),
        _scaled(3, radius),
    )
    pygame.draw.line(
        screen,
        (94, 92, 88),
        (center_x - _scaled(7, radius), center_y + _scaled(1, radius)),
        (center_x - _scaled(9, radius), center_y + _scaled(12, radius)),
        _scaled(2, radius),
    )


class HexTile:
    def __init__(self, x, y, resource_type, number=None, *, radius=HEX_RADIUS):
        self.x = x
        self.y = y
        self.resource_type = resource_type
        self.number = number
        self.radius = radius
        self.corners = []  # このタイルを囲むノード(Node)を保持

    def draw(self, screen, robber_tile=None):
        # 六角形の頂点座標を計算
        vertices = []
        for i in range(6):
            angle_deg = 60 * i - 30
            angle_rad = math.pi / 180 * angle_deg
            vertex_x = self.x + self.radius * math.cos(angle_rad)
            vertex_y = self.y + self.radius * math.sin(angle_rad)
            vertices.append((vertex_x, vertex_y))

        tile_surface = get_tile_surface(self.resource_type, self.radius)
        if tile_surface is not None:
            tile_rect = tile_surface.get_rect(center=(self.x, self.y))
            screen.blit(tile_surface, tile_rect)
        else:
            pygame.draw.polygon(screen, RESOURCE_COLORS[self.resource_type], vertices)
        pygame.draw.polygon(
            screen,
            COLORS["BLACK"],
            vertices,
            _scaled(2, self.radius),
        )

        # 数字トークン（砂漠でない場合のみ）
        if self.number is not None and self.resource_type != ResourceType.DESERT:
            _draw_number_token(
                screen,
                (self.x, self.y),
                self.number,
                self.radius,
            )
        elif self.resource_type == ResourceType.DESERT:
            font = get_font(_scaled(15, self.radius, minimum=12), bold=True)
            text = font.render("砂漠", True, (78, 59, 39))
            text_rect = text.get_rect(center=(self.x, self.y))
            screen.blit(text, text_rect)

        # 盗賊の表示
        if robber_tile is self:
            _draw_robber(screen, (self.x, self.y), self.radius)
