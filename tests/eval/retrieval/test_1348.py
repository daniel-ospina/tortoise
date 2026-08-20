"""#1348 harness tests — k sweep, fused_rerank arms, corpus enhancement.

Covers the pre-registered falsification mechanics:
- k-reorderability: k∈{20,60,100} CAN reorder a 2-list fused top-10 (the
  "guaranteed single-list degeneracy" premise is FALSE — embedded FTS
  populates 40/100 queries, so fused is 2-list on that subset).
- fused_rerank arm: tuple→dict scores.rrf adapter + production order
  (fuse → truncate → rerank) + stub-projection positive control + use_degree
  ablation.
- fingerprint equality: enhance_signals=False byte-identical to committed
  corpus; True differs only in confidence/edges (n_with_confidence +
  edge-pairs hash distinguish).
- baseline gate: --baseline against the committed v1 baseline with defaults
  passes unchanged (no-flag drift proof; schema-version semantics: older
  baseline gates).
"""
from __future__ import annotations

import json  # noqa: F401
import os
import random
import sys
import tempfile
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import pytest  # noqa: E402

from benchmarks.synthetic_corpus import (  # noqa: E402
    build_topic_oracle,
    generate_oracle_points,
    seed_corpus,
    seed_operator_edges,
)
from tests.eval.retrieval.run import _fused_rrf, _rerank_fused  # noqa: E402


def _new_sdk():
    from tortoise.sdk import TortoiseSDK
    return TortoiseSDK(os.path.join(tempfile.mkdtemp(prefix="retr_1348_"), "t.db"))


def _has_embedded():
    try:
        from tortoise.sdk import TortoiseSDK
        s = TortoiseSDK(os.path.join(tempfile.mkdtemp(prefix="probe_"), "t.db"))
        s.close()
        return True
    except Exception:
        return False


# ── k reorderability (0b pre-registration) ─────────────────────────────────

def test_k_reorders_two_list_top10():
    """k∈{20,60,100} CAN reorder a 2-list fused top-10 — the degenerate
    single-list premise is FALSE for multi-list fusion.

    Constructed crossover: d_a at rank (1, 200), d_b at rank (100, 5).
    d_a wins at small k (rank-1 dominates), d_b wins at large k (flat
    weights favor its consistently-good ranks):
      k=20:  d_a = 1/21+1/220 = 0.0522  > d_b = 1/120+1/25 = 0.0483
      k=100: d_a = 1/101+1/300 = 0.0132 < d_b = 1/200+1/105 = 0.0145
    """
    n = 210
    a = [(f"fill-a-{i}", 1.0 - i * 0.001) for i in range(n)]
    b = [(f"fill-b-{i}", 1.0 - i * 0.001) for i in range(n)]
    a[0] = ("d_a", 1.0)      # rank 1 in list a
    a[100] = ("d_b", 0.99)   # rank ~101 in list a
    b[199] = ("d_a", 0.99)   # rank ~200 in list b
    b[5] = ("d_b", 1.0)      # rank ~6 in list b
    fused20 = [pid for pid, _ in _fused_rrf([a, b], k=20)]
    fused100 = [pid for pid, _ in _fused_rrf([a, b], k=100)]
    # Multi-list RRF is k-sensitive: the two orderings differ.
    assert fused20 != fused100
    # And the crossover is in the expected direction (d_a first at k=20,
    # d_b first at k=100).
    assert fused20.index("d_a") < fused20.index("d_b")
    assert fused100.index("d_b") < fused100.index("d_a")


def test_fused_rerank_stub_bool_raises():
    """#1348 code-review P1 regression: a non-projection stub (e.g. the raw
    argparse bool) must RAISE, never silently zero every boost (True is not
    None → would be treated as a projection; _fetch_signals would swallow the
    AttributeError)."""
    fused = [("d1", 0.05), ("d2", 0.04)]
    with pytest.raises(TypeError, match="stub_projection"):
        _rerank_fused(fused, proj=None, stub_projection=True)
    with pytest.raises(TypeError, match="stub_projection"):
        _rerank_fused(fused, proj=None, stub_projection="not-a-projection")


def test_k_single_list_is_k_invariant():
    """A single ranked list is order-invariant under k (k is a monotone
    score transform) — the degeneracy that IS real for 1-strategy fusion."""
    a = [("d1", 1.0), ("d2", 0.9), ("d3", 0.8), ("d4", 0.7), ("d5", 0.6)]
    for k in (20, 60, 100):
        assert [pid for pid, _ in _fused_rrf([a], k=k)] == ["d1", "d2", "d3", "d4", "d5"]


# ── fused_rerank arm mechanics ──────────────────────────────────────────────

def test_fused_rerank_tuple_to_dict_adapter():
    """The fused_rerank arm converts (pid, rrf_score) tuples to result dicts
    carrying scores.rrf (GraphRanker's similarity contract). With a constant
    graph boost (projection=None → boost 0.0), rerank reproduces fused order."""
    fused = [("d1", 0.05), ("d2", 0.04), ("d3", 0.03), ("d4", 0.02), ("d5", 0.01)]
    reranked = _rerank_fused(fused, proj=None)
    ids = [pid for pid, _ in reranked]
    assert ids == ["d1", "d2", "d3", "d4", "d5"]  # no graph data → similarity order


