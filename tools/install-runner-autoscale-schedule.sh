#!/usr/bin/env bash
# install-runner-autoscale-schedule.sh — make the runner-capacity guard RUN, on this box.
#
# WHY A SCHEDULE AND NOT A HOOK. tools/runner_autoscale.py is a convergence predicate: it is
# only correct if it is EVALUATED repeatedly. Every runner in the pool was added by an ad-hoc
# lane, and a runner that dies stays dead because nothing re-reads it — that is the failure this
# installer exists to end. A manual script nobody calls is the fleet's most repeated defect.
#
# WHY NOT A SCHEDULED GITHUB WORKFLOW. The guard's job is to heal a DEAD RUNNER, and a workflow
# runs ON a runner: with the pool empty or one runner queueing, Actions is exactly the surface
# that cannot fire. The timer therefore lives on the box that owns the capacity.
#
# WIRING (measured, not assumed)
#   macOS  -> a launchd LaunchAgent (StartInterval), per login account
#   Linux  -> a systemd SYSTEM timer  (runner-autoscale.timer, User= at render time), because
#             this cell has systemd as PID 1 and NO crontab at all: a cron entry here would
#             install cleanly and never fire — an inert enforcer, the exact class this repo's
#             AGENTS.md calls out. cron is only used when it is actually installed.
#   Neither available -> REFUSED (exit 2). Nothing is written, and --status says why.
#
# Idempotent: identical rendered unit/plist -> no reload; re-running is safe on every sync.
#
# USAGE
#   tools/install-runner-autoscale-schedule.sh              install (armed: it acts)
#   tools/install-runner-autoscale-schedule.sh --dry-run    render + print; touch nothing
#   tools/install-runner-autoscale-schedule.sh --status     installed? and WILL IT FIRE?
#   tools/install-runner-autoscale-schedule.sh --uninstall  remove it
#   tools/install-runner-autoscale-schedule.sh --help
#
# ENV
#   RUNNER_AUTOSCALE_INTERVAL  seconds between passes (default 600 — a dead runner is replaced
#                              within ~10 min, and a convergence pass is idempotent)
#   RUNNER_AUTOSCALE_REPO      pool to serve (default daniel-ospina/tortoise; the tool's default)
#   AUTOSCALE_PYTHON           interpreter to pin (default: the repo's .venv, else python3)
#   AUTOSCALE_REPO_ROOT        checkout the schedule runs from (default: this script's repo)
#   AUTOSCALE_ALLOW_WORKTREE=1  install from a linked worktree (default: refused — a worktree is
#                              reaped when its PR merges, and the timer would then fail forever)
#   SYSTEMD_UNIT_DIR           default /etc/systemd/system
#   AGENTS_DIR                 launchd dir (default $HOME/Library/LaunchAgents)
#   AUTOSCALE_LOG              log path (default <state dir>/runner-autoscale.job.log)
#   AUTOSCALE_INSTALL_DIR      where the RUNNABLE copy lives (default $HOME/.pi/agent/scripts)
#
# ⛔ WHY A COPY AND NOT THE CHECKOUT PATH. The timer must survive the checkout: a linked worktree
# is reaped when its PR merges, and the main worktree's branch moves. The fleet's own machinery is
# wired this way (~/.pi/agent/scripts/ci_runner_gate.sh is the installed copy of agent-infra's
# script), so the schedule runs a STABLE copy refreshed by every install, and --status prints
# both sha256s so drift between the repo and the running copy is visible rather than assumed.
#
# EXIT  0 ok (or a clean skip) · 1 a step failed · 2 usage / refusal (nothing was written)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${AUTOSCALE_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LABEL="runner-autoscale"
PLIST_LABEL="com.tortoise.runner-autoscale"
INTERVAL="${RUNNER_AUTOSCALE_INTERVAL:-600}"

case "$INTERVAL" in ''|*[!0-9]*) echo "ERROR: RUNNER_AUTOSCALE_INTERVAL must be an integer (got '$INTERVAL')" >&2; exit 2 ;; esac
[ "$INTERVAL" -ge 60 ] || { echo "ERROR: interval must be >= 60s (got $INTERVAL) — a tighter loop only hammers the API" >&2; exit 2; }

