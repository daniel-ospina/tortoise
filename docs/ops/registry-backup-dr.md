# Registry/Knowledge-Graph Backup DR — Runbook (#596)

> "registry" naming is retained from the registry-era design — the content is
> **per-team knowledge graphs** (control-plane metadata migrates to Supabase
> under #669).

## Architecture
- **Driver:** `.github/workflows/registry-backup-cron.yml` (hourly, GH Actions) → internal-key endpoints. Independent failure domain — an OOM crash-loop (#545) must not blind the pipeline.
- **Watcher (driver-disabled leg):** in-process read-only staleness daemon (spawned in `_lifespan`) that files GitHub issues + pushes Telegram ITSELF — covered by construction when the workflow is disabled.
- **Direct R2 leg (app-down leg):** the driver computes per-team freshness from R2 prefixes (aws CLI) — independent of `/status`.
- **Alert sink (dual-channel):** GitHub issue (agent) + Telegram push (human), R2 create-once per-incident dedup (`ops/alerts/{KIND}/{team}.json`, delete-to-resolve), GH-search fallback, pending-push retries.

## R2 layout
- `backups/{team}/{ts}_{rnd}/dump.enc` + `manifest.json` — per-team archives (retention: 24 hourly + 7 daily + 4 weekly, `keep_hourly`).
- `ops/teams/{team}/state.json` — transition-guard counts.
- `ops/state.json` — team count (enumeration-delta guard) + sweep timestamps.
- `ops/watcher-heartbeat.json`, `ops/driver-heartbeat.json` — mutual supervision.
- `ops/alerts/`, `ops/pending-push/`, `ops/simulate/`, `ops/suppression.json`.

## Alert taxonomy + triage
| Kind | Meaning | Triage |
|---|---|---|
| STALE | a team's newest archive is older than `BACKUP_STALE_THRESHOLD_MIN` (90) | Check sweep logs; run the sweep; R2 connectivity |
| NEVER_BACKED_UP | a team exists with no archive yet | Confirm team is new; if old, investigate |
| METADATA_LOST | archives exist but team state object missing | Re-run sweep (state re-created) |
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
- **Drill endpoint:** `POST /v1/internal/backups/drill` `{team_id, backup_key}` — internal-key only; restores into `_drill_*` scratch (live-phase binds scratch; registry end-stamp skipped; ≥1h cooldown). Zero production writes — asserted server-side.
- **Production restore (`drill:false`) is NOT in scope (501)** — restore-and-rotate machinery retired with the registry (#669).
- **Rollout drill:** operator-invoked (documented commands in the drill workflow). Requires ≥1 team archive; in the chronic 0-teams state run against a seeded scratch graph or defer with a recorded reason. **Re-drill after any restore-path code change, R2 layout change, or key rotation.**
- **Mid-drill crash:** boot GC sweeps `_drill_*`/`registry_drill_*`/`*_restore_*`/`*_pre_restore_*` older than 6h.

## Operator actions
- **Suppression:** write `ops/suppression.json` `{"KIND": {"until": "ISO"}}` to pause a kind.
- **Re-baseline:** `POST /v1/internal/backups/re-baseline` `{team_id}` after verifying a DATA_LOSS_CANDIDATE is a false positive.
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
