#!/usr/bin/env bash
# tortoise-hook-version: 4
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
  # keeps it distinguishable from a ``sessions import`` capture failure (#4314).
  # #5838: the two kinds occupy SEPARATE slots, so this writer never touches
  # the ``capture-failure`` file and cannot destroy a live quota/network
  # refusal. Best-effort: a breadcrumb write can never break the exit-0
  # contract.
  # The ``kind`` argument distinguishes WHO is recording, and the distinction
  # is load-bearing: ``install-inert`` is the INSTALL leg's own evidence (an
  # installer that fired but captured nothing), while ``capture-failure`` is a
  # real capture failure. `session verify` READS the kind, so recording a
  # capture failure as ``install-inert`` would report a HEALTHY install as
  # INERT — precisely the inversion #4314 exists to prevent. It defaults to
  # the install-inert kind, so the pre-existing callers are unchanged.
  # #5838: the kind ALSO decides the SLOT (see below), and this is the ONE
  # place the filename is derived in this script.
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
  # contain double quotes (the 504 body is ``{"detail":"…"}``), and #4848
  # splices FILESYSTEM PATHS in, where a backslash is legal — unescaped, either
  # one made the breadcrumb unparseable by `session verify` (measured #4714),
  # and an unparseable record reads as NO record, i.e. as a healthy install.
  # #4848 review: the escape itself now lives in ONE shared helper inside the
  # guard block (`_tortoise_json_escape`), so this seam and its siblings cannot
  # drift into "escaped here, raw a file over".
  local esc esc_harness
  esc="$(_tortoise_json_escape "$detail")"
  esc_harness="$(_tortoise_json_escape "$harness")"
  mkdir -p "$crumb_dir" 2>/dev/null || true
  # #5838: `capture-failure` keeps the historical ``<harness>.json``;
  # `install-inert` takes ``<harness>-install.json``. Decided from the KIND,
  # which is the one input both the writer and `session verify` agree on.
  # An unrecognised kind writes NOTHING.  The `*)` arm used to fall back to the
  # capture slot — the one carrying a live quota/network refusal, i.e. exactly
  # the collision #5838 exists to remove — and it did so silently.  `kind`
  # defaults to `install-inert` (``${3:-install-inert}``), so the 2-argument
  # callers above still take the install slot; this arm is reachable only by a
  # genuinely new kind, which must be given a slot deliberately.
  local crumb_file
  case "$kind" in
    capture-failure) crumb_file="$harness.json" ;;
    install-inert) crumb_file="$harness-install.json" ;;
    *) return 0 ;;
  esac
  # #5919: redirect the WHOLE write block. Bash opens redirections left to
  # right and reports a failed open of the STDOUT target BEFORE a trailing
  # `2>/dev/null` takes effect, so an unwritable target dir leaked the shell's
  # own error onto the worker's stderr. A block's stderr is established first.
  {
    printf '{\n  "harness": "%s",\n  "detail": "%s",\n  "recorded_at": "%s",\n  "kind": "%s"\n}\n' \
      "$esc_harness" "$esc" "$stamp" "$kind" \
      > "$crumb_dir/$crumb_file"
  } 2>/dev/null || true
}

