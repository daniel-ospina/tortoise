#!/usr/bin/env bash
# orphan-bound.test.sh — self-check for .github/scripts/orphan-bound.sh (#4740).
#
# Run: bash .github/scripts/orphan-bound.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: writes
# hygiene-report fixtures to a temp dir. No CI, no runner, no network. Case 39
# reads `_HYGIENE_REPORT_FIELDS` from `tortoise/embedded_reaper.py`'s SOURCE
# with `ast` to pin the report↔gate contract — it never imports or runs the
# reaper. (It no longer reads `tests/conftest.py`: review 9 moved the end-sweep
# composition into the reaper as `build_end_sweep_report` and pins it
# behaviourally in `tests/test_reaper.py`, so the AST pin over `_sweep` is
# gone — see case 39's note below.)
#
# Coverage — one case per VERDICT-TABLE row, plus the positive controls that
# make the row's rule load-bearing:
#   * `no_embedded_servers` → bound 0, a count above 0 REDs, and a watchdog
#     kill downgrades that red (the `no_embedded_servers` cases 14-16).
#   * `{reaped, cleared, left}` → bound is READ from the report: two reports
#     with DIFFERENT `left` both PASS against their own value (cases 1, 2), so a
#     hardcoded bound cannot satisfy both.
#   * `cleared: false` → a DIAGNOSTIC at EVERY measured COUNT, never a verdict
#     (#4989): the arm emits one `::warning::` and the run PASSES whenever the
#     positive controls hold. Pinned at COUNT == 0 (cases 43, 45) — the #4740
#     case that must not regress — at COUNT == left (cases 3, 4, 35), at
#     COUNT < left (cases 41, 44, 45, 50), and at the exact #4989 reproduction
#     (case 50). The measurement-grounded reds on the same shape are unchanged:
#     COUNT > left (case 51) and the accounting identity (case 52). The arm
#     carries no #1371 kill branch — there is no red to downgrade (cases 4, 45).
#     The `probe_failed` (`left: null`) arm is SEPARATE and still keys on the
#     measured COUNT (case 40): `cleared` does not decide its verdict either.
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
#     above COUNT == 0 (cases 16b, 40), downgraded only by a watchdog kill
#     (cases 16c, 40) — never read as a plausible 0. A `COUNT` of 0 PASSES with
#     a warning at ANY `cleared` (cases 34, 40): there is no live residue to
#     bind. Case 40 pins that this arm applies the SAME measured-zero rule as
#     the `{reaped, cleared, left, before}` branch (cases 43-45), so `cleared`
#     is never itself a verdict. (Before #4740's COUNT==0 fix this arm still
#     REDded a `cleared: false` probe failure at COUNT == 0 — the asymmetry
#     that fix removes.)
#   * missing / unreadable / structurally-incomplete reports → RED (cases
#     17-21: 17/18 missing, 19/20 unreadable, 21 the report missing `left`),
#     downgraded only by a watchdog kill (cases 18, 20).
#   * the #1371 rc-unknown red path (the empty-rc case 22) and the fail-loud
#     argument validation (cases 23-25).
#   * the accounting identity `reaped + left >= before` → PASS at the boundary
#     (case 26), RED when violated even though COUNT <= left (case 27), and
#     only a kill downgrades it (case 28); skipped on `before: null` (case 29)
#     and `before: 0` (case 30) — while a report OMITTING `before` is unusable
#     → RED (case 31), so the control cannot be disabled by a shape change —
#     and it is enforced on the `cleared: false` path at COUNT == 0 (case 46),
#     where it is the remaining self-consistency control.
#   * the deadline-aborted sweep report (`reaped: 0, cleared: false`, with the
#     identity trivially satisfied and COUNT == left) → PASSES with the budget
#     warning (case 35), while the healthy residue shape (`cleared: true`)
#     PASSES with NO warning (case 36). The two shapes now differ only in the
#     warning, not the verdict (#4989): the measurement is self-consistent in
#     both, so the residue is bounded by `left` in both.
#   * a DEFERRED sweep (`other_suites` non-empty) → a warning and PASS with
#     the accounting identity skipped and only the `COUNT <= left` direction
#     applied (case 37); a deferred `COUNT` above even the mixed-population
#     `left` still REDs (case 38).
#   * a deferred fixture whose `left` DIFFERS from `COUNT` (case 49) so a swap
#     between those two fields on the deferred warning/pass lines is observable
#     (case 37's fixture has left == COUNT, which masks exactly that swap).
#   * the report↔gate contract (case 39, KEPT): the field set declared by
#     `_HYGIENE_REPORT_FIELDS` in `tortoise/embedded_reaper.py` (read from its
#     source with `ast` — never imported or run) must be named by the gate's
#     parser and carried by the harness's own canonical fixture, so a rename
#     cannot leave the harness green while the gate silently drops a field.
#     That is a producer↔consumer CONTRACT between two files, which is a
#     different thing from a data-flow property, so it stays here.
#   * (#4740 review 9) The SAME case USED TO also AST-pin the PRODUCER in
#     `tests/conftest.py`: `_sweep`'s returned builder call, the argument
#     order, each name's single binding, the pre/post-sweep order of the two
#     probe reads, and the absence of report-shaped dicts. Those sub-checks
#     were REMOVED. They police the COMPOSITION, which now lives in
#     `tortoise/embedded_reaper.py::build_end_sweep_report` and is pinned
#     BEHAVIOURALLY in `tests/test_reaper.py`:
#     `test_build_end_sweep_report_reads_probe_before_and_after_the_sweep`,
#     `test_build_end_sweep_report_threads_the_sweep_outcome` and
#     `test_live_embedded_server_count_wraps_the_probe`. Eight review rounds
#     showed a static shape check cannot prove a data-flow property — each
#     round closed one syntactic bypass (`left = max(...)`,
#     `for left in (...)`, `if (left := ...)`, `with ... as left`) while the
#     next found another. Driving the real function catches every one of those
#     as a wrong value or a wrong call count, which syntax cannot reach. The
#     AST read of `_HYGIENE_REPORT_FIELDS` above stays: it is the field-set
#     half of the contract.
#   * magnitude: an 18+ digit `--count` (case 32) and an 18+ digit `left` in
#     the report (case 33) both exit 2 as usage errors instead of reaching the
#     PASS line.
#   * the count-branch boundaries (cases 41): the structural-zero bounds
#     (`no_embedded_servers`, `skipped=no-pytest`) and the `left=null`
#     COUNT==0 carve-out are pinned at COUNT=1 — the first count past each
#     bound — so `-gt 0` cannot slide to `-gt 1`, `-gt 2`, or `-le 3` and
#     silently tolerate a residue that contradicts the bound's own comment.
#   * rc=1 is NOT a watchdog kill (case 42): `is_kill_rc` downgrades only
#     {124,137,2} for #1371, so a leak red on a merely-failing test run stays
#     RED — `1` in the kill set would silently warn every leak on the most
#     common non-zero rc.
#
# MUTATION PINS (verified by mutating the script, not the fixture): cases 5, 6,
# 7, 9, 10, 11, 12, 16, 17, 19, 21 each fail if their branch's verdict flips
# or its `exit 1` becomes a `return`/fall through, and cases 1/2 fail if the
# bound stops being read from `left`. Cases 27, 29, 30, 31, 32, 33 likewise
# fail when their new branch is removed or weakened (identity check dropped,
# `before: null` no longer accepted, `before` no longer required, either
# magnitude guard removed). Cases 34-40 fail when their branch is removed or
# weakened (the `probe_failed` COUNT==0 carve-out dropped, the deferral warning
# or its `COUNT <= left` rescue removed, the mixed-population identity made
# authoritative again, the contract check neutered). Cases 3, 4, 35, 41, 42,
# 43, 44, 45, 50 fail if the new warning is dropped or reworded (all of them
# pin it verbatim); restoring the removed `cleared=false && COUNT>0` red
# additionally reds 3, 4, 35, 41, 42, 44, 45, 50 on RC/pass-line (43 runs at
# COUNT == 0, where the old arm already warned) and pre-empts the measurement
# reds in 51/52; case 51 fails if `COUNT > left` stops redding — the
# guard that keeps the #4989 relaxation from being a blanket pass; case 52
# fails if the accounting identity is skipped whenever `cleared` is false; and
# case 40 fails if the `probe_failed` arm goes back to keying on `cleared` at
# COUNT == 0. The count-branch boundary pins (case 41) and the rc=1 kill-set
# pin (case 42) likewise fail when a bound is widened past 0 or `1` is added to
# the kill set (case 42's `cleared=false` sub-block now pins that the arm has
# NO kill branch at all).
# A case that merely restates a default would not catch its own removal.
#
# Every emitted line that interpolates a report measurement field ($COUNT,
# $left, $before, $reaped, $others, $foreign, $cleared) — plus $kind on the
# missing/unreadable arm — is pinned VERBATIM at the case that reaches it: the
# two pass lines, both deferred lines, every red message, and the
# argument-validation echoes. A single field, separator, or word swapped in any
# of them REDs instead of printing a wrong measurement. Cases: 1, 2, 3, 4, 5, 7,
# 8, 11-18, 23, 26-29, 32, 35-38, 40, 41, 43-45, 48, 49, 50, 51, 52.
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
printf '{"sweep":{"reaped":0,"cleared":false,"left":5,"before":40}}' > "$WORK/identity_bad_unproven.json"
printf '{"sweep":{"reaped":0,"cleared":true,"left":0,"before":5}}' > "$WORK/identity_boundary5.json"
printf '{"sweep":{"reaped":0,"cleared":true,"left":0,"before":1}}' > "$WORK/identity_boundary1.json"
printf '{"sweep":{"reaped":5,"cleared":false,"left":0,"before":5}}' > "$WORK/cleared_false_left_zero.json"
printf '{"sweep":{"reaped":12,"cleared":true,"left":14,"before":26}}' > "$WORK/identity_ok.json"
printf '{"sweep":{"reaped":1,"cleared":true,"left":2,"before":10}}' > "$WORK/identity_bad.json"
printf '{"sweep":{"reaped":1,"cleared":true,"left":2,"before":null}}' > "$WORK/before_null.json"
printf '{"sweep":{"reaped":0,"cleared":true,"left":2,"before":0}}' > "$WORK/before_zero.json"
printf '{"sweep":{"reaped":9,"cleared":true,"left":14}}' > "$WORK/nobefore.json"
printf '{"sweep":{"reaped":1,"cleared":true,"left":99999999999999999999999999,"before":10}}' > "$WORK/overflow_left.json"
printf '{"sweep":{"reaped":0,"cleared":false,"left":100,"before":100}}' > "$WORK/aborted.json"
printf '{"sweep":{"reaped":9,"cleared":false,"left":5,"before":14}}' > "$WORK/false4740.json"
printf '{"sweep":{"reaped":9,"cleared":false,"left":159,"before":168}}' > "$WORK/issue4989.json"
printf '{"sweep":{"reaped":9,"cleared":true,"left":100,"before":109}}' > "$WORK/healthy100.json"
printf '{"token":"t","other_suites":["1234-abcdef12"],"foreign_pids":[],"sweep":{"reaped":1,"cleared":true,"left":2,"before":40}}' > "$WORK/deferred.json"
printf '{"token":"t","other_suites":["1234-abcdef12"],"foreign_pids":["111","222"],"sweep":{"reaped":1,"cleared":true,"left":5,"before":40}}' > "$WORK/deferred5.json"
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
assert_contains "$OUT" "orphaned redislite servers after suite: 14 (sweep before=20 left=14 reaped=9, cleared=true) — within the sweep's own measurement" \
  "the COUNT==left pass line is reported VERBATIM — every field and separator"

