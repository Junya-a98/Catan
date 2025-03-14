from game.resources import ResourceType

class Player:
    def __init__(self, name, color):
        self.name = name
        self.color = color  # 建物表示用の色
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
    
    def __str__(self):
        res_str = ", ".join([f"{r.name}:{self.resources[r]}" for r in self.resources])
        return f"Player({self.name}) - {res_str}"
