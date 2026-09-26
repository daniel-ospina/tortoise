#!/usr/bin/env bash
# install-reaper-schedule.sh — install the embedded-reaper periodic sweep
# (issue #1642 FIX 1: the reaper was correct but unscheduled; suites that
# are SIGKILLed/watchdog-killed never sweep, so their redis-servers + socket
# dirs leaked forever — 456 orphans / 32k tempdir entries observed).
#
# Epic #1647 P4 (Task 10) DEMOTION: this schedule is LOCAL-DEV MACHINE
# HYGIENE ONLY. CI runs the docker lane (both fast halves provision
# falkordb; the carve-out files run in the URI-unset carve-out job whose
# conftest _redislite_hygiene sweeps its own sessions), so the embedded
# reaper no longer carries any CI correctness role — docker halves produce
# no embedded orphans by construction (E2E-7). Dev machines keep the
# scheduled sweep for local redislite runs (a dev box's embedded sessions
# can still strand servers on SIGKILL; the cron/launchd sweep reclaims
# them).
#
# Installs `python -m tortoise.embedded_reaper --no-dry-run --only-safe`
# every 20 minutes:
#   - macOS  -> a launchd LaunchAgent (StartInterval 1200)
#   - Linux  -> a cron entry (*/20 * * * *)
# The reaper's singleton lock (<tempdir>/.tortoise-reaper-<uid>/.reaper.lock, fcntl;
# tempdir-scoped since #1658 — NOT ~/.tortoise) makes concurrent runs safe,
# so the periodic run can overlap a suite-end sweep.
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
#   REAPER_INTERVAL interval in SECONDS on BOTH platforms (launchd uses it
#                   directly; cron divides by 60), default REAPER_TIMEOUT + 300
#   REAPER_TIMEOUT  sweep budget in seconds; default 900
#   REAPER_JOBS     parallel CLIENT LIST probe workers; default 16
#   AGENTS_DIR      launchd install dir (default $HOME/Library/LaunchAgents).
#                   ⛔ A THROWAWAY $HOME/AGENTS_DIR DOES NOT SANDBOX THIS
#                   SCRIPT. `launchctl` addresses the user domain BY UID
#                   (`gui/$(id -u)`), so a bootstrap here re-points the LIVE
#                   agent launchd holds for that label whatever HOME says.
#                   The install is REFUSED unless AGENTS_DIR is the LOGIN
#                   ACCOUNT's <passwd-home>/Library/LaunchAgents (resolved
#                   from the password database, not the mutable $HOME) — a
#                   throwaway-$HOME default is refused too — unless
#                   REAPER_ALLOW_NONSTANDARD_AGENTS_DIR=1 (see below).
#   REAPER_ALLOW_NONSTANDARD_AGENTS_DIR  set to 1 to permit a non-standard
#                   AGENTS_DIR — at your own risk; it re-points the live agent
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
# #4299: the sweep's ceiling (`--timeout`) and the probe pool (`--jobs`).
# The condition under which a backlog drains is tracked separately
# (#4487 / #4500); it is not asserted here.
#   - `--timeout`: the SIGALRM sweep budget. `_ReaperLock` serializes sweeps,
#     so the interval must EXCEED the budget or a fire is refused mid-sweep.
#     `REAPER_INTERVAL` defaults to `REAPER_TIMEOUT + 300` and a warning is
#     emitted when it is not greater.
#   - `--jobs`: the parallel CLIENT LIST probe pool. `_run_sweep` used to drop
#     the flag before `reap()`, so it reached discovery only; forwarding it is
#     the #4438 fix.
REAPER_TIMEOUT="${REAPER_TIMEOUT:-900}"
REAPER_JOBS="${REAPER_JOBS:-16}"
# #4438 review P2: bound the digit length BEFORE any arithmetic, and compare
# FAIL-CLOSED. `[ "$X" -lt 1 ]` ERRORS on a value too large for the shell's
# integer type (status 2, which `if` reads as false), so the >= 1 guard was
# bypassed and the wrapped value then passed the digit `case` — installing a
# corrupt `--timeout` that raises OverflowError in `signal.alarm()` on every
# fire. `! [ "$X" -ge 1 ]` turns that exact overflow into the refusal branch.
_MAX_DIGITS=9
case "$REAPER_TIMEOUT" in
    ''|*[!0-9]*)
        echo "ERROR: REAPER_TIMEOUT must be a whole number of seconds, got '$REAPER_TIMEOUT'" >&2
        exit 2 ;;
esac
if [ "${#REAPER_TIMEOUT}" -gt "$_MAX_DIGITS" ]; then
    echo "ERROR: REAPER_TIMEOUT out of range (max ${_MAX_DIGITS} digits), got '$REAPER_TIMEOUT'" >&2
    exit 2