echo "2. a DIFFERENT left also PASSES against its own value (the bound is read, not constant)"
# If the script carried any fixed bound, at most one of cases 1/2 could pass.
run_gate 7 0 report7.json
assert_eq "$RC" "0" "exits 0"
assert_contains "$OUT" "orphaned redislite servers after suite: 7 (sweep before=9 left=7 reaped=2, cleared=true) — within the sweep's own measurement" \
  "the bound is READ from the report: the whole pass line carries this report's own values"
assert_not_contains "$OUT" "left=14" "uses the report's value, not case 1's"

echo "3. cleared=false at COUNT>0 does NOT red — it warns and PASSES (#4989)"
# The old arm red at COUNT > 0; `cleared` describes the sweep's TIME BUDGET
# (runner load), not its residue, so at any measured count it is a diagnostic.
# Here COUNT == left == 14, so only the (removed) cleared arm could have red.
run_gate 14 0 budget.json
assert_eq "$RC" "0" "exits 0 at a non-zero count (the arm no longer reds)"
assert_contains "$OUT" "::warning::redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) with COUNT=14 against left=14 — the exhausted budget is diagnostic; the residue is bounded by left, not by the sweep's budget (issue #1005 / #4989)" \
  "the cleared=false warning is VERBATIM at COUNT==left — every field and word"
