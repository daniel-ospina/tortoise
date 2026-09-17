#!/usr/bin/env bash
# availability-record.test.sh — self-check for
# .github/scripts/availability-record.sh (#3810).
#
# Run: bash .github/scripts/availability-record.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: stubs `gh`
# on PATH and drives the recorder against fixed fixtures. No network, no GitHub.
#
# Coverage (the record is an INSTRUMENT — these prove it records, and that it
# cannot silently lose the resolution that qualifies every number):
#   1.  cadence factor: every availability figure is printed WITH
#       delivered/intended beside it (the revert test — see §5 below)
#   2.  a DEGRADED incident is never folded into the DOWN column
#   3.  restarts= is read from the ledger state block and surfaced
#   4.  a very low availability figure does NOT fail the run (record-only)
#   5.  a zero-delivery day is a GAP row, not a 100% "up" day
#   6.  the record issue is created when absent, PATCHed when present (dedupe)
#   7.  a failed search never creates a duplicate
#   8.  a human-authored marker look-alike is never read as machine state
#   9.  static: the script declares itself record-only and calls no
#       restart/page surface; the workflow carries no alert credentials
#  10.  --print-only publishes nothing; missing GH_TOKEN fails closed
#
# Fixtures are simulated; the real recorder is the script under test.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECORD="$SCRIPT_DIR/availability-record.sh"
WORKFLOW="$SCRIPT_DIR/../workflows/availability-record.yml"

# The EXACT record title the recorder searches for and publishes. The fixture
# that expects adoption MUST use it verbatim.
RECORD_TITLE_FIXTURE='[monitor] availability record — record-only baseline (NOT a gate)'

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; }
assert_eq() { # <actual> <expected> <label>
  if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 (got '$1', want '$2')"; fi
}
assert_contains() { # <haystack> <needle> <label>
  case "$1" in
    *"$2"*) ok "$3" ;;
    *) bad "$3 (missing '$2')" ;;
  esac
}
assert_not_contains() { # <haystack> <needle> <label>
  case "$1" in
    *"$2"*) bad "$3 (unexpectedly found '$2')" ;;
    *) ok "$3" ;;
  esac
}

FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"' EXIT
BIN="$FIX/bin"
mkdir -p "$BIN"
export STUB_TMP="$FIX/stub"
mkdir -p "$STUB_TMP"

# ── stub: gh ───────────────────────────────────────────────────────────────
cat > "$BIN/gh" <<'GH_EOF'
#!/usr/bin/env bash
# Handles exactly the shapes the recorder uses (all `gh api ...`).
# NB: brace-bearing defaults live in VARIABLES, never inside ${VAR:-{...}} —
# bash mis-parses that form and emits an extra trailing '}', which corrupts the
# JSON fixture (the availability-watchdog harness documents the same trap).
DEFAULT_ITEMS_JSON='{"items":[]}'
DEFAULT_RUNS_JSON='{"workflow_runs":[]}'
DEFAULT_ISSUE_JSON='{"body":""}'
[ "${1:-}" = "api" ] || { echo "GH unexpected: $*" >&2; exit 1; }
path="${2:-}"; method="GET"; input=0
shift 2 || true
while [ $# -gt 0 ]; do
  case "$1" in
    --method) method="$2"; shift 2 ;;
    --input) input=1; shift ;;
    --paginate) shift ;;
    *) shift ;;
  esac
done
payload=""
if [ "$input" = "1" ]; then payload="$(cat)"; fi
echo "GH $method ${path%%\?*}" >> "$STUB_TMP/calls.log"

