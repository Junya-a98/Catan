from dataclasses import FrozenInstanceError

import pygame
import pytest

from game.custom_map import CustomMapSpec
from game.development_cards import DevelopmentCardType
from game.game_board import GameBoard
from game.house_rules import HouseRules
from game.pre_game_settings_display import (
    ACTION_APPLY,
    ACTION_CANCEL,
    ACTION_EDIT_HARBORS,
    ACTION_EDIT_NUMBERS,
    ACTION_EDIT_TERRAIN,
    ACTION_RESET,
    ACTION_SHUFFLE_HARBORS,
    ACTION_SHUFFLE_NUMBERS,
    ACTION_SHUFFLE_TERRAIN,
    ACTION_TAB_MAP,
    ACTION_TAB_RULES,
    ACTION_TOGGLE_BANK_3_TO_1,
    ACTION_TOGGLE_SKIP_DISCARD,
    PreGameSettingsDisplayState,
    build_pre_game_settings_layout,
    development_toggle_action,
    draw_pre_game_settings_display,
    hit_test_pre_game_settings,
)


@pytest.fixture(scope="module", autouse=True)
def pygame_runtime():
    pygame.init()
    pygame.display.set_mode((1, 1))
    yield
    pygame.quit()


@pytest.fixture(scope="module")
def map_spec():
    return CustomMapSpec.from_board(
        GameBoard(mode="fully_random", seed=86712347),
        name="テスト島",
    )


def _state(map_spec, **changes):
    values = {
        "map_spec": map_spec,
        "house_rules": HouseRules.standard(),
        "tab": "map",
        "edit_layer": "terrain",
        "selected_tile": map_spec.tiles[0].axial,
        "selected_harbor": None,
        "can_apply": True,
        "error": "",
    }
    values.update(changes)
    return PreGameSettingsDisplayState(**values)


def _assert_pairwise_non_overlapping(items):
    for index, first in enumerate(items):
        for second in items[index + 1 :]:
            assert not first.rect.colliderect(second.rect), (first, second)


def test_display_state_is_frozen_and_validates_modes(map_spec):
    state = _state(map_spec)
    with pytest.raises(FrozenInstanceError):
        state.tab = "rules"
    with pytest.raises(ValueError, match="tab"):
        _state(map_spec, tab="basic")
    with pytest.raises(ValueError, match="edit_layer"):
        _state(map_spec, edit_layer="ports")
    with pytest.raises(ValueError, match="selected_tile"):
        _state(map_spec, selected_tile=(9, 9))
    with pytest.raises(ValueError, match="selected_harbor"):
        _state(map_spec, selected_harbor=9)


@pytest.mark.parametrize("size", [(1920, 1280), (1280, 720), (1200, 800)])
@pytest.mark.parametrize(
    "state_factory",
    (
        lambda spec: _state(spec, tab="map", edit_layer="terrain"),
        lambda spec: _state(spec, tab="map", edit_layer="numbers"),
        lambda spec: _state(
            spec,
            tab="map",
            edit_layer="harbors",
            selected_tile=None,
            selected_harbor=4,
        ),
        lambda spec: _state(spec, tab="rules", can_apply=False, error="設定エラー"),
    ),
)
def test_responsive_layout_keeps_every_target_visible_and_separate(
    size,
    state_factory,
    map_spec,
):
    layout = build_pre_game_settings_layout(size, state_factory(map_spec))

    assert layout.screen_rect.contains(layout.shell_rect)
    assert layout.shell_rect.contains(layout.header_rect)
    assert layout.shell_rect.contains(layout.content_rect)
    assert layout.shell_rect.contains(layout.footer_rect)
    assert layout.content_rect.contains(layout.preview_rect)
    assert layout.content_rect.contains(layout.editor_rect)
    assert not layout.preview_rect.colliderect(layout.editor_rect)
    assert layout.editor_rect.contains(layout.error_rect)

    for control in layout.controls:
        assert layout.screen_rect.contains(control.rect), control.action
        assert control.rect.height >= 40
    _assert_pairwise_non_overlapping(layout.controls)

    assert len(layout.tile_targets) == 19
    assert len(layout.harbor_targets) == 9
    for target in (*layout.tile_targets, *layout.harbor_targets):
        assert layout.preview_rect.contains(target.rect), target
    for target in layout.tile_targets:
        assert all(layout.preview_rect.collidepoint(point) for point in target.polygon)
    _assert_pairwise_non_overlapping(layout.tile_targets)
    _assert_pairwise_non_overlapping(layout.harbor_targets)


