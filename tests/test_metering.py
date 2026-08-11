"""Tests for tortoise.metering (#681) — write-op counting, threshold events,
usage queries, and period rollover.

Uses FalkorDBLite (embedded) — no Docker required.
"""
from __future__ import annotations

import logging
import os
import tempfile

import pytest

from tortoise.metering import (
    _current_period,
    _ops_allowance,
    _reset_thresholds_for_tests,
    _thresholds_fired,
    get_current_usage,
    record_write_ops,
)


@pytest.fixture(autouse=True)
def _embedded_env(monkeypatch, tmp_path):
    """Route metering SDKs to an embedded temp DB (no Docker in CI)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "metering.db"))
    _reset_thresholds_for_tests()


@pytest.fixture
def reg_sdk(monkeypatch, tmp_path):
    """Registry SDK with a team provisioned."""
    from tortoise.sdk import TortoiseSDK
    db = os.path.join(tmp_path, "metering.db")
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", db)
    sdk = TortoiseSDK(db, namespace="registry")
    # Create a team with known tier
    team = sdk.team_create(name="meter-test")
    tid = team["id"]
    # Stamp tier on the Team node (pro = overage-eligible)
    sdk._get_registry().query(
        "MATCH (t:Team {id: $tid}) SET t.tier = 'pro'",
        params={"tid": tid},
    )
    sdk._get_registry().query(
        "MATCH (t:Team {id: $tid}) SET t.tier = 'pro'",
        params={"tid": tid},
    )
    yield sdk, tid
    sdk.close()


# ── Increment tests ─────────────────────────────────────────────────────────

class TestRecordWriteOps:
    def test_increment_creates_record(self, reg_sdk):
        sdk, tid = reg_sdk
        result = record_write_ops(tid, tier="pro")
        assert result is not None
        assert result["write_ops"] == 1
        assert result["period"] == _current_period()
        assert result["overage_eligible"] is True  # pro tier
        assert result["ops_allowance"] == 50000  # from pricing.json

    def test_multiple_increments_accumulate(self, reg_sdk):
        sdk, tid = reg_sdk
        record_write_ops(tid, tier="pro")
        record_write_ops(tid, tier="pro")
        result = record_write_ops(tid, tier="pro")
        assert result["write_ops"] == 3

    def test_increment_n_greater_than_one(self, reg_sdk):
        sdk, tid = reg_sdk
        result = record_write_ops(tid, tier="pro", n=5)
        assert result["write_ops"] == 5

    def test_separate_teams_independent_counters(self, monkeypatch, tmp_path):
        """Two teams in the same registry get independent counters."""
        from tortoise.sdk import TortoiseSDK
        db = os.path.join(tmp_path, "metering.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db)
        sdk = TortoiseSDK(db, namespace="registry")
        t1 = sdk.team_create(name="team-a")
        t2 = sdk.team_create(name="team-b")
        sdk._get_registry().query(
            "MATCH (t:Team {id: $tid}) SET t.tier = 'pro'",
            params={"tid": t1["id"]},
        )
        sdk._get_registry().query(
            "MATCH (t:Team {id: $tid}) SET t.tier = 'pro'",
            params={"tid": t2["id"]},
        )
        record_write_ops(t1["id"], tier="pro", n=3)
        record_write_ops(t2["id"], tier="pro", n=7)
        r1 = record_write_ops(t1["id"], tier="pro")
        r2 = record_write_ops(t2["id"], tier="pro")
        assert r1["write_ops"] == 4
        assert r2["write_ops"] == 8
        sdk.close()

    def test_none_team_id_is_noop(self):
        result = record_write_ops("", tier="pro")
        assert result is None

    def test_non_fatal_on_db_error(self, reg_sdk, monkeypatch):
        """Metering failures are logged, never raised."""
        sdk, tid = reg_sdk
        # Break ALL future registry SDK connections by patching _reg_sdk
        import tortoise.metering as metering_mod
        def _bad_reg():
            raise RuntimeError("db down")
        monkeypatch.setattr(metering_mod, "_reg_sdk", _bad_reg)
        # Must not raise
        result = record_write_ops(tid, tier="pro")
        assert result is None


# ── Threshold events ────────────────────────────────────────────────────────

class TestThresholdEvents:
    def test_80_percent_warning(self, reg_sdk, caplog):
        """Crossing 80% of allowed ops emits a WARNING log once."""
        sdk, tid = reg_sdk
        _reset_thresholds_for_tests()
        allowance = 50000  # pro tier
        eighty_pct = int(allowance * 0.80)

        # Jump to 80% in one increment
        with caplog.at_level(logging.WARNING):
            record_write_ops(tid, tier="pro", n=eighty_pct)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING
                    and "threshold" in r.message]
        assert len(warnings) >= 1, (
            f"Expected at least one WARNING for 80% threshold, got: "
            f"{[r.message for r in caplog.records]}"
        )
        assert "80%" in warnings[0].message
        assert tid in warnings[0].message

    def test_100_percent_error(self, reg_sdk, caplog):
        """Crossing 100% of allowed ops emits an ERROR log once."""
        sdk, tid = reg_sdk
        _reset_thresholds_for_tests()
        allowance = 50000  # pro tier

        with caplog.at_level(logging.ERROR):
            record_write_ops(tid, tier="pro", n=allowance)

        errors = [r for r in caplog.records if r.levelno == logging.ERROR
                  and "threshold" in r.message]
        assert len(errors) >= 1, (
            f"Expected at least one ERROR for 100% threshold, got: "
            f"{[r.message for r in caplog.records]}"
        )
        assert "100%" in errors[0].message

    def test_threshold_fires_once_per_period(self, reg_sdk, caplog):
        """Threshold events fire only once per (team, period, pct)."""
        sdk, tid = reg_sdk
        _reset_thresholds_for_tests()

        # Cross 80% one op at a time from allowance-1 — should fire once
        allowance = 50000
        eighty_pct = int(allowance * 0.80)

        # First, set count to just below threshold
        record_write_ops(tid, tier="pro", n=eighty_pct - 1)

        with caplog.at_level(logging.WARNING):
            # Next op crosses 80%
            record_write_ops(tid, tier="pro")
            # Next op stays above 80% — should NOT fire again
            record_write_ops(tid, tier="pro")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING
                    and "80%" in r.message]
        assert len(warnings) == 1, (
            f"Expected exactly 1 WARNING for 80% threshold, got {len(warnings)}"
        )

    def test_no_threshold_for_free_tier(self, reg_sdk, caplog):
        """Free/Solo tiers never trigger threshold events (no overage)."""
        sdk, tid = reg_sdk
        _reset_thresholds_for_tests()
        # Switch team to free tier
        sdk._get_registry().query(
            "MATCH (t:Team {id: $tid}) SET t.tier = 'free'",
            params={"tid": tid},
        )

        with caplog.at_level(logging.WARNING):
            record_write_ops(tid, tier="free", n=99999)

        warnings = [r for r in caplog.records
                    if "threshold" in r.message]
        assert len(warnings) == 0, (
            f"Free tier should not trigger threshold events, got: {warnings}"
        )


# ── Usage query tests ───────────────────────────────────────────────────────

class TestGetCurrentUsage:
    def test_usage_returns_zeros_for_new_team(self, monkeypatch, tmp_path):
        """Team with no MeteringRecord returns 0 used."""
        from tortoise.sdk import TortoiseSDK
        db = os.path.join(tmp_path, "metering.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db)
        sdk = TortoiseSDK(db, namespace="registry")
        team = sdk.team_create(name="fresh-team")
        sdk._get_registry().query(
            "MATCH (t:Team {id: $tid}) SET t.tier = 'free'",
            params={"tid": team["id"]},
        )
        usage = get_current_usage(team["id"])
        assert usage["write_ops_used"] == 0
        assert usage["period"] == _current_period()
        assert usage["overage_eligible"] is False  # free tier
        sdk.close()

    def test_usage_reflects_accumulated_ops(self, reg_sdk):
        sdk, tid = reg_sdk
        record_write_ops(tid, tier="pro", n=42)
        usage = get_current_usage(tid)
        assert usage["write_ops_used"] == 42
        assert usage["write_ops_limit"] == 50000
        assert usage["overage_eligible"] is True

    def test_overage_cost_computed(self, reg_sdk):
        """When usage exceeds allowance, overage_cost_usd is computed."""
        sdk, tid = reg_sdk
        allowance = 50000  # pro
        # Use 55k ops → 5k overage → 1 block of 10k → $5
        record_write_ops(tid, tier="pro", n=55000)
        usage = get_current_usage(tid)
        assert usage["write_ops_used"] == 55000
        assert usage["overage_cost_usd"] == 5.0  # 1 × $5/10k (55000-50000=5000, ceil→10000)

    def test_overage_rounds_up_to_nearest_block(self, reg_sdk):
        """Overage is ceiling'd to nearest 10k block."""
        sdk, tid = reg_sdk
        # Use 50001 → 1 op over → 1 block → $5
        record_write_ops(tid, tier="pro", n=50001)
        usage = get_current_usage(tid)
        assert usage["overage_cost_usd"] == 5.0

    def test_no_overage_when_under_allowance(self, reg_sdk):
        sdk, tid = reg_sdk
        record_write_ops(tid, tier="pro", n=100)
        usage = get_current_usage(tid)
        assert usage["overage_cost_usd"] is None

    def test_free_tier_has_no_overage(self, monkeypatch, tmp_path):
        from tortoise.sdk import TortoiseSDK
        db = os.path.join(tmp_path, "metering.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db)
        sdk = TortoiseSDK(db, namespace="registry")
        team = sdk.team_create(name="free-team")
        sdk._get_registry().query(
            "MATCH (t:Team {id: $tid}) SET t.tier = 'free'",
            params={"tid": team["id"]},
        )
        record_write_ops(team["id"], tier="free", n=99999)
        usage = get_current_usage(team["id"])
        assert usage["overage_eligible"] is False
        assert usage["overage_cost_usd"] is None
        sdk.close()


# ── Supabase-mode degradation (fault injection, #923) ────────────────────

class TestGetCurrentUsageSupabaseDegrade:
    """A control-plane blip must never block reads: the Supabase branch of
    get_current_usage degrades to the zero-usage dict (mirroring the
    registry path) instead of raising (which 500'd /v1/team)."""

    def test_erroring_cp_returns_zero_usage_dict(self, monkeypatch, caplog):
        from tortoise.supabase_control import is_supabase_enabled
        from tests.fake_control_plane import ErrorControlPlane

        # Force the Supabase branch
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
        assert is_supabase_enabled() is True

        # Fault injection: every control-plane read raises (metering_get AND
        # team_tier both query through cp.query → RuntimeError).
        monkeypatch.setattr(
            "tortoise.supabase_control.get_control_plane",
            lambda: ErrorControlPlane(),
        )

        with caplog.at_level(logging.WARNING):
            usage = get_current_usage("team-blip-001")  # must not raise

        assert usage["write_ops_used"] == 0
        assert usage["write_ops_limit"] == _ops_allowance("free")
        assert usage["period"] == _current_period()
        assert usage["overage_eligible"] is False
        assert usage["overage_cost_usd"] is None
        # The failure is logged, not raised
        assert any(
            "metering usage query failed" in r.message
            for r in caplog.records
        )

    def test_erroring_cp_with_used_ops_still_degrades(self, monkeypatch):
        """Even a team with recorded usage gets the zero-usage view when the
        control plane errors — reads never fail, usage may read stale-low."""
        from tests.fake_control_plane import ErrorControlPlane

        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
        monkeypatch.setattr(
            "tortoise.supabase_control.get_control_plane",
            lambda: ErrorControlPlane(),
        )

        usage = get_current_usage("team-blip-002")
        assert usage["write_ops_used"] == 0
        assert usage["overage_cost_usd"] is None


# ── Period rollover ─────────────────────────────────────────────────────────

    def test_healthy_cp_returns_real_usage(self, monkeypatch):
        """Control test (VGATE #923): a healthy Supabase control plane must
        return REAL usage — the fault-injection tests above must not mask a
        broken happy path."""
        import tortoise.metering as m
        from tests.fake_control_plane import FakeControlPlane

        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
        import tortoise.supabase_control as sc

        # pro tier: 55,000 ops used this period — over the allowance → overage
        fake = FakeControlPlane({
            "metering_records": [
                {"team_id": "team-1", "period": _current_period(),
                 "write_ops": 55000},
            ],
            "teams": [{"id": "team-1", "tier": "pro"}],
        })
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)

        usage = m.get_current_usage("team-1")
        assert usage["write_ops_used"] == 55000
        assert usage["period"] == _current_period()
        assert usage["overage_eligible"] is True  # pro tier
        # overage beyond the pro allowance, rounded up to the 10k block
        assert usage["overage_cost_usd"] is not None
        assert usage["overage_cost_usd"] > 0


