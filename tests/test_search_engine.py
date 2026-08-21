"""Tests for tortoise.search_engine — RRF fusion, classifier, degradation."""
from __future__ import annotations  # noqa: I001

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.search_engine import (
    classify_query, rrf_fusion, SearchResult, SearchScores,
    EpBreakdown, EpEvidence, annotate_ep_batch, reset_circuit_breakers,
    run_fts_query, run_vector_query,
)

# ── Live-FalkorDB availability (mirrors tests/test_hnsw_vector_index.py) ──
# test_sdk_document_search_returns_metadata connects to docker://localhost:16379.
# Probe at module load so it skips gracefully in embedded-only CI (#493).
FALKORDB_AVAILABLE = False
try:
    from tortoise.projection import FalkorProjection as _FP
    _old_uri = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = "docker://:@localhost:16379/tortoise_test_sdk125"
    _probe = _FP.from_uri(os.environ["TORTOISE_DB_URI"])
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False
finally:
    if _old_uri is not None:
        os.environ["TORTOISE_DB_URI"] = _old_uri
    else:
        os.environ.pop("TORTOISE_DB_URI", None)


class TestClassifyQuery:
    def test_full_scan(self):
        result = classify_query(None, None)
        assert result == {"fts": False, "vector": False, "structural": True}

    def test_text_query(self):
        result = classify_query("hello", None)
        assert result == {"fts": True, "vector": True, "structural": True}

    def test_kind_only(self):
        result = classify_query(None, "statement")
        assert result == {"fts": False, "vector": False, "structural": True}

    def test_text_with_kind_and_context(self):
        result = classify_query("test", "statement")
        assert result == {"fts": True, "vector": True, "structural": True}


class TestRRFFusion:
    def test_single_strategy(self):
        fused = rrf_fusion([[("a", 1.0), ("b", 0.5)]])
        assert "a" in fused
        assert "b" in fused

    def test_two_strategies(self):
        lists = [[("a", 1.0), ("b", 0.5)], [("b", 0.9), ("a", 0.3)]]
        fused = rrf_fusion(lists)
        # With both swapping ranks → tied RRF scores
        assert abs(fused["a"] - fused["b"]) < 0.001

    def test_empty_list_handling(self):
        fused = rrf_fusion([[], [("a", 1.0)]])
        assert "a" in fused

    def test_all_empty(self):
        fused = rrf_fusion([[], []])
        assert fused == {}

    def test_ranking_prefers_dual_matches(self):
        # a appears in both lists, c only in one
        lists = [[("a", 1.0), ("c", 0.5)], [("a", 0.8), ("b", 1.0)]]
        fused = rrf_fusion(lists)
        assert fused["a"] > fused["c"]


class TestSearchResult:
    def test_to_dict(self):
        r = SearchResult(
            id="1", content="test", point_kind="statement", scores=SearchScores(fts=0.8, rrf=0.9),
            match_source="rrf",
            ep=EpBreakdown(
                confidence_mean=0.7,
                evidence=EpEvidence(impl_count=3, nand_count=1, total=4),
                contention=0.25,
            ),
        )
        d = r.to_dict()
        assert d["id"] == "1"
        assert d["scores"]["fts"] == 0.8
        assert d["scores"]["rrf"] == 0.9
        assert d["ep"]["confidence_mean"] == 0.7
        assert d["ep"]["evidence"]["impl_count"] == 3
        assert d["ep"]["contention"] == 0.25

    def test_to_dict_no_scores(self):
        r = SearchResult(id="1", content="test", point_kind="statement")
        d = r.to_dict()
        assert "scores" not in d
        assert "ep" not in d


class TestEpBreakdown:
    def test_defaults(self):
        ep = EpBreakdown()
        assert ep.confidence_mean == 0.0
        assert ep.contention == 0.0

    def test_evidence_counts(self):
        ev = EpEvidence(impl_count=5, nand_count=2, total=7)
        assert ev.impl_count == 5
        assert ev.nand_count == 2


class TestAnnotateEpBatch:
    def test_empty_ids(self):
        result = annotate_ep_batch(None, [])
        assert result == {}

    def test_no_graph(self):
        # Without a real graph, should return empty dict gracefully
        result = annotate_ep_batch(None, ["test-id"])
        assert isinstance(result, dict)


# ------------------------------------------------------------------ R3 #1542 D4 leg trace


def _trace_sdk(tmp_path):
    from tortoise.sdk import TortoiseSDK
    return TortoiseSDK(str(tmp_path / "vectrace.db"))


