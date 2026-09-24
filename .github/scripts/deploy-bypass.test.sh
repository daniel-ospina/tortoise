#!/usr/bin/env bash
# deploy-bypass.test.sh — self-check for .github/scripts/deploy-bypass.sh (#4759).
#
# Run: bash .github/scripts/deploy-bypass.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: no network,
# no gh, no GitHub context — every input the script reads is an argument or an
# env binding, exactly as the workflow supplies them.
#
# THE CONTRACT THIS HARNESS PINS (#4759). A bypassed gate and a passed gate must
# not look the same in the run summary, and a `SKIP_*` lane left set must be
# something a machine checks:
#   1-8. `report` writes a UNIFORM $GITHUB_STEP_SUMMARY block naming the gate,
#        the lane that fired (input vs variable — on a push run `inputs` is null
#        so the variable is the only lane), and stating BYPASSED. Neuter the
#        summary write and these cases go red — that is the mutation test.
#   9-12. ...and the window state: in-window / OVERDUE / NOT RECORDED / INVALID,
#        with a positive control that the window is the CONFIGURED one and not a
#        hardcoded number. Plus: a report with NO lane set must FAIL loudly (a
#        broken reporter must never certify a bypass), and an unknown key is
#        refused.
#  13-19. `audit` states EVERY gate in the job as bypassed or not — from the lane
#        state, plus (for the `wrapper` gates) whether the skip was actually
#        applied — so the absence of a bypass block is not itself ambiguous.
#  20-30. `expiry` is the machine check: a lane inside its window is a warning, a
#        lane past the window (or with no usable start date) is a VIOLATION and a
#        non-zero exit. The lane comparison is the EXACT `== 'true'` the workflow
#        uses, so the check can never disagree with what actually bypasses.
#  31.    the helper NEVER maps a gate's exit code — see the case.
#
# The assertion count is PINNED (see the summary): a case that is lost must not
# be indistinguishable from a case that passed.
#
# NOTE ON SCOPE. `expiry` exits 1 — that is its OWN scheduled workflow's red and
# it is never wired to a deploy. The could-not-determine (`exit 2`) class this
# helper must not touch lives in deploy-hosted.yml; the Python guard in
# tests/test_deploy_workflow.py pins that the exit-2 branches still precede the
# bypass branches. What this harness pins is that the helper never MAPS any
# gate's exit code — see case 31.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SCRIPT_DIR/deploy-bypass.sh"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ✅ $1"; }
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
trap 'rm -rf "$WORK"' EXIT

# Run `report` with a fresh summary file and a fresh marker; echoes "<rc>|<summary>".
# The step's own stdout/stderr land in $WORK/out and the marker in $WORK/m —
# read them with last_out / last_marker (the call itself runs in a subshell
# under command substitution, so shell variables would not survive).
run_report() { # <args...>
  : >"$WORK/s"
  : >"$WORK/m"
  local rc=0
  GITHUB_STEP_SUMMARY="$WORK/s" DEPLOY_BYPASS_MARKER="$WORK/m" \
    bash "$HELPER" report "$@" >"$WORK/out" 2>&1 || rc=$?
  printf '%s|%s' "$rc" "$(cat "$WORK/s")"
}
last_out() { cat "$WORK/out"; }
last_marker() { cat "$WORK/m"; }

echo "── report: the uniform summary block ───────────────────────────────"
# 1. input lane — per run, nothing left set.
r="$(run_report --key SKIP_PACK_SMOKE --input-fired true --variable-fired false \
      --effect 'the hosted image ships without a pack gate')"
