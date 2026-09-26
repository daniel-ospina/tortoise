---
title: "ADR-011: Resolution Authority for DR Alerts Is Evidence-Gated"
type: decisions
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-backups, tortoise-dr-alerts, alert-store
created: 2026-09-12
---

# ADR-011: Resolution Authority for DR Alerts Is Evidence-Gated

**Status:** Accepted (2026-09-12 — decided during the #2844 fix; supersedes the
implicit "whoever polls first clears it" behaviour)
**Date:** 2026-09-12
**Issues:** #2844, #3127
**Owner:** epistemic-team

## Context

Two writers file DR incidents into one R2-backed dedup store
(`ops/alerts/{KIND}/{subject}.json`):

- the **driver** (`.github/scripts/registry-cron.sh`, hourly) — probes R2 with
  its own `head-bucket` preflight, reads `/status.storage_error`, measures the
  pool with `list-objects-v2`, and reads the watcher heartbeat;
- the **watcher** (`tortoise/backup_watcher.py`, in-process) — probes R2
  reachability and computes per-team/per-graph archive freshness.

`#2844` fixed a real dedup defect: the two writers spelled a subject-less
sentinel differently (`_.json` vs `global.json`), so one condition had **two
create-once points** and two issues, and an asymmetric resolve stranded the
other spelling holding a closed issue's number. The chosen fix was design (A):
unify the write key on `_` and keep `global` as a legacy alias that every read
and delete consults.

That unification removed the duplicate-filing defect **and** introduced a worse
one, found in code review. While each writer could only delete its own spelling,
a writer's all-clear affected only its own record. Sharing one object made the
all-clear authoritative for the *condition* — and the two writers do not have
the same evidence.

