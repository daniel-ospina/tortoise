#!/usr/bin/env bash
# tortoise-hook-version: 12
# Tortoise session capture for Claude Code — SessionEnd hook (#564).
#
# #3615 (generation bump 3→4): the capture step now requires the explicit
# TORTOISE_CAPTURE=1 consent gate below. A generation-3 installed copy is a
# DIFFERENT script under the same number, so it must read as stale —
# `tortoise hooks status` / `tortoise doctor` then direct the user to re-copy
# (`.claude/hooks/*.sh` are per-project copies that never self-update).
#
# The `tortoise-hook-version` marker above is the install-contract generation
# for this hook (see tortoise/hook_install.py). Bump it on ANY behavioural
# edit — `tortoise hooks status` and `tortoise doctor` read it to tell an
# already-installed copy it is stale.
#
# Fires when a Claude Code session ends: converts the session transcript
# (Claude Code's .jsonl) into Tortoise's text-turn format (User:/Assistant:)
# and files it via `tortoise session capture` (hosted /v1/sessions).
#
# #3963: this hook is NO LONGER the mechanism of record — it is the final
# flush. The MECHANISM is session-turn.sh (UserPromptSubmit), which copies the
# conversation into the durable local spool (~/.tortoise/capture-spool) at
# every user prompt with NO network call. This hook then does the costly half:
# `tortoise session capture` spools again (no-op when unchanged) and files.
# A CANCELED SessionEnd (Claude Code's ~1.5s default, #3754) or a killed
# process therefore loses at most the in-flight turn, and the SessionStart
# hook's `tortoise session drain` files whatever is still pending. The explicit
# "timeout": 60 below is still load-bearing for the final flush — it is just no
# longer the only chance to capture.
# This is the exit-side counterpart to session-start.sh's memory injection —
# together they close the loop: memory in at session start, session filed at
# session end.
#
# Install (once, per project):
#   mkdir -p .claude/hooks
#   cp tortoise/claude-hooks/session-end.sh .claude/hooks/session-end.sh
#   chmod +x .claude/hooks/session-end.sh
#   # then add to .claude/settings.json:
#   # #3754: the explicit timeout is load-bearing — Claude Code cancels SessionEnd
#   # at its 1.5s default; the budget rises to the highest per-hook timeout (60 is
#   # the documented ceiling). This hook measured 9.26s on a real hosted run.
#   #   { "hooks": { "SessionEnd": [{ "matcher": "", "hooks": [{ "type": "command",
#   #       "command": ".claude/hooks/session-end.sh", "timeout": 60 }] }] } }
#
# CAPTURE REQUIRES EXPLICIT CONSENT (#3615): TORTOISE_CAPTURE=1 (truthy:
# 1/true/yes/on). It is INDEPENDENT of the credential — TORTOISE_API_KEY +
# TORTOISE_API_URL are the hosted credential/endpoint used to reach the API,
# and exporting the key for the MCP `Authorization: Bearer` header must NEVER
# opt the machine into shipping transcripts. With the opt-in absent the capture
# step is skipped (a notice on stderr, repeated on each session close while a
# legacy credential is present; only the durable file is one-time) and the hook
# still exits 0; the LOCAL reindex sweep below still runs — it is NOT guaranteed
# machine-local (it can post conversation-derived text to the configured metadata
# provider), but the consent gate must not disable local memory.
# For a LOCAL-only graph, replace the capture step with:
#   tortoise index --dir ~/.tortoise/docs/conversations/.
#
# The hook ALWAYS exits 0 — Claude Code must never be blocked by memory
# capture failing (offline, uninstalled, no transcript).

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
# A hook that captures nothing must leave EVIDENCE, never silence (#4314).
# Best-effort: a breadcrumb write can never break the exit-0 contract.
_record_breadcrumb() {
  # PURE SHELL, no python3: this is also the evidence path for the "resolved a
  # module dir but found no interpreter" branch, which is reached BECAUSE
  # python3 is missing — a python3-written breadcrumb could never run there.
  # The ``install-inert`` kind marks this as the INSTALL leg's own evidence and
  # keeps it distinguishable from a ``sessions import`` capture failure, which
  # writes ``kind: capture-failure`` (#4314). #5838: the two kinds occupy
  # SEPARATE slots, so this writer never touches the ``capture-failure`` file
  # and cannot destroy a live quota/network refusal. Best-effort: a breadcrumb
  # write can never break the exit-0 contract.
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

# Claude Code passes SessionEnd hook metadata as JSON on stdin:
# {"session_id": "...", "transcript_path": "...", "cwd": "..."}
# #1727 (Task 14, T1-P11): the REAL session_id is forwarded as the capture
# idempotency key (re-POSTs of the same session_id converge to one Session);
# harness='claude' is passed for per-harness receipts.
META="$(python3 -c 'import sys
# CWE-427: drop the process cwd from sys.path BEFORE importing anything
# non-builtin. ``python -c`` puts cwd at sys.path[0], so a planted ./json.py
# in the session workspace would otherwise execute at every SessionEnd. `sys`
# is a builtin module and cannot be shadowed, so importing it first is safe.
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
# NO `[ -f "$TRANSCRIPT_PATH" ]` pre-check (#3963, mirrors session-turn.sh): `-f`
# is FALSE for an unreadable file or a non-traversable parent, so an unreadable
# transcript was skipped SILENTLY — no capture, no ledger line. The conversion
# classifies it and the ledger records `transcript_unreadable`.

# Convert the Claude Code .jsonl transcript into text turns (User:/Assistant:).
TMP="$(mktemp -t tortoise_session_end.XXXXXX)"
trap 'rm -f "$TMP"' EXIT
CONVERT_RC=0
python3 - "$TRANSCRIPT_PATH" "$TMP" << 'PYEOF' || CONVERT_RC=$?
import sys
# CWE-427: `python3 -` sets sys.path[0] = '' (the cwd), so a planted ./json.py
# in the session workspace would execute here at every SessionEnd. `sys` is a
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
  # Record the loss in the SAME ledger `tortoise session capture` writes to
  # (schema mirrors capture_spool.record_discard). Best-effort: still exit 0.
  python3 - "$SESSION_ID" "$TRANSCRIPT_PATH" << 'PYEOF' 2>/dev/null || true
import sys
# CWE-427: `python3 -` sets sys.path[0] = '' (the cwd), so a planted ./json.py
# in the session workspace would execute here — on every SessionEnd. `sys` is a
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
            "detail": f"{path}: the SessionEnd hook could not read the transcript",
        }) + "\n")
