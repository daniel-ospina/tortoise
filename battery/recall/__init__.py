"""Matched-recall pre-pass (issue #1413): top-K factual F1 per arm with the
symmetric trigger + INCONCLUSIVE branch. Outcome is immutable per run and
recorded in profile.json — never re-interpreted post-hoc.

#3327 wires the pre-pass into the run path: ``prepass.py`` adapts an arm's
``ArmAdapter.retrieve`` onto the ``Retriever`` protocol and assembles the
persisted block; the trigger population is ``TRIGGER_POPULATION``
(``{a1, a2, a2b, a3, a4}`` — a0 excluded, retained as the positive control).
"""
from __future__ import annotations

from battery.recall.matcher import (
    EXCLUDED_CONTROL_REASON,
    EXCLUDED_CONTROLS,
    F1_TOLERANCE,
    SUBSET_FLOOR,
    TOP_K,
    TRIGGER_POPULATION,
    FactualProbe,
    RecallResult,
    Retriever,
    default_probes,
    match_recall,
    scenario_probes,
)
from battery.recall.prepass import (
    A0StubRetriever,
    ArmFactualRetriever,
    CapturedRetriever,
    build_matched_recall_block,
    capture_factual_recall,
    question_scenario_map,
)

__all__ = [
    "EXCLUDED_CONTROLS",
    "EXCLUDED_CONTROL_REASON",
    "F1_TOLERANCE",
    "SUBSET_FLOOR",
    "TOP_K",
    "TRIGGER_POPULATION",
    "A0StubRetriever",
    "ArmFactualRetriever",
    "CapturedRetriever",
    "FactualProbe",
    "RecallResult",
    "Retriever",
    "build_matched_recall_block",
    "capture_factual_recall",
    "default_probes",
    "match_recall",
    "question_scenario_map",
    "scenario_probes",
]
