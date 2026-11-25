#!/usr/bin/env bash
# #14592 Phase D — the one way to run this validation.
#
# Two deviations from the repo's documented invocation, both measured on this
# box (cmux cell) and both recorded in the report:
#   1. `uv sync` cannot build this worktree's venv: PyPI DNS resolution fails
#      (`failed to lookup address information: Temporary failure in name
#      resolution` for files.pythonhosted.org, retried over ~1h, while
#      api.github.com resolves). So we run the MAIN checkout's venv with
#      PYTHONPATH pinned at this worktree — `import tortoise` then resolves to
#      the worktree's code (verified), i.e. the code under test is THIS
#      worktree at origin/main, which is what the validation must measure.
#   2. No LLM provider key is exported in the shell. The product's extractor v4
#      reads DEEPSEEK_API_KEY (tortoise/sdk.py `_session_llm_provider`), so we
#      export the box's existing deepseek credential from pi's auth store.
#
# The private lane container is this worktree's own FalkorDB (never the shared
# 6379 instance, never live-v5):
#   TORTOISE_DB_URI='docker://:@127.0.0.1:16467/tortoise_test_matrix'
set -euo pipefail

WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"   # worktree root
VENV=/home/cmux/tortoise/.venv
PY="$VENV/bin/python"

export PYTHONPATH="$WT"
export TORTOISE_DB_URI="${TORTOISE_DB_URI:?set TORTOISE_DB_URI (see header)}"
export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-$("$PY" -c 'import json,os;print(json.load(open(os.path.expanduser("~/.pi/agent/auth.json")))["deepseek"]["key"])')}"

cd "$WT"
exec "$PY" "$@"
