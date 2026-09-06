"""R1 — contradiction surfacing (E2E-1.2).

Metrics: surfaced-within-1-turn rate (≥90%), silent-flip-flop rate (≤10%),
false-positive rate on non-contradictory controls (≤5%). The trace records
whether the arm surfaced the planted contradiction (NAND filed / explicit
conflict notice) within one turn of the injection turn k.
"""
from __future__ import annotations

from typing import Any

from battery.probes.base import ProbeResult

#: Schema-v1.1 emitter-registry contract (issue #2284): the trace semantic
#: keys this probe reads. Declarative only — behavior unchanged until the
#: probe re-points reads onto the registry-emitted log (Task 9).
CONSUMED_FIELDS: tuple[str, ...] = (
    "contradiction_surfaced", "flip_flopped", "explicit_resolution",
    "false_positive", "surfaced_within_turn", "injection_turn",
)


class R1ContradictionProbe:
    """Scores the contradiction-surfacing gate from episode traces.

    Population split (PR #2341 review round 2, P2): the PLANTED ct
    population measures the surfaced-within-1-turn rule (below); the benign
    bct FP-control population is scored via ``score_control`` on the
    log-derived control verdict (``false_positive``) under a DISTINCT metric
    — a control episode is never scored on the surfaced rule (no planted ¬A
    turn → k=0, so an FP at any later turn would read as a surfaced-rate
    true negative and bct 0.0s would cap a flawless planted run at 15/21 <
    the 0.90 surfaced-rate [cal] row). Control-verdict emission is
    executor-owned (Task 9): until it lands, control episodes report the
    no-data sentinel (insufficient_n).
    """

    #: Hyphenated cal-table metric key (thresholds.yaml) — PLANTED population.
    cal_metric = "surfaced-rate"
    probe_id = "R1"
    metric = "surfaced_rate"

    #: FP-control population capability (benign bct twins): control episodes
    #: record under ``control_cal_metric``, never under the surfaced metric.
    supports_control_population = True
    control_cal_metric = "false-positive-rate"
    control_metric = "false_positive_rate"

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

    def score_control(self, trace: dict[str, Any],
                      threshold: float = 0.0) -> ProbeResult:
        """One benign FP-control episode's verdict (bct population): 1.0
        when the arm wrongly flagged a conflict on the benign surface (the
        log-derived control verdict ``false_positive`` is True), 0.0 when it
        correctly stayed quiet. Threshold 0.0 = a per-episode FP always
        fails the gate (the ≤5% rate row is a pool statistic that locks with
        the Task-9 executor + cal table). An ABSENT verdict never reaches
        here — the adapter turns it into the no-data sentinel."""
        fp = bool(trace.get("false_positive", False))
        return ProbeResult(
            probe_id=self.probe_id, scenario_id=trace.get("scenario_id", "?"),
            metric=self.control_metric, value=1.0 if fp else 0.0,
            passed=not fp, threshold=threshold,
            evidence=(f"control_verdict=false_positive={fp}",))
