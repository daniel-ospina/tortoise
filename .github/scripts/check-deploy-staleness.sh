#!/usr/bin/env bash
# ============================================================================
# check-deploy-staleness.sh — is the DEPLOYED hosted API running origin/main?
# The #10109 staleness detector: reads the deployment's OWN revision and says,
# positively, how far behind `origin/main` it is.
#
# THE INVARIANT (#10109) — one sentence:
#   The commit production is running MUST be an ancestor of `origin/main`, and
#   the distance must be STATED — `deployed=<sha> main=<sha> behind=<n>`.
#
# WHY THIS EXISTS
# ---------------
# 2026-10-26: production ran a commit 7 commits behind `origin/main` for ~9
# hours while every readiness signal stayed green. `deploy-hosted` was red on
# every run — 4 cancelled by `cancel-in-progress: true` as later merges landed,
# and a migration-drift gate failing closed before that — but a deploy that
# cannot land DECLINES NOTHING. Every existing instrument is a *decline
# channel* (it fires when a deploy attempts and fails, or when the service stops
# answering), and none of them reads the one datum that answers the question:
# the deployed commit vs `origin/main`.
#
#   - `/health` + `/health/ready` (tortoise/hosted_api.py): process liveness +
#     DB/control-plane readiness. A stale build serves 200s happily; no field
#     knows the deployed commit.
#   - `availability-watchdog`: asserts the app ANSWERS (401/2xx). A lagging
#     deployment answers; "up" ≠ "current".
#   - `availability-liveness` / `availability-record`: watch the watchdog's own
#     heartbeat / record its cadence. Green because the watchdog WAS running.
#   - `main-health-nightly`: watches the five required branch-protection
#     contexts. Deploy success is not one of them, by design.
#   - `deploy-api-alert` (deploy-hosted.yml `if: failure()`): an IN-JOB
#     notifier. It never fires on a cancelled run (documented residual,
#     deploy-hosted.yml), and even when it fires it says "the gate failed",
#     never "production is N commits behind".
#   - `ci-queue-health`: would have shown the backlog, but was itself QUEUED
#     behind the same backlog — an instrument that cannot run during the
#     condition it detects is not a detector.
#   - `check-deployed-drift.sh` (agent-infra): checks the ORCHESTRATOR harness
#     on cells, not the hosted API. A different deployment, so it was green.
#
# THE READING IS POSITIVE, NOT AN ABSENCE
# ---------------------------------------
# This script does not infer health from "no failure was reported" — that is
# the exact signal that lied. It reads the deployed revision from the public
# `GET /v1/version` (`commit_sha`, baked at deploy time by deploy-hosted.yml as
# TORTOISE_GIT_SHA=${GITHUB_SHA}, in SKIP_AUTH so it needs no credentials) and
# compares it to `origin/main` with `git merge-base --is-ancestor` — NEVER a
# commit count for the DECISION (a lagging deployment IS an ancestor; a
# divergent one is not, and those are different failures). The count is only
# the STATED distance.
#
# WHY IT CANNOT BE STARVED INTO SILENCE
# -------------------------------------
# The 4 runs that vanished tonight were cancelled by the `deploy-hosted`
# concurrency group (`cancel-in-progress: true`) as later merges superseded
# them. This check:
#   1. runs on its OWN schedule + `workflow_dispatch`, NOT `push: main` — no
#      merge ever creates its run, so no merge ever cancels it or queues it
#      behind the deploy backlog;
#   2. has its OWN concurrency group with `cancel-in-progress: false`, so a
#      run is never superseded;
#   3. detects a STEADY STATE (production stays behind until a deploy lands),
#      so a GitHub-dropped/delayed scheduled fire is not a loss — the next
#      tick re-fires the same red;
#   4. fails closed: if the deployed revision CANNOT be read, that is a RED,
#      never a pass.
#   Residual (stated, not hidden): a GitHub Actions outage takes the schedule
#   down with it, so the detector's own absence is the one thing it cannot
#   observe — the same residual availability-liveness (#4573) already names
#   for the pager, closing which needs an endpoint external to GitHub and an
#   owner decision.
#
# EXIT CONTRACT: 0 = current (behind ≤ bound, distance stated); 1 = STALE,
# diverged, unreadable, or the checker itself could not assess — every red is
# loud, and a non-zero exit fails the run.
#
# Harness: .github/scripts/check-deploy-staleness.test.sh (synthetic git repo,
# stubs gh, no network).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REPO="${GITHUB_REPOSITORY:-daniel-ospina/tortoise}"
API_URL="${DEPLOY_STALENESS_API_URL:-https://api.premiselabs.co}"
# The deployed commit is read from the API by default; a caller/harness may pin
# it instead. The MAIN side is always a git rev (REF below) in REPO_DIR.
REPO_DIR="${DEPLOY_STALENESS_REPO_DIR:-$SCRIPT_DIR/../..}"
REF="${DEPLOY_STALENESS_REF:-origin/main}"
GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
ALERT_LABEL="${ALERT_LABEL:-auto-filed}"

