from game.player import Player
from game.node import Node

class Road:
    def __init__(self, owner: Player, node1: Node, node2: Node):
        self.owner = owner
        self.node1 = node1
        self.node2 = node2

    def touches(self, node: Node):
        return self.node1 is node or self.node2 is node

    def other_node(self, node: Node):
        if self.node1 is node:
            return self.node2
        if self.node2 is node:
            return self.node1
        return None
