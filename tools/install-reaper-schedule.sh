#!/usr/bin/env bash
# install-reaper-schedule.sh — install the embedded-reaper periodic sweep
# (issue #1642 FIX 1: the reaper was correct but unscheduled; suites that
# are SIGKILLed/watchdog-killed never sweep, so their redis-servers + socket
# dirs leaked forever — 456 orphans / 32k tempdir entries observed).
#
# Installs `python -m tortoise.embedded_reaper --no-dry-run --only-safe`
# every 10 minutes:
#   - macOS  -> a launchd LaunchAgent (StartInterval 600)
#   - Linux  -> a cron entry (*/10 * * * *)
# The reaper's singleton lock (~/.tortoise/.reaper.lock, fcntl) makes
# concurrent runs safe, so the periodic run can overlap a suite-end sweep.
# --only-safe is the concurrency-safe mode: it kills only orphan-CONFIRMED
# live servers (persisted 0-client state >= 10 min, no live suite markers)
# plus stale_socket leftovers — a running test suite's servers are never
# disturbed (#1642 FIX 3).
#
# Idempotent: byte-identical rendered plist/cron line -> no reload. Safe to
# re-run on every sync.
#
# Usage:
#   install-reaper-schedule.sh             install (macOS launchd / Linux cron)
#   install-reaper-schedule.sh --status    show install state
#   install-reaper-schedule.sh --uninstall remove the schedule
#   install-reaper-schedule.sh --help
#
# Env overrides:
#   TORTOISE_REPO   repo root (default: this script's repo)
#   PYTHON_BIN      interpreter for the sweep (default: <repo>/.venv/bin/python
#                   if present, else `command -v python3`)
#   REAPER_INTERVAL interval seconds (launchd) / minutes (cron); default 600/10
#   AGENTS_DIR      launchd install dir (default $HOME/Library/LaunchAgents)
#   CRONTAB_CMD     crontab binary (default: crontab)
#
# Exit codes: 0 = ok (or clean skip), 1 = failure (loud), 2 = usage error.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${TORTOISE_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LABEL="com.tortoise.embedded-reaper"
PLIST_NAME="$LABEL.plist"
AGENTS_DIR="${AGENTS_DIR:-$HOME/Library/LaunchAgents}"
PLIST_PATH="$AGENTS_DIR/$PLIST_NAME"
CRONTAB_CMD="${CRONTAB_CMD:-crontab}"
CRON_MARKER="# tortoise-embedded-reaper (#1642)"
CRON_LINE_RAW="$CRON_MARKER"

if [ -x "$REPO/.venv/bin/python" ]; then
    PYTHON_BIN="${PYTHON_BIN:-$REPO/.venv/bin/python}"
else
    PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
fi
if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: no python3 found (set PYTHON_BIN)" >&2
    exit 1
fi
REAPER_CMD="$PYTHON_BIN -m tortoise.embedded_reaper --no-dry-run --only-safe --timeout 300"

usage() {
    sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
}

render_plist() {
    cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_BIN</string>
    <string>-m</string>
    <string>tortoise.embedded_reaper</string>
    <string>--no-dry-run</string>
    <string>--only-safe</string>
    <string>--timeout</string>
    <string>300</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartInterval</key><integer>${REAPER_INTERVAL:-600}</integer>
  <key>StandardOutPath</key><string>$HOME/.tortoise/reaper.log</string>
  <key>StandardErrorPath</key><string>$HOME/.tortoise/reaper.log</string>
</dict>
</plist>
PLIST
}

cron_line() {
    # run every REAPER_INTERVAL minutes (default 10)
    local interval="${REAPER_INTERVAL:-600}"
    local minutes=$(( interval / 60 ))
    [ "$minutes" -lt 1 ] && minutes=1
    echo "$CRON_LINE_RAW"
    echo "*/$minutes * * * * cd $REPO && $REAPER_CMD >> \$HOME/.tortoise/reaper.log 2>&1"
}

