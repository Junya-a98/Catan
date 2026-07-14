from dataclasses import dataclass
import json

import pytest

from game import self_play_report as report_module
from game.self_play_report import (
    ReportError,
    build_report_data,
    main,
    render_html_dashboard,
    render_terminal_summary,
    write_report,
)


def _batch():
    return {
        "master_seed": 99,
        "board_mode": "constrained",
        "victory_target": 10,
        "player_count": 2,
        "matches": [
            {
                "match_seed": 100,
                "board_seed": 10,
                "completed": True,
                "termination_reason": "victory",
                "winner_seat": 1,
                "starting_player_seat": 1,
                "turn_order": [1, 2],
                "turns": 50,
                "dice_counts": {6: 5, 7: 6, 8: 5},
                "players": [
                    {
                        "seat": 1,
                        "name": "CPU1",
                        "personality": "builder",
                        "vp": 10,
                        "roads": 8,
                        "settlements": 3,
                        "cities": 2,
                        "action_counts": {
                            "domestic_trade_offers": 2,
                            "domestic_trades_completed": 1,
                            "bank_trades": 3,
                            "robber_moves": 2,
                            "knights_used": 1,
                        },
                    },
                    {
                        "seat": 2,
                        "name": "CPU2",
                        "personality": "trader",
                        "vp": 7,
                        "roads": 5,
                        "settlements": 4,
                        "cities": 1,
                        "action_counts": {
                            "domestic_trade_offers": 4,
                            "domestic_trades_completed": 2,
                            "bank_trades": 1,
                            "robber_moves": 0,
                            "knights_used": 0,
                        },
                    },
                ],
            },
            {
                "match_seed": 101,
                "board_seed": 11,
                "completed": True,
                "termination_reason": "victory",
                "winner_seat": 2,
                "starting_player_seat": 1,
                "turn_order": [1, 2],
                "turns": 70,
                "dice_counts": [1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1],
                "players": [
                    {
                        "seat": 1,
                        "name": "CPU1",
                        "personality": "expansion",
                        "vp": 8,
                        "roads": 10,
                        "settlements": 2,
                        "cities": 3,
                        "action_counts": {
                            "domestic_trade_offers": 4,
                            "domestic_trades_completed": 1,
                            "bank_trades": 1,
                            "robber_moves": 1,
                            "knights_used": 0,
                        },
                    },
                    {
                        "seat": 2,
                        "name": "CPU2",
                        "personality": "trader",
                        "vp": 10,
                        "roads": 6,
                        "settlements": 3,
                        "cities": 2,
                        "action_counts": {
                            "domestic_trade_offers": 2,
                            "domestic_trades_completed": 1,
                            "bank_trades": 3,
                            "robber_moves": 3,
                            "knights_used": 2,
                        },
                    },
                ],
            },
            {
                "match_seed": 102,
                "board_seed": 12,
                "completed": False,
                "termination_reason": "step_limit",
                "starting_player_seat": 2,
                "turn_order": [2, 1],
                "turns": 100,
                "dice_counts": {},
                "players": [
                    {"seat": 1, "name": "CPU1", "personality": "builder", "vp": 6},
                    {"seat": 2, "name": "CPU2", "personality": "trader", "vp": 8},
                ],
            },
        ],
    }