def test_map_tab_exposes_stable_actions_and_selected_layer(map_spec):
    layout = build_pre_game_settings_layout(
        (1280, 720),
        _state(map_spec, edit_layer="numbers"),
    )
    controls = layout.control_by_action

    assert set(controls) == {
        ACTION_TAB_MAP,
        ACTION_TAB_RULES,
        ACTION_EDIT_TERRAIN,
        ACTION_EDIT_NUMBERS,
        ACTION_EDIT_HARBORS,
        ACTION_SHUFFLE_TERRAIN,
        ACTION_SHUFFLE_NUMBERS,
        ACTION_SHUFFLE_HARBORS,
        ACTION_RESET,
        ACTION_CANCEL,
        ACTION_APPLY,
    }
    assert controls[ACTION_TAB_MAP].selected is True
    assert controls[ACTION_EDIT_NUMBERS].selected is True
    assert controls[ACTION_EDIT_TERRAIN].selected is False
    assert controls[ACTION_APPLY].enabled is True


def test_rules_tab_exposes_each_house_rule_with_stable_enum_actions(map_spec):
    house_rules = HouseRules(
        bank_trade_3_to_1=True,
        skip_discard_on_seven=True,
        disabled_development_cards=frozenset(
            {DevelopmentCardType.MONOPOLY, DevelopmentCardType.VICTORY_POINT}
        ),
    )
    layout = build_pre_game_settings_layout(
        (1280, 720),
        _state(map_spec, tab="rules", house_rules=house_rules),
    )
    controls = layout.control_by_action

    assert controls[ACTION_TAB_RULES].selected is True
    assert controls[ACTION_TOGGLE_BANK_3_TO_1].selected is True
    assert controls[ACTION_TOGGLE_SKIP_DISCARD].selected is True
    for card_type in DevelopmentCardType:
        action = development_toggle_action(card_type)
        assert action == f"toggle_dev_{card_type.value}"
        assert action in controls
        assert controls[action].selected is (
            card_type not in house_rules.disabled_development_cards
        )


def test_hit_test_returns_typed_action_tile_and_harbor_targets(map_spec):
    tile_state = _state(map_spec, edit_layer="terrain")
    tile_layout = build_pre_game_settings_layout((1280, 720), tile_state)

    edit_control = tile_layout.control_by_action[ACTION_EDIT_NUMBERS]
    action_target = hit_test_pre_game_settings(
        tile_layout,
        edit_control.rect.center,
    )
    assert action_target.kind == "action"
    assert action_target.action == ACTION_EDIT_NUMBERS

    tile = tile_layout.tile_targets[0]
    tile_target = hit_test_pre_game_settings(tile_layout, tile.rect.center)
    assert tile_target.kind == "tile"
    assert tile_target.axial == tile.axial
    assert tile_target.harbor_index is None

    inactive_harbor = tile_layout.harbor_targets[0]
    assert hit_test_pre_game_settings(tile_layout, inactive_harbor.rect.center) is None

    harbor_layout = build_pre_game_settings_layout(
        (1280, 720),
        _state(
            map_spec,
            edit_layer="harbors",
            selected_tile=None,
            selected_harbor=0,
        ),
    )
    harbor = harbor_layout.harbor_targets[0]
    harbor_target = hit_test_pre_game_settings(harbor_layout, harbor.rect.center)
    assert harbor_target.kind == "harbor"
    assert harbor_target.harbor_index == 0
    assert harbor_target.axial is None

    inactive_tile = harbor_layout.tile_targets[0]
    assert hit_test_pre_game_settings(harbor_layout, inactive_tile.rect.center) is None


def test_hit_test_ignores_disabled_apply(map_spec):
    layout = build_pre_game_settings_layout(
        (1280, 720),
        _state(map_spec, can_apply=False, error="6と8が隣接しています。"),
    )
    apply_control = layout.control_by_action[ACTION_APPLY]

    assert apply_control.enabled is False
    assert hit_test_pre_game_settings(layout, apply_control.rect.center) is None


@pytest.mark.parametrize("size", [(1920, 1280), (1280, 720), (1200, 800)])
@pytest.mark.parametrize("tab", ["map", "rules"])
def test_render_smoke_draws_each_tab_at_supported_sizes(size, tab, map_spec):
    surface = pygame.Surface(size)
    surface.fill((0, 0, 0))
    state = _state(map_spec, tab=tab)

    layout = draw_pre_game_settings_display(surface, state)

    assert layout.screen_rect.size == size
    assert surface.get_at((0, 0))[:3] != (0, 0, 0)
    assert surface.get_at(layout.shell_rect.center)[:3] != (0, 0, 0)
    assert surface.get_at(layout.preview_rect.center)[:3] != (0, 0, 0)


def test_invalid_sizes_and_pointer_values_are_rejected_safely(map_spec):
    state = _state(map_spec)
    with pytest.raises(ValueError, match="960x600"):
        build_pre_game_settings_layout((959, 600), state)
    with pytest.raises(ValueError, match="960x600"):
        build_pre_game_settings_layout((960, 599), state)

    layout = build_pre_game_settings_layout((1200, 800), state)
    assert hit_test_pre_game_settings(layout, ()) is None
    assert hit_test_pre_game_settings(layout, ("not", "coordinates")) is None
