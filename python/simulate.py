"""Run reproducible AI self-play and create an offline dashboard."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time
import webbrowser


os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from game.constants import MAX_VICTORY_POINT_TARGET, MIN_VICTORY_POINT_TARGET
from game.self_play import (
    DEFAULT_GAME_COUNT,
    DEFAULT_MAX_ACTION_STEPS,
    DEFAULT_MAX_TURNS,
    SUPPORTED_BOARD_MODES,
    SUPPORTED_PLAYER_COUNTS,
    parse_personality_lineup,
    run_batch,
)
from game.self_play_report import (
    ReportError,
    build_report_data,
    render_terminal_summary,
    write_report,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AI同士を同じルールで対戦させ、統計ダッシュボードを作成します。"
    )
    parser.add_argument("--games", type=int, default=DEFAULT_GAME_COUNT, help="試合数（既定: 20）")
    parser.add_argument("--seed", type=int, default=0, help="連番match seedの開始値")
    parser.add_argument(
        "--board-seed",
        type=int,
        default=None,
        help="全試合で検証する固定盤面seed（省略時は試合ごとに変更）",
    )
    parser.add_argument("--mode", choices=SUPPORTED_BOARD_MODES, default="constrained")
    parser.add_argument("--players", type=int, choices=SUPPORTED_PLAYER_COUNTS, default=4)
    parser.add_argument(
        "--personalities",
        type=parse_personality_lineup,
        default=None,
        help=(
            "席1から順にAI性格をカンマ区切りで指定。"
            "省略時は人数に応じてstandard,expansion,trader,disruptorの先頭から使用"
        ),
    )
    parser.add_argument(
        "--target",
        type=int,
        choices=range(MIN_VICTORY_POINT_TARGET, MAX_VICTORY_POINT_TARGET + 1),
        default=10,
        help="勝利点（5〜15、既定: 10）",
    )
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--max-actions", type=int, default=DEFAULT_MAX_ACTION_STEPS)
    parser.add_argument(
        "--workers",
        type=int,
        choices=range(0, 33),
        default=0,
        metavar="0..32",
        help=(
            "並列worker数（0: CPU数に応じて自動、1: 逐次実行、"
            "2〜32: 指定数で並列、既定: 0）"
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("self-play-reports"))
    parser.add_argument("--basename", default="self-play-latest")
    parser.add_argument("--open", action="store_true", help="完了後にHTMLを既定ブラウザで開く")
    parser.add_argument("--quiet", action="store_true", help="試合ごとの進捗表示を省略")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    started = time.perf_counter()
    requested_workers_label = "自動" if args.workers == 0 else str(args.workers)

    if not args.quiet:
        print(
            f"AI自己対戦を開始: {args.games}戦 / workers要求 {requested_workers_label}",
            file=sys.stderr,
        )

    def show_progress(index, total, match):
        if args.quiet:
            return
        status = "完走" if match.completed else match.reason
        print(
            f"\rAI自己対戦 {index:>3}/{total} — seed {match.match_seed}: {status}",
            end="",
            file=sys.stderr,
            flush=True,
        )

    try:
        batch = run_batch(
            game_count=args.games,
            match_seed_start=args.seed,
            board_seed=args.board_seed,
            board_mode=args.mode,
            player_count=args.players,
            victory_target=args.target,
            max_turns=args.max_turns,
            max_action_steps=args.max_actions,
            personalities=args.personalities,
            progress=show_progress,
            workers=args.workers,
        )
        duration = time.perf_counter() - started
        generated_at = datetime.now(timezone.utc).isoformat()
        metadata = {
            "games_requested": args.games,
            "master_seed": args.seed,
            "board_seed": args.board_seed,
            "board_seed_scope": "全試合固定" if args.board_seed is not None else "試合ごとに変更",
            "board_mode": args.mode,
            "victory_target": args.target,
            "player_count": args.players,
            "personality_lineup": " / ".join(batch.personality_lineup),
            "personality_seat_rotation": True,
            "workers_requested": args.workers,
            "workers_used": batch.worker_count,
            "duration_seconds": round(duration, 3),
            "max_turns": args.max_turns,
            "max_action_steps": args.max_actions,
        }
        paths = write_report(
            batch,
            args.output_dir,
            basename=args.basename,
            metadata=metadata,
            generated_at=generated_at,
        )
        report = build_report_data(
            batch,
            metadata=metadata,
            generated_at=generated_at,
        )
    except (OSError, ReportError, TypeError, ValueError) as exc:
        if not args.quiet:
            print(file=sys.stderr)
        parser.error(str(exc))

    if not args.quiet:
        print(file=sys.stderr)
    print(render_terminal_summary(report))
    print(
        "実行: "
        f"workers要求={requested_workers_label} / "
        f"使用={batch.worker_count} / {duration:.3f}秒"
    )
    print(f"JSON: {paths.json_path.resolve()}")
    print(f"HTML: {paths.html_path.resolve()}")
    if args.open:
        webbrowser.open(paths.html_path.resolve().as_uri(), new=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
