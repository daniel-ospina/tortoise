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
  - embedded (default, smoke): an isolated embedded DB file. NO HNSW vector
    index in embedded FalkorDBLite — the vector arm falls back to brute-force
    and TF-IDF runs in-process. **The FULLTEXT index EXISTS on embedded**
    (provenance indexes.fts=true; FTS populated 40/100 queries in the
    committed baseline) — the FTS strategy returns rows, not empty; structural
    degrades to empty without a kind. Synthetic semantic query vectors
    (topic-centroid stand-ins) replace the missing embedding model. Numbers
    are NOT prod-parity — Docker is authoritative.
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

    # 4-model surface (#1349): run the hard tier under a candidate embedder
    python -m tests.eval.retrieval.run --db /tmp/new.db --model arctic-s --query-prompt query
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

from benchmarks.synthetic_corpus import (  # noqa: E402, I001
    build_topic_oracle,
    corpus_fingerprint_from_graph,
    generate_oracle_points,
    oracle_grades_for_query,
    seed_corpus,
    seed_operator_edges,
    verify_indexes,
)
from tests.eval.retrieval import bootstrap  # noqa: E402
from tests.eval.retrieval.judge import Pool, build_pool  # noqa: E402, F401
from tests.eval.retrieval.metrics import (  # noqa: E402
    METRICS, aggregate, compute_metrics,
)
from tests.eval.retrieval.queries import (  # noqa: E402
    AUTHORED_QUERIES_PATH, ORACLE_QUERIES_PATH,
    build_oracle_query_set, load_authored_queries, load_oracle_queries,
)
from tools.embedder_probe import PROBE_MODELS, inject_model  # noqa: E402

STRATEGIES = ("fts", "vector", "structural", "tfidf", "fused")
# #1348: fused_rerank is an OPT-IN arm (--graph-ranker-arm) — it is NOT in
# the default STRATEGIES so default runs keep identical metric values and
# the committed v1 baseline reproduces byte-identically.
RERANK_STRATEGY = "fused_rerank"
SCHEMA_VERSION = 2
ELEVATED_TIMEOUT_MS = 5000   # quality measurement: never truncate strategy work
DEFAULT_CORPUS_SIZE = 2000
DEFAULT_LIMIT = 50           # pooling depth (top-50/strategy/query per spec)
KSWEEP = (20, 60, 100)       # #1348 k-sweep set (k=60 Cormack default)
# #1348 pool-semantics note: eval depth == eval limit (no x2 in this harness);
# SDK str_limit = limit x2, with env-only opt-in TORTOISE_POOL_FLOOR (no baked
# default). --limit = returned limit (metrics are computed against top-limit),
# --depth = per-strategy retrieval depth.


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


def _resolved_embedding_model(use_model: bool, injected: bool) -> str:
    """Truthful embedding-model identity for provenance (#1349 T3).

    --model active: the probe-recorded candidate hf_id (the probe state IS
    the swap proof — inject_model HARD FAILs before the run otherwise). No
    injection but a real model loaded: the probe state when present (in a
    warm in-process re-run the loaded singleton may be a previously-injected
    candidate — the probe state persists, so reporting its hf_id is truthful),
    else the default model id (the only model EmbeddingModel._load resolves
    without the probe). Degraded (no model — synthetic query vectors):
    'unavailable'.
    """
    from tools import embedder_probe
    state = embedder_probe.get_state()
    if injected:
        if state is not None:
            return str(state["hf_id"])
        return "unavailable"
    if use_model:
        if state is not None:
            return str(state["hf_id"])
        return embedder_probe.DEFAULT_MODEL_ID
    return "unavailable"


def _capture_provenance(proj, is_embedded: bool, extras: dict) -> dict:
    prov = {
        "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),  # noqa: UP017
        "git_sha": _git_sha(),
        "host": _host_specs(),
        "db_mode": "embedded-falkordblite" if is_embedded else "docker-falkordb",
        "tor_toise_db_uri_set": bool(os.environ.get("TORTOISE_DB_URI")),
        "embedding_model": extras.get("embedding_model", "unavailable"),
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
            "redislite (no HNSW vector index — vector runs brute-force; "
            "FULLTEXT index EXISTS on embedded (indexes.fts=true; FTS "
            "populated 40/100 queries in the committed baseline), structural "
            "empty without kind; numbers NOT prod-parity)"
        )
    return prov


