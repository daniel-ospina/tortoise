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
6. **The NON-VACUITY marker** (`test_two_zeroes_verify_trivially_and_the_verdict_SAYS_so`,
   `#13798`): the gate is `record >= graph`, so `0 >= 0` verified an unseeded label
   exactly like a backfilled one. `Coverage.trivial` marks the two-zero case, the
   real-verification case is pinned NOT to carry it, and the unmeasurable arm is
   pinned not to be swallowed by it. The marker is additive: every shipped consumer's
   verdict is unchanged, which is pinned in
   `test_a_trivial_verdict_is_still_a_verified_verdict_for_the_shipped_paths`.

Run (embedded lane; the DB-free cases need no database, the two projection cases
build their own by path)::

    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
        tests/test_13610_graph_vector_copy.py -q
"""
from __future__ import annotations

import ast
import inspect
import os
import re
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

    def serve(self, rows: list[tuple]) -> None:
        """Change what the kNN arm answers — for the read AFTER a cached verdict.

        Deliberately NOT a second `_stub_record` call, which resets the coverage
        cache: the case this exists for needs the SAME cached trivial verdict while
        the record's ANSWER changes, which is the ordinary fresh-tenant lifecycle
        (measure 0/0, then write, then read a non-empty cohort).
        """
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return (self._count,)

    def close(self):
        return None


class _SplitCursor(_Cursor):
    """Answers the coverage ``count(*)`` PER KIND — one `public.source`, two halves.

    `record_vector.LABEL_SERVED` splits `:Source` on `document_kind` while the graph
    denominator counts the WHOLE label, so the two halves verify independently and a
    single ``count`` (what :class:`_Cursor` answers) cannot express that state. This
    reads the kind predicate out of the statement it is handed and answers that
    half's own numerator — the only way to drive the half-verified read arm without
    faking the count by call order.
    """

    def __init__(self, *, documents: int, sources: int, rows: list[tuple] | None = None):
        super().__init__(count=documents, rows=list(rows or []))
        self._documents = documents
        self._sources = sources
        self._answer = documents

    def execute(self, sql, params=None):
        super().execute(sql, params)
        if "document_kind IS NOT NULL" in sql:
            self._answer = self._documents
        elif "document_kind IS NULL" in sql:
            self._answer = self._sources

    def fetchone(self):
        return (self._answer,)


class _Conn:
    def close(self):
        return None


def _stub_record(monkeypatch, *, count: int, rows: list[tuple],
                 cursor: _Cursor | None = None) -> _Cursor:
    """Point the record doors at an in-memory cursor (the #13422 pattern).

    Both statements the leg issues go to ONE cursor because both doors are the
    same door: `record_vector.coverage`'s ``count(*)`` (answered by
    :meth:`_Cursor.fetchone`) and the folded arm's kNN (answered by
    :meth:`_Cursor.fetchall`). The fold's own DB half is stubbed — the cage is
    `tests/test_13422_object_subject_dense_leg.py`'s subject, and what THIS file
    measures is which STORE answered.

    ``cursor`` overrides the default :class:`_Cursor` for a case whose numerator is
    not one number (:class:`_SplitCursor`, i.e. the two halves of one `:Source`
    label). Everything else about the stub — the DSN, the journal flag, the fold's
    DB half, the coverage cache — is set up the same way, so such a case cannot
    drift from the rest of the file's harness.
    """
    import tortoise.record_projection as rp
    import tortoise.record_serve as rs

    cur = cursor if cursor is not None else _Cursor(count=count, rows=rows)
    monkeypatch.setattr(rs, "open_record", lambda *a, **k: _Conn(), raising=True)
    monkeypatch.setattr(rp, "scope_connection", lambda c, org: cur, raising=True)
    # `len(cur._rows)`, not `len(rows)`: the two are equal at setup, but a case that
    # changes what the arm serves AFTER the stub is installed (`_Cursor.serve`) must
    # not leave the fold counting the cohort the arm no longer returns — a fixture
    # that disagrees with itself is a trap for the next assertion, not a guard.
    monkeypatch.setattr(
        record_vector, "_folded_terminal_keys",
        lambda conn, cur_, scope, entity_type, excluded: ([], len(cur_._rows)),
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


def test_the_contract_is_the_LABELS_freeze_not_one_half_of_it(monkeypatch):
    """⛔ ONE HALT OF `:Source` VERIFYING DOES NOT FREEZE `:Source` (#13854 review).

    The no-fallback arm claims the graph copy has been FROZEN, and the freeze is
    produced by the WRITERS — which stop for a LABEL. Every writer of `:Source`
    (`_upsert_document`, `_upsert_source`, `restore_graph`, `_update_entity`) asks
    BOTH halves and stores while EITHER answers KEEP, so `document` verifying while
    `source` has not leaves the graph copy COMPLETE and still being fed. Reading the
    contract off the current `entity_type` alone answered `contracted=True` there
    and turned a record blip into `RecordVectorWithoutFallback` — the complete graph
    answer refused on the default posture, which `#11395`/option 2 never licensed.

    ⛔ NON-VACUOUS BOTH WAYS: the half-verified case must reach the NAMED fallback,
    and the BOTH-verified case must still refuse one — the conjunct is a label rule,
    not a blanket disable of the contract for `:Source`.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)

    def _boom(*_a, **_k):
        raise record_vector.RecordVectorDeclined("bound")

    monkeypatch.setattr(record_vector, "vector_search", _boom, raising=True)

    # ── one half verified (10 >= 10 documents), the other not (0 < 10 sources) ──
    _stub_record(monkeypatch, count=0, rows=[],
                 cursor=_SplitCursor(documents=10, sources=0))
    # The NAMED fallback, i.e. the graph may answer — `vector_search`'s decline is
    # passed through rather than sealed.
    with pytest.raises(record_vector.RecordVectorDeclined):
        _searcher(10)("document", [0.1] * 8, 10)

    # ── BOTH halves verified → the graph really is frozen → sealed ─────────────
    record_vector.reset_coverage_cache()
    _stub_record(monkeypatch, count=0, rows=[],
                 cursor=_SplitCursor(documents=10, sources=10))
    with pytest.raises(record_vector.RecordVectorWithoutFallback):
        _searcher(10)("document", [0.1] * 8, 10)

    # ── and a single-type label is untouched by the conjunct ──────────────────
    record_vector.reset_coverage_cache()
    _stub_record(monkeypatch, count=10, rows=[])
    with pytest.raises(record_vector.RecordVectorWithoutFallback):
        _searcher(10)("object", [0.1] * 8, 10)


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