def test_build_report_calculates_completion_seat_personality_and_turn_stats():
    report = build_report_data(_batch(), generated_at="2026-01-02T03:04:05+00:00")
    summary = report["summary"]

    assert summary["matches"] == 3
    assert summary["completed"] == 2
    assert summary["completion_rate"] == pytest.approx(2 / 3)
    assert summary["average_turns"] == 60
    assert summary["median_turns"] == 60
    assert summary["total_rolls"] == 52

    seats = {row["seat"]: row for row in summary["seat_statistics"]}
    assert seats[1]["wins"] == 1
    assert seats[1]["win_rate"] == pytest.approx(0.5)
    assert seats[1]["average_vp"] == pytest.approx(9)
    assert seats[2]["average_vp"] == pytest.approx(8.5)

    personalities = {row["personality"]: row for row in summary["personality_statistics"]}
    assert personalities["expansion"]["wins"] == 1
    assert personalities["trader"]["wins"] == 1
    assert personalities["expansion"]["win_rate"] == pytest.approx(0.5)
    assert personalities["expansion"]["average_domestic_trade_offers"] == 3
    assert personalities["expansion"]["average_roads"] == 9
    assert personalities["expansion"]["average_settlements"] == 2.5
    assert personalities["expansion"]["average_cities"] == 2.5
    assert personalities["trader"]["average_domestic_trades_completed"] == 1.5
    assert personalities["trader"]["average_knights_used"] == 1

    personality_seats = {
        (row["personality"], row["seat"]): row
        for row in summary["personality_seat_statistics"]
    }
    assert personality_seats[("expansion", 1)]["wins"] == 1
    assert personality_seats[("trader", 2)]["average_vp"] == pytest.approx(8.5)

    starts = {row["seat"]: row for row in summary["starting_seat_statistics"]}
    assert starts[1]["appearances"] == 2
    assert starts[1]["wins"] == 1
    positions = {row["position"]: row for row in summary["turn_position_statistics"]}
    assert positions[0]["wins"] == 1
    assert positions[1]["wins"] == 1


def test_html_is_offline_responsive_and_escapes_untrusted_result_text():
    batch = _batch()
    batch["matches"][0]["players"][0]["name"] = '<script src="https://bad.example/x"></script>'
    report = build_report_data(batch, generated_at="fixed")
    rendered = render_html_dashboard(report)

    assert "Content-Security-Policy" in rendered
    assert "@media(max-width:480px)" in rendered
    assert "<script" not in rendered
    assert 'src="https://bad.example' not in rendered
    assert "&lt;script src=&quot;https://bad.example/x&quot;&gt;" in rendered
    assert "席順別" in rendered
    assert "AI性格 × 席" in rendered
    assert "銀行交易" in rendered
    assert "ダイス分布" in rendered
    assert "100戦未満" in rendered


@pytest.mark.parametrize(
    "lineup",
    [
        "balanced / builder / trader / blocker",
        ["balanced", "builder", "trader", "blocker"],
    ],
)
def test_personality_aliases_metadata_and_control_characters_are_normalised(lineup):
    batch = _batch()
    batch["personality_lineup"] = lineup
    batch["matches"][2]["players"][1]["personality"] = "\x1b[31m\u202ecustom\n"

    report = build_report_data(batch, generated_at="fixed")
    rendered = render_html_dashboard(report)
    terminal = render_terminal_summary(report)
    personalities = {
        row["personality"] for row in report["summary"]["personality_statistics"]
    }

    assert "builder" not in personalities
    assert "expansion" in personalities
    assert report["matches"][2]["players"][1]["personality"] == "[31mcustom"
    assert "標準 / 拡大重視 / 交渉重視 / 妨害重視" in rendered
    assert "balanced / builder" not in rendered
    assert "\x1b" not in terminal
    assert "\n" not in report["matches"][2]["players"][1]["personality"]


def test_write_report_creates_round_trip_json_and_static_html(tmp_path):
    paths = write_report(
        _batch(),
        tmp_path / "reports",
        basename="batch-99",
        generated_at="2026-01-02T03:04:05+00:00",
    )

    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
    assert payload["format"] == "catan-self-play-report"
    assert payload["metadata"]["master_seed"] == 99
    assert payload["matches"][0]["dice_counts"]["7"] == 6
    assert paths.html_path.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert paths.json_path.stat().st_mode & 0o077 == 0
    assert paths.html_path.stat().st_mode & 0o077 == 0


@dataclass
class _Player:
    seat: int
    name: str
    vp: int


@dataclass
class _Match:
    winner_seat: int
    turn_count: int
    dice_counts: dict
    players: list


@dataclass
class _Batch:
    matches: list


def test_dataclass_and_accepted_aliases_are_normalised():
    report = build_report_data(
        _Batch(matches=[_Match(1, 12, {7: 1}, [_Player(1, "CPU", 10)])]),
        generated_at="fixed",
    )

    match = report["matches"][0]
    assert match["completed"] is True
    assert match["turns"] == 12
    assert match["winner_name"] == "CPU"
    assert match["dice_counts"]["7"] == 1


