"""Epic 903-C7 (#1245) — dream observability: zero-output alarm + health
record (DE2E-8).

Hermetic harness per tests/epic903_fixtures.py (fresh_sdk disables the
calibration gate; live claims; fixed seeds — never wall-clock).
"""
from __future__ import annotations

import random

from tests.epic903_fixtures import FIXED_SEED, f1_corpus, f2_staleness_regions


class TestAlarm:
    def test_alarm_fires_on_zero_output_with_backlog(self):
        """A dead/silently-skipped dreamer: backlog exists AND the last pass
        produced zero output → alarm verdict True (A8 detection)."""
        f = f2_staleness_regions()
        try:
            # Simulate the silent-skip state: a backlog exists but the last
            # "pass" produced no output (EP stubbed to a no-op by the
            # caller — the metric record reflects it).
            f.sdk._mark_dirty([f.regions[0].claims[0]])
            f.sdk._dream_metrics["last_pass_at"] = "2026-08-14T00:00:00Z"
            f.sdk._dream_metrics["last_pass_output"] = 0
            h = f.sdk.dream_health_check()
            assert h["alarm_verdict"] is True
            assert h["alarm_reason"] == "zero_output_with_backlog"
            assert h["stale_backlog"] > 0
        finally:
            f.sdk.close()

    def test_positive_control_real_output_no_alarm(self):
        """A backlog WITH real output is healthy — the alarm MUST NOT fire
        (an implementation that fires on backlog alone fails this — the
        ignored-output-conjunct bug)."""
        f = f2_staleness_regions()
        try:
            f.sdk._mark_dirty([f.regions[0].claims[0]])
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="local")
            assert r["converged"] is True
            h = f.sdk.dream_health_check()
            assert h["alarm_verdict"] is False, (
                "real output must suppress the zero-output alarm")
            assert h["last_pass_output"] > 0
        finally:
            f.sdk.close()

    def test_no_false_positive_on_healthy_idle(self):
        """No backlog, no pass → no alarm (healthy idle).

        #1163: dirty state is graph-persisted — "no backlog" means clearing
        the graph flags too (the in-memory set alone rehydrates on the next
        health check).
        """
        f = f1_corpus()
        try:
            f.sdk._dirty_roots.clear()
            f.sdk._get_proj().g.query(
                "MATCH (n:Point) SET n.ep_dirty = null, n.ep_dirty_at = null")
            f.sdk._dream_metrics["last_pass_output"] = 0
            f.sdk._dream_metrics["last_pass_at"] = None
            h = f.sdk.dream_health_check()
            assert h["alarm_verdict"] is False
            assert h["stale_backlog"] == 0
        finally:
            f.sdk.close()


class TestHealthRecord:
    def test_metrics_recorded_after_passes(self):
        """Per-mode counts, coverage, failure rate populated after passes."""
        f = f2_staleness_regions()
        try:
            random.seed(FIXED_SEED)
            f.sdk.dream(mode="full")
            random.seed(FIXED_SEED)
            f.sdk.dream(mode="stale-first", budget=2)
            h = f.sdk.dream_health_check()
            assert h["per_mode_counts"].get("full", 0) >= 1
            assert h["per_mode_counts"].get("stale-first", 0) >= 1
            assert 0.0 <= h["coverage_pct"] <= 1.0
            assert 0.0 <= h["failure_rate"] <= 1.0
            assert h["last_pass_at"] is not None
            assert "region_attempts" in h
            assert "warm_skipped_updates" in h
        finally:
            f.sdk.close()

    def test_failure_rate_counts_non_convergence(self):
        """A non-converged pass increments the failure counter."""
        from tests.epic903_fixtures import f3_nonconvergent
        f = f3_nonconvergent()
        try:
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="local")
            assert r["converged"] is False
            h = f.sdk.dream_health_check()
            assert h["failure_rate"] > 0.0
        finally:
            f.sdk.close()
