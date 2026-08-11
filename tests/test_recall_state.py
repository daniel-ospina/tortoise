"""Tests for UC1 "state" recall (#898 Wave A) — multiplicative confidence gate.

Unit coverage: StateRanker math (golden-set ordering, centrality weak-boost
bound, multiplicative gate, neutral fallback for uncalibrated points, tunable
a/b/w_c params, contestation never demotes).

Integration coverage (embedded FalkorDBLite): recall_state golden set
(well-supported > low-support > contradicted-flagged), contested surfacing
with counter-evidence, state filter (superseded/deprecated excluded by
default; include_superseded brings them back; retracted always excluded),
object-centric ranking, MCP tortoise_recall mode wiring.

Regression: existing GraphRanker (order_by=graph) and default RRF paths are
unchanged — covered by the existing test_ranking.py / test_search_engine.py
suites (run alongside).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.ranking import (  # noqa: E402
    StateRanker, NEUTRAL_CONFIDENCE,
    DEFAULT_RELEVANCE_EXP, DEFAULT_CONFIDENCE_EXP, DEFAULT_CENTRALITY_WEIGHT,
)
from tortoise.sdk import TortoiseSDK  # noqa: E402


# ── Unit: StateRanker math (no DB) ─────────────────────────────────────────

def _ranked(results, **kw):
    return {r["id"]: r["recall_ranking"] for r in StateRanker().rerank(results, **kw)}


def test_golden_set_well_supported_outranks_low_support_and_contradicted():
    """Golden set (unit): equally relevant claims separate by epistemic
    confidence — well-supported (posterior 0.923) > low-support (0.667) >
    contradicted (0.5, contested). The contested claim is NOT buried and is
    flagged."""
    results = [
        {"id": "well", "scores": {"rrf": 0.05}, "state_confidence": 12 / 13,
         "state_has_ep": True, "state_degree": 3},
        {"id": "low", "scores": {"rrf": 0.05}, "state_confidence": 2 / 3,
         "state_has_ep": True, "state_degree": 1},
        {"id": "cont", "scores": {"rrf": 0.05}, "state_confidence": 0.5,
         "state_has_ep": True, "state_degree": 2,
         "state_variance": 0.05, "state_contested": True},
    ]
    out = StateRanker().rerank(results)
    ids = [r["id"] for r in out]
    assert ids[0] == "well"
    assert ids.index("low") < ids.index("cont")  # low-support not buried either
    by_id = _ranked(results)
    assert by_id["cont"]["contested"] is True
    assert by_id["well"]["confidence"] > by_id["low"]["confidence"] > by_id["cont"]["confidence"]


def test_contested_never_demotes():
    """Contestation is a FLAG, never a score input: identical confidence +
    relevance → identical final score whether contested or not."""
    ranker = StateRanker()
    clean = ranker.rerank([{"id": "a", "scores": {"rrf": 0.05}, "state_confidence": 0.9,
                            "state_has_ep": True, "state_variance": 0.01, "state_contested": False}])
    flagged = ranker.rerank([{"id": "b", "scores": {"rrf": 0.05}, "state_confidence": 0.9,
                              "state_has_ep": True, "state_variance": 0.05, "state_contested": True}])
    assert clean[0]["recall_ranking"]["final_score"] == flagged[0]["recall_ranking"]["final_score"]
    assert flagged[0]["recall_ranking"]["contested"] is True


def test_centrality_weak_boost_never_outranks_confidence():
    """A high-centrality low-confidence point must NOT outrank a
    higher-confidence point (the whole centrality boost is at most +10% —
    subordinate to the multiplicative confidence term)."""
    out = StateRanker().rerank([
        {"id": "hiconf", "scores": {"rrf": 0.05}, "state_confidence": 0.9,
         "state_has_ep": True, "state_degree": 0},
        {"id": "hicent", "scores": {"rrf": 0.05}, "state_confidence": 0.6,
         "state_has_ep": True, "state_degree": 8},
    ])
    assert out[0]["id"] == "hiconf"
    assert out[0]["recall_ranking"]["final_score"] > out[1]["recall_ranking"]["final_score"]
    # Bound: centrality multiplier never exceeds (1 + w_c).
    assert out[1]["recall_ranking"]["final_score"] <= 0.5 * 0.6 * (1 + DEFAULT_CENTRALITY_WEIGHT) + 1e-9


def test_multiplicative_gate_relevance_alone_cannot_rescue_low_confidence():
    """The gate is multiplicative: top relevance × 0.05 confidence loses to
    mid relevance × 0.9 confidence (an additive ranker would do the reverse)."""
    out = StateRanker().rerank([
        {"id": "rel_top_conf_lo", "scores": {"rrf": 0.06}, "state_confidence": 0.05,
         "state_has_ep": True},
        {"id": "rel_mid_conf_hi", "scores": {"rrf": 0.055}, "state_confidence": 0.9,
         "state_has_ep": True},
        {"id": "rel_bot_conf_hi", "scores": {"rrf": 0.05}, "state_confidence": 0.9,
         "state_has_ep": True},
    ])
    assert out[0]["id"] == "rel_mid_conf_hi"


def test_zero_confidence_hard_suppresses():
    """A zero-confidence claim scores 0 regardless of relevance (multiplicative)."""
    out = StateRanker().rerank([
        {"id": "zero", "scores": {"rrf": 0.06}, "state_confidence": 0.0, "state_has_ep": True},
        {"id": "pos", "scores": {"rrf": 0.055}, "state_confidence": 0.8, "state_has_ep": True},
        {"id": "fill", "scores": {"rrf": 0.05}, "state_confidence": 0.9, "state_has_ep": True},
    ])
    by_id = {r["id"]: r["recall_ranking"] for r in out}
    # Zero confidence → 0.0 even at the TOP relevance (the whole point of
    # the multiplicative gate); a positive-confidence claim outranks it.
    assert by_id["zero"]["relevance_norm"] == 1.0
    assert by_id["zero"]["final_score"] == 0.0
    assert out[0]["id"] == "pos"


def test_uncalibrated_points_fall_back_to_neutral():
    """Uncalibrated points (no persisted α/β) get the documented neutral 0.5
    (Beta(1,1) prior mean) — absence of measurement is NOT low support."""
    out = StateRanker().rerank([{"id": "u", "scores": {"rrf": 0.05}}])
    rr = out[0]["recall_ranking"]
    assert rr["confidence"] == NEUTRAL_CONFIDENCE
    assert rr["confidence_source"] == "neutral"


def test_all_equal_degree_gets_no_centrality_boost():
    """All-equal degree → centrality_norm 0.0 (no differentiation → no boost),
    unlike relevance's midpoint convention."""
    out = StateRanker().rerank([
        {"id": "a", "scores": {"rrf": 0.05}, "state_confidence": 0.8, "state_has_ep": True},
        {"id": "b", "scores": {"rrf": 0.05}, "state_confidence": 0.7, "state_has_ep": True},
    ])
    assert out[0]["recall_ranking"]["centrality_norm"] == 0.0
    assert out[0]["recall_ranking"]["final_score"] == out[0]["recall_ranking"]["base_score"]


