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
#     measurement is broken. A `{reaped, exited, cleared, left, before,
#     own_left}` report is therefore accepted when the count/probe agreement
#     and the accounting identity hold, with `cleared` a diagnostic flag that
#     does not decide the verdict at any measured `COUNT` (#4989):
#       * the ATTRIBUTED count/probe agreement: `own_now <= own_left`.
#         `own_now` is how many of the workflow's counted servers live under
#         ONE OF THIS RUN'S session temp roots (read from their argv);
#         `own_left` is the same quantity measured by the sweep at its own
#         post-sweep seam. Same population, two instants — the only
#         count-based comparison this gate can make honestly (#1005).
#         `own_now > own_left` means a server under OUR root appeared AFTER the
#         sweep measured our population: a real leak, and it REDs.
#         The HOST-WIDE agreement (`COUNT <= left`) is a DIAGNOSTIC, not a
#         control: `COUNT` is read later from the runner shell and includes
#         every other process on the host, so any concurrent job (or, under
#         `pytest -n`, any sibling worker) can push it above `left` without
#         this run having leaked anything. That is measured, not theoretical:
#         two PASSING runs redded at 9-vs-8 (rc 0) and 22-vs-14. Counted
#         servers whose argv does NOT carry one of our roots are named in a
#         `::warning::` and never decide the verdict. A count BELOW the sweep's
#         `left` is the residual NOSAVE shutdown (the #1371 fast close is
#         fire-and-forget, so a server the sweep still saw exits in the ~0.05s
#         before the workflow's later probe) — a pass with the delta logged.
#       * `cleared` — whether the sweep FINISHED or ran out of its time budget.
#         `cleared: false` says the budget was exhausted, which is a function of
#         runner LOAD, not of the residue — so it does not decide the verdict at
#         any measured `COUNT` (#4989). When the positive controls hold, the run
#         is clean by measurement and the exhausted budget is a `::warning::`
#         diagnostic. It is a flag about the sweep's budget, not a residue of
#         its own, and not a red: the reds are the measurement-grounded ones
#         (an own-population growth, a broken accounting identity, a failed
#         probe at a non-zero attributed count, and an error/skipped/
#         unattributable report).
#       * the sweep's own ACCOUNTING IDENTITY holds:
#         `reaped + exited + left >= before`, where `before` is the sweep's
#         pre-sweep live count and `exited` (#6984) is the number of servers
#         the sweep OBSERVED to have exited on their own — a live `candidate`
#         at discovery whose pid was dead at kill time. A sweep that reaped
#         `reaped`, watched `exited` shut themselves down, and left `left`
#         must account for at least everything it started with; servers it
#         reaped that its own report no longer accounts for mean the SWEEP's
#         measurement is broken, not the count. `exited` is MEASURED at that
#         dead-pid branch, never derived as `before - reaped - left`: the
#         complement form makes the identity a tautology (it holds for EVERY
#         report), which would retire the guard while appearing to keep it.
#         Skipped only when `before` is `null` (the probe failed — a distinct
#         state) or `0`; a report that OMITS `before` or `exited` is unusable
#         → RED, so the
#         control can never be disabled by a report shape change.
#     One shape is non-gating for a DIFFERENT reason. A DEFERRED end-sweep
#     (`other_suites` non-empty) ran with `only_safe=True`, so `left` measures
#     the whole host's embedded servers and the mixed-population identity is
#     vacuous — the identity is SKIPPED there. The ATTRIBUTED comparison still
#     runs, because `own_left`/`own_now` are scoped to this run's own session
#     roots and are therefore sound even when the sweep deferred. The deferral
#     itself is a `::warning::` naming it: it is a designed state
#     (`only_safe=bool(others)`).
#     (The note that used to live here — "on CI the job runs on a fresh
#     `ubuntu-latest` VM, so `other_suites` is empty" — is FALSE: the test legs
#     run on the self-hosted pool. Measured 2026-10-28, `test (g)` reported
#     `other_suites=4`. The attributed path is what makes that safe.)
#
# VERDICT TABLE
#   A note on POPULATION first, because it is the whole point of #1005. Every
#   count-based red below is applied to servers ATTRIBUTED TO THIS RUN — those
#   whose argv carries a socket path under one of the run's own session temp
#   roots (`own_roots` in the report, `own_now` here). The workflow's `COUNT`
#   and the sweep's `left` are both HOST-WIDE pgrep readings, taken at
#   different moments by different processes, and under `pytest -n` by
#   different workers; on the self-hosted pool a dozen jobs share one process
#   table. `COUNT > left` therefore says only that SOMEONE started a server
#   after the sweep — not that this run leaked one — and redding it failed
#   green runs (9 counted vs left=8 with pytest rc 0; 22 vs 14). It is now a
#   `::warning::` that names the attribution; the verdict moved to
#   `own_now > own_left`, which compares the same population at two instants.
#
#   sweep.error               → RED, always: the hygiene path crashed, so the
#                               residue is unaccounted for.
#   sweep.skipped (any value
#     other than no-pytest)   → RED, always: the sweep never ran (reaper lock
#                               held), so nothing cleared the backlog.
#   sweep.skipped=no-pytest   → pytest never ran (the run step found an empty
#                               file selection, so the conftest sweep could not
#                               have written a report). This leg spawned NOTHING
#                               by construction, so no server can be its
#                               residue: a non-zero HOST-WIDE count is another
#                               process's, named in a `::warning::`, and the run
#                               PASSES. This is NOT a relaxation of `missing`: a
#                               real pytest run that produced no report is still
#                               `missing`/`unreadable` → RED.
#   sweep.no_embedded_servers → bound 0 for THIS RUN: its sweep probed the host
#                               and found nothing live, so nothing of ours may
#                               remain — RED if `own_now > 0`. A host-wide count
#                               of other processes' servers is a `::warning::`.
#   sweep.left == null        → the sweep's own probe FAILED, so the run
#                               produced no `left`. The ATTRIBUTED count
#                               decides: `own_now > 0` means servers under OUR
#                               roots that nothing bounded — RED
#                               (kill-downgradable). Otherwise a `::warning::`
#                               naming the failed sweep-side probe, and PASS.
#                               `cleared` is a confidence flag about a `left`
#                               this report does not carry, so it does not
#                               decide the verdict; the same rule applies at any
#                               `cleared`.
#   {reaped, exited, cleared,
#     left, before, own_left}  → the ATTRIBUTED bound `own_left` plus the
#                               positive controls above (the attributed
#                               count/probe agreement and the
#                               `reaped + exited + left >= before` accounting
#                               identity). `own_left: null` with `own_now > 0`
#                               is an unmeasured own residue → RED.
#                               A `cleared: false` report does not red at all:
#                               it is a `::warning::` diagnostic at ANY
#                               measured COUNT, because `cleared` reports
#                               whether the sweep's TIME BUDGET sufficed — a
#                               function of runner load — not its residue
#                               (#4989). The reds that remain are the
#                               measurement-grounded ones below.
#                               When the report carries non-empty
#                               `other_suites` the sweep DEFERRED: the identity
#                               is skipped (mixed population) and the deferral
#                               is a `::warning::`; the ATTRIBUTED comparison
#                               still runs and can still RED. An uncomparably
#                               large number (18+ digits, past bash's 64-bit
#                               integer range) is a usage error → exit 2, never
#                               a pass.
#   missing/unreadable report → RED: no USABLE measurement, so the count cannot
#                               be accounted for. Distinct from the residue reds
#                               below — neither the absence of a report nor an
#                               unparseable one is evidence of a residue. The red
#                               carries the pytest exit code (or says it is
#                               unknown) so a crashed run is distinguishable
#                               from a leak, and it names which subtype it is:
#                               `missing` (no report file) vs `unreadable` (a
#                               report exists but cannot be used) (#6852).
#   unattributable report     → RED: the report carries no usable `own_roots`
#                               key, so the counted servers cannot be attributed
#                               to this run and a residue could not be told from
#                               another process's. Fail-closed on the SHAPE: a
#                               producer change must not silently retire the
#                               leak check (same rule as a dropped `before`).
#
# WHAT THIS BOUND DOES NOT CATCH — do not read the table above as "a real leak
#   reds". The bound IS the sweep's own post-sweep measurement of OUR servers
#   (`own_left`), so it holds for any residue present at teardown, including a
#   residue the sweep MEASURES and DECLINES to act on: `reap()` skips a live co-tenant/foreign server, an unconfirmed
#   path-based server, and a client whose close the exit budget neutralised
#   (tortoise/embedded_reaper.py, tortoise/embedded_lifecycle.py).
#   That class lands in `left`/`own_left`, is reported with `cleared: true`, and
#   PASSES at `own_now == own_left` — this gate is bounded by that measurement
#   and does not independently red it. What the gate DOES red: a server under
#   OUR roots that appears AFTER the sweep measured ours
#   (`own_now > own_left`), an abort or failure that produced no usable report
#   (`error`, `skipped`), a `probe_failed` with a non-zero ATTRIBUTED count
#   (`own_now > 0`; there is no `own_left` to bind to), an identity violation
#   (`reaped + exited + left < before`), and an unaccounted/unreadable report.
#   `cleared: false` does NOT red at any count: it reports whether the sweep's
#   time budget sufficed, which is a function of
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
#   detector), as does a failed probe with a non-zero ATTRIBUTED count. A
#   budget-exhausted sweep is a `::warning::` at EVERY count (#4989), so it has
#   no red left to
#   downgrade. `error`, `skipped` and `unattributable` stay RED even under a
#   kill: those are unaccounted — or unmeasurable — no matter why pytest
#   stopped.
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
#                   [--argv <path-to-pid-argv-list>]
#   Exits 0 (pass, or a kill-aware warning), 1 (red), 2 (bad invocation).
#
#   `--argv` is the counted servers' argv, one `pid<space>args` line per line
#   (the workflow's `ps -ww -o pid=,args= -p $PIDS`). It is what lets the gate
#   say WHICH of the counted servers this run owns; without it the count is
#   unattributable and the host-wide comparison below can only warn. A missing
#   or unreadable file is not a usage error: see the note on that path.
# ============================================================================

