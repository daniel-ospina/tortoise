#!/usr/bin/env bash
# registry-cron.test.sh — self-check for .github/scripts/registry-cron.sh (#2796).
#
# Run: bash .github/scripts/registry-cron.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: stubs
# aws / curl / date on PATH and drives the driver with simulated /status,
# sweep and direct-R2 fixtures. No network, no R2, no GitHub.
#
# Coverage (the #2796 taxonomy):
#   1. deliberately off (measured-fresh pool)              → exit 0, silent
#   2. off because config broken (config_error non-null)   → incident + RED
#   3. off while the pool goes stale (age > driver-down)   → incident + RED
#   4. off because storage is down (storage_error)         → R2_DOWN + RED
#   5. enabled but 0 teams backed up while R2 has teams    → incident + RED
#      (the #2823 shape) — and it must NOT self-heal WATCHER_DOWN
#   6. enabled, 0 teams, R2 pool also empty (pre-beta)     → exit 0, silent
#   7. enabled + backed_up                                 → exit 0, self-heals
#   8. enabled + no_work (teams enumerated, none backed)   → incident + RED
#   9. dedup recurrence: 412 + object with no issue → re-files
#  10. dedup repeat:    412 + object with an OPEN issue → adopts (no duplicate)
#  11. R2 listing fails  → pool UNKNOWN, never read as empty/fresh
#  12. R2 listing fails while OFF → SWEEP_OFF_STALE + RED (unconfirmed pause)
#  13. R2 preflight down + 0 teams → R2_DOWN stays OPEN (no self-close loop)
#  14. lock held + stale last_sweep → SWEEP_NO_COVERAGE + RED
#  15. lock held + fresh last_sweep → healthy, silent
#  16. no_work + empty R2 pool → silent (pre-beta)
#  17. missing/non-JSON .enabled → SWEEP_NO_COVERAGE (fail CLOSED) + RED
#  18. watcher.running=false → WATCHER_DOWN filed (jq `//` false bug, #2843)
#  19. prefix without DEFAULT archive while OFF → SWEEP_OFF_STALE + RED
#  20. config_error secret redacted before PUBLICATION (#2796 review R2/R4)
#  21. 412 + R2 object with OPEN issue_number + empty search → NO duplicate
#  22. 412 + R2 object with CLOSED issue_number → re-files
#  23. WATCHER_DOWN self-heals on watcher EVIDENCE even on no_work (R3)
#  24. purge ride-along failure → RED
#  25. reconcile ride-along failure → RED
#  26. unquoted/bare secret runs are redacted too (security review)
#  27. last_sweep (graph_failures[].error) is redacted before publication
#  28. a 404 (deleted) tracked issue re-files; a 500 blip does not duplicate
#  29. resolve deletes BOTH global.json and the server-owned _.json (#2844)
#  30. a missing .watcher block neither files nor self-heals WATCHER_DOWN
#  31. degraded is healthy; no_eligible_teams is the no-coverage family
#  32. lock held with no usable last_sweep + pool data → SWEEP_NO_COVERAGE
#  33. sweep error / non-JSON body → SWEEP_NO_COVERAGE + RED (catch-all)
#  34. missing GITHUB_TOKEN → fail closed (no silent-deaf alerting)
#  35. a per-team listing failure is unmeasured, not a false stale
#  36. positive self-heals: SWEEP_CONFIG_ERROR / SWEEP_OFF_STALE resolve
#  37. multi-team pool: `--output text` is ONE tab-separated line (review P1)
#  38. held lock + UNMEASURED pool → SWEEP_NO_COVERAGE (review P1)
#  39. redaction of DSN/URI/Basic/lowercase/numbered secrets (security review)
#  40. the purge failure body is redacted (security review)
#  41. storage_error while ENABLED is loud, and blocks R2_DOWN self-heal
#  42. no_work with graph_totals.backed_up>0 is not a coverage gap
#  43. APP_DOWN is RED (any filing is RED)
#  44. an index read failure is unmeasured, not "no index"
#  45. a measured-fresh off pool resolves a stale R2_DOWN
#  11. R2 listing fails  → pool UNKNOWN, never read as empty/fresh
#  12. R2 listing fails while OFF → does NOT resolve SWEEP_OFF_STALE
#  13. R2 preflight down + 0 teams → R2_DOWN stays OPEN (no self-close loop)
#  14. 202 lock held + stale last_sweep → SWEEP_NO_COVERAGE + RED
#  15. 202 lock held + fresh last_sweep → healthy, silent
#  16. no_work + empty R2 pool → silent (pre-beta)
#  17. missing/non-JSON .enabled → SWEEP_NO_COVERAGE (fail CLOSED) + RED
#  18. watcher.running=false → WATCHER_DOWN filed (jq `//` false bug, #2843)
#  19. prefix without DEFAULT archive while OFF → SWEEP_OFF_STALE + RED
#  20. config_error secret redacted before PUBLICATION (#2796 review R2/R4)
#  21. 412 + R2 object with OPEN issue_number + empty search → NO duplicate
#  22. 412 + R2 object with CLOSED issue_number → re-files
#  23. WATCHER_DOWN self-heals on watcher EVIDENCE even on no_work (R3)
#  24. purge ride-along failure → RED
#  25. reconcile ride-along failure → RED
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
assert_filed() { # <log> <KIND> <label> — a create POST whose body carries KIND
  # NB: matching a bare KIND against the log is confounded by the GitHub SEARCH
  # URL (which carries the kind). Assert on the create POST instead.
  if printf '%s' "$1" | grep -qE "GH POST .*/issues .*${2}"; then ok "$3"; else bad "$3 (no incident POST for ${2})"; fi
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
      "backups/")
        [ "${STUB_LIST_FAIL:-0}" = "1" ] && exit 1
        printf '%s' "${R2_TEAMS:-}" ;;
      */default/)
        [ "${STUB_LIST_FAIL_TEAM:-0}" = "1" ] && exit 1
        printf '%s' "${R2_DEFAULT_LIST:-}" ;;
      backups/*/2)  printf '%s' "${R2_FLAT_LIST:-[]}" ;;
      *)            printf '' ;;
    esac
    ;;
  put-object)   [ "${STUB_412:-0}" = "1" ] && exit 1 || exit 0 ;;
  get-object)
    # STUB_INDEX_FAIL emulates a NON-404 failure reading the legacy-flat index
    # (a transient S3 error) so the driver must treat the pool as unmeasured.
    if [ "${STUB_INDEX_FAIL:-0}" = "1" ] && case "$(argval --key)" in *legacy-flat-index*) true ;; *) false ;; esac; then
      echo "An error occurred (InternalError) when calling the GetObject operation" >&2
      exit 1
    fi
    printf '%s' "${STUB_GET_BODY:-}" ;;
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
      */issues/*)
        if [ "$method" = "GET" ]; then
          # emit honours -o/-w: gh_issue_open asks for the code first, then the body.
          emit "{\"state\":\"${GH_ISSUE_STATE:-open}\"}" "${STUB_ISSUE_CODE:-200}"
        else
          printf '{}'
        fi ;;
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
        SIMULATE_APP_DOWN \
        STUB_LIST_FAIL STUB_LIST_FAIL_TEAM STUB_INDEX_FAIL GH_ISSUE_STATE STUB_ISSUE_CODE \
        GH_SEARCH_JSON GH_NEW_ISSUE R2_TEAMS R2_DEFAULT_LIST R2_FLAT_LIST \
        GH_ISSUE_SWEEP_CONFIG_ERROR GH_ISSUE_SWEEP_OFF_STALE GH_ISSUE_SWEEP_NO_COVERAGE \
        GH_ISSUE_WATCHER_DOWN GH_ISSUE_APP_DOWN GH_ISSUE_R2_DOWN GH_ISSUE_STALE || true
  export R2_FLAT_LIST="[]"
}

run_driver() { # -> sets RC
  # Invoke the driver directly (it carries a shebang + the exec bit) rather
  # than `bash "$DRIVER"`: a $(bash <script>) command substitution is read by
  # the agent-infra worktree guard as unverifiable "git content" and blocks the
  # harness locally (#1484 false positive — filed separately).
  set +e
  OUT="$("$DRIVER" 2>&1)"
  RC=$?
  set -e
}

status_body() { # enabled config storage [watcher_running] [watcher_age] [last_sweep_at]
  printf '{"enabled":%s,"config_error":%s,"storage_error":%s,"per_team":{},"last_sweep":{"last_sweep_at":"%s","last_team_count":0},"watcher":{"running":%s,"age_minutes":%s}}' \
    "$1" "$2" "$3" "${6:-2026-08-09T23:30:00Z}" "${4:-true}" "${5:-1}"
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
assert_filed "$(cat "$LOG")" SWEEP_CONFIG_ERROR "2. config-broken files SWEEP_CONFIG_ERROR"
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
assert_filed "$(cat "$LOG")" R2_DOWN "4. storage-broken files R2_DOWN"

# ── 5. enabled but 0 teams backed up while R2 has teams → SWEEP_NO_COVERAGE ─
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null false 999)"
export STUB_SWEEP_BODY='{"status":"no_teams","teams_backed_up":0}'
export GH_ISSUE_WATCHER_DOWN=55
run_driver
assert_eq "$RC" 1 "5. enabled-no-coverage exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "5. enabled-no-coverage files SWEEP_NO_COVERAGE"
assert_match "$(cat "$LOG")" "AWS put-object key=ops/alerts/WATCHER_DOWN/global.json" "5. the stale watcher incident is recorded"
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
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "8. no_work files SWEEP_NO_COVERAGE"

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

# ── 11. R2 listing failure → pool UNKNOWN, not empty ────────────────────────
reset_case
export STUB_LIST_FAIL=1
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_teams","teams_backed_up":0}'
run_driver
assert_eq "$RC" 1 "11. unmeasurable pool + no_teams exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "11. unmeasurable pool files SWEEP_NO_COVERAGE"
assert_contains "$OUT" "pool state is UNKNOWN" "11. the failed listing is recorded as unknown"

# ── 12. R2 listing failure while OFF → SWEEP_OFF_STALE + RED ────────────────
# Review R5/Agent1: an unmeasurable pool is NOT a confirmed deliberate pause,
# so the driver files and goes RED (unknown ≠ fresh).
reset_case
export STUB_LIST_FAIL=1
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "12. off + unmeasurable pool exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_OFF_STALE "12. an unmeasured pool while off files SWEEP_OFF_STALE"
assert_contains "$OUT" "cannot be confirmed" "12. the driver says the pause is unconfirmed"

# ── 13. R2 preflight down + 0 teams → R2_DOWN stays OPEN ────────────────────
reset_case
export STUB_R2_DOWN=1
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_teams","teams_backed_up":0}'
export GH_ISSUE_R2_DOWN=88
run_driver
assert_eq "$RC" 1 "13. R2 down + 0 teams exits RED (1)"
assert_match "$(cat "$LOG")" "AWS put-object key=ops/alerts/R2_DOWN/global.json" "13. R2_DOWN is recorded"
assert_not_match "$(cat "$LOG")" "GH PATCH .*/issues/88" "13. a failed R2 probe does NOT self-close the R2_DOWN it filed"

