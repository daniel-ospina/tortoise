"""Deterministic tests for review_connections (#913 W6).

review_connections is the hygiene counterpart to connect (design:
product/2026-08-11-tooling-surface-consolidation.md, PR #912):

  - mode=add:    surface related-but-MISSING connections (semantic similarity
                 above threshold, no existing edge). Suggestions only — the
                 agent decides; never creates.
  - mode=prune:  flag ILLOGICAL/stale connections via EP signals:
                   * stale          — edge incident to a retracted/superseded/
                                      outdated/archived point (terminal or
                                      legacy outdated flag)
                   * contested      — edge incident to a claim with high
                                      posterior variance (stored EP params)
                                      or a claim with an incoming NAND edge
                   * contradictory  — the same pair is BOTH IMPL- and
                                      NAND-linked
  - mode=both:   runs both, returns {add: [...], prune: [...]}.
  - READ-ONLY:   review_connections never mutates the graph.

Determinism: similarity is injected via similarity_fn (same seam as the
injectable rankers in #898 recall) so tests never depend on embedding-model
availability. EP params are written directly to the graph (stored-prior
read), so no EP run is needed either.

Runs against the embedded FalkorDBLite SDK (sdk_factory, conftest) — no
Docker required.
"""
from __future__ import annotations

import pytest

# ── Helpers ──────────────────────────────────────────────────────────


def _sim(pairs: dict[tuple[str, str], float]):
    """Build an injectable similarity_fn from a {sorted_pair: score} map.

    The real signature is similarity_fn(points) -> {(a_id, b_id): score}
    with a_id < b_id; the injected fn ignores contents and returns the
    fixed map (deterministic regardless of embedding-model availability).
    """
    def _fn(points):
        out = {}
        for (a, b), s in pairs.items():
            if s > 0 and any(p["id"] == a for p in points) \
                    and any(p["id"] == b for p in points):
                out[(a, b)] = s
        return out
    return _fn


def _edge_prune(entries, issue=None):
    """Filter prune entries by issue (None = all)."""
    if issue is None:
        return entries
    return [e for e in entries if e["issue"] == issue]


# ── mode=add ─────────────────────────────────────────────────────────

def test_add_surfaces_missing_connection(sdk_factory, tmp_path):
    """Related-but-unconnected pair is suggested; already-connected pair is NOT."""
    sdk = sdk_factory(tmp_path)
    p1 = sdk.create_point("statement", "kubernetes rollout plan")
    p2 = sdk.create_point("statement", "kubernetes rollout plan v2")
    p3 = sdk.create_point("statement", "kubernetes deployment guide")
    # p1-p2 are ALREADY connected (shared IMPL operator); p1-p3 are not.
    sdk.create_operator("IMPL", p1["id"], [p2["id"]], label="supports")

    result = sdk.review_connections(
        mode="add",
        similarity_fn=_sim({(p1["id"], p2["id"]): 0.9, (p1["id"], p3["id"]): 0.8}),
    )
    assert set(result.keys()) == {"add"}
    add = result["add"]
    assert len(add) == 1, f"expected exactly 1 suggestion, got {add}"
    sug = add[0]
    assert {sug["from"], sug["to"]} == {p1["id"], p3["id"]}
    assert sug["suggested_relation"] == "IMPL"
    assert isinstance(sug["reason"], str) and len(sug["reason"]) > 0
    assert abs(sug["similarity"] - 0.8) < 1e-9
    # The connected pair (p1,p2) must NOT be suggested even though it has
    # the HIGHER similarity.
    assert {sug["from"], sug["to"]} != {p1["id"], p2["id"]}


def test_add_does_not_suggest_connected_pairs(sdk_factory, tmp_path):
    """Already-connected pairs are never suggested."""
    sdk = sdk_factory(tmp_path)
    p1 = sdk.create_point("statement", "the cat sat on the mat")
    p2 = sdk.create_point("statement", "the cat sat on the mat")
    sdk.create_operator("IMPL", p1["id"], [p2["id"]])
    # Also connect via a direct (operator-less) edge, reification rule
    # (ontology v3.5 §8) — direct edges must count as connected too.
    p3 = sdk.create_point("statement", "the cat sat on the mat")
    sdk._get_proj().g.query(
        "MATCH (a:Point {id:$a}), (b:Point {id:$b}) CREATE (a)-[:IMPL]->(b)",
        params={"a": p1["id"], "b": p3["id"]},
    )
    # p2-p3: connect via a shared operator too so EVERY pair is connected.
    sdk.create_operator("IMPL", p2["id"], [p3["id"]])

    result = sdk.review_connections(
        mode="add",
        similarity_fn=_sim({
            (p1["id"], p2["id"]): 0.95,
            (p1["id"], p3["id"]): 0.95,
            (p2["id"], p3["id"]): 0.95,
        }),
    )
    assert result["add"] == []