ALERT_MARKER='<!-- deploy-staleness-alert -->'
ALERT_TITLE='[OPS] production deployment is stale (behind origin/main)'

# The ONE bound: how many commits of lag are tolerated before the check reds.
# Default 2 — a deploy in flight can legitimately sit 1-2 commits behind while
# main advances under it; more than that for longer than one tick is "deploys
# are not keeping up". The 2026-10-26 incident (7 behind) would have fired on
# the first tick at this bound. Overridable via vars.DEPLOY_STALENESS_MAX_BEHIND
# (the workflow passes it) or DEPLOY_STALENESS_MAX_BEHIND for the harness.
MAX_BEHIND_DEFAULT=2
MAX_BEHIND="${DEPLOY_STALENESS_MAX_BEHIND:-}"

# A browser UA — Cloudflare serves 403 to the default curl/urllib agent (the
# same UA tools/ship_test_onboarding.py uses for the same endpoint).
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

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

# ⛔ THE BOUND IS UNTRUSTED (a repository variable / env). Accept ONLY a plain
# integer; digit-stripping `2h`→2 or `-1`→1 would silently near-zero the bound
# and false-fire on every run — the pager that cries wolf is the defect
# restated. Bound the MAGNITUDE before any arithmetic: bash's `[ -gt ]` on an
# over-intmax value reads FALSE (and `$(( ))` wraps), which would mute the
# comparison. Reject on DIGIT COUNT, then on range.
normalize_max_behind() { # <raw> -> commits
  local raw="${1:-}"
  if [ -z "$raw" ]; then printf '%s' "$MAX_BEHIND_DEFAULT"; return 0; fi
  case "$raw" in
    *[!0-9]*)
      warn "DEPLOY_STALENESS_MAX_BEHIND='[${raw}]' is not a plain integer — using the default ${MAX_BEHIND_DEFAULT} (a mangled bound must not disable the comparison)"
      printf '%s' "$MAX_BEHIND_DEFAULT"; return 0 ;;
  esac
  [ "${#raw}" -gt 6 ] && {
    warn "DEPLOY_STALENESS_MAX_BEHIND='[${raw}]' has more than 6 digits — using the default ${MAX_BEHIND_DEFAULT}"
    printf '%s' "$MAX_BEHIND_DEFAULT"; return 0; }
  raw="$(printf '%s' "$raw" | sed 's/^0*//')"
  [ -n "$raw" ] || raw=0
  if [ "$raw" -gt 10000 ]; then
    warn "DEPLOY_STALENESS_MAX_BEHIND=${raw} is above 10000 — using the default ${MAX_BEHIND_DEFAULT}"
    printf '%s' "$MAX_BEHIND_DEFAULT"; return 0; fi
  printf '%s' "$raw"
}

