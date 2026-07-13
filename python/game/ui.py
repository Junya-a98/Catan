import math
from dataclasses import dataclass
from functools import lru_cache

import pygame

from game.assets import get_font
from game.constants import (
    BOARD_CENTER_X,
    COLORS,
    EDGE_SELECTION_RADIUS,
    HELP_PANEL_HEIGHT,
    HELP_PANEL_COLLAPSED_HEIGHT,
    HELP_PANEL_WIDTH,
    HELP_PANEL_X,
    HELP_PANEL_Y,
    HEX_RADIUS,
    LOG_PANEL_WIDTH,
    NODE_SELECTION_RADIUS,
    SCREEN_HEIGHT,
    SIDE_PANEL_WIDTH,
    SIDE_PANEL_X,
)
from game.resources import ResourceType


RESOURCE_LABELS = {
    ResourceType.WOOD: "木",
    ResourceType.SHEEP: "羊",
    ResourceType.WHEAT: "麦",
    ResourceType.BRICK: "土",
    ResourceType.ORE: "鉄",
}

PROHIBITED_LINE_START = frozenset("、。，．・：；？！)]｝〕〉》」』】〙〗〟’”")


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
    return get_font(size)


def _wrap_text(font, text, max_width):
    lines = []
    current = ""
    for char in text:
        candidate = current + char
        if current and font.size(candidate)[0] > max_width:
            if char in PROHIBITED_LINE_START and len(current) > 1:
                lines.append(current[:-1])
                current = current[-1] + char
            else:
                lines.append(current)
                current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [text]


def _truncate_text(font, text, max_width):
    if font.size(text)[0] <= max_width:
        return text
    ellipsis = "…"
    result = text
    while result and font.size(result + ellipsis)[0] > max_width:
        result = result[:-1]
    return result + ellipsis


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