assert_contains "$OUT" "orphaned redislite servers after suite: 14 (sweep before=23 left=14 reaped=9, cleared=false) — within the sweep's own measurement" \
  "the COUNT==left pass line is VERBATIM with this report's own cleared=false"
assert_not_contains "$OUT" "::error::" "no red at a non-zero count"

echo "4. cleared=false is rc-independent: a watchdog kill rc does not change it"
# There is no red left to downgrade, so the same warning + pass line appear at
# rc=124 as at rc=0 — the arm carries no kill branch (#1371 does not apply).
run_gate 14 124 budget.json
assert_eq "$RC" "0" "exits 0 under rc=124"
assert_contains "$OUT" "::warning::redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) with COUNT=14 against left=14 — the exhausted budget is diagnostic; the residue is bounded by left, not by the sweep's budget (issue #1005 / #4989)" \
  "the warning is IDENTICAL under a kill rc (no kill branch on this arm)"
assert_contains "$OUT" "orphaned redislite servers after suite: 14 (sweep before=23 left=14 reaped=9, cleared=false) — within the sweep's own measurement" \
  "the pass line is printed under a kill rc too"
assert_not_contains "$OUT" "#1371" "the arm is not kill-aware — there is no red to downgrade"
assert_not_contains "$OUT" "::error::" "no red under a kill rc either"

echo "5. a COUNT ABOVE left REDs (the positive control)"
run_gate 15 0 report14.json
assert_eq "$RC" "1" "exits 1 when COUNT > left on a normal exit"
assert_contains "$OUT" "did not account for" "names the counter/population disagreement"
assert_contains "$OUT" "::error::redislite orphan gate: 15 servers counted but the sweep reported left=14 — the counter observes a population the sweep did not account for — pytest rc 0 (issue #1005 / epic #1647 E2E-7)" \
  "the COUNT>left red is reported VERBATIM at rc=0 (every field and separator)"
assert_not_contains "$OUT" "within the sweep's own measurement" \
  "does NOT fall through to the pass line (fail-open pin)"

echo "6. a kill downgrades only a count ABOVE the bound"
run_gate 15 124 report14.json
assert_eq "$RC" "0" "exits 0 under rc=124"
assert_contains "$OUT" "::warning::" "emits a warning"
assert_contains "$OUT" "#1371" "cites the kill-aware rationale"
assert_not_contains "$OUT" "within the sweep's own measurement" \
  "the warning path does not print a pass line either"

echo "7. a COUNT BELOW left PASSES at rc=0 (the NOSAVE window is normal)"
# Since #1005 `left` is read AFTER conftest's in-process close, the same seam
# as the workflow probe; the residual delta is the fire-and-forget NOSAVE
# window (~0.05s per server), so the later probe legitimately sees fewer. This
# is the direction that must never red.
run_gate 13 0 report14.json
assert_eq "$RC" "0" "exits 0 on COUNT < left"
assert_contains "$OUT" "orphaned redislite servers after suite: 13 (sweep before=20 left=14 reaped=9, cleared=true; 1 shut down after the sweep's post-close reading (fire-and-forget NOSAVE)) — within the sweep's own measurement" \
  "the COUNT<left pass line is reported VERBATIM — every field, separator and delta"
