from dataclasses import dataclass

import pygame
import pytest

from game.constants import COLORS
from game.result_display import (
    NEW_BOARD_ACTION,
    REPLAY_SELECTED_ACTION,
    RESTART_SAME_BOARD_ACTION,
    RESULT_ACTIONS,
    ImportantResultEvent,
    MatchResultSummary,
    VictoryPointSnapshot,
    build_result_display_layout,
    draw_result_display,
    hit_test_result_display,
    normalise_match_result,
    selected_replay_frame,
)


@pytest.fixture(autouse=True)
def pygame_display():
    pygame.init()
    pygame.display.set_mode((1, 1))
    yield
    pygame.quit()


def result_mapping(event_count=10):
    player_names = ("Player1", "CPU拡大", "CPU交渉", "CPU妨害")
    players = [
        {
            "name": "CPU交渉",
            "vp": 7,
            "color": (244, 166, 42),
            "roads": 8,
            "settlements": 4,
            "cities": 2,
            "trades": 12,
            "luck": 1.5,
            "is_ai": True,
        },
        {
            "name": "Player1",
            "victory_points": 10,
            "color": (242, 76, 67),
            "roads_built": 11,
            "settlements_built": 5,
            "cities_built": 4,
            "trades_completed": 7,
            "luck_index": 4.25,
        },
        {
            "name": "CPU妨害",
            "points": 5,
            "color": (80, 210, 225),
            "roads": 7,
            "settlements": 4,
            "cities": 1,
            "trades": 4,
            "luck": None,
            "is_ai": True,
        },
        {
            "name": "CPU拡大",
            "vp": 8,
            "color": (53, 101, 233),
            "roads": 12,
            "settlements": 5,
            "cities": 3,
            "trades": 5,
            "luck": -2.75,
            "is_ai": True,
        },
    ]
    progression = []
    final_scores = {
        "Player1": 10,
        "CPU拡大": 8,
        "CPU交渉": 7,
        "CPU妨害": 5,
    }
    for turn in (0, 12, 27, 43, 61):
        progression.append(
            {
                "turn": turn,
                "points": {
                    name: min(score, 2 + round((score - 2) * turn / 61))
                    for name, score in final_scores.items()
                },
            }
        )
    events = [
        {
            "title": f"重要イベント {index + 1}",
            "detail": "建設または交易についての公開情報",
            "turn": index * 6 + 1,
            "player_name": player_names[index % len(player_names)],
            "replay_frame": index * 3,
        }
        for index in range(event_count)
    ]
    return {
        "winner_name": "Player1",
        "victory_point_target": 10,
        "turns": 61,
        "subtitle": "制約付き盤面 ・ seed 86712347",
        "players": players,
        "score_history": list(reversed(progression)),
        "events": events,
    }


@dataclass
class PlayerPayload:
    name: str
    victory_points: int
    color: tuple
    roads_built: int
    settlements_built: int
    cities_built: int
    trades_completed: int
    luck_index: float


@dataclass
class ResultPayload:
    winner_name: str
    players: tuple
    victory_target: int
    turn_count: int
    vp_progression: tuple
    important_events: tuple


def test_normalise_match_result_accepts_mappings_and_orders_standings():
    result = normalise_match_result(result_mapping())

    assert isinstance(result, MatchResultSummary)
    assert [player.name for player in result.players] == [
        "Player1",
        "CPU拡大",
        "CPU交渉",
        "CPU妨害",
    ]
    assert [player.rank for player in result.players] == [1, 2, 3, 4]
    assert result.players[0].roads_built == 11
    assert result.players[1].luck_index == -2.75
    assert result.victory_target == 10
    assert [snapshot.turn for snapshot in result.vp_progression] == [0, 12, 27, 43, 61]
    assert result.important_events[3].replay_frame == 9


def test_normalise_match_result_accepts_plain_dataclasses_and_tied_ranks():
    payload = ResultPayload(
        winner_name="Alice",
        players=(
            PlayerPayload("Alice", 10, (255, 0, 0), 10, 5, 4, 7, 2.0),
            PlayerPayload("Bob", 7, (0, 0, 255), 8, 4, 3, 3, -1.0),
            PlayerPayload("Carol", 7, (255, 165, 0), 9, 4, 2, 5, 0.0),
        ),
        victory_target=10,
        turn_count=50,
        vp_progression=(
            VictoryPointSnapshot(0, {"Alice": 2, "Bob": 2, "Carol": 2}),
            VictoryPointSnapshot(50, {"Alice": 10, "Bob": 7, "Carol": 7}),
        ),
        important_events=(ImportantResultEvent("勝利", replay_frame=24),),
    )

    result = normalise_match_result(payload)

    assert [player.rank for player in result.players] == [1, 2, 2]
    assert result.vp_progression[-1].points["Carol"] == 7
    assert result.important_events[0].title == "勝利"


