"""MemoryAgentBench Conflict Resolution — the TORTOISE arm lane (#2800).

The full-context lane (`mabench_run.run_cr_lane`) injects the WHOLE knowledge
pool and measures the reader. This module runs the SAME benchmark, with the
SAME prompts and the SAME metric, but the reader sees only what the Tortoise
arm RETRIEVED for the question — so the two rows are commensurable and the
difference between them is attributable to retrieval, not to a rewritten task.

The lane contract (what makes the two rows comparable):

1. **Ingest once, before any question.** The knowledge pool is split into fact
   units and every unit is written through the product WRITE path
   (``memory.ingest`` → ``TortoiseSDK.create_point``; never raw Cypher, never a
   direct FalkorDB call — the lane-matrix rule in
   ``docs/epics/1402-eval-battery/lane-matrix.md``). A partial ingest is a
   refusal, never a number.
2. **Retrieve per question.** ``memory.recall(question, k)`` reads through the
   product state surface (``TortoiseSDK.recall_state``) — but NOT byte-for-byte
   what the A4 arm does: A4 reads with the product default
   ``object_centric=True`` (``a4_tortoise.py``), while this lane passes
   ``object_centric=False`` explicitly (see ``_TortoiseMemory.recall``) because
   the CR pool is a flat list of facts with no object structure to pivot on and
   the object-centric read returned materially less. That is a deliberate,
   documented deviation from "the product as A4 configures it" and it is a
   retrieval-quality knob, NOT a benchmark-tuned one: it was chosen against the
   benchmark's own pool shape and never tuned against its scores.
3. **Ask with the benchmark's OWN templates.** The per-question prompt is
   ``mabench_run.build_lane_prompt(retrieved_context, question)`` — the
   benchmark's system message + memorize turn + conflict-rule query template,
   verbatim. The ONLY difference from the baseline row is the context string:
   retrieved fact units instead of the full pool.
4. **Score with the benchmark's OWN metric.** ``mabench.score_cr`` — the same
   ``substring_exact_match`` normalization — so an accuracy here is directly
   comparable to the published long-context baseline.
5. **Say which lane it is.** The cell's ``lane`` is ``real_tortoise`` /
   ``mock_tortoise`` (never ``real``/``mock``), so a Tortoise number can never
   be mistaken for the full-context baseline it is compared against.

Fact-unit split (and why): the pinned CR ``context`` is a header line followed
by numbered lines (``0. Thomas Kyd was born in the city of London.``). One
unit = one non-blank, non-header line, **serial number included**. The CR
conflict rule is "the newer fact has the larger serial number" — stripping the
serial would delete the only signal the reader has for resolving a conflict
between two otherwise-identical facts, so the unit is the line as published.

Deliberate non-goals: this module does not re-tune retrieval, does not re-rank,
and does not invent a metric. It is the measurement seam, and nothing else.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Protocol

from battery.parity.executors import ExecutedCell, ExecutorUnavailable
from battery.parity.mabench import CR_SHA256, CrItem, MabenchError, score_cr

# Cost metering is the #2906/#2874 single source; imported rather than copied
# so the Tortoise row is priced by exactly the same basis as the baseline row.
from battery.parity.mabench_run import (
    CrRun,
    _call_cost_and_basis,
    build_lane_prompt,
)
from battery.runner.model_calls import aggregate_cost_basis

#: The two Tortoise lane labels — deliberately distinct from the baseline
#: lane's ``real``/``mock`` so the rows can never be confused.
LANE_REAL = "real_tortoise"
LANE_MOCK = "mock_tortoise"
TORTOISE_LANES: tuple[str, ...] = (LANE_REAL, LANE_MOCK)

#: Default retrieval depth. ``20`` is the A4 arm's own declared state-read
#: depth (``recall_state(..., limit=20)`` in ``battery/arms/a4_tortoise.py``):
#: using the arm's depth keeps this lane a read of the product as configured,
#: not a knob tuned for the benchmark. On the pinned 6K pool (455 fact units)
#: it is ~4% of the pool — genuinely retrieved context, not the full pool.
DEFAULT_K = 20

#: The A4 arm's fact kind: ``evidence`` is a decision-part kind, so a point
#: created without an explicit status is born LIVE with a stated starting
#: belief (``medium`` = Beta(3,1)) — live points are what ``recall_state``
#: reads. A plain ``statement`` would land DRAFT and be invisible to the read
#: surface, silently turning the lane into a no-context baseline.
_FACT_KIND = "evidence"
_FACT_CREDIBILITY = "medium"

#: The publisher's pool header (the first line of ``context``) — not a fact.
_POOL_HEADER_RE = re.compile(r"^here is a list of facts:?$", re.IGNORECASE)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class CrMemory(Protocol):
    """The injected memory seam: a write path and a read path.

    Both are the ONLY ways this lane touches memory, so a fake can be injected
    in tests and the real implementation can be swapped without the lane
    knowing. Implementations MUST go through the product SDK verbs (the arm's
    seam) — never raw Cypher, never a direct FalkorDB call.
    """

    def ingest(self, texts: list[str]) -> int:
        """Write each fact unit as a point; return the number written."""
        ...

    def recall(self, question: str, k: int) -> list[str]:
        """Return up to ``k`` retrieved fact texts for ``question``."""
        ...


def split_fact_units(context: str) -> list[str]:
    """Split the pinned pool into fact units (blank lines and the publisher's
    header dropped, everything else kept verbatim — serial numbers included).

    Lossless by construction: no non-blank line is ever silently discarded, so
    the ingest count the lane reports is the count of facts in the pool. An
    empty result means there was no pool to read, which the lane refuses.
    """
    units: list[str] = []
    for raw in (context or "").splitlines():
        line = raw.strip()
        if not line or _POOL_HEADER_RE.match(line):
            continue
        units.append(line)
    return units


class TortoiseCrMemory:
    """The REAL arm memory: product verbs over an embedded, per-run store.

    Hermetic by construction — an explicit ``db_path`` means the store is a
    private embedded graph (``TORTOISE_DB_URI`` is ignored), so a lane run
    cannot touch a shared/CI graph. One handle, ingest-then-recall, close at
    the end.

    Write: ``create_point`` (the A4 arm's fact verb) with ``dedup=True`` so a
    re-run is content-hash idempotent. Read: ``recall_state`` with
    ``object_centric=False`` so the returned pool is points only (the arm's
    own read surface, minus the object rows this lane has no text for).
    """

    def __init__(self, *, db_path: str | None = None,
                 graph_name: str = "mabench_cr",
                 source_session: str = "mabench-cr") -> None:
        from tortoise.sdk import TortoiseSDK
        if not db_path:
            db_path = str(
                Path(tempfile.mkdtemp(prefix="mabench_tortoise_")) / "cr.db")
        self._db_path = db_path
        self._session = source_session
        ev_dir = Path(db_path).parent / "events"
        self._sdk = TortoiseSDK(
            db_path=db_path,
            graph_name=graph_name,
            event_log_path=str(ev_dir / f"{graph_name}.jsonl"),
        )

    def ingest(self, texts: list[str]) -> int:
        """Write every fact unit as a live point; return the number written.

        A create that comes back without an id raises — the lane must never
        report a partial ingest as if the pool were in the store.
        """
        written = 0
        for text in texts:
            created = self._sdk.create_point(
                kind=_FACT_KIND, content=text, dedup=True,
                credibility=_FACT_CREDIBILITY,
                source_harness="battery-parity", source_session=self._session)
            pid = created.get("id") if isinstance(created, dict) else None
            if not pid:
                raise RuntimeError(
                    f"tortoise ingest: create_point returned no id for "
                    f"{text[:60]!r}")
            written += 1
        return written

    def recall(self, question: str, k: int) -> list[str]:
        """The product state read, filtered to point rows with text."""
        rows = self._sdk.recall_state(
            query=question, kind=None, limit=k, object_centric=False)
        out: list[str] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            if row.get("entity_type") != "point" or row.get("is_operator"):
                continue
            content = str(row.get("content") or "")
            if not content or content.startswith("[MITIGATION]"):
                continue
            out.append(content)
        return out[:k]

    def close(self) -> None:
        self._sdk.close()


class _FakeCrMemory:
    """Deterministic in-memory :class:`CrMemory` for the mock lane.

    No store, no keys, no spend — its job is to exercise the lane path
    (ingest → retrieve → benchmark prompt → official metric → labelled cell)
    in CI. Ranking is lexical token overlap, stable-ordered by first
    occurrence, so the mock lane is reproducible.
    """

    def __init__(self, facts: list[str] | None = None) -> None:
        self.facts: list[str] = list(facts or [])

    def ingest(self, texts: list[str]) -> int:
        self.facts.extend(texts)
        return len(texts)

    def recall(self, question: str, k: int) -> list[str]:
        if k <= 0:
            return []
        want = set(_TOKEN_RE.findall(question.lower()))
        scored = [(len(want & set(_TOKEN_RE.findall(f.lower()))), i, f)
                  for i, f in enumerate(self.facts)]
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [f for _, _, f in scored[:k]]


def run_cr_tortoise_lane(
    items: tuple[CrItem, ...],
    memory: CrMemory,
    caller,
    *,
    lane: str,
    config: str,
    context: str,
    limit: int | None = None,
    k: int = DEFAULT_K,
) -> tuple[ExecutedCell, CrRun]:
    """Ingest the pool once, retrieve per question, score with the official
    metric — the Tortoise row of the CR lane.

    ``context`` is the pinned configuration's knowledge pool (the baseline
    lane's ``context`` argument — same source, same bytes). ``caller`` only
    has to satisfy the one-method reader protocol the baseline uses
    (``call(prompt=...)``), so the lane inherits the same spend metering.

    Fail-closed: an ingest that writes fewer facts than it was given, or a
    recall that raises, aborts the whole lane (``ExecutorUnavailable``) rather
    than scoring a run over a self-selected subset. ``limit is not None`` — a
    ``limit=0`` run asks NOTHING and refuses before it writes anything.

    Returns the labelled ``ExecutedCell`` plus the ``CrRun`` detail, exactly
    like ``run_cr_lane``.
    """
    if lane not in TORTOISE_LANES:
        raise ValueError(
            f"tortoise lane must be one of {TORTOISE_LANES}, got {lane!r} — a "
            f"retrieved-context number must never wear the full-context "
            f"baseline's lane label")
    if k < 1:
        # k=0 would retrieve nothing and hand the reader an empty context —
        # a no-context baseline mislabelled as the Tortoise lane.
        raise ValueError(f"retrieval depth k must be >= 1, got {k!r}")
    if limit is not None and limit < 0:
        # A negative slice would silently drop the corpus's tail while the
        # lane still reported a full-pool ingest.
        raise ValueError(f"limit must be >= 0, got {limit!r}")

    # `limit is not None` (not truthiness): --limit 0 must ask NOTHING. Checked
    # BEFORE ingest, because ingesting the whole pool for a run that will score
    # nothing is an unbounded write with no consumer.
    chosen = items[:limit] if limit is not None else items
    if not chosen:
        raise MabenchError(
            "no items to score — refusing an empty measurement (limit=0 asks "
            "nothing and the pool is not ingested)")

    ids = [i.qa_pair_id for i in chosen]
    if len(set(ids)) != len(ids):
        raise ValueError(
            "duplicate qa_pair_id in the CR items — two questions would share "
            "one answer slot and the score would be silently wrong")

    # 1. Ingest ONCE, before any question.
    units = split_fact_units(context)
    if not units:
        raise ExecutorUnavailable(
            "memoryagentbench_tortoise: the knowledge pool is empty — "
            "refusing to run a reader with nothing to retrieve")
    try:
        ingested = memory.ingest(units)
    except Exception as e:
        raise ExecutorUnavailable(
            f"memoryagentbench_tortoise: ingest failed "
            f"({type(e).__name__}: {e})") from e
    if not isinstance(ingested, int) or ingested != len(units):
        raise ExecutorUnavailable(
            f"memoryagentbench_tortoise: ingest wrote {ingested!r} of "
            f"{len(units)} fact units — refusing a partial ingest (a lane that "
            f"scored with facts missing would understate retrieval)")

    # 2-3. Retrieve per question, then ask with the benchmark's OWN template.
    outputs: dict[str, str] = {}
    bases: list[str] = []
    calls = 0
    spend = 0.0
    retrieved_chars = 0
    empty_retrievals = 0
    for item in chosen:
        try:
            facts = memory.recall(item.question, k)
        except Exception as e:
            raise ExecutorUnavailable(
                f"memoryagentbench_tortoise: recall failed for "
                f"{item.qa_pair_id} ({type(e).__name__}: {e})") from e
        texts = [str(f) for f in (facts or []) if str(f).strip()]
        if not texts:
            empty_retrievals += 1
        retrieved = "\n".join(texts)
        retrieved_chars += len(retrieved)
        prompt = build_lane_prompt(retrieved, item.question)
        outputs[item.qa_pair_id] = caller.call(prompt=prompt)
        calls += 1
        pt = int(getattr(caller, "last_prompt_tokens", 0) or 0)
        ct = int(getattr(caller, "last_completion_tokens", 0) or 0)
        call_cost, basis = _call_cost_and_basis(caller, pt, ct)
        spend += call_cost
        bases.append(basis)

    # A per-question retrieval miss is a real PRODUCT outcome and is scored
    # (the reader answers from the query template alone, almost certainly
    # wrong) — that is informative, not a fabricated number. But a run in
    # which NOTHING was retrieved has no retrieved context anywhere: it is a
    # no-context baseline, and reporting it under the Tortoise label would be
    # indistinguishable from a real Tortoise result. Refuse it, and expose
    # the per-question miss count in `detail` so a partial miss stays visible.
    if calls and empty_retrievals == len(chosen):
        raise ExecutorUnavailable(
            f"memoryagentbench_tortoise: retrieved no facts for ANY of the "
            f"{len(chosen)} questions — the lane degenerated to a no-context "
            f"baseline; refusing to label that a Tortoise measurement")

    # The benchmark's own metric. `score_cr` returns (accuracy, len(chosen))
    # by contract, and the loop above asks exactly one question per chosen
    # item — but assert the per-item output IS present, so a future refactor
    # that skipped an item can never be scored over a self-selected subset.
    missing = [i.qa_pair_id for i in chosen if i.qa_pair_id not in outputs]
    if missing:
        raise ExecutorUnavailable(
            f"memoryagentbench_tortoise: no answer recorded for "
            f"{missing[:3]} — refusing a score over unattempted questions")
    accuracy, samples = score_cr(outputs, chosen)
    cost_basis = aggregate_cost_basis(bases)

    cell = ExecutedCell(
        benchmark="memoryagentbench",
        accuracy=accuracy,
        samples=samples,
        revision=f"memoryagentbench-cr@{CR_SHA256[:16]}",
        lane=lane,
        detail={
            "config": config,
            "calls": calls,
            "spend_usd": spend,
            "cost_basis": cost_basis,
            "ingested": ingested,
            "k": k,
            "retrieved_chars": retrieved_chars,
            "empty_retrievals": empty_retrievals,
        },
    )
    run = CrRun(accuracy=accuracy, samples=samples, calls=calls,
                spend_usd=spend, config=config, cost_basis=cost_basis)
    return cell, run
