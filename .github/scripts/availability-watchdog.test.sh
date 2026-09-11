#!/usr/bin/env bash
# availability-watchdog.test.sh — self-check for
# .github/scripts/availability-watchdog.sh (#2850).
#
# Run: bash .github/scripts/availability-watchdog.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: stubs
# curl / gh / flyctl on PATH and drives the watchdog with simulated probe
# results. No network, no GitHub, no Fly.
#
# Coverage:
#   Verdicts
#     1. 200  → UP, exit 0 (no incident filed)
#     2. 401  → UP, exit 0 (the EXPECTED unauthenticated /v1/teams answer)
#     3. 403  → UP, exit 0
#     4. 429  → UP, exit 0 (the app answered; it is throttling us)
#     5. 000 (timeout) ×3 → DOWN, exit 1, exactly PROBE_ATTEMPTS probes
#     6. 503 ×3 → DOWN, exit 1
#     7. 599 → DOWN (the whole 5xx family)
#     8. 500 then 200 → UP, exit 0 (a retry absorbs a transient blip)
#     9. 404 → UNEXPECTED (route gone), exit 1, DEGRADED title, no restart
#    10. 302 → UNEXPECTED, exit 1, no restart
#   Dedupe (#2706 failure mode)
#    11. new incident → exactly ONE issue, marker title, auto-filed label
#    12. existing open incident → NO new issue, body PATCHed, count incremented
#    13. a repeat inside the throttle window does NOT comment (but still counts)
#    14. a repeat past the window DOES comment with the escalated count
#    15. a failed issue SEARCH refuses to file (never duplicate) + exit 1
#    16. a failed issue CREATE fails the run (never silently unalerted)
#    17. missing GH_TOKEN → fail closed before probing (never a deaf monitor)
#    18. recovery: comments "Recovered" + closes the incident
#    19. recovery with no incident → no comments, no closes
#   Self-healing (rate limited)
#    20. down < sustained window → no restart, reason in the body
#    21. sustained + token + started machine → ONE restart, marker recorded
#    22. inside the cooldown → no restart
#    23. cap reached (2 restarts in the last hour) → no restart + HUMAN ask
#    24. restarts outside the rolling hour do not count → restart allowed
#    25. sustained but no FLY_API_TOKEN → no restart, secret named in a comment
#    26. drill PROBE_URL → restart DISARMED (a drill must not touch prod)
#    27. UNEXPECTED verdict → never restarts
#    28. no STARTED machine → restart fails loudly, escalated to a human
#    29. flyctl restart errors → restart fails loudly, escalated
#    30. flyctl absent entirely → restart fails loudly, escalated
#   Paging
#    31. a new incident pages (Telegram) once
#    32. no Telegram secrets → page skipped, no call
#    33. repeat runs do NOT re-page
#   Hygiene
#    32. public body: credentials in PROBE_URL are redacted
#    33. the watchdog-state block round-trips through the issue body
#
# Fixtures are simulated; the real watchdog is the script under test.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHDOG="$SCRIPT_DIR/availability-watchdog.sh"

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
    *"$2"*) bad "$3 (unexpectedly found '$2')" ;;
    *) ok "$3" ;;
  esac
}

FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"' EXIT
BIN="$FIX/bin"
BIN_NOFLY="$FIX/bin-no-fly"
mkdir -p "$BIN" "$BIN_NOFLY"
export STUB_TMP="$FIX/stub"
mkdir -p "$STUB_TMP"

# ── stub: curl ──────────────────────────────────────────────────────────────
cat > "$BIN/curl" <<'CURL_EOF'
#!/usr/bin/env bash
# Handles exactly the two shapes the watchdog uses:
#   probe:    curl -sS -o FILE -w '<fmt>' --connect-timeout N --max-time N URL
#   telegram: curl -sS --max-time 15 -o /dev/null URL --data-urlencode k=v ...
out_file=""; write_fmt=""; url=""; data=""
args=("$@"); i=0
while [ $i -lt ${#args[@]} ]; do
  a="${args[$i]}"
  case "$a" in
    -o) out_file="${args[$((i+1))]:-}"; i=$((i+2)) ;;
    -w) write_fmt="${args[$((i+1))]:-}"; i=$((i+2)) ;;
    --data-urlencode) data="${data}${data:+&}${args[$((i+1))]:-}"; i=$((i+2)) ;;
    --connect-timeout|--max-time|-H) i=$((i+2)) ;;
    -s|-sS|-S|-L|-k|--fail) i=$((i+1)) ;;
    *) if [ -z "$url" ]; then url="$a"; fi; i=$((i+1)) ;;
  esac
done