# ── 14. 202 lock held + stale last_sweep → SWEEP_NO_COVERAGE ────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null true 1 "$TS_STALE")"
export STUB_SWEEP_BODY='{"status":"already_running"}'
run_driver
assert_eq "$RC" 1 "14. stuck lock exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "14. stuck lock files SWEEP_NO_COVERAGE"

# ── 15. 202 lock held + fresh last_sweep → healthy ──────────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null true 1 "$TS_RECENT")"
export STUB_SWEEP_BODY='{"status":"already_running"}'
run_driver
assert_eq "$RC" 0 "15. healthy lock exits 0"
assert_not_contains "$(cat "$LOG")" "SWEEP_NO_COVERAGE" "15. healthy lock files nothing"

# ── 16. no_work + empty R2 pool → silent (pre-beta) ─────────────────────────
reset_case
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_work","teams_backed_up":0}'
run_driver
assert_eq "$RC" 0 "16. no_work + empty pool exits 0"
assert_not_contains "$(cat "$LOG")" "SWEEP_NO_COVERAGE" "16. no_work + empty pool files nothing"

# ── 17. missing / non-JSON .enabled → fail CLOSED ───────────────────────────
reset_case
export STUB_STATUS_BODY='{"config_error":null,"storage_error":null}'
run_driver
assert_eq "$RC" 1 "17a. missing .enabled exits RED (1)"
assert_contains "$(cat "$LOG")" "unclassifiable" "17a. missing .enabled files the unclassifiable incident"
reset_case
export STUB_STATUS_BODY='Internal Server Error'
run_driver
assert_eq "$RC" 1 "17b. non-JSON 200 exits RED (1)"
assert_contains "$(cat "$LOG")" "unclassifiable" "17b. non-JSON 200 files the unclassifiable incident"

