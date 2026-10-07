#!/usr/bin/env bash
# telegram-send.test.sh — self-check for .github/scripts/telegram-send.sh
# (#4574 item 1, delivered by #2240).
#
# Run: bash .github/scripts/telegram-send.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: stubs
# `curl` and `jq`-less real jq on PATH. No network.
#
# Coverage:
#   1. transport OK + `ok:true` → DELIVERED (return 0)
#   2. transport OK (HTTP 200) + `ok:false` → NOT delivered (return 1) — the
#      fail-open the whole contract exists to close: a misconfigured chat id
#      answers 200 with `{"ok":false,"description":"chat not found"}`
#   3. HTTP 4xx → NOT delivered, and Telegram's OWN description reaches the log
#      (not just curl's opaque `(22)`)
#   4. a transport error → NOT delivered, and the token is REDACTED out of the
#      echoed URL before the log line is written (the log is PUBLIC)
#   5. a missing token / a missing chat → NOT delivered, loudly, with NO call
#   6. the delivery verdict is the API's `ok`, not the HTTP status (pinned
#      against a 200 that says `ok:false`)
#   7. caller hooks are honoured: TG_WARN_FN is the logger, TG_SCRUB_FN is the
#      redactor (this is how availability-watchdog.sh keeps its own semantics)
#   8. the default redactor still removes the token when no hook is given
#   9. TG_BODY_FILE is honoured
#  10. sourcing the file executes NOTHING (functions only — the seam every
#      caller relies on) and does not change the caller's shell options

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$SCRIPT_DIR/telegram-send.sh"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; }
assert_eq() { if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 (got '$1', want '$2')"; fi; }
assert_contains() {
  case "$1" in *"$2"*) ok "$3" ;; *) bad "$3 (missing '$2')" ;; esac
}
assert_not_contains() {
  case "$1" in *"$2"*) bad "$3 (unexpectedly found '$2')" ;; *) ok "$3" ;; esac
}

FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"' EXIT
BIN="$FIX/bin"
mkdir -p "$BIN"
STUB_TMP="$FIX/stub"
mkdir -p "$STUB_TMP"
export STUB_TMP

# ── stub: curl ──────────────────────────────────────────────────────────────
cat > "$BIN/curl" <<'CURL_EOF'
#!/usr/bin/env bash
# Handles the shape tg_send uses: curl -sS --fail-with-body --max-time 15
#   -o <file> <url> --data-urlencode k=v --data-urlencode k=v
# STUB_TG_HTTP=<code>  → answer that HTTP status (4xx path)
# STUB_TG_TRANSPORT_FAIL=1 → a connect failure (curl exit 7)
# STUB_TG_BODY=<json>  → what lands in the -o body file
echo "CURL $*" >> "$STUB_TMP/calls.log"
out=""; url=""
args=("$@")
for i in "${!args[@]}"; do
  case "${args[$i]}" in
    -o) out="${args[$((i + 1))]}" ;;
    https://*) url="${args[$i]}" ;;
  esac
done
# curl echoes the failing URL — which carries the bot token — in its error text.
if [ "${STUB_TG_TRANSPORT_FAIL:-0}" = "1" ]; then
  echo "curl: (7) Failed to connect to api.telegram.org port 443 (url: ${url})" >&2
  exit 7
fi
http="${STUB_TG_HTTP:-200}"
if [ "$http" != "200" ]; then
  [ -n "$out" ] && printf '%s' "${STUB_TG_BODY:-{\"ok\":false,\"description\":\"Bad Request\"}}" > "$out"
  echo "curl: (22) The requested URL returned error: ${http} (url: ${url})" >&2
  exit 22
fi
[ -n "$out" ] && printf '%s' "${STUB_TG_BODY:-{\"ok\":true,\"result\":{}}}" > "$out"
exit 0
CURL_EOF
chmod +x "$BIN/curl"
PATH="$BIN:$PATH"
export PATH

[ -f "$LIB" ] || { echo "❌ library not found: $LIB"; exit 1; }

reset_case() {
  : > "$STUB_TMP/calls.log"
  unset STUB_TG_HTTP STUB_TG_BODY STUB_TG_TRANSPORT_FAIL TG_WARN_FN TG_SCRUB_FN TG_BODY_FILE || true
}
count_calls() { grep -c -- "CURL" "$STUB_TMP/calls.log" 2>/dev/null || true; }

# ── case 10 first: sourcing must execute nothing ────────────────────────────
echo "── case 10: sourcing is side-effect free ─────────────────────────────"
reset_case
# shellcheck disable=SC1090
( TELEGRAM_BOT_TOKEN="tg-secret" . "$LIB"
  [ "$(count_calls)" = "0" ] || { echo "SOURCE_CALLED"; exit 1; }
  declare -F tg_send >/dev/null || { echo "NO_TG_SEND"; exit 1; }
  # The library must not arm `-e` in the caller: a sourced file that silently
  # turns on errexit would change a live monitor's failure behaviour.
  case "$-" in *e*) echo "ARMED_ERREXIT"; exit 1 ;; esac ) > "$FIX/src.out" 2>&1
SRC_RC=$?
assert_eq "$SRC_RC" "0" "sourcing defines tg_send, calls nothing, and arms no shell options"
assert_not_contains "$(cat "$FIX/src.out")" "SOURCE_CALLED" "sourcing made no network call"
assert_not_contains "$(cat "$FIX/src.out")" "ARMED_ERREXIT" "sourcing did not arm errexit in the caller"

