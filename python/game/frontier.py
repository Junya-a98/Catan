"""Pure state helpers for frontier exploration board catalogs.

The authoritative board still owns the real terrain, number tokens, and
harbors.  This module stores only which axial coordinates are public, so a
network projection can mask everything that has not been discovered yet.

The original nineteen-tile frontier document intentionally has no ``catalog``
field.  That byte-level shape is part of existing save/replay identity.  New
catalogs therefore opt in with an explicit discriminator instead of rewriting
legacy documents during canonicalization.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


FRONTIER_KIND = "frontier"
FRONTIER_INITIAL_RADIUS = 1
FRONTIER_REVEAL_RULE = "road_adjacent_v1"
STANDARD_FRONTIER_CATALOG = "standard_19_v1"
EXPANDED_FRONTIER_CATALOG = "outer_ring_37_v1"
DEFAULT_FRONTIER_OPTIONS = {
    "initial_radius": FRONTIER_INITIAL_RADIUS,
    "reveal_rule": FRONTIER_REVEAL_RULE,
}
EXPANDED_FRONTIER_OPTIONS = {
    "catalog": EXPANDED_FRONTIER_CATALOG,
    "initial_radius": FRONTIER_INITIAL_RADIUS,
    "reveal_rule": FRONTIER_REVEAL_RULE,
}
_LEGACY_OPTION_KEYS = frozenset(DEFAULT_FRONTIER_OPTIONS)
_EXPANDED_OPTION_KEYS = frozenset(EXPANDED_FRONTIER_OPTIONS)
_LEGACY_PUBLIC_KEYS = frozenset({"revealed_tiles", "discovery_count"})
_EXPANDED_PUBLIC_KEYS = frozenset(
    {"catalog", "revealed_tiles", "discovery_count"}
)
_PRIVATE_KEYS = frozenset({"initial_revealed_tiles"})


class FrontierError(ValueError):
    """Raised when frontier configuration or runtime state is malformed."""


def _standard_axials(radius: int = 2) -> tuple[tuple[int, int], ...]:
    coordinates = []
    for q in range(-radius, radius + 1):
        r_min = max(-radius, -q - radius)
        r_max = min(radius, -q + radius)
        for r in range(r_min, r_max + 1):
            coordinates.append((q, r))
    return tuple(sorted(coordinates, key=lambda coordinate: (coordinate[1], coordinate[0])))


STANDARD_AXIALS = _standard_axials(radius=2)
STANDARD_AXIAL_SET = frozenset(STANDARD_AXIALS)
EXPANDED_AXIALS = _standard_axials(radius=3)
EXPANDED_AXIAL_SET = frozenset(EXPANDED_AXIALS)
INITIAL_CORE_AXIALS = tuple(
    axial
    for axial in STANDARD_AXIALS
    if max(abs(axial[0]), abs(axial[1]), abs(axial[0] + axial[1]))
    <= FRONTIER_INITIAL_RADIUS
)


def axial_key(axial: tuple[int, int]) -> str:
    """Return a stable JSON key for one axial coordinate."""

    if (
        not isinstance(axial, tuple)
        or len(axial) != 2
        or type(axial[0]) is not int
        or type(axial[1]) is not int
    ):
        raise FrontierError("frontier axial座標が不正です。")
    return f"{axial[0]},{axial[1]}"


def _allowed_axial_set(catalog: str) -> frozenset[tuple[int, int]]:
    if catalog == STANDARD_FRONTIER_CATALOG:
        return STANDARD_AXIAL_SET
    if catalog == EXPANDED_FRONTIER_CATALOG:
        return EXPANDED_AXIAL_SET
    raise FrontierError("frontier catalog が不正です。")


def axial_from_key(
    value: Any,
    *,
    catalog: str = STANDARD_FRONTIER_CATALOG,
) -> tuple[int, int]:
    if not isinstance(value, str):
        raise FrontierError("frontier tile keyは文字列で指定してください。")
    pieces = value.split(",")
    if len(pieces) != 2:
        raise FrontierError("frontier tile keyが不正です。")
    try:
        axial = (int(pieces[0]), int(pieces[1]))
    except ValueError as exc:
        raise FrontierError("frontier tile keyが不正です。") from exc
    if axial_key(axial) != value or axial not in _allowed_axial_set(catalog):
        raise FrontierError("frontier tile keyが盤面catalogの範囲外です。")
    return axial


def _sort_axials(axials: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    return tuple(sorted(set(axials), key=lambda coordinate: (coordinate[1], coordinate[0])))


def _keys_for(axials: Iterable[tuple[int, int]]) -> list[str]:
    return [axial_key(axial) for axial in _sort_axials(axials)]


def canonical_frontier_options(options: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(options, Mapping):
        raise FrontierError("frontier options の項目が不正です。")
    keys = set(options)
    if keys == _LEGACY_OPTION_KEYS:
        canonical = DEFAULT_FRONTIER_OPTIONS
    elif keys == _EXPANDED_OPTION_KEYS:
        if options.get("catalog") != EXPANDED_FRONTIER_CATALOG:
            raise FrontierError("frontier catalog が不正です。")
        canonical = EXPANDED_FRONTIER_OPTIONS
    else:
        raise FrontierError("frontier options の項目が不正です。")
    if options.get("initial_radius") != FRONTIER_INITIAL_RADIUS:
        raise FrontierError("frontier initial_radius は1で指定してください。")
    if options.get("reveal_rule") != FRONTIER_REVEAL_RULE:
        raise FrontierError("frontier reveal_rule が不正です。")
    return dict(canonical)


def frontier_catalog_from_options(options: Mapping[str, Any]) -> str:
    """Return the implicit legacy or explicit expanded catalog identity."""

    canonical = canonical_frontier_options(options)
    return canonical.get("catalog", STANDARD_FRONTIER_CATALOG)


def frontier_board_radius_from_options(options: Mapping[str, Any]) -> int:
    """Return the authoritative hex radius selected by frontier options."""

    return 3 if frontier_catalog_from_options(options) == EXPANDED_FRONTIER_CATALOG else 2


def _frontier_catalog_from_public(public: Mapping[str, Any]) -> str:
    if not isinstance(public, Mapping):
        raise FrontierError("frontier public stateの項目が不正です。")
    keys = set(public)
    if keys == _LEGACY_PUBLIC_KEYS:
        return STANDARD_FRONTIER_CATALOG
    if keys == _EXPANDED_PUBLIC_KEYS:
        if public.get("catalog") != EXPANDED_FRONTIER_CATALOG:
            raise FrontierError("frontier public stateのcatalogが不正です。")
        return EXPANDED_FRONTIER_CATALOG
    raise FrontierError("frontier public stateの項目が不正です。")


def create_initial_frontier_documents(
    options: Mapping[str, Any],
    *,
    robber_axial: tuple[int, int],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create state with the center ring and current robber tile public."""

    catalog = frontier_catalog_from_options(options)
    if robber_axial not in _allowed_axial_set(catalog):
        raise FrontierError("初期盗賊タイルが盤面catalogの範囲外です。")
    if catalog == EXPANDED_FRONTIER_CATALOG:
        if robber_axial != (0, 0):
            raise FrontierError("拡張frontierの初期盗賊タイルは中央です。")
        initial = INITIAL_CORE_AXIALS
    else:
        initial = _sort_axials((*INITIAL_CORE_AXIALS, robber_axial))
    public = {
        "revealed_tiles": _keys_for(initial),
        "discovery_count": 0,
    }
    if catalog == EXPANDED_FRONTIER_CATALOG:
        public = {"catalog": catalog, **public}
    private = {"initial_revealed_tiles": _keys_for(initial)}
    return public, private


