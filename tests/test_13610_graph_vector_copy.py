"""#13610 — the graph stops storing AND serving `:Object`/`:Subject` vectors.

THE ISSUE IN ONE PARAGRAPH. `#11395` comment `6956793300` (owner, 2026-11-17)
ordered the graph-side copy retired in two steps — *"record first, graph removal
second, in that order and not in one release"*. Step 1 landed (`#13422` serves both
labels from the record; `#13447` made the restore land a vector and the write pay
ONE model call). Step 2 is this change, and it ships **option 2** of the issue's
three: *the graph write is retained while UNPROVEN and removed once the record
proves coverage*, so double-storage is a transitional state that ends
per-deployment and no deployment is ever left with neither copy.

WHAT THIS FILE PINS, and each item is a way the change could be defeated:

1. **A default deployment answers goals/people from the record** with the graph
   holding NO copy (`test_a_default_deployment_...`). ⛔ On the tree this issue was
   filed against, this test FAILS: `TORTOISE_RECORD_VECTOR_LEG` defaulted OFF, so
   `searcher()` returned ``None``, `run_vector_query` took the graph branches, and
   a node with no `embedding` was invisible to a dense query.
2. **The no-silent-loss proof** (`test_unverified_coverage_...`): a graph node that
   carries a vector while its record row does not must still be FOUND. The
   mechanism is asserted, not described — the coverage COUNT declines the record
   leg and the graph branch answers. The same test drives the MUTANT (coverage
   forced verified) and asserts the row then vanishes, so the mechanism is what is
   being measured rather than a coincidence of the fixture.
3. **Post-contract there is no graph fallback** (`test_post_contract_...`): once
   the count is satisfied the graph copy is frozen, so a graph answer would be a
   SMALLER cohort presented as the answer — the leg reports the failure instead.
4. **The write gate is the read gate inverted** (`test_mirror_...`), so the two
   halves cannot disagree about which store owns the vector; and the folds really
   do stop encoding AND stop writing (`test_the_folds_...`, driven through a real
   embedded projection).
5. **The wiring is pinned** (`test_every_searcher_construction_site_...`): an
   unwired caller silently gets double-storage for ever, which no test of the
   predicate would catch.

Run (embedded lane; the DB-free cases need no database, the two projection cases
build their own by path)::

    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
        tests/test_13610_graph_vector_copy.py -q
"""
from __future__ import annotations

