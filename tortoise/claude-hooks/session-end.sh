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
# CAPTURE REQUIRES EXPLICIT CONSENT (#3615): TORTOISE_CAPTURE=1 (truthy:
# 1/true/yes/on). It is INDEPENDENT of the credential — TORTOISE_API_KEY +
# TORTOISE_API_URL are the hosted credential/endpoint used to reach the API,
# and exporting the key for the MCP `Authorization: Bearer` header must NEVER
# opt the machine into shipping transcripts. With the opt-in absent the capture
# step is skipped (a notice on stderr, repeated on each session close while a
# legacy credential is present; only the durable file is one-time) and the hook
# still exits 0; the LOCAL reindex sweep below still runs — it never leaves the
# machine.
# For a LOCAL-only graph, replace the capture step with:
#   tortoise index --dir ~/.tortoise/docs/conversations/.
#
# The hook ALWAYS exits 0 — Claude Code must never be blocked by memory
# capture failing (offline, uninstalled, no transcript).

set -euo pipefail

# Claude Code passes SessionEnd hook metadata as JSON on stdin:
# {"session_id": "...", "transcript_path": "...", "cwd": "..."}
# #1727 (Task 14, T1-P11): the REAL session_id is forwarded as the capture
# idempotency key (re-POSTs of the same session_id converge to one Session);
# harness='claude' is passed for per-harness receipts.
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

# ── Explicit capture consent (#3615) ─────────────────────────────────────
# Consent is a separate concept from authentication: a credential is an
# authentication artifact; capture is data-sharing with a vendor. The opt-in
# must be explicit and CANNOT be inferred from credential presence. The truthy
# set here MUST match tortoise/capture_consent.py::capture_consent_enabled
# (parity pinned by tests/test_session_capture_e2e.py — the bash matrix —
# and tests/test_capture_consent.py — the Python/CLI side). The bash/CLI
# duplication is deliberate defense-in-depth: this gate keeps the ambient path
# from even attempting the upload; the CLI gate stops a STALE copied hook
# (hooks are copied per-project and never update themselves).
CAPTURE_ENABLED=0
# Trim leading/trailing whitespace, then lowercase — mirrors Python's
# str(...).strip(" \t\r\n\v\f").lower() so the two implementations agree
# EXACTLY. The explicit ASCII set is deliberate: `[[:space:]]` is locale- and
# platform-dependent and also matches Unicode spaces (U+00A0, U+2028, U+2029,
# U+3000, …) that Python no longer trims, which would reopen the parity-drift
# class in the opposite direction (bash authorizes, Python refuses).
CAPTURE_RAW="${TORTOISE_CAPTURE:-}"
CAPTURE_RAW="${CAPTURE_RAW#"${CAPTURE_RAW%%[!$' \t\r\n\v\f']*}"}"
CAPTURE_RAW="${CAPTURE_RAW%"${CAPTURE_RAW##*[!$' \t\r\n\v\f']}"}"
case "$(printf '%s' "$CAPTURE_RAW" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on) CAPTURE_ENABLED=1 ;;
esac

if [ "$CAPTURE_ENABLED" != "1" ]; then
  # Non-silent MIGRATION notice for hosts that would have captured under the old
  # contract (a resolvable credential). A capture-off host (no credential) sees
  # nothing. Best-effort + non-blocking; never fails the hook. The marker's
  # CONTENT is the instruction (not an empty stamp) so it is a durable channel
  # even when hook stderr is not surfaced; the CLI primitive writes the same
  # file, which is what reaches users whose copied hook is stale and swallows
  # stderr. The visible stderr line is deliberately NOT gated on the marker's
  # absence: a stale copied hook makes the CLI write that marker, so a
  # marker-gated notice was permanently suppressed on precisely the hosts it
  # targets (solution-verify cycle 2 P1). One quiet line per session close is
  # the cost of not migrating a breaking change in silence.
  LEGACY_KEY="${TORTOISE_API_KEY:-}"
  LEGACY_KEY="${LEGACY_KEY#"${LEGACY_KEY%%[![:space:]]*}"}"
  LEGACY_KEY="${LEGACY_KEY%"${LEGACY_KEY##*[![:space:]]}"}"
  if [ -n "$LEGACY_KEY" ] || [ -f "$PWD/.tortoise" ] \
      || [ -f "${HOME:-/nonexistent}/.tortoise/credentials.json" ]; then
    NOTICE_MARKER="${HOME:-/nonexistent}/.tortoise/capture-consent-notice"
    if [ ! -f "$NOTICE_MARKER" ]; then
      mkdir -p "$(dirname "$NOTICE_MARKER")" 2>/dev/null || true
      # `2>/dev/null` MUST precede `> "$NOTICE_MARKER"`: a failed redirect
      # setup is reported to the shell's CURRENT stderr, so with the stdout
      # redirect first a non-writable ~/.tortoise leaks a raw bash error line
      # on every session close. Ordering stderr first suppresses the setup
      # failure too (`|| true` only rescues the exit status).
      printf '%s\n' \
        "Tortoise: session capture is OFF — it now requires explicit consent. Re-enable with TORTOISE_CAPTURE=1 (docs/quickstart-cloud.md)." \
        2>/dev/null > "$NOTICE_MARKER" || true
    fi
    printf '%s\n' \
      "tortoise: session capture is OFF — it now requires explicit consent. Re-enable with TORTOISE_CAPTURE=1 (docs/quickstart-cloud.md)." >&2 || true
  fi
fi

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
  # #1727 (Task 14): harness + the real session_id (idempotency key) pass
  # through to the capture payload — via env (the session id is untrusted
  # shell input; never interpolated into the -c string).
  # #3615: only when capture consent is explicit (see the gate above).
  if [ "$CAPTURE_ENABLED" = "1" ]; then
    TORTOISE_HOOK_SESSION_ID="$SESSION_ID" \
    "$PYTHON_BIN" -c "
import sys, os
sys.path.insert(0, '$TORTOISE_MODULE')
from tortoise.__main__ import main
argv = ['session', 'capture', '--file', '$TMP', '--harness', 'claude']
if os.environ.get('TORTOISE_HOOK_SESSION_ID'):
    argv += ['--session-id', os.environ['TORTOISE_HOOK_SESSION_ID']]
raise SystemExit(main(argv))
" 2>/dev/null || exit 0
  fi
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
  # #1727 (Task 14, T1-P11): capture with harness + the real session_id
  # (idempotency key — re-POST converges to one Session, one receipt).
  # #3615: only when capture consent is explicit (see the gate above).
  if [ "$CAPTURE_ENABLED" = "1" ]; then
    CAPTURE_ARGS=(--file "$TMP" --harness claude)
    [ -n "$SESSION_ID" ] && CAPTURE_ARGS+=(--session-id "$SESSION_ID")
    tortoise session capture "${CAPTURE_ARGS[@]}" 2>/dev/null || exit 0
  fi
fi
