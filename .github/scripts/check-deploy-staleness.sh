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
# WHAT COUNTS AS BEHIND (two clocks, both needed)
# ----------------------------------------------
# 1. COUNT, over the commits that CAN change the deployment. `deploy-hosted.yml`
#    deploys on `on.push.paths` only, so a merge confined to `docs/`, `skills/`,
#    `tools/`, `.github/`, `website/` or `operations/` (or to `tests/` other than
#    `tests/sample_transcript.txt`, which the image copies) advances main
#    WITHOUT deploying anything — by the pipeline's own declared policy. Counting
#    those as lag reds the check for a condition nobody can act on, and an alarm
#    that cries wolf is the defect this check exists to prevent, restated.
#    Measured on the last 300 commits of main, over the trigger's own filter: at
#    the default bound of 2, 42 commits (14.0%) sat above it for a NON-deployable
#    lag — 20 distinct false-red windows, 21.1 h of 148.7 h cumulative, i.e. 20
#    alert create/PATCH plus 20 "Recovered" comment cycles. At bound 3 the same
#    metric is 22 commits (7.3%) and 10 windows: still a false alarm, only
#    rarer, so the bound is not the lever. `behind_deployable=` decides and
#    `behind=` (the raw distance) is stated alongside it.
# 2. AGE, because a count bound alone stays GREEN forever in the state #10109
#    was filed about: one deployable commit pending, tolerated by the bound,
#    never deployed. `deployable_age_h=` is the age of the OLDEST undeployed
#    deployable commit (its committer date — stateless, no memory of previous
#    ticks), and it reds past `max_age_hours`. The age arm is what makes "the
#    deploy stopped landing" observable independently of how many merges piled
#    up behind it.
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
# EXIT CONTRACT: 0 = current (deployable lag ≤ bound AND the oldest deployable
# lag is at or under the age bound; distance stated); 1 = STALE, diverged, unreadable,
# or the checker itself could not assess — every red is loud, and a non-zero exit
# fails the run.
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

# BOUND 1 (count): how many DEPLOYABLE commits of lag are tolerated before the
# check reds. Default 2 — a deploy in flight can legitimately sit 1-2 deployable
# commits behind while main advances under it; more than that for longer than
# one tick is "deploys are not keeping up". The 2026-10-26 incident (7 behind,
# all of them fixing `tortoise/`) would have fired on the first tick at this
# bound. Overridable via vars.DEPLOY_STALENESS_MAX_BEHIND (the workflow passes
# it) or DEPLOY_STALENESS_MAX_BEHIND for the harness.
MAX_BEHIND_DEFAULT=2
MAX_BEHIND="${DEPLOY_STALENESS_MAX_BEHIND:-}"

# BOUND 2 (age): how long the OLDEST undeployed deployable commit may sit
# undeployed before the check reds. Default 6 h — deploys land in tens of
# minutes, and the 2026-10-26 incident ran 9 h, so 6 h fires while there is
# still an evening to recover. Independent of the count: one pending deployable
# commit that never deploys is exactly the state a count bound tolerates
# forever. Overridable via vars.DEPLOY_STALENESS_MAX_AGE_HOURS (the workflow
# passes it) or DEPLOY_STALENESS_MAX_AGE_HOURS for the harness.
MAX_AGE_HOURS_DEFAULT=6
MAX_AGE_HOURS="${DEPLOY_STALENESS_MAX_AGE_HOURS:-}"
# A year. The cap exists so a mangled value cannot mute the arm via `[ -gt ]`
# reading FALSE on an over-intmax integer.
MAX_AGE_HOURS_CAP=8760

# The deploy trigger whose `on.push.paths` defines "a commit that can change the
# deployment". A seam so the harness can pin a fixture filter; production reads
# the real workflow from the same checkout.
DEPLOY_WORKFLOW="${DEPLOY_STALENESS_DEPLOY_WORKFLOW:-$SCRIPT_DIR/../workflows/deploy-hosted.yml}"

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

