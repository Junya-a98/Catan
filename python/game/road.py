from game.player import Player
from game.node import Node

class Road:
    def __init__(self, owner: Player, node1: Node, node2: Node):
        self.owner = owner
        self.node1 = node1
        self.node2 = node2