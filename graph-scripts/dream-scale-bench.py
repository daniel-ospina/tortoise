#!/usr/bin/env python
"""#1338 — production-scale dreaming benchmark harness (operator-run).

The epic-903 diagnostics decision gate (``06-diagnostics.md``) was recorded at
the F5 synthetic scale (12 operators). The production range is 5k-500k
operators. This harness builds a synthetic graph at a CHOSEN scale and measures
the four quantities #1338 asks for at that scale:

  1. **Staleness-ranking query time** — ``analyze._stale_first_claims`` (both
     the unbounded ranking the I2 report reads and the ``LIMIT``-bounded window
     the scheduler reads), plus ``_stale_first_count_stamped``. This is the
     ``lastDreamedAt`` index's effectiveness at scale.
  2. **Window selection + per-pass budget adherence** — one
     ``Dreamer.dream_window(budget=B)`` pass: wall time, ``budget_used``,
     batches, convergence.
  3. **Warm-start (γ-skip) savings** — the DE2E-6b counter
     (``TortoiseEP._warm_skipped_updates``) and the wall-clock delta between a
     cold (``warm_start=False``) and a warm (``warm_start=True``) EP run over
     the SAME subgraph (the shape ``tests/test_epic903_warmstart.py`` pins at
     fixture scale).
  4. **Coverage-cycle time** — passes needed for one full cycle
     (``ceil(operators / budget)``) times the measured per-pass cost, for both
     the shipped default and the pre-#1338 200-op value.

Deliberately NOT a pytest test: a 100k-operator graph does not belong in the
merge gate. The CI-checked half of the same claims is
``tests/test_epic903_scale.py``; the numbers this harness prints are recorded in
``docs/epics/2026-08-13-903-dreaming-ep/06-diagnostics.md``.

Graph shape (deterministic; one chained region per operator so windows are not
trivially disjoint):

- ``n_ops`` operators (``is_operator=true``, live);
- ``n_ops + 1`` live claims;
- 2 IMPL edges per operator — ``op_i -> c_i`` and ``op_i -> c_i+1`` — so each
  operator is a real (2-endpoint, live) EP factor and ``max_hops=2`` reaches
  the neighbouring operators;
- a staleness spread over the claims (a share left NULL = never dreamed, the
  rest stamped at three fixed levels).

Usage (embedded, hermetic)::

    .venv/bin/python graph-scripts/dream-scale-bench.py --operators 5000

Usage (the docker lane — much faster bulk writes, and what the recorded
numbers were measured on)::

    TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tm1338_scale' \\
      .venv/bin/python graph-scripts/dream-scale-bench.py \\
        --operators 100000 --namespace tm1338scale --drop

``--drop`` removes the benchmark graph afterwards (only ever the graph named by
``--namespace``, never a default/tenant graph).
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"graph-scripts/dream-scale-bench.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python graph-scripts/dream-scale-bench.py`"
    )

import argparse
import json
import os
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

STAMP_OLD = "2026-01-15T00:00:00+00:00"
STAMP_MID = "2026-06-01T00:00:00+00:00"
STAMP_NEW = "2026-08-01T00:00:00+00:00"

#: Pre-#1338 default — the value the coverage-cycle comparison is against.
LEGACY_WINDOW_BUDGET = 200


def _build(proj, n_ops: int, *, null_frac: float, batch: int = 2_000) -> dict:
    """Build the synthetic graph with batched Cypher (no per-node SDK calls)."""
    n_claims = n_ops + 1
    n_null = int(n_claims * null_frac)
    t0 = time.perf_counter()
    claims = []
    for j in range(n_claims):
        if j < n_null:
            stamp = None
        elif j % 3 == 0:
            stamp = STAMP_OLD
        elif j % 3 == 1:
            stamp = STAMP_MID
        else:
            stamp = STAMP_NEW
        claims.append({"id": f"c_{j}", "content": f"claim {j}", "stamp": stamp})
    ops = [{"id": f"op_{i}"} for i in range(n_ops)]
    edges = []
    for i in range(n_ops):
        edges.append({"op": f"op_{i}", "claim": f"c_{i}"})
        edges.append({"op": f"op_{i}", "claim": f"c_{i + 1}"})

    def _chunks(rows):
        for s in range(0, len(rows), batch):
            yield rows[s:s + batch]

    for part in _chunks(claims):
        proj.g.query(
            "UNWIND $rows AS row CREATE (n:Point) "
            "SET n.id = row.id, n.content = row.content, "
            "n.is_operator = false, n.status = 'live', n.confidence = 0.5, "
            "n.lastDreamedAt = row.stamp, n.createdAt = $ts",
            params={"rows": part, "ts": STAMP_OLD},
        )
    for part in _chunks(ops):
        proj.g.query(
            "UNWIND $rows AS row CREATE (n:Point) "
            "SET n.id = row.id, n.content = row.id, "
            "n.is_operator = true, n.status = 'live', n.createdAt = $ts",
            params={"rows": part, "ts": STAMP_OLD},
        )
    for part in _chunks(edges):
        proj.g.query(
            "UNWIND $rows AS row "
            "MATCH (o:Point {id: row.op}), (c:Point {id: row.claim}) "
            "CREATE (o)-[:IMPL {direction: 'bidirectional', weight: 0.8}]->(c)",
            params={"rows": part},
        )
    return {
        "n_operators": n_ops,
        "n_claims": n_claims,
        "n_edges": len(edges),
        "n_never_dreamed": n_null,
        "build_seconds": round(time.perf_counter() - t0, 3),
    }


def _ms(fn, repeats: int = 3) -> tuple[float, object]:
    """Best-of-N wall time in ms (best-of, because the host is shared)."""
    best = None
    out = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        out = fn()
        dt = (time.perf_counter() - t0) * 1000.0
        best = dt if best is None else min(best, dt)
    return round(best, 1), out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--operators", type=int, default=100_000,
                    help="number of operators to build (default 100000)")
    ap.add_argument("--budget", type=int, default=None,
                    help="per-pass window budget (default: the shipped "
                         "DEFAULT_WINDOW_BUDGET)")
    ap.add_argument("--null-frac", type=float, default=0.25,
                    help="share of claims with no lastDreamedAt (default 0.25)")
    ap.add_argument("--namespace", default="tm1338_scale_bench",
                    help="graph namespace for the benchmark fixture")
    ap.add_argument("--batch", type=int, default=2_000,
                    help="rows per build query (a shared/loaded FalkorDB "
                         "trips the socket timeout on huge single statements)")
    ap.add_argument("--drop", action="store_true",
                    help="DROP the benchmark graph when finished")
    ap.add_argument("--json-out", default=None, help="write the metrics JSON here")
    args = ap.parse_args()

    from tortoise.dream import (DEFAULT_WINDOW_BUDGET,  # noqa: I001
                                resolve_window_budget)
    from tortoise.sdk import TortoiseSDK, _derive_graph_name

    os.environ.setdefault("TORTOISE_EP_REQUIRE_CALIBRATION", "0")
    budget = args.budget if args.budget is not None else resolve_window_budget(None)
    metrics: dict = {
        "issue": 1338,
        "budget": budget,
        "shipped_default_budget": DEFAULT_WINDOW_BUDGET,
        "legacy_default_budget": LEGACY_WINDOW_BUDGET,
        "db_uri_set": bool(os.environ.get("TORTOISE_DB_URI")),
    }

    sdk = TortoiseSDK(namespace=args.namespace)
    try:
        proj = sdk._get_proj()
        metrics.update(_build(proj, args.operators, null_frac=args.null_frac,
                              batch=args.batch))
        sdk._dirty_roots.clear()

        from tortoise.analyze import (_bfs_select_operators,  # noqa: I001
                                      _stale_first_claims,
                                      _stale_first_count_stamped)

        # ── 1. staleness ranking ────────────────────────────────────
        metrics["count_stamped_ms"], _ = _ms(
            lambda: _stale_first_count_stamped(proj))
        metrics["ranking_full_ms"], ranked = _ms(
            lambda: _stale_first_claims(proj, limit=None), repeats=1)
        metrics["ranking_topn_ms"], window_full = _ms(
            lambda: _stale_first_claims(proj, limit=budget), repeats=1)
        assert window_full == ranked[:budget], "window must be a ranking prefix"

        # ── 2. one bounded pass ─────────────────────────────────────
        dreamer = sdk._get_dreamer()
        t0 = time.perf_counter()
        r_pass = dreamer.dream_window(budget=budget)
        pass_seconds = time.perf_counter() - t0
        metrics["pass_seconds"] = round(pass_seconds, 3)
        metrics["pass_budget_used"] = r_pass["budget_used"]
        metrics["pass_batches"] = r_pass["batches"]
        metrics["pass_converged"] = bool(r_pass["converged_all"])
        metrics["pass_operators_deduped"] = r_pass["operators_deduped"]
        metrics["budget_adherent"] = r_pass["budget_used"] <= budget

        # ── 3. warm-start savings on the SAME subgraph ──────────────
        # The pass above persists edge messages; re-run its exact operator set
        # cold vs warm. Cold REMOVEs the persisted messages (#1242), so it must
        # run AFTER the warm read to keep the comparison on the same state.
        ops, _anchors = _bfs_select_operators(
            proj, window_full[:min(budget, len(window_full))],
            max_hops=2, rel_filter="IMPL|NAND", direction="both", op_cap=budget)
        seeds = sorted(ops)[:max(1, min(len(ops), budget))]
        ep = sdk._get_ep()

        def _run(warm: bool) -> tuple[float, int, bool]:
            t = time.perf_counter()
            _it, conv, _aff = dreamer._ep_run_batch(
                proj, list(seeds), 2, stamp=False, warm_start=warm)
            return time.perf_counter() - t, int(
                getattr(ep, "_warm_skipped_updates", 0) or 0), bool(conv)

        warm_s, warm_skips, warm_conv = _run(True)
        cold_s, cold_skips, cold_conv = _run(False)
        metrics["warmstart"] = {
            "subgraph_operators": len(seeds),
            "warm_seconds": round(warm_s, 3),
            "cold_seconds": round(cold_s, 3),
            "warm_gamma_skips": warm_skips,
            "cold_gamma_skips": cold_skips,
            "savings_pct": (round((cold_s - warm_s) / cold_s * 100.0, 1)
                            if cold_s > 0 else None),
            "warm_converged": warm_conv,
            "cold_converged": cold_conv,
        }

        # ── 4. coverage cycle ───────────────────────────────────────
        per_pass = max(1, r_pass["budget_used"])
        passes = -(-args.operators // per_pass)
        legacy_passes = -(-args.operators // LEGACY_WINDOW_BUDGET)
        metrics["coverage"] = {
            "operators": args.operators,
            "per_pass_operators": per_pass,
            "passes_to_cover": passes,
            "cycle_seconds_at_shipped_default": round(passes * pass_seconds, 1),
            "legacy_default_passes_to_cover": legacy_passes,
            "legacy_default_cycle_seconds": round(
                legacy_passes * pass_seconds, 1),
        }

        print(json.dumps(metrics, indent=2, sort_keys=True))
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    finally:
        if args.drop:
            try:
                proj = sdk._get_proj()
                name = getattr(proj, "_graph_name", None)
                # Only ever the graph THIS run minted. The expected name is the
                # SDK's OWN derivation (``_derive_graph_name``), not a hardcoded
                # ``org_{ns}`` pair: a ``test_*``/``tortoise_test*`` namespace
                # mints ``{ns}_tortoise``, so the pair refused a legitimate drop.
                # Anything else — a tenant graph, a default graph — is refused
                # rather than risk a GRAPH.DELETE against live data.
                expected = _derive_graph_name(namespace=args.namespace,
                                              graph_name=None)
                if name != expected:
                    raise RuntimeError(
                        f"refusing to drop a graph this run did not mint: "
                        f"{name!r} (expected {expected!r})")
                # GRAPH.DELETE is a Redis command, not Cypher: ``graph.query``
                # sends it as a Cypher body — FalkorDB parses it as one and
                # rejects it, and the old ``except`` swallowed that, so the
                # documented cleanup silently left the graph behind (#5084).
                # The canonical channel is the client's ``delete()``.
                proj.g.delete()
                print(f"# dropped graph {name}")
            except Exception as exc:
                print(f"# WARNING: drop failed: {exc}", file=sys.stderr)
        sdk.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
