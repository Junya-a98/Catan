import io
import os
import zipfile

import pygame

from game.constants import BOARD_CENTER_X, BOARD_CENTER_Y, COLORS, SCREEN_HEIGHT, SCREEN_WIDTH


ASSET_ZIP_PATH = "material/d12_pygame_assets.zip"
FONT_PATH = "Noto_Sans_JP/NotoSansJP-VariableFont_wght.ttf"
DISPLAY_SIZE = 188
ROLL_DURATION_MS = 900
RESULT_HOLD_MS = 650
FRAME_TIME_MS = 45


def _load_font(size):
    try:
        return pygame.font.Font(FONT_PATH, size)
    except Exception:
        return pygame.font.Font(None, size)


class DiceAnimationOverlay:
    def __init__(self, asset_zip_path=ASSET_ZIP_PATH):
        self.asset_zip_path = asset_zip_path
        self.available = False
        self.idle_image = None
        self.shadow_image = None
        self.face_images = {}
        self.roll_frames = []
        self.state = "idle"
        self.result = None
        self.title = ""
        self.subtitle = ""
        self.roll_started_at = 0
        self.result_started_at = 0
        self._load_assets()

    @property
    def is_active(self):
        return self.state != "idle"

    def _load_assets(self):
        if not os.path.exists(self.asset_zip_path):
            return

        try:
            with zipfile.ZipFile(self.asset_zip_path) as asset_zip:
                self.idle_image = self._load_scaled_image(asset_zip, "d12_idle.png")
                self.shadow_image = self._load_shadow(asset_zip)
                self.roll_frames = [
                    self._load_scaled_image(asset_zip, f"roll/d12_roll_{index:02d}.png")
                    for index in range(24)
                ]
                self.face_images = {
                    index: self._load_scaled_image(asset_zip, f"faces/d12_face_{index:02d}.png")
                    for index in range(1, 13)
                }
        except (OSError, zipfile.BadZipFile, pygame.error) as error:
            print("ダイス素材の読み込みに失敗:", error)
            self.idle_image = None
            self.shadow_image = None
            self.roll_frames = []
            self.face_images = {}
            self.available = False
            return

        self.available = bool(self.roll_frames and self.face_images)

    def _load_scaled_image(self, asset_zip, member_name):
        raw_bytes = asset_zip.read(member_name)
        image = pygame.image.load(io.BytesIO(raw_bytes), member_name).convert_alpha()
        return pygame.transform.smoothscale(image, (DISPLAY_SIZE, DISPLAY_SIZE))

    def _load_shadow(self, asset_zip):
        raw_bytes = asset_zip.read("d12_shadow.png")
        image = pygame.image.load(io.BytesIO(raw_bytes), "d12_shadow.png").convert_alpha()
        return pygame.transform.smoothscale(image, (DISPLAY_SIZE + 18, DISPLAY_SIZE + 18))

    def start(self, result, title, subtitle=""):
        self.result = result
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

        panel_rect = pygame.Rect(0, 0, 320, 312)
        panel_rect.center = (BOARD_CENTER_X, BOARD_CENTER_Y + 8)
        panel_surface = pygame.Surface(panel_rect.size, pygame.SRCALPHA)
        pygame.draw.rect(panel_surface, (*COLORS["PANEL_BG"], 238), panel_surface.get_rect(), border_radius=26)
        pygame.draw.rect(panel_surface, COLORS["PANEL_BORDER"], panel_surface.get_rect(), 2, border_radius=26)
        screen.blit(panel_surface, panel_rect.topleft)

        image = self._get_current_image()
        shadow = self.shadow_image if self.shadow_image is not None else None
        image_rect = image.get_rect(center=(panel_rect.centerx, panel_rect.centery + 18))

        if shadow is not None:
            shadow_rect = shadow.get_rect(center=(image_rect.centerx, image_rect.centery + 78))
            shadow_surface = shadow.copy()
            shadow_surface.set_alpha(150)
            screen.blit(shadow_surface, shadow_rect)

        screen.blit(image, image_rect)

        title_font = _load_font(28)
        subtitle_font = _load_font(18)
        result_font = _load_font(22)

        title_surface = title_font.render(self.title, True, COLORS["WHITE"])
        screen.blit(title_surface, title_surface.get_rect(center=(panel_rect.centerx, panel_rect.y + 34)))

        if self.subtitle:
            subtitle_surface = subtitle_font.render(self.subtitle, True, COLORS["TEXT_MUTED"])
            screen.blit(subtitle_surface, subtitle_surface.get_rect(center=(panel_rect.centerx, panel_rect.y + 68)))

        if self.state == "rolling":
            footer_text = "ダイスを振っています..."
            footer_color = (235, 240, 250)
        else:
            footer_text = f"結果: {self.result}"
            footer_color = (255, 225, 165)
        footer_surface = result_font.render(footer_text, True, footer_color)
        screen.blit(footer_surface, footer_surface.get_rect(center=(panel_rect.centerx, panel_rect.bottom - 28)))

    def _get_current_image(self):
        if not self.available:
            return self._build_fallback_surface()

        if self.state == "rolling":
            elapsed = pygame.time.get_ticks() - self.roll_started_at
            frame_index = int((elapsed / FRAME_TIME_MS) % len(self.roll_frames))
            return self.roll_frames[frame_index]

        if self.state == "result":
            return self.face_images.get(self.result, self.idle_image)

        return self.idle_image

    def _build_fallback_surface(self):
        surface = pygame.Surface((DISPLAY_SIZE, DISPLAY_SIZE), pygame.SRCALPHA)
        pygame.draw.circle(surface, (230, 236, 244), (DISPLAY_SIZE // 2, DISPLAY_SIZE // 2), DISPLAY_SIZE // 2 - 8)
        pygame.draw.circle(surface, COLORS["PANEL_BORDER"], (DISPLAY_SIZE // 2, DISPLAY_SIZE // 2), DISPLAY_SIZE // 2 - 8, 4)
        font = _load_font(46)
        text = font.render(str(self.result or "?"), True, COLORS["BLACK"])
        surface.blit(text, text.get_rect(center=(DISPLAY_SIZE // 2, DISPLAY_SIZE // 2)))
        return surface
