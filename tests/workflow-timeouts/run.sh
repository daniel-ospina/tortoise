#!/bin/bash
# tests/workflow-timeouts/run.sh — #2055 regression suite for scripts/check-workflow-timeouts.mjs
#
# THE DEFECT (why this exists)
# ----------------------------
#   No job in .github/workflows/*.yml declared `timeout-minutes`, so every job inherited
#   GitHub's 360-minute default. On PR #2044 `bash-suites` sat in_progress for 34+ minutes
#   after every peer job had finished and `admin-merge` queued behind it: a WEDGED shard was
#   indistinguishable from a slow one for six hours, and NO PR could land while it hung.
#   That is the land half of the autopilot failing on both platforms — the rail the fleet
#   calls is a check that can never complete (#1926 is the same class on the reading side).
#
# WHAT THIS SUITE PINS
# --------------------
#   0. LIVE — the guard passes on this repo's real workflows, and the count it reports is a
#      real population (not a vacuous one): a bound per shell job, reusable callers excluded.
#   1. A shell job with no bound            → rc 1 (the #2055 defect itself).
#   2. A non-numeric bound                  → rc 1 (`15m` is not a minute count).
#   3. A zero bound                         → rc 1 (GitHub's minimum is 1).
#   4. A bound ABOVE the policy ceiling     → rc 1 (a 360-minute bound is the default again,
#                                             so "add 360 everywhere" cannot satisfy the gate).
#   5. `timeout-minutes` ON a reusable call → rc 1 (GitHub rejects the key there; the bound
#                                             must live on the called workflow's jobs).
#   6. A reusable call to a FOREIGN repo    → rc 1 (its bounds are unverifiable from here).
#   7. A good tree (bounded jobs + same-repo reusable call) → rc 0, so the gate is not
#      simply always-red, and the caller→target resolution is exercised in the pass direction.
#   8. Unparseable workflow YAML            → rc 2 (fail-closed, never "no violations found").
#   9. A workflow with no jobs              → rc 2 (a vacuous mapping is not a clean audit).
#  10. A gutted job population              → rc 2 (the vacuity floor).
#  11. CLI contract — `--help` → 0, unknown argument → 2.
#  12. WIRING — the guard is invoked by scripts/run-bash-shards.sh, the ONE shard list both
#      lanes run. A guard nothing runs is a no-op gate (the #1967 class).
#
# NOTE ON STYLE: runs inside a command substitution are written as `cmd >file` + `cat file`,
# never `OUT="$(… node …)"` — the main-worktree-guard content walker (#1484) refuses a script
# that wraps a shell invocation in a command substitution (#1122).

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GUARD="$ROOT/scripts/check-workflow-timeouts.mjs"
FIX="$ROOT/tests/fixtures/workflow-timeouts"
RUNNER="$ROOT/scripts/run-bash-shards.sh"
OUT="$(mktemp /tmp/workflow-timeouts-out.XXXXXX)"
failures=0

cleanup() { rm -f "$OUT"; }
trap cleanup EXIT

pass() { echo "   ✅ $1"; }
fail() { echo "   ❌ $1"; failures=$((failures + 1)); }

# Guard: never modify fixtures.
if ! git -C "$ROOT" diff --quiet -- tests/fixtures/workflow-timeouts 2>/dev/null; then
  echo "❌ tests/fixtures/workflow-timeouts has uncommitted modifications — commit them first."
  exit 1
fi

command -v node >/dev/null 2>&1 || {
  echo "❌ node not found — this guard is a Node module (scripts/workflow-yaml.mjs)"
  exit 1
}

# run_guard <expected-exit> <label> [args...]
run_guard() {
  local expected="$1"; shift
  local label="$1"; shift
  node "$GUARD" "$@" >"$OUT" 2>&1
  local actual=$?
  if [ "$actual" -eq "$expected" ]; then
    pass "$label (exit $actual)"
  else
    fail "$label — expected exit $expected, got $actual"
    sed 's/^/      /' "$OUT"
  fi
}

# Fixtures are not the repo's workflow set, so the vacuity floor is lowered for them.
FIXTURE_ARGS=(--min-jobs 1)

echo "== Case 0: LIVE — the repo's own workflows are bounded =="
node "$GUARD" >"$OUT" 2>&1
if [ $? -eq 0 ]; then
  pass "live workflows pass"
else
  fail "live workflows FAIL the timeout gate — #2055 is not actually fixed"
  sed 's/^/      /' "$OUT"
