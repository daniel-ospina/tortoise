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

# ── The local capture-error breadcrumb ───────────────────────────────────
# Mirrors `tortoise.__main__._record_capture_error` (same file layout, same
# `TORTOISE_IMPORT_RECEIPT_DIR` override) for the one case that helper cannot
# cover: the module dir did not resolve, so the Python helper is unreachable.
# A hook that injects nothing must leave EVIDENCE, never silence (#4314).
# Best-effort: a breadcrumb write can never break the exit-0 contract.
_record_breadcrumb() {
  python3 - "$1" "$2" <<'PY' 2>/dev/null || true
import json, os, sys, time
from pathlib import Path
harness, detail = sys.argv[1], sys.argv[2]
receipt_dir = Path(os.environ.get(
    "TORTOISE_IMPORT_RECEIPT_DIR",
    str(Path.home() / ".tortoise" / "import-receipts")))
path = receipt_dir.parent / "capture-errors" / f"{harness}.json"
try:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "harness": harness,
        "detail": detail,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2), encoding="utf-8")
except OSError:
    pass
PY
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
  exit 0
fi
if [ -z "$TORTOISE_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || true)"
  if [ -z "$PYTHON_BIN" ]; then
    _record_breadcrumb claude \
      "the installed Claude session-start hook resolved a tortoise module dir but found no python3 interpreter, and injected nothing"
    exit 0
  fi
  # #3755: the digest is BEST-EFFORT — it must never short-circuit this
  # script, because the install-probe beacon below is independent of it.
  # `|| exit 0` here (before #3755) skipped the probe whenever the embedded
  # store was busy, silently dropping install telemetry. The resolved module
  # dir travels via ENV and is prepended INSIDE ``-c`` — never via ``-m``
  # (CPython prepends the process CWD ahead of PYTHONPATH for ``-m``, so a
  # planted ``tortoise/`` package in the workspace would execute as the user,
  # CWE-427) and never string-interpolated into the source.
  TORTOISE_MODULE_DIR="$TORTOISE_MODULE" "$PYTHON_BIN" -c '
import os, sys
sys.path.insert(0, os.environ["TORTOISE_MODULE_DIR"])
from tortoise.__main__ import main
raise SystemExit(main(["context"]))
' 2>/dev/null || true
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
    TORTOISE_MODULE_DIR="$TORTOISE_MODULE" "$PYTHON_BIN" -c '
import os, sys
sys.path.insert(0, os.environ["TORTOISE_MODULE_DIR"])
from tortoise.__main__ import main
raise SystemExit(main(["session", "probe", "--harness", "claude"]))
' >/dev/null 2>&1 || true
  fi
fi
