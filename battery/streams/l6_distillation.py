"""L6 — distillation fidelity (E2E-2.6).

After consolidation (tortoise/topic_summarization.py — reuse, don't
rebuild), reasoning tasks answered from the DISTILLED graph must score as
well as from RAW sessions: reasoning-fidelity = distilled/raw ≥ 0.95 [cal];
no contradiction pair dropped below the surfacing threshold.
"""
from __future__ import annotations

from typing import Any

from battery.streams.base import StreamResult


class L6DistillationStream:
    stream_id = "L6"
    metric = "reasoning_fidelity"

    def score(self, sessions: list[dict[str, Any]],
              golds: dict[str, str] | None,
              threshold: float) -> StreamResult:
        """sessions: per scenario {distilled_score, raw_score} (both from
        the R1–R5 rubric, #1409)."""
        if not sessions:
            return StreamResult(self.stream_id, self.metric, 0.0, False,
                                threshold, ())
        pairs = [(float(s.get("distilled_score", 0.0)),
                  float(s.get("raw_score", 0.0))) for s in sessions]
        pairs = [(d, r) for d, r in pairs if r > 0]
        if not pairs:
            return StreamResult(self.stream_id, self.metric, 0.0, False,
                                threshold, ())
        fidelity = sum(d / r for d, r in pairs) / len(pairs)
        dropped = [s for s in sessions
                   if s.get("contradiction_dropped", False)]
        passed = fidelity >= threshold and not dropped
        return StreamResult(
            self.stream_id, self.metric, fidelity, passed, threshold,
            trajectory=tuple(d / r for d, r in pairs),
            evidence=(f"fidelity={fidelity:.3f} n={len(pairs)} "
                      f"dropped_pairs={len(dropped)}",))
