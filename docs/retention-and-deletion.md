---
title: "Retention and Deletion — the one promise"
type: operations
domain: platform
doc_status: live
created: 2026-09-18
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-cloud
related:
  - issue: 4179
    note: "Canonical source of truth for the retention/deletion promise (owner ruling 2026-09-18)."
  - issue: 2304
    note: "Graph delete = trash: quarantine → 7-day grace → erasure."
  - issue: 302
    note: "Team-account soft delete → grace → hard purge."
---

# Retention and Deletion — the one promise

> **This is the canonical document.** If you need to state a retention,
> deletion, or restore window anywhere — code, copy, a runbook, a legal page —
> **link here and do not restate the number.** The only exceptions are the
> implementation constants named below. Three copies of "7" is how the windows
> drifted apart; this document exists so they cannot drift again.

## The promise

Deleting a **user account**, a **graph**, or a **team account** is reversible
for a **7-day restore window**. After that, the deleted thing is purged.

> **Delete = gone from your view immediately · restorable for 7 days · no copy
> remains after about four weeks.** *(Owner, 2026-09-18.)*

The horizon is **path-dependent**, and the difference matters:

| Path / copy | Deletion horizon | Where it lives in code |
|-------------|------------------|------------------------|
| **Graph** deletion — the undo window | **7 days** | `tortoise/backup_sweep.py` `_GRAPH_PURGE_GRACE_DAYS` → `retention.RESTORE_WINDOW_DAYS` |
| **Graph** deletion — the graph's own backup pool | erased **at the 7-day purge, best-effort per artifact family** — a residual is **not retried** (see *Accepted gaps the promise carries*) | `tortoise/backup_sweep.py` `_purge_graph_storage` |
| **Live**-data snapshots (a backup horizon, **not** an undo) | ≈ **28 days** (24 hourly + 7 daily + 4 weekly) | `tortoise/backup_config.py` `retention_hourly/daily/weekly` |
| **Team-account** deletion — backup artifacts | **not yet erased** — see *Open gaps* below | `tortoise/hosted_api.py` `_purge_deleted_orgs` |
| **Second-region mirror** (shipped; `BACKUP_MIRROR_ENABLED` default **off**) | deletion **not propagated** (append-only) | `docs/ops/registry-backup-dr.md` |

> **Restore window ≠ retention window.** R1 forbids retaining user **content**
> for any length. The 7 days are an **undo** offer, never a claim that we keep
> content. Do not conflate the two in copy or in code.

### Accepted gaps the promise carries

Two consequences of the code below the promise are **owner-accepted, not copy
claims**: they are recorded here, and **must not be re-disclosed in customer
copy** (see *The end-destination rule* below).

- **The graph backup purge is best-effort and is not retried.** Deleting a graph
  drops its namespace and purges its own backup pool, but `_purge_graph_storage`
  works **per artifact family**, collects the failures, and never aborts the
  purge — and the tombstone row is stamped **regardless**, so a residual artifact
  (a failed `storage.delete`, or an unreadable legacy-flat index) is left behind
  with no automatic follow-up (`tortoise/backup_sweep.py:1473-1510`, row stamped
  at `:1620-1622`). The end destination is still erasure; the residual is what we
  repair toward, not a promise we make differently.
- **Per-graph vs per-entity.** Deletion is scoped to a **graph** (or an
  account) — there is **no separate purge for an entity** (a single point)
  removed inside a **live** graph. Such a removal is an ordinary live-graph
  write; the only copies that still hold it are the live-data snapshots on the
  24/7/4 cycle, which age out on the ≈28-day horizon. **That is why the
  four-week horizon matters:** for an entity deleted inside a live graph, the
  backup copy — not a per-entity purge — is what bounds the residual.

## The authoritative implementation constants

These are the only places a window may be stated numerically without linking
this document:

| Constant | Role |
|----------|------|
| `tortoise/retention.py` `RESTORE_WINDOW_DAYS` / `RESTORE_WINDOW_HOURS` | **Sole authority for the restore window** |
| `tortoise/backup_config.py` `retention_hourly` / `retention_daily` / `retention_weekly` | **Sole authority for the backup-hold counts** (a different concept from the restore window) |
| `tortoise/backup_config.py` `LOCK_DAYS_MAX` | the bucket-lock bound; must stay strictly below the restore window (`RESTORE_WINDOW_DAYS - 1`) |

