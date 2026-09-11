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

| class | n | correct | 95% CI | admission | conv-refusal | conv-wrong |
| --- | --- | --- | --- | --- | --- | --- |
| **ALL 133** | 133 | 8 (0.060) | 0.031–0.114 | 125 | 0 | 0 |
| ordering/compare | 34 | 2 (0.059) | 0.016–0.191 | 32 | 0 | 0 |
| ago-relative | 31 | 1 (0.032) | 0.006–0.162 | 30 | 0 | 0 |
| interval | 19 | 0 (0.000) | 0.000–0.168 | 19 | 0 | 0 |
| frequency/count | 12 | 1 (0.083) | 0.015–0.354 | 11 | 0 | 0 |
| recency/current-state | 8 | 0 (0.000) | 0.000–0.324 | 8 | 0 | 0 |
| duration-state | 7 | 0 (0.000) | 0.000–0.354 | 7 | 0 | 0 |
| other | 6 | 1 (0.167) | 0.030–0.564 | 5 | 0 | 0 |
| relative-date-lookup | 6 | 1 (0.167) | 0.030–0.564 | 5 | 0 | 0 |
| nary-ordering | 4 | 1 (0.250) | 0.046–0.699 | 3 | 0 | 0 |
| current-state | 2 | 1 (0.500) | 0.095–0.905 | 1 | 0 | 0 |
| recency | 2 | 0 (0.000) | 0.000–0.658 | 2 | 0 | 0 |
| offset-comparison | 1 | 0 (0.000) | 0.000–0.793 | 1 | 0 | 0 |
| pattern/recurring | 1 | 0 (0.000) | 0.000–0.793 | 1 | 0 | 0 |

**Overall:** 8/133 correct
(0.060); admission-attributed
125, conversion-refusal 0,
conversion-wrong 0.

## What this adds beyond the 55-Q matrix

1. The whole-class number is the honest headline for "how does the
   product do on temporal questions at default settings" — it is NOT
   cherry-picked for fireability.
2. Every class in the census is represented, including the classes the
   55-Q subset excludes by construction (`ago-relative` 31,
   `frequency/count` 12, `duration-state` 7, `relative-date-lookup` 6,
   `nary-ordering` 4 — the deferred negative classes filed as
   #2652–#2655 + #2886).
3. The per-class split shows where the admission-vs-conversion boundary
   sits for classes the widening arms do not cover.

Method: `classify_outcome` with the default pool horizon (40) and the
derivable `structural-absence-undated-gold` sub-class from the dataset
join; `wilson_ci` from `tools/longmem_eval/report.py`.
