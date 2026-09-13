"""Issue #1413 — matched-recall pre-pass: F1, symmetric trigger (A4
included), balanced subset, INCONCLUSIVE branch (E2E-3.7)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.recall.matcher import (  # noqa: I001
    FactualProbe,  # noqa: F401
    RecallResult,  # noqa: F401
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


# ---------------------------------------------------------------------------
# #3327 — trigger population: a0 EXCLUDED from the trigger, retained as the
# positive control; probes sourced from the scenario corpus
# ---------------------------------------------------------------------------

def test_trigger_population_is_the_retrieval_capable_comparators():
    from battery.recall.matcher import TRIGGER_POPULATION
    assert TRIGGER_POPULATION == ("a1", "a2", "a2b", "a3", "a4")
    assert "a0" not in TRIGGER_POPULATION


def test_a0_zero_recall_does_not_drive_the_trigger():
    """The property that makes the four-verdict contract meaningful: a0 is
    MEASURED at 0.0 and every comparator arm agrees at 1.0 — the run is
    matched. An a0-inclusive trigger would fire here on every run."""
    probes = default_probes()
    all_ids = {p.id for p in probes}
    arms = _arms({"a0": set(), "a1": all_ids, "a2": all_ids,
                  "a2b": all_ids, "a3": all_ids, "a4": all_ids})
    r = match_recall(probes, arms)
    assert r.f1_by_arm["a0"] == 0.0     # measured + published, never dropped
    assert r.trigger_fired is False     # a0's constant 0.0 is not the trigger
    assert r.outcome == "matched"


def test_in_population_divergence_fires_the_trigger():
    probes = default_probes()
    all_ids = {p.id for p in probes}
    half = set(list(all_ids)[:4])
    arms = _arms({"a2": all_ids, "a4": half})
    r = match_recall(probes, arms)
    assert r.trigger_fired is True
    assert r.subset_pct >= 0.5          # balanced subset survived the floor
    assert r.outcome == "matched"       # rerun on the matched subset


def test_positive_control_fixture_fires_inconclusive_with_a0_present():
    """E2E-3.7 positive control: a0 is present at 0.0 (never dropped) and
    the detector CAN fire — an IN-POPULATION arm (a1) diverges on a disjoint
    question set, collapsing the balanced subset below 50%."""
    probes = default_probes()
    all_ids = {p.id for p in probes}
    arms = _arms({"a0": set(), "a1": {next(iter(all_ids))}, "a4": all_ids})
    r = match_recall(probes, arms)
    assert r.f1_by_arm["a0"] == 0.0
    assert r.trigger_fired is True      # fired by a1, not by a0
    assert r.subset_pct < 0.5
    assert r.outcome == "inconclusive"


def test_control_only_population_refuses_rather_than_vacuous_matched():
    """A probe set whose only arm is the excluded control can not express
    the relative trigger — a vacuous 'matched' would be a false pass."""
    probes = default_probes()
    with pytest.raises(ValueError):
        match_recall(probes, _arms({"a0": set()}))


class _Scn:
    """Minimal Scenario surface for scenario_probes (id/question/golds)."""

    def __init__(self, sid: str, question: str = "", gold: str = ""):
        self.id = sid
        self.question = question
        self._gold = gold

    def golds(self):
        return (self._gold,) if self._gold else ()


def test_scenario_probes_are_sourced_from_the_corpus():
    """#3327.3 — probes land on the scenario corpus (01-align.md:56), NOT on
    default_probes()' generic world facts. Scenarios without a question or a
    gold are skipped, never fabricated into a probe."""
    from battery.recall.matcher import scenario_probes
    scenarios = [
        _Scn("s1", "q one?", "gold one"),
        _Scn("s2"),                      # no question -> skipped
        _Scn("s3", "q three?", ""),      # no gold -> skipped
        _Scn("s4", "q four?", "gold four"),
    ]
    probes = scenario_probes(scenarios)
    assert [p.id for p in probes] == ["s1", "s4"]
    assert probes[0].question == "q one?"
    assert probes[0].gold == "gold one"
    assert len(scenario_probes(scenarios, limit=1)) == 1


def test_default_probes_is_a_hermetic_fixture_not_the_run_source():
    from battery.recall.matcher import scenario_probes
    assert default_probes(), "the hermetic fixture stays available for tests"
    corpus = scenario_probes([_Scn("s1", "corpus question?", "corpus gold")])
    assert corpus[0].question == "corpus question?"
    assert corpus[0].question != default_probes()[0].question