case "$url" in
  *api.telegram.org*)
    echo "CURL telegram $data" >> "$STUB_TMP/calls.log"
    if [ "${STUB_TELEGRAM_FAIL:-0}" = "1" ]; then
      # curl echoes the URL (which carries the bot token) in its error text.
      echo "curl: (6) Could not resolve host: $url" >&2
      exit 1
    fi
    printf '{"ok":true}' > /dev/null
    exit 0 ;;
  *generate_204*|*control.example*)
    # Runner-side egress control. Deliberately does NOT touch probe.count: the
    # control is not an app probe, so probe-count assertions stay exact.
    n=0
    [ -f "$STUB_TMP/control.count" ] && n="$(cat "$STUB_TMP/control.count")"
    n=$((n + 1))
    echo "$n" > "$STUB_TMP/control.count"
    IFS=',' read -r -a ccodes <<< "${STUB_CONTROL_CODES:-204}"
    cidx=$((n - 1)); [ "$cidx" -ge "${#ccodes[@]}" ] && cidx=$(( ${#ccodes[@]} - 1 ))
    ccode="${ccodes[$cidx]}"
    echo "CURL control #${n} code=${ccode} url=${url}" >> "$STUB_TMP/calls.log"
    # The control probe asks for -w '%{http_code}' ONLY: emit the bare code, or
    # the caller's `case 2??` would never match "204 0.05".
    if [ -n "$write_fmt" ]; then printf '%s' "$ccode"; fi
    exit 0 ;;
esac

# probe: cycle through the configured code sequence; the last value repeats.
n=0
[ -f "$STUB_TMP/probe.count" ] && n="$(cat "$STUB_TMP/probe.count")"
n=$((n + 1))
echo "$n" > "$STUB_TMP/probe.count"
IFS=',' read -r -a codes <<< "${STUB_PROBE_CODES:-200}"
idx=$((n - 1)); [ "$idx" -ge "${#codes[@]}" ] && idx=$(( ${#codes[@]} - 1 ))
code="${codes[$idx]}"
echo "CURL probe #${n} code=${code} url=${url}" >> "$STUB_TMP/calls.log"
[ -n "$out_file" ] && printf '%s' "${STUB_PROBE_BODY:-}" > "$out_file"
if [ -n "$write_fmt" ]; then printf '%s %s' "$code" "${STUB_PROBE_TIME:-0.42}"; fi
exit 0
CURL_EOF

# ── stub: gh ────────────────────────────────────────────────────────────────
cat > "$BIN/gh" <<'GH_EOF'
#!/usr/bin/env bash
# Handles exactly the shapes the watchdog uses (all `gh api ...`).
# NB: brace-bearing defaults live in VARIABLES, never inside ${VAR:-{...}} —
# bash mis-parses that form and emits an extra trailing '}' (observed on the
# macOS bash 3.2 dev box), which silently corrupts the JSON fixture.
DEFAULT_ITEMS_JSON='{"items":[]}'
[ "${1:-}" = "api" ] || { echo "GH unexpected: $*" >&2; exit 1; }
path="${2:-}"; method="GET"; input=0
shift 2 || true
while [ $# -gt 0 ]; do
  case "$1" in
    --method) method="$2"; shift 2 ;;
    --input) input=1; shift ;;
    --jq) shift 2 ;;
    *) shift ;;
  esac
done
payload=""
if [ "$input" = "1" ]; then payload="$(cat)"; fi
echo "GH $method ${path%%\?*}" >> "$STUB_TMP/calls.log"

case "$path" in
  search/issues*)
    [ "${STUB_SEARCH_FAIL:-0}" = "1" ] && { echo "gh: search failed" >&2; exit 1; }
    # The full query is logged so a test can prove WHICH dedupe key was used.
    echo "GH-Q $path" >> "$STUB_TMP/calls.log"
    # Marker-aware: only the seeded KIND answers, exactly as production search
    # would (an encoded marker match). Unset → always answer.
    if [ -n "${STUB_SEARCH_MARKER:-}" ]; then
      case "$path" in
        *"$STUB_SEARCH_MARKER"*) printf '%s' "${STUB_SEARCH_JSON:-$DEFAULT_ITEMS_JSON}" ;;
        *) printf '%s' "$DEFAULT_ITEMS_JSON" ;;
      esac
    else
      printf '%s' "${STUB_SEARCH_JSON:-$DEFAULT_ITEMS_JSON}"
    fi ;;
  */comments)
    echo "GH-COMMENT" >> "$STUB_TMP/calls.log"
    [ "${STUB_COMMENT_FAIL:-0}" = "1" ] && { echo "gh: comment failed" >&2; exit 1; }
    # ONE compact line per comment — the harness counts comments by lines.
    printf '%s' "$payload" | jq -c . >> "$STUB_TMP/comments.log"
    printf '{}' ;;
  */issues)
    printf '%s' "$payload" | jq -c . > "$STUB_TMP/created.json"
    [ "${STUB_CREATE_FAIL:-0}" = "1" ] && { echo "gh: create failed" >&2; exit 1; }
    printf '{"number":%s}' "${STUB_NEW_ISSUE:-900}" ;;
  */issues/*)
    if [ "$method" = "GET" ]; then
      [ "${STUB_GET_BODY_FAIL:-0}" = "1" ] && { echo "gh: body read failed" >&2; exit 1; }
      if [ -f "$STUB_TMP/issue.json" ]; then cat "$STUB_TMP/issue.json"; else printf '{"body":""}'; fi
    else
      [ "${STUB_PATCH_FAIL:-0}" = "1" ] && { echo "gh: patch failed" >&2; exit 1; }
      # Every state write is appended (compact) — a recovery run PATCHes the
      # body AND then closes, and both matter to the assertions.
      printf '%s' "$payload" | jq -c . >> "$STUB_TMP/patched.log"
      printf '{}'
    fi ;;
  *) printf '{}' ;;
esac
exit 0
GH_EOF

# ── stub: flyctl ────────────────────────────────────────────────────────────
cat > "$BIN/flyctl" <<'FLY_EOF'
#!/usr/bin/env bash
DEFAULT_MACHINES_JSON='[{"id":"8654509b634758","state":"started"}]'
echo "FLYCTL $*" >> "$STUB_TMP/calls.log"
case "${1:-} ${2:-}" in
  "machine list") [ "${STUB_FLY_LIST_FAIL:-0}" = "1" ] && { echo "fly: api down" >&2; exit 1; }
                  printf '%s' "${STUB_FLY_MACHINES:-$DEFAULT_MACHINES_JSON}" ;;
  "machine restart") if [ "${STUB_FLY_RESTART_FAIL:-0}" = "1" ]; then
                    if [ "${STUB_FLY_LEAK:-0}" = "1" ]; then
                      echo "Error: unauthorized: token ${FLY_API_TOKEN:-leaked} rejected" >&2
                    else
                      echo "fly: restart failed" >&2
                    fi
                    exit 1
                  fi
                  printf 'restarted\n' ;;  *) printf '{}' ;;
esac
exit 0
FLY_EOF

chmod +x "$BIN/curl" "$BIN/gh" "$BIN/flyctl"
cp "$BIN/curl" "$BIN_NOFLY/curl"
cp "$BIN/gh" "$BIN_NOFLY/gh"
chmod +x "$BIN_NOFLY/curl" "$BIN_NOFLY/gh"

# ── base env ────────────────────────────────────────────────────────────────
export PATH="$BIN:$PATH"
export GH_TOKEN="test-token"
export GITHUB_REPOSITORY="daniel-ospina/tortoise"
export PROBE_RETRY_SLEEP_S=0          # no real sleeps in the harness
export PROBE_ATTEMPTS=3
unset GITHUB_ACTIONS TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID FLY_API_TOKEN || true
unset WATCHDOG_NOW_EPOCH PROBE_URL ALERT_LABEL STUB_PROBE_BODY || true

# A fixed clock so cooldown/velocity arithmetic is deterministic.
NOW=1800000000

reset_case() {
  : > "$STUB_TMP/calls.log"
  rm -f "$STUB_TMP/stderr.log"
  rm -f "$STUB_TMP/probe.count" "$STUB_TMP/control.count" "$STUB_TMP/created.json" "$STUB_TMP/patched.log" \
        "$STUB_TMP/comments.log" "$STUB_TMP/issue.json"
  unset STUB_PROBE_CODES STUB_PROBE_BODY STUB_PROBE_TIME STUB_SEARCH_JSON \
        STUB_SEARCH_FAIL STUB_SEARCH_MARKER STUB_CREATE_FAIL STUB_NEW_ISSUE \
        STUB_FLY_MACHINES STUB_FLY_LIST_FAIL STUB_FLY_RESTART_FAIL STUB_FLY_LEAK \
        STUB_TELEGRAM_FAIL STUB_COMMENT_FAIL STUB_GET_BODY_FAIL \
        STUB_CONTROL_CODES \
        PROBE_URL FLY_API_TOKEN TELEGRAM_BOT_TOKEN \
        TELEGRAM_CHAT_ID PROBE_HOST_LABEL STUB_PATCH_FAIL \
        RECOVERY_CONFIRM_PROBES SUSTAINED_MIN_RUNS PROBE_ATTEMPTS \
        MAX_RESTARTS_PER_HOUR STALE_RESET_MINUTES CONTROL_URL 2>/dev/null || true
  export GH_TOKEN="test-token"
  export WATCHDOG_NOW_EPOCH="$NOW"
  export PATH="$BIN:$PATH"
}

run_watchdog() { # -> RC (exit code), OUT (stderr/log), OUT_STDOUT (must be empty)
  set +e
  local so
  so="$("$WATCHDOG" 2>"$STUB_TMP/stderr.log")"
  RC=$?
  OUT="$([ -f "$STUB_TMP/stderr.log" ] && cat "$STUB_TMP/stderr.log" || true)"
  OUT_STDOUT="$so"
  set +e
  # ENFORCED ON EVERY CASE: stdout is DATA only. Every helper that returns a
  # value on stdout is called inside $( ), so a log line that escaped to stdout
  # would be captured into that value (this class already bit us once: a
  # warning merged into the __ERR__ sentinel). Asserting it here means the
  # invariant can never silently regress.
  if [ -n "$OUT_STDOUT" ]; then
    bad "stdout is DATA-only — leaked: $(first_chars_stub "$OUT_STDOUT")"
  fi
}

run_watchdog_no_flyctl() { # -> RC, OUT, OUT_STDOUT (PATH without flyctl)
  set +e
  local so
  # NB: PATH is set with the assignment PREFIX, not `env PATH=… cmd`. The
  # `env VAR=… cmd` form is a git-bearing-script false positive for
  # main-worktree-guard (#1484), which made this harness unrunnable in a hub
  # session (and hid the whole suite from reviewers).
  so="$(PATH="$BIN_NOFLY:/usr/bin:/bin:/usr/sbin:/sbin" "$WATCHDOG" 2>"$STUB_TMP/stderr.log")"
  RC=$?
  OUT="$([ -f "$STUB_TMP/stderr.log" ] && cat "$STUB_TMP/stderr.log" || true)"
  OUT_STDOUT="$so"
  set +e
  if [ -n "$OUT_STDOUT" ]; then
    bad "stdout is DATA-only — leaked: $(first_chars_stub "$OUT_STDOUT")"
  fi
}

first_chars_stub() { printf '%s' "$1" | head -c 200 || true; }

# seed an existing open incident in the stub's issue store
seed_issue() { # <kind> <first_failure_ts> <down_runs> <last_comment_ts> <restarts> [number]
  # The real writer emits restarts as ts,ts — normalize any space-separated
  # input so a fixture cannot silently seed a DIFFERENT state than production.
  local restarts
  restarts="$(printf '%s' "$5" | tr ' ' ',')"
  jq -n --arg b "<!-- watchdog-state kind=$1 first_failure_ts=$2 down_runs=$3 last_comment_ts=$4 restarts=$restarts -->" \
    '{body:$b}' > "$STUB_TMP/issue.json"
  STUB_SEARCH_JSON="{\"items\":[{\"number\":${6:-42},\"title\":\"[monitor] PROD DOWN — seeded\"}]}"
  export STUB_SEARCH_JSON
  # Only the seeded KIND matches the search (jq @uri encodes spaces as %20).
  if [ "$1" = "down" ]; then
    export STUB_SEARCH_MARKER='PROD%20DOWN'
  else
    export STUB_SEARCH_MARKER='PROD%20DEGRADED'
  fi
}

num_lines() { # <file>
  [ -f "$1" ] && wc -l < "$1" | tr -d ' ' || echo 0
}
count_calls() { # <pattern>
  grep -c -- "$1" "$STUB_TMP/calls.log" 2>/dev/null || true
}
created_json()  { [ -f "$STUB_TMP/created.json" ] && cat "$STUB_TMP/created.json" || echo '{}'; }
# The body PATCHes only (a close PATCH carries no body). The body is
# multi-line, so slurp the whole log and take the LAST object that has one.
patched_body()  { if [ -f "$STUB_TMP/patched.log" ]; then jq -r -s '[.[] | select(.body != null and .body != "")] | last | .body // ""' "$STUB_TMP/patched.log"; else echo ''; fi; }
patched_all()   { [ -f "$STUB_TMP/patched.log" ] && cat "$STUB_TMP/patched.log" || echo ''; }
comments_all()  { [ -f "$STUB_TMP/comments.log" ] && cat "$STUB_TMP/comments.log" || echo ''; }

echo "availability-watchdog.test.sh — #2850 taxonomy"
echo

# ── 1-4: the UP family ──────────────────────────────────────────────────────
for pair in "200:200 OK" "401:401 missing session token" "403:403 forbidden" "429:429 throttled"; do
  code="${pair%%:*}"; label="${pair#*:}"
  reset_case
  STUB_PROBE_CODES="$code"
  export STUB_PROBE_CODES
  run_watchdog
  assert_eq "$RC" "0" "HTTP ${label} → exit 0 (the app answered)"
  assert_eq "$(count_calls 'GH POST .*/issues$')" "0" "HTTP ${code} → no issue filed"
  assert_eq "$(cat "$STUB_TMP/probe.count")" "2" "HTTP ${code} → one probe + one recovery-confirmation probe"
