"""L4 — cross-session contradiction accumulation (E2E-2.4).

Contradictions only visible across sessions (A in s1, ¬A in s5; surfaced
when queried in s6+). AC: 100% surfaced by session N+1; surfacing latency
DECREASES as the graph grows.
"""
from __future__ import annotations

from typing import Any

from battery.streams.base import StreamResult


class L4CrossSessionStream:
    stream_id = "L4"
    metric = "surfaced_by_n1_rate"

    def score(self, sessions: list[dict[str, Any]],
              golds: dict[str, str] | None,
              threshold: float) -> StreamResult:
        """sessions: per-planted-contradiction {planted_session, surfaced_session,
        query_session, resolved_via_supersede}."""
        if not sessions:
            return StreamResult(self.stream_id, self.metric, 0.0, False,
                                threshold, ())
        ok = [s for s in sessions
              if s.get("surfaced_session", 0) is not None
              and s.get("surfaced_session", 0) <= int(s.get("query_session", 0))
              and s.get("resolved_via_supersede", False)]
        rate = len(ok) / len(sessions)
        latencies = [int(s.get("surfaced_session", 0))
                     - int(s.get("planted_session", 0))
                     for s in sessions if s.get("surfaced_session")]
        return StreamResult(
            self.stream_id, self.metric, rate, rate >= threshold,
            threshold,
            trajectory=tuple(float(l) for l in latencies),
            evidence=(f"surfaced={rate:.2f} n={len(sessions)}",))

    def latency_trend(self, sessions: list[dict[str, Any]]) -> bool:
        """Surfacing latency must NOT rise as the graph grows (earlier
        surfacing with density). Compares first-half vs second-half mean."""
        lat = [int(s.get("surfaced_session", 0)) - int(s.get("planted_session", 0))
               for s in sessions if s.get("surfaced_session")]
        if len(lat) < 4:
            return True  # insufficient data — no trend claim
        half = len(lat) // 2
        first = sum(lat[:half]) / half
        second = sum(lat[half:]) / (len(lat) - half)
        return second <= first
