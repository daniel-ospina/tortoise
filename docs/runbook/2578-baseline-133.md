---
title: "2578 Temporal Measurement — 133-Q Whole-Class Baseline"
type: operations
domain: operations
doc_status: live
created: 2026-09-10
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
issue: 2578
---

# 2578 — 133-Q Whole-Class Baseline

> The census-enumerated full temporal class (133 questions), default knobs
> (no widening), real pinned reader, facts gate ON. The 55-Q deterministic
> subset above drives the widening matrix; THIS table is the whole-class
> denominator — the subset is selected for deterministic fireability, so
> several of its per-class cells are n≤2 and carry no interval worth
> quoting.

## Accuracy by census class (95% Wilson CI)

| class | n | correct (95% CI) | correct on answerable (n, 95% CI) | admission-attributed | conv-refusal | conv-wrong |
| --- | --- | --- | --- | --- | --- | --- |
| **ALL 133** | 133 | 8 (0.031–0.114) | 2/127 (0.004–0.056) | 125 | 0 | 0 |
| ordering/compare | 34 | 2 (0.016–0.191) | 0/32 (0.000–0.107) | 32 | 0 | 0 |
| ago-relative | 31 | 1 (0.006–0.162) | 1/31 (0.006–0.162) | 30 | 0 | 0 |
| interval | 19 | 0 (0.000–0.168) | 0/19 (0.000–0.168) | 19 | 0 | 0 |
| frequency/count | 12 | 1 (0.015–0.354) | 0/11 (0.000–0.259) | 11 | 0 | 0 |
| recency/current-state | 8 | 0 (0.000–0.324) | 0/8 (0.000–0.324) | 8 | 0 | 0 |
| duration-state | 7 | 0 (0.000–0.354) | 0/7 (0.000–0.354) | 7 | 0 | 0 |
| other | 6 | 1 (0.030–0.564) | 0/5 (0.000–0.434) | 5 | 0 | 0 |
| relative-date-lookup | 6 | 1 (0.030–0.564) | 1/6 (0.030–0.564) | 5 | 0 | 0 |
| nary-ordering | 4 | 1 (0.046–0.699) | 0/3 (0.000–0.562) | 3 | 0 | 0 |
| current-state | 2 | 1 (0.095–0.905) | 0/1 (0.000–0.793) | 1 | 0 | 0 |
| recency | 2 | 0 (0.000–0.658) | 0/2 (0.000–0.658) | 2 | 0 | 0 |
| offset-comparison | 1 | 0 (0.000–0.793) | 0/1 (0.000–0.793) | 1 | 0 | 0 |
| pattern/recurring | 1 | 0 (0.000–0.793) | 0/1 (0.000–0.793) | 1 | 0 | 0 |

**Overall:** 8/133 scored correct
(0.060) — but see the answerable column:
only **2 of 127 questions
that HAVE an answer** were answered correctly. Every other "correct" row
is an abstention-design question (refusing is the correct behaviour), so
the raw `correct` column is not a capability measure.

Attribution: admission-attributed 125,
conversion-refusal 0, conversion-wrong
0. **Conversion is not observable in this
baseline** — gold was admitted on 0 of 133 questions, so the
conversion channel is empty by construction: 0/0 is "not measurable",
NOT "conversion is not binding".

## What this adds beyond the 55-Q matrix

1. The whole-class denominator is NOT cherry-picked for fireability —
   unlike the 55-Q subset, every census class is represented.
2. The answerable-only column is the capability headline; the raw
   `correct` column mixes in abstention-design questions where refusing
   scores as correct.
3. Every class in the census is represented, including the classes the
   55-Q subset excludes by construction (`ago-relative` 31,
   `frequency/count` 12, `duration-state` 7, `relative-date-lookup` 6,
   `nary-ordering` 4 — the deferred negative classes filed as
   #2652–#2655 + #2886).
4. The per-class split shows where the admission-vs-conversion boundary
   sits for classes the widening arms do not cover.

Method: `classify_outcome` with the default pool horizon (40) and the
derivable `structural-absence-undated-gold` sub-class from the dataset
join; `wilson_ci` from `tools/longmem_eval/report.py`.
