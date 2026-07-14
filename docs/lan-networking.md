# LAN対戦バックエンド

## 現在の実装範囲

LAN対戦は、ローカルゲームと同じルール処理を権威サーバーだけで実行する構成です。

- 2〜4人部屋の作成
- 6文字の参加コード
- player / spectator参加
- 全席ready後にhostだけが開始
- 切断後120秒の席予約
- 256bit以上の再接続tokenによる同じ席への復帰
- 待機室の明示退出では席を即時解放し、hostが不在になれば接続中のplayerへ引き継ぎ
- 対局中のplayerが120秒以内に戻らない場合は、全peerへ理由を通知して部屋を終了
- 対局中にplayerが明示退出した場合は、再接続待ちにせず全peerへ通知して即時終了
- 観戦者を含む閲覧者別の非公開情報マスキング
- semantic command、revision、sequenceによる権限検証と二重実行防止
- command結果と対応snapshotが揃うまで次の操作をロック
- TCPの分割受信・複数frame受信・接続断・安全な終了
- Pythonの盤面生成を再現しなくても描画できる公開board manifest
- 1200×800から1920×1280に対応するPygameロビー表示部品
- serverが配布した合法手だけを操作に変換するPygame対局画面
- snapshotの情報漏れと不正参照を拒否するimmutable client view
- Pygame本体からの部屋作成・参加・観戦・再接続・対局操作

通信・認証・ロビー・ルール実行はUIから独立しています。Pygame本体はこの境界を通じてロビーと対局画面を開き、対局中はserverが配布する `command_options` だけをボタンや盤面の操作候補として表示します。

## 境界

```text
LanServerTransport (TCP frame)
  -> LanServerRuntime (queue / lifecycle)
    -> LanServerController (room / session / exactly-once)
      -> LobbyRoom (ready / reconnect / spectator)
      -> apply_game_command (rules authority)
      -> build_state_snapshot (viewer privacy / board manifest)

state_snapshot
  -> NetworkGameView (strict validation / immutable read model)
    -> LanMatchDisplay (board manifest / authority command options)

lobby_snapshot
  -> LanLobbyFlow (connection / screen state)
    -> LanLobbyDisplay (Pygame lobby)
```

各層の役割:

- `game.lan_transport`: socketとversion付きJSON frameだけを扱います。
- `game.lan_runtime`: socket threadのイベントをcontrollerへ渡します。
- `game.lan_controller`: 部屋と接続sessionを結び、revisionとsequenceを管理します。
- `game.lan_lobby`: socketやPygameに依存しない純粋なロビー状態です。
- `game.network_actions`: 接続から確定した席だけに意味的操作を許可します。
- `game.network_protocol`: 非公開カードを伏せ、公開盤面をstable IDで配信します。
- `game.network_view`: 不信なsnapshotを検証し、描画専用のimmutable viewへ変換します。
- `game.lan_lobby_flow`: 接続worker、host runtime、画面状態をPygameから独立して管理します。
- `game.lan_lobby_display`: socketや権威gameに依存しないロビー表示とhit testを担当します。
- `game.lan_match_display`: board manifestを描画し、serverが許可した操作をstable IDのcommandへ変換します。

この分離により、将来Web版ではTCP層だけをWebSocketへ置き換えられます。

## 最小API例

サーバー:

```python
import threading

from game.lan_runtime import LanServerRuntime

runtime = LanServerRuntime("0.0.0.0", 47624)
stop = threading.Event()
runtime.run_forever(stop)
```

クライアント側は `LanClientSession` を使い、受信処理を定期的に `poll()` します。

```python
from game.lan_runtime import LanClientSession

client = LanClientSession()
client.connect("192.168.1.20", 47624)
client.create_room(
    "Host",
    player_count=3,
    victory_target=10,
    board_mode="constrained",
    board_seed=86712347,
)

# UI loopから定期的に呼ぶ
events = client.poll()
room_code = client.room_code
```

参加・観戦は `join_room(room_code, display_name, spectator=False/True)`、再接続は `reconnect_room(...)`、待機室の退出は `leave_room()`、ゲーム操作は `send_game_command(...)` を使います。Pygame画面から作成する場合も既定のportは `47624` です。

## 安全性

- 参加コードは部屋を見つけるための識別子であり、パスワードではありません。
- 再接続tokenはクライアント本人に一度だけ返し、サーバーはSHA-256 hashだけを保持します。
- token、hash、member ID、transport connection IDは公開ロビーsnapshotへ含めません。
- 操作メッセージにplayer indexを含めず、サーバーが認証済みsessionから席を決定します。
- 観戦者、手番外、別の特殊フェーズ担当者、古いrevision、sequenceの改ざんを拒否します。
- 対局中の秘密VPを含む履歴はsnapshotから除去します。
- ログと対局イベントはネットワークsnapshot内で上限を設けます。
- NaN / Infinity、過大frame、深すぎるargs、巨大文字列、循環参照を拒否します。
- lobby snapshotも型・件数・席・roleを検証し、不正データを描画へ渡しません。

## 信頼モデルと未対応

現在は同じ家庭・社内など、信頼できるLAN専用です。次の機能はまだありません。

- TLS / WSS
- Internet公開用のaccount認証
- NAT越え、relay、public matchmaking
- rate limit、IP ban、監査ログ
- 再起動をまたぐ部屋/token永続化
- UDPによる参加コード自動探索

Internetへ直接port開放しないでください。Web公開時はWSS、Origin検証、認証、rate limit、永続sessionを追加します。

## テスト

```bash
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy PYTHONPATH=python \
  python -m pytest -q \
  tests/test_network_protocol.py \
  tests/test_network_actions.py \
  tests/test_lan_lobby.py \
  tests/test_lan_transport.py \
  tests/test_lan_controller.py \
  tests/test_lan_runtime.py \
  tests/test_lan_lobby_display.py \
  tests/test_lan_lobby_flow.py \
  tests/test_lan_game_integration.py \
  tests/test_lan_match_display.py \
  tests/test_network_view.py
```
