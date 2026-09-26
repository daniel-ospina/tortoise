#!/usr/bin/env bash
# deploy-health-gate.test.sh — self-check for
# .github/scripts/deploy-health-gate.sh (#4545).
#
# Run: bash .github/scripts/deploy-health-gate.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: stubs
# `curl` on PATH. No network, no Fly, no live service.
#
# Coverage:
#   1. app unreachable            → exit 1 without ever waiting on the DB, and
#                                   without reaching the readiness assertion (a
#                                   dead app is a deploy failure, not a slow
#                                   database). Probe-count control included.
#   2. db.ok never true + readiness 200 → exit 0 (PASS) with a loud ::warning::
#      naming the observation. THE #4771 CONTRACT: the WEAKER predicate informs,
#      the STRONGEST decides. Reverting phase 2 to `exit 1` reddens this case.
#   3. REGRESSION (#4545): db.ok true on the first DB poll while /health/ready
#      is still 503, becoming 200 on a LATER probe → exit 0. This is the defect:
#      the gate used to assert readiness ONCE, 2-3 s after db.ok, and mark a
#      healthy deploy FAILED. It FAILS against the pre-fix single-shot gate and
#      passes only with the readiness poll.
#   4. readiness never 200        → exit 1 with the readiness message (the gate
#                                   stays FAIL-CLOSED: #3 does not weaken it).
#                                   The FalkorDB clause is absent here because
#                                   db.ok WAS observed true — never assert a
#                                   failure you did not observe.
#   5. the db.ok read is a JSON FIELD read, not a body text-match: every
#      non-ready body here carries `backup_watcher.ok = true` beside
#      `db.ok = false` — the exact shape that made a `grep '"ok": true'`
#      short-circuit and silently neutralise the 3-minute tolerance (#4470).
#      Post-#4771 the same shape must still print the phase-2 EXHAUSTION
#      warning — a text-match would have short-circuited and never warned.
#   6. the db.ok RETRY is genuinely exercised: db.ok false on the first DB poll
#      and true on the second → exit 0, with a positive control on the probe
#      count. (The counter is shared with the app-reachability probe, so the
#      knob is named for the Nth /health response — the app probe consumes one.)
#   7. readiness answering 3xx    → exit 1. `curl -f` treats ANY status < 400 as
#      success, so a redirecting /health/ready PASSED this gate — main logged
#      the true status (`-w %{http_code}`) but never failed on it, so a 302
#      sailed through as a success. Same class as #4545: passing an
#      unobserved predicate. (The stub also STREAMS A BODY, as real curl does,
#      so dropping `-o /dev/null` is caught: curl would then capture
#      "<body>200", which never equals "200" and would redden EVERY deploy —
#      case 3 is that pin.)
#   8. a BAD KNOB                → exit 2. A non-integer knob used to abort the
#      shell inside the `$((...))` of the failure message (under `set -u`), so
#      `exit 1` never ran and bash 3.2 exited 0 — GREEN on a dead DB. Without
#      this case, DELETING the validator leaves every assertion here green.
#   9. an observed code + a non-zero curl exit → still SUCCEEDS. curl can print
#      a real code and STILL exit non-zero (a transfer failure after the status
#      line). The app answered, so readiness WAS observed; overwriting that 200
#      with the 000 sentinel would redden a ready deploy — the #4545 symptom.
#      Carries a READY_POLLS control so its state is NOT one case 8 leaves
#      behind (otherwise this case could silently stop running and still pass).
#  10. readiness with NO RESPONSE → exit 1, and the message names the 000
#      sentinel, so a dead app is not reported as an unready one.
#  11. db.ok never true + readiness never 200 → exit 1. THE SAFETY HALF of
#      #4771: readiness ANDs the SAME FalkorDB data plane on its OWN probe, so
#      a genuinely unreachable DB fails BOTH. #4771 is therefore NOT a
#      weakening — if readiness had become advisory (or phase 3 been skipped),
#      this case would exit 0 and the gate would be failing OPEN.
#
# The count of assertions is PINNED (see the summary): a case that is lost must
# not be indistinguishable from a case that passed.
#
# POSITIVE CONTROLS: in case 3 readiness is polled MORE THAN ONCE, in case 6
# /health is polled more than once, in case 1 the app phase is shown to stop
# rather than fall through, in cases 2/5 the phase-2 poll is shown to run to
# EXHAUSTION (a skipped poll cannot print the warning), and in case 11 phase 3
# is shown to be reached and polled to exhaustion. (A test that cannot fail
# proves nothing.)
#
# Case 3 is the one that matters: it is the difference between "the deploy
# succeeded" and "the deploy was merely early".

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="$SCRIPT_DIR/deploy-health-gate.sh"

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
trap 'find "$WORK" -depth -mindepth 1 -delete 2>/dev/null; rmdir "$WORK" 2>/dev/null' EXIT
STUB_BIN="$WORK/bin"
mkdir -p "$STUB_BIN"

