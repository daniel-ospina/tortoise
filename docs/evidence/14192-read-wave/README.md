---
title: "#14192 - the read path's legs, measured"
type: evidence
domain: platform
doc_status: live
created: 2026-11-22
subjects.team: epistemic-team
aboutSubjects: Tortoise read path, read-wave latency, rank identity, p50/p95 delta
aboutObjects: "#14192, tools/read_wave_latency.py, docs/evidence/14192-read-wave/ab-full-read.json, docs/evidence/14192-read-wave/ab-relationships.json"
---

# #14192 — the read path's legs, measured

Two artifacts, both from `tools/read_wave_latency.py` (added by this issue). They
answer the two halves of the issue's *Done when*: **a measured p50/p95 delta** and
**a rank-identity verdict**.

> Every number in the **two result tables** below is either a **cell** of the two
> JSONs in this directory or **arithmetic on named cells** of them — never from
> an earlier run. The percentages and the mean delta are that arithmetic, not
> leaves of either JSON (`−26.8 %` = `total_delta_p50_ms / baseline_total.p50`,
> `−21.4 %` = `total_delta_p95_ms / baseline_total.p95`, `−26.3 ms` = the mean's
> `old_ms − new_ms`). ⚠️ The FIRST table is the exception and is not a results
> table: it carries the corpus and provenance. `2000` Points and seed `42` are
> cells of `ab-full-read.json` (both its `baseline` and `after` arms); `5000`
> operator edges is a harness INPUT that appears in NEITHER JSON (`grep -c 5000`
> is `0` in both); and the `git_sha` row is provenance, not a measurement.
>
> ⛔ **That rule governs the TABLES. Figures in the prose are a different class,
> and this file has drifted on exactly that boundary before** — so the rule is
> **every prose figure names its source where it stands.** These are the classes
> its sources fall into; it is a MAP, not a closed inventory, so a figure that is
> a code constant or a derivation says so where it stands:
>
> - the `159 ms` / `231 ms` / `167 ms` bullet pair under "Result —
>   `ab-full-read.json`" (NOT under "What the first measurement found", whose
>   bullets are artifact cells apart from its `≤120-row` pool constant, which is
>   one of the code constants below) is the FIRST measurement's, and says so there.
> - `5000` operator edges (corpus row) is a harness INPUT — `grep -c 5000` over
>   both JSONs is `0`.
> - the `26,463` / `14,732` character counts are **git** artifacts, not JSON
>   cells: the Q2-crit statement's length before and after `7d276aa07a`, readable
>   with `git show 5c9cfe5407:tortoise/search_engine.py` vs
>   `git show 7d276aa07a:tortoise/search_engine.py`.
> - `retrieval 1199 ms` is the **named external production record** in the
>   paragraph below (`grep -c 1199` over both JSONs is `0` — it is not ours).
> - `embed_get` bimodal `0 ms / ~330 ms`, and the `−68.6` / `−48.4` / `−43.5 ms`
>   variance list, are **this box's own earlier runs**, labelled as such where
>   they appear; only `−43.479` (`verdict.total_delta_p50_ms`) is an artifact
>   cell.
> - **`6 reads recorded 1`** counter op is a **stub-graph probe**, not an
>   artifact cell: it is produced by an instrumented stub graph in
>   `test_every_wave_read_is_visible_to_the_capture_counter`, and it measures the
>   capture counter, not latency.
> - **code constants** are read out of the code they name, not out of a JSON:
>   `VARIANCE_SUM_MIN` `1e-154` / `VARIANCE_SUM_MAX` `1e102`
>   (`tortoise/search_engine.py:5792-5793`), the `5.24`–`5.26` band that `_grid()`
>   constructs in the test file, and the `≤120-row` fusion pool
>   (`tortoise/retrieval.py:128`, `DEFAULT_POOL_SIZE = 120`).
>
> ⚠️ Earlier cuts of this note twice claimed a stronger form — that every number
> was an artifact cell, then that no other *performance* figure was outside the
> JSONs. Both were falsified by their own file, which is what a global claim in a
> document this long always is. Hence the per-figure rule above instead.

