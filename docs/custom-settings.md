# カスタムマップ・ハウスルール境界

開始前の詳細設定は、Pygameの描画座標やbutton状態ではなく、次の2つの不変モデルで保持します。

- `game.custom_map.CustomMapSpec`
- `game.house_rules.HouseRules`

ローカル対局、セーブ、リプレイ、LAN権威サーバーは同じdocumentを読みます。将来のWebクライアントは画面だけを置き換え、ルール処理と検証をサーバー側で再利用できます。

## カスタムマップ

現在のtopologyは `standard-19` です。19個のaxial座標へ地形と数字を割り当て、外周9港をslot番号で保持します。

```json
{
  "format": "catan-custom-map",
  "version": 1,
  "topology": "standard-19",
  "name": "カスタムマップ",
  "tiles": [
    {"q": 0, "r": -2, "resource": "WOOD", "number": 10}
  ],
  "harbors": [
    {"slot": 0, "resource": null},
    {"slot": 1, "resource": "WOOD"}
  ]
}
```

実際のdocumentには19タイルと9港をすべて含めます。検証では次を保証します。

- 木4・羊4・麦4・土3・鉄3・砂漠1
- 数字 `2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12`
- 一般港4、木・羊・麦・土・鉄の各資源港1
- axial座標と港slotの重複・欠落なし
- 未知field、boolを整数として使う値、未知資源を拒否

editorは地形、数字、港を新規作成せず、2つの要素を交換します。このため編集途中でも公式の在庫構成を崩しません。6/8隣接や高確率資源と同資源港の組み合わせは、構造上のエラーではなくbalance警告として表示します。

SHA-256 fingerprintは、名前を除いたtopology・タイル・港のcanonical JSONから計算します。表示名を変えても同じ盤面として扱い、内容の改ざんやLANでの取り違えを検出します。

## ハウスルール

```json
{
  "bank_trade_3_to_1": false,
  "skip_discard_on_seven": false,
  "disabled_development_cards": []
}
```

`disabled_development_cards`には `KNIGHT`、`ROAD_BUILDING`、`YEAR_OF_PLENTY`、`MONOPOLY`、`VICTORY_POINT`を指定できます。documentが旧セーブや旧リプレイに存在しない場合だけ標準ルールへ戻します。documentが存在する場合は全fieldを必須とし、誤記を暗黙の既定値へ置き換えません。

## 適用と保存

詳細設定画面はdraftを編集します。

1. 開いた時点の盤面とルールを不変draftへコピー
2. 編集中はlive gameを変更しない
3. `キャンセル`でdraftを破棄
4. `適用`時に新しい盤面を先に検証・構築
5. 構築成功後だけ盤面、ルール、発展カード山札をまとめて交換

カスタム盤面のセーブは完全なmap documentとfingerprintを両方要求します。リプレイはmapとhouse rulesのfingerprintを対局identityに含めます。LANではroom作成時にdocumentを権威サーバーが再検証し、対局中は公開board manifestとfingerprintをクライアントが照合します。

## Web版への移行方針

ブラウザへPythonオブジェクトやローカルTCP protocolを直接公開しません。

- room作成時に上記JSONをHTTPSで送る
- サーバーが同じstrict parserで再検証する
- 対局操作はWebSocket上のsemantic commandとして送る
- サーバーだけが `CatanGame` を進行する
- クライアントはstable ID付きboard manifestと閲覧者別snapshotを描画する
- 非公開手札、再接続token、発展カード山札順は公開snapshotへ含めない

Internet公開前にはWSS、Origin検証、account認証、rate limit、永続session、監査ログを追加します。現在のLAN portをそのままInternetへ公開する構成は対象外です。