set -uo pipefail

COUNT=""
RC=""
HYGIENE=""
ARGV=""
while [ $# -gt 0 ]; do
  _flag="$1"
  case "$_flag" in
    --count | --rc | --hygiene | --argv)
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
    --argv) ARGV="$2" ;;
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


def _own_now(roots, argv_path):
    """Count the servers that live under ONE OF THIS RUN S OWN roots.

    #1005 (epic #1647 E2E-7): `--count` is a HOST-WIDE pgrep, so it also counts
    servers that other processes started — a concurrent job on the same
    self-hosted runner, or a sibling `pytest -n` worker. The sweep s `left` is
    host-wide too, but it is a snapshot from inside one pytest process, taken
    before the counter runs; between the two, any other process can start a
    server and drive `COUNT` above `left`. That is not this run s leak, and
    redding it fails green runs (9-vs-8 and 22-vs-14 both came from it).

    The discriminator is the SOCKET PATH in each server s argv: a redislite
    server daemonizes (its parent is gone), but it is always started from a
    directory under the session temp root of the pytest run that spawned it, so
    its argv carries that root. `own_roots` is the root set of THIS run, so a
    server belongs to it iff its argv contains one of those roots. A server of
    another run lives under a different root and cannot be counted here.

    Returns None (never 0) when the attribution cannot be measured at all.
    """
    if not argv_path:
        return None
    try:
        with open(argv_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    prefixes = [root.rstrip("/") + "/" for root in roots]
    if not prefixes:
        return None
    own = 0
    for line in lines:
        parts = line.split(None, 1)
        # `ps -o pid=,args=` pads the pid with spaces; a row with no argv is a
        # pid that died between the count and the `ps` read, not a server.
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        args = parts[1]
        if any(prefix in args for prefix in prefixes):
            own += 1
    return own


def _own_now_str(roots, attributable, argv_path):
    if not attributable:
        return "null"
    value = _own_now(roots, argv_path)
    return "null" if value is None else str(value)


try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        doc = json.load(fh)
    argv_path = sys.argv[2] if len(sys.argv) > 2 else ""
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
# #1005: the run s OWN session roots — the attribution key. REQUIRED for every
# shape whose verdict reads the count: a report that dropped it would silently
# disable the leak check (every server would read as foreign), exactly like a
# dropped `before`/`exited`. Absent or unusable → `unattributable` → RED.
_roots = doc.get("own_roots") if isinstance(doc, dict) else None
roots = ([r for r in _roots if isinstance(r, str) and r.strip()]
         if isinstance(_roots, list) else [])
attributable = bool(roots)
rootlist = ",".join(roots) if roots else "-"

if not isinstance(sweep, dict):
    print("kind=unreadable")
elif "error" in sweep:
    print("kind=error")
elif sweep.get("skipped") == "no-pytest":
    # pytest never RAN, so this run cannot have spawned a server at all: the
    # host-wide count needs no attribution on this path, and a non-zero count
    # is other processes  (see the shell arm).
    print("kind=skipped_nopytest")
elif "skipped" in sweep:
    print("kind=skipped")
elif not attributable:
    # A shape whose verdict reads the count, without the key that attributes it.
    print("kind=unattributable")
elif sweep.get("no_embedded_servers") is True:
    print("kind=no_embedded_servers own_now=%s roots=%s"
          % (_own_now_str(roots, attributable, argv_path), rootlist))
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
        "kind=probe_failed cleared=%s own_now=%s roots=%s"
        % ("true" if sweep["cleared"] else "false",
           _own_now_str(roots, attributable, argv_path),
           rootlist)
    )
