---
title: "2976 — Temporal Retrieval Admission Trace (where the temporal evidence is lost)"
type: research
domain: retrieval
doc_status: live
created: 2026-09-11
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
issue: 2976
---

# 2976 — Temporal Retrieval Admission Trace

> Diagnostic trace for issue #2976: the shipped retrieval config answers
> **0 of 52** answerable TEMPORAL questions, while an oracle reader handed
> the gold sessions verbatim answers **42/52 (81%)** and refuses only 7.7%.
> Under real retrieval the reader refuses **98.2%** of the time.
>
> Derived **entirely from the committed #2578 measurement** (commit
> `1b59244e8`; artifacts under `docs/runbook/2578-*`, runbook
> `docs/runbook/2578-temporal-measurement.md`) plus the committed census
> (`tests/_assembly_census.json`). No eval was re-run. Every number below is
> pinned by `tests/longmem_eval/test_temporal_admission_trace.py`.

## Verdict

**The temporal evidence is lost at the READER-WINDOW CUT, not at pool
admission.** The gold is in the retrieval pool for every question; it is
never selected into the reader's window because the window is the semantic
RRF *head* (`tr_top_k` = 12), and gold sits at pool ranks 41–120.

| hypothesis | measured verdict |
| --- | --- |
| Admission (gold never enters the pool) | **REJECTED** — `marked_points_in_pool == marked_points_total` on 55/55 |
| Pool depth (gold beyond the fetch horizon) | **REJECTED** — all 113 marks are inside the fetched pool (#1947's 60→120 deepening worked) |
| Ranking / window cut (gold in the pool, below the cut) | **CONFIRMED** — 113/113 marks in the 41–120 band; 0 in `top-20`, 0 in `21-40` |
| Context rendering (dates not surfaced) | **REJECTED** — `_render_block` emits `[session N] (session date YYYY-MM-DD)`; `lme_session_index` is written at ingest (`tools/longmem_eval/ingest.py`) |
| TR window filter drops the answer session | **REJECTED — the filter never fires at all** (see §3) |

## 1. Pool membership is complete; admission is zero

Baseline arm (`A-default`, 55 questions, `tr_top_k=12`, rerank OFF,
evidence-boost OFF):

| metric | value |
| --- | --- |
| questions with gold marks in the pool | **55 / 55** |
| total marked (`has_answer`) points | 113 |
| `marked_points_bands` | `top-20: 0` · `21-40: 0` · **`41-120: 113`** · `121+: 0` |
| `gold_admitted` (post-`_assemble_context` reader context) | **0 / 55** |
| reader refusals | **54 / 55** (0.982) |
| correct | 3 / 55 — all three are `_abs` abstention-design controls |

The **52 answerable** questions are answered **0 / 52**. The 98.2% refusal
rate is therefore a *symptom*, not an independent reader defect: with an
empty evidence window, abstaining is the correct reader behaviour. This is
what makes the refusal rate a valid judge-independent signal for the
*admission* failure — and it is also why it says nothing about reader
capability (the oracle's 7.7% settles that separately).

## 2. The reader window is the semantic head, and gold is at rank 41+

`tools/longmem_eval/retrieve.py::retrieve_for_question`:

* `effective_top_k = tr_top_k if is_tr else top_k` → **12** for TR;
* `eff_item_cap = tr_top_k` → **12** item cap for TR (vs 40 for non-TR);
* the context handed to the reader is
  `assemble_context(pool, top_k=12, context_item_cap=12, max_context_tokens=8000)`
  — i.e. **`pool[:12]` subject to the token budget** — and *then* the 12
  survivors are re-sorted time-ascending. The date sort reorders **within**
  the window; it cannot pull an item **into** it.

So the window is chosen entirely by semantic RRF rank. Gold at rank ≥ 41 is
unreachable. Reach ceilings, measured:

| lever | pool ranks it can reach | gold admitted | answerable-correct |
| --- | --- | --- | --- |
| `A-default` (window 12) | ≤ 12 | 0 | 0 / 52 |
| `tr_top_k16` | ≤ 16 | 0 | 0 / 52 |
| `tr_top_k20` | ≤ 20 | 0 | 0 / 52 |
| `tr_top_k24` | ≤ 24 | 1 | 0 / 52 (1 question) |
| `c2-on` (evidence boost) | position-ceiling promotion | 0 | 0 / 52 |
| `pool-only-isolation` | — (see §4) | 0 | 0 / 52 |
| `cap3-only` (rerank cap 3) | full pool **reorder** | **21** | 5 / 52 |
| `applied-rerank` (pool 120 + cap 3) | full pool **reorder** | **21** | **6 / 52** |

The band starts at rank 40; widening the window to 24 cannot reach it, which
is exactly the measured `tr_top_k` plateau. **Only a reorder of the full
pool** (the cross-encoder reranker) promotes gold into the window — and it
lifts refusal from 0.982 to 0.764, admission-attributed failures from 52 to
30, and answerable-correct from 0 to 6.

## 3. The TR temporal machinery is inert on this corpus

The TR path has exactly one mechanism that can reorder/narrow the pool before
the cut: `detect_time_constraint` → `_apply_time_window` (hard window on
`session_date`, with `tr_window_fallback` for "never starve the reader").

On the pinned 55-Q subset (`detect_time_constraint` run over the committed
census):

| detected kind | n | pool effect |
| --- | --- | --- |
| `None` | **34** | no filter, no reorder |
| `ordering` | **21** | no filter, no reorder |
| `interval` | **0** | — |
| `recency` | **0** | — |

`_apply_time_window` only acts on `interval`/`recency`. It therefore **never
runs** on this corpus, and `tr_window_fallback` is never even reached. TR
questions get no temporal handling beyond a flat engine `recency_boost=0.5`
and the post-cut date re-sort — i.e. the reader window is pure semantic RRF.

This is a detection *boundary*, not a detector bug: the dominant LongMemEval
TR shapes ("how many days passed between the day I… and the day I…",
"which event happened first, X or Y?") reference **events**, not dates, so
there is no computable date window to filter on. They are comparison /
ordering questions that need **both compared instances' dated turns
co-present**, which the eval lane has no mechanism to guarantee.

The commit's own docstring states the intent — for `ordering`, "the question
needs the full dated set to compute a span/ordering" — but the code's
implementation of that intent is *no filter, no reorder*, which reduces to
"whatever RRF put in the top 12".

## 4. Measurement defects found in the #2578 receipts

Two receipt-level findings that qualify the runbook's conclusions (both
pinned by tests, both filed as a follow-up):

1. **`pool-only-isolation` is vacuous.** Since #1947 the baseline already
   fetches `DEFAULT_POOL_SIZE` = 120, so `--rerank-pool 120` with rerank OFF
   is **byte-identical** to `A-default` (`pool_depth.requested = 120` in
   both; identical `gold_admitted` vector). The runbook's "widening the pool
   WITHOUT reranking changes nothing" carries **no evidence** — the pool was
   never widened. The depth lever is *untested*, not null.
2. **`--rerank` alone silently collapses the fetch depth 120 → 40.**
   `rerank_pool` defaults to 40 and the rerank branch *overrides*
   `pool_limit` (`pool_limit = max(rerank_pool, max(ks))`) instead of taking
   `max(rerank_pool, baseline_depth)`. `cap3-only` therefore measured a
   40-deep fetch (`pool_depth.requested = 40`, pool 75–80) while every other
   arm fetched 120. Turning the reranker on discards the #1947 deepening
   unless the caller also passes `--rerank-pool 120`.

## 5. Why no product fix was shipped in this PR

The trace shows the fix is **product-directional**, not a surgical rank tweak:

* **Window widening is measured null** — 16/20/24 all sit below the rank-40
  band, and the window is already ≈ the 8000-token budget ceiling (~19 items
  at median chunk). Widening past 24 would breach the R5 flood-control
  rationale (9/18 TR losses were refusals under ~40k-token floods) for no
  measured gain.
* **Enabling the reranker by default for TR** is the one measured lever
  (0 → 21 admitted, 0 → 6 correct) but it adds a cross-encoder model
  dependency and 6.6× context growth (94.9 → 624.0 tokens), and it still
  reaches only 6/52 — that is a product cost/latency decision, not a bug fix.
* **Closing the remaining admission gap** requires co-assembling the two
  compared instances' dated turns per entity — i.e. the deterministic
  temporal assembler scoped as **#2165 lane 2** ("Both halves of a
  comparison must be in the context by construction", synthesis constraint
  6). That is a different lane's deliverable, and the #2578 runbook already
  pre-registered conversion-on-admitted-gold as reader-bound.

Shipping a `tr_top_k` bump or a rerank-default flip here would be the "easy
over good" change the repo's design principle rejects: neither is supported
by the measured reach ceilings, and both risk the R5 flood regression that
`tr_top_k=12` exists to prevent.

## 6. Regression guard

`tests/longmem_eval/test_temporal_admission_trace.py` (hermetic; committed
artifacts + pure functions only) pins:

* the pool-membership / window-cut split and the 41–120 band;
* the 54/55 refusal + 3/55 `_abs`-only correct baseline;
* the reach-ceiling table (`tr_top_k` ≤ 24 vs the reorder's 21);
* the two measurement defects of §4;
* the mechanism: a `has_answer` hit at pool rank 45 is present in the pool
  and absent from a 12-item window, and widening to 20/24 still misses it
  while a reorder admits it;
* the inert TR machinery: 0/55 `interval`/`recency`, and `ordering` is a
  pool no-op.

Run:

```
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/longmem_eval/test_temporal_admission_trace.py -v
```