fi
if ! [ "$REAPER_TIMEOUT" -ge 1 ] 2>/dev/null; then
    echo "ERROR: REAPER_TIMEOUT must be >= 1, got '$REAPER_TIMEOUT'" >&2
    exit 2
fi
case "$REAPER_JOBS" in
    ''|*[!0-9]*)
        echo "ERROR: REAPER_JOBS must be a whole number, got '$REAPER_JOBS'" >&2
        exit 2 ;;
esac
if [ "${#REAPER_JOBS}" -gt "$_MAX_DIGITS" ]; then
    echo "ERROR: REAPER_JOBS out of range (max ${_MAX_DIGITS} digits), got '$REAPER_JOBS'" >&2
    exit 2
fi
if ! [ "$REAPER_JOBS" -ge 1 ] 2>/dev/null; then
    echo "ERROR: REAPER_JOBS must be >= 1, got '$REAPER_JOBS'" >&2
    exit 2
fi
# #4438 review: derive the interval from the budget so the
# "interval > budget" invariant holds by construction, and warn (never
# silently) when a hand-set value violates it.
REAPER_INTERVAL="${REAPER_INTERVAL:-$((10#$REAPER_TIMEOUT + 300))}"
case "$REAPER_INTERVAL" in
    ''|*[!0-9]*)
        echo "ERROR: REAPER_INTERVAL must be a whole number of seconds, got '$REAPER_INTERVAL'" >&2
        exit 2 ;;
esac
if ! [ "$REAPER_INTERVAL" -ge 1 ] 2>/dev/null; then
    echo "ERROR: REAPER_INTERVAL must be >= 1, got '$REAPER_INTERVAL'" >&2
    exit 2
fi
if [ "$REAPER_INTERVAL" -le "$REAPER_TIMEOUT" ]; then
    echo "WARNING: REAPER_INTERVAL ($REAPER_INTERVAL) must exceed REAPER_TIMEOUT ($REAPER_TIMEOUT); a fire may be refused mid-sweep" >&2
