#!/usr/bin/env bash
# availability-liveness.test.sh — self-check for
# .github/scripts/availability-liveness.sh (#4573).
#
# Run: bash .github/scripts/availability-liveness.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: stubs `gh`
# on PATH (no network, no GitHub) and pins "now" via LIVENESS_NOW_EPOCH.
#
# ⛔ NON-VACUITY IS THE POINT. A liveness check that cannot fail is the defect
# (#4573) restated, so the load-bearing cases are the ones where the checker
# MUST go red: a stale heartbeat, an absent heartbeat on an established monitor,
# an unparseable/unreadable record, and a failed search. Every one of them
# asserts BOTH the non-zero exit AND the filed/updated alert — a check that
# merely logs would pass a weaker test while proving nothing.
#
# Coverage:
#   Freshness
#     1.  a fresh heartbeat (5 min)            → exit 0, NO alert
#     2.  exactly at the threshold (90 min)    → exit 0 (the bound is `>`)
#     3.  one minute past the threshold        → exit 1, alert filed
#   Staleness
#     4.  a stale heartbeat (200 min)          → exit 1, alert carries marker+reason+threshold
#     5.  an existing open alert               → PATCHed, NOT duplicated
#     6.  an UNPARSEABLE record                → STALE (fail closed, never fresh)
#     7.  an UNREADABLE record                 → STALE (fail closed)
#     8.  a FUTURE record (clock skew)         → clamped, fresh (not stale)
#   Bootstrap (no record at all)
#     9.  workflow established (> threshold)   → exit 1, reason=no-heartbeat-record
#     10. workflow just created (< threshold)   → exit 0, not an alarm
#     11. workflow age unreadable               → STALE (fail closed)
#   Recovery
#     12. fresh + open alert                    → close + Recovered comment, exit 0
#     13. close fails                           → exit 1 (a stale open alert is not silent)
#   Fail-closed / security
#     14. a human-authored look-alike heartbeat → NOT adopted (treated as no record)
#     15. heartbeat search fails                → exit 1, NO alert filed (not a false page)
#     16. alert search fails                    → exit 1, no create/close
#     17. missing GH_TOKEN                       → exit 1 before any gh call
#     18. HEARTBEAT_MAX_AGE_MIN garbage/0        → normalized to the measured default
#     18f/18g/18h. int-overflow values (threshold, heartbeat_epoch, epoch:) are
#         unparseable → the default / STALE, never a wrapped-negative "LIVE"
#     19. the produced alert body NAMES the channel independence (not Telegram)
#   Fail-closed side effects
#     21. a stale alert that CANNOT be filed    → exit 1 (the run is the alert)
#     22. a recovery comment that fails          → exit 1 (not silent)
#     23. a stale alert body that cannot PATCH   → exit 1 (not silent)
#   Validation branches
#     24. a value above the 1..100000 bound      → rejected to the default
#     25. a value below the p95 (1..28)          → honoured, but WARNED
#     26. heartbeat_at=epoch:<valid>             → accepted (the writer's form)
#     27. a FUTURE workflow created_at           → clamped (bootstrap path)
#     28. jq absent                              → exit 1 (cannot parse = refuse)
#   Parity
#     20. the heartbeat TITLE + MARKER match the watchdog's byte-for-byte
#         (a rename on one side would otherwise alarm forever)
#
# Fixtures are simulated; the real checker is the script under test.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASH_BIN="$(command -v bash)"
CHECKER="$SCRIPT_DIR/availability-liveness.sh"
WATCHDOG_SCRIPT="$SCRIPT_DIR/availability-watchdog.sh"

HEARTBEAT_TITLE_FIXTURE='[OPS] availability-watchdog heartbeat (rolling)'
HEARTBEAT_MARKER_FIXTURE='<!-- availability-watchdog-heartbeat -->'
ALERT_TITLE_FIXTURE='[OPS] availability-watchdog LIVENESS — no heartbeat'
ALERT_MARKER_FIXTURE='<!-- availability-liveness-alert -->'

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; }
assert_eq() { if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 (got '$1', want '$2')"; fi; }
assert_contains() { case "$1" in *"$2"*) ok "$3" ;; *) bad "$3 (missing '$2')" ;; esac; }
assert_not_contains() { case "$1" in *"$2"*) bad "$3 (unexpectedly found '$2')" ;; *) ok "$3" ;; esac; }

FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"' EXIT
BIN="$FIX/bin"
mkdir -p "$BIN"
export STUB_TMP="$FIX/stub"
mkdir -p "$STUB_TMP"

# ── stub: gh ────────────────────────────────────────────────────────────────
cat > "$BIN/gh" <<'GH_EOF'
#!/usr/bin/env bash
[ "${1:-}" = "api" ] || { echo "GH unexpected: $*" >&2; exit 1; }
# NB: brace-bearing defaults live in a VARIABLE, never inside ${VAR:-{...}} —
# bash mis-parses that form and emits a trailing '}' (observed on macOS bash
# 3.2), which corrupts the JSON fixture and makes every search look FAILED.
DEFAULT_ITEMS_JSON='{"items":[]}'
path="${2:-}"; method="GET"; input=0; paginate=0
shift 2 || true
while [ $# -gt 0 ]; do
  case "$1" in
    --method) method="$2"; shift 2 2>/dev/null || shift ;;
    --input) input=1; shift ;;
    --jq) shift 2 2>/dev/null || shift ;;
    --paginate) paginate=1; shift ;;
    *) shift ;;
  esac
done
payload=""
if [ "$input" = "1" ]; then payload="$(cat)"; fi
echo "GH $method $path" >> "$STUB_TMP/calls.log"