elif (
    isinstance(sweep.get("cleared"), bool)
    and _count(sweep.get("left"))
    and _count(sweep.get("reaped"))
    and _count(sweep.get("exited"))
    and "before" in sweep
    and (sweep["before"] is None or _count(sweep["before"]))
):
    left = sweep["left"]
    reaped = sweep["reaped"]
    exited = sweep["exited"]
    before = sweep["before"]
    # #1005: `own_left` is the run-scoped population the gate compares its own
    # attribution against. REQUIRED (an absent key is a shape change, not a
    # tolerance); `null` is a legitimate value — the attribution was attempted
    # and could not be measured — and is handled explicitly by the shell.
    own_left = sweep.get("own_left", "absent")
    own_left_ok = own_left is None or _count(own_left)
    # `before` is REQUIRED for the accounting identity: a report that dropped
    # it would silently disable the one control derived from the sweep itself,
    # so a malformed or older report is `unreadable` -> RED, never a skip.
    # #6984: `exited` is REQUIRED for the same reason — it is the identity
    # fourth term (a server that exited on its own). A report that omitted it
    # would silently restore the false red the term exists to remove, so an
    # older/malformed report is `unreadable` -> RED.
    if own_left == "absent" or not own_left_ok:
        print("kind=unreadable")
    elif max(left, reaped, exited,
             -1 if before is None else before,
             -1 if own_left is None else own_left) >= MAX_COMPARABLE:
        print("kind=oversized")
    else:
        print(
            "kind=report cleared=%s left=%d reaped=%d exited=%d "
            "before=%s others=%d foreign=%d own_left=%s own_now=%s roots=%s"
            % (
                "true" if sweep["cleared"] else "false",
                left,
                reaped,
                exited,
                "null" if before is None else str(before),
                others_n,
                foreign_n,
                "null" if own_left is None else str(own_left),
                _own_now_str(roots, attributable, argv_path),
                rootlist,
            )
        )