# ── 18. watcher.running=false → WATCHER_DOWN (jq `//` false bug, #2843) ─────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null false 1)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
run_driver
assert_eq "$RC" 1 "18. backed_up is RED while the staleness watcher is dead (loud)"
assert_match "$(cat "$LOG")" 'GH POST .*/issues .*WATCHER_DOWN' "18. running=false is honored and files WATCHER_DOWN"
assert_contains "$OUT" "exiting RED" "18. the run is RED because it filed an incident"

# ── 19. prefix without DEFAULT archive while OFF → SWEEP_OFF_STALE ───────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST=""
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "19. never-backed-up pool + OFF exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_OFF_STALE "19. never-backed-up pool files SWEEP_OFF_STALE"

# ── 20. config_error secret is redacted before publication ──────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY='{"enabled":false,"config_error":"must be base64 (got '"'"'SECRETKEY9'"'"'...)","storage_error":null,"per_team":{},"last_sweep":{"last_sweep_at":"2026-08-09T23:30:00Z","last_team_count":0},"watcher":{"running":true,"age_minutes":1}}'
run_driver
assert_eq "$RC" 1 "20. config-broken off exits RED (1)"
assert_contains "$(cat "$LOG")" "<redacted>" "20. the published body carries the redaction marker"
assert_not_contains "$(cat "$LOG")" "SECRETKEY9" "20. the key prefix is NOT published"
assert_not_contains "$OUT" "SECRETKEY9" "20. the key prefix is NOT logged either"

