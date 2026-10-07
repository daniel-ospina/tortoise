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
#  11.  (#3896) a >1,000-run window is fetched COMPLETELY — one bounded UTC-day
#       slice per day, asserted against the API's own total_count, so no day is
#       clipped into a false zero-delivery row
#  12.  (#3896) a TRUNCATED fetch is an INSTRUMENT FAILURE that publishes
#       nothing — never a no-sample day; an EMPTY/unparseable run response is an
#       unparseable ANSWER, not an empty window (12b); a slice AT the cap is
#       refused, because at the cap the two counts cannot tell complete from
#       clipped (12c); and a malformed trust anchor is refused, not coerced to
#       zero runs everywhere (12d)
#  13.  (#3896) render REFUSES to print a record from an unverified fetch — and
#       re-derives the marker's own consistency (enumerated == reported) and
#       its window coverage, so a truncation that slipped past the fetch checks
#       is still refused (13b)
#  14.  a failed run list / ledger search exits non-zero and publishes nothing
#  15.  (#3896 P2-1) an inconclusive run is excluded from BOTH the numerator
#       and the denominator of availability (a cancelled run cannot inflate it)
#  16.  (#3896 P2-2) a non-numeric RECORD_CRON_PER_DAY is normalized, not a
#       bare `set -u` abort
#  17.  (#3896 P2-3) a state block missing a matchable field is warned about and
#       still listed/counted — never silently dropped
#  18.  (#3896 P2-6) the in-progress UTC day is scored on elapsed intent and
#       marked, never presented as a complete day
#  19.  a CUSTOM cron intent keeps today's elapsed intent consistent with it
#       (the record never states an intent its own denominator contradicts)
#  20.  a numeric-but-unrenderable incident epoch lands as an UNDATED row, so a
#       restart stamp is never printed in the first_observed column (bash `read`
#       collapses an empty MIDDLE field)
#  21.  a dispatch in the FIRST cron interval of a UTC day cannot produce a /0
#       denominator or the malformed cadence value `n/a%` — including the
#       reachable all-zero window, whose TOTAL used to print `cadence=n/a%`
#       while its own cadence column said 0.0%
#  22.  a returned run with no created_at fails the fetch CLOSED — it used to be
#       dropped with a stderr-only warning while the marker still certified the
#       pre-drop count, so the body could print "COMPLETE — N enumerated" beside
#       an availability figure computed over the reduced set (OVERSTATED when the
#       dropped run was a failure). The overstatement is REMOVED, not annotated.
#  23.  (#3810 P2-1) a returned run whose created_at is a VALID date OUTSIDE the
#       window — or a created_at that is present but unusable (whitespace /
#       garbage) — cannot reach a rendered row. The render reconciles the
#       marker's enumeration against the runs that land on a window day and
#       refuses; the fetch drops an unusable created_at closed. Otherwise the
#       marker certifies "COMPLETE — N enumerated" while the body asserts zero
#       delivery: the same false-absence claim, one stage later.
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
# Percent-decoder for the query string (`:` is sent as %3A by jq's @uri).
urldecode() { printf '%b' "${1//%/\\x}"; }
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
    echo "GH-RUNS $path" >> "$STUB_TMP/calls.log"
    # An anomalous 200 with NO body: `gh` exits 0 and emits nothing.
    [ "${STUB_RUNS_EMPTY:-0}" = "1" ] && { printf ''; exit 0; }
    # A RAW body, verbatim: the seam for a shape the per-day filter would itself
    # reject — e.g. a run with no created_at, which the filter's day comparison
    # excludes BEFORE the recorder could ever see it.
    [ -n "${STUB_RUNS_RAW:-}" ] && { printf '%s' "$STUB_RUNS_RAW"; exit 0; }
    POP="${STUB_RUNS_POPULATION:-${STUB_RUNS_JSON:-$DEFAULT_RUNS_JSON}}"
    CAP="${STUB_RUNS_CAP:-1000}"
    CREATED_RAW="${path#*created=}"
    if [ "$CREATED_RAW" = "$path" ]; then CREATED_RAW=""; else CREATED_RAW="${CREATED_RAW%%&*}"; fi
    CREATED="$(urldecode "$CREATED_RAW")"
    # Emulate GitHub EXACTLY on the two behaviours #3896 turns on:
    #   * the `created` filter decides the population (so a per-day slice returns
    #     only that day's runs);
    #   * `total_count` is the TRUE filtered count and is NOT clipped, while the
    #     returned `workflow_runs` ARE clipped to STUB_RUNS_CAP (default 1000),
    #     newest first. A single wide query therefore loses its tail — which is
    #     how this harness can prove the recorder no longer does one.
    printf '%s' "$POP" | jq -c --arg created "$CREATED" --argjson cap "$CAP" --argjson force "${STUB_RUNS_TOTAL_FORCE:-null}" '
      . as $pop
      | ( if $created == "" then { from: null, to: null }
          elif ($created | startswith(">=")) then { from: $created[2:], to: null }
          elif ($created | startswith(">"))  then { from: $created[1:], to: null }
          elif ($created | test("\\."))     then { from: ($created | split("..")[0]), to: ($created | split("..")[1]) }
          else { from: $created, to: null } end ) as $r
      | [ $pop.workflow_runs[]?
          | select(($r.from == null or (.created_at // "") >= $r.from)
                   and ($r.to == null or (.created_at // "") <= $r.to)) ]
      | sort_by(.created_at) | reverse
      | { total_count: (if $force == null then length else $force end), workflow_runs: .[0:$cap] }' ;;
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

# ── a >1,000-run fixture (#3896) ────────────────────────────────────────────
# 14 UTC days x 110 runs = 1,540 runs: 1.54x GitHub's documented 1,000-result
# cap for a `created`-filtered query, so a single wide query MUST clip the tail
# (and the clipped days render as zero-delivery rows). The recorder's chunked
# fetch must not. Runs land at 00:01-01:50 UTC of each day; every 11th fails.
BIG_DAYS=14
BIG_PER_DAY=110
BIG_TOTAL=$((BIG_DAYS * BIG_PER_DAY))
# 13 complete days (288 each) + today, 12h in = 144 elapsed 5-minute intervals.
BIG_TOTAL_INTENDED=$(( (BIG_DAYS - 1) * 288 + 144 ))
BIG_POP="$(jq -nc --argjson now "$NOW" --argjson days "$BIG_DAYS" --argjson per "$BIG_PER_DAY" '
  [ range(0; $days) as $k
    | ( (($now - (($days - 1 - $k) * 86400)) / 86400 | floor) * 86400 ) as $d0
    | range(1; $per + 1) as $i
    | { conclusion: (if $i % 11 == 0 then "failure" else "success" end),
        created_at: (($d0 + $i * 60) | todate) } ]
  | { workflow_runs: . }')"

reset_case() {
  : > "$STUB_TMP/calls.log"
  rm -f "$STUB_TMP/stderr.log" "$STUB_TMP/created.json" "$STUB_TMP/patched.log"
  unset STUB_RUNS_JSON STUB_RUNS_FAIL STUB_RUNS_CAP STUB_RUNS_POPULATION STUB_RUNS_EMPTY \
        STUB_RUNS_RAW STUB_RUNS_TOTAL_FORCE \
        STUB_LEDGER_SEARCH_JSON STUB_LEDGER_SEARCH_FAIL \
        STUB_RECORD_SEARCH_JSON STUB_RECORD_SEARCH_FAIL STUB_CREATE_FAIL STUB_NEW_ISSUE \
        STUB_PATCH_FAIL STUB_GET_BODY_FAIL STUB_ISSUE_JSON \
        RECORD_FETCH_INTEGRITY RECORD_CRON_PER_DAY 2>/dev/null || true
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

row_cols() { # <record output> <UTC day> -> that row's 4 trailing incident-column values
  # The availability column contains spaces, so the LAST four fields are the
  # incident columns (down_inc degraded_inc unknown_inc restarts). Asserting on
  # data cells rather than on the header labels is the difference between a
  # check that can fail and one answered by the always-printed header.
  printf '%s\n' "$1" | awk -v d="$2" '$1 == d { print $(NF-3), $(NF-2), $(NF-1), $NF }'
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
assert_contains "$OUT" "25.0% (delivered=4/288 cadence=1.4% decided=4/4)" "availability% is IMMEDIATELY beside its resolution (incl. the verdict denominator)"
assert_contains "$OUT" "delivered=2/288 cadence=0.7%" "the 2026-09-15 row carries its resolution"
assert_contains "$OUT" "delivered=6/720 cadence=0.8%" "the TOTAL carries its resolution (today counted on ELAPSED intent: 13x288+144)"
assert_contains "$OUT" "cron intent: 288 samples/day" "the cron intent (the denominator) is stated"

# ── 2: DEGRADED is never folded into DOWN ───────────────────────────────────
echo
echo "2. a DEGRADED incident is never folded into the DOWN column"
assert_contains "$OUT" "incident totals: down=1 degraded=1 unknown=0 restart_attempts=2" "the totals line counts each kind SEPARATELY (exact figure, not a combined one)"
assert_contains "$OUT" "kind=degraded" "the DEGRADED incident is labelled degraded"
assert_contains "$OUT" "kind=down" "the DOWN incident is labelled down"
assert_eq "$(row_cols "$OUT" 2026-09-16)" "1 1 0 2" \
  "the 09-16 row carries down_inc=1 and degraded_inc=1 in their OWN columns (never folded to 2/0)"
assert_eq "$(row_cols "$OUT" 2026-09-15)" "0 0 0 0" "a day with no incident shows 0 in every incident column"
assert_contains "$OUT" "totals are WINDOW-SCOPED" "the totals are declared window-scoped while the list below is ledger-wide"

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
assert_contains "$OUT" "0.0% (delivered=2/288 cadence=0.7% decided=2/2)" "the 0% figure is still printed with its resolution"

# ── 5: a zero-delivery day is a GAP, never a 100% day ───────────────────────
echo
echo "5. a zero-delivery day is a gap row, not an 'up' day"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON"
run_record 3 --print-only
assert_contains "$OUT" "delivered=0/144 cadence=0.0%)" "a no-sample day reports n/a (0 delivered), NOT 100%"
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

# ── 11: a >1,000-run window is fetched COMPLETELY (#3896) ───────────────────
# THE REGRESSION TEST FOR #3896. GitHub returns at most 1,000 results for a
# `created`-filtered query, so the OLD single-call fetch dropped the tail of
# every window of this size and the dropped days rendered as zero-delivery —
# "the PROBE was not running". Reintroduce the single call and this goes RED
# twice over: the fetch-integrity check fails the run, and (with the check also
# removed) the completeness assertions below fail.
echo
echo "11. a >1,000-run window is fetched COMPLETELY — no cap-clipped days"
reset_case
assert_eq "$(printf '%s' "$BIG_POP" | jq '.workflow_runs|length')" "$BIG_TOTAL" \
  "the fixture really holds >1,000 runs ($BIG_TOTAL; a <=1,000 fixture proves nothing)"
assert_eq "$(printf '%s' "$BIG_POP" | jq '[.workflow_runs[] | select(.created_at <= "2026-09-05T23:59:59Z")] | length')" \
  "$((2 * BIG_PER_DAY))" "the fixture really has runs OLDER than the newest 1,000 (so a single query DOES clip)"
export STUB_RUNS_POPULATION="$BIG_POP" STUB_LEDGER_SEARCH_JSON='{"items":[]}'
run_record "$BIG_DAYS" --print-only
assert_eq "$RC" "0" "a >1,000-run window still exits 0 (record-only, not a gate)"
assert_eq "$(count_calls 'GH-RUNS')" "$BIG_DAYS" "the fetch is ONE BOUNDED query per UTC day — never one wide query"
assert_contains "$OUT" "run fetch integrity: COMPLETE" "the fetch asserts itself COMPLETE against the API's own total_count"
assert_contains "$OUT" "zero-delivery days: 0 of $BIG_DAYS" "NO day of a >1,000-run window is rendered as zero-delivery"
assert_not_contains "$OUT" "n/a (NO samples delivered" "no day reads as a gap (a day slice is never at the cap)"
assert_contains "$OUT" "delivered=$BIG_TOTAL/$BIG_TOTAL_INTENDED" \
  "ALL $BIG_TOTAL runs are counted — the window is not clipped at 1,000"
assert_contains "$OUT" "delivered=$BIG_PER_DAY/288 cadence=38.2%" "each COMPLETE day is bucketed from its own runs"
assert_contains "$(cat "$STUB_TMP/calls.log")" "created=2026-09-04T00%3A00%3A00Z..2026-09-04T23%3A59%3A59Z" \
  "each slice is bounded on BOTH ends (no overlap, no gap)"

# ── 12: a TRUNCATED fetch is an instrument failure, never a no-sample day ───
echo
echo "12. a TRUNCATED fetch is an INSTRUMENT FAILURE, never a no-sample day"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON" STUB_RUNS_CAP=1
run_record 3
assert_eq "$RC" "1" "a fetch that returns fewer runs than the API reports exits non-zero"
assert_contains "$ERR" "INSTRUMENT FAILURE" "the truncation is reported as an INSTRUMENT FAILURE"
assert_contains "$ERR" "TRUNCATED" "the failure names the truncation"
assert_contains "$ERR" "NOT a gap" "the message says explicitly that this is not a probe gap"
assert_not_contains "$OUT" "n/a (NO samples delivered" "a truncation is NEVER rendered as a no-sample day row"
assert_not_contains "$OUT" "RECORD-ONLY baseline" "no record is rendered at all from an untrusted fetch"
assert_eq "$(count_calls 'GH POST repos/daniel-ospina/tortoise/issues$')" "0" "a truncated fetch publishes nothing"
assert_eq "$(count_calls 'GH PATCH')" "0" "a truncated fetch patches nothing"

# ── 13: render refuses to print a record from an unverified fetch ───────────
echo
echo "13. render REFUSES to print a record from an UNVERIFIED fetch"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON" \
       RECORD_FETCH_INTEGRITY='TRUNCATED: slice 2026-09-15 returned 1 of 2 reported'
run_record 3
assert_eq "$RC" "1" "an unverified fetch renders nothing and exits non-zero"
assert_contains "$OUT" "INSTRUMENT FAILURE" "the refusal names itself an instrument failure"
assert_not_contains "$OUT" "n/a (NO samples delivered" "no availability row is printed from an unverified fetch"
assert_not_contains "$OUT" "GAPS" "no gap section exists to be misread"
assert_eq "$(count_calls 'GH POST repos/daniel-ospina/tortoise/issues$')" "0" "an unverified fetch publishes nothing"

# ── 12b: an EMPTY run response is an unparseable ANSWER, not an empty window ─
echo
echo "12b. an EMPTY/unparseable run response is an instrument failure, not an empty window"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON" STUB_RUNS_EMPTY=1
run_record 3
assert_eq "$RC" "1" "an empty (200, no body) run response exits non-zero"
assert_contains "$ERR" "no page object carrying total_count" "the empty answer is named as an unparseable ANSWER"
assert_not_contains "$OUT" "n/a (NO samples delivered" "an empty answer is NEVER rendered as the probe not running"
assert_not_contains "$OUT" "RECORD-ONLY baseline" "an empty answer renders no record"
assert_eq "$(count_calls 'GH POST repos/daniel-ospina/tortoise/issues$')" "0" "an empty answer publishes nothing"

# ── 12c: a slice AT the cap cannot be told apart from a clipped one ─────────
echo
echo "12c. a slice AT GitHub's cap is refused (at the cap == cannot be told from clipped)"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON" STUB_RUNS_TOTAL_FORCE=1000
run_record 3
assert_eq "$RC" "1" "a slice reporting EXACTLY the cap exits non-zero"
assert_contains "$ERR" "at GitHub's 1000-result cap" "the refusal names the cap boundary"
assert_contains "$ERR" "cannot be told apart from a clipped one" "the refusal explains why the boundary is refused"
assert_not_contains "$ERR" "no slice reached the 1000-result cap" "the log never claims nothing reached a cap it refuses on"
assert_not_contains "$OUT" "n/a (NO samples delivered" "a cap-boundary slice is never rendered as absence"

# The ONE state the two counts cannot discriminate: returned == reported == the
# cap. Only a real 1,000-run population pins it, and it is the state in which a
# cap-clipped fetch would look "internally consistent".
CAP_POP="$(jq -nc --argjson now "$NOW" '
  [ range(1; 1001) as $i
    | { conclusion: "success",
        created_at: (((($now / 86400) | floor) * 86400 + $i) | todate) } ]
  | { workflow_runs: . }')"
reset_case
export STUB_RUNS_POPULATION="$CAP_POP" STUB_LEDGER_SEARCH_JSON='{"items":[]}'
run_record 1 --print-only
assert_eq "$(printf '%s' "$CAP_POP" | jq '.workflow_runs|length')" "1000" "the fixture is exactly 1,000 runs (returned == reported == the cap)"
assert_eq "$RC" "1" "a 1,000-run day is refused: at the cap, complete and clipped are indistinguishable"
assert_contains "$ERR" "reports total_count=1000" "the refusal names the 1,000-run day"
assert_contains "$ERR" "cannot be told apart from a clipped one" "the refusal explains the indistinguishability"
assert_not_contains "$OUT" "RECORD-ONLY baseline" "a cap-sized day renders no record at all"

# A malformed trust anchor is refused, never coerced to "zero runs everywhere".
echo
echo "12d. a malformed fetch-integrity marker is refused, never coerced to zero"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON" \
       RECORD_FETCH_INTEGRITY=$'COMPLETE\txx\tyy\t3\t1000'
run_record 3
assert_eq "$RC" "1" "a marker with non-numeric counts exits non-zero"
assert_contains "$OUT" "missing or malformed" "the refusal names the malformed anchor"
assert_not_contains "$OUT" "RECORD-ONLY baseline" "a malformed anchor renders no record"
assert_not_contains "$OUT" "n/a (NO samples delivered" "a malformed anchor is never read as an empty window"

# An EMPTY INTERIOR field in the marker: bash `read` collapses tab runs, so only
# a field COUNT can catch this — the values would otherwise shift into the wrong
# slots and look well-formed.
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON" \
       RECORD_FETCH_INTEGRITY=$'COMPLETE\t6\t6\t\t3\t1000'
run_record 3
assert_eq "$RC" "1" "a marker with an empty INTERIOR field exits non-zero (6 fields, not 5)"
assert_contains "$OUT" "missing or malformed" "the shifted marker is refused"
assert_not_contains "$OUT" "RECORD-ONLY baseline" "a shifted marker renders no record"

# ── 13b: the render gate re-derives the marker's own consistency ────────────
echo
echo "13b. the render gate re-derives the marker's consistency and its window coverage"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON" \
       RECORD_FETCH_INTEGRITY=$'COMPLETE\t1000\t1540\t14\t1000'
run_record 3
assert_eq "$RC" "1" "a marker whose two counts DISAGREE is refused (the clipped-tail signature)"
assert_contains "$OUT" "internally inconsistent" "the refusal names the inconsistency"
assert_not_contains "$OUT" "n/a (NO samples delivered" "a self-inconsistent fetch is still never rendered as absence"

reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON" \
       RECORD_FETCH_INTEGRITY=$'COMPLETE\t6\t6\t1\t1000'
run_record 3
assert_eq "$RC" "1" "a marker covering FEWER slices than the window is refused"
assert_contains "$OUT" "covers 1 slice(s) but the window has 3 UTC day(s)" "the refusal names the uncovered window"

# ── 14: a failed run list / ledger search is an instrument failure ──────────
echo
echo "14. a failed run fetch or ledger search exits non-zero and publishes nothing"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON" STUB_RUNS_FAIL=1
run_record 3
assert_eq "$RC" "1" "a failed run list exits non-zero"
assert_contains "$ERR" "could not list runs" "the run-list failure is named"
assert_not_contains "$OUT" "RECORD-ONLY baseline" "a failed run list renders no record"
assert_eq "$(count_calls 'GH POST repos/daniel-ospina/tortoise/issues$')" "0" "a failed run list publishes nothing"
assert_eq "$(count_calls 'GH PATCH')" "0" "a failed run list patches nothing"

reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_FAIL=1
run_record 3
assert_eq "$RC" "1" "a failed ledger search exits non-zero"
assert_contains "$ERR" "could not search incident ledger" "the ledger-search failure is named"
assert_not_contains "$OUT" "RECORD-ONLY baseline" "a failed ledger search renders no record (an empty incident list would assert there were none)"
assert_eq "$(count_calls 'GH POST repos/daniel-ospina/tortoise/issues$')" "0" "a failed ledger search publishes nothing"

# ── 15: an inconclusive run is NOT availability (#3896 P2-1) ────────────────
echo
echo "15. an inconclusive run is NOT availability (out of numerator AND denominator)"
reset_case
INCONC_JSON='{"workflow_runs":[
 {"conclusion":"success","created_at":"2026-09-15T01:00:00Z"},
 {"conclusion":"cancelled","created_at":"2026-09-15T02:00:00Z"},
 {"conclusion":"failure","created_at":"2026-09-15T03:00:00Z"},
 {"conclusion":"failure","created_at":"2026-09-15T04:00:00Z"},
 {"conclusion":"success","created_at":"2026-09-16T01:00:00Z"},
 {"conclusion":"cancelled","created_at":"2026-09-16T02:00:00Z"},
 {"conclusion":"cancelled","created_at":"2026-09-17T01:00:00Z"},
 {"conclusion":"cancelled","created_at":"2026-09-17T02:00:00Z"}]}'
export STUB_RUNS_JSON="$INCONC_JSON" STUB_LEDGER_SEARCH_JSON='{"items":[]}'
run_record 3 --print-only
assert_eq "$RC" "0" "inconclusive runs do not fail the instrument"
# 09-15: 1 success + 2 failure + 1 cancelled -> 1/3 (33.3%), NOT 2/4 (50.0%).
assert_contains "$OUT" "33.3% (delivered=4/288 cadence=1.4% decided=3/4)" \
  "a cancelled run is EXCLUDED from the denominator (33.3%, not 50.0%)"
assert_not_contains "$OUT" "50.0% (delivered=4/288" "a cancelled run is never counted as an available sample"
# 09-16: 1 success + 1 cancelled -> 1/1 with the denominator disclosed.
assert_contains "$OUT" "100.0% (delivered=2/288 cadence=0.7% decided=1/2)" \
  "a 1-success+1-cancelled day discloses its 1-of-2 verdict denominator"
# 09-17: every delivered run inconclusive -> NO VERDICT, which is not a gap.
assert_contains "$OUT" "n/a (NO VERDICT" "an all-inconclusive day is n/a for a DIFFERENT reason than a gap"
assert_contains "$OUT" "delivered=2/144" "the all-inconclusive day still reports its delivered samples"
assert_contains "$OUT" "zero-delivery days: 0 of 3" "an all-inconclusive day is NOT a zero-delivery gap"
assert_contains "$OUT" "inconclusive runs (NO verdict — NOT availability, excluded from BOTH numerator and denominator): 4 (cancelled=4)" \
  "the inconclusive count and its conclusions are surfaced, never averaged away"

# ── 16: a non-numeric cron intent is normalized (#3896 P2-2) ───────────────
echo
echo "16. a non-numeric RECORD_CRON_PER_DAY is normalized, not a set -u abort"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON" RECORD_CRON_PER_DAY=abc
run_record 3 --print-only
assert_eq "$RC" "0" "a non-numeric cron intent does not abort the run"
assert_not_contains "$ERR" "unbound variable" 'the bare set -u abort is gone'
assert_contains "$OUT" "cron intent: 288 samples/day" "it falls back to the documented default"
assert_contains "$OUT" "delivered=2/288 cadence=0.7%" "the fallback is a real denominator, not a mangled one"

# ── 17: a state block missing a matchable field is never dropped (P2-3) ─────
echo
echo "17. a state block missing a matchable field is warned about, never dropped"
reset_case
PARTIAL_LEDGER='{"items":[
 {"number":4001,"user":{"login":"github-actions[bot]"},"title":"a","body":"<!-- watchdog-state kind=down first_failure_ts=1789547000 -->\n<!-- availability-watchdog-state -->"},
 {"number":4002,"user":{"login":"github-actions[bot]"},"title":"b","body":"<!-- watchdog-state first_failure_ts=1789547000 restarts=1789500000 -->\n<!-- availability-watchdog-state -->"},
 {"number":4003,"user":{"login":"github-actions[bot]"},"title":"c","body":"<!-- watchdog-state kind=degraded restarts= -->\n<!-- availability-watchdog-state -->"}]}'
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$PARTIAL_LEDGER"
run_record 3 --print-only
assert_eq "$RC" "0" "a malformed state block does not stop the record"
assert_contains "$OUT" "#4001" "an incident whose block has NO restarts= is still listed (it used to vanish)"
assert_contains "$OUT" "#4002" "an incident whose block has NO kind= is still listed (it used to vanish)"
assert_contains "$OUT" "#4003" "an incident whose block has NO first_failure_ts= is still listed (it used to vanish)"
assert_contains "$OUT" "incident totals: down=1 degraded=0 unknown=1 restart_attempts=1" \
  "the DATED incidents missing a field are still counted (both used to vanish)"
assert_contains "$OUT" "1 incident(s) carry no parseable first_failure_ts: listed below, but attributable to no UTC day and therefore NOT counted in the window totals above" \
  "the undated incident is listed and disclosed as not-window-counted — never silently dropped"
assert_contains "$ERR" "has no parseable kind=" "the unparsable kind is warned about"
assert_contains "$ERR" "has no parseable first_failure_ts — listed, but attributable to no UTC day and therefore NOT counted in the window totals" \
  "the undated warning AGREES with the disclosure (it is not counted in the totals)"

# ── 18: the in-progress UTC day is scored on elapsed intent (P2-6) ──────────
echo
echo "18. the in-progress UTC day is scored on ELAPSED intent and marked"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON"
run_record 3 --print-only
# NOW is 2026-09-17T12:00:00Z -> 144 five-minute intervals elapsed since 00:00.
assert_contains "$OUT" "2026-09-17*" "the in-progress day is MARKED, never presented as a complete day"
assert_contains "$OUT" "is IN PROGRESS: intended=144" "the mark is explained"
assert_contains "$OUT" "delivered=6/720" "the window denominator uses elapsed intent for today, not a fictional full day"
assert_not_contains "$OUT" "delivered=6/864" "the old full-288-for-today denominator is gone"

# ── 19: a custom cron intent stays internally consistent ───────────────────
echo
echo "19. a custom cron intent keeps today's elapsed intent consistent with it"
reset_case
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$LEDGER_JSON" RECORD_CRON_PER_DAY=96
run_record 3 --print-only
# 96/day = one sample per 900s; 12h elapsed = 48 intervals, NOT the hardcoded 144.
assert_contains "$OUT" "cron intent: 96 samples/day" "the header states the configured intent"
assert_contains "$OUT" "is IN PROGRESS: intended=48" "today's elapsed intent follows the CONFIGURED cadence (48, not 144)"
assert_contains "$OUT" "delivered=6/240" "the window total is day-consistent with the stated intent (2x96 + 48 = 240)"
assert_not_contains "$OUT" "delivered=6/336" "the record never states an intent its own denominator contradicts"
assert_not_contains "$OUT" "5-minute cadence" "no section hardcodes a 5-minute cadence the configured intent contradicts"
assert_contains "$OUT" "of the cron's nominal intent" "the GAPS section derives its wording from the configured intent"

# ── 20: an unrenderable incident epoch keeps its restart ledger ────────────
echo
echo "20. an incident whose epoch cannot be rendered lands as UNDATED, keeping its restarts"
reset_case
BIG_TS_LEDGER='{"items":[
 {"number":5001,"user":{"login":"github-actions[bot]"},"title":"x","body":"<!-- watchdog-state kind=degraded first_failure_ts=99999999999999999 restarts=1789500000 -->\n<!-- availability-watchdog-state -->"}]}'
export STUB_RUNS_JSON="$RUNS_JSON" STUB_LEDGER_SEARCH_JSON="$BIG_TS_LEDGER"
run_record 3 --print-only
assert_eq "$RC" "0" "an unrenderable epoch does not stop the record"
assert_contains "$OUT" "first_observed=n/a" "the incident is reported UNDATED"
assert_not_contains "$OUT" "first_observed=1789500000" "a restart stamp is never printed as the observation day"
assert_contains "$OUT" "restarts=1789500000" "the restart ledger survives the shift (it used to be erased)"
assert_contains "$OUT" "restart_attempts=1" "the restart attempt count survives"
assert_contains "$ERR" "has no parseable first_failure_ts" "the undated incident is warned about"

# ── 21: a first-interval dispatch cannot produce /0 or n/a% ────────────────
echo
echo "21. a dispatch in the FIRST cron interval of a UTC day cannot produce /0 or n/a%"
reset_case
export STUB_RUNS_JSON='{"workflow_runs":[{"conclusion":"success","created_at":"2026-09-17T00:01:00Z"}]}' \
       STUB_LEDGER_SEARCH_JSON='{"items":[]}'
export RECORD_NOW_EPOCH=1789603320   # 2026-09-17T00:02:00Z — two minutes after UTC midnight
run_record 1 --print-only
assert_eq "$RC" "0" "a just-after-midnight dispatch still records"
assert_contains "$OUT" "is IN PROGRESS: intended=1" "the elapsed intent floors at 1 (we are inside the first interval)"
assert_not_contains "$OUT" "delivered=1/0" "no ratio is asserted against a zero denominator"
assert_not_contains "$OUT" "n/a%" "the cadence column is never a malformed value"

# The reachable zero-delivery WINDOW (probe fully down / workflow disabled) — no
# seam needed. This is the branch that used to print the malformed `cadence=n/a%`
# while the row's own cadence column said 0.0%.
reset_case
export STUB_RUNS_JSON='{"workflow_runs":[]}' STUB_LEDGER_SEARCH_JSON='{"items":[]}'
run_record 3 --print-only
assert_eq "$RC" "0" "an all-zero window still exits 0 (record-only, not a gate)"
assert_contains "$OUT" "n/a (delivered=0/720 cadence=0.0%)" "the TOTAL carries a WELL-FORMED cadence, not n/a%"
assert_not_contains "$OUT" "n/a%" "no malformed cadence token anywhere in a zero-delivery window"
assert_contains "$OUT" "zero-delivery days: 3 of 3" "every day is a gap row, never an 'up' day"

# ── 22: a run with no created_at fails the fetch CLOSED (truthfulness) ───────
# A returned run whose `created_at` is missing cannot be placed on a UTC day. The
# old code DROPPED it with a stderr-only warning while the marker still certified
# the pre-drop count, so the published body could assert "COMPLETE — 2 run(s)
# enumerated" while rendering delivered=1 and 100.0% — OVERSTATING availability
# when the dropped run was a failure (truth over the returned runs was 50%). It
# now fails the fetch closed, like every other run the instrument cannot fully
# account for.
echo
echo "22. a run with no created_at fails the fetch CLOSED, never a quiet drop"
reset_case
export STUB_RUNS_RAW='{"total_count":2,"workflow_runs":[{"conclusion":"failure"},{"conclusion":"success","created_at":"2026-09-17T02:00:00Z"}]}' \
       STUB_LEDGER_SEARCH_JSON='{"items":[]}'
run_record 1 --print-only
assert_eq "$RC" "1" "a returned run with no created_at exits non-zero (nothing published)"
assert_contains "$ERR" "INSTRUMENT FAILURE" "the drop is reported as an INSTRUMENT FAILURE"
assert_contains "$ERR" "no created_at" "the failure names the missing created_at"
assert_contains "$ERR" "OVERSTATE availability" "the failure explains the overstatement it prevents"
assert_not_contains "$OUT" "run fetch integrity: COMPLETE" "the record never certifies a fetch that dropped a run"
assert_not_contains "$OUT" "RECORD-ONLY baseline" "no record is rendered from a fetch that dropped a run"
assert_not_contains "$OUT" "100.0%" "the overstated 100.0% figure is never printed"
assert_eq "$(count_calls 'GH POST repos/daniel-ospina/tortoise/issues$')" "0" "a dropped run publishes nothing"
assert_eq "$(count_calls 'GH PATCH')" "0" "a dropped run patches nothing"

# ── 23: a run dated OUTSIDE the window fails the render CLOSED (#3810 P2-1) ──
# fetch_runs' checks are about the ENUMERATION, not about the window this render
# sums: a run whose created_at is a VALID date outside the window passes every
# slice check (returned == reported, under the cap, placeable) and is then
# counted by the marker while the per-day table — which iterates window_days.txt
# — gives it no row. The body would print zero delivery for every day while the
# marker certified "COMPLETE — N enumerated": a false-absence claim over a
# dropped (here, FAILURE) run, the same class as test 22 one stage later. The
# render now reconciles the marker against the runs that land on a rendered day.
echo
echo "23. a run dated OUTSIDE the window is refused, never rendered as absence"
reset_case
# STUB_RUNS_RAW bypasses the stub's own `created` filter — the live API is
# range-tight, so this forges a return that violates its OWN slice. That is the
# defence-in-depth case: every slice check passes, and only the render-time
# reconciliation can see that the enumerated run never reaches a row.
export STUB_RUNS_RAW='{"total_count":1,"workflow_runs":[{"conclusion":"failure","created_at":"2026-09-01T02:00:00Z"}]}' \
       STUB_LEDGER_SEARCH_JSON='{"items":[]}'
run_record 3 --print-only
assert_eq "$RC" "1" "a run enumerated but outside the window exits non-zero (nothing published)"
assert_contains "$OUT" "INSTRUMENT FAILURE, NOT A READING" "the mismatch is reported as an INSTRUMENT FAILURE"
assert_contains "$OUT" "only 0 fall on a UTC day this window renders" "the refusal names the enumerated-vs-rendered mismatch"
assert_not_contains "$OUT" "run fetch integrity: COMPLETE" "the record never certifies a fetch whose runs have no row"
assert_not_contains "$OUT" "zero-delivery days: 3 of 3" "the false-absence body is never printed"
assert_not_contains "$OUT" "RECORD-ONLY baseline" "no record is rendered from a fetch that drops a run"
assert_eq "$(count_calls 'GH POST repos/daniel-ospina/tortoise/issues$')" "0" "an out-of-window run publishes nothing"
assert_eq "$(count_calls 'GH PATCH')" "0" "an out-of-window run patches nothing"

# The same class, one stage earlier: a created_at that is PRESENT but is not a
# usable UTC date is an UNPLACEABLE run — a fetch failure — not a quiet drop.
echo
echo "23b. a whitespace or garbage created_at is an unplaceable run, not a drop"
reset_case
export STUB_RUNS_RAW='{"total_count":2,"workflow_runs":[{"conclusion":"failure","created_at":" "},{"conclusion":"success","created_at":"2026-09-17T02:00:00Z"}]}' \
       STUB_LEDGER_SEARCH_JSON='{"items":[]}'
run_record 1 --print-only
assert_eq "$RC" "1" "a whitespace created_at exits non-zero (nothing published)"
assert_contains "$ERR" "no created_at that is a usable UTC date" "the refusal names the unusable created_at"
assert_not_contains "$OUT" "RECORD-ONLY baseline" "a whitespace created_at renders no record"

reset_case
export STUB_RUNS_RAW='{"total_count":2,"workflow_runs":[{"conclusion":"failure","created_at":"not-a-date"},{"conclusion":"success","created_at":"2026-09-17T02:00:00Z"}]}' \
       STUB_LEDGER_SEARCH_JSON='{"items":[]}'
run_record 1 --print-only
assert_eq "$RC" "1" "a garbage created_at exits non-zero (nothing published)"
assert_contains "$ERR" "no created_at that is a usable UTC date" "the refusal names the unusable created_at"
assert_not_contains "$OUT" "RECORD-ONLY baseline" "a garbage created_at renders no record"
echo
if [ "$FAIL" -eq 0 ]; then
  echo "availability-record.test.sh: $PASS passed, 0 failed ✅"
  exit 0
fi
echo "availability-record.test.sh: $PASS passed, $FAIL FAILED ❌"
exit 1
