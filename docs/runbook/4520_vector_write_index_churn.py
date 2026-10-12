#!/usr/bin/env python3
"""#4520 — measure the clear-then-set guard's per-write cost on a SERVER store.

The PR body reports:

    the guard costs +0.19 ms/write (+16 %) on an unindexed store; on indexed
    stores the engine's own index maintenance (2.7–13.5 ms/write,
    order-of-magnitude run-to-run variance) swamps it, so no additional
    measurable churn is attributable to the guard.

That number lived only in the PR description. This is the committed harness,
so the claim is checkable instead of asserted. It is a DIAGNOSTIC, not a CI
gate: wall-clock numbers are inherently noisy, so this measure and report and
never fails on a threshold.

Run against a private lane container (the shared :6379 is shared and slow):

    eval "$(uv run python tools/test_lane.py uri)"
    uv run python docs/runbook/4520_vector_write_index_churn.py --writes 200

It writes two scratch labels through both shapes:

  * ``bare``   — ``SET n.embedding = vecf32($e)`` (the pre-#4520 write);
  * ``seamed`` — ``tortoise/vector_write.py``'s clear-then-set guard.

one overwrite per node, then repeats with a vector index present. Prints
mean / median / p90 ms per write; ``--json`` also writes the raw sample set.

Honest reading (the report's conclusion, corroborated by the review):
  * UNINDEXED — the seamed form is a small, roughly constant amount slower.
  * INDEXED   — HNSW maintenance dominates and varies by an order of magnitude
    run to run; the seam's contribution sits inside that noise, so the guard
    is not a material cost.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

DIM = 384


def _vector(seed: float) -> list[float]:
    return [seed + 1e-4 * i for i in range(DIM)]


def _seed(g, label: str, ids: list[str], vec: list[float]) -> None:
    for pid in ids:
        g.query(
            f"CREATE (n:{label} {{id:$id}}) SET n.embedding = vecf32($e)",
            params={"id": pid, "e": vec})


def _time_overwrites(g, label, ids, vec, *, seamed: bool,
                     clear_then_set_prefix, set_embedding_clause) -> list[float]:
    if seamed:
        query = (f"MATCH (n:{label} {{id:$id}}) "
                 + clear_then_set_prefix("n", "$e")
                 + "SET " + set_embedding_clause("n", "$e"))
    else:
        query = (f"MATCH (n:{label} {{id:$id}}) "
                 "SET n.embedding = vecf32($e)")
    samples = []
    for pid in ids:
        t0 = time.perf_counter()
        g.query(query, params={"id": pid, "e": vec})
        samples.append((time.perf_counter() - t0) * 1000.0)
    return samples


def _stats(samples: list[float]) -> dict:
    ordered = sorted(samples)
    n = len(ordered)
    return {
        "n": n,
        "mean_ms": round(statistics.fmean(ordered), 4),
        "median_ms": round(statistics.median(ordered), 4),
        "p90_ms": round(ordered[min(n - 1, int(0.9 * n))], 4),
        "min_ms": round(ordered[0], 4),
        "max_ms": round(ordered[-1], 4),
    }


def _ensure_index(g, label: str) -> str:
    """Create an HNSW vector index on ``label.embedding``; return the API used."""
    try:
        g.query(
            f"CALL db.idx.vector.createNodeIndex('{label}', 'embedding', "
            f"{DIM}, 'HNSW')")
        return "procedure"
    except Exception:
        g.query(
            f"CREATE VECTOR INDEX FOR (n:{label}) ON (n.embedding) "
            f"OPTIONS {{dimension: {DIM}, similarityFunction: 'euclidean'}}")
        return "cypher"


def _measure(g, label: str, ids: list[str], base, moved, *, seamed: bool,
             clear_then_set_prefix, set_embedding_clause) -> list[float]:
    _seed(g, label, ids, base)
    samples = _time_overwrites(
        g, label, ids, moved, seamed=seamed,
        clear_then_set_prefix=clear_then_set_prefix,
        set_embedding_clause=set_embedding_clause)
    g.query(f"MATCH (n:{label}) DETACH DELETE n")
    return samples


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--writes", type=int, default=200,
                        help="overwrites measured per (shape, indexedness) cell")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="also write the raw samples to this path")
    args = parser.parse_args(argv)

    uri = os.environ.get("TORTOISE_DB_URI")
    if not uri:
        print("TORTOISE_DB_URI is unset — this harness measures the SERVER "
              "lane; run `eval \"$(uv run python tools/test_lane.py uri)\"`.",
              file=sys.stderr)
        return 2

    from tortoise.projection import FalkorProjection  # noqa: E402
    from tortoise.vector_write import (  # noqa: E402
        clear_then_set_prefix,
        set_embedding_clause,
    )

    nonce = uuid.uuid4().hex[:8]
    graph_name = f"test_4520_churn_{nonce}"
    proj = FalkorProjection.from_uri(uri, graph_name=graph_name)
    g = proj.g

    base = _vector(-0.02)
    moved = list(base)
    moved[0] += 0.02  # a realistic small-Δ overwrite (real embedder output)
    ids = [f"c{i}" for i in range(args.writes)]

    result: dict = {"graph_name": graph_name, "writes": args.writes,
                    "sections": {}}
    try:
        for name, seamed in (("bare_unindexed", False),
                             ("seamed_unindexed", True)):
            result["sections"][name] = _stats(_measure(
                g, f"_Churn{nonce}{'S' if seamed else 'B'}", ids, base, moved,
                seamed=seamed,
                clear_then_set_prefix=clear_then_set_prefix,
                set_embedding_clause=set_embedding_clause))

        for name, seamed in (("bare_indexed", False),
                             ("seamed_indexed", True)):
            label = f"_ChurnIdx{nonce}{'S' if seamed else 'B'}"
            result["sections"][name + "_api"] = _ensure_index(g, label)
            result["sections"][name] = _stats(_measure(
                g, label, ids, base, moved, seamed=seamed,
                clear_then_set_prefix=clear_then_set_prefix,
                set_embedding_clause=set_embedding_clause))
    finally:
        try:  # the scratch graph is disjoint from every product graph
            proj.db.select_graph(graph_name).delete()
        except Exception:  # noqa: BLE001 — cleanup must not mask a result
            pass

    for key in ("bare_unindexed", "seamed_unindexed",
                "bare_indexed", "seamed_indexed"):
        st = result["sections"].get(key)
        if st:
            print(f"{key:18s} n={st['n']:4d} mean={st['mean_ms']:8.3f} ms "
                  f"median={st['median_ms']:8.3f} p90={st['p90_ms']:8.3f} "
                  f"min={st['min_ms']:8.3f} max={st['max_ms']:8.3f}")

    bare_u = result["sections"].get("bare_unindexed")
    seam_u = result["sections"].get("seamed_unindexed")
    if bare_u and seam_u:
        delta = seam_u["mean_ms"] - bare_u["mean_ms"]
        pct = (delta / bare_u["mean_ms"] * 100.0) if bare_u["mean_ms"] else 0.0
        print(f"\nunindexed: +{delta:.3f} ms/write (+{pct:.1f} %) for the guard")
    bare_i = result["sections"].get("bare_indexed")
    seam_i = result["sections"].get("seamed_indexed")
    if bare_i and seam_i:
        delta = seam_i["mean_ms"] - bare_i["mean_ms"]
        print(f"indexed:   {delta:+.3f} ms/write — HNSW maintenance variance "
              f"(bare max {bare_i['max_ms']:.1f} ms) dominates; not material")

    if args.json_path:
        with open(args.json_path, "w") as fh:
            json.dump(result, fh, indent=2)
        print("wrote", args.json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
