#!/usr/bin/env bash
# tortoise-hook-version: 2
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

# ── The local capture-error breadcrumb ───────────────────────────────────
# Mirrors `tortoise.__main__._record_capture_error` (same file layout, same
# `TORTOISE_IMPORT_RECEIPT_DIR` override) for the one case that helper cannot
# cover: the module dir did not resolve, so the Python helper is unreachable.
# A hook that captures nothing must leave EVIDENCE, never silence (#4314).
# Best-effort: a breadcrumb write can never break the exit-0 contract.
_record_breadcrumb() {
  # PURE SHELL, no python3: this is also the evidence path for the "resolved a
  # module dir but found no interpreter" branch, which is reached BECAUSE
  # python3 is missing — a python3-written breadcrumb could never run there.
  # The ``install-inert`` kind marks this as the INSTALL leg's own evidence and
  # keeps it distinguishable from a ``sessions import`` capture failure, which
  # writes the same file with ``kind: capture-failure`` (#4314). Best-effort:
  # a breadcrumb write can never break the exit-0 contract.
  # The ``kind`` argument distinguishes WHO is recording, and the distinction
  # is load-bearing: ``install-inert`` is the INSTALL leg's own evidence (an
  # installer that fired but captured nothing), while ``capture-failure`` is a
  # real capture failure. `session verify` READS the kind, so recording a
  # capture failure as ``install-inert`` would report a HEALTHY install as
  # INERT — precisely the inversion #4314 exists to prevent. It defaults to
  # the install-inert kind, so the pre-existing callers are unchanged.
  local harness="$1" detail="$2" kind="${3:-install-inert}"
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
  # JSON-escape the detail. A capture error is arbitrary server text and DOES
  # contain double quotes — the 504 body is
  # ``{"detail":"The server's wait budget … exceeded"}`` — so interpolating it
  # raw emitted INVALID JSON and made the breadcrumb unparseable by
  # `session verify` (measured #4714). Order matters: backslash FIRST, then the
  # quote, then the control characters (each insertion adds its own backslash,
  # so escaping the backslash last would double-escape it). The C0 set matters
  # for more than cosmetics: `sessions import` ALREADY writes this same file
  # with `json.dumps` on its own failure branches, so an unescaped tab or CR
  # here would REPLACE a valid breadcrumb with an unparseable one and destroy
  # the evidence this change exists to preserve. Pure shell: this function must
  # run where python3 does not exist.
  local esc="${detail//\\/\\\\}"
  esc="${esc//\"/\\\"}"
  esc="${esc//$'\n'/\\n}"
  esc="${esc//$'\r'/\\r}"
  esc="${esc//$'\t'/\\t}"
  # JSON has escapes for BS and FF too, so use them rather than dropping the
  # characters: escaping is lossless, and a discarded byte is evidence lost.
  esc="${esc//$'\b'/\\b}"
  esc="${esc//$'\f'/\\f}"
  # Sweep whatever is left of the C0 class (NUL, VT, ESC and the rest), which
  # has NO JSON escape at all. BS/FF were converted to their two-character
  # escapes directly above and are therefore not raw bytes here. `tr` is POSIX
  # and as available as the `mkdir`/`date` already used, so the function stays
  # pure-shell — which matters, because this is also the evidence path for the
  # branch reached BECAUSE python3 is missing.
  esc="$(printf '%s' "$esc" | tr -d '\000-\010\013\014\016-\037')"
  local esc_harness="${harness//\\/\\\\}"
  esc_harness="${esc_harness//\"/\\\"}"
  esc_harness="${esc_harness//$'\n'/\\n}"
  esc_harness="${esc_harness//$'\r'/\\r}"
  esc_harness="${esc_harness//$'\t'/\\t}"
  esc_harness="${esc_harness//$'\b'/\\b}"
  esc_harness="${esc_harness//$'\f'/\\f}"
  esc_harness="$(printf '%s' "$esc_harness" | tr -d '\000-\010\013\014\016-\037')"
  mkdir -p "$crumb_dir" 2>/dev/null || true
  printf '{\n  "harness": "%s",\n  "detail": "%s",\n  "recorded_at": "%s",\n  "kind": "%s"\n}\n' \
    "$esc_harness" "$esc" "$stamp" "$kind" \
    > "$crumb_dir/$harness.json" 2>/dev/null || true
}

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
import sys
# CWE-427: drop the process cwd before importing `json` — `python -c` puts cwd
# at sys.path[0], so a planted ./json.py in the session workspace would execute
# at every SessionEnd. `sys` is builtin and cannot be shadowed.
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
import json

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
import sys
# CWE-427: drop the process cwd before importing the stdlib — see the META
# parser above; a planted ./glob.py or ./re.py would execute here too.
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
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

  # ── Resolve the capture entry (PATH install → .venv → module) ─────────
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
    # leave evidence instead of silence (#4314). This runs in the DETACHED
    # worker, so Cursor's shutdown is still not delayed.
    _record_breadcrumb cursor \
      "the installed Cursor hook could not resolve a tortoise module dir (checked TORTOISE_SRC_DIR, \$HOME/.tortoise/hook-src-dir, and ../..), found no tortoise binary, and captured nothing"
    exit 0
  fi

  # Python fallback: run the resolved checkout via an ENV-fed ``-c`` prefix
  # (never ``-m`` — see the CWE-427 note at the invocation below).
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
      # The module dir resolved but there is no interpreter to run it: record
      # the breadcrumb (evidence, not silence) and exit 0 (#4314).
      _record_breadcrumb cursor \
        "the installed Cursor hook resolved a tortoise module dir but found no python3 interpreter, and captured nothing"
      exit 0
    fi
  fi

  # `sessions import --harness cursor` is the canonical Cursor capture step:
  # it parses the agent transcript with the HARNESS-AWARE `parse_transcript`
  # dispatcher (`parse_cursor`), POSTs the SAME `/v1/sessions` payload the
  # Claude/Codex hooks send (harness + the REAL session_id as the idempotency
  # key), and stages the parsed session locally.
  #
  # #4714: the defect was NOT this command — it was the `|| true` below, which
  # discarded a real failure so the user was told nothing while nothing was
  # captured. The failure is now RECORDED as a `capture-failure` breadcrumb.
  #
  # Do NOT switch this to `session capture`: that command parses via
  # `_parse_transcript(text)`, which is not harness-aware, so it finds ZERO
  # turns in a cursor transcript ("No conversation turns found in transcript")
  # and would turn a transient 504 into a PERMANENT silent no-capture. Measured
  # 2026-09-22.
  #
  # Fail-open is preserved: the hook still exits 0 and never blocks the
  # session — a capture failure must be EVIDENCE, not silence.
  if [ -n "$TORTOISE_BIN" ]; then
    ARGS=(sessions import --file "$TRANSCRIPT_PATH" --harness cursor)
    [ -n "$SESSION_ID" ] && ARGS+=(--session-id "$SESSION_ID")
    if ! _CAPTURE_ERR="$("$TORTOISE_BIN" "${ARGS[@]}" 2>&1)"; then
      _record_breadcrumb cursor "capture failed: ${_CAPTURE_ERR:-no output}" capture-failure
    fi
  else
    ARGS=(sessions import --file "$TRANSCRIPT_PATH" --harness cursor)
    [ -n "$SESSION_ID" ] && ARGS+=(--session-id "$SESSION_ID")
    # The resolved module dir travels via ENV and is prepended INSIDE ``-c``
    # — never string-interpolated into the source (a quote in the path must
    # not inject code) and never via ``-m``: CPython prepends the process CWD
    # ahead of PYTHONPATH for ``-m``, so a planted ``tortoise/`` package in
    # the agent's workspace would execute as the user (CWE-427, #4314).
    if ! _CAPTURE_ERR="$("$PYTHON_BIN" -c \
      'import sys; sys.path[:] = [p for p in sys.path if p not in ("", ".")]; sys.path.insert(0, sys.argv[1]); from tortoise.__main__ import main; raise SystemExit(main(sys.argv[2:]))' \
      "$TORTOISE_MODULE" "${ARGS[@]}" 2>&1)"; then
      _record_breadcrumb cursor "capture failed: ${_CAPTURE_ERR:-no output}" capture-failure
    fi
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