Everything else is a **derived alias**, never independently set:

- `tortoise/backup_sweep.py` `_GRAPH_PURGE_GRACE_DAYS`
- `tortoise/hosted_api.py` `_TRASH_GRACE_DAYS` (imports `_GRAPH_PURGE_GRACE_DAYS`)
- `tortoise/hosted_api.py` `TEAM_DELETE_GRACE_HOURS`
- `tortoise/hosted_api.py` `USER_ACCOUNT_DELETE_GRACE_HOURS`
- the frontend `website/apps/dashboard/src/graphs.js` `TRASH_GRACE_DAYS`
  (bound to the authority by `tests/test_retention_promise.py`)

## The three deletion paths

| Path | Access ends | Restorable for | Restore mechanism |
|------|-------------|----------------|-------------------|
| **Graph** | immediately (API keys revoked) | 7 days | self-service Trash in the dashboard |
| **Team account** | immediately (keys + memberships revoked) | 7 days | support / operational (no self-service surface yet) |
| **User account** | immediately (on deletion request) | 7 days | support (email request — privacy §16) |

The **user-account** path has no in-product self-service deletion today:
privacy §16 says so, and the blocker for a surface is the missing Supabase
auth-admin deletion wiring — not the deferred permissions model (D31).
`USER_ACCOUNT_DELETE_GRACE_HOURS` records the promised window for that support
path.

## A different axis: the operational event store

`TORTOISE_EVENT_RETENTION_DAYS` (default 30) in `tortoise/sdk.py` is the
`:GraphEvent` **operational log** retention (see `docs/event-catalog.md`). It is
**not** user content and **not** a deletion promise, so it is exempt from the
"link or be a named constant" rule.

## A different axis: OAuth credential hygiene

`tortoise/oauth.py` `OAUTH_ACCESS_RETENTION_S` / `OAUTH_REFRESH_RETENTION_S` /
`OAUTH_CODE_RETENTION_S` (each 86400s by default; env-overridable as
`TORTOISE_OAUTH_{ACCESS,REFRESH,CODE}_RETENTION_S`) is the grace kept after a
row's own `expires_at` before the scheduled OAuth sweep hard-deletes it
(`sweep_oauth_retention`, wired into the `hosted_api` sweep runner). These rows
are service-role-only SHA-256 hashes — never user content, never plaintext — so
this is **credential hygiene**, a different axis from the deletion promise above,
and it is exempt from the "link or be a named constant" rule by the same logic
as the operational event store. The control-plane audit trail lives in
`audit_events`; no deletion here destroys an audit record.

## Open gaps and open decisions

- **Team-account cascade (D5 — follow-up).** Deleting a team account today
  revokes access and drops the **default** graph's namespace, but it does **not**
  erase custom-graph namespaces, and `prune_backups` explicitly never touches
  nested per-graph pools — so the nested backups of **every** graph in the org
  (**default included**) survive indefinitely. The owner ruled the team account
  "should delete everything inside"; that widening is a separate, destructive
  change filed as **#4190** — which the end-destination ruling below makes
  **promise-blocking**, not merely a gap.
- **Backups on a delete request (D6 — owner decision).** On a support/account
  delete request, does live deletion actively purge backup snapshots, or do they
  age out on the 24/7/4 cycle? And does the deletion propagate to the
  second-region mirror (today it never does)? **Not decided.** The public wording
  below is written to be true under the current default (mirror off).

## The end-destination rule — the team-account promise is a commitment

The owner ruled (2026-09-18) that customer copy states the **end destination**,
not the gaps: *"don't worry about those promises, we'll deliver soon on them or
when someone asks we do it... focus on end-destination."* The published promise
is therefore a **commitment about where deletion ends**, not a description of
what the code does today.

For a **team account** the published promise is: restorable for 7 days, then
**permanent erasure including its backup copies**. **#4190 is consequently a
promise-blocking item, not "a gap found"** — deleting a team account today
drops only the default graph namespace, and custom namespaces plus every org
backup pool survive indefinitely. The code must be made to perform the erasure
the copy promises; **#4190 is not optional and must not be read as such.**

