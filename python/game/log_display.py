import pygame

# フォントファイルのパス
font_path = "Noto_Sans_JP/NotoSansJP-VariableFont_wght.ttf"

def draw_log(screen, log_messages):
    font_size = 20
    try:
        font = pygame.font.Font(font_path, font_size)
    except Exception as e:
        print("フォント読み込み失敗:", e)
        font = pygame.font.Font(None, font_size)
    y = 10  # 描画開始の Y 座標
    for message in log_messages:
        text_surface = font.render(message, True, (255, 255, 255))
        screen.blit(text_surface, (10, y))
        y += text_surface.get_height() + 5

def draw_resource_counts(screen, players):
    try:
        font = pygame.font.Font(font_path, 24)
    except Exception as e:
        print("フォント読み込み失敗:", e)
        font = pygame.font.Font(None, 24)
    margin = 10
    line_height = 24 + 5
    start_y = screen.get_height() - margin - (line_height * len(players))
    for player in players:
        resource_str = ", ".join([f"{res.name}:{count}" for res, count in player.resources.items()])
        text = f"{player.name}: {resource_str}"
        text_surface = font.render(text, True, (255, 255, 255))
        x = screen.get_width() - margin - text_surface.get_width()
        screen.blit(text_surface, (x, start_y))
        start_y += line_height

def draw_current_turn(screen, players, current_player_index):
    try:
        font = pygame.font.Font(font_path, 24)
    except Exception as e:
        print("フォント読み込み失敗:", e)
        font = pygame.font.Font(None, 24)
    current_player = players[current_player_index]
    text = f"手番: {current_player.name}"
    text_surface = font.render(text, True, (255, 255, 0))  # 黄色で表示
    x = screen.get_width() - text_surface.get_width() - 10
    y = 10
    screen.blit(text_surface, (x, y))
