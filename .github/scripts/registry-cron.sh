#!/usr/bin/env bash
# Per-team knowledge-graph backup driver (#596) — the app-down/crash-loop leg.
#
# Pre-flight classification is the critical piece: an OOM crash-loop (#545)
# answers /status between restarts, so the driver's DIRECT R2 freshness check
# (aws CLI) is the only signal that covers the app-down case. This script:
#   1. GET /status (classify: connect-fail → APP_DOWN; app-503/429 → up/degraded)
#   2. kill-switch check (enabled:false → exit 0, no filings)
#   3. DIRECT R2 freshness (per-team prefixes) → STALE independent of /status
#   4. POST /backups/sweep
#   5. POST /reconcile ride-along (skipped when the sweep returned 202 — lock held)
#   6. POST /driver/heartbeat
#   7. self-heal: close open APP_DOWN/WATCHER_DOWN/STALE on success
#
# Driver-side alert filings use the SAME R2 create-once dedup objects as the
# daemon (aws s3api put-object --if-none-match) with a GH-search fallback, and
# push Telegram via GH secrets. "registry" naming retained from the registry-era
# design — content is per-team.
set -euo pipefail

API="${INTERNAL_API_URL:-}"
KEY="${FASTAPI_INTERNAL_KEY:-}"
STALE_MIN="${BACKUP_STALE_THRESHOLD_MIN:-90}"
REPO="${GH_REPO:-daniel-ospina/tortoise}"
GH_TOKEN="${GITHUB_TOKEN:-}"
SIMULATE_APP_DOWN="${SIMULATE_APP_DOWN:-false}"
# aws CLI reads AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY; the workflow passes
# the R2_* names — bridge them (plan §3.8). Without this, every aws call fails
# auth and the direct-R2 leg + driver-side filings silently no-op.
export AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:-}"
export AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:-}"

log() { echo "[backup-driver] $*"; }
fail() { echo "[backup-driver] ERROR: $*" >&2; }

if [ -z "$API" ] || [ -z "$KEY" ]; then
  fail "INTERNAL_API_URL / FASTAPI_INTERNAL_KEY not set"
  exit 1
fi

# ── dedup helpers (R2 create-once + GH-search fallback) ─────────────────────
r2_put_once() { # key body_file
  aws s3api put-object --endpoint-url "https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" \
    --bucket "$R2_BUCKET" --key "$1" --body "$2" --if-none-match "*" >/dev/null 2>&1
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
  local kind="$1" title="$2" body="$3" id="$4" num=""
  local tmp; tmp="$(mktemp)"
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
  fi
  rm -f "$tmp"
}

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
  # the contract; avoids noisy Actions-failure emails)
  log "app unreachable — filing APP_DOWN"
  file_alert APP_DOWN "[DR] APP_DOWN — app unreachable" \
    "The hosted API did not answer /status. Runbook: docs/ops/registry-backup-dr.md" "global"
  exit 0
fi

ENABLED="$(printf '%s' "$STATUS" | jq -r '.enabled // false' 2>/dev/null || echo false)"
if [ "$ENABLED" != "true" ]; then
  log "kill-switch: backups disabled — skipping (no filings)"
  exit 0
fi

# ── 2. direct R2 freshness (the crash-loop leg) ─────────────────────────────
# Teams = top-level prefixes under backups/. A listing failure is NEVER
# treated as confirmed-empty (no STALE/NEVER filing from a failed read).
TEAMS="$(aws s3api list-objects-v2 --endpoint-url "https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" \
  --bucket "$R2_BUCKET" --prefix "backups/" --delimiter "/" --query "CommonPrefixes[].Prefix" \
  --output text 2>/dev/null || true)"
if [ -n "$TEAMS" ]; then
  for prefix in $TEAMS; do
    team="$(basename "$prefix")"
    newest="$(aws s3api list-objects-v2 --endpoint-url "https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" \
      --bucket "$R2_BUCKET" --prefix "backups/${team}/" --query "Contents[?ends_with(Key, '.enc')] | sort_by(@, &LastModified) | [-1].LastModified" \
      --output text 2>/dev/null || true)"
    if [ -n "$newest" ]; then
      age_min=$(( ($(date +%s) - $(date -d "$newest" +%s)) / 60 ))
      if [ "$age_min" -gt "$STALE_MIN" ]; then
        log "team ${team}: newest archive ${age_min}m old — filing STALE (direct leg)"
        file_alert STALE "[DR] STALE — ${team}" "Direct R2 freshness check: newest archive ${age_min}m old (> ${STALE_MIN}m)." "$team"
      fi
    fi
  done
fi

# ── 3. run the sweep ────────────────────────────────────────────────────────
RUN="$(curl -sS -m 600 -X POST -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -d '{}' \
  "${API}/v1/internal/backups/sweep" 2>/dev/null || true)"
RUN_STATUS="$(printf '%s' "$RUN" | jq -r '.status // "error"' 2>/dev/null || echo error)"
log "sweep status: $RUN_STATUS"

# ── 4. reconcile ride-along (#654) — skipped when the sweep returned 202 ────
if [ "$RUN_STATUS" != "already_running" ]; then
  curl -sS -m 120 -X POST -H "Authorization: Bearer $KEY" \
    "${API}/v1/internal/reconcile" >/dev/null 2>&1 || log "reconcile ride-along failed (best-effort)"
else
  log "sweep returned 202 (lock held) — skipping reconcile to avoid racing a restore"
fi

# ── 5. driver heartbeat ─────────────────────────────────────────────────────
curl -sS -m 20 -X POST -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d "{\"run_id\":\"$(date +%s)\",\"status\":\"$RUN_STATUS\"}" \
  "${API}/v1/internal/driver/heartbeat" >/dev/null 2>&1 || true

# ── 6. self-heal: close open APP_DOWN / WATCHER_DOWN / STALE on success ─────
if [ "$RUN_STATUS" = "backed_up" ] || [ "$RUN_STATUS" = "no_teams" ]; then
  for kind in APP_DOWN WATCHER_DOWN; do
    num="$(gh_find_open "$kind")"
    [ -n "$num" ] && gh_close "$num" "Resolved — sweep succeeded ($RUN_STATUS)."
  done
  log "self-heal: closed open incidents for a healthy run"
fi

log "done"
exit 0