case "$path" in
  search/issues*)
    [ "${STUB_SEARCH_FAIL:-0}" = "1" ] && { echo "gh: search failed" >&2; exit 1; }
    # ORDER MATTERS: the ALERT title ends "... no heartbeat", so a `*heartbeat*`
    # test would swallow the alert search. "LIVENESS" is unique to the alert.
    case "$path" in
      *LIVENESS*)
        [ "${STUB_ALERT_SEARCH_FAIL:-0}" = "1" ] && { echo "gh: alert search failed" >&2; exit 1; }
        printf '%s' "${STUB_ALERT_SEARCH_JSON:-$DEFAULT_ITEMS_JSON}" ;;
      *heartbeat*) printf '%s' "${STUB_HB_SEARCH_JSON:-$DEFAULT_ITEMS_JSON}" ;;
      *) printf '%s' "$DEFAULT_ITEMS_JSON" ;;
    esac ;;
  */comments)
    printf '%s' "$payload" | jq -c . >> "$STUB_TMP/comments.log"
    [ "${STUB_COMMENT_FAIL:-0}" = "1" ] && { echo "gh: comment failed" >&2; exit 1; }
    printf '{}' ;;
  */actions/workflows/*)
    [ "${STUB_WF_FAIL:-0}" = "1" ] && { echo "gh: workflow read failed" >&2; exit 1; }
    printf '{"created_at":"%s"}' "${STUB_WF_CREATED_AT:-2026-09-13T03:34:06Z}" ;;
  */issues)
    printf '%s' "$payload" | jq -c . > "$STUB_TMP/created.json"
    [ "${STUB_ALERT_CREATE_FAIL:-0}" = "1" ] && { echo "gh: create failed" >&2; exit 1; }
    printf '{"number":%s}' "${STUB_NEW_ALERT:-500}" ;;
  */issues/*)
    n="${path##*/}"
    if [ "$method" = "GET" ]; then
      [ "${STUB_GET_BODY_FAIL:-0}" = "1" ] && { echo "gh: body read failed" >&2; exit 1; }
      if [ "$n" = "${STUB_ALERT_ISSUE:-500}" ] && [ -f "$STUB_TMP/alert-issue.json" ]; then
        cat "$STUB_TMP/alert-issue.json"
      elif [ "$n" = "${STUB_HB_ISSUE:-7000}" ] && [ -f "$STUB_TMP/heartbeat-issue.json" ]; then
        cat "$STUB_TMP/heartbeat-issue.json"
      else
        printf '{"body":""}'
      fi
    else
      printf '%s' "$payload" | jq -c . >> "$STUB_TMP/patched.log"
      case "$payload" in *'"state":"closed"'*) echo "CLOSE $n" >> "$STUB_TMP/patched.log" ;; esac
      [ "${STUB_ALERT_PATCH_FAIL:-0}" = "1" ] && { echo "gh: patch failed" >&2; exit 1; }
      printf '{}'
    fi ;;
  *) printf '{}' ;;
esac
exit 0
GH_EOF
chmod +x "$BIN/gh"

# ── base env ────────────────────────────────────────────────────────────────
export PATH="$BIN:$PATH"
export GH_TOKEN="test-token"
export GITHUB_REPOSITORY="daniel-ospina/tortoise"
unset GITHUB_ACTIONS STUB_HB_SEARCH_JSON STUB_ALERT_SEARCH_JSON || true

# A fixed clock so age arithmetic is deterministic.
NOW=1800000000
HB_ISO="$(date -u -d "@$NOW" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$NOW" +%Y-%m-%dT%H:%M:%SZ)"

reset_case() {
  : > "$STUB_TMP/calls.log"
  rm -f "$STUB_TMP/created.json" "$STUB_TMP/patched.log" "$STUB_TMP/comments.log" \
        "$STUB_TMP/heartbeat-issue.json" "$STUB_TMP/alert-issue.json"
  unset STUB_SEARCH_FAIL STUB_HB_SEARCH_JSON STUB_ALERT_SEARCH_JSON STUB_ALERT_SEARCH_FAIL \
        STUB_ALERT_CREATE_FAIL STUB_NEW_ALERT STUB_ALERT_ISSUE STUB_HB_ISSUE \
        STUB_GET_BODY_FAIL STUB_ALERT_PATCH_FAIL STUB_COMMENT_FAIL \
        STUB_WF_FAIL STUB_WF_CREATED_AT HEARTBEAT_MAX_AGE_MIN 2>/dev/null || true
  export GH_TOKEN="test-token"
  export LIVENESS_NOW_EPOCH="$NOW"
  export PATH="$BIN:$PATH"
}

run_checker() { # -> RC, OUT
  _run_checker "$CHECKER" "$PATH"
}

# Same, but the checker runs with PATH="$BIN" ONLY — provably no jq. `bash` is
# resolved to an ABSOLUTE path here, in the harness's own PATH, and the shebang
# is bypassed, so no system bin dir (which may contain jq on usrmerged Ubuntu,
# where /bin -> /usr/bin) can leak into the child. Assuming "no jq under /bin"
# was the case-28 defect: it held on macOS and failed on the CI runner.
run_checker_no_jq() { # -> RC, OUT
  _run_checker "$CHECKER" "$BIN"
}

_run_checker() { # <script> <child-PATH>
  set +e
  local so
  so="$(PATH="$2" "$BASH_BIN" "$1" 2>"$STUB_TMP/stderr.log" </dev/null)"
  RC=$?
  OUT="$([ -f "$STUB_TMP/stderr.log" ] && cat "$STUB_TMP/stderr.log" || true)"
  OUT_STDOUT="$so"
  set +e
  [ -n "$OUT_STDOUT" ] && bad "stdout is DATA-only — leaked: $(printf '%s' "$OUT_STDOUT" | head -c 200)"
}