assert_eq "${r%%|*}" "0" "input lane: exits 0"
body="${r#*|}"
assert_contains "$body" "## ⛔ Deploy gate BYPASSED — pack-catalog smoke" "input lane: summary heading names the gate"
assert_contains "$body" "**Lane that fired:** input" "input lane: summary names the lane that fired"
assert_contains "$body" "skip-pack-smoke" "input lane: summary names the dispatch input"
assert_contains "$body" "SKIP_PACK_SMOKE" "input lane: summary names the repo variable"
assert_contains "$body" "The dispatch input applies to this run only" "input lane: says nothing is left set"
assert_contains "$(last_out)" "::warning::" "input lane: ::warning:: preserved (auditability)"
assert_not_contains "$body" "days ago" "input lane: no window age for a per-run lane"
assert_eq "$(last_marker)" "SKIP_PACK_SMOKE" "input lane: audit marker records the bypassed gate"

# 2. variable lane, inside the window.
r="$(run_report --key SKIP_DB_HEALTH_GATE --input-fired false --variable-fired true \
      --set-at 2026-09-20 --today 2026-09-22 --effect 'the release health check does not run')"
assert_eq "${r%%|*}" "0" "variable lane: exits 0"
body="${r#*|}"
assert_contains "$body" "**Lane that fired:** variable" "variable lane: summary names the variable lane"
assert_contains "$body" "**Set \`2\` days ago** (\`2026-09-20\`; 7-day window)" "variable lane: summary states the age and window"
assert_contains "$body" "gh variable delete SKIP_DB_HEALTH_GATE" "variable lane: summary gives the clear command"
assert_eq "$(last_marker)" "SKIP_DB_HEALTH_GATE" "variable lane: audit marker records the bypassed gate"

# 3. both lanes fire (push-lane variable set while a dispatch also passed the input).
r="$(run_report --key SKIP_FLY_MACHINES_GUARD --input-fired true --variable-fired true \
      --set-at 2026-09-21 --today 2026-09-22 --effect 'orphan machines do not block')"
assert_contains "${r#*|}" "**Lane that fired:** input,variable" "both lanes: summary names both"

# 4. OVERDUE — the whole point of the window.
r="$(run_report --key SKIP_FLY_SECRET_PROVENANCE --input-fired false --variable-fired true \
      --set-at 2026-09-01 --today 2026-09-22 --effect 'undeclared secrets do not block')"
assert_contains "${r#*|}" "**⚠️ OVERDUE: set \`21\` days ago**" "overdue: summary calls it out"
assert_contains "${r#*|}" "past the 7-day window" "overdue: summary names the window"

# 5. NOT RECORDED — a bypass with no start date cannot be aged.
r="$(run_report --key SKIP_PACK_SMOKE --input-fired false --variable-fired true \
      --set-at '' --today 2026-09-22)"
assert_contains "${r#*|}" "Window start: NOT RECORDED" "no start date: summary says so"
assert_contains "${r#*|}" "SKIP_PACK_SMOKE_SET_AT" "no start date: summary names the variable to set"

# 6. INVALID start date.
r="$(run_report --key SKIP_PACK_SMOKE --input-fired false --variable-fired true \
      --set-at 2026-13-45 --today 2026-09-22)"
assert_contains "${r#*|}" "Window start INVALID: \`2026-13-45\`" "invalid date: summary says so"

# 7. FUTURE start date (never ages — a bypass would hide forever).
r="$(run_report --key SKIP_PACK_SMOKE --input-fired false --variable-fired true \
      --set-at 2027-01-01 --today 2026-09-22)"
assert_contains "${r#*|}" "Window start IN FUTURE: \`2027-01-01\`" "future date: summary says so"

# 8. THE MUTATION TARGET, made explicit: the summary file is written at all.
#    Deleting the `emit` body's append (or bypassing `emit`) reddens this and
#    every case above — a reporting change that cannot fail its own test is not
#    verified.
r="$(run_report --key SKIP_DB_HEALTH_GATE --input-fired false --variable-fired true \
      --set-at 2026-09-22 --today 2026-09-22)"
assert_contains "${r#*|}" "Deploy gate BYPASSED" "summary is genuinely written to \$GITHUB_STEP_SUMMARY"

