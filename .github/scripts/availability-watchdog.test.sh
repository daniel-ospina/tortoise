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
#     2. 401  → UP, exit 0 (the EXPECTED unauthenticated /v1/organizations answer)
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
#   Hardening (review findings on #3064 — each of these FAILS on the pre-fix
#   code and passes only with the fix)
#    60. the dedupe search carries the machine-author constraint
#    61. a forged HUMAN-authored look-alike incident is never adopted (P1)
#    62. the sustained window is clamped to the server-side created_at (P2)
#    63. an unusable server-side anchor fails closed (P2)
#    64. an omitted last_down_ts is UNKNOWN, not 'just now' (P2)
#    65. a >300-char token never leaks as a PREFIX (redact BEFORE truncate, P2)
#    66. a Fly-SHAPED token of unknown value is redacted too (P2)
#   44b/c/d. a corrupt `restarts=` ledger fails closed (P2 — refused, not dropped)
#    Round 2 (each FAILS on the round-1 code):
#    67/67b. a WRAPPED `FlyV1 <macaroon>` token (split by \n, \r\n and a space)
#           is redacted — scrub_output() must REFLOW first, THEN redact (P1)
#    68. a URI whose userinfo contains a '/' never leaks the password through
#        the published host label, and the same guard covers redact_url() (P2)
#    69. the restart budget survives an incident close/reopen (flapping) (P2)
#    70. an unreadable previous ledger fails CLOSED (no restart) (P2)
#    71/72. DNS and TLS/certificate failures do NOT restart (P2)
#    73/74/75. transport failures / 5xx still do (the contrast cases)
#    Round 3 (each FAILS on the round-2 code):
#    76. a Telegram HTTP 4xx is a failure, not a silent success (--fail-with-body)
#    77. another App's bot (renovate[bot]) is not adopted (reserved login only)
#    78. adoption needs the EXACT title AND the body marker, not loose terms
#    79. a corrupt SEEDED ledger names the SOURCE issue, not the new one
#    80. a bare fm2_ macaroon fragment is shape-redacted
#    81. the run log records the restart verdict
#    82. a real certificate message under a generic code is still TLS
#    Round 4 (each FAILS on the round-3 code):
#    83. the adoption marker survives render_body — run 2's search fixture is
#        built from run 1's REAL published body, not a synthetic marker
#    84. the fail-closed ledger verdict is DURABLE and RESUMABLE (a corrupt or
#        unreadable source ledger cannot be silently replaced by an empty budget)
#    70b. an unreadable previous ledger persists `ledger_state=unreadable` and
#        the next run RETRIES the lookup instead of arming with an empty budget
#
# Fixtures are simulated; the real watchdog is the script under test.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHDOG="$SCRIPT_DIR/availability-watchdog.sh"

# The EXACT production titles + body marker the watchdog now demands before it
# adopts a returned search item (round 3, P2-2/P2-3). Fixtures that expect
# adoption MUST use these verbatim; anything else is treated as NOT ours.
DOWN_TITLE_FIXTURE='[monitor] PROD DOWN — api.premiselabs.co is not answering the availability probe'
DEGRADED_TITLE_FIXTURE='[monitor] PROD DEGRADED — api.premiselabs.co answered the availability probe unexpectedly'
DRILL_DOWN_TITLE_FIXTURE='[monitor] DRILL DOWN — staging.example.test [DRILL] is not answering the availability probe'
INCIDENT_STATE_MARKER_FIXTURE='<!-- availability-watchdog-state -->'

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
assert_not_empty() { # <value> <label>
  # A DERIVED value that came back empty must FAIL: every assert_contains is a
  # `case` glob, so an empty needle matches any haystack. Without this, a
  # quoted/folded/anchored source value silently vacates the assertions built
  # on it — the guard would pass while checking nothing (found in review).
  if [ -n "$1" ]; then ok "$2"; else bad "$2 (derived an EMPTY value — the guard would be vacuous)"; fi
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
out_file=""; write_fmt=""; url=""; data=""; fail_body=0; hdr_file=""
args=("$@"); i=0
while [ $i -lt ${#args[@]} ]; do
  a="${args[$i]}"
  case "$a" in
    -o) out_file="${args[$((i+1))]:-}"; i=$((i+2)) ;;
    -D) hdr_file="${args[$((i+1))]:-}"; i=$((i+2)) ;;
    -w) write_fmt="${args[$((i+1))]:-}"; i=$((i+2)) ;;
    --data-urlencode) data="${data}${data:+&}${args[$((i+1))]:-}"; i=$((i+2)) ;;
    --connect-timeout|--max-time|-H) i=$((i+2)) ;;
    -s|-sS|-S|-L|-k|--fail) i=$((i+1)) ;;
    --fail-with-body) fail_body=1; i=$((i+1)) ;;
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
    if [ "${STUB_TELEGRAM_HTTP:-0}" != "0" ]; then
      # A live Telegram API 4xx (400 chat not found / 401 Unauthorized). A bare
      # `curl -sS` EXITS 0 on an HTTP error, so the watchdog believed it had
      # paged a human. With --fail-with-body curl exits 22. This stub only fails
      # when the flag WAS passed, so the P2-1 assertion is sensitive to the fix.
      if [ "$fail_body" = "1" ]; then
        # `--fail-with-body` still passes the response BODY through to -o, which
        # is where the actionable `description` lives (round 4, P3-7).
        if [ -n "$out_file" ] && [ "$out_file" != "/dev/null" ]; then
          printf '{"ok":false,"error_code":%s,"description":"%s"}' \
            "$STUB_TELEGRAM_HTTP" "${STUB_TELEGRAM_DESC:-Bad Request: chat not found}" > "$out_file"
        fi
        echo "curl: (22) The requested URL returned error: ${STUB_TELEGRAM_HTTP}" >&2
        exit 22
      fi
      printf '{"ok":false,"error_code":%s}' "$STUB_TELEGRAM_HTTP" > /dev/null
      exit 0
    fi
    if [ "${STUB_TELEGRAM_OK:-1}" != "1" ]; then
      # HTTP 2xx with the API's own verdict false: a bad chat id / a bot that was
      # removed from the chat. curl exits 0; the ONLY signal is the body. The
      # watchdog must treat this as NOT DELIVERED (the ok:true contract), so this
      # knob is what makes the delivery check non-vacuous.
      if [ -n "$out_file" ] && [ "$out_file" != "/dev/null" ]; then
        printf '{"ok":false,"error_code":400,"description":"%s"}' \
          "${STUB_TELEGRAM_DESC:-Bad Request: chat not found}" > "$out_file"
      fi
      exit 0
    fi
    # A real success body goes to -o FILE; `-o /dev/null` is not used by the
    # watchdog any more, but honour it if a future caller uses it.
    if [ -n "$out_file" ] && [ "$out_file" != "/dev/null" ]; then
      printf '{"ok":true,"result":{}}' > "$out_file"
    fi
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
# `-D FILE` dumps RESPONSE HEADERS. STUB_PROBE_HEADERS reproduces the header
# block a real curl writes (status line + headers), which is what the watchdog
# reads for PROBE_REQUIRE_HEADER. Absent → an empty dump (no header found).
[ -n "$hdr_file" ] && printf '%s' "${STUB_PROBE_HEADERS:-}" > "$hdr_file"
# A real curl emits the -w output even when the transfer FAILED (http_code is
# then 000) and exits non-zero with the reason on stderr. STUB_PROBE_RC /
# STUB_PROBE_STDERR reproduce that so the harness can drive classify_failure().
if [ -n "${STUB_PROBE_RC:-}" ] && [ "${STUB_PROBE_RC:-0}" != "0" ]; then
  echo "curl: (${STUB_PROBE_RC}) ${STUB_PROBE_STDERR:-connection failed}" >&2
fi
if [ -n "$write_fmt" ]; then printf '%s %s' "$code" "${STUB_PROBE_TIME:-0.42}"; fi
exit "${STUB_PROBE_RC:-0}"
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
path="${2:-}"; method="GET"; input=0; paginate=0
shift 2 || true
while [ $# -gt 0 ]; do
  case "$1" in
    # `shift 2` FAILS when the option is the LAST argument (no value follows),
    # and a FAILED shift shifts NOTHING — the loop then re-reads the same "$1"
    # forever (a genuine infinite spin, one argument-ordering change from
    # live). Fall back to a single shift so EVERY branch makes progress.
    --method) method="$2"; shift 2 2>/dev/null || shift ;;
    --input) input=1; shift ;;
    --jq) shift 2 2>/dev/null || shift ;;
    --paginate) paginate=1; shift ;;
    *) shift ;;
  esac
done
payload=""
if [ "$input" = "1" ]; then payload="$(cat)"; fi
echo "GH $method ${path%%\?*}" >> "$STUB_TMP/calls.log"