def test_winner_only_legacy_input_has_consistent_seat_appearances():
    report = build_report_data(
        {"matches": [{"completed": True, "winner_seat": 1}]},
        generated_at="fixed",
    )

    seat = report["summary"]["seat_statistics"][0]
    assert seat["appearances"] == 1
    assert seat["completed_appearances"] == 1
    assert seat["wins"] == 1
    assert seat["win_rate"] == 1.0


@pytest.mark.parametrize(
    "bad_value",
    [
        {"matches": "not-a-list"},
        {"matches": [{"completed": "yes"}]},
        {"matches": [{"dice_counts": {13: 1}}]},
        {"matches": [{"players": [{"seat": True}]}]},
        {
            "matches": [
                {"players": [{"seat": 1, "initial_high_probability_access": "yes"}]}
            ]
        },
        {
            "matches": [
                {
                    "completed": True,
                    "winner_seat": 2,
                    "players": [{"seat": 1, "name": "CPU1"}],
                }
            ]
        },
        {
            "matches": [
                {
                    "players": [
                        {"seat": 1, "action_counts": {"bank_trades": -1}}
                    ]
                }
            ]
        },
        {
            "matches": [
                {
                    "players": [{"seat": 1}, {"seat": 2}],
                    "turn_order": [1],
                    "starting_player_seat": 1,
                }
            ]
        },
        {
            "matches": [
                {
                    "players": [{"seat": 1}, {"seat": 2}],
                    "turn_order": [1, 2],
                    "starting_player_seat": 2,
                }
            ]
        },
    ],
)
def test_invalid_results_fail_with_report_error(bad_value):
    with pytest.raises(ReportError):
        build_report_data(bad_value)


def test_html_omits_optional_fairness_rows_when_source_has_no_turn_order():
    report = build_report_data(
        {"matches": [{"completed": False, "players": []}]}, generated_at="fixed"
    )
    rendered = render_html_dashboard(report)

    assert report["summary"]["starting_seat_statistics"] == []
    assert report["summary"]["turn_position_statistics"] == []
    assert "初手番データがありません。" in rendered
    assert "手番順データがありません。" in rendered


def test_integrity_error_is_excluded_from_completion_and_win_rates():
    batch = _batch()
    batch["matches"][0]["validation_errors"] = ["resource total"]

    report = build_report_data(batch, generated_at="fixed")
    seats = {row["seat"]: row for row in report["summary"]["seat_statistics"]}

    assert report["summary"]["completed"] == 1
    assert report["summary"]["integrity_failures"] == 1
    assert report["summary"]["total_rolls"] == 36
    assert seats[1]["wins"] == 0
    assert seats[2]["wins"] == 1


def test_completed_match_without_a_winner_is_not_used_as_a_win_sample():
    report = build_report_data(
        {
            "matches": [
                {
                    "completed": True,
                    "players": [{"seat": 1, "name": "CPU1", "vp": 9}],
                }
            ]
        },
        generated_at="fixed",
    )

    assert report["summary"]["completed"] == 0
    assert report["summary"]["seat_statistics"][0]["completed_appearances"] == 0


def test_partial_legacy_action_metrics_are_not_shown_as_full_sample_averages():
    batch = _batch()
    del batch["matches"][1]["players"][1]["action_counts"]

    report = build_report_data(batch, generated_at="fixed")
    trader = next(
        row
        for row in report["summary"]["personality_statistics"]
        if row["personality"] == "trader"
    )

    assert trader["completed_appearances"] == 2
    assert trader["average_domestic_trade_offers"] is None
    assert trader["average_bank_trades"] is None


def test_html_bounds_the_individual_match_table(monkeypatch):
    monkeypatch.setattr(report_module, "MAX_HTML_MATCH_ROWS", 1)

    rendered = render_html_dashboard(build_report_data(_batch(), generated_at="fixed"))

    assert "個別試合 1 / 3件" in rendered
    assert "全結果はJSONに保存" in rendered
    assert "拡大重視" in rendered
    assert "交渉重視" in rendered


