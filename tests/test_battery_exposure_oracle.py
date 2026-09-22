"""#2284 Task 8 Step 2 — oracle sensitivity: super-A4 through R1/R3/R5.

The instrument (real probe scorers) must register a planted effect at MAX
strength AND register absence as the 0.0 floor — a vacuous instrument
(all-1.0 or blind to null) retires none of the executor risks. Traces use
the schema-v1.1 semantic keys each probe's CONSUMED_FIELDS declares.
"""
from __future__ import annotations

from battery.exposure.oracle import (
    null_traces,
    oracle_ok,
    run_oracle_sensitivity,
    super_a4_contested_traces,
)
from battery.probes.r1_contradiction import R1ContradictionProbe
from battery.probes.r3_calibration import R3CalibrationProbe


def test_super_a4_max_strength_registers():
    """Plant the effect at max strength → every probe hits its ceiling."""
    report = run_oracle_sensitivity()
    assert oracle_ok(report), report
    m = report["max_strength"]
    assert m["r1_surfaced_rate"] == 1.0
    assert m["r1_flip_flop_rate"] == 0.0
    assert m["r1_false_positive_rate"] == 0.0
    assert m["r3_brier"] == 0.0
    assert m["r3_honest_undecided_rate"] == 1.0
    assert m["r5_correct_direction_rate"] == 1.0
    assert m["r5_over_reaction_rate"] == 0.0
    assert report["null_floor"]["r1_surfaced_rate"] == 0.0


def test_null_floor_not_vacuous():
    """Absence registers as 0.0 — the instrument is NOT all-1.0 (a 1.0-only
    instrument is blind and retires nothing)."""
    r1 = R1ContradictionProbe()
    floor = r1.flip_flop_rate(null_traces())
    assert floor == 0.0
    assert r1.false_positive_rate(null_traces()) == 0.0
    # and the surfaced rule on the null floor reads 0.0 (never surfaced)
    rates = [r1.score(t, gold=None, threshold=0.92).value
             for t in null_traces()]
    assert all(v == 0.0 for v in rates)


def test_honest_undecided_contested_ceiling():
    """On genuinely contested EP, the super-A4 says 'undecided' — the
    honest-UNDEC branch (E2E-1.3) measures at its ceiling, not a vacuous
    pass."""
    r3 = R3CalibrationProbe()
    assert r3.honest_undecided_rate(super_a4_contested_traces()) == 1.0
    assert r3.confident_wrong_rate(super_a4_contested_traces()) == 0.0
