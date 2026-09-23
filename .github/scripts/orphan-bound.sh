#!/usr/bin/env bash
# ============================================================================
# orphan-bound.sh — the redislite orphan gate's verdict (#4740; epic #1647 E2E-7).
#
# WHY THIS IS A SCRIPT AND NOT INLINE WORKFLOW SHELL
#   The workflow assertion it replaces carried a hand-picked constant threshold
#   whose stated job was to "absorb" the embedded-file-contract atexit race.
#   That number is not derivable from anything the run knows: the component
#   that actually knows what it left behind is the session-end hygiene sweep in
#   `tests/conftest.py`, and the sweep's own report distinguishes a CLEARED
#   backlog from an EXHAUSTED budget. Its `left` field is the post-sweep
#   `redislite/bin/redis-server` population — the very number this gate counts —
#   so the bound is a measurement of the run, not a guess about it. A constant
#   below the residue a healthy suite leaves reds healthy runs; raising the
#   constant is the SAME defect with a bigger number. So the number is not a
#   constant at all: the sweep reports what it did, and this gate binds to it.
#
#   THE POSITIVE CONTROL
#     A bound of `left` could be satisfied by a leak that inflates its own bound.
#     A `{reaped, cleared, left}` report is therefore accepted only when BOTH
#     hold:
#       * the workflow's `pgrep` COUNT is at or below the sweep's own `left` —
#         the two probes run the identical `pgrep -f redislite/bin/redis-server`,
#         one step apart. COUNT ABOVE `left` means the counter is measuring a
#         population the sweep did not account for, which REDs. COUNT BELOW
#         `left` is the NORMAL atexit outcome: the sweep's `left` is read during
#         fixture teardown, and redislite's own `atexit` handler shuts servers
#         down (`redislite/client.py`) after pytest fully exits, so the
#         workflow's later probe may legitimately see fewer. A count BELOW the
#         sweep's measurement is therefore a PASS with the delta logged, not an
#         anomaly.
#       * `cleared == true` — the sweep FINISHED rather than running out of its
#         time budget. `cleared: false` means the residue is arbitrary, which is
#         the real "hygiene is broken" signal, and it reds whatever the count is
#         — except under a #1371 watchdog kill, where the run is already red and
#         a second red adds no signal.
#
# VERDICT TABLE
#   sweep.error               → RED, always: the hygiene path crashed, so the
#                               residue is unaccounted for.
#   sweep.skipped (any value
#     other than no-pytest)   → RED, always: the sweep never ran (reaper lock
#                               held), so nothing cleared the backlog.
#   sweep.skipped=no-pytest   → pytest never ran (the run step found an empty
#                               file selection, so the conftest sweep could not
#                               have written a report). PASS only when COUNT==0;
#                               a non-zero count means servers exist that
#                               nothing swept — RED. This is NOT a relaxation of
#                               `missing`: a real pytest run that produced no
#                               report is still `missing`/`unreadable` → RED.
#   sweep.no_embedded_servers → bound 0: nothing was spawned, so nothing may
#                               remain.
#   sweep.left == null        → RED (kill-downgradable): the sweep's own probe
#                               FAILED, so the run produced no `left` to bind
#                               to — an unmeasured residue, named as such rather
#                               than read as a plausible 0.
#   {reaped, cleared, left}   → bound `left`, plus the two positive controls.
#   missing/unreadable report → RED: the residue is unaccounted for.
#
# #1371 KILL-AWARE: after a WATCHDOG kill (pytest rc 124/137/2) the counted
#   servers are a GUARANTEED kill-path artifact — SIGKILL skips atexit and the
#   conftest end-sweep, so the finalizer that would have produced the report
#   never ran. A count ABOVE this gate's own bound therefore downgrades to a
#   `::warning::` (the run is already red and a second red only blinds the
#   detector), as does a budget-exhausted sweep and a failed probe. `error` and
#   `skipped` stay RED even under a kill: those are unaccounted no matter why
#   pytest stopped.
#
# NO LITERAL BOUND. Every number this gate compares against is read from the
#   hygiene report (`left`) or supplied as `--count`. The only numeric literals
#   are the process exit codes 0/1/2 and the #1371 pytest-kill rc set.
#
# USAGE
#   orphan-bound.sh --count <n> --rc <pytest-rc> --hygiene <path-to-json>
#   Exits 0 (pass, or a kill-aware warning), 1 (red), 2 (bad invocation).
# ============================================================================