else:
    print("kind=unreadable")
' "$HYGIENE" "${ARGV:-}" 2>/dev/null) || HYGIENE_OUT="kind=unreadable"
fi

kind=""
cleared=""
left=""
reaped=""
exited=""
before=""
others=""
foreign=""
own_left=""
own_now=""
roots=""
for _tok in $HYGIENE_OUT; do
  case "$_tok" in
    kind=*) kind="${_tok#kind=}" ;;
    cleared=*) cleared="${_tok#cleared=}" ;;
    left=*) left="${_tok#left=}" ;;
    reaped=*) reaped="${_tok#reaped=}" ;;
    exited=*) exited="${_tok#exited=}" ;;
    before=*) before="${_tok#before=}" ;;
    others=*) others="${_tok#others=}" ;;
    foreign=*) foreign="${_tok#foreign=}" ;;
    own_left=*) own_left="${_tok#own_left=}" ;;
    own_now=*) own_now="${_tok#own_now=}" ;;
    roots=*) roots="${_tok#roots=}" ;;
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

foreign_diagnostic() { # <count> <left> <own_now> <own_left> <roots>
  # #1005: the counter's pgrep is HOST-WIDE. On the self-hosted pool many jobs
  # share one process table, and under `pytest -n` a run's own workers do too,
  # so a count above the sweep's snapshot is normal and says nothing about this
  # run. Say what the surplus IS (other processes) instead of redding a green
  # run on it. Never exits: a warning either way.
  local _count="$1" _left="$2" _own_now="$3" _own_left="$4" _roots="$5"
  if [ "$_own_now" = "null" ]; then
    echo "::warning::redislite orphan gate: $_count servers counted against the sweep's left=$_left, but the counter's attribution is UNAVAILABLE (no argv measurement) — a host-wide count cannot be attributed to this run, so it is a diagnostic and NOT a leak (issue #1005 / epic #1647 E2E-7)"
    return 0
  fi
  echo "::warning::redislite orphan gate: $_count servers counted against the sweep's left=$_left, but only $_own_now of them live under this run's own session roots (own_left=$_own_left, roots=$_roots) — the $((_count - _own_now)) surplus belongs to other processes sharing this host (concurrent jobs on the self-hosted pool, sibling pytest -n workers), which is NOT this run's residue; a host-wide count is not a bound for one run (issue #1005 / epic #1647 E2E-7)"
}

