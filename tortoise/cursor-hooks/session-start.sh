#!/usr/bin/env bash
# tortoise-hook-version: 4
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
# `$HOME`, which is not a checkout (#4314).
TORTOISE_MODULE=""
for CANDIDATE in "${TORTOISE_SRC_DIR:-}" \
                 "$(cat "${HOME:-/nonexistent}/.tortoise/hook-src-dir" 2>/dev/null || true)" \
                 "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd || true)"; do
  if [ -n "$CANDIDATE" ] && [ -d "$CANDIDATE/tortoise" ]; then
    TORTOISE_MODULE="$CANDIDATE"
    break
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
PYTHON_BIN=""
if [ -n "$TORTOISE_MODULE" ] && [ -x "$TORTOISE_MODULE/.venv/bin/python" ]; then
  PYTHON_BIN="$TORTOISE_MODULE/.venv/bin/python"
elif [ -n "$TORTOISE_VENV" ] && [ -x "$TORTOISE_VENV/bin/python" ]; then
  PYTHON_BIN="$TORTOISE_VENV/bin/python"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
  PYTHON_BIN="$VIRTUAL_ENV/bin/python"
else
  PYTHON_BIN="$(command -v python3 || true)"
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
