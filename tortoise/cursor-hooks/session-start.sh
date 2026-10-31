#!/usr/bin/env bash
# tortoise-hook-version: 5
# Tortoise memory injection for Cursor — sessionStart hook (#2122).
#
# The `tortoise-hook-version` marker above is the install-contract generation
# for this hook (see tortoise/hook_install.py): column-0, one per file, bumped
# on ANY behavioural edit. Cursor's seam is graded as ONE generation, so this
# file and `session-end.sh` carry the SAME number.
#
# ── WHY sessionStart, AND NOT A PER-TURN HOOK ──────────────────────────────
# Cursor's per-turn event is `beforeSubmitPrompt`, and its output contract is
# `{continue, user_message}` — it can OBSERVE and BLOCK a prompt, it cannot
# INJECT anything into the model's context (verified against Cursor's own hook
# reference on 2026-10-11: "Output { continue: true | false, user_message }").
# There is no `additional_context` (or Claude's `additionalContext`) on that
# event, so the shared per-turn reflex (`claude-hooks/volunteer-turn.sh`) has
# nothing to deliver here and #2122's watch item stays open upstream.
#
# The ONE push seam Cursor documents is `sessionStart`:
#
#     // Output
#     { "env": { "<key>": "<value>" },
#       "additional_context": "<context to add to conversation>" }
#
# `additional_context` (snake_case — Cursor does NOT read Claude Code's
# `additionalContext`) is added to the conversation's initial system context,
# so this hook delivers the same cold-context digest the Claude Code
# SessionStart hook injects (`tortoise context`), through Cursor's own field.
# The hook is registered NATIVELY in `~/.cursor/hooks.json` (see
# `tortoise install cursor`), which is why only the native field is emitted:
# a native `sessionStart` entry is parsed against Cursor's native schema, and
# Cursor's config validation is strict in this codebase's measurements
# (#3819 — an unknown key invalidates the WHOLE document), so no undocumented
# sibling key is added "just in case".
#
# ── FAIL-OPEN CONTRACT (unchanged from every other seam) ───────────────────
# Nothing to say (empty digest) / no interpreter / no reflex at all → the hook
# prints NOTHING and exits 0: Cursor injects nothing and the session starts
# untouched. A hook failure must never break the session.
#
# #4314: a hook that injects nothing must leave EVIDENCE, never silence — the
# inert branch records the SAME `capture-errors/cursor-install.json` breadcrumb
# the other seams write, so `tortoise session verify` reports INERT instead of
# reading absent output as health.
#
# ── WHY THE REFLEX IS RESOLVED WITH A FALLBACK CHAIN (#8106) ───────────────
# A `tortoise` shim on PATH that exists but cannot import the package (a stale
# homebrew/pipx/npx leftover) makes PATH-first resolution a SILENT dead end:
# the hook runs a broken binary, gets rc≠0, and exits 0 having injected
# nothing. `volunteer-turn.sh` currently has exactly that defect (#8106, open,
# measured 2026-10-11). This hook therefore walks the SAME candidate order but
# treats an rc≠0 answer as "this candidate cannot answer" and tries the NEXT
# one — PATH → the source checkout's venv → the venv that OWNS the module dir
# (a wheel install, #2385) → the module under a resolved interpreter. Only
# when EVERY candidate refuses does the inert branch record evidence.
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

# ── Drain stdin ───────────────────────────────────────────────────────────
# Cursor hands the sessionStart payload on stdin:
#   {"session_id": "...", "is_background_agent": bool, "composer_mode": "..."}
# The digest is the same for every session, so no field is read — but the
# pipe is drained so a slow reader can never block Cursor's writer.  Guarded
# on stdin NOT being a terminal: the same script run by hand (the documented
# install check) would otherwise block forever on `cat`.
if [ ! -t 0 ]; then
  cat >/dev/null 2>&1 || true
fi