## What was measured, and on what

| | |
|---|---|
| box | this cell, private lane container (`tools/test_lane.py`), local FalkorDB |
| corpus | `benchmarks.synthetic_corpus`, 2000 Points, seed 42, 5000 operator edges |
| queries | the first 24 of `benchmarks/query_mix.json`, identical in both arms |
| read | `TortoiseSDK._tortoise_fts_query` — what `/v1/search` and the MCP `tortoise_search` call |
| arms | pre-change `get_relationships_bounded` out of `e74cf1d13d` vs the working tree, in one process — ⚠️ **the two artifacts interleave differently and the difference matters for drift**: `ab-relationships` alternates old/new **per round** (9 rounds), while `ab-full-read` runs **two sequential 24-query blocks** (baseline first, then after), so a load spike lands entirely inside one arm there |
| code measured | the working tree at the time of measurement — ⛔ **the recorded `git_sha` names a commit reachable from no ref**, so this row cannot be checked from a fresh clone; see the provenance note below |

⛔ **PROVENANCE: BOTH ARTIFACTS RECORD A `git_sha` THAT NO REF REACHES.** They
carry `git_sha: 0667b5358950`, and `git for-each-ref --contains 0667b5358950`
returns **no refs** — it is a commit a later amend or rebase discarded, which is
the harness recording the measuring checkout's `HEAD` (`_git_sha()`). ⚠️ **What
that does and does not mean, stated narrowly because an earlier cut of this
paragraph over-claimed it:** the object still resolves in a clone that has
fetched the discarded branch (`git cat-file -t` answers `commit` there), so "it
does not exist" is too strong — but a FRESH clone and CI cannot resolve it, which
is what makes the row uncheckable as written. It is left in the JSONs rather
than rewritten, because an artifact edited to look resolvable is worse than one
that says what it is.

The claim an earlier cut of this table made with the sha — "`tortoise/`, `tests/`
and `tools/` are byte-identical to the merged head" — is **withdrawn**, and not
because the diff was uncomputable: `git diff 0667b5358950 1c07761e69 -- tortoise
tests tools` is EMPTY, so those trees are identical to `1c07761e69`; but
`1c07761e69` is **not the merged head**. Against the merged head (`94d75667dc`)
the same paths differ in 46 files. So the sha pins the measured code only relative
to a discard-point ancestor, which is not a statement anyone should rely on.

What IS verifiable from the artifacts, and is what the claims below rest on: both
arms ran in ONE process against ONE corpus (`points: 2000`, `corpus_seed: 42`,
`queries: 24` in both); each arm recorded `sum(len(q["order"]) for q in
per_query)` = **240 order ids** across its 24 `per_query` entries, with `0` nulls
and `0` empty pages; `comparable: true`; and the pre-change arm was materialised with
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

