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
states three things: the mechanism (where the data actually lives),
the honest loss window, and **the strongest verification actually performed** —
existence → checksum → restore drill. Where a restore has never been drilled,
this document says **not drilled**, in those words. Green-looking machinery that
has never been restored is worse than absent machinery: it suppresses the
operator's own backups while reporting health.

### Authoritative node classes across a rebuild

**This subsection is the home of the complete node-class map.** Durability here
has a second, narrower meaning than the deployment rows below: a **graph-resident
class that rides no journal record and is not re-derivable** does not survive
`rebuild_all`, whose wipe is an unconditional `MATCH (n) DETACH DELETE n` and
whose replay is journal-only. Such a class is either **preserved** (captured into
the durable pre-wipe sidecar and restored after replay) or it is **lost** —
there is no third mechanism. Classes that can be re-derived are deliberately
**not** preserved, because preserving them would restore a stale value as if it
were truth.

The preserved set is **declared in code**, not discovered:
`tortoise/projection/__init__.py::_config_classes()`. It is **not** a
completeness gate — a class nobody enrolled still recurs, and **#2296
contributes its indicators into this subsection** rather than creating a rival
artifact.

<!-- config-registry:preserved -->
| Preserved class (authoritative) | Identity property |
|---|---|
| `:PackInstall` | `namespace` |
| `:PackManifest` | `namespace` |
| `:Meta{key:'calibration_milestone'}` | `key` |
| `:Meta{key:'config_reset'}` | `key` |
<!-- config-registry:end -->

The `config_reset` entry is the **third state**: a sticky marker distinguishing
"config existed and did not come back" from "never configured". Absence is what
a never-configured graph looks like, so absence can never encode it. It is
**not** a "tombstone" — that is a controlled term in `docs/ONTOLOGY.md` for a
retracted `Point`.

<!-- config-registry:not-preserved -->
- `:EpMeta` — a monotonic epoch that must be re-baselined with the Points;
  preserving it would pin a stale epoch.
- `:Meta{key:'point_fts_v2'}` — the index watermark, re-created on open.
- `:Meta{key:'event_fts_v2'}` — the index watermark, re-created on open.
<!-- config-registry:end -->

The `:Meta` class is therefore enrolled **key-scoped**. A label-wide `:Meta`
read would sweep the two derived `point_fts_v2`/`event_fts_v2` markers into the
preserved set and restore them as if they were authoritative configuration.

### The one carried high-water mark: `:GraphEventMeta`

