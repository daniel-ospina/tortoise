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
