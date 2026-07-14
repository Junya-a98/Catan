"""Small RFC 6455 primitives for the local browser transport.

The module deliberately contains no room, session, or game-rule logic.  It
validates a WebSocket upgrade and moves bounded JSON objects over an already
upgraded ``BaseHTTPRequestHandler`` ``rfile``/``wfile`` pair.  Browser-to-
server frames must be masked and complete; extensions, fragmented messages,
and binary messages are intentionally unsupported by this local MVP.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
import hashlib
import json
import threading
from typing import Any, BinaryIO


WEBSOCKET_VERSION = "13"
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DEFAULT_MAX_WEBSOCKET_MESSAGE_BYTES = 512 * 1024
MAX_CONTROL_PAYLOAD_BYTES = 125


class WebSocketOpcode(IntEnum):
    """RFC 6455 opcodes used by the dependency-free adapter."""

    CONTINUATION = 0x0
    TEXT = 0x1
    BINARY = 0x2
    CLOSE = 0x8
    PING = 0x9
    PONG = 0xA


_CONTROL_OPCODES = frozenset(
    {WebSocketOpcode.CLOSE, WebSocketOpcode.PING, WebSocketOpcode.PONG}
)


class WebSocketHandshakeError(ValueError):
    """An HTTP upgrade request that must be rejected before switching."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        response_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.response_headers = response_headers