done

# ── 5-7: the DOWN family ────────────────────────────────────────────────────
for pair in "000:timeout/connection error" "503:503" "599:599"; do
  code="${pair%%:*}"; label="${pair#*:}"
  reset_case
  STUB_PROBE_CODES="$code"
  export STUB_PROBE_CODES
  run_watchdog
  assert_eq "$RC" "1" "HTTP ${label} ×3 → exit 1 (workflow fails, notifications fire)"
  assert_eq "$(cat "$STUB_TMP/probe.count")" "3" "HTTP ${label} → retried PROBE_ATTEMPTS=3 times"
  assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "HTTP ${label} → exactly one incident issue"
done

# ── 8: a retry absorbs a transient blip ─────────────────────────────────────
reset_case
export STUB_PROBE_CODES="500,200"
run_watchdog
assert_eq "$RC" "0" "500 then 200 → exit 0 (retry absorbed a transient blip)"
assert_eq "$(cat "$STUB_TMP/probe.count")" "3" "500 then 200 → 2 attempts + 1 recovery-confirmation probe"
assert_eq "$(count_calls 'GH POST .*/issues$')" "0" "500 then 200 → no incident filed"

# ── 9-10: UNEXPECTED never restarts ─────────────────────────────────────────
reset_case
export STUB_PROBE_CODES="404"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$RC" "1" "404 → exit 1 (the route is gone)"
assert_contains "$(created_json)" "PROD DEGRADED" "404 → DEGRADED title (not PROD DOWN)"
assert_eq "$(count_calls 'FLYCTL')" "0" "404 → no restart attempted"

