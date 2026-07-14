"""Responsive, transport-independent Pygame presentation for the LAN lobby."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Sequence

import pygame

from game.assets import get_font
from game.constants import COLORS


DEFAULT_LAN_LOBBY_SIZE = (1920, 1280)
LAN_LOBBY_MODES = frozenset(
    {"home", "create", "join", "connected", "disconnected"}
)

ACTION_CLOSE = "lobby_close"
ACTION_BACK = "lobby_back"
ACTION_MODE_CREATE = "lobby_mode_create"
ACTION_MODE_JOIN = "lobby_mode_join"
ACTION_MODE_SPECTATE = "lobby_mode_spectate"
ACTION_INPUT_NAME = "lobby_input_name"
ACTION_INPUT_ADDRESS = "lobby_input_address"
ACTION_INPUT_ROOM_CODE = "lobby_input_room_code"
ACTION_SPECTATOR_TOGGLE = "lobby_spectator_toggle"
ACTION_CREATE_ROOM = "lobby_create_room"
ACTION_JOIN_ROOM = "lobby_join_room"
ACTION_COPY_ROOM_CODE = "lobby_copy_room_code"
ACTION_TOGGLE_READY = "lobby_toggle_ready"
ACTION_START_MATCH = "lobby_start_match"
ACTION_LEAVE_ROOM = "lobby_leave_room"
ACTION_RECONNECT = "lobby_reconnect"

INPUT_ACTIONS = frozenset(
    {ACTION_INPUT_NAME, ACTION_INPUT_ADDRESS, ACTION_INPUT_ROOM_CODE}
)


@dataclass(frozen=True)
class LanLobbyDisplayState:
    """Immutable UI input; networking and game objects stay outside this module."""

    mode: str = "home"
    name: str = ""
    address: str = ""
    room_code: str = ""
    spectator: bool = False
    connecting: bool = False
    error: str = ""
    lobby_snapshot: Mapping[str, Any] | None = None
    local_role: str | None = None
    local_seat: int | None = None
    focused_field: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in LAN_LOBBY_MODES:
            raise ValueError(f"unsupported LAN lobby display mode: {self.mode}")
        if self.local_role not in (None, "host", "player", "spectator"):
            raise ValueError("local_role must be host, player, spectator, or None")
        if self.local_seat is not None and (
            type(self.local_seat) is not int or not 1 <= self.local_seat <= 4
        ):
            raise ValueError("local_seat must be one-based and between 1 and 4")
        if self.focused_field is not None and self.focused_field not in INPUT_ACTIONS:
            raise ValueError("focused_field is not a lobby input action")


@dataclass(frozen=True)
class LanLobbyControl:
    action: str
    label: str
    rect: pygame.Rect
    kind: str = "button"
    enabled: bool = True
    selected: bool = False
    value: str = ""
    placeholder: str = ""


@dataclass(frozen=True)
class LanLobbySeatLayout:
    seat: int
    rect: pygame.Rect


@dataclass(frozen=True)
class LanLobbyLayout:
    screen_rect: pygame.Rect
    shell_rect: pygame.Rect
    header_rect: pygame.Rect
    content_rect: pygame.Rect
    footer_rect: pygame.Rect
    primary_rect: pygame.Rect
    secondary_rect: pygame.Rect
    controls: tuple[LanLobbyControl, ...]
    seat_layouts: tuple[LanLobbySeatLayout, ...] = ()

    @property
    def control_by_action(self) -> dict[str, LanLobbyControl]:
        return {control.action: control for control in self.controls}


@dataclass(frozen=True)
class LanLobbyHitTarget:
    action: str
    kind: str


def build_lan_lobby_layout(
    size: Sequence[int], state: LanLobbyDisplayState
) -> LanLobbyLayout:
    """Build deterministic rectangles for a 1920x1280 or compact window."""

    width, height = _validated_size(size)
    screen_rect = pygame.Rect(0, 0, width, height)
    margin_x = _clamp(round(width * 0.03), 18, 58)
    margin_y = _clamp(round(height * 0.025), 14, 38)
    shell_rect = screen_rect.inflate(-margin_x * 2, -margin_y * 2)

    header_height = _clamp(round(height * 0.105), 72, 124)
    footer_height = _clamp(round(height * 0.09), 62, 96)
    gap = _clamp(round(min(width, height) * 0.014), 10, 22)
    header_rect = pygame.Rect(
        shell_rect.x,
        shell_rect.y,
        shell_rect.width,
        header_height,
    )
    footer_rect = pygame.Rect(
        shell_rect.x,
        shell_rect.bottom - footer_height,
        shell_rect.width,
        footer_height,
    )
    content_rect = pygame.Rect(
        shell_rect.x,
        header_rect.bottom + gap,
        shell_rect.width,
        footer_rect.top - header_rect.bottom - gap * 2,
    )

    controls: list[LanLobbyControl] = []
    seat_layouts: list[LanLobbySeatLayout] = []
    primary_rect = content_rect.copy()
    secondary_rect = pygame.Rect(0, 0, 0, 0)

    if state.mode == "home":
        primary_rect = content_rect.inflate(-gap * 2, -gap)
        card_gap = gap
        card_width = (primary_rect.width - card_gap * 2) // 3
        card_height = min(
            primary_rect.height,
            _clamp(round(height * 0.40), 250, 440),
        )
        card_y = primary_rect.centery - card_height // 2
        home_specs = (
            (ACTION_MODE_CREATE, "部屋を作成"),
            (ACTION_MODE_JOIN, "参加する"),
            (ACTION_MODE_SPECTATE, "観戦する"),
        )
        for index, (action, label) in enumerate(home_specs):
            controls.append(
                LanLobbyControl(
                    action,
                    label,
                    pygame.Rect(
                        primary_rect.x + index * (card_width + card_gap),
                        card_y,
                        card_width,
                        card_height,
                    ),
                    kind="mode_card",
                )
            )
        controls.append(
            _footer_button(footer_rect, ACTION_CLOSE, "ローカル設定へ戻る")
        )
    elif state.mode in ("create", "join"):
        primary_rect, secondary_rect = _split_content(content_rect, gap, 0.56)
        controls.extend(_build_form_controls(primary_rect, state, gap))
        controls.append(_footer_button(footer_rect, ACTION_BACK, "戻る"))
    elif state.mode == "connected":
        primary_rect, secondary_rect = _split_content(content_rect, gap, 0.66)
        snapshot = _snapshot(state)
        player_count = _safe_int(
            _mapping(snapshot.get("settings")).get("player_count"),
            4,
            2,
            4,
        )
        seat_layouts.extend(
            _build_seat_layouts(primary_rect, player_count, gap)
        )
        controls.extend(
            _build_connected_controls(secondary_rect, state, snapshot, gap)
        )
        leave_label = (
            "対局を終了して退出"
            if snapshot.get("phase") == "started"
            else "ロビーを退出"
        )
        controls.append(
            _footer_button(footer_rect, ACTION_LEAVE_ROOM, leave_label)
        )
    else:
        panel_width = min(content_rect.width, max(560, round(width * 0.48)))
        panel_height = min(content_rect.height, max(290, round(height * 0.38)))
        primary_rect = pygame.Rect(
            content_rect.centerx - panel_width // 2,
            content_rect.centery - panel_height // 2,
            panel_width,
            panel_height,
        )
        reconnect_width = min(primary_rect.width - gap * 4, 420)
        button_height = _control_height(height)
        controls.append(
            LanLobbyControl(
                ACTION_RECONNECT,
                "再接続する",
                pygame.Rect(
                    primary_rect.centerx - reconnect_width // 2,
                    primary_rect.bottom - button_height - gap * 2,
                    reconnect_width,
                    button_height,
                ),
                enabled=not state.connecting,
                selected=state.connecting,
            )
        )
        controls.append(_footer_button(footer_rect, ACTION_BACK, "ローカルへ戻る"))

    return LanLobbyLayout(
        screen_rect=screen_rect,
        shell_rect=shell_rect,
        header_rect=header_rect,
        content_rect=content_rect,
        footer_rect=footer_rect,
        primary_rect=primary_rect,
        secondary_rect=secondary_rect,
        controls=tuple(controls),
        seat_layouts=tuple(seat_layouts),
    )


def hit_test_lan_lobby_display(
    layout: LanLobbyLayout, pos: Sequence[int]
) -> LanLobbyHitTarget | None:
    """Return the enabled stable action under ``pos``."""

    if not isinstance(pos, Sequence) or len(pos) != 2:
        return None
    try:
        point = (int(pos[0]), int(pos[1]))
    except (TypeError, ValueError, OverflowError):
        return None
    for control in reversed(layout.controls):
        if control.enabled and control.rect.collidepoint(point):
            return LanLobbyHitTarget(control.action, control.kind)
    return None


def draw_lan_lobby_display(
    surface: pygame.Surface, state: LanLobbyDisplayState
) -> LanLobbyLayout:
    """Draw the current lobby mode and return its clickable layout."""

    if not isinstance(surface, pygame.Surface):
        raise TypeError("surface must be a pygame.Surface")
    layout = build_lan_lobby_layout(surface.get_size(), state)
    surface.blit(_background_surface(surface.get_size()), (0, 0))
    _draw_shell(surface, layout.shell_rect)
    _draw_header(surface, layout, state)

    if state.mode == "home":
        _draw_home(surface, layout)
    elif state.mode in ("create", "join"):
        _draw_form(surface, layout, state)
    elif state.mode == "connected":
        _draw_connected(surface, layout, state)
    else:
        _draw_disconnected(surface, layout, state)

    for control in layout.controls:
        _draw_control(surface, control, layout.screen_rect.height)
    _draw_error(surface, layout, state)
    return layout


def _build_form_controls(
    rect: pygame.Rect,
    state: LanLobbyDisplayState,
    gap: int,
) -> list[LanLobbyControl]:
    header_space = _clamp(round(rect.height * 0.12), 62, 86)
    inner = pygame.Rect(
        rect.x + gap * 3,
        rect.y + header_space,
        rect.width - gap * 6,
        rect.height - header_space - gap * 2,
    )
    control_height = _control_height(rect.height)
    label_gap = max(20, control_height // 2)
    row_step = control_height + label_gap + gap
    controls = [
        _input_control(
            ACTION_INPUT_NAME,
            "表示名",
            state.name,
            "Player name",
            pygame.Rect(inner.x, inner.y + label_gap, inner.width, control_height),
            state,
        ),
        _input_control(
            ACTION_INPUT_ADDRESS,
            "接続先アドレス",
            state.address,
            "192.168.1.10:47624",
            pygame.Rect(
                inner.x,
                inner.y + label_gap + row_step,
                inner.width,
                control_height,
            ),
            state,
        ),
    ]
    next_y = inner.y + label_gap + row_step * 2
    if state.mode == "join":
        controls.append(
            _input_control(
                ACTION_INPUT_ROOM_CODE,
                "参加コード",
                state.room_code,
                "ABC234",
                pygame.Rect(inner.x, next_y, inner.width, control_height),
                state,
            )
        )
        next_y += row_step
        toggle_width = min(inner.width, max(240, inner.width // 2))
        controls.append(
            LanLobbyControl(
                ACTION_SPECTATOR_TOGGLE,
                "観戦者として参加",
                pygame.Rect(inner.x, next_y, toggle_width, control_height),
                kind="toggle",
                enabled=not state.connecting,
                selected=state.spectator,
            )
        )
        next_y += control_height + gap
        action = ACTION_JOIN_ROOM
        label = "接続中…" if state.connecting else "ロビーへ参加"
    else:
        action = ACTION_CREATE_ROOM
        label = "作成中…" if state.connecting else "部屋を作成"

    controls.append(
        LanLobbyControl(
            action,
            label,
            pygame.Rect(inner.x, next_y, inner.width, control_height),
            enabled=(
                not state.connecting
                and bool(state.name.strip())
                and bool(state.address.strip())
                and (state.mode == "create" or bool(state.room_code.strip()))
            ),
            selected=state.connecting,
        )
    )
    return controls


def _build_connected_controls(
    rect: pygame.Rect,
    state: LanLobbyDisplayState,
    snapshot: Mapping[str, Any],
    gap: int,
) -> list[LanLobbyControl]:
    inner = rect.inflate(-gap * 2, -gap * 2)
    height = _control_height(rect.height)
    controls: list[LanLobbyControl] = []
    # Keep the action stack below all four room-detail rows on compact layouts.
    y = inner.y + max(225, round(rect.height * 0.43))
    room_code = _safe_text(snapshot.get("room_code") or state.room_code, 20)
    controls.append(
        LanLobbyControl(
            ACTION_COPY_ROOM_CODE,
            "参加コードをコピー",
            pygame.Rect(inner.x, y, inner.width, height),
            enabled=bool(room_code) and not state.connecting,
        )
    )
    y += height + gap

    if snapshot.get("phase") == "started":
        controls.append(
            LanLobbyControl(
                ACTION_START_MATCH,
                "対局画面へ戻る",
                pygame.Rect(inner.x, y, inner.width, height),
                enabled=not state.connecting,
                selected=True,
            )
        )
        return controls

    member = _local_member(snapshot, state.local_seat)
    local_ready = bool(member.get("ready", False))
    if state.local_role in ("host", "player"):
        controls.append(
            LanLobbyControl(
                ACTION_TOGGLE_READY,
                "準備を取り消す" if local_ready else "準備完了",
                pygame.Rect(inner.x, y, inner.width, height),
                enabled=not state.connecting,
                selected=local_ready,
            )
        )
        y += height + gap
    if state.local_role == "host":
        controls.append(
            LanLobbyControl(
                ACTION_START_MATCH,
                "対局を開始",
                pygame.Rect(inner.x, y, inner.width, height),
                enabled=bool(snapshot.get("can_start", False))
                and not state.connecting,
                selected=bool(snapshot.get("can_start", False)),
            )
        )
    return controls


def _build_seat_layouts(
    rect: pygame.Rect, player_count: int, gap: int
) -> list[LanLobbySeatLayout]:
    padding = gap * 2
    header_space = _clamp(round(rect.height * 0.12), 58, 82)
    inner = pygame.Rect(
        rect.x + padding,
        rect.y + header_space,
        rect.width - padding * 2,
        rect.height - header_space - padding,
    )
    columns = 2
    rows = (player_count + columns - 1) // columns
    seat_width = (inner.width - gap) // columns
    seat_height = (inner.height - gap * (rows - 1)) // rows
    result = []
    for index in range(player_count):
        row, column = divmod(index, columns)
        result.append(
            LanLobbySeatLayout(
                index + 1,
                pygame.Rect(
                    inner.x + column * (seat_width + gap),
                    inner.y + row * (seat_height + gap),
                    seat_width,
                    seat_height,
                ),
            )
        )
    return result


def _split_content(
    rect: pygame.Rect, gap: int, primary_ratio: float
) -> tuple[pygame.Rect, pygame.Rect]:
    primary_width = round((rect.width - gap) * primary_ratio)
    primary = pygame.Rect(rect.x, rect.y, primary_width, rect.height)
    secondary = pygame.Rect(
        primary.right + gap,
        rect.y,
        rect.right - primary.right - gap,
        rect.height,
    )
    return primary, secondary


def _footer_button(rect: pygame.Rect, action: str, label: str) -> LanLobbyControl:
    height = min(46, rect.height - 12)
    width = min(300, max(200, rect.width // 4))
    return LanLobbyControl(
        action,
        label,
        pygame.Rect(
            rect.x + 12,
            rect.centery - height // 2,
            width,
            height,
        ),
        kind="secondary_button",
    )


def _input_control(
    action: str,
    label: str,
    value: str,
    placeholder: str,
    rect: pygame.Rect,
    state: LanLobbyDisplayState,
) -> LanLobbyControl:
    return LanLobbyControl(
        action,
        label,
        rect,
        kind="input",
        enabled=not state.connecting,
        selected=state.focused_field == action,
        value=_safe_text(value, 96),
        placeholder=placeholder,
    )


def _draw_shell(surface: pygame.Surface, rect: pygame.Rect) -> None:
    shadow = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(shadow, (3, 8, 16, 135), shadow.get_rect(), border_radius=26)
    surface.blit(shadow, rect.move(5, 7).topleft)
    pygame.draw.rect(surface, COLORS["PANEL_BG"], rect, border_radius=24)
    pygame.draw.rect(surface, COLORS["PANEL_BORDER"], rect, 2, border_radius=24)


def _draw_header(
    surface: pygame.Surface,
    layout: LanLobbyLayout,
    state: LanLobbyDisplayState,
) -> None:
    title_size = _font_size(layout.screen_rect.height, 40, 28, 48)
    subtitle_size = _font_size(layout.screen_rect.height, 20, 15, 24)
    title = get_font(title_size).render("LAN対戦ロビー", True, COLORS["WHITE"])
    title_pos = (layout.header_rect.x + 28, layout.header_rect.y + 12)
    surface.blit(title, title_pos)
    subtitles = {
        "home": "遊び方を選んでください",
        "create": "この端末をホストにして部屋を作成",
        "join": "同じLAN内の部屋へ参加",
        "connected": "参加者の準備を確認して対局開始",
        "disconnected": "接続が切れました",
    }
    subtitle = get_font(subtitle_size).render(
        subtitles[state.mode], True, COLORS["TEXT_MUTED"]
    )
    subtitle_y = min(
        title_pos[1] + title.get_height() + 2,
        layout.header_rect.bottom - subtitle.get_height() - 8,
    )
    surface.blit(subtitle, (layout.header_rect.x + 30, subtitle_y))

    status = "接続中…" if state.connecting else "LAN / private room"
    status_surface = get_font(subtitle_size).render(
        status,
        True,
        COLORS["WARNING"] if state.connecting else COLORS["SUCCESS"],
    )
    surface.blit(
        status_surface,
        (
            layout.header_rect.right - status_surface.get_width() - 28,
            layout.header_rect.centery - status_surface.get_height() // 2,
        ),
    )


def _draw_home(surface: pygame.Surface, layout: LanLobbyLayout) -> None:
    descriptions = {
        ACTION_MODE_CREATE: ("HOST", "新しい部屋を作り、参加コードを共有します。"),
        ACTION_MODE_JOIN: ("PLAYER", "名前と参加コードを入力して席へ参加します。"),
        ACTION_MODE_SPECTATE: ("WATCH", "手札を伏せた公開情報だけで観戦します。"),
    }
    for control in layout.controls:
        if control.kind != "mode_card":
            continue
        _draw_panel(surface, control.rect, accent=True)
        badge, description = descriptions[control.action]
        badge_font = get_font(_font_size(layout.screen_rect.height, 17, 13, 20))
        title_font = get_font(_font_size(layout.screen_rect.height, 28, 20, 34))
        body_font = get_font(_font_size(layout.screen_rect.height, 18, 14, 21))
        badge_surface = badge_font.render(badge, True, COLORS["WARNING"])
        surface.blit(badge_surface, (control.rect.x + 24, control.rect.y + 24))
        title_surface = title_font.render(control.label, True, COLORS["WHITE"])
        surface.blit(title_surface, (control.rect.x + 24, control.rect.y + 62))
        _draw_wrapped_text(
            surface,
            body_font,
            description,
            COLORS["TEXT_MUTED"],
            pygame.Rect(
                control.rect.x + 24,
                control.rect.y + 112,
                control.rect.width - 48,
                control.rect.height - 136,
            ),
            max_lines=4,
        )


def _draw_form(
    surface: pygame.Surface,
    layout: LanLobbyLayout,
    state: LanLobbyDisplayState,
) -> None:
    _draw_panel(surface, layout.primary_rect)
    _draw_panel(surface, layout.secondary_rect)
    title = "部屋を作成" if state.mode == "create" else "部屋へ参加"
    _draw_section_title(surface, layout.primary_rect, title, layout.screen_rect.height)
    info_title = "ホストの役割" if state.mode == "create" else "参加前の確認"
    _draw_section_title(surface, layout.secondary_rect, info_title, layout.screen_rect.height)
    info = (
        "盤面設定と開始操作はホストが管理します。作成後に表示される6文字の参加コードを、同じLAN内の相手へ共有してください。"
        if state.mode == "create"
        else "addressにはホストのLANアドレスを入力します。観戦では席を消費せず、全員の非公開カードは伏せられます。"
    )
    _draw_wrapped_text(
        surface,
        get_font(_font_size(layout.screen_rect.height, 19, 14, 22)),
        info,
        COLORS["TEXT_MUTED"],
        layout.secondary_rect.inflate(-36, -90).move(0, 38),
        max_lines=8,
    )


def _draw_connected(
    surface: pygame.Surface,
    layout: LanLobbyLayout,
    state: LanLobbyDisplayState,
) -> None:
    snapshot = _snapshot(state)
    _draw_panel(surface, layout.primary_rect)
    _draw_panel(surface, layout.secondary_rect)
    _draw_section_title(surface, layout.primary_rect, "プレイヤー席", layout.screen_rect.height)
    _draw_section_title(surface, layout.secondary_rect, "ルーム情報", layout.screen_rect.height)

    raw_members = snapshot.get("members", ())
    if not isinstance(raw_members, (tuple, list)):
        raw_members = ()
    members = [
        member
        for member in raw_members
        if isinstance(member, Mapping)
    ]
    member_by_seat = {
        member.get("seat"): member
        for member in members
        if type(member.get("seat")) is int
    }
    for seat_layout in layout.seat_layouts:
        _draw_seat_card(
            surface,
            seat_layout,
            member_by_seat.get(seat_layout.seat),
            state,
            layout.screen_rect.height,
        )

    room_code = _safe_text(snapshot.get("room_code") or state.room_code, 20) or "------"
    settings = _mapping(snapshot.get("settings"))
    spectators = _safe_int(snapshot.get("spectators"), 0, 0, 999)
    info_font = get_font(_font_size(layout.screen_rect.height, 18, 14, 21))
    code_font = get_font(_font_size(layout.screen_rect.height, 30, 22, 36))
    code_surface = code_font.render(room_code, True, COLORS["WARNING"])
    surface.blit(code_surface, (layout.secondary_rect.x + 24, layout.secondary_rect.y + 62))
    address = _truncate(
        info_font,
        _safe_text(state.address, 260) or "address未設定",
        layout.secondary_rect.width - 48,
    )
    details = (
        f"接続 {address}\n"
        f"観戦 {spectators}人 / 勝利 {settings.get('victory_target', 10)} VP\n"
        f"盤面 {settings.get('board_mode', 'constrained')}\n"
        f"seed {settings.get('board_seed', 0)}"
    )
    _draw_multiline(
        surface,
        info_font,
        details,
        COLORS["TEXT_MUTED"],
        layout.secondary_rect.x + 24,
        layout.secondary_rect.y + 112,
    )


def _draw_disconnected(
    surface: pygame.Surface,
    layout: LanLobbyLayout,
    state: LanLobbyDisplayState,
) -> None:
    _draw_panel(surface, layout.primary_rect, danger=True)
    icon_center = (layout.primary_rect.centerx, layout.primary_rect.y + 76)
    pygame.draw.circle(surface, COLORS["DANGER"], icon_center, 24, 3)
    pygame.draw.line(
        surface,
        COLORS["DANGER"],
        (icon_center[0] - 8, icon_center[1] - 8),
        (icon_center[0] + 8, icon_center[1] + 8),
        3,
    )
    pygame.draw.line(
        surface,
        COLORS["DANGER"],
        (icon_center[0] + 8, icon_center[1] - 8),
        (icon_center[0] - 8, icon_center[1] + 8),
        3,
    )
    title_font = get_font(_font_size(layout.screen_rect.height, 28, 20, 34))
    body_font = get_font(_font_size(layout.screen_rect.height, 18, 14, 21))
    title = title_font.render("ホストとの接続が切れました", True, COLORS["WHITE"])
    surface.blit(title, title.get_rect(center=(layout.primary_rect.centerx, icon_center[1] + 54)))
    body = "予約時間内なら同じ席へ再接続できます。対局状態は権威サーバー側に保持されています。"
    _draw_wrapped_text(
        surface,
        body_font,
        body,
        COLORS["TEXT_MUTED"],
        pygame.Rect(
            layout.primary_rect.x + 42,
            icon_center[1] + 80,
            layout.primary_rect.width - 84,
            72,
        ),
        max_lines=3,
        center=True,
    )


def _draw_seat_card(
    surface: pygame.Surface,
    seat_layout: LanLobbySeatLayout,
    member: Mapping[str, Any] | None,
    state: LanLobbyDisplayState,
    screen_height: int,
) -> None:
    rect = seat_layout.rect
    local = seat_layout.seat == state.local_seat and state.local_role != "spectator"
    connected = bool(member and member.get("connected", False))
    ready = bool(member and member.get("ready", False))
    fill = COLORS["CARD_BG"] if member else (26, 38, 52)
    border = COLORS["BUTTON_HIGHLIGHT_BORDER"] if local else COLORS["CARD_BORDER"]
    pygame.draw.rect(surface, fill, rect, border_radius=16)
    pygame.draw.rect(surface, border, rect, 3 if local else 2, border_radius=16)

    small = get_font(_font_size(screen_height, 16, 12, 19))
    title = get_font(_font_size(screen_height, 23, 17, 27))
    seat_surface = small.render(f"SEAT {seat_layout.seat}", True, COLORS["WARNING"])
    surface.blit(seat_surface, (rect.x + 18, rect.y + 16))
    if member:
        name = _safe_text(member.get("display_name"), 36) or f"Player {seat_layout.seat}"
        if local:
            name += "  ・あなた"
        name_surface = title.render(_truncate(title, name, rect.width - 36), True, COLORS["WHITE"])
        surface.blit(name_surface, (rect.x + 18, rect.y + 48))
        if not connected:
            seconds = _safe_int(member.get("reservation_seconds_remaining"), 0, 0, 999)
            status = f"再接続待ち {seconds}秒"
            color = COLORS["DANGER"]
        elif ready:
            status = "準備完了"
            color = COLORS["SUCCESS"]
        else:
            status = "準備中"
            color = COLORS["TEXT_MUTED"]
        role = _safe_text(member.get("role"), 16).upper()
        detail = small.render(f"{role}  /  {status}", True, color)
    else:
        name_surface = title.render("募集中", True, COLORS["TEXT_MUTED"])
        surface.blit(name_surface, (rect.x + 18, rect.y + 48))
        detail = small.render("参加コードでこの席へ参加", True, COLORS["TEXT_MUTED"])
    surface.blit(detail, (rect.x + 18, rect.bottom - detail.get_height() - 16))


def _draw_control(
    surface: pygame.Surface,
    control: LanLobbyControl,
    screen_height: int,
) -> None:
    if control.kind == "mode_card":
        return
    font = get_font(_font_size(screen_height, 18, 14, 21))
    if control.kind == "input":
        label = font.render(control.label, True, COLORS["TEXT_MUTED"])
        surface.blit(label, (control.rect.x + 2, control.rect.y - label.get_height() - 5))
        fill = (31, 45, 61) if control.enabled else COLORS["BUTTON_DISABLED"]
        border = COLORS["WARNING"] if control.selected else COLORS["PANEL_BORDER"]
        pygame.draw.rect(surface, fill, control.rect, border_radius=11)
        pygame.draw.rect(surface, border, control.rect, 2, border_radius=11)
        shown = control.value or control.placeholder
        color = COLORS["WHITE"] if control.value else COLORS["TEXT_MUTED"]
        shown = _truncate(font, shown, control.rect.width - 30)
        text = font.render(shown, True, color)
        surface.blit(text, (control.rect.x + 14, control.rect.centery - text.get_height() // 2))
        if control.selected:
            cursor_x = min(control.rect.right - 12, control.rect.x + 14 + text.get_width() + 2)
            pygame.draw.line(
                surface,
                COLORS["WARNING"],
                (cursor_x, control.rect.y + 12),
                (cursor_x, control.rect.bottom - 12),
                2,
            )
        return

    if not control.enabled:
        fill = COLORS["BUTTON_DISABLED"]
        border = (88, 98, 108)
        text_color = (155, 165, 174)
    elif control.selected:
        fill = COLORS["BUTTON_HIGHLIGHT"]
        border = COLORS["BUTTON_HIGHLIGHT_BORDER"]
        text_color = COLORS["WHITE"]
    elif control.kind == "secondary_button":
        fill = COLORS["CARD_BG"]
        border = COLORS["CARD_BORDER"]
        text_color = COLORS["TEXT_MUTED"]
    else:
        fill = COLORS["BUTTON_ACTIVE"]
        border = COLORS["PANEL_BORDER"]
        text_color = COLORS["BUTTON_TEXT"]
    pygame.draw.rect(surface, fill, control.rect, border_radius=12)
    pygame.draw.rect(surface, border, control.rect, 2, border_radius=12)
    prefix = "✓ " if control.kind == "toggle" and control.selected else ""
    label = _truncate(font, prefix + control.label, control.rect.width - 20)
    text = font.render(label, True, text_color)
    surface.blit(text, text.get_rect(center=control.rect.center))


def _draw_error(
    surface: pygame.Surface,
    layout: LanLobbyLayout,
    state: LanLobbyDisplayState,
) -> None:
    error = _safe_text(state.error, 240)
    if not error:
        return
    font = get_font(_font_size(layout.screen_rect.height, 17, 13, 20))
    max_width = min(layout.footer_rect.width // 2, 760)
    text = _truncate(font, error, max_width - 24)
    text_surface = font.render(text, True, COLORS["DANGER"])
    rect = pygame.Rect(
        layout.footer_rect.right - max_width - 12,
        layout.footer_rect.centery - 20,
        max_width,
        40,
    )
    pygame.draw.rect(surface, (63, 32, 38), rect, border_radius=10)
    pygame.draw.rect(surface, COLORS["DANGER"], rect, 1, border_radius=10)
    surface.blit(text_surface, text_surface.get_rect(center=rect.center))


def _draw_panel(
    surface: pygame.Surface,
    rect: pygame.Rect,
    *,
    accent: bool = False,
    danger: bool = False,
) -> None:
    pygame.draw.rect(surface, COLORS["CARD_BG"], rect, border_radius=18)
    border = (
        COLORS["DANGER"]
        if danger
        else COLORS["BUTTON_HIGHLIGHT_BORDER"]
        if accent
        else COLORS["CARD_BORDER"]
    )
    pygame.draw.rect(surface, border, rect, 2, border_radius=18)


def _draw_section_title(
    surface: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    screen_height: int,
) -> None:
    font = get_font(_font_size(screen_height, 23, 17, 28))
    title = font.render(label, True, COLORS["WHITE"])
    surface.blit(title, (rect.x + 22, rect.y + 18))


def _draw_wrapped_text(
    surface: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    color: tuple[int, int, int],
    rect: pygame.Rect,
    *,
    max_lines: int,
    center: bool = False,
) -> None:
    lines = _wrap(font, text, rect.width)[:max_lines]
    y = rect.y
    for line in lines:
        rendered = font.render(line, True, color)
        x = rect.centerx - rendered.get_width() // 2 if center else rect.x
        surface.blit(rendered, (x, y))
        y += rendered.get_height() + 5


def _draw_multiline(
    surface: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    color: tuple[int, int, int],
    x: int,
    y: int,
) -> None:
    for line in text.splitlines():
        rendered = font.render(line, True, color)
        surface.blit(rendered, (x, y))
        y += rendered.get_height() + 5


def _wrap(font: pygame.font.Font, text: str, max_width: int) -> list[str]:
    lines: list[str] = []
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
    result = text
    while result and font.size(result + "…")[0] > max_width:
        result = result[:-1]
    return result + "…"


def _snapshot(state: LanLobbyDisplayState) -> Mapping[str, Any]:
    return _mapping(state.lobby_snapshot)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _local_member(
    snapshot: Mapping[str, Any], local_seat: int | None
) -> Mapping[str, Any]:
    if local_seat is None:
        return {}
    members = snapshot.get("members", ())
    if not isinstance(members, (tuple, list)):
        return {}
    for member in members:
        if isinstance(member, Mapping) and member.get("seat") == local_seat:
            return member
    return {}


def _safe_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    text = "".join(
        character
        for character in str(value)
        if character >= " " and character != "\x7f"
    ).strip()
    return text[:limit]


def _safe_int(
    value: Any,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(maximum, parsed))


def _validated_size(size: Sequence[int]) -> tuple[int, int]:
    if not isinstance(size, Sequence) or len(size) != 2:
        raise ValueError("size must be a width/height pair")
    width, height = size
    if (
        type(width) is not int
        or type(height) is not int
        or width < 960
        or height < 600
    ):
        raise ValueError("LAN lobby display requires at least 960x600")
    return width, height


def _control_height(reference_height: int) -> int:
    return _clamp(round(reference_height * 0.075), 42, 62)


def _font_size(
    screen_height: int,
    preferred: int,
    minimum: int,
    maximum: int,
) -> int:
    scaled = round(preferred * screen_height / DEFAULT_LAN_LOBBY_SIZE[1])
    return _clamp(scaled, minimum, maximum)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


@lru_cache(maxsize=8)
def _background_surface(size: tuple[int, int]) -> pygame.Surface:
    width, height = size
    surface = pygame.Surface(size)
    top = (42, 89, 130)
    bottom = (24, 55, 84)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(
            round(top[channel] + (bottom[channel] - top[channel]) * ratio)
            for channel in range(3)
        )
        pygame.draw.line(surface, color, (0, y), (width, y))
    for x, y, radius in (
        (width // 8, height // 5, height // 5),
        (width * 7 // 8, height * 4 // 5, height // 4),
    ):
        glow = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        pygame.draw.circle(
            glow,
            (130, 196, 225, 24),
            (radius, radius),
            radius,
        )
        surface.blit(glow, (x - radius, y - radius))
    return surface


__all__ = (
    "ACTION_BACK",
    "ACTION_CLOSE",
    "ACTION_COPY_ROOM_CODE",
    "ACTION_CREATE_ROOM",
    "ACTION_INPUT_ADDRESS",
    "ACTION_INPUT_NAME",
    "ACTION_INPUT_ROOM_CODE",
    "ACTION_JOIN_ROOM",
    "ACTION_LEAVE_ROOM",
    "ACTION_MODE_CREATE",
    "ACTION_MODE_JOIN",
    "ACTION_MODE_SPECTATE",
    "ACTION_RECONNECT",
    "ACTION_SPECTATOR_TOGGLE",
    "ACTION_START_MATCH",
    "ACTION_TOGGLE_READY",
    "DEFAULT_LAN_LOBBY_SIZE",
    "INPUT_ACTIONS",
    "LAN_LOBBY_MODES",
    "LanLobbyControl",
    "LanLobbyDisplayState",
    "LanLobbyHitTarget",
    "LanLobbyLayout",
    "LanLobbySeatLayout",
    "build_lan_lobby_layout",
    "draw_lan_lobby_display",
    "hit_test_lan_lobby_display",
)
