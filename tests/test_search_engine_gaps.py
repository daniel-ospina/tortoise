"""Unit tests for search_engine failure paths and error handling.

Covers:
- fallback_tfidf: empty/malformed points, graceful degradation
- degradation_chain: parallel strategy execution, partial failure, timeout
- run_vector_query: HNSW fallback, embedded vs Docker, index not found
- run_fts_query: graph errors, index-not-found
- run_structural_query: scoring, error paths
"""
from __future__ import annotations

import os
import sys
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.search_engine import (
    fallback_tfidf,
    degradation_chain,
    get_relationships,
    run_fts_query,
    run_vector_query,
    run_structural_query,
)


# ── FalkorDB availability check (#174) ─────────────────────────────────────
# The #125 document-FTS tests below need a LIVE FalkorDB server: FTS indexes
# are version-gated (>= 4.x) and are not supported by the embedded FalkorDBLite
# backend, so graceful skip is the right semantic instead of an embedded swap.
# Probe the env URI and common local defaults once at import time; when no
# server is reachable the tests skip so the documented no-Docker command
# `python3 -m pytest tests/` passes (AGENTS.md: FalkorDBLite embedded, no
# Docker needed). Mirrors tests/test_integration_search.py's probe pattern,
# but records the working URI in _WORKING_URI instead of mutating
# os.environ, so other test files in the same session are unaffected.
FALKORDB_AVAILABLE = False
_WORKING_URI: str | None = None
_uri_candidates = [
    os.environ.get("TORTOISE_DB_URI"),
    "docker://:falkordb@localhost:6379/tortoise_test_fts125",
    "docker://:@localhost:16379/tortoise_test_fts125",
]
for _uri in _uri_candidates:
    if not _uri:
        continue
    _proj = None
    try:
        from tortoise.projection import FalkorProjection
        _proj = FalkorProjection.from_uri(_uri)
        _proj.g.query("RETURN 1")
        _WORKING_URI = _uri
        FALKORDB_AVAILABLE = True
        break
    except Exception:
        continue
    finally:
        if _proj is not None:
            try:
                _proj.close()
            except Exception:
                pass


# ── Mock helpers ────────────────────────────────────────────────────────────

class MockResultSet:
    """Simulates FalkorDB query result with a .result_set attribute."""

    def __init__(self, result_set: list):
        self.result_set = result_set


class SimpleMockGraph:
    """Graph that returns a fixed result_set, or raises on query()."""

    def __init__(self, result_set=None, raise_on_query=None):
        self._result_set = result_set or []
        self._raise_on_query = raise_on_query
        self.query_calls: list[tuple[str, dict]] = []

    def query(self, cypher, params=None):
        self.query_calls.append((cypher, params or {}))
        if self._raise_on_query:
            raise self._raise_on_query
        return MockResultSet(self._result_set)


class MultiCallGraph:
    """Graph returning different results per call — for testing fallback chains.

    call_responses: list of (result_set, exception_or_None) tuples.
    After exhausting planned responses, returns empty.
    """

    def __init__(self, call_responses: list):
        self._responses = call_responses
        self._index = 0
        self.query_calls: list[tuple[str, dict]] = []

    def query(self, cypher, params=None):
        self.query_calls.append((cypher, params or {}))
        if self._index >= len(self._responses):
            return MockResultSet([])
        result_set, exc = self._responses[self._index]
        self._index += 1
        if exc:
            raise exc
        return MockResultSet(result_set or [])


class StrategyControlledGraph:
    """Graph that returns different results based on the Cypher query content.

    cypher_map: dict of substring → (result_set, exception_or_None).
    First matching substring wins.
    """

    def __init__(self, cypher_map: dict[str, tuple] = None):
        self._map = cypher_map or {}
        self.query_calls: list[tuple[str, dict]] = []

    def query(self, cypher, params=None):
        self.query_calls.append((cypher, params or {}))
        for pattern, (result_set, exc) in self._map.items():
            if pattern in cypher:
                if exc:
                    raise exc
                return MockResultSet(result_set or [])
        # Default: empty
        return MockResultSet([])


# ── fallback_tfidf ──────────────────────────────────────────────────────────