case "$path" in
  search/issues*)
    [ "${STUB_SEARCH_FAIL:-0}" = "1" ] && { echo "gh: search failed" >&2; exit 1; }
    case "$path" in
      *is%3Aopen*) : ;;
      *) [ "${STUB_LEDGER_SEARCH_FAIL:-0}" = "1" ] && { echo "gh: ledger search failed" >&2; exit 1; } ;;
    esac
    # The full query is logged so a test can prove WHICH dedupe key was used.
    echo "GH-Q paginate=${paginate} $path" >> "$STUB_TMP/calls.log"
    # TWO distinct searches hit this endpoint: the OPEN-incident dedupe
    # (is:open) and the cross-incident LEDGER lookup (no is:open — it must see
    # CLOSED incidents too). They need separate fixtures: a test has to be able
    # to say "no open incident" AND "a closed machine issue still has ledger".
    json="${STUB_SEARCH_JSON:-$DEFAULT_ITEMS_JSON}"
    marker="${STUB_SEARCH_MARKER:-}"
    case "$path" in
      *is%3Aopen*) : ;;
      *) json="${STUB_LEDGER_SEARCH_JSON-$json}"
         marker="${STUB_LEDGER_SEARCH_MARKER-$marker}" ;;
    esac
    # Marker-aware: only the seeded KIND answers, exactly as production search
    # would (an encoded marker match). Unset → always answer.
    if [ -n "$marker" ]; then
      case "$path" in
        *"$marker"*) printf '%s' "$json" ;;
        *) printf '%s' "$DEFAULT_ITEMS_JSON" ;;
      esac
    else
      printf '%s' "$json"
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
      # A PER-ISSUE body store (round 4): the fail-closed ledger retry reads BOTH
      # the open incident and the named SOURCE incident in one run, so a single
      # shared issue.json cannot express "the current body says X, the source
      # says Y". `issue.<n>.json` wins when present; `issue.json` is the
      # fallback that keeps every existing fixture working.
      issue_num="${path##*/}"
      issue_file="$STUB_TMP/issue.json"
      [ -n "$issue_num" ] && [ -f "$STUB_TMP/issue.${issue_num}.json" ] && issue_file="$STUB_TMP/issue.${issue_num}.json"
      if [ -f "$issue_file" ]; then
        # GitHub ALWAYS returns a server-side created_at. Emulate it: fixtures
        # may set STUB_ISSUE_CREATED_AT explicitly (ISO-8601 or `epoch:<n>`);
        # otherwise derive it from the state block's first_failure_ts so the
        # legacy fixtures keep their meaning. Set STUB_ISSUE_CREATED_AT to a
        # non-timestamp (e.g. "not-a-timestamp") to exercise the fail-closed
        # "no usable anchor" path.
        if [ -n "${STUB_ISSUE_CREATED_AT:-}" ]; then
          jq -c --arg c "$STUB_ISSUE_CREATED_AT" '. + {created_at:$c}' "$issue_file"
        else
          jq -c 'if ((.created_at // "") != "") then .
                 else . + {created_at: ("epoch:" + (try (((.body // "") | capture("first_failure_ts=(?<ts>[0-9]+)").ts)) catch "0"))} end' \
            "$issue_file" 2>/dev/null || cat "$issue_file"
        fi
      else printf '{"body":""}'; fi
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
                    if [ "${STUB_FLY_LEAK_SHAPE:-0}" = "1" ]; then
                      # A Fly-shaped macaroon the caller does NOT hold the value
                      # of: only SHAPE redaction can catch it.
                      echo "Error: unauthorized: token FlyV1 fm2_lJAbCdEf$(printf 'x%.0s' {1..400}) rejected" >&2
                    elif [ "${STUB_FLY_SPLIT:-0}" = "1" ]; then
                      # A WRAPPED credential. A real Fly token is
                      # `FlyV1 <macaroon>` — a SPACE-bearing value — and a
                      # captured log wraps it at that space. Emit the SAME
                      # credential three ways (newline, CRLF, plain space at the
                      # wrap point): only the reflow-then-redact order catches
                      # the first two, and the third is the control.
                      half_a="${FLY_API_TOKEN%% *}"; half_b="${FLY_API_TOKEN#* }"
                      printf 'Error: unauthorized: token %s\n%s rejected\n' "$half_a" "$half_b" >&2
                      printf 'Error: unauthorized: token %s\r\n%s rejected\r\n' "$half_a" "$half_b" >&2
                      printf 'Error: unauthorized: token %s %s rejected\n' "$half_a" "$half_b" >&2
                      exit 1
                    elif [ "${STUB_FLY_LEAK:-0}" = "1" ]; then
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
        "$STUB_TMP/comments.log" "$STUB_TMP/issue.json" "$STUB_TMP"/issue.*.json
  unset STUB_PROBE_CODES STUB_PROBE_BODY STUB_PROBE_TIME STUB_SEARCH_JSON \
        STUB_SEARCH_FAIL STUB_SEARCH_MARKER STUB_CREATE_FAIL STUB_NEW_ISSUE \
        STUB_LEDGER_SEARCH_JSON STUB_LEDGER_SEARCH_MARKER STUB_LEDGER_SEARCH_FAIL \
        STUB_PROBE_RC STUB_PROBE_STDERR STUB_PROBE_HEADERS \
        STUB_FLY_MACHINES STUB_FLY_LIST_FAIL STUB_FLY_RESTART_FAIL STUB_FLY_LEAK \
        STUB_FLY_LEAK_SHAPE STUB_FLY_SPLIT STUB_ISSUE_CREATED_AT \
        STUB_TELEGRAM_FAIL STUB_TELEGRAM_HTTP STUB_TELEGRAM_DESC STUB_TELEGRAM_OK STUB_COMMENT_FAIL STUB_GET_BODY_FAIL \
        STUB_CONTROL_CODES \
        PROBE_URL FLY_API_TOKEN TELEGRAM_BOT_TOKEN \
        TELEGRAM_CHAT_ID ESCALATION_CHAT_ID ESCALATE_ENABLED \
        ESCALATE_SUSTAINED_MINUTES ESCALATE_MIN_RUNS \
        PROBE_HOST_LABEL STUB_PATCH_FAIL \
        PROBE_EXPECT_STATUS PROBE_REQUIRE_HEADER PROD_PROBE_URLS RESTARTABLE_PROBE_URLS \
        RECOVERY_CONFIRM_PROBES SUSTAINED_MIN_RUNS PROBE_ATTEMPTS \
        MAX_RESTARTS_PER_HOUR STALE_RESET_MINUTES CONTROL_URL 2>/dev/null || true
  export GH_TOKEN="test-token"
  export WATCHDOG_NOW_EPOCH="$NOW"
  export PATH="$BIN:$PATH"
}

run_watchdog() { # -> RC (exit code), OUT (stderr/log), OUT_STDOUT (must be empty)
  set +e
  local so
  # `</dev/null`: the gh stub has the pipeline's only unbounded read
  # (`payload="$(cat)"`), and this call hands the watchdog the HARNESS's own
  # stdin. Every watchdog `--input` call is pipe-fed, but closing stdin here
  # makes a non-piped read impossible to block on a terminal.
  so="$("$WATCHDOG" 2>"$STUB_TMP/stderr.log" </dev/null)"
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
  # Same `</dev/null` as run_watchdog(): this is the other direct watchdog
  # invocation, so it hands over the harness's stdin too.
  so="$(PATH="$BIN_NOFLY:/usr/bin:/bin:/usr/sbin:/sbin" "$WATCHDOG" 2>"$STUB_TMP/stderr.log" </dev/null)"
  RC=$?
  OUT="$([ -f "$STUB_TMP/stderr.log" ] && cat "$STUB_TMP/stderr.log" || true)"
  OUT_STDOUT="$so"
  set +e
  if [ -n "$OUT_STDOUT" ]; then
    bad "stdout is DATA-only — leaked: $(first_chars_stub "$OUT_STDOUT")"
  fi
}

first_chars_stub() { printf '%s' "$1" | head -c 200 || true; }

# Unit-call probe_host_label() straight from the script. WATCHDOG_LIB_ONLY=1 is
# the script's own seam (it skips main), so a pure parser can be tested without
# dragging the whole state machine through a probe.
probe_label() { # <url>
  WATCHDOG_LIB_ONLY=1 bash -c 'source "$0"; probe_host_label "$1"' "$WATCHDOG" "$1"
}

# Unit-call scrub_output() so its REDACTION CONTRACT can be asserted directly.
# Deliberately NOT end-to-end only: every publication helper applies redact_text
# a SECOND time, which masks an order bug for a token whose value we hold, while
# a captured log line goes to the PUBLIC Actions log through warn/fail with NO
# second pass at all.
scrub_unit() { # <text> <max>
  WATCHDOG_LIB_ONLY=1 bash -c 'source "$0"; scrub_output "$1" "$2"' "$WATCHDOG" "$1" "$2"
}

# Unit-call redact_text() (the PUBLICATION-BOUNDARY pass, which does NOT
# reflow) so the bare-macaroon shape rule can be asserted without scrub_output
# rejoining a wrapped fragment first.
redact_unit() { # <text>
  WATCHDOG_LIB_ONLY=1 bash -c 'source "$0"; redact_text "$1"' "$WATCHDOG" "$1"
}

# seed an existing open incident in the stub's issue store.
seed_issue() { # <kind> <first_failure_ts> <down_runs> <last_comment_ts> <restarts> [number] [last_down_ts]
  # The real writer emits restarts as ts,ts — normalize any space-separated
  # input so a fixture cannot silently seed a DIFFERENT state than production.
  # last_down_ts defaults to a run 1 min ago (the realistic "previous run")
  # rather than being omitted: an omitted/0 last_down_ts is UNKNOWN, not "just
  # now", and now trips the stale-clock reset (fail closed) — see parse_state.
  local restarts last_down
  restarts="$(printf '%s' "$5" | tr ' ' ',')"
  last_down="${7:-$((NOW - 60))}"
  jq -n --arg b "<!-- watchdog-state kind=$1 first_failure_ts=$2 down_runs=$3 last_down_ts=$last_down last_comment_ts=$4 cap_notified_ts=0 restarts=$restarts -->" \
    '{body:$b}' > "$STUB_TMP/issue.json"
  STUB_SEARCH_JSON="$(search_json "${6:-42}" "$DOWN_TITLE_FIXTURE")"
  export STUB_SEARCH_JSON
  # Only the seeded KIND matches the search (jq @uri encodes spaces as %20); the
  # fixture title must be the EXACT production title the watchdog expects.
  if [ "$1" = "down" ]; then
    export STUB_SEARCH_MARKER='PROD%20DOWN'
  else
    STUB_SEARCH_JSON="$(search_json "${6:-42}" "$DEGRADED_TITLE_FIXTURE")"
    export STUB_SEARCH_JSON
    export STUB_SEARCH_MARKER='PROD%20DEGRADED'
  fi
}

# A search result item as PRODUCTION would return it: authored by the GitHub
# Actions bot. The watchdog rejects any non-machine match, so every fixture
# that expects adoption MUST carry this author. Use a different author
# deliberately in the hijack tests.
search_json() { # <number> [title] [login] [type]
  # The default title is the EXACT production DOWN title, and every item
  # carries the body-only marker, because the watchdog now refuses to adopt an
  # item that lacks either (round 3, P2-2).
  local n="${1:-42}" t="${2:-$DOWN_TITLE_FIXTURE}" l="${3:-github-actions[bot]}" ty="${4:-Bot}"
  printf '{"items":[{"number":%s,"title":"%s","body":"%s","user":{"login":"%s","type":"%s"}}]}' \
    "$n" "$t" "$INCIDENT_STATE_MARKER_FIXTURE" "$l" "$ty"
}

# A search item built from a PUBLISHED body (round 4, P3-3): the marker round-
# trip must be driven by what render_body actually wrote, not by a marker the
# harness injects itself — otherwise deleting the marker from render_body leaves
# the suite green.
search_json_body() { # <number> <title> <body> [login] [type]
  local n="$1" t="$2" b="$3" l="${4:-github-actions[bot]}" ty="${5:-Bot}"
  jq -n --arg n "$n" --arg t "$t" --arg b "$b" --arg l "$l" --arg ty "$ty" \
    '{items:[{number:($n|tonumber),title:$t,body:$b,user:{login:$l,type:$ty}}]}'
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
# The FIRST body PATCH. The escalation leg is WRITE-THEN-ACT: the attempt marker
# rides the FIRST write and the confirmed outcome the LAST, so only a first-PATCH
# read can see that the marker was durable BEFORE the send (F1).
patched_body_first() { if [ -f "$STUB_TMP/patched.log" ]; then jq -r -s '[.[] | select(.body != null and .body != "")] | first | .body // ""' "$STUB_TMP/patched.log"; else echo ''; fi; }
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
assert_contains "$(created_json)" "$INCIDENT_STATE_MARKER_FIXTURE" "new incident → the PUBLISHED body carries the adoption marker (round 4, P3-3)"

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
assert_contains "$OUT" "restart outcome: wait_cooldown" "cooldown → the run log carries the decide_restart reason (round 4, P3-8)"

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
export PROBE_URL="https://staging.example.test/v1/organizations"
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
STUB_SEARCH_JSON="$(search_json 42)"
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
# page_ok_ts is seeded alongside it (#3887): it is the CONFIRMED-human-page stamp
# the sustained-escalation leg's cross-mechanism bound reads, so this fixture now
# means "a human was actually paged 5 min ago" and silences BOTH the cap's own
# re-notify window AND the new leg. Without it the leg correctly pages — a
# sustained incident whose last page was an UNCONFIRMED attempt must not be read
# as "the human already knows".
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 last_down_ts=$((NOW - 60)) last_comment_ts=$((NOW - 60)) cap_notified_ts=$((NOW - 300)) escalate_state=sent escalate_ts=$((NOW - 300)) page_ok_ts=$((NOW - 300)) restarts=$((NOW - 1500)),$((NOW - 1300)) -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
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
export PROBE_URL="https://staging.example.test/v1/organizations"
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
export STUB_SEARCH_JSON="$(search_json 42)"
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
export STUB_SEARCH_JSON="$(search_json 42)"
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
# F2: this early exit sits BEFORE the escalation leg, so a flapping service
# keeps an open incident with zero leaving-GitHub signal. The exit semantics are
# deliberately unchanged; the gap must be NAMED rather than silent.
assert_contains "$OUT" "NO escalation page is sent this run" "flapping with an open incident → the missing escalation page is NAMED, not silent"

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
export STUB_SEARCH_JSON="$(search_json 42)"
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
STUB_SEARCH_JSON="$(search_json 42)"
export STUB_SEARCH_JSON
export STUB_SEARCH_MARKER='PROD%20DOWN'
run_watchdog
assert_contains "$(patched_body)" "down_runs=2" "state block round-trips (run 1 wrote it, run 2 read it)"
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "round-trip run → no duplicate issue (still 1 create total)"

# ── 33: public-body hygiene ─────────────────────────────────────────────────
reset_case
export STUB_PROBE_CODES="000"
export PROBE_URL="https://user:s3cr3t@staging.example.test/v1/organizations"
run_watchdog
assert_not_contains "$(patched_body)" "s3cr3t" "credentials in PROBE_URL are redacted from the public body"
assert_not_contains "$(created_json)" "s3cr3t" "credentials in PROBE_URL never reach the public TITLE either"
assert_contains "$(patched_body)" "<redacted>" "redaction marker present"

# ── 42: query-string credentials are redacted too ──────────────────────────
reset_case
export STUB_PROBE_CODES="000"
export PROBE_URL="https://api.premiselabs.co/v1/organizations?debug=1&token=qs3cr3t"
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
export STUB_SEARCH_JSON="$(search_json 42)"
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
export STUB_SEARCH_JSON="$(search_json 42)"
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
# `0008` is NOT corruption — to_int normalises it to the (ancient) stamp 8, so
# it is preserved, not dropped, and the restart is allowed. That is the
# contrast case for the fail-CLOSED cases below.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 3600)) down_runs=5 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts=0008 -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$RC" "1" "restarts=0008 (octal) → a normal DOWN run, not a silent shell death"
assert_not_contains "$OUT" "value too great for base" "restarts=0008 → no arithmetic abort"
assert_eq "$(count_calls 'FLYCTL machine restart')" "1" "restarts=0008 → normalized to an ANCIENT stamp (outside the hour) → restart allowed"

# 44b: an entry to_int REJECTS (>12 digits) used to be silently DROPPED, which
# can only WEAKEN the rate limit. It must now fail CLOSED: refuse to act.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 3600)) down_runs=5 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts=99999999999999999999 -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$RC" "1" "a >12-digit ledger entry → a normal DOWN run (never a crash)"
assert_not_contains "$OUT" "integer expression expected" "a >12-digit entry → no arithmetic error"
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "a >12-digit entry → FAIL CLOSED: zero restarts (it is refused, never dropped)"
assert_contains "$OUT" "not fully parseable" "a >12-digit entry → says exactly why"
assert_contains "$OUT" "WEAKEN" "a >12-digit entry → names the fail-open risk it refuses"
assert_eq "$(count_calls 'GH PATCH')" "0" "a >12-digit entry → refuses before any state write (no silent purge-and-restart)"

# 44c: `restarts=abc` captures as EMPTY under the old permissive regex, which
# read as "no restarts" — the fail-open direction. Refuse instead.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 3600)) down_runs=5 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts=abc -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$RC" "1" "restarts=abc → a normal DOWN run"
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "restarts=abc → FAIL CLOSED: zero restarts (never read as 'no restarts')"
assert_contains "$OUT" "not fully parseable" "restarts=abc → says exactly why"

# 44d: a MIXED ledger (one valid stamp, one unparseable) must not be partially
# honoured — the unparseable entry could be the cooldown stamp.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 3600)) down_runs=5 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts=$((NOW - 1500)),zzz -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "a mixed valid+garbage ledger → FAIL CLOSED (no restart from the valid half)"
assert_contains "$OUT" "not fully parseable" "a mixed ledger → says exactly why"

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
export STUB_SEARCH_JSON="$(search_json 42)"
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
export STUB_SEARCH_JSON="$(search_json 42)"
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
export STUB_SEARCH_JSON="$(search_json 777 "$DRILL_DOWN_TITLE_FIXTURE")"
export STUB_PROBE_CODES="200"
export PROBE_URL="https://staging.example.test/v1/organizations"
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
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$(count_calls 'CURL telegram')" "1" "a FUTURE cap stamp → clamped → the cap escalation still pages (never muted)"
assert_contains "$(patched_body)" "cap_notified_ts=$NOW" "a future cap stamp → rewritten clamped"

# ── 60: the dedupe search is constrained to the machine author ───────────
# PUBLIC repo: "an open issue with our title" is not ours to adopt.
reset_case
export STUB_PROBE_CODES="000"
run_watchdog
assert_eq "$(count_calls 'GH-Q.*is%3Aopen.*author%3Aapp%2Fgithub-actions')" "1" "the dedupe search carries the author:app/github-actions constraint"
# The cross-incident ledger lookup (Fix 4) must carry it too — it reads a body
# and seeds machine state from it, so it is the same hijack surface.
assert_eq "$(count_calls 'GH-Q.*author%3Aapp%2Fgithub-actions')" "2" "both searches (open dedupe + cross-incident ledger) carry the constraint"
assert_eq "$(count_calls 'GH-Q paginate=1')" "2" "both searches PAGINATE — no page-1-only truncation (round 3, P3-12)"

# ── 61: a forged HUMAN-authored look-alike incident is never adopted (P1) ──
# Anyone can open an issue with the watchdog's title and a forged state block:
# an ancient clock + down_runs=999 would authorise a restart on the FIRST
# observed failing run, and a forged `restarts=` ledger would defeat the
# cooldown AND the hourly cap. It must be ignored entirely.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 86400)) down_runs=999 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_JSON="$(search_json 666 '[monitor] PROD DOWN — forged' 'attacker' 'User')"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "a human-authored look-alike → NO restart (the forged clock/cap is never adopted)"
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "a human-authored look-alike → a FRESH machine issue is filed instead"
assert_eq "$(count_calls 'GH GET repos/.*/issues/666')" "0" "a human-authored look-alike → its body is never read"
assert_eq "$(count_calls 'GH PATCH repos/.*/issues/666')" "0" "a human-authored look-alike → never PATCHed with machine state"
assert_contains "$OUT" "NONE was authored by" "a human-authored look-alike → logged loudly"

# ── 62: the sustained window is clamped to the server-side created_at (P2) ─
# A MACHINE issue can still carry a hand-edited/forged ancient clock. The
# issue's created_at is GitHub-assigned and immutable, so it bounds the window.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 86400)) down_runs=999 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_ISSUE_CREATED_AT="epoch:$((NOW - 120))"
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "a forged ancient first_failure_ts → clamped to created_at → NO restart"
assert_contains "$OUT" "clamping the sustained clock" "the server-side clamp is logged"
assert_contains "$(patched_body)" "first_failure_ts=$((NOW - 120))" "the clamped clock is what is persisted"
assert_contains "$(patched_body)" "waits 10 min" "the body explains the fresh sustained window"

# ── 63: an unusable server-side anchor fails closed ──────────────────────
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 86400)) down_runs=999 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_ISSUE_CREATED_AT="not-a-timestamp"
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "an unusable created_at → NO restart (fail closed, never trust the body clock)"
assert_contains "$OUT" "no usable server-side created_at" "an unusable created_at → says exactly why"

# ── 64: an OMITTED last_down_ts is UNKNOWN, not 'just now' (P2) ─────────
# Defaulting it to `now` made the stale-clock guard unreachable for a body that
# simply omitted the field — an ancient first_failure_ts then satisfied the
# sustained window on the FIRST observed failing run.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 86400)) down_runs=999 last_comment_ts=0 cap_notified_ts=0 restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "an omitted last_down_ts → UNKNOWN → stale reset → NO restart"
assert_contains "$OUT" "stale incident" "an omitted last_down_ts → the stale clock is logged"

# ── 65: a LONG token must not leak as a PREFIX (reflow → redact → truncate) ──
# The round-1 order truncated captured flyctl output to 200 chars FIRST,
# splitting a >300-char token so the literal-value match no longer matched the
# survivor. (The order is now reflow → redact → truncate; see tests 67/67b for
# the WRAPPING half of that bug.)
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
LONG_TOKEN="fly-live-$(printf 'a%.0s' {1..400})"
export FLY_API_TOKEN="$LONG_TOKEN"
export STUB_PROBE_CODES="000"
export STUB_FLY_RESTART_FAIL=1
export STUB_FLY_LEAK=1
run_watchdog
assert_contains "$OUT" "automatic restart failed" "long token → the restart failure is escalated"
assert_not_contains "$(comments_all)" "${LONG_TOKEN:0:150}" "a >300-char token → NO partial token in the PUBLIC comment"
assert_not_contains "$(patched_body)" "${LONG_TOKEN:0:150}" "a >300-char token → NO partial token in the PUBLIC body"
assert_not_contains "$OUT" "${LONG_TOKEN:0:150}" "a >300-char token → NO partial token in the PUBLIC log"
assert_contains "$(comments_all)" "<redacted>" "a >300-char token → the redaction marker is what is published"

# ── 66: a Fly-SHAPED token we do not hold the value of is redacted too ──
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export FLY_API_TOKEN="some-other-token"
export STUB_PROBE_CODES="000"
export STUB_FLY_RESTART_FAIL=1
export STUB_FLY_LEAK_SHAPE=1
run_watchdog
assert_not_contains "$(comments_all)" "fm2_lJ" "a Fly-shaped token (unknown value) → shape-redacted from the comment"
assert_not_contains "$OUT" "fm2_lJ" "a Fly-shaped token → shape-redacted from the log"
assert_not_contains "$(patched_body)" "fm2_lJ" "a Fly-shaped token → shape-redacted from the body"
assert_contains "$(comments_all)" "<redacted>" "a Fly-shaped token → redaction marker present"

# ── 67: a WRAPPED credential must be redacted (reflow BEFORE redact) ──────────
# Round 1 kept the order REDACT → reflow. A real Fly token is
# `FlyV1 <macaroon>` — a SPACE-bearing value — and a captured log wraps it at
# that space, so the literal-value match failed on the newline and the reflow
# that followed RE-JOINED the fragments into a complete, readable credential.
# Asserted on the token's DISTINCTIVE substring, not on its length.
SPLIT_TAIL="fm2_REFLOW_DISTINCTIVE_$(printf 'q%.0s' {1..30})"
NL_TEXT="$(printf 'Error: token FlyV1\n%s rejected' "$SPLIT_TAIL")"
CRLF_TEXT="$(printf 'Error: token FlyV1\r\n%s rejected' "$SPLIT_TAIL")"
SP_TEXT="$(printf 'Error: token FlyV1 %s rejected' "$SPLIT_TAIL")"
export FLY_API_TOKEN="FlyV1 $SPLIT_TAIL"
assert_not_contains "$(scrub_unit "$NL_TEXT" 300)" "REFLOW_DISTINCTIVE" "a \\n-wrapped FlyV1 token is redacted by scrub_output() itself"
assert_not_contains "$(scrub_unit "$CRLF_TEXT" 300)" "REFLOW_DISTINCTIVE" "a \\r\\n-wrapped FlyV1 token is redacted by scrub_output() itself"
assert_not_contains "$(scrub_unit "$SP_TEXT" 300)" "REFLOW_DISTINCTIVE" "a space-split FlyV1 token is redacted by scrub_output() itself"
assert_contains "$(scrub_unit "$NL_TEXT" 300)" "<redacted>" "the reflow path publishes the redaction marker"
assert_contains "$(scrub_unit "$NL_TEXT" 300)" "Error: token" "the reflow path keeps the non-secret diagnostic text"
assert_contains "$(scrub_unit "$NL_TEXT" 40)" "<redacted>" "the reflow path still redacts BEFORE it truncates (a budget INSIDE the credential keeps the marker)"
assert_not_contains "$(scrub_unit "$NL_TEXT" 40)" "fm2_REFLOW" "the reflow path still redacts BEFORE it truncates (no credential prefix survives)"

# ── 67b: …and the same credential never reaches a public surface end-to-end ──
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export FLY_API_TOKEN="FlyV1 $SPLIT_TAIL"
export STUB_PROBE_CODES="000"
export STUB_FLY_RESTART_FAIL=1
export STUB_FLY_SPLIT=1
run_watchdog
assert_contains "$OUT" "automatic restart failed" "wrapped token → the restart failure is still escalated"
assert_not_contains "$(comments_all)" "REFLOW_DISTINCTIVE" "a wrapped token → no fragment in the PUBLIC comment"
assert_not_contains "$(patched_body)" "REFLOW_DISTINCTIVE" "a wrapped token → no fragment in the PUBLIC body"
assert_not_contains "$OUT" "REFLOW_DISTINCTIVE" "a wrapped token → no fragment in the PUBLIC log"
assert_contains "$(comments_all)" "<redacted>" "a wrapped token → the redaction marker is what is published"

# ── 68: a credentialed URI must not leak through the HOST LABEL ─────────────
# The label is published in the issue TITLE on its own (redact_url only cleans
# the full URL), and a hand-written userinfo containing a `/` walked past the
# naive "authority, then @-strip" split: `rediss://user:pa/ss@host:6379` made
# the label `user:pa` — the password prefix.
assert_eq "$(probe_label 'rediss://user:pa/ss@host:6379')" "<redacted-host>" "a '/' inside the userinfo → the label is REDACTED (not the password prefix)"
assert_eq "$(probe_label 'https://user:s3cr3t@staging.example.test/v1/organizations')" "staging.example.test" "a normal userinfo is stripped from the label"
assert_eq "$(probe_label 'https://user:s3cr3t@staging.example.test:8443/v1')" "staging.example.test" "the port is stripped with the userinfo"
assert_eq "$(probe_label 'https://api.premiselabs.co/v1/organizations')" "api.premiselabs.co" "a credential-free URL still yields its host"
assert_eq "$(probe_label 'https://host/v1?x=a@b')" "<redacted-host>" "an '@' outside the authority fails closed (never publish an unverified cut)"
# …and the same guard end-to-end: the DRILL issue TITLE must not carry it.
reset_case
export STUB_PROBE_CODES="000"
export PROBE_URL="rediss://user:pa/ss@host:6379"
run_watchdog
assert_eq "$RC" "1" "credentialed-URI drill → still alerts"
assert_not_contains "$(created_json)" "pa/ss" "credentialed-URI drill → the public TITLE carries no password fragment"
assert_contains "$(created_json)" "<redacted-host>" "credentialed-URI drill → the title uses the redacted placeholder"

# ── 69: the restart budget survives an incident close/reopen (flapping) ─────
# MAX_RESTARTS_PER_HOUR used to be counted against the CURRENT incident, so a
# machine flapping every ~10 min closed its incident, opened a new one with an
# EMPTY ledger and restarted forever without ever tripping the cap. A new
# incident must inherit the still-in-window stamps of the previous (closed) one.
reset_case
# The PREVIOUS incident: closed, machine-authored, with 2 restarts already spent
# inside the hour. The open-incident dedupe answers "none" (default empty
# fixture) while the ledger lookup (no is:open) still finds this one.
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 2400)) down_runs=4 last_down_ts=$((NOW - 3000)) last_comment_ts=0 cap_notified_ts=0 restarts=$((NOW - 2400)),$((NOW - 2100)) -->\"}" > "$STUB_TMP/issue.json"
export STUB_LEDGER_SEARCH_JSON="$(search_json 777 "$DOWN_TITLE_FIXTURE")"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL')" "0" "cross-incident cap → run 1 of the new incident waits (the sustained window restarts)"
assert_contains "$(patched_body)" "restarts=$((NOW - 2400)),$((NOW - 2100))" "a NEW incident SEEDS its ledger from the previous closed incident"
# Run 2, 10 min later: the open incident now carries the inherited ledger, so
# the 3rd restart inside the hour is what the cap must BLOCK.
jq -n --arg b "$(patched_body)" '{body:$b}' > "$STUB_TMP/issue.json"
STUB_SEARCH_JSON="$(search_json 900)"
export STUB_SEARCH_JSON
export STUB_SEARCH_MARKER='PROD%20DOWN'
: > "$STUB_TMP/calls.log"
export WATCHDOG_NOW_EPOCH="$((NOW + 600))"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "cross-incident cap → the 3rd restart in the hour is BLOCKED after the reopen"
assert_contains "$(patched_body)" "velocity cap" "cross-incident cap → the body reports the cap"
export WATCHDOG_NOW_EPOCH="$NOW"

# ── 70: an unreadable previous ledger fails CLOSED (no restart) ─────────────
# STUB_LEDGER_SEARCH_FAIL fails ONLY the ledger lookup (no is:open): the open
# dedupe still succeeds with an empty fixture, so main actually reaches
# recent_restart_ledger(). STUB_SEARCH_FAIL failed BOTH searches, so main exited
# at the open-search check and the disarmed:no_ledger branch was never run —
# the old assertions passed vacuously (round 3, P2-6).
reset_case
export STUB_LEDGER_SEARCH_FAIL=1
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$RC" "1" "unreadable previous ledger → exit 1 (still alerts)"
assert_eq "$(count_calls 'FLYCTL')" "0" "unreadable previous ledger → NO restart (the hourly budget cannot be proven)"
assert_contains "$OUT" "restart-ledger search failed" "unreadable previous ledger → the LEDGER search failure is named"
assert_contains "$(patched_body)" "previous incident's restart ledger could not be read" "unreadable previous ledger → the NEW incident body says why no restart ran"
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "unreadable previous ledger → still files the alert"
# ── 70b: …and that fail-closed verdict is DURABLE (round 4, P2-7) ────────────
# Without the persisted sentinel, run 2 adopts the new incident, reads its EMPTY
# `restarts=` as valid, and arms with an empty hourly budget — the cap stamps
# the source carried are gone. The sentinel keeps the retry fail-closed until a
# lookup actually succeeds.
assert_contains "$(patched_body)" "ledger_state=unreadable" "unreadable previous ledger → the fail-closed verdict is PERSISTED in the state block"
jq -n --arg b "$(patched_body)" '{body:$b}' > "$STUB_TMP/issue.json"
STUB_SEARCH_JSON="$(search_json 900)"
STUB_LEDGER_SEARCH_JSON="$(search_json 900)"
unset STUB_LEDGER_SEARCH_FAIL
unset STUB_SEARCH_FAIL
: > "$STUB_TMP/calls.log"
export STUB_SEARCH_JSON STUB_LEDGER_SEARCH_JSON STUB_SEARCH_MARKER='PROD%20DOWN'
export WATCHDOG_NOW_EPOCH="$((NOW + 600))"
run_watchdog
assert_eq "$(count_calls 'GH POST .*/issues$')" "0" "run 2 adopts the incident (no duplicate) and RETRIES the ledger lookup"
assert_eq "$(count_calls 'FLYCTL machine restart')" "1" "run 2's lookup now succeeds (no other incident) → the budget is genuinely empty → self-heal resumes"
export WATCHDOG_NOW_EPOCH="$NOW"

# ── 71: a DNS failure must NOT restart, and must say so ─────────────────────
# A restart cannot repair name resolution; it only spends the restart budget.
# The incident is filed and the body must explain the disarm explicitly.
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export STUB_PROBE_RC=6
export STUB_PROBE_STDERR="Could not resolve host: api.premiselabs.co"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$RC" "1" "DNS failure → still alerts (exit 1)"
assert_eq "$(count_calls 'FLYCTL')" "0" "DNS failure → NO restart (a restart cannot fix resolution)"
assert_contains "$(patched_body)" "a DNS resolution failure" "DNS failure → the body names the failure class"
assert_contains "$(patched_body)" "cannot fix" "DNS failure → the body explains the disarm"

# ── 72: a TLS/certificate failure must NOT restart either ───────────────────
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export STUB_PROBE_RC=60
export STUB_PROBE_STDERR="SSL certificate problem: certificate has expired"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL')" "0" "TLS failure → NO restart (a restart cannot fix a certificate)"
assert_contains "$(patched_body)" "a TLS/certificate failure" "TLS failure → the body names the failure class"

# ── 73: a TRANSPORT failure still restarts (contrast case) ──────────────────
# Same sustained state and same 000, but connection refused is the signature of
# a wedged process — the restart leg must stay armed.
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export STUB_PROBE_RC=7
export STUB_PROBE_STDERR="Failed to connect to api.premiselabs.co port 443: Connection refused"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "1" "connection refused → the restart leg is still ARMED (contrast with DNS/TLS)"

# ── 74: a 5xx is an APP failure, not a DNS one — restart stays armed ────────
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="503"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "1" "a 5xx (the app answered) → restart stays armed"

# ── 75: classify_failure() coverage on the message-only fallback path ───────
# Some builds report a cert/name failure under a generic curl code; the stderr
# text is then the only signal. curl 52/55/56 are excluded from this fallback
# (round 3, P2-8): their text is a half-dead-process signature, not a cert one.
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export STUB_PROBE_RC=56
export STUB_PROBE_STDERR="OpenSSL SSL_read: error:0A000126:SSL routines::unexpected eof"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "1" "curl 56 + SSL_read eof → a HALF-DEAD PROCESS → restart stays ARMED (round 3, P2-8)"

# ══ Round 3 (each FAILS on the round-2 code) ═══════════════════════════════

# ── 76: a Telegram HTTP 4xx is a FAILURE, not a silent success (P2-1) ────────
# `curl -sS` exits 0 on an HTTP error, so 400 chat not found / 401 Unauthorized
# looked like a delivered page. The stub only fails when --fail-with-body was
# actually passed, so this assertion is sensitive to the fix.
reset_case
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
export STUB_TELEGRAM_HTTP=400
run_watchdog
assert_contains "$OUT" "telegram page failed" "a Telegram 4xx → the dead escalation channel is logged (--fail-with-body)"
assert_contains "$OUT" "400" "a Telegram 4xx → the status is surfaced"
assert_contains "$OUT" "chat not found" "a Telegram 4xx → Telegram's own error description reaches the log (round 4, P3-7)"

# ── 77: another installed App's bot is NOT the Actions bot (P2-3) ───────────
# `renovate[bot]` has user.type == "Bot", so the old local re-check admitted it.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 86400)) down_runs=999 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_JSON="$(search_json 555 "$DOWN_TITLE_FIXTURE" 'renovate[bot]' 'Bot')"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "a renovate[bot] look-alike → NO restart"
assert_eq "$(count_calls 'GH PATCH repos/.*/issues/555')" "0" "a renovate[bot] look-alike → never PATCHed with machine state"
assert_eq "$(count_calls 'GH GET repos/.*/issues/555')" "0" "a renovate[bot] look-alike → its body is never read"
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "a renovate[bot] look-alike → a fresh machine issue is filed"

# ── 78: the dedupe requires the EXACT title AND the body marker (P2-2) ──────
# `in:title "<phrase>"` is a loose AND, not an exact phrase, and this repo
# carries hundreds of bot-authored monitor issues — the exact title plus the
# body-only marker are the tokens no other producer emits.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 86400)) down_runs=999 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts= -->\"}" > "$STUB_TMP/issue.json"
# A different workflow's bot issue whose title merely CONTAINS our terms.
export STUB_SEARCH_JSON='{"items":[{"number":556,"title":"[monitor] PROD DOWN — api.premiselabs.co is not answering the availability probe (legacy)","body":"<!-- availability-watchdog-state -->","user":{"login":"github-actions[bot]","type":"Bot"}}]}'
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "a near-title bot issue → NOT adopted (no restart)"
assert_eq "$(count_calls 'GH PATCH repos/.*/issues/556')" "0" "a near-title bot issue → never PATCHed with machine state"
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "a near-title bot issue → a fresh machine issue is filed"

# …and the exact title WITHOUT the body marker is still not ours.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 86400)) down_runs=999 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_JSON="$(printf '{"items":[{"number":557,"title":"%s","body":"no marker here","user":{"login":"github-actions[bot]","type":"Bot"}}]}' "$DOWN_TITLE_FIXTURE")"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "the exact title without the marker → NOT adopted (no restart)"
assert_eq "$(count_calls 'GH PATCH repos/.*/issues/557')" "0" "the exact title without the marker → never PATCHed"
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "the exact title without the marker → a fresh machine issue is filed"

# ── 79: a corrupt SEEDED ledger names the SOURCE, not the new incident (P2-4) ─
# The corrupt value came from #777, but the message named the just-created
# #900, and the run exited before the end-of-run body write — leaving the new
# incident promising a self-healing decision the run never wrote.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 2400)) down_runs=4 last_down_ts=$((NOW - 3000)) last_comment_ts=0 cap_notified_ts=0 restarts=abc -->\"}" > "$STUB_TMP/issue.json"
export STUB_LEDGER_SEARCH_JSON="$(search_json 777 "$DOWN_TITLE_FIXTURE")"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$RC" "1" "a corrupt previous ledger → exit 1 (still alerts)"
assert_eq "$(count_calls 'FLYCTL')" "0" "a corrupt previous ledger → NO restart"
assert_contains "$OUT" "restart ledger in incident #777" "a corrupt previous ledger → names the SOURCE (#777)"
assert_contains "$OUT" "restart decision: disarmed:corrupt_ledger" "a corrupt previous ledger → the run logs its disarm verdict (P2-5)"
assert_not_contains "$OUT" "restart ledger in incident #900" "a corrupt previous ledger → does NOT blame the newly-created incident"
assert_contains "$(patched_body)" "#777" "a corrupt previous ledger → the NEW incident body points at the source"
assert_contains "$(patched_body)" "corrupt" "a corrupt previous ledger → the NEW incident body records the disarm"
assert_eq "$(count_calls 'GH PATCH repos/.*/issues/777')" "0" "a corrupt previous ledger → the SOURCE is never PATCHed (its ledger is not erased)"

# ── 80: a bare macaroon fragment is shape-redacted (P3-10) ──────────────────
# A hard wrap can split the `FlyV1 ` prefix off, leaving `fm2_<40 chars>` on
# its own line. The old shape rule required the `FlyV1 ` prefix, so the fragment
# survived at the publication boundary (redact_text, which does not reflow).
reset_case
FM2_FRAG="fm2_$(printf 'Z%.0s' {1..40})"
FM2_TEXT="$(printf 'Error: token\n%s rejected' "$FM2_FRAG")"
export FLY_API_TOKEN="unrelated-token"
assert_not_contains "$(redact_unit "$FM2_TEXT")" "ZZZZ" "a bare fm2_ macaroon fragment is shape-redacted"
assert_contains "$(redact_unit "$FM2_TEXT")" "<redacted>" "a bare fm2_ fragment → the redaction marker is published"
unset FLY_API_TOKEN

# ── 81: the run log records the restart verdict (P2-5 / runbook §6.4) ───────
reset_case
seed_issue down "$((NOW - 300))" 1 0 ""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_contains "$OUT" "restart decision: DOWN" "an armed run logs its restart verdict ('DOWN')"
assert_contains "$OUT" "restart outcome: wait_sustained" "an armed run logs WHY no restart happened yet — the decide_restart outcome (round 4, P3-8)"
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export PROBE_URL="https://staging.example.test/v1/organizations"
run_watchdog
assert_contains "$OUT" "restart decision: disarmed:drill" "a drill logs its disarm reason"
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export STUB_PROBE_RC=6
export STUB_PROBE_STDERR="Could not resolve host: api.premiselabs.co"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_contains "$OUT" "restart decision: disarmed:unfixable" "a DNS failure logs its disarm reason"

# ── 82: a genuine certificate message under a generic code is still TLS ─────
# The narrowed fallback must still catch a real cert failure (it only drops the
# bare `ssl`/`tls` tokens and the 52/55/56 transport codes).
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export STUB_PROBE_CODES="000"
export STUB_PROBE_RC=1
export STUB_PROBE_STDERR="SSL certificate problem: unable to get local issuer certificate"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "a generic code + a certificate message → TLS → NO restart"
assert_contains "$(patched_body)" "a TLS/certificate failure" "a certificate message → the body names the TLS class"

# ══ Round 4 (each FAILS on the round-3 code) ═══════════════════════════════

# ── 83: the adoption marker survives render_body (P3-3) ─────────────────────
# The marker is LOAD-BEARING (search_open_alert refuses an item that lacks it)
# but was UNTESTED: deleting it from render_body left the suite green because
# every fixture injected it synthetically via search_json(). Here run 2's search
# result is built from run 1's REAL published body + title, so dropping the
# marker makes run 2 fail to recognise its own incident and file a duplicate.
reset_case
export STUB_PROBE_CODES="000"
run_watchdog
assert_contains "$(created_json)" "$INCIDENT_STATE_MARKER_FIXTURE" "the PUBLISHED incident body carries the adoption marker"
RUN1_TITLE="$(created_json | jq -r '.title')"
RUN1_BODY="$(created_json | jq -r '.body')"
STUB_SEARCH_JSON="$(search_json_body 900 "$RUN1_TITLE" "$RUN1_BODY")"
export STUB_SEARCH_JSON
export STUB_SEARCH_MARKER='PROD%20DOWN'
jq -n --arg b "$RUN1_BODY" '{body:$b}' > "$STUB_TMP/issue.json"
: > "$STUB_TMP/calls.log"
export WATCHDOG_NOW_EPOCH="$((NOW + 300))"
run_watchdog
assert_eq "$(count_calls 'GH POST .*/issues$')" "0" "run 2 adopts run 1's REAL published body — no duplicate (#2706)"
assert_contains "$(patched_body)" "down_runs=2" "run 2 read the REAL published state back (1 → 2)"
export WATCHDOG_NOW_EPOCH="$NOW"

# ── 84: the fail-closed ledger verdict is DURABLE and RESUMABLE (P2-7) ──────
# Round 3 persisted nothing: a NEW incident seeded from a corrupt source kept an
# EMPTY `restarts=`, so the next run adopted the open incident, read the empty
# ledger as valid, and armed with an EMPTY hourly budget — silently dropping the
# cap stamps the source carried (a restart storm). The sentinel is persisted
# instead, re-verified on every run, and cleared only when the source parses.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=9 last_down_ts=$((NOW - 3000)) last_comment_ts=0 cap_notified_ts=0 restarts=abc -->\"}" > "$STUB_TMP/issue.777.json"
export STUB_LEDGER_SEARCH_JSON="$(search_json 777 "$DOWN_TITLE_FIXTURE")"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$RC" "1" "run 1: corrupt seeded ledger → exit 1 (still alerts)"
assert_eq "$(count_calls 'FLYCTL')" "0" "run 1: corrupt seeded ledger → NO restart"
assert_contains "$(patched_body)" "ledger_state=invalid" "run 1: the fail-closed verdict is PERSISTED in the new incident"
assert_contains "$(patched_body)" "ledger_src=777" "run 1: the SOURCE issue is persisted so the next run can re-check it"
assert_eq "$(count_calls 'GH GET repos/.*/issues/777')" "1" "run 1: the corrupt source is read exactly ONCE (no redundant re-verify in the same run)"
# Run 2, 10 min later: the source is STILL corrupt. Without the sentinel this run
# would arm with an empty budget (sustained + 2 observed runs) and restart.
jq -n --arg b "$(patched_body)" '{body:$b}' > "$STUB_TMP/issue.json"
STUB_SEARCH_JSON="$(search_json 900)"
export STUB_SEARCH_JSON
export STUB_SEARCH_MARKER='PROD%20DOWN'
: > "$STUB_TMP/calls.log"
export WATCHDOG_NOW_EPOCH="$((NOW + 600))"
run_watchdog
assert_eq "$RC" "1" "run 2: still fail-closed (exit 1)"
assert_eq "$(count_calls 'FLYCTL')" "0" "run 2: the persisted verdict is RE-DERIVED → NO restart from an empty budget"
assert_contains "$OUT" "restart decision: disarmed:corrupt_ledger" "run 2: the disarm verdict is logged again"
assert_contains "$OUT" "restart ledger in incident #777" "run 2: the persisted SOURCE (#777) is named"
# Run 3, 20 min later: the source is FIXED. The recovered stamps must be adopted
# (so the cap still applies), not silently discarded.
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=9 last_down_ts=$((NOW - 3000)) last_comment_ts=0 cap_notified_ts=0 restarts=$((NOW - 1500)),$((NOW - 1300)) -->\"}" > "$STUB_TMP/issue.777.json"
: > "$STUB_TMP/calls.log"
export WATCHDOG_NOW_EPOCH="$((NOW + 1200))"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "run 3: the REPAIRED source ledger is adopted → its 2 stamps still cap the hour"
assert_contains "$(patched_body)" "restarts=$((NOW - 1500)),$((NOW - 1300))" "run 3: the recovered ledger is carried into the incident body"
assert_contains "$(patched_body)" "velocity cap" "run 3: the recovered stamps trip the cap — the budget was NOT lost"
assert_not_contains "$(patched_body)" "ledger_state=invalid" "run 3: the sentinel is CLEARED once the source parses"
export WATCHDOG_NOW_EPOCH="$NOW"

# ══ #3628: the Pages auth surface (a SECOND production target) ══════════════
# Every case below FAILS on the pre-#3628 code:
#   * the healthy-302 case is RED under the old hardcoded `2??` UP arm — which
#     is exactly why a bare liveness probe of /auth/start would page on a
#     HEALTHY site;
#   * PROBE_EXPECT_STATUS / PROBE_REQUIRE_HEADER did not exist and `is_prod`
#     was a single-literal comparison, so the auth URL classified as a DRILL
#     ([DRILL]-titled incident, no PROD page).
AUTH_URL="https://app.premiselabs.co/auth/start"
API_URL="https://api.premiselabs.co/v1/organizations"
AUTH_TITLE_DOWN='[monitor] PROD DOWN — app.premiselabs.co is not answering the availability probe'
PKCE_HEADERS=$'HTTP/2 302\r\nlocation: https://github.com/login/oauth/authorize?client_id=x&code_challenge=abc&code_challenge_method=s256\r\n'

# Unit-call the pure helpers straight from the script (the WATCHDOG_LIB_ONLY
# seam). Prose-level integration cases below cover the same ground end-to-end.
classify_unit() { WATCHDOG_LIB_ONLY=1 bash -c 'source "$0"; classify_code "$1"' "$WATCHDOG" "$1"; }
prod_unit() { WATCHDOG_LIB_ONLY=1 bash -c 'source "$0"; if is_production_url "$1"; then printf PROD; else printf DRILL; fi' "$WATCHDOG" "$1"; }
restartable_unit() { WATCHDOG_LIB_ONLY=1 bash -c 'source "$0"; if is_restartable_url "$1"; then printf YES; else printf NO; fi' "$WATCHDOG" "$1"; }

# ── 93: classify_code — the allow-list is ADDITIVE, not a replacement ──────
reset_case
assert_eq "$(classify_unit 200)" "UP" "classify DEFAULT: 200 → UP"
assert_eq "$(classify_unit 401)" "UP" "classify DEFAULT: 401 → UP"
assert_eq "$(classify_unit 503)" "DOWN" "classify DEFAULT: 503 → DOWN"
assert_eq "$(classify_unit 302)" "UNEXPECTED" "classify DEFAULT: 302 → UNEXPECTED (THE TRAP a naive auth probe falls into)"
export PROBE_EXPECT_STATUS="302"
assert_eq "$(classify_unit 302)" "UP" "classify allow-list: 302 → UP"
assert_eq "$(classify_unit 503)" "DOWN" "classify allow-list keeps 5xx DOWN (302 does NOT make an outage healthy)"
assert_eq "$(classify_unit 404)" "UNEXPECTED" "classify allow-list keeps other codes UNEXPECTED"
assert_eq "$(classify_unit 200)" "UNEXPECTED" "classify allow-list: a code OUTSIDE the list is not UP"
# The DOWN arm is checked FIRST and cannot be widened by the list: a malformed
# list that names a 5xx/000 must still alert (fail closed), not disarm the probe.
export PROBE_EXPECT_STATUS="302 503 000"
assert_eq "$(classify_unit 503)" "DOWN" "classify allow-list: a LISTED 5xx is STILL DOWN (the list cannot disarm the outage class)"
assert_eq "$(classify_unit 000)" "DOWN" "classify allow-list: a LISTED 000 is STILL DOWN"
assert_eq "$(classify_unit 302)" "UP" "classify allow-list: the good code in the same list is still UP"
unset PROBE_EXPECT_STATUS

# ── 94: prod/restartable classification is SET-based and fail-closed ───────
assert_eq "$(prod_unit "$AUTH_URL")" "PROD" "prod set: the auth URL is PRODUCTION (files a PROD incident, pages)"
assert_eq "$(prod_unit "https://api.premiselabs.co/v1/organizations")" "PROD" "prod set: the API URL stays PRODUCTION"
assert_eq "$(prod_unit "https://staging.example.test/v1/organizations")" "DRILL" "prod set: an unrecognised URL is a DRILL (fail closed — no armed self-heal)"
assert_eq "$(prod_unit "https://api.premiselabs.co/v1/organizations/")" "PROD" "prod set: a trailing slash is normalised away"
assert_eq "$(restartable_unit "https://api.premiselabs.co/v1/organizations")" "YES" "restart set: the Fly API surface may restart"
assert_eq "$(restartable_unit "$AUTH_URL")" "NO" "restart set: the Pages auth surface is NEVER restartable"
assert_eq "$(restartable_unit "https://staging.example.test/v1/organizations")" "NO" "restart set: a drill is not restartable"

# ── 94b: an explicitly EMPTY set is honoured — empty ≠ unset (P3) ──────────
# `${VAR:-default}` substitutes on unset OR EMPTY, so an operator who sets
# `PROD_PROBE_URLS=""` to neutralise the set gets the exact opposite: the
# default (production membership + an ARMED restart). The documented fail-closed
# property must hold for the value an operator would actually use, so the
# default is applied ONLY when the variable is UNSET (single-dash).
export PROD_PROBE_URLS=""
assert_eq "$(prod_unit "$API_URL")" "DRILL" "empty PROD_PROBE_URLS: the API URL is a DRILL (fail closed)"
assert_eq "$(prod_unit "$AUTH_URL")" "DRILL" "empty PROD_PROBE_URLS: the auth URL is a DRILL (fail closed)"
unset PROD_PROBE_URLS
assert_eq "$(prod_unit "$API_URL")" "PROD" "unset PROD_PROBE_URLS: the defaults still apply (API production membership)"
assert_eq "$(prod_unit "$AUTH_URL")" "PROD" "unset PROD_PROBE_URLS: the defaults still apply (auth production membership)"
export RESTARTABLE_PROBE_URLS=""
assert_eq "$(restartable_unit "$API_URL")" "NO" "empty RESTARTABLE_PROBE_URLS: nothing may restart (fail closed)"
unset RESTARTABLE_PROBE_URLS
assert_eq "$(restartable_unit "$API_URL")" "YES" "unset RESTARTABLE_PROBE_URLS: the default applies (API still restartable)"

# ── 94c: an EMPTY prod set cannot ARM a restart (end to end) ────────────────
# The strongest case: sustained DOWN and a Fly token, so the ONLY thing that
# can disarm the restart is the (now empty) production set.
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export PROD_PROBE_URLS=""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL')" "0" "empty prod set: sustained DOWN + a token → ZERO flyctl calls (fail closed)"
assert_contains "$OUT" "restart decision: disarmed:drill" "empty prod set: the run log names the drill disarm"
assert_contains "$(created_json)" "DRILL DOWN" "empty prod set: files a DRILL-titled incident, not a PROD one"

# ── 94d: an EMPTY restartable set cannot ARM a restart (end to end) ─────────
reset_case
seed_issue down "$((NOW - 1200))" 3 0 ""
export RESTARTABLE_PROBE_URLS=""
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
run_watchdog
assert_eq "$(count_calls 'FLYCTL')" "0" "empty restartable set: sustained DOWN + a token → ZERO flyctl calls (fail closed)"
assert_contains "$OUT" "restart decision: disarmed:no_machine" "empty restartable set: the production URL is disarmed as no_machine"
# Membership and restartability are SEPARATE sets: emptying the restart set must
# not demote the target to a drill. The run log's [DRILL:…] marker is the
# membership signal, and the adopted production incident is still tracked.
assert_not_contains "$OUT" "[DRILL:" "empty restartable set: still PRODUCTION for alerting (membership is a separate set)"
assert_contains "$(patched_body)" "down_runs=4" "empty restartable set: the production incident is still tracked and alerted"

# ── 86: a healthy 302 + the PKCE header on the auth target → UP ─────────────
reset_case
export PROBE_URL="$AUTH_URL"
export PROBE_HOST_LABEL="app.premiselabs.co"
export PROBE_EXPECT_STATUS="302"
export PROBE_REQUIRE_HEADER="code_challenge_method=s256"
export STUB_PROBE_CODES="302"
export STUB_PROBE_HEADERS="$PKCE_HEADERS"
run_watchdog
assert_eq "$RC" "0" "auth healthy: 302 + PKCE header → exit 0 (UP)"
assert_eq "$(count_calls 'GH POST .*/issues$')" "0" "auth healthy: no incident filed"
assert_eq "$(count_calls 'FLYCTL')" "0" "auth healthy: no flyctl call"
assert_eq "$(cat "$STUB_TMP/probe.count")" "2" "auth healthy: one probe + one recovery-confirmation probe"

# ── 87: the SAME 302 without the header is NOT UP (the #3616 class) ────────
reset_case
export PROBE_URL="$AUTH_URL"
export PROBE_HOST_LABEL="app.premiselabs.co"
export PROBE_EXPECT_STATUS="302"
export PROBE_REQUIRE_HEADER="code_challenge_method=s256"
export STUB_PROBE_CODES="302"
export STUB_PROBE_HEADERS=$'HTTP/2 302\r\nlocation: https://github.com/login/oauth/authorize?client_id=x\r\n'
run_watchdog
assert_eq "$RC" "1" "auth: 302 WITHOUT the PKCE header → exit 1 (up but not signing anyone in)"
assert_contains "$(patched_body)" "required response header NOT found" "auth: the incident body names the missing required header"
# The body an operator reads FIRST is the verdict row and the heal note — not
# the raw evidence. A header failure must DIAGNOSE as a header failure, not as
# a status mismatch (the status was 302, the healthy code), and the self-healing
# note must point at the PKCE flow rather than the route/deploy surface. The
# runbook tells operators this body is the primary diagnostic, so a
# self-contradictory body sends them to the wrong place (review P2).
assert_contains "$(patched_body)" "the required response header was missing" "auth: the verdict row names the missing header (not a status mismatch)"
assert_not_contains "$(patched_body)" "an unexpected HTTP status" "auth: the verdict row does NOT claim the status was unexpected (it was 302)"
assert_contains "$(patched_body)" "PKCE" "auth: the heal note points at the PKCE flow (the right surface)"
assert_not_contains "$(patched_body)" "check the deployed revision and the route" "auth: the heal note does NOT send the operator to the route/deploy surface"
assert_contains "$(created_json)" "PROD DEGRADED" "auth: answered-but-wrong → PROD DEGRADED (an ANSWER, not an outage)"
assert_eq "$(count_calls 'FLYCTL')" "0" "auth: answered-but-wrong → no restart attempt"
assert_eq "$(cat "$STUB_TMP/probe.count")" "1" "auth: the header check is deterministic → no retry budget burned"

# ── 87b: an AUTH STATUS mismatch diagnoses the Pages route, not "an API route" ─
# The status branch (PROBE_DEGRADED_REASON=status) is the header branch's
# sibling and needs the SAME target-awareness. `/auth/start` is a Cloudflare
# Pages route, and the runbook tells operators this body is the primary
# diagnostic, so calling it "an authenticated API route" sends them to the
# wrong surface (review P3).
reset_case
export PROBE_URL="$AUTH_URL"
export PROBE_HOST_LABEL="app.premiselabs.co"
export PROBE_EXPECT_STATUS="302"
export PROBE_REQUIRE_HEADER="code_challenge_method=s256"
export STUB_PROBE_CODES="200"          # answered, but OUTSIDE the allow-list
export STUB_PROBE_HEADERS="$PKCE_HEADERS"   # header present → the failure is the STATUS
run_watchdog
assert_eq "$RC" "1" "auth status mismatch: 200 vs an allow-list of 302 → exit 1 (DEGRADED)"
assert_contains "$(created_json)" "PROD DEGRADED" "auth status mismatch: PROD DEGRADED (an ANSWER, not an outage)"
assert_contains "$(patched_body)" "disarmed" "auth status mismatch: the body says self-healing is disarmed"
assert_not_contains "$(patched_body)" "authenticated API route" "auth status mismatch: the heal note does NOT call the Pages route an API route (P3)"
assert_contains "$(patched_body)" "No restart attempted" "auth status mismatch: the heal note still explains why nothing restarted"
# The heal note's status example must be the OBSERVED code, not a hardcoded
# class: `/auth/start` expects 302, so `3xx` is the NORMAL answer and `404` is
# impossible there — the old enumeration contradicted the `http_code=200` in
# the evidence two paragraphs above (review P3).
assert_not_contains "$(patched_body)" "3xx" "auth status mismatch: the heal note never names 3xx (normal for /auth/start) as the failure (P3)"
assert_not_contains "$(patched_body)" "404" "auth status mismatch: the heal note never names 404 (impossible for /auth/start) as the failure (P3)"
assert_contains "$(patched_body)" "HTTP 200" "auth status mismatch: the heal note names the ACTUAL observed status, not a hypothetical one (P3)"
assert_eq "$(count_calls 'FLYCTL')" "0" "auth status mismatch: no restart attempt"

# ── 99b: the API target's status mismatch KEEPS the API-route guidance ──────
# The contrast case: target-awareness must not degrade the restartable API
# target's own (correct) guidance into Pages prose.
reset_case
export STUB_PROBE_CODES="404"
run_watchdog
assert_contains "$(patched_body)" "authenticated API route" "api status mismatch: the heal note still names the API surface (no regression)"

# ── 88: a 503 on the auth target → DOWN and flyctl is NEVER called ─────────
reset_case
export PROBE_URL="$AUTH_URL"
export PROBE_HOST_LABEL="app.premiselabs.co"
export PROBE_EXPECT_STATUS="302"
export PROBE_REQUIRE_HEADER="code_challenge_method=s256"
export STUB_PROBE_CODES="503"
export FLY_API_TOKEN="fly-token"     # present on purpose: the token must NOT be enough
run_watchdog
assert_eq "$RC" "1" "auth: 503 → exit 1"
assert_contains "$(created_json)" "PROD DOWN" "auth: 503 → PROD DOWN title"
assert_eq "$(count_calls 'FLYCTL')" "0" "auth: 503 → flyctl was NEVER called (no Fly machine; no restart)"
assert_contains "$OUT" "restart decision: disarmed:no_machine" "auth: the run log names the no-machine disarm"
assert_contains "$(patched_body)" "NO Fly machine" "auth: the incident body explains why nothing was restarted"

# ── 89: sustained 60 min + a Fly token is STILL a hard no-restart ──────────
# The strongest possible case for a restart and it must still not happen.
reset_case
export PROBE_URL="$AUTH_URL"
export PROBE_HOST_LABEL="app.premiselabs.co"
export PROBE_EXPECT_STATUS="302"
export STUB_PROBE_CODES="503"
export FLY_API_TOKEN="fly-token"
jq -n --arg b "<!-- watchdog-state kind=down first_failure_ts=$((NOW - 3600)) down_runs=20 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts= -->" '{body:$b}' > "$STUB_TMP/issue.json"
STUB_SEARCH_JSON="$(search_json 77 "$AUTH_TITLE_DOWN")"
export STUB_SEARCH_JSON
export STUB_SEARCH_MARKER='PROD%20DOWN'
run_watchdog
assert_eq "$(count_calls 'FLYCTL')" "0" "auth sustained: 60 min down + a Fly token → STILL zero flyctl calls (hard disarm)"
assert_contains "$OUT" "restart decision: disarmed:no_machine" "auth sustained: the disarm is still no_machine"
assert_contains "$(patched_body)" "down_runs=21" "auth sustained: the incident is still tracked and alerted (count increments)"
assert_contains "$(comments_all)" "NO Fly machine" "auth sustained: a human sees why nothing restarted"

# ── 90: the auth URL is PROD (not DRILL) → PROD title with its OWN label ───
reset_case
export PROBE_URL="$AUTH_URL"
export PROBE_HOST_LABEL="app.premiselabs.co"
export PROBE_EXPECT_STATUS="302"
export STUB_PROBE_CODES="503"
run_watchdog
assert_contains "$(created_json)" "[monitor] PROD DOWN" "auth: the incident is PROD-titled (the URL is in the prod SET)"
assert_not_contains "$(created_json)" "DRILL" "auth: the incident is NOT a drill"
assert_contains "$(created_json)" "app.premiselabs.co" "auth: the title carries the auth host — its OWN dedupe key"

# ── 90b: the auth run never adopts (or mutates) the API incident ───────────
reset_case
seed_issue down "$((NOW - 600))" 3 0 ""   # seeds an OPEN API incident (#42)
export PROBE_URL="$AUTH_URL"
export PROBE_HOST_LABEL="app.premiselabs.co"
export PROBE_EXPECT_STATUS="302"
export STUB_PROBE_CODES="503"
run_watchdog
assert_eq "$(count_calls 'GH POST .*/issues$')" "1" "auth: the API incident is NOT adopted → the auth incident is filed fresh"
assert_eq "$(count_calls 'GH PATCH repos/.*/issues/42$')" "0" "auth: the API incident (#42) is NEVER mutated (separate dedupe identity)"
assert_contains "$(created_json)" "app.premiselabs.co" "auth: the fresh incident carries the auth host"

# ── 91: the required-header match is CASE-INSENSITIVE ──────────────────────
reset_case
export PROBE_URL="$AUTH_URL"
export PROBE_HOST_LABEL="app.premiselabs.co"
export PROBE_EXPECT_STATUS="302"
export PROBE_REQUIRE_HEADER="code_challenge_method=s256"
export STUB_PROBE_CODES="302"
export STUB_PROBE_HEADERS=$'HTTP/2 302\r\nLocation: https://x/authorize?Code_Challenge_Method=S256\r\n'
run_watchdog
assert_eq "$RC" "0" "auth: header name/value casing is irrelevant → still UP"

# ── 92: a trailing slash still classifies as PROD ──────────────────────────
reset_case
export PROBE_URL="$AUTH_URL/"
export PROBE_HOST_LABEL="app.premiselabs.co"
export PROBE_EXPECT_STATUS="302"
export STUB_PROBE_CODES="503"
run_watchdog
assert_contains "$(created_json)" "[monitor] PROD DOWN" "auth: a trailing slash is normalised → still PROD, not a drill"

# ── 95: the API target's contract is UNCHANGED with the knobs unset ────────
# The end-to-end regression is the whole 1-33 block above (it runs with no
# knobs); these two make the contrast explicit against the auth cases.
reset_case
export STUB_PROBE_CODES="302"
run_watchdog
assert_eq "$RC" "1" "api regression: a bare 302 with NO allow-list is still UNEXPECTED"
assert_contains "$(created_json)" "PROD DEGRADED" "api regression: 302 → DEGRADED, not DOWN"
reset_case
export STUB_PROBE_CODES="503"
run_watchdog
assert_eq "$RC" "1" "api regression: 503 → DOWN"
assert_eq "$(count_calls 'FLYCTL')" "0" "api regression: 503 with no token → no restart (unchanged)"

# ── 97: a malformed allow-list cannot make a 5xx healthy (end to end) ──────
reset_case
export PROBE_URL="$AUTH_URL"
export PROBE_HOST_LABEL="app.premiselabs.co"
export PROBE_EXPECT_STATUS="302 503"    # 503 must NOT become healthy
export STUB_PROBE_CODES="503"
run_watchdog
assert_eq "$RC" "1" "allow-list: a LISTED 5xx still fails the run (fail closed)"
assert_contains "$(created_json)" "PROD DOWN" "allow-list: a listed 5xx still files a DOWN incident"
assert_eq "$(count_calls 'FLYCTL')" "0" "allow-list: a listed 5xx on the auth target still never restarts"

# ── 98: the no-Fly-machine prose keys on RESTARTABILITY, not the knob ──────
# With an expectation knob set on the RESTARTABLE API target, the public incident
# body must NOT tell an operator that nothing will restart (it could).
reset_case
export PROBE_URL="https://api.premiselabs.co/v1/organizations"
export PROBE_EXPECT_STATUS="200"
export STUB_PROBE_CODES="503"
run_watchdog
assert_eq "$RC" "1" "prose: an expectation knob on the API target still alerts"
assert_not_contains "$(patched_body)" "no Fly machine" "prose: a RESTARTABLE target's body never claims there is no Fly machine"

# ── 85: workflow credential containment (round 4, P3-5/P3-6) ────────────────
# These invariants live in the workflow, not the script, so the harness cannot
# drive them — a STATIC check is the only automated guard. Both FAIL on the
# round-3 workflow (no `persist-credentials`, TELEGRAM at job level).
WORKFLOW="$SCRIPT_DIR/../workflows/availability-watchdog.yml"
# Comments NAME these invariants to explain them, so every assertion runs
# against a comment-stripped view — otherwise the guard is satisfied by its own
# explanatory comment (it was: the first draft passed with the fix reverted).
WORKFLOW_CODE="$(grep -v '^[[:space:]]*#' "$WORKFLOW")"
assert_contains "$WORKFLOW_CODE" "persist-credentials: false" \
  "the checkout step does not persist the workflow token into .git/config (P3-5)"
assert_contains "$WORKFLOW_CODE" "TELEGRAM_BOT_TOKEN: \${{ secrets.TELEGRAM_BOT_TOKEN }}" \
  "TELEGRAM_BOT_TOKEN is exported to the probe step"
assert_contains "$WORKFLOW_CODE" "TELEGRAM_CHAT_ID: \${{ secrets.TELEGRAM_CHAT_ID }}" \
  "TELEGRAM_CHAT_ID is exported to the probe step"
# Everything up to and including `steps:` is the JOB-level env — a secret there
# is handed to actions/checkout and to the third-party setup-flyctl action.
# Comments are stripped: the comments legitimately NAME the secrets to explain
# why they are not here.
JOB_ENV="$(sed -n '1,/^    steps:/p' "$WORKFLOW" | grep -v '^[[:space:]]*#' || true)"
assert_not_contains "$JOB_ENV" "TELEGRAM_BOT_TOKEN" "TELEGRAM_BOT_TOKEN is NOT job-level (step env only, P3-6)"
assert_not_contains "$JOB_ENV" "TELEGRAM_CHAT_ID" "TELEGRAM_CHAT_ID is NOT job-level (step env only, P3-6)"
assert_not_contains "$JOB_ENV" "FLY_API_TOKEN" "FLY_API_TOKEN is NOT job-level (step env only)"
assert_not_contains "$JOB_ENV" "GH_TOKEN" "GH_TOKEN is NOT job-level (step env only)"
# GitHub env precedence is STEP > JOB > WORKFLOW, so refusing PROD_PROBE_URLS on
# the step is not enough: a job-level value reaches the script just as well and
# narrows the production set, classifying the auth probe a DRILL ([DRILL]-titled,
# no page, self-heal disarmed) while the diff looks correct. Found in review — the
# step-scoped refusal below passes with a job-level PROD_PROBE_URLS present.
assert_not_contains "$JOB_ENV" "PROD_PROBE_URLS" \
  "PROD_PROBE_URLS is NOT job-level either (env precedence: job-level reaches the script and silently drills the auth probe)"
assert_not_contains "$JOB_ENV" "PROBE_HOST_LABEL" \
  "PROBE_HOST_LABEL is NOT job-level (a job-level label renames another surface's incident to a host it never probes)"
# …and the positional slice above is not enough on its own. GitHub applies a
# top-level env: to EVERY job and step, and YAML key order is not semantic, so a
# workflow-level PROD_PROBE_URLS placed AFTER `steps:` sits outside JOB_ENV — the
# reviewer reproduced it (a top-level `env: PROD_PROBE_URLS: …` appended to the
# file, guard still 0 failures, auth probe silently a DRILL). Assert on the whole
# comment-stripped workflow instead; that closes step, job, workflow-anywhere and
# second-job placements at once. (The one `PROD_PROBE_URLS` mention in the file is
# a comment and is stripped — a comment must never satisfy its own guard.)
assert_not_contains "$WORKFLOW_CODE" "PROD_PROBE_URLS" \
  "PROD_PROBE_URLS is not set at ANY level of the watchdog workflow (the workflow's own comment is stripped, so it cannot satisfy this)"
# #3887 round 2: the NEW knob joins the same containment surface. Added to the
# guard in the same change that adds the knob — otherwise a workflow-level
# ESCALATION_CHAT_ID placed after `steps:` escapes the pre-steps slice and
# silently re-points EVERY sustained page at a different chat, with nothing red.
# (Same class as the PROD_PROBE_URLS escape recorded in the #4286 review.)
assert_contains "$WORKFLOW_CODE" "ESCALATION_CHAT_ID: \${{ secrets.ESCALATION_CHAT_ID }}" \
  "ESCALATION_CHAT_ID is exported to the probe steps (step env)"
assert_not_contains "$JOB_ENV" "ESCALATION_CHAT_ID" \
  "ESCALATION_CHAT_ID is NOT job-level (step env only — otherwise the override escapes containment)"
assert_eq "$(printf '%s' "$WORKFLOW_CODE" | grep -c 'ESCALATION_CHAT_ID: \${{ secrets.ESCALATION_CHAT_ID }}')" "2" \
  "ESCALATION_CHAT_ID is set on BOTH probe steps (the API step and the auth step fail independently)"
# #3887 round 5: the KILL SWITCH joins the same containment surface. The runbook
# documents ESCALATE_ENABLED as an operator control, so it must be wired — an
# unwired knob is a documented control an operator cannot engage without editing
# the workflow, and the unset repo variable silently leaves the script default
# (paging enabled) in place. It comes from the repository VARIABLE (not a secret:
# it is a switch, not a credential), and it stays STEP-level: the two probes fail
# independently, so both must carry it, and step placement keeps the knob visible
# only where it is consumed (a job-level env would also hand it to
# actions/checkout and the third-party setup-flyctl action). A workflow-level
# `vars.` placed after `steps:` would escape the JOB_ENV slice — hence the
# whole-workflow check.
assert_contains "$WORKFLOW_CODE" "ESCALATE_ENABLED: \${{ vars.ESCALATE_ENABLED }}" \
  "ESCALATE_ENABLED is wired to the probe steps from the repo variable (a documented operator control must be reachable)"
assert_not_contains "$JOB_ENV" "ESCALATE_ENABLED" \
  "ESCALATE_ENABLED is NOT job-level (step env only — the same containment discipline as ESCALATION_CHAT_ID)"
assert_eq "$(printf '%s' "$WORKFLOW_CODE" | grep -c 'ESCALATE_ENABLED: \${{ vars.ESCALATE_ENABLED }}')" "2" \
  "ESCALATE_ENABLED is set on BOTH probe steps (wiring only one leaves the other surface unkillable)"

# ── 96: the workflow drives the auth surface as its OWN step (#3628) ───────
AUTH_WORKFLOW="$SCRIPT_DIR/../workflows/availability-watchdog.yml"
# Same comment-stripping rule as case 85: a comment must not satisfy the guard.
AUTH_STEP="$(sed -n '/Probe the Pages auth surface/,/availability-watchdog.sh/p' "$AUTH_WORKFLOW" | grep -v '^[[:space:]]*#' || true)"
assert_contains "$AUTH_STEP" "https://app.premiselabs.co/auth/start" "the auth step probes the SESSION-BEARING origin (/auth/start on app.*, #4054)"
assert_contains "$AUTH_STEP" "PROBE_EXPECT_STATUS: '302'" "the auth step expects a 302"
assert_contains "$AUTH_STEP" "PROBE_REQUIRE_HEADER: 'code_challenge_method=s256'" "the auth step requires the PKCE header"
# The host label is the incident DEDUPE KEY and `is_prod` is decided by SET
# MEMBERSHIP over the URLs, so the URL, its label and the script's constant must
# move TOGETHER. These are DERIVED, not pinned to a literal, because that is the
# exact drift that stranded the probe: #4054 moved the BFF to app.* and left all
# three behind, so a literal pin would have had to be edited in three places and
# the guard would have gone on passing while sign-in was unmonitored.
AUTH_PROBE_URL_STEP="$(printf '%s\n' "$AUTH_STEP" | sed -n "s|.*PROBE_URL:[[:space:]]*[\"']\{0,1\}\(https://[^ \"']*\)[\"']\{0,1\}.*|\1|p" | head -1)"
AUTH_HOST="$(printf '%s' "$AUTH_PROBE_URL_STEP" | sed -n 's|https://\([^/]*\)/.*|\1|p')"
# EMPTY DERIVATION MUST FAIL, not pass vacuously: assert_contains is a `case`
# glob, so an empty needle matches ANY haystack — a quoted or folded PROBE_URL
# would make both assertions below silently vacuous, which is the exact
# silent-DRILL failure they exist to catch. (Found in review; reproduced with
# the value quoted.)
assert_not_empty "$AUTH_PROBE_URL_STEP" "the auth step's PROBE_URL is derivable (a quoted/folded value must not silently vacate these guards)"
assert_not_empty "$AUTH_HOST" "the auth step's probe host is derivable from its PROBE_URL"
assert_contains "$AUTH_STEP" "PROBE_HOST_LABEL: $AUTH_HOST" \
  "the auth step's host label matches the host it actually probes (its dedupe key)"
# …and the script must classify that same URL as PRODUCTION. PROD_PROBE_URLS is
# built from AUTH_PROBE_URL; if only the workflow moves, the step still runs but
# is classified a DRILL — [DRILL]-titled, no page, self-heal disarmed — while
# looking correct in the diff. Setting PROD_PROBE_URLS in the step would do the
# same thing through another route, so it is refused too.
assert_contains "$(grep '^AUTH_PROBE_URL=' "$WATCHDOG" || true)" "$AUTH_PROBE_URL_STEP" \
  "the watchdog script's AUTH_PROBE_URL matches the step's probe URL (else the run is silently a DRILL)"
assert_not_contains "$AUTH_STEP" "PROD_PROBE_URLS" \
  "the auth step does not set PROD_PROBE_URLS (a second route to a silent DRILL)"
# A step-scoped guard cannot see a SECOND step: the old target can be re-added
# under a new name ("legacy check", a copy-paste, another surface) and refile the
# false PROD DEGRADED incident with every guard above still green. So assert the
# INVENTORY, not just this step — no comment-stripped line in the workflow may
# name the pre-#4054 auth target. (Review mutant: a second step carrying
# PROBE_URL/PROBE_HOST_LABEL on the old host passed the step-scoped guard.)
AUTH_WORKFLOW_CODE="$(grep -v '^[[:space:]]*#' "$AUTH_WORKFLOW" || true)"
assert_not_contains "$AUTH_WORKFLOW_CODE" "tortoise.premiselabs.co/auth/start" \
  "no second step re-adds the pre-#4054 auth target (a 'legacy' probe would refile the false incident)"
# Banning the old URL is not enough: the LABEL is the exact-title dedupe key, so a
# label left behind on a *different* line orphans the incident just as the URL did.
# The reviewer's mutants that slipped past a URL-only inventory: a SECOND
# PROBE_HOST_LABEL line in the auth step (YAML last-wins → the old host), a second
# step with the new URL but the old label, and a label on another surface. The old
# host has no legitimate occurrence in this workflow at all, so ban the HOST, and
# require the auth step to carry exactly one label line.
assert_not_contains "$AUTH_WORKFLOW_CODE" "tortoise.premiselabs.co" \
  "the departed host appears nowhere in the watchdog workflow (a leftover PROBE_HOST_LABEL would orphan that surface's incident)"
assert_eq "$(printf '%s\n' "$AUTH_STEP" | grep -c 'PROBE_HOST_LABEL:')" "1" \
  "the auth step carries exactly ONE host label (a duplicate line is last-wins and could re-point the dedupe key)"
assert_not_contains "$AUTH_STEP" "FLY_API_TOKEN" "the auth step gets NO Fly token (no restart path)"
assert_contains "$AUTH_STEP" '!cancelled()' "the auth step runs even when the API probe failed (independent alerting)"
assert_contains "$AUTH_STEP" "TELEGRAM_BOT_TOKEN: \${{ secrets.TELEGRAM_BOT_TOKEN }}" "the auth step can page too"

# ════════════════════════════════════════════════════════════════════════════
# #3887 — the sustained-incident ESCALATION LEG (leaves GitHub: a thresholded,
# addressed page for the class the restart leg declines).
#
# Acceptance criteria covered here:
#  (unit) decide_escalation: off | wait_sustained | wait_runs | wait_page_quiet |
#         wait_reminder | page | remind
#  (unit) normalize_escalation_knobs: derived defaults + the `>= restart` clamp
#  (unit) parse_state: escalate_ts is trusted ONLY while escalate_state=sent;
#         pending/failed/absent ⇒ retried; a FUTURE stamp is clamped to 0
#  (unit) state-block field parity (declared vs rendered, both directions + order)
#  (doc)  the runbook's state-block field list names every declared field
#  (e2e a) one failing run ⇒ NO sustained page
#  (e2e b) 3 runs + >=30 min ⇒ exactly ONE page
#  (e2e c) inside the reminder window ⇒ no page
#  (e2e d) channel unconfigured ⇒ NO delivery stamp + escalate_state=failed + a
#          loud failure naming the channel (never "all clear")
#  (e2e e) HTTP 2xx with ok:false ⇒ NOT delivered (the ok:true contract)
#  (e2e f) a FUTURE escalate_ts ⇒ pages (a stamp that cannot be true never mutes)
#  (e2e g) a page in the SAME run ⇒ no second page
#  (e2e h) a confirmed page 10 min ago ⇒ no second page (cross-mechanism bound)
#  (e2e i) sustained DEGRADED/UNEXPECTED ⇒ pages (the #3887 incident's class)
#  (e2e j) ESCALATE_ENABLED=0 ⇒ no send, no stamp
#  (e2e k) a failed state write ⇒ NO escalation send (no notify without a record)
#  (e2e l) a sustained DRILL pages but still never restarts
#  (e2e m) the public body/log never carry the recipient id
#  (e2e n) a stale confirmed escalation ⇒ a REMINDER page
#  (e2e o) ESCALATION_CHAT_ID really is a separate, working recipient
#  (unit) G1: a non-numeric/non-boolean ESCALATE_ENABLED (false/off/…), which
#         `to_int` collapses to the default 1, is LOUD — not silently paging
#  (unit) G2: the escalation wall-clock is ANCHORED, so a stale-clock reset
#         (first_failure_ts=now) cannot zero a >45-min-cadence incident's window
#  (unit) G3: a body-forged FUTURE anchor is untrustworthy and defers to the
#         run leg (fail toward paging) instead of muting the pager forever
#  (e2e b2) a >STALE_RESET_MINUTES-cadence incident created 3 h ago → PAGE
#  (e2e b3) a forged future first_failure_ts → PAGE (created_at is the anchor)
#  (e2e b4) an UNUSABLE created_at → PAGE with NO 1970-derived age (run leg
#         alone authorises it; an untrustworthy anchor carries no age)
# ════════════════════════════════════════════════════════════════════════════

# Unit-call decide_escalation() through the script's own LIB_ONLY seam. The
# function reads persisted STATE_* only, so a unit call drives every branch with
# no probe, no issue and no network. WATCHDOG_NOW_EPOCH is set EXPLICITLY (never
# inherited from an earlier case) so each helper is order-independent.
esc_unit() { # <first_failure_ts> <down_runs> <escalate_ts> <escalate_state> <page_ok_ts> <now>
  WATCHDOG_NOW_EPOCH="$NOW" WATCHDOG_LIB_ONLY=1 bash -c '
    source "$0"
    normalize_escalation_knobs 10 2
    STATE_FIRST_FAILURE_TS="$1"; STATE_DOWN_RUNS="$2"; STATE_ESCALATE_TS="$3"
    STATE_ESCALATE_STATE="$4"; STATE_PAGE_OK_TS="$5"
    decide_escalation "$6"
  ' "$WATCHDOG" "$1" "$2" "$3" "$4" "$5" "$6"
}

# Unit-call decide_escalation() with an EXPLICIT escalation anchor — the value
# main() resolves from the incident's server-side `created_at`. This is the ONE
# seam that can drive the G2/G3 anchoring without dragging the whole
# probe/issue/network stub through a run, and it lets the two anchors differ
# (which the real stale-reset path needs).
esc_anchor_unit() { # <anchor> <first_failure_ts> <down_runs> <now>
  WATCHDOG_NOW_EPOCH="$NOW" WATCHDOG_LIB_ONLY=1 bash -c '
    source "$0"
    normalize_escalation_knobs 10 2
    STATE_ESCALATE_ANCHOR_TS="$1"
    STATE_FIRST_FAILURE_TS="$2"; STATE_DOWN_RUNS="$3"
    STATE_ESCALATE_TS=0; STATE_ESCALATE_STATE=""; STATE_PAGE_OK_TS=0
    decide_escalation "$4"
  ' "$WATCHDOG" "$1" "$2" "$3" "$4"
}

# Unit-call parse_state() and print the escalation triple it derived. parse_state
# reads the clock (the future-stamp clamps), so WATCHDOG_NOW_EPOCH is pinned here
# too — otherwise the assertion depends on whatever an earlier case exported.
parse_esc() { # <body> -> "escalate_ts|escalate_state|page_ok_ts"
  WATCHDOG_NOW_EPOCH="$NOW" WATCHDOG_LIB_ONLY=1 bash -c '
    source "$0"
    parse_state "$1"
    printf "%s|%s|%s" "$STATE_ESCALATE_TS" "$STATE_ESCALATE_STATE" "$STATE_PAGE_OK_TS"
  ' "$WATCHDOG" "$1"
}

# Unit-call the knob normalizer with an arbitrary sustained pair.
knobs_unit() { # <s_min> <s_runs> -> "minutes/runs"
  WATCHDOG_NOW_EPOCH="$NOW" WATCHDOG_LIB_ONLY=1 bash -c '
    source "$0"
    normalize_escalation_knobs "$1" "$2"
    printf "%s/%s" "$ESCALATE_SUSTAINED_MINUTES" "$ESCALATE_MIN_RUNS"
  ' "$WATCHDOG" "$1" "$2"
}

# The knob normalizer's STDERR (the coercion warning). `2>&1 >/dev/null` puts
# stderr on the captured pipe while discarding stdout.
knobs_warn_unit() { # <s_min> <s_runs> -> stderr text
  WATCHDOG_NOW_EPOCH="$NOW" WATCHDOG_LIB_ONLY=1 bash -c '
    source "$0"
    normalize_escalation_knobs "$1" "$2"
  ' "$WATCHDOG" "$1" "$2" 2>&1 >/dev/null
}

block_unit()  { WATCHDOG_LIB_ONLY=1 bash -c 'source "$0"; state_block down' "$WATCHDOG"; }
fields_unit() { WATCHDOG_LIB_ONLY=1 bash -c 'source "$0"; printf "%s" "$STATE_FIELDS"' "$WATCHDOG"; }

# ── unit: decide_escalation, every outcome ─────────────────────────────────
assert_eq "$(esc_unit "$((NOW - 60))" 5 0 "" 0 "$NOW")" "wait_sustained" "esc: 1 min of failure → wait_sustained (a single tick cannot page)"
assert_eq "$(esc_unit "$((NOW - 300))" 5 0 "" 0 "$NOW")" "wait_sustained" "esc: 5 min → wait_sustained (the wall-clock leg alone does not page)"
assert_eq "$(esc_unit "$((NOW - 1900))" 2 0 "" 0 "$NOW")" "wait_runs" "esc: ≥30 min but only 2 observed runs → wait_runs (the run leg is required too)"
assert_eq "$(esc_unit "$((NOW - 1900))" 3 0 "" 0 "$NOW")" "page" "esc: 30 min AND 3 runs → page (both legs satisfied)"
assert_eq "$(esc_unit "$((NOW - 7200))" 20 0 "" 0 "$NOW")" "page" "esc: a long incident → page"
assert_eq "$(esc_unit "$((NOW - 7200))" 20 0 "" "$((NOW - 300))" "$NOW")" "wait_page_quiet" "esc: a CONFIRMED human page 5 min ago → wait_page_quiet (no double page)"
assert_eq "$(esc_unit "$((NOW - 7200))" 20 "$((NOW - 300))" sent 0 "$NOW")" "wait_reminder" "esc: this leg paged 5 min ago → wait_reminder"
assert_eq "$(esc_unit "$((NOW - 9000))" 20 "$((NOW - 7200))" sent 0 "$NOW")" "remind" "esc: this leg paged 2 h ago → remind"
assert_eq "$(esc_unit "$((NOW - 9000))" 20 "$((NOW - 7200))" sent "$((NOW - 100))" "$NOW")" "wait_page_quiet" "esc: a more recent confirmed page wins over the reminder window"
assert_eq "$(ESCALATE_ENABLED=0 esc_unit "$((NOW - 7200))" 20 0 "" 0 "$NOW")" "off" "esc: ESCALATE_ENABLED=0 → off (operator kill switch)"
assert_eq "$(ESCALATE_SUSTAINED_MINUTES=60 esc_unit "$((NOW - 1900))" 20 0 "" 0 "$NOW")" "wait_sustained" "esc: an explicit longer wall-clock threshold is honoured"
assert_eq "$(ESCALATE_MIN_RUNS=5 esc_unit "$((NOW - 7200))" 3 0 "" 0 "$NOW")" "wait_runs" "esc: an explicit higher run threshold is honoured"
# Both legs are REQUIRED — a burst of queued runs cannot fake the wall clock.
assert_eq "$(esc_unit "$((NOW - 600))" 99 0 "" 0 "$NOW")" "wait_sustained" "esc: 99 runs in 10 min → STILL wait_sustained (runs alone cannot page)"

# ── unit: the thresholds derive from the restart gate and clamp UP ──────────
assert_eq "$(knobs_unit 10 2)" "30/3" "knobs: derived defaults 30 min / 3 runs at the wired sustained pair"
assert_eq "$(knobs_unit 20 4)" "60/5" "knobs: the defaults SCALE with the sustained pair (one declared relation, not two literals)"
assert_eq "$(ESCALATE_SUSTAINED_MINUTES=1 ESCALATE_MIN_RUNS=1 knobs_unit 10 2)" "10/2" "knobs: an explicit escalation threshold BELOW the restart gate is clamped UP (a human must never page before the automated action)"
assert_eq "$(ESCALATE_SUSTAINED_MINUTES=99 ESCALATE_MIN_RUNS=9 knobs_unit 10 2)" "99/9" "knobs: an explicit LOOSER threshold is honoured (clamping only ever tightens)"
# A NON-BOOLEAN kill switch must fail CLOSED toward paging. `banana` was a trap:
# a `to_int`-first implementation coerces any non-numeric value to the default 1,
# so asserting on `banana` alone left the boolean check unpinned — deleting it
# kept the suite green, and a numeric `ESCALATE_ENABLED=2` then read as the
# fail-OPEN `off`. The RAW value is now the ONLY kill-switch predicate, which is
# why the digit-bearing cases at the end of this block matter as much as the
# spelled-out ones.
assert_eq "$(ESCALATE_ENABLED=2 knobs_unit 10 2)" "30/3" "knobs: a numeric non-boolean kill switch (2) leaves the derived thresholds alone"
assert_eq "$(ESCALATE_ENABLED=2 esc_unit "$((NOW - 7200))" 20 0 "" 0 "$NOW")" "page" "knobs: ESCALATE_ENABLED=2 still PAGES (fail CLOSED toward paging, never 'off')"
assert_contains "$(ESCALATE_ENABLED=2 knobs_warn_unit 10 2)" "is not 0 or 1" "knobs: coercing a non-boolean kill switch is LOUD"
# G1: `to_int` runs first, so the BOOLEAN-SPELLED kill switches never reach the
# case above — they collapse to the default 1 and the operator's
# `ESCALATE_ENABLED=false` SILENTLY keeps paging. The RAW check must warn for
# these too, while still coercing toward paging.
assert_contains "$(ESCALATE_ENABLED=false knobs_warn_unit 10 2)" "is not 0 or 1" "knobs: ESCALATE_ENABLED=false is LOUD (silent coercion to 1 would keep paging)"
assert_contains "$(ESCALATE_ENABLED=off knobs_warn_unit 10 2)" "is not 0 or 1" "knobs: ESCALATE_ENABLED=off is LOUD too"
assert_eq "$(ESCALATE_ENABLED=false esc_unit "$((NOW - 7200))" 20 0 "" 0 "$NOW")" "page" "knobs: ESCALATE_ENABLED=false still PAGES (fail CLOSED toward paging, never silently 0)"
# …and the warning is NOT a blanket one: the valid values stay quiet.
assert_eq "$(ESCALATE_ENABLED=1 knobs_warn_unit 10 2)" "" "knobs: a valid ESCALATE_ENABLED=1 is NOT warned (the raw check is targeted)"
# `to_int` strips NON-DIGITS, so `00`, `0abc`, `0.0` and `0x` all collapse to
# `0` — under a `to_int`-first implementation that SILENTLY MUTED the pager: a
# malformed operator value choosing the kill switch, which is the #3887 failure
# mode sitting inside its own fix. Only a LITERAL `0` may disable escalation.
assert_eq "$(ESCALATE_ENABLED=00 esc_unit "$((NOW - 7200))" 20 0 "" 0 "$NOW")" "page" "knobs: ESCALATE_ENABLED=00 still PAGES (a digit-bearing non-boolean must not mute)"
assert_eq "$(ESCALATE_ENABLED=0abc esc_unit "$((NOW - 7200))" 20 0 "" 0 "$NOW")" "page" "knobs: ESCALATE_ENABLED=0abc still PAGES (to_int must not pick the kill switch)"
assert_eq "$(ESCALATE_ENABLED=0.0 esc_unit "$((NOW - 7200))" 20 0 "" 0 "$NOW")" "page" "knobs: ESCALATE_ENABLED=0.0 still PAGES"
assert_eq "$(ESCALATE_ENABLED=0x esc_unit "$((NOW - 7200))" 20 0 "" 0 "$NOW")" "page" "knobs: ESCALATE_ENABLED=0x still PAGES"
assert_contains "$(ESCALATE_ENABLED=0abc knobs_warn_unit 10 2)" "is not 0 or 1" "knobs: a digit-bearing non-boolean kill switch is LOUD"
assert_eq "$(ESCALATE_ENABLED=0 knobs_warn_unit 10 2)" "" "knobs: a valid ESCALATE_ENABLED=0 is NOT warned"
# `banana` is not a boolean either, but it reaches the `*)` branch and resolves
# to the default 1 — assert what that path actually does.
assert_eq "$(ESCALATE_ENABLED=banana esc_unit "$((NOW - 7200))" 20 0 "" 0 "$NOW")" "page" "knobs: a non-numeric kill switch (banana → default 1) still pages"
# The WHITESPACE subclass (round 5). The previous normalize ran
# `tr -d '[:space:]'` before the case, so `" 0"` / `"0\n"` / `" "` took the
# kill switch with NO warning — a silent mute of a fail-closed pager. A YAML
# block/folded scalar in a workflow `env:` (`|` or `>`) produces exactly
# `"0\n"`, so this is not hypothetical. Only a BYTE-EXACT `0` may disable
# escalation; every padded form pages AND warns.
assert_eq "$(ESCALATE_ENABLED=' 0' esc_unit "$((NOW - 7200))" 20 0 "" 0 "$NOW")" "page" \
  "knobs: a LEADING-space ' 0' still PAGES (whitespace must not select the kill switch)"
assert_contains "$(ESCALATE_ENABLED=' 0' knobs_warn_unit 10 2)" "is not 0 or 1" \
  "knobs: a LEADING-space ' 0' is LOUD (a silent mute is the failure mode)"
assert_eq "$(ESCALATE_ENABLED='0 ' esc_unit "$((NOW - 7200))" 20 0 "" 0 "$NOW")" "page" \
  "knobs: a TRAILING-space '0 ' still PAGES"
assert_contains "$(ESCALATE_ENABLED='0 ' knobs_warn_unit 10 2)" "is not 0 or 1" \
  "knobs: a TRAILING-space '0 ' is LOUD too"
assert_eq "$(ESCALATE_ENABLED=$'0\n' esc_unit "$((NOW - 7200))" 20 0 "" 0 "$NOW")" "page" \
  "knobs: a YAML block-scalar '0\\n' still PAGES (the real workflow-env shape)"
assert_contains "$(ESCALATE_ENABLED=$'0\n' knobs_warn_unit 10 2)" "is not 0 or 1" \
  "knobs: a YAML block-scalar '0\\n' is LOUD"
assert_contains "$(ESCALATE_ENABLED=' ' knobs_warn_unit 10 2)" "is not 0 or 1" \
  "knobs: whitespace-only ' ' is LOUD (it is not the empty default)"
assert_eq "$(ESCALATE_ENABLED=' ' esc_unit "$((NOW - 7200))" 20 0 "" 0 "$NOW")" "page" \
  "knobs: whitespace-only ' ' still PAGES (never silently the kill switch)"
# …and the exact values stay quiet, so the whitespace guard did not become a
# blanket warning.
assert_eq "$(ESCALATE_ENABLED=0 knobs_warn_unit 10 2)" "" "knobs: a byte-exact 0 is still NOT warned"
assert_eq "$(ESCALATE_ENABLED=1 knobs_warn_unit 10 2)" "" "knobs: a byte-exact 1 is still NOT warned"

# ── unit: the ESCALATION wall-clock ANCHOR (G2/G3) ─────────────────────────
# G2: the stale-clock reset sets first_failure_ts=now, which used to make the
# escalation window unsatisfiable forever (the exact #3887 failure). Anchored on
# the incident's 3-hour-old created_at, the wall-clock leg still passes and the
# run leg pages.
assert_eq "$(esc_anchor_unit "$((NOW - 10800))" "$NOW" 20 "$NOW")" "page" \
  "G2: an incident created 3 h ago survives a stale-clock reset (ff=now) → page, not wait_sustained"
assert_eq "$(esc_anchor_unit "$((NOW - 10800))" "$NOW" 1 "$NOW")" "wait_runs" \
  "G2: the anchor alone does NOT page — the run leg still gates a long-lived incident's FIRST observed run"
assert_eq "$(esc_anchor_unit "$((NOW - 10800))" "$NOW" 3 "$NOW")" "page" \
  "G2: …and it pages once run-leg quorum is reached (~30 min of observed runs)"
# G3: a body-forged FUTURE first_failure_ts must not mute the pager. A future
# anchor is untrustworthy and defers to the run leg (fail toward paging).
assert_eq "$(esc_anchor_unit "$((NOW + 100000))" "$((NOW + 100000))" 20 "$NOW")" "page" \
  "G3: a FUTURE anchor is untrustworthy → page (fail toward paging), never a permanent mute"
assert_eq "$(esc_anchor_unit "$((NOW + 100000))" "$((NOW + 100000))" 2 "$NOW")" "wait_runs" \
  "G3: …but the run leg still gates it — a future stamp alone cannot page"
assert_eq "$(esc_anchor_unit "$((NOW + 100000))" "$((NOW - 7200))" 20 "$NOW")" "page" \
  "G3: a future anchor does not gate even when the fallback clock alone would pass (fail toward paging)"
# The unstaged fallback (main() resolved no anchor — the pure caller path):
# a future first_failure_ts must not mute.
assert_eq "$(esc_unit "$((NOW + 100000))" 20 0 "" 0 "$NOW")" "page" \
  "G3: a future first_failure_ts with NO anchor → page (fail toward paging)"
assert_eq "$(esc_unit "$((NOW + 100000))" 2 0 "" 0 "$NOW")" "wait_runs" \
  "G3: …still gated by the run leg"

# ── unit: parse_state gates the stamp on its OUTCOME ───────────────────────
assert_eq "$(parse_esc "<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 escalate_state=sent escalate_ts=$((NOW - 600)) page_ok_ts=$((NOW - 300)) restarts= -->")" \
  "$((NOW - 600))|sent|$((NOW - 300))" "parse: a CONFIRMED escalation is trusted, and page_ok_ts round-trips"
assert_eq "$(parse_esc "<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 escalate_state=pending escalate_ts=$((NOW - 600)) restarts= -->")" \
  "0|pending|0" "parse: 'pending' (a run died before recording the outcome) ⇒ the stamp is NOT trusted ⇒ retried"
assert_eq "$(parse_esc "<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 escalate_state=failed escalate_ts=0 restarts= -->")" \
  "0|failed|0" "parse: 'failed' ⇒ not trusted ⇒ retried next run"
assert_eq "$(parse_esc "<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 escalate_ts=$((NOW - 600)) restarts= -->")" \
  "0||0" "parse: a stamp with NO recorded outcome ⇒ not trusted (fail loud, never silent)"
assert_eq "$(parse_esc "<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 escalate_state=sent escalate_ts=$((NOW + 86400)) page_ok_ts=$((NOW + 86400)) restarts= -->")" \
  "0|sent|0" "parse: BOTH future stamps are clamped to 0 — a value that cannot be true never mutes the pager"
assert_eq "$(parse_esc "<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 escalate_state=suppress escalate_ts=$((NOW - 600)) restarts= -->")" \
  "0||0" "parse: an unrecognised escalate_state is whitelisted AWAY (never an instruction)"
assert_eq "$(parse_esc "<!-- watchdog-state kind=down first_failure_ts=invalid down_runs=20 restarts= -->")" "0||0" "parse: a body with no escalation fields at all ⇒ all zero (legacy bodies)"

# ── unit: the state block's field list has a SINGLE declaration ────────────
BLOCK="$(block_unit)"
FIELDS="$(fields_unit)"
assert_not_empty "$FIELDS" "parity: STATE_FIELDS is declared (a derived empty value would make every assertion vacuous)"
for f in $FIELDS; do
  assert_contains "$BLOCK" "$f=" "parity: state_block renders the declared field '$f'"
done
for tok in $(printf '%s' "$BLOCK" | tr ' ' '\n' | sed -n 's/^\([a-z_]*\)=.*/\1/p'); do
  case " $FIELDS " in
    *" $tok "*) ok "parity: rendered field '$tok' is declared in STATE_FIELDS" ;;
    *) bad "parity: state_block renders '$tok', which STATE_FIELDS does NOT declare (the field list has drifted)" ;;
  esac
