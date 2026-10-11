---
title: "Extraction cost metrics — the metric set (#3840)"
type: operations
domain: economics
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-hosted-api, tortoise-extractor
aboutObjects: tortoise
created: 2026-10-11
updated: 2026-10-11
---

# Extraction cost metrics — the metric set (#3840)

> **This is a MEASUREMENT definition, not a policy.** It defines what we record so
> we can calibrate extraction cost for ourselves. There is **no spending ceiling**
> here, no customer-visible limit, and no change to what extraction does.
> `#3840` was repurposed to exactly this: *"This measures; policy follows."*
> The cap's number is a separate, owner-held decision.

## Why this document exists

The issue's definitions lived only in an out-of-repo lane report, which is not a
durable home — a definition that exists on one machine is not a definition. This
page is the in-repo home: what we track, what it is for, and what it cannot tell
you.

Read the measurement from `tools/capture_cost_report.py`; the percentiles and the
repricing are computed by `tools/longmem_eval/costing.py` against the versioned
`PRICING_MAP`. Neither is on the billing path: the customer-visible unit stays
`write_ops`.

## The metric set — per extraction call

| Metric | Why it is tracked | Where it lives |
|---|---|---|
| input tokens (`prompt_tokens`) | the dominant cost driver | the `capture_cost` row, the per-stage lane |
| output tokens (`completion_tokens`) | priced differently from input at most providers | the `capture_cost` row, the per-stage lane |
| **total tokens** (`prompt + completion`) | the single figure comparable across providers and models | DERIVED at report time — never stored as a fourth counter |
| the model used | without it, tokens cannot be turned into money, and a model change silently reprices everything | the `by_stage` keys (`stage → provider → model`) |
| the pipeline stage | tells us *where* cost concentrates, so calibration targets the expensive part | the `by_stage` keys |
| number of calls | retries and multi-call stages inflate real cost invisibly | the row's `calls`/`retries`, the lane's `calls` |

### Why total tokens is derived and not stored

A stored total is a second representation of the same quantity and can drift from
the two halves that produced it. The `#5868` lesson is that a sub-total which will
not reconcile is worse than no sub-total at all. Every route the extractor calls
is OpenAI-compatible, where `usage.total_tokens == prompt_tokens +
completion_tokens`, so the derivation is exact rather than an approximation.

## Disclosure counters — the absences this measurement must never hide

A missing measurement must never read as a cheap one. Each counter names a
distinct absence, and all of them ride the emitted row:

| Counter | The distinct fact it names |
|---|---|
| `calls_without_usage` | the call carried no usage block at all |
| `calls_without_cost` | the call carried no usable charge — including a call that carried no usage block at all, which bumps **both** counters, so `calls_without_usage` is a subset of `calls_without_cost` |
| `deadline_aborts` | the generation was killed on a deadline: billed upstream, no tokens |
| `calls_without_tokens` | the provider sent a token count that was not a valid count (bool, fraction, negative, absurd magnitude) — rejected, never shaped into a number |
| `unattributed` | calls were made and no roll-up survived them (`#3824`) |
| `route_cost_overflows` | a `by_stage` sub-total could not be represented, so the breakdown is not a complete partition of the row (`#5868`) |

## Attribution — every row traces back to what caused it

**team · session · timestamp · harness.** Team is the row's `org_id`, session is
`session_id`, timestamp is `created_at`, and `harness` was the last element with
no carrier on the row until `#3843` — it is now always present, with `"unknown"`
standing in when the server could not name one. That sentinel is deliberate: a
missing key and an unproven harness are different facts and must not be the same
shape.

## Derived figures

| Figure | Definition | Where to read it |
|---|---|---|
| tokens per capture | **median and 95th percentile** of the per-capture total — a mean is dominated by outliers | the report's `extraction tokens` section; `distribution.tokens` in the `--out` payload |
| tokens per team per month | the per-team total over a metering window | `by_team[].total_tokens` in the payload for a window read; the durable ledger below for a true month |

The two derived figures and the per-team token totals use the SAME included
captures as the cost percentiles and the per-team spend, so "tokens per
capture", "$ per capture" and "this team's tokens" can never describe
different sets of captures. **One exception, stated rather than implied:** the
per-stage line's `total=` is a breakdown of the `by_stage` envelopes over
**every row in the window**, including the captures the percentiles exclude
(`excluded_no_calls` / `excluded_unmeasured`); it is a split of the emitted
envelopes, not a population-filtered total, which is also why it already
carries the `[NOT additive]` marker.

## Known limits — read before quoting a number

- **The token percentiles include captures whose token count is a FLOOR.**
  Three disclosures ride the token block: `partial_captures` (a call happened
  whose tokens we could not see — an unmetered timeout, an unrolled call, a
  metered call that reported a charge but no token field, or a stored value the
  reader rejected), `tokenless_captures` (calls were metered yet produced no
  usable token count — the field was absent, was an honest zero, or was
  rejected; so the 0 is an absence rather than a tiny extraction, and it
  overlaps `invalid_token_captures`) and
  `invalid_token_captures` (a negative or non-integral stored count — rejected
  rather than truncated or sign-flipped, the reader half of `#5854`).
  `lower_bound` is true when any capture's figure is a floor.
- **The durable per-team-per-month figure is written but not yet readable.**
  `metering_records.capture_tokens_in` / `capture_tokens_out` carry the extraction
  token workload per org per metering window (`#5045`), but no production reader
  exposes them — `get_current_usage` reads write-ops only. Until a reader exists,
  the per-team-per-month figure in a report is a window total, not a month.
- **The percentiles are per SESSION, not per row.** `analytics_events` is
  append-only and a retried capture writes a second row for the same
  `session_id`; rows are summed per session first, so one capture is one sample.
- **Not covered by this metric set:** per-team search volume, fixed/shared SaaS
  allocation (that is `docs/ops/cost-allocation.md`), and storage bytes.
