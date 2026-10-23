---
title: "#2976 — TR admission measurement: the 0/52 baseline does not reproduce, and spending the budget buys +25 pts"
type: operations
domain: operations
doc_status: live
created: 2026-10-23
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise, tortoise-longmemeval
related:
  - "#2976 (the oracle ceiling + the 0/52 temporal result)"
  - "#2578 (the measurement that surfaced it)"
  - "#2978 (content-less operator nodes consuming context slots)"
  - "#2165 (deterministic temporal assembler, lane 2)"
---

## #2976 — TR admission measurement

> Three admission arms over ONE ingest of the pinned 55-question temporal subset
> (52 answerable), same reader prompt, same rendering, one grader.
> Instrument: `tools/longmem_eval/temporal_admission_arms.py`.
> Per-question rows: `docs/runbook/2976-admission-arms.jsonl`.
> Every number below is reproducible from the commands at the end.

## 1. Headline

| arm | correct / 52 | refusals | ctx tokens (median) | items | sessions | gold admitted |
|---|---|---|---|---|---|---|
| **issue baseline** (committed `#2578` A-default, PRE-`#2978` code, re-graded) | **9 / 52 (17.3%)** | 52 / 52 | **97** | 12 | — | 0 / 55 (committed facts) |
| **today's baseline** (main, same knobs) | **31 / 52 (59.6%)** | 7 / 52 | 2 171 | 12 | 4 | 51 / 52 |
| **`admission`** (this change) | **44 / 52 (84.6%)** | **1 / 52** | 7 749 | 35.5 | 7 | 52 / 52 |
| `admission+completion` | 39 / 52 (75.0%) | 2 / 52 | 7 490 | 35.5 | 6 | 52 / 52 |

Per class (correct / n):

| class | n | issue baseline | today's baseline | `admission` | `admission+completion` |
|---|---|---|---|---|---|
| interval | 19 | 0 / 19 | 11 / 19 | **18 / 19** | 16 / 19 |
| ordering/compare | 32 | 9 / 32 | 20 / 32 | **25 / 32** | 22 / 32 |
| current-state | 1 | 0 / 1 | 0 / 1 | **1 / 1** | 1 / 1 |

**Admission is the fix. Session completion is not** — it regresses 5 questions
(both classes) by diluting a window that was already spending its budget, and
is left OFF.

## 2. The issue's premise is HALF stale — and that is the finding

Issue #2976 quotes two defects. Re-measured on today's `main`:

1. **"Gold at rank 41–120 never enters a 12-item window" — no longer reproduces.**
   On the pinned subset the gold is admitted on **51 / 52** questions under the
   shipped 12-item window. The committed 0/52 measurement predates **#2978**
   (merged two days after it), which stopped content-less operator nodes
   consuming reader-context slots. Before that fix ~61% of the 12 slots were
   decorations, so the window held almost nothing; today the same 12 slots hold
   **2 171 tokens of real evidence** instead of 97.
2. **"~8 tokens/item" — also a #2978 artifact.** Measured today: ~180 tokens per
   admitted item (2 171 / 12). The granularity defect the issue describes is the
   *empty-slot* defect, already fixed.

So the residual defect is not admission-vs-rank. It is **volume**: the TR window
is capped at 12 items, which spends **27% of the 8 000-token budget** the lane
configures, and the reader needs more evidence than that — the 7 questions still
refused at baseline (3 interval, 4 ordering/compare) and the 21 wrong answers
are evidence *starvation*, not evidence *absence*.

That is why the oracle comparison is misleading in both directions: the oracle
(42/52) is not an upper bound for retrieval — this change reaches **44/52** —
because a broad dated window across ~7 sessions can beat the annotated gold
sessions, which are themselves a subset of what the reader can use.

## 3. The mechanism — breadth first, then depth (session-granular)

`tortoise.retrieval.assemble_session_context` (new, product, pure):

1. **Breadth** — walk the pool in RANK order, admitting one hit at a time until
   `breadth_share` (default 0.5) of the token budget is spent. The item cap is
   **not** passed: the token budget is the volume bound (the field's pattern —
   Hindsight trims a token budget, not a fixed K;
   `docs/research/2026-09-11-decisions-2952-2976-competitor-evidence.md`).