# ── 21. 412 + R2 object with an OPEN issue_number → no duplicate ────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
export STUB_GET_BODY='{"kind":"SWEEP_CONFIG_ERROR","issue_number":42}'
export GH_ISSUE_STATE=open
run_driver
assert_eq "$RC" 1 "21. object-tracked incident is still RED (1)"
assert_contains "$OUT" "already tracked by open issue #42" "21. the object is trusted over the search"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "21. no duplicate issue is filed"

# ── 22. 412 + R2 object with a CLOSED issue_number → re-files ───────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
export STUB_GET_BODY='{"kind":"SWEEP_CONFIG_ERROR","issue_number":42}'
export GH_ISSUE_STATE=closed
run_driver
assert_eq "$RC" 1 "22. closed-issue recurrence exits RED (1)"
assert_match "$(cat "$LOG")" "GH POST .*/issues \{" "22. a closed issue RE-FILES (not swallowed)"
assert_match "$(cat "$LOG")" "AWS put-object key=ops/alerts/SWEEP_CONFIG_ERROR/global.json" "22. the adopted issue number is backfilled"

# ── 23. WATCHER_DOWN self-heals on watcher EVIDENCE even on no_work (R3) ────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null true 1)"
export STUB_SWEEP_BODY='{"status":"no_work","teams_backed_up":0}'
export GH_ISSUE_WATCHER_DOWN=66
run_driver
assert_eq "$RC" 1 "23. no_work is still RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "23. no_work files SWEEP_NO_COVERAGE"
assert_match "$(cat "$LOG")" "GH PATCH .*/issues/66" "23. a fresh heartbeat still clears WATCHER_DOWN"

