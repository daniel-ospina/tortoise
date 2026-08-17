---
title: "Extractor v2 — cleaning-pass experiment log (2026-08-14, for future continuation)"
type: log
domain: capability
subjects:
  team: epistemic-team
ownedBy: epistemic-team
doc_status: draft
created: 2026-08-14
governingAgreement: "#909, #1350"
---

# Extractor v2 — cleaning-pass experiment log

**Status: PAUSED — decision (owner, 2026-08-14): ship single-flash for now; this
structured-pipeline work is documented here for future continuation.**

## The question we were testing

Does a cheap pre-processing pass (solar-pro4) that cleans the raw conversation,
then a capable pass (deepseek-v4-flash) that narrates from the clean signal,
produce better memory extraction than flash reading the raw directly — at
acceptable cost?

## The architecture tested (v1-v7 cleaner prompts)

```
raw conversation
  → [solar-pro4] CLEANER: compress to durable narrative + entities + points
  → [deterministic gate] regex-strip mechanics tokens (#\d+, PR #, hashes, N/N tests, load, times)
  → [deepseek-v4-flash] S1: narrate from cleaned signal + durable_memo anchors
```

Early versions (v1-v5) used free-form "cleaned" prose. v6+ added a structured
`durable_memo` with named fields (root_cause_chain, chosen_fix_and_why,
residual_defects, independent_resolution, environment_beliefs) to force the
load-bearing facts to be extracted rather than narrated.

## Key findings (from the optimization loops + 3 parallel stability agents)

1. **Flash-direct (Path A) produced the cleanest narrative** — no mechanics
   leakage, correct IMPL/NAND/MITIGATES usage, captured the belief-shift +
   coordination discovery + environment belief. Cost ~$0.0005/session.
2. **The split (Path B) was cheaper** ($0.0004 on small windows; ~30% cheaper
   at 1M-token scale) BUT leaked more mechanics and had a failure mode (solar
   returning empty/partial cleaned text).
3. **The mechanics leak was the persistent bug**: solar ignored its own REMOVE
   list in 100% of runs — because the prompt's concrete example quoted this
   conversation's own tokens, anchoring the model on them. A deterministic
   regex gate fixed the leak but exposed the next layer.
4. **The durable_memo fixed memory-fidelity structurally** (root-cause chain +
   coordination discovery forced out, surviving 100% of runs vs 1/5 without)
   but solar filled the memo with PROCESS (CI status, PR numbers) instead of
   durable content — the granularity statements didn't stop it.
5. **Flash truncated** with memo anchors (restated only 2 of 5 sections).

## The cost/quality trade

| Path | Cost/session (80 EDU) | Mechanics leak | Memory fidelity |
|---|---|---|---|
| Flash direct | $0.0005 | low | good, but root-cause/coordination drift across runs |
| solar→flash (v5-v7) | $0.0004-0.0016 | high→fixed-by-gate | better (memo anchors) but process-laden + truncation |

## The decision

**Single-flash + granularity for now** (owner): the 2-model structured pipeline
adds machinery, cost, and a failure surface without yet beating flash-direct
on output quality. The granularity work (below) applies to the flash path too.

## What carries forward (the durable progress)

1. **Pack `memory_granularity` field** (committed): each pack manifest now
   declares what its domain considers durable vs ephemeral (product-strategy,
   dev, marketing, project-management). `compile_value_brief()` returns it.
   This replaces the vague "six months" heuristic with per-domain policy —
   applicable to ANY extractor path.
2. **The deterministic mechanics-gate + sanitizer** (in the experiment
   harnesses): regex strip of `#\d+`, `PR #`, hashes, `N/N tests`, `load`,
   elapsed times, `VGATE`, `worktree`, code identifiers. Reusable if the
   cleaning path is revisited.
3. **The `durable_memo` schema** (root_cause_chain, chosen_fix_and_why,
   residual_defects, independent_resolution, environment_beliefs): the
   structured-fact pattern that fixed memory fidelity. Worth adopting into the
   flash path as a "verify these facts are present" checklist.
4. **The cost model**: flash-direct ~$0.0005/session; the split inverts to
   cheaper at large scale (1M tokens: $0.042 vs $0.061). Relevant when volume
   grows.

## Experiment artifacts (not committed — in the worktree)

- `cleaner-v1.md` … `cleaner-v7.md` — the cleaner prompt evolution
- `run_clean_test.py` — the first A/B (solar vs direct)
- `run_ab.py` — the A/B used for the cost comparison
- `run_loop.py` — the optimization-loop harness
- `run_fix.py` — the sanitizer + durable_memo harness (v7)
- `run_s1_test.py` — the flash S1 prompt test

## How to resume

1. Adopt the `memory_granularity` into the flash S1 prompt (it's in
   `compile_value_brief()`).
2. If the cleaning path is revisited: start from `cleaner-v7.md` + the
   sanitizer in `run_fix.py`, and fix the two known issues — (a) solar fills
   the memo with process, (b) flash truncates multi-section memos.
3. Consider single-flash with a `durable_memo`-style "verify these facts"
   checklist appended to S1, which gets the memory-fidelity win without the
   second model.
