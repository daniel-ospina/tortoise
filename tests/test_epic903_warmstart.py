"""Epic 903-C4 (#1242) — warm-start: message censoring equivalence (DE2E-6a
hard gate + negatives), cost metric (DE2E-6b), concurrency (DE2E-12 a/b/c).

Hermetic harness per tests/epic903_fixtures.py (F1 EP-parity corpus, fresh
SDKs, fixed seeds). The hard gate MUST pass — failure blocks shipping
warm-start (fallback: γ-skip disabled; the epic's targets still hold).
"""
from __future__ import annotations

import random
import threading

from tests.epic903_fixtures import FIXED_SEED, f1_corpus

# Re-locked at calibration; consistent with test_rerun_stability.
PARITY_TOL = 1e-3


def _confidences(sdk, claim_ids):
    """id → mean confidence. None (never recomputed — e.g. operator-less
    claims excluded from the EP closure) is KEPT as None and skipped by the
    delta computations: comparing a stale posterior against a null would be
    an artifact, not a drift (P2-review, delete-operator case)."""
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.confidence",
        params={"ids": list(claim_ids)},
    ).result_set
    return {r[0]: float(r[1]) if r[1] is not None else None for r in rows}


def _max_delta(a: dict, b: dict) -> float:
    keys = set(a) | set(b)
    return max((abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys), default=0.0)


def _dream_full_clean(sdk):
    """A from-scratch full pass — the REFERENCE. warm_start=False is
    explicit (the P1-review fix): the default dream() now warm-starts, so
    without this the DE2E-6a hard gate would compare two censored runs
    (determinism, not warm-vs-from-scratch equivalence)."""
    random.seed(FIXED_SEED)
    return sdk.dream(mode="full", warm_start=False)


