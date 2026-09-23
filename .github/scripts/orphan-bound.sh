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
#       * the workflow's `pgrep` COUNT equals the sweep's own `left` — the two
#         probes run the identical `pgrep -f redislite/bin/redis-server`, one
#         step apart, so a disagreement means the counter is measuring a
#         different population than this suite;
#       * `cleared == true` — the sweep FINISHED rather than running out of its
#         time budget. `cleared: false` means the residue is arbitrary, which is
#         the real "hygiene is broken" signal, and it reds whatever the count is.
#
# VERDICT TABLE
#   sweep.error               → RED, always: the hygiene path crashed, so the
#                               residue is unaccounted for.
#   sweep.skipped             → RED, always: the sweep never ran (reaper lock
#                               held), so nothing cleared the backlog.
#   sweep.no_embedded_servers → bound 0: nothing was spawned, so nothing may
#                               remain.
#   {reaped, cleared, left}   → bound `left`, plus the two positive controls.
#   missing/unreadable report → RED: the residue is unaccounted for.
#
# #1371 KILL-AWARE: after a WATCHDOG kill (pytest rc 124/137/2) the counted
#   servers are a GUARANTEED kill-path artifact — SIGKILL skips atexit and the
#   conftest end-sweep, so the finalizer that would have produced the report
#   never ran. A count ABOVE this gate's own bound therefore downgrades to a
#   `::warning::` (the run is already red and a second red only blinds the
#   detector). `cleared: false` and a count BELOW the sweep's own measurement
#   are not kill artifacts and stay RED.
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
elif "skipped" in sweep:
    print("kind=skipped")
elif sweep.get("no_embedded_servers") is True:
    print("kind=no_embedded_servers")
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
  no_embedded_servers)
    # Nothing was ever spawned, so nothing may remain. This bound is structural
    # (zero), not a chosen tolerance.
    if [ "$COUNT" -gt 0 ]; then
      red_or_kill_warning "redislite server leak: $COUNT servers after suite but the sweep reports no embedded server was ever running"
    fi
    echo "no embedded redislite server was running; $COUNT remain after suite"
    exit 0
    ;;
  report)
    if [ "$cleared" != "true" ]; then
      red "redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) — the $COUNT residue is arbitrary, not a bounded outcome (issue #1005)"
    fi
    if [ "$COUNT" != "$left" ]; then
      if [ "$COUNT" -gt "$left" ]; then
        red_or_kill_warning "redislite orphan gate: $COUNT servers counted but the sweep reported left=$left — the two probes must observe the same population"
      else
        red "redislite orphan gate: $COUNT servers counted but the sweep reported left=$left — the workflow counter is measuring a different population than the suite's own probe (issue #1005)"
      fi
    fi
    echo "orphaned redislite servers after suite: $COUNT (sweep left=$left, cleared=true) — within the sweep's own measurement"
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
