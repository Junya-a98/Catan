# AI自己対戦レポート

`game.self_play_report` は、自己対戦コアが出力した試合結果を、JSONと外部依存のないHTMLダッシュボードへ変換します。HTMLはJavaScript、CDN、外部フォント、アクセス解析を使いません。

## 入力スキーマ

バッチは `matches` 配列と任意の `metadata` を持ちます。試合結果は辞書またはdataclassで渡せます。

```python
batch = {
    "metadata": {
        "master_seed": 42,
        "board_mode": "constrained",
        "victory_target": 10,
        "player_count": 4,
    },
    "matches": [
        {
            "match_seed": 42,
            "board_seed": 1824,
            "board_mode": "constrained",
            "victory_target": 10,
            "completed": True,
            "termination_reason": "victory",
            "winner_seat": 1,
            "winner_name": "CPU1",
            "starting_player_seat": 3,
            "turn_order": [3, 4, 1, 2],
            "turns": 71,
            "action_steps": 802,
            "dice_counts": {2: 3, 3: 5, 4: 8, 5: 9, 6: 11, 7: 12,
                            8: 10, 9: 8, 10: 7, 11: 4, 12: 2},
            "players": [
                {"seat": 1, "name": "CPU1", "personality": "standard", "vp": 10,
                 "roads": 9, "settlements": 3, "cities": 3, "knights": 1,
                 "initial_pips": 21, "initial_high_probability_access": True,
                 "initial_resource_diversity": 4,
                 "action_counts": {"domestic_trade_offers": 3,
                                   "domestic_trades_completed": 1,
                                   "bank_trades": 2, "robber_moves": 4,
                                   "knights_used": 1}},
                {"seat": 2, "name": "CPU2", "personality": "expansion", "vp": 7},
                {"seat": 3, "name": "CPU3", "personality": "trader", "vp": 8},
                {"seat": 4, "name": "CPU4", "personality": "disruptor", "vp": 6},
            ],
            "validation_errors": [],
        }
    ],
}
```

席番号は人が読む表示と同じ1始まりです。互換入力として `winner_index` / `seat_index` を使う場合だけ0始まりとして1始まりへ正規化します。`dice_counts` は2〜12の辞書、または順番どおりの11要素配列を受け付けます。`starting_player_seat` / `turn_order` がない場合、その公平性表だけを安全に省略します。

`action_counts` は自己対戦専用の行動計測です。`domestic_trade_offers` と
`domestic_trades_completed` は手番プレイヤーが開始した提案と成立、`bank_trades` は
銀行・港交易、`robber_moves` は盗賊移動、`knights_used` は騎士使用を表します。
性格別の平均は完走試合だけから計算します。古いJSONにこの項目がない場合、平均値は
0扱いせず `—` と表示します。
`roads` / `settlements` / `cities` も同様に、各性格が対局終了時に所有していた
街道・開拓地・都市の平均として表示します。旧名称の `balanced` / `builder` /
`blocker` は、それぞれ `standard` / `expansion` / `disruptor` へ統合します。

## Pythonから生成

```python
from game.self_play_report import render_terminal_summary, write_report

paths = write_report(batch, "self-play-reports", basename="batch-42")
print(paths.html_path)
```

## 自己対戦から一括生成

```bash
PYTHONPATH=python python python/simulate.py --games 100 --seed 42
```

`--board-seed` を省略すると盤面も試合ごとに変わります。特定盤面の公平性を比較するときは、同じ盤面を固定してダイスなどのmatch seedだけを変えます。
全体100戦未満、または統計の各比較行が20出場未満の場合は、小標本として注意が表示されます。

4人戦で性格を省略すると `standard,expansion,trader,disruptor` が割り当てられ、
試合ごとに席をローテーションします。カスタム構成は人数と同じ数を指定します。
旧バージョンの全員標準AIと同じ条件を再現する場合は、人数分の `standard` を
`--personalities` に明示します。

```bash
PYTHONPATH=python python python/simulate.py --games 100 \
  --personalities standard,expansion,trader,disruptor
```

```bash
PYTHONPATH=python python python/simulate.py \
  --games 100 --seed 42 --board-seed 86712347
```

## 既存JSONから生成

```bash
PYTHONPATH=python python -m game.self_play_report results.json \
  --output-dir self-play-reports --basename batch-42
```

レポートには、完走率、状態整合性、平均・中央値ターン、席順別・AI性格別勝率、性格×席の勝率、平均VP、性格別の平均行動回数と平均建設数、初手番・手番位置別勝率、初期配置のpipと6・8接触、ダイス実測値と期待値の差、個別試合一覧が含まれます。

自己対戦コアは各試合後に、資源総数、駒総数、街道辺の重複、所有者、勝者VP、ダイス回数を検査します。不整合があった試合は `validation_errors` と `integrity_error` を残し、完走勝率へ混ぜません。
