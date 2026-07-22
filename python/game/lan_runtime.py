"""Runtime adapters that connect the LAN controller to framed TCP sockets."""

from __future__ import annotations

from collections import deque
import ipaddress
import threading
import time
from typing import Any, Mapping

from game.custom_map import CustomMapSpec
from game.house_rules import HouseRules
from game.lan_controller import LanServerController, OutboundMessage
from game.lan_lobby import RoomSettings
from game.lan_transport import (
    DEFAULT_LAN_HOST,
    DEFAULT_LAN_PORT,
    LanClientTransport,
    LanServerTransport,
    LanTransportError,
    LanTransportEvent,
)
from game.network_protocol import (
    NETWORK_PROTOCOL_VERSION,
    NetworkProtocolError,
    build_game_command,
)
from game.variant import VariantConfig


def _peer_is_loopback(peer: tuple[str, int] | None) -> bool:
    """Trust only an actual socket peer address, never a client-supplied host."""

    if not isinstance(peer, tuple) or len(peer) != 2 or type(peer[0]) is not str:
        return False
    try:
        return ipaddress.ip_address(peer[0]).is_loopback
    except ValueError:
        return False


class LanServerRuntime:
    """Own a TCP listener and drive an authoritative controller on one thread."""

    def __init__(
        self,
        host: str = DEFAULT_LAN_HOST,
        port: int = DEFAULT_LAN_PORT,
        *,
        controller: LanServerController | None = None,
        transport: LanServerTransport | None = None,
    ) -> None:
        if transport is not None and (
            host != DEFAULT_LAN_HOST or port != DEFAULT_LAN_PORT
        ):
            raise ValueError("host/port cannot be combined with a transport")
        self.controller = controller or LanServerController()
        self.transport = transport or LanServerTransport(host, port)
        self._last_tick = time.monotonic()

    @property
    def address(self) -> tuple[str, int]:
        return self.transport.address

    def start(self) -> tuple[str, int]:
        return self.transport.start()

    def pump(self, *, event_limit: int = 200) -> int:
        """Process queued socket events and return the number consumed."""

        events = self.transport.poll(limit=event_limit)
        for event in events:
            if event.kind == "message" and event.connection_id is not None:
                outbound = self.controller.handle(
                    event.connection_id,
                    event.message or {},
                    protected_room_access_allowed=_peer_is_loopback(event.peer),
                )
                self._send_all(outbound)
            elif event.kind == "disconnected" and event.connection_id is not None:
                self._send_all(self.controller.disconnect(event.connection_id))
        now = time.monotonic()
        if now - self._last_tick >= 1.0:
            self._send_all(self.controller.tick())
            self._last_tick = now
        return len(events)

    def run_forever(
        self,
        stop_event: threading.Event,
        *,
        poll_seconds: float = 0.01,
    ) -> None:
        """Run until ``stop_event``; intended for a standalone host process."""

        if not isinstance(stop_event, threading.Event):
            raise TypeError("stop_event must be threading.Event")
        if (
            isinstance(poll_seconds, bool)
            or not isinstance(poll_seconds, (int, float))
            or not 0 < poll_seconds <= 1
        ):
            raise ValueError("poll_seconds must be within (0, 1]")
        self.start()
        try:
            while not stop_event.is_set():
                self.pump()
                stop_event.wait(float(poll_seconds))
        finally:
            self.stop()

    def stop(self) -> None:
        self.transport.stop()

    def _send_all(self, outbound: tuple[OutboundMessage, ...]) -> None:
        pending = deque(outbound)
        disconnected: set[str] = set()
        while pending:
            item = pending.popleft()
            try:
                connection_id = int(item.connection_id)
            except (TypeError, ValueError):
                continue
            try:
                self.transport.send(connection_id, item.message)
            except (LanTransportError, NetworkProtocolError):
                self.transport.close_connection(connection_id)
                if item.connection_id in disconnected:
                    continue
                disconnected.add(item.connection_id)
                pending.extend(self.controller.disconnect(item.connection_id))

    def __enter__(self) -> LanServerRuntime:
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.stop()


