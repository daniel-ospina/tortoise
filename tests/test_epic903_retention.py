"""Epic 903-C5 (#1243) — non-convergence retention + attempt cap +
stale_unresolved surfacing (DE2E-7a/7b, DE2E-12d interplay).

Hermetic harness per tests/epic903_fixtures.py (fresh_sdk disables the
calibration gate; live claims; F3 = calibrated fails-to-converge fixture).
"""
from __future__ import annotations

import random
from datetime import datetime, timezone  # noqa: F401

from tests.epic903_fixtures import FIXED_SEED, f2_staleness_regions, f3_nonconvergent

STAMP_OLD = "2026-01-15T00:00:00+00:00"


def _read_stamp(proj, pid: str) -> str | None:
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.lastDreamedAt",
        params={"id": pid},
    ).result_set
    return rows[0][0] if rows else None


class TestDe2e7aRetentionThenResolve:
    """Failed run keeps roots dirty + keeps old stamps; resolve → converge."""

    def test_failed_run_keeps_dirty_roots_and_stamps(self):
        f = f3_nonconvergent()
        try:
            proj = f.sdk._get_proj()
            a_id, b_id = f.claims["a"], f.claims["b"]
            for pid in (a_id, b_id):
                proj.g.query(
                    "MATCH (n:Point {id:$id}) SET n.lastDreamedAt = $ts",
                    params={"id": pid, "ts": STAMP_OLD},
                )
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="local")
            assert r["converged"] is False, "F3 must fail convergence"
            # A2-fix: affected claim-roots REMAIN dirty after the failed run.
            assert a_id in f.sdk._dirty_roots, "root a dropped on failure (A2)"
            assert b_id in f.sdk._dirty_roots, "root b dropped on failure (A2)"
            # W4: the failed run did NOT stamp fresh.
            assert _read_stamp(proj, a_id) == STAMP_OLD
            assert _read_stamp(proj, b_id) == STAMP_OLD
        finally:
            f.sdk.close()

    def test_resolve_evidence_then_converges_and_stamps(self):
        f = f3_nonconvergent()
        try:
            proj = f.sdk._get_proj()
            a_id, b_id = f.claims["a"], f.claims["b"]
            random.seed(FIXED_SEED)
            r1 = f.sdk.dream(mode="local")
            assert r1["converged"] is False
            # Resolve the conflict: remove the 2:1 IMPL:NAND imbalance that
            # sustains the limit cycle (delete the extra IMPL operators).
            for op_id in f.operators["impl"][1:]:
                f.sdk.delete_point(op_id)
            random.seed(FIXED_SEED)
            r2 = f.sdk.dream(mode="local")
            assert r2["converged"] is True, "resolved F3 must converge"
            # Roots cleared + stamped by the converged run.
            assert a_id not in f.sdk._dirty_roots
            assert b_id not in f.sdk._dirty_roots
            assert _read_stamp(proj, a_id) is not None
            assert _read_stamp(proj, b_id) is not None
        finally:
            f.sdk.close()


class TestDe2e7bAttemptCap:
    """Cap → root dropped from dirty + surfaced as stale_unresolved."""

    def test_cap_surfaces_stale_unresolved(self):
        f = f3_nonconvergent()
        try:
            a_id, b_id = f.claims["a"], f.claims["b"]
            f.sdk.retry_attempt_cap = 2  # cap low for the test
            random.seed(FIXED_SEED)
            for _ in range(3):  # 3 attempts > cap 2
                f.sdk.dream(mode="local")
            state = f.sdk.dream_health_state()
            unresolved = state["stale_unresolved"]
            # Both roots capped and dropped from the dirty set.
            assert a_id not in f.sdk._dirty_roots
            assert b_id not in f.sdk._dirty_roots
            assert a_id in unresolved and b_id in unresolved
            # P2-review tightening: exact attempt count (cap=2 → the third
            # attempt is the one that caps → attempts==3), retry trackers
            # emptied for capped roots, clock base documented.
            assert state["clock"] == "monotonic"
            for root in (a_id, b_id):
                rec = unresolved[root]
                assert rec["reason"] == "non_converged"
                assert rec["backoff_state"] == "capped"
                assert rec["attempts"] == 3
                assert root not in state["retry_attempts"]
                assert root not in state["retry_backoff_until"]
        finally:
            f.sdk.close()

    def test_attempts_reset_on_convergence(self):
        f = f2_staleness_regions()
        try:
            f.sdk._mark_dirty([f.regions[0].claims[0]])
            f.sdk._register_failed_attempt({f.regions[0].claims[0]})
            assert f.sdk._retry_attempts.get(f.regions[0].claims[0]) == 1
            # A converged local dream clears the root + resets its state.
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="local")
            assert r["converged"] is True
            assert f.sdk._retry_attempts.get(f.regions[0].claims[0]) is None
        finally:
            f.sdk.close()


class TestBackoffState:
    def test_backoff_recorded_and_injectable_clock(self):
        f = f3_nonconvergent()
        try:
            a_id = f.claims["a"]
            f.sdk._retry_clock = lambda: 1000.0  # injectable fake clock
            f.sdk._register_failed_attempt({a_id})
            assert f.sdk._retry_attempts[a_id] == 1
            assert f.sdk._retry_backoff_until[a_id] == 1002.0  # 2**1 s
            assert f.sdk._is_backed_off(a_id) is True  # now=1000 < until=1002
            f.sdk._retry_clock = lambda: 1003.0
            assert f.sdk._is_backed_off(a_id) is False
        finally:
            f.sdk.close()


class TestDe2e12dCrashInterplay:
    """Crash-mid-pass: in-memory dirty set lost → un-stamped claims
    re-selected by the next scheduled pass via null/old stamps (the W2
    staleness-ranking union self-heals)."""

    def test_unstamped_claims_reselect_after_dirty_set_loss(self):
        f = f2_staleness_regions()
        try:
            # Simulate a crash: the dirty state is gone — both the in-memory
            # set AND the graph flags (#1163: the graph is the persisted
            # source of truth, so a genuine dirty-set loss clears both), and
            # the null-stamp region was never dreamed (stamps are null).
            f.sdk._dirty_roots.clear()
            proj = f.sdk._get_proj()
            proj.g.query(
                "MATCH (n:Point) SET n.ep_dirty = null, n.ep_dirty_at = null")
            null = [c for c in f.regions if c.name == "null"][0]  # noqa: RUF015
            # Null-stamp claims must be null (never dreamed → crash window).
            for cid in null.claims:
                assert _read_stamp(proj, cid) is None
            # Next scheduled pass (stale-first) re-selects them via the
            # null-as-stalest ranking — no dirty set needed.
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="stale-first", budget=2)
            assert r["mode"] == "stale-first"
            assert set(null.claims) <= set(r["affected_claims"]), (
                "null-stamp claims must re-enter the window after crash"
            )
        finally:
            f.sdk.close()
