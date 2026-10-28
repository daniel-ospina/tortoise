#!/usr/bin/env bash
# Tortoise per-turn volunteering-memory injection (epic #2080 end-state seams).
#
# UserPromptSubmit-style hook for the Claude-hooks-compatible harness family
# (Claude Code, Codex, Cline, Devin). Reads the hook's stdin JSON, extracts
# the user prompt, runs the ONE canonical reflex (`tortoise volunteer` — the
# shared tortoise/volunteer.py pipeline via POST /v1/context or the local
# SDK), and emits the per-harness hook output JSON carrying the injected
# context block.
#
# Install (per harness — all four wire a UserPromptSubmit hook that runs
# this script with a harness arg):
#   .codex/hooks.json       → "UserPromptSubmit": "…/volunteer-turn.sh codex"
#   .claude/settings.json   → "UserPromptSubmit": "…/volunteer-turn.sh claude"
#   .cline/…                → "UserPromptSubmit": "…/volunteer-turn.sh cline"
#   .devin/hooks.v1.json    → "UserPromptSubmit": [{"hooks": [{"type":
#                              "command", "command": "…/volunteer-turn.sh devin"}]}]
#                             (Devin's file IS the hooks object — no "hooks"
#                              wrapper key; docs.devin.ai/cli/extensibility/hooks)
# or agent-first: `tortoise install codex|claude|cline|devin` (writes the
# registration file — the #2123/#2124 agent-first onboarding mandate).
#
# Fail-open contract: if Tortoise is unreachable / misconfigured / the graph
# has nothing above the confidence gate, the script exits 0 with EMPTY output
# (the harness injects nothing; the turn proceeds untouched). A reflex
# failure must NEVER break the agent turn.
#
# Hook input contract (Claude/Codex UserPromptSubmit): stdin JSON with a
# top-level "prompt" string; older/other harnesses send a text blob. Both
# are accepted.
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

# ── The local capture-error breadcrumb ───────────────────────────────────
# Mirrors `tortoise.__main__._record_capture_error` (same file layout, same
# `TORTOISE_IMPORT_RECEIPT_DIR` override) for the one case that helper cannot
# cover: the module dir did not resolve, so the Python helper is unreachable.
# A hook that does nothing must leave EVIDENCE, never silence (#4314).
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
  local harness="$1" detail="$2"
  local receipt_dir crumb_dir stamp
  # #4848: the refusal now splices a FILESYSTEM PATH into `detail`, so an
  # unescaped backslash or quote would make the record unparseable — and an
  # unparseable breadcrumb reads as NO breadcrumb, i.e. as a healthy install.
  # One escaper, shared by every seam (it lives in the guard block above).
  harness="$(_tortoise_json_escape "$harness")"
  detail="$(_tortoise_json_escape "$detail")"
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
  mkdir -p "$crumb_dir" 2>/dev/null || true
  # #5919: redirect the WHOLE write block. Bash opens redirections left to
  # right and reports a failed open of the STDOUT target BEFORE a trailing
  # `2>/dev/null` takes effect, so an unwritable target dir leaked the shell's
  # own error onto the hook's stderr. A block's stderr is established first.
  {
    printf '{\n  "harness": "%s",\n  "detail": "%s",\n  "recorded_at": "%s",\n  "kind": "install-inert"\n}\n' \
      "$harness" "$detail" "$stamp" \
      > "$crumb_dir/$harness-install.json"
  } 2>/dev/null || true
}

HARNESS="${1:-codex}"
IN="$(cat)"

# ── Extract the user prompt from the hook input ──────────────────────────
PROMPT=""
if [ -n "$IN" ]; then
  PROMPT="$(printf '%s' "$IN" | python3 -c '
import sys
# CWE-427: drop the process cwd before importing `json` — `python -c` puts cwd
# at sys.path[0], so a planted ./json.py in the session workspace would execute
# on every prompt. `sys` is builtin and cannot be shadowed.
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
import json

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # not JSON — caller falls back to the raw stdin text
if isinstance(d, str):
    print(d)
