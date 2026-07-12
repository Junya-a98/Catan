import math
from functools import lru_cache
from pathlib import Path

import pygame

from game.constants import HEX_RADIUS
from game.resources import ResourceType


MATERIAL_SHEET_PATH = (
    Path(__file__).resolve().parents[2]
    / "material"
    / "ChatGPT Image 2026年3月21日 16_16_06.png"
)

# The bundled sheet is a fixed 2 x 3 layout.  These crop rectangles were
# measured once from that exact file so normal startup never scans its
# 1.5 million pixels.  The sixth cell is another wheat field, not a desert,
# and is intentionally excluded.
EXPECTED_SHEET_SIZE = (1024, 1536)
RESOURCE_TILE_RECTS = {
    ResourceType.BRICK: (81, 166, 355, 388),
    ResourceType.WOOD: (588, 165, 355, 389),
    ResourceType.SHEEP: (81, 589, 354, 384),
    ResourceType.WHEAT: (588, 588, 354, 385),
    ResourceType.ORE: (81, 1005, 355, 381),
}

DESERT_TOP = (239, 210, 151)
DESERT_BOTTOM = (205, 157, 91)
DESERT_DUNE = (173, 116, 60)
DESERT_HIGHLIGHT = (250, 226, 170)


def _surface_size(radius):
    return math.ceil(math.sqrt(3) * radius) + 2, radius * 2 + 2


def _hex_vertices(width, height, radius):
    center_x = (width - 1) / 2
    center_y = (height - 1) / 2
    return [
        (
            round(center_x + radius * math.cos(math.radians(60 * index - 30))),
            round(center_y + radius * math.sin(math.radians(60 * index - 30))),
        )
        for index in range(6)
    ]


def _mask_to_hex(surface, radius):
    mask = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
    pygame.draw.polygon(mask, (255, 255, 255, 255), _hex_vertices(*surface.get_size(), radius))
    surface.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return surface


@lru_cache(maxsize=1)
def _load_source_tiles():
    """Load the five valid resource illustrations from the bundled sheet."""
    try:
        sheet = pygame.image.load(str(MATERIAL_SHEET_PATH))
        try:
            sheet = sheet.convert_alpha()
        except pygame.error:
            # Unit tests and headless utilities may not have opened a display.
            sheet = sheet.copy()
    except (OSError, pygame.error):
        return {}

    if sheet.get_size() != EXPECTED_SHEET_SIZE:
        # A changed or corrupt sheet must not silently map the wrong art to a
        # resource.  HexTile will use its simple color fallback instead.
        return {}

    return {
        resource_type: sheet.subsurface(pygame.Rect(rect)).copy()
        for resource_type, rect in RESOURCE_TILE_RECTS.items()
    }


@lru_cache(maxsize=None)
def _build_desert_surface(radius):
    """Create deterministic sand-and-dune artwork without external assets."""
    width, height = _surface_size(radius)
    surface = pygame.Surface((width, height), pygame.SRCALPHA)

    # A short cached gradient gives the desert depth while staying inexpensive.
    for y in range(height):
        progress = y / max(1, height - 1)
        color = tuple(
            round(top + (bottom - top) * progress)
            for top, bottom in zip(DESERT_TOP, DESERT_BOTTOM)
        )
        pygame.draw.line(surface, color, (0, y), (width - 1, y))

    sun_center = (round(width * 0.29), round(height * 0.28))
    pygame.draw.circle(surface, (244, 194, 92), sun_center, max(3, radius // 9))
    pygame.draw.circle(surface, DESERT_HIGHLIGHT, sun_center, max(3, radius // 9), 1)

    dune_specs = (
        (0.53, 0.0, DESERT_HIGHLIGHT, 2),
        (0.65, 0.7, DESERT_DUNE, 2),
        (0.77, 1.5, (151, 96, 52), 1),
    )
    for y_ratio, phase, color, line_width in dune_specs:
        points = []
        for x in range(-4, width + 5, 3):
            wave = math.sin((x / max(1, width)) * math.pi * 1.35 + phase)
            y = round(height * y_ratio + wave * radius * 0.09)
            points.append((x, y))
        pygame.draw.lines(surface, color, False, points, line_width)

    # Small deterministic stones add texture without random state or downloads.
    for x_ratio, y_ratio, stone_radius in (
        (0.20, 0.69, 2),
        (0.27, 0.73, 1),
        (0.70, 0.36, 1),
        (0.75, 0.39, 2),
        (0.62, 0.82, 1),
        (0.80, 0.70, 1),
    ):
        center = (round(width * x_ratio), round(height * y_ratio))
        pygame.draw.circle(surface, (128, 91, 61), center, max(1, round(stone_radius * radius / 50)))
        pygame.draw.circle(surface, (225, 184, 113), (center[0] - 1, center[1] - 1), 1)

    return _mask_to_hex(surface, radius)


@lru_cache(maxsize=None)
def get_tile_surface(resource_type, radius=HEX_RADIUS):
    if resource_type == ResourceType.DESERT:
        return _build_desert_surface(radius)

    source_surface = _load_source_tiles().get(resource_type)
    if source_surface is None:
        return None

    width, height = _surface_size(radius)
    scaled_surface = pygame.transform.smoothscale(source_surface, (width, height))
    return _mask_to_hex(scaled_surface, radius)
