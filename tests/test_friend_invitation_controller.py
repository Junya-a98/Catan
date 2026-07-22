import pytest

from game.friend_invitation import FriendInvitationAuthenticationError
from game.lan_controller import LanControllerError, LanServerController
from game.network_protocol import NETWORK_PROTOCOL_VERSION
from game.server_state import SQLiteRoomAuthorityStore


PASSPHRASE = "private friends catan room"
WALL_CLOCK_MS = 1_900_000_000_000


def message(message_type, **payload):
    return {
        "type": message_type,
        "protocol_version": NETWORK_PROTOCOL_VERSION,
        **payload,
    }


def create_protected_room(controller, *, player_count=3):
    outbound = controller.handle(
        "host",
        message(
            "create_room",
            display_name="Host",
            settings={
                "player_count": player_count,
                "victory_target": 10,
                "board_mode": "constrained",
                "board_seed": 86712347,
            },
            passphrase=PASSPHRASE,
        ),
        protected_room_access_allowed=True,
    )
    return next(
        item.message["room_code"]
        for item in outbound
        if item.message["type"] == "session_welcome"
    )


def welcome(outbound):
    return next(
        item.message for item in outbound if item.message["type"] == "session_welcome"
    )


def test_host_issues_and_inspects_role_scoped_invite_without_mutating_membership():
    controller = LanServerController(wall_clock_ms=lambda: WALL_CLOCK_MS)
    room_code = create_protected_room(controller)

    grant = controller.issue_friend_invitation(
        "host",
        role="player",
        ttl_seconds=900,
    )
    claim = controller.inspect_friend_invitation(room_code, grant.token)

    assert claim.room_id == grant.room_id
    assert claim.role == grant.role == "player"
    assert claim.issued_at_ms == WALL_CLOCK_MS
    assert claim.expires_at_ms == WALL_CLOCK_MS + 900_000
    assert controller._rooms[room_code].lobby.public_snapshot()["player_members"] == 1


def test_only_host_can_issue_and_player_invite_stops_after_match_start():
    controller = LanServerController(wall_clock_ms=lambda: WALL_CLOCK_MS)
    room_code = create_protected_room(controller, player_count=2)
    player = controller.issue_friend_invitation(
        "host", role="player", ttl_seconds=300
    )
    controller.join_room_with_friend_invitation(
        "guest",
        room_code=room_code,
        display_name="Guest",
        invite_token=player.token,
        expected_room_id=player.room_id,
    )
    with pytest.raises(LanControllerError) as denied:
        controller.issue_friend_invitation(
            "guest", role="spectator", ttl_seconds=300
        )
    assert denied.value.code == "forbidden"

    controller.handle("host", message("set_ready", ready=True))
    controller.handle("guest", message("set_ready", ready=True))
    controller.handle("host", message("start_game"))
    with pytest.raises(LanControllerError) as late_player:
        controller.issue_friend_invitation(
            "host", role="player", ttl_seconds=300
        )
    assert late_player.value.code == "invalid_state"
    assert controller.issue_friend_invitation(
        "host", role="spectator", ttl_seconds=300
    ).role == "spectator"


def test_invitation_bypasses_hidden_passphrase_once_and_server_owns_role():
    controller = LanServerController(wall_clock_ms=lambda: WALL_CLOCK_MS)
    room_code = create_protected_room(controller)
    grant = controller.issue_friend_invitation(
        "host", role="spectator", ttl_seconds=300
    )

    joined = controller.join_room_with_friend_invitation(
        "viewer",
        room_code=room_code,
        display_name="Viewer",
        invite_token=grant.token,
        expected_room_id=grant.room_id,
    )
    assert welcome(joined)["role"] == "spectator"
    assert welcome(joined)["seat_index"] is None

    with pytest.raises(LanControllerError) as replayed:
        controller.join_room_with_friend_invitation(
            "attacker",
            room_code=room_code,
            display_name="Attacker",
            invite_token=grant.token,
            expected_room_id=grant.room_id,
        )
    assert replayed.value.code == "authentication_failed"


def test_room_instance_mismatch_and_code_reuse_style_attack_fail_generically():
    controller = LanServerController(wall_clock_ms=lambda: WALL_CLOCK_MS)
    room_code = create_protected_room(controller)
    grant = controller.issue_friend_invitation(
        "host", role="player", ttl_seconds=300
    )

    with pytest.raises(LanControllerError) as mismatch:
        controller.join_room_with_friend_invitation(
            "guest",
            room_code=room_code,
            display_name="Guest",
            invite_token=grant.token,
            expected_room_id="0" * 32,
        )
    assert mismatch.value.code == "authentication_failed"
    assert controller.inspect_friend_invitation(room_code, grant.token).role == "player"

    with pytest.raises(LanControllerError) as missing:
        controller.inspect_friend_invitation("ZZZ999", grant.token)
    assert missing.value.code == "authentication_failed"


