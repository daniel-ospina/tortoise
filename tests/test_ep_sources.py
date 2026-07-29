"""EP source credibility validation — log-scale aggregation experiments.

Tests the planned log-scale source aggregation model:
  effective_pc = base_pc × log₂(N + 1)
  effective_prior = Beta(1 + effective_pc_pos, 1 + effective_pc_neg)

Covers:
  - 10 orthogonal situations (Section 3 of experiment design doc)
  - 3 graph topologies: linear chain, loopy single-source, loopy dual-source
  - Log-scale formula validation
  - Cross-scenario comparisons

Design doc: docs/ep-source-credibility-experiment.md
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.sdk import TortoiseSDK


# ═══════════════════════════════════════════════════════════════════
# Constants — Source Credibility Tiers
# ═══════════════════════════════════════════════════════════════════

TIER_MAP = {
    "T0": (10.0, 1.0),   # Gold:       pc=9.0, mean=0.9091
    "T1": (5.0, 1.0),    # High:       pc=4.0, mean=0.8333
    "T2": (3.0, 1.0),    # Medium:     pc=2.0, mean=0.7500
    "T3": (2.0, 1.0),    # Low:        pc=1.0, mean=0.6667
    "T4": (1.1, 1.0),    # Unverified: pc=0.1, mean=0.5238
}

TIER_PC = {tier: alpha - 1.0 for tier, (alpha, beta) in TIER_MAP.items()}

# Tolerance constants
DELTA = 1e-4       # Exact comparison (direct prior math)
EPSILON = 0.02     # EP convergence tolerance
EPSILON_LOOP = 0.03  # Loopy graph tolerance


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def log_aggregate_pc(base_pc: float, n_sources: int) -> float:
    """Compute log-scale aggregated pseudo-count.

    Formula: base_pc × log₂(N + 1)

    Prevents Sybil attacks by making credibility scale sub-linearly
    with number of same-tier sources.
    """
    if n_sources <= 0:
        return 0.0
    return base_pc * math.log2(n_sources + 1)


def log_aggregate_prior(tier: str, n_sources: int) -> tuple[float, float]:
    """Compute Beta(α, β) prior for N same-tier sources with log aggregation.

    Returns (alpha, beta) for Beta distribution.
    """
    base_alpha, base_beta = TIER_MAP[tier]
    base_pc = base_alpha - 1.0
    effective_pc = log_aggregate_pc(base_pc, n_sources)
    return (1.0 + effective_pc, 1.0)


def log_aggregate_prior_mixed(
    pos_sources: dict[str, int],  # {tier: count} — supporting sources
    neg_sources: dict[str, int] | None = None,  # {tier: count} — NAND sources
) -> tuple[float, float]:
    """Compute Beta(α, β) prior for mixed-tier sources with log aggregation.

    Positive sources contribute to alpha (success pseudo-count).
    Negative (NAND) sources contribute to beta (failure pseudo-count).
    """
    total_pc_pos = sum(
        log_aggregate_pc(TIER_PC[tier], count)
        for tier, count in pos_sources.items()
    )
    total_pc_neg = 0.0
    if neg_sources:
        total_pc_neg = sum(
            log_aggregate_pc(TIER_PC[tier], count)
            for tier, count in neg_sources.items()
        )
    return (1.0 + total_pc_pos, 1.0 + total_pc_neg)


def mean_from_beta(alpha: float, beta: float) -> float:
    """Compute mean of Beta(α, β) distribution."""
    return alpha / (alpha + beta)


@contextmanager
def fresh_sdk():
    """Yield a TortoiseSDK with a fresh temp database.

    Ensures test isolation — each test gets a clean FalkorDBLite instance.
    """
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_ep_src_"), "test.db")
    sdk = TortoiseSDK(db_path)
    try:
        yield sdk
    finally:
        try:
            sdk.close()
        except Exception:
            pass


def make_point(sdk: TortoiseSDK, content: str, kind: str = "statement") -> dict:
    """Create a Point node. Returns {id, ...}."""
    return sdk.create_point(kind, content)


def make_operator(sdk: TortoiseSDK, source_id: str, target_id: str,
                   op_type: str = "IMPL") -> dict:
    """Create an operator edge between two Points."""
    return sdk.create_operator(op_type, source_id, [target_id])


def set_source_evidence(sdk: TortoiseSDK, point_id: str, tier: str):
    """Set Beta prior on a Point to simulate a single source of given tier.

    For single sources, this is equivalent to calling log_aggregate_prior(tier, 1).
    """
    alpha, beta = TIER_MAP[tier]
    sdk.set_point_baseline(point_id, alpha, beta)


def set_aggregated_evidence(
    sdk: TortoiseSDK,
    point_id: str,
    pos_sources: dict[str, int],
    neg_sources: dict[str, int] | None = None,
):
    """Set aggregated evidence from multiple sources on a Point.

    Uses log-scale aggregation to combine same-tier sources before
    setting the Beta prior via set_point_baseline.
    """
    alpha, beta = log_aggregate_prior_mixed(pos_sources, neg_sources)
    sdk.set_point_baseline(point_id, alpha, beta)


def run_ep(sdk: TortoiseSDK) -> dict:
    """Run EP belief propagation and return {iterations, converged, confidences}."""
    return sdk.compute_confidence()


def get_conf(result: dict, node_id: str) -> float:
    """Extract mean confidence for a node from EP result."""
    return result["confidences"][node_id]["mean"]


# ═══════════════════════════════════════════════════════════════════
# Scenario Builders
# ═══════════════════════════════════════════════════════════════════

def build_scenario_a(sdk: TortoiseSDK) -> tuple[str, str]:
    """Linear chain: Point_A →[IMPL]→ Claim_B.

    Returns (a_id, b_id).
    Sources are attached to Point_A. Belief propagates to Claim_B.
    """
    a = make_point(sdk, "Point A: evidence aggregation point")
    b = make_point(sdk, "Claim B: conclusion")
    make_operator(sdk, a["id"], b["id"], "IMPL")
    return a["id"], b["id"]


def build_scenario_b(sdk: TortoiseSDK) -> tuple[str, str, str]:
    """Loopy cluster: A →[IMPL]→ B →[IMPL]→ C →[IMPL]→ A.

    Returns (a_id, b_id, c_id).
    Three-node directed cycle. Sources attached to Point_A only (single-source variant).
    """
    a = make_point(sdk, "Point A: loopy cluster")
    b = make_point(sdk, "Point B: loopy cluster")
    c = make_point(sdk, "Point C: loopy cluster")
    make_operator(sdk, a["id"], b["id"], "IMPL")
    make_operator(sdk, b["id"], c["id"], "IMPL")
    make_operator(sdk, c["id"], a["id"], "IMPL")
    return a["id"], b["id"], c["id"]


def build_scenario_c(sdk: TortoiseSDK) -> tuple[str, str, str]:
    """Loopy cluster with dual sources on A and B.

    Same topology as Scenario B. Caller sets sources on both A and B.
    """
    return build_scenario_b(sdk)


# ═══════════════════════════════════════════════════════════════════
# Unit Tests — Log-Scale Aggregation Math (no EP needed)
# ═══════════════════════════════════════════════════════════════════

class TestLogAggregationMath:
    """Pure math: log-scale aggregation formulas.

    These tests validate the mathematical correctness of the aggregation
    formulas without requiring a running EP engine or FalkorDB.
    """

    def test_base_case_n1(self):
        """N=1 → effective_pc = base_pc (no scaling)."""
        for tier, (alpha, _beta) in TIER_MAP.items():
            base_pc = alpha - 1.0
            effective = log_aggregate_pc(base_pc, 1)
            assert abs(effective - base_pc) < DELTA, \
                f"{tier}: effective_pc={effective} != base_pc={base_pc}"

    def test_n0_returns_zero(self):
        """N=0 sources → zero pseudo-count."""
        assert log_aggregate_pc(9.0, 0) == 0.0
        assert log_aggregate_pc(0.1, 0) == 0.0

    def test_diminishing_returns(self):
        """Each additional source adds less pseudo-count than the previous one."""
        base_pc = 1.0
        gains = []
        for n in range(1, 10):
            pc_n = log_aggregate_pc(base_pc, n)
            pc_next = log_aggregate_pc(base_pc, n + 1)
            gains.append(pc_next - pc_n)
        # Strictly diminishing
        for i in range(len(gains) - 1):
            assert gains[i] > gains[i + 1] - DELTA, \
                f"Gain at n={i+1} ({gains[i]:.6f}) not > gain at n={i+2} ({gains[i+1]:.6f})"

    def test_anti_sybil_extreme(self):
        """1000 T4 sources barely exceed 1 T3 source (anti-Sybil protection)."""
        t4_pc_1000 = log_aggregate_pc(TIER_PC["T4"], 1000)
        t3_pc_1 = TIER_PC["T3"]
        # 1000 T4: 0.1 × log₂(1001) ≈ 0.1 × 9.966 = 0.997
        # 1 T3: 1.0
        assert abs(t4_pc_1000 - t3_pc_1) < 0.1, \
            f"1000 T4 pc={t4_pc_1000:.4f}, 1 T3 pc={t3_pc_1:.4f}"

    def test_log_aggregate_prior_single(self):
        """log_aggregate_prior for N=1 returns base tier prior exactly."""
        for tier, (alpha, beta) in TIER_MAP.items():
            a, b = log_aggregate_prior(tier, 1)
            assert abs(a - alpha) < DELTA, f"{tier}: alpha mismatch"
            assert abs(b - beta) < DELTA, f"{tier}: beta mismatch"

    def test_prior_mean_monotonic_with_n(self):
        """As N increases, the prior mean increases monotonically for all tiers."""
        for tier in TIER_MAP:
            prev_mean = 0.0
            for n in [1, 2, 3, 5, 10, 100]:
                a, b = log_aggregate_prior(tier, n)
                m = mean_from_beta(a, b)
                assert m > prev_mean, \
                    f"{tier} N={n}: mean {m:.4f} <= prev {prev_mean:.4f}"
                prev_mean = m

    def test_prior_never_exceeds_1(self):
        """No matter how many sources, mean stays below 1.0."""
        for tier in TIER_MAP:
            for n in [1, 10, 100, 1000, 10000]:
                a, b = log_aggregate_prior(tier, n)
                m = mean_from_beta(a, b)
                assert m < 1.0, \
                    f"{tier} N={n}: mean {m:.6f} exceeded 1.0"

    def test_prior_above_baseline(self):
        """All tiers at N≥1 produce mean > 0.5 (above uniform)."""
        for tier in TIER_MAP:
            for n in [1, 2, 10]:
                a, b = log_aggregate_prior(tier, n)
                m = mean_from_beta(a, b)
                assert m > 0.5, \
                    f"{tier} N={n}: mean {m:.4f} <= 0.5"

    def test_mixed_tier_aggregation(self):
        """Mixed tiers sum pseudo-counts correctly.

        1×T0 + 5×T4:
          T0: pc=9 × log₂(2) = 9.0
          T4: pc=0.1 × log₂(6) = 0.259
          Total: 9.259 → Beta(10.259, 1) → mean ≈ 0.9112
        """
        a, b = log_aggregate_prior_mixed(
            pos_sources={"T0": 1, "T4": 5},
        )
        expected_alpha = 1.0 + (9.0 * math.log2(2)) + (0.1 * math.log2(6))
        assert abs(a - expected_alpha) < DELTA
        assert abs(b - 1.0) < DELTA

    def test_nand_contribution_to_beta(self):
        """NAND sources contribute to beta (pc_neg), not alpha (pc_pos).

        1×T0 pos + 1×T4 NAND:
          pc_pos = 9.0, pc_neg = 0.1
          Beta(10, 1.1) → mean = 10/11.1 ≈ 0.9009
        """
        a, b = log_aggregate_prior_mixed(
            pos_sources={"T0": 1},
            neg_sources={"T4": 1},
        )
        assert abs(a - 10.0) < DELTA
        assert abs(b - 1.1) < DELTA
        mean = mean_from_beta(a, b)
        assert mean < mean_from_beta(10.0, 1.0)  # NAND reduces mean
        assert mean > 0.85  # Gold still dominates weak NAND

    def test_nand_only_below_baseline(self):
        """NAND sources alone produce mean below 0.5 (contradiction without support)."""
        a, b = log_aggregate_prior_mixed(
            pos_sources={},
            neg_sources={"T4": 1},
        )
        mean = mean_from_beta(a, b)
        # Beta(1, 1.1) → mean = 1/2.1 ≈ 0.4762
        assert mean < 0.50

    def test_equal_tier_contradiction_neutral(self):
        """Equal positive and negative same-tier → Beta symmetric → mean = 0.5."""
        a, b = log_aggregate_prior_mixed(
            pos_sources={"T0": 1},
            neg_sources={"T0": 1},
        )
        mean = mean_from_beta(a, b)
        assert abs(mean - 0.50) < DELTA

    def test_log2_values(self):
        """Known log₂ values for key N values."""
        cases = [
            (1, 1.0),
            (2, math.log2(3)),    # ~1.585
            (3, 2.0),
            (7, 3.0),
            (10, math.log2(11)),  # ~3.459
            (15, 4.0),
            (31, 5.0),
        ]
        for n, expected in cases:
            result = log_aggregate_pc(1.0, n)  # base_pc=1 makes it pure log₂(N+1)
            assert abs(result - expected) < DELTA, \
                f"N={n}: log₂({n+1}) expected {expected:.4f}, got {result:.4f}"

    def test_scalability_ratio(self):
        """N=10 vs N=100: 10× sources → < 2× pseudo-count growth."""
        pc10 = log_aggregate_pc(1.0, 10)   # log₂(11) ≈ 3.459
        pc100 = log_aggregate_pc(1.0, 100)  # log₂(101) ≈ 6.658
        ratio = pc100 / pc10
        assert ratio < 2.0, f"Ratio {ratio:.3f} >= 2.0 — log-scale not effective enough"

    def test_t0_saturation(self):
        """T0 marginal returns diminish: per-source gain_1/2 > per-source gain_5/10."""
        def t0_pc(n):
            return log_aggregate_pc(TIER_PC["T0"], n)

        # Per-source gain: how much each additional source adds on average
        per_source_1_2 = (t0_pc(2) - t0_pc(1)) / 1   # 1 source added
        per_source_5_10 = (t0_pc(10) - t0_pc(5)) / 5   # 5 sources added
        assert per_source_1_2 > per_source_5_10, \
            f"T0: per-source gain (1→2)={per_source_1_2:.3f} not > per-source (5→10)={per_source_5_10:.3f}"


# ═══════════════════════════════════════════════════════════════════
# Integration Tests — EP with Sources (Situations 1–10)
# ═══════════════════════════════════════════════════════════════════

class TestSituation1_NoSourceToT4:
    """Situation 1: No source → add single T4.

    Verify that any source (even weakest T4) increases confidence above baseline.
    """

    def test_t4_increases_confidence(self):
        # Baseline (no source)
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            baseline = run_ep(sdk)
            conf_a_base = get_conf(baseline, a_id)
            conf_b_base = get_conf(baseline, b_id)

        # With T4 source
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T4")
            with_t4 = run_ep(sdk)
            conf_a_t4 = get_conf(with_t4, a_id)
            conf_b_t4 = get_conf(with_t4, b_id)

        # S1.1: T4 increases above baseline
        assert conf_a_t4 > conf_a_base + 0.01

        # S1.2: Increase is in expected range (~0.024)
        delta = conf_a_t4 - conf_a_base
        assert 0.015 < delta < 0.035, \
            f"Delta={delta:.4f} out of [0.015, 0.035]"

        # S1.3: A stays well above 0.51
        assert conf_a_t4 > 0.51

        # S1.4: Downstream B should not decrease
        assert conf_b_t4 >= conf_b_base - EPSILON

    def test_t4_predicted_mean(self):
        """T4 prior mean should match Beta(1.1, 1) = 0.5238."""
        with fresh_sdk() as sdk:
            a_id, _b_id = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T4")
            result = run_ep(sdk)
            conf = get_conf(result, a_id)
            assert abs(conf - 0.5238) < EPSILON, \
                f"Expected 0.5238, got {conf:.4f}"


class TestSituation2_TierProportional:
    """Situation 2: No source → add T3/T2/T1/T0 — proportional increase.

    Verify monotonic increase with source tier and correct absolute values.
    """

    EXPECTED_MEANS = {
        "T4": 0.5238,
        "T3": 0.6667,
        "T2": 0.7500,
        "T1": 0.8333,
        "T0": 0.9091,
    }

    @pytest.mark.parametrize("tier,expected", [
        ("T4", 0.5238),
        ("T3", 0.6667),
        ("T2", 0.7500),
        ("T1", 0.8333),
        ("T0", 0.9091),
    ])
    def test_single_source_mean(self, tier, expected):
        """Each single source should produce its expected prior mean."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, tier)
            conf = get_conf(run_ep(sdk), a_id)
            assert abs(conf - expected) < EPSILON, \
                f"{tier}: expected {expected:.4f}, got {conf:.4f}"

    def test_monotonic_tier_ordering(self):
        """T4 < T3 < T2 < T1 < T0 — strict ordering."""
        confs = {}
        for tier in ["T4", "T3", "T2", "T1", "T0"]:
            with fresh_sdk() as sdk:
                a_id, _b_id = build_scenario_a(sdk)
                set_source_evidence(sdk, a_id, tier)
                result = run_ep(sdk)
                confs[tier] = get_conf(result, a_id)

        ordered = ["T4", "T3", "T2", "T1", "T0"]
        for i in range(len(ordered) - 1):
            assert confs[ordered[i]] < confs[ordered[i + 1]], (
                f"{ordered[i]}: {confs[ordered[i]]:.4f} >= "
                f"{ordered[i+1]}: {confs[ordered[i+1]]:.4f}")

    def test_t0_vs_t4_gap(self):
        """T0 substantially exceeds T4 (gap > 0.30)."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T4")
            conf_t4 = get_conf(run_ep(sdk), a_id)

        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T0")
            conf_t0 = get_conf(run_ep(sdk), a_id)

        assert conf_t0 - conf_t4 > 0.30, \
            f"T0-T4 gap = {conf_t0 - conf_t4:.4f}, expected > 0.30"

    def test_all_distinct_gaps(self):
        """All adjacent tiers separated by at least 0.05."""
        confs = {}
        for tier in ["T4", "T3", "T2", "T1", "T0"]:
            with fresh_sdk() as sdk:
                a_id, _ = build_scenario_a(sdk)
                set_source_evidence(sdk, a_id, tier)
                confs[tier] = get_conf(run_ep(sdk), a_id)

        ordered = ["T4", "T3", "T2", "T1", "T0"]
        for i in range(len(ordered) - 1):
            gap = confs[ordered[i+1]] - confs[ordered[i]]
            assert gap > 0.05, \
                f"Gap {ordered[i]}→{ordered[i+1]}: {gap:.4f} ≤ 0.05"


class TestSituation3_CumulativeT4:
    """Situation 3: 1→2→3→10 T4 sources — cumulative with diminishing returns."""

    @pytest.mark.parametrize("n_sources,expected_mean", [
        (1, 0.5238),
        (2, 0.5368),
        (3, 0.5455),
        (5, 0.5571),
        (10, 0.5737),
    ])
    def test_cumulative_t4_expected_means(self, n_sources, expected_mean):
        """Each N produces the predicted mean within EP tolerance."""
        with fresh_sdk() as sdk:
            a_id, _b_id = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior("T4", n_sources)
            sdk.set_point_baseline(a_id, alpha, beta)
            result = run_ep(sdk)
            conf = get_conf(result, a_id)
            assert abs(conf - expected_mean) < EPSILON, \
                f"N={n_sources}: expected {expected_mean:.4f}, got {conf:.4f}"

    def test_diminishing_returns(self):
        """Each additional T4 gives less gain than the previous one."""
        gains = []
        prev_conf = None
        for n in range(1, 11):
            with fresh_sdk() as sdk:
                a_id, _ = build_scenario_a(sdk)
                alpha, beta = log_aggregate_prior("T4", n)
                sdk.set_point_baseline(a_id, alpha, beta)
                conf = get_conf(run_ep(sdk), a_id)
                if prev_conf is not None:
                    gains.append(conf - prev_conf)
                prev_conf = conf

        # Gains should be strictly decreasing (within floating-point tolerance)
        for i in range(len(gains) - 1):
            assert gains[i] > gains[i + 1] - DELTA, \
                f"Gain {i}: {gains[i]:.6f} not > gain {i+1}: {gains[i+1]:.6f}"

    def test_10_t4_below_t3(self):
        """10 T4 sources should NOT reach T3 credibility (anti-Sybil at work)."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior("T4", 10)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_10t4 = get_conf(run_ep(sdk), a_id)

        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T3")
            conf_1t3 = get_conf(run_ep(sdk), a_id)

        assert conf_10t4 < conf_1t3, \
            f"10×T4 conf={conf_10t4:.4f} >= 1×T3 conf={conf_1t3:.4f}"

    def test_monotonic_increase_with_n(self):
        """Confidence strictly increases as N grows from 1 to 10."""
        prev = 0.0
        for n in [1, 2, 3, 5, 10]:
            with fresh_sdk() as sdk:
                a_id, _ = build_scenario_a(sdk)
                alpha, beta = log_aggregate_prior("T4", n)
                sdk.set_point_baseline(a_id, alpha, beta)
                conf = get_conf(run_ep(sdk), a_id)
                assert conf > prev, f"N={n}: {conf:.4f} <= prev {prev:.4f}"
                prev = conf


