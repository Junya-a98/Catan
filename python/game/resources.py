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

BUILD_COSTS = {
    "road": {
        ResourceType.WOOD: 1,
        ResourceType.BRICK: 1,
    },
    "settlement": {
        ResourceType.WOOD: 1,
        ResourceType.BRICK: 1,
        ResourceType.SHEEP: 1,
        ResourceType.WHEAT: 1,
    },
    "city": {
        ResourceType.ORE: 3,
        ResourceType.WHEAT: 2,
    },
    "development": {
        ResourceType.ORE: 1,
        ResourceType.SHEEP: 1,
        ResourceType.WHEAT: 1,
    },
}
