#!/usr/bin/env bash
# Capstone #13038 — one command to IGNORE the machine's shared FalkorDB and run the
# assessment against a private throwaway one.
#
#   bash docs/evidence/13038-capstone/rerun.sh --out /tmp/capstone-run
#                                                          # ingest + ask, no verdicts yet
#   bash docs/evidence/13038-capstone/rerun.sh --judge fresh  # also spawn a fresh reader
#
# `tools/test_lane.py run` runs PYTEST, so it cannot take the instrument's own argv;
# the `uri` route is the documented way to get a private lane's four seam variables.
set -euo pipefail
cd "$(dirname "$0")/../../.."   # worktree root

uri="$(uv run python tools/test_lane.py uri)" || {
    echo "capstone: could not start the private test lane (see the message above)" >&2
    exit 1
}
eval "$uri"

exec uv run python tools/capstone_state_value.py run --target local "$@"
