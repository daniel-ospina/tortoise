#!/usr/bin/env bash
# ============================================================================
# availability-liveness.sh — INDEPENDENT dead-man switch for the availability
# pager (#4573). The checker half; the record half is emit_heartbeat() in
# .github/scripts/availability-watchdog.sh.
#
# THE GAP (#4573)
# ---------------
# availability-watchdog.sh (#3887) is fail-closed for a broken CHANNEL: an
# undelivered sustained-incident page is durable (escalate_state=failed) and the
# run goes red. It cannot see a dead MONITOR — a disabled workflow, a dropped
# schedule, or a crash before the down path means NO RUN AT ALL, so no
# escalation fires and the log is silent. "The pager is dead" reads exactly like
# "all clear" — the same fail-open class, one level up.
#
# THE CANONICAL FIX (see #4573's sources: promlabs end-to-end watchdog alerts,
# Grafana metamonitoring / Dead Man's Snitch, OneUptime, pingcap/dead-mans-switch)
# ------------------------------------------------------------------------------
# An always-firing heartbeat, checked by a service EXTERNAL to the monitored
# path. THIS script is that checker, run by a SEPARATE workflow
# (availability-liveness.yml) on its OWN schedule — so `gh workflow disable
# availability-watchdog` and a dropped watchdog schedule cannot silence it.
#
# ⛔ TWO LOAD-BEARING PROPERTIES, neither optional:
#
#   1. THE HEARTBEAT DOES NOT TRAVEL THE CHANNEL IT VERIFIES. The watchdog
#      writes its heartbeat into a rolling GitHub ISSUE BODY and pages humans
#      over TELEGRAM. This checker READS the issue body and ALARMS through a
#      GitHub issue plus a RED RUN (GitHub's own failure notifications) — it
#      never calls Telegram, and it does not read the incident issue the
#      Telegram leg is keyed on. A dead Telegram bot cannot silence this check.
#      RESIDUAL (stated in the runbook, not hidden): an outage of GitHub
#      Actions itself takes both halves out. Closing that needs an endpoint
#      external to GitHub (Dead Man's Snitch / OneUptime) — an external account
#      and an owner decision, deliberately out of this scope.
#
#   2. THE CADENCE IS MEASURED, NOT INTENDED. The watchdog's cron *intent* is
#      5 min; its MEASURED delivery is ~96 runs/day. Re-measured for this issue
#      over the whole live population (the watchdog workflow was created
#      2026-09-12T22:15 CDT, 2026-09-13T03:15Z) through
#      2026-09-27T08:55:32Z — 14.22 days, n=1377 scheduled runs:
#
#          runs/day = 96.8      mean inter-arrival = 14.88 min
#          median 11.9 · p90 23.9 · p95 28.6 · p99 53.8 · max 63.5 min
#          gaps > 45 min: 15    gaps > 60 min: 4
#
#      ⛔ THE MEAN IS NOT THE THRESHOLD BASIS. `3 x 15 = 45 min` would have
#      FALSE-FIRED 15 times in that 14.2-day window — GitHub drops and delays
#      scheduled runs under load, and a pager that cries wolf is the defect
#      restated. The default threshold is therefore 3 x the measured p95
#      inter-arrival (3 x 28.6 = 85.8, rounded UP to 90 min), which clears the
#      observed max (63.5 min) with ~40% headroom. Re-measure before changing
#      it; the reason it is not `3 x mean` is in this block, not in a comment
#      somewhere else.
#
# IN-REPO PRECEDENT (followed, not reinvented)
# --------------------------------------------
# registry-cron.sh already implements a watcher-down heartbeat: it reads
# `.watcher.age_minutes` from the status payload, and when the watcher is dead
# (`running != true` or `age > 30`) it files a `WATCHER_DOWN` GitHub alert and
# fails the run. Same shape here: a measured staleness bound, an independent
# reader, a deduped alert issue, and a red run as the second delivery.
# DELIBERATE DIVERGENCE, stated so a later reader does not "unify" them: the
# WATCHER_DOWN reader and the restart leg share the app's /status payload, while
# this heartbeat lives on a channel deliberately SEPARATE from the Telegram page
# — because the thing being verified IS the pager.
#
# EXIT CONTRACT: 0 = fresh (or not yet established); 1 = STALE or the checker
# itself could not assess — both are loud, and a non-zero exit fails the run.
#
# Harness: .github/scripts/availability-liveness.test.sh (stubs gh, no network).