class TestSituation4_AntiSybil:
    """Situation 4: 10 T4 vs 1 T2 — anti-Sybil validation.

    Log-scale aggregation must prevent Sybil attacks where an attacker
    creates many low-tier sources to simulate high credibility.
    """

    def test_quality_beats_quantity(self):
        """1×T2 should substantially exceed 10×T4."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior("T4", 10)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_10t4 = get_conf(run_ep(sdk), a_id)

        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T2")
            conf_1t2 = get_conf(run_ep(sdk), a_id)

        assert conf_1t2 > conf_10t4, \
            f"1×T2 ({conf_1t2:.4f}) not > 10×T4 ({conf_10t4:.4f})"
        assert conf_1t2 - conf_10t4 > 0.10, \
            f"Gap {conf_1t2 - conf_10t4:.4f} too small"

    def test_100_t4_still_below_t2(self):
        """Even 100 T4 sources cannot match 1 T2."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior("T4", 100)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_100t4 = get_conf(run_ep(sdk), a_id)

        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T2")
            conf_1t2 = get_conf(run_ep(sdk), a_id)

        assert conf_100t4 < conf_1t2, \
            f"100×T4 ({conf_100t4:.4f}) not < 1×T2 ({conf_1t2:.4f})"

    def test_1000_t4_approx_t3(self):
        """1000 T4 sources roughly equal 1 T3 — the boundary of anti-Sybil."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior("T4", 1000)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_1000t4 = get_conf(run_ep(sdk), a_id)

        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T3")
            conf_1t3 = get_conf(run_ep(sdk), a_id)

        assert abs(conf_1000t4 - conf_1t3) < 0.05, \
            f"1000×T4 ({conf_1000t4:.4f}) vs 1×T3 ({conf_1t3:.4f}), diff={abs(conf_1000t4 - conf_1t3):.4f}"

    def test_10_t4_below_t3(self):
        """10 T4 not even close to T3."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior("T4", 10)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_10t4 = get_conf(run_ep(sdk), a_id)

        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T3")
            conf_1t3 = get_conf(run_ep(sdk), a_id)

        assert conf_10t4 < conf_1t3


