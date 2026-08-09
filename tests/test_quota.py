"""Tests for tortoise.quota (#329)."""
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

    def test_provisioned_team_has_tier_limits(self, reg_sdk):
        """#310 GAP-B: a team_create'd team resolves TIER-derived limits from
        pricing.json (free = 10k points / 2 api keys / 1000 sessions) — never
        the old DEFAULT_MAX_* consts."""
        tid = _find_team_id(reg_sdk)
        limits = resolve_team_limits(tid)
        assert limits["max_points"] == 10000
        assert limits["max_api_keys"] == 2
        assert limits["max_sessions"] == 1000

    def test_legacy_team_no_stored_limits_resolves_tier_limits(self, monkeypatch, tmp_path):
        """#310 GAP-B (review fix 2): a legacy Team node with tier='pro' and NO
        stored max_* fields resolves tier-derived values (max_points == 100000
        == max_graph_nodes, max_api_keys == 10) — REST get_current_team and
        MCP resolve_team_limits must agree exactly."""
        import os
        db = os.path.join(tmp_path, "legacy.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db)
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK(db, namespace="registry")
        try:
            team = sdk.team_create("legacy-pro")
            # Simulate a pre-D1 node: tier upgraded out-of-band, limits never stored.
            sdk._get_registry().query(
                "MATCH (t:Team {id:$id}) SET t.tier='pro' REMOVE t.max_users, "
                "t.max_graphs, t.max_api_keys, t.max_points, t.max_sessions",
                params={"id": team["id"]},
            )
            ak = sdk.apikey_create(team["id"], "user-1")
            limits = resolve_team_limits(team["id"])
            assert limits["tier"] == "pro"
            assert limits["max_points"] == 100000
            assert limits["max_api_keys"] == 10
            assert limits["max_sessions"] == 1000

            # REST parity: GET /v1/team (real auth) returns identical numbers.
            from fastapi.testclient import TestClient
            from tortoise.hosted_api import app
            with TestClient(app) as tc:
                r = tc.get("/v1/team", headers={"Authorization": f"Bearer {ak['api_key']}"})
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["tier"] == "pro"
                assert body["max_points"] == 100000
                assert body["max_api_keys"] == 10
                assert body["max_sessions"] == 1000
        finally:
            sdk.close()


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
