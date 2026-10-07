"""#4999 — the vector leg's trace must tell an INDEXED query from a full SCAN.

Class B (mechanical architecture conformance): the decision is already
recorded — *a fallback must be visible* — so these tests assert the
**contract**, not the code. Two paths with very different costs (measured:
4.97 ms indexed vs 16.96 ms full scan of 7,859 :Object vectors) used to emit
an IDENTICAL leg-trace entry, so a caller could not tell them apart without
reading ``search_engine.py``. From #4999 the entry carries ``mechanism``.

⚠️ The trap this file exists to defeat: a test asserting ``reason == "ok"`` on
a healthy path PASSES on the pre-fix code, because both paths said ``ok``.
The table below divides the two kinds deliberately: the **falsifiers** fail on
the pre-fix revision (the contract is absent/invisible there), while the two
**anti-overfix guards** pass pre-fix *by design* — they defend the contracts
this fix must not break (#2952's ``degraded``, the R2 #1541 shared shape).

| test | kind | fails when … |
|---|---|---|
| index path reports ``index`` | falsifier | the key is absent (pre-fix) or names the scan |
| docker fallback reports ``scan_fallback`` | falsifier | the key is absent (pre-fix) or says ``ok``/``scan`` |
| embedded scan reports ``scan`` | falsifier | the key is absent (pre-fix) |
| index vs scan are DISTINGUISHABLE | falsifier | both paths report the same mechanism (the defect) |
| zero-row fallback still reports the mechanism | falsifier | the zero-row exit drops the key |
| fallback keeps ``degraded=False`` + is not a declared degraded read | anti-overfix guard | someone "fixes" #4999 by flipping ``degraded`` (breaks #2952) |
| fts/structural entries do NOT gain the key | anti-overfix guard | the additive key leaks into the R2 #1541 shared shape |
| the chain merge carries the key | falsifier | ``degradation_chain``'s private-list merge drops it |
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.search_engine import (
    MECHANISM_INDEX,
    MECHANISM_SCAN,
    MECHANISM_SCAN_FALLBACK,
    VECTOR_LEG_UNAVAILABLE,
    VECTOR_MECHANISM_KEY,
    declared_degraded_read,
    degradation_chain,
    require_hybrid_read,
    reset_circuit_breakers,
    run_fts_query,
    run_structural_query,
    run_vector_query,
)

VEC = [0.1] * 384


@pytest.fixture(autouse=True)
def _reset_breakers():
    """Circuit-breaker state is module-level — isolate every test."""
    reset_circuit_breakers()
    yield
    reset_circuit_breakers()


class _MockResultSet:
    def __init__(self, result_set):
        self.result_set = result_set


class _FixedGraph:
    """Returns one fixed result_set for every query; raises on demand."""

    def __init__(self, result_set=None, raise_on_query=None):
        self._result_set = result_set or []
        self._raise_on_query = raise_on_query

    def query(self, cypher, params=None, timeout=None):
        if self._raise_on_query:
            raise self._raise_on_query
        return _MockResultSet(self._result_set)


class _ScriptedGraph:
    """Returns a different (result_set, exc) per call, in call order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._i = 0

    def query(self, cypher, params=None, timeout=None):
        if self._i >= len(self._responses):
            return _MockResultSet([])
        result_set, exc = self._responses[self._i]
        self._i += 1
        if exc:
            raise exc
        return _MockResultSet(result_set or [])


class _RoutingGraph:
    """Routes by cypher substring — for the concurrent ``degradation_chain``."""

    def __init__(self, routes):
        #: list of (substring, result_set, exc)
        self._routes = routes

    def query(self, cypher, params=None, timeout=None):
        for sub, result_set, exc in self._routes:
            if sub in cypher:
                if exc:
                    raise exc
                return _MockResultSet(result_set or [])
        return _MockResultSet([])


def _vec_entry(trace):
    return next(e for e in trace if e["leg"] == "vector")


def _index_path_trace(entity_type="object"):
    """A docker-mode query served by the HNSW index (sig A → (id,) rows)."""
    graph = _FixedGraph(result_set=[("a",), ("b",)])
    trace: list[dict] = []
    run_vector_query(graph, VEC, limit=5, is_embedded=False,
                     entity_type=entity_type, leg_trace=trace)
    return trace


def _scan_fallback_trace(entity_type="object"):
    """A docker-mode query whose index FAILED and fell through to the scan."""
    graph = _ScriptedGraph([
        (None, Exception("vector index does not exist for label Object")),
        ([("a", 0.9), ("b", 0.7)], None),
    ])
    trace: list[dict] = []
    run_vector_query(graph, VEC, limit=5, is_embedded=False,
                     entity_type=entity_type, leg_trace=trace)
    return trace


