"""Phase-by-phase latency profiler for the Tortoise ask read path (WAVE-R / M1).

Instruments the REAL read path (`run_ask_lane` -> `tortoise_fts_query`) with
runtime monkeypatches (NO product-code change) and reports:

  * a phase -> p50 / p95 / max table (ms)
  * graph round-trips per query, grouped by the calling function
  * the sequential post-retrieval chain total

Read-only w.r.t. any real store: it seeds an in-memory graph from the frozen
ask fixture. The reader is a deterministic stub by default; `--real-reader N`
also runs N real pinned-reader calls (paid).

Requires the `falkordblite` embedded extra. Run from the repo root:

    TORTOISE_ALLOW_NONSTANDARD_PATH=1 \
        uv run python tools/profile_read_path.py --queries 12

See docs/research/2026-09-19-m1-read-path-latency-profile.md for the measured
results and their interpretation.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# The local ask lane refuses hosted client mode.
os.environ.pop("TORTOISE_API_URL", None)
os.environ.pop("TORTOISE_API_KEY", None)

FIXTURE = Path(_REPO_ROOT) / "tests" / "fixtures" / "ask_spotcheck_composition.json"

PHASES: dict[str, list[float]] = {}
PH_LOCK = threading.Lock()
GRAPH: list[tuple[str, str, float]] = []


def _rec(phase: str, ms: float) -> None:
    with PH_LOCK:
        PHASES.setdefault(phase, []).append(ms)


def _wrap(target, name: str, phase: str) -> None:
    orig = getattr(target, name)

    def f(*a, **k):
        t = time.perf_counter()
        try:
            return orig(*a, **k)
        finally:
            _rec(phase, (time.perf_counter() - t) * 1000)

    setattr(target, name, f)


def install() -> None:
    """Install the phase timers + the graph round-trip recorder."""
    import tortoise.ask_lane as AL
    import tortoise.embeddings as E
    import tortoise.projection as P
    import tortoise.reader as R
    import tortoise.rerank as RR
    import tortoise.retrieval as RET
    import tortoise.sdk as SDK
    import tortoise.search_engine as SE

    orig_q = P._GuardedGraph.query

    def gq(self, cypher, params=None, timeout=None):
        t = time.perf_counter()
        try:
            return orig_q(self, cypher, params=params, timeout=timeout)
        finally:
            ms = (time.perf_counter() - t) * 1000
            caller = sys._getframe(1).f_code.co_name
            with PH_LOCK:
                GRAPH.append((caller, " ".join(str(cypher).split())[:70], ms))

    P._GuardedGraph.query = gq

    _wrap(SE, "classify_query", "classify")
    _wrap(R, "detect_question_type", "detect_qtype")
    # EmbeddingModel.get() returns the underlying SentenceTransformer.
    try:
        from sentence_transformers import SentenceTransformer
        _wrap(SentenceTransformer, "encode", "query_encode")
    except Exception:
        _wrap(E.EmbeddingModel, "encode", "query_encode")

    _wrap(SE, "degradation_chain", "legs_wave")
    _wrap(SE, "run_fts_query", "leg_fts")
    _wrap(SE, "run_vector_query", "leg_vector")
    if hasattr(SE, "run_structural_query"):
        _wrap(SE, "run_structural_query", "leg_structural")
    _wrap(SE, "rrf_fusion", "fusion")
    _wrap(SDK.TortoiseSDK, "_search_keys_prf_expansion", "prf_expansion")

    _wrap(SE, "annotate_ep_batch", "post_ep_annotate")
    _wrap(SE, "get_relationships_bounded", "post_relationships")
    _wrap(SE, "fetch_point_epistemic_state", "post_epistemic_state")
    _wrap(SDK.TortoiseSDK, "annotate_ask_hits", "post_ask_annotate")

    _wrap(RET, "dedup_pool", "dedup")
    _wrap(RET, "apply_evidence_boost", "evidence_boost")
    _wrap(RET, "assemble_context", "assemble")
    _wrap(RET, "render_context", "render")
    _wrap(RET, "estimate_tokens_ask", "token_estimate")
    _wrap(RR, "ask_lane_rerank", "rerank")

    _wrap(AL, "_ask_reader_complete", "reader")


class StubReader:
    """Deterministic reader: no paid call, no effect on retrieval timing."""

    last_route = "stub"
    last_finish_reason = "stop"
    last_completion_tokens = 5

    def __init__(self, model: str = "stub-reader"):
        self.model = model

    def complete(self, *, system: str, user: str, max_tokens=None) -> str:
        return "The evidence is insufficient to answer."

    def close(self) -> None:
        pass

    def failed(self) -> bool:
        return False

    def incr_inflight(self) -> None:
        pass

    def decr_inflight(self) -> None:
        pass


def stats(xs: list[float]) -> dict:
    xs = sorted(xs)
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "p50": round(xs[len(xs) // 2], 3),
        "p95": round(xs[min(len(xs) - 1, int(len(xs) * 0.95))], 3),
        "max": round(xs[-1], 3),
        "mean": round(statistics.fmean(xs), 3),
    }


PHASE_NAMES = [
    "query_encode", "classify", "detect_qtype", "legs_wave", "leg_fts",
    "leg_vector", "leg_structural", "prf_expansion", "fusion",
    "post_ep_annotate", "post_relationships", "post_epistemic_state",
    "post_ask_annotate", "dedup", "evidence_boost", "assemble", "render",
    "token_estimate", "rerank", "reader",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=int, default=12)
    ap.add_argument("--real-reader", type=int, default=0)
    ap.add_argument("--out", default="m1-read-path-latency.json")
    args = ap.parse_args()

    import tools.ask_spotcheck as spot
    from tortoise import ask_lane as AL
    from tortoise.sdk import TortoiseSDK

    install()

    fixture = json.loads(FIXTURE.read_text())
    # One realistic haystack; per-query leg cost is capped by the 120 pool.
    seed_q = next(q for q in fixture if q["question_id"] == "e9327a54")
    sdk = TortoiseSDK(db_path=":memory:")
    proj = sdk._get_proj()
    # W7A pinned ``embed=False``: the profile's recorded corpus carries NO
    # embedding (docs/research/2026-09-19-m1-read-path-latency-profile.md); the
    # seeder now defaults to embedded, so leaving this un-pinned would silently
    # change the measured corpus.
    spot._seed_memory(sdk, seed_q, embed=False)
    n_points = proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
    print(f"seeded haystack: {n_points} Points")

    texts = [q["question"] for q in fixture][: args.queries]
    while len(texts) < args.queries:
        texts.append(texts[-1])

    with PH_LOCK:
        GRAPH.clear()
    PHASES.clear()
    AL._reset_ask_reader_cache_for_tests()
    AL.run_ask_lane(sdk, texts[0], _reader_factory=StubReader)  # warm-up
    print("warm-up done")

    per_query = []
    for i, qtext in enumerate(texts):
        with PH_LOCK:
            GRAPH.clear()
        PHASES.clear()
        AL._reset_ask_reader_cache_for_tests()
        t0 = time.perf_counter()
        AL.run_ask_lane(sdk, qtext, _reader_factory=StubReader)
        total = (time.perf_counter() - t0) * 1000
        with PH_LOCK:
            g = list(GRAPH)
        per_query.append({
            "total_ms": total,
            "phases": {k: sum(v) for k, v in PHASES.items()},
            "round_trips": len(g),
            "calls": g,
        })
        print(f"  q{i}: total={total:.0f}ms rt={len(g)} "
              f"graph={sum(x[2] for x in g):.1f}ms")

    print("\n================ PHASE TABLE (ms, per query) ================")
    print(f"{'phase':<24} {'p50':>8} {'p95':>8} {'max':>8} {'mean':>8}")
    agg = {}
    for p in PHASE_NAMES:
        xs = [pq["phases"].get(p, 0.0) for pq in per_query]
        st = stats([x for x in xs if x > 0] or [0.0])
        agg[p] = st
        print(f"{p:<24} {st['p50']:>8.3f} {st['p95']:>8.3f} "
              f"{st['max']:>8.3f} {st['mean']:>8.3f}")
    tot = stats([pq["total_ms"] for pq in per_query])
    print(f"{'ASK TOTAL (stub reader)':<24} {tot['p50']:>8.3f} "
          f"{tot['p95']:>8.3f} {tot['max']:>8.3f} {tot['mean']:>8.3f}")

    all_calls = [c for pq in per_query for c in pq["calls"]]
    by_caller: dict[str, list[float]] = {}
    for caller, _head, ms in all_calls:
        by_caller.setdefault(caller, []).append(ms)
    rt_counts = stats([float(pq["round_trips"]) for pq in per_query])
    print("\n================ GRAPH ROUND-TRIPS / QUERY ================")
    print(f"total round-trips: p50={rt_counts['p50']} p95={rt_counts['p95']} "
          f"max={rt_counts['max']} min={min(pq['round_trips'] for pq in per_query)}")
    print(f"{'caller':<30} {'n/q':>6} {'p50ms':>8} {'mean/q':>10}")
    for caller, xs in sorted(by_caller.items(),
                             key=lambda kv: -statistics.fmean(kv[1])):
        st = stats(xs)
        print(f"{caller:<30} {len(xs) / len(per_query):>6.1f} "
              f"{st['p50']:>8.3f} {st['mean']:>10.3f}")

    post_callers = {"annotate_ep_batch", "get_relationships_bounded",
                    "fetch_point_epistemic_state", "annotate_ask_hits"}
    post_stats = stats([
        sum(c[2] for c in pq["calls"] if c[0] in post_callers)
        for pq in per_query])
    # The entity-content fetch is inline in ``tortoise_fts_query``; on the ask
    # lane's defaults it is the ONLY query attributed to that caller.
    post_incl_stats = stats([
        sum(c[2] for c in pq["calls"]
            if c[0] in post_callers or c[0] == "tortoise_fts_query")
        for pq in per_query])
    print("\npost-retrieval chain (ep_annotate+relationships+epistemic+ask_annotate):")
    print(f"  p50={post_stats['p50']}ms p95={post_stats['p95']}ms; "
          f"incl. entity fetch p50={post_incl_stats['p50']}ms")

    real_reader = None
    if args.real_reader > 0:
        print(f"\n================ REAL READER ({args.real_reader} paid calls) ================")
        real = []
        for i in range(args.real_reader):
            with PH_LOCK:
                GRAPH.clear()
            PHASES.clear()
            AL._reset_ask_reader_cache_for_tests()
            t0 = time.perf_counter()
            resp = AL.run_ask_lane(sdk, texts[i % len(texts)])
            total = (time.perf_counter() - t0) * 1000
            rd = sum(PHASES.get("reader", [0.0]))
            real.append({"total_ms": total, "reader_ms": rd,
                         "provider": resp.get("provider"),
                         "model": resp.get("model"),
                         "cost": resp.get("cost_estimate_usd")})
            print(f"  real q{i}: total={total:.0f}ms reader={rd:.0f}ms "
                  f"provider={resp.get('provider')} model={resp.get('model')} "
                  f"cost=${resp.get('cost_estimate_usd')}")
        real_reader = {"runs": real,
                       "reader_ms": stats([r["reader_ms"] for r in real])}

    out = {
        "n_points": n_points,
        "queries": args.queries,
        "phase_table": agg,
        "ask_total_stub": tot,
        "round_trips_per_query": rt_counts,
        "by_caller": {k: stats(v) for k, v in by_caller.items()},
        "post_retrieval_chain_ms": post_stats,
        "post_retrieval_chain_incl_entity_fetch_ms": post_incl_stats,
        "real_reader": real_reader,
        "raw_per_query": [
            {"total_ms": pq["total_ms"], "phases": pq["phases"],
             "round_trips": pq["round_trips"]} for pq in per_query],
    }
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
