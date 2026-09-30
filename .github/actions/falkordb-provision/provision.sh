#!/usr/bin/env bash
# #6673 — provision the docker-lane FalkorDB services on EPHEMERAL host ports.
#
# Replaces the workflows' `services:` blocks. WHY the replacement (and not
# `ports: - 0:6379` inside `services:`): a `services:` container is created by
# the runner under a runner-generated name (`<32hex>_falkordbfalkordbserverv4206_<6hex>`)
# and carries only `--label <runner-start-hash>` — the job has no way to
# identify ITS containers among the host's concurrent jobs, so it could not
# read back the assigned port. Starting them here makes the name, the port
# discovery and the health gate all explicit.
#
# Exports (via $GITHUB_ENV, so every later step sees them):
#   TORTOISE_TEST_DOCKER_PORT  the passworded service's assigned host port
#   TORTOISE_TEST_LEGACY_PORT  the passwordless legacy service's (when legacy=true)
#   TORTOISE_DB_URI            only when $FALKORDB_URI_GRAPH is non-empty
# The port names are read by tests/_live_utils.py (#6673). They are NOT the
# product's FALKORDB_HOST/FALKORDB_PORT pair — see that module's note.
set -euo pipefail

IMAGE="${FALKORDB_IMAGE:?}"
LEGACY="${FALKORDB_LEGACY:-true}"
URI_GRAPH="${FALKORDB_URI_GRAPH:-}"
# Health-gate bound (seconds). Widened from the `services:` blocks' 5s x 10
# because this also covers the image pull on a cold runner; a caller with a
# slow pull can raise it, and the hermetic tests lower it.
HEALTH_TIMEOUT="${FALKORDB_HEALTH_TIMEOUT:-60}"

# Ownership label: one value per JOB INSTANCE. The teardown step selects on
# it, so this job can remove exactly its own containers.
#
# ⚠️ Residual risk, stated rather than papered over: a job that is HARD-KILLED
# (runner death) runs neither the teardown step nor the EXIT trap, and `--rm`
# only removes a container when the container itself exits — so such a job can
# leave a RUNNING container behind. Its label names the run/job/attempt, which
# is how a human or a future reaper can attribute it; NO automatic reaper
# exists today.
## ⛔ GITHUB_JOB is the job_id — IDENTICAL for every matrix shard (a 9-shard
# fast matrix is ONE GITHUB_JOB), so the run/job/attempt triple alone is NOT
# unique among concurrent shards: keying on it made each shard's
# `docker rm -f` (and the teardown's label sweep) destroy a PEER SHARD's live
# server — the collision this change exists to remove, moved into the name
# namespace. The instance token is generated here and exported, so teardown
# removes exactly this job's containers and its own EXIT trap removes what it
# started if it fails part-way.
SUFFIX="${GITHUB_RUN_ID:-local}-${GITHUB_JOB:-job}-${GITHUB_RUN_ATTEMPT:-1}-$$-$RANDOM"
LABEL="tortoise-ci-falkordb=${SUFFIX}"
NAME_PW="falkordb-6673-pw-${SUFFIX}"
NAME_LEGACY="falkordb-6673-legacy-${SUFFIX}"

# Export the exact label BEFORE anything is created, so the paired teardown
# step can always select this job's containers (the fallback is that the
# EXIT trap below has already removed them).
echo "TORTOISE_CI_FALKORDB_LABEL=$LABEL" >> "$GITHUB_ENV"

# A failure mid-provision (image pull, port read, PING gate) must not leave a
# running container eating memory on the runner host. The trap is DISARMED on
# the success path below: it fires on a normal exit too, so leaving it armed
# would delete the containers this step just started — the false red this
# action exists to remove, with the ports already exported and dead.
cleanup_own() {
  docker rm -f "$NAME_PW" "$NAME_LEGACY" >/dev/null 2>&1 || true
  rm -f "$PORT_FILE"
}
trap cleanup_own EXIT

# The assigned port is handed back through a FILE, not stdout. Two reasons:
# `$( )` capture would swallow any diagnostic written inside start(), and
# GitHub renders an `::error::` annotation only for a workflow command that
# starts the line on the step's stdout stream — so a captured stdout would
# quietly turn the failure annotation into plain text.
PORT_FILE="$(mktemp)"

