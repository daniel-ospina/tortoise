"""Per-capture graph-operation accounting (#3359).

Pins the instrumentation that makes graph operations countable per captured
session: ``_GuardedGraph.query`` records every op into the active capture
counter, split read/write by leading verb and attributed to the capture phase
(``session_store`` / ``extraction`` / ``commit`` / ``belief``).

The integration test runs a real capture through the offline MockModel
extractor (``TORTOISE_SESSION_LLM_MOCK=1`` seam) on an embedded DB — no
provider key, no network. It asserts the counter is NON-ZERO and
PHASE-ATTRIBUTED, which is the contract that keeps the measurement honest: a
counter that silently reports 0 would be worse than no counter at all.
"""
from __future__ import annotations

import pytest

from tortoise.graph_ops import (
    PHASES,
    GraphOpsCounter,
    capture_phase,
    classify_op,
    count_graph_ops,
    distribution_from_rows,
    record_graph_op,
)
from tortoise.sdk import TortoiseSDK

CONV = [
    {"role": "user", "content": "I think the auth dead-end is the top issue. "
                                "We decided to ship serve --http first."},
    {"role": "assistant", "content": "Agreed. Evidence suggests the website "
                                     "config is the root cause."},
    {"role": "user", "content": "ok"},
]


@pytest.fixture(autouse=True)
def _mock_extractor(monkeypatch):
    """Offline MockModel extractor (#822 seam) — zero network, deterministic."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


# ── unit: verb classification ─────────────────────────────────────────
@pytest.mark.parametrize("cypher,expected", [
    ("MERGE (t:Point {id:$id}) SET t.content=$c", "write"),
    ("CREATE (n:Point {id:$id})", "write"),
    ("SET n.content = $c", "write"),
    ("DETACH DELETE n", "write"),
    ("MATCH (n:Point) WHERE n.id IN $ids RETURN n.id", "read"),
    ("CALL db.idx.fulltext.queryNodes('Point', $q) YIELD node RETURN node",
     "read"),
    # leading comments/whitespace must not defeat the verb read
    ("\n\n  // a comment\n MERGE (n:Point {id:$id})", "write"),
    ("/* block */ MATCH (n) RETURN n", "read"),
    # documented limitation: a chained read-then-write classifies by the
    # LEADING verb (matches the #3359 hand-measurement rule; total is
    # unaffected, only the read/write split).
    ("MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) MERGE (s)-[:CONTAINS]->(t)",
     "read"),
    ("MATCH (n:Point {id:$id}) DETACH DELETE n", "read"),
])
def test_classify_op(cypher, expected):
    assert classify_op(cypher) == expected


def test_record_graph_op_inert_without_active_capture():
    """No active capture → the choke point records nothing anywhere (the
    counter object passed in is only touched while counting is active)."""
    counter = GraphOpsCounter()
    record_graph_op("MERGE (n:Point)")  # must not raise / no counter to hit
    assert counter.total == 0
    with count_graph_ops(counter):
        record_graph_op("MERGE (n:Point)")
        record_graph_op("MATCH (n) RETURN n")
    assert counter.total == 2
    assert counter.writes == 1
    assert counter.reads == 1
    # after the context exits, recording is inert again
    record_graph_op("MERGE (n:Point)")
    assert counter.total == 2


def test_extract_marker_splits_reads_and_writes():
    """Inside the ``extract`` phase, reads → extraction, writes → commit."""
    counter = GraphOpsCounter()
    with count_graph_ops(counter), capture_phase("extract"):
        record_graph_op("MATCH (n) RETURN n")          # read  → extraction
        record_graph_op("MERGE (n:Point {id:$id})")    # write → commit
    assert counter.by_phase["extraction"]["read"] == 1
    assert counter.by_phase["commit"]["write"] == 1
    assert counter.by_phase["extraction"]["write"] == 0
    assert counter.by_phase["commit"]["read"] == 0


def test_distribution_shape():
    rows = [
        {"total": 100, "read": 80, "write": 20, "turns": 4},
        {"total": 200, "read": 170, "write": 30, "turns": 6},
        {"total": 300, "read": 260, "write": 40, "turns": 8},
        {"total": 400, "read": 350, "write": 50, "turns": 10},
    ]
    d = distribution_from_rows(rows)
    assert d["sessions"] == 4
    assert d["median"] == 250.0
    assert d["p95"] == 400.0
    assert d["min"] == 100.0 and d["max"] == 400.0
    assert d["read_total"] == 860 and d["write_total"] == 140
    assert distribution_from_rows([])["sessions"] == 0


def test_distribution_tolerates_malformed_fields():
    """A stored row with an unusable read/write/turns value is read as 0 —
    the reader must never abort the whole distribution on one bad row."""
    rows = [
        {"total": 10, "read": "n/a", "write": 1, "turns": 1},
        {"total": 100, "read": 90, "write": 10, "turns": 100},
        {"total": 5, "read": 5, "write": None, "turns": 0},
    ]
    d = distribution_from_rows(rows)
    assert d["sessions"] == 3
    assert d["read_total"] == 95   # "n/a" → 0, None → 0
    assert d["write_total"] == 11
    # per-session rates 10/1=10, 100/100=1, 5/0→0 → median 1.0
    assert d["median_per_turn"] == 1.0


def test_capture_phase_rejects_unknown_phase():
    """An unknown phase must RAISE, not mint a bucket ``as_dict`` omits —
    otherwise ``total != sum(by_phase)`` silently in the emitted row."""
    with pytest.raises(ValueError), capture_phase("typo_phase"):
        pass  # pragma: no cover


def test_counter_rejects_unknown_phase_via_the_public_class():
    """The invariant is enforced at the class boundary too, not only in
    ``capture_phase`` — ``GraphOpsCounter`` is exported."""
    with pytest.raises(ValueError):
        GraphOpsCounter().record("typo_phase", "read")


def test_distribution_reads_the_emitted_namespaced_shape():
    """The production read path aggregates the emitted ``graph_ops_*`` props
    from ``analytics_events``: ``distribution_from_rows`` must canonicalise
    them. Reporting all zeros here is the silent-loss failure the module
    exists to prevent."""
    rows = [{
        "session_id": "s1", "graph_ops_turns": 4, "graph_ops_total": 400,
        "graph_ops_read": 350, "graph_ops_write": 50,
        "graph_ops_by_phase": {
            "session_store": {"read": 1, "write": 1, "total": 2}},
    }]
    d = distribution_from_rows(rows)
    assert d["total_ops"] == 400
    assert d["read_total"] == 350 and d["write_total"] == 50
    assert d["sessions"] == 1 and d["distinct_sessions"] == 1


def test_distribution_distinct_sessions_dedupes_a_retry():
    """``sessions`` counts capture ATTEMPTS; a retried session emits a second
    row with the same id, so ``distinct_sessions`` is the deduped count."""
    rows = [
        {"session_id": "s1", "total": 100, "read": 90, "write": 10, "turns": 4},
        {"session_id": "s1", "total": 120, "read": 100, "write": 20, "turns": 4},
        {"session_id": "s2", "total": 80, "read": 70, "write": 10, "turns": 4},
    ]
    d = distribution_from_rows(rows)
    assert d["sessions"] == 3
    assert d["distinct_sessions"] == 2


# ── integration: a real capture reports a non-zero, phase-attributed count ──
def test_capture_reports_nonzero_phase_attributed_ops(tmp_path, monkeypatch):
    from tortoise.projection import _GuardedGraph

    # An INDEPENDENT ORACLE for the headline number. ``total`` is BY
    # CONSTRUCTION ``sum(by_phase)``, so comparing those two can never detect a
    # double count — doubling every op moves both sides together, and an
    # earlier version of this test could not fail for that reason. Counting the
    # guarded queries ACTUALLY ISSUED does: if the choke point recorded one op
    # twice, ``total`` would be 2x this number.
    issued = {"n": 0}
    _real_query = _GuardedGraph.query

    def _counting_query(self, cypher, *args, **kwargs):
        issued["n"] += 1
        return _real_query(self, cypher, *args, **kwargs)

    monkeypatch.setattr(_GuardedGraph, "query", _counting_query)

    sdk = TortoiseSDK(db_path=str(tmp_path / "gops.db"))
    try:
        # warm-up: index creation etc. is NOT a captured session and must not
        # leak into any capture's count.
        sdk.capture_session(CONV, session_id="warmup_000")
        issued["n"] = 0  # measure ONLY the capture below
        res = sdk.capture_session(CONV, session_id="gops_000")
        # Snapshot HERE, before ``close()`` — the close is teardown, not
        # capture. It issues guarded queries (12 in the docker lane, 0 in the
        # embedded one) and they are NOT metered, because no capture phase is
        # active: ``record_graph_op`` returns early on an inactive phase. Read
        # after the close instead and the oracle compares a capture-sized
        # numerator against a capture-plus-teardown denominator — which is how
        # this test first went red in CI (65 recorded for 77 issued) while
        # passing locally, i.e. it asserted an accident of the embedded lane.
        measured = issued["n"]
    finally:
        sdk.close()

    ops = res["graph_ops"]
    assert ops["total"] > 0, "capture issued no counted graph ops"
    assert ops["read"] + ops["write"] == ops["total"]
    # every phase is present (the stored shape is stable for readers)
    assert set(ops["by_phase"]) == set(PHASES)
    # phase attribution is real: the turn-store loop always writes, the
    # extraction call is entered (its reads → extraction, writes → commit),
    # and the ingest belief pass always reads the graph. Asserting ONLY
    # session_store/belief would let a lost `@phased("extract")` decorator
    # silently migrate every extraction op into session_store and still pass.
    assert ops["by_phase"]["session_store"]["total"] > 0
    assert ops["by_phase"]["session_store"]["write"] >= 1
    assert ops["by_phase"]["extraction"]["total"] > 0, (
        "extraction phase unattributed — the @phased('extract') decorator is "
        "not wired to the extract call")
    assert ops["by_phase"]["commit"]["total"] > 0, (
        "commit phase unattributed — extraction writes are not reaching the "
        "commit bucket")
    assert ops["by_phase"]["belief"]["total"] > 0
    # No op is double-counted — checked against the guarded queries ACTUALLY
    # ISSUED **during the capture**, not against a restatement of the
    # counter's own definition (``total`` is BY CONSTRUCTION
    # ``sum(by_phase)``, so that comparison moves both sides together and
    # cannot fail).
    #
    # Equality is the right assertion *for this capture*, but not because every
    # guarded query is metered — two paths are deliberately unmetered and this
    # capture happens to take neither:
    #   * a refused bulk wipe is checked BEFORE the choke point
    #     (``projection._assert_test_graph``) and is not work the capture
    #     caused, so it is counted by the oracle and not by the meter;
    #   * ``record_graph_op`` is fail-soft (``except Exception`` → WARNING), so
    #     a drop is possible by design.
    # The honest form of the claim is therefore: every guarded query that
    # REACHES ``record_graph_op`` is recorded, and it neither drops nor doubles
    # for this capture. If a future capture issues a refused wipe, this REDs
    # with an under-count on a correct meter — so read the failure before
    # assuming the meter is wrong.
    assert ops["total"] == measured, (
        f"{ops['total']} ops recorded for {measured} guarded queries issued "
        "during the capture — a double count at the choke point shows exactly "
        "here (2x), and a dropped record shows as an under-count")


def test_record_graph_op_is_fail_soft_on_an_unknown_phase():
    """The choke point must not turn a metering bug into a FAILED GRAPH WRITE.

    ``GraphOpsCounter.record`` deliberately RAISES on an unknown phase (it must
    not mint a bucket ``as_dict`` omits), and ``record_graph_op`` runs BEFORE
    the caller's query executes — so an uncaught raise would abort a valid
    write. ``capture_phase`` validates, but ``_PHASE`` is a ContextVar any
    caller could set directly, so the hazard is one typo away.

    Falsified by removing the guard in ``record_graph_op``: this call raises
    ValueError and the test fails.
    """
    import tortoise.graph_ops as graph_ops

    counter = GraphOpsCounter()
    with count_graph_ops(counter):
        token = graph_ops._PHASE.set("typo_phase")
        try:
            # must NOT raise, and must NOT count anything
            record_graph_op("MATCH (n) RETURN count(n)")
        finally:
            graph_ops._PHASE.reset(token)
    assert counter.total == 0


# ── hosted: the analytics row is allowlisted and readable ──────────────
def test_capture_graph_ops_props_are_allowlisted_and_emitted(
        tmp_path, monkeypatch):
    """The hosted lane builds a PII-filtered ``capture_graph_ops`` row and
    the real writer emits it; the reader returns the distribution. If the
    allowlist strips a measured field the measurement is silently lost."""
    import json

    from tortoise import hosted_api as ha
    from tortoise.graph_ops import (
        capture_graph_ops_distribution,
        read_capture_graph_ops,
    )

    counter = GraphOpsCounter()
    with count_graph_ops(counter):
        record_graph_op("MERGE (s:Session {id:$sid})")           # write
        record_graph_op("MATCH (n) RETURN n")                     # read
        with capture_phase("extract"):
            record_graph_op("MATCH (n:Point) RETURN n")           # extraction
            record_graph_op("MERGE (n:Point {id:$id})")           # commit
        with capture_phase("belief"):
            record_graph_op("MATCH (n) RETURN n")                 # belief read

    props = ha._capture_graph_ops_props("sess-1", 3, counter)
    assert props["session_id"] == "sess-1"
    assert props["graph_ops_turns"] == 3
    assert props["graph_ops_total"] == 5
    assert props["graph_ops_read"] == 3 and props["graph_ops_write"] == 2
    assert props["graph_ops_by_phase"]["belief"]["total"] == 1
    # the allowlist must not strip a single measured field
    assert set(props) <= ha._ALLOWED_ANALYTICS_PROPS

    # emit through the REAL writer (Supabase unset → JSONL fallback)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    fallback = str(tmp_path / "analytics.jsonl")
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", fallback)
    ha._track_analytics_event("team-1", "capture_graph_ops", props)
    rec = json.loads((tmp_path / "analytics.jsonl").read_text()
                     .strip().splitlines()[-1])
    assert rec["event_name"] == "capture_graph_ops"
    assert rec["properties"]["graph_ops_total"] == 5
    assert rec["properties"]["graph_ops_by_phase"]["belief"]["read"] == 1

    # the readable hook returns the distribution from the same store
    rows = read_capture_graph_ops(fallback)
    assert len(rows) == 1
    dist = capture_graph_ops_distribution(fallback)
    assert dist["sessions"] == 1
    assert dist["median"] == 5.0
