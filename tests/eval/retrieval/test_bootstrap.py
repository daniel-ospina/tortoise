"""Paired bootstrap CI + gate semantics tests (#1144)."""
from __future__ import annotations

import random

import pytest

from tests.eval.retrieval.bootstrap import (
    GATE_BLOCK_LOWER,
    GATE_WARN_LOWER,
    ConfidenceInterval,
    bh_fdr,
    one_sample_ci,
    one_sided_bootstrap_p,
    paired_bootstrap_ci,
    paired_deltas,
    quality_gate,
)


# ── Paired bootstrap CI ─────────────────────────────────────────────────────

def test_ci_constant_deltas_collapse():
    ci = paired_bootstrap_ci([-1.0] * 100)
    assert ci.lower == pytest.approx(-1.0, abs=0.01)
    assert ci.upper == pytest.approx(-1.0, abs=0.01)
    assert ci.n == 100


def test_ci_zero_deltas():
    ci = paired_bootstrap_ci([0.0] * 50)
    assert ci.lower == pytest.approx(0.0, abs=1e-6)
    assert ci.upper == pytest.approx(0.0, abs=1e-6)


def test_ci_contains_sample_mean():
    rng = random.Random(1144)
    deltas = [rng.gauss(0.3, 0.5) for _ in range(60)]
    ci = paired_bootstrap_ci(deltas, n_resamples=2000, rng=rng)
    mean = sum(deltas) / len(deltas)
    assert ci.lower <= mean <= ci.upper
    # A nonzero delta with spread should keep 0 out when mean >> sd/√n.
    assert ci.lower < ci.upper


def test_ci_deterministic_given_seed():
    rng1, rng2 = random.Random(5), random.Random(5)
    deltas = [1.0, -2.0, 0.5, 3.0, -1.5, 2.0] * 10
    assert paired_bootstrap_ci(deltas, rng=rng1) == paired_bootstrap_ci(deltas, rng=rng2)


def test_ci_is_exact_percentile_of_resampled_means():
    """Percentile-method lock: the CI bounds must equal the alpha/2 and
    1-alpha/2 quantiles of the resampled-mean distribution, at the exact
    indices int(alpha/2 * (n-1)) and int((1-alpha/2) * (n-1)). Replicate
    the resampling in-test with the same rng and compare bound-for-bound."""
    rng = random.Random(99)
    deltas = [rng.gauss(0.1, 0.7) for _ in range(50)]
    n_resamples, alpha = 500, 0.10
    ci = paired_bootstrap_ci(deltas, n_resamples=n_resamples, alpha=alpha,
                             rng=random.Random(99))
    # Recompute the resampled-mean distribution with the identical seed.
    r2 = random.Random(99)
    n = len(deltas)
    means = sorted(
        sum(deltas[r2.randrange(n)] for _ in range(n)) / n
        for _ in range(n_resamples)
    )
    lo_idx = int((alpha / 2) * (n_resamples - 1))
    hi_idx = int((1 - alpha / 2) * (n_resamples - 1))
    assert ci.lower == pytest.approx(means[lo_idx])
    assert ci.upper == pytest.approx(means[hi_idx])
    assert lo_idx < hi_idx


def test_resampling_is_paired_on_query_identity():
    """Paired-bootstrap lock: the CI is computed over per-query deltas
    (new − baseline per identical query id), so resampling the delta
    indices is equivalent to resampling QUERY indices and recomputing the
    delta per resampled query — never resampling the two arms
    independently. Unpaired queries are dropped BEFORE resampling."""
    qids = [f"q{i:02d}" for i in range(40)]  # zero-padded → lexicographic == numeric
    rng = random.Random(7)
    new = {qid: rng.random() for qid in qids}
    baseline = {qid: rng.random() for qid in qids}
    deltas, dropped = paired_deltas(new, baseline)
    assert dropped == 0
    # Direct resampling over query ids (the paired construction):
    r1 = random.Random(1234)
    ci = paired_bootstrap_ci(deltas, n_resamples=800, rng=r1)
    # Recompute by resampling query ids and rebuilding (new-baseline)*100.
    r2 = random.Random(1234)
    n = len(qids)
    means = []
    for _ in range(800):
        s = 0.0
        for _ in range(n):
            qid = qids[r2.randrange(n)]
            s += (new[qid] - baseline[qid]) * 100.0
        means.append(s / n)
    means.sort()
    assert ci.lower == pytest.approx(means[int(0.05 * 799)])
    assert ci.upper == pytest.approx(means[int(0.95 * 799)])


