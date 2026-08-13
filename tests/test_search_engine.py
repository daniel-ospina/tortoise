"""Tests for tortoise.search_engine — RRF fusion, classifier, degradation."""
from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.search_engine import (
    classify_query, rrf_fusion, SearchResult, SearchScores,
    EpBreakdown, EpEvidence, annotate_ep_batch,
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


# ------------------------------------------------------------------ #125 SDK document metadata


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")
def test_sdk_document_search_returns_metadata():
    """#125: tortoise_search(entity_type='document') returns capture metadata.
    #167: includes sourcePath in results."""
    from tortoise.projection import FalkorProjection
    uri = os.environ.get("TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise_test_sdk125")
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
        sdk = TortoiseSDK()
        sdk._proj = proj
        results = sdk.tortoise_fts_query("licensing", entity_type="document")
        doc = next((r for r in results if r.get("id") == "test-sdk-doc"), None)
        assert doc, f"doc not in results: {results}"
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
