---
title: "Durability Posture — the one authority"
type: operations
domain: platform
doc_status: live
created: 2026-09-19
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise
related:
  - issue: 2881
    note: "Canonical durability authority (design round §6)."
  - issue: 2879
    note: "Embedded AOF default is opt-in — documented and pinned, never a production path."
  - issue: 2880
    note: "Self-hosted off-box copy — the rule below is not yet met without it."
---

# Durability Posture — the one authority

> **This is the single source of truth for durability claims.** Any statement
> about what keeps a deployment's graph alive, what it can lose, and what has
> actually been verified lives here. Other files **must not carry a durability
> authority claim of their own** — a mechanism, a loss window, or a verification
> level stated as a promise; they state the rule and link here. (Known stale
> restatements are tracked under *Open items*, not silently tolerated.)
>
> Durability vocabulary — *RPO, backup, snapshot, restore drill, AOF/RDB,
> source of truth* — is **operational, not ontology**. No durability **authority**
> statement lives in `docs/ONTOLOGY.md`, which governs graph vocabulary. That
> document carries only domain-level phrasing — such as the `Object.status`
> rows (`§2`, `§4.3`), the `§4.2`/`§4.3` "registration durability" notes (journal
> survival for `rebuild_all`), and the Episodic-layer "is the truth" lines
> (`§2`, `§5`) — never a durability authority claim.

## The rule

In every deployment that runs a FalkorDB server, **durability = the store's own
persistence plus an off-box copy of it.** Nothing else.

- The **JSONL is a domain event log** — it reconstructs a projection under
  changed fold logic, migrates engines, and audits beyond `:GraphEvent`'s 30-day
  window. **It is never the durability mechanism.** It is written outside the
  store's transaction, so it cannot be the authority, and it is not complete:
  a lane with no `event_log_path` (the hosted write path, `tortoise/sdk.py:3416`)
  journals nothing, pre-#2194/#2295 journals miss first-registration writes, and
  raw-Cypher paths have no event at all — so a wipe-and-replay rebuilds only what
  the log holds, not the graph (`tortoise/consistency.py::recover_from_log`
  documents that partial reconstruction; `tortoise/backup.py:65-77` is RDB-first
  for exactly this reason). A second, non-atomic copy would also be the textbook
  dual-write hazard — the two copies can disagree.
- `:GraphEvent` is the **30-day delivery/audit stream**, not a backup.
- This is the settled industry answer (Delta Lake, Apache Iceberg, PostgreSQL
  WAL, etcd, Neo4j, Memgraph, Debezium outbox): authority stays in a
  store-managed artifact, never in an app-written file outside the store's
  transaction.

**A promise that names no mechanism is not a promise.** Each deployment below
therefore states three things: the mechanism (where the data actually lives),
the honest loss window, and **the strongest verification actually performed** —
existence → checksum → restore drill. Where a restore has never been drilled,
this document says **not drilled**, in those words. Green-looking machinery that
has never been restored is worse than absent machinery: it suppresses the
operator's own backups while reporting health.

## Per-deployment posture

