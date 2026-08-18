"""L5 — decision-drift resistance (E2E-2.5).

Same decision re-derived at t+7d/t+21d (fresh context). AC: treatment
decision-consistency ≥90% + rationale-consistency ≥80% +
hallucinated-rationale ≤10% (control ~100% fabricates); control drift ≥30%
(calibration floor).
"""
from __future__ import annotations

from typing import Any

from battery.streams.base import StreamResult


class L5DecisionDriftStream:
    stream_id = "L5"
    metric = "decision_consistency"

    def score(self, sessions: list[dict[str, Any]],
              golds: dict[str, str] | None,
              threshold: float) -> StreamResult:
        """sessions: per re-derivation {decision, rationale, t_days}."""
        if not sessions:
            return StreamResult(self.stream_id, self.metric, 0.0, False,
                                threshold, ())
        consistent = [s for s in sessions if s.get("decision_matches", False)]
        rate = len(consistent) / len(sessions)
        return StreamResult(
            self.stream_id, self.metric, rate, rate >= threshold,
            threshold,
            trajectory=tuple(float(s.get("decision_matches", False))
                             for s in sessions),
            evidence=(f"decision_consistency={rate:.2f} n={len(sessions)}",))

    def rationale_consistency(self, sessions: list[dict[str, Any]]) -> float:
        """AC: ≥80% — same criteria weighted + same counter-arguments."""
        if not sessions:
            return 0.0
        ok = [s for s in sessions if s.get("rationale_matches", False)]
        return len(ok) / len(sessions)

    def hallucinated_rationale_rate(self, sessions: list[dict[str, Any]]) -> float:
        """AC: ≤10% treatment (control fabricates ~100%)."""
        if not sessions:
            return 0.0
        hall = [s for s in sessions if s.get("hallucinated_rationale", False)]
        return len(hall) / len(sessions)
