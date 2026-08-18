"""Issue #1412 — differential: D1 full 14-family matrix (no exclusions),
D2 spread ≥2× (A4 converges, memory arms flat), D3 feedback loop (fix-rate
margin + monotone + evidence filed), D4 adversarial (poison ≥80%, Sybil
ordering, anchoring abandoned)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.differential.d1_sweep import METRIC_FAMILIES, build_matrix
from battery.differential.d2_spread import compute_spread
from battery.differential.d3_feedback import evaluate_loop, margin_vs_control
from battery.differential.d4_adversarial import evaluate_adversarial


class TestD1:
    def test_full_matrix_required(self):
        partial = {f: {"a4": 1.0} for f in METRIC_FAMILIES[:-1]}
        with pytest.raises(ValueError):
            build_matrix(partial)  # no exclusions — full profile

    def test_matrix_builds(self):
        full = {f: {"a4": 1.0, "a2": 0.5} for f in METRIC_FAMILIES}
        m = build_matrix(full, {"f1_by_arm": {"a4": 0.9}})
        assert len(m.families()) == 14
        assert "a4" in m.arms() and "a2" in m.arms()


class TestD2:
    def test_spread_2x(self):
        a4 = (100, 80, 50)      # converging to 50 (-50%)
        arms = {"a2": (100, 100, 100), "a2b": (100, 100, 100)}  # flat
        r = compute_spread(a4, arms, threshold=2.0)
        assert r.passed  # 100/50 = 2.0× spread
        assert r.spread >= 2.0

    def test_no_convergence_fails(self):
        a4 = (100, 100, 100)  # A4 also flat → spread undefined → fail
        arms = {"a2": (100, 100, 100)}
        assert not compute_spread(a4, arms).passed


class TestD3:
    def test_fix_rate_and_margin(self):
        iters = [{"fixed": True, "evidence_filed": True},
                 {"fixed": True, "evidence_filed": True},
                 {"fixed": False, "evidence_filed": True},
                 {"fixed": True, "evidence_filed": True}]
        r = evaluate_loop(iters)
        assert r.fix_rate == pytest.approx(0.75)
        assert r.evidence_filed
        assert margin_vs_control(r.fix_rate, 0.4)  # 0.75 >= 0.4 + 0.10

    def test_regression_breaks_monotone(self):
        iters = [{"fixed": True}, {"fixed": False}]  # regress
        assert not evaluate_loop(iters).improvement_monotone

    def test_monotone_progression(self):
        iters = [{"fixed": False}, {"fixed": True}, {"fixed": True}]
        assert evaluate_loop(iters).improvement_monotone


class TestD4:
    def test_poison_rejection(self):
        poisoned = [{"rejected": True, "high_confidence": True}] * 8 + \
                   [{"rejected": False, "high_confidence": True}] * 2
        r = evaluate_adversarial(
            poisoned, {"t0_conf": 0.9, "t4_10x_conf": 0.5},
            [{"abandoned": True}], {"a2": 0.2})
        assert r.poisoned_rejection == pytest.approx(0.80)
        assert r.sybil_ordering_ok
        assert r.anchoring_abandoned
        assert r.passed

    def test_sybil_inversion_fails(self):
        r = evaluate_adversarial(
            [{"rejected": True, "high_confidence": True}],
            {"t0_conf": 0.4, "t4_10x_conf": 0.9},  # 10×T4 > T0 → bad
            [{"abandoned": True}])
        assert not r.sybil_ordering_ok
        assert not r.passed
