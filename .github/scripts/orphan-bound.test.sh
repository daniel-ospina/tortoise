#!/usr/bin/env bash
# orphan-bound.test.sh — self-check for .github/scripts/orphan-bound.sh (#4740).
#
# Run: bash .github/scripts/orphan-bound.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: writes
# hygiene-report fixtures to a temp dir. No CI, no runner, no network. Case 39
# reads `tests/conftest.py`'s SOURCE with `ast` to pin the report contract —
# it never imports or runs conftest.
#
# Coverage — one case per VERDICT-TABLE row, plus the positive controls that
# make the row's rule load-bearing:
#   * `no_embedded_servers` → bound 0, a count above 0 REDs, and a watchdog
#     kill downgrades that red (cases 12-14).
#   * `{reaped, cleared, left}` → bound is READ from the report: two reports
#     with DIFFERENT `left` both PASS against their own value (cases 1, 2), so a
#     hardcoded bound cannot satisfy both.
#   * `cleared: false` → RED whatever the count is on a normal exit (case 3):
#     the true "hygiene is broken" signal a bigger constant would swallow —
#     but a watchdog kill downgrades it (case 4).
#   * the positive control: COUNT ABOVE `left` REDs (case 5), downgraded only
#     by a watchdog kill (case 6); COUNT BELOW `left` is the documented atexit
#     outcome and PASSES with the delta logged, at rc=0 (case 7) and under a
#     kill (case 8) — the direction that must never red a healthy run.
#   * `error` and `skipped` → RED ALWAYS (cases 9, 10), including under a kill.
#   * `skipped=no-pytest` (the empty-selection path) → PASS at COUNT=0 (case
#     11), RED at COUNT>0 (case 12), and NOT downgraded by a kill that never
#     happened (case 13) — while a genuinely MISSING report still REDs (case 17)
#     and is distinct (case 11's fixture is a DIFFERENT fixture from case 17's).
#   * `left: null` (the sweep's probe failed) → RED named as a probe failure
#     (case 16), downgraded only by a watchdog kill (case 16b) — never read as
#     a plausible 0. A `COUNT` of 0 PASSES with a warning (case 34) ONLY when
#     the sweep also reported `cleared: true`; the same shape with
#     `cleared: false` still REDs at COUNT == 0 (case 40): an exhausted budget
#     is not a diagnostic, and reading it as one is the #4740 review-5
#     fail-open.
#   * missing / unreadable / structurally-incomplete reports → RED (cases
#     17-20), downgraded only by a watchdog kill (cases 18, 20).
#   * the #1371 rc-unknown red path (case 21) and the fail-loud argument
#     validation (cases 22-24).
#   * the accounting identity `reaped + left >= before` → PASS at the boundary
#     (case 26), RED when violated even though COUNT <= left (case 27), and
#     only a kill downgrades it (case 28); skipped on `before: null` (case 29)
#     and `before: 0` (case 30) — while a report OMITTING `before` is unusable
#     → RED (case 31), so the control cannot be disabled by a shape change.
#   * the deadline-aborted sweep report (`reaped: 0, cleared: false`, with the
#     identity trivially satisfied and COUNT == left) → RED (case 35), while
#     the healthy residue shape (`cleared: true`) PASSES (case 36). Only the
#     `cleared` field separates them, so a gate that drops the `cleared`
#     branch greens the exact abort the sweep now reports.
#   * a DEFERRED sweep (`other_suites` non-empty) → a warning and PASS with
#     the accounting identity skipped and only the `COUNT <= left` direction
#     applied (case 37); a deferred `COUNT` above even the mixed-population
#     `left` still REDs (case 38).
#   * the conftest↔gate report contract (case 39): the field set declared by
#     `_HYGIENE_REPORT_FIELDS` in `tests/conftest.py` (read from its source
#     with `ast` — never imported or run) must be named by the gate's parser
#     and carried by the harness's own canonical fixture, so a conftest rename
#     cannot leave the harness green while the gate silently drops a field.
#     The SAME ast pass (case 39) pins the PRODUCER: in `_sweep`'s body the
#     name `cleared` must have exactly one binding, from
#     `sweep_until_cleared(...)`, and the report dict must carry that NAME —
#     so a regression to `cleared = True` (or `= not acted`) after the call
#     cannot green the gate by discarding the honest value.
#   * magnitude: an 18+ digit `--count` (case 32) and an 18+ digit `left` in
#     the report (case 33) both exit 2 as usage errors instead of reaching the
#     PASS line.
#
# MUTATION PINS (verified by mutating the script, not the fixture): cases 3, 4,
# 5, 6, 7, 9, 10, 11, 12, 16, 17, 19, 21 each fail if their branch's verdict
# flips or its `exit 1` becomes a `return`/fall through, and cases 1/2 fail if
# the bound stops being read from `left`. Cases 27, 29, 30, 31, 32, 33
# likewise fail when their new branch is removed or weakened (identity check
# dropped, `before: null` no longer accepted, `before` no longer required,
# either magnitude guard removed). Cases 34-40 fail when their branch is
# removed or weakened (the `probe_failed` COUNT==0 carve-out dropped, the
# `cleared=false` red removed, the deferral warning or its `COUNT <= left`
# rescue removed, the mixed-population identity made authoritative again, the
# contract check neutered, or `_sweep`'s `cleared` rebindable from a literal).
# A case that merely restates a default would not catch its own removal.
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
printf '{"sweep":{"reaped":9,"cleared":true,"left":14,"before":20}}' > "$WORK/report14.json"
printf '{"sweep":{"reaped":2,"cleared":true,"left":7,"before":9}}' > "$WORK/report7.json"
printf '{"sweep":{"reaped":9,"cleared":false,"left":14,"before":23}}' > "$WORK/budget.json"
printf '{"sweep":{"reaped":12,"cleared":true,"left":14,"before":26}}' > "$WORK/identity_ok.json"
printf '{"sweep":{"reaped":1,"cleared":true,"left":2,"before":10}}' > "$WORK/identity_bad.json"
printf '{"sweep":{"reaped":1,"cleared":true,"left":2,"before":null}}' > "$WORK/before_null.json"
printf '{"sweep":{"reaped":0,"cleared":true,"left":2,"before":0}}' > "$WORK/before_zero.json"
printf '{"sweep":{"reaped":9,"cleared":true,"left":14}}' > "$WORK/nobefore.json"
printf '{"sweep":{"reaped":1,"cleared":true,"left":99999999999999999999999999,"before":10}}' > "$WORK/overflow_left.json"
printf '{"sweep":{"reaped":0,"cleared":false,"left":100,"before":100}}' > "$WORK/aborted.json"
printf '{"sweep":{"reaped":9,"cleared":true,"left":100,"before":109}}' > "$WORK/healthy100.json"
printf '{"token":"t","other_suites":["1234-abcdef12"],"foreign_pids":[],"sweep":{"reaped":1,"cleared":true,"left":2,"before":40}}' > "$WORK/deferred.json"
printf '{"sweep":{"error":"probe exploded"}}' > "$WORK/error.json"
printf '{"sweep":{"skipped":"reaper-lock-held"}}' > "$WORK/skipped.json"
printf '{"sweep":{"skipped":"no-pytest"}}' > "$WORK/nopytest.json"
printf '{"sweep":{"no_embedded_servers":true}}' > "$WORK/none.json"
printf '{"sweep":{"reaped":9,"cleared":true}}' > "$WORK/noleft.json"
printf '{"sweep":{"reaped":9,"cleared":true,"left":null}}' > "$WORK/leftnull.json"
printf '{"sweep":{"reaped":0,"cleared":false,"left":null,"before":5}}' > "$WORK/leftnull_aborted.json"
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

