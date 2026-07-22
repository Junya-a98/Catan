# ローカル / LAN Web版

## 目的

将来ブラウザで離れた相手と遊ぶためのローカル実装です。ブラウザ側へルールを移植せず、LAN版と同じ `LanServerController` が唯一の権威として部屋・手番・AI・合法手・盤面を管理します。このためPygame版とWeb版でルール判定が分岐しません。Pygame版を維持しながら、今後の画面、演出、アクセシビリティ、バリアント表示はWeb版を主導UIとして改善します。

## 起動

```bash
make web
```

既定のURLは `http://127.0.0.1:8765/` です。終了するときは起動したterminalで `Ctrl+C` を押します。

信頼できる同一LANの別端末から試す場合は、LAN modeと、その端末がアクセスに使う実際のIP/hostnameを明示します。

```bash
PYTHONPATH=python python python/web_main.py \
  --host 0.0.0.0 \
  --lan \
  --allowed-host 192.168.1.20
```

この例では別端末から `http://192.168.1.20:8765/` を開きます。`--allowed-host` はschemeやportを含めず、RFC1918 IPv4、link-local、IPv6 ULA、またはcanonicalな `.local` 名で指定し、複数ある場合はoptionを繰り返します。公開IP、通常の公開DNS名、CGNAT/文書用アドレスは拒否します。さらに実際のTCP接続元もloopback/private/link-local/ULAか検査します。これは信頼できるLAN専用であり、Internetへのport公開には使えません。

信頼済みLAN内の通信も暗号化する場合は、接続先hostnameをSubject Alternative Nameに含み、各接続端末が発行元を信頼しているserver証明書と秘密鍵を同時に指定します。

```bash
PYTHONPATH=python python python/web_main.py \
  --host 0.0.0.0 \
  --lan \
  --allowed-host catan-box.local \
  --tls-cert /安全な場所/server-cert.pem \
  --tls-key /安全な場所/server-key.pem
```

この場合は `https://catan-box.local:8765/` とsame-origin `wss://` を使い、session cookieにも `Secure` を付けます。cert/keyは必ずペアで指定し、秘密鍵をrepositoryへ置かないでください。証明書のhostname不一致や未信頼警告を無視して接続する運用は対象外です。TLSを指定してもbind・Host・Origin・rate limitの境界は緩まず、`--internet-public` は引き続き起動を拒否します。

離れた友人とのalpha対戦には、一般公開を有効にせずTailscale内だけで待ち受ける `--friends-vpn` profileがあります。TLS、ホスト自身のTailscale IPへの直接bind、証明書と一致する完全修飾 `.ts.net` 名1つ、Tailscale範囲のTCP peerをすべて必須にし、server側で招待専用部屋と招待参加だけを許可します。通常のLAN modeとは別のfail-closed境界です。証明書発行、Tailscale access control、起動commandは [`friends-vpn.md`](friends-vpn.md) を参照してください。

現在の対応範囲:

- 部屋作成、6文字の参加コード、2〜4人、勝利点、盤面mode / seed、`standard` / `forecast_events` / `trade2` / `credit` / 固定複合 `composite` / 全部入り `grand_campaign_v1` / 19・37タイル版 `frontier` のバリアント選択
- 既定の招待専用部屋、hostだけが発行できるplayer / spectator別の1時間・1回限り招待
- 任意の15〜64文字の入室パスフレーズ、参加・観戦用認証ポップアップ、再接続tokenによる再入力不要の復帰
- 参加コードだけを使う従来のopen roomと、URL queryによる参加フォームへのコード入力補助（自動参加なし）
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
- TCP接続元に結び付けたbrowser session、1 sessionにつき同時に1本だけの有効WebSocket
- session作成・message・部屋作成/参加/再接続試行のprocess内rate limit
- 最終順位、VP推移、累積建設、国内・銀行交易、運指数、重要イベントのリザルト
- 最初 / 前 / 再生 / 次 / 最後、スライダー、0.5〜4倍速を備えた対局後リプレイ
- 重要イベントから対応リプレイフレームへの直接移動
- 閲覧者別にマスクした上限付きサーバー履歴と、読み取り専用の過去盤面
- WebSocket優先通信とsame-origin HTTP pollingへの自動フォールバック
- desktop / tablet / mobile幅のレスポンシブ表示

