"""Tests for embeddings.py + search_engine.py filter gaps.

Covers:
  - compute_embedding failure paths (#7865)
  - search_points TF-IDF / threshold / snippet (#7865)
  - filter_by_relationship failure paths (#7862)
  - filter_by_traversal_predicate failure paths (#7862)

Runs with: python3 -m pytest tests/test_embeddings_filters.py -v
"""
from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════════
# 1. compute_embedding failure paths (embeddings.py:48-65)
# ═══════════════════════════════════════════════════════════════════════

class TestComputeEmbeddingFailures:
    """Covers lines 48-65 of embeddings.py — compute_embedding edge cases."""

    def test_model_get_returns_none(self):
        """EmbeddingModel.get() returns None → compute_embedding returns None (lines 54-56)."""
        from tortoise.embeddings import compute_embedding, EmbeddingModel

        with patch.object(EmbeddingModel, "get", return_value=None):
            result = compute_embedding("hello world")
            assert result is None

    def test_content_truncation_to_max_tokens(self):
        """Content longer than max_tokens is truncated before encoding (lines 60-61)."""
        from tortoise.embeddings import compute_embedding, EmbeddingModel

        fake_model = MagicMock()
        fake_model.encode.return_value = np.array([[0.1] * 384], dtype=np.float64)

        with patch.object(EmbeddingModel, "get", return_value=fake_model):
            # 100 words, max_tokens=5 → only first 5 words encoded
            long_content = "word " * 100
            compute_embedding(long_content, max_tokens=5)

        # Verify encode was called with exactly 5 words
        call_args = fake_model.encode.call_args[0][0]
        assert isinstance(call_args, list)
        assert len(call_args) == 1
        encoded_text = call_args[0]
        assert encoded_text.count(" ") == 4  # 5 words → 4 spaces
        assert "word word word word word" in encoded_text

    def test_encode_returns_none(self):
        """model.encode returns None → compute_embedding returns None (lines 64-65)."""
        from tortoise.embeddings import compute_embedding, EmbeddingModel

        fake_model = MagicMock()
        fake_model.encode.return_value = None

        with patch.object(EmbeddingModel, "get", return_value=fake_model):
            result = compute_embedding("hello world")
            assert result is None

    def test_encode_returns_empty_array(self):
        """model.encode returns empty array → compute_embedding returns None (line 65)."""
        from tortoise.embeddings import compute_embedding, EmbeddingModel

        fake_model = MagicMock()
        fake_model.encode.return_value = np.array([], dtype=np.float64)

        with patch.object(EmbeddingModel, "get", return_value=fake_model):
            result = compute_embedding("hello world")
            assert result is None

    def test_encode_exception_returns_none(self):
        """model.encode raises → compute_embedding returns None (lines 67-68)."""
        from tortoise.embeddings import compute_embedding, EmbeddingModel

        fake_model = MagicMock()
        fake_model.encode.side_effect = RuntimeError("CUDA out of memory")

        with patch.object(EmbeddingModel, "get", return_value=fake_model):
            result = compute_embedding("hello world")
            assert result is None


# ═══════════════════════════════════════════════════════════════════════
# 2. search_points (embeddings.py:117-165)
# ═══════════════════════════════════════════════════════════════════════

