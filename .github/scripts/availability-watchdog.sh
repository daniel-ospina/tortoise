#!/usr/bin/env bash
# ============================================================================
# availability-watchdog.sh — out-of-band availability probe + rate-limited
# self-healing + deduplicated alerting for the hosted API (#2850).
#
# WHY THIS EXISTS
# ---------------
# 2026-09-10, ~19:10–19:55 UTC: https://api.premiselabs.co (Fly app
# `tortoise-y4mjjq`) was fully unreachable for ~45 minutes and NOBODY WAS
# PAGED. It was found by a user reading a browser console error and ended by a
# human typing `flyctl machine restart`.
#
# Nothing in the stack could have caught it:
#   * Sentry runs INSIDE the process → structurally blind to a process that is
#     alive-but-not-serving. The failure class raises NO exception; the Fly
#     proxy simply stops routing ("no known healthy instances found for route
#     tcp/443") and every public request hangs until it times out.
#   * There is exactly ONE machine, so its de-registration IS a total outage.
#   * The Fly HTTP health check flaps and then goes quiet — its signal is the
#     same silence as "no traffic".
#
# So this probe deliberately runs OUTSIDE Fly (GitHub Actions, a different
# failure domain) and asserts the REAL USER PATH — an authenticated API route
# served by the app — not merely that a socket is open.
#
# WHAT IT DOES
#   1. PROBE   `GET <PROBE_URL>` (default GET /v1/teams) with a generous
#              per-request timeout and N attempts before declaring failure, so
#              a transient blip cannot fire a false alarm.
#              Verdicts (see classify_code):
#                UP         — the app ANSWERED (2xx | 401 | 403 | 429)
#                DOWN       — no answer at all (000 timeout/conn) or 5xx
#                UNEXPECTED — it answered something else (3xx, 404, …)
#              A 401 is the EXPECTED unauthenticated answer for /v1/teams
#              (source-verified: tortoise/session_auth.py verify_session_jwt
#              raises 401 "Missing session token" with zero network I/O when
#              the Authorization header is absent). 2xx and 401 both mean "the
#              app answered"; only 000/5xx mean it did not.
#              KNOWN BLIND SPOTS: this probes ONE route, and only its
#              UNAUTHENTICATED branch — an outage that leaves /v1/teams
#              answering while other routes fail reads as UP, and so does an
#              auth-leg break that rejects every real token (the probe sends
#              none). It proves liveness + route presence, not end-to-end
#              authenticated traffic. See the runbook §6.8.
#   2. ALERT   Files exactly ONE GitHub issue per incident, keyed by title, and
#              comments with an incremented count on later runs. On recovery it
#              confirms the recovery, comments and closes. This is the fix for
#              the #2706 failure mode: welcome-e2e-monitor.yml created a NEW
#              issue per failing run (its title embedded github.run_id) and
#              produced dozens of near-identical duplicates — all still open
#              (#2231 → present; 59 open at the 2026-09-11 19:41Z census, 61
#              the same evening — re-measure at cleanup time; #3014).
#   3. HEAL    If the service is confirmed DOWN for a SUSTAINED period, restart
#              the Fly machine — strictly rate limited so a database outage
#              cannot become an infinite restart loop. AWS Well-Architected
#              guidance for automated remediation: "only allow the watcher
#              service to terminate one instance every minute, and if it tries
#              to terminate more, then it sends an alert to get a human
#              involved". The analogue here (5-min cadence, ONE machine):
#                * SUSTAINED_DOWN_MINUTES (10) of continuous failure AND
#                  SUSTAINED_MIN_RUNS (≥2) observed failing runs before the
#                  FIRST restart,
#                * RESTART_COOLDOWN_MINUTES (20) between attempts,
#                * MAX_RESTARTS_PER_HOUR (2) across a rolling hour, tracked
#                  INDEPENDENT of incident identity: a NEW incident inherits the
#                  still-in-window restart stamps left by the previous (now
#                  closed) incident, so a machine that flaps — down, restarted,
#                  recovers, incident closes, down again — cannot reset the
#                  budget at every incident boundary and restart forever;
#              when the cap is hit we do NOT restart and we comment (and page)
#              asking for a human — the "get a human involved" leg, re-notified
#              at most once per CAP_RENOTIFY_MINUTES (60).
#              A DNS-resolution or TLS/certificate failure is NOT restartable:
#              no machine restart repairs a name-resolution or certificate
#              problem, so the incident is filed and reported but the restart
#              leg is disarmed and the issue body says exactly why (the restart
#              budget is reserved for the transport-level failures a wedged
#              process actually produces).
#              An ATTEMPT is recorded to the incident BEFORE flyctl runs, so a
#              crash or a failed API call cannot erase the cooldown/cap record
#              (write-then-act: the durable record gates the side effect). A
#              failed attempt still counts against the cap — the failure mode
#              we are preventing is a restart attempt every 5 minutes.
#              MAX_RESTARTS_PER_HOUR=0 is honoured as an operator KILL SWITCH
#              (automated restarts off), not rounded up to the default.
#              Before any restart the RUNNER'S OWN EGRESS is controlled with a
#              probe of a known-good endpoint (CONTROL_URL): if the runner
#              cannot reach the internet the verdict is INCONCLUSIVE — the
#              incident is still recorded and alerted, but nothing is restarted
#              on the strength of a probe that may have failed locally.
#   4. PAGE    (optional) Telegram page on transitions only, reusing the
#              TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID secrets the DR driver
#              already uses. Absent secrets → skipped with a log line.
#
# STATE / DEDUPE KEY
#   The single open issue IS the incident state (no external store, no
#   variables API, no PAT). Its body carries a machine-readable one-line block
#   that this script rewrites on every run:
#     <!-- watchdog-state kind=… first_failure_ts=… down_runs=… \
#          last_down_ts=… last_comment_ts=… cap_notified_ts=… restarts=ts,ts -->
#   The title is the dedupe key — the script searches for an open issue whose
#   title contains the marker before filing, CONSTRAINED TO A MACHINE AUTHOR
#   (`author:app/github-actions` + a `user.type == "Bot"` re-check). On a PUBLIC
#   repo anyone can open an issue with our title and a forged state block;
#   adopting it would hand an attacker the sustained clock and the restart
#   ledger (a restart storm). A non-machine match is NEVER adopted, patched or
#   closed — it is treated as "no incident" and a fresh machine issue is filed.
#   The body is HUMAN-EDITABLE, so every parsed value is bounded/validated
#   (to_int), a stale clock (no failing run within STALE_RESET_MINUTES)
#   restarts the sustained window instead of trusting it, and the sustained
#   window is additionally CLAMPED to the issue's server-side `created_at`
#   (immutable, not body-editable) so no body edit can authorise a restart
#   before the incident has actually existed that long. The restart ledger is
#   FAIL-CLOSED: a `restarts=` value that is present but not fully parseable
#   refuses to act rather than silently dropping an entry (dropping could only
#   WEAKEN the cooldown/cap).
#
# SAFETY PROPERTIES
#   * No token is ever hard-coded; the Fly token comes from a repository
#     secret (FLY_API_TOKEN — the name already used by deploy-hosted.yml).
#     Absent → the restart leg is skipped with a clear log line + escalation,
#     and alerting still works.
#   * A non-default PROBE_URL (a drill) DISARMS the restart leg AND gets its
#     OWN incident identity (`[monitor] DRILL DOWN` + a `[DRILL]` suffix on the
#     host in the title): a
#     drill must never restart production, never mutate a production incident
#     (its body carries the cooldown/cap ledger), and never close one. Without
#     that separate identity a typo'd drill host files a MISLABELLED production
#     incident (and the next production run would then "recover" it).
#   * A failed issue SEARCH refuses to file (never duplicate) and fails the
#     run; a non-numeric issue id is refused the same way. The monitor cannot
#     go silently deaf in the alerting direction.
#   * UNEXPECTED verdicts never restart: a restart cannot fix a route that
#     404s or a redirect, and restarting on it would be a restart loop.
#   * Nothing secret is ever published: redact_text() is applied AT THE
#     PUBLICATION BOUNDARY (create_issue / update_issue_body / comment_issue /
#     page) so it scrubs the probe URL (credentials + query string), the Fly
#     token and the Telegram token from every public body/comment/page/log
#     line — including text echoed back by flyctl. Captured command output goes
#     through scrub_output(), whose order is REFLOW → REDACT → TRUNCATE.
#     Reflowing the whitespace FIRST is what makes a WRAPPED credential
#     matchable: a real Fly token is `FlyV1 <macaroon>`, a SPACE-bearing value
#     that a captured log wraps at that space, so redacting first left both
#     fragments unmatched and the subsequent reflow re-joined them into a
#     complete, readable credential in the published body/comment/log.
#     Redacting before truncating is what keeps a long token whole:
#     truncating first splits it, after which the literal-value match
#     no longer matches the surviving prefix and a PARTIAL production token is
#     published. redact_text() also scrubs BY SHAPE (Fly `FlyV1 …` macaroons,
#     `id:secret` Telegram tokens) so a partial whose full value we do not hold
#     is still caught. stdout is DATA only (all logging is on stderr) so a log
#     line can never corrupt a captured helper return.
#   * Only a MACHINE-authored issue is ever adopted or mutated: the dedupe
#     search carries `author:app/github-actions` AND the returned item's
#     `user.type`/`user.login` is re-checked before its number is used, so no
#     public account can seed or hijack the incident state (see STATE above).
#   * Every notification is gated on a DURABLE record: the restart attempt and
#     the cap/INCONCLUSIVE escalation stamps are written BEFORE the side effect,
#     and a routine comment is published only after the body (which carries the
#     comment-throttle stamp) has been written. A failed state write therefore
#     means no restart, no page and no comment — never an unbounded
#     notification loop driven by state that could not be persisted.
#
# Deliberately NOT here: no paid third-party uptime service; no fly.toml or
# tortoise/ edits (owned elsewhere); no new GitHub labels (only a pre-existing
# one is used — the API silently DROPS unknown labels, which would leave the
# dedupe search blind).
#
# EXIT CODES
#   0  service UP (or recovered)
#   1  service DOWN / UNEXPECTED — the workflow FAILS so GitHub's own
#      notifications fire
#   1  the monitor itself is broken (missing GH_TOKEN/jq, failed search, failed
#      issue write/close, a drill that cannot record state) — fail loud, never
#      a deaf monitor
#
# Usage:  bash .github/scripts/availability-watchdog.sh
# Env:    see the defaults below (all overridable; the test harness drives them)
# Test:   bash .github/scripts/availability-watchdog.test.sh  (self-contained,
#         stubs curl/gh/flyctl; runs in CI job availability-watchdog)
# ============================================================================

set -euo pipefail

