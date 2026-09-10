"""#2800 — the MemoryAgentBench CR run loop (full-context lane).

Two things decide whether a CR number means anything: the PROMPT must be the
benchmark's own (published numbers exist only under its prompt), and the
SAMPLE COUNT must be the questions actually asked and scored — never the
corpus size, never a self-selected subset of the ones that worked.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.parity.mabench import CR_SHA256, CrItem
from battery.parity.mabench_run import (
    QUERY_TEMPLATE,
    SYSTEM_MESSAGE,
    answer_items,
    build_lane_prompt,
    build_memorize_prompt,
    build_query_prompt,
    run_cr_lane,
)

CTX = ("Here is a list of facts: 0. The chairperson of Fatah is Mahmoud "
       "Abbas. 1. Amy Winehouse died in the city of Camden Town.")

ITEMS = (
    CrItem(qa_pair_id="q1", config="c", question="Who chairs Fatah?",
           accepted=("Mahmoud Abbas",)),
    CrItem(qa_pair_id="q2", config="c", question="Where did Amy Winehouse die?",
           accepted=("Camden Town",)),
    CrItem(qa_pair_id="q3", config="c", question="Which continent is Bengaluru in?",
           accepted=("Asia",)),
)


class ScriptedCaller:
    """Deterministic reader: returns a scripted answer per call."""

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


class TestPromptFidelity:
    """The lane is only comparable if the prompt is the benchmark's own."""

    def test_system_message_is_the_benchmarks(self):
        assert SYSTEM_MESSAGE == (
            "You are a helpful assistant that can read the context and "
            "memorize it for future retrieval.")

    def test_query_template_carries_the_conflict_rule(self):
        # the CR family's rule: the NEWEST fact has the LARGEST serial number
        assert "newer fact has larger serial number" in QUERY_TEMPLATE
        assert "serial number" in QUERY_TEMPLATE
        assert "{question}" in QUERY_TEMPLATE

    def test_query_prompt_substitutes_the_question(self):
        p = build_query_prompt("Who chairs Fatah?")
        assert "Who chairs Fatah?" in p and "{question}" not in p

    def test_memorize_prompt_carries_the_context_verbatim(self):
        ctx = "Here is a list of facts: 0. A. 1. B."
        p = build_memorize_prompt(ctx)
        assert ctx in p and "{context}" not in p
        assert "{time_stamp}" not in p


class TestRunLoop:
    def test_every_item_is_asked_and_scored(self):
        caller = ScriptedCaller(["Answer: Mahmoud Abbas",
                                 "Answer: Camden Town", "Answer: Asia"])
        outputs, calls, cost = answer_items(ITEMS, caller, context=CTX)
        assert calls == 3 and len(outputs) == 3
        assert cost == 0.0, "a mock reader costs nothing"

    def test_limit_bounds_calls_and_the_sample_count(self):
        caller = ScriptedCaller(["Answer: Mahmoud Abbas"])
        cell, run = run_cr_lane(ITEMS, caller, lane="mock", config="c",
                                context=CTX, limit=1)
        assert run.calls == 1
        assert cell.samples == 1, "the sample count is what was asked"
        assert cell.accuracy == 1.0

    def test_accuracy_uses_the_official_metric(self):
        caller = ScriptedCaller(["Answer: Mahmoud Abbas", "wrong", "Answer: Asia"])
        cell, run = run_cr_lane(ITEMS, caller, lane="mock", config="c",
                                context=CTX)
        assert cell.accuracy == pytest.approx(2 / 3)
        assert cell.samples == 3 and run.calls == 3

    def test_cell_identity_is_the_dataset_digest(self):
        caller = ScriptedCaller(["a", "b", "c"])
        cell, _ = run_cr_lane(ITEMS, caller, lane="mock", config="fact_sh_6k",
                              context=CTX)
        assert cell.revision == f"memoryagentbench-cr@{CR_SHA256[:16]}"
        assert cell.lane == "mock"
        assert cell.detail["config"] == "fact_sh_6k"

    def test_reader_failure_propagates_instead_of_shrinking_the_sample(self):
        """A lane that skipped the questions it could not answer would report
        an accuracy over a self-selected subset."""
        caller = ScriptedCaller(["a", "b"], fail_on=2)
        with pytest.raises(RuntimeError, match="reader unavailable"):
            answer_items(ITEMS, caller, context=CTX)

    def test_token_reporting_produces_a_metered_cost(self):
        caller = ScriptedCaller(["Answer: x"] * 3,
                                prompt_tokens=1000, completion_tokens=100)
        _, calls, cost = answer_items(ITEMS, caller, context=CTX)
        assert calls == 3
        expected = 3 * (1000 * 0.27 + 100 * 1.10) / 1_000_000
        assert cost == pytest.approx(expected)

    def test_the_knowledge_pool_is_IN_the_prompt(self):
        """The lane is full-context: without the pool in the prompt the reader
        has nothing to answer from and the lane silently becomes a no-context
        baseline."""
        caller = ScriptedCaller(["Answer: x"])
        answer_items(ITEMS[:1], caller, context=CTX)
        assert CTX in caller.prompts[0]
        assert "facts I have learned" in caller.prompts[0]

    def test_lane_prompt_layout(self):
        p = build_lane_prompt(CTX, "Who chairs Fatah?")
        assert p.index(SYSTEM_MESSAGE) < p.index(CTX) < p.index("Who chairs Fatah?")
        assert "serial number" in p, "the conflict rule must survive the layout"

    def test_prompt_carries_system_message_and_question(self):
        caller = ScriptedCaller(["Answer: x"])
        answer_items(ITEMS[:1], caller, context=CTX)
        assert SYSTEM_MESSAGE in caller.prompts[0]
        assert "Who chairs Fatah?" in caller.prompts[0]


