import json
import random
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from game.custom_map import (
    CUSTOM_MAP_FORMAT,
    CUSTOM_MAP_TOPOLOGY,
    CUSTOM_MAP_VERSION,
    CustomMapError,
    CustomMapSpec,
    CustomTileSpec,
    STANDARD_AXIAL_COORDS,
)
from game.game_board import GameBoard
from game.resources import ResourceType


def make_spec(*, seed=417, name="テストマップ"):
    return CustomMapSpec.from_board(GameBoard(seed=seed), name=name)


def test_board_document_round_trip_is_canonical_json_and_defensive():
    spec = make_spec()

    assert len(spec.tiles) == 19
    assert tuple(tile.axial for tile in spec.tiles) == STANDARD_AXIAL_COORDS
    assert len(spec.harbors) == 9
    assert isinstance(spec.tiles, tuple)
    assert isinstance(spec.harbors, tuple)

    document = spec.to_document()
    encoded = json.dumps(document, ensure_ascii=False, allow_nan=False)
    restored = CustomMapSpec.from_document(json.loads(encoded))

    assert restored == spec
    assert restored.fingerprint == spec.fingerprint
    assert document["format"] == CUSTOM_MAP_FORMAT
    assert document["version"] == CUSTOM_MAP_VERSION
    assert document["topology"] == CUSTOM_MAP_TOPOLOGY

    document["tiles"][0]["resource"] = "DESERT"
    document["harbors"][0]["resource"] = "ORE"
    assert spec.to_document()["tiles"][0] != document["tiles"][0]
    assert spec.to_document()["harbors"][0] != document["harbors"][0]


def test_direct_construction_canonicalizes_tile_order_and_is_immutable():
    original = make_spec()
    reordered = CustomMapSpec(
        tiles=tuple(reversed(original.tiles)),
        harbors=list(original.harbors),
        name=original.name,
    )

    assert reordered == original
    assert tuple(tile.axial for tile in reordered.tiles) == STANDARD_AXIAL_COORDS
    with pytest.raises(FrozenInstanceError):
        reordered.name = "変更"
    with pytest.raises(FrozenInstanceError):
        reordered.tiles[0].number = 5


def test_fingerprint_ignores_display_name_but_changes_with_layout():
    spec = make_spec()
    renamed = CustomMapSpec(
        tiles=spec.tiles,
        harbors=spec.harbors,
        name="別名",
    )
    land = [tile for tile in spec.tiles if tile.resource is not ResourceType.DESERT]
    first = land[0]
    second = next(tile for tile in land[1:] if tile.number != first.number)
    changed = spec.swap_numbers(first.axial, second.axial)

    assert renamed.fingerprint == spec.fingerprint
    assert changed.fingerprint != spec.fingerprint
    assert len(spec.fingerprint) == 64
    assert set(spec.fingerprint) <= set("0123456789abcdef")


def test_swap_tiles_moves_terrain_and_number_together_without_mutating_source():
    spec = make_spec()
    desert = next(tile for tile in spec.tiles if tile.resource is ResourceType.DESERT)
    land = next(tile for tile in spec.tiles if tile.resource is not ResourceType.DESERT)

    swapped = spec.swap_tiles(desert.axial, land.axial)

    assert swapped.tile_at(desert.axial).resource is land.resource
    assert swapped.tile_at(desert.axial).number == land.number
    assert swapped.tile_at(land.axial).resource is ResourceType.DESERT
    assert swapped.tile_at(land.axial).number is None
    assert spec.tile_at(desert.axial).resource is ResourceType.DESERT
    assert swapped.swap_tiles(desert.axial, land.axial) == spec


def test_swap_numbers_requires_two_non_desert_tiles():
    spec = make_spec()
    desert = next(tile for tile in spec.tiles if tile.resource is ResourceType.DESERT)
    land = [tile for tile in spec.tiles if tile.resource is not ResourceType.DESERT]
    first = land[0]
    second = next(tile for tile in land[1:] if tile.number != first.number)

    swapped = spec.swap_numbers(first.axial, second.axial)

    assert swapped.tile_at(first.axial).resource is first.resource
    assert swapped.tile_at(second.axial).resource is second.resource
    assert swapped.tile_at(first.axial).number == second.number
    assert swapped.tile_at(second.axial).number == first.number
    with pytest.raises(CustomMapError, match="砂漠"):
        spec.swap_numbers(desert.axial, first.axial)


