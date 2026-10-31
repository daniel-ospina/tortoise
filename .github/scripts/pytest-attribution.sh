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

# ── the summary MUST be THIS leg's session, not an earlier/nested one ────────
#
# A SECOND pytest session writes its own banner and summary into this same file, and
# the measured `test-slow (a)` artifact carries one. Two mechanisms produce that, and
# the artifact shows the first: the job runs on a self-hosted pool whose runners share
# ONE `/tmp` (the embedded session's `-- /home/…/actions-runner-6/…` sits in the log of
# the job that ran on agent 2), so another leg's `> /tmp/pytest.log` truncates and
# interleaves this one; a test that shells out to pytest does the same. Taking any
# match as the leg's verdict is a FALSE CLEAN, and BOTH measured shapes are reachable:
#
#   * the nested summary is followed by more of the leg's own progress lines —
#     the 2026-10-19 `test-slow (a)` artifact's only summary ("3 passed in
#     51.08s", line 25) is followed by 89 start lines of the KILLED session; and
#   * the foreign summary is the LAST thing in the log, because the kill landed
#     after that writer finished AND its `>` redirect had already truncated the
#     leg's own header away (`head -27` of that same artifact reproduces it, with a
#     33 KB NUL hole). No POSITIONAL rule can see this one: there is no progress
#     line after the summary and no second banner, because the file was rewritten
#     underneath the leg.
#
# ⚠️ NAMED RESIDUAL, and the real fix FOR THE LEGS NAMED BELOW: this reporter
# cannot always tell one session's output from another's while a leg writes a
# HOST-GLOBAL path (`/tmp/*.log`) — the rc file next to it already moved to
# `${RUNNER_TEMP:-/tmp}/pytest-rc`. A foreign writer that rewrites the file and stops
# leaves no NUL hole and no unbalanced stack, so under rc=1 it can still read as the
# leg's verdict. For those legs the durable fix is a per-leg log path under
# `${RUNNER_TEMP}` (#10280); all three matrix jobs now pass one, so for them the
# signals below are a backstop rather than the load-bearing defence.
#
# ⚠️ STATUS: APPLIED TO EVERY `python-ci.yml` LEG THAT WRITES A `> ...` CAPTURE
# (2026-10-29 #10028 for `test`/`test-slow`; 2026-10-30 #10280 for the rest).
# `python-ci.yml`'s `test` job — the fast matrix, NINE legs, and the largest share of
# this failure — passes `--log "$PYTEST_LOG"` (= `${{ runner.temp }}/pytest.log`,
# per-runner and therefore per-job, since a runner runs one job at a time), and
# `test-slow`'s FIVE legs pass it too. That second half was a measured false red, not
# a tidy-up: run 40808264676's `test-slow (a)` printed its own `365 passed, 2 skipped`
# (= its manifest's 367 expected nodeids) AND its own `pytest exit code: 0` banner, and
# the job still concluded `failure`, because a sibling's `> /tmp/pytest.log` truncated
# the file between the leg's `tail` and this reporter's read. For those jobs signals
# (1)-(3) are a backstop against a test that shells out to pytest, not the load-bearing
# defence against a co-tenant.
#
# #10280 then migrated the three legs that still wrote a `>` capture — measured, not
# prophylactic: on run 40990582554 `test-track-b` wrote `4 expected nodeids ->
# /tmp/expected-nodeids.txt` and then reconciled its own 4 testcases against a
# `test-carve-out (c)` leg's register (`emit-manifest: 616 expected nodeids ->` the same
# shared path, 11s earlier), concluding `616 expected nodeid(s) absent from the
# junitxml` with every test in the leg passing — a FALSE RED on a required check, and
# the mirror image of the false PASS the shared manifest makes reachable. Those three
# jobs (`test-concurrency-falkor`, `test-track-b`, `test-carve-out`) now pass per-job
# `"$PYTEST_LOG"` / `"$PYTEST_JUNIT"` / `"$PYTEST_MANIFEST"` paths, so for them too
# these signals are a backstop.
#
# The legs that still write `/tmp/...` literals, and still need the same change, are
# the deliberate residual named on #10280, which carries the rest of the migration:
#
#   `validate`                  (`/tmp/pmv-pytest.log`)  — `post-merge-validation.yml`
#
# `validate` is residual for its LITERAL, not its redirect, and the distinction matters
# (PR #10264 review, verified against `capture_site`):
#
#   `validate` WRITES its log with `> /tmp/pmv-pytest.log 2>&1` and is COUNTED at the
#   separate `tail -n 300 /tmp/pmv-pytest.log` site, whose branch
#   (`^tail\s+-n?\s*\d+\s+(\S+)$`) accepts ANY single token — so migrating it would be
#   counted without a widening. `capture_site` has NO `>` branch at all: the `>` write is
#   not a shape it reads, which is why this leg is residual for its LITERAL, not its
#   redirect.
#
# The OTHER leg #10264 named in this paragraph captures through `| tee` rather than a
# tail, and it is MIGRATED (#7815, PR #10143) — so it is NOT residual and is deliberately
# not named here. The counter widening it owed is recorded BELOW the delimiter that ends
# this list, because naming a migrated leg inside the window is itself a red.
#
# ⛔ THIS LIST IS DERIVED, NOT REMEMBERED — do not edit it from memory. It is
# `tests/test_pytest_attribution.py::test_the_residual_header_is_scoped_to_the_legs_that_still_are_residuals`,
# which reads every capture site in both workflows this guard names and reds unless the
# paragraph above names EXACTLY the legs still capturing a host-global `/tmp/*.log`.
# Migrating one leg (or adding a leg) is therefore a deliberate edit HERE, in the same
# change — and naming a leg that HAS migrated is a red too, because an over-wide residual
# sends the next lane to migrate work that is already done.
#
# `validate` is the LAST one: it is the warn-only job, so for it the consequence is a
# misleading artifact rather than a false red on a required check — named anyway, so the
# residual reads as one scope instead of a list that stops where the gate impact does.
#
# ⚠️ THE FOUR `python-ci.yml` LEGS THIS LIST USED TO NAME — `test-concurrency-falkor`,
# `test-track-b`, `test-carve-out` and `test-d14-hosted-api` — are MIGRATED (#7815, PR
# #10143), which is why `test-track-b` is no longer a residual. They are migrated in TWO
# spellings, and the difference is deliberate: #10280 moved the first three onto
# `"$PYTEST_LOG"` / `"$PYTEST_JUNIT"` / `"$PYTEST_MANIFEST"`, resolved once in the job's
# first step and failing CLOSED on `${RUNNER_TEMP:?}`, while `test-d14-hosted-api` keeps
# this PR's per-step `${RUNNER_TEMP:-/tmp}/...`. Both are per-runner (a runner runs one
# job at a time); neither writes a host-global `/tmp` artifact.
#
# `test-track-b` was the sharpest of the five and #10280's own
# ROOT measurement — its shared `/tmp/expected-nodeids.txt` + `/tmp/junit.xml` produced
# `4 expected nodeids -> 640 absent`, a false red on a required check — and that is
# exactly what the per-job paths closed.
#
# `test-d14-hosted-api` owed the counter widening #10264 predicted, because it captures
# through `| tee` rather than a tail: the `tee` branch matched a `/tmp/...` LITERAL only,
# so a migrated `tee` would have gone UNCOUNTED and the leg's reporter obligation would
# have stopped being checked at all. That widening is delivered in
# `tests/test_pytest_attribution.py`'s `_TEE_SITE_RE`, which now accepts the
# `"${RUNNER_TEMP:-/tmp}/<name>.log"` spelling as well — a guard whose subject is this
# drift must not be spelling-dependent.
#
# For the legs still named above the three signals below stay load-bearing, and they keep
# the KILL path — the one that produced the "timeout flakes" — from being fooled.
#
# So ownership is "PROVEN, or treated as STALE", on three independent signals:
#
#   (1) SESSION STACK: every `test session starts` banner opens a session and
#       every summary closes one. The stack must be EMPTY at EOF — i.e. the
#       summary that ended the file also closed the OUTERMOST session.
#   (2) POSITION: no test START line may follow the summary (a session that
#       completed prints its summary last).
#   (3) NO TRUNCATION: a log with NUL bytes was rewritten mid-session by a second
#       writer (the measured truncation signature), so nothing in it is provable.
#
# Any failure of (1)-(3) means the leg has NO verdict of its own, so the STALE
# label and the INCOMPLETE marker must still fire. The asymmetry is deliberate: a
# redundant marker costs one "unobservable" key in the #3467 set, while a false
# clean silently omits a whole leg from that comparison — the vacuous pass this
# file exists to prevent.
#
# CRLF is normalised alongside the NUL strip so that all four pipelines here agree
# about the same bytes; a stray `\r` would otherwise defeat the `$` anchors in
# SUMMARY_RE and the banner match.
SESSIONS="$(strip_sgr < "$LOG" | tr -d '\r\000' | SUMMARY_RE="$SUMMARY_RE" awk '
  BEGIN { depth = 0; summaries = 0 }
  $0 ~ /^=+ test session starts =+$/ { depth++; next }
  $0 ~ ENVIRON["SUMMARY_RE"] { summaries++; if (depth > 0) depth--; next }
  END { printf "%d %d\n", depth, summaries }
