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

    def test_provisioned_team_has_defaults(self, reg_sdk):
        tid = _find_team_id(reg_sdk)
        limits = resolve_team_limits(tid)
        assert limits["max_points"] == 1000
        assert limits["max_api_keys"] == 20
        assert limits["max_sessions"] == 1000


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