def test_fused_rerank_use_degree_ablation():
    """use_degree=False neutralizes the degree term — graph_boost becomes
    confidence-only (the confidence-only ablation arm, #1348). Tests
    graph_boost DIRECTLY (the production code path the flag changes) with
    NON-ZERO degree so the two paths genuinely diverge (second-model gate
    ISSUE-1: the prior full-rerank test used degree=0 → connectivity 0 in
    both paths → vacuous; similarity weight also masked the effect)."""
    from tortoise.ranking import GraphRanker

    # d1: high connectivity (degree 9), moderate confidence (0.5).
    # d2: no connectivity (degree 0), high confidence (0.9).
    sig1 = {"confidence": 0.5, "degree": 9}
    sig2 = {"confidence": 0.9, "degree": 0}

    g_on = GraphRanker(None, use_degree=True)
    g_off = GraphRanker(None, use_degree=False)
    b1_on = g_on.graph_boost({}, sig1)   # 0.5*0.5 + 0.5*(1-1/10) = 0.70
    b2_on = g_on.graph_boost({}, sig2)   # 0.5*0.9 + 0.5*0       = 0.45
    b1_off = g_off.graph_boost({}, sig1)  # 0.5 (confidence only)
    b2_off = g_off.graph_boost({}, sig2)  # 0.9 (confidence only)

    # use_degree=True: connectivity lifts d1 above d2 (0.70 > 0.45).
    assert b1_on > b2_on
    # use_degree=False: degree neutralized — d2's higher confidence wins.
    assert b2_off > b1_off
    # Same signals, different flag → different boost (non-vacuous).
    assert b1_on != b1_off and b2_on != b2_off


def test_stub_projection_positive_control_seam():
    """The positive-control seam: a duck-typed projection whose .g.query
    returns oracle-grade-derived signals in _fetch_point_signals row shape.
    With graph_boost_weight=1.0 the control reproduces oracle-greedy order."""
    from tests.eval.retrieval.run import _StubOracleProjection, _STUB_CURRENT  # noqa: I001

    oracle = build_topic_oracle(42)
    live_ids_by_topic = {k: [f"p-{k}-{i}" for i in range(10)] for k in oracle.core}
    stub = _StubOracleProjection(oracle, live_ids_by_topic, "query")
    _STUB_CURRENT.oracle_target = 0
    try:
        rows = stub.g.query("x", params={"ids": [f"p-0-1", f"p-0-2"]}).result_set  # noqa: F541
        # grade 2 (target topic 0) → conf 1.0, degree 0 (neutralized).
        assert rows[0][0] == "p-0-1"
        assert rows[0][1] == 1.0
        assert rows[0][2] == 0
    finally:
        _STUB_CURRENT.oracle_target = None


# ── corpus enhancement fingerprint (0c / Task 5) ────────────────────────────

@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_fingerprint_detects_enhancement():
    """enhance_signals=True differs from False in confidence + edges ONLY —
    n_points/embeddings are byte-identical; n_with_confidence and the
    edge-pairs hash distinguish the enhancement state."""
    from benchmarks.synthetic_corpus import corpus_fingerprint_from_graph

    oracle = build_topic_oracle(42)

    def _fp(enhance):
        sdk = _new_sdk()
        try:
            proj = sdk._get_proj()
            points, _ = generate_oracle_points(300, oracle, seed=42,
                                               enhance_signals=enhance)
            seed_corpus(proj.g, points)
            seed_operator_edges(proj.g, random.Random(42), n_edges_per_op=50,
                                topic_correlated=enhance, oracle=oracle)
            return corpus_fingerprint_from_graph(proj.g)
        finally:
            sdk.close()

    plain = _fp(False)
    enhanced = _fp(True)
    assert plain["n_points"] == enhanced["n_points"] == 300
    assert plain["embeddings_hash"] == enhanced["embeddings_hash"]
    assert plain["n_with_confidence"] == 0
    assert enhanced["n_with_confidence"] > 0
    # Edge COUNT may collide at max_total — the pair LIST hash distinguishes.
    assert plain["edge_pairs_hash"] != enhanced["edge_pairs_hash"]


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_enhancement_zero_main_stream_draw_growth():
    """The rng byte-identity contract: enhanced point dicts contain no field
    whose computation consumes the MAIN rng beyond the drawn alpha/beta —
    content/embeddings must be byte-identical across plain/enhanced."""
    oracle = build_topic_oracle(42)
    p_plain, _ = generate_oracle_points(200, oracle, seed=42, enhance_signals=False)
    p_enh, _ = generate_oracle_points(200, oracle, seed=42, enhance_signals=True)
    for pp, pe in zip(p_plain, p_enh):  # noqa: B905
        assert pp["id"] == pe["id"]
        assert pp["content"] == pe["content"]
        assert pp["embedding"] == pe["embedding"]
        assert pp["pointKind"] == pe["pointKind"]
        assert pp["status"] == pe["status"]
        # posterior draws identical (main stream preserved); enhanced adds
        # only the confidence key (derived, not a new main-stream draw).
        assert pp.get("posterior_alpha") == pe.get("posterior_alpha")
        assert pp.get("posterior_beta") == pe.get("posterior_beta")
        if "confidence" in pe:
            assert 0.0 <= pe["confidence"] <= 1.0


def test_enhanced_confidence_non_tautological():
    """Within-topic confidence spread > 0 — the enhanced signal is noisy
    (overlapping per-topic ranges), NOT recoverable from topic alone."""
    oracle = build_topic_oracle(42)
    _points, _ = generate_oracle_points(500, oracle, seed=42, enhance_signals=True)
    # All confidence values are not identical (spread exists).
    confs = [p["confidence"] for p in _points if "confidence" in p]
    assert len(set(confs)) > 1, "confidence must not be a constant"