def test_terminal_summary_and_cli_are_human_readable(tmp_path, capsys):
    source = tmp_path / "results.json"
    source.write_text(json.dumps(_batch(), ensure_ascii=False), encoding="utf-8")

    assert main([str(source), "--output-dir", str(tmp_path / "out")]) == 0
    output = capsys.readouterr().out
    assert "AI自己対戦レポート" in output
    assert "席順別勝率" in output
    assert "HTML:" in output

    report = build_report_data(_batch(), generated_at="fixed")
    summary_text = render_terminal_summary(report)
    assert "完走: 2 (66.7%)" in summary_text
    assert "拡大重視" in summary_text


def test_basename_cannot_escape_output_directory(tmp_path):
    with pytest.raises(ReportError):
        write_report(_batch(), tmp_path, basename="../escape")


def test_real_self_play_batch_dataclass_is_directly_accepted():
    from game.self_play import run_batch

    batch = run_batch(
        match_seeds=(20260713,),
        board_seed=42,
        player_count=2,
        victory_target=5,
    )
    report = build_report_data(batch, generated_at="fixed")

    assert report["metadata"]["games_requested"] == 1
    assert report["metadata"]["board_seed"] == 42
    assert report["summary"]["completed"] == 1
    assert report["summary"]["integrity_failures"] == 0
    assert report["summary"]["integrity_rate"] == 1.0
    assert report["summary"]["initial_placement_statistics"]
    assert report["summary"]["initial_pip_statistics"]
    assert {
        row["personality"] for row in report["summary"]["personality_statistics"]
    } == {"standard", "expansion"}
    assert report["summary"]["personality_seat_statistics"]
    assert all(
        row["average_roads"] is not None
        and row["average_settlements"] is not None
        and row["average_cities"] is not None
        for row in report["summary"]["personality_statistics"]
    )
    assert report["matches"][0]["winner_seat"] in (1, 2)
    assert report["matches"][0]["turns"] == sum(
        report["matches"][0]["dice_counts"].values()
    )


def _analytics_match(
    *,
    board_seed=4242,
    winner_seat=1,
    expansion_vp=10,
    trader_vp=7,
    turns=30,
    validation_errors=None,
):
    return {
        "board_mode": "constrained",
        "board_seed": board_seed,
        "completed": True,
        "winner_seat": winner_seat,
        "turns": turns,
        "players": [
            {
                "seat": 1,
                "name": "Builder",
                "personality": "expansion",
                "vp": expansion_vp,
            },
            {
                "seat": 2,
                "name": "Trader",
                "personality": "trader",
                "vp": trader_vp,
            },
        ],
        "validation_errors": validation_errors or [],
    }


def test_personality_matchups_are_directionally_consistent_and_ignore_bad_samples():
    matches = [
        _analytics_match(expansion_vp=10, trader_vp=7, winner_seat=1),
        _analytics_match(expansion_vp=8, trader_vp=10, winner_seat=2),
        _analytics_match(expansion_vp=9, trader_vp=9, winner_seat=1),
        _analytics_match(expansion_vp=10, trader_vp=9, winner_seat=1),
        _analytics_match(
            expansion_vp=99,
            trader_vp=0,
            winner_seat=1,
            validation_errors=["broken"],
        ),
        {
            **_analytics_match(expansion_vp=10, trader_vp=7),
            "completed": False,
        },
        {
            **_analytics_match(expansion_vp=10, trader_vp=7),
            "players": [
                {"seat": 1, "name": "Unknown", "personality": None, "vp": 10},
                {"seat": 2, "name": "No VP", "personality": "trader"},
            ],
        },
    ]

    summary = build_report_data({"matches": matches}, generated_at="fixed")["summary"]
    rows = {
        (row["personality_a"], row["personality_b"]): row
        for row in summary["personality_matchup_statistics"]
    }
    forward = rows[("expansion", "trader")]
    reverse = rows[("trader", "expansion")]

    assert forward["comparisons"] == 4
    assert (forward["wins"], forward["ties"], forward["losses"]) == (2, 1, 1)
    assert forward["score_rate"] == pytest.approx(0.625)
    assert forward["average_vp_margin"] == pytest.approx(0.5)
    assert reverse["wins"] == forward["losses"]
    assert reverse["ties"] == forward["ties"]
    assert reverse["losses"] == forward["wins"]
    assert reverse["score_rate"] == pytest.approx(1 - forward["score_rate"])
    assert reverse["average_vp_margin"] == pytest.approx(
        -forward["average_vp_margin"]
    )
    assert forward["score_rate_ci95"]["lower"] == pytest.approx(
        1 - reverse["score_rate_ci95"]["upper"]
    )
    assert forward["score_rate_ci95"]["upper"] == pytest.approx(
        1 - reverse["score_rate_ci95"]["lower"]
    )


