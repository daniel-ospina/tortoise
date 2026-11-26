#!/usr/bin/env bash
# #14691 T6 — the one way to run this validation.
#
# Inherited from #14606 (`docs/evidence/14592-operating-model-validation/run.sh`)
# and kept deliberately identical in shape: NO box-bound path and NO credential
# store is baked in. Both inputs are caller-supplied, so the run reproduces
# anywhere the prerequisites are met.
#
# Prerequisites:
#   TORTOISE_DB_URI   the private lane FalkorDB graph. This lane used
#                     `docker://:@127.0.0.1:16413/tortoise_test_matrix` — ITS OWN
#                     container (`tools/test_lane.py uri`), never the shared 6379
#                     instance, never `live-v5` (recorded decision (d)).
#   DEEPSEEK_API_KEY  the extraction LLM credential — required ONLY by
#                     `run_ingest.py`. The read-only phases (`run_readback.py`,
#                     `score_t6.py`) make no model call. Never read from a file.
# Optional:
#   TORTOISE_VENV     interpreter root (default: <worktree>/.venv)
#   TORTOISE_REQUIRE_LLM_KEY=1  force the key requirement for any phase
set -euo pipefail

WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"   # worktree root
VENV="${TORTOISE_VENV:-$WT/.venv}"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
  echo "no interpreter at $PY — set TORTOISE_VENV (e.g. TORTOISE_VENV=/path/to/checkout/.venv)" >&2
  exit 1
fi

export PYTHONPATH="$WT"
export TORTOISE_DB_URI="${TORTOISE_DB_URI:?set TORTOISE_DB_URI (see header)}"

# The extraction credential belongs to the phase that makes the LLM call, not to
# the driver (#14606 review: requiring it unconditionally aborted every read-only
# step of the recipe before Python started).
if [ "$(basename "${1:-}")" = "run_ingest.py" ] || [ "${TORTOISE_REQUIRE_LLM_KEY:-0}" = "1" ]; then
  export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:?set DEEPSEEK_API_KEY — this script reads no credential store}"
elif [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  export DEEPSEEK_API_KEY
fi

cd "$WT"
exec "$PY" "$@"
