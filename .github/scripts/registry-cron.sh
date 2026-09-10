#!/usr/bin/env bash
# Per-team knowledge-graph backup driver (#596) — the app-down/crash-loop leg.
#
# Pre-flight classification is the critical piece: an OOM crash-loop (#545)
# answers /status between restarts, so the driver's DIRECT R2 freshness check
# (aws CLI) is the only signal that covers the app-down case. The direct-R2
# leg runs BEFORE the APP_DOWN early-exit (review P2-2) — it is genuinely
# app-independent.
#   1. aws preflight (R2_DOWN on failure) + DIRECT R2 freshness (per-team)
#   2. GET /status (classify: connect-fail → APP_DOWN; app-503/429 → up)
#   3. kill-switch check — a DISABLED sweep is NOT automatically intentional:
#      config_error → SWEEP_CONFIG_ERROR; stale R2 pool → SWEEP_OFF_STALE;
#      storage_error → R2_DOWN; only a genuinely deliberate pause (no config
#      error, fresh pool) stays silent (#2796)
#   4. POST /backups/sweep (202 = lock held)
#   4b. enabled-but-nothing — a sweep that backed up 0 teams while the R2 pool
#      holds team prefixes → SWEEP_NO_COVERAGE, job red (#2796/#2823)
#   5. POST /backups/purge + /reconcile ride-along (skipped on 202; #2304 purge
#      erases expired trash on the hourly cadence — wired by #2317)
#   6. POST /driver/heartbeat
#   7. self-heal: close open APP_DOWN/WATCHER_DOWN/R2_DOWN + resolved SWEEP_*
#      incidents on a genuinely healthy run
set -euo pipefail

API="${INTERNAL_API_URL:-}"
KEY="${FASTAPI_INTERNAL_KEY:-}"
STALE_MIN="${BACKUP_STALE_THRESHOLD_MIN:-90}"
# #2796: the "driver down" window (4 missed hourly runs) is the tripwire for a
# disabled-but-not-deliberate sweep. Same knob the daemon uses
# (tortoise/backup_config.py); already exported by registry-backup-cron.yml.
DRIVER_DOWN_MIN="${BACKUP_DRIVER_DOWN_THRESHOLD_MIN:-240}"
REPO="${GH_REPO:-daniel-ospina/tortoise}"
GH_TOKEN="${GITHUB_TOKEN:-}"
SIMULATE_APP_DOWN="${SIMULATE_APP_DOWN:-false}"
R2_ENDPOINT="https://${R2_ACCOUNT_ID:-}.r2.cloudflarestorage.com"

# aws CLI reads AWS_* env vars — bridge the R2_* names (review fix) and set
# the Cloudflare-required region (SigV4 fails without region=auto on R2).
export AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:-}"
export AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:-}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-auto}"

log() { echo "[backup-driver] $*"; }
fail() { echo "[backup-driver] ERROR: $*" >&2; }

if [ -z "$API" ] || [ -z "$KEY" ]; then
  fail "INTERNAL_API_URL / FASTAPI_INTERNAL_KEY not set"
  exit 1
fi