')"
SESSION_DEPTH="${SESSIONS%% *}"
SUMMARY_COUNT="${SESSIONS##* }"
SUMMARY_TEXT="$(strip_sgr < "$LOG" | tr -d '\r\000' | grep -aE "$SUMMARY_RE" | tail -n 1 || true)"
SUMM_LINE="$(strip_sgr < "$LOG" | tr -d '\r\000' | grep -anE "$SUMMARY_RE" | tail -n 1 | cut -d: -f1 || true)"
START_LINE="$(strip_sgr < "$LOG" | tr -d '\r\000' | grep -anE '^[^[:space:]]+::' | tail -n 1 | cut -d: -f1 || true)"
# A NUL byte anywhere in the file is the truncation signature: the leg's own
# session header may be gone, and every line number above then refers to a file a
# second writer replaced mid-run.
# A truncating second writer leaves a HOLE — the measured one is 33 KB of contiguous
# NULs — while a test that writes a single NUL byte to stdout leaves a stray one. So
# count the LONGEST RUN OF NUL BYTES and require at least 1 KiB. The mapping matters:
# every NON-NUL byte -> \001 and NUL -> newline, so only real NULs create a run of
# empty lines. Both easier mappings are wrong: `tr -c '\000' '\n'` turns every text
# byte into a newline (a NUL-free log becomes one giant run), and mapping NUL -> \n
# alone counts 1024 consecutive BLANK LINES as a hole — either way a completed green
# leg is reported INCOMPLETE, the inverse of the marker's purpose.
TRUNCATED=0
LONGEST_NUL_RUN="$(tr -c '\000' '\001' < "$LOG" | tr '\000' '\n' | awk 'length($0) == 0 { r++; if (r > max) max = r; next } { r = 0 } END { print max + 0 }')"
if [ "${LONGEST_NUL_RUN:-0}" -ge 1024 ]; then
  TRUNCATED=1