class TestVectorMechanismAndDistinction:
    """The mechanism is reported, and the two paths are tellable apart."""

    def test_index_path_reports_index(self):
        """Index-accelerated path → mechanism='index'."""
        entry = _vec_entry(_index_path_trace())
        assert entry[VECTOR_MECHANISM_KEY] == MECHANISM_INDEX

    def test_docker_fallback_reports_scan_fallback(self):
        """Index attempted and failed → mechanism='scan_fallback' (the
        silent fallback #4999 makes visible)."""
        entry = _vec_entry(_scan_fallback_trace())
        assert entry[VECTOR_MECHANISM_KEY] == MECHANISM_SCAN_FALLBACK

    def test_embedded_scan_reports_plain_scan(self):
        """Embedded module — no index is attempted (the scan is the design),
        so the mechanism is the plain 'scan', NOT a fallback."""
        graph = _FixedGraph(result_set=[("a", 0.9)])
        trace: list[dict] = []
        run_vector_query(graph, VEC, limit=5, is_embedded=True, leg_trace=trace)
        assert _vec_entry(trace)[VECTOR_MECHANISM_KEY] == MECHANISM_SCAN

    def test_index_and_scan_are_distinguishable(self):
        """THE contract: an index-used query and a scan must report DIFFERENT
        mechanisms. This is the test the pre-fix code fails — both paths
        reported the same entry, so a caller could not tell them apart."""
        m_index = _vec_entry(_index_path_trace()).get(VECTOR_MECHANISM_KEY)
        m_scan = _vec_entry(_scan_fallback_trace()).get(VECTOR_MECHANISM_KEY)
        assert m_index, "index path must report a mechanism"
        assert m_scan, "scan path must report a mechanism"
        assert m_index != m_scan, (
            "an indexed query and a table scan reported the SAME mechanism "
            f"({m_index!r}) — the degradation is invisible to a caller"
        )
        # …and the embedded scan is a THIRD value, distinct from the fallback:
        # "an index was expected and was not used" must not be laundered into
        # the normal embedded design.
        assert m_scan != MECHANISM_SCAN

    def test_zero_row_scan_fallback_still_reports_the_mechanism(self):
        """The zero-row exits carry the mechanism too — a scan that found
        nothing is still a scan."""
        graph = _ScriptedGraph([
            (None, Exception("vector index does not exist")),
            ([], None),        # brute-force returns no rows
            ([(0,)], None),    # count guard: zero embedded points
        ])
        trace: list[dict] = []
        run_vector_query(graph, VEC, limit=5, is_embedded=False,
                         entity_type="object", leg_trace=trace)
        entry = _vec_entry(trace)
        assert entry["reason"] == "no_embeddings"
        assert entry[VECTOR_MECHANISM_KEY] == MECHANISM_SCAN_FALLBACK


class TestDegradedContractPreserved:
    """#4999 must NOT be delivered by flipping ``degraded``.

    #2952 defines ``degraded`` on the vector leg as "contributed no semantic
    results". A brute-force scan DOES return semantic rows, so ``degraded``
    must stay False and the read must still certify as hybrid — otherwise
    :func:`require_hybrid_read` would refuse a valid semantic read.
    """

    def test_fallback_keeps_degraded_false_and_certifies_as_hybrid(self):
        trace = _scan_fallback_trace()
        entry = _vec_entry(trace)
        assert entry["degraded"] is False
        assert entry["ran"] is True
        assert declared_degraded_read(trace) is None
        assert require_hybrid_read(trace) == {
            "hybrid": True, VECTOR_LEG_UNAVAILABLE: False}


class TestSharedShapeUnchanged:
    """The key is ADDITIVE — fts/structural entries keep the R2 #1541 shape."""

    def test_fts_entry_has_no_mechanism_key(self):
        graph = _FixedGraph(result_set=[("p1", 0.9)])
        trace: list[dict] = []
        run_fts_query(graph, "hello", leg_trace=trace)
        assert "mechanism" not in trace[-1]
        assert set(trace[-1]) == {"leg", "ran", "degraded", "reason", "count"}

    def test_structural_entry_has_no_mechanism_key(self):
        graph = _FixedGraph(result_set=[("p1", 1.0)])
        trace: list[dict] = []
        run_structural_query(graph, "statement", leg_trace=trace)
        assert "mechanism" not in trace[-1]


class TestMechanismSurvivesTheChain:
    """The field must survive ``degradation_chain``'s private-list merge."""

    def test_chain_merge_keeps_the_scan_fallback_mechanism(self):
        graph = _RoutingGraph([
            ("euclideanDistance", [("obj-1", 0.9)], None),
            ("vector.queryNodes", [],
             Exception("vector index does not exist for label Object")),
            ("fulltext.queryNodes", [("obj-1", 2.0)], None),
        ])
        trace: list[dict] = []
        degradation_chain(
            graph, "hello", None, VEC,
            {"fts": True, "vector": True, "structural": False},
            entity_type="object", is_embedded=False, leg_trace=trace,
        )
        entry = _vec_entry(trace)
        assert entry["ran"] is True
        assert entry[VECTOR_MECHANISM_KEY] == MECHANISM_SCAN_FALLBACK