The runbook already encoded the evidence rule for one kind
(`docs/ops/registry-backup-dr.md`, "Self-heal is evidence-tiered", hardened by
the #2796 review rounds R1/R3 and #2411):

> `R2_DOWN` clears **only** when the driver's own `head-bucket` preflight
> succeeded, `storage_error` is clear **and** the pool was measured this run.

`grep storage_error tortoise/backup_watcher.py` returns **0 hits**. The watcher
has no access to that evidence, so with one shared object its healthy poll would
close the driver's `R2_DOWN` and delete the driver's sentinel. The driver would
then see no open issue and file a fresh one — **one duplicate issue + Telegram
pair per hour** — while a genuine driver-side storage fault (the documented
`R2_LIST_OK=0` class: the bucket is reachable but the key cannot list it, which
the watcher's probe never tests) was recorded as **resolved**.

Two further defects in the same area, both about believing a sentinel more than
the evidence allows:

- **Liveness was inferred from the sentinel, not the issue.** Adoption was gated
  on `issue_number` truthiness, so a stranded sentinel naming a **closed** issue
  made `open_incident` a permanent no-op: a live fault that pages once and never
  again. Pre-fix `main` re-filed here, so this was a regression on the
  driver-disabled leg — the one leg where nobody else can repair it.
- **Backfilling wrote the adopted legacy spelling**, leaving the canonical key
  free for the other writer's conditional PUT to succeed on — two create-once
  points again, precisely in the create-then-die window the store exists to
  recover.

## Decision

**Identity is unified; authority is not.** Three rules:

1. **One object per (kind, subject)** stays — it is the single linearization
   point that makes one condition produce one issue. `global` remains a
   read/delete legacy alias; `_` is the only write spelling.
2. **Resolution authority is evidence-gated.** For each kind exactly one writer's
   probes cover its recovery condition, and only that writer may declare it
   recovered. Anything else is refused and logged with the sentinel left intact.
   The mapping lives in `KIND_OWNERS` (`tortoise/alert_store.py`), is mirrored
   by `kind_owner()` (`.github/scripts/registry-cron.sh`), and is pinned across
   the language boundary by `test_kind_owner_contract_with_driver`. Each
   sentinel records the `writer` that filed it for diagnosis only — the field is
   never an authority token, and a legacy object with no `writer` field resolves
   by the same `KIND_OWNERS` rule.
3. **Liveness comes from the issue, not the sentinel.** A sentinel is trusted
   only while the issue it names is still open, read by state
   (`github_issue.issue_is_open_checked`), never inferred from a search result.
   Refusing to trust a sentinel requires positive evidence of closure: a failed
   state read, or an unwired reader, counts as open. A 404/410 is positive
   evidence of closure and re-files.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Keep two sentinels, cross-check on read** (the pre-#2844 shape, symmetric) | Two keys can never be one linearization point: two writers failing at the same instant still produce two issues. It fixes the regressions by giving up the duplicate-fix, and `#2844` exists precisely because duplicates were the problem. |
| **Designate one global writer** (e.g. the app's watcher reconciles all incidents) | Viable only if the app is up. The incidents that matter most — `APP_DOWN`, `WATCHER_DOWN`, and the driver's own `R2_DOWN` — are filed *because* the app is unreachable, so the driver must be able to open incidents independently. |
| **Guard by provenance only** ("you may only close what you opened") | Rejected after three review rounds each found a way the self-asserted note failed: it was stamped by an adopter that never filed the issue, it survived a placeholder that was never filed, and it outlived the issue it described. A field the caller writes cannot decide authority — each defect let a non-owner clear a kind it had no evidence about. |
| **Split `R2_DOWN` into two kinds** (unreachable vs. unlistable) | The cleanest *long-term* shape — one kind should correspond to one recovery condition — but it changes the runbook triage table, issue titles, suppression config and the driver's filing sites. Deferred; the ownership guard makes one kind with two failure modes safe in the meantime. |
| **Require N consecutive healthy polls before closing** (recovery thresholds / hysteresis) | Standard practice and the right anti-flap measure, but orthogonal: it does not stop a probe clearing a condition it never tested. Deferred to its own issue. |

## Consequences

**Positive**

- A fault can no longer be marked recovered by a probe that does not cover it,
  so a genuine driver-side storage fault stays open until driver-side evidence
  clears it.
- No hourly close/reopen churn from the shared object.
- A sentinel naming a closed issue no longer swallows a recurrence.
- The ownership policy is one declarative map, pinned by a test in both
  languages, so the two writers cannot silently drift.

**Negative / accepted**

- **Driver-disabled stranding is an accepted cost.** With authority decided by
  `KIND_OWNERS` alone, the watcher cannot clear an `R2_DOWN` — even one it
  filed — while the driver is disabled or dead, so the incident stays open with
  no writer able to close it. That is correct rather than a defect: if the
  driver is off, the evidence that storage recovered (its own `head-bucket`
  preflight, a clear `storage_error`, and a measured pool) does not exist, so
  the incident **should** stay open. The driver's next healthy run closes it,
  and a stale sentinel is dropped on the next open attempt.
- Kinds that are genuinely contested remain one kind with two failure modes,
  which the ownership map papers over rather than models. Tracked for the
  split.
- Unlisted kinds (`SIZE_GUARD_ABORT`, `DATA_LOSS_CANDIDATE`, `abuse_suspended`)
  are unguarded — authority was never contested there, and inventing an owner
  for them would be a guess rather than a decision.
- An incident whose kind's owner never runs again stays open until it does (or
  a human closes it) — the same accepted cost, on the leg where the owner is
  gone rather than merely disabled. Tracked with the kind split in #3147.
- A **persistent** non-404 failure of the issue-state read (revoked token, 403,
  sustained 5xx) still counts as "open", so a sentinel naming a closed issue is
  trusted for as long as the failure lasts. A blip must not re-file; a permanent
  failure must not be silent. Counting consecutive failures and escalating is
  not implemented here.

## Evidence base

Recovery must be validated by a probe that covers the failing dependency, not
merely by the condition ceasing to look true:

- Datadog, *Recovery thresholds* — recovery is an explicit hysteresis condition,
  not "the alert condition stopped being true".
- Datadog, *Synthetics alerting* — a recovery notification is distinct from the
  alert condition clearing; unexpected recoveries warrant checking scope/retries.
- OneUptime, *Alert on monitor state changes* — acknowledging an alert is not
  resolving it; monitors need separate open/recover thresholds.
- InfraBeacon, *Reducing uptime monitoring false positives* — recovery should
  mean stability (consecutive successes), not one lucky response.
- AtomPing, *Reducing false alarms* — quorum confirmation distinguishes probe
  failure from target failure "especially when a probe could mark an incident
  resolved without truly validating the failing dependency".
- Rootly, *Alert management best practices* — every production alert has exactly
  one accountable owner; Rootly/SRESchool — dedup policies must define ownership
  explicitly and must preserve it.
- Single-writer principle (rifty.ai glossary; Bernd Rücker; Marko Živković) —
  one active writing authority per mutable state domain; others publish
  observations or proposals and do not mutate governed state directly.
