from io import BytesIO
import json

import pytest

from game.websocket_transport import (
    WebSocketConnection,
    WebSocketEOF,
    WebSocketHandshakeError,
    WebSocketOpcode,
    WebSocketProtocolError,
    encode_websocket_frame,
    read_client_event,
    read_websocket_frame,
    validate_websocket_handshake,
    websocket_accept_key,
)


CLIENT_MASK = b"mask"


def client_frame(payload=b"", *, opcode=WebSocketOpcode.TEXT, final=True):
    return encode_websocket_frame(
        payload,
        opcode=opcode,
        final=final,
        masking_key=CLIENT_MASK,
    )


def test_rfc_handshake_example_and_response_headers():
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    assert websocket_accept_key(key) == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="

    handshake = validate_websocket_handshake(
        {
            "connection": "keep-alive, Upgrade",
            "upgrade": "websocket",
            "sec-websocket-version": "13",
            "sec-websocket-key": key,
        }
    )
    assert handshake.response_headers == (
        ("Upgrade", "websocket"),
        ("Connection", "Upgrade"),
        ("Sec-WebSocket-Accept", "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="),
    )


@pytest.mark.parametrize(
    ("headers", "code", "status"),
    [
        ({}, "missing_connection_upgrade", 400),
        (
            {
                "Connection": "Upgrade",
                "Upgrade": "websocket",
                "Sec-WebSocket-Version": "12",
                "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
            },
            "unsupported_version",
            426,
        ),
        (
            {
                "Connection": "Upgrade",
                "Upgrade": "websocket",
                "Sec-WebSocket-Version": "13",
                "Sec-WebSocket-Key": "not-base64",
            },
            "invalid_key",
            400,
        ),
    ],
)
def test_handshake_rejects_missing_or_invalid_requirements(headers, code, status):
    with pytest.raises(WebSocketHandshakeError) as caught:
        validate_websocket_handshake(headers)
    assert caught.value.code == code
    assert caught.value.status == status


def test_masked_text_json_object_roundtrip_is_bounded_and_unmasked_on_output():
    document = {"type": "ping", "nonce": "こんにちは"}
    incoming = client_frame(json.dumps(document, ensure_ascii=False).encode("utf-8"))
    output = BytesIO()
    connection = WebSocketConnection(BytesIO(incoming), output)

    event = connection.receive()
    assert event.kind == "message"
    assert event.message == document

    connection.send_json({"type": "pong", "nonce": event.message["nonce"]})
    frame = read_websocket_frame(BytesIO(output.getvalue()), require_mask=False)
    assert frame.opcode is WebSocketOpcode.TEXT
    assert json.loads(frame.payload) == {"type": "pong", "nonce": "こんにちは"}


def test_unmasked_browser_frame_is_rejected():
    frame = encode_websocket_frame(b"{}", opcode=WebSocketOpcode.TEXT)
    with pytest.raises(WebSocketProtocolError) as caught:
        read_client_event(BytesIO(frame))
    assert caught.value.code == "invalid_mask"
    assert caught.value.close_code == 1002


def test_fragmented_and_continuation_messages_are_rejected():
    fragmented = client_frame(b"{}", final=False)
    with pytest.raises(WebSocketProtocolError) as caught:
        read_client_event(BytesIO(fragmented))
    assert caught.value.code == "fragmented_message"
    assert caught.value.close_code == 1003

    continuation = client_frame(b"{}", opcode=WebSocketOpcode.CONTINUATION)
    with pytest.raises(WebSocketProtocolError) as caught:
        read_client_event(BytesIO(continuation))
    assert caught.value.code == "unexpected_continuation"


def test_declared_oversized_message_is_rejected_before_payload_read():
    # Masked 16-bit payload header declares 512 bytes but intentionally omits
    # its mask and payload.  Size validation must win over an EOF allocation.
    header_only = bytes((0x81, 0x80 | 126)) + (512).to_bytes(2, "big")
    with pytest.raises(WebSocketProtocolError) as caught:
        read_client_event(BytesIO(header_only), max_message_bytes=128)
    assert caught.value.code == "message_too_large"
    assert caught.value.close_code == 1009


def test_binary_and_invalid_text_documents_are_rejected():
    with pytest.raises(WebSocketProtocolError) as caught:
        read_client_event(
            BytesIO(client_frame(b"bytes", opcode=WebSocketOpcode.BINARY))
        )
    assert caught.value.code == "binary_unsupported"
    assert caught.value.close_code == 1003

    for payload, expected_code in (
        (b"\xff", "invalid_utf8"),
        (b"[1,2]", "invalid_message"),
        (b'{"value":NaN}', "invalid_json"),
    ):
        with pytest.raises(WebSocketProtocolError) as caught:
            read_client_event(BytesIO(client_frame(payload)))
        assert caught.value.code == expected_code
        assert caught.value.close_code == 1007


def test_ping_is_echoed_as_an_unmasked_pong():
    output = BytesIO()
    connection = WebSocketConnection(
        BytesIO(client_frame(b"alive", opcode=WebSocketOpcode.PING)),
        output,
    )

    event = connection.receive()
    assert event.kind == "ping"
    assert connection.handle_control(event) is False

    pong = read_websocket_frame(BytesIO(output.getvalue()), require_mask=False)
    assert pong.opcode is WebSocketOpcode.PONG
    assert pong.payload == b"alive"


def test_control_payload_limit_is_independent_of_text_message_limit():
    ping = client_frame(b"ok", opcode=WebSocketOpcode.PING)
    event = read_client_event(BytesIO(ping), max_message_bytes=1)
    assert event.kind == "ping"
    assert event.payload == b"ok"


def test_close_is_validated_echoed_once_and_stops_the_loop():
    close_payload = (1000).to_bytes(2, "big") + "終了".encode("utf-8")
    output = BytesIO()
    connection = WebSocketConnection(
        BytesIO(client_frame(close_payload, opcode=WebSocketOpcode.CLOSE)),
        output,
    )

    event = connection.receive()
    assert event.kind == "close"
    assert event.close_code == 1000
    assert event.reason == "終了"
    assert connection.close_received is True
    assert connection.handle_control(event) is True
    assert connection.send_close() is False

    close = read_websocket_frame(BytesIO(output.getvalue()), require_mask=False)
    assert close.opcode is WebSocketOpcode.CLOSE
    assert close.payload == close_payload


def test_protocol_error_can_be_sent_as_a_safe_close_frame():
    output = BytesIO()
    connection = WebSocketConnection(BytesIO(), output)
    error = WebSocketProtocolError(
        "too_large",
        "private parser detail",
        close_code=1009,
        close_reason="message too large",
    )

    assert connection.send_protocol_error(error) is True
    frame = read_websocket_frame(BytesIO(output.getvalue()), require_mask=False)
    assert frame.opcode is WebSocketOpcode.CLOSE
    assert int.from_bytes(frame.payload[:2], "big") == 1009
    assert frame.payload[2:].decode("utf-8") == "message too large"
    with pytest.raises(WebSocketEOF):
        connection.send_json({"type": "late"})


def test_truncated_frame_and_invalid_close_payload_are_rejected():
    with pytest.raises(WebSocketEOF):
        read_client_event(BytesIO(b"\x81"))

    with pytest.raises(WebSocketProtocolError) as caught:
        read_client_event(BytesIO(client_frame(b"\x03", opcode=WebSocketOpcode.CLOSE)))
    assert caught.value.code == "invalid_close"