class TestFallbackTfidf:
    """fallback_tfidf — last-resort in-memory TF-IDF fallback."""

    def test_empty_points_returns_empty(self):
        """Empty points list → [] (search_points also returns [])."""
        result = fallback_tfidf("query", [])
        assert result == []

    def test_points_missing_id_key(self):
        """Points without 'id' → graceful [].

        Points missing 'id' are filtered from meta dict (p.get("id") is
        falsy), but still in the list passed to search_points(), which
        does p["id"] and raises KeyError. fallback_tfidf catches → [].
        """
        points = [
            {"content": "point with no id", "pointKind": "statement", "context": "ctx"},
        ]
        result = fallback_tfidf("query", points)
        assert result == []

    def test_normal_search_returns_search_result_dicts(self):
        """Mock search_points → valid SearchResult.to_dict() output."""
        mock_results = [
            {"id": "p1", "content": "alpha", "similarity": 0.9, "snippet": "alpha"},
            {"id": "p2", "content": "beta", "similarity": 0.7, "snippet": "beta"},
        ]
        points = [
            {"id": "p1", "content": "alpha", "pointKind": "statement", "context": "ctx"},
            {"id": "p2", "content": "beta", "pointKind": "question", "context": None},
        ]
        # search_points is imported locally inside fallback_tfidf from tortoise.embeddings
        with mock.patch("tortoise.embeddings.search_points", return_value=mock_results):
            result = fallback_tfidf("test query", points, limit=2)

        assert len(result) == 2
        assert result[0]["id"] == "p1"
        assert result[0]["content"] == "alpha"
        assert result[0]["match_source"] == "tfidf"
        assert result[0]["scores"]["rrf"] == 0.9
        assert result[0]["scores"]["fts"] is None
        assert result[1]["id"] == "p2"
        assert result[1]["context"] is None
        assert result[1]["point_kind"] == "question"

    def test_search_points_raises_returns_empty(self):
        """search_points throws → fallback_tfidf catches and returns []."""
        points = [{"id": "p1", "content": "test", "pointKind": "statement"}]
        with mock.patch(
            "tortoise.embeddings.search_points",
            side_effect=RuntimeError("TF-IDF engine failure"),
        ):
            result = fallback_tfidf("query", points)
        assert result == []

    def test_point_kind_missing_in_meta_falls_back_to_empty_string(self):
        """When meta dict entry has no pointKind key → ''."""
        mock_results = [
            {"id": "p1", "content": "test", "similarity": 0.5, "snippet": "test"},
        ]
        points = [{"id": "p1", "content": "test"}]  # no pointKind
        with mock.patch("tortoise.embeddings.search_points", return_value=mock_results):
            result = fallback_tfidf("query", points)
        assert len(result) == 1
        assert result[0]["point_kind"] == ""

    def test_limit_is_passed_through(self):
        """limit param reaches search_points call."""
        points = [{"id": f"p{i}", "content": f"c{i}", "pointKind": "stmt"} for i in range(10)]
        mock_results = [{"id": "p0", "content": "c0", "similarity": 0.5, "snippet": "c0"}]
        with mock.patch("tortoise.embeddings.search_points", return_value=mock_results) as sp:
            fallback_tfidf("query", points, limit=3)
            sp.assert_called_once_with("query", points, threshold=0.0, limit=3)


# ── degradation_chain ───────────────────────────────────────────────────────

