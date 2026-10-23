#!/usr/bin/env bash
# #9551 — remove the Postgres container this job started. Best-effort by design:
# a teardown failure must never change a job's verdict (the run step already
# decided it). A HARD-KILLED job runs neither this nor provision.sh's own EXIT
# trap and can leave a container behind; its label names the run/job/attempt,
# which is how a human or a future reaper attributes it.
set -uo pipefail

SUFFIX="${GITHUB_RUN_ID:-local}-${GITHUB_JOB:-job}-${GITHUB_RUN_ATTEMPT:-1}"

# The provision step exports the EXACT per-instance label. Without it there is
# nothing this job can safely claim: the run/job/attempt key is shared by every
# matrix shard (GITHUB_JOB is the job_id, not the shard), so sweeping that prefix
# would delete a PEER shard's live server. A container left behind by a provision
# that failed before exporting is removed by provision.sh's own EXIT trap.
LABEL="${TORTOISE_CI_POSTGRES_LABEL:-}"
if [ -z "$LABEL" ]; then
  echo "#9551 teardown: no TORTOISE_CI_POSTGRES_LABEL ($SUFFIX) — nothing to remove"
  exit 0
fi

ids="$(docker ps -aq --filter "label=$LABEL" 2>/dev/null || true)"
if [ -z "$ids" ]; then
  echo "#9551 teardown: no containers left for $LABEL"
  exit 0
fi
# shellcheck disable=SC2086  # $ids is a deliberate word-split id list
echo "#9551 teardown: removing $(printf '%s\n' $ids | wc -l | tr -d ' ') container(s) for $LABEL"
# shellcheck disable=SC2086  # $ids is a deliberate word-split id list
docker rm -f $ids >/dev/null 2>&1 || true
exit 0