done
# ORDER: `restarts` captures the remaining [^>]* tail, so it MUST be last — a
# reorder would make it swallow the following fields, and the strict ledger
# parser would then fail closed forever.
assert_eq "$(printf '%s' "$BLOCK" | tr ' ' '\n' | sed -n 's/^\([a-z_]*\)=.*/\1/p' | tail -1)" "restarts" \
  "parity: 'restarts' is the LAST rendered field (its parser captures the [^>]* tail)"
# The DOCUMENTED declaration must not be the stale kind that already drifted.
RUNBOOK="$SCRIPT_DIR/../../docs/infra-runbook.md"
RUNBOOK_BLOCK="$(grep -m1 'watchdog-state kind=' "$RUNBOOK" || true)"
assert_not_empty "$RUNBOOK_BLOCK" "doc parity: the runbook has a state-block line to check"
for f in $FIELDS; do
  assert_contains "$RUNBOOK_BLOCK" "$f=" "doc parity: the runbook's state-block line names '$f' (stale prose is how the last drift happened)"
done

# ── e2e(a): a single failing run must NOT produce a sustained page ──────────
reset_case
export STUB_PROBE_CODES="404"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_contains "$OUT" "escalation outcome: wait_sustained" "e2e(a): one failing tick → wait_sustained (the leg is evaluated and says so)"
assert_eq "$(count_calls 'CURL telegram')" "1" "e2e(a): only the ONE transition page fires (the sustained leg does not)"

