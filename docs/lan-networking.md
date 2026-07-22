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
- loopbackと明示的な信頼済みLAN modeのブラウザからのWebSocket優先接続、HTTP polling fallback
- `standard` / `forecast_events` / 19・37タイル版 `frontier` のroom設定と、予告済みのイベント・対象・有効効果だけを含む公開snapshot

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
- `game.web_server`: 既定のloopback、または明示した信頼済みLANだけへ静的Web UI、JSON API、WebSocket endpointを配信します。

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

## Web版を同一LANへ開く

Web版は何も指定しなければ従来どおり `127.0.0.1` だけで待ち受けます。同じ信頼済みLANの別端末から開く場合に限り、LAN modeと、端末がURLに使う実際のIPまたはhostnameを明示します。

```bash
PYTHONPATH=python python python/web_main.py \
  --host 0.0.0.0 \
  --lan \
  --allowed-host 192.168.1.20
```

別端末では `http://192.168.1.20:8765/` を開きます。mDNS名も使う場合は `--allowed-host catan-box.local` を追加できます。値はscheme・port・pathを含まないRFC1918 IPv4、link-local、IPv6 ULA、またはcanonicalな `.local` 名だけを受理し、複数指定は同じoptionを繰り返します。公開IP、通常DNS名、CGNAT/文書用アドレスは拒否します。wildcard bindの `0.0.0.0` 自体はアクセス先として許可せず、少なくとも1つの非loopback access hostが必須です。HostとOriginを許可host、同一hostname、実際のlisten portまで照合し、TCP接続元自体もloopback/private/link-local/ULAの範囲に制限します。

信頼済みLAN内でHTTPS / WSSを使う場合は、接続先hostnameをSANに含み、各端末が信頼するserver証明書と秘密鍵をペアで指定します。

```bash
PYTHONPATH=python python python/web_main.py \
  --host 0.0.0.0 \
  --lan \
  --allowed-host catan-box.local \
  --tls-cert /安全な場所/server-cert.pem \
  --tls-key /安全な場所/server-key.pem
```

この構成では `https://catan-box.local:8765/`、same-origin `wss://`、`Secure` session cookieを使います。秘密鍵はrepositoryへ保存せず、証明書警告を無視しないでください。TLSの指定はLANの信頼境界をInternetへ広げません。

離れた友人とは、LAN modeをInternetへ開かず、Tailscale限定の `--friends-vpn` profileを使います。ホストのTailscale IPへだけbindし、Tailscale device範囲のTCP peer、証明書と一致する完全修飾 `.ts.net` Host / Origin、HTTPS / WSS、期限付き一回招待をすべて強制します。Tailscale側でも対象の友人とportだけを許可し、Funnelやport forwardingは使いません。詳細は [`friends-vpn.md`](friends-vpn.md) を参照してください。

## 再起動復元（明示opt-in）

Web serverは既定ではメモリ内だけで動作します。待機室と進行中の対局をserver再起動後に復元する場合だけ、privateな保存先を指定します。

```bash
PYTHONPATH=python python python/web_main.py \
  --state-db saves/web-room-authority.sqlite3 \
  --replay-db saves/web-network-replay.sqlite3 \
  --rate-limit-db saves/web-rate-limits.sqlite3
```

任意の `--state-key /安全な場所/authority.key` も指定できます。省略時はDBの隣に32-byteの認証鍵を作成します。SQLiteはWAL / `synchronous=FULL`、roomごとのgeneration CAS、HMAC-SHA256を使い、ロビー、開始済みgame、room RNG、revision、直近128件のcommand結果を同じrowで確定します。保存に失敗した操作を成功扱いせず、結果不明のcommit後にメモリ運用へfallbackしません。再起動時は古いtransport IDを捨て、browserがHttpOnly復帰cookieとして保持する再接続credentialで同じmember・席・command sequenceへ戻します。

DBには手札、発展カード、未公開バリアント状態、入室credential digestが含まれます。HMACは暗号化ではないため、DBと鍵の両方をWeb static root、共有folder、repositoryへ置かないでください。fileはowner-only権限へ制限され、鍵不一致、改ざん、schema不一致では起動を拒否します。再起動時に接続中だったmemberには1回だけ120秒の復帰猶予を付け、再起動を繰り返して期限を延長しません。通常のbrowser transport sessionはprocess memoryのままですが、別のHttpOnly部屋復帰cookieとauthority内token hashにより席を取り戻せます。