except OSError:
    pass
PYEOF
  exit 0
fi

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
    # #3684: `-f` FOLLOWS a symlink and `>` follows it too, so a DANGLING link at
    # the marker made `-f` false, the guard pass, and the redirect then CREATE the
    # attacker's target at the umask (reproduced: 0644). `-L` refuses a link
    # outright whether or not it dangles; `set -C` (noclobber) makes the create
    # fail rather than truncate if a regular file appeared in between; `umask 077`
    # pins 0600. `capture_consent.py` writes this same path under O_NOFOLLOW, so
    # the two writers now agree on both the refusal and the mode.
    if [ ! -e "$NOTICE_MARKER" ] && [ ! -L "$NOTICE_MARKER" ]; then
      mkdir -p "$(dirname "$NOTICE_MARKER")" 2>/dev/null || true
      # `2>/dev/null` MUST precede `> "$NOTICE_MARKER"`: a failed redirect
      # setup is reported to the shell's CURRENT stderr, so with the stdout
      # redirect first a non-writable ~/.tortoise leaks a raw bash error line
      # on every session close. Ordering stderr first suppresses the setup
      # failure too (`|| true` only rescues the exit status).
      (umask 077; set -C; printf '%s\n' \
        "Tortoise: session capture is OFF — it now requires explicit consent. Re-enable with TORTOISE_CAPTURE=1 (docs/quickstart-cloud.md)." \
        2>/dev/null > "$NOTICE_MARKER") || true
    fi
    printf '%s\n' \
      "tortoise: session capture is OFF — it now requires explicit consent. Re-enable with TORTOISE_CAPTURE=1 (docs/quickstart-cloud.md)." >&2 || true
  fi
fi

# Prefer a local install; fall back to the installer's recorded checkout.
# #4848: a `tortoise` on PATH is only as usable as the interpreter its console
# script names on `#!` — a stale venv's script names one that may not exist, or
# one too old to import the package. It is PROBED here, not assumed.
TORTOISE_BIN="$(command -v tortoise || true)"
TORTOISE_UNUSABLE_BIN=""
if [ -n "$TORTOISE_BIN" ] && ! _tortoise_bin_qualifies "$TORTOISE_BIN"; then
  TORTOISE_UNUSABLE_BIN="$TORTOISE_BIN"
  TORTOISE_BIN=""
