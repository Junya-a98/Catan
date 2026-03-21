import pygame

# フォントファイルのパス
font_path = "Noto_Sans_JP/NotoSansJP-VariableFont_wght.ttf"


def _load_font(size):
    try:
        return pygame.font.Font(font_path, size)
    except Exception as e:
        print("フォント読み込み失敗:", e)
        return pygame.font.Font(None, size)


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
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [message]


def draw_log(screen, log_messages):
    title_font = _load_font(24)
    log_font = _load_font(18)

    panel_rect = pygame.Rect(12, 12, 420, 340)
    panel_surface = pygame.Surface(panel_rect.size, pygame.SRCALPHA)
    pygame.draw.rect(panel_surface, (12, 18, 28, 215), panel_surface.get_rect(), border_radius=18)
    pygame.draw.rect(panel_surface, (115, 150, 190, 235), panel_surface.get_rect(), 2, border_radius=18)
    screen.blit(panel_surface, panel_rect.topleft)

    title_surface = title_font.render("イベントログ", True, (255, 255, 255))
    screen.blit(title_surface, (panel_rect.x + 18, panel_rect.y + 14))
    pygame.draw.line(
        screen,
        (90, 120, 155),
        (panel_rect.x + 18, panel_rect.y + 48),
        (panel_rect.right - 18, panel_rect.y + 48),
        1,
    )

    content_width = panel_rect.width - 36
    bottom_y = panel_rect.bottom - 16
    line_gap = 4
    visible_entries = list(log_messages[-24:])

    for entry_index, message in enumerate(reversed(visible_entries)):
        message_lines = _wrap_message(log_font, f"・{message}", content_width)
        message_height = len(message_lines) * (log_font.get_height() + line_gap)
        bottom_y -= message_height
        if bottom_y < panel_rect.y + 58:
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
):
    font = _load_font(24)
    margin = 10
    line_height = 24 + 5
    start_y = screen.get_height() - margin - (line_height * len(players))
    for player in players:
        resource_str = ", ".join([f"{res.name}:{count}" for res, count in player.resources.items()])
        extras = []
        if points_by_player is not None:
            extras.append(f"VP:{points_by_player.get(player.name, 0)}")
        if longest_road_owner is not None and longest_road_owner.name == player.name:
            extras.append("最長交易路")
        if largest_army_owner is not None and largest_army_owner.name == player.name:
            extras.append("最大騎士力")
        suffix = f" | {' '.join(extras)}" if extras else ""
        text = f"{player.name}: {resource_str}{suffix}"
        text_surface = font.render(text, True, (255, 255, 255))
        x = screen.get_width() - margin - text_surface.get_width()
        screen.blit(text_surface, (x, start_y))
        start_y += line_height


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
