from dataclasses import replace

import pygame
import pytest

from game.game import CatanGame
from game.lan_lobby_flow import LanLobbyFlowDisplayState
from game.network_protocol import build_state_snapshot
from game.network_view import parse_state_snapshot


@pytest.fixture
def game():
    pygame.init()
    pygame.display.set_mode((1, 1))
    instance = CatanGame(board_seed=4242, ai_player_count=0)
    yield instance
    if instance.lan_lobby_flow is not None:
        instance.close_lan_lobby(permanent=True)
    instance.audio.stop()
    pygame.quit()


def _connected_lobby_snapshot(*, phase="started"):
    return {
        "room_code": "ABC234",
        "revision": 4,
        "phase": phase,
        "settings": {
            "player_count": 2,
            "victory_target": 10,
            "board_mode": "constrained",
            "board_seed": 4242,
        },
        "can_start": False,
        "members": [
            {
                "display_name": "Player1",
                "role": "host",
                "seat": 1,
                "connected": True,
                "ready": True,
            },
            {
                "display_name": "Player2",
                "role": "player",
                "seat": 2,
                "connected": True,
                "ready": True,
            },
        ],
        "spectators": 0,
    }


class StubMatchFlow:
    def __init__(self, view, options, *, mode="connected"):
        self.is_open = True
        self.mode = mode
        self.latest_game_view = view
        self.latest_command_options = tuple(options)
        self.command_pending = False
        self.is_connected = mode == "connected"
        self.sent = []
        self.actions = []
        self.update_calls = 0
        self.close_calls = 0

    @property
    def match_active(self):
        return self.latest_game_view is not None

    @property
    def display_state(self):
        error = "" if self.mode == "connected" else "接続が切れました。"
        return LanLobbyFlowDisplayState(
            mode=self.mode,
            name="Player1",
            address="192.168.1.20:47624",
            room_code="ABC234",
            error=error,
            lobby_snapshot=_connected_lobby_snapshot(),
            local_role="host",
            local_seat=1,
        )

    def send_game_command(self, command, args=None):
        if self.command_pending:
            return False
        self.sent.append((command, dict(args or {})))
        self.command_pending = True
        return True

    def handle_action(self, action):
        self.actions.append(action)
        return True

    def update(self):
        self.update_calls += 1

    def close(self):
        self.close_calls += 1
        self.is_open = False

    def leave(self, *, close_overlay=False):
        self.is_open = not close_overlay


def _match_view():
    authority = CatanGame(board_seed=4242, ai_player_count=0, headless=True)
    authority.configure_players(2, reset_logs=False)
    authority.phase = "main"
    authority.initial_dice_phase = False
    authority.dice_rolled = True
    return parse_state_snapshot(
        build_state_snapshot(authority, viewer_player_index=0, revision=7)
    )


def test_initial_screen_exposes_exact_room_settings_and_locks_lan_button(game):
    assert game.get_lan_room_settings() == {
        "player_count": 2,
        "victory_target": 10,
        "board_mode": "constrained",
        "board_seed": 4242,
    }
    buttons = {button.action: button for button in game.build_buttons()}
    assert buttons["lan_lobby_open"].enabled is True
    assert buttons["replay_open"].rect.right == buttons["lan_lobby_open"].rect.right + 12 + buttons["replay_open"].rect.width

    game.initial_dice_histories[game.players[0].name] = [8]
    buttons = {button.action: button for button in game.build_buttons()}
    assert buttons["lan_lobby_open"].enabled is False
    assert game.open_lan_lobby() is False


def test_lobby_routes_mouse_and_text_input_without_leaking_global_keys(game):
    assert game.open_lan_lobby() is True
    game.render()
    create = game.lan_lobby_layout.control_by_action["lobby_mode_create"]
    game.handle_lan_event(
        pygame.event.Event(
            pygame.MOUSEBUTTONDOWN,
            button=1,
            pos=create.rect.center,
        )
    )
    assert game.lan_lobby_flow.mode == "create"

    game.lan_lobby_flow.set_input("lobby_input_name", "")
    game.render()
    name = game.lan_lobby_layout.control_by_action["lobby_input_name"]
    game.handle_lan_event(
        pygame.event.Event(
            pygame.MOUSEBUTTONDOWN,
            button=1,
            pos=name.rect.center,
        )
    )
    game.handle_lan_event(pygame.event.Event(pygame.TEXTINPUT, text="海賊A"))
    assert game.lan_lobby_flow.display_state.name == "海賊A"

    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_h))
    game.handle_events()
    assert game.show_help_panel is False


