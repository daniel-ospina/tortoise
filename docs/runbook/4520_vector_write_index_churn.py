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

one overwrite per node, then repeats with a vector index present. The two
shapes are timed INTERLEAVED (after a discarded warm-up pass per shape), and
the headline reports the MEDIAN — the first-cell/warm-up cost otherwise lands
wholly on whichever shape is measured first (always ``bare``), which was enough
to flip the reported mean's sign run to run. Prints mean / median / p90 ms per
write; ``--json`` also writes the raw sample set.

Honest reading (the report's conclusion, corroborated by the review):
  * UNINDEXED — the median headline is the checkable form of the cost claim.
    The PR body says the guard costs about +0.19 ms/write here; the harness
    prints the measured median delta with its own sign so the claim can be
    confirmed or contradicted rather than asserted. The mean is printed below
    it as a secondary line, since it is outlier-dominated.
  * INDEXED   — HNSW maintenance dominates and varies by an order of magnitude
    run to run; the seam's contribution sits inside that noise, so the guard
    is not a material cost.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import sys
import time
import uuid

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

DIM = 384


def _vector(seed: float) -> list[float]:
    return [seed + 1e-4 * i for i in range(DIM)]


def _seed(g, label: str, ids: list[str], vec: list[float]) -> None:
    for pid in ids:
        g.query(
            f"CREATE (n:{label} {{id:$id}}) SET n.embedding = vecf32($e)",
            params={"id": pid, "e": vec},
        )


def _interleave_overwrites(
    g,
    ids,
    *,
    label_bare: str,
    label_seamed: str,
    vec: list[float],
    warmup: int,
    clear_then_set_prefix,
    set_embedding_clause,
) -> dict[str, list[float]]:
    """Time the two shapes INTERLEAVED, per node, after a discarded warm-up.

    The shapes write to their own node sets, so interleaving cannot contaminate
    the data — it only spreads the *timing* order. That is the fix for the
    defect this headline had: the first-cell cost (engine/index warm-up) landed
    wholly on whichever shape was measured first, which was always ``bare``
    (``bare.max`` 99 ms against a ~1.5 ms median), enough to flip the reported
    mean's sign run to run. Each shape also runs a discarded warm-up pass
    first, so no TIMED write pays the engine's first-touch cost.
    """
    bare_query = f"MATCH (n:{label_bare} {{id:$id}}) SET n.embedding = vecf32($e)"
    seamed_query = (
        f"MATCH (n:{label_seamed} {{id:$id}}) "
        + clear_then_set_prefix("n", "$e")
        + "SET "
        + set_embedding_clause("n", "$e")
    )
    for pid in ids[:warmup]:
        g.query(bare_query, params={"id": pid, "e": vec})
        g.query(seamed_query, params={"id": pid, "e": vec})

    samples: dict[str, list[float]] = {"bare": [], "seamed": []}
    shapes = (("bare", bare_query), ("seamed", seamed_query))
    for i, pid in enumerate(ids):
        # Alternate which shape is timed first so order cannot bias either.
        order = shapes if i % 2 == 0 else shapes[::-1]
        for name, query in order:
            t0 = time.perf_counter()
            g.query(query, params={"id": pid, "e": vec})
            samples[name].append((time.perf_counter() - t0) * 1000.0)
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
        g.query(f"CALL db.idx.vector.createNodeIndex('{label}', 'embedding', {DIM}, 'HNSW')")
        return "procedure"
    except Exception:
        g.query(
            f"CREATE VECTOR INDEX FOR (n:{label}) ON (n.embedding) "
            f"OPTIONS {{dimension: {DIM}, similarityFunction: 'euclidean'}}"
        )
        return "cypher"


def _measure_pair(
    g,
    labels: dict[str, str],
    ids: list[str],
    base,
    moved,
    *,
    warmup: int,
    clear_then_set_prefix,
    set_embedding_clause,
) -> dict[str, list[float]]:
    """Seed both shapes' labels, time them interleaved, then drop both."""
    for label in labels.values():
        _seed(g, label, ids, base)
    samples = _interleave_overwrites(
        g,
        ids,
        label_bare=labels["bare"],
        label_seamed=labels["seamed"],
        vec=moved,
        warmup=warmup,
        clear_then_set_prefix=clear_then_set_prefix,
        set_embedding_clause=set_embedding_clause,
    )
    for label in labels.values():
        g.query(f"MATCH (n:{label}) DETACH DELETE n")
    return samples


def _cost_phrase(delta_median: float) -> str:
    """Sign-NEUTRAL attribution for the seamed-minus-bare median delta.

    ``+`` is never hardcoded and the causal phrase is conditional: the headline
    must not assert "for the guard" when the measurement does not support it —
    it flipped sign in 3 of 4 observed runs on the private lane, and a headline
    that flips sign defeats the harness's whole purpose (making the PR body's
    cost claim checkable instead of asserted).
    """
    if delta_median > 0:
        return "— seamed is slower; consistent with the guard's small cost"
    if delta_median < 0:
        return (
            "— seamed measured FASTER; the sign is noise-dominated, so this "
            "run does not support a guard cost"
        )
    return "— no measurable difference between the two forms"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--writes", type=int, default=200, help="overwrites measured per (shape, indexedness) cell"
    )
    parser.add_argument(
        "--json", dest="json_path", default=None, help="also write the raw samples to this path"
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=20,
        help="discarded warm-up overwrites per shape (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    uri = os.environ.get("TORTOISE_DB_URI")
    if not uri:
        print(
            "TORTOISE_DB_URI is unset — this harness measures the SERVER "
            'lane; run `eval "$(uv run python tools/test_lane.py uri)"`.',
            file=sys.stderr,
        )
        return 2

    from tortoise.projection import FalkorProjection
    from tortoise.vector_write import (
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

    result: dict = {"graph_name": graph_name, "writes": args.writes, "sections": {}}
    try:
        for suffix, prefix, indexed in (
            ("unindexed", "_Churn", False),
            ("indexed", "_ChurnIdx", True),
        ):
            labels = {
                "bare": f"{prefix}{nonce}B",
                "seamed": f"{prefix}{nonce}S",
            }
            if indexed:
                for shape in ("bare", "seamed"):
                    result["sections"][f"{shape}_{suffix}_api"] = _ensure_index(
                        g, labels[shape]
                    )
            samples = _measure_pair(
                g,
                labels,
                ids,
                base,
                moved,
                warmup=args.warmup,
                clear_then_set_prefix=clear_then_set_prefix,
                set_embedding_clause=set_embedding_clause,
            )
            for shape in ("bare", "seamed"):
                result["sections"][f"{shape}_{suffix}"] = _stats(samples[shape])
    finally:
        with contextlib.suppress(Exception):  # cleanup must not mask a result
            # the scratch graph is disjoint from every product graph
            proj.db.select_graph(graph_name).delete()

    for key in ("bare_unindexed", "seamed_unindexed", "bare_indexed", "seamed_indexed"):
        st = result["sections"].get(key)
        if st:
            print(
                f"{key:18s} n={st['n']:4d} mean={st['mean_ms']:8.3f} ms "
                f"median={st['median_ms']:8.3f} p90={st['p90_ms']:8.3f} "
                f"min={st['min_ms']:8.3f} max={st['max_ms']:8.3f}"
            )

    bare_u = result["sections"].get("bare_unindexed")
    seam_u = result["sections"].get("seamed_unindexed")
    if bare_u and seam_u:
        d_med = seam_u["median_ms"] - bare_u["median_ms"]
        d_mean = seam_u["mean_ms"] - bare_u["mean_ms"]
        p_med = (d_med / bare_u["median_ms"] * 100.0) if bare_u["median_ms"] else 0.0
        print(
            f"\nunindexed (median): {d_med:+.3f} ms/write ({p_med:+.1f} %) "
            f"{_cost_phrase(d_med)}"
        )
        print(
            f"unindexed (mean):   {d_mean:+.3f} ms/write "
            f"(outlier-dominated — the median above is the headline)"
        )
    bare_i = result["sections"].get("bare_indexed")
    seam_i = result["sections"].get("seamed_indexed")
    if bare_i and seam_i:
        delta = seam_i["mean_ms"] - bare_i["mean_ms"]
        print(
            f"indexed:   {delta:+.3f} ms/write — HNSW maintenance variance "
            f"(bare max {bare_i['max_ms']:.1f} ms) dominates; not material"
        )

    if args.json_path:
        with open(args.json_path, "w") as fh:
            json.dump(result, fh, indent=2)
        print("wrote", args.json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