class WebSocketProtocolError(ValueError):
    """A post-upgrade error that can be returned as a Close frame."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        close_code: int = 1002,
        close_reason: str = "protocol error",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.close_code = close_code
        self.close_reason = close_reason


class WebSocketEOF(EOFError):
    """The peer closed the TCP stream before another complete frame."""


@dataclass(frozen=True)
class WebSocketHandshake:
    """Validated information needed for an HTTP 101 response."""

    accept_key: str

    @property
    def response_headers(self) -> tuple[tuple[str, str], ...]:
        return (
            ("Upgrade", "websocket"),
            ("Connection", "Upgrade"),
            ("Sec-WebSocket-Accept", self.accept_key),
        )


@dataclass(frozen=True)
class WebSocketFrame:
    """One decoded frame before application-level interpretation."""

    final: bool
    opcode: WebSocketOpcode
    payload: bytes
    masked: bool


@dataclass(frozen=True)
class WebSocketEvent:
    """One validated JSON or control event delivered to a server loop."""

    kind: str
    message: dict[str, Any] | None = None
    payload: bytes = b""
    close_code: int | None = None
    reason: str = ""


def websocket_accept_key(client_key: str) -> str:
    """Return the RFC 6455 accept value for one validated client key."""

    key = _validated_client_key(client_key)
    digest = hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def validate_websocket_handshake(
    headers: Mapping[str, str],
    *,
    method: str = "GET",
) -> WebSocketHandshake:
    """Validate an RFC 6455 browser upgrade without trusting header casing.

    ``headers`` may be a regular mapping or ``BaseHTTPRequestHandler.headers``.
    The caller remains responsible for its existing Host, Origin, cookie, and
    session checks.  On success it should emit HTTP 101 plus
    :attr:`WebSocketHandshake.response_headers`.
    """

    if method != "GET":
        raise WebSocketHandshakeError(
            "invalid_method",
            "WebSocket upgrade requires GET",
            status=405,
            response_headers=(("Allow", "GET"),),
        )
    connection_tokens = _header_tokens(headers, "Connection")
    if "upgrade" not in connection_tokens:
        raise WebSocketHandshakeError(
            "missing_connection_upgrade",
            "Connection header must contain Upgrade",
        )
    upgrade_tokens = _header_tokens(headers, "Upgrade")
    if upgrade_tokens != {"websocket"}:
        raise WebSocketHandshakeError(
            "invalid_upgrade",
            "Upgrade header must be websocket",
        )
    version_values = _header_values(headers, "Sec-WebSocket-Version")
    if len(version_values) != 1 or version_values[0].strip() != WEBSOCKET_VERSION:
        raise WebSocketHandshakeError(
            "unsupported_version",
            "Sec-WebSocket-Version must be 13",
            status=426,
            response_headers=(("Sec-WebSocket-Version", WEBSOCKET_VERSION),),
        )
    key_values = _header_values(headers, "Sec-WebSocket-Key")
    if len(key_values) != 1:
        raise WebSocketHandshakeError(
            "invalid_key",
            "Exactly one Sec-WebSocket-Key is required",
        )
    return WebSocketHandshake(websocket_accept_key(key_values[0]))


def encode_websocket_frame(
    payload: bytes = b"",
    *,
    opcode: WebSocketOpcode | int,
    final: bool = True,
    masking_key: bytes | None = None,
) -> bytes:
    """Encode one frame; ``masking_key`` is only for clients and tests."""

    try:
        validated_opcode = WebSocketOpcode(opcode)
    except (TypeError, ValueError) as exc:
        raise ValueError("opcode is not supported") from exc
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if validated_opcode in _CONTROL_OPCODES:
        if not final:
            raise ValueError("control frames cannot be fragmented")
        if len(payload) > MAX_CONTROL_PAYLOAD_BYTES:
            raise ValueError("control frame payload is too large")
    if masking_key is not None and (
        not isinstance(masking_key, bytes) or len(masking_key) != 4
    ):
        raise ValueError("masking_key must contain exactly four bytes")

    first = (0x80 if final else 0) | int(validated_opcode)
    masked_bit = 0x80 if masking_key is not None else 0
    length = len(payload)
    if length <= 125:
        header = bytes((first, masked_bit | length))
    elif length <= 0xFFFF:
        header = bytes((first, masked_bit | 126)) + length.to_bytes(2, "big")
    elif length <= 0x7FFF_FFFF_FFFF_FFFF:
        header = bytes((first, masked_bit | 127)) + length.to_bytes(8, "big")
    else:
        raise ValueError("payload is too large for RFC 6455")
    if masking_key is None:
        return header + payload
    masked_payload = bytes(
        byte ^ masking_key[index % 4] for index, byte in enumerate(payload)
    )
    return header + masking_key + masked_payload


def read_websocket_frame(
    reader: BinaryIO,
    *,
    require_mask: bool | None = None,
    max_payload_bytes: int = DEFAULT_MAX_WEBSOCKET_MESSAGE_BYTES,
) -> WebSocketFrame:
    """Read one bounded frame from a binary stream.

    Set ``require_mask=True`` for browser input and ``False`` for server
    output.  A length over the configured limit is rejected before allocating
    or reading the payload.
    """

    limit = _validated_size_limit(max_payload_bytes)
    header = _read_exact(reader, 2)
    first, second = header
    if first & 0x70:
        raise WebSocketProtocolError(
            "reserved_bits",
            "RSV bits require a negotiated extension",
            close_reason="extensions unsupported",
        )
    final = bool(first & 0x80)
    try:
        opcode = WebSocketOpcode(first & 0x0F)
    except ValueError as exc:
        raise WebSocketProtocolError(
            "invalid_opcode", "Frame opcode is reserved"
        ) from exc
    masked = bool(second & 0x80)
    if require_mask is not None and masked is not require_mask:
        requirement = "masked" if require_mask else "unmasked"
        raise WebSocketProtocolError(
            "invalid_mask",
            f"Frame must be {requirement}",
            close_reason="invalid masking",
        )

    length_code = second & 0x7F
    if opcode in _CONTROL_OPCODES and length_code > MAX_CONTROL_PAYLOAD_BYTES:
        raise WebSocketProtocolError(
            "control_too_large",
            "Control frames may not use extended lengths",
            close_reason="control frame too large",
        )
    if length_code == 126:
        payload_length = int.from_bytes(_read_exact(reader, 2), "big")
        if payload_length < 126:
            raise WebSocketProtocolError(
                "noncanonical_length",
                "16-bit length encoding was not minimal",
                close_reason="invalid frame length",
            )
    elif length_code == 127:
        raw_length = _read_exact(reader, 8)
        if raw_length[0] & 0x80:
            raise WebSocketProtocolError(
                "invalid_length",
                "64-bit frame length must be non-negative",
                close_reason="invalid frame length",
            )
        payload_length = int.from_bytes(raw_length, "big")
        if payload_length < 65_536:
            raise WebSocketProtocolError(
                "noncanonical_length",
                "64-bit length encoding was not minimal",
                close_reason="invalid frame length",
            )
    else:
        payload_length = length_code
    if opcode in _CONTROL_OPCODES and not final:
        raise WebSocketProtocolError(
            "fragmented_control",
            "Control frames must not be fragmented",
            close_reason="fragmented control",
        )
    payload_limit = MAX_CONTROL_PAYLOAD_BYTES if opcode in _CONTROL_OPCODES else limit
    if payload_length > payload_limit:
        raise WebSocketProtocolError(
            "message_too_large",
            "WebSocket payload exceeds the configured limit",
            close_code=1009,
            close_reason="message too large",
        )

    masking_key = _read_exact(reader, 4) if masked else None
    payload = _read_exact(reader, payload_length)
    if masking_key is not None:
        payload = bytes(
            byte ^ masking_key[index % 4] for index, byte in enumerate(payload)
        )
    return WebSocketFrame(final, opcode, payload, masked)


def read_client_event(
    reader: BinaryIO,
    *,
    max_message_bytes: int = DEFAULT_MAX_WEBSOCKET_MESSAGE_BYTES,
) -> WebSocketEvent:
    """Read one complete, masked browser frame as a JSON/control event."""

    frame = read_websocket_frame(
        reader,
        require_mask=True,
        max_payload_bytes=max_message_bytes,
    )
    if frame.opcode is WebSocketOpcode.CONTINUATION:
        raise WebSocketProtocolError(
            "unexpected_continuation",
            "Continuation frames are unsupported",
            close_reason="fragmentation unsupported",
        )
    if not frame.final:
        raise WebSocketProtocolError(
            "fragmented_message",
            "Fragmented data messages are unsupported",
            close_code=1003,
            close_reason="fragmentation unsupported",
        )
    if frame.opcode is WebSocketOpcode.BINARY:
        raise WebSocketProtocolError(
            "binary_unsupported",
            "Binary messages are unsupported",
            close_code=1003,
            close_reason="binary unsupported",
        )
    if frame.opcode is WebSocketOpcode.TEXT:
        return WebSocketEvent(
            "message",
            message=_decode_json_object(frame.payload),
            payload=frame.payload,
        )
    if frame.opcode is WebSocketOpcode.PING:
        return WebSocketEvent("ping", payload=frame.payload)
    if frame.opcode is WebSocketOpcode.PONG:
        return WebSocketEvent("pong", payload=frame.payload)
    if frame.opcode is WebSocketOpcode.CLOSE:
        close_code, reason = _decode_close_payload(frame.payload)
        return WebSocketEvent(
            "close",
            payload=frame.payload,
            close_code=close_code,
            reason=reason,
        )
    raise WebSocketProtocolError("invalid_opcode", "Unsupported frame opcode")


class WebSocketConnection:
    """Thread-safe writes plus validated reads for one upgraded HTTP socket."""

    def __init__(
        self,
        reader: BinaryIO,
        writer: BinaryIO,
        *,
        max_message_bytes: int = DEFAULT_MAX_WEBSOCKET_MESSAGE_BYTES,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.max_message_bytes = _validated_size_limit(max_message_bytes)
        self.close_sent = False
        self.close_received = False
        self._write_lock = threading.Lock()

    def receive(self) -> WebSocketEvent:
        event = read_client_event(
            self.reader,
            max_message_bytes=self.max_message_bytes,
        )
        if event.kind == "close":
            self.close_received = True
        return event

    def send_json(self, message: Mapping[str, Any]) -> None:
        payload = _encode_json_object(message, self.max_message_bytes)
        self._send_frame(WebSocketOpcode.TEXT, payload)

    def send_pong(self, payload: bytes = b"") -> None:
        if len(payload) > MAX_CONTROL_PAYLOAD_BYTES:
            raise ValueError("pong payload is too large")
        self._send_frame(WebSocketOpcode.PONG, payload)

    def send_close(self, code: int = 1000, reason: str = "") -> bool:
        """Send one Close frame, returning false if one was already sent."""

        payload = _encode_close_payload(code, reason)
        with self._write_lock:
            if self.close_sent:
                return False
            self._write_encoded(
                encode_websocket_frame(payload, opcode=WebSocketOpcode.CLOSE)
            )
            self.close_sent = True
            return True

    def send_protocol_error(self, error: WebSocketProtocolError) -> bool:
        """Convert a safe parser error into its RFC Close response."""

        return self.send_close(error.close_code, error.close_reason)

    def handle_control(self, event: WebSocketEvent) -> bool:
        """Reply to Ping/Close; return true when the loop should terminate."""

        if event.kind == "ping":
            self.send_pong(event.payload)
            return False
        if event.kind == "pong":
            return False
        if event.kind == "close":
            self.send_close(event.close_code or 1000, event.reason)
            return True
        raise ValueError("event is not a WebSocket control event")

    def _send_frame(self, opcode: WebSocketOpcode, payload: bytes) -> None:
        with self._write_lock:
            if self.close_sent:
                raise WebSocketEOF("WebSocket Close frame was already sent")
            self._write_encoded(encode_websocket_frame(payload, opcode=opcode))

    def _write_encoded(self, frame: bytes) -> None:
        written = self.writer.write(frame)
        if written is not None and written != len(frame):
            raise OSError("short WebSocket frame write")
        flush = getattr(self.writer, "flush", None)
        if callable(flush):
            flush()


def _validated_client_key(value: str) -> str:
    if not isinstance(value, str):
        raise WebSocketHandshakeError("invalid_key", "Sec-WebSocket-Key must be text")
    key = value.strip()
    try:
        decoded = base64.b64decode(key.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise WebSocketHandshakeError(
            "invalid_key", "Sec-WebSocket-Key is not valid base64"
        ) from exc
    if len(decoded) != 16 or base64.b64encode(decoded).decode("ascii") != key:
        raise WebSocketHandshakeError(
            "invalid_key", "Sec-WebSocket-Key must encode exactly 16 bytes"
        )
    return key


def _header_values(headers: Mapping[str, str], name: str) -> tuple[str, ...]:
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        values = get_all(name, [])
        return tuple(str(value) for value in values)
    return tuple(
        str(value)
        for key, value in headers.items()
        if isinstance(key, str) and key.casefold() == name.casefold()
    )


def _header_tokens(headers: Mapping[str, str], name: str) -> set[str]:
    return {
        token.strip().casefold()
        for value in _header_values(headers, name)
        for token in value.split(",")
        if token.strip()
    }


def _validated_size_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 64 * 1024 * 1024:
        raise ValueError("message size limit must be 1..67108864")
    return value


def _read_exact(reader: BinaryIO, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = reader.read(size - len(chunks))
        if not chunk:
            raise WebSocketEOF("WebSocket stream ended during a frame")
        chunks.extend(chunk)
    return bytes(chunks)


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite number is not valid JSON: {value}")


def _decode_json_object(payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise WebSocketProtocolError(
            "invalid_utf8",
            "Text message is not valid UTF-8",
            close_code=1007,
            close_reason="invalid UTF-8",
        ) from exc
    try:
        document = json.loads(text, parse_constant=_reject_non_finite_json)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise WebSocketProtocolError(
            "invalid_json",
            "Text message is not strict JSON",
            close_code=1007,
            close_reason="invalid JSON",
        ) from exc
    if type(document) is not dict:
        raise WebSocketProtocolError(
            "invalid_message",
            "WebSocket message must be a JSON object",
            close_code=1007,
            close_reason="JSON object required",
        )
    return document


def _encode_json_object(message: Mapping[str, Any], limit: int) -> bytes:
    if not isinstance(message, Mapping):
        raise TypeError("WebSocket message must be a mapping")
    try:
        payload = json.dumps(
            dict(message),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("WebSocket message is not strict JSON") from exc
    if len(payload) > limit:
        raise ValueError("WebSocket message exceeds the configured limit")
    return payload


def _valid_close_code(code: int) -> bool:
    if type(code) is not int:
        return False
    if code in {1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014}:
        return True
    return 3000 <= code <= 4999


def _decode_close_payload(payload: bytes) -> tuple[int | None, str]:
    if not payload:
        return None, ""
    if len(payload) == 1:
        raise WebSocketProtocolError(
            "invalid_close",
            "Close payload cannot contain only one byte",
            close_reason="invalid close frame",
        )
    close_code = int.from_bytes(payload[:2], "big")
    if not _valid_close_code(close_code):
        raise WebSocketProtocolError(
            "invalid_close_code",
            "Close code is reserved or invalid",
            close_reason="invalid close code",
        )
    try:
        reason = payload[2:].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise WebSocketProtocolError(
            "invalid_close_reason",
            "Close reason is not valid UTF-8",
            close_code=1007,
            close_reason="invalid close reason",
        ) from exc
    return close_code, reason


def _encode_close_payload(code: int, reason: str) -> bytes:
    if not _valid_close_code(code):
        raise ValueError("close code is reserved or invalid")
    if not isinstance(reason, str):
        raise TypeError("close reason must be text")
    payload = code.to_bytes(2, "big") + reason.encode("utf-8")
    if len(payload) > MAX_CONTROL_PAYLOAD_BYTES:
        raise ValueError("close reason is too large")
    return payload