def test_board_fairness_groups_repeated_boards_and_summarises_one_off_seeds():
    matches = [
        _analytics_match(winner_seat=1, turns=10),
        _analytics_match(winner_seat=1, turns=20),
        _analytics_match(winner_seat=2, turns=30),
        _analytics_match(winner_seat=2, turns=40),
        _analytics_match(board_seed=9999, winner_seat=1, turns=50),
        _analytics_match(
            board_seed=4242,
            winner_seat=1,
            turns=1,
            validation_errors=["invalid"],
        ),
    ]

    summary = build_report_data({"matches": matches}, generated_at="fixed")["summary"]
    overview = summary["board_fairness_overview"]
    rows = summary["board_fairness_statistics"]

    assert overview == {
        "valid_completed_matches": 5,
        "eligible_matches": 5,
        "ungrouped_matches": 0,
        "board_groups": 2,
        "repeated_board_groups": 1,
        "repeated_board_matches": 4,
        "single_match_board_groups": 1,
        "single_match_matches": 1,
        "small_sample_repeated_board_groups": 1,
    }
    assert len(rows) == 1
    board = rows[0]
    assert board["board_seed"] == 4242
    assert board["player_count"] == 2
    assert board["matches"] == 4
    assert board["average_turns"] == 25
    assert board["max_seat_win_rate_gap"] == 0
    assert board["normalized_winner_entropy"] == pytest.approx(1)
    assert board["small_sample"] is True
    seats = {row["seat"]: row for row in board["seat_statistics"]}
    assert seats[1]["wins"] == seats[2]["wins"] == 2
    assert seats[1]["win_rate"] == pytest.approx(0.5)
    assert seats[1]["win_rate_ci95"]["lower"] == pytest.approx(0.15003899)
    assert seats[1]["win_rate_ci95"]["upper"] == pytest.approx(0.84996101)


def test_board_fairness_marks_twenty_match_sample_and_html_escapes_seed():
    unsafe_seed = '<img src="https://bad.example/x">'
    matches = [
        _analytics_match(
            board_seed=unsafe_seed,
            winner_seat=1 if index % 2 == 0 else 2,
        )
        for index in range(20)
    ]
    report = build_report_data({"matches": matches}, generated_at="fixed")
    board = report["summary"]["board_fairness_statistics"][0]
    rendered = render_html_dashboard(report)

    assert board["small_sample"] is False
    assert "AI性格相性表" in rendered
    assert "A \\ B" in rendered
    assert "盤面公平性" in rendered
    assert "Wilson score interval" in rendered
    assert ".panel { min-width:0;" in rendered
    assert ".scroll { max-width:100%; overflow:auto;" in rendered
    assert rendered.count('class="seat-line"') == 2
    assert 'class="seat-stats"' in rendered
    assert ">1.000</td>" in rendered
    assert ">0.0pt</td>" in rendered
    assert unsafe_seed not in rendered
    assert "&lt;img src=&quot;https://bad.example/x&quot;&gt;" in rendered
    assert '<script src="http' not in rendered


def test_new_dashboard_sections_are_backward_compatible_with_old_report_payload():
    old_report = {
        "generated_at": "old",
        "metadata": {},
        "matches": [],
        "summary": {
            "matches": 0,
            "completed": 0,
            "completion_rate": None,
            "average_turns": None,
            "median_turns": None,
            "total_rolls": 0,
            "dice_total_variation": None,
            "integrity_failures": 0,
            "integrity_rate": None,
        },
    }

    rendered = render_html_dashboard(old_report)

    assert "AI性格相性データがありません。" in rendered
    assert "盤面seed付きの完走データがありません。" in rendered
