"""#1144 retrieval quality eval runner.

Seeds the latent-topic oracle corpus (benchmarks/synthetic_corpus.py) →
runs all four strategies + fused RRF per query (reusing
search_engine.degradation_chain) → computes graded metrics (nDCG@10,
P@5, R@10, MRR) per strategy and fused → paired 90% bootstrap CIs →
emits a JSON report with provenance.

Query sets:
  - 100 ORACLE queries (deterministic labels — no judges): 53 from the
    #316 query mix + 47 new; tiers 50/30/20 easy/medium/hard.
  - 50 AUTHORED queries over the real internal graph domain (subjective
    relevance → LLM judges + owner adjudication via judge.py). The runner
    pools their top-50/strategy/query (deduped) to --pool-out; metrics are
    computed only when adjudicated labels are merged via --judge-labels.

Modes:
  - embedded (default, smoke): an isolated embedded DB file. NO FTS/HNSW
    in embedded FalkorDBLite — the FTS strategy degrades to empty results
    (its production queryNodes path returns no rows on redislite) and
    structural degrades to empty without a kind; the vector arm falls back
    to brute-force and TF-IDF runs in-process. Synthetic semantic query
    vectors (topic-centroid stand-ins) replace the missing embedding model.
    Numbers are NOT prod-parity — Docker is authoritative.
  - Docker FalkorDB (authoritative): --db docker://... (or TORTOISE_DB_URI)
    with real FTS + HNSW indexes and (optionally) the all-MiniLM embedding
    model; vectors encode query semantics natively.

Usage:
    # embedded smoke (baseline)
    TORTOISE_DB_URI= python -m tests.eval.retrieval.run \
        --db /tmp/tortoise-eval.db --corpus-size 2000 --out report.json

    # authoritative (Docker FalkorDB >= 4.x, indexes auto-created at boot)
    python -m tests.eval.retrieval.run --db docker://falkor:6379

    # gate a new run against the baseline
    python -m tests.eval.retrieval.run --db /tmp/new.db --baseline baseline.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import random
import subprocess
import sys
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from benchmarks.synthetic_corpus import (  # noqa: E402
    build_topic_oracle,
    corpus_fingerprint_from_graph,
    generate_oracle_points,
    oracle_grades_for_query,
    seed_corpus,
    seed_operator_edges,
    verify_indexes,
)
from tests.eval.retrieval import bootstrap  # noqa: E402
from tests.eval.retrieval.judge import Pool, build_pool  # noqa: E402
from tests.eval.retrieval.metrics import (  # noqa: E402
    METRICS, aggregate, compute_metrics,
)
from tests.eval.retrieval.queries import (  # noqa: E402
    AUTHORED_QUERIES_PATH, ORACLE_QUERIES_PATH,
    build_oracle_query_set, load_authored_queries, load_oracle_queries,
)

STRATEGIES = ("fts", "vector", "structural", "tfidf", "fused")
SCHEMA_VERSION = 1
ELEVATED_TIMEOUT_MS = 5000   # quality measurement: never truncate strategy work
DEFAULT_CORPUS_SIZE = 2000
DEFAULT_LIMIT = 50           # pooling depth (top-50/strategy/query per spec)


# ── Provenance ──────────────────────────────────────────────────────────────

def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _host_specs() -> dict:
    specs = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "machine": platform.machine(),
    }
    return specs


def _capture_provenance(proj, is_embedded: bool, extras: dict) -> dict:
    prov = {
        "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "host": _host_specs(),
        "db_mode": "embedded-falkordblite" if is_embedded else "docker-falkordb",
        "tor_toise_db_uri_set": bool(os.environ.get("TORTOISE_DB_URI")),
        "indexes": extras.get("indexes", {}),
        "corpus": extras.get("corpus_fingerprint", {}),
        "oracle": extras.get("oracle_meta", {}),
        "query_mix": extras.get("query_mix_meta", {}),
        "limit": extras.get("limit", DEFAULT_LIMIT),
        "elevated_timeout_ms": ELEVATED_TIMEOUT_MS,
        "synthetic_query_vectors": extras.get("synthetic_query_vectors", False),
        "judge_labels": extras.get("judge_labels", "none"),
    }
    if is_embedded:
        prov["embedded_engine"] = (
            "redislite (no FTS/HNSW — FTS degrades to empty, structural to "
            "empty without kind, vector brute-force; numbers NOT prod-parity)"
        )
    return prov


# ── Retrieval ───────────────────────────────────────────────────────────────

def retrieve_per_strategy(
    graph, query, kind, query_vec, tfidf_points, is_embedded,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, list[tuple[str, float]]]:
    """Run all strategies + fused RRF for ONE query (isolation arms).

    Reuses degradation_chain (parallel FTS/vector/structural with the
    per-strategy circuit breakers) and rrf_fusion — the production path.
    TF-IDF is the in-memory fallback (not part of the chain); structural
    returns [] for kind-less queries (honest: it cannot rank text).
    Returns {strategy: [(point_id, score)]} for fts/vector/structural/
    tfidf/fused, each ranked best-first, truncated to `limit` per strategy.
    """
    from tortoise.search_engine import (
        classify_query, degradation_chain, fallback_tfidf, rrf_fusion,
    )

    strategies = classify_query(query, kind)
    raw = degradation_chain(
        graph, query, kind, query_vec, strategies,
        entity_type="point", limit=limit, is_embedded=is_embedded,
        elevated_timeout_ms=ELEVATED_TIMEOUT_MS,
    )
    out: dict[str, list[tuple[str, float]]] = {
        "fts": raw.get("fts", []),
        "vector": raw.get("vector", []),
        "structural": raw.get("structural", []),
    }
    tf = fallback_tfidf(query, tfidf_points, limit=limit)
    out["tfidf"] = [(d["id"], float(d.get("similarity", 0.0))) for d in tf]
    out["fused"] = list(rrf_fusion(
        [lst for lst in (out["fts"], out["vector"], out["structural"]) if lst]
    ).items())
    return out


def _point_ids(ranked: list[tuple[str, float]]) -> list[str]:
    return [pid for pid, _score in ranked]


def _tfidf_snapshot(graph) -> list[dict]:
    rows = graph.query(
        "MATCH (n:Point) WHERE n.is_operator <> true "
        "RETURN n.id, n.content, n.pointKind"
    ).result_set
    return [
        {"id": r[0], "content": r[1] or "", "pointKind": r[2] or ""} for r in rows
    ]


# ── Per-query metrics ───────────────────────────────────────────────────────

def _run_oracle_query(q: dict, ctx: dict, oracle) -> dict:
    """One oracle query → per-strategy ranked ids + metrics vs oracle labels."""
    ranked = retrieve_per_strategy(
        ctx["graph"], q["query"], q.get("kind"), ctx["vecs"].get(q["id"]),
        ctx["tfidf_points"], ctx["is_embedded"], ctx["limit"],
    )
    labels = oracle_grades_for_query(
        oracle, q["oracle_target"], ctx["live_ids_by_topic"],
    )
    metrics = {
        strat: compute_metrics(_point_ids(ranked[strat]), labels)
        for strat in STRATEGIES
    }
    return ranked, metrics


def _run_authored_query(q: dict, ctx: dict) -> dict[str, list[tuple[str, float]]]:
    return retrieve_per_strategy(
        ctx["graph"], q["query"], q.get("kind"), ctx["vecs"].get(q["id"]),
        ctx["tfidf_points"], ctx["is_embedded"], ctx["limit"],
    )


def _query_vecs(oracle, queries, seed: int) -> dict[str, list[float]]:
    """Precompute one query vector per query (outside the measured window).

    Embedded mode: synthetic semantic stand-ins derived from the oracle
    topic structure (see TopicOracle.query_vector_for). Docker mode: the
    real embedding model when available (tortoise.embeddings.EmbeddingModel),
    else the same synthetic stand-in (flagged in provenance).
    """
    use_model = False
    try:
        from tortoise.embeddings import EmbeddingModel
        use_model = EmbeddingModel.get() is not None
    except Exception:
        use_model = False
    vecs: dict[str, list[float]] = {}
    for q in queries:
        text = q["query"]
        if use_model:
            from tortoise.embeddings import EmbeddingModel
            try:
                vecs[q["id"]] = EmbeddingModel.get().encode([text])[0].tolist()
                continue
            except Exception:
                pass
        vecs[q["id"]] = oracle.query_vector_for(text, seed)
    return vecs, use_model


# ── Aggregation ─────────────────────────────────────────────────────────────

def _metric_series(per_query: dict[str, dict], strategy: str, metric: str) -> list[float]:
    return [per_query[qid][strategy][metric] for qid in sorted(per_query)]


def _aggregate_with_ci(per_query, strategy, rng) -> dict:
    out = {}
    for metric in METRICS:
        series = _metric_series(per_query, strategy, metric)
        ci = bootstrap.one_sample_ci(series, rng=rng)
        out[metric] = {"value": aggregate({metric: series})[metric], "ci": ci.to_dict()}
    return out


def _paired_vs_fused(per_query, rng) -> dict:
    """Paired 90% CIs on (fused − strategy) deltas, in points (×100)."""
    out = {}
    for strat in ("fts", "vector", "structural", "tfidf"):
        entry = {}
        for metric in ("ndcg@10", "p@5"):
            deltas = [
                (per_query[qid]["fused"][metric] - per_query[qid][strat][metric]) * 100.0
                for qid in sorted(per_query)
            ]
            entry[f"{metric}_delta_points"] = bootstrap.paired_bootstrap_ci(
                deltas, rng=rng,
            ).to_dict()
        out[strat] = entry
    return out


def _gate_vs_baseline(per_query, baseline_per_query, rng) -> dict | None:
    if not baseline_per_query:
        return None
    ndcg_deltas = []
    p5_deltas = []
    for qid in sorted(per_query):
        b = baseline_per_query.get(qid)
        if not b or "fused" not in b:
            continue
        ndcg_deltas.append((per_query[qid]["fused"]["ndcg@10"]
                            - b["fused"]["ndcg@10"]) * 100.0)
        p5_deltas.append((per_query[qid]["fused"]["p@5"]
                          - b["fused"]["p@5"]) * 100.0)
    if not ndcg_deltas:
        return None
    return bootstrap.quality_gate(ndcg_deltas, p5_deltas, rng=rng).to_dict()


# ── Runner ──────────────────────────────────────────────────────────────────

def run_eval(args) -> dict:
    from tortoise.sdk import TortoiseSDK

    db_arg = args.db
    if db_arg is None and os.environ.get("TORTOISE_DB_URI"):
        db_arg = os.environ["TORTOISE_DB_URI"]
    if db_arg is None:
        db_arg = str(Path(__file__).resolve().parent / "reports" / "eval.db")
    is_uri = "://" in db_arg
    if is_uri:
        os.environ.setdefault("TORTOISE_DB_URI", db_arg)
        sdk = TortoiseSDK()
    else:
        sdk = TortoiseSDK(db_arg)
    try:
        return _run_with_sdk(args, sdk)
    finally:
        try:
            sdk.close()
        except Exception:
            pass


def _run_with_sdk(args, sdk) -> dict:
    proj = sdk._get_proj()
    is_embedded = getattr(proj, "_is_embedded", True)
    rng = random.Random(args.seed * 65537)  # bootstrap/CI resampling rng

    # 1. Oracle + corpus.
    oracle = build_topic_oracle(args.seed)
    points, topic_counts = generate_oracle_points(args.corpus_size, oracle, seed=args.seed)
    seeded = 0
    if not args.no_seed_corpus:
        seeded = seed_corpus(proj.g, points)
        edges = seed_operator_edges(proj.g, random.Random(args.seed), n_edges_per_op=200)
        if not args.quiet:
            print(f"[corpus] seeded {seeded} points, {edges} operator edges")
    else:
        if not args.quiet:
            print("[corpus] using existing corpus (--no-seed-corpus)")
    corpus_fp = corpus_fingerprint_from_graph(proj.g)
    assert corpus_fp["n_points"] > 0, "structural non-empty assertion failed"
    indexes = verify_indexes(proj)
    if not args.quiet:
        print(f"[indexes] fts={indexes['fts']} vector={indexes['vector']} "
              f"(mode={'embedded' if is_embedded else 'docker'})")

    # Live (non-operator, non-retracted) point ids by topic — the oracle's
    # graded sets. Operators are meta-points, excluded like the TF-IDF arm.
    live_ids_by_topic: dict[int, list[str]] = {k: [] for k in oracle.core}
    for p in points:
        if p["is_operator"] or p["status"] == "retracted":
            continue
        live_ids_by_topic[p["topic"]].append(p["id"])

    # 2. Query sets.
    oracle_set = load_oracle_queries()
    authored_set = load_authored_queries()
    if args.rebuild_queries:
        rebuilt = build_oracle_query_set(oracle, seed=args.seed)
        ORACLE_QUERIES_PATH.write_text(json.dumps(rebuilt, indent=2) + "\n")
        AUTHORED_QUERIES_PATH.write_text(
            json.dumps({
                "meta": {
                    "issue": "1144",
                    "name": "authored retrieval queries over the real internal "
                            "graph domain (anchor slice)",
                    "n_queries": len(AUTHORED_QUERIES),
                    "labels": (
                        "subjective — judged by two cross-vendor LLM judges + "
                        "owner adjudication (judge.py); no logs required"
                    ),
                },
                "queries": AUTHORED_QUERIES,
            }, indent=2) + "\n"
        )
        oracle_set = load_oracle_queries()
        authored_set = load_authored_queries()

    all_queries = oracle_set["queries"] + authored_set["queries"]
    vecs, use_model = _query_vecs(oracle, all_queries, args.seed)
    tfidf_points = _tfidf_snapshot(proj.g)
    ctx = {
        "graph": proj.g, "is_embedded": is_embedded,
        "tfidf_points": tfidf_points, "vecs": vecs, "limit": args.limit,
        "live_ids_by_topic": live_ids_by_topic,
    }

    # 3. Oracle queries: per-strategy ranked lists + metrics.
    oracle_per_query: dict[str, dict] = {}
    oracle_pool_results: dict[str, dict] = {}
    for q in oracle_set["queries"]:
        ranked, metrics = _run_oracle_query(q, ctx, oracle)
        oracle_per_query[q["id"]] = metrics
        oracle_pool_results[q["id"]] = {
            "_query": q["query"],
            **{strat: [
                {"id": pid, "content": next(
                    (p["content"] for p in points if p["id"] == pid), ""),
                 "point_kind": next(
                    (p["pointKind"] for p in points if p["id"] == pid), "")}
                for pid, _s in ranked[strat]
            ] for strat in STRATEGIES},
        }

    # 4. Authored queries: pooled top-50s (for the judges) + metrics when
    #    adjudicated labels are merged.
    authored_meta = {"n_queries": len(authored_set["queries"]), "labels_status": "pending"}
    authored_pool_results: dict[str, dict] = {}
    authored_per_query: dict[str, dict] = {}
    judge_labels: dict[str, dict[str, int]] | None = None
    if args.judge_labels:
        judge_labels = json.loads(Path(args.judge_labels).read_text())
    for q in authored_set["queries"]:
        ranked = _run_authored_query(q, ctx)
        authored_pool_results[q["id"]] = {
            "_query": q["query"],
            **{strat: [
                {"id": pid, "content": next(
                    (p["content"] for p in points if p["id"] == pid), ""),
                 "point_kind": next(
                    (p["pointKind"] for p in points if p["id"] == pid), "")}
                for pid, _s in ranked[strat]
            ] for strat in STRATEGIES},
        }
        if judge_labels and q["id"] in judge_labels:
            labels = judge_labels[q["id"]]
            authored_per_query[q["id"]] = {
                strat: compute_metrics(_point_ids(ranked[strat]), labels)
                for strat in STRATEGIES
            }
    if judge_labels and authored_per_query:
        authored_meta["labels_status"] = "adjudicated"
        authored_meta["n_labeled"] = len(authored_per_query)
    elif judge_labels:
        authored_meta["labels_status"] = "labels-file-no-matching-queries"

    # 5. Aggregate + CIs.
    per_strategy = {
        strat: _aggregate_with_ci(oracle_per_query, strat, rng)
        for strat in STRATEGIES
    }
    by_tier: dict[str, dict] = {}
    for tier in ("easy", "medium", "hard"):
        tier_qids = [q["id"] for q in oracle_set["queries"] if q["tier"] == tier]
        by_tier[tier] = {
            strat: {
                metric: aggregate({metric: [
                    oracle_per_query[qid][strat][metric] for qid in tier_qids
                ]})[metric]
                for metric in METRICS
            }
            for strat in STRATEGIES
        }

    paired = _paired_vs_fused(oracle_per_query, rng)
    gate = None
    if args.baseline:
        base = json.loads(Path(args.baseline).read_text())
        gate = _gate_vs_baseline(
            oracle_per_query, base.get("per_query", {}), rng,
        )

    # 6. Authored pool emission (top-50/strategy/query, deduped per query).
    pool_out_path = None
    if args.pool_out and authored_pool_results:
        pool = build_pool(authored_pool_results, corpus_fp, list(STRATEGIES))
        Path(args.pool_out).write_text(json.dumps(pool.to_dict(), indent=2) + "\n")
        pool_out_path = args.pool_out

    authored_agg = (
        {strat: _aggregate_with_ci(authored_per_query, strat, rng)
         for strat in STRATEGIES}
        if authored_per_query else {}
    )

    report = {
        "schema_version": SCHEMA_VERSION,
        "issue": "1144",
        "seed": args.seed,
        "corpus_target": args.corpus_size,
        "seeded_points": seeded,
        "limit": args.limit,
        "strategies": list(STRATEGIES),
        "oracle": {
            "n_queries": len(oracle_set["queries"]),
            "tiers": oracle_set["meta"]["tiers"],
            "composition": oracle_set["meta"]["composition"],
            "query_mix_excluded": oracle_set["meta"]["query_mix_excluded"],
            "structure": oracle.to_dict(),
        },
        "authored": authored_meta,
        "authored_pool": pool_out_path,
        "metrics": per_strategy,
        "by_tier": by_tier,
        "paired_vs_fused": paired,
        "gate_vs_baseline": gate,
        "per_query": {qid: oracle_per_query[qid] for qid in sorted(oracle_per_query)},
        "authored_metrics": authored_agg,
        "provenance": _capture_provenance(proj, is_embedded, {
            "indexes": indexes,
            "corpus_fingerprint": corpus_fp,
            "oracle_meta": oracle_set["meta"],
            "query_mix_meta": oracle_set["meta"]["composition"],
            "limit": args.limit,
            "synthetic_query_vectors": not use_model,
            "judge_labels": args.judge_labels or "none",
        }),
        "notes": [
            "Embedded FalkorDBLite: NO FTS/HNSW — FTS degrades to EMPTY "
            "(the production parameter-bound queryNodes path returns no rows "
            "on redislite), structural degrades to empty for kind-less "
            "queries, vector runs brute-force. FTS/structural columns on "
            "embedded runs are measurement-environment artifacts, not quality "
            "statements — Docker FalkorDB >= 4.x is authoritative.",
            "On embedded, fused RRF = vector alone (FTS/structural empty).",
            "Query vectors on embedded are SYNTHETIC SEMANTIC stand-ins "
            "(topic-centroid sums from the oracle structure + seeded noise) "
            "because no embedding model is loaded — same information a real "
            "embedding model encodes (token semantics), not oracle labels.",
            "R@10 uses the oracle denominator = the grade-2 target set "
            "(~77-84 points at corpus 2000/24 topics) — the ceiling is "
            "10/|target| (~12%), so compare R@10 RELATIVELY across "
            "strategies; the recall diagnostic for retrieval-gap filing is "
            "in the top-50 pool.",
            "Authored queries: relevance is subjective → LLM judges + owner "
            "adjudication (judge.py); baseline reports them labels_pending "
            "until the emitted pool is judged and merged via --judge-labels.",
            "Paired deltas (fused vs per-strategy and gate-vs-baseline) are "
            "90% paired bootstrap CIs on per-query deltas in POINTS (x100).",
        ],
    }
    return report


def write_outputs(report: dict, out_path: str | None) -> str:
    if out_path is None:
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = str(Path(__file__).resolve().parent / "reports"
                       / f"{ts}-retrieval-eval.json")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(report, indent=2) + "\n")
    return out_path


def _markdown_summary(report: dict) -> str:
    lines = ["| strategy | nDCG@10 | P@5 | R@10 | MRR |", "|---|---|---|---|---|"]
    for strat in STRATEGIES:
        m = report["metrics"][strat]
        lines.append(
            f"| {strat} | {m['ndcg@10']['value']:.3f} | {m['p@5']['value']:.3f} "
            f"| {m['r@10']['value']:.3f} | {m['mrr']['value']:.3f} |"
        )
    g = report.get("gate_vs_baseline")
    gate_line = f"gate vs baseline: {g['verdict']}" if g else "gate: none (no --baseline)"
    return "\n".join(lines + ["", gate_line])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m tests.eval.retrieval.run",
        description="Issue #1144 retrieval quality eval runner (per-strategy "
        "+ fused graded metrics, paired bootstrap CIs, baseline gate).",
    )
    p.add_argument("--db", help="FalkorDB URI (docker://|bolt://) or embedded "
                   "db file path (default: $TORTOISE_DB_URI, else a fresh "
                   "embedded db under tests/eval/retrieval/reports/)")
    p.add_argument("--corpus-size", type=int, default=DEFAULT_CORPUS_SIZE)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help="pooling depth per strategy (top-K/strategy/query)")
    p.add_argument("--out", help="report JSON path")
    p.add_argument("--pool-out", help="write the authored-query judge pool "
                   "JSON (top-50/strategy/query, deduped)")
    p.add_argument("--judge-labels", help="merged adjudicated labels JSON "
                   "(judge.py --apply-rulings --out) → authored metrics")
    p.add_argument("--baseline", help="baseline report JSON → quality gate "
                   "(SHIP/WARN/BLOCK vs baseline, paired on query ids)")
    p.add_argument("--no-seed-corpus", action="store_true")
    p.add_argument("--rebuild-queries", action="store_true",
                   help="regenerate and overwrite the committed query JSONs")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    if not args.quiet:
        print(f"#1144 retrieval eval — corpus={args.corpus_size} "
              f"limit={args.limit} seed={args.seed}")
    report = run_eval(args)
    out = write_outputs(report, args.out)
    print("\n" + _markdown_summary(report))
    print(f"\nreport: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
