from dataclasses import FrozenInstanceError

import pygame
import pytest

from game.lan_lobby_display import (
    ACTION_BACK,
    ACTION_CREATE_ROOM,
    ACTION_INPUT_ADDRESS,
    ACTION_INPUT_NAME,
    ACTION_INPUT_ROOM_CODE,
    ACTION_JOIN_ROOM,
    ACTION_MODE_CREATE,
    ACTION_MODE_JOIN,
    ACTION_MODE_SPECTATE,
    ACTION_RECONNECT,
    ACTION_SPECTATOR_TOGGLE,
    ACTION_START_MATCH,
    ACTION_TOGGLE_READY,
    LanLobbyDisplayState,
    build_lan_lobby_layout,
    draw_lan_lobby_display,
    hit_test_lan_lobby_display,
)


@pytest.fixture(scope="module", autouse=True)
def pygame_runtime():
    pygame.init()
    pygame.display.set_mode((1, 1))
    yield
    pygame.quit()


def _snapshot(*, can_start=True, player_count=4, phase="waiting"):
    members = [
        {
            "display_name": "Host",
            "role": "host",
            "seat": 1,
            "connected": True,
            "ready": True,
            "reservation_seconds_remaining": None,
        },
        {
            "display_name": "Player 2",
            "role": "player",
            "seat": 2,
            "connected": True,
            "ready": True,
            "reservation_seconds_remaining": None,
        },
        {
            "display_name": "Player 3",
            "role": "player",
            "seat": 3,
            "connected": False,
            "ready": True,
            "reservation_seconds_remaining": 90,
        },
        {
            "display_name": "Viewer",
            "role": "spectator",
            "seat": None,
            "connected": True,
            "ready": False,
            "reservation_seconds_remaining": None,
        },
    ]
    return {
        "room_code": "ABC234",
        "revision": 8,
        "phase": phase,
        "settings": {
            "player_count": player_count,
            "victory_target": 10,
            "board_mode": "constrained",
            "board_seed": 86712347,
        },
        "full": False,
        "can_start": can_start,
        "members": members,
        "player_members": 3,
        "spectators": 1,
    }


def _states():
    return (
        LanLobbyDisplayState(mode="home"),
        LanLobbyDisplayState(
            mode="create",
            name="Host",
            address="0.0.0.0:47624",
            focused_field=ACTION_INPUT_NAME,
        ),
        LanLobbyDisplayState(
            mode="join",
            name="Player",
            address="192.168.1.12:47624",
            room_code="ABC234",
            spectator=True,
            focused_field=ACTION_INPUT_ROOM_CODE,
        ),
        LanLobbyDisplayState(
            mode="connected",
            room_code="ABC234",
            lobby_snapshot=_snapshot(),
            local_role="host",
            local_seat=1,
        ),
        LanLobbyDisplayState(
            mode="disconnected",
            connecting=True,
            error="接続が切れました。再試行しています。",
            local_role="player",
            local_seat=2,
        ),
    )


def test_display_state_is_frozen_and_validates_mode():
    state = LanLobbyDisplayState()
    with pytest.raises(FrozenInstanceError):
        state.mode = "join"
    with pytest.raises(ValueError):
        LanLobbyDisplayState(mode="internet")


@pytest.mark.parametrize("size", [(1920, 1280), (1280, 720), (1200, 800)])
@pytest.mark.parametrize("state", _states())
def test_responsive_layout_keeps_controls_visible_and_non_overlapping(size, state):
    layout = build_lan_lobby_layout(size, state)

    assert layout.screen_rect.contains(layout.shell_rect)
    assert layout.shell_rect.contains(layout.header_rect)
    assert layout.shell_rect.contains(layout.content_rect)
    assert layout.shell_rect.contains(layout.footer_rect)
    for control in layout.controls:
        assert layout.screen_rect.contains(control.rect), control.action
    for index, first in enumerate(layout.controls):
        for second in layout.controls[index + 1 :]:
            assert not first.rect.colliderect(second.rect), (
                first.action,
                second.action,
            )
    for seat in layout.seat_layouts:
        assert layout.primary_rect.contains(seat.rect)
    for index, first in enumerate(layout.seat_layouts):
        for second in layout.seat_layouts[index + 1 :]:
            assert not first.rect.colliderect(second.rect)


