"""Concurrent load test for the hosted search path (#1656 launch capacity).

Measures the throughput/failure behavior of ``TortoiseSDK.tortoise_fts_query``
under N concurrent workers — the search path /v1/search offloads to a thread
pool (#1676) precisely so concurrent requests overlap instead of serializing
on the event loop.

Why this shape: /v1/search's cost is dominated by the SDK query path (query
encode ~10-50ms for bge-small + the 3 search legs). A load test against the
HTTP layer needs a running server + auth; this benchmark drives the SAME SDK
call the handler uses, across a thread pool, with a warm embedded graph — the
real concurrency behavior, locally reproducible, no server required.

Usage:
    python -m benchmarks.load_test --concurrency 15 --queries 150 --seed 42
    python -m benchmarks.load_test --concurrency 50 --queries 500   # find the ceiling

Metrics per run:
  * requests/sec (completed / wall-time)
  * p50/p95/p99 per-query latency
  * failure count (0 expected — the search path is fail-soft: no embedder
    degrades to FTS, never raises)
  * effective concurrency = requests/sec x mean-latency (≈ workers when
    overlapping; ≈ 1 when serialized — the offload discriminator)
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# A fixed query mix — representative search queries (content-presence only;
# the corpus is seeded identically so results are deterministic per run).
QUERY_MIX = [
    "falkordb graph traversal",
    "how does the database handle complex queries",
    "graph performance scaling",
    "what are the traversal algorithms",
    "database indexing strategy",
    "concurrent access patterns",
    "query optimization techniques",
    "graph data model design",
]

# Rough token budget for the seeded corpus — enough points that the FTS +
# vector legs have work to do (the embedded graph writes each point's
# embedding via the injected model; without sentence-transformers installed
# the degrade path still exercises FTS, which is the fallback behavior).
CORPUS_TEXTS = [
    "The falkordb database provides excellent traversal performance for "
    "graph queries and supports complex algorithms at scale.",
    "Graph databases optimize traversal patterns differently from relational "
    "stores, trading join cost for path-finding speed.",
    "Vector similarity search finds semantically related points by embedding "
    "distance, complementing keyword matching.",
    "Hybrid retrieval fuses keyword and semantic signals with reciprocal "
    "rank fusion, weighting each retriever's rank positions.",
    "Circuit breakers protect the search path from slow downstream queries, "
    "marking failures and degrading gracefully to fallback strategies.",
    "The embedded store keeps per-team graphs isolated by namespace, so "
    "concurrent tenants never share writes.",
]


def _seed_graph(sdk, *, namespace: str) -> None:
    """Write a deterministic set of points so the search legs have work."""
    for i, text in enumerate(CORPUS_TEXTS):
        pid = f"lme:loadtest:{namespace}:p{i}"
        sdk.create_point(
            id=pid,
            content=text,
            kind="statement",
            source="load-test",
            status="live",
        )


def _run_one(sdk, query: str) -> float:
    """One search call; returns latency ms. Raises on hard failure."""
    t0 = time.monotonic()
    sdk.tortoise_fts_query(query, limit=10)
    return (time.monotonic() - t0) * 1000.0


def run_load_test(*, concurrency: int, queries: int, seed: int) -> dict:
    """Drive ``queries`` searches across ``concurrency`` workers."""
    import tortoise.hosted_api as ha_mod

    ns = f"loadtest-{seed}"
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "load.db")
        # Point the SDK at this temp DB (the same seam the test suite uses).
        import tortoise.hosted_api as _ha
        _orig = _ha.TortoiseSDK.__init__
        def _patched(self, db_path_arg=None, *, namespace=None, **kw):
            _orig(self, db_path, namespace=namespace)
        _ha.TortoiseSDK.__init__ = _patched
        try:
            sdk = ha_mod._make_sdk(namespace=ns)
            _seed_graph(sdk, namespace=ns)
            # Warm the search path once (model load / index build) before the
            # timed run — cold-start is NOT the metric here.
            _run_one(sdk, QUERY_MIX[0])

            queries_list = [QUERY_MIX[i % len(QUERY_MIX)]
                            for i in range(queries)]
            latencies: list[float] = []
            failures: list[str] = []
            lock = threading.Lock()
            start = time.monotonic()

            def _worker(q: str):
                try:
                    ms = _run_one(sdk, q)
                    with lock:
                        latencies.append(ms)
                except Exception as e:
                    with lock:
                        failures.append(repr(e))

            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                list(ex.map(_worker, queries_list))
            wall = time.monotonic() - start

            latencies.sort()
            n = len(latencies)
            req_s = n / wall if wall else 0.0
            mean_ms = statistics.mean(latencies) if latencies else 0.0
            p50 = latencies[n // 2] if n else 0.0
            p95 = latencies[int(n * 0.95)] if n else 0.0
            p99 = latencies[int(n * 0.99)] if n else 0.0
            return {
                "harness": "benchmarks/load_test.py",
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "concurrency": concurrency,
                "queries": queries,
                "seed": seed,
                "wall_seconds": round(wall, 3),
                "requests_per_sec": round(req_s, 2),
                "mean_ms": round(mean_ms, 2),
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
                "p99_ms": round(p99, 2),
                "failures": len(failures),
                "failure_examples": failures[:3],
                # The offload discriminator: effective concurrency ≈ workers
                # when requests overlap on the thread pool; ≈ 1 when they
                # serialize (pre-#1676 behavior).
                "effective_concurrency": round(
                    req_s * (mean_ms / 1000.0), 2),
            }
        finally:
            _ha.TortoiseSDK.__init__ = _orig


def _main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--concurrency", type=int, default=15,
                    help="concurrent workers (launch target = 15)")
    ap.add_argument("--queries", type=int, default=150,
                    help="total search calls across workers")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None,
                    help="write the JSON result to this path")
    args = ap.parse_args(argv)
    result = run_load_test(concurrency=args.concurrency, queries=args.queries,
                           seed=args.seed)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(f"\nresult written to: {out}")
    return result


if __name__ == "__main__":
    _main()