# 9. POSITIVE CONTROL for the window: the configured window decides, not a
#    hardcoded 7. A 2-day-old bypass is inside the default and OVERDUE at 1.
r="$(run_report --key SKIP_PACK_SMOKE --input-fired false --variable-fired true \
      --set-at 2026-09-20 --today 2026-09-22 --window-days 1)"
assert_contains "${r#*|}" "OVERDUE" "window is honoured: 2 days is overdue at --window-days 1"
assert_contains "${r#*|}" "past the 1-day window" "window value is rendered verbatim"
r="$(run_report --key SKIP_PACK_SMOKE --input-fired false --variable-fired true \
      --set-at 2026-09-20 --today 2026-09-22 --window-days 7)"
assert_not_contains "${r#*|}" "OVERDUE" "control: the same age is fine at --window-days 7"

# 10. No GITHUB_STEP_SUMMARY at all (a bare local run) must not fail the step.
rc=0
bash "$HELPER" report --key SKIP_PACK_SMOKE --input-fired true --variable-fired false \
  >/dev/null 2>&1 || rc=$?
assert_eq "$rc" "0" "no \$GITHUB_STEP_SUMMARY: report still exits 0"

# 11. a report with NO lane set is a wiring defect and must NOT look like a
#     successful report of a bypass.
r="$(run_report --key SKIP_PACK_SMOKE --input-fired false --variable-fired false)"
assert_eq "${r%%|*}" "1" "no lane set: exits non-zero"
assert_contains "$(last_out)" "::error::" "no lane set: loud ::error::"
assert_contains "${r#*|}" "report FAILED" "no lane set: summary says the report failed"

# 12. an unknown gate key is refused (the table is the allow-list).
rc=0
bash "$HELPER" report --key SKIP_NOT_A_GATE --input-fired true --variable-fired false \
  >/dev/null 2>&1 || rc=$?
assert_eq "$rc" "64" "unknown key: refused"

echo "── audit: every gate in the job, bypassed or not ───────────────────"
# 13. no lane set anywhere → the job states every gate was NOT bypassed, so the
#     absence of a bypass block is not itself ambiguous.
: >"$WORK/empty-marker"
out="$(DEPLOY_BYPASS_MARKER="$WORK/empty-marker" bash "$HELPER" audit --job deploy-api \
      --state SKIP_PACK_SMOKE=false/false \
      --state SKIP_FLY_MACHINES_GUARD=false/false \
      --state SKIP_FLY_SECRET_PROVENANCE=false/false 2>&1)"
assert_contains "$out" "Deploy gate audit — deploy-api" "audit: names the job"
assert_contains "$out" "\`SKIP_PACK_SMOKE\` (pack-catalog smoke) — lane NOT set: **not bypassed**" "audit: names a gate that was not bypassed"
assert_not_contains "$out" "skipped" "audit: a lane-not-set gate makes no claim that its step EXECUTED (skipped)"
assert_not_contains "$out" "ran" "audit: a lane-not-set gate makes no claim that its step EXECUTED (ran)"
assert_contains "$out" "\`SKIP_FLY_MACHINES_GUARD\`" "audit: names every gate in the job"
assert_contains "$out" "No bypass lane was set for any bypassable deploy gate in this job" "audit: states unambiguously that nothing was bypassed"

# 14. an `if`-gate lane set → BYPASSED, stated as SKIPPED not passed; the other
#     gates are still listed as not bypassed (no blanket claim).
out="$(DEPLOY_BYPASS_MARKER="$WORK/empty-marker" bash "$HELPER" audit --job deploy-api \
      --state SKIP_PACK_SMOKE=true/false \
      --state SKIP_FLY_SECRET_PROVENANCE=false/false 2>&1)"
assert_contains "$out" "\`SKIP_PACK_SMOKE\` (pack-catalog smoke) — **BYPASSED** (lane(s): input) — the gate was SKIPPED, not passed" \
  "audit: an if-gate with a set lane is BYPASSED and said to be SKIPPED"