fi
# #4438 review: cron's minute step is only 0-59, so `*/N` with N >= 60 is
# evaluated over the minute range and matches minute 0 only — SILENTLY hourly,
# while launchd still gets N seconds. Render exact whole-hour intervals in the
# hour field; warn when cron cannot express the interval exactly. Computed
# once here so the warning and the emitted field cannot drift.
CRON_MINUTES=$(( 10#$REAPER_INTERVAL / 60 ))
[ "$CRON_MINUTES" -lt 1 ] && CRON_MINUTES=1
if [ "$CRON_MINUTES" -lt 60 ]; then
    CRON_SCHEDULE="*/$CRON_MINUTES * * * *"
    if [ $(( 10#$REAPER_INTERVAL % 60 )) -ne 0 ]; then
        echo "WARNING: cron takes REAPER_INTERVAL in whole minutes; ${REAPER_INTERVAL}s floors to ${CRON_MINUTES} min" >&2
    fi
elif [ $(( CRON_MINUTES % 60 )) -eq 0 ] && [ $(( CRON_MINUTES / 60 )) -le 23 ]; then
    CRON_SCHEDULE="0 */$(( CRON_MINUTES / 60 )) * * *"
else
    CRON_SCHEDULE="0 * * * *"
    echo "WARNING: cron cannot express REAPER_INTERVAL ${REAPER_INTERVAL}s exactly (${CRON_MINUTES} min); scheduling hourly at minute 0" >&2
fi
REAPER_CMD="$PYTHON_BIN -m tortoise.embedded_reaper --no-dry-run --only-safe --timeout $REAPER_TIMEOUT --jobs $REAPER_JOBS"

usage() {
    # #4438 review: terminate on a marker, not a hard-coded line number —
    # `2,48p` silently truncated `--help` whenever the header grew.
    sed -n '2,/^# Exit codes:/p' "$0" | sed 's/^# \{0,1\}//'
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
    <string>$REAPER_TIMEOUT</string>
    <string>--jobs</string>
    <string>$REAPER_JOBS</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartInterval</key><integer>${REAPER_INTERVAL:-1200}</integer>
  <key>StandardOutPath</key><string>$HOME/.tortoise/reaper.log</string>
  <key>StandardErrorPath</key><string>$HOME/.tortoise/reaper.log</string>
</dict>
</plist>
PLIST
}

cron_line() {
    # CRON_SCHEDULE is computed once at startup (see REAPER_INTERVAL), so the
    # warning and the emitted field share one source of truth.
    echo "$CRON_LINE_RAW"
    echo "$CRON_SCHEDULE cd $REPO && $REAPER_CMD >> \$HOME/.tortoise/reaper.log 2>&1"
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

# #4438 review P1: `launchctl` addresses the user domain BY UID
# (`gui/$(id -u)`), never by HOME — so overriding HOME/AGENTS_DIR with a
# throwaway does NOT sandbox a Darwin install. A `bootstrap` (or a
# label-scoped `bootout`) here re-points the LIVE agent launchd holds for
# `gui/<uid>/$LABEL`, whatever HOME says. Not theoretical: two review runs on
# this host left the live agent pointing at a deleted temp plist (interpreter
# `/path/to/venv/bin/python`, failing EX_CONFIG).
#
# The authority for “where the live agent lives” is the LOGIN ACCOUNT's home
# from the password database — NOT the exported $HOME, which a caller can
# point anywhere. Refuse any AGENTS_DIR other than that account's
# ~/Library/LaunchAgents unless explicitly opted in.
_login_home() {
    local user
    user="$(id -un)"
    if command -v getent >/dev/null 2>&1; then
        getent passwd "$user" 2>/dev/null | cut -d: -f6
    elif command -v dscl >/dev/null 2>&1; then
        dscl . -read "/Users/$user" NFSHomeDirectory 2>/dev/null \
            | awk '{print $2}'
    else
        eval echo "~$user"
    fi
}

require_standard_agents_dir() {
    local expected
    expected="$(_login_home)/Library/LaunchAgents"
    [ "$AGENTS_DIR" = "$expected" ] && return 0
    if [ "${REAPER_ALLOW_NONSTANDARD_AGENTS_DIR:-0}" = "1" ]; then
        echo "WARNING: AGENTS_DIR '$AGENTS_DIR' is not the login account's '$expected'; launchctl acts on gui/$(id -u)/$LABEL regardless (REAPER_ALLOW_NONSTANDARD_AGENTS_DIR=1)" >&2
        return 0
    fi
    echo "ERROR: refusing to install/uninstall to non-standard AGENTS_DIR '$AGENTS_DIR'." >&2
    echo "  launchctl addresses the user domain BY UID, so a throwaway HOME/AGENTS_DIR" >&2
    echo "  does NOT sandbox this install — it would re-point the LIVE" >&2
    echo "  gui/$(id -u)/$LABEL agent (login account dir: '$expected')." >&2
    echo "  Set REAPER_ALLOW_NONSTANDARD_AGENTS_DIR=1 to override." >&2
    return 2
}

install_darwin() {
    # Refuse BEFORE any plist is written or any launchctl call is made.
    require_standard_agents_dir || return $?
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
        echo "installed + loaded: $PLIST_PATH (interval ${REAPER_INTERVAL:-1200}s)"
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
        # #4438 review: the old ERE never matched the marker (it omitted the
        # space in "# ... reaper (#1642)"), so re-running the installer
        # ACCUMULATED markers and broke --status. Match the exact marker and
        # any schedule line by FIXED string.
        current="$(printf '%s\n' "$current" \
            | grep -vF -e "$CRON_MARKER" -e "tortoise.embedded_reaper")"
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
            require_standard_agents_dir || return $?
            launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
            rm -f "$PLIST_PATH"
            echo "removed $PLIST_PATH"
            ;;
        Linux)
            local current filtered
            current="$($CRONTAB_CMD -l 2>/dev/null || true)"
            # `grep -v` exits 1 when it selects NOTHING (a crontab holding
            # only reaper entries) — exactly the state this installer
            # creates. Under `set -o pipefail` that made the write look
            # failed, so `|| return 1` fired and the success message was
            # skipped. Capture the filtered text first, tolerating no-match.
            filtered="$(printf '%s\n' "$current" \
                | { grep -vF -e "$CRON_MARKER" -e "tortoise.embedded_reaper" || true; })"
            if [ -n "$filtered" ]; then
                printf '%s\n' "$filtered" | $CRONTAB_CMD - || return 1
            else
                printf '' | $CRONTAB_CMD - || return 1
            fi
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
            # Propagate the installer's exit code — a refusal (exit 2) must
            # never be followed by a "reaper schedule installed" message.
            Darwin) install_darwin || exit $? ;;
            Linux) install_linux || exit $? ;;
            *) echo "ERROR: unsupported platform: $(uname -s) (launchd/cron only)" >&2; exit 1 ;;
        esac
        echo "reaper schedule installed. Verify: $(dirname "$0")/install-reaper-schedule.sh --status"
        echo "Logs: $HOME/.tortoise/reaper.log"
        ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage; exit 2 ;;
esac
