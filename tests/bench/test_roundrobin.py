"""Query-mix round-robin guarantee (issue #316 review, P2).

_runroundrobin must guarantee inclusion of the special queries (no-match
degrade triggers, kind-bearing, kind-only structural) whenever the sample
count is smaller than the mix — otherwise default samples=50 < 56 queries
would measure only the first n queries and the mix-weighted p95 would never
see the degrade/structural tail. Pure function test: no graph, no DB.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.run_report import _is_special_query, _roundrobin

pytestmark = pytest.mark.bench

MIX_PATH = Path(__file__).resolve().parent.parent.parent / "benchmarks" / "query_mix.json"
MIX = json.loads(MIX_PATH.read_text())
ALL_QUERIES: list[dict] = MIX["queries"]


def _arm_queries(arm: str) -> list[dict]:
    return [q for q in ALL_QUERIES if arm in q.get("arms", [])]


def _special_ids(arm: str) -> list[str]:
    return [q["id"] for q in _arm_queries(arm) if _is_special_query(q)]


def test_special_queries_are_guaranteed_at_default_samples():
    """With default samples=50 < 56 distinct queries, every special query must
    still appear in the picks for every arm."""
    for arm in ("fts", "vector", "hybrid", "tfidf", "e2e"):
        arm_qs = _arm_queries(arm)
        assert len(arm_qs) > 50, f"{arm} mix must exceed default samples for this test"
        picks = _roundrobin(arm_qs, 50)
        picked_ids = {q["id"] for q in picks}
        missing = [sid for sid in _special_ids(arm) if sid not in picked_ids]
        assert not missing, f"{arm} picks dropped special queries: {missing}"
        assert len(picks) == 50


def test_specials_are_spread_not_front_loaded():
    """Specials must be spread across the picks (not all at the front) so at
    least one lands beyond the max warmup consumption (20) into the measured
    window, and no single special type is systematically excluded."""
    arm_qs = _arm_queries("e2e")
    picks = _roundrobin(arm_qs, 50)
    special_positions = [i for i, q in enumerate(picks) if _is_special_query(q)]
    assert len(special_positions) == len(_special_ids("e2e"))  # every special placed
    # Evenly spread: spacing between consecutive specials ~ n/len(specials) = 6.
    gaps = [b - a for a, b in zip(special_positions, special_positions[1:])]  # noqa: B905, RUF007
    assert 3 <= min(gaps) and max(gaps) <= 10  # noqa: SIM300
    # At least one special sits beyond the warmup-max region (20 iters) so the
    # measured window sees a special without relying on cycle wrap-around.
    assert special_positions[-1] > 20


def test_roundrobin_full_cycle_when_n_ge_mix_size():
    """n >= mix size → plain cycling covers every query (unchanged behavior)."""
    arm_qs = _arm_queries("fts")
    picks = _roundrobin(arm_qs, 60)
    assert len(picks) == 60
    assert {q["id"] for q in picks} == {q["id"] for q in arm_qs}


def test_roundrobin_empty_and_tiny():
    assert _roundrobin([], 50) == []
    arm_qs = _arm_queries("hybrid")  # 56 queries, 8 specials
    picks = _roundrobin(arm_qs, 5)   # n < number of specials
    assert len(picks) == 5
    assert all(_is_special_query(q) for q in picks)  # specials only, cycled
