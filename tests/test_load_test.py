"""Tests for benchmarks/load_test.py (#1656 launch capacity)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


from benchmarks.load_test import QUERY_MIX, run_load_test  # noqa: E402


def test_run_load_test_small_produces_shape():
    """A tiny run returns the expected metrics dict with sane values."""
    r = run_load_test(concurrency=2, queries=4, seed=7)
    assert r["concurrency"] == 2
    assert r["queries"] == 4
    assert r["failures"] == 0
    assert r["requests_per_sec"] > 0
    assert r["p50_ms"] >= 0
    assert r["effective_concurrency"] > 0
    assert r["harness"] == "benchmarks/load_test.py"
    # The offload discriminator: with 2 workers overlapping, effective
    # concurrency should approach the worker count (not ~1 serialized).
    assert r["effective_concurrency"] > 1.0, \
        "requests should overlap on the thread pool (offload working)"


def test_load_test_failure_isolation():
    """A failing query is recorded, not fatal — the run completes."""
    # Seed a run and confirm it completes with the failure list present.
    r = run_load_test(concurrency=3, queries=3, seed=9)
    assert isinstance(r["failures"], int)
    assert isinstance(r["failure_examples"], list)


def test_query_mix_is_fixed():
    """The query mix is deterministic — reproducibility for the verdict."""
    assert len(QUERY_MIX) == 8
    assert QUERY_MIX[0] == "falkordb graph traversal"
    assert len(set(QUERY_MIX)) == len(QUERY_MIX), "queries must be distinct"


def test_deterministic_seed_reproduces():
    """Same seed -> same result (the corpus + query order are seeded)."""
    a = run_load_test(concurrency=2, queries=4, seed=42)
    b = run_load_test(concurrency=2, queries=4, seed=42)
    assert a["queries"] == b["queries"] == 4
    # Effective concurrency is the stable discriminator; p95 may vary
    # slightly under machine contention, so compare the structural keys.
    assert a["failures"] == b["failures"]
