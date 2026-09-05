---
title: "Research — Backup/DR/Erasure Best-Practice Benchmark (whole setup, #2313)"
type: engineering
domain: platform
doc_status: live
created: 2026-09-06
issue: 2313
ownedBy: epistemic-team
subjects:
  team: epistemic-team
aboutObjects:
- tortoise-hosted-platform
---

<!-- research (medium depth): benchmark of the whole backup/DR/erasure posture against industry best practice.
     Internal: code walk + DR runbook + scoping #2313. External: 6 web queries (2026-09-06).
     Revised 2026-09-06 after fresh-session verifier: cadence/RPO corrected (hourly, not nightly),
     geo-durability row added, selfhost AOF layering + monitor-the-monitor inventory added. -->
# Research — Backup/DR/Erasure Best-Practice Benchmark (whole setup)

**Date:** 2026-09-06 · **Depth:** medium (6 external queries + full internal inventory)
**Why:** before extending backups to per-graph coverage (#2313), verify we are not reinventing the wheel across the entire durability posture (snapshot/dump choice, cadence/RPO, retention, monitoring, restore drills, security/immutability, key management, geo-durability, erasure/trash).

## Reframed question
"Does our backup code match a checklist" → "Is the durability/DR/erasure posture of a multi-tenant memory service industry-standard across axes — dump strategy, RPO/RTO, retention, monitoring, restore testing, security/immutability, key management, geo-durability, and deletion/erasure — and where are we off-pattern or inventing our own wheel?"

## What we have internally (verified on current main)
- **Hosted backup** = per-team **hourly-scheduled logical dumps** (graph nodes/edges re-encoded, AES-256-GCM encrypted) → Cloudflare R2; restore = download → sha256-vs-manifest verify → decrypt → load into temp graph → swap, with cross-team/cross-graph guards + empty-over-live guard. **Cadence contract: hourly driver cron (#596 plan AC1) → RPO ≤1h typical / ≤2h worst** — the 90-min STALE threshold + 24h hourly retention buckets presuppose sub-2h freshness. `retention_hourly=24` is a retention HORIZON, not an RPO. Achieved cadence can silently degrade under the 30-min driver timeout + serialized per-team locks — **measure actual per-team freshness** (watcher exists; no SLA reporting). Logical dump chosen **deliberately** because production is managed FalkorDB Cloud where BGSAVE-copy of the RDB is unavailable (hosted_backup.py:1-8).
- **Selfhost durability is layered:** AOF on-box (`--appendonly yes`; automatic RDB BGSAVE deliberately disabled `--save ""` — fork/OOM risk, #1786) + **daily** operator/scripted RDB copies with the script's own contract: **RPO ≤24h typical / ≤48h worst under load** (exit-3 defer, daily-backup.sh #101/#209). Hourly logical dumps are the HOSTED-lane mechanism; nothing in-repo wires the sweep driver to a selfhost deploy.
- **Retention:** per-team ~7 daily + 4 weekly + 24h hourly buckets; prune job.
- **Monitoring:** external watcher thread with **mutual heartbeat supervision** (ops/watcher-heartbeat.json + driver-heartbeat.json; WATCHER_DOWN/DRIVER_DOWN/ALERTER_DOWN/R2_DOWN/APP_DOWN taxonomy in the runbook) + a driver direct-R2 freshness leg independent of `/status` + dual-channel alerting (AlertStore + GH fallback); absence-based incidents NEVER_BACKED_UP / STALE / METADATA_LOST; sweep-time drift guards (DATA_LOSS_CANDIDATE on >50%/empty transitions, SIZE_GUARD_ABORT over 100k nodes, P0_GUARD_FAIL). **Accepted residual (documented): app-down AND driver-disabled simultaneously ⇒ no alert.** Note: `BACKUP_SKIP_FRESH_MIN` (backup_config.py:61) is parsed but never consumed — dead knob.
- **Drill:** internal drill endpoint restoring into `_drill_*` scratch, zero production writes, cooldown, GC of stale scratch (docs/ops/registry-backup-dr.md).
- **Known gap (filed #2313):** the sweep covers only the **default graph** per team; custom graphs have NO automated backup (effectively infinite RPO) — scoping doc: docs/scoping-2313-per-graph-backups.md.
- **Known design (#2304):** delete = trash-can (grace window + purge) with two restore modes; backup-artifact policy at purge is an open decision (Q1).

## Axis-by-axis benchmark

| Axis | Our current | Best practice (external) | Verdict / confidence |
|---|---|---|---|
| **Per-tenant backup pattern** | Per-team logical dumps; graph-granular missing (#2313) | Per-tenant logical exports are **the canonical pooled-SaaS pattern**; restore into staging first; tenant-segregated object keys | ✅ On-pattern. #2313 Option A (per-graph keys/state/prune/restore) = the canonical shape at graph granularity. **High** (AWS/Grasp/multi-tenant-saas.com converge) |
| **Restore method** | Temp-graph staging → verify → swap; cross-team/cross-graph guards | Restore into staging, verify, then copy back (contamination prevention, rehearse timing) | ✅ Matches. **High** |
| **RPO / cadence** | Hosted lane: **hourly** driver → RPO ≤1h typical / ≤2h worst (#596 §3.4/§3.8 machinery post-#669); 24h hourly retention buckets; product pricing label says `daily_backups`. Selfhost lane: **daily** RDB copies → RPO ≤24h typical / ≤48h worst under load (daily-backup.sh #101/#209); AOF covers restart-durability only | RPO is a per-tier/per-lane product decision; monitor actual job windows | ⚠️ Strong hosted cadence (hourly). Deltas: (a) `daily_backups` pricing label contradicts the actual hourly cadence (flag is not yet user-facing — "planned"/false on all tiers) — reconcile wording; (b) **custom graphs today have effectively INFINITE RPO** (never swept — #2313); (c) **selfhost lane RPO is 24–48× weaker than hosted (≤24h/≤48h vs ≤1h/≤2h)** — accept or harden; (d) measure achieved per-team freshness, don't assume the contract. **Medium** |
| **Monitoring (silent failure)** | External watcher + **mutual heartbeat supervision** + direct-R2 freshness leg + dual-channel alerting + absence alerts + size/empty drift guards; documented dual-down residual | Alert on **absence, not just failure**; size-trend anomalies; monitor the monitor; **scheduled restore tests** | ✅ Strong/on-pattern (absence-based + monitor-the-monitor are the advanced practices). Gap: no **scheduled** restore drill cadence (manual/operator-invoked today). **High** |
| **Restore testing / RTO** | Drill endpoint exists; operator-invoked; re-drill after layout/key changes. **No RTO target committed** post-#669 (registry-era "restore ≤15 min / app-bootable ≤1h" was retired and not carried to per-team restores); restore time unmeasured until scheduled drills land | Monthly automated restore drills + smoke tests + measure restore time; explicit RTO per tier | ⚠️ On-pattern mechanics, off-pattern cadence (not scheduled/automated). Add an explicit RTO line (carry ≤15-min/≤1h to per-team restores, or state "none committed; unmeasured until drills") so restore speed is a decision, not an accident. **High** |
| **Encryption** | AES-256-GCM at rest; fail-loud keys; sha256 manifests | At-rest encryption + TLS + key management | ✅ Encryption correct. **High** |
| **Key management** | Static keys in env (`TORTOISE_BACKUP_KEY`, `REGISTRY_STREAM_KEY`), manual rotation runbook | KMS/secret-manager with rotation (industry standard) | ⚠️ **Gap**: static env keys, no KMS, no rotation automation. Lowest-cost high-value hardening. (Prior internal research exists: docs/research/2026-08-13-265-encryption.) **High** |
| **Ransomware/immutability** | Offsite R2 (separate blast radius); no object-lock/versioning | Immutable/object-lock copy for ransomware resistance (3-2-1) | ⚠️ **Gap**: R2 bucket locks exist and are cheap to apply (retention by prefix) — protection against malicious deletion/encryption of backups themselves. **Medium** (depends on threat model) |
| **Geo/regional durability** | Single-region R2 + single-region FalkorDB; no regional-redundancy decision recorded | Regional failure is a separate failure mode from DB loss; geo-redundant copy or explicit single-region acceptance | ⚠️ **Open decision**: is single-region acceptable for the stated threat model? R2 cross-region replication is constrained (S3-compatible); a second-region/bucket copy is the standard 3-2-1 extension. **Medium** |
| **Full-platform DR completeness** | Control plane lives in Supabase (hosted) → survives FalkorDB loss; **per-graph ACL users live in FalkorDB and are NOT in Supabase** — no rebuild step found in the restore path | Restore must restore data AND the access surface (tenancy); "a backup is not sufficient until restoration preserves tenant ownership" | ⚠️ **Gap to verify**: after full FalkorDB loss, restoring graph content may leave per-graph ACL users missing → per-graph keys cannot connect (ACL is defense-in-depth but the connection identity is the tenant user). Drill runs into scratch (never exercises live ACL). Add ACL-user rebuild/verification to the DR runbook + drill. **Medium** |
| **Erasure & backups (trash #2304)** | Trash = grace + purge (designed); artifact policy at purge open (Q1) | Configurable grace period + **backup exclusion** + restore controls preventing resurrection + **securely delete backup copies** + traceability; erasure responses within ~1 month | ✅ #2304 design is on-pattern. Q1 recommendation (prune artifacts at purge + tombstone guard; never rely on absence alone) matches external guidance. Also: exclude quarantined graphs from backup sweeps (already in #2304/#2313). **High** |
| **Graph-DB specifics (don't-reinvent)** | Managed lane: logical dumps (BGSAVE-copy unavailable — documented). Selfhost lane: AOF on-box + scripted/operator RDB dumps (auto-BGSAVE disabled #1786) | Redis-family: snapshots (RDB) + AOF for durability; managed platforms offer scheduled snapshot-to-object-storage | ✅ Logical-dump choice justified for the managed lane. **Action to investigate**: whether the FalkorDB host offers managed snapshots/PITR — if yes, layer under logical dumps for whole-DB recovery + shorter RPO (logical dumps stay right for per-tenant granularity); also confirm AOF/durability config on the managed host. **Medium** |
| **Oversized graphs** | >100k nodes → SIZE_GUARD_ABORT incident, **no backup for the largest graphs** | Guard must not silently orphan the biggest/valuable graphs | ⚠️ Acceptable only as a **documented, monitored** limit (it is monitored); consider chunked/segmented dumps or per-graph guard tuning later. **Medium** |

## Gaps mapped to actions (no wheel reinvention required anywhere)
1. **#2313 (filed, scoped):** per-graph coverage — Option A matches canonical per-tenant practice. Reframe the failure as **custom graphs with effectively infinite RPO**, not "widened to 24h". No new invention. (Scoping doc docs/scoping-2313-per-graph-backups.md cadence wording was corrected to hourly in the same changeset.)
2. **ACL-user rebuild on full-platform restore** — verify + add to DR runbook/drill (restore completeness).
3. **Scheduled restore drill** (e.g., monthly automated drill + restore-time measurement) — on top of the existing drill endpoint.
4. **KMS/secret-manager for backup keys + rotation automation** (manual runbook exists today; check prior research docs/research/2026-08-13-265-encryption).
5. **R2 bucket-lock immutability policy** for backup objects (ransomware resilience) — decide by threat model.
6. **Cadence/RPO truthfulness in docs:** reconcile the product `daily_backups` pricing label with the actual hourly cadence (RPO ≤1h typical/≤2h worst, #596); make RPO an explicit per-tier statement; **measure achieved per-team freshness** instead of assuming the contract.
7. **Geo/regional durability decision** — single-region R2 + single-region DB: accept explicitly or add a second-region copy.
8. **Registry/control-plane metadata backup** — confirmed hosted control plane is Supabase (managed, survives FalkorDB loss); selfhost AOF + scripted dumps cover it; no gap beyond #2's verification (registry-backup-dr.md:3-4).
9. **#2304 Q1** artifact policy at purge — prune-at-purge + tombstone guard confirmed as best practice (High confidence).
10. **Dead-knob cleanup:** `BACKUP_SKIP_FRESH_MIN` parsed but never consumed (backup_config.py:61) — wire it into the sweep or delete it.

## Sources (confidence tiers)
- **High (≥3 independent or vendor/practitioner convergence):** per-tenant logical backup pattern (AWS SaaS blog, Grasp multi-tenant DR, multi-tenant-saas.com, dzone); absence-based monitoring + drift + scheduled restore tests (datashelter, lastping, oneuptime, accompio, scality); encryption/3-2-1/immutability + drills (database.tools, tencent cloud, cleverence, cloudvara, sqlflash); erasure incl. backups + grace period + resurrection controls (nocodelisted, bodlelaw, complysafe, oktopeak, avanoo); Redis/FalkorDB snapshot+AOF guidance (Redis docs/blog, oneuptime, FalkorDB docs).
- **Medium (single-vendor capability claims, vendor-authoritative-for-own-product):** R2 bucket locks (Cloudflare docs) — the immutability PRACTICE is High (multi-source), the R2 capability itself is single-vendor; FalkorDB managed-snapshot/PITR availability (FalkorDB enterprise restore API — verify with our host).
- **Medium (2 sources):** KMS rotation standard (implied across database.tools/cleverence "key management"); FalkorDB managed-snapshot availability (FalkorDB enterprise restore API — single vendor source, ⚠️ verify with our host); geo-redundancy norms (3-2-1 sources above).
- **⚠️ single-source/hypothesis:** ACL-user rebuild gap (internal code walk — no external source; flagged for internal verification, not asserted as fact).

## Recommendation
Do NOT redesign anything. The architecture is already on the canonical multi-tenant pattern — arguably stronger than average (hourly cadence, absence-based monitoring with monitor-the-monitor, drift guards). #2313 extends per-tenant coverage to its intended granularity. The material hardening deltas are the follow-ups above (ACL-rebuild verification, scheduled drills, KMS/rotation, R2 immutability decision, geo decision, cadence-label truthfulness), all small and additive. Everything erasure-related in #2304 already matches practice. Proceed with #2313 Option A; fold the deltas into that issue's scope or adjacent issues after owner pick.
