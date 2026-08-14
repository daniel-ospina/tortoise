#!/usr/bin/env bash
# Tortoise session capture for Claude Code — SessionEnd hook (#564).
#
# Fires when a Claude Code session ends: converts the session transcript
# (Claude Code's .jsonl) into Tortoise's text-turn format (User:/Assistant:)
# and files it via `tortoise session capture` (hosted /v1/sessions).
# This is the exit-side counterpart to session-start.sh's memory injection —
# together they close the loop: memory in at session start, session filed at
# session end.
#
# Install (once, per project):
#   mkdir -p .claude/hooks
#   cp tortoise/claude-hooks/session-end.sh .claude/hooks/session-end.sh
#   chmod +x .claude/hooks/session-end.sh
#   # then add to .claude/settings.json:
#   #   { "hooks": { "SessionEnd": [{ "matcher": "", "hooks": [{ "type": "command",
#   #       "command": ".claude/hooks/session-end.sh" }] }] } }
#
# Requires TORTOISE_API_KEY + TORTOISE_API_URL (hosted) or a local `tortoise`
# install with hosted capture configured. For a LOCAL-only graph, replace the
# capture step with: tortoise index --dir ~/.tortoise/docs/conversations/.
#
# The hook ALWAYS exits 0 — Claude Code must never be blocked by memory
# capture failing (offline, uninstalled, no transcript).

set -euo pipefail

# Claude Code passes SessionEnd hook metadata as JSON on stdin:
# {"session_id": "...", "transcript_path": "...", "cwd": "..."}
TRANSCRIPT_PATH="$(python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    print(d.get("transcript_path") or "")
except Exception:
    print("")' 2>/dev/null || true)"

[ -n "$TRANSCRIPT_PATH" ] || exit 0
[ -f "$TRANSCRIPT_PATH" ] || exit 0

# Convert the Claude Code .jsonl transcript into text turns (User:/Assistant:).
TMP="$(mktemp -t tortoise_session_end.XXXXXX)"
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

[ -s "$TMP" ] || exit 0  # nothing parseable — skip silently

# Prefer a local install; fall back to the repo checkout (mirrors session-start.sh).
TORTOISE_BIN="$(command -v tortoise || true)"
if [ -z "$TORTOISE_BIN" ]; then
  TORTOISE_MODULE="${TORTOISE_SRC_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
  if [ ! -d "$TORTOISE_MODULE/tortoise" ]; then
    exit 0
  fi
  PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"
  [ -z "$PYTHON_BIN" ] && exit 0
  # #280 item 3: reconciliation sweep — periodically scan the local corpus
  # (~/.tortoise/docs/conversations/) for unindexed/stale session files and
  # re-index them. Fired BEFORE capture (Round-10 P3): a hosted-capture
  # failure (missing API key, network down) must not disable local
  # auto-reindexing — the sweep targets the local graph, independent of
  # capture. Backgrounded + ALWAYS exits 0 (never block session close);
  # the per-session flock serializes against capture and manual CLI runs.
  #
  # Epic #900 T8 migration (#1044): the sweep now converges onto the unified
  # index path — `tortoise index directory <corpus> --metadata` — preserving
  # the #280 reconciliation role (corpus-wide sweep at every session close)
  # AND the legacy sweep's embedding behavior (--metadata). The corpus dir is
  # passed POSITIONALLY, resolved via session_corpus_dir() (honors
  # TORTOISE_SESSION_CORPUS else ~/.tortoise/docs/conversations).
  # tortoise-hook-version: 2
  SWEEP_CORPUS="$("$PYTHON_BIN" -c "
import sys, os
sys.path.insert(0, '$TORTOISE_MODULE')
from tortoise.session_indexer import session_corpus_dir
print(session_corpus_dir())" 2>/dev/null || true)"
  [ -z "$SWEEP_CORPUS" ] && SWEEP_CORPUS="$HOME/.tortoise/docs/conversations"
  nohup "$PYTHON_BIN" -c "
import sys, os
sys.path.insert(0, '$TORTOISE_MODULE')
from tortoise.__main__ import main
# TORTOISE_INDEX_CHILD_STDERR debug-redirect is OPT-IN: only when the
# operator set it (never force-write a file at every session close —
# review-gate P2). truncate-on-open + fail-safe inside the CLI.
os.environ.setdefault('TORTOISE_INDEX_CHILD_STDERR', '')
raise SystemExit(main(['index', 'directory', '$SWEEP_CORPUS', '--metadata']))
" >/dev/null 2>&1 &
  "$PYTHON_BIN" -c "
import sys
sys.path.insert(0, '$TORTOISE_MODULE')
from tortoise.__main__ import main
raise SystemExit(main(['session', 'capture', '--file', '$TMP']))
" 2>/dev/null || exit 0
else
  # Round-10 P3: sweep first (capture failure must not disable reindexing).
  # The corpus dir is resolved via session_corpus_dir() (honors
  # TORTOISE_SESSION_CORPUS else ~/.tortoise/docs/conversations) using the
  # interpreter that owns the installed tortoise package — a missing
  # resolution silently fell back to the default corpus and ignored a
  # configured TORTOISE_SESSION_CORPUS — the corpus-dir divergence class
  # the plan condemns, review-gate P1).
  SWEEP_CORPUS="$(python3 -c "
from tortoise.session_indexer import session_corpus_dir
print(session_corpus_dir())" 2>/dev/null || true)"
  [ -z "$SWEEP_CORPUS" ] && SWEEP_CORPUS="$HOME/.tortoise/docs/conversations"
  # tortoise-hook-version: 2
  # CHILD_STDERR debug-redirect is OPT-IN: only when the operator set it
  # (never force-write a file at every session close)
  if [ -z "${TORTOISE_INDEX_CHILD_STDERR:-}" ]; then
    nohup "$TORTOISE_BIN" index directory "$SWEEP_CORPUS" --metadata >/dev/null 2>&1 &
  else
    TORTOISE_INDEX_CHILD_STDERR="$TORTOISE_INDEX_CHILD_STDERR" \
      nohup "$TORTOISE_BIN" index directory "$SWEEP_CORPUS" --metadata >/dev/null 2>&1 &
  fi
  tortoise session capture --file "$TMP" 2>/dev/null || exit 0
fi