class TestSearchPoints:
    """Covers lines 117-165 of embeddings.py.

    No mocking needed — sentence_transformers is not installed in this env,
    so the TF-IDF fallback path activates naturally.
    """

    def test_empty_points_returns_empty(self):
        """Empty points list → [] (lines 128-129)."""
        from tortoise.embeddings import search_points
        result = search_points("query", [])
        assert result == []

    def test_tfidf_fallback_ranking(self):
        """TF-IDF fallback ranks results by cosine similarity (lines 136-140).

        p1 and p3 are about 'quantum physics', p2 is about 'cookies'.
        When querying 'quantum physics', p1 and p3 should rank above p2.
        """
        from tortoise.embeddings import search_points

        points = [
            {"id": "p1", "content": "quantum physics research papers and experiments"},
            {"id": "p2", "content": "chocolate chip cookie baking recipes"},
            {"id": "p3", "content": "quantum mechanics and physics theory"},
        ]

        result = search_points("quantum physics", points, threshold=0.0, limit=5)

        assert len(result) >= 2
        ids = [r["id"] for r in result]
        # p1 or p3 should be top (most similar to "quantum physics")
        assert ids[0] in ("p1", "p3")
        # p2 should be last (least relevant)
        assert ids[-1] == "p2"
        assert all("id" in r and "content" in r and "similarity" in r
                   and "snippet" in r for r in result)

    def test_threshold_filtering(self):
        """Points below similarity threshold are excluded (lines 148-149)."""
        from tortoise.embeddings import search_points

        points = [
            {"id": "p1", "content": "the cat sat on the mat"},
            {"id": "p2", "content": "completely unrelated topic here"},
        ]

        # Low threshold — both should be included
        result_low = search_points("cat mat", points, threshold=0.0, limit=5)
        # High threshold — only highly similar items survive
        result_high = search_points("cat mat", points, threshold=0.3, limit=5)

        assert len(result_low) >= len(result_high)
        # p1 (cat/mat) should always survive even at moderate threshold
        high_ids = [r["id"] for r in result_high]
        assert "p1" in high_ids, \
            f"p1 should survive threshold 0.3, got {high_ids}"

    def test_snippet_truncation_to_200_chars(self):
        """Snippet is truncated to 200 characters (line 157)."""
        from tortoise.embeddings import search_points

        long_content = "x" * 300
        points = [{"id": "p1", "content": long_content}]

        result = search_points("x", points, threshold=0.0, limit=5)

        assert len(result) == 1
        assert len(result[0]["snippet"]) == 200
        assert result[0]["snippet"] == long_content[:200]

    def test_snippet_no_truncation_short_content(self):
        """Short content (< 200 chars) snippet equals full content (line 157 else)."""
        from tortoise.embeddings import search_points

        short_content = "hello world"
        points = [{"id": "p1", "content": short_content}]

        result = search_points("hello", points, threshold=0.0, limit=5)

        assert len(result) == 1
        assert result[0]["snippet"] == short_content

    def test_limit_truncation(self):
        """Results respect the limit parameter (line 161)."""
        from tortoise.embeddings import search_points

        points = [
            {"id": f"p{i}", "content": f"document number {i} about testing"}
            for i in range(20)
        ]

        result = search_points("testing", points, threshold=0.0, limit=3)

        assert len(result) == 3

    def test_sort_descending_by_similarity(self):
        """Results are sorted by similarity descending (line 160)."""
        from tortoise.embeddings import search_points

        points = [
            {"id": "p1", "content": "the quick brown fox jumps over the lazy dog"},
            {"id": "p2", "content": "fox dog"},
            {"id": "p3", "content": "unrelated words here"},
        ]

        result = search_points("fox dog", points, threshold=0.0, limit=5)

        sims = [r["similarity"] for r in result]
        assert sims == sorted(sims, reverse=True), \
            f"results should be descending by similarity, got {sims}"


# ═══════════════════════════════════════════════════════════════════════
# 3. filter_by_relationship failure paths (search_engine.py:518, 532-534)
# ═══════════════════════════════════════════════════════════════════════