import ast
import inspect
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from tortoise import record_vector
from tortoise.projection import FalkorProjection
from tortoise.search_engine import (
    VECTOR_SCOPE_EMPTY,
    VECTOR_SCOPE_KEY,
    declared_degraded_read,
    run_vector_query,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SDK = _REPO_ROOT / "tortoise" / "sdk.py"
_RECORD_VECTOR = _REPO_ROOT / "tortoise" / "record_vector.py"
_ENTITIES = _REPO_ROOT / "tortoise" / "projection" / "entities.py"
_STORAGE_DOC = _REPO_ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE.md"

_DIM = 384
_SCOPE = ("org_13610", "graph_13610")


# ── fakes: a graph that records what reached it, a record that answers ────


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _Graph:
    """A graph door that RECORDS. ``queries == []`` is how "the graph never
    answered" is asserted — the dense branches are the only thing it can do."""

    def __init__(self, rows=()):
        self.queries: list[str] = []
        self.rows = list(rows)

    def query(self, sql, params=None, timeout=None):
        self.queries.append(sql)
        if "queryNodes" in sql:
            # This store carries no VECTOR index for the label — the MEASURED
            # state of `:Object`/`:Subject` before this issue (`#4997`/`#13108`:
            # no index, full scan, 13-17 ms), and the shape that makes the graph
            # branch a SCAN so the mechanism assertion means something.
            raise RuntimeError(
                "Invalid arguments for procedure 'db.idx.vector.queryNodes'")
        return _Result(self.rows)


class _Cursor:
    """Answers the coverage ``count(*)`` and the arm's kNN, and records both."""

    def __init__(self, *, count: int, rows: list[tuple]):
        self.statements: list[tuple[str, tuple]] = []
        self._count = count
        self._rows = rows
        self.description = [("id",), ("distance",), ("recency",)]

    def execute(self, sql, params=None):
        self.statements.append((sql, tuple(params or ())))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return (self._count,)

    def close(self):
        return None


class _Conn:
    def close(self):
        return None


def _stub_record(monkeypatch, *, count: int, rows: list[tuple]) -> _Cursor:
    """Point the record doors at an in-memory cursor (the #13422 pattern).

    Both statements the leg issues go to ONE cursor because both doors are the
    same door: `record_vector.coverage`'s ``count(*)`` (answered by
    :meth:`_Cursor.fetchone`) and the folded arm's kNN (answered by
    :meth:`_Cursor.fetchall`). The fold's own DB half is stubbed — the cage is
    `tests/test_13422_object_subject_dense_leg.py`'s subject, and what THIS file
    measures is which STORE answered.
    """
    import tortoise.record_projection as rp
    import tortoise.record_serve as rs

    cur = _Cursor(count=count, rows=rows)
    monkeypatch.setattr(rs, "open_record", lambda *a, **k: _Conn(), raising=True)
    monkeypatch.setattr(rp, "scope_connection", lambda c, org: cur, raising=True)
    monkeypatch.setattr(
        record_vector, "_folded_terminal_keys",
        lambda conn, cur_, scope, entity_type, excluded: ([], len(rows)),
        raising=True)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    monkeypatch.delenv("TORTOISE_RECORD_JOURNAL", raising=False)
    # Tolerant on purpose: this is SETUP, not the claim. It lets this file run
    # against the PRE-#13610 tree (where the cache does not exist) so the
    # definition-of-done case below can be shown failing on the BEHAVIOUR rather
    # than on a missing helper — a new test that only fails because it names a new
    # function proves nothing.
    getattr(record_vector, "reset_coverage_cache", lambda: None)()
    return cur


def _searcher(graph_vectors):
    """A searcher for this scope, with the graph's denominator supplied."""
    return record_vector.searcher(
        carried=_SCOPE, timeout_ms=500,
        graph_population=lambda entity_type: graph_vectors)


def _run(pg_vector, *, entity_type="object", graph=None):
    trace: list[dict] = []
    graph = graph or _Graph()
    rows = run_vector_query(
        graph, [0.01] * 8, limit=10, is_embedded=False, entity_type=entity_type,
        leg_trace=trace, pg_vector=pg_vector, timeout_ms=500)
    return rows, graph, trace


# ── 1. the DoD: a DEFAULT deployment answers from the record ──────────────


@pytest.mark.parametrize("entity_type,key", [("object", "obj-13610"),
                                             ("subject", "sub-13610")])
def test_a_default_deployment_answers_from_the_record_with_no_graph_copy(
        entity_type, key, monkeypatch):
    """⛔ THE DEFINITION OF DONE, AND IT FAILS ON THE PRE-#13610 TREE.

    A live `:Object` and a live `:Subject` whose vector exists ONLY in the record
    (the graph node carries no `embedding` — the post-contract shape, and the shape
    a fresh deployment reaches on its first write) must answer a dense query on a
    deployment with **no environment variable set**.

    On the tree this issue was filed against, `TORTOISE_RECORD_VECTOR_LEG`
    defaulted OFF: `searcher()` returned ``None``, `run_vector_query` had no
    `pg_vector`, took the graph branches, and returned NOTHING for a row the graph
    has no vector for. So this assertion is the issue's target, and its failure
    pre-change is what makes it a pin rather than a description.

    ``graph_population`` answers ``0``: the graph holds no vector-bearing node, so
    the coverage count is satisfied by ANY record vector (``2 >= 0``) — which is
    exactly right, because there is no graph-side material for the record to be
    short of.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    _stub_record(monkeypatch, count=2,
                 rows=[(key, 0.10, "2026-11-18T00:00:00+00:00")])

    graph = _Graph()
    rows, graph, trace = _run(_searcher(0), entity_type=entity_type, graph=graph)

    # ⛔ THE CLAIM FIRST, so this case fails on the BEHAVIOUR on the pre-#13610
    # tree (an empty dense answer) rather than on the arming predicate below.
    assert [row[0] for row in rows] == [key], (
        "a dense query for a live entity answered from neither store — the record "
        "holds the only copy and it was not consulted")
    assert record_vector.cutover_armed() is True, (
        "the arm is the DEFAULT since #13610 — an unset deployment is meant to "
        "serve goals/people from the record")
    assert graph.queries == [], (
        "the graph was queried for a label whose vector lives only in the record: "
        f"{graph.queries}")
    assert [entry.get("mechanism") for entry in trace] == ["pgvector"], trace


# ── 2. the no-silent-loss proof ───────────────────────────────────────────


def test_unverified_coverage_answers_from_the_graph_and_not_a_short_record(
        monkeypatch):
    """⛔ THE ONE OUTCOME THE ISSUE FORBIDS: a row in NEITHER answer.

    The state: a graph holding an object vector whose record row does NOT have one
    — i.e. exactly the pre-existing population on a deployment that has not
    backfilled (`#13420` §1.2: *"the column arrives empty"*). `record_vector` is
    enabled, the record answers, and its answer is SHORT.

    Taken as the answer, that short list is the `#5090` Q2 defect with
    `mechanism=pgvector, reason=ok, degraded=False` beside it. So the coverage
    COUNT decides, and it declines: `record>=graph` is ``1 >= 3``, false. The graph
    branch answers — and it is a COMPLETE copy, because the graph write is retained
    on exactly this branch (`mirror_required` is this predicate inverted).

    ⛔ AND THE MUTANT IS DRIVEN IN THE SAME TEST. With the gate forced to "the
    record is authoritative" — the shape of a naive implementation, and of the code
    a reviewer would accept if this file only asserted the happy path — the
    graph-only row DISAPPEARS from the answer. That is what makes this a
    measurement of the mechanism rather than of the fixture.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    record_rows = [("obj-in-record", 0.10, None)]
    _stub_record(monkeypatch, count=1, rows=record_rows)

    # The graph holds ITS OWN vector for the legacy node, and the record holds one
    # for a row the graph cannot serve. 3 vector-bearing nodes vs 1 record vector.
    graph_rows = [("obj-graph-only", 1.0 / 1.5),
                  ("obj-in-both", 1.0 / 2.0),
                  ("obj-graph-only-2", 1.0 / 3.0)]
    graph = _Graph(graph_rows)
    rows, graph, trace = _run(_searcher(3), graph=graph)

    served = {row[0] for row in rows}
    assert "obj-graph-only" in served, (
        "a graph-side vector whose record row has none became INVISIBLE — the "
        "record's partial answer was rendered as the corpus (#5090 Q2)")
    assert "obj-in-record" not in served, (
        "the record's rows were fused in even though the leg declined — this leg "
        "hands the graph the remainder of the read (#11691), it does not merge")
    assert graph.queries, "the graph branch never ran on an unverified coverage"
    assert [entry.get("mechanism") for entry in trace] == ["scan_fallback"], trace

    # ── the MUTANT: coverage forced verified, i.e. the naive cutover ──────
    record_vector.reset_coverage_cache()
    _stub_record(monkeypatch, count=1, rows=record_rows)
    monkeypatch.setattr(
        record_vector, "coverage",
        lambda *a, **k: record_vector.Coverage(
            label="object", verified=True, record_vectors=1, graph_vectors=3,
            reason="forced"), raising=True)
    rows, _graph, _trace = _run(_searcher(3), graph=_Graph(graph_rows))
    assert {row[0] for row in rows} == {"obj-in-record"}, (
        "the mutant did NOT lose the graph-only row, so this test cannot tell a "
        "gated cutover from a naive one — fix the fixture before trusting it")


# ── 3. post-contract, the graph is not a fallback ─────────────────────────


def test_post_contract_a_record_failure_is_reported_and_not_answered_from_graph(
        monkeypatch):
    """Once coverage is verified, the graph copy is FROZEN — so it is not a fallback.

    The state: the record holds a vector for every vector-bearing node the graph
    has (so the graph write is off, `mirror_required` is False), and the record
    fails this read. Answering from the graph would return the population frozen at
    the contract and report `ok`: every row written since would be missing from an
    answer that claims to be the corpus.

    So the leg reports that it could not look — `degraded`, `query_failed`, no rows
    — and the graph is not queried AT ALL. The read still answers from its
    keyword/structural legs and `declared_degraded_read` calls it degraded, which
    is `#5090` Q2's *"a search finds a claim or says it could not look"*.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    _stub_record(monkeypatch, count=4, rows=[])

    def _boom(*_a, **_k):
        raise record_vector.RecordVectorError("the record went away")

    # ⛔ THROUGH THE REAL SEARCHER, not a bare raising callable: the
    # Declined/WithoutFallback distinction is made THERE, so a test that injects
    # its own `pg_vector` would assert a translation the shipping path might not
    # perform.
    monkeypatch.setattr(record_vector, "vector_search", _boom, raising=True)
    graph = _Graph([("obj-graph-only", 1.0 / 1.5)])
    rows, graph, trace = _run(_searcher(4), graph=graph)

    assert rows == [], (
        "a post-contract record failure was answered from the frozen graph cohort")
    assert graph.queries == [], (
        f"the graph branches ran after the contract: {graph.queries}")
    assert trace and trace[-1]["degraded"] is True, trace
    assert trace[-1]["reason"] == "query_failed", trace
    # …and the SAME failure BEFORE a contract is the named fallback it always was.
    record_vector.reset_coverage_cache()
    _stub_record(monkeypatch, count=1, rows=[])
    rows, graph, trace = _run(_searcher(3),
                              graph=_Graph([("obj-legacy", 1.0 / 1.5)]))
    assert [row[0] for row in rows] == ["obj-legacy"], (rows, trace)
    assert trace[-1]["mechanism"] == "scan_fallback", trace


def test_a_post_contract_failure_on_an_empty_scope_still_declares_degraded(
        monkeypatch):
    """⛔ A FAILURE IS NEVER NEUTRAL — keyed on the SCOPE too (#13610 review F2).

    The post-contract arm records `query_failed` when the record cannot answer.
    On a SCOPED read whose probe measured an EMPTY scope, the closure's
    empty-scope rule (`_scope_empty and not failure`) stamps the entry
    `VECTOR_SCOPE_EMPTY`, and `declared_degraded_read` skips neutral entries. So
    without `failure=True` the leg that could not look is laundered into "no
    scope, nothing to say", and the read reports itself healthy after answering
    from nothing — the exact laundering that rule's own comment forbids. Every
    other FAILURE record in this function passes the flag; this one did not.

    The probe answers `(0, 0)`: a MEASURED, empty scope. The scope is non-empty,
    which is what makes `_scope_empty` reachable at all.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    _stub_record(monkeypatch, count=4, rows=[])

    def _boom(*_a, **_k):
        raise record_vector.RecordVectorError("the record went away")

    # THROUGH THE REAL SEARCHER (see the case above): the
    # Declined/WithoutFallback split is made there.
    monkeypatch.setattr(record_vector, "vector_search", _boom, raising=True)

    trace: list[dict] = []
    graph = _Graph([(0, 0)])
    rows = run_vector_query(
        graph, [0.01] * 8, limit=10, is_embedded=False, entity_type="object",
        leg_trace=trace, pg_vector=_searcher(4), timeout_ms=500,
        scope_kinds=("widget",))

    assert rows == []
    assert len(graph.queries) == 1 and "count(n.embedding)" in graph.queries[0], (
        f"the fixture must build the MEASURED-empty-scope shape: {graph.queries}")
    vector_entries = [e for e in trace if e.get("leg") == "vector"]
    assert vector_entries, trace
    assert all(e.get(VECTOR_SCOPE_KEY) != VECTOR_SCOPE_EMPTY
               for e in vector_entries), (
        f"a post-contract FAILURE was stamped NEUTRAL: {vector_entries}")
    entry = declared_degraded_read(trace)
    assert entry is not None, (
        "a post-contract failure on an empty scope was filtered out as neutral, "
        f"so the read declared itself healthy after answering nothing: {trace}")
    assert entry["reason"] == "query_failed", entry
    assert [e.get("mechanism") for e in vector_entries] == ["pgvector"], (
        f"the failure must name the leg that could not answer: {vector_entries}")


def test_an_unmeasurable_graph_denominator_keeps_the_graph_branch(monkeypatch):
    """⛔ THE SUPPLIER'S FAIL-SAFE, driven through the READ path (#13610 review F6).

    `_safe_population` turns a RAISING supplier into ``None``, and ``None`` is
    UNVERIFIED — which keeps the graph branch AND the graph write. Nothing below is
    subtle; what was missing is an assertion: the guard could be changed to
    ``return 0`` and every other case in this file stayed green, because they all
    hand the coverage a NUMBER.

    Read as ``0`` the comparison becomes ``record >= 0``, i.e. VERIFIED on no
    evidence at all: the write gate stops mirroring and the read stops declining,
    for a graph nobody measured. That is the fail-open the docstring says it
    prevents, so the pin drives the raising supplier through `searcher` and asserts
    the graph answered.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    _stub_record(monkeypatch, count=3, rows=[("obj-in-record", 0.10, None)])

    def _explode(_entity_type):
        raise RuntimeError("the graph's count could not be taken")

    searcher = record_vector.searcher(
        carried=_SCOPE, timeout_ms=500, graph_population=_explode)
    graph = _Graph([("obj-graph-only", 1.0 / 1.5)])
    rows, graph, trace = _run(searcher, graph=graph)

    assert [row[0] for row in rows] == ["obj-graph-only"], (
        "an unmeasurable graph count VERIFIED coverage — the record's partial answer "
        f"was served as the corpus and the graph was never asked: {rows}")
    assert graph.queries, "the graph branch did not answer an unmeasured denominator"
    assert trace[-1]["mechanism"] == "scan_fallback", trace
    assert record_vector.coverage_verified(
        "object", record_vector._Scope(*_SCOPE)) is False


def test_a_contracted_empty_answer_is_the_answer(monkeypatch):
    """⛔ POST-CONTRACT, AN EMPTY RECORD ANSWER IS NOT A REASON TO ASK THE GRAPH.

    Three outcomes come back from the record leg and the middle one is the trap:
    ``None`` (no table serves the label), rows, and **``[]`` — a complete, honest
    "nothing matches"**. Before the contract an empty answer legitimately meant
    "the record is a fold with no backfill, ask the graph", so `run_vector_query`
    falls through on ``[]``. AFTER the contract the mirror has stopped, so the
    graph's cohort is frozen at the contract instant and that fall-through answers
    with a SMALLER population labelled `scan_fallback` — #13610's Indicator (2),
    still reachable while the fall-through stayed unconditional.

    So the searcher stamps the contracted answer (`RecordAnswer`), and both
    directions are pinned here: sealed-empty answers empty, unsealed-empty still
    falls through to the graph.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)

    # ── contracted + empty → the answer is empty, and the graph is not asked ──
    _stub_record(monkeypatch, count=3, rows=[])
    graph = _Graph([("obj-graph-only", 1.0 / 1.5)])
    rows, graph, trace = _run(_searcher(3), graph=graph)  # 3 >= 3 → verified

    assert rows == [], (
        "a contracted empty answer fell through to the graph and returned its "
        f"post-contract frozen cohort: {rows}")
    assert graph.queries == [], (
        f"the graph was asked after the contract had frozen its cohort: {graph.queries}")
    assert trace[-1]["mechanism"] == "pgvector", trace
    assert trace[-1]["degraded"] is False, trace
    assert trace[-1]["reason"] == "empty_results", trace

    # ── the PRE-contract direction, unchanged: the graph still answers ──────
    record_vector.reset_coverage_cache()
    _stub_record(monkeypatch, count=1, rows=[])  # 1 < 3 → unverified
    rows, graph, trace = _run(_searcher(3),
                              graph=_Graph([("obj-legacy", 1.0 / 1.5)]))
    assert [row[0] for row in rows] == ["obj-legacy"], (
        "an UNVERIFIED label's empty record answer stopped falling through to the "
        f"graph — that is the pre-contract answer and it is correct: {rows}")
    assert trace[-1]["mechanism"] == "scan_fallback", trace


def test_the_searcher_raises_the_no_fallback_answer_for_a_gated_label(monkeypatch):
    """The distinction is made at the SEARCHER, so no caller can flatten it.

    `RecordVectorDeclined` (graph may answer) and
    `RecordVectorWithoutFallback` (nothing may answer but the record) are different
    exceptions because their callers' consequences are OPPOSITE — the same reason
    `RecordVectorDeclined` itself is raised rather than returned as ``None``
    (`#13466` review P3). A future caller that catches `RecordVectorError` broadly
    still gets the safe behaviour; only the one that wants to tell them apart
    looks.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    _stub_record(monkeypatch, count=1, rows=[])

    def _boom(*_a, **_k):
        raise record_vector.VectorShortfall("short")

    monkeypatch.setattr(record_vector, "vector_search", _boom, raising=True)
    # unverified (1 < 5) → DECLINED, and the caller is told the graph may answer
    with pytest.raises(record_vector.RecordVectorDeclined):
        _searcher(5)("object", [0.1] * 8, 10)
    # verified (6 >= 5) → WITHOUT FALLBACK, and the caller is told nothing may
    record_vector.reset_coverage_cache()
    _stub_record(monkeypatch, count=6, rows=[])
    with pytest.raises(record_vector.RecordVectorWithoutFallback):
        _searcher(5)("object", [0.1] * 8, 10)


# ── 4. the write gate: retained while unproven, removed when proven ───────


def test_mirror_is_required_in_every_unknown_and_removed_only_on_a_verified_count(
        monkeypatch):
    """⛔ THE READ GATE AND THE WRITE GATE ARE ONE PREDICATE, INVERTED.

    If the fold kept writing after the record took over, the deployment stays in
    double-storage for ever (the target design is never reached). If it stopped
    writing before the record proved coverage, the graph-only population has no
    successor and the read that declines to the graph finds a store that is being
    starved — the row in neither store the issue forbids.

    So every unknown answers ``True`` (write it): an unarmed deployment, no DSN, no
    tenant scope on the request (the hosted lane carries its tenant BY VALUE, so an
    ambient read finds none), and a label with no verdict. Only a VERIFIED count
    for THIS tenant and label answers ``False``.
    """
    from tortoise.record_writer import use_record_tenant

    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    record_vector.reset_coverage_cache()

    with use_record_tenant(*_SCOPE):
        # 1. no verdict yet → the mirror is written.
        assert record_vector.mirror_required("object") is True
        # 2. a SHORT count → still written (this is the transition).
        monkeypatch.setattr(record_vector, "_record_vector_count",
                            lambda *a, **k: 1, raising=True)
        short = record_vector.coverage("object", record_vector._Scope(*_SCOPE),
                                       graph_vectors=3)
        assert short.verified is False and short.reason
        assert record_vector.mirror_required("object") is True
        # 3. a satisfied count → the graph copy is retired for this label.
        record_vector.reset_coverage_cache()
        monkeypatch.setattr(record_vector, "_record_vector_count",
                            lambda *a, **k: 3, raising=True)
        ok = record_vector.coverage("object", record_vector._Scope(*_SCOPE),
                                    graph_vectors=3)
        assert ok.verified is True
        assert record_vector.mirror_required("object") is False
        # 4. the OTHER gated label is decided separately — a per-label verdict
        #    must not leak across labels (`:Object` and `:Subject` backfill
        #    independently).
        assert record_vector.mirror_required("subject") is True
        # 5. a label the issue does not retire is untouched.
        assert record_vector.mirror_required("event") is True

    # 6. no tenant scope on the request → the mirror is written (fail-safe).
    assert record_vector.mirror_required("object") is True, (
        "with no tenant on the request the record cannot be addressed, so the "
        "graph write must be kept — the hosted lane carries its tenant by VALUE "
        "and an ambient read finds none")

    # 7. the ROLLBACK restores the write even with a verified verdict cached.
    monkeypatch.setenv(record_vector.ARMING_ENV, "0")
    with use_record_tenant(*_SCOPE):
        assert record_vector.mirror_required("object") is True


def test_an_unmeasurable_graph_count_never_verifies_coverage(monkeypatch):
    """⛔ AN ABSENCE IS NOT A VERDICT, on the denominator too.

    ``graph_vectors=None`` is "nobody measured", and reading it as ``0`` would
    verify coverage on no evidence at all — retiring the graph copy for a store
    whose graph could not be counted. So it is UNVERIFIED, which keeps the graph
    branch and the graph write.

    Same for the RECORD side's count, which is why `_record_vector_count` answers
    ``None`` for a failure rather than ``0``.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    record_vector.reset_coverage_cache()
    scope = record_vector._Scope(*_SCOPE)
    verdict = record_vector.coverage("object", scope, graph_vectors=None)
    assert verdict.verified is False and verdict.record_vectors is None

    record_vector.reset_coverage_cache()
    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda *a, **k: None, raising=True)
    verdict = record_vector.coverage("object", scope, graph_vectors=0)
    assert verdict.verified is False, (
        "an unmeasurable record count verified coverage — the graph would be "
        "retired on a failed read")
    assert record_vector.coverage_verified("object", scope) is False