class TestDegradationChain:
    """degradation_chain — parallel strategies, graceful partial failure.

    Uses StrategyControlledGraph to control what each strategy sees
    from graph.query(), avoiding nondeterministic thread-completion
    ordering issues.
    """

    STRATEGIES_ALL = {"fts": True, "vector": True, "structural": True}
    QUERY_VEC = [0.1] * 384

    def test_one_strategy_succeeds_others_return_empty(self):
        """FTS returns results, vector + structural return [] → partial dict."""
        # brute-force vector uses vec.euclideanDistance in Cypher
        graph = StrategyControlledGraph({
            "fulltext": (          [("f1", 0.9)], None),
            "euclideanDistance": ( [], None),
            "pointKind": (         [], None),
        })

        result = degradation_chain(
            graph, query="test", kind=None, context=None,
            query_vec=self.QUERY_VEC, strategies=self.STRATEGIES_ALL,
            entity_type="point", limit=20, is_embedded=True,
        )

        # Only FTS produced non-empty results
        assert "fts" in result
        assert result["fts"] == [("f1", 0.9)]
        assert "vector" not in result
        assert "structural" not in result

    def test_all_strategies_fail_returns_empty_dict(self):
        """Every strategy's graph.query raises → {}."""
        graph = StrategyControlledGraph({
            "fulltext": (          None, Exception("FTS index unavailable")),
            "euclideanDistance": ( None, Exception("Vector index not found")),
            "pointKind": (         None, Exception("Structural down")),
        })

        result = degradation_chain(
            graph, query="test", kind=None, context=None,
            query_vec=self.QUERY_VEC, strategies=self.STRATEGIES_ALL,
        )

        assert result == {}

    def test_mixed_success_fts_and_vector(self):
        """FTS + vector return results, structural returns [] → 2 entries."""
        # brute-force vector query uses vec.euclideanDistance in the Cypher
        graph = StrategyControlledGraph({
            "fulltext": (        [("f1", 0.9)], None),
            "euclideanDistance": ([("v1", 0.8)], None),
            "pointKind": (       [], None),
        })

        result = degradation_chain(
            graph, query="test", kind=None, context=None,
            query_vec=self.QUERY_VEC, strategies=self.STRATEGIES_ALL,
        )

        assert "fts" in result
        assert "vector" in result
        assert "structural" not in result
        assert len(result) == 2

    def test_strategy_disabled_not_called(self):
        """Disabled strategies are skipped entirely."""
        graph = StrategyControlledGraph({
            "pointKind": ([("s1", )], None),
        })
        only_struct = {"fts": False, "vector": False, "structural": True}

        result = degradation_chain(
            graph, query="test", kind="stmt", context=None,
            query_vec=None, strategies=only_struct,
        )

        # kind="stmt" with context=None → score=0.5 (kind only)
        assert result == {"structural": [("s1", 0.5)]}
        # Verify only structural was queried
        assert len(graph.query_calls) == 1
        assert "pointKind" in graph.query_calls[0][0]

    def test_no_query_skips_fts(self):
        """query=None → FTS not submitted even if enabled."""
        # brute-force vector uses vec.euclideanDistance in Cypher
        graph = StrategyControlledGraph({
            "euclideanDistance": ([("v1", 0.5)], None),
            "pointKind": (       [], None),
        })
        strategies = {"fts": True, "vector": True, "structural": True}

        result = degradation_chain(
            graph, query=None, kind=None, context=None,
            query_vec=self.QUERY_VEC, strategies=strategies,
        )

        assert "vector" in result
        assert "fts" not in result
        # No fulltext call issued
        for cypher, _ in graph.query_calls:
            assert "fulltext" not in cypher

    def test_no_vector_query_vec_skips_vector(self):
        """query_vec is None → vector not submitted even if enabled."""
        graph = StrategyControlledGraph({
            "fulltext": (  [("f1", 0.9)], None),
            "pointKind": ( [], None),
        })
        strategies = {"fts": True, "vector": True, "structural": True}

        result = degradation_chain(
            graph, query="test", kind=None, context=None,
            query_vec=None, strategies=strategies,
        )

        assert "fts" in result
        assert "vector" not in result


# ── run_vector_query ────────────────────────────────────────────────────────