# ── stub curl ───────────────────────────────────────────────────────────────
# The last argument is the URL. Counters live in $STUB_STATE so a scenario can
# serve "failing for the first N, then healthy" — the exact real shape.
#
# NOTE on the counters: `/health` is requested by BOTH the app-reachability
# probe and the db.ok poll, so there is ONE shared `health_n` counter and the
# knob is named `STUB_DB_OK_ON_HEALTH_N` — the Nth /health response overall.
# Naming it after the DB poll alone would be off by the number of app probes.
cat > "$STUB_BIN/curl" <<'STUB'
#!/usr/bin/env bash
url="${@: -1}"
state="${STUB_STATE:?}"
bump() { # <file> → prints the new count
  local f="$state/$1" n
  n=$(cat "$f" 2>/dev/null || echo 0)
  n=$((n + 1))
  echo "$n" > "$f"
  echo "$n"
}
case "$url" in
  */health/ready)
    n=$(bump ready_n)
    if [ "${STUB_READY_NO_RESPONSE:-no}" = "yes" ]; then
      exit 7   # connection failure — curl -w prints nothing
    fi
    if [ "${STUB_READY_OK_ON_READY_N:-0}" -gt 0 ] && [ "$n" -ge "${STUB_READY_OK_ON_READY_N}" ]; then
      code=200
    else
      code="${STUB_READY_CODE:-503}"
    fi
    # Emulate curl's body streaming: real curl writes the response BODY to
    # stdout, then the -w output. If the gate ever dropped `-o /dev/null` the
    # command substitution would capture "<body>200", never equal "200", and
    # the gate would be red on EVERY deploy — a harness that prints only the
    # code cannot see that.
    has_devnull=0
    for a in "$@"; do case "$a" in */dev/null) has_devnull=1 ;; esac; done
    [ "$has_devnull" = "1" ] || printf '{"status":"ok","db":"connected","control_plane":"connected"}'
    echo "$code"
    # Emulate `curl -f`: it fails only on status >= 400, so a 3xx SUCCEEDS.
    # This is what makes case 7 meaningful — a stub that failed on any non-200
    # would let the `-f` form pass the harness while it failed in reality.
    has_f=0
    for a in "$@"; do case "$a" in -*f*) has_f=1 ;; esac; done
    if [ "$has_f" = "1" ] && [ "$code" -ge 400 ]; then exit 22; fi
    # `curl` can print a real status and STILL exit non-zero (a transfer failure
    # after the status line). STUB_READY_EXIT models that.
    exit "${STUB_READY_EXIT:-0}"
    ;;
  */health)
    n=$(bump health_n)
    [ "${STUB_APP_REACHABLE:-yes}" = "yes" ] || exit 22
    # NOTE: `backup_watcher.ok` is TRUE in BOTH arms — the body carries a
    # second `"ok": true` exactly as the real /health does (#4470), so a
    # text-matching reader would go green while db.ok is false.
    if [ "${STUB_DB_OK_ON_HEALTH_N:-0}" -gt 0 ] && [ "$n" -ge "${STUB_DB_OK_ON_HEALTH_N}" ]; then
      printf '{"status":"ok","db":{"ok":true,"latency_ms":2.5,"error":null},"backup_watcher":{"state":"running","ok":true}}\n'
    else
      printf '{"status":"degraded","db":{"ok":false,"latency_ms":null,"error":"unreachable"},"backup_watcher":{"state":"running","ok":true}}\n'
    fi
    exit 0
    ;;
esac
exit 1
STUB
chmod +x "$STUB_BIN/curl"