set -euo pipefail

REPO="${GITHUB_REPOSITORY:-daniel-ospina/tortoise}"
GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
ALERT_LABEL="${ALERT_LABEL:-auto-filed}"

# ⛔ PARITY: these two constants MUST match availability-watchdog.sh verbatim or
# the search never matches the record (and the checker alarms forever). The
# harness asserts the parity by reading both files, so a rename on one side
# cannot land green.
HEARTBEAT_MARKER='<!-- availability-watchdog-heartbeat -->'
HEARTBEAT_TITLE='[OPS] availability-watchdog heartbeat (rolling)'

LIVENESS_ALERT_MARKER='<!-- availability-liveness-alert -->'
LIVENESS_ALERT_TITLE='[OPS] availability-watchdog LIVENESS — no heartbeat'

# The "nothing to verify yet" grace is keyed to THIS feature's own rollout, NOT
# to the watchdog workflow's age. The watchdog workflow was created
# 2026-09-12T22:15 CDT (2026-09-13T03:15Z), so keying the grace on it would alarm
# from the moment this checker first ships until the first heartbeat lands — a
# false page produced by the very
# rollout meant to prevent false pages. The liveness workflow is created in the
# same change that adds the heartbeat, so ITS age is the feature's age. Metadata
# only; an unreadable value fails closed.
LIVENESS_WORKFLOW="${LIVENESS_WORKFLOW:-availability-liveness.yml}"
# MEASURED threshold (see the header). 90 = 3 x measured p95 (28.6), rounded up.
# The env override is UNTRUSTED and validated by normalize_max_age (below); this
# literal is only the default it falls back to.
HEARTBEAT_MAX_AGE_MIN_DEFAULT=90
HEARTBEAT_MAX_AGE_MIN="${HEARTBEAT_MAX_AGE_MIN:-}"
# Test seam: pin "now" so age arithmetic is deterministic.
LIVENESS_NOW_EPOCH="${LIVENESS_NOW_EPOCH:-}"

# ── logging (all to STDERR; stdout is DATA only) ─────────────────────────────
log() { echo "$*" >&2; }
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
  # The clock is an ARITHMETIC operand too, so a seam pin is treated like every
  # other epoch: DECIMAL and BOUNDED. bash's `$(( ))` reads a leading-zero value
  # as OCTAL — a SILENTLY WRONG number when every digit is 0-7, an abort when a
  # digit is 8/9 — and an over-intmax value wraps. An unusable seam is NOT a
  # clock: fall back to the real one rather than return "" or 0, either of which
  # would make the age NEGATIVE and read as LIVE, a fail-open on this checker's
  # own fail-closed surface.
  local v
  if [ -n "$LIVENESS_NOW_EPOCH" ]; then
    case "$LIVENESS_NOW_EPOCH" in
      *[!0-9]*)
        warn "LIVENESS_NOW_EPOCH='[${LIVENESS_NOW_EPOCH}]' is not a usable epoch — ignoring the pin"
        ;;
      *)
        v="$(dec_strip_zeros "$LIVENESS_NOW_EPOCH")"
        if [ -n "$v" ] && [ "${#v}" -le 12 ]; then
          printf '%s' "$v"; return 0
        fi
        warn "LIVENESS_NOW_EPOCH='[${LIVENESS_NOW_EPOCH}]' is not a usable epoch — ignoring the pin"
        ;;
    esac
  fi
  date -u +%s
}

