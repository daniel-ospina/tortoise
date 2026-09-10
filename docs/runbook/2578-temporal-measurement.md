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
> measured record. **PENDING:** gate output is written here by
> `tools.longmem_eval.measure_temporal.gate_output` after the operator-run
> baseline + widening arms complete (Task 4).

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
**admission-attributed**. `applied-rerank` (`--rerank --rerank-pool 120
--rerank-cap 3`) is the only arm that moves the needle: baseline 3/55
correct → 9/55 (McNemar p=0.0312, 6 discordant pairs, 6–0 arm wins), with
admission-attributed failures 52 → 30. `tr_top_k` 12→16/20/24, `c2-on`,
and `pool-only-isolation` are all null — widening the pool WITHOUT
reranking changes nothing (the isolation result the arms were built to
test). Rerank additionally pushes refusal DOWN (0.982 → 0.782) while the
reader context grows 94.9 → 624.0 mean tokens.

**Known limitations (recorded, not hidden):**
1. The R5 rollback guard is non-discriminating on this data — the
   pre-registered bound (baseline refusal 0.982 + margin 0.10 = 1.082)
   exceeds the ceiling of a refusal rate (1.0), so no arm could ever be
   flagged. The guard's silence carries no evidential weight here; the
   observed arm refusal rates all moved down or equal.
2. The 55-question subset is the temporal-analysis classes
   (`ordering/compare` 34, `interval` 19, `current-state` 2) — the
   `recency/current-state` family (8 questions) is a different knob family
   and is out of scope by pre-registration.
3. Correctness levels are low in absolute terms (baseline 3/55) —
   conversion on admitted gold remains the dominant residual error, with
   `conv-refusal` 13–17 and `conv-wrong` 1–3 on the reranked arms.

## 0/13 annotation

The uncommitted deep diagnosis ("gold at ranks 48–68") is superseded by
this measurement's committed per-question outcome rows.
