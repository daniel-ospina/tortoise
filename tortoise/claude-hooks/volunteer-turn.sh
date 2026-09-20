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

# ── The local capture-error breadcrumb ───────────────────────────────────
# Mirrors `tortoise.__main__._record_capture_error` (same file layout, same
# `TORTOISE_IMPORT_RECEIPT_DIR` override) for the one case that helper cannot
# cover: the module dir did not resolve, so the Python helper is unreachable.
# A hook that does nothing must leave EVIDENCE, never silence (#4314).
# Best-effort: a breadcrumb write can never break the exit-0 contract.
_record_breadcrumb() {
  python3 - "$1" "$2" <<'PY' 2>/dev/null || true
import json, os, sys, time
from pathlib import Path
harness, detail = sys.argv[1], sys.argv[2]
receipt_dir = Path(os.environ.get(
    "TORTOISE_IMPORT_RECEIPT_DIR",
    str(Path.home() / ".tortoise" / "import-receipts")))
path = receipt_dir.parent / "capture-errors" / f"{harness}.json"
try:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "harness": harness,
        "detail": detail,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2), encoding="utf-8")
except OSError:
    pass
PY
}

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

# ── Resolve the reflex entry (PATH install → .venv → module) ───────────
# A candidate module dir is accepted ONLY when it actually holds a
# `tortoise/` package. Candidate order: $TORTOISE_SRC_DIR, the installer's
# recorded module dir, then `../..` — the LAST resort, because from an
# installed hook that is `$HOME`, which is not a checkout (#4314).
TORTOISE_MODULE=""
for CANDIDATE in "${TORTOISE_SRC_DIR:-}" \
                 "$(cat "${HOME:-/nonexistent}/.tortoise/hook-src-dir" 2>/dev/null || true)" \
                 "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd || true)"; do
  if [ -n "$CANDIDATE" ] && [ -d "$CANDIDATE/tortoise" ]; then
    TORTOISE_MODULE="$CANDIDATE"
    break
  fi
done

TORTOISE_BIN="$(command -v tortoise || true)"
if [ -z "$TORTOISE_BIN" ] && [ -n "$TORTOISE_MODULE" ] \
   && [ -x "$TORTOISE_MODULE/.venv/bin/tortoise" ]; then
  TORTOISE_BIN="$TORTOISE_MODULE/.venv/bin/tortoise"
fi
if [ -z "$TORTOISE_BIN" ] && [ -n "${VIRTUAL_ENV:-}" ] \
   && [ -x "$VIRTUAL_ENV/bin/tortoise" ]; then
  TORTOISE_BIN="$VIRTUAL_ENV/bin/tortoise"
fi
if [ -z "$TORTOISE_BIN" ] && [ -z "$TORTOISE_MODULE" ]; then
  # No binary and no module dir: record the breadcrumb (the SAME shape and
  # location `sessions import` writes) and exit 0 — an inert install must
  # leave evidence instead of silence (#4314).
  _record_breadcrumb "$HARNESS" \
    "the installed volunteer hook could not resolve a tortoise module dir (checked TORTOISE_SRC_DIR, \$HOME/.tortoise/hook-src-dir, and ../..), found no tortoise binary, and injected nothing" \
    || true
  exit 0
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
  # Module fallback: run the resolved checkout as `python -m tortoise`. The
  # path travels via PYTHONPATH, never string-interpolated into source — a
  # quote in the path must not inject code.
  BLOCK="$(printf '%s' "$PROMPT" | PYTHONPATH="$TORTOISE_MODULE" \
    "$PYTHON_BIN" -m tortoise volunteer 2>"$REFLEX_ERR" || true)"
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
