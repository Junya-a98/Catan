"""Offline statistics reports for AI self-play batches.

The report layer intentionally depends only on Python's standard library.  It
accepts plain mappings or dataclasses, normalises the small public result
schema, and produces JSON plus a static HTML dashboard.  The HTML has no
scripts, remote fonts, analytics, or CDN dependencies, so it is safe to open
without a network connection.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import html
import json
import math
import os
from pathlib import Path
import re
import statistics
import tempfile
from typing import Any, Iterable, Mapping, Optional, Sequence


__all__ = (
    "DICE_WEIGHTS",
    "MAX_REPORT_MATCHES",
    "ReportError",
    "ReportPaths",
    "build_report_data",
    "render_html_dashboard",
    "render_terminal_summary",
    "write_report",
)


DICE_WEIGHTS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
MAX_REPORT_MATCHES = 100_000
MAX_HTML_MATCH_ROWS = 1_000
MAX_INPUT_BYTES = 64 * 1024 * 1024
_SAFE_BASENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_MISSING = object()


class ReportError(ValueError):
    """Raised when self-play results cannot be safely normalised or written."""


@dataclass(frozen=True)
class ReportPaths:
    json_path: Path
    html_path: Path


def build_report_data(
    source: Any,
    *,
    metadata: Optional[Mapping[str, Any]] = None,
    generated_at: Optional[str] = None,
) -> dict[str, Any]:
    """Normalise a self-play batch and calculate dashboard statistics.

    ``source`` may be a sequence of match mappings/dataclasses or a batch
    mapping/dataclass containing ``matches``.  The canonical match fields are
    documented in ``docs/self-play-report.md``; a few conservative aliases are
    accepted to keep the report decoupled from the simulation runner.
    """

    raw_matches, source_metadata = _extract_batch(source)
    if len(raw_matches) > MAX_REPORT_MATCHES:
        raise ReportError(f"1レポートは最大 {MAX_REPORT_MATCHES:,} 試合です。")

    matches = [_normalise_match(match, index) for index, match in enumerate(raw_matches)]
    combined_metadata = dict(source_metadata)
    if metadata is not None:
        combined_metadata.update(_as_mapping(metadata, "metadata"))

    total = len(matches)
    completed = sum(bool(match["completed"]) for match in matches)
    winner_known = sum(match["winner_seat"] is not None for match in matches)
    turns = [
        match["turns"]
        for match in matches
        if match["completed"] and match["turns"] is not None
    ]

    seat_rows = _seat_statistics(matches)
    personality_rows = _personality_statistics(matches)
    starting_seat_rows = _starting_seat_statistics(matches)
    turn_position_rows = _turn_position_statistics(matches)
    initial_placement_rows = _initial_placement_statistics(matches)
    initial_pip_rows = _initial_pip_statistics(matches)
    dice_rows, total_rolls, dice_gap = _dice_statistics(matches)
    integrity_failures = sum(bool(match["validation_errors"]) for match in matches)

    summary: dict[str, Any] = {
        "matches": total,
        "completed": completed,
        "completion_rate": _ratio(completed, total),
        "winner_known": winner_known,
        "integrity_failures": integrity_failures,
        "integrity_rate": _ratio(total - integrity_failures, total),
        "average_turns": _mean_or_none(turns),
        "median_turns": _median_or_none(turns),
        "total_rolls": total_rolls,
        "dice_total_variation": dice_gap,
        "seat_statistics": seat_rows,
        "personality_statistics": personality_rows,
        "starting_seat_statistics": starting_seat_rows,
        "turn_position_statistics": turn_position_rows,
        "initial_placement_statistics": initial_placement_rows,
        "initial_pip_statistics": initial_pip_rows,
        "dice_statistics": dice_rows,
    }

    report: dict[str, Any] = {
        "format": "catan-self-play-report",
        "version": 1,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "metadata": _json_safe(combined_metadata, "metadata"),
        "summary": summary,
        "matches": matches,
    }
    # Validate the exact payload now, rather than failing halfway through an
    # atomic file write later.
    _encode_json(report)
    return report


def render_terminal_summary(report: Mapping[str, Any]) -> str:
    """Return a compact Japanese terminal summary for a report payload."""

    summary = _report_summary(report)
    lines = [
        "AI自己対戦レポート",
        (
            f"  試合: {summary['matches']} / 完走: {summary['completed']} "
            f"({_percent(summary['completion_rate'])})"
        ),
        (
            "  ターン: "
            f"平均 {_number(summary.get('average_turns'))} / "
            f"中央値 {_number(summary.get('median_turns'))}"
        ),
        (
            f"  ダイス: {summary.get('total_rolls', 0)} 回 / "
            "期待分布との差 "
            f"{_percent(summary.get('dice_total_variation'))}"
        ),
        (
            f"  状態整合性: {summary['matches'] - summary.get('integrity_failures', 0)}"
            f" / {summary['matches']} ({_percent(summary.get('integrity_rate'))})"
        ),
    ]
    if _has_small_sample(summary):
        lines.append("  注記: 全体100戦未満または各比較20出場未満は傾向把握用です。")

    seat_rows = summary.get("seat_statistics", [])
    if seat_rows:
        lines.append("  席順別勝率:")
        for row in seat_rows:
            lines.append(
                f"    席{row['seat']}: {row['wins']}勝/{row['completed_appearances']}出場 / "
                f"{_percent(row['win_rate'])} / 平均VP {_number(row['average_vp'])}"
            )

    personality_rows = summary.get("personality_statistics", [])
    if personality_rows:
        lines.append("  AI性格別勝率:")
        for row in personality_rows:
            lines.append(
                f"    {row['personality']}: {row['wins']}勝/"
                f"{row['completed_appearances']}出場 / "
                f"{_percent(row['win_rate'])}"
            )
    return "\n".join(lines)


def render_html_dashboard(report: Mapping[str, Any]) -> str:
    """Render a responsive, script-free, offline HTML dashboard."""

    summary = _report_summary(report)
    metadata = report.get("metadata", {})
    matches = report.get("matches", [])
    if not isinstance(metadata, Mapping) or not isinstance(matches, Sequence):
        raise ReportError("レポートのmetadataまたはmatchesが不正です。")

    title = "CATAN風 AI自己対戦ダッシュボード"
    meta_chips = []
    metadata_labels = (
        ("games_requested", "要求試合数"),
        ("master_seed", "基準seed"),
        ("board_seed", "盤面seed"),
        ("board_seed_scope", "盤面seed運用"),
        ("board_mode", "盤面mode"),
        ("victory_target", "勝利点"),
        ("player_count", "人数"),
        ("duration_seconds", "実行秒"),
    )
    for key, label in metadata_labels:
        if key in metadata and metadata[key] is not None:
            meta_chips.append(
                f'<span class="chip"><b>{_h(label)}</b> {_h(metadata[key])}</span>'
            )

    cards = (
        _metric_card("試合数", str(summary["matches"]), "集計対象"),
        _metric_card(
            "完走率",
            _percent(summary["completion_rate"]),
            f"{summary['completed']} / {summary['matches']}",
        ),
        _metric_card(
            "平均ターン",
            _number(summary.get("average_turns")),
            f"中央値 {_number(summary.get('median_turns'))}",
        ),
        _metric_card(
            "ダイス分布差",
            _percent(summary.get("dice_total_variation")),
            f"{summary.get('total_rolls', 0)} ロール",
        ),
        _metric_card(
            "状態整合性",
            _percent(summary.get("integrity_rate")),
            f"エラー {summary.get('integrity_failures', 0)} 試合",
        ),
    )

    seat_table = _statistics_table(
        summary.get("seat_statistics", []),
        label_key="seat_label",
        empty="席順データがありません。",
    )
    personality_table = _statistics_table(
        summary.get("personality_statistics", []),
        label_key="personality",
        empty="AI性格データがありません。",
    )
    start_table = _simple_win_table(
        summary.get("starting_seat_statistics", []),
        label=lambda row: f"席{row['seat']}",
        exposure_label="完走/全",
        empty="初手番データがありません。",
    )
    position_table = _simple_win_table(
        summary.get("turn_position_statistics", []),
        label=lambda row: f"{row['position'] + 1}番手",
        exposure_label="完走/全",
        empty="手番順データがありません。",
    )
    initial_placement_table = _initial_placement_table(
        summary.get("initial_placement_statistics", [])
    )
    initial_pip_table = _initial_pip_table(
        summary.get("initial_pip_statistics", [])
    )
    dice_table = _dice_table(summary.get("dice_statistics", []))
    displayed_matches = matches[:MAX_HTML_MATCH_ROWS]
    match_table = _match_table(displayed_matches)
    match_limit_note = (
        f'<p class="notice">個別表は先頭{MAX_HTML_MATCH_ROWS:,}試合まで表示します。'
        "全結果はJSONに保存されています。</p>"
        if len(matches) > MAX_HTML_MATCH_ROWS
        else ""
    )

    sample_notice = (
        '<p class="notice">全体100戦未満、または各比較行20出場未満の勝率は振れ幅が大きいため、傾向把握用として見てください。</p>'
        if _has_small_sample(summary)
        else ""
    )

    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
  <title>{_h(title)}</title>
  <style>
    :root {{ color-scheme: dark; --bg:#07111d; --panel:#111f30; --line:#2e4964;
      --ink:#f4f0df; --muted:#a8b9c9; --gold:#efc878; --sea:#55b6c9;
      --green:#69c493; --red:#e97869; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:radial-gradient(circle at 50% 0,#153753 0,var(--bg) 45%);
      color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"Noto Sans JP",sans-serif;
      line-height:1.55; }}
    main {{ width:min(1180px,calc(100% - 28px)); margin:0 auto; padding:34px 0 60px; }}
    h1 {{ margin:0; font-size:clamp(1.55rem,4vw,2.45rem); letter-spacing:.02em; }}
    h2 {{ margin:0 0 14px; font-size:1.16rem; }}
    .lead {{ color:var(--muted); margin:.35rem 0 1rem; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:8px; }}
    .chip {{ border:1px solid var(--line); background:#0a1826; border-radius:999px;
      padding:5px 11px; color:var(--muted); font-size:.86rem; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin:22px 0; }}
    .card,.panel {{ border:1px solid var(--line); background:rgba(17,31,48,.94);
      box-shadow:0 10px 30px #0004; border-radius:15px; }}
    .card {{ padding:16px; }} .card small {{ color:var(--muted); }}
    .value {{ display:block; margin:4px 0 2px; color:var(--gold); font-size:1.65rem; font-weight:750; }}
    .layout {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
    .panel {{ padding:18px; overflow:hidden; }} .wide {{ grid-column:1/-1; }}
    .scroll {{ overflow:auto; border-radius:9px; }}
    table {{ width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }}
    th,td {{ border-bottom:1px solid #294158; padding:9px 8px; text-align:right; white-space:nowrap; }}
    th:first-child,td:first-child {{ text-align:left; }} th {{ color:var(--muted); font-size:.8rem; }}
    tbody tr:last-child td {{ border-bottom:0; }}
    .bar-track {{ min-width:100px; height:8px; border-radius:99px; background:#07111d; overflow:hidden; }}
    .bar {{ height:100%; border-radius:inherit; background:linear-gradient(90deg,var(--sea),var(--green)); }}
    .positive {{ color:var(--green); }} .negative {{ color:var(--red); }} .muted {{ color:var(--muted); }}
    .empty {{ color:var(--muted); margin:0; }}
    .notice {{ border-left:3px solid var(--gold); background:#182435; color:var(--muted);
      border-radius:0 9px 9px 0; padding:9px 12px; margin:14px 0 0; }}
    details summary {{ cursor:pointer; color:var(--gold); margin-bottom:12px; }}
    footer {{ margin-top:18px; color:var(--muted); font-size:.82rem; text-align:center; }}
    @media(max-width:820px) {{ .grid {{ grid-template-columns:1fr 1fr; }} .layout {{ grid-template-columns:1fr; }}
      .wide {{ grid-column:auto; }} }}
    @media(max-width:480px) {{ main {{ width:min(100% - 18px,1180px); padding-top:22px; }}
      .grid {{ grid-template-columns:1fr; }} .panel {{ padding:14px; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>{_h(title)}</h1>
    <p class="lead">seed付き自己対戦の結果を、席順・AI性格・ダイス分布から確認できます。</p>
    <div class="chips">{''.join(meta_chips)}</div>
    {sample_notice}
  </header>
  <section class="grid">{''.join(cards)}</section>
  <div class="layout">
    <section class="panel"><h2>席順別</h2>{seat_table}</section>
    <section class="panel"><h2>AI性格別</h2>{personality_table}</section>
    <section class="panel"><h2>初手番だった席</h2>{start_table}</section>
    <section class="panel"><h2>手番位置別</h2>{position_table}</section>
    <section class="panel"><h2>初期配置の6・8接触</h2>{initial_placement_table}</section>
    <section class="panel"><h2>初期pip別</h2>{initial_pip_table}</section>
    <section class="panel wide"><h2>ダイス分布</h2>{dice_table}</section>
    <section class="panel wide"><details open><summary>個別試合 {len(displayed_matches)} / {len(matches)}件</summary>{match_limit_note}{match_table}</details></section>
  </div>
  <footer>完全ローカルHTML · 外部通信、JavaScript、トラッキングなし · {_h(report.get('generated_at', ''))}</footer>
</main>
</body>
</html>
"""