# epoch -> ISO-8601 Z (GNU date first, BSD fallback — the harness runs on both).
fmt_iso() {
  date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null && return 0
  date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null && return 0
  printf 'epoch:%s' "$1"
}

# ISO-8601 (or fmt_iso's `epoch:<n>` fallback) -> epoch, or "" when unparseable.
# A "" is the fail-closed direction: an unreadable/`unparseable` heartbeat is
# NOT a fresh one.
#
# ⚠️ The Actions API emits `2026-09-12T22:15:01.000-05:00` — fractional seconds
# AND a numeric offset — and BSD `date -j -f` matches such a format as a PREFIX
# and silently DISCARDS the rest. Measured: that string -> 1789251301, i.e. the
# wall time read as UTC, **5 h early**; the only signal is a warning on stderr
# that `2>/dev/null` drops, so the result is a WRONG-BUT-PLAUSIBLE epoch — which
# the "" fail-closed contract cannot catch. The offset is therefore normalised
# HERE, arithmetically, so both `date` implementations agree; an unrecognised
# shape fails closed rather than being prefix-matched into a plausible instant.
iso_to_epoch() { # <iso|epoch:n> -> epoch or ""
  local iso="$1" e off=0 tail sign hh mm
  [ -n "$iso" ] || { printf ''; return 0; }
  case "$iso" in
    epoch:*)
      # A bounded digit run only: bash's `[ -gt ]`/`$(( ))` on an over-intmax
      # value errors (condition reads FALSE) and then WRAPS, which would defeat
      # the future-clamp and let a negative age read as fresh. An unbounded run
      # is therefore unusable, not trusted.
      e="${iso#epoch:}"
      case "$e" in ''|*[!0-9]*) printf ''; return 0 ;; esac
      [ "${#e}" -le 12 ] || { printf ''; return 0; }
      printf '%s' "$e"; return 0 ;;
  esac

  case "$iso" in
    *[+-][0-9][0-9]:[0-9][0-9])
      tail="${iso%??????}"
      off="${iso#"$tail"}"
      sign="${off%"${off#?}"}"
      hh="${off#?}"; hh="${hh%%:*}"
      mm="${off##*:}"
      hh="$(printf '%s' "$hh" | sed 's/^0*//')"; [ -n "$hh" ] || hh=0
      mm="$(printf '%s' "$mm" | sed 's/^0*//')"; [ -n "$mm" ] || mm=0
      off=$(( hh * 3600 + mm * 60 ))
      [ "$sign" = "-" ] && off=$(( 0 - off ))
      iso="$tail" ;;
    *Z) iso="${iso%Z}" ;;
  esac

  # Drop fractional seconds (`…:01.000`), then require EXACTLY the wall-clock
  # shape. A shape that reaches neither branch above is not something the API
  # emits, and BSD `date` would prefix-match it — so refuse it.
  case "$iso" in *.*) iso="${iso%%.*}" ;; esac
  case "$iso" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]) : ;;
    *) printf ''; return 0 ;;
  esac

  # GNU date first (the production runner), BSD second (the harness). The `Z`
  # makes GNU's reading explicitly UTC, matching BSD's `-u`.
  e="$(date -u -d "${iso}Z" +%s 2>/dev/null || true)"
  if [ -z "$e" ]; then
    e="$(date -u -j -f "%Y-%m-%dT%H:%M:%S" "$iso" +%s 2>/dev/null || true)"
  fi
  case "$e" in ''|*[!0-9]*) printf ''; return 0 ;; esac
  e=$(( e - off ))
  printf '%s' "$e"
}