fi
HAVE_SUMMARY=0
OWNED=0
if [ -n "$SUMMARY_TEXT" ] && [ "${SUMMARY_COUNT:-0}" -gt 0 ] && [ "${SESSION_DEPTH:-1}" -eq 0 ] && [ "$TRUNCATED" = "0" ]; then
  # (1) closed the outermost session, (3) the file was never rewritten...
  if [ -z "$START_LINE" ] || { [ -n "$SUMM_LINE" ] && [ "$SUMM_LINE" -gt "$START_LINE" ]; }; then
    # ...and (2) nothing of this leg ran after it.
    OWNED=1
  fi
fi
if [ "$OWNED" = "1" ]; then
  printf '%s\n' "$SUMMARY_TEXT"
  HAVE_SUMMARY=1
elif [ -n "$SUMMARY_TEXT" ]; then
  # Named, not swallowed: a reader who greps this log WILL find a summary, and has
  # to be told why it is not this leg's verdict — otherwise the marker below reads
  # as a reporter bug.
  echo "==================== STALE session summary (NOT this leg's verdict) ===================="
  printf '%s\n' "$SUMMARY_TEXT"
  echo "(ownership unprovable: it belongs to a nested pytest run, or to a session whose"
  echo " header a second writer truncated away — the stack is not closed at EOF, a test"
  echo " START line follows it, or the log carries a NUL HOLE from a truncating writer."
  echo " The leg has no verdict.)"
