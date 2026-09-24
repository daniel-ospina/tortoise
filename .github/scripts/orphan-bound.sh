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
#   THE POSITIVE CONTROLS
#     A bound of `left` could be satisfied by a leak that inflates its own bound,
#     and `left` is produced by the very same probe as the workflow's `COUNT`,
#     one step later — so neither alone can detect a sweep whose own
#     measurement is broken. A `{reaped, cleared, left, before}` report is
#     therefore accepted when the count/probe agreement and the accounting
#     identity hold, with `cleared` a diagnostic flag that does not decide the
#     verdict at any measured `COUNT` (#4989):
#       * the workflow's `pgrep` COUNT is at or below the sweep's own `left` —
#         the two probes run the identical `pgrep -f redislite/bin/redis-server`,
#         and conftest's session-end teardown now calls
#         `close_embedded_clients()` BEFORE its probe (#1005), so both read the
#         SAME seam: after this suite's own live clients were closed and their
#         pools disconnected. That producer-side ordering is what makes
#         `COUNT <= left` a same-seam comparison.
#         While the suite's own clients were still open, every server they held
#         read as a live-client server and `reap()` declined it, so `left`
#         counted the suite's own clients (147 in the post-#4927 CI sample)
#         while COUNT measured the post-exit residue (5) — a pair that
#         `COUNT <= left` could never fail on. COUNT ABOVE `left` means the
#         counter is measuring a population the sweep did not account for,
#         which REDs. COUNT BELOW `left` is the residual NOSAVE shutdown: the
#         #1371 fast close is fire-and-forget, so a server the sweep still saw
#         can exit in the ~0.05s before the workflow's later probe. A count
#         BELOW the sweep's measurement is therefore a PASS with the delta
#         logged, not an anomaly — the workflow can only observe FEWER servers
#         than the sweep, never more.
#       * `cleared` — whether the sweep FINISHED or ran out of its time budget.
#         `cleared: false` says the budget was exhausted, which is a function of
#         runner LOAD, not of the residue — so it does not decide the verdict at
#         any measured `COUNT` (#4989). When the positive controls hold, the run
#         is clean by measurement and the exhausted budget is a `::warning::`
#         diagnostic. It is a flag about the sweep's budget, not a residue of
#         its own, and not a red: the reds are the measurement-grounded ones
#         (COUNT above `left`, a broken accounting identity, a failed probe at a
#         non-zero count, and an error/skipped/unreadable report).
#       * the sweep's own ACCOUNTING IDENTITY holds: `reaped + left >= before`,
#         where `before` is the sweep's pre-sweep live count. A sweep that
#         reaped `reaped` and left `left` must account for at least everything
#         it started with; servers it reaped that its own report no longer
#         accounts for mean the SWEEP's measurement is broken, not the count.
#         Skipped only when `before` is `null` (the probe failed — a distinct
#         state) or `0`; a report that OMITS `before` is unusable → RED, so the
#         control can never be disabled by a report shape change.
#     One shape disables the bound ENTIRELY — it is NOT a gate. A DEFERRED
#     end-sweep (`other_suites` non-empty) ran with `only_safe=True`, so `left`
#     measures the whole host's embedded servers, not this suite's residue.
#     `left` is then NOT an authoritative bound and the mixed-population
#     identity is vacuous, so this path is a NON-GATING DIAGNOSTIC: NO BOUND
#     IS ENFORCED when the sweep deferred, because the measurement cannot
#     attribute the population (a residue of THIS suite is always `<= left`
#     and always passes). The gate still emits a `::warning::` naming the
#     deferral and keeps the `COUNT <= left` sanity check — the count must not
#     exceed even the inflated measurement — but nothing on this branch bounds
#     this suite. Warning, not RED: the deferral is a designed state
#     (`only_safe=bool(others)`) and a red would false-fire on every healthy
#     concurrent local run; on CI the job runs on a fresh `ubuntu-latest` VM,
#     so the documented report carries `other_suites: []` and takes the gated
#     path below. FOLLOW-UP: a self-hosted/shared runner where `other_suites`
#     can be non-empty needs a population-attributing measurement (per-suite
#     marker counts) before this path can be gated.
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
#   sweep.left == null        → the sweep's own probe FAILED, so the run
#                               produced no `left` to bind to. The measured
#                               `COUNT` decides: at zero the workflow's own
#                               identical pgrep measured nothing live, so there
#                               is no residue to account for — a
#                               `::warning::` naming the failed sweep-side
#                               probe, and PASS (an empty residue has nothing
#                               to bind). Above zero the residue is unmeasured
#                               and named as such rather than read as a
#                               plausible 0 — RED (kill-downgradable).
#                               `cleared` is a confidence flag about a `left`
#                               this report does not carry, so it does not
#                               decide the verdict; the same measured-zero rule
#                               applies at any `cleared`.
#   {reaped, cleared, left,
#     before}                  → bound `left`, plus the positive controls above
#                               (the count/probe agreement and the
#                               `reaped + left >= before` accounting identity).
#                               A `cleared: false` report does not red at all:
#                               it is a `::warning::` diagnostic at ANY
#                               measured COUNT, because `cleared` reports
#                               whether the sweep's TIME BUDGET sufficed — a
#                               function of runner load — not its residue
#                               (#4989). The reds that remain are the
#                               measurement-grounded ones below.
#                               When the report carries non-empty
#                               `other_suites` the sweep DEFERRED: that path
#                               is a NON-GATING DIAGNOSTIC (a `::warning::`
#                               plus the `COUNT <= left` sanity check, with
#                               NO bound enforced — see the note above). An
#                               uncomparably large number (18+ digits, past
#                               bash's 64-bit integer range) is a usage error →
#                               exit 2, never a pass.
#   missing/unreadable report → RED: the residue is unaccounted for.
#
# WHAT THIS BOUND DOES NOT CATCH — do not read the table above as "a real leak
#   reds". The bound IS the sweep's own post-sweep measurement (`left`), and
#   `COUNT` is the same `pgrep` pattern measured later, after interpreter exit
#   — a point at which atexit can only REMOVE servers. Since #1005 the suite's
#   own clients are closed BEFORE the sweep reads `left`, so `COUNT <= left`
#   now compares two post-close readings; but it still holds for any residue
#   present at teardown, including a residue the sweep MEASURES and DECLINES to
#   act on: `reap()` skips a live co-tenant/foreign server, an unconfirmed
#   path-based server, and a client whose close the exit budget neutralised
#   (tortoise/embedded_reaper.py, tortoise/embedded_lifecycle.py).
#   That class lands in `left`, is reported with `cleared: true`, and PASSES at
#   `COUNT == left` — this gate is bounded by that measurement and does not
#   independently red it. What the gate DOES red: a leak that appears AFTER the
#   sweep (`COUNT > left`), an abort or failure that produced no usable report
#   (`error`, `skipped`), a `probe_failed` at a non-zero count (no `left` to bind
#   to), an identity violation (`reaped + left < before`), and an
#   unaccounted/unreadable report. `cleared: false` does NOT red at any count:
#   it reports whether the sweep's time budget sufficed, which is a function of
#   runner load and unrelated to the residue, so redding it fires on healthy
#   loaded runs — a loaded runner reported `{reaped: 9, cleared: false, left:
#   159, before: 168}` at COUNT=13 with the identity and `COUNT <= left` both
#   holding, which is the #4740 false red returned (#4989). A `probe_failed`
#   report at a measured COUNT of zero is a `::warning::`, not a red: there is
#   no live residue to bound.
#   FOLLOW-UP: catching the declined class needs a measurement the sweep does
#   not yet produce — the count it examined and declined, with reasons — a
#   separate change, issue #4884. No hand-picked constant is reintroduced for
#   it: the old constant DID red this class, and that red was the false red
#   #4740 exists to remove (13-15 orphans is the end-sweep's documented
#   designed residue, per tests/conftest.py).
#
# #1371 KILL-AWARE: after a WATCHDOG kill (pytest rc 124/137/2) the counted
#   servers are a GUARANTEED kill-path artifact — SIGKILL skips atexit and the
#   conftest end-sweep, so the finalizer that would have produced the report
#   never ran. A count ABOVE this gate's own bound therefore downgrades to a
#   `::warning::` (the run is already red and a second red only blinds the
#   detector), as does a failed probe at a non-zero count. A budget-exhausted
#   sweep is a `::warning::` at EVERY count (#4989), so it has no red left to
#   downgrade. `error` and `skipped` stay RED even under a kill: those are
#   unaccounted no matter why pytest stopped.
#
# NO LITERAL BOUND. Every number this gate compares against is read from the
#   hygiene report (`left`) or supplied as `--count`. The only numeric literals
#   are the process exit codes 0/1/2 and the #1371 pytest-kill rc set. Because
#   bash's `[` returns 2 (not false) on a value outside its 64-bit integer
#   range — and a false branch would then reach the PASS line — both `--count`
#   and every number read from the report are also bounded in MAGNITUDE; an
#   oversized value exits 2 as a usage error rather than inverting the verdict.
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
# would read as an uncaught error and could invert the verdict). MAGNITUDE is
# guarded too: a value beyond bash's 64-bit integer range makes every `[`
# comparison itself return 2, and with the false branch taken the run would
# reach the PASS line — the same inversion by the other route (#4740 review 3).
case "$COUNT" in
  '' | *[!0-9]*)
    echo "::error::orphan-bound: --count must be a non-negative integer (got '$COUNT')"
    exit 2
    ;;
