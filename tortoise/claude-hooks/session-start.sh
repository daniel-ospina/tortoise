#!/usr/bin/env bash
# tortoise-hook-version: 9
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
# injects into the session context automatically. It ALSO prints the local
# capture breadcrumb when one is present, so a capture that did not land is
# reported to the agent rather than only to the machine (#4041). If Tortoise
# isn't reachable (offline, not installed), it exits 0 so the session starts
# normally — silent only when there is no breadcrumb to report.

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
  # writes ``kind: capture-failure`` (#4314). #5838: the two kinds now occupy
  # SEPARATE slots, so this writer never touches the ``capture-failure`` file
  # and cannot destroy a live quota/network refusal — the ``-install`` suffix
  # is the slot's own name, and `session verify` reads it back from the same
  # one. Best-effort: a breadcrumb write can never break the exit-0 contract.
  # `$3` is the timestamp, when the caller already computed one for the
  # rendered payload — so the record and what the agent is told cannot disagree
  # by a second (#4041). Absent, it is computed here as before.
  local harness="$1" detail="$2" stamp="${3:-}"
  # The directory derivation lives in `_tortoise_state_dir` — ONE derivation
  # for the two HOME-scoped writers in this script (#3797), including the
  # trailing-slash, trailing-`/.` and slash-less cases.
  local crumb_dir
  crumb_dir="$(_tortoise_state_dir capture-errors)"
  [ -n "$stamp" ] || stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"
  mkdir -p "$crumb_dir" 2>/dev/null || true
  printf '{\n  "harness": "%s",\n  "detail": "%s",\n  "recorded_at": "%s",\n  "kind": "install-inert"\n}\n' \
    "$harness" "$detail" "$stamp" \
    > "$crumb_dir/$harness-install.json" 2>/dev/null || true
}

# ── Reading the breadcrumb BACK to the agent (#4041) ─────────────────────
# The two breadcrumb writers above and in `tortoise.__main__` wrote a file that
# NOTHING read back: the user's agent was never told that memory had stopped
# being filed. The owner ruled the agent SESSION is the primary surface (the
# dashboard was explicitly rejected), so the payload below is printed to
# stdout, which Claude Code injects into the session context.
#
# ONE four-line payload PER RECORD, two renderers, because the two `kind`
# values have different reachability:
#
#   code: install-inert  -> PURE SHELL (`_render_breadcrumb_inert`), which owns
#          BOTH that record's payload and its recovery text. This record is
#          reached BECAUSE the interpreter or the module dir could not be
#          resolved, so a Python-only renderer could never report it.
#   code: capture-failure -> Python (`tortoise.capture_breadcrumb`), which sends
#          the detail through `tortoise.security.redact_secrets` — an error
#          string can carry a token. This record is always written by Python,
#          so the interpreter IS available on this path.
#
# #5838: the two kinds live in SEPARATE slots — `capture-failure` in
# `capture-errors/<harness>.json`, `install-inert` in
# `capture-errors/<harness>-install.json` — so an inert install can no longer
# overwrite a live quota/network refusal. The payload is assembled by THIS
# script and MAY carry BOTH facts: a machine can be over quota AND have a moved
# checkout, which are independent claims, and neither is picked over the other.
# The two STALE-source rules are deliberate: a stale `install-inert` record is
# never rendered on the resolved path (`render_file` refuses that kind), and a
# stale `capture-failure` record is cleared by the writer on a 2xx.
#
# ⛔ The recovery text is factual/available-actions, NEVER imperatives — this is
# VENDOR-MANDATED, not style. Claude Code's hook documentation warns that
# output framed as out-of-band system commands trips Claude's prompt-injection
# defences, which makes Claude surface the text to the user instead of treating
# it as injected context. `Recovery: `tortoise session drain` retries now` is
# right; `Run `tortoise session drain` now` costs the payload its injection.
# Do not "fix" this into imperatives.
#
# The exit-0 contract is inviolable: no breadcrumb file -> no output at all.
# The capture payload is BUFFERED and printed only when the renderer exits 0, so
# a renderer that fails — including one that writes partial output and THEN
# exits non-zero — contributes NOTHING to the session context. `set -euo
# pipefail` is active, so every stage is guarded and each function returns 0.

# The INSTALL-leg renderer. PURE SHELL, no python3 — the branch is reached
# because the interpreter or module dir did not resolve. The `detail` and stamp
# are the exact ones just written by `_record_breadcrumb`, so there is nothing
# to parse back out of the file. Those details are fixed self-authored strings
# with NO user content, so the redaction the Python half performs is not needed
# here — said explicitly so its absence cannot be read as an oversight.
_render_breadcrumb_inert() {
  local harness="$1" detail="$2" stamp="$3" recovery="$4"
  printf 'code:     install-inert\n' || true
  printf 'what:     Tortoise memory for this project has NOT been filed since %s. %s capture is affected.\n' \
    "$stamp" "$harness" || true
  printf 'why:      %s\n' "$detail" || true
  # ⛔ THE RECOVERY IS PER-BRANCH, NOT A CONSTANT (#4041 review round 3, P2). This
  # renderer serves TWO inert branches with DIFFERENT causes, and the hard-coded
  # sentence named the first one in both: branch 2's `why:` says a module dir WAS
  # resolved and only `python3` was missing, while its `next:` said the module dir was
  # NOT resolved and told the reader to run `tortoise hooks upgrade` — a payload that
  # contradicted itself and prescribed the wrong remedy. The caller passes the clause
  # that matches its own cause, so `why:` and `next:` cannot disagree.
  printf 'next:     Recovery: %s\n' "$recovery" || true
  return 0
}

