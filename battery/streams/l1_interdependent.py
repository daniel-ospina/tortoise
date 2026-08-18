"""L1 — interdependent-task stream (MemoryArena-style, E2E-2.2).

Later subtasks depend on earlier sessions' decisions. Metrics: composite
success ≥0.85 vs ≤0.5 control; recall-before-re-derive ≥90%; re-derivation
tool calls ≥5× fewer than control; provenance criterion (≥1 stored point
id verbatim + confidence ∈ [0,1] per cited point — reuses the recall
matcher semantics).
"""
from __future__ import annotations

from typing import Any

from battery.streams.base import StreamResult


class L1InterdependentStream:
    stream_id = "L1"
    metric = "interdependent_success"

    def score(self, sessions: list[dict[str, Any]],
              golds: dict[str, str] | None,
              threshold: float) -> StreamResult:
        if not sessions:
            return StreamResult(self.stream_id, self.metric, 0.0, False,
                                threshold, ())
        successes = [s for s in sessions if s.get("subtask_success", False)]
        rate = len(successes) / len(sessions)
        return StreamResult(
            self.stream_id, self.metric, rate, rate >= threshold,
            threshold,
            trajectory=tuple(float(s.get("subtask_success", False))
                             for s in sessions),
            evidence=(f"success={rate:.2f} n={len(sessions)}",))

    def recall_before_rederive_rate(self, sessions: list[dict]) -> float:
        """Fraction of dependent subtasks answered from memory (provenance
        citation present) before any re-derivation tool call. AC: ≥90%."""
        if not sessions:
            return 0.0
        ok = [s for s in sessions
              if s.get("recalled_before_rederive", False)]
        return len(ok) / len(sessions)

    def rederivation_ratio(self, treatment: float, control: float) -> float:
        """AC: treatment re-derivation calls ≥5× fewer than control."""
        return control / treatment if treatment > 0 else 0.0

    def provenance_ok(self, session: dict[str, Any]) -> bool:
        """Provenance criterion: ≥1 stored point id verbatim AND a
        confidence value ∈ [0,1] per cited point (plan E2E-2.2)."""
        cited = session.get("provenance_citations", [])
        if not cited:
            return False
        return all(
            isinstance(c.get("point_id"), str) and c.get("point_id")
            and isinstance(c.get("confidence"), (int, float))
            and 0.0 <= c["confidence"] <= 1.0
            for c in cited)