echo "3. cleared=false REDs whatever the count is (a normal exit)"
run_gate 14 0 budget.json
assert_eq "$RC" "1" "exits 1 even though COUNT==left"
assert_contains "$OUT" "cleared=false" "names the exhausted budget"
assert_contains "$OUT" "arbitrary" "states the residue is unbounded"
assert_not_contains "$OUT" "within the sweep's own measurement" "never prints a pass line"

echo "4. cleared=false under a watchdog kill downgrades to a warning"
# #1371: pytest runs session teardown on SIGINT, so a killed run can
# legitimately exhaust the sweep budget; the run is already red.
run_gate 14 124 budget.json
assert_eq "$RC" "0" "exits 0 under rc=124"
assert_contains "$OUT" "::warning::" "emits a warning"
assert_contains "$OUT" "#1371" "cites the kill-aware rationale"
assert_not_contains "$OUT" "within the sweep's own measurement" \
  "the warning path does not print a pass line either"

echo "5. a COUNT ABOVE left REDs (the positive control)"
run_gate 15 0 report14.json
assert_eq "$RC" "1" "exits 1 when COUNT > left on a normal exit"
assert_contains "$OUT" "did not account for" "names the counter/population disagreement"
assert_not_contains "$OUT" "within the sweep's own measurement" \
  "does NOT fall through to the pass line (fail-open pin)"