def test_normalise_match_result_accepts_versioned_match_result_payload_directly():
    payload = {
        "format": "catan-match-result",
        "version": 1,
        "winner": {"seat": 1, "name": "Alice"},
        "victory_target": 6,
        "standings": [
            {
                "rank": 1,
                "seat": 1,
                "name": "Alice",
                "victory_points": 6,
                "color": (220, 70, 60),
                "roads": 2,
                "settlements": 1,
                "cities": 1,
                "builds": {"roads": 9, "settlements": 4, "cities": 2},
                "trades": {"bank": 7, "domestic": 4},
                "luck_index": 1.25,
            },
            {
                "rank": 2,
                "seat": 2,
                "name": "Bob",
                "victory_points": 4,
                "color": (60, 100, 225),
                "roads": 3,
                "settlements": 2,
                "cities": 0,
                "builds": {"roads": None, "settlements": None, "cities": None},
                "trades": {"bank": 0, "domestic": 1},
                "luck_index": -0.5,
            },
        ],
        "vp_progression": [
            {
                "replay_frame_index": 0,
                "scores": [
                    {"seat": 1, "victory_points": 2},
                    {"seat": 2, "victory_points": 2},
                ],
            },
            {
                "replay_frame_index": 4,
                "scores": [
                    {"seat": 1, "victory_points": 6},
                    {"seat": 2, "victory_points": 4},
                ],
            },
        ],
        "important_events": [
            {
                "title": "Aliceの勝利",
                "detail": "6 VPに到達",
                "replay_frame_index": 4,
            }
        ],
        "replay": {"available": True, "frame_count": 5},
    }

    result = normalise_match_result(payload)

    assert result.winner_name == "Alice"
    assert result.timeline_unit == "フレーム"
    assert result.turn_count == 4
    assert result.players[0].roads_built == 9
    assert result.players[0].trades_completed == 11
    assert result.players[0].bank_trades == 7
    assert result.players[1].roads_built == 3
    assert result.vp_progression[-1].points == {"Alice": 6, "Bob": 4}
    assert result.important_events[0].replay_frame == 4
    assert selected_replay_frame(payload, 0) == 4


def test_layout_fits_1200_by_800_and_keeps_click_targets_disjoint():
    layout = build_result_display_layout((1200, 800), 4, 14, 10)

    for rect in (
        layout.header_rect,
        layout.standings_rect,
        layout.chart_rect,
        layout.events_rect,
        layout.controls_rect,
    ):
        assert layout.screen_rect.contains(rect)
    assert layout.standings_rect.bottom < layout.chart_rect.top
    assert layout.standings_rect.right < layout.events_rect.left
    assert layout.header_rect.bottom < layout.standings_rect.top
    assert layout.events_rect.bottom < layout.controls_rect.top
    assert layout.chart_rect.contains(layout.chart_plot_rect)

    assert len(layout.player_rects) == 4
    assert all(layout.standings_rect.contains(rect) for rect in layout.player_rects)
    assert all(
        not first.colliderect(second)
        for index, first in enumerate(layout.player_rects)
        for second in layout.player_rects[index + 1 :]
    )

    visible_indices = [hitbox.event_index for hitbox in layout.event_hitboxes]
    assert 10 in visible_indices
    assert visible_indices == list(range(visible_indices[0], visible_indices[-1] + 1))
    assert all(layout.events_rect.contains(hitbox.rect) for hitbox in layout.event_hitboxes)

    action_rects = [layout.action_rects[action] for action in RESULT_ACTIONS]
    assert all(layout.controls_rect.contains(rect) for rect in action_rects)
    assert all(
        not first.colliderect(second)
        for index, first in enumerate(action_rects)
        for second in action_rects[index + 1 :]
    )


def test_layout_and_hit_test_expose_actions_and_original_event_indices():
    layout = build_result_display_layout((1200, 800), 4, 18, 15)

    for action in RESULT_ACTIONS:
        target = hit_test_result_display(layout, layout.action_rects[action].center)
        assert target is not None
        assert target.kind == "action"
        assert target.action == action

    event_hitbox = next(
        hitbox for hitbox in layout.event_hitboxes if hitbox.event_index == 15
    )
    target = hit_test_result_display(layout, event_hitbox.rect.center)
    assert target is not None
    assert target.kind == "event"
    assert target.event_index == 15
    assert hit_test_result_display(layout, (0, 0)) is None


