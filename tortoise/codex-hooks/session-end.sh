#!/usr/bin/env bash
# tortoise-hook-version: 1
# Tortoise session capture for Codex CLI — SessionEnd hook (#3818).
#
# The `tortoise-hook-version` marker above is the install-contract generation
# for this hook (see tortoise/hook_install.py): column-0, one per file, bumped
# on ANY behavioural edit. #3818 is the first generation for the Codex seam.
#
# Fires when a Codex session ends. Codex delivers the event as JSON on stdin:
#
#   {"session_id": "...", "transcript_path": "...", "cwd": "...",
#    "hook_event_name": "SessionEnd", "reason": "..."}
#
# `transcript_path` is the session's rollout JSONL
# (`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`); the schema marks it
# NULLABLE, so a null/absent path is a clean no-op. The rollout is parsed by
# the canonical Codex parser (`tortoise.session_import.parsers.parse_codex`)
# and filed through `tortoise sessions import --harness codex` — the SAME
# `/v1/sessions` contract the Claude seam's `session capture` uses.
#
# ── WHY THIS HOOK DETACHES (measured, 2026-09-18, Codex CLI 0.154.0) ──────
# A Codex `SessionEnd` command hook is killed at a hard ~1 s budget: a hook
# whose only work was `sleep 1` logged START and never reached its next line,
# and a `timeoutSec: 30` budget did not extend it. ANY network POST measured
# here (the live capture POST runs ~1-10 s) would therefore be killed
# mid-flight and file nothing. So the hook does the minimum a synchronous
# hook may do — read stdin and hand off — and `nohup … & disown`s the real
# worker, which SURVIVES Codex's exit (verified: a detached `sleep 3`
# completed after `codex exec` returned). Codex kills the hook, not the
# session's whole process group.
#
# Install (once):
#   tortoise install codex            # installs this hook + the registration
#
# or by hand — the registration MUST live in `$CODEX_HOME/hooks.json`
# (`~/.codex/hooks.json`). The project-local `<repo>/.codex/hooks.json` is NOT
# a live Codex 0.154.0 hook source (verified: neither the project file nor
# `<repo>/.codex/config.toml [hooks]` fires; only `$CODEX_HOME/hooks.json`
# does):
#   {
#     "hooks": {
#       "SessionEnd": [
#         {"hooks": [{"type": "command",
#                     "command": "<abs-path>/tortoise-session-end.sh"}]}
#       ]
#     }
#   }
#
# Codex refuses to RUN a hook it has not been told to trust (TUI review, or
# `codex exec --dangerously-bypass-hook-trust` for trusted automation). A hook
# that is not trusted simply does not fire — fail-open, like everything here.
#
# The hook ALWAYS exits 0: memory capture must never block or fail a session.

set -uo pipefail

# ── The detached worker half ─────────────────────────────────────────────
# Invoked as `$0 --worker` by the parent below. All slow work lives here; the
# parent has already returned by the time this runs.
if [ "${1:-}" = "--worker" ]; then
  PAYLOAD="${TORTOISE_CODEX_PAYLOAD:-}"
  [ -n "$PAYLOAD" ] && [ -f "$PAYLOAD" ] || exit 0

  META="$(python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
print(d.get("transcript_path") or "")
print(d.get("session_id") or "")
' < "$PAYLOAD" 2>/dev/null || true)"
  rm -f "$PAYLOAD" 2>/dev/null || true

  TRANSCRIPT_PATH="$(printf '%s\n' "$META" | sed -n '1p')"
  SESSION_ID="$(printf '%s\n' "$META" | sed -n '2p')"

  # Nullable per the Codex schema — nothing to file.
  [ -n "$TRANSCRIPT_PATH" ] || exit 0
  [ -f "$TRANSCRIPT_PATH" ] || exit 0

  # ── Resolve the capture entry (PATH install → repo .venv → module) ─────
  TORTOISE_BIN="$(command -v tortoise || true)"
  TORTOISE_MODULE=""
  if [ -z "$TORTOISE_BIN" ]; then
    TORTOISE_MODULE="${TORTOISE_SRC_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd || true)}"
    if [ -n "$TORTOISE_MODULE" ] && [ -x "$TORTOISE_MODULE/.venv/bin/tortoise" ]; then
      TORTOISE_BIN="$TORTOISE_MODULE/.venv/bin/tortoise"
    elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/tortoise" ]; then
      TORTOISE_BIN="$VIRTUAL_ENV/bin/tortoise"
    elif [ -z "$TORTOISE_MODULE" ] || [ ! -d "$TORTOISE_MODULE/tortoise" ]; then
      exit 0  # no tortoise install or checkout — clean silence
    fi
  fi

  # Python fallback for a source checkout (mirrors volunteer-turn.sh).
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

  # `sessions import --harness codex` is the canonical Codex capture step: it
  # parses the rollout with `parse_codex`, POSTs the SAME `/v1/sessions`
  # payload the Claude hook sends (harness + the REAL session_id as the
  # idempotency key), and writes a local 2xx-only receipt. Fail-open: any
  # failure exits 0 and files nothing rather than blocking the session.
  if [ -n "$TORTOISE_BIN" ]; then
    ARGS=(sessions import --file "$TRANSCRIPT_PATH" --harness codex)
    [ -n "$SESSION_ID" ] && ARGS+=(--session-id "$SESSION_ID")
    "$TORTOISE_BIN" "${ARGS[@]}" >/dev/null 2>&1 || true
  else
    TORTOISE_CODEX_MODULE="$TORTOISE_MODULE" \
    TORTOISE_CODEX_FILE="$TRANSCRIPT_PATH" \
    TORTOISE_CODEX_SID="$SESSION_ID" \
    "$PYTHON_BIN" -c '
import os, sys
sys.path.insert(0, os.environ["TORTOISE_CODEX_MODULE"])
from tortoise.__main__ import main
argv = ["sessions", "import", "--file", os.environ["TORTOISE_CODEX_FILE"],
        "--harness", "codex"]
sid = os.environ.get("TORTOISE_CODEX_SID")
if sid:
    argv += ["--session-id", sid]
raise SystemExit(main(argv))
' >/dev/null 2>&1 || true
  fi
  exit 0
fi

# ── The synchronous hook half: read stdin, persist, detach, exit ─────────
# Everything here must finish in well under Codex's ~1 s SessionEnd budget.
PAYLOAD="$(mktemp -t tortoise_codex_end.XXXXXX 2>/dev/null)" || exit 0
if [ -z "$PAYLOAD" ]; then
  PAYLOAD="${TMPDIR:-/tmp}/tortoise_codex_end.$$"
fi
cat > "$PAYLOAD" 2>/dev/null || { rm -f "$PAYLOAD" 2>/dev/null || true; exit 0; }
[ -s "$PAYLOAD" ] || { rm -f "$PAYLOAD" 2>/dev/null || true; exit 0; }

# Absolute self-path: Codex invokes the registered command string, so `$0` is
# normally absolute — resolve anyway so the detached worker cannot depend on
# the session's cwd.
SELF="$0"
case "$SELF" in
  /*) : ;;
  *) SELF="$(cd "$(dirname "$0")" 2>/dev/null && pwd)/$(basename "$0")" ;;
esac

TORTOISE_CODEX_PAYLOAD="$PAYLOAD" nohup "$SELF" --worker >/dev/null 2>&1 &
disown 2>/dev/null || true
exit 0