予告イベントmodeの新規対局は `core_v2` を使います。次のイベント名、対象、発動までの残り手番、効果時間、現在有効な効果を専用カードへ表示し、mobile幅では盤面の直前に要点だけの予告帯を表示します。封鎖された交換所は鍵付き表示にし、地震対象の街道区画は亀裂を重ね、影響する盤面位置を文章だけに頼らず示します。

`core_v2` は次の7種類です。豊作は次の麦通常生産へ条件付きで1枚追加し、大干ばつは1ラウンド羊生産を止めます。港湾封鎖は予告した交換所を2手番使用不能にし、建設ブームは次の有料街道1本の木または土1枚を免除します。商人祭は1ラウンド中に成立した国内交易の双方へ銀行からランダム資源を1枚ずつ支給し、双方分の在庫がない場合は追加配布しません。山賊襲来は予告数字の高生産タイルへ盗賊を即時移動しますが、捨て札と略奪は行いません。地震は予告方角で街道建設を止め、既存街道を1ラウンドだけ接続と最長交易路へ使えなくしますが、コマは失いません。正確なcatalog境界は [`variants-roadmap.md`](variants-roadmap.md) を参照してください。

既存の `core_v1` セーブ・リプレイは「豊作」「大干ばつ」だけの従来形式として読み込み、`core_v2` へ暗黙変換しません。未来のイベント順はbrowserへ送らず、権威サーバーが予告済みイベントと対象だけを公開します。catalog、対象、進行位置は完全セーブ、部屋の再接続、閲覧者別リプレイで検証・復元し、予告・発動・終了から該当リプレイ位置へ移動できます。

フロンティア探索は従来互換の標準19タイル版と `outer_ring_37_v1` の拡張版を選べます。拡張版は37タイル、96頂点、132辺、12港のstable IDを開始時に確定し、中央7タイルだけを公開します。外周30タイルの資源・数字・港・盗賊と秘密Seedはlive snapshot、観戦、再接続、ネットワークリプレイでマスクします。

同じoriginのタブはHttpOnly session cookieを共有します。1台で複数席を動作確認するときは、別ブラウザ、別ブラウザプロファイル、または通常ウィンドウとプライベートウィンドウを使います。

## 招待リンク

新規部屋の既定は「期限付き招待リンクのみ」です。hostは待機室からplayer用またはspectator用のリンクを発行します。リンクは `/?room=ABC123#invite=...` 形式で、現在のorigin、公開の6文字room code、256-bitのopaque bearerだけを含みます。role、表示名、room passphrase、reconnect token、browser sessionはURLへ含めず、roleは発行時にserver authorityへ固定します。リンクは既定1時間、1回限りで、相手ごとに新しく発行します。

browserは初期化の最初にfragmentを厳格検証し、session開始やnetwork requestより前に `history.replaceState` でURLから消去します。その後same-origin claim endpointで生の招待bearerを別の256-bit claim credentialへ交換し、claimはJavaScriptから読めないhost-only `HttpOnly; SameSite=Strict; Path=/api` cookieへ、公開可能なroom code・role・期限だけを画面へ返します。TLS時はcookieへ `Secure` を付け、`Max-Age` は元の招待期限を越えません。本人が表示名を確認するまで自動参加しません。raw bearerとclaim credentialはDOM、toast、`localStorage`、`sessionStorage`、公開event、replayへ出しません。発行後はClipboard APIを先に試し、失敗時だけOSのWeb Shareへ切り替えます。共有が失敗またはキャンセルされた場合は、その場で管理IDにより招待を取り消し、secret linkを画面へ表示しません。

server authorityが保存するのは、元の招待tokenとclaim credentialを用途分離してSHA-256化したdigest、再利用されないroom instance ID、`player` / `spectator` scope、発行・失効時刻だけです。1招待あたり保留claimは最大8件で、いずれも元の招待期限を共有します。claimは招待を消費せず、room membershipと永続authorityのcommitに成功した時だけ招待と兄弟claimをatomicに消費します。複数browserが同時参加しても一人だけが成功し、hostによる取消では関連claimもすべて失効します。使用済み、期限切れ、取り消し済み、改ざん、room code再利用、別room、存在しないroomは同じ認証失敗として扱います。招待は最大32件/roomに制限し、期限切れを削除します。ホスト用一覧と取消にはdigest先頭128 bitから導出した非秘密の管理IDを使い、raw token、claim credential、完全digest、room instance IDは返しません。個別・一括取消はauthority更新と同じtransactionで確定します。従来の招待authorityはclaimなしとして読み込み、次の保存時に新形式へ移行します。