# ── 3b. the non-vacuity marker: `0 >= 0` is TRUE and is not EVIDENCE (#13798) ──


def test_two_zeroes_verify_trivially_and_the_verdict_SAYS_so(monkeypatch):
    """⛔ THE TRIVIAL PASS, MARKED — this is the whole of #13798's defect.

    `coverage` decides `record_vectors >= graph_vectors`, and `0 >= 0` is true: a
    label that holds nothing in EITHER store reported the SAME `verified=True` as a
    fully backfilled one, so *"the record caught up"* was a finding the count never
    established. `tools/claim_dense_leg_13732.py` met it first — an unseeded
    population PASSES this gate and is then served by a leg whose trace does not name
    the two-zero state: `empty_results` on the record for the two CONTRACTED labels
    (`object`/`subject`), and for the other five — including `point`, the label this
    case reads — the empty record answer falls through and the graph records
    `scan_fallback`/`no_embeddings`/`degraded=True`. Neither token says the count
    compared two zeroes, which is why the instrument could not tell the states apart.

    ⛔ `verified` STAYS TRUE, deliberately: with nothing in either store the two
    stores answer identically, so refusing would change deployment behaviour for a
    harmless state. What changes is that the state is TELLABLE — the marker and the
    reason both say the evidence was two zeroes.

    ⛔ THIS CASE REDS IF THE MARKER IS DROPPED — `verdict.trivial is True` fails on
    a `Coverage` without the field (and on one that always answers `False`), which
    is the falsifier the issue asks for. Both entry forms of the graph count are
    driven (supplied / supplier) so the marker cannot live on one branch only.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    record_vector.reset_coverage_cache()
    scope = record_vector._Scope(*_SCOPE)
    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda *a, **k: 0, raising=True)

    supplied = record_vector.coverage("point", scope, graph_vectors=0)
    assert supplied.verified is True, supplied.as_dict()
    assert supplied.trivial is True, (
        "a verified verdict from two zeroes was reported as an ordinary "
        f"verification — 'the record caught up' on no evidence: {supplied.as_dict()}")
    assert (supplied.record_vectors, supplied.graph_vectors) == (0, 0), supplied
    assert "TRIVIAL" in supplied.reason and "EMPTY" in supplied.reason, (
        f"the reason does not state the vacuity, so a reader who sees only the "
        f"prose is told nothing: {supplied.reason!r}")
    # ⛔ AND IT DATES THE COUNT (#13812 review P2). This string outlives the
    # instant it describes — a verified verdict is cached for the life of the
    # process, and `Coverage.as_dict()` hands the same string to a caller — so a
    # present-tense "the record HOLDS N / the graph SERVES M" is read later as a
    # claim about the population NOW, while the read rendering it may return rows
    # the count never saw. The renderer is the trace line the cases below drive;
    # this pins the string where it is BUILT, so the DECLINE line and `as_dict()`
    # cannot keep a present-tense count after the trace has been corrected.
    assert not re.search(r"\b(holds|serves)\s+\d", supplied.reason), (
        "the reason narrates its count in the PRESENT tense, so every later "
        "reader of it (a cached verdict, the unverified DECLINE line, a caller's "
        f"as_dict()) is told a stale number is the current one: {supplied.reason!r}")
    assert supplied.as_dict()["trivial"] is True, (
        "the marker is not on the serialised verdict, so an artifact that records "
        "`as_dict()` cannot tell a trivial pass from a catch-up")

    # The SUPPLIER form is a different branch (asked on a cache miss) and must
    # answer identically — a marker wired to one entry point is a marker the other
    # one silently skips.
    record_vector.reset_coverage_cache()
    via_supplier = record_vector.coverage(
        "point", scope, graph_population=lambda label: 0)
    assert via_supplier.verified is True and via_supplier.trivial is True, (
        f"the supplier form lost the marker: {via_supplier.as_dict()}")


def test_a_real_verification_never_carries_the_trivial_marker(monkeypatch):
    """The other direction: a satisfied count with a NON-EMPTY population.

    Without this the marker could be `verified and True` or a hard-coded `True` and
    the case above would still pass — the marker would then be on every verdict and
    tell an operator nothing. Both shapes of a real contract are driven (an equal
    count and a surplus) plus the shortfall, which is neither.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    scope = record_vector._Scope(*_SCOPE)

    for record_vectors, graph_vectors, expected in (
            (3, 3, True), (5, 3, True), (1, 1, True), (2, 3, False),
            # ⛔ record > 0 WITH AN EMPTY GRAPH — the ONE shape that distinguishes
            # the marker's `record_vectors == 0` clause from a `graph_vectors == 0`
            # one (#13798 review B). Both sides zero is not the only way to have a
            # zero in the comparison, and a mutant written as
            # `trivial = verified and graph_vectors == 0` would label this
            # SUBSTANTIVE verification TRIVIAL — a record that holds vectors against
            # a graph that serves none (a cleared or restored-empty graph), which is
            # exactly the catch-up the marker exists to let you tell apart. It is
            # also reachable, so it is a case and not a hypothetical.
            (5, 0, True)):
        record_vector.reset_coverage_cache()
        monkeypatch.setattr(record_vector, "_record_vector_count",
                            lambda *a, _n=record_vectors, **k: _n, raising=True)
        verdict = record_vector.coverage("point", scope,
                                         graph_vectors=graph_vectors)
        assert verdict.verified is expected, verdict.as_dict()
        assert verdict.trivial is False, (
            f"record={record_vectors}/graph={graph_vectors} was marked TRIVIAL — "
            f"a non-empty comparison is exactly the evidence the marker exists to "
            f"distinguish: {verdict.as_dict()}")
        assert "TRIVIAL" not in verdict.reason, verdict.reason


