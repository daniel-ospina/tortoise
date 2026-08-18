"""Aggregation — the E2E-1.5 aggregation half (scope DD8/AC6).

Episodes with ANY terminal non-ok call outcome (fallback_cached/failed/
rate_limited/timeout after retries) are EXCLUDED from metric aggregates and
reported as a count with their episode ids — never silent, never merged
into the aggregates. Only all-ok episodes contribute to aggregates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from battery.runner.episode import EpisodeResult


@dataclass(frozen=True)
class Aggregate:
    """Per-arm aggregation over episode results (valid-only)."""

    valid_episodes: int = 0
    excluded_count: int = 0
    excluded_episode_ids: tuple[str, ...] = ()
    excluded_reason: str = "none"
    metric_sums: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid_episodes": self.valid_episodes,
            "excluded": {
                "count": self.excluded_count,
                "episode_ids": list(self.excluded_episode_ids),
                "reason": self.excluded_reason,
            },
            "metric_sums": dict(self.metric_sums),
        }


def aggregate(episodes: list[EpisodeResult],
              metric_ids: tuple[str, ...]) -> Aggregate:
    """Exclude invalid episodes; sum metric values over valid ones.

    ``metric_ids`` is the scorer's pinned metric set; sums are over the
    valid episodes' own ``metric_values`` (the runner wrote them from the
    actual scorer — trace-derived, so trace ↔ metric_values cannot drift).
    """
    valid = [e for e in episodes if e.valid]
    excluded = [e for e in episodes if not e.valid]
    metric_sums: dict[str, float] = {}
    for ep in valid:
        for mid in metric_ids:
            metric_sums[mid] = metric_sums.get(mid, 0.0) + ep.metric_values.get(mid, 0.0)
    first_excluded = excluded[0] if excluded else None
    return Aggregate(
        valid_episodes=len(valid),
        excluded_count=len(excluded),
        excluded_episode_ids=tuple(e.scenario_id for e in excluded),
        excluded_reason=(first_excluded.excluded_reason
                         if first_excluded and first_excluded.excluded_reason
                         else ("terminal non-ok call outcome" if excluded else "none")),
        metric_sums=metric_sums,
    )
