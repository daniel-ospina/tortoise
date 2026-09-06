#!/usr/bin/env bash
# Tortoise per-turn volunteering-memory injection (epic #2080 end-state seams).
#
# UserPromptSubmit-style hook for the Claude-hooks-compatible harness family
# (Claude Code, Codex, Cline, Devin). Reads the hook's stdin JSON, extracts
# the user prompt, runs the ONE canonical reflex (`tortoise volunteer` — the
# shared tortoise/volunteer.py pipeline via POST /v1/context or the local
# SDK), and emits the per-harness hook output JSON carrying the injected
# context block.
#
# Install (per harness — all four wire a UserPromptSubmit hook that runs
# this script with a harness arg):
#   .codex/hooks.json       → "UserPromptSubmit": "…/volunteer-turn.sh codex"
#   .claude/settings.json   → "UserPromptSubmit": "…/volunteer-turn.sh claude"
#   .cline/…                → "UserPromptSubmit": "…/volunteer-turn.sh cline"
#   .devin/hooks.v1.json    → "UserPromptSubmit": "…/volunteer-turn.sh devin"
# or agent-first: `tortoise install codex` (writes the registration file —
# the #2123/#2124 agent-first onboarding mandate).
#
# Fail-open contract: if Tortoise is unreachable / misconfigured / the graph
# has nothing above the confidence gate, the script exits 0 with EMPTY output
# (the harness injects nothing; the turn proceeds untouched). A reflex
# failure must NEVER break the agent turn.
#
# Hook input contract (Claude/Codex UserPromptSubmit): stdin JSON with a
# top-level "prompt" string; older/other harnesses send a text blob. Both
# are accepted.
set -euo pipefail

HARNESS="${1:-codex}"
IN="$(cat)"

# ── Extract the user prompt from the hook input ──────────────────────────
PROMPT=""
if [ -n "$IN" ]; then
  PROMPT="$(printf '%s' "$IN" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # not JSON — caller falls back to the raw stdin text
if isinstance(d, str):
    print(d)
elif isinstance(d, dict):
    p = d.get("prompt") or d.get("userPrompt") or ""
    if isinstance(p, str):
        print(p)
' 2>/dev/null || true)"
fi
if [ -z "$PROMPT" ]; then
  PROMPT="$IN"
fi
PROMPT="$(printf '%s' "$PROMPT" | tr -d '\r' | head -c 15000 || true)"
if [ -z "$(printf '%s' "$PROMPT" | tr -d '[:space:]')" ]; then
  exit 0
fi

# ── Resolve the reflex entry (PATH install → repo .venv → module) ───────
TORTOISE_BIN="$(command -v tortoise || true)"
TORTOISE_MODULE=""
if [ -z "$TORTOISE_BIN" ]; then
  TORTOISE_MODULE="${TORTOISE_SRC_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
  if [ -x "$TORTOISE_MODULE/.venv/bin/tortoise" ]; then
    TORTOISE_BIN="$TORTOISE_MODULE/.venv/bin/tortoise"
  elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/tortoise" ]; then
    TORTOISE_BIN="$VIRTUAL_ENV/bin/tortoise"
  elif [ ! -d "$TORTOISE_MODULE/tortoise" ]; then
    exit 0  # no tortoise install or repo checkout — clean silence
  fi
fi
# Python fallback for a source checkout: prefer the checkout's own venv so
# the module runs under an interpreter that actually has tortoise installed.
PYTHON_BIN=""
if [ -z "$TORTOISE_BIN" ]; then
  if [ -x "$TORTOISE_MODULE/.venv/bin/python" ]; then
    PYTHON_BIN="$TORTOISE_MODULE/.venv/bin/python"
  elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python"
  else
    PYTHON_BIN="$(command -v python3 || true)"
  fi
  [ -z "$PYTHON_BIN" ] && exit 0
fi

# python3 (hook-input JSON parsing + per-harness output assembly) is a hard
# requirement once we reach emission — preflight it so a missing interpreter
# can never violate the fail-open exit-0 contract.
PY3="$(command -v python3 || true)"
[ -z "$PY3" ] && exit 0

# ── Run the reflex (fail-open: any failure → empty injection) ────────────
BLOCK=""
if [ -n "$TORTOISE_BIN" ]; then
  BLOCK="$(printf '%s' "$PROMPT" | "$TORTOISE_BIN" volunteer 2>/dev/null || true)"
else
  # Module fallback: the checkout path travels via ENV (never string-
  # interpolated into python -c source — a quote in the path must not inject
  # code). The -c body reads TORTOISE_VOLUNTEER_MODULE from os.environ.
  BLOCK="$(printf '%s' "$PROMPT" | TORTOISE_VOLUNTEER_MODULE="$TORTOISE_MODULE" "$PYTHON_BIN" -c "
import os, sys
sys.path.insert(0, os.environ['TORTOISE_VOLUNTEER_MODULE'])
from tortoise.__main__ import main
raise SystemExit(main(['volunteer']))
" 2>/dev/null || true)"
fi
if [ -z "$(printf '%s' "$BLOCK" | tr -d '[:space:]')" ]; then
  exit 0
fi

# ── Emit the per-harness hook output contract (block via env — safe for
#    quotes/backslashes/newlines) ─────────────────────────────────────────
export TORTOISE_VOLUNTEER_BLOCK="$BLOCK"
case "$HARNESS" in
  claude|codex|devin)
    # Claude-hooks-shaped: hookSpecificOutput.additionalContext (Codex
    # UserPromptSubmit and Devin hooks.v1 use the identical contract).
    "$PY3" -c "
import json, os
print(json.dumps({'hookSpecificOutput': {
    'hookEventName': 'UserPromptSubmit',
    'additionalContext': os.environ.get('TORTOISE_VOLUNTEER_BLOCK', '')}}))
" 2>/dev/null || true
    ;;
  cline)
    # Cline UserPromptSubmit → contextModification.context.
    "$PY3" -c "
import json, os
print(json.dumps({'contextModification': {
    'context': os.environ.get('TORTOISE_VOLUNTEER_BLOCK', '')}}))
" 2>/dev/null || true
    ;;
  *)
    # Unknown harness — print the block plainly (harmless where unsupported).
    printf '%s\n' "$BLOCK"
    ;;
esac
exit 0