# ── the deployed revision, positively read ───────────────────────────────────
# The ONLY ground truth for "what is production running". Empty when the seam is
# set empty, the HTTP read failed, or the payload carried no commit_sha — and an
# empty revision is a RED at the call site, never a pass.
read_deployed_sha() {
  # `${VAR+x}` tests SET, not non-empty: an explicitly-empty seam (the harness's
  # "revision unreadable" case) must be honoured as empty, not fall through to
  # the HTTP read. Unset = production = read the API.
  if [ "${DEPLOY_STALENESS_COMMIT_SHA+x}" = "x" ]; then
    printf '%s' "${DEPLOY_STALENESS_COMMIT_SHA}"; return 0
  fi
  local body rc errf
  errf="$(mktemp 2>/dev/null || printf '%s' "/tmp/deploy-staleness-version.$$")"
  if ! body="$(curl -fsS --max-time 30 --retry 2 -A "$UA" "${API_URL%/}/v1/version" 2>"$errf")"; then
    warn "GET ${API_URL%/}/v1/version failed — $(head -3 "$errf" 2>/dev/null | tr '\n' ' ')"
    rm -f "$errf"
    printf ''; return 0
  fi
  rm -f "$errf"
  printf '%s' "$body" | jq -r '.commit_sha // ""' 2>/dev/null || printf ''
}

