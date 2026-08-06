#!/usr/bin/env bash
# Tortoise memory injection for Claude Code — SessionStart hook.
#
# Install (once, per project):
#   mkdir -p .claude/hooks
#   cp tortoise/claude-hooks/session-start.sh .claude/hooks/session-start.sh
#   chmod +x .claude/hooks/session-start.sh
#   # then add to .claude/settings.json:
#   #   { "hooks": { "SessionStart": [{ "matcher": "", "hooks": [{ "type": "command", "command": ".claude/hooks/session-start.sh" }] }] } }
#
# The hook prints a Tortoise memory digest to stdout, which Claude Code
# injects into the session context automatically. If Tortoise isn't
# reachable (offline, not installed), it exits 0 silently so the session
# starts normally.

set -euo pipefail

# Prefer a local install; fall back to the repo checkout.
TORTOISE_BIN="$(command -v tortoise || true)"
if [ -z "$TORTOISE_BIN" ]; then
  # Source tree fallback (this repo checked out).
  TORTOISE_MODULE="${TORTOISE_SRC_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
  if [ ! -d "$TORTOISE_MODULE/tortoise" ]; then
    exit 0
  fi
  PYTHON_BIN="$(command -v python3 || true)"
  [ -z "$PYTHON_BIN" ] && exit 0
  "$PYTHON_BIN" -c "
import sys
sys.path.insert(0, '$TORTOISE_MODULE')
from tortoise.__main__ import main
raise SystemExit(main(['context']))
" 2>/dev/null || exit 0
else
  tortoise context 2>/dev/null || exit 0
fi