def test_state_ranker_params_tunable():
    """a/b/w_c are constructor params: confidence_exp=2 stretches the
    confidence gap; centrality_weight=0 removes the boost entirely."""
    results = [
        {"id": "hi", "scores": {"rrf": 0.05}, "state_confidence": 0.9, "state_has_ep": True, "state_degree": 0},
        {"id": "lo", "scores": {"rrf": 0.05}, "state_confidence": 0.5, "state_has_ep": True, "state_degree": 0},
    ]
    plain = StateRanker().rerank(results)
    squared = StateRanker(confidence_exp=2.0).rerank(results)
    ratio_plain = plain[0]["recall_ranking"]["base_score"] / plain[1]["recall_ranking"]["base_score"]
    ratio_squared = squared[0]["recall_ranking"]["base_score"] / squared[1]["recall_ranking"]["base_score"]
    assert ratio_squared > ratio_plain  # 0.9²/0.5² > 0.9/0.5
    # centrality_weight=0 → boost factor exactly 1.0.
    zero_cent = StateRanker(centrality_weight=0.0).rerank([
        {"id": "a", "scores": {"rrf": 0.05}, "state_confidence": 0.8, "state_has_ep": True, "state_degree": 5},
        {"id": "b", "scores": {"rrf": 0.05}, "state_confidence": 0.6, "state_has_ep": True, "state_degree": 0},
    ])
    assert zero_cent[0]["recall_ranking"]["final_score"] == zero_cent[0]["recall_ranking"]["base_score"]