def test_add_below_threshold_excluded(sdk_factory, tmp_path):
    """Pairs below the similarity threshold are not suggested."""
    sdk = sdk_factory(tmp_path)
    p1 = sdk.create_point("statement", "alpha")
    p2 = sdk.create_point("statement", "beta")
    result = sdk.review_connections(
        mode="add",
        similarity_fn=_sim({(p1["id"], p2["id"]): 0.3}),  # < default 0.40
    )
    assert result["add"] == []


def test_add_default_threshold_boundary(sdk_factory, tmp_path):
    """A pair at 0.45 (inside the #399-calibrated 'semantically related'
    band 0.35-0.51) IS suggested under the default threshold 0.40."""
    sdk = sdk_factory(tmp_path)
    p1 = sdk.create_point("statement", "alpha")
    p2 = sdk.create_point("statement", "beta")
    result = sdk.review_connections(
        mode="add",
        similarity_fn=_sim({(p1["id"], p2["id"]): 0.45}),
    )
    assert len(result["add"]) == 1
    assert result["add"][0]["similarity"] == pytest.approx(0.45)


def test_add_surfaces_iml_only_for_similar_unconnected(sdk_factory, tmp_path):
    """Every suggestion carries suggested_relation IMPL + a reason."""
    sdk = sdk_factory(tmp_path)
    p1 = sdk.create_point("statement", "alpha")
    p2 = sdk.create_point("statement", "beta")
    p3 = sdk.create_point("statement", "gamma")
    result = sdk.review_connections(
        mode="add",
        similarity_fn=_sim({
            (p1["id"], p2["id"]): 0.9,
            (p1["id"], p3["id"]): 0.7,
        }),
    )
    assert len(result["add"]) == 2
    for sug in result["add"]:
        assert set(sug.keys()) == {"from", "to", "suggested_relation", "reason", "similarity"}
        assert sug["suggested_relation"] == "IMPL"


def test_add_scope_topic_narrows_pool(sdk_factory, tmp_path):
    """scope=<topic text> restricts the candidate pool via retrieval."""
    sdk = sdk_factory(tmp_path)
    k1 = sdk.create_point("statement", "kubernetes rollout plan")
    k2 = sdk.create_point("statement", "kubernetes deployment pipeline")
    off = sdk.create_point("statement", "croissant bakery menu")
    # Unscoped, similarity says EVERYTHING is related — but scoped to the
    # kubernetes topic, the croissant point is outside the pool and must not
    # appear in any suggestion.
    result = sdk.review_connections(
        mode="add",
        scope="kubernetes deployment rollout",
        similarity_fn=_sim({
            (k1["id"], k2["id"]): 0.9,
            (k1["id"], off["id"]): 0.9,
            (k2["id"], off["id"]): 0.9,
        }),
    )
    pairs = [frozenset((s["from"], s["to"])) for s in result["add"]]
    assert pairs, "expected at least one suggestion in the scoped pool"
    assert all(off["id"] not in p for p in pairs), \
        f"out-of-scope point leaked into suggestions: {result['add']}"
    assert frozenset((k1["id"], k2["id"])) in pairs


def test_add_invalid_mode_and_limits(sdk_factory, tmp_path):
    """Invalid mode raises; add_limit caps suggestions."""
    sdk = sdk_factory(tmp_path)
    with pytest.raises(ValueError, match="mode"):
        sdk.review_connections(mode="bogus")
    p1 = sdk.create_point("statement", "alpha")
    p2 = sdk.create_point("statement", "beta")
    p3 = sdk.create_point("statement", "gamma")
    result = sdk.review_connections(
        mode="add",
        add_limit=1,
        similarity_fn=_sim({
            (p1["id"], p2["id"]): 0.9,
            (p1["id"], p3["id"]): 0.8,
        }),
    )
    assert len(result["add"]) == 1
    # Cap applies after similarity sorting → the HIGHEST-similarity pair wins.
    assert result["add"][0]["similarity"] == pytest.approx(0.9)