elif isinstance(d, dict):
    p = d.get("prompt") or d.get("userPrompt") or ""
    if isinstance(p, str):
        print(p)
' 2>/dev/null || true)"
fi
if [ -z "$PROMPT" ]; then
  PROMPT="$IN"
fi
PROMPT="$(printf '%s' "$PROMPT" | tr -d '\r' | head -c 15000 || true)"
if [ -z "$(printf '%s' "$PROMPT" | tr -d '[:space:]')" ]; then
  exit 0
fi

# ── Resolve the reflex entry (PATH install → .venv → module) ───────────
# A candidate module dir is accepted ONLY when it actually holds a
# `tortoise/` package. Candidate order: $TORTOISE_SRC_DIR, the installer's
# recorded module dir, then `../..` — the LAST resort, because from an
# installed hook that is `$HOME`, which is not a checkout (#4314). A
# candidate that HOLDS the package but cannot be RUN is skipped too, so the
# loop can still reach a later, usable one (#4848) — and the first such
# candidate is remembered so the refusal can NAME it.
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
# honoured as-is (see the guard above); it is never probed and never overridden
# by a guess.
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

# ── The venv that OWNS the module dir (the WHEEL case) ───────────────────
# A wheel install puts this hook in ``site-packages/tortoise/claude-hooks/``,
# so the module dir is ``site-packages`` — there is NO ``.venv`` beneath it
# and the source-checkout probe above can never match (#2385 item 3). The
# venv that owns site-packages is the one whose ``bin/tortoise`` console
# script and ``bin/python`` have the wheel's dependencies, and it is marked
# by a ``pyvenv.cfg`` at the venv root. POSIX
# (``lib/python3.x/site-packages``) and Windows (``Lib/site-packages``) differ
# in depth, so walk UP to the marker (bounded) instead of assuming a fixed
# ``../../..``. Without this, the module fallback runs under the ambient
# ``python3`` — which does not have the wheel's dependencies — and the hook
# silently injects nothing while reporting success.
TORTOISE_VENV=""
if [ -n "$TORTOISE_MODULE" ]; then
  _probe="$TORTOISE_MODULE"
  _depth=0
  while [ "$_depth" -lt 5 ]; do
    if [ -z "$_probe" ] || [ "$_probe" = "/" ]; then
      break
    fi
    if [ -f "$_probe/pyvenv.cfg" ]; then
      TORTOISE_VENV="$_probe"
      break
    fi
    _probe="${_probe%/*}"
    _depth=$((_depth + 1))
  done
fi
if [ -z "$TORTOISE_BIN" ] && [ -n "$TORTOISE_VENV" ] \
   && _tortoise_bin_qualifies "$TORTOISE_VENV/bin/tortoise"; then
  TORTOISE_BIN="$TORTOISE_VENV/bin/tortoise"
fi
if [ -z "$TORTOISE_BIN" ] && [ -n "${VIRTUAL_ENV:-}" ] \
   && _tortoise_bin_qualifies "$VIRTUAL_ENV/bin/tortoise"; then
  TORTOISE_BIN="$VIRTUAL_ENV/bin/tortoise"