assert_contains "$out" "\`SKIP_FLY_SECRET_PROVENANCE\` (Fly secret provenance) — lane NOT set" "audit: leaves the others as not bypassed"
assert_not_contains "$out" "No bypass lane was set" "audit: does not claim nothing was bypassed once one is"

# 15. a `wrapper` gate with a set lane and NO bypass recorded means the checker
#     did not return exit 1 — the gate is NOT bypassed, and exit 2 still blocks.
out="$(DEPLOY_BYPASS_MARKER="$WORK/empty-marker" bash "$HELPER" audit --job deploy-api \
      --state SKIP_FLY_MACHINES_GUARD=false/true 2>&1)"
assert_contains "$out" "lane(s) variable armed but **NOT bypassed**" "audit: a wrapper gate with a lane and no bypass reads as NOT bypassed"
assert_contains "$out" "the exit-2 class still blocks" "audit: restates that the exit-2 class is never bypassable"
assert_not_contains "$out" "it ran" "audit: never claims a gate step EXECUTED (a skipped checker would make that false)"
assert_not_contains "$out" "was not skipped" "audit: never claims a gate step was not skipped (an earlier failure can skip a checker)"

# 16. ...and WITH the bypass recorded by the report step it reads as BYPASSED.
printf 'SKIP_FLY_MACHINES_GUARD\n' >"$WORK/one-marker"
out="$(DEPLOY_BYPASS_MARKER="$WORK/one-marker" bash "$HELPER" audit --job deploy-api \
      --state SKIP_FLY_MACHINES_GUARD=false/true 2>&1)"
assert_contains "$out" "\`SKIP_FLY_MACHINES_GUARD\` (Fly machine orphan/crash-loop guard) — **BYPASSED** (lane(s): variable) — the checker's exit-1 class was translated" \
  "audit: a wrapper gate with a recorded bypass reads as BYPASSED"

# 17. post-deploy-verify has exactly one bypassable gate; both lanes on it are
#     reported (the push-lane case where `inputs` is null).
out="$(DEPLOY_BYPASS_MARKER="$WORK/empty-marker" bash "$HELPER" audit --job post-deploy-verify \
      --state SKIP_DB_HEALTH_GATE=false/true 2>&1)"
assert_contains "$out" "Deploy gate audit — post-deploy-verify" "audit: names the post-deploy job"
assert_contains "$out" "\`SKIP_DB_HEALTH_GATE\` (DB health verification) — **BYPASSED** (lane(s): variable)" \
  "audit: reports the variable lane for the health gate"

# 18. --state is required: an audit with nothing to enumerate proves nothing.
rc=0
bash "$HELPER" audit --job deploy-api >/dev/null 2>&1 || rc=$?
assert_eq "$rc" "64" "audit without --state: refused"

# 19. a malformed --state is refused rather than silently read as unset.
rc=0
bash "$HELPER" audit --job deploy-api --state SKIP_PACK_SMOKE=yes >/dev/null 2>&1 || rc=$?
assert_eq "$rc" "64" "audit with malformed --state: refused"

echo "── expiry: the machine check ──────────────────────────────────────"
run_expiry() { # <args...> (reads the four lanes from the environment)
  : >"$WORK/expsummary"
  local rc=0
  GITHUB_STEP_SUMMARY="$WORK/expsummary" bash "$HELPER" expiry "$@" >"$WORK/out" 2>&1 || rc=$?
  printf '%s|%s' "$rc" "$(cat "$WORK/expsummary")"
}

# 20. nothing set → green, and it SAYS every gate is armed.
r="$(SKIP_DB_HEALTH_GATE=false SKIP_PACK_SMOKE= SKIP_FLY_MACHINES_GUARD= \
     SKIP_FLY_SECRET_PROVENANCE=false run_expiry --today 2026-09-22)"
