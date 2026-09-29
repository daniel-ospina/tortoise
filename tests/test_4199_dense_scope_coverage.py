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
this read's dense leg can contribute nothing, so every healthy outcome record
is written as ``no_embeddings``/``degraded`` — the #2952 vocabulary, no new
term. Retrieval itself is untouched; only the DECLARATION changes.

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
from tortoise.search_engine import (
    VECTOR_LEG_UNAVAILABLE,
    declared_degraded_read,
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


def _capture_unembedded_turns(sdk, monkeypatch, session_id: str) -> list[str]:
    """The production state: turn Points written with NO dense material.

    Exercises the real capture loop with the embedder unavailable (#4194's
    declared negative — the turn is still stored, with an honest NULL vector),
    never a hand-written raw Cypher node.
    """
    _no_embedder(monkeypatch)
    assert sdk.capture_session(CONV, session_id=session_id)["turns"] == len(CONV)
    rows = sdk._get_proj().g.query(
        "MATCH (t:Point) WHERE t.id STARTS WITH $p RETURN t.id, t.embedding "
        "ORDER BY t.id",
        params={"p": f"{session_id}_t"}).result_set
    assert rows, f"capture wrote no turn Points for {session_id}"
    assert all(r[1] is None for r in rows), (
        "the fixture must build an UN-embedded turn corpus")
    return [r[0] for r in rows]


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

def test_partial_turn_corpus_read_is_declared_degraded(
        sdk_factory, embedder, monkeypatch):
    """The #4199 defect, end to end: turns un-embedded, extracted embedded.

    The read is scoped to the turn material (``kind='event'`` — the turn
    ``pointKind``). Before the fix this returned ``hybrid: True`` with
    ``declared_degraded_read() is None`` while every turn hit's ``vector``
    score was ``None``. The dense leg must not be reported as serving a scope
    it has no material for.
    """
    _offline_llm(monkeypatch)
    sdk = sdk_factory()
    try:
        turn_ids = _capture_unembedded_turns(sdk, monkeypatch, "sess-4199-part")
        # Extracted Points ARE embedded (the "partially embedded" half).
        _embedder(monkeypatch, embedder)
        sdk.create_point("statement", "auth dead-end top issue claim", id="ext-1")
        sdk.create_point("statement", "website config root cause claim", id="ext-2")

        census = sdk._get_proj().g.query(
            "MATCH (n:Point) RETURN count(n), count(n.embedding)").result_set[0]
        assert census[1] > 0, "the corpus must carry SOME dense material"
        in_scope = sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.pointKind='event' "
            "RETURN count(n), count(n.embedding)").result_set[0]
        assert in_scope[0] > 0 and in_scope[1] == 0, (
            "the fixture must build the measured partial state (scope HAS "
            f"nodes, NONE embedded), got {in_scope}")

        legs = sdk.retrieval_legs("auth dead-end", kind="event", limit=5)
    finally:
        sdk.close()

    marker = legs["declared_degraded_read"]
    assert marker is not None, (
        "a read whose dense leg has NO material for its own scope was "
        f"declared healthy: {legs['legs']}")
    assert marker["reason"] == "no_embeddings", marker
    assert marker["degraded_read"] is True, marker
    assert marker["hybrid"] is False, marker
    assert marker[VECTOR_LEG_UNAVAILABLE] is True, marker
    assert marker["missing_legs"] == ["vector"], marker
    assert legs["hybrid"] is False, legs
    assert legs["hybrid_refusal_reason"] == "no_embeddings", legs
    # The Point query's own vector entry says WHY — the leg ran, and the
    # scope had nothing for it. Asserted over ALL vector entries so the pin
    # holds in both the index (docker) and scan (embedded) lanes.
    vecs = _vector_entries(legs["legs"])
    assert vecs, legs["legs"]
    assert any(v["ran"] and v["degraded"] and v["reason"] == "no_embeddings"
               for v in vecs), vecs
    assert not any(v["ran"] and not v["degraded"] for v in vecs), (
        f"a vector entry still reads healthy over an un-embedded scope: {vecs}")
    # …and the returned material really is keyword-only for the turns.
    assert turn_ids, "sanity: the turn fixtures exist"


