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

    # R5 (#1544): recency-weighted RRF — date weight in fusion, default-off
    # so every pre-R5 caller stays byte-identical.

    def test_weighted_rrf_default_is_byte_identical(self):
        """#1657: no weights passed → identical to the pre-#1657 arithmetic."""
        lists = [[("a", 1.0), ("b", 0.5)], [("b", 0.9), ("a", 0.3)]]
        plain = rrf_fusion(lists)
        weighted = rrf_fusion(lists, strategy_names=["fts", "vector"],
                              weights=None)
        assert weighted == plain

    def test_weighted_rrf_weights_scale_contribution(self):
        """#1657: a heavier weight on one strategy lifts its rank-1 hit."""
        # doc in list0 only (rank 1), not in list1: with weights the
        # vector leg's contribution is scaled by its weight.
        lists = [[("doc", 1.0)], [("other", 1.0)]]
        equal = rrf_fusion(lists, strategy_names=["fts", "vector"])
        boosted = rrf_fusion(lists, strategy_names=["fts", "vector"],
                             weights={"vector": 3.0})
        # Equal: fts doc = 1/61, vector other = 1/61 → tie.
        assert abs(equal["doc"] - equal["other"]) < 1e-9
        # Weighted: vector leg × 3 → 'other' (from the weighted leg) wins.
        assert boosted["other"] > boosted["doc"]

    def test_weighted_rrf_weights_missing_strategy_uses_one(self):
        """#1657: a strategy absent from the weights map defaults to 1.0."""
        lists = [[("a", 1.0)], [("b", 1.0)]]
        fused = rrf_fusion(lists, strategy_names=["fts", "vector"],
                           weights={"vector": 2.0})
        # fts 'a' = 1/61; vector 'b' = 2/61.
        assert fused["b"] > fused["a"]

    def test_recency_boost_lifts_newer_over_older(self):
        # equal RRF across both docs — date weight must break the tie
        lists = [[("old", 1.0)], [("new", 1.0)]]
        weights = {"new": 1.0, "old": 0.0}
        fused = rrf_fusion(lists, recency_weights=weights, recency_boost=0.5)
        assert fused["new"] > fused["old"]

    def test_recency_defaults_byte_identical(self):
        lists = [[("a", 1.0), ("b", 0.5)]]
        assert rrf_fusion(lists) == rrf_fusion(lists, recency_weights={"a": 1.0}, recency_boost=0.0)
        assert rrf_fusion(lists) == rrf_fusion(lists, recency_weights=None, recency_boost=0.5)

    def test_undated_docs_get_no_boost(self):
        lists = [[("dated", 1.0)], [("undated", 1.0)]]
        fused = rrf_fusion(lists, recency_weights={"dated": 1.0}, recency_boost=0.5)
        assert fused["dated"] > fused["undated"]
        assert abs(fused["undated"] - (1.0 / 61)) < 1e-9  # unchanged contribution


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
        # Issue #2206: the default confidence_mean is the neutral Beta(1,1)
        # mean 0.5 (unmeasured == no information, NOT zero support) — never
        # the old structural edge-ratio default 0.0.
        ep = EpBreakdown()
        assert ep.confidence_mean == 0.5
        assert ep.has_ep is False
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
    # Epic #1647 (T7, cycle-5 P1-6): the env URI may resolve the SHARED job
    # path — bulk-DETACHing it would clobber concurrent sessions; resolve to
    # a per-test test_* graph instead.
    proj = FalkorProjection.from_uri(
        uri, graph_name=f"test_search_engine_doc_{os.urandom(4).hex()}")
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


# ───────────────────────── R2 #1541 OR-union + search_keys ──────────────────