class TestSituation5_CeilingEffect:
    """Situation 5: 2 Gold + add T4 — tiny increase (ceiling/saturation)."""

    def test_ceiling_negligible_increase(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            # 2×T0
            alpha, beta = log_aggregate_prior("T0", 2)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_2t0 = get_conf(run_ep(sdk), a_id)

            # 2×T0 + 1×T4
            alpha2, beta2 = log_aggregate_prior_mixed(
                pos_sources={"T0": 2, "T4": 1}
            )
            sdk.set_point_baseline(a_id, alpha2, beta2)
            conf_with_t4 = get_conf(run_ep(sdk), a_id)

        delta = conf_with_t4 - conf_2t0
        assert delta >= -DELTA, f"Delta={delta:.6f} — regression detected"
        assert delta < 0.005, f"Delta={delta:.6f} — increase too large for ceiling"

    def test_ceiling_mean_near_prediction(self):
        """2×T0 should produce mean ≈ 0.9345."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior("T0", 2)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf = get_conf(run_ep(sdk), a_id)
            # 2×T0: pc=9×1.585=14.27, Beta(15.27,1), mean≈0.9345
            assert abs(conf - 0.9345) < EPSILON


class TestSituation6_NoRegression:
    """Situation 6: 5 Gold + add T4 — must NOT decrease."""

    def test_five_gold_plus_t4_no_regression(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            # 5×T0
            alpha, beta = log_aggregate_prior("T0", 5)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_5t0 = get_conf(run_ep(sdk), a_id)

            # 5×T0 + 1×T4
            alpha2, beta2 = log_aggregate_prior_mixed(
                pos_sources={"T0": 5, "T4": 1}
            )
            sdk.set_point_baseline(a_id, alpha2, beta2)
            conf_with_t4 = get_conf(run_ep(sdk), a_id)

        # S6.1: No regression
        assert conf_with_t4 >= conf_5t0 - DELTA, \
            f"Regression: {conf_with_t4:.6f} < {conf_5t0:.6f}"

        # S6.2: Increase is negligible (ceiling)
        assert (conf_with_t4 - conf_5t0) < 0.005

    def test_five_gold_mean(self):
        """5×T0 should produce mean ≈ 0.9588."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior("T0", 5)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf = get_conf(run_ep(sdk), a_id)
            # 5×T0: pc=9×2.585=23.27, Beta(24.27,1), mean≈0.9588
            assert abs(conf - 0.9588) < EPSILON


class TestSituation7_Idempotency:
    """Situation 7: Add T4 → remove T4 → returns to baseline."""

    def test_add_remove_returns_to_baseline(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            # Baseline: 1×T2
            set_source_evidence(sdk, a_id, "T2")
            conf_baseline = get_conf(run_ep(sdk), a_id)

            # Add T4
            alpha, beta = log_aggregate_prior_mixed(
                pos_sources={"T2": 1, "T4": 1}
            )
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_with_t4 = get_conf(run_ep(sdk), a_id)

            # Remove T4 (back to 1×T2)
            set_source_evidence(sdk, a_id, "T2")
            conf_removed = get_conf(run_ep(sdk), a_id)

        # S7.1: T4 added something
        assert conf_with_t4 > conf_baseline, \
            f"T4 did not increase: {conf_with_t4:.4f} <= {conf_baseline:.4f}"

        # S7.2: Returns to original baseline
        assert abs(conf_removed - conf_baseline) < EPSILON, \
            f"Did not return: removed={conf_removed:.4f}, baseline={conf_baseline:.4f}"

        # S7.3: Relative error < 1%
        rel_err = abs(conf_removed - conf_baseline) / max(conf_baseline, 0.01)
        assert rel_err < 0.01, f"Relative error {rel_err:.4f} >= 0.01"


class TestSituation8_NANDContradiction:
    """Situation 8: 1 Gold + 1 NAND source — contradictory signals."""

    def test_weak_nand_reduces_confidence(self):
        """T4 NAND slightly reduces T0 confidence."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T0")
            conf_t0 = get_conf(run_ep(sdk), a_id)

        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            # T0 positive + T4 NAND
            alpha, beta = log_aggregate_prior_mixed(
                pos_sources={"T0": 1},
                neg_sources={"T4": 1},
            )
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_combined = get_conf(run_ep(sdk), a_id)

        # S8.1: NAND reduces
        assert conf_t0 > conf_combined

        # S8.2: Gold still dominates
        assert conf_combined > 0.85

        # S8.3: Drop is bounded
        drop = conf_t0 - conf_combined
        assert 0.005 < drop < 0.02, \
            f"Drop={drop:.4f} out of [0.005, 0.02]"

    def test_equal_tier_contradiction_returns_to_baseline(self):
        """T0 positive + T0 NAND → Beta(10, 10) → mean ≈ 0.5."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior_mixed(
                pos_sources={"T0": 1},
                neg_sources={"T0": 1},
            )
            sdk.set_point_baseline(a_id, alpha, beta)
            conf = get_conf(run_ep(sdk), a_id)

        assert abs(conf - 0.50) < EPSILON, \
            f"Equal-tier contradiction: conf={conf:.4f}, expected 0.50"

    def test_nand_only_below_baseline(self):
        """NAND alone produces confidence below 0.5."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior_mixed(
                pos_sources={},
                neg_sources={"T4": 1},
            )
            sdk.set_point_baseline(a_id, alpha, beta)
            conf = get_conf(run_ep(sdk), a_id)
            # Beta(1, 1.1) → mean = 1/2.1 ≈ 0.4762
            assert conf < 0.50, f"NAND alone conf={conf:.4f} >= 0.50"


class TestSituation9_Mitigation:
    """Situation 9: T4 source + mitigate edge — drops but stays above no-source."""

    def test_mitigation_reduces_but_above_baseline(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)

            # Baseline
            conf_baseline = get_conf(run_ep(sdk), a_id)

            # Full T4
            set_source_evidence(sdk, a_id, "T4")
            conf_full = get_conf(run_ep(sdk), a_id)

            # 50% mitigation: pc = 0.1 × 0.5 = 0.05
            mitigated_alpha = 1.0 + TIER_PC["T4"] * 0.5  # 1.05
            sdk.set_point_baseline(a_id, mitigated_alpha, 1.0)
            conf_mitigated = get_conf(run_ep(sdk), a_id)

            # Full neutralization
            sdk.set_point_baseline(a_id, 1.0, 1.0)
            conf_neutral = get_conf(run_ep(sdk), a_id)

        # S9.1: Mitigation reduces
        assert conf_full > conf_mitigated, \
            f"Unmitigated {conf_full:.4f} not > mitigated {conf_mitigated:.4f}"

        # S9.2: Stays above baseline
        assert conf_mitigated > conf_baseline - EPSILON

        # S9.3: Full neutralization returns to baseline
        assert abs(conf_neutral - conf_baseline) < EPSILON

    def test_proportional_mitigation(self):
        """Mitigation weakens evidence: 25% strength retains more signal than 75%."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T4")
            conf_full = get_conf(run_ep(sdk), a_id)

            # 25% of original strength (75% mitigated)
            sdk.set_point_baseline(a_id, 1.0 + TIER_PC["T4"] * 0.25, 1.0)
            conf_25pct = get_conf(run_ep(sdk), a_id)

            # 75% of original strength (25% mitigated)
            sdk.set_point_baseline(a_id, 1.0 + TIER_PC["T4"] * 0.75, 1.0)
            conf_75pct = get_conf(run_ep(sdk), a_id)

        # 75% strength is closer to full than 25% strength
        drop_25pct = conf_full - conf_25pct
        drop_75pct = conf_full - conf_75pct
        # 25% retained = bigger drop, 75% retained = smaller drop
        assert drop_25pct > drop_75pct, \
            f"25% retained drop {drop_25pct:.4f} not > 75% retained drop {drop_75pct:.4f}"