# The staleness bound is an operator override from a repository variable, so it
# is UNTRUSTED. Digit-stripping is NOT validation: `2h` -> 2, `1.5h` -> 15,
# `-1` -> 1, `0.5` -> 5 — a near-zero bound alerts on nearly every run, i.e. the
# false-firing pager this whole issue exists to avoid. Accept ONLY a plain
# integer; anything else warns and falls back to the measured default. A valid
# integer below the measured p95 is honoured (an explicit operator choice) but
# warned, because it sits inside GitHub's own scheduling jitter.
normalize_max_age() { # <raw> -> minutes
  local raw="${1:-}"
  if [ -z "$raw" ]; then printf '%s' "$HEARTBEAT_MAX_AGE_MIN_DEFAULT"; return 0; fi
  case "$raw" in
    *[!0-9]*)
      warn "HEARTBEAT_MAX_AGE_MIN='[${raw}]' is not a plain integer — using the measured default ${HEARTBEAT_MAX_AGE_MIN_DEFAULT} min (a near-zero bound would false-fire)"
      printf '%s' "$HEARTBEAT_MAX_AGE_MIN_DEFAULT"; return 0 ;;
  esac
  # ⛔ BOUND THE MAGNITUDE BEFORE ANY ARITHMETIC. bash's `[ -gt ]` on an integer
  # beyond int64 prints "integer expression expected" and exits 2, which a
  # CONDITION reads as FALSE — so an over-intmax value would slip past the
  # 1..100000 range guard (and past the p95 warn below, and past the staleness
  # compare in main) and silently MUTE the checker: every heartbeat reads LIVE.
  # Reject on DIGIT COUNT, not on a numeric compare.
  if [ "${#raw}" -gt 6 ]; then
    warn "HEARTBEAT_MAX_AGE_MIN='[${raw}]' has more than 6 digits — using the measured default ${HEARTBEAT_MAX_AGE_MIN_DEFAULT} min (an int-overflowing value must not disable the comparison)"
    printf '%s' "$HEARTBEAT_MAX_AGE_MIN_DEFAULT"; return 0
  fi
  raw="$(printf '%s' "$raw" | sed 's/^0*//')"
  [ -n "$raw" ] || raw=0
  if [ "$raw" -lt 1 ] || [ "$raw" -gt 100000 ]; then
    warn "HEARTBEAT_MAX_AGE_MIN=${raw} is outside 1..100000 — using the measured default ${HEARTBEAT_MAX_AGE_MIN_DEFAULT} min"
    printf '%s' "$HEARTBEAT_MAX_AGE_MIN_DEFAULT"; return 0
  fi
  if [ "$raw" -lt 29 ]; then
    warn "HEARTBEAT_MAX_AGE_MIN=${raw} is below the measured p95 inter-arrival (~29 min) — this WILL false-fire on GitHub's scheduling jitter (measured max 63.5 min); the measured default is ${HEARTBEAT_MAX_AGE_MIN_DEFAULT}"
  fi
  printf '%s' "$raw"
}

urlencode() { printf '%s' "$1" | jq -sRr @uri; }

# ⛔ STRIP LEADING ZEROS before any arithmetic. `00`, `007` … are all-digit and
# within every length bound, but bash's `$(( ))` reads a LEADING-ZERO operand as
# OCTAL: `$(( (NOW - 000000000009) / 60 ))` aborts with "value too great for base",
# killing the run BEFORE the alert is filed — the record reaches neither the STALE
# path nor the durable alert. (The single-bracket `[ -gt ]` is NOT the problem: it
# parses base-10 and returns TRUE for `000000000009` — but relying on that would
# leave the arithmetic to abort, so the value is normalized at the extraction.
# The threshold validator already normalizes this way; the epoch paths must too.)
# An all-zero input collapses to "", which each caller handles fail-closed: the
# heartbeat caller treats it as unparseable (STALE), the bootstrap caller as an
# unreadable feature age (also STALE), and `now_epoch` rejects it as an unusable
# pin and falls back to the real clock.
dec_strip_zeros() { printf '%s' "${1:-}" | sed 's/^0*//'; }

