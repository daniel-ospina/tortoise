"""degradation_chain elevated-timeout test (issue #316 review, P2).

The elevated_timeout_ms benchmark override must actually ELEVATE the
measurement window: with slow strategy runners, the default 500ms collective
cap truncates collection (as_completed times out → partial results), while
elevated_timeout_ms lets every strategy complete. Uses monkeypatched strategy
runners — no graph, no DB.

Note: ThreadPoolExecutor joins worker threads on exit, so the censored call
waits out the slow futures (~0.6s) even though the collection loop already
moved on — the test is intentionally tolerant of that.
"""
from __future__ import annotations

import time

import pytest

from tortoise.search_engine import degradation_chain, reset_circuit_breakers

pytestmark = pytest.mark.bench


@pytest.fixture(autouse=True)
def _reset_breakers():
    """Circuit breakers are module-level and persist across tests (#249); a
    vector strategy that fails in an earlier test (e.g. no index in the
    embedded env) trips the breaker and silently skips vector in later
    tests. Reset before each test so strategy filtering is per-test."""
    reset_circuit_breakers()
    yield
    reset_circuit_breakers()


def test_elevated_timeout_collects_more_than_censored(monkeypatch):
    """A 600ms strategy is dropped by the 500ms collective cap but collected
    under elevated_timeout_ms=5000 — proving the override actually elevates
    the measurement window (more strategies → more collected rows)."""

    def slow_fts(graph, query, entity_type="point", limit=20, timeout_ms=500,
                 excluded_statuses=None):
        time.sleep(0.6)
        return [("p1", 1.0)]

    def slow_vector(graph, query_vec, limit=20, timeout_ms=500,
                    is_embedded=True, entity_type="point",
                    vector_index_api=None, excluded_statuses=None):
        time.sleep(0.6)
        return [("p2", 0.9)]

    def fast_structural(graph, kind, entity_type="point", limit=20, timeout_ms=500,
                        excluded_statuses=None):
        return [("p3", 0.5)]

    monkeypatch.setattr("tortoise.search_engine.run_fts_query", slow_fts)
    monkeypatch.setattr("tortoise.search_engine.run_vector_query", slow_vector)
    monkeypatch.setattr("tortoise.search_engine.run_structural_query", fast_structural)

    strategies = {"fts": True, "vector": True, "structural": True}

    # Censored (default): 500ms collective cap → as_completed times out → only
    # the fast structural strategy is collected (fts/vector still sleeping).
    censored = degradation_chain(
        graph=None, query="q", kind="claim", query_vec=[0.1, 0.2, 0.3],
        strategies=strategies,
    )
    assert set(censored) == {"structural"}

    # Elevated: 5000ms window → all three strategies complete.
    elevated = degradation_chain(
        graph=None, query="q", kind="claim", query_vec=[0.1, 0.2, 0.3],
        strategies=strategies, elevated_timeout_ms=5000,
    )
    assert set(elevated) == {"fts", "vector", "structural"}
    # The elevated column collected strictly more strategy results than the
    # censored column.
    assert len(elevated) > len(censored)


def test_elevated_timeout_is_default_off(monkeypatch):
    """elevated_timeout_ms=None keeps byte-identical behavior: a fast strategy
    set completes fully under the default 500ms cap."""
    monkeypatch.setattr(
        "tortoise.search_engine.run_fts_query",
        lambda graph, query, entity_type="point", limit=20, timeout_ms=500,
               excluded_statuses=None: [("p1", 1.0)],
    )
    monkeypatch.setattr(
        "tortoise.search_engine.run_vector_query",
        lambda graph, query_vec, limit=20, timeout_ms=500, is_embedded=True,
        entity_type="point", vector_index_api=None,
        excluded_statuses=None: [("p2", 0.9)],
    )
    strategies = {"fts": True, "vector": True}

    results = degradation_chain(
        graph=None, query="q", kind=None, query_vec=[0.1, 0.2, 0.3],
        strategies=strategies,
    )
    assert set(results) == {"fts", "vector"}
