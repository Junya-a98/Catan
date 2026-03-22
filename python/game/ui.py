from dataclasses import dataclass

import pygame

from game.constants import COLORS, SCREEN_HEIGHT, SIDE_PANEL_WIDTH, SIDE_PANEL_X
from game.resources import ResourceType


FONT_PATH = "Noto_Sans_JP/NotoSansJP-VariableFont_wght.ttf"

RESOURCE_LABELS = {
    ResourceType.WOOD: "木",
    ResourceType.SHEEP: "羊",
    ResourceType.WHEAT: "麦",
    ResourceType.BRICK: "土",
    ResourceType.ORE: "鉄",
}


@dataclass
class UIButton:
    action: str
    label: str
    rect: pygame.Rect
    enabled: bool = True
    selected: bool = False


def _load_font(size):
    try:
        return pygame.font.Font(FONT_PATH, size)
    except Exception:
        return pygame.font.Font(None, size)


def _wrap_text(font, text, max_width):
    lines = []
    current = ""
    for char in text:
        candidate = current + char
        if current and font.size(candidate)[0] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [text]


def _draw_panel_background(screen, rect):
    shadow_rect = rect.move(4, 6)
    shadow = pygame.Surface(shadow_rect.size, pygame.SRCALPHA)
    pygame.draw.rect(shadow, (4, 8, 14, 120), shadow.get_rect(), border_radius=20)
    screen.blit(shadow, shadow_rect.topleft)

    panel_surface = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(panel_surface, (*COLORS["PANEL_BG"], 236), panel_surface.get_rect(), border_radius=18)
    pygame.draw.rect(panel_surface, COLORS["PANEL_BORDER"], panel_surface.get_rect(), 2, border_radius=18)
    screen.blit(panel_surface, rect.topleft)


def _draw_player_name(screen, font, x, y, player):
    pygame.draw.circle(screen, player.color, (x + 8, y + 12), 7)
    pygame.draw.circle(screen, COLORS["BLACK"], (x + 8, y + 12), 7, 1)
    name_surface = font.render(player.name, True, COLORS["WHITE"])
    screen.blit(name_surface, (x + 22, y))
    return name_surface.get_height()


def _draw_build_status(screen, x, y, width, font, item):
    status_color = COLORS["SUCCESS"] if item["available"] else COLORS["DANGER"]
    label_surface = font.render(item["label"], True, COLORS["WHITE"])
    screen.blit(label_surface, (x, y))

    lines = _wrap_text(font, item["detail"], width - 90)
    line_y = y
    for line in lines:
        detail_surface = font.render(line, True, status_color)
        screen.blit(detail_surface, (x + 80, line_y))
        line_y += font.get_height() + 2
    return max(font.get_height(), line_y - y)


def draw_button(screen, button):
    if not button.enabled:
        color = COLORS["BUTTON_DISABLED"]
    elif button.selected:
        color = COLORS["BUTTON_ACTIVE"]
    else:
        color = COLORS["BUTTON"]
    pygame.draw.rect(screen, color, button.rect, border_radius=12)
    pygame.draw.rect(screen, COLORS["PANEL_BORDER"], button.rect, 2, border_radius=12)
    font = _load_font(17)
    text_surface = font.render(button.label, True, COLORS["BUTTON_TEXT"])
    screen.blit(text_surface, text_surface.get_rect(center=button.rect.center))


def draw_side_panel(
    screen,
    title,
    subtitle,
    current_player,
    players,
    buttons,
    points_by_player,
    breakdown_by_player,
    trade_rates,
    development_summary,
    deck_remaining,
    affordability,
):
    panel_rect = pygame.Rect(SIDE_PANEL_X, 12, SIDE_PANEL_WIDTH, SCREEN_HEIGHT - 24)
    _draw_panel_background(screen, panel_rect)

    header_font = _load_font(24)
    section_font = _load_font(18)
    small_font = _load_font(15)

    title_surface = header_font.render(title, True, COLORS["WHITE"])
    screen.blit(title_surface, (panel_rect.x + 16, panel_rect.y + 14))

    subtitle_y = panel_rect.y + 46
    if subtitle:
        subtitle_lines = _wrap_text(small_font, subtitle, panel_rect.width - 32)
        for line in subtitle_lines[:2]:
            subtitle_surface = small_font.render(line, True, (205, 218, 229))
            screen.blit(subtitle_surface, (panel_rect.x + 16, subtitle_y))
            subtitle_y += small_font.get_height() + 2

    for button in buttons:
        draw_button(screen, button)

    buttons_bottom = max((button.rect.bottom for button in buttons), default=subtitle_y + 12)
    section_y = max(subtitle_y + 12, buttons_bottom + 18)

    if current_player is not None:
        section_title = section_font.render("現在プレイヤー", True, COLORS["WHITE"])
        screen.blit(section_title, (panel_rect.x + 16, section_y))
        section_y += section_title.get_height() + 6

        name_height = _draw_player_name(screen, section_font, panel_rect.x + 20, section_y, current_player)
        section_y += name_height + 4

        pieces_text = (
            f"手札 {current_player.total_resource_count()} 枚 / "
            f"道{current_player.roads_remaining} 開{current_player.settlements_remaining} 都{current_player.cities_remaining}"
        )
        pieces_surface = small_font.render(pieces_text, True, COLORS["TEXT_MUTED"])
        screen.blit(pieces_surface, (panel_rect.x + 20, section_y))
        section_y += small_font.get_height() + 2

        if trade_rates:
            trade_text = "交易: " + " ".join(
                f"{RESOURCE_LABELS[resource_type]}{trade_rates[resource_type]}:1"
                for resource_type in (
                    ResourceType.WOOD,
                    ResourceType.SHEEP,
                    ResourceType.WHEAT,
                    ResourceType.BRICK,
                    ResourceType.ORE,
                )
            )
            trade_surface = small_font.render(trade_text, True, COLORS["TEXT_MUTED"])
            screen.blit(trade_surface, (panel_rect.x + 20, section_y))
            section_y += small_font.get_height() + 2

        if development_summary:
            dev_surface = small_font.render(f"発展: {development_summary}", True, COLORS["TEXT_MUTED"])
            screen.blit(dev_surface, (panel_rect.x + 20, section_y))
            section_y += small_font.get_height() + 2

        deck_surface = small_font.render(f"発展山札: {deck_remaining} 枚", True, COLORS["TEXT_MUTED"])
        screen.blit(deck_surface, (panel_rect.x + 20, section_y))
        section_y += small_font.get_height() + 8

    if affordability:
        build_title = section_font.render("建設プレビュー", True, COLORS["WHITE"])
        screen.blit(build_title, (panel_rect.x + 16, section_y))
        section_y += build_title.get_height() + 6

        for item in affordability:
            height_used = _draw_build_status(
                screen,
                panel_rect.x + 20,
                section_y,
                panel_rect.width - 40,
                small_font,
                item,
            )
            section_y += height_used + 4