set -uo pipefail

COUNT=""
RC=""
HYGIENE=""
while [ $# -gt 0 ]; do
  _flag="$1"
  case "$_flag" in
    --count | --rc | --hygiene)
      if [ $# -lt 2 ]; then
        echo "::error::orphan-bound: $_flag requires a value"
        exit 2
      fi
      ;;
    *)
      echo "::error::orphan-bound: unknown argument '$_flag'"
      exit 2
      ;;
  esac
  case "$_flag" in
    --count) COUNT="$2" ;;
    --rc) RC="$2" ;;
    --hygiene) HYGIENE="$2" ;;
  esac
  shift 2
done

# A non-numeric count cannot be meaningfully compared; fail loudly rather than
# fall through to `[`'s "integer expression expected" (which, with `set +e`,
# would read as an uncaught error and could invert the verdict).
case "$COUNT" in
  '' | *[!0-9]*)
    echo "::error::orphan-bound: --count must be a non-negative integer (got '$COUNT')"
    exit 2
    ;;
esac

# ── read the hygiene report ─────────────────────────────────────────────────
# The report is a JSON document written by the conftest session-end fixture.
# Its `sweep` field is the outcome this gate binds to. Parse with python3 (the
# same reader style as deploy-health-gate.sh); a field's VALUE is read, never
# its spelling. Anything that is not one of the recognised shapes is reported as
# `unreadable`, so the gate fails closed instead of guessing.
HYGIENE_OUT="kind=missing"
if [ -n "$HYGIENE" ] && [ -f "$HYGIENE" ]; then
  HYGIENE_OUT=$(python3 -c '
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        doc = json.load(fh)
except Exception:
    print("kind=unreadable")
    raise SystemExit(0)

sweep = doc.get("sweep") if isinstance(doc, dict) else None
if not isinstance(sweep, dict):
    print("kind=unreadable")
elif "error" in sweep:
    print("kind=error")
elif sweep.get("skipped") == "no-pytest":
    print("kind=skipped_nopytest")
elif "skipped" in sweep:
    print("kind=skipped")
elif sweep.get("no_embedded_servers") is True:
    print("kind=no_embedded_servers")
elif (
    isinstance(sweep.get("cleared"), bool)
    and "left" in sweep
    and sweep["left"] is None
):
    # The sweep ran but its own probe failed: an unmeasured residue, never a
    # plausible 0.
    print("kind=probe_failed")
elif (
    isinstance(sweep.get("cleared"), bool)
    and "left" in sweep
    and isinstance(sweep["left"], int)
    and not isinstance(sweep["left"], bool)
):
    print("kind=report cleared=%s left=%d"
          % ("true" if sweep["cleared"] else "false", sweep["left"]))
else:
    print("kind=unreadable")
' "$HYGIENE" 2>/dev/null) || HYGIENE_OUT="kind=unreadable"
fi

kind=""
cleared=""
left=""
for _tok in $HYGIENE_OUT; do
  case "$_tok" in
    kind=*) kind="${_tok#kind=}" ;;
    cleared=*) cleared="${_tok#cleared=}" ;;
    left=*) left="${_tok#left=}" ;;
  esac
done

is_kill_rc() {
  case "${RC:-}" in
    124 | 137 | 2) return 0 ;;
    *) return 1 ;;
  esac
}

red() { # <message>
  echo "::error::$1"
  exit 1
}

red_or_kill_warning() { # <message> — a count a watchdog kill explains
  # This function NEVER returns: a warning is a pass (exit 0, the run is already
  # red from the kill) and a non-kill observation is a red (exit 1). Returning
  # instead would let a caller fall through to its own pass line and turn a red
  # verdict GREEN — the fail-open this harness pins.
  if is_kill_rc; then
    echo "::warning::$1 — rc=$RC: a watchdog kill skips atexit and the conftest end-sweep, so the count is a kill-path artifact, not a leak (issue #1371)"
    exit 0
  fi
  if [ -z "${RC:-}" ]; then
    echo "::error::$1 — pytest rc unknown (cancelled/killed?) (issue #1005)"
  else
    echo "::error::$1 — pytest rc ${RC} (issue #1005 / epic #1647 E2E-7)"
  fi
  exit 1
}