esac
# Strip leading zeros first: they inflate the digit count without changing the
# value and make bash arithmetic (`$((left - COUNT))`) read the number as octal.
_count="${COUNT#"${COUNT%%[!0]*}"}"
[ -n "$_count" ] || _count="0"
if [ "${#_count}" -ge 18 ]; then
  echo "::error::orphan-bound: --count is too large to compare safely (got '$COUNT')"
  exit 2
fi
COUNT="$_count"

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

# 18+ digits is past bash 64-bit integer range: a `[` comparison on it returns
# 2 (not false), and the false branch would then reach the PASS line. Bound the
# magnitude here so an uncomparably large number is a usage error, not a pass
# (#4740 review 3).
MAX_COMPARABLE = 10 ** 17


def _count(value):
    """A non-negative integer, excluding bool (JSON true/false are ints)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        doc = json.load(fh)
except Exception:
    print("kind=unreadable")
    raise SystemExit(0)

sweep = doc.get("sweep") if isinstance(doc, dict) else None
# #4740 review 4: the report carries the defer-to-last-suite-standing signal
# (`other_suites` / `foreign_pids`). When it is non-empty the sweep ran with
# only_safe=True and `left` counts the live servers of OTHER suites — an
# inflated figure that must not be adopted as an authoritative bound for the
# residue of this suite. The parser surfaces the counts; the shell decides
# the verdict.
_other = doc.get("other_suites") if isinstance(doc, dict) else None
_foreign = doc.get("foreign_pids") if isinstance(doc, dict) else None
others_n = len(_other) if isinstance(_other, list) else 0
foreign_n = len(_foreign) if isinstance(_foreign, list) else 0
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
    # plausible 0. The `cleared` value is carried too (#4740 review 5): a
    # failed probe from an EXHAUSTED budget (`cleared: false`) is a different
    # signal from a failed probe on a sweep that finished, and the shell must
    # be able to tell them apart.
    print(
        "kind=probe_failed cleared=%s"
        % ("true" if sweep["cleared"] else "false")
    )
elif (
    isinstance(sweep.get("cleared"), bool)
    and _count(sweep.get("left"))
    and _count(sweep.get("reaped"))
    and "before" in sweep
    and (sweep["before"] is None or _count(sweep["before"]))
):
    left = sweep["left"]
    reaped = sweep["reaped"]
    before = sweep["before"]
    # `before` is REQUIRED for the accounting identity: a report that dropped
    # it would silently disable the one control derived from the sweep itself,
    # so a malformed or older report is `unreadable` -> RED, never a skip.
    if max(left, reaped, -1 if before is None else before) >= MAX_COMPARABLE:
        print("kind=oversized")
    else:
        print(
            "kind=report cleared=%s left=%d reaped=%d before=%s others=%d foreign=%d"
            % (
                "true" if sweep["cleared"] else "false",
                left,
                reaped,
                "null" if before is None else str(before),
                others_n,
                foreign_n,
            )
        )
else:
    print("kind=unreadable")
' "$HYGIENE" 2>/dev/null) || HYGIENE_OUT="kind=unreadable"
fi

kind=""
cleared=""
left=""
reaped=""
before=""
others=""
foreign=""
for _tok in $HYGIENE_OUT; do
  case "$_tok" in
    kind=*) kind="${_tok#kind=}" ;;
    cleared=*) cleared="${_tok#cleared=}" ;;
    left=*) left="${_tok#left=}" ;;
    reaped=*) reaped="${_tok#reaped=}" ;;
    before=*) before="${_tok#before=}" ;;
    others=*) others="${_tok#others=}" ;;
    foreign=*) foreign="${_tok#foreign=}" ;;
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
    # The sweep's own count probe FAILED (`left: null`), so the run produced no
    # `left` to bind to. The measured COUNT decides the verdict (#4740): at
    # zero the workflow's own identical pgrep measured nothing live, so there
    # is no residue to account for and the failed sweep-side probe is a
    # `::warning::` diagnostic; above zero the residue is unmeasured and
    # unaccounted for, so this reds (kill-downgradable per #1371). This is the
    # same measured-zero rule the `{reaped, cleared, left, before}` branch
    # applies: `cleared` is a confidence flag about a `left` this report does
    # not carry, so it does not decide the verdict.
    if [ "$COUNT" -eq 0 ]; then
      echo "::warning::redislite orphan gate: the hygiene end-sweep's own count probe FAILED (left=null) but the workflow's probe measured 0 live servers — no residue exists to account for; the failed sweep-side probe is diagnostic only (issue #1005)"
      exit 0
    fi
    if [ "$cleared" = "false" ]; then
      red_or_kill_warning "redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) AND its own count probe FAILED (left=null) — the $COUNT servers that remain are unmeasured, not a bounded outcome (issue #1005)"
    fi
    red_or_kill_warning "redislite orphan gate: the hygiene end-sweep's own count probe FAILED (left=null) — the run produced no measurement to bind the bound to, so the $COUNT residue is unaccounted for (issue #1005)"
    ;;
  oversized)
    # The report carries a number bash cannot compare. Reading it would make
    # `[` return 2, and under the false branch the run would reach the PASS
    # line — so this is a usage error, not a verdict (#4740 review 3).
    echo "::error::orphan-bound: the hygiene report carries a count too large to compare safely (issue #4740)"
    exit 2
    ;;
  report)
    if [ "$cleared" != "true" ]; then
      # `cleared` is the sweep's confidence that its `left` is a trustworthy
      # BOUND, not a residue of its own: it says whether the reap loop's TIME
      # BUDGET sufficed, which is a property of the runner's load, not of the
      # residue. It is therefore diagnostic at EVERY measured count (#4989).
      # The bound is enforced below — `COUNT > left` and the accounting
      # identity — and both are derived from the sweep's own measurement rather
      # than from its budget, so neither is weakened here.
      #
      # This arm previously red at COUNT > 0, which reintroduced the false red
      # #4740 removed. A loaded run reported
      # `{reaped: 9, cleared: false, left: 159, before: 168}` with COUNT=13:
      # the identity held (9 + 159 == 168) and COUNT <= left held, so this was
      # the ONLY firing arm, on a count inside the documented normal band
      # (tests/conftest.py: "observed as 13 orphans on CI"). The same rule
      # passed at COUNT == 0 on the #4740 verification run (before=14), which
      # is what hid it. A gate that reds at the KNOWN-NORMAL residue is the
      # same defect class as one that reds at zero.
      echo "::warning::redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) with COUNT=$COUNT against left=$left — the exhausted budget is diagnostic; the residue is bounded by left, not by the sweep's budget (issue #1005 / #4989)"
    fi
    # #4740 review 5: a DEFERRED sweep (other_suites non-empty) ran with
    # only_safe=True, so `left` measures the whole host's embedded servers —
    # including other suites' — not this suite's residue. This branch is a
    # NON-GATING DIAGNOSTIC: NO BOUND IS ENFORCED when the sweep deferred,
    # because the measurement cannot attribute the population (a residue of
    # THIS suite is always <= left and always passes). The warning and the
    # `COUNT <= left` sanity check are kept — the count must not exceed even
    # this inflated measurement — but nothing here bounds this suite. Warning,
    # not RED: the deferral is a designed state (`only_safe=bool(others)`),
    # while a red would false-fire on every healthy concurrent local run; on CI
    # the job runs on a fresh `ubuntu-latest` VM, so the documented report
    # carries `other_suites: []` and takes the gated path below. FOLLOW-UP: a
    # self-hosted/shared runner where `other_suites` can be non-empty needs a
    # population-attributing measurement (per-suite marker counts) before this
    # path can be gated.
    if [ "${others:-0}" -gt 0 ]; then
      echo "::warning::redislite orphan gate: the hygiene end-sweep deferred to last-suite-standing (other_suites=$others foreign_pids=$foreign) — left=$left counts other suites' live servers, so it is NOT an authoritative bound for this suite's residue and NO bound is enforced on this path; only the COUNT <= left sanity check is applied (non-gating diagnostic, issue #4740)"
      if [ "$COUNT" -gt "$left" ]; then
        red_or_kill_warning "redislite orphan gate: $COUNT servers counted exceed even the deferred sweep's left=$left (other_suites=$others live) — the counter observes a population the sweep did not account for"
      fi
      echo "deferred sweep: $COUNT counted; the end-sweep deferred to last-suite-standing (other_suites=$others), so left=$left is not an authoritative bound for this suite's residue"
      exit 0
    fi
    # The sweep's own ACCOUNTING IDENTITY: a sweep that reaped `reaped` and
    # left `left` must account for at least everything it started with. `left`
    # is produced by the same probe as the workflow's COUNT one step later, so
    # on its own it cannot detect a sweep whose own measurement is broken; the
    # identity is the control derived from the sweep's own work (#4740 review
    # 3). Skipped ONLY when the pre-sweep probe failed (`before` is null) or
    # measured zero — a report that OMITS `before` is `unreadable`, so the
    # control cannot be silently disabled. Both operands are canonical decimal
    # (the parser prints `%d`), so the arithmetic cannot be read as octal.
    if [ "$before" != "null" ] && [ "$before" -gt 0 ]; then
      if [ "$((reaped + left))" -lt "$before" ]; then
        red_or_kill_warning "redislite orphan gate: the sweep does not account for the servers it started with — before=$before, reaped=$reaped, left=$left (reaped + left < before); the sweep's own measurement is broken"
      fi
    fi
    if [ "$COUNT" -gt "$left" ]; then
      red_or_kill_warning "redislite orphan gate: $COUNT servers counted but the sweep reported left=$left — the counter observes a population the sweep did not account for"
    fi
    # COUNT <= left: the workflow's later probe sees the same population or
    # fewer. Since #1005 the sweep's `left` is a post-in-process-close reading,
    # so the delta is the fire-and-forget NOSAVE window (a server the sweep
    # still saw exits in ~0.05s), not an interpreter-exit race — a pass, with
    # the delta logged so it stays visible.
    if [ "$COUNT" -lt "$left" ]; then
      echo "orphaned redislite servers after suite: $COUNT (sweep before=$before left=$left reaped=$reaped, cleared=$cleared; $((left - COUNT)) shut down after the sweep's post-close reading (fire-and-forget NOSAVE)) — within the sweep's own measurement"
    else
      echo "orphaned redislite servers after suite: $COUNT (sweep before=$before left=$left reaped=$reaped, cleared=$cleared) — within the sweep's own measurement"
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
