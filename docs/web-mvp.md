# ローカルWeb版MVP

## 目的

将来ブラウザで離れた相手と遊ぶための最初の縦切り実装です。ブラウザ側へルールを移植せず、LAN版と同じ `LanServerController` が唯一の権威として部屋・手番・合法手・盤面を管理します。このためPygame版とWeb版でルール判定が分岐しません。

## 起動

```bash
make web
```

既定のURLは `http://127.0.0.1:8765/` です。終了するときは起動したterminalで `Ctrl+C` を押します。

現在の対応範囲:

- 部屋作成、6文字の参加コード、2〜4人、勝利点、盤面mode / seed
- プレイヤー参加と公開情報だけを受け取る観戦
- ready、host開始、host権限の引き継ぎ
- stable board IDを使う配置操作と、権威サーバーが配る合法手ボタン
- プレイヤーごとの資源・発展カード・勝利点カードのマスキング
- revision / sequenceによる古い状態と二重操作の拒否
- ページ再読み込み時の最新状態復元
- browser session切断後のreconnect tokenによる席復帰
- desktop / tablet / mobile幅のレスポンシブ表示

同じoriginのタブはHttpOnly session cookieを共有します。1台で複数席を動作確認するときは、別ブラウザ、別ブラウザプロファイル、または通常ウィンドウとプライベートウィンドウを使います。

## 構成

- `python/game/web_gateway.py`: browser sessionとpolling event queueを権威コントローラーへ接続
- `python/game/web_server.py`: 標準ライブラリだけで静的ファイルとJSON APIを配信
- `python/web_main.py`: 起動CLI
- `web/`: HTML / CSS / JavaScriptクライアント

WebGatewayはHTTP固有のオブジェクトをゲーム側へ渡しません。将来WebSocketへ移行するときも、同じ `handle` / `poll` / `bootstrap` 境界を利用できます。

## API

すべてsame-origin JSONです。session tokenはJavaScriptから読めない `HttpOnly; SameSite=Strict` cookieに保存します。

- `GET /api/health`: processの生存確認
- `POST /api/session`: browser sessionの作成または既存sessionの復元
- `DELETE /api/session`: browser transportの切断
- `GET /api/events`: 自分宛てeventの取得
- `POST /api/message`: LAN protocolと同じversion付きmessageを送信

盤面全体をクライアントから送り返すAPIはありません。ゲーム操作は `command`、権威サーバーから受け取った引数、期待revision、連番だけを送ります。

## 安全上の境界

このMVPは `localhost` / loopback IP以外へのbindを拒否します。さらにHost / Origin / Fetch Metadataを確認し、CSP、frame拒否、MIME sniffing拒否を付与します。静的ファイルは固定routeだけを配り、任意pathをファイルとして開きません。

これはInternet公開用のsecurity modelではありません。次の段階で少なくとも以下が必要です。

- reverse proxyでのTLSと安全なWebSocket
- account認証とroom参加権限
- session / roomの永続化とserver再起動後の復帰
- IP・account・room単位のrate limitと監査ログ
- CSRF token、Origin allowlist、秘密情報管理の本番構成
- deploy先での負荷試験、依存監査、運用監視

## 次の改善候補

1. pollingをWebSocketへ置き換えてAI演出と状態更新を低遅延化
2. ブラウザへリザルト、リプレイ、カスタムマップ、ハウスルール設定を追加
3. room / session storeをprocess外へ分離し、LAN内の別端末へ段階的に公開
4. 認証・TLS・運用制限を整えた後にInternet向けstagingを構築
