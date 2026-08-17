"""Paired bootstrap CI + gate semantics tests (#1144)."""
from __future__ import annotations

import random

import pytest

from tests.eval.retrieval.bootstrap import (
    GATE_BLOCK_LOWER,
    GATE_WARN_LOWER,
    ConfidenceInterval,
    one_sample_ci,
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
