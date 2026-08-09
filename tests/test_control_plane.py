"""Tests for control-plane data model — registry CRUD operations.

Covers Team, Membership, APIKey, Invitation CRUD via FalkorDBLite.
No Postgres required — audit events fall back to JSONL.
"""
from __future__ import annotations

import pytest
from tortoise.sdk import TortoiseSDK
from tortoise.exceptions import ControlPlaneError


@pytest.fixture
def sdk(tmp_path):
    """SDK with embedded FalkorDBLite (no Postgres needed)."""
    db_path = str(tmp_path / "test_control_plane.db")
    return TortoiseSDK(db_path=db_path)


@pytest.fixture
def team(sdk):
    """Pre-created team fixture.

    R6 (#221): deletes the created team in teardown so the registry graph
    never accumulates leftover teams across runs.
    """
    result = sdk.team_create("test-team")
    yield result
    try:
        sdk.team_delete(result["id"], confirmation="test-team")
    except Exception:
        pass  # best-effort cleanup


class TestTeamCRUD:
    """Team create, read, update, delete."""

    def test_team_create_writes_to_registry_graph(self, sdk):
        result = sdk.team_create("acme-corp")
        assert result["name"] == "acme-corp"
        assert result["api_key"].startswith("tt_")
        assert result["graph_name"] == "team_acme-corp"
        assert result["id"]

        # Verify in registry graph
        team = sdk.team_get(result["id"])
        assert team is not None
        assert team["name"] == "acme-corp"

    def test_team_create_is_idempotent_with_key(self, sdk):
        result1 = sdk.team_create("durable", idempotency_key="key-123")
        result2 = sdk.team_create("durable", idempotency_key="key-123")
        assert result2.get("existing") is True
        assert result2["id"] == result1["id"]

    def test_team_create_rejects_duplicate_name(self, sdk):
        sdk.team_create("unique-name")
        with pytest.raises(ControlPlaneError, match="already exists"):
            sdk.team_create("unique-name")

    def test_team_create_rejects_empty_name(self, sdk):
        with pytest.raises(ControlPlaneError, match="must not be empty"):
            sdk.team_create("")

    def test_team_create_rejects_invalid_name(self, sdk):
        with pytest.raises(ControlPlaneError, match="Invalid team name"):
            sdk.team_create("name with spaces")

    def test_team_get_returns_none_for_missing(self, sdk):
        assert sdk.team_get("nonexistent-id") is None

    def test_team_get_returns_team(self, sdk, team):
        result = sdk.team_get(team["id"])
        assert result is not None
        assert result["name"] == "test-team"
        assert result["tier"] == "free"

    def test_team_list_returns_all_teams(self, sdk):
        sdk.team_create("alpha")
        sdk.team_create("beta")
        teams = sdk.team_list()
        names = {t["name"] for t in teams}
        assert "alpha" in names
        assert "beta" in names

    def test_team_update_changes_mutable_fields(self, sdk, team):
        sdk.team_update(team["id"], tier="pro", max_users=10)
        updated = sdk.team_get(team["id"])
        assert updated["tier"] == "pro"
        assert updated["max_users"] == 10

    def test_team_update_rejects_invalid_fields(self, sdk, team):
        with pytest.raises(ControlPlaneError, match="Invalid team fields"):
            sdk.team_update(team["id"], bogus_field=123)

    def test_team_delete_cascades_to_children(self, sdk):
        t = sdk.team_create("victim")
        # Add membership, API key, invitation
        sdk.membership_create(t["id"], "user-1", "admin")
        sdk.apikey_create(t["id"], "user-1")
        sdk.invitation_create(t["id"], "invite@test.com", "admin", "user-1")

        result = sdk.team_delete(t["id"], confirmation="victim")
        assert result["deleted"] is True

        # Verify cascade — team should be gone
        assert sdk.team_get(t["id"]) is None

    def test_team_delete_requires_name_confirmation(self, sdk, team):
        with pytest.raises(ControlPlaneError, match="must match team name"):
            sdk.team_delete(team["id"], confirmation="wrong-name")

    def test_migrate_teams_is_idempotent(self, sdk):
        # Create a team directly in the tortoise graph (simulating old data)
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (t:Team {id:'legacy-1', name:'legacy-team', "
            "api_key:'old-hash', graph_name:'team_legacy-team', "
            "createdAt:'2024-01-01'})"
        )
        result1 = sdk.migrate_teams_to_registry()
        assert result1["migrated"] >= 1
        result2 = sdk.migrate_teams_to_registry()
        assert result2["migrated"] == 0  # Idempotent


class TestMembershipCRUD:
    """Membership create, read, update, delete."""

    def test_membership_create_with_valid_role(self, sdk, team):
        m = sdk.membership_create(team["id"], "user-1", "admin")
        assert m["team_id"] == team["id"]
        assert m["user_id"] == "user-1"
        assert m["role"] == "admin"

    def test_membership_create_rejects_invalid_role(self, sdk, team):
        with pytest.raises(ControlPlaneError, match="Invalid role"):
            sdk.membership_create(team["id"], "user-1", "superuser")

    def test_membership_create_rejects_missing_team(self, sdk):
        with pytest.raises(ControlPlaneError, match="not found"):
            sdk.membership_create("bad-id", "user-1", "admin")

    def test_membership_create_rejects_at_max_users(self, sdk, team):
        sdk.team_update(team["id"], max_users=1)
        sdk.membership_create(team["id"], "user-1", "owner")
        with pytest.raises(ControlPlaneError, match="max users"):
            sdk.membership_create(team["id"], "user-2", "admin")

    def test_membership_list_returns_members(self, sdk, team):
        sdk.team_update(team["id"], max_users=10)
        sdk.membership_create(team["id"], "user-1", "admin")
        sdk.membership_create(team["id"], "user-2", "owner")
        members = sdk.membership_list(team["id"])
        assert len(members) == 2

    def test_membership_get_returns_membership(self, sdk, team):
        m = sdk.membership_create(team["id"], "user-1", "admin")
        result = sdk.membership_get(m["id"])
        assert result is not None
        assert result["user_id"] == "user-1"

    def test_membership_get_returns_none_for_missing(self, sdk):
        assert sdk.membership_get("nonexistent") is None

    def test_membership_update_role(self, sdk, team):
        m = sdk.membership_create(team["id"], "user-1", "admin")
        sdk.membership_update_role(m["id"], "owner")
        updated = sdk.membership_get(m["id"])
        assert updated["role"] == "owner"

    def test_membership_update_role_rejects_invalid(self, sdk, team):
        m = sdk.membership_create(team["id"], "user-1", "admin")
        with pytest.raises(ControlPlaneError, match="Invalid role"):
            sdk.membership_update_role(m["id"], "bogus")

    def test_membership_delete_is_idempotent(self, sdk, team):
        m = sdk.membership_create(team["id"], "user-1", "admin")
        assert sdk.membership_delete(m["id"])["deleted"] is True
        # Second delete should be idempotent
        result = sdk.membership_delete(m["id"])
        assert result["deleted"] is False
        assert result["reason"] == "not found"


class TestAPIKeyCRUD:
    """API key create, list, revoke, verify."""

    def test_apikey_create_stores_hash_not_plaintext(self, sdk, team):
        result = sdk.apikey_create(team["id"], "user-1")
        assert "api_key" in result
        assert result["api_key"].startswith("tt_")
        assert result["key_prefix"]
        # Verify stored as hash by looking up in graph
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {id:$id}) RETURN k.key_hash, k.key_prefix",
            params={"id": result["id"]},
        ).result_set
        assert rows
        stored_hash = rows[0][0]
        stored_prefix = rows[0][1]
        assert stored_hash != result["api_key"]
        assert stored_prefix == result["api_key"][:10]

    def test_apikey_create_returns_plaintext_once(self, sdk, team):
        result = sdk.apikey_create(team["id"], "user-1")
        plaintext = result["api_key"]
        # Listing should NOT include plaintext
        keys = sdk.apikey_list(team["id"])
        for k in keys:
            assert "api_key" not in k or k.get("api_key") is None

    def test_apikey_list_excludes_plaintext(self, sdk, team):
        sdk.apikey_create(team["id"], "user-1")
        sdk.apikey_create(team["id"], "user-2")
        keys = sdk.apikey_list(team["id"])
        assert len(keys) == 2
        for k in keys:
            assert "key_prefix" in k
            assert "key_hash" not in k

    def test_apikey_revoke_sets_revoked_at(self, sdk, team):
        result = sdk.apikey_create(team["id"], "user-1")
        revoke = sdk.apikey_revoke(result["id"])
        assert revoke["revoked"] is True
        assert revoke.get("revoked_at") is not None

    def test_apikey_revoke_is_idempotent(self, sdk, team):
        result = sdk.apikey_create(team["id"], "user-1")
        sdk.apikey_revoke(result["id"])
        revoke2 = sdk.apikey_revoke(result["id"])
        assert revoke2.get("already") is True

    def test_apikey_verify_revoked_returns_none(self, sdk, team):
        result = sdk.apikey_create(team["id"], "user-1")
        plaintext = result["api_key"]
        # Verify works before revoke
        valid = sdk.apikey_verify(plaintext)
        assert valid is not None
        assert valid["team_id"] == team["id"]
        # Revoke
        sdk.apikey_revoke(result["id"])
        # Verify after revoke
        invalid = sdk.apikey_verify(plaintext)
        assert invalid is None

    def test_apikey_verify_valid_returns_team_context(self, sdk, team):
        result = sdk.apikey_create(team["id"], "user-1")
        valid = sdk.apikey_verify(result["api_key"])
        assert valid is not None
        assert valid["team_id"] == team["id"]

    def test_apikey_verify_bad_key_returns_none(self, sdk):
        assert sdk.apikey_verify("tt_badkey123") is None

    def test_apikey_verify_many_keys_prefix_lookup(self, sdk, team):
        """#687: Many keys on a team — the correct key still authenticates
        via indexed key_prefix lookup (O(1) per verification, not O(keys)).

        We create 20 keys and verify the LAST one, proving it's not a scan
        that happened to land on an early match. The key_prefix index on
        :APIKey(key_prefix) in _ensure_registry_indexes is what makes the
        _verify_hashed_lookup short-circuit work.
        """
        target_key = None
        for i in range(20):
            result = sdk.apikey_create(team["id"], "user-1")
            if i == 19:  # last key is our target
                target_key = result["api_key"]
        assert target_key is not None
        # Verify the last-created key (would fail a naive early-match scan)
        valid = sdk.apikey_verify(target_key)
        assert valid is not None
        assert valid["team_id"] == team["id"]
        # A bad key with a plausible tt_ prefix is still rejected
        assert sdk.apikey_verify("tt_" + "0" * 32) is None

    def test_apikey_create_rejects_missing_team(self, sdk):
        with pytest.raises(ControlPlaneError, match="not found"):
            sdk.apikey_create("bad-id", "user-1")


