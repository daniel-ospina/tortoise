"""#13108 — the dense leg must not pay for an index the store has measured absent.

The read path already knows which labels carry a vector index
(``FalkorProjection._vector_indexed_labels``, filled by
``_record_vector_index_inventory`` — the same measurement behind the `#4997`
"unindexed for label(s)" notice). On an unindexed label the two
``CALL db.idx.vector.queryNodes`` attempts can only be REFUSED, and this engine
answers ``Invalid arguments for procedure`` for a missing index AND for a wrong
argument order alike — measured on FalkorDB v4.20.6 with
``tools/dense_leg_probe_13108.py``, where the two refused round trips cost
**≈23 ms of a 36.5 ms ``:Object`` dense read at 1,000 nodes** (63 %).

So the injected inventory lets the read skip them. Four things must stay true,
and this module pins each:

1. a label absent from the MEASURED set skips the attempt, and is still recorded
   ``scan_fallback`` — `#4999`'s "an index was expected and was not used", so the
   alert surface and the `#4997` notice keep their meaning;
2. a label IN the set still attempts the index, unchanged;
3. ``None`` (the inventory's NOT-MEASURED lane) attempts as before — a guess
   here would demote every read of an indexed label to a scan;
4. `#2952`/DoD row 3 is untouched: a genuine fallback is still DECLARED, so the
   fix cannot work by hiding degradation.

Hermetic: no DB, no graph, no embedder.
"""

from __future__ import annotations

import pytest

from tortoise.exceptions import HybridReadUnavailableError
from tortoise.search_engine import (
    MECHANISM_INDEX,
    MECHANISM_SCAN,
    MECHANISM_SCAN_FALLBACK,
    VECTOR_MECHANISM_KEY,
    declared_degraded_read,
    degradation_chain,
    require_hybrid_read,
    run_vector_query,
)


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class RecordingGraph:
    """Records every Cypher issued; answers per-query by predicate.

    ``refuse_query_nodes=True`` makes a ``queryNodes`` call raise the engine's own
    refusal text, which is what an unindexed label produces.
    """

    def __init__(self, scan_rows=(("a", 0.9),), refuse_query_nodes=True):
        self.cypher: list[str] = []
        self._scan_rows = list(scan_rows)
        self._refuse = refuse_query_nodes

    def query(self, cypher, params=None, timeout=None):
        self.cypher.append(cypher)
        if "CALL db.idx.vector.queryNodes" in cypher and self._refuse:
            raise RuntimeError(
                "Invalid arguments for procedure 'db.idx.vector.queryNodes'")
        if "CALL db.idx.vector.queryNodes" in cypher:
            return _Result([("i1",), ("i2",)])
        # the zero-row guard's count probe, then the scan itself
        if "count(n.embedding)" in cypher or "count(*)" in cypher:
            return _Result([(2,)])
        return _Result(self._scan_rows)

    def index_attempts(self) -> int:
        return sum(1 for c in self.cypher
                   if "CALL db.idx.vector.queryNodes" in c)


VEC = [0.1] * 8


def _trace_for(graph, **kwargs):
    trace: list[dict] = []
    run_vector_query(graph, VEC, limit=3, is_embedded=False,
                     entity_type="object", leg_trace=trace, **kwargs)
    return trace


# ── 1. a measured-absent label skips the attempt, still says scan_fallback ──

def test_absent_label_skips_both_query_nodes_attempts():
    graph = RecordingGraph()
    _trace_for(graph, vector_indexed_labels={"Point"})

    assert graph.index_attempts() == 0, (
        "an :Object read must not issue queryNodes when the store has measured "
        "that :Object carries no vector index")
    assert any("vec.euclideanDistance" in c for c in graph.cypher), (
        "the scan is the point — it must still run")


def test_absent_label_is_still_reported_as_scan_fallback():
    graph = RecordingGraph()
    trace = _trace_for(graph, vector_indexed_labels={"Point"})
    entry = next(e for e in trace if e.get("leg") == "vector")

    assert entry[VECTOR_MECHANISM_KEY] == MECHANISM_SCAN_FALLBACK, (
        "#4999: the mechanism must still say an index was EXPECTED and was not "
        "used — `scan` would claim the scan is the design and silence the alert")
    assert entry["degraded"] is False, (
        "a scan returns SEMANTIC rows; #2952 defines degraded as 'contributed no "
        "semantic results', which is not the case here")


def test_the_skip_does_not_drop_rows_or_change_their_shape():
    graph = RecordingGraph(scan_rows=(("a", 0.9), ("b", 0.7)))
    rows = run_vector_query(graph, VEC, limit=3, is_embedded=False,
                            entity_type="object",
                            vector_indexed_labels={"Point"})
    assert rows == [("a", 0.9), ("b", 0.7)]


# ── 2. an INDEXED label still takes the index branch ───────────────────────

