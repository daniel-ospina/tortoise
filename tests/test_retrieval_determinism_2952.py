"""Retrieval ranking must be a pure function of (graph, query, params) — #2952.

Issue #2952: for a static, unchanged store the ``recall_state`` /
``tortoise_fts_query`` top-k changed purely with wall-clock time, so the
MemoryAgentBench CR Tortoise lane (#2800) could not be replayed.

The root cause on this surface is that the fused ranking inherited two
*accidental* orderings from the retrieval plumbing:

1. ``degradation_chain`` populated its returned mapping inside
   ``concurrent.futures.as_completed`` — i.e. in thread COMPLETION order, which
   shifts with leg warm-up/latency (wall-clock dependent).
2. ``rrf_fusion`` then broke score TIES by insertion order (a stable sort of a
   dict built in that same completion order), so tied docs swapped places run
   to run. RRF ties are the norm on real corpora — same rank in different legs,
   or FalkorDBLite fulltext scores, which are 0.0 for every document.

Together those made the truncated top-k a function of how fast the legs
responded. The fix gives the fusion a deterministic TOTAL order over ties
(``(-score, id)``) and pins the leg order to (fts, vector, structural).

The one INTENTIONAL time-dependent ranking term — ``GraphRanker``'s exponential
recency decay — is kept, but its reference time is now an explicit, injectable
constructor input so a replay can pin the anchor.

Everything here is hermetic (no DB, no embedder), so it belongs in the unit
(`core`) surface and is safe on the URI-less CI lanes.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import UTC, datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise import search_engine as se  # noqa: E402, RUF100
from tortoise.ranking import GraphRanker, StateRanker
from tortoise.search_engine import degradation_chain, rrf_fusion


@pytest.fixture(autouse=True)
def _reset_breakers():
    """Circuit-breaker state is module-level — isolate every test."""
    se.reset_circuit_breakers()
    yield
    se.reset_circuit_breakers()


# ── rrf_fusion: stable total order over ties ───────────────────────────────

#: Leg contents chosen so RRF ties by construction: ``a`` is rank 0 in fts and
#: rank 1 in vector; ``b`` is the mirror. Both score 1/(k+1) + 1/(k+2) — the
#: same two terms, so their fused scores are EXACTLY equal and only the
#: tie-break decides their order.
_FTS = [("a", 0.9), ("b", 0.9)]
_VECTOR = [("b", 0.8), ("a", 0.8)]


def test_rrf_fusion_tie_order_is_leg_order_independent():
    """Same leg CONTENTS, different list order → byte-identical fused order.

    ``degradation_chain`` handed the fusion its legs in thread completion
    order, so this is the exact shape of the #2952 drift: identical inputs,
    different (timing-dependent) ordering, different top-k after truncation.
    """
    forward = list(rrf_fusion([_FTS, _VECTOR], strategy_names=["fts", "vector"]))
    reverse = list(rrf_fusion([_VECTOR, _FTS], strategy_names=["vector", "fts"]))
    assert forward == reverse, (
        "RRF tie order must not depend on the order the legs were passed in "
        f"(got {forward} vs {reverse})"
    )


def test_rrf_fusion_ties_break_by_id_not_insertion_order():
    """Equal-score docs come back in id order — a deterministic total order."""
    out = list(rrf_fusion([[("zz", 1.0)], [("aa", 1.0)]]))
    assert out == ["aa", "zz"]


def test_rrf_fusion_weights_still_apply():
    """Weighting is untouched by the tie-break (regression guard, #1657)."""
    plain = list(rrf_fusion([[("a", 0.9)], [("z", 0.8)]],
                            strategy_names=["fts", "vector"]))
    assert plain[0] == "a"
    boosted = list(rrf_fusion([[("a", 0.9)], [("z", 0.8)]],
                              strategy_names=["fts", "vector"],
                              weights={"vector": 1.5}))
    assert boosted[0] == "z"  # the vector leg's top hit, boosted


# ── degradation_chain: fixed leg order regardless of completion order ──────

class _SimClock:
    """Standing in for wall-clock time so the test can jump it forward."""

    def __init__(self) -> None:
        self.t = 0.0


def _install_clock_dependent_legs(monkeypatch, clock, *, slow_leg: str):
    """Install fake legs whose LATENCY flips with the simulated clock.

    Models a cold leg (slow, loses the race) vs a warm one (fast, wins it):
    the two runs below return identical leg CONTENTS in a different completion
    order, which is what a warm-up / clock jump produced in #2952.
    """
    def _delays():
        fast, slow = 0.0, 0.06
        if slow_leg == "fts":
            return (slow, fast) if clock.t >= 90 else (fast, slow)
        return (fast, slow) if clock.t >= 90 else (slow, fast)

    def fake_fts(_graph, _query=None, **_kw):
        time.sleep(_delays()[0])
        return list(_FTS)

    def fake_vector(_graph, _query_vec=None, **_kw):
        time.sleep(_delays()[1])
        return list(_VECTOR)

    monkeypatch.setattr(se, "run_fts_query", fake_fts)
    monkeypatch.setattr(se, "run_vector_query", fake_vector)


def _run_chain():
    return degradation_chain(
        object(), query="q", kind=None, query_vec=[0.1] * 384,
        strategies={"fts": True, "vector": True, "structural": False},
        entity_type="point", limit=20, is_embedded=True,
    )


def test_degradation_chain_returns_legs_in_fixed_order(monkeypatch):
    """Completion order must not leak into the returned mapping (#2952)."""
    clock = _SimClock()
    _install_clock_dependent_legs(monkeypatch, clock, slow_leg="vector")

    run_cold = _run_chain()
    assert list(run_cold) == ["fts", "vector"]

    clock.t += 90.0  # simulated clock jump: the vector leg is warm now
    run_warm = _run_chain()
    assert list(run_warm) == ["fts", "vector"], (
        "leg order must be fixed (fts, vector, structural), not completion order"
    )
    assert run_cold == run_warm  # identical contents, identical mapping order


def test_fused_topk_is_identical_across_a_clock_jump(monkeypatch):
    """The #2952 acceptance test: jump the clock 90s, same top-k ids.

    Two runs of the same query against the same fixed legs, with the leg
    completion order flipped between them (cold → warm). The fused ranking
    must be byte-identical: retrieval is a function of the leg CONTENTS, not
    of elapsed time.
    """
    clock = _SimClock()
    _install_clock_dependent_legs(monkeypatch, clock, slow_leg="vector")

    def fused_ids() -> list[str]:
        legs = _run_chain()
        return list(rrf_fusion(list(legs.values()),
                               strategy_names=list(legs), k=60))

    before = fused_ids()
    clock.t += 90.0
    after = fused_ids()
    assert before == after, f"top-k drifted across the clock jump: {before} vs {after}"


# ── no wall-clock term in the state ranker (recall_state's ranker) ─────────

class _ForbiddenClock:
    """A ``datetime`` stand-in whose ``now()`` FAILS on any call.

    Installed as ``tortoise.ranking.datetime`` so an accidental wall-clock
    term inside the state ranking raises instead of silently making the
    result time-dependent (#2952). ``fromisoformat`` still delegates to the
    real class (``_parse_iso`` is a legitimate, non-temporal user).
    """

    @staticmethod
    def fromisoformat(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def now(self, *_a, **_kw):  # pragma: no cover - failure path
        raise AssertionError(
            "ranking read the wall clock — the ranked output must be a pure "
            "function of (graph, query, params) (#2952)"
        )


def test_state_ranker_has_no_wall_clock_term(monkeypatch):
    """``StateRanker`` (the ``recall_state`` re-ranker) never reads a clock.

    Regression guard for the #2952 hypothesis class: an EP/posterior or
    recency/decay term sourced from ``now()``. The forbidden clock turns any
    such term into a hard failure rather than a silently time-varying score.
    """
    import tortoise.ranking as ranking

    results = [
        {"id": "a", "scores": {"rrf": 0.05}, "state_confidence": 0.8,
         "state_has_ep": True, "state_degree": 1},
        {"id": "b", "scores": {"rrf": 0.05}, "state_confidence": 0.7,
         "state_has_ep": True, "state_degree": 3},
        {"id": "c", "scores": {"rrf": 0.04}, "state_confidence": 0.9,
         "state_has_ep": True, "state_degree": 0},
    ]
    monkeypatch.setattr(ranking, "datetime", _ForbiddenClock())
    ranker = StateRanker()
    first = ranker.rerank([dict(r) for r in results])
    second = ranker.rerank([dict(r) for r in results])
    assert first == second
    assert [r["id"] for r in first] == ["a", "b", "c"]


# ── GraphRanker recency: intentional, but with an injectable anchor ────────

def _dated_items():
    # Equal relevance + no graph signals → the recency term is the only
    # differentiator, exactly as in the order_by="graph" path.
    return [
        {"id": "new", "scores": {"rrf": 0.05}, "createdAt": "2025-12-01T00:00:00Z"},
        {"id": "old", "scores": {"rrf": 0.05}, "createdAt": "2025-06-01T00:00:00Z"},
    ]


def test_graph_ranker_pinned_anchor_is_reproducible_across_a_clock_jump():
    """A pinned ``now`` anchor makes the recency-weighted ranking replayable.

    The same ranker is re-run after the wall clock has moved on; with an
    explicit anchor the full ranking breakdown is byte-identical, and a
    ranker pinned to a DIFFERENT anchor demonstrably scores the same claim
    differently (so the anchor — not some ambient clock — is the reference).
    """
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    ranker = GraphRanker(now=lambda: anchor)
    first = ranker.rerank(_dated_items())
    second = ranker.rerank(_dated_items())  # wall clock moved on; anchor pinned
    assert first == second
    assert first[0]["id"] == "new"

    shifted = GraphRanker(now=lambda: anchor + timedelta(days=3650))
    here = {r["id"]: r["graph_ranking"]["recency_boost"] for r in first}
    there = {r["id"]: r["graph_ranking"]["recency_boost"]
             for r in shifted.rerank(_dated_items())}
    assert here != there, "the injected anchor must be the recency reference"


def test_graph_ranker_recency_anchor_is_a_real_input():
    """The anchor drives the decay — recency stays intentional, not decoration.

    Two anchors 400 days apart give the SAME claim a materially different
    recency factor, proving the injected reference time is the one actually
    used (and that the 30-day half-life decay is preserved as a feature).
    """
    claim = {"id": "x", "createdAt": "2025-06-01T00:00:00Z"}
    far = GraphRanker(now=lambda: datetime(2026, 7, 1, tzinfo=UTC))
    near = GraphRanker(now=lambda: datetime(2025, 6, 2, tzinfo=UTC))
    far_boost = far.recency_boost(claim, {})
    near_boost = near.recency_boost(claim, {})
    assert far_boost < near_boost, (
        "the injected anchor must drive the decay "
        f"(400 days later gave {far_boost}, 1 day later gave {near_boost})"
    )
    assert near_boost > 0.9  # 1 day old → essentially fresh
    assert far_boost < 0.05  # 13 months old on a 30-day half-life → ~0


def test_graph_ranker_defaults_to_the_live_clock():
    """No ``now`` argument → the live UTC clock (unchanged behaviour)."""
    before = datetime.now(UTC)
    observed = GraphRanker()._now()
    after = datetime.now(UTC)
    assert before <= observed <= after, (
        "the default recency reference must be the live UTC clock, "
        f"got {observed!r} outside [{before!r}, {after!r}]"
    )