assert_not_contains "$OUT" "::error::" "no red on the healthy atexit boundary"

echo "8. a kill does NOT turn COUNT < left into a red either"
run_gate 13 124 report14.json
assert_eq "$RC" "0" "exits 0 on COUNT < left under rc=124"
assert_contains "$OUT" "orphaned redislite servers after suite: 13 (sweep before=20 left=14 reaped=9, cleared=true; 1 shut down after the sweep's post-close reading (fire-and-forget NOSAVE)) — within the sweep's own measurement" \
  "the COUNT<left pass line is VERBATIM under a kill too (the bound is unchanged)"
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
assert_contains "$OUT" "pytest never ran for this selection (skipped=no-pytest); no redislite server was spawned, 0 remain after suite" \
  "the no-pytest pass line is VERBATIM at COUNT=0"

echo "12. skipped=no-pytest with COUNT>0 REDs (servers nothing swept)"
run_gate 3 0 nopytest.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "nothing swept" "names the unswept population"
assert_contains "$OUT" "::error::redislite orphan gate: pytest never ran for this selection (skipped=no-pytest) but 3 redislite servers are live and nothing swept them (issue #1005)" \
  "the no-pytest red is VERBATIM — every field and word"
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
assert_contains "$OUT" "no embedded redislite server was running; 0 remain after suite" \
  "the no-embedded pass line is VERBATIM at COUNT=0"

echo "15. no_embedded_servers with a count above 0 REDs"
run_gate 3 0 none.json
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "no embedded server was ever running" "names the contradiction"
assert_contains "$OUT" "::error::redislite server leak: 3 servers after suite but the sweep reports no embedded server was ever running — pytest rc 0 (issue #1005 / epic #1647 E2E-7)" \
  "the no-embedded leak red is VERBATIM — every field and word"
assert_not_contains "$OUT" "remain after suite" "does NOT fall through to the pass line"

echo "16. no_embedded_servers above 0 under a kill downgrades to a warning"
run_gate 3 137 none.json
assert_eq "$RC" "0" "exits 0 under rc=137"
assert_contains "$OUT" "::warning::" "emits a warning"

echo "16b. left=null (the sweep's probe failed) REDs as an unmeasured residue"
run_gate 4 0 leftnull.json
assert_eq "$RC" "1" "exits 1 instead of reading the null as 0"
assert_contains "$OUT" "probe FAILED" "names the failed probe"
assert_contains "$OUT" "::error::redislite orphan gate: the hygiene end-sweep's own count probe FAILED (left=null) — the run produced no measurement to bind the bound to, so the 4 residue is unaccounted for (issue #1005) — pytest rc 0 (issue #1005 / epic #1647 E2E-7)" \
  "the probe-failure red is VERBATIM at rc=0"
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
assert_contains "$OUT" "::error::redislite orphan gate: no usable redislite-hygiene end-sweep report (missing) — the 4 residue is unaccounted for (issue #1005 / epic #1647 E2E-7)" \
  "the missing-report red is VERBATIM, naming kind=missing and COUNT=4"
assert_not_contains "$OUT" "no-pytest" "a real missing report is not excused"

echo "18. a missing report under a kill downgrades to a warning"
run_gate 4 2 does-not-exist.json
assert_eq "$RC" "0" "exits 0 under rc=2"
assert_contains "$OUT" "::warning::" "emits a warning"
assert_contains "$OUT" "::warning::no redislite-hygiene end-sweep report (missing) — rc=2: a watchdog kill skips the conftest end-sweep, so the count is a kill-path artifact, not a leak (issue #1371)" \
  "the kill-path missing-report warning is VERBATIM, naming kind=missing"

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
assert_contains "$OUT" "::error::orphan-bound: --count must be a non-negative integer (got 'x')" \
  "the invalid-count line is VERBATIM, echoing the offending input"

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
assert_contains "$OUT" "orphaned redislite servers after suite: 14 (sweep before=26 left=14 reaped=12, cleared=true) — within the sweep's own measurement" \
  "the COUNT==left pass line is VERBATIM for the identity fixture"

echo "27. an identity violation (reaped + left < before) REDs"
# The sweep reaped servers its own report no longer accounts for: the SWEEP's
# measurement, not the count, is broken. COUNT (2) <= left (2) here, so only
# the identity can produce the red.
run_gate 2 0 identity_bad.json
assert_eq "$RC" "1" "exits 1 even though COUNT <= left"
assert_contains "$OUT" "does not account for" "names the broken accounting"
assert_contains "$OUT" "::error::redislite orphan gate: the sweep does not account for the servers it started with — before=10, reaped=1, left=2 (reaped + left < before); the sweep's own measurement is broken — pytest rc 0 (issue #1005 / epic #1647 E2E-7)" \
  "the accounting-identity red is VERBATIM at rc=0"
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
assert_contains "$OUT" "orphaned redislite servers after suite: 2 (sweep before=null left=2 reaped=1, cleared=true) — within the sweep's own measurement" \
  "the COUNT==left pass line is VERBATIM with a null before too"
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
assert_contains "$OUT" "::error::orphan-bound: --count is too large to compare safely (got '99999999999999999999999999')" \
  "the oversized-count line is VERBATIM"

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