echo "6. a kill downgrades only a count ABOVE the bound"
run_gate 15 124 report14.json
assert_eq "$RC" "0" "exits 0 under rc=124"
assert_contains "$OUT" "::warning::" "emits a warning"
assert_contains "$OUT" "#1371" "cites the kill-aware rationale"
assert_not_contains "$OUT" "within the sweep's own measurement" \
  "the warning path does not print a pass line either"

echo "7. a COUNT BELOW left PASSES at rc=0 (the atexit race is normal)"
# `left` is read during fixture teardown; redislite's atexit handler shuts its
# last-client servers down after pytest fully exits, so the workflow probe
# legitimately sees fewer. This is the direction that must never red.
run_gate 13 0 report14.json
assert_eq "$RC" "0" "exits 0 on COUNT < left"
assert_contains "$OUT" "within the sweep's own measurement" "prints the pass line"
assert_contains "$OUT" "interpreter exit" "logs the atexit delta"
assert_not_contains "$OUT" "::error::" "no red on the healthy atexit boundary"

echo "8. a kill does NOT turn COUNT < left into a red either"
run_gate 13 124 report14.json
assert_eq "$RC" "0" "exits 0 on COUNT < left under rc=124"
assert_contains "$OUT" "within the sweep's own measurement" "prints the pass line"
assert_not_contains "$OUT" "::warning::" "no spurious warning for a below-bound count"

echo "9. an error report REDs ALWAYS — including under a kill"
run_gate 3 0 error.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "reported an error" "names the crashed hygiene path"
run_gate 3 124 error.json
assert_eq "$RC" "1" "stays RED under a watchdog kill (the residue is unaccounted)"
assert_not_contains "$OUT" "::warning::" "no downgrade for a crashed hygiene path"

echo "10. a skipped report REDs ALWAYS — including under a kill"
run_gate 3 0 skipped.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "reaper lock held" "names the skipped sweep"
run_gate 3 137 skipped.json
assert_eq "$RC" "1" "stays RED under a watchdog kill"
assert_not_contains "$OUT" "::warning::" "no downgrade for a skipped sweep"

echo "11. skipped=no-pytest with COUNT=0 PASSES (pytest never ran, nothing spawned)"
run_gate 0 0 nopytest.json
assert_eq "$RC" "0" "exits 0"
assert_contains "$OUT" "no-pytest" "names the never-ran path"
assert_contains "$OUT" "no redislite server was spawned" "states the basis"

echo "12. skipped=no-pytest with COUNT>0 REDs (servers nothing swept)"
run_gate 3 0 nopytest.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "nothing swept" "names the unswept population"
assert_not_contains "$OUT" "no redislite server was spawned" \
  "does NOT fall through to the pass line"

echo "13. skipped=no-pytest is NOT kill-downgraded (no pytest ran to kill)"
run_gate 3 124 nopytest.json
assert_eq "$RC" "1" "stays RED under rc=124"
assert_not_contains "$OUT" "::warning::" "no downgrade on the never-ran path"

echo "14. no_embedded_servers with COUNT=0 PASSES (bound 0)"
run_gate 0 0 none.json
assert_eq "$RC" "0" "exits 0"
assert_contains "$OUT" "no embedded redislite server was running" "states the basis"

echo "15. no_embedded_servers with a count above 0 REDs"
run_gate 3 0 none.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "no embedded server was ever running" "names the contradiction"
assert_not_contains "$OUT" "remain after suite" "does NOT fall through to the pass line"

echo "16. no_embedded_servers above 0 under a kill downgrades to a warning"
run_gate 3 137 none.json
assert_eq "$RC" "0" "exits 0 under rc=137"
assert_contains "$OUT" "::warning::" "emits a warning"

echo "16b. left=null (the sweep's probe failed) REDs as an unmeasured residue"
run_gate 4 0 leftnull.json
assert_eq "$RC" "1" "exits 1 instead of reading the null as 0"
assert_contains "$OUT" "probe FAILED" "names the failed probe"
assert_not_contains "$OUT" "within the sweep's own measurement" "never prints a pass line"