class TestRunVectorQuery:
    """run_vector_query — HNSW index-accelerated vs brute-force paths."""

    QUERY_VEC = [0.1] * 384

    # ── Empty input ──────────────────────────────────────────────────

    def test_empty_query_vec_returns_empty(self):
        """Empty query_vec → [] — early return before any DB call."""
        graph = SimpleMockGraph()
        result = run_vector_query(graph, [])
        assert result == []
        assert graph.query_calls == []

    # ── Embedded mode (brute-force) ──────────────────────────────────

    def test_embedded_mode_uses_brute_force(self):
        """is_embedded=True → skips HNSW, goes straight to brute-force.

        Brute-force returns rows as (id, score) — 2-element tuples.
        """
        graph = SimpleMockGraph(result_set=[("a", 0.9), ("b", 0.7)])

        result = run_vector_query(graph, self.QUERY_VEC, limit=10, is_embedded=True)

        assert len(result) == 2
        assert result[0] == ("a", 0.9)
        assert result[1] == ("b", 0.7)
        cypher = graph.query_calls[0][0]
        assert "vec.euclideanDistance" in cypher
        assert "CALL db.idx.vector.queryNodes" not in cypher

    def test_embedded_mode_empty_db_returns_empty(self):
        """Brute-force with no matching embeddings → []."""
        graph = SimpleMockGraph(result_set=[])

        result = run_vector_query(graph, self.QUERY_VEC, limit=10, is_embedded=True)

        assert result == []

    # ── Docker mode (HNSW) ───────────────────────────────────────────

    def test_docker_mode_uses_hnsw_index(self):
        """is_embedded=False → tries HNSW. HNSW returns just IDs, scores computed."""
        graph = SimpleMockGraph(result_set=[("a",), ("b",)])

        result = run_vector_query(graph, self.QUERY_VEC, limit=10, is_embedded=False)

        assert len(result) == 2
        cypher = graph.query_calls[0][0]
        assert "CALL db.idx.vector.queryNodes" in cypher
        assert "vec.euclideanDistance" not in cypher

    def test_docker_mode_index_not_found_falls_back_to_brute_force(self):
        """HNSW fails 'index not found' → brute-force fallback succeeds."""
        graph = MultiCallGraph([
            (None, Exception("vector index not found")),   # HNSW fails
            ([("a", 0.9), ("b", 0.7)], None),              # brute-force succeeds
        ])

        result = run_vector_query(graph, self.QUERY_VEC, limit=10, is_embedded=False)

        assert len(result) == 2
        assert result[0][0] == "a"
        assert len(graph.query_calls) == 2
        assert "CALL db.idx.vector.queryNodes" in graph.query_calls[0][0]
        assert "vec.euclideanDistance" in graph.query_calls[1][0]

    def test_docker_mode_generic_error_falls_back_to_brute_force(self):
        """HNSW fails with generic error → brute-force fallback."""
        graph = MultiCallGraph([
            (None, RuntimeError("Connection refused")),     # HNSW fails
            ([("c", 0.85)], None),                           # brute-force succeeds
        ])

        result = run_vector_query(graph, self.QUERY_VEC, limit=10, is_embedded=False)

        assert len(result) == 1
        assert result[0] == ("c", 0.85)
        assert len(graph.query_calls) == 2

    def test_both_hnsw_and_brute_force_fail_returns_empty(self):
        """Both HNSW and brute-force fail → []."""
        graph = MultiCallGraph([
            (None, Exception("vector index does not exist")),  # HNSW fails
            (None, Exception("index not found")),               # brute-force also fails
        ])

        result = run_vector_query(graph, self.QUERY_VEC, limit=10, is_embedded=False)

        assert result == []
        assert len(graph.query_calls) == 2

    # ── Entity type routing ──────────────────────────────────────────

    def test_entity_type_event_uses_eventid_field(self):
        """entity_type='event' → Cypher uses Event label + eventId."""
        graph = SimpleMockGraph(result_set=[("evt-1", 0.9)])

        result = run_vector_query(graph, self.QUERY_VEC, entity_type="event", is_embedded=True)

        assert len(result) == 1
        assert result[0][0] == "evt-1"
        cypher = graph.query_calls[0][0]
        assert "n.eventId" in cypher
        assert "MATCH (n:Event)" in cypher

    def test_entity_type_object_uses_object_label(self):
        """entity_type='object' → Object label."""
        graph = SimpleMockGraph(result_set=[("obj-1", 0.9)])

        result = run_vector_query(graph, self.QUERY_VEC, entity_type="object", is_embedded=True)

        assert len(result) == 1
        cypher = graph.query_calls[0][0]
        assert "MATCH (n:Object)" in cypher

    # ── Timeout ──────────────────────────────────────────────────────

    def test_brute_force_timeout_returns_empty(self):
        """Brute-force query exceeds timeout → []."""
        graph = SimpleMockGraph(result_set=[("a", 0.9)])

        with mock.patch("time.monotonic", side_effect=[0.0, 2.0]):
            result = run_vector_query(graph, self.QUERY_VEC, timeout_ms=500, is_embedded=True)

        assert result == []

    def test_hnsw_timeout_returns_empty(self):
        """HNSW query exceeds timeout → [] (no brute-force fallback on timeout)."""
        graph = SimpleMockGraph(result_set=[("a",)])

        with mock.patch("time.monotonic", side_effect=[0.0, 2.0]):
            result = run_vector_query(graph, self.QUERY_VEC, timeout_ms=500, is_embedded=False)

        assert result == []

    # ── Scoring ──────────────────────────────────────────────────────

    def test_vector_scores_are_monotonically_decreasing(self):
        """Docker-mode scores: 1.0 - i/N (monotonically decreasing)."""
        graph = SimpleMockGraph(result_set=[("a",), ("b",), ("c",)])

        result = run_vector_query(graph, self.QUERY_VEC, is_embedded=False, limit=10)

        assert len(result) == 3
        assert result[0][1] == 1.0
        assert result[1][1] == pytest.approx(2.0 / 3.0)
        assert result[2][1] == pytest.approx(1.0 / 3.0)