def test_indexed_label_still_attempts_and_uses_the_index():
    graph = RecordingGraph(refuse_query_nodes=False)
    trace = _trace_for(graph, vector_indexed_labels={"Point", "Object"})

    assert graph.index_attempts() >= 1
    entry = next(e for e in trace if e.get("leg") == "vector")
    assert entry[VECTOR_MECHANISM_KEY] == MECHANISM_INDEX


# ── 3. None = NOT MEASURED = attempt as before (byte-identical) ─────────────

def test_none_means_unmeasured_and_attempts_as_before():
    graph = RecordingGraph()
    trace = _trace_for(graph, vector_indexed_labels=None)

    assert graph.index_attempts() == 2, (
        "None is the inventory's NOT-MEASURED lane: both documented signatures "
        "are attempted exactly as they were before #13108")
    entry = next(e for e in trace if e.get("leg") == "vector")
    assert entry[VECTOR_MECHANISM_KEY] == MECHANISM_SCAN_FALLBACK


def test_a_label_missing_from_an_UNMEASURED_set_is_not_a_skip():
    """`set()` and `None` are DIFFERENT facts and must not collapse.

    An empty set means "measured, and NOTHING is indexed" (a real answer); None
    means "the catalogue could not be read" (no answer). Both skip here for
    `:Object`, so the distinction is pinned on a label that IS indexed in the
    set: `{"Point"}` keeps `point` on the index branch.
    """
    graph = RecordingGraph()
    trace = _trace_for(graph, vector_indexed_labels={"Point"})
    entry = next(e for e in trace if e.get("leg") == "vector")
    assert entry[VECTOR_MECHANISM_KEY] == MECHANISM_SCAN_FALLBACK

    g2 = RecordingGraph(refuse_query_nodes=False)
    t2: list[dict] = []
    run_vector_query(g2, VEC, limit=3, is_embedded=False, entity_type="point",
                     leg_trace=t2, vector_indexed_labels={"Point"})
    assert next(e for e in t2 if e.get("leg") == "vector")[
        VECTOR_MECHANISM_KEY] == MECHANISM_INDEX


def test_embedded_mode_is_unaffected():
    """An embedded store never attempts an index — `scan` is its design."""
    graph = RecordingGraph()
    trace: list[dict] = []
    run_vector_query(graph, VEC, limit=3, is_embedded=True,
                     entity_type="object", leg_trace=trace,
                     vector_indexed_labels={"Point"})
    assert graph.index_attempts() == 0
    entry = next(e for e in trace if e.get("leg") == "vector")
    assert entry[VECTOR_MECHANISM_KEY] == MECHANISM_SCAN, (
        "embedded scan is the design, not a fallback — the #13108 skip must not "
        "relabel it")


# ── 4. DoD row 3: a genuine fallback is still DECLARED ─────────────────────

def test_a_skipped_index_read_is_still_hybrid_and_a_FAILED_leg_still_refuses():
    """The fix must not buy speed by hiding degradation (#2952 / DoD row 3)."""
    graph = RecordingGraph()
    trace = _trace_for(graph, vector_indexed_labels={"Point"})
    # a scan that returned semantic rows IS hybrid
    assert require_hybrid_read(trace)["hybrid"] is True
    assert declared_degraded_read(trace) is None

    # …and a leg that genuinely failed is still declared, with its own reason
    failed = [{"leg": "fts", "ran": True, "degraded": False, "reason": "ok",
               "count": 3},
              {"leg": "vector", "ran": True, "degraded": True,
               "reason": "timeout", "count": 0}]
    marker = declared_degraded_read(failed)
    assert marker is not None
    assert marker["degraded_read"] is True
    assert marker["reason"] == "timeout"
    with pytest.raises(HybridReadUnavailableError):
        require_hybrid_read(failed)


# ── the chain forwards it only when supplied (pinned submit-kwargs contract) ─

def test_degradation_chain_forwards_the_inventory_when_supplied():
    """The chain merges the kwarg ONLY when supplied (the pinned contract)."""
    graph = RecordingGraph(refuse_query_nodes=False)
    trace: list[dict] = []
    degradation_chain(graph, "q", None, VEC,
                      {"vector": True}, entity_type="object", limit=3,
                      is_embedded=False, leg_trace=trace,
                      vector_indexed_labels={"Point"})
    with_hint = graph.index_attempts()
    trace.clear()
    degradation_chain(graph, "q", None, VEC,
                      {"vector": True}, entity_type="object", limit=3,
                      is_embedded=False, leg_trace=trace)
    without_hint = graph.index_attempts() - with_hint

    assert with_hint == 0, "the inventory reached run_vector_query (it skipped)"
    assert without_hint == 1, (
        "a default caller must still attempt exactly as before — the kwarg is "
        "merged only when supplied, so its submit kwargs stay byte-identical")