# ── e2e(b): 30 min + 3 observed runs ⇒ exactly ONE sustained page ──────────
reset_case
seed_issue down "$((NOW - 1800))" 3 0 ""
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$RC" "1" "e2e(b): a sustained incident still fails the run"
assert_contains "$OUT" "escalation outcome: page" "e2e(b): the leg decides to page"
assert_eq "$(count_calls 'CURL telegram')" "1" "e2e(b): EXACTLY ONE page for a sustained incident"
assert_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "HUMAN NEEDED" "e2e(b): the page says a human is needed"
assert_contains "$(patched_body)" "escalate_state=sent" "e2e(b): the delivery is recorded durably as CONFIRMED"
# F1: the ATTEMPT marker must ride the FIRST write (write-then-act). Mutating
# the pre-send `pending` to `sent` left every other escalation assertion green,
# so a crashed run would read as delivered — pin the first PATCH directly.
assert_not_empty "$(patched_body_first)" "e2e(b): a body PATCH was recorded (a derived empty body would vacate the next two checks)"
assert_contains "$(patched_body_first)" "escalate_state=pending" "e2e(b): the FIRST body PATCH carries the attempt marker (durable BEFORE the send)"
assert_not_contains "$(patched_body_first)" "escalate_state=sent" "e2e(b): …and the first write does NOT already claim delivery"
assert_contains "$(patched_body)" "page_ok_ts=$NOW" "e2e(b): the confirmed-page stamp is persisted"
assert_contains "$(patched_body)" "### Escalation" "e2e(b): the body carries an Escalation section for the operator"
assert_contains "$(patched_body)" "A human was paged" "e2e(b): the section reports that a human WAS reached"

