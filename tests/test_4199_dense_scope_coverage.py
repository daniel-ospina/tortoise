"""#4199 — the dense leg's health is judged against the READ's own scope.

THE DEFECT (reproduced on this branch before the fix — see ``## Proof`` below)

The read path could report **hybrid** (``declared_degraded_read() is None``)
while the dense (vector) leg contributed **zero material to the read's own
kind scope**. The measured production state is exactly this: captured turn
Points (``pointKind='event'``) are un-embedded pre-#4194, while extracted
Points ARE embedded. A turn-scoped read then:

* ran the vector leg over the WHOLE ``:Point`` label (no kind filter — that is
  the read path's design), so the unscoped query returned the extracted Points;
* recorded that run ``ran=True, degraded=False, reason="ok"`` — the label-wide
  view sees embeddings, so nothing looked wrong;
* dropped every one of those rows at the read's kind filter, leaving a
  keyword-only answer that was still DECLARED hybrid.

The zero-row guard had the same blind spot from the other direction: it counted
``count(n.embedding)`` over the WHOLE label, so a corpus embedded only OUTSIDE
the requested kind reported ``empty_results`` ("there are embeddings, just no
near neighbour") instead of ``no_embeddings``.

THE FIX (``#4199`` in ``tortoise/search_engine.py::run_vector_query``)

When a leg trace is being recorded, the caller now names the KIND values its
own post-retrieval kind filter selects (``scope_kinds``). The leg measures its
material over THAT scope once: a scope that HAS nodes but NONE embedded means
this read's dense leg can contribute nothing. Retrieval itself is untouched;
only the DECLARATION changes.

WHY NOT ``require_hybrid_retrieval`` (#2985)

That preflight is an AVAILABILITY gate — it proves the EMBEDDER can load and
refuses before ingest. It cannot see a healthy embedder over a corpus with no
dense material for the requested scope, which is this defect. The two are
complementary: availability before, scope coverage during. This file pins the
second, and asserts the first is untouched.

RESIDUAL (deliberate, documented — NOT a defect this fix claims to close)

A scope with *partial* coverage (some embedded, some not) still reports
``hybrid``: the dense leg genuinely contributes semantic rows for the embedded
part, and flipping ``degraded`` there would mislabel a read that fused real
semantic material (and contradict #2952's ``degraded`` = "contributed no
semantic results"). The zero-coverage case — where the claim is unsupportable
— is what is declared. Surfacing a coverage RATIO is future work.
"""
from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest

from tortoise.embeddings import EmbeddingModel
from tortoise.exceptions import HybridReadUnavailableError
from tortoise.search_engine import (
    VECTOR_LEG_UNAVAILABLE,
    VECTOR_SCOPE_EMPTY,
    VECTOR_SCOPE_KEY,
    declared_degraded_read,
    require_hybrid_read,
    reset_circuit_breakers,
    run_vector_query,
)

CONV = [
    {"role": "user",
     "content": "The auth dead-end is the top issue; ship serve --http first."},
    {"role": "assistant",
     "content": "Agreed, the website config looks like the root cause."},
]


