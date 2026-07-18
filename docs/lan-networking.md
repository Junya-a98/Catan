# LAN対戦バックエンド

## 現在の実装範囲

LAN対戦は、ローカルゲームと同じルール処理を権威サーバーだけで実行する構成です。

- 2〜4人部屋の作成
- 6文字の参加コード
- 人間を最低1人残した0〜総人数-1人のAI席と、AI性格mode
- AI席のready済みロビー表示と、判断ごとの上限付きrevision配信
- player / spectator参加
- 全席ready後にhostだけが開始
- 切断後120秒の席予約
- 256bit以上の再接続tokenによる同じ席への復帰
- 待機室の明示退出では席を即時解放し、hostが不在になれば接続中のplayerへ引き継ぎ
- 対局中のplayerが120秒以内に戻らない場合は、全peerへ理由を通知して部屋を終了
- 対局中にplayerが明示退出した場合は、再接続待ちにせず全peerへ通知して即時終了
- 観戦者を含む閲覧者別の非公開情報マスキング
- 閲覧席ごとの上限付きrevision履歴、公開リザルト、重要イベントからのリプレイ位置
- semantic command、revision、sequenceによる権限検証と二重実行防止
- command結果と対応snapshotが揃うまで次の操作をロック
- TCPの分割受信・複数frame受信・接続断・安全な終了
- Pythonの盤面生成を再現しなくても描画できる公開board manifest
- カスタムマップ・ハウスルールdocumentのroom境界検証と盤面fingerprint照合
- 1200×800から1920×1280に対応するPygameロビー表示部品
- serverが配布した合法手だけを操作に変換するPygame対局画面
- snapshotの情報漏れと不正参照を拒否するimmutable client view
- Pygame本体からの部屋作成・参加・観戦・再接続・対局操作
- loopbackブラウザからのWebSocket優先接続とHTTP polling fallback
- `standard` / `forecast_events` / `frontier` のroom設定と、予告済みのイベント・対象・有効効果だけを含む公開snapshot

通信・認証・ロビー・ルール実行はUIから独立しています。Pygame本体はこの境界を通じてロビーと対局画面を開き、対局中はserverが配布する `command_options` だけをボタンや盤面の操作候補として表示します。ローカルWeb版も同じcontrollerを利用し、browser側ではルールやAIを実行しません。

## 境界

```text
LanServerTransport (TCP frame)
  -> LanServerRuntime (queue / lifecycle)
    -> LanServerController (room / session / exactly-once / AI tick)

Browser
  -> WebSocket / same-origin JSON API
    -> WebGateway
      -> LanServerController

LanServerController
  -> LobbyRoom (ready / reconnect / spectator / AI seats)
  -> apply_game_command / SimpleAI (rules authority)
  -> build_state_snapshot (viewer privacy / board manifest)
  -> NetworkReplayStore (bounded viewer snapshots / public result)

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
- `game.network_replay`: revisionを上限付きで保持し、認証済み席ごとのread-only replayと公開resultを返します。
- `game.lan_lobby_flow`: 接続worker、host runtime、画面状態をPygameから独立して管理します。
- `game.lan_lobby_display`: socketや権威gameに依存しないロビー表示とhit testを担当します。
- `game.lan_match_display`: board manifestを描画し、serverが許可した操作をstable IDのcommandへ変換します。
- `game.web_gateway`: HttpOnly cookieのbrowser sessionをcontroller接続へ対応付け、WebSocket / HTTP共通のevent queueを管理します。
- `game.websocket_transport`: WebSocket handshake、mask済みJSON frame、Ping / Pong / Closeを検証します。
- `game.web_server`: loopback限定で静的Web UI、JSON API、WebSocket endpointを配信します。

この分離により、TCP LAN clientとローカルWeb clientが同じ部屋・session・合法手・非公開情報境界を利用します。

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

カスタム対局では `board_mode="custom"` と完全な `CustomMapSpec`（またはそのJSON document）を `custom_map` に渡します。`house_rules` には検証済み `HouseRules` または完全なJSON documentを指定できます。従来どおり4項目だけなら標準ハウスルールとして扱われます。権威サーバーは受信documentを再検証し、生成盤面へのcustom map混在、custom modeでのmap欠落、fingerprint不一致を拒否します。形式の詳細は [`custom-settings.md`](custom-settings.md) を参照してください。

controllerのroom settingsは `ai_player_count` と `ai_personality_mode` も受け取ります。AI席は総席数の末尾へ予約され、接続やreadyを必要としません。`mixed` はAI席ごとに `expansion`、`trader`、`disruptor` を順番に割り当てます。現在この設定を選ぶ画面はローカルWeb版にあります。

参加・観戦は `join_room(room_code, display_name, spectator=False/True)`、再接続は `reconnect_room(...)`、待機室の退出は `leave_room()`、ゲーム操作は `send_game_command(...)` を使います。Pygame画面から作成する場合も既定のportは `47624` です。

## 安全性

- 参加コードは部屋を見つけるための識別子であり、パスワードではありません。
- 再接続tokenはクライアント本人に一度だけ返し、サーバーはSHA-256 hashだけを保持します。
- token、hash、member ID、transport connection IDは公開ロビーsnapshotへ含めません。
- 操作メッセージにplayer indexを含めず、サーバーが認証済みsessionから席を決定します。
- replay要求にもplayer indexを含めず、サーバーが同じ認証済みsessionから閲覧variantを決定します。
- 観戦者、手番外、別の特殊フェーズ担当者、古いrevision、sequenceの改ざんを拒否します。
- 対局中の秘密VPを含む履歴はsnapshotから除去します。
- ログと対局イベントはネットワークsnapshot内で上限を設けます。
- NaN / Infinity、過大frame、深すぎるargs、巨大文字列、循環参照を拒否します。
- lobby snapshotも型・件数・席・roleを検証し、不正データを描画へ渡しません。
- replayはread-onlyでcommand optionを除去し、他playerの手札、提示前の交易条件、発展カード山札順を保存時にも検査します。
- バリアントの完全な実行時状態は権威サーバーだけが保持し、live snapshot・観戦・replayには公開領域だけを投影します。`private` キーの混入は保存時にも拒否します。
- 予告イベントの未来のdeck seedと未公開順は権威サーバーの完全保存だけに残し、clientとAIへは予告済みイベントと対象parameterだけを渡します。`core_v1` と `core_v2` の形式を混ぜず、イベントIDとparameterの組み合わせを受信・復元時に検証します。
- AIはroom専用の乱数状態で1ステップずつ実行し、失敗時はゲーム状態と乱数状態をrollbackします。

Web adapterはこれらに加えてHost / Origin / Fetch Metadata、HttpOnly SameSite cookie、CSP、WebSocket browser mask、JSON object、message sizeを検証します。Web serverは非loopback addressへのbindを拒否します。

## 信頼モデルと未対応

TCP serverは同じ家庭・社内など、信頼できるLAN専用です。Web serverは同一端末のloopback専用です。次の機能はまだありません。

- TLS / WSS
- Internet公開用のaccount認証
- NAT越え、relay、public matchmaking
- rate limit、IP ban、監査ログ
- 再起動をまたぐ部屋/token永続化
- UDPによる参加コード自動探索

Internetへ直接port開放しないでください。Web adapterにはsame-origin検証がありますが、Internet公開時はTLS / WSS、account認証、rate limit、永続sessionを追加します。

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
  tests/test_network_view.py \
  tests/test_network_replay.py \
  tests/test_web_gateway.py \
  tests/test_websocket_transport.py \
  tests/test_web_server.py
```