# ── The detached worker half ─────────────────────────────────────────────
# Invoked as `$0 --worker` by the parent below. All slow work lives here; the
# parent has already returned by the time this runs.
if [ "${1:-}" = "--worker" ]; then
  PAYLOAD="${TORTOISE_CODEX_PAYLOAD:-}"
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
print(d.get("transcript_path") or "")
print(d.get("session_id") or "")
' < "$PAYLOAD" 2>/dev/null || true)"
  rm -f "$PAYLOAD" 2>/dev/null || true

  TRANSCRIPT_PATH="$(printf '%s\n' "$META" | sed -n '1p')"
  SESSION_ID="$(printf '%s\n' "$META" | sed -n '2p')"

  # Nullable per the Codex schema — nothing to file.
  [ -n "$TRANSCRIPT_PATH" ] || exit 0
  [ -f "$TRANSCRIPT_PATH" ] || exit 0

  # ── Resolve the capture entry (PATH install → .venv → module) ─────────
  # A candidate module dir is accepted ONLY when it actually holds a
  # `tortoise/` package. Candidate order: $TORTOISE_SRC_DIR, the installer's
  # recorded module dir, then `../..` — the LAST resort, because from an
  # installed hook that is `$HOME`, which is not a checkout. A candidate that
  # fails the `tortoise/` test is not a module dir, however plausible it looks
  # (#4314). A candidate that HOLDS the package but cannot be RUN is skipped
  # too, so the loop can still reach a later, usable one (#4848) — and the
  # first such candidate is remembered so the refusal can NAME it.
  TORTOISE_MODULE=""
  TORTOISE_UNUSABLE_DIR=""
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

  # `PYTHON_BIN` from the ENVIRONMENT is an explicit operator override and is
  # honoured as-is (see the guard above); it is never probed and never
  # overridden by a guess.
  TORTOISE_BIN="$(command -v tortoise || true)"
  TORTOISE_UNUSABLE_BIN=""
  if [ -n "$TORTOISE_BIN" ] && ! _tortoise_bin_qualifies "$TORTOISE_BIN"; then
    TORTOISE_UNUSABLE_BIN="$TORTOISE_BIN"
    TORTOISE_BIN=""
  fi
  if [ -z "$TORTOISE_BIN" ] && [ -n "$TORTOISE_MODULE" ] \
     && _tortoise_bin_qualifies "$TORTOISE_MODULE/.venv/bin/tortoise"; then
    TORTOISE_BIN="$TORTOISE_MODULE/.venv/bin/tortoise"
  fi
  if [ -z "$TORTOISE_BIN" ] && [ -n "${VIRTUAL_ENV:-}" ] \
     && _tortoise_bin_qualifies "$VIRTUAL_ENV/bin/tortoise"; then
    TORTOISE_BIN="$VIRTUAL_ENV/bin/tortoise"
  fi
  if [ -z "$TORTOISE_BIN" ] && [ -z "$TORTOISE_MODULE" ]; then
    # No binary and no module dir: record the breadcrumb (the SAME shape and
    # location `sessions import` writes) and exit 0 — an inert install must
    # leave evidence instead of silence (#4314). This runs in the DETACHED
    # worker, so the synchronous hook still returns inside Codex's ~1 s
    # SessionEnd budget.
    #
    # #4848: a module dir (or a `tortoise` binary) that WAS found but cannot be
    # run is a DIFFERENT cause with a different remedy — say which, and name the
    # interpreter, so the report stops pointing at `datetime`.
    if [ -n "$TORTOISE_UNUSABLE_DIR" ]; then
      _record_breadcrumb codex \
        "the installed Codex hook resolved a tortoise source dir at $TORTOISE_UNUSABLE_DIR but $(_tortoise_unusable_reason "$TORTOISE_UNUSABLE_DIR") and captured nothing. Remedy: create that checkout's venv (uv sync --extra embeddings --extra parity in it) or set TORTOISE_SRC_DIR to a checkout with a working .venv"
      exit 0
    fi
    if [ -n "$TORTOISE_UNUSABLE_BIN" ]; then
      _record_breadcrumb codex \
        "the installed Codex hook found a tortoise binary at $TORTOISE_UNUSABLE_BIN but the interpreter on its #! line cannot run it (tortoise needs Python $_TORTOISE_MIN_PY+), and captured nothing. Remedy: reinstall it (uv tool install --force .) or set TORTOISE_SRC_DIR to a checkout with a working .venv"
      exit 0
    fi
    _record_breadcrumb codex \
      "the installed Codex hook could not resolve a tortoise module dir (checked TORTOISE_SRC_DIR, \$HOME/.tortoise/hook-src-dir, and ../..), found no tortoise binary, and captured nothing"
    exit 0
  fi

  # Python fallback: run the resolved checkout via an ENV-fed ``-c`` prefix
  # (never ``-m`` — see the CWE-427 note at the invocation below).
  if [ -z "$TORTOISE_BIN" ]; then
    # #4848 review: the interpreter comes from `_tortoise_dir_python` — the SAME
    # precedence `_tortoise_dir_runnable` accepted the candidate on (the
    # checkout's own `.venv`, the `pyvenv.cfg` venv that OWNS a wheel install,
    # an active `$VIRTUAL_ENV`, then the ambient `python3`) — so a dir the guard
    # just blessed can never be refused here for want of an interpreter. The
    # older ladders here knew neither the owning venv nor `$VIRTUAL_ENV` in every
    # seam, and `PYTHON_BIN` was left UNSET on that path: under `set -u` the
    # refusal below then aborted at `unbound variable` BEFORE it could write its
    # breadcrumb — silent no-capture, the exact class #4848 removes.
    PYTHON_BIN="${PYTHON_BIN:-}"
    if [ -z "$PYTHON_BIN" ]; then
      PYTHON_BIN="$(_tortoise_dir_python "${TORTOISE_MODULE:-}" || true)"
    fi
    if [ -z "$PYTHON_BIN" ]; then
      # The module dir resolved but there is no USABLE interpreter to run it:
      # record the breadcrumb (evidence, not silence) and exit 0 (#4314),
      # naming the interpreter that was refused (#4848).
      _record_breadcrumb codex \
        "the installed Codex hook resolved a tortoise module dir at ${TORTOISE_MODULE:-?} but $(_tortoise_unusable_reason "${TORTOISE_MODULE:-}") and captured nothing. Remedy: create that checkout's venv (uv sync --extra embeddings --extra parity in it) or set TORTOISE_SRC_DIR to a checkout with a working .venv"
      exit 0
    fi
  fi

  # `sessions import --harness codex` is the canonical Codex capture step: it
  # parses the rollout with the HARNESS-AWARE `parse_transcript` dispatcher
  # (`parse_codex` returns 6 turns for a real rollout), POSTs the SAME
  # `/v1/sessions` payload the Claude hook sends (harness + the REAL
  # session_id as the idempotency key), and stages the parsed session locally.
  #
  # #4714: the defect was NOT this command — it was the `|| true` below, which
  # discarded a real failure so the user was told nothing while nothing was
  # captured. The failure is now RECORDED as a `capture-failure` breadcrumb.
  #
  # Do NOT switch this to `session capture`: that command parses via
  # `_parse_transcript(text)`, which is not harness-aware, so it finds ZERO
  # turns in a codex rollout ("No conversation turns found in transcript") and
  # would turn a transient 504 into a PERMANENT silent no-capture — strictly
  # worse. Measured 2026-09-22: `parse_codex` 6 turns, `sessions import`
  # reaches the server, `session capture` 0 turns.
  #
  # Fail-open is preserved: the hook still exits 0 and never blocks the
  # session — a capture failure must be EVIDENCE, not silence.
  if [ -n "$TORTOISE_BIN" ]; then
    ARGS=(sessions import --file "$TRANSCRIPT_PATH" --harness codex)
    [ -n "$SESSION_ID" ] && ARGS+=(--session-id "$SESSION_ID")
    if ! _CAPTURE_ERR="$("$TORTOISE_BIN" "${ARGS[@]}" 2>&1)"; then
      _record_breadcrumb codex "capture failed: ${_CAPTURE_ERR:-no output}" capture-failure
    fi
  else
    ARGS=(sessions import --file "$TRANSCRIPT_PATH" --harness codex)
    [ -n "$SESSION_ID" ] && ARGS+=(--session-id "$SESSION_ID")
    # The resolved module dir travels via ENV and is prepended INSIDE ``-c``
    # — never string-interpolated into the source (a quote in the path must
    # not inject code) and never via ``-m``: CPython prepends the process CWD
    # ahead of PYTHONPATH for ``-m``, so a planted ``tortoise/`` package in
    # the agent's workspace would execute as the user (CWE-427, #4314).
    if ! _CAPTURE_ERR="$("$PYTHON_BIN" -c \
      'import sys; sys.path[:] = [p for p in sys.path if p not in ("", ".")]; sys.path.insert(0, sys.argv[1]); from tortoise.__main__ import main; raise SystemExit(main(sys.argv[2:]))' \
      "$TORTOISE_MODULE" "${ARGS[@]}" 2>&1)"; then
      _record_breadcrumb codex "capture failed: ${_CAPTURE_ERR:-no output}" capture-failure
    fi
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