echo "35. a deadline-aborted sweep report now WARNS and PASSES (#4989)"
# The abort shape: reap() hit the already-expired deadline on its first
# record and returned []; the identity holds (0 + 100 >= 100) and
# COUNT == left (100). The measurement is self-consistent, so the exhausted
# budget is a diagnostic — the residue is bounded by `left`, not the budget.
run_gate 100 0 aborted.json
assert_eq "$RC" "0" "exits 0 — the identity holds and COUNT==left, so only the budget was exhausted"
assert_contains "$OUT" "::warning::redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) with COUNT=100 against left=100 — the exhausted budget is diagnostic; the residue is bounded by left, not by the sweep's budget (issue #1005 / #4989)" \
  "the aborted sweep emits the same VERBATIM warning"
assert_contains "$OUT" "orphaned redislite servers after suite: 100 (sweep before=100 left=100 reaped=0, cleared=false) — within the sweep's own measurement" \
  "the COUNT==left pass line is VERBATIM for the aborted shape"
assert_not_contains "$OUT" "::error::" "does not red the unexamined-but-bounded backlog"

echo "36. the healthy residue shape PASSES with NO budget warning (cleared=true)"
run_gate 100 0 healthy100.json
assert_eq "$RC" "0" "exits 0 on reaped>0, cleared=true"
assert_contains "$OUT" "orphaned redislite servers after suite: 100 (sweep before=109 left=100 reaped=9, cleared=true) — within the sweep's own measurement" \
  "the COUNT==left pass line is VERBATIM for the healthy residue shape"
assert_not_contains "$OUT" "::warning::" "cleared=true emits no budget warning"

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
assert_contains "$OUT" "::warning::redislite orphan gate: the hygiene end-sweep deferred to last-suite-standing (other_suites=1 foreign_pids=0) — left=2 counts other suites' live servers, so it is NOT an authoritative bound for this suite's residue and NO bound is enforced on this path; only the COUNT <= left sanity check is applied (non-gating diagnostic, issue #4740)" \
  "the deferral warning is VERBATIM — every field and word"
assert_contains "$OUT" "deferred sweep: 2 counted; the end-sweep deferred to last-suite-standing (other_suites=1), so left=2 is not an authoritative bound for this suite's residue" \
  "the deferred pass line is VERBATIM — every field and word"
assert_not_contains "$OUT" "does not account for" \
  "the mixed-population identity is not asserted under a deferral"

echo "38. a deferred COUNT above even the mixed-population left still REDs"
run_gate 3 0 deferred.json
assert_eq "$RC" "1" "exits 1 when the count exceeds even the deferred measurement"
assert_contains "$OUT" "::error::redislite orphan gate: 3 servers counted exceed even the deferred sweep's left=2 (other_suites=1 live) — the counter observes a population the sweep did not account for — pytest rc 0 (issue #1005 / epic #1647 E2E-7)" \
  "the deferred COUNT>left red is VERBATIM at rc=0"

echo "39. the report↔gate contract (single source of truth)"
# #4740: tortoise/embedded_reaper.py owns the report field set in
# _HYGIENE_REPORT_FIELDS; the gate's parser must name every field, and this
# harness's canonical fixture must carry exactly that set. The reaper source
# is PARSED with ast — never imported or run. The orphan_bound_scripts CI
# trigger lists tortoise/embedded_reaper.py for exactly this case.
REAPER="$SCRIPT_DIR/../../tortoise/embedded_reaper.py"
FIELDS="$(python3 - "$REAPER" <<'PY' 2>/dev/null
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
  "reads the report field set from the reaper's own source"
MISSING=""
for _field in $FIELDS; do
  grep -q "\"$_field\"" "$GATE" || MISSING="$MISSING $_field"
done
assert_eq "$MISSING" "" "the gate's parser names every declared report field"
FIXTURE_KEYS="$(python3 -c 'import json,sys; print(" ".join(sorted(json.load(open(sys.argv[1]))["sweep"])))' "$WORK/report14.json")"
assert_eq "$FIXTURE_KEYS" "$(printf '%s\n' $FIELDS | sort | tr '\n' ' ' | sed 's/ *$//')" \
  "the harness's canonical fixture carries exactly the declared field set"
# (#4740 review 9) The PRODUCER pin that used to live here was REMOVED.
# It AST-pinned the composition inside `tests/conftest.py`'s `_sweep`
# (the returned builder call, argument order, each name's single binding,
# the pre/post-sweep order of the two probe reads, and the absence of
# report-shaped dicts). That composition now lives in
# `tortoise/embedded_reaper.py::build_end_sweep_report` and is pinned
# BEHAVIOURALLY in `tests/test_reaper.py`:
#   test_build_end_sweep_report_reads_probe_before_and_after_the_sweep
#   test_build_end_sweep_report_threads_the_sweep_outcome
#   test_live_embedded_server_count_wraps_the_probe
# A static shape check cannot prove a data-flow property: eight review
# rounds each closed one syntactic bypass (`left = max(...)`,
# `for left in (...)`, `if (left := ...)`, `with ... as left`) while the
# next found another. Driving the real function catches every one of them
# as a wrong value or a wrong call count. The field-set half of case 39
# above is kept — it pins a producer/consumer contract between two files.

