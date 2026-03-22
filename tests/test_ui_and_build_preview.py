from game.game import CatanGame
from game.harbor import Harbor
from game.player import Player
from game.resources import ResourceType


def test_resource_harbor_label_uses_resource_glyph():
    harbor = Harbor(object(), object(), 2, ResourceType.ORE)

    assert harbor.label == "鉄 2:1"


def test_build_preview_lists_missing_resources():
    game = CatanGame.__new__(CatanGame)
    game.development_deck = [object()]
    player = Player("Tester", (255, 0, 0))

    previews = game.get_build_affordability(player)
    road_preview = next(item for item in previews if item["label"] == "街道")
    settlement_preview = next(item for item in previews if item["label"] == "開拓地")

    assert road_preview["available"] is False
    assert road_preview["detail"] == "不足: 木1 土1"
    assert settlement_preview["detail"] == "不足: 木1 土1 羊1 麦1"


def test_build_preview_reports_supply_shortages_before_resource_shortages():
    game = CatanGame.__new__(CatanGame)
    game.development_deck = []
    player = Player("Tester", (255, 0, 0))
    player.roads_remaining = 0

    previews = game.get_build_affordability(player)
    road_preview = next(item for item in previews if item["label"] == "街道")
    development_preview = next(item for item in previews if item["label"] == "発展")

    assert road_preview["detail"] == "在庫なし"
    assert development_preview["detail"] == "山札なし"