`--state-db` を指定したserverではclaim digestも同じauthorityへ保存するため、claim後・join前にprocessが再起動しても `POST /api/invitations/resume` と同じcookieから公開metadataを復元できます。「この招待を使わない」は `DELETE /api/invitations/claim` で自分のclaimだけを解放し、元の招待はhostが取り消すか誰かが使用するまで維持します。参加成功、明確な認証失敗、失効、明示破棄ではclaim cookieを消し、rate limitや一時的な保存障害では再試行用に維持します。同じbrowser profileのタブはcookieを共有するため、保留できる招待は実質1件です。

従来の `/?room=ABC123` は秘密を含まない参加コード入力補助として維持します。参加コードだけでは認証にならず、open roomまたは別途共有したpassphraseで利用します。不正な `room` queryはURLから除去します。

入室保護は作成時だけ任意に有効化します。パスフレーズはNFC正規化し、ランダムsalt付きPBKDF2-HMAC-SHA256（60万回）としてauthority内に保持します。公開ロビーが示すのは `access.passphrase_required` の真偽だけです。参加・観戦では、まず通常の参加要求を送り、保護された部屋から汎用の認証失敗が返った場合だけ専用ポップアップを開きます。入力値は送信直後にinput、FormData由来messageから消去し、URL、`localStorage`、`sessionStorage`、toast、公開eventへ入れません。再接続は専用tokenだけを使います。

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

予告イベントmodeのAIは、公開中の次回イベント、対象、残り手番、現在有効な効果だけを評価します。豊作・大干ばつによる資源価値、建設ブームの実効街道費、商人祭中の交易判断、封鎖予定の港、山賊襲来の対象数字、地震予定区画を建設・交易・盗賊移動へ反映します。権威サーバーだけが保持する未来のevent deckは参照しません。

## API

すべてsame-origin JSONです。browser sessionと部屋復帰tokenは、どちらもJavaScriptから読めない別々の `HttpOnly; SameSite=Strict` cookieに保存します。TLS使用時は両方へ `Secure` を付けます。

- `GET /api/health`: processの生存確認と現在の `http` / `websocket` transport名（session数、部屋、tokenなどは返しません）
- `POST /api/session`: browser sessionの作成または既存sessionの復元
- `DELETE /api/session`: browser transportの切断
- `POST /api/invitations`: current hostがexact `{role}` で期限付きのplayer / spectator bearerを1件発行
- `POST /api/invitations/list`: current hostがexact `{}` でtoken-freeな未使用招待一覧を取得
- `DELETE /api/invitations`: current hostがexact `{invitation_id}` または `{all: true}` で未使用招待を取消
- `POST /api/invitations/claim`: exact `{room_code, token}` を検証し、raw bearerを返さず永続可能なHttpOnly claim cookieへ交換
- `POST /api/invitations/resume`: exact `{}` とHttpOnly claim cookieから、公開可能な招待metadataだけを復元
- `DELETE /api/invitations/claim`: exact `{}` で現在のclaimを解放し、claim cookieを破棄
- `POST /api/resume`: request bodyへ秘密値を入れず、HttpOnly部屋復帰cookieから席を復元し、新しい復帰cookieへローテーション
- `POST /api/resume/confirm`: 新しい復帰cookieの受領を確認し、直前のtokenを失効
- `DELETE /api/resume`: 明示退出後に部屋復帰cookieを破棄
- `GET /api/socket`: 認証済みsessionをsame-origin WebSocketへupgrade
- `GET /api/events`: 自分宛てeventの取得
- `POST /api/message`: LAN protocolと同じversion付きmessageを送信

`create_room` はWeb専用のtop-level `invite_only: true` を受け付けます。gatewayは既存の永続passphrase gateへserver生成の高entropy secretを設定し、そのsecretをJavaScriptやeventへ返しません。招待claim後の `join_room` はclient roleを受け付けず、server-side claimのscopeだけで席または観戦を決めます。clientが通常のroleを明示した場合はpending claimを破棄し、従来のcode / passphrase経路へ戻します。