usage() { sed -n '2,/^# EXIT/p' "$0" | sed 's/^# \{0,1\}//'; }

# ── the checkout the schedule runs FROM ────────────────────────────────────────────────────────
# ⛔ A LINKED WORKTREE IS EPHEMERAL: it is deleted when its PR merges, so a timer pinned to
# .worktrees/<branch>/… installs cleanly, runs for a few hours, then fails forever with
# "No such file or directory" — a schedule that reports installed and can never fire.
_common="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
case "$_common" in
  */.git)
    _main="${_common%/.git}"
    if [ -d "$_main" ] && [ "$_main" != "$REPO" ] && [ "${AUTOSCALE_ALLOW_WORKTREE:-0}" != "1" ]; then
      echo "ERROR: run from a linked worktree ($REPO)." >&2
      echo "       The schedule runs the code on MAIN, because a worktree is reaped with its PR." >&2
      echo "       Land this change, then re-run from the main checkout;" >&2
      echo "       AUTOSCALE_ALLOW_WORKTREE=1 installs from here anyway (short-lived experiments)." >&2
      exit 2
    fi ;;
esac

# ⛔ A STABLE INTERPRETER. The tool is stdlib-only on purpose, so the SYSTEM python is the right
# pin: the repo's .venv lives in a worktree that is reaped with its PR, and a timer pointing at a
# deleted interpreter fails forever while still reporting "installed".
PYTHON="${AUTOSCALE_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x /usr/bin/python3 ]; then PYTHON=/usr/bin/python3
  elif [ -x "$REPO/.venv/bin/python" ]; then PYTHON="$REPO/.venv/bin/python"
  else PYTHON="$(command -v python3 || true)"; fi
fi
[ -n "$PYTHON" ] && [ -x "$PYTHON" ] || { echo "ERROR: no python3 interpreter (set AUTOSCALE_PYTHON)" >&2; exit 2; }
# INTERPRETER FLOOR, CHECKED AT INSTALL TIME. The tool refuses anything below 3.12 with
# SystemExit(2) — and on macOS `/usr/bin/python3` is the Command Line Tools' 3.9, so an installer
# that only tested `-x` wrote a job that verifies "active" and exits 2 on every fire, forever:
# the "installed but inert" failure this script exists to prevent, one layer below its own check
# (round 7, finding 2). Refuse BEFORE writing anything.
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null \
  || { echo "REFUSED: $PYTHON is older than 3.12 — tools/runner_autoscale.py requires >= 3.12" >&2
       echo "          point AUTOSCALE_PYTHON at a >= 3.12 interpreter." >&2
       exit 2; }
TOOL="$REPO/tools/runner_autoscale.py"
[ -f "$TOOL" ] || { echo "ERROR: $TOOL is missing — the schedule would run nothing" >&2; exit 2; }
INSTALL_DIR="${AUTOSCALE_INSTALL_DIR:-$HOME/.pi/agent/scripts}"
INSTALLED="$INSTALL_DIR/runner_autoscale.py"

# ⛔ RESOLVED ONCE, HERE — NOT INSIDE A RENDER. The heredocs below are UNQUOTED (they expand the
# pinned paths), so ANY backtick in them — including one inside a comment — runs as a command
# substitution at render time. Measured: a backticked "gh" in a comment executed the shim, the
# shim found no credential and launched the interactive device flow, and the installer hung for
# its whole timeout having written NOTHING. So: no backticks in this file, and the gh directory is
# resolved from real file candidates (no execution) and pinned.
GH_DIR=""
for _c in /usr/bin/gh /usr/local/bin/gh "$HOME/bin/gh"; do
  [ -x "$_c" ] && { GH_DIR="$(dirname "$_c")"; break; }
done
[ -n "$GH_DIR" ] || GH_DIR="$(dirname "$(type -P gh 2>/dev/null || echo /usr/bin/gh)")"