case "$kind" in
  error)
    red "redislite orphan gate: the hygiene end-sweep reported an error — the residue is unaccounted for (issue #1005)"
    ;;
  skipped)
    red "redislite orphan gate: the hygiene end-sweep was skipped (reaper lock held) — nothing cleared the backlog (issue #1005)"
    ;;
  skipped_nopytest)
    # The run step found an empty file selection and wrote this report before
    # exiting: pytest never ran, so the conftest end-sweep never ran either and
    # no report could exist. Nothing was spawned by this leg, so a zero count is
    # the honest outcome; any non-zero count is servers nothing swept.
    if [ "$COUNT" -gt 0 ]; then
      red "redislite orphan gate: pytest never ran for this selection (skipped=no-pytest) but $COUNT redislite servers are live and nothing swept them (issue #1005)"
    fi
    echo "pytest never ran for this selection (skipped=no-pytest); no redislite server was spawned, $COUNT remain after suite"
    exit 0
    ;;
  no_embedded_servers)
    # Nothing was ever spawned, so nothing may remain. This bound is structural
    # (zero), not a chosen tolerance.
    if [ "$COUNT" -gt 0 ]; then
      red_or_kill_warning "redislite server leak: $COUNT servers after suite but the sweep reports no embedded server was ever running"
    fi
    echo "no embedded redislite server was running; $COUNT remain after suite"
    exit 0
    ;;
  probe_failed)
    red_or_kill_warning "redislite orphan gate: the hygiene end-sweep's own count probe FAILED (left=null) — the run produced no measurement to bind the bound to, so the $COUNT residue is unaccounted for (issue #1005)"
    ;;
  report)
    if [ "$cleared" != "true" ]; then
      # A budget-exhausted sweep is the real "hygiene is broken" signal and reds
      # whatever the count is. The single exception is a #1371 watchdog kill:
      # pytest DOES run session teardown on SIGINT, so a killed run can
      # legitimately exhaust the sweep budget — and the run is already red, so a
      # second red adds no signal.
      if is_kill_rc; then
        echo "::warning::redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) — rc=$RC: a watchdog kill can legitimately exhaust the sweep, so this is a kill-path artifact rather than a hygiene signal (issue #1371 / #1005)"
        exit 0
      fi
      red "redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) — the $COUNT residue is arbitrary, not a bounded outcome (issue #1005)"
    fi
    if [ "$COUNT" -gt "$left" ]; then
      red_or_kill_warning "redislite orphan gate: $COUNT servers counted but the sweep reported left=$left — the counter observes a population the sweep did not account for"
    fi
    # COUNT <= left: the workflow's later probe sees the same population or
    # fewer. Fewer is the documented atexit race (redislite shuts its last-client
    # servers down at interpreter exit, after the sweep's in-teardown reading),
    # so it is a pass — with the delta logged so it stays visible.
    if [ "$COUNT" -lt "$left" ]; then
      echo "orphaned redislite servers after suite: $COUNT (sweep left=$left, cleared=true; $((left - COUNT)) shut down at interpreter exit after the sweep's teardown reading) — within the sweep's own measurement"
    else
      echo "orphaned redislite servers after suite: $COUNT (sweep left=$left, cleared=true) — within the sweep's own measurement"
    fi
    exit 0
    ;;
  *)
    # missing / unreadable: no report at all, so the residue is unaccounted for.
    # A watchdog kill skips the session-end finalizer entirely (#1371).
    if is_kill_rc; then
      echo "::warning::no redislite-hygiene end-sweep report ($kind) — rc=$RC: a watchdog kill skips the conftest end-sweep, so the count is a kill-path artifact, not a leak (issue #1371)"
      exit 0
    fi
    red "redislite orphan gate: no usable redislite-hygiene end-sweep report ($kind) — the $COUNT residue is unaccounted for (issue #1005 / epic #1647 E2E-7)"
    ;;
esac