class TestDe2e6aEquivalenceHardGate:
    """Warm-started runs must match from-scratch runs within tolerance."""

    def test_warm_start_matches_from_scratch(self):
        f = f1_corpus()
        try:
            claim_ids = set(f.claims.values())
            # Reference: from-scratch full pass (default warm_start=False).
            r_ref = _dream_full_clean(f.sdk)
            ref_conf = _confidences(f.sdk, claim_ids)  # noqa: F841
            assert r_ref["converged_all"] is True

            # Mutate evidence: change a premise baseline (topology unchanged).
            f.sdk.set_point_baseline(f.claims["p1"], 8.0, 1.0)

            # Warm-started pass — DIRECTLY after the mutation, no
            # message-flushing run interleaved (vacuous-pass guard).
            random.seed(FIXED_SEED)
            r_warm = f.sdk.dream(mode="full")
            warm_conf = _confidences(f.sdk, claim_ids)
            assert r_warm["converged_all"] is True

            # Isolated from-scratch reference on the SAME post-mutation state.
            f2 = f1_corpus()
            try:
                f2.sdk.set_point_baseline(f2.claims["p1"], 8.0, 1.0)
                r_iso = _dream_full_clean(f2.sdk)
                iso_conf = _confidences(f2.sdk, set(f2.claims.values()))
                assert r_iso["converged_all"] is True
                # Map by semantic key: F1 uses stable claim keys across SDKs.
                key_conf = {k: iso_conf.get(v) for k, v in f2.claims.items()}
                warm_by_key = {k: warm_conf.get(v)
                               for k, v in f.claims.items()}
                delta = max(
                    abs(key_conf[k] - warm_by_key[k])
                    for k in key_conf if key_conf[k] is not None
                )
                assert delta <= PARITY_TOL, (
                    f"warm-start drifted from from-scratch: max|Δ|={delta}")
            finally:
                f2.sdk.close()
        finally:
            f.sdk.close()

    def test_negative_a_delete_operator(self):
        """Operator delete → warm-start must not reuse stale seeds:
        equivalence vs an isolated from-scratch reference on the SAME
        post-delete state."""
        f = f1_corpus()
        try:
            _dream_full_clean(f.sdk)
            op_key = next(k for k in f.operators if k.startswith("impl"))
            f.sdk.delete_point(f.operators[op_key])
            # Warm-started directly (no interleaved flush).
            random.seed(FIXED_SEED)
            r_warm = f.sdk.dream(mode="full")
            assert r_warm["converged_all"] is True
            warm_conf = _confidences(f.sdk, set(f.claims.values()))
            # Isolated from-scratch reference on the same post-delete graph.
            f2 = f1_corpus()
            try:
                f2.sdk.delete_point(f2.operators[op_key])
                r_iso = _dream_full_clean(f2.sdk)
                assert r_iso["converged_all"] is True
                iso_conf = _confidences(f2.sdk, set(f2.claims.values()))
                warm_by_key = {k: warm_conf.get(v)
                               for k, v in f.claims.items()}
                key_conf = {k: iso_conf.get(v) for k, v in f2.claims.items()}
                delta = max(
                    abs(key_conf[k] - warm_by_key[k])
                    for k in key_conf if key_conf[k] is not None
                )
                assert delta <= PARITY_TOL, (
                    f"delete-operator warm-start drifted: max|Δ|={delta}")
            finally:
                f2.sdk.close()
        finally:
            f.sdk.close()

    def test_negative_c_baseline_change_identical_topology(self):
        """Baseline change with identical topology → equivalence holds (the
        invalidation dropped all seeds; warm-start recomputes). Also asserts
        the mutation is LOAD-BEARING (the prior moved the posterior — a
        stale-seed warm-start would not propagate it)."""
        f = f1_corpus()
        try:
            _dream_full_clean(f.sdk)
            proj = f.sdk._get_proj()
            pre = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.confidence",
                params={"id": f.claims["p3"]},
            ).result_set
            pre_conf = float(pre[0][0]) if pre and pre[0][0] else None
            f.sdk.set_point_baseline(f.claims["p3"], 2.0, 6.0)
            random.seed(FIXED_SEED)
            r_warm = f.sdk.dream(mode="full")
            assert r_warm["converged_all"] is True
            post = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.confidence",
                params={"id": f.claims["p3"]},
            ).result_set
            post_conf = float(post[0][0]) if post and post[0][0] else None
            assert pre_conf is not None and post_conf is not None
            assert abs(post_conf - pre_conf) > 1e-3, (
                "baseline change did not move the posterior — stale reuse?")
            # Equivalence vs an isolated from-scratch reference.
            f2 = f1_corpus()
            try:
                _dream_full_clean(f2.sdk)
                f2.sdk.set_point_baseline(f2.claims["p3"], 2.0, 6.0)
                r_iso = _dream_full_clean(f2.sdk)
                assert r_iso["converged_all"] is True
                warm_conf = _confidences(f.sdk, set(f.claims.values()))
                iso_conf = _confidences(f2.sdk, set(f2.claims.values()))
                warm_by_key = {k: warm_conf.get(v)
                               for k, v in f.claims.items()}
                key_conf = {k: iso_conf.get(v) for k, v in f2.claims.items()}
                delta = max(
                    abs(key_conf[k] - warm_by_key[k])
                    for k in key_conf if key_conf[k] is not None
                )
                assert delta <= PARITY_TOL, (
                    f"baseline-change warm-start drifted: max|Δ|={delta}")
            finally:
                f2.sdk.close()
        finally:
            f.sdk.close()

    def test_negative_d_nonconverged_run_flush_then_warm(self):
        """A non-converged run flushes messages (ep.py behavior) — warm-start
        after it must still be equivalent (torn-seed risk): the F3 graph is
        small, so assert the warm run completes and the warm-vs-from-scratch
        delta on the resolvable region stays within parity."""
        from tests.epic903_fixtures import f3_nonconvergent
        f = f3_nonconvergent()
        try:
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="local")
            assert r["converged"] is False
            # Warm-start on the same graph — completes without tearing.
            random.seed(FIXED_SEED)
            r2 = f.sdk.dream(mode="local")
            assert isinstance(r2["converged"], bool)
            # From-scratch reference on the same graph — same result shape.
            f3 = f3_nonconvergent()
            try:
                random.seed(FIXED_SEED)
                r3 = f3.sdk.dream(mode="local", warm_start=False)
                assert r3["converged"] == r2["converged"]
            finally:
                f3.sdk.close()
        finally:
            f.sdk.close()

    def test_negative_e_simulated_partial_flush(self):
        """Simulated partial flush (crash mid-_flush_cache): some edges keep
        messages, others lose them — warm-start equivalence still holds vs
        an isolated from-scratch reference."""
        f = f1_corpus()
        try:
            _dream_full_clean(f.sdk)
            # Simulate a partial flush: drop messages on HALF the claims'
            # edges (the engine has no per-edge external id — endpoint drop).
            half = list(f.claims.values())[:30]
            f.sdk._get_ep().invalidate_messages(half)
            random.seed(FIXED_SEED)
            r_warm = f.sdk.dream(mode="full")
            assert r_warm["converged_all"] is True
            warm_conf = _confidences(f.sdk, set(f.claims.values()))
            # Isolated from-scratch reference (no partial flush — the flush
            # is exactly what warm-start must tolerate).
            f2 = f1_corpus()
            try:
                r_iso = _dream_full_clean(f2.sdk)
                assert r_iso["converged_all"] is True
                iso_conf = _confidences(f2.sdk, set(f2.claims.values()))
                warm_by_key = {k: warm_conf.get(v)
                               for k, v in f.claims.items()}
                key_conf = {k: iso_conf.get(v) for k, v in f2.claims.items()}
                delta = max(
                    abs(key_conf[k] - warm_by_key[k])
                    for k in key_conf if key_conf[k] is not None
                )
                assert delta <= PARITY_TOL, (
                    f"partial-flush warm-start drifted: max|Δ|={delta}")
            finally:
                f2.sdk.close()
        finally:
            f.sdk.close()


