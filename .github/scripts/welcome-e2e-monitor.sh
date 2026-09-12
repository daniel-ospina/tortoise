#!/usr/bin/env bash
# ============================================================================
# welcome-e2e-monitor.sh — the AUTO-FILE half of the live signup-funnel
# monitor (#801 / #2706 / #2850).
#
# WHY THIS IS A SCRIPT AND NOT INLINE WORKFLOW SHELL
#   The workflow used to carry this logic inline, which made it untestable — and
#   the #3064 round-2 review found exactly the kind of bug a test would have
#   caught: the dedupe search was TITLE-ONLY. This repo is PUBLIC, so any
#   external account can open an issue whose title matches ours and have the
#   monitor treat it as its own state carrier (comment on it, never file its
#   own tracker). That is the same hijack class the availability watchdog
#   hardened one file over (`.github/scripts/availability-watchdog.sh`), and it
#   gets the same two-part guard here:
#     1. the search carries `author:app/github-actions`, AND
#     2. the returned item's `user.type` / `user.login` is RE-CHECKED before its
#        number is used.
#   A title match that is NOT machine-authored is never commented on: it is
#   treated as "no incident" and a fresh machine issue is filed. The lookup is
#   driven by `.github/scripts/welcome-e2e-monitor.test.sh` (forged-title case
#   included).
#
# BEHAVIOUR (unchanged from the inline version)
#   ONE issue per failing streak (#2706), never one per run: the old title
#   embedded `run ${github.run_id}`, so every failing run filed a NEW issue
#   (59 open near-identical duplicates at the 2026-09-11 census, #2231 → #3014).
#   The title is now STABLE and acts as the dedupe key: search for an open
#   MACHINE-authored issue with it and COMMENT (with the run link) instead of
#   filing another. FORWARD-LOOKING ONLY: the pre-existing duplicates carry the
#   OLD title and are deliberately NOT adopted (adopting one at random would be
#   worse than one clean tracker) — they need a one-time bulk cleanup (#3014).
#   A failed search REFUSES to file (never duplicate) and exits 1, so the
#   monitor can never go silently deaf (#2140 class).
#
# Env:  GH_TOKEN (required), GITHUB_REPOSITORY / GITHUB_SERVER_URL /
#       GITHUB_RUN_ID (set by Actions on every step).
# Test: bash .github/scripts/welcome-e2e-monitor.test.sh
# ============================================================================

set -euo pipefail

REPO="${GITHUB_REPOSITORY:-daniel-ospina/tortoise}"
RUN_URL="${GITHUB_SERVER_URL:-https://github.com}/${REPO}/actions/runs/${GITHUB_RUN_ID:-0}"
TITLE="${WELCOME_MONITOR_TITLE:-live-signup-monitor: welcome-e2e failing}"
ALERT_LABEL="${WELCOME_MONITOR_LABEL:-bug}"

# The only author whose issue this monitor may adopt, comment on or PATCH
# machine state into. GitHub reserves the `[bot]` suffix and the
# `github-actions` app name, so this is not spoofable by a normal account.
BOT_LOGIN="github-actions[bot]"

err()  { echo "::error::$*" >&2; }
note() { echo "::notice::$*"; }
warn() { echo "::warning::$*" >&2; }