# ── 24. purge ride-along failure → RED ──────────────────────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export STUB_PURGE_CODE=500
run_driver
assert_eq "$RC" 1 "24. purge failure exits RED (1)"
assert_contains "$OUT" "purge ride-along failed" "24. the failure names the purge leg"

# ── 25. reconcile ride-along failure → RED ──────────────────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export STUB_RECONCILE_CODE=500
run_driver
assert_eq "$RC" 1 "25. reconcile failure exits RED (1)"
assert_contains "$OUT" "reconcile ride-along failed" "25. the failure names the reconcile leg"

# ── 26. bare/unquoted secret runs are redacted too (security review) ────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false "load_config failed: ghp_16C7e42F292c6912E7710c838347Ae178B4a at /home/runner/app/x.py" null)"
run_driver
assert_eq "$RC" 1 "26. bare-run secret + broken config exits RED (1)"
assert_not_contains "$(cat "$LOG")" "ghp_16C7e42F292c6912E7710c838347Ae178B4a" "26. an unquoted PAT is NOT published"
assert_not_contains "$OUT" "ghp_16C7e42F292c6912E7710c838347Ae178B4a" "26. an unquoted PAT is NOT logged"

# ── 27. last_sweep error text is redacted before publication ────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(printf '{"enabled":true,"config_error":null,"storage_error":null,"per_team":{},"last_sweep":{"last_sweep_at":"%s","last_team_count":1,"graph_failures":[{"error":"boom ghp_16C7e42F292c6912E7710c838347Ae178B4a"}]},"watcher":{"running":true,"age_minutes":1}}' "$TS_RECENT")"
export STUB_SWEEP_BODY='{"status":"no_work","teams_backed_up":0}'
run_driver
assert_eq "$RC" 1 "27. 0-team + pool data exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "27. no-coverage is filed"
assert_not_contains "$(cat "$LOG")" "ghp_16C7e42F292c6912E7710c838347Ae178B4a" "27. last_sweep error text is NOT published"

# ── 28. a 404 (deleted) tracked issue re-files; a 500 blip does not duplicate
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
export STUB_GET_BODY='{"kind":"SWEEP_CONFIG_ERROR","issue_number":42}'
export STUB_ISSUE_CODE=404
run_driver
assert_eq "$RC" 1 "28a. 404 tracked issue is still RED (1)"
assert_match "$(cat "$LOG")" "GH POST .*/issues \{" "28a. a deleted issue re-files the recurrence"
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
export STUB_GET_BODY='{"kind":"SWEEP_CONFIG_ERROR","issue_number":42}'
export STUB_ISSUE_CODE=500
run_driver
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "28b. a transient 500 assumes open (no duplicate)"

# ── 29. resolve deletes BOTH global.json and _.json (#2844) ─────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export GH_ISSUE_APP_DOWN=99
run_driver
assert_eq "$RC" 0 "29. healthy run exits 0"
assert_match "$(cat "$LOG")" "AWS delete-object key=ops/alerts/APP_DOWN/global.json" "29. the driver dedup object is deleted on resolve"
assert_match "$(cat "$LOG")" "AWS delete-object key=ops/alerts/APP_DOWN/_.json" "29. the server-side dedup object is deleted too"

# ── 30. missing .watcher block neither files nor self-heals WATCHER_DOWN ────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(printf '{"enabled":true,"config_error":null,"storage_error":null,"per_team":{},"last_sweep":{"last_sweep_at":"%s","last_team_count":1},"watcher":{}}' "$TS_RECENT")"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export GH_ISSUE_WATCHER_DOWN=66
run_driver
assert_eq "$RC" 0 "30. healthy sweep with an unclassifiable watcher exits 0"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues .*WATCHER_DOWN" "30. no WATCHER_DOWN is filed from a missing block"
assert_not_match "$(cat "$LOG")" "GH PATCH .*/issues/66" "30. a missing block does NOT close WATCHER_DOWN"

