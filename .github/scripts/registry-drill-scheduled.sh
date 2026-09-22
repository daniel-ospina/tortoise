#!/usr/bin/env bash
# #2317 scheduled verification-restore drill driver — monthly, unattended.
#
# Calls POST /v1/internal/backups/drill-scheduled: the app auto-selects the
# OLDEST eligible nested archive across teams (tombstoned/deleted graphs'
# archives are skipped — #2304), restores it into _drill_* scratch via the
# shared drill core (zero production writes; same cooldown + boot-GC as the
# manual drill), and records pass/fail + measured restore time to
# ops/drills/last.json (surfaced on /status → last_drill).
#
# Alerting is SERVER-side: a failed drill or an RTO breach (> 15 min) opens a
# deduplicated RESTORE_DRILL_FAILED incident (GH issue + Telegram, dual
# channel) via the app's own DR_ISSUES_PAT/Telegram secrets — this workflow
# carries only the internal key (no R2 creds, no PAT). The exit code makes
# the scheduled job green/red.
set -euo pipefail

API="${INTERNAL_API_URL:-}"
KEY="${FASTAPI_INTERNAL_KEY:-}"

if [ -z "$API" ] || [ -z "$KEY" ]; then
  echo "[drill-cron] ERROR: INTERNAL_API_URL / FASTAPI_INTERNAL_KEY not set" >&2
  exit 1
fi

TMP="$(mktemp)"
HTTP="$(curl -sS -o "$TMP" -w '%{http_code}' -m 1200 -X POST \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{}' \
  "${API}/v1/internal/backups/drill-scheduled" 2>/dev/null || echo '000')"
RESP="$(cat "$TMP")"
rm -f "$TMP"

STATUS="$(printf '%s' "$RESP" | jq -r '.status // "error"' 2>/dev/null || echo error)"
DUR="$(printf '%s' "$RESP" | jq -r '.duration_s // empty' 2>/dev/null || true)"
echo "[drill-cron] http=$HTTP status=$STATUS duration=${DUR:-n/a}s"
echo "[drill-cron] $RESP"

case "$HTTP" in
  429)
    # Benign: a manual/other drill ran within the ≥1h cooldown — the monthly
    # run defers (the endpoint already recorded that drill).
    echo "[drill-cron] SKIPPED — drill cooldown held (a drill ran < 1h ago)"
    exit 0
    ;;
esac

if [ "$HTTP" != "200" ]; then
  echo "[drill-cron] FAILED — drill-scheduled returned HTTP $HTTP (see RESTORE_DRILL_FAILED incident)" >&2
  exit 1
fi

case "$STATUS" in
  drill_ok)
    if [ "$(printf '%s' "$RESP" | jq -r '.within_rto // false' 2>/dev/null || echo false)" = "true" ]; then
      echo "[drill-cron] OK — restore verified within the committed RTO"
      exit 0
    fi
    echo "[drill-cron] FAILED — restore exceeded the committed RTO (see RESTORE_DRILL_FAILED incident)" >&2
    exit 1
    ;;
  no_candidates)
    echo "[drill-cron] SKIPPED — no eligible nested archive across teams (recorded in ops/drills/last.json)"
    exit 0
    ;;
  *)
    echo "[drill-cron] FAILED — drill did not succeed (see RESTORE_DRILL_FAILED incident)" >&2
    exit 1
    ;;
esac
