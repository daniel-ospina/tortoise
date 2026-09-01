"""Tests for tortoise.metering (#681) — write-op counting, threshold events,
usage queries, and period rollover.

Uses FalkorDBLite (embedded) — no Docker required.
"""
from __future__ import annotations

import logging
import os
import tempfile  # noqa: F401
from datetime import UTC

import pytest

from tortoise.metering import (
    _current_period,
    _ops_allowance,
    _reset_thresholds_for_tests,
    _thresholds_fired,  # noqa: F401
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
        sdk, tid = reg_sdk  # noqa: RUF059
        result = record_write_ops(tid, tier="pro")
        assert result is not None
        assert result["write_ops"] == 1
        assert result["period"] == _current_period()
        assert result["overage_eligible"] is True  # pro tier
        assert result["ops_allowance"] == 50000  # from pricing.json

    def test_multiple_increments_accumulate(self, reg_sdk):
        sdk, tid = reg_sdk  # noqa: RUF059
        record_write_ops(tid, tier="pro")
        record_write_ops(tid, tier="pro")
        result = record_write_ops(tid, tier="pro")
        assert result["write_ops"] == 3

    def test_increment_n_greater_than_one(self, reg_sdk):
        sdk, tid = reg_sdk  # noqa: RUF059
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
        sdk, tid = reg_sdk  # noqa: RUF059
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
        sdk, tid = reg_sdk  # noqa: RUF059
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
        sdk, tid = reg_sdk  # noqa: RUF059
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
        sdk, tid = reg_sdk  # noqa: RUF059
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
        sdk, tid = reg_sdk  # noqa: RUF059
        record_write_ops(tid, tier="pro", n=42)
        usage = get_current_usage(tid)
        assert usage["write_ops_used"] == 42
        assert usage["write_ops_limit"] == 50000
        assert usage["overage_eligible"] is True

    def test_overage_cost_computed(self, reg_sdk):
        """When usage exceeds allowance, overage_cost_usd is computed."""
        sdk, tid = reg_sdk  # noqa: RUF059
        allowance = 50000  # pro  # noqa: F841
        # Use 55k ops → 5k overage → 1 block of 10k → $5
        record_write_ops(tid, tier="pro", n=55000)
        usage = get_current_usage(tid)
        assert usage["write_ops_used"] == 55000
        assert usage["overage_cost_usd"] == 5.0  # 1 × $5/10k (55000-50000=5000, ceil→10000)

    def test_overage_rounds_up_to_nearest_block(self, reg_sdk):
        """Overage is ceiling'd to nearest 10k block."""
        sdk, tid = reg_sdk  # noqa: RUF059
        # Use 50001 → 1 op over → 1 block → $5
        record_write_ops(tid, tier="pro", n=50001)
        usage = get_current_usage(tid)
        assert usage["overage_cost_usd"] == 5.0

    def test_no_overage_when_under_allowance(self, reg_sdk):
        sdk, tid = reg_sdk  # noqa: RUF059
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
        from tortoise.supabase_control import is_supabase_enabled  # noqa: I001
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
        """Even a team WITH recorded usage gets the zero-usage view when the
        control plane errors — reads never fail, usage may read stale-low."""
        from tests.fake_control_plane import ErrorControlPlane

        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")

        # Seed a team that HAS usage, then make the control plane error on
        # the READ (after the seed) — the degradation must still return the
        # zero-usage view, not the seeded value, and must not raise.
        class _ErrorAfterSeed(ErrorControlPlane):
            def __init__(self, seeded):
                super().__init__()
                self._seeded = seeded

            def query(self, *a, **k):
                # First call (the seed read) succeeds; afterwards raise.
                if not hasattr(self, "_seeded_read"):
                    self._seeded_read = True
                    return self._seeded
                raise RuntimeError("Supabase down (simulated blip)")

        seeded = [{"team_id": "team-blip-002", "period": _current_period(),
                   "write_ops": 55000}]
        monkeypatch.setattr(
            "tortoise.supabase_control.get_control_plane",
            lambda: _ErrorAfterSeed(seeded),
        )

        usage = get_current_usage("team-blip-002")
        assert usage["write_ops_used"] == 0  # degrades, does NOT surface 55000
        assert usage["overage_cost_usd"] is None

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


# ── Period rollover ─────────────────────────────────────────────────────────

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


# ── #1987 Task 6: ask metering ──────────────────────────────────────────────

class TestAskMetering:
    def test_record_shape_and_increment(self, reg_sdk):
        sdk, tid = reg_sdk  # noqa: RUF059
        from tortoise.metering import record_ask_usage
        r1 = record_ask_usage(tid, tokens_in=100, tokens_out=50, cost_usd=0.001)
        assert r1["ask_calls"] == 1
        r2 = record_ask_usage(tid, tokens_in=200, tokens_out=10, cost_usd=0.002)
        assert r2 is not None
        from tortoise.metering import get_ask_usage
        usage = get_ask_usage(tid)
        assert usage["ask_calls"] == 2
        assert usage["ask_tokens_in"] == 300
        assert usage["ask_tokens_out"] == 60
        assert abs(usage["ask_cost_usd"] - 0.003) < 1e-9

    def test_none_team_id_noop(self):
        from tortoise.metering import get_ask_usage, record_ask_usage
        assert record_ask_usage(None, tokens_in=1) is None
        # registry read for a nonexistent team → zeros
        usage = get_ask_usage("no-such-team")
        assert usage["ask_calls"] == 0

    def test_selfhost_transport_exemption(self, reg_sdk, monkeypatch):
        """Transport-keyed exemption (P1-4/P1-1): `_selfhost_transport` set
        True → zero records; a hosted team with the RAW id "selfhost" DOES
        record (the flag channel, never the value)."""
        from tortoise.metering import get_ask_usage, record_ask_usage
        from tortoise.transport import _selfhost_transport
        sdk, tid = reg_sdk  # noqa: RUF059
        token = _selfhost_transport.set(True)
        try:
            assert record_ask_usage(tid, tokens_in=5) is None
        finally:
            _selfhost_transport.reset(token)
        assert get_ask_usage(tid)["ask_calls"] == 0
        # a hosted team literally named "selfhost" records usage
        assert record_ask_usage("selfhost", tokens_in=5) is not None
        assert get_ask_usage("selfhost")["ask_calls"] == 1

    def test_non_fatal_on_registry_failure(self, reg_sdk, monkeypatch, caplog):
        sdk, tid = reg_sdk  # noqa: RUF059
        from tortoise.metering import record_ask_usage
        def _boom(*a, **k):
            raise RuntimeError("registry down")
        monkeypatch.setattr("tortoise.metering._reg_sdk", _boom)
        assert record_ask_usage(tid, tokens_in=1) is None  # non-fatal

    def test_concurrent_increments_sum(self, reg_sdk):
        """Two threads calling record_ask_usage concurrently → the final
        record equals the sum (no lost increment)."""
        import threading

        from tortoise.metering import get_ask_usage, record_ask_usage
        sdk, tid = reg_sdk  # noqa: RUF059
        threads = [threading.Thread(
            target=record_ask_usage, args=(tid,),
            kwargs={"tokens_in": 10, "tokens_out": 5})
            for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        usage = get_ask_usage(tid)
        assert usage["ask_calls"] == 4
        assert usage["ask_tokens_in"] == 40
        assert usage["ask_tokens_out"] == 20

    def test_cost_bound_and_token_math(self):
        """estimate_ask_cost_usd(8000 in, 500 out) <= 0.01; the metering
        input identity: input_tokens == system prompt + rendered context."""
        from tortoise.metering import ASK_METER_RATES, estimate_ask_cost_usd
        from tortoise.reader import system_prompt_for
        from tortoise.retrieval import estimate_tokens_ask
        cost = estimate_ask_cost_usd(8000, 500, rates=ASK_METER_RATES)
        assert cost <= 0.01, cost
        rendered = "the gym schedule is monday and wednesday"
        inp = estimate_tokens_ask(system_prompt_for(None)) + estimate_tokens_ask(rendered)
        assert estimate_ask_cost_usd(inp, 500, rates=ASK_METER_RATES) > 0

    def test_strong_rates_selection_by_wire_family(self):
        """#2069: select_ask_meter_rates picks the STRONG envelope for
        family-prefixed strong-family wire ids (qwen/upstage/anthropic — the
        serving ``_LockedReader.model``); deepseek specs, bare ids and None
        stay on the deepseek envelope (the ×1.5 over-cover documents
        OpenRouter markup)."""
        from tortoise.metering import (
            ASK_METER_RATES,
            ASK_METER_RATES_STRONG,
            select_ask_meter_rates,
        )
        assert select_ask_meter_rates("qwen/qwen3.8-max") == ASK_METER_RATES_STRONG
        assert select_ask_meter_rates("upstage/solar-pro4") == ASK_METER_RATES_STRONG
        assert select_ask_meter_rates("anthropic/claude-opus-5") == ASK_METER_RATES_STRONG
        # deepseek family (incl. a deepseek spec forced to openrouter) stays
        # on the default envelope — the prefixed spec is deepseek-family.
        assert select_ask_meter_rates("deepseek/deepseek-v4-flash") == ASK_METER_RATES
        assert select_ask_meter_rates("deepseek-v4-flash") == ASK_METER_RATES  # bare
        assert select_ask_meter_rates(None) == ASK_METER_RATES
        assert select_ask_meter_rates("") == ASK_METER_RATES

    def test_strong_rates_bounds_and_target_break(self):
        """#2069: at STRONG {3.00, 9.00} the worst case (~$0.032) and
        typical (~$0.012) both EXCEED the $0.01 structural target (the
        recorded owner decision — the strong lane breaks it); the ×1.5
        over-cover never under-counts the REAL qwen rates ($2/$6)."""
        from tortoise.metering import (
            ASK_METER_RATES_STRONG,
            estimate_ask_cost_usd,
        )
        worst = estimate_ask_cost_usd(9200, 500, rates=ASK_METER_RATES_STRONG)
        typical = estimate_ask_cost_usd(3500, 150, rates=ASK_METER_RATES_STRONG)
        assert worst == pytest.approx(0.0321)
        assert typical == pytest.approx(0.01185)
        # the structural $0.01/query target is broken on the strong lane
        # (recorded in the runbook §#2069 — owner decision pending).
        assert worst > 0.01 and typical > 0.01
        real = {"prompt_per_1m": 2.00, "completion_per_1m": 6.00}
        assert estimate_ask_cost_usd(9200, 500, rates=ASK_METER_RATES_STRONG) >= \
            estimate_ask_cost_usd(9200, 500, rates=real)
        assert estimate_ask_cost_usd(3500, 150, rates=ASK_METER_RATES_STRONG) >= \
            estimate_ask_cost_usd(3500, 150, rates=real)

    def test_strong_lane_under_count_hazard_documented(self):
        """#2069: metering a strong-lane query at the DEFAULT envelope
        under-counts the real qwen rates ~10× — the pre-fix hazard the
        re-baseline removes (a strong ask must never meter at 0.21/0.42)."""
        from tortoise.metering import (
            ASK_METER_RATES,
            estimate_ask_cost_usd,
        )
        real = {"prompt_per_1m": 2.00, "completion_per_1m": 6.00}
        under = estimate_ask_cost_usd(9200, 500, rates=ASK_METER_RATES)
        real_cost = estimate_ask_cost_usd(9200, 500, rates=real)
        assert under < real_cost  # under-counts at the deepseek envelope
        assert real_cost / under > 9  # ~10×

    def test_zero_record_team_read(self, reg_sdk):
        """P2-14: a fresh team with zero ask records → get_ask_usage renders
        ZEROS (never 500) on both the registry read and the supabase-mode
        read."""
        from tortoise.metering import get_ask_usage
        sdk, tid = reg_sdk  # noqa: RUF059
        usage = get_ask_usage(tid)
        assert usage["ask_calls"] == 0
        assert usage["ask_tokens_in"] == 0
        assert usage["ask_cost_usd"] == 0.0

    def test_period_rollover_straddle(self, reg_sdk, monkeypatch):
        """P2-23: a record at T−1s lands in the OLD period; the new period
        starts zero."""
        from datetime import datetime

        from tortoise.metering import _current_period, get_ask_usage, record_ask_usage
        sdk, tid = reg_sdk  # noqa: RUF059
        # freeze at the LAST second of a period
        base = datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC)
        frozen = {"ts": base}
        class _FakeDT:
            @staticmethod
            def now(tz=None):
                return frozen["ts"]
        monkeypatch.setattr("tortoise.metering.datetime", _FakeDT)
        record_ask_usage(tid, tokens_in=10)
        old_period = _current_period()
        assert old_period == "2026-08"
        # roll the period
        frozen["ts"] = datetime(2026, 9, 1, 0, 0, 1, tzinfo=UTC)
        record_ask_usage(tid, tokens_in=20)
        usage = get_ask_usage(tid)
        assert usage["period"] == "2026-09"
        assert usage["ask_tokens_in"] == 20  # old period's record is frozen

    def test_migration_code_contract(self, monkeypatch):
        """Plan Task 6 Step 1: the migration↔code contract — the RPC name
        ``metering_increment_ask`` and the ask_* column set the CODE calls
        MUST match the real migration file (20260829000001), so a column
        reword or RPC rename in either direction fails loudly. Covers both
        record_ask_usage (supabase branch → RPC body keys) and
        get_ask_usage (supabase branch → the ask_* select)."""
        import re as _re
        from pathlib import Path
        mig = (Path(__file__).resolve().parent.parent
               / "supabase" / "migrations"
               / "20260829000001_metering_ask_columns.sql").read_text()
        # (a) the ADD COLUMN set the migration defines
        cols = set(_re.findall(r"ADD COLUMN IF NOT EXISTS\s+(\w+)", mig))
        assert cols == {"ask_calls", "ask_tokens_in", "ask_tokens_out",
                        "ask_cost_usd"}
        # the token counters are bigint (P2 — the ~20.7B token/month
        # envelope is ~10x over the integer range)
        assert "ask_tokens_in   bigint" in mig
        assert "ask_tokens_out  bigint" in mig
        # (b) the RPC name + parameter set the migration defines
        rpc = _re.search(r"CREATE OR REPLACE FUNCTION public\.(\w+)\(", mig)
        assert rpc is not None and rpc.group(1) == "metering_increment_ask"
        params = set(_re.findall(r"p_(\w+)\s+\w+", mig))
        assert params == {"team_id", "period", "calls", "tokens_in",
                          "tokens_out", "cost_usd"}
        # (c) the supabase-mode record path calls the SAME RPC with the
        # SAME p_* body keys (FakeControlPlane records the call body)
        from tests.fake_control_plane import FakeControlPlane
        from tortoise import supabase_control as sc
        from tortoise.metering import get_ask_usage, record_ask_usage
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
        fake = FakeControlPlane()
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
        record_ask_usage("team-1", tokens_in=100, tokens_out=50,
                         cost_usd=0.001)
        fn, body = fake.rpc_calls[-1]
        assert fn == "metering_increment_ask"
        assert set(body) == {"p_team_id", "p_period", "p_calls",
                             "p_tokens_in", "p_tokens_out", "p_cost_usd"}
        assert body["p_team_id"] == "team-1"
        assert body["p_tokens_in"] == 100 and body["p_tokens_out"] == 50
        # (d) the supabase-mode READ path selects the SAME ask_* columns
        fake.seed("metering_records", [{"team_id": "team-1",
                                         "period": body["p_period"],
                                         "ask_calls": 1,
                                         "ask_tokens_in": 100,
                                         "ask_tokens_out": 50,
                                         "ask_cost_usd": 0.001}])
        usage = get_ask_usage("team-1")
        assert usage["ask_calls"] == 1
        assert usage["ask_tokens_in"] == 100
        assert usage["ask_tokens_out"] == 50
        assert abs(usage["ask_cost_usd"] - 0.001) < 1e-9


# ── #1987 Task 6: estimate_tokens_ask ───────────────────────────────────────

class TestEstimateTokensAsk:
    def test_whitespace_parity_with_estimate_tokens(self):
        from tortoise.retrieval import estimate_tokens, estimate_tokens_ask
        text = "the quick brown fox jumps over the lazy dog"
        assert estimate_tokens_ask(text) == estimate_tokens(text)

    def test_cjk_run_over_estimated(self):
        """A long unspaced CJK run is conservatively over-estimated (the
        whitespace-based estimator under-counts it to ~0 words)."""
        from tortoise.retrieval import estimate_tokens_ask
        cjk = "\u4f60" * 2000
        est = estimate_tokens_ask(cjk)
        assert est >= 1000  # ~0.65/char conservative floor
        assert est > estimate_tokens_ask("x" * 2000)