_LIVE_URI = os.environ.get("TORTOISE_DB_URI") or "docker://:@localhost:16379/tortoise_test_sdk125"


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")
class TestR2OrUnionAndSearchKeys:
    """R2 (#1541) integration: OR-union one-token-drop tolerance, search_keys
    index expansion, the multi-field Point FTS migration, and the FTS leg
    trace on the live backend."""

    @pytest.fixture()
    def proj(self):
        from tortoise.projection import FalkorProjection
        reset_circuit_breakers()
        # Epic #1647 (T7): per-test graph — the env/job URI path is shared and
        # this fixture bulk-DETACHes its graph on every test.
        p = FalkorProjection.from_uri(
            _LIVE_URI, graph_name=f"test_search_engine_fts_{os.urandom(4).hex()}")
        p.g.query("MATCH (n) DETACH DELETE n")
        yield p
        reset_circuit_breakers()
        p.close()

    def _seed(self, proj, rows):
        for pid, content, sk in rows:
            proj.g.query(
                "CREATE (n:Point {id:$id, content:$c, search_keys:$sk, "
                "is_operator:false, pointKind:'statement'})",
                params={"id": pid, "c": content, "sk": sk or None},
            )

    def test_one_token_drop_tolerance(self, proj):
        """The R2 regression: two points differing by ONE token; a question
        that strictly ANDs one point's terms zeroes the other — OR-union
        must surface BOTH, overlap-ranked (the doc with more shared terms
        first)."""
        self._seed(proj, [
            ("gym6", "gym schedule 6pm", None),
            ("gym5", "gym schedule 5pm", None),
        ])
        # shared-token query: both surface under any semantics
        hits = run_fts_query(proj.g, "gym schedule", limit=10)
        ids = {h[0] for h in hits}
        assert "gym6" in ids and "gym5" in ids, f"both must surface: {ids}"
        # one-token-drop paraphrase: strict-AND ("gym schedule 5pm" as a raw
        # string) would zero gym6 (no 5pm in content); OR-union surfaces it
        hits = run_fts_query(proj.g, "gym schedule 5pm", limit=10)
        ids = {h[0] for h in hits}
        assert "gym6" in ids, f"previously-zeroed point missing: {ids}"
        assert "gym5" in ids

    def test_search_keys_index_surfaces_alias(self, proj):
        """R2 D3: the multi-field Point FTS index matches unqualified query
        tokens against search_keys — the "question ∪ search_keys" expansion.
        The point's CONTENT lacks the alias tokens entirely."""
        self._seed(proj, [
            ("pb", "personal best 5K time is 27:12",
             "fastest 5k running pb"),
            ("other", "the weather today is sunny", None),
        ])
        # "what's my fastest 5k" → OR-union tokens: fastest|5k
        hits = run_fts_query(proj.g, "what's my fastest 5k", limit=10)
        ids = {h[0] for h in hits}
        assert "pb" in ids, f"search_keys alias not surfaced: {ids}"
        assert "other" not in ids

    def test_write_path_flattens_search_keys(self, proj):
        """R2 D3: sdk.create_point stores search_keys as a FLAT string (E3's
        list payload is flattened on the write path — owner-flagged cross-lane
        deviation, plan D3) and the flat value is FTS-findable."""
        from tortoise.sdk import TortoiseSDK
        _old_uri = os.environ.get("TORTOISE_DB_URI")
        os.environ["TORTOISE_DB_URI"] = _LIVE_URI
        try:
            sdk = TortoiseSDK()
        finally:
            if _old_uri is None:
                os.environ.pop("TORTOISE_DB_URI", None)
            else:
                os.environ["TORTOISE_DB_URI"] = _old_uri
        sdk._proj = proj
        sdk.create_point("statement", "my 5K best is 27:12", id="flat-pb",
                         search_keys=["personal best", "27:12"])
        rows = proj.g.query(
            "MATCH (n:Point {id:'flat-pb'}) RETURN n.search_keys").result_set
        assert rows and rows[0][0] == "personal best 27:12", rows
        hits = run_fts_query(proj.g, "personal best", limit=10)
        assert "flat-pb" in {h[0] for h in hits}

    def test_multi_field_migration_marker_and_no_churn(self):
        """R2 D3 migration: a DB with the LEGACY single-field ('content')
        Point index + pre-R2 LIST-valued search_keys is migrated by
        _ensure_indexes — drop→recreate to ('content', 'search_keys') +
        one-time list flatten, guarded by the point_fts_v2 Meta marker; a
        re-run does not churn (marker guards)."""
        from falkordb import FalkorDB

        from tortoise.projection import FalkorProjection
        reset_circuit_breakers()
        # Epic #1647 (T7, cycle-6 P1-3): the historical FIXED name
        # tortoise_test_r2_migrate is a SHARED test-prefixed graph — the raw
        # client bypasses _assert_test_graph AND the per-test wipe scope, so
        # concurrent sessions DETACH each other's live writes (and the fixed
        # name passes the "test-prefixed" check vacuously). Resolve to a
        # per-test-unique name + journal it (raw-client code is TEST code and
        # CAN import tests/_embedded) so the session-end sweep GRAPH.DELETEs
        # it — the leak is closed, not just renamed (cycle-8 P2-2).
        client = FalkorDB(host="localhost", port=16379)
        gname = f"tortoise_test_r2_migrate_{os.urandom(4).hex()}"
        from tests._embedded import _journal_append
        _journal_append(gname)
        raw = client.select_graph(gname)
        raw.query("MATCH (n) DETACH DELETE n")
        try:  # noqa: SIM105
            raw.query("CALL db.idx.fulltext.drop('Point')")
        except Exception:
            pass
        # legacy state: single-field index + list-valued search_keys
        raw.query("CALL db.idx.fulltext.createNodeIndex('Point', 'content')")
        raw.query(
            "CREATE (n:Point {id:'legacy-pb', "
            "content:'personal best 5K time is 27:12', "
            "search_keys:['fastest 5k','running pb'], "
            "is_operator:false, pointKind:'statement'})")
        # booting the projection runs _ensure_indexes → the migration
        proj = FalkorProjection.from_uri(
            "docker://:@localhost:16379/" + gname)
        try:
            # the legacy LIST was flattened in place
            rows = proj.g.query(
                "MATCH (n:Point {id:'legacy-pb'}) RETURN n.search_keys"
            ).result_set
            assert rows and rows[0][0] == "fastest 5k running pb", rows
            # the two-field index answers a search_keys-only query
            hits = run_fts_query(proj.g, "fastest 5k", limit=10)
            assert "legacy-pb" in {h[0] for h in hits}, \
                f"search_keys-only query failed post-migration: {hits}"
            # marker set once
            marker = proj.g.query(
                "MATCH (m:Meta {key:'point_fts_v2'}) RETURN m.v").result_set
            assert marker and marker[0][0] is True, marker
            # re-run → no churn: marker still set, values untouched, index
            # still answers (the migration guard short-circuits)
            proj._ensure_indexes()
            rows = proj.g.query(
                "MATCH (n:Point {id:'legacy-pb'}) RETURN n.search_keys"
            ).result_set
            assert rows[0][0] == "fastest 5k running pb", \
                "flat value mangled by re-run"
            hits = run_fts_query(proj.g, "fastest 5k", limit=10)
            assert "legacy-pb" in {h[0] for h in hits}
        finally:
            proj.close()

    def test_fts_leg_trace_healthy(self, proj):
        """R2 D5 (inherited from R3 D4): a healthy run records the FTS leg
        with ran=True, degraded=False, reason='ok', count == hits."""
        self._seed(proj, [("lt1", "gym schedule", None)])
        trace: list[dict] = []
        hits = run_fts_query(proj.g, "gym schedule", limit=5, leg_trace=trace)
        fts = next(e for e in trace if e["leg"] == "fts")
        assert fts["ran"] is True
        assert fts["degraded"] is False
        assert fts["reason"] == "ok"
        assert fts["count"] == len(hits) == 1
        # default-None callers: no trace, byte-identical results
        assert run_fts_query(proj.g, "gym schedule", limit=5) == hits

    def test_fts_leg_trace_index_missing_live(self, proj):
        """R2 D5: with the Point FTS index dropped the FTS leg is recorded
        truthfully, NEVER silent — either the promoted index_missing reason
        (engines whose driver raises) or an explicit empty run (older
        engines return an empty result set instead of raising). The R3
        patched unit test pins the exact index_missing promotion; this live
        variant pins the never-silent contract on the real backend."""
        proj.g.query("CALL db.idx.fulltext.drop('Point')")
        trace: list[dict] = []
        hits = run_fts_query(proj.g, "gym", leg_trace=trace)
        assert hits == []
        fts = next(e for e in trace if e["leg"] == "fts")
        assert fts["reason"] in ("index_missing", "empty_results"), fts
        # shape rule: degraded ⇔ the leg could not serve (index missing); a
        # clean zero-hit run is degraded=False/empty_results (leg RAN).
        assert fts["degraded"] == (fts["reason"] == "index_missing")
        assert fts["ran"] is True
        # restore the shared live DB for later tests
        proj._ensure_indexes()

    def test_fts_leg_trace_breaker_open(self, proj):
        """R2 D5: the FTS breaker forced OPEN records reason='breaker_open'."""
        from tortoise import search_engine as se
        b = se._breaker("fts")
        for _ in range(b.fail_threshold):
            b.record_failure()
        assert b.is_open()
        try:
            trace: list[dict] = []
            hits = run_fts_query(proj.g, "gym", leg_trace=trace)
            assert hits == []
            fts = next(e for e in trace if e["leg"] == "fts")
            assert fts["reason"] == "breaker_open"
            assert fts["degraded"] is True
            assert fts["ran"] is False
        finally:
            reset_circuit_breakers()


