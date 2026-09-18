#!/usr/bin/env bash
# tortoise-hook-version: 4
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
import json, sys
try:
    print(json.load(sys.stdin).get("session_id") or "")
except Exception:
    print("")
' 2>/dev/null || true)"
fi

# Prefer a local install; fall back to the repo checkout.
TORTOISE_BIN="$(command -v tortoise || true)"
if [ -z "$TORTOISE_BIN" ]; then
  # Source tree fallback (this repo checked out).
  TORTOISE_MODULE="${TORTOISE_SRC_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
  if [ ! -d "$TORTOISE_MODULE/tortoise" ]; then
    exit 0
  fi
  PYTHON_BIN="$(command -v python3 || true)"
  [ -z "$PYTHON_BIN" ] && exit 0
  # #3755: the digest is BEST-EFFORT — it must never short-circuit this
  # script, because the install-probe beacon below is independent of it.
  # `|| exit 0` here (before #3755) skipped the probe whenever the embedded
  # store was busy, silently dropping install telemetry.
  "$PYTHON_BIN" -c "
import sys
sys.path.insert(0, '$TORTOISE_MODULE')
from tortoise.__main__ import main
raise SystemExit(main(['context']))
" 2>/dev/null || true
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
TORTOISE_BIN="$(command -v tortoise || true)"
if [ -n "$TORTOISE_BIN" ]; then
  "$TORTOISE_BIN" session probe --harness claude >/dev/null 2>&1 || true
else
  PYTHON_BIN="$(command -v python3 || true)"
  if [ -n "$PYTHON_BIN" ] && [ -d "$TORTOISE_MODULE/tortoise" ]; then
    "$PYTHON_BIN" -c "
import sys
sys.path.insert(0, '$TORTOISE_MODULE')
from tortoise.__main__ import main
raise SystemExit(main(['session', 'probe', '--harness', 'claude']))
" >/dev/null 2>&1 || true
  fi
fi

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
  if [ ${#EXCLUDE_ARGS[@]} -gt 0 ]; then
    nohup "$PYTHON_BIN" -c "
import sys
sys.path.insert(0, '$TORTOISE_MODULE')
from tortoise.__main__ import main
raise SystemExit(main(['session', 'drain', '--exclude-session-id', sys.argv[1]]))
" "$LIVE_SESSION_ID" >/dev/null 2>&1 &
  else
    nohup "$PYTHON_BIN" -c "
import sys
sys.path.insert(0, '$TORTOISE_MODULE')
from tortoise.__main__ import main
raise SystemExit(main(['session', 'drain']))
" >/dev/null 2>&1 &
  fi
fi