# The CAPTURE-leg renderer. The record's `kind` is decided IN PYTHON by
# `render_file`, which parses the record as JSON and returns nothing for any
# kind but `capture-failure`. A `sed`/`head` gate here would be a second,
# weaker parser: it would silently DISCARD a compact single-line record (the
# writer's `indent=2` is not a contract) — reintroducing the exact "nobody is
# told" defect #4041 exists to fix — and it would disable the whole feature
# wherever `sed`/`head` are absent, even though the interpreter this half exists
# to use IS present. An `install-inert` record is owned by
# `_render_breadcrumb_inert`; a STALE one reaching this path renders nothing
# because Python refuses the kind.
_render_capture_failure_breadcrumb() {
  local harness="$1" crumb py payload
  crumb="$(_tortoise_state_dir capture-errors)/$harness.json"
  [ -f "$crumb" ] || return 0
  py="${PYTHON_BIN:-$(command -v python3 || true)}"
  [ -n "$py" ] || return 0
  # Same CWE-427 posture as every other embedded block here: drop the process
  # cwd, then prepend the resolved module dir from ARGV (never `-m`, never
  # string-interpolated). An EMPTY module dir falls back to the installed
  # package on `sys.path`; if neither is importable the `|| return 0` below
  # makes this a silent no-op, never a broken session start.
  #
  # ATOMIC: capture the payload and print it ONLY on exit 0. A renderer that
  # writes partial output and THEN fails would otherwise inject garbage into
  # the session context at rc 0. `local payload="$(...)"` would MASK that exit
  # status (`local` always returns 0), so the assignment is deliberately a
  # separate command carrying its own guard.
  payload="$("$py" -c '
import sys
sys.path[:] = [p for p in sys.path if p not in ("", ".")]
if sys.argv[1]:
    sys.path.insert(0, sys.argv[1])
from tortoise.capture_breadcrumb import render_file
sys.stdout.write(render_file(sys.argv[2]))
' "${TORTOISE_MODULE:-}" "$crumb" 2>/dev/null)" || return 0
  [ -n "$payload" ] || return 0
  # ⛔ The trailing newline is LOAD-BEARING, not cosmetic. Command substitution
  # strips EVERY trailing newline, so `printf '%s'` would glue the payload's
  # last line (`next: …`) onto the memory digest's first line — `tortoise
  # context` writes to this SAME stdout immediately below — corrupting the
  # recovery line AND stopping the digest header from being a Markdown heading.
  printf '%s\n' "$payload" || true
  return 0
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
  # One stamp for the record AND the rendered payload, so they cannot disagree
  # by a second (#4041).
  INERT_DETAIL="the installed Claude session-start hook could not resolve a tortoise module dir (checked TORTOISE_SRC_DIR, \$HOME/.tortoise/hook-src-dir, and ../..), found no tortoise binary, and injected nothing"
  INERT_STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"
  _record_breadcrumb claude "$INERT_DETAIL" "$INERT_STAMP"
  # #5838: report the INDEPENDENT capture-failure fact too, when a Python
  # renderer is reachable. The two slots are read separately on purpose: the
  # install-inert record just written is the CURRENT run's evidence, while the
  # capture-failure record is a different cause that #5838 exists to preserve.
  # Best-effort — with no importable `tortoise` this prints nothing (see the
  # renderer), and the agent still gets the install-inert block below.
  _render_capture_failure_breadcrumb claude
  # #4041: tell the AGENT, not only the machine. `install-inert` is rendered
  # pure shell, because THIS branch is reached with no interpreter to run
  # Python with.
  _render_breadcrumb_inert claude "$INERT_DETAIL" "$INERT_STAMP" \
    '`tortoise hooks upgrade` reinstalls this hook; `tortoise hooks status` reports the drift. The seam resolved no tortoise module dir, and memory is not filed until it does.'
  # #3797: the hook RAN — record that too, so the install is not reported as
  # never-ran.  No probe was attempted, hence the bare `null`.
  _record_hook_run claude false null
  exit 0
fi
# #4041: read a `capture-failure` breadcrumb left by a previous capture back to
# the agent, before the digest. It renders NOTHING when no breadcrumb is
# present. It deliberately ignores a STALE `install-inert` record — that kind
# is refused by Python (`_render_capture_failure_breadcrumb`), and its own slot
# is read by `session verify`, not here. In the inert branch ABOVE it has
# already run (#5838), so both facts can reach the agent.
_render_capture_failure_breadcrumb claude
if [ -z "$TORTOISE_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || true)"
  if [ -z "$PYTHON_BIN" ]; then
    # Same one-stamp pattern as the branch above (#4041): this branch is the
    # one reached BECAUSE python3 is missing, so the renderer must be shell.
    INERT_DETAIL="the installed Claude session-start hook resolved a tortoise module dir but found no python3 interpreter, and injected nothing"
    INERT_STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"
    _record_breadcrumb claude "$INERT_DETAIL" "$INERT_STAMP"
    _render_breadcrumb_inert claude "$INERT_DETAIL" "$INERT_STAMP" \
      'a `python3` on the PATH this hook runs with provides the interpreter the capture half needs (the hook resolves it with `command -v python3`), and memory is not filed until one is there.'
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