# ── configuration (all overridable — the test harness drives these) ─────────
DEFAULT_PROBE_URL="https://api.premiselabs.co/v1/teams"
# ONE literal, referenced twice: the workflow passes an EMPTY PROBE_URL on the
# scheduled path so this default applies (see availability-watchdog.yml), and
# `is_prod` is decided by comparing PROBE_URL to DEFAULT_PROBE_URL. A second
# copy of the production URL is how a drift turns every production run into a
# silent drill (no restarts, [DRILL]-titled incidents).
PROBE_URL="${PROBE_URL:-$DEFAULT_PROBE_URL}"
# Display name in titles/logs. Deliberately NOT derived from PROBE_URL: the
# title is the dedupe key and must not move when a drill overrides the URL.
PROBE_HOST_LABEL="${PROBE_HOST_LABEL:-api.premiselabs.co}"
PROBE_TIMEOUT_S="${PROBE_TIMEOUT_S:-25}"          # per-request --max-time
PROBE_CONNECT_TIMEOUT_S="${PROBE_CONNECT_TIMEOUT_S:-10}"
PROBE_ATTEMPTS="${PROBE_ATTEMPTS:-3}"             # retries before DOWN
PROBE_RETRY_SLEEP_S="${PROBE_RETRY_SLEEP_S:-10}"
# Recovery hysteresis: extra confirmation probes that must ALSO answer UP
# before the incident is closed (0 disables). Prevents a single flapping
# success from closing a live incident and resetting the sustained clock.
RECOVERY_CONFIRM_PROBES="${RECOVERY_CONFIRM_PROBES:-1}"
SUSTAINED_DOWN_MINUTES="${SUSTAINED_DOWN_MINUTES:-10}"
# Minimum number of OBSERVED failing runs before the first restart. Guards the
# case where the issue body carries an old/large first_failure_ts (a reopened
# or human-edited incident): the wall clock alone must not authorise a restart.
SUSTAINED_MIN_RUNS="${SUSTAINED_MIN_RUNS:-}"
RESTART_COOLDOWN_MINUTES="${RESTART_COOLDOWN_MINUTES:-20}"
MAX_RESTARTS_PER_HOUR="${MAX_RESTARTS_PER_HOUR:-2}"
COMMENT_THROTTLE_MINUTES="${COMMENT_THROTTLE_MINUTES:-15}"
CAP_RENOTIFY_MINUTES="${CAP_RENOTIFY_MINUTES:-60}"
# A failing run must have been seen within this window for the sustained clock
# to be trusted; otherwise the clock is reset to now (a stale incident — e.g.
# one a human reopened after recovery — must not restart production instantly).
# It must be well ABOVE the 5-min cadence: GitHub delays/drops scheduled runs
# under load, and a reset also re-arms the sustained window, so a value near the
# cadence would re-arm the window on every run (see the stale branch in main).
STALE_RESET_MINUTES="${STALE_RESET_MINUTES:-45}"
EVIDENCE_MAX_CHARS="${EVIDENCE_MAX_CHARS:-4000}"
FLY_APP="${FLY_APP:-tortoise-y4mjjq}"
FLY_API_TOKEN="${FLY_API_TOKEN:-}"
GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
REPO="${GITHUB_REPOSITORY:-daniel-ospina/tortoise}"
# Runner-side egress control: an endpoint wholly outside this app's failure
# domain. Only consulted when the app probe says DOWN and a restart is on the
# table, so UP runs cost nothing.
CONTROL_URL="${CONTROL_URL:-https://www.google.com/generate_204}"
CONTROL_TIMEOUT_S="${CONTROL_TIMEOUT_S:-10}"
CONTROL_ATTEMPTS="${CONTROL_ATTEMPTS:-2}"
# Pre-existing label only (see header). Applied when filing; NOT used to
# filter the dedupe search (a renamed label would empty the search and turn the
# monitor back into a duplicate-issue spammer).
ALERT_LABEL="${ALERT_LABEL:-auto-filed}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
# Test seam: pin "now" so cooldown/velocity arithmetic is deterministic.
WATCHDOG_NOW_EPOCH="${WATCHDOG_NOW_EPOCH:-}"

DOWN_MARKER="[monitor] PROD DOWN"
DOWN_TITLE="${DOWN_MARKER} — ${PROBE_HOST_LABEL} is not answering the availability probe"
DEGRADED_MARKER="[monitor] PROD DEGRADED"
DEGRADED_TITLE="${DEGRADED_MARKER} — ${PROBE_HOST_LABEL} answered the availability probe unexpectedly"

# The label published when the probe URL cannot be parsed safely (see
# probe_host_label). A constant, so an unparseable DRILL URL can never fall
# back to the production label and read as a production outage.
PROBE_HOST_REDACTED="<redacted-host>"

# Host label for the probe URL (a drill's issue must name the host it actually
# probed). Falls back to the REDACTED placeholder above when the URL cannot be
# parsed — never to `$PROBE_HOST_LABEL`, which in the drill branch is the
# PRODUCTION label and would mislabel a drill as a production outage.
# SECURITY: the host is taken WITHOUT the userinfo — `https://user:s3cr3t@h/`
# must not put credentials in a public issue TITLE (redact_url() strips them
# from the full URL, but this label is published on its own). The naive
# "cut the authority at the first `/`, then strip up to `@`" split leaks a
# PASSWORD when a hand-written userinfo contains a `/` (legal in practice, not
# per RFC 3986): `rediss://user:pa/ss@host:6379` made the authority `user:pa`
# — the password prefix — because the `@` fell outside the cut. So an `@` that
# is present in the string but ABSENT from the authority we cut is treated as a
# parse failure and the label is redacted outright.
probe_host_label() { # <url>
  local rest authority host
  rest="$1"
  # 1. drop the scheme (a scheme-less URL — a hand-written drill — still parses)
  case "$rest" in
    *://*) rest="${rest#*://}" ;;
  esac
  [ -n "$rest" ] || { printf '%s' "$PROBE_HOST_REDACTED"; return 0; }
  # 2. authority = everything before the first `/`, `?` or `#`
  authority="${rest%%[/?#]*}"
  # 3. an `@` in the remainder that the authority does not contain means the
  #    userinfo was NOT where the split assumed it was (see the header) — fail
  #    closed instead of publishing whatever we happened to cut.
  case "$rest" in
    *@*)
      case "$authority" in
        *@*) : ;;  # normal: the userinfo sits inside the authority
        *) printf '%s' "$PROBE_HOST_REDACTED"; return 0 ;;
      esac ;;
  esac
  # 4. strip the userinfo (everything up to the LAST `@`) and the `:port`
  host="${authority##*@}"
  host="$(printf '%s' "$host" | sed -E 's/:[0-9]+$//' || true)"
  if [ -n "$host" ]; then printf '%s' "$host"; else printf '%s' "$PROBE_HOST_REDACTED"; fi
}

# The incident IDENTITY (marker + title + label) is per-target. The title is the
# dedupe key, so a drill needs its own: reusing the production marker would make
# a drill adopt — and WRITE ITS STATE INTO — the live production incident, and a
# typo'd drill host would file a production-titled issue that the next
# production run would then "recover".
set_incident_identity() { # <is_prod>
  if [ "$1" = "1" ]; then
    PROBE_HOST_LABEL="${PROBE_HOST_LABEL:-api.premiselabs.co}"
    DOWN_MARKER="[monitor] PROD DOWN"
    DEGRADED_MARKER="[monitor] PROD DEGRADED"
  else
    PROBE_HOST_LABEL="$(probe_host_label "$PROBE_URL") [DRILL]"
    DOWN_MARKER="[monitor] DRILL DOWN"
    DEGRADED_MARKER="[monitor] DRILL DEGRADED"
  fi
  DOWN_TITLE="${DOWN_MARKER} — ${PROBE_HOST_LABEL} is not answering the availability probe"
  DEGRADED_TITLE="${DEGRADED_MARKER} — ${PROBE_HOST_LABEL} answered the availability probe unexpectedly"
}

RUN_TMP="$(mktemp -d)"
trap 'rm -rf "$RUN_TMP"' EXIT

# ── logging ─────────────────────────────────────────────────────────────────
# ALL logging goes to STDERR. Several helpers (search_open_alert, do_restart)
# return their value on STDOUT and are called inside `$( )` — a log line on
# stdout would be captured into the return value and corrupt it (a __ERR__
# sentinel merged with a warning string was read as an issue number). stdout is
# DATA only; the harness asserts it stays empty on every case.
log()  { echo "$*" >&2; }
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  note() { echo "::notice::$*" >&2; }
  warn() { echo "::warning::$*" >&2; }
  fail() { echo "::error::$*" >&2; }
else
  note() { echo "NOTICE: $*" >&2; }
  warn() { echo "WARNING: $*" >&2; }
  fail() { echo "ERROR: $*" >&2; }
fi

now_epoch() {
  if [ -n "$WATCHDOG_NOW_EPOCH" ]; then
    printf '%s' "$WATCHDOG_NOW_EPOCH"
  else
    date -u +%s
  fi
}

# epoch -> ISO-8601 Z. GNU date first (CI runners), BSD date fallback (macOS
# local dev — the harness runs on both).
fmt_iso() {
  date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null && return 0
  date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null && return 0
  printf 'epoch:%s' "$1"
}

# ISO-8601 (GitHub's server-side timestamps) -> epoch, or "" when unparseable.
# The `epoch:<n>` form is accepted so this round-trips fmt_iso's own fallback
# (and works on a platform with neither GNU nor BSD date). A "" return is the
# fail-closed direction: the sustained-window anchor is then treated as
# unusable and the window is restarted, never trusted.
iso_to_epoch() { # <iso|epoch:n> -> epoch or ""
  local iso="$1" e
  [ -n "$iso" ] || { printf ''; return 0; }
  case "$iso" in
    epoch:*)
      # Only the numeric fallback form fmt_iso emits is accepted; anything else
      # is an unusable anchor (fail closed, not a trusted clock).
      e="${iso#epoch:}"
      case "$e" in
        ''|*[!0-9]*) printf ''; return 0 ;;
      esac
      printf '%s' "$e"; return 0 ;;
  esac
  e="$(date -u -d "$iso" +%s 2>/dev/null || true)"
  if [ -z "$e" ]; then
    # BSD date is strict about the literal 'Z'; try both spellings.
    e="$(date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$iso" +%s 2>/dev/null || true)"
  fi
  if [ -z "$e" ]; then
    e="$(date -u -j -f "%Y-%m-%dT%H:%M:%S" "${iso%Z}" +%s 2>/dev/null || true)"
  fi
  case "$e" in
    ''|*[!0-9]*) printf ''; return 0 ;;
  esac
  printf '%s' "$e"
}

# bash arithmetic is DECIMAL-BY-SURPRISE: $((08)) is invalid octal and aborts
# the shell. The state block lives in a human-editable issue body, so every
# parsed integer is normalized here (leading zeros stripped, absurd lengths
# rejected) before it can reach $(( )).
to_int() { # <raw> <default>
  local raw digits
  raw="$(printf '%s' "${1:-}" | tr -dc '0-9')"
  [ -n "$raw" ] || { printf '%s' "$2"; return 0; }
  digits="${#raw}"
  [ "$digits" -le 12 ] || { printf '%s' "$2"; return 0; }
  raw="$(printf '%s' "$raw" | sed 's/^0*//')"
  [ -n "$raw" ] || raw="0"
  printf '%s' "$raw"
}

# int_or <env value> <default> <min> — tolerate a missing/garbage env override
# (e.g. PROBE_ATTEMPTS=0 would otherwise "confirm" an outage from zero probes).
int_or() {
  local v
  v="$(to_int "${1:-}" "$2")"
  [ "$v" -ge "$3" ] 2>/dev/null || v="$2"
  printf '%s' "$v"
}

