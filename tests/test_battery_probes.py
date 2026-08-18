"""Issue #1409 — Tier-1 probes: AC gates (R1 90/10/5, R2 ≥1.5× + mechanism,
R3 Brier ≤−0.05 + UNDEC ≥80% + confident-wrong ≤10%, R4 ≥70% + completeness,
R5 ≥90% + over-reaction ≤10%)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.probes.r1_contradiction import R1ContradictionProbe
from battery.probes.r2_coverage import R2CoverageProbe
from battery.probes.r3_calibration import R3CalibrationProbe, brier
from battery.probes.r4_defeat import R4DefeatProbe
from battery.probes.r5_update import R5UpdateProbe


def _trace(**kw):
    base = {"scenario_id": "s1", "injection_turn": 5}
    base.update(kw)
    return base


class TestR1:
    def test_surfaced_within_turn(self):
        p = R1ContradictionProbe()
        r = p.score(_trace(contradiction_surfaced=True, surfaced_within_turn=6,
                           injection_turn=5), None, 0.9)
        assert r.passed
        r2 = p.score(_trace(contradiction_surfaced=False), None, 0.9)
        assert not r2.passed

    def test_flip_flop_gate(self):
        p = R1ContradictionProbe()
        # 1 silent flip in 10 runs = 10% (the gate boundary).
        traces = [{"flip_flopped": True, "explicit_resolution": False}] + \
                 [{"flip_flopped": False}] * 9
        assert p.flip_flop_rate(traces) <= 0.10
        assert p.flip_flop_rate(traces) == 0.10

    def test_false_positive_gate(self):
        p = R1ContradictionProbe()
        controls = [{"false_positive": True}] + [{"false_positive": False}] * 19
        assert p.false_positive_rate(controls) <= 0.05


class TestR2:
    def test_coverage_gate(self):
        p = R2CoverageProbe()
        r = p.score(_trace(coverage_subscore=0.75), None, 0.5)
        assert r.passed
        assert p.delta_vs_control(0.75, 0.4) >= 1.5

    def test_mechanism_gate_diagnostic(self):
        p = R2CoverageProbe()
        traces = [{"decide_cycles": 3}] * 8 + [{"decide_cycles": 1}] * 2
        assert p.mechanism_gate(traces) >= 0.80


class TestR3:
    def test_brier_perfect(self):
        assert brier([1.0, 0.0], [1, 0]) == 0.0

    def test_brier_miscalibrated(self):
        assert brier([1.0, 1.0], [1, 0]) == 0.5

    def test_honest_undecided(self):
        p = R3CalibrationProbe()
        traces = [{"ep_outcome": "undec", "stated_undecided": True}] * 8 + \
                 [{"ep_outcome": "undec", "stated_undecided": False}] * 2
        assert p.honest_undecided_rate(traces) >= 0.80
        assert p.honest_undecided_rate([{"ep_outcome": "converged"}]) == 1.0

    def test_confident_wrong(self):
        p = R3CalibrationProbe()
        traces = [{"stated_confidence": 0.9, "outcome_correct": False}] * 1 + \
                 [{"stated_confidence": 0.9, "outcome_correct": True}] * 9
        assert p.confident_wrong_rate(traces) <= 0.10


class TestR4:
    def test_defeat_precision(self):
        p = R4DefeatProbe()
        r = p.score(_trace(stated_defeat_conditions=["cond-a", "cond-x"],
                           real_defeat_conditions=["cond-a", "cond-b"]),
                    None, 0.7)
        assert r.value == 0.5
        assert not r.passed  # 0.5 < 0.7
        r2 = p.score(_trace(stated_defeat_conditions=["cond-a"],
                            real_defeat_conditions=["cond-a"]), None, 0.7)
        assert r2.passed

    def test_completeness(self):
        p = R4DefeatProbe()
        assert p.completeness([{"stated_defeat_conditions": ["a"],
                                "real_defeat_conditions": ["a"]}])
        assert not p.completeness([{"stated_defeat_conditions": ["x"],
                                    "real_defeat_conditions": ["a"]}])
        assert not p.completeness([{"stated_defeat_conditions": [],
                                    "real_defeat_conditions": ["a"]}])


class TestR5:
    def test_correct_direction(self):
        p = R5UpdateProbe()
        assert p.score(_trace(update_correct_direction=True), None, 0.9).passed

    def test_over_reaction(self):
        p = R5UpdateProbe()
        traces = [{"over_reacted": True}] + [{"over_reacted": False}] * 9
        assert p.over_reaction_rate(traces) <= 0.10


def test_load_probe_thresholds_cal_table():
    """The [cal]-locked lookup resolves the hyphenated cal-table key and
    hard-fails on a missing row (no silent default)."""
    import sys
    from pathlib import Path
    from battery.config.thresholds import load_thresholds
    from battery.probes.base import load_probe_thresholds
    cfg = load_thresholds(
        Path(__file__).resolve().parent.parent / "battery" / "config"
        / "thresholds.yaml")
    assert load_probe_thresholds(cfg, "surfaced-rate", "a4", 0.5) == 0.90
    assert load_probe_thresholds(cfg, "brier", "a4", 0.5) == 0.25
    import pytest as _p
    with _p.raises(KeyError):
        load_probe_thresholds(cfg, "no-such-metric", "a4", 0.5)