# Seed the rolling heartbeat issue with an age (minutes) in the past.
seed_heartbeat() { # <age-minutes> [number]
  local age="$1" n="${2:-7000}" at
  at=$((NOW - age * 60))
  printf '{"body":"%s\\nheartbeat_at=%s\\nheartbeat_epoch=%s\\nverdict=UP\\n"}' \
    "$HEARTBEAT_MARKER_FIXTURE" "$(date -u -d "@$at" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$at" +%Y-%m-%dT%H:%M:%SZ)" "$at" \
    > "$STUB_TMP/heartbeat-issue.json"
  STUB_HB_SEARCH_JSON="$(printf '{"items":[{"number":%s,"title":"%s","body":"%s","user":{"login":"github-actions[bot]","type":"Bot"}}]}' "$n" "$HEARTBEAT_TITLE_FIXTURE" "$HEARTBEAT_MARKER_FIXTURE")"
  export STUB_HB_SEARCH_JSON
}

seed_open_alert() { # [number]
  local n="${1:-500}"
  printf '{"body":"%s\\nliveness_state=stale\\n"}' "$ALERT_MARKER_FIXTURE" > "$STUB_TMP/alert-issue.json"
  STUB_ALERT_SEARCH_JSON="$(printf '{"items":[{"number":%s,"title":"%s","body":"%s","user":{"login":"github-actions[bot]","type":"Bot"}}]}' "$n" "$ALERT_TITLE_FIXTURE" "$ALERT_MARKER_FIXTURE")"
  export STUB_ALERT_SEARCH_JSON
}

# Seed an arbitrary heartbeat body (for adversarial/edge field values).
seed_heartbeat_raw() { # <body-json>
  printf '%s' "$1" > "$STUB_TMP/heartbeat-issue.json"
  STUB_HB_SEARCH_JSON="$(printf '{"items":[{"number":7000,"title":"%s","body":"%s","user":{"login":"github-actions[bot]","type":"Bot"}}]}' "$HEARTBEAT_TITLE_FIXTURE" "$HEARTBEAT_MARKER_FIXTURE")"
  export STUB_HB_SEARCH_JSON
}

count_calls() { grep -c -- "$1" "$STUB_TMP/calls.log" 2>/dev/null || true; }
created_json() { [ -f "$STUB_TMP/created.json" ] && cat "$STUB_TMP/created.json" || echo '{}'; }
patched_all() { [ -f "$STUB_TMP/patched.log" ] && cat "$STUB_TMP/patched.log" || echo ''; }

echo "availability-liveness.test.sh — #4573 liveness check"
echo

# 1. a fresh heartbeat → healthy, nothing filed.
reset_case
seed_heartbeat 5
run_checker
assert_eq "$RC" "0" "1: a 5-min-old heartbeat → exit 0 (pager LIVE)"
assert_eq "$(count_calls 'GH POST')" "0" "1: a fresh heartbeat files NO alert"

# 2. the bound is `>`: exactly at the threshold is still fresh.
reset_case
seed_heartbeat 90
run_checker
assert_eq "$RC" "0" "2: exactly 90 min old → exit 0 (the bound is strictly >)"

# 3. one minute past → stale + alert.
reset_case
seed_heartbeat 91
run_checker
assert_eq "$RC" "1" "3: 91 min old → exit 1 (STALE)"
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "3: …and files exactly one alert issue"

# 4. stale body content is diagnosable and names the channel independence.
reset_case
seed_heartbeat 200
run_checker
assert_contains "$(created_json)" "availability-liveness-alert" "4: alert carries the dedupe marker"
assert_contains "$(created_json)" "reason=heartbeat-too-old" "4: alert names the reason"
assert_contains "$(created_json)" "threshold_min=90" "4: alert names the measured threshold"
assert_contains "$(created_json)" "heartbeat_age_min=200" "4: alert carries the observed age"
assert_contains "$(created_json)" "not sent over Telegram" "4: alert STATES the channel independence"

# 5. an existing open alert is UPDATED, never duplicated.
reset_case
seed_heartbeat 300
seed_open_alert 500
run_checker
assert_eq "$RC" "1" "5: stale with an alert already open → exit 1"
assert_eq "$(count_calls 'GH POST .*/issues$')" "0" "5: …NO duplicate alert (dedupe by title+marker+author)"
assert_eq "$(count_calls 'GH PATCH repos/.*/issues/500$')" "1" "5: …the open alert is PATCHed"

# 6. an UNPARSEABLE record is stale, never fresh (fail closed).
reset_case
printf '{"body":"%s\\nno-time-here\\n"}' "$HEARTBEAT_MARKER_FIXTURE" > "$STUB_TMP/heartbeat-issue.json"
export STUB_HB_SEARCH_JSON="$(printf '{"items":[{"number":7000,"title":"%s","body":"%s","user":{"login":"github-actions[bot]","type":"Bot"}}]}' "$HEARTBEAT_TITLE_FIXTURE" "$HEARTBEAT_MARKER_FIXTURE")"
run_checker
assert_eq "$RC" "1" "6: an unparseable heartbeat → exit 1 (never read as fresh)"
assert_contains "$(created_json)" "reason=heartbeat-record-unparseable" "6: …reason is unparseable"

# 7. an UNREADABLE record is stale (fail closed).
reset_case
export STUB_HB_SEARCH_JSON="$(printf '{"items":[{"number":7000,"title":"%s","body":"%s","user":{"login":"github-actions[bot]","type":"Bot"}}]}' "$HEARTBEAT_TITLE_FIXTURE" "$HEARTBEAT_MARKER_FIXTURE")"
export STUB_GET_BODY_FAIL=1
run_checker
assert_eq "$RC" "1" "7: an unreadable heartbeat record → exit 1 (an unreadable record is not fresh)"
assert_contains "$(created_json)" "reason=heartbeat-record-unreadable" "7: …reason is unreadable"

