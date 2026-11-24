#!/usr/bin/env bash
# #14424 — the HOSTED (REST) door: signup → first write → read back → first search.
#
# Requires a credential in the user-global store (`tortoise signup`, ~11 s).
# Every step is timed in milliseconds and every response body is printed, so the
# transcript is the evidence. `Retry-After` is printed because the wait-bound
# refusal refers to it as "the advertised delay" — the CLI dropped it (#14424).
#
#   HOME=$(mktemp -d) ; export HOME
#   uv run python -m tortoise signup
#   docs/evidence/14424-onboarding/probe_hosted_rest.sh
set -u

API_URL="${TORTOISE_API_URL:-https://api.premiselabs.co}"
CRED="${HOME}/.tortoise/credentials.json"
MARKER="The onboarding probe marker is ZQ-14424-ORCHID (hosted REST door)."
QUERY="ZQ-14424-ORCHID"
ATTEMPTS="${ATTEMPTS:-2}"

[ -f "$CRED" ] || { echo "no credential at $CRED — run 'tortoise signup' first"; exit 2; }
KEY=$(python3 -c "import json,sys;print(json.load(open('$CRED'))['api_key'])")
URL=$(python3 -c "import json;print(json.load(open('$CRED')).get('api_url','$API_URL'))" | sed 's:/*$::')

step() {  # step <label> <curl args...>
  local label="$1"; shift
  local start end code
  start=$(date +%s%3N)
  code=$(curl -s -o /tmp/14424.out -D /tmp/14424.hdr -w '%{http_code}' "$@")
  end=$(date +%s%3N)
  printf '\n=== %s\n  status: %s   wall: %s ms\n' "$label" "$code" "$((end-start))"
  grep -i '^retry-after' /tmp/14424.hdr || true
  head -c 900 /tmp/14424.out; echo
}

step "POST /v1/points (first write, attempt 1)" \
  -X POST "$URL/v1/points" -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c "import json;print(json.dumps({'content':'''$MARKER''','kind':'statement'}))")"

for i in $(seq 2 "$ATTEMPTS"); do
  sleep "${RETRY_WAIT:-10}"
  step "POST /v1/points (retry $((i-1)), exactly as a user would re-run it)" \
    -X POST "$URL/v1/points" -H "Authorization: Bearer $KEY" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c "import json;print(json.dumps({'content':'''$MARKER''','kind':'statement'}))")"
done

step "GET /v1/points (read back — how many copies exist?)" \
  "$URL/v1/points?limit=50" -H "Authorization: Bearer $KEY"
python3 - "$MARKER" <<'PY'
import json, sys
d = json.load(open('/tmp/14424.out'))
pts = d.get('points', [])
same = [p for p in pts if p.get('content') == sys.argv[1]]
print(f"  -> org holds {d.get('count')} Points; {len(same)} carry the probe marker verbatim")
PY

step "GET /v1/search?q=... (the criterion's real moment)" \
  "$URL/v1/search?q=$QUERY&limit=5" -H "Authorization: Bearer $KEY"
grep -o '"stage":"[a-z_]*"\|"stages_ms":{[^}]*}' /tmp/14424.out 2>/dev/null || true
python3 - <<'PY'
import json
try:
    d = json.load(open('/tmp/14424.out'))
except Exception:
    raise SystemExit(0)
hits = d.get('results') or d.get('points') or []
print(f"  -> {len(hits)} hit(s)")
for h in hits[:3]:
    print("   ", h.get('id'), '|', (h.get('content') or '')[:70])
PY
