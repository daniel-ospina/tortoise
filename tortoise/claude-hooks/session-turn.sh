#!/usr/bin/env bash
# tortoise-hook-version: 7
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
META="$(python3 -c 'import sys
# CWE-427: `python3 -c` puts the process cwd at sys.path[0], so a planted
# ./json.py in the session workspace would execute here — on EVERY prompt.
# `sys` is a builtin and cannot be shadowed, so importing it first is safe.
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
import json
try:
    d = json.load(sys.stdin)
    print(d.get("transcript_path") or "")
    print(d.get("session_id") or "")
except Exception:
    print(""); print("")' 2>/dev/null || true)"
TRANSCRIPT_PATH="$(printf '%s\n' "$META" | sed -n '1p')"
SESSION_ID="$(printf '%s\n' "$META" | sed -n '2p')"

[ -n "$TRANSCRIPT_PATH" ] || exit 0
# NO `[ -f "$TRANSCRIPT_PATH" ]` pre-check: `-f` is FALSE for an unreadable file
# (EACCES) and for a non-traversable parent (ENOTDIR/EACCES), so it silently
# skipped the turn — no spool write, no ledger line, for that turn and every
# later one. The conversion below classifies it instead and the ledger records
# `transcript_unreadable`; a MISSING transcript is not an error (there is simply
# nothing yet).

# Same Claude Code .jsonl → text-turn conversion as session-end.sh, so both
# hooks feed `tortoise session spool`/`capture` the identical input.
TMP="$(mktemp -t tortoise_session_turn.XXXXXX)"
trap 'rm -f "$TMP"' EXIT
CONVERT_RC=0
python3 - "$TRANSCRIPT_PATH" "$TMP" << 'PYEOF' || CONVERT_RC=$?
import sys
# CWE-427: `python3 -` sets sys.path[0] = '' (the cwd), so a planted ./json.py
# in the session workspace would execute here at EVERY prompt. `sys` is a
# builtin and cannot be shadowed, so it is safe to import before the drop.
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
import json
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
except FileNotFoundError:
    # No transcript yet is not an error, and not a loss.
    raise SystemExit(3)
except (OSError, UnicodeDecodeError) as exc:
    # An unreadable source (EACCES, a directory, a non-traversable parent) is a
    # LOSS, and a loss is never silent: fail so the hook records it below.
    # `UnicodeDecodeError` is a `ValueError`, NOT an `OSError` — and a killed
    # partial append leaves a torn MULTI-BYTE character in a real transcript,
    # which is exactly the loss this hook must never drop on the floor.
    # `os.path.exists()` must NOT be the test — it is FALSE for exactly these
    # cases (and so classified a loss as "nothing to do").
    print(f"unreadable transcript: {src}: {exc}", file=sys.stderr)
    raise SystemExit(2)
with open(dst, "w", encoding="utf-8") as f:
    f.write("\n".join(out))
PYEOF

if [ "$CONVERT_RC" -eq 2 ]; then
  # Record the loss in the SAME ledger `tortoise session spool` writes to
  # (`discarded.jsonl`, one JSON object per line — schema mirrors
  # capture_spool.record_discard). Best-effort: the hook still exits 0.
  python3 - "$SESSION_ID" "$TRANSCRIPT_PATH" << 'PYEOF' 2>/dev/null || true
import sys
# CWE-427: `python3 -` sets sys.path[0] = '' (the cwd), so a planted ./json.py
# in the session workspace would execute here at EVERY prompt. `sys` is a
# builtin and cannot be shadowed, so it is safe to import before the drop.
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
import datetime, json, os
sid, path = sys.argv[1], sys.argv[2]
root = os.environ.get("TORTOISE_CAPTURE_SPOOL_DIR") or os.path.join(
    os.path.expanduser("~"), ".tortoise", "capture-spool")
try:
    os.makedirs(root, exist_ok=True, mode=0o700)
    with open(os.path.join(root, "discarded.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "session_id": sid or f"unknown:{os.path.basename(path)}",
            "capture_key": None,
            "reason": "transcript_unreadable",
            "detail": f"{path}: the per-turn hook could not read the transcript",
        }) + "\n")
except OSError:
    pass
PYEOF
  exit 0
fi

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
  # ── The local capture-error breadcrumb ──────────────────────────────────
  # Mirrors `tortoise.__main__._record_capture_error` (same file layout, same
  # `TORTOISE_IMPORT_RECEIPT_DIR` override) for the one case that helper cannot
  # cover: the module dir did not resolve, so the Python helper is unreachable.
  # A hook that captures nothing must leave EVIDENCE, never silence (#4314).
  # Best-effort: a breadcrumb write can never break the exit-0 contract.
  _record_breadcrumb() {
    # PURE SHELL, no python3: this is also the evidence path for the "resolved a
    # module dir but found no interpreter" branch, which is reached BECAUSE
    # python3 is missing — a python3-written breadcrumb could never run there.
    local harness="$1" detail="$2"
    local receipt_dir crumb_dir stamp
    receipt_dir="${TORTOISE_IMPORT_RECEIPT_DIR:-${HOME:-/nonexistent}/.tortoise/import-receipts}"
    # Normalize to pathlib's `.parent` semantics (#4373 review): `Path(x).parent`
    # DROPS trailing slashes, `${x%/*}` does not — a trailing slash would make
    # the shell write `…/import-receipts/capture-errors/` while `session verify`
    # reads `…/capture-errors/`, hiding the breadcrumb and reading an inert
    # install as PROVEN.
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

  # Resolve a candidate module dir — accepted ONLY when it actually holds a
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
  if [ -z "$TORTOISE_MODULE" ]; then
    # A hook that captures nothing must leave EVIDENCE, never silence (#4314):
    # from a HOME-scoped install `../..` is `$HOME`, so the old expression
    # silently exited 0 having captured nothing at every single prompt.
    _record_breadcrumb "claude" \
      "the installed per-turn hook could not resolve a tortoise module dir (checked TORTOISE_SRC_DIR, \$HOME/.tortoise/hook-src-dir, and ../..), and captured nothing" \
      || true
    exit 0
  fi
  PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"
  if [ -z "$PYTHON_BIN" ]; then
    _record_breadcrumb "claude" \
      "the installed per-turn hook resolved a tortoise module dir but found no python3 interpreter, and captured nothing" \
      || true
    exit 0
  fi
  # CWE-427: the `-c` source is SINGLE-QUOTED and the module dir travels as an
  # argv ELEMENT — never string-interpolated (a quote in the path must not
  # inject code). `-c` puts the process CWD at sys.path[0], so the cwd is
  # dropped before any non-builtin import: a planted ./json.py in the agent's
  # workspace would otherwise execute as the user at EVERY prompt. `sys` is a
  # builtin and cannot be shadowed, so importing it first is safe.
  "$PYTHON_BIN" -c '
import sys
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
sys.path.insert(0, sys.argv[1])
from tortoise.__main__ import main
raise SystemExit(main(["session", "spool", *sys.argv[2:]]))
' "$TORTOISE_MODULE" "${SPOOL_ARGS[@]}" >/dev/null || exit 0
fi

exit 0