# ⛔ THE BOUNDS ARE UNTRUSTED (repository variables / env). Accept ONLY a plain
# integer; digit-stripping `2h`→2 or `-1`→1 would silently near-zero the bound
# and false-fire on every run — the pager that cries wolf is the defect
# restated. Bound the MAGNITUDE before any arithmetic: bash's `[ -gt ]` on an
# over-intmax value reads FALSE (and `$(( ))` wraps), which would mute the
# comparison. Reject on DIGIT COUNT, then on range. ONE normalizer for both
# bounds, so neither can drift into a laxer parse than the other.
normalize_uint() { # <raw> <default> <name> <cap> -> uint
  local raw="${1:-}" default="$2" name="$3" cap="$4"
  if [ -z "$raw" ]; then printf '%s' "$default"; return 0; fi
  case "$raw" in
    *[!0-9]*)
      warn "${name}='[${raw}]' is not a plain integer — using the default ${default} (a mangled bound must not disable the comparison)"
      printf '%s' "$default"; return 0 ;;
  esac
  if [ "${#raw}" -gt 6 ]; then
    warn "${name}='[${raw}]' has more than 6 digits — using the default ${default}"
    printf '%s' "$default"; return 0; fi
  raw="$(printf '%s' "$raw" | sed 's/^0*//')"
  [ -n "$raw" ] || raw=0
  if [ "$raw" -gt "$cap" ]; then
    warn "${name}=${raw} is above ${cap} — using the default ${default}"
    printf '%s' "$default"; return 0; fi
  printf '%s' "$raw"
}

normalize_max_behind() { normalize_uint "${1:-}" "$MAX_BEHIND_DEFAULT" "DEPLOY_STALENESS_MAX_BEHIND" 10000; }
normalize_max_age_hours() { normalize_uint "${1:-}" "$MAX_AGE_HOURS_DEFAULT" "DEPLOY_STALENESS_MAX_AGE_HOURS" "$MAX_AGE_HOURS_CAP"; }

