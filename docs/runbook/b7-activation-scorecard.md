---
title: "Activation scorecard runbook (beta-readiness lane B7)"
type: operations
domain: operations
doc_status: live
created: 2026-09-17
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Activation scorecard — runbook

## Why this exists

The beta is judged on **activation**, not registration. Before this, nothing
in the hosted platform could answer *is this session activated?*:
`website/apps/dashboard/src/captureStatus.js:23` answers a DIFFERENT question —
*was this captured?* — off the capture **receipt** alone (a receipt proves a
durable hosted 2xx capture, and it is server-written on purpose; it is not a
mislabel of anything, it is simply not activation). `analytics.py:114` calls
`first_api_call` the "Activation event", but it fires on any `POST /v1/*`
returning < 400 — so a request that stored a transcript and produced no memory
scores as activation.

## The definition

`docs/product-success-eval.md:154` already defines Activation:

> first capture → first answered-from-memory, within 24 hours

and the beta roadmap adds a 15-minute window. That definition is the target.
This scorecard exposes the legs that are **measurable today** and refuses to
score the legs that are not.

## What is measured

| Stage | Meaning | Granularity |
| --- | --- | --- |
| `captured` | A `(:Session)` exists in the window | graph-scoped, sessions |
| `stored` | ≥1 transcript-turn `(:Point)` (`pointKind: 'event'`) wired `(:Session)-[:CONTAINS]->` — the transcript was actually persisted | graph-scoped, sessions |
| `memory_produced` | ≥1 point that is NOT a transcript turn (`pointKind IS NULL OR pointKind <> 'event'`), i.e. extraction produced memory | graph-scoped, sessions |
| `recall_attempted` | A retrieval tool call (`tortoise_search` / `tortoise_recall` / `tortoise_search_sessions`) at or after the org's `first_memory_at`. `tortoise_ask` is excluded — it is eval-only, not a product surface (#3849) | **org-wide, event count** |
| `value_confirmed` | **Not measurable** — permanent refusal | — |

### Two traps this is built to avoid

**A zero is only meaningful when paired with `state == "measured"`.** Every
stage carries `state` and a `reason`. An empty store and an unreachable store
both look like 0; only one of them is a fact. Never read a bare number.

The two non-measured states are **not** interchangeable:

| State | Means | Examples |
| --- | --- | --- |
| `unavailable` | the metric exists but could not be read *now* — a retry or a repair fixes it | `analytics_store_unreachable`, `analytics_write_path_unconfigured`, `analytics_page_cap_truncated`, `window_predicate_not_applied`, `unparseable_created_at`, `first_memory_at_missing`, `unparseable_analytic_rows`, `unclassifiable_analytic_rows` |
| `not_measurable` | there is nothing to measure, and no retry changes that | `no_memory_produced_in_lifetime` (nothing to recall from); `value_confirmed` (no instrument exists — the stage always reports this) |

The window guard **fails closed**: a row that is out of window, or that cannot
be placed on the timeline at all (unparseable, naive, an unexpected type),
makes all three graph stages `unavailable` rather than being counted — counting
it would assert it is in-window with no evidence.

`recall_attempted` refuses on the same principle rather than reporting a lower
bound as a count: a retrieval call whose timestamp will not parse, or whose
`tool_name` cannot be read, makes the stage `unavailable` instead of silently
shrinking the number. `first_memory_at` is NULL for two opposite reasons — no
memory exists (`not_measurable`) versus memory exists without a timestamp
(`unavailable`, `first_memory_at_missing`) — and the two must not be conflated.

**The cohort roll-up preserves the same distinction.** A stage where every org
answered "nothing to measure" rolls up to `not_measurable`; it never becomes
`unavailable`, which would accuse healthy orgs of failing to report.
`value_confirmed` is `not_measurable` for every org, so this is the normal path,
not an edge case.

The analytics leg's interval is **`[since, until)` — the same as the graph
legs.** This is a guarantee, not a coincidence: it is asserted by a test,
because the two legs are separate queries and nothing else stops them from
drifting. It DID drift once — the analytics leg used `gt`, so a tool call
landing exactly on `since` was dropped while a session created at that same
instant was counted, letting the funnel disagree with itself on the boundary
instant. If you change one leg's bounds, the other must move with it.

**`recall_attempted` is an ATTEMPT, not an answer.** `mcp_tool_call.status ==
"ok"` means only that the tool did not raise. `result_count` / `abstained` are
not in the analytics props allowlist
(`hosted_api.py::_ALLOWED_ANALYTICS_PROPS`), so "answered from
memory" is currently unknowable. No `activated` field and no activation rate is
emitted anywhere, and `tools/activation_cohort.py` fails loudly if a payload
ever grows one.

Note the legs mix granularity: stages 1–3 are per-graph session counts, stage 4
is an org-wide event count with no `graph_id` (`analytics_events` has none) and
no `session_id`. This is an activation **funnel** view, not a per-session claim.

## Querying it

```bash
curl -s "$BASE/v1/activation/scorecard?since=2026-09-01T00:00:00Z&until=2026-10-01T00:00:00Z" \
  -H "Authorization: Bearer $TT_KEY" | jq
```

