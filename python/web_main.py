"""Run the loopback-only browser MVP."""

from __future__ import annotations

import argparse
import os
import webbrowser

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from game.web_server import DEFAULT_WEB_HOST, DEFAULT_WEB_PORT, create_web_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="カタン風ゲームのローカルWeb版を起動します。",
    )
    parser.add_argument("--host", default=DEFAULT_WEB_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_WEB_PORT)
    parser.add_argument(
        "--open",
        action="store_true",
        help="起動後に既定ブラウザで開きます。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    server = create_web_server(args.host, args.port)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    print(f"Catan Web: {url}")
    print("ローカル専用です。Internetへportを公開しないでください。")
    if args.open:
        webbrowser.open(url, new=2)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nCatan Webを終了します。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