class TestSituation10_ChainPropagation:
    """Situation 10: Chain — source on A, check B's response."""

    def test_chain_attenuation_t0(self):
        """T0 on A: B moves significantly in same direction, but attenuated."""
        # Baseline
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            base = run_ep(sdk)
            conf_a_base = get_conf(base, a_id)
            conf_b_base = get_conf(base, b_id)

        # T0 on A
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T0")
            result = run_ep(sdk)
            conf_a_t0 = get_conf(result, a_id)
            conf_b_t0 = get_conf(result, b_id)

        # Direction preservation
        assert conf_a_t0 > conf_a_base
        assert conf_b_t0 > conf_b_base

        # Attenuation
        delta_a = conf_a_t0 - conf_a_base
        delta_b = conf_b_t0 - conf_b_base
        assert delta_b < delta_a, \
            f"B shift {delta_b:.4f} not < A shift {delta_a:.4f}"

        # B moves significantly
        assert conf_b_t0 - conf_b_base > 0.02

    def test_chain_attenuation_t4(self):
        """T4 on A: B barely moves (weak signal attenuates quickly)."""
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            base = run_ep(sdk)
            conf_b_base = get_conf(base, b_id)

        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T4")
            result = run_ep(sdk)
            conf_b_t4 = get_conf(result, b_id)

        assert abs(conf_b_t4 - conf_b_base) < 0.05, \
            f"T4 propagation too strong: Δ={abs(conf_b_t4 - conf_b_base):.4f}"

    def test_baseline_near_uniform(self):
        """No sources → both A and B near 0.5."""
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            result = run_ep(sdk)
            conf_a = get_conf(result, a_id)
            conf_b = get_conf(result, b_id)

        assert abs(conf_a - 0.50) < EPSILON
        assert abs(conf_b - 0.50) < EPSILON


