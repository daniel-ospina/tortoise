"""Issue #1413 — matched-recall pre-pass: F1, symmetric trigger (A4
included), balanced subset, INCONCLUSIVE branch (E2E-3.7)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.recall.matcher import (
    FactualProbe,
    RecallResult,
    match_recall,
    default_probes,
)


class _Ret:
    """Deterministic retriever: returns gold for a subset of questions."""

    def __init__(self, gold_ids: set[str]):
        self._gold_ids = gold_ids

    def retrieve_factual(self, question: str, k: int = 5) -> list[str]:
        # Map question back to probe id via gold-token matching.
        for p in default_probes():
            if p.question == question:
                return [p.gold] if p.id in self._gold_ids else []
        return []


def _arms(gold_ids: dict[str, set[str]]):
    return {aid: _Ret(gids) for aid, gids in gold_ids.items()}


def test_all_matched_no_trigger():
    probes = default_probes()
    arms = _arms({"a4": {p.id for p in probes}, "a2": {p.id for p in probes}})
    r = match_recall(probes, arms)
    assert r.outcome == "matched"
    assert not r.trigger_fired
    assert r.f1_by_arm["a4"] == 1.0


def test_symmetric_trigger_includes_a4():
    """A4 may LOSE to RAG on factual F1 — the trigger must fire on A4 too."""
    probes = default_probes()
    all_ids = {p.id for p in probes}
    arms = _arms({"a2": all_ids, "a4": set()})  # RAG beats the graph
    r = match_recall(probes, arms)
    assert r.trigger_fired
    assert r.outcome == "inconclusive" or r.f1_by_arm["a4"] < r.f1_by_arm["a2"]


def test_inconclusive_branch():
    """Strong RAG arm vs weak graph arm answering a disjoint question →
    intersection < 50% → INCONCLUSIVE (E2E-3.7 exercised, not vacuous)."""
    probes = default_probes()
    all_ids = {p.id for p in probes}
    arms = _arms({"a2": all_ids, "a4": {next(iter(all_ids))}})
    r = match_recall(probes, arms)
    assert r.trigger_fired
    assert r.subset_pct < 0.5
    assert r.outcome == "inconclusive"
    assert r.f1_by_arm["a4"] < r.f1_by_arm["a2"]


def test_result_immutable():
    import dataclasses
    probes = default_probes()
    arms = _arms({"a4": {p.id for p in probes}})
    r = match_recall(probes, arms)
    assert dataclasses.is_dataclass(r) and r.__dataclass_params__.frozen
    with pytest.raises(TypeError):
        r.f1_by_arm["a4"] = 0.0  # Mapping is immutable (not a plain dict)


def test_balanced_subset_rerun():
    """Trigger fires; kept subset ≥50% → rerun F1 on the subset."""
    probes = default_probes()
    all_ids = {p.id for p in probes}
    half = set(list(all_ids)[:4])
    arms = _arms({"a2": all_ids, "a4": half})
    r = match_recall(probes, arms)
    assert r.trigger_fired
    if r.outcome == "matched":
        assert r.subset_pct >= 0.5
        assert r.f1_by_arm["a4"] <= r.f1_by_arm["a2"]