fi
# A candidate module dir is accepted ONLY when it actually holds a
# `tortoise/` package. Candidate order: $TORTOISE_SRC_DIR, the installer's
# recorded module dir, then `../..` — the LAST resort, because from an
# installed hook that is `$HOME`, which is not a checkout (#4314). A candidate
# that HOLDS the package but cannot be RUN is skipped too, so the loop can
# still reach a later, usable one (#4848) — and the first such candidate is
# remembered so the refusal can NAME it.
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
if [ -z "$TORTOISE_BIN" ] && [ -z "$TORTOISE_MODULE" ]; then
  # No binary and no module dir: record the breadcrumb (the SAME shape and
  # location `session capture` writes) and exit 0 — an inert install must
  # leave evidence instead of silence (#4314).
  #
  # #4848: a module dir (or a `tortoise` binary) that WAS found but cannot be
  # run is a DIFFERENT cause with a different remedy — say which, and name the
  # interpreter, so the report stops pointing at `datetime`.
  if [ -n "$TORTOISE_UNUSABLE_DIR" ]; then
    _record_breadcrumb claude \
      "the installed Claude hook resolved a tortoise source dir at $TORTOISE_UNUSABLE_DIR but $(_tortoise_unusable_reason "$TORTOISE_UNUSABLE_DIR") and captured nothing. Remedy: create that checkout's venv (uv sync --extra embeddings --extra parity in it) or set TORTOISE_SRC_DIR to a checkout with a working .venv"
    exit 0
  fi
  if [ -n "$TORTOISE_UNUSABLE_BIN" ]; then
    _record_breadcrumb claude \
      "the installed Claude hook found a tortoise binary at $TORTOISE_UNUSABLE_BIN but the interpreter on its #! line cannot run it (tortoise needs Python $_TORTOISE_MIN_PY+), and captured nothing. Remedy: reinstall it (uv tool install --force .) or set TORTOISE_SRC_DIR to a checkout with a working .venv"
    exit 0
  fi
  _record_breadcrumb claude \
    "the installed Claude hook could not resolve a tortoise module dir (checked TORTOISE_SRC_DIR, \$HOME/.tortoise/hook-src-dir, and ../..), found no tortoise binary, and captured nothing"
  exit 0
fi
if [ -z "$TORTOISE_BIN" ]; then
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
    _record_breadcrumb claude \
      "the installed Claude hook resolved a tortoise module dir at $TORTOISE_MODULE but $(_tortoise_unusable_reason "$TORTOISE_MODULE") and captured nothing. Remedy: create that checkout's venv (uv sync --extra embeddings --extra parity in it) or set TORTOISE_SRC_DIR to a checkout with a working .venv"
    exit 0
  fi
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
  SWEEP_CORPUS="$("$PYTHON_BIN" -c '
import sys
# CWE-427: drop the process cwd from sys.path BEFORE importing anything
# non-builtin. ``python -c`` puts cwd at sys.path[0], so a planted ./os.py in
# the agent workspace would otherwise execute. The module dir arrives via
# argv (never the source) and is prepended AFTER the cwd is removed.
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
sys.path.insert(0, sys.argv[1])
from tortoise.session_indexer import session_corpus_dir
print(session_corpus_dir())' "$TORTOISE_MODULE" 2>/dev/null || true)"
  [ -z "$SWEEP_CORPUS" ] && SWEEP_CORPUS="$HOME/.tortoise/docs/conversations"
  TORTOISE_SWEEP_CORPUS="$SWEEP_CORPUS" \
    nohup "$PYTHON_BIN" -c '
