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
cost is zero".** The override variables are exactly (integer **cents**, monthly
window):

| Line | Override variable |
|---|---|
| `fly_base` | `TORTOISE_COST_FLY_BASE_CENTS` |
| `falkordb_base` | `TORTOISE_COST_FALKORDB_BASE_CENTS` |
| `supabase_base` | `TORTOISE_COST_SUPABASE_BASE_CENTS` |
| `posthog` | `TORTOISE_COST_POSTHOG_CENTS` |
| `sentry` | `TORTOISE_COST_SENTRY_CENTS` |

(The naming rule is `TORTOISE_COST_<LINE>_CENTS`; the table is the enumerated
list, and a test pins every `LineSpec.env_var` to an occurrence in
`.env.example`.) The in-repo figures are rates and dated estimates, never a
measured invoice — the B7 cost-structure audit established that vendor invoice
amounts are not in-repo.

### Override provenance

When an override supplies the value, the published provenance describes the
**override**, not the code declaration:

* `source` is the env var name (`env override TORTOISE_COST_…_CENTS`);
* `as_of` is the **observation date** — the refresh date on which the override
  was read — never the declaration's `as_of` (that date belongs to the in-repo
  figure, and repeating it would claim the operator's number was stated by the
  declaration);
* `is_estimate` is `False`: an operator-supplied invoice is a real figure, not
  an estimate.

Without this rule an operator's invoice is published as
`is_estimate=True, as_of=2026-09-10`, and the four zero-default lines report
`source="not configured…", as_of="unset"` beside a NON-ZERO total.

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
  orgs". The cost caller additionally passes `require_complete=True`: when the
  Supabase page fills its explicit limit an incomplete/possibly-truncated page
  returns `None` (the fleet is UNKNOWN, so the metric stays last-known-good),
  while the best-effort event-retention sweep still processes the page it
  received (#5388 tracks the residual exactly-at-the-cap ambiguity);
* the proportional basis is read with `metering.measure_write_ops`, which
  **raises** on an unreadable window or read (unlike `get_current_usage`, which
  degrades to a zero view — the reason it is not used here: its failure path is
  indistinguishable from a measured zero).

When the enumeration is unavailable the metric is left at **last-known-good**
(it is not cleared — clearing would make "unreadable" and "no cost"
indistinguishable). **The same holds when the enumeration succeeded but a line
could not be read**: `publish` gates on the whole-snapshot `state`, so a
line-`unavailable` refresh also leaves the metric at last-known-good. Re-recording
only the still-readable lines would silently DROP every org's published total
(the unreadable line's share vanishes, with no state on the metric to say so),
which reads exactly like "this org's cost fell". The distinction between the two
shapes is logged via `enumeration_available`.

That freeze is **unbounded in time**: nothing ages the retained values out,
and the metric itself carries **no staleness or freshness signal** — no series
reports the age of the figure. The only signal that a value is frozen is the
refresh log line (§"How to read it"); the metric surface itself is un-scraped
in production, so `tortoise_team_cost_cents` alone cannot distinguish a live
figure from a frozen one.

A **malformed override** is unreadable input too: a present-but-unusable
`TORTOISE_COST_*_CENTS` value (non-integer, negative, empty/whitespace, or above
the `MAX_LINE_CENTS` sanity ceiling) reports that line `unavailable` with a
detail naming the variable and the offending value — it is **never** silently
replaced by the declared default. Falling back would make a misconfiguration
indistinguishable from "unconfigured", and for four of the five lines the
default is `0`. An absent variable and a present-but-empty one are also
reported distinctly.

## How to read it

Three read paths, no dashboard:

1. **`fly logs`** — the refresh emits one `INFO` line per cycle
   (`tortoise.cost_allocation`): `cost allocation refresh kind=allocation
   state=… attempted_window=… published_window=… lines=… orgs=…
   published_cents=… residual_cents=… overflow_cents=…`. This is the
   production-readable path today (nothing scrapes `/metrics` in production).
   `attempted_window` is the window this refresh evaluated; `published_window`
   is the window the values READ BACK from the metric belong to. The two are
   equal whenever the last successful publish is in the SAME window; they
   differ whenever the retained window differs from the attempted one —
   including an `unavailable` refresh whose attempt falls in a LATER window
   (the metric still carries the last-known-good window then), and the
   never-published case where nothing has ever been published and
   `published_window=unknown..unknown`. Naming both means a reader can never
   attribute a stale figure to the attempted period. The `published_cents`
   figure is read
   back from the METRIC itself (`allocation_by_org()`), not from the in-memory
   shares, so the reconciliation warning compares what was actually published
   against the declared totals and can fire.
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
  one interval after boot **provided the enumeration is confirmable**. At or
  above the 1000-org enumeration cap, or on repeated enumeration failure, the
  cost caller fails closed on every cycle and the metric stays last-known-good
  (or empty if it was never populated), with no on-metric evidence that it is
  not live. It is best-effort: a refresh failure can never terminate event
  retention.
* The org label is bounded (`MAX_ORG_LABELS`, default 512, with a fixed
  `__other__` overflow child), so org growth cannot blow up the metric's
  cardinality.
* The only PRODUCTION path that writes `TEAM_COST` is
  `tortoise.cost_allocation.refresh_and_publish` → `publish`. In that path
  `monitoring.record_cost` is the setter and `monitoring.prune_team_cost` the
  pruner; both are called only from there, plus the test seams
  (`_reset_for_tests` / `clear_team_cost`). A test asserts this on the METRIC,
  not on a function-name substring. The guard matches syntactic
  `Name`/`Attribute` occurrences of `TEAM_COST` and its mutators **anywhere
  inside a `def`/`async def` subtree in `tortoise/**/*.py` outside
  `tortoise/monitoring.py`** — including a `lambda` or a class nested inside a
  function body, which `ast.walk` inspects and attributes to that function —
  and every such reference must sit inside `publish` or the test seam. What it
  does NOT match is module-level and top-level class-body references, aliased
  imports, and `getattr` string lookups. The skip is implemented by file NAME,
  not by path: `tortoise/monitoring.py` holds the definitions and must be
  skipped, and any OTHER file named `monitoring.py` is exempt for that same
  name-based reason — e.g. `tortoise/shared_state/monitoring.py`, which holds
  no team-cost definitions at all. That is a disclosed hole: a new mutator
  added to any other `monitoring.py` would not be caught.