# ── run_fts_query ───────────────────────────────────────────────────────────

class TestRunFtsQuery:
    """run_fts_query — full-text search via FalkorDB FTS index."""

    def test_graph_raises_returns_empty(self):
        """graph.query raises → [] with no crash."""
        graph = SimpleMockGraph(raise_on_query=RuntimeError("Connection refused"))
        result = run_fts_query(graph, "test query")
        assert result == []

    def test_index_not_found_returns_empty(self):
        """Index missing → graceful []."""
        graph = SimpleMockGraph(raise_on_query=Exception("FTS index not found"))
        result = run_fts_query(graph, "test query")
        assert result == []

    def test_index_does_not_exist_returns_empty(self):
        """Alternate phrasing 'does not exist' → graceful []."""
        graph = SimpleMockGraph(
            raise_on_query=Exception("vector index does not exist for label Point")
        )
        result = run_fts_query(graph, "test query")
        assert result == []

    def test_normal_fts_query_returns_ranked_results(self):
        """Valid FTS results → list of (id, float_score) tuples."""
        graph = SimpleMockGraph(result_set=[("p1", 0.95), ("p2", 0.80), ("p3", 0.60)])

        result = run_fts_query(graph, "test", limit=5)

        assert len(result) == 3
        assert result[0] == ("p1", 0.95)
        assert result[1] == ("p2", 0.80)
        assert result[2] == ("p3", 0.60)

    def test_fts_query_timeout_returns_empty(self):
        """Query exceeds timeout_ms → [] (post-hoc)."""
        graph = SimpleMockGraph(result_set=[("p1", 0.9)])

        with mock.patch("time.monotonic", side_effect=[0.0, 2.0]):
            result = run_fts_query(graph, "test", timeout_ms=500)

        assert result == []

    def test_entity_type_event_uses_eventid(self):
        """entity_type='event' → Event label + eventId field."""
        graph = SimpleMockGraph(result_set=[("evt-1", 0.9)])

        result = run_fts_query(graph, "test", entity_type="event")

        assert len(result) == 1
        assert result[0][0] == "evt-1"
        cypher = graph.query_calls[0][0]
        assert "node.eventId" in cypher
        assert "Event" in cypher

    def test_entity_type_subject_uses_id(self):
        """entity_type='subject' → Subject + id field."""
        graph = SimpleMockGraph(result_set=[("sub-1", 0.8)])

        result = run_fts_query(graph, "test", entity_type="subject")

        cypher = graph.query_calls[0][0]
        assert "node.id" in cypher
        assert "Subject" in cypher

    def test_entity_type_operator_uses_contains(self):
        """entity_type='operator' → Point label + is_operator CONTAINS branch."""
        graph = SimpleMockGraph(result_set=[("op-1", 1.0)])

        result = run_fts_query(graph, "label", entity_type="operator")

        assert len(result) == 1
        assert result[0] == ("op-1", 1.0)
        cypher = graph.query_calls[0][0]
        assert "MATCH (n:Point)" in cypher
        assert "n.is_operator = true" in cypher
        assert "CONTAINS" in cypher

    def test_empty_result_set_returns_empty(self):
        """FTS returns zero rows → []."""
        graph = SimpleMockGraph(result_set=[])

        result = run_fts_query(graph, "test")

        assert result == []


# ── run_structural_query ────────────────────────────────────────────────────

