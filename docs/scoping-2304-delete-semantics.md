# Scoping — #2304: custom-graph delete leaves data at rest + overstates permanence

> Double-diamond scoping (issue-scoping skill). Human approval gate: present options +
> recommendation to the owner before implementation planning.

## Problem Diamond

### Problem-diverge — alternative framings

| Framing | Plausibility | Notes |
|---|---|---|
| P1 "UI copy is wrong" (fix the words) | Rejected — copy is a symptom | Changing wording to "hidden, not deleted" would be *honest* but keeps indefinite data retention and no undo — the same operational hole with worse PR. |
| P2 "Delete must be a two-phase trash (quarantine → purge)" | Confirmed — owner direction 2026-09-06 (Option C) | Matches the platform-class pattern (GCS soft delete 7d default; SaaS grace windows 7–30d). |
| P3 "Delete must destroy data instantly (crypto-shred)" | Deferred | Per-graph encryption keys don't exist; interacts with #2318 (secret store). Best long-run erasure story; not buildable now without key infrastructure. |
| P4 "Delete is only a compliance problem" | Rejected — it is also a trust/product problem | Data at rest on a paid developer platform that the UI calls deleted erodes trust and invites a GDPR request that we cannot currently honor cleanly. |

**Root causes (fix roots, not symptoms):**
1. `delete_graph` tombstones (`status='deleted'`) and revokes keys, but **no purge ever runs** — the namespace data stays at rest forever (customs never swept; legacy tombstones predate everything).
2. **Backup archives of a deleted graph outlive deletion twice over**: pre-delete dumps survive normal retention (hourly/daily/weekly), and the graph's own nested archive pool is never pruned once the graph leaves the sweep enumeration (#2378-documented tombstone-pool accumulation).
3. UI copy ("removed permanently") describes intent, not behavior.
4. Delete is one-way today — a mistake (accidental delete, malicious actor) has no recovery path. Owner chose to fix this with a grace window.

### Problem-converge

**Confirmed problem:** Graph deletion must *mean* what the UI says — data is recoverable during a short disclosed grace window, then physically erased (live namespace **and** backup artifacts), with honest copy — and must cover graphs deleted before this feature ships. This is the privacy/trust fix + undo safety net.