assert_eq "${r%%|*}" "0" "expiry: nothing set exits 0"
assert_contains "${r#*|}" "No violation" "expiry: nothing set states no violation"
assert_contains "${r#*|}" "\`SKIP_DB_HEALTH_GATE\` — armed (not set)" "expiry: lists an unset lane as armed"

# 21. inside the window → warning, not a violation.
r="$(SKIP_DB_HEALTH_GATE=true SKIP_DB_HEALTH_GATE_SET_AT=2026-09-20 \
     run_expiry --today 2026-09-22)"
assert_eq "${r%%|*}" "0" "expiry: inside the window exits 0"
assert_contains "${r#*|}" "set **2 days** (since 2026-09-20)" "expiry: names the age"
assert_contains "$(last_out)" "::warning::" "expiry: inside the window is a warning"

# 22. past the window → VIOLATION, red, and it names the clear command.
r="$(SKIP_PACK_SMOKE=true SKIP_PACK_SMOKE_SET_AT=2026-08-31 run_expiry --today 2026-09-22)"
assert_eq "${r%%|*}" "1" "expiry: overdue exits 1 (the scheduled red)"
assert_contains "$(last_out)" "::error::" "expiry: overdue is an ::error::"
assert_contains "$(last_out)" "gh variable delete SKIP_PACK_SMOKE" "expiry: names the clear command"
assert_contains "${r#*|}" "\`SKIP_PACK_SMOKE\` — **VIOLATION**" "expiry: summary marks the violation"

# 23. set with NO start date → violation (the failure direction is never silence).
r="$(SKIP_FLY_MACHINES_GUARD=true SKIP_FLY_MACHINES_GUARD_SET_AT='' run_expiry --today 2026-09-22)"
assert_eq "${r%%|*}" "1" "expiry: missing start date exits 1"
assert_contains "$(last_out)" "CANNOT be checked" "expiry: missing start date says the window cannot be checked"

# 24. invalid start date — a calendar date that does not exist. Both `date`
#     parsers SILENTLY NORMALISE 2026-02-30 to 2026-03-02, so this case also
#     pins the round-trip check that turns that shift into an INVALID verdict.
r="$(SKIP_FLY_MACHINES_GUARD=true SKIP_FLY_MACHINES_GUARD_SET_AT=2026-02-30 run_expiry --today 2026-09-22)"
assert_eq "${r%%|*}" "1" "expiry: impossible calendar date exits 1"
assert_contains "${r#*|}" "not a valid YYYY-MM-DD date" "expiry: impossible date is named as invalid"

# 25. ...and the same date must not be silently aged from its normalised form.
r="$(SKIP_FLY_MACHINES_GUARD=true SKIP_FLY_MACHINES_GUARD_SET_AT=2026-02-30 run_expiry --today 2026-09-22)"
assert_not_contains "${r#*|}" "past the 7-day window" "expiry: an impossible date is never aged (no silent normalisation)"

# 26. future start date.
r="$(SKIP_FLY_MACHINES_GUARD=true SKIP_FLY_MACHINES_GUARD_SET_AT=2030-01-01 run_expiry --today 2026-09-22)"
assert_eq "${r%%|*}" "1" "expiry: future start date exits 1"
assert_contains "${r#*|}" "in the FUTURE" "expiry: future date is named"

# 27. a stale date left behind by a CLEARED bypass is hygiene, not a violation.
r="$(SKIP_DB_HEALTH_GATE=false SKIP_DB_HEALTH_GATE_SET_AT=2026-08-01 run_expiry --today 2026-09-22)"
assert_eq "${r%%|*}" "0" "expiry: stale date on an armed gate is not a violation"
assert_contains "${r#*|}" "stale" "expiry: stale date is named as stale"
assert_contains "$(last_out)" "::warning::" "expiry: stale date is a warning"

