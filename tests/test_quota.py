"""Tests for tortoise.quota (#329, #683)."""
from __future__ import annotations

import pytest

from tortoise.quota import (
    QuotaCheckError,
    QuotaExceededError,
    enforce_team_limit,
    resolve_team_limits,
)


@pytest.fixture(autouse=True)
def _embedded_env(monkeypatch, tmp_path):
    """Route quota SDKs to an embedded temp DB (no Docker in CI)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "quota.db"))


@pytest.fixture
def reg_sdk(monkeypatch, tmp_path):
    """Registry SDK with a team provisioned (same embedded DB as the env)."""
    from tortoise.sdk import TortoiseSDK
    import os
    db = os.path.join(tmp_path, "quota.db")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", db)
    sdk = TortoiseSDK(db, namespace="registry")
    sdk.team_create(name="quota-team")
    yield sdk
    sdk.close()


class TestResolveTeamLimits:
    def test_missing_team_fails_closed(self):
        with pytest.raises(QuotaCheckError):
            resolve_team_limits("no-such-team")

    def test_provisioned_team_has_defaults(self, reg_sdk):
        tid = _find_team_id(reg_sdk)
        limits = resolve_team_limits(tid)
        # team_create writes max_api_keys from pricing.json free tier (=2),
        # but NOT max_points / max_sessions — defaults apply (aligned with
        # product/pricing.json free tier: max_graph_nodes=10000).
        assert limits["max_points"] == 10000
        assert limits["max_api_keys"] == 2
        assert limits["max_sessions"] == 10000
        assert limits["max_users"] == 1
        assert limits["max_graphs"] == 1


def _find_team_id(sdk) -> str:
    """Find a team id in the registry graph (test helper)."""
    rows = sdk._get_registry().query(
        "MATCH (t:Team) RETURN t.id LIMIT 1"
    ).result_set
    assert rows, "no team provisioned"
    return rows[0][0]


class TestEnforceTeamLimit:
    def test_no_limits_skips(self):
        """stdio/operator: no team context → clean skip."""
        enforce_team_limit(None, "points")  # must not raise

    def test_at_limit_raises(self, tmp_path):
        from tortoise.sdk import TortoiseSDK
        import os
        db = os.path.join(tmp_path, "team.db")
        sdk = TortoiseSDK(db, namespace="team1")
        sdk.create_point("statement", "A")
        limits = {"team_id": "team1", "max_points": 1}
        with pytest.raises(QuotaExceededError):
            enforce_team_limit(limits, "points", sdk=sdk)
        sdk.close()

    def test_below_limit_passes(self, tmp_path):
        from tortoise.sdk import TortoiseSDK
        import os
        db = os.path.join(tmp_path, "team.db")
        sdk = TortoiseSDK(db, namespace="team1")
        sdk.create_point("statement", "A")
        limits = {"team_id": "team1", "max_points": 10}
        enforce_team_limit(limits, "points", sdk=sdk)  # must not raise
        sdk.close()

    def test_counting_error_fails_closed(self, tmp_path, monkeypatch):
        """Fail-closed: a counting exception → QuotaCheckError, never a pass."""
        from tortoise.sdk import TortoiseSDK
        import os
        db = os.path.join(tmp_path, "team.db")
        sdk = TortoiseSDK(db, namespace="team1")
        limits = {"team_id": "team1", "max_points": 1000}
        def boom(*a, **kw):
            raise RuntimeError("db down")
        monkeypatch.setattr(sdk._get_proj().g._g, "query", boom)
        with pytest.raises(QuotaCheckError):
            enforce_team_limit(limits, "points", sdk=sdk)
        sdk.close()

    def test_unknown_resource_fails_closed(self):
        with pytest.raises(QuotaCheckError):
            enforce_team_limit({"team_id": "t", "max_points": 10}, "widgets")


# ── #683: users + graphs enforcement ──────────────────────────────────────

class TestEnforceUsersLimit:
    """User/membership quota enforcement."""

    def test_users_below_limit_passes(self, reg_sdk):
        tid = _find_team_id(reg_sdk)
        limits = resolve_team_limits(tid)
        # team_create does NOT create a membership; count = 0, max_users = 1
        # → below limit
        enforce_team_limit(limits, "users")  # must not raise

    def test_users_at_limit_raises(self, reg_sdk):
        tid = _find_team_id(reg_sdk)
        # Create a membership to hit the limit
        reg_sdk.membership_create(tid, "user-1", "owner")
        limits = resolve_team_limits(tid)
        # 1 membership, max_users=1 → at limit
        with pytest.raises(QuotaExceededError, match="users limit reached"):
            enforce_team_limit(limits, "users")

    def test_users_unlimited_skips(self, reg_sdk):
        """None max_users = unlimited (Team tier) — never raises."""
        tid = _find_team_id(reg_sdk)
        limits = resolve_team_limits(tid)
        limits["max_users"] = None  # Team tier → unlimited
        enforce_team_limit(limits, "users")  # must not raise


class TestEnforceGraphsLimit:
    """Graph quota enforcement."""

    def test_graphs_below_limit_passes(self, reg_sdk):
        tid = _find_team_id(reg_sdk)
        limits = resolve_team_limits(tid)
        # team_create auto-creates 1 default graph; max_graphs=1
        # bump limit to 5 so we're below it
        limits["max_graphs"] = 5
        enforce_team_limit(limits, "graphs")  # must not raise

    def test_graphs_at_limit_raises(self, reg_sdk):
        tid = _find_team_id(reg_sdk)
        limits = resolve_team_limits(tid)
        # 1 default graph from team_create, max_graphs=1 → at limit
        with pytest.raises(QuotaExceededError, match="graphs limit reached"):
            enforce_team_limit(limits, "graphs")

    def test_graphs_unlimited_skips(self, reg_sdk):
        """None max_graphs = unlimited (pro/team tier) — never raises."""
        tid = _find_team_id(reg_sdk)
        limits = resolve_team_limits(tid)
        limits["max_graphs"] = None  # Pro/Team tier → unlimited
        enforce_team_limit(limits, "graphs")  # must not raise


# ── #683: None (unlimited) preservation in resolvers ──────────────────────

class TestNonePreservation:
    """None → unlimited must survive all limit resolvers (P0 regression)."""

    def test_resolve_team_limits_preserves_none_users(self, reg_sdk):
        """Team-tier team with max_users=None → resolve returns None, not 1."""
        tid = _find_team_id(reg_sdk)
        # Directly set max_users=None on the Team node (Team tier semantics)
        reg_sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_users = NULL",
            params={"id": tid},
        )
        limits = resolve_team_limits(tid)
        assert limits["max_users"] is None, (
            f"Expected None (unlimited), got {limits['max_users']!r}")

    def test_resolve_team_limits_preserves_none_graphs(self, reg_sdk):
        """Team-tier team with max_graphs=None → resolve returns None."""
        tid = _find_team_id(reg_sdk)
        reg_sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.max_graphs = NULL",
            params={"id": tid},
        )
        limits = resolve_team_limits(tid)
        assert limits["max_graphs"] is None, (
            f"Expected None (unlimited), got {limits['max_graphs']!r}")

    def test_team_limits_from_node_preserves_none_users(self):
        """_team_limits_from_node: None max_users → None (not coiled to 1)."""
        from tortoise.hosted_api import _team_limits_from_node
        node = {"id": "t1", "tier": "team",
                "max_users": None, "max_graphs": None}
        limits = _team_limits_from_node(node)
        assert limits["max_users"] is None, (
            f"Expected None (unlimited Team tier), got {limits['max_users']!r}")
        assert limits["max_graphs"] is None, (
            f"Expected None (unlimited Team tier), got {limits['max_graphs']!r}")

    def test_team_limits_from_node_preserves_none_graphs(self):
        """_team_limits_from_node: None max_graphs for pro tier = unlimited."""
        from tortoise.hosted_api import _team_limits_from_node
        node = {"id": "t2", "tier": "pro",
                "max_users": 2, "max_graphs": None}
        limits = _team_limits_from_node(node)
        # max_graphs=None (pro tier) → unlimited
        assert limits["max_graphs"] is None, (
            f"Expected None (unlimited pro graphs), got {limits['max_graphs']!r}")
        # max_users=2 is explicit → preserved
        assert limits["max_users"] == 2

    def test_team_limits_from_node_explicit_zero(self):
        """P1: explicit 0 is preserved, not conflated with missing."""
        from tortoise.hosted_api import _team_limits_from_node
        node = {"id": "t3", "tier": "free",
                "max_points": 0, "max_api_keys": 0, "max_sessions": 0}
        limits = _team_limits_from_node(node)
        assert limits["max_points"] == 0, (
            f"Explicit 0 should be 0, got {limits['max_points']!r}")
        assert limits["max_api_keys"] == 0, (
            f"Explicit 0 should be 0, got {limits['max_api_keys']!r}")
        assert limits["max_sessions"] == 0, (
            f"Explicit 0 should be 0, got {limits['max_sessions']!r}")

    def test_team_limits_from_node_free_tier_defaults(self):
        """Missing fields on free-tier node → pricing-aligned defaults."""
        from tortoise.hosted_api import _team_limits_from_node
        node = {"id": "t4", "tier": "free"}
        limits = _team_limits_from_node(node)
        assert limits["max_points"] == 10000
        assert limits["max_api_keys"] == 2
        assert limits["max_sessions"] == 10000
