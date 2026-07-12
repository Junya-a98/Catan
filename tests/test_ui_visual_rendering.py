import pygame

from game.constants import COLORS
from game.player import Player
from game.resources import ResourceType
from game.ui import UIButton, _draw_player_stat_chips, _get_button_colors, draw_button


def test_disabled_button_uses_muted_border_and_text_colors():
    disabled = UIButton("disabled", "建設できません", pygame.Rect(10, 10, 180, 42), enabled=False)
    enabled = UIButton("enabled", "建設できます", pygame.Rect(10, 10, 180, 42))

    disabled_fill, disabled_border, disabled_text = _get_button_colors(disabled)
    enabled_fill, enabled_border, enabled_text = _get_button_colors(enabled)

    assert disabled_fill == COLORS["BUTTON_DISABLED"]
    assert disabled_border != COLORS["PANEL_BORDER"]
    assert sum(disabled_text) < sum(enabled_text)
    assert (enabled_fill, enabled_border, enabled_text) == (
        COLORS["BUTTON"],
        COLORS["PANEL_BORDER"],
        COLORS["BUTTON_TEXT"],
    )


def test_disabled_button_pixels_are_visibly_dimmer_than_enabled_button():
    pygame.font.init()
    disabled_surface = pygame.Surface((200, 62))
    enabled_surface = pygame.Surface((200, 62))
    rect = pygame.Rect(10, 10, 180, 42)

    draw_button(disabled_surface, UIButton("disabled", "街道 木+土", rect, enabled=False))
    draw_button(enabled_surface, UIButton("enabled", "街道 木+土", rect))

    disabled_peak = max(max(disabled_surface.get_at((x, y))[:3]) for x in range(200) for y in range(62))
    enabled_peak = max(max(enabled_surface.get_at((x, y))[:3]) for x in range(200) for y in range(62))
    assert disabled_peak <= 170
    assert enabled_peak >= 240


def test_player_stat_chips_fit_the_side_panel_and_use_full_piece_names():
    pygame.font.init()
    surface = pygame.Surface((326, 60), pygame.SRCALPHA)
    player = Player("Player1", (215, 72, 61))
    player.resources[ResourceType.WOOD] = 12
    player.roads_remaining = 15
    player.settlements_remaining = 5
    player.cities_remaining = 4

    chips = _draw_player_stat_chips(surface, player, 0, 4, 286)
    labels = [label for label, _ in chips]
    rects = [rect for _, rect in chips]

    assert len(rects) == 4
    assert labels == ["手札 12", "街道 15", "開拓地 5", "都市 4"]
    assert rects[0].left == 0
    assert rects[-1].right <= 286
    assert all(first.right < second.left for first, second in zip(rects, rects[1:]))
    assert all(surface.get_at(rect.center).a > 0 for rect in rects)