fi

# The marker's ONE claim is "the failure set is unobservable". When the log carries
# FAILED/ERROR nodeids it IS observable — the rail reads them — so the marker would
# add a redundant key to a leg that is already attributable (measured on a
# collection error, where `ERROR tests/x.py` is the record and nothing else is).
# rc=2 is BOTH pytest's INTERRUPTED code and its COLLECTION-ERROR code, so the
# distinction is read from the LOG, not from rc: a collection error prints
# `Interrupted: N error(s) during collection` AND its own bannered summary
# (`=== 1 error in 0.09s ===`, measured on pytest 9.1.1 without `-q`), and its
# `ERROR <path>` record IS the attributable failure set (the rail reads it), so the
# marker would add a false second key to an already-attributable leg.
COLLECTION_ERROR=0
if strip_sgr < "$LOG" | tr -d '\r\000' | grep -aE 'Interrupted: [0-9]+ error' > /dev/null; then
  COLLECTION_ERROR=1
fi
OBSERVABLE=0
if strip_sgr < "$LOG" | tr -d '\r\000' | grep -aE '^(FAILED|ERROR) ' > /dev/null; then
  OBSERVABLE=1
fi
# A genuine collection error's OWN summary reports it (`= 1 error in 0.09s =`), which
# is what separates it from a SIGINT: a nested child's collection error can sit in the
# same log while the LEG's summary reports tests. Only the former is a completed leg.
COLLECTION_VERDICT=0
if [ "$COLLECTION_ERROR" = "1" ] && [ -n "$SUMMARY_TEXT" ] \
   && grep -qE '^[= ]*[0-9]+ error' <<< "$SUMMARY_TEXT"; then
  COLLECTION_VERDICT=1
fi