class _FakeEmbedder:
    """Deterministic 384-dim encoder — a healthy embedder with no HF load."""

    DIM = 384

    def encode(self, texts, batch_size=32, show_progress_bar=False):
        rows = []
        for text in texts:
            digest = hashlib.sha256(str(text).encode("utf-8")).digest()
            raw = (digest * (self.DIM // len(digest) + 1))[:self.DIM]
            rows.append(np.frombuffer(raw, dtype=np.uint8).astype(np.float64))
        return np.asarray(rows)


def _offline_llm(monkeypatch) -> None:
    """Keyless capture — no LLM extraction runs (no network, no mock)."""
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def _embedder(monkeypatch, embedder):
    monkeypatch.setattr(
        EmbeddingModel, "get",
        classmethod(lambda cls, load_timeout=None: embedder))
    EmbeddingModel._reset()


def _no_embedder(monkeypatch):
    monkeypatch.setattr(
        EmbeddingModel, "get",
        classmethod(lambda cls, load_timeout=None: None))
    EmbeddingModel._reset()


@pytest.fixture
def embedder(monkeypatch):
    emb = _FakeEmbedder()
    yield emb
    EmbeddingModel._reset()


def _vector_entries(legs: list[dict]) -> list[dict]:
    """Every vector entry in a ``recall_state`` trace.

    ``recall_state`` reads Points then Objects, so the trace carries one
    vector entry per query. Selection is deliberately NOT keyed on
    ``mechanism``: the Point query is ``index`` in the docker lane and
    ``scan`` in the embedded carve-out lane, so a mechanism-keyed assertion
    would pin the LANE rather than the contract.
    """
    return [e for e in legs if e.get("leg") == "vector"]


def _vector_entry(trace: list[dict]) -> dict:
    """The single vector entry of a one-call (hermetic) trace."""
    entries = _vector_entries(trace)
    assert len(entries) == 1, trace
    return entries[0]


# ── 1. The falsifier: a partially-embedded corpus reads DEGRADED ───────────


# ── 2. Anti-overfix guards: the fix is scope-precise, not a blanket degrade ─


# ── 3. The guard, hermetically: scope-aware counts, fail-closed probe ──────

VEC = [0.1] * 384


class _MockResultSet:
    def __init__(self, result_set):
        self.result_set = result_set


class _RoutingGraph:
    """Routes each Cypher by substring; records every query it answered."""

    def __init__(self, routes):
        self._routes = routes
        self.seen: list[str] = []

    def query(self, cypher, params=None, timeout=None):
        self.seen.append(cypher)
        for sub, result_set, exc in self._routes:
            if sub in cypher:
                if exc:
                    raise exc
                return _MockResultSet(result_set or [])
        return _MockResultSet([])


@pytest.fixture(autouse=True)
def _reset_breakers():
    """Circuit-breaker state is module-level — isolate every test."""
    reset_circuit_breakers()
    yield
    reset_circuit_breakers()


def _scoped_run(graph, trace, *, scope=("event",)):
    return run_vector_query(
        graph, VEC, limit=10, is_embedded=True, entity_type="point",
        leg_trace=trace, scope_kinds=scope)


def test_zero_row_guard_counts_the_scope_not_the_whole_label():
    """The issue's literal evidence: an out-of-scope-only embedding set.

    The unscoped scan finds NO rows (all dense rows live outside the read's
    kind) while the whole label HAS embeddings. Pre-fix the guard ran the
    unscoped count, saw those label-wide embeddings, and recorded
    ``empty_results`` ("there are embeddings, no near neighbour"); the honest
    verdict is ``no_embeddings`` for THIS read. The guard must therefore take
    its count from the scope probe and NOT issue the unscoped count at all.

    The route table therefore models BOTH states — the read's scope (2 nodes,
    0 dense) and the whole label (5 dense) — so reverting the guard to the
    unscoped count has a defined, observable response instead of falling
    through to the mock's empty default.
    """
    graph = _RoutingGraph([
        ("count(n.embedding)", [(2, 0)], None),  # the read's scope: nodes, no dense
        ("RETURN count(*)", [(5,)], None),       # the WHOLE label: 5 embedded
        ("euclideanDistance", [], None),         # unscoped scan: no rows
    ])
    trace: list[dict] = []
    out = _scoped_run(graph, trace)

    assert out == [], out
    vec = _vector_entry(trace)
    assert vec["ran"] is True, vec
    assert vec["degraded"] is True, vec
    assert vec["reason"] == "no_embeddings", (
        f"the scope's zero dense coverage was reported as {vec['reason']!r}")
    # The guard's whole point: the read's OWN scope count (2 nodes, 0 dense)
    # decided it. The unscoped whole-label count (5 dense) must never be
    # issued — for a hollow scope the #4199 ``_record`` rewrite ALSO turns
    # ``empty_results`` into ``no_embeddings``, so the absence of this query is
    # the only place the guard's own behaviour is observable.
    assert not any("n.embedding IS NOT NULL RETURN count(*)" in c
                   for c in graph.seen), (
        f"the unscoped whole-label count ran: {graph.seen}")
    assert declared_degraded_read(trace)["reason"] == "no_embeddings"


def test_out_of_scope_dense_rows_do_not_serve_the_read():
    """The end-to-end shape, hermetically: rows returned, none in scope.

    The leg must still RETURN the rows (the read's kind filter owns dropping
    them — retrieval is unchanged), but the record may not present them as
    material for this read.
    """
    graph = _RoutingGraph([
        ("count(n.embedding)", [(2, 0)], None),
        ("euclideanDistance", [("ext-1", 0.9), ("ext-2", 0.8)], None),
    ])
    trace: list[dict] = []
    out = _scoped_run(graph, trace)

    assert [pid for pid, _ in out] == ["ext-1", "ext-2"], (
        "the fix must not change which rows the leg returns")
    vec = _vector_entry(trace)
    assert vec["degraded"] is True and vec["reason"] == "no_embeddings", vec
    assert vec["count"] == 0, vec


def _vec_entry(*, degraded, reason, count=0, scope=None, ran=True):
    """A hand-built vector trace entry (the shared R3/#4199 shape)."""
    entry = {"leg": "vector", "ran": ran, "degraded": degraded,
             "reason": reason, "count": count}
    if scope is not None:
        entry[VECTOR_SCOPE_KEY] = scope
    return entry


def test_degraded_arm_is_not_laundered_by_a_neutral_empty_scope_arm():
    """#4199 review P1 (hermetic): the exact trace shape of the laundering.

    The Point arm (in scope) ran degraded; the Object arm measured an EMPTY
    scope and its unscoped scan returned a row (``ok``). Before the review
    fix that ``ok`` entry proved the read hybrid — the empty-scope arm is
    NEUTRAL and must be skipped.
    """
    trace = [
        _vec_entry(degraded=True, reason="no_embeddings"),
        _vec_entry(degraded=False, reason="ok", count=1,
                   scope=VECTOR_SCOPE_EMPTY),
    ]
    marker = declared_degraded_read(trace)
    assert marker is not None, trace
    assert marker["reason"] == "no_embeddings", marker
    assert marker["hybrid"] is False, marker
    assert marker[VECTOR_LEG_UNAVAILABLE] is True, marker


def test_in_scope_healthy_arm_still_proves_hybrid_despite_empty_scope_arms():
    """The null round: a neutral arm never over-declares a healthy read.

    The Point arm is embedded and healthy; the Object arm's empty scope is
    neutral. The read stays hybrid — the fix is scope-precise, not a blanket
    degrade (the Object arm must keep its ``ok`` record).
    """
    trace = [
        _vec_entry(degraded=False, reason="ok", count=2),
        _vec_entry(degraded=False, reason="ok", count=1,
                   scope=VECTOR_SCOPE_EMPTY),
    ]
    assert declared_degraded_read(trace) is None, trace


def test_every_arm_empty_scope_does_not_declare():
    """The #2952 empty-scope rule survives the aggregation change.

    When EVERY vector arm measured an empty scope the read is empty
    regardless of the dense leg — ``no_embeddings`` would be a category
    error, so nothing is declared. #4199 review P1: on THIS trace the SIBLING
    gate (``require_hybrid_read``) must not fall through — before the fix it
    fell through to ``_degraded_read_marker("leg_absent", ...)`` and RAISED,
    so ``retrieval_legs`` reported ``hybrid=False`` with a
    ``declared_degraded_read=None`` that contradicted it.
    """
    trace = [
        _vec_entry(degraded=False, reason="ok", count=1,
                   scope=VECTOR_SCOPE_EMPTY),
        _vec_entry(degraded=True, reason="no_embeddings",
                   scope=VECTOR_SCOPE_EMPTY),
    ]
    assert declared_degraded_read(trace) is None, trace
    assert require_hybrid_read(trace) == {
        "hybrid": True, VECTOR_LEG_UNAVAILABLE: False}, trace


def test_require_hybrid_read_docstring_states_the_in_scope_rule():
    """#4199 review P3: the public docstring must describe what the code does.

    ``require_hybrid_read`` is the fail-loud capability lanes read; its
    docstring claimed ``hybrid`` iff "at least one NOT-degraded vector entry
    RAN", which is now false — :func:`_vector_leg_healthy` additionally
    requires ``scope != 'empty'``. A lane trusting the old wording would read
    an out-of-scope arm as proof the read was served. The docstring must name
    the IN-SCOPE rule (matching ``_vector_leg_healthy``'s own) and the
    all-empty-scope escape.
    """
    doc = require_hybrid_read.__doc__ or ""
    assert "IN-SCOPE" in doc, (
        "require_hybrid_read's docstring omits the in-scope rule: "
        f"{doc!r}")
    assert "at least one NOT-degraded vector entry RAN" not in doc, (
        "the docstring still claims a bare 'ran' proves hybrid: " f"{doc!r}")
    assert "EVERY vector arm measured an empty scope" in doc, doc


def test_scope_with_dense_material_is_untouched_by_the_fix():
    """A scope that IS embedded keeps the healthy ``ok`` record."""
    graph = _RoutingGraph([
        ("count(n.embedding)", [(2, 2)], None),
        ("euclideanDistance", [("turn-1", 0.9)], None),
    ])
    trace: list[dict] = []
    out = _scoped_run(graph, trace)

    assert [pid for pid, _ in out] == ["turn-1"], out
    vec = _vector_entry(trace)
    assert vec["degraded"] is False and vec["reason"] == "ok", vec
    assert declared_degraded_read(trace) is None


def test_empty_scope_is_not_declared_un_embedded():
    """A scope with NO nodes is not a partial corpus — do not fire.

    ``no_embeddings`` would be a category error for a kind nobody wrote, and
    the read is empty regardless of the dense leg.
    """
    graph = _RoutingGraph([
        ("count(n.embedding)", [(0, 0)], None),
        ("euclideanDistance", [("ext-1", 0.9)], None),
    ])
    trace: list[dict] = []
    _scoped_run(graph, trace)

    vec = _vector_entry(trace)
    assert vec["degraded"] is False and vec["reason"] == "ok", vec
    # …and the empty scope is recorded NEUTRAL so it can never launder (or be
    # blamed for) another arm's coverage — the #4199 review aggregation rule.
    assert vec.get(VECTOR_SCOPE_KEY) == VECTOR_SCOPE_EMPTY, vec
    assert declared_degraded_read(trace) is None, trace


def test_empty_scope_cannot_launder_a_leg_failure():
    """#4199 review P2: a FAILURE is never neutralised by an empty scope.

    The scope probe reports ``(0, 0)`` — the scope is genuinely empty — and
    then the scan RAISES. Before the review fix ``_record`` tagged the
    failure entry ``scope=empty``, the declaration skipped it as neutral, and
    a keyword-only read whose dense leg ERRORED was reported with no
    declaration at all: the same laundering the empty-scope rule exists to
    stop, keyed on scope instead of arm. ``index_missing`` is the other
    failure reachable post-probe; all three are covered.
    """
    for message, reason in (("kaboom", "query_failed"),
                            ("no such index for vector", "index_missing"),
                            ("embedding is null", "no_embeddings")):
        graph = _RoutingGraph([
            ("count(n.embedding)", [(0, 0)], None),
            ("euclideanDistance", [], RuntimeError(message)),
        ])
        trace: list[dict] = []
        out = _scoped_run(graph, trace)
        assert out == [], out
        vec = _vector_entry(trace)
        assert vec["reason"] == reason, vec
        assert vec.get(VECTOR_SCOPE_KEY) != VECTOR_SCOPE_EMPTY, (
            f"a leg FAILURE ({reason}) was tagged NEUTRAL by an empty "
            f"scope: {vec}")
        marker = declared_degraded_read(trace)
        assert marker is not None, (
            f"the empty scope laundered a {reason} failure: {trace}")
        assert marker["reason"] == reason, marker
        assert marker[VECTOR_LEG_UNAVAILABLE] is True, marker
        # C2 agrees with C1: the failure is itself the refusal reason.
        with pytest.raises(HybridReadUnavailableError) as exc:
            require_hybrid_read(trace)
        assert exc.value.reason == reason, exc.value.marker


def test_unmeasurable_scope_fails_closed():
    """A probe that cannot run must NOT be reported as healthy (fail closed).

    Retrieval is unaffected — the rows are still returned — but the
    declaration refuses to claim the dense leg serves the scope.
    """
    graph = _RoutingGraph([
        ("count(n.embedding)", [], RuntimeError("boom")),
        ("euclideanDistance", [("ext-1", 0.9)], None),
    ])
    trace: list[dict] = []
    out = _scoped_run(graph, trace)

    assert [pid for pid, _ in out] == ["ext-1"], out
    vec = _vector_entry(trace)
    assert vec["degraded"] is True and vec["reason"] == "no_embeddings", vec
    assert declared_degraded_read(trace) is not None


def test_probe_returning_no_rows_fails_closed():
    """#5287: an EMPTY probe result set is UNMEASURABLE — fail CLOSED.

    The aggregate ``RETURN count(*), count(...)`` always yields one row, so
    an empty result set means the probe answered nothing rather than that the
    scope is empty. Pre-fix ``if _scope_rows:`` skipped the assignment
    entirely, leaving ``_scope_hollow=False``/``_scope_embedded=None`` so the
    zero-row guard fell back to the unscoped whole-label count and a healthy
    record could stand — contradicting the adjacent "an UNMEASURABLE scope
    fails CLOSED" comment. Only the raised-probe path (the sibling test
    above) actually failed closed.
    """
    graph = _RoutingGraph([
        ("count(n.embedding)", [], None),
        ("euclideanDistance", [("ext-1", 0.9)], None),
    ])
    trace: list[dict] = []
    out = _scoped_run(graph, trace)

    assert [pid for pid, _ in out] == ["ext-1"], out
    vec = _vector_entry(trace)
    assert vec["degraded"] is True and vec["reason"] == "no_embeddings", vec
    assert declared_degraded_read(trace) is not None, trace


def test_no_scope_and_no_trace_never_pays_for_the_probe():
    """The addition is opt-in: a default caller runs no scope query at all."""
    graph = _RoutingGraph([
        ("count(n.embedding)", None, AssertionError("scope probe must not run")),
        ("euclideanDistance", [("p-1", 0.9)], None),
    ])
    # No leg_trace and no scope_kinds — the default production shape.
    out = run_vector_query(graph, VEC, limit=10, is_embedded=True)
    assert [pid for pid, _ in out] == ["p-1"], out

    # A trace WITHOUT a scope is also unchanged (pre-#4199 behavior).
    trace: list[dict] = []
    _scoped_run(graph, trace, scope=None)
    assert len(trace) == 1 and trace[0]["reason"] == "ok", trace
