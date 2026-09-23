#!/usr/bin/env bash
# orphan-bound.test.sh — self-check for .github/scripts/orphan-bound.sh (#4740).
#
# Run: bash .github/scripts/orphan-bound.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: writes
# hygiene-report fixtures to a temp dir. No CI, no runner, no network.
#
# Coverage — one case per VERDICT-TABLE row, plus the positive controls that
# make the row's rule load-bearing:
#   * `no_embedded_servers` → bound 0 (case 9), and a count above 0 REDs
#     (case 10) while a watchdog kill downgrades it (case 11).
#   * `{reaped, cleared, left}` → bound is READ from the report: two reports
#     with DIFFERENT `left` both PASS against their own value (cases 1, 2), so a
#     hardcoded bound cannot satisfy both.
#   * `cleared: false` → RED whatever the count is (case 3): the true
#     "hygiene is broken" signal a bigger constant would swallow.
#   * the two positive controls on a report: COUNT must equal `left` (case 4),
#     and the mismatch may not fall through to a pass line (case 4) — a kill
#     downgrades only a count ABOVE the bound (case 5), never one BELOW it
#     (case 6).
#   * `error` and `skipped` → RED ALWAYS (cases 7, 8), including under a kill.
#   * missing / unreadable / structurally-incomplete reports → RED (cases 12,
#     14, 16), downgraded only by a watchdog kill (cases 13, 15).
#   * the #1371 rc-unknown red path (case 17) and the fail-loud argument
#     validation (cases 18-20).
#
# MUTATION PINS (verified by mutating the script, not the fixture): cases 4, 7,
# 8, 10, 14, 16 each fail if their branch's `exit 1` becomes a `return`/fall
# through, and case 1 fails if the bound stops being read from `left`. A case
# that merely restates a default would not catch its own removal.
#
# The assertion count is PINNED (see the summary): a lost case must not be
# indistinguishable from a passing one.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="$SCRIPT_DIR/orphan-bound.sh"

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); echo "  ✅ $1"; }
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
    *"$2"*) bad "$3 (unexpectedly contains '$2')" ;;
    *) ok "$3" ;;
  esac
}

WORK="$(mktemp -d)"
trap 'find "$WORK" -depth -mindepth 1 -delete 2>/dev/null; rmdir "$WORK" 2>/dev/null' EXIT

# ── report fixtures ─────────────────────────────────────────────────────────
printf '{"sweep":{"reaped":9,"cleared":true,"left":14}}' > "$WORK/report14.json"
printf '{"sweep":{"reaped":2,"cleared":true,"left":7}}' > "$WORK/report7.json"
printf '{"sweep":{"reaped":9,"cleared":false,"left":14}}' > "$WORK/budget.json"
printf '{"sweep":{"error":"probe exploded"}}' > "$WORK/error.json"
printf '{"sweep":{"skipped":"reaper-lock-held"}}' > "$WORK/skipped.json"
printf '{"sweep":{"no_embedded_servers":true}}' > "$WORK/none.json"
printf '{"sweep":{"reaped":9,"cleared":true}}' > "$WORK/noleft.json"
printf 'not json at all' > "$WORK/unreadable.json"
MISSING="$WORK/does-not-exist.json"

# ── driver ──────────────────────────────────────────────────────────────────
# run_gate <count> <rc> <fixture-basename> → sets OUT (stdout+stderr) and RC
run_gate() {
  local st=0
  OUT=$(bash "$GATE" --count "$1" --rc "$2" --hygiene "$WORK/$3" 2>&1) || st=$?
  RC=$st
}

echo "orphan-bound.test.sh (#4740)"
echo

echo "1. a report with cleared=true and COUNT==left PASSES at the sweep's own left"
run_gate 14 0 report14.json
assert_eq "$RC" "0" "exits 0"
assert_contains "$OUT" "left=14" "reports the bound READ from the report"
assert_contains "$OUT" "cleared=true" "records that the sweep finished"
assert_contains "$OUT" "within the sweep's own measurement" "states the pass basis"

echo "2. a DIFFERENT left also PASSES against its own value (the bound is read, not constant)"
# If the script carried any fixed bound, at most one of cases 1/2 could pass.
run_gate 7 0 report7.json
assert_eq "$RC" "0" "exits 0"
assert_contains "$OUT" "left=7" "reports the second report's own value"
assert_not_contains "$OUT" "left=14" "uses the report's value, not case 1's"

echo "3. cleared=false REDs whatever the count is"
run_gate 14 0 budget.json
assert_eq "$RC" "1" "exits 1 even though COUNT==left"
assert_contains "$OUT" "cleared=false" "names the exhausted budget"
assert_contains "$OUT" "arbitrary" "states the residue is unbounded"
assert_not_contains "$OUT" "within the sweep's own measurement" "never prints a pass line"

echo "4. a report whose COUNT disagrees with left REDs (the positive control)"
run_gate 15 0 report14.json
assert_eq "$RC" "1" "exits 1 when COUNT > left on a normal exit"
assert_contains "$OUT" "same population" "names the counter/population disagreement"
assert_not_contains "$OUT" "within the sweep's own measurement" \
  "does NOT fall through to the pass line (fail-open pin)"