# GitHub annotations must be the first thing on the line — no `#6673 ` prefix.
annotate_error() { printf '::error::%s\n' "$*"; }

# Logging goes to STDERR: stdout is reserved for GitHub workflow commands
# (`::error::` below must be the first thing on its line — a `#6673 ` prefix
# would make it plain text). The port does not travel on this stream at all;
# it goes through $PORT_FILE.
log() { printf '#6673 %s\n' "$*" >&2; }

# start <name> <REDIS_ARGS> <health-cli-auth-args>
# On success writes the ASSIGNED host port to $PORT_FILE and returns 0. All
# diagnostics go to stderr; on failure it emits an ::error:: annotation on
# stdout (uncaptured) and returns non-zero.
start() {
  local name="$1" redis_args="$2" auth="$3" port i

  # A re-run of the same (run, job, attempt) cannot happen, but a leftover
  # from a killed attempt can — never inherit its port.
  docker rm -f "$name" >/dev/null 2>&1 || true

  # -p 0:6379 asks Docker for ANY free host port (the collision fix).
  # --rm  removes the container when it STOPS (a normal exit, a crash, or a
  #       `docker stop`) — it is not a job-death reaper: a hard-killed runner
  #       can leave a RUNNING container, attributable by $LABEL.
  docker run -d --rm --name "$name" --label "$LABEL" \
    -e REDIS_ARGS="$redis_args" \
    -p 0:6379 \
    "$IMAGE" >/dev/null

  # `docker port` prints one line per address family ("0.0.0.0:PORT" and
  # "[::]:PORT"); take the first and keep the port.
  port="$(docker port "$name" 6379/tcp | head -n 1 | sed 's/.*://')"
  if [ -z "$port" ]; then
    annotate_error "$name got no host port from docker"
    docker logs "$name" 2>&1 | tail -n 40 >&2 || true
    return 1
  fi

  # Health gate — the `services:` health-cmd equivalent (5s x 10 retries),
  # widened to 60s because this also covers the image pull on a cold runner.
  for i in $(seq 1 "$HEALTH_TIMEOUT"); do
    # shellcheck disable=SC2086  # $auth is a deliberate 0-or-2 word "cli args"
    if docker exec "$name" redis-cli $auth ping 2>/dev/null | grep -q PONG; then
      log "$name healthy on host port $port after ${i}s"
      printf '%s' "$port" > "$PORT_FILE"
      return 0
    fi
    sleep 1
  done
  annotate_error "$name did not answer PING within ${HEALTH_TIMEOUT}s (host port $port)"
  docker logs "$name" 2>&1 | tail -n 40 >&2 || true
  return 1
}

log "starting passworded service from $IMAGE (label $LABEL)"
start "$NAME_PW" "--requirepass falkordb --save ''" "-a falkordb"
PW_PORT="$(cat "$PORT_FILE")"
echo "TORTOISE_TEST_DOCKER_PORT=$PW_PORT" >> "$GITHUB_ENV"

if [ "$LEGACY" = "true" ]; then
  log "starting passwordless legacy service"
  start "$NAME_LEGACY" "--save ''" ""
  LG_PORT="$(cat "$PORT_FILE")"
  echo "TORTOISE_TEST_LEGACY_PORT=$LG_PORT" >> "$GITHUB_ENV"
fi

if [ -n "$URI_GRAPH" ]; then
  echo "TORTOISE_DB_URI=docker://:falkordb@localhost:${PW_PORT}/${URI_GRAPH}" >> "$GITHUB_ENV"
  log "exported TORTOISE_DB_URI for graph $URI_GRAPH on port $PW_PORT"
fi

# SUCCESS: disarm the EXIT trap. It fires on a normal exit as well, so leaving
# it armed removes the containers just started — every later step would then
# dial a closed port and the availability-skip guards would red.
trap - EXIT
rm -f "$PORT_FILE"
log "provisioned $IMAGE (label $LABEL) — containers stay up for this job"
