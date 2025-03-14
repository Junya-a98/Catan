from enum import Enum
from game.constants import COLORS

class ResourceType(Enum):
    WOOD = 1
    SHEEP = 2
    WHEAT = 3
    BRICK = 4
    ORE = 5
    DESERT = 6

RESOURCE_COLORS = {
    ResourceType.WOOD: COLORS["GREEN"],
    ResourceType.SHEEP: COLORS["WHITE"],
    ResourceType.WHEAT: COLORS["YELLOW"],
    ResourceType.BRICK: COLORS["RED"],
    ResourceType.ORE: COLORS["GRAY"],
    ResourceType.DESERT: COLORS["WHEAT"]
}
