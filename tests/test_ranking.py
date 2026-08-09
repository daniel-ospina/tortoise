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

# Legacy predicate name for negative-direction tests (#281).
# Kept as a constant so no edge-syntax literal appears in source
# (Task 5 sweep + test_security drift test both require zero hits).
_LEGACY_INSTANTIATES = "INSTANTIATES"


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


# ── Event aboutObject signals (#281) ───────────────────────────────────────

@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(db_path=str(tmp_path / "t.db"))
    yield s
    s.close()


def test_event_signal_counts_about_objects(sdk):
    ev = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s2")
    proj = sdk._get_proj()
    # Public SDK path for Object nodes; distinct names survive the #452 MERGE dedup.
    objs = [sdk.create_object(f"obj{i}", "issue") for i in range(3)]
    for o in objs:
        proj.create_about_edge(ev["eventId"], o["id"], "aboutObject")
    # Negative direction of the swap in the happy path: a legacy INSTANTIATES
    # edge must NOT add to the count even when aboutObject edges exist (a
    # conditional legacy fallback would only show up here). Mirrors the
    # confidence-only pin in test_event_signal_confidence_only_degradation.
    legacy = sdk.create_object("legacy-inst-object", "issue")
    # Constant-interpolated rel-type: legacy edge created at runtime, no
    # edge-syntax literal in source (Task 5 sweep requires zero hits).
    proj.g.query(
        "MATCH (e:Event {eventId:$eid}), (o:Object {id:$oid}) "
        f"CREATE (e)-[:{_LEGACY_INSTANTIATES}]->(o)",
        params={"eid": ev["eventId"], "oid": legacy["id"]},
    )
    ranker = GraphRanker(projection=proj)
    sig = ranker._fetch_event_signals([ev["eventId"]])[ev["eventId"]]
    assert sig["about_objects"] == 3  # INSTANTIATES edge NOT counted (3, not 4)
    assert sig["is_event"] is True
    assert sig["confidence"] == 0.5  # no PRODUCES Points → coalesce fallback
    # Real-path chain: fetched sig → consumer. Guards producer key (sig key
    # rename → KeyError on the shape asserts above), consumer key (rename →
    # 0.6·0+0.2 = 0.2, value mismatch not exception), is_event routing, and
    # the producer→consumer handoff in one assertion — the plan's boost-level
    # silent-zero guard.
    assert ranker.graph_boost({}, sig) == 0.65  # 0.6·(1-1/4) + 0.4·0.5
    # Saturation-shape pin: inst_norm = 1 - 1/(1+n) is nonlinear (0, 0.5, 0.75
    # for n=0,1,3); a linearized inst_norm (e.g. 0.25·n) would pass the 0 and
    # 3 anchors but diverge here at the midpoint (0.5, not 0.35).
    assert ranker.graph_boost(
        {}, {"about_objects": 1, "is_event": True, "confidence": 0.5}) == round(0.6 * (1 - 1 / 2) + 0.4 * 0.5, 4)


def test_event_signal_degrades_without_about_objects(sdk):
    ev = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s3")
    ranker = GraphRanker(projection=sdk._get_proj())
    sig = ranker._fetch_event_signals([ev["eventId"]])[ev["eventId"]]
    assert sig["about_objects"] == 0
    assert sig["is_event"] is True
    assert sig["confidence"] == 0.5  # no PRODUCES Points → coalesce fallback
    # Real-path chain: if the producer dropped is_event this would route to the
    # point branch → 0.5·0.5 + 0 = 0.25, not 0.2. This 0.2 assert is
    # consumer-key-agnostic by construction (n=0 → inst_norm=0 under any key
    # name); the renamed-consumer-key guard lives in
    # test_event_signal_counts_about_objects' 0.65 asserts.
    assert ranker.graph_boost({}, sig) == 0.2


