#!/usr/bin/env bash
# registry-cron.test.sh — self-check for .github/scripts/registry-cron.sh (#2796).
#
# Run: bash .github/scripts/registry-cron.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: stubs
# aws / curl / date on PATH and drives the driver with simulated /status,
# sweep and direct-R2 fixtures. No network, no R2, no GitHub.
#
# Coverage (the #2796 taxonomy):
#   1. deliberately off (no config error, fresh pool)      → exit 0, silent
#   2. off because config broken (config_error non-null)   → incident + RED
#   3. off while the pool goes stale (age > driver-down)   → incident + RED
#   4. off because storage is down (storage_error)         → R2_DOWN + RED
#   5. enabled but 0 teams backed up while R2 has teams    → incident + RED
#      (the #2823 shape) — and it must NOT self-heal WATCHER_DOWN
#   6. enabled, 0 teams, R2 pool also empty (pre-beta)     → exit 0, silent
#   7. enabled + backed_up                                 → exit 0, self-heals
#   8. enabled + no_work (teams enumerated, none backed)   → incident + RED
#   9. dedup recurrence: 412 + closed issue → re-files (never a silent repeat)
#  10. dedup repeat:    412 + open issue   → adopts (no duplicate)
#
# Fixtures are simulated; the real driver defers nothing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRIVER="$SCRIPT_DIR/registry-cron.sh"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; }
assert_eq() { # <actual> <expected> <label>
  if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 (got '$1', want '$2')"; fi
}
assert_contains() { # <haystack> <needle> <label>
  if printf '%s' "$1" | grep -qF -- "$2"; then ok "$3"; else bad "$3 (missing: $2)"; fi
}
assert_not_contains() { # <haystack> <needle> <label>
  if printf '%s' "$1" | grep -qF -- "$2"; then bad "$3 (unexpected: $2)"; else ok "$3"; fi
}
assert_match() { # <haystack> <regex> <label>
  if printf '%s' "$1" | grep -qE -- "$2"; then ok "$3"; else bad "$3 (no match: $2)"; fi
}
assert_not_match() { # <haystack> <regex> <label>
  if printf '%s' "$1" | grep -qE -- "$2"; then bad "$3 (unexpected match: $2)"; else ok "$3"; fi
}

FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"' EXIT
BIN="$FIX/bin"
mkdir -p "$BIN"
LOG="$FIX/calls.log"
: > "$LOG"

