#!/usr/bin/env bash
# health.sh — Check all Meeting Intelligence Pipeline components
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { printf "  ${GREEN}✅${NC} %s\n" "$1"; }
fail() { printf "  ${RED}❌${NC} %s — %s\n" "$1" "$2"; }
warn() { printf "  ${YELLOW}⚠️${NC}  %s\n" "$1"; }

echo "=== Meeting Intelligence Pipeline Health ==="
echo ""

# Minutes CLI
echo "Minutes:"
if command -v minutes &>/dev/null; then
    pass "CLI installed ($(minutes --version 2>/dev/null || echo 'unknown'))"
else
    fail "CLI not installed" "brew install minutes"
fi

# Minutes model
if [ -d ~/.minutes/models ] && [ "$(ls -A ~/.minutes/models 2>/dev/null)" ]; then
    pass "Whisper model downloaded"
else
    warn "Whisper model not found — run: minutes setup --model small"
fi

# Minutes MCP
if pgrep -f "minutes-mcp" > /dev/null; then
    pass "MCP server running"
else
    warn "MCP server not running — run: npx minutes-mcp &"
fi

# Twenty CRM
echo ""
echo "Twenty CRM:"
TWENTY_URL="${TWENTY_BASE_URL:-http://localhost:3001}"
if [[ ! "$TWENTY_URL" =~ ^https?://[a-zA-Z0-9._-]+(:[0-9]+)?$ ]]; then
    fail "Invalid TWENTY_BASE_URL" "must be http(s)://host[:port]"
else
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$TWENTY_URL/api/health" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        pass "API reachable at $TWENTY_URL"
    else
        fail "API unreachable (HTTP $HTTP_CODE)" "docker compose up -d"
    fi
fi

# Docker
echo ""
echo "Infrastructure:"
if docker info &>/dev/null; then
    pass "Docker running"
else
    fail "Docker not running" "Start Docker Desktop or OrbStack"
fi

# Tortoise
if curl -s -o /dev/null localhost:6379 2>/dev/null; then
    pass "Tortoise/FalkorDB reachable (port 6379)"
else
    warn "Tortoise not detected — MCP queries may use file fallback"
fi

# Disk usage
echo ""
echo "Storage:"
DISK_PCT=$(df -h ~ | tail -1 | awk '{print $5}' | sed 's/%//')
if [ "$DISK_PCT" -lt 80 ]; then
    pass "Disk usage: ${DISK_PCT}%"
else
    fail "Disk usage: ${DISK_PCT}%" "Clean up old recordings: minutes cleanup"
fi

# Recent activity
echo ""
echo "Activity:"
MEETINGS_DIR=~/meetings
if [ -d "$MEETINGS_DIR" ]; then
    MEETING_COUNT=$(ls "$MEETINGS_DIR"/*.md 2>/dev/null | wc -l | tr -d ' ')
    LATEST=$(ls -t "$MEETINGS_DIR"/*.md 2>/dev/null | head -1)
    if [ -n "$LATEST" ]; then
        pass "$MEETING_COUNT meetings recorded"
        pass "Latest: $(basename "$LATEST") ($(stat -f '%Sm' "$LATEST" 2>/dev/null || echo 'unknown'))"
    else
        warn "No meetings recorded yet"
    fi
else
    warn "~/meetings/ directory not found — record your first meeting"
fi

# Bridge watchdog
echo ""
echo "Automation:"
if [ -f ~/.minutes/watchdog.pid ]; then
    WATCHDOG_PID=$(cat ~/.minutes/watchdog.pid)
    if kill -0 "$WATCHDOG_PID" 2>/dev/null; then
        pass "Bridge watchdog running (PID $WATCHDOG_PID)"
    else
        warn "Watchdog PID file exists but process dead — restart: launchctl start com.minutes.bridge"
    fi
elif launchctl print gui/$(id -u)/com.minutes.bridge 2>/dev/null | grep -q 'state = running'; then
    pass "Bridge watchdog running (launchd)"
else
    warn "Bridge watchdog not running — launchctl load ~/Library/LaunchAgents/com.minutes.bridge.plist"
fi

echo ""
echo "=== Health Check Complete ==="