@lru_cache(maxsize=4)
def _build_ocean_surface(width, height):
    surface = pygame.Surface((width, height), pygame.SRCALPHA)
    top = (76, 133, 180)
    bottom = (45, 96, 143)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(int(top[index] + (bottom[index] - top[index]) * ratio) for index in range(3))
        pygame.draw.line(surface, color, (0, y), (width, y))

    wave_color = (190, 224, 239, 42)
    wave_positions = (
        (36, 140, 52),
        (width - 86, 120, 56),
        (38, height - 210, 62),
        (width - 94, height - 170, 68),
        (width // 2 - 35, height - 72, 70),
    )
    for x, y, wave_width in wave_positions:
        for offset in range(3):
            arc_rect = pygame.Rect(x, y + offset * 9, wave_width, 18)
            pygame.draw.arc(surface, wave_color, arc_rect, math.radians(195), math.radians(345), 2)
    return surface


def draw_ocean_background(screen):
    """Give the board area depth without adding external image assets."""
    board_left = LOG_PANEL_WIDTH + 12
    board_width = SIDE_PANEL_X - board_left
    ocean = _build_ocean_surface(board_width, SCREEN_HEIGHT)
    screen.blit(ocean, (board_left, 0))

    island_shadow = pygame.Surface((440, 500), pygame.SRCALPHA)
    pygame.draw.ellipse(island_shadow, (7, 24, 35, 32), island_shadow.get_rect())
    screen.blit(island_shadow, (BOARD_CENTER_X - 220, 132))


def _draw_player_name(screen, font, x, y, player):
    pygame.draw.circle(screen, player.color, (x + 8, y + 12), 7)
    pygame.draw.circle(screen, COLORS["BLACK"], (x + 8, y + 12), 7, 1)
    marker = getattr(player, "marker", "●")
    name_surface = font.render(f"{marker} {player.name}", True, COLORS["WHITE"])
    screen.blit(name_surface, (x + 22, y))
    return name_surface.get_height()


def _draw_player_stat_chips(screen, player, x, y, max_width):
    """Draw the hand and remaining pieces without cryptic one-letter labels."""
    font = _load_font(13)
    chip_height = 25
    gap = 5
    chip_specs = (
        (f"手札 {player.total_resource_count()}", COLORS["BUTTON_HIGHLIGHT_BORDER"]),
        (f"街道 {player.roads_remaining}", COLORS["CARD_BORDER"]),
        (f"開拓地 {player.settlements_remaining}", COLORS["CARD_BORDER"]),
        (f"都市 {player.cities_remaining}", COLORS["CARD_BORDER"]),
    )
    desired_widths = [font.size(label)[0] + 14 for label, _ in chip_specs]
    available_for_chips = max_width - gap * (len(chip_specs) - 1)
    if sum(desired_widths) > available_for_chips:
        chip_widths = [available_for_chips // len(chip_specs)] * len(chip_specs)
        chip_widths[-1] += available_for_chips - sum(chip_widths)
    else:
        chip_widths = desired_widths

    rendered_chips = []
    chip_x = x
    for (label, border_color), chip_width in zip(chip_specs, chip_widths):
        rect = pygame.Rect(chip_x, y, chip_width, chip_height)
        fill_color = (42, 51, 61) if not rendered_chips else COLORS["CARD_BG"]
        pygame.draw.rect(screen, fill_color, rect, border_radius=8)
        pygame.draw.rect(screen, border_color, rect, 1, border_radius=8)
        text = _truncate_text(font, label, rect.width - 10)
        text_surface = font.render(
            text,
            True,
            COLORS["WHITE"] if not rendered_chips else COLORS["TEXT_MUTED"],
        )
        screen.blit(text_surface, text_surface.get_rect(center=rect.center))
        rendered_chips.append((label, rect))
        chip_x = rect.right + gap
    return rendered_chips


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
        fill_alpha = int(7 + 6 * pulse)
        outline_alpha = int(145 + 70 * pulse)
        pygame.draw.polygon(overlay, (*COLORS["HIGHLIGHT_TILE"], fill_alpha), vertices)
        pygame.draw.polygon(overlay, (*COLORS["HIGHLIGHT_TILE"], outline_alpha), vertices, width=4)

    for node1, node2 in edge_highlights:
        start = (int(node1.x), int(node1.y))
        end = (int(node2.x), int(node2.y))
        alpha = int(110 + 80 * pulse)
        pygame.draw.line(overlay, (*COLORS["HIGHLIGHT_EDGE"], 54), start, end, 10)
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = max(1.0, math.hypot(dx, dy))
        axis_x, axis_y = dx / length, dy / length
        segment_start = 4.0
        while segment_start < length - 4:
            segment_end = min(segment_start + 7, length - 4)
            dash_start = (
                round(start[0] + axis_x * segment_start),
                round(start[1] + axis_y * segment_start),
            )
            dash_end = (
                round(start[0] + axis_x * segment_end),
                round(start[1] + axis_y * segment_end),
            )
            pygame.draw.line(overlay, (*COLORS["HIGHLIGHT_EDGE"], alpha), dash_start, dash_end, 4)
            segment_start += 11
        midpoint = (round((start[0] + end[0]) / 2), round((start[1] + end[1]) / 2))
        pygame.draw.circle(overlay, (24, 30, 42, 220), midpoint, 8)
        pygame.draw.circle(overlay, (*COLORS["HIGHLIGHT_EDGE"], 245), midpoint, 8, 2)
        pygame.draw.line(overlay, (*COLORS["WHITE"], 245), (midpoint[0] - 4, midpoint[1]), (midpoint[0] + 4, midpoint[1]), 2)
        pygame.draw.line(overlay, (*COLORS["WHITE"], 245), (midpoint[0], midpoint[1] - 4), (midpoint[0], midpoint[1] + 4), 2)

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

    mouse_x, mouse_y = pygame.mouse.get_pos()
    hover_text = None
    best_distance = float("inf")

    for nodes, label in (
        (settlement_nodes, "ここに開拓地を建設"),
        (city_nodes, "都市へアップグレード"),
        (target_nodes, "この相手から略奪"),
    ):
        for node in nodes:
            distance = math.hypot(mouse_x - node.x, mouse_y - node.y)
            if distance <= NODE_SELECTION_RADIUS + 8 and distance < best_distance:
                best_distance = distance
                hover_text = label

    for node1, node2 in edge_highlights:
        center_x = (node1.x + node2.x) / 2
        center_y = (node1.y + node2.y) / 2
        distance = math.hypot(mouse_x - center_x, mouse_y - center_y)
        if distance <= EDGE_SELECTION_RADIUS + 8 and distance < best_distance:
            best_distance = distance
            hover_text = "ここに街道を建設"

    for tile in tile_highlights:
        distance = math.hypot(mouse_x - tile.x, mouse_y - tile.y)
        if distance <= HEX_RADIUS and distance < best_distance:
            best_distance = distance
            hover_text = "ここへ盗賊を移動"

    if hover_text:
        hint_font = _load_font(14)
        hint_surface = hint_font.render(hover_text, True, COLORS["WHITE"])
        hint_rect = pygame.Rect(0, 0, hint_surface.get_width() + 22, hint_surface.get_height() + 14)
        hint_rect.topleft = (mouse_x + 16, mouse_y + 16)
        if hint_rect.right > SIDE_PANEL_X - 8:
            hint_rect.right = SIDE_PANEL_X - 8
        if hint_rect.bottom > SCREEN_HEIGHT - 8:
            hint_rect.bottom = SCREEN_HEIGHT - 8
        pygame.draw.rect(screen, (24, 34, 46), hint_rect, border_radius=10)
        pygame.draw.rect(screen, COLORS["BUTTON_HIGHLIGHT_BORDER"], hint_rect, 2, border_radius=10)
        screen.blit(hint_surface, hint_surface.get_rect(center=hint_rect.center))


def draw_progress_header(screen, title, instruction, steps, actor_color=None, is_ai=False):
    """Keep the active player and required next step visible above the board."""
    rect = pygame.Rect(LOG_PANEL_WIDTH + 34, 14, SIDE_PANEL_X - LOG_PANEL_WIDTH - 54, 88)
    shadow_rect = rect.move(3, 4)
    shadow = pygame.Surface(shadow_rect.size, pygame.SRCALPHA)
    pygame.draw.rect(shadow, (3, 7, 12, 115), shadow.get_rect(), border_radius=18)
    screen.blit(shadow, shadow_rect.topleft)

    surface = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(surface, (*COLORS["PANEL_BG"], 238), surface.get_rect(), border_radius=16)
    border_color = actor_color or COLORS["PANEL_BORDER"]
    pygame.draw.rect(surface, border_color, surface.get_rect(), 3, border_radius=16)
    screen.blit(surface, rect.topleft)

    title_font = _load_font(20)
    body_font = _load_font(14)
    title_surface = title_font.render(title, True, COLORS["WHITE"])
    screen.blit(title_surface, (rect.x + 18, rect.y + 10))

    if is_ai:
        badge_font = _load_font(12)
        badge_surface = badge_font.render("AI進行中", True, COLORS["WHITE"])
        badge_rect = pygame.Rect(0, 0, badge_surface.get_width() + 18, 22)
        badge_rect.topright = (rect.right - 14, rect.y + 10)
        pygame.draw.rect(screen, COLORS["BUTTON_ACTIVE"], badge_rect, border_radius=10)
        screen.blit(badge_surface, badge_surface.get_rect(center=badge_rect.center))

    instruction_text = _truncate_text(body_font, instruction, rect.width - 36)
    instruction_surface = body_font.render(instruction_text, True, COLORS["WARNING"])
    screen.blit(instruction_surface, (rect.x + 18, rect.y + 38))

    tracker_rect = pygame.Rect(rect.x + 18, rect.bottom - 24, rect.width - 36, 16)
    _draw_inline_phase_steps(screen, tracker_rect, steps)


def draw_replay_status_card(
    screen,
    rect,
    event_title,
    event_detail,
    frame_index,
    total_frames,
    *,
    is_playing=False,
    keyboard_hint="Space 再生/停止  ←/→ 前後  Home/End 端へ",
):
    """Draw a compact replay timeline card and return its measured layout.

    ``frame_index`` is zero-based.  A 298 x 122 rectangle fits the standard
    right-panel content width; wider rectangles can also replace the board
    progress header without changing the layout contract.
    """
    card_rect = pygame.Rect(rect)
    if card_rect.width < 240 or card_rect.height < 118:
        raise ValueError("Replay status card requires at least 240 x 118 pixels")
    card_rect.clamp_ip(screen.get_rect())

    total = max(0, int(total_frames))
    if total:
        clamped_index = max(0, min(int(frame_index), total - 1))
        frame_number = clamped_index + 1
        progress = clamped_index / (total - 1) if total > 1 else 1.0
    else:
        clamped_index = 0
        frame_number = 0
        progress = 0.0

    shadow_rect = card_rect.move(3, 4)
    shadow = pygame.Surface(shadow_rect.size, pygame.SRCALPHA)
    pygame.draw.rect(shadow, (4, 8, 14, 110), shadow.get_rect(), border_radius=16)
    screen.blit(shadow, shadow_rect.topleft)

    card_surface = pygame.Surface(card_rect.size, pygame.SRCALPHA)
    pygame.draw.rect(card_surface, (*COLORS["PANEL_BG"], 242), card_surface.get_rect(), border_radius=14)
    pygame.draw.rect(card_surface, COLORS["PANEL_BORDER"], card_surface.get_rect(), 2, border_radius=14)
    screen.blit(card_surface, card_rect.topleft)

    accent_color = COLORS["SUCCESS"] if is_playing else COLORS["WARNING"]
    accent_rect = pygame.Rect(card_rect.x + 2, card_rect.y + 12, 4, card_rect.height - 24)
    pygame.draw.rect(screen, accent_color, accent_rect, border_radius=2)

    status_font = _load_font(12)
    status_text = "▶ 再生中" if is_playing else "Ⅱ 一時停止"
    status_surface = status_font.render(status_text, True, COLORS["WHITE"])
    status_rect = pygame.Rect(0, 0, status_surface.get_width() + 18, 23)
    status_rect.topleft = (card_rect.x + 14, card_rect.y + 9)
    status_fill = (35, 91, 66) if is_playing else (76, 60, 34)
    pygame.draw.rect(screen, status_fill, status_rect, border_radius=10)
    pygame.draw.rect(screen, accent_color, status_rect, 1, border_radius=10)
    screen.blit(status_surface, status_surface.get_rect(center=status_rect.center))

    frame_font = _load_font(13)
    frame_text = f"{frame_number} / {total}"
    frame_surface = frame_font.render(frame_text, True, COLORS["WHITE"])
    frame_rect = frame_surface.get_rect(
        right=card_rect.right - 14,
        centery=status_rect.centery,
    )
    screen.blit(frame_surface, frame_rect)

    hint_font = _load_font(11)
    detail_font = _load_font(13)
    title_font = _load_font(16)
    hint_y = card_rect.bottom - hint_font.get_height() - 7
    progress_rect = pygame.Rect(card_rect.x + 14, hint_y - 14, card_rect.width - 28, 8)
    detail_y = progress_rect.top - detail_font.get_height() - 6
    title_y = detail_y - title_font.get_height() - 1

    title_text = _truncate_text(
        title_font,
        event_title or "イベントなし",
        card_rect.width - 28,
    )
    detail_text = _truncate_text(
        detail_font,
        event_detail or "記録された詳細はありません",
        card_rect.width - 28,
    )
    title_surface = title_font.render(title_text, True, COLORS["WHITE"])
    detail_surface = detail_font.render(detail_text, True, COLORS["TEXT_MUTED"])
    title_rect = title_surface.get_rect(x=card_rect.x + 14, y=title_y)
    detail_rect = detail_surface.get_rect(x=card_rect.x + 14, y=detail_y)
    screen.blit(title_surface, title_rect)
    screen.blit(detail_surface, detail_rect)

    pygame.draw.rect(screen, (35, 47, 59), progress_rect, border_radius=4)
    pygame.draw.rect(screen, COLORS["CARD_BORDER"], progress_rect, 1, border_radius=4)
    progress_width = round(progress_rect.width * progress)
    progress_fill_rect = pygame.Rect(
        progress_rect.x,
        progress_rect.y,
        progress_width,
        progress_rect.height,
    )
    if progress_fill_rect.width:
        pygame.draw.rect(screen, COLORS["BUTTON_ACTIVE"], progress_fill_rect, border_radius=4)
        if progress_fill_rect.width >= 5:
            pygame.draw.line(
                screen,
                (139, 190, 231),
                (progress_fill_rect.left + 2, progress_fill_rect.top + 2),
                (progress_fill_rect.right - 2, progress_fill_rect.top + 2),
                1,
            )
    scrubber_x = round(progress_rect.left + (progress_rect.width - 1) * progress)
    scrubber_center = (scrubber_x, progress_rect.centery)
    pygame.draw.circle(screen, (19, 27, 37), scrubber_center, 5)
    pygame.draw.circle(screen, accent_color, scrubber_center, 4)
    pygame.draw.circle(screen, COLORS["WHITE"], scrubber_center, 4, 1)

    hint_text = _truncate_text(hint_font, keyboard_hint, card_rect.width - 28)
    hint_surface = hint_font.render(hint_text, True, COLORS["WARNING"])
    hint_rect = hint_surface.get_rect(x=card_rect.x + 14, y=hint_y)
    screen.blit(hint_surface, hint_rect)

    return {
        "card_rect": card_rect,
        "accent_rect": accent_rect,
        "status_rect": status_rect,
        "frame_rect": frame_rect,
        "title_rect": title_rect,
        "detail_rect": detail_rect,
        "progress_rect": progress_rect,
        "progress_fill_rect": progress_fill_rect,
        "scrubber_center": scrubber_center,
        "hint_rect": hint_rect,
        "frame_index": clamped_index,
        "frame_number": frame_number,
        "total_frames": total,
        "progress": progress,
        "is_playing": bool(is_playing),
        "title_text": title_text,
        "detail_text": detail_text,
        "hint_text": hint_text,
    }


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

        state_prefix = "✓" if step.state == "complete" else "▶" if step.state == "active" else "·"
        label_surface = step_font.render(f"{state_prefix} {step.label}", True, COLORS["WHITE"])
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


def draw_transient_message(screen, message):
    if message is None:
        return

    level_styles = {
        "info": ((31, 47, 69), COLORS["PANEL_BORDER"]),
        "warning": ((68, 53, 29), COLORS["WARNING"]),
        "error": ((82, 36, 36), COLORS["DANGER"]),
        "success": ((30, 64, 48), COLORS["SUCCESS"]),
    }
    background_color, border_color = level_styles.get(message.level, level_styles["info"])

    font = _load_font(18)
    max_width = 340
    wrapped_lines = _wrap_text(font, message.text, max_width)
    content_width = max(font.size(line)[0] for line in wrapped_lines)
    content_height = len(wrapped_lines) * (font.get_height() + 4) - 4
    rect = pygame.Rect(0, 0, content_width + 36, content_height + 24)
    board_area_left = HELP_PANEL_X + LOG_PANEL_WIDTH + 12
    board_area_right = SIDE_PANEL_X - 12
    rect.centerx = int((board_area_left + board_area_right) / 2)
    rect.y = 112

    toast_surface = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(toast_surface, (*background_color, 232), toast_surface.get_rect(), border_radius=16)
    pygame.draw.rect(toast_surface, border_color, toast_surface.get_rect(), 2, border_radius=16)
    screen.blit(toast_surface, rect.topleft)

    text_y = rect.y + 12
    for line in wrapped_lines:
        line_surface = font.render(line, True, COLORS["WHITE"])
        line_rect = line_surface.get_rect(centerx=rect.centerx, y=text_y)
        screen.blit(line_surface, line_rect)
        text_y += font.get_height() + 4


def _get_button_colors(button):
    if not button.enabled:
        return COLORS["BUTTON_DISABLED"], (80, 91, 102), (150, 160, 170)
    if button.selected:
        return COLORS["BUTTON_ACTIVE"], COLORS["BUTTON_HIGHLIGHT_BORDER"], COLORS["BUTTON_TEXT"]
    if button.highlighted:
        return COLORS["BUTTON_HIGHLIGHT"], COLORS["BUTTON_HIGHLIGHT_BORDER"], COLORS["BUTTON_TEXT"]
    return COLORS["BUTTON"], COLORS["PANEL_BORDER"], COLORS["BUTTON_TEXT"]


def draw_button(screen, button):
    color, border_color, text_color = _get_button_colors(button)
    if button.enabled and (button.selected or button.highlighted):
        glow_rect = button.rect.inflate(8, 8)
        glow = pygame.Surface(glow_rect.size, pygame.SRCALPHA)
        pygame.draw.rect(glow, (*COLORS["BUTTON_HIGHLIGHT_BORDER"], 42), glow.get_rect(), border_radius=16)
        screen.blit(glow, glow_rect.topleft)
    pygame.draw.rect(screen, color, button.rect, border_radius=12)
    pygame.draw.rect(screen, border_color, button.rect, 2, border_radius=12)
    if button.action == "seed_input_focus":
        font = _load_font(17)
        hint_font = _load_font(12)
        text_surface = font.render(button.label, True, text_color)
        screen.blit(
            text_surface,
            (button.rect.x + 14, button.rect.y + (button.rect.height - text_surface.get_height()) // 2),
        )
        hint_text = "入力中" if button.selected else "クリックで編集"
        hint_color = COLORS["WARNING"] if button.selected else COLORS["TEXT_MUTED"]
        hint_surface = hint_font.render(hint_text, True, hint_color)
        screen.blit(
            hint_surface,
            (
                button.rect.right - hint_surface.get_width() - 12,
                button.rect.y + (button.rect.height - hint_surface.get_height()) // 2,
            ),
        )
        return

    font = _load_font(17)
    text_surface = font.render(button.label, True, text_color)
    screen.blit(text_surface, text_surface.get_rect(center=button.rect.center))


def draw_side_panel(
    screen,
    title,
    subtitle,
    phase_steps,
    guidance_lines,
    current_player,
    players,
    buttons,
    points_by_player,
    breakdown_by_player,
    trade_rates,
    development_summary,
    deck_remaining,
    affordability,
    setup_summary=None,
    bank_resources=None,
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

    if guidance_lines:
        guide_rect = pygame.Rect(panel_rect.x + 14, tracker_y, panel_rect.width - 28, 78)
        guide_surface = pygame.Surface(guide_rect.size, pygame.SRCALPHA)
        pygame.draw.rect(guide_surface, (35, 48, 62, 232), guide_surface.get_rect(), border_radius=12)
        pygame.draw.rect(guide_surface, COLORS["BUTTON_HIGHLIGHT_BORDER"], guide_surface.get_rect(), 2, border_radius=12)
        screen.blit(guide_surface, guide_rect.topleft)

        guide_title = small_font.render("今やること", True, COLORS["WARNING"])
        screen.blit(guide_title, (guide_rect.x + 12, guide_rect.y + 7))
        guide_y = guide_rect.y + 29

        primary_line = guidance_lines[0]
        color = COLORS["WHITE"]
        if any(keyword in primary_line for keyword in ("不可", "不足", "ません")):
            color = COLORS["DANGER"]
        primary_text = _truncate_text(small_font, primary_line, guide_rect.width - 24)
        line_surface = small_font.render(primary_text, True, color)
        screen.blit(line_surface, (guide_rect.x + 12, guide_y))
        guide_y += small_font.get_height() + 1

        if len(guidance_lines) > 1:
            secondary_font = _load_font(13)
            secondary_text = _truncate_text(
                secondary_font,
                guidance_lines[1],
                guide_rect.width - 24,
            )
            secondary_surface = secondary_font.render(secondary_text, True, COLORS["TEXT_MUTED"])
            screen.blit(secondary_surface, (guide_rect.x + 12, guide_y))

    for button in buttons:
        draw_button(screen, button)

    buttons_bottom = max((button.rect.bottom for button in buttons), default=tracker_y + 86)
    section_y = max(tracker_y + 86, buttons_bottom + 16)

    if setup_summary:
        summary_title = section_font.render(setup_summary["title"], True, COLORS["WHITE"])
        screen.blit(summary_title, (panel_rect.x + 16, section_y))
        section_y += summary_title.get_height() + 6

        for label, value in setup_summary["rows"]:
            label_surface = small_font.render(label, True, COLORS["TEXT_MUTED"])
            screen.blit(label_surface, (panel_rect.x + 20, section_y))

            value_lines = _wrap_text(preview_font, value, panel_rect.width - 140)
            value_y = section_y - 1
            for value_line in value_lines:
                value_surface = preview_font.render(value_line, True, COLORS["WHITE"])
                screen.blit(value_surface, (panel_rect.x + 126, value_y))
                value_y += preview_font.get_height() + 2
            section_y = max(section_y + label_surface.get_height(), value_y) + 4

        description_lines = _wrap_text(small_font, setup_summary["description"], panel_rect.width - 36)
        for description_line in description_lines:
            description_surface = small_font.render(description_line, True, COLORS["TEXT_MUTED"])
            screen.blit(description_surface, (panel_rect.x + 20, section_y))
            section_y += small_font.get_height() + 3

        accent_lines = _wrap_text(small_font, setup_summary["accent"], panel_rect.width - 36)
        for accent_line in accent_lines:
            accent_surface = small_font.render(accent_line, True, COLORS["WARNING"])
            screen.blit(accent_surface, (panel_rect.x + 20, section_y))
            section_y += small_font.get_height() + 3
        section_y += 6

    if current_player is not None:
        name_height = _draw_player_name(screen, section_font, panel_rect.x + 20, section_y, current_player)
        section_y += name_height + 4

        stat_chips = _draw_player_stat_chips(
            screen,
            current_player,
            panel_rect.x + 20,
            section_y,
            panel_rect.width - 40,
        )
        section_y += max((rect.height for _, rect in stat_chips), default=0) + 4

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

        if bank_resources:
            bank_font = _load_font(13)
            bank_text = "銀行: " + " ".join(
                f"{RESOURCE_LABELS[resource_type]}{bank_resources.get(resource_type, 0)}"
                for resource_type in (
                    ResourceType.WOOD,
                    ResourceType.SHEEP,
                    ResourceType.WHEAT,
                    ResourceType.BRICK,
                    ResourceType.ORE,
                )
            )
            bank_surface = bank_font.render(bank_text, True, (205, 218, 229))
            screen.blit(bank_surface, (panel_rect.x + 20, section_y))
            section_y += bank_font.get_height() + 2

        if development_summary:
            deck_text = f"発展: {development_summary} / 山札{deck_remaining}"
        else:
            deck_text = f"発展山札: {deck_remaining} 枚"
        deck_font = _load_font(13) if development_summary else small_font
        deck_surface = deck_font.render(deck_text, True, COLORS["TEXT_MUTED"])
        screen.blit(deck_surface, (panel_rect.x + 20, section_y))
        section_y += deck_font.get_height() + 8

    if affordability:
        build_title = section_font.render("建設プレビュー", True, COLORS["WHITE"])
        screen.blit(build_title, (panel_rect.x + 16, section_y))
        section_y += build_title.get_height() + 6

        compact_preview = panel_rect.bottom - section_y < 120
        if compact_preview:
            compact_font = _load_font(13)
            cell_gap = 10
            cell_width = (panel_rect.width - 50 - cell_gap) // 2
            row_height = 36
            for index, item in enumerate(affordability):
                col = index % 2
                row = index // 2
                cell_x = panel_rect.x + 20 + col * (cell_width + cell_gap)
                cell_y = section_y + row * row_height
                status_color = COLORS["SUCCESS"] if item["available"] else COLORS["DANGER"]
                label_surface = compact_font.render(item["label"], True, COLORS["WHITE"])
                screen.blit(label_surface, (cell_x, cell_y))
                detail = _truncate_text(compact_font, item["detail"], cell_width)
                detail_surface = compact_font.render(detail, True, status_color)
                screen.blit(detail_surface, (cell_x, cell_y + compact_font.get_height() + 1))
        else:
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
