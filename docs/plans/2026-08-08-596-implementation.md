<!-- research-path: docs/plans/2026-08-08-596-research.md -->

# Knowledge-Graph DR: Scheduled Backup, Observability, Restore-Proof — Implementation Plan (v2.2 — plan-review converged: 3 cycles, no P0/P1, P2-P4 incorporated)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Give the per-team knowledge graphs (the actual customer data in FalkorDB) a scheduled backup driver, dual-watcher alerting with a Telegram + GitHub-issue dual-channel sink, and a proven restore path — closing the #101 "backups exist but nothing runs them" failure class, ready for beta.

**Team:** unknown | **Role:** product-implementer

**Architecture (v4 decision — controller, 2026-08-08):**
- **#596 backs up per-team knowledge graphs** (team namespaces in FalkorDB) — NOT the registry. The registry/control-plane metadata migrates to Supabase under **#669** (managed backups + PITR); any registry backup/restore/restore-and-rotate machinery is explicitly NOT built here (no build-then-delete). Registry sections of the scoping plan (§3.3/§3.5/§3.1-registry-rows) are **superseded by #669**.
- External GH Actions cron driver calls internal-key endpoints on the Fly app (independent failure domain — the app OOM-crash-loops per #545). A read-only in-process staleness daemon (driver-disabled leg) files alerts directly; the driver's own direct R2 freshness check covers the app-down leg.
- Alerts = GitHub issue (agent) + Telegram push (human) — **#673's Telegram leg is ABSORBED into Task 5** (issue #673 closed-as-absorbed). **Telegram creds exist in BOTH Fly (daemon-side pushes) and GH secrets (driver-side pushes for the app-down/daemon-dead legs — send-only token, acceptable exposure, same trust class as the R2 creds already in GH)**; both sinks share the R2 dedup authority (v2.1 P2-1 resolution: driver-filed incidents — APP_DOWN/WATCHER_DOWN/direct-STALE/R2_DOWN/ALERTER_DOWN — also push Telegram).
- Restore = drill-mode scratch-only per team, reusing the SHIPPED `restore_backup` verification stack (#582) — no new restore machinery (revocation capture/rotate-all was registry-auth-graph-specific; retires with #669).
- Team enumeration seam: FalkorDB registry pre-#669 → Supabase `teams` table post-#669. #655 becomes the activation/entitlement piece (tier/backup_enabled gating + flip-on for beta); the sweep MACHINERY ships in #596.

### Pattern Research

Skipped — plan touches zero new third-party dependencies (stdlib `urllib` for GitHub/Telegram HTTP; `boto3`/`cryptography` already in the `[backups]` extra; `aws` CLI preinstalled on GH runners). Telegram Bot API is a plain HTTP POST. Research notes in `2026-08-08-596-research.md`.

### Integration Surface Map

Carried from issue-scoping Phase 6 wiring check + plan-review cycle 1 (v2 fixes):

| Surface | Type | Test layer | Notes |
|---|---|---|---|
| FalkorDB team knowledge graphs | data store | unit (FalkorDBLite) + staging E2E | dump via shipped `create_backup`; per-team; enumeration seam: registry now → Supabase `teams` post-#669 |
| R2 bucket (per-team archives + ops state) | data store | unit (MemoryStorage / fake boto3) | `create_if_not_exists` dedup (+ HEAD/list fallback); 30d capture sweep N/A (no captures — registry-specific) |
| GitHub Issues API | external | unit (mock urllib) + staging | agent-visible ticket; GH-search fallback |
| Telegram Bot API (absorbed #673) | external | unit (mock urllib) + staging | human push; independent of GitHub; `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` required-when-enabled |
| GH Actions driver | external | bash -n + shellcheck + staging dispatch | direct R2 freshness leg (aws CLI, per-team prefixes) |
| Fly app / internal-key auth | api | integration (`internal_client` fixture) | `_check_internal`; drill endpoint internal-key only |
| Fly secrets / deploy-hosted.yml | cross-cutting | deploy E2E | sync new syncable secrets (incl. Telegram); `enabled=true` only when all present |

### Journey Test Map

Skipped — no user-facing journeys (backend/infra). Operator journeys covered by Task 11 (staging E2E checklist).

### Failure Modes

- GH cron silently skipped / 60-day auto-disable → daemon files DRIVER_DOWN (driver-disabled leg).
- App OOM crash-loop while `/status` answers between restarts → driver's direct R2 freshness check files STALE (per-team prefix scan).
- R2 outage → neither fabricates (last-known-good/UNKNOWN) nor silences (GH-search fallback + driver R2_DOWN).
- Telegram down → GitHub issue still files; push retries (pending-push state in R2).
- GitHub down → Telegram still pushes; GH-search dedup fallback.
- **0 teams (chronic pre-beta state)** → NO_TEAMS signal in `/status`, never an incident (never NEVER_BACKED_UP).
- Mid-drill crash → boot GC sweeps scratch graph patterns; drill leaves zero production writes.

---

## Tasks

### Task 1: Config module (`backup_config.py`)

**Intent:** Single validated source for the env contract — fail-closed default (`BACKUP_SWEEP_ENABLED=false`), required syncable secrets fail fast when enabled, Telegram secrets required-when-enabled (a silently-dead human channel is the #101 class), `GH_REPO` default `daniel-ospina/tortoise`.

**Acceptance:** Config parses/validates all vars (sweep thresholds, retention, size guard, Telegram, GitHub, watcher cadence); defaults unit-tested; missing syncable secret when enabled → clear boot error; `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` required-when-enabled (missing → boot error when alerting on); `GH_REPO` defaulted with explicit-empty → boot error when enabled.

**Files:**
- Create: `tortoise/backup_config.py`, `tests/test_backup_config.py`

### Task 2: `keep_hourly` retention extension

**Intent:** Sub-daily cadence needs sub-daily retention; `keep_hourly=0` preserves existing behavior byte-for-byte.

**Acceptance:** `prune_backups(..., keep_hourly=24, keep_daily=7, keep_weekly=4)` per-team; **semantics (v2.2 P2-1 — the interplay that pins retention): when `keep_hourly > 0`, the hourly window (keep ALL backups younger than `keep_hourly` hours) PLUS hour-bucket anchors (one per hour-bucket, bounded by the `keep_daily` horizon as anchor count) REPLACE the daily keep-all rule** — the existing "keep all < keep_daily days" branch is skipped in keep_hourly mode (otherwise anchors never bind and each team retains ~168 objects/week at hourly cadence); `keep_hourly=0` restores existing behavior byte-for-byte; newest-never-deleted; idempotent; **discriminating boundary test (v2.2): backups at hours {1, 23, 25, 49, 167, 169} with keep_hourly=24/keep_daily=7/keep_weekly=4 → assert the EXACT survivor set (~35 objects — the three keep-all/anchors variants must fail it)**; boundary cases (23:59/00:01, ISO week) tested; all 67 existing hosted_backup tests green; team key-shape round-trip test passes.

**Files:**
- Modify: `tortoise/hosted_backup.py`, `tests/test_hosted_backup.py`

### Task 3: Conditional-create storage primitive

**Intent:** R2 create-once (`IfNoneMatch='*'`) is the dedup linearization point; alert store + driver depend on it.

**Acceptance:** `R2Storage.create_if_not_exists(key, body)` → True on create / False on 412 (monkeypatched boto3); **HEAD-check + list-based adoption fallback implemented** (scoping §10 fallback — the dedup authority must not silently degrade); `MemoryStorage` identical.

**Files:**
- Modify: `tortoise/hosted_backup.py`, `tests/test_hosted_backup.py`

### Task 4: Backup sweep routine (`run_backup_sweep`)

**Intent:** The driver's core action — enumerate teams (seam), size-guard each, dump each team graph via the SHIPPED `create_backup` (per-team `backups/{team}/` stream), empty-content transition guard, prune. NO registry machinery (superseded by #669).

**Acceptance:** Enumerates teams from the seam (registry now → Supabase post-#669); **0 teams → `{"status":"no_teams"}` only on a CONFIRMED-EMPTY enumeration — an enum-source failure (registry down at first sweep) is NOT classified as chronic NO_TEAMS (mirrors `list_backups` fail-closed; v2.2 P4-2)** — never fake success, never an incident; per-team dump with size-guard abort (>100k → event + alert, no dump); DATA_LOSS_CANDIDATE per team fires only on transition (>0→0 or >50% drop vs that team's prior counts persisted in `ops/teams/{team}/state.json`), steady-0 team = signal not incident; **repeated-empty alert identity (v2.2 P3): the P0-guard's empty-dump alert ADOPTS/REUSES the open DATA_LOSS_CANDIDATE incident (stable key kind+team while active, delete-to-resolve on recovery) — a wiped team must produce ONE issue + ONE Telegram per incident, not per hour; two-cycle test asserts one issue**; **enumeration-delta guard (v2.1 P2-2): prior team count persisted in `ops/state.json`; an N>0→0 team-universe transition files an incident (a wiped enumeration source must not degrade silently to the chronic NO_TEAMS state); chronic-0 stays non-incident; a partial wipe (N→N-1) of a never-backed-up team is an accepted residual (no R2 prefix → no STALE leg; v2.2 P4-2)**; **P0-guard per team: `manifest.graph_name` == `f'team_{team_id}'` derived from the SEAM enumeration (independent of the dump projection — not tautological) AND `node_count >= 1`; wrong-name or empty ⇒ delete the just-uploaded objects + alert**; serialized per-team lock; prune per team.

**Files:**
- Create: `tortoise/backup_sweep.py`, `tests/test_backup_sweep.py`

### Task 5: Alert store — GitHub issue + Telegram push (absorbs #673)

**Intent:** The alert lifecycle — create-once dedup (R2 linearization point, 412-adopt with issue_number branch), GitHub issue filing (urllib, `BACKUP_ALERT_ASSIGNEE` as assignee — the assigned+labeled contract), **Telegram push (absorbed from #673: open → message with issue link; resolve → "resolved" message; pending-push retry state in R2)**, suppression, GH-search fallback, delete-to-resolve.

**Acceptance:** Create-once-first/file-second with adoption branch (placeholder without issue_number → adopter becomes filer — the create-then-die window never leaves an incident silent); per-incident dedup (redeploy mid-incident → 1 issue + 1 Telegram message); recovery → close + delete + "resolved" Telegram; Telegram API down → issue still files, push retried **from the R2 pending-push state by the DAEMON on its next poll (owner pinned, v2.1)**; filing-failure never deletes dedup objects; `dr:backup` label idempotent; issues created with `BACKUP_ALERT_ASSIGNEE`; **#673 closed-as-absorbed after this task lands. Per-team R2 object map (v2.1 P3-1): `backups/{team}/{ts}_{rnd}/dump.enc`+manifest, `ops/teams/{team}/state.json` (source, latest_backup_at, latest_object_key, node_count, counts), `ops/state.json` (team count for the delta guard, watcher/driver heartbeats), `ops/alerts/{KIND}-{ts}.json`, `ops/pending-push/`, `ops/simulate/`, `ops/suppression.json`.**

**Files:**
- Create: `tortoise/github_issue.py`, `tortoise/alert_store.py`, `tests/test_github_issue.py`, `tests/test_alert_store.py`
- Modify: `tortoise/backup_config.py` (Telegram vars)

### Task 6: Staleness computation + daemon loop (`backup_watcher.py`)

**Intent:** The driver-disabled leg — read-only in-process daemon in `_lifespan` computing PER-TEAM tri-state staleness and filing via the alert store; R2-outage last-known-good/UNKNOWN; watchdog + timeouts + memory guard; simulate hooks.

**Acceptance:** `compute_status` table-tested (per-team never/stamp-missing/stale, **NO_TEAMS steady-state → signal not incident**, **per-team universe = SEAM ENUMERATION ∪ R2 team prefixes under `backups/` (v2.2 P3-1): `never` computed over seam-enumerated teams lacking any R2 archive; R2-prefix set governs STALE/BACKUP_SET_MISSING — a team whose archives age without a live enum entry still STALEs; NEVER-detection when the app is down = accepted residual, runbook-documented**, post-restore grace, thresholds, DRIVER_DOWN, boot grace, R2-down UNKNOWN on fresh boot, BACKUP_SET_MISSING per team on confirmed-empty); daemon files once, adopts on restart, closes+deletes on recovery; **process `ops/pending-push/` retries each poll (v2.2 P4 — the Task 5 retry owner bound into the loop)**; no graph writes (asserted); simulate-stale → issue ≤2 polls; **watchdog restarts an exited daemon thread (unit test stops the poll loop); explicit socket timeouts; RSS trend memory guard; expired simulate objects ignored**; spawn gated on config + test-env signal.

**Files:**
- Create: `tortoise/backup_watcher.py`, `tests/test_backup_watcher.py`
- Modify: `tortoise/hosted_api.py` (`_lifespan` spawn), `tortoise/backup_config.py`

### Task 7: Internal endpoints (sweep / status / heartbeat / simulate / re-baseline / drill)

**Intent:** The driver + operator surface — internal-key auth, per-team response shapes.

**Acceptance:** `POST /v1/internal/backups/sweep` (202/skip/lock semantics, per-team results), `GET /v1/internal/backups/status` (per-team tri-state, NO_TEAMS, counts, daemon-not-running, r2_ok, gh_ok, watcher heartbeat age), `POST /v1/internal/driver/heartbeat`, simulate-stale|recover (403 when disabled), re-baseline (operator-gated, per team), **drill endpoint (`drill:true` only; internal-key auth; `target_graph` = `^_drill_` scratch only (v2.2 P4 — `registry_drill_` vestige trimmed); reuse the SHIPPED `restore_backup` verification stack; ISOLATION-CHECK DECOUPLING (v2.1 P2-2): manifest/payload graph-isolation checks bind to the CANONICAL `graph_name` (`restore_backup(team_id, graph_name=canonical, target_graph=scratch)`), ALL live-phase ops (empty-guard read, pre-restore copy, delete, swap) bind to `target_graph`; exact-name spy asserts no destructive call references any live team graph; `restore_backup`'s end-stamp (`Team.backup_restored_at`) SKIPPED when `drill:true` — **a `drill:true` flag param on `restore_backup` in addition to `target_graph` (v2.2 P4 — listed in Files)** — integration test asserts registry write-count == 0 across a drill; ≥1h cooldown, in-memory, resets on restart — stated)**; **boot GC sweeps `_drill_*`/`registry_drill_*` + `*_restore_*`/`*_pre_restore_*` (suffix forms matching shipped staging names) older than N hours**; integration tests via `internal_client`. **`drill:false` production restore: NOT in scope (returns 501) — restore-and-rotate retires with the registry (#669).**

**Files:**
- Modify: `tortoise/hosted_api.py`, `tortoise/hosted_backup.py` (`target_graph` + `drill:true` end-stamp-skip flags), `tests/test_hosted_api.py`, `tests/test_backup_sweep.py`

### Task 8: Driver workflow + script

**Intent:** The app-down/crash-loop leg — hourly GH cron with pre-flight classification, direct R2 freshness (PER-TEAM prefix scan via aws CLI), kill-switch branch, reconcile ride-along (#654, skipped on 202), heartbeat, self-heal.

**Acceptance:** `registry-backup-cron.yml` (hourly cron + dispatch inputs, permissions issues:write, concurrency group; **"registry" naming retained from the registry-era design — content is per-team (v2.2 P4)**); script shellcheck-clean; APP_DOWN classification (connect-failure vs app-503/429); kill-switch skip; direct R2 STALE independent of /status (**team list from R2 top-level prefixes under `backups/` — app-down-independent; per-team thresholds from workflow env `BACKUP_STALE_*` PINNED IDENTICAL to the daemon's config thresholds in the runbook + Task 11 E2E (v2.2 P4-1 — divergence would flap STALE/recovery between the two watchers); a never-backed-up team while app is down = accepted residual, runbook-documented**); listing-failure never confirmed-empty; R2_DOWN + ALERTER_DOWN legs; **files WATCHER_DOWN when `/status` reports stale watcher heartbeat + `r2_ok: true`**; **self-heal closes APP_DOWN/WATCHER_DOWN/STALE EXCLUDING simulate-triggered STALE**; **driver-side filings participate in the same create-once dedup (aws CLI conditional put) with GH-search fallback AND push Telegram (TELEGRAM_BOT_TOKEN/CHAT_ID as GH secrets — v2.1 P2-1: the app-down/daemon-dead legs are exactly when the human channel matters)**; reconcile skipped when run returns 202; staging dispatch produces objects + heartbeat; recovery closes issues.

**Files:**
- Create: `.github/workflows/registry-backup-cron.yml`, `.github/scripts/registry-cron.sh`

### Task 9: Drill workflow + rollout execution

**Intent:** Prove the restore path (a backup never restored is a guess) — operator-invoked rollout drill + dispatch-only drill workflow.

**Acceptance:** Rollout drill executed within 2 weeks of completion (operator-invoked, documented commands; **requires ≥1 team archive — in the chronic 0-teams state, run against a seeded scratch graph or defer with a recorded reason (v2.1 P3)**; operator supplies `team_id`+`backup_key`, default = newest non-empty archive across teams): counts match, content spot-check 0 mismatches, swap rehearsal passes, indexes present, **no production safety export created (asserted — the pre-restore copy is of the scratch target; the meaningful check is graph-level count before == after)** , production graph provably untouched (graph-level count before == after), scratch cleaned, no tenant 503s, endpoint-side RTO < 15 min; metrics posted as an issue comment.

**Files:**
- Create: `.github/workflows/registry-restore-drill.yml`, `.github/scripts/registry-drill.sh`

### Task 10: Wiring + secrets + docs

**Intent:** Secrets, config, runbook — deploy-hosted.yml syncs new syncable secrets (incl. Telegram) and sets `BACKUP_SWEEP_ENABLED=true` only when all present; `.env.example`; runbook; schema-doc fix. **Same-PR constraint: the deploy change lands in the same PR as Task 1's config fail-fast.**

**Acceptance:** deploy-hosted.yml updated (sync Telegram + new secrets; `enabled=true` only when ALL syncable secrets present — **the gate condition names `TELEGRAM_BOT_TOKEN`+`TELEGRAM_CHAT_ID` explicitly**; **create GH secrets `INTERNAL_API_URL`, `GITHUB_ISSUES_PAT`, `BACKUP_ALERT_ASSIGNEE`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` BEFORE the deploy change that can set `enabled=true`; post-deploy verify `/status` reports `enabled: true`**); `.env.example` covers all vars; `docs/ops/registry-backup-dr.md` (architecture, R2 layout per-team, alert taxonomy incl. Telegram triage + assignee + the app-down/NEVER-detection blind spots, drill execution, suppression, simulations, secret rotation incl. Telegram, GH single-provider axis, re-drill mandate); **`docs/00_index.md` CREATED (routing index entry — verified it does not exist)**; `docs/registry-graph-schema.md` updated. **Execution note (v2.1 P4): Tasks 1-10 land as a SINGLE PR; the agent runs the `gh secret set` commands at Task 10 before merging; the deploy change references only secrets that exist.**

**Files:**
- Modify: `.github/workflows/deploy-hosted.yml`, `.env.example`, `docs/registry-graph-schema.md`
- Create: `docs/ops/registry-backup-dr.md`, `docs/00_index.md`

### Task 11: Staging E2E verification

**Intent:** Proof of the headline claims (carried from scoping §6) — driver-disabled detection, crash-loop coverage, dedup, retention, re-baseline.

**Acceptance:** E2E checklist executed against staging: dispatch → per-team objects in R2; simulate-stale → STALE issue + Telegram ≤20 min with label + assignee; redeploy mid-incident → no duplicate; **driver-disabled leg: disable workflow → daemon files STALE itself + DRIVER_DOWN**; app-down leg (simulate_app_down) → APP_DOWN, exit 0, self-heal; **crash-loop leg: restart app repeatedly → driver's direct R2 check files STALE**; watcher-down → WATCHER_DOWN; retention backdated simulation; re-baseline E2E; restore-key absence N/A (drill internal-key only); OOM sanity across cycles.

**Files:**
- Test: staging environment (checklist in `docs/ops/registry-backup-dr.md`)

---

## Superseded / retired (do not build)

- **Registry backup, registry restore, restore-and-rotate, `REGISTRY_RESTORE_KEY`, registry drill-DB** — retired by **#669** (Supabase managed backups + PITR). No build-then-delete.
- **`drill:false` production restore** — 501; restore-and-rotate was registry-auth-graph-specific.
- **Per-team sweep entitlement gating (tier/backup_enabled, flip-on for beta)** — **#655** (activation issue; machinery ships here).
