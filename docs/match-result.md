# 対局メトリクスとリザルトJSON

対局後リザルトは、ゲームルール・集計・表示を分離しています。

- `game.match_metrics`: 対局中の累積値を記録するPygame非依存のモデル
- `game.match_result`: ゲーム状態とリプレイから公開用のversion付きJSONを生成
- `game.result_display`: JSONをPygameで表示し、クリック位置だけをゲーム本体へ返す

この境界により、将来のWeb版やLAN対戦では同じリザルトJSONをHTTP / WebSocketで送り、ブラウザ側だけ別の表示実装に置き換えられます。非公開の手札や発展カード内訳はリザルトJSONへ含めません。

## リザルト形式

`build_match_result(game, replay=archive)` は、概ね次の構造を返します。

```json
{
  "format": "catan-match-result",
  "version": 1,
  "completed": true,
  "board": {"mode": "constrained", "seed": 86712347},
  "victory_target": 10,
  "winner": {"seat": 2, "name": "CPU1"},
  "standings": [
    {
      "rank": 1,
      "seat": 2,
      "name": "CPU1",
      "victory_points": 10,
      "builds": {"roads": 9, "settlements": 4, "cities": 3},
      "trades": {"bank": 2, "domestic": 4},
      "luck_index": 108.4
    }
  ],
  "vp_progression": [
    {
      "sequence": 6,
      "replay_frame_index": 42,
      "label": "CPU1が都市へ発展",
      "scores": [{"seat": 2, "victory_points": 7}]
    }
  ],
  "important_events": [
    {
      "sequence": 12,
      "title": "CPU1の勝利",
      "detail": "10 VPに到達しました",
      "replay_frame_index": 85
    }
  ],
  "replay": {"available": true, "frame_count": 86}
}
```

フィールドはJSON安全な値だけで構成されます。受信側は未知の追加フィールドを無視し、`format` と `version` を確認してから利用してください。

`sequence` は対局内の意味的な時系列、`replay_frame_index` はリプレイへのジャンプ位置です。セーブ地点から再開した場合など、対応する過去リプレイがない項目は `replay_frame_index: null` のまま `sequence` で正しい順序を保ちます。プレイヤーの計測IDには表示名ではなく `seat-1`〜`seat-4` を使い、改名・再接続後も同じ席を追跡します。

## 運指数

運指数はダイスによる資源生産だけを測ります。

```text
運指数 = 実際の生産ユニット ÷ 確率上の期待生産ユニット × 100
```

- `100`: 期待どおり
- `100` より大きい: 盤面確率より多く生産
- `100` より小さい: 盤面確率より少なく生産
- 盗賊がいるタイルは実績・期待値の両方から除外
- 銀行在庫不足はダイス運ではないため、実績は本来生産するユニットで数える
- まだ期待生産がない場合は安全な中立値 `100`

## Web / LANでの利用方針

LANサーバーは権威状態から `match-result` を1回生成し、全参加者と観戦者へ同じ公開結果を配信します。ブラウザ版はこのJSONから順位表、SVG / CanvasのVPグラフ、重要イベント一覧を描画できます。

重要イベントの `replay_frame_index` はローカルの非公開リプレイを直接公開するためのURLではありません。ネットワーク対戦では、サーバーが閲覧者ごとにマスキングしたリプレイフレームを返す設計にします。
