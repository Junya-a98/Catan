from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GuidanceState:
    phase: str
    initial_dice_phase: bool
    waiting_for_road: bool
    initial_round: int
    special_phase: Optional[str]
    dice_rolled: bool
    action_mode: Optional[str]
    show_seed_input_hint: bool = False
    discard_player_name: Optional[str] = None
    discard_remaining: int = 0
    resource_selection_remaining: int = 0
    free_roads_remaining: int = 0


def build_help_panel_content(state: GuidanceState):
    if state.phase == "initial" and state.initial_dice_phase:
        accent = "盤面 mode と seed を決めたら初期ダイスへ進みます。"
        if state.show_seed_input_hint:
            accent = "seed を入力中です。Enter で反映、Esc で入力終了。"
        return (
            "操作ヘルプ",
            [
                "2 / 3 / 4: プレイヤー人数を変更",
                "AIプレイヤーボタン: CPU人数を変更",
                "Space: 初期ダイスを振る",
                "数字入力: seed を編集",
                "同点は自動で再ロール",
            ],
            accent,
        )

    if state.phase == "initial":
        accent = "ノードを選んで開拓地、次に隣接辺へ街道を置きます。"
        if state.waiting_for_road:
            accent = "直前の開拓地に接続する辺だけが有効です。"
        return (
            "初期配置ガイド",
            [
                "クリック: 開拓地または街道を配置",
                "光っている候補だけが配置可能",
                "2周目の開拓地では初期資源を獲得",
            ],
            accent,
        )

    help_lines = [
        "Space: ダイス / Enter: 手番終了",
        "R / S / C: 街道 / 開拓地 / 都市",
        "D / T / P: 発展購入 / 銀行交易 / 交渉",
        "K / B / Y / M: 騎士 / 街道建設 / 収穫 / 独占",
        "Esc: 選択取消",
    ]
    accent = "光っている候補をクリックして操作します。"
    if state.special_phase and state.special_phase.startswith("domestic_trade_"):
        accent = "右の交渉パネルで相手・資源・枚数・回答を選びます。"
    elif state.special_phase == "discard":
        accent = "右の資源ボタンまたは 1-5 で捨て札を選びます。"
    elif state.special_phase == "move_robber":
        accent = "黄色く光る地形へ盗賊を移動します。"
    elif state.special_phase == "steal":
        accent = "赤く光る相手の建物を選んで略奪します。"
    elif state.special_phase == "road_building":
        accent = "光っている辺に無料の街道を置けます。"
    elif state.special_phase in ("year_of_plenty", "monopoly"):
        accent = "右の資源ボタンまたは 1-5 で対象を選びます。"
    elif state.action_mode == "road":
        accent = "光っている辺が建設可能な街道です。"
    elif state.action_mode == "settlement":
        accent = "緑の交点が開拓地を建てられる場所です。"
    elif state.action_mode == "city":
        accent = "金色の交点が都市へアップグレード可能です。"
    elif not state.dice_rolled:
        accent = "手番開始時はまずダイスを振ります。"
    return "操作ヘルプ", help_lines, accent


def build_action_mode_guidance(action_mode, preview, candidate_count):
    if action_mode == "road":
        if not preview["available"]:
            return ["街道不可", preview["detail"]]
        if candidate_count <= 0:
            return ["街道不可", "接続できる建設先がありません。"]
        return [f"次: 光っている辺を選ぶ ({candidate_count} 箇所)", "Esc で選択を戻せます。"]

    if action_mode == "settlement":
        if not preview["available"]:
            return ["開拓地不可", preview["detail"]]
        if candidate_count <= 0:
            return ["開拓地不可", "自分の街道が接続する有効な交点がありません。"]
        return [f"次: 緑の交点を選ぶ ({candidate_count} 箇所)", "距離ルールを満たす候補だけ光ります。"]

    if action_mode == "city":
        if not preview["available"]:
            return ["都市不可", preview["detail"]]
        if candidate_count <= 0:
            return ["都市不可", "自分の開拓地がないためアップグレードできません。"]
        return [f"次: 金色の交点を選ぶ ({candidate_count} 箇所)", "自分の開拓地だけ都市にできます。"]

    return []


def build_side_panel_guidance(state: GuidanceState, action_mode_guidance, highlighted_action_labels):
    if state.phase == "initial" and state.initial_dice_phase:
        if state.show_seed_input_hint:
            return ["次: seed を入力して Enter で反映", "再生成ボタンで新しい盤面も作れます。"]
        return ["次: 人数・AI・盤面を確認して初期ダイスを振る", "最高点の同点だけ自動で再ロールされます。"]

    if state.phase == "initial":
        if state.waiting_for_road:
            return ["次: 直前の開拓地に接続する辺へ街道を置く", "光っている辺だけが有効です。"]
        return ["次: 緑の候補から開拓地を置く", "2周目の開拓地では初期資源を獲得します。"]

    if state.phase == "finished":
        return ["ゲーム終了", "同じ盤面で再戦するか、新しい盤面を生成できます。"]

    if state.special_phase == "discard" and state.discard_player_name is not None:
        return [f"次: {state.discard_player_name} の捨て札を選ぶ", f"右の資源ボタンで残り {state.discard_remaining} 枚を選びます。"]
    if state.special_phase == "move_robber":
        return ["次: 黄色く光る地形へ盗賊を移動する", "現在いる地形には置けません。"]
    if state.special_phase == "steal":
        return ["次: 赤く光る相手の建物を選ぶ", "手札0枚の相手を選ぶと略奪は空振りになります。"]
    if state.special_phase == "year_of_plenty":
        return ["次: 欲しい資源を 2 枚選ぶ", f"右の資源ボタンで残り {state.resource_selection_remaining} 枚を選びます。"]
    if state.special_phase == "monopoly":
        return ["次: 独占する資源を選ぶ", "右の資源ボタンで選んだ種類を全員から回収します。"]
    if state.special_phase == "road_building":
        return ["次: 光っている辺へ無料の街道を置く", f"残り {state.free_roads_remaining} 本です。"]
    if state.special_phase == "bank_trade_give":
        return ["次: 支払う資源を選ぶ", "強調された資源だけ支払いに使えます。"]
    if state.special_phase == "bank_trade_receive":
        return ["次: 受け取る資源を選ぶ", "支払った資源と同じ種類には交換できません。"]

    if not state.dice_rolled:
        return ["次: ダイスを振る", "騎士などの発展カードはダイス前にも使用できます。"]

    if action_mode_guidance:
        return action_mode_guidance

    if highlighted_action_labels and highlighted_action_labels != ["手番終了"]:
        return ["次: 行動を選ぶか「手番終了」", "実行可能: " + " / ".join(highlighted_action_labels[:4])]
    return ["次: 手番終了", "この手番で追加で実行できる行動はありません。"]