status() {
    case "$(uname -s)" in
        Darwin)
            if [ -f "$PLIST_PATH" ]; then
                echo "installed: $PLIST_PATH"
                plutil -lint "$PLIST_PATH" 2>/dev/null && echo "plist lint: OK"
                launchctl list 2>/dev/null | grep -F "$LABEL" \
                    || echo "loaded: NO (launchctl list has no $LABEL — run without --status to load)"
            else
                echo "NOT installed ($PLIST_PATH missing)"
            fi
            ;;
        Linux)
            if $CRONTAB_CMD -l 2>/dev/null | grep -qF "$CRON_MARKER"; then
                echo "installed: cron entry present:"
                $CRONTAB_CMD -l 2>/dev/null | grep -F "$CRON_MARKER" -A1
            else
                echo "NOT installed (no $CRON_MARKER in crontab)"
            fi
            ;;
        *) echo "unsupported platform: $(uname -s)"; return 1 ;;
    esac
    return 0
}

install_darwin() {
    mkdir -p "$AGENTS_DIR"
    render_plist > "$PLIST_PATH.tmp"
    local changed=0
    if [ -f "$PLIST_PATH" ]; then
        if cmp -s "$PLIST_PATH" "$PLIST_PATH.tmp"; then
            changed=0
        else
            changed=1
        fi
    else
        changed=1
    fi
    mv "$PLIST_PATH.tmp" "$PLIST_PATH"
    if [ "$changed" -eq 0 ]; then
        echo "unchanged: $PLIST_PATH (no reload)"
    else
        launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
        launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true
        if ! launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"; then
            echo "ERROR: launchctl bootstrap failed — see 'launchctl print gui/$(id -u)/$LABEL'" >&2
            return 1
        fi
        echo "installed + loaded: $PLIST_PATH (interval ${REAPER_INTERVAL:-600}s)"
    fi
    plutil -lint "$PLIST_PATH" || { echo "ERROR: rendered plist invalid" >&2; return 1; }
    return 0
}

install_linux() {
    local current
    current="$($CRONTAB_CMD -l 2>/dev/null || true)"
    local new_line
    new_line="$(cron_line | tail -1)"
    if printf '%s\n' "$current" | grep -qF "$CRON_MARKER"; then
        # Replace any prior tortoise-reaper block (marker + schedule lines).
        current="$(printf '%s\n' "$current" \
            | grep -vE "^# tortoise-embedded-reaper\\(#1642\\)$|tortoise\\.embedded_reaper")"
        printf '%s\n%s\n%s\n' "$current" "$CRON_MARKER" "$new_line" \
            | $CRONTAB_CMD - || return 1
        echo "updated cron entry: $new_line"
    else
        printf '%s\n%s\n%s\n' "$current" "$CRON_MARKER" "$new_line" \
            | $CRONTAB_CMD - || return 1
        echo "installed cron entry: $new_line"
    fi
    return 0
}

uninstall() {
    case "$(uname -s)" in
        Darwin)
            launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
            rm -f "$PLIST_PATH"
            echo "removed $PLIST_PATH"
            ;;
        Linux)
            local current
            current="$($CRONTAB_CMD -l 2>/dev/null || true)"
            printf '%s\n' "$current" \
                | grep -vE "^# tortoise-embedded-reaper\\(#1642\\)$|tortoise\\.embedded_reaper" \
                | $CRONTAB_CMD - || return 1
            echo "removed cron entry ($CRON_MARKER)"
            ;;
        *) echo "unsupported platform: $(uname -s)"; return 1 ;;
    esac
    return 0
}

case "${1:-}" in
    --status) status ;;
    --uninstall) uninstall ;;
    --help|-h) usage ;;
    "")
        case "$(uname -s)" in
            Darwin) install_darwin ;;
            Linux) install_linux ;;
            *) echo "ERROR: unsupported platform: $(uname -s) (launchd/cron only)" >&2; exit 1 ;;
        esac
        echo "reaper schedule installed. Verify: $(dirname "$0")/install-reaper-schedule.sh --status"
        echo "Logs: $HOME/.tortoise/reaper.log"
        ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage; exit 2 ;;
esac
