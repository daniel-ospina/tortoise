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
  - issue: 4029
    note: "Owner ruling 1B + 2B (2026-09-30): a solely-owned team is deleted with the account; an account's backup copies age out on the ~28-day cycle rather than being purged at 7 days."
  - issue: 2304
    note: "Graph delete = trash: quarantine → 7-day grace → erasure."
  - issue: 302
    note: "Team-account soft delete → grace → hard purge."
  - issue: 3925
    note: "Owner ruling: account memory is the ACCOUNT's property — a person's erasure deletes the identity layer and severs the analytics linkage, and never reaches into the account's memory. The account-side PII filter / selective delete is DEFERRED (not prioritised); this document carries the boundary so a well-meaning adjacent change cannot quietly reverse it."
  - issue: 8032
    note: "Auth-session-store GC grace registered 2026-10-12: the D1 `sessions` table keeps a dead row for a grace past its own `expires_at` before deleting it. An implementation window for an internal credential store — registered here because this document is where a window may be stated, but it is NOT one of the deletion promise's windows and changes no customer-facing wording."
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

After the 7 days the **live** data is purged. The 7 days are not, by themselves,
a claim that no copy survives: for a **user account**, its backup copies are
**not** actively purged and age out on the ≈28-day cycle (owner ruling **2B**,
2026-09-30 — see the table and *Owner decisions* below).

The horizon is **path-dependent**, and the difference matters:

