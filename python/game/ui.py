import math
from dataclasses import dataclass

import pygame

from game.constants import (
    COLORS,
    EDGE_SELECTION_RADIUS,
    HELP_PANEL_HEIGHT,
    HELP_PANEL_COLLAPSED_HEIGHT,
    HELP_PANEL_WIDTH,
    HELP_PANEL_X,
    HELP_PANEL_Y,
    HEX_RADIUS,
    NODE_SELECTION_RADIUS,
    SCREEN_HEIGHT,
    SIDE_PANEL_WIDTH,
    SIDE_PANEL_X,
)
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
    highlighted: bool = False


@dataclass
class PhaseStep:
    label: str
    state: str


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


def _draw_glass_panel(screen, rect, alpha=220):
    panel_surface = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(panel_surface, (*COLORS["PANEL_BG"], alpha), panel_surface.get_rect(), border_radius=18)
    pygame.draw.rect(panel_surface, COLORS["PANEL_BORDER"], panel_surface.get_rect(), 2, border_radius=18)
    screen.blit(panel_surface, rect.topleft)


def _draw_player_name(screen, font, x, y, player):
    pygame.draw.circle(screen, player.color, (x + 8, y + 12), 7)
    pygame.draw.circle(screen, COLORS["BLACK"], (x + 8, y + 12), 7, 1)
    name_surface = font.render(player.name, True, COLORS["WHITE"])
    screen.blit(name_surface, (x + 22, y))
    return name_surface.get_height()


def _draw_build_status(screen, x, y, width, label_font, detail_font, item):
    status_color = COLORS["SUCCESS"] if item["available"] else COLORS["DANGER"]
    label_surface = label_font.render(item["label"], True, COLORS["WHITE"])
    screen.blit(label_surface, (x, y))

    detail_x = x + 74
    lines = _wrap_text(detail_font, item["detail"], width - 74)
    line_y = y
    for line in lines:
        detail_surface = detail_font.render(line, True, status_color)
        screen.blit(detail_surface, (detail_x, line_y))
        line_y += detail_font.get_height() + 2
    return max(label_font.get_height(), line_y - y)


def _get_hex_vertices(tile):
    vertices = []
    for index in range(6):
        angle_deg = 60 * index - 30
        angle_rad = math.pi / 180 * angle_deg
        vertex_x = tile.x + HEX_RADIUS * math.cos(angle_rad)
        vertex_y = tile.y + HEX_RADIUS * math.sin(angle_rad)
        vertices.append((int(vertex_x), int(vertex_y)))
    return vertices


def draw_board_highlights(
    screen,
    settlement_nodes=None,
    city_nodes=None,
    target_nodes=None,
    edge_highlights=None,
    tile_highlights=None,
):
    settlement_nodes = settlement_nodes or []
    city_nodes = city_nodes or []
    target_nodes = target_nodes or []
    edge_highlights = edge_highlights or []
    tile_highlights = tile_highlights or []

    if not any((settlement_nodes, city_nodes, target_nodes, edge_highlights, tile_highlights)):
        return

    pulse = 0.55 + 0.45 * math.sin(pygame.time.get_ticks() / 180)
    overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)

    for tile in tile_highlights:
        vertices = _get_hex_vertices(tile)
        fill_alpha = int(38 + 22 * pulse)
        outline_alpha = int(145 + 70 * pulse)
        pygame.draw.polygon(overlay, (*COLORS["HIGHLIGHT_TILE"], fill_alpha), vertices)
        pygame.draw.polygon(overlay, (*COLORS["HIGHLIGHT_TILE"], outline_alpha), vertices, width=4)

    for node1, node2 in edge_highlights:
        start = (int(node1.x), int(node1.y))
        end = (int(node2.x), int(node2.y))
        alpha = int(90 + 80 * pulse)
        pygame.draw.line(overlay, (*COLORS["HIGHLIGHT_EDGE"], alpha), start, end, EDGE_SELECTION_RADIUS - 4)
        pygame.draw.circle(overlay, (*COLORS["HIGHLIGHT_EDGE"], alpha), start, 8)
        pygame.draw.circle(overlay, (*COLORS["HIGHLIGHT_EDGE"], alpha), end, 8)

    for node in settlement_nodes:
        center = (int(node.x), int(node.y))
        alpha = int(80 + 90 * pulse)
        pygame.draw.circle(overlay, (*COLORS["HIGHLIGHT_NODE"], alpha), center, NODE_SELECTION_RADIUS - 4)
        pygame.draw.circle(overlay, (*COLORS["WHITE"], 235), center, NODE_SELECTION_RADIUS - 6, 2)

    for node in city_nodes:
        rect = pygame.Rect(0, 0, 28, 28)
        rect.center = (int(node.x), int(node.y))
        alpha = int(84 + 96 * pulse)
        pygame.draw.rect(overlay, (*COLORS["HIGHLIGHT_CITY"], alpha), rect, border_radius=8)
        pygame.draw.rect(overlay, (*COLORS["WHITE"], 235), rect, 2, border_radius=8)

    for node in target_nodes:
        center = (int(node.x), int(node.y))
        alpha = int(95 + 90 * pulse)
        pygame.draw.circle(overlay, (*COLORS["HIGHLIGHT_TARGET"], alpha), center, NODE_SELECTION_RADIUS - 3)
        pygame.draw.circle(overlay, (*COLORS["WHITE"], 235), center, NODE_SELECTION_RADIUS - 5, 2)

    screen.blit(overlay, (0, 0))