# 28. POSITIVE CONTROL: the window is configured, not hardcoded.
r="$(SKIP_PACK_SMOKE=true SKIP_PACK_SMOKE_SET_AT=2026-09-20 run_expiry --today 2026-09-22)"
assert_eq "${r%%|*}" "0" "window control: 2 days is fine at the default window"
r="$(SKIP_PACK_SMOKE=true SKIP_PACK_SMOKE_SET_AT=2026-09-20 run_expiry --today 2026-09-22 --window-days 1)"
assert_eq "${r%%|*}" "1" "window control: the same 2 days violates --window-days 1"

# 29. the lane test must MATCH the engine. GitHub's expression `==` compares
#     strings case-INSENSITIVELY, so `TRUE` DOES bypass the gate and the monitor
#     must age it: a case-sensitive test here reads it as "armed (not set)",
#     returns green, and tells the operator to delete the only record of the
#     bypass while the deploy summary says BYPASSED — the exact
#     failure-looks-like-success defect #4759 removes. Only the string `true`,
#     ANY CASING, is a bypass; `1` genuinely is not.
r="$(SKIP_PACK_SMOKE=TRUE SKIP_PACK_SMOKE_SET_AT='' run_expiry --today 2026-09-22)"
assert_eq "${r%%|*}" "1" "expiry: 'TRUE' IS a bypass (GitHub compares case-insensitively) — exits 1"
assert_contains "${r#*|}" "bypass set with NO start date" "expiry: 'TRUE' with no start date is a violation, not 'armed'"
r="$(SKIP_PACK_SMOKE=TRUE SKIP_PACK_SMOKE_SET_AT=2026-01-01 run_expiry --today 2026-09-22)"
assert_eq "${r%%|*}" "1" "expiry: 'TRUE' is aged like 'true' — exits 1 past the window"
assert_contains "${r#*|}" "past the 7-day window" "expiry: 'TRUE' past the window is a VIOLATION"

# 29b. ...and the normalisation is only about CASING: `1` is not the string
#      "true", so it stays a non-bypass (and stays on the armed branch).
r="$(SKIP_PACK_SMOKE=1 SKIP_PACK_SMOKE_SET_AT='' run_expiry --today 2026-09-22)"
assert_eq "${r%%|*}" "0" "expiry: '1' is NOT a bypass"
assert_contains "${r#*|}" "\`SKIP_PACK_SMOKE\` — armed (not set)" "expiry: '1' reads as armed"

# 30. a bad --today / --window-days is a usage error, never a silent pass.
rc=0
SKIP_PACK_SMOKE=true bash "$HELPER" expiry --today not-a-date >/dev/null 2>&1 || rc=$?
assert_eq "$rc" "64" "expiry: bad --today refused"
rc=0
bash "$HELPER" expiry --window-days seven >/dev/null 2>&1 || rc=$?
assert_eq "$rc" "64" "expiry: bad --window-days refused"

# 30b. a value-taking flag given NO value is a usage error, not a spin. `shift 2`
#      FAILS when only one positional remains and bash leaves the parameters
#      UNCHANGED, so an ungated parser re-reads the same `$1` forever and the
#      exit-64 path is never reached. Bounded by a watchdog: a regression must
#      fail here as a RED, never hang the harness (and `report` is the whole
#      `run:` body of a deploy step with no `timeout-minutes`).
run_bounded() { # <cmd...> -> exit status (137 when the watchdog had to kill it)
  local rc=0 pid wt
  "$@" >"$WORK/out" 2>&1 &
  pid=$!
  ( sleep 5; kill -9 "$pid" 2>/dev/null ) >/dev/null 2>&1 &
  wt=$!
  wait "$pid" || rc=$?
  kill "$wt" 2>/dev/null
  wait "$wt" 2>/dev/null
  printf '%s' "$rc"
}
assert_eq "$(run_bounded bash "$HELPER" report --key)" "64" "report: a flag with no value is refused, not spun"
assert_contains "$(cat "$WORK/out")" "requires a value" "report: the refusal names the missing value"
assert_eq "$(run_bounded bash "$HELPER" audit --state)" "64" "audit: a flag with no value is refused, not spun"
assert_eq "$(run_bounded bash "$HELPER" expiry --today)" "64" "expiry: a flag with no value is refused, not spun"

