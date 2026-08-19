"""L3 — reasoning-quality trajectory (the core claim, E2E-2.3).

Tier-1 probes as recurring checkpoints across ≥3 waves (held-out variants
per wave). AC: reasoning-quality slope > 0 across ≥3 waves (treatment) vs
≈0 for control. The quality index aggregates the rubric-scored probe
subscores (coverage, Brier, contradiction-surfacing, correctness).
"""
from __future__ import annotations

from typing import Any

from battery.streams.base import StreamResult


def _slope(waves: list[float]) -> float:
    """Least-squares slope over the wave indices (0..n-1)."""
    n = len(waves)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(waves) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, waves))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0


class L3QualityTrajectoryStream:
    stream_id = "L3"
    metric = "quality_slope"

    def score(self, sessions: list[dict[str, Any]],
              golds: dict[str, str] | None,
              threshold: float) -> StreamResult:
        """sessions: per-wave {wave, quality_index}."""
        if not sessions:
            return StreamResult(self.stream_id, self.metric, 0.0, False,
                                threshold, ())
        by_wave: dict[int, list[float]] = {}
        for s in sessions:
            by_wave.setdefault(int(s.get("wave", 0)), []).append(
                float(s.get("quality_index", 0.0)))
        waves = [sum(v) / len(v) for _, v in sorted(by_wave.items())]
        if len(waves) < 3:
            return StreamResult(self.stream_id, self.metric, 0.0, False,
                                threshold, tuple(waves))
        sl = _slope(waves)
        return StreamResult(
            self.stream_id, self.metric, sl, sl > threshold, threshold,
            trajectory=tuple(waves),
            evidence=(f"slope={sl:.3f} waves={len(waves)} "
                      f"quality={[round(w,2) for w in waves]}",))
