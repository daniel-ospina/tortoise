#!/usr/bin/env bash
# tortoise-hook-version: 2
# Tortoise session capture for Cursor — sessionEnd hook (#3819).
#
# The `tortoise-hook-version` marker above is the install-contract generation
# for this hook (see tortoise/hook_install.py): column-0, one per file, bumped
# on ANY behavioural edit. #3819 is the first generation for the Cursor seam;
# #4314 is the second — the entry resolution no longer trusts a candidate that
# is not a checkout, consults the installer's `$HOME/.tortoise/hook-src-dir`
# record, and writes a breadcrumb instead of exiting silently.
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
# `transcript_path` is `null` when the user disabled transcripts. Cursor ALSO
# puts the transcript in the environment as `CURSOR_TRANSCRIPT_PATH`, and the
# two are NOT the same resolution: `executeHookForStep` computes the payload's
# `transcript_path` with `preferJsonl = (stop || subagentStop)` — FALSE for
# `sessionEnd` — so `getTranscriptPath` walks `['txt','jsonl']` and hands us a
# `.txt` whenever one exists, while `_buildHookEnvironment` computes
# `CURSOR_TRANSCRIPT_PATH` with `preferJsonl=true` → `['jsonl','txt']`, i.e.
# the JSONL. `parse_cursor` is a JSONL parser, so a `.txt` yields 0 turns and
# files nothing while this hook still exits 0 — the silent no-capture this
# seam exists to prevent. The env var therefore WINS, and every candidate is
# normalised to a `.jsonl` (the same-stem sibling) before it reaches the
# parser. When neither is present we fall back to Cursor's machine-local
# store, `~/.cursor/projects/<mangled-workspace>/agent-transcripts/
# <mangled-conversation-id>{,/<id>}.jsonl`
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
# Cursor reads hook registrations ONLY from `~/.cursor/hooks.json` (Cursor has
# no config-dir env var; a project-local `<repo>/.cursor/hooks.json` is gated
# on workspace trust). Install once:
#   tortoise install cursor           # installs this hook + the registration
#
# The hook ALWAYS exits 0: memory capture must never block or fail a session.

set -uo pipefail

# Echo `$1` when it is a readable `.jsonl` transcript, or the same-stem
# `.jsonl` sibling when `$1` names a `.txt` (or other extension) Cursor also
# writes. Print NOTHING when no JSONL form exists: the caller then tries the
# next candidate, so a `.txt` is never handed to the JSONL parser.
_jsonl_of() {
  [ -n "${1:-}" ] || return 0
  case "$1" in
    *.jsonl)
      [ -f "$1" ] && printf '%s\n' "$1"
      ;;
    *)
      local alt="${1%.*}.jsonl"
      if [ "$alt" != "$1" ] && [ -f "$alt" ]; then
        printf '%s\n' "$alt"
      fi
      ;;
  esac
}

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

  TRANSCRIPT_PATH_RAW="$(printf '%s\n' "$META" | sed -n '1p')"
  SESSION_ID="$(printf '%s\n' "$META" | sed -n '2p')"
  WORKSPACE_ROOT="$(printf '%s\n' "$META" | sed -n '3p')"

  # Candidate order: the env var (Cursor's jsonl-preferred resolution) first,
  # then the payload's `transcript_path` (txt-preferred for sessionEnd), then
  # the machine-local store. Each is normalised to a `.jsonl`; the first that
  # yields one wins, and a candidate with no JSONL form is skipped.
  STORE_TRANSCRIPT_PATH=""
  if [ -n "$SESSION_ID" ]; then
    STORE_TRANSCRIPT_PATH="$(TORTOISE_CURSOR_SID="$SESSION_ID" \
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

  TRANSCRIPT_PATH=""
  for CANDIDATE in "${CURSOR_TRANSCRIPT_PATH:-}" "$TRANSCRIPT_PATH_RAW" \
                   "$STORE_TRANSCRIPT_PATH"; do
    TRANSCRIPT_PATH="$(_jsonl_of "$CANDIDATE")"
    [ -n "$TRANSCRIPT_PATH" ] && break
  done

  [ -n "$TRANSCRIPT_PATH" ] || exit 0
  [ -f "$TRANSCRIPT_PATH" ] || exit 0

  # ── Resolve the capture entry (PATH install → module venv → module) ─────
  # The module dir is the root that CONTAINS `tortoise/`.  This hook does NOT
  # live inside a checkout once installed — the installer places it at
  # `~/.cursor/hooks/…`, where `dirname(BASH_SOURCE)/../..` is `$HOME`, not a
  # checkout — so every candidate is accepted ONLY when it really contains
  # `tortoise/`: the first such candidate wins, and a non-empty value that is
  # not a checkout is SKIPPED, never trusted by position.  The installer
  # records the module dir it installed FROM in `$HOME/.tortoise/hook-src-dir`
  # (tortoise/hook_install.py), which is what makes an installed hook
  # resolvable with no `tortoise` on PATH.
  _tortoise_capture_error() {
    # The SAME local breadcrumb `sessions import` writes for a failed capture
    # (tortoise/__main__.py `_capture_error_file`: `$HOME/.tortoise/
    # capture-errors/<harness>.json`), so a hook that cannot capture is
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
      _tortoise_capture_error cursor \
        "session capture skipped: the hook ($0) could not resolve a tortoise install or checkout — no 'tortoise' on PATH, no \$TORTOISE_SRC_DIR checkout, no \$HOME/.tortoise/hook-src-dir record, and no checkout above the hook's own directory"
      exit 0  # recorded above, then fail-open: never a silent no-capture
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
    if [ -z "$PYTHON_BIN" ]; then
      _tortoise_capture_error cursor \
        "session capture skipped: resolved the module dir $TORTOISE_MODULE but found no python interpreter to run it"
      exit 0  # recorded above, then fail-open
    fi
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
