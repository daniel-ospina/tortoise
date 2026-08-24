"""run_report — standalone report runner for the #316 latency benchmark.

Pre-registered numbers (issue #316 scoping v5.1 — fixed, do not edit here):
    per-strategy targets:  FTS < 50ms | vector < 100ms | hybrid < 200ms |
                           TF-IDF < 500ms  (isolation p95, censored column)
    E2E-8:                 mix-weighted p95 ≤ 300ms → "achieved";
                           300–500ms → "cap-dominated"; >500ms+ε → "tail";
                           >30% of censored samples capped/degraded (healthy-only
                           p95 over a minority) → "inconclusive"
    #317 headroom:         300 − full-E2E-uncensored-p95 (elevated column)

Two-column protocol per arm: censored (default 500ms cap — pristine prod
path) vs elevated-cap (elevated_timeout_ms=5000, true completion, unpaired).
Warmup-then-measure: warmup iterations run discarded until CV < 10% or the
max-iteration cap, then measured samples (mix-weighted, round-robin over the
query mix).

Usage:
    python -m benchmarks.run_report [--db PATH_OR_URI] [--corpus-size N]
        [--samples N] [--seed N] [--out PATH] [--no-seed-corpus]
        [--skip-e2e] [--quiet]

Environment: Docker FalkorDB ≥4.x (HNSW+FTS) is the measurement environment
for prod-parity numbers; embedded FalkorDBLite runs are supported for smoke
but flagged NOT-comparable (scoping: "numbers can reverse").
"""
from __future__ import annotations

import argparse
import datetime as _dt
import itertools
import json
import os
import platform
import random
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # benchmarks pkg

from benchmarks import bench_core  # noqa: E402
from benchmarks.bench_core import (  # noqa: E402
    CAP_MS,
    CLASSIFY_CAP_EPS_MS,
    E2E_TARGET_MS,
    ELEVATED_TIMEOUT_MS,
    PRE_REGISTERED_TARGETS_MS,
    ArmResult,
    LatencyStats,
    WarmupProtocol,
    e2e_verdict,
    failure_fraction,
    headroom_ms,
    run_arm,
    strategy_verdict,
)
from benchmarks.synthetic_corpus import (  # noqa: E402
    EMBEDDING_DIM,
    corpus_fingerprint_from_graph,
    default_corpus_path,
    generate_points,
    load_query_mix,
    seed_corpus,
    seed_operator_edges,
    verify_indexes,
)
from tools import embedder_probe  # noqa: E402
from tools.embedder_probe import PROBE_MODELS  # noqa: E402
from tortoise.embeddings import EMBEDDING_MODEL  # noqa: E402

MIX_PATH = Path(__file__).resolve().parent / "query_mix.json"


# ── Provenance ──────────────────────────────────────────────────────────────

def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5
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
    if sys.platform != "darwin":
        try:  # noqa: SIM105
            specs["mem_bytes"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError, AttributeError):
            pass
    return specs


def _resolved_embedding_model(use_model: bool, injected: bool) -> str:
    """Truthful embedding-model identity for provenance.

    --model active: the probe-recorded candidate hf_id (the probe state IS
    the swap proof). No injection but a real model loaded: the probe state
    when present (in a warm in-process re-run the loaded singleton may be a
    previously-injected candidate — the probe state persists, so reporting
    its hf_id is truthful), else the EMBEDDING_MODEL constant (T9 re-pointed
    provenance to the constant — the single source of truth for the default
    embedder). Degraded (no model — synthetic vectors): 'unavailable'.
    """
    state = embedder_probe.get_state()
    if injected:
        if state is not None:
            return str(state["hf_id"])
        return "unavailable"
    if use_model:
        if state is not None:
            return str(state["hf_id"])
        return EMBEDDING_MODEL
    return "unavailable"


