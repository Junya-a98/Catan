import json
import struct
from copy import deepcopy

from game.persistence import serialize_game


NETWORK_PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 2 * 1024 * 1024
_FRAME_HEADER = struct.Struct("!I")


class NetworkProtocolError(ValueError):
    pass


def build_state_snapshot(game, *, viewer_player_index=None, revision=0):
    """Build a viewer-specific state without leaking other players' private cards."""
    state = deepcopy(serialize_game(game))
    player_count = len(state["players"])
    if viewer_player_index is not None and (
        not isinstance(viewer_player_index, int)
        or not 0 <= viewer_player_index < player_count
    ):
        raise NetworkProtocolError("閲覧プレイヤー番号が不正です。")
    if not isinstance(revision, int) or revision < 0:
        raise NetworkProtocolError("同期revisionが不正です。")

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

    state["development_deck"] = {
        "remaining": len(state["development_deck"]),
    }
    return {
        "type": "state_snapshot",
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        "revision": revision,
        "viewer_player_index": viewer_player_index,
        "state": state,
    }


def build_action_request(*, player_index, sequence, action, payload=None):
    if not isinstance(player_index, int) or player_index < 0:
        raise NetworkProtocolError("操作プレイヤー番号が不正です。")
    if not isinstance(sequence, int) or sequence < 0:
        raise NetworkProtocolError("操作sequenceが不正です。")
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


def encode_frame(message):
    if not isinstance(message, dict):
        raise NetworkProtocolError("送信メッセージはJSON objectである必要があります。")
    try:
        payload = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
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
                message = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.reset()
                raise NetworkProtocolError(f"受信JSONが不正です: {exc}") from exc
            if not isinstance(message, dict):
                self.reset()
                raise NetworkProtocolError("受信JSONはobjectである必要があります。")
            if message.get("protocol_version") != NETWORK_PROTOCOL_VERSION:
                self.reset()
                raise NetworkProtocolError("ネットワークプロトコルのversionが一致しません。")
            messages.append(message)
        return messages

    def reset(self):
        self._buffer.clear()
        self._expected_size = None
