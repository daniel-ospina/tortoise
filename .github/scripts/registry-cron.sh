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
#   3. kill-switch check (enabled:false → exit 0, no filings)
#   4. POST /backups/sweep (202 = lock held)
#   5. POST /reconcile ride-along (skipped when the sweep returned 202)
#   6. POST /driver/heartbeat
#   7. self-heal: close open APP_DOWN/WATCHER_DOWN on success
set -euo pipefail

API="${INTERNAL_API_URL:-}"
KEY="${FASTAPI_INTERNAL_KEY:-}"
STALE_MIN="${BACKUP_STALE_THRESHOLD_MIN:-90}"
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
gh_find_open() { # kind -> first open issue number (or empty)
  [ -n "$GH_TOKEN" ] || return 0
  curl -sS -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/search/issues?q=repo:${REPO}+is:issue+is:open+label:%22dr:backup%22+in:title+%22%5BDR%5D+$1%22" \
    | jq -r '.items[0].number // empty' 2>/dev/null || true
}
gh_close() { # number comment
  [ -n "$GH_TOKEN" ] || return 0
  curl -sS -X POST -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPO}/issues/$1/comments" \
    -d "{\"body\":\"$2\"}" >/dev/null 2>&1 || true
  curl -sS -X PATCH -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPO}/issues/$1" -d '{"state":"closed"}' >/dev/null 2>&1 || true
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
    num="$(gh_find_open "$kind")"
    if [ -z "$num" ]; then
      num="$(curl -sS -X POST -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/${REPO}/issues" \
        -d "$(jq -nc --arg t "$title" --arg b "$body" '{title:$t, body:$b, labels:["dr:backup"]}')" \
        | jq -r '.number // empty' 2>/dev/null || true)"
    fi
    [ -n "$num" ] && telegram "🚨 DR alert: ${kind} — issue #${num}"
  else
    # 412 — the object exists (a prior creator won). Adopt: if the winner
    # never backfilled an issue number (create-then-die), become the filer
    # (review P2-3 — the window must never be silent).
    local existing
    existing="$(r2_get "ops/alerts/${kind}/${id}.json")"
    if [ -n "$existing" ] && [ "$(printf '%s' "$existing" | jq -r '.issue_number // "null"')" = "null" ]; then
      num="$(gh_find_open "$kind")"
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
  fi
  rm -f "$tmp"
}

# ── 0. aws preflight + DIRECT R2 freshness (the app-down/crash-loop leg) ─────
# A failed listing is NEVER confirmed-empty (no STALE/NEVER from a failed
# read); a broken R2 auth preflight files R2_DOWN loudly (review P3).
R2_OK=1
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
      newest="$(aws s3api list-objects-v2 --endpoint-url "$R2_ENDPOINT" \
        --bucket "$R2_BUCKET" --prefix "backups/${team}/" --query "Contents[?ends_with(Key, 'dump.enc')] | sort_by(@, &LastModified) | [-1].LastModified" \
        --output text 2>/dev/null || true)"
      if [ -n "$newest" ]; then
        newest_ts="$(date -d "$newest" +%s 2>/dev/null || echo 0)"
        if [ "$newest_ts" != "0" ]; then
          age_min=$(( ($(date +%s) - newest_ts) / 60 ))
          if [ "$age_min" -gt "$STALE_MIN" ]; then
            log "team ${team}: newest archive ${age_min}m old — filing STALE (direct leg)"
            file_alert STALE "[DR] STALE — ${team}" "Direct R2 freshness check: newest archive ${age_min}m old (> ${STALE_MIN}m)." "$team"
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
log "status: enabled=$ENABLED storage_error=${STORAGE_ERR:-none}"
log "raw status: $(printf '%s' "$STATUS" | head -c 600)"
if [ "$ENABLED" != "true" ]; then
  if [ -n "$STORAGE_ERR" ]; then
    log "status reports a storage error — filing R2_DOWN (not a kill-switch)"
    file_alert R2_DOWN "[DR] R2_DOWN — app storage unavailable" "status.storage_error: $STORAGE_ERR" "global"
  fi
  log "kill-switch: backups disabled — skipping (no filings)"
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

# ── 4. reconcile ride-along (#654) — skipped when the sweep returned 202 ────
# Non-2xx is a hard failure (the cron driver MUST NOT blind the pipeline —
# a silently skipped reconcile step is the same class of silent-no-op that
# left this endpoint uninvoked before #654). We track the failure and exit
# AFTER heartbeat + self-heal so the driver still files health signals.
RECONCILE_FAILED=0
if [ "$RUN_STATUS" != "already_running" ]; then
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
  log "sweep returned 202 (lock held) — skipping reconcile to avoid racing a restore"
fi

# ── 5. driver heartbeat (carries r2_ok so the R2_DOWN signal is auditable) ──
curl -sS -m 20 -X POST -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d "{\"run_id\":\"$(date +%s)\",\"status\":\"$RUN_STATUS\",\"r2_ok\":$([ "$R2_OK" = "1" ] && echo true || echo false)}" \
  "${API}/v1/internal/driver/heartbeat" >/dev/null 2>&1 || true

# ── 6. self-heal: close open APP_DOWN / WATCHER_DOWN / R2_DOWN on health ─────
if [ "$RUN_STATUS" = "backed_up" ] || [ "$RUN_STATUS" = "no_teams" ]; then
  for kind in APP_DOWN WATCHER_DOWN R2_DOWN; do
    num="$(gh_find_open "$kind")"
    [ -n "$num" ] && gh_close "$num" "Resolved — sweep succeeded ($RUN_STATUS)."
  done
  log "self-heal: closed open incidents for a healthy run"
fi

if [ "$RECONCILE_FAILED" = "1" ]; then
  fail "reconcile ride-along failed — investigate"
  exit 1
fi
log "done"
exit 0
