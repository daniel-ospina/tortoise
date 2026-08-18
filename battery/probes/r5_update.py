"""R5 — belief-update responsiveness (E2E-1.1: correct-direction ≥90%;
over-reaction ≤10%).

When evidence is retracted/undercut mid-session, the arm's position must
move proportionally in the correct direction — not stubbornly hold, not
recency-wins flip. The flat-store failure mode (0% responsive) is an
EMPIRICAL expectation about controls, not a by-construction win: an LLM
with in-context retraction CAN update (spec §R5 note).
"""
from __future__ import annotations

from typing import Any

from battery.probes.base import ProbeResult


class R5UpdateProbe:
    #: Hyphenated cal-table metric key (thresholds.yaml).
    cal_metric = "correct-direction-rate"
    probe_id = "R5"
    metric = "correct_direction_rate"

    def score(self, trace: dict[str, Any],
              gold: str | None, threshold: float) -> ProbeResult:
        correct = bool(trace.get("update_correct_direction", False))
        return ProbeResult(
            probe_id=self.probe_id, scenario_id=trace.get("scenario_id", "?"),
            metric=self.metric, value=1.0 if correct else 0.0,
            passed=correct, threshold=threshold,
            evidence=(f"correct_direction={correct}",))

    def over_reaction_rate(self, traces: list[dict[str, Any]]) -> float:
        """Fraction of runs where a WEAK evidence change produced a FULL flip
        (over-reaction). AC: ≤10%."""
        if not traces:
            return 0.0
        over = [t for t in traces if t.get("over_reacted", False)]
        return len(over) / len(traces)