def test_state_ranker_param_validation():
    with pytest.raises(ValueError):
        StateRanker(relevance_exp=0.0)
    with pytest.raises(ValueError):
        StateRanker(confidence_exp=-1.0)
    with pytest.raises(ValueError):
        StateRanker(centrality_weight=1.5)


def test_state_ranker_inputs_not_mutated():
    results = [{"id": "a", "scores": {"rrf": 0.03}, "state_confidence": 0.8, "state_has_ep": True}]
    original = dict(results[0])
    StateRanker().rerank(results)
    assert results[0] == original


# ── Integration: embedded FalkorDBLite ─────────────────────────────────────

@pytest.fixture(autouse=True)
def _use_shared_embedded_db(shared_embedded_db):
    pass


def _fresh_sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_recall_state_"), "test.db")
    sdk = TortoiseSDK(db_path)
    try:
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    return sdk


def _set_posterior(sdk, pid: str, alpha: float, beta: float):
    """Persist EP posterior params the way compute_confidence does (n.confidence
    = posterior mean α/(α+β); posterior_alpha/beta for variance/contested)."""
    mean = round(alpha / (alpha + beta), 4) if (alpha + beta) > 0 else 0.5
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.confidence = $c, "
        "n.posterior_alpha = $a, n.posterior_beta = $b",
        params={"id": pid, "a": alpha, "b": beta, "c": mean},
    )


def _build_golden_graph(sdk):
    """(i) well-supported: 2 IMPL operators, posterior 12/1 ≈ 0.923.
    (ii) low-support: 1 IMPL operator, posterior 2/1 ≈ 0.667.
    (iii) contradicted: 1 IMPL + 1 NAND operator, posterior 2/2 = 0.5
    (variance 0.05 → contested). Evidence sources use pointKind="evidence"
    so the kind="statement" full-scan retrieves ONLY the three claims
    (equally relevant). Returns dict of {role: point_id}."""
    ev = [sdk.create_point("evidence", f"golden evidence source {i}") for i in range(3)]
    well = sdk.create_point("statement", "zephyr launch date is q3 2026")
    low = sdk.create_point("statement", "zephyr launch delayed by supply chain")
    cont = sdk.create_point("statement", "zephyr launch cancelled outright")
    # well-supported: two supporting IMPL operators.
    sdk.create_operator("IMPL", ev[0]["id"], [well["id"]])
    sdk.create_operator("IMPL", ev[1]["id"], [well["id"]])
    # low-support: one IMPL operator.
    sdk.create_operator("IMPL", ev[2]["id"], [low["id"]])
    # contradicted: one IMPL (support) + one NAND (contradiction).
    sdk.create_operator("IMPL", ev[0]["id"], [cont["id"]])
    sdk.create_operator("NAND", ev[1]["id"], [cont["id"]])
    _set_posterior(sdk, well["id"], 12.0, 1.0)
    _set_posterior(sdk, low["id"], 2.0, 1.0)
    _set_posterior(sdk, cont["id"], 2.0, 2.0)
    return {"well": well["id"], "low": low["id"], "cont": cont["id"]}


def test_golden_set_integration():
    """Golden set end-to-end: full-scan retrieval of the three equally-relevant
    claims → multiplicative gate ranks well-supported first, low-support below,
    and the contradicted claim surfaces (not buried) with contested:true."""
    sdk = _fresh_sdk()
    try:
        g = _build_golden_graph(sdk)
        results = sdk.recall_state(kind="statement", limit=10)
        ids = [r["id"] for r in results]
        assert g["well"] in ids and g["low"] in ids and g["cont"] in ids
        assert ids[0] == g["well"]
        assert ids.index(g["low"]) < ids.index(g["cont"])  # low-support above contradicted
        by_id = {r["id"]: r for r in results}
        # Contested surfacing: flag + not demoted (same formula as uncontested).
        assert by_id[g["cont"]]["contested"] is True
        assert by_id[g["cont"]]["ep"]["contested"] is True
        rr = by_id[g["cont"]]["recall_ranking"]
        assert rr["confidence_source"] == "posterior"
        # Well-supported strictly outranks low-support on the gate score.
        assert by_id[g["well"]]["recall_ranking"]["final_score"] > \
               by_id[g["low"]]["recall_ranking"]["final_score"] > \
               by_id[g["cont"]]["recall_ranking"]["final_score"]
    finally:
        sdk.close()