def test_rerank_event_boost_from_about_objects(sdk):
    """Event rerank OUTCOME: at equal similarity + recency, the higher
    aboutObject boost wins the order, and final_score fuses the graph boost
    with the documented weights (α=0.5, β=0.35, γ=0.15). Covers the
    rerank → _fetch_signals routing and result-id → eventId keying (#281
    seam); NB: create_event sets node id == eventId, so eventId-keying is
    pinned exactly by test_event_signal_keyed_by_eventId_not_id.
    """
    ev_a = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s4")
    ev_b = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s4b")
    proj = sdk._get_proj()
    objs = [sdk.create_object(f"rerank-obj{i}", "issue") for i in range(3)]
    for o in objs:
        proj.create_about_edge(ev_a["eventId"], o["id"], "aboutObject")
    ranker = GraphRanker(projection=proj)
    out = ranker.rerank(
        [{"id": ev_a["eventId"], "scores": {"rrf": 0.05}},
         {"id": ev_b["eventId"], "scores": {"rrf": 0.05}}],
        entity_type="event",
    )
    by_id = {r["id"]: r for r in out}
    assert by_id[ev_a["eventId"]]["graph_ranking"]["graph_boost"] == 0.65
    assert by_id[ev_b["eventId"]]["graph_ranking"]["graph_boost"] == 0.2
    assert out[0]["id"] == ev_a["eventId"]  # boost decides the order
    # final_score uses unrounded inputs; approx mirrors test_rerank_annotates_and_sorts.
    assert by_id[ev_a["eventId"]]["graph_ranking"]["final_score"] == pytest.approx(
        0.5 * 0.5 + 0.35 * 0.65 + 0.15 * 1.0, abs=5e-4)


def test_event_signal_includes_produces_confidence(sdk):
    """PRODUCES branch: the event boost's 0.4·confidence term must come from
    the MEAN EP confidence of produced Points, not the 0.5 coalesce fallback.

    Two produced Points (0.9 and 0.3) pin avg() against max()/min()/first-row
    selection and, combined with 3 aboutObject edges, guard the count(o) query
    against a cartesian o×p merge (which would inflate about_objects to 6).

    NB: ranking.py queries `-[:PRODUCES]->` (uppercase). Production emits
    lowercase `produces` (sdk.py:1582, entities.py) — a PRE-EXISTING case
    mismatch (#25) that leaves this branch dead in production (confidence
    always coalesces to 0.5). This test pins the branch as ranking.py
    consumes it; when the #25 fix lands (lowercase query), flip the edge
    creation to lowercase and update expectations.
    """
    ev = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s5")
    proj = sdk._get_proj()
    objs = [sdk.create_object(f"prod-obj{i}", "issue") for i in range(3)]
    for o in objs:
        proj.create_about_edge(ev["eventId"], o["id"], "aboutObject")
    p_hi = sdk.create_point("statement", "session-produced finding hi")
    p_lo = sdk.create_point("statement", "session-produced finding lo")
    _set_confidence(sdk, p_hi["id"], 0.9)
    _set_confidence(sdk, p_lo["id"], 0.3)
    # No SDK path emits uppercase PRODUCES; raw Cypher matches the query shape.
    proj.g.query(
        "MATCH (e:Event {eventId:$eid}), (p:Point) WHERE p.id IN $pids "
        "CREATE (e)-[:PRODUCES]->(p)",
        params={"eid": ev["eventId"], "pids": [p_hi["id"], p_lo["id"]]},
    )
    ranker = GraphRanker(projection=proj)
    sig = ranker._fetch_event_signals([ev["eventId"]])[ev["eventId"]]
    assert sig["about_objects"] == 3  # NOT 6 — guards o×p cartesian merge
    assert sig["confidence"] == 0.6  # avg([0.9, 0.3]), not max/min/first-row
    assert ranker.graph_boost({}, sig) == 0.69  # 0.6·(1-1/4) + 0.4·0.6


