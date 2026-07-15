# ローカルWeb版

## 目的

将来ブラウザで離れた相手と遊ぶためのローカル実装です。ブラウザ側へルールを移植せず、LAN版と同じ `LanServerController` が唯一の権威として部屋・手番・AI・合法手・盤面を管理します。このためPygame版とWeb版でルール判定が分岐しません。

## 起動

```bash
make web
```

既定のURLは `http://127.0.0.1:8765/` です。終了するときは起動したterminalで `Ctrl+C` を押します。

現在の対応範囲:

- 部屋作成、6文字の参加コード、2〜4人、勝利点、盤面mode / seed
- 総人数より1人少ない範囲までのAI席と、標準・混合・拡大重視・交渉重視・妨害重視の性格設定
- ready済みのAI席表示、上限付き1ステップ進行、AI判断コメント
- プレイヤー参加と公開情報だけを受け取る観戦
- ready、host開始、host権限の引き継ぎ
- stable board IDを使う配置操作と、権威サーバーが配る合法手ボタン
- 同梱の生成地形・海面、確率ドット、立体駒、桟橋付き港を使うローカル版準拠の盤面描画（実行時の外部画像取得なし）
- 権威snapshotの実際の2個の出目を使うダイス演出と、stable board ID差分から一度だけ再生する建設演出
- ダイス・建設・交易・勝利の効果音、任意再生のオリジナルBGM、効果音 / BGM個別設定
- プレイヤーごとの資源・発展カード・勝利点カードのマスキング
- revision / sequenceによる古い状態と二重操作の拒否
- ページ再読み込み時の最新状態・対局結果復元
- browser session切断後のreconnect tokenによる席復帰
- 最終順位、VP推移、累積建設、国内・銀行交易、運指数、重要イベントのリザルト
- 最初 / 前 / 再生 / 次 / 最後、スライダー、0.5〜4倍速を備えた対局後リプレイ
- 重要イベントから対応リプレイフレームへの直接移動
- 閲覧者別にマスクした上限付きサーバー履歴と、読み取り専用の過去盤面
- WebSocket優先通信とsame-origin HTTP pollingへの自動フォールバック
- desktop / tablet / mobile幅のレスポンシブ表示

同じoriginのタブはHttpOnly session cookieを共有します。1台で複数席を動作確認するときは、別ブラウザ、別ブラウザプロファイル、または通常ウィンドウとプライベートウィンドウを使います。

## 構成

- `python/game/web_gateway.py`: browser session、event queue、リプレイ要求を権威コントローラーへ接続
- `python/game/websocket_transport.py`: RFC 6455 handshake、JSON frame、Ping / Pong / Closeを依存追加なしで処理
- `python/game/web_server.py`: 標準ライブラリだけで静的ファイル、JSON API、WebSocket endpointを配信
- `python/game/network_replay.py`: revision履歴、閲覧者別snapshot、公開リザルトとイベント位置を保持
- `python/web_main.py`: 起動CLI
- `web/`: HTML / CSS / JavaScriptクライアント

`web/audio.js` はWeb Audio APIで短い効果音とBGMを端末内合成します。外部音源やCDNへの通信はなく、曲と音色はこのプロジェクト用のオリジナルです。効果音は既定ON、BGMは既定OFFで、どちらもブラウザが認めたユーザー操作後にだけAudioContextを開始します。設定は同一originの `localStorage` に保存し、利用できない環境では安全に既定値へ戻します。

盤面画像は `web/assets/board/` に同梱し、Webサーバーは使用する7個のWebPだけを完全一致の許可リストから配信します。任意のファイルパスや素材フォルダー全体を公開しません。

WebGatewayはHTTPやWebSocket固有のオブジェクトをゲーム側へ渡しません。両transportとも同じ `handle` / `poll` / `bootstrap` 境界を使い、部屋・AI・ゲーム操作は同じcontrollerへ到達します。

AIはbrowser内で動きません。権威サーバー自身のservice loopが専用の乱数状態で判断し、既定ではmaintenance tickごとに1ステップだけ適用します。browser timerがbackgroundで遅くなってもAIの権威進行は継続します。成功した判断ごとにrevisionを進めてsnapshotを配信し、例外や不正な更新が起きた場合はゲーム状態と乱数状態をrollbackします。

