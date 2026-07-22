# 友人VPN限定Web対戦

`--friends-vpn` は、Tailscaleで認証された友人端末だけにWeb版を届けるための明示的な起動profileです。一般公開profileではありません。routerのport forwarding、cloudの公開IP、reverse proxy、Tailscale Funnelは使いません。

## セキュリティ境界

このprofileではアプリが次を強制します。

- listen先はwildcardではなく、この端末へ割り当てられたTailscale IPv4 `100.64.0.0/10` またはIPv6 `fd7a:115c:a1e0::/48` の1 addressだけ
- Tailscaleが内部用として予約するaddressはbind先と接続元の両方で拒否
- TCP接続元も同じTailscale device範囲だけ。`X-Forwarded-For` などの自己申告headerは参照しない
- URLのHost / Originは、証明書と一致するcanonicalな完全修飾 `.ts.net` 名1つだけ
- TLS 1.2以上のcert/keyを必須にし、HTTPS / WSSと`Secure; HttpOnly; SameSite=Strict` cookieを使用
- 新規部屋をserver側で必ず期限付き招待専用に変換し、passphrase部屋の作成とcode/passphraseによる直接参加を拒否
- 参加はhostが相手ごとに発行したplayer / spectator別の1時間・1回限り招待、または既存席のHttpOnly再接続cookieだけ
- `--lan`との併用、`--internet-public`、wildcard bind、通常LAN/public IP、通常DNS名をfail closedで拒否

Tailscale peerの本人確認、端末の所属、誰がこのportへ到達できるかはTailscaleのtailnetとaccess control policy / grantsが権威です。アプリはsource addressの範囲を再検査しますが、Tailscale accountやACLを代替しません。遊ぶ友人だけをtailnetへ招待し、対象端末とこのportだけを許可してください。不要になった端末・利用者はtailnetから削除またはアクセスを失効させます。

## 準備

1. ホストと参加者へTailscaleを導入し、同じtailnetまたは明示的に共有した端末として接続します。
2. Tailscaleのaccess controlで、遊ぶ友人の端末からホストのCatan用portだけを許可します。
3. ホストでMagicDNSとHTTPS certificateを有効にし、ホストの完全修飾名を確認します。
4. ホストで証明書と鍵をrepository外のowner-only directoryへ発行します。

例として、ホストのaddressと名前が `100.101.1.2`、`catan.tail1234.ts.net` の場合:

```bash
tailscale ip -4
tailscale cert \
  --cert-file=/安全な場所/catan.crt \
  --key-file=/安全な場所/catan.key \
  catan.tail1234.ts.net
```

TailscaleのHTTPS certificateを有効にすると、端末の完全修飾名がCertificate Transparencyの公開ledgerへ記録されます。機密情報を含まないmachine名を先に設定してください。fileへ出力した証明書は自動更新されないため、期限前の更新もホスト側の責任です。

## 起動

```bash
PYTHONPATH=python python python/web_main.py \
  --friends-vpn \
  --host 100.101.1.2 \
  --allowed-host catan.tail1234.ts.net \
  --tls-cert /安全な場所/catan.crt \
  --tls-key /安全な場所/catan.key \
  --state-db saves/web-room-authority.sqlite3 \
  --replay-db saves/web-network-replay.sqlite3 \
  --rate-limit-db saves/web-rate-limits.sqlite3
```

起動後、ホストは `https://catan.tail1234.ts.net:8765/` を開きます。`GET /api/health` の `access_profile` が `friends_vpn`、transportが`https` / `wss`であることを確認できます。

1. ホストが部屋を作ります。UIでopen roomを選んでも、このprofileではserverが招待専用へ固定します。
2. 待機室から、友人ごとにplayer用またはspectator用のリンクを1本発行します。
3. リンクを公開SNSではなく、本人との私的な経路で送ります。同じリンクを複数人へ転送しません。
4. 誤送信した場合は「未使用の招待」からそのリンクを取り消します。全員が参加したら残りを一括で取り消して開始します。
5. 受け手がリンクを開くと、生の招待はJavaScriptから読めない一時claim cookieへ交換されます。参加をやめる場合は「この招待を使わない」を押します。
6. `--state-db` を指定していれば、リンク確認後・入室前にserverを再起動しても、同じbrowserで再読み込みすると招待画面へ戻れます。入室後の再読み込みや一時切断も、別のHttpOnly再接続cookieで同じ席へ復帰できます。

同じbrowser profileではclaim cookieを共有するため、同時に複数の招待を保留しません。複数席を1台で確認する場合は、別browser、別profile、またはprivate windowを使います。

## 対象外

これは一台のホストと少人数の友人向けalphaです。account認証、public matchmaking、NAT越えrelay、複数host共有session/rate limit、証明書自動更新、DDoS対策はありません。claim発行直後にHTTP response自体が失われた場合、そのclaim digestは招待期限まで残ることがありますが、1招待8件の上限があります。また入室の永続commit直後にresponseとcookieの両方を受け取れず、そのままserverも再起動した場合の席credential再配布は今後の課題です。Tailscale Funnelやrouterのport開放で一般Internetへ出すと、このsecurity boundaryを外れるため使用しないでください。`--internet-public` は引き続き起動を拒否します。

参考:

- [TailscaleのIP address範囲](https://tailscale.com/docs/reference/reserved-ip-addresses)
- [Tailscale HTTPS certificateの設定](https://tailscale.com/docs/how-to/set-up-https-certificates)
- [tailnetの安全設定](https://tailscale.com/docs/reference/best-practices/security)