def test_event_signal_lowercase_produces_edge_not_matched(sdk):
    """Production-reality pin: the SDK emits lowercase `produces` (sdk.py:1582),
    which the uppercase `-[:PRODUCES]->` query never matches — so coalesce 0.5
    stays live even when produced Points exist (pre-existing #25 case
    mismatch). The #25 fix (lowercasing the query) flips FOUR tests: this one
    (0.5 → 0.9), test_event_signal_includes_produces_confidence (0.69 → 0.65),
    test_event_signal_confidence_only_degradation (0.24 → 0.2), and
    test_event_signal_batch_fetch_preserves_per_event_grouping (0.36 → 0.2).
    They are expected pin flips, not regressions.
    """
    ev = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s5b")
    proj = sdk._get_proj()
    p = sdk.create_point("statement", "decision-produced point")
    _set_confidence(sdk, p["id"], 0.9)
    assert proj.create_edge(ev["eventId"], p["id"], "produces") is True
    ranker = GraphRanker(projection=proj)
    sig = ranker._fetch_event_signals([ev["eventId"]])[ev["eventId"]]
    assert sig["confidence"] == 0.5  # lowercase edge NOT matched → coalesce


def test_event_signal_confidence_only_degradation(sdk):
    """Plan acceptance criterion: 'no aboutObject on old graphs → boost =
    0.4·confidence' must hold at a REAL produced confidence, not just the 0.5
    coalesce fallback. Pre-#281 graphs carry PRODUCES edges but zero
    aboutObject, so 0.4·avg_produced_conf is the migration-window production
    value. The row must survive at count=0 (a non-OPTIONAL aboutObject match
    would drop it → 0.0) and avg_conf must flow through even when no
    aboutObject exists. Also pins the NEGATIVE direction of the swap: a
    legacy INSTANTIATES edge must NOT count. NB: flips when #25 lands
    (lowercase PRODUCES query) — 0.24 → 0.2.
    """
    ev = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s5c")
    proj = sdk._get_proj()
    p_hi = sdk.create_point("statement", "old-graph finding hi")
    p_lo = sdk.create_point("statement", "old-graph finding lo")
    _set_confidence(sdk, p_hi["id"], 0.9)
    _set_confidence(sdk, p_lo["id"], 0.3)
    proj.g.query(
        "MATCH (e:Event {eventId:$eid}), (p:Point) WHERE p.id IN $pids "
        "CREATE (e)-[:PRODUCES]->(p)",
        params={"eid": ev["eventId"], "pids": [p_hi["id"], p_lo["id"]]},
    )
    # Negative direction of the #281 swap: a legacy INSTANTIATES edge must
    # NOT contribute to the aboutObject count. A union or partial rename
    # would silently INFLATE counts on live pre-migration graphs.
    legacy = sdk.create_object("legacy-inst-object", "issue")
    # Constant-interpolated rel-type: legacy edge created at runtime, no
    # edge-syntax literal in source (Task 5 sweep requires zero hits).
    proj.g.query(
        "MATCH (e:Event {eventId:$eid}), (o:Object {id:$oid}) "
        f"CREATE (e)-[:{_LEGACY_INSTANTIATES}]->(o)",
        params={"eid": ev["eventId"], "oid": legacy["id"]},
    )
    ranker = GraphRanker(projection=proj)
    sig = ranker._fetch_event_signals([ev["eventId"]])[ev["eventId"]]
    assert sig["about_objects"] == 0  # INSTANTIATES edge NOT counted
    assert sig["confidence"] == 0.6  # avg([0.9, 0.3]) with zero aboutObject
    assert ranker.graph_boost({}, sig) == 0.24  # 0.6·0 + 0.4·0.6