# ═══════════════════════════════════════════════════════════════════
# Scenario B — Loopy Cluster, Single Source on A
# ═══════════════════════════════════════════════════════════════════

class TestScenarioB_LoopySingleSource:
    """Three-node loopy cluster (A→B→C→A). T0 source on A only."""

    def test_loop_converges(self):
        """EP must converge on loopy graphs (not diverge)."""
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            result = run_ep(sdk)
            assert result["converged"] == True, \
                f"EP did not converge after {result['iterations']} iterations"

    def test_source_node_highest(self):
        """A (directly sourced) should have highest confidence."""
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            result = run_ep(sdk)
            conf_a = get_conf(result, a_id)
            conf_b = get_conf(result, b_id)
            conf_c = get_conf(result, c_id)

        assert conf_a > conf_b, f"A ({conf_a:.4f}) not > B ({conf_b:.4f})"
        assert conf_a > conf_c, f"A ({conf_a:.4f}) not > C ({conf_c:.4f})"

    def test_all_nodes_above_baseline(self):
        """All nodes receive some positive signal from the loop."""
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            result = run_ep(sdk)
            conf_a = get_conf(result, a_id)
            conf_b = get_conf(result, b_id)
            conf_c = get_conf(result, c_id)

        assert conf_a > 0.80
        assert conf_b > 0.54, f"B too low: {conf_b:.4f}"
        assert conf_c > 0.51, f"C too low: {conf_c:.4f}"

    def test_loop_no_explosion(self):
        """Feedback loop should NOT cause unbounded confidence inflation."""
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            result = run_ep(sdk)
            conf_a = get_conf(result, a_id)
            conf_c = get_conf(result, c_id)

        # C is 2 hops from source — should not exceed A
        assert conf_c < conf_a - 0.05, \
            f"C ({conf_c:.4f}) too close to A ({conf_a:.4f})"


