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
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from tortoise import record_vector
from tortoise.projection import FalkorProjection
from tortoise.search_engine import run_vector_query

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SDK = _REPO_ROOT / "tortoise" / "sdk.py"
_RECORD_VECTOR = _REPO_ROOT / "tortoise" / "record_vector.py"

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

    def query(self, sql, params=None, timeout=None):  # noqa: ARG002
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
    searcher = _searcher(4)  # 4 >= 4 → verified

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
    """The contract is ONE-WAY, and a later short count is REPORTED, not obeyed.

    Post-contract the graph copy is frozen, so flipping the read back to it would
    return a smaller cohort while looking like a healthy read. A graph that gains a
    vector-bearing node after the contract (`record-restore`'s fill, a backup
    restore) therefore cannot move the verdict — the disagreement is logged once
    and the record keeps the read. The repair is the graph-side sweep
    (`graph-scripts/backfill_embeddings.py`) or the next boot's re-verify, both of
    which are named in the warning.
    """
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    record_vector.reset_coverage_cache()
    scope = record_vector._Scope(*_SCOPE)
    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda *a, **k: 5, raising=True)
    assert record_vector.coverage("object", scope, graph_vectors=5).verified

    # The graph gains a node with a vector; the record has not caught up.
    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda *a, **k: 5, raising=True)
    after = record_vector.coverage("object", scope, graph_vectors=9)
    assert after.verified is True, (
        "a post-contract short count flipped the read back to a FROZEN graph copy "
        "— the rows written since the contract exist only in the record")


def test_a_verified_verdict_survives_its_ttl_but_an_unverified_one_does_not(
        monkeypatch):
    """The cache's two directions, which are not symmetric.

    Expiring an UNVERIFIED verdict costs one count and keeps the graph branch, so
    it expires normally. Expiring a VERIFIED one would let a transient read error
    (an unmeasurable count, a graph hiccup) hand the next read a frozen graph copy
    as if it were the corpus — so the positive verdict stands for the life of the
    process.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    record_vector.reset_coverage_cache()
    scope = record_vector._Scope(*_SCOPE)
    monkeypatch.setattr(record_vector, "_record_vector_count",
                        lambda *a, **k: 2, raising=True)
    record_vector.coverage("object", scope, graph_vectors=2)
    assert record_vector.coverage_verified("object", scope) is True
    real_monotonic = record_vector.time.monotonic
    monkeypatch.setattr(record_vector.time, "monotonic",
                        lambda: real_monotonic() + 10_000, raising=True)
    assert record_vector.coverage_verified("object", scope) is True, (
        "the verified verdict expired — a later failed count would demote the read "
        "to a frozen graph copy")


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
    issue's "no data deletion" constraint, and it is pinned by the third case.
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

    # ── the transitional object case, and NO DELETION on an existing node ─
    record_vector.reset_coverage_cache()
    with use_record_tenant(*_SCOPE):
        proj._upsert_subject({"id": "sub-13610-a", "name": "alice"})
    assert _embedding_of(proj.g, "Subject", "sub-13610-a") is not None, (
        "a re-fold DELETED a stored graph vector — this change removes no data")


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
        keywords = {kw.arg for kw in call.keywords}
        assert "projection" in keywords, ast.unparse(call)
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