# ── the main side, resolved from the git repo ────────────────────────────────
# Fetches only when REF names an origin/ branch and an origin remote exists
# (production); the harness pins REF to a local branch and there is no origin.
resolve_main() {
  case "$REF" in
    origin/*)
      if git -C "$REPO_DIR" remote 2>/dev/null | grep -qx origin; then
        git -C "$REPO_DIR" fetch --quiet origin "${REF#origin/}" 2>/dev/null \
          || warn "could not fetch $REF — comparing the LOCAL ref, which may be stale"
      fi ;;
  esac
  git -C "$REPO_DIR" rev-parse --verify --quiet "${REF}^{commit}" 2>/dev/null || printf ''
}

# ── GitHub issue helpers (the availability-liveness shape, trimmed) ─────────
urlencode() { printf '%s' "$1" | jq -sRr @uri; }

# ⛔ Same forged-title guard as availability-liveness.sh: this is a PUBLIC repo,
# so an open issue whose title matches is not necessarily ours. Adopt only
# `github-actions[bot]` + exact title + body marker.
search_issue() { # <marker> <exact-title> -> number | "" | __ERR__|<reason>
  local q enc out n errf
  q="repo:${REPO} is:issue is:open in:title author:app/github-actions \"$2\""
  enc="$(urlencode "$q")"
  errf="$(mktemp 2>/dev/null || printf '%s' "/tmp/deploy-staleness-search.$$")"
  if ! out="$(gh api "search/issues?q=${enc}&per_page=100" --paginate 2>"$errf")"; then
    printf '__ERR__|%s' "$(head -3 "$errf" 2>/dev/null | tr '\n' ' ')"; rm -f "$errf"; return 0
  fi
  rm -f "$errf"
  if ! n="$(printf '%s' "$out" | jq -rs --arg login 'github-actions[bot]' --arg title "$2" --arg marker "$1" \
      '[.[].items[]? | select((.user.login // "") == $login) | select((.title // "") == $title) | select(((.body // "") | contains($marker)))][0].number // empty' 2>/dev/null)"; then
    printf '__ERR__|unparseable search response'; return 0
  fi
  [ -n "$n" ] || { printf ''; return 0; }
  case "$n" in
    *[!0-9]*) printf '__ERR__|search returned a non-numeric issue number'; return 0 ;;
  esac
  printf '%s' "$n"
}

redact_text() {
  printf '%s' "$1" | sed -E \
    -e 's#([?&](token|key|secret|sig|signature|api_key|apikey|access_token)=)[^&"[:space:]]*#\1<redacted>#g' \
    -e 's#FlyV1[[:space:]]+[A-Za-z0-9_=+/.,-]+#<redacted>#g' \
    -e 's#fm2_[A-Za-z0-9_=+/.,-]{20,}#<redacted>#g'
}

create_issue() { # <title> <body> -> number ("" on failure)
  local payload out n
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
    warn "issue body update failed for #$1"; return 1
  fi
  return 0
}

comment_issue() { # <n> <body> -> 0 ok / 1 failed
  local payload
  payload="$(jq -n --arg b "$(redact_text "$2")" '{body:$b}')"
  if ! printf '%s' "$payload" | gh api "repos/${REPO}/issues/$1/comments" --method POST --input - >/dev/null 2>&1; then
    warn "comment failed on #$1"; return 1
  fi
  return 0
}

close_issue() { # <n> -> 0 ok / 1 failed
  if ! printf '%s' '{"state":"closed"}' | gh api "repos/${REPO}/issues/$1" --method PATCH --input - >/dev/null 2>&1; then
    warn "close failed on #$1"; return 1
  fi
  return 0
}

# ── the one message that names what is stale ─────────────────────────────────
# Kept in ONE place so the filed body and the deduped update can never drift.
alert_body() { # <reason> <behind-clause> <deployed> <main>
  cat <<BODY
${ALERT_MARKER}
deploy_staleness=stale
reason=$1
deployed=$3
main=$4
${2}
max_behind=${MAX_BEHIND}
checked_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

⛔ **The commit production is running is not current.** The invariant is one
sentence: the commit production is running must be an ancestor of \`origin/main\`,
and the distance must be stated. It is not, and this check states it:

\`\`\`
deployed=$3
main=$4
${2}
\`\`\`

This is a **positive reading**, not an absence: the deployed revision was
observed (via \`GET ${API_URL%/}/v1/version\`), compared with \`origin/main\`
via \`git merge-base --is-ancestor\`, and found stale. No failure was inferred
from silence — the silence is what this check exists to break.

**Check, in this order:**
1. \`gh run list --workflow deploy-hosted.yml --limit 20\` — are deploys landing?
   Red/cancelled runs here are the cause (a migration-drift gate failing closed,
   or a \`cancel-in-progress\` cascade from rapid merges).
2. The drift gate's own report (the failing run's \`Check migration drift\`
   step) — if a migration is ahead of the app, the co-move flip sequence in
   \`supabase-deploy.yml\` is the fix path, NOT a bypass.
3. If a deploy landed but this check still reds, the deployed commit diverged
   from main — read \`deployed=\` above against \`git log\`.

Runbook: docs/infra-runbook.md → §8.7 "When production runs an old commit —
the staleness detector".
BODY
}

# ── main ────────────────────────────────────────────────────────────────────
main() {
  local deployed main behind state reason behind_clause alert search_error new_body

  # Fail closed: a checker that cannot alert is a deaf checker.
  if [ -z "$GH_TOKEN" ]; then
    fail "GH_TOKEN is not set — refusing to run a staleness checker that cannot file its alert (a deaf checker is worse than no checker)"
    exit 1
  fi
  if ! command -v jq >/dev/null 2>&1; then
    fail "jq is required"
    exit 1
  fi
  if ! command -v git >/dev/null 2>&1; then
    fail "git is required"
    exit 1
  fi
  MAX_BEHIND="$(normalize_max_behind "$MAX_BEHIND")"

  log "check-deploy-staleness (#10109) — ${REPO}; ref=${REF}; bound=${MAX_BEHIND} commits"

  main="$(resolve_main)"
  if [ -z "$main" ]; then
    # cannot resolve main = cannot compare = cannot verify, which is never a
    # pass (agent-infra#1745 doctrine, the same rule check-deployed-drift.sh
    # enforces). RED, not silent.
    fail "cannot resolve ${REF} in ${REPO_DIR} — the comparison cannot run, and an unanswered check is never a pass"
    exit 1
  fi

  deployed="$(read_deployed_sha)"
  behind=""; state=""; reason=""; behind_clause=""

  if [ -z "$deployed" ]; then
    state="unreadable"; reason="deployed-sha-unreadable"
    behind_clause="behind=unknown"
  elif ! git -C "$REPO_DIR" cat-file -e "${deployed}^{commit}" 2>/dev/null; then
    # The sha was read but is not an object in this repo: production is running
    # a revision main does not contain (diverged, force-pushed away, or a sha
    # that was never on main). A different failure from lag, and still RED.
    state="diverged"; reason="deployed-sha-not-an-object"
    behind_clause="behind=unknown (deployed sha is not an object in this repo)"
  elif [ "$deployed" = "$main" ]; then
    state="current"; reason="current"
    behind=0
    behind_clause="behind=0"
  elif git -C "$REPO_DIR" merge-base --is-ancestor "$deployed" "$main" 2>/dev/null; then
    if ! behind="$(git -C "$REPO_DIR" rev-list --count "${deployed}..${main}" 2>/dev/null)"; then
      # A lag whose distance cannot be counted is STILL a lag: red, not green.
      # (A `|| 0` here would read a failed count as "current" — a fail-open.)
      state="unknown"; reason="distance-unreadable"
      behind_clause="behind=unknown (could not count the distance)"
    elif [ "$behind" -gt "$MAX_BEHIND" ]; then
      state="stale"; reason="behind"
      behind_clause="behind=${behind}"
    else
      state="current"; reason="within-bound"
      behind_clause="behind=${behind}"
    fi
  else
    state="diverged"; reason="not-ancestor"
    behind_clause="behind=n/a (deployed commit is NOT an ancestor of main)"
  fi

  # The distance is ALWAYS stated — even green, even the degenerate cases — so a
  # reader can see the bound being approached before it is crossed.
  note "deployed=${deployed} main=${main} ${behind_clause} (state=${state}, bound=${MAX_BEHIND})"

  alert="$(search_issue "$ALERT_MARKER" "$ALERT_TITLE")"
  case "$alert" in
    __ERR__*)
      search_error="${alert#__ERR__|}"
      fail "staleness-alert search failed — refusing to file or resolve (never duplicate); cannot confirm the alert state${search_error:+ — reason: ${search_error}}"
      exit 1 ;;
  esac

  if [ "$state" != "current" ]; then
    new_body="$(alert_body "$reason" "$behind_clause" "$deployed" "$main")"
    if [ -z "$alert" ]; then
      if ! alert="$(create_issue "$ALERT_TITLE" "$new_body")" || [ -z "$alert" ]; then
        fail "PRODUCTION STALE (${reason}) and the alert issue could not be filed — this failing run IS the alert"
        exit 1
      fi
      note "filed staleness alert #${alert} (${reason})"
    else
      if ! update_issue_body "$alert" "$new_body"; then
        fail "PRODUCTION STALE (${reason}) but the alert body on #${alert} could not be updated — this failing run IS the alert"
        exit 1
      fi
      note "staleness alert #${alert} is still STALE (${reason}) — body refreshed"
    fi
    fail "PRODUCTION STALE (${reason}) — deployed=${deployed} main=${main} ${behind_clause}. See #${alert}."
    exit 1
  fi

  # Current. Resolve a standing alert if one is open — a stale OPEN alert is
  # its own false alarm. Close BEFORE commenting, so a failing close cannot
  # re-post "Recovered" on every run.
  if [ -n "$alert" ]; then
    if ! close_issue "$alert"; then
      fail "production recovered but the alert issue #${alert} could not be closed — failing the run so the stale alert is not silent"
      exit 1
    fi
    if ! comment_issue "$alert" "✅ **Recovered** — the deployed commit is an ancestor of \`${REF}\` within the bound (${behind_clause}) at $(date -u +%Y-%m-%dT%H:%M:%SZ). This alert is closed and re-files automatically if production goes stale again."; then
      fail "#${alert} was CLOSED but the 'Recovered' comment failed — the state is correct, the record is missing; failing the run so it is not silent"
      exit 1
    fi
    note "closed staleness alert #${alert} (recovered)"
  fi
  note "production current — deployed=${deployed} main=${main} ${behind_clause} (bound ${MAX_BEHIND})"
  exit 0
}

if [ "${DEPLOY_STALENESS_LIB_ONLY:-0}" != "1" ]; then
  main "$@"
fi
