"""#13108 — the dense leg must not pay for an index the store has measured absent.

The read path already knows which labels carry a vector index
(``FalkorProjection._vector_indexed_labels``, filled by
``_record_vector_index_inventory`` — the same measurement behind the `#4997`
"unindexed for label(s)" notice). On an unindexed label the two
``CALL db.idx.vector.queryNodes`` attempts can only be REFUSED, and this engine
answers ``Invalid arguments for procedure`` for a missing index AND for a wrong
argument order alike — measured on FalkorDB v4.20.6 with
``tools/dense_leg_probe_13108.py``, where the two refused round trips cost
**20.17 ms of a 34.45 ms ``:Object`` dense read at 1,000 nodes** (58 %;
``fix-1000-paired-at-rest.json`` → ``index_attempt[0].elapsed_ms``, 10.18 + 9.97).
⛔ ``#13121`` review round 4 (F5) corrected an earlier ``≈23 ms of 36.5 ms (63 %)``
here: 23.45 ms is the SCAN's cost and 36.5 ms was a single sample, not an arm
statistic. The figures now match `tortoise/search_engine.py:3650` and brief §3.4.

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


# ── the inventory's BEHAVIOURAL lane, not its reported pair (review round 4) ──

class _InventoryStub:
    """Just enough of a projection to drive the REAL inventory method."""

    def __init__(self, catalogue_rows):
        self._is_embedded = False
        self._falkordb_version = (4, 20, 6)
        self._graph_name = "g13108"
        self._catalogue_rows = catalogue_rows
        self._vector_indexed_labels = "UNSET"
        self._vector_indexed_served_labels = "UNSET"
        self._vector_indexed_labels_for_reads = "UNSET"

    def _stable_endpoint(self):
        return "stub-endpoint-13108"


def _measure_inventory(rows):
    """Drive the shipped `_record_vector_index_inventory` over `rows`."""
    from tortoise.projection import FalkorProjection

    stub = _InventoryStub(rows)
    FalkorProjection._record_vector_index_inventory(stub, catalogue_rows=rows)
    return stub


def test_a_reshaped_catalogue_is_UNMEASURED_for_behaviour_only():
    """⛔ `set()` is two facts: measured-empty, and "returned but unparseable".

    `#11087` pins the REPORTING semantic: a catalogue that comes back as a row
    list whose rows do not parse reads as "measured and empty", because rows are
    parsed per row and an unparseable row is skipped. That is right for a
    message. It is wrong for a DECISION: a read that skips its index attempt on
    that evidence would demote every dense read — `:Point` included, the label
    the product ships indexed — to a full scan after an engine catalogue
    reshape, with only an INFO pair as the signal. So the behavioural half is
    `None` (NOT MEASURED) in exactly this lane, and the reported pair is NOT
    narrowed.
    """
    # Every row is unparseable: not a sequence of >= 3 columns, or a non-dict
    # `types` column — the shapes an engine re-shape produces.
    reshaped = [("Point",), ("Point", "embedding"), "Point", {"label": "Point"},
                ("Point", "embedding", ["VECTOR"])]
    stub = _measure_inventory(reshaped)

    assert stub._vector_indexed_labels == set(), (
        "the REPORTED pair must keep #11087's pinned semantic — an answered "
        f"catalogue is 'measured and empty', got {stub._vector_indexed_labels!r}")
    assert stub._vector_indexed_served_labels is not None
    assert stub._vector_indexed_labels_for_reads is None, (
        "the BEHAVIOURAL half must be NOT MEASURED when nothing in the "
        "catalogue parsed — acting on 'nothing is indexed' here would demote "
        "every dense read, `:Point` included, to a full scan")


def test_an_indexless_catalogue_is_measured_empty_for_BOTH_halves():
    """A genuinely index-less engine answers `[]` — that IS "nothing indexed"."""
    stub = _measure_inventory([])

    assert stub._vector_indexed_labels == set()
    assert stub._vector_indexed_labels_for_reads == set(), (
        "an empty catalogue is a real measurement, so the behavioural half must "
        "skip the doomed attempts rather than fall back to them")


def test_a_parsed_catalogue_populates_BOTH_halves_identically():
    """The common lane: the two halves agree, and only served labels are kept."""
    rows = [("Point", ["embedding"], {"embedding": ["VECTOR"]}),
            ("Object", ["embedding"], {"embedding": ["FULLTEXT"]}),
            ("NotServed", ["embedding"], {"embedding": ["VECTOR"]})]
    stub = _measure_inventory(rows)

    assert stub._vector_indexed_labels == {"Point"}
    assert stub._vector_indexed_labels_for_reads == {"Point"}


def test_the_sdk_forwards_the_BEHAVIOURAL_half_at_every_call_site():
    """A revert to `_vector_indexed_labels` re-opens the P0 the lane exists for.

    Static on purpose: the three call sites are inside large SDK methods, and
    what can silently drift is the ATTRIBUTE NAME. Every
    `vector_indexed_labels=` in `tortoise/sdk.py` must read the behavioural
    half.
    """
    import re
    from pathlib import Path

    source = Path("tortoise/sdk.py").read_text(encoding="utf-8")
    sites = re.findall(r"vector_indexed_labels=getattr\(\s*proj,\s*"
                       r"\"([A-Za-z_]+)\"", source)
    assert len(sites) == 3, f"expected 3 sdk call sites, found {len(sites)}"
    assert set(sites) == {"_vector_indexed_labels_for_reads"}, (
        "an sdk call site reads the REPORTED pair — that value collapses "
        f"'returned but unparseable' into an empty set: {sites}")
