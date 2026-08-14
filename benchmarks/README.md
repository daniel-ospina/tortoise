# Tortoise Retrieval Latency Benchmark (issue #316)

Benchmark harness for the FalkorDB hybrid retrieval path: FTS, vector, hybrid
RRF, and TF-IDF fallback latency — plus the E2E-8 end-to-end 300ms verdict.

Pre-registered numbers and verdict bands are **fixed** by the issue #316
scoping doc (v5.1) — do not change them without a scoping revision. They live
in `benchmarks/bench_core.py` (`PRE_REGISTERED_TARGETS_MS`, `E2E_TARGET_MS`,
`CAP_MS`, `ELEVATED_TIMEOUT_MS`) and are locked by tests
(`tests/bench/test_bench_core.py`).

## Pre-registered targets

| Strategy | Target (isolation p95, censored column) |
|---|---|
| FTS | < 50 ms |
| Vector | < 100 ms |
| Hybrid RRF | < 200 ms (≈ max(strategy) — strategies run in PARALLEL under the 500ms collective cap) |
| TF-IDF fallback | < 500 ms |
| E2E-8 (full path, mix-weighted p95) | ≤ 300 ms |

**E2E-8 verdict bands:** ≤ 300 ms → `achieved` · 300–500 ms → `cap-dominated`
(the 500ms collective cap dominates; budget eaten by the cap, not strategy
work) · > 500 ms + ε → `tail` (join-tail).

**#317 handoff:** `headroom = 300 − full-E2E-uncensored-p95` (elevated column)
bounds the cross-encoder reranker budget.

## Quickstart (embedded smoke — fast, NOT prod-parity)

```bash
# Requires: falkordblite (core dep) + scikit-learn (dev group).
# Embedded FalkorDBLite has NO FTS/HNSW → FTS degrades, vector runs
# brute-force. Numbers can reverse on Docker — smoke only.
python -m benchmarks.run_report --corpus-size 200 --samples 5 --warmup-iters 2
```

## Full run (prod-parity — the measurement environment)

```bash
# 1. Docker FalkorDB >= 4.x (HNSW + FTS indexes auto-created at boot by
#    projection._ensure_indexes) — e.g. `docker compose up -d`.
# 2. embeddings extra for in-path encode:  pip install -e '.[embeddings]'
export TORTOISE_DB_URI="docker://falkor:6379"   # or bolt://...
python -m benchmarks.run_report --corpus-size 1000 --samples 50
```

Scaling arms (identify where each strategy exceeds its budget and the cap
starts dropping strategies):

```bash
python -m benchmarks.run_report --corpus-size 10000 --samples 50
python -m benchmarks.run_report --corpus-size 100000 --samples 50
```

`--db` accepts a URI (`docker://`/`bolt://`) or an embedded db file path
(default: `$TORTOISE_DB_URI`, else a temp embedded db). `--no-seed-corpus`
runs against an existing corpus (indexes are verified, never silently
created — "verify-not-create").

## What it measures

- **Per-strategy isolation arms** (`fts` / `vector` / `hybrid` / `tfidf`),
  each in a **two-column protocol**: censored (default 500ms cap — pristine
  prod path) vs elevated-cap (5000ms, true completion; unpaired).
- **E2E arm** via `sdk.tortoise_fts_query` — the full budget composition:
  in-path embedding encode, degradation chain, RRF fusion + post-processing,
  EP annotation, relationships, entity fetch, serialization.
- **Warmup-then-measure**: warmup iterations run discarded until CV < 10% or
  the max-iteration cap, then measured samples (mix-weighted, round-robin
  over `query_mix.json`, ≥50 distinct queries incl. no-match degrade triggers
  and kind-bearing queries).
- **Failure taxonomy**: `healthy` / `degraded` / `capped` (right-censored) /
  `capped-tail` / `invalidating` / `breaker-open`. Capped and degraded
  samples are **never** in the p50/p95/p99 distribution (right-censoring).
- **Cold-start record**: first invocation per arm post-boot, excluded from
  the distributions.

## Output

- Machine-readable JSON: `benchmarks/reports/<timestamp>-report.json` (+
  `latest.json`), with per-arm censored/elevated stats, pass/flag verdicts,
  E2E-8 verdict, #317 headroom, and provenance (git SHA, host specs, DB mode,
  FalkorDB version, embedding model identity, index state, corpus
  fingerprint, query-mix meta, warmup config).
- Markdown table printed to stdout and saved alongside the JSON.
- `benchmarks/reports/` is gitignored — commit a report by copying it
  explicitly (e.g. `docs/research/`).

## Files

| File | Purpose |
|---|---|
| `bench_core.py` | Measurement core (percentiles, warmup, arm runner, taxonomy, verdicts) — DB-agnostic |
| `query_mix.json` | 56 pre-registered queries (match / kind-bearing / structural-only / no-match degrade) |
| `synthetic_corpus.py` | EP-structured corpus seeding (vectors, posterior α/β, operator edges, retracted) + index verification |
| `run_report.py` | Standalone report runner (`python -m benchmarks.run_report`) |

## Tests

```bash
python -m pytest tests/bench/ -v -m bench
```

- `test_bench_core.py` — pure-logic tests (percentiles, right-censoring,
  taxonomy, warmup, breaker hygiene, verdict bands) — no DB needed.
- `test_smoke_embedded.py` — end-to-end harness smoke on embedded
  FalkorDBLite (small corpus). **Warn-only** semantics: degraded arms are
  expected on embedded; the test fails only on harness-level errors
  (exception, INVALIDATED arm, missing output).

## Scope boundaries (per scoping doc)

- **P/R@K and competitive baselines** (Neo4j / Supermemory / Honcho) are
  DEFERRED to a separate quality study (no labeled retrieval set exists).
- **Embedded FalkorDBLite is excluded** from prod-parity numbers (brute-force
  vector, no FTS/HNSW — "numbers can reverse"). The Docker/HNSW path is the
  measurement environment.
- The elevated E2E column threads `_elevated_timeout_ms` through the SDK
  hybrid-search path (private, default-off — production behavior unchanged).
