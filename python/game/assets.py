from functools import lru_cache
from pathlib import Path

import pygame


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FONT_PATH = (PROJECT_ROOT / "Noto_Sans_JP" / "NotoSansJP-VariableFont_wght.ttf").resolve()
_display_surface = None
_display_generation = 0


def _display_session_key():
    """Keep cached pygame objects scoped to the active display session."""
    global _display_generation, _display_surface
    surface = pygame.display.get_surface()
    # Object ids can be reused after ``pygame.quit()``.  Retaining the last
    # surface and advancing a generation on identity changes prevents a stale
    # Font object from leaking into the next display session.
    if surface is not _display_surface:
        _display_surface = surface
        _display_generation += 1
    return _display_generation


@lru_cache(maxsize=96)
def _load_font(size, display_session_key, bold=False):
    del display_session_key
    try:
        font = pygame.font.Font(str(FONT_PATH), size)
    except Exception:
        font = pygame.font.Font(None, size)
    if bold and hasattr(font, "set_bold"):
        font.set_bold(True)
    return font


def get_font(size, *, bold=False):
    """Return one shared font per size and weight for the active display session."""
    return _load_font(int(size), _display_session_key(), bool(bold))


def clear_font_cache():
    """Release cached fonts, primarily for pygame lifecycle boundaries and tests."""
    _load_font.cache_clear()