def test_pairing_is_by_query_id_not_position():
    """Pairing lock: identical query sets in DIFFERENT insertion orders
    produce the identical paired delta multiset (pairing is by query id,
    never by list position)."""
    qids = [f"q{i}" for i in range(12)]
    new = {qid: i * 0.01 for i, qid in enumerate(qids)}
    baseline = {qid: 0.05 for qid in qids}
    d1, drop1 = paired_deltas(new, baseline)
    # Shuffle the insertion order of BOTH arms identically — the pairing
    # must still key on the query id.
    shuffled = qids[3:] + qids[:3]
    new2 = {qid: new[qid] for qid in shuffled}
    baseline2 = {qid: baseline[qid] for qid in shuffled}
    d2, drop2 = paired_deltas(new2, baseline2)
    assert sorted(d1) == sorted(d2)
    assert drop1 == drop2 == 0
    rng1, rng2 = random.Random(3), random.Random(3)
    assert (paired_bootstrap_ci(d1, rng=rng1)
            == paired_bootstrap_ci(d2, rng=rng2))


def test_ci_empty():
    ci = paired_bootstrap_ci([])
    assert ci == ConfidenceInterval(0.0, 0.0, 0.0, 0)


def test_one_sample_ci():
    ci = one_sample_ci([0.5] * 40)
    assert ci.lower == pytest.approx(0.5, abs=0.01)
    assert ci.n == 40


def test_paired_deltas_on_query_ids():
    new = {f"q{i}": 0.5 + 0.01 * i for i in range(10)}
    new["q-new"] = 0.9
    baseline = {f"q{i}": 0.6 for i in range(10)}
    baseline["q-old"] = 0.3
    deltas, dropped = paired_deltas(new, baseline)
    assert len(deltas) == 10           # 10 paired queries
    assert dropped == 2                # q-new + q-old unpaired
    # deltas are in POINTS (x100): q0 → (0.5-0.6)*100 = -10
    assert deltas[0] == pytest.approx(-10.0)


# ── #1349: one-sided bootstrap p + BH-FDR (net-new, alongside #1144) ──────

def test_one_sided_bootstrap_p_exact_on_tiny_synthetic():
    """Exact p on a tiny synthetic delta set with a deterministic rng: p must
    equal the fraction of resampled means ≤ 0, replicated in-test with the
    same seed (the #1144 exact-percentile lock, one-sided)."""
    deltas = [1.0, -1.0]  # resampled mean ∈ {1, 0, 0, −1}; P(≤0) = 3/4
    rng = random.Random(1349)
    n_resamples = 400
    p = one_sided_bootstrap_p(deltas, n_resamples=n_resamples, rng=rng)
    r2 = random.Random(1349)
    n = len(deltas)
    le = sum(
        1 for _ in range(n_resamples)
        if sum(deltas[r2.randrange(n)] for _ in range(n)) / n <= 0.0
    )
    assert p == pytest.approx(le / n_resamples)
    assert 0.0 < p < 1.0
    # The theoretical probability for this tiny set is 0.75.
    assert p == pytest.approx(0.75, abs=0.06)


def test_one_sided_bootstrap_p_sign_conventions():
    """All-positive deltas → p=0 (every resampled mean positive); all-negative
    → p=1; all-zero → p=1 (no evidence of a positive delta)."""
    assert one_sided_bootstrap_p([0.5] * 30, rng=random.Random(1)) == 0.0
    assert one_sided_bootstrap_p([-0.5] * 30, rng=random.Random(1)) == 1.0
    assert one_sided_bootstrap_p([0.0] * 30, rng=random.Random(1)) == 1.0


def test_one_sided_bootstrap_p_deterministic_given_seed():
    deltas = [0.2, -0.1, 0.4, -0.3, 0.1] * 10
    r1, r2 = random.Random(7), random.Random(7)
    assert (one_sided_bootstrap_p(deltas, rng=r1)
            == one_sided_bootstrap_p(deltas, rng=r2))


