import pygame
import pytest

from game.constants import SIDE_PANEL_WIDTH, SIDE_PANEL_X
from game.ui import draw_replay_status_card


@pytest.fixture(autouse=True)
def pygame_display():
    pygame.init()
    pygame.display.set_mode((1, 1))
    yield
    pygame.quit()


def test_replay_card_fits_the_standard_right_panel_content_width():
    surface = pygame.Surface((1200, 800), pygame.SRCALPHA)
    card_rect = pygame.Rect(SIDE_PANEL_X + 14, 118, SIDE_PANEL_WIDTH - 28, 122)

    layout = draw_replay_status_card(
        surface,
        card_rect,
        "Player1が港に接続する開拓地を建設して最長交易路を更新しました",
        "木 -1 / 土 -1 / 羊 -1 / 麦 -1 / とても長い補足説明",
        4,
        10,
        is_playing=False,
    )

    assert layout["card_rect"] == card_rect
    assert layout["frame_number"] == 5
    assert layout["total_frames"] == 10
    assert layout["title_text"].endswith("…")
    for key in (
        "accent_rect",
        "status_rect",
        "frame_rect",
        "title_rect",
        "detail_rect",
        "progress_rect",
        "hint_rect",
    ):
        assert card_rect.contains(layout[key])
    assert "Space" in layout["hint_text"]
    assert surface.get_at(card_rect.center).a > 0


def test_replay_progress_clamps_to_first_and_last_frame():
    card_rect = pygame.Rect(380, 14, 460, 122)
    first_surface = pygame.Surface((1200, 800), pygame.SRCALPHA)
    last_surface = pygame.Surface((1200, 800), pygame.SRCALPHA)

    first = draw_replay_status_card(
        first_surface,
        card_rect,
        "開始",
        "最初の状態",
        -20,
        8,
    )
    last = draw_replay_status_card(
        last_surface,
        card_rect,
        "終了",
        "最後の状態",
        200,
        8,
        is_playing=True,
    )

    assert (first["frame_index"], first["frame_number"], first["progress"]) == (0, 1, 0.0)
    assert first["progress_fill_rect"].width == 0
    assert first["scrubber_center"][0] == first["progress_rect"].left
    assert (last["frame_index"], last["frame_number"], last["progress"]) == (7, 8, 1.0)
    assert last["progress_fill_rect"].width == last["progress_rect"].width
    assert last["scrubber_center"][0] == last["progress_rect"].right - 1


def test_replay_play_and_pause_states_have_distinct_status_pixels():
    card_rect = pygame.Rect(380, 14, 460, 122)
    paused_surface = pygame.Surface((1200, 800), pygame.SRCALPHA)
    playing_surface = pygame.Surface((1200, 800), pygame.SRCALPHA)

    paused = draw_replay_status_card(
        paused_surface,
        card_rect,
        "交易成立",
        "Player1とPlayer2が交換",
        2,
        6,
        is_playing=False,
    )
    playing = draw_replay_status_card(
        playing_surface,
        card_rect,
        "交易成立",
        "Player1とPlayer2が交換",
        2,
        6,
        is_playing=True,
    )

    sample = (paused["accent_rect"].centerx, paused["accent_rect"].centery)
    assert paused_surface.get_at(sample) != playing_surface.get_at(sample)
    assert paused["is_playing"] is False
    assert playing["is_playing"] is True


def test_replay_card_rejects_a_rect_too_small_for_all_required_information():
    surface = pygame.Surface((1200, 800), pygame.SRCALPHA)

    with pytest.raises(ValueError, match="240 x 118"):
        draw_replay_status_card(surface, pygame.Rect(10, 10, 220, 90), "event", "detail", 0, 1)