**Constraints:** (a) restore must never resurrect into a quarantined/tombstoned target (existing #2313 tombstone guard stays); (b) keys of a quarantined graph stay dead (restore is owner-session-scoped); (c) quota freed at delete (owner decision) is NOT re-claimed during grace; (d) purge must be idempotent + safe under concurrent restore (grace boundary race); (e) copy in dashboard/privacy.html/runbook must be truthful.

## Solution Diamond

### Solution-diverge — candidate approaches

- **S1 — Trash-can in place (owner Option C):** delete = quarantine (existing tombstone hides it; keys dead; quota freed). Owner-only surfaces: "in trash" list, two restore modes, purge job past the 7-day grace. Purge = registry sweep over tombstoned custom graphs → GRAPH.DELETE the namespace → drop the graph's R2 backup pool + prune its legacy index entries → reconcile any failed cascade revokes.
- **S2 — Hard delete now, grace via backups:** keep behavior close to today, fix copy, and offer "restore from last backup" for ≤ grace. Cheaper (no quarantine read surface) but restore granularity = last archive (≤ hourly) and there is no pre-delete exact-state read-only reconciliation; weaker undo. Also leaves tombstone pools to #2304-adjacent cleanup only.
- **S3 — Crypto-shred per graph:** per-graph encryption key; delete = destroy key (data + ciphertext archives unreadable immediately); purge drops remnants. Strongest erasure (GDPR "backups beyond use" is moot) but requires per-graph key infrastructure → build after #2318.

### Solution-converge

**Recommendation: S1** (owner Option C) as a staged build, with S3 as the recorded future direction:
- **Stage 1 — honest quarantine + restore surfaces:** trash list + read-only query + full-restore (destructive-overwrite-warned when the name was reused), owner-scoped, keys stay dead.
- **Stage 2 — purge job:** sweep tombstoned customs past grace (incl. pre-existing tombstones); drop namespace via the `_drop_team_graph_impl` GRAPH.DELETE mechanics; drop the graph's nested R2 pool + legacy index entries; reconcile failed key revokes (delete must only 204 when the cascade is confirmed).
- **Stage 3 — copy/privacy:** delete-confirm wording ("deleted permanently after a short recovery window"), dashboard trash copy, runbook + auth-architecture notes, privacy.html §6/§16 truth check.
- **Stage 4 (future, separate issue):** crypto-shred per graph after #2318.

**Rejected:** S2 (worse undo + no exact-state reconciliation + copy-only honesty doesn't fix the retention hole); S3 now (no key infra; do after #2318).

**Key decisions to record:**
1. Grace window = 7 days (owner). Purge runs daily past grace; boundary race (restore vs purge in the same sweep) resolved by the sweep deleting only graphs still tombstoned ≥ grace at purge time AND restore taking an owner-scoped lock on the tombstone row.
2. **Purge ownership guard (verifier P1):** purge must NEVER drop a namespace that a LIVE graph now occupies. Custom namespaces embed the server-generated gid (`team_{tid}_{gid}` — gids never reused), but name-based namespaces (supabase-lane custom names / any legacy shape) CAN be re-occupied after delete+recreate. GRAPH.DELETE runs only after confirming the namespace still maps to the tombstoned graph-id row (`namespace → graph_id` ownership check against the registry/supabase row before drop).
3. Restore target: same graph id/namespace. For customs the namespace IS identity (`team_{tid}_{gid}`) — no cross-graph collision; a display-name reuse maps to a different namespace and full-restore into a LIVE occupant is destructive-overwrite, warned + confirm=true (read-only query never conflicts).
4. **Backup artifacts (pinned, verifier P2):** post-#2313 archives are PER-GRAPH pools — a deleted graph's pre-delete dumps live in ITS OWN nested pool, so purge dropping that pool makes "gone" true with no cross-pool leakage. The only cross-cutting artifacts are pre-#2313 legacy FLAT dumps (per-team shape): restore already refuses them via the tombstone guard (mid-grace and after), and purge deletes them via the #2370 classification index. The #2304 body's "nightly per-team dump outlives purge" phrasing predates #2313 and is stale — the scoping pins the current per-graph model.
5. **Registry-row disposition (verifier P2):** purge KEEPS the tombstone row with a `purged_at` marker + cleared namespace (audit trail + dedup history); trash UI filters purged rows so no dead Restore buttons appear. #2378's tombstone-pool accumulation is thereby resolved for custom graphs (nested pool dropped at purge; legacy flat pools per decision 4).
6. **GDPR basis (verifier P2 — corrected):** the 7-day owner-queryable window is data "in use", so ICO's "beyond use" carve-out does NOT justify it — the basis is a DISCLOSED contractual retention window (delete-confirm copy + terms state 7-day recovery then erasure) plus restore-time re-erasure for any remaining backup copies (ICO-recognized). Art. 17 erasure is "without undue delay": the ≤30d envelope is a product decision, not a statutory grace — counsel/plain-language check (issue research item 7) is MANDATORY before ship, not conditional.

## Verification Checklist (scoping)

| Surface | Test layer | Expected verification |
|---|---|---|
| Quarantine visibility | e2e/unit | tombstoned graph absent from normal lists; owner trash surface shows it |
| Key deadness | unit | quarantined keys reject reads/writes; restore never resurrects keys |
| Full restore | e2e | destructive-overwrite warn + confirm; content matches pre-delete (or last archive + reconcile) |
| Read-only query | e2e | owner reads quarantined namespace; no writes |
| Purge job | unit/e2e | past-grace tombstone → namespace dropped ONLY after namespace→graph-id ownership check, R2 pool dropped, legacy flat archives via the #2370 index cleaned, tombstone row kept with purged_at (no dead restore buttons), idempotent, covers legacy tombstones |
| Backup-restore refusal | unit | mid-grace and post-purge backup-restore of a deleted graph refused (tombstone guard) |
| Boundary race | unit | restore-vs-purge same-window resolves safely (either wins, no partial) |
| Name-reuse guard | unit | purge never drops a namespace re-occupied by a live graph |
| Cascade-revoke | unit | failed key revoke at delete is reconciled by purge/delete retry; delete 204 only on confirmed cascade |
| Copy/privacy | doc | dashboard + privacy.html + runbook truthful |

## Complexity

standard per issue body, but this spans hosted_api (delete/quarantine/restore endpoints), a new purge sweep (registry + FalkorDB drop), R2 pool/index cleanup, the dashboard (trash UI + copy), and docs — implementation should be treated as a multi-task project plan (writing-plans after owner approval).

## Fractal Fields
- **Level:** project · **OIT:** see issue #2304 · **E2E:** TBD · **Verification:** TBD · **Wiring:** TBD