echo "16c. left=null under a kill downgrades to a warning"
run_gate 4 124 leftnull.json
assert_eq "$RC" "0" "exits 0 under rc=124"
assert_contains "$OUT" "::warning::" "emits a warning"
assert_contains "$OUT" "#1371" "cites the kill-aware rationale"

echo "17. a missing report REDs (residue unaccounted, NOT the no-pytest path)"
run_gate 4 0 does-not-exist.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "unaccounted" "names the missing accounting"
assert_not_contains "$OUT" "no-pytest" "a real missing report is not excused"

echo "18. a missing report under a kill downgrades to a warning"
run_gate 4 2 does-not-exist.json
assert_eq "$RC" "0" "exits 0 under rc=2"
assert_contains "$OUT" "::warning::" "emits a warning"

echo "19. an unreadable report REDs"
run_gate 4 0 unreadable.json
assert_eq "$RC" "1" "exits 1 on invalid JSON"
assert_contains "$OUT" "unreadable" "names the unreadable report"

echo "20. an unreadable report under a kill downgrades to a warning"
run_gate 4 124 unreadable.json
assert_eq "$RC" "0" "exits 0 under rc=124"
assert_contains "$OUT" "::warning::" "emits a warning"

echo "21. a report missing the left field REDs (fail-closed on an incomplete report)"
run_gate 4 0 noleft.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "unreadable" "treats the incomplete report as unusable"

echo "22. an empty rc reds on a leak with the rc-unknown message"
run_gate 3 "" none.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "rc unknown" "names the unknown pytest rc"

echo "23. a non-numeric count exits 2 (fail loud, not a code path)"
bad_rc=0
OUT=$(bash "$GATE" --count x --rc 0 --hygiene "$WORK/none.json" 2>&1) || bad_rc=$?
assert_eq "$bad_rc" "2" "exits 2"
assert_contains "$OUT" "--count must be a non-negative integer" "names the bad count"

echo "24. an unknown argument exits 2"
bad_rc=0
OUT=$(bash "$GATE" --count 1 --nope 2>&1) || bad_rc=$?
assert_eq "$bad_rc" "2" "exits 2"
assert_contains "$OUT" "unknown argument" "names the bad flag"

echo "25. a flag with no value exits 2"
bad_rc=0
OUT=$(bash "$GATE" --count 1 --rc 2>&1) || bad_rc=$?
assert_eq "$bad_rc" "2" "exits 2"
assert_contains "$OUT" "requires a value" "names the missing value"

echo "26. the accounting identity reaped + left >= before holds → PASS"
# reaped=12, left=14, before=26 — the identity holds at exact equality.
run_gate 14 0 identity_ok.json
assert_eq "$RC" "0" "exits 0 when reaped + left == before"
assert_contains "$OUT" "within the sweep's own measurement" "prints the pass line"

echo "27. an identity violation (reaped + left < before) REDs"
# The sweep reaped servers its own report no longer accounts for: the SWEEP's
# measurement, not the count, is broken. COUNT (2) <= left (2) here, so only
# the identity can produce the red.
run_gate 2 0 identity_bad.json
assert_eq "$RC" "1" "exits 1 even though COUNT <= left"
assert_contains "$OUT" "does not account for" "names the broken accounting"
assert_not_contains "$OUT" "within the sweep's own measurement" \
  "does NOT fall through to the pass line (fail-open pin)"

echo "28. an identity violation under a watchdog kill downgrades to a warning"
run_gate 2 137 identity_bad.json
assert_eq "$RC" "0" "exits 0 under rc=137"
assert_contains "$OUT" "::warning::" "emits a warning"
assert_contains "$OUT" "#1371" "cites the kill-aware rationale"

echo "29. before=null SKIPS the identity (the pre-sweep probe failed) — not a red"
run_gate 2 0 before_null.json
assert_eq "$RC" "0" "exits 0"
assert_contains "$OUT" "within the sweep's own measurement" "prints the pass line"
assert_not_contains "$OUT" "does not account for" \
  "the identity is not asserted against a null before"

echo "30. before=0 SKIPS the identity — not a red"
run_gate 2 0 before_zero.json
assert_eq "$RC" "0" "exits 0"
assert_not_contains "$OUT" "does not account for" "the identity is skipped at before=0"