# ── Retrieval ───────────────────────────────────────────────────────────────

def _fused_rrf(lsts: list[list[tuple[str, float]]], k: int = 60) -> list[tuple[str, float]]:
    """RRF fusion of non-empty ranked lists at a given k (pure function)."""
    from tortoise.search_engine import rrf_fusion
    if not lsts:
        return []
    return list(rrf_fusion(lsts, k=k).items())


def _rerank_fused(
    fused: list[tuple[str, float]], proj, *,
    k: int = 60, use_degree: bool = True,
    stub_projection=None,
) -> list[tuple[str, float]]:
    """#1348 fused_rerank arm: fuse → truncate to limit → rerank (production
    order sdk.py:8851→9004). Input tuples (pid, rrf_score) become result dicts
    carrying scores.rrf (the GraphRanker similarity contract).

    stub_projection: for the positive-control arm — a duck-typed projection
    whose .g.query returns oracle-grade-derived signals in _fetch_point_signals
    row shape; zero GraphRanker production changes (the seam already exists).
    use_degree=False: confidence-only ablation (degree term neutralized).
    """
    from tortoise.ranking import GraphRanker
    if not fused:
        return []
    # #1348 code-review P1 guard: a bool (argparse flag) must NEVER reach the
    # stub-projection seam — GraphRanker(True) would silently zero every boost
    # (True is not None → treated as a projection; _fetch_signals swallows the
    # AttributeError). Fail loudly instead of corrupting the measurement.
    if stub_projection is not None and not hasattr(stub_projection, "g"):
        raise TypeError(
            "stub_projection must be a projection-like object with .g (or None), "
            f"got {type(stub_projection).__name__!r} — do NOT pass the argparse flag")
    dicts = [{"id": pid, "scores": {"rrf": score}} for pid, score in fused]
    proj_for_rank = stub_projection if stub_projection is not None else proj
    if stub_projection is not None:
        # MECHANISM control: graph_boost-only (similarity_weight=0,
        # graph_boost_weight=1.0) — the query-conditioned perfect signal.
        ranker = GraphRanker(
            proj_for_rank, similarity_weight=0.0, graph_boost_weight=1.0,
            recency_weight=0.0, use_degree=use_degree,
        )
    else:
        ranker = GraphRanker(proj_for_rank, use_degree=use_degree)
    reranked = ranker.rerank(dicts, entity_type="point")
    return [(r["id"], r["graph_ranking"]["final_score"]) for r in reranked]


def retrieve_per_strategy(
    graph, query, kind, query_vec, tfidf_points, is_embedded,
    limit: int = DEFAULT_LIMIT,
    *,
    depth: int | None = None,
    k_sweep: bool = False,
    proj=None,
    graph_ranker_arm: bool = False,
    corpus_variant: str = "plain",
    oracle=None,
    live_ids_by_topic=None,
    stub_projection=None,
) -> dict[str, list[tuple[str, float]]]:
    """Run all strategies + fused RRF for ONE query (isolation arms).

    Reuses degradation_chain (parallel FTS/vector/structural with the
    per-strategy circuit breakers) and rrf_fusion — the production path.
    TF-IDF is the in-memory fallback (not part of the chain); structural
    returns [] for kind-less queries (honest: it cannot rank text).

    #1348: `depth` is the per-strategy retrieval depth (defaults to `limit`);
    fusion runs over FULL-DEPTH lists (matching production str_limit), then
    metrics truncate to `limit` at the caller. k_sweep=True adds fused@k for
    k in {20,60,100} (paired by construction — identical lists, only k
    differs). graph_ranker_arm=True adds the fused_rerank strategy.

    Returns {strategy: [(point_id, score)]} for fts/vector/structural/
    tfidf/fused[/fused_rerank]/fused@{20,60,100}, ranked best-first.
    """
    from tortoise.search_engine import (  # noqa: I001
        classify_query, degradation_chain, fallback_tfidf,
    )

    strategies = classify_query(query, kind)
    raw = degradation_chain(
        graph, query, kind, query_vec, strategies,
        entity_type="point", limit=depth or limit, is_embedded=is_embedded,
        elevated_timeout_ms=ELEVATED_TIMEOUT_MS,
    )
    out: dict[str, list[tuple[str, float]]] = {
        "fts": raw.get("fts", []),
        "vector": raw.get("vector", []),
        "structural": raw.get("structural", []),
    }
    tf = fallback_tfidf(query, tfidf_points, limit=depth or limit)
    out["tfidf"] = [(d["id"], float(d.get("similarity", 0.0))) for d in tf]

    chain_lists = [lst for lst in (out["fts"], out["vector"], out["structural"]) if lst]
    out["fused"] = _fused_rrf(chain_lists, k=60)
    if k_sweep:
        for k in KSWEEP:
            out[f"fused@{k}"] = _fused_rrf(chain_lists, k=k)

    # #1348 fused_rerank arm: fuse → truncate to limit → rerank.
    if graph_ranker_arm and proj is not None:
        fused_trunc = out["fused"][:limit]
        # Positive control (--stub-projection) passes a per-query stub built
        # by the runner; the ON arm uses the real projection on the corpus.
        out[RERANK_STRATEGY] = _rerank_fused(
            fused_trunc, proj, k=60, use_degree=(corpus_variant != "enhanced-conf-only"),
            stub_projection=stub_projection,
        )
    return out