echo "40. left=null is verdict-uniform with the report branch: COUNT decides, not cleared"
# The two arms are now consistent: an unproven sweep is acceptable only at a
# measured zero. A `cleared: false` probe failure at COUNT == 0 is the same
# empty residue as a `cleared: true` one (case 34) — nothing live to bind — and
# above zero it REDs whatever `cleared` says.
run_gate 0 0 leftnull_aborted.json
assert_eq "$RC" "0" "exits 0 at COUNT=0 even with cleared=false"
assert_contains "$OUT" "::warning::" "warns that the sweep-side probe failed"
assert_not_contains "$OUT" "unaccounted for" "does not red an empty residue"
run_gate 1 0 leftnull_aborted.json
assert_eq "$RC" "1" "exits 1 at COUNT=1 (the residue is unmeasured)"
assert_contains "$OUT" "cleared=false" "names the exhausted budget"
assert_contains "$OUT" "probe FAILED" "names the failed sweep-side probe"
assert_contains "$OUT" "::error::redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) AND its own count probe FAILED (left=null) — the 1 servers that remain are unmeasured, not a bounded outcome (issue #1005) — pytest rc 0 (issue #1005 / epic #1647 E2E-7)" \
  "the cleared=false probe-failure red is VERBATIM at rc=0"
assert_not_contains "$OUT" "diagnostic only" "does not excuse the unmeasured residue"
run_gate 1 124 leftnull_aborted.json
assert_eq "$RC" "0" "a watchdog kill still downgrades it (rc=124)"
assert_contains "$OUT" "::warning::" "emits a warning under a kill"

echo "41. the count-branch boundaries are exact (COUNT=1 is the first red past each zero bound)"
# #4740 review 6: fixtures only used COUNT=0 and COUNT>=3, so `-gt 0` could
# slide to `-gt 1`/`-gt 2`, and the `left=null` COUNT==0 carve-out to `-le 3`,
# with the harness still green. COUNT=1 is the first count past each bound.
run_gate 1 0 none.json
assert_eq "$RC" "1" "no_embedded_servers: COUNT=1 REDs (the bound is structural zero, not a tolerance)"
assert_contains "$OUT" "no embedded server was ever running" "names the contradiction"
run_gate 1 0 nopytest.json
assert_eq "$RC" "1" "skipped=no-pytest: COUNT=1 REDs (nothing was spawned, so nothing may remain)"
assert_contains "$OUT" "nothing swept" "names the unswept population"
run_gate 1 0 leftnull.json
assert_eq "$RC" "1" "left=null: COUNT=1 REDs (the COUNT==0 carve-out is exact, not widened to 3)"
assert_contains "$OUT" "probe FAILED" "names the failed sweep-side probe"
# cleared=false is NOT count-gated (#4989): COUNT=1 must emit the same
# warning and PASS, so a re-introduced `[ "$COUNT" -gt 0 ]` (which would red
# here) is caught. The measurement reds are unchanged — case 51 (COUNT>left)
# and cases 27/47/52 (identity).
run_gate 1 0 budget.json
assert_eq "$RC" "0" "cleared=false: COUNT=1 PASSES (the arm is not count-gated)"
assert_contains "$OUT" "::warning::redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) with COUNT=1 against left=14 — the exhausted budget is diagnostic; the residue is bounded by left, not by the sweep's budget (issue #1005 / #4989)" \
  "the warning is VERBATIM at COUNT=1 (no measured-zero carve-out)"
assert_not_contains "$OUT" "::error::" "does not red a live residue on the budget alone"

echo "42. rc=1 (tests failed) is NOT a watchdog kill — red arms stay RED"
# #4740 review 6: `1` is the ordinary "tests failed" rc, not a #1371 kill.
# If it joined the kill set, every leak red on a merely-failing run would print
# `::warning::` and the step would PASS.
run_gate 1 1 none.json
assert_eq "$RC" "1" "no_embedded_servers REDs at rc=1"
assert_contains "$OUT" "::error::" "emits an error, not a warning"
assert_not_contains "$OUT" "::warning::" "does not downgrade a leak on a failing run"
# The cleared=false arm is no longer a red at all, so rc=1 changes nothing
# here: it must warn and PASS at rc=1 just as at rc=0 (cases 3/44). The rc
# still matters for the two genuine red arms above.
run_gate 14 1 budget.json
assert_eq "$RC" "0" "cleared=false PASSES at rc=1 (the arm has no red to keep)"
assert_contains "$OUT" "::warning::" "warns rather than erroring at rc=1"
assert_not_contains "$OUT" "::error::" "does not red the budget on a merely-failing run"
run_gate 15 1 report14.json
assert_eq "$RC" "1" "COUNT > left REDs at rc=1"
assert_contains "$OUT" "::error::" "emits an error, not a warning"
assert_not_contains "$OUT" "::warning::" "does not downgrade a count mismatch on a failing run"

echo "43. cleared=false with COUNT==0 still PASSES with a warning (#4740 must not regress)"
# The CI false red (#4740, run 35893361130): reaped=9, cleared=false, left=5,
# before=14, and the workflow measured COUNT=0 — redislite's atexit had already
# shut the 5 servers down. A budget-exhausted sweep is a diagnostic at every
# count now (#4989); at a measured zero there is likewise nothing to bound.
run_gate 0 0 false4740.json
assert_eq "$RC" "0" "exits 0 when the workflow's own probe measured zero"
assert_contains "$OUT" "::warning::redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) with COUNT=0 against left=5 — the exhausted budget is diagnostic; the residue is bounded by left, not by the sweep's budget (issue #1005 / #4989)" \
  "the warning is VERBATIM at a measured zero — every field and word"