2. **Depth** — with the remaining budget, complete the sessions the breadth pass
   TOUCHED, in first-appearance order, each as one contiguous block (whole or
   none). This is the half that makes an admitted session mean *the whole
   session* rather than a fragment.

**Why not "whole sessions first"?** Measured, and rejected: admitting blocks
before breadth concentrates the budget on the top two sessions and *displaces*
gold the plain rank walk had already reached (qid `gpt4_59149c77`: 2 → 1 gold
hits, 17 sessions dropped by the token cap). Breadth-then-depth keeps both
properties.

**Why not raise `tr_top_k`?** #2976's own trace measured 16/20/24 as a plateau
below the rank-40 band. The mechanism needed is *more budget spent*, not a
bigger item count — and with ~180 tok/item, an item cap near the budget ceiling
would silently re-bind at a different number.

**Why not the reranker?** `applied-rerank` is the only committed arm that moves
CONVERSION (6/52) — its gold-admitted count (21/55) is shared with `cap3-only`
(21/55), so admission alone does not distinguish them — and it adds a
cross-encoder dependency plus 6.6× context for those 6. The change here needs no
model.

## 4. Granularity: measured, and rejected

`admission+completion` adds the session-completion pass
(`session_reinjection.source_session_chunk_pass`, seeded on the ranked session
head) so each head session arrives whole. On the real lane it injects little
(the retrieved pool already carries each session's material) and it *regresses*
5 questions: 25 → 22 ordering, 18 → 16 interval.

⛔ Reading the 5 regressions: at least one inspected case is a **grader
artifact**, not a quality loss — gold `"1 week"` vs the answer `"One week
(7 days)"` fails variant-aware containment on the digit. One other inspected
case is a genuine refusal under the diluted window. The arm is rejected either
way, but the -5 is not all signal.

## 5. Cost

| arm | context tokens (median) | reader seconds (mean) | ingest |
|---|---|---|---|
| today's baseline | 2 171 | 6.38 | one pass, shared |
| `admission` | 7 749 | 2.98 | one pass, shared |
| `admission+completion` | 7 490 | 2.91 | one pass, shared |

The window grows 3.6× but stays inside the configured 8 000-token budget. The
reader is not slower (the baseline's higher mean is first-call/API variance, not
a systematic difference — the arms share one process and one route).

## 6. Method, deviations, and what this does NOT establish

- **Subset:** `measure_temporal.deterministic_subset` of the committed census =
  55 questions (ordering/compare 34, interval 19, current-state 2), 52
  answerable + 3 abstention controls excluded from every denominator.
- **Reader route — DEVIATES.** The pinned `openrouter:deepseek/deepseek-v4-flash`
  is blocked (monthly key limit, 403). This run uses the same model over its
  direct route (`deepseek:deepseek-v4-flash`, `DEEPSEEK_API_KEY`), which the
  provider served as **`deepseek-flash`** (the harness recorded the substitution
  explicitly). Same weights, different transport; NOT pinned
  (`reader_pinned=false`).
- **Grader — DEVIATES.** The official `gpt-4o-2024-08-06` judge is blocked by the
  same key limit. Every arm is graded with the deterministic **variant-aware
  containment** judge (`judge.grade_variant_aware`), which reproduces the
  committed oracle figure exactly (42/52 variant-aware vs 25/52 plain — ceiling
  re-derived from `docs/runbook/2578-oracle-ceiling.jsonl`). It is
  digit-sensitive, so treat the absolute numbers as a band (~±2 questions); the
  before/after comparison is apples-to-apples.
- **Provenance is IN the artifact.** Every row of
  `2976-admission-arms.jsonl` names `reader_model`, `reader_model_spec`,
  `reader_pinned` (`false`) and `judge_model`/`judge_model_spec`, and the
  summary repeats them under `provenance`, so a row can never be mistaken for
  one measured under the official judge or the pinned OpenRouter route. The
  served-id substitution (`requested 'deepseek-v4-flash'`, provider served
  `'deepseek-flash'`) is a harness-level warning rather than a row field — it is
  recorded here in the method section and in the PR body.
- **Issue baseline re-grade:** the committed `A-default` hypotheses are re-graded
  with the same grader (9/52) rather than quoting its official-judge 0/52, so all
  three rows are the same instrument.
- **Arms are pinned against the ambient env.** Every arm names BOTH #2976
  switches explicitly (`tr_session_admission` / `tr_session_completion`), so an
  operator shell carrying `TORTOISE_LME_TR_SESSION_COMPLETION=1` cannot make
  `admission` byte-identical to `admission+completion` and publish the
  completion number under the `admission` label. The arm that SPENDS the
  completion pass also pins its four budgets
  (`_TOTAL` 60 / `_WINDOW` 40 / `_SESSIONS` 5 / `_PER_SESSION` 12 — the product
  defaults, so nothing measured changes), because they are env seams too and
  the run fingerprint keys on them: a re-run inherits the arm table, not the
  shell.
- **Committed rows are the measurement columns.** The per-row arm censuses
  (`admission_stats` / `completion_stats`) and `gold_in_pool` were produced by a
  pre-review revision of the driver (a review finding: the completion pass was
  rebinding `pool`, so `gold_in_pool` was measured against the *injected* pool
  for that arm). They were DROPPED from the committed rows rather than published
  unreproducible; the measurement columns — verdict, refusal, context tokens /
  items / sessions, gold hits admitted, wall time — are unaffected by the fix
  and are what the tables above use.
- **What is NOT established:** that the *product* ask lane (40 items / 16 000
  tokens by default) has the same defect — it does not share the eval lane's
  `tr_top_k=12` guard rail. And that 84.6% is the ceiling: the oracle arm is a
  different (gold-only) window shape.

## 7. Adoption

The mechanism is available and OFF by default, and is an **eval-lane knob**
(`TORTOISE_LME_TR_SESSION_ADMISSION`, `retrieve_for_question(tr_session_admission=)`).
Flipping the TR default changes the pinned config every prior temporal
measurement was taken under, so it is recorded here as the measured case for
adoption rather than taken unilaterally:

- the guard rail `tr_top_k=12` exists to prevent **~40 000-token floods**
  (R5 #1544); the measured window at 7 749 tokens does not meet that failure
  mode, and the token budget bounds it by construction;
- the 12-item cap spends 27% of the configured budget for no measured benefit
  on this class.

Recommended next step (not in this change): adopt token-budgeted session
admission as the TR default in the eval lane and re-run the #2578 arm matrix
under it.

## 8. Reproduction

```bash
# 1) the ceiling, offline (no reader, no judge key). The 42/52 leg needs the
#    277 MB LongMemEval split in the local cache; it SKIPS where the split is
#    absent (CI), and the other five tests in that file are hermetic.
uv run python -m tools.longmem_eval.temporal_admission_arms --help   # instrument
uv run pytest tests/longmem_eval/test_variant_containment_judge.py -q

# 2) the arms over one ingest (needs a FalkorDB lane with >= 3 GB: 55 resident
#    per-question graphs OOM-killed a 2 GB container at q40, and the driver now
#    releases each question's graph after its arms run).
#    ⛔ Write to a FRESH path, not the committed ones: the driver appends, and a
#    second 165 rows would change every absolute number while the ratios survive
#    (so the artifact would still look valid). The driver now REFUSES to append
#    without --resume; the committed files below are the originals.
uv run python -m tools.longmem_eval.temporal_admission_arms \
    --split s --data /tmp/lme55.json \
    --db 'docker://:pw@localhost:6379/db' \
    --reader-model deepseek:deepseek-v4-flash \
    --judge-model variant-containment \
    --out /tmp/2976-admission-arms.jsonl \
    --sum-out /tmp/2976-admission-arms-summary.json

# the committed artifacts this runbook's tables come from:
#   docs/runbook/2976-admission-arms.jsonl           (165 rows = 55 q × 3 arms)
#   docs/runbook/2976-admission-arms-summary.json
```