class _StubOracleProjection:
    """#1348 positive-control seam: duck-typed projection whose .g.query
    returns oracle-grade-derived signals in _fetch_point_signals row shape
    [pid, conf, degree, created, alpha, beta, has_ep]. Query-conditioned per
    query (grades come from oracle_grades_for_query for THIS query's target).
    """

    def __init__(self, oracle, live_ids_by_topic, query):
        self.oracle = oracle
        self.live_ids_by_topic = live_ids_by_topic
        self._query_text = query  # NOT self.query — would shadow the method
        self._grades: dict[str, int] | None = None
        self.g = self  # projection.g is the query surface

    def _resolve_target(self) -> int | None:
        # The query dict's oracle_target is not available here; callers pass
        # the target via a module-level current-query hook set by the runner.
        return getattr(_STUB_CURRENT, "oracle_target", None)

    def query(self, cypher: str, params: dict | None = None):
        from benchmarks.synthetic_corpus import oracle_grades_for_query
        ids = (params or {}).get("ids") or []
        target = self._resolve_target()
        if target is None or not ids:
            rows = []
        else:
            grades = oracle_grades_for_query(
                self.oracle, target, self.live_ids_by_topic)
            rows = []
            for pid in ids:
                g = grades.get(pid, 0)
                conf = 1.0 if g >= 2 else (0.5 if g == 1 else 0.0)
                rows.append([pid, conf, 0, None, 1.0, 1.0, False])
        return _StubResult(rows)


class _StubResult:
    def __init__(self, rows):
        self.result_set = rows


class _CurrentQuery:
    oracle_target: int | None = None


_STUB_CURRENT = _CurrentQuery()


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

