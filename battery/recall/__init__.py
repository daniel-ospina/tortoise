"""Matched-recall pre-pass (issue #1413): top-K factual F1 per arm with the
symmetric trigger + INCONCLUSIVE branch. Outcome is immutable per run and
recorded in profile.json — never re-interpreted post-hoc.
"""
from __future__ import annotations

from battery.recall.matcher import (
    F1_TOLERANCE,
    SUBSET_FLOOR,
    TOP_K,
    FactualProbe,
    RecallResult,
    Retriever,
    default_probes,
    match_recall,
)

__all__ = [
    "F1_TOLERANCE", "SUBSET_FLOOR", "TOP_K", "FactualProbe",
    "RecallResult", "Retriever", "default_probes", "match_recall",
]