# ── mode=prune ───────────────────────────────────────────────────────

def test_prune_flags_stale_retracted(sdk_factory, tmp_path):
    """Edge to a retracted point is stale with suggested_action prune."""
    sdk = sdk_factory(tmp_path)
    src = sdk.create_point("statement", "live source claim")
    victim = sdk.create_point("statement", "soon-to-be-retracted")
    sdk.create_operator("IMPL", src["id"], [victim["id"]])
    sdk.retract_point(victim["id"])

    result = sdk.review_connections(mode="prune")
    stale = _edge_prune(result["prune"], "stale")
    assert len(stale) == 1, f"expected 1 stale entry, got {result['prune']}"
    e = stale[0]
    assert e["from"] == src["id"] and e["to"] == victim["id"]
    assert e["relation"] == "IMPL"
    assert e["suggested_action"] == "prune"  # retracted → no successor → prune
    assert e["detail"]["stale_endpoint"] == victim["id"]
    assert e["detail"]["status"] == "retracted"


def test_prune_flags_stale_superseded_repoint(sdk_factory, tmp_path):
    """Edge to a superseded point is stale with suggested_action re-point
    (successor found via CORRECTS)."""
    sdk = sdk_factory(tmp_path)
    src = sdk.create_point("statement", "live source claim")
    old = sdk.create_point("statement", "superseded claim")
    replacement = sdk.create_point("statement", "replacement claim")
    sdk.create_operator("IMPL", src["id"], [old["id"]])
    sdk.supersede_point(old["id"], replacement["id"])
    # supersede TRANSFERS the operator edge to the replacement — so the old
    # point is clean. A NEW edge to the superseded old point is the stale
    # connection the reviewer must flag.
    sdk.create_operator("IMPL", src["id"], [old["id"]])

    result = sdk.review_connections(mode="prune")
    stale = _edge_prune(result["prune"], "stale")
    hits = [e for e in stale if e["to"] == old["id"]]
    assert len(hits) == 1, f"expected 1 stale entry to superseded point, got {stale}"
    assert hits[0]["suggested_action"] == "re-point"
    assert hits[0]["detail"]["successor"] == replacement["id"]
    assert hits[0]["detail"]["status"] == "superseded"


def test_prune_flags_contested_high_variance(sdk_factory, tmp_path):
    """Edge incident to a high-variance claim is contested; low-variance
    claims are NOT flagged."""
    sdk = sdk_factory(tmp_path)
    a = sdk.create_point("statement", "contested claim")
    b = sdk.create_point("statement", "its supported claim")
    sdk.create_operator("IMPL", a["id"], [b["id"]])
    # Deterministic EP params (no EP run): Beta(1,2) → variance 2/36 ≈ 0.0556
    # > default variance_threshold 0.04 → contested.
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha=1.0, n.ep_beta=2.0",
        params={"id": a["id"]},
    )
    # Control pair: Beta(10,1) → variance ≈ 0.0069 < 0.04 → NOT contested.
    c = sdk.create_point("statement", "calm claim")
    d = sdk.create_point("statement", "its supported claim")
    sdk.create_operator("IMPL", c["id"], [d["id"]])
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha=10.0, n.ep_beta=1.0",
        params={"id": c["id"]},
    )

    result = sdk.review_connections(mode="prune")
    contested = _edge_prune(result["prune"], "contested")
    assert len(contested) == 1, f"expected exactly 1 contested entry, got {result['prune']}"
    e = contested[0]
    assert {e["from"], e["to"]} == {a["id"], b["id"]}
    assert e["relation"] == "IMPL"
    assert e["suggested_action"] == "review"
    assert e["detail"]["contested_endpoint"] == a["id"]
    assert e["detail"]["variance"] == pytest.approx(2.0 / 36.0, abs=1e-6)


