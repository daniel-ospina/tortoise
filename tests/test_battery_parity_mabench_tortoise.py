"""#2800 — the TORTOISE (retrieved-context) lane of MemoryAgentBench CR.

The value of this lane rests on four things, and each has a test here:

* the pool is ingested ONCE, before any question, and the written count is
  reported (a silently-partial ingest would understate retrieval);
* the reader sees the RETRIEVED facts — not the full pool — in the benchmark's
  OWN prompt template (the only difference from the baseline row);
* the cell carries a lane label distinct from the baseline's ``real``/``mock``;
* the accuracy is the benchmark's own metric over exactly the questions asked,
  and every failure path is a refusal (``ExecutorUnavailable`` / empty-
  measurement), never a partial score.

Everything here is hermetic: a fake injected memory and a deterministic
reader. No network, no keys, no spend.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.parity.executors import (
    EXECUTORS,
    LANES,
    ExecutedCell,
    ExecutorUnavailable,
)
from battery.parity.mabench import CR_SHA256, CrConfig, CrItem, MabenchError, score_cr
from battery.parity.mabench_run import SYSTEM_MESSAGE, build_lane_prompt
from battery.parity.mabench_tortoise import (
    DEFAULT_K,
    LANE_MOCK,
    LANE_REAL,
    _FakeCrMemory,
    run_cr_tortoise_lane,
    split_fact_units,
)

HEADER = "Here is a list of facts:"
#: The pool mirrors the pinned shape: a header line then numbered fact units,
#: with a conflicting pair (same subject/relation, different serial + answer).
POOL_UNITS = (
    "0. Thomas Kyd was born in the city of London.",
    "1. The chairperson of Fatah is Mahmoud Abbas.",
    "2. Amy Winehouse died in the city of Camden Town.",
    "306. Thomas Kyd was born in the city of Leeds.",
    "7. Bengaluru is located in the continent of Asia.",
)
POOL = "\n".join((HEADER, *POOL_UNITS))

ITEMS = (
    CrItem(qa_pair_id="q1", config="c",
           question="Where was Thomas Kyd born?", accepted=("Leeds",)),
    CrItem(qa_pair_id="q2", config="c",
           question="Who chairs Fatah?", accepted=("Mahmoud Abbas",)),
    CrItem(qa_pair_id="q3", config="c",
           question="Which continent is Bengaluru in?", accepted=("Asia",)),
)


class ScriptedCaller:
    """Deterministic reader: one scripted answer per call."""

    def __init__(self, answers, *, prompt_tokens=0, completion_tokens=0,
                 fail_on=None):
        self._answers = list(answers)
        self.prompts: list[str] = []
        self.last_prompt_tokens = prompt_tokens
        self.last_completion_tokens = completion_tokens
        self._fail_on = fail_on

    def call(self, *, prompt: str) -> str:
        self.prompts.append(prompt)
        if self._fail_on is not None and len(self.prompts) == self._fail_on:
            raise RuntimeError("reader unavailable")
        return self._answers.pop(0)


class RecordingMemory:
    """A fake ``CrMemory`` that records the ORDER of ingest/recall calls."""

    def __init__(self, *, returns=None, fail_ingest=False, fail_recall=False,
                 short_ingest=False):
        self.events: list[tuple[str, object]] = []
        self.ingested: list[str] = []
        self.recalls: list[tuple[str, int]] = []
        self._returns = returns
        self._fail_ingest = fail_ingest
        self._fail_recall = fail_recall
        self._short_ingest = short_ingest

    def ingest(self, texts: list[str]) -> int:
        self.events.append(("ingest", len(texts)))
        if self._fail_ingest:
            raise RuntimeError("store down")
        self.ingested = list(texts)
        return len(texts) - 1 if self._short_ingest else len(texts)

    def recall(self, question: str, k: int) -> list[str]:
        self.events.append(("recall", question))
        if self._fail_recall:
            raise RuntimeError("read down")
        self.recalls.append((question, k))
        if self._returns is not None:
            return list(self._returns)
        # Lexical fallback over what was ingested (deterministic).
        tokens = set(question.lower().split())
        return [f for f in self.ingested
                if tokens & set(f.lower().split())][:k]


def _run(items, memory, caller, **kw):
    return run_cr_tortoise_lane(
        items, memory, caller, lane=kw.pop("lane", LANE_MOCK),
        config=kw.pop("config", "c"), context=kw.pop("context", POOL), **kw)


class TestFactUnitSplit:
    """The split must be lossless: no fact may silently vanish from a run."""

    def test_header_and_blanks_dropped_every_fact_kept(self):
        units = split_fact_units("\n\n" + POOL + "\n\n")
        assert units == list(POOL_UNITS)

    def test_serials_are_preserved(self):
        """The CR conflict rule is 'larger serial number wins' — stripping the
        serial would delete the only conflict-resolution signal."""
        units = split_fact_units(POOL)
        assert units[0].startswith("0. ")
        assert units[3].startswith("306. ")
        assert "Leeds" in units[3]

    def test_empty_pool_is_empty(self):
        assert split_fact_units("") == []
        assert split_fact_units(HEADER) == []


class TestLaneSemantics:
    def test_ingest_happens_once_before_any_question(self):
        memory = RecordingMemory()
        caller = ScriptedCaller(["Answer: Leeds", "Answer: Mahmoud Abbas",
                                 "Answer: Asia"])
        cell, run = _run(ITEMS, memory, caller)
        assert memory.events[0] == ("ingest", len(POOL_UNITS)), \
            "the pool is ingested ONCE, BEFORE the first question"
        assert [e[0] for e in memory.events[1:]] == ["recall"] * 3
        assert cell.detail["ingested"] == len(POOL_UNITS)
        assert run.samples == 3 and cell.samples == 3

    def test_recall_uses_the_configured_depth(self):
        memory = RecordingMemory()
        caller = ScriptedCaller(["Answer: x"] * 3)
        _run(ITEMS, memory, caller, k=4)
        assert memory.recalls == [(i.question, 4) for i in ITEMS]

    def test_prompt_carries_retrieved_facts_not_the_full_pool(self):
        retrieved = [POOL_UNITS[4]]  # only the Bengaluru fact
        memory = RecordingMemory(returns=retrieved)
        caller = ScriptedCaller(["Answer: Asia"])
        _run(ITEMS[:1], memory, caller)
        prompt = caller.prompts[0]
        assert POOL_UNITS[4] in prompt, "the retrieved fact is in the prompt"
        assert POOL_UNITS[0] not in prompt, \
            "a non-retrieved pool fact must NOT leak into the prompt"
        assert HEADER not in prompt, "the pool header is not a retrieved fact"
        assert SYSTEM_MESSAGE in prompt
        # Exactly the benchmark's own template, with the retrieved context.
        assert prompt == build_lane_prompt(POOL_UNITS[4], ITEMS[0].question)

    def test_samples_and_accuracy_are_the_official_metric(self):
        memory = RecordingMemory()
        caller = ScriptedCaller(["Answer: Leeds", "wrong", "Answer: Asia"])
        cell, run = _run(ITEMS, memory, caller, limit=2)
        # limit=2 asks q1 + q2 only; the second answer is wrong.
        expected, n = score_cr({"q1": "Answer: Leeds", "q2": "wrong"},
                               ITEMS[:2])
        assert cell.samples == n == 2 == run.samples
        assert cell.accuracy == pytest.approx(expected) == pytest.approx(0.5)
        assert cell.detail["calls"] == 2

    def test_revision_is_the_dataset_digest(self):
        memory = RecordingMemory()
        cell, _ = _run(ITEMS[:1], memory, ScriptedCaller(["Answer: x"]))
        assert cell.revision == f"memoryagentbench-cr@{CR_SHA256[:16]}"
        assert cell.benchmark == "memoryagentbench"
        assert cell.detail["config"] == "c"
        assert cell.detail["k"] == DEFAULT_K
        assert cell.detail["retrieved_chars"] > 0
        assert cell.detail["cost_basis"] == "estimated"

    def test_limit_zero_asks_nothing_and_ingests_nothing(self):
        """--limit 0 must ask NOTHING: not the whole corpus, and no unbounded
        write of a pool nothing will read."""
        memory = RecordingMemory()
        caller = ScriptedCaller([])
        with pytest.raises(MabenchError, match="empty measurement"):
            _run(ITEMS, memory, caller, limit=0)
        assert memory.events == [], "limit=0 must not ingest or recall"
        assert caller.prompts == []

    def test_non_positive_k_is_refused(self):
        """k=0 would hand the reader an empty context — a no-context baseline
        wearing the Tortoise label."""
        with pytest.raises(ValueError, match="retrieval depth k"):
            _run(ITEMS[:1], RecordingMemory(), ScriptedCaller(["Answer: x"]),
                 k=0)

    def test_negative_limit_is_refused(self):
        """A negative slice would silently drop the corpus's tail."""
        with pytest.raises(ValueError, match="limit must be >= 0"):
            _run(ITEMS, RecordingMemory(), ScriptedCaller(["Answer: x"]),
                 limit=-1)