fi
if [ -z "$TORTOISE_BIN" ] && [ -z "$TORTOISE_MODULE" ]; then
  # No binary and no module dir: record the breadcrumb (the SAME shape and
  # location `sessions import` writes) and exit 0 — an inert install must
  # leave evidence instead of silence (#4314).
  #
  # #4848: a module dir (or a `tortoise` binary) that WAS found but cannot be
  # run is a DIFFERENT cause with a different remedy — say which, and name the
  # interpreter, so the report stops pointing at `datetime`.
  if [ -n "$TORTOISE_UNUSABLE_DIR" ]; then
    _record_breadcrumb "$HARNESS" \
      "the installed volunteer hook resolved a tortoise source dir at $TORTOISE_UNUSABLE_DIR but $(_tortoise_unusable_reason "$TORTOISE_UNUSABLE_DIR") and injected nothing. Remedy: create that checkout's venv (uv sync --extra embeddings --extra parity in it) or set TORTOISE_SRC_DIR to a checkout with a working .venv" \
      || true
    exit 0
  fi
  if [ -n "$TORTOISE_UNUSABLE_BIN" ]; then
    _record_breadcrumb "$HARNESS" \
      "the installed volunteer hook found a tortoise binary at $TORTOISE_UNUSABLE_BIN but the interpreter on its #! line cannot run it (tortoise needs Python $_TORTOISE_MIN_PY+), and injected nothing. Remedy: reinstall it (uv tool install --force .) or set TORTOISE_SRC_DIR to a checkout with a working .venv" \
      || true
    exit 0
  fi
  _record_breadcrumb "$HARNESS" \
    "the installed volunteer hook could not resolve a tortoise module dir (checked TORTOISE_SRC_DIR, \$HOME/.tortoise/hook-src-dir, and ../..), found no tortoise binary, and injected nothing" \
    || true
  exit 0
fi
# Python fallback for a source checkout: prefer the checkout's own venv so
# the module runs under an interpreter that actually has tortoise installed.
# A wheel install has no checkout venv, so fall back to the venv that OWNS
# the resolved module dir (#2385 item 3) before the ambient python3.
# #4848 review: that precedence is `_tortoise_dir_python` — the SAME one
# `_tortoise_dir_runnable` accepted the candidate on — so a dir the guard just
# blessed can never be refused below for want of an interpreter.
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$TORTOISE_BIN" ]; then
  if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(_tortoise_dir_python "${TORTOISE_MODULE:-}" || true)"
  fi
  # The owning venv resolved from a console script is an EQUIVALENT route to the
  # same tree (#2385) and stays as its own leg for the case where only the
  # binary was found.
  if [ -z "$PYTHON_BIN" ] && [ -n "$TORTOISE_VENV" ] \
     && _tortoise_py_qualifies "$TORTOISE_VENV/bin/python"; then
    PYTHON_BIN="$TORTOISE_VENV/bin/python"
  fi
  if [ -z "$PYTHON_BIN" ]; then
    # The module dir resolved but there is no USABLE interpreter to run it:
    # record the breadcrumb (evidence, not silence) and exit 0 (#4314), naming
    # the interpreter that was refused (#4848).
    _record_breadcrumb "$HARNESS" \
      "the installed volunteer hook resolved a tortoise module dir at ${TORTOISE_MODULE:-?} but $(_tortoise_unusable_reason "${TORTOISE_MODULE:-}") and injected nothing. Remedy: create that checkout's venv (uv sync --extra embeddings --extra parity in it) or set TORTOISE_SRC_DIR to a checkout with a working .venv" \
      || true
    exit 0
  fi
fi

# python3 (hook-input JSON parsing + per-harness output assembly) is a hard
# requirement once we reach emission — preflight it so a missing interpreter
# can never violate the fail-open exit-0 contract.
PY3="$(command -v python3 || true)"
[ -z "$PY3" ] && exit 0

# ── Run the reflex (fail-open: any failure → empty injection) ────────────
# The reflex prints a run-time endpoint-mode note on stderr when it runs
# hosted against a file config (#2369 D1.3). Capture that stderr and relay
# ONLY the note marker to OUR stderr (the harness log surfaces it), so the
# endpoint of a hosted run is visible at run time — while genuine reflex
# error noise stays out of the hook (stdout remains the only injection
# channel).
REFLEX_ERR="$(mktemp "${TMPDIR:-/tmp}/tortoise-reflex.XXXXXX" 2>/dev/null)" \
  || REFLEX_ERR=""
if [ -z "$REFLEX_ERR" ]; then
  REFLEX_ERR="$(mktemp 2>/dev/null)" || REFLEX_ERR="/tmp/tortoise-reflex.$$"
fi
trap 'rm -f "$REFLEX_ERR"' EXIT

