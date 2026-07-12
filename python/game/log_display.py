import pygame

from game.assets import get_font
from game.constants import COLORS, LOG_PANEL_HEIGHT, LOG_PANEL_WIDTH, SIDE_PANEL_X
from game.resources import ResourceType

RESOURCE_LABELS = {
    ResourceType.WOOD: "木",
    ResourceType.SHEEP: "羊",
    ResourceType.WHEAT: "麦",
    ResourceType.BRICK: "土",
    ResourceType.ORE: "鉄",
}

PROHIBITED_LINE_START = frozenset("、。，．・：；？！)]｝〕〉》」』】〙〗〟’”")


def _load_font(size):
    return get_font(size)


def _classify_log_color(message):
    if any(keyword in message for keyword in ("不足", "ありません", "できません", "選んでください", "置けません")):
        return (255, 160, 150)
    if any(keyword in message for keyword in ("勝利", "獲得", "建設", "配置", "アップグレード", "購入", "使用")):
        return (245, 235, 180)
    if any(keyword in message for keyword in ("フェーズ", "手番", "ダイス", "盗賊")):
        return (180, 225, 255)
    return (232, 236, 242)


def _wrap_message(font, message, max_width):
    lines = []
    current = ""
    for char in message:
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
    return lines or [message]


def draw_log(screen, log_messages, panel_height=LOG_PANEL_HEIGHT, latest_event=None):
    title_font = _load_font(24)
    log_font = _load_font(18)

    panel_rect = pygame.Rect(12, 12, LOG_PANEL_WIDTH, panel_height)
    panel_surface = pygame.Surface(panel_rect.size, pygame.SRCALPHA)
    pygame.draw.rect(panel_surface, (12, 18, 28, 215), panel_surface.get_rect(), border_radius=18)
    pygame.draw.rect(panel_surface, (115, 150, 190, 235), panel_surface.get_rect(), 2, border_radius=18)
    screen.blit(panel_surface, panel_rect.topleft)

    title_surface = title_font.render("イベント履歴", True, (255, 255, 255))
    screen.blit(title_surface, (panel_rect.x + 18, panel_rect.y + 14))
    pygame.draw.line(
        screen,
        (90, 120, 155),
        (panel_rect.x + 18, panel_rect.y + 48),
        (panel_rect.right - 18, panel_rect.y + 48),
        1,
    )

    content_width = panel_rect.width - 36
    content_top = panel_rect.y + 58
    if latest_event:
        card_height = 76
        card_rect = pygame.Rect(panel_rect.x + 14, content_top, panel_rect.width - 28, card_height)
        card_surface = pygame.Surface(card_rect.size, pygame.SRCALPHA)
        level = latest_event.get("level", "info")
        fill = {
            "success": (31, 68, 50),
            "warning": (72, 57, 30),
            "error": (78, 38, 38),
        }.get(level, (30, 48, 68))
        pygame.draw.rect(card_surface, (*fill, 238), card_surface.get_rect(), border_radius=12)
        pygame.draw.rect(card_surface, latest_event.get("color", (115, 150, 190)), card_surface.get_rect(), 2, border_radius=12)
        screen.blit(card_surface, card_rect.topleft)

        recent_font = _load_font(14)
        detail_font = _load_font(13)
        recent_surface = recent_font.render(latest_event.get("title", "直前の出来事"), True, (255, 255, 255))
        screen.blit(recent_surface, (card_rect.x + 12, card_rect.y + 8))
        detail_lines = _wrap_message(detail_font, latest_event.get("detail", ""), card_rect.width - 24)
        detail_y = card_rect.y + 34
        for line in detail_lines[:2]:
            detail_surface = detail_font.render(line, True, (220, 229, 238))
            screen.blit(detail_surface, (card_rect.x + 12, detail_y))
            detail_y += detail_font.get_height() + 2
        content_top = card_rect.bottom + 8

    bottom_y = panel_rect.bottom - 16
    line_gap = 4
    visible_entries = list(log_messages[-24:])

    for entry_index, message in enumerate(reversed(visible_entries)):
        message_lines = _wrap_message(log_font, f"・{message}", content_width)
        message_height = len(message_lines) * (log_font.get_height() + line_gap)
        bottom_y -= message_height
        if bottom_y < content_top:
            break

        color = _classify_log_color(message)
        if entry_index == 0:
            color = (255, 255, 255)

        y = bottom_y
        for line in message_lines:
            text_surface = log_font.render(line, True, color)
            screen.blit(text_surface, (panel_rect.x + 18, y))
            y += log_font.get_height() + line_gap
        bottom_y -= 6