# ── driver ──────────────────────────────────────────────────────────────────
# run_gate <app_reachable> <db_ok_on_health_n> <ready_ok_on_ready_n> [ready_code] [ready_no_response] [ready_exit]
#   → sets OUT (combined stdout+stderr), RC, HEALTH_POLLS, READY_POLLS
run_gate() {
  local state="$WORK/state-$$-$RANDOM"
  mkdir -p "$state"
  local st=0
  OUT=$(PATH="$STUB_BIN:$PATH" \
    STUB_STATE="$state" \
    STUB_APP_REACHABLE="$1" \
    STUB_DB_OK_ON_HEALTH_N="$2" \
    STUB_READY_OK_ON_READY_N="$3" \
    STUB_READY_CODE="${4:-503}" \
    STUB_READY_NO_RESPONSE="${5:-no}" \
    STUB_READY_EXIT="${6:-0}" \
    GATE_BASE="https://stub.invalid" \
    APP_PROBES=2 APP_PROBE_SLEEP=0 \
    DB_TRIES=3 DB_SLEEP=0 \
    READY_TRIES=4 READY_SLEEP=0 \
    bash "$GATE" 2>&1) || st=$?
  RC=$st
  READY_POLLS=$(cat "$state/ready_n" 2>/dev/null || echo 0)
  HEALTH_POLLS=$(cat "$state/health_n" 2>/dev/null || echo 0)
}

echo "deploy-health-gate.test.sh (#4545)"
echo

echo "1. app unreachable → fails without waiting on the DB"
run_gate "no" 1 1
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "app unreachable" "reports app-unreachable"
assert_not_contains "$OUT" "db.ok" "never reports a DB wait"
assert_not_contains "$OUT" "health/ready" "never reaches the readiness assertion"
assert_eq "$HEALTH_POLLS" "2" "stopped at the app budget, did not fall through (positive control)"

echo "2. db.ok never true + readiness 200 → PASSES with a warning (#4771)"
# THE #4771 CONTRACT: the weaker predicate INFORMS, the strongest DECIDES.
# db.ok never becomes true, but /health/ready — which ANDs the SAME data plane
# with the control plane — is 200. Pre-#4771 phase 2 exited 1 here and reddened
# a healthy release. Reverting that `exit 1` is what makes this case go RED.
run_gate "yes" 0 1
assert_eq "$RC" "0" "exits 0 — db.ok alone no longer fails the run (#4771)"
assert_contains "$OUT" "::warning::" "emits a loud annotated warning"
# Discriminating on purpose: the pre-#4771 phase-2 error ALSO contains
# "FalkorDB" ("… — FalkorDB unreachable"), so a bare `FalkorDB` assert would
# pass under the very regression this case exists to catch (review cycle 1,
# P2 — verified by mutation). This string exists only in the new warning path.
assert_contains "$OUT" "FalkorDB data-plane probe" "names the FalkorDB data plane as the observation"
assert_contains "$OUT" "health/ready 200" "the strongest predicate decides"
assert_eq "$HEALTH_POLLS" "4" "phase 2 still polled to exhaustion (positive control — a skipped poll cannot warn)"

echo "3. REGRESSION (#4545): db.ok true but readiness late → SUCCEEDS"
run_gate "yes" 1 3
assert_eq "$RC" "0" "exits 0 (was the red-deploy bug)"
assert_contains "$OUT" "db.ok true" "observed the data plane"
assert_contains "$OUT" "health/ready 200" "reported readiness"
assert_eq "$READY_POLLS" "3" "polled readiness until it answered (positive control)"

echo "4. readiness never 200 → still FAIL-CLOSED"
run_gate "yes" 1 0
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "/health/ready not 200" "names the readiness plane"
assert_not_contains "$OUT" "db.ok was ALSO never true" "does NOT claim a data-plane failure it never observed (db.ok was true here)"

echo "5. the db.ok read is a FIELD read, not a body text-match (#4470)"
# Every body above carries backup_watcher.ok=true while db.ok=false. If the
# gate text-matched `"ok": true` it would short-circuit on the FIRST probe, the
# phase-2 exhaustion warning would never print, and this case would fail.
run_gate "yes" 0 1
assert_eq "$RC" "0" "a body with backup_watcher.ok=true does NOT satisfy the db.ok poll"
assert_contains "$OUT" "db.ok stayed false" "the poll ran to EXHAUSTION (a text-match would have short-circuited)"
assert_not_contains "$OUT" "db.ok true" "never claims the data plane was observed ok"
assert_contains "$OUT" "health/ready 200" "and the strongest predicate still decides"

echo "6. the db.ok RETRY is exercised: false, then true"
# app probe consumes health_n=1; db poll 1 is health_n=2 (db.ok false);
# db poll 2 is health_n=3 (db.ok true).
run_gate "yes" 3 1
assert_eq "$RC" "0" "exits 0"
assert_contains "$OUT" "db.ok true" "observed the data plane"
assert_contains "$OUT" "health/ready 200" "reported readiness"
assert_eq "$HEALTH_POLLS" "3" "polled /health until db.ok flipped (positive control)"