reset_case
export STUB_PROBE_CODES="302"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$RC" "1" "302 → exit 1"
assert_eq "$(count_calls 'FLYCTL')" "0" "302 → no restart attempted"

# ── 11: a new incident files exactly one issue ──────────────────────────────
reset_case
export STUB_PROBE_CODES="000"
run_watchdog
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "new incident → exactly 1 issue created"
assert_contains "$(created_json)" "[monitor] PROD DOWN" "new incident → title carries the dedupe marker"
assert_contains "$(created_json)" "auto-filed" "new incident → uses the pre-existing auto-filed label"
assert_contains "$(patched_body)" "down_runs=1" "new incident → state block records down_runs=1"
assert_contains "$(patched_body)" "kind=down" "new incident → state block records the kind"

# ── 12: an existing open incident is adopted, never duplicated ──────────────
reset_case
seed_issue down "$((NOW - 300))" 1 0 ""
export STUB_PROBE_CODES="000"
run_watchdog
assert_eq "$(count_calls 'GH POST .*/issues$')" "0" "repeat → NO new issue (dedupe)"
assert_contains "$(patched_body)" "down_runs=2" "repeat → the body count increments (2)"
assert_eq "$(count_calls 'GH-Q.*label')" "0" "dedupe search is TITLE-ONLY (a renamed label cannot cause duplicate spam)"
assert_eq "$(count_calls 'GH-Q.*is%3Aopen')" "1" "dedupe search only considers OPEN issues"

# ── 13-14: comment throttling ───────────────────────────────────────────────
reset_case
seed_issue down "$((NOW - 600))" 2 "$((NOW - 300))" ""
export STUB_PROBE_CODES="000"
run_watchdog
assert_eq "$(num_lines "$STUB_TMP/comments.log")" "0" "repeat inside the throttle window → no comment"
assert_contains "$(patched_body)" "down_runs=3" "throttled repeat → the count still increments (3)"

reset_case
seed_issue down "$((NOW - 1800))" 5 "$((NOW - 1200))" ""
export STUB_PROBE_CODES="000"
run_watchdog
assert_eq "$(num_lines "$STUB_TMP/comments.log")" "1" "repeat past the throttle window → one comment"
assert_contains "$(comments_all)" "Still DOWN" "throttled repeat comment names the state"
assert_contains "$(comments_all)" "run #6" "throttled repeat comment carries the count"

# ── 15-16: never spam, never go deaf ────────────────────────────────────────
reset_case
export STUB_PROBE_CODES="000"
export STUB_SEARCH_FAIL=1
run_watchdog
assert_eq "$RC" "1" "search failure → exit 1"
assert_eq "$(count_calls 'GH POST .*/issues$')" "0" "search failure → REFUSES to file (never duplicate)"

reset_case
export STUB_PROBE_CODES="000"
export STUB_CREATE_FAIL=1
run_watchdog
assert_eq "$RC" "1" "create failure → exit 1 (never silently unalerted)"
assert_contains "$OUT" "could not file" "create failure → loud error"

reset_case
unset GH_TOKEN
run_watchdog
assert_eq "$RC" "1" "missing GH_TOKEN → exit 1 (fail closed)"
assert_contains "$OUT" "deaf monitor" "missing GH_TOKEN → names the deaf-monitor risk"
assert_eq "$(count_calls 'CURL probe')" "0" "missing GH_TOKEN → never even probes"

# ── 17-18: recovery ─────────────────────────────────────────────────────────
reset_case
seed_issue down "$((NOW - 1800))" 4 0 ""
export STUB_PROBE_CODES="200"
run_watchdog
assert_eq "$RC" "0" "recovery → exit 0"
assert_contains "$(comments_all)" "Recovered" "recovery → comments 'Recovered'"
assert_contains "$(patched_all)" "\"state\":\"closed\"" "recovery → closes the incident"
assert_eq "$(count_calls 'GH PATCH repos/.*/issues/42$')" "1" "recovery → closes THE seeded incident (#42), not a duplicate"

reset_case
export STUB_PROBE_CODES="200"
run_watchdog
assert_eq "$RC" "0" "healthy with no incident → exit 0"
assert_eq "$(num_lines "$STUB_TMP/comments.log")" "0" "healthy with no incident → no comments"
assert_eq "$(count_calls 'GH PATCH')" "0" "healthy with no incident → no state writes"

# ── 19: down < sustained window → no restart ────────────────────────────────
reset_case
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL')" "0" "down 0 min → no restart (below the sustained window)"
assert_contains "$(patched_body)" "waits 10 min" "down 0 min → body explains the sustained window"

# ── 20: sustained + armed → exactly one restart ─────────────────────────────
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart 8654509b634758')" "1" "sustained 20 min → restart issued once"
assert_contains "$(patched_body)" "restarts=$NOW" "restart → recorded in the state block"
assert_contains "$(comments_all)" "Self-heal" "restart → commented on the incident"
assert_eq "$(count_calls 'FLYCTL machine list')" "1" "restart → fleet list read once"

# ── 21: cooldown ────────────────────────────────────────────────────────────
reset_case
seed_issue down "$((NOW - 3600))" 5 0 "$((NOW - 300))"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "restarted 5 min ago → cooldown blocks"
assert_contains "$(patched_body)" "cooldown" "cooldown → body explains why"