STATE_DIR="${RUNNER_AUTOSCALE_STATE_DIR:-$HOME/.pi/agent/state}"
LOG="${AUTOSCALE_LOG:-$STATE_DIR/runner-autoscale.job.log}"
ARGV_TAIL=""
[ -n "${RUNNER_AUTOSCALE_REPO:-}" ] && ARGV_TAIL=" --repo $RUNNER_AUTOSCALE_REPO"

# ── platform detection, fail-closed ───────────────────────────────────────────────────────────
has_systemd() { [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; }
has_cron()    { command -v crontab >/dev/null 2>&1 && [ -n "$(command -v cron 2>/dev/null || command -v crond 2>/dev/null || ls /usr/sbin/cron 2>/dev/null)" ]; }

platform() {
  case "$(uname -s)" in
    Darwin) echo launchd ;;
    Linux)
      if has_systemd; then echo systemd
      elif has_cron; then echo cron
      else echo none; fi ;;
    *) echo unsupported ;;
  esac
}

render_systemd_service() {
  cat <<UNIT
[Unit]
Description=runner-autoscale — converge self-hosted CI runner capacity to the measured ceiling
Documentation=file://$INSTALLED
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$(id -un)
WorkingDirectory=$REPO
# ⛔ HOME IS PINNED AT INSTALL TIME, for the same reason PATH is: gh reads its credential from
# $HOME/.config/gh/hosts.yml, and a unit with no explicit HOME runs with the account's passwd home
# — which on these cells is NOT where the fleet's gh login lives. Measured: without this line the
# first timer fire failed closed with gh auth login (exit 2, nothing changed), i.e. the guard was
# inert. Fail-closed is the right direction, silently inert is not.
Environment=HOME=$HOME
Environment=RUNNER_AUTOSCALE_STATE_DIR=$STATE_DIR
Environment=PATH=$GH_DIR:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
ExecStart=$PYTHON $INSTALLED${ARGV_TAIL}
# A pass is idempotent, so a non-zero exit is a RECORD, not a reason to retry in a tight loop:
# Restart= is deliberately UNSET, and the next timer fire re-evaluates from a fresh measurement.
TimeoutStartSec=1800
UNIT
}

render_systemd_timer() {
  cat <<UNIT
[Unit]
Description=runner-autoscale timer (every ${INTERVAL}s)

[Timer]
OnBootSec=120
OnUnitActiveSec=${INTERVAL}
Unit=$LABEL.service

[Install]
WantedBy=timers.target
UNIT
}

render_plist() {
  cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$PLIST_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$INSTALLED</string>
$( [ -n "${RUNNER_AUTOSCALE_REPO:-}" ] && printf '    <string>--repo</string>\n    <string>%s</string>\n' "$RUNNER_AUTOSCALE_REPO" )
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>$HOME</string>
    <key>RUNNER_AUTOSCALE_STATE_DIR</key><string>$STATE_DIR</string>
  </dict>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
}

cron_line() {
  local minutes=$(( INTERVAL / 60 )); [ "$minutes" -lt 1 ] && minutes=1
  local sched
  if [ "$minutes" -lt 60 ]; then sched="*/$minutes * * * *"
  elif [ $(( minutes % 60 )) -eq 0 ] && [ $(( minutes / 60 )) -le 23 ]; then sched="0 */$(( minutes / 60 )) * * *"
  else sched="0 * * * *"; fi
  printf '%s cd %s && %s %s%s >> %s 2>&1 # %s\n' \
    "$sched" "$REPO" "$PYTHON" "$INSTALLED" "$ARGV_TAIL" "$LOG" "$LABEL-cron"
}

