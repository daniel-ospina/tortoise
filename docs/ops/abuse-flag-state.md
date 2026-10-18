---
title: "Reading why an org is flagged — the abuse state read (#4874)"
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-hosted-api, abuse-engine
aboutObjects: tortoise, supabase/migrations/0015_abuse_events.sql
created: 2026-10-18
updated: 2026-10-18
---

# Reading why an org is flagged — the abuse state read (#4874)

An org reads `"status": "flagged"` on `GET /v1/team`. That status is **derived
from one column** — `flagged_at IS NOT NULL` — and carries no reason. The reason
(the rule that fired, the count that breached it, the threshold and the staging
window) IS recorded: `AbuseEngine._flag` → `AbuseStore.flag_org` writes it
into `abuse_events.details`
(`{rule, count, threshold, window_s}`). Until #4874 the only way to read it
back was a hand-written query against `abuse_events`.

## The read

```bash
curl -sS "$API/v1/internal/abuse/state?org_id=<org-id>" \
  -H "Authorization: Bearer $FASTAPI_INTERNAL_KEY" \
  --max-time 10 | jq
```

```json
{
  "org_id": "org-…",
  "status": "flagged",
  "flagged_at": "2026-09-23T08:30:35+00:00",
  "suspended": false,
  "org_found": true,
  "flags": [
    {"rule": "point_create", "count": 612, "threshold": 500,
     "window_s": 3600, "since": "2026-09-23T08:30:35+00:00"}
  ],
  "org_state_error": null,
  "flags_error": null
}
```

* `status` / `flagged_at` / `suspended` come from the **same source**
  `GET /v1/team` reads — the `organizations` row in Supabase mode, the `Team`
  node props (`flagged_at` / `suspended_at`) in registry/selfhost mode — and
  `status` applies the same derivation (`flagged_at IS NOT NULL`). Sharing the
  source, not just the expression, is what makes the two unable to disagree
  about the chip: the in-process store dicts are per-process and empty after a
  restart, so a read built on them would answer `active` for an org whose chip
  says `flagged`.
* `status` is `flagged` / `active`, or `unknown_org` when **no such org
  exists** — see "Reading a failure correctly". `org_found` carries the same
  fact as a boolean (`null` only for a bare in-process store, which cannot say).
* `flags` lists the **active** episodes, newest first. "Active" is the
  enforcement path's own rule (`latest_flag_at`): the newest `flag` row for a
  rule, unless a `flag_clear` lands at or after it. A test pins the two
  definitions together.
* `count` / `threshold` / `window_s` are the values **recorded at flag time**.
  A flag row written before those fields existed reports `null` — the window it
  originally breached is not recoverable later, and a recomputed number would
  be worse than none.
* `suspended` is the store's own durable read in both modes.
  Registry/selfhost degradation: the **episode rows** are in-process, so after
  a restart `flags` can be empty while `status` still (correctly) reads
  `flagged` from the durable prop. The durable store is authoritative; an
  empty `flags` beside `status: "flagged"` means the reason is not readable
  from that process, never that there is no reason.

## Why this is operator-only

`point_create` / `key_create` are our internal vocabulary and are deliberately
absent from every **customer** surface. `GET /v1/team/alerts` returns a
customer-facing message with no rule token for exactly this reason (see
`abuse._alert_dict`) — a human operator needs the rule; the customer is told
what happened. That is why #4874 added this read here rather than widening
`/v1/team`.

Auth is `_check_internal` (`FASTAPI_INTERNAL_KEY`) — an org-scoped `tt_` key
cannot satisfy it, and an unconfigured key answers **503**, never open.

## Reading a failure correctly

A failed read is never rendered as "nothing is flagged":

* store error → `flags: null` **and** `flags_error` set;
* org-state error → `status: null` and `org_state_error` set;
* **no such org** → `status: "unknown_org"` and `org_found: false` (neither
  an error nor "clean"). `org_id` is supplied by hand on this route, so a typo
  is a real case: it used to come back as `status: "active"`, `flags: []`,
  both errors null — a confident clean bill of health for an org that does not
  exist.

No active episode is `flags: []` with no error. Keep those apart: an
operator who reads "no reason" where the truth is "could not read" is the exact
hour this endpoint exists to save.

## What a flag does and does not do

`flagged_at` enforces **nothing**. Refusal keys on `suspended_at` only (403 /
MCP `-32006`). A flag is: the stage-1 clock the stage-2 review alert anchors
against, the `status` chip, and one operator alert. It is cleared by a clean
window (`flag_clear`) or an un-suspend — nothing else, and for the durable
store that last path is the `abuse_unsuspend` SQL function, so
`abuse.FLAGGING_RULES` and that function's rule list must be kept in step.
The function is defined more than once (supabase applies migrations in
filename order, so the LAST `CREATE OR REPLACE` is what runs): add the rule to
a FORWARD migration that redefines `abuse_unsuspend`, never by editing one
already on `main`. Guarded, in both directions, by
`tests/test_abuse.py::test_durable_unsuspend_clears_every_flagging_rule`
(which reads the newest definition) and
`::test_the_newest_definition_is_the_one_guarded`.
