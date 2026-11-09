#!/usr/bin/env bash
# ============================================================================
# pytest-kill-attribution.sh — a killed pytest leg must state WHEN it died, not
# only how long its budget was (#3719, #6145, #10688).
#
# WHY THIS EXISTS (measured, twice)
#   `.github/workflows/python-ci.yml` prints, for `rc ∈ {124, 137, 2}`:
#
#     ========== WATCHDOG: pytest killed after ${matrix.watchdog_minutes}m (…)
#
#   That sentence states the BUDGET as if it were the ELAPSED time, and it was
#   read as the cause by every triager that met it:
#
#   * #10917 / `test (f)`, run 41843678044 (2026-11-04): `rc=137`, step wall
#     **1215 s** against a **1620 s** budget — killed 405 s BEFORE the watchdog
#     could fire. Reported as "killed after 27m".
#   * #10914 / `test (a)`, run 41952788142 (2026-11-05): `rc=137` after **346 s**
#     of a **1560 s** budget, with the runner logging
#     `##[error]The runner has received a shutdown signal` immediately after.
#     Reported as "killed after 26m" — and that is the line the dispatch brief
#     quoted as the reason the lane existed.
#   * #10914 / `test-carve-out (b)`, same run: the carve-out shard printed a
#     COMPLETE `505 passed in 518.25s`, and the step still ended `rc=137`
#     (killed after the tests had finished). Reported as "killed after 19m".
#
#   In all three the budget was NOT the lever — one was an external SIGKILL
#   (runner shutdown or the OOM shape #10399), and one had already finished. The
#   triager's next action differs completely between the two cases: raise
#   `watchdog_minutes` vs. re-run the commit on a healthy runner.
#
# WHY THE EXIT CODE CANNOT ANSWER IT
#   GNU `timeout` returns 124 when the command exceeds its duration, and 137
#   (=128+SIGKILL) when the command — or `timeout` itself — is SIGKILLed
#   (coreutils manual, "timeout invocation"). Both `timeout -s INT -k 10`'s own
#   follow-up kill and an unrelated OOM/runner kill produce 137, so the code
#   does not separate them. The ELAPSED time does: a watchdog kill cannot land
#   before the deadline it implements. That value is already in the step
#   (`$SECONDS` taken just before the `timeout` call) — nothing has to be
#   inferred, and GitHub exposes no other in-step signal (the runner's own
#   `shutdown signal` line is written at job teardown, after this step).
#
# WHAT IT DOES NOT DO
#   It reports; it never decides. It prints one line to stdout and exits 0
#   unconditionally: the step's status stays pytest's own rc (the workflow's
#   `exit $rc`), so this can neither turn a red leg green nor a green leg red.
#   It does not touch `watchdog_minutes` — a budget is derived by
#   `tools/ci_timing.py` and `tools/ci_selection.shard_watchdog_minutes`
#   (#2818/#9003); a branch does not hand-pick it, and this script does not
#   propose a number.
#
# USAGE
#   pytest-kill-attribution.sh --rc <code> --budget-min <matrix.watchdog_minutes>
#                              [--elapsed <seconds>]
#     --elapsed   pytest's own wall, i.e. `$((SECONDS - PYTEST_T0))` where
#                 PYTEST_T0 was taken IMMEDIATELY before the `timeout` call.
#                 OMIT it (`--elapsed "${PYTEST_T0:+…}"`, which expands to the
#                 empty string when the start stamp is missing) rather than
#                 passing a fabricated 0: the script then says UNKNOWN instead of
#                 claiming the budget was reached.
#   Exit 0: reported (a line was printed, or the rc is not a kill rc).
#   Exit 2: bad invocation — INCLUDING a flag whose value is missing (`--rc` as the
#           last argv element). Each arm guards on the COUNT of remaining argv, not
#           on the value being non-empty: `--elapsed ""` is a legitimate call (the
#           absent start stamp, see above) and must still reach the UNKNOWN arm.
#           Without that guard `shift 2` fails on a one-element tail, shifts
#           nothing, and `while [ $# -gt 0 ]` never advances — measured at
#           `dbf7cf9d8`: `timeout 3 … --elapsed` exits 124, i.e. it spins for ever.
#           `set -uo pipefail` carries no `-e`, so the failed shift is silent.
# ============================================================================
set -uo pipefail

