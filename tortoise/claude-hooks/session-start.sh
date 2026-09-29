#!/usr/bin/env bash
# tortoise-hook-version: 7
# Tortoise memory injection for Claude Code — SessionStart hook.
#
# The `tortoise-hook-version` marker above is the install-contract generation
# for this hook (see tortoise/hook_install.py). Bump it on ANY behavioural
# edit — `tortoise hooks status` and `tortoise doctor` read it to tell an
# already-installed copy it is stale.
#
# Install (once, per project):
#   mkdir -p .claude/hooks
#   cp tortoise/claude-hooks/session-start.sh .claude/hooks/session-start.sh
#   chmod +x .claude/hooks/session-start.sh
#   # then add to .claude/settings.json:
#   # #3754: an explicit timeout is set too — Claude Code's command-hook default on
#   # SessionStart is 600s, so a hung `tortoise context` would stall the session
#   # for 10 minutes; 60s bounds it with ~6× headroom over the measured path.
#   #   { "hooks": { "SessionStart": [{ "matcher": "", "hooks": [{ "type": "command", "command": ".claude/hooks/session-start.sh", "timeout": 60 }] }] } }
#
# The hook prints a Tortoise memory digest to stdout, which Claude Code
# injects into the session context automatically. If Tortoise isn't
# reachable (offline, not installed), it exits 0 silently so the session
# starts normally.

set -euo pipefail

# Claude Code passes the hook payload as JSON on stdin: `{session_id, transcript_path,
# source, ...}`. We only need `session_id` — see the drain below. Read it only when
# stdin is NOT a terminal, so running this script by hand cannot hang on `cat`.
STDIN_JSON=""
if [ ! -t 0 ]; then
  STDIN_JSON="$(cat 2>/dev/null || true)"
fi
HOOK_PY="$(command -v python3 || true)"
LIVE_SESSION_ID=""
if [ -n "$STDIN_JSON" ] && [ -n "$HOOK_PY" ]; then
  LIVE_SESSION_ID="$(printf '%s' "$STDIN_JSON" | "$HOOK_PY" -c '
import sys
# CWE-427: `python3 -c` puts the process cwd at sys.path[0], so a planted
# ./json.py in the session workspace would execute here at every SessionStart.
# `sys` is a builtin and cannot be shadowed, so importing it first is safe.
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
import json
try:
    print(json.load(sys.stdin).get("session_id") or "")
except Exception:
    print("")
' 2>/dev/null || true)"
fi