# 8. a FUTURE timestamp (clock skew) is clamped, not treated as stale.
reset_case
printf '{"body":"%s\\nheartbeat_at=%s\\nheartbeat_epoch=%s\\n"}' "$HEARTBEAT_MARKER_FIXTURE" "$HB_ISO" "$((NOW + 3600))" > "$STUB_TMP/heartbeat-issue.json"
export STUB_HB_SEARCH_JSON="$(printf '{"items":[{"number":7000,"title":"%s","body":"%s","user":{"login":"github-actions[bot]","type":"Bot"}}]}' "$HEARTBEAT_TITLE_FIXTURE" "$HEARTBEAT_MARKER_FIXTURE")"
run_checker
assert_eq "$RC" "0" "8: a future-stamped heartbeat → clamped to now, exit 0"
assert_contains "$OUT" "heartbeat is 0 min old" "8: …the age is CLAMPED to 0 (an unclamped future stamp would log a negative age)"

# 9. no record + an ESTABLISHED workflow → alarm.
reset_case
export STUB_WF_CREATED_AT="2026-09-13T03:34:06Z"   # ancient relative to NOW
run_checker
assert_eq "$RC" "1" "9: no heartbeat + established workflow → exit 1"
assert_contains "$(created_json)" "reason=no-heartbeat-record" "9: …reason is no-heartbeat-record"
assert_contains "$(created_json)" "liveness_workflow_age_min=" "9: …and names the liveness feature's age"

# 10. no record + a JUST-CREATED workflow → not yet established, no alarm.
reset_case
WF_RECENT="$(date -u -d "@$((NOW - 120))" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$((NOW - 120))" +%Y-%m-%dT%H:%M:%SZ)"
export STUB_WF_CREATED_AT="$WF_RECENT"
run_checker
assert_eq "$RC" "0" "10: a fresh deploy with no record yet → exit 0 (nothing to verify)"
assert_eq "$(count_calls 'GH POST')" "0" "10: …and files nothing"

# 11. no record + unreadable workflow metadata → fail closed.
reset_case
export STUB_WF_FAIL=1
run_checker
assert_eq "$RC" "1" "11: heartbeat absent and workflow age unreadable → exit 1 (fail closed)"

# 12. recovery: fresh + an open alert → close, comment, exit 0.
reset_case
seed_heartbeat 3
seed_open_alert 500
run_checker
assert_eq "$RC" "0" "12: recovery → exit 0"
assert_contains "$(patched_all)" "CLOSE 500" "12: …the standing alert is CLOSED"
assert_contains "$(cat "$STUB_TMP/comments.log" 2>/dev/null || echo '')" "Recovered" "12: …with a Recovered comment"

# 13. a failing close is not silent.
reset_case
seed_heartbeat 3
seed_open_alert 500
export STUB_ALERT_PATCH_FAIL=1
run_checker
assert_eq "$RC" "1" "13: recovered but the alert could not be closed → exit 1 (not silent)"

# 14. a human-authored look-alike heartbeat is NOT adopted.
reset_case
export STUB_HB_SEARCH_JSON="$(printf '{"items":[{"number":7000,"title":"%s","body":"%s","user":{"login":"attacker","type":"User"}}]}' "$HEARTBEAT_TITLE_FIXTURE" "$HEARTBEAT_MARKER_FIXTURE")"
export STUB_WF_CREATED_AT="2026-09-13T03:34:06Z"
run_checker
assert_eq "$RC" "1" "14: a forged heartbeat look-alike is ignored → treated as no record (STALE)"
assert_contains "$(created_json)" "reason=no-heartbeat-record" "14: …and does NOT satisfy the check"

# 15. a failed heartbeat search fails the run but files NO false alarm.
reset_case
export STUB_SEARCH_FAIL=1
run_checker
assert_eq "$RC" "1" "15: heartbeat search failure → exit 1 (cannot assess is loud)"
assert_eq "$(count_calls 'GH POST')" "0" "15: …but does NOT file a false 'pager dead' alert"

# 16. a failed ALERT search is fail-closed: no create, no close, exit 1.
# The alert search is the SECOND search in a stale run; STUB_SEARCH_FAIL is
# all-or-nothing, so the alert half has its own targeted knob.
reset_case
seed_heartbeat 200
export STUB_ALERT_SEARCH_FAIL=1
run_checker
assert_eq "$RC" "1" "16: a failed alert search → exit 1 (refuses to create or resolve)"
assert_eq "$(count_calls 'GH POST')" "0" "16: …does NOT duplicate-file the alert on an unreadable search"
assert_eq "$(count_calls 'GH PATCH')" "0" "16: …and does NOT close anything on a bad read"

# 17. missing GH_TOKEN → fail before any gh call.
reset_case
unset GH_TOKEN
run_checker
assert_eq "$RC" "1" "17: missing GH_TOKEN → exit 1 (a deaf checker refuses to run)"
assert_eq "$(count_calls 'GH ')" "0" "17: …before any GitHub call"
export GH_TOKEN="test-token"

# 18. the threshold validator: only a plain integer is accepted. Digit-stripping
# (`2h` -> 2, `-1` -> 1, `0.5` -> 5) would turn a typo into a near-zero bound
# that alerts on nearly every run — the false-firing pager this issue is about.
reset_case
seed_heartbeat 30
export HEARTBEAT_MAX_AGE_MIN=0
run_checker
assert_eq "$RC" "0" "18: HEARTBEAT_MAX_AGE_MIN=0 is normalized to the default (a 30-min heartbeat stays fresh)"
assert_contains "$OUT" "threshold=90 min" "18: …and the ACTIVE threshold is the measured default 90 (not an empty/raw fallback)"

