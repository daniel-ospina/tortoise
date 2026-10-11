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
# Health-gate bound (seconds) for the PING loop — NOT a bound on the image
# pull. The pull is resolved explicitly below, before any container starts, so
# its only bound is the step/job `timeout-minutes`.
HEALTH_TIMEOUT="${FALKORDB_HEALTH_TIMEOUT:-60}"

# #7867: Docker Hub rate-limits ANONYMOUS pulls per egress IP (100 / 3600 s —
# measured against the live token endpoint; the issue body's "6 h" is wrong),
# and the fleet's self-hosted runners share that egress. One burst exhausted the
# quota, after which EVERY container job in python-ci died at `docker run` —
# before pytest ran, so the run carried no `FAILED <nodeid>` line and the merge
# rail correctly refused it with no attributable failure identity.
#
# Fix direction B — the one that needs NOTHING from the owner: a first-party
# GHCR copy, which authenticates with `GITHUB_TOKEN`, so there is no anonymous
# Docker Hub quota to exhaust and no credential to provision (direction A would
# need a DOCKERHUB_USERNAME/TOKEN secret). .github/workflows/mirror-images.yml
# populates it.
#
# Candidate order, most-preferred first. Every tier is a PREFERENCE rather than
# a replacement, so no single registry can darken this surface again. The
# ordering is deliberate and was corrected once: a tier that CANNOT SERVE YET
# must never lead, because then it fails the pull for a new reason and the matrix
# stays dark — the fix cannot validate itself while it prefers an empty mirror.
# GHCR is the durable sanctioned home, but on the very PR that adds the mirror it
# is empty (a workflow_dispatch workflow is dispatchable only from the default
# branch), so the pull-through cache leads until the mirror is populated.
#   1. mirror.gcr.io  — Google's Docker Hub pull-through cache; NOT subject to
#                       the anonymous Docker Hub quota, so it serves today.
#   2. GHCR           — first-party, the durable home; authenticated when the
#                       action's best-effort login succeeds, so an unpopulated,
#                       private or unreachable mirror costs one failed attempt,
#                       never a job.
#   3. as given       — the Docker Hub upstream (today's behaviour).
log() { printf '#6673 %s\n' "$*" >&2; }
annotate_error() { printf '::error::%s\n' "$*"; }

GHCR_OWNER="${FALKORDB_GHCR_OWNER:-${GITHUB_REPOSITORY_OWNER:-daniel-ospina}}"
# GHCR requires a lowercase path; the mirror workflow and the python-ci leg both
# derive the owner dynamically, so this end must too — a hardcoded owner would
# silently stop matching after a transfer, rename or fork (masked, because
# mirror.gcr.io leads).
GHCR_OWNER="$(printf '%s' "$GHCR_OWNER" | tr '[:upper:]' '[:lower:]')"
MIRROR_HOST="${FALKORDB_MIRROR_HOST:-mirror.gcr.io}"

# Fail closed on a graph name that is not a plain identifier, BEFORE anything is
# created OR pulled: it is written verbatim into $GITHUB_ENV, which is
# line-delimited, so a newline in the value would append ARBITRARY env vars for
# every later step of the job. All five callers pass a literal today — but this
# action is reusable, and this is not a property to depend on. It must also
# precede the image resolver: refusing an invalid input must not first spend a
# pull (Docker Hub quota) or leave docker side effects for the refusal path.
if [ -n "$URI_GRAPH" ]; then
  case "$URI_GRAPH" in
    *[!A-Za-z0-9_-]*)
      annotate_error "uri_graph must be a plain graph identifier (A-Za-z0-9_-); refusing a value that could inject into GITHUB_ENV"
      exit 1
      ;;
  esac
fi

# A reference's first path component is a REGISTRY HOST only when it contains a
# '.' or a ':' (or is literally `localhost`) — Docker's own rule. So
# `falkordb/falkordb-server:v4.20.6` is Docker Hub, while `mirror.gcr.io/...`
# and `localhost:5000/...` are not. An already-qualified reference is used AS
# GIVEN: this action must not silently rewrite a caller's explicit registry.
image_candidates() {
  # The slash test is load-bearing: without it a SLASHLESS Docker Hub reference
  # such as `redis:7` has no `/`, so `${1%%/*}` is the whole string, it matches
  # `*:*`, and the reference is misread as an already-qualified registry — the
  # mirror tiers are then never attempted. A first component is a host only when
  # a `/` separates it from the rest.
  case "$1" in
    */*) case "${1%%/*}" in
           *.*|*:*|localhost) printf '%s' "$1"; return 0 ;;
         esac ;;
  esac
  printf '%s\n%s\n%s' \
    "$MIRROR_HOST/$1" \
    "ghcr.io/$GHCR_OWNER/${1##*/}" \
    "$1"
}