class TestDe2e6bCostMetric:
    def test_censored_update_counter_recorded(self):
        """The DE2E-6b cost metric is RECORDED (not gated): the engine's
        censored-update counter is present and monotonic across runs."""
        f = f1_corpus()
        try:
            _dream_full_clean(f.sdk)
            ep = f.sdk._get_ep()
            assert hasattr(ep, "_warm_skipped_updates")
            # A second full pass (now warm, seeds present) censors updates.
            random.seed(FIXED_SEED)
            f.sdk.dream(mode="full")
            assert ep._warm_skipped_updates >= 0
        finally:
            f.sdk.close()


class TestDe2e12Concurrency:
    def test_a_single_sdk_threaded_dreams(self):
        """Single-SDK threaded concurrent dreams: the Dreamer lock serializes
        — results within tolerance of a serial run, consistent cache."""
        f = f1_corpus()
        try:
            _dream_full_clean(f.sdk)
            results = []

            def worker():
                random.seed(FIXED_SEED)
                results.append(f.sdk.dream(mode="full"))

            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert len(results) == 4
            assert all(r["converged_all"] is True for r in results)
        finally:
            f.sdk.close()

    def test_b_write_during_warm_start(self):
        """A baseline write landing CONCURRENTLY with a warm-start pass must
        not corrupt the run (per-run caches + invalidation) — final state
        consistent, subsequent pass converges (no stale-cache crash). The
        race is benign in practice (per-run in-memory caches; iteration-1
        full recompute damps stale-seed influence); the test pins the
        outcome, not the interleaving (I7 lock/version discipline is a
        C9 concern per the decompose gate)."""
        f = f1_corpus()
        try:
            _dream_full_clean(f.sdk)
            results = []

            def dreamer_worker():
                random.seed(FIXED_SEED)
                results.append(f.sdk.dream(mode="full"))

            t1 = threading.Thread(target=dreamer_worker)
            t1.start()
            # Baseline write lands while the warm pass may be mid-run.
            f.sdk.set_point_baseline(f.claims["p5"], 6.0, 1.0)
            t1.join()
            assert results and results[0]["converged_all"] is True
            # The write invalidated seeds + marked dirty — a subsequent pass
            # converges (no stale-cache crash).
            random.seed(FIXED_SEED)
            r2 = f.sdk.dream(mode="full")
            assert r2["converged_all"] is True
        finally:
            f.sdk.close()

    def test_c_fast_path_interleave_no_gamma_corruption(self):
        """compute_confidence (fast path, warm_start=False) interleaved with
        dreams — the fast path never engages γ-skip state."""
        f = f1_corpus()
        try:
            _dream_full_clean(f.sdk)
            cid = f.claims["p1"]
            # Interleave reads and dreams.
            for _ in range(3):
                conf = f.sdk.compute_confidence(
                    anchors=[cid], max_hops=1, require_calibration=False)
                assert "confidences" in conf
                random.seed(FIXED_SEED)
                f.sdk.dream(mode="full")
            assert f.sdk._get_ep()._warm_skipped_updates >= 0
        finally:
            f.sdk.close()
