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
| `recall_returned` | Of those calls, the ones whose `result_count` ≥ 1 — i.e. the retrieval **handed back** at least one item. Emitted by the MCP dispatch seam (#3675) on the narrow retrieval allowlist. A retrieval row with no `result_count` (pre-deploy) or an unreadable one refuses the stage rather than counting as “returned nothing” | **org-wide, event count** |
| `value_confirmed` | **Not measurable** — a refusal, for a narrower reason since #3675 | — |

### Two traps this is built to avoid

**A zero is only meaningful when paired with `state == "measured"`.** Every
stage carries `state` and a `reason`. An empty store and an unreachable store
both look like 0; only one of them is a fact. Never read a bare number.

The two non-measured states are **not** interchangeable:

| State | Means | Examples |
| --- | --- | --- |
| `unavailable` | the metric exists but could not be read *now* — a retry or a repair fixes it | `analytics_store_unreachable`, `analytics_write_path_unconfigured`, `analytics_page_cap_truncated` (the walk could not be certified COMPLETE — see below), `window_predicate_not_applied`, `unparseable_created_at`, `first_memory_at_missing`, `unparseable_analytic_rows`, `unclassifiable_analytic_rows`, `result_count_not_recorded` (a window predating #3675's deploy), `result_count_unreadable` (a retrieval whose result carried no item collection) |
| `not_measurable` | there is nothing to measure, and no retry changes that | `no_memory_produced_in_lifetime` (nothing to recall from); `value_confirmed` (the surviving gap is whether a returned item was *used*, which no surface records) |

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
"ok"` means only that the tool did not raise.

**`recall_returned` is a RETURN, not an answer.** Since #3675 the
`mcp_tool_call` props carry `result_count` — the number of items the call
returned, or `null` when the result shape makes the count unknowable — so the
attempt/answer split IS measured. It is an **upper bound** on “answered from
memory”: it says the retrieval surface handed back candidates, never that a
candidate was relevant, read or used.

Two things make stage 5 refuse rather than report a count (the count would be a
lower bound presented as exact): a retrieval row with **no** `result_count` at
all (a window predating the prop's deploy — the emitter writes the key
unconditionally, so absence means the row is older than #3675), and one whose
`result_count` is present but not a count (`result_count_unreadable`: a call
that failed inside `_safe`, an unrecognised recall mode, or a result shape that
no longer matches the three measured ones). The counters
`detail.rows_without_result_count` and `detail.rows_uncountable_result_count`
name which fired.

Stage 5 uses the **narrow** retrieval allowlist only, where stage 4 reports both.
The wide set adds known-id reads (`tortoise_get_point` et al) whose result is a
single object with no item collection — uncountable by construction — so folding
them in would refuse the stage for any org that ever read a point by id.

The paired `abstained` prop #3675’s body names was **not** shipped: its only
producer was the `POST /v1/ask` abstention path, retired with `tortoise_ask`
(#3849/#3929), so the key would have had no producer at all. No `activated`
field and no activation rate is emitted anywhere, and
`tools/activation_cohort.py` fails loudly if a payload ever grows one.

**`value_confirmed` remains a refusal** — and a narrower one. Nothing recorded
says whether a returned candidate was *used*, and the two historical blockers
it used to name (#3518/#3519) are closed, as is the missing-instrument one.

Note the legs mix granularity: stages 1–3 are per-graph session counts, stages
4–5 are org-wide event counts with no `graph_id` (`analytics_events` has none)
and no `session_id`. This is an activation **funnel** view, not a per-session
claim.

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
feedback."* Beta exit is shipping-and-iterating. The surface reports a funnel
over whatever cohort the operator names — there is no N anyone must reach, and
no rate.

`GET /v1/internal/activation/cohort` (#3676) is the served roll-up. It takes an
**explicit** org list and returns the same six-stage shape folded across the
named orgs.

```bash
# The operator/internal credential — NOT a tenant `tt_` key.
export BASE=https://tortoise-y4mjjq.fly.dev
curl -sG "$BASE/v1/internal/activation/cohort" \
  -H "Authorization: Bearer $FASTAPI_INTERNAL_KEY" \
  --data-urlencode "orgs=org_alpha" --data-urlencode "orgs=org_beta" \
  --data-urlencode "since=2026-09-16T00:00:00Z" \
  --data-urlencode "until=2026-09-17T00:00:00Z" | jq
```

The committed client is preferred over the curl above: it refuses a plain-http
base, never follows a redirect (CPython's redirect handler copies request
headers, so a redirect would hand the internal key to whatever host answered),
and never lets the credential reach an error message.

```bash
# Run with the PROJECT interpreter via uv (this package requires Python >= 3.12;
# the tool uses `zip(..., strict=True)`, so a system Python 3.9/3.8 fails).
uv run python tools/activation_cohort.py \
  --api-base "$BASE" --org org_alpha --org org_beta \
  --since 2026-09-16T00:00:00Z --until 2026-09-17T00:00:00Z \
  --out /tmp/b7-cohort.json
```

The org list is **explicit and operator-supplied, never enumerated** — founder
and dogfood orgs are excluded by naming the cohort, not by a heuristic that
could silently redefine the number. Output always carries `cohort_definition`
and `dogfood_only`. A one-org run is flagged `dogfood_only: true` (the product's
own internal-gate vs public-gate vocabulary). An org that could not be read
makes its stage `partial` with the org named (and the reason in `org_errors`),
or `unavailable` when no org produced a measurable value at all — never
silently summed as 0, and never described as `not_measurable` (which means the
org answered, and the answer was "nothing to measure").

## Tenancy review — the cross-tenant cohort endpoint (#3676)

A cross-org read is a genuine tenant-boundary change, so the decisions below are
recorded rather than assumed.

| Question | Decision |
| --- | --- |
| **Auth** | The internal MACHINE plane (`FASTAPI_INTERNAL_KEY`, `_check_internal`). ADR-010 splits credentials into HUMAN (session JWT) and MACHINE (durable key/agent identity); an operator is the machine plane, and there is no admin *role* in the human plane to reuse. An org-scoped `tt_` key can never satisfy `_check_internal`, so the separation is structural (pinned by a test). |
| **Why not a new admin secret** | `FASTAPI_INTERNAL_KEY` already gates `/v1/internal/backups/restore`, `/purge` and `/sweep` — operations that read and REWRITE every tenant's graph. A read-only count over an operator-named list adds no capability to the holder, while a second long-lived master credential would widen the blast radius of a leak and need its own rotation path. |
| **Plane / prefix** | `GET /v1/internal/activation/cohort`. `/v1/internal/` is the documented operator/cron prefix and carries the recorded #3834 transport-wait-bound exemption (these routes "have no user and no user-patience budget to derive from"); a cohort of N orgs is exactly that class of batch job, so it inherits the ruling instead of widening it. A `/v1/admin/` prefix would have needed a new exemption decision and a new path family to secure. |
| **Enumeration** | The list is explicit; there is no wildcard token (`all`/`any`/`every`/`*`/`%`/`-1` are refused by name), no registry lookup, and the response contains no org the caller did not name — pinned by a test that seeds an unrequested org and asserts its id appears nowhere at any depth. |
| **Duplicates** | Refused (422), never deduped: deduping hides a copy-paste error, summing both inflates the number and its denominator. |
| **Size** | At most 100 orgs per request — a blast-radius bound, **not** a cohort-size requirement. |
| **Blast radius** | Each org is read independently, bounded by a 30 s per-org timeout and a concurrency of 4. An unreadable, refused, or hung org costs THAT org's cells and is NAMED in `orgs_unavailable` + `org_errors`; a partial cohort whose sum would be `0` refuses the number (`partial_zero_from_an_unreadable_cohort`) because a lower bound is not a fact. |
| **Nonexistent org name** | The endpoint does **NOT** verify that an org exists (there is no registry lookup — see *Enumeration*). A well-formed name with no org behind it reads as an empty graph and is reported as a **measured `0`**, counted in `orgs_measured` with no `org_errors` entry — indistinguishable from an existing org with genuinely zero activity. An entirely-typo'd cohort therefore returns a confident `0`; verify the names against the org list before acting on a `0`. Adding an existence check would itself be a registry lookup, so it is a new decision rather than a silent addition (recorded in `COHORT_LIMITATIONS`). |
| **Audit** | ONE append-only `audit_events` row per request, `operation=activation_cohort_read`, `org_id=""` (the operator plane — the non-org-scoped shape `identity_link` uses), naming every target org in `detail`. One row, not N: the action is one operator action and the actor is the operator, and `audit_events.team_id` is what RLS makes tenant-visible — N rows would present one operator action as N tenant operations and put a row in every tenant's trail for a read of a *count*. Tenant-visible notification is a product/privacy decision with its own RLS answer and is not invented here; the backup sweep reads every tenant's full graph and emits no audit rows at all. The write is FAIL-OPEN (#1529: bookkeeping must not 500 a serving path) — an abuser holds the key, so blocking the read while the sink is down would not stop them. |
| **Attribution** | No actor is bound for a cohort read: the operator is not the tenant's actor. |

`tools/activation_cohort.py` is now a thin, stdlib-only client of this endpoint
(it previously drove the org-scoped endpoint once per org with **one tenant API
key per org on the command line** — keys in `argv`/history, a second roll-up
implementation, and no durable record of the read). The roll-up itself lives in
`tortoise/activation_scorecard.roll_up_cohort`, so the org-scoped scorecard, the
cohort endpoint and the client cannot disagree about the same org.

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
`recall_attempted` cannot distinguish an answered query from an empty one on its
own (that is what `recall_returned` adds) and neither can be attributed to a
session or a graph; `recall_returned` is an upper bound — a returned candidate is
not a used one; `value_confirmed` is not measurable; and `memory_produced` is
extraction-succeeded, not memory-that-helped.

The analytics read is **keyset-paged to completion** on `id` (#4038), up to
`recall_page_cap × recall_page_limit` rows (1000 × 100) per window. A walk that
cannot be certified COMPLETE reports `unavailable` — `analytics_page_cap_truncated`
when the bound was reached, the store ignored `order`, or it would not advance
past the cursor, and `analytics_store_unreachable` when it failed mid-walk —
never a partial count. Both `recall_attempted` and `recall_returned` inherit that
ladder, so neither can be a lower bound published as exact.