reset_case
seed_heartbeat 30
export HEARTBEAT_MAX_AGE_MIN=2h
run_checker
assert_eq "$RC" "0" "18b: '2h' is REJECTED to the 90-min default, not digit-stripped to 2 min"
assert_contains "$OUT" "threshold=90 min" "18b: …the ACTIVE threshold is 90 (mutation D: a %s fallback of '' errors the compare and mutes the check)"

reset_case
seed_heartbeat 30
export HEARTBEAT_MAX_AGE_MIN=-1
run_checker
assert_eq "$RC" "0" "18c: '-1' is rejected to the default, not read as 1 min"
assert_contains "$OUT" "threshold=90 min" "18c: …the ACTIVE threshold is 90, not the raw token"

reset_case
seed_heartbeat 30
export HEARTBEAT_MAX_AGE_MIN=1.5h
run_checker
assert_eq "$RC" "0" "18d: '1.5h' is rejected to the default, not read as 15 min"
assert_contains "$OUT" "threshold=90 min" "18d: …the ACTIVE threshold is 90, not 15"

# 18e. a VALID explicit integer is honoured (the fix is validation, not a mute
# that always returns the default). 60 min > the 30-min heartbeat → fresh; and
# it must NOT have been silently forced back to 90 (that would also be fresh), so
# drive the negative: a 70-min heartbeat is STALE under an honoured 60.
reset_case
seed_heartbeat 70
export HEARTBEAT_MAX_AGE_MIN=60
run_checker
assert_eq "$RC" "1" "18e: a valid explicit 60 is HONOURED (70-min heartbeat is stale, not defaulted to 90)"

# 18f. an OVER-INTMAX threshold must not silently mute the comparison. bash's
# `[ -gt ]` errors (exit 2) on an int64 overflow and a CONDITION reads that as
# FALSE, so before the digit-count bound the oversized value was returned and
# every heartbeat read LIVE. 20 digits must fall back to the default.
reset_case
seed_heartbeat 200
export HEARTBEAT_MAX_AGE_MIN=99999999999999999999
run_checker
assert_eq "$RC" "1" "18f: a 20-digit threshold falls back to the default (200-min heartbeat is still STALE, not muted)"

# 18g. an OVER-INTMAX heartbeat_epoch must not wrap to a negative age. Without a
# bounded digit run the future-clamp errors (false) and $(( )) wraps, so the age
# goes negative and the record reads as fresh.
reset_case
seed_heartbeat_raw "$(printf '{"body":"%s\\nheartbeat_epoch=99999999999999999999\\n"}' "$HEARTBEAT_MARKER_FIXTURE")"
run_checker
assert_eq "$RC" "1" "18g: an unbounded heartbeat_epoch is unparseable → STALE (an int-wrap must not read as LIVE)"

# 18h. the iso_to_epoch `epoch:` overflow guard is the SIBLING of 18g — the
# watchdog's fmt_iso fallback can write `heartbeat_at=epoch:<n>`, so this path is
# production-reachable, and an unbounded value would wrap the same way.
reset_case
seed_heartbeat_raw "$(printf '{"body":"%s\\nheartbeat_at=epoch:99999999999999999999\\n"}' "$HEARTBEAT_MARKER_FIXTURE")"
run_checker
assert_eq "$RC" "1" "18h: an unbounded heartbeat_at=epoch: is unparseable → STALE (the iso_to_epoch sibling of 18g)"
assert_contains "$OUT" "heartbeat-record-unparseable" "18h: …reason is unparseable, not a wrapped-negative age"

# 19. the alert body names the channel independence (the load-bearing property).
reset_case
seed_heartbeat 400
run_checker
assert_contains "$(created_json)" "not sent over Telegram" "19: the alert body states the alert does not travel the Telegram leg"
assert_contains "$(created_json)" "A working Telegram bot is NOT evidence" "19: …and why a live Telegram bot proves nothing about the pager"

# 20. PARITY: the heartbeat title + marker must match the watchdog byte-for-byte.
# A rename on either side makes the checker search for an issue the watchdog
# never writes, and it would then alarm forever.
watchdog_title="$(grep -m1 '^HEARTBEAT_TITLE=' "$WATCHDOG_SCRIPT" | cut -d= -f2- | tr -d "'\"")"
watchdog_marker="$(grep -m1 '^HEARTBEAT_MARKER=' "$WATCHDOG_SCRIPT" | cut -d= -f2- | tr -d "'\"")"
checker_title="$(grep -m1 '^HEARTBEAT_TITLE=' "$CHECKER" | cut -d= -f2- | tr -d "'\"")"
checker_marker="$(grep -m1 '^HEARTBEAT_MARKER=' "$CHECKER" | cut -d= -f2- | tr -d "'\"")"
assert_eq "$checker_title" "$watchdog_title" "20: HEARTBEAT_TITLE matches the watchdog's"
assert_eq "$checker_marker" "$watchdog_marker" "20: HEARTBEAT_MARKER matches the watchdog's"

# 21. a stale heartbeat whose alert CANNOT be filed still fails the run (the
# failing run IS the alert). STUB_ALERT_CREATE_FAIL was a dead seam until now.
reset_case
seed_heartbeat 200
export STUB_ALERT_CREATE_FAIL=1
run_checker
assert_eq "$RC" "1" "21: stale but the alert issue cannot be filed → exit 1 (the run itself is the alert)"
assert_contains "$OUT" "could not be filed" "21: …and NAMES the un-fileable alert (not just the generic stale failure)"