def test_prune_flags_contested_incoming_nand(sdk_factory, tmp_path):
    """A live claim challenged by an incoming NAND operator edge is
    contested (derived `challenged` condition, ontology §5 — NAND on a LIVE
    point) even at low variance."""
    sdk = sdk_factory(tmp_path)
    x = sdk.create_point("statement", "attacked claim")
    y = sdk.create_point("statement", "attacker claim")
    sdk.update_point(x["id"], status="live")  # challenged requires LIVE
    sdk.create_operator("NAND", y["id"], [x["id"]])
    # Control pair: IMPL-only, uncalibrated → neither contested nor
    # contradictory.
    c = sdk.create_point("statement", "calm claim")
    d = sdk.create_point("statement", "its supported claim")
    sdk.create_operator("IMPL", c["id"], [d["id"]])

    result = sdk.review_connections(mode="prune")
    contested = _edge_prune(result["prune"], "contested")
    # The NAND links attacker→attacked; BOTH endpoints are live and carry
    # the incoming NAND edge (operator node fans out to both) → both are
    # contested, so the single NAND edge yields one entry.
    assert len(contested) == 1, f"expected 1 contested NAND entry, got {result['prune']}"
    e = contested[0]
    assert {e["from"], e["to"]} == {x["id"], y["id"]}
    assert e["relation"] == "NAND"
    assert e["suggested_action"] == "review"
    assert "NAND" in e["detail"]["reason"]
    # The IMPL control pair must not be flagged at all.
    assert not any(
        {c["id"], d["id"]} == {f["from"], f["to"]} for f in result["prune"])


def test_prune_nand_on_draft_not_contested(sdk_factory, tmp_path):
    """NAND into a DRAFT point is not `challenged` (ontology §5 requires
    live) — the draft victim is never the contested endpoint. (The live
    attacker y is challenged by its own NAND fan-out — that IS flagged.)"""
    sdk = sdk_factory(tmp_path)
    x = sdk.create_point("statement", "draft victim")  # stays draft
    y = sdk.create_point("statement", "attacker claim")  # promoted to live
    sdk.create_operator("NAND", y["id"], [x["id"]])
    result = sdk.review_connections(mode="prune")
    contested = _edge_prune(result["prune"], "contested")
    assert len(contested) == 1
    assert contested[0]["detail"]["contested_endpoint"] == y["id"]
    assert all(e["detail"]["contested_endpoint"] != x["id"] for e in contested)


def test_prune_flags_stale_outdated_flag(sdk_factory, tmp_path):
    """Legacy invalidate (outdated=true, status stays 'live') is reported
    stale with status 'outdated' (the signal that made it stale)."""
    sdk = sdk_factory(tmp_path)
    src = sdk.create_point("statement", "live source claim")
    victim = sdk.create_point("statement", "old claim")
    replacement = sdk.create_point("statement", "new claim")
    sdk.create_operator("IMPL", src["id"], [victim["id"]])
    sdk.invalidate_point(victim["id"], replacement["id"])  # outdated=true, status stays live

    result = sdk.review_connections(mode="prune")
    stale = _edge_prune(result["prune"], "stale")
    assert len(stale) == 1
    e = stale[0]
    assert e["to"] == victim["id"]
    assert e["detail"]["status"] == "outdated"
    assert e["suggested_action"] == "re-point"
    assert e["detail"]["successor"] == replacement["id"]


def test_prune_legacy_operator_without_idx(sdk_factory, tmp_path):
    """Legacy operators whose edges lack the idx property (pre-idx graphs)
    are still reviewed — all inputs degrade to unordered pairs (#913 review
    round 1: dict-keyed-by-None collapsed them to nothing)."""
    sdk = sdk_factory(tmp_path)
    src = sdk.create_point("statement", "legacy source")
    t1 = sdk.create_point("statement", "legacy target 1")
    t2 = sdk.create_point("statement", "legacy target 2")
    sdk.retract_point(t2["id"])
    # Raw-write a legacy operator node with IMPL edges but NO idx property.
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (op:Point {id:'legacy-op-1', is_operator:true, op_type:'IMPL'})"
    )
    for pid in (src["id"], t1["id"], t2["id"]):
        proj.g.query(
            "MATCH (op:Point {id:'legacy-op-1'}), (p:Point {id:$pid}) "
            "CREATE (op)-[:IMPL]->(p)",
            params={"pid": pid},
        )

    result = sdk.review_connections(mode="prune")
    stale = _edge_prune(result["prune"], "stale")
    # t2 is retracted → every pair involving t2 is stale (src-t2 and t1-t2).
    hits = [e for e in stale if t2["id"] in (e["from"], e["to"])]
    assert len(hits) == 2, f"expected 2 stale entries for legacy op, got {result['prune']}"
    assert all(e["relation"] == "IMPL" for e in hits)
    assert all(e["detail"]["via"] == "legacy-op-1" for e in hits)