def test_harbor_swap_and_seeded_shuffles_preserve_valid_inventory():
    spec = make_spec()
    first = 0
    second = next(
        index for index, value in enumerate(spec.harbors) if value != spec.harbors[0]
    )

    swapped = spec.swap_harbors(first, second)

    assert swapped.harbors[first] is spec.harbors[second]
    assert swapped.harbors[second] is spec.harbors[first]
    assert swapped.swap_harbors(first, second) == spec

    for method_name in (
        "shuffle_tiles",
        "shuffle_numbers",
        "shuffle_harbors",
        "shuffle_all",
    ):
        first_result = getattr(spec, method_name)(random.Random(8128))
        second_result = getattr(spec, method_name)(random.Random(8128))
        assert first_result == second_result
        assert CustomMapSpec.from_document(first_result.to_document()) == first_result


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("format", "other", "カスタムマップ"),
        ("version", True, "version"),
        ("version", 2, "version"),
        ("topology", "free-form", "トポロジー"),
        ("name", " bad", "マップ名"),
        ("name", "bad\nname", "マップ名"),
    ),
)
def test_document_rejects_invalid_header_values(field, value, message):
    document = make_spec().to_document()
    document[field] = value

    with pytest.raises(CustomMapError, match=message):
        CustomMapSpec.from_document(document)


def test_document_rejects_missing_and_unknown_keys_at_every_level():
    document = make_spec().to_document()
    extra_root = deepcopy(document)
    extra_root["script"] = "ignored?"
    missing_root = deepcopy(document)
    missing_root.pop("tiles")
    extra_tile = deepcopy(document)
    extra_tile["tiles"][0]["x"] = 10
    extra_harbor = deepcopy(document)
    extra_harbor["harbors"][0]["trade_rate"] = 2

    for candidate in (extra_root, missing_root, extra_tile, extra_harbor):
        with pytest.raises(CustomMapError, match="項目"):
            CustomMapSpec.from_document(candidate)


def test_document_rejects_bool_integer_fields_and_invalid_resources():
    document = make_spec().to_document()
    bool_coordinate = deepcopy(document)
    bool_coordinate["tiles"][0]["q"] = True
    bool_number = deepcopy(document)
    land_index = next(
        index
        for index, tile in enumerate(bool_number["tiles"])
        if tile["resource"] != "DESERT"
    )
    bool_number["tiles"][land_index]["number"] = True
    bool_slot = deepcopy(document)
    bool_slot["harbors"][0]["slot"] = False
    unknown_tile_resource = deepcopy(document)
    unknown_tile_resource["tiles"][0]["resource"] = "GOLD"
    desert_harbor = deepcopy(document)
    desert_harbor["harbors"][0]["resource"] = "DESERT"

    candidates = (
        bool_coordinate,
        bool_number,
        bool_slot,
        unknown_tile_resource,
        desert_harbor,
    )
    for candidate in candidates:
        with pytest.raises(CustomMapError):
            CustomMapSpec.from_document(candidate)


def test_document_rejects_duplicate_or_missing_tile_and_harbor_slots():
    document = make_spec().to_document()
    duplicate_tile = deepcopy(document)
    duplicate_tile["tiles"][0]["q"] = duplicate_tile["tiles"][1]["q"]
    duplicate_tile["tiles"][0]["r"] = duplicate_tile["tiles"][1]["r"]
    missing_tile = deepcopy(document)
    missing_tile["tiles"].pop()
    duplicate_harbor = deepcopy(document)
    duplicate_harbor["harbors"][0]["slot"] = duplicate_harbor["harbors"][1]["slot"]
    missing_harbor = deepcopy(document)
    missing_harbor["harbors"].pop()

    with pytest.raises(CustomMapError, match="重複"):
        CustomMapSpec.from_document(duplicate_tile)
    with pytest.raises(CustomMapError, match="19"):
        CustomMapSpec.from_document(missing_tile)
    with pytest.raises(CustomMapError, match="重複"):
        CustomMapSpec.from_document(duplicate_harbor)
    with pytest.raises(CustomMapError, match="slot"):
        CustomMapSpec.from_document(missing_harbor)