class TestLaneIsLabelled:
    def test_tortoise_labels_are_distinct_from_the_baseline(self):
        assert LANE_MOCK != "mock" and LANE_REAL != "real"
        assert LANE_MOCK in LANES and LANE_REAL in LANES

    def test_mock_lane_cell_carries_the_tortoise_label(self):
        memory = RecordingMemory()
        cell, _ = _run(ITEMS[:1], memory, ScriptedCaller(["Answer: x"]))
        assert cell.lane == "mock_tortoise"

    def test_baseline_lane_labels_are_refused(self):
        """A Tortoise number must never be mistakable for the full-context
        baseline — so the lane refuses to wear the baseline's label."""
        with pytest.raises(ValueError, match="tortoise lane"):
            _run(ITEMS[:1], RecordingMemory(),
                 ScriptedCaller(["Answer: x"]), lane="mock")
        with pytest.raises(ValueError, match="tortoise lane"):
            _run(ITEMS[:1], RecordingMemory(),
                 ScriptedCaller(["Answer: x"]), lane="real")


class TestFailClosed:
    def test_ingest_failure_is_unavailable_and_not_a_score(self):
        memory = RecordingMemory(fail_ingest=True)
        caller = ScriptedCaller(["Answer: x"] * 3)
        with pytest.raises(ExecutorUnavailable, match="ingest failed"):
            _run(ITEMS, memory, caller)
        assert caller.prompts == [], "no question may run after a failed ingest"
        assert [e[0] for e in memory.events] == ["ingest"]

    def test_partial_ingest_is_refused(self):
        memory = RecordingMemory(short_ingest=True)
        with pytest.raises(ExecutorUnavailable, match="partial ingest"):
            _run(ITEMS, memory, ScriptedCaller(["Answer: x"] * 3))

    def test_recall_failure_is_unavailable_and_not_a_score(self):
        memory = RecordingMemory(fail_recall=True)
        caller = ScriptedCaller(["Answer: x"] * 3)
        with pytest.raises(ExecutorUnavailable, match="recall failed"):
            _run(ITEMS, memory, caller)
        # The pool WAS ingested; the run still refuses rather than scoring a
        # subset of the questions it happened to answer.
        assert [e[0] for e in memory.events] == ["ingest", "recall"]

    def test_all_empty_retrieval_is_refused_not_labelled_tortoise(self):
        """An all-empty retrieval is a no-context baseline; reporting it under
        the Tortoise label would be indistinguishable from a real result."""
        memory = RecordingMemory(returns=[])
        caller = ScriptedCaller(["Answer: x"] * 3)
        with pytest.raises(ExecutorUnavailable, match="no-context baseline"):
            _run(ITEMS, memory, caller)
        assert caller.prompts, "the caller ran; the lane still refuses to score"

    def test_partial_retrieval_miss_is_scored_and_visible(self):
        """A per-question miss is a real product outcome (scored wrong), and
        the miss count is visible in detail — never silently swallowed."""
        class _Mixed(RecordingMemory):
            def recall(self, question, k):
                if question == ITEMS[1].question:
                    self.events.append(("recall", question))
                    return []
                return super().recall(question, k)

        memory = _Mixed(returns=[POOL_UNITS[0]])
        cell, _ = _run(ITEMS[:2], memory, ScriptedCaller(["Answer: Leeds",
                                                          "wrong"]))
        assert cell.samples == 2
        assert cell.detail["empty_retrievals"] == 1

    def test_empty_pool_is_refused(self):
        with pytest.raises(ExecutorUnavailable, match="pool is empty"):
            _run(ITEMS, RecordingMemory(), ScriptedCaller([]), context="")

    def test_duplicate_ids_refuse_rather_than_collapse(self):
        with pytest.raises(ValueError, match="duplicate qa_pair_id"):
            _run((ITEMS[0], ITEMS[0]), RecordingMemory(),
                 ScriptedCaller(["Answer: x"]))


