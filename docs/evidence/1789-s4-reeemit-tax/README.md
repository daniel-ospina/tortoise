---
title: "#1789 — the S4 re-emit tax, measured: −93.8 % output tokens on real geometry"
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
`origin/main` @ `23d227c53`), model `deepseek-v4-flash` (direct DeepSeek; the
provider served `deepseek-flash` — a dated pin, per its own substitution
notice), 7 real recorded sessions, **2 recorded repeats, both produced by the
committed instrument** (`ab-20261107T012628Z` = primary, `ab-20261107T013359Z`
= repeat). The headline is the PRIMARY run; the repeat is there so the spread
is visible (`−93.8 %` / `−95.7 %`), and the model is not deterministic even at
`temperature=0.0`.

**What was already true:** the delta contract (PR #8041) shipped and is
default-ON (`_s4_delta_enabled()` = `env_flag("TORTOISE_S4_DELTA", True)`;
`S4_TMPL_DELTA` derived from the byte-identical legacy constants).

**What was missing — this document:** the issue's own last comment
(2026-11-01) called the ticket **PARTIAL** and restated the remainder as
*"run the S4-token measurement against a frozen baseline, then compare to
≥40 %."* The plan's Task 10 (fresh 50-question E2E) was never run, so the
delta contract's **magnitude was unmeasured**.

---

## Verdict

| target (issue) | measured | verdict |
|---|---|---|
| **S4 output size per chunk drops ≥ 40 %** | **−93.8 %** token-weighted (32 351 → 2 006 output tokens over 7 sessions); median **−97.4 %**, worst session **−84.5 %**, best **−98.2 %** | **MET**, 2.3× the threshold; every one of 7 sessions clears 40 % on its own |
| truncation rate | `truncated=False` on **14/14** arm calls — neither contract truncated on this geometry | unchanged (no regression) |
| accuracy ≥ 0.826 / chunk@20 ≥ 0.738 | **not measured here** — the eval lane's gates | see *What this does not measure* |

Repeat (`ab-20261107T013359Z`): 32 539 → 1 413 tokens, **−95.66 %**, median
−96.81 %, min −86.98 %. The two runs disagree by 1.9 points, so the point
estimate is soft and the DIRECTION is not: the worst session in either run is
−84.5 %, still above twice the ticket's threshold.

## The method — one variable

The tax is a property of the **output contract**, not of the pipeline, so it
can be isolated instead of inferred. For each session, `run_s4()` is called
**twice over identical inputs**:

| arm | template | what the contract asks for |
|---|---|---|
| `legacy` | `S4_TMPL` (`delta=False` ⇒ today's `TORTOISE_S4_DELTA=0`) | re-emit the COMPLETE embed list |
| `delta` | `S4_TMPL_DELTA` (`delta=True` ⇒ the shipped default) | gap ADDITIONS + full-item CORRECTIONS only |

Same story, same S2 embed list, same reconstructed S3 search block, same
model, same cap. **The only variable is the contract.** The number reported is
the provider's own `usage.completion_tokens` — not a tokenizer estimate, not an
item-count proxy.

**Inputs are real, not synthetic.** The sessions come from the recorded
write-path eval receipts (`tests/eval/write_path/receipts/7947-arms-2026-10-18/`):
real S1 stories and real S2 embed lists (29–49 items per session across
entities/events/points/operators; the recorded S2 output those sessions cost is
**3 263–5 453 tokens, median 4 187**, per `inputs[].s2_reference`).

**Nothing is lost by construction.** The delta arm's omissions are preserved
by the E4 union: `merged_items` is 273 vs an S2 base of 266 — the delta added
7 gaps and dropped nothing (the legacy arm merged 276).

## What the delta arm stopped paying for

Summed over the 7 sessions (`composition` in the artifact):

| channel | legacy | delta | reading |
|---|---|---|---|
| `verbatim_reemissions` — items re-typed **unchanged** (the tax) | **201** | **0** | the 201 items are pure typing cost; nothing about them was new |
| `corrected_by_s4` — S2 items S4 re-emitted at all | 266 | 0 | legacy re-touched every single S2 item; delta touched none |
| `added_by_s4` — the gap channel (the value the contract exists to keep) | 10 | 7 | **the residual risk — see below** |
| `s4_items` emitted | 276 | 7 | 269 fewer items per 7 sessions |

(The repeat gives the same shape: legacy `verbatim_reemissions` 201, `added_by_s4`
11; delta 0 and 4.)

## ⚠️ The residual this measurement exposes (do not read the −94 % as a free win)

**The gap channel is thinner under delta: 10 additions (legacy) vs 7 (delta)
in the primary run, 11 vs 4 in the repeat** — across the same 7 sessions. Two
readings are consistent with the data and this instrument cannot separate them:

1. most of legacy's 10 "additions" are re-worded near-duplicates of S2 items
   (legacy is re-typing 266 items, so a paraphrase lands as a new identity),
   i.e. the 3 missing additions are noise; or
2. the delta contract suppressed genuine gap-finding (the delta arm emitted
   *nothing at all* in 5 of 7 sessions in both runs), i.e. up to 3 real gaps per
   7 sessions are now missed.

The two runs disagree on the counts too (delta 7 then 4, legacy 10 then 11),
so the channel is **noisy at n = 7** and a single run
cannot carry a fidelity verdict either way. Deciding between (1) and (2) needs the corrections-fidelity sampling the plan
assigns to Task 10 Step 5 (partition corrections into content-changing vs
kind/lifecycle-only vs re-emission-noise) — that is a follow-up, and it is the
one thing this document does not settle. It is recorded here rather than
smoothed over, because the honest form of "−94 % cost" is "−94 % cost, with the gap channel
4–7 vs 10–11 and unexplained at this sample size".

## What this does **not** measure

- **Accuracy / `chunk_evidence@20`** — the eval lane's gates (#1789's targets 3).
  A cheaper S4 that finds fewer gaps could still hold accuracy or could not;
  only the eval can answer that.
- **Model compliance in the wild** — one call per arm per session, not a
  production run. Non-compliance is *observable* per row
  (`verbatim_reemissions`, `s4_delta_reemit_suspect`), so a model that ignored
  the contract would show up in the artifact rather than be assumed away.
- **The S3 live-graph read** — the search block is reconstructed from the
  session's own S2 list (what production S4 sees after S2 is written), not a
  live graph query. Both arms see the same block, so the comparison holds.

## Reproduce

```bash
# zero-cost, no network — the input-side geometry + the vacuity pin
uv run python tools/s4_reeemit_tax.py dry-run

# the measurement (needs DEEPSEEK_API_KEY; ~2.5 min and a few cents for 7 sessions)
DEEPSEEK_API_KEY=… uv run python tools/s4_reeemit_tax.py run \
    --out docs/evidence/1789-s4-reeemit-tax/ab-$(date -u +%Y%m%dT%H%M%SZ).json
```

Artifacts: `ab-20261107T012628Z.json` (primary — per-arm rows, composition
totals, and the per-arm `s4_delta_empty` / `s4_delta_reemit_suspect` flags
derived from each arm's own composition) and `ab-20261107T013359Z.json` (the
repeat). The token totals are the provider's own `usage.completion_tokens`,
read from `stats["cost"]["completion_tokens"]` — the per-call SUM, so an
escalated retry or a parse-retry re-prompt is counted rather than overwritten
(`basis` records which accumulator was used on every row). An arm that reports
NO usage is recorded as unmeasured (`null`), never as 0 — a 0 delta would read
as a −100 % saving.
Instrument tests: `tests/test_s4_reeemit_tax.py` (17 tests, offline, fake model)
— they pin the three ways a number like this goes wrong silently: a vacuous A/B
(both arms rendering the same prompt), a mis-attributed escalation cost, and a
"measured zero" pretending to be a measurement.