def _embedder_or_skip():
    """Skip the dense-leg assertions when the embedder/model cache is
    unavailable (R3 tests run where CI guarantees .[test,embeddings] + the
    HF model cache; skip-if-no-embedder keeps this module green anywhere)."""
    from tortoise.embeddings import EmbeddingModel
    m = EmbeddingModel.get(load_timeout=120)
    if m is None:
        pytest.skip("sentence-transformers / all-MiniLM-L6-v2 cache "
                    "not available — dense-leg assertion skipped")
    return m


def test_vector_trace_no_embeddings_zero_row_guard(tmp_path):
    """D4 (R3): an empty-embedding graph → run_vector_query records
    reason='no_embeddings' via the EXPLICIT zero-row guard — the success
    path returns [] WITHOUT raising, so the exception-only catch never fires
    for the real empty case (review P1)."""
    sdk = _trace_sdk(tmp_path)
    try:
        # raw projection write — a Point with NO embedding (bypasses
        # create_point's optional embedding entirely)
        sdk._get_proj().g.query(
            "CREATE (p:Point {id:'p1', content:'hello', "
            "pointKind:'statement'})")
        trace: list[dict] = []
        hits = run_vector_query(sdk._get_proj().g, [0.0] * 384, limit=5,
                                leg_trace=trace)
        assert hits == []
        vec = next(e for e in trace if e["leg"] == "vector")
        assert vec["ran"] is True
        assert vec["count"] == 0
        assert vec["reason"] == "no_embeddings"
    finally:
        sdk.close()


def test_vector_trace_empty_results_when_embedded_but_excluded(tmp_path):
    """D4 (R3): embedded points present but ALL excluded by the status
    filter → 0-row result set with embedded points present →
    reason='empty_results' (NOT no_embeddings — the count guard
    distinguishes them)."""
    _embedder_or_skip()
    sdk = _trace_sdk(tmp_path)
    try:
        sdk.create_point("statement", "hello world", id="p1",
                         status="retracted")
        trace: list[dict] = []
        hits = run_vector_query(sdk._get_proj().g, [0.0] * 384, limit=5,
                                excluded_statuses=("retracted",),
                                leg_trace=trace)
        assert hits == []
        vec = next(e for e in trace if e["leg"] == "vector")
        assert vec["reason"] == "empty_results"
        assert vec["ran"] is True
    finally:
        sdk.close()


def test_vector_trace_ok_on_healthy_graph(tmp_path):
    """D4 (R3): a healthy embedded graph → run_vector_query records
    reason='ok' with count == len(hits)."""
    _embedder_or_skip()
    sdk = _trace_sdk(tmp_path)
    try:
        sdk.create_point("statement", "hello world", id="p1")
        trace: list[dict] = []
        hits = run_vector_query(sdk._get_proj().g, [0.0] * 384, limit=5,
                                leg_trace=trace)
        assert len(hits) == 1
        vec = next(e for e in trace if e["leg"] == "vector")
        assert vec["reason"] == "ok"
        assert vec["count"] == 1
    finally:
        sdk.close()


def test_vector_trace_poisoned_plain_list_node(tmp_path):
    """D4 (R3): a malformed plain-list embedding node poisons the brute-
    force MATCH (vecf32 throws per-row — 'Type mismatch: expected Null or
    Vectorf32 but was List') → reason='query_failed' (NOT no_embeddings)."""
    _embedder_or_skip()
    sdk = _trace_sdk(tmp_path)
    try:
        sdk.create_point("statement", "healthy vecf32 point", id="healthy")
        sdk._get_proj().g.query(
            "CREATE (p:Point {id:'poisoned', content:'board game', "
            "pointKind:'statement', embedding:[0.1, 0.2]})")
        trace: list[dict] = []
        hits = run_vector_query(sdk._get_proj().g, [0.0] * 384, limit=5,
                                leg_trace=trace)
        assert hits == []
        vec = next(e for e in trace if e["leg"] == "vector")
        assert vec["reason"] == "query_failed"
    finally:
        sdk.close()


def test_fts_trace_index_missing(tmp_path, monkeypatch):
    """D4 (R3): the PROMOTED FTS index-missing catch — an index-not-found
    driver error records reason='index_missing' (surface 11's owned
    negative: 'fts skipped (no index)' is truthful from R3 forward, never
    conflated with 'fts ran, no results'). Embedded FalkorDBLite's
    queryNodes returns empty instead of raising, so the driver error is
    simulated (the promoted catch is the unit under test)."""
    sdk = _trace_sdk(tmp_path)
    try:
        proj = sdk._get_proj()
        # proj.g is a read-only _GuardedGraph — patch the underlying handle
        def _raise_index(*a, **kw):
            raise RuntimeError("index does not exist: Point")
        monkeypatch.setattr(proj.g._g, "query", _raise_index)
        trace: list[dict] = []
        hits = run_fts_query(proj.g, "hello", leg_trace=trace)
        assert hits == []
        fts = next(e for e in trace if e["leg"] == "fts")
        assert fts["reason"] == "index_missing"
        assert fts["degraded"] is True
    finally:
        sdk.close()


