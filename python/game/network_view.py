"""Immutable, privacy-preserving read model for network state snapshots.

This module never calls ``restore_game`` and never constructs ``CatanGame``.
It validates the public ``board_manifest`` carried directly by a
``state_snapshot`` envelope, then copies only fields that a LAN client or Web
renderer needs.  Private card/resource maps are accepted only for the envelope's
declared viewer; an accidental non-viewer disclosure rejects the whole snapshot.

The DTOs intentionally contain no legal-target list.  Target legality remains
an authority-server decision.  Stable ID and coordinate lookups exist solely
for drawing and hit testing.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
import json
import math
import re
from types import MappingProxyType
from typing import Any, Optional
import unicodedata


__all__ = (
    "BoardView",
    "BoundsView",
    "BuildingView",
    "DomesticTradeView",
    "EdgeView",
    "FrozenCounts",
    "HarborView",
    "MAX_BOARD_MANIFEST_BYTES",
    "MAX_VIEW_LOG_MESSAGES",
    "NetworkGameView",
    "NetworkViewError",
    "NodeView",
    "PlayerView",
    "PointView",
    "RoadView",
    "TileView",
    "parse_network_view",
    "parse_state_snapshot",
)


NETWORK_PROTOCOL_VERSION = 1
MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
MAX_BOARD_MANIFEST_BYTES = 1_000_000
MAX_TILES = 64
MAX_NODES = 256
MAX_EDGES = 384
MAX_HARBORS = 64
MAX_VIEW_LOG_MESSAGES = 200
MAX_INPUT_LOG_MESSAGES = 2_000
MAX_TEXT_LENGTH = 1_000
MAX_COORDINATE = 1_000_000.0
MAX_COUNT = 1_000_000
MAX_BANK_RESOURCE_COUNT = 19
MAX_TOTAL_RESOURCE_CARDS = MAX_BANK_RESOURCE_COUNT * 5
MAX_DEVELOPMENT_DECK_SIZE = 25
MAX_PLAYER_ROADS = 15
MAX_PLAYER_SETTLEMENTS = 5
MAX_PLAYER_CITIES = 4
MAX_PLAYED_KNIGHTS = 14
MAX_RESOURCE_SELECTION = 2
MAX_FREE_ROADS = 2
MIN_VICTORY_TARGET = 5
MAX_VICTORY_TARGET = 15

_RESOURCE_KEYS = ("WOOD", "SHEEP", "WHEAT", "BRICK", "ORE")
_TILE_RESOURCES = frozenset((*_RESOURCE_KEYS, "DESERT"))
_DEVELOPMENT_KEYS = (
    "KNIGHT",
    "ROAD_BUILDING",
    "YEAR_OF_PLENTY",
    "MONOPOLY",
)
_PHASES = frozenset(("initial", "main", "finished"))
_ACTION_MODES = frozenset((None, "road", "settlement", "city"))
_BUILDING_TYPES = frozenset(("settlement", "city"))
_BOARD_MODES = frozenset(("constrained", "fully_random", "custom"))
_DOMESTIC_TRADE_EDIT_SIDES = frozenset(("give", "receive"))
_FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_ID_PATTERNS = {
    "tile": re.compile(r"tile-[0-9]{1,4}\Z"),
    "node": re.compile(r"node-[0-9]{1,4}\Z"),
    "edge": re.compile(r"edge-[0-9]{1,4}\Z"),
    "harbor": re.compile(r"harbor-[0-9]{1,4}\Z"),
}


class NetworkViewError(ValueError):
    """Raised when an untrusted snapshot cannot become a safe read model."""


@dataclass(frozen=True)
class FrozenCounts(Mapping[str, int]):
    """Small immutable mapping used for validated resource/card counts."""

    entries: tuple[tuple[str, int], ...]

    def __getitem__(self, key: str) -> int:
        for item_key, value in self.entries:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _value in self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def to_dict(self) -> dict[str, int]:
        return dict(self.entries)


@dataclass(frozen=True)
class PointView:
    x: float
    y: float


@dataclass(frozen=True)
class BoundsView:
    min_x: float
    max_x: float
    min_y: float
    max_y: float

    def contains(self, point: PointView) -> bool:
        return (
            self.min_x <= point.x <= self.max_x and self.min_y <= point.y <= self.max_y
        )


@dataclass(frozen=True)
class BuildingView:
    owner_seat: int
    building_type: str


@dataclass(frozen=True)
class RoadView:
    owner_seat: int


@dataclass(frozen=True)
class TileView:
    target_id: str
    axial: tuple[int, int]
    center: PointView
    resource: str
    number: Optional[int]
    corner_node_ids: tuple[str, ...]
    robber: bool


@dataclass(frozen=True)
class NodeView:
    target_id: str
    position: PointView
    adjacent_tile_ids: tuple[str, ...]
    adjacent_node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    harbor_ids: tuple[str, ...]
    building: Optional[BuildingView]


@dataclass(frozen=True)
class EdgeView:
    target_id: str
    node_ids: tuple[str, str]
    adjacent_tile_ids: tuple[str, ...]
    perimeter: bool
    road: Optional[RoadView]
    harbor_id: Optional[str]


@dataclass(frozen=True)
class HarborView:
    target_id: str
    edge_id: str
    node_ids: tuple[str, str]
    trade_rate: int
    resource: Optional[str]
    label: str


@dataclass(frozen=True)
class BoardView:
    """Validated public board with immutable target/coordinate lookups."""

    mode: str
    seed: int
    bounds: BoundsView
    tiles: tuple[TileView, ...]
    nodes: tuple[NodeView, ...]
    edges: tuple[EdgeView, ...]
    harbors: tuple[HarborView, ...]
    _tile_by_id: Mapping[str, TileView] = field(repr=False, compare=False)
    _node_by_id: Mapping[str, NodeView] = field(repr=False, compare=False)
    _edge_by_id: Mapping[str, EdgeView] = field(repr=False, compare=False)
    _harbor_by_id: Mapping[str, HarborView] = field(repr=False, compare=False)
    _position_by_id: Mapping[str, PointView] = field(repr=False, compare=False)
    # Appended with a default to preserve the original positional constructor
    # for generated-board consumers.
    custom_map_fingerprint: Optional[str] = None

    @property
    def tile_by_id(self) -> Mapping[str, TileView]:
        return self._tile_by_id

    @property
    def node_by_id(self) -> Mapping[str, NodeView]:
        return self._node_by_id

    @property
    def edge_by_id(self) -> Mapping[str, EdgeView]:
        return self._edge_by_id

    @property
    def harbor_by_id(self) -> Mapping[str, HarborView]:
        return self._harbor_by_id

    @property
    def position_by_id(self) -> Mapping[str, PointView]:
        """Centers/midpoints for renderers and local hit testing only."""

        return self._position_by_id

    def position_for(self, target_id: str) -> PointView:
        return self._position_by_id[target_id]

    def edge_segment(self, edge_id: str) -> tuple[PointView, PointView]:
        edge = self._edge_by_id[edge_id]
        return (
            self._node_by_id[edge.node_ids[0]].position,
            self._node_by_id[edge.node_ids[1]].position,
        )


@dataclass(frozen=True)
class PlayerView:
    seat: int
    name: str
    color: tuple[int, int, int]
    public_vp: int
    resource_total: int
    development_card_total: int
    is_viewer: bool
    resources: Optional[FrozenCounts]
    development_cards: Optional[FrozenCounts]
    new_development_cards: Optional[FrozenCounts]
    victory_point_cards: Optional[int]
    # Defaults keep the original positional constructor API source-compatible.
    piece_pattern: int = 0
    marker: str = "●"
    roads_remaining: int = MAX_PLAYER_ROADS
    settlements_remaining: int = MAX_PLAYER_SETTLEMENTS
    cities_remaining: int = MAX_PLAYER_CITIES
    played_knights: int = 0


@dataclass(frozen=True)
class DomesticTradeView:
    """Public offer state; resource holdings remain private to ``PlayerView``."""

    partner_seat: Optional[int]
    editor_seat: Optional[int]
    give: FrozenCounts
    receive: FrozenCounts
    edit_side: str
    is_counter: bool
    is_broadcast: bool


@dataclass(frozen=True)
class NetworkGameView:
    """Minimal immutable state shared by a Web UI and the Pygame LAN UI."""

    revision: int
    viewer_seat: Optional[int]
    phase: str
    special_phase: Optional[str]
    action_mode: Optional[str]
    dice_rolled: bool
    current_actor_seat: int
    winner_seat: Optional[int]
    players: tuple[PlayerView, ...]
    board: BoardView
    logs: tuple[str, ...]
    _player_by_seat: Mapping[int, PlayerView] = field(repr=False, compare=False)
    # Appended defaults preserve the original constructor while exposing the
    # extra public state required by both Pygame and future Web renderers.
    victory_target: int = 10
    bank_resources: FrozenCounts = field(
        default_factory=lambda: FrozenCounts(
            tuple((resource, 0) for resource in _RESOURCE_KEYS)
        )
    )
    development_deck_remaining: int = 0
    initial_dice_phase: bool = False
    waiting_for_road: bool = False
    discard_remaining: int = 0
    resource_selection_remaining: int = 0
    free_roads_remaining: int = 0
    bank_trade_give_resource: Optional[str] = None
    longest_road_owner_seat: Optional[int] = None
    longest_road_length: int = 0
    largest_army_owner_seat: Optional[int] = None
    largest_army_size: int = 0
    domestic_trade: DomesticTradeView = field(
        default_factory=lambda: DomesticTradeView(
            partner_seat=None,
            editor_seat=None,
            give=FrozenCounts(tuple((resource, 0) for resource in _RESOURCE_KEYS)),
            receive=FrozenCounts(tuple((resource, 0) for resource in _RESOURCE_KEYS)),
            edit_side="give",
            is_counter=False,
            is_broadcast=False,
        )
    )

    @property
    def player_by_seat(self) -> Mapping[int, PlayerView]:
        return self._player_by_seat

    @property
    def current_actor(self) -> PlayerView:
        return self._player_by_seat[self.current_actor_seat]


@dataclass(frozen=True)
class _ParsedPlayer:
    name: str
    color: tuple[int, int, int]
    resource_total: int
    development_card_total: int
    resources: Optional[FrozenCounts]
    development_cards: Optional[FrozenCounts]
    new_development_cards: Optional[FrozenCounts]
    victory_point_cards: Optional[int]
    piece_pattern: int
    marker: str
    roads_remaining: int
    settlements_remaining: int
    cities_remaining: int
    played_knights: int


@dataclass(frozen=True)
class _PhaseData:
    name: str
    special_phase: Optional[str]
    action_mode: Optional[str]
    dice_rolled: bool
    current_actor_index: int
    winner_index: Optional[int]
    longest_road_owner: Optional[int]
    longest_road_length: int
    largest_army_owner: Optional[int]
    largest_army_size: int


def parse_network_view(envelope: Any) -> NetworkGameView:
    """Validate a ``state_snapshot`` mapping and return its safe read model."""

    envelope = _mapping(envelope, "snapshot")
    if envelope.get("type") != "state_snapshot":
        raise NetworkViewError("snapshot.type must be 'state_snapshot'")
    if envelope.get("protocol_version") != NETWORK_PROTOCOL_VERSION:
        raise NetworkViewError("unsupported snapshot protocol_version")
    revision = _integer(
        _required(envelope, "revision", "snapshot"),
        "snapshot.revision",
        minimum=0,
        maximum=MAX_SAFE_JSON_INTEGER,
    )
    state = _mapping(_required(envelope, "state", "snapshot"), "snapshot.state")
    if state.get("format") != "catan-local-save" or state.get("version") != 1:
        raise NetworkViewError("snapshot.state has an unsupported format/version")

    raw_players = _list(
        _required(state, "players", "snapshot.state"),
        "snapshot.state.players",
        minimum=2,
        maximum=4,
    )
    player_count = len(raw_players)
    viewer_index = _optional_index(
        _required(envelope, "viewer_player_index", "snapshot"),
        player_count,
        "snapshot.viewer_player_index",
    )
    parsed_players = tuple(
        _parse_player(raw, index, viewer_index) for index, raw in enumerate(raw_players)
    )
    phase = _parse_phase(
        _required(state, "phase", "snapshot.state"),
        player_count,
    )
    victory_target = _parse_victory_target(_required(state, "rules", "snapshot.state"))
    bank_resources = _counts(
        _required(state, "bank", "snapshot.state"),
        _RESOURCE_KEYS,
        "snapshot.state.bank",
        maximum=MAX_BANK_RESOURCE_COUNT,
    )
    development_deck = _mapping(
        _required(state, "development_deck", "snapshot.state"),
        "snapshot.state.development_deck",
    )
    development_deck_remaining = _integer(
        _required(
            development_deck,
            "remaining",
            "snapshot.state.development_deck",
        ),
        "snapshot.state.development_deck.remaining",
        minimum=0,
        maximum=MAX_DEVELOPMENT_DECK_SIZE,
    )
    initial_dice_phase, waiting_for_road = _parse_initial_context(
        _required(state, "initial", "snapshot.state")
    )
    (
        discard_remaining,
        resource_selection_remaining,
        free_roads_remaining,
        bank_trade_give_resource,
    ) = _parse_special_context(_required(state, "special", "snapshot.state"))
    domestic_trade = _parse_domestic_trade(
        _required(state, "domestic_trade", "snapshot.state"),
        player_count,
    )
    board_state = _mapping(
        _required(state, "board", "snapshot.state"),
        "snapshot.state.board",
    )
    if "custom_map" in board_state:
        raise NetworkViewError(
            "network snapshot board must not embed a custom map document"
        )
    mode = _enum_text(
        _required(board_state, "mode", "snapshot.state.board"),
        _BOARD_MODES,
        "snapshot.state.board.mode",
    )
    seed = _integer(
        _required(board_state, "seed", "snapshot.state.board"),
        "snapshot.state.board.seed",
        minimum=-MAX_SAFE_JSON_INTEGER,
        maximum=MAX_SAFE_JSON_INTEGER,
    )
    if mode == "custom":
        custom_map_fingerprint = _fingerprint(
            _required(
                board_state,
                "custom_map_fingerprint",
                "snapshot.state.board",
            ),
            "snapshot.state.board.custom_map_fingerprint",
        )
    else:
        if "custom_map_fingerprint" in board_state:
            raise NetworkViewError(
                "generated snapshot board cannot contain a custom map fingerprint"
            )
        custom_map_fingerprint = None
    board = _parse_board_manifest(
        _required(envelope, "board_manifest", "snapshot"),
        player_count=player_count,
        expected_mode=mode,
        expected_seed=seed,
        expected_custom_map_fingerprint=custom_map_fingerprint,
    )

    public_points = [0] * player_count
    for node in board.nodes:
        if node.building is not None:
            public_points[node.building.owner_seat - 1] += (
                2 if node.building.building_type == "city" else 1
            )
    if phase.longest_road_owner is not None:
        public_points[phase.longest_road_owner] += 2
    if phase.largest_army_owner is not None:
        public_points[phase.largest_army_owner] += 2

    players = tuple(
        PlayerView(
            seat=index + 1,
            name=parsed.name,
            color=parsed.color,
            public_vp=public_points[index],
            resource_total=parsed.resource_total,
            development_card_total=parsed.development_card_total,
            is_viewer=index == viewer_index,
            resources=parsed.resources,
            development_cards=parsed.development_cards,
            new_development_cards=parsed.new_development_cards,
            victory_point_cards=parsed.victory_point_cards,
            piece_pattern=parsed.piece_pattern,
            marker=parsed.marker,
            roads_remaining=parsed.roads_remaining,
            settlements_remaining=parsed.settlements_remaining,
            cities_remaining=parsed.cities_remaining,
            played_knights=parsed.played_knights,
        )
        for index, parsed in enumerate(parsed_players)
    )
    player_lookup = MappingProxyType({player.seat: player for player in players})
    logs = _parse_logs(_required(state, "history", "snapshot.state"))
    return NetworkGameView(
        revision=revision,
        viewer_seat=None if viewer_index is None else viewer_index + 1,
        phase=phase.name,
        special_phase=phase.special_phase,
        action_mode=phase.action_mode,
        dice_rolled=phase.dice_rolled,
        current_actor_seat=phase.current_actor_index + 1,
        winner_seat=None if phase.winner_index is None else phase.winner_index + 1,
        players=players,
        board=board,
        logs=logs,
        _player_by_seat=player_lookup,
        victory_target=victory_target,
        bank_resources=bank_resources,
        development_deck_remaining=development_deck_remaining,
        initial_dice_phase=initial_dice_phase,
        waiting_for_road=waiting_for_road,
        discard_remaining=discard_remaining,
        resource_selection_remaining=resource_selection_remaining,
        free_roads_remaining=free_roads_remaining,
        bank_trade_give_resource=bank_trade_give_resource,
        longest_road_owner_seat=(
            None if phase.longest_road_owner is None else phase.longest_road_owner + 1
        ),
        longest_road_length=phase.longest_road_length,
        largest_army_owner_seat=(
            None if phase.largest_army_owner is None else phase.largest_army_owner + 1
        ),
        largest_army_size=phase.largest_army_size,
        domestic_trade=domestic_trade,
    )


def parse_state_snapshot(envelope: Any) -> NetworkGameView:
    """Alias with an envelope-oriented name for transport adapters."""

    return parse_network_view(envelope)


def _parse_player(raw: Any, index: int, viewer_index: Optional[int]) -> _ParsedPlayer:
    label = f"snapshot.state.players[{index}]"
    player = _mapping(raw, label)
    name = _text(_required(player, "name", label), f"{label}.name", maximum=64)
    color_raw = _list(
        _required(player, "color", label),
        f"{label}.color",
        minimum=3,
        maximum=3,
    )
    color = tuple(
        _integer(channel, f"{label}.color[{offset}]", minimum=0, maximum=255)
        for offset, channel in enumerate(color_raw)
    )
    resource_total = _integer(
        _required(player, "resource_total", label),
        f"{label}.resource_total",
        minimum=0,
        maximum=MAX_COUNT,
    )
    development_total = _integer(
        _required(player, "development_card_total", label),
        f"{label}.development_card_total",
        minimum=0,
        maximum=MAX_COUNT,
    )
    piece_pattern = _integer(
        _required(player, "piece_pattern", label),
        f"{label}.piece_pattern",
        minimum=0,
        maximum=3,
    )
    marker = _text(
        _required(player, "marker", label),
        f"{label}.marker",
        maximum=8,
    )
    roads_remaining = _integer(
        _required(player, "roads_remaining", label),
        f"{label}.roads_remaining",
        minimum=0,
        maximum=MAX_PLAYER_ROADS,
    )
    settlements_remaining = _integer(
        _required(player, "settlements_remaining", label),
        f"{label}.settlements_remaining",
        minimum=0,
        maximum=MAX_PLAYER_SETTLEMENTS,
    )
    cities_remaining = _integer(
        _required(player, "cities_remaining", label),
        f"{label}.cities_remaining",
        minimum=0,
        maximum=MAX_PLAYER_CITIES,
    )
    played_knights = _integer(
        _required(player, "played_knights", label),
        f"{label}.played_knights",
        minimum=0,
        maximum=MAX_PLAYED_KNIGHTS,
    )
    is_viewer = index == viewer_index
    private_fields = (
        "resources",
        "development_cards",
        "new_development_cards",
        "victory_point_cards",
    )
    if any(field_name not in player for field_name in private_fields):
        raise NetworkViewError(f"{label} is missing private visibility fields")
    if not is_viewer:
        leaked = [
            field_name
            for field_name in private_fields
            if player.get(field_name) is not None
        ]
        if leaked:
            raise NetworkViewError(
                f"{label} exposes private field(s) to a non-viewer: {', '.join(leaked)}"
            )
        return _ParsedPlayer(
            name=name,
            color=color,
            resource_total=resource_total,
            development_card_total=development_total,
            resources=None,
            development_cards=None,
            new_development_cards=None,
            victory_point_cards=None,
            piece_pattern=piece_pattern,
            marker=marker,
            roads_remaining=roads_remaining,
            settlements_remaining=settlements_remaining,
            cities_remaining=cities_remaining,
            played_knights=played_knights,
        )

    missing = [
        field_name for field_name in private_fields if player.get(field_name) is None
    ]
    if missing:
        raise NetworkViewError(
            f"{label} is missing viewer-private field(s): {', '.join(missing)}"
        )
    resources = _counts(player["resources"], _RESOURCE_KEYS, f"{label}.resources")
    development = _counts(
        player["development_cards"],
        _DEVELOPMENT_KEYS,
        f"{label}.development_cards",
    )
    new_development = _counts(
        player["new_development_cards"],
        _DEVELOPMENT_KEYS,
        f"{label}.new_development_cards",
    )
    victory_point_cards = _integer(
        player["victory_point_cards"],
        f"{label}.victory_point_cards",
        minimum=0,
        maximum=MAX_COUNT,
    )
    if sum(resources.values()) != resource_total:
        raise NetworkViewError(f"{label}.resource_total does not match its private map")
    if (
        sum(development.values()) + sum(new_development.values()) + victory_point_cards
        != development_total
    ):
        raise NetworkViewError(
            f"{label}.development_card_total does not match its private maps"
        )
    return _ParsedPlayer(
        name=name,
        color=color,
        resource_total=resource_total,
        development_card_total=development_total,
        resources=resources,
        development_cards=development,
        new_development_cards=new_development,
        victory_point_cards=victory_point_cards,
        piece_pattern=piece_pattern,
        marker=marker,
        roads_remaining=roads_remaining,
        settlements_remaining=settlements_remaining,
        cities_remaining=cities_remaining,
        played_knights=played_knights,
    )


def _parse_phase(raw: Any, player_count: int) -> _PhaseData:
    phase = _mapping(raw, "snapshot.state.phase")
    name = _enum_text(
        _required(phase, "name", "snapshot.state.phase"),
        _PHASES,
        "snapshot.state.phase.name",
    )
    turn_order = _list(
        _required(phase, "turn_order", "snapshot.state.phase"),
        "snapshot.state.phase.turn_order",
        minimum=player_count,
        maximum=player_count,
    )
    turn_order = tuple(
        _integer(
            value,
            f"snapshot.state.phase.turn_order[{position}]",
            minimum=0,
            maximum=player_count - 1,
        )
        for position, value in enumerate(turn_order)
    )
    if set(turn_order) != set(range(player_count)):
        raise NetworkViewError("snapshot.state.phase.turn_order is not a permutation")
    current_position = _integer(
        _required(phase, "current_player_index", "snapshot.state.phase"),
        "snapshot.state.phase.current_player_index",
        minimum=0,
        maximum=player_count - 1,
    )
    action_mode = _required(phase, "action_mode", "snapshot.state.phase")
    if action_mode not in _ACTION_MODES:
        raise NetworkViewError("snapshot.state.phase.action_mode is invalid")
    special_phase = _required(phase, "special_phase", "snapshot.state.phase")
    if special_phase is not None:
        special_phase = _text(
            special_phase,
            "snapshot.state.phase.special_phase",
            maximum=64,
        )
    longest_road_owner = _optional_index(
        _required(phase, "longest_road_owner", "snapshot.state.phase"),
        player_count,
        "snapshot.state.phase.longest_road_owner",
    )
    longest_road_length = _integer(
        _required(phase, "longest_road_length", "snapshot.state.phase"),
        "snapshot.state.phase.longest_road_length",
        minimum=0,
        maximum=MAX_PLAYER_ROADS,
    )
    largest_army_owner = _optional_index(
        _required(phase, "largest_army_owner", "snapshot.state.phase"),
        player_count,
        "snapshot.state.phase.largest_army_owner",
    )
    largest_army_size = _integer(
        _required(phase, "largest_army_size", "snapshot.state.phase"),
        "snapshot.state.phase.largest_army_size",
        minimum=0,
        maximum=MAX_PLAYED_KNIGHTS,
    )
    return _PhaseData(
        name=name,
        special_phase=special_phase,
        action_mode=action_mode,
        dice_rolled=_boolean(
            _required(phase, "dice_rolled", "snapshot.state.phase"),
            "snapshot.state.phase.dice_rolled",
        ),
        current_actor_index=turn_order[current_position],
        winner_index=_optional_index(
            _required(phase, "winner", "snapshot.state.phase"),
            player_count,
            "snapshot.state.phase.winner",
        ),
        longest_road_owner=longest_road_owner,
        longest_road_length=longest_road_length,
        largest_army_owner=largest_army_owner,
        largest_army_size=largest_army_size,
    )


def _parse_victory_target(raw: Any) -> int:
    rules = _mapping(raw, "snapshot.state.rules")
    return _integer(
        _required(rules, "victory_point_target", "snapshot.state.rules"),
        "snapshot.state.rules.victory_point_target",
        minimum=MIN_VICTORY_TARGET,
        maximum=MAX_VICTORY_TARGET,
    )


def _parse_initial_context(raw: Any) -> tuple[bool, bool]:
    initial = _mapping(raw, "snapshot.state.initial")
    return (
        _boolean(
            _required(initial, "dice_phase", "snapshot.state.initial"),
            "snapshot.state.initial.dice_phase",
        ),
        _boolean(
            _required(initial, "waiting_for_road", "snapshot.state.initial"),
            "snapshot.state.initial.waiting_for_road",
        ),
    )


def _parse_special_context(
    raw: Any,
) -> tuple[int, int, int, Optional[str]]:
    special = _mapping(raw, "snapshot.state.special")
    bank_trade_resource = _required(
        special,
        "bank_trade_give_resource",
        "snapshot.state.special",
    )
    if bank_trade_resource is not None:
        bank_trade_resource = _enum_text(
            bank_trade_resource,
            frozenset(_RESOURCE_KEYS),
            "snapshot.state.special.bank_trade_give_resource",
        )
    return (
        _integer(
            _required(special, "discard_remaining", "snapshot.state.special"),
            "snapshot.state.special.discard_remaining",
            minimum=0,
            maximum=MAX_TOTAL_RESOURCE_CARDS,
        ),
        _integer(
            _required(
                special,
                "resource_selection_remaining",
                "snapshot.state.special",
            ),
            "snapshot.state.special.resource_selection_remaining",
            minimum=0,
            maximum=MAX_RESOURCE_SELECTION,
        ),
        _integer(
            _required(
                special,
                "free_roads_remaining",
                "snapshot.state.special",
            ),
            "snapshot.state.special.free_roads_remaining",
            minimum=0,
            maximum=MAX_FREE_ROADS,
        ),
        bank_trade_resource,
    )


def _parse_domestic_trade(raw: Any, player_count: int) -> DomesticTradeView:
    trade = _mapping(raw, "snapshot.state.domestic_trade")
    give = _counts(
        _required(trade, "give", "snapshot.state.domestic_trade"),
        _RESOURCE_KEYS,
        "snapshot.state.domestic_trade.give",
        maximum=MAX_BANK_RESOURCE_COUNT,
    )
    receive = _counts(
        _required(trade, "receive", "snapshot.state.domestic_trade"),
        _RESOURCE_KEYS,
        "snapshot.state.domestic_trade.receive",
        maximum=MAX_BANK_RESOURCE_COUNT,
    )
    if any(give[resource] and receive[resource] for resource in _RESOURCE_KEYS):
        raise NetworkViewError(
            "snapshot.state.domestic_trade cannot give and receive one resource"
        )
    return DomesticTradeView(
        partner_seat=_optional_seat(
            _required(trade, "partner", "snapshot.state.domestic_trade"),
            player_count,
            "snapshot.state.domestic_trade.partner",
        ),
        editor_seat=_optional_seat(
            _required(trade, "editor", "snapshot.state.domestic_trade"),
            player_count,
            "snapshot.state.domestic_trade.editor",
        ),
        give=give,
        receive=receive,
        edit_side=_enum_text(
            _required(trade, "edit_side", "snapshot.state.domestic_trade"),
            _DOMESTIC_TRADE_EDIT_SIDES,
            "snapshot.state.domestic_trade.edit_side",
        ),
        is_counter=_boolean(
            _required(trade, "is_counter", "snapshot.state.domestic_trade"),
            "snapshot.state.domestic_trade.is_counter",
        ),
        is_broadcast=_boolean(
            _required(trade, "is_broadcast", "snapshot.state.domestic_trade"),
            "snapshot.state.domestic_trade.is_broadcast",
        ),
    )


def _parse_board_manifest(
    raw: Any,
    *,
    player_count: int,
    expected_mode: str,
    expected_seed: int,
    expected_custom_map_fingerprint: Optional[str],
) -> BoardView:
    manifest = _mapping(raw, "snapshot.board_manifest")
    _validate_manifest_size(manifest)
    if manifest.get("format") != "catan-board-manifest" or manifest.get("version") != 1:
        raise NetworkViewError(
            "snapshot.board_manifest has an unsupported format/version"
        )
    mode = _enum_text(
        _required(manifest, "mode", "snapshot.board_manifest"),
        _BOARD_MODES,
        "snapshot.board_manifest.mode",
    )
    seed = _integer(
        _required(manifest, "seed", "snapshot.board_manifest"),
        "snapshot.board_manifest.seed",
        minimum=-MAX_SAFE_JSON_INTEGER,
        maximum=MAX_SAFE_JSON_INTEGER,
    )
    if mode != expected_mode or seed != expected_seed:
        raise NetworkViewError("board manifest mode/seed does not match snapshot state")
    if mode == "custom":
        custom_map_fingerprint = _fingerprint(
            _required(
                manifest,
                "custom_map_fingerprint",
                "snapshot.board_manifest",
            ),
            "snapshot.board_manifest.custom_map_fingerprint",
        )
        if custom_map_fingerprint != expected_custom_map_fingerprint:
            raise NetworkViewError(
                "board manifest custom map fingerprint does not match snapshot state"
            )
    else:
        if "custom_map_fingerprint" in manifest:
            raise NetworkViewError(
                "generated board manifest cannot contain a custom map fingerprint"
            )
        custom_map_fingerprint = None

    coordinate_space = _mapping(
        _required(manifest, "coordinate_space", "snapshot.board_manifest"),
        "snapshot.board_manifest.coordinate_space",
    )
    if coordinate_space.get("kind") != "board-pixels":
        raise NetworkViewError(
            "snapshot.board_manifest coordinate space is unsupported"
        )
    bounds_raw = _mapping(
        _required(
            coordinate_space,
            "bounds",
            "snapshot.board_manifest.coordinate_space",
        ),
        "snapshot.board_manifest.coordinate_space.bounds",
    )
    bounds = BoundsView(
        min_x=_coordinate(_required(bounds_raw, "min_x", "bounds"), "bounds.min_x"),
        max_x=_coordinate(_required(bounds_raw, "max_x", "bounds"), "bounds.max_x"),
        min_y=_coordinate(_required(bounds_raw, "min_y", "bounds"), "bounds.min_y"),
        max_y=_coordinate(_required(bounds_raw, "max_y", "bounds"), "bounds.max_y"),
    )
    if bounds.min_x > bounds.max_x or bounds.min_y > bounds.max_y:
        raise NetworkViewError("snapshot.board_manifest bounds are inverted")

    raw_tiles = _list(
        _required(manifest, "tiles", "snapshot.board_manifest"),
        "snapshot.board_manifest.tiles",
        minimum=1,
        maximum=MAX_TILES,
    )
    raw_nodes = _list(
        _required(manifest, "nodes", "snapshot.board_manifest"),
        "snapshot.board_manifest.nodes",
        minimum=1,
        maximum=MAX_NODES,
    )
    raw_edges = _list(
        _required(manifest, "edges", "snapshot.board_manifest"),
        "snapshot.board_manifest.edges",
        minimum=1,
        maximum=MAX_EDGES,
    )
    raw_harbors = _list(
        _required(manifest, "harbors", "snapshot.board_manifest"),
        "snapshot.board_manifest.harbors",
        minimum=0,
        maximum=MAX_HARBORS,
    )

    tiles = tuple(
        _parse_tile(item, index, bounds) for index, item in enumerate(raw_tiles)
    )
    nodes = tuple(
        _parse_node(item, index, bounds, player_count)
        for index, item in enumerate(raw_nodes)
    )
    edges = tuple(
        _parse_edge(item, index, player_count) for index, item in enumerate(raw_edges)
    )
    harbors = tuple(
        _parse_harbor(item, index) for index, item in enumerate(raw_harbors)
    )
    tile_by_id = _unique_lookup(tiles, "tile")
    node_by_id = _unique_lookup(nodes, "node")
    edge_by_id = _unique_lookup(edges, "edge")
    harbor_by_id = _unique_lookup(harbors, "harbor")
    _validate_manifest_references(tile_by_id, node_by_id, edge_by_id, harbor_by_id)
    _validate_geometry(bounds, tiles, nodes)

    positions: dict[str, PointView] = {}
    positions.update({tile.target_id: tile.center for tile in tiles})
    positions.update({node.target_id: node.position for node in nodes})
    for edge in edges:
        first = node_by_id[edge.node_ids[0]].position
        second = node_by_id[edge.node_ids[1]].position
        positions[edge.target_id] = _midpoint(first, second)
    for harbor in harbors:
        first = node_by_id[harbor.node_ids[0]].position
        second = node_by_id[harbor.node_ids[1]].position
        positions[harbor.target_id] = _midpoint(first, second)
    return BoardView(
        mode=mode,
        seed=seed,
        custom_map_fingerprint=custom_map_fingerprint,
        bounds=bounds,
        tiles=tiles,
        nodes=nodes,
        edges=edges,
        harbors=harbors,
        _tile_by_id=MappingProxyType(tile_by_id),
        _node_by_id=MappingProxyType(node_by_id),
        _edge_by_id=MappingProxyType(edge_by_id),
        _harbor_by_id=MappingProxyType(harbor_by_id),
        _position_by_id=MappingProxyType(positions),
    )


def _parse_tile(raw: Any, index: int, bounds: BoundsView) -> TileView:
    label = f"snapshot.board_manifest.tiles[{index}]"
    tile = _mapping(raw, label)
    target_id = _target_id(_required(tile, "id", label), "tile", f"{label}.id")
    axial = _mapping(_required(tile, "axial", label), f"{label}.axial")
    axial_pair = (
        _integer(
            _required(axial, "q", f"{label}.axial"),
            f"{label}.axial.q",
            minimum=-1000,
            maximum=1000,
        ),
        _integer(
            _required(axial, "r", f"{label}.axial"),
            f"{label}.axial.r",
            minimum=-1000,
            maximum=1000,
        ),
    )
    center = _point(_required(tile, "center", label), f"{label}.center")
    if not bounds.contains(center):
        raise NetworkViewError(f"{label}.center is outside manifest bounds")
    resource = _enum_text(
        _required(tile, "resource", label),
        _TILE_RESOURCES,
        f"{label}.resource",
    )
    number = tile.get("number")
    if resource == "DESERT":
        if number is not None:
            raise NetworkViewError(f"{label}.number must be null for desert")
    else:
        number = _integer(number, f"{label}.number", minimum=2, maximum=12)
        if number == 7:
            raise NetworkViewError(f"{label}.number cannot be 7")
    corners = _id_list(
        _required(tile, "corner_node_ids", label),
        "node",
        f"{label}.corner_node_ids",
        minimum=6,
        maximum=6,
    )
    return TileView(
        target_id=target_id,
        axial=axial_pair,
        center=center,
        resource=resource,
        number=number,
        corner_node_ids=corners,
        robber=_boolean(_required(tile, "robber", label), f"{label}.robber"),
    )


def _parse_node(
    raw: Any, index: int, bounds: BoundsView, player_count: int
) -> NodeView:
    label = f"snapshot.board_manifest.nodes[{index}]"
    node = _mapping(raw, label)
    position = _point(_required(node, "position", label), f"{label}.position")
    if not bounds.contains(position):
        raise NetworkViewError(f"{label}.position is outside manifest bounds")
    building_raw = _required(node, "building", label)
    building = None
    if building_raw is not None:
        building_raw = _mapping(building_raw, f"{label}.building")
        building = BuildingView(
            owner_seat=_owner_seat(building_raw, player_count, f"{label}.building"),
            building_type=_enum_text(
                _required(building_raw, "type", f"{label}.building"),
                _BUILDING_TYPES,
                f"{label}.building.type",
            ),
        )
    return NodeView(
        target_id=_target_id(_required(node, "id", label), "node", f"{label}.id"),
        position=position,
        adjacent_tile_ids=_id_list(
            _required(node, "adjacent_tile_ids", label),
            "tile",
            f"{label}.adjacent_tile_ids",
            minimum=1,
            maximum=3,
        ),
        adjacent_node_ids=_id_list(
            _required(node, "adjacent_node_ids", label),
            "node",
            f"{label}.adjacent_node_ids",
            minimum=2,
            maximum=3,
        ),
        edge_ids=_id_list(
            _required(node, "edge_ids", label),
            "edge",
            f"{label}.edge_ids",
            minimum=2,
            maximum=3,
        ),
        harbor_ids=_id_list(
            _required(node, "harbor_ids", label),
            "harbor",
            f"{label}.harbor_ids",
            minimum=0,
            maximum=1,
        ),
        building=building,
    )


def _parse_edge(raw: Any, index: int, player_count: int) -> EdgeView:
    label = f"snapshot.board_manifest.edges[{index}]"
    edge = _mapping(raw, label)
    road_raw = _required(edge, "road", label)
    road = None
    if road_raw is not None:
        road_raw = _mapping(road_raw, f"{label}.road")
        road = RoadView(owner_seat=_owner_seat(road_raw, player_count, f"{label}.road"))
    harbor_id = _required(edge, "harbor_id", label)
    if harbor_id is not None:
        harbor_id = _target_id(harbor_id, "harbor", f"{label}.harbor_id")
    node_ids = _id_list(
        _required(edge, "node_ids", label),
        "node",
        f"{label}.node_ids",
        minimum=2,
        maximum=2,
    )
    return EdgeView(
        target_id=_target_id(_required(edge, "id", label), "edge", f"{label}.id"),
        node_ids=(node_ids[0], node_ids[1]),
        adjacent_tile_ids=_id_list(
            _required(edge, "adjacent_tile_ids", label),
            "tile",
            f"{label}.adjacent_tile_ids",
            minimum=1,
            maximum=2,
        ),
        perimeter=_boolean(_required(edge, "perimeter", label), f"{label}.perimeter"),
        road=road,
        harbor_id=harbor_id,
    )


def _parse_harbor(raw: Any, index: int) -> HarborView:
    label = f"snapshot.board_manifest.harbors[{index}]"
    harbor = _mapping(raw, label)
    trade_rate = _integer(
        _required(harbor, "trade_rate", label),
        f"{label}.trade_rate",
        minimum=2,
        maximum=3,
    )
    resource = _required(harbor, "resource", label)
    if resource is not None:
        resource = _enum_text(resource, frozenset(_RESOURCE_KEYS), f"{label}.resource")
    if (trade_rate == 2) != (resource is not None):
        raise NetworkViewError(f"{label} trade_rate/resource combination is invalid")
    node_ids = _id_list(
        _required(harbor, "node_ids", label),
        "node",
        f"{label}.node_ids",
        minimum=2,
        maximum=2,
    )
    return HarborView(
        target_id=_target_id(_required(harbor, "id", label), "harbor", f"{label}.id"),
        edge_id=_target_id(
            _required(harbor, "edge_id", label), "edge", f"{label}.edge_id"
        ),
        node_ids=(node_ids[0], node_ids[1]),
        trade_rate=trade_rate,
        resource=resource,
        label=_text(_required(harbor, "label", label), f"{label}.label", maximum=32),
    )


def _validate_manifest_references(
    tiles: Mapping[str, TileView],
    nodes: Mapping[str, NodeView],
    edges: Mapping[str, EdgeView],
    harbors: Mapping[str, HarborView],
) -> None:
    robber_count = sum(tile.robber for tile in tiles.values())
    if robber_count != 1:
        raise NetworkViewError("board manifest must contain exactly one robber")

    for tile in tiles.values():
        _require_references(
            tile.corner_node_ids, nodes, f"{tile.target_id}.corner_node_ids"
        )
        for node_id in tile.corner_node_ids:
            if tile.target_id not in nodes[node_id].adjacent_tile_ids:
                raise NetworkViewError("tile/node adjacency is not reciprocal")

    for node in nodes.values():
        _require_references(
            node.adjacent_tile_ids, tiles, f"{node.target_id}.adjacent_tile_ids"
        )
        _require_references(
            node.adjacent_node_ids, nodes, f"{node.target_id}.adjacent_node_ids"
        )
        _require_references(node.edge_ids, edges, f"{node.target_id}.edge_ids")
        _require_references(node.harbor_ids, harbors, f"{node.target_id}.harbor_ids")
        if node.target_id in node.adjacent_node_ids:
            raise NetworkViewError("a node cannot be adjacent to itself")
        for tile_id in node.adjacent_tile_ids:
            if node.target_id not in tiles[tile_id].corner_node_ids:
                raise NetworkViewError("node/tile adjacency is not reciprocal")
        for edge_id in node.edge_ids:
            if node.target_id not in edges[edge_id].node_ids:
                raise NetworkViewError("node lists an edge that does not touch it")
        for harbor_id in node.harbor_ids:
            if node.target_id not in harbors[harbor_id].node_ids:
                raise NetworkViewError("node lists a harbor that does not touch it")
        for adjacent_id in node.adjacent_node_ids:
            if node.target_id not in nodes[adjacent_id].adjacent_node_ids:
                raise NetworkViewError("node adjacency is not reciprocal")
            if not any(
                frozenset(edges[edge_id].node_ids)
                == frozenset((node.target_id, adjacent_id))
                for edge_id in node.edge_ids
            ):
                raise NetworkViewError("adjacent nodes have no matching edge")

    endpoint_pairs = [frozenset(edge.node_ids) for edge in edges.values()]
    if len(set(endpoint_pairs)) != len(endpoint_pairs):
        raise NetworkViewError("board manifest contains duplicate edge endpoints")
    for edge in edges.values():
        _require_references(edge.node_ids, nodes, f"{edge.target_id}.node_ids")
        _require_references(
            edge.adjacent_tile_ids, tiles, f"{edge.target_id}.adjacent_tile_ids"
        )
        if edge.perimeter != (len(edge.adjacent_tile_ids) == 1):
            raise NetworkViewError("edge perimeter flag disagrees with adjacent tiles")
        first, second = edge.node_ids
        if (
            edge.target_id not in nodes[first].edge_ids
            or edge.target_id not in nodes[second].edge_ids
        ):
            raise NetworkViewError("edge/node reference is not reciprocal")
        if (
            second not in nodes[first].adjacent_node_ids
            or first not in nodes[second].adjacent_node_ids
        ):
            raise NetworkViewError("edge endpoints are not adjacent nodes")
        for tile_id in edge.adjacent_tile_ids:
            tile_nodes = set(tiles[tile_id].corner_node_ids)
            if first not in tile_nodes or second not in tile_nodes:
                raise NetworkViewError(
                    "edge/tile reference is geometrically inconsistent"
                )
        if edge.harbor_id is not None:
            _require_references(
                (edge.harbor_id,), harbors, f"{edge.target_id}.harbor_id"
            )
            if harbors[edge.harbor_id].edge_id != edge.target_id:
                raise NetworkViewError("edge/harbor reference is not reciprocal")

    for harbor in harbors.values():
        _require_references((harbor.edge_id,), edges, f"{harbor.target_id}.edge_id")
        _require_references(harbor.node_ids, nodes, f"{harbor.target_id}.node_ids")
        edge = edges[harbor.edge_id]
        if edge.harbor_id != harbor.target_id or frozenset(edge.node_ids) != frozenset(
            harbor.node_ids
        ):
            raise NetworkViewError("harbor/edge reference is inconsistent")
        for node_id in harbor.node_ids:
            if harbor.target_id not in nodes[node_id].harbor_ids:
                raise NetworkViewError("harbor/node reference is not reciprocal")


def _validate_geometry(
    bounds: BoundsView,
    tiles: Sequence[TileView],
    nodes: Sequence[NodeView],
) -> None:
    node_positions = {(node.position.x, node.position.y) for node in nodes}
    if len(node_positions) != len(nodes):
        raise NetworkViewError("board manifest contains duplicate node coordinates")
    tile_axials = {tile.axial for tile in tiles}
    if len(tile_axials) != len(tiles):
        raise NetworkViewError(
            "board manifest contains duplicate tile axial coordinates"
        )
    expected = (
        min(node.position.x for node in nodes),
        max(node.position.x for node in nodes),
        min(node.position.y for node in nodes),
        max(node.position.y for node in nodes),
    )
    actual = (bounds.min_x, bounds.max_x, bounds.min_y, bounds.max_y)
    if any(
        not math.isclose(a, b, rel_tol=0.0, abs_tol=1e-6)
        for a, b in zip(expected, actual)
    ):
        raise NetworkViewError("board manifest bounds do not match node coordinates")


def _parse_logs(raw_history: Any) -> tuple[str, ...]:
    history = _mapping(raw_history, "snapshot.state.history")
    raw_logs = _required(history, "log_messages", "snapshot.state.history")
    logs = _list(
        raw_logs,
        "snapshot.state.history.log_messages",
        minimum=0,
        maximum=MAX_INPUT_LOG_MESSAGES,
    )
    validated = tuple(
        _text(
            message,
            f"snapshot.state.history.log_messages[{index}]",
            maximum=MAX_TEXT_LENGTH,
        )
        for index, message in enumerate(logs)
    )
    return validated[-MAX_VIEW_LOG_MESSAGES:]


def _counts(
    raw: Any,
    expected_keys: Sequence[str],
    label: str,
    *,
    maximum: int = MAX_COUNT,
) -> FrozenCounts:
    values = _mapping(raw, label)
    if set(values) != set(expected_keys):
        raise NetworkViewError(f"{label} has missing or unknown keys")
    return FrozenCounts(
        tuple(
            (
                key,
                _integer(values[key], f"{label}.{key}", minimum=0, maximum=maximum),
            )
            for key in expected_keys
        )
    )


def _unique_lookup(items: Sequence[Any], kind: str) -> dict[str, Any]:
    result = {}
    for item in items:
        if item.target_id in result:
            raise NetworkViewError(f"duplicate {kind} target id: {item.target_id}")
        result[item.target_id] = item
    return result


def _require_references(
    ids: Sequence[str], targets: Mapping[str, Any], label: str
) -> None:
    if any(target_id not in targets for target_id in ids):
        raise NetworkViewError(f"{label} contains an unknown target id")


def _id_list(
    raw: Any,
    kind: str,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> tuple[str, ...]:
    values = _list(raw, label, minimum=minimum, maximum=maximum)
    result = tuple(
        _target_id(value, kind, f"{label}[{index}]")
        for index, value in enumerate(values)
    )
    if len(set(result)) != len(result):
        raise NetworkViewError(f"{label} contains duplicate IDs")
    return result


def _target_id(value: Any, kind: str, label: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERNS[kind].fullmatch(value):
        raise NetworkViewError(f"{label} is not a stable {kind} ID")
    return value


def _fingerprint(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _FINGERPRINT_PATTERN.fullmatch(value):
        raise NetworkViewError(f"{label} must be a lowercase SHA-256 fingerprint")
    return value


def _owner_seat(raw: Mapping[str, Any], player_count: int, label: str) -> int:
    owner_index = _integer(
        _required(raw, "owner_player_index", label),
        f"{label}.owner_player_index",
        minimum=0,
        maximum=player_count - 1,
    )
    return owner_index + 1


def _point(raw: Any, label: str) -> PointView:
    value = _mapping(raw, label)
    return PointView(
        _coordinate(_required(value, "x", label), f"{label}.x"),
        _coordinate(_required(value, "y", label), f"{label}.y"),
    )


def _midpoint(first: PointView, second: PointView) -> PointView:
    return PointView((first.x + second.x) / 2, (first.y + second.y) / 2)


def _validate_manifest_size(manifest: Mapping[str, Any]) -> None:
    try:
        encoded = json.dumps(
            manifest,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise NetworkViewError("snapshot.board_manifest is not safe JSON") from exc
    if len(encoded) > MAX_BOARD_MANIFEST_BYTES:
        raise NetworkViewError("snapshot.board_manifest exceeds the size limit")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NetworkViewError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise NetworkViewError(f"{label} keys must be strings")
    return value


def _list(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise NetworkViewError(f"{label} must contain {minimum}..{maximum} items")
    return value


def _required(mapping: Mapping[str, Any], key: str, label: str) -> Any:
    if key not in mapping:
        raise NetworkViewError(f"{label}.{key} is required")
    return mapping[key]


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise NetworkViewError(f"{label} is outside the accepted integer range")
    return value


def _optional_index(value: Any, player_count: int, label: str) -> Optional[int]:
    if value is None:
        return None
    return _integer(value, label, minimum=0, maximum=player_count - 1)


def _optional_seat(value: Any, player_count: int, label: str) -> Optional[int]:
    index = _optional_index(value, player_count, label)
    return None if index is None else index + 1


def _coordinate(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NetworkViewError(f"{label} must be a finite coordinate")
    result = float(value)
    if not math.isfinite(result) or abs(result) > MAX_COORDINATE:
        raise NetworkViewError(f"{label} must be a finite coordinate")
    return result


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise NetworkViewError(f"{label} must be a boolean")
    return value


def _enum_text(value: Any, allowed: frozenset[Any], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise NetworkViewError(f"{label} has an unsupported value")
    return value


def _text(value: Any, label: str, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise NetworkViewError(f"{label} is invalid text")
    return value