def test_contested_surfaces_with_counter_evidence():
    """A contradicted claim with significant support is returned with
    contested:true AND the NANDing point attached as counter-evidence — it is
    surfaced, not buried."""
    sdk = _fresh_sdk()
    try:
        g = _build_golden_graph(sdk)
        results = sdk.recall_state(kind="statement", limit=10)
        by_id = {r["id"]: r for r in results}
        cont = by_id[g["cont"]]
        assert cont["contested"] is True
        assert cont.get("counter_evidence"), "counter-evidence must be attached"
        # The NAND operator that contradicts it is present in counter-evidence.
        assert any(ce["is_operator"] for ce in cont["counter_evidence"])
        assert any(ce["content"] or ce["id"] for ce in cont["counter_evidence"])
        # High-contention NAND surfaced too.
        assert any(n["mechanism"] == "NAND" for n in cont.get("nands", []))
        # Arguments (IMPL operators) attached to the well-supported claim.
        assert any(a["mechanism"] == "IMPL" for a in by_id[g["well"]].get("arguments", []))
    finally:
        sdk.close()


def test_state_filter_excludes_superseded_deprecated():
    """UC1 state semantics: superseded/deprecated claims are excluded by
    default; include_superseded=True brings them back. Retracted stays
    excluded either way (#689)."""
    sdk = _fresh_sdk()
    try:
        live = sdk.create_point("statement", "nova engine coolant spec is q3 target")
        superseded = sdk.create_point("statement", "nova engine coolant spec superseded old")
        deprecated = sdk.create_point("statement", "nova engine coolant deprecated old")
        retracted = sdk.create_point("statement", "nova engine coolant retracted old")
        proj = sdk._get_proj()
        proj.g.query("MATCH (n:Point {id:$id}) SET n.status = 'superseded'",
                     params={"id": superseded["id"]})
        proj.g.query("MATCH (n:Point {id:$id}) SET n.status = 'deprecated'",
                     params={"id": deprecated["id"]})
        proj.g.query("MATCH (n:Point {id:$id}) SET n.status = 'retracted'",
                     params={"id": retracted["id"]})

        default_ids = {r["id"] for r in sdk.recall_state(kind="statement", limit=10)}
        assert live["id"] in default_ids
        assert superseded["id"] not in default_ids
        assert deprecated["id"] not in default_ids
        assert retracted["id"] not in default_ids

        include_ids = {r["id"] for r in sdk.recall_state(kind="statement", limit=10,
                                                         include_superseded=True)}
        assert superseded["id"] in include_ids
        assert deprecated["id"] in include_ids
        assert retracted["id"] not in include_ids  # retracted is hard-excluded (#689)
    finally:
        sdk.close()


def test_object_centric_ranking():
    """Objects and the Points about them are ranked together; the object's
    confidence is the mean EP posterior of its about-Points."""
    sdk = _fresh_sdk()
    try:
        obj = sdk.create_object("zephyr", "product")
        p_hi = sdk.create_point("statement", "zephyr supports orbital refueling")
        p_lo = sdk.create_point("statement", "zephyr supports crew rotation")
        _set_posterior(sdk, p_hi["id"], 10.0, 1.0)  # ≈0.909
        _set_posterior(sdk, p_lo["id"], 1.0, 1.0)   # 0.5 neutral-ish
        proj = sdk._get_proj()
        proj.create_about_edge(p_hi["id"], obj["id"], "aboutObject")
        proj.create_about_edge(p_lo["id"], obj["id"], "aboutObject")

        results = sdk.recall_state(kind="product", limit=10, object_centric=True)
        obj_ids = [r["id"] for r in results if r.get("entity_type") == "object"]
        assert obj["id"] in obj_ids
        obj_res = next(r for r in results if r["id"] == obj["id"])
        assert obj_res["entity_type"] == "object"
        # Object confidence = mean posterior of about-points ≈ (0.909+0.5)/2.
        assert obj_res["recall_ranking"]["confidence"] == pytest.approx(
            (10.0 / 11.0 + 0.5) / 2.0, abs=1e-3)
        # Related points attached (object-centric surfacing).
        rp = {p["id"]: p for p in obj_res.get("related_points", [])}
        assert p_hi["id"] in rp and p_lo["id"] in rp
        # object_centric=False → no objects in the pool.
        no_obj = sdk.recall_state(kind="product", limit=10, object_centric=False)
        assert not any(r.get("entity_type") == "object" for r in no_obj)
    finally:
        sdk.close()


