from enum import Enum

import pygame


PIECE_OUTLINE_COLOR = (25, 28, 32)
PIECE_SHADOW_COLOR = (24, 26, 28)


def _mix_color(color, target, amount):
    amount = max(0.0, min(1.0, amount))
    return tuple(
        round(channel + (target_channel - channel) * amount)
        for channel, target_channel in zip(color[:3], target)
    )


def _translate_points(points, center, offset=(0, 0)):
    cx, cy = center
    ox, oy = offset
    return [(round(cx + x + ox), round(cy + y + oy)) for x, y in points]


def _draw_owner_mark(surface, center, pattern, color):
    x, y = center
    pattern %= 4
    if pattern == 0:
        pygame.draw.circle(surface, color, (x, y), 2)
    elif pattern == 1:
        pygame.draw.polygon(surface, color, ((x, y - 3), (x + 3, y), (x, y + 3), (x - 3, y)))
    elif pattern == 2:
        pygame.draw.polygon(surface, color, ((x, y - 3), (x + 3, y + 2), (x - 3, y + 2)))
    else:
        pygame.draw.rect(surface, color, pygame.Rect(x - 2, y - 2, 5, 5))


class BuildingType(Enum):
    SETTLEMENT = "settlement"
    CITY = "city"


class Building:
    def __init__(self, owner, building_type=BuildingType.SETTLEMENT):
        self.owner = owner
        self.building_type = building_type

    @property
    def victory_points(self):
        if self.building_type == BuildingType.CITY:
            return 2
        return 1

    @property
    def resource_multiplier(self):
        if self.building_type == BuildingType.CITY:
            return 2
        return 1

    def upgrade_to_city(self):
        self.building_type = BuildingType.CITY

    def draw(self, surface, center):
        """Render a settlement or city as a small dimensional wooden token."""
        center = (round(center[0]), round(center[1]))
        if self.building_type == BuildingType.CITY:
            self._draw_city(surface, center)
        else:
            self._draw_settlement(surface, center)

    def _piece_colors(self):
        base = tuple(self.owner.color[:3])
        return {
            "base": base,
            "light": _mix_color(base, (255, 255, 244), 0.50),
            "roof": _mix_color(base, (48, 31, 22), 0.26),
            "shade": _mix_color(base, (22, 19, 18), 0.43),
            "detail": _mix_color(base, (23, 24, 27), 0.58),
        }

    def _draw_settlement(self, surface, center):
        colors = self._piece_colors()
        silhouette = ((-11, 10), (-11, -1), (0, -12), (11, -1), (11, 10))

        pygame.draw.ellipse(
            surface,
            PIECE_SHADOW_COLOR,
            pygame.Rect(center[0] - 12, center[1] + 6, 27, 9),
        )
        pygame.draw.polygon(
            surface,
            PIECE_SHADOW_COLOR,
            _translate_points(silhouette, center, (2, 3)),
        )
        pygame.draw.polygon(surface, colors["base"], _translate_points(silhouette, center))

        roof = ((-11, -1), (0, -12), (11, -1), (9, 2), (0, -7), (-9, 2))
        pygame.draw.polygon(surface, colors["roof"], _translate_points(roof, center))
        right_bevel = ((7, 2), (11, -1), (11, 10), (7, 8))
        pygame.draw.polygon(surface, colors["shade"], _translate_points(right_bevel, center))

        door = pygame.Rect(center[0] - 2, center[1] + 3, 5, 7)
        pygame.draw.rect(surface, colors["detail"], door, border_radius=1)
        pygame.draw.line(
            surface,
            colors["light"],
            (center[0] - 9, center[1] - 1),
            (center[0], center[1] - 10),
            2,
        )
        pygame.draw.line(
            surface,
            colors["light"],
            (center[0] - 9, center[1] + 1),
            (center[0] - 9, center[1] + 8),
            1,
        )
        _draw_owner_mark(
            surface,
            (center[0], center[1] - 2),
            getattr(self.owner, "piece_pattern", 0),
            colors["light"],
        )
        pygame.draw.polygon(
            surface,
            PIECE_OUTLINE_COLOR,
            _translate_points(silhouette, center),
            2,
        )

    def _draw_city(self, surface, center):
        colors = self._piece_colors()
        # The attached house and taller hall create a city silhouette that is
        # immediately distinguishable from the settlement at board scale.
        silhouette = (
            (-16, 12),
            (-16, -1),
            (-8, -10),
            (0, -3),
            (0, -13),
            (13, -13),
            (13, 12),
        )

        pygame.draw.ellipse(
            surface,
            PIECE_SHADOW_COLOR,
            pygame.Rect(center[0] - 17, center[1] + 7, 34, 10),
        )
        pygame.draw.polygon(
            surface,
            PIECE_SHADOW_COLOR,
            _translate_points(silhouette, center, (2, 3)),
        )
        pygame.draw.polygon(surface, colors["base"], _translate_points(silhouette, center))

        house_roof = ((-16, -1), (-8, -10), (0, -3), (0, 1), (-8, -6), (-16, 2))
        pygame.draw.polygon(surface, colors["roof"], _translate_points(house_roof, center))
        tower_top = ((0, -13), (13, -13), (10, -9), (0, -9))
        pygame.draw.polygon(surface, colors["light"], _translate_points(tower_top, center))
        right_bevel = ((10, -9), (13, -13), (13, 12), (9, 9))
        pygame.draw.polygon(surface, colors["shade"], _translate_points(right_bevel, center))

        pygame.draw.line(
            surface,
            colors["light"],
            (center[0] - 14, center[1] - 1),
            (center[0] - 8, center[1] - 8),
            2,
        )
        pygame.draw.line(
            surface,
            colors["detail"],
            (center[0], center[1] - 8),
            (center[0], center[1] + 10),
            1,
        )
        for window_x in (4,):
            window = pygame.Rect(center[0] + window_x, center[1] - 5, 4, 5)
            pygame.draw.rect(surface, colors["detail"], window, border_radius=1)
            pygame.draw.line(
                surface,
                colors["light"],
                window.topleft,
                (window.right - 1, window.top),
                1,
            )
        door = pygame.Rect(center[0] - 11, center[1] + 4, 5, 8)
        pygame.draw.rect(surface, colors["detail"], door, border_radius=1)
        _draw_owner_mark(
            surface,
            (center[0] + 5, center[1] + 3),
            getattr(self.owner, "piece_pattern", 0),
            colors["light"],
        )
        pygame.draw.polygon(
            surface,
            PIECE_OUTLINE_COLOR,
            _translate_points(silhouette, center),
            2,
        )