# ───────────────────────── #1791 special-char FTS escape (live) ─────────────

# The R2 class above probes the UNAUTHENTICATED :16379 service; this repo's
# compose lane (eldato/operations/memory/docker-compose.yml) maps only the
# authed 6379 (FALKORDB_PASSWORD). Probe the docker-lane URI so the #1791
# regression RUNS on the standard local lane (and in CI, which provisions
# both services).
_FTS_LANE_URI = os.environ.get("TORTOISE_DB_URI") or \
    "docker://:falkordb@localhost:6379/tortoise_test_sdk125"
_FTS_ESCAPE_LIVE = False
try:
    from tortoise.projection import FalkorProjection as _FP_escape
    _old_uri = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = _FTS_LANE_URI
    _probe = _FP_escape.from_uri(_FTS_LANE_URI)
    _probe.close()
    _FTS_ESCAPE_LIVE = True
except Exception:
    _FTS_ESCAPE_LIVE = False
finally:
    if _old_uri is not None:
        os.environ["TORTOISE_DB_URI"] = _old_uri
    else:
        os.environ.pop("TORTOISE_DB_URI", None)


@pytest.mark.skipif(not _FTS_ESCAPE_LIVE, reason="Live FalkorDB (Docker) not available")
class TestFtsSpecialCharEscape:
    """#1791 live regression: degenerate query terms carrying RediSearch-
    special chars (times, quotes, parens, field modifiers, fuzzy ops from
    conversation content) previously flowed RAW into queryNodes →
    "RediSearch: Syntax error at offset N" → FTS leg failed → breaker
    degraded FTS for ALL later queries. Post-fix the escaped form must parse
    clean: the leg runs (empty or matched), the breaker stays closed, and a
    subsequent NORMAL query still gets the FTS leg."""

    @pytest.fixture()
    def proj(self):
        from tortoise.projection import FalkorProjection
        reset_circuit_breakers()
        p = FalkorProjection.from_uri(
            _FTS_LANE_URI,
            graph_name=f"test_fts_escape_{os.urandom(4).hex()}")
        p.g.query("MATCH (n) DETACH DELETE n")
        yield p
        reset_circuit_breakers()
        p.close()

    def _seed(self, proj, rows):
        for pid, content, sk in rows:
            proj.g.query(
                "CREATE (n:Point {id:$id, content:$c, search_keys:$sk, "
                "is_operator:false, pointKind:'statement'})",
                params={"id": pid, "c": content, "sk": sk or None},
            )

    def test_special_char_terms_no_syntax_error_no_breaker_trip(self, proj):
        """The revalidation-log signature is ``10:00`` (all-digit tokens →
        0 surviving → RAW passthrough → ':' field-separator → "Syntax error
        at offset 2"). Each degenerate term must parse clean post-escape:
        the FTS leg records ran=True, never query_failed, and the per-strategy
        breaker stays closed — then a NORMAL query still runs the FTS leg
        (the #1791 recall regression: breaker skip = degraded recall)."""
        from tortoise import search_engine as se
        self._seed(proj, [
            ("p1", "meeting at 10:00 (urgent) @home 100% ready", None),
            ("p2", "gym schedule 5pm", None),
        ])
        reset_circuit_breakers()
        # pre-fix, the 3rd of these (breaker fail_threshold=3) trips FTS.
        # The 21-char escape set is byte-pinned at the unit layer; this loop
        # live-verifies the "every escaped form parses clean" claim (issue
        # #1791's evidence class — documented semantics diverged per-char:
        # 10:00→offset 2, @speed→offset 0) for ALL of them: each term
        # resolves to the RAW fallback (≤1 surviving token, asserted below)
        # and must parse against real RediSearch.
        import time

        from tortoise.sparse import tokenize_sparse_query
        for term in ("10:00", '"(maybe)"', "@home", "100%", "A|B",
                     "{urgent}", "a~", "(maybe", 'say"', ":",
                     "-x", "[x]", "x;y", "a,b", "a<b", "a>b", "a=b",
                     "$x", "a\\b", "foo*",
                     # excluded-char boundary probes: the unit layer pins
                     # . ! # ' ^ & + as NON-escaped, so live-verify the
                     # excluded boundary parses on a real RediSearch dialect
                     # too — w's (DIALECT-2 w'...' wildcard edge), &, !.
                     "w's", "x&y", "bang!x"):
            # self-verifying: the loop only exercises the escape if the term
            # actually lands on the raw fallback (≤1 surviving token).
            assert len(tokenize_sparse_query(term)) <= 1, term
            for attempt in range(3):
                trace: list[dict] = []
                hits = run_fts_query(proj.g, term, leg_trace=trace)
                fts = next(e for e in trace if e["leg"] == "fts")
                if fts["reason"] in ("ok", "empty_results"):
                    break
                # #1568: transient load can starve the FIRST call — retry
                # (idempotent read), resetting the breaker so a transient
                # failure cannot bleed into the next term's assertion.
                if attempt < 2:
                    reset_circuit_breakers()
                    time.sleep(0.5 * (attempt + 1))
            assert fts["ran"] is True, f"{term!r}: {fts}"
            assert fts["reason"] in ("ok", "empty_results"), \
                f"{term!r}: {fts}"
            assert not se._breaker("fts").is_open(), \
                f"{term!r} tripped the FTS breaker: {fts}"
            # the trace is always internally consistent (count == len(hits));
            # the hit COUNT itself is incidental — escaped literals may or
            # may not match indexed tokens (tokenizer-dependent), so zero
            # hits is NOT contractual. The discriminating guards are the
            # trace assertions above + the normal queries after the batch.
            assert fts["count"] == len(hits), f"{term!r}: {fts} {hits}"
        # the actual #1791 recall regression: after the degenerate terms, a
        # NORMAL query (single-token AND OR-union) still gets the FTS leg
        # (breaker never opened) and hits.
        trace: list[dict] = []
        hits = run_fts_query(proj.g, "meeting", leg_trace=trace)
        assert {h[0] for h in hits} == {"p1"}, hits
        fts = next(e for e in trace if e["leg"] == "fts")
        assert fts["reason"] == "ok", fts
        trace = []
        hits = run_fts_query(proj.g, "gym schedule", leg_trace=trace)
        assert "p2" in {h[0] for h in hits}, hits
        fts = next(e for e in trace if e["leg"] == "fts")
        assert fts["reason"] == "ok", fts