`--replay-db` は `--state-db` と同時に指定した場合だけ、認証済み席別・観戦者用network replayを別のowner-only SQLiteへ保存します。各revisionは独立したimmutable row、link mapと公開リザルトは小さなroom metadata rowとして保存し、それぞれを専用鍵でHMAC認証します。任意の `--replay-key` を省略した場合はDBの隣へ専用鍵を作成します。既定上限は512フレーム/部屋、64部屋、128 MiB、7日で、短い参加コードではなくauthorityの安定room IDに結び付けます。

ゲームauthorityのcommitを先に行い、replay write失敗ではゲーム操作をrollbackしません。欠落revisionは次の成功または再起動時に `truncated` と再開境界を記録します。replay revisionがゲームauthorityより先なら起動をfail closedにします。同じrevisionのsnapshotが現在のauthorityと一致しない、または待機室に過去frameがある場合は、そのroomのreplayを読み取り不可のまま固定し、後続revisionを古い履歴へ追加しません。閲覧時にも現在の安定room IDを再確認し、再利用された参加コードから古い対局履歴を読めないようにします。これは現在のroom membershipで認可する同一host用archiveであり、account所有の長期保存・公開共有・複数host共同書き込みではありません。

ロビーauthority v2は、memberごとのcurrent token hashに加えて、Web復帰中だけprevious token hashと絶対失効時刻を保存します。従来のv1 documentも読み込み、previousなしの状態として復元し、次の保存時にv2へ移行します。不完全なprevious field、currentとのhash重複、保存時刻以前の失効時刻はfail closedで拒否します。これにより復帰response消失後にserverが再起動しても、元の最大120秒の期限内だけ旧cookieから再試行できます。

`--rate-limit-db` は別のowner-only SQLiteを使い、Web session作成、操作、heartbeat、room参加、入室保護試行のrolling windowを再起動後と同一hostの複数processで共有します。任意の `--rate-limit-key` を省略した場合は専用鍵をDBの隣に作成します。生のIP、cookie、reconnect token、部屋コードは保存せず、scopeとHMAC-SHA256識別子だけを記録します。1操作が複数上限に関係するときは同じtransactionで判定し、DB障害時はprocess内制限へfallbackせずcontroller操作前に拒否します。対局・replay・制限の各DBと各鍵に同じpathは指定できません。

新規Web roomは「期限付き招待リンクのみ」が既定です。hostは待機室でplayer用またはspectator用を選び、相手ごとに1時間・1回限りのURL（`https://catan-box.local:8765/?room=ABC123#invite=...`）を発行します。roleはURL parameterではなくserver authorityへ固定し、受け手がroleを改ざんしても変更できません。browserはsession作成前にfragmentをURLから消去し、raw bearerをDOM、storage、event、replayへ残しません。hostは未使用招待のroleと期限だけを一覧し、誤送信したリンクを個別または一括で取り消せます。発行・一覧・取消・claimはloopbackまたは検証可能なHTTPS / WSSだけで許可します。

authorityへ保存するのはSHA-256 token digest、再利用されないroom instance ID、role、発行・失効時刻だけです。claimでは消費せずserver-side browser sessionへ一時保持し、member追加とauthority commitに成功した時だけ一度消費します。同時利用では一人だけが成功します。招待はroomごとに最大32件で、期限切れをpruneし、使用済み・期限切れ・取消済み・改ざん・別roomは同じ認証失敗として返します。管理一覧へはbearerでない128-bit ID、role、発行・失効時刻だけを返します。server restart後も未使用digestは復元しますが、claimからjoinまでの短い間にrestartした場合は、secretをbrowserへ永続化しない設計上、新しいリンクが必要です。

6文字codeだけのopen roomも明示的に選択できます。この場合は `/?room=ABC123` がフォーム入力を補助しますが、自動参加せず、認証情報にもなりません。`room` はASCII英数字6文字だけを受け付け、小文字は大文字へ正規化し、不正値はURLから除去します。

任意の入室保護を使う場合は15〜64文字のパスフレーズを設定し、招待リンクとは別の安全な経路で参加者へ伝えます。パスフレーズはNFC正規化後にsalt付きPBKDF2-HMAC-SHA256（60万回）で派生し、権威サーバーは平文を保持しません。公開ロビーが返すのは `access.passphrase_required` の真偽だけです。入力値はbrowser storageや再接続情報へ保存せず、再接続は専用tokenだけで行います。保護付き部屋の作成・参加は同一端末のloopback、またはHTTPS / WSSでのみ許可されるため、別端末から使う場合は上記TLS構成が必須です。

