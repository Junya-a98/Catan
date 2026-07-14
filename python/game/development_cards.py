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


def create_development_deck(*, disabled_cards=()):
    """Build the standard deck, omitting explicitly disabled card types."""

    try:
        disabled = frozenset(disabled_cards)
    except TypeError as exc:
        raise ValueError("disabled development cards must be iterable") from exc
    if any(not isinstance(card_type, DevelopmentCardType) for card_type in disabled):
        raise ValueError("disabled development cards contain an unknown type")
    deck = (
        [DevelopmentCardType.KNIGHT] * 14
        + [DevelopmentCardType.VICTORY_POINT] * 5
        + [DevelopmentCardType.ROAD_BUILDING] * 2
        + [DevelopmentCardType.YEAR_OF_PLENTY] * 2
        + [DevelopmentCardType.MONOPOLY] * 2
    )
    deck = [card_type for card_type in deck if card_type not in disabled]
    random.shuffle(deck)
    return deck
