"""Tests for control-plane data model — registry CRUD operations.

Covers Team, Membership, APIKey, Invitation CRUD via FalkorDBLite.
No Postgres required — audit events fall back to JSONL.
"""
from __future__ import annotations  # noqa: I001

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

    R6 (#221) used to delete the created team in teardown so the registry graph
    never accumulated leftover teams across runs. That teardown is gone: it
    called `sdk.org_delete`, which #4282 deleted with no replacement, and the
    `except Exception: pass` around it turned the removal into a silent no-op.
    Nothing cleans up now because nothing needs to — `sdk` is built on a
    `tmp_path` database, so this team's graph is discarded with the test.
    """
    result = sdk.org_create("test-team")
    yield result


class TestTeamCRUD:
    """Team create, read, update, delete."""

    def test_team_create_writes_to_registry_graph(self, sdk):
        result = sdk.org_create("acme-corp")
        assert result["name"] == "acme-corp"
        assert result["api_key"].startswith("tt_")
        assert result["graph_name"] == "org_acme-corp"
        assert result["id"]

        # Verify in registry graph
        team = sdk.org_get(result["id"])
        assert team is not None
        assert team["name"] == "acme-corp"

    def test_team_create_is_idempotent_with_key(self, sdk):
        result1 = sdk.org_create("durable", idempotency_key="key-123")
        result2 = sdk.org_create("durable", idempotency_key="key-123")
        assert result2.get("existing") is True
        assert result2["id"] == result1["id"]

    def test_team_create_idempotent_recall_returns_no_api_key(self, sdk):
        """#1710: idempotent re-call with the same idempotency_key returns
        existing:true WITHOUT an api_key field — the key previously returned
        on this branch was minted but never hashed/persisted, so callers got
        a dead key that failed auth on first use. The caller holds the
        plaintext from the original creation."""
        from tortoise.auth import verify_api_key

        result1 = sdk.org_create("durable-no-key", idempotency_key="key-abc")
        result2 = sdk.org_create("durable-no-key", idempotency_key="key-abc")

        assert result2.get("existing") is True
        assert result2["id"] == result1["id"]
        assert "api_key" not in result2
        # Nothing new was persisted by the re-call: exactly ONE Team node
        # carries this idempotency key, and the stored hash still verifies
        # the FIRST call's plaintext (no phantom key was hashed/stored).
        reg = sdk._get_registry()
        count = reg.query(
            "MATCH (t:Team {idempotency_key:$ik}) RETURN count(t)",
            params={"ik": "key-abc"},
        ).result_set[0][0]
        assert count == 1
        stored_hash = reg.query(
            "MATCH (t:Team {idempotency_key:$ik}) RETURN t.api_key",
            params={"ik": "key-abc"},
        ).result_set[0][0]
        assert verify_api_key(result1["api_key"], stored_hash) is True

    def test_team_create_rejects_duplicate_name(self, sdk):
        sdk.org_create("unique-name")
        with pytest.raises(ControlPlaneError, match="already exists"):
            sdk.org_create("unique-name")

    def test_team_create_rejects_empty_name(self, sdk):
        with pytest.raises(ControlPlaneError, match="must not be empty"):
            sdk.org_create("")

    def test_team_create_accepts_spaces(self, sdk):
        """Spaces in team names are now accepted."""
        result = sdk.org_create("name with spaces")
        assert result["name"] == "name with spaces"
        assert result["api_key"].startswith("tt_")

    def test_team_get_returns_none_for_missing(self, sdk):
        assert sdk.org_get("nonexistent-id") is None

    def test_team_get_returns_team(self, sdk, team):
        result = sdk.org_get(team["id"])
        assert result is not None
        assert result["name"] == "test-team"
        assert result["tier"] == "free"

    def test_tenant_list_is_not_on_the_public_surface(self, sdk):
        """No public call RETURNS every tenant — owner ruling 2026-10-25 (#4282).

        The qualifier is load-bearing: `migrate_orgs_to_registry` is still public and still
        scans `MATCH (t:Team)` unscoped, but it returns only `{migrated, skipped}` — no
        tenant data reaches the caller. So the disclosure claim this file guards is true,
        while the unscoped scan is #4282 Phase 2's to widen, not this pin's to imply it is
        already gone.

        The method that replaced this test listed every `Team` node with
        `MATCH (t:Team)` and no tenant filter, so a customer-facing SDK could
        enumerate other customers' accounts from it. It had **zero** callers
        (measured: the only other matches were a same-named local variable in
        `tortoise/cost_allocation.py`). It was removed rather than scoped, because
        a scoped version is new behaviour and the surface is frozen until this
        cleanup lands.

        This test is the regression guard, not a test of a feature: the failure it
        prevents is a later lane "restoring" an unscoped tenant list because it
        looked like a missing capability. Reading ONE account is `org_get`.
        """
        assert not hasattr(sdk, "org_list"), (
            "a public `org_list` is back on TortoiseSDK. It returns EVERY tenant "
            "(`MATCH (t:Team)`, unscoped) and was removed by the owner's ruling on "
            "#4282 (2026-10-25). A scoped account list is NEW behaviour and needs "
            "Daniel's approval — it is not a restoration of this method."
        )







class TestMembershipCRUD:
    """Membership create, read, update, delete."""

    def test_membership_create_with_valid_role(self, sdk, team):
        m = sdk.membership_create(team["id"], "user-1", "admin")
        assert m["org_id"] == team["id"]
        assert m["user_id"] == "user-1"
        assert m["role"] == "admin"

    def test_membership_create_rejects_invalid_role(self, sdk, team):
        with pytest.raises(ControlPlaneError, match="Invalid role"):
            sdk.membership_create(team["id"], "user-1", "superuser")

    def test_membership_create_rejects_missing_team(self, sdk):
        with pytest.raises(ControlPlaneError, match="not found"):
            sdk.membership_create("bad-id", "user-1", "admin")



    def test_membership_get_returns_membership(self, sdk, team):
        m = sdk.membership_create(team["id"], "user-1", "admin")
        result = sdk.membership_get(m["id"])
        assert result is not None
        assert result["user_id"] == "user-1"

    def test_membership_get_returns_none_for_missing(self, sdk):
        assert sdk.membership_get("nonexistent") is None

    def test_membership_create_rejects_at_max_users(self, sdk, team):
        """#4282 deleted `org_update`, which only SET the cap here.

        The gate under test is `membership_create`'s own `max_users` check, on
        the SDK door — alive and otherwise covered only at the HTTP layer.

        The cap is set to 2, NOT the free tier's default of 1: at 1 the write
        would be redundant and the test would pass with the write broken (the
        default gate alone raises). At 2 the first two members must be ADMITTED
        and only the third refused, so a cap that never landed fails the test.
        """
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_users = 2",
            params={"id": team["id"]})
        sdk.membership_create(team["id"], "user-1", "owner")
        sdk.membership_create(team["id"], "user-2", "admin")
        with pytest.raises(ControlPlaneError, match="max users"):
            sdk.membership_create(team["id"], "user-3", "member")





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
        assert valid["org_id"] == team["id"]
        # Revoke
        sdk.apikey_revoke(result["id"])
        # Verify after revoke
        invalid = sdk.apikey_verify(plaintext)
        assert invalid is None

    def test_apikey_verify_valid_returns_team_context(self, sdk, team):
        result = sdk.apikey_create(team["id"], "user-1")
        valid = sdk.apikey_verify(result["api_key"])
        assert valid is not None
        assert valid["org_id"] == team["id"]

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
        assert valid["org_id"] == team["id"]
        # A bad key with a plausible tt_ prefix is still rejected
        assert sdk.apikey_verify("tt_" + "0" * 32) is None

    def test_apikey_create_rejects_missing_team(self, sdk):
        with pytest.raises(ControlPlaneError, match="not found"):
            sdk.apikey_create("bad-id", "user-1")




class TestGraphC1Status:
    """C1 (#2110) registry parity — Graph node status/recording + `_graph_count`."""

    def test_graph_create_registry_node_has_status(self, sdk, team):
        """New Graph nodes carry status='active' (v1 lifecycle: no archive)."""
        g = sdk._graph_create(team["id"], "prod", kind="custom")
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (g:Graph {id:$id}) RETURN g.status, g.recording",
            params={"id": g["graph_id"]},
        ).result_set
        assert rows and rows[0][0] == "active"
        assert rows[0][1] is None  # recording NULL = inherit team default

    def test_list_memory_graphs_emits_status_and_recording(self, sdk, team):
        """Registry-shaped rows now carry status/recording (None-safe)."""
        sdk._graph_create(team["id"], "prod", kind="custom")
        graphs = sdk.list_memory_graphs(team["id"])
        assert any(g["name"] == "prod" for g in graphs)
        for g in graphs:
            assert "status" in g
            assert "recording" in g

    def test_graph_count_includes_default_and_custom(self, sdk, team):
        """Registry mode: team_create's default node + custom graph."""
        assert sdk._graph_count(team["id"]) == 1  # default node (team_create)
        sdk._graph_create(team["id"], "prod", kind="custom")
        assert sdk._graph_count(team["id"]) == 2


class TestAPIKeyC1Tenancy:
    def test_apikey_create_legacy_shape_unchanged(self, sdk, team):
        """Back-compat: no kwargs → no tenancy props on the node, list shows
        None-safe defaults (legacy class)."""
        result = sdk.apikey_create(team["id"], "user-1")
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {id:$id}) RETURN k.graph_id, k.scopes, "
            "k.delegation_depth, k.created_by_key_id",
            params={"id": result["id"]},
        ).result_set
        assert (rows[0][0], rows[0][1], rows[0][2], rows[0][3]) == (None, None, None, None)
        # #4282 deleted apikey_list; the props it projected are
        # asserted directly above, off the node the create door wrote.

    def test_apikey_create_scoped_graph_key_stores_props(self, sdk, team):
        """A C1-minted graph-bound key stores its tenancy props."""
        result = sdk.apikey_create(
            team["id"], "user-1",
            graph_id="g_abc123def4567890",
            scopes=["graphs:read", "graphs:write"],
            created_by_key_id="key-parent-000",
            delegation_depth=0,
        )
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {id:$id}) RETURN k.graph_id, k.scopes, "
            "k.delegation_depth, k.created_by_key_id",
            params={"id": result["id"]},
        ).result_set
        assert rows[0][0] == "g_abc123def4567890"
        assert rows[0][1] == ["graphs:read", "graphs:write"]
        assert rows[0][2] == 0
        assert rows[0][3] == "key-parent-000"
        # #4282 deleted apikey_list; the props it projected are
        # asserted directly above, off the node the create door wrote.
        # Still authenticates (registry parity: hash path unchanged)
        valid = sdk.apikey_verify(result["api_key"])
        assert valid is not None
        assert valid["org_id"] == team["id"]


