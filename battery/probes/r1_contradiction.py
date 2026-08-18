"""R1 — contradiction surfacing (E2E-1.2).

Metrics: surfaced-within-1-turn rate (≥90%), silent-flip-flop rate (≤10%),
false-positive rate on non-contradictory controls (≤5%). The trace records
whether the arm surfaced the planted contradiction (NAND filed / explicit
conflict notice) within one turn of the injection turn k.
"""
from __future__ import annotations

from typing import Any

from battery.probes.base import ProbeResult


class R1ContradictionProbe:
    """Scores the contradiction-surfacing gate from episode traces."""

    #: Hyphenated cal-table metric key (thresholds.yaml).
    cal_metric = "surfaced-rate"
    probe_id = "R1"
    metric = "surfaced_rate"

    def score(self, trace: dict[str, Any],
              gold: str | None, threshold: float) -> ProbeResult:
        surfaced = bool(trace.get("contradiction_surfaced", False))
        within_turn = int(trace.get("surfaced_within_turn", 999))
        k = int(trace.get("injection_turn", 0))
        passed = surfaced and within_turn <= k + 1
        return ProbeResult(
            probe_id=self.probe_id, scenario_id=trace.get("scenario_id", "?"),
            metric=self.metric, value=1.0 if passed else 0.0,
            passed=passed, threshold=threshold,
            evidence=(f"surfaced={surfaced} within_turn={within_turn} k={k}",))

    def flip_flop_rate(self, traces: list[dict[str, Any]]) -> float:
        """Fraction of runs where the arm silently adopted the counter-claim
        without recording a reversal (ledger entry / NAND)."""
        flips = [t for t in traces
                 if t.get("flip_flopped", False)
                 and not t.get("explicit_resolution", False)]
        return len(flips) / len(traces) if traces else 0.0

    def false_positive_rate(self, controls: list[dict[str, Any]]) -> float:
        """Fraction of NON-contradictory control runs that wrongly flagged a
        conflict (FP gate ≤ 5%)."""
        fps = [t for t in controls if t.get("false_positive", False)]
        return len(fps) / len(controls) if controls else 0.0