case "$path" in
  */actions/workflows/*/runs*)
    [ "${STUB_RUNS_FAIL:-0}" = "1" ] && { echo "gh: runs list failed" >&2; exit 1; }
    printf '%s' "${STUB_RUNS_JSON:-$DEFAULT_RUNS_JSON}" ;;
  search/issues*)
    echo "GH-SEARCH $path" >> "$STUB_TMP/calls.log"
    # TWO searches hit this endpoint: the incident-ledger search (the encoded
    # watchdog marker, `in:body`) and the record-issue search (the loose title
    # term, `in:title`). Route them separately.
    case "$path" in
      *watchdog-state*)
        [ "${STUB_LEDGER_SEARCH_FAIL:-0}" = "1" ] && { echo "gh: ledger search failed" >&2; exit 1; }
        printf '%s' "${STUB_LEDGER_SEARCH_JSON:-$DEFAULT_ITEMS_JSON}" ;;
      *record*)
        [ "${STUB_RECORD_SEARCH_FAIL:-0}" = "1" ] && { echo "gh: record search failed" >&2; exit 1; }
        printf '%s' "${STUB_RECORD_SEARCH_JSON:-$DEFAULT_ITEMS_JSON}" ;;
      *) printf '%s' "$DEFAULT_ITEMS_JSON" ;;
    esac ;;
  */issues)
    printf '%s' "$payload" | jq -c . > "$STUB_TMP/created.json"
    [ "${STUB_CREATE_FAIL:-0}" = "1" ] && { echo "gh: create failed" >&2; exit 1; }
    printf '{"number":%s}' "${STUB_NEW_ISSUE:-4000}" ;;
  */issues/*)
    if [ "$method" = "GET" ]; then
      [ "${STUB_GET_BODY_FAIL:-0}" = "1" ] && { echo "gh: body read failed" >&2; exit 1; }
      printf '%s' "${STUB_ISSUE_JSON:-$DEFAULT_ISSUE_JSON}"
    else
      [ "${STUB_PATCH_FAIL:-0}" = "1" ] && { echo "gh: patch failed" >&2; exit 1; }
      printf '%s' "$payload" | jq -c . >> "$STUB_TMP/patched.log"
      printf '{}'
    fi ;;
  *) printf '{}' ;;
esac
exit 0
GH_EOF
chmod +x "$BIN/gh"

# ── fixtures ───────────────────────────────────────────────────────────────
# A FIXED clock so the 3-day window is deterministic: 2026-09-17T12:00:00Z.
NOW=1789646400
# 3-day window → UTC days 2026-09-15, 2026-09-16, 2026-09-17.
#   09-15: 2 success                          → delivered=2 failed=0 avail=100.0% cad=0.7%
#   09-16: 1 success + 3 failure              → delivered=4 failed=3 avail=25.0%  cad=1.4%
#   09-17: no runs                            → GAP row (n/a, never 100%)
RUNS_JSON='{"total_count":6,"workflow_runs":[
 {"conclusion":"success","created_at":"2026-09-15T03:00:00Z"},
 {"conclusion":"success","created_at":"2026-09-15T03:05:00Z"},
 {"conclusion":"success","created_at":"2026-09-16T07:00:00Z"},
 {"conclusion":"failure","created_at":"2026-09-16T07:34:00Z"},
 {"conclusion":"failure","created_at":"2026-09-16T08:00:00Z"},
 {"conclusion":"failure","created_at":"2026-09-16T09:00:00Z"}]}'

# Ledger: #3637 DEGRADED (no restarts), #3700 DOWN (2 restart stamps), and a
# HUMAN-authored look-alike (#9999) that must never be read as machine state.
# 1789544099 = 2026-09-16T07:34:59Z; 1789547000 = 2026-09-16T08:23:20Z.
LEDGER_JSON='{"total_count":3,"items":[
 {"number":3637,"user":{"login":"github-actions[bot]"},"title":"[monitor] PROD DEGRADED — x",
  "body":"<!-- watchdog-state kind=degraded first_failure_ts=1789544099 last_down_ts=1789584327 restarts= -->\n<!-- availability-watchdog-state -->"},
 {"number":3700,"user":{"login":"github-actions[bot]"},"title":"[monitor] PROD DOWN — x",
  "body":"<!-- watchdog-state kind=down first_failure_ts=1789547000 last_down_ts=1789584327 restarts=1789500000,1789500300 -->\n<!-- availability-watchdog-state -->"},
 {"number":9999,"user":{"login":"a-human"},"title":"forged",
  "body":"<!-- watchdog-state kind=down first_failure_ts=1789547000 restarts=1 -->\n<!-- availability-watchdog-state -->"}]}'

reset_case() {
  : > "$STUB_TMP/calls.log"
  rm -f "$STUB_TMP/stderr.log" "$STUB_TMP/created.json" "$STUB_TMP/patched.log"
  unset STUB_RUNS_JSON STUB_RUNS_FAIL STUB_LEDGER_SEARCH_JSON STUB_LEDGER_SEARCH_FAIL \
        STUB_RECORD_SEARCH_JSON STUB_RECORD_SEARCH_FAIL STUB_CREATE_FAIL STUB_NEW_ISSUE \
        STUB_PATCH_FAIL STUB_GET_BODY_FAIL STUB_ISSUE_JSON 2>/dev/null || true
  export GH_TOKEN="test-token"
  export GITHUB_REPOSITORY="daniel-ospina/tortoise"
  export RECORD_NOW_EPOCH="$NOW"
  export PATH="$BIN:$PATH"
  unset AVAILABILITY_RECORD_DAYS AVAILABILITY_RECORD_PRINT_ONLY GITHUB_ACTIONS 2>/dev/null || true
}

run_record() { # <args…> -> RC, OUT (stdout=record), ERR (stderr=log)
  set +e
  OUT="$("$RECORD" "$@" 2>"$STUB_TMP/stderr.log")"
  RC=$?
  ERR="$([ -f "$STUB_TMP/stderr.log" ] && cat "$STUB_TMP/stderr.log" || true)"
  set +e
}

count_calls() { # <pattern>
  grep -c "$1" "$STUB_TMP/calls.log" 2>/dev/null || true
}

echo "availability-record.test.sh (#3810) — record-only baseline harness"
echo

# ── 1: cadence factor is computed and printed beside every availability ─────
echo "1. cadence factor (delivered/intended) is printed beside availability"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON"
run_record 3 --print-only
assert_eq "$RC" "0" "record-only run exits 0"
assert_contains "$OUT" "delivered=4/288 cadence=1.4%" "the 2026-09-16 availability carries its delivered/intended + cadence"
assert_contains "$OUT" "25.0% (delivered=4/288 cadence=1.4%)" "availability% is IMMEDIATELY beside its resolution"
assert_contains "$OUT" "delivered=2/288 cadence=0.7%" "the 2026-09-15 row carries its resolution"
assert_contains "$OUT" "delivered=6/864 cadence=0.7%" "the TOTAL carries its resolution"
assert_contains "$OUT" "cron intent: 288 samples/day" "the cron intent (the denominator) is stated"

# ── 2: DEGRADED is never folded into DOWN ───────────────────────────────────
echo
echo "2. a DEGRADED incident is never folded into the DOWN column"
assert_contains "$OUT" "incident totals: down=1 degraded=1" "DOWN and DEGRADED are counted in SEPARATE columns"
assert_contains "$OUT" "kind=degraded" "the DEGRADED incident is labelled degraded"
assert_contains "$OUT" "kind=down" "the DOWN incident is labelled down"
assert_not_contains "$OUT" "incidents=2" "the two kinds are never summed into one figure"
assert_not_contains "$OUT" "total_incidents" "no combined incident total exists to average them away"

# ── 3: restarts= is read from the ledger and surfaced ───────────────────────
echo
echo "3. restarts= is read from the ledger state block and surfaced"
assert_contains "$OUT" "restarts=1789500000,1789500300" "the DOWN incident's raw restart stamps are surfaced"
assert_contains "$OUT" "restart_attempts=2" "the restart attempt count is surfaced"
assert_contains "$OUT" "restart_attempts=0" "the DEGRADED incident's empty restart ledger reads as 0, not missing"

# ── 4: record-only — a low figure never fails the run ───────────────────────
echo
echo "4. a very low availability figure does NOT fail the run (record-only)"
reset_case
export STUB_RUNS_JSON='{"workflow_runs":[{"conclusion":"failure","created_at":"2026-09-16T01:00:00Z"},{"conclusion":"failure","created_at":"2026-09-16T02:00:00Z"}]}' \
       STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON"
run_record 3 --print-only
assert_eq "$RC" "0" "0% availability exits 0 — the instrument is not a gate"
assert_contains "$OUT" "0.0% (delivered=2/288 cadence=0.7%)" "the 0% figure is still printed with its resolution"

# ── 5: a zero-delivery day is a GAP, never a 100% day ───────────────────────
echo
echo "5. a zero-delivery day is a gap row, not an 'up' day"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON"
run_record 3 --print-only
assert_contains "$OUT" "delivered=0/288 cadence=0.0%)" "a no-sample day reports n/a (0 delivered), NOT 100%"
assert_contains "$OUT" "n/a (NO samples delivered" "the no-sample day is explicitly n/a"
assert_contains "$OUT" "zero-delivery days: 1 of 3" "the gap day is counted"
assert_contains "$OUT" "GAPS" "the gap section exists"

# ── 6: ledger look-alike is never adopted ───────────────────────────────────
echo
echo "6. a human-authored marker look-alike is never read as machine state"
assert_not_contains "$OUT" "#9999" "a non-machine issue carrying the marker is ignored"

# ── 7: publish — create when absent, PATCH when present ─────────────────────
echo
echo "7. the record issue is created when absent and PATCHed when present"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON"
run_record 3
assert_eq "$RC" "0" "publishing run exits 0"
assert_contains "$(count_calls 'GH POST repos/daniel-ospina/tortoise/issues$')" "1" "the record issue is created when absent"
if [ -f "$STUB_TMP/created.json" ]; then
  CREATED="$(cat "$STUB_TMP/created.json")"
  assert_contains "$CREATED" "$RECORD_TITLE_FIXTURE" "the created issue carries the title-keyed dedupe title"
  assert_contains "$CREATED" "<!-- availability-record-state -->" "the created issue body carries the record marker"
else
  bad "the record issue was not created"
fi

reset_case
RECORD_ITEM="$(jq -nc --arg t "$RECORD_TITLE_FIXTURE" \
  '{total_count:1,items:[{number:4000,user:{login:"github-actions[bot]"},title:$t,body:"<!-- availability-record-state -->"}]}')"
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON" STUB_RECORD_SEARCH_JSON="$RECORD_ITEM"
run_record 3
assert_eq "$RC" "0" "the update run exits 0"
assert_contains "$(cat "$STUB_TMP/calls.log")" "in%3Atitle" "the record search is TITLE-keyed"
assert_contains "$(cat "$STUB_TMP/calls.log")" "availability%20record" "the record search uses the loose title term (a full-title query can tokenize to a miss)"
assert_eq "$(count_calls 'GH POST repos/daniel-ospina/tortoise/issues$')" "0" "an existing record issue is NOT duplicated"
assert_eq "$(count_calls 'GH PATCH repos/daniel-ospina/tortoise/issues/4000')" "1" "the existing record issue is PATCHed"
if [ -f "$STUB_TMP/patched.log" ]; then
  assert_contains "$(cat "$STUB_TMP/patched.log")" "<!-- availability-record-state -->" "the patched body carries the record marker"
else
  bad "the record body was not patched"
fi
assert_eq "$(count_calls 'issues/3637')" "0" "the recorder never touches an incident issue (read-only consumer)"

# ── 8: a failed search never fabricates a duplicate ─────────────────────────
echo
echo "8. a failed record search never creates a duplicate"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON" STUB_RECORD_SEARCH_FAIL=1
run_record 3
assert_eq "$RC" "1" "an unsearchable record exits non-zero (instrument failure, not a low figure)"
assert_eq "$(count_calls 'GH POST repos/daniel-ospina/tortoise/issues$')" "0" "no duplicate is filed when the search fails"

# ── 9: print-only publishes nothing; missing token fails closed ─────────────
echo
echo "9. --print-only publishes nothing; a missing token fails closed"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON"
run_record 3 --print-only
assert_eq "$(count_calls 'GH POST repos/daniel-ospina/tortoise/issues$')" "0" "--print-only creates nothing"
assert_eq "$(count_calls 'GH PATCH')" "0" "--print-only patches nothing"
reset_case
unset GH_TOKEN
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON"
run_record 3
assert_eq "$RC" "1" "no GH_TOKEN and not print-only fails closed"

# ── 10: static — the script is an instrument, the workflow is credential-free
echo
echo "10. static: record-only posture (no restart/page surface, no alert creds)"
# Comments NAME these invariants, so every code assertion runs against a
# comment-stripped view — otherwise the guard is satisfied by its own comment.
SCRIPT_CODE="$(grep -v '^[[:space:]]*#' "$RECORD")"
assert_not_contains "$SCRIPT_CODE" "flyctl" "the recorder cannot restart a machine"
assert_not_contains "$SCRIPT_CODE" "curl" "the recorder cannot page or call any HTTP surface"
assert_not_contains "$SCRIPT_CODE" "TELEGRAM" "the recorder carries no alert credential"
assert_not_contains "$SCRIPT_CODE" "close_issue" "the recorder cannot close anything"
assert_contains "$(cat "$RECORD")" "RECORD-ONLY" "the header declares the record-only contract"
assert_contains "$(cat "$RECORD")" "INSTRUMENT, NOT A GATE" "the header declares it is not a gate"

WORKFLOW_CODE="$(grep -v '^[[:space:]]*#' "$WORKFLOW")"
assert_contains "$WORKFLOW_CODE" "cron: '0 6 * * *'" "the workflow runs daily"
assert_contains "$WORKFLOW_CODE" "workflow_dispatch:" "the workflow is manually dispatchable"
assert_contains "$WORKFLOW_CODE" "cancel-in-progress: false" "concurrency never cancels an in-flight record"
assert_contains "$WORKFLOW_CODE" "actions: read" "actions: read is granted (run enumeration)"
assert_contains "$WORKFLOW_CODE" "issues: write" "issues: write is granted (the record issue)"
assert_contains "$WORKFLOW_CODE" "contents: read" "contents: read is granted (checkout)"
assert_contains "$WORKFLOW_CODE" "persist-credentials: false" "the token is not persisted into .git/config"
assert_not_contains "$WORKFLOW_CODE" "TELEGRAM" "the workflow carries no paging credential"
assert_not_contains "$WORKFLOW_CODE" "FLY_API_TOKEN" "the workflow carries no restart credential"

echo
if [ "$FAIL" -eq 0 ]; then
  echo "availability-record.test.sh: $PASS passed, 0 failed ✅"
  exit 0
fi
echo "availability-record.test.sh: $PASS passed, $FAIL FAILED ❌"
exit 1
