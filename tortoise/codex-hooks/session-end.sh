#!/usr/bin/env bash
# tortoise-hook-version: 2
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
  # (#4314).
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
    # worker, so the synchronous hook still returns inside Codex's ~1 s
    # SessionEnd budget.
    _record_breadcrumb codex \
      "the installed Codex hook could not resolve a tortoise module dir (checked TORTOISE_SRC_DIR, \$HOME/.tortoise/hook-src-dir, and ../..), found no tortoise binary, and captured nothing"
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
      _record_breadcrumb codex \
        "the installed Codex hook resolved a tortoise module dir but found no python3 interpreter, and captured nothing"
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
