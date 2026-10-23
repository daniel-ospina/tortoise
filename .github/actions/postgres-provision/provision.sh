#!/usr/bin/env bash
# #9551 (R3) — provision a throwaway Postgres on an EPHEMERAL host port.
#
# The record's two Postgres legs (`tests/test_9078_record_written_by_production_path.py`)
# SKIP unless `TEST_RECORD_DB_URI` names a database carrying the record
# migrations, and no CI job armed it — so a schema↔producer disagreement was
# only ever caught by an operator. This action arms that leg in CI.
#
# WHY AN ACTION AND NOT A `services:` BLOCK (#6673, the same defect the FalkorDB
# pair was built for): a `services:` container is created by the runner under a
# runner-generated name and carries only a runner-start label, so the job has no
# way to identify ITS containers among the host's concurrent jobs and cannot
# read back an assigned port; and a FIXED host port is a single global namespace
# on a shared Docker daemon (two overlapping jobs → "Bind for 0.0.0.0:5432
# failed: port is already allocated" → the runner aborts pre-step → false red
# with zero tests run). `python-ci.yml` is forbidden a `services:` block by
# `tests/test_runner_ports_6673.py::test_no_fixed_port_service_mapping_anywhere`.
#
# Also `tests/conftest.py` fails the session unless `TORTOISE_DB_URI` is set OR
# the caller opted into the URI-less shape (epic #1647 P4) — this action exports
# no graph URI on purpose: the record's database is Postgres, and the caller
# sets `TORTOISE_TEST_CARVE_OUT=1` for the graph. The job then asserts ZERO
# skips, so the opt-in cannot be how the leg "passes".
#
# Exports (via $GITHUB_ENV, so every later step sees them):
#   TEST_RECORD_DB_URI          postgresql://<user>:<pass>@127.0.0.1:<port>/<db>
#   TORTOISE_CI_POSTGRES_LABEL  the ownership label the paired teardown selects
set -euo pipefail

IMAGE="${POSTGRES_IMAGE:?}"
HEALTH_TIMEOUT="${POSTGRES_HEALTH_TIMEOUT:-60}"
DB_NAME="${POSTGRES_DB_NAME:-record}"
DB_USER="${POSTGRES_DB_USER:-record}"
DB_PASS="${POSTGRES_DB_PASSWORD:-record}"

log() { printf '#9551 %s\n' "$*" >&2; }
annotate_error() { printf '::error::%s\n' "$*"; }

# Fail closed on any value that is not a plain identifier, BEFORE anything is
# pulled or created. All three are concatenated into a URI that is written
# verbatim into $GITHUB_ENV, which is line-delimited — a newline in any of them
# would append ARBITRARY env vars for every later step of the job. All callers
# pass the defaults today, but this action is reusable and that is not a
# property to depend on.
for pair in "database:$DB_NAME" "username:$DB_USER" "password:$DB_PASS"; do
  case "${pair#*:}" in
    ''|*[!A-Za-z0-9_]*)
      annotate_error "postgres ${pair%%:*} must be a plain identifier (A-Za-z0-9_); refusing a value that could inject into GITHUB_ENV or break the URI"
      exit 1
      ;;
  esac
done

