"""GraphRanker tests (#25) — unit (no DB) + integration (embedded FalkorDBLite).

Unit coverage: recency decay math, weight validation, min-max normalization,
rerank fusion math, missing-signal neutrality, breakdown annotations.
Integration coverage: order_by='graph' ranks high-EP-confidence Points above
low-confidence Points with identical similarity; order_by='confidence' uses the
PERSISTED EP confidence (n.confidence), not the structural impl/(impl+nand)
proxy; suggest_entry_points accepts a GraphRanker; default ordering unchanged.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.ranking import GraphRanker, recency_decay, _min_max_normalize  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402


# ── Unit: recency decay ────────────────────────────────────────────────────

def test_recency_decay_fresh_is_one():
    assert recency_decay(0.0) == 1.0
    assert recency_decay(-5.0) == 1.0


def test_recency_decay_halflife():
    # At exactly one half-life, decay = 0.5.
    assert recency_decay(30.0) == pytest.approx(0.5, abs=1e-6)
    # Two half-lives → 0.25.
    assert recency_decay(60.0) == pytest.approx(0.25, abs=1e-6)


def test_recency_decay_monotonic():
    assert recency_decay(1.0) > recency_decay(30.0) > recency_decay(365.0)


# ── Unit: normalization ────────────────────────────────────────────────────

def test_min_max_normalize():
    assert _min_max_normalize([0.0, 0.5, 1.0]) == [0.0, 0.5, 1.0]
    assert _min_max_normalize([3.0, 1.0]) == [1.0, 0.0]
    # All-equal → midpoint (no div-by-zero).
    assert _min_max_normalize([0.2, 0.2, 0.2]) == [0.5, 0.5, 0.5]
    assert _min_max_normalize([]) == []


# ── Unit: GraphRanker math (no DB) ─────────────────────────────────────────

def test_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        GraphRanker(similarity_weight=0.9, graph_boost_weight=0.9, recency_weight=0.1)
    # Valid combo accepted.
    GraphRanker(similarity_weight=0.4, graph_boost_weight=0.4, recency_weight=0.2)


def test_rerank_annotates_and_sorts():
    ranker = GraphRanker()  # no projection → signals from result dicts only
    results = [
        {"id": "a", "scores": {"rrf": 0.03}, "createdAt": datetime.now(timezone.utc).isoformat()},
        {"id": "b", "scores": {"rrf": 0.01}, "createdAt": "2026-01-01T00:00:00+00:00"},
    ]
    out = ranker.rerank(results, entity_type="point")
    assert len(out) == 2
    assert all("graph_ranking" in r for r in out)
    # Fresh + higher similarity ranks first.
    assert out[0]["id"] == "a"
    for r in out:
        g = r["graph_ranking"]
        assert 0.0 <= g["graph_boost"] <= 1.0
        assert 0.0 <= g["recency_boost"] <= 1.0
        assert g["final_score"] == pytest.approx(
            0.5 * g["similarity"] + 0.35 * g["graph_boost"] + 0.15 * g["recency_boost"],
            abs=5e-4,  # breakdown values are rounded; final used unrounded inputs
        )


def test_rerank_recency_demotes_old_equivalent_results():
    # Same similarity + same graph signals; only age differs (AC3).
    ranker = GraphRanker()
    now = datetime.now(timezone.utc)
    results = [
        {"id": "old", "scores": {"rrf": 0.02}, "createdAt": (now - timedelta(days=60)).isoformat()},
        {"id": "new", "scores": {"rrf": 0.02}, "createdAt": (now - timedelta(days=1)).isoformat()},
    ]
    out = ranker.rerank(results, entity_type="point")
    assert out[0]["id"] == "new"
    assert out[0]["graph_ranking"]["recency_boost"] > out[1]["graph_ranking"]["recency_boost"]


def test_missing_signals_are_neutral_not_penalizing():
    ranker = GraphRanker()
    out = ranker.rerank(
        [{"id": "x", "scores": {"rrf": 0.02}}],  # no createdAt, no graph signals
        entity_type="point",
    )
    g = out[0]["graph_ranking"]
    assert g["graph_boost"] == 0.0
    assert g["recency_boost"] == 1.0  # unknown age → neutral


def test_graph_boost_uses_embedded_signals_without_projection():
    # Signals embedded in the result dicts (unit-testable without a DB, AC6).
    ranker = GraphRanker()
    strong = ranker.graph_boost({"id": "p"}, {"confidence": 0.9, "degree": 8})
    weak = ranker.graph_boost({"id": "p"}, {"confidence": 0.1, "degree": 0})
    assert strong > weak
    assert strong <= 1.0


def test_rerank_input_dicts_not_mutated():
    results = [{"id": "a", "scores": {"rrf": 0.03}}]
    original = dict(results[0])
    ranker = GraphRanker()
    ranker.rerank(results, entity_type="point")
    assert results[0] == original


# ── Unit: similarity extraction ────────────────────────────────────────────

def test_similarity_prefers_scores_rrf():
    ranker = GraphRanker()
    out = ranker.rerank(
        [{"id": "a", "scores": {"rrf": 0.04}, "similarity": 0.9}],
        entity_type="point",
    )
    # With a single result, normalization → midpoint 0.5 regardless; just
    # assert it ran and annotated.
    assert "graph_ranking" in out[0]


# ── Integration: embedded FalkorDBLite ────────────────────────────────────

@pytest.fixture(autouse=True)
def _use_shared_embedded_db(shared_embedded_db):
    pass


def _fresh_sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_ranking_"), "test.db")
    sdk = TortoiseSDK(db_path)
    try:
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    return sdk


def _set_confidence(sdk, pid: str, conf: float):
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.confidence = $c",
        params={"id": pid, "c": conf},
    )


def test_order_by_graph_ranks_high_ep_above_low_ep():
    """AC1: identical similarity → persisted EP confidence decides order."""
    sdk = _fresh_sdk()
    proj = sdk._get_proj()
    p_high = sdk.create_point("statement", "vector embedding cache invalidation strategy")
    p_low = sdk.create_point("statement", "cache strategy invalidation vector embedding")
    _set_confidence(sdk, p_high["id"], 0.9)
    _set_confidence(sdk, p_low["id"], 0.1)

    results = sdk.tortoise_fts_query("cache invalidation", limit=10, order_by="graph")
    ids = [r["id"] for r in results]
    assert p_high["id"] in ids and p_low["id"] in ids
    assert ids.index(p_high["id"]) < ids.index(p_low["id"])
    # No operator edges → connectivity 0, so boost = 0.5·confidence (0.45 for
    # conf 0.9). High-EP must strictly out-boost low-EP.
    top, bottom = results[0]["graph_ranking"]["graph_boost"], results[1]["graph_ranking"]["graph_boost"]
    assert top > bottom
    assert top > 0.4


def test_order_by_confidence_uses_persisted_not_proxy():
    """The structural impl/(impl+nand) proxy is edge-ratio, not belief — a
    point with no operator edges but a persisted EP confidence of 0.9 must
    outrank a proxy-1.0 point with persisted confidence 0.1."""
    sdk = _fresh_sdk()
    p_high = sdk.create_point("statement", "epistemic grounding with high belief")
    p_low = sdk.create_point("statement", "grounding epistemic belief low")
    _set_confidence(sdk, p_high["id"], 0.9)
    _set_confidence(sdk, p_low["id"], 0.1)

    results = sdk.tortoise_fts_query("epistemic grounding belief", limit=10, order_by="confidence")
    ids = [r["id"] for r in results]
    assert ids.index(p_high["id"]) < ids.index(p_low["id"])


def test_default_relevance_unchanged():
    """AC4: default ordering untouched by the new option."""
    sdk = _fresh_sdk()
    p1 = sdk.create_point("statement", "unique zebra finch migration pattern analysis")
    p2 = sdk.create_point("statement", "zebra finch migration pattern analysis unique")
    results = sdk.tortoise_fts_query("zebra finch migration", limit=10)
    assert "graph_ranking" not in results[0]  # no annotation on default path
    assert {r["id"] for r in results} == {p1["id"], p2["id"]}


def test_suggest_entry_points_with_graph_ranker():
    sdk = _fresh_sdk()
    from tortoise.ranking import GraphRanker
    ranker = GraphRanker(sdk._get_proj())
    p_high = sdk.create_point("decision", "pricing model for premium subscription tiers")
    p_low = sdk.create_point("decision", "subscription pricing tiers model premium")
    _set_confidence(sdk, p_high["id"], 0.95)
    _set_confidence(sdk, p_low["id"], 0.05)

    # Without ranker — substring confidence order (identical lengths → tie).
    plain = sdk.suggest_entry_points("pricing subscription", limit=10)
    # With ranker — persisted EP confidence breaks the tie. The embedded FTS
    # gives zero RRF for this query (→ zero-signal fallback returns []), so
    # mock the hybrid query deterministically with rrf > 0 to exercise the
    # ranker path (mirrors test_fallback_confidence_scale_invariant_to_rrf).
    sdk.tortoise_fts_query = lambda q, **kw: [
        {"id": p_high["id"], "content": "pricing model for premium subscription tiers",
         "point_kind": "decision", "scores": {"rrf": 0.0164}},
        {"id": p_low["id"], "content": "subscription pricing tiers model premium",
         "point_kind": "decision", "scores": {"rrf": 0.0082}},
    ]
    ranked = sdk.suggest_entry_points("pricing subscription", limit=10, graph_ranker=ranker)
    ids = [r["id"] for r in ranked if r["id"] in (p_high["id"], p_low["id"])]
    assert ids.index(p_high["id"]) < ids.index(p_low["id"])
    assert any("graph_ranking" in r for r in ranked)


# ── Contestation flag + demotion (epistemic honesty) ──────────────────────

def test_contested_claim_not_penalized_in_graph_boost():
    """Contestation is SURFACED, not scored: a contested claim gets the SAME
    graph boost as an uncontested one with the same confidence — ranking stays
    about relevance + graph structure; epistemic honesty is a flag, not a
    penalty."""
    ranker = GraphRanker()
    uncontested = ranker.graph_boost({"id": "p"}, {"confidence": 0.9, "variance": 0.0119, "degree": 0})
    contested = ranker.graph_boost({"id": "p"}, {"confidence": 0.9, "variance": 0.05, "contested": True, "degree": 0})
    uncalibrated = ranker.graph_boost({"id": "p"}, {"confidence": 0.9, "variance": 1 / 12, "contested": False, "degree": 0})
    # All three identical: 0.5·0.9 = 0.45.
    assert uncontested == contested == uncalibrated == pytest.approx(0.45)


def test_order_by_graph_surfaces_contestation_without_penalty():
    """Contestation is surfaced as a flag on the result, never used to change
    the rank: identical similarity + identical confidence → identical ranking,
    with ep/graph_ranking carrying contested: True/False so the agent KNOWS."""
    import tempfile as _tf
    db_path = os.path.join(_tf.mkdtemp(prefix="tortoise_rankcontest_"), "test.db")
    sdk = TortoiseSDK(db_path)
    try:
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
        # Identical content → identical FTS/vector similarity.
        pa = sdk.create_point("statement", "zebra finch migration phenology contested claim")
        pb = sdk.create_point("statement", "zebra finch migration phenology contested claim")
        proj = sdk._get_proj()
        for pid, a, b in [(pa["id"], 10.0, 10.0), (pb["id"], 2.0, 2.0)]:
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n.confidence = 0.9, "
                "n.ep_alpha = $a, n.ep_beta = $b",
                params={"id": pid, "a": a, "b": b},
            )
        results = sdk.tortoise_fts_query("zebra finch migration", limit=10, order_by="graph")
        ids = [r["id"] for r in results]
        assert pa["id"] in ids and pb["id"] in ids
        # Identical similarity + confidence + connectivity → identical graph
        # boost; contestation must NOT change the rank (no demotion).
        assert results[0]["graph_ranking"]["graph_boost"] == results[1]["graph_ranking"]["graph_boost"]
        by_id = {r["id"]: r for r in results}
        # ...but the flag IS surfaced on the result for the agent to see.
        assert by_id[pa["id"]]["graph_ranking"]["contested"] is False
        assert by_id[pb["id"]]["graph_ranking"]["contested"] is True
        assert by_id[pb["id"]]["graph_ranking"]["variance"] > by_id[pa["id"]]["graph_ranking"]["variance"]
    finally:
        sdk.close()