# Publication-boundary scrub, mirroring the watchdog's redact_text. This script
# holds no probe URL, Fly token or Telegram token, so only the SHAPE pass can
# matter today — but the boundary is where the watchdog's own invariant lives
# ("redact at the last point before publication"), so a future caller cannot
# reintroduce a leak by putting caller-supplied text into an alert.
redact_text() {
  printf '%s' "$1" | sed -E \
    -e 's#([?&](token|key|secret|sig|signature|api_key|apikey|access_token)=)[^&"[:space:]]*#\1<redacted>#g' \
    -e 's#FlyV1[[:space:]]+[A-Za-z0-9_=+/.,-]+#<redacted>#g' \
    -e 's#fm2_[A-Za-z0-9_=+/.,-]{20,}#<redacted>#g' \
    -e 's#[0-9]{6,12}:[A-Za-z0-9_-]{30,}#<redacted>#g'
}

# ── GitHub issue helpers ────────────────────────────────────────────────────
# Echoes a positive issue number, "" when none is open, or "__ERR__" when the
# search failed or answered something unparseable. SECURITY: this is a PUBLIC
# repo, so "an open issue whose title matches" is NOT ours. Any account can open
# an issue with our title and a FORGED heartbeat body; adopting it would let a
# stranger claim the pager is alive (a fail-OPEN mute of the liveness check).
# So the search carries `author:app/github-actions` AND each item is re-checked
# against the reserved `github-actions[bot]` login, an EXACT title and the
# body-only marker.
search_issue() { # <body-marker> <exact-title>
  local q enc out n
  # ⛔ The QUERY term must be the TITLE, not the body marker: `in:title` searches
  # titles, and the body marker is an HTML comment that never appears in one —
  # using it made the search return zero results every time, so the checker
  # would re-file a duplicate alert on every stale run instead of finding the
  # open one. The marker still gates adoption in the jq below (a same-titled
  # issue from another producer is never adopted).
  q="repo:${REPO} is:issue is:open in:title author:app/github-actions \"$2\""
  enc="$(urlencode "$q")"
  if ! out="$(gh api "search/issues?q=${enc}&per_page=100" --paginate 2>/dev/null)"; then
    printf '__ERR__'; return 0
  fi
  if ! n="$(printf '%s' "$out" | jq -rs --arg login 'github-actions[bot]' --arg title "$2" --arg marker "$1" \
      '[.[].items[]? | select((.user.login // "") == $login) | select((.title // "") == $title) | select(((.body // "") | contains($marker)))][0].number // empty' 2>/dev/null)"; then
    printf '__ERR__'; return 0
  fi
  # EMPTY = "not found" (a real answer: the caller may create one or fall back
  # to the bootstrap path). Only a NON-EMPTY non-numeric value is ERR. Collapsing
  # the two would make "no heartbeat issue yet" read as "the search is broken".
  [ -n "$n" ] || { printf ''; return 0; }
  case "$n" in
    *[!0-9]*) printf '__ERR__'; return 0 ;;
  esac
  printf '%s' "$n"
}

get_issue_body() { # <n> -> the body, or __ERR__ when the read failed
  local out body
  if ! out="$(gh api "repos/${REPO}/issues/$1" 2>/dev/null)"; then
    printf '__ERR__'; return 0
  fi
  if ! body="$(printf '%s' "$out" | jq -r '.body // ""' 2>/dev/null)"; then
    printf '__ERR__'; return 0
  fi
  printf '%s' "$body"
}

create_issue() { # <title> <body> -> number ("" on failure)
  local payload out n
  # redact_text AT THE BOUNDARY: the last point before publication, so no
  # future caller can leak a credential by forgetting to scrub.
  payload="$(jq -n --arg t "$(redact_text "$1")" --arg b "$(redact_text "$2")" --arg l "$ALERT_LABEL" \
    '{title:$t, body:$b, labels:[$l]}')"
  if ! out="$(printf '%s' "$payload" | gh api "repos/${REPO}/issues" --method POST --input - 2>/dev/null)"; then
    printf ''; return 0
  fi
  n="$(printf '%s' "$out" | jq -r '.number // empty' 2>/dev/null || true)"
  case "$n" in ''|*[!0-9]*) printf ''; return 0 ;; esac
  printf '%s' "$n"
}