def _draw_inline_phase_steps(screen, rect, steps):
    if not steps:
        return

    step_font = _load_font(13)
    segment_width = (rect.width - 8 * (len(steps) - 1)) / len(steps)
    for index, step in enumerate(steps):
        step_rect = pygame.Rect(
            int(rect.x + index * (segment_width + 8)),
            rect.y,
            int(segment_width),
            18,
        )
        if step.state == "complete":
            fill = (82, 162, 120)
        elif step.state == "active":
            fill = COLORS["BUTTON_ACTIVE"]
        else:
            fill = (58, 73, 90)
        pygame.draw.rect(screen, fill, step_rect, border_radius=8)
        pygame.draw.rect(screen, COLORS["PANEL_BORDER"], step_rect, 1, border_radius=8)

        label_surface = step_font.render(step.label, True, COLORS["WHITE"])
        label_rect = label_surface.get_rect(center=step_rect.center)
        screen.blit(label_surface, label_rect)


def draw_help_panel(screen, title, lines, accent_text="", collapsed=False):
    panel_height = HELP_PANEL_COLLAPSED_HEIGHT if collapsed else HELP_PANEL_HEIGHT
    panel_y = HELP_PANEL_Y if not collapsed else HELP_PANEL_Y + HELP_PANEL_HEIGHT - HELP_PANEL_COLLAPSED_HEIGHT
    rect = pygame.Rect(HELP_PANEL_X, panel_y, HELP_PANEL_WIDTH, panel_height)
    _draw_glass_panel(screen, rect, alpha=222)

    title_font = _load_font(22)
    body_font = _load_font(16)
    accent_font = _load_font(15)

    if collapsed:
        collapsed_surface = accent_font.render("H: ヘルプを表示", True, COLORS["WARNING"])
        screen.blit(
            collapsed_surface,
            (rect.x + 18, rect.y + (rect.height - collapsed_surface.get_height()) // 2),
        )
        return

    title_surface = title_font.render(title, True, COLORS["WHITE"])
    screen.blit(title_surface, (rect.x + 16, rect.y + 14))

    toggle_surface = accent_font.render("H: 閉じる", True, COLORS["WARNING"])
    screen.blit(
        toggle_surface,
        (rect.right - toggle_surface.get_width() - 16, rect.y + 18),
    )

    line_y = rect.y + 52
    for line in lines[:7]:
        wrapped_lines = _wrap_text(body_font, line, rect.width - 36)
        for wrapped_line in wrapped_lines:
            text_surface = body_font.render(wrapped_line, True, COLORS["TEXT_MUTED"])
            screen.blit(text_surface, (rect.x + 18, line_y))
            line_y += body_font.get_height() + 4
        line_y += 2

    if accent_text:
        accent_lines = _wrap_text(accent_font, accent_text, rect.width - 36)
        accent_y = rect.bottom - 14 - len(accent_lines) * (accent_font.get_height() + 2)
        for accent_line in accent_lines:
            accent_surface = accent_font.render(accent_line, True, COLORS["WARNING"])
            screen.blit(accent_surface, (rect.x + 18, accent_y))
            accent_y += accent_font.get_height() + 2


def draw_button(screen, button):
    if not button.enabled:
        color = COLORS["BUTTON_DISABLED"]
        border_color = COLORS["PANEL_BORDER"]
    elif button.selected:
        color = COLORS["BUTTON_ACTIVE"]
        border_color = COLORS["BUTTON_HIGHLIGHT_BORDER"]
    elif button.highlighted:
        color = COLORS["BUTTON_HIGHLIGHT"]
        border_color = COLORS["BUTTON_HIGHLIGHT_BORDER"]
    else:
        color = COLORS["BUTTON"]
        border_color = COLORS["PANEL_BORDER"]
    if button.enabled and (button.selected or button.highlighted):
        glow_rect = button.rect.inflate(8, 8)
        glow = pygame.Surface(glow_rect.size, pygame.SRCALPHA)
        pygame.draw.rect(glow, (*COLORS["BUTTON_HIGHLIGHT_BORDER"], 42), glow.get_rect(), border_radius=16)
        screen.blit(glow, glow_rect.topleft)
    pygame.draw.rect(screen, color, button.rect, border_radius=12)
    pygame.draw.rect(screen, border_color, button.rect, 2, border_radius=12)
    font = _load_font(17)
    text_surface = font.render(button.label, True, COLORS["BUTTON_TEXT"])
    screen.blit(text_surface, text_surface.get_rect(center=button.rect.center))


def draw_side_panel(
    screen,
    title,
    subtitle,
    phase_steps,
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
    preview_font = _load_font(17)

    title_surface = header_font.render(title, True, COLORS["WHITE"])
    screen.blit(title_surface, (panel_rect.x + 16, panel_rect.y + 14))

    subtitle_y = panel_rect.y + 46
    if subtitle:
        subtitle_lines = _wrap_text(small_font, subtitle, panel_rect.width - 32)
        for line in subtitle_lines[:2]:
            subtitle_surface = small_font.render(line, True, (205, 218, 229))
            screen.blit(subtitle_surface, (panel_rect.x + 16, subtitle_y))
            subtitle_y += small_font.get_height() + 2

    tracker_y = subtitle_y + 6
    if phase_steps:
        tracker_rect = pygame.Rect(panel_rect.x + 16, tracker_y, panel_rect.width - 32, 18)
        _draw_inline_phase_steps(screen, tracker_rect, phase_steps)
        tracker_y = tracker_rect.bottom + 10

    for button in buttons:
        draw_button(screen, button)

    buttons_bottom = max((button.rect.bottom for button in buttons), default=tracker_y + 4)
    section_y = max(tracker_y + 4, buttons_bottom + 18)

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
                preview_font,
                item,
            )
            section_y += height_used + 6