# ── status: "installed" is not "will fire" ────────────────────────────────────────────────────
status() {
  if [ -f "$INSTALLED" ]; then
    echo "run-copy: $INSTALLED sha256=$(sha256sum "$INSTALLED" 2>/dev/null | cut -d' ' -f1)"
    if [ "$(sha256sum "$INSTALLED" 2>/dev/null | cut -d' ' -f1)" != "$(sha256sum "$TOOL" 2>/dev/null | cut -d' ' -f1)" ]; then
      echo "DRIFT: the repo copy ($TOOL) differs from the RUNNING copy — re-run this installer"
    fi
  else
    echo "run-copy: MISSING ($INSTALLED) — the schedule would run nothing"
  fi
  case "$(platform)" in
    systemd)
      local svc="$UNIT_DIR/$LABEL.service" tmr="$UNIT_DIR/$LABEL.timer"
      if [ -f "$tmr" ]; then
        echo "installed: $tmr"
        echo "enabled: $(systemctl is-enabled "$LABEL.timer" 2>&1)"
        echo "active:  $(systemctl is-active "$LABEL.timer" 2>&1)"
        systemctl list-timers "$LABEL.timer" --all --no-pager 2>/dev/null | sed -n '1,2p'
        [ -f "$svc" ] || echo "WARNING: $svc is missing — the timer activates nothing"
      else
        echo "NOT installed (no $tmr)"
      fi ;;
    cron)
      if crontab -l 2>/dev/null | grep -qF "$LABEL-cron"; then
        echo "installed: cron entry present"; crontab -l 2>/dev/null | grep -F "$LABEL-cron"
      else echo "NOT installed (no $LABEL-cron in crontab)"; fi ;;
    launchd)
      local plist="$AGENTS_DIR/$PLIST_LABEL.plist"
      if [ -f "$plist" ]; then
        echo "installed: $plist"
        launchctl list 2>/dev/null | grep -F "$PLIST_LABEL" || echo "loaded: NO (run without --status to load)"
      else echo "NOT installed ($plist missing)"; fi ;;
    none) echo "NOT installed AND CANNOT BE: no systemd and no cron on this box" >&2; return 2 ;;
    *) echo "unsupported platform: $(uname -s)" >&2; return 2 ;;
  esac
}

# ── arg parsing ──────────────────────────────────────────────────────────────────────────────
MODE=install
for a in "$@"; do
  case "$a" in
    --dry-run)   MODE=dry ;;
    --status)    MODE=status ;;
    --uninstall) MODE=uninstall ;;
    -h|--help)   usage; exit 0 ;;
    *) echo "usage: $0 [--dry-run|--status|--uninstall|--help]" >&2; exit 2 ;;
  esac
done

PLATFORM="$(platform)"
UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
AGENTS_DIR="${AGENTS_DIR:-$HOME/Library/LaunchAgents}"

case "$MODE" in
  status) status; exit $? ;;
  dry)
    echo "# platform=$PLATFORM repo=$REPO python=$PYTHON interval=${INTERVAL}s"
    case "$PLATFORM" in
      systemd) echo "--- $UNIT_DIR/$LABEL.service"; render_systemd_service
               echo "--- $UNIT_DIR/$LABEL.timer"; render_systemd_timer ;;
      cron)    cron_line ;;
      launchd) render_plist ;;
      *) echo "REFUSED: no systemd, no cron — a schedule here would never fire" >&2; exit 2 ;;
    esac
    exit 0 ;;
  uninstall)
    case "$PLATFORM" in
      systemd)
        sudo -n systemctl disable --now "$LABEL.timer" 2>/dev/null || true
        sudo -n rm -f "$UNIT_DIR/$LABEL.timer" "$UNIT_DIR/$LABEL.service" || exit 1
        sudo -n systemctl daemon-reload || true
        rm -f "$INSTALLED" 2>/dev/null || true
    echo "removed: $LABEL.timer/.service + the runnable copy" ;;
      cron)
        crontab -l 2>/dev/null | grep -vF "$LABEL-cron" | crontab - || { echo "ERROR: could not rewrite crontab" >&2; exit 1; }
        echo "removed: $LABEL-cron" ;;
      launchd)
        launchctl bootout "gui/$(id -u)/$PLIST_LABEL" 2>/dev/null || true
        rm -f "$AGENTS_DIR/$PLIST_LABEL.plist" || exit 1
        echo "removed: $PLIST_LABEL" ;;
      *) echo "nothing to remove on $(uname -s)" ;;
    esac
    exit 0 ;;
esac

