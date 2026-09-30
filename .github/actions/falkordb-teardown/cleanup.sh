#!/usr/bin/env bash
# #6673 — remove the containers this job started. Best-effort by design: a
# teardown failure must never change a job's verdict (the provision step
# already decided it), and the runner-host reaper is the backstop for a job
# that was killed before this ran.
set -uo pipefail

SUFFIX="${GITHUB_RUN_ID:-local}-${GITHUB_JOB:-job}-${GITHUB_RUN_ATTEMPT:-1}"
LABEL="tortoise-ci-falkordb=${SUFFIX}"

ids="$(docker ps -aq --filter "label=$LABEL" 2>/dev/null || true)"
if [ -z "$ids" ]; then
  echo "#6673 teardown: no containers left for $LABEL"
  exit 0
fi

echo "#6673 teardown: removing $(printf '%s\n' $ids | wc -l | tr -d ' ') container(s) for $LABEL"
# shellcheck disable=SC2086  # $ids is a deliberate word-split id list
docker rm -f $ids >/dev/null 2>&1 || true
exit 0