assert_contains "$OUT" "orphaned redislite servers after suite: 0 (sweep before=14 left=5 reaped=9, cleared=false; 5 shut down after the sweep's post-close reading (fire-and-forget NOSAVE)) — within the sweep's own measurement" \
  "the COUNT<left pass line is VERBATIM, interpolating this report's own cleared=false"
assert_not_contains "$OUT" "cleared=true" \
  "the pass line reports the sweep's own cleared=false, never a hardcoded true"
assert_not_contains "$OUT" "::error::" "does not red an empty residue"

echo "44. cleared=false with COUNT>0 PASSES and warns with the actual count (#4989)"
# COUNT (3) is below left (14), so with the old arm this was the ONLY red.
# The warning interpolates COUNT and left, so a swap is observable here.
run_gate 3 0 budget.json
assert_eq "$RC" "0" "exits 0 at a non-zero count"
assert_contains "$OUT" "::warning::redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) with COUNT=3 against left=14 — the exhausted budget is diagnostic; the residue is bounded by left, not by the sweep's budget (issue #1005 / #4989)" \
  "the warning is VERBATIM with COUNT=3 != left=14"
assert_contains "$OUT" "orphaned redislite servers after suite: 3 (sweep before=23 left=14 reaped=9, cleared=false; 11 shut down after the sweep's post-close reading (fire-and-forget NOSAVE)) — within the sweep's own measurement" \
  "the COUNT<left pass line is VERBATIM at COUNT=3"
assert_not_contains "$OUT" "::error::" "does not red"

echo "45. the cleared=false arm is identical at a kill rc (no red, no #1371 branch)"
run_gate 0 124 budget.json
assert_eq "$RC" "0" "exits 0 at COUNT=0 under rc=124"
assert_contains "$OUT" "::warning::redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) with COUNT=0 against left=14 — the exhausted budget is diagnostic; the residue is bounded by left, not by the sweep's budget (issue #1005 / #4989)" \
  "the warning at a measured zero is the same VERBATIM warning"
assert_contains "$OUT" "orphaned redislite servers after suite: 0 (sweep before=23 left=14 reaped=9, cleared=false; 14 shut down after the sweep's post-close reading (fire-and-forget NOSAVE)) — within the sweep's own measurement" \
  "the COUNT<left pass line is VERBATIM at a measured zero under a kill"
run_gate 3 124 budget.json
assert_eq "$RC" "0" "exits 0 at COUNT>0 under rc=124"
assert_contains "$OUT" "::warning::redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) with COUNT=3 against left=14 — the exhausted budget is diagnostic; the residue is bounded by left, not by the sweep's budget (issue #1005 / #4989)" \
  "the same warning at COUNT>0 — the kill rc changes nothing"
assert_contains "$OUT" "orphaned redislite servers after suite: 3 (sweep before=23 left=14 reaped=9, cleared=false; 11 shut down after the sweep's post-close reading (fire-and-forget NOSAVE)) — within the sweep's own measurement" \
  "the pass line is printed under a kill rc too"
assert_not_contains "$OUT" "#1371" "no kill-aware branch remains on this arm"

echo "46. the accounting identity is enforced under cleared=false at COUNT==0"
# A budget-exhausted sweep is not exempt from its own accounting:
# `reaped + left < before` means the sweep's own measurement is broken even
# when nothing is live. This is the remaining self-consistency control on the
# cleared=false path, so a report that violates it must RED — the warning is
# emitted first, but it is a diagnostic and does not rescue the red.
run_gate 0 0 identity_bad_unproven.json
assert_eq "$RC" "1" "exits 1 when reaped + left < before at COUNT=0 under cleared=false"
assert_contains "$OUT" "::warning::" "the budget warning is still emitted (diagnostic, not a rescue)"
# This fixture's `left` (5) differs from the COUNT (0) this case drives, so the
# line below is satisfied ONLY by the sweep's own measurement: a `left=$left`
# -> `left=$COUNT` swap on the identity red prints `left=0` for a report that
# measured `left=5` and cannot satisfy this pin (case 27 cannot catch that swap:
# there COUNT == left == 2, so both readings print identically).
assert_contains "$OUT" "::error::redislite orphan gate: the sweep does not account for the servers it started with — before=40, reaped=0, left=5 (reaped + left < before); the sweep's own measurement is broken — pytest rc 0 (issue #1005 / epic #1647 E2E-7)" \
  "the accounting-identity red is VERBATIM with left=5 != COUNT=0"

echo "47. the accounting identity guard's lower boundaries RED (cleared=true)"
# The identity is skipped ONLY when `before` is null or measures zero, so its
# guard is `before -gt 0`. Widening it — an extra `&& [ "$left" -gt 0 ]`, or
# `-gt 1` — turns a real should-red report green. These fixtures sit exactly on
# those boundaries (left=0, before=1 and before=5) so either widening is caught.
run_gate 0 0 identity_boundary5.json
assert_eq "$RC" "1" "exits 1 when left=0 and before=5 (identity still enforced)"
assert_contains "$OUT" "does not account for the servers it started with" \
  "names the broken sweep accounting, not some other red"
run_gate 0 0 identity_boundary1.json
assert_eq "$RC" "1" "exits 1 when left=0 and before=1 (the lower boundary)"
assert_contains "$OUT" "does not account for the servers it started with" \
  "names the broken sweep accounting at before=1"