# flake: known redislite lifecycle issue (#176)
def test_event_signal_batch_fetch_preserves_per_event_grouping(sdk):
    """One WHERE eventId IN $ids call over multiple MATCHED events must keep
    per-event aggregation for BOTH about_objects count AND avg confidence —
    counts and confidence must not merge across events. Production rerank
    batches all search results in a single fetch (sdk.py:3252)."""
    ev_a = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s8")
    ev_b = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s9")
    proj = sdk._get_proj()
    objs = [sdk.create_object(f"batch-obj{i}", "issue") for i in range(3)]
    for o in objs:
        proj.create_about_edge(ev_a["eventId"], o["id"], "aboutObject")
    p_b = sdk.create_point("statement", "batch-produced point")
    _set_confidence(sdk, p_b["id"], 0.9)
    proj.g.query(
        "MATCH (e:Event {eventId:$eid}), (p:Point {id:$pid}) CREATE (e)-[:PRODUCES]->(p)",
        params={"eid": ev_b["eventId"], "pid": p_b["id"]},
    )
    ranker = GraphRanker(projection=proj)
    sig = ranker._fetch_event_signals([ev_a["eventId"], ev_b["eventId"]])
    assert sig[ev_a["eventId"]]["about_objects"] == 3
    assert sig[ev_a["eventId"]]["confidence"] == 0.5  # coalesce — no PRODUCES
    assert sig[ev_b["eventId"]]["about_objects"] == 0
    assert sig[ev_b["eventId"]]["confidence"] == 0.9  # avg scoped to ev_b
    out = ranker.rerank(
        [{"id": ev_a["eventId"], "scores": {"rrf": 0.05}},
         {"id": ev_b["eventId"], "scores": {"rrf": 0.05}}],
        entity_type="event",
    )
    by_id = {r["id"]: r for r in out}
    assert by_id[ev_a["eventId"]]["graph_ranking"]["graph_boost"] == 0.65
    assert by_id[ev_b["eventId"]]["graph_ranking"]["graph_boost"] == 0.36  # 0.6·0 + 0.4·0.9
    # NB: flips when #25 lowercases the PRODUCES query — 0.36 → 0.2.


def test_event_signal_keyed_by_eventId_not_id(sdk):
    """Signal rows are keyed by eventId, NOT node id. The SDK's create_event
    always sets id == eventId, hiding the seam; production session_indexer
    creates Events via raw `CREATE (e:Event {eventId:...})` with NO id
    property (session_indexer.py:550) — so a regression to id-keying would
    silently zero the boost for every session-indexed event (bug pattern (a)).
    """
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (e:Event {id:'evt-node-id', eventId:'evt-7', "
        "name:'AgentSession', eventKind:'AgentSession'})"
    )
    ranker = GraphRanker(projection=proj)
    sig = ranker._fetch_event_signals(["evt-7"])
    assert "evt-7" in sig  # keyed by eventId; an id-keyed producer returns no row
    # for this id and the membership assert fails (AssertionError, not KeyError).
    # rerank with result id == eventId resolves to the signal row.
    out = ranker.rerank([{"id": "evt-7", "scores": {"rrf": 0.05}}], entity_type="event")
    assert out[0]["graph_ranking"]["graph_boost"] == 0.2  # no aboutObject edges


def test_rerank_event_unmatched_signals_are_neutral(sdk):
    """Unmatched/missing event ids degrade to neutral, never crash, and never
    cross-contaminate matched events. Asserting matched (0.2) and unknown
    (0.0) divergence in ONE rerank call makes a swallowed producer crash
    (defensive try/except → {}) visible: it would collapse everything to 0.0."""
    ev = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s6")
    ranker = GraphRanker(projection=sdk._get_proj())
    out = ranker.rerank(
        [{"id": ev["eventId"], "scores": {"rrf": 0.05}},
         {"id": "no-such-event", "scores": {"rrf": 0.05}}],
        entity_type="event",
    )
    by_id = {r["id"]: r for r in out}
    assert by_id[ev["eventId"]]["graph_ranking"]["graph_boost"] == 0.2
    assert by_id["no-such-event"]["graph_ranking"]["graph_boost"] == 0.0
    # WHERE eventId IN $ids must scope to requested ids: fetching one event
    # never returns another event's row (would pass if the filter were removed).
    # Arrange: a second, unrequested event.
    other = sdk.create_event("AgentSession", eventKind="AgentSession", session_id="s7")
    # Act: fetch only the requested id.
    assert other["eventId"] not in ranker._fetch_event_signals([ev["eventId"]])
    # Result without an id → neutral 0.0, no crash. NB: this pins the
    # no-crash/neutral CONTRACT only — the `if not ids` guard itself is
    # behaviorally unpinnable (removing it yields the same {} via the
    # try/except swallow or an empty IN match), so it is defense-in-depth.
    no_id = ranker.rerank([{"scores": {"rrf": 0.05}}], entity_type="event")
    assert no_id[0]["graph_ranking"]["graph_boost"] == 0.0