update_issue_body() { # <n> <body> -> 0 ok / 1 failed
  local payload
  payload="$(jq -n --arg b "$(redact_text "$2")" '{body:$b}')"
  if ! printf '%s' "$payload" | gh api "repos/${REPO}/issues/$1" --method PATCH --input - >/dev/null 2>&1; then
    warn "issue body update failed for #$1"
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

# The liveness workflow's own created_at (ISO) or "" when it cannot be read.
# Used ONLY to keep this checker's own rollout from alerting before the first
# heartbeat can land (see LIVENESS_WORKFLOW above); an unreadable value is NOT
# treated as young.
workflow_created_at() {
  local out
  if ! out="$(gh api "repos/${REPO}/actions/workflows/${LIVENESS_WORKFLOW}" 2>/dev/null)"; then
    printf ''; return 0
  fi
  printf '%s' "$out" | jq -r '.created_at // ""' 2>/dev/null || printf ''
}

# ── the alert body ──────────────────────────────────────────────────────────
# Kept in ONE place so the filed body and the deduped update can never drift.
liveness_body() { # <reason> <age-clause>
  cat <<BODY
${LIVENESS_ALERT_MARKER}
liveness_state=stale
reason=$1
${2}
threshold_min=${HEARTBEAT_MAX_AGE_MIN}
checked_at=$(fmt_iso "$NOW")

⛔ **The availability pager's own liveness check is failing.** No heartbeat has been
recorded by \`availability-watchdog\` within ${HEARTBEAT_MAX_AGE_MIN} minutes, or its record is
unreadable. This is the "dead monitor" case: a disabled workflow, a dropped schedule,
or a crash before the down path produces **no escalation and no run log** — so up to
now, "the pager is dead" read exactly like "all clear".

**This alert is deliberately not sent over Telegram.** The Telegram leg is the thing
under suspicion; the heartbeat and this alert therefore travel a different channel
(this GitHub issue plus the failed run). A working Telegram bot is NOT evidence that
the pager is alive.

**Check, in this order:**
1. \`gh workflow list --all\` → is \`availability-watchdog\` \`active\`? A disabled schedule
   produces no runs and no heartbeat (\`gh workflow enable availability-watchdog\`).
2. \`gh run list --workflow availability-watchdog.yml --limit 20\` → are scheduled runs
   arriving? A gap here with an \`active\` workflow is GitHub dropping the schedule.
3. The rolling heartbeat issue \`${HEARTBEAT_TITLE}\` → does its \`heartbeat_at=\` field move?
4. If runs ARE arriving but the heartbeat is not, read the last run's log for the
   \`heartbeat:\` lines — the record is best-effort, and the liveness check is
   fail-closed on the read side, so a heartbeat that stops arriving IS the alarm.

**Measured cadence, not cron intent:** the 5-minute cron delivers ~96 runs/day
(mean inter-arrival ~15 min). But the measured p95 gap is ~28.6 min and the measured
**max is ~63.5 min** (GitHub drops/delays scheduled runs), so \`3 × mean = 45 min\` would
false-fire ~15 times per 14 days. The threshold is ${HEARTBEAT_MAX_AGE_MIN} min =
3 × the measured p95. Runbook: docs/infra-runbook.md → § *Out-of-band availability watchdog*.
BODY
}

# ── main ────────────────────────────────────────────────────────────────────
main() {
  local hb_issue body hb_epoch age_min reason age_clause wf_created wf_epoch wf_age alert state

  # Fail closed: a checker that cannot alert is a deaf checker.
  if [ -z "$GH_TOKEN" ]; then
    fail "GH_TOKEN is not set — refusing to run a liveness checker that cannot file its alert (a deaf checker is worse than no checker)"
    exit 1
  fi
  if ! command -v jq >/dev/null 2>&1; then
    fail "jq is required"
    exit 1
  fi
  HEARTBEAT_MAX_AGE_MIN="$(normalize_max_age "$HEARTBEAT_MAX_AGE_MIN")"
  NOW="$(now_epoch)"

  log "availability-liveness (#4573) — checker for ${REPO}; threshold=${HEARTBEAT_MAX_AGE_MIN} min"
  state="fresh"; reason=""; age_clause=""; age_min=""

  hb_issue="$(search_issue "$HEARTBEAT_MARKER" "$HEARTBEAT_TITLE")"
  case "$hb_issue" in
    __ERR__)
      # "cannot read the heartbeat" is NOT "the pager is dead", and it is not
      # "all clear" either. Do not file a liveness-alert issue on a transient
      # search failure (that would be a false page); fail the run loudly so the
      # checker's own blindness is visible and retried next run.
      fail "heartbeat search failed — cannot assess the pager's liveness this run (not treated as fresh; retried on the next run)"
      exit 1 ;;
  esac

  if [ -n "$hb_issue" ]; then
    body="$(get_issue_body "$hb_issue")"
    if [ "$body" = "__ERR__" ]; then
      state="stale"; reason="heartbeat-record-unreadable"
      age_clause="heartbeat_issue=#${hb_issue}
heartbeat_age_min=unknown"
      warn "heartbeat record #${hb_issue} could not be read — treating as STALE (an unreadable heartbeat is not a fresh one)"
    else
      hb_epoch=""
      # Prefer the epoch field (no date parsing), fall back to the ISO field.
      # The digit run is BOUNDED: an over-intmax epoch would error the future
      # clamp (condition false) and then wrap in $(( )) to a negative age, which
      # reads as fresh — a fail-open on the fail-closed surface this check is.
      local ep_raw
      ep_raw="$(printf '%s' "$body" | sed -n 's/^heartbeat_epoch=\([0-9]\{1,12\}\)$/\1/p' | head -n1)"
      if [ -n "$ep_raw" ]; then
        hb_epoch="$ep_raw"
      else
        local iso_raw
        iso_raw="$(printf '%s' "$body" | sed -n 's/^heartbeat_at=\(.*\)$/\1/p' | head -n1)"
        hb_epoch="$(iso_to_epoch "$iso_raw")"
      fi
      if [ -n "$hb_epoch" ]; then hb_epoch="$(dec_strip_zeros "$hb_epoch")"; fi
      if [ -z "$hb_epoch" ]; then
        state="stale"; reason="heartbeat-record-unparseable"
        age_clause="heartbeat_issue=#${hb_issue}
heartbeat_age_min=unknown"
        warn "heartbeat record #${hb_issue} carries no parseable heartbeat time — treating as STALE"
      else
        # A future stamp (runner clock skew) is clamped to now: it is not
        # evidence of staleness, and the next scheduled run re-stamps it.
        [ "$hb_epoch" -gt "$NOW" ] && hb_epoch="$NOW"
        age_min=$(( (NOW - hb_epoch) / 60 ))
        age_clause="heartbeat_issue=#${hb_issue}
heartbeat_at=$(fmt_iso "$hb_epoch")
heartbeat_age_min=${age_min}"
        if [ "$age_min" -gt "$HEARTBEAT_MAX_AGE_MIN" ]; then
          state="stale"; reason="heartbeat-too-old"
          warn "heartbeat is ${age_min} min old (threshold ${HEARTBEAT_MAX_AGE_MIN} min) — PAGER LIVENESS FAILING"
        else
          log "heartbeat is ${age_min} min old (threshold ${HEARTBEAT_MAX_AGE_MIN} min) — pager is LIVE"
        fi
      fi
    fi
  else
    # No heartbeat record at all. Distinguish a fresh rollout (nothing to
    # verify yet) from a monitor that should have written one: read the LIVENESS
    # workflow's own created_at — the feature's age, not the watchdog workflow's
    # (which predates this feature and would make the first run alarm). An
    # unreadable value fails closed (assume it should have run).
    wf_created="$(workflow_created_at)"
    wf_epoch="$(dec_strip_zeros "$(iso_to_epoch "$wf_created")")"
    if [ -n "$wf_epoch" ] && [ "$wf_epoch" -gt "$NOW" ]; then wf_epoch="$NOW"; fi
    if [ -n "$wf_epoch" ]; then
      wf_age=$(( (NOW - wf_epoch) / 60 ))
      age_clause="heartbeat_issue=none
liveness_workflow_age_min=${wf_age}"
      if [ "$wf_age" -le "$HEARTBEAT_MAX_AGE_MIN" ]; then
        log "no heartbeat record yet and this liveness feature is only ${wf_age} min old — not yet established; not alerting"
        state="fresh"; reason="not-yet-established"
      else
        state="stale"; reason="no-heartbeat-record"
        warn "no heartbeat record exists and this liveness feature is ${wf_age} min old — the pager may never have run"
      fi
    else
      state="stale"; reason="no-heartbeat-record"
      age_clause="heartbeat_issue=none
liveness_workflow_age_min=unknown"
      warn "no heartbeat record exists and the liveness workflow's age could not be read — treating as STALE (fail closed)"
    fi
  fi

  alert="$(search_issue "$LIVENESS_ALERT_MARKER" "$LIVENESS_ALERT_TITLE")"
  case "$alert" in
    __ERR__)
      fail "liveness-alert search failed — refusing to file or resolve (never duplicate); cannot confirm the alert state"
      exit 1 ;;
  esac

  if [ "$state" = "stale" ]; then
    local new_body
    new_body="$(liveness_body "$reason" "$age_clause")"
    if [ -z "$alert" ]; then
      if ! alert="$(create_issue "$LIVENESS_ALERT_TITLE" "$new_body")" || [ -z "$alert" ]; then
        fail "LIVENESS STALE (${reason}) and the alert issue could not be filed — this failing run IS the alert"
        exit 1
      fi
      note "filed liveness alert #${alert} (${reason})"
    else
      # Dedupe: ONE alert issue. The body carries the current age/reason; a
      # body write is not a comment, so a long failure does not spam watchers.
      if ! update_issue_body "$alert" "$new_body"; then
        fail "liveness is STALE (${reason}) but the alert body on #${alert} could not be updated — this failing run IS the alert"
        exit 1
      fi
      note "liveness alert #${alert} is still STALE (${reason}) — body refreshed"
    fi
    fail "PAGER LIVENESS FAILING (${reason}) — the availability watchdog has not recorded a heartbeat within ${HEARTBEAT_MAX_AGE_MIN} min. See #${alert}. Alerted over GitHub (issue + failing run), NOT Telegram, because the Telegram leg is what is under suspicion."
    exit 1
  fi

  # Fresh. Resolve a standing alert if one is open — a stale OPEN alert is its
  # own false alarm. Close BEFORE commenting, so a failing close cannot re-post
  # "Recovered" on every run.
  if [ -n "$alert" ]; then
    if ! close_issue "$alert"; then
      fail "liveness recovered but the alert issue #${alert} could not be closed — failing the run so the stale alert is not silent"
      exit 1
    fi
    if ! comment_issue "$alert" "✅ **Recovered** — a heartbeat has been recorded within ${HEARTBEAT_MAX_AGE_MIN} min at $(fmt_iso "$NOW") (${age_clause//$'\n'/'; '}). The pager is live again; this alert is closed and re-files automatically if the heartbeat goes stale."; then
      fail "#${alert} was CLOSED but the 'Recovered' comment failed — the state is correct, the record is missing; failing the run so it is not silent"
      exit 1
    fi
    note "closed liveness alert #${alert} (recovered)"
  fi
  note "pager liveness OK — heartbeat within ${HEARTBEAT_MAX_AGE_MIN} min"
  exit 0
}

if [ "${LIVENESS_LIB_ONLY:-0}" != "1" ]; then
  main "$@"
fi
