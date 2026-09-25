---
title: "Fixed / shared SaaS cost allocation"
type: operations
domain: economics
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-hosted-api, tortoise-monitoring
aboutObjects: tortoise, product/pricing.json
created: 2026-09-25
updated: 2026-09-25
---

# Fixed / shared SaaS cost allocation

> **This is an ALLOCATION (showback), not a measurement.** The numbers on this
> page are a POLICY applied to a stated total. No measurement observes "this
> org's share of the Fly machine"; nothing here should be read as a measured
> cost. Implemented in `tortoise/cost_allocation.py` (issue **#4493**); the
> consuming metric is `tortoise_team_cost_cents`.

## Why this exists

The fixed / shared monthly lines below arrive as **one bill with no per-org
attribution**, so no tier can be checked against its true total cost — only its
marginal LLM cost. The per-org LLM cost is measured separately (`metering_records`
→ `ask_cost_usd` / `capture_cost_usd`, summed by `get_cohort_spend_usd`); this
document covers the **fixed/shared** remainder.

## The rule

A line is declared in `tortoise/cost_allocation.py` → `ALLOCATION_LINES`:

| Field | Meaning |
|---|---|
| `name` | the line |
| `basis` | how the total is split: `even` (indivisible base fee) or `proportional` (by the org's measured write-ops for its current metering window) |
| `total_cents` | the stated **monthly** amount (integer cents) |
| `source` | where the figure came from |
| `as_of` | the date the figure is stated for |
| `is_estimate` | `True` when the figure is a rate/estimate, **not** a measured invoice |
| `env_var` | the override an operator sets to supply a real invoice figure |

Declared lines (2026-09-25):

| Line | Basis | Default (cents/mo) | Source | Estimate? |
|---|---|---|---|---|
| `fly_base` | `even` | 2140 | `fly.toml` COST note (iad list price snapshot) | **yes** — `as_of 2026-09-10` |
| `falkordb_base` | `even` | 0 | not configured | yes |
| `supabase_base` | `even` | 0 | not configured | yes |
| `posthog` | `proportional` | 0 | not configured | yes |
| `sentry` | `proportional` | 0 | not configured | yes |

**A `0` default means "this line must be configured by an operator", not "the
cost is zero".** Set `TORTOISE_COST_<LINE>_CENTS` to supply a real figure. The
in-repo figures are rates and dated estimates, never a measured invoice — the
B7 cost-structure audit established that vendor invoice amounts are not in-repo.

### Arithmetic

Integer **Hamilton largest-remainder**: every org gets `floor(total × w / W)`,
then the leftover cents go one each to the largest fractional remainders, ties
broken lexicographically by org id. The shares always sum to the line total
**exactly**, and anything that cannot be allocated lands in the explicit
residual bucket `__residual__` — never smeared, never dropped.

### Window

The declared total is **monthly** (the vendor's window). The allocation window
is reported on every snapshot. The org's own metering window is
subscription-anchored (D10/D13) and is deliberately **not** used to prorate:
the full declared total is allocated across the orgs in the refresh. Proration
across differing windows is deferred to the #5045 substrate.

## Fail-closed, never a silent zero

An unreadable input is **never** reported as `0`:

* the org enumeration (`_iter_registered_orgs`) returns `[]` on *any* failure,
  so an empty list is treated as **unavailable**, never as "a fleet with no
  orgs";
* the proportional basis is read with `metering.measure_write_ops`, which
  **raises** on an unreadable window or read (unlike `get_current_usage`, which
  degrades to a zero view — the reason it is not used here: its failure path is
  indistinguishable from a measured zero).

When the enumeration is unavailable the metric is left at **last-known-good**
(it is not cleared — clearing would make "unreadable" and "no cost"
indistinguishable). A single unreadable proportional line is reported
`unavailable` while the readable lines still publish.

## How to read it

Three read paths, no dashboard:

1. **`fly logs`** — the refresh emits one `INFO` line per cycle
   (`tortoise.cost_allocation`): `cost allocation refresh kind=allocation
   state=… window=… lines=… orgs=… published_cents=… residual_cents=…`. This is
   the production-readable path today (nothing scrapes `/metrics` in production).
2. **PromQL** — `tortoise_team_cost_cents{team="<org>"}` once a scraper exists.
3. **In-process** — `tortoise.cost_allocation.current_snapshot()` (full
   declaration + shares + state) or `monitoring.team_cost_cents()`.

### State vocabulary

Every figure carries `state ∈ {measured, unavailable, not_measurable}` — the
same vocabulary as `tortoise/activation_scorecard.py` and
`docs/runbook/b7-activation-scorecard.md` §"Zero vs no-signal". A `0` is only
ever emitted with `state == "measured"`. `not_measurable` means the line's
declared total is 0 (unconfigured) or no org carries weight.

## What this is NOT

* Not written into `metering_records` — that ledger's cost columns are
  "MEASURED metres, never billing estimates", and the **#3665** cohort spend
  ceiling sums them. An allocation there would inflate the cap. The separation
  is enforced by a test.
* Not a price, a tier boundary, a cap or a quota. `product/pricing.json` is
  untouched by this mechanism.

## Operational notes

* The writer runs on the existing hourly maintenance loop
  (`hosted_api._event_retention_loop`), so the metric is first populated within
  one interval after boot. It is best-effort: a refresh failure can never
  terminate event retention.
* The org label is bounded (`MAX_ORG_LABELS`, default 512, with a fixed
  `__other__` overflow child), so org growth cannot blow up the metric's
  cardinality.
* The single writer of `TEAM_COST` is
  `tortoise.cost_allocation.refresh_and_publish`; `monitoring.record_cost` is
  the only function that touches the metric.