```text
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
  measurement (`159 / 231 = 68.8 %`). ⚠️ An earlier cut of `search_engine.py`'s
  wave comment quoted this same figure; that comment now **withdraws** it and
  points at `ab-full-read.json` instead, so the comment is NOT a source for these
  two numbers — this file's own history is. The wave is the post-Q1a portion the
  re-schedule covers, not the whole member.
- **the MEMBER was 167 ms p50 of that same 231 ms search** (`167 / 231 =
  72.3 %`). Its movement on the current head is
  `verdict.member_delta.post_relationships` in `ab-full-read.json`.

⚠️ **An earlier cut of the table above disagreed with its own JSON** — it carried
`160.7 → 79.7 / −81.0` for `retrieval` and `148.2` for the
`post_relationships` baseline, copied from a harness run made BEFORE the
metering fix. A `−81.0 ms` retrieval delta cannot coexist with the `−68.6 ms`
total that same table stated, because `retrieval` is the stage that total is
mostly made of. The table and both JSONs are regenerated together; **if a future
edit changes one it must change the other.**

⛔ **THE JSONs describe `5c9cfe5407`, NOT THIS HEAD.** That commit re-measured
both artifacts, and **two later commits then changed the code they measure
without regenerating them**, in this chronological order: `6ec51de9ec` added the
`_InlineMember` inline fallback, then `7d276aa07a` removed the duplicated
predicate from the Q2-crit query (26,463 chars → 14,732,
`$contested_threshold` twice → once). ⚠️ **WHICH WAY THAT
BIASES THE NUMBER IS NOT ASSERTED HERE, and an earlier cut of this paragraph got
it backwards:** the after-arm is priced on code that (for the crit query) was
HEAVIER than this head's, which makes the reported win too SMALL rather than too
large — while the inline fallback could push the net the other way. With
two commits of opposite sign the direction is not derivable from the char count
alone, so the operative statement is the direction-free one: **the after-arm
prices code this head no longer runs.** Re-measure before quoting any after-arm
figure as the head's.

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
| mean | 205.9 ms | 179.6 ms (−26.3 ms) |
| p95 | 273.0 ms | **502.6 ms** (⚠️ **worse**) |
| max | 273.0 ms | **502.6 ms** (⚠️ **worse**) |
| output | 193 entries | 193 entries — **identical** (`dict(old) == dict(new)`), `decoration_error` empty on both |

⚠️ **The tail moved the WRONG WAY and that is stated here rather than left in
the JSON.** `n=9` aggregated rounds: the new arm's worst round is **502.6 ms**
against the old arm's **273.0 ms** (its own p50 × 4.3). p50 improves 38.6 % and
the mean only 12.8 % — both are arithmetic on `ab-relationships.json`'s cells
(`73.72 / 191.245` and `26.32 / 205.888`) — which is the signature of **one
outlier round**, and the
artifact stores aggregates with no per-round series — so it cannot be attributed
here. It is either first-round pool warm-up or a real tail on per-call pool
creation, and **nobody knows which**, which is exactly why the figure is
reported instead of dropped. The whole-read artifact is clean on the same axis
(`p95 198.2 → 155.7 ms`, `max 207.9 → 165.1 ms`, n=24), so this reads as an
open question about the isolation run, not a refutation of the change.

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
   performs -- measured on a stub graph: **6 reads recorded 1** counter op.
   The pin for that number is
   `tests/test_14192_beta_variance_sum_bound.py::test_every_wave_read_is_visible_to_the_capture_counter`,
   NOT `tests/test_capture_graph_ops.py` (an earlier cut of this sentence
   pointed there; that file contains no wave test at all -- it pins the
   generic binder for one worker op, `0 -> 1`). ⛔ `retrieval_metering`
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
   contested pair, across a **positive** grid spanning `VARIANCE_SUM_MIN`
   `1e-154` … `VARIANCE_SUM_MAX` `1e102`, the exact halves of the bound from
   `5.24` to `5.26`, and the band edges — ⚠️ `_grid()` constructs no negative and
   no subnormal operand, and `1e-154` is a NORMAL double) and by the isolation
   run above, which compares the whole decoration
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
    --ab-relationships-from e74cf1d13d --ab-rounds 9 \
    --out docs/evidence/14192-read-wave/ab-relationships.json
# ⛔ `--ab-rounds 9` is REQUIRED to reproduce the committed artifact: the flag
# defaults to 7, and `ab-relationships.json` records `"rounds": 9`. Without it
# you get a different p50 and may read the evidence as drifted.
```

## What the first measurement found that the issue did not assume

- **The legs were already parallel.** `degradation_chain` submits fts / vector /
  structural to a `ThreadPoolExecutor` since #7780, and the measured leg wave is
  **6.2 ms of a 162.3 ms baseline read**
  (`verdict.member_delta.legs_wave`: `6.246 → 6.29`, in the artifact's member
  table). The issue's
  premise ("we run three of those four legs, but sequentially") is true of the
  *chain*, not of the legs: the sequentiality was **after** the legs — in the
  decoration reads.
- **`fusion` is not the problem either** (0.0 ms p50): `rrf_fusion` is pure CPU
  over a ≤120-row pool (`retrieval.DEFAULT_POOL_SIZE = 120`, a code constant).
- The remaining in-scope cost is a chain of **post-fusion graph reads**, which is
  what this change re-schedules and makes cheap.
