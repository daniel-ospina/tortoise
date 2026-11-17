#!/usr/bin/env bash
# Capstone #13038 — one command to IGNORE the machine's shared FalkorDB and run the
# assessment against a private throwaway one.
#
#   bash docs/evidence/13038-capstone/rerun.sh                 # ingest + ask, to a SCRATCH dir
#   bash docs/evidence/13038-capstone/rerun.sh --out DIR       # …to the directory you name
#   bash docs/evidence/13038-capstone/rerun.sh --judge fresh   # also spawn a fresh reader
#
# ⚠️ The UNJUDGED form may not publish into the evidence bundle: an all-`unjudged` board is
# not a baseline and would replace `run-local.json` / `scoreboard-local.md` (the instrument
# refuses it, exit 1). With no `--out` this wrapper therefore defaults to a scratch
# directory; a SCORED run (`--judge fresh|file`) may publish into the bundle.
#
# `tools/test_lane.py run` runs PYTEST, so it cannot take the instrument's own argv;
# the `uri` route is the documented way to get a private lane's four seam variables.
set -euo pipefail

# Default the output directory to a SCRATCH path for the UNJUDGED form only. An explicit
# --out is the caller's decision, and a scoring --judge may publish into the bundle.
out_default=()
has_out=0
has_judge=0
for arg in "$@"; do
    case "$arg" in
        --out|--out=*) has_out=1 ;;
        --judge|--judge=*) has_judge=1 ;;
    esac
done
if [ "$has_out" -eq 0 ] && [ "$has_judge" -eq 0 ]; then
    out_default=(--out "${TMPDIR:-/tmp}/capstone-13038-unjudged")
fi

cd "$(dirname "$0")/../../.."   # worktree root

uri="$(uv run python tools/test_lane.py uri)" || {
    echo "capstone: could not start the private test lane (see the message above)" >&2
    exit 1
}
eval "$uri"

exec uv run python tools/capstone_state_value.py run --target local \
    ${out_default[@]+"${out_default[@]}"} "$@"
