#!/usr/bin/env bash
# published-artifact-alert.sh — the DURABLE record for release-artifact-canary (#12363).
#
# WHY THIS EXISTS. The canary's job summary is visible only to whoever opens that
# run. The sibling detectors it is modelled on (deploy-staleness #10109,
# availability-watchdog) file an issue and close it on recovery — without that, a
# 97-day-broken published artifact can keep being broken while every summary says so.
# A red that opens no issue is a red nobody sees.
#
# usage: published-artifact-alert.sh open|close
#   open   — refresh the open alert issue for this repository (comment if it exists,
#            create it if not). Reads $EXIT_CODE so an exit-2 detector outage is NOT
#            presented as "the artifact is broken, cut a release".
#   close  — comment + close every open alert issue (the run is green again).
#
# Contracts (both fail CLOSED, never silently):
#   * `gh` must exist, or the step fails NAMING the missing tool (#9018) rather than
#     filing nothing while reporting success.
#   * GH_TOKEN must be present — Actions does not export GITHUB_TOKEN itself (#2140).
#   * `close` with no `gh` exits 0: nothing to close is not an error, and a green run
#     must never be turned red by the alert plumbing.
set -euo pipefail

TITLE="release-artifact-canary: published PyPI artifact is red"

die() {
  echo "::error::$*" >&2
  exit 1
}

require_gh() {
  command -v gh >/dev/null 2>&1 || die "gh is missing on this runner — the alert issue cannot be filed (#9018)"
  [ -n "${GH_TOKEN:-}" ] || die "GH_TOKEN is not set — Actions does not export GITHUB_TOKEN itself (#2140)"
  [ -n "${GITHUB_REPOSITORY:-}" ] || die "GITHUB_REPOSITORY is not set"
}

open_issue() {
  require_gh
  local why
  if [ "${EXIT_CODE:-}" = "2" ]; then
    why="The detector could not evaluate the artifact (exit 2: the index was unreachable or the install could not be judged either way). This is **not** evidence the artifact is broken — check the detector before anything else."
  else
    why="The **published** artifact fails the documented steps (\`pip install tortoise-graph\` then \`tortoise init\`, and \`pip install tortoise-client\` resolves on the index). No change in this repository fixes it: it clears when a release lands (version bump → tag → \`publish-pypi.yml\`)."
  fi

  local log="${RUNNER_TEMP:-/tmp}/canary/published-artifact-canary.log"
  local tail_text="(no transcript was written)"
  if [ -s "$log" ]; then
    tail_text="$(sed -n '1,40p' "$log")"
  fi

  local body
  body="$(printf '%s\n\n```\n%s\n```\n\nRun: %s/%s/actions/runs/%s\nTranscript: the `published-artifact-canary-log` artifact on that run.\n\nRefs #12363\n' \
    "$why" "$tail_text" "${GITHUB_SERVER_URL:-https://github.com}" "$GITHUB_REPOSITORY" "${GITHUB_RUN_ID:-unknown}")"

  local existing
  existing="$(gh issue list --repo "$GITHUB_REPOSITORY" --state open \
    --search "$TITLE in:title" --json number --jq '.[0].number // empty')"
  if [ -n "$existing" ]; then
    gh issue comment "$existing" --repo "$GITHUB_REPOSITORY" --body "$body"
    echo "refreshed alert issue #$existing"
  else
    gh issue create --repo "$GITHUB_REPOSITORY" --title "$TITLE" --body "$body" --label bug
    echo "opened the alert issue"
  fi
}

close_issues() {
  # A green run must not be turned red by the alert plumbing: no gh here means the
  # issue was never filed, so there is nothing to close.
  command -v gh >/dev/null 2>&1 || { echo "gh absent — no alert issue to close"; return 0; }
  [ -n "${GH_TOKEN:-}" ] || { echo "GH_TOKEN absent — no alert issue to close"; return 0; }
  local numbers
  numbers="$(gh issue list --repo "${GITHUB_REPOSITORY:?}" --state open \
    --search "$TITLE in:title" --json number --jq '.[].number')"
  if [ -z "$numbers" ]; then
    echo "no open alert issue"
    return 0
  fi
  for number in $numbers; do
    gh issue comment "$number" --repo "$GITHUB_REPOSITORY" \
      --body "Green: the published artifact now passes its documented first command. Closing. (Refs #12363)"
    gh issue close "$number" --repo "$GITHUB_REPOSITORY" --reason completed
    echo "closed alert issue #$number"
  done
}

case "${1:-}" in
  open) open_issue ;;
  close) close_issues ;;
  *) die "usage: $0 open|close" ;;
esac
