"""R2 — adversarial deliberation coverage (E2E-1.1 gate: ≥1.5× vs A0).

The JUDGED adversarial-coverage subscore (via the validated judge) measures
counter-argument coverage, mitigation-first behavior, and 'what could be
wrong' specificity. The Tier-1 MECHANISM gate (≥80% of decisions reach 3+
Challenge/Deepen cycles) is a separate diagnostic — it is EXCLUDED from the
Tier-3 verdict (spec §R2) and reported via decide_cycles.
"""
from __future__ import annotations

from typing import Any, Callable

from battery.probes.base import ProbeResult


class R2CoverageProbe:
    """Adversarial-coverage scoring (judge-gated subscore + mechanism gate)."""

    probe_id = "R2"
    metric = "coverage_subscore"

    def score(self, trace: dict[str, Any],
              gold: str | None, threshold: float) -> ProbeResult:
        # trace['coverage_subscore'] is populated by the gated judge
        # (validated rubric only — RubricRegistry.require_validated).
        sub = float(trace.get("coverage_subscore", 0.0))
        return ProbeResult(
            probe_id=self.probe_id, scenario_id=trace.get("scenario_id", "?"),
            metric=self.metric, value=sub, passed=sub >= threshold,
            threshold=threshold,
            evidence=(f"coverage_subscore={sub:.2f}",))

    def mechanism_gate(self, traces: list[dict[str, Any]]) -> float:
        """Tier-1 process-fidelity: fraction of decisions reaching 3+
        Challenge/Deepen cycles (decide_cycles >= 3). REPORTED, not scored
        in the Tier-3 verdict (spec §R2)."""
        if not traces:
            return 0.0
        ok = [t for t in traces if int(t.get("decide_cycles", 0)) >= 3]
        return len(ok) / len(traces)

    def delta_vs_control(self, treatment: float, control: float) -> float:
        """AC gate: treatment coverage ≥ 1.5× control."""
        return treatment / control if control > 0 else 0.0