# A probe URL may carry credentials (user:pass@) AND a query string that can
# carry a token. The issue body is PUBLIC — publish neither.
# The userinfo strip must not assume the authority cut comes first: a
# hand-written userinfo containing a `/` (`rediss://user:pa/ss@host:6379`) put
# the `@` OUTSIDE the naive `[^@/]*` window, so the sed-only version published
# the password prefix VERBATIM in the issue body. An `@` that is present in the
# remaining string but absent from the authority we cut therefore means the URL
# is unparseable, and the whole authority is replaced with `<redacted-url>`
# rather than guessed at.
redact_url() {
  local url="$1" scheme="" rest auth tail
  case "$url" in
    *://*) scheme="${url%%://*}://"; rest="${url#*://}" ;;
    *)     rest="$url" ;;
  esac
  # The authority ends at the first `/`, `?` or `#`.
  auth="${rest%%[/?#]*}"
  tail="${rest#"$auth"}"
  case "$rest" in
    *@*)
      case "$auth" in
        *@*) auth="<redacted>@${auth##*@}" ;;
        *)   printf '%s<redacted-url>' "$scheme"; return 0 ;;
      esac ;;
  esac
  printf '%s%s%s' "$scheme" "$auth" \
    "$(printf '%s' "$tail" | sed -E 's#\?.*$#?<redacted>#' || true)"
}

# Scrub anything secret out of text that ends up in a public issue body, a
# comment, or an Actions log: the raw probe URL, query-string credentials, the
# Fly token and the Telegram bot token — by VALUE and, belt-and-braces, by
# SHAPE. The shape pass is what catches a token we do not hold the value of (a
# token from another environment, or a PARTIAL already split by truncation) and
# it is why redaction must happen at the CAPTURE site, not only at the
# publication boundary.
redact_text() {
  local t="$1"
  if [ -n "$FLY_API_TOKEN" ]; then t="${t//$FLY_API_TOKEN/<redacted>}"; fi
  if [ -n "$TELEGRAM_BOT_TOKEN" ]; then t="${t//$TELEGRAM_BOT_TOKEN/<redacted>}"; fi
  if [ -n "$PROBE_URL" ]; then t="${t//$PROBE_URL/$(redact_url "$PROBE_URL")}"; fi
  printf '%s' "$t" | sed -E \
    -e 's#([?&](token|key|secret|sig|signature|api_key|apikey|access_token)=)[^&"[:space:]]*#\1<redacted>#g' \
    -e 's#FlyV1[[:space:]]+[A-Za-z0-9_=+/.,-]+#<redacted>#g' \
    -e 's#[0-9]{6,12}:[A-Za-z0-9_-]{30,}#<redacted>#g'
}

# head -c closes the pipe early → SIGPIPE (exit 141) on the writer, which under
# `set -e` would abort an assignment. `|| true` keeps the truncation total.
first_chars() { # <text> <max>
  printf '%s' "$1" | head -c "$2" || true
}

# Capture-then-publish helper for COMMAND OUTPUT we are about to put in a
# public body/comment/log. The ORDER is the whole point:
#   1. REFLOW the whitespace to a single line, THEN
#   2. REDACT by value and by shape, THEN
#   3. TRUNCATE.
# (1) is load-bearing and was round 1's bug: a real Fly token is
# `FlyV1 <macaroon>` — a SPACE-bearing value — and a captured log wraps it at
# that space. Redacting FIRST left both fragments unmatched (the literal value
# contains a space, the log contained a newline) and the reflow that followed
# then RE-JOINED them into a complete, readable credential in the published
# body/comment/log. `tr -s '[:space:]' ' '` (not `tr '\n' ' '`) also folds the
# `\r` of a CRLF wrap, so `FlyV1\r\nfm2_…` reassembles to the exact token.
# (2) before (3) keeps a long token whole: truncating first splits it, after
# which the literal-value match no longer matches the surviving PREFIX and a
# partial production token would be published.
scrub_output() { # <text> <max>
  local t
  t="$(printf '%s' "$1" | tr -s '[:space:]' ' ' || true)"
  t="$(redact_text "$t")"
  first_chars "$t" "$2"
}

# ── verdict classification ──────────────────────────────────────────────────
# UP         — the app answered. 2xx is the obvious one; 401/403 mean "the
#              route exists and the app is enforcing auth" (the expected
#              unauthenticated answer for /v1/teams); 429 means "the app
#              answered and is throttling us" (our probe being throttled is not
#              an outage).
# DOWN       — no answer (000: timeout / connection error) or a 5xx.
# UNEXPECTED — it answered something else: a 3xx (curl does not follow
#              redirects, so a moved route shows up) or a 4xx-other (404 = the
#              route is GONE — a deploy regression, not an outage).
classify_code() {
  case "$1" in
    2??)         printf 'UP' ;;
    401|403|429) printf 'UP' ;;
    000|5??)     printf 'DOWN' ;;
    *)           printf 'UNEXPECTED' ;;
  esac
}

# curl exit code -> the LAYER that failed. A "no answer" (000) can come from
# very different layers, and only some of them are a restart's business:
# a WEDGED PROCESS shows up as a transport-level failure (the app refused the
# connection, timed out, or answered 5xx), which a restart can clear; a
# DNS-resolution or TLS/certificate failure is a name/service/certificate
# problem that no machine restart can repair — restarting on one only burns the
# automated-restart budget and adds noise to an incident a human must fix
# (review round 2, P2).
#   dns       — curl 6 (could not resolve host)
#   tls       — curl 35/51/58/59/60/66/77/80/82/83/90/91 (handshake/cert/SNI)
#   timeout   — curl 28
#   refused   — curl 7
#   transport — any other non-zero curl exit (56 recv failure, 52 empty reply…)
#   app       — curl itself succeeded; the HTTP status carries the story
#   none      — nothing recorded
classify_failure() { # <curl_exit_code> <stderr>
  local rc="$1" text="$2"
  case "$rc" in
    0)                                    printf 'app';     return 0 ;;
    6)                                    printf 'dns';     return 0 ;;
    28)                                   printf 'timeout'; return 0 ;;
    7)                                    printf 'refused'; return 0 ;;
    35|51|58|59|60|66|77|80|82|83|90|91)   printf 'tls';     return 0 ;;
  esac
  # Some builds / a TLS-terminating proxy report a cert or name failure with a
  # generic code; the message is then the only signal. Match case-insensitively.
  case "$(printf '%s' "$text" | tr 'A-Z' 'a-z')" in
    *"certificate"*|*"ssl"*|*"tls"*|*"self-signed"*|*"self signed"*) printf 'tls'; return 0 ;;
    *"resolve host"*|*"could not resolve"*|*"name or service not known"*|*"nodename nor servname"*) printf 'dns'; return 0 ;;
  esac
  printf 'transport'
}

# 0 = this failure class IS a restart's business; 1 = a restart cannot fix it.
restartable_failure() { # <class>
  case "$1" in
    dns|tls) return 1 ;;
    *)       return 0 ;;
  esac
}

# Human-readable form of a failure class, for the public issue body.
failure_label() { # <class>
  case "$1" in
    dns)       printf 'a DNS resolution failure' ;;
    tls)       printf 'a TLS/certificate failure' ;;
    timeout)   printf 'a connection timeout' ;;
    refused)   printf 'a connection refusal' ;;
    transport) printf 'a transport-level failure' ;;
    app)       printf 'an application error (the app itself answered 5xx)' ;;
    *)         printf 'a probe failure' ;;
  esac
}

# ── probe ───────────────────────────────────────────────────────────────────
# Sets: PROBE_VERDICT, PROBE_CODE, PROBE_FAILURE_CLASS, PROBE_EVIDENCE
# (multi-line). PROBE_FAILURE_CLASS is the curl-level failure layer from
# classify_failure() and is what decides whether a restart is even on the table.
# Retries DOWN verdicts (transient blips) — an UNEXPECTED verdict is a
# deterministic answer, so it stops immediately.
probe() {
  local attempt code timing body_file err_file err_raw err_body v="" rc=0
  PROBE_EVIDENCE=""
  PROBE_CODE="000"
  PROBE_FAILURE_CLASS="none"

  attempt=1
  while [ "$attempt" -le "$PROBE_ATTEMPTS" ]; do
    body_file="$RUN_TMP/body.$attempt"
    err_file="$RUN_TMP/err.$attempt"
    : > "$body_file"
    : > "$err_file"
    # Capture curl's EXIT CODE, not just its -w output: an HTTP 000 is emitted
    # for a DNS failure, a TLS failure, a refusal and a timeout alike, and the
    # exit code is the only thing that tells them apart (see classify_failure).
    rc=0
    timing="$(curl -sS -o "$body_file" -w '%{http_code} %{time_total}' \
      --connect-timeout "$PROBE_CONNECT_TIMEOUT_S" \
      --max-time "$PROBE_TIMEOUT_S" "$PROBE_URL" 2>"$err_file")" || rc=$?
    code="${timing%% *}"
    [ -n "$code" ] || code="000"
    timing="${timing#* }"
    [ "$timing" != "$code" ] || timing="?"
    PROBE_CODE="$code"
    err_raw="$(cat "$err_file" 2>/dev/null || true)"
    PROBE_FAILURE_CLASS="$(classify_failure "$rc" "$err_raw")"
    err_body="$(scrub_output "$err_raw" 300)"
    PROBE_EVIDENCE="${PROBE_EVIDENCE}attempt ${attempt}/${PROBE_ATTEMPTS}: http_code=${code} curl_exit=${rc} failure=${PROBE_FAILURE_CLASS} elapsed=${timing}s"
    [ -n "$err_body" ] && PROBE_EVIDENCE="${PROBE_EVIDENCE} curl=\"${err_body}\""
    PROBE_EVIDENCE="${PROBE_EVIDENCE}"$'\n'

    v="$(classify_code "$code")"
    if [ "$v" = "UP" ]; then
      PROBE_VERDICT="UP"
      return 0
    fi
    if [ "$v" = "UNEXPECTED" ]; then
      # The app answered — capture what it said (a public error body only; the
      # probe carries no credentials, so there is nothing secret to leak).
      if [ -s "$body_file" ]; then
        PROBE_EVIDENCE="${PROBE_EVIDENCE}response body (first 200 chars): $(scrub_output "$(cat "$body_file" 2>/dev/null || true)" 200)"$'\n'
      fi
      PROBE_VERDICT="UNEXPECTED"
      return 0
    fi
    if [ "$attempt" -lt "$PROBE_ATTEMPTS" ]; then
      log "attempt ${attempt}: HTTP ${code} — retrying in ${PROBE_RETRY_SLEEP_S}s"
      sleep "$PROBE_RETRY_SLEEP_S"
    fi
    attempt=$((attempt + 1))
  done

  PROBE_VERDICT="DOWN"
  return 0
}

# Runner-side egress control. 0 = this runner can reach the internet. 1 = it
# cannot, so a failing app probe is NOT evidence that the app is down: DNS, an
# egress proxy or a Cloudflare-side block on the runner's IP all look exactly
# like "no answer". Restarting production is the disruptive action in this
# script, so it is gated on a verdict we can trust.
control_ok() {
  local attempt code
  if [ -z "$CONTROL_URL" ]; then
    log "control probe disabled (CONTROL_URL is empty) — trusting the app probe for the restart decision"
    return 0
  fi
  attempt=1
  while [ "$attempt" -le "$CONTROL_ATTEMPTS" ]; do
    code="$(curl -sS -o /dev/null -w '%{http_code}' \
      --connect-timeout "$PROBE_CONNECT_TIMEOUT_S" \
      --max-time "$CONTROL_TIMEOUT_S" "$CONTROL_URL" 2>/dev/null || true)"
    case "$code" in
      2??|3??) return 0 ;;
    esac
    attempt=$((attempt + 1))
  done
  return 1
}

# ── GitHub issue helpers ────────────────────────────────────────────────────
urlencode() { printf '%s' "$1" | jq -sRr @uri; }

