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
BACKGROUND_THRESHOLD = 245
RESOURCE_TILE_ORDER = [
    ResourceType.BRICK,
    ResourceType.WOOD,
    ResourceType.SHEEP,
    ResourceType.WHEAT,
    ResourceType.ORE,
    ResourceType.DESERT,
]


def _is_foreground(color):
    return min(color[:3]) < BACKGROUND_THRESHOLD


def _find_segments(length, has_foreground_at):
    segments = []
    start = None
    for index in range(length):
        has_foreground = has_foreground_at(index)
        if has_foreground and start is None:
            start = index
        elif not has_foreground and start is not None:
            segments.append((start, index - 1))
            start = None
    if start is not None:
        segments.append((start, length - 1))
    return segments


def _find_foreground_bbox(surface, left, right, top, bottom):
    min_x = None
    min_y = None
    max_x = None
    max_y = None

    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            if _is_foreground(surface.get_at((x, y))):
                min_x = x if min_x is None else min(min_x, x)
                min_y = y if min_y is None else min(min_y, y)
                max_x = x if max_x is None else max(max_x, x)
                max_y = y if max_y is None else max(max_y, y)

    if min_x is None:
        return None

    return pygame.Rect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


@lru_cache(maxsize=1)
def _load_source_tiles():
    sheet = pygame.image.load(str(MATERIAL_SHEET_PATH)).convert_alpha()
    width, height = sheet.get_size()

    row_segments = _find_segments(
        height,
        lambda y: any(_is_foreground(sheet.get_at((x, y))) for x in range(width)),
    )
    col_segments = _find_segments(
        width,
        lambda x: any(_is_foreground(sheet.get_at((x, y))) for y in range(height)),
    )

    extracted_tiles = {}
    tile_index = 0
    for top, bottom in row_segments:
        for left, right in col_segments:
            if tile_index >= len(RESOURCE_TILE_ORDER):
                break
            bbox = _find_foreground_bbox(sheet, left, right, top, bottom)
            if bbox is None:
                continue
            extracted_tiles[RESOURCE_TILE_ORDER[tile_index]] = sheet.subsurface(bbox).copy()
            tile_index += 1

    return extracted_tiles


@lru_cache(maxsize=None)
def get_tile_surface(resource_type, radius=HEX_RADIUS):
    source_tiles = _load_source_tiles()
    source_surface = source_tiles.get(resource_type)
    if source_surface is None:
        return None

    width = math.ceil(math.sqrt(3) * radius) + 2
    height = radius * 2 + 2
    scaled_surface = pygame.transform.smoothscale(source_surface, (width, height))

    masked_surface = pygame.Surface((width, height), pygame.SRCALPHA)
    masked_surface.blit(scaled_surface, (0, 0))

    mask = pygame.Surface((width, height), pygame.SRCALPHA)
    center_x = (width - 1) / 2
    center_y = (height - 1) / 2
    vertices = []
    for index in range(6):
        angle = math.radians(60 * index - 30)
        vertices.append(
            (
                round(center_x + radius * math.cos(angle)),
                round(center_y + radius * math.sin(angle)),
            )
        )
    pygame.draw.polygon(mask, (255, 255, 255, 255), vertices)
    masked_surface.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return masked_surface
