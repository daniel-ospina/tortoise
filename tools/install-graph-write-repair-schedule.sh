#!/usr/bin/env bash
# install-graph-write-repair-schedule.sh — install the periodic check+repair of the
# shared `tortoise` graph's write path (tools/graph_write_repair.py).
#
# WHY THIS EXISTS (MEASURED, not inferred)
# ----------------------------------------
# MEASURED 2026-10-13 on FalkorDB 6.0.1 (`falkordb-16379`), the shared `tortoise` graph:
# READS succeed for every query while EVERY WRITE is refused with
#     ResponseError: graph was deleted or replaced while the query was running, aborting
# from the SDK, raw Cypher and `redis-cli` alike. It survives a container restart, and it
# is SPECIFIC TO THIS GRAPH — before a restart `tortoise`, `org_tortoise`,
# `registry_tortoise`, `test_test_9ac6a1580cd5` and `org_team_s1_aed9f79d` were all
# writable; after it, the other four were and ONLY `tortoise` was broken. Not eviction
# (`maxmemory=0`, `noeviction`) and not a concurrent deleter (a `MONITOR` capture during a
# refused write showed no `GRAPH.DELETE` of `tortoise`).
#
# The container runs with `restartpolicy=always`, so EVERY restart silently takes the
# whole fleet's graph writes down — and a lane that hits it sees a read-only graph with no
# idea why. A repair that only runs when a lane notices inherits exactly the weakness the
# defect creates: the lanes that would notice are the lanes that are blocked.
#
# So this installs the missing trigger: `graph_write_repair.py` on a timer, no lane in the
# loop.
#
# WHY THE ENTRY POINT IS THE CHECKOUT'S OWN PYTHON, NOT `bash`
# -----------------------------------------------------------
# macOS TCC is per-binary. A launchd-spawned `bash` is denied the read of a script under
# ~/Documents ("Operation not permitted") — the failure recorded in
# templates/launchd/com.tortoise.worktree-reaper.plist and agent-infra #427/#431. The
# binary that DOES hold the access is the checkout's own interpreter, so the plist starts
# `<repo>/.venv/bin/python` directly on the tool.
#
# WHY A SCHEDULE IS ENOUGH — THE TOOL'S SAFETY CONTRACT
# -----------------------------------------------------
# Arming it unattended is safe because the tool:
#   * REPAIRS ONLY WHAT IT JUST PROVED BROKEN — it writes a probe node first and repairs
#     only if that write is refused, so a healthy graph is a cheap no-op;
#   * KEEPS A FALLBACK AND NEVER DELETES IT — `GRAPH.COPY` to a scratch clone, which it
#     refuses to swap in unless the clone is complete AND writable; on EVERY failure it
#     parks that clone at one deterministic quarantine key per graph (`__gwr_q_<graph>`,
#     replaced each run, so a standing failure cannot accumulate full-graph copies — except
#     when the new clone is strictly smaller than the copy already retained, which is kept
#     and the smaller clone preserved under its run-unique name instead);
#   * VERIFIES BY WRITING AND READING BACK, never by a return value;
#   * IS IDEMPOTENT — safe to re-run at any time.
#
# EXIT CODE IS A REPORT, NOT A JOB HEALTH SIGNAL
# ----------------------------------------------
# `0` healthy or repaired / `2` cannot connect / `3` broken and NOT repaired. launchd does
# not alert on exit status; the READ ARTIFACT is the log. A green run after a restart is
# the repair working. A standing `3` is the alarm: the repair itself failed and a human
# needs to see it. A standing `2` means the container is down.
#
# Usage:
#   bash tools/install-graph-write-repair-schedule.sh --apply
#   bash tools/install-graph-write-repair-schedule.sh --apply --interval 300
#   bash tools/install-graph-write-repair-schedule.sh --uninstall
#   bash tools/install-graph-write-repair-schedule.sh --status
#
# Without --apply it renders and reports what it WOULD do, and changes nothing.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.tortoise.graph-write-repair"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/$LABEL.plist"
LOG_DIR="$HOME/.pi/agent/state/graph-write-repair"
INTERVAL=300
APPLY=0
UNINSTALL=0
STATUS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --status) STATUS=1 ;;
    --interval) INTERVAL="${2:?--interval needs a value}"; shift ;;
    -h|--help) sed -n '2,61p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

PY="$REPO/.venv/bin/python"
TOOL="$REPO/tools/graph_write_repair.py"

if [[ "$STATUS" == 1 ]]; then
  echo "label:    $LABEL"
  echo "plist:    $PLIST $([[ -f "$PLIST" ]] && echo '(present)' || echo '(absent)')"
  echo "tool:     $TOOL   $([[ -f "$TOOL" ]] && echo '(present)' || echo '(MISSING)')"
  echo "python:   $PY     $([[ -x "$PY" ]] && echo '(executable)' || echo '(MISSING)')"
  echo "logs:     $LOG_DIR"
  if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    echo "launchd:  loaded"
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state|runs|last exit" | head -5 || true
  else
    echo "launchd:  NOT loaded"
  fi
  echo
  echo "last run log:"
  tail -n 12 "$LOG_DIR/run.log" 2>/dev/null || echo "  (no log yet)"
  exit 0