# Echoes a POSITIVE integer issue number, "" when none is open, or "__ERR__"
# when the search itself failed or answered something unparseable. ERR must
# NEVER be treated as "none" — that is the duplicate-spam direction. The caller
# refuses to file on anything non-numeric.
#
# SECURITY (PUBLIC repo): the repo is public, so "an open issue whose title
# matches" is NOT ours to adopt. Any account can open an issue with our title
# and a FORGED state block, and adopting it would hand them the sustained clock
# and the restart ledger — `down_runs=999` bypasses SUSTAINED_MIN_RUNS and a
# forged `restarts=` bypasses the cooldown AND the hourly cap (a restart storm
# that makes an outage worse). So the search carries `author:app/github-actions`
# AND the returned item's author is re-checked below (`.user.type == "Bot"` /
# `.user.login == "github-actions[bot]"`); a non-machine match is treated as
# "no incident" (a fresh machine issue is filed) and is NEVER adopted, patched,
# commented on or closed.
search_open_alert() { # <marker>
  local q enc out n total
  # TITLE-ONLY dedupe key (no `label:` filter): a renamed/deleted label would
  # silently empty the search and turn the monitor back into a duplicate-issue
  # spammer (#2706 class). The label is applied when FILING, not when searching.
  # The author qualifier is the load-bearing security constraint (see above).
  q="repo:${REPO} is:issue is:open in:title author:app/github-actions \"$1\""
  enc="$(urlencode "$q")"
  # NB: the query MUST go in the URL path — `gh api -f q=…` switches the method
  # to POST and 404s on this endpoint (tenant-provision-monitor, #1133).
  if ! out="$(gh api "search/issues?q=${enc}&per_page=5" 2>"$RUN_TMP/search.err")"; then
    warn "GitHub issue search failed: $(scrub_output "$(cat "$RUN_TMP/search.err" 2>/dev/null || true)" 200)"
    printf '__ERR__'
    return 0
  fi
  # "no open incident" (an empty item list) is NOT a failure — only an
  # unparseable answer is. Conflating the two makes the watchdog refuse to file
  # on every first outage (the search result for a fresh incident is empty).
  if ! n="$(printf '%s' "$out" | jq -r '[.items[]? | select((.user.type // "") == "Bot" or (.user.login // "") == "github-actions[bot]")][0].number // empty' 2>/dev/null)"; then
    warn "GitHub issue search returned an unparseable body"
    printf '__ERR__'
    return 0
  fi
  if [ -z "$n" ]; then
    # Distinguish "nothing matched" from "something matched but was NOT ours":
    # the latter is the hijack attempt this guard exists for, and it is worth a
    # loud line (we still file a fresh machine issue).
    total="$(printf '%s' "$out" | jq -r '.items | length' 2>/dev/null || true)"
    case "$total" in
      ''|*[!0-9]*) total=0 ;;
    esac
    if [ "$total" -gt 0 ]; then
      warn "issue search matched ${total} open issue(s) but NONE was authored by the GitHub Actions bot — ignoring them (a forged look-alike is never adopted) and filing a fresh machine issue"
    fi
    printf ''
    return 0
  fi
  case "$n" in
    *[!0-9]*|0) printf '__ERR__'; return 0 ;;
  esac
  printf '%s' "$n"
}

get_issue_body() { # <n> -> the body, or __ERR__ when the read failed
  local out body
  # The body is the ONLY cooldown/cap memory. Swallowing a read failure would
  # look exactly like "no state" and the next write would ERASE the ledger
  # (down_runs reset to 1, restarts cleared) — the read-side twin of the
  # write-side bug that write-then-act fixes. Return a sentinel and let the
  # caller fail closed, like search_open_alert does.
  #
  # SEAM: the caller reads the issue's SERVER-SIDE created_at from
  # $RUN_TMP/issue.created_at (see the sustained-window clamp in main). It is
  # deliberately NOT returned on stdout: stdout carries the body / __ERR__ only,
  # and this helper is called inside `$( )` where a global assignment would not
  # survive anyway. The file is truncated FIRST so a failed re-read can never
  # leave a stale anchor behind.
  : > "$RUN_TMP/issue.created_at"
  if ! out="$(gh api "repos/${REPO}/issues/$1" 2>"$RUN_TMP/body.err")"; then
    warn "could not read the body of #$1: $(scrub_output "$(cat "$RUN_TMP/body.err" 2>/dev/null || true)" 200)"
    printf '__ERR__'
    return 0
  fi
  if ! body="$(printf '%s' "$out" | jq -r '.body // ""' 2>/dev/null)"; then
    warn "issue body for #$1 was not the expected JSON object"
    printf '__ERR__'
    return 0
  fi
  printf '%s' "$out" | jq -r '.created_at // ""' > "$RUN_TMP/issue.created_at" 2>/dev/null || : > "$RUN_TMP/issue.created_at"
  printf '%s' "$body"
}

create_issue() { # <title> <body> -> number ("" on failure)
  local payload out n
  # redact_text AT THE BOUNDARY: this is the last point before publication, so
  # no future caller can leak a probe URL / Fly token / Telegram token by
  # forgetting to scrub (idempotent — redacting twice is harmless).
  payload="$(jq -n --arg t "$(redact_text "$1")" --arg b "$(redact_text "$2")" --arg l "$ALERT_LABEL" \
    '{title:$t, body:$b, labels:[$l]}')"
  if ! out="$(printf '%s' "$payload" | gh api "repos/${REPO}/issues" --method POST --input - 2>"$RUN_TMP/create.err")"; then
    fail "issue create failed: $(scrub_output "$(cat "$RUN_TMP/create.err" 2>/dev/null || true)" 300)"
    printf ''
    return 0
  fi
  n="$(printf '%s' "$out" | jq -r '.number // empty' 2>/dev/null || true)"
  case "$n" in
    ''|*[!0-9]*) printf ''; return 0 ;;
  esac
  printf '%s' "$n"
}

# The issue body is the ONLY durable state store, so its write is
# AUTHORITATIVE: a failed update returns 1 and the caller decides (the restart
# path aborts rather than restarting without a durable cooldown/cap record).
# INVARIANT (security): the target number must have come from search_open_alert
# (machine-author verified) or create_issue (authored by our own token, i.e.
# the Actions app) — a PATCH here writes machine state into the issue, so it
# must NEVER be pointed at a human/attacker-authored look-alike.
update_issue_body() { # <n> <body> -> 0 ok / 1 failed
  local payload
  payload="$(jq -n --arg b "$(redact_text "$2")" '{body:$b}')"
  if ! printf '%s' "$payload" | gh api "repos/${REPO}/issues/$1" --method PATCH --input - >/dev/null 2>"$RUN_TMP/patch.err"; then
    warn "issue body update failed for #$1: $(scrub_output "$(cat "$RUN_TMP/patch.err" 2>/dev/null || true)" 200)"
    return 1
  fi
  return 0
}

comment_issue() { # <n> <body> -> 0 ok / 1 failed
  local payload
  payload="$(jq -n --arg b "$(redact_text "$2")" '{body:$b}')"
  if ! printf '%s' "$payload" | gh api "repos/${REPO}/issues/$1/comments" --method POST --input - >/dev/null 2>&1; then
    warn "comment failed on #$1"
    return 1
  fi
  return 0
}

close_issue() { # <n> -> 0 ok / 1 failed
  if ! printf '%s' '{"state":"closed"}' | gh api "repos/${REPO}/issues/$1" --method PATCH --input - >/dev/null 2>&1; then
    warn "close failed on #$1"
    return 1
  fi
  return 0
}

# ── optional Telegram page (transitions only; never fails the run) ──────────
page() { # <text>
  local err
  # Same publication boundary as the issue helpers: a page carries the probe
  # URL, flyctl's echoed output and (inside curl's URL) the bot token.
  local text
  text="$(redact_text "$1")"
  if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    log "telegram page skipped (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set)"
    return 0
  fi
  # The bot token is IN THE URL, and curl echoes the URL in its error text —
  # which would land in a PUBLIC Actions log. Capture stderr and redact the
  # token before logging.
  if ! err="$(curl -sS --max-time 15 -o /dev/null \
      "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
      --data-urlencode "text=$text" 2>&1)"; then
    warn "telegram page failed (non-fatal): $(scrub_output "$err" 200)"
  fi
  return 0
}

# ── incident state (carried in the issue body) ──────────────────────────────
STATE_FIRST_FAILURE_TS=""
STATE_DOWN_RUNS="0"
STATE_LAST_DOWN_TS="0"
STATE_LAST_COMMENT_TS="0"
STATE_CAP_NOTIFIED_TS="0"
STATE_RESTARTS=""
# Set by parse_state when `restarts=` is PRESENT but not fully parseable. The
# restart gate refuses to act on it (fail closed) — dropping an entry could
# only WEAKEN the cooldown/cap.
STATE_RESTARTS_INVALID="0"
STATE_RESTARTS_RAW=""