# ── dedup helpers (R2 create-once + GH-search fallback) ─────────────────────
r2_put_once() { # key body_file
  aws s3api put-object --endpoint-url "$R2_ENDPOINT" \
    --bucket "$R2_BUCKET" --key "$1" --body "$2" --if-none-match "*" >/dev/null 2>&1
}
r2_get() { # key -> body (empty on failure)
  aws s3api get-object --endpoint-url "$R2_ENDPOINT" --bucket "$R2_BUCKET" --key "$1" /dev/stdout 2>/dev/null || true
}
r2_delete() { # key — delete-to-resolve (the alert_store lifecycle contract)
  aws s3api delete-object --endpoint-url "$R2_ENDPOINT" --bucket "$R2_BUCKET" --key "$1" >/dev/null 2>&1 || true
}
gh_find_open() { # kind id(subject) -> first open issue number whose TITLE subject matches exactly (or empty)
  # #2375: subject-scoped — a bare kind search lets a per-graph issue
  # ("[DR] STALE — team_a:g_x") be adopted by a team-level file ("… team_a")
  # and vice versa (the bare team subject is a PREFIX of the per-graph
  # subject); recovery then closes the WRONG issue and orphans its dedup
  # object (silent-loss cross-talk — the server side is subject-scoped since
  # #2313; the driver must match). Global alerts (id="global") keep the
  # kind-only match (their titles carry prose, not the id).
  [ -n "$GH_TOKEN" ] || return 0
  local kind="$1" id="${2:-}"
  if [ "$id" = "global" ] || [ -z "$id" ]; then
    curl -sS -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
      "https://api.github.com/search/issues?q=repo:${REPO}+is:issue+is:open+label:%22dr:backup%22+in:title+%22%5BDR%5D+$kind%22" \
      | jq -r '.items[0].number // empty' 2>/dev/null || true
    return 0
  fi
  curl -sS -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/search/issues?q=repo:${REPO}+is:issue+is:open+label:%22dr:backup%22+in:title+%22%5BDR%5D+$kind%22&per_page=20" \
    | jq -r --arg suf " — $id" \
      '[.items[] | select(.title | endswith($suf))][0].number // empty' 2>/dev/null || true
}
gh_close() { # number comment kind id
  [ -n "$GH_TOKEN" ] || return 0
  local kind="${3:-}" id="${4:-}"
  curl -sS -X POST -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPO}/issues/$1/comments" \
    -d "{\"body\":\"$2\"}" >/dev/null 2>&1 || true
  curl -sS -X PATCH -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPO}/issues/$1" -d '{"state":"closed"}' >/dev/null 2>&1 || true
  # delete-to-resolve: drop the R2 dedup object so a RECURRENCE is a new
  # incident. Without this, file_alert's 412 branch would adopt the stale
  # object and silently swallow the recurrence (the #2796 class).
  if [ -n "$kind" ]; then
    r2_delete "ops/alerts/${kind}/${id:-_}.json"
  fi
}
resolve_global() { # kind comment — close an open global incident (no-op if none)
  local kind="$1" comment="$2" num=""
  num="$(gh_find_open "$kind" "global")"
  if [ -n "$num" ]; then gh_close "$num" "$comment" "$kind" "global"; fi
}
telegram() { # text
  [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] \
    && curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=$1" >/dev/null 2>&1 || true
}
file_alert() { # kind title body dedup_id
  local kind="$1" title="$2" body="$3" id="$4" num="" tmp=""
  tmp="$(mktemp)"
  printf '{"kind":"%s","issue_number":null,"filed_at":"%s"}' "$kind" "$(date -u +%FT%TZ)" > "$tmp"
  if r2_put_once "ops/alerts/${kind}/${id}.json" "$tmp"; then
    num="$(gh_find_open "$kind" "$id")"
    if [ -z "$num" ]; then
      num="$(curl -sS -X POST -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/${REPO}/issues" \
        -d "$(jq -nc --arg t "$title" --arg b "$body" '{title:$t, body:$b, labels:["dr:backup"]}')" \
        | jq -r '.number // empty' 2>/dev/null || true)"
    fi
    [ -n "$num" ] && telegram "🚨 DR alert: ${kind} — issue #${num}"
  else
    # 412 — the object exists (a prior creator won). Two shapes reach here:
    #   (a) the winner created the object but died before backfilling an issue
    #       number (create-then-die — review P2-3);
    #   (b) a RESOLVED incident is recurring — the dedup object outlived the
    #       closed issue, and its stale issue_number would otherwise swallow
    #       the recurrence silently (the #2796 class: a condition that pages
    #       once must page again after it recurs).
    # The correct action is the same for both: adopt an OPEN issue for this
    # (kind, subject) if one exists, else become the filer.
    num="$(gh_find_open "$kind" "$id")"
    if [ -z "$num" ]; then
      num="$(curl -sS -X POST -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/${REPO}/issues" \
        -d "$(jq -nc --arg t "$title" --arg b "$body" '{title:$t, body:$b, labels:["dr:backup"]}')" \
        | jq -r '.number // empty' 2>/dev/null || true)"
    fi
    if [ -n "$num" ]; then
      printf '{"kind":"%s","issue_number":%s,"filed_at":"%s"}' "$kind" "$num" "$(date -u +%FT%TZ)" > "$tmp"
      aws s3api put-object --endpoint-url "$R2_ENDPOINT" --bucket "$R2_BUCKET" \
        --key "ops/alerts/${kind}/${id}.json" --body "$tmp" >/dev/null 2>&1 || true
      telegram "🚨 DR alert: ${kind} — issue #${num}"
    fi
  fi
  rm -f "$tmp"
}

