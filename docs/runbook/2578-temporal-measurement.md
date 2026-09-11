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

## Task 5 — v2-lane structural probe (wave 1, saturation reached)

The assembler lane's blocking question was whether the v2 lane actually
*produces* the structure an assembler would consume. The probe ingests the
real haystack through `ingest_v2` (the committed v2 path, LLM extractor,
one fresh graph namespace per question on the dedicated `falkordb-eval`
container) and then measures the substrate directly — no reader, no
judge, no answer scoring.

**Result: substrate present on 15 / 15 questions, unanimous across all
three census classes — the pre-registered stop rule fires and the full-55
run is not justified.**

| class | n | substrate present | Objects | Point→aboutObject | Event→aboutObject | dated `startedAt` | gold sessions with events |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ordering/compare | 9 | **9/9** | 2664 | 6351 | 1261 | 603 | 17 (of 18) |
| interval | 4 | **4/4** | 991 | 2442 | 457 | 229 | 8 (of 8) |
| current-state | 2 | **2/2** | 509 | 1322 | 282 | 134 | 4 (of 4) |
| **all** | **15** | **15/15** (1.000) | 4164 | 10115 | 2000 | 966 | **29 of 30** |

Per-question (all 15 in `2578-probe-wave1-outcomes.jsonl`): 164–370 Objects,
427–843 Point→aboutObject edges, 86–184 Event→aboutObject edges, and a
dated gold event for **both** gold sessions on 14 of 15 questions (one
ordering/compare question got 1 of 2). `errors = 0`.

**What this settles, and what it does not.**

- It settles the *existence* question: the v2 lane produces aboutObject
  edges from Points AND Events, and dates them, for the gold sessions of
  the deterministic-fireable questions in every class. The assembler lane
  is not building on an empty substrate.
- It does **not** settle assembler admission or answer quality — those are
  the 55-Q matrix above and #2165's own acceptance. Substrate existence is
  a prerequisite, never a result.

**Extractor provenance.** The run resolved `('deepseek-direct', ['deepseek-direct', 'openrouter'])` and stayed on `deepseek-direct` (recorded at report level in `extractor`). Note the per-row `extractor_model` field is `null` — `RoutingModel` exposes `provider`, not an `id`, so the probe's per-row field could not be filled; the report-level record is authoritative here.

**The name-match figure is a PROXY and must not be read as a resolution
rate.** The probe derives candidate entity names by capitalisation from the
gold subject and matches them against ingested Object names: **227 of 2823
(8.0%)**. This is a crude string proxy with no eval-side entity resolver, so
an 8% figure says the *proxy* is weak far more than it says the entities are
missing (the same runs produced 4164 Objects with 10115 aboutObject edges).
Reported because hiding it would be worse; never quoted as "8% of entities
resolve".

**Idempotence check (NOT an independent re-measurement).** The
parallelised per-question workers and the sequential runner overlapped on
6 questions, so each was run against its graph twice. On **5 of the 6**
the short pass finished in **~4–9 s instead of ~15–20 min** because that question's graph
namespace was **already populated** and the ingested point ids are
deterministic (`lme:<qid>:s<n>:t<m>`) — so the re-run is an idempotent
re-read, and its agreeing verdict is near-tautological. It shows the
substrate is *persisted* and (for 5 of 6) the measurement is *stable on a
populated graph*; it says nothing about extraction variance.

Counts were identical in **5 of 6**. The exception is `08f4fc43`: 150
Objects on an 8.9 s pass and 164 on a 4530 s pass. The committed row is
the second (164) — the longer, actually-ingesting pass. **The 14-Object
difference is unexplained and is reported as unexplained**, not attributed
to extraction variance. Per-pass rows for all 6:
[`2578-probe-wave1-duplicates.jsonl`](2578-probe-wave1-duplicates.jsonl).

**Infra incident (#2969).** Partway through the wave the ingest collapsed
from ~25 min/question to >4 h/question, blocked indefinitely in a FalkorDB
socket read — no timeout, no heartbeat, the client at 0% CPU and no error —
while the extractor endpoint (1.0–1.2 s at a realistic payload) and
isolated graph queries (0.07–0.68 ms) were both fast. The eval container was
in a continuous active-defrag loop; defrag was disabled as a mitigation and
the wave-1 remainder was parallelised per question (independent graph
namespaces) to bound the wall-clock. Filed with full evidence as #2969.

## Evidence artifacts

| artifact | contents |
| --- | --- |
| [`2578-reports/`](2578-reports) | the raw per-arm run reports behind the 55-Q matrix: methodology (reader spec + prompt hash + judge + `applied_knobs`) and per-outcome `measure_facts`, plus each run's `integrity` block (`valid=true` for all 8 arms — #1747 census-class criterion, `n_hard_invalid=0`, `invalid_rate=0.0 <= threshold 0.0`) |
| [`2578-reports-133/A-default-133q.json`](2578-reports-133) | the whole-class 133-Q baseline report (same shape; `integrity.valid=true`) |
| [`2578-measured-outcomes.jsonl`](2578-measured-outcomes.jsonl) | 8 arms x 55 questions, one graded outcome per line |
| [`2578-measured-outcomes-133.jsonl`](2578-measured-outcomes-133.jsonl) | the 133-Q baseline, one graded outcome per line |
| [`2578-probe-wave1.json`](2578-probe-wave1.json) + [`-outcomes.jsonl`](2578-probe-wave1-outcomes.jsonl) | the Task 5 v2 structural probe: per-class aggregates, saturation, extractor provenance, the #2969 infra record; one row per question |
| [`2578-probe-wave1-duplicates.jsonl`](2578-probe-wave1-duplicates.jsonl) | all 6 questions that were run twice (every pass, with duration + counts), so the idempotence claim is checkable |

**Reproduce the published tables from the committed data** (no cache, no
network, no docker):

```
python -m tools.longmem_eval.measure_temporal \
  --reports docs/runbook/2578-reports \
  --instances <the 55-Q dataset rows> \
  --out-md /tmp/gate.md --out-jsonl /tmp/rows.jsonl
```

Both outputs are byte-identical to the committed `2578-gate-output.md`
and `2578-measured-outcomes.jsonl`. The producer also runs the
pre-registered session dedup (`dedup_instance_sessions`) on every supplied
row, so the date-alignment guarantee applies to the committed path itself.

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
rerank family with the pool left at 40. `tr_top_k` 16, `tr_top_k20`, `c2-on`
and `pool-only-isolation` are all null on answerable-correct (0/52).
`pool-only-isolation` is the clean isolation result — widening the pool
WITHOUT reranking changes nothing, answerable-correct byte-identical to
baseline (0/52). `tr_top_k24` sits at the edge: 1/52 answerable-correct
(one question), 1 discordant pair, p=1.0000 — reported as a single-question
movement, not an effect.

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
   run). The two agree exactly on all 55 shared qids (labels, classes and
   context tokens), so no number changes — recorded here as a divergence.
5. Conversion is NOT observable in the 133-Q baseline: gold was admitted
   on 0 of 133 questions, so the channel is empty by construction (0
   conv-refusal / 0 conv-wrong is "not measurable", not "not binding").

## 0/13 annotation

The uncommitted deep diagnosis ("gold at ranks 48–68") is superseded by
this measurement's committed per-question outcome rows.