起動時にも表示する通り、これは信頼できる家庭内・社内LAN専用です。routerのport forwarding、公開reverse proxy、cloud VMなどを使ってInternetへportを公開しないでください。LAN modeは既定でHTTP / `ws://`、cert/key指定時はHTTPS / `wss://` ですが、Internet用のaccount認証や永続sessionは提供しません。

## 安全性

- 参加コードは部屋を見つけるための識別子であり、パスワードではありません。
- `#invite=` を持たないcode-only URLは入力補助だけです。期限付き招待URLのfragmentは一回限りの認証bearerなので、各友人へ別々に送り、SNSや公開場所へ掲載しません。role、部屋パスフレーズ、再接続token、session情報はURLへ含めません。
- 送信先を間違えた場合は待機室の「未使用の招待」から直ちに取り消します。全員が入室した後は、残っている未使用招待を一括で取り消します。
- 招待bearerの平文は発行response以外へ永続化せず、authorityにはdigest、room instance、role、有効期限だけを保存します。member追加と同じ永続transactionに成功したときだけ消費します。
- 入室パスフレーズのsalt、派生値、KDF parameterはauthority内だけに置き、公開snapshotには保護の有無だけを含めます。認証処理には接続元・全体双方の試行回数上限を適用します。
- LAN clientには安定した再接続tokenを本人へ一度だけ返し、LANの再接続ではcurrent tokenだけを受理してローテーションしません。Webではserverが直接host-only HttpOnly cookieへ設定してJSON / WebSocket / browser storageへ出さず、復帰ごとに新tokenへローテーションします。新cookieの受領確認までは提示された旧tokenを最大120秒だけ併存させ、`POST /api/resume/confirm` 後に失効します。authorityはtokenのSHA-256 hashだけを保持します。
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

Web adapterはこれらに加えてHost authority（listen portを含む）/ Origin / Fetch Metadata、TCP接続元のprofile別範囲、2種類のHttpOnly SameSite cookie、CSP、WebSocket browser mask、JSON object、message sizeを検証します。browser sessionはサーバーが観測したTCP接続元へ結び付け、同じsessionでは最新のWebSocket 1本だけを有効にします。HTTPとWebSocketで共通のmessage上限、接続元単位のsession作成・room参加試行上限、PBKDF2実行前の入室保護専用上限を適用し、再接続tokenは全JSON / WebSocket / bootstrapとbrowser storageから除外します。部屋作成・参加・退出とWeb復帰・復帰確認はcookieを確実に更新・確認できるHTTPへ固定します。復帰responseを受け取れなかった同じbrowser sessionにはserverが新cookieとbootstrapを再送し、再起動後はauthority v2のprevious token猶予を使います。既定はprocess内、明示したSQLite backendでは再起動・同一host process間で共有します。Web serverは既定では非loopback bindを拒否し、明示的な `--lan` とprivate/link-local/ULAまたは `.local` に限定した `--allowed-host` が揃ったときだけ信頼済みLANへbindします。`--friends-vpn` ではwildcardを認めずTailscale IPへ直接bindし、TLSと `.ts.net` Host 1つを要求し、code/passphrase直接参加をserver側で拒否します。公開用flagを指定してもInternet向け安全要件が揃うまでは起動しません。

## 信頼モデルと未対応

TCP serverと `--lan` Web serverは同じ家庭・社内など、信頼できるLAN専用です。Web serverの既定値は同一端末のloopback専用で、WebのTLS / WSSはcert/keyを明示したときだけ有効です。次の機能はまだありません。

- Internet運用向けの証明書自動更新とTLS終端
- Internet公開用のaccount認証
- NAT越え、relay、public matchmaking
- 複数host共有のaccount/IP/room rate limit、IP ban、監査ログ
- browser account session、account所有の長期replay archiveと共有権限
- UDPによる参加コード自動探索

Internetへ直接port開放しないでください。Web adapterにはoptional TLS / WSS、same-origin検証、同一host共有可能な基本rate limitがありますが、Internet公開時はaccount認証、証明書自動更新、複数host backend、永続sessionを追加します。`--internet-public` は現在これらが未実装であることを表示して起動を拒否します。

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