# ── 0. aws preflight + DIRECT R2 freshness (the app-down/crash-loop leg) ─────
# A failed listing is NEVER confirmed-empty (no STALE/NEVER from a failed
# read); a broken R2 auth preflight files R2_DOWN loudly (review P3).
R2_OK=1
# #2796: pool-level signals retained from the app-independent leg and consumed
# by the kill-switch classifier (off-while-stale) and the no-coverage check
# (enabled but 0 teams backed up) below.
POOL_STALE=0        # any graph's newest archive older than DRIVER_DOWN_MIN
R2_TEAM_COUNT=0     # team prefixes present under backups/
# Bucket-scoped probe (head-bucket) — R2's S3 API does not reliably support
# the account-level ListBuckets call from an object-scoped access key.
if ! aws s3api head-bucket --endpoint-url "$R2_ENDPOINT" --bucket "$R2_BUCKET" >/dev/null 2>&1; then
  R2_OK=0
  log "R2 preflight failed — filing R2_DOWN"
  file_alert R2_DOWN "[DR] R2_DOWN — backup storage unreachable" \
    "R2 preflight (head-bucket) failed from the driver. Runbook: docs/ops/registry-backup-dr.md" "global"
fi

if [ "$R2_OK" = "1" ]; then
  TEAMS="$(aws s3api list-objects-v2 --endpoint-url "$R2_ENDPOINT" \
    --bucket "$R2_BUCKET" --prefix "backups/" --delimiter "/" --query "CommonPrefixes[].Prefix" \
    --output text 2>/dev/null || true)"
  if [ -n "$TEAMS" ]; then
    for prefix in $TEAMS; do
      team="$(basename "$prefix")"
      R2_TEAM_COUNT=$((R2_TEAM_COUNT + 1))
      # #2375: DEFAULT-graph freshness ONLY — nested default segment
      # (backups/{team}/default/) + legacy flat (pre-#2313 default dumps;
      # flat keys start with the dump year "2xxx" so the 2-prefix never
      # matches custom nested gids g_*). The pre-#2375 leg took the newest
      # dump.enc under the WHOLE team prefix, so a team whose DEFAULT failed
      # for > STALE_MIN while a custom graph backed up fresh read "not
      # stale" — exactly the app-down case this leg exists to cover. A
      # legacy-flat classification index (#2370) additionally excludes
      # C5-era custom flat dumps when present (flat-only fallback is parity
      # pre-index).
      newest="$(aws s3api list-objects-v2 --endpoint-url "$R2_ENDPOINT" \
        --bucket "$R2_BUCKET" --prefix "backups/${team}/default/" \
        --query "Contents[?ends_with(Key, 'dump.enc')] | sort_by(@, &LastModified) | [-1].LastModified" \
        --output text 2>/dev/null || true)"
      flat_list="$(aws s3api list-objects-v2 --endpoint-url "$R2_ENDPOINT" \
        --bucket "$R2_BUCKET" --prefix "backups/${team}/2" \
        --query "Contents[?ends_with(Key, 'dump.enc')].[Key,LastModified]" \
        --output json 2>/dev/null || true)"
      if [ -n "$flat_list" ] && [ "$flat_list" != "[]" ]; then
        idx="$(r2_get "ops/legacy-flat-index/${team}.json")"
        if [ -n "$idx" ] && [ "$(printf '%s' "$idx" | jq -r 'type' 2>/dev/null)" = "object" ]; then
          flat_newest="$(printf '%s' "$flat_list" | jq -r --argjson idx "$idx" \
            '[.[] | select((($idx[(.[0] | split("/")[1] + "/" + split("/")[2])].graph_id) // "") == "" or ($idx[(.[0] | split("/")[1] + "/" + split("/")[2])].graph_id) == "default") | .[1]] | max // empty' 2>/dev/null || true)"
        else
          # no index (pre-#2370) — flat pool is default parity
          flat_newest="$(printf '%s' "$flat_list" | jq -r '[.[] | .[1]] | max // empty' 2>/dev/null || true)"
        fi
        if [ -n "$flat_newest" ]; then
          if [ -n "$newest" ]; then
            newest="$(printf '%s\n%s' "$newest" "$flat_newest" | sort | tail -1)"
          else
            newest="$flat_newest"
          fi
        fi
      fi
      if [ -n "$newest" ]; then
        newest_ts="$(date -d "$newest" +%s 2>/dev/null || echo 0)"
        if [ "$newest_ts" != "0" ]; then
          age_min=$(( ($(date +%s) - newest_ts) / 60 ))
          if [ "$age_min" -gt "$STALE_MIN" ]; then
            log "team ${team}: newest archive ${age_min}m old — filing STALE (direct leg)"
            file_alert STALE "[DR] STALE — ${team}" "Direct R2 freshness check: newest archive ${age_min}m old (> ${STALE_MIN}m)." "$team"
          fi
          if [ "$age_min" -gt "$DRIVER_DOWN_MIN" ]; then
            POOL_STALE=1
          fi
        fi
      fi
    done
  fi
