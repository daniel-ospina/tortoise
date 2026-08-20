"""Differentiation classification (issue #1415, spec §6).

Every metric × arm cell is classified STRONG / STRUCTURAL / PARITY / WEAK
with a load-bearing flag. Load-bearing = the axis is customer-visible
(contradiction, staleness, calibration, decision consistency,
improvement-over-time) — the axes where a measured advantage converts.

Classification (owner decision 2026-08-14, no exclusions): ALL metrics are
scored and reported; the verdict requires ≥1 empirically-won STRONG on a
load-bearing axis (structural wins are reported, not disqualifying, not
sufficient — competitors could replicate the primitive).
"""
from __future__ import annotations

from dataclasses import dataclass, field  # noqa: F401

#: Metric families whose axis is customer-visible (verdict load-bearing).
LOAD_BEARING_FAMILIES: frozenset[str] = frozenset({
    "R1", "R2", "R3", "R4", "R5",   # contradiction, coverage, calibration,
                                    # defeat conditions, belief updates
    "L1", "L2", "L3", "L4", "L5",   # improvement-over-time axes
    "D3", "D4",                     # feedback, robustness
})
#: Families won by construction for A4 (the graph's primitives firing).
STRUCTURAL_FAMILIES: frozenset[str] = frozenset({"R1", "R4"})

CLASSIFICATIONS = ("STRONG", "STRUCTURAL", "PARITY", "WEAK")


@dataclass(frozen=True)
class CellClassification:
    family: str
    arm: str
    value: float
    classification: str
    load_bearing: bool


def classify_cell(family: str, arm: str, value: float,
                  best_comparator: float,
                  delta_threshold: float = 0.10) -> CellClassification:
    """Classify one cell vs the best comparator arm.

    - STRONG: value beats best_comparator by ≥ delta AND not structural
    - STRUCTURAL: family is a structural win for A4 (won by construction)
    - PARITY: within ±delta of the comparator
    - WEAK: comparator beats value by ≥ delta
    """
    if (family in STRUCTURAL_FAMILIES and arm == "a4"
            and value >= best_comparator):
        # Structural label only when the primitive actually WON — an a4
        # loss on R1/R4 must surface as WEAK, never masked (code-review P2).
        return CellClassification(family, arm, value, "STRUCTURAL",
                                  family in LOAD_BEARING_FAMILIES)
    if value >= best_comparator + delta_threshold:
        return CellClassification(family, arm, value, "STRONG",
                                  family in LOAD_BEARING_FAMILIES)
    if best_comparator >= value + delta_threshold:
        return CellClassification(family, arm, value, "WEAK",
                                  family in LOAD_BEARING_FAMILIES)
    return CellClassification(family, arm, value, "PARITY",
                              family in LOAD_BEARING_FAMILIES)
