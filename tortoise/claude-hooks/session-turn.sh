#!/usr/bin/env bash
# tortoise-hook-version: 12
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

# ── The interpreter guard (#4848) ─────────────────────────────────────────
# BEGIN tortoise-interp-guard (#4848)
#
# A capture seam must never hand the `tortoise` package to an interpreter that
# cannot import it. `python3` on a stock macOS box is 3.9 while the package
# needs 3.12+ (`datetime.UTC`, `tortoise/quota.py`), so a source tree holding
# `tortoise/` but no `.venv` used to be ACCEPTED on that test alone and then
# run under the ambient interpreter — dying with
# `ImportError: cannot import name 'UTC' from 'datetime'`, an error that names
# nothing near the missing venv, at rc 0, so the only trace was a breadcrumb
# nobody read. EXISTENCE IS NOT USABILITY (#4848): every candidate this seam is
# about to EXECUTE is probed first, and an unusable one is SKIPPED so the
# resolution loop can still reach a later, runnable candidate.
#
# The floor travels as ARGV to the probe, so the check and the refusal text
# cannot disagree and one edit moves both. `PYTHON_BIN` in the ENVIRONMENT is an
# explicit operator override and is honoured as-is — never probed, never
# second-guessed.
_TORTOISE_MIN_PY="3.12"

# 0 when "$1" is an interpreter that can run this package, 1 otherwise.
# An unanswerable probe (missing, not executable, a python 2, a shell script
# wearing the name `python3`) is a REFUSAL: for an interpreter the seam is about
# to execute, "cannot tell" is not "yes".
_tortoise_py_qualifies() {
  if [ -z "${1:-}" ] || [ ! -x "$1" ]; then
    return 1
  fi
  "$1" -c 'import sys
raise SystemExit(0 if sys.version_info >= tuple(int(p) for p in sys.argv[1].split(".")) else 1)' \
    "$_TORTOISE_MIN_PY" >/dev/null 2>&1
}

# The interpreter a console script names on its `#!` line, or nothing when the
# script is not python at all. A console script is only as usable as that
# interpreter: a stale venv's `bin/tortoise` still names one that no longer
# exists, and accepting it runs into `bad interpreter` — the same silent
# no-capture, one layer down. UNKNOWN (a `#!/bin/sh` wrapper, an unreadable
# file) is NOT a refusal: this guard only judges what it can see.
_tortoise_script_interp() {
  local first rest cmd
  if [ -z "${1:-}" ] || [ ! -f "$1" ] || [ ! -r "$1" ]; then
    return 0
  fi
  first=""
  IFS= read -r first < "$1" 2>/dev/null || return 0
  case "$first" in
    '#!'*) : ;;
    *) return 0 ;;
  esac
  rest="${first#\#!}"
  rest="${rest#"${rest%%[![:space:]]*}"}"
  cmd="${rest%%[[:space:]]*}"
  case "$cmd" in
    */env | env)
      # `#!/usr/bin/env python3` names an interpreter to LOOK UP, not to test.
      rest="${rest#"$cmd"}"
      rest="${rest#"${rest%%[![:space:]]*}"}"
      case "$rest" in
        -* | '') return 0 ;;
      esac
      cmd="${rest%%[[:space:]]*}"
      ;;
  esac
  case "$cmd" in
    *python*) printf '%s\n' "$cmd" ;;
  esac
  return 0
}