# 30c. the per-job audit marker write must not be SILENT when it fails. The
#      audit derives a `wrapper` gate's verdict from that marker, so a dropped
#      write would make it state NOT bypassed for a gate the report block says
#      BYPASSED. It must stay non-fatal (failing the step would strand the
#      incident deploy the bypass exists for) but say so in the LOG and the
#      SUMMARY.
: >"$WORK/s-marker"
rc=0
GITHUB_STEP_SUMMARY="$WORK/s-marker" DEPLOY_BYPASS_MARKER="$WORK/no-such-dir/m" \
  bash "$HELPER" report --key SKIP_FLY_MACHINES_GUARD --variable-fired true >"$WORK/out" 2>&1 || rc=$?
assert_eq "$rc" "0" "marker write failure: report still exits 0 (never strands the incident deploy)"
assert_contains "$(cat "$WORK/out")" "could not record the audit marker" "marker write failure: LOUD ::warning:: in the step log"
assert_contains "$(cat "$WORK/s-marker")" "Audit marker NOT written" "marker write failure: the run summary says the audit cannot certify"
r="$(run_report --key SKIP_PACK_SMOKE --variable-fired true --set-at 2026-09-22)"
assert_not_contains "${r#*|}" "Audit marker NOT written" "marker write SUCCESS: no false alarm"

echo "── the helper never maps a gate's exit code ───────────────────────"
# 31. `exit 2` is the could-not-determine class and belongs to the GATES, not to
#     this reporter. A reporter that learns about exit codes is a reporter that
#     can be wired into one. Pin that it never does: no `exit 2`, and it never
#     runs a gate script.
assert_eq "$(grep -vE '^[[:space:]]*#' "$HELPER" | grep -cE '(^|[;|&]|[[:space:]])exit[[:space:]]+2([[:space:]]|;|$)' || true)" "0" \
  "helper contains no 'exit 2' statement on any executable line"
assert_eq "$(grep -cE 'check-fly-(machines-guard|secret-drift)\.py|deploy-health-gate\.sh' <(grep -v '^#' "$HELPER") || true)" "0" \
  "helper never invokes a gate script (the header's do-not note is a comment)"

echo "──────────────────────────────────────────"
# A LOST case must not be indistinguishable from success: deleting a case
# leaves FAIL=0 and merely a LOWER count, so the count is pinned too.
expected_assertions=88
if [ "$PASS" -eq "$expected_assertions" ]; then
  PASS=$((PASS + 1))
  echo "  ✅ assertion count pinned at $expected_assertions (a lost case is not a green run)"
elif [ "$FAIL" -gt 0 ]; then
  echo "  ❌ only $PASS of $expected_assertions assertions ran — a consequence of the failures above"
elif [ "$PASS" -gt "$expected_assertions" ]; then
  # The INVERSE cause: a case was ADDED without bumping the constant above.
  # This MUST fail too — otherwise a stale pin is indistinguishable from a
  # correct one in the direction that matters least often but silently:
  # a green CI over a stale count (review P2 on PR #4802).
  echo "  ❌ $PASS assertions ran, expected $expected_assertions — a case was ADDED: bump expected_assertions"
  FAIL=$((FAIL + 1))
else
  echo "  ❌ expected $expected_assertions assertions, got $PASS — a case was LOST"
  FAIL=$((FAIL + 1))
fi

echo "──────────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
  echo "✅ ALL PASSED — $PASS assertions"
  exit 0
fi
echo "❌ $FAIL FAILED, $PASS passed"
exit 1