parse_state() { # <body>
  local body="$1"
  # Every field is passed through to_int: the body is human-editable and bash
  # arithmetic aborts the shell on values like `08` (invalid octal).
  STATE_FIRST_FAILURE_TS="$(to_int "$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*first_failure_ts=\([0-9][0-9]*\).*/\1/p' | head -1 || true)" "")"
  STATE_DOWN_RUNS="$(to_int "$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*down_runs=\([0-9][0-9]*\).*/\1/p' | head -1 || true)" "0")"
  STATE_LAST_DOWN_TS="$(to_int "$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*last_down_ts=\([0-9][0-9]*\).*/\1/p' | head -1 || true)" "0")"
  STATE_LAST_COMMENT_TS="$(to_int "$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*last_comment_ts=\([0-9][0-9]*\).*/\1/p' | head -1 || true)" "0")"
  STATE_CAP_NOTIFIED_TS="$(to_int "$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*cap_notified_ts=\([0-9][0-9]*\).*/\1/p' | head -1 || true)" "0")"
  STATE_RESTARTS=""
  STATE_RESTARTS_INVALID="0"
  STATE_RESTARTS_RAW=""
  # ── the restart ledger: FAIL CLOSED ────────────────────────────────────────
  # Every other state read in this script fails closed (get_issue_body /
  # search_open_alert return __ERR__ and the caller exits 1). This one used to
  # do the OPPOSITE: a permissive capture plus a `continue` on any entry
  # `to_int` rejected meant an unparseable entry was silently DROPPED. Dropping
  # a cooldown stamp can only weaken the rate limit (the entry vanishes and
  # decide_restart may return `go` sooner) — that is fail-open, and this is the
  # field an attacker/human edit would target. So: parse the WHOLE value
  # strictly; anything not fully parseable sets STATE_RESTARTS_INVALID and the
  # restart gate refuses to act.
  STATE_RESTARTS_RAW="$(printf '%s' "$body" \
    | sed -n 's/.*watchdog-state[^>]*restarts=\([^>]*\).*/\1/p' | head -1 || true)"
  STATE_RESTARTS_RAW="$(printf '%s' "$STATE_RESTARTS_RAW" \
    | tr ',' ' ' | tr -s '[:space:]' ' ' | sed -E 's/^[[:space:]-]+//; s/[[:space:]-]+$//')"
  # The ledger needs the SAME normalizer as the scalar fields, for the same
  # human-editable-body reasons: `restarts=…,0008` is bash OCTAL (the
  # substitution inside decide_restart aborts the shell, and with `set -e` the
  # run dies BEFORE any state write), a >12-digit entry makes `[ ts -gt last ]`
  # error so `last` stays 0 and the COOLDOWN IS SKIPPED, and a future
  # millisecond epoch yields a negative elapsed time → cooldown forever (an
  # inert self-heal with no escalation). Clamp a future stamp to now; REJECT
  # (do not drop) anything the normalizer refuses.
  local raw_ts clean_ts cnow ledger=""
  cnow="$(now_epoch)"
  if [ -n "$STATE_RESTARTS_RAW" ]; then
    case "$STATE_RESTARTS_RAW" in
      *[!0-9[:space:]]*) STATE_RESTARTS_INVALID="1" ;;
    esac
    if [ "$STATE_RESTARTS_INVALID" = "0" ]; then
      for raw_ts in $STATE_RESTARTS_RAW; do
        clean_ts="$(to_int "$raw_ts" "")"
        if [ -z "$clean_ts" ]; then
          # >12 digits (to_int's own bound): unparseable, not droppable.
          STATE_RESTARTS_INVALID="1"
          break
        fi
        if [ "$clean_ts" -gt "$cnow" ]; then clean_ts="$cnow"; fi
        ledger="${ledger:+$ledger }$clean_ts"
      done
      if [ "$STATE_RESTARTS_INVALID" = "0" ]; then STATE_RESTARTS="$ledger"; fi
    fi
  fi
  # Bound the ledger: keep only the last 24 attempts (a year of incidents at
  # the cap would otherwise grow the state block without limit).
  local kept="" ts n=0
  local rev=""
  for ts in $STATE_RESTARTS; do rev="$ts $rev"; done
  for ts in $rev; do
    # explicit `if` (not `[ ] && x`) — no reliance on the set -e AND-OR-list
    # exemption, which differs subtly across shell versions.
    if [ "$n" -lt 24 ]; then kept="$ts $kept"; fi
    n=$((n + 1))
  done
  STATE_RESTARTS="$(printf '%s' "$kept" | sed -E 's/ +$//')"
  # Absent first_failure_ts → this run is the FIRST failure (the conservative
  # direction: never let a missing clock authorise a restart).
  # NB: explicit `if` — a trailing `[ … ] && x` would make this function return
  # 1 whenever the state IS present, and `set -e` would kill the caller.
  if [ -z "$STATE_FIRST_FAILURE_TS" ]; then STATE_FIRST_FAILURE_TS="$cnow"; fi
  # Absent/0 last_down_ts is left at 0 — an UNKNOWN last failing run, NOT "just
  # now". Defaulting it to now made the stale-clock guard unreachable for a
  # body that simply omitted the field (an ancient first_failure_ts with no
  # last_down_ts then satisfied the sustained window on the FIRST observed
  # failing run). 0 trips the stale branch in main, which restarts the window —
  # the fail-closed direction. A machine body always carries a real stamp, so
  # this only fires on a hand-edited/legacy body.
  # A clock in the FUTURE (clock skew, or a hand-edited body) would print
  # negative durations AND lock the sustained gate forever (now - ff < 0), i.e.
  # an inert self-heal with no escalation — the worse failure mode. Clamp it.
  if [ "$STATE_FIRST_FAILURE_TS" -gt "$cnow" ]; then STATE_FIRST_FAILURE_TS="$cnow"; fi
  if [ "$STATE_LAST_DOWN_TS" -gt "$cnow" ]; then STATE_LAST_DOWN_TS="$cnow"; fi
  # The THROTTLE stamps need a clamp too, but the FAIL-LOUD direction: a future
  # `cap_notified_ts` would silence BOTH the cap escalation and the
  # INCONCLUSIVE page (they share the stamp), and a future `last_comment_ts`
  # would silence routine comments — clock skew or a hand-edited body could
  # mute the human channel indefinitely. A stamp that cannot be true is
  # therefore treated as "never notified" (0): at most one extra comment/page,
  # never a silent one.
  if [ "$STATE_LAST_COMMENT_TS" -gt "$cnow" ]; then STATE_LAST_COMMENT_TS="0"; fi
  if [ "$STATE_CAP_NOTIFIED_TS" -gt "$cnow" ]; then STATE_CAP_NOTIFIED_TS="0"; fi
  return 0
}

# CROSS-INCIDENT RESTART BUDGET (review round 2, P2).
# The restart ledger used to die with its incident. A machine that flaps — down
# 10 min, restart, recovers, the incident closes, down again — therefore began
# every NEW incident with an EMPTY ledger, so MAX_RESTARTS_PER_HOUR never
# tripped: one restart per ~10 min forever, which is precisely the unbounded
# self-inflicted restart loop the cap exists to bound.
# So a NEW incident seeds its ledger with the still-in-window restart stamps of
# the most recent MACHINE-authored incident for this marker — open OR closed,
# i.e. the ledger the previous incident left behind. `sort=created&order=desc`
# makes `.items[0]` the most recent. The same hijack guard as the dedupe search
# applies: a non-machine look-alike is never read.
# Reads STATE_RESTARTS / STATE_RESTARTS_INVALID. Returns 1 when the previous
# ledger could not be READ, and the caller then fails closed (no restart
# without a provable hourly budget).
recent_restart_ledger() { # <marker> <now>
  local q enc out n body now="$2" ledger ts
  q="repo:${REPO} is:issue author:app/github-actions in:title \"$1\""
  enc="$(urlencode "$q")"
  if ! out="$(gh api "search/issues?q=${enc}&per_page=5&sort=created&order=desc" 2>"$RUN_TMP/ledger.err")"; then
    warn "restart-ledger search failed: $(scrub_output "$(cat "$RUN_TMP/ledger.err" 2>/dev/null || true)" 200)"
    return 1
  fi
  if ! n="$(printf '%s' "$out" | jq -r '[.items[]? | select((.user.type // "") == "Bot" or (.user.login // "") == "github-actions[bot]")][0].number // empty' 2>/dev/null)"; then
    warn "restart-ledger search returned an unparseable body"
    return 1
  fi
  case "$n" in
    '') STATE_RESTARTS=""; STATE_RESTARTS_INVALID="0"; return 0 ;;
    *[!0-9]*|0)
      warn "restart-ledger search returned a non-numeric issue id ('${n}')"
      return 1 ;;
  esac
  body="$(get_issue_body "$n")"
  if [ "$body" = "__ERR__" ]; then return 1; fi
  # Reuse the ONE normalizer/validator (to_int bounds, future-stamp clamp,
  # strict whole-value parse) rather than a second parser that could drift.
  parse_state "$body"
  # Keep only the stamps still inside the rolling hour. decide_restart re-checks
  # the window; this just keeps the carried body small and the semantics plain.
  ledger=""
  for ts in $STATE_RESTARTS; do
    if [ $((now - ts)) -lt 3600 ]; then ledger="${ledger:+$ledger }$ts"; fi
  done
  STATE_RESTARTS="$ledger"
  return 0
}

restart_history() { printf '%s' "$STATE_RESTARTS" | tr ' ' ','; }

state_block() { # <kind>
  # kind is lowercased in the machine-readable block (stable for parsers).
  printf '<!-- watchdog-state kind=%s first_failure_ts=%s down_runs=%s last_down_ts=%s last_comment_ts=%s cap_notified_ts=%s restarts=%s -->' \
    "$(printf '%s' "$1" | tr 'A-Z' 'a-z')" "$STATE_FIRST_FAILURE_TS" "$STATE_DOWN_RUNS" \
    "$STATE_LAST_DOWN_TS" "$STATE_LAST_COMMENT_TS" "$STATE_CAP_NOTIFIED_TS" \
    "$(restart_history)"
}