def test_home_join_and_spectator_actions_have_stable_ids():
    home = build_lan_lobby_layout((1280, 720), LanLobbyDisplayState())
    assert {control.action for control in home.controls} >= {
        ACTION_MODE_CREATE,
        ACTION_MODE_JOIN,
        ACTION_MODE_SPECTATE,
    }

    join = build_lan_lobby_layout(
        (1280, 720),
        LanLobbyDisplayState(
            mode="join",
            name="Player",
            address="127.0.0.1:47624",
            room_code="ABC234",
            spectator=True,
            focused_field=ACTION_INPUT_ROOM_CODE,
        ),
    )
    controls = join.control_by_action
    assert controls[ACTION_INPUT_NAME].kind == "input"
    assert controls[ACTION_INPUT_ADDRESS].kind == "input"
    assert controls[ACTION_INPUT_ROOM_CODE].selected is True
    assert controls[ACTION_SPECTATOR_TOGGLE].selected is True
    assert controls[ACTION_JOIN_ROOM].enabled is True


def test_connecting_disables_form_submission_and_input():
    state = LanLobbyDisplayState(
        mode="create",
        name="Host",
        address="0.0.0.0:47624",
        connecting=True,
    )
    controls = build_lan_lobby_layout((1280, 720), state).control_by_action

    assert controls[ACTION_CREATE_ROOM].enabled is False
    assert controls[ACTION_INPUT_NAME].enabled is False
    assert controls[ACTION_INPUT_ADDRESS].enabled is False


def test_connected_host_controls_and_seats_follow_public_snapshot():
    state = LanLobbyDisplayState(
        mode="connected",
        lobby_snapshot=_snapshot(can_start=True, player_count=4),
        local_role="host",
        local_seat=1,
    )
    layout = build_lan_lobby_layout((1280, 720), state)
    controls = layout.control_by_action

    assert [seat.seat for seat in layout.seat_layouts] == [1, 2, 3, 4]
    assert controls[ACTION_TOGGLE_READY].selected is True
    assert controls[ACTION_START_MATCH].enabled is True

    spectator = build_lan_lobby_layout(
        (1280, 720),
        LanLobbyDisplayState(
            mode="connected",
            lobby_snapshot=_snapshot(),
            local_role="spectator",
            local_seat=None,
        ),
    ).control_by_action
    assert ACTION_TOGGLE_READY not in spectator
    assert ACTION_START_MATCH not in spectator


def test_started_room_replaces_ready_controls_with_resume_for_every_role():
    for role, seat in (("host", 1), ("player", 2), ("spectator", None)):
        controls = build_lan_lobby_layout(
            (1280, 720),
            LanLobbyDisplayState(
                mode="connected",
                lobby_snapshot=_snapshot(phase="started"),
                local_role=role,
                local_seat=seat,
            ),
        ).control_by_action

        assert ACTION_TOGGLE_READY not in controls
        assert controls[ACTION_START_MATCH].label == "対局画面へ戻る"
        assert controls[ACTION_START_MATCH].enabled is True


def test_hit_test_returns_enabled_action_and_ignores_disabled_button():
    disconnected = build_lan_lobby_layout(
        (1280, 720),
        LanLobbyDisplayState(mode="disconnected", connecting=False),
    )
    reconnect = disconnected.control_by_action[ACTION_RECONNECT]
    target = hit_test_lan_lobby_display(disconnected, reconnect.rect.center)
    assert target.action == ACTION_RECONNECT
    assert target.kind == "button"

    connecting = build_lan_lobby_layout(
        (1280, 720),
        LanLobbyDisplayState(mode="disconnected", connecting=True),
    )
    disabled = connecting.control_by_action[ACTION_RECONNECT]
    assert hit_test_lan_lobby_display(connecting, disabled.rect.center) is None
    assert ACTION_BACK in connecting.control_by_action


@pytest.mark.parametrize("state", _states())
@pytest.mark.parametrize("size", [(1920, 1280), (1280, 720), (1200, 800)])
def test_render_smoke_draws_each_mode_at_supported_sizes(state, size):
    surface = pygame.Surface(size)
    surface.fill((0, 0, 0))

    layout = draw_lan_lobby_display(surface, state)

    assert layout.screen_rect.size == size
    assert surface.get_at((0, 0))[:3] != (0, 0, 0)
    assert surface.get_at(layout.shell_rect.center)[:3] != (0, 0, 0)