| Path / copy | Deletion horizon | Where it lives in code |
|-------------|------------------|------------------------|
| **Graph** deletion — the undo window | **7 days** | `tortoise/backup_sweep.py` `_GRAPH_PURGE_GRACE_DAYS` → `retention.RESTORE_WINDOW_DAYS` |
| **Graph** deletion — the graph's own backup pool | erased **at the 7-day purge, best-effort per artifact family** — a residual is **not retried** (see *Accepted gaps the promise carries*) | `tortoise/backup_sweep.py` `_purge_graph_storage` |
| **User-account** deletion — the account's own backup copies | **not actively erased** — they **age out on the ≈28-day cycle** (**owner ruling 2B**, 2026-09-30; no account-backup purge exists) | `tortoise/backup_config.py` `retention_hourly/daily/weekly`; the age-out prune is `prune_backups` (`tortoise/hosted_backup.py:2952`) |
| **Live**-data snapshots (a backup horizon, **not** an undo) | ≈ **28 days** (24 hourly + 7 daily + 4 weekly) | `tortoise/backup_config.py` `retention_hourly/daily/weekly` |
| **Team-account** deletion — backup artifacts | **not yet erased** — see *Open gaps* below | `tortoise/hosted_api.py` `_purge_deleted_orgs` |
| **Session recording** — the recording itself | **no expiry** — retained until deleted by a member of the organization (**owner ruling 2026-10-08**); a snapshot taken before the deletion retains it until it ages out on the ≈28-day cycle, so deletion is complete in the live service immediately and in backups within four weeks | `tortoise/hosted_api.py` `delete_session` |
| **Product-usage record** — the per-capture / per-team `capture_cost` rows in `analytics_events` (#3843) | **no default window** — the record is kept; on an account/team erasure the **identity layer is hard-deleted and the row is retained decoupled** (the table has **no FK** to `organizations`, so no cascade reaches it, and the `organizations` row carrying the name is deleted) | `tortoise/supabase_control.py` `purge_org_control_plane` — it deletes `api_keys` / `org_memberships` / `invitations` and then the `organizations` row via `purge_org_record`, and deliberately **never touches `analytics_events`** |
| **Second-region mirror** (shipped; `BACKUP_MIRROR_ENABLED` default **off**) | deletion **not propagated** (append-only) | `docs/ops/registry-backup-dr.md` |
| **Auth session store** — an expired row in the D1 `sessions` table (the opaque handle + its refresh token) | deleted **24 hours after its own `expires_at`**; a **revoked** row is never deleted early, so it survives the session's whole declared life (400 days, `SESSION_MAX_AGE_S`) | `website/apps/dashboard/functions/_shared/auth/session.ts` `SESSION_SWEEP_GRACE_S` |

The **auth session store** row is the store's own garbage collection, not a promise
window: it bounds how long a dead handle (and the credential it carries) stays readable
in D1. It says nothing about user content, and no customer copy states it — the store's
declared lifetime is a session's `expires_at` (400 days) plus this grace, and revocation
does not shorten it. See `docs/engineering/auth/d1-session-store.md` §5a.

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
| `website/apps/dashboard/functions/_shared/auth/session.ts` `SESSION_SWEEP_GRACE_S` | **Sole authority for the D1 auth-session-store grace** — how long a `sessions` row is kept past its own `expires_at` before `sweepExpiredSessions` deletes it (#8032). Not a deletion-promise window; see the table above. |

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
| **User account** | immediately (solely-owned teams cascaded, keys revoked) | 7 days | support / operational (no self-service restore surface yet) |

The **user-account** path is self-service **in-product** since **#4029**:
`DELETE /v1/user/account` cascades the teams the person ALONE owns through the
SAME cascade the team endpoint uses (owner ruling **1B**, 2026-09-30), stamps
the one window via `USER_ACCOUNT_DELETE_GRACE_HOURS`, and the boot + hourly
`_purge_deleted_accounts` erases the auth account once the **STORED** window
elapses (erasing the auth account cascades the person's memberships in every
org). The Supabase auth-admin deletion wiring that used to be the blocker for a
surface now exists; the deferred permissions model (D31) was never the blocker.
**Restore** within the window is support/operational — there is no self-service
restore surface yet, so the 7 days are an undo held for support, not a button.
**Backups are not actively deleted on this path** (owner ruling **2B**): they
age out on the existing ≈28-day cycle.

The 7 days in the **user-account** row is the *undo* window only. After it, the
account's **live** data is erased; the account's copies in the backup pool are
**not** actively deleted and instead age out on the ≈28-day 24/7/4 cycle (owner
ruling **2B**, 2026-09-30 — see *Owner decisions — the account-deletion path*
below). And deleting a personal account **deletes any team for which the
deleter is the only owner** (owner ruling **1B**, 2026-09-30): such a team is
**not** held for handover first.

## Owner decisions — the account-deletion path (2026-09-30)

Two owner decisions were ruled on **#4029**, both settling questions this
document had left open. They govern the **user-account** deletion path, which
**#4029** builds as an in-product self-service flow.

**1B — a solely-owned team is deleted with the account.** Deleting a personal
account **deletes any team for which that person is the only owner**; the team
is **not** held for handover. This is recorded as a conscious choice to delete
shared workspaces, not an oversight — so it is **not** to be re-litigated in
the build. Because it removes other people's data, the ruling pairs it with a
**confirmation step**: the delete control warns

> Deleting your personal account will also delete any teams for which you're the only owner

and offers **confirm** / **cancel**, with cancel a real exit — no partial
delete, no account left in an ambiguous state. The warning is the safeguard;
it is not cosmetic.

The warning is a **product control**, not policy copy: it ships with the
in-product deletion flow that **#4029 adds**, and privacy §16 now describes that
flow. The policy copy therefore states the **consequence** (below); the warning
itself stays in the product, where the decision is made.

A solely-owned team deleted this way goes through the **team-account** purge
(`tortoise/hosted_api.py` `_purge_deleted_orgs`), so its nested custom-graph
namespaces and per-graph backup pools are still the **#4190** gap documented
above — still promise-blocking.

**2B — the account's backups are not actively deleted; they age out.** At the
end of the 7-day restore window the account's **live** data is erased, but its
copies in our backup pool are **not** purged by the deletion. They age out on
the existing ≈**28-day** horizon — the 24-hourly / 7-daily / 4-weekly rotation
in `tortoise/backup_config.py` (`retention_hourly` / `retention_daily` /
`retention_weekly`), pruned by `prune_backups` (`tortoise/hosted_backup.py:2952`).

This is **path-dependent**, and the two paths legitimately differ:

| Deletion path | What happens to its own backup copies |
|---------------|---------------------------------------|
| **Graph** | erased at the **7-day** purge (`_purge_graph_storage`) |
| **User account** | **age out** on the ≈**28-day** cycle (2B) |

Both paths are reconciled by the promise's own end destination — *no copy
remains after about four weeks* — which is why the public copy states the
graph's 7-day erasure **and** the four-week backup horizon as **separate
sentences** rather than one number for both. Do **not** "reconcile" the two
into a single figure in copy: that would make one of them false.

## Whose memory it is — a person's erasure does not reach the account's memory

> **OVERRIDES:** the common erasure practice — a data-subject request reaching
> every copy of *that person's* personal data held on our side, **including
> inside the memory of an account that stores it** — because that memory
> **belongs to the account**: a person's erasure deletes the **identity layer**
> and severs the **analytics linkage**, and what the account keeps, the account
> deletes. *(Owner, #3925.)*

Three owner statements, recorded on **#3925**. The third is the one a
well-meaning implementation is most likely to reverse by accident, so it is
stated as a rule here rather than left in an issue thread:

1. **Account memory is the account's property.** *"asking to delete the PII
   can't be done inside team memory. That's the property of the account. IF a
   user wants that deleted they delete the memory."*
2. **A person's erasure does not reach into it.** An erasure request deletes the
   **identity layer (PII)** and severs the **analytics linkage** — it does not
   reach into the account's memory. If someone wants something gone from that
   memory, **the account deletes the memory**: that is the account's action, on
   the account's property.
3. **The account-side capability is DEFERRED, and no lane owns it.** Letting an
   organisation account **search its own memory for personal identifiers and
   selectively delete them** is a *possible future* capability, explicitly not
   prioritised — *"for now that's their problem, not ours."* **Do not build it
   as a side-effect** of an erasure, retention, or storage change, and do not
   read a per-subject erasure request as licence to implement it.

### Why ruling 1B is not a counter-example

Ruling **1B** (above) deletes a **solely-owned** team with its owner's account.
That is *not* a person's erasure reaching into an account's memory: with no
other owner, the person **is** the account, so the **account** deletes its own
memory — the same action, on the same property, reached through the same gate. A
**member's or co-owner's** erasure cascades **no** team at all: a second active
owner, a non-owner role, and the anonymous agent anchor (`user_id IS NULL`) each
keep the org out of the cascade.

### What holds the boundary today

| Anchor | What it does |
|--------|--------------|
| `tortoise/supabase_control.py` `sole_owned_org_ids` | the personal-erasure cascade's ownership gate — an org qualifies only when the account holder is its **only** active owner |
| `tortoise/supabase_control.py` `caller_membership_org_ids` | the replay claim's gate — a claimed org is honoured only while the caller still holds a membership row there, so a stale anchor cannot drive a cascade against a stranger's team |
| `tests/test_export_delete.py::TestAccountDeletion::test_cascades_only_solely_owned_teams` | the mutation-confirmed regression: a shared team is left untouched |
| the MCP/SDK surface-approval rule (`CONTRIBUTING.md` → *the MCP tool surface and public SDK methods cannot grow by accident*; `config/surface-manifest.yml`) | a surface change is a reviewable diff that **requires owner approval** — and the tool or method a team-memory filter would need is a surface change |

The last row is why this is written down: the capability is **deferred by
decision**, so the boundary is the thing that must survive until that decision
is reopened — never the thing to "finish" while completing something adjacent.
Reopening it is an owner question on **#3925**; it is not a discovery to be
absorbed into an adjacent deletion change.

**It is not a small change if it is ever prioritised.** There is **no
per-entity purge inside a live graph** (see *Accepted gaps the promise carries*
above — a point removed from a live graph is an ordinary write, bounded only by
the backup horizon). A selective, per-identifier delete over account memory is
therefore a **storage-layer** change, not an API convenience on top of the
existing delete paths.

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

## A different axis: the product-usage record (#3843)

The owner decided (#3843, 2026-09-17) to keep a rich **per-capture, per-team**
usage record — *team · session · timestamp · harness · model(s) · calls · input
tokens · output tokens · cost* — written as one `capture_cost` row per capture
ATTEMPT into `analytics_events`, so usage can be read at team level ("this
team's usage is climbing") and at capture level ("this capture was unusually
expensive"). Its retention is **not** the deletion promise above and **is not a
window**:

- **No default window, of any length.** The owner ruled (#3853, 2026-09-17) that
  there is no default retention period on user content, and the only
  user-initiated deletion flow is the account/team path.
- **On erasure the record is retained decoupled, not deleted.** *"Usage data
  that's been aggregated (product usage analytics) can't be fully deleted but
  gets decoupled from your identity (which is fully deleted)."* (Owner, #3853.)
  No code severs a link, because the decoupling **is** the identity deletion:
  `purge_org_control_plane` hard-deletes `api_keys`, `org_memberships`,
  `invitations` and then the `organizations` row — the row that carries the
  name, and through it the identity — and **never touches `analytics_events`**,
  which has no FK to `organizations` to cascade through. The surviving row
  carries a random `org_id` and no longer resolves to a team or a person. Pinned
  by
  `tests/test_retention_promise.py::test_usage_record_is_retained_decoupled_on_purge`.

> **OVERRIDES:** the common erasure practice — a data-subject request deleting
> *every* copy of that subject's personal data, analytics included — because the
> owner ruled that product-usage analytics are kept and **de-identified** rather
> than deleted. Customer copy states the same carve-out: privacy §6 *"analytics
> data is not silently claimed to be deleted"*, and the §16 deletion scope is
> *"deleted or de-identified"*. Reopening it is an owner question on **#3853**,
> not a discovery to absorb into an adjacent deletion change.

## Open gaps and open decisions

- **Team-account cascade (D5 — follow-up).** Deleting a team account today
  revokes access and drops the **default** graph's namespace, but it does **not**
  erase custom-graph namespaces, and `prune_backups` explicitly never touches
  nested per-graph pools — so the nested backups of **every** graph in the org
  (**default included**) survive indefinitely. The owner ruled the team account
  "should delete everything inside"; that widening is a separate, destructive
  change filed as **#4190** — which the end-destination ruling below makes
  **promise-blocking**, not merely a gap.
- **Backups on a delete request (D6 — DECIDED 2026-09-30).** The owner ruled
  **2B**: on a user-account deletion, live deletion does **not** actively purge
  backup snapshots — they **age out on the 24/7/4 (≈28-day) cycle** (see *Owner
  decisions — the account-deletion path* above). The second-region mirror is
  **unaffected**: deletion is still **not propagated** to it (append-only;
  `BACKUP_MIRROR_ENABLED` default off), which is consistent with aging out
  rather than actively deleting.

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

## Public wording — §6 approved (2026-09-18); account deletion applied (2026-09-30); session-recording retention (OWNER RULING 2026-10-08, #2535); DPA §11 pending

> **Privacy §6 is OWNER-APPROVED.** The owner ruled (2026-09-18): *"Delete =
> gone from your view immediately · restorable for 7 days · no copy remains
> after about four weeks."* The approved §6 bullets are quoted below; each is
> labelled **applied** or **superseded**, and `website/privacy.html` carries
> the applied text (with one recorded exception — the §6 Accounts bullet's
> account-level immediate-removal claim; see *Recorded gap* under that bullet
> below). The §"Deletion scope" sentence
> was **applied 2026-09-30** (rulings 1B + 2B; quoted below). `website/dpa.html`
> §11 already states the backup carve-out as *up to four weeks*, which is what
> ruling 2B requires, so it was left **unchanged** and remains pending the
> owner's confirmation.
>
> **Mirror note.** The draft's **policy body** is coupled to the published
> page: `tests/e2e/test_legal_pages.py::test_draft_to_render_fidelity`
> (anchored at `^## Scope of this policy`) requires every ≥40-char sentence of
> the draft body to appear in the served `/privacy` page, so §6/§16 (and every
> other retained-number sentence) **must be edited in BOTH files together** —
> updating one without the other reddens the gate. Only the G-gate
> preface/metadata (the `## Draft metadata` block, the delete-account capability
> check, and the LFPDPPP / outline / canonical-sentence notes above the anchor)
> is the historical snapshot, kept for the record and not rendered.
>
> **Division of labour.** The policy copy states the **restore window** as one
> number (7 days), the **backup horizon** as one number (four weeks), and the
> **single path difference** between them (a deleted graph's own backups end at
> 7 days; an account's age out over the four weeks). The 24-hour / 7-day /
> 4-weekly rotation, the per-graph-vs-entity distinction, the best-effort purge
> caveat, and the mirror note live in the sections above. The doc explains; the
> policy states. Neither may contradict the other.

**Privacy §6 — the approved list items:**

**Memory graphs (applied 2026-09-18):**

> **Memory graphs.** Deleting a memory graph removes it from your view
> immediately and revokes its API keys. It stays restorable from the
> organization's "Trash" for 7 days. After that it is permanently erased,
> together with its backup copies.

**Session recordings (OWNER RULING 2026-10-08, applied):**

The owner ruled: *"We keep session recording until the user deletes them. We're a
memory product, it's like asking Google Drive how long they keep files."* There is
no retention window and no automatic expiry — persistence is the delivered
behaviour of a memory product, not an incidental retention choice. The applied
text is:

> **Session recordings.** When an organization has session recording enabled,
> agent sessions recorded on a member's own machine are retained **until they are
> deleted** — there is no retention window and no automatic expiry. … Deletion is
> performed by the organization's members: any member of the organization can
> view the organization's recorded sessions and delete them. … Turning recording
> off stops new captures; it does not delete sessions already recorded. A member
> who leaves an organization loses access when their membership ends, and
> sessions they contributed before leaving remain part of that organization's
> stored memory until deleted.

**Stated, not implied.** GDPR Art. 5(1)(e) storage limitation requires personal
data be kept no longer than necessary for the purpose, so the retention is tied to
a disclosed purpose (§2) and a stated legal basis (§4) rather than asserted
barely. Silence would not be a defence.

**Authorization, measured — not assumed.** The policy originally described a
member-scoped / admin-scoped deletion split. That split is **not shipped**: session
list, detail and delete are organisation-member authorised with **no owner check
and no role gate** (`delete_session` → `get_current_org_session_ungated` +
`_require_scope`, which returns early for session auth), so **any active member can
delete any session in the organization**. The published text therefore states the
shipped model, and describes the scoped split as a planned refinement. A policy
describing an access-control boundary that does not exist is a security-review
finding, not a stylistic one.

**The original Backups bullet — SUPERSEDED 2026-09-30, NOT applied:**

> **Backups.** Our backups cover the last four weeks. A backup taken while your
> data was live can therefore still contain it for up to four weeks. A deleted
> memory graph is not in that category — its own backups are erased when its
> 7-day window ends.

The wording above was the approved §6 Backups bullet and is kept here as
history; ruling 2B replaced it in `website/privacy.html` with the version under
*the backup path distinction* below, which is the applied text.

**Privacy §6 — account deletion (owner ruling 2026-09-30, applied):**

> **Accounts.** Deleting a user account or a team account removes it from your
> view immediately. It stays restorable for 7 days. After that the account's
> live data is permanently erased. Deleting your personal account also deletes
> any team for which you are the only owner.

**Recorded gap — the account-level half of that wording is not shipped, and the
published copy is narrowed to what ships.** The approved wording covers two
things and only the first is enforced. The **solely-owned-team** half IS:
`DELETE /v1/user/account` cascades every team the deleter alone owns through the
same `_cascade_soft_delete_org` the team endpoint uses (API keys revoked,
memberships set `removed`), so those teams are gone from the deleter's view
immediately (ruling 1B). The **account-level** half — "gone from your view
immediately" for the account itself — is NOT: nothing in the shipped change
gates sign-in for a delete-pending account. `account_deletions` is read only by
the deletion ledger seams and the boot/hourly `_purge_deleted_accounts` erase
sweep, so a user inside the 7-day window can sign back in and keep using the
account; the account and its live data are erased when the stored window
elapses. `website/privacy.html` §6/§16 therefore state immediate removal for the
**solely-owned teams** only, and describe the account as erased after the same
7-day window (reversible through the email channel), keeping every other
approved element — the 7-day window, the four-week backup horizon, the
sole-ownership rule, and the no-self-service-restore statement — unchanged.
Closing the account-level half needs a session-mint gate outside
`tortoise/hosted_api.py`, where no sign-in path reads the ledger; the
account-level immediate-removal sentence returns to the copy when that gate
ships. This is a **recorded gap against the ruling, not a new ruling**: the
approved wording above is kept as the approval record, and the published copy
now describes the behaviour the product actually ships.

**Privacy §6 — the backup path distinction (owner ruling 2B, applied):**

> **Backups.** Our backups cover the last four weeks. A backup taken while your
> data was live can therefore still contain it for up to four weeks. The two
> deletion paths differ in how their own backup copies end: a deleted memory
> graph's own backups are erased when its 7-day window ends, while deleting an
> account does not actively purge its copies in our backups — those age out on
> the same four-week cycle. A deleted session recording is likewise not actively
> purged from snapshots taken before its deletion; those copies age out on the
> same four-week cycle, so deletion of a recording is complete in the live
> service immediately and in backups within four weeks.

The 1B warning ("Deleting your personal account will also delete any teams for
which you're the only owner", confirm/cancel) is deliberately **not** in the
policy copy: it is a control on the in-product flow **#4029 ships**, so it
belongs in the product, not in the policy.

**Privacy §"Deletion scope" — applied 2026-09-30 (was pending confirmation):**

> A deleted memory graph, user account, or team account remains restorable for
> 7 days. After that, the deleted item's live data is permanently erased; for a
> deleted account, copies in our backups are not actively deleted and age out on
> the four-week cycle described in §6. §6 covers how a deleted memory graph and
> a deleted account are erased, and the
> [retention and deletion policy](https://github.com/daniel-ospina/tortoise/blob/main/docs/retention-and-deletion.md)
> is the single source of truth for these windows. **Session recordings are not
> governed by the 7-day restore window:** they are retained until deleted, as
> described in §6.

**DPA §11 — the aligned one-number form (pending confirmation; unchanged by 2B):**

> … data in backups retained for up to four weeks — our backups cover the last
> four weeks — to maintain integrity and not used for any other purpose.

Ruling 2B (account backups age out on the ≈28-day cycle) is already what DPA §11
states, so no DPA change was needed. The `website/dpa.html` §11 sentence stands.