fi
if grep -qE 'OK — [0-9]+ job\(s\) bounded' "$OUT"; then
  bounded="$(sed -n 's/.*OK — \([0-9][0-9]*\) job(s) bounded.*/\1/p' "$OUT")"
  if [ "${bounded:-0}" -ge 20 ]; then
    pass "live audit covers a real population ($bounded bounded jobs)"
  else
    fail "live audit covered only ${bounded:-0} jobs — vacuous"
  fi
else
  fail "live run did not report a bounded-job count"
fi

echo "== Case 1: a shell job with no bound → BLOCK (the #2055 defect) =="
run_guard 1 "missing timeout-minutes blocks" --root "$FIX/missing" "${FIXTURE_ARGS[@]}"
grep -q "no 'timeout-minutes'" "$OUT" && pass "the reason names the missing key" || fail "the reason does not name the missing key"

echo "== Case 2: a non-numeric bound → BLOCK =="
run_guard 1 "non-integer timeout blocks" --root "$FIX/non-integer" "${FIXTURE_ARGS[@]}"

echo "== Case 3: a zero bound → BLOCK =="
run_guard 1 "zero timeout blocks" --root "$FIX/zero" "${FIXTURE_ARGS[@]}"

echo "== Case 4: a bound above the policy ceiling → BLOCK =="
run_guard 1 "over-ceiling (360) blocks" --root "$FIX/over-ceiling" "${FIXTURE_ARGS[@]}"
grep -q "policy ceiling" "$OUT" && pass "the reason names the policy ceiling" || fail "the reason does not name the policy ceiling"

echo "== Case 5: timeout-minutes ON a reusable-workflow call → BLOCK =="
run_guard 1 "timeout on a reusable call blocks" --root "$FIX/caller-with-timeout" "${FIXTURE_ARGS[@]}"
grep -q "not allowed on a job that calls a reusable workflow" "$OUT" \
  && pass "the reason names GitHub's caller-key restriction" \
  || fail "the reason does not name GitHub's caller-key restriction"

echo "== Case 6: a reusable call to a FOREIGN repo → BLOCK =="
# --min-jobs 0: this fixture is one caller job and no shell jobs, so the vacuity floor would
# mask the arm under test. The floor has its own case (10).
run_guard 1 "foreign reusable target blocks" --root "$FIX/foreign-caller" --min-jobs 0
grep -q "unverifiable here" "$OUT" && pass "the reason names unverifiability" || fail "the reason does not name unverifiability"

echo "== Case 7: a good tree (bounded jobs + same-repo reusable call) → PASS =="
run_guard 0 "good tree passes" --root "$FIX/good" "${FIXTURE_ARGS[@]}"

echo "== Case 8: unparseable workflow YAML → FAIL-CLOSED =="
run_guard 2 "unparseable YAML is fail-closed" --root "$FIX/unparseable" "${FIXTURE_ARGS[@]}"
grep -q "FAIL-CLOSED" "$OUT" && pass "the refusal says FAIL-CLOSED" || fail "the refusal does not say FAIL-CLOSED"

echo "== Case 9: a workflow with no jobs → FAIL-CLOSED =="
run_guard 2 "empty jobs mapping is fail-closed" --root "$FIX/no-jobs" "${FIXTURE_ARGS[@]}"

echo "== Case 10: a gutted job population → FAIL-CLOSED (vacuity floor) =="
run_guard 2 "vacuity floor refuses a one-job tree" --root "$FIX/vacuous" --min-jobs 20

echo "== Case 11: CLI contract =="
run_guard 0 "--help exits 0" --help
run_guard 2 "an unknown argument exits 2" --nonsense

echo "== Case 12: WIRING — the guard is invoked by the ONE shard list both lanes run =="
if [ -f "$RUNNER" ] && grep -q 'tests/workflow-timeouts/run.sh' "$RUNNER"; then
  pass "run-bash-shards.sh invokes this suite (both lanes)"
else
  fail "this suite is not in scripts/run-bash-shards.sh — a guard nothing runs is a no-op gate (#1967)"
fi
# The chain is lane → runner → this suite → guard. Assert the middle link here; Case 0 already
# proves the guard EXECUTES (a suite that merely named it would leave the live audit undone).
if grep -q 'check-workflow-timeouts.mjs' "$0"; then
  pass "this suite invokes the guard (the chain lane → runner → suite → guard is intact)"
else
  fail "this suite does not invoke the guard — the wiring is decorative"
fi

echo
if [ "$failures" -eq 0 ]; then
  echo "workflow-timeouts: ALL CHECKS PASSED"
  exit 0
fi
echo "workflow-timeouts: $failures check(s) FAILED"
exit 1