# ------------------------------------------------------------------ #125 SDK document metadata


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")
def test_sdk_document_search_returns_metadata():
    """#125: tortoise_search(entity_type='document') returns capture metadata.
    #167: includes sourcePath in results.
    #1568: hermetic under parallel-suite load — (1) the module-level search
    circuit breakers (shared across every test in this pytest process) can be
    left OPEN by an earlier test's failed queries under load, which would
    short-circuit FTS/vector BEFORE this test's query runs; reset them so this
    test sees deterministic breaker state. (2) The search query is retried a
    couple of times with backoff: under a loaded 2-core runner all strategies
    can degrade inside the 500ms collective cap on the FIRST call, but the
    FTS index is synchronous — the document is found as soon as the server
    answers, so a retry is a legitimate read, never a re-do.
    """
    import time

    from tortoise.projection import FalkorProjection
    reset_circuit_breakers()
    # #1585: treat a set-but-EMPTY TORTOISE_DB_URI as unset (a leaked ""
    # from an earlier test must not short-circuit to the default-less empty
    # string and blow up from_uri with "Unsupported scheme").
    uri = os.environ.get("TORTOISE_DB_URI") or "docker://:@localhost:16379/tortoise_test_sdk125"
    proj = FalkorProjection.from_uri(uri)
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj._ensure_indexes()
    proj.g.query(
        "CREATE (d:Document {id:'test-sdk-doc', title:'Conv', "
        "documentKind:'transcript', topics:['licensing'], "
        "summary:'Test', sessionId:'s1', eventId:'e1', "
        "sourcePath:'/tmp/conv.md', "
        "_searchText:'Conv Test licensing'})"
    )
    try:
        from tortoise.sdk import TortoiseSDK
        # Construct the SDK with the live URI, NOT the embedded default path:
        # a URI-based SDK never spawns a redislite server, so this test adds
        # zero embedded-DB churn to the shared temp-DB topology (#1568). The
        # projection is replaced immediately below — the SDK's own lazy URI
        # projection is never created.
        _old_uri = os.environ.get("TORTOISE_DB_URI")
        os.environ["TORTOISE_DB_URI"] = uri
        try:
            sdk = TortoiseSDK()
        finally:
            if _old_uri is None:
                os.environ.pop("TORTOISE_DB_URI", None)
            else:
                os.environ["TORTOISE_DB_URI"] = _old_uri
        sdk._proj = proj
        # #1568: transient load can starve the first call — retry (idempotent
        # read) before failing. Bounded: 3 attempts, ~1.5s worst-case added.
        results = []
        doc = None
        for attempt in range(3):
            results = sdk.tortoise_fts_query("licensing", entity_type="document")
            doc = next((r for r in results if r.get("id") == "test-sdk-doc"), None)
            if doc is not None:
                break
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
        assert doc, f"doc not in results after retries: {results}"
        assert doc["topics"] == ["licensing"], doc.get("topics")
        assert doc["summary"] == "Test"
        assert doc["sessionId"] == "s1"
        assert doc["eventId"] == "e1"
        assert doc.get("sourcePath") == "/tmp/conv.md", doc.get("sourcePath")
    finally:
        proj.close()


# ------------------------------------------------------------------ #198 limit validation


class TestTortoiseFtsQueryLimit:
    """#198: limit validation in tortoise_fts_query — bound at 1-10000."""

    def test_limit_10001_raises_value_error(self):
        """limit=10001 rejects with clear error."""
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()
        with pytest.raises(ValueError, match="limit must be 1-10000"):
            sdk.tortoise_fts_query("test", limit=10001)

    def test_limit_0_raises_value_error(self):
        """limit=0 (below floor) still raises."""
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()
        with pytest.raises(ValueError, match="limit must be 1-10000"):
            sdk.tortoise_fts_query("test", limit=0)

    def test_limit_10000_allowed_no_value_error(self):
        """limit=10000 is allowed — validation passes.

        The call may fail later (no DB connection), but it must not
        raise ValueError.
        """
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()
        try:
            sdk.tortoise_fts_query("test", limit=10000)
        except ValueError:
            pytest.fail("limit=10000 should not raise ValueError")
        except Exception:
            pass  # ConnectionError expected without DB

    def test_default_limit_unaffected(self):
        """Default limit=10 still works (validation passes)."""
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()
        try:
            sdk.tortoise_fts_query("test", limit=10)
        except ValueError:
            pytest.fail("default limit=10 should not raise ValueError")
        except Exception:
            pass  # ConnectionError expected without DB