`create_room` と通常の `join_room` は必要な場合だけtop-level `passphrase` を1回送ります。部屋作成・参加・退出、招待の発行・一覧・取消・claim・claim復元・claim解放は、response headerとsecret処理を確実に行えるHTTP経路へ固定し、WebSocketからは受理しません。招待endpointはOrigin headerを必須にします。一覧と取消はその部屋へ接続中のhostだけが使えます。保護付き処理を許可するかはmessageの自己申告ではなく、serverが観測したTLS状態とTCP peerから決めます。loopback以外の平文HTTPでは拒否し、接続元あたり毎分5回・server全体で毎分30回の専用上限をKDFやauthority処理より前に適用します。

盤面全体をクライアントから送り返すAPIはありません。ゲーム操作は `command`、権威サーバーから受け取った引数、期待revision、連番だけを送ります。

ブラウザはsession作成後にWebSocketへ接続し、成功中はJSON messageを同じgatewayへ送ります。WebSocketを利用できない環境、upgrade失敗、または切断中は `POST /api/message` と `GET /api/events` を使い続け、裏でupgradeを再試行します。WebSocket frameはbrowserからのmask、UTF-8のJSON object、message上限を検証し、分割data frameやbinary frameを受理しません。

## 資源信用UI

開始画面で `credit` / `bank_loan_v1` を選ぶと、対局画面に「資源信用所」を表示します。未返済ローンは公開情報として、借り手、借入資源、残り期限または延滞残債、公開VP減点を全参加者・観戦者・リプレイへ同じ形で配信します。

借入と返済の編集モーダルは本人の有効な操作候補があるときだけ開きます。借入では銀行在庫がある資源だけを選択でき、通常返済では借りた資源1枚を含む合計2枚、延滞後は残債以下の任意資源を指定できます。サマリーは `aria-live` で検証結果を伝え、確定時はloan IDとrevisionを含む権威サーバーの候補に一致するか再確認します。`credit_borrow` / `credit_repay` は専用パネルへ集約し、一般の行動ボタンへ重複表示しません。

## イベント＆経済（複合）UI

開始画面の `composite` / `events_economy_v1` は、予告イベント、交易2.0（常設市場と公開競売）、資源信用を固定構成で同時に有効化します。ブラウザは権威snapshotの `variant_state.public.components` にある各componentの公開領域だけを共通adapterで読み、秘密のevent deck、採番値などの `private` 領域や未対応componentを参照しません。個別モードと同じcommand optionをそのまま使うため、合法手判定やrevision境界をブラウザ側へ複製しません。

対局画面ではイベント予告カード、常設市場、公開競売、資源信用所を同時に表示します。狭い画面では盤面前のイベント予告帯を維持し、その後は行動、常設市場、公開競売、資源信用、プレイヤー、イベント履歴の順に通常のページスクロールで確認できます。各編集モーダルのfocus復帰、背景の操作禁止、Escapeで閉じる操作は個別モードと共通です。ルール早見表には3機能の注意を同時に表示します。

## グランドキャンペーン（全部入り）UI

開始画面の「グランドキャンペーン（全部入り）」は `composite` / `grand_campaign_v1` を送り、37タイル探索、予告イベント、常設市場、公開競売、資源信用を固定構成で有効化します。既存の標準19タイル `events_economy_v1` とは別モードで、任意の子componentや子optionsをブラウザから組み替えません。

Webは入れ子の `forecast_events` / `frontier` / `trade2` / `credit` の4種類だけを許可し、各componentの公開catalogがそれぞれ `campaign_v1` / `outer_ring_37_v1` / `market_auction_v1` / `bank_loan_v1` と一致するときだけ表示します。未知component、外側と子の `private`、イベントdeckなどの非公開値は読みません。予告イベントと探索状況は同時に表示し、狭い画面でも予告帯、行動、市場、競売、信用、プレイヤー、イベント・探索情報を通常のページスクロールで確認できます。

