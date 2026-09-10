"""MemoryAgentBench CR — the full-context run loop (#2800).

Slice 2 gave us the pinned dataset and the benchmark's own metric. This slice
runs a LANE over it and produces a measurable cell.

Which lane, and why this one first: the benchmark publishes a
**long-context baseline** ("inject the facts once, then ask"), scored with the
same metric, with published numbers to compare against (agent ceiling ~6%;
GPT-4o 28% on multi-hop @6K). Running that lane first proves the whole path —
prompt → answers → official metric → cell — and gives a number that is
directly comparable. The Tortoise (graph) lane is a later slice; it must be
scored by this same module so the two are commensurable.

Prompt fidelity matters as much as metric fidelity: identical prompts are why
published numbers mean anything. The strings below are the benchmark's own
(`utils/templates.py`, `factconsolidation`) — `SYSTEM_MESSAGE`, the
`memorize` turn that injects the knowledge pool, and the
`long_context_agent` query template that states the conflict rule (the newer
fact has the larger serial number). They are copied verbatim, with the
citation, and a test pins their content so an edit cannot silently change
what the lane measures.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from battery.parity.executors import ExecutedCell
from battery.parity.mabench import CR_SHA256, CrItem, score_cr

#: Verbatim from MemoryAgentBench `utils/templates.py::SYSTEM_MESSAGE`.
SYSTEM_MESSAGE = ("You are a helpful assistant that can read the context and "
                  "memorize it for future retrieval.")

#: Verbatim from `utils/templates.py` (`factconsolidation.memorize`): the
#: single turn that injects the knowledge pool.
MEMORIZE_TEMPLATE = (
    "Dialogue between User and Assistant {time_stamp} \\n<User> The following "
    "context is the facts I have learned: \n{context}\n <Assistant> I have "
    "learned the facts and I will answer the question you ask.")

#: Verbatim from `utils/templates.py`
#: (`factconsolidation.query.long_context_agent`) — the conflict rule the CR
#: family is testing: the newest fact carries the largest serial number.
QUERY_TEMPLATE = (
    "Pretend you are a knowledge management system. Each fact in the knowledge "
    "pool is provided with a serial number at the beginning, and the newer "
    "fact has larger serial number. \n You need to solve the conflicts of "
    "facts in the knowledge pool by finding the newest fact with larger "
    "serial number. You need to answer a question based on this rule. You "
    "should give a very concise answer without saying other words for the "
    "question **only** from the knowledge pool you have memorized rather than "
    "the real facts in real world. \n\nFor example:\n\n [Knowledge Pool] \n\n "
    "Question: Based on the provided Knowledge Pool, what is the name of the "
    "current president of Russia? \nAnswer: Donald Trump \n\n Now Answer the "
    "Question: Based on the provided Knowledge Pool, {question} \nAnswer:")


class ReaderCaller(Protocol):
    """Anything that can answer a prompt (``battery.runner.model_calls``
    callers satisfy this, so the lane inherits their spend metering)."""

    def call(self, *, prompt: str) -> str: ...


@dataclass(frozen=True)
class CrRun:
    """The outcome of a CR lane run: the official metric + what it cost."""

    accuracy: float
    samples: int
    calls: int
    spend_usd: float
    config: str


def build_memorize_prompt(context: str, *, time_stamp: str = "") -> str:
    """The injection turn: the knowledge pool (the fact list) as one message."""
    return MEMORIZE_TEMPLATE.format(time_stamp=time_stamp, context=context)


def build_query_prompt(question: str) -> str:
    """The per-question turn, using the benchmark's own CR instruction."""
    return QUERY_TEMPLATE.format(question=question)


