"""Tests for UC2 "gaps" recall + UC3 "subgraph" recall (#898 Wave B).

Unit coverage: GapsRanker math (load/support formula, zero-load is not a gap,
high support collapses the score, embedded-signal contract, input immutability)
and SubgraphExpander validation + empty-seed behavior.

Integration coverage (embedded FalkorDBLite): gaps golden set (a
load-bearing-but-unsourced claim IS returned; a well-supported claim is NOT;
an isolated no-load claim is NOT), the reification rule (operator-less direct
IMPL/NAND edges count as load/support), topic-scoped gaps, subgraph
completeness (seeded topic returns the connected subgraph with nodes + edges,
completeness over precision), depth + completeness=core behavior, seed-by-id
and seed-by-topic resolution.

MCP wiring: tortoise_recall mode routing — state/gaps/subgraph/custom all
route correctly; invalid mode errors; missing seed errors.

Regression: existing recall(mode='state') tests still pass (run
tests/test_recall_state.py alongside) and default RRF/GraphRanker paths are
untouched (tests/test_ranking.py).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.ranking import (  # noqa: E402, I001, RUF100
    GapsRanker, SubgraphExpander, NEUTRAL_CONFIDENCE,
)
from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100


# ── Unit: GapsRanker math (no DB) ─────────────────────────────────────────

def _gapped(results, **kw):
    return {r["id"]: r["gaps_ranking"] for r in GapsRanker().rerank(results, **kw)}


def test_gap_formula_load_high_support_low():
    """score = load / (1 + support): a load-bearing unsourced claim scores
    1.0; equal load with support collapses the score; zero load → 0."""
    out = GapsRanker().rerank([
        {"id": "gap", "gaps_outgoing_impl": 1, "gaps_outgoing_nand": 0,
         "gaps_incoming_impl": 0, "gaps_source_count": 0},
        {"id": "supported", "gaps_outgoing_impl": 1, "gaps_incoming_impl": 3,
         "gaps_source_count": 1},
        {"id": "isolated", "gaps_outgoing_impl": 0, "gaps_incoming_impl": 0,
         "gaps_source_count": 0},
        {"id": "heavy", "gaps_outgoing_impl": 3, "gaps_outgoing_nand": 1,
         "gaps_incoming_impl": 0, "gaps_source_count": 0},
    ])
    by_id = {r["id"]: r["gaps_ranking"] for r in out}
    assert by_id["gap"]["score"] == pytest.approx(1.0)
    assert by_id["supported"]["score"] == pytest.approx(1.0 / 5.0)  # 1/(1+4)
    assert by_id["isolated"]["score"] == 0.0       # nothing leans on it
    assert by_id["heavy"]["score"] == pytest.approx(4.0)  # 4/(1+0)
    # Heavy load outranks light load at equal support.
    assert out[0]["id"] == "heavy"


def test_gap_breakdown_fields():
    """Breakdown exposes the decomposed load/support signals, not just the
    aggregate score (transparency for agents deciding what to research)."""
    out = GapsRanker().rerank([
        {"id": "x", "gaps_outgoing_impl": 2, "gaps_outgoing_nand": 1,
         "gaps_incoming_impl": 1, "gaps_incoming_nand": 2,
         "gaps_source_count": 0, "gaps_confidence": 0.9, "gaps_has_ep": True},
    ])
    rr = out[0]["gaps_ranking"]
    assert rr["outgoing_impl"] == 2 and rr["outgoing_nand"] == 1
    assert rr["load"] == 3
    assert rr["incoming_impl"] == 1 and rr["incoming_nand"] == 2
    assert rr["source_count"] == 0
    assert rr["support"] == 1  # incoming IMPL + source — NAND is NOT support
    assert rr["confidence"] == 0.9 and rr["confidence_source"] == "posterior"


def test_incoming_nand_is_never_support():
    """Incoming NAND is contention, not support — a heavily-attacked claim
    still reads as a gap if it is load-bearing with no incoming IMPL/Source."""
    out = GapsRanker().rerank([
        {"id": "attacked", "gaps_outgoing_impl": 1,
         "gaps_incoming_impl": 0, "gaps_incoming_nand": 5,
         "gaps_source_count": 0, "gaps_variance": 0.05,
         "gaps_has_ep": True, "gaps_contested": True},
    ])
    rr = out[0]["gaps_ranking"]
    assert rr["support"] == 0
    assert rr["score"] == 1.0
    assert rr["incoming_nand"] == 5
    assert rr["contested"] is True  # contested surfaced, never hidden


def test_gap_uncalibrated_confidence_neutral():
    """Uncalibrated claims (no persisted α/β) get the documented neutral 0.5
    — the STRUCTURE (load vs support) is what makes a gap, not the prior."""
    out = GapsRanker().rerank([{"id": "u", "gaps_outgoing_impl": 1}])
    rr = out[0]["gaps_ranking"]
    assert rr["confidence"] == NEUTRAL_CONFIDENCE
    assert rr["confidence_source"] == "neutral"


def test_gaps_ranker_inputs_not_mutated():
    results = [{"id": "a", "gaps_outgoing_impl": 1, "gaps_incoming_impl": 0}]
    original = dict(results[0])
    GapsRanker().rerank(results)
    assert results[0] == original


def test_subgraph_expander_validation():
    ex = SubgraphExpander(projection=None)
    with pytest.raises(ValueError):
        ex.expand(["x"], depth=0)
    with pytest.raises(ValueError):
        ex.expand(["x"], depth=6)
    with pytest.raises(ValueError):
        ex.expand(["x"], completeness="bogus")
    with pytest.raises(ValueError):
        ex.expand(["x"], max_nodes=5)
    empty = ex.expand([])
    assert empty["nodes"] == [] and empty["edges"] == []
    assert empty["stats"]["node_count"] == 0


# ── Integration: embedded FalkorDBLite ─────────────────────────────────────

@pytest.fixture(autouse=True)
def _use_shared_embedded_db(shared_embedded_db):
    pass


def _fresh_sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_recall_gaps_"), "test.db")
    sdk = TortoiseSDK(db_path)
    try:  # noqa: SIM105
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    return sdk


def _set_posterior(sdk, pid: str, alpha: float, beta: float):
    mean = round(alpha / (alpha + beta), 4) if (alpha + beta) > 0 else 0.5
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.confidence = $c, "
        "n.posterior_alpha = $a, n.posterior_beta = $b",
        params={"id": pid, "a": alpha, "b": beta, "c": mean},
    )


def _link_source(sdk, pid: str, url: str):
    """Attach a Source to a Point via extractedFrom (reification-era graphs
    attach provenance directly, no operator involved)."""
    sdk.create_source(url, "T1")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}), (s:Source {url:$u}) CREATE (n)-[:extractedFrom]->(s)",
        params={"id": pid, "u": url},
    )


def _build_gaps_golden_graph(sdk):
    """(i) ``gap``: load-bearing (outgoing IMPL to ``other``) but completely
    unsupported — no incoming IMPL, no Source, uncalibrated. A gap.
    (ii) ``well``: equally load-bearing but WELL supported (3 incoming IMPL
    operators + a Source) — not a gap.
    (iii) ``isolated``: no load at all — not a gap (nothing leans on it).
    (iv) ``other``: pure support target — has incoming IMPL but no outgoing
    load — not a gap.
    Returns dict of {role: point_id}."""
    ev = [sdk.create_point("evidence", f"gaps golden evidence {i}") for i in range(3)]
    gap = sdk.create_point("statement", "zephyr q3 launch is load-bearing gap")
    other = sdk.create_point("statement", "zephyr launch derivative requirement")
    well = sdk.create_point("statement", "zephyr supply chain resolved well-supported")
    isolated = sdk.create_point("statement", "nova engine coolant spec is q3 target")
    # gap is load-bearing: it supports `other` (operator-mediated).
    sdk.create_operator("IMPL", gap["id"], [other["id"]])
    # well is equally load-bearing (supports `other` directly via reification
    # rule — operator-less IMPL edge) BUT well-supported: 3 incoming IMPL
    # operators + a Source.
    sdk._get_proj().g.query(
        "MATCH (a:Point {id:$s}), (b:Point {id:$t}) CREATE (a)-[:IMPL]->(b)",
        params={"s": well["id"], "t": other["id"]},
    )
    for e in ev:
        sdk.create_operator("IMPL", e["id"], [well["id"]])
    _link_source(sdk, well["id"], "https://example.com/supply-chain")
    _set_posterior(sdk, well["id"], 10.0, 1.0)
    return {"gap": gap["id"], "well": well["id"],
            "isolated": isolated["id"], "other": other["id"]}


def test_gaps_golden_set_integration():
    """Golden set end-to-end: the load-bearing-but-unsourced claim IS
    returned (and ranks first); the well-supported claim is NOT; the
    no-load claim is NOT; the pure support target is NOT."""
    sdk = _fresh_sdk()
    try:
        g = _build_gaps_golden_graph(sdk)
        results = sdk.recall_gaps(kind="statement", limit=20)
        ids = [r["id"] for r in results]
        assert g["gap"] in ids, "load-bearing unsourced claim must be returned"
        assert g["well"] not in ids, "well-supported claim must NOT be returned"
        assert g["isolated"] not in ids, "no-load claim must NOT be returned"
        assert g["other"] not in ids, "pure support target must NOT be returned"
        assert ids[0] == g["gap"], "the gap ranks first"
        by_id = {r["id"]: r for r in results}
        rr = by_id[g["gap"]]["gaps_ranking"]
        assert rr["load"] == 1 and rr["support"] == 0
        assert rr["score"] == pytest.approx(1.0)
        assert rr["confidence_source"] == "neutral"  # uncalibrated
    finally:
        sdk.close()


def test_gaps_reification_rule_direct_edges():
    """Reification rule (ontology v3.5): operator-less IMPL edges count —
    a direct (a)-[:IMPL]->(b) edge is load for a and support for b. The
    golden graph above already exercises this for `well`; this test pins the
    direct-edge path explicitly for a claim whose ONLY load is direct."""
    sdk = _fresh_sdk()
    try:
        proj = sdk._get_proj()
        src = sdk.create_point("statement", "direct source claim")
        dst = sdk.create_point("statement", "direct target claim")
        proj.g.query(
            "MATCH (a:Point {id:$s}), (b:Point {id:$t}) CREATE (a)-[:IMPL]->(b)",
            params={"s": src["id"], "t": dst["id"]},
        )
        results = sdk.recall_gaps(kind="statement", limit=20)
        by_id = {r["id"]: r for r in results}
        assert src["id"] in by_id
        rr = by_id[src["id"]]["gaps_ranking"]
        assert rr["outgoing_impl"] == 1 and rr["load"] == 1
        assert rr["support"] == 0
        # dst has incoming IMPL (support) and no load — NOT a gap.
        assert dst["id"] not in by_id
    finally:
        sdk.close()


def test_gaps_mixed_edges_count_twice():
    """P0 regression: a claim that is BOTH operator-source (idx=0) AND has a
    direct outgoing IMPL edge is load-bearing twice — the cross-product must
    not collapse the two same-type edges into one count."""
    sdk = _fresh_sdk()
    try:
        proj = sdk._get_proj()
        a = sdk.create_point("statement", "mixed load claim")
        c = sdk.create_point("statement", "mixed load target")
        sdk.create_operator("IMPL", a["id"], [c["id"]])          # op idx=0
        proj.g.query(
            "MATCH (x:Point {id:$s}), (y:Point {id:$t}) CREATE (x)-[:IMPL]->(y)",
            params={"s": a["id"], "t": c["id"]},
        )                                                            # direct
        results = sdk.recall_gaps(kind="statement", limit=20)
        rr = next(r for r in results if r["id"] == a["id"])["gaps_ranking"]
        assert rr["outgoing_impl"] == 2 and rr["load"] == 2
        assert rr["score"] == pytest.approx(2.0)
    finally:
        sdk.close()


def test_gaps_direct_nand_incoming_surfaced():
    """P1 regression: a direct (reification) operator-less NAND edge into a
    claim is contention — surfaced as incoming_nand, never support."""
    sdk = _fresh_sdk()
    try:
        proj = sdk._get_proj()
        a = sdk.create_point("statement", "directly attacked claim")
        b = sdk.create_point("statement", "direct attacker")
        c = sdk.create_point("statement", "load target")
        # a is load-bearing (supports c) AND directly NANDed by b.
        proj.g.query(
            "MATCH (x:Point {id:$s}), (y:Point {id:$t}) CREATE (x)-[:IMPL]->(y)",
            params={"s": a["id"], "t": c["id"]},
        )
        proj.g.query(
            "MATCH (x:Point {id:$s}), (y:Point {id:$t}) CREATE (x)-[:NAND]->(y)",
            params={"s": b["id"], "t": a["id"]},
        )
        results = sdk.recall_gaps(kind="statement", limit=20)
        rr = next(r for r in results if r["id"] == a["id"])["gaps_ranking"]
        assert rr["incoming_nand"] == 1
        assert rr["support"] == 0  # NAND never counts as support
    finally:
        sdk.close()


def test_gaps_topic_scope_uses_hybrid_pool():
    """Topic-scoped gaps: retrieval pool scoped by query (mocked
    deterministically, same pattern as test_recall_state's mixed-pool test)
    — operators surfaced by point retrieval are filtered out of the pool."""
    sdk = _fresh_sdk()
    try:
        g = _build_gaps_golden_graph(sdk)
        sdk.tortoise_fts_query = lambda q, **kw: [
            {"id": g["gap"], "content": "zephyr q3 launch is load-bearing gap",
             "point_kind": "statement", "scores": {"rrf": 0.05}},
            {"id": g["well"], "content": "zephyr supply chain resolved well-supported",
             "point_kind": "statement", "scores": {"rrf": 0.05}},
            # An operator sneaking into point retrieval must be filtered out.
            {"id": "op-1", "content": "", "point_kind": "statement",
             "scores": {"rrf": 0.03}},
        ]
        # Operator node so the batch exclusion query can see it.
        op = sdk._get_proj().g.query(
            "CREATE (o:Point {id:'op-1', is_operator:true, op_type:'IMPL'}) RETURN o.id",
        ).result_set
        assert op, "operator fixture must be created"
        results = sdk.recall_gaps("zephyr", kind="statement", limit=20)
        ids = [r["id"] for r in results]
        assert "op-1" not in ids
        assert g["gap"] in ids and g["well"] not in ids
    finally:
        sdk.close()


def test_gaps_requires_pool_definition():
    sdk = _fresh_sdk()
    try:
        with pytest.raises(ValueError):
            sdk.recall_gaps(limit=10)  # no query, no kind
        with pytest.raises(ValueError):
            sdk.recall_gaps(kind="statement", min_load=-1)
        with pytest.raises(ValueError):
            sdk.recall_gaps(kind="statement", max_support=-1)
        with pytest.raises(ValueError):
            sdk.recall_gaps(kind="statement", limit=0)
    finally:
        sdk.close()


def test_gaps_superseded_excluded_by_default():
    sdk = _fresh_sdk()
    try:
        g = _build_gaps_golden_graph(sdk)
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.status = 'superseded'",
            params={"id": g["gap"]},
        )
        default_ids = {r["id"] for r in sdk.recall_gaps(kind="statement", limit=20)}
        assert g["gap"] not in default_ids
        inc = {r["id"] for r in sdk.recall_gaps(
            kind="statement", limit=20, include_superseded=True)}
        assert g["gap"] in inc
    finally:
        sdk.close()


# ── Integration: subgraph (UC3) ───────────────────────────────────────────

def _build_subgraph_graph(sdk):
    """A topic cluster + an unrelated island:
    zephyr cluster: claim1 --IMPL--> claim2 (operator-mediated), both
    aboutObject→zephyr, claim1 extractedFrom→source.
    atlas island: claim3 (unrelated — must NOT appear)."""
    c1 = sdk.create_point("statement", "zephyr supports orbital refueling")
    c2 = sdk.create_point("statement", "zephyr supports crew rotation")
    c3 = sdk.create_point("statement", "atlas launcher reuses vega avionics")
    sdk.create_operator("IMPL", c1["id"], [c2["id"]])
    obj = sdk.create_object("zephyr", "product")
    proj = sdk._get_proj()
    proj.create_about_edge(c1["id"], obj["id"], "aboutObject")
    proj.create_about_edge(c2["id"], obj["id"], "aboutObject")
    _link_source(sdk, c1["id"], "https://example.com/zephyr")
    return {"c1": c1["id"], "c2": c2["id"], "c3": c3["id"], "obj": obj["id"]}


def test_subgraph_completeness_over_precision():
    """UC3: seeded topic returns the CONNECTED subgraph (nodes + edges
    between them), completeness-optimized — the unrelated island is absent,
    but every reachable node (claims, operator, object, source) is present
    with its edges."""
    sdk = _fresh_sdk()
    try:
        g = _build_subgraph_graph(sdk)
        res = sdk.recall_subgraph(g["c1"], depth=2, completeness="full")
        node_ids = {n["id"] for n in res["nodes"]}
        assert g["c1"] in node_ids and g["c2"] in node_ids
        assert g["obj"] in node_ids
        assert any(n["type"] == "source" for n in res["nodes"])
        assert g["c3"] not in node_ids, "unrelated claim must not leak in"
        # The operator node is part of the epistemic structure.
        assert any(n.get("is_operator") for n in res["nodes"])
        # Edges: closed over the node set, both endpoints present.
        edge_types = {(e["type"],) for e in res["edges"]}
        assert ("IMPL",) in edge_types
        assert ("aboutObject",) in edge_types
        assert ("extractedFrom",) in edge_types
        node_set = set(node_ids)
        assert all(e["source"] in node_set and e["target"] in node_set
                   for e in res["edges"])
        assert res["stats"]["node_count"] == len(res["nodes"])
        assert res["stats"]["edge_count"] == len(res["edges"])
    finally:
        sdk.close()


def test_subgraph_depth_bounds_expansion():
    """depth controls reach: depth=1 stops at the first-hop neighborhood
    (c2 unreachable — the IMPL is operator-mediated and needs 2 hops)."""
    sdk = _fresh_sdk()
    try:
        g = _build_subgraph_graph(sdk)
        d1 = sdk.recall_subgraph(g["c1"], depth=1)
        ids1 = {n["id"] for n in d1["nodes"]}
        assert g["c2"] not in ids1  # operator-mediated IMPL needs hop 2
        d2 = sdk.recall_subgraph(g["c1"], depth=2)
        ids2 = {n["id"] for n in d2["nodes"]}
        assert g["c2"] in ids2
    finally:
        sdk.close()


def test_subgraph_completeness_core():
    """completeness='core' → epistemic core only (IMPL|NAND edges); about*
    and extractedFrom edges (and their entity nodes) are excluded."""
    sdk = _fresh_sdk()
    try:
        g = _build_subgraph_graph(sdk)
        res = sdk.recall_subgraph(g["c1"], depth=2, completeness="core")
        assert all(e["type"] in ("IMPL", "NAND") for e in res["edges"])
        assert not any(n.get("type") == "object" for n in res["nodes"])
        assert not any(n.get("type") == "source" for n in res["nodes"])
        # c2 still reachable via the operator's IMPL edge.
        assert g["c2"] in {n["id"] for n in res["nodes"]}
    finally:
        sdk.close()


def test_subgraph_seed_by_topic_text():
    """Seed may be a topic TEXT (resolved via retrieval, mocked
    deterministically) — the expansion then pulls in the entities the seed
    points touch."""
    sdk = _fresh_sdk()
    try:
        g = _build_subgraph_graph(sdk)
        sdk.tortoise_fts_query = lambda q, **kw: [
            {"id": g["c1"], "content": "zephyr supports orbital refueling",
             "point_kind": "statement", "scores": {"rrf": 0.05}},
        ]
        res = sdk.recall_subgraph("zephyr orbital refueling", depth=2)
        assert g["c1"] in {n["id"] for n in res["nodes"]}
        assert g["obj"] in {n["id"] for n in res["nodes"]}
        assert res["stats"]["node_count"] >= 3
    finally:
        sdk.close()


def test_subgraph_unresolvable_seed_returns_empty():
    sdk = _fresh_sdk()
    try:
        res = sdk.recall_subgraph("no-such-id-anywhere")
        assert res["nodes"] == [] and res["edges"] == []
        assert res["stats"]["node_count"] == 0
        assert res["stats"]["depth"] == 0
        with pytest.raises(ValueError):
            sdk.recall_subgraph("")
        # Bounds validated even when the seed does not resolve (P2).
        with pytest.raises(ValueError):
            sdk.recall_subgraph("no-such-id-anywhere", depth=6)
        with pytest.raises(ValueError):
            sdk.recall_subgraph("no-such-id-anywhere", completeness="bogus")
        with pytest.raises(ValueError):
            sdk.recall_subgraph("no-such-id-anywhere", max_nodes=5)
    finally:
        sdk.close()


def test_subgraph_max_nodes_truncation():
    """max_nodes bounds the expansion and reports truncated — bounded, not
    exhaustive-until-crash (UC3 completeness with a safety valve)."""
    sdk = _fresh_sdk()
    try:
        proj = sdk._get_proj()  # noqa: F841
        hub = sdk.create_point("statement", "hub claim")
        for i in range(8):
            leaf = sdk.create_point("statement", f"leaf claim {i}")
            sdk.create_operator("IMPL", hub["id"], [leaf["id"]])
        res = sdk.recall_subgraph(hub["id"], depth=2, max_nodes=10)
        assert res["stats"]["truncated"] is True
        assert res["stats"]["node_count"] <= 10
        # A comfortable cap gets everything (hub + 8 leaves + 8 ops).
        res2 = sdk.recall_subgraph(hub["id"], depth=2, max_nodes=50)
        assert res2["stats"]["truncated"] is False
        assert res2["stats"]["node_count"] == 17
    finally:
        sdk.close()


# ── MCP tool wiring (epic #898) ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _transport_context():
    from tortoise.mcp_auth import (  # noqa: I001
        _current_team_id, _current_team_limits, _transport_mode,
    )
    _transport_mode.set("stdio")
    _current_team_id.set(None)
    _current_team_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_team_id.set(None)
    _current_team_limits.set(None)


def test_tortoise_recall_mode_routing():
    """state/gaps/subgraph/custom route to their intent; invalid mode errors
    with a clear message."""
    from tortoise import mcp_server as mcp_mod
    from tortoise.mcp_server import tortoise_recall
    sdk = _fresh_sdk()
    orig_sdk = mcp_mod.sdk
    mcp_mod.sdk = sdk
    try:
        g = _build_gaps_golden_graph(sdk)
        # state
        r = tortoise_recall(mode="state", kind="statement")
        assert r["mode"] == "state" and isinstance(r["results"], list)
        # gaps
        r = tortoise_recall(mode="gaps", kind="statement")
        assert r["mode"] == "gaps"
        ids = [x["id"] for x in r["results"]]
        assert g["gap"] in ids and g["well"] not in ids
        # subgraph (flat {nodes, edges, stats} shape)
        r = tortoise_recall(mode="subgraph", seed=g["gap"])
        assert r["mode"] == "subgraph"
        assert "nodes" in r and "edges" in r and "stats" in r
        assert g["gap"] in {n["id"] for n in r["nodes"]}
        # custom
        r = tortoise_recall(mode="custom", kind="statement", limit=3)
        assert r["mode"] == "custom" and len(r["results"]) <= 3
        # invalid mode
        r = tortoise_recall(mode="nonsense")
        assert r["mode"] == "nonsense" and "error" in r
        # subgraph without a seed → error dict, not a crash
        r = tortoise_recall(mode="subgraph")
        assert "error" in r
    finally:
        mcp_mod.sdk = orig_sdk
        sdk.close()


def test_tortoise_recall_gaps_defaults_overridable():
    """Per-mode preset defaults are individually overridable (preset +
    override pattern): min_load=2 excludes the single-load claim."""
    from tortoise import mcp_server as mcp_mod
    from tortoise.mcp_server import tortoise_recall
    sdk = _fresh_sdk()
    orig_sdk = mcp_mod.sdk
    mcp_mod.sdk = sdk
    try:
        g = _build_gaps_golden_graph(sdk)
        r = tortoise_recall(mode="gaps", kind="statement", min_load=2)
        ids = [x["id"] for x in r["results"]]
        assert g["gap"] not in ids  # load=1 < min_load=2 override
        # Custom depth override on subgraph.
        r = tortoise_recall(mode="subgraph", seed=g["gap"], depth=1)
        assert r["stats"]["depth"] == 1
        # max_nodes override passes through (P2: subgraph cap overridable).
        r = tortoise_recall(mode="subgraph", seed=g["gap"], max_nodes=50)
        assert "error" not in r
    finally:
        mcp_mod.sdk = orig_sdk
        sdk.close()


def test_tortoise_recall_gaps_preset_limit_is_20():
    """P1 regression: mode='gaps' without an explicit limit must use the
    gaps preset (limit=20), not the state default (10)."""
    from tortoise import mcp_server as mcp_mod
    from tortoise.mcp_server import tortoise_recall
    sdk = _fresh_sdk()
    orig_sdk = mcp_mod.sdk
    mcp_mod.sdk = sdk
    try:
        # 12 load-bearing-unsupported claims (each supports a shared target).
        target = sdk.create_point("statement", "shared load target")
        for i in range(12):
            c = sdk.create_point("statement", f"gap claim {i} unsupported")
            sdk.create_operator("IMPL", c["id"], [target["id"]])
        r = tortoise_recall(mode="gaps", kind="statement")
        assert r["mode"] == "gaps"
        assert len(r["results"]) == 12, "gaps preset must not truncate at 10"
        # Explicit limit override still wins.
        r = tortoise_recall(mode="gaps", kind="statement", limit=5)
        assert len(r["results"]) == 5
    finally:
        mcp_mod.sdk = orig_sdk
        sdk.close()