def _run_oracle_query(q: dict, ctx: dict, oracle) -> tuple[dict, dict, dict]:
    """One oracle query → (ranked, metrics, k_metrics).

    #1348: metrics computed on each strategy's top-`limit` (k-capped by
    compute_metrics); fused_rerank mirrors production fuse→truncate→rerank.
    k_metrics is the fused@{20,60,100} dict when k_sweep is on, else {} — kept
    SEPARATE from per_query so per_query stays strategy-keyed (P2-E fix).
    """
    stub = None
    if ctx.get("stub_projection") and ctx.get("graph_ranker_arm"):
        # #1348 positive control: a per-query duck-typed stub projection whose
        # .g.query returns oracle-grade-derived signals (query-conditioned).
        stub = _StubOracleProjection(
            ctx.get("oracle"), ctx.get("live_ids_by_topic"), q["query"])
        _STUB_CURRENT.oracle_target = q.get("oracle_target")
    try:
        ranked = retrieve_per_strategy(
            ctx["graph"], q["query"], q.get("kind"), ctx["vecs"].get(q["id"]),
            ctx["tfidf_points"], ctx["is_embedded"], ctx["limit"],
            depth=ctx.get("depth"), k_sweep=ctx.get("k_sweep", False),
            proj=ctx.get("proj"), graph_ranker_arm=ctx.get("graph_ranker_arm", False),
            corpus_variant=ctx.get("corpus_variant", "plain"),
            oracle=ctx.get("oracle"), live_ids_by_topic=ctx.get("live_ids_by_topic"),
            stub_projection=stub,
        )
    finally:
        _STUB_CURRENT.oracle_target = None
    labels = oracle_grades_for_query(
        oracle, q["oracle_target"], ctx["live_ids_by_topic"],
    )
    metrics = {
        strat: compute_metrics(_point_ids(ranked[strat])[:ctx["limit"]], labels)
        for strat in ctx["strategies"]
    }
    # #1348 k-sweep: fused@{20,60,100} metrics are returned SEPARATELY (NOT
    # added to per_query — keeps the schema contract "arm-OFF per_query keys ==
    # active strategies" true; the report's k_sweep section is built from
    # k_metrics). P2-E code-review fix.
    k_metrics = {}
    if ctx.get("k_sweep"):
        for k in KSWEEP:
            k_metrics[str(k)] = compute_metrics(
                _point_ids(ranked.get(f"fused@{k}", []))[:ctx["limit"]], labels)
    return ranked, metrics, k_metrics


def _run_authored_query(q: dict, ctx: dict) -> dict[str, list[tuple[str, float]]]:
    # #1348: authored queries have NO oracle grades — the stub-projection
    # positive control is oracle-only. Passing the raw argparse bool would
    # construct GraphRanker(True) and silently zero every boost (the bool
    # is not None → treated as a projection; _fetch_signals swallows the
    # AttributeError). Always None here (code-review P1 fix).
    return retrieve_per_strategy(
        ctx["graph"], q["query"], q.get("kind"), ctx["vecs"].get(q["id"]),
        ctx["tfidf_points"], ctx["is_embedded"], ctx["limit"],
        depth=ctx.get("depth"), k_sweep=ctx.get("k_sweep", False),
        proj=ctx.get("proj"), graph_ranker_arm=ctx.get("graph_ranker_arm", False),
        corpus_variant=ctx.get("corpus_variant", "plain"),
        oracle=None, live_ids_by_topic=None,
        stub_projection=None,
    )


def _inject_probe_model(args) -> None:
    """Apply --model before the run: inject the candidate embedder via
    tools.embedder_probe so _query_vecs encodes with it (after injection
    EmbeddingModel.get() IS the candidate, which is exactly what _query_vecs
    auto-uses). HARD FAIL (EmbedderProbeError) when the candidate cannot
    load — --model never silently degrades to synthetic query vectors."""
    model = getattr(args, "model", None)
    if model is None:
        return
    inject_model(model, query_prompt=getattr(args, "query_prompt", None))