# A reference's first path component is a REGISTRY HOST only when it contains a
# '.' or a ':' (or is literally `localhost`) — Docker's own rule. So
# `postgres:16` is Docker Hub (two components? no: a SLASHLESS reference has no
# `/`, so `${1%%/*}` is the whole string and the `*:*` arm would misread the tag
# as a registry — hence the explicit slash test), while `mirror.gcr.io/…` is not.
image_candidates() {
  case "$1" in
    */*) case "${1%%/*}" in
           *.*|*:*|localhost) printf '%s' "$1"; return 0 ;;
         esac ;;
  esac
  printf '%s\n%s' "mirror.gcr.io/$1" "$1"
}

# Best-first references, one per word (a reference never contains a space).
# shellcheck disable=SC2207  # deliberate word-splitting of a reference list
IMAGE_CANDIDATES=($(image_candidates "$IMAGE"))

# Resolve ONCE, before any container starts. A warm self-hosted runner usually
# already HAS the image — and that is the common case here, since the daemon is
# shared — so the local check runs first across every candidate: a rate-limited
# or unreachable registry must not fail a job that needs no pull at all (#7867).
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
  annotate_error "could not obtain '$IMAGE' from any of: ${IMAGE_CANDIDATES[*]} — no container can start, so no record leg can run (#7867)"
  exit 1
fi

# Ownership label: one value per JOB INSTANCE. The teardown step selects on it,
# so this job removes exactly its own container. GITHUB_JOB is the job_id and is
# NOT unique among concurrent shards of one matrix, so the run/job/attempt
# triple is not enough either — the instance token makes it unique.
SUFFIX="${GITHUB_RUN_ID:-local}-${GITHUB_JOB:-job}-${GITHUB_RUN_ATTEMPT:-1}-$$-$RANDOM"
LABEL="tortoise-ci-postgres=${SUFFIX}"
NAME="tortoise-ci-postgres-${SUFFIX}"

# Export the exact label BEFORE anything is created, so the paired teardown can
# always select this job's container (the fallback is the EXIT trap below).
echo "TORTOISE_CI_POSTGRES_LABEL=$LABEL" >> "$GITHUB_ENV"

# A failure mid-provision (pull, port read, health gate) must not leave a
# container holding memory on the runner host. Disarmed on the success path:
# the trap fires on a normal exit too, so leaving it armed would delete the
# container this step just started — the false red this action exists to remove.
cleanup_own() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup_own EXIT

# -p 127.0.0.1:0:5432 is the collision fix AND the loopback bind (#7637):
#   * `0` host port → Docker assigns ANY free port (the #6673 collision fix),
#   * `127.0.0.1` host IP → the publish is LOOPBACK ONLY.
# A bare `-p 0:5432` assigns the port but leaves the host address unset, and
# Docker resolves that to 0.0.0.0 — putting a test database on the LAN of every
# lane/developer machine. Every consumer here dials 127.0.0.1, so restricting the
# bind costs the CI path nothing.
if ! docker run -d --name "$NAME" --label "$LABEL" \
    -e POSTGRES_PASSWORD="$DB_PASS" -e POSTGRES_USER="$DB_USER" \
    -e POSTGRES_DB="$DB_NAME" \
    -p 127.0.0.1:0:5432 \
    "$RUN_IMAGE" >/dev/null; then
  # Annotated explicitly: `set -e` would otherwise abort with docker's stderr as
  # the only trace, and the contract is that every failure here is visible.
  annotate_error "$NAME failed to start (docker run) — see the docker error above"
  exit 1
fi

# The loopback bind above prints a single "127.0.0.1:PORT" line; take its port.
PORT="$(docker port "$NAME" 5432/tcp | head -n 1 | sed 's/.*://')"
# Digits only, not merely non-empty: $PORT is exported into $GITHUB_ENV and
# interpolated into the URI, so a truncated/error line must never get there.
case "$PORT" in
  ''|*[!0-9]*)
    annotate_error "$NAME got no usable host port from docker (got '$PORT')"
    docker logs "$NAME" 2>&1 | tail -n 40 >&2 || true
    exit 1
    ;;
esac

# Health gate — an AUTHENTICATED TCP round trip as the role/database this action
# exports, which is the exact thing the tests will do.
#
# ⚠️ NOT `pg_isready` over the unix socket: the official image starts a TEMPORARY
# server while it runs initdb with `listen_addresses=''`, so a unix-socket probe
# answers "accepting connections" from a server that is about to be torn down
# and replaced — the gate would pass, then the first test would dial a closed
# TCP port. `-h 127.0.0.1` can only succeed once the REAL server is up, and
# `-U/-d` prove the exported role and database exist (initdb race: while the
# container restarts, `docker exec` fails and the loop simply retries).
ready=0
for i in $(seq 1 "$HEALTH_TIMEOUT"); do
  if docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null | grep -q true \
     && docker exec -e PGPASSWORD="$DB_PASS" "$NAME" \
          psql -h 127.0.0.1 -p 5432 -U "$DB_USER" -d "$DB_NAME" -tAc 'SELECT 1' \
          >/dev/null 2>&1; then
    log "$NAME healthy on host port $PORT after ${i}s"
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  annotate_error "$NAME did not accept an authenticated TCP connection as '$DB_USER'@'$DB_NAME' within ${HEALTH_TIMEOUT}s (host port $PORT) — every record leg would fail on a dead database, so the leg reds HERE and names the cause instead"
  docker logs "$NAME" 2>&1 | tail -n 40 >&2 || true
  exit 1
fi

echo "TEST_RECORD_DB_URI=postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:${PORT}/${DB_NAME}" >> "$GITHUB_ENV"
log "exported TEST_RECORD_DB_URI for database $DB_NAME on port $PORT"

# SUCCESS: disarm the EXIT trap. It fires on a normal exit as well, so leaving it
# armed removes the container just started — every later step would then dial a
# closed port.
trap - EXIT
log "provisioned $RUN_IMAGE (label $LABEL) — container stays up for this job"
