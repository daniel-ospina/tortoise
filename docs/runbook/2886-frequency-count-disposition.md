---
title: "2886 — census frequency/count disposition (deterministic temporal aggregation)"
type: operations
domain: operations
doc_status: live
created: 2026-10-08
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
issue: 2886
---

# #2886 — frequency/count aggregation resolution — disposition

> Lane `lane:c4-answer-quality`, team `epistemic-team`, domain `data`.
> Deterministic core: `tortoise/temporal_aggregation.py`.
> Per-question outcomes: `docs/runbook/2886-frequency-count-outcomes.jsonl`.
> Repro tool: `tools/longmem_eval/freq_count_disposition.py`.

## Decision

**The census `frequency/count` class (12/133) is not a counting class.** All
12 rows are **date arithmetic over two dated events** — `before`-offset
(5), duration (4), interval (2), summed span (1). Zero of the 12 asks for a
frequency ("how many times"/"how often"). The census label misdescribes
every member, so a frequency/count *aggregator* would have been aimed at a
population that does not exist in the class.

Two committed artifacts establish this independently:

1. the class's own question texts (`tests/_assembly_census.json`) — all 12
   match `how many <time-unit>`; none matches an explicit frequency surface;
2. the assembler's own shape router (`tests/test_assembly_pure.py`) already
   files `370a8ff4` — a row labelled `frequency/count` — as an interval-since
   twin, and pins the class as a measured-negative.

The work that DOES exist is split in two, and this issue lands the first half:

* **deterministic temporal aggregation core** (`tortoise/temporal_aggregation.py`)
  — resolves count/total intents to a distinct-event tally (the multi-session
  restatement trap closed) AND the class's actual date arithmetic
  (interval / before-offset / duration) over admitted dated events. Pure,
  hermetic, abstain-never-guess.
* **admission of the anchor events** — a write-side / retrieval problem, not a
  reader-arithmetic problem, and not this module's job.

## Measured disposition (structural vs conversion)

From `docs/runbook/2578-measured-outcomes-133.jsonl` (the committed
133-question, single-arm `A-default-133q` run — the only committed run that
contains these 12 qids):

| disposition | n | note |
| --- | --- | --- |
| **structural** (gold never admitted) | **11** | every answerable class member |
| **abstention-control** (correct refusal) | 1 | `c8090214_abs` — refusing is correct, not a capability fix |
| **conversion** (gold admitted, reader wrong) | 0 | — |
| **fixed-by-admission** (gold admitted, reader right) | 0 | — |

**`conversion` is UNREACHABLE by construction, not measured as zero.** All
three arms in the committed 8-arm file that carry `gold_admitted` rows
(`applied-rerank` 21, `cap3-only` 21, `tr_top_k24` 1 — qid `8c18457d`) ran
only on the 55-question subset — which contains **none** of
the 12. No run exists in which a class member is observed *with gold
admitted*, so the conversion bucket cannot fire. The
`conversion_undetermined` flag reports whether the rows being summarized
observed a gold-admitted class member at all — so a `conversion` of 0 in
those rows is **undetermined, not measured**. The **union scan over the two
committed outcome files declared as `OUTCOME_SOURCES` (every arm in each)**
— `2578-measured-outcomes-133.jsonl`
and `2578-measured-outcomes.jsonl` — is reported separately, as
`conversion_reachable_any_arm`, so the union claim is established for the
committed data without being conflated with the loaded arm. Resolving it
requires the 12 re-run under `applied-rerank` (the reported remainder,
below).

Per-question rows are committed in the same shape as
`2578-measured-outcomes.jsonl` (arm / qid / cls / label / context_tokens /
gold_admitted / reader_refusal / answer) plus the reclassification
(`reclassified_cls`, `aggregate_kind`, `aggregate_unit`) and `disposition`.
Regenerate:

```bash
.venv/bin/python tools/longmem_eval/freq_count_disposition.py --print
```

## The deterministic core

`tortoise/temporal_aggregation.py` — pure functions over ADMITTED dated
events; no graph, no model, no IO, no clock.

| shape | intent | resolution |
| --- | --- | --- |
| `count` | "how many times", "how often" | distinct-event tally (`count_distinct_events`) |
| `total` | "how many weeks **in total**" across events | sum of distinct events' spans |
| `interval` | "how many days **between** A and B / **since** A when B" | `end − start` |
| `before-offset` | "how many days **before** B did A happen" | `end − start` |
| `duration` | "how many days did it take / have I been" | `end − start` |

The named path is `resolve_temporal_aggregate(question, *, events, start,
end, unit)`; it abstains (`value=None`, explicit `reason`) whenever the
question is non-temporal, the anchors are missing, or the input exceeds
`MAX_EVENTS` — the reader lane keeps the case, so no LLM is ever silently
bypassed (a reader-model swap stays #2013-gated).

**Restatement trap.** Distinct-event identity is: explicit `event_id` → else
normalized content → else the repo's committed conservative paraphrase band
(`extractor_v2.fold_allowed` + `NOOP_MIN_OVERLAP`). Events are canonicalised
in `(session_date, event_id, normalized content)` order — a **total** key, so
same-date / undated identity-less rows never tie-break on input index — and
restatements are clustered order-independently (union-find over the pairwise
fold relation, i.e. its transitive closure, not a greedy sequential scan),
so the earliest articulation wins and the tally is input-order independent. A
negated / re-conditioned / subject-substituted restatement stays its own
event (D12/O4).

**Not folded into the assembler (#2165).** The assembler locates subjects
through the graph; this core is a downstream arithmetic/tally step over the
events the reader already admitted.

## Acceptance mapping

| acceptance criterion | status |
| --- | --- |
| named aggregation path exercised against the 12 census qids | ✅ `tests/test_temporal_aggregation.py::test_census_frequency_count_class_classified` classifies all 12; the resolved intent is pinned per qid |
| per-question outcomes committed, 2578 shape | ✅ `docs/runbook/2886-frequency-count-outcomes.jsonl` |
| structural vs conversion split stated | ✅ this doc; 11 structural / 0 conversion (unreachable) / 1 abstention-control |
| reader-model change stays #2013-gated | ✅ no reader/prompt/production path changed |

## Remainder disposition

1. **Run the 12 under a gold-admitting arm** (`applied-rerank`) to make the
   conversion leg reachable. Until then the disposition is
   `conversion_undetermined=True`, not `conversion=0`.
2. **Wire the core into the eval reader lane** behind an OFF-by-default
   flag (mirroring #2521's `aggregative_flag`), so a gold-admitted run can
   measure how many of the conversion-bound cases the deterministic path
   closes. Not landed here — it is measurement wiring, and it needs the
   gold-admitting run to read out.
3. **`detect_aggregative_intent` false-positive** — the #2521 detector
   currently classifies all 12 date-arithmetic rows as entity-scoped
   aggregative (its own contract says elapsed-time shapes are R5 temporal).
   Filed separately as **#7804** rather than absorbed, per the scoping
   skill's file-extra-issues rule.

## Reproduce

```bash
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
  tests/test_temporal_aggregation.py \
  tests/longmem_eval/test_freq_count_disposition.py -q -p no:cacheprovider
```
