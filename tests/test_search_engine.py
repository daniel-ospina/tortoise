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


class TestClassifyQuery:
    def test_full_scan(self):
        result = classify_query(None, None, "ctx")
        assert result == {"fts": False, "vector": False, "structural": True}

    def test_text_query(self):
        result = classify_query("hello", None, None)
        assert result == {"fts": True, "vector": True, "structural": True}

    def test_kind_only(self):
        result = classify_query(None, "statement", None)
        assert result == {"fts": False, "vector": False, "structural": True}

    def test_text_with_kind_and_context(self):
        result = classify_query("test", "statement", "physics")
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
            id="1", content="test", point_kind="statement", context="ctx",
            scores=SearchScores(fts=0.8, rrf=0.9),
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


def test_sdk_document_search_returns_metadata():
    """#125: tortoise_search(entity_type='document') returns capture metadata."""
    from tortoise.projection import FalkorProjection
    uri = os.environ.get("TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise_test_sdk125")
    proj = FalkorProjection.from_uri(uri)
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj._ensure_indexes()
    proj.g.query(
        "CREATE (d:Document {id:'test-sdk-doc', title:'Conv', "
        "documentKind:'transcript', topics:['licensing'], "
        "summary:'Test', sessionId:'s1', eventId:'e1', "
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
    finally:
        proj.close()