def _query_vecs(oracle, queries, seed: int) -> dict[str, list[float]]:
    """Precompute one query vector per query (outside the measured window).

    Embedded mode: synthetic semantic stand-ins derived from the oracle
    topic structure (see TopicOracle.query_vector_for). Docker mode: the
    real embedding model when available (tortoise.embeddings.EmbeddingModel),
    else the same synthetic stand-in (flagged in provenance). When --model
    was passed, _inject_probe_model already loaded the candidate, so
    EmbeddingModel.get() returns the candidate here.
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


def _paired_vs_fused(per_query, rng, strategies=("fts", "vector", "structural", "tfidf")) -> dict:
    """Paired 90% CIs on (fused − strategy) deltas, in points (×100).

    #1348: fused_rerank is EXCLUDED here — it is not an isolation arm (it is a
    rerank OF fused) and its comparison lives in rerank_verdict (second-model
    gate ISSUE-2: including it doubled the fused_rerank−fused comparison with
    the OPPOSITE sign).
    """
    out = {}
    for strat in strategies:
        if strat in ("fused", RERANK_STRATEGY) or not any(
                qid in per_query and strat in per_query[qid] for qid in per_query):
            continue
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


def _rerank_verdict(per_query, rng) -> dict | None:
    """#1348 paired fused_rerank − fused verdict statistic (90% CI). Only
    populated when the arm is ON (fused_rerank present in per-query metrics)."""
    present = [qid for qid in per_query if "fused_rerank" in per_query[qid]]
    if not present:
        return None
    out = {}
    for metric in ("ndcg@10", "p@5", "r@10", "mrr"):
        deltas = [
            (per_query[qid]["fused_rerank"][metric] - per_query[qid]["fused"][metric]) * 100.0
            for qid in present
        ]
        out[f"{metric}_delta_points"] = bootstrap.paired_bootstrap_ci(
            deltas, rng=rng,
        ).to_dict()
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
        try:  # noqa: SIM105
            sdk.close()
        except Exception:
            pass


def _run_with_sdk(args, sdk) -> dict:
    proj = sdk._get_proj()
    is_embedded = getattr(proj, "_is_embedded", True)
    _inject_probe_model(args)  # must precede _query_vecs (provenance records it)
    rng = random.Random(args.seed * 65537)  # bootstrap/CI resampling rng

    # 1. Oracle + corpus. #1348: --corpus-variant enhanced seeds topic-correlated
    # EP structure (enhance_signals) so the GraphRanker verdict is measurable.
    oracle = build_topic_oracle(args.seed)
    enhance = getattr(args, "corpus_variant", "plain") in ("enhanced", "enhanced-conf-only")
    points, topic_counts = generate_oracle_points(
        args.corpus_size, oracle, seed=args.seed, enhance_signals=enhance,
    )
    seeded = 0
    if not args.no_seed_corpus:
        seeded = seed_corpus(proj.g, points)
        edges = seed_operator_edges(
            proj.g, random.Random(args.seed), n_edges_per_op=200,
            topic_correlated=enhance, oracle=oracle,
        )
        if not args.quiet:
            print(f"[corpus] seeded {seeded} points, {edges} operator edges")
    else:
        if not args.quiet:
            print("[corpus] using existing corpus (--no-seed-corpus)")
    corpus_fp = corpus_fingerprint_from_graph(proj.g)
    # #1348: per-topic live counts come from the oracle generator (the hidden
    # topic key is never written to the graph — seed_corpus SET is
    # explicit-field), merged into the fingerprint for provenance.
    corpus_fp["topic_counts"] = dict(sorted(topic_counts.items()))
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
                    "n_queries": len(AUTHORED_QUERIES),  # noqa: F821
                    "labels": (
                        "subjective — judged by two cross-vendor LLM judges + "
                        "owner adjudication (judge.py); no logs required"
                    ),
                },
                "queries": AUTHORED_QUERIES,  # noqa: F821
            }, indent=2) + "\n"
        )
        oracle_set = load_oracle_queries()
        authored_set = load_authored_queries()

    all_queries = oracle_set["queries"] + authored_set["queries"]
    vecs, use_model = _query_vecs(oracle, all_queries, args.seed)
    tfidf_points = _tfidf_snapshot(proj.g)
    # #1348: ACTIVE strategies tuple — fused_rerank added only when the arm is
    # ON (default runs stay shape-identical to the v1 baseline). k_sweep is a
    # separate top-level report section (fused@{20,60,100} from k_metrics, NOT
    # per_query keys) and never touches the strategies list.
    active_strategies = list(STRATEGIES)
    graph_ranker_arm = bool(getattr(args, "graph_ranker_arm", False))
    if graph_ranker_arm:
        active_strategies.append(RERANK_STRATEGY)
    ctx = {
        "graph": proj.g, "is_embedded": is_embedded,
        "tfidf_points": tfidf_points, "vecs": vecs, "limit": args.limit,
        "depth": getattr(args, "depth", None),
        "k_sweep": bool(getattr(args, "k_sweep", False)),
        "strategies": tuple(active_strategies),
        "proj": proj,
        "graph_ranker_arm": graph_ranker_arm,
        "corpus_variant": getattr(args, "corpus_variant", "plain"),
        "oracle": oracle,
        "live_ids_by_topic": live_ids_by_topic,
        "stub_projection": getattr(args, "stub_projection", None),
    }

    # 3. Oracle queries: per-strategy ranked lists + metrics.
    oracle_per_query: dict[str, dict] = {}
    oracle_pool_results: dict[str, dict] = {}
    oracle_ranked: dict[str, dict] = {}
    oracle_k_metrics: dict[str, dict] = {}
    for q in oracle_set["queries"]:
        ranked, metrics, k_metrics = _run_oracle_query(q, ctx, oracle)
        oracle_per_query[q["id"]] = metrics
        oracle_ranked[q["id"]] = ranked
        if k_metrics:
            oracle_k_metrics[q["id"]] = k_metrics
        oracle_pool_results[q["id"]] = {
            "_query": q["query"],
            **{strat: [
                {"id": pid, "content": next(
                    (p["content"] for p in points if p["id"] == pid), ""),
                 "point_kind": next(
                    (p["pointKind"] for p in points if p["id"] == pid), "")}
                for pid, _s in ranked[strat]
            ] for strat in active_strategies},
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
            ] for strat in active_strategies},
        }
        if judge_labels and q["id"] in judge_labels:
            labels = judge_labels[q["id"]]
            authored_per_query[q["id"]] = {
                strat: compute_metrics(_point_ids(ranked[strat])[:args.limit], labels)
                for strat in active_strategies
            }
    if judge_labels and authored_per_query:
        authored_meta["labels_status"] = "adjudicated"
        authored_meta["n_labeled"] = len(authored_per_query)
    elif judge_labels:
        authored_meta["labels_status"] = "labels-file-no-matching-queries"

    # 5. Aggregate + CIs.
    per_strategy = {
        strat: _aggregate_with_ci(oracle_per_query, strat, rng)
        for strat in active_strategies
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
            for strat in active_strategies
        }

    paired = _paired_vs_fused(oracle_per_query, rng, strategies=tuple(active_strategies))
    rerank_verdict = _rerank_verdict(oracle_per_query, rng) if graph_ranker_arm else None
    gate = None
    if args.baseline:
        base = json.loads(Path(args.baseline).read_text())
        base_sv = base.get("schema_version", 1)
        if base_sv > SCHEMA_VERSION:
            print(f"[gate] WARN: baseline schema_version {base_sv} > runner "
                  f"{SCHEMA_VERSION} — gating anyway (older baseline always gates)")
        gate = _gate_vs_baseline(
            oracle_per_query, base.get("per_query", {}), rng,
        )

    # 6. Authored pool emission (top-50/strategy/query, deduped per query).
    pool_out_path = None
    if args.pool_out and authored_pool_results:
        pool = build_pool(authored_pool_results, corpus_fp, list(active_strategies))
        Path(args.pool_out).write_text(json.dumps(pool.to_dict(), indent=2) + "\n")
        pool_out_path = args.pool_out

    authored_agg = (
        {strat: _aggregate_with_ci(authored_per_query, strat, rng)
         for strat in active_strategies}
        if authored_per_query else {}
    )

    # 7. #1348 k-sweep section (separate top-level block, NOT per_query keys —
    # P2-E code-review fix: per_query stays strategy-keyed).
    k_sweep_section = None
    if ctx.get("k_sweep"):
        k_sweep_section = {
            "k_set": list(KSWEEP),
            "per_query": {
                qid: oracle_k_metrics[qid] for qid in sorted(oracle_k_metrics)
            },
            "aggregate": {
                str(k): {
                    metric: aggregate({metric: [
                        oracle_k_metrics[qid][str(k)][metric] for qid in sorted(oracle_k_metrics)
                    ]})[metric]
                    for metric in METRICS
                }
                for k in KSWEEP
            },
        }

    # 8. #1348 per-strategy population counts (self-declared authority).
    #    Computed from the step-3 ranked lists — NO second retrieval pass
    #    (P2 code-review fix: re-running _run_oracle_query doubled eval cost
    #    and risked count/metric disagreement on non-deterministic retrieval).
    pop_counts: dict[str, int] = {s: 0 for s in active_strategies}
    for qid in oracle_ranked:
        for s in active_strategies:
            if oracle_ranked[qid].get(s):
                pop_counts[s] += 1

    report = {
        "schema_version": SCHEMA_VERSION,
        "issue": "1144",
        "seed": args.seed,
        "corpus_target": args.corpus_size,
        "seeded_points": seeded,
        "limit": args.limit,
        "depth": getattr(args, "depth", None) or args.limit,
        "strategies": list(active_strategies),
        "k_sweep": k_sweep_section,
        "population_counts": pop_counts,
        "rerank_verdict": rerank_verdict,
        "corpus_variant": getattr(args, "corpus_variant", "plain"),
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
            "depth": getattr(args, "depth", None) or args.limit,
            "k_sweep": bool(getattr(args, "k_sweep", False)),
            "graph_ranker_arm": graph_ranker_arm,
            "corpus_variant": getattr(args, "corpus_variant", "plain"),
            "synthetic_query_vectors": not use_model,
            "embedding_model": _resolved_embedding_model(
                use_model, getattr(args, "model", None) is not None),
            "judge_labels": args.judge_labels or "none",
        }),
        "notes": [
            "Embedded FalkorDBLite: no HNSW vector index — vector runs "
            "brute-force; the FULLTEXT index EXISTS on embedded "
            "(indexes.fts=true; FTS populated 40/100 queries in the committed "
            "baseline) and structural populates only for kind-carrying queries. "
            "FTS/structural columns on embedded runs are measurement-"
            "environment artifacts, not quality statements — Docker FalkorDB "
            ">= 4.x is authoritative.",
            "On embedded, fused RRF is NOT vector alone — per-query data shows "
            "FTS populated 40/100 queries and fused != vector on 17/100 "
            "(fused-vs-vector nDCG@10 delta CI -3.25..-0.83, excludes 0).",
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
            "Paired deltas (fused vs per-strategy, rerank verdict, and gate-"
            "vs-baseline) are 90% paired bootstrap CIs on per-query deltas in "
            "POINTS (x100).",
            "#1348 pool-semantics note: eval depth == eval limit (no x2 in this "
            "harness); SDK str_limit = limit x2, with env-only opt-in "
            "TORTOISE_POOL_FLOOR (no baked default). "
            "--limit = returned limit (metrics are top-limit), --depth = "
            "per-strategy retrieval depth; all depth/k/GraphRanker measurements "
            "here are 2-strategy fusion (RRF fts+vector) on a kind-less corpus "
            "(structural dead), while production fuses 3 strategies — the floor "
            "default for 3-strategy production is a projection.",
            "#1348 k_sweep: fused@{20,60,100} computed from IDENTICAL lists "
            "(paired by construction); k-verdict interpretable only where >=2 "
            "strategies populate (population_counts self-declare authority). "
            "fused key is the k=60 alias (gate compat).",
            "#1348 fused_rerank arm: fuse -> truncate to limit -> rerank "
            "(production order sdk.py:8851->9004); corpus-variant enhanced "
            "seeds topic-correlated EP (n.confidence + edges, no new nodes); "
            "oracle-proxy = query-conditioned positive control (stub "
            "projection, graph_boost_weight=1.0); enhanced-conf-only is a "
            "RANKER-LEVEL ablation (corpus identical to enhanced; the ranker "
            "neutralizes the degree term via use_degree=False so the boost is "
            "confidence-only).",
        ],
    }
    return report


def write_outputs(report: dict, out_path: str | None) -> str:
    if out_path is None:
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017
        out_path = str(Path(__file__).resolve().parent / "reports"
                       / f"{ts}-retrieval-eval.json")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(report, indent=2) + "\n")
    return out_path


def _markdown_summary(report: dict) -> str:
    lines = ["| strategy | nDCG@10 | P@5 | R@10 | MRR |", "|---|---|---|---|---|"]
    for strat in report.get("strategies", STRATEGIES):
        m = report["metrics"][strat]
        lines.append(
            f"| {strat} | {m['ndcg@10']['value']:.3f} | {m['p@5']['value']:.3f} "
            f"| {m['r@10']['value']:.3f} | {m['mrr']['value']:.3f} |"
        )
    g = report.get("gate_vs_baseline")
    gate_line = f"gate vs baseline: {g['verdict']}" if g else "gate: none (no --baseline)"
    rv = report.get("rerank_verdict")
    if rv:
        c = rv.get("ndcg@10_delta_points", {})
        rv_line = (f"rerank verdict nDCG@10 delta: mean={c.get('mean')} "
                   f"CI[{c.get('lower')},{c.get('upper')}] (n={c.get('n')})")
        return "\n".join(lines + ["", gate_line, rv_line])  # noqa: RUF005
    return "\n".join(lines + ["", gate_line])  # noqa: RUF005


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m tests.eval.retrieval.run",
        description="Issue #1144 retrieval quality eval runner (per-strategy "
        "+ fused graded metrics, paired bootstrap CIs, baseline gate; #1348 "
        "depth/k-sweep/GraphRanker arms).",
    )
    p.add_argument("--db", help="FalkorDB URI (docker://|bolt://) or embedded "
                   "db file path (default: $TORTOISE_DB_URI, else a fresh "
                   "embedded db under tests/eval/retrieval/reports/)")
    p.add_argument("--corpus-size", type=int, default=DEFAULT_CORPUS_SIZE)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help="RETURNED limit — metrics are computed against top-limit "
                        "(default 50; #1348 pool-semantics: eval depth == eval limit)")
    p.add_argument("--depth", type=int, default=None,
                   help="#1348 per-strategy retrieval depth (default = --limit; "
                        "fusion runs over FULL-depth lists, then metrics truncate "
                        "to --limit)")
    p.add_argument("--k-sweep", action="store_true",
                   help="#1348 in-loop paired k sweep: fused@{20,60,100} from "
                        "identical lists (separate k_sweep report section)")
    p.add_argument("--graph-ranker-arm", action="store_true",
                   help="#1348 add fused_rerank strategy (fuse->truncate->rerank, "
                        "production order) + paired rerank verdict CI")
    p.add_argument("--corpus-variant", default="plain",
                   choices=("plain", "enhanced", "enhanced-conf-only"),
                   help="#1348 corpus EP structure: plain (baseline-identical), "
                        "enhanced (topic-correlated confidence + edges). "
                        "enhanced-conf-only is a RANKER-level ablation: corpus is "
                        "identical to enhanced, but the ranker neutralizes the "
                        "degree term (use_degree=False) so the boost is "
                        "confidence-only — the corpus itself keeps topic-"
                        "correlated edges (see report notes).")
    p.add_argument("--stub-projection", action="store_true",
                   help="#1348 positive control: query-conditioned oracle-grade "
                        "signals via a duck-typed stub projection (MECHANISM test)")
    p.add_argument("--out", help="report JSON path")
    p.add_argument("--pool-out", help="write the authored-query judge pool "
                   "JSON (top-50/strategy/query, deduped)")
    p.add_argument("--judge-labels", help="merged adjudicated labels JSON "
                   "(judge.py --apply-rulings --out) → authored metrics")
    p.add_argument("--baseline", help="baseline report JSON → quality gate "
                   "(SHIP/WARN/BLOCK vs baseline, paired on query ids; older "
                   "baseline schema always gates, newer warns)")
    p.add_argument("--no-seed-corpus", action="store_true")
    p.add_argument("--rebuild-queries", action="store_true",
                   help="regenerate and overwrite the committed query JSONs")
    p.add_argument("--model", choices=sorted(PROBE_MODELS.keys()),
                   help="embedding model short name for query "
                   "vectors (tools/embedder_probe PROBE_MODELS: minilm | "
                   "arctic-xs | arctic-s | bge-small); injected BEFORE the "
                   "run — HARD FAIL (EmbedderProbeError) if it cannot load")
    p.add_argument("--query-prompt", help="named prompt template threaded to "
                   "the injected model (e.g. 'query' for the snowflake-arctic "
                   "vendor config prompt_name='query')")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    # #1348 post-parse resolution: depth defaults to limit (argparse defaults
    # are static — resolve after parse).
    if args.depth is None:
        args.depth = args.limit

    if args.query_prompt is not None and args.model is None:
        p.error("--query-prompt requires --model")

    if not args.quiet:
        print(f"#1144 retrieval eval — corpus={args.corpus_size} "
              f"limit={args.limit} depth={args.depth} seed={args.seed} "
              f"k_sweep={args.k_sweep} arm={args.graph_ranker_arm} "
              f"variant={args.corpus_variant}")
    report = run_eval(args)
    out = write_outputs(report, args.out)
    print("\n" + _markdown_summary(report))
    print(f"\nreport: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