def _parse_revealed(
    raw: Any,
    *,
    label: str,
    catalog: str,
) -> tuple[tuple[int, int], ...]:
    if not isinstance(raw, (list, tuple)):
        raise FrontierError(f"{label}は配列で指定してください。")
    axials = tuple(axial_from_key(value, catalog=catalog) for value in raw)
    if len(set(axials)) != len(axials) or list(raw) != _keys_for(axials):
        raise FrontierError(f"{label}は重複なしの盤面順で指定してください。")
    return axials


def validate_frontier_public(
    public: Mapping[str, Any],
    *,
    options: Mapping[str, Any] | None = None,
) -> None:
    catalog = _frontier_catalog_from_public(public)
    if options is not None and catalog != frontier_catalog_from_options(options):
        raise FrontierError("frontier stateのcatalogが設定と一致しません。")
    revealed = _parse_revealed(
        public.get("revealed_tiles"),
        label="revealed_tiles",
        catalog=catalog,
    )
    if not set(INITIAL_CORE_AXIALS).issubset(revealed):
        raise FrontierError("frontier public stateに中央公開領域がありません。")
    discovery_count = public.get("discovery_count")
    if type(discovery_count) is not int or discovery_count < 0:
        raise FrontierError("frontier discovery_countが不正です。")
    expected_counts = (
        (len(revealed) - 7,)
        if catalog == EXPANDED_FRONTIER_CATALOG
        else (len(revealed) - 7, len(revealed) - 8)
    )
    # The legacy desert may add zero or one outer tile to the core.  Expanded
    # boards fix the desert/robber at the center and always start with seven.
    if discovery_count not in expected_counts:
        raise FrontierError("frontier discovery_countと公開数が一致しません。")