# ═══════════════════════════════════════════════════════════════════
# Scenario C — Loopy Cluster, Dual Source on A and B
# ═══════════════════════════════════════════════════════════════════

class TestScenarioC_LoopyDualSource:
    """Three-node loopy cluster (A→B→C→A). T0 on A, T1 on B."""

    def test_dual_source_converges(self):
        """EP converges with two sources in the loop."""
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            set_source_evidence(sdk, b_id, "T1")
            result = run_ep(sdk)
            assert result["converged"] == True

    def test_both_sources_at_or_above_tier(self):
        """A ≥ 0.90 (T0), B ≥ 0.83 (T1)."""
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            set_source_evidence(sdk, b_id, "T1")
            result = run_ep(sdk)
            conf_a = get_conf(result, a_id)
            conf_b = get_conf(result, b_id)

        assert conf_a >= 0.90 - EPSILON, f"A too low: {conf_a:.4f}"
        assert conf_b >= 0.83 - EPSILON, f"B too low: {conf_b:.4f}"

    def test_c_gets_more_signal_than_single_source(self):
        """Dual sources → C gets more signal than single-source Scenario B."""
        # Single source (Scenario B)
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            result = run_ep(sdk)
            conf_c_single = get_conf(result, c_id)

        # Dual source (Scenario C)
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            set_source_evidence(sdk, b_id, "T1")
            result = run_ep(sdk)
            conf_c_dual = get_conf(result, c_id)

        assert conf_c_dual > conf_c_single + 0.02, \
            f"Dual C ({conf_c_dual:.4f}) not > single C ({conf_c_single:.4f}) + 0.02"

    def test_no_contradiction(self):
        """Both A and B are IMPL (supporting) so both should be high."""
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            set_source_evidence(sdk, b_id, "T1")
            result = run_ep(sdk)
            conf_a = get_conf(result, a_id)
            conf_b = get_conf(result, b_id)

        assert conf_a > 0.80
        assert conf_b > 0.80