class TestFakeMemory:
    """The mock lane's in-memory memory is deterministic and bounded by k."""

    def test_ingest_then_lexical_recall(self):
        mem = _FakeCrMemory()
        assert mem.ingest(list(POOL_UNITS)) == len(POOL_UNITS)
        hits = mem.recall("Where was Thomas Kyd born?", 2)
        assert len(hits) == 2
        assert all("Thomas Kyd" in h for h in hits), hits

    def test_recall_is_bounded_by_k(self):
        mem = _FakeCrMemory(list(POOL_UNITS))
        assert mem.recall("Thomas Kyd", 1) == [POOL_UNITS[0]]
        assert mem.recall("Thomas Kyd", 0) == []


class TestRegisteredExecutor:
    """`battery parity --execute` reaches the Tortoise lane through the
    registry, under a benchmark key of its own."""

    def test_registry_contains_the_tortoise_lane(self):
        assert "memoryagentbench_tortoise" in EXECUTORS

    def test_mock_executor_is_hermetic_and_labelled(self, monkeypatch,
                                                     tmp_path):
        import battery.parity.mabench as mabench_mod

        monkeypatch.setattr(mabench_mod, "load_cr", lambda config, path=None:
                            CrConfig(config=config, context=POOL, items=ITEMS))
        cell = EXECUTORS["memoryagentbench_tortoise"](
            mock=True, limit=2, out_dir=tmp_path)
        assert cell.lane == "mock_tortoise"
        assert cell.samples == 2, "two questions asked, two scored"
        assert cell.accuracy == 0.0, "the mock reader answers nothing useful"
        assert cell.revision.startswith("memoryagentbench-cr@")
        assert cell.detail["ingested"] == len(POOL_UNITS)

    def test_unavailable_dataset_fails_closed(self, monkeypatch, tmp_path):
        import battery.parity.mabench as mabench_mod

        def _boom(config, path=None):
            raise mabench_mod.PyarrowUnavailable("install the parity extra")

        monkeypatch.setattr(mabench_mod, "load_cr", _boom)
        with pytest.raises(ExecutorUnavailable, match="parity extra"):
            EXECUTORS["memoryagentbench_tortoise"](mock=True, limit=1,
                                                   out_dir=tmp_path)

    def test_real_lane_without_a_key_fails_closed(self, monkeypatch, tmp_path):
        """The real lane must NEVER silently fall back to the mock lane."""
        import battery.parity.mabench as mabench_mod

        monkeypatch.setattr(mabench_mod, "load_cr", lambda config, path=None:
                            CrConfig(config=config, context=POOL, items=ITEMS))

        class _NoKey:
            def __init__(self):
                raise RuntimeError("OPENROUTER_API_KEY is not set")

        monkeypatch.setattr("battery.runner.model_calls.RealModelCaller",
                            _NoKey)
        with pytest.raises(ExecutorUnavailable, match="real reader unavailable"):
            EXECUTORS["memoryagentbench_tortoise"](mock=False, limit=1,
                                                   out_dir=tmp_path)

    def test_cell_lane_vocabulary_still_refuses_unknown_labels(self):
        with pytest.raises(ValueError, match="must be"):
            ExecutedCell(benchmark="memoryagentbench", accuracy=0.5, samples=1,
                         revision="d@s", lane="production")


