#!/usr/bin/env bash
# tortoise-hook-version: 4
# Tortoise per-turn capture for Claude Code — UserPromptSubmit hook (#3963).
#
# The `tortoise-hook-version` marker above is the install-contract generation
# for this hook (see tortoise/hook_install.py). Bump it on ANY behavioural edit
# — `tortoise hooks status` and `tortoise doctor` read it to tell an
# already-installed copy it is stale.
#
# WHY THIS HOOK EXISTS
# --------------------
# Capture used to happen ONLY at SessionEnd. That is structurally lossy: Claude
# Code CANCELS SessionEnd at its ~1.5s default (#3754), and when the process is
# killed (terminal closed, laptop sleep-kill, crash) NO stop-hook fires at all.
# A session interrupted mid-conversation therefore filed NOTHING — invisibly.
#
# This hook is the CHEAP half of the cadence: at every user prompt it copies the
# session's turns into Tortoise's durable local spool
# (~/.tortoise/capture-spool) with NO network call. The costly half (the file,
# which triggers server-side extraction) is deferred to the SessionStart drain
# and to the SessionEnd final flush — filing a still-growing conversation would
# make the server replay it and silently drop every later turn.
#
# A kill can therefore lose at most the in-flight turn.
#
# Install (once, per project):
#   mkdir -p .claude/hooks
#   cp tortoise/claude-hooks/session-turn.sh .claude/hooks/session-turn.sh
#   chmod +x .claude/hooks/session-turn.sh
#   #   { "hooks": { "UserPromptSubmit": [{ "matcher": "", "hooks": [{ "type":
#   #       "command", ".claude/hooks/session-turn.sh", "timeout": 30 }] }] } }
#
# UserPromptSubmit hook stdout is INJECTED INTO THE PROMPT, so this script writes
# NOTHING to stdout — every internal call is silenced. It always exits 0: a user
# turn must never be blocked by memory capture.

set -euo pipefail

# Claude Code passes hook metadata as JSON on stdin:
# {"session_id": "...", "transcript_path": "...", "cwd": "..."}
META="$(python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    print(d.get("transcript_path") or "")
    print(d.get("session_id") or "")
except Exception:
    print(""); print("")' 2>/dev/null || true)"
TRANSCRIPT_PATH="$(printf '%s\n' "$META" | sed -n '1p')"
SESSION_ID="$(printf '%s\n' "$META" | sed -n '2p')"

[ -n "$TRANSCRIPT_PATH" ] || exit 0
[ -f "$TRANSCRIPT_PATH" ] || exit 0

# Same Claude Code .jsonl → text-turn conversion as session-end.sh, so both
# hooks feed `tortoise session spool`/`capture` the identical input.
TMP="$(mktemp -t tortoise_session_turn.XXXXXX)"
trap 'rm -f "$TMP"' EXIT
python3 - "$TRANSCRIPT_PATH" "$TMP" << 'PYEOF'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
out = []
try:
    with open(src, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            msg = rec.get("message") or {}
            role = msg.get("role") or rec.get("type") or ""
            content = msg.get("content")
            if isinstance(content, list):
                content = " ".join(
                    str(c.get("text", "")) for c in content
                    if isinstance(c, dict) and c.get("text")
                )
            if not content:
                continue
            if role == "user":
                out.append("User: " + str(content))
            elif role == "assistant":
                out.append("Assistant: " + str(content))
except Exception:
    pass
with open(dst, "w", encoding="utf-8") as f:
    f.write("\n".join(out))
PYEOF

[ -s "$TMP" ] || exit 0  # nothing parseable yet — the next prompt retries

# Prefer a local install; fall back to the repo checkout (mirrors the other hooks).
# The session id is untrusted shell input: it is passed as an argv ELEMENT, never
# interpolated into a command string, and never as a prefix assignment on the
# same line (a prefix assignment does not affect that line's own expansions —
# the `${VAR:+…}` form silently dropped the id).
SPOOL_ARGS=(--file "$TMP" --harness claude)
[ -n "$SESSION_ID" ] && SPOOL_ARGS+=(--session-id "$SESSION_ID")

TORTOISE_BIN="$(command -v tortoise || true)"
if [ -n "$TORTOISE_BIN" ]; then
  # stdout MUST stay empty (UserPromptSubmit injects it into the user's prompt),
  # but stderr is NOT swallowed: this is the mechanism of record, and a silent
  # no-op (a wrong interpreter, an unreadable transcript) is precisely the
  # "losing sessions silently" failure #3963 exists to remove.
  "$TORTOISE_BIN" session spool "${SPOOL_ARGS[@]}" >/dev/null || exit 0
else
  TORTOISE_MODULE="${TORTOISE_SRC_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
  [ -d "$TORTOISE_MODULE/tortoise" ] || exit 0
  PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"
  [ -n "$PYTHON_BIN" ] || exit 0
  "$PYTHON_BIN" -c "
import sys
sys.path.insert(0, '$TORTOISE_MODULE')
from tortoise.__main__ import main
raise SystemExit(main(['session', 'spool', *sys.argv[1:]]))
" "${SPOOL_ARGS[@]}" >/dev/null || exit 0
fi

exit 0