def test_prune_stale_prefers_successor_endpoint(sdk_factory, tmp_path):
    """When BOTH endpoints of an edge are stale, the entry reports the
    endpoint WITH a CORRECTS successor (action re-point), not the first
    stale endpoint (Qwen gate, PR #933)."""
    sdk = sdk_factory(tmp_path)
    old_a = sdk.create_point("statement", "retracted claim")   # no successor
    old_b = sdk.create_point("statement", "superseded claim")  # successor
    repl = sdk.create_point("statement", "replacement claim")
    sdk.retract_point(old_a["id"])
    sdk.supersede_point(old_b["id"], repl["id"])
    sdk.create_operator("IMPL", old_a["id"], [old_b["id"]])

    result = sdk.review_connections(mode="prune")
    stale = _edge_prune(result["prune"], "stale")
    hit = [e for e in stale
           if {e["from"], e["to"]} == {old_a["id"], old_b["id"]}]
    assert len(hit) == 1, f"expected 1 stale entry for the pair, got {stale}"
    assert hit[0]["detail"]["stale_endpoint"] == old_b["id"]
    assert hit[0]["suggested_action"] == "re-point"
    assert hit[0]["detail"]["successor"] == repl["id"]


def test_prune_scope_empty_pool_returns_empty(sdk_factory, tmp_path):
    """A scoped prune whose scope matches nothing returns [] — never the
    whole-graph flag list (fail quiet, consistent with mode=add)."""
    sdk = sdk_factory(tmp_path)
    src = sdk.create_point("statement", "live source claim")
    victim = sdk.create_point("statement", "doomed claim")
    sdk.create_operator("IMPL", src["id"], [victim["id"]])
    sdk.retract_point(victim["id"])

    result = sdk.review_connections(mode="prune", scope="zzz-no-such-topic-xyz")
    assert result["prune"] == []


def test_prune_flags_contradictory_impl_nand(sdk_factory, tmp_path):
    """A pair linked by BOTH an IMPL and a NAND operator is contradictory."""
    sdk = sdk_factory(tmp_path)
    a = sdk.create_point("statement", "the deployment succeeded")
    b = sdk.create_point("statement", "the deployment failed")
    sdk.create_operator("IMPL", a["id"], [b["id"]], label="supports")
    sdk.create_operator("NAND", a["id"], [b["id"]], label="opposes")
    # Control pair: IMPL-only → NOT contradictory.
    c = sdk.create_point("statement", "calm claim")
    d = sdk.create_point("statement", "its supported claim")
    sdk.create_operator("IMPL", c["id"], [d["id"]])

    result = sdk.review_connections(mode="prune")
    contradictory = _edge_prune(result["prune"], "contradictory")
    assert len(contradictory) == 2, \
        f"expected 2 contradictory entries (IMPL + NAND), got {result['prune']}"
    rels = {e["relation"] for e in contradictory}
    assert rels == {"IMPL", "NAND"}
    for e in contradictory:
        assert {e["from"], e["to"]} == {a["id"], b["id"]}
        assert e["suggested_action"] == "review"
    assert not any(
        {c["id"], d["id"]} == {f["from"], f["to"]} and f["issue"] == "contradictory"
        for f in result["prune"])


def test_prune_direct_edges_flagged(sdk_factory, tmp_path):
    """Operator-less direct edges (reification rule) are reviewed too."""
    sdk = sdk_factory(tmp_path)
    a = sdk.create_point("statement", "alpha")
    b = sdk.create_point("statement", "beta")
    sdk._get_proj().g.query(
        "MATCH (a:Point {id:$a}), (b:Point {id:$b}) CREATE (a)-[:NAND]->(b)",
        params={"a": a["id"], "b": b["id"]},
    )
    sdk._get_proj().g.query(
        "MATCH (a:Point {id:$a}), (b:Point {id:$b}) CREATE (a)-[:IMPL]->(b)",
        params={"a": a["id"], "b": b["id"]},
    )
    result = sdk.review_connections(mode="prune")
    contradictory = _edge_prune(result["prune"], "contradictory")
    assert len(contradictory) == 2
    assert {e["relation"] for e in contradictory} == {"IMPL", "NAND"}
    for e in contradictory:
        assert e["detail"]["via"] == "direct"


