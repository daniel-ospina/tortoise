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
    _calendar_month_period,
    _current_period,
    _ops_allowance,
    _reset_thresholds_for_tests,
    _thresholds_fired,  # noqa: F401
    get_current_usage,
    record_write_ops,
)
from tortoise.quota import QuotaCheckError


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
    team = sdk.org_create(name="meter-test")
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


def _break_increment_only(monkeypatch, sdk) -> None:
    """Break the increment's OWN row write, leaving the anchor read working.

    ``_reg_sdk`` is the dependency of BOTH halves of the meter: ``_metering_anchor``
    reads the billing anchor through it, and the increment issues its
    ``MERGE (m:MeteringRecord …)`` through it. A blanket ``_reg_sdk`` raise is
    therefore no longer a statement about the increment at all — since #3825 it
    makes the WINDOW unresolvable, and window resolution RAISES by design (see
    ``test_anchor_read_failure_raises_the_signal``, which pins that signal).

    Injecting the failure BY STATEMENT — everything delegates to the real
    registry except the increment's MERGE — lets the window resolve and breaks
    only the increment, which is exactly the claim ``test_non_fatal_on_db_error``
    and ``test_non_fatal_on_registry_failure`` have always made.
    """
    import tortoise.metering as metering_mod

    real_reg = sdk._get_registry()

    class _IncrementBrokenRegistry:
        """Delegate every statement to the REAL registry; fail the increment."""

        def query(self, cypher, *args, **kwargs):
            if "MERGE (m:MeteringRecord" in cypher:
                raise RuntimeError("registry increment write failed")
            return real_reg.query(cypher, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(real_reg, name)

    class _StubSDK:
        def _get_registry(self):
            return _IncrementBrokenRegistry()

    monkeypatch.setattr(metering_mod, "_reg_sdk", lambda: _StubSDK())


# ── Increment tests ─────────────────────────────────────────────────────────

class TestRecordWriteOps:
    def test_increment_creates_record(self, reg_sdk):
        _sdk, tid = reg_sdk
        result = record_write_ops(tid, tier="pro")
        assert result is not None
        assert result["write_ops"] == 1
        # #3825: ``period`` is now the DERIVED month label of the resolved
        # WINDOW; the window itself rides on period_start/period_end. For an
        # org with no subscription (D13) the window IS the calendar month, so
        # the label is unchanged from the pre-#3825 behaviour.
        window = _current_period(tid)
        assert result["period"] == window.label
        assert result["period_start"] == window.start_iso
        assert result["period_end"] == window.end_iso
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
        t1 = sdk.org_create(name="team-a")
        t2 = sdk.org_create(name="team-b")
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

    def test_none_org_id_is_noop(self):
        result = record_write_ops("", tier="pro")
        assert result is None

    def test_non_fatal_on_db_error(self, reg_sdk, monkeypatch, caplog):
        """A failed INCREMENT row write is logged, never raised.

        #3825: only the increment is broken here (``_break_increment_only``).
        A blanket ``_reg_sdk`` failure would now make the window unresolvable,
        and window resolution RAISES by design — so the old shape could not
        tell "I could not resolve the window" apart from "the row write
        failed". The window resolves; the MERGE raises; the write is non-fatal
        (the increment is dropped — it is NOT retried at any call site; that
        residual is #3824's representation scope).
        """
        sdk, tid = reg_sdk
        _break_increment_only(monkeypatch, sdk)
        with caplog.at_level(logging.WARNING, logger="tortoise.metering"):
            result = record_write_ops(tid, tier="pro")
        assert result is None  # non-fatal: only the increment failed
        assert any("increment failed" in r.message for r in caplog.records), (
            [r.message for r in caplog.records]
        )

    def test_anchor_read_failure_raises_the_signal(self, reg_sdk, monkeypatch):
        """The OTHER half of the pair (#3825): when the ANCHOR READ fails, the
        org's window is unresolvable and the writer RAISES ``QuotaCheckError``.

        #3981: that raise is a SIGNAL, not enforcement. This is the module
        boundary; every production caller absorbs it, serves the request and
        reports the dropped increment to the operator
        (``metering.report_unmetered_increment``). The user-facing refusal is
        the pre-spend admission gate, never this raise. The assertion below is
        therefore about the WRITER's contract, not about a refused write.

        ``test_non_fatal_on_db_error`` above pins the increment-RPC half (window
        known → non-fatal). Together the two tests hold the paths APART: the
        same monkeypatched seam, two different statements, two opposite
        outcomes — which is what makes "a dropped increment undercounts the
        cohort cap" a caught mutation rather than a silent one.
        """
        import tortoise.metering as metering_mod

        _sdk, tid = reg_sdk

        def _bad_reg():
            raise RuntimeError("db down")

        monkeypatch.setattr(metering_mod, "_reg_sdk", _bad_reg)
        with pytest.raises(QuotaCheckError):
            record_write_ops(tid, tier="pro")


# ── Threshold events ────────────────────────────────────────────────────────

# ── #4957: scope threshold-event capture to the METERING logger ─────────────
#
# These tests assert on threshold EVENTS, and only ``tortoise.metering`` emits
# them. Filtering ``caplog.records`` by the bare word "threshold" matches ANY
# logger's FORMATTED message, because ``caplog`` captures every logger that
# propagates to root. pytest's ``tmp_path`` derives from the test's own name —
# ``test_no_threshold_for_free_tier`` — so the unrelated
# ``#4879: replay allowed for registry <path>`` WARNING emitted by
# ``tortoise.embedded_lifecycle`` matched on its embedded path and reddened
# main's ``test (b)`` (#4954 / #4962).
#
# The scope below is by LOGGER, so no other module's record can be mistaken for
# a metering event, whatever it logs. An ABSENCE assertion is the shape
# pollution breaks, and it is the shape used below.
_METERING_LOGGER = "tortoise.metering"


def _metering_records(caplog, needle: str = "threshold",
                      levelno: int | None = None) -> list:
    """Records the METERING logger emitted whose message contains ``needle``.

    Scoped by logger NAME, not by the bare word alone (#4957): the word
    "threshold" occurs in this test module's own tmpdir path, so an unscoped
    filter matches another module's record and turns a real absence into a
    failure. ``needle`` and ``levelno`` narrow WITHIN that scope.
    """
    return [
        r for r in caplog.records
        if r.name == _METERING_LOGGER
        and (levelno is None or r.levelno == levelno)
        and needle in r.message
    ]


class TestThresholdEvents:
    def test_80_percent_warning(self, reg_sdk, caplog):
        """Crossing 80% of allowed ops emits a WARNING log once."""
        sdk, tid = reg_sdk  # noqa: RUF059
        _reset_thresholds_for_tests()
        allowance = 50000  # pro tier
        eighty_pct = int(allowance * 0.80)

        # Jump to 80% in one increment
        with caplog.at_level(logging.WARNING, logger=_METERING_LOGGER):
            record_write_ops(tid, tier="pro", n=eighty_pct)

        warnings = _metering_records(caplog, levelno=logging.WARNING)
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

        with caplog.at_level(logging.ERROR, logger=_METERING_LOGGER):
            record_write_ops(tid, tier="pro", n=allowance)

        errors = _metering_records(caplog, levelno=logging.ERROR)
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

        with caplog.at_level(logging.WARNING, logger=_METERING_LOGGER):
            # Next op crosses 80%
            record_write_ops(tid, tier="pro")
            # Next op stays above 80% — should NOT fire again
            record_write_ops(tid, tier="pro")

        warnings = _metering_records(
            caplog, needle="80%", levelno=logging.WARNING)
        assert len(warnings) == 1, (
            f"Expected exactly 1 WARNING for 80% threshold, got {len(warnings)}"
        )

    def test_no_threshold_for_free_tier(self, reg_sdk, caplog):
        """Free (a zero-price tier) never triggers threshold events: no
        overage. Solo is PAID and therefore metered since #4815 — see
        test_paid_solo_tier_triggers_threshold_events."""
        sdk, tid = reg_sdk
        _reset_thresholds_for_tests()
        # Switch team to free tier
        sdk._get_registry().query(
            "MATCH (t:Team {id: $tid}) SET t.tier = 'free'",
            params={"tid": tid},
        )

        with caplog.at_level(logging.WARNING, logger=_METERING_LOGGER):
            record_write_ops(tid, tier="free", n=99999)

        warnings = _metering_records(caplog)
        assert len(warnings) == 0, (
            f"Free tier should not trigger threshold events, got: {warnings}"
        )

    def test_paid_solo_tier_triggers_threshold_events(self, reg_sdk, caplog):
        """#4815: solo is a PAID tier, so overage is ON — it emits the
        80%/100% threshold events exactly as pro/team do. This is the
        ruling's observable metering consequence."""
        sdk, tid = reg_sdk
        _reset_thresholds_for_tests()
        sdk._get_registry().query(
            "MATCH (t:Team {id: $tid}) SET t.tier = 'solo'",
            params={"tid": tid},
        )

        # Scoped to the METERING logger (#4957) — see _metering_records. This
        # is a PRESENCE assertion, which is exactly the shape an unscoped
        # caplog.records scan can false-PASS: any other module logging "80%"
        # at WARNING would satisfy it without solo being metered at all.
        with caplog.at_level(logging.WARNING, logger=_METERING_LOGGER):
            record_write_ops(tid, tier="solo", n=99999)

        warnings = _metering_records(
            caplog, needle="80%", levelno=logging.WARNING)
        errors = _metering_records(
            caplog, needle="100%", levelno=logging.ERROR)
        assert warnings, (
            "Solo (paid) must fire the 80% threshold, got: "
            f"{_metering_records(caplog)}"
        )
        assert errors, (
            "Solo (paid) must fire the 100% threshold, got: "
            f"{_metering_records(caplog)}"
        )

    def test_unrelated_logger_is_not_a_threshold_event(self, reg_sdk, caplog):
        """#4957: another module's record must never count as a threshold event.

        This is the shape that reddened main's ``test (b)``: the ``#4879``
        replay-allowed WARNING from ``tortoise.embedded_lifecycle`` embeds the
        embedded registry path, and pytest's ``tmp_path`` derives from the
        test's own name (``test_no_threshold_for_free_tier``) — so a filter on
        the bare word matched the test's OWN tmpdir.

        The injected record reproduces that shape, and this test asserts through
        the SAME helper the real absence assertion uses, so re-broadening the
        filter fails here rather than re-arming on main.
        """
        sdk, tid = reg_sdk
        _reset_thresholds_for_tests()
        sdk._get_registry().query(
            "MATCH (t:Team {id: $tid}) SET t.tier = 'free'",
            params={"tid": tid},
        )

        with caplog.at_level(logging.WARNING, logger=_METERING_LOGGER):
            logging.getLogger("tortoise.embedded_lifecycle").warning(
                "#4879: replay allowed for registry %s — gate=%s%s "
                "(this construction holds the in-flight replay claim)",
                "/tmp/pytest-of-runner/pytest-0/test_no_threshold_for_free_tier0",
                "allow",
                "",
            )
            record_write_ops(tid, tier="free", n=99999)

        # Non-vacuous the other way too: the polluted record really IS captured,
        # so the guard under test is the LOGGER SCOPE, not the record's absence.
        assert any(
            r.name == "tortoise.embedded_lifecycle" and "threshold" in r.message
            for r in caplog.records
        ), "the injected record must be captured, or this test proves nothing"

        assert _metering_records(caplog) == [], (
            "an unrelated logger's record was counted as a metering threshold "
            f"event: {_metering_records(caplog)}"
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
        team = sdk.org_create(name="fresh-team")
        sdk._get_registry().query(
            "MATCH (t:Team {id: $tid}) SET t.tier = 'free'",
            params={"tid": team["id"]},
        )
        usage = get_current_usage(team["id"])
        assert usage["write_ops_used"] == 0
        assert usage["period"] == _current_period(team["id"]).label
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
        team = sdk.org_create(name="free-team")
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
        # #3825: the FIRST control-plane read on this path is the metering
        # WINDOW anchor, so with a wholly unreachable plane the WINDOW itself
        # is unresolvable and the degrade renders the display placeholder
        # label — never a calendar-month KEY (there is no row to key to).
        assert usage["period"] == _calendar_month_period().label
        assert usage["period_start"] is None
        assert usage["period_end"] is None
        assert usage["overage_eligible"] is False
        assert usage["overage_cost_usd"] is None
        # The failure is logged, not raised. #3825 changed WHICH read fails
        # first — the WINDOW anchor now precedes the metering_records read, so
        # with a wholly unreachable plane the window-unresolvable path logs
        # its own (more precise) message and the supabase metering read never
        # runs. The subject of this assertion is "a failure was logged", not
        # a wording, so both messages are accepted.
        assert any(
            ("metering usage query failed" in r.message
             or "metering window unresolvable" in r.message)
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
                # First call (the WINDOW ANCHOR read) succeeds; afterwards
                # raise (#3825 changed the read order: the anchor read is now
                # FIRST, so the seed is the org row, not a ledger row).
                if not hasattr(self, "_seeded_read"):
                    self._seeded_read = True
                    return self._seeded
                raise RuntimeError("Supabase down (simulated blip)")

        # The anchor resolves — an UNRESOLVABLE anchor is a different degrade
        # (the zero view with a placeholder label), and this test is about a
        # team that HAS usage whose metering read blows up.
        seeded = [{"id": "team-blip-002", "subscription_id": "sub-blip-002",
                   "current_period_start": "2026-09-03T00:00:00+00:00",
                   "current_period_end": "2026-10-03T00:00:00+00:00"}]
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

        # pro tier: 55,000 ops used in the org's current WINDOW — over the
        # allowance → overage. The org row carries no subscription, so the
        # window is the D13 calendar month in UTC; the ledger row must be
        # keyed on THAT window's start (the ledger key is the window, not the
        # month label).
        window = _calendar_month_period()
        fake = FakeControlPlane({
            "metering_records": [
                {"org_id": "team-1", "period_start": window.start_iso,
                 "period_end": window.end_iso, "period": window.label,
                 "write_ops": 55000},
            ],
            "organizations": [{"id": "team-1", "tier": "pro"}],
        })
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)

        usage = m.get_current_usage("team-1")
        assert usage["write_ops_used"] == 55000
        assert usage["period"] == window.label
        assert usage["overage_eligible"] is True  # pro tier
        # overage beyond the pro allowance, rounded up to the 10k block
        assert usage["overage_cost_usd"] is not None
        assert usage["overage_cost_usd"] > 0


# ── Period rollover ─────────────────────────────────────────────────────────

class TestPeriodRollover:
    """#3825 re-keyed this class.

    ``test_period_is_calendar_month_utc`` is DELETED, not updated: it asserted
    ``len(period) == 7`` and ``period[4] == "-"`` — i.e. it pinned the exact
    month-STRING semantics D10 removes, and a half-open window is not a
    7-character label. Keeping it green is incompatible with the decision. Its
    surviving intent (D13: an org with no subscription meters on the calendar
    month in UTC) is asserted below on the real window; the boundary-keyed
    half moved to ``tests/test_metering_period_window.py`` (T8/T11), where it
    drives the real increment instead of inserting ledger rows directly (the
    anti-pattern ``(c.3)`` rejects — a direct insert bypasses the writer, and
    the writer is where the row key is minted).
    """

    def test_period_without_subscription_is_the_calendar_month_window(
            self, reg_sdk):
        """D13: no ``subscription_id`` → the calendar month in UTC, as a
        half-open WINDOW (``period`` survives only as its derived label)."""
        from datetime import datetime

        _sdk, tid = reg_sdk
        period = _current_period(tid)  # the fixture's Team has no subscription
        now = datetime.now(UTC)
        assert period.label == f"{now.year}-{now.month:02d}"
        assert period.start.day == 1
        assert (period.start.hour, period.start.minute, period.start.second,
                period.start.microsecond) == (0, 0, 0, 0)
        assert (period.end - period.start).days in (28, 29, 30, 31)
        # the label is DERIVED from the window start, so it cannot drift from
        # the key it labels
        assert period.label == f"{period.start.year}-{period.start.month:02d}"


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
        assert has_overage("anon") is False

    def test_solo_tier_has_overage(self):
        # #4815: solo is a PAID tier → metered (no longer a hard cap).
        from tortoise.pricing import has_overage
        assert has_overage("solo") is True

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

    def test_none_org_id_noop(self):
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
        """A failed ASK-increment row write is logged, never raised.

        As in ``test_non_fatal_on_db_error`` (#3825), the failure is injected
        into the increment's own ``MERGE (m:MeteringRecord …)`` so the anchor
        read still resolves the window — only the increment is broken.
        """
        sdk, tid = reg_sdk
        from tortoise.metering import record_ask_usage

        _break_increment_only(monkeypatch, sdk)
        with caplog.at_level(logging.WARNING, logger="tortoise.metering"):
            assert record_ask_usage(tid, tokens_in=1) is None  # non-fatal
        assert any("increment failed" in r.message for r in caplog.records), (
            [r.message for r in caplog.records]
        )

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
        """P2-23, RE-KEYED to a SUBSCRIPTION window (#3825 / D10).

        The old form froze at the last second of a CALENDAR MONTH, because the
        row key used to be a month label. Under D10 a subscription org's row
        key is its BILLING PERIOD, and rollover happens when the ANCHOR
        advances — NOT when the clock crosses a month boundary and NOT from
        ``now`` (the frozen clock below never moves, so a self-anchored
        mutation would collapse both writes into one row and RED here).

        A record written before the renewal lands in the OLD window; after the
        renewal the org records to a NEW row and the old row is frozen
        byte-identical.
        """
        from datetime import datetime

        from tortoise.metering import _current_period, get_ask_usage, record_ask_usage
        sdk, tid = reg_sdk
        reg = sdk._get_registry()

        def _anchor(start_iso: str, end_iso: str) -> None:
            reg.query(
                "MATCH (t:Team {id: $tid}) "
                "SET t.subscription_id = 'sub-straddle', "
                "    t.current_period_start = $ps, "
                "    t.current_period_end = $pe",
                params={"tid": tid, "ps": start_iso, "pe": end_iso},
            )

        _anchor("2026-08-20T00:00:00+00:00", "2026-09-20T00:00:00+00:00")
        # Freeze 1 second BEFORE the renewal instant, and never move the clock
        # (a subclass of ``datetime`` so ``_anchor_instant``'s fromisoformat /
        # isinstance checks keep working under the patch).
        frozen = {"ts": datetime(2026, 9, 19, 23, 59, 59, tzinfo=UTC)}

        class _FrozenDT(datetime):
            @staticmethod
            def now(tz=None):
                return frozen["ts"]

        monkeypatch.setattr("tortoise.metering.datetime", _FrozenDT)
        record_ask_usage(tid, tokens_in=10)
        old = _current_period(tid)
        assert old.start_iso == "2026-08-20T00:00:00+00:00"
        assert get_ask_usage(tid)["ask_tokens_in"] == 10

        # Stripe renews: the new period starts at EXACTLY the old window's
        # end. Half-open [start, end) → the boundary instant belongs to the
        # NEW window.
        _anchor("2026-09-20T00:00:00+00:00", "2026-10-20T00:00:00+00:00")
        record_ask_usage(tid, tokens_in=20)
        new = _current_period(tid)
        assert new.start_iso == old.end_iso
        usage = get_ask_usage(tid)
        assert usage["period_start"] == new.start_iso
        assert usage["ask_tokens_in"] == 20  # the old window's row is frozen
        # ...and the prior row is still on the ledger, unchanged
        rows = reg.query(
            "MATCH (m:MeteringRecord {org_id: $tid}) "
            "RETURN m.period_start, m.ask_tokens_in "
            "ORDER BY m.period_start",
            params={"tid": tid},
        ).result_set
        assert rows == [[old.start_iso, 10], [new.start_iso, 20]]

    def test_migration_code_contract(self, monkeypatch):
        """Plan Task 6 Step 1: the migration↔code contract — the RPC name
        ``metering_increment_ask`` and the ask_* column set the CODE calls
        MUST match the real migration files, so a column reword or RPC rename
        in either direction fails loudly. Covers both
        record_ask_usage (supabase branch → RPC body keys) and
        get_ask_usage (supabase branch → the ask_* select).

        #3543: migrations are append-only (``check-migration-append-only``), so
        the RPC's parameter rename lives in the NEWEST file that redefines it —
        the tenancy migration (``20260915000001``), which DROPs and recreates
        the function with ``p_org_id``. The column set is still owned by the
        original metering migration, which stays byte-identical.
        """
        import re as _re
        from pathlib import Path
        migdir = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
        mig = (migdir / "20260829000001_metering_ask_columns.sql").read_text()
        # (a) the ADD COLUMN set the migration defines
        cols = set(_re.findall(r"ADD COLUMN IF NOT EXISTS\s+(\w+)", mig))
        assert cols == {"ask_calls", "ask_tokens_in", "ask_tokens_out",
                        "ask_cost_usd"}
        # the token counters are bigint (P2 — the ~20.7B token/month
        # envelope is ~10x over the integer range)
        assert "ask_tokens_in   bigint" in mig
        assert "ask_tokens_out  bigint" in mig
        # (b) the RPC name + parameter set the EFFECTIVE migration defines —
        # the newest file that recreates it (append-only: a signature change
        # cannot be an edit to 20260829000001/20260915000001, so #3825's
        # migration owns it now — and it DROPs before CREATE, because a new
        # argument list would otherwise be an OVERLOAD that leaves the old
        # month-keyed function callable).
        eff = (migdir / "20260918000001_metering_period_window.sql").read_text()
        sig = _re.search(
            r"CREATE (?:OR REPLACE )?FUNCTION public\.metering_increment_ask\((.*?)\)\s*RETURNS",
            eff, _re.S)
        assert sig is not None, (
            "the effective migration must recreate metering_increment_ask")
        params = set(_re.findall(r"p_(\w+)\s+\w+", sig.group(1)))
        # #3825: the month label is replaced by the half-open WINDOW. Asserted
        # with ==, never a subset — a subset stops catching a dropped bound.
        assert params == {"org_id", "period_start", "period_end", "calls",
                          "tokens_in", "tokens_out", "cost_usd"}
        # the OLD month-keyed signature must be DROPPED, not merely shadowed
        assert _re.search(
            r"DROP FUNCTION IF EXISTS public\.metering_increment_ask\(\s*text,\s*text,",
            eff), "the month-keyed overload must be dropped, not left callable"
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
        # #3825: the WINDOW replaces the month label. Asserted with ==, never a
        # subset — a subset would stop detecting a dropped window bound.
        assert set(body) == {"p_org_id", "p_period_start", "p_period_end",
                             "p_calls", "p_tokens_in", "p_tokens_out",
                             "p_cost_usd"}
        assert body["p_org_id"] == "team-1"
        assert body["p_tokens_in"] == 100 and body["p_tokens_out"] == 50
        assert body["p_period_start"] and body["p_period_end"]
        # (d) the supabase-mode READ path selects the SAME ask_* columns, keyed
        # on the SAME window start. Full replacement (not a seed/append) so the
        # assertion cannot be satisfied by the row the RPC emulation just
        # wrote: DISTINCT values make a mis-wired read show up.
        fake.tables["metering_records"] = [{
            "org_id": "team-1",
            "period_start": body["p_period_start"],
            "period_end": body["p_period_end"],
            "period": "2026-09",
            "ask_calls": 7, "ask_tokens_in": 700, "ask_tokens_out": 70,
            "ask_cost_usd": 0.007,
        }]
        usage = get_ask_usage("team-1")
        assert usage["ask_calls"] == 7
        assert usage["ask_tokens_in"] == 700
        assert usage["ask_tokens_out"] == 70
        assert abs(usage["ask_cost_usd"] - 0.007) < 1e-9


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