def test_disabled_replay_button_is_not_returned_by_hit_test():
    layout = build_result_display_layout(
        (1200, 800),
        4,
        1,
        0,
        replay_enabled=False,
    )

    assert REPLAY_SELECTED_ACTION not in layout.enabled_actions
    assert hit_test_result_display(
        layout,
        layout.action_rects[REPLAY_SELECTED_ACTION].center,
    ) is None
    assert hit_test_result_display(
        layout,
        layout.action_rects[RESTART_SAME_BOARD_ACTION].center,
    ).action == RESTART_SAME_BOARD_ACTION
    assert hit_test_result_display(
        layout,
        layout.action_rects[NEW_BOARD_ACTION].center,
    ).action == NEW_BOARD_ACTION


def test_draw_result_display_is_deterministic_and_renders_all_sections_offscreen():
    summary = result_mapping(11)
    summary["players"][1]["name"] = (
        "Player1という非常に長いプレイヤー名でも枠からはみ出さない"
    )
    summary["winner_name"] = summary["players"][1]["name"]
    summary["events"][7]["title"] = (
        "とても長い重要イベント名が入力されても"
        "隣のターン表示と重ならないよう省略される"
    )
    first_surface = pygame.Surface((1200, 800), pygame.SRCALPHA)
    second_surface = pygame.Surface((1200, 800), pygame.SRCALPHA)

    first_layout = draw_result_display(first_surface, summary, 7)
    second_layout = draw_result_display(second_surface, summary, 7)

    assert first_layout == second_layout
    assert first_layout.selected_event_index == 7
    assert REPLAY_SELECTED_ACTION in first_layout.enabled_actions
    assert pygame.image.tostring(first_surface, "RGBA") == pygame.image.tostring(
        second_surface,
        "RGBA",
    )
    assert first_surface.get_at((0, 0)).a == 255
    assert first_surface.get_at(first_layout.header_rect.center) != pygame.Color(0, 0, 0, 0)
    assert first_surface.get_at(first_layout.chart_plot_rect.center).a == 255
    selected_row = next(
        hitbox.rect
        for hitbox in first_layout.event_hitboxes
        if hitbox.event_index == 7
    )
    assert first_surface.get_at((selected_row.x, selected_row.centery))[:3] == COLORS[
        "WARNING"
    ]


def test_empty_events_render_with_disabled_replay_and_keep_other_controls():
    summary = result_mapping(0)
    surface = pygame.Surface((1200, 800), pygame.SRCALPHA)

    layout = draw_result_display(surface, summary)

    assert layout.selected_event_index is None
    assert layout.event_hitboxes == ()
    assert REPLAY_SELECTED_ACTION not in layout.enabled_actions
    assert set(layout.enabled_actions) == {
        RESTART_SAME_BOARD_ACTION,
        NEW_BOARD_ACTION,
    }
    disabled_center = layout.action_rects[REPLAY_SELECTED_ACTION].center
    assert hit_test_result_display(layout, disabled_center) is None


def test_selected_replay_frame_clamps_selection_and_handles_missing_frames():
    summary = result_mapping(3)

    assert selected_replay_frame(summary, -20) == 0
    assert selected_replay_frame(summary, 200) == 6
    assert selected_replay_frame(summary, None) is None
    summary["events"][1].pop("replay_frame")
    assert selected_replay_frame(summary, 1) is None


def test_replay_selection_requires_available_in_range_archive_metadata():
    summary = result_mapping(1)
    summary["replay"] = {"available": False, "frame_count": 20}
    assert selected_replay_frame(summary, 0) is None

    summary["replay"] = {"available": True, "frame_count": 0}
    assert selected_replay_frame(summary, 0) is None

    summary["replay"] = {"available": True, "frame_count": 1}
    assert selected_replay_frame(summary, 0) == 0


def test_event_sequence_is_timeline_metadata_not_a_replay_frame():
    summary = result_mapping(1)
    summary["events"] = [{"title": "得点更新", "sequence": 7}]

    event = normalise_match_result(summary).important_events[0]

    assert event.turn == 7
    assert event.replay_frame is None


def test_invalid_layout_and_summary_inputs_fail_clearly():
    with pytest.raises(ValueError, match="at least"):
        build_result_display_layout((900, 600), 4, 3)
    with pytest.raises(ValueError, match="four"):
        build_result_display_layout((1200, 800), 5, 3)
    with pytest.raises(ValueError, match="at least one player"):
        normalise_match_result({"players": []})
    with pytest.raises(ValueError, match="sequence"):
        normalise_match_result({"players": {"name": "invalid"}})
