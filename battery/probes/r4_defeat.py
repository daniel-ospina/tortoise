"""R4 — defeat conditions (E2E-1.1: precision ≥70% + ≥1 real defeat
condition per decision).

The agent states 'what evidence would overturn this decision'; the stated
conditions are compared against the graph's ACTUAL NAND/mitigation
structure (the real weakest links). Precision = fraction of stated
conditions that correspond to real graph edges.
"""
from __future__ import annotations

from typing import Any

from battery.probes.base import ProbeResult


#: Schema-v1.1 emitter-registry contract (issue #2284): the trace semantic
#: keys this probe reads. Declarative only — behavior unchanged until the
#: probe re-points reads onto the registry-emitted log (Task 9).
CONSUMED_FIELDS: tuple[str, ...] = (
    "stated_defeat_conditions", "real_defeat_conditions",
)


class R4DefeatProbe:
    #: Hyphenated cal-table metric key (thresholds.yaml).
    cal_metric = "defeat-precision"
    probe_id = "R4"
    metric = "defeat_precision"

    def score(self, trace: dict[str, Any],
              gold: str | None, threshold: float) -> ProbeResult:
        stated = trace.get("stated_defeat_conditions", [])
        real = trace.get("real_defeat_conditions", [])
        real_set = set(real)
        if not stated:
            return ProbeResult(
                probe_id=self.probe_id, scenario_id=trace.get("scenario_id", "?"),
                metric=self.metric, value=0.0, passed=False,
                threshold=threshold, evidence=("no defeat conditions stated",))
        precision = sum(1 for s in stated if s in real_set) / len(stated)
        return ProbeResult(
            probe_id=self.probe_id, scenario_id=trace.get("scenario_id", "?"),
            metric=self.metric, value=precision, passed=precision >= threshold,
            threshold=threshold,
            evidence=(f"precision={precision:.2f} stated={len(stated)} "
                      f"real={len(real)}",))

    def completeness(self, traces: list[dict[str, Any]]) -> bool:
        """AC: ≥1 REAL defeat condition found per decision — a stated
        condition that does not correspond to a real graph edge does not
        count (precision would be 0 on it)."""
        for t in traces:
            real = set(t.get("real_defeat_conditions", []))
            stated = t.get("stated_defeat_conditions", [])
            if not any(s in real for s in stated):
                return False
        return True