class FailingUpdateStore:
    def __init__(self, inner):
        self.inner = inner
        self.fail_updates = False

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def update_room(self, *args, **kwargs):
        if self.fail_updates:
            raise RuntimeError("simulated durable commit failure")
        return self.inner.update_room(*args, **kwargs)


def test_join_and_consumption_roll_back_together_on_persistence_failure(tmp_path):
    database = tmp_path / "authority.sqlite3"
    key = tmp_path / "authority.key"
    store_a = SQLiteRoomAuthorityStore(database, key_path=key)
    fault = FailingUpdateStore(store_a)
    first = LanServerController(
        state_store=fault,
        wall_clock_ms=lambda: WALL_CLOCK_MS,
    )
    room_code = create_protected_room(first)
    grant = first.issue_friend_invitation(
        "host", role="player", ttl_seconds=300
    )

    fault.fail_updates = True
    with pytest.raises(LanControllerError) as failed:
        first.join_room_with_friend_invitation(
            "guest",
            room_code=room_code,
            display_name="Guest",
            invite_token=grant.token,
            expected_room_id=grant.room_id,
        )
    assert failed.value.code == "persistence_unavailable"
    store_a.close()

    store_b = SQLiteRoomAuthorityStore(database, key_path=key)
    restored = LanServerController(
        state_store=store_b,
        wall_clock_ms=lambda: WALL_CLOCK_MS + 1_000,
    )
    assert restored.inspect_friend_invitation(room_code, grant.token).room_id == grant.room_id
    joined = restored.join_room_with_friend_invitation(
        "guest-restored",
        room_code=room_code,
        display_name="Guest",
        invite_token=grant.token,
        expected_room_id=grant.room_id,
    )
    assert welcome(joined)["role"] == "player"
    store_b.close()


def test_expired_invites_are_pruned_and_persisted_without_extending_room_ttl(tmp_path):
    now = {"value": WALL_CLOCK_MS}
    store = SQLiteRoomAuthorityStore(tmp_path / "authority.sqlite3")
    controller = LanServerController(
        state_store=store,
        wall_clock_ms=lambda: now["value"],
    )
    room_code = create_protected_room(controller)
    grant = controller.issue_friend_invitation(
        "host", role="spectator", ttl_seconds=300
    )
    before = store.get_room_by_code(room_code)
    now["value"] += 300_000

    controller.tick()
    after = store.get_room_by_code(room_code)
    assert after.expires_at_ms == before.expires_at_ms
    assert after.authority["friend_invitations"]["invitations"] == []
    with pytest.raises(LanControllerError) as expired:
        controller.inspect_friend_invitation(room_code, grant.token)
    assert expired.value.code == "authentication_failed"
    store.close()


def test_host_lists_and_revokes_token_free_invitations_in_canonical_order():
    controller = LanServerController(wall_clock_ms=lambda: WALL_CLOCK_MS)
    room_code = create_protected_room(controller)
    player = controller.issue_friend_invitation(
        "host", role="player", ttl_seconds=300
    )
    spectator = controller.issue_friend_invitation(
        "host", role="spectator", ttl_seconds=600
    )

    listed = controller.list_friend_invitations("host")
    assert [item.invitation_id for item in listed] == sorted(
        (player.invitation_id, spectator.invitation_id)
    )
    assert all(not hasattr(item, "token") for item in listed)
    assert player.token not in repr(listed)
    assert spectator.token not in repr(listed)

    revoked = controller.revoke_friend_invitation(
        "host", invitation_id=player.invitation_id
    )
    assert revoked.invitation_id == player.invitation_id
    with pytest.raises(LanControllerError) as rejected:
        controller.inspect_friend_invitation(room_code, player.token)
    assert rejected.value.code == "authentication_failed"
    assert controller.inspect_friend_invitation(room_code, spectator.token).role == (
        "spectator"
    )

    assert controller.revoke_all_friend_invitations("host") == 1
    assert controller.list_friend_invitations("host") == ()
    with pytest.raises(LanControllerError) as rejected_all:
        controller.inspect_friend_invitation(room_code, spectator.token)
    assert rejected_all.value.code == "authentication_failed"


def test_only_host_can_manage_invitations_and_unknown_ids_are_uniform():
    controller = LanServerController(wall_clock_ms=lambda: WALL_CLOCK_MS)
    room_code = create_protected_room(controller)
    viewer_invite = controller.issue_friend_invitation(
        "host", role="spectator", ttl_seconds=300
    )
    controller.join_room_with_friend_invitation(
        "viewer",
        room_code=room_code,
        display_name="Viewer",
        invite_token=viewer_invite.token,
        expected_room_id=viewer_invite.room_id,
    )

    for operation in (
        lambda: controller.list_friend_invitations("viewer"),
        lambda: controller.revoke_friend_invitation(
            "viewer", invitation_id=viewer_invite.invitation_id
        ),
        lambda: controller.revoke_all_friend_invitations("viewer"),
    ):
        with pytest.raises(LanControllerError) as denied:
            operation()
        assert denied.value.code == "forbidden"

    for unknown in ("A" * 22, "bad"):
        with pytest.raises(LanControllerError) as missing:
            controller.revoke_friend_invitation(
                "host", invitation_id=unknown
            )
        assert missing.value.code == "invitation_not_found"
        assert str(missing.value) == "招待を確認できませんでした。"


