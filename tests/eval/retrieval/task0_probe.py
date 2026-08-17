"""Task-0 cheap-falsification probe for issue #1348.

Runs on the COMMITTED corpus with the EXISTING harness internals (zero
enhancement code) to pre-register the falsification verdicts BEFORE any
enhancement/Phase-2 work:

- 0a. Depth curve verdict (delta 20→50 and 20→100 on fused nDCG@10) — feeds
      the pool-floor default decision (FLOOR-ACTION rule: SIGNAL requires
      delta >= 1.0 point at the ADOPTED depth, not just at 100).
- 0b. k-sweep: fused@{20,60,100} computed from IDENTICAL per-strategy lists
      (paired by construction — single run, no re-retrieval). Verdict driven
      by per-query population counts (embedded FTS populates 40/100 → 2-list
      fusion on that subset, where k CAN reorder the top-10; Docker leg is
      authoritative).
- 0c. Ceiling probe: oracle-greedy max nDCG@10 over the fused pool's grade
      composition (in-memory ranked lists + oracle_grades_for_query), at both
      eval limits 50 and production-parity 10. If ceiling - fused < 0.02 at
      the relevant limit, no reranker can produce an interpretable verdict on
      this corpus → GraphRanker verdict needs a real-EP corpus.

Usage:
    uv run python -m tests.eval.retrieval.task0_probe \
        --db /tmp/task0.db --corpus-size 2000 --seed 42 --quiet
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from benchmarks.synthetic_corpus import (  # noqa: E402
    build_topic_oracle,
    generate_oracle_points,
    oracle_grades_for_query,
    seed_corpus,
    seed_operator_edges,
)
from tests.eval.retrieval.metrics import (  # noqa: E402
    ndcg_at_k, precision_at_k, recall_at_k,
)
from tests.eval.retrieval.queries import load_oracle_queries  # noqa: E402

KS = (20, 60, 100)


def _run_probe(args) -> dict:
    from tortoise.sdk import TortoiseSDK

    db_arg = args.db or str(Path(__file__).resolve().parent / "reports" / "task0.db")
    is_uri = "://" in db_arg
    if is_uri:
        import os
        os.environ.setdefault("TORTOISE_DB_URI", db_arg)
        sdk = TortoiseSDK()
    else:
        sdk = TortoiseSDK(db_arg)
    try:
        return _probe(sdk, args)
    finally:
        try:
            sdk.close()
        except Exception:
            pass


def _probe(sdk, args) -> dict:
    from tests.eval.retrieval.run import retrieve_per_strategy, _query_vecs

    proj = sdk._get_proj()
    is_embedded = getattr(proj, "_is_embedded", True)

    # Seed the SAME corpus as the committed baseline (seed 42).
    oracle = build_topic_oracle(args.seed)
    points, topic_counts = generate_oracle_points(args.corpus_size, oracle, seed=args.seed)
    seeded = seed_corpus(proj.g, points)
    seed_operator_edges(proj.g, random.Random(args.seed), n_edges_per_op=200)

    live_ids_by_topic = {k: [] for k in oracle.core}
    for p in points:
        if p["is_operator"] or p["status"] == "retracted":
            continue
        live_ids_by_topic[p["topic"]].append(p["id"])

    oracle_set = load_oracle_queries()
    queries = oracle_set["queries"]
    vecs, use_model = _query_vecs(oracle, queries, args.seed)
    tfidf_points = []

    # Populate the tfidf snapshot like run.py does (point content only).
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.is_operator <> true RETURN n.id, n.content, n.pointKind"
    ).result_set
    tfidf_points = [{"id": r[0], "content": r[1] or "", "pointKind": r[2] or ""} for r in rows]

    ctx = {
        "graph": proj.g, "is_embedded": is_embedded,
        "tfidf_points": tfidf_points, "vecs": vecs,
        "live_ids_by_topic": live_ids_by_topic,
    }

    depth = args.depth
    per_query = {}      # qid -> {"fused": {k: ndcg@10}, "ranks": {strat: [ids]}, "labels": {...}}
    pop_counts = {"fts": 0, "vector": 0, "structural": 0}
    for q in queries:
        ranked = retrieve_per_strategy(
            ctx["graph"], q["query"], q.get("kind"), ctx["vecs"].get(q["id"]),
            ctx["tfidf_points"], ctx["is_embedded"], depth,
        )
        labels = oracle_grades_for_query(
            oracle, q["oracle_target"], ctx["live_ids_by_topic"],
        )
        for strat in ("fts", "vector", "structural"):
            if ranked.get(strat):
                pop_counts[strat] += 1
        # Paired k sweep: identical lists, only k differs.
        fused_lists = [lst for lst in (ranked.get("fts"), ranked.get("vector"),
                                       ranked.get("structural")) if lst]
        fused_by_k = {}
        if fused_lists:
            from tortoise.search_engine import rrf_fusion
            for k in KS:
                fused_by_k[k] = [pid for pid, _s in
                                 rrf_fusion(fused_lists, k=k).items()]
        per_query[q["id"]] = {
            "fused_by_k": fused_by_k,
            "ranks": {s: [pid for pid, _x in ranked.get(s, [])] for s in ("fts", "vector", "structural", "tfidf")},
            "fused_union": [pid for pid, _x in ranked.get("fused", [])],
            "labels": labels,
        }

    # 0a. Depth-curve verdict — fused nDCG@10 at the depths we have.
    #     (The harness reports are produced separately; here we compute the
    #     per-query paired deltas at THIS depth vs the baseline depth=50 using
    #     the same fused@60 ordering.)
    fused_ndcg = {}
    fused_p5 = {}
    for qid, d in per_query.items():
        fused_ndcg[qid] = ndcg_at_k(d["fused_by_k"][60], d["labels"])
        fused_p5[qid] = precision_at_k(d["fused_by_k"][60], d["labels"])

    # 0b. k-sweep verdict — count queries where k changes the top-10.
    k_reorders = {20: 0, 60: 0, 100: 0}
    k_ndcg = {k: {} for k in KS}
    for qid, d in per_query.items():
        top10_60 = set(d["fused_by_k"][60][:10])
        for k in KS:
            k_ndcg[k][qid] = ndcg_at_k(d["fused_by_k"][k], d["labels"])
            if k != 60 and set(d["fused_by_k"][k][:10]) != top10_60:
                k_reorders[k] += 1

    # 0c. Ceiling probe — oracle-greedy max nDCG@10 over the fused pool.
    def _ceiling(fused_ids, labels):
        # Grade the pool, sort greedy (grade 2 first, then 1), compute max nDCG@10.
        graded = sorted(
            fused_ids, key=lambda pid: labels.get(pid, 0), reverse=True)
        return ndcg_at_k(graded, labels)

    ceilings = {}
    for qid, d in per_query.items():
        ceilings[qid] = {
            "limit50": _ceiling(d["fused_union"], d["labels"]),
            "limit10": _ceiling(d["fused_union"][:10], d["labels"]),
        }

    # Aggregate.
    def _mean(dct):
        return round(sum(dct.values()) / len(dct), 6) if dct else 0.0

    result = {
        "seed": args.seed,
        "corpus_size": args.corpus_size,
        "depth": depth,
        "is_embedded": is_embedded,
        "n_queries": len(per_query),
        "population_counts": pop_counts,
        "fused_ndcg10_at_depth": _mean(fused_ndcg),
        "fused_p5_at_depth": _mean(fused_p5),
        "k_sweep": {
            "ndcg10": {str(k): _mean(k_ndcg[k]) for k in KS},
            "n_queries_top10_reordered_vs_60": k_reorders,
            "verdict": (
                "interpretable" if sum(1 for k in (20, 100) if k_reorders[k] > 0)
                else "no top-10 reorder on any query"
            ),
        },
        "ceiling_probe": {
            "mean_ceiling_limit50": _mean({q: c["limit50"] for q, c in ceilings.items()}),
            "mean_ceiling_limit10": _mean({q: c["limit10"] for q, c in ceilings.items()}),
            "mean_fused_limit50": _mean(fused_ndcg),
        },
        "notes": [
            "0b: embedded FTS populates ~40/100 queries → 2-list fusion on that subset; "
            "k-reorder verdict driven by per-query population counts; Docker leg is authoritative.",
            "0c: ceiling at limit10 is bounded by the 10-item returned list; "
            "pool-50 ceiling does not bound the limit-10 leg.",
        ],
    }
    return result


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m tests.eval.retrieval.task0_probe")
    p.add_argument("--db", help="FalkorDB URI or embedded db path")
    p.add_argument("--corpus-size", type=int, default=2000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--depth", type=int, default=50,
                   help="per-strategy retrieval depth for this probe leg")
    p.add_argument("--out", help="JSON output path (default: stdout)")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    result = _run_probe(args)
    blob = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(blob + "\n")
        print(f"wrote {args.out}")
    else:
        print(blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
