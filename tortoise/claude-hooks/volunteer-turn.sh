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
  # PURE SHELL, no python3: this is also the evidence path for the "resolved a
  # module dir but found no interpreter" branch, which is reached BECAUSE
  # python3 is missing — a python3-written breadcrumb could never run there.
  # The ``install-inert`` kind marks this as the INSTALL leg's own evidence and
  # keeps it distinguishable from a ``sessions import`` capture failure, which
  # writes the same file with ``kind: capture-failure`` (#4314). Best-effort:
  # a breadcrumb write can never break the exit-0 contract.
  local harness="$1" detail="$2"
  local receipt_dir crumb_dir stamp
  receipt_dir="${TORTOISE_IMPORT_RECEIPT_DIR:-${HOME:-/nonexistent}/.tortoise/import-receipts}"
  # Normalize to pathlib's `.parent` semantics (#4373 review). Python's
  # `Path(x).parent` DROPS trailing slashes before taking the parent; `${x%/*}`
  # does not — so `…/import-receipts/` made the shell write
  # `…/import-receipts/capture-errors/` while `session verify` read
  # `…/capture-errors/`, leaving the breadcrumb invisible and an INERT install
  # reading PROVEN. That is the exact false-PROVEN this seam exists to remove.
  while [ "${receipt_dir%/}" != "$receipt_dir" ] && [ "$receipt_dir" != "/" ]; do
    receipt_dir="${receipt_dir%/}"
  done
  case "$receipt_dir" in
    */*) crumb_dir="${receipt_dir%/*}/capture-errors" ;;
    *) crumb_dir="capture-errors" ;;
  esac
  stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"
  mkdir -p "$crumb_dir" 2>/dev/null || true
  printf '{\n  "harness": "%s",\n  "detail": "%s",\n  "recorded_at": "%s",\n  "kind": "install-inert"\n}\n' \
    "$harness" "$detail" "$stamp" \
    > "$crumb_dir/$harness.json" 2>/dev/null || true
}

HARNESS="${1:-codex}"
IN="$(cat)"

# ── Extract the user prompt from the hook input ──────────────────────────
PROMPT=""
if [ -n "$IN" ]; then
  PROMPT="$(printf '%s' "$IN" | python3 -c '
import sys
# CWE-427: drop the process cwd before importing `json` — `python -c` puts cwd
# at sys.path[0], so a planted ./json.py in the session workspace would execute
# on every prompt. `sys` is builtin and cannot be shadowed.
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
import json

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

# ── The venv that OWNS the module dir (the WHEEL case) ───────────────────
# A wheel install puts this hook in ``site-packages/tortoise/claude-hooks/``,
# so the module dir is ``site-packages`` — there is NO ``.venv`` beneath it
# and the source-checkout probe above can never match (#2385 item 3). The
# venv that owns site-packages is the one whose ``bin/tortoise`` console
# script and ``bin/python`` have the wheel's dependencies, and it is marked
# by a ``pyvenv.cfg`` at the venv root. POSIX
# (``lib/python3.x/site-packages``) and Windows (``Lib/site-packages``) differ
# in depth, so walk UP to the marker (bounded) instead of assuming a fixed
# ``../../..``. Without this, the module fallback runs under the ambient
# ``python3`` — which does not have the wheel's dependencies — and the hook
# silently injects nothing while reporting success.
TORTOISE_VENV=""
if [ -n "$TORTOISE_MODULE" ]; then
  _probe="$TORTOISE_MODULE"
  _depth=0
  while [ "$_depth" -lt 5 ]; do
    if [ -z "$_probe" ] || [ "$_probe" = "/" ]; then
      break
    fi
    if [ -f "$_probe/pyvenv.cfg" ]; then
      TORTOISE_VENV="$_probe"
      break
    fi
    _probe="${_probe%/*}"
    _depth=$((_depth + 1))
  done
fi
if [ -z "$TORTOISE_BIN" ] && [ -n "$TORTOISE_VENV" ] \
   && [ -x "$TORTOISE_VENV/bin/tortoise" ]; then
  TORTOISE_BIN="$TORTOISE_VENV/bin/tortoise"
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
# A wheel install has no checkout venv, so fall back to the venv that OWNS
# the resolved module dir (#2385 item 3) before the ambient python3.
PYTHON_BIN=""
if [ -z "$TORTOISE_BIN" ]; then
  if [ -x "$TORTOISE_MODULE/.venv/bin/python" ]; then
    PYTHON_BIN="$TORTOISE_MODULE/.venv/bin/python"
  elif [ -n "$TORTOISE_VENV" ] && [ -x "$TORTOISE_VENV/bin/python" ]; then
    PYTHON_BIN="$TORTOISE_VENV/bin/python"
  elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python"
  else
    PYTHON_BIN="$(command -v python3 || true)"
  fi
  if [ -z "$PYTHON_BIN" ]; then
    # The module dir resolved but there is no interpreter to run it: record
    # the breadcrumb (evidence, not silence) and exit 0 (#4314).
    _record_breadcrumb "$HARNESS" \
      "the installed volunteer hook resolved a tortoise module dir but found no python3 interpreter, and injected nothing" \
      || true
    exit 0
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
  # Module fallback: run the resolved checkout via ``-c``. The path travels
  # via ENV and is prepended INSIDE the ``-c`` source — never via ``-m``
  # (CPython prepends the process CWD ahead of PYTHONPATH for ``-m``, so a
  # planted ``tortoise/`` package in the agent's workspace would execute as
  # the user and its stdout is injected into the model context, CWE-427)
  # and never string-interpolated into the source (a quote in the path must
  # not inject code).
  BLOCK="$(printf '%s' "$PROMPT" | \
    "$PYTHON_BIN" -c 'import sys; sys.path[:] = [p for p in sys.path if p not in ("", ".")]; sys.path.insert(0, sys.argv[1]); from tortoise.__main__ import main; raise SystemExit(main(sys.argv[2:]))' "$TORTOISE_MODULE" volunteer 2>"$REFLEX_ERR" || true)"
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
import sys
# CWE-427: -c puts the cwd at sys.path[0], so a planted ./json.py or ./os.py
# in the session workspace would execute on every prompt. This source is a
# DOUBLE-quoted shell string, so two rules apply: the predicate must avoid
# quotes (p and p != '.' is p not in ('', '.')), and the comments must contain
# NO backticks, which the shell would command-substitute and splice into this
# source. Neither rule is theoretical: an earlier revision of this comment ran
# three bogus commands here on every prompt.
sys.path[:] = [p for p in sys.path if p and p != '.']
import json, os
print(json.dumps({'hookSpecificOutput': {
    'hookEventName': 'UserPromptSubmit',
    'additionalContext': os.environ.get('TORTOISE_VOLUNTEER_BLOCK', '')}}))
" 2>/dev/null || true
    ;;
  cline)
    # Cline UserPromptSubmit → contextModification.context.
    "$PY3" -c "
import sys
# CWE-427: same double-quoted source and same quote-free predicate as the
# claude|codex|devin arm above - and likewise NO backticks in this comment.
sys.path[:] = [p for p in sys.path if p and p != '.']
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