The map above is keyed on **node classes**; this entry is a single **counter**,
and it is carried for a different reason. The rebuild wipe destroys
`:GraphEventMeta`, whose `last_seq` is the per-graph allocator handed to
`event_store.next_seq` — and it is **not re-derivable**: `rebuild_all` does not
replay `:GraphEvent` rows at all (**#4664**, consolidated into #5048, unfixed)
and the JSONL journal carries no `seq`, so a post-replay scan reads `null`. Left
uncarried, the next emit MERGEs a fresh counter at 1 and hands out a `seq` the
graph already issued; because `read_after` is `seq > cursor`, every subscriber
holding a cursor at or above the restart **silently under-counts**. That is the
identical harm `hosted_backup._restore_event_meta` (#3902) already prevents on
the backup/restore path.

<!-- config-registry:watermark -->
| Carried class | Carried field | Restore rule after replay |
|---|---|---|
| `:GraphEventMeta` | `last_seq` (high-water mark) | `max(carried, max(:GraphEvent.seq), live)` — the MAXIMUM of the pre-wipe sidecar's value, the replayed log and any counter still in the graph, which is a counter's only safe merge and the value `first_seq` is then derived from; `first_seq` is `min(:GraphEvent.seq)`, or `last_seq + 1` when the log is empty, and is raised only (never lowered). `last_seq` is bounded by `event_store.MAX_SEQ`; `first_seq` is bounded by `event_store.MAX_INT64` and may legitimately sit at `MAX_SEQ + 1` when the log comes back empty |

Defect and vehicle: this entry was added by **#4653**; the durable carrier is
the #2943 pre-wipe sidecar's `event_meta` section, re-established by
`event_store.reestablish_watermark` after every replay pass.
<!-- config-registry:end -->

A counter node **missing** one of its two properties — the legacy shape
`hosted_backup._restore_event_meta` (#3902) already falls back on — is
REPAIRED, not preserved. The monotone clause is `IS NULL OR <` because a NULL
carries no position to preserve: preserving it would report a restore that
never happened, and a NULL `last_seq` would then make the next `next_seq`
evaluate `null + 1`. A `last_seq` of some **other** type (a string) cannot be
compared by Cypher's `CASE`, so it is left in place by the write and REFUSED
loudly afterwards rather than coerced into a value the node does not hold.

`first_seq` is deliberately **not** carried. On this path it is not recoverable
from the surviving log either — the rebuild replays points, not `:GraphEvent`
rows (#4664), so the log comes back EMPTY and `first_seq` is derived as
`last_seq + 1`. That is the truthful floor once the stream is truncated, and it
makes `events_poll` answer 410 instead of returning `[]` forever to a subscriber
parked above the fresh counter (the pre-fix behaviour). Carrying `first_seq`
would only relabel the same truncation. The pre-wipe purge floor is therefore
LOST, which is why the backup/restore sibling (`hosted_backup._restore_event_meta`,
#3902) does carry it: there the `:GraphEvent` rows are copied, so the floor is
there to be carried.

**The counter's domain is `0 .. MAX_SEQ`, and the ceiling is `2**53 - 1`, not
INT64_MAX.** `ensure_event_schema` indexes `:GraphEvent.seq`, and FalkorDB
compares numeric RANGES in double precision, where integers above `2**53` are no
longer all representable — so `read_after`'s `WHERE e.seq > $after` **silently
drops** rows in that range (verified on FalkorDB 4.20.4: with the index,
`e.seq > 2**53` returns the row at `2**53 + 2` but not the one at `2**53 + 1`;
without the index both come back). A counter in that range would re-create the
exact #4653 harm — events that exist and are never delivered, with no 410 to
tell the subscriber. `MAX_SEQ` is therefore the last counter value whose next
`seq` is still compared exactly, and the bound is enforced in all three places
that can introduce a value: the pre-wipe validator (untrusted file),
`event_store.capture_watermark` (live counter) and
`event_store.reestablish_watermark` (the write).

**Residual — the ALLOCATOR itself has no ceiling.** `next_seq` is unchanged by
#4653, so a graph that keeps emitting can still walk past `MAX_SEQ` (and would
wrap negative at INT64_MAX) — reachable only at ~9e15 events. An out-of-domain
counter is ALSO reachable without emitting anything, via an unbounded dump
restore (`hosted_backup._restore_event_meta` writes the dump's value verbatim)
or a hand-written node, and that state is what `capture_watermark` refuses: the
rebuild aborts before the wipe rather than restoring it. Fail-closed, but not a
repair; capping allocation is a separate change (**#5379**).

**Residual — the same gap, one step out.** With the log EMPTY (today's state,
#4664) the floor is `last_seq + 1`, so no cursor survives; but the moment a
replay writes `:GraphEvent` rows again, the floor becomes `min(:GraphEvent.seq)`
and a subscriber parked ABOVE the top replayed `seq` — served nothing, yet not
below the floor — reads `[]` with no 410 while a live counter sits far above it.
That starvation is the #4664 truncated-stream residual, not a regression from
carrying the counter — the pre-fix allocator starved the same cursor — and it is
why `first_seq` is raised, never lowered.

The carrier is the durable #2943 pre-wipe sidecar (`event_meta` section),
**not** the config registry above: this class has no identity property to key a
node-class entry on, and snapshotting it as configuration would restore a value
as if it were authored configuration rather than a monotonic counter. **#4653**
is the defect.

**A rescue file can carry no watermark, and the outcome is reported rather than
assumed.** Two sidecar shapes do this: a file written before #4653 (version 1 or
2) has no `event_meta` section at all, and a current-version file can carry an
EMPTY one. The loader accepts both on purpose (a legacy file is still the only
record of the graph-only nodes it holds), and the rebuild then logs one of two
ERRORs, neither of which claims a cause the file cannot record:

- **no counter anywhere** — the allocator is left absent, so it restarts at 1
  at the next emit. That is correct for a graph that never emitted and a silent
  under-count for one whose counter a wipe destroyed; the file records only its
  capture-time state, so it cannot tell the two apart;
- **a counter is present but did not come from the file** — a state-UNKNOWN
  signal, like `legacy_sidecar_no_config_record` (#2814): the value is either the
  one the graph already had (the window between the sidecar write and the wipe is
  real, so nothing was lost) or one re-created after that wipe (the pre-wipe
  position is gone).

Only when the file itself carried the mark is the outcome a success.

**A rebuild still requires a QUIESCED graph.** An event emitted between the
pre-wipe capture and the `DETACH DELETE` is destroyed with the node, and because
the journal carries no `seq` its number is lost too — so the allocator can
re-issue that `seq` after the rebuild. The carry closes the entire post-wipe
window and every non-racing path; it cannot close this one, which is inherent to
replacing a graph with a full wipe+replay.

<!-- config-registry:unenrolled -->
- `:OnboardingState`, `:OnboardingStep`, and their `COMPLETED_STEP` edges —
  destroyed silently by `rebuild_all` today; no vehicle in this change.
  **#4641**
- `:TeamMeta` — same disposition, same vehicle. **#4641**
<!-- config-registry:end -->

**Operator audit** — read-only; every preserved class is enumerated by the
registry, and the one carried counter by the query's last leg, so this query
cannot silently under-report after a class is added:

<!-- config-registry:audit-query -->
```cypher
MATCH (p:PackInstall) RETURN 'PackInstall' AS cls, p.namespace AS ident
UNION ALL
MATCH (m:PackManifest) RETURN 'PackManifest' AS cls, m.namespace AS ident
UNION ALL
MATCH (x:Meta) WHERE x.key IN ['calibration_milestone', 'config_reset']
RETURN 'Meta' AS cls, x.key AS ident
UNION ALL
MATCH (m:GraphEventMeta)
RETURN 'GraphEventMeta' AS cls, toString(m.last_seq) AS ident
```
<!-- config-registry:end -->

## Per-deployment posture

### Derived properties that are STORED, not recomputed

The node-class map above covers *classes*. A **property** can be in the same
position for a different reason — not because it would be lost, but because
recomputing it does not reproduce what the graph held.

**R1 — the embedding STORES: a replay restores it verbatim and never
regenerates it.** A re-embed is a *re-run*, not a replay. `embed(text)` depends
on the model id, its pinned revision and the tokenizer — none of which is a
function of the journal — so re-encoding on replay turns a fold into a silent
re-execution: the same log yields a **different** graph after a model or
provider change. A **creating** producer (the record that computed the vector
from the content riding with it) therefore journals the vector **and** the
identity it was computed under (`embedding_model`, `embedding_revision`,
`embedding_text_hash`). A **re-emitted snapshot** — `PointPromoted`,
`OperatorPromoted`, a re-capture that preserved an older vector — carries the
vector but **no** identity, because that vector may predate a model change and
the record cannot attest an origin it does not know. The replay restores the
vector verbatim either way, *recording* — never resolving — a divergence from
the configured embedder.

The rule is **presence is ownership**: a producer that owns the `embedding`
field always writes the key (the vector, or an explicit `None` when it has
none), and a replay restores it, clears it, or leaves it — it never recomputes.
A key that is *absent* is a pre-#5004 record, where recomputation is the only
behaviour available and is kept deliberately for back-compat.

This is **not** a durability-authority claim — the JSONL is still not the
durability mechanism (see *The rule*). It states what a `rebuild_all` replay of
that log must reproduce. **Paths still outside the rule, each pinned by a test
and filed:** `PointRevised` — the record's `embedding` (including an explicit
`null` clear) is not read on replay (**#5046**); `_update_entity`'s Point branch
journals no embedding line at all — so its caller vector falls back to the
creation value on a rebuild, and the `embedding_verbatim` marker it sets is
**live-only** (**#4094**; the marker is deliberately still set, because leaving
the vector unmarked would trade this declared marker gap for an undeclared
byte-level vector divergence on the LATER, journaled `promote_point` re-emit);
a stale `PointPromoted` predating a `delete → recreate` re-applies the dead
incarnation's derived fields — the `#2884 A7` gate is belief-only by a recorded
#785 decision (**#5068**).

| Deployment | Mechanism (where the data lives) | Honest loss window | Strongest verification actually performed |
|---|---|---|---|
| **Hosted — graph archives** (`tortoise/hosted_backup.py`, `tortoise/backup_sweep.py`) | Per-graph **logical dump** (`tortoise-logical-dump-v1`), AES-256-GCM encrypted, uploaded with a sha256 manifest to **Cloudflare R2 (off-box)**, swept hourly (`registry-backup-cron.yml`, `17 * * * *`). **Gated:** `BACKUP_SWEEP_ENABLED` is fail-closed (default off, `tortoise/backup_config.py:172-180`, `:249`); `deploy-hosted.yml:358-380` sets it true only when every required secret is present. | **≤ 1 h typical / ≤ 2 h worst-case** for *entitled* graphs (`tier ≠ free` AND `backup_enabled`; `tortoise/backup_sweep.py:248-272`) *when the sweep is enabled* (`docs/ops/registry-backup-dr.md` §RPO; achieved age is measured per team/graph via `/v1/internal/backups/status`). An **unentitled** graph has **no periodic archive** — its window is unbounded. With the sweep **off** the same holds; the Pro on-demand backup endpoint (`POST /backups`, `tortoise/hosted_api.py`) and the vendor snapshot below remain. | **Checksum + on-path restore verification; the drill is NOT yet clean.** The live restore path verifies sha256 against the manifest and node/edge counts against the authenticated payload before swapping (`tortoise/hosted_backup.py:3-40`) — that gate is what caught the incomplete dump. A **restore drill was attempted once and FAILED** (2026-09-17, `workflow_dispatch`): `Edge restore incomplete: 9687/10000 linked — dump references missing nodes` (#3894; export defect fixed by #3921, `_DUMP_REVISION = 2` — #3895 stays open tracking a successful re-drill of a real artifact). The scheduled monthly drill (`registry-drill-cron.yml`, #2317) has **not yet run**. Until a real archive restores end-to-end this row is **not drilled to success**; the sweep producing archives is covered by the freshness watcher (#2790 / #2922). |
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

- **#2814 residual** — the preserved set is a **declared** registry, not a
  completeness gate. For a class nobody enrolled, `rebuild_all` captures
  nothing and refuses nothing: the byte-identical unconditional wipe (which
  #2814 does **not** change, by owner decision — the surface belongs to PR
  #2996) destroys it exactly as before. The live instance is the
  `unenrolled` list above (#4641).
- **#2814 residual** — within an interrupted-rebuild window a **pending**
  (non-retired) pre-wipe sidecar reverts a **colliding** config key to its
  pre-wipe value, so a config deliberately deleted after the wipe can be
  resurrected by the retry. The remedy is the operator deleting the pending
  rescue file — never the retired, entry-less one. Pinned by
  `test_pending_sidecar_restores_leftover_config_over_a_post_wipe_delete`.
- **#2814 residual** — a pre-preservation (`version: 1`) rescue file carries no
  config record at all, so the state of the graph it describes is **unknown**,
  not "reset". It is reported as
  `reason='legacy_sidecar_no_config_record'` and never as `reset`.
- **#2814 residual** — the sticky `config_reset` marker makes a data-gone graph
  report `count(n) > 0`, so `consistency.py` returns "graph already has nodes —
  no rebuild" and auto-recovery stays suppressed until an operator clears it.
  Safe (it refuses rather than wipes), but a real availability effect; changing
  that gate to exclude bookkeeping nodes is a separate recovery-semantics
  decision, out of #2814's unit. Pinned by
  `test_recover_from_log_refuses_nonempty_graph_with_config`.
- **agent-infra #1344** — `skills/tortoise-rebuild/SKILL.md` (the operator runbook
  the runtime points operators to) states the event log is the source of truth
  and describes `rebuild_all` as only lossy. That file lives in **agent-infra**
  (the tortoise `operations/skills/*` paths are symlinks into it), so it is
  outside this document's gate roots and outside the tortoise repo's diff: the
  four required corrections are tracked there, not silently rewritten here.
- **#2296** — the class-wide durability contract. **Dormant/unowned.** Its
  indicators are contributed into *Authoritative node classes across a rebuild*
  above; it does not create a rival map.

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
- **#3895** — the logical dump's edge export *was* unfiltered (fixed by #3921, `_DUMP_REVISION = 2`; `tortoise/hosted_backup.py:387` now restricts edges to the exported node set). #3895 stays open for AC-1's remaining evidence: a fresh drill restoring a real production-sized artifact end-to-end. The one restore drill ever attempted (2026-09-17) failed on exactly this (`9687/10000 linked — dump references missing nodes`); the hosted row above is **not drilled to success** until a real archive restores.
- **#2826 row A2** — the register row recommending the journal be authoritative.
  The #2881 design round (§7) recommends reversing it to store-authoritative.
  This document states the store-authoritative rule the shipped code already
  implements; the row is the owner's to answer.