echo "5. a kill downgrades only a count ABOVE the bound"
run_gate 15 124 report14.json
assert_eq "$RC" "0" "exits 0 under rc=124"
assert_contains "$OUT" "::warning::" "emits a warning"
assert_contains "$OUT" "#1371" "cites the kill-aware rationale"
assert_not_contains "$OUT" "within the sweep's own measurement" \
  "the warning path does not print a pass line either"

echo "6. a kill does NOT downgrade a count BELOW the bound"
# A kill can only leave MORE servers; a count below the sweep's own measurement
# means the counter is measuring a different population, kill or not.
run_gate 13 124 report14.json
assert_eq "$RC" "1" "exits 1 on COUNT < left even under a kill"
assert_contains "$OUT" "different population" "names the disagreement"

echo "7. an error report REDs ALWAYS — including under a kill"
run_gate 3 0 error.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "reported an error" "names the crashed hygiene path"
run_gate 3 124 error.json
assert_eq "$RC" "1" "stays RED under a watchdog kill (the residue is unaccounted)"
assert_not_contains "$OUT" "::warning::" "no downgrade for a crashed hygiene path"

echo "8. a skipped report REDs ALWAYS — including under a kill"
run_gate 3 0 skipped.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "reaper lock held" "names the skipped sweep"
run_gate 3 137 skipped.json
assert_eq "$RC" "1" "stays RED under a watchdog kill"
assert_not_contains "$OUT" "::warning::" "no downgrade for a skipped sweep"

echo "9. no_embedded_servers with COUNT=0 PASSES (bound 0)"
run_gate 0 0 none.json
assert_eq "$RC" "0" "exits 0"
assert_contains "$OUT" "no embedded redislite server was running" "states the basis"

echo "10. no_embedded_servers with a count above 0 REDs"
run_gate 3 0 none.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "no embedded server was ever running" "names the contradiction"
assert_not_contains "$OUT" "remain after suite" "does NOT fall through to the pass line"

echo "11. no_embedded_servers above 0 under a kill downgrades to a warning"
run_gate 3 137 none.json
assert_eq "$RC" "0" "exits 0 under rc=137"
assert_contains "$OUT" "::warning::" "emits a warning"

echo "12. a missing report REDs (residue unaccounted)"
run_gate 4 0 does-not-exist.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "unaccounted" "names the missing accounting"

echo "13. a missing report under a kill downgrades to a warning"
run_gate 4 2 does-not-exist.json
assert_eq "$RC" "0" "exits 0 under rc=2"
assert_contains "$OUT" "::warning::" "emits a warning"

echo "14. an unreadable report REDs"
run_gate 4 0 unreadable.json
assert_eq "$RC" "1" "exits 1 on invalid JSON"
assert_contains "$OUT" "unreadable" "names the unreadable report"

echo "15. an unreadable report under a kill downgrades to a warning"
run_gate 4 124 unreadable.json
assert_eq "$RC" "0" "exits 0 under rc=124"
assert_contains "$OUT" "::warning::" "emits a warning"

echo "16. a report missing the left field REDs (fail-closed on an incomplete report)"
run_gate 4 0 noleft.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "unreadable" "treats the incomplete report as unusable"

echo "17. an empty rc reds on a leak with the rc-unknown message"
run_gate 3 "" none.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "rc unknown" "names the unknown pytest rc"

echo "18. a non-numeric count exits 2 (fail loud, not a code path)"
bad_rc=0
OUT=$(bash "$GATE" --count x --rc 0 --hygiene "$WORK/none.json" 2>&1) || bad_rc=$?
assert_eq "$bad_rc" "2" "exits 2"
assert_contains "$OUT" "--count must be a non-negative integer" "names the bad count"

echo "19. an unknown argument exits 2"
bad_rc=0
OUT=$(bash "$GATE" --count 1 --nope 2>&1) || bad_rc=$?
assert_eq "$bad_rc" "2" "exits 2"
assert_contains "$OUT" "unknown argument" "names the bad flag"

echo "20. a flag with no value exits 2"
bad_rc=0
OUT=$(bash "$GATE" --count 1 --rc 2>&1) || bad_rc=$?
assert_eq "$bad_rc" "2" "exits 2"
assert_contains "$OUT" "requires a value" "names the missing value"

echo
# A LOST case must not be indistinguishable from success: deleting a case
# leaves FAIL=0 and merely a LOWER count, so the count is pinned too.
expected_assertions=53
if [ "$PASS" -eq "$expected_assertions" ]; then
  PASS=$((PASS + 1))
  echo "  ✅ assertion count pinned at $expected_assertions (a lost case is not a green run)"
else
  if [ "$FAIL" -gt 0 ]; then
    echo "  ❌ only $PASS of $expected_assertions assertions ran — a consequence of the failures above"
  elif [ "$PASS" -gt "$expected_assertions" ]; then
    echo "  ❌ $PASS assertions ran, expected $expected_assertions — a case was ADDED: bump expected_assertions"
  else
    echo "  ❌ expected $expected_assertions assertions, got $PASS — a case was LOST"
  fi
  FAIL=$((FAIL + 1))
fi

echo "──────────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
  echo "✅ ALL PASSED — $PASS assertions"
  exit 0
fi
echo "❌ $FAIL FAILED, $PASS passed"
exit 1
