"""Read-only Pygame presentation for an authoritative LAN match.

The module consumes :class:`~game.network_view.NetworkGameView` and semantic
``command_options`` supplied by the server.  It deliberately has no socket,
``CatanGame``, or mutation dependency: every clickable command is copied from
the option list and board clicks send stable manifest IDs instead of screen
coordinates.  The same boundary keeps spectators read-only even if a caller
accidentally passes stale options.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
import json
import math
from types import MappingProxyType
from typing import Any

import pygame

from game.assets import get_font
from game.constants import COLORS
from game.network_view import NetworkGameView, PointView
from game.resources import ResourceType
from game.tile_art import get_tile_surface


DEFAULT_LAN_MATCH_SIZE = (1200, 800)
BUILD_PIECES = ("road", "settlement", "city")
RESOURCE_ORDER = ("WOOD", "SHEEP", "WHEAT", "BRICK", "ORE")

_RESOURCE_LABELS = {
    "WOOD": "木",
    "SHEEP": "羊",
    "WHEAT": "麦",
    "BRICK": "土",
    "ORE": "鉄",
    "DESERT": "砂漠",
}
_RESOURCE_COLORS = {
    "WOOD": (62, 129, 75),
    "SHEEP": (126, 178, 75),
    "WHEAT": (221, 174, 62),
    "BRICK": (177, 91, 64),
    "ORE": (126, 136, 149),
    "DESERT": (214, 178, 111),
}
_PIECE_LABELS = {
    "road": "街道",
    "settlement": "開拓地",
    "city": "都市",
}
_CARD_LABELS = {
    "knight": "騎士",
    "road_building": "街道建設",
    "year_of_plenty": "収穫",
    "monopoly": "独占",
}
_SIMPLE_COMMAND_LABELS = {
    "roll_dice": "サイコロを振る",
    "end_turn": "手番終了",
    "cancel": "戻る / 中止",
    "buy_development": "発展カードを購入",
    "start_bank_trade": "銀行・港と交易",
    "start_domestic_trade": "プレイヤーと交渉",
    "trade_broadcast": "全員へ呼びかけ",
    "trade_submit": "条件を提示",
    "trade_reveal": "提案内容を確認",
    "trade_accept": "交渉を承諾",
    "trade_counter": "条件を変更",
    "trade_reject": "交渉を断る",
    "finish_road_building": "街道建設を終了",
}
_BOARD_COMMANDS = frozenset(("initial_place", "move_robber", "build"))
_SUPPORTED_COMMANDS = frozenset(
    {
        *_SIMPLE_COMMAND_LABELS,
        *_BOARD_COMMANDS,
        "steal",
        "select_resource",
        "trade_partner",
        "trade_edit_side",
        "trade_adjust",
        "use_development",
    }
)
_EMPTY_ARG_COMMANDS = frozenset(_SIMPLE_COMMAND_LABELS)


@dataclass(frozen=True)
class LanMatchDisplayState:
    """Immutable input to the LAN match renderer."""

    view: NetworkGameView
    command_options: Sequence[Mapping[str, Any]] = ()
    selected_build_piece: str | None = None
    room_code: str = ""
    connected: bool = True
    error: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.view, NetworkGameView):
            raise TypeError("view must be a NetworkGameView")
        if self.selected_build_piece not in (None, *BUILD_PIECES):
            raise ValueError("selected_build_piece is invalid")


@dataclass(frozen=True)
class LanMatchControl:
    """One side-panel action or local build-piece selector."""

    action_id: str
    label: str
    rect: pygame.Rect
    kind: str
    enabled: bool
    selected: bool = False
    command: str | None = None
    args: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    build_piece: str | None = None


@dataclass(frozen=True)
class LanMatchBoardTarget:
    """A legal stable-ID board target copied from one command option."""

    target_id: str
    target_kind: str
    center: tuple[int, int]
    hit_radius: int
    command: str
    args: Mapping[str, Any]
    segment: tuple[tuple[int, int], tuple[int, int]] | None = None


@dataclass(frozen=True)
class LanMatchHarborLayout:
    harbor_id: str
    rect: pygame.Rect
    anchor: tuple[int, int]
    connector_end: tuple[int, int]


@dataclass(frozen=True)
class LanMatchPlayerLayout:
    seat: int
    rect: pygame.Rect


@dataclass(frozen=True)
class LanMatchBoardTransform:
    scale: float
    center_x: float
    center_y: float
    source_center_x: float
    source_center_y: float
    tile_radius: int

    def point(self, value: PointView) -> tuple[int, int]:
        return (
            round(self.center_x + (value.x - self.source_center_x) * self.scale),
            round(self.center_y + (value.y - self.source_center_y) * self.scale),
        )


@dataclass(frozen=True)
class LanMatchLayout:
    screen_rect: pygame.Rect
    header_rect: pygame.Rect
    board_rect: pygame.Rect
    side_rect: pygame.Rect
    guidance_rect: pygame.Rect
    action_rect: pygame.Rect
    log_rect: pygame.Rect
    players_rect: pygame.Rect
    transform: LanMatchBoardTransform
    controls: tuple[LanMatchControl, ...]
    board_targets: tuple[LanMatchBoardTarget, ...]
    harbors: tuple[LanMatchHarborLayout, ...]
    players: tuple[LanMatchPlayerLayout, ...]

    @property
    def control_by_action(self) -> dict[str, LanMatchControl]:
        return {control.action_id: control for control in self.controls}


@dataclass(frozen=True)
class LanMatchHitTarget:
    """Result of local hit testing.

    ``kind == 'command'`` always carries an exact server-issued semantic
    command and arguments.  ``kind == 'select_build_piece'`` is presentation
    state only and never goes on the wire.
    """

    kind: str
    command: str | None = None
    args: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    build_piece: str | None = None


@dataclass(frozen=True)
class _Option:
    command: str
    args: Mapping[str, Any]


def build_lan_match_layout(
    size: Sequence[int], state: LanMatchDisplayState
) -> LanMatchLayout:
    """Build deterministic, collision-safe rectangles for the match screen."""

    width, height = _validated_size(size)
    screen_rect = pygame.Rect(0, 0, width, height)
    margin = _clamp(round(min(width, height) * 0.016), 10, 22)
    gap = _clamp(round(min(width, height) * 0.012), 8, 16)
    header_height = _clamp(round(height * 0.105), 72, 112)
    player_height = _clamp(round(height * 0.175), 118, 174)

    header_rect = pygame.Rect(
        margin,
        margin,
        width - margin * 2,
        header_height,
    )
    players_rect = pygame.Rect(
        margin,
        height - margin - player_height,
        width - margin * 2,
        player_height,
    )
    body_top = header_rect.bottom + gap
    body_bottom = players_rect.top - gap
    side_width = _clamp(round(width * 0.275), 314, 430)
    side_rect = pygame.Rect(
        width - margin - side_width,
        body_top,
        side_width,
        body_bottom - body_top,
    )
    board_rect = pygame.Rect(
        margin,
        body_top,
        side_rect.left - gap - margin,
        body_bottom - body_top,
    )

    side_padding = _clamp(round(side_width * 0.045), 12, 20)
    guidance_height = _clamp(round(side_rect.height * 0.22), 86, 126)
    guidance_rect = pygame.Rect(
        side_rect.x + side_padding,
        side_rect.y + side_padding + 27,
        side_rect.width - side_padding * 2,
        guidance_height,
    )
    log_height = _clamp(round(side_rect.height * 0.21), 76, 124)
    log_rect = pygame.Rect(
        side_rect.x + side_padding,
        side_rect.bottom - side_padding - log_height,
        side_rect.width - side_padding * 2,
        log_height,
    )
    action_rect = pygame.Rect(
        guidance_rect.x,
        guidance_rect.bottom + gap,
        guidance_rect.width,
        max(24, log_rect.top - gap - guidance_rect.bottom - gap),
    )

    transform = _build_board_transform(board_rect, state.view)
    options = _usable_options(state)
    controls = _build_controls(action_rect, state, options)
    board_targets = _build_board_targets(state, options, transform)
    harbors = _build_harbor_layouts(board_rect, state.view, transform)
    players = _build_player_layouts(players_rect, state.view, gap)
    return LanMatchLayout(
        screen_rect=screen_rect,
        header_rect=header_rect,
        board_rect=board_rect,
        side_rect=side_rect,
        guidance_rect=guidance_rect,
        action_rect=action_rect,
        log_rect=log_rect,
        players_rect=players_rect,
        transform=transform,
        controls=controls,
        board_targets=board_targets,
        harbors=harbors,
        players=players,
    )


def hit_test_lan_match_display(
    layout: LanMatchLayout, pos: Sequence[int]
) -> LanMatchHitTarget | None:
    """Return a local selection or exact semantic command under ``pos``."""

    if not isinstance(pos, Sequence) or len(pos) != 2:
        return None
    try:
        point = (int(pos[0]), int(pos[1]))
    except (TypeError, ValueError, OverflowError):
        return None

    for control in reversed(layout.controls):
        if not control.enabled or not control.rect.collidepoint(point):
            continue
        if control.kind == "select_build_piece":
            return LanMatchHitTarget(
                kind="select_build_piece",
                build_piece=control.build_piece,
            )
        return LanMatchHitTarget(
            kind="command",
            command=control.command,
            args=control.args,
        )

    candidates: list[tuple[float, str, LanMatchBoardTarget]] = []
    for target in layout.board_targets:
        score = _target_hit_score(target, point)
        if score is not None:
            candidates.append((score, target.target_id, target))
    if not candidates:
        return None
    target = min(candidates, key=lambda item: (item[0], item[1]))[2]
    return LanMatchHitTarget(
        kind="command",
        command=target.command,
        args=target.args,
    )


def draw_lan_match_display(
    surface: pygame.Surface, state: LanMatchDisplayState
) -> LanMatchLayout:
    """Draw a complete LAN match and return its deterministic hit layout."""

    if not isinstance(surface, pygame.Surface):
        raise TypeError("surface must be a pygame.Surface")
    layout = build_lan_match_layout(surface.get_size(), state)
    surface.blit(_background_surface(surface.get_size()), (0, 0))
    _draw_panel(surface, layout.header_rect, radius=17)
    _draw_panel(surface, layout.board_rect, radius=20, water=True)
    _draw_panel(surface, layout.side_rect, radius=18)
    _draw_panel(surface, layout.players_rect, radius=16)

    _draw_header(surface, layout, state)
    _draw_board(surface, layout, state)
    _draw_side(surface, layout, state)
    _draw_players(surface, layout, state)
    if not state.connected:
        _draw_connection_overlay(surface, layout, state)
    elif state.error:
        _draw_error_banner(surface, layout, state.error)
    return layout


def _build_board_transform(
    rect: pygame.Rect, view: NetworkGameView
) -> LanMatchBoardTransform:
    bounds = view.board.bounds
    source_width = max(1.0, bounds.max_x - bounds.min_x)
    source_height = max(1.0, bounds.max_y - bounds.min_y)
    pad_x = _clamp(round(rect.width * 0.105), 58, 112)
    pad_y = _clamp(round(rect.height * 0.125), 46, 90)
    scale = min(
        max(1.0, rect.width - pad_x * 2) / source_width,
        max(1.0, rect.height - pad_y * 2) / source_height,
        1.52,
    )
    scale = max(0.05, scale)
    edge_lengths = []
    for edge in view.board.edges[:24]:
        start, end = view.board.edge_segment(edge.target_id)
        edge_lengths.append(math.hypot(end.x - start.x, end.y - start.y))
    source_radius = sorted(edge_lengths)[len(edge_lengths) // 2] if edge_lengths else 50
    tile_radius = _clamp(round(source_radius * scale), 16, 78)
    return LanMatchBoardTransform(
        scale=scale,
        center_x=rect.centerx,
        center_y=rect.centery + _clamp(round(rect.height * 0.01), 0, 8),
        source_center_x=(bounds.min_x + bounds.max_x) / 2,
        source_center_y=(bounds.min_y + bounds.max_y) / 2,
        tile_radius=tile_radius,
    )


def _usable_options(state: LanMatchDisplayState) -> tuple[_Option, ...]:
    if state.view.viewer_seat is None:
        return ()
    if not isinstance(state.command_options, Sequence) or isinstance(
        state.command_options, (str, bytes, bytearray)
    ):
        return ()
    options: list[_Option] = []
    seen: set[tuple[str, str]] = set()
    for raw in tuple(state.command_options)[:512]:
        if not isinstance(raw, Mapping):
            continue
        command = raw.get("command")
        args = raw.get("args")
        if (
            type(command) is not str
            or command not in _SUPPORTED_COMMANDS
            or not isinstance(args, Mapping)
        ):
            continue
        copied: dict[str, Any] = {}
        valid = True
        for key, value in args.items():
            if (
                type(key) is not str
                or len(key) > 64
                or type(value) not in (str, int)
                or (isinstance(value, str) and len(value) > 128)
            ):
                valid = False
                break
            copied[key] = value
        if not valid:
            continue
        if not _option_args_well_formed(state.view, command, copied):
            continue
        canonical = json.dumps(copied, ensure_ascii=True, sort_keys=True)
        signature = (command, canonical)
        if signature in seen:
            continue
        seen.add(signature)
        options.append(_Option(command, MappingProxyType(copied)))
    return tuple(options)


def _option_args_well_formed(
    view: NetworkGameView,
    command: str,
    args: Mapping[str, Any],
) -> bool:
    """Fail closed when a malformed server descriptor reaches presentation."""

    if command in _EMPTY_ARG_COMMANDS:
        return not args
    if command == "build":
        if set(args) != {"piece", "target"} or args.get("piece") not in BUILD_PIECES:
            return False
        target = args.get("target")
        expected = (
            view.board.edge_by_id
            if args["piece"] == "road"
            else view.board.node_by_id
        )
        return type(target) is str and target in expected
    if command == "initial_place":
        return (
            set(args) == {"target"}
            and type(args.get("target")) is str
            and (
                args["target"] in view.board.node_by_id
                or args["target"] in view.board.edge_by_id
            )
        )
    if command == "move_robber":
        return (
            set(args) == {"target"}
            and type(args.get("target")) is str
            and args["target"] in view.board.tile_by_id
        )
    if command in ("steal", "trade_partner"):
        return (
            set(args) == {"seat_index"}
            and type(args.get("seat_index")) is int
            and 0 <= args["seat_index"] < len(view.players)
        )
    if command == "select_resource":
        return set(args) == {"resource"} and args.get("resource") in RESOURCE_ORDER
    if command == "trade_edit_side":
        return set(args) == {"side"} and args.get("side") in ("give", "receive")
    if command == "trade_adjust":
        return (
            set(args) == {"side", "resource", "delta"}
            and args.get("side") in ("give", "receive")
            and args.get("resource") in RESOURCE_ORDER
            and args.get("delta") in (-1, 1)
        )
    if command == "use_development":
        return set(args) == {"card"} and args.get("card") in _CARD_LABELS
    return False


def _build_controls(
    rect: pygame.Rect,
    state: LanMatchDisplayState,
    options: tuple[_Option, ...],
) -> tuple[LanMatchControl, ...]:
    if any(option.command == "trade_adjust" for option in options):
        return _build_trade_editor_controls(rect, state, options)

    specs: list[tuple[str, str, _Option | None, str | None, bool]] = []
    build_options = [option for option in options if option.command == "build"]
    build_pieces = [
        piece
        for piece in BUILD_PIECES
        if any(option.args.get("piece") == piece for option in build_options)
    ]
    for piece in build_pieces:
        specs.append(
            (
                f"select_build_piece:{piece}",
                _PIECE_LABELS[piece],
                None,
                piece,
                state.selected_build_piece == piece
                or (state.selected_build_piece is None and len(build_pieces) == 1),
            )
        )

    for index, option in enumerate(options):
        if option.command in _BOARD_COMMANDS:
            continue
        specs.append(
            (
                _command_action_id(index, option),
                _command_label(state.view, option),
                option,
                None,
                False,
            )
        )
    if not specs:
        return ()

    columns = (
        1
        if len(specs) <= 5
        else 2
        if len(specs) <= 14
        else 3
        if len(specs) <= 18
        else 4
    )
    rows = math.ceil(len(specs) / columns)
    column_gap = 5
    row_gap = 4
    cell_width = max(24, (rect.width - column_gap * (columns - 1)) // columns)
    cell_height = min(
        38,
        max(18, (rect.height - row_gap * (rows - 1)) // rows),
    )
    total_height = rows * cell_height + (rows - 1) * row_gap
    y_start = rect.y + max(0, (rect.height - total_height) // 2)
    controls = []
    for index, (action_id, label, option, piece, selected) in enumerate(specs):
        row, column = divmod(index, columns)
        cell = pygame.Rect(
            rect.x + column * (cell_width + column_gap),
            y_start + row * (cell_height + row_gap),
            cell_width,
            cell_height,
        )
        controls.append(
            LanMatchControl(
                action_id=action_id,
                label=label,
                rect=cell,
                kind="select_build_piece" if piece else "command",
                enabled=state.connected,
                selected=selected,
                command=None if option is None else option.command,
                args=(
                    MappingProxyType({})
                    if option is None
                    else option.args
                ),
                build_piece=piece,
            )
        )
    return tuple(controls)


def _build_trade_editor_controls(
    rect: pygame.Rect,
    state: LanMatchDisplayState,
    options: tuple[_Option, ...],
) -> tuple[LanMatchControl, ...]:
    """Give dense +/- trade controls height without shrinking final actions."""

    indexed = tuple(enumerate(options))
    adjustments = tuple(
        item for item in indexed if item[1].command == "trade_adjust"
    )
    primary = tuple(
        item for item in indexed if item[1].command != "trade_adjust"
    )
    if not adjustments:
        return ()

    gap = 5
    adjustment_columns = 4
    adjustment_rows = math.ceil(len(adjustments) / adjustment_columns)
    primary_height = min(38, max(24, rect.height // 7)) if primary else 0
    adjustment_height = rect.height - primary_height - (gap if primary else 0)
    row_gap = 4
    column_gap = 5
    cell_width = max(
        24,
        (rect.width - column_gap * (adjustment_columns - 1))
        // adjustment_columns,
    )
    cell_height = min(
        38,
        max(
            18,
            (adjustment_height - row_gap * (adjustment_rows - 1))
            // adjustment_rows,
        ),
    )
    grid_height = adjustment_rows * cell_height + row_gap * (
        adjustment_rows - 1
    )
    grid_y = rect.y + max(0, (adjustment_height - grid_height) // 2)
    controls = []

    for position, (index, option) in enumerate(adjustments):
        row, column = divmod(position, adjustment_columns)
        controls.append(
            LanMatchControl(
                action_id=_command_action_id(index, option),
                label=_command_label(state.view, option),
                rect=pygame.Rect(
                    rect.x + column * (cell_width + column_gap),
                    grid_y + row * (cell_height + row_gap),
                    cell_width,
                    cell_height,
                ),
                kind="command",
                enabled=state.connected,
                command=option.command,
                args=option.args,
            )
        )

    if primary:
        primary_y = rect.bottom - primary_height
        primary_gap = 6
        primary_width = max(
            24,
            (rect.width - primary_gap * (len(primary) - 1)) // len(primary),
        )
        for position, (index, option) in enumerate(primary):
            controls.append(
                LanMatchControl(
                    action_id=_command_action_id(index, option),
                    label=_command_label(state.view, option),
                    rect=pygame.Rect(
                        rect.x + position * (primary_width + primary_gap),
                        primary_y,
                        primary_width,
                        primary_height,
                    ),
                    kind="command",
                    enabled=state.connected,
                    command=option.command,
                    args=option.args,
                )
            )
    return tuple(controls)


def _build_board_targets(
    state: LanMatchDisplayState,
    options: tuple[_Option, ...],
    transform: LanMatchBoardTransform,
) -> tuple[LanMatchBoardTarget, ...]:
    if not state.connected:
        return ()
    build_pieces = {
        option.args.get("piece")
        for option in options
        if option.command == "build" and option.args.get("piece") in BUILD_PIECES
    }
    active_piece = state.selected_build_piece
    if active_piece not in build_pieces:
        active_piece = next(iter(build_pieces)) if len(build_pieces) == 1 else None

    targets = []
    for option in options:
        if option.command not in _BOARD_COMMANDS:
            continue
        if option.command == "build" and option.args.get("piece") != active_piece:
            continue
        target_id = option.args.get("target")
        if type(target_id) is not str or target_id not in state.view.board.position_by_id:
            continue
        if target_id in state.view.board.node_by_id:
            kind = "node"
            segment = None
            hit_radius = _clamp(round(transform.tile_radius * 0.38), 13, 24)
        elif target_id in state.view.board.edge_by_id:
            kind = "edge"
            start, end = state.view.board.edge_segment(target_id)
            segment = (transform.point(start), transform.point(end))
            hit_radius = _clamp(round(transform.tile_radius * 0.28), 11, 18)
        elif target_id in state.view.board.tile_by_id:
            kind = "tile"
            segment = None
            hit_radius = _clamp(round(transform.tile_radius * 0.68), 18, 44)
        else:
            continue
        center = transform.point(state.view.board.position_for(target_id))
        targets.append(
            LanMatchBoardTarget(
                target_id=target_id,
                target_kind=kind,
                center=center,
                hit_radius=hit_radius,
                command=option.command,
                args=option.args,
                segment=segment,
            )
        )
    targets.sort(key=lambda target: (target.target_kind, target.target_id))
    return tuple(targets)


def _build_harbor_layouts(
    board_rect: pygame.Rect,
    view: NetworkGameView,
    transform: LanMatchBoardTransform,
) -> tuple[LanMatchHarborLayout, ...]:
    result: list[LanMatchHarborLayout] = []
    occupied: list[pygame.Rect] = []
    inner_bounds = board_rect.inflate(-8, -8)
    font = get_font(_clamp(round(transform.tile_radius * 0.34), 12, 18))
    board_center = (transform.center_x, transform.center_y)
    for harbor in view.board.harbors:
        start_point, end_point = view.board.edge_segment(harbor.edge_id)
        start = transform.point(start_point)
        end = transform.point(end_point)
        midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        vector = (midpoint[0] - board_center[0], midpoint[1] - board_center[1])
        length = max(1.0, math.hypot(*vector))
        outward = (vector[0] / length, vector[1] / length)
        tangent = (-outward[1], outward[0])
        label_width = _clamp(font.size(harbor.label)[0] + 16, 42, 78)
        label_height = _clamp(round(transform.tile_radius * 0.46), 23, 32)
        base_distance = transform.tile_radius * 0.72 + max(label_width, label_height) * 0.37
        center = (
            midpoint[0] + outward[0] * base_distance,
            midpoint[1] + outward[1] * base_distance,
        )
        edge_rect = pygame.Rect(
            min(start[0], end[0]),
            min(start[1], end[1]),
            max(1, abs(start[0] - end[0])),
            max(1, abs(start[1] - end[1])),
        ).inflate(18, 18)
        candidate = pygame.Rect(0, 0, label_width, label_height)
        candidate.center = (round(center[0]), round(center[1]))
        for attempt in range(18):
            conflicts = candidate.colliderect(edge_rect) or any(
                candidate.colliderect(previous.inflate(3, 3)) for previous in occupied
            )
            if not conflicts and inner_bounds.contains(candidate):
                break
            distance = 4 + attempt * 1.4
            direction = -1 if attempt % 2 else 1
            candidate.center = (
                round(center[0] + outward[0] * distance + tangent[0] * direction * (attempt // 5) * 5),
                round(center[1] + outward[1] * distance + tangent[1] * direction * (attempt // 5) * 5),
            )
            candidate.clamp_ip(inner_bounds)
        occupied.append(candidate.copy())
        connector_end = (
            round(candidate.centerx - outward[0] * label_height * 0.48),
            round(candidate.centery - outward[1] * label_height * 0.48),
        )
        result.append(
            LanMatchHarborLayout(
                harbor_id=harbor.target_id,
                rect=candidate,
                anchor=(round(midpoint[0]), round(midpoint[1])),
                connector_end=connector_end,
            )
        )
    return tuple(result)


def _build_player_layouts(
    rect: pygame.Rect, view: NetworkGameView, gap: int
) -> tuple[LanMatchPlayerLayout, ...]:
    padding = _clamp(round(rect.height * 0.10), 10, 17)
    inner = rect.inflate(-padding * 2, -padding * 2)
    count = len(view.players)
    card_gap = max(6, gap - 2)
    card_width = (inner.width - card_gap * (count - 1)) // count
    return tuple(
        LanMatchPlayerLayout(
            player.seat,
            pygame.Rect(
                inner.x + index * (card_width + card_gap),
                inner.y,
                card_width,
                inner.height,
            ),
        )
        for index, player in enumerate(view.players)
    )


def _target_hit_score(
    target: LanMatchBoardTarget, point: tuple[int, int]
) -> float | None:
    if target.segment is not None:
        distance = _point_segment_distance(point, *target.segment)
    else:
        distance = math.hypot(point[0] - target.center[0], point[1] - target.center[1])
    if distance > target.hit_radius:
        return None
    return distance / max(1, target.hit_radius)


def _point_segment_distance(
    point: tuple[int, int], start: tuple[int, int], end: tuple[int, int]
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator <= 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    position = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator
    position = max(0.0, min(1.0, position))
    closest = (start[0] + dx * position, start[1] + dy * position)
    return math.hypot(point[0] - closest[0], point[1] - closest[1])


def _draw_header(
    surface: pygame.Surface, layout: LanMatchLayout, state: LanMatchDisplayState
) -> None:
    view = state.view
    height = layout.screen_rect.height
    actor = view.current_actor
    title_font = get_font(_font_size(height, 27, 20, 34), bold=True)
    body_font = get_font(_font_size(height, 16, 12, 20))
    room_font = get_font(_font_size(height, 14, 11, 18))
    marker_color = actor.color
    pygame.draw.rect(
        surface,
        marker_color,
        pygame.Rect(layout.header_rect.x, layout.header_rect.y, 7, layout.header_rect.height),
        border_top_left_radius=17,
        border_bottom_left_radius=17,
    )
    if view.winner_seat is not None:
        title = f"勝者 — {view.player_by_seat[view.winner_seat].name}"
    elif view.viewer_seat == view.current_actor_seat:
        title = f"あなたの手番 — {actor.name}"
    else:
        title = f"{actor.name} の手番"
    rendered = title_font.render(title, True, COLORS["WHITE"])
    surface.blit(rendered, (layout.header_rect.x + 24, layout.header_rect.y + 13))
    guidance = _guidance_text(state)
    guidance_surface = body_font.render(
        _truncate(body_font, guidance, max(120, layout.header_rect.width - 410)),
        True,
        COLORS["WARNING"] if state.view.viewer_seat == view.current_actor_seat else COLORS["TEXT_MUTED"],
    )
    surface.blit(guidance_surface, (layout.header_rect.x + 25, layout.header_rect.bottom - guidance_surface.get_height() - 12))

    status = "接続済み" if state.connected else "再接続待ち"
    status_color = COLORS["SUCCESS"] if state.connected else COLORS["DANGER"]
    room = _safe_text(state.room_code, 16) or "------"
    right_text = f"ROOM {room}   REV {view.revision}   Esc ロビー"
    room_surface = room_font.render(right_text, True, COLORS["TEXT_MUTED"])
    surface.blit(room_surface, (layout.header_rect.right - room_surface.get_width() - 22, layout.header_rect.y + 15))
    badge_text = room_font.render(status, True, status_color)
    badge_rect = pygame.Rect(0, 0, badge_text.get_width() + 26, badge_text.get_height() + 12)
    badge_rect.topright = (layout.header_rect.right - 20, layout.header_rect.bottom - badge_rect.height - 10)
    pygame.draw.rect(surface, (30, 49, 48) if state.connected else (67, 36, 40), badge_rect, border_radius=badge_rect.height // 2)
    pygame.draw.rect(surface, status_color, badge_rect, 1, border_radius=badge_rect.height // 2)
    surface.blit(badge_text, badge_text.get_rect(center=badge_rect.center))


def _draw_board(
    surface: pygame.Surface, layout: LanMatchLayout, state: LanMatchDisplayState
) -> None:
    view = state.view
    transform = layout.transform
    radius = transform.tile_radius
    halo_radius = min(layout.board_rect.width, layout.board_rect.height) // 2 - 10
    pygame.draw.circle(
        surface,
        (52, 104, 145),
        layout.board_rect.center,
        max(24, halo_radius),
    )

    for tile in view.board.tiles:
        center = transform.point(tile.center)
        _draw_tile(surface, center, radius, tile.resource, tile.number, tile.robber)

    harbor_by_id = view.board.harbor_by_id
    for harbor_layout in layout.harbors:
        harbor = harbor_by_id[harbor_layout.harbor_id]
        _draw_harbor(surface, harbor_layout, harbor.label, harbor.resource, height=layout.screen_rect.height)

    # Targets sit below pieces, so ownership remains readable while the glow
    # still peeks around every legal road, node, or tile.
    for target in layout.board_targets:
        _draw_board_target(surface, target, transform.tile_radius)

    for edge in view.board.edges:
        if edge.road is None:
            continue
        start, end = view.board.edge_segment(edge.target_id)
        owner = view.player_by_seat[edge.road.owner_seat]
        _draw_road_piece(
            surface,
            transform.point(start),
            transform.point(end),
            owner.color,
            owner.piece_pattern,
            radius,
        )
    for node in view.board.nodes:
        if node.building is None:
            continue
        owner = view.player_by_seat[node.building.owner_seat]
        _draw_building_piece(
            surface,
            transform.point(node.position),
            owner.color,
            owner.piece_pattern,
            node.building.building_type,
            radius,
        )

    if layout.board_targets:
        legend_font = get_font(_font_size(layout.screen_rect.height, 13, 10, 16))
        legend = legend_font.render("光っている場所を選択", True, COLORS["HIGHLIGHT_NODE"])
        surface.blit(legend, (layout.board_rect.x + 14, layout.board_rect.bottom - legend.get_height() - 10))


def _draw_tile(
    surface: pygame.Surface,
    center: tuple[int, int],
    radius: int,
    resource: str,
    number: int | None,
    robber: bool,
) -> None:
    vertices = _hex_vertices(center, radius)
    tile_surface = None
    try:
        tile_surface = get_tile_surface(ResourceType[resource], radius)
    except (KeyError, TypeError):
        tile_surface = None
    if tile_surface is not None:
        surface.blit(tile_surface, tile_surface.get_rect(center=center))
    else:
        pygame.draw.polygon(surface, _RESOURCE_COLORS.get(resource, (130, 145, 125)), vertices)
    pygame.draw.polygon(surface, (34, 35, 34), vertices, max(2, radius // 22))
    pygame.draw.lines(surface, (240, 221, 164), True, vertices, 1)

    if resource == "UNKNOWN":
        for ring_radius in (round(radius * 0.52), round(radius * 0.34)):
            pygame.draw.circle(surface, (93, 151, 150), center, ring_radius, 2)
        font = get_font(_clamp(round(radius * 0.48), 16, 31), bold=True)
        text = font.render("?", True, (218, 232, 216))
        surface.blit(text, text.get_rect(center=center))
        return

    if number is not None:
        token_radius = _clamp(round(radius * 0.34), 11, 23)
        token_center = center
        pygame.draw.circle(surface, (248, 244, 225), token_center, token_radius)
        pygame.draw.circle(surface, (44, 39, 33), token_center, token_radius, 2)
        font = get_font(_clamp(round(radius * 0.42), 13, 28), bold=True)
        color = (190, 49, 42) if number in (6, 8) else (26, 28, 30)
        text = font.render(str(number), True, color)
        surface.blit(text, text.get_rect(center=(token_center[0], token_center[1] - 2)))
        pip_count = max(1, 6 - abs(7 - number))
        dot_y = token_center[1] + token_radius - 5
        start_x = token_center[0] - (pip_count - 1) * 2
        for index in range(pip_count):
            pygame.draw.circle(surface, color, (start_x + index * 4, dot_y), 1)
    if robber:
        _draw_robber(surface, center, radius)


def _draw_robber(surface: pygame.Surface, center: tuple[int, int], radius: int) -> None:
    x, y = center
    head_radius = _clamp(round(radius * 0.13), 4, 8)
    body_width = _clamp(round(radius * 0.34), 12, 23)
    body_height = _clamp(round(radius * 0.38), 14, 25)
    shadow = pygame.Rect(x - body_width // 2, y + body_height // 3, body_width, max(5, body_height // 3))
    pygame.draw.ellipse(surface, (38, 28, 22), shadow)
    pygame.draw.circle(surface, (22, 22, 24), (x, y - body_height // 3), head_radius)
    body = pygame.Rect(x - body_width // 2, y - body_height // 4, body_width, body_height)
    pygame.draw.ellipse(surface, (22, 22, 24), body)
    pygame.draw.ellipse(surface, (237, 207, 144), body, 2)


def _draw_harbor(
    surface: pygame.Surface,
    layout: LanMatchHarborLayout,
    label: str,
    resource: str | None,
    *,
    height: int,
) -> None:
    pygame.draw.line(surface, (227, 215, 181), layout.anchor, layout.connector_end, 3)
    pygame.draw.circle(surface, (102, 65, 39), layout.anchor, 4)
    pygame.draw.rect(surface, (235, 210, 158), layout.rect, border_radius=7)
    accent = _RESOURCE_COLORS.get(resource, (106, 119, 132))
    pygame.draw.rect(surface, accent, pygame.Rect(layout.rect.x + 3, layout.rect.y + 4, 4, layout.rect.height - 8), border_radius=2)
    pygame.draw.rect(surface, (89, 63, 42), layout.rect, 2, border_radius=7)
    font = get_font(_font_size(height, 13, 10, 16))
    text = font.render(_truncate(font, label, layout.rect.width - 13), True, (72, 53, 39))
    surface.blit(text, text.get_rect(center=(layout.rect.centerx + 2, layout.rect.centery)))


def _draw_board_target(
    surface: pygame.Surface, target: LanMatchBoardTarget, tile_radius: int
) -> None:
    color = COLORS["HIGHLIGHT_TILE"] if target.target_kind == "tile" else COLORS["HIGHLIGHT_EDGE"] if target.target_kind == "edge" else COLORS["HIGHLIGHT_NODE"]
    if target.segment is not None:
        pygame.draw.line(surface, (31, 43, 54), *target.segment, target.hit_radius * 2)
        pygame.draw.line(surface, color, *target.segment, max(5, target.hit_radius))
        pygame.draw.line(surface, (255, 255, 244), *target.segment, 2)
    elif target.target_kind == "tile":
        vertices = _hex_vertices(target.center, max(12, round(tile_radius * 0.88)))
        pygame.draw.lines(surface, color, True, vertices, max(3, tile_radius // 10))
        pygame.draw.lines(surface, (255, 255, 245), True, vertices, 1)
    else:
        # Keep the legal intersection readable without covering the surrounding
        # terrain when the empty opening board exposes dozens of valid nodes.
        ring_radius = max(7, target.hit_radius - 3)
        pygame.draw.circle(surface, (25, 38, 48), target.center, ring_radius, 4)
        pygame.draw.circle(surface, color, target.center, ring_radius, 3)
        pygame.draw.circle(surface, (255, 255, 244), target.center, 3)


def _draw_road_piece(
    surface: pygame.Surface,
    start: tuple[int, int],
    end: tuple[int, int],
    color: tuple[int, int, int],
    pattern: int,
    tile_radius: int,
) -> None:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 2:
        return
    axis = (dx / length, dy / length)
    normal = (-axis[1], axis[0])
    inset = min(length * 0.16, max(3.0, tile_radius * 0.12))
    a = (start[0] + axis[0] * inset, start[1] + axis[1] * inset)
    b = (end[0] - axis[0] * inset, end[1] - axis[1] * inset)
    half = _clamp(round(tile_radius * 0.115), 4, 8)

    def polygon(offset_x: float, offset_y: float, extra: int) -> list[tuple[int, int]]:
        width = half + extra
        return [
            (round(a[0] + normal[0] * width + offset_x), round(a[1] + normal[1] * width + offset_y)),
            (round(b[0] + normal[0] * width + offset_x), round(b[1] + normal[1] * width + offset_y)),
            (round(b[0] - normal[0] * width + offset_x), round(b[1] - normal[1] * width + offset_y)),
            (round(a[0] - normal[0] * width + offset_x), round(a[1] - normal[1] * width + offset_y)),
        ]

    pygame.draw.polygon(surface, (20, 24, 28), polygon(2, 3, 2))
    pygame.draw.polygon(surface, (26, 29, 32), polygon(0, 0, 2))
    pygame.draw.polygon(surface, color, polygon(0, 0, 0))
    light = _mix(color, (255, 255, 245), 0.52)
    shade = _mix(color, (25, 22, 20), 0.42)
    pygame.draw.line(
        surface,
        light,
        (round(a[0] + normal[0] * (half - 1)), round(a[1] + normal[1] * (half - 1))),
        (round(b[0] + normal[0] * (half - 1)), round(b[1] + normal[1] * (half - 1))),
        2,
    )
    pygame.draw.line(
        surface,
        shade,
        (round(a[0] - normal[0] * (half - 1)), round(a[1] - normal[1] * (half - 1))),
        (round(b[0] - normal[0] * (half - 1)), round(b[1] - normal[1] * (half - 1))),
        2,
    )
    positions = (0.5,) if pattern % 2 == 0 else (0.38, 0.62)
    for position in positions:
        center = (
            round(a[0] + (b[0] - a[0]) * position),
            round(a[1] + (b[1] - a[1]) * position),
        )
        if pattern % 4 < 2:
            pygame.draw.circle(surface, shade, center, 2)
        else:
            pygame.draw.line(
                surface,
                light,
                (round(center[0] - normal[0] * (half - 1)), round(center[1] - normal[1] * (half - 1))),
                (round(center[0] + normal[0] * (half - 1)), round(center[1] + normal[1] * (half - 1))),
                1,
            )


def _draw_building_piece(
    surface: pygame.Surface,
    center: tuple[int, int],
    color: tuple[int, int, int],
    pattern: int,
    building_type: str,
    tile_radius: int,
) -> None:
    scale = max(0.72, min(1.28, tile_radius / 48))
    x, y = center
    light = _mix(color, (255, 255, 245), 0.50)
    roof = _mix(color, (48, 31, 22), 0.28)
    shade = _mix(color, (22, 19, 18), 0.44)
    outline = (23, 26, 29)

    def points(values: Sequence[tuple[float, float]], offset=(0, 0)) -> list[tuple[int, int]]:
        return [
            (round(x + px * scale + offset[0]), round(y + py * scale + offset[1]))
            for px, py in values
        ]

    if building_type == "city":
        silhouette = ((-16, 12), (-16, -1), (-8, -10), (0, -3), (0, -13), (13, -13), (13, 12))
        pygame.draw.ellipse(surface, (22, 24, 27), pygame.Rect(round(x - 17 * scale), round(y + 7 * scale), round(34 * scale), max(5, round(10 * scale))))
        pygame.draw.polygon(surface, outline, points(silhouette, (2, 3)))
        pygame.draw.polygon(surface, color, points(silhouette))
        pygame.draw.polygon(surface, roof, points(((-16, -1), (-8, -10), (0, -3), (0, 1), (-8, -6), (-16, 2))))
        pygame.draw.polygon(surface, light, points(((0, -13), (13, -13), (10, -9), (0, -9))))
        pygame.draw.polygon(surface, shade, points(((10, -9), (13, -13), (13, 12), (9, 9))))
        mark_center = (round(x + 5 * scale), round(y + 3 * scale))
    else:
        silhouette = ((-11, 10), (-11, -1), (0, -12), (11, -1), (11, 10))
        pygame.draw.ellipse(surface, (22, 24, 27), pygame.Rect(round(x - 12 * scale), round(y + 6 * scale), round(27 * scale), max(4, round(9 * scale))))
        pygame.draw.polygon(surface, outline, points(silhouette, (2, 3)))
        pygame.draw.polygon(surface, color, points(silhouette))
        pygame.draw.polygon(surface, roof, points(((-11, -1), (0, -12), (11, -1), (9, 2), (0, -7), (-9, 2))))
        pygame.draw.polygon(surface, shade, points(((7, 2), (11, -1), (11, 10), (7, 8))))
        mark_center = (x, round(y - 2 * scale))
    pygame.draw.polygon(surface, outline, points(silhouette), 2)
    _draw_owner_mark(surface, mark_center, pattern, light, max(2, round(3 * scale)))


def _draw_owner_mark(
    surface: pygame.Surface,
    center: tuple[int, int],
    pattern: int,
    color: tuple[int, int, int],
    radius: int,
) -> None:
    x, y = center
    if pattern % 4 == 0:
        pygame.draw.circle(surface, color, center, max(1, radius - 1))
    elif pattern % 4 == 1:
        pygame.draw.polygon(surface, color, ((x, y - radius), (x + radius, y), (x, y + radius), (x - radius, y)))
    elif pattern % 4 == 2:
        pygame.draw.polygon(surface, color, ((x, y - radius), (x + radius, y + radius), (x - radius, y + radius)))
    else:
        pygame.draw.rect(surface, color, pygame.Rect(x - radius + 1, y - radius + 1, radius * 2 - 1, radius * 2 - 1))


def _draw_side(
    surface: pygame.Surface, layout: LanMatchLayout, state: LanMatchDisplayState
) -> None:
    height = layout.screen_rect.height
    title_font = get_font(_font_size(height, 20, 15, 25), bold=True)
    body_font = get_font(_font_size(height, 14, 10, 17))
    tiny_font = get_font(_font_size(height, 13, 10, 16))
    title = title_font.render("操作パネル", True, COLORS["WHITE"])
    surface.blit(title, (layout.side_rect.x + 16, layout.side_rect.y + 11))

    pygame.draw.rect(surface, (28, 42, 57), layout.guidance_rect, border_radius=11)
    pygame.draw.rect(surface, COLORS["BUTTON_HIGHLIGHT_BORDER"], layout.guidance_rect, 1, border_radius=11)
    guidance_lines = _wrap(body_font, _guidance_text(state), layout.guidance_rect.width - 20)[:3]
    y = layout.guidance_rect.y + 9
    for line in guidance_lines:
        rendered = body_font.render(line, True, COLORS["WARNING"])
        surface.blit(rendered, (layout.guidance_rect.x + 10, y))
        y += rendered.get_height() + 2
    trade = state.view.domestic_trade
    if state.view.special_phase and state.view.special_phase.startswith("domestic_trade"):
        summary = f"渡す {_bundle_label(trade.give)}  /  受取 {_bundle_label(trade.receive)}"
        rendered = tiny_font.render(
            _truncate(tiny_font, summary, layout.guidance_rect.width - 20),
            True,
            COLORS["TEXT_MUTED"],
        )
        surface.blit(rendered, (layout.guidance_rect.x + 10, layout.guidance_rect.bottom - rendered.get_height() - 7))

    for control in layout.controls:
        _draw_control(surface, control, height)
    if not layout.controls and not layout.board_targets:
        idle = body_font.render(
            "観戦中" if state.view.viewer_seat is None else "相手の操作を待っています",
            True,
            COLORS["TEXT_MUTED"],
        )
        surface.blit(idle, idle.get_rect(center=layout.action_rect.center))

    pygame.draw.rect(surface, (15, 24, 35), layout.log_rect, border_radius=10)
    log_title = tiny_font.render("直近のイベント", True, COLORS["TEXT_MUTED"])
    surface.blit(log_title, (layout.log_rect.x + 9, layout.log_rect.y + 6))
    log_y = layout.log_rect.y + log_title.get_height() + 8
    available = layout.log_rect.bottom - log_y - 5
    line_height = tiny_font.get_height() + 2
    max_lines = max(1, available // max(1, line_height))
    log_lines: list[str] = []
    for message in reversed(state.view.logs[-6:]):
        wrapped = _wrap(tiny_font, f"・{message}", layout.log_rect.width - 18)
        log_lines[0:0] = wrapped[-2:]
        if len(log_lines) >= max_lines:
            log_lines = log_lines[-max_lines:]
            break
    if not log_lines:
        log_lines = ["まだイベントはありません"]
    for line in log_lines[-max_lines:]:
        rendered = tiny_font.render(line, True, COLORS["TEXT_MUTED"])
        surface.blit(rendered, (layout.log_rect.x + 9, log_y))
        log_y += line_height


def _draw_control(
    surface: pygame.Surface, control: LanMatchControl, screen_height: int
) -> None:
    if not control.enabled:
        fill = COLORS["BUTTON_DISABLED"]
        border = (89, 96, 104)
        text_color = (155, 163, 171)
    elif control.selected:
        fill = COLORS["BUTTON_HIGHLIGHT"]
        border = COLORS["BUTTON_HIGHLIGHT_BORDER"]
        text_color = COLORS["WHITE"]
    elif control.command in ("end_turn", "trade_reject", "cancel"):
        fill = (57, 64, 72)
        border = COLORS["CARD_BORDER"]
        text_color = COLORS["TEXT_MUTED"]
    elif control.command in ("trade_accept", "trade_submit", "roll_dice"):
        fill = (67, 116, 91)
        border = COLORS["BUTTON_HIGHLIGHT_BORDER"]
        text_color = COLORS["WHITE"]
    else:
        fill = COLORS["BUTTON_ACTIVE"]
        border = COLORS["PANEL_BORDER"]
        text_color = COLORS["BUTTON_TEXT"]
    pygame.draw.rect(surface, fill, control.rect, border_radius=min(9, control.rect.height // 3))
    pygame.draw.rect(surface, border, control.rect, 1 if control.rect.height < 25 else 2, border_radius=min(9, control.rect.height // 3))
    font = get_font(_clamp(round(control.rect.height * 0.48), 10, _font_size(screen_height, 15, 11, 18)))
    prefix = "✓ " if control.selected and control.kind == "select_build_piece" else ""
    label = _truncate(font, prefix + control.label, control.rect.width - 8)
    rendered = font.render(label, True, text_color)
    surface.blit(rendered, rendered.get_rect(center=control.rect.center))


def _draw_players(
    surface: pygame.Surface, layout: LanMatchLayout, state: LanMatchDisplayState
) -> None:
    view = state.view
    height = layout.screen_rect.height
    name_font = get_font(_font_size(height, 18, 13, 22), bold=True)
    body_font = get_font(_font_size(height, 13, 10, 16))
    tiny_font = get_font(_font_size(height, 12, 10, 15))
    for player_layout in layout.players:
        player = view.player_by_seat[player_layout.seat]
        rect = player_layout.rect
        is_actor = player.seat == view.current_actor_seat and view.winner_seat is None
        is_viewer = player.is_viewer
        fill = (28, 40, 54) if is_viewer else (20, 30, 43)
        border = player.color if is_actor or is_viewer else COLORS["CARD_BORDER"]
        pygame.draw.rect(surface, fill, rect, border_radius=10)
        pygame.draw.rect(surface, border, rect, 3 if is_actor else 2, border_radius=10)
        pygame.draw.rect(surface, player.color, pygame.Rect(rect.x, rect.y, 6, rect.height), border_top_left_radius=10, border_bottom_left_radius=10)
        marker = f"{player.marker} {player.name}"
        if is_viewer:
            marker += " ・あなた"
        rendered = name_font.render(_truncate(name_font, marker, rect.width - 80), True, COLORS["WHITE"])
        surface.blit(rendered, (rect.x + 13, rect.y + 8))
        vp = name_font.render(f"{player.public_vp}/{view.victory_target} VP", True, COLORS["WARNING"])
        surface.blit(vp, (rect.right - vp.get_width() - 10, rect.y + 8))
        public = f"手札 {player.resource_total}  発展 {player.development_card_total}  騎士 {player.played_knights}"
        public_surface = body_font.render(_truncate(body_font, public, rect.width - 22), True, COLORS["TEXT_MUTED"])
        surface.blit(public_surface, (rect.x + 13, rect.y + 37))
        pieces = f"道{player.roads_remaining}  開{player.settlements_remaining}  都{player.cities_remaining}"
        pieces_surface = tiny_font.render(pieces, True, COLORS["TEXT_MUTED"])
        surface.blit(pieces_surface, (rect.x + 13, rect.bottom - pieces_surface.get_height() - 8))
        if player.resources is not None and rect.height >= 88:
            resources = " ".join(
                f"{_RESOURCE_LABELS[key]}{player.resources[key]}" for key in RESOURCE_ORDER
            )
            resource_surface = tiny_font.render(_truncate(tiny_font, resources, rect.width - 22), True, COLORS["SUCCESS"])
            surface.blit(resource_surface, (rect.x + 13, rect.y + 59))


def _draw_connection_overlay(
    surface: pygame.Surface, layout: LanMatchLayout, state: LanMatchDisplayState
) -> None:
    overlay = pygame.Surface(layout.board_rect.size, pygame.SRCALPHA)
    overlay.fill((5, 9, 15, 175))
    surface.blit(overlay, layout.board_rect.topleft)
    width = min(430, layout.board_rect.width - 40)
    height = min(120, layout.board_rect.height - 30)
    rect = pygame.Rect(0, 0, width, height)
    rect.center = layout.board_rect.center
    pygame.draw.rect(surface, (55, 34, 39), rect, border_radius=14)
    pygame.draw.rect(surface, COLORS["DANGER"], rect, 2, border_radius=14)
    title_font = get_font(_font_size(layout.screen_rect.height, 22, 16, 27), bold=True)
    body_font = get_font(_font_size(layout.screen_rect.height, 14, 11, 17))
    title = title_font.render("接続が切れました", True, COLORS["WHITE"])
    surface.blit(title, title.get_rect(center=(rect.centerx, rect.y + 35)))
    message = _safe_text(state.error, 160) or "同じ席への再接続を待っています。"
    body = body_font.render(_truncate(body_font, message, rect.width - 28), True, COLORS["DANGER"])
    surface.blit(body, body.get_rect(center=(rect.centerx, rect.bottom - 32)))


def _draw_error_banner(
    surface: pygame.Surface, layout: LanMatchLayout, message: str
) -> None:
    font = get_font(_font_size(layout.screen_rect.height, 13, 10, 16))
    text = _truncate(font, _safe_text(message, 200), layout.board_rect.width - 48)
    rendered = font.render(text, True, COLORS["DANGER"])
    rect = pygame.Rect(0, 0, rendered.get_width() + 24, rendered.get_height() + 12)
    rect.midtop = (layout.board_rect.centerx, layout.board_rect.y + 10)
    pygame.draw.rect(surface, (69, 36, 42), rect, border_radius=8)
    pygame.draw.rect(surface, COLORS["DANGER"], rect, 1, border_radius=8)
    surface.blit(rendered, rendered.get_rect(center=rect.center))


def _guidance_text(state: LanMatchDisplayState) -> str:
    view = state.view
    options = _usable_options(state)
    commands = {option.command for option in options}
    if not state.connected:
        return "再接続すると同じ席から対局を続けられます。"
    if view.winner_seat is not None:
        winner = view.player_by_seat[view.winner_seat]
        return f"{winner.name} が {winner.public_vp} VPで勝利しました。"
    if view.viewer_seat is None:
        return f"観戦中：{view.current_actor.name} の操作を見守っています。"
    if view.viewer_seat != view.current_actor_seat and not options:
        return f"{view.current_actor.name} の操作を待っています。"
    if view.phase == "initial":
        if "roll_dice" in commands:
            return "配置順を決めるサイコロを振ってください。"
        if view.waiting_for_road:
            return "今置いた開拓地につながる街道を選んでください。"
        return "距離ルールを守って開拓地を置いてください。"
    special = view.special_phase or ""
    if special == "discard":
        return f"盗賊：捨てる資源を選択（残り {view.discard_remaining}枚）"
    if special == "move_robber":
        return "盗賊を移動するタイルを選んでください。"
    if special == "steal":
        return "資源を1枚奪う相手を選んでください。"
    if special == "year_of_plenty":
        return f"収穫：銀行から資源を選択（残り {view.resource_selection_remaining}枚）"
    if special == "monopoly":
        return "独占する資源を1種類選んでください。"
    if special == "bank_trade_give":
        return "銀行・港へ渡す資源を選んでください。"
    if special == "bank_trade_receive":
        resource = _RESOURCE_LABELS.get(view.bank_trade_give_resource or "", "資源")
        return f"{resource}を渡して受け取る資源を選んでください。"
    if special == "road_building":
        return f"無料の街道を配置（残り {view.free_roads_remaining}本）"
    if special == "domestic_trade_partner":
        return "交渉相手を選ぶか、全員へ呼びかけてください。"
    if special == "domestic_trade_edit":
        return "渡す資源と受け取る資源を調整してください。"
    if special in ("domestic_trade_handoff", "domestic_trade_counter_handoff"):
        return "提案内容を確認して交渉を続けてください。"
    if special in ("domestic_trade_response", "domestic_trade_counter_response"):
        return "提示された条件に返答してください。"
    if "roll_dice" in commands:
        return "サイコロを振るか、手札の発展カードを使えます。"
    build_pieces = {
        option.args.get("piece") for option in options if option.command == "build"
    }
    if len(build_pieces) > 1 and state.selected_build_piece not in build_pieces:
        return "建設する駒を選ぶか、交易・交渉を行ってください。"
    return "建設・交易・交渉を行うか、手番を終了してください。"


def _command_label(view: NetworkGameView, option: _Option) -> str:
    command = option.command
    args = option.args
    if command in _SIMPLE_COMMAND_LABELS:
        return _SIMPLE_COMMAND_LABELS[command]
    if command == "select_resource":
        return _RESOURCE_LABELS.get(str(args.get("resource")), "資源")
    if command == "use_development":
        return _CARD_LABELS.get(str(args.get("card")), "発展カード")
    if command in ("steal", "trade_partner"):
        index = args.get("seat_index")
        if type(index) is int and 0 <= index < len(view.players):
            prefix = "奪う：" if command == "steal" else "交渉："
            return prefix + view.players[index].name
        return "プレイヤー"
    if command == "trade_edit_side":
        return "受取を編集" if args.get("side") == "receive" else "渡す側を編集"
    if command == "trade_adjust":
        side = "渡" if args.get("side") == "give" else "受"
        resource = _RESOURCE_LABELS.get(str(args.get("resource")), "?")
        delta = "＋" if args.get("delta") == 1 else "−"
        return f"{side} {resource} {delta}"
    return command


def _command_action_id(index: int, option: _Option) -> str:
    canonical = json.dumps(dict(option.args), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return f"command:{index}:{option.command}:{canonical}"


def _bundle_label(bundle: Mapping[str, int]) -> str:
    entries = [
        f"{_RESOURCE_LABELS[key]}{bundle[key]}"
        for key in RESOURCE_ORDER
        if bundle[key] > 0
    ]
    return "・".join(entries) if entries else "なし"


def _draw_panel(
    surface: pygame.Surface,
    rect: pygame.Rect,
    *,
    radius: int,
    water: bool = False,
) -> None:
    shadow_rect = rect.move(3, 4)
    pygame.draw.rect(surface, (5, 10, 17), shadow_rect, border_radius=radius)
    fill = (44, 91, 128) if water else COLORS["PANEL_BG"]
    pygame.draw.rect(surface, fill, rect, border_radius=radius)
    pygame.draw.rect(surface, COLORS["PANEL_BORDER"], rect, 2, border_radius=radius)


@lru_cache(maxsize=8)
def _background_surface(size: tuple[int, int]) -> pygame.Surface:
    width, height = size
    result = pygame.Surface(size)
    top = (47, 88, 126)
    bottom = (29, 55, 81)
    for y in range(height):
        progress = y / max(1, height - 1)
        color = tuple(round(a + (b - a) * progress) for a, b in zip(top, bottom))
        pygame.draw.line(result, color, (0, y), (width - 1, y))
    return result


def _hex_vertices(center: tuple[int, int], radius: int) -> list[tuple[int, int]]:
    return [
        (
            round(center[0] + radius * math.cos(math.radians(60 * index - 30))),
            round(center[1] + radius * math.sin(math.radians(60 * index - 30))),
        )
        for index in range(6)
    ]


def _mix(
    color: tuple[int, int, int], target: tuple[int, int, int], amount: float
) -> tuple[int, int, int]:
    amount = max(0.0, min(1.0, amount))
    return tuple(round(a + (b - a) * amount) for a, b in zip(color, target))


def _wrap(font: pygame.font.Font, text: str, max_width: int) -> list[str]:
    lines = []
    current = ""
    for character in _safe_text(text, 500):
        candidate = current + character
        if current and font.size(candidate)[0] > max_width:
            lines.append(current)
            current = character
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _truncate(font: pygame.font.Font, text: str, max_width: int) -> str:
    if font.size(text)[0] <= max_width:
        return text
    value = text
    while value and font.size(value + "…")[0] > max_width:
        value = value[:-1]
    return value + "…"


def _safe_text(value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(character for character in value if character >= " " or character in "\t")[
        :maximum
    ]


def _validated_size(size: Sequence[int]) -> tuple[int, int]:
    if not isinstance(size, Sequence) or len(size) != 2:
        raise ValueError("size must contain width and height")
    try:
        width, height = int(size[0]), int(size[1])
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("size must contain integer dimensions") from error
    if width < 960 or height < 640:
        raise ValueError("LAN match display requires at least 960x640")
    return width, height


def _font_size(
    screen_height: int, preferred: int, minimum: int, maximum: int
) -> int:
    return _clamp(round(preferred * screen_height / 800), minimum, maximum)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))