# ── 22: velocity cap ────────────────────────────────────────────────────────
reset_case
seed_issue down "$((NOW - 7200))" 20 0 "$((NOW - 1500)) $((NOW - 1300))"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "2 restarts in the last hour → cap blocks"
assert_contains "$(comments_all)" "HUMAN" "cap → asks for a human (the AWS 'get a human involved' leg)"
assert_contains "$(patched_body)" "velocity cap" "cap → body explains the velocity cap"

# ── 23: the rolling window is 1 hour ────────────────────────────────────────
reset_case
seed_issue down "$((NOW - 7200))" 9 0 "$((NOW - 5400)) $((NOW - 5000))"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "1" "restarts 90+ min ago → outside the window, restart allowed"

# ── 24: sustained but the token is missing ──────────────────────────────────
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
run_watchdog
assert_eq "$(count_calls 'FLYCTL')" "0" "missing FLY_API_TOKEN → restart leg inert"
assert_contains "$(comments_all)" "FLY_API_TOKEN" "missing token → a comment names the exact secret to create"

# ── 25: a drill can never restart production NOR touch its incident ────────
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export PROBE_URL="https://staging.example.test/v1/teams"
run_watchdog
assert_eq "$RC" "1" "drill → still alerts (exit 1)"
assert_eq "$(count_calls 'FLYCTL')" "0" "drill → restart DISARMED"
assert_contains "$(created_json)" "DRILL DOWN" "drill → files its OWN (drill-titled) incident, never a production one"
assert_contains "$(created_json)" "[DRILL]" "drill → the issue does not read as a production outage"
assert_eq "$(count_calls 'GH-Q.*DRILL%20DOWN')" "1" "drill → the dedupe key is the DRILL marker"
assert_eq "$(count_calls 'GH PATCH repos/.*/issues/42$')" "0" "drill → never MUTATES the production incident (#42 carries its restart ledger)"
assert_contains "$(patched_body)" "disarmed" "drill → the drill incident's body says self-healing is disarmed"

# ── 26: the cooldown survives ACROSS runs (state lives in the body) ────────
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "1" "cross-run: run 1 restarts"
# Feed run 1's written state back as run 2's issue (5 minutes later).
jq -n --arg b "$(patched_body)" '{body:$b}' > "$STUB_TMP/issue.json"
STUB_SEARCH_JSON='{"items":[{"number":42,"title":"seeded"}]}'
export STUB_SEARCH_JSON
export STUB_SEARCH_MARKER='PROD%20DOWN'
export WATCHDOG_NOW_EPOCH="$((NOW + 300))"
: > "$STUB_TMP/calls.log"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "cross-run: run 2 (5 min later) is inside the cooldown — no second restart"
assert_contains "$(patched_body)" "down_runs=5" "cross-run: the count keeps incrementing across runs (seeded 3 → 4 → 5)"
export WATCHDOG_NOW_EPOCH="$NOW"

# ── 27: restart failures escalate, never loop ───────────────────────────────
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export STUB_FLY_MACHINES='[{"id":"m1","state":"stopped"}]'
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "no STARTED machine → no restart"
assert_contains "$(comments_all)" "FAILED" "no STARTED machine → escalated loudly"
assert_contains "$(comments_all)" "human must intervene" "no STARTED machine → asks for a human"

reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export STUB_FLY_RESTART_FAIL=1
run_watchdog
assert_contains "$(comments_all)" "FAILED" "restart error → escalated loudly"
assert_contains "$(patched_body)" "restarts=$NOW" "failed attempt → STILL recorded (it counts against the cap — no 5-min retry storm)"
assert_contains "$(comments_all)" "COUNTED against" "failed attempt → the comment says it was counted"

reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog_no_flyctl
assert_contains "$(comments_all)" "flyctl is not installed" "flyctl absent → escalated, not crashed"
assert_contains "$(comments_all)" "FLY_API_TOKEN" "flyctl absent → points at the token secret"

# ── 29-31: paging ───────────────────────────────────────────────────────────
reset_case
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$(count_calls 'CURL telegram')" "1" "new incident → paged once"

reset_case
export STUB_PROBE_CODES="000"
run_watchdog
assert_eq "$(count_calls 'CURL telegram')" "0" "no Telegram secrets → page skipped, no call"

reset_case
seed_issue down "$((NOW - 600))" 2 "$((NOW - 300))" ""
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$(count_calls 'CURL telegram')" "0" "repeat run → does NOT re-page"

# ── 34: the velocity cap is a STATE — do not re-page/comment every run ──────
reset_case
seed_issue down "$((NOW - 7200))" 20 0 "$((NOW - 1500)) $((NOW - 1300))"
# cap_notified_ts is part of the state block; seed a RECENT notification.
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 last_down_ts=$((NOW - 60)) last_comment_ts=$((NOW - 60)) cap_notified_ts=$((NOW - 300)) restarts=$((NOW - 1500)),$((NOW - 1300)) -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON='{"items":[{"number":42,"title":"seeded"}]}'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$RC" "1" "cap still in force → the run is a normal DOWN run (not a silent death)"
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "cap still in force → no restart"
assert_contains "$OUT" "cap still in force" "cap still in force → logged"
assert_eq "$(num_lines "$STUB_TMP/comments.log")" "0" "cap re-notify window → NO repeat comment"
assert_eq "$(count_calls 'CURL telegram')" "0" "cap re-notify window → NO repeat page"

# ── 35: a DRILL must not resolve (close) a live production incident (P0) ────
reset_case
seed_issue down "$((NOW - 600))" 2 0 ""
export STUB_PROBE_CODES="200"
export PROBE_URL="https://staging.example.test/v1/teams"
run_watchdog
assert_eq "$RC" "0" "drill UP → exit 0"
assert_eq "$(count_calls 'GH PATCH repos/.*/issues/42$')" "0" "drill UP → does NOT close the production incident"
assert_eq "$(num_lines "$STUB_TMP/comments.log")" "0" "drill UP → does NOT comment Recovered on the production incident"
assert_contains "$OUT" "resolving only DRILL incidents" "drill UP → says so loudly"

# ── 36: no restart without a durably recorded attempt (write-then-act) ─────
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export STUB_PATCH_FAIL=1
run_watchdog
assert_eq "$RC" "1" "state write fails → exit 1"
assert_eq "$(count_calls 'FLYCTL')" "0" "state write fails → NO restart (the cooldown/cap record must exist first)"
assert_contains "$OUT" "could not record the restart attempt" "state write fails → says exactly why"