render_body() { # <kind> <kindlabel> <selfheal-note>
  local kind="$1" kindlabel="$2" heal="$3" summary what
  if [ "$kind" = "DOWN" ]; then
    summary="🔴 **${PROBE_HOST_LABEL} is DOWN** — the probe got no answer from the app."
    what="no answer (timeout / connection error / 5xx) after ${PROBE_ATTEMPTS} attempts"
  else
    summary="🟠 **${PROBE_HOST_LABEL} answered unexpectedly** — the probe reached the app, but not with an expected response."
    what="an unexpected HTTP status (not 2xx/401/403/429, not 5xx)"
  fi
  cat <<EOF
$(state_block "$kind")

> 🤖 Machine-managed by \`.github/scripts/availability-watchdog.sh\` (#2850).
> The body is rewritten on every probe run — **add human notes as comments**.
> The **title is the dedupe key** (the watchdog searches for it before filing),
> so do not rename this issue.

## ${summary}

| | |
|---|---|
| **Verdict** | ${what} |
| **Kind** | ${kindlabel} |
| **Probe** | \`GET $(redact_url "$PROBE_URL")\` from GitHub Actions — OUTSIDE Fly (a different failure domain than the app) |
| **First observed** | $(fmt_iso "$STATE_FIRST_FAILURE_TS") |
| **Failing probe runs** | ${STATE_DOWN_RUNS} (scheduled every 5 min; last at $(fmt_iso "$STATE_LAST_DOWN_TS")) |
| **Automated restart attempts (this incident)** | $(if [ -n "$(restart_history)" ]; then printf '%s' "$(restart_history)"; else printf 'none'; fi) |

The probe asserts the **real user path**, not just that a socket is open: an
authenticated API route served by the app. \`2xx\`/\`401\`/\`403\`/\`429\` all mean
"the app answered"; a timeout, a connection error or a 5xx mean it did not.
Sentry cannot see this class of failure at all — it runs inside the process,
and a process that is alive-but-not-serving raises no exception.

### Latest probe evidence

\`\`\`
$(first_chars "$(redact_text "$PROBE_EVIDENCE")" "$EVIDENCE_MAX_CHARS")
\`\`\`

### Self-healing

$(redact_text "$heal")

**Operator runbook:** \`docs/infra-runbook.md\` → *Out-of-band availability
watchdog* — how to read a failure, how to restart manually, the cooldown, and
what to do when restarts do not help.
EOF
}

# ── restart decision + execution ────────────────────────────────────────────
# Echoes: go | wait_sustained | wait_runs | wait_cooldown | cap
# Reads STATE_FIRST_FAILURE_TS / STATE_DOWN_RUNS / STATE_RESTARTS; <now> via $1.
decide_restart() {
  local now="$1" ts last=0 n=0
  if [ $((now - STATE_FIRST_FAILURE_TS)) -lt $((SUSTAINED_DOWN_MINUTES * 60)) ]; then
    printf 'wait_sustained'; return 0
  fi
  # Wall-clock alone is not enough: at least SUSTAINED_MIN_RUNS failing runs
  # must have been OBSERVED (guards a stale/reopened incident whose stored
  # first_failure_ts is ancient).
  if [ "$STATE_DOWN_RUNS" -lt "$SUSTAINED_MIN_RUNS" ]; then
    printf 'wait_runs'; return 0
  fi
  for ts in $STATE_RESTARTS; do
    if [ "$ts" -gt "$last" ]; then last="$ts"; fi
  done
  if [ "$last" -gt 0 ] && [ $((now - last)) -lt $((RESTART_COOLDOWN_MINUTES * 60)) ]; then
    printf 'wait_cooldown'; return 0
  fi
  for ts in $STATE_RESTARTS; do
    if [ $((now - ts)) -lt 3600 ]; then n=$((n + 1)); fi
  done
  if [ "$n" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
    printf 'cap'; return 0
  fi
  printf 'go'
}

# 0 = restarted (machine ids echoed to stdout), 2 = could not (reason echoed)
do_restart() {
  local ids id out reason states
  if ! command -v flyctl >/dev/null 2>&1; then
    printf 'flyctl is not installed (FLY_API_TOKEN absent → the setup step was skipped)'
    return 2
  fi
  if ! out="$(flyctl machine list --app "$FLY_APP" --json 2>"$RUN_TMP/fly.err")"; then
    reason="$(scrub_output "$(cat "$RUN_TMP/fly.err" 2>/dev/null || true)" 200)"
    printf 'flyctl machine list failed: %s' "$reason"
    return 2
  fi
  ids="$(printf '%s' "$out" | jq -r '.[]? | select(.state == "started") | .id' 2>/dev/null || true)"
  if [ -z "$ids" ]; then
    # Report WHAT the fleet looks like: `starting`/`created` means a boot or a
    # deploy is in flight, in which case "restart FAILED" is the wrong story.
    states="$(printf '%s' "$out" | jq -r '[.[]? | .state] | join(",")' 2>/dev/null || true)"
    printf 'no STARTED machine for app %s (states: %s) — %s' "$FLY_APP" "${states:-unknown}" \
      "$(case "$states" in *starting*|*created*) printf 'a boot/deploy looks in flight — a restart would not help';; *) printf 'fleet state could not be confirmed';; esac)"
    return 2
  fi
  for id in $ids; do
    if ! flyctl machine restart "$id" --app "$FLY_APP" >/dev/null 2>>"$RUN_TMP/fly.err"; then
      reason="$(scrub_output "$(cat "$RUN_TMP/fly.err" 2>/dev/null || true)" 200)"
      printf 'flyctl machine restart %s failed: %s' "$id" "$reason"
      return 2
    fi
    printf '%s ' "$id"
  done
  return 0
}

# ── main ────────────────────────────────────────────────────────────────────
main() {
  local now kind kindlabel title marker issue decision heal_note comment_body
  local transition_kind restarted_ids rc n_loop kind_loop stale_note="" is_prod=0 body_loop=""
  # Cross-incident restart budget (see recent_restart_ledger): the ledger
  # carried from the previous incident, whether it was readable, and whether the
  # carried ledger was itself corrupt (which must stay fail-closed).
  local carried_ledger="" carried_invalid="0" ledger_ok=1

  # Fail closed: a monitor that cannot file is a DEAF monitor (#2140 class).
  if [ -z "$GH_TOKEN" ]; then
    fail "GH_TOKEN is not set — refusing to run a monitor that cannot file or close issues (a deaf monitor is worse than no monitor)"
    exit 1
  fi
  if ! command -v jq >/dev/null 2>&1; then
    fail "jq is required"
    exit 1
  fi

  # Numeric env hardening: a misconfigured value must not turn into nonsense
  # (PROBE_ATTEMPTS=0 would "confirm" an outage from zero probes).
  PROBE_ATTEMPTS="$(int_or "$PROBE_ATTEMPTS" 3 1)"
  PROBE_CONNECT_TIMEOUT_S="$(int_or "$PROBE_CONNECT_TIMEOUT_S" 10 1)"
  PROBE_TIMEOUT_S="$(int_or "$PROBE_TIMEOUT_S" 25 1)"
  PROBE_RETRY_SLEEP_S="$(int_or "$PROBE_RETRY_SLEEP_S" 10 0)"
  RECOVERY_CONFIRM_PROBES="$(int_or "$RECOVERY_CONFIRM_PROBES" 1 0)"
  SUSTAINED_DOWN_MINUTES="$(int_or "$SUSTAINED_DOWN_MINUTES" 10 1)"
  RESTART_COOLDOWN_MINUTES="$(int_or "$RESTART_COOLDOWN_MINUTES" 20 1)"
  # 0 is a deliberate KILL SWITCH ("never auto-restart"), not a typo to round up
  # to the default — honour it, and let the cap branch report why.
  if [ "$(to_int "$MAX_RESTARTS_PER_HOUR" "")" = "0" ]; then
    MAX_RESTARTS_PER_HOUR=0
  else
    MAX_RESTARTS_PER_HOUR="$(int_or "$MAX_RESTARTS_PER_HOUR" 2 1)"
  fi
  COMMENT_THROTTLE_MINUTES="$(int_or "$COMMENT_THROTTLE_MINUTES" 15 0)"
  CAP_RENOTIFY_MINUTES="$(int_or "$CAP_RENOTIFY_MINUTES" 60 1)"
  STALE_RESET_MINUTES="$(int_or "$STALE_RESET_MINUTES" 45 1)"
  CONTROL_TIMEOUT_S="$(int_or "$CONTROL_TIMEOUT_S" 10 1)"
  CONTROL_ATTEMPTS="$(int_or "$CONTROL_ATTEMPTS" 2 1)"
  # ceil(SUSTAINED/5), floored at 2 observed failing runs.
  local default_min_runs=$(( (SUSTAINED_DOWN_MINUTES + 4) / 5 ))
  [ "$default_min_runs" -ge 2 ] || default_min_runs=2
  SUSTAINED_MIN_RUNS="$(int_or "${SUSTAINED_MIN_RUNS:-}" "$default_min_runs" 1)"

  # A non-default probe URL is a DRILL: it must not restart production AND must
  # not resolve (close/comment) a production incident.
  [ "${PROBE_URL%/}" = "${DEFAULT_PROBE_URL%/}" ] && is_prod=1
  # …and it gets its OWN incident identity (marker/title/label). See
  # set_incident_identity — a shared marker would make a drill write its state
  # INTO the live production incident.
  set_incident_identity "$is_prod"

  now="$(now_epoch)"
  log "availability-watchdog (#2850) — probe $(redact_url "$PROBE_URL") → ${REPO}$([ "$is_prod" = 1 ] || printf ' [DRILL: self-heal + incident resolution DISARMED]')"
  log "limits: sustained=${SUSTAINED_DOWN_MINUTES}m/${SUSTAINED_MIN_RUNS} runs cooldown=${RESTART_COOLDOWN_MINUTES}m cap=${MAX_RESTARTS_PER_HOUR}/h comment-throttle=${COMMENT_THROTTLE_MINUTES}m cap-renotify=${CAP_RENOTIFY_MINUTES}m stale-reset=${STALE_RESET_MINUTES}m"

  probe
  log "verdict: ${PROBE_VERDICT} (HTTP ${PROBE_CODE})"

  # ── UP ───────────────────────────────────────────────────────────────────
  if [ "$PROBE_VERDICT" = "UP" ]; then
    note "✅ ${PROBE_HOST_LABEL} is UP (HTTP ${PROBE_CODE})"
    if [ "$is_prod" != 1 ]; then
      # A drill resolves DRILL incidents only — its markers are DRILL-specific,
      # so the loop below can never touch a production incident. Resolving is
      # still needed: otherwise a typo'd drill host files an issue nothing can
      # ever close (a slow-motion #2706 accumulation).
      note "drill probe (PROBE_URL is not the production endpoint) — resolving only DRILL incidents; never a production one, never a restart"
    fi
    # Recovery hysteresis: a single flapping success must not close a live
    # incident (and reset the sustained clock). Require RECOVERY_CONFIRM_PROBES
    # additional UP answers.
    local i=1 confirmed=1
    while [ "$i" -le "$RECOVERY_CONFIRM_PROBES" ]; do
      local saved_verdict="$PROBE_VERDICT" saved_evidence="$PROBE_EVIDENCE"
      probe
      if [ "$PROBE_VERDICT" != "UP" ]; then
        # Deliberately do NOT restore the saved UP verdict/evidence: the
        # fall-through below needs the FAILING probe's result.
        confirmed=0
        warn "recovery NOT confirmed — confirmation probe ${i}/${RECOVERY_CONFIRM_PROBES} returned ${PROBE_VERDICT} (HTTP ${PROBE_CODE}); NOT closing any incident (flapping)"
        break
      fi
      PROBE_VERDICT="$saved_verdict"
      PROBE_EVIDENCE="$saved_evidence"
      i=$((i + 1))
    done

    if [ "$confirmed" = "1" ]; then
      for kind_loop in DOWN DEGRADED; do
        if [ "$kind_loop" = "DOWN" ]; then marker="$DOWN_MARKER"; else marker="$DEGRADED_MARKER"; fi
        n_loop="$(search_open_alert "$marker")"
        case "$n_loop" in
          __ERR__*|'')
            if [ "$n_loop" = "__ERR__" ]; then
              # A monitor that cannot read its own incident state is broken; the
              # service being up does not make that acceptable (the next DOWN run
              # could act on a stale incident).
              fail "issue search failed on the recovery path — the monitor cannot confirm incident state; failing the run (service is UP)"
              exit 1
            fi
            continue ;;
          *[!0-9]*)
            fail "issue search returned a non-numeric issue id ('${n_loop}') — refusing to act (service is UP)"
            exit 1 ;;
        esac
        body_loop="$(get_issue_body "$n_loop")"
        if [ "$body_loop" = "__ERR__" ]; then
          fail "could not read the body of #${n_loop} — refusing to close an incident whose state cannot be read (the body carries the restart ledger)"
          exit 1
        fi
        parse_state "$body_loop"
        # CLOSE FIRST, comment second: a persistently failing close PATCH must
        # not re-post "Recovered" on every run (the UP path has no throttle
        # stamp — the closed incident itself is what ends the repetition).
        if ! close_issue "$n_loop"; then
          fail "could not close #${n_loop} — failing the run; a stale open incident can authorise a restart later"
          exit 1
        fi
        if ! comment_issue "$n_loop" "✅ **Recovered** — the availability probe at \`$(redact_url "$PROBE_URL")\` answered HTTP ${PROBE_CODE} at $(fmt_iso "$now").

The service failed ${STATE_DOWN_RUNS} probe run(s), starting $(fmt_iso "$STATE_FIRST_FAILURE_TS") (~$(( (now - STATE_FIRST_FAILURE_TS) / 60 )) min). This incident is closed — the watchdog re-files automatically if it recurs."; then
          fail "#${n_loop} was CLOSED but the 'Recovered' comment failed — the state is correct, the record is missing; failing the run so it is not silent"
          exit 1
        fi
        note "closed ${kind_loop} incident #${n_loop} (recovered after ${STATE_DOWN_RUNS} failing run(s))"
        page "✅ RECOVERED — ${PROBE_HOST_LABEL} answers HTTP ${PROBE_CODE} again. Incident #${n_loop} closed after ${STATE_DOWN_RUNS} failing probe run(s) (~$(( (now - STATE_FIRST_FAILURE_TS) / 60 )) min)."
      done
      exit 0
    fi

    # Unconfirmed recovery (the app answered once, then failed): an OPEN
    # incident is already the standing alert, so leave it open and stay green.
    # With NO incident open, reporting green after observing a full failure
    # would be the "green while down" failure mode — fall through to the
    # DOWN/UNEXPECTED path below with the failing confirmation probe's verdict.
    local any_open=0
    for kind_loop in DOWN DEGRADED; do
      if [ "$kind_loop" = "DOWN" ]; then marker="$DOWN_MARKER"; else marker="$DEGRADED_MARKER"; fi
      n_loop="$(search_open_alert "$marker")"
      case "$n_loop" in
        __ERR__*) fail "issue search failed while checking for an open incident (flapping probe) — the monitor cannot confirm incident state; failing the run"; exit 1 ;;
        *[!0-9]*) fail "issue search returned a non-numeric issue id ('${n_loop}') — refusing to act"; exit 1 ;;
        '') : ;;
        *) any_open=1 ;;
      esac
    done
    if [ "$any_open" = "1" ]; then
      note "recovery not confirmed and an incident is already open — leaving it open (the standing alert)"
      exit 0
    fi
    warn "recovery NOT confirmed (${PROBE_VERDICT}, HTTP ${PROBE_CODE}) and NO incident is open — filing an incident for the observed failure"
    # fall through: PROBE_VERDICT / PROBE_CODE / PROBE_EVIDENCE are the failing
    # confirmation probe's, which is exactly the state to report.
  fi

  # ── DOWN / UNEXPECTED ────────────────────────────────────────────────────
  if [ "$PROBE_VERDICT" = "DOWN" ]; then
    kind="DOWN"; kindlabel="DOWN (no answer)"; title="$DOWN_TITLE"; marker="$DOWN_MARKER"
  else
    kind="DEGRADED"; kindlabel="UNEXPECTED (answered wrongly)"; title="$DEGRADED_TITLE"; marker="$DEGRADED_MARKER"
  fi

  # UNEXPECTED never restarts (a restart cannot fix a 404 / a redirect), a
  # non-default PROBE_URL is a drill, and a DNS/TLS failure is not a restart's
  # business (see restartable_failure).
  local restart_mode="$kind"
  if [ "$kind" = "DEGRADED" ]; then
    restart_mode="disarmed:unexpected"
  elif [ "$is_prod" != 1 ]; then
    restart_mode="disarmed:drill"
  elif ! restartable_failure "$PROBE_FAILURE_CLASS"; then
    # No machine restart repairs a name-resolution or certificate problem; it
    # would only spend the restart budget and add noise. The incident is still
    # filed and reported, and the body says why no restart ran.
    restart_mode="disarmed:unfixable"
    log "probe failed at the '${PROBE_FAILURE_CLASS}' layer — NOT restartable; the incident will be reported without a restart"
  fi

  issue="$(search_open_alert "$marker")"
  case "$issue" in
    __ERR__*)
      fail "GitHub issue search failed — refusing to file (never duplicate). This failing run IS the alert."
      exit 1 ;;
    *[!0-9]*)
      fail "GitHub issue search returned a non-numeric issue id ('${issue}') — refusing to act (never comment on or file a garbage id)"
      exit 1 ;;
  esac

  if [ -z "$issue" ]; then
    # CROSS-INCIDENT RESTART BUDGET (P2): a new incident inherits the
    # still-in-window restart stamps of the last machine incident, so closing
    # and reopening the incident (which is what flapping looks like) cannot
    # reset the hourly cap. Only the restart path needs it.
    if [ "$restart_mode" = "DOWN" ]; then
      if recent_restart_ledger "$marker" "$now"; then
        carried_ledger="$STATE_RESTARTS"
        carried_invalid="$STATE_RESTARTS_INVALID"
      else
        ledger_ok=0
      fi
    fi
    # New incident — the issue IS the dedupe key and the state store.
    STATE_FIRST_FAILURE_TS="$now"
    STATE_DOWN_RUNS="0"
    STATE_LAST_DOWN_TS="$now"
    STATE_LAST_COMMENT_TS="0"
    STATE_CAP_NOTIFIED_TS="0"
    STATE_RESTARTS="$carried_ledger"
    STATE_RESTARTS_INVALID="$carried_invalid"
    progress="new"
  else
    body_loop="$(get_issue_body "$issue")"
    if [ "$body_loop" = "__ERR__" ]; then
      fail "could not read the body of incident #${issue} — the body is the cooldown/cap memory, so acting on an unreadable state could restart without a limit; refusing to act (never treat unreadable as 'no state')"
      exit 1
    fi
    parse_state "$body_loop"
    progress="repeat"
    # ── server-side anchor for the sustained window ─────────────────────────
    # SUSTAINED_DOWN_MINUTES used to gate solely on the body's
    # first_failure_ts, which the body itself carries. The issue's created_at
    # is set by GitHub and is NOT body-editable, so clamping the sustained
    # clock to it makes the window unforgeable from below: no body edit (and no
    # look-alike issue) can authorise a restart before the incident has
    # actually existed for SUSTAINED_DOWN_MINUTES. Belt-and-braces with the
    # machine-author search constraint.
    local created_ts
    created_ts="$(iso_to_epoch "$(cat "$RUN_TMP/issue.created_at" 2>/dev/null || true)")"
    if [ -z "$created_ts" ]; then
      # Production ALWAYS returns created_at; absent/unparseable means we have
      # no unforgeable anchor, so do not trust the body's clock — restart the
      # window (fail closed).
      log "incident #${issue} has no usable server-side created_at — restarting the sustained window (fail closed)"
      STATE_FIRST_FAILURE_TS="$now"
    else
      # A clock in the future (skew) would lock the sustained gate forever.
      if [ "$created_ts" -gt "$now" ]; then created_ts="$now"; fi
      if [ "$STATE_FIRST_FAILURE_TS" -lt "$created_ts" ]; then
        log "incident #${issue}: first_failure_ts precedes its created_at — clamping the sustained clock to the server-side created_at"
        STATE_FIRST_FAILURE_TS="$created_ts"
      fi
    fi
    # Stale-clock guard: if no failing run has been recorded recently, this
    # incident is NOT continuous (a human reopened it, a close failed after
    # recovery, or the issue sat open) — restart the sustained window instead
    # of trusting an ancient first_failure_ts.
    #
    # It resets ONLY the sustained clock. It must NOT touch the restart ledger
    # (restarts / cap_notified_ts): those are the rate-limit memory, and
    # clearing them here would disarm the cooldown and the hourly cap on any
    # run gap — the very moment (an incident) when GitHub delays runs. It must
    # not reset down_runs either: each run OBSERVES a failure, and pinning the
    # count at 0 would starve the SUSTAINED_MIN_RUNS gate forever (no restart,
    # ever) whenever the schedule slips.
    if [ $((now - STATE_LAST_DOWN_TS)) -gt $((STALE_RESET_MINUTES * 60)) ]; then
      stale_note="ℹ️ Stale incident clock reset: the previous failing run was $(fmt_iso "$STATE_LAST_DOWN_TS") (more than ${STALE_RESET_MINUTES} min ago), so the ${SUSTAINED_DOWN_MINUTES} min sustained window restarts now. Failing runs are counted continuously since $(fmt_iso "$now")."
      log "stale incident (last failing run $(fmt_iso "$STATE_LAST_DOWN_TS")) — sustained clock reset (the restart ledger is preserved)"
      STATE_FIRST_FAILURE_TS="$now"
    fi
  fi
  STATE_DOWN_RUNS=$((STATE_DOWN_RUNS + 1))
  STATE_LAST_DOWN_TS="$now"

  # Create the issue BEFORE acting, so every later write has a target.
  if [ "$progress" = "new" ]; then
    issue="$(create_issue "$title" "$(render_body "$kind" "$kindlabel" "⏳ Diagnosing — the self-healing decision is written at the end of this run.")")"
    if [ -z "$issue" ]; then
      fail "could not file the incident issue — this failing run IS the alert (runbook documents manual filing)"
      exit 1
    fi
    note "filed ${kind} incident #${issue} — ${title}"
    page "🔴 ${kind} — ${PROBE_HOST_LABEL} probe failing (HTTP ${PROBE_CODE} after ${PROBE_ATTEMPTS} attempts). Incident #${issue}: https://github.com/${REPO}/issues/${issue}"
  fi

  # ── self-heal decision ───────────────────────────────────────────────────
  # transition_kind drives the comment policy:
  #   major     → always comment (restart / restart-failed / velocity cap)
  #   otherwise → comment only if the throttle window has elapsed
  heal_note=""
  comment_body=""
  transition_kind="repeat"

  if [ "$restart_mode" = "DOWN" ]; then
    if [ "$ledger_ok" = "0" ]; then
      # Fail closed: without the previous incident's ledger we cannot prove
      # this restart is inside the hourly cap, and a restart storm is the worse
      # failure. The incident is still filed/escalated below.
      restart_mode="disarmed:no_ledger"
      warn "the previous incident's restart ledger could not be read — cannot prove the hourly budget; NOT restarting (fail closed)"
    elif [ "$STATE_RESTARTS_INVALID" = "1" ]; then
      # FAIL CLOSED on an unparseable restart ledger BEFORE the egress control,
      # so a runner-side network failure cannot mask a corrupt ledger (and the
      # run never rewrites the body with the corrupt ledger silently
      # discarded). `restarts=` is the only cooldown/cap memory; if we cannot
      # read it whole we cannot prove a restart is allowed, and silently
      # treating it as "no restarts" is exactly the fail-OPEN direction that
      # turns into a restart storm. Refuse to act (like an unreadable issue
      # body) and fail the run loudly — the open incident remains the standing
      # alert.
      fail "the restart ledger in incident #${issue} is present but not fully parseable ('$(scrub_output "$STATE_RESTARTS_RAW" 120)') — refusing to restart: a dropped entry could only WEAKEN the cooldown/hourly cap. Fix the \`restarts=\` field in the issue body (ts,ts or empty) and the next run resumes."
      exit 1
    fi
    # Runner-side egress control: a DOWN verdict from a runner that cannot
    # reach the internet says nothing about the app, and a restart is the
    # disruptive action here — do not take it on an untrustworthy verdict.
    if ! control_ok; then
      restart_mode="disarmed:no_egress"
      warn "control probe $(redact_url "$CONTROL_URL") also failed from this runner — cannot distinguish a runner network failure from an app outage; NOT restarting"
    fi
  fi

  if [ "$restart_mode" = "DOWN" ]; then
    decision="$(decide_restart "$now")"
    case "$decision" in
      go)
        if [ -z "$FLY_API_TOKEN" ]; then
          heal_note="⚠️ **Restart NOT issued — the \`FLY_API_TOKEN\` repository secret is not set.** Self-healing is inert until an operator creates it (\`gh secret set FLY_API_TOKEN\`, a Fly token with machine-restart scope — \`fly tokens create deploy --app ${FLY_APP}\` is enough). Until then a human must restart manually: see the runbook § *Out-of-band availability watchdog*."
          warn "restart skipped — the FLY_API_TOKEN secret is absent"
          transition_kind="heal_disarmed"
        else
          # WRITE-THEN-ACT: record the attempt in the incident body BEFORE
          # calling flyctl. The body is the only cooldown/cap memory, so a
          # crash, a lost response, or a failed restart API call must not be
          # able to erase it (that would allow an unthrottled restart every
          # 5 minutes). If the record cannot be written, do NOT restart.
          STATE_RESTARTS="${STATE_RESTARTS:+$STATE_RESTARTS }$now"
          STATE_CAP_NOTIFIED_TS="0"
          if ! update_issue_body "$issue" "$(render_body "$kind" "$kindlabel" "🚑 Restart attempt #$((STATE_DOWN_RUNS)) recorded at $(fmt_iso "$now") — issuing \`flyctl machine restart\` next; the next probe run (~5 min) is the recovery check.")"; then
            fail "could not record the restart attempt in #${issue} — NOT restarting (refusing to restart without a durable cooldown/cap record)"
            exit 1
          fi
          set +e
          restarted_ids="$(do_restart)"
          rc=$?
          set -e
          if [ "$rc" -eq 0 ]; then
            heal_note="🚑 **Restart issued** at $(fmt_iso "$now"): \`flyctl machine restart\` on \`${restarted_ids}\` (app \`${FLY_APP}\`). A restart takes ~60–90 s to boot, so the next probe run (~5 min) is the recovery check."
            transition_kind="restart"
            comment_body="🚑 **Self-heal — restarting Fly machine(s)** \`${restarted_ids}\` (app \`${FLY_APP}\`) at $(fmt_iso "$now").

Down for ~$(( (now - STATE_FIRST_FAILURE_TS) / 60 )) min across ${STATE_DOWN_RUNS} failing run(s) (thresholds: ${SUSTAINED_DOWN_MINUTES} min and ≥${SUSTAINED_MIN_RUNS} runs; ${RESTART_COOLDOWN_MINUTES} min cooldown; ${MAX_RESTARTS_PER_HOUR}/hour cap).

A restart is a **symptom fix** — if this recurs, the root cause is still live (#2850: a FalkorDB socket timeout wedges the event loop; #2953: uvicorn binds the socket only after lifespan startup)."
            note "restart issued on: $(redact_text "${restarted_ids:-<unknown>}")"
            page "🚑 SELF-HEAL — ${PROBE_HOST_LABEL} down ~$(( (now - STATE_FIRST_FAILURE_TS) / 60 )) min; restarting ${restarted_ids} (app ${FLY_APP}). Incident #${issue}."
          else
            heal_note="⛔ **Automatic restart FAILED** — ${restarted_ids}. **A human must intervene now**: \`flyctl machine restart <machine-id> -a ${FLY_APP}\` (\`<machine-id>\` from \`flyctl machine list -a ${FLY_APP}\`), or inspect \`flyctl logs -a ${FLY_APP}\`. This attempt is COUNTED against the ${MAX_RESTARTS_PER_HOUR}/hour cap (the watchdog will not retry it every 5 minutes). Runbook § *Out-of-band availability watchdog*."
            transition_kind="heal_failed"
            comment_body="$heal_note"
            fail "automatic restart failed: $(redact_text "${restarted_ids:-<unknown>}") (recorded as an attempt; counted against the hourly cap)"
            page "⛔ SELF-HEAL FAILED — ${PROBE_HOST_LABEL} still down; the automatic restart did not work (${restarted_ids}). A human is needed. Incident #${issue}."
          fi
        fi
        ;;
      wait_sustained)
        heal_note="⏳ No restart yet: down for $(( (now - STATE_FIRST_FAILURE_TS) / 60 )) min. The watchdog waits ${SUSTAINED_DOWN_MINUTES} min of continuous failure before the first automated restart."
        ;;
      wait_runs)
        heal_note="⏳ No restart yet: ${STATE_DOWN_RUNS} failing run(s) observed; at least ${SUSTAINED_MIN_RUNS} are required (guard against a stale/reopened incident whose stored clock is old)."
        ;;
      wait_cooldown)
        heal_note="⏳ No restart yet: the ${RESTART_COOLDOWN_MINUTES} min cooldown between automated restarts has not elapsed."
        ;;
      cap)
        if [ "$MAX_RESTARTS_PER_HOUR" = "0" ]; then
          heal_note="⛔ **Automated restarts are DISABLED (\`MAX_RESTARTS_PER_HOUR=0\`, an operator kill switch) and the service is still down.** No restart will be attempted — a human must investigate: \`flyctl machine list -a ${FLY_APP}\`, \`flyctl logs -a ${FLY_APP}\`, FalkorDB reachability, the last deploy. Runbook § *Out-of-band availability watchdog*."
        else
          heal_note="⛔ **Restart velocity cap reached — a HUMAN must get involved.** ${MAX_RESTARTS_PER_HOUR} automated restart attempt(s) already ran within the last hour and the service is still down. A restart is not fixing it, so the watchdog deliberately stops: an unbounded restart loop on a database/platform outage makes the outage worse (this is the automated-remediation velocity cap). Investigate the root cause — \`flyctl logs -a ${FLY_APP}\`, FalkorDB reachability, the last deploy — and see the runbook § *Out-of-band availability watchdog*."
        fi
        comment_body="$heal_note"
        # The cap is a STATE, not an event: notify on entry, then re-notify at
        # most once per CAP_RENOTIFY_MINUTES (never every 5 minutes).
        if [ $((now - STATE_CAP_NOTIFIED_TS)) -ge $((CAP_RENOTIFY_MINUTES * 60)) ]; then
          transition_kind="cap"
          STATE_CAP_NOTIFIED_TS="$now"
          # WRITE-THEN-ACT again: if the notification stamp cannot be recorded,
          # a page now would repeat on EVERY run (the failure mode this
          # throttle exists to prevent). Publish only once it is durable.
          if ! update_issue_body "$issue" "$(render_body "$kind" "$kindlabel" "$heal_note")"; then
            fail "could not record the cap notification in #${issue} — NOT escalating now (refusing to re-page every run because a write failed)"
            exit 1
          fi
          fail "restart velocity cap reached (${MAX_RESTARTS_PER_HOUR}/hour, ${STATE_DOWN_RUNS} failing runs) — escalating to a human"
          page "⛔ RESTART CAP REACHED — ${PROBE_HOST_LABEL} down ~$(( (now - STATE_FIRST_FAILURE_TS) / 60 )) min and ${MAX_RESTARTS_PER_HOUR} restart attempt(s) in the last hour did not fix it. A HUMAN must intervene. Incident #${issue}."
        else
          transition_kind="cap_silent"
          warn "restart velocity cap still in force — already escalated $(( (now - STATE_CAP_NOTIFIED_TS) / 60 )) min ago (re-notify every ${CAP_RENOTIFY_MINUTES} min)"
        fi
        ;;
    esac
  else
    case "$restart_mode" in
      disarmed:drill)
        heal_note="🔒 Self-healing is **disarmed for this run** because \`PROBE_URL\` is not the production endpoint (a drill must never restart production). Recovery is NOT resolved from a drill either."
        transition_kind="disarmed"
        ;;
      disarmed:unexpected)
        heal_note="⛔ **No restart attempted** — the app ANSWERED (an unexpected status, not silence), so a process restart is not the remediation. An unexpected \`404\`/\`3xx\` on an authenticated API route usually means a bad deploy or a moved route, not a wedged process: check the deployed revision and the route."
        transition_kind="disarmed"
        ;;
      disarmed:unfixable)
        # DNS and TLS/certificate failures (classify_failure) — the incident is
        # reported, the restart leg is disarmed, and the body says exactly why
        # so a human is not left wondering why self-healing did not fire.
        heal_note="⛔ **No restart attempted — the probe failed at a layer a machine restart cannot fix.** The last failing probe was $(failure_label "$PROBE_FAILURE_CLASS") against \`$(redact_url "$PROBE_URL")\`. Restarting the Fly machine would not repair name resolution or a TLS/certificate problem; it would only spend the automated-restart budget and add noise to an incident a human has to fix anyway. Check, in this order: the DNS record for the probe host, the certificate/SNI the app presents, then the platform proxy trace (\`flyctl logs -a ${FLY_APP}\`). Runbook § *Out-of-band availability watchdog*."
        transition_kind="disarmed"
        ;;
      disarmed:no_ledger)
        # The cross-incident restart budget is only as good as the previous
        # incident's ledger; without it we cannot prove this restart is inside
        # the hourly cap, so we fail closed (the incident is still reported).
        heal_note="⛔ **No restart attempted — the previous incident's restart ledger could not be read**, so the watchdog cannot prove another restart is inside the ${MAX_RESTARTS_PER_HOUR}/hour cap. It fails closed rather than risk a restart storm (an unbounded restart loop is what the cap exists to bound). Re-run once GitHub's search API responds, or inspect manually: \`flyctl machine list -a ${FLY_APP}\`, \`flyctl logs -a ${FLY_APP}\`. Runbook § *Out-of-band availability watchdog*."
        transition_kind="disarmed"
        ;;
      disarmed:no_egress)
        heal_note="⚠️ **No restart attempted — INCONCLUSIVE.** The app probe got no answer, but a control probe of a known-good endpoint (\`$(redact_url "$CONTROL_URL")\`) ALSO failed from this runner. A runner-side network/DNS/proxy failure looks exactly like an app outage, so the watchdog refuses to take the disruptive action (a restart) on an untrustworthy verdict. If the app is genuinely down, the alerting half of this monitor still fired and a human should check: \`flyctl machine list -a ${FLY_APP}\`. Runbook § *Out-of-band availability watchdog*."
        transition_kind="disarmed"
        # This is a HUMAN-ESCALATION page, so it is throttled like the cap: the
        # condition can persist for hours and paging every run would be the
        # notification-spam class this monitor exists to kill. cap_notified_ts
        # doubles as the "last human escalation" stamp (the two states are
        # mutually exclusive within a run) and is written BEFORE the page.
        if [ $((now - STATE_CAP_NOTIFIED_TS)) -ge $((CAP_RENOTIFY_MINUTES * 60)) ]; then
          STATE_CAP_NOTIFIED_TS="$now"
          if ! update_issue_body "$issue" "$(render_body "$kind" "$kindlabel" "$heal_note")"; then
            fail "could not record the inconclusive notification in #${issue} — NOT paging now (refusing to re-page every run because a write failed)"
            exit 1
          fi
          page "⚠️ INCONCLUSIVE — ${PROBE_HOST_LABEL} probe failing AND the runner-side control probe failed; the watchdog cannot tell an app outage from its own network. NO restart. Incident #${issue}."
        else
          warn "inconclusive (runner egress) — already escalated $(( (now - STATE_CAP_NOTIFIED_TS) / 60 )) min ago (re-notify every ${CAP_RENOTIFY_MINUTES} min)"
        fi
        ;;
    esac
  fi

  [ -n "$stale_note" ] && heal_note="${stale_note}

${heal_note}"
  # ── comment policy ───────────────────────────────────────────────────────
  # Decide WHAT to publish, then WRITE THE STATE FIRST and publish after: the
  # throttle stamp lives in the body, so a comment posted before the stamp is
  # recorded would repeat on every later run — the same
  # notification-without-a-durable-record bug the restart path guards against.
  local do_comment=0
  if [ "$progress" = "new" ]; then
    : # the freshly created issue body is the first record; nothing to add
  elif [ "$transition_kind" = "restart" ] || [ "$transition_kind" = "heal_failed" ] || [ "$transition_kind" = "cap" ]; then
    do_comment=1
    STATE_LAST_COMMENT_TS="$now"
  elif [ "$STATE_LAST_COMMENT_TS" -gt 0 ] \
       && [ $((now - STATE_LAST_COMMENT_TS)) -lt $((COMMENT_THROTTLE_MINUTES * 60)) ]; then
    log "run #${STATE_DOWN_RUNS}: comment throttled (last comment $(( (now - STATE_LAST_COMMENT_TS) / 60 )) min ago; throttle ${COMMENT_THROTTLE_MINUTES} min)"
  else
    do_comment=1
    STATE_LAST_COMMENT_TS="$now"
    comment_body="🔁 **Still ${kind}** — probe run #${STATE_DOWN_RUNS} failed at $(fmt_iso "$now") (HTTP ${PROBE_CODE}); down since $(fmt_iso "$STATE_FIRST_FAILURE_TS") (~$(( (now - STATE_FIRST_FAILURE_TS) / 60 )) min).

${heal_note}"
  fi

  if ! update_issue_body "$issue" "$(render_body "$kind" "$kindlabel" "$heal_note")"; then
    fail "state write to #${issue} failed — NOT publishing a comment without a durable throttle record; the incident body is the cooldown/cap memory and is now STALE (this run's verdict is still ${PROBE_VERDICT})"
    exit 1
  fi
  if [ "$do_comment" = "1" ]; then
    comment_issue "$issue" "$comment_body" || warn "comment on #${issue} failed (non-fatal: the body already carries the state)"
  fi

  fail "verdict ${PROBE_VERDICT} (HTTP ${PROBE_CODE}) — failing this run so GitHub's own notifications fire; incident: #${issue}"
  exit 1
}

if [ "${WATCHDOG_LIB_ONLY:-0}" != "1" ]; then
  main "$@"
fi
