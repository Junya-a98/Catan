from enum import Enum


class BuildingType(Enum):
    SETTLEMENT = "settlement"
    CITY = "city"


class Building:
    def __init__(self, owner, building_type=BuildingType.SETTLEMENT):
        self.owner = owner
        self.building_type = building_type

    @property
    def victory_points(self):
        if self.building_type == BuildingType.CITY:
            return 2
        return 1

    @property
    def resource_multiplier(self):
        if self.building_type == BuildingType.CITY:
            return 2
        return 1

    def upgrade_to_city(self):
        self.building_type = BuildingType.CITY