# Echoes a POSITIVE integer issue number, "" when NO machine-authored issue is
# open, or "__ERR__" when the search failed or answered something unparseable.
# ERR must NEVER be treated as "none" — that is the duplicate-spam direction.
find_own_open_issue() { # <title>
  local q enc out n total
  # TITLE-ONLY dedupe key (the label is not filtered on: a renamed/deleted label
  # would silently empty the search and turn the monitor back into a
  # duplicate-issue spammer). The author qualifier is the load-bearing security
  # constraint, and it is only the FIRST half of the guard — `user.type` /
  # `user.login` are re-checked on the returned item below.
  q="repo:${REPO} is:issue is:open in:title author:app/github-actions \"$1\""
  enc="$(printf '%s' "$q" | jq -sRr @uri)"
  # NB: the query MUST go in the URL path — `gh api -f q=…` switches the method
  # to POST and 404s on this endpoint (tenant-provision-monitor, #1133).
  if ! out="$(gh api "search/issues?q=${enc}&per_page=5")"; then
    err "issue search failed — refusing to file a possible duplicate; this failing run IS the alert"
    printf '__ERR__'
    return 0
  fi
  # An empty item list ("no incident") is NOT a failure — only an unparseable
  # answer is. Conflating the two makes the monitor refuse to file on the very
  # first outage.
  if ! n="$(printf '%s' "$out" | jq -r '[.items[]? | select((.user.type // "") == "Bot" or (.user.login // "") == "'"$BOT_LOGIN"'")][0].number // empty' 2>/dev/null)"; then
    err "issue search returned an unparseable body"
    printf '__ERR__'
    return 0
  fi
  if [ -z "$n" ]; then
    # Distinguish "nothing matched" from "something matched but was NOT ours":
    # the latter is the forged-title hijack this guard exists for, and it is
    # worth a loud line (we still file a fresh machine issue).
    total="$(printf '%s' "$out" | jq -r '.items | length' 2>/dev/null || true)"
    case "$total" in
      ''|*[!0-9]*) total=0 ;;
    esac
    if [ "$total" -gt 0 ]; then
      warn "issue search matched ${total} open issue(s) but NONE was authored by ${BOT_LOGIN} — ignoring the look-alike(s) (a forged-title issue is never adopted) and filing a fresh machine issue"
    fi
    printf ''
    return 0
  fi
  case "$n" in
    *[!0-9]*|0) printf '__ERR__'; return 0 ;;
  esac
  printf '%s' "$n"
}

body_text() { # <run_url>
  cat <<EOF
Monitor failure — run: $1

This is the #801 live signup funnel monitor (schedule + on-merge). The smoke asserts POST /v1/signup/email → 200 and the auto sign-in (auth/v1/token?grant_type=password) → 200; failures are typically a real signup-funnel regression, an API 429 (over_email_send_rate_limit / over_request_rate_limit / over_request_rate_limit_ip), or a stale test vs the deployed page. Investigate or re-run; it is NOT a PR regression gate.

**One issue per failing streak (#2706):** later failures comment here instead of filing a new issue. The body is not machine-managed; close this issue when the streak ends.
EOF
}

main() {
  if [ -z "${GH_TOKEN:-}" ]; then
    err "GH_TOKEN is not set — refusing to run a monitor that cannot file or comment (a deaf monitor is worse than no monitor)"
    exit 1
  fi
  if ! command -v jq >/dev/null 2>&1; then
    err "jq is required"
    exit 1
  fi

  local found
  found="$(find_own_open_issue "$TITLE")"
  case "$found" in
    __ERR__)
      err "issue search failed — refusing to file a possible duplicate; this failing run IS the alert"
      exit 1 ;;
    *[!0-9]*)
      err "issue search returned a non-numeric issue id ('${found}') — refusing to comment on a garbage id"
      exit 1 ;;
  esac

  if [ -n "$found" ]; then
    # `gh api` exits non-zero on an HTTP 4xx/5xx, so a failed comment fails the
    # step loudly (the #2140 deaf-monitor class: a bare `curl -s` would exit 0 on
    # an HTTP error).
    gh api "repos/${REPO}/issues/${found}/comments" \
      --method POST -f body="🔁 Still failing — run: ${RUN_URL}" >/dev/null
    note "monitor issue #${found} is already open and MACHINE-authored — commented instead of filing a duplicate (#2706)"
  else
    gh api "repos/${REPO}/issues" --method POST \
      -f title="${TITLE}" -f body="$(body_text "$RUN_URL")" \
      -f "labels[]=${ALERT_LABEL}" >/dev/null
    note "no open machine-authored monitor issue — filed one (further failures will comment on it)"
  fi
}

if [ "${WELCOME_MONITOR_LIB_ONLY:-0}" != "1" ]; then
  main "$@"
fi