fi

# ── 1. pre-flight status ────────────────────────────────────────────────────
STATUS=""
if [ "$SIMULATE_APP_DOWN" = "true" ]; then
  log "simulate_app_down: pointing pre-flight at a dead URL"
  STATUS=""
else
  STATUS="$(curl -sS -m 20 -H "Authorization: Bearer $KEY" \
    "${API}/v1/internal/backups/status" 2>/dev/null || true)"
fi

if [ -z "$STATUS" ]; then
  # connect/DNS/timeout/5xx-without-app-body → APP_DOWN (exit 0: the issue is
  # the contract; avoids noisy Actions-failure emails). The direct-R2 STALE
  # filings above already ran, so the app-down case still surfaces staleness.
  log "app unreachable — filing APP_DOWN"
  file_alert APP_DOWN "[DR] APP_DOWN — app unreachable" \
    "The hosted API did not answer /status. Runbook: docs/ops/registry-backup-dr.md" "global"
  exit 0
fi

ENABLED="$(printf '%s' "$STATUS" | jq -r '.enabled // false' 2>/dev/null || echo false)"
STORAGE_ERR="$(printf '%s' "$STATUS" | jq -r '.storage_error // empty' 2>/dev/null || true)"
CONFIG_ERR="$(printf '%s' "$STATUS" | jq -r '.config_error // empty' 2>/dev/null || true)"
log "status: enabled=$ENABLED storage_error=${STORAGE_ERR:-none} config_error=${CONFIG_ERR:-none}"
log "raw status: $(printf '%s' "$STATUS" | head -c 600)"
if [ "$ENABLED" != "true" ]; then
  # #2796: enabled:false conflates four states. Only a genuine deliberate
  # pause (no config error, no storage error, fresh pool) may exit silently.
  if [ -n "$CONFIG_ERR" ]; then
    log "kill-switch is NOT an operator decision — config_error is set; filing SWEEP_CONFIG_ERROR (job red)"
    file_alert SWEEP_CONFIG_ERROR "[DR] SWEEP_CONFIG_ERROR — backups off, config broken" \
      "enabled=false with config_error: ${CONFIG_ERR}. The sweep flag says 'run' but load_config() raised, so nothing can be written. Fix the Fly secret/config (runbook: docs/ops/registry-backup-dr.md §REGISTRY_STREAM_KEY), then re-run." "global"
    fail "sweep disabled by a configuration error: ${CONFIG_ERR}"
    exit 1
  fi
  # Config is readable again (config_error is null here) — clear the incident.
  resolve_global SWEEP_CONFIG_ERROR "Resolved — load_config() no longer raises."
  if [ -n "$STORAGE_ERR" ]; then
    log "status reports a storage error — filing R2_DOWN (not a kill-switch)"
    file_alert R2_DOWN "[DR] R2_DOWN — app storage unavailable" "status.storage_error: $STORAGE_ERR" "global"
    fail "backups disabled by a storage error: ${STORAGE_ERR}"
    exit 1
  fi
  if [ "$POOL_STALE" = "1" ]; then
    log "kill-switch while the R2 pool is stale (oldest > ${DRIVER_DOWN_MIN}m) — filing SWEEP_OFF_STALE (job red)"
    file_alert SWEEP_OFF_STALE "[DR] SWEEP_OFF_STALE — backups off and the pool is stale" \
      "enabled=false with no config_error, but the direct-R2 leg found an archive older than ${DRIVER_DOWN_MIN}m (${R2_TEAM_COUNT} team prefix(es)). An intentional pause must not let the pool decay unnoticed: re-enable backups or declare a bounded pause." "global"
    fail "backups disabled while the R2 pool is stale (> ${DRIVER_DOWN_MIN}m)"
    exit 1
  fi
  resolve_global SWEEP_OFF_STALE "Resolved — the R2 pool is fresh again."
  log "kill-switch: backups deliberately disabled (no config error, pool fresh) — skipping"
  exit 0
