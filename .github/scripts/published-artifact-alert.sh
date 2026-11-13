#!/usr/bin/env bash
# published-artifact-alert.sh — the DURABLE record for release-artifact-canary (#12363).
#
# WHY THIS EXISTS. The canary's job summary is visible only to whoever opens that
# run. The sibling detectors it is modelled on (deploy-staleness #10109,
# availability-watchdog) file an issue and close it on recovery — without that, a
# 97-day-broken published artifact can keep being broken while every summary says so.
# A red that opens no issue is a red nobody sees.
#
# ⛔ IT DOES NOT RE-IMPLEMENT FILING. Dedupe, `Recurrence #N`, close-then-comment and
# the refusal to file on a failed search are the SHARED substrate
# (`.github/scripts/auto-file-issue.sh`, #3907) — every other monitor rides it, and a
# local copy is how the fleet ended up with three different behaviours in the first
# place. This script only supplies the canary's TITLE and BODY.
#
# usage: published-artifact-alert.sh open|close
#   open   — file or recur the alert issue. Reads $EXIT_CODE so an exit-2 detector
#            outage is NOT presented as "the artifact is broken, cut a release".
#            Needs GH_TOKEN + GITHUB_REPOSITORY (Actions does not export
#            GITHUB_TOKEN itself — #2140).
#   close  — resolve it (close-then-comment, via the substrate's `resolve` verb).
#            Never fails a green run: nothing to resolve / no gh is a no-op.
set -euo pipefail

# ⛔ ONE LEDGER PER SEVERITY. Both classes shared one dedupe title, so only the FIRST
# occurrence carried a body and every later one arrived as a severity-free `Recurrence #N`
# — the release-owed issue silently absorbed a detector outage, and the reader could not
# tell which red they were looking at (#12363 round 7).
TITLE_RELEASE="release-artifact-canary: published PyPI artifact is red"
TITLE_OUTAGE="release-artifact-canary: could not evaluate the published artifact"

# ⛔ The substrate path is spelled LITERALLY (no `$(dirname "$BASH_SOURCE")`, no variable):
# every caller runs from the repository root — the workflow step by default, and the
# harness by an explicit `cd` — and a literal path is what lets the shell-discard guard
# READ the script before it runs. A `$`-spelled script path is unverifiable to that
# guard, which fails closed and blocks the harness locally (#12363 round 6).
af_file() { bash .github/scripts/auto-file-issue.sh file "$@"; }
af_resolve() { bash .github/scripts/auto-file-issue.sh resolve "$@"; }

# ⛔ `die` RETURNS, it does not `exit`: the harness SOURCES this file, and an `exit` here
# would kill the harness mid-run instead of failing the assertion that called us. The
# dispatcher at the bottom turns a returned non-zero into the process exit.
die() {
  echo "::error::$1" >&2
  return 1
}

open_issue() {
  [ -f .github/scripts/auto-file-issue.sh ] || {
    die "the shared auto-file substrate is missing at .github/scripts/auto-file-issue.sh (#3907) — run from the repository root" || return 1
  }
  [ -n "${GH_TOKEN:-}" ] || {
    die "GH_TOKEN is not set — Actions does not export GITHUB_TOKEN itself (#2140)" || return 1
  }
  [ -n "${GITHUB_REPOSITORY:-}" ] || { die "GITHUB_REPOSITORY is not set" || return 1; }

  # FAIL-SAFE: only an explicit 1 (the artifact is broken) gets the release body.
  # Anything else — exit 2, unset (a cancelled step writes no output), a future code —
  # is treated as "the canary could not answer", which never asks for an upload.
  local title why
  if [ "${EXIT_CODE:-}" = "1" ]; then
    title="$TITLE_RELEASE"
    why="The **published** artifact fails the documented steps (\`pip install tortoise-graph\` then \`tortoise init\`, and \`pip install tortoise-client\` must resolve on the index). No change in this repository fixes it: it clears when a release lands (version bump → tag → \`publish-pypi.yml\`)."
  else
    title="$TITLE_OUTAGE"
    why="The detector could not evaluate the artifact (exit ${EXIT_CODE:-none}: the index was unreachable, or the install could not be judged either way). This is **not** evidence the artifact is broken — check the detector before anything else, and do NOT cut a release on this run."
  fi

  # The TAIL is what carries the failure: the tool writes the venv/pip setup first and
  # the `tortoise init` output plus its `[exit N]` last, so a head would show the setup
  # and hide the diagnosis (#12363 round 6).
  local log="${RUNNER_TEMP:-/tmp}/canary/published-artifact-canary.log"
  local tail_text="(no transcript was written)"
  if [ -s "$log" ]; then
    tail_text="$(tail -n 40 "$log")"
  fi

  # No `trap ... RETURN`: the trap is PROCESS-global, so a harness that SOURCES this
  # file inherits it and the next function return reads a local that is already out of
  # scope (`body_file: unbound variable` under `set -u`). Clean up explicitly instead.
  local body_file
  body_file="$(mktemp)"
  {
    printf '%s\n\n' "$why"
    printf '```\n%s\n```\n\n' "$tail_text"
    printf 'Run: %s/%s/actions/runs/%s\n' \
      "${GITHUB_SERVER_URL:-https://github.com}" "$GITHUB_REPOSITORY" "${GITHUB_RUN_ID:-unknown}"
    printf 'Transcript: the `published-artifact-canary-log` artifact on that run.\n\n'
    printf 'Refs #12363\n'
  } >"$body_file"

  local rc=0
  af_file --title "$title" --body-file "$body_file" --label bug || rc=$?
  rm -f "$body_file"
  # ⛔ An EXIT-1 run PROVES the artifact evaluates at all, so an open "could not evaluate"
  # issue is now claiming a condition that is no longer true. Same contract the substrate
  # states for `resolve` (#8841) — the ledger must not outlive the state that justified it
  # (#12363 round 8). Best-effort: the filing above is the finding, this is bookkeeping.
  if [ "$title" = "$TITLE_RELEASE" ]; then
    af_resolve --title "$TITLE_OUTAGE" \
      --why "The canary IS evaluating the artifact again (this run reached a verdict): the outage this issue recorded has ended." ||
      echo "::warning::could not resolve the outage ledger"
  fi
  return "$rc"
}

close_issues() {
  # A green run must not be turned red by the alert plumbing: no token/gh means the
  # issue was never filed, so there is nothing to resolve.
  if [ -z "${GH_TOKEN:-}" ] || [ -z "${GITHUB_REPOSITORY:-}" ] || ! command -v gh >/dev/null 2>&1; then
    echo "no alert plumbing in this environment — nothing to resolve"
    return 0
  fi
  local rc=0
  af_resolve --title "$TITLE_RELEASE" \
    --why "Green: the published artifact now passes its documented first command." || rc=$?
  af_resolve --title "$TITLE_OUTAGE" \
    --why "Green: the canary can evaluate the published artifact again." || rc=$?
  if [ "$rc" -ne 0 ]; then
    # A green run must not be reddened by bookkeeping: the READING is what this workflow
    # reports, and the issue ledger is downstream of it (#12363 round 7).
    echo "::warning::the alert ledger could not be resolved (exit $rc) — the run is still green"
    return 0
  fi
}

# Executed → dispatch. SOURCED → the caller owns the verbs (the harness redefines
# `af_file`/`af_resolve` to record instead of filing).
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-}" in
    open) open_issue || exit 1 ;;
    close) close_issues || exit 1 ;;
    *)
      die "usage: $0 open|close" || exit 1
      ;;
  esac
fi