# ── stub: aws ───────────────────────────────────────────────────────────────
cat > "$BIN/aws" <<'AWS_EOF'
#!/usr/bin/env bash
op="${2:-}"
args=("$@")
argval() { # <flag> -> next arg
  local i
  for ((i=0; i<${#args[@]}; i++)); do
    if [ "${args[$i]}" = "$1" ]; then printf '%s' "${args[$((i+1))]}"; return 0; fi
  done
}
echo "AWS $op key=$(argval --key) prefix=$(argval --prefix)" >> "$STUB_LOG"
case "$op" in
  head-bucket)  [ "${STUB_R2_DOWN:-0}" = "1" ] && exit 1 || exit 0 ;;
  list-objects-v2)
    p="$(argval --prefix)"
    case "$p" in
      "backups/")   printf '%s' "${R2_TEAMS:-}" ;;
      */default/)   printf '%s' "${R2_DEFAULT_LIST:-}" ;;
      backups/*/2)  printf '%s' "${R2_FLAT_LIST:-[]}" ;;
      *)            printf '' ;;
    esac
    ;;
  put-object)   [ "${STUB_412:-0}" = "1" ] && exit 1 || exit 0 ;;
  get-object)   printf '%s' "${STUB_GET_BODY:-}" ;;
  delete-object) exit 0 ;;
  *) exit 0 ;;
esac
exit 0
AWS_EOF

# ── stub: date (GNU-compatible enough for the driver) ───────────────────────
cat > "$BIN/date" <<'DATE_EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "-d" ]; then
  arg="${2:-}"
  python3 -c "import sys,datetime;print(int(datetime.datetime.fromisoformat(sys.argv[1].replace('Z','+00:00')).timestamp()))" "$arg" 2>/dev/null \
    || /bin/date -u -d "$arg" +%s 2>/dev/null \
    || echo 0
  exit 0
fi
exec /bin/date "$@"
DATE_EOF

# ── stub: curl ──────────────────────────────────────────────────────────────
cat > "$BIN/curl" <<'CURL_EOF'
#!/usr/bin/env bash
out_file=""; write_fmt=""; url=""; data=""; method="GET"
args=("$@"); i=0
while [ $i -lt ${#args[@]} ]; do
  a="${args[$i]}"
  case "$a" in
    -o) out_file="${args[$((i+1))]:-}"; i=$((i+2)) ;;
    -w) write_fmt="${args[$((i+1))]:-}"; i=$((i+2)) ;;
    -X) method="${args[$((i+1))]:-GET}"; i=$((i+2)) ;;
    -d) data="${args[$((i+1))]:-}"; i=$((i+2)) ;;
    -H|-m|--data-urlencode|--data|--header|--max-time|-u) i=$((i+2)) ;;
    -s|-sS|-S|-L|-k|-f|--fail) i=$((i+1)) ;;
    *) url="$a"; i=$((i+1)) ;;
  esac
done

emit() { # body code
  local body="$1" code="${2:-200}"
  [ -n "$out_file" ] && printf '%s' "$body" > "$out_file"
  if [ -n "$write_fmt" ]; then printf '%s' "$code"; else [ -z "$out_file" ] && printf '%s' "$body"; fi
}
# NB: defaults live in variables — a literal JSON object inside ${VAR:-{...}}
# is mis-parsed (the first '}' closes the expansion, leaking the rest).
DEFAULT_STATUS='{}'
DEFAULT_SWEEP='{"status":"backed_up"}'
DEFAULT_PURGE='{"status":"ok","teams_purged":0}'
gh_log() { # line
  if [ -n "$data" ]; then echo "GH $method $url $data" >> "$STUB_LOG"; else echo "GH $method $url" >> "$STUB_LOG"; fi
}

case "$url" in
  *api.telegram.org*) exit 0 ;;
  *api.github.com*)
    gh_log
    case "$url" in
      */search/issues*)
        items="[]"
        for kind in SWEEP_CONFIG_ERROR SWEEP_OFF_STALE SWEEP_NO_COVERAGE WATCHER_DOWN APP_DOWN R2_DOWN STALE; do
          var="GH_ISSUE_$kind"
          val="${!var:-}"
          if [ -n "$val" ] && printf '%s' "$url" | grep -q "$kind"; then
            items="[{\"number\":$val,\"title\":\"[DR] $kind\"}]"
            break
          fi
        done
        printf '{"items":%s}' "$items" ;;
      */issues/*/comments*) printf '{}' ;;
      */issues/*) printf '{}' ;;
      */issues) printf '{"number":%s}' "${GH_NEW_ISSUE:-900}" ;;
      *) printf '{}' ;;
    esac
    ;;
  *"/v1/internal/backups/status"*)
    [ "${STUB_APP_DOWN:-0}" = "1" ] && exit 0
    sbody="${STUB_STATUS_BODY:-$DEFAULT_STATUS}"; emit "$sbody" ;;
  *"/v1/internal/backups/sweep"*)
    sbody="${STUB_SWEEP_BODY:-$DEFAULT_SWEEP}"; emit "$sbody" ;;
  *"/v1/internal/backups/purge"*)
    sbody="${STUB_PURGE_BODY:-$DEFAULT_PURGE}"; emit "$sbody" "${STUB_PURGE_CODE:-200}" ;;
  *"/v1/internal/reconcile"*)
    emit '{}' "${STUB_RECONCILE_CODE:-200}" ;;
  *"/v1/internal/driver/heartbeat"*) printf '{}' ;;
  *) printf '{}' ;;
esac
exit 0
CURL_EOF

chmod +x "$BIN/aws" "$BIN/date" "$BIN/curl"
export PATH="$BIN:$PATH"
export STUB_LOG="$LOG"

# ── fixture timestamps ──────────────────────────────────────────────────────
TS_RECENT="$(python3 -c "import datetime;print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ'))")"
TS_STALE="$(python3 -c "import datetime;print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=500)).strftime('%Y-%m-%dT%H:%M:%SZ'))")"
export TS_RECENT TS_STALE

# ── driver env ──────────────────────────────────────────────────────────────
export INTERNAL_API_URL="https://api.example.test"
export FASTAPI_INTERNAL_KEY="test-key"
export R2_ACCOUNT_ID="acct"
export R2_BUCKET="tortoise-backups"
export GH_REPO="daniel-ospina/tortoise"
export GITHUB_TOKEN="test-token"
export BACKUP_STALE_THRESHOLD_MIN="90"
export BACKUP_DRIVER_DOWN_THRESHOLD_MIN="240"
unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID || true

reset_case() {
  : > "$LOG"
  unset STUB_STATUS_BODY STUB_SWEEP_BODY STUB_PURGE_BODY STUB_PURGE_CODE \
        STUB_RECONCILE_CODE STUB_412 STUB_APP_DOWN STUB_R2_DOWN STUB_GET_BODY \
        GH_SEARCH_JSON GH_NEW_ISSUE R2_TEAMS R2_DEFAULT_LIST R2_FLAT_LIST \
        GH_ISSUE_SWEEP_CONFIG_ERROR GH_ISSUE_SWEEP_OFF_STALE GH_ISSUE_SWEEP_NO_COVERAGE \
        GH_ISSUE_WATCHER_DOWN GH_ISSUE_APP_DOWN GH_ISSUE_R2_DOWN GH_ISSUE_STALE || true
  export R2_FLAT_LIST="[]"
}