# 22. a recovery whose 'Recovered' comment fails is not silent.
reset_case
seed_heartbeat 3
seed_open_alert 500
export STUB_COMMENT_FAIL=1
run_checker
assert_eq "$RC" "1" "22: recovered and closed, but the Recovered comment failed → exit 1 (not silent)"
assert_contains "$(patched_all)" "CLOSE 500" "22: …the close itself succeeded; only the comment failed"
assert_contains "$OUT" "Recovered' comment failed" "22: …and NAMES the failed recovery comment"

# 23. stale + an open alert whose BODY PATCH fails → exit 1 (the sibling of the
# close-fail arm in 13 and the comment-fail arm in 22).
reset_case
seed_heartbeat 200
seed_open_alert 500
export STUB_ALERT_PATCH_FAIL=1
run_checker
assert_eq "$RC" "1" "23: stale but the alert body cannot be refreshed → exit 1 (the run is the alert)"
assert_contains "$OUT" "could not be updated" "23: …and NAMES the un-updatable alert body"

# 24. the UPPER half of the magnitude guard: a 6-digit value above the bound is
# rejected to the default (otherwise ~694 days would be honoured and mute the
# compare — the fail-open direction).
reset_case
seed_heartbeat 200
export HEARTBEAT_MAX_AGE_MIN=100001
run_checker
assert_eq "$RC" "1" "24: 100001 (above the 1..100000 bound) is rejected to the default → 200-min heartbeat is STALE"
assert_contains "$OUT" "threshold=90 min" "24: …the ACTIVE threshold is the measured default 90"

# 25. the below-p95 WARN fires but the value is still HONOURED. The heartbeat is
# 20 min — fresh under the DEFAULT 90 but STALE under the honoured 10, so this
# distinguishes "honoured" from "silently defaulted" (a 5-min fixture was fresh
# under both, so only the warn half was load-bearing).
reset_case
seed_heartbeat 20
export HEARTBEAT_MAX_AGE_MIN=10
run_checker
assert_eq "$RC" "1" "25: a valid 10-min bound is HONOURED (a 20-min heartbeat is STALE, not defaulted to 90)"
assert_contains "$OUT" "below the measured p95" "25: …and it WARNS that it sits inside the scheduling jitter"

# 26. the iso_to_epoch `epoch:<n>` ACCEPTANCE path (18h covers only rejection):
# the watchdog's fmt_iso fallback writes this exact form.
reset_case
seed_heartbeat_raw "$(printf '{"body":"%s\\nheartbeat_at=epoch:%s\\n"}' "$HEARTBEAT_MARKER_FIXTURE" "$((NOW - 1200))")"
run_checker
assert_eq "$RC" "0" "26: heartbeat_at=epoch:<valid> is PARSED (20-min heartbeat is fresh)"
assert_contains "$OUT" "heartbeat is 20 min old" "26: …the age is read from the epoch field"

# 27. the BOOTSTRAP path's own future clamp (a created_at ahead of now must not
# yield a negative feature age).
reset_case
WF_FUTURE="$(date -u -d "@$((NOW + 3600))" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$((NOW + 3600))" +%Y-%m-%dT%H:%M:%SZ)"
export STUB_WF_CREATED_AT="$WF_FUTURE"
run_checker
assert_eq "$RC" "0" "27: a future liveness-workflow created_at → clamped, not yet established, exit 0"
assert_contains "$OUT" "is only 0 min old" "27: …the feature age is CLAMPED to 0 (not negative)"
assert_eq "$(count_calls 'actions/workflows/availability-liveness.yml')" "1" "27: …the grace reads the LIVENESS workflow, not the watchdog's (keying it on the watchdog would alarm from the first run)"

# 28. a checker without jq refuses to run (fail closed). PATH="$BIN" ONLY — see
# run_checker_no_jq: /bin may itself contain jq on usrmerged Linux.
reset_case
run_checker_no_jq
assert_eq "$RC" "1" "28: jq absent → exit 1 (a checker that cannot parse refuses to run)"
assert_contains "$OUT" "jq is required" "28: …and says why"
assert_eq "$(count_calls 'GH ')" "0" "28: …before any GitHub call (a full run would prove jq WAS found)"

# 29. a heartbeat-search response that does not PARSE is a search failure, not
# "no record yet" — the __ERR__ arm of search_issue's jq guard. With a young
# workflow the collapse would read as "not yet established / LIVE".
reset_case
export STUB_HB_SEARCH_JSON='not-json'
export STUB_WF_CREATED_AT="$WF_RECENT"
run_checker
assert_eq "$RC" "1" "29: an unparseable heartbeat search → exit 1 (NOT downgraded to no-record)"
assert_contains "$OUT" "heartbeat search failed" "29: …and names the search failure"

# 30. a search result whose issue NUMBER is not numeric is a search failure, not
# an adoptable alert.
reset_case
seed_heartbeat 5
export STUB_ALERT_SEARCH_JSON="$(printf '{"items":[{"number":"abc","title":"%s","body":"%s","user":{"login":"github-actions[bot]","type":"Bot"}}]}' "$ALERT_TITLE_FIXTURE" "$ALERT_MARKER_FIXTURE")"
run_checker
assert_eq "$RC" "1" "30: a non-numeric issue number → exit 1 (never adopted as the open alert)"
assert_contains "$OUT" "liveness-alert search failed" "30: …and refuses to file or resolve"
assert_eq "$(count_calls 'GH PATCH')" "0" "30: …so nothing is closed on a garbage number"

# 31. a heartbeat body that does not PARSE → unreadable (the jq arm of
# get_issue_body, distinct from the gh-level failure in case 7).
reset_case
seed_heartbeat_raw 'not-json'
run_checker
assert_eq "$RC" "1" "31: an unparseable heartbeat BODY → exit 1 (fail closed)"
assert_contains "$OUT" "heartbeat-record-unreadable" "31: …reason is unreadable, not unparseable"