Requires the `graphs:read` scope. Graph-bound keys are refused with 403
`GRAPH_SCOPED_TEAM_SURFACE` (the analytics leg is org-wide, so a graph-scoped
credential cannot be served honestly). Default window 24h, max 90d; malformed,
naive (no timezone), non-positive, or over-long windows are 422.

### Cohort roll-up

The free tier allows one org per person with no invites, so **each tester is
their own org** and a cohort is structurally multi-org. An org-scoped endpoint
alone reports n=1 — a dogfood read.

**This is not a success gate.** The owner withdrew the proposed "≥5 of 10" aha
bar and the minimum-N requirement on #3497 §7.4 (2026-09-15): *"there should eb
no number. stop creating bureocracy and focus on shipping and iterating with
feedback."* Beta exit is shipping-and-iterating. The tool reports a funnel over
whatever cohort the operator names — there is no N anyone must reach, and no
rate.

```bash
# Run with the PROJECT interpreter via uv (this package requires Python >= 3.12;
# the tool uses `zip(..., strict=True)`, so a system Python 3.9/3.8 fails).
uv run python tools/activation_cohort.py \
  --api-base https://api.premiselabs.co \
  --org "$ORG_A=$KEY_A" --org "$ORG_B=$KEY_B" \
  --since 2026-09-16T00:00:00Z --until 2026-09-17T00:00:00Z \
  --out /tmp/b7-cohort.json
```

The org list is **explicit and operator-supplied, never enumerated** — founder
and dogfood orgs are excluded by naming the cohort, not by a heuristic that
could silently redefine the number. Output always carries `cohort_definition`
and `dogfood_only`. A one-org run is flagged `dogfood_only: true` (the product's
own internal-gate vs public-gate vocabulary). An org that could not be read
makes its stage `partial` with the org named, or `unavailable` when no org
produced a measurable value at all — never silently summed as 0, and never
described as `not_measurable` (which means the org answered, and the answer was
"nothing to measure").

A cross-tenant admin cohort endpoint is possible but needs its own
tenant-isolation security review; it is filed separately, not smuggled here.

## Measured baseline (production, 2026-09-17)

Read off the live cohort via the production read endpoints
(`GET /v1/sessions` + `GET /v1/sessions/{id}`) and folded through this
scorecard's own `stage_counts()`:

| Window | captured | stored | memory_produced | turn points | extracted |
| --- | --- | --- | --- | --- | --- |
| 2026-09-01 → 10-01 | 50 | 50 | **0** | 633 | 0 |
| 2026-09-16 19:00 → 20:00 | 44 | 44 | **0** | 578 | 0 |
| 2026-09-17 → 09-18 | 0 | 0 | 0 | 0 | 0 |

All harness `pi`, all captured 2026-09-16. `memory_produced == 0` with
`state == "measured"` **is a real zero**: extraction is not structurally broken
(a real 15-turn Pi session through the v2 extractor with the real DeepSeek
provider produced 19 extracted points / 21 points locally), so this is an
environment/routing difference in production, not a dead code path. Tracked in
the capture-status issue.

## Deployment requirement

The endpoint and the analytics write-path repair are **not deployed** — the
production release still runs the pre-change commit. Reaching the lane's exit
evidence ("an activation scorecard is queryable in production for a real
cohort") needs exactly:

1. A release of `tortoise` including `tortoise/activation_scorecard.py`,
   `tools/activation_cohort.py`, and the `hosted_api.py` changes.
2. No new secret, and no migration — `analytics_events` already exists
   (`supabase/migrations/0004_analytics_events.sql`).
3. **After deploy, confirm the repair actually took effect**: the write path now
   accepts `SUPABASE_SERVICE_ROLE_KEY` (what Fly actually sets) as well as the
   legacy `SUPABASE_SERVICE_KEY`. Verify by observing `mcp_tool_call` rows land
   in Supabase `analytics_events` — before the repair every event was appended
   to `~/.tortoise/analytics_fallback.jsonl` on an ephemeral Fly VM and lost.

Until step 3 is observed, `recall_attempted` reads `unavailable`
(`analytics_write_path_unconfigured`) — the honest answer.

### Two ways a zero here can still mislead

**A configured writer is not a working writer — but check the direction.** The
pre-check is a *config* probe (URL + a service key), not a liveness probe. Note
that this read and the telemetry **write** use the *same* credential in the
*same* process, so a rotated/revoked key fails the read too and surfaces
honestly as `unavailable` (`analytics_store_unreachable`) — **not** as a false
zero. A false `measured 0` needs a failure that rejects the WRITE while letting
the READ succeed: an INSERT-only RLS denial, a partial/limited role, or a silent
PostgREST drop. Detectability is #3677.

**A window predating the repair cannot be told apart from a window with no
events.** Every event written before the repair is gone (ephemeral disk), and
nothing records when the repair landed, so a pre-deploy window reports
`measured 0`. Reconcile against the deploy time before citing a zero that
straddles it. This is in the payload's `limitations` too.

## Known limitations

Carried in the payload's `limitations` list — read it before citing a number.
`recall_attempted` cannot distinguish an answered query from an empty one; it
cannot be attributed to a session or a graph; the analytics read is capped at
1000 rows (a truncated page reports `unavailable`, never a partial count);
`value_confirmed` is not measurable; and `memory_produced` is
extraction-succeeded, not memory-that-helped.