def test_the_returned_material_is_keyword_only_for_the_turns(
        sdk_factory, embedder, monkeypatch):
    """The dense score is absent from every turn hit — the claim being denied.

    This is the observable the declaration must agree with: no turn hit
    carries a ``vector`` score, so "hybrid" would be unsupported. (Pre-fix
    the trace said ``hybrid`` anyway — that divergence IS the bug.)
    """
    _offline_llm(monkeypatch)
    sdk = sdk_factory()
    try:
        _capture_unembedded_turns(sdk, monkeypatch, "sess-4199-prov")
        _embedder(monkeypatch, embedder)
        sdk.create_point("statement", "auth dead-end top issue claim", id="ext-1")
        hits = sdk.recall_state("auth dead-end", kind="event", limit=5)
        turn_hits = [h for h in hits if str(h.get("id", "")).startswith(
            "sess-4199-prov_t")]
        assert turn_hits, f"no turn hit in the read: {[h.get('id') for h in hits]}"
        for hit in turn_hits:
            assert (hit.get("scores") or {}).get("vector") is None, hit
    finally:
        sdk.close()


# ── 2. Anti-overfix guards: the fix is scope-precise, not a blanket degrade ─

def test_fully_embedded_turn_corpus_still_reports_hybrid(
        sdk_factory, embedder, monkeypatch):
    """A turn scope that IS embedded must keep reading as hybrid.

    Guards against "fixing" #4199 by degrading every kind-scoped read: the
    verdict is a measurement of the scope, not a pessimistic default.
    """
    _offline_llm(monkeypatch)
    sdk = sdk_factory()
    try:
        _embedder(monkeypatch, embedder)
        assert sdk.capture_session(CONV, session_id="sess-4199-full")["turns"] == 2
        rows = sdk._get_proj().g.query(
            "MATCH (t:Point) WHERE t.is_episodic=true RETURN t.embedding"
        ).result_set
        assert rows and all(r[0] is not None for r in rows), rows

        legs = sdk.retrieval_legs("auth dead-end", kind="event", limit=5)
    finally:
        sdk.close()

    assert legs["declared_degraded_read"] is None, legs["legs"]
    assert legs["hybrid"] is True, legs
    assert legs["hybrid_refusal_reason"] is None, legs
    vecs = _vector_entries(legs["legs"])
    assert any(v["ran"] and not v["degraded"] and v["reason"] == "ok"
               for v in vecs), vecs


def test_unscoped_read_keeps_reporting_hybrid(
        sdk_factory, embedder, monkeypatch):
    """No kind scope ⇒ the whole label is the scope, and it HAS dense material.

    A partially-embedded corpus is not globally degraded: the extracted
    Points genuinely serve an unscoped read, so ``hybrid`` stays true. This
    pins that the fix does not over-reach from "this scope" to "the corpus".
    """
    _offline_llm(monkeypatch)
    sdk = sdk_factory()
    try:
        _capture_unembedded_turns(sdk, monkeypatch, "sess-4199-noscope")
        _embedder(monkeypatch, embedder)
        sdk.create_point("statement", "auth dead-end top issue claim", id="ext-1")

        legs = sdk.retrieval_legs("auth dead-end", limit=5)
    finally:
        sdk.close()

    assert legs["declared_degraded_read"] is None, legs["legs"]
    assert legs["hybrid"] is True, legs


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
    kind) while the whole label HAS embeddings. Pre-fix that recorded
    ``empty_results`` ("there are embeddings, no near neighbour"); the honest
    verdict is ``no_embeddings`` for THIS read.
    """
    graph = _RoutingGraph([
        ("count(n.embedding)", [(2, 0)], None),   # the read's scope
        ("euclideanDistance", [], None),          # unscoped scan: no rows
    ])
    trace: list[dict] = []
    out = _scoped_run(graph, trace)

    assert out == [], out
    vec = _vector_entry(trace)
    assert vec["ran"] is True, vec
    assert vec["degraded"] is True, vec
    assert vec["reason"] == "no_embeddings", (
        f"the scope's zero dense coverage was reported as {vec['reason']!r}")
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
