"""Run the browser MVP in loopback or explicit trusted-LAN mode."""

from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import sys
import webbrowser

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from game.lan_controller import LanServerController
from game.network_replay_store import SQLiteNetworkReplayStore
from game.server_state import SQLiteRoomAuthorityStore
from game.shared_rate_limit import SQLiteSharedRateLimitStore
from game.web_gateway import WebGateway
from game.web_server import DEFAULT_WEB_HOST, DEFAULT_WEB_PORT, create_web_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="カタン風ゲームのローカルWeb版を起動します。",
    )
    parser.add_argument("--host", default=DEFAULT_WEB_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_WEB_PORT)
    parser.add_argument(
        "--lan",
        action="store_true",
        help="信頼できる同一LANの別端末からの接続を明示的に許可します。",
    )
    parser.add_argument(
        "--friends-vpn",
        action="store_true",
        help=(
            "Tailscaleの認証済みpeerだけに、期限付き招待専用のHTTPS版を"
            "明示的に公開します。--lanとは併用できません。"
        ),
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        metavar="HOSTNAME_OR_IP",
        help=(
            "LAN端末がURLに使うprivate/link-local IPまたは.local名です。"
            "--lan時は1回以上指定します。--friends-vpn時は証明書の完全な"
            ".ts.net名をちょうど1つ指定します。"
        ),
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="起動後に既定ブラウザで開きます。",
    )
    parser.add_argument(
        "--tls-cert",
        metavar="CERT_PEM",
        help=(
            "HTTPS / WSS用のserver証明書PEMです。--tls-keyと同時に指定します。"
        ),
    )
    parser.add_argument(
        "--tls-key",
        metavar="KEY_PEM",
        help=(
            "HTTPS / WSS用の秘密鍵PEMです。--tls-certと同時に指定します。"
        ),
    )
    parser.add_argument(
        "--internet-public",
        action="store_true",
        help=(
            "予約済みの安全確認flagです。account認証・永続session・本番用"
            "security profileが未実装のため、現在は起動を拒否します。"
        ),
    )
    parser.add_argument(
        "--state-db",
        metavar="SQLITE_PATH",
        help=(
            "対局の再起動復元を明示的に有効化し、権威状態を保存するSQLite "
            "fileを指定します。省略時は従来どおりmemory内だけで動作します。"
        ),
    )
    parser.add_argument(
        "--state-key",
        metavar="KEY_PATH",
        help=(
            "永続状態を認証する秘密鍵fileです。--state-dbと同時に指定します。"
            "省略時はDBと同じ場所に専用鍵を安全に作成します。"
        ),
    )
    parser.add_argument(
        "--rate-limit-db",
        metavar="SQLITE_PATH",
        help=(
            "Webの回数制限を再起動・同一hostのprocess間で共有する専用SQLite "
            "fileです。生の接続元やcookieは保存しません。"
        ),
    )
    parser.add_argument(
        "--rate-limit-key",
        metavar="KEY_PATH",
        help=(
            "共有回数制限の接続元識別子を匿名化する秘密鍵fileです。"
            "--rate-limit-dbと同時に指定します。"
        ),
    )
    parser.add_argument(
        "--replay-db",
        metavar="SQLITE_PATH",
        help=(
            "network対局のreplayを再起動後も復元する専用SQLite fileです。"
            "権威状態と組み合わせるため--state-dbも指定します。"
        ),
    )
    parser.add_argument(
        "--replay-key",
        metavar="KEY_PATH",
        help=(
            "network replayを認証する秘密鍵fileです。--replay-dbと同時に"
            "指定します。省略時はDBと同じ場所に専用鍵を安全に作成します。"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.internet_public:
        parser.error(
            "Internet公開はまだ有効化できません。TLS/WSSを指定しただけでは不十分です。"
            "account認証、永続session、複数host共有rate limitを構成した専用profileが"
            "完成するまでloopbackまたは信頼済みLANで起動してください。"
        )
    if args.lan and args.friends_vpn:
        parser.error("--lanと--friends-vpnは同時に指定できません。")
    if args.allowed_host and not (args.lan or args.friends_vpn):
        parser.error(
            "--allowed-hostは--lanまたは--friends-vpnと一緒に指定してください。"
        )
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("--tls-certと--tls-keyは同時に指定してください。")
    if args.friends_vpn and not args.tls_cert:
        parser.error("--friends-vpnには--tls-certと--tls-keyが必要です。")
    if args.state_key is not None and args.state_db is None:
        parser.error("--state-keyは--state-dbと一緒に指定してください。")
    if args.rate_limit_key is not None and args.rate_limit_db is None:
        parser.error(
            "--rate-limit-keyは--rate-limit-dbと一緒に指定してください。"
        )
    if args.replay_key is not None and args.replay_db is None:
        parser.error("--replay-keyは--replay-dbと一緒に指定してください。")
    if args.replay_db is not None and args.state_db is None:
        parser.error("--replay-dbは--state-dbと一緒に指定してください。")
    if not _storage_paths_are_distinct(args):
        parser.error(
            "対局状態、network replay、共有回数制限のDB・鍵fileには、"
            "それぞれ別のpathを指定してください。"
        )

    state_store: SQLiteRoomAuthorityStore | None = None
    replay_store: SQLiteNetworkReplayStore | None = None
    rate_limit_store: SQLiteSharedRateLimitStore | None = None
    gateway: WebGateway | None = None
    if (
        args.state_db is not None
        or args.replay_db is not None
        or args.rate_limit_db is not None
    ):
        try:
            if args.state_db is not None:
                state_store = SQLiteRoomAuthorityStore(
                    args.state_db,
                    key_path=args.state_key,
                )
            if args.replay_db is not None:
                replay_store = SQLiteNetworkReplayStore(
                    args.replay_db,
                    key_path=args.replay_key,
                )
            if args.rate_limit_db is not None:
                rate_limit_store = SQLiteSharedRateLimitStore(
                    args.rate_limit_db,
                    key_path=args.rate_limit_key,
                )
            controller_kwargs = {}
            if state_store is not None:
                controller_kwargs["state_store"] = state_store
            if replay_store is not None:
                controller_kwargs["replay_store"] = replay_store
            controller = LanServerController(**controller_kwargs)
            gateway_kwargs = {"controller": controller}
            if rate_limit_store is not None:
                gateway_kwargs["shared_rate_limit_store"] = rate_limit_store
            gateway = WebGateway(**gateway_kwargs)
        except Exception:
            _close_rate_limit_store(rate_limit_store)
            _close_replay_store(replay_store)
            _close_state_store(state_store)
            parser.error(
                "永続状態を初期化できませんでした。network replayと共有回数制限を"
                "含む保存先、権限、鍵fileを確認してください。"
            )

    server_kwargs = {
        "lan_mode": args.lan,
        "allowed_hosts": args.allowed_host,
        "tls_certfile": args.tls_cert,
        "tls_keyfile": args.tls_key,
    }
    if args.friends_vpn:
        server_kwargs["friends_vpn_mode"] = True
    if gateway is not None:
        server_kwargs["gateway"] = gateway
    try:
        server = create_web_server(
            args.host,
            args.port,
            **server_kwargs,
        )
    except ValueError as exc:
        _close_rate_limit_store(rate_limit_store)
        _close_replay_store(replay_store)
        _close_state_store(state_store)
        parser.error(str(exc))
    except BaseException:
        _close_rate_limit_store(rate_limit_store)
        _close_replay_store(replay_store)
        _close_state_store(state_store)
        raise
    host, port = server.server_address[:2]
    display_host = (
        args.allowed_host[0]
        if args.lan or args.friends_vpn
        else str(host)
    )
    scheme = "https" if args.tls_cert else "http"
    url = f"{scheme}://{_url_host(display_host)}:{port}/"
    print(f"Catan Web: {url}")
    if args.tls_cert:
        print(
            "HTTPS / WSSを有効化しました。接続端末で証明書の発行元と"
            "接続先hostnameを確認してください。"
        )
    if state_store is not None:
        print("対局状態の再起動復元を有効化しました。")
    if replay_store is not None:
        print("ネットワークリプレイの再起動復元を有効化しました。")
    if rate_limit_store is not None:
        print("Webの共有回数制限を有効化しました。")
    if args.friends_vpn:
        print(
            "友人VPN専用です。Tailscaleのaccess controlと期限付き招待を"
            "併用し、Funnelやrouterのport開放は有効にしないでください。"
        )
    elif args.lan:
        print("信頼できるLAN専用です。Internetへportを公開しないでください。")
    else:
        print("ローカル専用です。Internetへportを公開しないでください。")
    if args.open:
        webbrowser.open(url, new=2)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nCatan Webを終了します。")
    finally:
        try:
            server.server_close()
        finally:
            try:
                _close_rate_limit_store(rate_limit_store)
            finally:
                try:
                    _close_replay_store(replay_store)
                finally:
                    _close_state_store(state_store)
    return 0


def _url_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    return f"[{address.compressed}]" if address.version == 6 else address.compressed


def _storage_paths_are_distinct(args: argparse.Namespace) -> bool:
    paths: list[Path] = []
    for database, key in (
        (args.state_db, args.state_key),
        (args.replay_db, args.replay_key),
        (args.rate_limit_db, args.rate_limit_key),
    ):
        if database is None:
            continue
        database_path = Path(database).expanduser().resolve(strict=False)
        key_path = (
            Path(key).expanduser().resolve(strict=False)
            if key is not None
            else database_path.with_suffix(database_path.suffix + ".key")
        )
        paths.extend((database_path, key_path))
    return len(paths) == len(set(paths))


def _close_state_store(store: SQLiteRoomAuthorityStore | None) -> None:
    if store is None:
        return
    try:
        store.close()
    except Exception:
        # Shutdown and CLI errors must not disclose filesystem, key, SQLite,
        # or authority details originating from the persistence layer.
        print("永続状態を安全に終了できませんでした。", file=sys.stderr)


def _close_rate_limit_store(store: SQLiteSharedRateLimitStore | None) -> None:
    if store is None:
        return
    try:
        store.close()
    except Exception:
        print("共有回数制限を安全に終了できませんでした。", file=sys.stderr)


def _close_replay_store(store: SQLiteNetworkReplayStore | None) -> None:
    if store is None:
        return
    try:
        store.close()
    except Exception:
        print(
            "ネットワークリプレイを安全に終了できませんでした。",
            file=sys.stderr,
        )


if __name__ == "__main__":
    raise SystemExit(main())
