#!/usr/bin/env bash
# Verification-restore drill (#596): restore a real team archive into a
# scratch graph (_drill_*) via the drill endpoint (internal-key only; the
# endpoint skips the registry end-stamp and binds all live-phase ops to the
# scratch target — zero production writes, asserted server-side).
set -euo pipefail

# #3293 review: the API response is app-controlled (a failed restore echoes a
# signed URL / credential) and this body is echoed into a PUBLIC Actions log.
# Scrub it at this boundary — the same rule registry-cron.sh applies with its
# redact() — so the echo never publishes a credential even if the server-side
# detail scrub is bypassed. Mirrors tortoise/security.redact_for_publication.
redact_response() { # text -> text safe for a public Actions log
  printf '%s' "$1" | sed -E \
    -e 's#([?&](token|key|secret|sig|signature|api_key|apikey|access_token|credential|password|passwd|x-amz-signature|x-amz-credential|x-amz-security-token)=)[^&"[:space:]<>]*#\1<redacted>#Ig' \
    -e 's#([A-Za-z][A-Za-z0-9+.-]*://)[^@[:space:]]*@#\1<redacted>@#g' \
    -e 's#(^|[^A-Za-z0-9_])(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|glpat-|AKIA|ASIA|sk-)[A-Za-z0-9_]+#\1\2<redacted>#g' \
    -e 's#[0-9]{6,12}:[A-Za-z0-9_-]{30,}#<redacted>#g' || true
}

API="${INTERNAL_API_URL:-}"
KEY="${FASTAPI_INTERNAL_KEY:-}"
REPO="${GH_REPO:-daniel-ospina/tortoise}"
ORG_ID="${DRILL_ORG_ID:-}"
BACKUP_KEY="${DRILL_BACKUP_KEY:-}"

if [ -z "$API" ] || [ -z "$KEY" ]; then
  echo "[drill] ERROR: INTERNAL_API_URL / FASTAPI_INTERNAL_KEY not set" >&2
  exit 1
fi

# Default: newest non-empty archive across teams (from /status per-team view is
# not archive-keyed — list R2 directly via the status/sweep metadata; the
# simplest robust default: require explicit selection for the rollout drill).
if [ -z "$ORG_ID" ] || [ -z "$BACKUP_KEY" ]; then
  echo "[drill] ERROR: org_id and backup_key are required for the drill" >&2
  echo "[drill]   (default newest-archive selection is an operator decision; see runbook)" >&2
  exit 2
fi

START=$(date +%s)
RESP="$(curl -sS -m 900 -X POST -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --arg t "$ORG_ID" --arg k "$BACKUP_KEY" '{org_id:$t, backup_key:$k}')" \
  "${API}/v1/internal/backups/drill" 2>/dev/null || true)"
DUR=$(( $(date +%s) - START ))

STATUS="$(printf '%s' "$RESP" | jq -r '.status // "error"' 2>/dev/null || echo error)"
echo "[drill] status=$STATUS duration=${DUR}s"
echo "[drill] $(redact_response "$RESP")"

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
