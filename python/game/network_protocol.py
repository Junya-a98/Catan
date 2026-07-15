from collections.abc import Mapping
from copy import deepcopy
import json
import math
import re
import struct

from game.custom_map import CustomMapError, CustomMapSpec
from game.persistence import serialize_game
from game.variant_state import VariantState


NETWORK_PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
MAX_GAME_COMMAND_ARGS_BYTES = 64 * 1024
MAX_GAME_COMMAND_DEPTH = 8
MAX_GAME_COMMAND_ITEMS = 2_048
MAX_GAME_COMMAND_COLLECTION_ITEMS = 256
MAX_GAME_COMMAND_DICT_KEYS = 64
MAX_GAME_COMMAND_KEY_LENGTH = 64
MAX_GAME_COMMAND_STRING_LENGTH = 4_096
MAX_GAME_COMMAND_NAME_LENGTH = 64
MAX_SNAPSHOT_LOG_MESSAGES = 200
MAX_LIVE_MATCH_EVENTS = 200
MAX_FINISHED_MATCH_EVENTS = 1_000

_PUBLIC_VARIANT_STATE_FIELDS = (
    "format",
    "version",
    "kind",
    "config_fingerprint",
    "public",
)
_FULL_VARIANT_STATE_FIELDS = frozenset((*_PUBLIC_VARIANT_STATE_FIELDS, "private"))

_FRAME_HEADER = struct.Struct("!I")
_GAME_COMMAND_PATTERN = re.compile(
    rf"[a-z][a-z0-9_]{{0,{MAX_GAME_COMMAND_NAME_LENGTH - 1}}}\Z"
)


class NetworkProtocolError(ValueError):
    pass


