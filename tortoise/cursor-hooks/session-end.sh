#!/usr/bin/env bash
# tortoise-hook-version: 1
# Tortoise session capture for Cursor — sessionEnd hook (#3819).
#
# The `tortoise-hook-version` marker above is the install-contract generation
# for this hook (see tortoise/hook_install.py): column-0, one per file, bumped
# on ANY behavioural edit. #3819 is the first generation for the Cursor seam.
#
# Fires when a Cursor agent session ends. Cursor delivers the event as JSON on
# stdin (verified against the installed bundle, Cursor 3.20.21:
# `CursorHooksService.executeHookForStep` builds the payload and
# `shellExecService.executeHookDirect(..., "stdin")` pipes it in):
#
#   {"conversation_id": "...", "generation_id": "...", "model": "...",
#    "reason": "window_close" | "user_close", "duration_ms": 123,
#    "is_background_agent": false, "final_status": "completed",
#    "session_id": "<== conversation_id>", "hook_event_name": "sessionEnd",
#    "cursor_version": "3.20.21", "workspace_roots": ["..."],
#    "user_email": "...", "transcript_path": "<...>" | null}
#
# `transcript_path` is `null` when the user disabled transcripts. Cursor also
# puts it in the environment as `CURSOR_TRANSCRIPT_PATH`; when it is absent we
# fall back to Cursor's machine-local store, `~/.cursor/projects/<mangled-
# workspace>/agent-transcripts/<mangled-conversation-id>{,/<id>}.jsonl`
# (`transcriptsDir = joinPath(projectDir, "agent-transcripts")`,
# `pathForConversation = agent-transcripts/<encodeURIComponent(id).replace(
# /%/g,"_")>.jsonl` — verified in the bundle). The fallback is machine-local
# and subject to Cursor's own `.agent-data-cleanup-*` sweep, so it is a best
# effort, never a guarantee.
#
# The transcript is parsed by the canonical Cursor parser
# (`tortoise.session_import.parsers.parse_cursor`) and filed through
# `tortoise sessions import --harness cursor` — the SAME `/v1/sessions`
# contract the Claude/Codex seams use, with the real session_id as the
# idempotency key.
#
# ── WHY THIS HOOK DETACHES ─────────────────────────────────────────────────
# Cursor's `onWillShutdown` JOINS the sessionEnd hook promise, so a hook that
# performs the capture POST synchronously DELAYS the app quitting by the POST
# duration (measured ~1–10 s for a hosted capture). The hook therefore does
# the minimum a synchronous hook may do — read stdin and hand off — and
# `nohup … & disown`s the real worker. A hook that runs the capture
# synchronously REDs the detach test and stalls Cursor's shutdown.
#
# ── CONSTRAINT: sessionEnd IS IDE-ONLY ─────────────────────────────────────
# Cursor's docs state, verbatim: "Cloud agents have no editor-lifetime session
# boundary. `sessionEnd` is tied to the IDE session, not a cloud agent chat."
# Cursor cloud-agent sessions are therefore OUT OF REACH for this seam. The
# desktop editor IS the expected surface for this seam; the cloud-agent gap is
# disclosed where a user chooses Cursor, not engineered around here.
#
# Cursor reads hook registrations ONLY from `$CURSOR_HOME/hooks.json`
# (default `~/.cursor/hooks.json`); a project-local `<repo>/.cursor/hooks.json`
# is gated on workspace trust and fires nothing when untrusted. Install once:
#   tortoise install cursor           # installs this hook + the registration
#
# The hook ALWAYS exits 0: memory capture must never block or fail a session.

set -uo pipefail

# ── The detached worker half ─────────────────────────────────────────────
# Invoked as `$0 --worker` by the parent below. All slow work lives here; the
# parent has already returned by the time this runs.
if [ "${1:-}" = "--worker" ]; then
  PAYLOAD="${TORTOISE_CURSOR_PAYLOAD:-}"
  [ -n "$PAYLOAD" ] && [ -f "$PAYLOAD" ] || exit 0

  META="$(python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