# A single long-lived shell for the rest: source once, call many.
# shellcheck disable=SC1090
. "$LIB"

echo "── case 1: transport OK + ok:true → DELIVERED ────────────────────────"
reset_case
export TELEGRAM_BOT_TOKEN="tg-secret"
if tg_send "12345" "hello" > "$FIX/out" 2>&1; then RC=0; else RC=$?; fi
assert_eq "$RC" "0" "ok:true is a delivered page"
assert_eq "$(count_calls)" "1" "exactly one API call"
assert_contains "$(cat "$STUB_TMP/calls.log")" "sendMessage" "the send endpoint was used"

echo "── case 2/6: HTTP 200 with ok:false → NOT delivered ─────────────────"
reset_case
export TELEGRAM_BOT_TOKEN="tg-secret"
export STUB_TG_BODY='{"ok":false,"description":"chat not found"}'
if tg_send "nope" "hello" > "$FIX/out" 2>&1; then RC=0; else RC=$?; fi
OUT="$(cat "$FIX/out")"
assert_eq "$RC" "1" "a 200 whose API verdict is ok:false is NOT delivery (#4574 contract)"
assert_contains "$OUT" "REJECTED by the API" "the rejection is named"
assert_contains "$OUT" "chat not found" "the API's own description reaches the log"

echo "── case 3: HTTP 4xx → NOT delivered, description surfaced ───────────"
reset_case
export TELEGRAM_BOT_TOKEN="tg-secret"
export STUB_TG_HTTP=400
export STUB_TG_BODY='{"ok":false,"description":"Bad Request: chat not found"}'
if tg_send "12345" "hello" > "$FIX/out" 2>&1; then RC=0; else RC=$?; fi
OUT="$(cat "$FIX/out")"
assert_eq "$RC" "1" "an HTTP 4xx is NOT delivery"
assert_contains "$OUT" "telegram page failed" "the failure is logged"
assert_contains "$OUT" "chat not found" "the API response body is surfaced"

echo "── case 4/8: a transport error redacts the token from the echoed URL ──"
reset_case
export TELEGRAM_BOT_TOKEN="tg-secret"
export STUB_TG_TRANSPORT_FAIL=1
if tg_send "12345" "hello" > "$FIX/out" 2>&1; then RC=0; else RC=$?; fi
OUT="$(cat "$FIX/out")"
assert_eq "$RC" "1" "a transport failure is NOT delivery"
assert_contains "$OUT" "telegram page failed" "the transport failure is logged"
assert_not_contains "$OUT" "tg-secret" "the bot token never reaches the (public) log"
assert_contains "$(cat "$STUB_TMP/calls.log")" "tg-secret" "the STUB echoed it (so the assertion above is not vacuous)"

echo "── case 5: missing token / missing chat → loud skip, no call ─────────"
reset_case
unset TELEGRAM_BOT_TOKEN || true
if tg_send "12345" "hello" > "$FIX/out" 2>&1; then RC=0; else RC=$?; fi
assert_eq "$RC" "1" "no token → not delivered"
assert_contains "$(cat "$FIX/out")" "telegram send skipped" "the skip is loud"
assert_eq "$(count_calls)" "0" "no token → no API call"
reset_case
export TELEGRAM_BOT_TOKEN="tg-secret"
if tg_send "" "hello" > "$FIX/out" 2>&1; then RC=0; else RC=$?; fi
assert_eq "$RC" "1" "no chat id → not delivered"
assert_contains "$(cat "$FIX/out")" "telegram send skipped" "an unaddressed page is refused loudly"

echo "── case 7: caller hooks are honoured ────────────────────────────────"
reset_case
export TELEGRAM_BOT_TOKEN="tg-secret"
export STUB_TG_TRANSPORT_FAIL=1
hook_log="$FIX/hook.log"
# The watchdog's shape: its own logger + its own (richer) redactor.
tg_log_hook() { printf 'HOOKLOG %s\n' "$*" >> "$hook_log"; }
tg_scrub_hook() { printf 'HOOKSCRUB'; }
TG_WARN_FN=tg_log_hook TG_SCRUB_FN=tg_scrub_hook tg_send "12345" "hi" > "$FIX/out" 2>&1 || true
HOOKED="$(cat "$hook_log" 2>/dev/null || echo '')"
assert_contains "$HOOKED" "HOOKLOG" "TG_WARN_FN is the logger (the caller's own warn() is used)"
# The redactor's output must land in the CALLER'S logger — not on stderr — so
# this is exactly the path availability-watchdog.sh keeps its semantics on.
assert_contains "$HOOKED" "HOOKSCRUB" "TG_SCRUB_FN is the redactor (the caller's own redaction is used)"
assert_not_contains "$(cat "$FIX/out")" "HOOKLOG" "with a logger hook, nothing bypasses it to raw stderr"

echo "── case 9: TG_BODY_FILE is honoured ─────────────────────────────────"
reset_case
export TELEGRAM_BOT_TOKEN="tg-secret"
export TG_BODY_FILE="$FIX/custom-body.json"
tg_send "12345" "hi" >/dev/null 2>&1 || true
if [ -s "$TG_BODY_FILE" ]; then ok "the response body landed in TG_BODY_FILE"; else bad "TG_BODY_FILE was ignored"; fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "✅ telegram-send.test.sh — $PASS assertions passed"
  exit 0
fi
echo "❌ telegram-send.test.sh — $FAIL of $((PASS + FAIL)) assertions failed"
exit 1