# WHICH TEST THE LEG WAS RUNNING WHEN IT DIED.
#
# WHY (measured 2026-10-19): three of the sixteen failing suite jobs on main that
# day were KILLS, not test failures, and none of them named a test. Shard
# `test (a)` froze at "2397 passed" for three consecutive 5-minute heartbeats and
# was SIGKILLed at its 20m watchdog (rc=137); the only record of the culprit was a
# faulthandler dump inside a 300-line tail that had to be read by hand to learn it
# was `test_concurrent_recovery_mints_stay_at_cap`. That is how the cross-PR
# triage filed kills as "timeout flakes" and re-ran them: a kill that names
# nothing is indistinguishable from a flake.
#
# The kill signature used here is EVIDENCE, not the exit code: a test that printed
# a start line and never printed a result. That matters because rc=2 is NOT only
# "pytest's own SIGINT" — it is also pytest's COLLECTION-ERROR code (measured:
# `Interrupted: 1 error during collection` exits 2), and such a leg never started
# a test at all. Keying this on rc alone would print kill prose over a collection
# error; keying it on the log tells the two apart.
#
# The THREE shapes of a progress line, each verified by execution rather than
# assumed:
#
#   (a) `-v`:          `<nodeid> PASSED [ 42%]`   — result appended to the SAME line
#   (b) `-v` + xdist:  `<nodeid>` … then `[gw0] [ 42%] PASSED <nodeid>`
#                      — result on a LATER line, nodeid at the END
#   (c) a kill:        `<nodeid>` with no result line at all — the culprit
#
# A nodeid is therefore NOT a whitespace-delimited token: pytest builds
# parametrize ids from parameter VALUES, and this repo really has ids containing a
# space and parentheses (`pytest.param({"a": 1}, id="map-shape (control)")`,
# tests/test_3817_tags_rebuild_durability.py). An allow-list-of-id-characters
# version truncated at the first space, which does not merely misname the culprit:
# the truncated key can equal the key of an EARLIER, already-PASSED parametrized
# sibling, so the real culprit is filtered out as "done" and the report asserts the
# log's opposite ("none unresolved"). So a nodeid is "the whole line minus a
# RECOGNISED result suffix", and `done_nid` is keyed on that untruncated string —
# one id can only ever be closed by itself.
#
# What keeps the `-r fEs` and `--durations` blocks from being read as starts is the
# `^<token>::` anchor: `FAILED tests/x.py::t - …` and `116.35s call  tests/…` both
# begin with something that carries no `::`, and neither is a bare nodeid. This is
# a REPORT, not a verdict — it never changes the step status — and prose (not an
# annotation) is deliberate: the INCOMPLETE marker below already gives the leg its
# #3467 key, and a second annotation would double-count an already-attributable leg.
IN_FLIGHT="$(strip_sgr < "$LOG" | tr -d '\000' | awk '
  function rtrim(s) { sub(/[ \t]+$/, "", s); return s }
  {
    line = $0
    sub(/\r$/, "", line)
    # (a2) a SUBTEST progress line (`-v` + pytest-subtests, measured):
    # `tests/x.py::t SUBPASSED[case] (i=0)          [100%]`. Its `SUB*` verdict is
    # not in the allow-list below, so it would otherwise be read as a START and every
    # completed subtest reported as in flight — accusing tests that passed. Instead it
    # registers the PARENT as started: the parent is only closed by its own
    # PASSED/FAILED, which is exactly right when a kill lands INSIDE a subtest.
    if (match(line, /[ \t]SUB(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)/)) {
      # The parent is the LAST nodeid-shaped token on the line: with xdist the
      # verdict comes first and the nodeid last (`[gw0] [100%] SUBPASSED[case]
      # (i=0) tests/x.py::t`), so "everything before SUB" would fabricate a test
      # called `[gw0] [100%]` that can never be closed.
      parent = ""
      head = rtrim(substr(line, 1, RSTART - 1))
      if (index(head, "::") > 0) {
        # serial: everything before the verdict IS the parent, spaces and all
        # (`…::test_param[map-shape (control)] SUBPASSED…`).
        parent = head
      } else {
        # xdist: the verdict comes first and the parent is the LAST token, so scan
        # from the end — again as a substring, so a spaced id survives.
        rest = line
        while (match(rest, /[^ \t]+/)) {
          tok = substr(rest, RSTART, RLENGTH)
          if (index(tok, "::") > 0) { parent = tok; break }
          rest = substr(rest, RSTART + RLENGTH)
        }
      }
      if (parent == "") next
      if (!(parent in seen)) {
        seen[parent] = 1
        order[++n] = parent
      }
      next
    }
    # (b) an xdist result line carries the verdict FIRST and the nodeid LAST.
    if (match(line, /^\[gw[0-9]+\][ \t]+\[ *[0-9]+%\][ \t]+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS|RERUN)[ \t]+/)) {
      done_nid[rtrim(substr(line, RLENGTH + 1))] = 1
      next
    }
    # (a) a result appended to the start line, optionally then `[ NN%]`. The
    # status may carry an abbreviated reason (a verbose skip prints
    # `tests/x.py::t SKIPPED (...) [ 80%]`, measured), so the reason is part of
    # the SUFFIX and never of the nodeid.
    body = line
    sub(/[ \t]+\[ *[0-9]+%\][ \t]*$/, "", body)
    if (match(body, /[ \t]+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS|RERUN)([ \t]+\(.*\))?[ \t]*$/)) {
      done_nid[rtrim(substr(body, 1, RSTART - 1))] = 1
      next
    }
    # (c) a start line: its first whitespace-free token carries the `::`, which
    # excludes `FAILED …`, `ERROR …` and `<secs>s call  …` by construction.
    if (line ~ /^[^ \t]+::/) {
      nid = rtrim(line)
      if (!(nid in seen)) {
        seen[nid] = 1
        order[++n] = nid
      }
    }
  }
  END {
    k = 0
    for (i = n; i >= 1 && k < 4; i--) if (!(order[i] in done_nid)) keep[++k] = order[i]
    for (j = k; j >= 1; j--) print keep[j]
  }