def test_prune_limit_caps(sdk_factory, tmp_path):
    """prune_limit caps the number of flagged entries."""
    sdk = sdk_factory(tmp_path)
    src = sdk.create_point("statement", "src")
    for i in range(3):
        v = sdk.create_point("statement", f"victim {i}")
        sdk.create_operator("IMPL", src["id"], [v["id"]])
        sdk.retract_point(v["id"])
    result = sdk.review_connections(mode="prune", prune_limit=2)
    assert len(result["prune"]) == 2
    assert all(e["issue"] == "stale" for e in result["prune"])


# ── mode=both + review-only contract ─────────────────────────────────

def test_both_returns_add_and_prune(sdk_factory, tmp_path):
    """mode=both returns non-empty add AND prune sections."""
    sdk = sdk_factory(tmp_path)
    # add-side: related-but-unconnected pair
    p1 = sdk.create_point("statement", "kubernetes rollout plan")
    p2 = sdk.create_point("statement", "kubernetes deployment guide")
    # prune-side: stale edge to a retracted point
    src = sdk.create_point("statement", "live source claim")
    victim = sdk.create_point("statement", "doomed claim")
    sdk.create_operator("IMPL", src["id"], [victim["id"]])
    sdk.retract_point(victim["id"])

    result = sdk.review_connections(
        mode="both",
        similarity_fn=_sim({(p1["id"], p2["id"]): 0.85}),
    )
    assert set(result.keys()) == {"add", "prune"}
    assert len(result["add"]) == 1
    assert {result["add"][0]["from"], result["add"][0]["to"]} == {p1["id"], p2["id"]}
    assert len(result["prune"]) == 1
    assert result["prune"][0]["issue"] == "stale"


def test_review_only_no_mutation(sdk_factory, tmp_path):
    """review_connections is READ-ONLY — the graph is byte-identical after
    both modes run (nodes, edges, properties, event log)."""
    sdk = sdk_factory(tmp_path)
    p1 = sdk.create_point("statement", "kubernetes rollout plan")
    p2 = sdk.create_point("statement", "kubernetes deployment guide")
    src = sdk.create_point("statement", "live source claim")
    victim = sdk.create_point("statement", "doomed claim")
    sdk.create_operator("IMPL", src["id"], [victim["id"]])
    sdk.retract_point(victim["id"])

    def snapshot():
        proj = sdk._get_proj()
        nodes = sorted(
            (str(r[0]), tuple(sorted((str(k), str(v)) for k, v in (r[1] or {}).items())))
            for r in proj.g.query("MATCH (n) RETURN n.id, properties(n)").result_set
        )
        rels = sorted(
            (r[0], str(r[1]), str(r[2]), tuple(sorted((str(k), str(v)) for k, v in (r[3] or {}).items())))
            for r in proj.g.query(
                "MATCH (a)-[r]->(b) RETURN type(r), a.id, b.id, properties(r)"
            ).result_set
        )
        return {"nodes": nodes, "rels": rels}

    before = snapshot()
    result = sdk.review_connections(
        mode="both",
        similarity_fn=_sim({(p1["id"], p2["id"]): 0.85}),
    )
    assert result["add"] and result["prune"]  # exercise both branches
    after = snapshot()
    assert after == before, "review_connections mutated the graph"


# ── MCP / registry surface ───────────────────────────────────────────

def test_registry_entry_readonly_and_sdk_method(sdk_factory, tmp_path):
    """The MCP tool is registered as read-only with the SDK method wired."""
    from tortoise.tool_registry import TOOL_REGISTRY
    td = next(t for t in TOOL_REGISTRY if t.name == "tortoise_review_connections")
    assert td.sdk_method == "review_connections"
    assert td.annotations.readOnlyHint is True
    assert td.annotations.destructiveHint is False
    assert td.http_policy is True
    assert td.group == "reasoning"

    from tortoise.mcp_server import tortoise_review_connections
    assert callable(tortoise_review_connections)

    # SDK surface: the method exists and returns the documented shape.
    sdk = sdk_factory(tmp_path)
    assert callable(sdk.review_connections)