# ── install ──────────────────────────────────────────────────────────────────────────────────
# ⛔ REFUSE BEFORE WRITING ANYTHING. A box with no scheduler must be left untouched — the header
# promises "nothing was written", and refreshing the runnable copy first made that a half-truth
# (VGATE round 1). Check the platform, THEN mutate.
case "$PLATFORM" in
  systemd|cron|launchd) : ;;
  *) echo "REFUSED: no systemd and no cron on this box — a schedule here would install and never fire." >&2
     echo "         Install one, or run tools/runner_autoscale.py from the fleet's controller loop." >&2
     exit 2 ;;
esac
mkdir -p "$STATE_DIR" "$INSTALL_DIR" || exit 1
install -m 0755 "$TOOL" "$INSTALLED" || { echo "ERROR: could not install the runnable copy" >&2; exit 1; }
echo "run-copy: $INSTALLED (sha256=$(sha256sum "$INSTALLED" | cut -d' ' -f1))"
case "$PLATFORM" in
  systemd)
    changed=0
    want_svc="$(render_systemd_service)"; want_tmr="$(render_systemd_timer)"
    cur_svc="$(sudo -n cat "$UNIT_DIR/$LABEL.service" 2>/dev/null || true)"
    cur_tmr="$(sudo -n cat "$UNIT_DIR/$LABEL.timer" 2>/dev/null || true)"
    if [ "$want_svc" != "$cur_svc" ]; then
      printf '%s\n' "$want_svc" | sudo -n tee "$UNIT_DIR/$LABEL.service" >/dev/null || exit 1
      changed=1
    fi
    if [ "$want_tmr" != "$cur_tmr" ]; then
      printf '%s\n' "$want_tmr" | sudo -n tee "$UNIT_DIR/$LABEL.timer" >/dev/null || exit 1
      changed=1
    fi
    if [ "$changed" = 1 ]; then
      sudo -n systemctl daemon-reload || exit 1
      sudo -n systemctl enable "$LABEL.timer" || exit 1
    fi
    sudo -n systemctl start "$LABEL.timer" || exit 1
    # VERIFY the timer will actually fire — "enabled" alone is not a schedule.
    state="$(systemctl is-active "$LABEL.timer" 2>&1)"
    [ "$state" = "active" ] || { echo "ERROR: $LABEL.timer is '$state', not active — it will not fire" >&2; exit 1; }
    echo "installed: $UNIT_DIR/$LABEL.{service,timer} (active, every ${INTERVAL}s, changed=$changed)"
    systemctl list-timers "$LABEL.timer" --no-pager 2>/dev/null | sed -n '1,2p' ;;
  cron)
    line="$(cron_line)"
    if crontab -l 2>/dev/null | grep -qF "$LABEL-cron"; then
      cur="$(crontab -l 2>/dev/null | grep -F "$LABEL-cron")"
      if [ "$cur" = "$line" ]; then echo "installed (unchanged): cron entry present"; exit 0; fi
      crontab -l 2>/dev/null | grep -vF "$LABEL-cron" | { cat; echo "$line"; } | crontab - || exit 1
    else
      { crontab -l 2>/dev/null; echo "$line"; } | crontab - || exit 1
    fi
    echo "installed: cron entry -> $line" ;;
  launchd)
    mkdir -p "$AGENTS_DIR" || exit 1
    plist="$AGENTS_DIR/$PLIST_LABEL.plist"
    want="$(render_plist)"
    if [ "$want" = "$(cat "$plist" 2>/dev/null || true)" ]; then
      echo "installed (unchanged): $plist"
    else
      printf '%s\n' "$want" > "$plist" || exit 1
      plutil -lint "$plist" >/dev/null 2>&1 || { echo "ERROR: rendered plist does not lint" >&2; exit 1; }
      launchctl bootout "gui/$(id -u)/$PLIST_LABEL" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$plist" || exit 1
      echo "installed: $plist"
    fi
    launchctl list 2>/dev/null | grep -F "$PLIST_LABEL" || { echo "ERROR: $PLIST_LABEL is not loaded" >&2; exit 1; } ;;
  *)
    echo "REFUSED: no systemd and no cron on this box — a schedule here would install and never fire." >&2
    echo "         Install one, or run tools/runner_autoscale.py from the fleet's controller loop." >&2
    exit 2 ;;
esac