def write_report(
    source: Any,
    output_dir: os.PathLike[str] | str,
    *,
    basename: str = "self-play-report",
    metadata: Optional[Mapping[str, Any]] = None,
    generated_at: Optional[str] = None,
) -> ReportPaths:
    """Atomically write JSON and HTML reports and return their paths."""

    if not isinstance(basename, str) or not _SAFE_BASENAME.fullmatch(basename):
        raise ReportError("basenameは英数字・点・ハイフン・下線（80文字以内）で指定してください。")
    directory = Path(output_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise ReportError("レポート出力先がディレクトリではありません。")

    report = build_report_data(source, metadata=metadata, generated_at=generated_at)
    json_text = _encode_json(report) + "\n"
    html_text = render_html_dashboard(report)
    json_path = directory / f"{basename}.json"
    html_path = directory / f"{basename}.html"
    _atomic_write_text(json_path, json_text)
    _atomic_write_text(html_path, html_text)
    return ReportPaths(json_path=json_path, html_path=html_path)


def _extract_batch(source: Any) -> tuple[list[Any], dict[str, Any]]:
    if is_dataclass(source) and not isinstance(source, type):
        source = asdict(source)
    if isinstance(source, Mapping):
        if "matches" not in source:
            raise ReportError("バッチ結果にmatchesがありません。")
        matches = source["matches"]
        metadata = source.get("metadata", {})
        if metadata is None:
            metadata = {}
        metadata = dict(_as_mapping(metadata, "metadata"))
        for key in (
            "games_requested",
            "master_seed",
            "board_mode",
            "victory_target",
            "player_count",
            "duration_seconds",
        ):
            if key in source and key not in metadata:
                metadata[key] = source[key]
        if "game_count" in source and "games_requested" not in metadata:
            metadata["games_requested"] = source["game_count"]
        if "board_seed" in source and "board_seed" not in metadata:
            metadata["board_seed"] = source["board_seed"]
    else:
        matches = source
        metadata = {}

    if isinstance(matches, (str, bytes, bytearray)) or not isinstance(matches, Sequence):
        raise ReportError("matchesは試合結果の配列にしてください。")
    return list(matches), metadata


def _normalise_match(raw: Any, index: int) -> dict[str, Any]:
    match = _as_mapping(raw, f"matches[{index}]")
    players = _normalise_players(match.get("players", []), index)

    match_seed = _first(match, "match_seed", "seed", default=None)
    board_seed = _first(match, "board_seed", default=None)
    winner_from_index = "winner_seat" not in match and "winner_index" in match
    winner = _first(match, "winner_seat", "winner_index", "winner", default=None)
    winner_seat: Optional[int]
    winner_name = _first(match, "winner_name", default=None)
    if isinstance(winner, str):
        winner_seat = None
        winner_name = winner_name or winner
    elif winner is None:
        winner_seat = None
    else:
        winner_seat = _non_negative_int(winner, f"matches[{index}].winner_seat")
        if winner_from_index:
            winner_seat += 1
        elif winner_seat == 0:
            raise ReportError(f"matches[{index}].winner_seatは1始まりです。")

    if winner_seat is None and winner_name:
        for player in players:
            if player["name"] == str(winner_name):
                winner_seat = player["seat"]
                break
    if winner_seat is not None and winner_name is None:
        winner_player = next((p for p in players if p["seat"] == winner_seat), None)
        if winner_player:
            winner_name = winner_player["name"]
    if players:
        player_seats = {player["seat"] for player in players}
        player_names = {player["name"] for player in players}
        if winner_seat is not None and winner_seat not in player_seats:
            raise ReportError(f"matches[{index}].winner_seatが参加席にありません。")
        if winner_name is not None and str(winner_name) not in player_names:
            raise ReportError(f"matches[{index}].winner_nameが参加者にありません。")
        if winner_seat is not None and winner_name is not None:
            seat_player = next(player for player in players if player["seat"] == winner_seat)
            if seat_player["name"] != str(winner_name):
                raise ReportError(f"matches[{index}]の勝者名と席番号が一致しません。")

    completed_raw = _first(match, "completed", default=_MISSING)
    if completed_raw is _MISSING:
        completed = winner_seat is not None or winner_name is not None
    elif isinstance(completed_raw, bool):
        completed = completed_raw
    else:
        raise ReportError(f"matches[{index}].completedは真偽値にしてください。")

    turns_raw = _first(match, "turns", "turn_count", "rounds", default=None)
    turns = None if turns_raw is None else _non_negative_int(turns_raw, f"matches[{index}].turns")
    action_steps_raw = _first(match, "action_steps", "steps", default=None)
    action_steps = (
        None
        if action_steps_raw is None
        else _non_negative_int(action_steps_raw, f"matches[{index}].action_steps")
    )
    dice_counts = _normalise_dice_counts(
        _first(match, "dice_counts", "roll_counts", "dice_distribution", default={}),
        index,
    )

    turn_order_raw = _first(match, "turn_order", default=[])
    if turn_order_raw is None:
        turn_order_raw = []
    if isinstance(turn_order_raw, (str, bytes)) or not isinstance(turn_order_raw, Sequence):
        raise ReportError(f"matches[{index}].turn_orderは席番号の配列にしてください。")
    turn_order = [
        _positive_int(value, f"matches[{index}].turn_order") for value in turn_order_raw
    ]
    if len(set(turn_order)) != len(turn_order):
        raise ReportError(f"matches[{index}].turn_orderに重複があります。")
    if players and any(seat not in player_seats for seat in turn_order):
        raise ReportError(f"matches[{index}].turn_orderに参加者以外の席があります。")
    if players and turn_order and set(turn_order) != player_seats:
        raise ReportError(f"matches[{index}].turn_orderに全参加席を含めてください。")

    starting_raw = _first(match, "starting_player_seat", "starting_seat", default=None)
    starting_player_seat = (
        None
        if starting_raw is None
        else _positive_int(starting_raw, f"matches[{index}].starting_player_seat")
    )
    if starting_player_seat is None and turn_order:
        starting_player_seat = turn_order[0]
    if players and starting_player_seat is not None and starting_player_seat not in player_seats:
        raise ReportError(f"matches[{index}].starting_player_seatが参加席にありません。")
    if turn_order and starting_player_seat not in turn_order:
        raise ReportError(f"matches[{index}].starting_player_seatがturn_orderにありません。")
    if turn_order and starting_player_seat != turn_order[0]:
        raise ReportError(f"matches[{index}].starting_player_seatとturn_orderの先頭が一致しません。")

    winner_player = next((p for p in players if p["seat"] == winner_seat), None)
    winner_personality = _first(match, "winner_personality", default=None)
    if winner_personality is None and winner_player:
        winner_personality = winner_player["personality"]

    termination = _first(match, "termination_reason", "reason", default="")
    validation_errors_raw = _first(match, "validation_errors", default=[])
    if validation_errors_raw is None:
        validation_errors_raw = []
    if isinstance(validation_errors_raw, (str, bytes)) or not isinstance(
        validation_errors_raw, Sequence
    ):
        raise ReportError(f"matches[{index}].validation_errorsは文字列の配列にしてください。")
    validation_errors = [str(error) for error in validation_errors_raw]
    if validation_errors or (completed and winner_seat is None and winner_name is None):
        completed = False
    return {
        "match_number": index + 1,
        "match_seed": _json_scalar(match_seed, f"matches[{index}].match_seed"),
        "board_seed": _json_scalar(board_seed, f"matches[{index}].board_seed"),
        "board_mode": str(_first(match, "board_mode", default="")),
        "victory_target": _optional_non_negative_int(
            _first(match, "victory_target", default=None),
            f"matches[{index}].victory_target",
        ),
        "completed": completed,
        "termination_reason": str(termination or ""),
        "winner_seat": winner_seat,
        "winner_name": None if winner_name is None else str(winner_name),
        "winner_personality": (
            None if winner_personality in (None, "") else str(winner_personality)
        ),
        "starting_player_seat": starting_player_seat,
        "turn_order": turn_order,
        "turns": turns,
        "action_steps": action_steps,
        "dice_counts": dice_counts,
        "players": players,
        "validation_errors": validation_errors,
    }


def _normalise_players(raw: Any, match_index: int) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise ReportError(f"matches[{match_index}].playersは配列にしてください。")
    players = []
    seen_seats = set()
    for fallback_seat, value in enumerate(raw):
        player = _as_mapping(value, f"matches[{match_index}].players[{fallback_seat}]")
        seat_from_index = "seat" not in player and "seat_index" in player
        seat = _non_negative_int(
            _first(player, "seat", "seat_index", default=fallback_seat + 1),
            f"matches[{match_index}].players[{fallback_seat}].seat",
        )
        if seat_from_index:
            seat += 1
        elif seat == 0:
            raise ReportError(
                f"matches[{match_index}].players[{fallback_seat}].seatは1始まりです。"
            )
        if seat in seen_seats:
            raise ReportError(f"matches[{match_index}].playersの席番号が重複しています。")
        seen_seats.add(seat)
        vp = _first(player, "vp", "victory_points", "points", default=None)
        personality = _first(player, "personality", "ai_personality", "profile", default=None)
        raw_resources = _first(player, "resources", default={})
        if raw_resources is None:
            raw_resources = {}
        if not isinstance(raw_resources, Mapping):
            raise ReportError(
                f"matches[{match_index}].players[{fallback_seat}].resourcesは辞書にしてください。"
            )
        resources = {
            str(resource): _non_negative_int(
                amount,
                f"matches[{match_index}].players[{fallback_seat}].resources[{resource}]",
            )
            for resource, amount in raw_resources.items()
        }
        players.append(
            {
                "seat": seat,
                "name": str(_first(player, "name", default=f"Player{seat}")),
                "personality": None if personality in (None, "") else str(personality),
                "vp": _optional_non_negative_int(
                    vp, f"matches[{match_index}].players[{fallback_seat}].vp"
                ),
                "public_vp": _optional_non_negative_int(
                    _first(player, "public_vp", "public_victory_points", default=None),
                    f"matches[{match_index}].players[{fallback_seat}].public_vp",
                ),
                "resources": resources,
                "resource_cards": _optional_non_negative_int(
                    _first(player, "resource_cards", default=None),
                    f"matches[{match_index}].players[{fallback_seat}].resource_cards",
                ),
                "roads": _optional_non_negative_int(
                    _first(player, "roads", default=None),
                    f"matches[{match_index}].players[{fallback_seat}].roads",
                ),
                "settlements": _optional_non_negative_int(
                    _first(player, "settlements", default=None),
                    f"matches[{match_index}].players[{fallback_seat}].settlements",
                ),
                "cities": _optional_non_negative_int(
                    _first(player, "cities", default=None),
                    f"matches[{match_index}].players[{fallback_seat}].cities",
                ),
                "knights": _optional_non_negative_int(
                    _first(player, "knights", "played_knights", default=None),
                    f"matches[{match_index}].players[{fallback_seat}].knights",
                ),
                "initial_pips": _optional_non_negative_int(
                    _first(player, "initial_pips", default=None),
                    f"matches[{match_index}].players[{fallback_seat}].initial_pips",
                ),
                "initial_high_probability_access": _optional_bool(
                    _first(player, "initial_high_probability_access", default=None),
                    f"matches[{match_index}].players[{fallback_seat}]"
                    ".initial_high_probability_access",
                ),
                "initial_resource_diversity": _optional_non_negative_int(
                    _first(player, "initial_resource_diversity", default=None),
                    f"matches[{match_index}].players[{fallback_seat}]"
                    ".initial_resource_diversity",
                ),
                "longest_road": _optional_bool(
                    _first(player, "longest_road", default=None),
                    f"matches[{match_index}].players[{fallback_seat}].longest_road",
                ),
                "largest_army": _optional_bool(
                    _first(player, "largest_army", default=None),
                    f"matches[{match_index}].players[{fallback_seat}].largest_army",
                ),
                "won": _optional_bool(
                    _first(player, "won", default=None),
                    f"matches[{match_index}].players[{fallback_seat}].won",
                ),
            }
        )
    return sorted(players, key=lambda player: player["seat"])


def _normalise_dice_counts(raw: Any, match_index: int) -> dict[str, int]:
    if raw is None:
        raw = {}
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        if len(raw) != 11:
            raise ReportError(f"matches[{match_index}].dice_countsの配列は2〜12の11要素です。")
        raw = {total: count for total, count in zip(range(2, 13), raw)}
    if not isinstance(raw, Mapping):
        raise ReportError(f"matches[{match_index}].dice_countsは辞書または11要素の配列です。")

    result = {str(total): 0 for total in range(2, 13)}
    for key, count in raw.items():
        try:
            total = int(key)
        except (TypeError, ValueError) as exc:
            raise ReportError(f"matches[{match_index}].dice_countsの出目が不正です。") from exc
        if total not in DICE_WEIGHTS or str(key).strip() != str(total):
            raise ReportError(f"matches[{match_index}].dice_countsは2〜12だけを指定してください。")
        result[str(total)] = _non_negative_int(
            count, f"matches[{match_index}].dice_counts[{total}]"
        )
    return result


def _seat_statistics(matches: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seats = sorted(
        {
            player["seat"]
            for match in matches
            for player in match["players"]
        }
        | {
            match["winner_seat"]
            for match in matches
            if match["winner_seat"] is not None
        }
    )
    playerless_matches = [match for match in matches if not match["players"]]
    rows = []
    for seat in seats:
        wins = sum(
            match["completed"] and match["winner_seat"] == seat
            for match in matches
        )
        vp_values = [
            player["vp"]
            for match in matches
            for player in match["players"]
            if match["completed"]
            and player["seat"] == seat
            and player["vp"] is not None
        ]
        appearances = sum(
            any(player["seat"] == seat for player in match["players"])
            for match in matches
        ) + len(playerless_matches)
        completed_appearances = sum(
            match["completed"] and any(player["seat"] == seat for player in match["players"])
            for match in matches
        ) + sum(match["completed"] for match in playerless_matches)
        rows.append(
            {
                "seat": seat,
                "seat_label": f"席{seat}",
                "appearances": appearances,
                "completed_appearances": completed_appearances,
                "wins": wins,
                "win_rate": _ratio(wins, completed_appearances),
                "average_vp": _mean_or_none(vp_values),
            }
        )
    return rows


def _personality_statistics(matches: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    appearances: Counter[str] = Counter()
    completed_appearances: Counter[str] = Counter()
    wins: Counter[str] = Counter()
    vp_values: dict[str, list[int]] = defaultdict(list)
    for match in matches:
        for player in match["players"]:
            personality = player["personality"]
            if not personality:
                continue
            appearances[personality] += 1
            if match["completed"]:
                completed_appearances[personality] += 1
            if match["completed"] and player["vp"] is not None:
                vp_values[personality].append(player["vp"])
            if match["completed"] and match["winner_seat"] == player["seat"]:
                wins[personality] += 1
    return [
        {
            "personality": personality,
            "appearances": appearances[personality],
            "completed_appearances": completed_appearances[personality],
            "wins": wins[personality],
            "win_rate": _ratio(wins[personality], completed_appearances[personality]),
            "average_vp": _mean_or_none(vp_values[personality]),
        }
        for personality in sorted(appearances)
    ]


def _starting_seat_statistics(matches: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seats = sorted(
        {
            match["starting_player_seat"]
            for match in matches
            if match["starting_player_seat"] is not None
        }
    )
    rows = []
    for seat in seats:
        relevant = [match for match in matches if match["starting_player_seat"] == seat]
        completed = [match for match in relevant if match["completed"]]
        wins = sum(match["winner_seat"] == seat for match in completed)
        rows.append(
            {
                "seat": seat,
                "appearances": len(relevant),
                "completed_appearances": len(completed),
                "wins": wins,
                "win_rate": _ratio(wins, len(completed)),
            }
        )
    return rows


def _turn_position_statistics(matches: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    positions = sorted(
        {position for match in matches for position, _seat in enumerate(match["turn_order"])}
    )
    rows = []
    for position in positions:
        relevant = [match for match in matches if len(match["turn_order"]) > position]
        completed = [match for match in relevant if match["completed"]]
        wins = sum(
            match["winner_seat"] == match["turn_order"][position] for match in completed
        )
        rows.append(
            {
                "position": position,
                "appearances": len(relevant),
                "completed_appearances": len(completed),
                "wins": wins,
                "win_rate": _ratio(wins, len(completed)),
            }
        )
    return rows


def _initial_placement_statistics(
    matches: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for has_access, label in ((True, "6・8あり"), (False, "6・8なし")):
        observations = [
            (match, player)
            for match in matches
            for player in match["players"]
            if player["initial_high_probability_access"] is has_access
        ]
        if not observations:
            continue
        completed = [item for item in observations if item[0]["completed"]]
        wins = sum(
            match["winner_seat"] == player["seat"]
            for match, player in completed
        )
        pips = [
            player["initial_pips"]
            for _match, player in observations
            if player["initial_pips"] is not None
        ]
        diversity = [
            player["initial_resource_diversity"]
            for _match, player in observations
            if player["initial_resource_diversity"] is not None
        ]
        rows.append(
            {
                "label": label,
                "appearances": len(observations),
                "completed_appearances": len(completed),
                "wins": wins,
                "win_rate": _ratio(wins, len(completed)),
                "average_pips": _mean_or_none(pips),
                "average_resource_diversity": _mean_or_none(diversity),
            }
        )
    return rows


def _initial_pip_statistics(
    matches: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    pip_values = sorted(
        {
            player["initial_pips"]
            for match in matches
            for player in match["players"]
            if player["initial_pips"] is not None
        }
    )
    rows = []
    for pip_value in pip_values:
        observations = [
            (match, player)
            for match in matches
            for player in match["players"]
            if player["initial_pips"] == pip_value
        ]
        completed = [item for item in observations if item[0]["completed"]]
        wins = sum(
            match["winner_seat"] == player["seat"]
            for match, player in completed
        )
        vp_values = [
            player["vp"]
            for match, player in observations
            if match["completed"] and player["vp"] is not None
        ]
        rows.append(
            {
                "pips": pip_value,
                "appearances": len(observations),
                "completed_appearances": len(completed),
                "wins": wins,
                "win_rate": _ratio(wins, len(completed)),
                "average_vp": _mean_or_none(vp_values),
            }
        )
    return rows


def _dice_statistics(
    matches: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int, Optional[float]]:
    valid_matches = [match for match in matches if not match["validation_errors"]]
    counts = {
        total: sum(match["dice_counts"][str(total)] for match in valid_matches)
        for total in DICE_WEIGHTS
    }
    total_rolls = sum(counts.values())
    rows = []
    absolute_delta = 0.0
    for total, weight in DICE_WEIGHTS.items():
        actual = _ratio(counts[total], total_rolls)
        expected = weight / 36
        delta = None if actual is None else actual - expected
        if delta is not None:
            absolute_delta += abs(delta)
        rows.append(
            {
                "total": total,
                "count": counts[total],
                "actual_rate": actual,
                "expected_rate": expected,
                "delta": delta,
            }
        )
    variation = None if not total_rolls else absolute_delta / 2
    return rows, total_rolls, variation


def _statistics_table(rows: Sequence[Mapping[str, Any]], *, label_key: str, empty: str) -> str:
    if not rows:
        return f'<p class="empty">{_h(empty)}</p>'
    body = []
    for row in rows:
        width = _bar_width(row.get("win_rate"))
        body.append(
            "<tr>"
            f"<td>{_h(row[label_key])}</td>"
            f"<td>{row.get('completed_appearances', 0)}/{row.get('appearances', 0)}</td>"
            f"<td>{row['wins']}</td>"
            f"<td>{_percent(row.get('win_rate'))}</td>"
            f'<td><div class="bar-track"><div class="bar" style="width:{width:.2f}%"></div></div></td>'
            f"<td>{_number(row.get('average_vp'))}</td>"
            "</tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr><th>区分</th><th>完走/全</th><th>勝</th><th>勝率</th>'
        f"<th>比較</th><th>平均VP</th></tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def _simple_win_table(
    rows: Sequence[Mapping[str, Any]],
    *,
    label: Any,
    exposure_label: str,
    empty: str,
) -> str:
    if not rows:
        return f'<p class="empty">{_h(empty)}</p>'
    body = []
    for row in rows:
        width = _bar_width(row.get("win_rate"))
        body.append(
            "<tr>"
            f"<td>{_h(label(row))}</td>"
            f"<td>{row.get('completed_appearances', 0)}/{row['appearances']}</td>"
            f"<td>{row['wins']}</td>"
            f"<td>{_percent(row.get('win_rate'))}</td>"
            f'<td><div class="bar-track"><div class="bar" style="width:{width:.2f}%"></div></div></td>'
            "</tr>"
        )
    return (
        f'<div class="scroll"><table><thead><tr><th>区分</th><th>{_h(exposure_label)}</th>'
        f"<th>勝</th><th>勝率</th><th>比較</th></tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def _initial_placement_table(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return '<p class="empty">初期配置データがありません。</p>'
    body = []
    for row in rows:
        width = _bar_width(row.get("win_rate"))
        body.append(
            "<tr>"
            f"<td>{_h(row['label'])}</td>"
            f"<td>{row.get('completed_appearances', 0)}/{row['appearances']}</td>"
            f"<td>{row['wins']}</td><td>{_percent(row.get('win_rate'))}</td>"
            f'<td><div class="bar-track"><div class="bar" style="width:{width:.2f}%"></div></div></td>'
            f"<td>{_number(row.get('average_pips'))}</td>"
            f"<td>{_number(row.get('average_resource_diversity'))}</td>"
            "</tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr><th>初期配置</th><th>完走/全</th>'
        '<th>勝</th><th>勝率</th><th>比較</th><th>平均pip</th><th>平均資源種類</th>'
        f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def _initial_pip_table(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return '<p class="empty">初期pipデータがありません。</p>'
    body = []
    for row in rows:
        width = _bar_width(row.get("win_rate"))
        body.append(
            "<tr>"
            f"<td>{row['pips']}</td>"
            f"<td>{row.get('completed_appearances', 0)}/{row['appearances']}</td>"
            f"<td>{row['wins']}</td>"
            f"<td>{_percent(row.get('win_rate'))}</td>"
            f'<td><div class="bar-track"><div class="bar" style="width:{width:.2f}%"></div></div></td>'
            f"<td>{_number(row.get('average_vp'))}</td>"
            "</tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr><th>pip</th><th>完走/全</th><th>勝</th>'
        '<th>勝率</th><th>比較</th><th>平均VP</th>'
        f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def _dice_table(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return '<p class="empty">ダイスデータがありません。</p>'
    body = []
    for row in rows:
        delta = row.get("delta")
        delta_class = "positive" if delta is not None and delta > 0 else "negative" if delta else "muted"
        width = _bar_width(row.get("actual_rate"), scale=6)
        body.append(
            "<tr>"
            f"<td>{row['total']}</td><td>{row['count']}</td>"
            f"<td>{_percent(row.get('actual_rate'))}</td><td>{_percent(row.get('expected_rate'))}</td>"
            f'<td class="{delta_class}">{_signed_percent(delta)}</td>'
            f'<td><div class="bar-track"><div class="bar" style="width:{width:.2f}%"></div></div></td>'
            "</tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr><th>出目</th><th>回数</th><th>実測</th>'
        f"<th>期待値</th><th>差</th><th>分布</th></tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def _match_table(matches: Sequence[Mapping[str, Any]]) -> str:
    if not matches:
        return '<p class="empty">個別試合がありません。</p>'
    body = []
    for match in matches:
        winner = match.get("winner_name")
        if not winner and match.get("winner_seat") is not None:
            winner = f"席{match['winner_seat']}"
        winner = winner or "—"
        vp = ", ".join(
            f"{player['name']}:{_number(player.get('vp'))}" for player in match.get("players", [])
        ) or "—"
        if match.get("validation_errors"):
            status = "整合性エラー"
        else:
            status = "完走" if match.get("completed") else "未完走"
        body.append(
            "<tr>"
            f"<td>{match['match_number']}</td><td>{_h(match.get('match_seed'))}</td>"
            f"<td>{_h(match.get('board_seed'))}</td><td>{_h(status)}</td>"
            f"<td>{_h(winner)}</td><td>{_number(match.get('turns'))}</td>"
            f"<td>{_h(vp)}</td><td>{_h(match.get('termination_reason', ''))}</td>"
            "</tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr><th>#</th><th>match seed</th><th>board seed</th>'
        "<th>状態</th><th>勝者</th><th>ターン</th><th>最終VP</th><th>終了理由</th>"
        f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def _metric_card(label: str, value: str, note: str) -> str:
    return (
        f'<article class="card"><small>{_h(label)}</small>'
        f'<span class="value">{_h(value)}</span><small>{_h(note)}</small></article>'
    )


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    elif hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise ReportError(f"{label}は辞書またはdataclassにしてください。")
    return value


def _first(mapping: Mapping[str, Any], *keys: str, default: Any) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReportError(f"{label}は0以上の整数にしてください。")
    return value


def _positive_int(value: Any, label: str) -> int:
    value = _non_negative_int(value, label)
    if value == 0:
        raise ReportError(f"{label}は1以上の整数にしてください。")
    return value


def _optional_non_negative_int(value: Any, label: str) -> Optional[int]:
    return None if value is None else _non_negative_int(value, label)


def _optional_bool(value: Any, label: str) -> Optional[bool]:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ReportError(f"{label}は真偽値にしてください。")
    return value


def _json_scalar(value: Any, label: str) -> Any:
    if value is None or isinstance(value, (str, int)) and not isinstance(value, bool):
        return value
    raise ReportError(f"{label}は文字列・整数・nullのいずれかにしてください。")


def _json_safe(value: Any, label: str, *, depth: int = 0) -> Any:
    if depth > 8:
        raise ReportError(f"{label}の入れ子が深すぎます。")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ReportError(f"{label}にNaNまたはInfinityは使えません。")
        return value
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, f"{label}.{key}", depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _json_safe(item, f"{label}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    raise ReportError(f"{label}にJSON化できない値があります。")


def _encode_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ReportError("レポートをJSONへ変換できません。") from exc


def _report_summary(report: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(report, Mapping) or not isinstance(report.get("summary"), Mapping):
        raise ReportError("集計済みレポートにsummaryがありません。")
    return report["summary"]


def _has_small_sample(summary: Mapping[str, Any]) -> bool:
    if summary.get("matches", 0) < 100:
        return True
    row_groups = (
        "seat_statistics",
        "personality_statistics",
        "starting_seat_statistics",
        "turn_position_statistics",
        "initial_placement_statistics",
        "initial_pip_statistics",
    )
    return any(
        row.get("completed_appearances", 0) < 20
        for group in row_groups
        for row in summary.get(group, [])
    )


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return None if denominator <= 0 else numerator / denominator


def _mean_or_none(values: Iterable[int]) -> Optional[float]:
    values = list(values)
    return None if not values else statistics.fmean(values)


def _median_or_none(values: Iterable[int]) -> Optional[float]:
    values = list(values)
    return None if not values else float(statistics.median(values))


def _number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _percent(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def _signed_percent(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:+.1f}pt"


def _bar_width(value: Any, *, scale: float = 1.0) -> float:
    if value is None:
        return 0.0
    return min(100.0, max(0.0, float(value) * 100 * scale))


def _h(value: Any) -> str:
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)


def _atomic_write_text(path: Path, text: str) -> None:
    temporary_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except OSError as exc:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
        raise ReportError(f"レポートを書き込めませんでした: {path}") from exc


def _load_json(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ReportError(f"入力JSONは最大 {MAX_INPUT_BYTES // (1024 * 1024)} MiBです。")
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except ReportError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReportError(f"入力JSONを読み込めませんでした: {path}") from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AI自己対戦JSONから安全なローカルレポートを作成")
    parser.add_argument("input", type=Path, help="自己対戦結果JSON")
    parser.add_argument("--output-dir", type=Path, default=Path("self-play-reports"))
    parser.add_argument("--basename", default="self-play-report")
    args = parser.parse_args(argv)
    try:
        source = _load_json(args.input)
        paths = write_report(source, args.output_dir, basename=args.basename)
        report = build_report_data(source)
    except ReportError as exc:
        parser.exit(2, f"error: {exc}\n")
    print(render_terminal_summary(report))
    print(f"JSON: {paths.json_path}")
    print(f"HTML: {paths.html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