# ── The ONE HOME-scoped local-state derivation (#3797) ───────────────────
# The two HOME-scoped writers in THIS script — the capture-error breadcrumb
# and the hook-run observation — resolve their directory here, so they cannot
# disagree about where the state tree is.  FIVE sibling hooks still carry their
# own copies of this surgery and must be kept in step BY HAND: `session-end.sh`
# and `session-turn.sh` in this directory, `volunteer-turn.sh`,
# `codex-hooks/session-end.sh` and `cursor-hooks/session-end.sh`.  This helper
# is the only one of the six that also drops a trailing `/.` — the copies do
# not — so a `TORTOISE_IMPORT_RECEIPT_DIR` ending in `/.` still splits them.
# That residual is the #4373 duplication, tracked in #5503 (with the measured
# per-copy matrix, the receipt writer/reader empty-override split, and the
# fallible-derivation traceback); a sourced shared snippet would close it, and
# until then the copies are what the comment above must not overstate.
# The subtle half is the base: `$TORTOISE_IMPORT_RECEIPT_DIR` names the
# RECEIPT dir, so the base is its `.parent`, and that must match pathlib's
# `Path(x).parent` — a TRAILING SLASH is dropped first (a bare `${x%/*}`
# leaves `…/import-receipts/` → `…/import-receipts`, the #4373 false-PROVEN),
# a trailing `/.` is dropped too (pathlib reads `Path('/a/b/.')` as `/a/b`,
# whose parent is `/a`, while `${x%/*}` would say `/a/b` — reader and writer
# would then look in two different trees for the SAME run), and a slash-less
# relative value has no parent at all.
_tortoise_state_dir() {
  local leaf="$1" receipt_dir
  receipt_dir="${TORTOISE_IMPORT_RECEIPT_DIR:-${HOME:-/nonexistent}/.tortoise/import-receipts}"
  while :; do
    case "$receipt_dir" in
      "/") break ;;
      */) receipt_dir="${receipt_dir%/}" ;;
      */.) receipt_dir="${receipt_dir%/.}" ;;
      *) break ;;
    esac
    if [ -z "$receipt_dir" ]; then receipt_dir="/"; fi
  done
  case "$receipt_dir" in
    */*) printf '%s' "${receipt_dir%/*}/$leaf" ;;
    *) printf '%s' "$leaf" ;;
  esac
}

# ── The local capture-error breadcrumb ───────────────────────────────────
# Mirrors `tortoise.__main__._record_capture_error` (same file layout, same
# `TORTOISE_IMPORT_RECEIPT_DIR` override) for the one case that helper cannot
# cover: the module dir did not resolve, so the Python helper is unreachable.
# A hook that injects nothing must leave EVIDENCE, never silence (#4314).
# Best-effort: a breadcrumb write can never break the exit-0 contract.
_record_breadcrumb() {
  # PURE SHELL, no python3: this is also the evidence path for the "resolved a
  # module dir but found no interpreter" branch, which is reached BECAUSE
  # python3 is missing — a python3-written breadcrumb could never run there.
  # The ``install-inert`` kind marks this as the INSTALL leg's own evidence and
  # keeps it distinguishable from a ``sessions import`` capture failure, which
  # writes the same file with ``kind: capture-failure`` (#4314). Best-effort:
  # a breadcrumb write can never break the exit-0 contract.
  local harness="$1" detail="$2"
  # The directory derivation lives in `_tortoise_state_dir` — ONE derivation
  # for the two HOME-scoped writers in this script (#3797), including the
  # trailing-slash, trailing-`/.` and slash-less cases.
  local crumb_dir stamp
  crumb_dir="$(_tortoise_state_dir capture-errors)"
  stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"
  mkdir -p "$crumb_dir" 2>/dev/null || true
  printf '{\n  "harness": "%s",\n  "detail": "%s",\n  "recorded_at": "%s",\n  "kind": "install-inert"\n}\n' \
    "$harness" "$detail" "$stamp" \
    > "$crumb_dir/$harness.json" 2>/dev/null || true
}

# ── The hook-run observation (#3797) ─────────────────────────────────────
# An installed-but-UNCONFIGURED hook used to leave nothing anywhere: the
# credential gate refuses `session probe` before any request is dispatched,
# the server route (`POST /v1/sessions/install-probe`) is auth-gated, and
# this script DISCARDED the probe's exit status — so "installed and ran" was
# indistinguishable from "not installed", on every surface.
#
# This is the hook's OWN observation that it ran, and the hook is the only
# faithful witness (`tortoise session probe` is also invoked by hand and by
# other harnesses).  It carries no credential and no content — harness,
# timestamp, and the probe's outcome — and it is read by the
# credential-free `tortoise hooks status`.  PURE SHELL, no python3: one call
# site is the branch reached BECAUSE the interpreter is missing.  Best-effort:
# a failed write can never break the exit-0 contract.
#
# `$2` is the probe outcome (did the SERVER accept it) and `$3` is the
# probe's exit status, printed VERBATIM — so a branch that never probed
# passes the bare JSON token `null`, never an interpolated empty string
# (`"probe_rc": ""` is invalid JSON and would read back as "no run").
_record_hook_run() {
  local harness="$1" recorded="$2" rc="$3" run_dir stamp
  run_dir="$(_tortoise_state_dir hook-runs)"
  stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"
  mkdir -p "$run_dir" 2>/dev/null || true
  printf '{\n  "harness": "%s",\n  "kind": "hook-run",\n  "recorded_at": "%s",\n  "probe_recorded": %s,\n  "probe_rc": %s\n}\n' \
    "$harness" "$stamp" "$recorded" "$rc" \
    > "$run_dir/$harness.json" 2>/dev/null || true
}

# Prefer a local install; fall back to the installer's recorded checkout.
TORTOISE_BIN="$(command -v tortoise || true)"
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
if [ -z "$TORTOISE_BIN" ] && [ -z "$TORTOISE_MODULE" ]; then
  _record_breadcrumb claude \
    "the installed Claude session-start hook could not resolve a tortoise module dir (checked TORTOISE_SRC_DIR, \$HOME/.tortoise/hook-src-dir, and ../..), found no tortoise binary, and injected nothing"
  # #3797: the hook RAN — record that too, so the install is not reported as
  # never-ran.  No probe was attempted, hence the bare `null`.
  _record_hook_run claude false null
  exit 0
fi
if [ -z "$TORTOISE_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || true)"
  if [ -z "$PYTHON_BIN" ]; then
    _record_breadcrumb claude \
      "the installed Claude session-start hook resolved a tortoise module dir but found no python3 interpreter, and injected nothing"
    # #3797: same as the other inert branch — the hook ran, the probe did not.
    _record_hook_run claude false null
    exit 0
  fi
  # #3755: the digest is BEST-EFFORT — it must never short-circuit this
  # script, because the install-probe beacon below is independent of it.
  # `|| exit 0` here (before #3755) skipped the probe whenever the embedded
  # store was busy, silently dropping install telemetry. The resolved module
  # dir travels via ARGV and is prepended INSIDE ``-c`` AFTER the process cwd
  # is dropped from sys.path — never via ``-m`` (CPython prepends the process
  # CWD ahead of PYTHONPATH for ``-m``, so a planted ``tortoise/`` package in
  # the workspace would execute as the user, CWE-427) and never
  # string-interpolated into the source.
  "$PYTHON_BIN" -c '
import sys
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
sys.path.insert(0, sys.argv[1])
from tortoise.__main__ import main
raise SystemExit(main(["context"]))
' "$TORTOISE_MODULE" 2>/dev/null || true
else
  # #3755: same as above — a failed digest (busy/unreachable store) is
  # best-effort and must fall through to the probe, not exit the script.
  tortoise context 2>/dev/null || true
fi

# #1727 Slice 2 (Task 14, T2-P1): install-probe beacon.
#
# The dashboard cannot stat the user's filesystem — "is the capture hook
# installed?" is answered by a probe the installed artifact itself fires:
# POST /v1/sessions/install-probe with the harness name (harness + timestamp
# ONLY — zero conversation content), recording install_probe_claude on the
# team's onboarding state. The server is reached via the .tortoise config's
# TORTOISE_API_URL (self-hosted routing pin — never a hardcoded hosted host).
# Best-effort: no config / unreachable API → exit 0 silently (the session
# start digest must never be blocked by the probe). The probe is NOT
# consent-gated — it's install telemetry; the dashboard reads it for the
# off → install-pending → waiting → active 4-state before/independent of
# consent.
# #3797: the outcome of this attempt is RECORDED, not discarded.  These two
# are initialised HERE — immediately before the branch — so `set -u` cannot
# bite and a branch that probes nothing can never read as accepted; a probe
# that never ran leaves `probe_rc` as the bare JSON token `null`.
PROBE_RECORDED=false
PROBE_RC=null
TORTOISE_BIN="$(command -v tortoise || true)"
if [ -n "$TORTOISE_BIN" ]; then
  PROBE_RC=0
  if "$TORTOISE_BIN" session probe --harness claude >/dev/null 2>&1; then
    PROBE_RECORDED=true
  else
    PROBE_RC=$?
  fi
else
  PYTHON_BIN="$(command -v python3 || true)"
  if [ -n "$PYTHON_BIN" ] && [ -d "$TORTOISE_MODULE/tortoise" ]; then
    PROBE_RC=0
    if "$PYTHON_BIN" -c '
import sys
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
sys.path.insert(0, sys.argv[1])
from tortoise.__main__ import main
raise SystemExit(main(["session", "probe", "--harness", "claude"]))
' "$TORTOISE_MODULE" >/dev/null 2>&1; then
      PROBE_RECORDED=true
    else
      PROBE_RC=$?
    fi
  fi
fi
# The hook's own observation that it ran, with the probe's outcome (#3797).
# Best-effort: this can never change the exit-0 contract below.
_record_hook_run claude "$PROBE_RECORDED" "$PROBE_RC"

# #3963: the REPLAY OPPORTUNITY. An interrupted or laptop-closed session left
# its turns in the local capture spool (~/.tortoise/capture-spool) — the
# SessionEnd hook is not guaranteed to have run (Claude Code cancels it at its
# ~1.5s default — #3754 — and a killed process fires no hook at all). The
# per-turn session-turn.sh hook is what put them there; this files them now, at
# session start, before the new session produces a turn. BEST-EFFORT + BACKGROUNDED: a large replay must never spend the
# SessionStart budget on network retries, and it must never block the session.
#
# `--exclude-session-id "$LIVE_SESSION_ID"`: the drain must never file the
# session that is LIVE right now. `--resume` / `/clear` / a compaction reuses
# the session id, so a mid-conversation capture of it is already on the spool;
# filing it makes the server REPLAY it (extraction is skipped once a session
# exists), which stores the session while permanently losing its extraction.
# The drain files OTHER sessions only — the final flush (SessionEnd) files the
# live one.
EXCLUDE_ARGS=()
if [ -n "$LIVE_SESSION_ID" ]; then
  EXCLUDE_ARGS=(--exclude-session-id "$LIVE_SESSION_ID")
fi
if [ -n "$TORTOISE_BIN" ]; then
  nohup "$TORTOISE_BIN" session drain ${EXCLUDE_ARGS[@]+"${EXCLUDE_ARGS[@]}"} >/dev/null 2>&1 &
elif [ -n "${PYTHON_BIN:-}" ] && [ -d "${TORTOISE_MODULE:-}/tortoise" ]; then
  # CWE-427: the `-c` source is SINGLE-quoted and the module dir travels as an
  # argv ELEMENT — never string-interpolated (a quote in the path must not
  # inject code). `-c` puts the process cwd at sys.path[0], so the cwd is
  # dropped before any non-builtin import: a planted ./json.py in the agent's
  # workspace would otherwise execute as the user. `sys` is a builtin and
  # cannot be shadowed, so importing it first is safe.
  if [ ${#EXCLUDE_ARGS[@]} -gt 0 ]; then
    nohup "$PYTHON_BIN" -c '
import sys
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
sys.path.insert(0, sys.argv[1])
from tortoise.__main__ import main
raise SystemExit(main(["session", "drain", "--exclude-session-id", sys.argv[2]]))
' "$TORTOISE_MODULE" "$LIVE_SESSION_ID" >/dev/null 2>&1 &
  else
    nohup "$PYTHON_BIN" -c '
import sys
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
sys.path.insert(0, sys.argv[1])
from tortoise.__main__ import main
raise SystemExit(main(["session", "drain"]))
' "$TORTOISE_MODULE" >/dev/null 2>&1 &
  fi
fi