class TestFilterByRelationshipFailures:
    """Covers lines 517-518 and 532-534 of search_engine.py."""

    @pytest.fixture
    def mock_graph(self):
        """FalkorDB graph mock with query support."""
        g = MagicMock()
        g.query.return_value.result_set = []
        return g

    def test_empty_point_ids(self, mock_graph):
        """Empty point_ids → [] (line 517-518 guard)."""
        from tortoise.search_engine import filter_by_relationship
        result = filter_by_relationship(mock_graph, [], "addresses", "target-1")
        assert result == []

    def test_empty_predicate(self, mock_graph):
        """Empty predicate → [] (line 517-518 guard)."""
        from tortoise.search_engine import filter_by_relationship
        result = filter_by_relationship(mock_graph, ["p1"], "", "target-1")
        assert result == []

    def test_empty_target_id(self, mock_graph):
        """Empty target_id → [] (line 517-518 guard)."""
        from tortoise.search_engine import filter_by_relationship
        result = filter_by_relationship(mock_graph, ["p1"], "addresses", "")
        assert result == []

    def test_unknown_predicate_returns_empty(self, mock_graph):
        """Valid predicate that matches no operators → [].

        The Cypher query runs but finds zero matching operators, so result_set is empty.
        This simulates a predicate like 'unknown-relation' that exists in no operator label.
        """
        from tortoise.search_engine import filter_by_relationship
        mock_graph.query.return_value.result_set = []
        result = filter_by_relationship(mock_graph, ["p1"], "unknown-relation", "target-1")
        assert result == []

    def test_graph_exception_returns_empty(self, mock_graph):
        """graph.query raises → returns [] (lines 532-534)."""
        from tortoise.search_engine import filter_by_relationship
        mock_graph.query.side_effect = ConnectionError("FalkorDB connection refused")
        result = filter_by_relationship(mock_graph, ["p1"], "addresses", "target-1")
        assert result == []

    def test_success_path_returns_matching_ids(self, mock_graph):
        """Happy path: valid query returns filtered IDs."""
        from tortoise.search_engine import filter_by_relationship
        mock_graph.query.return_value.result_set = [["p1"], ["p3"]]
        result = filter_by_relationship(mock_graph, ["p1", "p2", "p3"],
                                        "addresses", "target-1")
        assert result == ["p1", "p3"]


# ═══════════════════════════════════════════════════════════════════════
# 4. filter_by_traversal_predicate (search_engine.py:550, 563-565)
# ═══════════════════════════════════════════════════════════════════════

class TestFilterByTraversalPredicate:
    """Covers lines 549-550 and 563-565 of search_engine.py."""

    @pytest.fixture
    def mock_graph(self):
        """FalkorDB graph mock with query support."""
        g = MagicMock()
        g.query.return_value.result_set = []
        return g

    def test_empty_point_ids(self, mock_graph):
        """Empty point_ids → [] (line 549-550 guard)."""
        from tortoise.search_engine import filter_by_traversal_predicate
        result = filter_by_traversal_predicate(mock_graph, [], "contains")
        assert result == []

    def test_empty_predicate(self, mock_graph):
        """Empty predicate → [] (line 549-550 guard)."""
        from tortoise.search_engine import filter_by_traversal_predicate
        result = filter_by_traversal_predicate(mock_graph, ["p1"], "")
        assert result == []

    def test_unknown_predicate_returns_empty(self, mock_graph):
        """Unknown predicate label → graph returns empty result_set → [].

        The traversal path resolves to a predicate that exists in no operator label.
        """
        from tortoise.search_engine import filter_by_traversal_predicate
        mock_graph.query.return_value.result_set = []
        result = filter_by_traversal_predicate(mock_graph, ["p1"], "unknown-traversal")
        assert result == []

    def test_graph_exception_returns_empty(self, mock_graph):
        """graph.query raises → returns [] (lines 563-565)."""
        from tortoise.search_engine import filter_by_traversal_predicate
        mock_graph.query.side_effect = RuntimeError("graph database failure")
        result = filter_by_traversal_predicate(mock_graph, ["p1"], "contains")
        assert result == []

    def test_success_path_returns_matching_ids(self, mock_graph):
        """Happy path: valid predicate returns IDs that participate in operators."""
        from tortoise.search_engine import filter_by_traversal_predicate
        mock_graph.query.return_value.result_set = [["p2"], ["p4"]]
        result = filter_by_traversal_predicate(mock_graph, ["p1", "p2", "p3", "p4"],
                                               "contains")
        assert result == ["p2", "p4"]