def validate_frontier_documents(
    public: Mapping[str, Any],
    private: Mapping[str, Any],
    *,
    options: Mapping[str, Any] | None = None,
) -> None:
    validate_frontier_public(public, options=options)
    catalog = _frontier_catalog_from_public(public)
    if not isinstance(private, Mapping) or set(private) != _PRIVATE_KEYS:
        raise FrontierError("frontier private stateの項目が不正です。")
    revealed = _parse_revealed(
        public["revealed_tiles"],
        label="revealed_tiles",
        catalog=catalog,
    )
    initial = _parse_revealed(
        private.get("initial_revealed_tiles"),
        label="initial_revealed_tiles",
        catalog=catalog,
    )
    valid_initial_lengths = (
        (7,)
        if catalog == EXPANDED_FRONTIER_CATALOG
        else (7, 8)
    )
    if (
        not set(INITIAL_CORE_AXIALS).issubset(initial)
        or len(initial) not in valid_initial_lengths
    ):
        raise FrontierError("frontier初期公開領域が不正です。")
    if not set(initial).issubset(revealed):
        raise FrontierError("frontier公開領域が初期領域を含んでいません。")
    if public["discovery_count"] != len(revealed) - len(initial):
        raise FrontierError("frontier discovery_countと初期領域が一致しません。")


def reveal_frontier_tiles(
    public: Mapping[str, Any],
    private: Mapping[str, Any],
    axials: Iterable[tuple[int, int]],
) -> tuple[dict[str, Any], dict[str, Any], tuple[tuple[int, int], ...]]:
    """Reveal valid standard-board coordinates and return newly public tiles."""

    validate_frontier_documents(public, private)
    catalog = _frontier_catalog_from_public(public)
    requested = _sort_axials(axials)
    if any(axial not in _allowed_axial_set(catalog) for axial in requested):
        raise FrontierError("公開対象に盤面catalog外のタイルがあります。")
    existing = set(
        _parse_revealed(
            public["revealed_tiles"],
            label="revealed_tiles",
            catalog=catalog,
        )
    )
    newly_revealed = tuple(axial for axial in requested if axial not in existing)
    if not newly_revealed:
        return dict(public), dict(private), ()
    revealed = _sort_axials((*existing, *newly_revealed))
    next_public = {
        "revealed_tiles": _keys_for(revealed),
        "discovery_count": public["discovery_count"] + len(newly_revealed),
    }
    if catalog == EXPANDED_FRONTIER_CATALOG:
        next_public = {"catalog": catalog, **next_public}
    next_private = {"initial_revealed_tiles": list(private["initial_revealed_tiles"])}
    validate_frontier_documents(next_public, next_private)
    return next_public, next_private, newly_revealed


__all__ = (
    "DEFAULT_FRONTIER_OPTIONS",
    "EXPANDED_AXIALS",
    "EXPANDED_FRONTIER_CATALOG",
    "EXPANDED_FRONTIER_OPTIONS",
    "FRONTIER_KIND",
    "FrontierError",
    "INITIAL_CORE_AXIALS",
    "STANDARD_AXIALS",
    "STANDARD_FRONTIER_CATALOG",
    "axial_from_key",
    "axial_key",
    "canonical_frontier_options",
    "create_initial_frontier_documents",
    "frontier_board_radius_from_options",
    "frontier_catalog_from_options",
    "reveal_frontier_tiles",
    "validate_frontier_documents",
    "validate_frontier_public",
)