# ── 37: a stale/reopened incident cannot authorise an instant restart ──────
reset_case
seed_issue down "$((NOW - 2592000))" 99 "$((NOW - 5184000))" ""   # 30 days ago
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 2592000)) down_runs=99 last_down_ts=$((NOW - 2592000)) last_comment_ts=0 cap_notified_ts=0 restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON='{"items":[{"number":42,"title":"seeded"}]}'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL')" "0" "30-day-old incident → NO restart on the first failing run"
assert_contains "$OUT" "stale incident" "stale clock → logged"
assert_contains "$(patched_body)" "down_runs=100" "stale clock → the sustained window restarts but observed runs keep counting (99 → 100)"
assert_contains "$(patched_body)" "waits 10 min" "stale clock → the body explains the fresh sustained window"

# ── 38: garbage state values must not crash the shell (octal trap) ─────────
reset_case
printf '%s' '{"body":"<!-- watchdog-state kind=down first_failure_ts=1800000000 down_runs=08 last_down_ts=0 last_comment_ts=0 cap_notified_ts=0 restarts= -->"}' > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON='{"items":[{"number":42,"title":"seeded"}]}'
export STUB_PROBE_CODES="000"
run_watchdog
assert_eq "$RC" "1" "down_runs=08 (octal) → a normal DOWN run, not a crash"
assert_not_contains "$OUT" "value too great for base" "down_runs=08 → no arithmetic abort"
assert_contains "$(patched_body)" "down_runs=9" "down_runs=08 → normalized to 8 then incremented to 9"

# ── 39: a garbage numeric env must not fabricate an outage ─────────────────
reset_case
export STUB_PROBE_CODES="000"
export PROBE_ATTEMPTS=0
run_watchdog
assert_eq "$(cat "$STUB_TMP/probe.count")" "3" "PROBE_ATTEMPTS=0 → clamped to 3 (never DOWN from zero probes)"
assert_eq "$RC" "1" "PROBE_ATTEMPTS=0 → still a real DOWN after real probes"

# ── 40: recovery hysteresis — one flapping success must not close ──────────
reset_case
seed_issue down "$((NOW - 600))" 2 0 ""
export STUB_PROBE_CODES="200,000"   # first probe UP, confirmation probe DOWN
run_watchdog
assert_eq "$RC" "0" "flapping → exit 0"
assert_eq "$(count_calls 'GH PATCH repos/.*/issues/42$')" "0" "flapping → incident left OPEN (no close)"
assert_contains "$OUT" "recovery NOT confirmed" "flapping → logged as unconfirmed recovery"

# ── 41: the UP path must not stay GREEN when it cannot do its job ──────────
reset_case
seed_issue down "$((NOW - 600))" 2 0 ""
export STUB_PROBE_CODES="200"
export STUB_PATCH_FAIL=1                  # the recovery close (PATCH) fails
run_watchdog
assert_eq "$RC" "1" "recovery close fails → exit 1 (not a green run with a stale open incident)"
assert_contains "$OUT" "could not close" "recovery close fails → says exactly why"

# ── 42: a future state clock is clamped (no negative durations, no inert heal) ─
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW + 86400)) down_runs=5 last_down_ts=$((NOW + 86400)) last_comment_ts=0 cap_notified_ts=0 restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON='{"items":[{"number":42,"title":"seeded"}]}'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$RC" "1" "future clock → a normal DOWN run"
assert_not_contains "$(patched_body)" "down for -" "future clock → no negative duration is published"
assert_eq "$(count_calls 'FLYCTL')" "0" "future clock → no restart (clock clamped to now)"

# ── 33: a failing page must not leak the bot token into the log ─────────────
reset_case
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token-secret"
export TELEGRAM_CHAT_ID="12345"
export STUB_TELEGRAM_FAIL=1
run_watchdog
assert_contains "$OUT" "telegram page failed" "page failure → logged, non-fatal"
assert_not_contains "$OUT" "tg-token-secret" "page failure → the bot token is REDACTED from the public log"
assert_contains "$OUT" "<redacted>" "page failure → redaction marker present"

# ── 32: the state block round-trips through the issue body ──────────────────
reset_case
export STUB_PROBE_CODES="000"
run_watchdog
jq -n --arg b "$(patched_body)" '{body:$b}' > "$STUB_TMP/issue.json"
STUB_SEARCH_JSON='{"items":[{"number":42,"title":"seeded"}]}'
export STUB_SEARCH_JSON
export STUB_SEARCH_MARKER='PROD%20DOWN'
run_watchdog
assert_contains "$(patched_body)" "down_runs=2" "state block round-trips (run 1 wrote it, run 2 read it)"
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "round-trip run → no duplicate issue (still 1 create total)"

# ── 33: public-body hygiene ─────────────────────────────────────────────────
reset_case
export STUB_PROBE_CODES="000"
export PROBE_URL="https://user:s3cr3t@staging.example.test/v1/teams"
run_watchdog
assert_not_contains "$(patched_body)" "s3cr3t" "credentials in PROBE_URL are redacted from the public body"
assert_not_contains "$(created_json)" "s3cr3t" "credentials in PROBE_URL never reach the public TITLE either"
assert_contains "$(patched_body)" "<redacted>" "redaction marker present"

# ── 42: query-string credentials are redacted too ──────────────────────────
reset_case
export STUB_PROBE_CODES="000"
export PROBE_URL="https://api.premiselabs.co/v1/teams?debug=1&token=qs3cr3t"
run_watchdog
assert_not_contains "$(patched_body)" "qs3cr3t" "query-string credentials are redacted from the public body"
assert_contains "$(patched_body)" "?<redacted>" "the query string is redacted"

# ── 43: a stale-clock reset must PRESERVE the restart ledger ────────────────
# The ledger is the only cooldown/cap memory. Clearing it on a stale reset
# disarms the velocity cap (a restart storm), and pinning down_runs at 0
# starves the SUSTAINED_MIN_RUNS gate forever (no self-heal at all).
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 3000)) down_runs=5 last_down_ts=$((NOW - 3000)) last_comment_ts=0 cap_notified_ts=0 restarts=$((NOW - 2400)),$((NOW - 2100)) -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON='{"items":[{"number":42,"title":"seeded"}]}'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL')" "0" "stale reset → no restart this run (the sustained window restarts)"
assert_contains "$(patched_body)" "restarts=$((NOW - 2400)),$((NOW - 2100))" "stale reset → the restart ledger is PRESERVED"
# 10 min later the fresh window has elapsed — the PRESERVED ledger must still cap.
jq -n --arg b "$(patched_body)" '{body:$b}' > "$STUB_TMP/issue.json"
: > "$STUB_TMP/calls.log"
export WATCHDOG_NOW_EPOCH="$((NOW + 600))"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "preserved ledger → the velocity cap still blocks 10 min later (no cap bypass)"
assert_contains "$(patched_body)" "velocity cap" "preserved ledger → the body reports the cap"
export WATCHDOG_NOW_EPOCH="$NOW"

