import pygame

from game.assets import get_font
from game.constants import BOARD_CENTER_X, BOARD_CENTER_Y, COLORS, SCREEN_HEIGHT, SCREEN_WIDTH


DIE_SIZE = 96
DICE_GAP = 24
ROLL_DURATION_MS = 900
RESULT_HOLD_MS = 650
FRAME_TIME_MS = 45

ROLL_FRAME_VALUES = [
    (1, 5),
    (6, 2),
    (3, 4),
    (5, 6),
    (2, 1),
    (4, 3),
    (6, 6),
    (1, 2),
    (5, 4),
    (3, 1),
    (2, 6),
    (4, 5),
    (6, 3),
    (1, 4),
    (5, 2),
    (3, 6),
    (2, 5),
    (4, 1),
    (6, 4),
    (1, 3),
    (5, 1),
    (2, 4),
    (3, 5),
    (4, 6),
]

PIP_LAYOUTS = {
    1: [(0, 0)],
    2: [(-1, -1), (1, 1)],
    3: [(-1, -1), (0, 0), (1, 1)],
    4: [(-1, -1), (1, -1), (-1, 1), (1, 1)],
    5: [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)],
    6: [(-1, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (1, 1)],
}


def _load_font(size):
    return get_font(size)


def _draw_die(surface, value):
    rect = surface.get_rect()
    pygame.draw.rect(surface, (248, 250, 252), rect, border_radius=20)
    pygame.draw.rect(surface, COLORS["PANEL_BORDER"], rect, 3, border_radius=20)

    center_x = rect.width // 2
    center_y = rect.height // 2
    offset = 22
    pip_radius = 6
    pip_color = (24, 32, 42)

    for dx, dy in PIP_LAYOUTS[value]:
        pygame.draw.circle(
            surface,
            pip_color,
            (center_x + dx * offset, center_y + dy * offset),
            pip_radius,
        )


def _compose_dice_surface(left_value, right_value):
    width = DIE_SIZE * 2 + DICE_GAP
    height = DIE_SIZE
    surface = pygame.Surface((width, height), pygame.SRCALPHA)

    left_surface = pygame.Surface((DIE_SIZE, DIE_SIZE), pygame.SRCALPHA)
    right_surface = pygame.Surface((DIE_SIZE, DIE_SIZE), pygame.SRCALPHA)
    _draw_die(left_surface, left_value)
    _draw_die(right_surface, right_value)

    surface.blit(left_surface, (0, 0))
    surface.blit(right_surface, (DIE_SIZE + DICE_GAP, 0))
    return surface


class DiceAnimationOverlay:
    def __init__(self):
        self.available = True
        self.face_images = {value: self._build_die_face(value) for value in range(1, 7)}
        self.roll_frames = [_compose_dice_surface(left, right) for left, right in ROLL_FRAME_VALUES]
        self.shadow_image = self._build_shadow()
        self.state = "idle"
        self.result_values = (1, 1)
        self.result_total = 2
        self.title = ""
        self.subtitle = ""
        self.roll_started_at = 0
        self.result_started_at = 0

    @property
    def is_active(self):
        return self.state != "idle"

    def _build_die_face(self, value):
        surface = pygame.Surface((DIE_SIZE, DIE_SIZE), pygame.SRCALPHA)
        _draw_die(surface, value)
        return surface

    def _build_shadow(self):
        width = DIE_SIZE * 2 + DICE_GAP + 28
        height = DIE_SIZE + 24
        surface = pygame.Surface((width, height), pygame.SRCALPHA)
        pygame.draw.ellipse(surface, (0, 0, 0, 72), pygame.Rect(0, 8, width, height - 8))
        return surface

    def start(self, result_values, title, subtitle=""):
        if isinstance(result_values, int):
            result_values = (max(1, min(6, result_values - 1)), 1)

        self.result_values = tuple(result_values)
        self.result_total = sum(self.result_values)
        self.title = title
        self.subtitle = subtitle
        self.roll_started_at = pygame.time.get_ticks()
        self.result_started_at = 0
        self.state = "rolling"

    def update(self, now):
        if self.state == "idle":
            return False

        if self.state == "rolling":
            if now - self.roll_started_at >= ROLL_DURATION_MS:
                self.state = "result"
                self.result_started_at = now
            return False

        if self.state == "result" and now - self.result_started_at >= RESULT_HOLD_MS:
            self.state = "idle"
            return True

        return False

    def draw(self, screen):
        if not self.is_active:
            return

        overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        overlay.fill((5, 10, 18, 110))
        screen.blit(overlay, (0, 0))

        panel_rect = pygame.Rect(0, 0, 360, 320)
        panel_rect.center = (BOARD_CENTER_X, BOARD_CENTER_Y + 8)
        panel_surface = pygame.Surface(panel_rect.size, pygame.SRCALPHA)
        pygame.draw.rect(panel_surface, (*COLORS["PANEL_BG"], 238), panel_surface.get_rect(), border_radius=26)
        pygame.draw.rect(panel_surface, COLORS["PANEL_BORDER"], panel_surface.get_rect(), 2, border_radius=26)
        screen.blit(panel_surface, panel_rect.topleft)

        image = self._get_current_image()
        shadow_rect = self.shadow_image.get_rect(center=(panel_rect.centerx, panel_rect.centery + 72))
        screen.blit(self.shadow_image, shadow_rect)

        image_rect = image.get_rect(center=(panel_rect.centerx, panel_rect.centery + 8))
        screen.blit(image, image_rect)

        title_font = _load_font(28)
        subtitle_font = _load_font(18)
        total_font = _load_font(30)
        detail_font = _load_font(18)

        title_surface = title_font.render(self.title, True, COLORS["WHITE"])
        screen.blit(title_surface, title_surface.get_rect(center=(panel_rect.centerx, panel_rect.y + 34)))

        if self.subtitle:
            subtitle_surface = subtitle_font.render(self.subtitle, True, COLORS["TEXT_MUTED"])
            screen.blit(subtitle_surface, subtitle_surface.get_rect(center=(panel_rect.centerx, panel_rect.y + 68)))

        total_surface = total_font.render(f"合計 {self.result_total}", True, (255, 225, 165))
        screen.blit(total_surface, total_surface.get_rect(center=(panel_rect.centerx, panel_rect.bottom - 56)))

        detail_text = f"{self.result_values[0]} + {self.result_values[1]}"
        if self.state == "rolling":
            detail_text = "2d6 を振っています..."
        detail_surface = detail_font.render(detail_text, True, (235, 240, 250))
        screen.blit(detail_surface, detail_surface.get_rect(center=(panel_rect.centerx, panel_rect.bottom - 26)))

    def _get_current_image(self):
        if self.state == "rolling":
            elapsed = pygame.time.get_ticks() - self.roll_started_at
            frame_index = int((elapsed / FRAME_TIME_MS) % len(self.roll_frames))
            return self.roll_frames[frame_index]

        left_surface = self.face_images[self.result_values[0]]
        right_surface = self.face_images[self.result_values[1]]
        surface = pygame.Surface((DIE_SIZE * 2 + DICE_GAP, DIE_SIZE), pygame.SRCALPHA)
        surface.blit(left_surface, (0, 0))
        surface.blit(right_surface, (DIE_SIZE + DICE_GAP, 0))
        return surface
