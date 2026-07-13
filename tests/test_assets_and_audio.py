import os
from types import SimpleNamespace


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from game import assets
from game import audio as audio_module
from game import dice_animation, log_display, ui
from game.assets import FONT_PATH, clear_font_cache, get_font
from game.audio import GameAudio


def test_font_path_is_absolute_and_exists():
    assert FONT_PATH.is_absolute()
    assert FONT_PATH.is_file()


def test_fonts_are_cached_by_size(monkeypatch):
    clear_font_cache()
    calls = []

    def counting_font(path, size):
        calls.append((path, size))
        return object()

    monkeypatch.setattr(assets.pygame, "font", SimpleNamespace(Font=counting_font))
    monkeypatch.setattr(assets, "_display_session_key", lambda: "test-session")
    try:
        first = ui._load_font(18)
        second = log_display._load_font(18)
        dice_font = dice_animation._load_font(18)
        other_size = get_font(24)

        assert first is second
        assert dice_font is first
        assert other_size is not first
        assert calls == [(str(FONT_PATH), 18), (str(FONT_PATH), 24)]
    finally:
        clear_font_cache()


def test_font_cache_does_not_cross_pygame_display_sessions():
    clear_font_cache()
    pygame = assets.pygame
    try:
        pygame.init()
        pygame.display.set_mode((1, 1))
        first = get_font(17)
        first.render("性格", True, (255, 255, 255))
        pygame.quit()

        pygame.init()
        pygame.display.set_mode((1, 1))
        second = get_font(17)
        rendered = second.render("性格", True, (255, 255, 255))

        assert second is not first
        assert rendered.get_width() > 0
    finally:
        pygame.quit()
        clear_font_cache()


def test_audio_falls_back_to_silence_when_mixer_is_unavailable(monkeypatch):
    def unavailable():
        raise NotImplementedError("mixer module not available")

    monkeypatch.setattr(audio_module.pygame, "mixer", SimpleNamespace(get_init=unavailable))

    audio = GameAudio()

    assert audio.enabled is False
    assert audio.bgm_channel is None
    assert audio.sounds == {}
    audio.start_bgm()
    audio.play("dice")
    audio.stop()