# ── 43b: a stale reset must not starve self-heal forever ──────────────────
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 3000)) down_runs=5 last_down_ts=$((NOW - 3000)) last_comment_ts=0 cap_notified_ts=0 restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON='{"items":[{"number":42,"title":"seeded"}]}'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL')" "0" "stale reset → run A waits"
jq -n --arg b "$(patched_body)" '{body:$b}' > "$STUB_TMP/issue.json"
: > "$STUB_TMP/calls.log"
export WATCHDOG_NOW_EPOCH="$((NOW + 600))"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "1" "stale reset → the NEXT sustained run still self-heals (the observed-run counter kept counting)"
export WATCHDOG_NOW_EPOCH="$NOW"

# ── 44: a malformed restart ledger must not crash or bypass the arithmetic ──
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 3600)) down_runs=5 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts=0008 -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON='{"items":[{"number":42,"title":"seeded"}]}'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$RC" "1" "restarts=0008 (octal) → a normal DOWN run, not a silent shell death"
assert_not_contains "$OUT" "value too great for base" "restarts=0008 → no arithmetic abort"
assert_eq "$(count_calls 'FLYCTL machine restart')" "1" "restarts=0008 → normalized to an ANCIENT stamp (outside the hour) → restart allowed"

reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 3600)) down_runs=5 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts=99999999999999999999 -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON='{"items":[{"number":42,"title":"seeded"}]}'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$RC" "1" "a >12-digit ledger entry → a normal DOWN run"
assert_not_contains "$OUT" "integer expression expected" "a >12-digit entry → no arithmetic error (it is DROPPED, not compared)"
assert_contains "$(patched_body)" "restarts=$NOW" "a >12-digit entry → the garbage is purged from the rewritten ledger"

# ── 45: unreadable incident state is NOT 'no state' (never erase the ledger) ─
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export STUB_GET_BODY_FAIL=1
run_watchdog
assert_eq "$RC" "1" "a failed body READ → exit 1 (monitor malfunction)"
assert_eq "$(count_calls 'FLYCTL')" "0" "a failed body READ → NO restart (unreadable state could hide the cap)"
assert_eq "$(count_calls 'GH PATCH')" "0" "a failed body READ → NO state write (which would ERASE the ledger)"
assert_contains "$OUT" "could not read the body" "a failed body READ → says exactly why"

# ── 46: the UP path must not stay GREEN when it cannot do its job ──────────
reset_case
seed_issue down "$((NOW - 600))" 2 0 ""
export STUB_PROBE_CODES="200"
export STUB_SEARCH_FAIL=1
run_watchdog
assert_eq "$RC" "1" "UP + search failure → exit 1 (cannot confirm incident state)"
assert_contains "$OUT" "search failed on the recovery path" "UP + search failure → says exactly why"

reset_case
seed_issue down "$((NOW - 600))" 2 0 ""
export STUB_PROBE_CODES="200"
export STUB_COMMENT_FAIL=1
run_watchdog
assert_eq "$RC" "1" "UP + comment failure → exit 1"
assert_eq "$(count_calls 'GH PATCH repos/.*/issues/42$')" "1" "UP + comment failure → the incident WAS closed first (close-then-comment, no half-close)"
assert_contains "$OUT" "was CLOSED but the 'Recovered' comment failed" "UP + comment failure → says exactly why (closed, record missing)"

# ── 47: an unconfirmed recovery with NO incident must not report green ─────
reset_case
export STUB_PROBE_CODES="200,000"
run_watchdog
assert_eq "$RC" "1" "flapping with no open incident → exit 1 (the observed DOWN is reported)"
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "flapping with no open incident → files the incident it would otherwise swallow"
assert_contains "$OUT" "NO incident is open" "flapping with no open incident → says exactly why"

# ── 48: a runner-side network failure must not restart production ──────────
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export STUB_CONTROL_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$RC" "1" "control probe fails → still alerts"
assert_eq "$(count_calls 'FLYCTL')" "0" "control probe fails → NO restart (a runner outage is not an app outage)"
assert_eq "$(count_calls 'CURL control')" "2" "control probe → retried CONTROL_ATTEMPTS=2 times"
assert_contains "$(patched_body)" "INCONCLUSIVE" "control probe fails → the body says INCONCLUSIVE"
assert_contains "$OUT" "control probe" "control probe fails → logged"

# ── 49: MAX_RESTARTS_PER_HOUR=0 is an operator kill switch, not a typo ─────
reset_case
seed_issue down "$((NOW - 3600))" 9 0 ""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export MAX_RESTARTS_PER_HOUR=0
run_watchdog
assert_eq "$(count_calls 'FLYCTL')" "0" "MAX_RESTARTS_PER_HOUR=0 → never restarts (kill switch honoured)"
assert_contains "$(patched_body)" "DISABLED" "MAX_RESTARTS_PER_HOUR=0 → the body says restarts are disabled"

# ── 50: the observed-run gate (not just the wall clock) ─────────────────────
# down_runs is incremented BEFORE the decision, so seeding 0 makes THIS run the
# first observation — the observed-run gate (≥2) must block even though the
# stored clock is already 20 min old.
reset_case
seed_issue down "$((NOW - 1200))" 0 0 ""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL')" "0" "20 min down but too few observed runs → no restart (SUSTAINED_MIN_RUNS gate)"
assert_contains "$(patched_body)" "required" "observed-run gate → the body explains the run requirement"

# ── 51: flyctl output echoed into a COMMENT must be redacted too ───────────
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token-secret"
export STUB_FLY_RESTART_FAIL=1
export STUB_FLY_LEAK=1
run_watchdog
assert_contains "$OUT" "automatic restart failed" "restart error → escalated"
assert_not_contains "$(comments_all)" "fly-token-secret" "the PUBLIC comment never carries the token echoed by flyctl"
assert_contains "$(comments_all)" "<redacted>" "the comment shows the redaction marker instead"
assert_not_contains "$OUT" "fly-token-secret" "the public LOG never carries the token either"
assert_not_contains "$(patched_body)" "fly-token-secret" "the public body never carries the token either"