BLOCK=""
if [ -n "$TORTOISE_BIN" ]; then
  BLOCK="$(printf '%s' "$PROMPT" | "$TORTOISE_BIN" volunteer 2>"$REFLEX_ERR" || true)"
else
  # Module fallback: run the resolved checkout via ``-c``. The path travels
  # via ENV and is prepended INSIDE the ``-c`` source — never via ``-m``
  # (CPython prepends the process CWD ahead of PYTHONPATH for ``-m``, so a
  # planted ``tortoise/`` package in the agent's workspace would execute as
  # the user and its stdout is injected into the model context, CWE-427)
  # and never string-interpolated into the source (a quote in the path must
  # not inject code).
  BLOCK="$(printf '%s' "$PROMPT" | \
    "$PYTHON_BIN" -c 'import sys; sys.path[:] = [p for p in sys.path if p not in ("", ".")]; sys.path.insert(0, sys.argv[1]); from tortoise.__main__ import main; raise SystemExit(main(sys.argv[2:]))' "$TORTOISE_MODULE" volunteer 2>"$REFLEX_ERR" || true)"
fi
# Relay the #2369 endpoint-mode note (marker-filtered; only emitted on
# hosted-against-file runs) to the hook's stderr for the harness log.
if [ -s "$REFLEX_ERR" ]; then
  grep -F "tortoise: hosted-mode note:" "$REFLEX_ERR" >&2 || true
fi
if [ -z "$(printf '%s' "$BLOCK" | tr -d '[:space:]')" ]; then
  exit 0
fi

# ── Emit the per-harness hook output contract (block via env — safe for
#    quotes/backslashes/newlines) ─────────────────────────────────────────
export TORTOISE_VOLUNTEER_BLOCK="$BLOCK"
case "$HARNESS" in
  claude|codex|devin)
    # Claude-hooks-shaped: hookSpecificOutput.additionalContext (Codex
    # UserPromptSubmit and Devin hooks.v1 use the identical contract).
    "$PY3" -c "
import sys
# CWE-427: -c puts the cwd at sys.path[0], so a planted ./json.py or ./os.py
# in the session workspace would execute on every prompt. This source is a
# DOUBLE-quoted shell string, so two rules apply: the predicate must avoid
# quotes (p and p != '.' is p not in ('', '.')), and the comments must contain
# NO backticks, which the shell would command-substitute and splice into this
# source. Neither rule is theoretical: an earlier revision of this comment ran
# three bogus commands here on every prompt.
sys.path[:] = [p for p in sys.path if p and p != '.']
import json, os
print(json.dumps({'hookSpecificOutput': {
    'hookEventName': 'UserPromptSubmit',
    'additionalContext': os.environ.get('TORTOISE_VOLUNTEER_BLOCK', '')}}))
" 2>/dev/null || true
    ;;
  cline)
    # Cline UserPromptSubmit → contextModification.context.
    "$PY3" -c "
import sys
# CWE-427: same double-quoted source and same quote-free predicate as the
# claude|codex|devin arm above - and likewise NO backticks in this comment.
sys.path[:] = [p for p in sys.path if p and p != '.']
import json, os
print(json.dumps({'contextModification': {
    'context': os.environ.get('TORTOISE_VOLUNTEER_BLOCK', '')}}))
" 2>/dev/null || true
    ;;
  *)
    # Unknown harness: print the block plainly so the hook never fails a turn.
    # That is NOT an injection, and it is NOT the fail-open posture: a harness
    # that parses hook stdout (Gemini CLI's strict-JSON rule) renders non-JSON
    # text as a systemMessage SHOWN TO THE USER, and a harness that parses
    # nothing delivers nothing to the model. A per-harness arm is a small
    # addition once demand arrives — see docs/research/2026-09-01-gbrain-learnings/platform-seams.md
    # (correction log, #2124). The comment that stood here presented the bare
    # block as harmless; that claim is retracted.
    printf '%s\n' "$BLOCK"
    ;;
esac
exit 0