class TestLaneMatrix:
    """The new module is a runtime store surface: it must hold ZERO raw
    Cypher (the lane-matrix contract), exactly like the A4 arm."""

    _FORBIDDEN = ("FalkorProjection", ".query(", "MERGE", "MATCH", "UNWIND",
                  "g.query")

    def test_no_raw_cypher_tokens(self):
        path = (Path(__file__).resolve().parent.parent
                / "battery" / "parity" / "mabench_tortoise.py")
        source = path.read_text()
        tree = ast.parse(source)
        offenders: list[str] = []
        for node in ast.walk(tree):
            for attr in ("id", "attr"):
                name = getattr(node, attr, None)
                if isinstance(name, str) and any(
                        tok in name for tok in self._FORBIDDEN):
                    offenders.append(name)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for tok in self._FORBIDDEN:
                    if tok in node.value:
                        offenders.append(f"string:{tok}")
        assert not offenders, (
            "raw Cypher leaked into the Tortoise CR lane (lane-matrix "
            f"violation): {offenders}")


@pytest.mark.skipif(
    not os.environ.get("BATTERY_PARITY_TORTOISE_E2E"),
    reason="opt-in: stands up a real embedded Tortoise store (~20s) — set "
           "BATTERY_PARITY_TORTOISE_E2E=1")
def test_real_tortoise_memory_roundtrip(tmp_path):
    """The REAL arm memory against an embedded store (no keys, no network):
    ingest writes points and recall reads them back through ``recall_state``.
    Opt-in because the embedded store is slow to stand up in CI."""
    from battery.parity.mabench_tortoise import TortoiseCrMemory

    mem = TortoiseCrMemory(db_path=str(tmp_path / "cr.db"))
    try:
        written = mem.ingest(list(POOL_UNITS))
        assert written == len(POOL_UNITS), "every fact unit was written"
        hits = mem.recall("Where was Thomas Kyd born?", 5)
        assert any("Thomas Kyd" in h for h in hits), hits
    finally:
        mem.close()