class TestRegisteredExecutor:
    """`battery parity --execute` reaches the CR lane through the registry."""

    def test_executor_refuses_when_the_dataset_is_unavailable(self, monkeypatch,
                                                              tmp_path):
        import battery.parity.executors as ex
        import battery.parity.mabench as mabench_mod
        from battery.parity.executors import ExecutorUnavailable

        def _boom(config, path=None):
            raise mabench_mod.PyarrowUnavailable(
                "MemoryAgentBench CR needs the parquet reader: install the "
                "parity extra")

        monkeypatch.setattr(mabench_mod, "load_cr", _boom)
        with pytest.raises(ExecutorUnavailable, match="parity extra"):
            ex.memoryagentbench_executor(mock=True, limit=1, out_dir=tmp_path)

    def test_mock_lane_produces_a_labelled_cell(self, monkeypatch, tmp_path):
        """The mock lane must be a real end-to-end run of OUR path, and must
        be labelled so it cannot read as a comparable measurement."""
        import battery.parity.executors as ex
        import battery.parity.mabench as mabench_mod

        monkeypatch.setattr(mabench_mod, "load_cr", lambda config, path=None:
                            mabench_mod.CrConfig(config=config, context=CTX,
                                                 items=ITEMS))
        cell = ex.memoryagentbench_executor(mock=True, limit=2, out_dir=tmp_path)
        assert cell.lane == "mock"
        assert cell.samples == 2, "two questions asked, two scored"
        assert cell.accuracy == 0.0, "the mock reader answers nothing useful"
        assert cell.revision.startswith("memoryagentbench-cr@")


class TestReviewP2s:
    """Fixes from the #2861 review."""

    def test_limit_zero_asks_nothing(self):
        """`--limit 0` must mean zero questions — never 'unlimited' (on the
        real lane that is an unbounded spend)."""
        caller = ScriptedCaller([])
        outputs, calls, _ = answer_items(ITEMS, caller, context=CTX, limit=0)
        assert calls == 0 and outputs == {}

    def test_duplicate_ids_refuse_rather_than_collapse(self):
        dup = (ITEMS[0], ITEMS[0])
        with pytest.raises(ValueError, match="duplicate qa_pair_id"):
            answer_items(dup, ScriptedCaller(["a"]), context=CTX)

    def test_midrun_reader_failure_becomes_not_measured(self, monkeypatch,
                                                        tmp_path):
        """A failed run must not abort the whole parity leg: the cell is
        recorded as not-measured with the reason."""
        import battery.parity.executors as ex
        import battery.parity.mabench as mabench_mod
        from battery.parity.executors import ExecutorUnavailable

        monkeypatch.setattr(mabench_mod, "load_cr", lambda config, path=None:
                            mabench_mod.CrConfig(config=config, context=CTX,
                                                 items=ITEMS))
        monkeypatch.setattr(ex, "_MockReader", lambda: ScriptedCaller(
            [], fail_on=1))
        with pytest.raises(ExecutorUnavailable, match="run failed"):
            ex.memoryagentbench_executor(mock=True, limit=2, out_dir=tmp_path)