def test_a_post_contract_drop_does_not_silently_flip_the_read_back(monkeypatch):
    """The contract is ONE-WAY: a later count cannot flip the read back.

    Post-contract the graph copy is frozen, so flipping the read back to it would
    return a smaller cohort while looking like a healthy read. A graph that gains a
    vector-bearing node after the contract (`record-restore`'s fill, a backup
    restore) therefore cannot move the verdict.

    ⛔ AND IT IS NOT RE-MEASURED, WHICH IS A STATED RESIDUAL (§ "Honest gaps" of the
    PR): `coverage()` returns a verified verdict before it takes a count, so the
    divergence is undetected until the process restarts. An earlier cut of this
    change carried a `warning()` for the gain case, and it was UNREACHABLE — the
    early return always came first — so it was deleted rather than left as an alarm
    an operator could trust (`#11660` round 6's "a false True muted the alarm",
    of which an unreachable warning is the same shape). What the pin asserts is the
    behaviour that actually ships: sticky verdict, NO count on the second call.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    record_vector.reset_coverage_cache()
    scope = record_vector._Scope(*_SCOPE)
    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda *a, **k: 5, raising=True)
    assert record_vector.coverage("object", scope, graph_vectors=5).verified

    # The graph gains nodes (9 vector-bearing) and the record has not caught up.
    calls: list[int] = []

    def _count(*a, **k):
        calls.append(1)
        return 5

    monkeypatch.setattr(record_vector, "_record_vector_count", _count, raising=True)
    after = record_vector.coverage("object", scope, graph_vectors=9)
    assert after.verified is True, (
        "a post-contract short count flipped the read back to a FROZEN graph copy "
        "— the rows written since the contract exist only in the record")
    assert calls == [], (
        "a verified verdict was re-measured; the contract is one-way and the count "
        "costs a record round trip on the read path")


def test_a_verified_verdict_survives_its_ttl_but_an_unverified_one_does_not(
        monkeypatch):
    """The cache's two directions, which are not symmetric.

    Expiring an UNVERIFIED verdict costs one count and keeps the graph branch, so
    it expires normally. Expiring a VERIFIED one would let a transient read error
    (an unmeasurable count, a graph hiccup) hand the next read a frozen graph copy
    as if it were the corpus — so the positive verdict stands for the life of the
    process.

    ⛔ BOTH DIRECTIONS ARE DRIVEN, because each one alone passes vacuously (#13610
    review). The first cut of this case seeded a VERIFIED verdict and asked only
    that half while its name claimed both — and the expiry clause it was named for
    (`_cached_coverage`'s `and not verdict.verified`) is the ONLY mechanism by which
    a deployment that finishes a backfill ever re-verifies, so nothing reddened when
    it was replaced with `if False:`. Measured then: 54 passed.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    record_vector.reset_coverage_cache()
    scope = record_vector._Scope(*_SCOPE)
    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda *a, **k: 2, raising=True)
    record_vector.coverage("object", scope, graph_vectors=2)
    assert record_vector.coverage_verified("object", scope) is True
    # A MUTABLE offset, so the clock can be jumped BETWEEN a seed and a read — the
    # only shape that exercises an expiry. A fixed jump applied before both makes
    # the entry's age zero and the pin vacuous in the other direction.
    real_monotonic = record_vector.time.monotonic
    offset = [0.0]
    monkeypatch.setattr(record_vector.time, "monotonic",
                        lambda: real_monotonic() + offset[0], raising=True)
    offset[0] = 10_000.0
    assert record_vector.coverage_verified("object", scope) is True, (
        "the verified verdict expired — a later failed count would demote the read "
        "to a frozen graph copy")

    # ── the OTHER direction: an UNVERIFIED verdict EXPIRES ───────────────────
    # Seeded at the un-jumped clock, then read after the jump — a label the record
    # does not yet cover (a record/graph deficit) is cached UNVERIFIED, and a
    # backfill that lands afterwards can only be seen if the entry expires.
    offset[0] = 0.0
    counts: list[int] = []
    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda *a, **k: counts.append(1) or 1, raising=True)
    assert record_vector.coverage("point", scope, graph_vectors=4).verified is (
        False), "the fixture did not seed an UNVERIFIED verdict"
    assert counts == [1], counts
    assert record_vector.coverage_verified("point", scope) is False
    # The backfill lands; the next miss must MEASURE again and believe the answer.
    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda *a, **k: counts.append(1) or 9, raising=True)
    offset[0] = 10_000.0
    assert record_vector.coverage("point", scope, graph_vectors=4).verified is True, (
        "the UNVERIFIED verdict did not expire: a deployment that finished its "
        "backfill would serve the cached short verdict for the life of the process "
        "and never converge — no label would ever cut over, and nothing says so")
    assert len(counts) == 2, (
        f"the expired verdict was served instead of re-measured: counts={counts}")


