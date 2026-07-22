from game.ai_personality import normalize_ai_personality
from game.development_cards import DevelopmentCardType
from game.resource_ledger import ResourceLedger
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

    @property
    def resources(self):
        """Total owned resource cards, including logically reserved cards."""

        return self._resources

    @resources.setter
    def resources(self, values):
        # Persistence replaces the complete map when restoring an older save.
        # Rebinding here keeps the sidecar ledger attached to that same public,
        # backwards-compatible dictionary instead of retaining stale totals.
        self._resources = values
        self.resource_ledger = ResourceLedger(self._resources)
    
    def add_resource(self, resource_type, amount=1):
        if resource_type in self.resources:
            self.resources[resource_type] += amount

    def remove_resource(self, resource_type, amount=1):
        if amount <= 0:
            return False
        return self.resource_ledger.spend_available({resource_type: amount})

    def can_afford(self, cost):
        return all(
            self.available_resource_count(resource) >= amount
            for resource, amount in cost.items()
        )

    def spend_resources(self, cost):
        if not self.can_afford(cost):
            return False
        if not cost:
            return True
        return self.resource_ledger.spend_available(cost)

    def total_resource_count(self):
        return sum(self.resources.values())

    def available_resource_count(self, resource_type):
        return self.resource_ledger.available_count(resource_type)

    def available_resource_total(self):
        return sum(self.resource_ledger.available_map().values())

    def reserved_resource_count(self, resource_type):
        return self.resource_ledger.reserved_count(resource_type)

    def reserved_resource_total(self):
        return sum(self.resource_ledger.reserved_map().values())

    def reserve_resources(self, reservation_id, bundle):
        return self.resource_ledger.reserve(reservation_id, bundle)

    def release_reserved_resources(self, reservation_id):
        return self.resource_ledger.release(reservation_id)

    def consume_reserved_resources(self, reservation_id):
        return self.resource_ledger.consume(reservation_id)

    def remove_owned_resource(self, resource_type, amount=1):
        """Apply a compulsory loss, cancelling funded reservations if needed."""

        return self.resource_ledger.remove_owned(resource_type, amount)

    def restore_resource_ledger(self, document):
        self.resource_ledger = ResourceLedger.from_document(
            self.resources,
            document,
        )

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