echo "31. a report that OMITS before is unusable → RED (the control cannot be disabled)"
run_gate 2 0 nobefore.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "unreadable" "treats the missing identity input as unusable"

echo "32. an oversized --count exits 2 (magnitude, not just characters)"
# 99999999999999999999999999 is beyond bash's 64-bit integer range: `[` would
# return 2 and the false branch would reach the PASS line.
bad_rc=0
OUT=$(bash "$GATE" --count 99999999999999999999999999 --rc 0 --hygiene "$WORK/report14.json" 2>&1) || bad_rc=$?
assert_eq "$bad_rc" "2" "exits 2 instead of reaching the pass line"
assert_contains "$OUT" "too large to compare" "names the magnitude guard"

echo "33. an oversized left in the report exits 2 (never a pass)"
bad_rc=0
OUT=$(bash "$GATE" --count 1 --rc 0 --hygiene "$WORK/overflow_left.json" 2>&1) || bad_rc=$?
assert_eq "$bad_rc" "2" "exits 2"
assert_contains "$OUT" "too large to compare" "names the magnitude guard"

echo "34. left=null with COUNT=0 PASSES with a warning (nothing live to account for)"
# The workflow's own identical pgrep measured zero, so no residue exists:
# a failed sweep-side probe is a diagnostic, not an orphan. RED only when
# COUNT > 0 (case 16b).
run_gate 0 0 leftnull.json
assert_eq "$RC" "0" "exits 0 when the workflow's own probe measured zero"
assert_contains "$OUT" "::warning::" "warns that the sweep-side probe failed"
assert_not_contains "$OUT" "unaccounted for" "does not red an empty residue"

echo "35. a deadline-aborted sweep report REDs (cleared=false proves it)"
# The abort shape: reap() hit the already-expired deadline on its first
# record and returned []; the identity holds (0 + 100 >= 100) and
# COUNT == left (100), so ONLY the cleared field can catch the unexamined
# backlog. Before the conftest fix this shape reported cleared=true.
run_gate 100 0 aborted.json
assert_eq "$RC" "1" "exits 1 even though the identity holds and COUNT==left"
assert_contains "$OUT" "cleared=false" "names the aborted sweep"
assert_not_contains "$OUT" "within the sweep's own measurement" \
  "does NOT fall through to the pass line (fail-open pin)"

echo "36. the healthy residue shape PASSES (only cleared separates it from case 35)"
run_gate 100 0 healthy100.json
assert_eq "$RC" "0" "exits 0 on reaped>0, cleared=true"
assert_contains "$OUT" "within the sweep's own measurement" "prints the pass line"

echo "37. a DEFERRED sweep warns, skips the mixed-population identity, and PASSES"
# other_suites non-empty: left counts other suites' servers, so it is not an
# authoritative bound. Here the foreign suite exited mid-sweep, so
# reaped + left (3) < before (40) — the identity would false-red a healthy
# deferral, which is exactly why it is skipped under a deferral.
run_gate 2 0 deferred.json
assert_eq "$RC" "0" "exits 0 — left is not an authoritative bound under a deferral"
assert_contains "$OUT" "::warning::" "names the deferral"
assert_contains "$OUT" "non-gating diagnostic" "labels the deferred path a non-gating diagnostic"
assert_contains "$OUT" "not an authoritative bound" "states why the bound is not authoritative"
assert_not_contains "$OUT" "does not account for" \
  "the mixed-population identity is not asserted under a deferral"

echo "38. a deferred COUNT above even the mixed-population left still REDs"
run_gate 3 0 deferred.json
assert_eq "$RC" "1" "exits 1 when the count exceeds even the deferred measurement"

echo "39. the conftest↔gate report contract (single source of truth)"
# #4740: tests/conftest.py declares the report field set in
# _HYGIENE_REPORT_FIELDS; the gate's parser must name every field, and this
# harness's canonical fixture must carry exactly that set. The conftest source
# is PARSED with ast — never imported or run. The orphan_bound_scripts CI
# trigger lists tests/conftest.py for exactly this case.
CONFTEST="$SCRIPT_DIR/../../tests/conftest.py"
FIELDS="$(python3 - "$CONFTEST" <<'PY' 2>/dev/null
import ast
import sys

try:
    tree = ast.parse(open(sys.argv[1], encoding="utf-8").read())