import sys
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
sys.path.insert(0, sys.argv[1])
import os
from tortoise.__main__ import main
# TORTOISE_INDEX_CHILD_STDERR debug-redirect is OPT-IN: only when the
# operator set it (never force-write a file at every session close —
# review-gate P2). truncate-on-open + fail-safe inside the CLI.
os.environ.setdefault("TORTOISE_INDEX_CHILD_STDERR", "")
raise SystemExit(main(["index", "directory",
                       os.environ["TORTOISE_SWEEP_CORPUS"], "--metadata"]))' \
    "$TORTOISE_MODULE" >/dev/null 2>&1 &
  # #1727 (Task 14): harness + the real session_id (idempotency key) pass
  # through to the capture payload — via env (the session id and every path
  # are untrusted shell input; never interpolated into the -c string).
  # #3615: only when capture consent is explicit (see the gate above).
  if [ "$CAPTURE_ENABLED" = "1" ]; then
    TORTOISE_HOOK_SESSION_ID="$SESSION_ID" \
    TORTOISE_CAPTURE_FILE="$TMP" \
    TORTOISE_HOOK_TRANSCRIPT_PATH="$TRANSCRIPT_PATH" \
    "$PYTHON_BIN" -c '
import sys
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
sys.path.insert(0, sys.argv[1])
import os
from tortoise.__main__ import main
argv = ["session", "capture", "--file", os.environ["TORTOISE_CAPTURE_FILE"],
        "--harness", "claude"]
# #4197: `$TMP` is deleted by the hook trap at exit, so the saved-backlog
# ledger must record the DURABLE source (the Claude project transcript) or a
# store-only deferral could never be re-processed. Passed via env, never
# interpolated into the -c source (every path here is untrusted shell input).
if os.environ.get("TORTOISE_HOOK_TRANSCRIPT_PATH"):
    argv += ["--transcript-path", os.environ["TORTOISE_HOOK_TRANSCRIPT_PATH"]]
if os.environ.get("TORTOISE_HOOK_SESSION_ID"):
    argv += ["--session-id", os.environ["TORTOISE_HOOK_SESSION_ID"]]
raise SystemExit(main(argv))
' "$TORTOISE_MODULE" 2>/dev/null || exit 0
  fi
else
  # Round-10 P3: sweep first (capture failure must not disable reindexing).
  # The corpus dir is resolved via session_corpus_dir() (honors
  # TORTOISE_SESSION_CORPUS else ~/.tortoise/docs/conversations) using the
  # interpreter that owns the installed tortoise package — a missing
  # resolution silently fell back to the default corpus and ignored a
  # configured TORTOISE_SESSION_CORPUS — the corpus-dir divergence class
  # the plan condemns, review-gate P1).
  # #4848: this resolver IMPORTS the package, so it may only run under an
  # interpreter the guard accepts — the ambient `python3` on a stock macOS box
  # is 3.9 and could never import it, which is the failure this issue is about.
  # With no qualifying interpreter the documented default corpus stands.
  SWEEP_PY="$(command -v python3 || true)"
  if ! _tortoise_py_qualifies "$SWEEP_PY"; then
    SWEEP_PY=""
  fi
  SWEEP_CORPUS=""
  if [ -n "$SWEEP_PY" ]; then
  SWEEP_CORPUS="$("$SWEEP_PY" -c '
import sys
# CWE-427: the corpus resolver imports tortoise by NAME, so the cwd must be off
# sys.path first - a planted ./tortoise/ in the session workspace would
# otherwise be imported and executed here. NOTE: this heredoc-style source is
# SINGLE-quoted on purpose: inside a double-quoted shell string the quotes in
# ("", ".") close the string and the source is mangled into a SyntaxError.
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
from tortoise.session_indexer import session_corpus_dir
print(session_corpus_dir())' 2>/dev/null || true)"
  fi
  [ -z "$SWEEP_CORPUS" ] && SWEEP_CORPUS="$HOME/.tortoise/docs/conversations"
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
    # #4197: `$TMP` is removed by this hook's own `trap` at exit; the durable
    # source the saved-backlog ledger must record is Claude's project transcript.
    [ -n "$TRANSCRIPT_PATH" ] && CAPTURE_ARGS+=(--transcript-path "$TRANSCRIPT_PATH")
    [ -n "$SESSION_ID" ] && CAPTURE_ARGS+=(--session-id "$SESSION_ID")
    # #4848 review: run the RESOLVED binary, never a bare `tortoise` — the
    # resolution can now land on a checkout's own `.venv/bin/tortoise` (a leg the
    # guard accepts), which is NOT on PATH, so a bare name would silently find
    # nothing and the `|| exit 0` would drop the capture with no record.
    "$TORTOISE_BIN" session capture "${CAPTURE_ARGS[@]}" 2>/dev/null || exit 0
  fi
fi