RC=""
ELAPSED=""
BUDGET_MIN=""

while [ $# -gt 0 ]; do
  case "$1" in
    --rc)
      [ $# -ge 2 ] || { echo "pytest-kill-attribution: --rc requires a value" >&2; exit 2; }
      RC="${2:-}"; shift 2 ;;
    --elapsed)
      [ $# -ge 2 ] || { echo "pytest-kill-attribution: --elapsed requires a value (pass an empty one for UNKNOWN)" >&2; exit 2; }
      ELAPSED="${2:-}"; shift 2 ;;
    --budget-min)
      [ $# -ge 2 ] || { echo "pytest-kill-attribution: --budget-min requires a value" >&2; exit 2; }
      BUDGET_MIN="${2:-}"; shift 2 ;;
    -h|--help)     sed -n '2,70p' "$0"; exit 0 ;;
    *)             echo "pytest-kill-attribution: unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$RC" ]; then
  echo "pytest-kill-attribution: --rc is required" >&2
  exit 2
fi
if [ -z "$BUDGET_MIN" ]; then
  echo "pytest-kill-attribution: --budget-min is required" >&2
  exit 2
fi
case "$RC" in
  ''|*[!0-9]*) echo "pytest-kill-attribution: --rc must be an integer (got '$RC')" >&2; exit 2 ;;
esac
case "$BUDGET_MIN" in
  ''|*[!0-9]*) echo "pytest-kill-attribution: --budget-min must be an integer (got '$BUDGET_MIN')" >&2; exit 2 ;;
esac

# Not a kill rc: the step ran to its own exit and owes no attribution. Stay
# silent — the workflow only calls this on the kill path, and a second opinion
# on a normal exit would be noise in a log that already says `pytest exit code: 0`.
if [ "$RC" -ne 124 ] && [ "$RC" -ne 137 ] && [ "$RC" -ne 2 ]; then
  exit 0
fi

BUDGET_S=$(( BUDGET_MIN * 60 ))

# Fail CLOSED on an unreadable elapsed: an empty/`0`-fabricated value would land
# in the "before the budget" arm and assert an external kill on no evidence.
case "$ELAPSED" in
  '')
    echo "(measured: elapsed UNKNOWN — no pytest start stamp, so where this kill landed relative to the ${BUDGET_MIN}m budget CANNOT be derived. The kill may or may not be the watchdog: attribute nothing from the banner above.)"
    exit 0
    ;;
  *[!0-9]*)
    echo "(measured: elapsed UNKNOWN (--elapsed '$ELAPSED' is not a whole number of seconds) — where this kill landed relative to the ${BUDGET_MIN}m budget CANNOT be derived.)"
    exit 0
    ;;
esac

if [ "$ELAPSED" -lt "$BUDGET_S" ]; then
  REMAINING=$(( BUDGET_S - ELAPSED ))
  echo "(measured: pytest died ${ELAPSED}s into a ${BUDGET_S}s budget — the kill landed ${REMAINING}s BEFORE the watchdog could fire, so the shard's budget is NOT the cause. The process was killed from OUTSIDE its budget: a runner shutdown/cancel, or the SIGKILL/OOM shape of #10399. Do not raise watchdog_minutes; the response is a re-run of this same commit on a runner that can hold it (#3719).)"
else
  echo "(measured: pytest ran ${ELAPSED}s against a ${BUDGET_S}s budget — the watchdog's own deadline was reached, so the budget (watchdog_minutes=${BUDGET_MIN}) is the lever, not the runner.)"
fi
exit 0
