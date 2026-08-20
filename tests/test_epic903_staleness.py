"""Epic 903-C10 (#1248) — staleness-error evaluation: DE2E-9 (Indicator 3
acceptance) + eval-spec gate entry.

F4 frozen-ground-truth fixture (tests/epic903_fixtures.f4_frozen_truth): the
oracle (converged confidence vector) is computed OUT-OF-BAND on a sandboxed
clone; the live fixture is returned unmutated. DE2E-9 mutates the live
fixture (region R — forced stalest-ranked), measures stale-error vs the
oracle, runs stale-first passes with increasing coverage, and asserts the
error shrinks below the pinned threshold (mean |Δ| ≤ 0.01 at coverage ≥ 80%,
re-locked at calibration).

Fixture-validation step: the mutation must be LOAD-BEARING — a from-scratch
recompute on the mutated fixture must move confidence by > the error
threshold (otherwise the test is trivially green).
"""
from __future__ import annotations

import random

from tests.epic903_fixtures import FIXED_SEED, f4_frozen_truth

# Pinned thresholds (pinned BEFORE implementation, re-locked at calibration):
# DE2E-9: mean |Δ| ≤ 0.01 once coverage ≥ 0.80 (threshold, not monotonicity).
ERROR_EPS = 0.01
COVERAGE_TARGET = 0.80
# Fixture-validation: the mutation must move the oracle by > ERROR_EPS.
MUTATION_MIN_MOVE = ERROR_EPS


def _conf_by_key(sdk, ids: dict[str, str]) -> dict[str, float]:
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.confidence",
        params={"ids": list(ids.values())},
    ).result_set
    by_id = {r[0]: float(r[1]) for r in rows if r[1] is not None}
    return {k: by_id.get(v) for k, v in ids.items()}


def _stale_error(actual: dict[str, float], oracle: dict[str, float]) -> float:
    keys = [k for k in oracle if oracle[k] is not None and actual.get(k) is not None]
    if not keys:
        return float("inf")
    return sum(abs(actual[k] - oracle[k]) for k in keys) / len(keys)


def _coverage(sdk, ids: dict[str, str]) -> float:
    """Fraction of live claims with a lastDreamedAt stamp (freshness)."""
    proj = sdk._get_proj()
    stamped = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids AND n.lastDreamedAt IS NOT NULL "
        "RETURN count(n)",
        params={"ids": list(ids.values())},
    ).result_set
    return int(stamped[0][0]) / len(ids) if ids else 0.0


def _post_mutation_oracle(ids: dict[str, str]) -> dict[str, float]:
    """Ground truth AFTER the mutation: build F4 on a fresh sandboxed clone,
    apply the same mutation, converge from-scratch (warm_start=False), read
    the converged means. The pre-mutation F4 oracle is NOT the truth once the
    evidence changed — the staleness error must be measured against the
    post-mutation truth (DE2E-9: freeze ground truth → apply change →
    measure un-dreamed vs dreamed error vs TRUTH)."""
    from tests.epic903_fixtures import _build_f4_graph, fresh_sdk
    clone, _ = fresh_sdk(prefix="tortoise_epic903_f4truth_")
    try:
        clone_ids, clone_ops = _build_f4_graph(clone)  # noqa: RUF059
        clone.set_point_baseline(clone_ids["r1"], 1.0, 8.0)  # same mutation
        random.seed(FIXED_SEED)
        clone.dream(mode="full", warm_start=False)
        proj = clone._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.confidence",
            params={"ids": list(clone_ids.values())},
        ).result_set
        by_id = {r[0]: float(r[1]) for r in rows if r[1] is not None}
        # Key by the SEMANTIC key (matches the live fixture's ids keys) —
        # the clone's ulid ids differ from the live fixture's.
        return {k: by_id.get(v) for k, v in clone_ids.items()}
    finally:
        clone.close()


class TestDe2e9StalenessError:
    def test_error_shrinks_below_threshold_as_coverage_grows(self):
        f = f4_frozen_truth(seed=FIXED_SEED)
        try:
            sdk = f.sdk
            # Mutate region R's evidence (r1's baseline — R's claims are
            # null-stamped → stalest-ranked → drained FIRST by stale-first).
            sdk.set_point_baseline(f.ids["r1"], 1.0, 8.0)
            # POST-mutation ground truth (sandboxed clone with the mutation).
            truth = _post_mutation_oracle(f.ids)

            # ── Fixture validation: the mutation is LOAD-BEARING ──
            # The post-mutation truth must differ from the pre-mutation
            # oracle by > MUTATION_MIN_MOVE on R (else the test is trivially
            # green — the mutation changed nothing).
            # r5 is R's convergence point (non-baseline → has a posterior);
            # r1 is a baseline leaf (immutable prior, null posterior — #844).
            move = abs(truth["r5"] - f.oracle["r5"])
            assert move > MUTATION_MIN_MOVE, (
                f"mutation not load-bearing: r5 moved only {move}")

            # ── Stale-error vs post-mutation truth at increasing coverage ──
            # The live fixture still carries the PRE-mutation posterior on
            # R's claims (never refreshed) → initial stale-error = the
            # mutation's effect.
            pre = _conf_by_key(sdk, f.ids)
            pre_err = _stale_error(pre, truth)
            assert pre_err > ERROR_EPS, (
                f"initial stale error {pre_err} should exceed {ERROR_EPS}")
            budget = 2
            errors = []
            coverages = []
            for _ in range(6):
                random.seed(FIXED_SEED)
                r = sdk.dream(mode="stale-first", budget=budget)
                assert r["converged_all"] is True
                conf = _conf_by_key(sdk, f.ids)
                errors.append(_stale_error(conf, truth))
                coverages.append(_coverage(sdk, f.ids))
            # Assert: error shrinks below ERROR_EPS once coverage ≥ target
            # (threshold, not monotonicity).
            best = min((e for e, c in zip(errors, coverages)  # noqa: B905
                        if c >= COVERAGE_TARGET), default=None)
            assert best is not None, (
                f"coverage never reached {COVERAGE_TARGET}: {coverages}")
            assert best <= ERROR_EPS, (
                f"staleness error {best:.4f} > {ERROR_EPS} at "
                f"coverage {COVERAGE_TARGET} — Indicator 3 acceptance "
                f"FAILED (errors: {[round(e,4) for e in errors]}, "
                f"coverages: {coverages})")
            # The error curve is recorded for the eval-spec gate (threshold,
            # not monotonicity).
            assert pre_err >= best, (
                "stale-first passes must not increase staleness error")
        finally:
            f.sdk.close()

    def test_unstamped_claims_rank_stalest(self):
        """Coverage plumbing: F4's live fixture starts with ZERO stamps —
        R, S, T all null → stalest-ranked; a stale-first pass drains the
        null region first (deterministic)."""
        f = f4_frozen_truth(seed=FIXED_SEED)
        try:
            assert _coverage(f.sdk, f.ids) == 0.0
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="stale-first", budget=2)
            assert r["converged_all"] is True
            assert _coverage(f.sdk, f.ids) > 0.0
        finally:
            f.sdk.close()