`campaign_v1` の港湾封鎖は、予告時点ですでに公開されていた交換所だけから固定した公開planを表示します。対象がある場合は交換所番号を示し、公開済み交換所がなかった場合は「公開済み交換所なし・今回は発動なし」と表示します。予告後に新しい港が公開されても対象を選び直さないため、未知領域の港を予告から推測できません。

## AI席

`player_count` は人間とAIを合わせた総席数、`ai_player_count` はそのうち最後の席から予約するAI数です。少なくとも人間を1人残すため、AIは `0` から `player_count - 1` 人まで選べます。AI席は接続やready操作を必要とせず、ロビー上では `CPU1` から順に表示されます。人間は残りの先頭席へ参加します。

性格mode:

- `standard`: 標準
- `mixed`: 非公開match seedで拡大重視、交渉重視、妨害重視をシャッフルし、対局後にだけ実際の割り当てを公開
- `expansion`: 拡大重視
- `trader`: 交渉重視
- `disruptor`: 妨害重視

## リザルトとネットワークリプレイ

権威サーバーは開始時と、受理した人間操作・成功したAI判断の各revisionを対局履歴へ記録します。既定の履歴上限は部屋あたり512フレームです。上限を超えた長期戦では保持中の先頭revisionをリザルトに明示し、序盤が保存範囲外であることをUIに表示します。保存対象は各player席用とspectator用に分け、取得するvariantはbrowserが指定せず、認証済みcontroller sessionの席から決定します。

リプレイsnapshotでは操作候補を空にして読み取り専用にし、累積ログと重複メトリクスを圧縮します。他playerの資源、発展カード、勝利点カード、提示前の交易条件、未配布の発展カード順に加え、バリアント実行時状態の `private` 領域も保存境界で検査します。spectator用は全playerの非公開内訳を伏せ、バリアントは予告済み情報などの公開領域だけを保持します。

終了時は既存の公開match resultから、順位、VP推移、建設・交易数、運指数、重要イベントを配信します。重要イベントとVP checkpointは最初に観測したrevisionへ結び付けます。履歴上限で該当revisionが既に消えている場合は、別のフレームへ誤って移動せずリンクなしとして表示します。

既定ではこの履歴はprocess memory内だけに置きます。`--state-db` と `--replay-db` を同時に指定すると、各revisionを独立したHMAC付きSQLite rowへ保存し、server再起動後も同じ認証済み席から最初の保持フレームまで遡れます。既定上限は1部屋512フレーム、64部屋、合計128 MiB、最終更新から7日です。部屋削除時は履歴も削除し、参加コード再利用時は安定したauthority room IDの不一致を確認して古い履歴を破棄します。

対局状態を先に確定してからリプレイをbest-effortで保存します。リプレイ保存失敗によって成功済みのゲーム操作をrollbackせず、次回保存または再起動時に欠落revisionを `truncated` として明示します。反対に、リプレイが保存済み対局revisionより先行していれば誤った盤面を見せず起動を拒否します。同じrevisionでもviewer snapshotが現在の対局と一致しない場合や、待機室に不可能な過去frameがある場合は、その部屋のリプレイを利用不可にして以降のrevisionも混ぜません。これは認証中の部屋member向け履歴であり、account所有の長期アーカイブや公開共有URLではありません。ローカルPygame版が保存する全状態JSONリプレイとも別物です。リザルトJSON自体の形式は [`match-result.md`](match-result.md) を参照してください。

## 安全上の境界

既定では `localhost` / loopback IP以外へのbindを拒否します。非loopback bindは、信頼済みLANでは `--lan` とprivate/link-local/ULA IPまたは `.local` の `--allowed-host` を明示した場合だけ許可します。友人VPNでは `--friends-vpn`、TLS pair、ホストのassign可能なTailscale IPへの直接bind、完全修飾 `.ts.net` の `--allowed-host` 1つをすべて要求します。公開IP・通常DNS名・範囲外のTCP接続元を拒否し、wildcard bind自体を友人VPNでは禁止します。HostとOriginは正規化後の同一hostnameと実際のlisten portまで完全一致させます。`--internet-public` は、account認証・証明書自動更新・永続session・共有rate limitの専用profileが完成するまでは明示的に起動を拒否します。Origin / Fetch Metadata、CSP、frame拒否、MIME sniffing拒否を適用し、静的ファイルは固定routeだけを配って任意pathをファイルとして開きません。