# ═══════════════════════════════════════════════════════════════════
# Edge Cases & System Properties
# ═══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases, convergence, and system invariants."""

    def test_zero_sources_baseline(self):
        """No sources → uniform prior → confidence ≈ 0.5."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            result = run_ep(sdk)
            conf = get_conf(result, a_id)
            assert abs(conf - 0.50) < EPSILON

    def test_convergence_under_50_iterations(self):
        """EP always converges within max_iter=50 for any source config."""
        configs = [
            {"T4": 1}, {"T0": 1}, {"T4": 100}, {"T0": 10},
            {"T0": 1, "T4": 1},
        ]
        for pos_sources in configs:
            with fresh_sdk() as sdk:
                a_id, _ = build_scenario_a(sdk)
                alpha, beta = log_aggregate_prior_mixed(pos_sources=pos_sources)
                sdk.set_point_baseline(a_id, alpha, beta)
                result = run_ep(sdk)
                assert result["converged"] == True, \
                    f"Not converged for sources={pos_sources}"
                assert result["iterations"] <= 50

    def test_confidence_bounds(self):
        """All confidences remain in [0, 1] for all configurations."""
        test_cases = [
            {"T4": 1}, {"T0": 1}, {"T4": 100}, {"T0": 10},
            {"T0": 1, "T4": 1},  # mixed positive
        ]
        for pos_sources in test_cases:
            with fresh_sdk() as sdk:
                a_id, _ = build_scenario_a(sdk)
                alpha, beta = log_aggregate_prior_mixed(pos_sources=pos_sources)
                sdk.set_point_baseline(a_id, alpha, beta)
                result = run_ep(sdk)
                conf = get_conf(result, a_id)
                assert 0.0 <= conf <= 1.0, \
                    f"conf={conf:.4f} out of [0,1] for sources={pos_sources}"

    def test_determinism(self):
        """Same setup → same confidence (EP is deterministic)."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T0")
            conf1 = get_conf(run_ep(sdk), a_id)
            conf2 = get_conf(run_ep(sdk), a_id)
            assert abs(conf1 - conf2) < DELTA, \
                f"Non-deterministic: {conf1:.6f} != {conf2:.6f}"