The team-account carve-out was deliberately removed from the customer copy and
must not be re-added.

## Where this promise is stated

The anti-scatter scan (`tests/test_retention_promise.py::test_no_unlinked_retention_claims`)
fails on any new retention/deletion claim that neither links here nor is a named
constant; `test_surveyed_files_link_to_canonical_doc` guards the link set itself.
The coverage is:

**Checked by the link gate** — exactly `LINKED_FILES` in
`tests/test_retention_promise.py` — `docs/00_index.md` · `docs/data-safety.md` ·
`docs/infra-runbook.md` · `docs/ops/registry-backup-dr.md` ·
`docs/registry-graph-schema.md` · `docs/scoping-2304-delete-semantics.md` ·
`docs/scoping-2313-per-graph-backups.md` · `tortoise/hosted_backup.py` ·
`website/apps/dashboard/src/graphs.js` · `website/apps/dashboard/src/main.jsx`.

`website/privacy.html` and `website/dpa.html` link here as well, but are gated by
the owner-gated privacy test rather than by `LINKED_FILES`; this is a link gate,
not a whitelist — a file outside the set above may still link here.

**Named implementation constants** (exempt by design): `tortoise/retention.py` and
`tortoise/backup_config.py` are exempt whole-file (every line is the authority);
`tortoise/{backup_sweep,hosted_api,sdk,supabase_control}.py` are exempt only on
lines that name a canonical constant. `website/apps/dashboard/src/graphs.test.js`
is derived: it imports `TRASH_GRACE_DAYS` from `graphs.js` rather than restating
the number.

**Point-in-time records — exempt, not gated** (they state the window as it was;
this document supersedes them): `docs/plans/2026-09-06-2304-delete-trash-can.md` ·
`docs/prototypes/2304-trash-ui.md` ·
`docs/research/2026-09-06-backup-dr-best-practices.md` ·
`docs/scoping-432-subscriptions-claim-lifecycle.md`.

**Different axis — exempt, not gated:** `docs/event-catalog.md` (the 30-day
`TORTOISE_EVENT_RETENTION_DAYS` operational event log, not a deletion promise).

## Public wording — §6 APPROVED (2026-09-18); Deletion-scope/DPA pending

> **Privacy §6 is OWNER-APPROVED.** The owner ruled (2026-09-18): *"Delete =
> gone from your view immediately · restorable for 7 days · no copy remains
> after about four weeks."* The approved §6 bullets are quoted below and are now
> applied to `website/privacy.html` and its mirror
> `docs/drafts/2026-08-08-657-privacy-draft.md`. The §"Deletion scope" and
> `website/dpa.html` §11 sentences are aligned to the same one-number style and
> are **pending the owner's confirmation**.
>
> **Division of labour.** The policy copy states **one number** (four weeks)
> plus the single graph exception. The 24-hour / 7-day / 4-weekly rotation, the
> per-graph-vs-entity distinction, the best-effort purge caveat, the mirror
> note, and the open backups-on-a-delete decision live in the sections above.
> The doc explains; the policy states. Neither may contradict the other.

**Privacy §6 — the approved list items (applied):**

> **Memory graphs.** Deleting a memory graph removes it from your view
> immediately and revokes its API keys. It stays restorable from the
> organization's "Trash" for 7 days. After that it is permanently erased,
> together with its backup copies.

> **Backups.** Our backups cover the last four weeks. A backup taken while your
> data was live can therefore still contain it for up to four weeks. A deleted
> memory graph is not in that category — its own backups are erased when its
> 7-day window ends.

**Privacy §"Deletion scope" — the aligned closing sentence (pending confirmation):**

> A deleted memory graph, user account, or team account remains restorable for
> 7 days before permanent erasure; §6 covers how a deleted memory graph is
> erased, and the
> [retention and deletion policy](https://github.com/daniel-ospina/tortoise/blob/main/docs/retention-and-deletion.md)
> is the single source of truth for these windows.

**DPA §11 — the aligned one-number form (pending confirmation):**

> … data in backups retained for up to four weeks — our backups cover the last
> four weeks — to maintain integrity and not used for any other purpose.
