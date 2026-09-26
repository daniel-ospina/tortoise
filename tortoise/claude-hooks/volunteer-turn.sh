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

# ── Resolve the reflex entry (PATH install → module venv → module) ────
# The module dir is the root that CONTAINS `tortoise/`.  This hook does NOT
# live inside a checkout once installed — it is registered by absolute path in
# the harness's own config, often under `$HOME/.<harness>/hooks/`, where
# `dirname(BASH_SOURCE)/../..` is `$HOME`, not a checkout — so every candidate
# is accepted ONLY when it really contains `tortoise/`: the first such
# candidate wins, and a non-empty value that is not a checkout is SKIPPED,
# never trusted by position.  The installer records the module dir it
# installed FROM in `$HOME/.tortoise/hook-src-dir` (tortoise/hook_install.py),
# which is what makes an installed hook resolvable with no `tortoise` on PATH.
_tortoise_capture_error() {
  # The SAME local breadcrumb `sessions import` writes for a failed capture
  # (tortoise/__main__.py `_capture_error_file`: `$HOME/.tortoise/
  # capture-errors/<harness>.json`), so a hook that cannot run its reflex is
  # OBSERVABLE instead of silent.  Best-effort only: a breadcrumb must never
  # break the fail-open exit.
  _T_DIR="$(dirname "${TORTOISE_IMPORT_RECEIPT_DIR:-${HOME:-}/.tortoise/import-receipts}")/capture-errors"
  mkdir -p "$_T_DIR" 2>/dev/null || return 0
  _T_ESC="$(printf '%s' "$2" | tr '\n\r' '  ' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' || true)"
  printf '{\n  "harness": "%s",\n  "detail": "%s",\n  "recorded_at": "%s"\n}\n' \
    "$1" "$_T_ESC" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    > "$_T_DIR/$1.json" 2>/dev/null || true
  return 0
}

TORTOISE_BIN="$(command -v tortoise || true)"
TORTOISE_MODULE=""
if [ -z "$TORTOISE_BIN" ]; then
  for _T_CAND in \
    "${TORTOISE_SRC_DIR:-}" \
    "$(head -n 1 "${HOME:-/nonexistent}/.tortoise/hook-src-dir" 2>/dev/null || true)" \
    "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd || true)"
  do
    if [ -n "$_T_CAND" ] && [ -d "$_T_CAND/tortoise" ]; then
      TORTOISE_MODULE="$_T_CAND"
      break
    fi
  done
  if [ -n "$TORTOISE_MODULE" ] && [ -x "$TORTOISE_MODULE/.venv/bin/tortoise" ]; then
    TORTOISE_BIN="$TORTOISE_MODULE/.venv/bin/tortoise"
  elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/tortoise" ]; then
    TORTOISE_BIN="$VIRTUAL_ENV/bin/tortoise"
  elif [ -z "$TORTOISE_MODULE" ]; then
    _tortoise_capture_error "$HARNESS" \
      "volunteer reflex skipped: the hook ($0) could not resolve a tortoise install or checkout — no 'tortoise' on PATH, no \$TORTOISE_SRC_DIR checkout, no \$HOME/.tortoise/hook-src-dir record, and no checkout above the hook's own directory"
    exit 0  # recorded above, then fail-open: never a silent no-reflex
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
  if [ -z "$PYTHON_BIN" ]; then
    _tortoise_capture_error "$HARNESS" \
      "volunteer reflex skipped: resolved the module dir $TORTOISE_MODULE but found no python interpreter to run it"
    exit 0  # recorded above, then fail-open
  fi
fi

# python3 (hook-input JSON parsing + per-harness output assembly) is a hard
# requirement once we reach emission — preflight it so a missing interpreter
# can never violate the fail-open exit-0 contract.
PY3="$(command -v python3 || true)"
[ -z "$PY3" ] && exit 0

# ── Run the reflex (fail-open: any failure → empty injection) ────────────
# The reflex prints a run-time endpoint-mode note on stderr when it runs
# hosted against a file config (#2369 D1.3). Capture that stderr and relay
# ONLY the note marker to OUR stderr (the harness log surfaces it), so the
# endpoint of a hosted run is visible at run time — while genuine reflex
# error noise stays out of the hook (stdout remains the only injection
# channel).
REFLEX_ERR="$(mktemp "${TMPDIR:-/tmp}/tortoise-reflex.XXXXXX" 2>/dev/null)" \
  || REFLEX_ERR=""
if [ -z "$REFLEX_ERR" ]; then
  REFLEX_ERR="$(mktemp 2>/dev/null)" || REFLEX_ERR="/tmp/tortoise-reflex.$$"
fi
trap 'rm -f "$REFLEX_ERR"' EXIT

BLOCK=""
if [ -n "$TORTOISE_BIN" ]; then
  BLOCK="$(printf '%s' "$PROMPT" | "$TORTOISE_BIN" volunteer 2>"$REFLEX_ERR" || true)"
else
  # Module fallback: the checkout path travels via ENV (never string-
  # interpolated into python -c source — a quote in the path must not inject
  # code). The -c body reads TORTOISE_VOLUNTEER_MODULE from os.environ.
  BLOCK="$(printf '%s' "$PROMPT" | TORTOISE_VOLUNTEER_MODULE="$TORTOISE_MODULE" "$PYTHON_BIN" -c "
import os, sys
sys.path.insert(0, os.environ['TORTOISE_VOLUNTEER_MODULE'])
from tortoise.__main__ import main
raise SystemExit(main(['volunteer']))
" 2>"$REFLEX_ERR" || true)"
fi
# Relay the #2369 endpoint-mode note (marker-filtered; only emitted on
# hosted-against-file runs) to the hook's stderr for the harness log.
if [ -s "$REFLEX_ERR" ]; then
  grep -F "tortoise: hosted-mode note:" "$REFLEX_ERR" >&2 || true
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