def build_lane_prompt(context: str, question: str, *,
                      system_message: str = SYSTEM_MESSAGE,
                      time_stamp: str = "") -> str:
    """The benchmark's own multi-turn setup, flattened into ONE prompt.

    The published lane injects the knowledge pool once (the ``memorize``
    turn) and then asks each question in the resulting conversation. Our
    caller takes a single prompt, so the injection turn is carried in every
    request — the same knowledge pool, the same query template, no extra
    instruction of our own. Omitting the pool would leave the reader with
    nothing to answer FROM and silently turn the lane into a no-context
    baseline.
    """
    return (f"{system_message}\n\n"
            f"{build_memorize_prompt(context, time_stamp=time_stamp)}\n\n"
            f"{build_query_prompt(question)}")


def answer_items(items: tuple[CrItem, ...], caller: ReaderCaller, *,
                 context: str, limit: int | None = None,
                 system_message: str = SYSTEM_MESSAGE
                 ) -> tuple[dict[str, str], int, float]:
    """Ask the reader every item's question; return (raw outputs, calls, cost).

    ``limit`` caps the item count (a bounded, cheaper lane) and the return
    count reflects what was actually asked — the caller's sample count is the
    number of real answers, never the size of the corpus.

    A failing call is NOT silently dropped: the exception propagates, because a
    lane that quietly skips the questions it could not answer would report an
    accuracy over a self-selected subset.
    """
    # `limit is not None` (not truthiness): --limit 0 must ask NOTHING, never
    # silently run the whole corpus on a real (paid) lane.
    chosen = items[:limit] if limit is not None else items
    ids = [i.qa_pair_id for i in chosen]
    if len(set(ids)) != len(ids):
        raise ValueError(
            "duplicate qa_pair_id in the CR items — two questions would share "
            "one answer slot and the score would be silently wrong")
    outputs: dict[str, str] = {}
    cost = 0.0
    calls = 0
    for item in chosen:
        prompt = build_lane_prompt(context, item.question,
                                   system_message=system_message)
        outputs[item.qa_pair_id] = caller.call(prompt=prompt)
        calls += 1
        pt = int(getattr(caller, "last_prompt_tokens", 0) or 0)
        ct = int(getattr(caller, "last_completion_tokens", 0) or 0)
        cost += _call_cost(caller, pt, ct)
    return outputs, calls, cost


def _call_cost(caller: ReaderCaller, prompt_tokens: int,
               completion_tokens: int) -> float:
    """Per-call spend: derived from tokens when the caller reports them,
    else whatever the caller itself metered (0.0 for a mock)."""
    if prompt_tokens or completion_tokens:
        return _cost(prompt_tokens, completion_tokens)
    return float(getattr(caller, "cost_usd", 0.0) or 0.0)


#: Mirror of battery/runner/model_calls.py's pinned price basis (deepseek-v4-
#: flash): ONE price basis across the battery's spend meters.
_RATES_PER_1M_USD: tuple[float, float] = (0.27, 1.10)


def _cost(prompt_tokens: int, completion_tokens: int) -> float:
    p_in, p_out = _RATES_PER_1M_USD
    return (prompt_tokens * p_in + completion_tokens * p_out) / 1_000_000.0


def run_cr_lane(items: tuple[CrItem, ...], caller: ReaderCaller, *,
                lane: str, config: str, context: str, limit: int | None = None
                ) -> tuple[ExecutedCell, CrRun]:
    """Answer the items and score them with the benchmark's own metric.

    Returns the ``ExecutedCell`` the parity leg records (accuracy, samples,
    revision = the dataset digest, lane) plus the run detail. The revision is
    the DATASET DIGEST, not the runner's self-reported name, so the cell's
    identity is checkable against the bytes that were scored.
    """
    outputs, calls, spend = answer_items(items, caller, context=context,
                                         limit=limit)
    scored_items = items[:limit] if limit is not None else items
    accuracy, samples = score_cr(outputs, scored_items)
    cell = ExecutedCell(
        benchmark="memoryagentbench",
        accuracy=accuracy,
        samples=samples,
        revision=f"memoryagentbench-cr@{CR_SHA256[:16]}",
        lane=lane,
        detail={"config": config, "calls": calls, "spend_usd": spend},
    )
    return cell, CrRun(accuracy=accuracy, samples=samples, calls=calls,
                       spend_usd=spend, config=config)
