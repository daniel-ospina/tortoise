---
title: "2578 Temporal Measurement — Runbook & Gate Output"
type: operations
domain: operations
doc_status: live
created: 2026-09-09
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# 2578 Temporal Measurement — Runbook & Gate Output

> Procedure + committed results for the #2578 temporal measurement lane
> (2×2 admission/conversion attribution + pre-registered widening ablation
> on the 55-Q deterministic subset of the 133-Q temporal census).
> Companion lane to the what/when/why assembler build (#2165 lane 2) —
> this lane MEASURES only; it never builds the assembler.
>
> The historical "temporal 0/13" claim is NOT reproducible from committed
> artifacts (no committed artifact pins the 13) — per the 2026-09-09 scope
> decision it is annotated as unreproducible and superseded by this
> measured record (see the generated gate output linked below).

## Pre-registration

Written before any arm runs by `measure_temporal.write_preregistration`
(pinned ARM TABLE with per-arm reach statements, the conversion null, the
R5 rollback guard, reader-constancy assertion, refusal-classifier bars).
See the scoped plan `docs/plans/2026-09-09-2578-temporal-measurement.md`
+ the scope comment on issue #2578.

## Gate output

The Task-4 operator run's gate output is generated (never hand-edited) at
[`docs/runbook/2578-gate-output.md`](2578-gate-output.md) by
`measure_temporal.gate_output`: 2×2 per census class, widening-arm
comparisons (McNemar), rollback-guard readout, per-arm
reach-vs-observed-gold-depth table, and the three pre-registered decision
branches. The per-question evidence rows are committed at
[`docs/runbook/2578-measured-outcomes.jsonl`](2578-measured-outcomes.jsonl)
(8 arms × 55 questions, one graded outcome per line).

**Measured verdict (2026-09-10):** decision branch =
**admission-attributed**. The honest headline is NOT the raw `correct`
count: on the 55-Q subset the baseline answers **0 of the 52 questions
that have an answer** (all 3 of its "correct" outcomes are
abstention-design questions where refusing is the right behaviour); the
whole-class 133-Q baseline is **2 of 127 answerable** (0.016, 95% CI
0.004–0.056). Reranking is the one lever that produces real answers:
`applied-rerank` (`--rerank --rerank-pool 120 --rerank-cap 3`) takes
answerable-correct from **0 → 6 of 52** (McNemar p=0.0312, 6 discordant
pairs, 6–0), with admission-attributed failures 52 → 30, refusal rate
0.982 → 0.764, and mean reader context 94.9 → 624.0 tokens. `cap3-only`
also lifts (answerable 0 → 5 of 52; p=0.0625, 5–0) — it is the same
rerank family with the pool left at 40. `tr_top_k` 16/20/24, `c2-on`, and
`pool-only-isolation` are all null: widening the pool WITHOUT reranking
changes nothing (the isolation result the arms exist to test — 0
answerable-correct, byte-identical to baseline).

**Known limitations (recorded, not hidden):**
1. The R5 rollback guard is non-discriminating on this data — the
   pre-registered bound (baseline refusal 0.982 + margin 0.10 = 1.082)
   exceeds the ceiling of a refusal rate (1.0), so no arm could ever be
   flagged. The guard's silence carries no evidential weight here. The
   observed arm rates are NOT all down: `c2-on` and `pool-only-isolation`
   sit AT 1.000, above the 0.982 baseline — the gate output reports the
   above/equal/below tally from the data rather than asserting a
   direction.
2. The 55-question subset is the temporal-analysis classes
   (`ordering/compare` 34, `interval` 19, `current-state` 2) — the
   `recency/current-state` family (8 questions) is a different knob family
   and is out of scope by pre-registration.
3. Correctness levels are low in absolute terms (0 of 52 answerable at
   baseline). On the reranked arms conversion becomes the visible residual:
   `conv-refusal` 12–17 and `conv-wrong` 1–4. **The reader-wrong figure is
   an UPPER BOUND**: a second-opinion scan (`refusal_classifier_hint`) finds
   3 of the 5 conversion-wrong answers are absence statements the shared
   product classifier does not match — they are NOT re-labelled (the 2×2
   deliberately uses the same classifier the product uses), and the gate
   output reports the disagreement count alongside the split.
4. The 55-Q gate baseline comes from its own `A-default` run rather than
   from the 133-Q run's subset (the plan asked for no duplicate default
   run). The two agree exactly on the 52 shared qids (labels, classes and
   context tokens), so no number changes — recorded here as a divergence.
5. Conversion is NOT observable in the 133-Q baseline: gold was admitted
   on 0 of 133 questions, so the channel is empty by construction (0
   conv-refusal / 0 conv-wrong is "not measurable", not "not binding").

## 0/13 annotation

The uncommitted deep diagnosis ("gold at ranks 48–68") is superseded by
this measurement's committed per-question outcome rows.