def test_the_trivial_marker_does_not_swallow_the_unmeasurable_arm(monkeypatch):
    """⛔ THE ARM THE MARKER MUST NOT REACH.

    An unmeasurable side is `None`, never `0` — *"an absence is not a verdict"* —
    and reading it as a zero would let the marker turn a FAILED MEASUREMENT into a
    harmless trivial pass. The dangerous shape is the mixed one: the graph supplies
    a real `0` while the RECORD could not be counted, which is one zero away from
    the trivial condition and must still be UNVERIFIED.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    scope = record_vector._Scope(*_SCOPE)

    # (a) the graph's count could not be measured at all.
    record_vector.reset_coverage_cache()
    verdict = record_vector.coverage("point", scope, graph_vectors=None)
    assert verdict.verified is False and verdict.trivial is False, verdict.as_dict()
    assert verdict.record_vectors is None and verdict.graph_vectors is None

    # (b) the graph says 0 and the RECORD could not be counted — one zero short of
    #     the trivial condition, and still not a pass.
    record_vector.reset_coverage_cache()
    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda *a, **k: None, raising=True)
    verdict = record_vector.coverage("point", scope, graph_vectors=0)
    assert verdict.verified is False, (
        "an unmeasurable record count verified coverage — the graph copy would be "
        f"retired on a FAILED read: {verdict.as_dict()}")
    assert verdict.trivial is False, (
        f"the trivial marker swallowed the unmeasurable arm: {verdict.as_dict()}")
    assert "TRIVIAL" not in verdict.reason, verdict.reason
    assert record_vector.coverage_verified("point", scope) is False

    # (c) a supplier that RAISES is the same unmeasured side, not a zero.
    record_vector.reset_coverage_cache()

    def _boom(_label: str) -> int:
        raise RuntimeError("graph handle gone")

    verdict = record_vector.coverage("point", scope, graph_population=_boom)
    assert verdict.verified is False and verdict.trivial is False, verdict.as_dict()


def test_a_trivial_verdict_is_still_a_verified_verdict_for_the_shipped_paths(
        monkeypatch):
    """⛔ ADDITIVE, PINNED: the marker changes NO shipped consumer's verdict.

    The two-zero state is a real state, and the mandate on #13798 is explicit that
    refusing it would change deployment behaviour for a harmless one. So the arms a
    trivial verdict reaches must behave EXACTLY as before the marker:

    * the READ gate (`searcher`'s `if not verdict.verified`) proceeds — both stores
      answer the same empty cohort;
    * the WRITE gate (`coverage_verified` -> `mirror_required`) still answers "the
      record owns it", which is why this state is not a refusal;
    * the CACHE still holds it stickily, which is the documented one-way contract
      (`Coverage`'s fourth residual: a trivially-verified label inherits the
      gain-case residual, so an operator must not read `trivial` as a backfill
      certificate).
    """
    from tortoise.record_writer import use_record_tenant

    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    record_vector.reset_coverage_cache()
    scope = record_vector._Scope(*_SCOPE)
    counts: list[int] = []
    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda *a, **k: counts.append(1) or 0, raising=True)

    verdict = record_vector.coverage("object", scope, graph_vectors=0)
    assert verdict.trivial is True and verdict.verified is True, verdict.as_dict()
    assert record_vector.coverage_verified("object", scope) is True
    with use_record_tenant(*_SCOPE):
        assert record_vector.mirror_required("object") is False, (
            "the trivial verdict was turned into a refusal at the write gate — with "
            "nothing in either store the two stores answer identically, so this "
            "would change deployment behaviour for a harmless state (#13798)")

    # Sticky, exactly like any other verified verdict: a second read takes NO count.
    assert record_vector.coverage("object", scope, graph_vectors=0).trivial is True
    assert len(counts) == 1, (
        "the trivial verdict was re-measured — the cache contract moved, and the "
        f"cost model with it: counts={counts}")

    # The marker is DEFAULTED, so a `Coverage` built without it (every call site in
    # this tree, plus any out-of-tree reader) is an ordinary non-trivial verdict.
    assert record_vector.Coverage(
        label="object", verified=True, record_vectors=4, graph_vectors=4,
        reason="").trivial is False


def test_a_trivially_verified_read_SAYS_so_on_the_path_that_serves_it(
        monkeypatch, caplog):
    """⛔ THE MARKER REACHES A TRACE, not only the object (#13798 review C).

    `trivial` on the verdict, `as_dict()["trivial"]` and the reason string are real
    data — but every SHIPPED consumer branches on `verdict.verified` alone, and the
    serve gate logged its `reason` only in the DECLINE branch, which a verified
    verdict never reaches. So on the tree before this case, a label holding nothing
    in either store served an EMPTY cohort as `mechanism=pgvector,
    reason=empty_results` — the SAME token a backfilled label answering the same
    read records, so nothing in any trace told the two apart. The sealed-empty arm
    already recorded `empty_results`/`degraded=False` (`search_engine.py:4176-4178`),
    so the indistinguishability was never a different token: one token covers both
    states, and no line said WHICH. The state #13798 exists to make tellable was
    tellable only to a reader who already had the object in hand.

    ⛔ REDS if the marker is dropped (always-False `trivial` -> the line is never
    emitted), i.e. the same mutant as the zero-zero case, measured on the
    OBSERVABILITY surface rather than on the object. And it changes NO decision: the
    read still proceeds and still answers, because with both sides empty the two
    stores answer identically.

    ⛔ "IT CHANGES NO DECISION" IS PINNED HERE, NOT ONLY ASSERTED IN PROSE. An
    empty `rows` cannot carry that claim on its own: `run_vector_query` returns
    ``[]`` from the SERVED path (`mechanism=pgvector`, `reason=empty_results`,
    `degraded=False` — `search_engine.py:4176-4178`) AND from a DECLINE that has NO
    fallback leg to announce (`search_engine.py:4153-4155`: `mechanism=pgvector`,
    `reason=query_failed`, `degraded=True`, `return []` — the post-contract arm where
    the graph is not a fallback and is never asked). A mutant that raises
    `RecordVectorDeclined` *after* the marker's debug line therefore keeps
    `rows == []` and both caplog assertions green — the half of #13798's mandate
    that says the marker must not turn a serve into a refusal was asserted in prose
    and pinned by nothing. The trace is the evidence that separates them, and this
    file already reads it that way for the non-trivial case
    (`test_a_contracted_empty_answer_is_the_answer`, `:445-449`).

    ⛔ WHAT EACH ASSERTION ACTUALLY HOLDS — measured BOTH ways, because the
    intuitive reading of this pin is wrong and whoever reproduces the mutation will
    otherwise infer grip it does not have. Two decline shapes were injected after the
    marker's debug line, each run once with the graph assertion suppressed and once
    with the trace trio suppressed:

      * `graph.queries == []` catches ONLY the ESCAPING decline — a raise that
        leaves the leg, so the graph IS asked (`mechanism=scan_fallback`,
        `degraded=True`, `reason=no_embeddings`). It stays GREEN over an in-leg
        decline, which never asks the graph at all.
      * the trace trio catches BOTH shapes: the escaped one above, and the in-leg one
        (`mechanism=pgvector`, `degraded=True`, `reason=query_failed`).

    So the trio is the load-bearing set, and `graph.queries == []` is still not
    redundant with it: it pins a property the trace cannot report at all — that no
    work was spent on the graph for a cohort known empty on both sides.
    """
    import logging

    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    _stub_record(monkeypatch, count=0, rows=[])

    with caplog.at_level(logging.DEBUG, logger=record_vector.LOG_NAME):
        rows, graph, trace = _run(_searcher(0), entity_type="object")

    assert rows == [], rows
    assert graph.queries == [], (
        "a trivially-verified read asked the graph — with both sides empty the two "
        f"stores answer identically, so nothing may be spent on the graph: {graph.queries}")
    assert trace[-1]["mechanism"] == "pgvector", trace
    assert trace[-1]["degraded"] is False, (
        "the trivial marker turned a SERVE into a DEGRADED/declined read — that is "
        f"the one behaviour #13798 forbids changing: {trace}")
    assert trace[-1]["reason"] == "empty_results", (
        "the read did not take the record's healthy empty answer, so `rows == []` "
        f"is not evidence that anything was served: {trace}")
    assert "TRIVIALLY" in caplog.text, (
        "a trivially-verified label served an empty cohort with nothing in the trace "
        "saying why — `mechanism=pgvector, reason=empty_results` is then "
        f"indistinguishable from a catch-up: {caplog.text!r}")
    assert "BOTH SIDES ARE EMPTY" in caplog.text, (
        f"the trace names the state but not its evidence: {caplog.text!r}")


def test_a_CACHED_trivial_verdict_does_not_claim_this_read_is_empty(
        monkeypatch, caplog):
    """⛔ THE LINE MUST STATE A MEASUREMENT, NOT THIS READ (#13799 review, open P2).

    `trivial` is a MEASUREMENT-TIME fact and a VERIFIED verdict is sticky for the
    life of the process (`_cached_coverage`: the only expiry is
    `and not verdict.verified`). A trivial verdict is verified, so the ordinary
    fresh-tenant lifecycle reaches a state the earlier wording claimed was
    impossible: the first gated read measures `0/0` -> `trivial=True` is cached ->
    the tenant WRITES (the mirror stays off, `mirror_required` is already False) ->
    a later read is served the cached verdict and **returns rows**.

    A present-tense line — *"this read is empty because the POPULATION is empty"* —
    is then FALSE on every one of those reads. That is the trace-that-lies class
    this repo treats as a defect, and it is self-defeating for the line's own
    purpose: it exists so a trace can tell a vacuous pass from a catch-up, and the
    wording would relabel the catch-up.

    ⛔ THIS CASE REDS ON THE PRE-FIX WORDING. Nothing else in the tree emits
    "TRIVIALLY", so the capture below cannot be satisfied by a pre-existing line;
    the assertion that bites is the measurement-time one, which the reverted message
    fails while every other assertion here stays green (measured).

    ⛔ AND REDS IF THE COUNT IS RENDERED IN THE PRESENT TENSE (`#13812` review P2
    follow-up). Wording the wrapper "was verified … WHEN IT WAS TAKEN" while
    interpolating a present-tense `verdict.reason` ("the record holds 0 vector(s)")
    makes the sentence contradict itself on THIS read: the parenthetical says the
    record holds nothing while `rows2` carries a row. Both halves are pinned below —
    the line must carry the measurement-time phrasing, and must not carry a
    present-tense count — so correcting one half of the sentence cannot pass.
    """
    import logging

    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    # `cursor` and not a second `_stub_record`: the cache must SURVIVE into the
    # second read, and `_stub_record` resets it as part of its setup.
    cursor = _stub_record(monkeypatch, count=0, rows=[])

    with caplog.at_level(logging.DEBUG, logger=record_vector.LOG_NAME):
        rows, _graph, _trace = _run(_searcher(0), entity_type="object")
    assert rows == [], rows
    assert "TRIVIALLY" in caplog.text, caplog.text

    # ── the tenant writes; the SAME cached trivial verdict gates the next read ──
    caplog.clear()
    cursor.serve([("obj-written-later", 0.10, None)])
    with caplog.at_level(logging.DEBUG, logger=record_vector.LOG_NAME):
        rows2, _graph2, trace2 = _run(_searcher(0), entity_type="object")

    assert [row[0] for row in rows2] == ["obj-written-later"], (
        "the write that landed after the trivial verdict was not served — the "
        f"cached verdict decides the GATE, never the rows: {rows2}")
    # ⛔ AND THE SECOND READ WAS REALLY CACHED — not a silent re-measurement. The
    # case is named CACHED, and without this the cache could stop retaining a trivial
    # verdict and the case would still pass: it would re-measure `0/0` and emit the
    # same line. The COUNT is the discriminator — one aggregate for TWO reads.
    aggregates = [sql for sql, _p in cursor.statements if "count(" in sql.lower()]
    assert len(aggregates) == 1, (
        "the second read re-measured the coverage count instead of taking the "
        f"sticky verdict from the cache: {len(aggregates)} aggregate(s) — {aggregates}")
    # The record answered, healthily: this is the caught-up case, not a decline.
    assert trace2[-1]["reason"] == "ok", trace2
    # ⛔ `mechanism`, NOT `degraded`. `reason == "ok"` already implies
    # `degraded is False` at every emit site in `search_engine.run_vector_query`
    # (`:3045`, `:3180`, `:3633`, `:4229`, `:4444`, `:4551`, `:4644`, `:4904` all pass
    # `degraded=False` literally) and the one re-labelling that flips it also rewrites
    # `reason` (`:3935-3945`), so a `degraded is False` line here could never fail on
    # its own — the dead-assertion shape. `mechanism` CAN differ at `reason="ok"`:
    # the graph legs record `"ok"` with `MECHANISM_INDEX` (`:4445`, `:4552`) and with
    # the scan mechanism (`:4645`), so this pins the thing the case is about — the
    # cohort came from the RECORD, not from a graph leg that answered instead.
    assert trace2[-1]["mechanism"] == "pgvector", trace2

    # ⛔ The verdict is STILL reported (provenance is the line's job) ...
    assert "TRIVIALLY" in caplog.text, (
        "a CACHED trivial verdict served a cohort with no provenance in the trace — "
        "the one state an operator cannot reconstruct from the rows: "
        f"{caplog.text!r}")
    # ... and it says the fact is a MEASUREMENT, and therefore does NOT claim a
    # present-tense property of a read that just returned rows.
    assert "MEASUREMENT" in caplog.text, (
        "the line does not say the verdict was a MEASUREMENT-time fact, so it reads "
        f"as a claim about this read: {caplog.text!r}")
    # ⛔ AND THE PARENTHETICAL IS DATED TOO (`#13812` review P2 follow-up). The
    # wrapper above can say "was verified … WHEN IT WAS TAKEN" while the
    # `verdict.reason` it interpolates still narrates its count in the PRESENT tense
    # — "the record holds 0 vector(s)" — which on this very read (a row is served)
    # makes the sentence contradict itself. Pinning only the wrapper left that half
    # correctable-by-omission, so both halves are asserted.
    trivial_line = next(
        (line for line in caplog.text.splitlines() if "TRIVIALLY" in line), "")
    assert "the count measured" in trivial_line, (
        "the parenthetical does not date the count it prints, so a cached verdict "
        f"is rendered as a reading of THIS read: {trivial_line!r}")
    assert not re.search(r"\b(holds|serves)\s+\d", trivial_line), (
        "the parenthetical still states the stale count in the PRESENT tense — on "
        "the very read this case returns a row for, an operator triaging from the "
        f"log is told the record holds 0 vectors: {trivial_line!r}")
    assert "this read is empty" not in caplog.text, (
        "⛔ THE TRACE LIES: the verdict was taken when both stores were empty, but "
        "this read returned rows, so a present-tense 'this read is empty' is false "
        "and it mislabels the caught-up case the line exists to distinguish: "
        f"{caplog.text!r}")


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


def test_a_HALF_verified_Source_label_keeps_the_fallback(monkeypatch):
    """⮔ THE CONTRACT IS PER **LABEL**, not per `entity_type` (`#13826` review, P2).

    `:Source` carries ONE vector per node and every fold that writes it keeps that
    vector when EITHER `document` OR `source` answers KEEP (`_upsert_document`'s two
    arms, `_upsert_source`, `restore_graph`'s ``all(...)``). So the graph copy is
    frozen only when EVERY type addressing the label has verified — which is also the
    AND `label_write_gate` requires before a strip can run. With one half verified
    and the other not, the copy is complete and still being FED: refusing it turns a
    record blip into a silent availability regression on the DEFAULT posture, and it
    inverts the cutover order (the fallback goes LAST).

    This is the ORDINARY state, not a corner: the graph denominator is LABEL-wide
    (`_label_vector_count`) while the record numerator applies the KIND predicate, so
    the half that dominates the label verifies against the same denominator the other
    half cannot.

    Both halves are driven — the `document`-verified direction AND its mirror, which
    the searcher-level arm does not cover — and the fully-verified case is kept as the
    control: a fix that simply removed `document`/`source` from
    `MIRROR_RETIRED_LABELS` would pass the first two arms and fail the third. The
    assertions are made on `run_vector_query`'s OUTCOME (the named
    `scan_fallback` and the row it returns), not on which exception was raised.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    assert record_vector.LABEL_SERVED["document"][0] == \
        record_vector.LABEL_SERVED["source"][0] == "source", (
        "the two halves no longer share one served table — the label AND this file "
        "pins is then measuring a population the product does not have")

    def _boom(*_a, **_k):
        raise record_vector.RecordVectorDeclined("the record skipped this read")

    def _searcher_by_label(populations):
        return record_vector.searcher(
            carried=_SCOPE, timeout_ms=500,
            graph_population=lambda entity_type: populations.get(entity_type, 0))

    monkeypatch.setattr(record_vector, "vector_search", _boom, raising=True)

    # ── the `document` half verified (3 >= 3), the `source` half NOT (3 < 9) ──
    _stub_record(monkeypatch, count=3, rows=[])
    rows, graph, trace = _run(
        _searcher_by_label({"document": 3, "source": 9}), entity_type="document",
        graph=_Graph([("doc-graph-only", 1.0 / 1.5)]))
    assert [row[0] for row in rows] == ["doc-graph-only"], (
        "a half-verified `:Source` read refused the graph — the label was still "
        "being written by its other half, so the fallback was whole: "
        f"{rows} (trace={trace[-1] if trace else None})")
    assert graph.queries, "the graph branch never ran for a still-fed label"
    assert trace[-1]["mechanism"] == "scan_fallback", trace
    assert trace[-1]["degraded"] is False, trace

    # ── symmetric: the `source` half verified (3 >= 3), `document` NOT (3 < 9) ──
    record_vector.reset_coverage_cache()
    _stub_record(monkeypatch, count=3, rows=[])
    monkeypatch.setattr(record_vector, "vector_search", _boom, raising=True)
    rows, graph, trace = _run(
        _searcher_by_label({"document": 9, "source": 3}), entity_type="source",
        graph=_Graph([("src-graph-only", 1.0 / 1.5)]))
    assert [row[0] for row in rows] == ["src-graph-only"], (
        f"the mirror-image half-verified read refused the graph: {rows}")
    assert trace[-1]["mechanism"] == "scan_fallback", trace

    # ── the CONTROL: both halves verified → the contract still holds ──
    record_vector.reset_coverage_cache()
    _stub_record(monkeypatch, count=9, rows=[])
    monkeypatch.setattr(record_vector, "vector_search", _boom, raising=True)
    rows, graph, trace = _run(
        _searcher_by_label({"document": 3, "source": 9}), entity_type="document",
        graph=_Graph([("doc-graph-only", 1.0 / 1.5)]))
    assert rows == [], (
        "a fully-verified `:Source` read recovered its frozen cohort — the fix "
        "must narrow the contract to a half-verified label, not remove it")
    assert graph.queries == [], "the graph was queried after `:Source`'s contract"
    assert trace[-1]["reason"] == "query_failed", trace

    # ── a single-type label is unaffected by the label AND (no extra refusal) ──
    record_vector.reset_coverage_cache()
    _stub_record(monkeypatch, count=3, rows=[])
    monkeypatch.setattr(record_vector, "vector_search", _boom, raising=True)
    rows, graph, trace = _run(_searcher(3), graph=_Graph([("obj-graph-only", 1.0 / 1.5)]))
    assert rows == [] and graph.queries == [], (
        f"the single-type `object` contract was weakened by the label AND: {rows}")


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
    surplus of plain points, and the operator-scoped read's record answer would hold
    zero rows of that kind — which for a non-contracted label falls through to the
    graph (`search_engine.py:4179`) — so nothing on the record leg reports the
    shortfall. `record ≥ graph` is the expected
    steady state once the record (truth)
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


def test_the_operator_denominator_applies_the_operator_predicate_so_the_gate_is_satisfiable(
        monkeypatch):
    """F1 (round 13) INVERTED: `operator` is REACHABLE, and by MEASUREMENT (`#14560`).

    ⛔ WHAT THIS FILE USED TO PIN, AND WHY THAT WAS THE DEFECT. It asserted the two
    facts that made the operator slice verify-proof: `ENTITY_TYPE_LABELS["operator"]
    == "Point"`, so the DENOMINATOR was every vector-bearing `:Point`, while the
    NUMERATOR applies `(is_operator IS TRUE OR op_type IS NOT NULL)` — and non-operator
    Points are exactly the population that carries embeddings by default, so one
    embedded plain point kept `record >= graph` false for ever. The direction was called
    "safe", but it is a gate whose PASS CONDITION CANNOT BE MET: `operator` gates
    `:Point`'s strip (`label_write_gate` ANDs every entity type addressing the label),
    so 39,662 vectors (~71 MB) stayed resident with no repair available. The measured
    production state on `#14560`: 17,899 operator rows, 0 with a vector, denominator
    39,662 — impossible by arithmetic, not by backfill.

    What is pinned now, each part executed rather than described:

    1. **ONE declaration**, and it is the repo's canonical operator identity (`#943`),
       i.e. the Cypher twin of `LABEL_SERVED["operator"][2]`'s SQL. `point` — the label
       whose denominator IS the whole label — is untouched, so `:Point`'s own count is
       not lowered by this and the strip keeps its counterweight.
    2. **The count splices it**, on a graph door that RECORDS: the `operator` statement
       carries the predicate, the `point` statement does not.
    3. **The arithmetic is satisfiable in both directions** — 0 vs 0 verifies (and says
       NOTHING TO COPY, naming why rather than implying a backfill is behind), 0 vs 1
       does not, and 1 vs 1 verifies substantively.
    4. **The prose says the true thing**, so a reader cannot re-derive the old
       "unreachable" premise from this module's own docstring.
    """
    from tortoise.projection import FalkorProjection

    # (1) THE DECLARATION — one, closed, and the twin of the SQL predicate.
    assert record_vector.GRAPH_KIND_FILTER == {
        "operator": "(n.is_operator = true OR n.op_type IS NOT NULL)"}, \
        record_vector.GRAPH_KIND_FILTER
    sql_kind = record_vector.LABEL_SERVED["operator"][2]
    for term in ("is_operator", "op_type"):
        assert term in record_vector.GRAPH_KIND_FILTER["operator"], term
        assert term in sql_kind, (term, sql_kind)
    # ...and NOTHING else is narrowed: a whole-label count is right for the rest, and
    # narrowing `point` would lower the denominator of the label the strip is about.
    assert record_vector.graph_kind_filter("point") == ""
    assert record_vector.graph_kind_filter("event") == ""
    assert "point" not in record_vector.GRAPH_KIND_FILTER

    # (2) THE COUNT SPLICES IT — executed through the projection's own method, on a
    # door that records the statement it was handed.
    class _RecordingGraph:
        def __init__(self, count):
            self.statements: list[str] = []
            self._count = count

        def query(self, sql, params=None, timeout=None):
            self.statements.append(sql)
            return _Result([[self._count]])

    class _Proj:
        def __init__(self, count):
            self.g = _RecordingGraph(count)

    op_proj, pt_proj = _Proj(0), _Proj(3)
    assert FalkorProjection._label_vector_count(op_proj, "operator") == 0
    assert FalkorProjection._label_vector_count(pt_proj, "point") == 3
    op_sql, pt_sql = op_proj.g.statements[0], pt_proj.g.statements[0]
    assert "MATCH (n:Point)" in op_sql and "MATCH (n:Point)" in pt_sql, (op_sql, pt_sql)
    assert "n.embedding IS NOT NULL" in op_sql, op_sql
    assert "n.is_operator = true OR n.op_type IS NOT NULL" in op_sql, op_sql
    assert "is_operator" not in pt_sql and "op_type" not in pt_sql, (
        "a whole-label population was narrowed — `point`'s denominator must stay the "
        "label's whole vector-bearing count: " + pt_sql)

    # (3) THE ARITHMETIC, in all three reachable directions (the record side is stubbed;
    # this asserts the COMPARISON, not the door).
    scope = record_vector._Scope(*_SCOPE)

    def _verdict(record: int, graph: int):
        monkeypatch.setattr(record_vector, "_record_vector_count",
                            lambda *a, **k: record, raising=True)
        record_vector.reset_coverage_cache()
        return record_vector.coverage("operator", scope, graph_vectors=graph,
                                      record_dsn=None, now=0.0)

    empty = _verdict(0, 0)
    assert empty.verified is True and empty.trivial is True, empty.as_dict()
    assert (empty.record_vectors, empty.graph_vectors) == (0, 0), empty.as_dict()
    assert "NOTHING TO COPY" in empty.reason, empty.reason
    assert "#548" in empty.reason, empty.reason

    short = _verdict(0, 1)
    assert short.verified is False and short.trivial is False, short.as_dict()
    assert "--label Point" in short.reason, (
        "the shortfall must name the repair that EXISTS (the `:Point` population "
        "already addresses this slice); it must not send an operator to a per-kind "
        "slice the tool cannot select: " + short.reason)

    covered = _verdict(1, 1)
    assert covered.verified is True and covered.trivial is False, covered.as_dict()

    # ⛔ THE TWO STATES A FIRST CUT GOT WRONG (verification-gate findings, reproduced),
    # and both are reachable in production rather than theoretical:
    #
    #   (a) the POST-STRIP steady state — the record holds this slice's vectors and the
    #       graph holds none — must NOT claim "no vector in either store"; a clause that
    #       contradicted its own counts would be what an operator reads after the strip,
    #       because a VERIFIED reason is cached and re-rendered.
    stripper = _verdict(17_999, 0)
    assert stripper.verified is True and stripper.trivial is False, stripper.as_dict()
    assert "NOTHING TO COPY" not in stripper.reason, stripper.reason
    assert "--label Point" not in stripper.reason, stripper.reason

    #   (b) a COVERED slice must NOT be sent to the backfill — the repair clause belongs
    #       to a shortfall, not to every non-empty graph.
    assert "--label Point" not in covered.reason, covered.reason
    short_by_two = _verdict(3, 5)
    assert short_by_two.verified is False, short_by_two.as_dict()
    assert "--label Point" in short_by_two.reason, short_by_two.reason
    record_vector.reset_coverage_cache()

    # (4) THE PROSE — no stale "unreachable" claim survives, and the two-sided kind
    # half is named where a reader looks for the comparison's terms.
    doc = record_vector.Coverage.__doc__ or ""
    assert "not vector-bearing by default" not in doc, doc
    collapsed = " ".join(doc.split())
    assert "THE KIND HALF IS NOW TWO-SIDED" in collapsed, collapsed[:600]
    assert "GRAPH_KIND_FILTER" in collapsed, collapsed[:600]
    # `source`/`document` are stated as still one-sided rather than silently dropped.
    assert "NOT narrowed yet" in collapsed, collapsed[:600]


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