# ── e2e(b2): a stale-clock reset must not zero the ESCALATION window (G2) ───
# The failing runs here are >STALE_RESET_MINUTES (45) apart and the incident is
# 3 h old. Before the created_at anchor the stale guard set
# first_failure_ts=now and decide_escalation returned wait_sustained — so a
# sustained incident observed on a >45-min cadence NEVER reached a human, the
# exact #3887 failure. The restart clock still resets (that behaviour is
# unchanged); the pager does not.
reset_case
seed_issue down "$((NOW - 10800))" 20 0 "" 42 "$((NOW - 5400))"
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$RC" "1" "e2e(b2): the sustained incident still fails the run"
assert_contains "$OUT" "stale incident" "e2e(b2): …the restart clock DID stale-reset (the reset still happens)"
assert_contains "$OUT" "escalation outcome: page" "e2e(b2): …but the escalation leg still decides to PAGE (the window is not zeroed)"
assert_eq "$(count_calls 'CURL telegram')" "1" "e2e(b2): EXACTLY ONE page for the >45-min-cadence incident"
assert_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "HUMAN NEEDED" "e2e(b2): a human is paged"
# The page reports the ANCHOR's age (the incident is 3 h old), not the reset
# first_failure_ts (~0 min) — a page that said "for ~0 min" would contradict the
# fix that let it fire.
assert_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "for ~180 min" "e2e(b2): the page reports the incident age (180 min), not the reset clock"
assert_not_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "for ~0 min" "e2e(b2): …never a contradictory '~0 min' page"