# ── 4b. the cost model: the counts are taken on a MISS, and only then ─────


def test_a_cached_verdict_takes_no_count_at_all(monkeypatch):
    """The gate must be priced as ONE aggregate per TTL, not one PER READ.

    ⛔ The shape this pins out is the one that shipped in the first cut and that two
    reviewers measured: `graph_population` invoked at the CALL SITE, before
    `coverage()` could consult its cache — so a full-label graph aggregate
    (`MATCH (n:Object) WHERE n.embedding IS NOT NULL RETURN count(n)`) ran on EVERY
    dense goals/people read, forever, including after the contract when the answer
    could not change. Against this repo's own measurement of that scan (34 ms p50 at
    rest, 133 ms under load) inside a 500 ms read budget, that is the #13108 cost
    class, re-introduced by the gate that was supposed to be cheap.

    Both directions are pinned, because either one alone can pass vacuously: the
    VERIFIED case (no count, ever — the sticky verdict) and the UNVERIFIED case (one
    count per TTL, not per read).
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    record_vector.reset_coverage_cache()
    scope = record_vector._Scope(*_SCOPE)

    populations: list[str] = []
    record_counts: list[int] = []

    def _population(label):
        populations.append(label)
        return 3

    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda *a, **k: record_counts.append(1) or 3, raising=True)
    first = record_vector.coverage("object", scope, graph_population=_population)
    assert first.verified is True
    assert len(populations) == 1 and len(record_counts) == 1, (
        "the first (MISSING) verdict did not take exactly one count each")

    for _ in range(4):
        assert record_vector.coverage(
            "object", scope, graph_population=_population).verified is True
    assert len(populations) == 1, (
        f"the graph's full-label aggregate ran {len(populations)} times for 5 dense "
        f"reads — the supplier is being asked BEFORE the cache is consulted")
    assert len(record_counts) == 1, (
        f"the record's aggregate ran {len(record_counts)} times for 5 dense reads")

    # UNVERIFIED: one measurement per TTL, not one per read. Otherwise the state
    # every deployment starts in pays a record round trip on every read.
    record_vector.reset_coverage_cache()
    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda *a, **k: record_counts.append(1) or 1, raising=True)
    record_counts.clear()
    for _ in range(5):
        assert record_vector.coverage(
            "object", scope, graph_population=_population).verified is False
    assert len(record_counts) == 1, (
        f"an UNVERIFIED verdict was re-measured {len(record_counts)} times in 5 "
        f"reads: the TTL is not bounding anything, and this is the state every "
        f"deployment is in until it has backfilled")


def test_every_label_the_record_serves_is_gated():
    """`#13610`'s read gate is EVERY served label, and the narrow cut was wrong.

    The two-label set ({object, subject}) was justified as "only these are derived
    in BOTH stores". The tree says otherwise (`_upsert_event` / `_upsert_document`
    DERIVE their graph vector exactly as the two folds do), and the justification is
    not what decides the gate anyway: since the arm became the DEFAULT the record is
    the first dense source for all seven labels, and the record is a fold with NO
    backfill — so `point` (the primary retrieval label), `operator`, `event` and
    `document` would be answered from a possibly-short record with the trace reading
    `mechanism=pgvector, reason=ok, degraded=False`. That is `#5090` Q2 on the
    default posture.

    Gating costs a count and DECLINES the label to the graph while the count is
    short, and the graph is complete precisely because the mirror is still written —
    so the wide set is the safe direction and the narrow one is the silent one.
    """
    assert set(record_vector.COVERAGE_GATED_LABELS) == set(record_vector.LABEL_SERVED), (
        "a label the record serves is not gated, so it is answered from the record "
        "with no coverage proof: "
        f"{sorted(set(record_vector.LABEL_SERVED) - set(record_vector.COVERAGE_GATED_LABELS))}")
    for label in ("point", "operator", "event", "document", "source"):
        assert label in record_vector.COVERAGE_GATED_LABELS


def test_a_verified_label_whose_fold_still_writes_the_graph_keeps_the_fallback(
        monkeypatch):
    """⮔ THE CONTRACT IS ONLY WHERE THE GRAPH COPY IS ACTUALLY FROZEN (#13617 F1).

    `contracted` claims a PREMISE — *the graph copy stopped being maintained at the
    contract, so a graph answer is a SMALLER cohort presented as the answer* — and
    that premise is produced by exactly one thing: the fold consulting
    `record_vector.mirror_required` before it encodes. Only
    `_upsert_subject`/`_upsert_object` do (`projection/entities.py:4906`, `:5112`),
    so only they have a frozen graph. `point` — the PRIMARY retrieval label — is
    still mirrored unconditionally, so on a record blip the graph holds a COMPLETE,
    still-fed copy and refusing it empties the dense leg on the DEFAULT posture
    (where `main` answered from that same copy).

    Both directions are pinned, and the MUTANT IS DRIVEN: widening the retired set
    to include `point` — the pre-fix code's `contracted = True` for every gated
    label, in its narrowest form — makes the same row vanish, which is what makes
    this a measurement of the mechanism rather than of the fixture.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    assert "point" in record_vector.COVERAGE_GATED_LABELS
    assert "point" not in record_vector.MIRROR_RETIRED_LABELS

    def _boom(*_a, **_k):
        raise record_vector.RecordVectorError("the record went away")

    # coverage VERIFIED for `point` (3 >= 3), and the record leg then fails.
    _stub_record(monkeypatch, count=3, rows=[])
    monkeypatch.setattr(record_vector, "vector_search", _boom, raising=True)
    rows, graph, trace = _run(_searcher(3), entity_type="point",
                              graph=_Graph([("point-graph-only", 1.0 / 1.5)]))

    assert [row[0] for row in rows] == ["point-graph-only"], (
        "a VERIFIED `point` read whose record leg failed answered nothing — the "
        "no-fallback contract was applied to a label whose fold still writes the "
        f"graph copy, so the dense leg for the primary label went empty: {rows}")
    assert graph.queries, "the graph branch never ran for a still-mirrored label"
    assert trace[-1]["mechanism"] == "scan_fallback", trace
    assert trace[-1]["degraded"] is False, trace

    # ── the MUTANT: the contract widened to every gated label ──────────────
    record_vector.reset_coverage_cache()
    _stub_record(monkeypatch, count=3, rows=[])
    monkeypatch.setattr(record_vector, "MIRROR_RETIRED_LABELS",
                        frozenset({"object", "subject", "point"}), raising=True)
    rows, _graph, trace = _run(_searcher(3), entity_type="point",
                               graph=_Graph([("point-graph-only", 1.0 / 1.5)]))
    assert rows == [], (
        "the mutant did NOT lose the still-mirrored graph row, so this test cannot "
        "tell the narrowed contract from the wide one — fix the fixture before "
        "trusting it")
    assert trace[-1]["reason"] == "query_failed", trace

    # ── and the VERIFIED `object` direction is unchanged: still no fallback ──
    record_vector.reset_coverage_cache()
    _stub_record(monkeypatch, count=3, rows=[])
    rows, graph, _trace = _run(_searcher(3), graph=_Graph([("obj-graph-only", 1.0 / 1.5)]))
    assert rows == [], "a contracted `object` read recovered its frozen cohort"
    assert graph.queries == [], "the graph was queried after `object`'s contract"


def test_the_no_fallback_arm_covers_exactly_the_labels_the_folds_stop_writing():
    """⮔ THE SET IS PINNED AGAINST THE FOLDS, not restated (#13617 F1).

    `record_vector.MIRROR_RETIRED_LABELS` says which labels' graph copy the folds
    really stop writing, and the read's no-fallback arm is narrowed to it. The
    membership is produced by the write-gate call sites in
    `projection/entities.py` — `_derived_mirror_required(<label>)`, or a direct
    `record_vector.mirror_required(<label>)` — so it is read off THAT FILE rather
    fallback to a graph copy nobody maintains (the `#5090` Q2 direction), and a
    fold silently un-wired would leave its label refusing a complete copy (the
    direction this test was written for).

    Also asserts the two sets relate correctly: the CONTRACT is never wider than
    the GATE — a label that may not be refused is not a label that may be sealed.

    ⛔ THE PARSE COVERS BOTH SPELLINGS OF THE GATE, because the helper is not the
    only door: `projection/entities.py` can import `record_vector.mirror_required`
    and consult it directly. Matching `_derived_mirror_required` alone would leave
    such a fold INVISIBLE — green over a label whose mirror stopped being written
    while `contracted` still refused its graph copy. The one non-literal call
    (`_derived_mirror_required`'s own delegation of its parameter, `entities.py:104`)
    is exempted by name and shape, so a third fold written that way still reddens.
    """
    tree = ast.parse(_ENTITIES.read_text(encoding="utf-8"))
    wired: set[str] = set()

    def _walk(node: ast.AST, scope: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = child.name
            if isinstance(child, ast.Call):
                func = child.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name in ("_derived_mirror_required", "mirror_required"):
                    literal = (
                        len(child.args) == 1
                        and isinstance(child.args[0], ast.Constant)
                        and isinstance(child.args[0].value, str)
                    )
                    delegating = (
                        name == "mirror_required"
                        and inner == "_derived_mirror_required"
                        and len(child.args) == 1
                        and isinstance(child.args[0], ast.Name)
                        and child.args[0].id == "entity_type"
                    )
                    assert literal or delegating, (
                        "a write-gate call site passes a non-literal argument and is "
                        "not the helper's own delegation, so the retired-label set "
                        f"cannot be derived from it: {ast.unparse(child)}")
                    if literal:
                        wired.add(child.args[0].value)
            _walk(child, inner)

    _walk(tree, None)

    assert wired, (
        "no write-gate call site (`_derived_mirror_required(<label>)` or "
        "`mirror_required(<label>)`) was found in projection/entities.py "
        "— either the write gate was removed (the graph copy is written for ever) "
        "or it was renamed, and `MIRROR_RETIRED_LABELS` is now unbacked")
    assert wired == set(record_vector.MIRROR_RETIRED_LABELS), (
        "the no-fallback arm and the folds' write gate disagree — `contracted` "
        f"claims a frozen graph for {sorted(record_vector.MIRROR_RETIRED_LABELS)} "
        f"while the folds stop writing {sorted(wired)}")
    assert set(record_vector.MIRROR_RETIRED_LABELS) <= set(
        record_vector.COVERAGE_GATED_LABELS), (
        "the contract covers a label the coverage gate does not, so a label could "
        "be sealed with no coverage proof at all: "
        f"{sorted(set(record_vector.MIRROR_RETIRED_LABELS) - set(record_vector.COVERAGE_GATED_LABELS))}")


def test_the_two_denominators_are_stated_as_a_comparison_and_not_a_membership_proof():
    """What `Coverage` claims, so a reader does not read a count as a set proof.

    `record_vectors >= graph_vectors` compares SIZES: it cannot see a
    cardinality-preserving mismatch (a graph node whose record row is missing, offset
    by a record row whose graph node is gone). #13610's step 2 is a COVERAGE claim and
    this is the bound on what it proves — recorded here because a docstring that said
    "the two populations are equal" would be false, and the next reader would build
    on it.
    """
    doc = (record_vector.Coverage.__doc__ or "").lower()
    assert "membership proof" in doc, (
        "the Coverage docstring must state the count/membership boundary explicitly: "
        "a reader who takes `record_vectors >= graph_vectors` as a set proof builds "
        "on something it does not establish")
    assert "residual" in doc, (
        "the divergent-membership shapes must be recorded as residuals, not left for "
        "the next lane to rediscover")
    assert "name" in doc and "id" in doc, (
        "the graph-key/record-key asymmetry (id vs name) is the residual that makes "
        "coverage unreachable for a duplicate-name label — it belongs in the docstring")


def test_the_gate_is_a_size_comparison_by_construction(monkeypatch):
    """The residual's BOUND, pinned as the SHAPE that makes it one.

    ⛔ THE CASE THIS REPLACES PROMISED A DRIVEN MISMATCH AND DROVE NONE (#13610
    review F3). It handed `coverage` the literal ``7`` on BOTH sides and asserted
    the verdict — but ``7 >= 7`` holds under every candidate implementation
    (``>=``, ``==``, a membership check), and the function never receives a
    population to compare. Its name claimed the residual was driven; the body
    re-asserted the happy path already covered by the cached-verdict case, so no
    mutation of the diff could redden it.

    What IS pinnable at this layer is the comparison the design actually is, and
    the two facts below are what a membership implementation would have to break:

    * the boundary is ``record >= graph``, ONE-SIDED by design — an equal or larger
      record verifies, a SHORT one never does (``==`` reddens the ``8 vs 7`` case,
      ``<=`` the ``6 vs 7`` one). This is the direction the issue's no-silent-loss
      rule depends on;
    * the surface carries COUNTS and no identities, so no implementation reachable
      through it can compare membership. That is the honest statement of the
      residual, whose prose lives in `Coverage.__doc__` (pinned by the case above).

    ⚠️ The OTHER stated residual — the graph MERGEs on `id` while the record keys on
    `name`, so two nodes sharing a name can hold the verdict below what the record
    can reach — is not pinnable at this layer either: it needs a graph with
    duplicate names and a record that folds them to one row, which is a store-level
    fixture rather than a property of this function. `chk_object_node_key_is_contract_key`
    is its schema-side pin.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    scope = record_vector._Scope(*_SCOPE)

    for record_vectors, expected in ((7, True), (8, True), (6, False)):
        record_vector.reset_coverage_cache()
        monkeypatch.setattr(record_vector, "_record_vector_count",
                            lambda *a, _n=record_vectors, **k: _n, raising=True)
        verdict = record_vector.coverage("object", scope, graph_vectors=7)
        assert verdict.verified is expected, (
            f"record={record_vectors} graph=7 answered verified="
            f"{verdict.verified}; the gate is `record >= graph` and a SHORT record "
            "must never verify")

    params = set(inspect.signature(record_vector.coverage).parameters)
    identities = {"ids", "keys", "members", "nodes", "rows"}
    assert "graph_vectors" in params and not (identities & params), (
        "`coverage` grew an identity-bearing parameter — the gate could then "
        "compare membership, and `Coverage`'s stated residual would be wrong: "
        f"{sorted(params)}")
    fields = set(record_vector.Coverage.__dataclass_fields__)
    assert not (identities & fields), (
        f"the verdict grew an identity-bearing field: {sorted(fields)}")


# ── 5. the folds: no encode, no write ────────────────────────────────────


@pytest.fixture
def proj():
    tmpdir = tempfile.mkdtemp(prefix="graphvec13610_")
    p = FalkorProjection(os.path.join(tmpdir, "graphvec.db"), graph_name="tortoise")
    try:
        yield p
    finally:
        p.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _embedding_of(g, label: str, key: str):
    rows = g.query(
        f"MATCH (n:{label} {{id: $key}}) RETURN n.embedding",
        params={"key": key}).result_set
    return rows[0][0] if rows else None


def _install_verified(monkeypatch, label: str, count: int = 3, graph=3,
                      *, reset: bool = True) -> None:
    from tortoise.record_writer import use_record_tenant

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    if reset:
        record_vector.reset_coverage_cache()
    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda *a, **k: count, raising=True)
    with use_record_tenant(*_SCOPE):
        verdict = record_vector.coverage(
            label, record_vector._Scope(*_SCOPE), graph_vectors=graph)
        assert verdict.verified is True, verdict


def test_the_folds_do_not_encode_or_write_once_the_record_owns_the_vector(
        proj, monkeypatch):
    """⛔ ITEM 1 OF THE ISSUE, MEASURED ON A REAL GRAPH, IN BOTH DIRECTIONS.

    Unproven coverage: the fold encodes `name` and the node carries the vector
    (today's behaviour, and the transitional state this change keeps). Verified
    coverage: the encode is never CALLED — not called-and-discarded — and the node
    carries none.

    ⚠️ Nothing is DELETED either way: `set_embedding_clause` writes no property for
    a NULL vector and the `clear_embedding` pre-REMOVE is emitted only when a vector
    will be written, so an existing node's stored vector is untouched. That is the
    issue's "no data deletion" constraint, and the third case pins it **with the
    mirror RETIRED** — the only arrangement in which those two guards are
    load-bearing rather than masked by the fold's own re-write.
    """
    from tortoise import derived_embedding
    from tortoise.record_writer import use_record_tenant

    calls: list[str] = []
    vector = [0.05] * _DIM
    monkeypatch.setattr(derived_embedding, "encode",
                        lambda text, dim, **kw: (calls.append(text), vector)[1],
                        raising=True)

    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    record_vector.reset_coverage_cache()

    # ── unproven: the mirror is written (option 2's retained half) ───────
    with use_record_tenant(*_SCOPE):
        proj._upsert_subject({"id": "sub-13610-a", "name": "alice"})
    assert calls == ["alice"], calls
    assert _embedding_of(proj.g, "Subject", "sub-13610-a") is not None

    # ── verified: no encode, no write ───────────────────────────────────
    _install_verified(monkeypatch, "subject")
    _install_verified(monkeypatch, "object", reset=False)
    with use_record_tenant(*_SCOPE):
        proj._upsert_subject({"id": "sub-13610-b", "name": "bob"})
        proj._upsert_object({"id": "obj-13610-b", "name": "widget"})
    assert calls == ["alice"], (
        f"the fold still encoded after the record took ownership: {calls}")
    assert _embedding_of(proj.g, "Subject", "sub-13610-b") is None, (
        "the graph stored a copy its record already owns")
    assert _embedding_of(proj.g, "Object", "obj-13610-b") is None

    # ── NO DELETION on an existing node — driven with the mirror RETIRED ──
    # ⛔ The ARRANGEMENT is the claim, and it is why this block is no longer the
    # shape it used to be (`#13610` review F1). Resetting the coverage cache
    # RE-ARMS the mirror, so the fold re-encodes and REWRITES the vector: the
    # property is present because it was just written, and neither guard
    # (`clear_embedding`'s `vector is None` no-op, `set_embedding_clause`'s
    # `ELSE s.embedding`) is reached. Coverage for `subject` is VERIFIED by case 2
    # and a verified verdict is sticky, so this fold takes the mirror-RETIRED
    # path: no encode, and the MERGE is handed a NULL vector for a node that
    # already CARRIES one. That is the only arrangement in which dropping either
    # guard deletes stored data — `preserve_on_null=False` writes NULL into the
    # ON MATCH clause, and removing `clear_embedding`'s early return fires its
    # REMOVE — so it is the only one that pins the issue's `⛔ No data deletion`.
    assert _embedding_of(proj.g, "Subject", "sub-13610-a") is not None, (
        "fixture: the transitional case above must leave a stored vector to lose")
    with use_record_tenant(*_SCOPE):
        proj._upsert_subject({"id": "sub-13610-a", "name": "alice"})
    assert calls == ["alice"], (
        f"the fold encoded with the mirror RETIRED: {calls}")
    assert _embedding_of(proj.g, "Subject", "sub-13610-a") is not None, (
        "a re-fold with the mirror RETIRED deleted a stored graph vector — "
        "this change removes no data")


def test_the_object_fold_is_gated_too(proj, monkeypatch):
    """A gate on `_upsert_subject` alone would leave half the issue in place.

    The two folds are separate code with the same rule, and the issue names both.
    """
    from tortoise import derived_embedding
    from tortoise.record_writer import use_record_tenant

    calls: list[str] = []
    monkeypatch.setattr(derived_embedding, "encode",
                        lambda text, dim, **kw: (calls.append(text), [0.05] * _DIM)[1],
                        raising=True)
    _install_verified(monkeypatch, "object", reset=False)
    with use_record_tenant(*_SCOPE):
        proj._upsert_object({"id": "obj-13610-c", "name": "gadget"})
    assert calls == [], calls
    assert _embedding_of(proj.g, "Object", "obj-13610-c") is None


# ── 6. the wiring and the SQL shapes ─────────────────────────────────────


def test_the_sdk_door_installs_the_carried_tenant_so_the_mirror_retires_on_the_hosted_lane(
        tmp_path, monkeypatch):
    """⛔ THE WRITE GATE COULD NOT SEE THE CARRIED TENANT (#13610 review, P1).

    `mirror_required` resolves the tenant through
    `record_writer.record_tenant_effective`, whose ambient door is the
    `use_record_tenant` ContextVar. The MCP lane installs it per request; the
    hosted REST lane deliberately does NOT (its auth seam is a plain dependency —
    `record_writer.use_record_tenant`'s own docstring), it carries the tenant BY
    VALUE on the SDK (`_bind_record_tenant`). A FOLD has no SDK handle, so on that
    lane the write gate answered "no tenant" on every write and kept the graph
    mirror FOR EVER — while the READ half of the same request cut over, which is
    the double storage this issue exists to end, on the lane built for
    multi-tenancy.

    The door closes it by installing what it holds for the length of ONE graph
    write. Both halves are pinned here, because either alone can pass vacuously:

    1. the SDK's scope makes the gate answer ``False`` (mirror retired), while
       outside it the same cached verdict answers ``True`` — that is the pre-fix
       behaviour and it is why step 2 is a behaviour change, not a rename;
    2. a real `add_object` through the SDK's own write door encodes NOTHING once
       coverage is verified, with no ambient scope anywhere on the request.
    """
    from tortoise import derived_embedding
    from tortoise.sdk import TortoiseSDK

    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    _install_verified(monkeypatch, "object")

    # 1. the carrier, asked directly: no ambient scope on this request at all.
    sdk = TortoiseSDK(str(tmp_path / "graphvec_hosted.db"))
    try:
        sdk._bind_record_tenant(*_SCOPE)
        assert record_vector.mirror_required("object") is True, (
            "with no tenant carrier visible, the gate must keep the mirror — this "
            "is the conservative answer and the pre-fix behaviour on this lane")
        with sdk._record_tenant_scope():
            assert record_vector.mirror_required("object") is False, (
                "the SDK's carried tenant did not reach the write gate, so the "
                "graph mirror is kept for ever on the hosted REST lane")

        # 2. the same answer through the REAL fold, on a real graph.
        calls: list[str] = []
        monkeypatch.setattr(
            derived_embedding, "encode",
            lambda text, dim, **kw: (calls.append(text), [0.05] * _DIM)[1],
            raising=True)
        # The record seam itself is not this test's subject (it would open a
        # socket): the fold runs under the write door, the projection write is
        # stubbed out.
        monkeypatch.setattr(sdk, "_write_record_projection",
                            lambda *a, **k: None, raising=True)
        sdk.create_entity("object", "gadget",
                          _server_id="obj-13610-hosted")
        assert calls == [], (
            "the SDK write door did not carry its tenant into the fold, so the "
            f"graph copy was encoded and written on the hosted lane: {calls}")
        assert _embedding_of(sdk._get_proj().g, "Object",
                             "obj-13610-hosted") is None
    finally:
        sdk.close()


def test_an_unmeasurable_or_unknown_denominator_is_None_and_never_zero(proj, monkeypatch):
    """The two fail-CLOSED guards in `_label_vector_count`, which had no assertion.

    ⛔ ``None`` IS NOT ``0``, and this is the method's central safety claim
    (`record_vector.coverage` treats an unmeasurable denominator as UNVERIFIED and
    keeps the graph branch and the graph write; `0` would VERIFY coverage on a
    failed measurement and retire the graph for a store that still needs it). The
    only exercise was the happy path (`== 3`), so `except Exception: return 0`
    measured 23 passed. Both arms are driven here:

    * a driver error while counting is ``None``;
    * an unparseable/absent row is ``None``;
    * a label that is NOT a known entity type answers ``None`` WITHOUT issuing a
      statement — `entity_label` capitalises rather than validates, so this branch
      is the only barrier against a caller-supplied string being spliced into
      ``MATCH (n:{label})``.
    """
    class _Boom:
        def query(self, sql, params=None, timeout=None):
            raise RuntimeError("the graph door is gone")

    monkeypatch.setattr(proj, "g", _Boom(), raising=True)
    assert proj._label_vector_count("object", timeout_ms=500) is None, (
        "a FAILED graph measurement answered a number — `0` would verify coverage "
        "on the failure and retire the graph for a store that still needs it")

    class _Empty:
        def query(self, sql, params=None, timeout=None):
            return _Result([])

    monkeypatch.setattr(proj, "g", _Empty(), raising=True)
    assert proj._label_vector_count("object", timeout_ms=500) is None, (
        "an empty result set answered a number; an absence is not a zero")

    statements: list[str] = []

    class _Recorder:
        def query(self, sql, params=None, timeout=None):
            statements.append(sql)
            return _Result([(3,)])

    monkeypatch.setattr(proj, "g", _Recorder(), raising=True)
    hostile = "Object`) DETACH DELETE n //"
    assert proj._label_vector_count(hostile, timeout_ms=500) is None, (
        "an unknown entity type was measured — `entity_label` capitalises rather "
        "than validating, so this must answer None, never splice the string")
    assert statements == [], (
        f"the label was spliced into a graph statement: {statements}")
    # …and the known label still measures, so the pin above is about validation
    # rather than about the door refusing everything.
    assert proj._label_vector_count("object", timeout_ms=500) == 3


def test_the_graph_denominator_is_bounded_by_the_reads_own_budget(proj, monkeypatch):
    """The denominator runs on the dense read path, so it carries the read's budget.

    `:Object`/`:Subject` have no graph vector index (`#4997`), so this count is a
    full-label scan taken on the gate's miss path. Left unbounded it runs on the
    door's default while every other graph statement on that path is bounded by
    `run_vector_query`'s `timeout=_graph_timeout_ms` — the same 30x mismatch
    `#11691` closed for the record half of this gate. The supplier is handed the
    budget by `_record_vector_searcher`, and this pins that it reaches the query.
    """
    seen: list[int | None] = []

    class _Recorder:
        def query(self, sql, params=None, timeout=None):
            seen.append(timeout)
            return _Result([(3,)])

    monkeypatch.setattr(proj, "g", _Recorder(), raising=True)
    assert proj._label_vector_count("object", timeout_ms=500) == 3
    assert seen == [500], (
        f"the graph's coverage aggregate ran with timeout={seen} — it must carry "
        "the read's own budget, like every other graph statement on that path")


def test_every_searcher_construction_site_passes_a_graph_population():
    """⛔ THE WIRING, because an unwired caller is silently pre-#13610.

    With no ``graph_population`` the gated labels cannot be verified, so they
    decline for ever: the deployment keeps double-storage and the target design is
    never reached — with every predicate test in this file still green. So the
    construction sites are pinned by AST rather than trusted.
    """
    tree = ast.parse(_SDK.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and getattr(node.func, "id", None) == "_record_vector_searcher"]
    assert len(calls) >= 3, (
        "expected the three shipped construction sites (the fused read, C2's "
        f"expansion pass, the cross-lens pool), found {len(calls)}")
    for call in calls:
        keyword = next((kw for kw in call.keywords if kw.arg == "projection"), None)
        assert keyword is not None, ast.unparse(call)
        # ⛔ A WIRED SITE, NOT A SPELLED KEYWORD (#13610 review). This pin used to
        # assert only that the NAME was present, so `projection=None` at any site
        # left it — and every runtime case — green, while the gated labels declined
        # for ever and the deployment kept double-storage: the exact failure the
        # factory's docstring claims the wiring prevents. A literal is never a
        # wiring; the value must be a real expression (the projection the read
        # already holds).
        assert not isinstance(keyword.value, ast.Constant), (
            f"the call at sdk.py:{call.lineno} passes `projection="
            f"{ast.unparse(keyword.value)}` — a literal wires nothing, so the "
            f"graph denominator is never measured and every gated label declines "
            f"for ever with no test red")
    # …and the factory forwards it, or the wiring above is decorative.
    factory = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "_record_vector_searcher")
    source = ast.unparse(factory)
    assert "graph_population=population" in source, source[-800:]
    assert "_label_vector_count" in source, source[-800:]


def test_the_graph_denominator_counts_only_vector_bearing_nodes():
    """The denominator is the graph's VECTOR-BEARING population, and it is load-bearing.

    Counting every node of the label would make the contract unreachable on a
    store with any deliberately vectorless row — `#5008`'s identifier-only text
    exists in BOTH stores, and a name-stub from `_event_plain_merge` is vectorless
    by construction — so the deployment would sit in double-storage for ever with
    no defect to point at. Counting only vector-bearing nodes makes the two sides
    the same population.
    """
    source = (_REPO_ROOT / "tortoise" / "projection" / "__init__.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "_label_vector_count")
    body = ast.unparse(method)
    assert "n.embedding IS NOT NULL" in body, body
    assert "count(n)" in body, body


def test_the_numerator_applies_the_labels_kind_predicate():
    """`operator` ⊂ `point` and `document` ⊂ `source`, so the count must be filtered.

    ⛔ THE DEFECT THIS PINS (#13630). The SERVE applies the label's `kind_filter`
    (`vector_search`: `if kind_filter: where.append(kind_filter)`), so a numerator
    taken over the whole table compares a KIND's served rows against the PARENT
    table's total: a record holding only plain points would verify `operator` on a
    surplus of plain points, and the operator-scoped read would then be answered
    from the record with zero rows of that kind, reported `mechanism=pgvector,
    reason=ok`. `record ≥ graph` is the expected steady state once the record (truth)
    is ahead of the graph (cache), so the surplus is not exotic.
    """
    sql: list[str] = []

    class _Cur:
        def execute(self, statement, params=None):
            sql.append(statement)

        def fetchone(self):
            return (3,)

        def close(self):
            return None

    import tortoise.record_projection as rp
    import tortoise.record_serve as rs

    class _Conn:
        def close(self):
            return None

    # the door is patched for the length of the call only
    real_open = rs.open_record
    real_scope = rp.scope_connection
    try:
        rs.open_record = lambda *a, **k: _Conn()
        rp.scope_connection = lambda c, org: _Cur()
        for label in ("operator", "document", "object"):
            sql.clear()
            record_vector._record_vector_count(
                label, record_vector._Scope(*_SCOPE), record_dsn=None,
                timeout_ms=None)
            assert len(sql) == 1, sql
            predicate = record_vector.LABEL_SERVED[label][2]
            if predicate:
                assert predicate in sql[0], (
                    f"`{label}`'s count does not apply its own kind predicate "
                    f"(`{predicate}`), so the parent table's rows count for it: "
                    f"{sql[0]}")
            else:
                assert "is_operator" not in sql[0] and "document_kind" not in sql[0]
    finally:
        rs.open_record = real_open
        rp.scope_connection = real_scope


def test_the_operator_denominator_is_the_whole_Point_label_so_the_gate_can_never_verify(
        monkeypatch):
    """F1 (round 13): `operator` is UNREACHABLE, not "merely harder".

    ⛔ THE CLAIM THIS PINS, AND THE ONE IT RETIRES. `Coverage`'s docstring used to
    exempt `operator` from the UNREACHABLE set with *"`point`'s other members are
    not vector-bearing by default"* — which is inverted. `security.ENTITY_TYPE_LABELS["operator"]
    == "Point"`, so the DENOMINATOR is every vector-bearing `:Point`, while the
    numerator applies `(is_operator IS TRUE OR op_type IS NOT NULL)`; and
    non-operator Points are exactly the population that carries embeddings by
    default (`projection.entities._upsert_point_props` computes OR restores the
    vector for NON-operator Points, `#7778`). One embedded plain point therefore
    keeps `record >= graph` false for ever — the same shape as `document`/`source`.

    The direction is safe (a longer double-storage, never a short answer), which is
    why this is a claim fix and not a runtime fix — but `operator` stays on the
    graph branch that `record_vector`'s own header records as defective (no
    operator predicate at all), so the prose an operator reads must say so.
    """
    from tortoise.security import ENTITY_TYPE_LABELS, entity_label

    # (1) THE CODE FACT: the denominator resolves `operator` to the PARENT label,
    # and the label's own predicate never reaches it.
    assert ENTITY_TYPE_LABELS["operator"] == "Point", ENTITY_TYPE_LABELS["operator"]
    assert entity_label("operator") == "Point"
    tree = ast.parse((_REPO_ROOT / "tortoise" / "projection" / "__init__.py").read_text(
        encoding="utf-8"))
    counter = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "_label_vector_count")
    body = ast.unparse(counter)
    assert "n.embedding IS NOT NULL" in body, body
    assert "is_operator" not in body, (
        "the denominator now applies an operator predicate — if that is deliberate, "
        "`operator` may be reachable again and F1's prose must be re-derived: " + body)

    # (2) THE CONSEQUENCE, EXECUTED: 2 operator rows in the record against the
    # whole `:Point` population (3, one of them plain) is UNVERIFIED. The record
    # side is stubbed — this asserts the COMPARISON, not the door.
    monkeypatch.setattr(record_vector, "_record_vector_count", lambda *a, **k: 2)
    record_vector.reset_coverage_cache()
    verdict = record_vector.coverage(
        "operator", record_vector._Scope(*_SCOPE), graph_vectors=3,
        record_dsn=None, now=0.0)
    assert verdict.verified is False, verdict.as_dict()
    assert (verdict.record_vectors, verdict.graph_vectors) == (2, 3), verdict.as_dict()
    # ...and the other side of the same comparison, so this is a size test and not
    # an always-False stub: 3 operator rows DO verify against the same denominator.
    monkeypatch.setattr(record_vector, "_record_vector_count", lambda *a, **k: 3)
    record_vector.reset_coverage_cache()
    assert record_vector.coverage(
        "operator", record_vector._Scope(*_SCOPE), graph_vectors=3,
        record_dsn=None, now=0.0).verified is True
    record_vector.reset_coverage_cache()

    # (3) THE PROSE: the exemption and its inverted reason are gone, and `operator`
    # is named in the same UNREACHABLE set as the other two.
    doc = record_vector.Coverage.__doc__ or ""
    assert "not vector-bearing by default" not in doc, doc
    idx = doc.find("is UNREACHABLE")
    assert idx != -1, doc
    # The whole bullet, not one wrapped line: the claim and the labels that carry it
    # are spread across the wrap, so a line-scoped read would be a pin on the
    # formatter rather than on the contract.
    clause = " ".join(doc[max(0, idx - 900): idx + 1200].split())
    assert "operator" in clause, (
        "`Coverage`'s docstring no longer names `operator` as unreachable: " + clause)
    for label in ("document", "source"):
        assert label in clause, clause


def test_the_coverage_contract_says_the_numerator_applies_the_kind_predicate():
    """F2 (round 13): `coverage()`'s docstring said "no `kind_filter`"; the code applies one.

    ⛔ THE TWO CONTRACTS IN ONE FILE SAID OPPOSITE THINGS. `_record_vector_count`'s
    own docstring documents the predicate deliberately (`#13630`), while
    `coverage()`'s — the one carrying the COMPARABILITY claim the whole gate rests
    on — promised a whole-table numerator. The stale half was then copied into the
    shipped §14.3, which contradicted itself six paragraphs later.

    The code is the authority and already has its own pin
    (`test_the_numerator_applies_the_labels_kind_predicate`); this one keeps the two
    prose surfaces from drifting back off it. It fails on the pre-fix tree: the old
    sentence reads ``(no status fold, no `kind_filter`)``.
    """
    doc = record_vector.coverage.__doc__ or ""
    assert "no `kind_filter`" not in doc, doc
    assert "KIND predicate" in doc, doc
    assert "#13630" in doc, doc
    # the status fold is genuinely absent — that half of the old sentence was true
    assert "no status fold" in doc, doc

    storage = _STORAGE_DOC.read_text(encoding="utf-8")
    collapsed = " ".join(storage.split())
    # §14.3's fact 2 is the shipped copy of the same claim, and it wrapped, so the
    # comparison is on the collapsed text rather than on a line.
    assert "whole table for the tenant **except the label's own KIND predicate**" in collapsed, (
        "§14.3's fact 2 still claims a whole-table numerator")
    assert "except the label's own KIND predicate" in collapsed


def test_the_record_numerator_counts_only_vector_bearing_rows():
    """The numerator's predicate, which is the other half of the same comparison.

    ``embedding_vec`` (the GENERATED, indexed column) rather than ``embedding``:
    the generated column is what the kNN reads, and a vector the converter rejected
    is a row the dense leg cannot serve.
    """
    tree = ast.parse(_RECORD_VECTOR.read_text(encoding="utf-8"))
    func = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "_record_vector_count")
    body = ast.unparse(func)
    assert "embedding_vec IS NOT NULL" in body, body
    assert "org_id = %s" in body and "graph_id = %s" in body, body


def test_the_coverage_sql_costs_one_aggregate_and_not_a_second_read():
    """The verification must not cost as much as the read it gates.

    ⛔ ONE statement, and no kNN: the question is a count, and the pattern's whole
    point (expand -> backfill -> **verify** -> contract) is that the verify step is
    cheap enough to run on a read path. A second statement here would silently
    double the gated labels' statement budget against the arm's divisor.
    """
    tree = ast.parse(_RECORD_VECTOR.read_text(encoding="utf-8"))
    func = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "_record_vector_count")
    executes = [node for node in ast.walk(func)
                if isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "execute"]
    assert len(executes) == 1, ast.unparse(func)
    assert "<=>" not in ast.unparse(func), "the verification ran a kNN"