def capture_provenance(proj, is_embedded: bool, extras: dict) -> dict:
    prov: dict = {
        "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),  # noqa: UP017
        "git_sha": _git_sha(),
        "host": _host_specs(),
        "db_mode": "embedded-falkordblite" if is_embedded else "docker-falkordb",
        "tor_toise_db_uri_set": bool(os.environ.get("TORTOISE_DB_URI")),
        "embedding_model": extras.get("embedding_model", "unavailable"),
        "synthetic_vectors": extras.get("synthetic_vectors", False),
        "tfidf_available": extras.get("tfidf_available", False),
        "indexes": extras.get("indexes", {}),
        "corpus": extras.get("corpus_fingerprint", {}),
        "query_mix": extras.get("query_mix_meta", {}),
        "warmup": extras.get("warmup", {}),
        "samples_per_arm": extras.get("samples", bench_core.DEFAULT_SAMPLES),
        "elevated_timeout_ms": ELEVATED_TIMEOUT_MS,
        "cap_ms": CAP_MS,
        "e2e_target_ms": E2E_TARGET_MS,
        "pre_registered_targets_ms": PRE_REGISTERED_TARGETS_MS,
        # #1348: per-arm retrieval depth + embedder/decorator pins (Task 8) —
        # the e2e@new-floor minus retrieval@new-floor decomposition is
        # unauditable without these in the emitted report.
        "arm_limit": extras.get("arm_limit"),
        "embedder_pinned": extras.get("embedder_pinned", False),
        "decorator_state": extras.get("decorator_state"),
    }
    for mod_name, attr in (("falkordb", "__version__"),):
        try:
            mod = __import__(mod_name)
            prov[f"{mod_name}_version"] = getattr(mod, attr, "unknown")
        except Exception:
            pass
    if is_embedded:
        prov["embedded_engine"] = (
            "redislite (no HNSW vector index — vector runs brute-force; "
            "FULLTEXT index EXISTS on embedded (indexes.fts=true; FTS "
            "populated 40/100 queries in the committed #1144 baseline); "
            "numbers NOT prod-parity — can reverse on Docker)"
        )
    if extras.get("query_prompt") is not None:
        prov["query_prompt"] = extras["query_prompt"]
    return prov


# ── Arm machinery ───────────────────────────────────────────────────────────

def _query_vec_for(query: str, seed: int, use_model: bool) -> list[float] | None:
    """Precompute a query vector ONCE per query (outside the measured window).

    Real embedding when the model is available; otherwise a deterministic
    pseudo-random 384-dim vector (flagged `synthetic_vectors` in provenance —
    latency shape only, NOT semantic quality).

    Determinism: builtin hash() is salted per-process (PYTHONHASHSEED) so a
    seeded run would produce different vectors per process → non-reproducible
    reports. random.Random(str) seeds from SHA-512 of the string bytes
    (Python 3 seed v2), which is stable across processes for the same
    seed:query key (measurement-validity: a re-run must measure the same
    workload)."""
    if use_model:
        try:
            from tortoise.embeddings import EmbeddingModel
            model = EmbeddingModel.get()
            if model is not None:
                return model.encode([query])[0].tolist()
        except Exception:
            pass
    rng = random.Random(f"{seed}:{query}")
    return [rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIM)]


def _is_special_query(q: dict) -> bool:
    """Special queries that MUST be measured: no-match degrade triggers and
    kind-bearing / kind-only structural queries (query_mix.json q049-q056).
    They are the tail-of-mix cases the latency distribution must see."""
    return (
        q.get("expect") in ("no-match-degrade", "structural")
        or q.get("kind") is not None
    )


