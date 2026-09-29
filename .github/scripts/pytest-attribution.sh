#!/usr/bin/env bash
# ============================================================================
# pytest-attribution.sh — make a failing pytest leg SELF-DESCRIBING (#3470).
#
# WHY THIS EXISTS
#   Every pytest leg in python-ci.yml reported its result with a BLIND
#   `tail -n N /tmp/pytest.log` (`tail -25 /tmp/trackb.log` for track-b). That
#   is a LINE-COUNT window, not a result. Any
#   burst of output written AFTER pytest's own summary — the redislite
#   interpreter-exit flood (`Exception ignored in: <function RedisMixin.__del__>`
#   → `AttributeError: 'Redis' object has no attribute 'connection_pool'`, ~14
#   lines per leaked server, observed at 18/88/178 occurrences) — fills the
#   window and pushes BOTH the session summary line and the `-r fEs` failure
#   report above the cut. The log then stops saying whether the leg finished at
#   all, and a reviewer cannot tell "failed" from "did not complete".
#
#   So the summary is read from the FULL log, pattern-anchored, which makes it
#   present whenever pytest produced one — whatever the tail happened to hold.
#
# WHY THE INCOMPLETE CASE NEEDS A MARKER (measured, not theorised)
#   When a leg ends WITHOUT a session summary, its job log carries neither a
#   `FAILED`/`ERROR` nodeid nor any annotation, so the #3467 merge bar's
#   extractor returns ZERO ids for that entire leg:
#
#       $ python3 scripts/ci_exemption.py ids --log capture.txt
#       ci-exemption: ids=0 unattributable=0 guard-steps=0
#
#   A leg that contributed NOTHING is indistinguishable from a leg that was
#   clean. `comm -23 pr-fails.txt main-fails.txt` over a set that silently
#   omitted a whole leg is UN-FALSIFIABLE rather than proven — the vacuous pass
#   #3467 exists to prevent, and the reason an omitted leg must be named.
#
#   The marker is emitted as a workflow-command ANNOTATION (`::error::`) rather
#   than prose BECAUSE of that measurement: `scripts/ci_exemption.py` classifies
#   an annotation as an attributable failure, so the unobservable leg becomes a
#   NAMED key in the failure set instead of a silent gap —
#
#       guard-step::<step>::test-leg-INCOMPLETE-no-pytest-summary-failure-set-unobservable
#
#   A `(no FAILED/ERROR nodeids)` prose line prints bytes in the log and
#   contributes nothing (that is the ids=0 above); only the annotation is read.
#
# WHAT IT DOES NOT DO
#   It reports; it never decides. The step's status stays pytest's own rc (the
#   workflow's `exit $rc`), so this cannot turn a red leg green or a green leg
#   red — an `::error::` annotation is a check-run annotation, and annotations
#   do not fail a step.
#
# USAGE
#   pytest-attribution.sh --log <path-to-pytest.log> [--rc <pytest-exit-code>]
#   Exit 0: reported (a session summary was found, or INCOMPLETE was named).
#   Exit 2: bad invocation.
# ============================================================================
set -uo pipefail

LOG=""
RC=""

while [ $# -gt 0 ]; do
  case "$1" in
    --log)
      LOG="${2:-}"
      [ -n "$LOG" ] || { echo "::error::pytest-attribution: --log requires a value"; exit 2; }
      shift 2
      ;;
    --rc)
      RC="${2:-}"
      [ -n "$RC" ] || { echo "::error::pytest-attribution: --rc requires a value"; exit 2; }
      shift 2
      ;;
    *)
      echo "::error::pytest-attribution: unknown argument '$1'"
      exit 2
      ;;
  esac
done

if [ -z "$LOG" ]; then
  echo "::error::pytest-attribution: --log <path> is required"
  exit 2
fi

# pytest's session summary line, as written by `TerminalReporter.write_sep`: a
# run of `=` banners the line on BOTH sides and it always closes with the
# duration. Anchoring on that banner is what keeps every other `==== … ====`
# header out of the match — `pytest summary (tail)`, `short test summary info`,
# `slowest N durations`, and the `pytest exit code` banner all carry no
# ` in <n>s `. The `--durations` block (`116.35s call     tests/…`) and the
# `WATCHDOG:` banner carry no leading banner / no duration respectively.
SUMMARY_RE='^=+ .* in [0-9]+(\.[0-9]+)?s( \([0-9]+:[0-9]{2}:[0-9]{2}\))?( =*)?$'

# pytest can colourise the summary (`FORCE_COLOR=1`, `PY_COLORS=1`,
# `--color=yes`). Today every leg redirects into a pipe, so colour is off and the
# anchor above is enough — but a colourised summary that failed to match would
# fire a FALSE INCOMPLETE on a healthy run (a spurious failure key, the inverse
# of the defect this file exists for). Strip SGR first, exactly as
# `scripts/ci_exemption.py` strips it from a `--log-failed` capture.
strip_sgr() { sed -E $'s/\x1b\\[[0-9;]*[A-Za-z]//g'; }

echo "==================== pytest session summary (full log) ===================="

if [ ! -f "$LOG" ]; then
  # No log at all: pytest never ran (a step that skips the run exits before it
  # reaches this call) or the file was lost. Either way there is no summary.
  echo "::error::test leg INCOMPLETE — no pytest summary; failure set unobservable"
  echo "(no log at $LOG — this leg produced no pytest log to attribute)"
  exit 0
fi

if SUMMARY="$(strip_sgr < "$LOG" | grep -E "$SUMMARY_RE" || true)" && [ -n "$SUMMARY" ]; then
  printf '%s\n' "$SUMMARY"
  exit 0
fi

# pytest RAN (the caller reaches this only after invoking it) and the FULL log
# carries no session summary: the interpreter never reached pytest's final
# report — a watchdog SIGKILL, a crash, or an aborted interpreter. The leg's
# failure set cannot be read, and saying so out loud is the whole point: the
# annotation is what turns an unobservable leg into a named failure instead of
# a leg the #3467 bar silently treats as clean.
echo "::error::test leg INCOMPLETE — no pytest summary; failure set unobservable"
echo "(the full log at $LOG carries no pytest session-summary line; pytest rc=${RC:-unknown})"
echo "(do NOT read the absence of FAILED lines from this leg as a pass — the failure set is unobservable)"
exit 0