def test_recall_state_default_rrf_and_graph_paths_untouched():
    """Regression anchor: recall_state is a NEW path — the default RRF path
    carries no recall_ranking annotation, and order_by='graph' still works
    (GraphRanker)."""
    sdk = _fresh_sdk()
    try:
        p1 = sdk.create_point("statement", "orca echolocation frequency range unique analysis")
        p2 = sdk.create_point("statement", "orca echolocation unique frequency analysis range")
        _set_posterior(sdk, p1["id"], 9.0, 1.0)
        _set_posterior(sdk, p2["id"], 1.0, 9.0)
        # Default path unchanged: no recall_ranking key, plain RRF order.
        plain = sdk.tortoise_fts_query("orca echolocation", limit=10)
        assert plain, "seeded points must be retrievable by the default path"
        assert "recall_ranking" not in plain[0]
        assert "graph_ranking" not in plain[0]
        # Graph path unchanged: still annotates graph_ranking and ranks by EP.
        graph = sdk.tortoise_fts_query("orca echolocation", limit=10, order_by="graph")
        assert "graph_ranking" in graph[0]
        by_id = {r["id"]: r for r in graph}
        assert by_id[p1["id"]]["graph_ranking"]["final_score"] > \
               by_id[p2["id"]]["graph_ranking"]["final_score"]
    finally:
        sdk.close()


# ── MCP tool wiring (epic #898) ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _transport_context():
    """MCP tools require an initialized transport mode (#236 auth gate)."""
    from tortoise.mcp_auth import (
        _current_team_id, _current_team_limits, _transport_mode,
    )
    _transport_mode.set("stdio")
    _current_team_id.set(None)
    _current_team_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_team_id.set(None)
    _current_team_limits.set(None)


def test_tortoise_recall_mode_state_shape():
    from tortoise.mcp_server import tortoise_recall
    result = tortoise_recall("nova engine", limit=5)
    assert isinstance(result, dict)
    assert result["mode"] == "state"
    assert isinstance(result["results"], list)


def test_tortoise_recall_scaffolds_wave_b_modes():
    """gaps/subgraph are Wave B — scaffolded with a clear error, not silent."""
    from tortoise.mcp_server import tortoise_recall
    for mode in ("gaps", "subgraph"):
        result = tortoise_recall("x", mode=mode)
        assert result["mode"] == mode
        assert "error" in result and "Wave B" in result["error"]


def test_tortoise_recall_state_returns_ranked_results():
    """MCP surface end-to-end: tortoise_recall(mode='state') returns the
    golden-set ranking with contested surfacing (module-level SDK swap pattern,
    tests/test_enumeration_surfaces.py:310)."""
    from tortoise import mcp_server as mcp_mod
    from tortoise.mcp_server import tortoise_recall
    sdk = _fresh_sdk()
    orig_sdk = mcp_mod.sdk
    mcp_mod.sdk = sdk
    try:
        g = _build_golden_graph(sdk)
        result = tortoise_recall(kind="statement", limit=10)
        assert result["mode"] == "state"
        ids = [r["id"] for r in result["results"]]
        assert g["well"] in ids and g["low"] in ids and g["cont"] in ids
        assert ids[0] == g["well"]
        cont = next(r for r in result["results"] if r["id"] == g["cont"])
        assert cont["contested"] is True
    finally:
        mcp_mod.sdk = orig_sdk
        sdk.close()