class LanClientSession:
    """Stateful client facade for lobby, reconnect, and exactly-once commands."""

    def __init__(self, *, transport: LanClientTransport | None = None) -> None:
        self.transport = transport or LanClientTransport()
        self.room_code: str | None = None
        self.role: str | None = None
        self.seat_index: int | None = None
        self.reconnect_token: str | None = None
        self.lobby: dict[str, Any] | None = None
        self.game_snapshot: dict[str, Any] | None = None
        self.game_revision: int | None = None
        self.next_sequence = 0
        self.pending_commands: dict[int, dict[str, Any]] = {}
        self.command_results: dict[int, dict[str, Any]] = {}
        self.last_error: dict[str, Any] | None = None
        self._session_welcome_received = False
        self._session_synchronized = False
        self._required_snapshot_revision: int | None = None

    @property
    def is_connected(self) -> bool:
        return self.transport.is_connected

    @property
    def is_synchronized(self) -> bool:
        """Whether commands may use the current authoritative revision."""

        return self._session_synchronized

    def connect(self, host: str, port: int, *, timeout: float = 5.0) -> None:
        self._begin_session_sync()
        self.transport.connect(host, port, timeout=timeout)

    def create_room(
        self,
        display_name: str,
        *,
        player_count: int = 4,
        victory_target: int = 10,
        board_mode: str = "constrained",
        board_seed: int = 0,
        ai_player_count: int | None = None,
        ai_personality_mode: str | None = None,
        custom_map: CustomMapSpec | Mapping[str, Any] | None = None,
        house_rules: HouseRules | Mapping[str, Any] | None = None,
        variant: VariantConfig | Mapping[str, Any] | None = None,
        passphrase: str | None = None,
    ) -> None:
        self._require_loopback_for_passphrase(passphrase)
        include_ai_settings = (
            ai_player_count is not None or ai_personality_mode is not None
        )
        settings = RoomSettings(
            player_count=player_count,
            victory_target=victory_target,
            board_mode=board_mode,
            board_seed=board_seed,
            ai_player_count=0 if ai_player_count is None else ai_player_count,
            ai_personality_mode=(
                "standard" if ai_personality_mode is None else ai_personality_mode
            ),
            custom_map=custom_map,
            house_rules=house_rules,
            variant=variant,
        ).to_public_dict()
        if not include_ai_settings:
            settings.pop("ai_player_count", None)
            settings.pop("ai_personality_mode", None)
        if variant is None:
            # Older callers omit the field; the authority restores standard.
            settings.pop("variant", None)
        self._begin_session_sync()
        payload: dict[str, Any] = {
            "display_name": display_name,
            "settings": settings,
        }
        if passphrase is not None:
            payload["passphrase"] = passphrase
        self._send("create_room", **payload)

    def join_room(
        self,
        room_code: str,
        display_name: str,
        *,
        spectator: bool = False,
        passphrase: str | None = None,
    ) -> None:
        self._require_loopback_for_passphrase(passphrase)
        self._begin_session_sync()
        payload: dict[str, Any] = {
            "room_code": room_code,
            "display_name": display_name,
            "role": "spectator" if spectator else "player",
        }
        if passphrase is not None:
            payload["passphrase"] = passphrase
        self._send("join_room", **payload)

    def reconnect_room(
        self,
        room_code: str | None = None,
        reconnect_token: str | None = None,
    ) -> None:
        code = room_code or self.room_code
        token = reconnect_token or self.reconnect_token
        if not code or not token:
            raise LanTransportError("room code and reconnect token are required")
        # Keep the caller-supplied credential in memory.  A successful
        # reconnect welcome intentionally does not transmit it a second time.
        self.room_code = code
        self.reconnect_token = token
        self._begin_session_sync()
        self._send("reconnect_room", room_code=code, reconnect_token=token)

    def _require_loopback_for_passphrase(self, passphrase: str | None) -> None:
        if passphrase is None:
            return
        if not _peer_is_loopback(getattr(self.transport, "peer", None)):
            raise LanTransportError(
                "room passphrases require a loopback LAN connection; "
                "use the HTTPS/WSS Web transport for remote peers"
            )

    def set_ready(self, ready: bool = True) -> None:
        if not isinstance(ready, bool):
            raise ValueError("ready must be boolean")
        self._send("set_ready", ready=ready)

    def start_game(self) -> None:
        self._send("start_game")

    def leave_room(self) -> None:
        """Intentionally leave a waiting room without reserving the seat."""

        self._send("leave_room")

    def send_game_command(
        self,
        command: str,
        args: Mapping[str, Any] | None = None,
    ) -> int:
        if not self._session_synchronized:
            raise LanTransportError("LAN session synchronization is not complete")
        if self.role not in ("host", "player") or self.seat_index is None:
            raise LanTransportError("spectators cannot send game commands")
        if self.game_revision is None:
            raise LanTransportError("latest game snapshot is not available")
        sequence = self.next_sequence
        request = build_game_command(
            sequence=sequence,
            expected_revision=self.game_revision,
            command=command,
            args=dict(args) if args is not None else None,
        )
        self.transport.send(request)
        self.pending_commands[sequence] = request
        self.next_sequence += 1
        return sequence

    def resend_game_command(self, sequence: int) -> None:
        if not self._session_synchronized:
            raise LanTransportError("LAN session synchronization is not complete")
        if type(sequence) is not int or sequence not in self.pending_commands:
            raise NetworkProtocolError("再送する操作sequenceが見つかりません。")
        self.transport.send(self.pending_commands[sequence])

    def poll(self, *, limit: int = 100) -> list[LanTransportEvent]:
        events = self.transport.poll(limit=limit)
        for event in events:
            if event.kind == "disconnected":
                self._begin_session_sync()
                continue
            if event.kind != "message" or event.message is None:
                continue
            message = event.message
            message_type = message.get("type")
            if message_type == "session_welcome":
                self._session_synchronized = False
                self._session_welcome_received = True
                self.room_code = message.get("room_code")
                self.role = message.get("role")
                self.seat_index = message.get("seat_index")
                token = message.get("reconnect_token")
                if token:
                    self.reconnect_token = token
                next_sequence = message.get("next_sequence", 0)
                if type(next_sequence) is int and next_sequence >= 0:
                    # The authority may not have received the final command
                    # before a disconnect.  Its cursor is therefore the source
                    # of truth; retained pending commands can still be resent.
                    self.next_sequence = next_sequence
            elif message_type == "lobby_snapshot":
                lobby = message.get("lobby")
                if isinstance(lobby, dict):
                    self.lobby = lobby
                    self._sync_role_from_lobby(lobby)
            elif message_type == "state_snapshot":
                revision = message.get("revision")
                if type(revision) is int and revision >= 0:
                    accepted_snapshot = (
                        self.game_revision is None or revision >= self.game_revision
                    )
                    if accepted_snapshot:
                        self.game_revision = revision
                        self.game_snapshot = message
                    required = self._required_snapshot_revision
                    if (
                        accepted_snapshot
                        and self._session_welcome_received
                        and (required is None or revision >= required)
                    ):
                        self._required_snapshot_revision = None
                        self._session_synchronized = True
            elif message_type == "game_command_result":
                sequence = message.get("sequence")
                revision = message.get("revision")
                if type(sequence) is int and sequence >= 0:
                    self.command_results[sequence] = message
                    self.pending_commands.pop(sequence, None)
                    accepted = message.get("accepted")
                    code = message.get("code")
                    if accepted is True or (
                        accepted is False
                        and code
                        not in {
                            "sequence_conflict",
                            "sequence_expired",
                            "sequence_gap",
                        }
                    ):
                        self.next_sequence = max(
                            self.next_sequence,
                            sequence + 1,
                        )
                if type(revision) is int and revision >= 0:
                    snapshot_revision = (
                        self.game_snapshot.get("revision")
                        if isinstance(self.game_snapshot, dict)
                        else None
                    )
                    if (
                        type(snapshot_revision) is not int
                        or snapshot_revision < revision
                    ):
                        required = self._required_snapshot_revision
                        self._required_snapshot_revision = max(
                            revision,
                            required if required is not None else revision,
                        )
                        self._session_synchronized = False
            elif message_type == "request_error":
                self.last_error = message
        return events

    def close(self) -> None:
        self._begin_session_sync()
        self.transport.close()

    def _begin_session_sync(self) -> None:
        self._session_welcome_received = False
        self._session_synchronized = False
        self._required_snapshot_revision = None

    def _sync_role_from_lobby(self, lobby: Mapping[str, Any]) -> None:
        """Keep the public session role aligned after host promotion."""

        if type(self.seat_index) is not int:
            return
        members = lobby.get("members")
        if not isinstance(members, list):
            return
        seat = self.seat_index + 1
        for member in members:
            if not isinstance(member, dict) or member.get("seat") != seat:
                continue
            role = member.get("role")
            if role in ("host", "player"):
                self.role = role
            return

    def _send(self, message_type: str, **payload: Any) -> None:
        self.transport.send(
            {
                "type": message_type,
                "protocol_version": NETWORK_PROTOCOL_VERSION,
                **payload,
            }
        )

    def __enter__(self) -> LanClientSession:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


__all__ = (
    "LanClientSession",
    "LanServerRuntime",
)