def _roundrobin(items: list[dict], n: int) -> list[dict]:
    """Cycle the query list to produce exactly n mix-weighted picks.

    Measurement-validity contract: when n < len(items) the special queries
    (no-match q055/q056, kind-bearing q049-q052, kind-only structural
    q053/q054) are guaranteed inclusion and spread evenly across the picks —
    otherwise default samples=50 < 56 queries would never measure the
    degrade/structural tail and the mix-weighted p95 would be computed over
    the first n queries only. Even spread also keeps the specials inside the
    measured window after warmup consumes the front of the cycle."""
    if not items:
        return []
    if len(items) <= n:
        return [items[i % len(items)] for i in range(n)]
    special = [q for q in items if _is_special_query(q)]
    if not special:
        return [items[i % len(items)] for i in range(n)]
    if len(special) >= n:
        # n too small to fit every special once — cycle specials so no single
        # special type is systematically excluded.
        return [special[i % len(special)] for i in range(n)]
    regular = [q for q in items if not _is_special_query(q)]
    # Evenly spread the specials across the n slots (integer positions are
    # strictly increasing when n >= len(special), so every special is placed).
    special_positions = {i * n // len(special) for i in range(len(special))}
    picks: list[dict] = []
    si = ri = 0
    for i in range(n):
        if i in special_positions:
            picks.append(special[si])
            si += 1
        else:
            picks.append(regular[ri % len(regular)])
            ri += 1
    return picks


def _make_arm(arm_name: str, ctx: dict):
    """Build fn(timeout_ms, qi) → (elapsed_ms, results_count, error) for one arm.

    #1348: retrieval arms run at ctx["arm_limit"] (per-strategy depth), set by
    _run_with_sdk to args.depth or max(2 x e2e_limit, TORTOISE_POOL_FLOOR). The
    ctx.get fallback 20 below is DEFENSIVE-ONLY (pre-floor #316 comparator); the
    run-level default lives in _run_with_sdk.
    """
    graph = ctx["graph"]
    is_embedded = ctx["is_embedded"]
    tfidf_points = ctx["tfidf_points"]
    precomputed = ctx["precomputed"]
    arm_limit = ctx.get("arm_limit", 20)  # per-strategy depth for this run

    def _vec(q: str) -> list[float] | None:
        # Vectors are precomputed for the FULL mix before any arm runs
        # (see _run_with_sdk) — never embed inside the measured window.
        return precomputed.get(q)

    def fn(timeout_ms: float, qi: dict):
        q, kind = qi.get("query"), qi.get("kind")
        from tortoise.search_engine import (
            classify_query,
            degradation_chain,
            fallback_tfidf,
            run_fts_query,
            run_vector_query,
        )

        t0 = time.perf_counter()
        err = None
        count = 0
        driver_timeout = int(timeout_ms)  # driver timeout must be an int (#316)
        try:
            if arm_name == "fts":
                rows = run_fts_query(graph, q, entity_type="point", limit=arm_limit,
                                     timeout_ms=driver_timeout)
                count = len(rows)
            elif arm_name == "vector":
                rows = run_vector_query(graph, _vec(q), limit=arm_limit, timeout_ms=driver_timeout,
                                        is_embedded=is_embedded)
                count = len(rows)
            elif arm_name == "hybrid":
                strategies = classify_query(q, kind)
                vec = _vec(q) if q and strategies.get("vector") else None
                # Censored column: elevated_timeout_ms=None → the chain's own
                # 500ms collective cap (real prod path). Elevated column: thread
                # the benchmark cap so no strategy work is truncated.
                elevated = driver_timeout if driver_timeout > CAP_MS else None
                results = degradation_chain(
                    graph, q, kind, vec, strategies, entity_type="point",
                    limit=arm_limit, is_embedded=is_embedded,
                    elevated_timeout_ms=elevated,
                )
                count = sum(len(v) for v in results.values())
            elif arm_name == "tfidf":
                rows = fallback_tfidf(q, tfidf_points, limit=arm_limit)
                count = len(rows)
            else:
                raise ValueError(f"unknown arm {arm_name}")
        except Exception as e:  # noqa: BLE001, RUF100
            err = str(e)
        return (time.perf_counter() - t0) * 1000, count, err

    return fn


def _sdk_e2e(sdk, qi: dict, elevated: int | None, *, pool_size: int | None = None):
    t0 = time.perf_counter()
    err = None
    count = 0
    try:
        # #1348: pool_size threads the run-level arm_limit into the SDK e2e
        # leg so the E2E depth MATCHES the retrieval arms (--depth desync fix,
        # code-review P2). Default None → SDK's own str_limit (env floor or
        # historical limit*2), preserving the #316 baseline for default runs.
        rows = sdk.tortoise_fts_query(
            query=qi.get("query"), kind=qi.get("kind"), limit=10,
            pool_size=pool_size,
            _elevated_timeout_ms=int(elevated) if elevated is not None else None,
        )
        count = len(rows)
    except Exception as e:  # noqa: BLE001, RUF100
        err = str(e)
    return (time.perf_counter() - t0) * 1000, count, err


def _breaker_is_open(name: str) -> bool:
    from tortoise.search_engine import _breaker
    return _breaker(name).is_open()


def _run_column(
    arm_name: str, fn, picks: list[dict], timeout_ms: float, args,
    breaker_names: list[str] | None,
    classify_eps_ms: float = CLASSIFY_CAP_EPS_MS,
) -> ArmResult:
    """One two-column leg: warmup + `samples` measured picks (round-robin).

    classify_eps_ms: cap-boundary tolerance for sample classification — 0.0
    for arms with no enforced timeout (tfidf fallback) so a genuine completion
    near the cap stays healthy."""
    from tortoise.search_engine import reset_circuit_breakers

    def reset():
        reset_circuit_breakers()

    # Round-robin iterator over the mix; each sample measures ONE query.
    cycle = itertools.cycle(picks)
    warmup = WarmupProtocol(iters=args.warmup_iters, max_iters=args.warmup_max_iters)

    def one_sample():
        qi = next(cycle)
        return fn(timeout_ms, qi)

    return run_arm(
        f"{arm_name}:{'censored' if timeout_ms <= CAP_MS else 'elevated'}",
        one_sample,
        samples=args.samples,
        timeout_ms=timeout_ms,
        warmup=warmup,
        breaker_names=breaker_names or [],
        reset_breakers=reset if breaker_names else None,
        breaker_is_open=_breaker_is_open if breaker_names else None,
        classify_eps_ms=classify_eps_ms,
    )


# ── Verdict / report shaping ────────────────────────────────────────────────

def _arm_dict(arm: ArmResult, target_ms: float | None) -> dict:
    d = arm.to_dict()
    if arm.invalidated:
        d["verdict"] = "INVALIDATED"
        return d
    if arm.degraded_fast:
        d["verdict"] = "DEGRADED-FAST"
        return d
    if target_ms is not None:
        d["verdict"] = strategy_verdict(arm.stats.p95_ms, target_ms)
        d["target_ms"] = target_ms
    return d


def _invalidated_arm(name: str, timeout_ms: float, samples: int, reason: str) -> ArmResult:
    """Fabricate an INVALIDATED arm result (environmental failure — the run is
    not comparable). Used when the DB pre-flight fails before any measurement."""
    return ArmResult(
        name=name, timeout_ms=timeout_ms, samples_requested=samples,
        stats=LatencyStats(), warmup_iters=0,
        invalidated=True, invalidated_reason=reason,
    )


def _e2e_report(censored: ArmResult, elevated: ArmResult) -> dict:
    if censored.invalidated or elevated.invalidated:
        reason = censored.invalidated_reason or elevated.invalidated_reason
        return {
            "target_ms": E2E_TARGET_MS,
            "verdict": "INVALIDATED",
            "invalidated_reason": reason,
            "censored_p95_ms": round(censored.stats.p95_ms, 2),
            "censored_failure_fraction": round(failure_fraction(censored.stats), 3),
            "censored": censored.to_dict(),
            "elevated_p95_ms": round(elevated.stats.p95_ms, 2),
            "elevated": elevated.to_dict(),
            "headroom_ms_317": round(headroom_ms(elevated.stats.p95_ms), 2),
        }
    p95 = censored.stats.p95_ms
    frac = failure_fraction(censored.stats)
    return {
        "target_ms": E2E_TARGET_MS,
        "verdict": e2e_verdict(p95, frac),
        "censored_p95_ms": round(p95, 2),
        "censored_failure_fraction": round(frac, 3),
        "censored": censored.to_dict(),
        "elevated_p95_ms": round(elevated.stats.p95_ms, 2),
        "elevated": elevated.to_dict(),
        "headroom_ms_317": round(headroom_ms(elevated.stats.p95_ms), 2),
    }


def _markdown_table(report: dict) -> str:
    lines = [
        "# #316 FalkorDB latency benchmark report",
        "",
        f"- issue: **{report['issue']}** · corpus: **{report['corpus_target']}** "
        f"points · mode: **{report['provenance']['db_mode']}**",
        f"- git sha: `{report['provenance']['git_sha']}` · "
        f"timestamp: {report['provenance']['timestamp_utc']}",
        f"- E2E-8 verdict: **{report['arms'].get('e2e', {}).get('verdict', 'n/a')}** "
        f"(target ≤ {E2E_TARGET_MS:.0f}ms) · #317 headroom: "
        f"**{report['arms'].get('e2e', {}).get('headroom_ms_317', 'n/a')} ms**",
        "",
        "| Strategy | Target | Censored p50 | Censored p95 | Censored p99 | Verdict | Elevated p95 |",
        "|---|---|---|---|---|---|---|",
    ]
    for arm_name, target in PRE_REGISTERED_TARGETS_MS.items():
        col = report["arms"].get(arm_name, {})
        cen = col.get("censored", {})
        ele = col.get("elevated", {})
        s = cen.get("stats", {})
        lines.append(
            f"| {arm_name} | <{target:.0f} ms | {s.get('p50_ms', 0):.1f} | "
            f"{s.get('p95_ms', 0):.1f} | {s.get('p99_ms', 0):.1f} | "
            f"{cen.get('verdict', 'n/a')} | {ele.get('stats', {}).get('p95_ms', 0):.1f} |"
        )
    lines.append("")
    return "\n".join(lines)


def _print_arm_row(arm_name: str, censored: ArmResult, elevated: ArmResult) -> None:
    if censored.invalidated or elevated.invalidated:
        reason = censored.invalidated_reason or elevated.invalidated_reason
        print(f"[{arm_name:6s}] INVALIDATED — {reason}")
        return
    target = PRE_REGISTERED_TARGETS_MS[arm_name]
    v = "INVALIDATED" if censored.invalidated else (
        "DEGRADED-FAST" if censored.degraded_fast
        else strategy_verdict(censored.stats.p95_ms, target)
    )
    print(
        f"[{arm_name:6s}] censored p50={censored.stats.p50_ms:7.2f}ms "
        f"p95={censored.stats.p95_ms:7.2f}ms p99={censored.stats.p99_ms:7.2f}ms "
        f"| elevated p95={elevated.stats.p95_ms:7.2f}ms | verdict={v} "
        f"(healthy={censored.stats.healthy}/{censored.stats.count})"
    )


def _print_e2e_row(censored: ArmResult, elevated: ArmResult) -> None:
    if censored.invalidated or elevated.invalidated:
        reason = censored.invalidated_reason or elevated.invalidated_reason
        print(f"[e2e    ] INVALIDATED — {reason}")
        return
    v = e2e_verdict(censored.stats.p95_ms, failure_fraction(censored.stats))
    h = headroom_ms(elevated.stats.p95_ms)
    print(
        f"[e2e    ] censored p50={censored.stats.p50_ms:7.2f}ms "
        f"p95={censored.stats.p95_ms:7.2f}ms p99={censored.stats.p99_ms:7.2f}ms "
        f"| elevated p95={elevated.stats.p95_ms:7.2f}ms | E2E-8={v} "
        f"| #317 headroom={h:.2f}ms"
    )


def write_outputs(report: dict, out_path: str | None) -> dict:
    out_dir = (Path(out_path).parent if out_path
               else Path(__file__).resolve().parent / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    if out_path:
        json_path = Path(out_path)
    else:
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017
        json_path = out_dir / f"{stamp}-report.json"
    json_path.write_text(json.dumps(report, indent=2, default=str))
    md_path = json_path.with_suffix(".md")
    md_path.write_text(_markdown_table(report))
    latest = out_dir / "latest.json"
    latest.write_text(json.dumps(report, indent=2, default=str))
    return {"json": str(json_path), "markdown": str(md_path), "latest": str(latest)}


# ── Main run ────────────────────────────────────────────────────────────────

def run_benchmark(args) -> dict:
    from tortoise.sdk import TortoiseSDK

    # 1. Open the store (embedded file or docker URI).
    db_arg = args.db
    if db_arg is None and os.environ.get("TORTOISE_DB_URI"):
        db_arg = os.environ["TORTOISE_DB_URI"]
    if db_arg is None:
        db_arg = default_corpus_path()
    is_uri = "://" in db_arg
    sdk = TortoiseSDK(None if is_uri else db_arg)
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

    # --model: inject the candidate embedder BEFORE the E2E-8 measurement
    # (query vectors are precomputed below via EmbeddingModel.get() — after
    # injection that IS the candidate). HARD FAIL if it cannot load.
    if getattr(args, "model", None) is not None:
        embedder_probe.inject_model(
            args.model,
            query_prompt=getattr(args, "query_prompt", None),
            load_timeout=getattr(args, "load_timeout", None))

    # 2. Corpus (seed unless --no-seed-corpus).
    seeded = 0
    if not args.no_seed_corpus:
        points, fp = generate_points(args.corpus_size, seed=args.seed)  # noqa: RUF059
        seeded = seed_corpus(proj.g, points)
        rng = random.Random(args.seed)
        edges = seed_operator_edges(proj.g, rng, n_edges_per_op=200)
        if not args.quiet:
            print(f"[corpus] seeded {seeded} points, {edges} operator edges "
                  f"(target {args.corpus_size})")
    else:
        if not args.quiet:
            print("[corpus] using existing corpus (--no-seed-corpus)")

    corpus_fp = corpus_fingerprint_from_graph(proj.g)
    assert corpus_fp["n_points"] > 0, "structural non-empty assertion failed — corpus empty"

    # 3. Index verification (verify-not-create).
    indexes = verify_indexes(proj)
    if not args.quiet:
        print(f"[indexes] fts={indexes['fts']} vector={indexes['vector']} "
              f"(mode={'embedded' if is_embedded else 'docker'})")

    # 4. Query mix + env probes.
    mix = load_query_mix(str(MIX_PATH))
    all_queries: list[dict] = mix["queries"]
    use_model = False
    try:
        from tortoise.embeddings import EmbeddingModel
        use_model = EmbeddingModel.get() is not None
    except Exception:
        use_model = False
    tfidf_available = True
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: F401
    except Exception:
        tfidf_available = False

    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.is_operator <> true "
        "RETURN n.id, n.content, n.pointKind"
    ).result_set
    tfidf_points = [
        {"id": r[0], "content": r[1] or "", "pointKind": r[2] or ""} for r in rows
    ]
    if not args.quiet:
        print(f"[tfidf] corpus snapshot {len(tfidf_points)} docs "
              f"(sklearn={'yes' if tfidf_available else 'NO'})")

    # 5. Precompute query vectors for the FULL mix, OUTSIDE the measured
    # window: embedding a query lazily inside fn() would be measured latency
    # (docstring contract in _query_vec_for) and would re-embed per column.
    precomputed: dict[str, list[float] | None] = {}
    for qi in all_queries:
        q = qi.get("query")
        if q:
            precomputed[q] = _query_vec_for(q, args.seed, use_model)
    if not args.quiet:
        print(f"[vec] precomputed {len(precomputed)} query vectors (model={use_model})")

    # Pre-flight the DB connection BEFORE any arm: the strategy runners
    # swallow driver/connection exceptions internally and return [] (degraded),
    # so a dead/unreachable store would masquerade as DEGRADED-FAST latency
    # instead of an environmental failure. One trivial query proves reachability;
    # on failure every arm is INVALIDATED (not comparable).
    preflight_reason: str | None = None
    try:
        proj.g.query("RETURN 1").result_set  # noqa: B018
    except Exception as e:  # noqa: BLE001, RUF100
        preflight_reason = f"DB connection pre-flight failed: {e}"
        if not args.quiet:
            print(f"[preflight] {preflight_reason} — invalidating all arms")

    # 6. Arms. #1348: arm_limit = per-strategy retrieval depth. Default = the
    # pool floor (max(2 x e2e_limit, TORTOISE_POOL_FLOOR)) so a default run
    # measures the valid e2e@new-floor minus retrieval@new-floor decomposition;
    # pre-floor comparator (depth 20) recorded in provenance.
    e2e_limit = getattr(args, "e2e_limit", 10)
    # #1348: NO baked default floor — the depth finding was CEILING-CAPPED;
    # env-only opt-in. Unset → historical e2e_limit*2 semantics. Keep the
    # parse in sync with sdk.py tortoise_fts_query (clamp 1..10000).
    pool_floor = 0
    raw_floor = os.environ.get("TORTOISE_POOL_FLOOR", "")
    if raw_floor.strip():
        try:  # noqa: SIM105
            pool_floor = int(raw_floor)
        except (TypeError, ValueError):
            pass
        pool_floor = max(1, min(pool_floor, 10000))  # keep in sync with sdk.py
    arm_limit = getattr(args, "depth", None) or max(e2e_limit * 2, pool_floor)
    # #1348 pre-flight: arm_limit must be within the SDK pool_size validation
    # bound (1..10000) or the e2e arm fails per-sample mid-run. Fail EARLY.
    if not (1 <= arm_limit <= 10000):
        raise SystemExit(
            f"--depth/arm_limit must be 1-10000 (got {arm_limit}) — "
            "matches the sdk.py pool_size validation bound (#1348)")
    ctx = {
        "graph": proj.g,
        "is_embedded": is_embedded,
        "tfidf_points": tfidf_points,
        "seed": args.seed,
        "use_model": use_model,
        "precomputed": precomputed,
        "arm_limit": arm_limit,
    }
    reports: dict[str, dict] = {}
    cold_start: dict[str, float] = {}
    breaker_names = {"fts": ["fts"], "vector": ["vector"], "hybrid": ["fts", "vector", "structural"]}

    for arm_name in ("fts", "vector", "hybrid", "tfidf"):
        arm_qs = [q for q in all_queries if arm_name in q.get("arms", [])]
        if not arm_qs:
            reports[arm_name] = {"skipped": "no queries in mix"}
            continue
        fn = _make_arm(arm_name, ctx)
        picks = _roundrobin(arm_qs, args.samples)
        bnames = breaker_names.get(arm_name)

        if preflight_reason is not None:
            censored = _invalidated_arm(f"{arm_name}:censored", CAP_MS, args.samples, preflight_reason)
            elevated = _invalidated_arm(f"{arm_name}:elevated", ELEVATED_TIMEOUT_MS, args.samples, preflight_reason)
            reports[arm_name] = {
                "target_ms": PRE_REGISTERED_TARGETS_MS[arm_name],
                "censored": _arm_dict(censored, PRE_REGISTERED_TARGETS_MS[arm_name]),
                "elevated": _arm_dict(elevated, None),
            }
            if not args.quiet:
                _print_arm_row(arm_name, censored, elevated)
            continue

        # Cold-start record: first pick pre-warmup (post-boot, post-seed).
        c0 = time.perf_counter()
        fn(CAP_MS, picks[0])
        cold_start[arm_name] = (time.perf_counter() - c0) * 1000

        # tfidf enforces no timeout — a genuine completion near the cap is
        # healthy, never cap-truncated (classify_eps_ms=0).
        eps = 0.0 if arm_name == "tfidf" else CLASSIFY_CAP_EPS_MS
        censored = _run_column(arm_name, fn, picks, CAP_MS, args, bnames, classify_eps_ms=eps)
        elevated = _run_column(arm_name, fn, picks, ELEVATED_TIMEOUT_MS, args, bnames, classify_eps_ms=eps)
        reports[arm_name] = {
            "target_ms": PRE_REGISTERED_TARGETS_MS[arm_name],
            "censored": _arm_dict(censored, PRE_REGISTERED_TARGETS_MS[arm_name]),
            "elevated": _arm_dict(elevated, None),
        }
        if not args.quiet:
            _print_arm_row(arm_name, censored, elevated)

    # E2E arm (full budget: in-path encode, chain, RRF, EP annotation,
    # relationships, entity fetch, serialization).
    if not args.skip_e2e:
        e2e_qs = [q for q in all_queries if "e2e" in q.get("arms", [])]
        picks = _roundrobin(e2e_qs, args.samples)

        def e2e_fn(timeout_ms, qi):
            # #1348: thread arm_limit as pool_size so the E2E leg runs at the
            # same retrieval depth as the isolation arms (--depth desync fix).
            return _sdk_e2e(sdk, qi, timeout_ms if timeout_ms > CAP_MS else None,
                            pool_size=arm_limit)

        if preflight_reason is not None:
            censored = _invalidated_arm("e2e:censored", CAP_MS, args.samples, preflight_reason)
            elevated = _invalidated_arm("e2e:elevated", ELEVATED_TIMEOUT_MS, args.samples, preflight_reason)
            reports["e2e"] = _e2e_report(censored, elevated)
            if not args.quiet:
                _print_e2e_row(censored, elevated)
        else:
            c0 = time.perf_counter()
            # #1348: cold-start e2e at the same retrieval depth as the measured
            # arms (pool_size=arm_limit) so the cold figure is not desynced.
            sdk.tortoise_fts_query(
                query=picks[0].get("query"), kind=picks[0].get("kind"), limit=10,
                pool_size=arm_limit)
            cold_start["e2e"] = (time.perf_counter() - c0) * 1000

            censored = _run_column("e2e", e2e_fn, picks, CAP_MS, args,
                                   ["fts", "vector", "structural"])
            elevated = _run_column("e2e", e2e_fn, picks, ELEVATED_TIMEOUT_MS, args,
                                   ["fts", "vector", "structural"])
            reports["e2e"] = _e2e_report(censored, elevated)
            if not args.quiet:
                _print_e2e_row(censored, elevated)

    # 7. Assemble.
    extras = {
        "embedding_model": _resolved_embedding_model(
            use_model, getattr(args, "model", None) is not None),
        "query_prompt": getattr(args, "query_prompt", None),
        "synthetic_vectors": not use_model,
        "tfidf_available": tfidf_available,
        "indexes": indexes,
        "corpus_fingerprint": corpus_fp,
        "query_mix_meta": mix["meta"],
        "warmup": {"iters": args.warmup_iters, "max_iters": args.warmup_max_iters},
        "samples": args.samples,
        # #1348: per-arm retrieval depth + embedder/decorator pins so the
        # e2e@new-floor minus retrieval@new-floor decomposition is auditable.
        # Pins reflect REAL state (code-review P2 fix — no fabricated values):
        # embedder_pinned = whether --depth was explicitly set (the pool-20
        # comparator must be re-measured under the same embedder); decorator
        # state is asserted from the environment (#1353 not merged → status-quo).
        "arm_limit": arm_limit,
        "embedder_pinned": bool(getattr(args, "depth", None)),
        "decorator_state": (
            "status-quo" if os.environ.get("TORTOISE_1353_DECORATION", "").strip() != "1"
            else "optimized"
        ),
    }
    return {
        "schema_version": 1,
        "issue": "316",
        "seed": args.seed,
        "corpus_target": args.corpus_size,
        "seeded_points": seeded,
        "cold_start_ms": cold_start,
        "arms": reports,
        "provenance": capture_provenance(proj, is_embedded, extras),
        "notes": [
            "Per-strategy p95s are isolation measurements (single strategy, "
            "censored column = pristine prod path).",
            "hybrid p95 ≈ max(fts, vector, structural) — strategies run in "
            "PARALLEL under the 500ms collective cap.",
            "E2E verdict band: ≤300ms achieved / 300-500ms cap-dominated / "
            ">500ms+ε tail (mix-weighted p95, censored column). When >30% of "
            "censored samples are capped/degraded the p95 is computed over a "
            "non-representative minority → inconclusive.",
            "#317 headroom = 300 − full-E2E-uncensored-p95 (elevated column).",
            "Embedded FalkorDBLite numbers are NOT prod-parity (no HNSW "
            "vector index — vector brute-force; FULLTEXT index EXISTS on "
            "embedded; numbers can reverse on Docker).",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m benchmarks.run_report",
        description="Issue #316 FalkorDB retrieval latency benchmark report runner",
    )
    p.add_argument("--db", help="FalkorDB URI (docker://|bolt://) or embedded db path "
                                "(default: $TORTOISE_DB_URI or a temp embedded db)")
    p.add_argument("--corpus-size", type=int, default=1000,
                   help="synthetic corpus size (scaling arms: 10000/100000)")
    p.add_argument("--samples", type=int, default=bench_core.DEFAULT_SAMPLES,
                   help="measured samples per arm column (scoping tiers: 100/50/25)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--warmup-iters", type=int, default=bench_core.DEFAULT_WARMUP_ITERS)
    p.add_argument("--warmup-max-iters", type=int, default=bench_core.DEFAULT_WARMUP_MAX_ITERS)
    p.add_argument("--out", help="report JSON path (default: benchmarks/reports/<ts>-report.json)")
    p.add_argument("--no-seed-corpus", action="store_true",
                   help="use the existing corpus; verify indexes only")
    p.add_argument("--skip-e2e", action="store_true", help="skip the E2E SDK arm")
    p.add_argument("--model", choices=sorted(PROBE_MODELS.keys()),
                   help="embedding model short name (tools/"
                   "embedder_probe PROBE_MODELS: minilm | arctic-xs | "
                   "arctic-s | bge-small) injected before the E2E-8 "
                   "measurement; provenance records the probe-recorded model "
                   "id (#1349 pre-swap precondition runs)")
    p.add_argument("--query-prompt", help="named prompt template threaded to "
                   "the injected model (e.g. 'query' for the snowflake-arctic "
                   "vendor config prompt_name='query') — parity with run.py: "
                   "the in-path E2E-8 encode must carry the vendor prompt "
                   "prefix (it adds tokens to every measured encode)")
    p.add_argument("--load-timeout", type=float, default=None,
                   help="EmbeddingModel load timeout override (seconds) — "
                        "the first model load on a contended machine can exceed "
                        "the 30s default (bge-small measured ~57s)")
    p.add_argument("--depth", type=int, default=None,
                   help="#1348 per-strategy retrieval depth (default = the pool "
                        "floor max(2 x e2e_limit, TORTOISE_POOL_FLOOR)); the "
                        "pre-floor #316 baseline used depth 20")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    if args.query_prompt is not None and args.model is None:
        p.error("--query-prompt requires --model")

    if not args.quiet:
        print(f"#316 benchmark — corpus={args.corpus_size} samples/arm={args.samples} "
              f"elevated={ELEVATED_TIMEOUT_MS:.0f}ms")
    report = run_benchmark(args)
    out = write_outputs(report, args.out)
    print("\n" + _markdown_table(report))
    print(f"\nreport: {out['json']}\nmarkdown: {out['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
