---
title: "Registry/Knowledge-Graph Backup DR — Runbook (#596)"
type: operations
domain: platform
doc_status: live
created: 2026-08-08
issue: 596
ownedBy: epistemic-team
subjects:
  team: epistemic-team
aboutObjects:
- tortoise-hosted-platform
---

# Registry/Knowledge-Graph Backup DR — Runbook (#596)

> "registry" naming is retained from the registry-era design — the content is
> **per-team knowledge graphs** (control-plane metadata migrates to Supabase
> under #669). Since #2313 the sweep covers EVERY active graph of a team
> (the default + custom graphs), each with its own archives, state, retention
> and staleness incidents.

## Architecture
- **Driver:** `.github/workflows/registry-backup-cron.yml` (hourly, GH Actions) → internal-key endpoints. Independent failure domain — an OOM crash-loop (#545) must not blind the pipeline.
- **Watcher (driver-disabled leg):** in-process read-only staleness daemon (spawned in `_lifespan`) that files GitHub issues + pushes Telegram ITSELF — covered by construction when the workflow is disabled.
- **Direct R2 leg (app-down leg):** the driver computes the DEFAULT graph's freshness from R2 prefixes (aws CLI) — nested `backups/{team}/default/` + legacy flat (`backups/{team}/2…`, the pre-#2313 default dumps; a legacy-flat classification index #2370 excludes C5-era custom flats when present) — independent of `/status`. A fresh CUSTOM graph can never mask a stale default (#2375).
- **Alert sink (dual-channel):** GitHub issue (agent) + Telegram push (human), R2 create-once per-incident dedup (`ops/alerts/{KIND}/{team}.json`, delete-to-resolve), GH-search fallback, pending-push retries.

## R2 layout
- `backups/{team}/{graph}/{ts}_{rnd}/dump.enc` + `manifest.json` — per-GRAPH archives (#2313; the default graph uses the literal `default` segment; custom graphs their control-plane id). Retention per graph: 24 hourly + 7 daily + 4 weekly (`keep_hourly`) ≈ **35 objects/pool** — keep ALL dumps younger than 24 h, then the NEWEST per UTC day within the 7-day horizon, then the newest per ISO week (4). #2373: day-bucket anchors — the pre-#2373 implementation kept one anchor per UTC HOUR-bucket (~172 objects/pool over the horizon); #2319's lock-window math uses the ~35 figure. Pre-#2313 team-level flat objects (`backups/{team}/{ts}_{rnd}/…`) are the DEFAULT graph's legacy archives — read-bucketed as default, drained by the sweep's per-team legacy prune.
- `ops/teams/{team}/state.json` — legacy transition-guard counts (mirror of the default graph's per-graph state; pre-#2313 consumers).
- `ops/teams/{team}/graphs/{graph_id}/state.json` — per-graph transition-guard counts (#2313).
- `ops/state.json` — team count (enumeration-delta guard) + sweep timestamps.
- Alerts are keyed per (kind, subject): team incidents use the team id; CUSTOM-graph incidents use `"{team}:{graph}"` (#2313) — the same subject re-baseline resolves and the watcher opens.
- `ops/watcher-heartbeat.json`, `ops/driver-heartbeat.json` — mutual supervision.
- `ops/alerts/`, `ops/pending-push/`, `ops/simulate/`, `ops/suppression.json`.

## Alert taxonomy + triage
| Kind | Meaning | Triage |
|---|---|---|
| STALE | a graph's newest archive is older than `BACKUP_STALE_THRESHOLD_MIN` (90) — subject `team` (default) or `team:graph` (custom) | Check sweep logs; run the sweep; R2 connectivity |
| NEVER_BACKED_UP | an active graph exists with no archive yet (custom-graph incidents carry `team:graph`) | Confirm graph is new/empty; if old, investigate |
| METADATA_LOST | archives exist but the graph's per-graph state object missing | Re-run sweep (state re-created) |
| BACKUP_SET_MISSING | state exists but no archives (bulk delete/erroneous prune) | Investigate R2; restore from a retained archive if possible |
| DRIVER_DOWN | driver heartbeat stale (> 4h) — workflow disabled/dead | Re-enable the workflow; GH 60-day auto-disable |
| R2_DOWN | R2 unreachable (driver-side signal) | Check R2 creds/billing/bucket policy |
| ALERTER_DOWN | daemon's GitHub PAT dead (`gh_ok: false`) | Rotate `DR_ISSUES_PAT` |
| APP_DOWN | app unreachable from the driver | Fly health; cold-start OOM (#545) |
| WATCHER_DOWN | watcher heartbeat stale (daemon dead) | Check app logs; restart |
| LIVENESS_NO_WORK | driver ran but did nothing (sweep skipped + reconcile empty) | Verify teams exist; otherwise expected pre-beta |
| SIZE_GUARD_ABORT | team graph > 100k nodes — dump aborted | Investigate graph growth; raise limit deliberately |
| DATA_LOSS_CANDIDATE | a team's node count dropped >50% (or >0→0) | **Manual close only** — verify + re-baseline or restore |
| P0_GUARD_FAIL | a dump named the wrong graph or was empty — objects deleted | Investigate the sweep; alert auto-consolidates |

## Restore / drill
- **Drill endpoint:** `POST /v1/internal/backups/drill` `{team_id, backup_key}` — internal-key only; restores into `_drill_*` scratch (live-phase binds scratch; registry end-stamp skipped; ≥1h cooldown). Zero production writes — asserted server-side. The archive's key shape names its graph; the target resolves through the ACTIVE-graph seam — **drilling a deleted/quarantined graph's archive is refused (409)** (#2313 tombstone guard, #2304).
- **ACL rebuild after full-platform restore:** a DR into a fresh FalkorDB server restores graph DATA from R2 — per-graph ACL server users do NOT live in the graph namespace. Run `POST /v1/internal/backups/acl-reconcile` (internal key) to replay the idempotent `create_acl_user` upsert for every active custom graph of every eligible team (default graphs ride the team-scoped ACL; tombstoned graphs never touched).
- **Production restore (`drill:false`) is NOT in scope (501)** — restore-and-rotate machinery retired with the registry (#669).
- **Rollout drill:** operator-invoked (documented commands in the drill workflow). Requires ≥1 team archive; in the chronic 0-teams state run against a seeded scratch graph or defer with a recorded reason. **Re-drill after any restore-path code change, R2 layout change, or key rotation.**
- **Mid-drill crash:** boot GC sweeps `_drill_*`/`registry_drill_*`/`*_restore_*`/`*_pre_restore_*` older than 6h.

## Operator actions
- **Suppression:** write `ops/suppression.json` `{"KIND": {"until": "ISO"}}` to pause a kind.
- **Re-baseline:** `POST /v1/internal/backups/re-baseline` `{team_id}` (+ optional `graph_id`, default `"default"`) after verifying a DATA_LOSS_CANDIDATE is a false positive. Custom-graph incidents resolve under `"{team}:{graph}"`; the default under the bare team.
- **Simulate (staging):** `POST /v1/internal/backups/simulate-stale|recover` (gated on `BACKUP_SIMULATE_ENABLED`) — proves detection→filing→dedup ≤ 2× poll cadence.
- **Secrets:** `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`/`DR_ISSUES_PAT`/`BACKUP_ALERT_ASSIGNEE` are Fly + GH secrets; the Telegram pair exists in both (daemon-side and driver-side legs). `BACKUP_SWEEP_ENABLED=true` is set by deploy-hosted.yml only when all required secrets are present (fail-closed).

### REGISTRY_STREAM_KEY — out-of-band Fly secret (#661)

**Purpose:** encrypts sweep backup archives (dump.enc) with a key that is
NEVER present in GitHub. This breaks the GH-trust-boundary dependency:
even a GH-capable collaborator cannot decrypt registry backup archives
because the key lives only on Fly, set by the operator out-of-band.

**Setup (operator, once):**
```bash
# Generate the key (do NOT commit or share):
python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"

# Set it directly on Fly (NEVER add to GitHub secrets):
fly secrets set REGISTRY_STREAM_KEY=<generated-key> --app tortoise-y4mjjq
```

**Deploy safety:** `deploy-hosted.yml` deliberately EXCLUDES
`REGISTRY_STREAM_KEY` from the secret-sync loop — there is no active
negative check (the workflow can't check Fly-side state), but the key is
never read from `secrets.*` in the YAML. A missing key causes the sweep
endpoint (`POST /v1/internal/backups/sweep`) to 503 fail-closed — the app
boots and serves normally, but no sweep backups are created until the key
is set.

**Rotating:**
```bash
# 1. Generate a new key
# 2. Set it on Fly (overwrites the old key):
fly secrets set REGISTRY_STREAM_KEY=<new-key> --app tortoise-y4mjjq
# 3. Deploy to pick up the new secret value:
fly deploy --app tortoise-y4mjjq
# 4. Old archives remain decryptable with the OLD key — recover via manual
#    decryption with TORTOISE_BACKUP_KEY (GH-secret, retained for recovery).
```

**Verification (operator, post-setup):**
```bash
# Trigger a drill against the oldest archive to confirm the key works:
curl -sS -X POST -H "Authorization: Bearer $FASTAPI_INTERNAL_KEY" \
  -H "Content-Type: application/json" \
  -d '{"team_id":"<team>","backup_key":"backups/<team>/.../dump.enc"}' \
  https://api.premiselabs.co/v1/internal/backups/drill
```

## Known residuals (accepted)
- **App down AND driver disabled simultaneously** — no alert (documented residual; reopen condition: first unattended app-down).
- **NEVER detection while the app is down** — daemon-only; a never-backed-up team during an outage is silent until recovery.
- **GH single-provider scheduling axis** — the driver terminates in GitHub; a GH incident delays backup triggers (the daemon still alerts).
- **R2 outage** — neither fabricates (UNKNOWN on fresh boot) nor silences (GH-search fallback + driver R2_DOWN) once a known-good baseline exists.

### Post-#2313 residuals (recorded 2026-09-06 audit — #2378)
- **Watcher heartbeat is per-team only** — `ops/watcher-heartbeat.json` carries the per-team tri-state; the watcher's per-graph states live in the daemon's in-process last-status (not persisted). Per-graph SWEEP outcomes (totals, failures, consecutive-error streaks) surface on `GET /v1/internal/backups/status` → `last_sweep` (#2372). A daemon restart loses the in-process per-graph watch until the next poll.
- **Legacy-flat mislabel under control-plane failure** — with the control plane down (or before a team's first legacy-flat classification index exists, ≤1 sweep after #2370 deploys), legacy flat archives on `GET /backups` fall back to the DEFAULT graph bucket even when they were C5-era custom dumps (#2370 index makes this the exception). Restore of a legacy flat custom archive is refused regardless (cross-graph guard).
- **Tombstoned-graph archive pools are never pruned** — per-graph prune runs only for enumerated ACTIVE graphs and the team-wide drain skips nested keys, so a deleted graph's `backups/{team}/{gid}/` pool accumulates until #2304's purge decision lands (its research item 4 covers backup-artifact disposition; the mechanism is recorded here).
- **Failing-default drain drops C5-era custom history (#2415)** — while the DEFAULT is failing, the sweep's legacy-flat cleanup prunes pre-#2313 custom-era FLAT dumps of ACTIVE customs that backed up THIS pass (their current data is protected by the same-run nested pool, but the flat was the custom's only PRE-cutover historical snapshot; tombstoned/errored/unresolvable flats are left in place — their disposition is #2304's purge decision).
