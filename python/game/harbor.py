from game.resources import ResourceType


HARBOR_RESOURCE_LABELS = {
    ResourceType.WOOD: "木",
    ResourceType.SHEEP: "羊",
    ResourceType.WHEAT: "麦",
    ResourceType.BRICK: "土",
    ResourceType.ORE: "鉄",
}


class Harbor:
    def __init__(self, node1, node2, trade_rate, resource_type=None):
        self.node1 = node1
        self.node2 = node2
        self.trade_rate = trade_rate
        self.resource_type = resource_type

    @property
    def label(self):
        if self.resource_type is None:
            return f"{self.trade_rate}:1"
        return f"{HARBOR_RESOURCE_LABELS[self.resource_type]} {self.trade_rate}:1"
