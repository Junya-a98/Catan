"""Validated, immutable custom layouts for the standard 19-hex board.

The custom-map boundary deliberately stores game semantics rather than Pygame
coordinates or runtime object IDs.  A document therefore remains portable to
the LAN/Web renderers, while :class:`game.game_board.GameBoard` can continue to
derive its nodes and edges from the existing standard topology.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import random
import unicodedata
from typing import Any, Optional

from game.resources import ResourceType


__all__ = (
    "CUSTOM_MAP_FORMAT",
    "CUSTOM_MAP_TOPOLOGY",
    "CUSTOM_MAP_VERSION",
    "CustomMapError",
    "CustomMapSpec",
    "CustomTileSpec",
    "STANDARD_AXIAL_COORDS",
)


CUSTOM_MAP_FORMAT = "catan-custom-map"
CUSTOM_MAP_VERSION = 1
CUSTOM_MAP_TOPOLOGY = "standard-19"
MAX_MAP_NAME_LENGTH = 64

AXIAL_DIRECTIONS = (
    (1, 0),
    (1, -1),
    (0, -1),
    (-1, 0),
    (-1, 1),
    (0, 1),
)


def _standard_axial_coordinates() -> tuple[tuple[int, int], ...]:
    coordinates = []
    radius = 2
    for q in range(-radius, radius + 1):
        r_min = max(-radius, -q - radius)
        r_max = min(radius, -q + radius)
        for r in range(r_min, r_max + 1):
            coordinates.append((q, r))
    return tuple(
        sorted(coordinates, key=lambda coordinate: (coordinate[1], coordinate[0]))
    )


STANDARD_AXIAL_COORDS = _standard_axial_coordinates()
_STANDARD_AXIAL_SET = frozenset(STANDARD_AXIAL_COORDS)
_STANDARD_AXIAL_INDEX = {
    coordinate: index for index, coordinate in enumerate(STANDARD_AXIAL_COORDS)
}

_LAND_RESOURCES = (
    ResourceType.WOOD,
    ResourceType.SHEEP,
    ResourceType.WHEAT,
    ResourceType.BRICK,
    ResourceType.ORE,
)
_OFFICIAL_RESOURCES = (
    (ResourceType.WOOD,) * 4
    + (ResourceType.SHEEP,) * 4
    + (ResourceType.WHEAT,) * 4
    + (ResourceType.BRICK,) * 3
    + (ResourceType.ORE,) * 3
    + (ResourceType.DESERT,)
)
_OFFICIAL_NUMBERS = (2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12)
_OFFICIAL_HARBORS = (None, None, None, None, *_LAND_RESOURCES)
_VALID_NUMBERS = frozenset(_OFFICIAL_NUMBERS)

# ``GameBoard._create_harbors`` selects these nine coastal positions in this
# stable angular order.  Keeping the slot-to-tile relation semantic here lets
# balance warnings run without importing Pygame or constructing a board.
_HARBOR_ADJACENT_AXIALS = (
    (-2, 0),
    (0, -2),
    (1, -2),
    (2, -2),
    (2, 0),
    (1, 1),
    (0, 2),
    (-2, 2),
    (-2, 1),
)


class CustomMapError(ValueError):
    """Raised when a custom layout or operation is invalid."""


def _is_integer(value: Any) -> bool:
    return type(value) is int


def _validate_name(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_MAP_NAME_LENGTH
        or value != value.strip()
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise CustomMapError(
            f"マップ名は前後空白・制御文字を含まない1〜{MAX_MAP_NAME_LENGTH}文字で指定してください。"
        )
    return value


def _require_exact_keys(
    value: Any, expected: frozenset[str], *, label: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise CustomMapError(f"{label}はJSON objectで指定してください。")
    keys = frozenset(value)
    if keys != expected:
        missing = sorted(expected - keys)
        unknown = sorted(keys - expected)
        detail = []
        if missing:
            detail.append(f"不足: {', '.join(missing)}")
        if unknown:
            detail.append(f"未知: {', '.join(unknown)}")
        raise CustomMapError(f"{label}の項目が不正です（{' / '.join(detail)}）。")
    return value


def _parse_resource(
    value: Any, *, label: str, allow_none: bool = False
) -> Optional[ResourceType]:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or value not in ResourceType.__members__:
        raise CustomMapError(f"{label}の資源種類が不正です。")
    resource = ResourceType[value]
    if allow_none and resource is ResourceType.DESERT:
        raise CustomMapError(f"{label}に砂漠を港として指定できません。")
    return resource


def _coerce_axial(value: Any, *, label: str) -> tuple[int, int]:
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 2
        or not _is_integer(value[0])
        or not _is_integer(value[1])
    ):
        raise CustomMapError(f"{label}は(q, r)の整数座標で指定してください。")
    coordinate = (value[0], value[1])
    if coordinate not in _STANDARD_AXIAL_SET:
        raise CustomMapError(f"{label}が標準19タイル盤面の外です。")
    return coordinate


def _coerce_harbor_slot(value: Any, *, label: str) -> int:
    if not _is_integer(value) or not 0 <= value < len(_HARBOR_ADJACENT_AXIALS):
        raise CustomMapError(f"{label}は0〜8の整数で指定してください。")
    return value


def _shuffle(values: list[Any], rng: Any) -> None:
    source = random.Random() if rng is None else rng
    shuffle = getattr(source, "shuffle", None)
    if not callable(shuffle):
        raise TypeError("rng must provide a callable shuffle method")
    shuffle(values)


@dataclass(frozen=True)
class CustomTileSpec:
    """One immutable terrain/number assignment at a standard axial position."""

    q: int
    r: int
    resource: ResourceType
    number: Optional[int]

    def __post_init__(self) -> None:
        if not _is_integer(self.q) or not _is_integer(self.r):
            raise CustomMapError("タイル座標q/rはboolではない整数で指定してください。")
        if not isinstance(self.resource, ResourceType):
            raise CustomMapError("タイル資源はResourceTypeで指定してください。")
        if self.resource is ResourceType.DESERT:
            if self.number is not None:
                raise CustomMapError("砂漠タイルに数字を設定できません。")
            return
        if not _is_integer(self.number) or self.number not in _VALID_NUMBERS:
            raise CustomMapError(
                "土地タイルの数字は2〜12（7を除く）の公式数字で指定してください。"
            )

    @property
    def axial(self) -> tuple[int, int]:
        return self.q, self.r

    def to_document(self) -> dict[str, Any]:
        return {
            "q": self.q,
            "r": self.r,
            "resource": self.resource.name,
            "number": self.number,
        }


@dataclass(frozen=True)
class CustomMapSpec:
    """A canonical, official-inventory layout for the standard board."""

    tiles: tuple[CustomTileSpec, ...]
    harbors: tuple[Optional[ResourceType], ...]
    name: str = "カスタムマップ"

    def __post_init__(self) -> None:
        name = _validate_name(self.name)
        try:
            tiles = tuple(self.tiles)
            harbors = tuple(self.harbors)
        except TypeError as exc:
            raise CustomMapError("tiles/harborsは配列で指定してください。") from exc

        if len(tiles) != len(STANDARD_AXIAL_COORDS):
            raise CustomMapError("標準盤面には19枚のタイルが必要です。")
        if not all(isinstance(tile, CustomTileSpec) for tile in tiles):
            raise CustomMapError("tilesにはCustomTileSpecだけを指定してください。")

        coordinates = [tile.axial for tile in tiles]
        duplicate_coordinates = sorted(
            coordinate
            for coordinate, count in Counter(coordinates).items()
            if count > 1
        )
        if duplicate_coordinates:
            raise CustomMapError(
                f"タイル座標が重複しています: {duplicate_coordinates[0]}"
            )
        coordinate_set = frozenset(coordinates)
        if coordinate_set != _STANDARD_AXIAL_SET:
            missing = sorted(_STANDARD_AXIAL_SET - coordinate_set)
            unknown = sorted(coordinate_set - _STANDARD_AXIAL_SET)
            detail = missing[0] if missing else unknown[0]
            raise CustomMapError(
                f"標準19タイル盤面の座標が不足または範囲外です: {detail}"
            )

        if Counter(tile.resource for tile in tiles) != Counter(_OFFICIAL_RESOURCES):
            raise CustomMapError("地形タイルの枚数が公式構成と一致しません。")
        land_numbers = [
            tile.number for tile in tiles if tile.resource is not ResourceType.DESERT
        ]
        if Counter(land_numbers) != Counter(_OFFICIAL_NUMBERS):
            raise CustomMapError("数字チップの枚数が公式構成と一致しません。")

        if len(harbors) != len(_OFFICIAL_HARBORS):
            raise CustomMapError("標準盤面には9か所の港が必要です。")
        if any(
            harbor is not None
            and (not isinstance(harbor, ResourceType) or harbor is ResourceType.DESERT)
            for harbor in harbors
        ):
            raise CustomMapError("港資源は土地資源またはNoneで指定してください。")
        if Counter(harbors) != Counter(_OFFICIAL_HARBORS):
            raise CustomMapError("港の種類と枚数が公式構成と一致しません。")

        canonical_tiles = tuple(
            sorted(tiles, key=lambda tile: _STANDARD_AXIAL_INDEX[tile.axial])
        )
        object.__setattr__(self, "tiles", canonical_tiles)
        object.__setattr__(self, "harbors", harbors)
        object.__setattr__(self, "name", name)

    @classmethod
    def from_board(cls, board: Any, *, name: str = "カスタムマップ") -> "CustomMapSpec":
        try:
            raw_tiles = tuple(board.tiles)
            raw_harbors = tuple(board.harbors)
        except (AttributeError, TypeError) as exc:
            raise CustomMapError(
                "標準盤面からカスタムマップを作成できません。"
            ) from exc

        tiles = []
        try:
            for tile in raw_tiles:
                q, r = tile.axial
                tiles.append(
                    CustomTileSpec(
                        q=q,
                        r=r,
                        resource=tile.resource_type,
                        number=tile.number,
                    )
                )
            harbors = tuple(harbor.resource_type for harbor in raw_harbors)
        except (AttributeError, TypeError, ValueError) as exc:
            if isinstance(exc, CustomMapError):
                raise
            raise CustomMapError("盤面のタイルまたは港データが不正です。") from exc
        return cls(tiles=tuple(tiles), harbors=harbors, name=name)

    @classmethod
    def from_document(cls, document: Any) -> "CustomMapSpec":
        root = _require_exact_keys(
            document,
            frozenset(("format", "version", "topology", "name", "tiles", "harbors")),
            label="custom map",
        )
        if root["format"] != CUSTOM_MAP_FORMAT:
            raise CustomMapError("このゲームのカスタムマップではありません。")
        if not _is_integer(root["version"]) or root["version"] != CUSTOM_MAP_VERSION:
            raise CustomMapError(
                f"未対応のカスタムマップversionです: {root['version']}"
            )
        if root["topology"] != CUSTOM_MAP_TOPOLOGY:
            raise CustomMapError("未対応の盤面トポロジーです。")
        name = _validate_name(root["name"])

        raw_tiles = root["tiles"]
        if type(raw_tiles) is not list:
            raise CustomMapError("custom map.tilesはJSON arrayで指定してください。")
        tiles = []
        for index, raw_tile in enumerate(raw_tiles):
            label = f"custom map.tiles[{index}]"
            tile = _require_exact_keys(
                raw_tile,
                frozenset(("q", "r", "resource", "number")),
                label=label,
            )
            if not _is_integer(tile["q"]) or not _is_integer(tile["r"]):
                raise CustomMapError(
                    f"{label}.q/rはboolではない整数で指定してください。"
                )
            resource = _parse_resource(tile["resource"], label=f"{label}.resource")
            tiles.append(
                CustomTileSpec(
                    q=tile["q"],
                    r=tile["r"],
                    resource=resource,
                    number=tile["number"],
                )
            )

        raw_harbors = root["harbors"]
        if type(raw_harbors) is not list:
            raise CustomMapError("custom map.harborsはJSON arrayで指定してください。")
        harbor_by_slot: dict[int, Optional[ResourceType]] = {}
        for index, raw_harbor in enumerate(raw_harbors):
            label = f"custom map.harbors[{index}]"
            harbor = _require_exact_keys(
                raw_harbor,
                frozenset(("slot", "resource")),
                label=label,
            )
            slot = _coerce_harbor_slot(harbor["slot"], label=f"{label}.slot")
            if slot in harbor_by_slot:
                raise CustomMapError(f"港slotが重複しています: {slot}")
            harbor_by_slot[slot] = _parse_resource(
                harbor["resource"],
                label=f"{label}.resource",
                allow_none=True,
            )
        if set(harbor_by_slot) != set(range(len(_OFFICIAL_HARBORS))):
            raise CustomMapError("港slot 0〜8に不足があります。")

        return cls(
            tiles=tuple(tiles),
            harbors=tuple(
                harbor_by_slot[index] for index in range(len(_OFFICIAL_HARBORS))
            ),
            name=name,
        )

    def to_document(self) -> dict[str, Any]:
        """Return a fresh document containing JSON-native values only."""

        return {
            "format": CUSTOM_MAP_FORMAT,
            "version": CUSTOM_MAP_VERSION,
            "topology": CUSTOM_MAP_TOPOLOGY,
            "name": self.name,
            "tiles": [tile.to_document() for tile in self.tiles],
            "harbors": [
                {
                    "slot": slot,
                    "resource": resource.name if resource is not None else None,
                }
                for slot, resource in enumerate(self.harbors)
            ],
        }

    @property
    def fingerprint(self) -> str:
        """Return the stable SHA-256 identity of gameplay-relevant layout data."""

        content = {
            "format": CUSTOM_MAP_FORMAT,
            "version": CUSTOM_MAP_VERSION,
            "topology": CUSTOM_MAP_TOPOLOGY,
            "tiles": [tile.to_document() for tile in self.tiles],
            "harbors": [
                resource.name if resource is not None else None
                for resource in self.harbors
            ],
        }
        payload = json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def tile_at(self, axial: Sequence[int]) -> CustomTileSpec:
        coordinate = _coerce_axial(axial, label="tile coordinate")
        return self.tiles[_STANDARD_AXIAL_INDEX[coordinate]]

    def swap_tiles(
        self, first: Sequence[int], second: Sequence[int]
    ) -> "CustomMapSpec":
        """Swap complete terrain/number pieces while preserving official inventory."""

        first_coordinate = _coerce_axial(first, label="first tile")
        second_coordinate = _coerce_axial(second, label="second tile")
        if first_coordinate == second_coordinate:
            return self
        first_tile = self.tile_at(first_coordinate)
        second_tile = self.tile_at(second_coordinate)
        replacements = {
            first_coordinate: (second_tile.resource, second_tile.number),
            second_coordinate: (first_tile.resource, first_tile.number),
        }
        return self._replace_tile_contents(replacements)

    def swap_numbers(
        self, first: Sequence[int], second: Sequence[int]
    ) -> "CustomMapSpec":
        """Swap number tokens between two non-desert tiles."""

        first_coordinate = _coerce_axial(first, label="first tile")
        second_coordinate = _coerce_axial(second, label="second tile")
        if first_coordinate == second_coordinate:
            return self
        first_tile = self.tile_at(first_coordinate)
        second_tile = self.tile_at(second_coordinate)
        if (
            first_tile.resource is ResourceType.DESERT
            or second_tile.resource is ResourceType.DESERT
        ):
            raise CustomMapError("砂漠には数字チップを置けないため交換できません。")
        replacements = {
            first_coordinate: (first_tile.resource, second_tile.number),
            second_coordinate: (second_tile.resource, first_tile.number),
        }
        return self._replace_tile_contents(replacements)

    def swap_harbors(self, first_slot: int, second_slot: int) -> "CustomMapSpec":
        first = _coerce_harbor_slot(first_slot, label="first harbor slot")
        second = _coerce_harbor_slot(second_slot, label="second harbor slot")
        if first == second:
            return self
        harbors = list(self.harbors)
        harbors[first], harbors[second] = harbors[second], harbors[first]
        return CustomMapSpec(tiles=self.tiles, harbors=tuple(harbors), name=self.name)

    def shuffle_tiles(self, rng: Any = None) -> "CustomMapSpec":
        """Shuffle complete terrain/number pieces across the 19 positions."""

        contents = [(tile.resource, tile.number) for tile in self.tiles]
        _shuffle(contents, rng)
        replacements = {
            tile.axial: content for tile, content in zip(self.tiles, contents)
        }
        return self._replace_tile_contents(replacements)

    def shuffle_numbers(self, rng: Any = None) -> "CustomMapSpec":
        """Shuffle number tokens while leaving every terrain in place."""

        numbers = [
            tile.number
            for tile in self.tiles
            if tile.resource is not ResourceType.DESERT
        ]
        _shuffle(numbers, rng)
        number_iterator = iter(numbers)
        replacements = {
            tile.axial: (
                tile.resource,
                None if tile.resource is ResourceType.DESERT else next(number_iterator),
            )
            for tile in self.tiles
        }
        return self._replace_tile_contents(replacements)

    def shuffle_harbors(self, rng: Any = None) -> "CustomMapSpec":
        harbors = list(self.harbors)
        _shuffle(harbors, rng)
        return CustomMapSpec(tiles=self.tiles, harbors=tuple(harbors), name=self.name)

    def shuffle_all(self, rng: Any = None) -> "CustomMapSpec":
        """Shuffle pieces, tokens, and harbors with one reproducible RNG stream."""

        source = random.Random() if rng is None else rng
        return (
            self.shuffle_tiles(source).shuffle_numbers(source).shuffle_harbors(source)
        )

    def balance_warnings(self) -> tuple[str, ...]:
        """Describe potentially lopsided but structurally valid arrangements."""

        warnings = []
        tile_by_axial = {tile.axial: tile for tile in self.tiles}

        for tile in self.tiles:
            if tile.number not in (6, 8):
                continue
            tile_index = _STANDARD_AXIAL_INDEX[tile.axial]
            for dq, dr in AXIAL_DIRECTIONS:
                neighbor_coordinate = (tile.q + dq, tile.r + dr)
                neighbor = tile_by_axial.get(neighbor_coordinate)
                if (
                    neighbor is not None
                    and neighbor.number in (6, 8)
                    and tile_index < _STANDARD_AXIAL_INDEX[neighbor_coordinate]
                ):
                    warnings.append(
                        "6/8が隣接しています: "
                        f"({tile.q},{tile.r}) と ({neighbor.q},{neighbor.r})"
                    )

        high_number_counts = Counter(
            tile.resource for tile in self.tiles if tile.number in (6, 8)
        )
        for resource in _LAND_RESOURCES:
            count = high_number_counts[resource]
            if count > 1:
                warnings.append(f"{resource.name}に6/8が{count}枚集中しています。")

        for slot, harbor_resource in enumerate(self.harbors):
            if harbor_resource is None:
                continue
            adjacent = tile_by_axial[_HARBOR_ADJACENT_AXIALS[slot]]
            if adjacent.resource is harbor_resource and adjacent.number in (6, 8):
                warnings.append(
                    f"港slot {slot} ({harbor_resource.name}) が同資源の"
                    f"高確率タイル({adjacent.q},{adjacent.r})に隣接しています。"
                )
        return tuple(warnings)

    def _replace_tile_contents(
        self,
        replacements: Mapping[tuple[int, int], tuple[ResourceType, Optional[int]]],
    ) -> "CustomMapSpec":
        tiles = tuple(
            CustomTileSpec(
                q=tile.q,
                r=tile.r,
                resource=replacements.get(tile.axial, (tile.resource, tile.number))[0],
                number=replacements.get(tile.axial, (tile.resource, tile.number))[1],
            )
            for tile in self.tiles
        )
        return CustomMapSpec(tiles=tiles, harbors=self.harbors, name=self.name)