def draw_resource_counts(
    screen,
    players,
    points_by_player=None,
    longest_road_owner=None,
    largest_army_owner=None,
    visible_player=None,
    reveal_all=False,
    current_player=None,
):
    if not players:
        return

    title_font = _load_font(18)
    body_font = _load_font(15)
    detail_font = _load_font(13)
    margin = 12
    card_gap = 10
    columns = len(players) if len(players) <= 3 else 2
    rows = (len(players) + columns - 1) // columns
    available_width = SIDE_PANEL_X - (margin * 2)
    card_width = int((available_width - card_gap * (columns - 1)) / columns)
    card_height = 96
    total_height = rows * card_height + (rows - 1) * card_gap
    start_y = screen.get_height() - total_height - margin

    for index, player in enumerate(players):
        row = index // columns
        col = index % columns
        card_x = margin + col * (card_width + card_gap)
        card_y = start_y + row * (card_height + card_gap)
        card_rect = pygame.Rect(card_x, card_y, card_width, card_height)

        card_surface = pygame.Surface(card_rect.size, pygame.SRCALPHA)
        pygame.draw.rect(card_surface, (*COLORS["CARD_BG"], 232), card_surface.get_rect(), border_radius=16)
        is_current = player is current_player
        border_color = player.color if is_current else COLORS["CARD_BORDER"]
        pygame.draw.rect(card_surface, border_color, card_surface.get_rect(), 4 if is_current else 2, border_radius=16)
        pygame.draw.rect(card_surface, player.color, pygame.Rect(0, 0, 8, card_rect.height), border_radius=16)
        screen.blit(card_surface, card_rect.topleft)

        if getattr(player, "is_ai", False):
            role_label = "CPU"
        elif player is visible_player:
            role_label = "あなた"
        else:
            role_label = "人間"
        marker = getattr(player, "marker", "●")
        name_label = f"{marker} {player.name}・{role_label}"
        name_surface = title_font.render(name_label, True, COLORS["WHITE"])
        screen.blit(name_surface, (card_rect.x + 18, card_rect.y + 10))

        vp_value = points_by_player.get(player.name, 0) if points_by_player is not None else 0
        vp_surface = title_font.render(f"VP {vp_value}", True, (255, 236, 178))
        screen.blit(vp_surface, (card_rect.right - vp_surface.get_width() - 16, card_rect.y + 10))

        show_resource_types = reveal_all or player is visible_player
        if show_resource_types:
            resource_text_top = "  ".join(
                f"{RESOURCE_LABELS[resource_type]} {player.resources[resource_type]}"
                for resource_type in (
                    ResourceType.WOOD,
                    ResourceType.SHEEP,
                    ResourceType.WHEAT,
                )
            )
            resource_text_bottom = "  ".join(
                f"{RESOURCE_LABELS[resource_type]} {player.resources[resource_type]}"
                for resource_type in (
                    ResourceType.BRICK,
                    ResourceType.ORE,
                )
            )
            resource_surface = body_font.render(resource_text_top, True, COLORS["TEXT_MUTED"])
            screen.blit(resource_surface, (card_rect.x + 18, card_rect.y + 36))
            secondary_surface = body_font.render(resource_text_bottom, True, COLORS["TEXT_MUTED"])
            screen.blit(secondary_surface, (card_rect.x + 18, card_rect.y + 56))
        else:
            hidden_text = f"手札 {player.total_resource_count()} 枚（内訳は非公開）"
            hidden_surface = body_font.render(hidden_text, True, COLORS["TEXT_MUTED"])
            screen.blit(hidden_surface, (card_rect.x + 18, card_rect.y + 43))

        badges = []
        if longest_road_owner is not None and longest_road_owner.name == player.name:
            badges.append("最長交易路")
        if largest_army_owner is not None and largest_army_owner.name == player.name:
            badges.append("最大騎士力")
        if is_current:
            badges.insert(0, "手番中")
        detail_text = " / ".join(badges) if badges else f"資源合計 {player.total_resource_count()} 枚"
        detail_surface = detail_font.render(detail_text, True, (255, 222, 160) if badges else (190, 205, 220))
        detail_y = card_rect.bottom - detail_surface.get_height() - 7
        screen.blit(detail_surface, (card_rect.right - detail_surface.get_width() - 16, detail_y))


def draw_current_turn(
    screen,
    players,
    current_player_index,
    action_mode=None,
    winner=None,
    special_phase=None,
    discard_player=None,
    discard_remaining=0,
    development_summary=None,
):
    font = _load_font(24)
    if winner is not None:
        text = f"勝者: {winner.name}"
    elif special_phase == "discard" and discard_player is not None:
        text = f"捨て札: {discard_player.name} / 残り {discard_remaining} 枚"
    elif special_phase == "move_robber":
        current_player = players[current_player_index]
        text = f"手番: {current_player.name} / 盗賊を移動"
    elif special_phase == "steal":
        current_player = players[current_player_index]
        text = f"手番: {current_player.name} / 略奪対象を選択"
    else:
        current_player = players[current_player_index]
        text = f"手番: {current_player.name}"
        if action_mode is not None:
            labels = {
                "road": "街道",
                "settlement": "開拓地",
                "city": "都市",
            }
            text += f" / モード: {labels.get(action_mode, action_mode)}"
    text_surface = font.render(text, True, (255, 255, 0))
    x = screen.get_width() - text_surface.get_width() - 10
    y = 10
    screen.blit(text_surface, (x, y))

    help_font = _load_font(18)
    if special_phase == "discard":
        help_text = "捨て札: 1=木 2=羊 3=麦 4=土 5=鉄"
    elif special_phase == "move_robber":
        help_text = "盗賊移動: 移動先の地形をクリック"
    elif special_phase == "steal":
        help_text = "略奪: 相手の建物をクリック"
    elif special_phase == "year_of_plenty":
        help_text = "収穫: 1=木 2=羊 3=麦 4=土 5=鉄"
    elif special_phase == "monopoly":
        help_text = "独占: 1=木 2=羊 3=麦 4=土 5=鉄"
    elif special_phase == "road_building":
        help_text = "街道建設: 辺をクリック / Escで終了"
    else:
        help_text = "Space:ダイス D:購入 K:騎士 B:街道建設 Y:収穫 M:独占 R/S/C:建設 Enter:終了"
    help_surface = help_font.render(help_text, True, (255, 255, 255))
    help_x = screen.get_width() - help_surface.get_width() - 10
    screen.blit(help_surface, (help_x, y + text_surface.get_height() + 4))

    if development_summary:
        dev_surface = help_font.render(f"発展: {development_summary}", True, (255, 255, 255))
        dev_x = screen.get_width() - dev_surface.get_width() - 10
        screen.blit(dev_surface, (dev_x, y + text_surface.get_height() + help_surface.get_height() + 8))
