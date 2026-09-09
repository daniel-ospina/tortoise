---
title: "2578 Temporal Measurement — Runbook & Gate Output"
type: operations
domain: operations
doc_status: live
created: 2026-09-09
ownedBy: epistemic-team
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

(written by the Task-4 operator run — 2×2 per census class, widening-arm
comparisons, rollback-guard readout, per-arm reach-vs-observed-gold-depth
table, and the three pre-registered decision branches)

## 0/13 annotation

The uncommitted deep diagnosis ("gold at ranks 48–68") is superseded by
this measurement's committed per-question outcome rows.