class TestInvitationRevoke:
    """`invitation_revoke` survives — only its create door was deleted by #4282.

    The subject here is the IDEMPOTENT branch (`already: True`) of a live public
    method, plus the not-found refusal. Nothing else in the tree reaches either:
    `tests/test_invites_http.py` exercises the FIRST revoke through the
    endpoint, which 404s/409s before idempotency can show.
    """

    def _seed_invitation(self, sdk, team, inv_id: str = "inv-probe-1") -> str:
        # `invitation_create` was deleted; the node shape it wrote is written
        # directly, in the graph the surviving methods read (`_get_registry`).
        sdk._get_registry().query(
            "CREATE (i:Invitation {id:$id, org_id:$t, email:$e, role:$r, "
            "status:'pending'})",
            params={"id": inv_id, "t": team["id"],
                    "e": "rev2@test.com", "r": "admin"})
        return inv_id

    def test_invitation_revoke_is_idempotent(self, sdk, team):
        inv_id = self._seed_invitation(sdk, team)
        first = sdk.invitation_revoke(inv_id)
        assert first["revoked"] is True and "already" not in first, first
        assert sdk.invitation_get_by_id(inv_id)["status"] == "revoked"

        again = sdk.invitation_revoke(inv_id)
        assert again.get("already") is True, again
        assert again["invitation_id"] == inv_id

    def test_invitation_revoke_missing_is_not_an_error(self, sdk, team):
        out = sdk.invitation_revoke("inv-does-not-exist")
        assert out == {"revoked": False, "reason": "not found"}, out