| Deployment | Mechanism (where the data lives) | Honest loss window | Strongest verification actually performed |
|---|---|---|---|
| **Hosted — graph archives** (`tortoise/hosted_backup.py`, `tortoise/backup_sweep.py`) | Per-graph **logical dump** (`tortoise-logical-dump-v1`), AES-256-GCM encrypted, uploaded with a sha256 manifest to **Cloudflare R2 (off-box)**, swept hourly (`registry-backup-cron.yml`, `17 * * * *`). **Gated:** `BACKUP_SWEEP_ENABLED` is fail-closed (default off, `tortoise/backup_config.py:172-180`, `:249`); `deploy-hosted.yml:358-380` sets it true only when every required secret is present. | **≤ 1 h typical / ≤ 2 h worst-case** for *entitled* graphs (`tier ≠ free` AND `backup_enabled`; `tortoise/backup_sweep.py:248-272`) *when the sweep is enabled* (`docs/ops/registry-backup-dr.md` §RPO; achieved age is measured per team/graph via `/v1/internal/backups/status`). An **unentitled** graph has **no periodic archive** — its window is unbounded. With the sweep **off** the same holds; the Pro on-demand backup endpoint (`tortoise/hosted_api.py:22938` — `POST /backups`) and the vendor snapshot below remain. | **Checksum + on-path restore verification; the drill is NOT yet clean.** The live restore path verifies sha256 against the manifest and node/edge counts against the authenticated payload before swapping (`tortoise/hosted_backup.py:3-40`) — that gate is what caught the incomplete dump. A **restore drill was attempted once and FAILED** (2026-09-17, `workflow_dispatch`): `Edge restore incomplete: 9687/10000 linked — dump references missing nodes` (#3894; root cause #3895, still open). The scheduled monthly drill (`registry-drill-cron.yml`, #2317) has **not yet run**. Until a real archive restores end-to-end this row is **not drilled to success**; the sweep producing archives is covered by the freshness watcher (#2790 / #2922). |
| **Hosted — vendor platform persistence** (FalkorDB Cloud) | Vendor-managed. Off-box **snapshots every 12 h, 7-day retention** (Startup & Pro); snapshots deleted after 14 days. **Restore creates a NEW instance.** | **≤ 12 h** off-box. In-box persistence (AOF) is **contested** — see below; until settled, treat in-box persistence as unverified. | **Existence only — not drilled.** The cadence and retention are vendor-documented; no restore of a vendor snapshot has been performed by us. |
| **Self-hosted (Docker Compose sidecar)** | FalkorDB sidecar with AOF + named volume (`README.md` self-host path, `docker-compose.yml`) — **on-box only**. | AOF `everysec` = **≤ 1 s** on a clean host; **total loss** if the host or volume is lost. The documented compose path ships **no off-box copy**; `scripts/daily-backup.sh` is a host-specific RDB copy (issue #101) that is not wired into the compose path or its docs — the general off-box copy is **#2880 (pending)**. | **Not drilled.** AOF is a live on-box artifact, not a backup, and no restore of a self-hosted archive has been performed. |
| **Embedded (redisLite — eval only)** | RDB file; **AOF off by default** (`TORTOISE_EMBEDDED_AOF=1` opts in — `tortoise/projection/__init__.py:36-45` (`_embedded_aof_enabled`), `:1157-1181`, `:1831`, `:1865`). Single-writer; concurrent writers lose data. | AOF on: **≤ 1 s**. AOF off: **up to the next RDB save or a clean close** — RDB snapshots may never fire for a small graph (#915, #2879). | **Not drilled** (and not a production path). AOF's on-disk artifact is measured when opted in (`tests/test_embedded_durability_claim.py`) — presence, not a restore. |

> These rows describe **data-plane durability**. Deletion and retention windows
> are a separate promise and live in `docs/retention-and-deletion.md`; encryption
> at rest and in transit lives in `docs/data-safety.md`.

## FalkorDB Cloud — verified, and not

Verified against the vendor's primary documentation (owner, 2026-09-13):

| Claim | Status |
|---|---|
| Off-box snapshots: 12-hourly, 7-day retention (Startup & Pro); snapshots deleted after 14 days | **Verified** (vendor docs) |
| Restore creates a **NEW** instance | **Verified** |
| Point-in-time recovery (PITR) | **Not documented → treat as absent** |
| Credentials are **per-user ACL users** (`instances/{id}/users`) | **Verified** — rotation is **per user, not instance/tenant-wide** |
| `GRAPH.BACKUP` / `GRAPH.RESTORE` | **Refuted** — both command pages 404. The vendor's documented graph backup is Redis `DUMP`/`RESTORE`; our own per-graph archive (above) is an independent logical dump, not a vendor command. |
| AOF durability on our tier | **CONTESTED — do not restate as fact.** The vendor's own docs conflict: the tier table lists AOF as **Pro/Enterprise**, while the API schema exposes `AOFPersistenceConfig` (default `everysec`) with availability = **Yes for Startup**. This is to be **settled with FalkorDB support, not inferred** from either page. |

An unverifiable durability promise is **not** re-stated here as fact. Until the
AOF question is settled with the vendor, the 12-hourly snapshot is the
deployment's actual off-box mechanism, and in-box persistence is unverified.
`entrypoint.sh:8-14` states this correctly today and must not regress to
"FalkorDB Cloud = AOF durability".

## Why this document exists

Three statements disagreed about whether the store or the journal was the
durability authority, so the text inherited whatever the nearest comment
believed. The fix is **deletion, not reconciliation**: the three contradicting
journal-authority statements are removed, and no other file in the gate's
contract surfaces carries the claim. Every remaining live restatement is
enumerated under *Open items*, not silently tolerated. The durability-scoped
gate in `tests/test_durability_posture.py` fails the build if a
`log`/`journal`/`jsonl`/`event stream` is bound to `source of truth` — or to
`is [the] truth` — outside this file.

## Open items

- **#2880** — the self-hosted off-box copy. Until it ships, the self-hosted row
  is on-box only and the rule above is not yet met there. `README.md`'s
  "durable" for the compose path means *persistent on-box, multi-writer* (as
  opposed to the single-writer embedded eval path), not an off-box-copy promise.
  `docs/quickstart-selfhosted.md:57` carries the same wording and is enumerated
  here for the same reason — it is a gate-scanned surface that does not yet
  link this document.
- **#2968** — `docs/infra-runbook.md:29` still states "FalkorDB Cloud (managed)
  — provides AOF durability", the exact claim this document marks CONTESTED.
  Tracked there; the gate's pattern does not match an `AOF … durability` claim,
  so it is not silently tolerated, it is a named residual.
- **`graph-scripts/`** — `add_convergence_evidence.py` and `baseline_scan.py`
  still describe the JSONL as the "source of truth" for graph convergence. This
  document's gate is scoped to the contract surfaces (`tortoise/`, the five
  docs, README); the archival/operation scripts are a separate, open residual.
- **#3895** — the logical dump's edge export is unfiltered, so a production-sized archive could not restore; the one restore drill ever attempted (2026-09-17) failed on exactly that (`9687/10000 linked — dump references missing nodes`). The hosted row above is honest about it: **not drilled to success** until a real archive restores end-to-end.
- **#2826 row A2** — the register row recommending the journal be authoritative.
  The #2881 design round (§7) recommends reversing it to store-authoritative.
  This document states the store-authoritative rule the shipped code already
  implements; the row is the owner's to answer.