echo "7. readiness answering 3xx → FAILS (curl -f would pass it)"
run_gate "yes" 1 0 302
assert_eq "$RC" "1" "exits 1 on a redirect"
assert_contains "$OUT" "last status 302" "reports the OBSERVED status"

echo "8. a BAD KNOB → exit 2 (NOT the bash-3.2 fail-open)"
# Deleting the validator leaves every other assertion green, so this case is the
# only CI pin on the fail-open fix.
bad_knob_rc=0
OUT=$(GATE_BASE="https://stub.invalid" DB_TRIES=abc bash "$GATE" 2>&1) || bad_knob_rc=$?
assert_eq "$bad_knob_rc" "2" "a non-integer knob exits 2"
assert_contains "$OUT" "DB_TRIES must be a non-negative integer" "names the bad knob"
assert_not_contains "$OUT" "db.ok" "never reaches a poll (fails before touching the endpoint)"

# and a VALID override still works, so the guard is not a blanket refusal
run_gate "yes" 1 1
assert_eq "$RC" "0" "a valid override still passes"

echo "9. an observed code + a non-zero curl exit → still SUCCEEDS"
# First poll: 503, curl exits 56. Second: 200, curl ALSO exits 56 (a transfer
# failure after the status line) — the app answered, so readiness WAS observed.
# Overwriting that 200 with the 000 sentinel would loop to exhaustion and redden
# a ready deploy.
#
# The READY_POLLS control is load-bearing: without it this case asserts only
# RC==0 + "health/ready 200", which is EXACTLY the state case 8 leaves behind —
# so the case could silently stop running (the missing-newline bug this file
# just fixed) and the harness would still report ALL PASSED.
run_gate "yes" 1 2 503 no 56
assert_eq "$RC" "0" "exits 0 — the observed 200 is not overwritten by 000"
assert_contains "$OUT" "health/ready 200" "reported readiness"
assert_eq "$READY_POLLS" "2" "polled twice — a state case 8 cannot produce (positive control)"

# NOTE: case 3 is ALSO the pin for `-o /dev/null` — the stub streams a body,
# so dropping the flag makes the captured status "<body>200", which never
# equals "200", and case 3 fails.

echo "10. readiness with NO response → FAILS and names the sentinel"
run_gate "yes" 1 0 503 yes
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "last status 000" "distinguishes a dead app from an unready one"

echo "11. db.ok never true + readiness never 200 → FAILS (#4771 is NOT a weakening)"
# The safety half of the #4771 contract. Readiness ANDs the SAME FalkorDB data
# plane on its OWN probe, so a genuinely unreachable DB fails BOTH. If the
# change had made readiness advisory (or if phase 3 were skipped), this case
# would exit 0 and the gate would be failing OPEN.
run_gate "yes" 0 0
assert_eq "$RC" "1" "exits 1 — readiness never 200, so the run FAILS"
assert_contains "$OUT" "/health/ready not 200" "names the readiness plane"
assert_contains "$OUT" "db.ok was ALSO never true" "records the db.ok observation in the failure too"
assert_contains "$OUT" "FalkorDB unreachable" "preserves the specific FalkorDB diagnosis"
assert_eq "$READY_POLLS" "4" "phase 3 was reached and polled to exhaustion (positive control)"

echo
# A LOST case must not be indistinguishable from success: deleting a case
# leaves FAIL=0 and merely a LOWER count, so the count is pinned too.
expected_assertions=41
if [ "$PASS" -eq "$expected_assertions" ]; then
  PASS=$((PASS + 1))
  echo "  ✅ assertion count pinned at $expected_assertions (a lost case is not a green run)"
else
  # Distinguish the two causes: a shortfall with failures already recorded is a
  # CONSEQUENCE of them, not an independent lost case. Reporting "a case was
  # lost" there misdirects the reader at every ordinary regression.
  if [ "$FAIL" -gt 0 ]; then
    echo "  ❌ only $PASS of $expected_assertions assertions ran — a consequence of the failures above"
  elif [ "$PASS" -gt "$expected_assertions" ]; then
    # The INVERSE cause: a case was ADDED without bumping the constant above.
    # Reporting "a case was LOST" here sends the reader hunting a deletion that
    # never happened — the same one-diagnostic-two-causes defect, one branch on.
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