fi

# ── 2. watcher supervision (WATCHER_DOWN when the daemon is dead) ────────────
WATCHER_RUNNING="$(printf '%s' "$STATUS" | jq -r '.watcher.running // true' 2>/dev/null || echo true)"
WATCHER_AGE="$(printf '%s' "$STATUS" | jq -r '.watcher.age_minutes // 0' 2>/dev/null || echo 0)"
if [ "$WATCHER_RUNNING" != "true" ] || [ "${WATCHER_AGE%.*}" -gt 30 ] 2>/dev/null; then
  log "watcher heartbeat stale — filing WATCHER_DOWN"
  file_alert WATCHER_DOWN "[DR] WATCHER_DOWN — staleness daemon dead" \
    "The in-process watcher is not reporting. Check app logs." "global"
fi

# ── 3. run the sweep ────────────────────────────────────────────────────────
RUN="$(curl -sS -m 600 -X POST -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -d '{}' \
  "${API}/v1/internal/backups/sweep" 2>/dev/null || true)"
RUN_STATUS="$(printf '%s' "$RUN" | jq -r '.status // "error"' 2>/dev/null || echo error)"
log "sweep status: $RUN_STATUS"

# ── 4b. enabled-but-backing-up-nothing (#2823 shape, #2796) ─────────────────
# A sweep that backed up ZERO teams while the R2 pool already holds team
# prefixes is NOT a healthy run — it is the #2823 empty-enumeration class
# (and covers no_work/enum_failed/error, which mean the sweep tried and
# failed). The chronic 0-team pre-beta state (R2 pool empty too) stays
# silent: the false-positive envelope requires BOTH surfaces to be empty.
NO_COVERAGE=0
case "$RUN_STATUS" in
  backed_up|degraded|already_running)
    ;;
  no_teams|no_eligible_teams)
    if [ "${R2_TEAM_COUNT:-0}" -gt 0 ]; then
      log "sweep backed up 0 teams but the R2 pool holds ${R2_TEAM_COUNT} team prefix(es) — filing SWEEP_NO_COVERAGE (job red)"
      file_alert SWEEP_NO_COVERAGE "[DR] SWEEP_NO_COVERAGE — enabled but 0 teams backed up" \
        "sweep status=${RUN_STATUS} but the R2 pool holds ${R2_TEAM_COUNT} team prefix(es); last_sweep=$(printf '%s' "$STATUS" | jq -c '.last_sweep' 2>/dev/null || echo null). The sweep is enabled yet enumerates 0 teams (#2823) — backups are NOT running." "global"
      NO_COVERAGE=1
    else
      log "sweep found 0 teams and the R2 pool is empty — chronic pre-beta state, no incident"
    fi
    ;;
  *)
    log "sweep did not back up (status=$RUN_STATUS) — filing SWEEP_NO_COVERAGE (job red)"
    file_alert SWEEP_NO_COVERAGE "[DR] SWEEP_NO_COVERAGE — enabled but the sweep backed up nothing" \
      "sweep status=${RUN_STATUS} (raw: $(printf '%s' "$RUN" | head -c 300)). The sweep is enabled but backed up no team — backups are NOT running." "global"
    NO_COVERAGE=1
    ;;
esac

