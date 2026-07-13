from game.ai_personality import normalize_ai_personality
from game.development_cards import DevelopmentCardType
from game.resources import ResourceType

class Player:
    def __init__(
        self,
        name,
        color,
        *,
        is_ai=False,
        piece_pattern=0,
        marker="●",
        ai_personality="standard",
    ):
        self.name = name
        self.color = color  # 建物表示用の色
        self.is_ai = is_ai
        self.piece_pattern = piece_pattern
        self.marker = marker
        self.ai_personality = normalize_ai_personality(ai_personality)
        self.roads_remaining = 15
        self.settlements_remaining = 5
        self.cities_remaining = 4
        self.played_knights = 0
        self.victory_point_cards = 0
        self.development_cards = {
            DevelopmentCardType.KNIGHT: 0,
            DevelopmentCardType.ROAD_BUILDING: 0,
            DevelopmentCardType.YEAR_OF_PLENTY: 0,
            DevelopmentCardType.MONOPOLY: 0,
        }
        self.new_development_cards = {
            DevelopmentCardType.KNIGHT: 0,
            DevelopmentCardType.ROAD_BUILDING: 0,
            DevelopmentCardType.YEAR_OF_PLENTY: 0,
            DevelopmentCardType.MONOPOLY: 0,
        }
        # 各資源の所持数
        self.resources = {
            ResourceType.WOOD: 0,
            ResourceType.SHEEP: 0,
            ResourceType.WHEAT: 0,
            ResourceType.BRICK: 0,
            ResourceType.ORE: 0
        }
    
    def add_resource(self, resource_type, amount=1):
        if resource_type in self.resources:
            self.resources[resource_type] += amount

    def remove_resource(self, resource_type, amount=1):
        if self.resources.get(resource_type, 0) < amount:
            return False
        self.resources[resource_type] -= amount
        return True

    def can_afford(self, cost):
        return all(self.resources.get(resource, 0) >= amount for resource, amount in cost.items())

    def spend_resources(self, cost):
        if not self.can_afford(cost):
            return False
        for resource, amount in cost.items():
            self.resources[resource] -= amount
        return True

    def total_resource_count(self):
        return sum(self.resources.values())

    def add_development_card(self, card_type, available=False):
        if card_type == DevelopmentCardType.VICTORY_POINT:
            self.victory_point_cards += 1
            return
        target = self.development_cards if available else self.new_development_cards
        target[card_type] += 1

    def activate_new_development_cards(self):
        for card_type, amount in self.new_development_cards.items():
            self.development_cards[card_type] += amount
            self.new_development_cards[card_type] = 0

    def has_playable_development_card(self, card_type):
        return self.development_cards.get(card_type, 0) > 0

    def use_development_card(self, card_type):
        if self.development_cards.get(card_type, 0) <= 0:
            return False
        self.development_cards[card_type] -= 1
        return True
    
    def __str__(self):
        res_str = ", ".join([f"{r.name}:{self.resources[r]}" for r in self.resources])
        return (
            f"Player({self.name}) - {res_str}, "
            f"Knight:{self.development_cards[DevelopmentCardType.KNIGHT]}, "
            f"RoadBuild:{self.development_cards[DevelopmentCardType.ROAD_BUILDING]}, "
            f"Plenty:{self.development_cards[DevelopmentCardType.YEAR_OF_PLENTY]}, "
            f"Monopoly:{self.development_cards[DevelopmentCardType.MONOPOLY]}, "
            f"VP:{self.victory_point_cards}"
        )