')"

# A leg whose own session completed AND left nothing in flight has nothing to report
# — but ONLY when it was not killed. `timeout -s INT` makes pytest unwind far enough
# to print a bannered summary (measured: a 6s watchdog on 3 tests yields
# `1 passed in 6.08s`), and on a kill that summary is a partial verdict: it stops at
# the interrupt, so it must not be served as the leg's own while rc says the budget
# fired.
# A kill, for the purpose of "the suite was CUT SHORT": the watchdog's SIGINT (124),
# the SIGKILL that follows it (137), and pytest's own INTERRUPTED code (2) — which is
# also its collection-error code, so `2` counts only when that signature is absent.
# `--rc` is optional, so the kill is ALSO read from the log: a hand-run against a
# downloaded log (the natural way a triager uses this file) otherwise serves a
# partial summary as a clean run. The patterns are pytest's OWN shapes, anchored on
# purpose: `Timeout (0:04:40)!` is pytest-timeout's report, and the interrupt is a
# pytest's own interrupt BANNER (`!!!!!! KeyboardInterrupt !!!!!!`, measured). A bare
# substring is not enough — this repo's own tests raise KeyboardInterrupt on purpose
# (tests/test_ship_test_onboarding.py), so a COMPLETED leg's FAILURES section contains
# the word, and treating that as a kill reports a finished leg as unfinished.
KILL_EVIDENCE=0
if strip_sgr < "$LOG" | tr -d '\r\000' | grep -aE 'Timeout \([0-9]+:|^!+ KeyboardInterrupt !+' > /dev/null; then
  KILL_EVIDENCE=1
fi
KILLED=0
case "${RC:-}" in
  124|137) KILLED=1 ;;
  2)
    # A real collection error in a non-quiet run prints its OWN bannered summary
    # (`1 error in 0.09s`, measured), so it is a completed leg and the early exit
    # above takes it. The collection signature therefore only decides rc=2 when the
    # leg OWNS the summary that reports it — otherwise a nested child's collection
    # error would turn this leg's INTERRUPTED run into "not killed" (a false clean).
    # `COLLECTION_VERDICT` reads the LAST summary in the file whatever session it
    # belongs to, so it is necessary but NOT sufficient: ownership is what makes the
    # `ERROR <path>` record this leg's failure set instead of the child's.
    if [ "$COLLECTION_VERDICT" = "0" ] || [ "$HAVE_SUMMARY" = "0" ]; then
      KILLED=1
    fi
    ;;
  "") KILLED="$KILL_EVIDENCE" ;;
esac
if [ "$HAVE_SUMMARY" = "1" ] && [ "$KILLED" = "0" ] && [ -z "$IN_FLIGHT" ]; then
  exit 0
fi

# Gated on HAVE_SUMMARY as well as on the collection verdict: a collection error that
# belongs to a NESTED child must not switch the marker off for a leg whose own session
# never closed. Without that gate the leg contributes ZERO keys to the #3467 failure
# set — the `ids=0` / un-falsifiable comparison this file exists to prevent.
SUPPRESS_MARKER=0
if [ "$HAVE_SUMMARY" = "1" ] && [ "$COLLECTION_VERDICT" = "1" ] && [ "$OBSERVABLE" = "1" ] && [ "$KILLED" = "0" ]; then
  SUPPRESS_MARKER=1