class TestRunStructuralQuery:
    """run_structural_query — kind/context filtering via range indexes."""

    def test_kind_and_context_gives_full_score(self):
        """Both kind and context match → score=1.0."""
        graph = SimpleMockGraph(result_set=[("p1",), ("p2",)])

        result = run_structural_query(graph, kind="statement", context="physics")

        assert len(result) == 2
        assert result[0][1] == 1.0
        assert result[1][1] == 1.0

    def test_kind_only_gives_half_score(self):
        """Only kind filter → score=0.5."""
        graph = SimpleMockGraph(result_set=[("p1",)])

        result = run_structural_query(graph, kind="decision", context=None)

        assert result[0][1] == 0.5

    def test_context_without_kind_gives_half_score(self):
        """context provided, kind=None → score=0.5."""
        graph = SimpleMockGraph(result_set=[("p1",)])

        result = run_structural_query(graph, kind=None, context="physics")

        assert result[0][1] == 0.5

    def test_no_kind_and_no_context_returns_empty(self):
        """Neither kind nor context → [] (no filters to apply)."""
        graph = SimpleMockGraph()

        result = run_structural_query(graph, kind=None, context=None)

        assert result == []

    def test_graph_raises_returns_empty(self):
        """graph.query raises → []."""
        graph = SimpleMockGraph(raise_on_query=RuntimeError("DB connection lost"))
        result = run_structural_query(graph, kind="statement", context=None)
        assert result == []

    def test_entity_type_event_uses_eventkind(self):
        """entity_type='event' → eventKind + eventId."""
        graph = SimpleMockGraph(result_set=[("evt-1",)])

        result = run_structural_query(
            graph, kind="transcript", context=None, entity_type="event",
        )

        assert result[0][0] == "evt-1"
        cypher = graph.query_calls[0][0]
        assert "n.eventKind" in cypher
        assert "n.eventId" in cypher
        assert "MATCH (n:Event)" in cypher

    def test_entity_type_point_context_is_factored_in(self):
        """For points, context is included in WHERE clause."""
        graph = SimpleMockGraph(result_set=[("p1",)])

        result = run_structural_query(
            graph, kind="statement", context="physics", entity_type="point",
        )

        cypher = graph.query_calls[0][0]
        assert "n.context = $context" in cypher
        assert result[0][1] == 1.0

    def test_entity_type_subject_ignores_context_for_filtering_but_not_score(self):
        """For subject, context is NOT in the Cypher WHERE, but score is 1.0.

        Because run_structural_query checks truthiness of (kind and context)
        for scoring, not whether context was applied to the query. For subject
        with kind="person", context="ignored-context": both are truthy → 1.0.
        """
        graph = SimpleMockGraph(result_set=[("sub-1",)])

        result = run_structural_query(
            graph, kind="person", context="ignored-context", entity_type="subject",
        )

        cypher = graph.query_calls[0][0]
        assert "context" not in cypher
        # Score is 1.0 because both kind + context are truthy, even though
        # context filter wasn't applied to the query (non-point type).
        assert result[0][1] == 1.0

    def test_limit_param_is_passed_to_query(self):
        """limit is passed as a Cypher param."""
        graph = SimpleMockGraph(result_set=[("p1",), ("p2",)])

        run_structural_query(graph, kind="statement", context=None, limit=5)

        assert graph.query_calls[0][1]["limit"] == 5


# ── Cross-cutting edge cases ────────────────────────────────────────────────

class TestCrossCutting:
    """Edge cases spanning multiple engine functions."""

    def test_empty_query_vec_plus_no_query_skips_fts_and_vector(self):
        """Neither query nor query_vec → only structural runs."""
        graph = StrategyControlledGraph({
            "pointKind": ([("s1",)], None),
        })

        result = degradation_chain(
            graph, query=None, kind="statement", context=None,
            query_vec=[], strategies={"fts": True, "vector": True, "structural": True},
        )

        assert "structural" in result
        assert "fts" not in result
        assert "vector" not in result

    def test_run_fts_query_empty_string_query(self):
        """Empty string query still sent to FTS (classification is upstream)."""
        graph = SimpleMockGraph(result_set=[])

        result = run_fts_query(graph, "")

        assert result == []
        assert len(graph.query_calls) == 1

    def test_structural_only_strategy_ignores_fts_vector_flags(self):
        """When only structural is enabled, FTS+vector are not called."""
        graph = SimpleMockGraph(result_set=[("p1",)])

        result = degradation_chain(
            graph, query="test", kind="stmt", context=None,
            query_vec=[0.1] * 384,
            strategies={"fts": False, "vector": False, "structural": True},
        )

        # kind="stmt" with context=None → score=0.5
        assert result == {"structural": [("p1", 0.5)]}


# ── get_relationships ──────────────────────────────────────────────────────