# ── The local install-inert breadcrumb (#4314) ───────────────────────────
# Mirrors `tortoise.__main__._record_capture_error` (same file layout, same
# `TORTOISE_IMPORT_RECEIPT_DIR` override) for the case the Python helper
# cannot cover: no reflex resolved at all. PURE SHELL, no python3 — this is
# also the evidence path reached when no interpreter is available.
# Best-effort: a breadcrumb write can never break the exit-0 contract.
_record_breadcrumb() {
  local detail="$1"
  local receipt_dir crumb_dir stamp
  # #4848: the refusal splices a FILESYSTEM PATH into `detail`, so an unescaped
  # backslash or quote would make the record unparseable — and an unparseable
  # breadcrumb reads as NO breadcrumb, i.e. as a healthy install. One escaper,
  # shared by every seam (it lives in the guard block above).
  detail="$(_tortoise_json_escape "$detail")"
  receipt_dir="${TORTOISE_IMPORT_RECEIPT_DIR:-${HOME:-/nonexistent}/.tortoise/import-receipts}"
  # Normalize to pathlib's `.parent` semantics (#4373): a trailing slash or a
  # trailing `/.` must not make this writer and the reader disagree about the
  # tree, which would leave the breadcrumb invisible and an INERT install
  # reading as health.
  while [ "${receipt_dir%/}" != "$receipt_dir" ] && [ "$receipt_dir" != "/" ]; do
    receipt_dir="${receipt_dir%/}"
  done
  case "$receipt_dir" in
    */. ) receipt_dir="${receipt_dir%/.}" ;;
  esac
  case "$receipt_dir" in
    */*) crumb_dir="${receipt_dir%/*}/capture-errors" ;;
    *) crumb_dir="capture-errors" ;;
  esac
  stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"
  mkdir -p "$crumb_dir" 2>/dev/null || true
  # #5919: redirect the WHOLE write block — bash opens redirections left to
  # right, so an unwritable target must not leak the shell's own error onto
  # this hook's stderr.
  {
    printf '{\n  "harness": "cursor",\n  "detail": "%s",\n  "recorded_at": "%s",\n  "kind": "install-inert"\n}\n' \
      "$detail" "$stamp" \
      > "$crumb_dir/cursor-install.json"
  } 2>/dev/null || true
}

# ── Resolve the reflex entry (PATH → module venv → owning venv → module) ──
# A candidate module dir is accepted ONLY when it actually holds a `tortoise/`
# package. Candidate order: $TORTOISE_SRC_DIR, the installer's recorded module
# dir, then `../..` — the LAST resort, because from an installed hook that is
# `$HOME`, which is not a checkout (#4314). A candidate that HOLDS the package
# but cannot be RUN is skipped too, so the loop can still reach a later, usable
# one (#4848) — and the first such candidate is remembered so the refusal can
# NAME it.
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

# The venv that OWNS the module dir (the WHEEL case, #2385): a wheel install
# puts this hook in `site-packages/tortoise/cursor-hooks/`, so there is no
# `.venv` beneath the module dir and the source-checkout probe can never
# match. Walk UP to the `pyvenv.cfg` marker (bounded) instead of assuming a
# fixed depth — POSIX and Windows layouts differ.
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

# The interpreter the module fallback would run under. Each venv candidate is
# guarded on its OWN base being non-empty: an unguarded "${X:-}/bin/python"
# collapses to `/bin/python`, which would shadow the real `python3` on a host
# where that path exists.
#
# #4848: every candidate is PROBED before it is used, so an interpreter too old
# for the package is never chosen — that is exactly how a missing venv used to
# degrade to a stock-macOS 3.9 and die inside the CLI. `PYTHON_BIN` from the
# ENVIRONMENT is an explicit operator override and is honoured as-is.
# #4848 review: the precedence is `_tortoise_dir_python` — the SAME one
# `_tortoise_dir_runnable` accepted the candidate on — so a dir the guard just
# blessed can never be left without the interpreter that blessed it (a wheel
# install whose venv is found only by the `pyvenv.cfg` walk-up was accepted and
# then reported as un-runnable).
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(_tortoise_dir_python "${TORTOISE_MODULE:-}" || true)"
fi
# The owning venv resolved from a console script is an EQUIVALENT route to the
# same tree (#2385) and stays as its own leg for the case where only the binary
# was found.
if [ -z "$PYTHON_BIN" ] && [ -n "$TORTOISE_VENV" ] \
   && _tortoise_py_qualifies "$TORTOISE_VENV/bin/python"; then
  PYTHON_BIN="$TORTOISE_VENV/bin/python"
fi

PATH_TORTOISE="$(command -v tortoise || true)"

# ── Run the reflex, FIRST SUCCESSFUL CANDIDATE WINS (#8106) ───────────────
# rc==0 means the reflex ANSWERED (an empty digest is still an answer: the
# graph has nothing to inject) — trust it and stop. rc!=0 means THIS candidate
# cannot answer, so try the next one rather than reporting silence as health.
DIGEST=""
_ANSWERED=""
_run_reflex() {
  # $@ = the reflex command. Echoes the digest and returns 0 when it answered.
  local out
  if out="$("$@" 2>/dev/null)"; then
    printf '%s' "$out"
    return 0
  fi
  return 1
}

if [ -n "$PATH_TORTOISE" ]; then
  if DIGEST="$(_run_reflex "$PATH_TORTOISE" context)"; then
    _ANSWERED="PATH"
  fi
fi
if [ -z "$_ANSWERED" ] && [ -n "$TORTOISE_MODULE" ] \
   && [ -x "$TORTOISE_MODULE/.venv/bin/tortoise" ]; then
  if DIGEST="$(_run_reflex "$TORTOISE_MODULE/.venv/bin/tortoise" context)"; then
    _ANSWERED="checkout-venv"
  fi
fi
if [ -z "$_ANSWERED" ] && [ -n "$TORTOISE_VENV" ] \
   && [ -x "$TORTOISE_VENV/bin/tortoise" ]; then
  if DIGEST="$(_run_reflex "$TORTOISE_VENV/bin/tortoise" context)"; then
    _ANSWERED="owning-venv"
  fi
fi
if [ -z "$_ANSWERED" ] && [ -n "${VIRTUAL_ENV:-}" ] \
   && [ -x "$VIRTUAL_ENV/bin/tortoise" ]; then
  if DIGEST="$(_run_reflex "$VIRTUAL_ENV/bin/tortoise" context)"; then
    _ANSWERED="active-venv"
  fi
fi
if [ -z "$_ANSWERED" ] && [ -n "$PYTHON_BIN" ] && [ -n "$TORTOISE_MODULE" ]; then
  # Module fallback: the resolved checkout travels via ARGV and is prepended
  # INSIDE the `-c` source — never via `-m` (CPython prepends the process CWD
  # ahead of PYTHONPATH for `-m`, so a planted `tortoise/` package in the
  # agent's workspace would execute as the user, CWE-427) and never
  # string-interpolated into the source (a quote in the path must not inject
  # code).
  if DIGEST="$(_run_reflex "$PYTHON_BIN" -c 'import sys; sys.path[:] = [p for p in sys.path if p not in ("", ".")]; sys.path.insert(0, sys.argv[1]); from tortoise.__main__ import main; raise SystemExit(main(["context"]))' "$TORTOISE_MODULE")"; then
    _ANSWERED="module"
  fi
fi

if [ -z "$_ANSWERED" ]; then
  # Every candidate refused. An inert install must leave EVIDENCE (#4314).
  # #4848: when a module dir WAS found and could not be run, that is the cause
  # and the refusal names it — an "under no interpreter" line would hide the
  # wrong-interpreter fact the user actually needs.
  if [ -n "$TORTOISE_UNUSABLE_DIR" ] && [ -z "$PYTHON_BIN" ]; then
    _record_breadcrumb "no tortoise reflex could answer Cursor's sessionStart digest: the hook resolved a tortoise source dir at $TORTOISE_UNUSABLE_DIR but $(_tortoise_unusable_reason "$TORTOISE_UNUSABLE_DIR"). Remedy: create that checkout's venv (uv sync --extra embeddings --extra parity in it) or set TORTOISE_SRC_DIR to a checkout with a working .venv; Cursor injected nothing"
    exit 0
  fi
  _record_breadcrumb "no tortoise reflex could answer Cursor's sessionStart digest (tried the PATH binary, the source checkout venv, the owning venv, the active venv and the module under ${PYTHON_BIN:-no interpreter}); Cursor injected nothing"
  exit 0
fi

# Nothing to inject → emit NOTHING (Cursor injects nothing; exit 0).
if [ -z "$(printf '%s' "$DIGEST" | tr -d '[:space:]')" ]; then
  exit 0
fi

# ── Emit Cursor's sessionStart output contract ───────────────────────────
# `additional_context` is passed through the ENVIRONMENT (safe for quotes,
# backslashes and newlines — the digest carries all three), then escaped by
# a JSON encoder. python3 is a hard requirement once we emit: without it the
# digest cannot be escaped into valid JSON, so the hook leaves EVIDENCE and
# exits 0 rather than emitting a possibly-malformed document.
PY3="${PYTHON_BIN:-$(command -v python3 || true)}"
if [ -z "$PY3" ]; then
  _record_breadcrumb "the Cursor sessionStart hook produced a digest (${#DIGEST} chars) but found no python3 to encode Cursor's JSON output contract; Cursor injected nothing"
  exit 0
fi
export TORTOISE_SESSION_CONTEXT="$DIGEST"
# CWE-427: `-c` puts the process cwd at sys.path[0], so a planted ./json.py in
# the session workspace would execute at every session start. This source is a
# DOUBLE-quoted shell string, so the predicate avoids quotes and the comment
# carries NO backticks (the shell would command-substitute them).
if ! "$PY3" -c "
import sys
sys.path[:] = [p for p in sys.path if p and p != '.']
import json, os
print(json.dumps({'additional_context': os.environ.get('TORTOISE_SESSION_CONTEXT', '')}))
" 2>/dev/null; then
  # The encoder is a hard requirement once we emit (#4314): a python3 that
  # EXISTS but cannot run this source (an Xcode-CLT-less /usr/bin/python3 is
  # the stock macOS case) leaves stdout empty, so record the same kind of
  # breadcrumb the missing-`PY3` branch above does instead of reporting
  # silence as health.
  _record_breadcrumb "the Cursor sessionStart hook produced a digest (${#DIGEST} chars) but its python3 encoder (${PY3}) failed to emit Cursor's JSON output contract; Cursor injected nothing"
fi
exit 0