# ── 31. degraded is healthy; no_eligible_teams is the no-coverage family ────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"degraded","teams_backed_up":1}'
run_driver
assert_eq "$RC" 0 "31a. degraded exits 0"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues .*SWEEP_NO_COVERAGE" "31a. degraded files no no-coverage"
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_eligible_teams","teams_backed_up":0}'
run_driver
assert_eq "$RC" 1 "31b. no_eligible_teams + pool data exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "31b. no_eligible_teams files SWEEP_NO_COVERAGE"

# ── 32. lock held with no usable last_sweep + pool data → RED ───────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY='{"enabled":true,"config_error":null,"storage_error":null,"per_team":{},"last_sweep":null,"watcher":{"running":true,"age_minutes":1}}'
export STUB_SWEEP_BODY='{"status":"already_running"}'
run_driver
assert_eq "$RC" 1 "32. unverifiable lock + pool data exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "32. unverifiable lock files SWEEP_NO_COVERAGE"

# ── 33. sweep error / non-JSON body → SWEEP_NO_COVERAGE + RED ───────────────
reset_case
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"error"}'
run_driver
assert_eq "$RC" 1 "33a. sweep error exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "33a. sweep error files SWEEP_NO_COVERAGE"
reset_case
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='not json at all'
run_driver
assert_eq "$RC" 1 "33b. non-JSON sweep body exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "33b. non-JSON sweep body files SWEEP_NO_COVERAGE"

# ── 34. missing GITHUB_TOKEN → fail closed ─────────────────────────────────
reset_case
export STUB_STATUS_BODY="$(status_body true null null)"
unset GITHUB_TOKEN
run_driver
assert_eq "$RC" 1 "34. missing GITHUB_TOKEN exits RED (1)"
assert_contains "$OUT" "GITHUB_TOKEN not set" "34. the driver refuses to run blind"
export GITHUB_TOKEN="test-token"

# ── 35. a per-team listing failure is unmeasured, not a false stale ────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export STUB_LIST_FAIL_TEAM=1
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "35. per-team listing failure while OFF exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_OFF_STALE "35. an unmeasured team is loud (not a false stale)"
assert_not_contains "$OUT" "no default archive" "35. a failed read is not reported as a missing archive"

# ── 36. positive self-heals (recovered config + measured-fresh pool) ───────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false null null)"
export GH_ISSUE_SWEEP_CONFIG_ERROR=44
export GH_ISSUE_SWEEP_OFF_STALE=45
run_driver
assert_eq "$RC" 0 "36. recovered config + measured-fresh pool exits 0"
assert_match "$(cat "$LOG")" "GH PATCH .*/issues/44" "36. SWEEP_CONFIG_ERROR self-heals when the config is readable"
assert_match "$(cat "$LOG")" "GH PATCH .*/issues/45" "36. SWEEP_OFF_STALE self-heals on a measured-fresh pool"

# ── 37. multi-team pool: `--output text` is ONE tab-separated line (P1) ────
# Review P1 (bug-deep, conf 98): aws renders a list as a single tab-separated
# line, so a bare `while read` measured only the LAST team. teamA must still be
# seen — its stale default drives POOL_STALE / SWEEP_OFF_STALE.
reset_case
export R2_TEAMS=$'backups/teamZ/\tbackups/teamA/'
export R2_DEFAULT_LIST=""
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "37. a 2-team tab-separated pool while OFF exits RED (1)"
assert_match "$OUT" "team teamA: team prefix present but no default archive" "37. the NON-LAST team is measured too"
assert_filed "$(cat "$LOG")" SWEEP_OFF_STALE "37. the non-last stale team drives SWEEP_OFF_STALE"