# 0 when the console script "$1" would run under a qualifying interpreter.
_tortoise_bin_qualifies() {
  local interp
  if [ -z "${1:-}" ] || [ ! -x "$1" ]; then
    return 1
  fi
  interp="$(_tortoise_script_interp "$1")"
  if [ -z "$interp" ]; then
    return 0
  fi
  case "$interp" in
    /*) : ;;
    *) interp="$(command -v "$interp" 2>/dev/null || true)" ;;
  esac
  _tortoise_py_qualifies "$interp"
}

# The interpreter this seam WOULD use for the module dir "$1", by the same
# precedence the seams resolve in. Prints nothing when there is none at all.
# DIAGNOSIS only — never a gate.
_tortoise_candidate_python() {
  local dir="${1:-}"
  if [ -n "${PYTHON_BIN:-}" ]; then
    printf '%s\n' "$PYTHON_BIN"
  elif [ -n "$dir" ] && [ -x "$dir/.venv/bin/python" ]; then
    printf '%s\n' "$dir/.venv/bin/python"
  elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    printf '%s\n' "$VIRTUAL_ENV/bin/python"
  else
    command -v python3 2>/dev/null || true
  fi
}

# The interpreter this seam would RUN the module dir "$1" with, or nothing when
# there is none. THIS is the precedence: `_tortoise_dir_runnable` accepts a
# candidate exactly when something can run it, and the seam's own resolution
# takes its interpreter from HERE — so ACCEPTANCE and EXECUTION cannot disagree.
# They did (#4848 review): the predicate blessed a wheel tree through the
# `pyvenv.cfg` walk-up and an active `$VIRTUAL_ENV` while the resolution
# ladders knew neither, so a candidate the guard had just called RUNNABLE was
# then refused as un-runnable — or, in the codex/cursor seams, crashed at an
# unset `PYTHON_BIN` BEFORE the refusal could record its breadcrumb. One
# predicate, one answer.
_tortoise_dir_python() {
  local dir="${1:-}" probe depth found
  if [ -n "${PYTHON_BIN:-}" ]; then
    printf '%s\n' "$PYTHON_BIN"
    return 0
  fi
  if [ -n "$dir" ] && _tortoise_py_qualifies "$dir/.venv/bin/python"; then
    printf '%s\n' "$dir/.venv/bin/python"
    return 0
  fi
  probe="$dir"
  depth=0
  while [ "$depth" -lt 5 ]; do
    if [ -z "$probe" ] || [ "$probe" = "/" ]; then
      break
    fi
    if [ -f "$probe/pyvenv.cfg" ]; then
      if _tortoise_py_qualifies "$probe/bin/python"; then
        printf '%s\n' "$probe/bin/python"
        return 0
      fi
      break
    fi
    probe="${probe%/*}"
    depth=$((depth + 1))
  done
  if [ -n "${VIRTUAL_ENV:-}" ] && _tortoise_py_qualifies "$VIRTUAL_ENV/bin/python"; then
    printf '%s\n' "$VIRTUAL_ENV/bin/python"
    return 0
  fi
  found="$(command -v python3 2>/dev/null || true)"
  if _tortoise_py_qualifies "$found"; then
    printf '%s\n' "$found"
    return 0
  fi
  return 1
}

# 0 when this seam can actually RUN the checkout at "$1". Holding `tortoise/`
# is not that (#4848): the tree must offer an entry point this guard accepts. A
# WHEEL install resolves a module dir INSIDE a venv's site-packages, where there
# is no `.venv` beneath it — the venv that OWNS the tree is the one marked by
# `pyvenv.cfg`, found by walking up (bounded), the same way the seams' own wheel
# probe does (#2385). The console-script legs stay here because a seam that
# resolves `TORTOISE_BIN` runs the SCRIPT, not an interpreter; every interpreter
# leg lives in `_tortoise_dir_python` alone, so acceptance and execution share
# one answer instead of two that can drift (#4848 review).
_tortoise_dir_runnable() {
  local dir="${1:-}" probe depth
  if [ -z "$dir" ]; then
    return 1
  fi
  if _tortoise_bin_qualifies "$dir/.venv/bin/tortoise"; then
    return 0
  fi
  probe="$dir"
  depth=0
  while [ "$depth" -lt 5 ]; do
    if [ -z "$probe" ] || [ "$probe" = "/" ]; then
      break
    fi
    if [ -f "$probe/pyvenv.cfg" ]; then
      if _tortoise_bin_qualifies "$probe/bin/tortoise"; then
        return 0
      fi
      break
    fi
    probe="${probe%/*}"
    depth=$((depth + 1))
  done
  _tortoise_dir_python "$dir" >/dev/null 2>&1
}

# JSON-escape "$1" for a breadcrumb `detail`/`harness` field. A capture error is
# arbitrary server text and DOES contain double quotes (the 504 body is
# `{"detail":"…"}`), and #4848 splices FILESYSTEM PATHS into the same field — a
# backslash (legal on macOS/Linux) or a quote turns the record into invalid
# JSON. That is not cosmetic: `session verify` cannot tell an unparseable
# breadcrumb from NO breadcrumb (`session_verify.py` returns None on ValueError),
# so an inert install would report as PROVEN — the #4314 inversion this file
# exists to prevent. Order matters: backslash FIRST, then the quote, then the
# control characters (each insertion adds its own backslash, so escaping the
# backslash last would double-escape it). The C0 set matters for more than
# cosmetics: `sessions import` ALREADY writes this same file with `json.dumps`
# on its own failure branches, so an unescaped tab or CR here would REPLACE a
# valid breadcrumb with an unparseable one and destroy the evidence this change
# exists to preserve (#4714). BS and FF use their JSON escapes rather than being
# dropped, and those two ARE lossless. The remaining C0 bytes (0x01-0x07, VT,
# 0x0E-0x1F) have NO JSON escape and are DELIBERATELY DISCARDED by the sweep
# below: a per-byte `\u00XX` rewrite is not expressible in pure shell (this
# function is also the evidence path reached BECAUSE python3 is missing), and a
# raw ESC or VT inside a path is a hostile-byte signal, not something a user
# reads. The consequence is named here so it is not mistaken for losslessness:
# a path carrying one of those bytes is recorded with that byte REMOVED, so the
# remedy text can name a slightly different path. Everything else round-trips
# exactly and the record is always parseable — which is what makes it evidence.
# PURE SHELL: this must run where python3 does not exist.
_tortoise_json_escape() {
  local esc="${1:-}"
  esc="${esc//\\/\\\\}"
  esc="${esc//\"/\\\"}"
  esc="${esc//$'\n'/\\n}"
  esc="${esc//$'\r'/\\r}"
  esc="${esc//$'\t'/\\t}"
  esc="${esc//$'\b'/\\b}"
  esc="${esc//$'\f'/\\f}"
  esc="$(printf '%s' "$esc" | tr -d '\000-\010\013\014\016-\037')"
  printf '%s' "$esc"
  return 0
}

# The clause a refusal splices in when a candidate was found but could not be
# run. Names the interpreter AND its version, because the whole defect is a
# message that pointed at `datetime` instead of at "wrong interpreter".
_tortoise_unusable_reason() {
  local dir="${1:-}" probe version
  probe="$(_tortoise_candidate_python "$dir")"
  if [ -z "$probe" ]; then
    printf 'found no python3 interpreter'
    return 0
  fi
  version="$("$probe" -c 'import platform
print(platform.python_version())' 2>/dev/null || true)"
  if [ -z "$version" ]; then
    version="an unrecognised version"
  fi
  printf 'the interpreter available is %s at %s and cannot run it (tortoise needs Python %s+)' \
    "$version" "$probe" "$_TORTOISE_MIN_PY"
}
# END tortoise-interp-guard (#4848)

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

# The breadcrumb writer is defined here, OUTSIDE the `else` branch: the
# binary leg below is now subject to the same #4848 refusal, and a refusal
# it cannot record is no evidence at all.
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
  # The record is INSTALL-leg evidence (``kind: install-inert``), kept
  # distinguishable from a ``sessions import`` capture failure (#4314).
  # #5838: the two kinds occupy SEPARATE slots, so this writer never touches
  # the ``capture-failure`` file and cannot destroy a live quota/network
  # refusal. NOTE this hook's stdout MUST stay empty (see the header); the
  # slot split adds no output here, unlike `session-start.sh`.
  local harness="$1" detail="$2"
  local receipt_dir crumb_dir stamp
  # #4848: the refusal now splices a FILESYSTEM PATH into `detail`, so an
  # unescaped backslash or quote would make the record unparseable — and an
  # unparseable breadcrumb reads as NO breadcrumb, i.e. as a healthy install.
  # One escaper, shared by every seam (it lives in the guard block above).
  harness="$(_tortoise_json_escape "$harness")"
  detail="$(_tortoise_json_escape "$detail")"
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
    > "$crumb_dir/$harness-install.json" 2>/dev/null || true
}

TORTOISE_BIN="$(command -v tortoise || true)"
# #4848 review: the PATH binary is EXECUTED, so it is PROBED like every
# other entry point. A stale `bin/tortoise` names an interpreter that may be
# gone (`bad interpreter`), and the `|| exit 0` below turns that into a
# silent no-capture — for THIS seam that means every prompt, with no record
# at all, which `session verify` reads as PROVEN (the #4314 inversion).
TORTOISE_UNUSABLE_BIN=""
if [ -n "$TORTOISE_BIN" ] && ! _tortoise_bin_qualifies "$TORTOISE_BIN"; then
  TORTOISE_UNUSABLE_BIN="$TORTOISE_BIN"
  TORTOISE_BIN=""
fi
# #4848 review round 3: a stale binary on PATH is one MORE candidate this seam
# must SKIP, never a dead end. Refusing here — BEFORE the module loop — left the
# loop's own skip rule unapplied in the one seam that fires on every prompt, so
# a host with a leftover `uv tool install` earlier on PATH and a perfectly
# runnable checkout recorded in `hook-src-dir` kept the stale install and
# recovered nothing: exactly the outcome #4848's acceptance decision exists to
# prevent ("the loop still preferring an unusable candidate over a usable later
# one"). It also made the refusal's own remedy a lie: it offered "set
# TORTOISE_SRC_DIR to a checkout with a working .venv", but that variable is
# read only INSIDE the loop below, after the `exit 0` — a user who followed the
# message saw no change and had no way to tell why, which is the defect class
# this change is about. A runnable checkout now WINS over the stale binary, and
# the refusal fires only when nothing else resolved.
TORTOISE_MODULE=""
TORTOISE_UNUSABLE_DIR=""
if [ -z "$TORTOISE_BIN" ]; then

  # Resolve a candidate module dir — accepted ONLY when it actually holds a
  # `tortoise/` package. Candidate order: $TORTOISE_SRC_DIR, the installer's
  # recorded module dir, then `../..` — the LAST resort, because from an
  # installed hook that is `$HOME`, which is not a checkout (#4314). A
  # candidate that HOLDS the package but cannot be RUN is skipped too, so the
  # loop can still reach a later, usable one (#4848) — and the first such
  # candidate is remembered so the refusal can NAME it.
  for CANDIDATE in "${TORTOISE_SRC_DIR:-}" \
                   "$(cat "${HOME:-/nonexistent}/.tortoise/hook-src-dir" 2>/dev/null || true)" \
                   "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd || true)"; do
    if [ -n "$CANDIDATE" ] && [ -d "$CANDIDATE/tortoise" ]; then
      if _tortoise_dir_runnable "$CANDIDATE"; then
        TORTOISE_MODULE="$CANDIDATE"
        break
      fi
      [ -n "$TORTOISE_UNUSABLE_DIR" ] || TORTOISE_UNUSABLE_DIR="$CANDIDATE"
    fi
  done
  # #4848 review: a candidate the loop just ACCEPTED on its console script must be
  # the thing this seam RUNS. `_tortoise_dir_runnable` accepts `$dir/.venv/bin/
  # tortoise` even when its `#!` is not python (an UNKNOWN interpreter is never
  # refused), so resolving only `command -v tortoise` + an interpreter left that
  # leg unexecuted: the loop broke on the accepted candidate and the seam then
  # refused it — instead of running it, or skipping to a later candidate. The
  # other seams (codex/cursor session-end, volunteer-turn, cursor session-start)
  # already resolve from the module dir; these three did not.
  if [ -z "$TORTOISE_BIN" ] && [ -n "${TORTOISE_MODULE:-}" ] \
     && _tortoise_bin_qualifies "$TORTOISE_MODULE/.venv/bin/tortoise"; then
    TORTOISE_BIN="$TORTOISE_MODULE/.venv/bin/tortoise"
  fi
fi
if [ -n "$TORTOISE_BIN" ]; then
  # stdout MUST stay empty (UserPromptSubmit injects it into the user's prompt),
  # but stderr is NOT swallowed: this is the mechanism of record, and a silent
  # no-op (a wrong interpreter, an unreadable transcript) is precisely the
  # "losing sessions silently" failure #3963 exists to remove.
  "$TORTOISE_BIN" session spool "${SPOOL_ARGS[@]}" >/dev/null || exit 0
  exit 0
fi
if [ -z "$TORTOISE_MODULE" ]; then
  # A hook that captures nothing must leave EVIDENCE, never silence (#4314):
  # from a HOME-scoped install `../..` is `$HOME`, so the old expression
  # silently exited 0 having captured nothing at every single prompt.
  # #4848: a binary that WAS found but cannot be RUN is a different cause with a
  # different remedy, and it is named FIRST — it is the more specific of the two
  # states, and it is reachable only once no checkout resolved above.
  if [ -n "$TORTOISE_UNUSABLE_BIN" ]; then
    _record_breadcrumb "claude" \
      "the installed per-turn hook found a tortoise binary at $TORTOISE_UNUSABLE_BIN but the interpreter on its #! line cannot run it (tortoise needs Python $_TORTOISE_MIN_PY+), and no runnable checkout was found instead, so it captured nothing. Remedy: reinstall it (uv tool install --force .) or set TORTOISE_SRC_DIR to a checkout with a working .venv" \
      || true
    exit 0
  fi
  # #4848: a dir that WAS found but cannot be run is a different cause with a
  # different remedy, and the refusal names the interpreter.
  if [ -n "$TORTOISE_UNUSABLE_DIR" ]; then
    _record_breadcrumb "claude" \
      "the installed per-turn hook resolved a tortoise source dir at $TORTOISE_UNUSABLE_DIR but $(_tortoise_unusable_reason "$TORTOISE_UNUSABLE_DIR") and captured nothing. Remedy: create that checkout's venv (uv sync --extra embeddings --extra parity in it) or set TORTOISE_SRC_DIR to a checkout with a working .venv" \
      || true
    exit 0
  fi
  _record_breadcrumb "claude" \
    "the installed per-turn hook could not resolve a tortoise module dir (checked TORTOISE_SRC_DIR, \$HOME/.tortoise/hook-src-dir, and ../..), and captured nothing" \
    || true
  exit 0
fi
# `PYTHON_BIN` from the ENVIRONMENT is an explicit operator override and is
# honoured as-is; the resolved fallback is PROBED, so an interpreter too old
# for the package is refused here instead of failing inside the CLI (#4848).
# The checkout's OWN venv is preferred: it owns the package's dependencies
# (#2385), and the guard has already probed it.
# #4848 review: the interpreter comes from `_tortoise_dir_python` — the SAME
# precedence `_tortoise_dir_runnable` accepted the candidate on — so a dir the
# guard just blessed can never be refused here for want of an interpreter.
if [ -z "${PYTHON_BIN:-}" ]; then
  PYTHON_BIN="$(_tortoise_dir_python "${TORTOISE_MODULE:-}" || true)"
fi
if [ -z "${PYTHON_BIN:-}" ]; then
  _record_breadcrumb "claude" \
    "the installed per-turn hook resolved a tortoise module dir at $TORTOISE_MODULE but $(_tortoise_unusable_reason "$TORTOISE_MODULE") and captured nothing. Remedy: create that checkout's venv (uv sync --extra embeddings --extra parity in it) or set TORTOISE_SRC_DIR to a checkout with a working .venv" \
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

exit 0
