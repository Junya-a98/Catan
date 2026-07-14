"""Responsive Pygame presentation for custom maps and house rules.

The display module owns no editable game state.  It receives immutable domain
objects, produces deterministic rectangles, and returns typed hit targets.
Keeping layout, drawing, and input translation separate makes the same settings
documents suitable for a future Web client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import math
from typing import Sequence

import pygame

from game.assets import get_font
from game.constants import COLORS
from game.custom_map import CustomMapSpec
from game.development_cards import DEVELOPMENT_CARD_LABELS, DevelopmentCardType
from game.house_rules import HouseRules
from game.resources import ResourceType


DEFAULT_PRE_GAME_SETTINGS_SIZE = (1920, 1280)
PRE_GAME_SETTINGS_TABS = frozenset({"map", "rules"})
MAP_EDIT_LAYERS = frozenset({"terrain", "numbers", "harbors"})

ACTION_TAB_MAP = "tab_map"
ACTION_TAB_RULES = "tab_rules"
ACTION_EDIT_TERRAIN = "edit_terrain"
ACTION_EDIT_NUMBERS = "edit_numbers"
ACTION_EDIT_HARBORS = "edit_harbors"
ACTION_SHUFFLE_TERRAIN = "shuffle_terrain"
ACTION_SHUFFLE_NUMBERS = "shuffle_numbers"
ACTION_SHUFFLE_HARBORS = "shuffle_harbors"
ACTION_TOGGLE_BANK_3_TO_1 = "toggle_bank_3_to_1"
ACTION_TOGGLE_SKIP_DISCARD = "toggle_skip_discard"
ACTION_RESET = "reset"
ACTION_CANCEL = "cancel"
ACTION_APPLY = "apply"


RESOURCE_LABELS = {
    ResourceType.WOOD: "木",
    ResourceType.SHEEP: "羊",
    ResourceType.WHEAT: "麦",
    ResourceType.BRICK: "土",
    ResourceType.ORE: "鉄",
    ResourceType.DESERT: "砂漠",
}

RESOURCE_PREVIEW_COLORS = {
    ResourceType.WOOD: (72, 128, 74),
    ResourceType.SHEEP: (151, 190, 98),
    ResourceType.WHEAT: (218, 177, 73),
    ResourceType.BRICK: (174, 92, 65),
    ResourceType.ORE: (135, 143, 151),
    ResourceType.DESERT: (206, 174, 119),
}


def development_toggle_action(card_type: DevelopmentCardType) -> str:
    """Return the stable action used by both Pygame and future clients."""

    if not isinstance(card_type, DevelopmentCardType):
        raise TypeError("card_type must be a DevelopmentCardType")
    return f"toggle_dev_{card_type.value}"


@dataclass(frozen=True)
class PreGameSettingsDisplayState:
    """Immutable data needed to render one detailed-settings frame."""

    map_spec: CustomMapSpec
    house_rules: HouseRules = field(default_factory=HouseRules.standard)
    tab: str = "map"
    edit_layer: str = "terrain"
    selected_tile: tuple[int, int] | None = None
    selected_harbor: int | None = None
    can_apply: bool = True
    error: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.map_spec, CustomMapSpec):
            raise TypeError("map_spec must be a CustomMapSpec")
        if not isinstance(self.house_rules, HouseRules):
            raise TypeError("house_rules must be a HouseRules")
        if self.tab not in PRE_GAME_SETTINGS_TABS:
            raise ValueError("tab must be 'map' or 'rules'")
        if self.edit_layer not in MAP_EDIT_LAYERS:
            raise ValueError("edit_layer must be terrain, numbers, or harbors")
        if self.selected_tile is not None:
            if (
                type(self.selected_tile) is not tuple
                or len(self.selected_tile) != 2
                or any(type(value) is not int for value in self.selected_tile)
            ):
                raise ValueError("selected_tile must be an axial (q, r) pair")
            available = {(tile.q, tile.r) for tile in self.map_spec.tiles}
            if self.selected_tile not in available:
                raise ValueError("selected_tile is not part of map_spec")
        if self.selected_harbor is not None and (
            type(self.selected_harbor) is not int
            or not 0 <= self.selected_harbor < len(self.map_spec.harbors)
        ):
            raise ValueError("selected_harbor is outside map_spec")
        if type(self.can_apply) is not bool:
            raise TypeError("can_apply must be a bool")
        if not isinstance(self.error, str):
            raise TypeError("error must be a string")


@dataclass(frozen=True)
class PreGameSettingsControl:
    action: str
    label: str
    rect: pygame.Rect
    enabled: bool = True
    selected: bool = False


@dataclass(frozen=True)
class PreGameTileTarget:
    axial: tuple[int, int]
    center: tuple[int, int]
    polygon: tuple[tuple[int, int], ...]
    rect: pygame.Rect
    enabled: bool
    selected: bool = False


@dataclass(frozen=True)
class PreGameHarborTarget:
    harbor_index: int
    center: tuple[int, int]
    rect: pygame.Rect
    enabled: bool
    selected: bool = False


@dataclass(frozen=True)
class PreGameSettingsLayout:
    screen_rect: pygame.Rect
    shell_rect: pygame.Rect
    header_rect: pygame.Rect
    content_rect: pygame.Rect
    footer_rect: pygame.Rect
    preview_rect: pygame.Rect
    editor_rect: pygame.Rect
    error_rect: pygame.Rect
    controls: tuple[PreGameSettingsControl, ...]
    tile_targets: tuple[PreGameTileTarget, ...]
    harbor_targets: tuple[PreGameHarborTarget, ...]
    board_center: tuple[int, int]
    tile_radius: int

    @property
    def control_by_action(self) -> dict[str, PreGameSettingsControl]:
        return {control.action: control for control in self.controls}

    @property
    def tile_by_axial(self) -> dict[tuple[int, int], PreGameTileTarget]:
        return {target.axial: target for target in self.tile_targets}

    @property
    def harbor_by_index(self) -> dict[int, PreGameHarborTarget]:
        return {target.harbor_index: target for target in self.harbor_targets}


@dataclass(frozen=True)
class PreGameSettingsHitTarget:
    kind: str
    action: str | None = None
    axial: tuple[int, int] | None = None
    harbor_index: int | None = None


def build_pre_game_settings_layout(
    size: Sequence[int], state: PreGameSettingsDisplayState
) -> PreGameSettingsLayout:
    """Build deterministic rectangles for wide and compact game windows."""

    width, height = _validated_size(size)
    screen_rect = pygame.Rect(0, 0, width, height)
    margin_x = _clamp(round(width * 0.03), 18, 58)
    margin_y = _clamp(round(height * 0.025), 14, 38)
    shell_rect = screen_rect.inflate(-margin_x * 2, -margin_y * 2)
    gap = _clamp(round(min(width, height) * 0.014), 10, 22)
    header_height = _clamp(round(height * 0.105), 72, 124)
    footer_height = _clamp(round(height * 0.09), 62, 96)

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

    preview_width = round((content_rect.width - gap) * 0.60)
    preview_rect = pygame.Rect(
        content_rect.x,
        content_rect.y,
        preview_width,
        content_rect.height,
    )
    editor_rect = pygame.Rect(
        preview_rect.right + gap,
        content_rect.y,
        content_rect.right - preview_rect.right - gap,
        content_rect.height,
    )

    controls: list[PreGameSettingsControl] = []
    control_height = _clamp(round(height * 0.061), 40, 58)
    control_gap = _clamp(round(gap * 0.72), 8, 14)
    header_control_height = min(control_height, header_rect.height - 24)
    header_inner = header_rect.inflate(-18 * 2, -12 * 2)
    tab_gap = control_gap
    tab_total_width = min(
        round(header_rect.width * 0.39),
        _clamp(round(width * 0.32), 320, 600),
    )
    tab_width = (tab_total_width - tab_gap) // 2
    tab_x = header_inner.right - tab_total_width
    tab_y = header_rect.centery - header_control_height // 2
    controls.extend(
        (
            PreGameSettingsControl(
                ACTION_TAB_MAP,
                "カスタムマップ",
                pygame.Rect(tab_x, tab_y, tab_width, header_control_height),
                selected=state.tab == "map",
            ),
            PreGameSettingsControl(
                ACTION_TAB_RULES,
                "ハウスルール",
                pygame.Rect(
                    tab_x + tab_width + tab_gap,
                    tab_y,
                    tab_total_width - tab_width - tab_gap,
                    header_control_height,
                ),
                selected=state.tab == "rules",
            ),
        )
    )

    editor_inner = editor_rect.inflate(-16 * 2, -16 * 2)
    editor_row_y = editor_inner.y + _clamp(round(height * 0.055), 44, 68)
    if state.tab == "map":
        controls.extend(
            _map_controls(
                editor_inner,
                editor_row_y,
                control_height,
                control_gap,
                state,
            )
        )
    else:
        controls.extend(
            _rules_controls(
                editor_inner,
                editor_row_y,
                control_height,
                control_gap,
                state,
            )
        )

    error_height = _clamp(round(height * 0.072), 46, 72)
    error_rect = pygame.Rect(
        editor_inner.x,
        editor_inner.bottom - error_height,
        editor_inner.width,
        error_height,
    )

    footer_inner = footer_rect.inflate(-18 * 2, -10 * 2)
    footer_control_height = min(control_height, footer_inner.height)
    footer_y = footer_rect.centery - footer_control_height // 2
    footer_gap = gap
    footer_width = (footer_inner.width - footer_gap * 2) // 3
    footer_specs = (
        (ACTION_RESET, "公式設定へ戻す", True, False),
        (ACTION_CANCEL, "キャンセル  Esc", True, False),
        (ACTION_APPLY, "この設定を適用  Enter", state.can_apply, True),
    )
    for index, (action, label, enabled, highlighted) in enumerate(footer_specs):
        x = footer_inner.x + index * (footer_width + footer_gap)
        width_for_control = (
            footer_inner.right - x if index == len(footer_specs) - 1 else footer_width
        )
        controls.append(
            PreGameSettingsControl(
                action,
                label,
                pygame.Rect(x, footer_y, width_for_control, footer_control_height),
                enabled=enabled,
                selected=highlighted and state.can_apply,
            )
        )

    tile_targets, harbor_targets, board_center, tile_radius = _map_targets(
        preview_rect,
        state,
    )
    return PreGameSettingsLayout(
        screen_rect=screen_rect,
        shell_rect=shell_rect,
        header_rect=header_rect,
        content_rect=content_rect,
        footer_rect=footer_rect,
        preview_rect=preview_rect,
        editor_rect=editor_rect,
        error_rect=error_rect,
        controls=tuple(controls),
        tile_targets=tile_targets,
        harbor_targets=harbor_targets,
        board_center=board_center,
        tile_radius=tile_radius,
    )


def hit_test_pre_game_settings(
    layout: PreGameSettingsLayout, pos: Sequence[int]
) -> PreGameSettingsHitTarget | None:
    """Return a typed enabled target for one pointer position."""

    if not isinstance(layout, PreGameSettingsLayout):
        raise TypeError("layout must be a PreGameSettingsLayout")
    if not isinstance(pos, Sequence) or len(pos) != 2:
        return None
    try:
        point = (int(pos[0]), int(pos[1]))
    except (TypeError, ValueError, OverflowError):
        return None

    for control in reversed(layout.controls):
        if control.enabled and control.rect.collidepoint(point):
            return PreGameSettingsHitTarget("action", action=control.action)
    for target in reversed(layout.harbor_targets):
        if target.enabled and target.rect.collidepoint(point):
            return PreGameSettingsHitTarget(
                "harbor",
                harbor_index=target.harbor_index,
            )
    for target in reversed(layout.tile_targets):
        if target.enabled and target.rect.collidepoint(point):
            return PreGameSettingsHitTarget("tile", axial=target.axial)
    return None


def draw_pre_game_settings_display(
    surface: pygame.Surface,
    state: PreGameSettingsDisplayState,
) -> PreGameSettingsLayout:
    """Draw the detailed settings overlay and return its clickable layout."""

    if not isinstance(surface, pygame.Surface):
        raise TypeError("surface must be a pygame.Surface")
    layout = build_pre_game_settings_layout(surface.get_size(), state)
    surface.blit(_background_surface(surface.get_size()), (0, 0))
    _draw_shell(surface, layout)
    _draw_header(surface, layout, state)
    _draw_preview(surface, layout, state)
    _draw_editor(surface, layout, state)
    for control in layout.controls:
        _draw_control(surface, control, surface.get_height())
    _draw_error(surface, layout.error_rect, state, surface.get_height())
    return layout


# The short name is convenient at the CatanGame integration boundary.
draw = draw_pre_game_settings_display
hit_test = hit_test_pre_game_settings


def _map_controls(
    rect: pygame.Rect,
    row_y: int,
    height: int,
    gap: int,
    state: PreGameSettingsDisplayState,
) -> tuple[PreGameSettingsControl, ...]:
    controls: list[PreGameSettingsControl] = []
    layer_specs = (
        (ACTION_EDIT_TERRAIN, "地形", "terrain"),
        (ACTION_EDIT_NUMBERS, "数字", "numbers"),
        (ACTION_EDIT_HARBORS, "港", "harbors"),
    )
    cell_width = (rect.width - gap * 2) // 3
    for index, (action, label, layer) in enumerate(layer_specs):
        x = rect.x + index * (cell_width + gap)
        width = rect.right - x if index == 2 else cell_width
        controls.append(
            PreGameSettingsControl(
                action,
                label,
                pygame.Rect(x, row_y, width, height),
                selected=state.edit_layer == layer,
            )
        )

    shuffle_y = row_y + height + gap
    shuffle_specs = (
        (ACTION_SHUFFLE_TERRAIN, "地形シャッフル", "terrain"),
        (ACTION_SHUFFLE_NUMBERS, "数字シャッフル", "numbers"),
        (ACTION_SHUFFLE_HARBORS, "港シャッフル", "harbors"),
    )
    for index, (action, label, layer) in enumerate(shuffle_specs):
        x = rect.x + index * (cell_width + gap)
        width = rect.right - x if index == 2 else cell_width
        controls.append(
            PreGameSettingsControl(
                action,
                label,
                pygame.Rect(x, shuffle_y, width, height),
                selected=state.edit_layer == layer,
            )
        )
    return tuple(controls)


def _rules_controls(
    rect: pygame.Rect,
    row_y: int,
    height: int,
    gap: int,
    state: PreGameSettingsDisplayState,
) -> tuple[PreGameSettingsControl, ...]:
    rules = state.house_rules
    specs: list[tuple[str, str, bool]] = [
        (
            ACTION_TOGGLE_BANK_3_TO_1,
            "銀行交易を 3:1 にする",
            rules.bank_trade_3_to_1,
        ),
        (
            ACTION_TOGGLE_SKIP_DISCARD,
            "7でも手札を捨てない",
            rules.skip_discard_on_seven,
        ),
    ]
    for card_type in DevelopmentCardType:
        enabled = card_type not in rules.disabled_development_cards
        specs.append(
            (
                development_toggle_action(card_type),
                f"{DEVELOPMENT_CARD_LABELS[card_type]}カードを使用",
                enabled,
            )
        )

    column_gap = gap
    column_width = (rect.width - column_gap) // 2
    controls: list[PreGameSettingsControl] = []
    for index, (action, label, selected) in enumerate(specs):
        column = index % 2
        row = index // 2
        x = rect.x + column * (column_width + column_gap)
        width = rect.right - x if column else column_width
        y = row_y + row * (height + gap)
        controls.append(
            PreGameSettingsControl(
                action,
                label,
                pygame.Rect(x, y, width, height),
                selected=selected,
            )
        )
    return tuple(controls)


def _map_targets(
    preview_rect: pygame.Rect,
    state: PreGameSettingsDisplayState,
) -> tuple[
    tuple[PreGameTileTarget, ...],
    tuple[PreGameHarborTarget, ...],
    tuple[int, int],
    int,
]:
    board_area = preview_rect.inflate(-24 * 2, -18 * 2)
    title_allowance = _clamp(round(preview_rect.height * 0.075), 34, 58)
    board_area.y += title_allowance
    board_area.height -= title_allowance
    tile_radius = _clamp(
        int(min(board_area.width / 11.5, board_area.height / 10.2)),
        22,
        82,
    )
    board_center = (board_area.centerx, board_area.centery + tile_radius // 12)
    tile_enabled = state.tab == "map" and state.edit_layer != "harbors"
    harbor_enabled = state.tab == "map" and state.edit_layer == "harbors"
    target_radius = max(10, round(tile_radius * 0.40))

    tile_targets: list[PreGameTileTarget] = []
    for tile in state.map_spec.tiles:
        center = _axial_to_point(board_center, tile_radius, tile.q, tile.r)
        polygon = _hex_polygon(center, tile_radius)
        hit_rect = pygame.Rect(0, 0, target_radius * 2, target_radius * 2)
        hit_rect.center = center
        axial = (tile.q, tile.r)
        tile_targets.append(
            PreGameTileTarget(
                axial=axial,
                center=center,
                polygon=polygon,
                rect=hit_rect,
                enabled=tile_enabled,
                selected=state.selected_tile == axial,
            )
        )

    badge_width = _clamp(round(tile_radius * 1.25), 46, 86)
    badge_height = _clamp(round(tile_radius * 0.72), 28, 48)
    ring_x = tile_radius * 4.82
    ring_y = tile_radius * 4.58
    harbor_targets: list[PreGameHarborTarget] = []
    harbor_count = len(state.map_spec.harbors)
    for index in range(harbor_count):
        angle = -math.pi + index * math.tau / max(1, harbor_count)
        center = (
            round(board_center[0] + math.cos(angle) * ring_x),
            round(board_center[1] + math.sin(angle) * ring_y),
        )
        rect = pygame.Rect(0, 0, badge_width, badge_height)
        rect.center = center
        harbor_targets.append(
            PreGameHarborTarget(
                harbor_index=index,
                center=center,
                rect=rect,
                enabled=harbor_enabled,
                selected=state.selected_harbor == index,
            )
        )
    return tuple(tile_targets), tuple(harbor_targets), board_center, tile_radius


def _draw_shell(surface: pygame.Surface, layout: PreGameSettingsLayout) -> None:
    shadow = pygame.Surface(layout.shell_rect.size, pygame.SRCALPHA)
    pygame.draw.rect(shadow, (4, 8, 14, 125), shadow.get_rect(), border_radius=24)
    surface.blit(shadow, layout.shell_rect.move(5, 7).topleft)
    pygame.draw.rect(surface, COLORS["PANEL_BG"], layout.shell_rect, border_radius=22)
    pygame.draw.rect(
        surface,
        COLORS["PANEL_BORDER"],
        layout.shell_rect,
        2,
        border_radius=22,
    )
    for rect in (
        layout.header_rect,
        layout.preview_rect,
        layout.editor_rect,
        layout.footer_rect,
    ):
        pygame.draw.rect(surface, COLORS["CARD_BG"], rect, border_radius=18)
        pygame.draw.rect(surface, COLORS["CARD_BORDER"], rect, 2, border_radius=18)


def _draw_header(
    surface: pygame.Surface,
    layout: PreGameSettingsLayout,
    state: PreGameSettingsDisplayState,
) -> None:
    height = surface.get_height()
    title_font = get_font(_font_size(height, 32, 22, 38), bold=True)
    subtitle_font = get_font(_font_size(height, 17, 13, 21))
    title = title_font.render("ゲーム詳細設定", True, COLORS["WHITE"])
    surface.blit(title, (layout.header_rect.x + 22, layout.header_rect.y + 13))
    subtitle_text = (
        "標準19タイルの配置を編集"
        if state.tab == "map"
        else "公式ルールとの差分を開始前に確認"
    )
    subtitle = subtitle_font.render(subtitle_text, True, COLORS["TEXT_MUTED"])
    subtitle_y = layout.header_rect.bottom - subtitle.get_height() - 12
    surface.blit(subtitle, (layout.header_rect.x + 24, subtitle_y))


def _draw_preview(
    surface: pygame.Surface,
    layout: PreGameSettingsLayout,
    state: PreGameSettingsDisplayState,
) -> None:
    height = surface.get_height()
    title_font = get_font(_font_size(height, 22, 16, 27), bold=True)
    title = title_font.render(state.map_spec.name, True, COLORS["WHITE"])
    surface.blit(title, (layout.preview_rect.x + 20, layout.preview_rect.y + 14))

    tile_by_axial = {(tile.q, tile.r): tile for tile in state.map_spec.tiles}
    token_font = get_font(_font_size(height, 19, 13, 23), bold=True)
    resource_font = get_font(_font_size(height, 13, 10, 16))
    for target in layout.tile_targets:
        tile = tile_by_axial[target.axial]
        fill = RESOURCE_PREVIEW_COLORS[tile.resource]
        pygame.draw.polygon(surface, fill, target.polygon)
        border = COLORS["BUTTON_HIGHLIGHT_BORDER"] if target.selected else (25, 34, 41)
        pygame.draw.polygon(
            surface,
            border,
            target.polygon,
            4 if target.selected else 2,
        )
        resource_label = resource_font.render(
            RESOURCE_LABELS[tile.resource],
            True,
            (31, 36, 37),
        )
        label_y = target.center[1] - resource_label.get_height() - 4
        surface.blit(
            resource_label,
            resource_label.get_rect(centerx=target.center[0], y=label_y),
        )
        if tile.number is not None:
            token_radius = _clamp(round(layout.tile_radius * 0.27), 10, 20)
            token_center = (
                target.center[0],
                target.center[1] + token_radius // 2 + 4,
            )
            pygame.draw.circle(surface, (244, 240, 222), token_center, token_radius)
            token_color = (174, 54, 48) if tile.number in (6, 8) else (32, 35, 37)
            number = token_font.render(str(tile.number), True, token_color)
            surface.blit(number, number.get_rect(center=token_center))

    harbor_font = get_font(_font_size(height, 13, 10, 16), bold=True)
    for target in layout.harbor_targets:
        harbor_type = state.map_spec.harbors[target.harbor_index]
        pygame.draw.line(
            surface,
            (154, 119, 76),
            target.center,
            _point_towards(
                target.center, layout.board_center, max(18, layout.tile_radius)
            ),
            max(2, layout.tile_radius // 12),
        )
        fill = (84, 112, 137) if not target.selected else (93, 135, 115)
        border = (
            COLORS["BUTTON_HIGHLIGHT_BORDER"]
            if target.selected
            else COLORS["CARD_BORDER"]
        )
        pygame.draw.rect(surface, fill, target.rect, border_radius=9)
        pygame.draw.rect(
            surface,
            border,
            target.rect,
            3 if target.selected else 2,
            border_radius=9,
        )
        label = "3:1" if harbor_type is None else f"{RESOURCE_LABELS[harbor_type]} 2:1"
        rendered = harbor_font.render(label, True, COLORS["WHITE"])
        surface.blit(rendered, rendered.get_rect(center=target.rect.center))


def _draw_editor(
    surface: pygame.Surface,
    layout: PreGameSettingsLayout,
    state: PreGameSettingsDisplayState,
) -> None:
    height = surface.get_height()
    title_font = get_font(_font_size(height, 23, 17, 28), bold=True)
    body_font = get_font(_font_size(height, 15, 12, 19))
    title_text = "盤面編集" if state.tab == "map" else "ハウスルール"
    title = title_font.render(title_text, True, COLORS["WHITE"])
    surface.blit(title, (layout.editor_rect.x + 18, layout.editor_rect.y + 14))

    if state.tab == "map":
        selected_text = "盤面上のタイルを選択してください。"
        if state.edit_layer == "harbors":
            selected_text = "外周の港バッジを選択してください。"
        elif state.selected_tile is not None:
            selected_text = f"選択中: axial {state.selected_tile}"
        if state.selected_harbor is not None and state.edit_layer == "harbors":
            selected_text = f"選択中: 港スロット {state.selected_harbor + 1}"
        hint_y = layout.editor_rect.y + _clamp(round(height * 0.18), 136, 220)
        hint_rect = pygame.Rect(
            layout.editor_rect.x + 18,
            hint_y,
            layout.editor_rect.width - 36,
            max(44, layout.error_rect.y - hint_y - 14),
        )
        _draw_wrapped_text(
            surface, body_font, selected_text, COLORS["TEXT_MUTED"], hint_rect, 3
        )
    else:
        hint_y = layout.editor_rect.y + _clamp(round(height * 0.25), 176, 310)
        hint_rect = pygame.Rect(
            layout.editor_rect.x + 18,
            hint_y,
            layout.editor_rect.width - 36,
            max(44, layout.error_rect.y - hint_y - 14),
        )
        _draw_wrapped_text(
            surface,
            body_font,
            "点灯している項目が有効です。カードを無効にすると山札から除外されます。",
            COLORS["TEXT_MUTED"],
            hint_rect,
            4,
        )


def _draw_control(
    surface: pygame.Surface,
    control: PreGameSettingsControl,
    screen_height: int,
) -> None:
    if not control.enabled:
        fill = COLORS["BUTTON_DISABLED"]
        border = (80, 91, 102)
        text_color = (150, 160, 170)
    elif control.selected:
        fill = COLORS["BUTTON_ACTIVE"]
        border = COLORS["BUTTON_HIGHLIGHT_BORDER"]
        text_color = COLORS["WHITE"]
    else:
        fill = COLORS["BUTTON"]
        border = COLORS["PANEL_BORDER"]
        text_color = COLORS["BUTTON_TEXT"]
    pygame.draw.rect(surface, fill, control.rect, border_radius=11)
    pygame.draw.rect(surface, border, control.rect, 2, border_radius=11)
    font = get_font(_font_size(screen_height, 17, 12, 21))
    label = _truncate(font, control.label, control.rect.width - 16)
    rendered = font.render(label, True, text_color)
    surface.blit(rendered, rendered.get_rect(center=control.rect.center))


def _draw_error(
    surface: pygame.Surface,
    rect: pygame.Rect,
    state: PreGameSettingsDisplayState,
    screen_height: int,
) -> None:
    message = state.error.strip()
    if not message and state.can_apply:
        message = "設定を適用できます。"
    elif not message:
        message = "設定を確認してください。"
    danger = bool(state.error) or not state.can_apply
    fill = (63, 32, 38) if danger else (35, 63, 52)
    border = COLORS["DANGER"] if danger else COLORS["SUCCESS"]
    pygame.draw.rect(surface, fill, rect, border_radius=10)
    pygame.draw.rect(surface, border, rect, 1, border_radius=10)
    font = get_font(_font_size(screen_height, 14, 11, 17))
    _draw_wrapped_text(
        surface,
        font,
        message,
        COLORS["WHITE"],
        rect.inflate(-14 * 2, -8 * 2),
        2,
    )


def _draw_wrapped_text(
    surface: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    color: tuple[int, int, int],
    rect: pygame.Rect,
    max_lines: int,
) -> None:
    y = rect.y
    for line in _wrap(font, text, rect.width)[:max_lines]:
        rendered = font.render(line, True, color)
        surface.blit(rendered, (rect.x, y))
        y += rendered.get_height() + 4


def _axial_to_point(
    center: tuple[int, int], radius: int, q: int, r: int
) -> tuple[int, int]:
    return (
        round(center[0] + math.sqrt(3) * radius * (q + r / 2)),
        round(center[1] + 1.5 * radius * r),
    )


def _hex_polygon(center: tuple[int, int], radius: int) -> tuple[tuple[int, int], ...]:
    return tuple(
        (
            round(center[0] + radius * math.cos(math.radians(60 * index - 30))),
            round(center[1] + radius * math.sin(math.radians(60 * index - 30))),
        )
        for index in range(6)
    )


def _point_towards(
    start: tuple[int, int], end: tuple[int, int], distance: int
) -> tuple[int, int]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = max(1.0, math.hypot(dx, dy))
    return (
        round(start[0] + dx / length * distance),
        round(start[1] + dy / length * distance),
    )


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


def _safe_text(value: object, limit: int) -> str:
    return "".join(
        character
        for character in str(value)
        if character >= " " and character != "\x7f"
    )[:limit]


def _validated_size(size: Sequence[int]) -> tuple[int, int]:
    if not isinstance(size, Sequence) or len(size) != 2:
        raise ValueError("size must be a width/height pair")
    width, height = size
    if type(width) is not int or type(height) is not int or width < 960 or height < 600:
        raise ValueError("pre-game settings display requires at least 960x600")
    return width, height


def _font_size(
    screen_height: int,
    preferred: int,
    minimum: int,
    maximum: int,
) -> int:
    scaled = round(preferred * screen_height / DEFAULT_PRE_GAME_SETTINGS_SIZE[1])
    return _clamp(scaled, minimum, maximum)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


@lru_cache(maxsize=8)
def _background_surface(size: tuple[int, int]) -> pygame.Surface:
    width, height = size
    surface = pygame.Surface(size)
    top = (47, 99, 143)
    bottom = (23, 53, 80)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(
            round(top[channel] + (bottom[channel] - top[channel]) * ratio)
            for channel in range(3)
        )
        pygame.draw.line(surface, color, (0, y), (width, y))
    return surface


__all__ = (
    "ACTION_APPLY",
    "ACTION_CANCEL",
    "ACTION_EDIT_HARBORS",
    "ACTION_EDIT_NUMBERS",
    "ACTION_EDIT_TERRAIN",
    "ACTION_RESET",
    "ACTION_SHUFFLE_HARBORS",
    "ACTION_SHUFFLE_NUMBERS",
    "ACTION_SHUFFLE_TERRAIN",
    "ACTION_TAB_MAP",
    "ACTION_TAB_RULES",
    "ACTION_TOGGLE_BANK_3_TO_1",
    "ACTION_TOGGLE_SKIP_DISCARD",
    "DEFAULT_PRE_GAME_SETTINGS_SIZE",
    "MAP_EDIT_LAYERS",
    "PRE_GAME_SETTINGS_TABS",
    "PreGameHarborTarget",
    "PreGameSettingsControl",
    "PreGameSettingsDisplayState",
    "PreGameSettingsHitTarget",
    "PreGameSettingsLayout",
    "PreGameTileTarget",
    "build_pre_game_settings_layout",
    "development_toggle_action",
    "draw",
    "draw_pre_game_settings_display",
    "hit_test",
    "hit_test_pre_game_settings",
)