# ── e2e(b3): a body-forged FUTURE first_failure_ts cannot mute the pager (G3) ─
# parse_state future-clamps the forged stamp to `now`, and the escalation anchor
# is the incident's real server-side created_at (3 h ago), so the leg pages
# instead of sitting on `wait_sustained`.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW + 100000)) down_runs=20 last_down_ts=$((NOW + 100000)) last_comment_ts=0 cap_notified_ts=0 restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_ISSUE_CREATED_AT="epoch:$((NOW - 10800))"
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$RC" "1" "e2e(b3): a forged future state clock → a normal failing run"
assert_not_contains "$(patched_body)" "down for -" "e2e(b3): no negative duration is published"
assert_contains "$OUT" "escalation outcome: page" "e2e(b3): a forged FUTURE first_failure_ts cannot mute the pager"
assert_eq "$(count_calls 'CURL telegram')" "1" "e2e(b3): …the escalation page still goes out"
assert_eq "$(count_calls 'FLYCTL')" "0" "e2e(b3): …and the restart leg still declines (the forged clock cannot restart)"

# ── e2e(b4): an UNUSABLE created_at carries NO age into the page ────────────
# main() sets STATE_ESCALATE_ANCHOR_TS=0 when created_at is unusable (the "no
# unforgeable start" sentinel). The sentinel authorises the RUN leg but is not a
# clock, so the page must not publish the age it implies — `now - 0` is
# "~28 million min" and `fmt_iso 0` is 1970. Before the clamp the page did
# exactly that; this pins the untrustworthy-anchor treatment.
reset_case
seed_issue down "$((NOW - 86400))" 20 0 "" 42
export STUB_ISSUE_CREATED_AT="not-a-timestamp"
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_contains "$OUT" "escalation outcome: page" "e2e(b4): an unusable created_at still reaches the pager (the run leg authorises it)"
assert_eq "$(count_calls 'CURL telegram')" "1" "e2e(b4): exactly one page goes out"
# The 0 sentinel has no age: the clamp reports ~0 min. Without it the page
# publishes `(now - 0)/60` — a fixed, absurd epoch-0 age (NOW/60 here). Pin the
# exact number so the assertion cannot pass vacuously on a different failure.
assert_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "for ~0 min" "e2e(b4): an untrustworthy anchor reports ~0 min, not an epoch-derived age"
assert_not_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "for ~$((NOW / 60)) min" "e2e(b4): …never the (now minus 0) epoch age the 0 sentinel implies"

