class Node:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.building = None  # 建物がない場合は None
        self.tiles = []       # このノードに接しているタイル (HexTile) のリスト
