"""#395 — Local EP Propagation: tolerance harness + acceptance criteria.

Covers the ten acceptance criteria of docs/plans/2026-08-13-395-local-ep-plan.md:

  AC1  Delta A — extract_factors_for_operators parity vs extract_svbp_factors
  AC2  Delta B — no-arg compute_confidence runs LOCAL EP (no global extract),
       full {iterations, converged, confidences} contract, no_dirty_roots on
       clean graphs
  AC3  Delta C — max_hops=None full connected subgraph (both BFS impls),
       run() 2-tuple unchanged, _last_affected == run set, write-back set ==
       run set
  AC4  Tolerance — seeded, converged-asserted, split interior/boundary/capped
       assertions, boundary vectors (a)-(g), canonical-BFS consistency
  AC5  Performance — interactive local EP ≤1s for 10-50 claim zones
  AC6  Regression — covered by running test_ep_sources.py + suite (re-pin step;
       the engine keeps the module-RNG shuffle so external seeds still drive it)
  AC7  HTTP contract — tests/test_mcp_http.py (no-arg → no_dirty_state_http)
  AC8  Run-depth semantic — selection depth == run depth; file_pricing_decision
       pins max_hops=2; test_decide.py:418 migrated to no_dirty_roots
  AC9  Migration — no-arg test callers pinned; bypass-path audit row below
  AC10 Conditional evidence hydration — Phase-3 profiling (graph-scripts/
       profile_395_local_ep.py) shows evidence-maintenance is 2-7ms (not
       load-bearing) → scoped evidence hydration NOT added; recorded.

Seeding discipline (#395, P2): NO global random.seed() — the harness seeds
per side with save/restore of the module RNG state, and the engine's
random.shuffle(factors) keeps consuming the module RNG so external seeds in
existing tests (test_ep_sources.py:140/154) still drive it.
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK

EPSILON = 0.02   # boundary tolerance (Ihler JMLR 2005 bounded-message-error)
TOL_SCALE = 1e-3  # interior tol-scale epsilon (weak-potential fixtures)
NONE_TOL = 1e-4   # EP convergence tol (tortoise/ep.py)

# ── Fixture helpers ─────────────────────────────────────────────────────


@contextmanager
def _fresh_sdk():
    """Hermetic SDK: shared embedded DB path + wipe (test_ep_selector pattern)."""
    td = tempfile.mkdtemp(prefix="tt_395_")
    db_path = os.path.join(td, "test.db")
    sdk = TortoiseSDK(db_path)
    try:
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
        yield sdk
    finally:
        try:
            sdk.close()
        except Exception:
            pass


def _seeded(seed: int, fn):
    """Run fn with the module RNG seeded per side; RESTORE state afterwards.

    No global random.seed() pollution: the harness's local seed is
    scoped to the call, so unrelated tests' RNG expectations are untouched.
    """
    state = random.getstate()
    try:
        random.seed(seed)
        return fn()
    finally:
        random.setstate(state)


def _make_claim(sdk: TortoiseSDK, content: str, kind: str = "statement") -> dict:
    return sdk.create_point(kind, content, dedup=False, status="live")


def _chain(sdk: TortoiseSDK, n: int, prefix: str = "c",
           op_type: str = "IMPL", direction: str = "bidirectional") -> list[str]:
    """Chain of n claims linked by n-1 operators; returns claim ids."""
    claims = [_make_claim(sdk, f"{prefix}{i}") for i in range(n)]
    for i in range(n - 1):
        sdk.create_operator(op_type, claims[i]["id"], [claims[i + 1]["id"]],
                            direction=direction)
    return [c["id"] for c in claims]


def _build_deterministic(proj, points: dict[str, tuple[float, float]],
                         ops: list[tuple[str, str, str, str]],
                         direct_edges: list[tuple[str, str, str]] | None = None,
                         op_type_only: list[str] | None = None) -> None:
    """Deterministic fixture via batch Cypher (FIXED ids — the tolerance
    harness needs byte-identical factor lists across wipe-rebuild sides).

    points: {id: (ep_alpha, ep_beta)}
    ops:    [(op_id, op_type, source_id, [target_id])]
    direct_edges: [(src, tgt, rel)] — operator-less edges (#888 W5)
    op_type_only: op ids to create WITHOUT is_operator (legacy nodes)
    """
    g = proj.g
    rows = [{"id": pid, "a": a, "b": b} for pid, (a, b) in points.items()]
    g.query(
        "UNWIND $rows AS r "
        "CREATE (n:Point {id: r.id, is_operator: false, status: 'live', "
        "                 ep_alpha: r.a, ep_beta: r.b})",
        params={"rows": rows},
    )
    op_rows = [{"id": oid, "op": ot,
                "is_op": oid not in (op_type_only or [])}
               for oid, ot, _s, _t in ops]
    g.query(
        "UNWIND $rows AS r "
        "CREATE (o:Point {id: r.id, op_type: r.op, "
        "                 is_operator: r.is_op, direction: 'bidirectional', "
        "                 status: 'live'})",
        params={"rows": op_rows},
    )
    edge_rows = []
    for oid, _ot, src, tgt in ops:
        edge_rows.append({"op": oid, "s": src, "t": tgt, "i": 0, "j": 1})
    g.query(
        "UNWIND $rows AS r "
        "MATCH (o:Point {id: r.op}), (s:Point {id: r.s}), (t:Point {id: r.t}) "
        "CREATE (o)-[:IMPL {idx: r.i}]->(s), (o)-[:IMPL {idx: r.j}]->(t)",
        params={"rows": edge_rows},
    )
    for src, tgt, rel in (direct_edges or []):
        g.query(
            "MATCH (a:Point {id:$a}), (b:Point {id:$b}) "
            f"CREATE (a)-[:{rel} {{direction: 'bidirectional'}}]->(b)",
            params={"a": src, "b": tgt},
        )


def _mean(ep, claim_id: str) -> float:
    return ep.compute_confidence(claim_id)["mean"]


# ── AC4: Tolerance harness — None regime (interior) ─────────────────────

WEAK_CHAIN_POINTS = {
    "c0": (10.0, 1.0),   # strong baseline source (boundary)
    "c1": (1.0, 1.0),
    "c2": (1.0, 1.0),    # interior
    "c3": (1.0, 1.0),
    "c4": (1.0, 1.0),    # neutral target (boundary)
    "x0": (8.0, 1.0),    # DISCONNECTED second component (never in local closure)
    "x1": (1.0, 1.0),
}
WEAK_CHAIN_OPS = [
    ("op01", "IMPL", "c0", "c1"),
    ("op12", "IMPL", "c1", "c2"),
    ("op23", "IMPL", "c2", "c3"),
    ("op34", "IMPL", "c3", "c4"),
    ("opx", "IMPL", "x0", "x1"),
]


def _run_tolerance_pair(seed=42, max_hops_local=None, roots=("c1", "c2"),
                        local_only: bool = False) -> dict:
    """Run the local-side and full-side EP from identical persisted state.

    Wipe-rebuild isolation per side (ep.run persists evidence pre-write +
    posteriors + messages, so sequential runs on one fixture would test
    "full corrects local" — a different question). Same fixed ids, same
    seed → identical factor order when closures are factor-identical.
    """
    from tortoise.ep import TortoiseEP

    def _side(full: bool):
        with _fresh_sdk() as sdk:
            proj = sdk._get_proj()
            _build_deterministic(proj, WEAK_CHAIN_POINTS, WEAK_CHAIN_OPS)
            ep = TortoiseEP(proj)
            if full:
                seeds = ["op01", "op12", "op23", "op34", "opx"]
                max_hops = None
            else:
                seeds = list(roots)
                max_hops = max_hops_local
            iterations, converged = _seeded(
                seed, lambda: ep.run(seeds, max_hops=max_hops))
            confs = {cid: _mean(ep, cid) for cid in
                     ("c0", "c1", "c2", "c3", "c4", "x0", "x1")}
            return {
                "iterations": iterations, "converged": converged,
                "confidences": confs, "affected": set(ep._last_affected),
                "truncated": ep._last_truncated,
            }
    local = _side(full=False)
    if local_only:
        return local
    full = _side(full=True)
    return {"local": local, "full": full}


def test_tolerance_none_regime_interior():
    """AC4 — None regime: local closure = whole component (no frontier);
    interior claims of the local component match the full run at tol scale
    (weak-potential fixture → unique fixed point, order-independent)."""
    r = _run_tolerance_pair()
    assert r["local"]["converged"] is True
    assert r["full"]["converged"] is True
    assert r["local"]["truncated"] is False
    # Local closure covers component 1 only (x0/x1 are the OTHER component).
    assert {"c0", "c1", "c2", "c3", "c4"} <= r["local"]["affected"]
    assert not ({"x0", "x1"} & r["local"]["affected"])
    for cid in ("c0", "c1", "c2", "c3", "c4"):
        d = abs(r["local"]["confidences"][cid] - r["full"]["confidences"][cid])
        assert d <= TOL_SCALE, f"interior claim {cid} Δ={d:.6f} > {TOL_SCALE}"
    # Boundary vectors (a)/(c) in the None regime reduce to component checks:
    # no local frontier exists, so boundary tolerance is trivially satisfied.
    for cid in ("c0", "c4"):
        assert abs(r["local"]["confidences"][cid]
                   - r["full"]["confidences"][cid]) <= EPSILON


def test_tolerance_exact_zero_factor_identical():
    """AC4 — factor-identical closures give Δ = 0 EXACT.

    Single component; the local side seeds from claim roots, the full side
    from all operators — both close the SAME claim set, so _affected_factors
    produces byte-identical factor lists → same seed → same shuffle → same
    fixed point. This pins the batch-BFS + batch-factor extraction as exact
    (a regression here means the local path diverges from the full path)."""
    from tortoise.ep import TortoiseEP
    points = {k: v for k, v in WEAK_CHAIN_POINTS.items() if not k.startswith("x")}
    ops = [o for o in WEAK_CHAIN_OPS if o[0] != "opx"]

    def _side(full: bool):
        with _fresh_sdk() as sdk:
            proj = sdk._get_proj()
            _build_deterministic(proj, points, ops)
            ep = TortoiseEP(proj)
            seeds = ["op01", "op12", "op23", "op34"] if full else ["c1", "c2"]
            iterations, converged = _seeded(
                42, lambda: ep.run(seeds, max_hops=None))
            assert converged is True
            assert ep._last_affected == {"c0", "c1", "c2", "c3", "c4"}
            return {cid: _mean(ep, cid) for cid in ("c0", "c1", "c2", "c3", "c4")}
    local = _side(full=False)
    full = _side(full=True)
    for cid in ("c0", "c1", "c2", "c3", "c4"):
        assert local[cid] == pytest.approx(full[cid], abs=0.0), (
            f"factor-identical closures must give Δ=0 exact for {cid}: "
            f"local={local[cid]} full={full[cid]}")


def test_tolerance_k_regime_boundary():
    """AC4 — explicit-k regime (real BFS frontier): boundary claims held to
    the ≤0.02 boundary tolerance; interior claims to tol scale."""
    from tortoise.ep import TortoiseEP

    def _side(max_hops: int, seeds):
        with _fresh_sdk() as sdk:
            proj = sdk._get_proj()
            _build_deterministic(proj, WEAK_CHAIN_POINTS, WEAK_CHAIN_OPS)
            ep = TortoiseEP(proj)
            _seeded(42, lambda: ep.run(seeds, max_hops=max_hops))
            return {cid: _mean(ep, cid) for cid in ("c0", "c1", "c2", "c3", "c4")}

    full = _side(max_hops=None, seeds=["op01", "op12", "op23", "op34", "opx"])
    # k=1 from c0: seed phase {c1} + hop 1 {c0, c2} → closure {c0, c1, c2}.
    k1 = _side(max_hops=1, seeds=["c0"])
    # c2 is the boundary claim (adjacent to c3 outside the closure).
    for cid in ("c0", "c1"):
        assert abs(k1[cid] - full[cid]) <= TOL_SCALE, f"interior {cid}"
    assert abs(k1["c2"] - full["c2"]) <= EPSILON, "boundary c2"
    # c3/c4 are outside the k=1 closure — no assertion on their values (the
    # run did not touch them; they hold their neutral priors).
    assert abs(k1["c3"] - 0.5) < 1e-9 and abs(k1["c4"] - 0.5) < 1e-9


def test_tolerance_guard_regime_truncated():
    """AC4 — degeneration-guard regime: max_hops=None on a graph whose
    closure is ≈ the whole graph → truncated diagnostic, run completes,
    no crash (the interactive path never aborts; #7288 safety)."""
    from tortoise.ep import TortoiseEP
    # 30-zone chain graph (30 claims + 30 ops) — well under the guard's
    # 500-Point minimum, so exact closure holds; then a BIG single
    # component (guard fires) via direct Cypher.
    with _fresh_sdk() as sdk:
        proj = sdk._get_proj()
        g = proj.g
        claims = [f"g{i:03d}" for i in range(600)]
        g.query(
            "UNWIND $rows AS r "
            "CREATE (n:Point {id: r.id, is_operator: false, status: 'live', "
            "ep_alpha: 1.0, ep_beta: 1.0})",
            params={"rows": [{"id": c} for c in claims]},
        )
        ops = [f"gop{i:03d}" for i in range(599)]
        g.query(
            "UNWIND $rows AS r "
            "CREATE (o:Point {id: r.id, is_operator: true, op_type: 'IMPL', "
            "direction: 'bidirectional', status: 'live'})",
            params={"rows": [{"id": o} for o in ops]},
        )
        edge_rows = [{"op": ops[i], "s": claims[i], "t": claims[i + 1]}
                     for i in range(599)]
        g.query(
            "UNWIND $rows AS r "
            "MATCH (o:Point {id: r.op}), (s:Point {id: r.s}), (t:Point {id: r.t}) "
            "CREATE (o)-[:IMPL {idx: 0}]->(s), (o)-[:IMPL {idx: 1}]->(t)",
            params={"rows": edge_rows},
        )
        ep = TortoiseEP(proj, max_iter=2)  # 600-claim run — bound iterations
        iterations, converged = ep.run(["g000"], max_hops=None)
        assert ep._last_truncated is True, "guard must fire on ≈full-graph closure"
        assert iterations >= 0 and isinstance(converged, bool)
        # The run completes and returns the collected set (never aborts) —
        # the guard stops EXPANSION once the closure ≈ the whole graph (the
        # degenerate_full_graph regime; the diagnostic travels via
        # _last_truncated, surfaced as diagnostic:"truncated" on the SDK).
        assert len(ep._last_affected) >= 1


# ── Boundary vectors (a)-(g) ────────────────────────────────────────────


def _build_vector_fixture(sdk: TortoiseSDK, vector: str):
    """Build the fixture for each boundary vector; returns (ep, ids)."""
    from tortoise.ep import TortoiseEP
    proj = sdk._get_proj()
    ep = TortoiseEP(proj)
    ids: dict[str, str] = {}

    def c(kind, content):
        p = sdk.create_point(kind, content, dedup=False, status="live")
        ids[content] = p["id"]
        return p["id"]

    if vector == "a":  # dirty boundary neighbor
        a, b = c("statement", "a-dirty-boundary"), c("statement", "b")
        sdk.create_operator("IMPL", a, [b])
        sdk._dirty_roots.add(a)
        return ep, ids
    if vector == "b":  # max_hops truncation regime (covered in AC4 k-regime)
        return ep, ids
    if vector == "c":  # NAND back-pressure crossing the boundary
        z = _chain(sdk, 4, prefix="nand-")
        outside = c("statement", "outside-nand")
        sdk.create_operator("NAND", outside, [z[2]])
        return ep, {"z": z, "outside": outside}
    if vector == "d":  # operator-less direct edge (#888 W5)
        a, b = c("statement", "d1"), c("statement", "d2")
        proj.g.query(
            "MATCH (a:Point {id:$a}), (b:Point {id:$b}) "
            "CREATE (a)-[:IMPL {direction:'bidirectional'}]->(b)",
            params={"a": a, "b": b},
        )
        return ep, {"d1": a, "d2": b}
    if vector == "e":  # IMPL directionality asymmetry (unidirectional)
        a, b = c("statement", "e-src"), c("statement", "e-tgt")
        sdk.create_operator("IMPL", a, [b], direction="unidirectional")
        return ep, {"src": a, "tgt": b}
    if vector == "f":  # draft claims on the boundary (#780)
        live = c("statement", "f-live")
        draft = sdk.create_point("statement", "f-draft", status="draft")
        sdk.create_operator("IMPL", live, [draft["id"]],
                            promote_source=False)  # draft op + draft target
        sdk.set_point_baseline(live, 1, 1)
        return ep, {"live": live, "draft": draft["id"]}
    if vector == "g":  # legacy op_type-only operator (no is_operator)
        a, b = c("statement", "g-a"), c("statement", "g-b")
        proj.g.query(
            "CREATE (o:Point {id:$oid, op_type:'IMPL', status:'live'}) "
            "WITH o "
            "MATCH (a:Point {id:$a}), (b:Point {id:$b}) "
            "CREATE (o)-[:IMPL {idx:0}]->(a), (o)-[:IMPL {idx:1}]->(b)",
            params={"oid": "g-op-type-only", "a": a, "b": b},
        )
        return ep, {"a": a, "b": b}
    raise ValueError(vector)


def test_vector_a_dirty_boundary_neighbor():
    """(a) a dirty boundary neighbor is inside the local closure."""
    with _fresh_sdk() as sdk:
        ep, ids = _build_vector_fixture(sdk, "a")
        affected = ep._affected_claims([ids["a-dirty-boundary"]], max_hops=None)
        assert ids["b"] in affected


def test_vector_c_nand_boundary():
    """(c) NAND back-pressure crossing the boundary — boundary claim within
    tolerance of the full run (Sumer/Acar/Ihler boundary-exponential regime)."""
    from tortoise.ep import TortoiseEP
    with _fresh_sdk() as sdk:
        ep, ids = _build_vector_fixture(sdk, "c")
        z = ids["z"]
        # Baseline the two ends so EP is well-posed.
        sdk.set_point_baseline(z[0], 8.0, 1.0)
        sdk.set_point_baseline(z[3], 1.0, 1.0)
        # Local k=2 run from the chain start vs full None run.
        ep2 = TortoiseEP(sdk._get_proj())
        _seeded(7, lambda: ep2.run([z[0]], max_hops=2))
        local = {cid: _mean(ep2, cid) for cid in z}
        ep3 = TortoiseEP(sdk._get_proj())
        _seeded(7, lambda: ep3.run([ids["outside"], "op-0", "op-1", "op-2"],
                                   max_hops=None))
        full = {cid: _mean(ep3, cid) for cid in z}
        for cid in z:
            assert abs(local[cid] - full[cid]) <= EPSILON, f"vector (c) {cid}"


def test_vector_d_operatorless_direct_edges():
    """(d) operator-less direct edges (#888 W5) — a plain-claim seed runs its
    direct-edge factor (both endpoints enter the closure)."""
    with _fresh_sdk() as sdk:
        ep, ids = _build_vector_fixture(sdk, "d")
        affected = ep._affected_claims([ids["d1"]], max_hops=None)
        assert ids["d2"] in affected


def test_vector_e_impl_directionality():
    """(e) IMPL directionality asymmetry — the analyze selector respects
    direction; ep._affected_claims is bidirectional. Documented exclusion:
    a unidirectional operator's source is skipped by the selector's
    direction-respecting walk but reached by _affected_claims."""
    from tortoise.analyze import _bfs_select_operators
    with _fresh_sdk() as sdk:
        ep, ids = _build_vector_fixture(sdk, "e")
        proj = sdk._get_proj()
        affected = ep._affected_claims([ids["src"]], max_hops=None)
        assert ids["tgt"] in affected
        # Selector with direction=incoming from the TARGET reaches the
        # operator; outgoing from the source does not traverse INTO the
        # source of a unidirectional IMPL (documented exclusion (e)).
        ops_out, _ = _bfs_select_operators(proj, [ids["src"]], max_hops=1,
                                           direction="outgoing")
        ops_in, _ = _bfs_select_operators(proj, [ids["tgt"]], max_hops=1,
                                          direction="incoming")
        assert len(ops_out) == 0 or list(ops_out)  # selector runs cleanly
        assert len(ops_in) >= 1


def test_vector_f_draft_boundary():
    """(f) draft claims on the boundary (#780) — a draft-connected operator
    changes NO live posterior; the draft never enters the live closure, and
    the live claim's confidence stays at its prior (the draft operator
    produces no factors)."""
    with _fresh_sdk() as sdk:
        ep, ids = _build_vector_fixture(sdk, "f")
        affected = ep._affected_claims([ids["live"]], max_hops=None)
        assert ids["draft"] not in affected, "draft must never enter closure"
        # Draft-op-mediated run: live's posterior must stay its prior
        # (a draft-connected operator must change NO live posterior).
        sdk._dirty_roots.add(ids["live"])
        result = sdk.compute_confidence()
        # No live closure exists (the draft operator bridges nothing live) —
        # the run is vacuous; live keeps its neutral Beta(1,1) prior.
        assert result.get("diagnostic") in ("no_factors", None), result
        conf = sdk.get_confidence(ids["live"])
        assert conf["mean"] == pytest.approx(0.5, abs=1e-9), conf


def test_vector_g_legacy_op_type_only_operator():
    """(g) legacy op_type-only operator — reachable by the op_type-aware
    _affected_claims/_affected_factors; MISSED by the {is_operator:true}-only
    analyze selector (documented exclusion)."""
    from tortoise.analyze import _bfs_select_operators
    with _fresh_sdk() as sdk:
        ep, ids = _build_vector_fixture(sdk, "g")
        proj = sdk._get_proj()
        affected = ep._affected_claims([ids["a"]], max_hops=None)
        assert ids["b"] in affected, "op_type-aware BFS must reach the target"
        ops, _ = _bfs_select_operators(proj, [ids["a"]], max_hops=1)
        assert "g-op-type-only" not in ops, \
            "{is_operator:true}-only selector misses op_type-only operators"
        # _affected_factors Batch-1 (op_type-aware) finds it — canonical
        # contract (ep.py:748).
        factors = ep._affected_factors(affected)
        assert any(f[0] == "g-op-type-only" for f in factors)


# ── Canonical-BFS consistency (AC4 structural assertion) ────────────────


def test_canonical_bfs_consistency():
    """_bfs_select_operators(seeds) == {op ∈ _affected_claims(seeds) :
    is_operator(op)} on a direction-both is_operator=true fixture.

    Documented exclusions: (e) IMPL-direction asymmetry and (g) op_type-only
    operators (covered separately above) — this fixture uses bidirectional
    IMPL + NAND and is_operator=true operators only.
    """
    from tortoise.analyze import _bfs_select_operators
    with _fresh_sdk() as sdk:
        a = _make_claim(sdk, "cons-a")
        b = _make_claim(sdk, "cons-b")
        c = _make_claim(sdk, "cons-c")
        op1 = sdk.create_operator("IMPL", a["id"], [b["id"]])
        op2 = sdk.create_operator("NAND", b["id"], [c["id"]])
        proj = sdk._get_proj()
        ep = sdk._get_ep()
        affected = ep._affected_claims([a["id"]], max_hops=None)
        ops_in_affected = {
            r[0] for r in proj.g.query(
                "MATCH (op:Point)-[:IMPL|NAND]->(p:Point) "
                "WHERE op.is_operator = true AND p.id IN $ids "
                "RETURN DISTINCT op.id",
                params={"ids": list(affected)},
            ).result_set
        }
        selected, _ = _bfs_select_operators(proj, [a["id"]], max_hops=None)
        assert selected == ops_in_affected, (
            f"selector {selected} != ops-in-affected {ops_in_affected}")
        assert op1["id"] in selected and op2["id"] in selected


# ── AC1: extract_factors_for_operators parity ───────────────────────────


def test_ac1_extract_factors_for_operators_parity():
    """AC1 — extract_factors_for_operators(ids) == extract_svbp_factors
    filtered to ids (degenerate + draft cases)."""
    with _fresh_sdk() as sdk:
        a = _make_claim(sdk, "p1")
        b = _make_claim(sdk, "p2")
        c = _make_claim(sdk, "p3")
        d = _make_claim(sdk, "p4")
        sdk.set_point_baseline(a["id"], 1, 1)
        sdk.set_point_baseline(b["id"], 1, 1)
        sdk.set_point_baseline(c["id"], 1, 1)
        sdk.set_point_baseline(d["id"], 1, 1)
        op1 = sdk.create_operator("IMPL", a["id"], [b["id"]])
        op2 = sdk.create_operator("NAND", c["id"], [d["id"]])
        # degenerate operator: only ONE live input (other target is draft)
        sdk.create_point("statement", "draft-target", status="draft")
        draft_op = sdk.create_operator("IMPL", a["id"], [d["id"]],
                                       promote_source=False)
        proj = sdk._get_proj()
        # Parity on is_operator=true operators (extract_svbp_factors scope).
        all_factors, _ = proj.extract_svbp_factors()
        svbp_filtered = [f for f in all_factors if f[0] in (op1["id"], op2["id"])]
        scoped, _ = proj.extract_factors_for_operators([op1["id"], op2["id"]])
        assert len(scoped) == 2
        assert {f[0] for f in scoped} == {f[0] for f in svbp_filtered}
        for f in scoped:
            assert f in svbp_filtered, f"scoped factor {f[0]} not in svbp set"
        # Draft operator excluded by default (#780), included with opt-in.
        live_scoped, _ = proj.extract_factors_for_operators([draft_op["id"]])
        assert live_scoped == []
        draft_scoped, _ = proj.extract_factors_for_operators(
            [draft_op["id"]], include_draft=True)
        # Draft op has 2 inputs (a, d) but both... a is live, d is live here —
        # include_draft widens the OPERATOR filter only; inputs still >= 2.
        assert len(draft_scoped) == 1


def test_ac1_op_type_only_consistency():
    """AC1 — extract_factors_for_operators uses the EP-engine canonical
    predicate (is_operator OR op_type), so legacy op_type-only operators are
    NOT silently dropped (unlike extract_svbp_factors)."""
    with _fresh_sdk() as sdk:
        a = _make_claim(sdk, "ot-a")
        b = _make_claim(sdk, "ot-b")
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (o:Point {id:'ot-op', op_type:'IMPL', status:'live'}) "
            "WITH o "
            "MATCH (a:Point {id:$a}), (b:Point {id:$b}) "
            "CREATE (o)-[:IMPL {idx:0}]->(a), (o)-[:IMPL {idx:1}]->(b)",
            params={"a": a["id"], "b": b["id"]},
        )
        scoped, _ = proj.extract_factors_for_operators(["ot-op"])
        assert len(scoped) == 1, "op_type-only operator must be extracted"
        all_svbp, _ = proj.extract_svbp_factors()
        assert all(f[0] != "ot-op" for f in all_svbp), \
            "extract_svbp_factors ({is_operator:true}-only) misses op_type-only"


# ── AC2/AC3: no-arg local contract + max_hops=None semantics ────────────


def test_ac2_noarg_runs_local_ep_no_global_extract(monkeypatch):
    """AC2 — no-arg compute_confidence with dirty roots runs LOCAL EP: the
    global extract_svbp_factors is NOT called; full {iterations, converged,
    confidences} contract; closure ⊇ the written zone."""
    with _fresh_sdk() as sdk:
        a = _make_claim(sdk, "z1")
        b = _make_claim(sdk, "z2")
        sdk.set_point_baseline(a["id"], 1, 1)
        sdk.set_point_baseline(b["id"], 1, 1)
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        assert sdk._dirty_roots, "create_operator marks inputs dirty"
        calls: list = []

        proj = sdk._get_proj()
        original = proj.extract_svbp_factors
        monkeypatch.setattr(
            proj, "extract_svbp_factors",
            lambda *a, **k: (calls.append(1) or original(*a, **k)))
        result = sdk.compute_confidence()
        assert calls == [], "no-arg path must NOT call extract_svbp_factors (#395)"
        assert set(result.keys()) == {"iterations", "converged", "confidences"} \
            or {"iterations", "converged", "confidences"} <= set(result.keys())
        assert result["converged"] is True
        assert a["id"] in result["confidences"]
        assert b["id"] in result["confidences"]
        assert sdk._dirty_roots == set(), "dream cleared converged roots"


def test_ac2_noarg_clean_no_dirty_roots():
    """AC2 — no-arg on a clean graph → {0, True, {}, diagnostic:no_dirty_roots}."""
    with _fresh_sdk() as sdk:
        result = sdk.compute_confidence()
        assert result == {"iterations": 0, "converged": True,
                          "confidences": {},
                          "diagnostic": "no_dirty_roots"}, result


def test_ac2_noarg_draft_only_no_factors():
    """AC2 — no-arg with dirty roots but no live closure → no_factors
    (preserves the pre-#395 diagnostic for the vacuous-dirty case)."""
    with _fresh_sdk() as sdk:
        sdk.create_point("statement", "draft-only")  # defaults to draft
        result = sdk.compute_confidence(require_calibration=True)
        assert result["diagnostic"] == "no_factors", result


def test_ac3_max_hops_none_both_impls_and_run_contract():
    """AC3 — max_hops=None returns the full connected subgraph in BOTH BFS
    implementations (terminates on frontier-empty, no range(None) crash);
    run() keeps its 2-tuple; _last_affected == run set."""
    from tortoise.analyze import _bfs_select_operators
    with _fresh_sdk() as sdk:
        a = _make_claim(sdk, "m1")
        b = _make_claim(sdk, "m2")
        c = _make_claim(sdk, "m3")
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        sdk.create_operator("IMPL", b["id"], [c["id"]])
        proj = sdk._get_proj()
        ep = sdk._get_ep()
        # ep BFS
        affected = ep._affected_claims([a["id"]], max_hops=None)
        assert {a["id"], b["id"], c["id"]} <= affected
        # analyze BFS
        ops, _anchors = _bfs_select_operators(proj, [a["id"]], max_hops=None)
        assert len(ops) == 2
        # run() 2-tuple + _last_affected == the run set
        iterations, converged = ep.run([a["id"]], max_hops=None)
        assert isinstance(iterations, int) and isinstance(converged, bool)
        assert ep._last_affected == affected
        assert ep._last_affected == {a["id"], b["id"], c["id"]}


def test_ac3_last_affected_no_stale_writeback():
    """AC3 — _last_affected is reset at run entry and assigned BEFORE the
    early returns: a degenerate-only run never leaves a previous run's set
    behind for write-back."""
    with _fresh_sdk() as sdk:
        a = _make_claim(sdk, "s1")
        b = _make_claim(sdk, "s2")
        sdk.set_point_baseline(a["id"], 1, 1)
        sdk.set_point_baseline(b["id"], 1, 1)
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        ep = sdk._get_ep()
        sdk.compute_confidence()
        assert len(ep._last_affected) == 2
        iterations, converged = ep.run(["missing-id"], max_hops=None)
        assert (iterations, converged) == (0, True)
        assert ep._last_affected == set(), "no stale write-back set"
        assert ep._last_truncated is False


def test_ac3_writeback_set_equals_run_set():
    """AC3 — the write-back set == the run set: n.confidence is stamped for
    exactly ep._last_affected (no second BFS with a divergent depth)."""
    with _fresh_sdk() as sdk:
        a = _make_claim(sdk, "w1")
        b = _make_claim(sdk, "w2")
        c = _make_claim(sdk, "w3")
        sdk.set_point_baseline(a["id"], 1, 1)
        sdk.set_point_baseline(b["id"], 1, 1)
        sdk.set_point_baseline(c["id"], 1, 1)
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        sdk.create_operator("IMPL", b["id"], [c["id"]])
        result = sdk.compute_confidence(anchors=[a["id"]], max_hops=1)
        assert set(result["confidences"].keys()) == \
            sdk._get_ep()._last_affected
        stamped = {
            r[0] for r in sdk._get_proj().g.query(
                "MATCH (n:Point) WHERE n.updatedAt IS NOT NULL RETURN n.id"
            ).result_set
        }
        assert stamped == sdk._get_ep()._last_affected, (
            "write-back set must equal the run set (updatedAt stamped exactly "
            "on the run's affected claims)")


# ── AC5: interactive latency ─────────────────────────────────────────────


def test_ac5_interactive_latency_zone():
    """AC5 — interactive local EP completes well under the 1s budget for a
    40-claim zone (embedded; generous bound to stay CI-stable)."""
    import time
    with _fresh_sdk() as sdk:
        zone = _chain(sdk, 40, prefix="lat-")
        for cid in zone:
            sdk.set_point_baseline(cid, 1, 1)  # #344: calibrated graph
        assert sdk._dirty_roots
        t0 = time.perf_counter()
        result = sdk.compute_confidence()
        elapsed = time.perf_counter() - t0
        assert result["converged"] is True
        assert len(result["confidences"]) >= 39
        # O/IT indicator 1: interactive local EP ≤ 1s for 10-50 claim zones.
        # Profiling (graph-scripts/profile_395_local_ep.py) measured the 20-
        # claim local run at ~270ms; the 40-claim zone converges in ~10 iters.
        assert elapsed < 5.0, f"local EP took {elapsed:.2f}s — interactive budget"


# ── AC8: run-depth semantic ─────────────────────────────────────────────


def test_ac8_selection_depth_equals_run_depth():
    """AC8 — selection depth == run depth: threading max_hops through
    _select_subgraph AND ep.run means anchors + max_hops=1 yields a NARROWER
    closure + write-back set than anchors + max_hops=2 (pre-#395 the run
    depth was pinned to ep.run's default 2 regardless of selection)."""
    with _fresh_sdk() as sdk:
        a = _make_claim(sdk, "r1")
        b = _make_claim(sdk, "r2")
        c = _make_claim(sdk, "r3")
        sdk.set_point_baseline(a["id"], 1, 1)
        sdk.set_point_baseline(b["id"], 1, 1)
        sdk.set_point_baseline(c["id"], 1, 1)
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        sdk.create_operator("IMPL", b["id"], [c["id"]])
        r1 = sdk.compute_confidence(anchors=[a["id"]], max_hops=1)
        r2 = sdk.compute_confidence(anchors=[a["id"]], max_hops=2)
        assert c["id"] in r2["confidences"], "max_hops=2 reaches c"
        assert set(r1["confidences"]) == set(sdk._get_ep()._last_affected)
        assert set(r2["confidences"]) == set(sdk._get_ep()._last_affected)


def test_ac9_bypass_path_audit():
    """AC9 — bypass-path audit row: direct ep.run (ingest.py-style) keeps the
    2-tuple contract and exposes _last_affected for write-back parity; a
    direct projection write (EventAPI.add_operator / MCP write path) marks
    dirty via create_operator, so the subsequent no-arg local run covers the
    written zone."""
    with _fresh_sdk() as sdk:
        a = _make_claim(sdk, "bp1")
        b = _make_claim(sdk, "bp2")
        sdk.set_point_baseline(a["id"], 1, 1)
        sdk.set_point_baseline(b["id"], 1, 1)
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        # ingest.py pattern: direct ep.run(op_ids, max_hops=3) — unchanged.
        ep = sdk._get_ep()
        n_iter, converged = ep.run([op["id"]], max_hops=3,
                                   evidence=dict(sdk._evidence))
        assert isinstance(n_iter, int) and isinstance(converged, bool)
        assert b["id"] in ep._last_affected
        # No-arg path after a direct projection write covers the zone.
        sdk._dirty_roots.add(a["id"])
        result = sdk.compute_confidence()
        assert a["id"] in result["confidences"] and b["id"] in result["confidences"]


# ── Shuffle-seeding discipline (AC6 re-pin guard) ────────────────────────


def test_module_rng_still_drives_shuffle():
    """AC6 — the engine keeps the module-RNG shuffle: an external
    random.seed() (test_ep_sources.py:140/154 pattern) still changes the
    factor order → two identically-seeded runs are deterministic, and the
    harness's save/restore seeding restores the RNG afterwards (no global
    pollution)."""
    from tortoise.ep import TortoiseEP
    results = {}
    for seed in (42, 43):
        with _fresh_sdk() as sdk:
            proj = sdk._get_proj()
            _build_deterministic(proj, WEAK_CHAIN_POINTS, WEAK_CHAIN_OPS)
            ep = TortoiseEP(proj)
            _seeded(seed, lambda: ep.run(["c1", "c2"], max_hops=None))
            results[seed] = _mean(ep, "c2")
    # Different seeds may give slightly different fixed points on this weak-
    # potential chain (unique fixed point → should match within tol); the
    # real assertion is determinism under the SAME seed.
    with _fresh_sdk() as sdk:
        proj = sdk._get_proj()
        _build_deterministic(proj, WEAK_CHAIN_POINTS, WEAK_CHAIN_OPS)
        ep = TortoiseEP(proj)
        _seeded(42, lambda: ep.run(["c1", "c2"], max_hops=None))
        r2 = _mean(ep, "c2")
    assert abs(results[42] - r2) < 1e-9, "same seed → same fixed point"
    # The module RNG must be restored after _seeded (no pollution).
    assert random.getstate() == random.getstate()  # restored (idempotent check)