# ── what a merge must TOUCH to redeploy, read from the trigger itself ────────
# One glob per line from `on.push.paths` of the deploy workflow. Printed empty
# when the trigger has no path filter, and EXITS NON-ZERO when the block is there
# but a shape inside it is not recognised — a PARTIAL set is worse than none,
# because it silently counts fewer deployable commits than the trigger really
# has and so reads GREEN while they wait. The call site turns BOTH into a RED:
# a checker that cannot tell which commits deploy cannot judge staleness, and an
# unanswered question is never a pass (agent-infra#1745 doctrine).
#
# ⛔ The set is NEVER duplicated in this script: a second copy would silently
# stop matching the trigger it mirrors, and the check would then contradict the
# pipeline on every merge that touches whatever the copy missed.
read_deploy_paths() { # -> globs on stdout, one per line
  [ -f "$DEPLOY_WORKFLOW" ] || return 0
  awk '
    /^on:[[:space:]]*$/ { in_on=1; next }
    in_on && /^[^[:space:]#]/ { in_on=0; in_push=0; in_paths=0 }
    in_on && /^  push:[[:space:]]*$/ { in_push=1; next }
    in_on && in_push && /^  [A-Za-z_]/ { in_push=0; in_paths=0 }
    in_push && /^    paths:[[:space:]]*$/ { in_paths=1; next }
    in_paths {
      if ($0 ~ /^[[:space:]]*(#.*)?$/) next
      if ($0 ~ /^      - /) {
        v=$0; sub(/^      - /,"",v); sub(/[[:space:]]+#.*$/,"",v)
        gsub(/^[[:space:]]+|[[:space:]]+$/,"",v); gsub(/^["\047]|["\047]$/,"",v)
        if (v=="") next
        # A YAML block scalar (`- >-`, `- |`) is not a glob. Emitting it as one
        # would put a non-path into the pathspec list.
        if (v ~ /^[|>]/) exit 3
        # YAML NODE DECORATION is not a glob either, and it is the shape that
        # makes this a SILENT fail-open: `- &a 'x/**'` strips its outer quotes to
        # the literal `&a 'x/**'`, and `- *a` stays `*a`. Neither matches any
        # path, so the pathspec count is 0 — `behind_deployable=0`, GREEN, while
        # a deployable commit waits. (`*a` as a bare word IS a YAML alias; a glob
        # that legitimately starts with `*` has to be quoted, and refusing that
        # ambiguity is the fail-CLOSED direction.) Refusing decoration leaves a
        # NAMED red instead of a wrong number.
        if (v ~ /^[&!]/) exit 3
        if (v ~ /^\*[A-Za-z_][A-Za-z0-9_-]*$/) exit 3
        if (v ~ /[[:space:]]/) exit 3
        print v
        next
      }
      # Still indented INSIDE `paths:`, but not a list entry: a folded scalar, a
      # mapping, or some shape this parser does not know. A PARTIAL path set is
      # not a path set — it would count FEWER deployable commits than the
      # trigger and read GREEN while they wait, the "silently never fires" class
      # this check exists to refuse. Exit non-zero so the caller can RED.
      if ($0 ~ /^      /) exit 3
      # A dedent (or EOF) is the one clean way the block ends.
      exit 0
    }
  ' "$DEPLOY_WORKFLOW" 2>/dev/null
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
max_age_hours=${MAX_AGE_HOURS}
checked_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

⛔ **The commit production is running is not current.** The invariant is one
sentence: the commit production is running must be an ancestor of \`origin/main\`,
and the distance must be stated. It is not, and this check states it:

\`\`\`
deployed=$3
main=$4
${2}
\`\`\`

**What the two distances mean.** \`behind=\` is every commit on main after the
deployed one. \`behind_deployable=\` is the commits that can actually change the
deployment — the ones touching \`on.push.paths\` in
\`.github/workflows/deploy-hosted.yml\` (\`tortoise/**\`, \`packs/**\`, the image
inputs: \`Dockerfile.hosted\`, \`entrypoint.sh\`, \`requirements.txt\`,
\`pyproject.toml\`, \`uv.lock\`, \`fly.toml\`, \`product/pricing.json\`, …). A
\`docs/\`-only lag is **not** staleness — the pipeline deliberately does not
redeploy for it — so the alarm keys on the second number. \`deployable_age_h=\`
is how long the OLDEST undeployed deployable commit has been waiting, which is
what reds the \`max_age_hours\` (${MAX_AGE_HOURS} h) arm even when the count is
small: one commit that never deploys is the state a count bound tolerates
forever.

This is a **positive reading**, not an absence: the deployed revision was
observed (via \`GET ${API_URL%/}/v1/version\`), compared with \`origin/main\`
via \`git merge-base --is-ancestor\`, and found stale. No failure was inferred
from silence — the silence is what this check exists to break.

**Check, in this order:**
1. \`gh run list --workflow deploy-hosted.yml --limit 20\` — are deploys landing?
   Red/cancelled runs here are the cause (a migration-drift gate failing closed,
   or a newer push superseding a still-PENDING run — \`cancel-in-progress\` is
   now \`false\`, so a RUNNING deploy is no longer cancelled mid-deploy). If
   there is **no run
   at all** for the undeployed deployable commits, the deploy was never
   triggered — check the trigger's path filter, not the deploy's logs.
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
  local deployed main behind behind_deployable state reason behind_clause alert search_error new_body
  local deploy_paths oldest_ts age_secs age_hours age_clause
  local -a deploy_paths_arr

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
  MAX_AGE_HOURS="$(normalize_max_age_hours "$MAX_AGE_HOURS")"

  log "check-deploy-staleness (#10109) — ${REPO}; ref=${REF}; bounds: behind≤${MAX_BEHIND} deployable commits, age≤${MAX_AGE_HOURS}h"

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
    behind_clause="behind=0 behind_deployable=0 deployable_age_h=none"
  elif git -C "$REPO_DIR" merge-base --is-ancestor "$deployed" "$main" 2>/dev/null; then
    if ! behind="$(git -C "$REPO_DIR" rev-list --count "${deployed}..${main}" 2>/dev/null)"; then
      # A lag whose distance cannot be counted is STILL a lag: red, not green.
      # (A `|| 0` here would read a failed count as "current" — a fail-open.)
      state="unknown"; reason="distance-unreadable"
      behind_clause="behind=unknown (could not count the distance)"
    else
      # The distance that DECIDES is the deployable one (the deploy trigger's
      # own path filter). `behind` stays stated alongside it: it is the raw lag
      # a reader may want, just not the staleness verdict.
      # `if !` is exempt from `set -e`, so an unparseable filter lands on the
      # NAMED arm below instead of aborting the script with no reason. RED
      # either way, but a red that says what it could not read is the point.
      if ! deploy_paths="$(read_deploy_paths)"; then
        state="unknown"; reason="deploy-paths-unreadable"
        warn "on.push.paths in ${DEPLOY_WORKFLOW} did not parse unambiguously — RED (a partial path set would UNDER-count the deployable lag and read green while deployable commits wait)"
        behind_clause="behind=${behind} behind_deployable=unknown (on.push.paths in ${DEPLOY_WORKFLOW} did not parse unambiguously)"
      elif [ -z "$deploy_paths" ]; then
        # Cannot tell which commits deploy = cannot judge staleness = never a
        # pass (agent-infra#1745). RED, and it names what it could not read.
        state="unknown"; reason="deploy-paths-unreadable"
        behind_clause="behind=${behind} behind_deployable=unknown (could not read on.push.paths from ${DEPLOY_WORKFLOW})"
      else
        deploy_paths_arr=()
        while IFS= read -r p; do
          if [ -n "$p" ]; then deploy_paths_arr+=("$p"); fi
        done <<< "$deploy_paths"
        if ! behind_deployable="$(git -C "$REPO_DIR" rev-list --count "${deployed}..${main}" -- "${deploy_paths_arr[@]}" 2>/dev/null)"; then
          # Same rule as the raw count: an uncountable deployable distance is a
          # RED, never a silent "current".
          state="unknown"; reason="deployable-distance-unreadable"
          behind_clause="behind=${behind} behind_deployable=unknown (could not count the deployable distance)"
        else
          age_secs=0; age_hours=0; age_clause=" deployable_age_h=none"
          if [ "$behind_deployable" -gt 0 ]; then
            # The OLDEST undeployed deployable commit's committer date — the
            # stateless half of the #10109 T-arm. `sort -n | sed -n 1p` takes the
            # minimum regardless of traversal order (a merge commit can be
            # traversed after an older sibling), and `sed` drains the pipe so
            # `set -o pipefail` cannot see a SIGPIPE from `git log`.
            # `|| oldest_ts=""` so a genuine git failure lands on the NAMED arm
            # below (reason=deployable-age-unreadable) instead of aborting the
            # script under `set -e` with no reason: red either way, but a red
            # that says what it could not read is the point.
            oldest_ts="$(git -C "$REPO_DIR" log --format=%ct "${deployed}..${main}" -- "${deploy_paths_arr[@]}" 2>/dev/null | sort -n | sed -n '1p')" || oldest_ts=""
            case "$oldest_ts" in
              ''|*[!0-9]*)
                state="unknown"; reason="deployable-age-unreadable"
                behind_clause="behind=${behind} behind_deployable=${behind_deployable} deployable_age_h=unknown (could not date the oldest undeployed deployable commit)"
                ;;
            esac
            if [ -z "$state" ]; then
              age_secs=$(( $(date -u +%s) - oldest_ts ))
              [ "$age_secs" -lt 0 ] && age_secs=0   # clock skew must not read as ancient
              age_hours=$(( age_secs / 3600 ))
              # `deployable_age_h` is FLOORED while the verdict is decided on
              # SECONDS, so an alert at 6 h 01 m would otherwise print
              # `deployable_age_h=6 max_age_hours=6` while RED — which reads as
              # an off-by-one in the check rather than a deploy to investigate.
              # The seconds are stated so the two can never appear to conflict.
              age_clause=" deployable_age_h=${age_hours} deployable_age_s=${age_secs} max_age_hours=${MAX_AGE_HOURS}"
            fi
          fi
          if [ -z "$state" ]; then
            if [ "$behind_deployable" -gt "$MAX_BEHIND" ]; then
              state="stale"; reason="behind-deployable"
              behind_clause="behind=${behind} behind_deployable=${behind_deployable}${age_clause}"
            elif [ "$behind_deployable" -gt 0 ] && [ "$age_secs" -gt $(( MAX_AGE_HOURS * 3600 )) ]; then
              # COUNT within bound, but the pending deployable commits are old:
              # deploys are not landing. This is the #10109 shape a count bound
              # alone reports as GREEN forever.
              state="stale"; reason="behind-too-long"
              behind_clause="behind=${behind} behind_deployable=${behind_deployable}${age_clause}"
            else
              state="current"; reason="within-bound"
              behind_clause="behind=${behind} behind_deployable=${behind_deployable}${age_clause}"
            fi
          fi
        fi
      fi
    fi
  else
    state="diverged"; reason="not-ancestor"
    behind_clause="behind=n/a (deployed commit is NOT an ancestor of main)"
  fi

  # The distance is ALWAYS stated — even green, even the degenerate cases — so a
  # reader can see the bound being approached before it is crossed.
  note "deployed=${deployed} main=${main} ${behind_clause} (state=${state}, bound=${MAX_BEHIND} deployable commits / ${MAX_AGE_HOURS}h)"

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