# ── 38. held lock + UNMEASURED pool → SWEEP_NO_COVERAGE (review P1) ────────
reset_case
export STUB_LIST_FAIL=1
export STUB_STATUS_BODY='{"enabled":true,"config_error":null,"storage_error":null,"per_team":{},"last_sweep":null,"watcher":{"running":true,"age_minutes":1}}'
export STUB_SWEEP_BODY='{"status":"already_running"}'
run_driver
assert_eq "$RC" 1 "38. stuck lock + unmeasurable pool exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "38. unknown pool is never read as empty for a held lock"
assert_not_contains "$(cat "$LOG")" "leaving silent" "38. the unmeasurable lock is never silently dropped"

# ── 39. redaction shapes (security review F1) ─────────────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"dsn falkor://usr:SuperSecret123@cloud.example:6380 and Authorization: Bearer abcDEFghiJKL012345678 also key SECRETVALUE1234567890"' null)"
run_driver
assert_eq "$RC" 1 "39. secret-bearing config error exits RED (1)"
for leaked in SuperSecret123 abcDEFghiJKL012345678 SECRETVALUE1234567890; do
  assert_not_contains "$(cat "$LOG")" "$leaked" "39. the DSN/header/value '$leaked' is NOT published"
  assert_not_contains "$OUT" "$leaked" "39. the DSN/header/value '$leaked' is NOT logged"
done
assert_match "$OUT" "<redacted>" "39. the redaction marker is present"

# ── 40. the purge failure body is redacted ────────────────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export STUB_PURGE_CODE=500
export STUB_PURGE_BODY='{"status":"error","detail":"mirror misconfig with key TESTONLYTOKENAbCdEfGhIjKlMnOpQrSt"}'
run_driver
assert_eq "$RC" 1 "40. a purge failure is RED (1)"
assert_not_contains "$(cat "$LOG")" "TESTONLYTOKENAbCdEfGhIjKlMnOpQrSt" "40. the purge body secret is NOT published"

# ── 41. storage_error while ENABLED is loud and blocks R2_DOWN self-heal ───
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null '"heartbeat read: boom"')"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export GH_ISSUE_R2_DOWN=88
run_driver
assert_eq "$RC" 1 "41. storage_error while enabled exits RED (1)"
assert_match "$(cat "$LOG")" "AWS put-object key=ops/alerts/R2_DOWN/global.json" "41. storage_error while enabled records R2_DOWN"
assert_not_match "$(cat "$LOG")" "GH PATCH .*/issues/88" "41. R2_DOWN is NOT closed while storage_error is set"

# ── 42. no_work with graph_totals.backed_up>0 is not a coverage gap ───────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_work","teams_backed_up":0,"graph_totals":{"attempted":2,"backed_up":1,"failed":1}}'
run_driver
assert_eq "$RC" 0 "42. a custom-graph backup is not a 0-coverage outage"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues .*SWEEP_NO_COVERAGE" "42. no false SWEEP_NO_COVERAGE"

# ── 43. APP_DOWN is RED (any filing is RED) ──────────────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export SIMULATE_APP_DOWN=true
run_driver
assert_eq "$RC" 1 "43. APP_DOWN exits RED (1)"
assert_filed "$(cat "$LOG")" APP_DOWN "43. APP_DOWN is filed"

# ── 44. an index read failure is unmeasured, not "no index" ──────────────
# STUB_INDEX_FAIL makes the classification-index get-object fail with a
# non-404 error while the legacy-flat listing succeeds.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_FLAT_LIST='[["backups/teamA/2024/dump.enc","2024-01-01T00:00:00Z"]]'
export STUB_INDEX_FAIL=1
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "44. an unreadable flat index while OFF exits RED (1)"
assert_contains "$OUT" "legacy-flat index read FAILED" "44. the failed index read is surfaced"
assert_filed "$(cat "$LOG")" SWEEP_OFF_STALE "44. unreadable index is unmeasured → no confirmed pause"

# ── 45. a measured-fresh off pool resolves a stale R2_DOWN ────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false null null)"
export GH_ISSUE_R2_DOWN=88
run_driver
assert_eq "$RC" 0 "45. recovered storage exits 0"
assert_match "$(cat "$LOG")" "GH PATCH .*/issues/88" "45. R2_DOWN self-heals once storage answers while OFF"

echo ""
echo "registry-cron.test.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
