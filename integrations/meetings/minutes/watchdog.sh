#!/usr/bin/env bash
# watchdog.sh — inotify watcher for ~/meetings/
# Monitors for new Minutes markdown files and triggers the bridge script.
set -euo pipefail

WATCH_DIR="${HOME}/meetings"
BRIDGE_SCRIPT="${HOME}/.minutes/bridge.py"
LOG_FILE="${HOME}/.minutes/watchdog.log"
PID_FILE="${HOME}/.minutes/watchdog.pid"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"
}

# Prevent duplicate watchdog instances (macOS-compatible)
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        log "Another watchdog is already running (PID $OLD_PID). Exiting."
        exit 0
    fi
    log "Removing stale PID file (PID $OLD_PID no longer running)"
    rm -f "$PID_FILE"
fi
echo $$ > "$PID_FILE"

trap 'rm -f "$PID_FILE"; log "Watchdog stopped."' EXIT SIGTERM SIGINT

# Check bridge script exists
if [ ! -f "$BRIDGE_SCRIPT" ]; then
    log "ERROR: Bridge script not found at $BRIDGE_SCRIPT"
    log "Symlink it: ln -s $(pwd)/integrations/crm/twenty/bridge.py $BRIDGE_SCRIPT"
    exit 1
fi

log "Watchdog started — monitoring $WATCH_DIR"

PROCESSED_FILE="${HOME}/.minutes/processed_files.txt"
touch "$PROCESSED_FILE"

if command -v fswatch &>/dev/null; then
    log "Using fswatch for event-driven monitoring"
    fswatch -0 --event Created --event Renamed "$WATCH_DIR" | while read -d "" file; do
        if [[ "$file" == *.md ]] && ! grep -qxF "$file" "$PROCESSED_FILE"; then
            log "New meeting detected: $file"
            sleep 2
            if perl -e 'alarm shift; exec @ARGV' 120 python3 "$BRIDGE_SCRIPT" "$file" >> "$LOG_FILE" 2>&1; then
                printf '%s\n' "$file" >> "$PROCESSED_FILE"
                log "✅ Processed: $file"
            else
                log "❌ Failed: $file (will retry on next cycle)"
            fi
        fi
    done
else
    log "fswatch not installed — using polling (5s interval)"
    log "Install fswatch for event-driven: brew install fswatch"
    while true; do
        for file in "$WATCH_DIR"/*.md; do
            [ -f "$file" ] || continue
            if ! grep -qxF "$file" "$PROCESSED_FILE"; then
                log "New meeting detected: $file"
                sleep 2
                if perl -e 'alarm shift; exec @ARGV' 120 python3 "$BRIDGE_SCRIPT" "$file" >> "$LOG_FILE" 2>&1; then
                    printf '%s\n' "$file" >> "$PROCESSED_FILE"
                    log "✅ Processed: $file"
                else
                    log "❌ Failed: $file (will retry)"
                fi
            fi
        done
        sleep 5
    done
fi