# Best-first image references, one per word (a reference never contains a space).
# shellcheck disable=SC2206  # deliberate word-splitting of a reference list
IMAGE_CANDIDATES=($(image_candidates "$IMAGE"))

# Resolve ONCE, before any container starts. A warm self-hosted runner usually
# already HAS the image, so the local check runs first across every candidate: a
# rate-limited registry must not fail a job that needs no pull at all (that is
# the whole defect).
RUN_IMAGE=""
for candidate in "${IMAGE_CANDIDATES[@]}"; do
  if docker image inspect "$candidate" >/dev/null 2>&1; then
    RUN_IMAGE="$candidate"
    log "image $candidate already present locally (requested $IMAGE)"
    break
  fi
done
if [ -z "$RUN_IMAGE" ]; then
  for candidate in "${IMAGE_CANDIDATES[@]}"; do
    if docker pull "$candidate" >/dev/null 2>&1; then
      RUN_IMAGE="$candidate"
      log "pulled $candidate (requested $IMAGE)"
      break
    fi
    log "pull failed for $candidate — trying the next source"
  done
fi
if [ -z "$RUN_IMAGE" ]; then
  annotate_error "could not obtain '$IMAGE' from any of: ${IMAGE_CANDIDATES[*]} — no container can start, so no test in this job can run (#7867)"
  exit 1
fi

# log/annotate_error are defined ABOVE, before their first use: this block
# reports through them, and defining them below it aborted the whole action
# under `set -e` (127: command not found) on every path — no container started,
# no port was exported, and the matrix went dark for a reason that had nothing to
# do with any registry.

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

# start <name> <REDIS_ARGS> <health-cli-auth-args>
# On success writes the ASSIGNED host port to $PORT_FILE and returns 0. All
# diagnostics go to stderr; on failure it emits an ::error:: annotation on
# stdout (uncaptured) and returns non-zero.
start() {
  local name="$1" redis_args="$2" auth="$3" port i

  # A re-run of the same (run, job, attempt) cannot happen, but a leftover
  # from a killed attempt can — never inherit its port.
  docker rm -f "$name" >/dev/null 2>&1 || true

  # -p 127.0.0.1:0:6379 is the collision fix AND the loopback bind (#7637):
  #   * `0` host port → Docker assigns ANY free port (the #6673 collision fix),
  #   * `127.0.0.1` host IP → the publish is LOOPBACK ONLY.
  # A bare `-p 0:6379` assigns the port but leaves the host address unset, and
  # Docker resolves that to 0.0.0.0 — putting an unauthenticated, password-less
  # (legacy service) test DB on the LAN of every lane/developer machine, where
  # it accumulates silently (measured 2026-10-07: two such containers live).
  # Every consumer of this action dials localhost (the exported
  # TORTOISE_TEST_DOCKER_PORT/_LEGACY_PORT are read by `tests/_live_utils.py`
  # with host `localhost`), so restricting the bind costs the CI path nothing.
  # --rm  removes the container when it STOPS (a normal exit, a crash, or a
  #       `docker stop`) — it is not a job-death reaper: a hard-killed runner
  #       can leave a RUNNING container, attributable by $LABEL.
  if ! docker run -d --rm --name "$name" --label "$LABEL" \
      -e REDIS_ARGS="$redis_args" \
      -p 127.0.0.1:0:6379 \
      "$RUN_IMAGE" >/dev/null; then
    # Annotated explicitly: `set -e` would otherwise abort with docker's stderr
    # as the only trace, and the contract is that every failure of this step is
    # a visible annotation.
    annotate_error "$name failed to start (docker run) — see the docker error above"
    return 1
  fi

  # `docker port` prints one line per address family (with a bare `-p`:
  # "0.0.0.0:PORT" and "[::]:PORT"); take the first and keep the port. The
  # loopback bind above prints a single "127.0.0.1:PORT" line, which this parses
  # identically — the assigned port still flows through $PORT_FILE.
  port="$(docker port "$name" 6379/tcp | head -n 1 | sed 's/.*://')"
  # Digits only, not merely non-empty: $port is exported into $GITHUB_ENV and
  # interpolated into a URI, and a truncated/error line from `docker port`
  # would otherwise be written there verbatim.
  case "$port" in
    ""|*[!0-9]*)
      annotate_error "$name got no usable host port from docker (got '$port')"
      docker logs "$name" 2>&1 | tail -n 40 >&2 || true
      return 1
      ;;
  esac

  # Health gate — the `services:` health-cmd equivalent (5s x 10 retries),
  # widened to 60s for slow starts on a loaded runner. It does NOT cover the
  # image pull, which provision.sh resolves explicitly BEFORE this loop (the
  # pull is bounded by the step/job timeout-minutes only).
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

log "starting passworded service from $RUN_IMAGE (label $LABEL)"
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
log "provisioned $RUN_IMAGE (label $LABEL) — containers stay up for this job"