class TestGetRelationships:
    """get_relationships — operator-edge batch fetch for Points (#141).

    Handles direction inference, multiple operator types, predicate/
    mechanism/operator_id fields, error handling, empty inputs.
    """

    # Row shape from the Cypher RETURN clause (9 columns):
    # n.id, mechanism, predicate, operator_id, related_id, related_kind,
    # related_content, n_idx, other_idx
    OUTGOING_ROW = (
        "p1", "IMPL", "supports", "op-1", "p2", "statement",
        "related content", 0, 0,
    )
    INCOMING_ROW = (
        "p1", "NAND", "contradicts", "op-2", "p3", "hypothesis",
        "other content", 1, 0,
    )

    def test_empty_input_returns_empty_dict(self):
        """No point_ids → {} and no DB query is issued."""
        graph = SimpleMockGraph()
        result = get_relationships(graph, [])
        assert result == {}
        assert graph.query_calls == []

    def test_point_with_no_operators_returns_empty_list(self):
        """Graph returns no rows → every requested point maps to [] ."""
        graph = SimpleMockGraph(result_set=[])
        result = get_relationships(graph, ["p1", "p2"])
        assert result == {"p1": [], "p2": []}

    def test_outgoing_impl_direction(self):
        """n_idx=0 → direction='outgoing'; full output shape is populated."""
        graph = SimpleMockGraph(result_set=[self.OUTGOING_ROW])
        result = get_relationships(graph, ["p1"])
        assert result["p1"] == [{
            "predicate": "supports",
            "mechanism": "IMPL",
            "related_id": "p2",
            "related_kind": "statement",
            "related_content": "related content",
            "direction": "outgoing",
            "operator_id": "op-1",
        }]

    def test_incoming_nand_direction(self):
        """n_idx>0 → direction='incoming'; mechanism/operator_id preserved."""
        graph = SimpleMockGraph(result_set=[self.INCOMING_ROW])
        result = get_relationships(graph, ["p1"])
        assert result["p1"][0]["direction"] == "incoming"
        assert result["p1"][0]["mechanism"] == "NAND"
        assert result["p1"][0]["operator_id"] == "op-2"
        assert result["p1"][0]["related_id"] == "p3"

    def test_hasp_part_edge(self):
        """hasPart composition edges are surfaced with their mechanism."""
        row = ("p1", "hasPart", "contains", "op-3", "p4", "statement", "child", 0, 0)
        graph = SimpleMockGraph(result_set=[row])
        result = get_relationships(graph, ["p1"])
        assert result["p1"][0]["mechanism"] == "hasPart"
        assert result["p1"][0]["direction"] == "outgoing"
        assert result["p1"][0]["predicate"] == "contains"

    def test_multiple_points_in_batch(self):
        """Each point in the batch gets its own relationship list."""
        rows = [
            ("p1", "IMPL", "supports", "op-1", "p2", "statement", "c1", 0, 0),
            ("p2", "NAND", "contradicts", "op-2", "p3", "hypothesis", "c2", 1, 0),
        ]
        graph = SimpleMockGraph(result_set=rows)
        result = get_relationships(graph, ["p1", "p2"])
        assert len(result["p1"]) == 1
        assert len(result["p2"]) == 1
        assert result["p1"][0]["related_id"] == "p2"
        assert result["p1"][0]["direction"] == "outgoing"
        assert result["p2"][0]["related_id"] == "p3"
        assert result["p2"][0]["direction"] == "incoming"

    def test_graph_failure_returns_empty_lists_gracefully(self):
        """graph.query raises → per-point empty lists, no crash."""
        graph = SimpleMockGraph(raise_on_query=RuntimeError("Connection refused"))
        result = get_relationships(graph, ["p1", "p2"])
        assert result == {"p1": [], "p2": []}

    def test_mechanism_defaults_to_impl_when_missing(self):
        """None mechanism/predicate/kind/content → safe defaults."""
        row = ("p1", None, None, "op-1", "p2", None, None, 0, 0)
        graph = SimpleMockGraph(result_set=[row])
        result = get_relationships(graph, ["p1"])
        entry = result["p1"][0]
        assert entry["mechanism"] == "IMPL"
        assert entry["predicate"] == ""
        assert entry["related_kind"] == ""
        assert entry["related_content"] == ""

    def test_related_content_truncated_to_200_chars(self):
        """related_content longer than 200 chars is truncated."""
        row = ("p1", "IMPL", "supports", "op-1", "p2", "statement", "x" * 500, 0, 0)
        graph = SimpleMockGraph(result_set=[row])
        result = get_relationships(graph, ["p1"])
        assert len(result["p1"][0]["related_content"]) == 200

    def test_row_missing_index_columns_defaults_to_incoming(self):
        """Short row (no idx columns) → n_idx=None → direction='incoming'."""
        row = ("p1", "IMPL", "supports", "op-1", "p2", "statement", "content")
        graph = SimpleMockGraph(result_set=[row])
        result = get_relationships(graph, ["p1"])
        assert result["p1"][0]["direction"] == "incoming"

    def test_row_for_unrequested_point_is_dropped(self):
        """Rows whose pid was not requested are filtered out."""
        graph = SimpleMockGraph(result_set=[self.OUTGOING_ROW])  # row is for "p1"
        result = get_relationships(graph, ["p9"])
        assert result == {"p9": []}

    def test_query_receives_ids_param_and_operator_edge_patterns(self):
        """Cypher covers IMPL|NAND|hasPart operator edges + ids param."""
        graph = SimpleMockGraph(result_set=[])
        get_relationships(graph, ["p1", "p2"])
        cypher, params = graph.query_calls[0]
        assert params == {"ids": ["p1", "p2"]}
        assert "IMPL|NAND|hasPart" in cypher
        assert "is_operator:true" in cypher


