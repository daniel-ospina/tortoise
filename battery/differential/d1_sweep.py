"""D1 — the differential sweep (E2E-3.1).

Runs the same battery (Tier-1 probes + Tier-2 streams) on all arms and
emits the RAW 14-family × 6-arm matrix. Classification into
STRONG/STRUCTURAL/PARITY/WEAK + load-bearing flags is #1415's report
logic (E2E-3.2) — this module emits the raw matrix only (the pinned
boundary from the #1412 clarity fix).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

METRIC_FAMILIES: tuple[str, ...] = (
    "R1", "R2", "R3", "R4", "R5",
    "L1", "L2", "L3", "L4", "L5", "L6",
    "D2", "D3", "D4",
)


@dataclass(frozen=True)
class SweepMatrix:
    """Raw metric × arm value matrix (no classification — #1415's job)."""

    values: dict[str, dict[str, float]]  # family -> arm -> value
    matched_recall: dict[str, Any] = field(default_factory=dict)

    def families(self) -> tuple[str, ...]:
        return tuple(sorted(self.values))

    def arms(self) -> tuple[str, ...]:
        return tuple(sorted({a for fam in self.values.values() for a in fam}))


def build_matrix(results: dict[str, dict[str, float]],
                matched_recall: dict[str, Any] | None = None) -> SweepMatrix:
    """results: family -> arm -> measured value (14 families, 6 arms)."""
    missing = [f for f in METRIC_FAMILIES if f not in results]
    if missing:
        raise ValueError(
            f"sweep matrix incomplete — missing families: {missing} "
            f"(full profile, no exclusions — owner decision)")
    return SweepMatrix(values=results,
                       matched_recall=matched_recall or {})