fi
if { [ "$HAVE_SUMMARY" = "0" ] || [ "$KILLED" = "1" ]; } && [ "$SUPPRESS_MARKER" = "0" ]; then
  # Either the interpreter never reached pytest's final report (a SIGKILL, a crash,
  # an aborted interpreter), or it did reach it only because the watchdog's SIGINT
  # let it unwind: a killed leg's suite was CUT SHORT, so every test that never ran
  # is missing from its failure set. Both are unobservable, and saying so out loud is
  # the whole point — the annotation is what turns an unobservable leg into a named
  # failure instead of a leg the #3467 bar silently treats as clean.
  echo "::error::test leg INCOMPLETE — no pytest summary; failure set unobservable"
  if [ "$HAVE_SUMMARY" = "1" ]; then
    echo "(the leg was KILLED at its budget (pytest rc=${RC:-unknown}) and unwound far enough to"
    echo " print a summary of the tests that DID run; the tests after the kill never executed, so"
    echo " that summary is a partial verdict, not this leg's result)"
  else
    echo "(no session summary from THIS leg's own session — a summary printed above, if any,"
    echo " belongs to an earlier/nested session; pytest rc=${RC:-unknown})"
  fi
  echo "(do NOT read the absence of FAILED lines from this leg as a pass — the failure set is unobservable)"
fi

# Only a leg with NO verdict of its own can have been killed mid-test: with an owned
# summary, an unresolved start line means the parser saw something this leg did not
# run (a nested run's captured stdout), and naming it would send a triager after a
# test that was never in flight here.
# A leg that owns a summary and was NOT killed has nothing to report, whatever
# stray start lines a nested run's captured stdout put in the log.
if [ "$KILLED" = "0" ]; then
  [ "$HAVE_SUMMARY" = "1" ] && exit 0
  # `OBSERVABLE` only licenses silence when the record it reads is THIS LEG's. A log
  # carrying a collection signature whose summary this leg does not own has an
  # `ERROR <path>` line that belongs to a nested child (it is already labelled STALE
  # above), so it cannot stand in for a verdict this leg never produced — the leg's
  # only actionable report is the in-flight list below.
  if [ "$OBSERVABLE" = "1" ] && [ "$COLLECTION_ERROR" = "0" ]; then
    exit 0
  fi
fi

if [ -n "$IN_FLIGHT" ]; then
  echo "==================== in-flight test(s): what the leg was doing when it died ===================="
  while IFS= read -r nid; do
    [ -n "$nid" ] && echo "  $nid"
  done <<< "$IN_FLIGHT"
  echo "(raw log line(s): a pytest-timeout suffix is kept — started and never reported a result,"
  echo " so this is NOT a failure verdict — the leg was killed at its budget or crashed here)"
else
  echo "==================== in-flight test(s): none unresolved ===================="
  if [ "$TRUNCATED" = "1" ]; then
    echo "(the log carries a NUL hole: a second writer truncated it mid-run, so test START"
    echo " lines may be missing entirely — read this as 'not visible', never as 'not killed')"
  elif [ "$COLLECTION_ERROR" = "1" ] && [ "$KILLED" = "0" ]; then
    echo "(no test ran and lost its result, and this log carries a COLLECTION ERROR: its"
    echo " attributable record is the 'ERROR <path>' line, not a kill. In a -q log that"
    echo " summary line carries no '====' banner, so SUMMARY_RE cannot match it)"
  elif [ "$KILLED" = "0" ] && [ -n "${RC:-}" ]; then
    echo "(no test printed a start line at all: pytest exited rc=$RC without a session"
    echo " summary — a load/collection or internal error, not a budget kill — so there is"
    echo " no test to name here)"
  elif [ "$KILLED" = "1" ]; then
    echo "(the kill landed between tests — no test had started without reporting a result —"
    echo " so the culprit is not a single nodeid; the tests that never ran are still missing"
    echo " from this leg's failure set)"
  else
    echo "(every test that started also reported a result, so this was not a mid-test kill:"
    echo " pytest exited between tests, or the leg ended before any test began)"
  fi
fi
exit 0