# 32. a create response whose number is not numeric is a FAILED create.
reset_case
seed_heartbeat 200
export STUB_NEW_ALERT='"abc"'
run_checker
assert_eq "$RC" "1" "32: a non-numeric created number → exit 1 (treated as unfiled)"
assert_contains "$OUT" "could not be filed" "32: …and names the un-fileable alert"

# 33. malformed workflow metadata fails closed to STALE (the jq fallback in
# workflow_created_at), rather than aborting outside the 0/1 contract.
reset_case
export STUB_WF_CREATED_AT='x"y'
run_checker
assert_eq "$RC" "1" "33: malformed workflow metadata → exit 1 (fail closed)"
assert_contains "$OUT" "no-heartbeat-record" "33: …reason is no-heartbeat-record, not an unreadable-age abort"

# 34. a non-numeric `epoch:` payload is unparseable (the inner digit guard in
# iso_to_epoch; 18h covers only the sibling length guard).
reset_case
seed_heartbeat_raw "$(printf '{"body":"%s\\nheartbeat_at=epoch:abc\\n"}' "$HEARTBEAT_MARKER_FIXTURE")"
run_checker
assert_eq "$RC" "1" "34: heartbeat_at=epoch:abc → exit 1 (unparseable, never a coerced 0)"
assert_contains "$OUT" "heartbeat-record-unparseable" "34: …reason is unparseable"

# 35. LEADING-ZERO epochs are all-digit and within the length bound, but bash's
# $(( )) reads them as OCTAL and aborts with "value too great for base" — killing
# the run BEFORE the alert is filed, so the record reaches neither the STALE path
# nor the durable alert. (The single-bracket compare is base-10 and does NOT
# error; the arithmetic is what aborts.)
reset_case
seed_heartbeat_raw "$(printf '{"body":"%s\\nheartbeat_epoch=000000000009\\n"}' "$HEARTBEAT_MARKER_FIXTURE")"
run_checker
assert_eq "$RC" "1" "35: a leading-zero heartbeat_epoch → exit 1 (decimal-normalized, not an octal abort)"
assert_contains "$(created_json)" "reason=heartbeat-too-old" "35: …the record reaches the STALE path"
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "35: …and the DURABLE alert is actually filed"

# 36. the same via the ISO fallback form the watchdog can write.
reset_case
seed_heartbeat_raw "$(printf '{"body":"%s\\nheartbeat_at=epoch:000000000009\\n"}' "$HEARTBEAT_MARKER_FIXTURE")"
run_checker
assert_eq "$RC" "1" "36: a leading-zero heartbeat_at=epoch: → exit 1 (decimal-normalized)"
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "36: …and the alert is filed"

# 37. the publication-boundary scrub is pinnable directly through the script's
# LIVENESS_LIB_ONLY seam (it is otherwise only exercised indirectly).
RAW_LEAKY='url=https://x?token=SUPERSECRET&k=1 FlyV1 abcDEF123 fm2_abcdefghijklmnopqrstuvwxyz 123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
REDACTED="$(LIVENESS_LIB_ONLY=1 "$BASH_BIN" -c '. "$1"; redact_text "$2"' _ "$CHECKER" "$RAW_LEAKY")"
assert_not_contains "$REDACTED" "SUPERSECRET" "37: a ?token= value is scrubbed at the publication boundary"
assert_not_contains "$REDACTED" "FlyV1 abcDEF123" "37: …a FlyV1 token is scrubbed"
assert_not_contains "$REDACTED" "fm2_abcdefghijklmnopqrstuvwxyz" "37: …an fm2_ key is scrubbed"
assert_not_contains "$REDACTED" "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" "37: …a Telegram-shaped token is scrubbed"
assert_contains "$REDACTED" "<redacted>" "37: …and the scrub is visible in the text"
# …and it does not mangle ordinary text.
assert_eq "$(LIVENESS_LIB_ONLY=1 "$BASH_BIN" -c '. "$1"; redact_text "$2"' _ "$CHECKER" 'plain message, no secrets')" "plain message, no secrets" "37: ordinary text passes through unchanged"

# 38. the search QUERY is constrained (an unpinned constraint is a silent
# behaviour change: without is:open, a CLOSED alert is re-adopted and PATCHed
# instead of a new issue being created, leaving the durable half of the alert
# dead). The stub now logs the full query, so this is observable.
reset_case
seed_heartbeat 5
run_checker
HB_QUERY="$(grep -m1 'search/issues' "$STUB_TMP/calls.log" 2>/dev/null || true)"
assert_contains "$HB_QUERY" "is%3Aopen" "38: the heartbeat search is constrained to OPEN issues"
assert_contains "$HB_QUERY" "in%3Atitle" "38: …searches the TITLE (the marker is a body comment)"
assert_contains "$HB_QUERY" "author%3Aapp%2Fgithub-actions" "38: …and to the Actions app"
assert_contains "$HB_QUERY" "repo%3A" "38: …and is scoped to THIS repo (a fleet sibling shares the app author)"
assert_contains "$HB_QUERY" "is%3Aissue" "38: …and to issues, so a bot PR with the same title is never adopted"