@pytest.mark.parametrize("revoke_all", (False, True))
def test_revoke_rolls_back_memory_and_durable_authority_on_failure(
    tmp_path,
    revoke_all,
):
    database = tmp_path / f"authority-{revoke_all}.sqlite3"
    key = tmp_path / f"authority-{revoke_all}.key"
    inner = SQLiteRoomAuthorityStore(database, key_path=key)
    fault = FailingUpdateStore(inner)
    controller = LanServerController(
        state_store=fault,
        wall_clock_ms=lambda: WALL_CLOCK_MS,
    )
    room_code = create_protected_room(controller)
    grant = controller.issue_friend_invitation(
        "host", role="player", ttl_seconds=300
    )
    durable_before = inner.get_room_by_code(room_code)

    fault.fail_updates = True
    with pytest.raises(LanControllerError) as failed:
        if revoke_all:
            controller.revoke_all_friend_invitations("host")
        else:
            controller.revoke_friend_invitation(
                "host", invitation_id=grant.invitation_id
            )
    assert failed.value.code == "persistence_unavailable"
    assert controller._rooms[room_code].friend_invitations.inspect(
        grant.token, now_ms=WALL_CLOCK_MS
    ).invitation_id == grant.invitation_id
    durable_after = inner.get_room_by_code(room_code)
    assert durable_after.generation == durable_before.generation
    assert durable_after.authority == durable_before.authority
    inner.close()


def test_list_expiry_prune_rolls_back_on_persistence_failure(tmp_path):
    now = {"value": WALL_CLOCK_MS}
    inner = SQLiteRoomAuthorityStore(tmp_path / "authority.sqlite3")
    fault = FailingUpdateStore(inner)
    controller = LanServerController(
        state_store=fault,
        wall_clock_ms=lambda: now["value"],
    )
    room_code = create_protected_room(controller)
    grant = controller.issue_friend_invitation(
        "host", role="spectator", ttl_seconds=300
    )
    durable_before = inner.get_room_by_code(room_code)
    now["value"] += 300_000
    fault.fail_updates = True

    with pytest.raises(LanControllerError) as failed:
        controller.list_friend_invitations("host")
    assert failed.value.code == "persistence_unavailable"
    assert controller._rooms[room_code].friend_invitations.invitation_count == 1
    durable_after = inner.get_room_by_code(room_code)
    assert durable_after.generation == durable_before.generation
    assert durable_after.authority == durable_before.authority
    # The bearer is expired (and therefore cannot authenticate) even though
    # rollback correctly retained its durable record for a later safe prune.
    with pytest.raises(FriendInvitationAuthenticationError):
        controller._rooms[room_code].friend_invitations.inspect(
            grant.token, now_ms=now["value"]
        )
    inner.close()


def test_stale_controller_revoke_fails_closed_under_generation_cas(tmp_path):
    database = tmp_path / "authority.sqlite3"
    key = tmp_path / "authority.key"
    store_a = SQLiteRoomAuthorityStore(database, key_path=key)
    first = LanServerController(
        state_store=store_a,
        wall_clock_ms=lambda: WALL_CLOCK_MS,
    )
    created = first.handle(
        "host",
        message(
            "create_room",
            display_name="Host",
            settings={
                "player_count": 3,
                "victory_target": 10,
                "board_mode": "constrained",
                "board_seed": 86712347,
            },
            passphrase=PASSPHRASE,
        ),
        protected_room_access_allowed=True,
    )
    host_welcome = welcome(created)
    room_code = host_welcome["room_code"]
    reconnect_token = host_welcome["reconnect_token"]
    grant = first.issue_friend_invitation(
        "host", role="spectator", ttl_seconds=300
    )

    # Restoring the same room advances its durable generation, deliberately
    # making the first controller stale before it attempts the revocation.
    store_b = SQLiteRoomAuthorityStore(database, key_path=key)
    second = LanServerController(
        state_store=store_b,
        wall_clock_ms=lambda: WALL_CLOCK_MS + 1_000,
    )
    reconnected = second.handle(
        "host-restored",
        message(
            "reconnect_room",
            room_code=room_code,
            reconnect_token=reconnect_token,
        ),
    )
    assert welcome(reconnected)["role"] == "host"

    with pytest.raises(LanControllerError) as stale:
        first.revoke_friend_invitation(
            "host", invitation_id=grant.invitation_id
        )
    assert stale.value.code == "persistence_unavailable"
    assert first._rooms[room_code].friend_invitations.inspect(
        grant.token, now_ms=WALL_CLOCK_MS + 1_000
    ).invitation_id == grant.invitation_id
    assert [item.invitation_id for item in second.list_friend_invitations(
        "host-restored"
    )] == [grant.invitation_id]
    store_b.close()
    store_a.close()