class TestFusionWeightsProductionDefault:
    """#1657 (owner decision 2026-08-25): the PRODUCTION fusion default is
    vector=1.5 (the measured dilution fix, ON by default); env override
    tunes it. rrf_fusion's own default stays None (= all 1.0) so the
    function-level contract is byte-identical; the SDK call site supplies
    the production default."""

    @staticmethod
    def _spy_and_two_legs(monkeypatch, captured):
        from tortoise import search_engine as se_mod
        from tortoise.sdk import TortoiseSDK

        def _spy(ranked_lists, **kwargs):
            captured["weights"] = kwargs.get("weights")
            captured["names"] = kwargs.get("strategy_names")
            fused = {}
            for rl in ranked_lists:
                for pid, score in rl:
                    fused[pid] = fused.get(pid, 0.0) + score
            return fused

        def _two_legs(*a, **k):
            return {"fts": [("doc1", 1.0)], "vector": [("doc2", 1.0)]}

        monkeypatch.setattr(se_mod, "rrf_fusion", _spy)
        monkeypatch.setattr(se_mod, "degradation_chain", _two_legs)
        return TortoiseSDK

    def test_sdk_passes_vector_15_default(self, monkeypatch):
        """No env → the fusion weights default to {'vector': 1.5}."""
        captured = {}
        TortoiseSDK = self._spy_and_two_legs(monkeypatch, captured)
        monkeypatch.delenv("TORTOISE_FUSION_WEIGHTS", raising=False)
        sdk = TortoiseSDK()
        sdk._proj = _FakeProj2()
        sdk.tortoise_fts_query("test query", limit=10)
        assert captured.get("weights") == {"vector": 1.5}, \
            f"production default must weight vector 1.5, got {captured.get('weights')}"
        assert captured.get("names") == ["fts", "vector"]

    def test_env_override_replaces_default(self, monkeypatch):
        """TORTOISE_FUSION_WEIGHTS overrides the production default."""
        captured = {}
        TortoiseSDK = self._spy_and_two_legs(monkeypatch, captured)
        monkeypatch.setenv("TORTOISE_FUSION_WEIGHTS", '{"vector": 2.0}')
        sdk = TortoiseSDK()
        sdk._proj = _FakeProj2()
        sdk.tortoise_fts_query("test query", limit=10)
        assert captured.get("weights") == {"vector": 2.0}, captured.get("weights")


class _FakeProj2:
    """Minimal projection stub for the fusion-default tests."""

    def __init__(self):
        class _G:
            def query(self, *a, **k):
                class _R:
                    def __init__(self):
                        self.result_set = []
                return _R()
        self.g = _G()
