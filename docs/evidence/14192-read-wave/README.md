# #14192 — the read path's legs, measured

Two artifacts, both from `tools/read_wave_latency.py` (added by this issue). They
answer the two halves of the issue's *Done when*: **a measured p50/p95 delta** and
**a rank-identity verdict**.

## What was measured, and on what

| | |
|---|---|
| box | this cell, private lane container (`tools/test_lane.py`), local FalkorDB |
| corpus | `benchmarks.synthetic_corpus`, 2000 Points, seed 42, 5000 operator edges |
| queries | the first 24 of `benchmarks/query_mix.json`, identical in both arms |
| read | `TortoiseSDK._tortoise_fts_query` — what `/v1/search` and the MCP `tortoise_search` call |
| arms | pre-change `get_relationships_bounded` at `e74cf1d13d` vs the working tree, **in one process, alternating** |

⛔ **The absolute numbers are this box's, not production's.** They price the
*change*, not the read: this container answers a graph query in single-digit
milliseconds, where the production record (`pre_handler 5996 · embed_encode
4003 · retrieval 1199 ms`) implies per-query costs an order of magnitude higher.
The percentage is the transferable number; the milliseconds are not.

⚠️ **The dense leg is DECLARED absent in both arms.** This box's embedder is
contended (`embed_get` bimodal 0 ms / ~330 ms; the dense leg declared
`vector:load_in_flight` on most queries), which would swamp a p50 comparison. Both
arms ran with `--no-embed`, so the legs declaration is `vector:load_in_flight ·
fts:ok · structural:empty_results` on both sides — the comparison isolates what the
change touches. A keyword-only read is **not** hybrid retrieval (#2952), and this
file does not claim it is.

## Result — `ab-full-read.json`

Whole read, 24 queries, same process, same corpus:

| | baseline (`e74cf1d13d`) | after | delta |
|---|---|---|---|
| **total p50** | 164.3 ms | 95.7 ms | **−68.6 ms (−41.7 %)** |
| **total p95** | 212.0 ms | 120.7 ms | **−91.3 ms (−43.1 %)** |
| `retrieval` stage p50 | 158.6 ms | 90.1 ms | −68.5 ms |
| `post_relationships` p50 | 144.6 ms | 72.3 ms *(member column)* | −72.3 ms |
| fused order | — | — | **RANK-IDENTICAL on all 24 queries** |

⛔ THE ARTIFACT IS THE AUTHORITY, this table is a transcription of it: `total` cells
from `verdict.baseline_total`/`after_total`, the two stage/member rows from
`verdict.stage_delta.retrieval` and `verdict.member_delta.post_relationships`,
and `rank_identical`/`rank_divergent_queries` verbatim. An earlier cut of this
table carried `160.7 → 79.7 / −81.0` for `retrieval` and `148.2` for the
`post_relationships` baseline, neither of which appears in the artifact
(`158.6 → 90.1 / −68.5` and `144.6`); a −81.0 ms `retrieval` delta cannot
coexist with the `−68.6 ms` total this same table states, because `retrieval`
is the stage that total is mostly made of. Re-derive with
`python3 -c "import json; print(json.load(open('ab-full-read.json'))['verdict'])"`
rather than trusting these cells.

The two arms differ in exactly one function: `get_relationships_bounded`, which
issued six graph reads in a chain. Two different figures describe it and they
are NOT interchangeable, so both are stated with what they measure:

- **the WAVE was 69 % of the read** — 159 ms of a 231 ms search, in the first
  measurement, and that is the figure `search_engine.py`'s own comment quotes
  (`159 / 231 = 68.8 %`). The wave is the post-Q1a portion the re-schedule
  covers, not the whole member.
- **the MEMBER was 167 ms p50 of that same 231 ms search** (`167 / 231 =
  72.3 %`) — and in the artifact above the `post_relationships` member falls
  from 144.6 ms to 72.3 ms.

An earlier cut attached 167 ms to the 69 % claim, which cannot both be true;
neither number was wrong, only the pairing was.

## Result — `ab-relationships.json`

The changed function in isolation, 9 alternating rounds on the same 12 ids:

| | old | new |
|---|---|---|
| p50 | 190.3 ms | 82.0 ms (**−108.3 ms**) |
| p95 | 648.3 ms | 223.5 ms |
| output | 193 entries | 193 entries — **identical** (`dict(old) == dict(new)`), `decoration_error` empty on both |

## The two halves of the change

1. **Scheduling — `_ReadWave`.** The five reads that only need `point_ids` /
   `op_ids` (CORRECTS, mitigations, critical, support, family) are *submitted*
   together and *consumed* afterwards, so they cost `max(read)` instead of
   `sum(read)`. No deadline is introduced, read or pooled: every member keeps the
   `_DECORATION_TIMEOUT_MS` it already passed to `graph.query`, so
   `#3834`'s wait budget is untouched. A member that raises still re-raises on the
   consuming thread, so the whole-batch reset + `decoration_error` labelling
   (#6971) is unchanged.
2. **The one query that dominated the wave.** Parallelising five reads only helps
   if no single member dominates — and Q2-crit did (160 ms of 190 ms): it
   evaluates a per-row Beta-variance division to decide whether a peer is
   *contested*. `alpha*beta <= s*s/4` bounds that width by `1/(4*(s+1))`, so a
   pair whose `alpha+beta >= 1/(4*threshold) - 1` **provably** cannot clear the
   threshold. That necessary precondition now runs before the division
   (`beta_variance_sum_bound`, pinned by
   `tests/test_14192_beta_variance_sum_bound.py`), taking the query to ~58 ms with
   a **byte-identical row set** (79 rows both ways, measured; and the isolation
   run above compares the whole decoration).

## Reproducing

```bash
uri="$(uv run python tools/test_lane.py uri)" || exit 1
eval "$uri"

uv run python tools/read_wave_latency.py --points 2000 --queries 24 --no-embed \
    --ab-full-read-from e74cf1d13d \
    --out docs/evidence/14192-read-wave/ab-full-read.json

uv run python tools/read_wave_latency.py --points 2000 \
    --ab-relationships-from e74cf1d13d \
    --out docs/evidence/14192-read-wave/ab-relationships.json
```

## What the first measurement found that the issue did not assume

- **The legs were already parallel.** `degradation_chain` submits fts / vector /
  structural to a `ThreadPoolExecutor` since #7780, and the measured leg wave is
  **16–18 ms of a 231 ms read**. The issue's premise ("we run three of those four
  legs, but sequentially") is true of the *chain*, not of the legs: the
  sequentiality was **after** the legs — in the decoration reads.
- **`fusion` is not the problem either** (0.2 ms p50): `rrf_fusion` is pure CPU
  over a ≤120-row pool.
- The remaining in-scope cost is a chain of **post-fusion graph reads**, which is
  what this change re-schedules and makes cheap.