def _validated_nonnegative_integer(value, *, label, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NetworkProtocolError(f"{label}が不正です。")
    if maximum is not None and value > maximum:
        raise NetworkProtocolError(f"{label}が大きすぎます。")
    return value


def _validated_coordinate(value):
    try:
        coordinate = float(value)
    except (TypeError, ValueError) as exc:
        raise NetworkProtocolError("盤面座標が不正です。") from exc
    if not math.isfinite(coordinate):
        raise NetworkProtocolError("盤面座標が有限値ではありません。")
    return round(coordinate, 6)


def _owner_player_index(owner, player_indices):
    try:
        return player_indices[owner]
    except (KeyError, TypeError) as exc:
        raise NetworkProtocolError("盤面の駒所有者が参加者に存在しません。") from exc


def build_board_reference_index(game):
    """Map public stable target IDs to the authoritative board objects.

    The semantic command router and the public manifest must both use this
    function.  Numeric IDs intentionally follow sorted geometry/topology, not
    mutable list insertion order.
    """

    board = game.board
    sorted_nodes = sorted(
        board.nodes,
        key=lambda node: (
            _validated_coordinate(node.y),
            _validated_coordinate(node.x),
        ),
    )
    node_references = {
        f"node-{index}": node for index, node in enumerate(sorted_nodes)
    }
    node_positions = {
        (
            _validated_coordinate(node.x),
            _validated_coordinate(node.y),
        )
        for node in sorted_nodes
    }
    if (
        len(set(node_references.values())) != len(board.nodes)
        or len(node_positions) != len(board.nodes)
    ):
        raise NetworkProtocolError("盤面ノードに重複があります。")
    node_ids = {node: target_id for target_id, node in node_references.items()}

    canonical_edges = []
    for node1, node2 in board.edges:
        try:
            endpoints = tuple(sorted((node_ids[node1], node_ids[node2])))
        except KeyError as exc:
            raise NetworkProtocolError("辺が不明なノードを参照しています。") from exc
        canonical_edges.append((endpoints, node1, node2))
    canonical_edges.sort(key=lambda item: item[0])
    edge_references = {
        f"edge-{index}": (node1, node2)
        for index, (_, node1, node2) in enumerate(canonical_edges)
    }
    if len({frozenset(edge) for edge in edge_references.values()}) != len(
        board.edges
    ):
        raise NetworkProtocolError("盤面の辺に重複があります。")

    sorted_tiles = sorted(
        board.tiles,
        key=lambda tile: (int(tile.axial[1]), int(tile.axial[0])),
    )
    tile_references = {
        f"tile-{index}": tile for index, tile in enumerate(sorted_tiles)
    }
    tile_axials = {
        (int(tile.axial[0]), int(tile.axial[1])) for tile in sorted_tiles
    }
    if (
        len(set(tile_references.values())) != len(board.tiles)
        or len(tile_axials) != len(board.tiles)
    ):
        raise NetworkProtocolError("盤面タイルに重複があります。")

    edge_ids = {
        frozenset((node1, node2)): target_id
        for target_id, (node1, node2) in edge_references.items()
    }
    harbor_edges = []
    for harbor in board.harbors:
        edge_key = frozenset((harbor.node1, harbor.node2))
        try:
            edge_id = edge_ids[edge_key]
        except KeyError as exc:
            raise NetworkProtocolError("港が不明な辺を参照しています。") from exc
        harbor_edges.append((edge_id, harbor))
    harbor_edges.sort(key=lambda item: item[0])
    harbor_references = {
        f"harbor-{index}": harbor
        for index, (_, harbor) in enumerate(harbor_edges)
    }
    if (
        len(set(harbor_references.values())) != len(board.harbors)
        or len({edge_id for edge_id, _ in harbor_edges}) != len(board.harbors)
    ):
        raise NetworkProtocolError("盤面の港に重複があります。")

    return {
        "node": node_references,
        "edge": edge_references,
        "tile": tile_references,
        "harbor": harbor_references,
    }


def build_board_manifest(game):
    """Return a complete public board description for non-Python clients.

    IDs are derived from stable board geometry rather than object identity or
    container insertion order.  A browser can therefore draw the current
    board directly without reproducing Python's random number generator.
    """

    board = game.board
    players = list(game.players)
    player_indices = {player: index for index, player in enumerate(players)}

    references = build_board_reference_index(game)
    node_by_id = references["node"]
    edge_by_id = references["edge"]
    tile_by_id = references["tile"]
    harbor_by_id = references["harbor"]
    sorted_nodes = list(node_by_id.values())
    node_ids = {node: target_id for target_id, node in node_by_id.items()}
    canonical_edges = [
        (tuple(sorted((node_ids[node1], node_ids[node2]))), node1, node2)
        for node1, node2 in edge_by_id.values()
    ]
    sorted_tiles = list(tile_by_id.values())
    edge_ids = {
        frozenset((node1, node2)): target_id
        for target_id, (node1, node2) in edge_by_id.items()
    }
    tile_ids = {tile: target_id for target_id, tile in tile_by_id.items()}
    harbor_ids = {
        harbor: target_id for target_id, harbor in harbor_by_id.items()
    }
    harbor_edges = sorted(
        [
            (
                edge_ids[frozenset((harbor.node1, harbor.node2))],
                harbor,
            )
            for harbor in harbor_by_id.values()
        ],
        key=lambda item: item[0],
    )

    road_by_edge = {}
    for road in board.roads:
        edge_key = frozenset((road.node1, road.node2))
        if edge_key not in edge_ids:
            raise NetworkProtocolError("街道が不明な辺を参照しています。")
        if edge_key in road_by_edge:
            raise NetworkProtocolError("同じ辺に複数の街道があります。")
        road_by_edge[edge_key] = road

    harbor_by_edge = {
        frozenset((harbor.node1, harbor.node2)): harbor
        for _, harbor in harbor_edges
    }
    perimeter_edges = {
        frozenset((node1, node2)) for node1, node2 in board.perimeter_edges
    }

    edge_ids_by_node = {node: [] for node in sorted_nodes}
    adjacent_nodes = {node: [] for node in sorted_nodes}
    for _, node1, node2 in canonical_edges:
        edge_id = edge_ids[frozenset((node1, node2))]
        edge_ids_by_node[node1].append(edge_id)
        edge_ids_by_node[node2].append(edge_id)
        adjacent_nodes[node1].append(node_ids[node2])
        adjacent_nodes[node2].append(node_ids[node1])

    tiles = []
    for tile in sorted_tiles:
        number = tile.number
        if number is not None and (
            isinstance(number, bool) or not isinstance(number, int)
        ):
            raise NetworkProtocolError("タイルの数字が不正です。")
        tiles.append(
            {
                "id": tile_ids[tile],
                "axial": {"q": int(tile.axial[0]), "r": int(tile.axial[1])},
                "center": {
                    "x": _validated_coordinate(tile.x),
                    "y": _validated_coordinate(tile.y),
                },
                "resource": tile.resource_type.name,
                "number": number,
                "corner_node_ids": [node_ids[node] for node in tile.corners],
                "robber": tile is board.robber_tile,
            }
        )

    nodes = []
    for node in sorted_nodes:
        building = None
        if node.building is not None:
            building = {
                "owner_player_index": _owner_player_index(
                    node.building.owner,
                    player_indices,
                ),
                "type": node.building.building_type.value,
            }
        nodes.append(
            {
                "id": node_ids[node],
                "position": {
                    "x": _validated_coordinate(node.x),
                    "y": _validated_coordinate(node.y),
                },
                "adjacent_tile_ids": sorted(tile_ids[tile] for tile in node.tiles),
                "adjacent_node_ids": sorted(adjacent_nodes[node]),
                "edge_ids": sorted(edge_ids_by_node[node]),
                "harbor_ids": sorted(
                    harbor_ids[harbor] for harbor in node.harbors
                ),
                "building": building,
            }
        )

    edges = []
    for _, node1, node2 in canonical_edges:
        edge_key = frozenset((node1, node2))
        road = road_by_edge.get(edge_key)
        harbor = harbor_by_edge.get(edge_key)
        adjacent_tile_ids = sorted(
            tile_ids[tile]
            for tile in sorted_tiles
            if node1 in tile.corners and node2 in tile.corners
        )
        edges.append(
            {
                "id": edge_ids[edge_key],
                "node_ids": sorted((node_ids[node1], node_ids[node2])),
                "adjacent_tile_ids": adjacent_tile_ids,
                "perimeter": edge_key in perimeter_edges,
                "road": (
                    None
                    if road is None
                    else {
                        "owner_player_index": _owner_player_index(
                            road.owner,
                            player_indices,
                        )
                    }
                ),
                "harbor_id": harbor_ids.get(harbor),
            }
        )

    harbors = []
    for edge_id, harbor in harbor_edges:
        harbors.append(
            {
                "id": harbor_ids[harbor],
                "edge_id": edge_id,
                "node_ids": sorted(
                    (node_ids[harbor.node1], node_ids[harbor.node2])
                ),
                "trade_rate": int(harbor.trade_rate),
                "resource": (
                    harbor.resource_type.name
                    if harbor.resource_type is not None
                    else None
                ),
                "label": str(harbor.label),
            }
        )

    positions = [node["position"] for node in nodes]
    bounds = {
        "min_x": min(position["x"] for position in positions),
        "max_x": max(position["x"] for position in positions),
        "min_y": min(position["y"] for position in positions),
        "max_y": max(position["y"] for position in positions),
    }
    manifest = {
        "format": "catan-board-manifest",
        "version": 1,
        "mode": str(board.mode),
        "seed": board.seed,
        "coordinate_space": {
            "kind": "board-pixels",
            "bounds": bounds,
        },
        "tiles": tiles,
        "nodes": nodes,
        "edges": edges,
        "harbors": harbors,
    }
    if board.mode == "custom":
        custom_map = getattr(board, "custom_map", None)
        if not isinstance(custom_map, CustomMapSpec):
            raise NetworkProtocolError(
                "カスタム盤面の公開identityがありません。"
            )
        try:
            live_map = CustomMapSpec.from_board(board, name=custom_map.name)
        except CustomMapError as exc:
            raise NetworkProtocolError(
                "カスタム盤面を公開manifestへ変換できません。"
            ) from exc
        if live_map.fingerprint != custom_map.fingerprint:
            raise NetworkProtocolError(
                "カスタム盤面の公開identityが現在の盤面と一致しません。"
            )
        manifest["custom_map_fingerprint"] = custom_map.fingerprint
    return manifest


def build_state_snapshot(game, *, viewer_player_index=None, revision=0):
    """Build a viewer-specific state without leaking other players' private cards."""
    state = deepcopy(serialize_game(game))
    if "variant_state" in state:
        state["variant_state"] = _public_variant_state_document(
            state["variant_state"]
        )
    board_state = state.get("board")
    if isinstance(board_state, dict):
        # The manifest already carries every public tile/harbor needed by LAN
        # and Web renderers.  Keep only the stable fingerprint in network
        # state; repeating the editable map document on every live snapshot
        # wastes bandwidth and enlarges the untrusted client input surface.
        board_state.pop("custom_map", None)
    player_count = len(state["players"])
    if viewer_player_index is not None and (
        isinstance(viewer_player_index, bool)
        or not isinstance(viewer_player_index, int)
        or not 0 <= viewer_player_index < player_count
    ):
        raise NetworkProtocolError("閲覧プレイヤー番号が不正です。")
    _validated_nonnegative_integer(
        revision,
        label="同期revision",
        maximum=MAX_SAFE_JSON_INTEGER,
    )

    for index, player in enumerate(state["players"]):
        resources = player["resources"]
        development_cards = player["development_cards"]
        new_development_cards = player["new_development_cards"]
        player["resource_total"] = sum(resources.values())
        player["development_card_total"] = (
            sum(development_cards.values())
            + sum(new_development_cards.values())
            + player["victory_point_cards"]
        )
        if index == viewer_player_index:
            continue
        player["resources"] = None
        player["development_cards"] = None
        player["new_development_cards"] = None
        player["victory_point_cards"] = None

    phase = state.get("phase", {})
    domestic_trade = state.get("domestic_trade")
    if (
        isinstance(phase, dict)
        and phase.get("special_phase") == "domestic_trade_edit"
        and isinstance(domestic_trade, dict)
        and viewer_player_index != domestic_trade.get("editor")
    ):
        # Draft terms have not been offered yet.  Other players and spectators
        # may know that somebody is editing a proposal, but not its contents.
        for field in ("give", "receive"):
            bundle = domestic_trade.get(field)
            if isinstance(bundle, dict):
                domestic_trade[field] = {key: 0 for key in bundle}

    # Checkpoints use true VP totals for the local post-game graph.  Those
    # totals include unrevealed VP development cards, and historic checkpoints
    # cannot be reconstructed accurately from the current public score.  Omit
    # them for every live viewer (including the player themselves and
    # spectators) until the match is finished.
    match_finished = phase.get("name") == "finished"
    match_metrics = state.get("match_metrics")
    if isinstance(match_metrics, dict):
        event_limit = (
            MAX_FINISHED_MATCH_EVENTS
            if match_finished
            else MAX_LIVE_MATCH_EVENTS
        )
        important_events = match_metrics.get("important_events")
        if isinstance(important_events, list):
            match_metrics["important_events"] = important_events[-event_limit:]
        checkpoints = match_metrics.get("point_checkpoints")
        if not match_finished:
            match_metrics["point_checkpoints"] = []
        elif isinstance(checkpoints, list):
            match_metrics["point_checkpoints"] = checkpoints[
                -MAX_FINISHED_MATCH_EVENTS:
            ]

    history = state.get("history")
    if isinstance(history, dict):
        log_messages = history.get("log_messages")
        if isinstance(log_messages, list):
            history["log_messages"] = log_messages[-MAX_SNAPSHOT_LOG_MESSAGES:]
    ui_state = state.get("ui")
    if isinstance(ui_state, dict):
        # A receiver owns its own scroll position; retaining the host offset can
        # also point beyond the bounded network log tail.
        ui_state["log_scroll_offset"] = 0

    state["development_deck"] = {
        "remaining": len(state["development_deck"]),
    }
    return {
        "type": "state_snapshot",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "revision": revision,
        "viewer_player_index": viewer_player_index,
        "board_manifest": build_board_manifest(game),
        "state": state,
    }


def _public_variant_state_document(value):
    """Return the canonical public-only runtime variant document.

    ``serialize_game`` persists the authoritative full document, including its
    server-only ``private`` section.  Network snapshots must never copy that
    section, even for the player who caused the snapshot or for a finished
    match.  The central public-document parser validates the selected fields
    after the private field has been discarded.
    """

    if not isinstance(value, Mapping):
        raise NetworkProtocolError("variant_stateが不正です。")
    if set(value) != _FULL_VARIANT_STATE_FIELDS:
        raise NetworkProtocolError("variant_stateの完全文書が不正です。")
    public_document = {
        field: deepcopy(value[field]) for field in _PUBLIC_VARIANT_STATE_FIELDS
    }
    try:
        public_document = VariantState.from_public_document(
            public_document
        ).to_public_document()
    except (TypeError, ValueError) as exc:
        raise NetworkProtocolError("variant_stateが不正です。") from exc

    if not isinstance(public_document, Mapping) or set(public_document) != set(
        _PUBLIC_VARIANT_STATE_FIELDS
    ):
        raise NetworkProtocolError("variant_stateの公開文書が不正です。")
    return {
        field: deepcopy(public_document[field])
        for field in _PUBLIC_VARIANT_STATE_FIELDS
    }


def build_action_request(*, player_index, sequence, action, payload=None):
    _validated_nonnegative_integer(player_index, label="操作プレイヤー番号")
    _validated_nonnegative_integer(sequence, label="操作sequence")
    if action not in ("button", "board_click", "key"):
        raise NetworkProtocolError("未対応のネットワーク操作です。")
    if payload is not None and not isinstance(payload, dict):
        raise NetworkProtocolError("操作payloadが不正です。")
    return {
        "type": "action_request",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "player_index": player_index,
        "sequence": sequence,
        "action": action,
        "payload": payload or {},
    }


def _validate_command_json_value(value, *, depth, item_count, active_containers):
    item_count[0] += 1
    if item_count[0] > MAX_GAME_COMMAND_ITEMS:
        raise NetworkProtocolError("操作argsの項目数が多すぎます。")
    if depth > MAX_GAME_COMMAND_DEPTH:
        raise NetworkProtocolError("操作argsの入れ子が深すぎます。")

    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_JSON_INTEGER:
            raise NetworkProtocolError("操作argsの整数が大きすぎます。")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NetworkProtocolError("操作argsに有限でない数値があります。")
        return
    if isinstance(value, str):
        if len(value) > MAX_GAME_COMMAND_STRING_LENGTH:
            raise NetworkProtocolError("操作argsの文字列が長すぎます。")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise NetworkProtocolError("操作argsの文字列に制御文字があります。")
        return
    if not isinstance(value, (dict, list)):
        raise NetworkProtocolError("操作argsにJSONで表せない値があります。")

    container_id = id(value)
    if container_id in active_containers:
        raise NetworkProtocolError("操作argsに循環参照があります。")
    active_containers.add(container_id)
    try:
        if isinstance(value, list):
            if len(value) > MAX_GAME_COMMAND_COLLECTION_ITEMS:
                raise NetworkProtocolError("操作argsの配列が長すぎます。")
            for item in value:
                _validate_command_json_value(
                    item,
                    depth=depth + 1,
                    item_count=item_count,
                    active_containers=active_containers,
                )
            return

        if len(value) > MAX_GAME_COMMAND_DICT_KEYS:
            raise NetworkProtocolError("操作argsのobject項目が多すぎます。")
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise NetworkProtocolError("操作argsのキーが不正です。")
            if len(key) > MAX_GAME_COMMAND_KEY_LENGTH:
                raise NetworkProtocolError("操作argsのキーが長すぎます。")
            if any(ord(character) < 32 or ord(character) == 127 for character in key):
                raise NetworkProtocolError("操作argsのキーに制御文字があります。")
            _validate_command_json_value(
                item,
                depth=depth + 1,
                item_count=item_count,
                active_containers=active_containers,
            )
    finally:
        active_containers.remove(container_id)


def _validated_game_command_args(args):
    if args is None:
        return {}
    if not isinstance(args, dict):
        raise NetworkProtocolError("操作argsが不正です。")
    _validate_command_json_value(
        args,
        depth=0,
        item_count=[0],
        active_containers=set(),
    )
    try:
        encoded = json.dumps(
            args,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise NetworkProtocolError(f"操作argsをJSONへ変換できません: {exc}") from exc
    if len(encoded) > MAX_GAME_COMMAND_ARGS_BYTES:
        raise NetworkProtocolError("操作argsが大きすぎます。")
    # Round-trip to guarantee the returned payload contains only plain JSON
    # containers, even when a dict/list subclass was supplied by a caller.
    return json.loads(encoded.decode("utf-8"))


def build_game_command(*, sequence, expected_revision, command, args=None):
    """Build a transport-neutral, semantic game command.

    The authoritative server derives the acting player from the authenticated
    connection; a client-controlled player index is intentionally absent.
    """

    _validated_nonnegative_integer(
        sequence,
        label="操作sequence",
        maximum=MAX_SAFE_JSON_INTEGER,
    )
    _validated_nonnegative_integer(
        expected_revision,
        label="期待revision",
        maximum=MAX_SAFE_JSON_INTEGER,
    )
    # Semantic authorization belongs to the authoritative action router.  The
    # wire layer only accepts a bounded, language-neutral identifier so the
    # router can remain the single allowlist and protocol/action modules do not
    # form a dependency cycle.
    if not isinstance(command, str) or _GAME_COMMAND_PATTERN.fullmatch(command) is None:
        raise NetworkProtocolError("ゲーム操作commandが不正です。")
    return {
        "type": "game_command",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "sequence": sequence,
        "expected_revision": expected_revision,
        "command": command,
        "args": _validated_game_command_args(args),
    }


def encode_frame(message):
    if not isinstance(message, dict):
        raise NetworkProtocolError("送信メッセージはJSON objectである必要があります。")
    try:
        payload = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise NetworkProtocolError(f"JSONへ変換できません: {exc}") from exc
    if len(payload) > MAX_FRAME_BYTES:
        raise NetworkProtocolError("ネットワークメッセージが大きすぎます。")
    return _FRAME_HEADER.pack(len(payload)) + payload


class FrameDecoder:
    """Incrementally decode length-prefixed JSON frames from a TCP byte stream."""

    def __init__(self):
        self._buffer = bytearray()
        self._expected_size = None

    def feed(self, data):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise NetworkProtocolError("受信データはbytesである必要があります。")
        self._buffer.extend(data)
        messages = []
        while True:
            if self._expected_size is None:
                if len(self._buffer) < _FRAME_HEADER.size:
                    break
                self._expected_size = _FRAME_HEADER.unpack(
                    self._buffer[: _FRAME_HEADER.size]
                )[0]
                del self._buffer[: _FRAME_HEADER.size]
                if self._expected_size > MAX_FRAME_BYTES:
                    self.reset()
                    raise NetworkProtocolError("受信メッセージが許容サイズを超えています。")

            if len(self._buffer) < self._expected_size:
                break
            payload = bytes(self._buffer[: self._expected_size])
            del self._buffer[: self._expected_size]
            self._expected_size = None
            try:
                message = json.loads(
                    payload.decode("utf-8"),
                    parse_constant=_reject_nonfinite_json_constant,
                )
            except (UnicodeDecodeError, ValueError, RecursionError) as exc:
                self.reset()
                raise NetworkProtocolError(f"受信JSONが不正です: {exc}") from exc
            if not isinstance(message, dict):
                self.reset()
                raise NetworkProtocolError("受信JSONはobjectである必要があります。")
            protocol_version = message.get("protocol_version")
            if (
                isinstance(protocol_version, bool)
                or not isinstance(protocol_version, int)
                or protocol_version != NETWORK_PROTOCOL_VERSION
            ):
                self.reset()
                raise NetworkProtocolError("ネットワークプロトコルのversionが一致しません。")
            messages.append(message)
        return messages

    def reset(self):
        self._buffer.clear()
        self._expected_size = None


def _reject_nonfinite_json_constant(value):
    raise ValueError(f"finite JSON number required: {value}")