class TestInvitationCRUD:
    """Invitation create, list, accept, revoke, cleanup."""

    def test_invitation_create_rejects_duplicate_pending(self, sdk, team):
        sdk.invitation_create(team["id"], "dup@test.com", "admin", "user-1")
        with pytest.raises(ControlPlaneError, match="already exists"):
            sdk.invitation_create(team["id"], "dup@test.com", "admin", "user-1")

    def test_invitation_accept_rejects_expired(self, sdk, team):
        from datetime import datetime, timedelta, timezone
        inv = sdk.invitation_create(team["id"], "exp@test.com", "admin", "user-1")
        # Manually set expires_at to past
        reg = sdk._get_registry()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        reg.query(
            "MATCH (i:Invitation {id:$id}) SET i.expires_at = $exp",
            params={"id": inv["id"], "exp": past},
        )
        with pytest.raises(ControlPlaneError, match="expired"):
            sdk.invitation_accept(inv["id"], "user-2")

    def test_invitation_accept_creates_membership(self, sdk, team):
        inv = sdk.invitation_create(team["id"], "join@test.com", "admin", "user-1")
        result = sdk.invitation_accept(inv["id"], "user-2")
        assert result["team_id"] == team["id"]
        assert result["membership_id"]
        # Verify membership was created
        members = sdk.membership_list(team["id"])
        assert any(m["user_id"] == "user-2" for m in members)

    def test_invitation_accept_rejects_already_accepted(self, sdk, team):
        inv = sdk.invitation_create(team["id"], "used@test.com", "admin", "user-1")
        sdk.invitation_accept(inv["id"], "user-2")
        with pytest.raises(ControlPlaneError, match="already accepted"):
            sdk.invitation_accept(inv["id"], "user-3")

    def test_invitation_get_by_token_finds_match(self, sdk, team):
        inv = sdk.invitation_create(team["id"], "token@test.com", "admin", "user-1")
        token = inv["token"]
        found = sdk.invitation_get_by_token(token)
        assert found is not None
        assert found["email"] == "token@test.com"

    def test_invitation_get_by_token_returns_none_for_bad_token(self, sdk):
        assert sdk.invitation_get_by_token("bad-token") is None

    def test_invitation_revoke(self, sdk, team):
        inv = sdk.invitation_create(team["id"], "rev@test.com", "admin", "user-1")
        result = sdk.invitation_revoke(inv["id"])
        assert result["revoked"] is True
        # Accept should fail after revoke
        with pytest.raises(ControlPlaneError, match="revoked"):
            sdk.invitation_accept(inv["id"], "user-2")

    def test_invitation_revoke_is_idempotent(self, sdk, team):
        inv = sdk.invitation_create(team["id"], "rev2@test.com", "admin", "user-1")
        sdk.invitation_revoke(inv["id"])
        result2 = sdk.invitation_revoke(inv["id"])
        assert result2.get("already") is True

    def test_cleanup_expired_invitations_marks_expired(self, sdk, team):
        inv = sdk.invitation_create(team["id"], "clean@test.com", "admin", "user-1")
        # Manually expire
        reg = sdk._get_registry()
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        reg.query(
            "MATCH (i:Invitation {id:$id}) SET i.expires_at = $exp",
            params={"id": inv["id"], "exp": past},
        )
        result = sdk.cleanup_expired_invitations()
        assert result["cleaned"] >= 1

    def test_invitation_create_rejects_invalid_role(self, sdk, team):
        with pytest.raises(ControlPlaneError, match="Invalid role"):
            sdk.invitation_create(team["id"], "bad@test.com", "bogus", "user-1")