def test_lan_update_pauses_every_local_game_runtime_step(game, monkeypatch):
    assert game.open_lan_lobby() is True
    local_calls = []
    monkeypatch.setattr(game, "update_ai", lambda: local_calls.append("ai"))
    monkeypatch.setattr(
        game,
        "update_dice_animation",
        lambda: local_calls.append("dice"),
    )
    monkeypatch.setattr(
        game,
        "flush_replay_capture",
        lambda: local_calls.append("replay"),
    )

    game.update()

    assert local_calls == []


def test_match_clicks_send_only_exact_advertised_commands(game):
    view = _match_view()
    road_id = view.board.edges[0].target_id
    options = (
        {"command": "build", "args": {"piece": "road", "target": road_id}},
        {"command": "end_turn", "args": {}},
    )
    flow = StubMatchFlow(view, options)
    game.lan_lobby_flow = flow
    game.lan_match_visible = True
    game.lan_match_seen = True
    game.render()

    road_control = next(
        control
        for control in game.lan_match_layout.controls
        if control.build_piece == "road"
    )
    game.handle_lan_event(
        pygame.event.Event(
            pygame.MOUSEBUTTONDOWN,
            button=1,
            pos=road_control.rect.center,
        )
    )
    assert game.lan_selected_build_piece == "road"
    assert flow.sent == []

    game.render()
    target = game.lan_match_layout.board_targets[0]
    game.handle_lan_event(
        pygame.event.Event(
            pygame.MOUSEBUTTONDOWN,
            button=1,
            pos=target.center,
        )
    )
    assert flow.sent == [
        ("build", {"piece": "road", "target": road_id})
    ]

    game.render()
    assert game.lan_match_layout.controls == ()
    assert game.lan_match_layout.board_targets == ()


def test_escape_opens_connected_lobby_and_resume_keeps_session(game):
    flow = StubMatchFlow(_match_view(), ())
    game.lan_lobby_flow = flow
    game.lan_match_visible = True
    game.lan_match_seen = True

    game.handle_lan_event(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)
    )
    assert game.lan_match_visible is False
    assert flow.is_connected is True
    assert flow.actions == []

    game.render()
    resume = game.lan_lobby_layout.control_by_action["lobby_start_match"]
    assert resume.label == "対局画面へ戻る"
    game.handle_lan_event(
        pygame.event.Event(
            pygame.MOUSEBUTTONDOWN,
            button=1,
            pos=resume.rect.center,
        )
    )
    assert game.lan_match_visible is True
    assert flow.actions == []


def test_disconnect_uses_reconnect_lobby_instead_of_stale_match_controls(game):
    flow = StubMatchFlow(_match_view(), ({"command": "end_turn", "args": {}},))
    flow.mode = "disconnected"
    flow.is_connected = False
    game.lan_lobby_flow = flow
    game.lan_match_visible = True
    game.lan_match_seen = True

    game.render()

    assert game.lan_match_layout is None
    assert game.lan_lobby_layout is not None
    assert "lobby_reconnect" in game.lan_lobby_layout.control_by_action


def test_new_revision_clears_a_stale_build_piece_selection(game):
    view = _match_view()
    flow = StubMatchFlow(
        view,
        (
            {
                "command": "build",
                "args": {
                    "piece": "road",
                    "target": view.board.edges[0].target_id,
                },
            },
        ),
    )
    game.lan_lobby_flow = flow
    game.lan_match_visible = True
    game.lan_match_seen = True
    game.lan_selected_build_piece = "road"
    game.update()
    assert game.lan_selected_build_piece == "road"

    flow.latest_game_view = replace(view, revision=view.revision + 1)
    flow.latest_command_options = ({"command": "end_turn", "args": {}},)
    game.update()
    assert game.lan_selected_build_piece is None


def test_run_closes_lan_resources_even_when_loop_is_already_stopped(
    game,
    monkeypatch,
):
    flow = StubMatchFlow(None, ())
    game.lan_lobby_flow = flow
    game.running = False
    quit_calls = []
    monkeypatch.setattr(pygame, "quit", lambda: quit_calls.append(True))

    game.run()

    assert flow.close_calls == 1
    assert game.lan_lobby_flow is None
    assert quit_calls == [True]