Web session cookieはサーバーが観測したTCP接続元へ結び付けます。`X-Forwarded-For` などの転送headerは現在信頼しません。同じsessionで新しいWebSocketが接続すると古いsocketを閉じ、HTTP pollingとWebSocketのどちらを使っても同じmessage上限を消費します。部屋作成・参加・再接続試行はsessionを作り直しても接続元単位で制限し、入室パスフレーズを伴う作成と全参加試行には重い照合処理より前の専用上限も適用します。reconnect tokenの平文は作成/参加時にserver内部で捕捉し、HTTP JSON、WebSocket、reload bootstrap、`localStorage`、`sessionStorage` のいずれにも掲載しません。browserへは最大7日のhost-only HttpOnly復帰cookieとして渡しますが、実際に復帰できる期間はauthority側の席予約期限（通常120秒）が上限です。明示退出ではmembershipを取り消した後にcookieを削除し、単なるtransport切断では復帰のため保持します。

復帰cookieはbearer credentialです。平文LANでは同じLAN上の盗聴に耐えないため、別端末から遊ぶ場合はHTTPS / WSSを使ってください。Webの復帰に成功するとauthorityは新tokenをcurrentとして保存し、その要求で提示されたtokenをpreviousとして最大120秒だけ併存させます。browserは `session_welcome` を処理した後に `POST /api/resume/confirm` を送り、serverは新しいHttpOnly cookieのtokenがcurrentと一致することを確認してpreviousを失効させます。確認は公開ロビーrevisionを変えませんが、privateなcredential更新としてauthority generation CASで永続化します。

復帰commit後にHTTP responseが失われた場合、同じprocessとbrowser sessionが残っていればserver側に保持した新credentialからcookieとbootstrapを再送します。serverが先に再起動した場合も、永続authorityに残したpreviousの絶対期限内ならbrowserの旧cookieで復帰を再試行できます。previousを使った再試行は元の期限を引き継ぐため、再試行を繰り返して猶予を延長できません。LAN protocolの `reconnect_room` は互換性のためcurrent tokenだけを受理し、tokenをローテーションしません。

既定のrate limitは単一process内の第一防御層です。`--rate-limit-db` を指定すると、同じsemantic制限を別SQLiteへ保存し、再起動後と同一hostの複数processで共有します。DBには生のIP、cookie、reconnect token、部屋コードを残さず、別のowner-only鍵でHMAC化したbucket識別子と時刻だけを保存します。複数bucketを使う1操作は1transactionで判定し、backend障害時はmemory制限へfallbackせず、controller mutationより前に503で停止します。

このSQLite backendはnetwork filesystemや複数host cluster向けではありません。複数hostで共有するaccount/IP単位制限、ban、監査ログ、本番edge proxyの代わりにはなりません。

LAN modeは既定ではHTTP / `ws://` のままです。`--tls-cert` と `--tls-key` を同時指定した場合だけHTTPS / `wss://` へ切り替え、Origin検証もHTTPSを要求し、session cookieへ `Secure` を付けます。友人VPN profileはTLSを省略できず、Tailscale access controlが認証・到達性の外側の権威になります。どちらもrouterのport forwardingや公開reverse proxyへそのまま接続しないでください。内蔵TLSと友人VPNは少人数alphaのtransport境界であり、Internet一般公開用のsecurity profileではありません。次の段階で少なくとも以下が必要です。

- 証明書の安全な配布・自動更新を含む本番TLS / WSS終端
- account認証とroom参加権限
- account session、account所有の長期リプレイarchiveと共有権限
- 複数hostで共有するIP・account・room単位rate limit backend
- 複数instanceで共有するIP・account・room単位のrate limit、ban、監査ログ
- CSRF token、Origin allowlist、秘密情報管理の本番構成
- deploy先での負荷試験、依存監査、運用監視

## 次の改善候補

1. `grand_campaign_v1` の対戦データを検証し、イベント頻度・探索速度・経済バランスを調整する
2. 実装済みのroom authority・network replay storeと同一host共有rate limitを土台に、account sessionと複数host backendを追加する
3. フロンティアの特殊探索物を、通常37タイル版と分離した次catalogで追加する
4. account認証・証明書自動更新・複数host共有rate limit・監査ログを整えた後にInternet向けstagingを構築