## API

すべてsame-origin JSONです。session tokenはJavaScriptから読めない `HttpOnly; SameSite=Strict` cookieに保存します。

- `GET /api/health`: processの生存確認
- `POST /api/session`: browser sessionの作成または既存sessionの復元
- `DELETE /api/session`: browser transportの切断
- `GET /api/socket`: 認証済みsessionをsame-origin WebSocketへupgrade
- `GET /api/events`: 自分宛てeventの取得
- `POST /api/message`: LAN protocolと同じversion付きmessageを送信

盤面全体をクライアントから送り返すAPIはありません。ゲーム操作は `command`、権威サーバーから受け取った引数、期待revision、連番だけを送ります。

ブラウザはsession作成後にWebSocketへ接続し、成功中はJSON messageを同じgatewayへ送ります。WebSocketを利用できない環境、upgrade失敗、または切断中は `POST /api/message` と `GET /api/events` を使い続け、裏でupgradeを再試行します。WebSocket frameはbrowserからのmask、UTF-8のJSON object、message上限を検証し、分割data frameやbinary frameを受理しません。

## AI席

`player_count` は人間とAIを合わせた総席数、`ai_player_count` はそのうち最後の席から予約するAI数です。少なくとも人間を1人残すため、AIは `0` から `player_count - 1` 人まで選べます。AI席は接続やready操作を必要とせず、ロビー上では `CPU1` から順に表示されます。人間は残りの先頭席へ参加します。

性格mode:

- `standard`: 標準
- `mixed`: AIごとに拡大重視、交渉重視、妨害重視を順番に割り当て
- `expansion`: 拡大重視
- `trader`: 交渉重視
- `disruptor`: 妨害重視

## リザルトとネットワークリプレイ

権威サーバーは開始時と、受理した人間操作・成功したAI判断の各revisionを対局履歴へ記録します。既定の履歴上限は部屋あたり512フレームです。上限を超えた長期戦では保持中の先頭revisionをリザルトに明示し、序盤が保存範囲外であることをUIに表示します。保存対象は各player席用とspectator用に分け、取得するvariantはbrowserが指定せず、認証済みcontroller sessionの席から決定します。

リプレイsnapshotでは操作候補を空にして読み取り専用にし、累積ログと重複メトリクスを圧縮します。他playerの資源、発展カード、勝利点カード、提示前の交易条件、未配布の発展カード順は保存境界でも検査します。spectator用は全playerの非公開内訳を伏せます。

終了時は既存の公開match resultから、順位、VP推移、建設・交易数、運指数、重要イベントを配信します。重要イベントとVP checkpointは最初に観測したrevisionへ結び付けます。履歴上限で該当revisionが既に消えている場合は、別のフレームへ誤って移動せずリンクなしとして表示します。

この履歴はprocess memory内の対局用データです。ローカルPygame版が保存する全状態JSONリプレイとは別物で、部屋の終了・削除またはserver再起動をまたぐ永続アーカイブではありません。リザルトJSON自体の形式は [`match-result.md`](match-result.md) を参照してください。

## 安全上の境界

このローカル版は `localhost` / loopback IP以外へのbindを拒否します。さらにHost / Origin / Fetch Metadataを確認し、CSP、frame拒否、MIME sniffing拒否を付与します。静的ファイルは固定routeだけを配り、任意pathをファイルとして開きません。

これはInternet公開用のsecurity modelではありません。実装済みのWebSocketも `ws://127.0.0.1` 用で、次の段階で少なくとも以下が必要です。

- reverse proxyでのTLS / WSS終端
- account認証とroom参加権限
- session / roomの永続化とserver再起動後の復帰
- IP・account・room単位のrate limitと監査ログ
- CSRF token、Origin allowlist、秘密情報管理の本番構成
- deploy先での負荷試験、依存監査、運用監視

## 次の改善候補

1. ブラウザへカスタムマップとハウスルール設定を追加
2. room / session / replay storeをprocess外へ分離し、server再起動後も復帰可能にする
3. LAN内の別端末へ段階的に公開し、接続探索と運用制限を追加
4. 認証・TLS / WSS・rate limitを整えた後にInternet向けstagingを構築
