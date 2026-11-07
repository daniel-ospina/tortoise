---
title: "#1789 — the S4 re-emit tax, measured: −95.3 % output tokens on real geometry"
type: evidence
domain: extraction
doc_status: final
created: 2026-11-07
subjects.team: epistemic-team
aboutSubjects: S4 gap-review stage
aboutObjects: "#1789, PR #8041, #4917, #2408, plan docs/plans/2026-08-27-1789-s4-delta-contract.md"
---

# `#1789` — the S4 re-emit tax, measured

**Measured on:** 2026-11-07, `feat/1789-s4-reeemit-tax-measurement` (base
`origin/main` @ `23d227c53`), model **requested** `deepseek-v4-flash` via the
direct DeepSeek adapter, 7 real recorded sessions, **2 recorded runs, both
produced by the committed instrument**: `ab-20261107T020837Z` (primary) and
`ab-20261107T021100Z` (re-run, same code, same inputs). The filenames are each
run's START time; `measured_at` inside is its measured end. The model is not
deterministic even at `temperature=0.0`, which is why two runs are recorded and
the spread (`−95.3 %` / `−94.2 %`) is stated rather than hidden.

**What was already true:** the delta contract (PR #8041) shipped and is
default-ON (`_s4_delta_enabled()` = `env_flag("TORTOISE_S4_DELTA", True)`;
`S4_TMPL_DELTA` derived from the byte-identical legacy constants).

**What was missing — this document:** the issue's own last comment (2026-11-01)
called the ticket **PARTIAL** and restated the remainder as *"run the S4-token
measurement against a frozen baseline, then compare to ≥40 %."* The plan's
Task 10 (fresh 50-question E2E) was never run, so the delta contract's
**magnitude was unmeasured**.

---

## Verdict

| target (issue) | measured | verdict |
|---|---|---|
| **S4 output size per chunk drops ≥ 40 %** | **−95.3 %** token-weighted (31 775 → 1 497 output tokens over 7 sessions); median **−96.8 %**, worst session **−90.6 %**, best **−98.0 %** | **MET**, 2.4× the threshold; every one of the 7 sessions clears 40 % on its own |
| truncation rate | `truncated=False` on **14/14** arm calls, `attempts=1` — neither contract truncated on this geometry | unchanged (no regression) |
| accuracy ≥ 0.826 / chunk@20 ≥ 0.738 | **not measured here** — the eval lane's gates | see *What this does not measure* |

Re-run (`ab-20261107T021100Z`): 33 180 → 1 914 tokens, **−94.23 %**, median
−97.23 %, worst −84.71 %. The two runs differ by 1.1 points, so the point
estimate is soft and the DIRECTION is not: the worst session in either run is
−84.7 %, still above twice the ticket's threshold.

## The method — one variable

The tax is a property of the **output contract**, not of the pipeline, so it can
be isolated instead of inferred. For each session, `run_s4()` is called **twice
over identical inputs**:

| arm | template | what the contract asks for |
|---|---|---|
| `legacy` | `S4_TMPL` (`delta=False` ⇒ today's `TORTOISE_S4_DELTA=0`) | re-emit the COMPLETE embed list |
| `delta` | `S4_TMPL_DELTA` (`delta=True` ⇒ the shipped default) | gap ADDITIONS + full-item CORRECTIONS only |

Same story, same S2 embed list, same reconstructed S3 search block, same model,
same cap — `run_s4` threads `delta` and nothing else (pinned by
`test_both_arms_get_identical_inputs`). The number is the provider's own
`usage.completion_tokens`, read from `stats["cost"]["completion_tokens"]`, the
**per-call SUM** — so an escalated retry or a parse-retry re-prompt is counted
rather than overwritten. An arm whose provider reported no usage for any of its
calls is recorded as **unmeasured** (`null`), never as 0, and so is an arm whose
recovery accumulator cannot be shown to be complete.

**Inputs are real, not synthetic.** The sessions come from the recorded
write-path eval receipts (`tests/eval/write_path/receipts/7947-arms-2026-10-18/`):
real S1 stories and real S2 embed lists (29–49 items per session across
entities/events/points/operators; the recorded S2 output those sessions cost is
**3 263–5 453 tokens, median 4 187**, per `inputs[].s2_reference`).

**Nothing is lost by construction.** The delta arm's omissions are preserved by
the E4 union: `merged_items` is 270 vs an S2 base of 266 — the delta added 4
gaps and dropped none of the 266 S2 items (the legacy arm merged 275).

## What the delta arm stopped paying for

Summed over the 7 sessions (`composition` in the artifact, primary run):

| channel | legacy | delta | reading |
|---|---|---|---|
| `verbatim_reemissions` — items re-typed **unchanged** (the tax) | **200** | **0** | 200 items were pure typing cost; nothing about them was new |
| `corrected_by_s4` — S2 items S4 re-emitted at all | 265 | 0 | legacy re-touched every S2 item it saw; delta touched none |
| of which a field actually changed (`corrected_items`) | **64** | **0** | see the residual — this is the channel the design exists for |
| `added_by_s4` — the gap channel (`added_items`) | 9 | 4 | see the residual |
| `s4_items` emitted | 274 | 4 | 270 fewer items per 7 sessions |

(Re-run: legacy 201 verbatim / 266 corrected / 16 added / 64 corrected items;
delta 0 / 0 / 5.) The census count (265) and the recorded `corrected_items` (64)
sit one item apart from `265 − 200 = 65`: the census helper compares whole
parsed dicts, while the item snapshot compares the listed correction-relevant
fields. Neither number is a claim about value — see the residual.

## ⚠️ The residual this measurement exposes (do not read the −95 % as a free win)

**BOTH value channels thinned, and the corrections channel went to zero.**

- **Corrections.** The legacy arm re-emitted 265 S2 items, of which 200 were pure
  re-emits, so 65 carried a change by the census mask (`_verbatim_reemissions`
  ignores `source_ref`/`confidence`/`quote`/`search_keys`/`source_turn_id`, so
  those are real corrections, not noise fields); the item-level snapshot records
  **64** of them with a changed field, keeping both sides. The delta arm emitted
  **0 colliding items in 14 calls**: the corrections channel was never used once.
  The plan's own research is why that matters — the corrections/retraction
  channel is the stated reason NOT to ship a pure ADD-only contract, and accuracy
  is exactly what this document does not measure.
- **Gaps.** legacy `added_by_s4` 9 (primary) / 16 (re-run) vs delta 4 / 5, and the
  delta arm emitted **no new tax-surface item** in 5 of 7 sessions in both runs.
  The narrower statement matters: `s4_delta_empty` — production's predicate over
  EVERY emitted section, not just the four tax sections — is **False on all 7
  delta rows in both runs**, because those arms still carried a non-tax section
  (graph-search bookkeeping). "Emitted no item" and "emitted nothing" are
  different claims, and only the first is true here.

Two readings fit both channels and this instrument cannot separate them:

1. the legacy arm's extra items and its 64 corrections are artefacts of it
   re-typing 266 items — a paraphrase lands as a new identity, a re-statement as
   a "correction" — i.e. they are the tax's own noise; or
2. the delta contract suppressed genuine gap-finding and correction, i.e. up to
   16 gaps (9 in the primary run, 16 in the re-run) and 64 corrections per 7
   sessions are now missed.

Deciding between them is **Task 10 Step 5** of the plan (partition each
correction into content-changing / kind-lifecycle-only / re-emission-noise).
That partition needs the ITEMS, which is why the artifact carries them: every
row records `added_items` (the gaps — section, merge key, content, kind,
lifecycle, `supersedes`) **and `corrected_items`** — the correction PAIRS, each
with `changed_fields`, `s2_before` and `s4_after`, because a correction reuses
the S2 merge key and any "items not in S2" filter would drop the entire channel
the partition is about. The follow-up can therefore be done **from this
artifact**, without a second paid run.

## What this does **not** measure

- **Accuracy / `chunk_evidence@20`** — the eval lane's gates (#1789 target 3).
  A cheaper S4 that corrects less could hold accuracy or could not; only the eval
  can answer that, and the corrections channel above is the reason to ask.
- **Model compliance in the wild** — one call per arm per session, not a
  production run. Non-compliance is *observable* per row
  (`verbatim_reemissions`, `corrected_by_s4`, `s4_delta_reemit_suspect`), so a
  model that ignored the contract would show up in the artifact rather than being
  assumed away.
- **Which model actually served the calls** — the artifact records the model id
  REQUESTED. (The adapter logs a substitution warning when the provider pins a
  dated id; that warning is logged, not persisted here.)
- **The S3 live-graph read** — the search block is reconstructed from the
  session's own S2 list (what production S4 sees after S2 is written), not a live
  graph query. Both arms see the same block, so the comparison holds.

## Reproduce

```bash
# zero-cost, no network — the input-side geometry + the vacuity pin
uv run python tools/s4_reeemit_tax.py dry-run

# the measurement (needs DEEPSEEK_API_KEY; ~4 min and a few cents for 7 sessions)
DEEPSEEK_API_KEY=… uv run python tools/s4_reeemit_tax.py run \
    --out docs/evidence/1789-s4-reeemit-tax/ab-$(date -u +%Y%m%dT%H%M%SZ).json
```

Artifacts: `ab-20261107T020837Z.json` (primary — per-arm rows, the added and
corrected items, composition totals, and the per-arm `s4_delta_empty` /
`s4_delta_reemit_suspect` flags derived with production's own predicates) and
`ab-20261107T021100Z.json` (the re-run).

Instrument tests: `tests/test_s4_reeemit_tax.py` (23 tests, offline, fake
model) — they pin the ways a number like this goes wrong silently: a vacuous A/B
(both arms rendering the same prompt), the two arms receiving different inputs, a
mis-attributed cost (a per-call total rather than the final call, and `null`
rather than 0 when the provider reports nothing or the accumulator is provably
incomplete), a "measured zero" pretending to be a measurement, a corrections
channel that an items-not-in-S2 filter silently drops, and `s4_delta_empty`
diverging from production's predicate.
