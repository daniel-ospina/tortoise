#!/usr/bin/env bash
# #14691 T1-T5 — the one way to run this validation.
#
# Inherited from #14691 T6 (`docs/evidence/14691-structure-validation/run.sh`) and
# deliberately identical in shape: NO box-bound path and NO credential store is
# baked in. Both inputs are caller-supplied, so the run reproduces anywhere the
# prerequisites are met.
#
# Prerequisites:
#   TORTOISE_DB_URI   the private lane FalkorDB URI. This lane used its own
#                     container (`uv run python tools/test_lane.py uri`) — never
#                     the shared 6379 instance, never `live-v5` (recorded decision
#                     (d)). The graph NAME is fixed in the scripts
#                     (`xv14691_noise`), so the run cannot land on another lane's
#                     data even if the container is shared.
#   DEEPSEEK_API_KEY  the extraction LLM credential — required ONLY by
#                     `run_ingest.py`. `run_readback.py` and `score_t1_t5.py`
#                     make no model call. Never read from a file.
# Optional:
#   TORTOISE_VENV     interpreter root (default: <worktree>/.venv)
#   TORTOISE_REQUIRE_LLM_KEY=1  force the key requirement for any phase
set -euo pipefail

WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"   # worktree root
VENV="${TORTOISE_VENV:-$WT/.venv}"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
  echo "no interpreter at $PY — set TORTOISE_VENV" >&2
  exit 1
fi

export PYTHONPATH="$WT"
export TORTOISE_DB_URI="${TORTOISE_DB_URI:?set TORTOISE_DB_URI (see header)}"

# The extraction credential belongs to the phase that makes the LLM call, not to
# the driver: requiring it unconditionally would abort the read-only steps of the
# recipe before Python starts.
if [ "$(basename "${1:-}")" = "run_ingest.py" ] || [ "${TORTOISE_REQUIRE_LLM_KEY:-0}" = "1" ]; then
  export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:?set DEEPSEEK_API_KEY — this script reads no credential store}"
elif [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  export DEEPSEEK_API_KEY
fi

cd "$WT"
exec "$PY" "$@"