def test_document_rejects_non_official_resource_number_and_harbor_multisets():
    document = make_spec().to_document()
    resources = deepcopy(document)
    first_wood = next(tile for tile in resources["tiles"] if tile["resource"] == "WOOD")
    first_wood["resource"] = "SHEEP"
    numbers = deepcopy(document)
    first_two = next(tile for tile in numbers["tiles"] if tile["number"] == 2)
    first_two["number"] = 3
    harbors = deepcopy(document)
    generic = next(
        harbor for harbor in harbors["harbors"] if harbor["resource"] is None
    )
    generic["resource"] = "WOOD"

    with pytest.raises(CustomMapError, match="地形"):
        CustomMapSpec.from_document(resources)
    with pytest.raises(CustomMapError, match="数字チップ"):
        CustomMapSpec.from_document(numbers)
    with pytest.raises(CustomMapError, match="港の種類"):
        CustomMapSpec.from_document(harbors)


def test_balance_warnings_are_empty_for_constrained_board_and_find_red_adjacency():
    spec = make_spec()
    assert spec.balance_warnings() == ()
    tile_by_axial = {tile.axial: tile for tile in spec.tiles}
    red_tiles = [tile for tile in spec.tiles if tile.number in (6, 8)]
    candidate = None
    for red in red_tiles:
        for dq, dr in ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)):
            neighbor = tile_by_axial.get((red.q + dq, red.r + dr))
            if (
                neighbor is not None
                and neighbor.resource is not ResourceType.DESERT
                and neighbor.number not in (6, 8)
            ):
                source = next(other for other in red_tiles if other is not red)
                candidate = spec.swap_numbers(neighbor.axial, source.axial)
                break
        if candidate is not None:
            break

    assert candidate is not None
    assert any("6/8が隣接" in warning for warning in candidate.balance_warnings())


def test_balance_warnings_find_resource_concentration_and_matching_harbor():
    board = GameBoard(seed=417)
    spec = CustomMapSpec.from_board(board)
    red_tiles = [tile for tile in spec.tiles if tile.number in (6, 8)]

    target_red = red_tiles[0]
    same_resource_non_red = next(
        tile
        for tile in spec.tiles
        if tile.resource is target_red.resource and tile.number not in (6, 8)
    )
    other_red = next(
        tile for tile in red_tiles if tile.resource is not target_red.resource
    )
    concentrated = spec.swap_numbers(same_resource_non_red.axial, other_red.axial)
    assert any("集中" in warning for warning in concentrated.balance_warnings())

    matching = None
    for slot, harbor in enumerate(board.harbors):
        resource = harbor.resource_type
        if resource is None:
            continue
        adjacent_tile = board.get_edge_adjacent_tiles((harbor.node1, harbor.node2))[0]
        source = next(
            (
                tile
                for tile in red_tiles
                if tile.resource is resource and tile.axial != adjacent_tile.axial
            ),
            None,
        )
        if source is None:
            continue
        matching = spec.swap_tiles(adjacent_tile.axial, source.axial)
        assert any(
            f"港slot {slot}" in warning for warning in matching.balance_warnings()
        )
        break

    assert matching is not None


def test_public_operations_reject_bool_indices_and_out_of_board_coordinates():
    spec = make_spec()
    with pytest.raises(CustomMapError):
        spec.tile_at((True, 0))
    with pytest.raises(CustomMapError):
        spec.swap_tiles((99, 99), (0, 0))
    with pytest.raises(CustomMapError):
        spec.swap_harbors(True, 1)


def test_custom_tile_spec_rejects_bool_number_and_desert_number():
    with pytest.raises(CustomMapError):
        CustomTileSpec(0, 0, ResourceType.WOOD, True)
    with pytest.raises(CustomMapError):
        CustomTileSpec(0, 0, ResourceType.DESERT, 5)
