import pygame
import math
import random
from enum import Enum

# 画面サイズなどの定数
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 600
HEX_RADIUS = 50
WINDOW_TITLE = "カタン風ゲーム"

# 色の定義
COLORS = {
    "BLACK": (0, 0, 0),
    "WHITE": (255, 255, 255),
    "RED": (255, 0, 0),
    "GREEN": (0, 255, 0),
    "BLUE": (0, 0, 255),
    "YELLOW": (255, 255, 0),
    "BROWN": (139, 69, 19),
    "ORANGE": (255, 165, 0),
    "GRAY": (128, 128, 128),
    "WHEAT": (245, 222, 179)
}