# ── 4. trash purge ride-along (#2304, wired #2317) + reconcile ride-along (#654) ──
# Both are skipped when the sweep returned 202 (the lock-holder is running;
# purge/reconcile would only queue behind it). Non-2xx is a hard failure for
# reconcile (the cron driver MUST NOT blind the pipeline — a silently skipped
# reconcile step is the same class of silent-no-op that left this endpoint
# uninvoked before #654). The purge erases EXPIRED trash tombstones (> 7-day
# grace) so the runbook's "erased within a day of expiry" claim stays true;
# a purge body of status "errors" (per-tombstone failures) is loud too — the
# per-team lock/retry anchors keep it safe to re-run next hour. We track both
# failures and exit AFTER heartbeat + self-heal so the driver still files
# health signals.
PURGE_FAILED=0
RECONCILE_FAILED=0
if [ "$RUN_STATUS" != "already_running" ]; then
  PURGE_CODE="$(curl -sS -o /tmp/purge-resp.json -w '%{http_code}' -m 300 -X POST \
    -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{}' \
    "${API}/v1/internal/backups/purge" 2>/dev/null || echo '000')"
  PURGE_BODY="$(cat /tmp/purge-resp.json 2>/dev/null || true)"
  PURGE_ST="$(printf '%s' "$PURGE_BODY" | jq -r '.status // "error"' 2>/dev/null || echo error)"
  if [ "$PURGE_CODE" = "200" ] && { [ "$PURGE_ST" = "ok" ] || [ "$PURGE_ST" = "already_running" ]; }; then
    log "purge ride-along OK (status=$PURGE_ST teams_purged=$(printf '%s' "$PURGE_BODY" | jq -r '.teams_purged // 0'))"
  else
    log "purge ride-along FAILED (HTTP $PURGE_CODE status=$PURGE_ST): $(printf '%s' "$PURGE_BODY" | head -c 300)"
    PURGE_FAILED=1
  fi
  RECONCILE_CODE="$(curl -sS -o /dev/null -w '%{http_code}' -m 120 -X POST \
    -H "Authorization: Bearer $KEY" \
    "${API}/v1/internal/reconcile" 2>/dev/null || echo '000')"
  if [ "$RECONCILE_CODE" -ge 200 ] 2>/dev/null && [ "$RECONCILE_CODE" -lt 300 ]; then
    log "reconcile ride-along OK ($RECONCILE_CODE)"
  else
    log "reconcile ride-along FAILED (HTTP $RECONCILE_CODE)"
    RECONCILE_FAILED=1
  fi
else
  log "sweep returned 202 (lock held) — skipping purge/reconcile to avoid racing a restore"
fi

# ── 5. driver heartbeat (carries r2_ok so the R2_DOWN signal is auditable) ──
curl -sS -m 20 -X POST -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d "{\"run_id\":\"$(date +%s)\",\"status\":\"$RUN_STATUS\",\"r2_ok\":$([ "$R2_OK" = "1" ] && echo true || echo false)}" \
  "${API}/v1/internal/driver/heartbeat" >/dev/null 2>&1 || true

# ── 6. self-heal ────────────────────────────────────────────────────────────
# Two tiers, because a no-coverage sweep does NOT prove the same things as a
# real backup:
#   * APP_DOWN / R2_DOWN (and the resolved SWEEP_* config/off-stale
#     incidents) are cleared by ANY completed /status + sweep round trip —
#     the app and its storage answered.
#   * WATCHER_DOWN requires a run that actually BACKED UP. #2796: the pre-fix
#     code treated `no_teams` as healthy and closed a WATCHER_DOWN it had just
#     filed — an empty sweep says nothing about the in-process staleness
#     daemon.
# #2411: "degraded" (per-graph errors with ≥1 graph backed up) still PROVES
# the watcher/R2 are up — it self-heals like backed_up.
case "$RUN_STATUS" in
  backed_up|degraded|no_teams|no_eligible_teams|no_work) SWEEP_COMPLETED=1 ;;
  *) SWEEP_COMPLETED=0 ;;
esac
if [ "$SWEEP_COMPLETED" = "1" ]; then
  for kind in APP_DOWN R2_DOWN SWEEP_CONFIG_ERROR SWEEP_OFF_STALE; do
    resolve_global "$kind" "Resolved — the app/storage answered (/status + sweep completed, status=$RUN_STATUS)."
  done
fi
if [ "$RUN_STATUS" = "backed_up" ] || [ "$RUN_STATUS" = "degraded" ]; then
  for kind in WATCHER_DOWN SWEEP_NO_COVERAGE; do
    resolve_global "$kind" "Resolved — sweep succeeded ($RUN_STATUS)."
  done
  log "self-heal: closed open incidents for a healthy run"
fi

if [ "$PURGE_FAILED" = "1" ]; then
  fail "purge ride-along failed — investigate (expired trash not erased)"
  exit 1
fi

if [ "$RECONCILE_FAILED" = "1" ]; then
  fail "reconcile ride-along failed — investigate"
  exit 1
fi

if [ "$NO_COVERAGE" = "1" ]; then
  fail "sweep backed up nothing (status=$RUN_STATUS) — see the SWEEP_NO_COVERAGE incident"
  exit 1
fi
log "done"
exit 0