fi

if [[ "$UNINSTALL" == 1 ]]; then
  if [[ "$APPLY" != 1 ]]; then echo "refusing to uninstall without --apply"; exit 2; fi
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  # VERIFY the bootout. `|| true` above silences a failure, and `rm -f` then removes the
  # config -- so a job that failed to boot out stays LOADED and keeps firing every
  # StartInterval with no plist on disk. Refuse to remove the plist in that case.
  if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    echo "ERROR: '$LABEL' is STILL LOADED after bootout -- leaving $PLIST in place." >&2
    echo "       Inspect with: launchctl print gui/$(id -u)/$LABEL" >&2
    exit 3
  fi
  rm -f "$PLIST"
  echo "uninstalled $LABEL (verified not loaded)"
  exit 0
fi

if [[ ! -x "$PY" ]]; then echo "ERROR: no executable python at $PY" >&2; exit 2; fi
if [[ ! -f "$TOOL" ]]; then echo "ERROR: no tool at $TOOL" >&2; exit 2; fi

# Validate the interval BEFORE it is interpolated into <integer>: `plutil` rejects
# <integer>5m</integer>, but the rendered plist was previously written to $PLIST first, so a
# plist that failed lint stayed on disk where launchd could later try to load it.
if ! [[ "$INTERVAL" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --interval must be an integer number of seconds (got '$INTERVAL')" >&2
  exit 2
fi
if (( INTERVAL < 30 )); then
  echo "ERROR: --interval must be >= 30s (got ${INTERVAL}s)" >&2
  exit 2
fi

# ⛔ THE WORKTREE GUARD APPLIES TO --apply ONLY. A dry run installs nothing, so refusing it
# from a worktree would only stop a lane from INSPECTING the render — the check belongs at
# the moment of installing, not at the moment of looking.
if [[ "$APPLY" == 1 && "$REPO" == *"/.worktrees/"* ]]; then
  echo "ERROR: refusing to install from a lane worktree:" >&2
  echo "       $REPO" >&2
  echo "       The job names this path absolutely and the worktree reaper will delete it," >&2
  echo "       after which the job fails SILENTLY (a launchd spawn failure never reaches the" >&2
  echo "       tool's own error.log). Run this from the primary checkout:" >&2
  echo "         cd /Users/danielospina/Documents/GitHub/tortoise && bash tools/install-graph-write-repair-schedule.sh --apply" >&2
  exit 2
fi

xml_escape() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

# The wrapper redirects the tool's output to a log launchd owns, and forwards the tool's
# exit code so `last exit code` in `launchctl print` is the tool's verdict, not the shell's.
render_plist() {
  cat <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <!-- The checkout's own interpreter, NOT bash: macOS TCC denies a launchd-spawned bash
       the read of a script under ~/Documents (agent-infra #427/#431). -->
  <key>ProgramArguments</key>
  <array>
    <string>$(xml_escape "$PY")</string>
    <string>$(xml_escape "$TOOL")</string>
    <string>--quiet</string>
  </array>
  <key>WorkingDirectory</key><string>$(xml_escape "$REPO")</string>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$(xml_escape "$LOG_DIR/run.log")</string>
  <key>StandardErrorPath</key><string>$(xml_escape "$LOG_DIR/error.log")</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$(xml_escape "$HOME/.local/bin")</string>
    <key>HOME</key><string>$(xml_escape "$HOME")</string>
  </dict>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLIST_EOF
}

echo "label:    $LABEL"
echo "repo:     $REPO"
echo "tool:     $TOOL"
echo "interval: ${INTERVAL}s"
echo "plist:    $PLIST"

if [[ "$APPLY" != 1 ]]; then
  echo
  echo "DRY RUN (no --apply). Rendered plist would be:"
  render_plist
  exit 0
fi

mkdir -p "$PLIST_DIR" "$LOG_DIR"
# Render to a TEMP file, lint it, then move it into place -- so a plist that fails lint is
# never left at $PLIST where launchd could later try to load it.
_tmp="$PLIST.tmp.$$"
render_plist > "$_tmp"
if ! plutil -lint "$_tmp" >/dev/null; then
  echo "ERROR: rendered plist is invalid; nothing installed" >&2
  plutil -lint "$_tmp" >&2 || true
  rm -f "$_tmp"
  exit 1
fi
mv "$_tmp" "$PLIST"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true

# VERIFY THE NEW JOB LOADED. Without this, a failed `bootstrap` aborts under `set -e` and
# leaves a plist on disk with NO job loaded -- the repair silently stops, which is the exact
# failure the worktree guard above exists to prevent. (The uninstall path already verifies.)
if ! launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "ERROR: '$LABEL' did NOT load after bootstrap -- the repair would be silently dead." >&2
  echo "       The plist is at $PLIST; inspect with:" >&2
  echo "         launchctl print gui/$(id -u)/$LABEL" >&2
  exit 3
fi

echo
echo "installed and bootstrapped."
echo "Run the tool once NOW to prove the path works end to end:"
echo "  cd $REPO && .venv/bin/python tools/graph_write_repair.py"
echo "Then inspect:"
echo "  bash tools/install-graph-write-repair-schedule.sh --status"