own_growth_check() { # <own_now> <own_left> <roots>
  # #1005: THE leak red. Both numbers are the same population — servers whose
  # argv carries one of THIS run's session roots — read at two instants: the
  # sweep's post-sweep reading (`own_left`) and the counter's later reading
  # (`own_now`). `own_now > own_left` means this run's own servers GREW after
  # the sweep measured them: a post-sweep spawn under our own temp root, which
  # nothing swept. That is a genuine leak, and it is the one direction the
  # count can prove. A server of another process can never enter `own_now` (its
  # socket lives under a different root), so this check cannot false-fire on a
  # shared host.
  local _own_now="$1" _own_left="$2" _roots="$3"
  [ "$_own_now" = "null" ] && return 0
  if [ "$_own_left" = "null" ]; then
    if [ "$_own_now" -gt 0 ]; then
      red_or_kill_warning "redislite orphan gate: $_own_now servers live under this run's OWN session roots but the sweep reported no attributable measurement (own_left=null) — an unaccounted residue of this run, not another process's (roots=$_roots)"
    fi
    return 0
  fi
  if [ "$_own_now" -gt "$_own_left" ]; then
    red_or_kill_warning "redislite orphan gate: this run's own servers grew after the sweep measured them — own_now=$_own_now own_left=$_own_left (roots=$_roots): a server under this run's session temp root that the sweep did not account for"
  fi
  return 0
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
    # no report could exist. This leg therefore spawned NOTHING — by
    # construction — so a non-zero HOST-WIDE count is another process's servers
    # (a concurrent job on the shared pool), which nothing here may red on
    # (#1005: it used to, and redded green empty-selection legs on the pool).
    if [ "$COUNT" -gt 0 ]; then
      echo "::warning::redislite orphan gate: pytest never ran for this selection (skipped=no-pytest) but $COUNT redislite servers are live on this host — this leg started none, so the count belongs to another process, not to a residue of this run (issue #1005 / epic #1647 E2E-7)"
    fi
    echo "pytest never ran for this selection (skipped=no-pytest); no redislite server was spawned, $COUNT remain after suite"
    exit 0
    ;;
  no_embedded_servers)
    # The sweep probed the host and found NOTHING live, so nothing of this run's
    # may remain. This bound is structural (zero), not a chosen tolerance — but
    # it is applied to the population this run OWNS: `own_now` counts only
    # servers whose argv carries one of this run's session roots, so a server
    # another job started after the sweep cannot be read as this run's leak
    # (#1005).
    if [ "$own_now" != "null" ] && [ "$own_now" -gt 0 ]; then
      # #10255 review B (P3): the message quotes the ATTRIBUTED count. The
      # condition was moved onto the attributed population but the sentence
      # still printed `$COUNT` — the host-wide figure — so on the shared host
      # this gate is written for, own_now=1 with COUNT=40 emitted a leak
      # report claiming 40 servers leaked, none of the surplus attributed to
      # this run. Every other arm in this file names own_now/own_left for
      # exactly that reason. `COUNT` stays in the line as an explicit
      # host-wide diagnostic so the reader can still see the difference.
      red_or_kill_warning "redislite server leak: $own_now server(s) under this run's own session temp roots (roots=$roots) after suite but the sweep reports no embedded server was ever running — COUNT=$COUNT counted host-wide, so the unattributed surplus belongs to other processes"
    fi
    if [ "$COUNT" -gt 0 ]; then
      foreign_diagnostic "$COUNT" "0" "$own_now" "0" "$roots"
    fi
    echo "no embedded redislite server was running; $COUNT remain after suite"
    exit 0
    ;;
  probe_failed)
    # The sweep's own count probe FAILED (`left: null`), so the run produced no
    # host-wide measurement to bind to. The ATTRIBUTED count decides (#1005):
    # servers under this run's own roots are a residue nothing bounded, so they
    # red (kill-downgradable per #1371); a host-wide count that no attribution
    # places under our roots is another process's and is diagnostic only.
    # `cleared` is a confidence flag about a `left` this report does not carry,
    # so it does not decide the verdict.
    if [ "$own_now" != "null" ] && [ "$own_now" -gt 0 ]; then
      # #10255 review B (P3), same class: these two carried the host-wide
      # `$COUNT` while their condition reads `own_now` — the leaked servers
      # this run actually owns.
      if [ "$cleared" = "false" ]; then
        red_or_kill_warning "redislite orphan gate: the hygiene end-sweep exhausted its time budget (cleared=false) AND its own count probe FAILED (left=null) — the $own_now server(s) under this run's own session roots (roots=$roots) are unmeasured, not a bounded outcome (issue #1005)"
      fi
      red_or_kill_warning "redislite orphan gate: the hygiene end-sweep's own count probe FAILED (left=null) — the run produced no measurement to bind the bound to, so the $own_now server(s) under this run's own session roots (roots=$roots) are unaccounted for (issue #1005)"
    fi
    if [ "$COUNT" -eq 0 ]; then
      echo "::warning::redislite orphan gate: the hygiene end-sweep's own count probe FAILED (left=null) but the workflow's probe measured 0 live servers — no residue exists to account for; the failed sweep-side probe is diagnostic only (issue #1005)"
    else
      foreign_diagnostic "$COUNT" "null" "$own_now" "null" "$roots"
    fi
    echo "nothing attributable to this run remains ($COUNT counted host-wide, none under this run's session roots); the sweep's own probe failed (left=null) — diagnostic only (issue #1005)"
    exit 0
    ;;
  unattributable)
    # The report is one whose verdict reads the count but which carries no
    # usable `own_roots` key, so the counted population cannot be attributed to
    # this run — and an unattributable disagreement is exactly what the gate is
    # forbidden to call a leak. Fail closed on the SHAPE (a report change must
    # not silently disable the leak check), never on the number.
    red "redislite orphan gate: the hygiene report carries no usable own_roots attribution key — the counted servers cannot be attributed to this run, so a residue could not be told from another process's (shape change: the attribution cannot be disabled, issue #1005 / epic #1647 E2E-7)"
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
      # The bound is enforced below — the ATTRIBUTED comparison
      # (`own_now` vs `own_left`) and the accounting identity — and both are
      # derived from the sweep's own measurement rather than from its budget, so
      # neither is weakened here. (`COUNT > left` is NOT one of them any more;
      # see the measured-verdict block at the end of this arm.)
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
    # including other suites' — not this suite's residue, and the mixed-
    # population accounting identity is vacuous. NO HOST-WIDE BOUND IS ENFORCED
    # on this path (nor on the gated one below: that comparison is a diagnostic
    # everywhere, see #1005). The ATTRIBUTED comparison runs on BOTH paths,
    # because `own_left`/`own_now` are scoped to this run's own session roots
    # and are therefore sound even when the sweep deferred. Warning, not RED:
    # the deferral is a designed state (`only_safe=bool(others)`).
    if [ "${others:-0}" -gt 0 ]; then
      echo "::warning::redislite orphan gate: the hygiene end-sweep deferred to last-suite-standing (other_suites=$others foreign_pids=$foreign) — left=$left counts other suites' live servers, so it is NOT an authoritative bound for this suite's residue; the HOST-WIDE comparison is diagnostic only on this path, while the ATTRIBUTED own_now <= own_left check is enforced (issue #4740 / #1005)"
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
    # A DEFERRED sweep mixes populations, so its identity proves nothing.
    if [ "${others:-0}" -eq 0 ] && [ "$before" != "null" ] && [ "$before" -gt 0 ]; then
      if [ "$((reaped + exited + left))" -lt "$before" ]; then
        red_or_kill_warning "redislite orphan gate: the sweep does not account for the servers it started with — before=$before, reaped=$reaped, exited=$exited, left=$left (reaped + exited + left < before); the sweep's own measurement is broken"
      fi
    fi
    # #1005 — THE MEASURED VERDICT. `own_now` and `own_left` are the same
    # population (this run's own servers, attributed by session root) at the two
    # instants the count can actually compare, so a growth between them is a
    # real leak and reds. This is the check `COUNT > left` was believed to be.
    own_growth_check "$own_now" "$own_left" "$roots"
    # `COUNT > left` is NOT a verdict any more. The counter is a HOST-WIDE
    # pgrep run later from the runner shell; `left` is a snapshot from inside
    # one pytest process. On a shared self-hosted runner (and under `pytest
    # -n`) any other process can start a server in between, so this direction
    # redded PASSING runs — 9 counted against left=8 (rc 0) and 22 against
    # left=14 (rc 1) — and the gate had no way to tell those servers from its
    # own. It is now the diagnostic that names the attribution.
    if [ "$COUNT" -gt "$left" ]; then
      foreign_diagnostic "$COUNT" "$left" "$own_now" "$own_left" "$roots"
    fi
    if [ "${others:-0}" -gt 0 ]; then
      echo "deferred sweep: $COUNT counted; the end-sweep deferred to last-suite-standing (other_suites=$others), so left=$left is not an authoritative bound for this suite's residue"
      exit 0
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
    # missing / unreadable: no report at all, so the count cannot be accounted
    # for. A watchdog kill skips the session-end finalizer entirely (#1371).
    if is_kill_rc; then
      echo "::warning::no redislite-hygiene end-sweep report ($kind) — rc=$RC: a watchdog kill skips the conftest end-sweep, so the count is a kill-path artifact, not a leak (issue #1371)"
      exit 0
    fi
    # Not a kill rc. There is no USABLE report, so there is no measurement to
    # account for the count with — which is NOT the same claim as "a residue was
    # observed". Say which, and carry the exit that produced it, so a reader
    # (and the landing rail) can tell a crashed run from a leak (#6852).
    #
    # The two subtypes differ and must not share one cause clause: `missing`
    # means no report file exists (the run may never have reached teardown),
    # while `unreadable` means the file EXISTS but is malformed or structurally
    # incomplete — asserting "without writing one" there would be false, since
    # one was written and the gate could not use it.
    case "$kind" in
      missing)
        if [ -n "${RC:-}" ]; then
          red "redislite orphan gate: no redislite-hygiene end-sweep report (missing) — the run exited rc=${RC} without writing one, so the $COUNT count is unaccounted for (issue #1005 / epic #1647 E2E-7)"
        else
          red "redislite orphan gate: no redislite-hygiene end-sweep report (missing) and the pytest rc is unknown (cancelled/killed?) — the $COUNT count is unaccounted for (issue #1005 / epic #1647 E2E-7)"
        fi
        ;;
      *)
        if [ -n "${RC:-}" ]; then
          red "redislite orphan gate: the redislite-hygiene end-sweep report is unreadable ($kind) — the run exited rc=${RC} but its report was unusable, so the $COUNT count is unaccounted for (issue #1005 / epic #1647 E2E-7)"
        else
          red "redislite orphan gate: the redislite-hygiene end-sweep report is unreadable ($kind) and the pytest rc is unknown (cancelled/killed?) — the report was unusable, so the $COUNT count is unaccounted for (issue #1005 / epic #1647 E2E-7)"
        fi
        ;;
    esac
    ;;
esac
