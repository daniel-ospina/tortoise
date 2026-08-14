#!/usr/bin/env python3
"""Phase-3 profiling gate for #395 — Local EP Propagation.

Measures the current global-extraction cost profile vs the affected-subgraph
expectation on a production-SHAPE synthetic graph (the plan's "1,827
operators" count is [unverified-at-scope-time]; we build a graph of the same
order: ~1,800 operators across ~90 connected claim zones).

Gate outputs (docs/plans/2026-08-13-395-local-ep-plan.md Task 1):
  1. Connected-component distribution — determines exact-closure vs
     capped-component regime.
  2. Per-phase timings: extract / BFS / EP-loop / write-back — determines
     whether delta A (scoped factor extraction) is load-bearing and whether
     the local subgraph run wins.

Usage: python3 graph-scripts/profile_395_local_ep.py [--operators 1800]
       (embedded FalkorDBLite; no Docker, no network.)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import random  # noqa: E402


def _build_graph(proj, n_ops: int, claims_per_zone: int = 20,
                 connect_zones: bool = False) -> dict:
    """Build ~n_ops operators in a chain-of-zones topology via batch Cypher.

    Each zone = claims_per_zone claims in a chain (op_i IMPL claim_{i+1}).
    connect_zones=False (default) leaves zones DISCONNECTED (the realistic
    claim-zone regime — small components, exact-closure); True links zones
    into ONE giant component (the degeneration-guard regime). Returns counts.
    """
    n_zones = max(1, n_ops // (claims_per_zone + 1))
    g = proj.g
    g.query("MATCH (n) DETACH DELETE n")
    n_claims = n_zones * claims_per_zone
    n_ops_actual = n_zones * (claims_per_zone + 1) - (0 if connect_zones else n_zones - 1)
    # Claims
    claims = [f"claim-{i:05d}" for i in range(n_claims)]
    g.query(
        "UNWIND $rows AS r "
        "CREATE (n:Point {id: r.id, is_operator: false, status: 'live', "
        "                  ep_alpha: 1.0, ep_beta: 1.0})",
        params={"rows": [{"id": c} for c in claims]},
    )
    # Operators (zone chains + optional inter-zone links)
    ops = []
    edges = []
    op_idx = 0
    for z in range(n_zones):
        base = z * claims_per_zone
        for i in range(claims_per_zone - 1):
            op = f"op-{op_idx:05d}"
            ops.append(op)
            edges.append((op, "IMPL", claims[base + i], claims[base + i + 1], i))
            op_idx += 1
        if connect_zones and z < n_zones - 1:
            op = f"op-{op_idx:05d}"
            ops.append(op)
            edges.append((op, "IMPL", claims[base + claims_per_zone - 1],
                          claims[base + claims_per_zone], 0))
            op_idx += 1
    g.query(
        "UNWIND $rows AS r "
        "CREATE (o:Point {id: r.id, is_operator: true, op_type: 'IMPL', "
        "                 direction: 'bidirectional', status: 'live'})",
        params={"rows": [{"id": o} for o in ops]},
    )
    g.query(
        "UNWIND $rows AS r "
        "MATCH (o:Point {id: r.op}), (s:Point {id: r.src}), (t:Point {id: r.tgt}) "
        "CREATE (o)-[:IMPL {idx: r.idx}]->(s), (o)-[:IMPL {idx: r.idx2}]->(t) "
        "RETURN count(*)",
        params={"rows": [{"op": op, "src": src, "tgt": tgt, "idx": idx, "idx2": 0}
                         for (op, _rel, src, tgt, idx) in edges]},
    )
    return {"claims": n_claims, "operators": n_ops_actual, "zones": n_zones}


def component_distribution(proj) -> dict:
    """BFS over all IMPL/NAND edges (Python-side) — component sizes."""
    g = proj.g
    nodes = {r[0] for r in g.query("MATCH (n:Point) RETURN n.id").result_set}
    edge_rows = g.query(
        "MATCH (a:Point)-[:IMPL|NAND]-(b:Point) RETURN DISTINCT a.id, b.id"
    ).result_set
    adj: dict[str, set[str]] = {n: set() for n in nodes}
    for a, b in edge_rows:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen: set[str] = set()
    sizes = []
    for n in nodes:
        if n in seen:
            continue
        stack = [n]
        seen.add(n)
        comp = 0
        while stack:
            cur = stack.pop()
            comp += 1
            for nb in adj.get(cur, ()):
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        sizes.append(comp)
    sizes.sort(reverse=True)
    return {
        "components": len(sizes),
        "max_component": sizes[0] if sizes else 0,
        "giant_fraction": (sizes[0] / len(nodes)) if nodes else 0.0,
        "small_component_p95": sizes[int(0.05 * len(sizes))] if sizes else 0,
    }


def main() -> int:
    n_ops = int(sys.argv[sys.argv.index("--operators") + 1]) \
        if "--operators" in sys.argv else 1800
    connect = "--connected" in sys.argv

    from tortoise.projection import FalkorProjection
    from tortoise.ep import TortoiseEP

    td = tempfile.mkdtemp(prefix="tortoise_395_profile_")
    proj = FalkorProjection(os.path.join(td, "profile.db"), graph_name="test")
    stats = _build_graph(proj, n_ops, connect_zones=connect)
    g = proj.g
    print(f"graph: {stats['claims']} claims, {stats['operators']} operators, "
          f"{stats['zones']} zones ({"connected" if connect else "disconnected"})")

    # ── Gate output 1: connected-component distribution ──
    t0 = time.perf_counter()
    comp = component_distribution(proj)
    t_comp = (time.perf_counter() - t0) * 1000
    print(f"\n[component distribution] {t_comp:.1f} ms")
    for k, v in comp.items():
        print(f"  {k}: {v}")

    # ── Gate output 2: per-phase timings ──
    # 2a. Global extraction (the #7288 no-arg path today)
    t0 = time.perf_counter()
    factors_all, _ = proj.extract_svbp_factors()
    t_global_extract = (time.perf_counter() - t0) * 1000
    print(f"\n[extract] global extract_svbp_factors: {t_global_extract:.1f} ms "
          f"({len(factors_all)} factors)")

    # 2b. Local BFS (affected-subgraph from a dirty zone's roots)
    roots = ["claim-00000", "claim-00001", "claim-00002"]
    ep = TortoiseEP(proj)
    t0 = time.perf_counter()
    affected = ep._affected_claims(roots, max_hops=None)
    t_local_bfs = (time.perf_counter() - t0) * 1000
    print(f"[extract] local _affected_claims(max_hops=None): {t_local_bfs:.1f} ms "
          f"({len(affected)} claims)")

    # 2c. Local factor extraction (_affected_factors — the no-arg local path)
    t0 = time.perf_counter()
    factors_local = ep._affected_factors(affected)
    t_local_factors = (time.perf_counter() - t0) * 1000
    print(f"[extract] local _affected_factors: {t_local_factors:.1f} ms "
          f"({len(factors_local)} factors)")

    # 2d. Scoped extraction (delta B — the landed local path is
    # ep._affected_factors; the scoped-by-operator-ids extractor
    # extract_factors_for_operators was removed as dead code, PR #1273).
    # Measure a SECOND zone's closure so the per-zone extraction cost is
    # shown independently of the 2b/2c zone.
    zone2_roots = ["claim-00020", "claim-00021", "claim-00022"]
    zone2_affected = ep._affected_claims(zone2_roots, max_hops=None)
    t0 = time.perf_counter()
    factors_scoped = ep._affected_factors(zone2_affected)
    t_scoped = (time.perf_counter() - t0) * 1000
    print(f"[extract] _affected_factors(zone2): {t_scoped:.1f} ms "
          f"({len(factors_scoped)} factors)")

    # 2e. EP loop — full vs local (bounded iterations; per-iteration cost)
    # The FULL-graph run on the production-shape graph (1,800 ops / 9k
    # claims) is INFEASIBLE on the embedded backend — the 9k-node
    # _flush_cache UNWIND kills the embedded server (the #7288-class
    # problem this epic fixes). Measure the full run on a SCALED subgraph
    # (300 ops / 1.5k claims, max_iter=1) for the per-iteration cost and
    # extrapolate; guard the full-shape attempt so phases still report.
    full_ids = [f[0] for f in factors_all]
    ep_full = TortoiseEP(proj, max_iter=1)
    t0 = time.perf_counter()
    try:
        it_full, conv_full = ep_full.run(full_ids, max_hops=None)
        t_full = (time.perf_counter() - t0) * 1000
        print(f"\n[EP-loop] full run (all {len(full_ids)} ops, max_iter=1): "
              f"{t_full:.0f} ms ({it_full} iters, converged={conv_full})")
    except Exception as e:
        t_full = None
        print(f"\n[EP-loop] full run ({len(full_ids)} ops): INFEASIBLE on "
              f"embedded — {type(e).__name__}: {str(e)[:80]} "
              f"(#7288-class blowup; {time.perf_counter() - t0:.1f}s elapsed)")

    # Scaled full run on a 300-op / 1.5k-claim subgraph for a per-iteration
    # cost number.
    sub_ops = [f"op-{i:05d}" for i in range(300)]
    sub_claims = [f"claim-{i:05d}" for i in range(1500)]
    ep_sub = TortoiseEP(proj, max_iter=1)
    t0 = time.perf_counter()
    it_sub, conv_sub = ep_sub.run(sub_ops, max_hops=None)
    t_sub = (time.perf_counter() - t0) * 1000
    print(f"[EP-loop] scaled full run (300 ops → {len(ep_sub._last_affected)}"
          f" claims, max_iter=1): {t_sub:.0f} ms — per-iteration ≈ "
          f"{t_sub / max(it_sub, 1):.0f} ms for {len(ep_sub._last_affected)} claims")

    ep_local = TortoiseEP(proj, max_iter=3)
    t0 = time.perf_counter()
    it_local, conv_local = ep_local.run(roots, max_hops=None)
    t_local = (time.perf_counter() - t0) * 1000
    print(f"[EP-loop] local run ({len(roots)} roots → {len(ep_local._last_affected)} "
          f"claims): {t_local:.1f} ms ({it_local} iters, converged={conv_local}) — "
          f"extrapolated 50-iter ≈ {t_local / max(it_local, 1) * 50:.0f} ms")

    # 2f. Write-back: per-claim SET loop vs batch UNWIND
    n_wb = len(affected)
    t0 = time.perf_counter()
    for cid in affected:
        g.query(
            "MATCH (n:Point {id:$id}) SET n.confidence = $c, n.updatedAt = $now",
            params={"id": cid, "c": 0.5, "now": "now"},
        )
    t_per_claim = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    g.query(
        "UNWIND $params AS p "
        "MATCH (n:Point {id: p.id}) SET n.confidence = p.c, n.updatedAt = $now",
        params={"params": [{"id": cid, "c": 0.5} for cid in affected], "now": "now"},
    )
    t_unwind = (time.perf_counter() - t0) * 1000
    print(f"\n[write-back] per-claim SET × {n_wb}: {t_per_claim:.1f} ms; "
          f"batch UNWIND: {t_unwind:.1f} ms "
          f"({t_per_claim / max(t_unwind, 1e-6):.0f}× faster)")

    # 2g. Evidence maintenance (hydrate/source-inheritance — per-call cost)
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(os.path.join(td, "sdk.db"))
    sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    sdk._get_proj().g.query(
        "UNWIND $rows AS r CREATE (n:Point {id: r.id, is_operator: false, status: 'live'})",
        params={"rows": [{"id": c} for c in [f"c{i}" for i in range(2000)]]},
    )
    t0 = time.perf_counter()
    sdk._hydrate_evidence()
    t_hydrate = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    sdk._apply_source_inheritance(recency_decay=1.0)
    t_inherit = (time.perf_counter() - t0) * 1000
    print(f"[evidence-maintenance] _hydrate_evidence: {t_hydrate:.1f} ms; "
          f"_apply_source_inheritance: {t_inherit:.1f} ms")

    print("\n── gate summary ──")
    print(f"  global extract:      {t_global_extract:7.1f} ms")
    print(f"  local BFS:           {t_local_bfs:7.1f} ms")
    print(f"  local factors:       {t_local_factors:7.1f} ms")
    print(f"  scoped extract (dB): {t_scoped:7.1f} ms")
    if t_full is not None:
        print(f"  full EP (1-iter):     {t_full:7.0f} ms (infeasible at 50 iters)")
    else:
        print(f"  full EP (1,800 ops):  INFEASIBLE on embedded (#7288-class)")
    print(f"  scaled full EP (1-iter, 300 ops): {t_sub:7.0f} ms")
    print(f"  local EP (50-iter≈): {t_local / max(it_local, 1) * 50:7.0f} ms")
    print(f"  write-back UNWIND:   {t_unwind:7.1f} ms (per-claim: {t_per_claim:.0f} ms)")
    proj.close()
    sdk.close()
    return 0


if __name__ == "__main__":
    random.seed(42)  # local to this script — never a global re-seed
    raise SystemExit(main())
