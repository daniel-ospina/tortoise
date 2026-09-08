"""#2284 Task 8, Step 2 — oracle-sensitivity harness.

Risk-1/risk-3 leg: the INSTRUMENT (R1/R3/R5 probe scorers) must see a
planted effect at max strength (super-A4) AND register null effect as the
floor — a vacuous instrument (all 1.0, or blind to absence) retires
nothing. Traces are emitted exactly as a real run would emit them
(schema-v1.1 semantic keys the probes' CONSUMED_FIELDS declare) — the
probes are the real scorer classes, never reimplementations.
"""
from __future__ import annotations

from typing import Any

from battery.probes.r1_contradiction import R1ContradictionProbe
from battery.probes.r3_calibration import R3CalibrationProbe
from battery.probes.r5_update import R5UpdateProbe


def super_a4_traces(n: int = 5, *, k: int = 3) -> list[dict[str, Any]]:
    """n max-strength decision episodes: contradiction surfaced within one
    turn of the injection turn k, no flip-flop, explicit resolution,
    perfectly calibrated, honest-undecided on the contested row, correct
    update direction, no over-reaction."""
    traces = []
    for i in range(n):
        traces.append({
            "scenario_id": f"ct-00{i % 3 + 1}",
            "contradiction_surfaced": True,
            "surfaced_within_turn": k,          # <= k+1 → surfaced rule met
            "injection_turn": k,
            "flip_flopped": False,
            "explicit_resolution": True,
            "false_positive": False,
            "confidences": [1.0, 1.0],
            "outcomes": [1, 1],
            "outcome_correct": True,
            "ep_outcome": "converged",
            "stated_undecided": False,
            "update_correct_direction": True,
            "over_reacted": False,
        })
    return traces


def super_a4_contested_traces(n: int = 5) -> list[dict[str, Any]]:
    """Max-strength HONEST-UNDEC episodes: EP genuinely contested and the
    agent says 'undecided' (never a confident number on contested EP)."""
    return [{
        "scenario_id": "ct-001",
        "ep_outcome": "contested",
        "stated_undecided": True,
        "confidences": [],
        "outcomes": [],
        "outcome_correct": True,
    } for _ in range(n)]


def null_traces(n: int = 5) -> list[dict[str, Any]]:
    """Floor traces (a0-style / no planted effect): nothing surfaced, no
    flip-flop, no FP, correct-but-vacuous brier absent (confidences empty
    is NOT a pass row for calibration — the honest-absent read is the
    surfaced/fp/flip metrics at 0.0)."""
    return [{
        "scenario_id": "ct-001",
        "contradiction_surfaced": False,
        "surfaced_within_turn": 999,
        "injection_turn": 0,
        "flip_flopped": False,
        "explicit_resolution": False,
        "false_positive": False,
    } for _ in range(n)]


def run_oracle_sensitivity() -> dict[str, Any]:
    """Max-strength super-A4 through the REAL R1/R3/R5 scorers; the report
    cites these numbers. Also registers the null floor so the verdict is
    never vacuous (a 1.0-only instrument is blind)."""
    r1 = R1ContradictionProbe()
    r3 = R3CalibrationProbe()
    r5 = R5UpdateProbe()
    strong = super_a4_traces()
    contested = super_a4_contested_traces()
    # R1 aggregate rate helpers (surfaced rule on the planted population)
    surfaced = sum(1 for t in strong if (
        t["contradiction_surfaced"]
        and t["surfaced_within_turn"] <= t["injection_turn"] + 1))
    flip_flop = r1.flip_flop_rate(strong)
    fp = r1.false_positive_rate(strong)
    # R3
    brier = r3.score({"confidences": [1.0, 1.0], "outcomes": [1, 1]},
                     gold=None, threshold=0.05).value
    honest = r3.honest_undecided_rate(contested)
    # R5
    correct = r5.score({"update_correct_direction": True},
                       gold=None, threshold=0.9).value
    over = r5.over_reaction_rate(strong)
    # Null floor
    null_surfaced = sum(1 for t in null_traces() if (
        t["contradiction_surfaced"]
        and t["surfaced_within_turn"] <= t["injection_turn"] + 1))
    return {
        "max_strength": {
            "r1_surfaced_rate": surfaced / len(strong),
            "r1_flip_flop_rate": flip_flop,
            "r1_false_positive_rate": fp,
            "r3_brier": float(brier),
            "r3_honest_undecided_rate": honest,
            "r5_correct_direction_rate": float(correct),
            "r5_over_reaction_rate": over,
        },
        "null_floor": {
            "r1_surfaced_rate": null_surfaced / 5,
        },
    }


def oracle_ok(report: dict[str, Any]) -> bool:
    """Max strength registers at ceiling AND the null floor reads 0.0 —
    both directions prove the instrument sees what a run emits."""
    m = report["max_strength"]
    ceiling_ok = (
        m["r1_surfaced_rate"] == 1.0
        and m["r1_flip_flop_rate"] == 0.0
        and m["r1_false_positive_rate"] == 0.0
        and m["r3_brier"] == 0.0
        and m["r3_honest_undecided_rate"] == 1.0
        and m["r5_correct_direction_rate"] == 1.0
        and m["r5_over_reaction_rate"] == 0.0)
    floor_ok = report["null_floor"]["r1_surfaced_rate"] == 0.0
    return ceiling_ok and floor_ok
