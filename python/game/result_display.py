"""Standalone post-match result presentation for the Pygame client.

The renderer deliberately accepts ordinary mappings or small dataclasses.  It
does not import :mod:`game.game`, player objects, or the replay controller, so
the same result payload can later be produced by a LAN server or Web client.
``draw_result_display`` returns every clickable rectangle required by the game
controller; no input handling is hidden inside the component.
"""

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import pygame

from game.ai_personality import get_ai_personality_profile
from game.assets import get_font
from game.constants import COLORS


REPLAY_SELECTED_ACTION = "replay_selected_event"
RESTART_SAME_BOARD_ACTION = "restart_same_board"
NEW_BOARD_ACTION = "new_board"
RESULT_ACTIONS = (
    REPLAY_SELECTED_ACTION,
    RESTART_SAME_BOARD_ACTION,
    NEW_BOARD_ACTION,
)

_ACTION_LABELS = {
    REPLAY_SELECTED_ACTION: "選択イベントからリプレイ",
    RESTART_SAME_BOARD_ACTION: "同じ盤面でもう一戦",
    NEW_BOARD_ACTION: "新しい盤面で遊ぶ",
}
_FALLBACK_PLAYER_COLORS = (
    (235, 76, 70),
    (73, 128, 222),
    (235, 166, 45),
    (76, 195, 202),
    (167, 112, 220),
    (102, 184, 114),
)


@dataclass(frozen=True)
class ResultPlayerSummary:
    """Public, serialisable statistics for one participant."""

    name: str
    victory_points: int
    color: Tuple[int, int, int]
    roads_built: int = 0
    settlements_built: int = 0
    cities_built: int = 0
    trades_completed: int = 0
    bank_trades: int = 0
    domestic_trades: int = 0
    luck_index: Optional[float] = None
    is_ai: bool = False
    personality: Optional[str] = None
    rank: int = 1


@dataclass(frozen=True)
class VictoryPointSnapshot:
    """Victory points visible at one turn on the compact result chart."""

    turn: int
    points: Mapping[str, int]


@dataclass(frozen=True)
class ImportantResultEvent:
    """A result event which may link to a replay frame."""

    title: str
    detail: str = ""
    turn: Optional[int] = None
    player_name: str = ""
    replay_frame: Optional[int] = None


@dataclass(frozen=True)
class MatchResultSummary:
    """Normalised, UI-facing result data independent of live game objects."""

    winner_name: str
    players: Tuple[ResultPlayerSummary, ...]
    victory_target: int
    turn_count: int
    vp_progression: Tuple[VictoryPointSnapshot, ...] = ()
    important_events: Tuple[ImportantResultEvent, ...] = ()
    subtitle: str = "対局終了"
    timeline_unit: str = "ターン"


@dataclass(frozen=True)
class ResultEventHitbox:
    event_index: int
    rect: pygame.Rect


@dataclass
class ResultDisplayLayout:
    """Deterministic rectangles returned to the game input controller."""

    screen_rect: pygame.Rect
    header_rect: pygame.Rect
    standings_rect: pygame.Rect
    chart_rect: pygame.Rect
    events_rect: pygame.Rect
    controls_rect: pygame.Rect
    player_rects: Tuple[pygame.Rect, ...]
    chart_plot_rect: pygame.Rect
    event_hitboxes: Tuple[ResultEventHitbox, ...]
    action_rects: Dict[str, pygame.Rect]
    enabled_actions: Tuple[str, ...]
    selected_event_index: Optional[int]


@dataclass(frozen=True)
class ResultHitTarget:
    """One actionable target under a pointer position."""

    kind: str
    action: Optional[str] = None
    event_index: Optional[int] = None


def _get_value(source: Any, keys: Sequence[str], default: Any = None) -> Any:
    for key in keys:
        if isinstance(source, Mapping) and key in source:
            return source[key]
        if hasattr(source, key):
            return getattr(source, key)
    return default


