#!/usr/bin/env bash
# Verification-restore drill (#596): restore a real team archive into a
# scratch graph (_drill_*) via the drill endpoint (internal-key only; the
# endpoint skips the registry end-stamp and binds all live-phase ops to the
# scratch target — zero production writes, asserted server-side).
set -euo pipefail

API="${INTERNAL_API_URL:-}"
KEY="${FASTAPI_INTERNAL_KEY:-}"
REPO="${GH_REPO:-daniel-ospina/tortoise}"
TEAM_ID="${DRILL_TEAM_ID:-}"
BACKUP_KEY="${DRILL_BACKUP_KEY:-}"

if [ -z "$API" ] || [ -z "$KEY" ]; then
  echo "[drill] ERROR: INTERNAL_API_URL / FASTAPI_INTERNAL_KEY not set" >&2
  exit 1
fi

# Default: newest non-empty archive across teams (from /status per-team view is
# not archive-keyed — list R2 directly via the status/sweep metadata; the
# simplest robust default: require explicit selection for the rollout drill).
if [ -z "$TEAM_ID" ] || [ -z "$BACKUP_KEY" ]; then
  echo "[drill] ERROR: team_id and backup_key are required for the drill" >&2
  echo "[drill]   (default newest-archive selection is an operator decision; see runbook)" >&2
  exit 2
fi

START=$(date +%s)
RESP="$(curl -sS -m 900 -X POST -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --arg t "$TEAM_ID" --arg k "$BACKUP_KEY" '{team_id:$t, backup_key:$k}')" \
  "${API}/v1/internal/backups/drill" 2>/dev/null || true)"
DUR=$(( $(date +%s) - START ))

STATUS="$(printf '%s' "$RESP" | jq -r '.status // "error"' 2>/dev/null || echo error)"
echo "[drill] status=$STATUS duration=${DUR}s"
echo "[drill] $RESP"

if [ "$STATUS" != "drill_ok" ]; then
  echo "[drill] FAILED — verification restore drill did not succeed" >&2
  exit 1
fi
if [ "$DUR" -gt 900 ]; then
  echo "[drill] FAILED — RTO budget exceeded (endpoint-side > 15 min)" >&2
  exit 1
fi
echo "[drill] OK — counts/spot-check/swap verified server-side; scratch cleaned"
exit 0