class TestPeriodRollover:
    def test_period_is_calendar_month_utc(self):
        """_current_period returns YYYY-MM in UTC."""
        period = _current_period()
        assert len(period) == 7
        assert period[4] == "-"
        year, month = period.split("-")
        assert 2026 <= int(year) <= 2099
        assert 1 <= int(month) <= 12

    def test_different_periods_are_separate_records(self, monkeypatch, tmp_path):
        """Explicitly writing to a past period creates a separate record."""
        from tortoise.sdk import TortoiseSDK
        db = os.path.join(tmp_path, "metering.db")
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", db)
        sdk = TortoiseSDK(db, namespace="registry")
        team = sdk.team_create(name="period-team")
        tid = team["id"]

        # Simulate writes in two periods by directly manipulating the registry
        reg = sdk._get_registry()
        reg.query(
            "MERGE (m:MeteringRecord {team_id: $tid, period: '2026-07'}) "
            "SET m.write_ops = coalesce(m.write_ops, 0) + 100",
            params={"tid": tid},
        )
        reg.query(
            "MERGE (m:MeteringRecord {team_id: $tid, period: '2026-08'}) "
            "SET m.write_ops = coalesce(m.write_ops, 0) + 50",
            params={"tid": tid},
        )

        # Verify separate records exist
        rows = reg.query(
            "MATCH (m:MeteringRecord {team_id: $tid}) "
            "RETURN m.period, m.write_ops ORDER BY m.period",
            params={"tid": tid},
        ).result_set
        assert len(rows) == 2
        assert rows[0] == ["2026-07", 100]
        assert rows[1] == ["2026-08", 50]
        sdk.close()


# ── Pricing.json integration ────────────────────────────────────────────────

class TestPricingIntegration:
    def test_pro_allowance_matches_pricing_json(self):
        """Pro tier has 50,000 included_write_ops_per_month."""
        from tortoise.pricing import tier_limits
        lim = tier_limits("pro")
        assert lim["included_write_ops_per_month"] == 50000

    def test_team_allowance_matches_pricing_json(self):
        """Team tier has 200,000 included_write_ops_per_month."""
        from tortoise.pricing import tier_limits
        lim = tier_limits("team")
        assert lim["included_write_ops_per_month"] == 200000

    def test_free_tier_has_no_overage(self):
        from tortoise.pricing import has_overage
        assert has_overage("free") is False
        assert has_overage("solo") is False

    def test_pro_and_team_have_overage(self):
        from tortoise.pricing import has_overage
        assert has_overage("pro") is True
        assert has_overage("team") is True

    def test_overage_price_is_5_dollars_per_10k(self):
        from tortoise.pricing import overage_price_per_10k
        assert overage_price_per_10k() == 5.0