echo "48. the COUNT==left pass line reports the sweep's own cleared=false"
# The pass line has TWO branches: COUNT < left and COUNT == left. Case 43
# exercises only the first, so a hardcoded `cleared=true` on the COUNT==left
# branch was unobserved. left=0 at COUNT=0 reaches that branch with
# cleared=false (reaped + left == before, so the identity holds).
run_gate 0 0 cleared_false_left_zero.json
assert_eq "$RC" "0" "exits 0 — nothing live to bound"
assert_contains "$OUT" "orphaned redislite servers after suite: 0 (sweep before=5 left=0 reaped=5, cleared=false) — within the sweep's own measurement" \
  "the COUNT==left pass line is VERBATIM, reporting the sweep's own cleared=false"
assert_not_contains "$OUT" "cleared=true" \
  "never fabricates cleared=true on the COUNT==left pass line"

echo "49. a DEFERRED sweep with left != COUNT pins the deferred lines' own fields"
# Case 37's fixture has left == COUNT (2 == 2), so a swap between those two
# fields on the deferred path — `left=$left` -> `left=$COUNT` on the pass line,
# or the mirror on the warning — prints a wrong measurement and stays green.
# Here left=5 and COUNT=2, and foreign_pids (2) differs from other_suites (1),
# so every field on both deferred lines is independently observable.
run_gate 2 0 deferred5.json
assert_eq "$RC" "0" "exits 0 — the deferral does not red a count within left"
assert_contains "$OUT" "::warning::redislite orphan gate: the hygiene end-sweep deferred to last-suite-standing (other_suites=1 foreign_pids=2) — left=5 counts other suites' live servers, so it is NOT an authoritative bound for this suite's residue and NO bound is enforced on this path; only the COUNT <= left sanity check is applied (non-gating diagnostic, issue #4740)" \
  "the deferral warning is VERBATIM with left=5 != COUNT=2 and foreign=2 != others=1"
assert_contains "$OUT" "deferred sweep: 2 counted; the end-sweep deferred to last-suite-standing (other_suites=1), so left=5 is not an authoritative bound for this suite's residue" \
  "the deferred pass line is VERBATIM with left=5 != COUNT=2"

echo "50. REGRESSION #4989: the reported loaded run (cleared=false, left=159, before=168) at COUNT=13 PASSES"
# The exact CI reproduction: reaped=9, left=159, before=168 (identity holds,
# 9 + 159 == 168), COUNT=13 <= left=159. The workflow's pgrep measured 13,
# inside the documented normal band (tests/conftest.py: "observed as 13
# orphans on CI"); only the removed `cleared=false && COUNT>0` arm could red
# it. This case fails if that arm or its message returns.
run_gate 13 0 issue4989.json
assert_eq "$RC" "0" "exits 0 — the reported false red is gone"
assert_contains "$OUT" "::warning::redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) with COUNT=13 against left=159 — the exhausted budget is diagnostic; the residue is bounded by left, not by the sweep's budget (issue #1005 / #4989)" \
  "emits the new warning VERBATIM at the reported fields"
assert_contains "$OUT" "orphaned redislite servers after suite: 13 (sweep before=168 left=159 reaped=9, cleared=false; 146 shut down after the sweep's post-close reading (fire-and-forget NOSAVE)) — within the sweep's own measurement" \
  "the COUNT<left pass line is VERBATIM at the reported fields"
assert_not_contains "$OUT" "::error::" "the diagnostic is not a red"

echo "51. on the SAME #4989 report a COUNT ABOVE left still REDs (not a blanket pass)"
# COUNT=200 > left=159: the counter observes a population the sweep did not
# account for. This is the guard that keeps the #4989 relaxation from turning
# the arm into a blanket pass.
run_gate 200 0 issue4989.json
assert_eq "$RC" "1" "exits 1 when COUNT exceeds the sweep's own left"
assert_contains "$OUT" "::error::redislite orphan gate: 200 servers counted but the sweep reported left=159 — the counter observes a population the sweep did not account for — pytest rc 0 (issue #1005 / epic #1647 E2E-7)" \
  "the COUNT>left red is VERBATIM on the #4989 report"
assert_not_contains "$OUT" "within the sweep's own measurement" \
  "does NOT fall through to the pass line (fail-open pin)"

echo "52. on a cleared=false report an accounting-identity violation at COUNT=13 still REDs"
# reaped=0 + left=5 < before=40: the sweep's own measurement is broken, so the
# budget warning does not rescue it. At COUNT=13 the COUNT>left arm also fires
# (13 > 5); the second run at COUNT=2 (<= left) isolates the identity, proving
# it alone still reds on a cleared=false report.
run_gate 13 0 identity_bad_unproven.json
assert_eq "$RC" "1" "exits 1 at COUNT=13 (left=5, before=40)"
assert_contains "$OUT" "::warning::" "the budget warning is still emitted (diagnostic, not a rescue)"
assert_contains "$OUT" "does not account for the servers it started with" "names the broken accounting at COUNT=13"
run_gate 2 0 identity_bad_unproven.json
assert_eq "$RC" "1" "exits 1 at COUNT=2 <= left=5, so only the identity can red"
assert_contains "$OUT" "::error::redislite orphan gate: the sweep does not account for the servers it started with — before=40, reaped=0, left=5 (reaped + left < before); the sweep's own measurement is broken — pytest rc 0 (issue #1005 / epic #1647 E2E-7)" \
  "the identity red is VERBATIM at COUNT=2 (COUNT <= left isolates it)"

echo
# A LOST case must not be indistinguishable from success: deleting a case
# leaves FAIL=0 and merely a LOWER count, so the count is pinned too.
expected_assertions=196
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
