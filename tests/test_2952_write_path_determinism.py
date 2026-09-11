"""#2952 defect 1 — the FRESH-CREATE write path must not skew fulltext scores.

Retrieval must be a function of (graph, query, params). This file covers the
second, independent root cause of the "``recall_state`` results change with
wall-clock time and no writes" report; the *ordering* half of #2952
(``rrf_fusion`` tie-break, fixed leg order, injectable ``GraphRanker`` anchor)
is covered by the hermetic ``tests/test_retrieval_determinism_2952.py`` and is
NOT re-tested here.

The defect (defect 1):

``TortoiseSDK.create_point`` used to write a new Point in several statements —
a ``CREATE`` followed by ``SET n.embedding = vecf32(...)`` and then one
``SET n += $props`` per property, with ``_mark_dirty`` adding a further
``SET n.ep_dirty = ...``. On FalkorDB (embedded *and* server — same engine) a
write that touches an already-created node makes the fulltext index delete +
re-add the document; the re-add double-counts the index's collection
statistics (the document count / total field length behind the ``idf`` and
``fieldNorm`` terms of ``score``), and only an ASYNC engine pass reconciles
them tens of seconds later. So ``db.idx.fulltext.queryNodes`` scores — and with
them the RRF-fused order and ``recall_state``'s top-k — changed on an unchanged
graph, purely as a function of elapsed wall-clock time.

Evidence assembled when the fix was written (all on an unchanged graph):

* engine ``score`` for the same query shifted 4.0 → 8.0 with no write;
* the term statistics (``n`` / ``df``) were correct from t=0 — only the
  engine's collection statistics were skewed;
* the arms test (kept below, as
  ``test_create_point_is_one_graph_write_per_point``): an inline single-write
  ``CREATE`` scores identically before and after settle, while ``set_prop``,
  ``set_dirty`` and even a no-op ``REMOVE`` skew the score;
* reproduced on the embedded and Docker lanes;
* test corpus on MemoryAgentBench ``factconsolidation_sh_6k`` (455 facts, 100
  questions, k=20): top-20 membership drift 56/100 after 90 s idle, 5/100
  after 3 s, 0/100 once the fresh-create path stopped issuing post-CREATE
  writes.

The fix folds EVERY property of a new Point into the ``CREATE``'s property map
(one graph write per point), building it as a MAPPING so a caller prop that
collides with a server-owned field replaces it rather than duplicating the key
(a list-form map raised ``Duplicate property key 'createdAt'``).

No assertion here sleeps: the settled state is reached deterministically by
closing and reopening the SAME store (the engine rebuilds its index from the
persisted nodes), and the freed-extra-write invariant is asserted directly on
the emitted Cypher. ``force_sparse_tfidf`` pins the degraded sparse path so the
retrieval pool size is environment-independent.
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.live import TERMINAL_EXCLUDED_STATUSES
from tortoise.sdk import POINT_STATUS_VALUES, TortoiseSDK
from tortoise.sparse import build_or_query

# ── Hermetic corpus ────────────────────────────────────────────────────────
# Self-authored (no benchmark download): a deliberately shared vocabulary so
# one question matches many facts, and three different field LENGTHS for the
# same fact — which is what makes the engine's collection statistics (idf /
# fieldNorm) visible in the scores, and therefore in the ranking. A corpus
# with uniform field lengths hides the defect: a uniform score shift cancels
# out in StateRanker's min-max relevance normalisation.
_CITIES = ["london", "paris", "rome", "madrid", "berlin", "oslo", "dublin", "vienna"]
_COUNTRIES = ["england", "france", "italy", "spain", "germany", "norway",
              "ireland", "austria"]
_FILLER = ["indeed", "moreover", "historically", "notably", "reportedly",
           "allegedly", "apparently", "furthermore", "consequently",
           "significantly", "traditionally", "previously", "officially",
           "eventually", "interestingly"]


def _fact(i):
    city, country, year = _CITIES[i % 8], _COUNTRIES[i % 8], 1200 + i * 7
    if i % 3 == 0:
        # terse — the query terms are a large share of the field
        return f"{i}. The city of {city} lies in {country}."
    if i % 3 == 1:
        return (f"{i}. The city of {city} is the capital of {country} and "
                f"was founded in the year {year}.")
    # verbose — the SAME fact with a much longer field (fieldNorm differs)
    pad = " ".join(_FILLER[: 1 + i % len(_FILLER)])
    return (f"{i}. The city of {city} is the capital of {country} and was "
            f"founded in the year {year}. {pad}")


FACTS = [_fact(i) for i in range(96)]
QUESTIONS = [
    "In which country is the city of london?",
    "Which city is the capital of france?",
    "When was the city of rome founded?",
    "Which country has the city of vienna as its capital?",
    "What year was the city of oslo founded?",
    "In which country is the city of dublin?",
]


def _open(tmp_path, namespace):
    """Open THE one store these tests measure across a close/reopen.

    A ``test_``-prefixed namespace resolves to the same graph on both lanes
    (embedded db file, or the URI-aware test redirect's
    ``test_<ns>_tortoise`` graph), so ``close()`` + reopen is the same store.
    """
    return TortoiseSDK(
        os.path.join(str(tmp_path), "repro2952.db"),
        namespace=namespace,
        event_log_path=os.path.join(str(tmp_path), "events2952.jsonl"),
    )


def _new_namespace():
    return f"test_2952_{uuid.uuid4().hex[:8]}"


def _ingest(sdk):
    """Write the corpus through the product write path."""
    for fact in FACTS:
        sdk.create_point(kind="evidence", content=fact, dedup=True,
                         credibility="medium", source_harness="battery-parity",
                         source_session="regression-2952")


def _raw_fts(sdk, questions):
    g = sdk._get_proj().g
    return [
        [(r[0], round(float(r[1]), 6)) for r in g.query(
            "CALL db.idx.fulltext.queryNodes('Point', $q) YIELD node, score "
            "RETURN node.id, score ORDER BY score DESC LIMIT 20",
            params={"q": build_or_query(q)}, timeout=10000).result_set]
        for q in questions
    ]


def _rankings(sdk, questions, k=20):
    """``recall_state``'s returned rows reduced to what the caller sees as
    "the result": the ordered ids AND the ranking breakdown derived from the
    retrieved relevance scores.

    Both halves matter. On a corpus with uniform field lengths the id ORDER
    can survive the engine's statistics skew (the shift is close to uniform,
    and StateRanker's min-max relevance normalisation cancels it) while the
    scores behind it do not — and it is the scores that reorder real corpora.
    """
    return [
        [(r["id"], r["recall_ranking"]["relevance_norm"],
          r["recall_ranking"]["final_score"]) for r in sdk.recall_state(
              query=q, kind=None, limit=k, object_centric=False)]
        for q in questions
    ]


# ── 1. the invariant the fix rests on: ONE graph write per new point ───────

def test_create_point_is_one_graph_write_per_point(tmp_path, monkeypatch,
                                                   force_sparse_tfidf):
    """A fresh point must be created by ONE graph write. Every extra write on
    the node costs the fulltext index an extra entry, which is what skewed the
    engine's statistics (and, pre-fix, made the ranking drift for ~tens of
    seconds after ingest). This is the PLAIN variant; the sourced
    (``extractedFrom``) variant is asserted by
    ``test_create_point_sourced_with_inherited_at_is_one_graph_write`` below
    (the invariant is universal — it is asserted for every fresh-create
    path, not just this one)."""
    from tortoise.projection import _GuardedGraph

    namespace = _new_namespace()
    sdk = _open(tmp_path, namespace)

    writes: list[str] = []
    original = _GuardedGraph.query

    def spy(self, cypher, *args, **kwargs):
        text = " ".join(str(cypher).split())
        # A statement that CREATES/SETS/REMOVEs on a Point node.
        if "Point" in text and any(
                marker in text for marker in
                ("CREATE (n:Point", "SET n.", "SET n ", "SET n +=",
                 "REMOVE n.")):
            writes.append(text[:120])
        return original(self, cypher, *args, **kwargs)

    monkeypatch.setattr(_GuardedGraph, "query", spy)
    try:
        sdk.create_point(kind="evidence", content=FACTS[0], credibility="medium",
                         source_harness="battery-parity",
                         source_session="regression-2952")
    finally:
        sdk.close()

    assert len(writes) == 1, (
        "create_point must write the new Point node exactly once — every "
        f"post-CREATE write costs a fulltext re-add (#2952). Saw {len(writes)}: "
        f"{writes}"
    )
    assert writes[0].startswith("CREATE (n:Point")


# ── 1b. the ONE-write invariant is universal, not plain variant only ──────
#
# The invariant above is asserted for a plain point. The `extractedFrom`
# path is the second fresh-create variant, and it is asserted explicitly
# below (P2, PR #3018 review — a test that implies a universal invariant it
# does not check would be worse than no test).


def test_create_point_sourced_with_inherited_at_is_one_graph_write(
        tmp_path, monkeypatch, force_sparse_tfidf):
    """The sourced (``extractedFrom``) path is ONE write too, including when
    the caller supplies ``inherited_at`` (P2, PR #3018 review).

    That path used to follow the CREATE with a `REMOVE n.inherited_at`, and
    when the caller passed an `inherited_at` prop the CREATE map wrote it
    first — so the point took TWO property writes, the exact fulltext re-add
    #2952 is about. The prop is now dropped before the CREATE map is built:
    final node state identical (`inherited_at` absent, #398 gate invalid),
    ONE write.
    """
    from tortoise.projection import _GuardedGraph

    namespace = _new_namespace()
    sdk = _open(tmp_path, namespace)

    writes: list[str] = []
    original = _GuardedGraph.query

    def spy(self, cypher, *args, **kwargs):
        text = " ".join(str(cypher).split())
        if "Point" in text and any(
                marker in text for marker in
                ("CREATE (n:Point", "SET n.", "SET n ", "SET n +=",
                 "REMOVE n.")):
            writes.append(text[:120])
        return original(self, cypher, *args, **kwargs)

    monkeypatch.setattr(_GuardedGraph, "query", spy)
    try:
        point = sdk.create_point(
            kind="evidence", content=FACTS[0], credibility="medium",
            source_harness="battery-parity",
            source_session="regression-2952",
            extractedFrom="doc:2952-sourced",
            inherited_at="2026-01-01T00:00:00+00:00",
        )
        pid = point["id"]
        stored = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN n.inherited_at, n.extractedFrom",
            params={"id": pid}).result_set
    finally:
        sdk.close()

    assert len(writes) == 1, (
        "the extractedFrom + caller-supplied inherited_at path must write "
        "the new Point node exactly once too — the inheritance-gate REMOVE "
        f"is folded into the CREATE by dropping the prop. Saw {len(writes)}: "
        f"{writes}"
    )
    assert writes[0].startswith("CREATE (n:Point")
    assert stored == [[None, "doc:2952-sourced"]], (
        "final node state must be unchanged from the write-then-REMOVE "
        "version: inherited_at absent (a freshly-sourced point is always "
        f"inherit-eligible, #398), extractedFrom stored. graph={stored}"
    )


# ── 1c. born-terminal points must not become dirty roots (#2422) ──────────

#: Born-terminal statuses `create_point`'s closed vocabulary admits
#: (``POINT_STATUS_VALUES``). ``deprecated`` is in
#: ``TERMINAL_EXCLUDED_STATUSES`` but is not a create-time status — legacy /
#: assessment paths write it — so it cannot be BORN through this path.
_BORNABLE_TERMINAL = sorted(TERMINAL_EXCLUDED_STATUSES & POINT_STATUS_VALUES)


def test_born_terminal_point_is_not_a_dirty_root(tmp_path, force_sparse_tfidf):
    """A point created ALREADY terminal must not be stamped ``ep_dirty`` by
    its own CREATE (P1, PR #3018 review — the #2422 ghost class).

    The inline ``ep_dirty``/``ep_dirty_at`` write coexisted with the
    ``pre_stamped`` handshake, so ``_mark_dirty`` counted the id as persisted
    (``persisted |= _stamped & set(dirty_ids)``), the persist query's terminal
    WHERE never ran, and the terminal classification never fired. A terminal
    point can never enter an EP affected set, so ``_sweep_dirty_roots`` could
    never clear the flag: a never-clearable dirty root pinning
    ``_auto_dream_mode`` to ``'local'`` forever — exactly the state the
    comment in ``_mark_dirty`` forbids.

    The observables mirror the reviewer's evidence on the pre-fix tip: the
    node carried ``(status='retracted', ep_dirty=true, ep_dirty_at=1)``, the
    id was in ``_dirty_roots``, and replaying the pre-#2952 persist statement
    on that node returned ``[]`` (the terminal WHERE already excluded it, so
    only the inline stamp kept it flagged).
    """
    assert _BORNABLE_TERMINAL, "expected at least one born-terminal status"
    for status in _BORNABLE_TERMINAL:
        namespace = _new_namespace()
        sdk = _open(tmp_path, namespace)
        try:
            point = sdk.create_point(
                kind="statement",
                content=f"born {status} — #2422 regression",
                status=status)
            pid = point["id"]
            rows = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) "
                "RETURN n.status, n.ep_dirty, n.ep_dirty_at",
                params={"id": pid}).result_set
            roots = set(sdk._dirty_roots)
            # The pre-#2952 persist statement, replayed verbatim: it is the
            # statement the fix routes a born-terminal id through instead of
            # pre-stamping it. `[]` proves the terminal classification owns
            # this node (a non-empty row would mean it should be dirty).
            replayed = sdk._get_proj().g.query(
                "UNWIND $ids AS pid MATCH (n:Point {id: pid}) "
                "WHERE NOT coalesce(n.status, 'live') IN $terminal "
                "AND coalesce(n.outdated, false) = false "
                "SET n.ep_dirty = true, n.ep_dirty_at = $ep RETURN pid",
                params={"ids": [pid],
                        "terminal": sorted(TERMINAL_EXCLUDED_STATUSES),
                        "ep": 0}).result_set
        finally:
            sdk.close()

        assert rows == [[status, None, None]], (
            f"a point born {status!r} must not carry an EP-dirty stamp — the "
            "terminal exclusion lives only in _mark_dirty's persist WHERE, "
            f"which an inline stamp bypasses. graph={rows}"
        )
        assert pid not in roots, (
            f"a point born {status!r} must not enter _dirty_roots — a "
            "terminal point can never be swept, so the flag would strand and "
            f"pin _auto_dream_mode to 'local' forever. roots={sorted(roots)}"
        )
        assert replayed == [], (
            "the persist WHERE already classifies the born-terminal id as "
            "terminal (nothing to mark), so the id must flow through the "
            f"terminal classification, never pre-stamping. replayed={replayed}"
        )


# ── 2. the engine's own scores must not drift with elapsed wall-clock ──────

def test_fulltext_scores_are_stable_across_a_reopen(tmp_path,
                                                    force_sparse_tfidf):
    """The engine's fulltext scores for a fixed (graph, query) must not depend
    on how long ago the graph was written.

    ``close()`` + reopen is the deterministic stand-in for "wait until the
    async pass has run": the engine rebuilds its index from the persisted
    nodes, which is the settled state. Pre-fix the freshly-ingested store
    scored strictly lower than the reopened one (measured on both lanes:
    fresh 1.0 vs reopened 3.0 at 80 facts), and it drifted up over ~30-60 s.
    """
    namespace = _new_namespace()
    sdk = _open(tmp_path, namespace)
    _ingest(sdk)
    fresh = _raw_fts(sdk, QUESTIONS)
    sdk.close()

    sdk = _open(tmp_path, namespace)
    settled = _raw_fts(sdk, QUESTIONS)
    sdk.close()

    assert settled == fresh, (
        "the same store returned different fulltext scores minutes apart with "
        "no writes — the ranking is not a function of (graph, query, params). "
        f"fresh={fresh} settled={settled}"
    )


# ── 3. the user-visible surface ────────────────────────────────────────────

def test_recall_state_results_are_stable_across_a_reopen(tmp_path,
                                                         force_sparse_tfidf):
    """``recall_state``'s returned rows for a fixed (graph, query, params)
    must be identical before and after a reopen — the ordered ids AND the
    relevance/final scores they were ranked on."""
    namespace = _new_namespace()
    sdk = _open(tmp_path, namespace)
    _ingest(sdk)
    fresh = _rankings(sdk, QUESTIONS)
    sdk.close()

    sdk = _open(tmp_path, namespace)
    settled = _rankings(sdk, QUESTIONS)
    sdk.close()

    assert settled == fresh, (
        "recall_state returned different results for the same unchanged store "
        f"before vs after a reopen. fresh={fresh} settled={settled}"
    )
