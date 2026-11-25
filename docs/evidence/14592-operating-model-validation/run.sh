#!/usr/bin/env bash
# #14592 Phase D — the one way to run this validation.
#
# NO box-bound path and NO credential store is baked into this script: both are
# caller-supplied, so the run reproduces anywhere the two prerequisites below are
# met. The two MEASURED deviations of the 2026-11-25 run are documented in the
# report (§9) and are supplied by the operator through these two variables, never
# by a literal in this file:
#   1. `TORTOISE`-style `uv sync` could not build the worktree's venv on the cmux
#      cell: PyPI DNS resolution failed (`failed to lookup address information:
#      Temporary failure in name resolution` for files.pythonhosted.org, retried
#      over ~1h, while api.github.com resolved). That run therefore pointed
#      TORTOISE_VENV at the MAIN checkout's venv and pinned PYTHONPATH at this
#      worktree — `import tortoise` then resolves to the worktree's code, which
#      is what the validation must measure.
#   2. No LLM provider key was exported in that shell. The product's v4 path
#      reads DEEPSEEK_API_KEY (`tortoise/sdk.py` `_session_llm_provider`), so the
#      operator exported it from the box's own store before running this script.
#      This script never reads a credential file itself.
#
# Prerequisites:
#   TORTOISE_DB_URI   the private lane FalkorDB graph (the 2026-11-25 run used
#                     'docker://:@127.0.0.1:16467/tortoise_test_matrix' — this
#                     worktree's own container, never the shared 6379 instance,
#                     never live-v5)
#   DEEPSEEK_API_KEY  the extraction LLM credential — required ONLY by
#                     `run_ingest.py` (and by any phase you force with
#                     TORTOISE_REQUIRE_LLM_KEY=1). The read-only phases
#                     (`run_readback.py`, `run_census.py`, `run_census2.py`,
#                     `run_census3.py`) make no model call, so demanding a key
#                     for them aborted the report's own read-only recipe
#                     before Python started. Never read from a credential file.
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
# the driver. Requiring it unconditionally made every read-only step of the
# report's recipe abort on `set -u` with no Python ever run.
if [ "$(basename "${1:-}")" = "run_ingest.py" ] || [ "${TORTOISE_REQUIRE_LLM_KEY:-0}" = "1" ]; then
  export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:?set DEEPSEEK_API_KEY — this script reads no credential store}"
elif [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  export DEEPSEEK_API_KEY
fi

cd "$WT"
exec "$PY" "$@"
