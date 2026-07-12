import math

import pygame

from game.player import Player
from game.node import Node


ROAD_OUTLINE_COLOR = (25, 28, 32)
ROAD_SHADOW_COLOR = (24, 26, 28)


def _mix_color(color, target, amount):
    """Blend an RGB color while keeping player-piece hues recognizable."""
    amount = max(0.0, min(1.0, amount))
    return tuple(
        round(channel + (target_channel - channel) * amount)
        for channel, target_channel in zip(color[:3], target)
    )


def _rounded_points(points):
    return [(round(x), round(y)) for x, y in points]


class Road:
    def __init__(self, owner: Player, node1: Node, node2: Node):
        self.owner = owner
        self.node1 = node1
        self.node2 = node2

    def touches(self, node: Node):
        return self.node1 is node or self.node2 is node

    def other_node(self, node: Node):
        if self.node1 is node:
            return self.node2
        if self.node2 is node:
            return self.node1
        return None

    def draw(self, surface):
        """Draw a beveled wooden road piece between its two nodes.

        The geometry remains tied exactly to the underlying edge, but the piece is
        rendered as a short plank instead of a plain line.  A dark silhouette,
        drop shadow, and light/dark bevel keep every player color readable against
        both pale and dark terrain.
        """
        node_start = (float(self.node1.x), float(self.node1.y))
        node_end = (float(self.node2.x), float(self.node2.y))
        dx = node_end[0] - node_start[0]
        dy = node_end[1] - node_start[1]
        full_length = math.hypot(dx, dy)
        if full_length < 1.0:
            return

        axis = (dx / full_length, dy / full_length)
        normal = (-axis[1], axis[0])
        inset = min(6.0, full_length * 0.15)
        start = (
            node_start[0] + axis[0] * inset,
            node_start[1] + axis[1] * inset,
        )
        length = full_length - inset * 2
        base_color = tuple(self.owner.color[:3])
        highlight_color = _mix_color(base_color, (255, 255, 246), 0.52)
        bevel_color = _mix_color(base_color, (24, 20, 17), 0.38)
        grain_color = _mix_color(base_color, (44, 30, 21), 0.28)

        shadow = self._plank_polygon(
            start,
            axis,
            normal,
            length,
            width=17,
            cap_extension=2.2,
            offset=(2.5, 3.5),
        )
        outline = self._plank_polygon(
            start,
            axis,
            normal,
            length,
            width=16,
            cap_extension=2.0,
        )
        face = self._plank_polygon(
            start,
            axis,
            normal,
            length,
            width=11,
            cap_extension=1.4,
        )

        pygame.draw.polygon(surface, ROAD_SHADOW_COLOR, shadow)
        pygame.draw.polygon(surface, ROAD_OUTLINE_COLOR, outline)
        pygame.draw.polygon(surface, base_color, face)

        # Upper and lower bevels give the road a small wooden-block profile.
        self._draw_local_line(
            surface,
            start,
            axis,
            normal,
            (3.5, -3.5),
            (length - 3.5, -3.5),
            highlight_color,
            2,
        )
        self._draw_local_line(
            surface,
            start,
            axis,
            normal,
            (3.5, 3.7),
            (length - 3.5, 3.7),
            bevel_color,
            2,
        )

        # Restrained grain marks make the token feel physical without obscuring
        # the owning player's color at normal board scale.
        for along, side in ((0.30, -0.4), (0.64, 0.6)):
            center = length * along
            self._draw_local_line(
                surface,
                start,
                axis,
                normal,
                (center - 3.0, side),
                (center + 3.0, side),
                grain_color,
                1,
            )

        pattern = getattr(self.owner, "piece_pattern", 0) % 4
        if pattern in (0, 1):
            positions = (0.5,) if pattern == 0 else (0.40, 0.60)
            for position in positions:
                point = (
                    round(start[0] + axis[0] * length * position),
                    round(start[1] + axis[1] * length * position),
                )
                pygame.draw.circle(surface, grain_color, point, 2)
                pygame.draw.circle(surface, highlight_color, point, 2, 1)
        else:
            positions = (0.5,) if pattern == 2 else (0.38, 0.62)
            for position in positions:
                center = length * position
                self._draw_local_line(
                    surface,
                    start,
                    axis,
                    normal,
                    (center, -3.0),
                    (center, 3.0),
                    highlight_color,
                    1,
                )

    @staticmethod
    def _plank_polygon(
        start,
        axis,
        normal,
        length,
        *,
        width,
        cap_extension,
        offset=(0.0, 0.0),
    ):
        half_width = width / 2
        bevel = min(3.5, length * 0.12)
        local_points = (
            (-cap_extension, -half_width * 0.48),
            (bevel, -half_width),
            (length - bevel, -half_width),
            (length + cap_extension, -half_width * 0.48),
            (length + cap_extension, half_width * 0.48),
            (length - bevel, half_width),
            (bevel, half_width),
            (-cap_extension, half_width * 0.48),
        )
        points = []
        for along, across in local_points:
            x = start[0] + axis[0] * along + normal[0] * across + offset[0]
            y = start[1] + axis[1] * along + normal[1] * across + offset[1]
            points.append((x, y))
        return _rounded_points(points)

    @staticmethod
    def _draw_local_line(surface, start, axis, normal, local_start, local_end, color, width):
        def transform(point):
            along, across = point
            return (
                round(start[0] + axis[0] * along + normal[0] * across),
                round(start[1] + axis[1] * along + normal[1] * across),
            )

        pygame.draw.line(surface, color, transform(local_start), transform(local_end), width)