# 39. the jq adoption predicate's EXACT-TITLE select. GitHub's `in:title
# "<phrase>"` is a phrase match, not equality, so the jq equality is the only
# exactness guard: a bot issue titled "<title> EXTRA" carries the marker and
# would otherwise be adopted (and its fresh body read as LIVE).
reset_case
seed_heartbeat_raw "$(printf '{"body":"%s\\nheartbeat_epoch=%s\\n"}' "$HEARTBEAT_MARKER_FIXTURE" "$NOW")"
export STUB_HB_SEARCH_JSON="$(printf '{"items":[{"number":7000,"title":"%s EXTRA","body":"%s","user":{"login":"github-actions[bot]","type":"Bot"}}]}' "$HEARTBEAT_TITLE_FIXTURE" "$HEARTBEAT_MARKER_FIXTURE")"
export STUB_WF_CREATED_AT="2026-09-13T03:34:06Z"
run_checker
assert_eq "$RC" "1" "39: a same-author title that is not EXACT is NOT adopted (treated as no record)"
assert_contains "$(created_json)" "reason=no-heartbeat-record" "39: …reason is no-heartbeat-record"

# 40. the jq adoption predicate's BODY-MARKER select: a bot issue with the exact
# title but no marker must not be adopted either.
reset_case
seed_heartbeat_raw "$(printf '{"body":"heartbeat_epoch=%s\\n"}' "$NOW")"
export STUB_HB_SEARCH_JSON="$(printf '{"items":[{"number":7000,"title":"%s","body":"heartbeat_epoch=%s","user":{"login":"github-actions[bot]","type":"Bot"}}]}' "$HEARTBEAT_TITLE_FIXTURE" "$NOW")"
export STUB_WF_CREATED_AT="2026-09-13T03:34:06Z"
run_checker
assert_eq "$RC" "1" "40: a same-author exact-title issue WITHOUT the body marker is NOT adopted"
assert_contains "$(created_json)" "reason=no-heartbeat-record" "40: …reason is no-heartbeat-record"

# 41. the redaction must be applied AT the publication boundary, not merely
# available as a function. Drives the real helpers through the lib seam and
# inspects what the stub actually received.
reset_case
LIVENESS_LIB_ONLY=1 "$BASH_BIN" -c '. "$1"; create_issue "t https://x?token=SECRET" "b FlyV1 abcDEF123" >/dev/null' _ "$CHECKER"
assert_not_contains "$(created_json)" "SECRET" "41: create_issue redacts the TITLE at the boundary"
assert_not_contains "$(created_json)" "FlyV1 abcDEF123" "41: …and the BODY"
assert_contains "$(created_json)" "<redacted>" "41: …and the scrub is applied"
LIVENESS_LIB_ONLY=1 "$BASH_BIN" -c '. "$1"; update_issue_body 500 "b fm2_abcdefghijklmnopqrstuvwxyz" >/dev/null' _ "$CHECKER"
assert_not_contains "$(patched_all)" "fm2_abcdefghijklmnopqrstuvwxyz" "41: update_issue_body redacts at the boundary"
assert_contains "$(patched_all)" "<redacted>" "41: …and the update payload is the REDACTED text, not emptied"
LIVENESS_LIB_ONLY=1 "$BASH_BIN" -c '. "$1"; comment_issue 500 "c 123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" >/dev/null' _ "$CHECKER"
assert_not_contains "$(cat "$STUB_TMP/comments.log" 2>/dev/null || echo '')" "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" "41: comment_issue redacts at the boundary"

# 42. the CLOCK is an arithmetic operand too: the seam value is normalized like
# any epoch, so a leading-zero clock cannot abort the run.
reset_case
seed_heartbeat 5
export LIVENESS_NOW_EPOCH="0$NOW"   # the same instant, leading-zero form
run_checker
assert_eq "$RC" "0" "42: a leading-zero clock is normalized (same instant, not an octal abort)"

# 43. the BOOTSTRAP epoch is normalized too — the sibling of 35/36 for the
# workflow created_at (a non-ISO `created_at` that iso_to_epoch accepts).
reset_case
export STUB_WF_CREATED_AT="epoch:000000000009"
run_checker
assert_eq "$RC" "1" "43: a leading-zero workflow created_at → exit 1 (decimal-normalized, not an octal abort)"
assert_contains "$(created_json)" "reason=no-heartbeat-record" "43: …the record reaches the STALE path"
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "43: …and the durable alert is filed"

# 44. a USELESS clock pin (all-zero) must not read LIVE. The fallback is the REAL
# wall clock, so the heartbeat is seeded 120 min before *that* (the harness's pin
# is a fixed instant, so a fixture relative to it would be in the future and
# clamp to fresh). Without the guard NOW="" → the age goes negative → LIVE.
reset_case
REAL_NOW="$(date -u +%s)"
seed_heartbeat_raw "$(printf '{"body":"%s\\nheartbeat_epoch=%s\\n"}' "$HEARTBEAT_MARKER_FIXTURE" "$((REAL_NOW - 7200))")"
export LIVENESS_NOW_EPOCH=0
run_checker
assert_eq "$RC" "1" "44: an all-zero clock pin falls back to the real clock (a 120-min heartbeat is STALE, not LIVE)"
assert_contains "$OUT" "ignoring the pin" "44: …and it says the pin was ignored"
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "44: …and the alert is filed"

# 45. an OVER-INTMAX clock pin (would wrap in $(( ))) is likewise unusable.
reset_case
REAL_NOW="$(date -u +%s)"
seed_heartbeat_raw "$(printf '{"body":"%s\\nheartbeat_epoch=%s\\n"}' "$HEARTBEAT_MARKER_FIXTURE" "$((REAL_NOW - 7200))")"
export LIVENESS_NOW_EPOCH=18446744073709551616
run_checker
assert_eq "$RC" "1" "45: an over-intmax clock pin falls back to the real clock (STALE, not LIVE)"

echo
if [ "$FAIL" -eq 0 ]; then
  echo "availability-liveness.test.sh: $PASS passed, 0 failed ✅"
  exit 0
fi
echo "availability-liveness.test.sh: $PASS passed, $FAIL FAILED ❌"
exit 1
