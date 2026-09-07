# Plan — #2304: trash-can delete (quarantine → restore → purge)

> Source: docs/scoping-2304-delete-semantics.md (owner-approved Option 1). Owner decisions
> 2026-09-06: 7-day grace, two restore modes, purge incl. legacy tombstones.

## Task 1 — Quarantine + owner restore surfaces (backend)

**1a. Enumerate tombstones (owner view).** Add `GET /v1/graphs/trash` (owner/admin session
only; never keys): registry mode queries `Graph {team_id, status:'deleted'}` rows returning
`{graph_id, name, namespace, kind, deleted_at}`; supabase mode queries `graphs` `status eq
deleted` via the existing control-plane seam (`soft_delete_graph` sets a deleted_at — confirm
column; if absent add it in the same migration touch). Default graph never appears. Requires
`deleted_at` on the registry node too (graph_delete must stamp it — check/add).

**1b. Full restore endpoint.** `POST /v1/graphs/trash/{graph_id}/restore` (owner/admin
session). Semantics: un-tombstone the row (status→active), re-create the ACL user (C4
`_acl_user_create_hook`), re-mint keys is NOT automatic (owner mints after restore — do not
resurrect old keys: dead means dead). If the original namespace is still occupied by the
quarantined data → restore is a plain flip + verify. If a LIVE graph now occupies a
name-based namespace (supabase customs / name-reuse) → destructive-overwrite guard:
`confirm=true` body param + strong warning; overwrite = drop live occupant's data via
`_drop_team_graph_impl` ONLY after ownership check (purge guard reused). Return the restored
graph row.

**1c. Read-only query surface.** `GET /v1/graphs/trash/{graph_id}/points` (owner/admin
session): owner reads the quarantined namespace read-only (points/sessions) for
reconciliation. Writes 403. Reuse the data-plane session SDK bound to the namespace with a
read-only wrapper — no key auth.

**1d. Keys stay dead.** Quarantined graph keys are already revoked at delete; restore never
resurrects them; trash endpoints are session-role-gated (owner/admin) so a revoked key can
never read the trash.

## Task 2 — Purge sweep (backend)

**2a. Purge enumeration.** New sweep (extend `backup_sweep`-adjacent purge or a
`run_graph_purge` in hosted purge machinery): every tombstoned custom graph of every team
with `deleted_at ≤ now - 7d` (include legacy tombstones — no deleted_at → treat as expired
once, but gate legacy drops behind the ownership guard). Default graph never eligible.

**2b. Ownership guard (verifier P1).** Before any GRAPH.DELETE: re-read the row; drop ONLY
if the namespace still maps to the tombstoned graph id (a name-based namespace re-occupied
by a live graph must NOT be dropped). If re-occupied → skip the namespace drop, mark the row
`purged_at` with `namespace_retained:true` residual for operator review (data of the deleted
graph no longer separately addressable; the live occupant owns the namespace).

**2c. Namespace drop.** `_drop_team_graph_impl(team_id, namespace)` (GRAPH.DELETE via
select_graph(namespace).delete(); absent-graph = success). Idempotent.

**2d. Backup artifacts.** Delete the graph's nested R2 pool `backups/{team}/{gid}/` +
per-graph state `ops/teams/{team}/graphs/{gid}/` via the storage seam. Delete legacy FLAT
archives of this graph via the #2370 classification index (`ops/legacy-flat-index/{team}.json`
entries with graph_id == gid) → delete those flat objects + rebuild the index object.
Best-effort deletes logged (never fail the purge of the namespace).

**2e. Row disposition.** Keep the tombstone row; stamp `purged_at` (+ `purged_namespace`).
Trash list filters purged rows (no dead Restore). Idempotent re-runs converge.

**2f. Schedule/trigger.** Purge runs in the daily/hourly driver sweep cadence (reuse the
existing scheduled sweep entrypoint — operator-triggerable internal endpoint first, wire the
cron cadence second). Locked per-team against concurrent restore (same as restore).

## Task 3 — Dashboard trash UI (frontend)

Trash list view (Graphs tab → "Trash (N)" when owner/admin): rows show name/kind/`deleted_at`
+ purge countdown; actions: Restore (full — with destructive-overwrite confirm modal when the
name is reused) and Open read-only (points view). Empty state. Delete-confirm copy updated:
"Graph is recoverable for 7 days, then permanently erased (data + backups)."

## Task 4 — Copy/privacy/docs

Delete-confirm + trash copy; `website/privacy.html` §6/§16 truth check ("deleted after a
short recovery window; backup copies erased with the graph or refused at restore");
runbook + auth-architecture notes; counsel/plain-language note recorded on the issue
(research item 7 — mandatory).

## Task 5 — Tests/E2E

Unit (carve-out): tombstone enumeration, restore flip + role gates + keys-dead, read-only
403s, purge ownership guard (incl. re-occupied namespace), purge idempotency, artifact pool
drop, index rebuild, purged_at filter, boundary race (restore vs purge via lock), legacy
tombstone coverage. Docker-lane E2E: delete → trash list → read-only → full restore →
delete → purge → gone (namespace + pool + restore-refused). Registration in ci-surfaces.

## Sequencing / commits

T1a→T1b→T1c/d (one commit cluster per surface + tests), T2 (sweep + guard + artifacts +
tests), T3 (UI + tests), T4 (copy), T5 (E2E). Each commit VGATE + CI. File-level budget:
tortoise/hosted_api.py (+supabase_control), a new purge module or hosted purge function,
website/apps/dashboard/src/main.jsx, docs. Complexity: treat as project (multi-task).
