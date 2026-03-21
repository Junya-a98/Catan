import random
from enum import Enum


class DevelopmentCardType(Enum):
    KNIGHT = "knight"
    ROAD_BUILDING = "road_building"
    YEAR_OF_PLENTY = "year_of_plenty"
    MONOPOLY = "monopoly"
    VICTORY_POINT = "victory_point"


DEVELOPMENT_CARD_LABELS = {
    DevelopmentCardType.KNIGHT: "騎士",
    DevelopmentCardType.ROAD_BUILDING: "街道建設",
    DevelopmentCardType.YEAR_OF_PLENTY: "収穫",
    DevelopmentCardType.MONOPOLY: "独占",
    DevelopmentCardType.VICTORY_POINT: "勝利点",
}


def create_development_deck():
    deck = (
        [DevelopmentCardType.KNIGHT] * 14
        + [DevelopmentCardType.VICTORY_POINT] * 5
        + [DevelopmentCardType.ROAD_BUILDING] * 2
        + [DevelopmentCardType.YEAR_OF_PLENTY] * 2
        + [DevelopmentCardType.MONOPOLY] * 2
    )
    random.shuffle(deck)
    return deck