# ------------------------------------------------------------------ #125 Document FTS + backfill


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
def test_document_fts_index_created(live_proj_fixture=None):
    """#125: Document._searchText FTS index exists after projection init."""
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(_WORKING_URI)
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj._ensure_indexes()
    # db.indexes() output: [label, properties, ...] — label is col 0, props col 1
    rows = proj.g.query("CALL db.indexes()").result_set
    found = any(r[0] == "Document" and "_searchText" in r[1] for r in rows)
    proj.close()
    assert found, f"Document _searchText FTS index missing: {rows[:3]}"


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
def test_backfill_document_search_text():
    """#125: backfill sets _searchText=title on pre-existing Documents."""
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(_WORKING_URI)
    proj.g.query("MATCH (n) DETACH DELETE n")
    # Create a Document WITHOUT _searchText (simulating pre-125)
    proj.g.query(
        "CREATE (d:Document {id:'old-1', title:'Old Doc', documentKind:'transcript'})"
    )
    n = proj.backfill_document_search_text()
    rows = proj.g.query("MATCH (d:Document {id:'old-1'}) RETURN d._searchText").result_set
    proj.close()
    assert n >= 1, f"backfill returned {n}"
    assert rows and rows[0][0] == "Old Doc", rows


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
def test_document_fts_search_by_topic():
    """#125: Document FTS on _searchText returns sessions matching a topic."""
    from tortoise.projection import FalkorProjection
    import tortoise.search_engine as se
    proj = FalkorProjection.from_uri(_WORKING_URI)
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj._ensure_indexes()
    proj.g.query(
        "CREATE (d:Document {id:'doc-t1', title:'Licensing Talk', "
        "documentKind:'transcript', _searchText:'Licensing Talk Compared AGPL licenses'})"
    )
    try:
        # FTS query by topic word (in _searchText)
        hits = se.run_fts_query(proj.g, "AGPL", entity_type="document")
        ids = [h[0] for h in hits]
        assert "doc-t1" in ids, f"FTS did not return doc-t1: {hits}"
        # Structural query by documentKind
        s_hits = se.run_structural_query(proj.g, kind="transcript", context=None, entity_type="document")
        assert any(h[0] == "doc-t1" for h in s_hits), f"structural miss: {s_hits}"
    finally:
        proj.close()


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
def test_document_structural_topic_any():
    """#125: any() list filter matches topics on Document nodes."""
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(_WORKING_URI)
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.g.query(
        "CREATE (d:Document {id:'doc-a', topics:['licensing','AGPL'], documentKind:'transcript'})"
    )
    try:
        rows = proj.g.query(
            "MATCH (d:Document) WHERE any(t IN d.topics WHERE t = 'licensing') RETURN d.id"
        ).result_set
        assert any(r[0] == "doc-a" for r in rows), rows
    finally:
        proj.close()