# ── e2e(c): no repeat inside the reminder window ───────────────────────────
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 escalate_state=sent escalate_ts=$((NOW - 600)) page_ok_ts=0 restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_contains "$OUT" "escalation outcome: wait_reminder" "e2e(c): inside the window the leg says wait_reminder"
assert_eq "$(count_calls 'CURL telegram')" "0" "e2e(c): NO page inside the reminder window"

# ── e2e(n): a STALE confirmed escalation ⇒ a REMINDER ──────────────────────
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 25000)) down_runs=40 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 escalate_state=sent escalate_ts=$((NOW - 7200)) page_ok_ts=$((NOW - 7200)) restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_contains "$OUT" "escalation outcome: remind" "e2e(n): 2 h after the last confirmed page the leg REMINDS"
assert_eq "$(count_calls 'CURL telegram')" "1" "e2e(n): the reminder is exactly ONE page (11 h of incident is not 11 h of pages)"
assert_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "STILL RUNNING" "e2e(n): the reminder says STILL RUNNING, not a fresh escalation"

# ── e2e(i): sustained DEGRADED / UNEXPECTED pages — the #3887 incident's class
reset_case
seed_issue degraded "$((NOW - 1800))" 3 0 ""
export STUB_PROBE_CODES="404"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$RC" "1" "e2e(i): a sustained UNEXPECTED run still fails (never green while answered-wrongly)"
assert_contains "$OUT" "escalation outcome: page" "e2e(i): the answered-wrongly class DOES reach the escalation leg"
assert_eq "$(count_calls 'CURL telegram')" "1" "e2e(i): sustained DEGRADED → paged (the class that had NO coverage)"
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "e2e(i): …and it still never restarts (a restart cannot fix answered-wrongly)"

# ── e2e(d): channel UNCONFIGURED ⇒ no delivery stamp, durable failure, loud ─
reset_case
seed_issue down "$((NOW - 1800))" 3 0 ""
export STUB_PROBE_CODES="000"
unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID ESCALATION_CHAT_ID || true
run_watchdog
assert_eq "$RC" "1" "e2e(d): an unconfigured channel still fails the run"
assert_contains "$OUT" "escalation outcome: page" "e2e(d): the leg DID decide to page (the failure is the channel, not the decision)"
assert_contains "$OUT" "escalation REQUIRED and NOT DELIVERED" "e2e(d): the run names the undelivered escalation (never 'all clear')"
assert_not_contains "$(patched_body)" "escalate_state=sent" "e2e(d): an undelivered page is NEVER recorded as sent"
assert_contains "$(patched_body)" "escalate_state=failed" "e2e(d): the failure is DURABLE in the body"
assert_contains "$(patched_body)" "NOT DELIVERED" "e2e(d): the body tells the operator a human was NOT reached"
assert_eq "$(count_calls 'CURL telegram')" "0" "e2e(d): with no credentials there is no call to make"
# Pin the ANNOTATION LEVEL, not just the wording: `fail` (::error::) is what makes
# a broken pager a FAILED RUN. A `warn` here would leave the message in the log
# while the run's only failure was the verdict — i.e. fail-SILENT, which is the
# one outcome requirement 5 forbids.
assert_contains "$OUT" "ERROR: ⛔ sustained-incident escalation REQUIRED and NOT DELIVERED" "e2e(d): the undelivered page is a run FAILURE, not a warning"

# ── e2e(e): HTTP 200 with {"ok":false} is NOT delivery ─────────────────────
reset_case
seed_issue down "$((NOW - 1800))" 3 0 ""
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
export STUB_TELEGRAM_OK=0
export STUB_TELEGRAM_DESC="Bad Request: chat not found"
run_watchdog
assert_contains "$OUT" "REJECTED by the API" "e2e(e): a 2xx with ok:false is surfaced, not swallowed"
assert_contains "$OUT" "escalation REQUIRED and NOT DELIVERED" "e2e(e): …and it is treated as an UNDELIVERED escalation"
assert_contains "$(patched_body)" "escalate_state=failed" "e2e(e): an API-rejected page is never stamped as delivered"
assert_not_contains "$OUT" "tg-token" "e2e(e): the bot token never appears in the PUBLIC run log"

# ── e2e(e2): a TRANSPORT failure — curl echoes the token-bearing URL ───────
# (`curl: (6) Could not resolve host: https://api.telegram.org/bot<TOKEN>/…`).
# This is the only path that puts the token into the log's TEXT, so it is what
# makes the redaction assertion non-vacuous.
reset_case
seed_issue down "$((NOW - 1800))" 3 0 ""
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
export STUB_TELEGRAM_FAIL=1
run_watchdog
assert_contains "$OUT" "escalation REQUIRED and NOT DELIVERED" "e2e(e2): a transport failure is an undelivered escalation"
assert_contains "$OUT" "ERROR: ⛔ sustained-incident escalation REQUIRED" "e2e(e2): …reported at ERROR level (a broken pager must fail the run)"
assert_not_contains "$OUT" "tg-token" "e2e(e2): curl's echoed URL is scrubbed — the PUBLIC log carries no bot token"
assert_contains "$OUT" "<redacted>" "e2e(e2): …the redaction placeholder is there instead"
assert_not_contains "$(patched_body)" "escalate_state=sent" "e2e(e2): a transport failure is never recorded as sent"
assert_contains "$(patched_body)" "escalate_state=failed" "e2e(e2): …it is recorded as failed for the next run"

# ── e2e(f): a FUTURE escalate_ts is clamped ⇒ the leg still pages ──────────
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 1800)) down_runs=3 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 escalate_state=sent escalate_ts=$((NOW + 86400)) page_ok_ts=$((NOW + 86400)) restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$(count_calls 'CURL telegram')" "1" "e2e(f): a future stamp cannot mute the pager — the escalation still fires"

# ── e2e(g): a page in the SAME run ⇒ no second page ────────────────────────
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=$((NOW + 86400)) restarts=$((NOW - 1500)),$((NOW - 1300)) -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_contains "$OUT" "escalation outcome: suppressed" "e2e(g): a confirmed page earlier in the SAME run suppresses the leg"
assert_contains "$(patched_body)" "page_ok_ts=$NOW" "e2e(g): a cap page's CONFIRMED delivery stamps page_ok_ts — the cross-mechanism bound's DURABLE memory (without this the bound dies with the run)"
assert_eq "$(count_calls 'CURL telegram')" "1" "e2e(g): exactly ONE page for the incident in this run (the cap escalation's)"
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "e2e(g): …and the cap still blocks the restart"

# ── e2e(h): a confirmed page 10 min AGO ⇒ no second page ───────────────────
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 page_ok_ts=$((NOW - 600)) restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_contains "$OUT" "escalation outcome: wait_page_quiet" "e2e(h): a confirmed page 10 min ago quiets the leg"
assert_eq "$(count_calls 'CURL telegram')" "0" "e2e(h): NO second page across mechanisms inside the window"

# ── e2e(j): ESCALATE_ENABLED=0 is an operator KILL SWITCH ──────────────────
reset_case
seed_issue down "$((NOW - 1800))" 3 0 ""
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
export ESCALATE_ENABLED=0
run_watchdog
assert_contains "$OUT" "escalation outcome: off" "e2e(j): ESCALATE_ENABLED=0 → off"
assert_eq "$(count_calls 'CURL telegram')" "0" "e2e(j): a kill switch sends nothing"
assert_contains "$(patched_body)" "DISABLED" "e2e(j): …and the body says the escalation is deliberately disabled (not 'clear')"

# ── e2e(k): no notification without a durable record ───────────────────────
reset_case
seed_issue down "$((NOW - 1800))" 3 0 ""
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
export STUB_PATCH_FAIL=1
run_watchdog
assert_eq "$RC" "1" "e2e(k): a failed state write fails the run"
assert_eq "$(count_calls 'CURL telegram')" "0" "e2e(k): …and NO escalation page is sent (the durable record gates the side effect)"

# ── e2e(l): a sustained DRILL pages but must never restart ─────────────────
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 1800)) down_runs=3 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='DRILL%20DOWN'
export STUB_SEARCH_JSON="$(search_json 900 "$DRILL_DOWN_TITLE_FIXTURE")"
export PROBE_URL="https://staging.example.test/v1/organizations"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$(count_calls 'FLYCTL machine restart')" "0" "e2e(l): a sustained DRILL never restarts production"
assert_contains "$OUT" "escalation outcome: page" "e2e(l): …but it DOES exercise the escalation leg (this is how the leg is drilled)"

# ── e2e(m): the public body/log never carry the recipient id ───────────────
reset_case
seed_issue down "$((NOW - 1800))" 3 0 ""
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="99887766"
export ESCALATION_CHAT_ID="99887766"
run_watchdog
assert_not_contains "$(patched_body)" "99887766" "e2e(m): the public incident body never publishes the chat id"
assert_not_contains "$OUT" "99887766" "e2e(m): the run log never publishes the chat id"
assert_contains "$OUT" "recipient=telegram-default" "e2e(m): the log names the recipient KIND instead of the id (both ids identical here)"
assert_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "chat_id=99887766" "e2e(m): …while the request IS addressed to the configured chat"