except OSError:
    raise SystemExit(0)
for node in tree.body:
    if isinstance(node, ast.Assign) and any(
        getattr(t, "id", None) == "_HYGIENE_REPORT_FIELDS"
        for t in node.targets
    ):
        print(" ".join(ast.literal_eval(node.value)))
        break
PY
)"
assert_eq "$FIELDS" "reaped cleared left before" \
  "reads the report field set from conftest's own source"
MISSING=""
for _field in $FIELDS; do
  grep -q "\"$_field\"" "$GATE" || MISSING="$MISSING $_field"
done
assert_eq "$MISSING" "" "the gate's parser names every conftest report field"
FIXTURE_KEYS="$(python3 -c 'import json,sys; print(" ".join(sorted(json.load(open(sys.argv[1]))["sweep"])))' "$WORK/report14.json")"
assert_eq "$FIXTURE_KEYS" "$(printf '%s\n' $FIELDS | sort | tr '\n' ' ' | sed 's/ *$//')" \
  "the harness's canonical fixture carries exactly the declared field set"

# The SAME source, pinned for the PRODUCER rather than the field set: `_sweep`
# must thread the helper's own `cleared` into the report. The gate cases above
# pin the CONSUMER (`cleared: false` reds) and tests/test_reaper.py pins the
# helper's stop shapes — but nothing pinned that `_sweep` binds `cleared` FROM
# the helper, so a regression to `cleared = True` (or `= not acted`) after the
# call would green a deadline-aborted backlog invisibly (#4740 review 5). Read
# `_sweep`'s AST: `cleared` must have exactly ONE Store binding and it must be
# the `sweep_until_cleared(...)` unpack; the report dict must carry that NAME.
PRODUCER="$(python3 - "$CONFTEST" <<'PY' 2>/dev/null
import ast
import sys

try:
    tree = ast.parse(open(sys.argv[1], encoding="utf-8").read())
except OSError:
    print("no-source")
    raise SystemExit(0)

fn = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_sweep":
        fn = node
        break
if fn is None:
    print("no-sweep-fn")
    raise SystemExit(0)

stores = [
    n
    for n in ast.walk(fn)
    if isinstance(n, ast.Name)
    and n.id == "cleared"
    and isinstance(n.ctx, ast.Store)
]

helper = 0
for node in ast.walk(fn):
    if not isinstance(node, ast.Assign):
        continue
    if not (
        isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "sweep_until_cleared"
    ):
        continue
    for tgt in node.targets:
        helper += len(
            [
                n
                for n in ast.walk(tgt)
                if isinstance(n, ast.Name)
                and n.id == "cleared"
                and isinstance(n.ctx, ast.Store)
            ]
        )

report = any(
    isinstance(k, ast.Constant)
    and k.value == "cleared"
    and isinstance(v, ast.Name)
    and v.id == "cleared"
    for node in ast.walk(fn)
    if isinstance(node, ast.Dict)
    for k, v in zip(node.keys, node.values)
)

print(f"stores={len(stores)} helper={helper} report={int(report)}")
PY
)"
assert_eq "$PRODUCER" "stores=1 helper=1 report=1" \
  "_sweep binds cleared only from sweep_until_cleared and reports that NAME"

echo "40. left=null with cleared=FALSE REDs even at COUNT=0 (an exhausted budget is not a diagnostic)"
# #4740 review 5: the COUNT==0 carve-out (case 34) exists only for a sweep that
# FINISHED. The same failed probe from an EXHAUSTED budget is the sweep's own
# "hygiene is broken" signal and must red whatever the count is.
run_gate 0 0 leftnull_aborted.json
assert_eq "$RC" "1" "exits 1 at COUNT=0 when the sweep reported cleared=false"
assert_contains "$OUT" "cleared=false" "names the exhausted budget"
assert_contains "$OUT" "probe FAILED" "names the failed sweep-side probe"
assert_not_contains "$OUT" "diagnostic only" "does not excuse the aborted sweep"
run_gate 0 124 leftnull_aborted.json
assert_eq "$RC" "0" "a watchdog kill still downgrades it (rc=124)"
assert_contains "$OUT" "::warning::" "emits a warning under a kill"

echo
# A LOST case must not be indistinguishable from success: deleting a case
# leaves FAIL=0 and merely a LOWER count, so the count is pinned too.
expected_assertions=120
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
