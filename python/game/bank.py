from game.resources import ResourceType


BANK_RESOURCE_COUNT = 19
RESOURCE_TYPES = (
    ResourceType.WOOD,
    ResourceType.SHEEP,
    ResourceType.WHEAT,
    ResourceType.BRICK,
    ResourceType.ORE,
)


class ResourceBank:
    """Finite CATAN resource-card supply (19 cards per resource)."""

    def __init__(self, cards_per_resource=BANK_RESOURCE_COUNT):
        self.cards_per_resource = cards_per_resource
        self.resources = {
            resource_type: cards_per_resource
            for resource_type in RESOURCE_TYPES
        }

    def available(self, resource_type):
        return self.resources.get(resource_type, 0)

    def can_withdraw(self, resource_type, amount=1):
        return amount >= 0 and self.available(resource_type) >= amount

    def withdraw(self, resource_type, amount=1):
        if not self.can_withdraw(resource_type, amount):
            return False
        self.resources[resource_type] -= amount
        return True

    def withdraw_up_to(self, resource_type, amount):
        taken = min(max(0, amount), self.available(resource_type))
        self.resources[resource_type] -= taken
        return taken

    def deposit(self, resource_type, amount=1):
        if resource_type not in self.resources or amount <= 0:
            return
        self.resources[resource_type] += amount

    def deposit_cost(self, cost):
        for resource_type, amount in cost.items():
            self.deposit(resource_type, amount)

    def total_cards(self):
        return sum(self.resources.values())