roots = d.get("workspace_roots") or []
root0 = roots[0] if isinstance(roots, list) and roots else ""
print(d.get("transcript_path") or "")
print(d.get("session_id") or d.get("conversation_id") or "")
print(root0)
' < "$PAYLOAD" 2>/dev/null || true)"
  rm -f "$PAYLOAD" 2>/dev/null || true

  TRANSCRIPT_PATH="$(printf '%s\n' "$META" | sed -n '1p')"
  SESSION_ID="$(printf '%s\n' "$META" | sed -n '2p')"
  WORKSPACE_ROOT="$(printf '%s\n' "$META" | sed -n '3p')"

  # Nullable per the Cursor schema — fall back to the machine-local
  # agent-transcripts store before giving up.
  if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
    TRANSCRIPT_PATH="$(TORTOISE_CURSOR_SID="$SESSION_ID" \
      TORTOISE_CURSOR_PROJECT="${CURSOR_PROJECT_DIR:-$WORKSPACE_ROOT}" \
      python3 -c '
import glob, os, re, urllib.parse
sid = os.environ.get("TORTOISE_CURSOR_SID") or ""
cwd = os.environ.get("TORTOISE_CURSOR_PROJECT") or ""
if not sid:
    raise SystemExit(0)
def mangle(p):
    return re.sub(r"-+", "-", re.sub(r"[^a-zA-Z0-9]", "-", p)).strip("-")
cid = urllib.parse.quote(sid, safe="").replace("%", "_")[:200]
base = os.path.join(os.path.expanduser("~"), ".cursor", "projects")
cands = []
if cwd:
    ws = mangle(os.path.expanduser(cwd))
    cands += [
        os.path.join(base, ws, "agent-transcripts", cid, cid + ".jsonl"),
        os.path.join(base, ws, "agent-transcripts", cid + ".jsonl"),
    ]
cands += [
    os.path.join(base, "*", "agent-transcripts", cid, cid + ".jsonl"),
    os.path.join(base, "*", "agent-transcripts", cid + ".jsonl"),
]
for pattern in cands:
    hits = sorted(glob.glob(pattern))
    if hits:
        print(hits[0])
        break
' 2>/dev/null || true)"
  fi

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

  # `sessions import --harness cursor` is the canonical Cursor capture step:
  # it parses the agent transcript with `parse_cursor`, POSTs the SAME
  # `/v1/sessions` payload the Claude/Codex hooks send (harness + the REAL
  # session_id as the idempotency key), and writes a local 2xx-only receipt.
  # Fail-open: any failure exits 0 and files nothing rather than blocking the
  # session.
  if [ -n "$TORTOISE_BIN" ]; then
    ARGS=(sessions import --file "$TRANSCRIPT_PATH" --harness cursor)
    [ -n "$SESSION_ID" ] && ARGS+=(--session-id "$SESSION_ID")
    "$TORTOISE_BIN" "${ARGS[@]}" >/dev/null 2>&1 || true
  else
    TORTOISE_CURSOR_MODULE="$TORTOISE_MODULE" \
    TORTOISE_CURSOR_FILE="$TRANSCRIPT_PATH" \
    TORTOISE_CURSOR_SID2="$SESSION_ID" \
    "$PYTHON_BIN" -c '
import os, sys
sys.path.insert(0, os.environ["TORTOISE_CURSOR_MODULE"])
from tortoise.__main__ import main
argv = ["sessions", "import", "--file", os.environ["TORTOISE_CURSOR_FILE"],
        "--harness", "cursor"]
sid = os.environ.get("TORTOISE_CURSOR_SID2")
if sid:
    argv += ["--session-id", sid]
raise SystemExit(main(argv))
' >/dev/null 2>&1 || true
  fi
  exit 0
fi

# ── The synchronous hook half: read stdin, persist, detach, exit ─────────
# Everything here must be fast: Cursor JOINS this promise on shutdown, so any
# work here delays the app quitting.
PAYLOAD="$(mktemp -t tortoise_cursor_end.XXXXXX 2>/dev/null)" || exit 0
if [ -z "$PAYLOAD" ]; then
  PAYLOAD="${TMPDIR:-/tmp}/tortoise_cursor_end.$$"
fi
cat > "$PAYLOAD" 2>/dev/null || { rm -f "$PAYLOAD" 2>/dev/null || true; exit 0; }
[ -s "$PAYLOAD" ] || { rm -f "$PAYLOAD" 2>/dev/null || true; exit 0; }

# Absolute self-path: Cursor invokes the registered command string, so `$0` is
# normally absolute — resolve anyway so the detached worker cannot depend on
# the session's cwd.
SELF="$0"
case "$SELF" in
  /*) : ;;
  *) SELF="$(cd "$(dirname "$0")" 2>/dev/null && pwd)/$(basename "$0")" ;;
esac

TORTOISE_CURSOR_PAYLOAD="$PAYLOAD" nohup "$SELF" --worker >/dev/null 2>&1 &
disown 2>/dev/null || true
exit 0
