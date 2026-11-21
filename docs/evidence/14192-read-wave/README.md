# #14192 — the read path's legs, measured

Two artifacts, both from `tools/read_wave_latency.py` (added by this issue). They
answer the two halves of the issue's *Done when*: **a measured p50/p95 delta** and
**a rank-identity verdict**.

> Every number below is copied from the two JSONs in this directory, not from an
> earlier run. If a number is not in a JSON here, it is not claimed here.

## What was measured, and on what

| | |
|---|---|
| box | this cell, private lane container (`tools/test_lane.py`), local FalkorDB |
| corpus | `benchmarks.synthetic_corpus`, 2000 Points, seed 42, 5000 operator edges |
| queries | the first 24 of `benchmarks/query_mix.json`, identical in both arms |
| read | `TortoiseSDK._tortoise_fts_query` — what `/v1/search` and the MCP `tortoise_search` call |
| arms | pre-change `get_relationships_bounded` out of `e74cf1d13d` vs the working tree, **in one process, alternating** |
| code measured | the working tree at the time of measurement — ⛔ **the recorded `git_sha` does not resolve, so this row cannot be checked as written**; see the provenance note below |

⛔ **PROVENANCE: BOTH ARTIFACTS RECORD A `git_sha` THAT DOES NOT EXIST IN THIS
REPOSITORY.** They carry `git_sha: 0667b5358950`, and `git cat-file -t
0667b5358950` answers `fatal: Not a valid object name` — checked in this clone
and in the hub checkout, which share one object DB. So `git diff 0667b5358950
1c07761e69` cannot be computed at all, and the claim an earlier cut of this table
made with it ("`tortoise/`, `tests/` and `tools/` are byte-identical to the merged
head") is **unverifiable and withdrawn**. The sha is what the harness recorded
(`_git_sha()` is the measuring checkout's `HEAD`); it is a commit a later amend or
rebase discarded. It is left in the JSONs rather than rewritten, because an
artifact edited to look resolvable is worse than one that says what it is.

What IS verifiable from the artifacts, and is what the claims below rest on: both
arms ran in ONE process against ONE corpus (`points: 2000`, `corpus_seed: 42`,
`queries: 24` in both); each arm recorded **240 order ids, 0 nulls, 0 empty
pages**; `comparable: true`; and the pre-change arm was materialised with
`git show e74cf1d13d:tortoise/search_engine.py`, a revision that DOES resolve.
Read the delta and the rank verdict as measurements of this change on this box.
Do not read the sha as a pin on a commit.

⛔ **The absolute numbers are this box's, not production's.** They price the
*change*, not the read: this container answers a graph query in single-digit
milliseconds, where the production record (`retrieval 1199 ms`) implies per-query
costs an order of magnitude higher. The **percentage** is the transferable number;
the milliseconds are not.

⚠️ **The dense leg is DECLARED absent in both arms.** This box's embedder is
contended (`embed_get` bimodal 0 ms / ~330 ms; the dense leg declared
`vector:load_in_flight` on every query), which would swamp a p50 comparison. Both
arms ran with `--no-embed`. ⚠️ **Read what that flag does and does not do:** it
patches `EmbeddingModel.get`, but the read resolves its decline FIRST via
`EmbeddingModel.await_read_ready_reason(...)`, which returns `load_in_flight` when
the model has not warmed inside the read's budget — so on this box the flag never
executed and the declaration below is the BOX's, not the flag's. That is still a
deterministic comparison (both arms, same declaration, same box), and it is
stated here rather than credited to the flag. Both arms declare:

```
vector:load_in_flight · fts:ok · structural:empty_results · valid_time:no-prefer-latest-intent
```

A keyword-only read is **not** hybrid retrieval (#2952), and this file does not
claim it is.

## Result — `ab-full-read.json`

Whole read, 24 queries, same process, same corpus, arms differing only in
`get_relationships_bounded`:

| | baseline (`e74cf1d13d`) | after | delta |
|---|---|---|---|
| **total p50** | 162.3 ms | **118.8 ms** | **−43.5 ms (−26.8 %)** |
| **total p95** | 198.2 ms | **155.7 ms** | **−42.5 ms (−21.4 %)** |
| `retrieval` stage p50 | 157.1 ms | 113.7 ms | −43.4 ms |
| `post_relationships` p50 (member column) | 143.0 ms | 101.2 ms | −41.9 ms |
| `fusion` p50 | 0.0 ms | 0.0 ms | 0.0 |
| **fused order** | — | — | **RANK-IDENTICAL on all 24 queries** (real ids — `rank_divergent_queries: []`) |

⛔ THE ARTIFACT IS THE AUTHORITY, this table is a transcription of it: `total` cells
from `verdict.baseline_total`/`after_total`, the two stage/member rows from
`verdict.stage_delta.retrieval` and `verdict.member_delta.post_relationships`,
and `rank_identical`/`rank_divergent_queries` verbatim. Re-derive with
`python3 -c "import json; print(json.load(open('ab-full-read.json'))['verdict'])"`
rather than trusting these cells.

The two arms differ in exactly one function: `get_relationships_bounded`, which
issued six graph reads in a chain and was the dominant post-fusion read. Two
figures describe it and they are NOT interchangeable, so both are stated with
what they measure. ⚠️ Both are the FIRST measurement's figures (kept because
this file's history records them), not cells of the artifact above — the
artifact's own figures for the same function are
`verdict.member_delta.post_relationships`:

- **the WAVE was ~69 % of the read** — 159 ms of a 231 ms search in the first
  measurement, which is the figure `search_engine.py`'s own comment quotes
  (`159 / 231 = 68.8 %`). The wave is the post-Q1a portion the re-schedule
  covers, not the whole member.
- **the MEMBER was 167 ms p50 of that same 231 ms search** (`167 / 231 =
  72.3 %`). Its movement on the current head is
  `verdict.member_delta.post_relationships` in `ab-full-read.json`.

⚠️ **An earlier cut of the table below disagreed with its own JSON** — it carried
`160.7 → 79.7 / −81.0` for `retrieval` and `148.2` for the
`post_relationships` baseline, copied from a harness run made BEFORE the
metering fix. A `−81.0 ms` retrieval delta cannot coexist with the `−68.6 ms`
total that same table stated, because `retrieval` is the stage that total is
mostly made of. The table and both JSONs are regenerated together from the head
that will merge; **if a future edit changes one it must change the other.**

⚠️ **Run-to-run variance is real and is not hidden.** Successive whole-read A/Bs
on this box measured `−68.6`, `−48.4` and `−43.5 ms` p50 for the same change
(the box runs a fleet; load moved between them). The *direction and the
rank-identity* were stable in all of them; the size of the win is not a constant
and none of these should be quoted as a production figure.

## Result — `ab-relationships.json`

The changed function in isolation, 9 alternating rounds on the same 12 ids:

| | old | new |
|---|---|---|
| p50 | 191.2 ms | 117.5 ms (**−73.7 ms**) |
| output | 193 entries | 193 entries — **identical** (`dict(old) == dict(new)`), `decoration_error` empty on both |

## The two halves of the change

1. **Scheduling — `_ReadWave`.** The five reads that only need `point_ids` /
   `op_ids` (CORRECTS, mitigations, critical, support, family) are *submitted*
   together and *consumed* afterwards, so they cost `max(read)` instead of
   `sum(read)`. No deadline is introduced, read or pooled: every member keeps the
   `_DECORATION_TIMEOUT_MS` it already passed to `graph.query`, so #3834's wait
   budget is untouched. A member that raises still re-raises on the consuming
   thread (pinned by `test_a_raising_wave_member_resets_the_batch_and_labels_it`),
   so the whole-batch reset + `decoration_error` labelling (#6971) is unchanged.
   Each member is submitted wrapped in `graph_ops.bind_metering_context` — without
   it the capture's graph-op counter silently missed every read the wave
   performs (that is exactly what `tests/test_capture_graph_ops.py` measured
   before the wrapper was added: 6 reads recorded 1). ⛔ `retrieval_metering`
   is deliberately **NOT** bound: that tally accounts the retrieval LEGS, and
   these are decoration graph ops, not legs (#2952).
2. **The one query that dominated the wave.** Parallelising five reads only helps
   if no single member dominates — and Q2-crit did: it evaluates a per-row
   Beta-variance division to decide whether a peer is *contested*.
   `alpha*beta <= s*s/4` bounds that width by `1/(4*(s+1))`, so a pair whose
   `alpha+beta` is at or above `1/(4*threshold) - 1` **provably** cannot clear the
   threshold. `beta_variance_gt_cypher` now emits that necessary guard and the
   width comparison together from ONE owner, before the division.

   The correctness claim is pinned twice: by
   `tests/test_14192_beta_variance_sum_bound.py` (the guard never rejects a
   contested pair, across a grid including negatives, subnormals and the band
   edges) and by the isolation run above, which compares the whole decoration
   object and finds it identical. ⚠️ The timing of the crit query alone (a scratch
   probe — not committed, so not quoted here) is what motivated the guard; the
   artifact for its effect is `ab-relationships.json`'s whole-function delta.

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
  **5–6 ms of a 231 ms read** (`legs_wave` in the member table). The issue's
  premise ("we run three of those four legs, but sequentially") is true of the
  *chain*, not of the legs: the sequentiality was **after** the legs — in the
  decoration reads.
- **`fusion` is not the problem either** (0.0 ms p50): `rrf_fusion` is pure CPU
  over a ≤120-row pool.
- The remaining in-scope cost is a chain of **post-fusion graph reads**, which is
  what this change re-schedules and makes cheap.