# ── 52: the cap escalation is write-then-act (never re-page every run) ─────
reset_case
seed_issue down "$((NOW - 7200))" 20 0 "$((NOW - 1500)) $((NOW - 1300))"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON='{"items":[{"number":42,"title":"seeded"}]}'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
export STUB_PATCH_FAIL=1
run_watchdog
assert_eq "$RC" "1" "cap escalation with a failed write → exit 1"
assert_contains "$OUT" "could not record the cap notification" "cap escalation with a failed write → says exactly why"
assert_eq "$(count_calls 'CURL telegram')" "0" "cap escalation with a failed write → NO page (it would repeat every run)"

# ── 53: a FUTURE ledger stamp must be clamped, not trusted ────────────────
# Untrusted: `now - ts` is negative, so the cooldown reads as satisfied forever
# → self-heal permanently inert AND the cap branch unreachable (no escalation).
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 3600)) down_runs=5 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts=$((NOW + 86400)) -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON='{"items":[{"number":42,"title":"seeded"}]}'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL')" "0" "a future ledger stamp → clamped to now → the COOLDOWN governs (no inert wait, no restart)"
assert_contains "$(patched_body)" "cooldown" "a future ledger stamp → the body explains the cooldown"
assert_contains "$(patched_body)" "restarts=$NOW" "a future ledger stamp → rewritten clamped (no future stamp is persisted)"

# ── 54: a drill UP resolves its OWN incident (so drills cannot accumulate) ─
reset_case
printf '%s' '{"body":"<!-- watchdog-state kind=down first_failure_ts=1800000000 down_runs=3 last_down_ts=0 last_comment_ts=0 cap_notified_ts=0 restarts= -->"}' > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='DRILL%20DOWN'
export STUB_SEARCH_JSON='{"items":[{"number":777,"title":"[monitor] DRILL DOWN — staged"}]}'
export STUB_PROBE_CODES="200"
export PROBE_URL="https://staging.example.test/v1/teams"
run_watchdog
assert_eq "$RC" "0" "drill UP → exit 0"
assert_eq "$(count_calls 'GH PATCH repos/.*/issues/777$')" "1" "drill UP → closes its OWN drill incident (drills cannot accumulate)"
assert_eq "$(count_calls 'GH PATCH repos/.*/issues/42$')" "0" "drill UP → still never touches a production incident"

# ── 55: the page BOUNDARY redacts (a page carries flyctl's echoed output) ──
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token-secret"
export STUB_FLY_RESTART_FAIL=1
export STUB_FLY_LEAK=1
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$(count_calls 'CURL telegram')" "1" "page boundary → the escalation is paged once"
assert_not_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "fly-token-secret" "the PAGE payload never carries the token echoed by flyctl"
assert_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "<redacted>" "the page shows the redaction marker instead"

# ── 56: the INCONCLUSIVE (runner-egress) escalation is THROTTLED ──────────
# It is a human-escalation page for a condition that can persist for hours, so
# paging every run would be the notification-spam class this monitor exists to
# kill.
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 1200)) down_runs=3 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=$((NOW - 300)) restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_PROBE_CODES="000"
export STUB_CONTROL_CODES="000"
export FLY_API_TOKEN="fly-token"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$(count_calls 'CURL telegram')" "0" "inconclusive inside the re-notify window → NO page (bounded, not per-run)"
assert_contains "$OUT" "already escalated" "inconclusive inside the window → logged as throttled"

reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export STUB_CONTROL_CODES="000"
export FLY_API_TOKEN="fly-token"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$(count_calls 'CURL telegram')" "1" "inconclusive with a stale stamp → paged exactly once"
assert_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "INCONCLUSIVE" "the page names the inconclusive state"
assert_contains "$(patched_body)" "cap_notified_ts=$NOW" "inconclusive → the escalation stamp is durable BEFORE the page (write-then-act)"

reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export STUB_CONTROL_CODES="000"
export STUB_PATCH_FAIL=1
export FLY_API_TOKEN="fly-token"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$RC" "1" "inconclusive with a failed stamp write → exit 1"
assert_eq "$(count_calls 'CURL telegram')" "0" "inconclusive with a failed stamp write → NO page (it would repeat every run)"
assert_contains "$OUT" "could not record the inconclusive notification" "inconclusive with a failed stamp write → says exactly why"

# ── 57: no comment without a durable throttle stamp ────────────────────────
reset_case
seed_issue down "$((NOW - 1800))" 5 "$((NOW - 1200))" ""
export STUB_PROBE_CODES="000"
export STUB_PATCH_FAIL=1
run_watchdog
assert_eq "$RC" "1" "routine comment with a failed state write → exit 1"
assert_eq "$(num_lines "$STUB_TMP/comments.log")" "0" "routine comment with a failed state write → NO comment (it would repeat every run)"
assert_contains "$OUT" "NOT publishing a comment" "routine comment with a failed state write → says exactly why"

# ── 58: a persistently failing CLOSE must not re-post 'Recovered' ─────────
# The UP path has no throttle stamp — the CLOSED incident is what ends the
# repetition — so the close must happen BEFORE the comment.
reset_case
seed_issue down "$((NOW - 600))" 2 0 ""
export STUB_PROBE_CODES="200"
export STUB_PATCH_FAIL=1
run_watchdog
assert_eq "$RC" "1" "a failed close → exit 1"
assert_eq "$(num_lines "$STUB_TMP/comments.log")" "0" "a failed close → NO 'Recovered' comment (no unbounded repeat)"
assert_contains "$OUT" "could not close" "a failed close → says exactly why"

# ── 59: a future THROTTLE stamp is clamped (never mute the human channel) ──
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=$((NOW + 86400)) restarts=$((NOW - 1500)),$((NOW - 1300)) -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON='{"items":[{"number":42,"title":"seeded"}]}'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$(count_calls 'CURL telegram')" "1" "a FUTURE cap stamp → clamped → the cap escalation still pages (never muted)"
assert_contains "$(patched_body)" "cap_notified_ts=$NOW" "a future cap stamp → rewritten clamped"

echo
if [ "$FAIL" -eq 0 ]; then
  echo "availability-watchdog.test.sh: $PASS passed, 0 failed ✅"
  exit 0
fi
echo "availability-watchdog.test.sh: $PASS passed, $FAIL FAILED ❌"
exit 1