# ── e2e(o): ESCALATION_CHAT_ID is a separate, working recipient ────────────
reset_case
seed_issue down "$((NOW - 1800))" 3 0 ""
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="11111"
export ESCALATION_CHAT_ID="22222"
run_watchdog
assert_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "chat_id=22222" "e2e(o): the sustained page goes to ESCALATION_CHAT_ID"
assert_not_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "chat_id=11111" "e2e(o): …and NOT to the transition chat"
assert_contains "$OUT" "recipient=telegram-override" "e2e(o): the log records that an override recipient is in use (by KIND, never the id)"
assert_not_contains "$OUT" "22222" "e2e(o): …and never the override id itself"

# ════════════════════════════════════════════════════════════════════════════
# #3887 round 2 — findings from the code review of PR #4591. Each case pins a
# property a FRESH reviewer PROVED was unpinned, by mutating the guard and
# watching the whole escalation section stay green.
# ════════════════════════════════════════════════════════════════════════════

# A SOURCE incident's body, as it looks after its own sustained page.
PAGED_SRC_BODY="<!-- watchdog-state kind=down first_failure_ts=$((NOW - 9999)) down_runs=9 last_down_ts=$((NOW - 9000)) last_comment_ts=0 cap_notified_ts=0 ledger_state= ledger_src= escalate_state=sent escalate_ts=$((NOW - 600)) page_ok_ts=$((NOW - 600)) restarts=$((NOW - 1500)) -->"

# Unit-call recent_restart_ledger() against a SOURCE incident that was paged,
# while the CALLER holds a fresh incident's (empty) escalation state. Drives the
# real helper through the harness's stubbed gh.
bleed_unit() { # -> "state|ts|page_ok" the CALLER still holds afterwards
  WATCHDOG_LIB_ONLY=1 bash -c '
    source "$0"
    STATE_FIRST_FAILURE_TS=111; STATE_DOWN_RUNS=1; STATE_LAST_DOWN_TS=111
    STATE_LAST_COMMENT_TS=0; STATE_CAP_NOTIFIED_TS=0
    STATE_LEDGER_STATE=""; STATE_LEDGER_SRC=""; STATE_RESTARTS=""
    STATE_ESCALATE_STATE=""; STATE_ESCALATE_TS=0; STATE_PAGE_OK_TS=0
    recent_restart_ledger "down" "$WATCHDOG_NOW_EPOCH" "$1" >/dev/null 2>&1 || true
    printf "%s|%s|%s" "$STATE_ESCALATE_STATE" "$STATE_ESCALATE_TS" "$STATE_PAGE_OK_TS"
  ' "$WATCHDOG" "$DOWN_TITLE_FIXTURE"
}

# Unit-call reseed_ledger_from_source() against the SAME paged SOURCE incident,
# while the CALLER holds a DELIBERATELY DISTINCT escalation state. This helper
# runs mid-run on a repeat DOWN run (immediately before the final body write), so
# a missing snapshot/restore would overwrite the CURRENT incident's triple. The
# return code is part of the readout: rc=0 proves the function actually READ the
# source (so the assertion cannot pass by bailing out early).
reseed_bleed_unit() { # <src-issue> -> "rc|state|ts|page_ok" the CALLER still holds
  WATCHDOG_NOW_EPOCH="$NOW" WATCHDOG_LIB_ONLY=1 bash -c '
    source "$0"
    STATE_FIRST_FAILURE_TS=111; STATE_DOWN_RUNS=1; STATE_LAST_DOWN_TS=111
    STATE_LAST_COMMENT_TS=0; STATE_CAP_NOTIFIED_TS=0
    STATE_LEDGER_STATE="unreadable"; STATE_LEDGER_SRC="$1"
    STATE_RESTARTS=""; STATE_RESTARTS_INVALID="0"; STATE_RESTARTS_RAW=""
    STATE_ESCALATE_STATE="failed"; STATE_ESCALATE_TS=4444; STATE_PAGE_OK_TS=0
    if reseed_ledger_from_source "$1" >/dev/null 2>&1; then rc=0; else rc=$?; fi
    printf "%s|%s|%s|%s" "$rc" "$STATE_ESCALATE_STATE" "$STATE_ESCALATE_TS" "$STATE_PAGE_OK_TS"
  ' "$WATCHDOG" "$1"
}

# ── unit: a SOURCE incident's page must NOT bleed into the CURRENT incident ──
# Both helpers parse ANOTHER incident's body while the caller's state is live.
# Omitting the escalation triple from their snapshot/restore let the source's
# CONFIRMED-page stamps clobber the caller's — and decide_escalation then read
# them as "this incident already paged a human", MUTING the new incident's page
# (fail-OPEN). Reproduced at head 67fac3305 before the fix.
reset_case
printf '%s' "{\"body\":\"$PAGED_SRC_BODY\"}" > "$STUB_TMP/issue.777.json"
export STUB_LEDGER_SEARCH_JSON="$(search_json 777 "$DOWN_TITLE_FIXTURE")"
assert_eq "$(bleed_unit)" "|0|0" "bleed: recent_restart_ledger does NOT adopt the SOURCE incident's escalation state"
unset STUB_LEDGER_SEARCH_JSON
# …and the parse itself IS what the snapshot protects against, so the assertion
# above cannot be passing for the wrong reason (e.g. a parse that read nothing).
assert_eq "$(parse_esc "$PAGED_SRC_BODY")" "$((NOW - 600))|sent|$((NOW - 600))" \
  "bleed: parse_state DOES read those fields (the snapshot is load-bearing, not vacuous)"
# F3: the SAME rule in reseed_ledger_from_source(). Removing ONLY its escalation
# restore line left the suite green; the caller here holds a distinct triple, so
# a missing restore is visible (it would come back 'sent|NOW-600|NOW-600').
assert_eq "$(reseed_bleed_unit 777)" "0|failed|4444|0" \
  "bleed: reseed_ledger_from_source does NOT adopt the SOURCE incident's escalation state (rc=0 proves the source WAS read)"

# ── e2e(p): a NEW incident is never born stamped as already-paged ───────────
# The end-to-end consequence: a new incident carried from a paged source must
# not inherit its stamps, or its leg answers wait_page_quiet/remind while this
# incident has never paged anyone.
reset_case
printf '%s' "{\"body\":\"$PAGED_SRC_BODY\"}" > "$STUB_TMP/issue.777.json"
export STUB_LEDGER_SEARCH_JSON="$(search_json 777 "$DOWN_TITLE_FIXTURE")"
export STUB_SEARCH_JSON="$(search_json 900 "$DOWN_TITLE_FIXTURE")"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_contains "$(patched_body)" "escalate_state=" "e2e(p): the new incident's body carries an escalation-state field"
assert_not_contains "$(patched_body)" "escalate_state=sent" "e2e(p): a NEW incident is never born stamped already-paged by a PREVIOUS incident"
assert_not_contains "$(patched_body)" "page_ok_ts=$((NOW - 600))" "e2e(p): …and never inherits the previous incident's confirmed-page stamp"
unset STUB_LEDGER_SEARCH_JSON

# ── e2e(q): an UNDELIVERED escalation must not stamp page_ok_ts ─────────────
# page_ok_ts is the cross-run retry gate. Stamping it on an ATTEMPT rather than
# a confirmed delivery turns the advertised "retried next run" into "not retried
# for 60 min" while every other assertion stays green (proved by a reviewer).
reset_case
seed_issue down "$((NOW - 1800))" 3 0 ""
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
export STUB_TELEGRAM_FAIL=1
run_watchdog
assert_contains "$(patched_body)" "escalate_state=failed" "e2e(q): the undelivered attempt is recorded as failed"
assert_contains "$(patched_body)" "page_ok_ts=0" "e2e(q): an UNDELIVERED page stamps NO confirmed-page stamp (it must not gate the retry)"
assert_not_contains "$(patched_body)" "page_ok_ts=$NOW" "e2e(q): …specifically not 'now'"

# ── e2e(r): run 2 of an undelivered escalation actually RETRIES ─────────────
# The consequence, asserted directly: reload the body run 1 PUBLISHED and confirm
# the leg decides `page`, not `wait_page_quiet`.
FAILED_BODY="$(patched_body)"
reset_case
jq -n --arg b "$FAILED_BODY" '{body:$b}' > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_contains "$OUT" "escalation outcome: page" "e2e(r): a failed page is RETRIED on the next run, not throttled away"
assert_eq "$(count_calls 'CURL telegram')" "1" "e2e(r): …and the retry actually calls the channel"

# ── e2e(v): a body left at `pending` RETRIES (the crash-between-PATCHes case) ─
# The write then act contract is only SAFE because the READER treats the attempt
# marker as "outcome unknown ⇒ retry". This drives that claim end-to-end: a body
# whose last run died after writing `pending` (so escalate_ts is non-zero) must
# decide `page`, never `wait_reminder` (`escalate_ts` is trusted only while
# escalate_state=sent).
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=0 escalate_state=pending escalate_ts=$((NOW - 600)) page_ok_ts=0 restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_contains "$OUT" "escalation outcome: page" "e2e(v): a body left at 'pending' RETRIES the page (the marker can never mute the leg)"
assert_eq "$(count_calls 'CURL telegram')" "1" "e2e(v): …and the retry actually calls the channel"

# ── e2e(s): a FAILED human page suppresses nothing ──────────────────────────
# HUMAN_PAGED_THIS_RUN must be 1 only on CONFIRMED delivery. Setting it
# unconditionally mutes the one channel that could still reach a human — the
# fail-open this leg exists to close (proved by a reviewer).
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=$((NOW + 86400)) restarts=$((NOW - 1500)),$((NOW - 1300)) -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
export STUB_TELEGRAM_FAIL=1
run_watchdog
assert_contains "$OUT" "escalation outcome: page" "e2e(s): the cap page FAILED → the sustained leg is NOT suppressed by it"
assert_eq "$(count_calls 'CURL telegram')" "2" "e2e(s): both the cap page AND the escalation page were attempted"
assert_not_contains "$(patched_body)" "page_ok_ts=$NOW" "e2e(s): a failed page stamps no confirmed-page stamp"

# ── e2e(t): an operator override does NOT move the pre-existing pages ───────
# ESCALATION_CHAT_ID is the SUSTAINED leg's recipient. Routing page_human() to it
# would silently move the restart / cap / INCONCLUSIVE pages off the ops chat
# whenever an override is configured — a change to pre-existing paging that no
# doc stated.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 7200)) down_runs=20 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=$((NOW + 86400)) restarts=$((NOW - 1500)),$((NOW - 1300)) -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="11111"
export ESCALATION_CHAT_ID="22222"
run_watchdog
assert_eq "$(count_calls 'CURL telegram')" "1" "e2e(t): the confirmed cap page is the run's only page (it suppresses the duplicate leg)"
assert_contains "$(grep -m1 'CURL telegram' "$STUB_TMP/calls.log")" "chat_id=11111" "e2e(t): the pre-existing cap page still goes to TELEGRAM_CHAT_ID"
assert_not_contains "$(grep -m1 'CURL telegram' "$STUB_TMP/calls.log")" "chat_id=22222" "e2e(t): …NOT to the sustained-leg override"

# ── e2e(u): the corrupt-ledger refusal sends NO page (and says so) ──────────
# That path deliberately refuses to rewrite the body it cannot trust, so a send
# could not be stamped and would repeat on every run. It must not page at all,
# and the refusal must NAME that gap rather than leaving it silent.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 2400)) down_runs=4 last_down_ts=$((NOW - 3000)) last_comment_ts=0 cap_notified_ts=0 restarts=abc -->\"}" > "$STUB_TMP/issue.json"
export STUB_LEDGER_SEARCH_JSON="$(search_json 777 "$DOWN_TITLE_FIXTURE")"
export STUB_PROBE_CODES="000"
export FLY_API_TOKEN="fly-token"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_eq "$RC" "1" "e2e(u): a corrupt ledger still fails the run (never silent)"
# The ONE page here is the run-1 TRANSITION alert (🚨 DOWN) — the pre-existing
# signal, which must keep firing. What must NOT happen is an ESCALATION page:
# this path deliberately refuses to rewrite the body it cannot trust, so a send
# could not be stamped and would repeat on every run.
assert_not_contains "$(cat "$STUB_TMP/calls.log")" "HUMAN NEEDED" "e2e(u): the corrupt-ledger path sends NO escalation page (no durable stamp ⇒ it would repeat every run)"
assert_not_contains "$OUT" "escalation outcome:" "e2e(u): the escalation leg is never even reached (the run exits first)"
assert_eq "$(count_calls 'CURL telegram')" "1" "e2e(u): …while the pre-existing transition alert still goes out"
assert_contains "$OUT" "no escalation page is sent" "e2e(u): the refusal NAMES that no human page is sent (documented, not silent)"

# ── e2e(w): a sustained INCONCLUSIVE run must not be paged as a confirmed DOWN ─
# On the no-egress path the incident's own heal note says the watchdog cannot
# distinguish an app outage from its own network. A page claiming "<host> has
# been DOWN" would state a diagnosis this run explicitly refuses to make. The
# INCONCLUSIVE transition page is throttled here (cap_notified_ts is in-window),
# so the ESCALATION leg's page is the one the channel actually receives.
reset_case
printf '%s' "{\"body\":\"<!-- watchdog-state kind=down first_failure_ts=$((NOW - 2400)) down_runs=5 last_down_ts=$((NOW - 60)) last_comment_ts=0 cap_notified_ts=$((NOW - 300)) restarts= -->\"}" > "$STUB_TMP/issue.json"
export STUB_SEARCH_MARKER='PROD%20DOWN'
export STUB_SEARCH_JSON="$(search_json 42)"
export STUB_PROBE_CODES="000"
export STUB_CONTROL_CODES="000"
export FLY_API_TOKEN="fly-token"
export TELEGRAM_BOT_TOKEN="tg-token"
export TELEGRAM_CHAT_ID="12345"
run_watchdog
assert_contains "$OUT" "escalation outcome: page" "e2e(w): a sustained INCONCLUSIVE incident reaches the sustained page"
assert_eq "$(count_calls 'CURL telegram')" "1" "e2e(w): exactly one page (the INCONCLUSIVE transition page was throttled)"
assert_not_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "has been DOWN" "e2e(w): …and it does NOT assert a confirmed DOWN diagnosis the probe cannot support"
assert_contains "$(grep 'CURL telegram' "$STUB_TMP/calls.log")" "INCONCLUSIVE" "e2e(w): …it names the INCONCLUSIVE case instead"

# ── unit: the `pending` note branch is reachable and renders ────────────────
# The operator-facing prose for the crash-between-the-two-PATCHes case (the
# at-least-once retry story). Deleting it left the suite green.
PENDING_NOTE="$(WATCHDOG_LIB_ONLY=1 bash -c '
  source "$0"
  STATE_FIRST_FAILURE_TS=111; STATE_DOWN_RUNS=3
  STATE_ESCALATE_STATE="pending"; STATE_ESCALATE_TS=222; STATE_PAGE_OK_TS=0
  escalation_note
' "$WATCHDOG")"
assert_contains "$PENDING_NOTE" "being attempted" "note: escalate_state=pending renders the 'being attempted' text (the crash-between-PATCHes story)"
assert_not_contains "$PENDING_NOTE" "A human was paged" "note: …and does NOT claim a human was reached"

# ── unit: the gh stub's argument loop ALWAYS makes progress ────────────────
# `--method` / `--jq` as the LAST argument used to make `shift 2` fail, and a
# failed shift shifts NOTHING → the loop re-read the same "$1" forever. The
# spin was unreachable today (every caller passes a value) but one
# argument-ordering change from live, and it would hang whatever run touched
# it. `timeout` is not available here, so the WALL-CLOCK bound is a poll of the
# stub PID plus a direct SIGKILL: the stub is launched directly (so the PID we
# kill IS the spinner, not a wrapper), and the assertion fails on the resulting
# 137 instead of hanging the suite.
# ⛔ Polled, NOT a `( sleep …; kill … ) &` killer subshell: killing that subshell
# ORPHANS its `sleep` (reparented to PID 1), which then runs for the full bound —
# three linger per suite run, and the comment claiming the reap prevented it was
# false. Here the `sleep` is a foreground child of the polling loop, so nothing
# can outlive the case.
# Deliberately NOT called in $( ): a function invoked in a command substitution
# runs in a SUBSHELL, so its GH_STUB_RC would never reach the caller (and `set
# -u` would then abort the whole harness on the unbound read).
gh_stub_bounded() { # <bound-seconds> <args...>; sets GH_STUB_RC, output → $STUB_TMP/ghstub.out
  local bound="$1"; shift
  local out_f="$STUB_TMP/ghstub.out" pid i
  rm -f "$out_f"
  "$BIN/gh" "$@" </dev/null >"$out_f" 2>&1 &
  pid=$!
  i=0
  while kill -0 "$pid" 2>/dev/null && [ "$i" -lt "$bound" ]; do sleep 1; i=$((i+1)); done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
  wait "$pid"; GH_STUB_RC=$?
}

gh_stub_bounded 10 api /repos/o/r/issues --method POST
assert_eq "$GH_STUB_RC" "0" "stub: a well-formed '--method POST' terminates (never spins on the arg loop)"
assert_contains "$(cat "$STUB_TMP/ghstub.out")" '{"number":900}' "stub: …and still PARSES the method (the create branch is reached)"
assert_eq "$(grep -c '^GH POST /repos/o/r/issues$' "$STUB_TMP/calls.log")" "1" \
  "stub: …with method=POST actually consumed (a failed shift would have dropped it)"
gh_stub_bounded 10 api /repos/o/r/issues --method
assert_eq "$GH_STUB_RC" "0" "stub: a BARE trailing --method (no value) terminates instead of spinning"
gh_stub_bounded 10 api /repos/o/r/issues --jq
assert_eq "$GH_STUB_RC" "0" "stub: a BARE trailing --jq (no value) terminates instead of spinning"

echo
if [ "$FAIL" -eq 0 ]; then
  echo "availability-watchdog.test.sh: $PASS passed, 0 failed ✅"
  exit 0
fi
echo "availability-watchdog.test.sh: $PASS passed, $FAIL FAILED ❌"
exit 1
