# Problem Convergence — #596 (issue-scoping Phase 2 + 2.5)

**Date:** 2026-08-08

## Confirmed Problem Definition (v3 — after problem-verify cycles 1–2)

The hosted backup pipeline is **silent, unverifiable, and unsafe to schedule in its current form**:

1. **Nothing drives it** — no cron exists anywhere; the only "scheduled" precedent (`/v1/internal/reconcile`, "Called by an external cron") is itself **uninvoked** — the pattern being copied is the very silent-no-op class this issue exists to prevent.
2. **Nothing watches it — and the watchdog must not share the driver's infrastructure.** No heartbeat consumes `Team.backup_latest_at` (itself a best-effort post-upload stamp); no alert channel exists (GitHub-issue filing lacks push/dedup/severity). **Dual-watcher design:** (a) the external driver's pre-flight staleness check covers app-down (#545 crash-loop); (b) an in-process, out-of-band staleness check (read-only: stamp age / R2 latest-object age → file deduplicated alert) covers driver-disabled and GH-Actions silent-skip when the app is up. The out-of-band check must NOT be a second GH Actions workflow (same kill-switch domain). Staleness classification distinguishes never / stamp-missing / stale, and must reconcile `backup_latest_at` against R2 object state so a **registry restore** (which rolls back Team-node stamps) does not cause false-stale alarms.
3. **Restore is unproven and the actual DR surface is unbacked** — the registry/control-plane graph (auth, tenant map, API keys, memberships) has no operator-controlled backup and no restore path. The tenant `/backups/restore` authenticates through the very graph it would restore; the available mechanism is an internal-key endpoint mirroring `reconcile`. Restoring the **auth graph** resurrects revoked API keys (`get_current_team` filters `revoked_at IS NULL`) and rolls back post-snapshot writes — a security hazard requiring a **restore-and-rotate procedure** (post-restore key invalidation), not a pure-DR win.
4. **The specced per-team scheduler would ship as a guaranteed silent no-op** — zero Pro teams exist (every provision path hardcodes `tier:'free'`, `backup_enabled:false`; no flip path; #296 deferred). The team sweep is split out to #655 (env-flagged, gated on the no-op assertion).
5. **Dump execution must be isolated for the OOM-vector surface — non-negotiable** — full-graph in-memory dumps of TEAM graphs (hosted_backup.py:126–158) run inside the 4GB OOM-documented app process (#545); an unthrottled in-process team sweep is a cross-tenant outage vector. **Resolution (problem-verify cycle 3):** team dumps (#655) must never execute in the API process (out-of-process worker or memory-bounded), and #596's registry dump runs in-process as a **bounded carve-out** — size-guarded (abort + alert > 100k nodes), serialized, never concurrent. Acceptance: **team sweep dumps never execute in the API process**; registry dump bounded by the size guard.

**The problem is:** control-plane (registry) DR with a real restore path + making backup health **observable** and restore **provable** (with restore-and-rotate + freshness safeguards), with the per-team scheduler as a **separate, later issue** (#655). Registry cadence + observability + restore are the #596 envelope (standard-complexity sized).

**Target acceptance:** registry RPO ≤ 1h typical / ≤ 2h worst-case (GH Actions jitter acknowledged — sub-hourly driver cadence or explicit worst-case restatement required); operator-initiated RTO ≤ 1h **measured from app-bootable state** (app recovery excluded; restore assumes control-plane quiescence or bounded write-loss ≤ RPO); at least one executed verification restore against a real R2 archive **including a content spot-check (sampled node props + edges)** and **tenant API-key rotation within RTO** (TORTOISE_BACKUP_KEY rotation out of scope); sub-daily retention for the hourly registry cadence (supersedes diverge's "retention out of scope"); per-incident alert dedup with dedup state in R2 (redeploy-surviving); **simulated stale/missing stamp → deduplicated alert within ≤ 2× check cadence (and ≤ worst-case RPO)**; **a scheduled run with no work items (registry skipped / reconcile empty) emits a deduplicated liveness alert**; alert sink = GitHub-issue filing (assigned + labeled) declared as the notification contract, no push channel in this envelope.

## Why This Anchor (merge rationale — controller)

- **F4 (registry/control-plane DR)** leads: it is the highest-impact surface (platform continuity — the graph that auth itself reads), tier-independent, buildable today, and its absence falsifies the issue's own E2E leg "registry restorable." Vendor FalkorDB snapshots exist but are not operator-controlled/portable → app-level logical backup + restore path is the gap. (Confidence 84 — Agent A anchor.)
- **F2 (silent operation/observability)** carries the failure-class definition: both "nothing runs" and "ran but produced nothing" are equally invisible today; the issue's own E2E legs ("failure raises an alert", restore freshness) are observability statements. (Confidence 85 — Agent B anchor.)
- **F1 (entitlement chain)** is a **constraint, not the anchor**: its empty-set fact makes the naive scheduler a day-one silent no-op; its fix (billing, #296) is deliberately deferred. Adopted as the no-op assertion + alert-until-entitlement design.
- **F3 (restore confidence)** is folded in: count-only verification is real; the sharpest concrete point (registry no-restore-path + circular auth) is F4's. Mechanism bounded: ≥1 executed verification restore + registry restore runbook (NOT recurring drills — out of scope, per diverge boundary).
- Both converge agents verified the same facts independently (84/85 confidence): free-tier hardcode ×3 paths (hosted_api.py:392–393, 1044–1045; sdk.py:3166), no tier-flip call sites, no heartbeat consumer, MemoryStorage/fake-boto3-only tests, registry restore circular auth (with internal-key escape hatch), zero `schedule:` in 10 workflows, count-only verification, OOM-documented 4GB VM.

## Rejected Framings (when they WOULD have been better)

- **F1 as anchor** — would have been better if #296 billing shipped concurrently (then the scheduler has a population and the entitlement gap is the blocker). Rejected: would convert #596 into product/billing work the align gate deliberately defers. Split out: team sweep → #655.
- **F3 as anchor** — would have been better if the platform had paying tenants with real restore exposure (drill cost justified). Rejected at zero-tenancy scale: recurring drills read as gold-plating; bounded to one executed verification restore (with content spot-check).
- **F2 as anchor alone** — would have been better if the registry were already covered (vendor snapshots signed off as sufficient DR). Rejected: observability of an unbacked highest-value surface is loud alarms with no recovery path.

## Falsification Check

This definition is wrong if: (1) an operator-controlled registry restore path already exists and is exercised; (2) vendor FalkorDB snapshots are signed-off DR for the control plane (then registry half shrinks to observability-only); (3) Pro/Team tenants already exist in production via a flip path not found; (4) an out-of-repo heartbeat already consumes `backup_latest_at`.

## problem-verify — Cycle 1 (2 verifiers)

- Verifier A: P0=0, P1=2, P2=6, P3=5, P4=1 — P1s: OOM blast radius dropped from problem; watchdog-can't-watch-itself.
- Verifier B: P0=0, P1=1, P2=3, P3=3, P4=2 — P1: registry-restore resurrects revoked keys / rolls back writes (auth-graph hazard).
- Controller action: **Fixed all 3 P1s** — dump-execution-isolation constraint (#5); watchdog-liveness requirement (#2); restore-and-rotate + freshness target (#3). P2s: persisted this converge doc; bounded restore-proof mechanism (≥1 executed verification restore + runbook, not recurring drills); sub-daily retention; observability audience = operator-internal (accepted); real-R2-state in acceptance; heartbeat tri-state. P3s: RPO/RTO targets, <100 replaced, per-incident dedup.

## problem-verify — Cycle 3 (2 verifiers)

- Verifier A: P0=0, P1=0, P2=1, P3=3, P4=2. P2: dump-isolation mechanism contradiction — align's primary (external cron → internal endpoints) executes dumps in-process via asyncio.to_thread (hosted_api.py:2589), contradicting v3's non-negotiable 'sweep dumps never execute in the API process'.
- Verifier B: P0=0, P1=0, P2=0, P3=3, P4=3. "v3 is factually sound… remaining items are P3/P4 that do not block the problem definition."
- **Exit condition met (both verifiers no P0/P1).** Pass-through: P2+ incorporated (below).

### Controller incorporation (P2/P3/P4, no re-dispatch)

1. **[P2→RESOLVED] Dump-execution mechanism (the one substantive finding):** recorded — the #596 envelope backs up the **registry** graph only (small: teams × keys × memberships, bounded by tenant count, currently ~0 teams). The registry dump runs **in-process as a bounded carve-out**: size-guarded (if registry graph exceeds 100k nodes → abort + loud alert), serialized (one dump at a time, per-backup lock), never concurrent. The OOM vector is the **team-graph** dump (embedding-heavy, full-graph in-memory) — that is #655's envelope, split out, env-flagged off, and REQUIRED to be out-of-process or memory-bounded when it ships. The align-gate tension is hereby resolved: internal endpoints may execute the registry dump (bounded carve-out) but must never execute unguarded team dumps.
2. **[P3→fixed] Restore-and-rotate key semantics:** post-restore tenant **API-key invalidation** via the existing revocation mechanism (`revoked_at` — re-applying revocations requires a revocation log; the restore runbook must capture pre-snapshot revocations and re-apply them, or rotate all tenant keys as the fallback). **TORTOISE_BACKUP_KEY rotation stays out of scope** (diverge boundary upheld) — restore-provable is bounded to the current key.
3. **[P3→fixed] Alert sink contract:** GitHub-issue filing IS the notification contract (issue assigned + labeled, existing GH notification path) — no push channel in this envelope; declared explicitly so the plan doesn't silently inherit a rejected mechanism. Residual corner (app+driver down) re-evaluated if a push channel ships later.
4. **[P3→fixed] Dedup state location:** dedup state lives in R2 (a `backup-alert-state` object) — survives redeploys (a local file would reset mid-incident); R2 is the shared signal, not shared execution infra.
5. **[P3→fixed] no-op acceptance rewording:** '0 teams → no-op alert' is #655's acceptance; for #596 it reads as **driver-liveness**: 'a scheduled run with no work items (registry skipped / reconcile empty) emits a deduplicated liveness alert'.
6. **[P4s→fixed]** X (stale→alert latency) ≤ 2× check cadence and ≤ worst-case RPO; registry restore assumes control-plane quiescence (app down) or bounded write-loss ≤ RPO; residual corner named as a gap with reopen condition (first unattended app-down).

**Gate result: problem-verify clean after 3 cycles.**
