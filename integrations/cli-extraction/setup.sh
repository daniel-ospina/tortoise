#!/usr/bin/env bash
# Prerequisite checks for the CLI-extraction lane. Exits nonzero on a missing
# requirement. Nothing is installed or modified — this only verifies.
set -euo pipefail

fail=0
need() {  # need <label> <check-command...>
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo "  ok      $label"
    else
        echo "  MISSING $label"
        fail=1
    fi
}

compose="${TORTOISE_COMPOSE_FILE:-$HOME/tortoise/docker-compose.yml}"
service="${TORTOISE_SERVICE:-tortoise}"

echo "Tortoise container:"
need "docker compose file ($compose)" test -f "$compose"
# `compose ps -q` exits 0 with EMPTY output when nothing is running, so the
# daemon check must assert non-empty: an empty id list means dump/write will
# fail at the first exec.
if [ -n "$(docker compose -f "$compose" ps -q "$service" 2>/dev/null)" ]; then
    echo "  ok      daemon reachable (container up: $service)"
else
    echo "  MISSING daemon reachable (no running container for service $service)"
    fail=1
fi

echo "Engines (at least one required):"
engines=0
if command -v "${TORTOISE_CLAUDE_AUTH:-claude}" >/dev/null 2>&1; then
    echo "  ok      claude (${TORTOISE_CLAUDE_AUTH:-claude on PATH})"; engines=$((engines+1))
else
    echo "  absent  claude (claude CLI on PATH, or a wrapper via TORTOISE_CLAUDE_AUTH)"
fi
if [ -x "${CLAUDE_GLM_BIN:-$HOME/.local/bin/claude-glm}" ]; then
    echo "  ok      glm (claude-glm wrapper)"; engines=$((engines+1))
else
    echo "  absent  glm (claude-glm wrapper)"
fi
if command -v "${CODEX_BIN:-codex}" >/dev/null 2>&1; then
    echo "  ok      codex"; engines=$((engines+1))
else
    echo "  absent  codex"
fi
[ "$engines" -ge 1 ] || { echo "  no engine available — install at least one CLI"; fail=1; }

echo "Python:"
need "python3" python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"

if [ "$fail" -ne 0 ]; then
    echo "setup: FAILED" >&2
    exit 1
fi
echo "setup: all prerequisites present."
echo "next: python3 extract_turns.py dump   # then run/write (see README.md)"
