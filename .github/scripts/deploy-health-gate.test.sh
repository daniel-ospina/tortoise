#!/usr/bin/env bash
# deploy-health-gate.test.sh — self-check for
# .github/scripts/deploy-health-gate.sh (#4545).
#
# Run: bash .github/scripts/deploy-health-gate.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: stubs
# `curl` on PATH. No network, no Fly, no live service.
#
# Coverage:
#   1. app unreachable            → exit 1, fails FAST, and never reports a DB
#                                   wait (a dead app is a deploy failure, not a
#                                   slow database).
#   2. db.ok never true           → exit 1 with the FalkorDB message.
#   3. REGRESSION (#4545): db.ok true on the FIRST probe while /health/ready is
#      still 503, becoming 200 on a LATER probe → exit 0. This is the defect:
#      the gate used to assert readiness ONCE, 2-3 s after db.ok, and mark a
#      healthy deploy FAILED. It FAILS against the pre-fix single-shot gate
#      and passes only with the readiness poll.
#   4. readiness never 200        → exit 1 with the readiness message (the gate
#                                   stays FAIL-CLOSED: #3 does not weaken it).
#   5. the db.ok read is a JSON FIELD read, not a body text-match: every
#      non-ready body here carries `backup_watcher.ok = true` beside
#      `db.ok = false` — the exact shape that made a `grep '"ok": true'`
#      short-circuit and silently neutralise the 3-minute tolerance (#4470).
#   6. late data plane            → db.ok false for two probes then true, with
#      readiness immediately 200 → exit 0 (the original poll still works).
#   7. POSITIVE CONTROL: in case 3 the readiness endpoint is polled MORE THAN
#      ONCE — proving the poll ran rather than the deploy passing because the
#      assertion was skipped. (A test that cannot fail proves nothing.)
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
trap 'rm -rf "$WORK"' EXIT
STUB_BIN="$WORK/bin"
mkdir -p "$STUB_BIN"

# ── stub curl ───────────────────────────────────────────────────────────────
# The last argument is the URL. Counters live in $STUB_STATE so a scenario can
# serve "failing for the first N probes, then healthy" — the exact real shape.
cat > "$STUB_BIN/curl" <<'STUB'
#!/usr/bin/env bash
url="${@: -1}"
state="${STUB_STATE:?}"
bump() { # <file>
  local f="$state/$1" n
  n=$(cat "$f" 2>/dev/null || echo 0)
  n=$((n + 1))
  echo "$n" > "$f"
  echo "$n"
}
case "$url" in
  */health/ready)
    n=$(bump ready_n)
    if [ "${STUB_READY_OK_AFTER:-0}" -gt 0 ] && [ "$n" -ge "${STUB_READY_OK_AFTER}" ]; then
      exit 0
    fi
    exit 22
    ;;
  */health)
    n=$(bump health_n)
    [ "${STUB_APP_REACHABLE:-yes}" = "yes" ] || exit 22
    # NOTE: `backup_watcher.ok` is TRUE in BOTH branches — the body carries a
    # second `"ok": true` exactly as the real /health does (#4470), so a
    # text-matching reader would go green while db.ok is false.
    if [ "${STUB_DB_OK_AFTER:-0}" -gt 0 ] && [ "$n" -ge "${STUB_DB_OK_AFTER}" ]; then
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
# run_gate <app_reachable> <db_ok_after> <ready_ok_after>
#   → sets OUT (combined stdout+stderr) and RC (exit code)
run_gate() {
  local state="$WORK/state-$$-$RANDOM"
  mkdir -p "$state"
  local st=0
  OUT=$(PATH="$STUB_BIN:$PATH" \
    STUB_STATE="$state" \
    STUB_APP_REACHABLE="$1" \
    STUB_DB_OK_AFTER="$2" \
    STUB_READY_OK_AFTER="$3" \
    BASE="https://stub.invalid" \
    APP_PROBES=2 APP_PROBE_SLEEP=0 \
    DB_TRIES=3 DB_SLEEP=0 \
    READY_TRIES=4 READY_SLEEP=0 \
    bash "$GATE" 2>&1) || st=$?
  RC=$st
  READY_POLLS=$(cat "$state/ready_n" 2>/dev/null || echo 0)
}

echo "deploy-health-gate.test.sh (#4545)"
echo

echo "1. app unreachable → fail fast, no DB wait"
run_gate "no" 1 1
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "app unreachable" "reports app-unreachable"
assert_not_contains "$OUT" "db.ok" "never reports a DB wait"
assert_not_contains "$OUT" "health/ready" "never reaches the readiness assertion"

echo "2. db.ok never true → FalkorDB message"
run_gate "yes" 0 1
assert_eq "$RC" "1" "exits 1"
assert_contains "$OUT" "FalkorDB unreachable" "names the data plane"

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

echo "5. the db.ok read is a FIELD read, not a body text-match (#4470)"
# Every body above carries backup_watcher.ok=true while db.ok=false. If the
# gate text-matched `"ok": true` it would short-circuit on the FIRST probe and
# case 2 would exit 0 instead of 1.
run_gate "yes" 0 1
assert_eq "$RC" "1" "a body with backup_watcher.ok=true does NOT satisfy the db.ok poll"
assert_contains "$OUT" "FalkorDB unreachable" "correctly attributes the failure"

echo "6. late data plane, readiness immediate → SUCCEEDS"
run_gate "yes" 2 1
assert_eq "$RC" "0" "exits 0"
assert_contains "$OUT" "db.ok true" "observed the data plane"
assert_contains "$OUT" "health/ready 200" "reported readiness"

echo
echo "──────────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
  echo "✅ ALL PASSED — $PASS assertions"
  exit 0
fi
echo "❌ $FAIL FAILED, $PASS passed"
exit 1