run_driver() { # -> sets RC
  set +e
  OUT="$(bash "$DRIVER" 2>&1)"
  RC=$?
  set -e
}

status_body() { # enabled config storage [watcher_running] [watcher_age]
  printf '{"enabled":%s,"config_error":%s,"storage_error":%s,"per_team":{},"last_sweep":{"last_sweep_at":"2026-08-09T23:30:00Z","last_team_count":0},"watcher":{"running":%s,"age_minutes":%s}}' \
    "$1" "$2" "$3" "${4:-true}" "${5:-1}"
}

echo "registry-cron.test.sh — #2796 taxonomy"

# ── 1. deliberately off (no config error, fresh pool) → silent exit 0 ───────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false null null)"
export STUB_SWEEP_BODY='{"status":"backed_up"}'
run_driver
assert_eq "$RC" 0 "1. deliberate-off exits 0"
assert_not_contains "$(cat "$LOG")" "GH POST" "1. deliberate-off files nothing"
assert_contains "$OUT" "deliberately disabled" "1. deliberate-off logs the kill-switch as deliberate"

# ── 2. off because config broken → SWEEP_CONFIG_ERROR + RED ─────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
run_driver
assert_eq "$RC" 1 "2. config-broken off exits RED (1)"
assert_contains "$(cat "$LOG")" "SWEEP_CONFIG_ERROR" "2. config-broken files SWEEP_CONFIG_ERROR"
assert_contains "$OUT" "kill-switch is NOT an operator decision" "2. config-broken is not treated as a pause"

# ── 3. off while the pool goes stale → SWEEP_OFF_STALE + RED ────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_STALE"
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "3. off-while-stale exits RED (1)"
assert_contains "$(cat "$LOG")" "SWEEP_OFF_STALE" "3. off-while-stale files SWEEP_OFF_STALE"
assert_contains "$OUT" "newest archive" "3. direct leg still reports the measured age"

# ── 4. off because storage is down → R2_DOWN + RED ──────────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false null '"bucket unreachable"')"
run_driver
assert_eq "$RC" 1 "4. storage-broken off exits RED (1)"
assert_contains "$(cat "$LOG")" "R2_DOWN" "4. storage-broken files R2_DOWN"

# ── 5. enabled but 0 teams backed up while R2 has teams → SWEEP_NO_COVERAGE ─
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null false 999)"
export STUB_SWEEP_BODY='{"status":"no_teams","teams_backed_up":0}'
export GH_ISSUE_WATCHER_DOWN=55
run_driver
assert_eq "$RC" 1 "5. enabled-no-coverage exits RED (1)"
assert_contains "$(cat "$LOG")" "SWEEP_NO_COVERAGE" "5. enabled-no-coverage files SWEEP_NO_COVERAGE"
assert_contains "$(cat "$LOG")" "WATCHER_DOWN" "5. the stale watcher was actually filed"
assert_not_match "$(cat "$LOG")" "GH PATCH .*/issues/55" "5. enabled-no-coverage does NOT self-heal WATCHER_DOWN"

# ── 6. enabled, 0 teams, R2 pool also empty (chronic pre-beta) → silent ─────
reset_case
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_teams","teams_backed_up":0}'
run_driver
assert_eq "$RC" 0 "6. pre-beta 0-teams exits 0"
assert_not_contains "$(cat "$LOG")" "SWEEP_NO_COVERAGE" "6. pre-beta 0-teams files nothing"

# ── 7. enabled + backed_up → self-heal closes WATCHER_DOWN ──────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export GH_ISSUE_WATCHER_DOWN=77
run_driver
assert_eq "$RC" 0 "7. healthy run exits 0"
assert_contains "$(cat "$LOG")" "WATCHER_DOWN" "7. healthy run resolves WATCHER_DOWN"
assert_match "$(cat "$LOG")" "GH PATCH .*/issues/77" "7. healthy run closes the incident"

# ── 8. enabled + no_work (teams enumerated, none backed up) → RED ───────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_work","teams_backed_up":0}'
run_driver
assert_eq "$RC" 1 "8. no_work exits RED (1)"
assert_contains "$(cat "$LOG")" "SWEEP_NO_COVERAGE" "8. no_work files SWEEP_NO_COVERAGE"

# ── 9. dedup recurrence: 412 + no open issue → re-files ─────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
run_driver
assert_eq "$RC" 1 "9. recurrence exits RED (1)"
assert_match "$(cat "$LOG")" "GH POST .*/issues \{" "9. recurrence with a closed issue RE-FILES (not swallowed)"

# ── 10. dedup repeat: 412 + open issue → adopt, no duplicate ────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
export GH_ISSUE_SWEEP_CONFIG_ERROR=42
run_driver
assert_eq "$RC" 1 "10. repeat is still RED (1)"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "10. repeat adopts the open issue (no duplicate)"

echo ""
echo "registry-cron.test.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
