#!/usr/bin/env bash
# ============================================================================
# telegram-send.sh — the ONE shell Telegram sender (#4574 item 1).
#
# WHY THIS FILE EXISTS
#   #4574 measured three Telegram senders in this repo with TWO different
#   delivery contracts:
#     1. tortoise/telegram_push.py::send_message — status AND the API's `ok`
#        field (the shared Python sender; `tortoise/notify.py` and
#        `tortoise/hosted_api.py` both delegate to it);
#     2. .github/scripts/availability-watchdog.sh::telegram_send — status AND
#        `ok:true` (aligned with #1 by #3887);
#     3. .github/scripts/registry-cron.sh::telegram — `curl … || true`, with NO
#        status check and NO `ok` check.
#   #4574's verdict was `unify-contract-keep-drivers`: the CI-shell DRIVER is a
#   deliberate split (the watchdog job has no python/uv toolchain, and adding
#   one would put supply-chain surface inside the one job whose whole value is
#   sitting outside the app's failure domain), but the CONTRACT must be one.
#   This file is that contract as a sourced shell helper.
#
# THE CONTRACT — a 2xx is NOT delivery
#   Telegram answers `200 {"ok":false,"description":"chat not found"}` for a
#   misconfigured chat id or a bot removed from the chat, and `curl` exits 0 on
#   it. Treating transport success as delivery is the fail-open this contract
#   closes: `tg_send` returns 0 only when the transport succeeded AND the API's
#   own `ok` is `true` (same rule as `tortoise/telegram_push.py`). Callers that
#   need "a human actually received this" — the watchdog's escalation leg —
#   depend on that, so a caller that swallows the return code is choosing
#   best-effort explicitly.
#
# THE PUBLICATION BOUNDARY — the bot token is IN THE URL
#   `curl` echoes the failing URL in its own error text, and these logs are
#   PUBLIC (this is a public repo's Actions log). Both the request URL and the
#   API's response body therefore pass through a scrubber before they are
#   logged. `tg_send` takes its text ALREADY redacted by the caller (the
#   watchdog's `redact_text` covers the probe URL and flyctl output a page can
#   carry); the transport's own token leak is closed HERE, for every caller.
#
# USAGE
#   . .github/scripts/telegram-send.sh
#   tg_send <chat_id> <text-already-redacted>   # 0 = delivered
#
#   Optional hooks (for a caller with richer logging/redaction than the
#   defaults) — set them as a command prefix, e.g.
#   `TG_WARN_FN=warn TG_SCRUB_FN=scrub_output tg_send "$chat" "$text"`:
#     TG_WARN_FN   <message…>            log a warning (default: ::warning::)
#     TG_SCRUB_FN  <text> <max-chars>    redact before logging (default: token
#                                        replacement only)
#     TG_BODY_FILE <path>                where to capture Telegram's response
#                                        JSON (default: $RUN_TMP/telegram-body.json)
#
#   Set TELEGRAM_SEND_LIB_ONLY=1 to source without executing anything (this file
#   defines functions only, so sourcing is always side-effect free — the seam
#   exists to match the family's convention and to make that explicit in a test).
#
# Test: bash .github/scripts/telegram-send.test.sh
# ============================================================================
# ⛔ NO `set -euo pipefail` HERE. This file is SOURCED into a running shell, so
# options set here become the caller's — a library that silently arms `-e` (or
# `pipefail`) in `.github/scripts/availability-watchdog.sh` would change the
# failure behaviour of a live out-of-band monitor. The already-unset-safe `${…:-}`
# expansions below do not need it; each caller keeps its own options.

TELEGRAM_API_BASE="${TELEGRAM_API_BASE:-https://api.telegram.org}"

tg_warn_default() { echo "::warning::$*" >&2; }

# Fallback scrubber: replace the bot token wherever it appears. The token is the
# ONLY credential on this path (it is embedded in the request URL, which curl
# echoes on failure), so token replacement is the whole boundary when a caller
# has no richer scrubber to offer.
tg_scrub_default() {
  local text="${1:-}"
  if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
    text="${text//"$TELEGRAM_BOT_TOKEN"/***}"
  fi
  printf '%s' "$text"
}

tg_warn() { # <message…>
  if [ -n "${TG_WARN_FN:-}" ] && declare -F "$TG_WARN_FN" >/dev/null 2>&1; then
    "$TG_WARN_FN" "$@"
  else
    tg_warn_default "$@"
  fi
}

tg_scrub() { # <text> -> redacted text, safe for a public log
  if [ -n "${TG_SCRUB_FN:-}" ] && declare -F "$TG_SCRUB_FN" >/dev/null 2>&1; then
    "$TG_SCRUB_FN" "$1" 200
  else
    tg_scrub_default "$1"
  fi
}

# Send <text> to <chat_id>. Returns 0 ONLY when the transport succeeded AND the
# API answered `ok:true`, i.e. when a human actually received it.
tg_send() { # <chat_id> <text-already-redacted>
  local chat="${1:-}" text="${2:-}" body_file err resp
  if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "$chat" ]; then
    tg_warn "telegram send skipped — TELEGRAM_BOT_TOKEN and the escalation chat are both required (a page must be addressed and signed)"
    return 1
  fi
  body_file="${TG_BODY_FILE:-${RUN_TMP:-${TMPDIR:-/tmp}}/telegram-body.json}"
  # `--fail-with-body` makes an HTTP 4xx a failure, but `-o /dev/null` would
  # THROW AWAY Telegram's own error JSON — the actionable `description` ("chat
  # not found", "Unauthorized") never reaches the log, only curl's opaque
  # `(22) … error: 400`. Capture the body to a file and surface it (scrubbed) so
  # a dead paging channel is diagnosable from the public run log.
  if ! : > "$body_file" 2>/dev/null; then
    body_file="$(mktemp)"
  fi
  if ! err="$(curl -sS --fail-with-body --max-time 15 -o "$body_file" \
      "${TELEGRAM_API_BASE}/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${chat}" \
      --data-urlencode "text=$text" 2>&1)"; then
    resp="$(tg_scrub "$(cat "$body_file" 2>/dev/null || true)")"
    if [ -n "$resp" ]; then
      tg_warn "telegram page failed: $(tg_scrub "$err") — api response: ${resp}"
    else
      tg_warn "telegram page failed: $(tg_scrub "$err")"
    fi
    return 1
  fi
  # A 2xx is NOT delivery. The API's own verdict is the authority.
  if ! jq -e '.ok == true' "$body_file" >/dev/null 2>&1; then
    resp="$(tg_scrub "$(cat "$body_file" 2>/dev/null || true)")"
    tg_warn "telegram page REJECTED by the API (HTTP 2xx but ok != true) — api response: ${resp:-<empty>}"
    return 1
  fi
  return 0
}