def test_one_sided_bootstrap_p_empty_is_no_evidence():
    assert one_sided_bootstrap_p([]) == 1.0


def test_bh_fdr_known_rejection_pattern():
    """Hand-computed BH step-up at q=0.10 on pvals [0.001, 0.02, 0.04, 0.2],
    m=4: sorted p_(i) vs q·i/m = {0.025, 0.05, 0.075, 0.10} → p_(1..3) all
    ≤ their threshold, p_(4)=0.2 > 0.10 → k=3, reject the 3 smallest."""
    pvals = [0.001, 0.02, 0.04, 0.2]
    rejected = bh_fdr(pvals, q=0.10)
    assert rejected == [True, True, True, False]


def test_bh_fdr_lower_q_rejects_less():
    """At q=0.05 the thresholds are {0.0125, 0.025, 0.0375, 0.05} → only
    p_(1)=0.001 and p_(2)=0.02 clear → k=2."""
    pvals = [0.001, 0.02, 0.04, 0.2]
    assert bh_fdr(pvals, q=0.05) == [True, True, False, False]


def test_bh_fdr_monotonicity_in_pvals():
    """BH is monotone in the p-values: shrinking any p cannot un-reject."""
    base = [0.001, 0.02, 0.04, 0.2]
    smaller = [0.0005, 0.015, 0.04, 0.2]
    r_base = bh_fdr(base, q=0.10)
    r_smaller = bh_fdr(smaller, q=0.10)
    for i in range(len(base)):
        assert (r_base[i] and not r_smaller[i]) is False  # never un-rejected


def test_bh_fdr_m6_top_rank_threshold():
    """The #1349 locked case: m=6, q=0.10 → the smallest p must be ≤
    q/m = 0.10/6 = 0.016666… (presented as 0.0167, z≈2.128) to reject
    anything. A p exactly at q/m with the rest large is rejected; a hair
    above is not."""
    boundary = 0.10 / 6
    assert bh_fdr([boundary, 0.9, 0.9, 0.9, 0.9, 0.9], q=0.10)[0] is True
    assert bh_fdr([boundary + 1e-9, 0.9, 0.9, 0.9, 0.9, 0.9], q=0.10)[0] is False


def test_bh_fdr_edge_cases():
    assert bh_fdr([]) == []
    assert bh_fdr([0.001] * 4, q=0.10) == [True] * 4
    assert bh_fdr([0.5] * 4, q=0.10) == [False] * 4


# ── Gate bands (pre-registered semantics) ───────────────────────────────────

def test_gate_ship_when_ci_does_not_exclude_minus_2():
    g = quality_gate([-1.0] * 100, [0.0] * 100, rng=random.Random(1))
    assert g.verdict == "SHIP"
    assert "does not exclude" in g.reason


def test_gate_ship_at_exact_boundary():
    g = quality_gate([-2.0] * 100, [0.0] * 100, rng=random.Random(1))
    assert g.verdict == "SHIP"


def test_gate_warn_band():
    g = quality_gate([-3.0] * 100, [0.0] * 100, rng=random.Random(1))
    assert g.verdict == "WARN"
    assert g.ndcg_ci.lower >= GATE_BLOCK_LOWER
    assert g.ndcg_ci.lower < GATE_WARN_LOWER


def test_gate_block_below_minus_4():
    g = quality_gate([-5.0] * 100, [0.0] * 100, rng=random.Random(1))
    assert g.verdict == "BLOCK"


def test_gate_block_on_significant_p5_drop_even_with_ok_ndcg():
    g = quality_gate([0.0] * 100, [-2.0] * 100, rng=random.Random(1))
    assert g.verdict == "BLOCK"
    assert "P@5" in g.reason
    assert g.p5_ci is not None and g.p5_ci.upper < 0.0


def test_gate_no_p5_drop_when_ci_spans_zero():
    rng = random.Random(2)
    g = quality_gate([0.0] * 100, [0.0] * 100, rng=rng)
    assert g.verdict == "SHIP"  # P@5 CI includes 0 → no drop