def _safe_int(value: Any, default: int = 0, minimum: Optional[int] = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if minimum is not None:
        result = max(minimum, result)
    return result


def _safe_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return max(-999.0, min(999.0, result))


def _safe_text(value: Any, default: str, limit: int = 180) -> str:
    text = default if value is None else str(value)
    text = "".join(character for character in text if character >= " " and character != "\x7f")
    text = text.strip()
    return (text or default)[:limit]


def _safe_color(value: Any, fallback: Tuple[int, int, int]) -> Tuple[int, int, int]:
    if not isinstance(value, (tuple, list)) or len(value) < 3:
        return fallback
    channels = []
    for channel in value[:3]:
        if isinstance(channel, bool):
            return fallback
        try:
            channels.append(max(0, min(255, int(channel))))
        except (TypeError, ValueError, OverflowError):
            return fallback
    return tuple(channels)


def result_player_role_label(player: ResultPlayerSummary) -> str:
    if not player.is_ai:
        return "PLAYER"
    if not player.personality:
        return "AI"
    profile = get_ai_personality_profile(player.personality)
    return f"AI・{profile.label}"


def _normalise_player(source: Any, index: int) -> ResultPlayerSummary:
    name = _safe_text(
        _get_value(source, ("name", "player_name")),
        f"Player{index + 1}",
        48,
    )
    color = _safe_color(
        _get_value(source, ("color", "player_color")),
        _FALLBACK_PLAYER_COLORS[index % len(_FALLBACK_PLAYER_COLORS)],
    )
    builds = _get_value(source, ("builds",), {})
    if not isinstance(builds, Mapping):
        builds = {}

    def build_count(key: str, aliases: Sequence[str]) -> int:
        nested_value = builds.get(key)
        if nested_value is not None:
            return _safe_int(nested_value)
        return _safe_int(_get_value(source, aliases))

    trades = _get_value(source, ("trades",), None)
    if isinstance(trades, Mapping):
        bank_trades = _safe_int(trades.get("bank"))
        domestic_trades = _safe_int(trades.get("domestic"))
        trades_completed = bank_trades + domestic_trades
    else:
        bank_trades = _safe_int(_get_value(source, ("bank_trades",)))
        domestic_trades = _safe_int(_get_value(source, ("domestic_trades",)))
        explicit_total = _get_value(source, ("trades_completed", "trade_count"), None)
        if explicit_total is None:
            explicit_total = trades
        trades_completed = (
            bank_trades + domestic_trades
            if explicit_total is None
            else _safe_int(explicit_total)
        )
    personality = _safe_text(
        _get_value(source, ("personality", "ai_personality")),
        "",
        32,
    )
    return ResultPlayerSummary(
        name=name,
        victory_points=_safe_int(
            _get_value(source, ("victory_points", "points", "vp")),
        ),
        color=color,
        roads_built=build_count("roads", ("roads_built", "roads")),
        settlements_built=build_count(
            "settlements",
            ("settlements_built", "settlements"),
        ),
        cities_built=build_count("cities", ("cities_built", "cities")),
        trades_completed=trades_completed,
        bank_trades=bank_trades,
        domestic_trades=domestic_trades,
        luck_index=_safe_float(_get_value(source, ("luck_index", "luck"))),
        is_ai=bool(_get_value(source, ("is_ai",), False)),
        personality=personality or None,
    )


def _normalise_snapshot(
    source: Any,
    player_names: Sequence[str],
    seat_to_name: Mapping[int, str],
    index: int,
) -> VictoryPointSnapshot:
    turn = _safe_int(
        _get_value(
            source,
            ("turn", "round", "index", "sequence", "replay_frame_index"),
            index,
        ),
        minimum=0,
    )
    points_source = _get_value(source, ("points", "victory_points", "values"), None)
    if not isinstance(points_source, Mapping):
        points_source = source if isinstance(source, Mapping) else {}
    points = {
        name: _safe_int(points_source.get(name, 0), minimum=0)
        for name in player_names
    }
    scores = _get_value(source, ("scores",), ()) or ()
    if isinstance(scores, Sequence) and not isinstance(scores, (str, bytes, Mapping)):
        for score in scores:
            seat = _safe_int(_get_value(score, ("seat",), 0), minimum=0)
            player_name = _get_value(score, ("name", "player_name"), None)
            if not player_name:
                player_name = seat_to_name.get(seat)
            if player_name in points:
                points[player_name] = _safe_int(
                    _get_value(score, ("victory_points", "points", "vp")),
                    minimum=0,
                )
    return VictoryPointSnapshot(turn=turn, points=points)


def _normalise_event(source: Any, index: int) -> ImportantResultEvent:
    turn_value = _get_value(source, ("turn", "round", "sequence"), None)
    frame_value = _get_value(
        source,
        (
            "replay_frame",
            "replay_frame_index",
            "frame_index",
            "replay_sequence",
        ),
        None,
    )
    return ImportantResultEvent(
        title=_safe_text(
            _get_value(source, ("title", "event", "message")),
            f"イベント {index + 1}",
            120,
        ),
        detail=_safe_text(_get_value(source, ("detail", "description")), "", 220),
        turn=None if turn_value is None else _safe_int(turn_value, minimum=0),
        player_name=_safe_text(
            _get_value(source, ("player_name", "player", "actor")),
            "",
            48,
        ),
        replay_frame=None if frame_value is None else _safe_int(frame_value, minimum=0),
    )


def normalise_match_result(summary: Any) -> MatchResultSummary:
    """Coerce a mapping or dataclass into the renderer's stable public model."""

    player_sources = _get_value(summary, ("players", "standings"), ()) or ()
    if isinstance(player_sources, (str, bytes, Mapping)):
        raise ValueError("result players must be a sequence")
    players_with_order = [
        (_normalise_player(source, index), index)
        for index, source in enumerate(player_sources)
    ]
    if not players_with_order:
        raise ValueError("result summary requires at least one player")
    seat_to_name = {}
    for player, source_index in players_with_order:
        source = player_sources[source_index]
        seat = _safe_int(
            _get_value(source, ("seat",), source_index + 1),
            minimum=1,
        )
        seat_to_name[seat] = player.name

    players_with_order.sort(key=lambda item: (-item[0].victory_points, item[1]))
    ranked_players = []
    previous_points = None
    previous_rank = 0
    for position, (player, _) in enumerate(players_with_order, start=1):
        rank = previous_rank if player.victory_points == previous_points else position
        ranked_players.append(
            ResultPlayerSummary(
                name=player.name,
                victory_points=player.victory_points,
                color=player.color,
                roads_built=player.roads_built,
                settlements_built=player.settlements_built,
                cities_built=player.cities_built,
                trades_completed=player.trades_completed,
                bank_trades=player.bank_trades,
                domestic_trades=player.domestic_trades,
                luck_index=player.luck_index,
                is_ai=player.is_ai,
                personality=player.personality,
                rank=rank,
            )
        )
        previous_points = player.victory_points
        previous_rank = rank

    player_names = tuple(player.name for player in ranked_players)
    raw_winner = _get_value(summary, ("winner_name", "winner"))
    if isinstance(raw_winner, Mapping) or hasattr(raw_winner, "name"):
        raw_winner = _get_value(raw_winner, ("name", "player_name"), None)
    winner_name = _safe_text(
        raw_winner,
        ranked_players[0].name,
        48,
    )
    victory_target = _safe_int(
        _get_value(summary, ("victory_target", "victory_point_target", "target"), 10),
        default=10,
        minimum=1,
    )
    raw_turn_count = _get_value(summary, ("turn_count", "turns", "rounds"), None)
    turn_count = _safe_int(
        raw_turn_count,
        minimum=0,
    )
    timeline_unit = _safe_text(
        _get_value(summary, ("timeline_unit",), None),
        "ターン" if raw_turn_count is not None else "フレーム",
        12,
    )

    progression_sources = _get_value(
        summary,
        ("vp_progression", "victory_point_progression", "score_history"),
        (),
    ) or ()
    if isinstance(progression_sources, (str, bytes, Mapping)):
        progression_sources = ()
    progression = tuple(
        sorted(
            (
                _normalise_snapshot(source, player_names, seat_to_name, index)
                for index, source in enumerate(progression_sources)
            ),
            key=lambda snapshot: snapshot.turn,
        )
    )
    if not turn_count and progression:
        turn_count = progression[-1].turn
    if not turn_count:
        replay = _get_value(summary, ("replay",), {})
        if isinstance(replay, Mapping):
            turn_count = max(0, _safe_int(replay.get("frame_count")) - 1)

    event_sources = _get_value(summary, ("important_events", "events"), ()) or ()
    if isinstance(event_sources, (str, bytes, Mapping)):
        event_sources = ()
    events = tuple(
        _normalise_event(source, index)
        for index, source in enumerate(event_sources)
    )

    return MatchResultSummary(
        winner_name=winner_name,
        players=tuple(ranked_players),
        victory_target=victory_target,
        turn_count=turn_count,
        vp_progression=progression,
        important_events=events,
        subtitle=_safe_text(
            _get_value(summary, ("subtitle", "result_label")),
            "対局終了",
            80,
        ),
        timeline_unit=timeline_unit,
    )


def _visible_event_indices(total: int, selected: Optional[int], capacity: int) -> range:
    if total <= 0 or capacity <= 0:
        return range(0)
    selected_index = 0 if selected is None else max(0, min(selected, total - 1))
    start = max(0, selected_index - capacity // 2)
    start = min(start, max(0, total - capacity))
    return range(start, min(total, start + capacity))


def build_result_display_layout(
    screen_size: Sequence[int],
    player_count: int,
    event_count: int,
    selected_event_index: Optional[int] = 0,
    *,
    replay_enabled: bool = True,
) -> ResultDisplayLayout:
    """Build stable geometry without drawing, suitable for mouse hit tests."""

    width, height = int(screen_size[0]), int(screen_size[1])
    if width < 960 or height < 680:
        raise ValueError("result display requires at least 960 x 680 pixels")
    player_count = max(1, int(player_count))
    if player_count > 4:
        raise ValueError("result display supports at most four players")
    event_count = max(0, int(event_count))
    selected = (
        None
        if event_count == 0
        else max(0, min(int(selected_event_index or 0), event_count - 1))
    )

    screen_rect = pygame.Rect(0, 0, width, height)
    margin = 22
    gap = 14
    header_height = 76
    controls_height = 74
    header_rect = pygame.Rect(margin, margin, width - margin * 2, header_height)
    controls_rect = pygame.Rect(
        margin,
        height - margin - controls_height,
        width - margin * 2,
        controls_height,
    )
    main_top = header_rect.bottom + gap
    main_bottom = controls_rect.top - gap
    main_height = main_bottom - main_top
    available_width = width - margin * 2 - gap
    left_width = round(available_width * 0.56)
    right_width = available_width - left_width
    standings_height = min(326, max(260, round(main_height * 0.56)))

    standings_rect = pygame.Rect(margin, main_top, left_width, standings_height)
    chart_rect = pygame.Rect(
        margin,
        standings_rect.bottom + gap,
        left_width,
        main_bottom - standings_rect.bottom - gap,
    )
    events_rect = pygame.Rect(
        standings_rect.right + gap,
        main_top,
        right_width,
        main_height,
    )

    player_top = standings_rect.y + 51
    player_bottom = standings_rect.bottom - 12
    row_gap = 5
    row_height = max(
        39,
        (player_bottom - player_top - row_gap * (player_count - 1)) // player_count,
    )
    player_rects = tuple(
        pygame.Rect(
            standings_rect.x + 12,
            player_top + index * (row_height + row_gap),
            standings_rect.width - 24,
            row_height,
        )
        for index in range(player_count)
    )

    chart_plot_rect = pygame.Rect(
        chart_rect.x + 43,
        chart_rect.y + 48,
        chart_rect.width - 61,
        max(42, chart_rect.height - 85),
    )

    event_list_top = events_rect.y + 53
    event_list_bottom = events_rect.bottom - 35
    event_gap = 6
    preferred_event_height = 60
    event_capacity = max(
        1,
        (event_list_bottom - event_list_top + event_gap)
        // (preferred_event_height + event_gap),
    )
    visible_indices = _visible_event_indices(event_count, selected, event_capacity)
    event_hitboxes = tuple(
        ResultEventHitbox(
            event_index=event_index,
            rect=pygame.Rect(
                events_rect.x + 12,
                event_list_top + local_index * (preferred_event_height + event_gap),
                events_rect.width - 24,
                preferred_event_height,
            ),
        )
        for local_index, event_index in enumerate(visible_indices)
    )

    button_gap = 12
    button_area = controls_rect.inflate(-24, -26)
    button_width = (button_area.width - button_gap * 2) // 3
    action_rects = {}
    for index, action in enumerate(RESULT_ACTIONS):
        width_adjustment = (
            button_area.width - (button_width * 3 + button_gap * 2)
            if index == 2
            else 0
        )
        action_rects[action] = pygame.Rect(
            button_area.x + index * (button_width + button_gap),
            button_area.y,
            button_width + width_adjustment,
            button_area.height,
        )

    return ResultDisplayLayout(
        screen_rect=screen_rect,
        header_rect=header_rect,
        standings_rect=standings_rect,
        chart_rect=chart_rect,
        events_rect=events_rect,
        controls_rect=controls_rect,
        player_rects=player_rects,
        chart_plot_rect=chart_plot_rect,
        event_hitboxes=event_hitboxes,
        action_rects=action_rects,
        enabled_actions=tuple(
            action
            for action in RESULT_ACTIONS
            if action != REPLAY_SELECTED_ACTION or (event_count and replay_enabled)
        ),
        selected_event_index=selected,
    )


def hit_test_result_display(
    layout: ResultDisplayLayout,
    position: Sequence[int],
) -> Optional[ResultHitTarget]:
    """Return the button or event row under ``position``."""

    point = (int(position[0]), int(position[1]))
    for action in RESULT_ACTIONS:
        if action in layout.enabled_actions and layout.action_rects[action].collidepoint(point):
            return ResultHitTarget(kind="action", action=action)
    for hitbox in layout.event_hitboxes:
        if hitbox.rect.collidepoint(point):
            return ResultHitTarget(kind="event", event_index=hitbox.event_index)
    return None


def selected_replay_frame(
    summary: Any,
    selected_event_index: Optional[int],
) -> Optional[int]:
    """Resolve the replay frame without coupling the renderer to replay state."""

    result = normalise_match_result(summary)
    if selected_event_index is None or not result.important_events:
        return None
    index = max(0, min(int(selected_event_index), len(result.important_events) - 1))
    frame = result.important_events[index].replay_frame
    if frame is None:
        return None
    replay = _get_value(summary, ("replay",), None)
    if isinstance(replay, Mapping):
        if not bool(replay.get("available", False)):
            return None
        frame_count = _safe_int(replay.get("frame_count"), minimum=0)
        if frame_count <= 0 or frame >= frame_count:
            return None
    return frame


def _truncate(font: pygame.font.Font, text: str, max_width: int) -> str:
    if font.size(text)[0] <= max_width:
        return text
    ellipsis = "…"
    result = text
    while result and font.size(result + ellipsis)[0] > max_width:
        result = result[:-1]
    return result + ellipsis


def _draw_background(screen: pygame.Surface) -> None:
    width, height = screen.get_size()
    top = (41, 82, 119)
    bottom = (18, 31, 49)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(
            round(top[channel] + (bottom[channel] - top[channel]) * ratio)
            for channel in range(3)
        )
        pygame.draw.line(screen, color, (0, y), (width, y))
    glow = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    pygame.draw.circle(glow, (103, 182, 224, 18), (width // 4, height // 3), 300)
    pygame.draw.circle(glow, (255, 201, 104, 12), (width - 150, 120), 220)
    screen.blit(glow, (0, 0))


def _draw_panel(screen: pygame.Surface, rect: pygame.Rect, alpha: int = 238) -> None:
    shadow = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(shadow, (2, 6, 12, 125), shadow.get_rect(), border_radius=17)
    screen.blit(shadow, rect.move(3, 5))
    panel = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(panel, (*COLORS["PANEL_BG"], alpha), panel.get_rect(), border_radius=16)
    pygame.draw.rect(panel, COLORS["PANEL_BORDER"], panel.get_rect(), 2, border_radius=16)
    screen.blit(panel, rect)


def _draw_header(
    screen: pygame.Surface,
    result: MatchResultSummary,
    rect: pygame.Rect,
) -> None:
    _draw_panel(screen, rect, 244)
    winner = next(
        (player for player in result.players if player.name == result.winner_name),
        result.players[0],
    )
    medallion_center = (rect.x + 43, rect.centery)
    pygame.draw.circle(screen, (76, 57, 31), medallion_center, 25)
    pygame.draw.circle(screen, COLORS["WARNING"], medallion_center, 22, 3)
    crown = (
        (medallion_center[0] - 12, medallion_center[1] + 7),
        (medallion_center[0] - 14, medallion_center[1] - 9),
        (medallion_center[0] - 5, medallion_center[1] - 1),
        (medallion_center[0], medallion_center[1] - 13),
        (medallion_center[0] + 6, medallion_center[1] - 1),
        (medallion_center[0] + 14, medallion_center[1] - 9),
        (medallion_center[0] + 12, medallion_center[1] + 7),
    )
    pygame.draw.polygon(screen, COLORS["WARNING"], crown)

    title_font = get_font(26, bold=True)
    subtitle_font = get_font(13)
    winner_text = _truncate(
        title_font,
        f"{winner.name} の勝利",
        rect.width - 420,
    )
    screen.blit(
        title_font.render(winner_text, True, COLORS["WHITE"]),
        (rect.x + 78, rect.y + 11),
    )
    subtitle = f"{result.subtitle}  ・  最終 {winner.victory_points} VP"
    screen.blit(
        subtitle_font.render(subtitle, True, COLORS["TEXT_MUTED"]),
        (rect.x + 80, rect.y + 45),
    )

    chip_font = get_font(13)
    chips = (
        f"勝利条件 {result.victory_target} VP",
        f"全 {result.turn_count} {result.timeline_unit}",
    )
    right = rect.right - 16
    for label in reversed(chips):
        surface = chip_font.render(label, True, COLORS["WHITE"])
        chip_rect = pygame.Rect(0, 0, surface.get_width() + 24, 30)
        chip_rect.right = right
        chip_rect.centery = rect.centery
        pygame.draw.rect(screen, (40, 61, 79), chip_rect, border_radius=14)
        pygame.draw.rect(screen, COLORS["CARD_BORDER"], chip_rect, 1, border_radius=14)
        screen.blit(surface, surface.get_rect(center=chip_rect.center))
        right = chip_rect.x - 10


def _format_luck(value: Optional[float]) -> Tuple[str, Tuple[int, int, int]]:
    if value is None:
        return "運 —", COLORS["TEXT_MUTED"]
    if value >= 110:
        color = COLORS["SUCCESS"]
    elif value <= 90:
        color = COLORS["DANGER"]
    else:
        color = COLORS["TEXT_MUTED"]
    return f"運 {value:.0f}", color


def _draw_standings(
    screen: pygame.Surface,
    result: MatchResultSummary,
    layout: ResultDisplayLayout,
) -> None:
    rect = layout.standings_rect
    _draw_panel(screen, rect)
    title_font = get_font(19, bold=True)
    hint_font = get_font(11)
    screen.blit(title_font.render("最終順位", True, COLORS["WHITE"]), (rect.x + 15, rect.y + 12))
    hint = "建設（街道・開拓地・都市） / 交易 / 運指数（100=期待値）"
    hint_surface = hint_font.render(hint, True, COLORS["TEXT_MUTED"])
    screen.blit(hint_surface, (rect.right - hint_surface.get_width() - 14, rect.y + 18))

    rank_font = get_font(17, bold=True)
    name_font = get_font(16, bold=True)
    metric_font = get_font(12)
    vp_font = get_font(18, bold=True)
    for player, player_rect in zip(result.players, layout.player_rects):
        winner = player.name == result.winner_name
        row_surface = pygame.Surface(player_rect.size, pygame.SRCALPHA)
        fill = (50, 62, 57, 245) if winner else (*COLORS["CARD_BG"], 235)
        pygame.draw.rect(row_surface, fill, row_surface.get_rect(), border_radius=12)
        border = COLORS["WARNING"] if winner else COLORS["CARD_BORDER"]
        pygame.draw.rect(row_surface, border, row_surface.get_rect(), 2, border_radius=12)
        pygame.draw.rect(
            row_surface,
            player.color,
            pygame.Rect(0, 0, 6, player_rect.height),
            border_radius=10,
        )
        screen.blit(row_surface, player_rect)

        rank_center = (player_rect.x + 27, player_rect.centery)
        pygame.draw.circle(screen, (39, 51, 64), rank_center, 16)
        pygame.draw.circle(screen, border, rank_center, 16, 1)
        rank_surface = rank_font.render(str(player.rank), True, COLORS["WHITE"])
        screen.blit(rank_surface, rank_surface.get_rect(center=rank_center))

        name_x = player_rect.x + 51
        name_max_width = max(80, round(player_rect.width * 0.27))
        role = result_player_role_label(player)
        name_text = _truncate(name_font, player.name, name_max_width)
        screen.blit(
            name_font.render(name_text, True, COLORS["WHITE"]),
            (name_x, player_rect.y + max(3, player_rect.height // 2 - 20)),
        )
        screen.blit(
            metric_font.render(role, True, player.color),
            (name_x, player_rect.y + player_rect.height // 2 + 3),
        )

        vp_text = f"{player.victory_points} VP"
        vp_surface = vp_font.render(vp_text, True, COLORS["WARNING"])
        vp_x = player_rect.x + round(player_rect.width * 0.36)
        screen.blit(vp_surface, (vp_x, player_rect.centery - vp_surface.get_height() // 2))

        metric_x = player_rect.x + round(player_rect.width * 0.52)
        construction = (
            f"建設  道{player.roads_built}  開{player.settlements_built}  都{player.cities_built}"
        )
        if player.bank_trades or player.domestic_trades:
            trade = f"交易 銀{player.bank_trades} 国内{player.domestic_trades}"
        else:
            trade = f"交易 {player.trades_completed}回"
        luck_text, luck_color = _format_luck(player.luck_index)
        first_y = player_rect.y + max(3, player_rect.height // 2 - 17)
        screen.blit(
            metric_font.render(construction, True, COLORS["TEXT_MUTED"]),
            (metric_x, first_y),
        )
        trade_surface = metric_font.render(trade, True, COLORS["TEXT_MUTED"])
        screen.blit(trade_surface, (metric_x, player_rect.y + player_rect.height // 2 + 3))
        luck_surface = metric_font.render(luck_text, True, luck_color)
        luck_x = min(
            player_rect.right - luck_surface.get_width() - 10,
            metric_x + trade_surface.get_width() + 18,
        )
        screen.blit(luck_surface, (luck_x, player_rect.y + player_rect.height // 2 + 3))


def _draw_chart(
    screen: pygame.Surface,
    result: MatchResultSummary,
    layout: ResultDisplayLayout,
) -> None:
    rect = layout.chart_rect
    plot = layout.chart_plot_rect
    _draw_panel(screen, rect)
    title_font = get_font(18, bold=True)
    axis_font = get_font(10)
    screen.blit(title_font.render("VP推移", True, COLORS["WHITE"]), (rect.x + 15, rect.y + 11))
    timeline_text = f"開始 → {result.turn_count}{result.timeline_unit}"
    timeline_surface = axis_font.render(timeline_text, True, COLORS["TEXT_MUTED"])
    screen.blit(timeline_surface, (rect.right - timeline_surface.get_width() - 14, rect.y + 17))

    pygame.draw.rect(screen, (10, 17, 27), plot, border_radius=8)
    max_recorded = max(
        (
            value
            for snapshot in result.vp_progression
            for value in snapshot.points.values()
        ),
        default=0,
    )
    chart_max = max(1, result.victory_target, max_recorded)
    for step in range(5):
        ratio = step / 4
        y = round(plot.bottom - 1 - ratio * (plot.height - 1))
        pygame.draw.line(screen, (49, 65, 81), (plot.x, y), (plot.right - 1, y), 1)
        label = str(round(chart_max * ratio))
        label_surface = axis_font.render(label, True, COLORS["TEXT_MUTED"])
        screen.blit(label_surface, (plot.x - label_surface.get_width() - 7, y - 6))

    if result.vp_progression:
        minimum_turn = result.vp_progression[0].turn
        maximum_turn = max(minimum_turn + 1, result.vp_progression[-1].turn)
        for player in result.players:
            points = []
            for snapshot in result.vp_progression:
                x_ratio = (snapshot.turn - minimum_turn) / (maximum_turn - minimum_turn)
                x = round(plot.x + x_ratio * (plot.width - 1))
                y_ratio = min(chart_max, snapshot.points.get(player.name, 0)) / chart_max
                y = round(plot.bottom - 1 - y_ratio * (plot.height - 1))
                points.append((x, y))
            if len(points) >= 2:
                pygame.draw.lines(screen, player.color, False, points, 3)
            elif points:
                pygame.draw.circle(screen, player.color, points[0], 4)
            for point in points:
                pygame.draw.circle(screen, player.color, point, 3)
    else:
        message_font = get_font(12)
        message = message_font.render("VP推移データはありません", True, COLORS["TEXT_MUTED"])
        screen.blit(message, message.get_rect(center=plot.center))

    legend_font = get_font(10)
    legend_width = max(1, (rect.width - 28) // len(result.players))
    legend_y = rect.bottom - 22
    for index, player in enumerate(result.players):
        legend_x = rect.x + 14 + index * legend_width
        pygame.draw.circle(screen, player.color, (legend_x + 5, legend_y + 5), 4)
        label = _truncate(legend_font, player.name, legend_width - 17)
        screen.blit(
            legend_font.render(label, True, COLORS["TEXT_MUTED"]),
            (legend_x + 13, legend_y - 2),
        )


def _draw_events(
    screen: pygame.Surface,
    result: MatchResultSummary,
    layout: ResultDisplayLayout,
) -> None:
    rect = layout.events_rect
    _draw_panel(screen, rect)
    title_font = get_font(19, bold=True)
    count_font = get_font(12)
    screen.blit(title_font.render("重要イベント", True, COLORS["WHITE"]), (rect.x + 15, rect.y + 12))
    count_surface = count_font.render(
        f"{len(result.important_events)}件",
        True,
        COLORS["TEXT_MUTED"],
    )
    screen.blit(count_surface, (rect.right - count_surface.get_width() - 15, rect.y + 18))

    if not result.important_events:
        empty_font = get_font(14)
        message = empty_font.render("重要イベントは記録されていません", True, COLORS["TEXT_MUTED"])
        screen.blit(message, message.get_rect(center=rect.center))

    number_font = get_font(12, bold=True)
    event_font = get_font(14, bold=True)
    detail_font = get_font(11)
    meta_font = get_font(10)
    player_colors = {player.name: player.color for player in result.players}
    for hitbox in layout.event_hitboxes:
        event = result.important_events[hitbox.event_index]
        row_rect = hitbox.rect
        selected = hitbox.event_index == layout.selected_event_index
        fill = (48, 66, 74) if selected else COLORS["CARD_BG"]
        border = COLORS["WARNING"] if selected else COLORS["CARD_BORDER"]
        pygame.draw.rect(screen, fill, row_rect, border_radius=11)
        pygame.draw.rect(screen, border, row_rect, 2 if selected else 1, border_radius=11)
        if selected:
            pygame.draw.rect(
                screen,
                COLORS["WARNING"],
                pygame.Rect(row_rect.x, row_rect.y + 8, 4, row_rect.height - 16),
                border_radius=2,
            )

        badge_center = (row_rect.x + 24, row_rect.centery)
        event_color = player_colors.get(event.player_name, COLORS["BUTTON_ACTIVE"])
        pygame.draw.circle(screen, event_color, badge_center, 14)
        badge = number_font.render(str(hitbox.event_index + 1), True, COLORS["WHITE"])
        screen.blit(badge, badge.get_rect(center=badge_center))

        text_x = row_rect.x + 47
        meta_parts = []
        if event.turn is not None:
            meta_parts.append(f"T{event.turn}")
        if event.player_name:
            meta_parts.append(event.player_name)
        meta_text = " ・ ".join(meta_parts)
        meta_surface = meta_font.render(meta_text, True, event_color)
        meta_x = row_rect.right - meta_surface.get_width() - 9
        title_width = max(40, meta_x - text_x - 8)
        title = _truncate(event_font, event.title, title_width)
        screen.blit(event_font.render(title, True, COLORS["WHITE"]), (text_x, row_rect.y + 8))
        screen.blit(meta_surface, (meta_x, row_rect.y + 10))
        detail = event.detail or (
            f"リプレイ frame {event.replay_frame}"
            if event.replay_frame is not None
            else "詳細記録なし"
        )
        detail = _truncate(detail_font, detail, row_rect.right - text_x - 10)
        screen.blit(
            detail_font.render(detail, True, COLORS["TEXT_MUTED"]),
            (text_x, row_rect.y + 34),
        )

    footer_font = get_font(10)
    if layout.event_hitboxes:
        first = layout.event_hitboxes[0].event_index + 1
        last = layout.event_hitboxes[-1].event_index + 1
        footer = (
            f"{first}–{last} / {len(result.important_events)}  ・  "
            "↑↓/Wheel 選択・Enter 再生・L ログ"
        )
    else:
        footer = "記録されたイベントからリプレイへ移動できます"
    screen.blit(
        footer_font.render(footer, True, COLORS["TEXT_MUTED"]),
        (rect.x + 14, rect.bottom - 25),
    )


def _draw_controls(
    screen: pygame.Surface,
    layout: ResultDisplayLayout,
) -> None:
    _draw_panel(screen, layout.controls_rect, 246)
    font = get_font(14, bold=True)
    for action in RESULT_ACTIONS:
        rect = layout.action_rects[action]
        enabled = action in layout.enabled_actions
        if action == REPLAY_SELECTED_ACTION and enabled:
            fill = COLORS["BUTTON_ACTIVE"]
            border = COLORS["WARNING"]
        elif action == NEW_BOARD_ACTION:
            fill = COLORS["BUTTON_HIGHLIGHT"]
            border = COLORS["SUCCESS"]
        elif enabled:
            fill = COLORS["BUTTON"]
            border = COLORS["CARD_BORDER"]
        else:
            fill = COLORS["BUTTON_DISABLED"]
            border = COLORS["CARD_BORDER"]
        pygame.draw.rect(screen, fill, rect, border_radius=12)
        pygame.draw.rect(screen, border, rect, 2, border_radius=12)
        label_color = COLORS["BUTTON_TEXT"] if enabled else COLORS["TEXT_MUTED"]
        label = _truncate(font, _ACTION_LABELS[action], rect.width - 22)
        surface = font.render(label, True, label_color)
        screen.blit(surface, surface.get_rect(center=rect.center))


def draw_result_display(
    screen: pygame.Surface,
    summary: Any,
    selected_event_index: Optional[int] = 0,
) -> ResultDisplayLayout:
    """Draw the complete post-match screen and return its integration layout."""

    result = normalise_match_result(summary)
    selected = (
        None
        if not result.important_events
        else max(
            0,
            min(int(selected_event_index or 0), len(result.important_events) - 1),
        )
    )
    replay_enabled = (
        selected is not None
        and result.important_events[selected].replay_frame is not None
    )
    layout = build_result_display_layout(
        screen.get_size(),
        len(result.players),
        len(result.important_events),
        selected_event_index,
        replay_enabled=replay_enabled,
    )
    _draw_background(screen)
    _draw_header(screen, result, layout.header_rect)
    _draw_standings(screen, result, layout)
    _draw_chart(screen, result, layout)
    _draw_events(screen, result, layout)
    _draw_controls(screen, layout)
    return layout


__all__ = (
    "ImportantResultEvent",
    "MatchResultSummary",
    "NEW_BOARD_ACTION",
    "REPLAY_SELECTED_ACTION",
    "RESTART_SAME_BOARD_ACTION",
    "RESULT_ACTIONS",
    "ResultDisplayLayout",
    "ResultEventHitbox",
    "ResultHitTarget",
    "ResultPlayerSummary",
    "VictoryPointSnapshot",
    "build_result_display_layout",
    "draw_result_display",
    "hit_test_result_display",
    "normalise_match_result",
    "result_player_role_label",
    "selected_replay_frame",
)
