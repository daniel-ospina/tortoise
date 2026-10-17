#!/usr/bin/env bash
# install-ci-check-repair-schedule.sh — install the periodic LOCAL FALLBACK for
# the `ci-check-repair` workflow (#8375 item 1).
#
# WHY A SCHEDULE AND NOT A HOOK. The fallback exists for the two cases the
# workflow cannot cover: GitHub Actions being degraded (so the scheduled
# workflow never fires) and the workflow REPORTING a PR that needs a credential
# able to create a workflow run. Both are steady states, so a timer that
# re-checks is what sees them; nothing else needs to invoke it.
#
# WHY THE ENTRY POINT IS THE CHECKOUT'S INTERPRETER ON A PYTHON SHIM, NOT `bash`
# -------------------------------------------------------------------------
# macOS TCC is per-binary: a launchd-spawned `bash` is denied the read of a
# script under ~/Documents ("Operation not permitted"), and a launchd-spawned
# `git` the read of the repository there. The launcher is under ~/Documents AND
# it runs `git -C <repo> rev-parse` at startup, so a `bash <launcher>` entry
# point would install cleanly, report `loaded: yes`, and then silently do
# nothing — the pre-flight cannot catch it, because an `-x` test succeeds.
#
# The PROVEN shape in this repo is the checkout's OWN interpreter (see
# `com.tortoise.queue-reconcile.plist` and `com.tortoise.worktree-reaper.plist`,
# the latter recording the falsified `bash` control experiment verbatim). So the
# plist starts the >=3.12 interpreter this script's pre-flight resolved, on
# `tools/ci_check_repair_launchd.py`, which reads the launcher itself and hands
# it to `bash -s` on stdin; bash and the `git` it spawns inherit that access.
#
# The plist also carries an explicit HOME and a PATH whose first entry is the
# real `gh`'s directory, and the interpreter path is PINNED at install time — a
# `command -v` resolved at FIRE time could pick a different (or missing) binary,
# and `~/.pi/agent/shims` must not be relied on under launchd.
#
# USAGE
#   tools/install-ci-check-repair-schedule.sh             install (armed: --apply)
#   tools/install-ci-check-repair-schedule.sh --report-only
#                                                  install a job that MEASURES
#                                                  and reports but never repairs
#   tools/install-ci-check-repair-schedule.sh --dry-run   render the plist and
#                                                  print it; touch nothing
#   tools/install-ci-check-repair-schedule.sh --status    show install state
#   tools/install-ci-check-repair-schedule.sh --uninstall remove it
#   tools/install-ci-check-repair-schedule.sh --help
#
# NOTE ON `--dry-run`: it means NO SIDE EFFECTS, nothing else. It was briefly an
# alias for `--report-only` ("install, but inert"), which is the opposite of what
# every reader of the flag expects — a lane that ran it to inspect the plist got a
# loaded launchd job instead. The two are separate flags because they do two
# different things.
#
# Env overrides:
#   TORTOISE_REPO   repo root (default: this script's repo)
#   CI_REPAIR_INTERVAL  interval in SECONDS (default 1800 — one 30-minute
#                   window, matching the workflow's schedule)
#   CI_REPAIR_GH    the `gh` binary the installed job will use (default:
#                   `command -v gh`, preferring ~/bin/gh over the shim dir)
#   AGENTS_DIR      launchd install dir (default $HOME/Library/LaunchAgents);
#                   refused when it is not the login account's, because
#                   launchctl addresses the user domain BY UID and a throwaway
#                   HOME does not sandbox the install. Override with
#                   CI_REPAIR_ALLOW_NONSTANDARD_AGENTS_DIR=1.
#
# Exit codes: 0 = ok (or clean skip), 1 = failure, 2 = usage/refusal.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${TORTOISE_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LABEL="com.tortoise.ci-check-repair"
AGENTS_DIR="${AGENTS_DIR:-${HOME:?HOME is required}/Library/LaunchAgents}"
PLIST_PATH="$AGENTS_DIR/$LABEL.plist"
LAUNCHER="$REPO/tools/ci-check-repair-local.sh"
ENTRYPOINT="$REPO/tools/ci_check_repair_launchd.py"
# A LINKED WORKTREE IS EPHEMERAL — it is reaped once the lane's PR merges. A plist
# pinned to `.worktrees/<branch>/...` would install cleanly, run for a few hours,
# and then fail forever with `No such file or directory`. The schedule belongs to
# the MAIN checkout, so resolve it from the git common dir (the same source the
# interpreter fallback uses) and pin everything to that.
if [ "${CI_REPAIR_ALLOW_WORKTREE_INSTALL:-0}" != "1" ]; then
    _common="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    case "$_common" in
        */.git)
            _main="${_common%/.git}"
            if [ -d "$_main" ] && [ "$_main" != "$REPO" ]; then
                echo "NOTE: run from a linked worktree ($REPO);" >&2
                echo "      installing the schedule against the MAIN checkout ($_main)," >&2
                echo "      because the worktree is deleted when its PR merges." >&2
                if [ ! -f "$_main/tools/ci-check-repair-local.sh" ]; then
                    echo "ERROR: the main checkout has no tools/ci-check-repair-local.sh yet," >&2
                    echo "       so nothing can be installed from here: the schedule runs the" >&2
                    echo "       code on main, and refusing now is the point. Land this change," >&2
                    echo "       then re-run the installer from the main checkout. To install" >&2
                    echo "       from THIS worktree anyway (a short-lived experiment only)," >&2
                    echo "       set CI_REPAIR_ALLOW_WORKTREE_INSTALL=1." >&2
                    exit 2
                fi
                REPO="$_main"
                LAUNCHER="$REPO/tools/ci-check-repair-local.sh"
                ENTRYPOINT="$REPO/tools/ci_check_repair_launchd.py"
            fi
            ;;
    esac
fi
LOG_PATH="$HOME/.pi/agent/state/ci-check-repair.launchd.log"
INTERVAL="${CI_REPAIR_INTERVAL:-1800}"
# `gh` resolution: an explicit override wins; otherwise the REAL binary, not the
# fleet's `gh` shim. The shim is a symlink into `agent-infra/scripts/gh-shim/gh`
# (a script under ~/Documents), and macOS TCC denies a launchd-spawned process the
# read there — so installing a job whose PATH leads with the shim installs a job
# that fails on every fire. `command -v gh` DOES resolve to the shim in a fleet
# shell, which is why this prefers `$HOME/bin/gh` rather than trusting the lookup.
GH_BIN="${CI_REPAIR_GH:-}"
if [ -z "$GH_BIN" ]; then
    for _cand in "$HOME/bin/gh" "$(command -v gh || true)"; do
        [ -n "$_cand" ] && [ -x "$_cand" ] && { GH_BIN="$_cand"; break; }
    done
fi
# Set by preflight (which `--status` intentionally does not run); declared here
# so `--status` under `set -u` never trips on it.
PY_BIN=""

usage() { sed -n '2,/^# Exit codes:/p' "$0" | sed 's/^# \{0,1\}//'; }

_xml_escape() { # `&` FIRST, or the other replacements' output is re-escaped
    local s="$1"
    s="${s//&/&amp;}"; s="${s//</&lt;}"; s="${s//>/&gt;}"
    printf '%s' "$s"
}

_login_home() {
    local user; user="$(id -un)"
    if command -v dscl >/dev/null 2>&1; then
        dscl . -read "/Users/$user" NFSHomeDirectory 2>/dev/null | awk '{print $2}'
    else
        getent passwd "$user" 2>/dev/null | cut -d: -f6
    fi
}

require_standard_agents_dir() {
    local expected; expected="$(_login_home)/Library/LaunchAgents"
    [ "$AGENTS_DIR" = "$expected" ] && return 0
    [ "${CI_REPAIR_ALLOW_NONSTANDARD_AGENTS_DIR:-0}" = "1" ] && {
        echo "WARNING: AGENTS_DIR '$AGENTS_DIR' != '$expected' (override set)" >&2; return 0; }
    echo "ERROR: refusing non-standard AGENTS_DIR '$AGENTS_DIR' (launchctl acts on" >&2
    echo "       gui/$(id -u)/$LABEL regardless of \$HOME; expected '$expected')." >&2
    return 2
}

# Fail-closed: never install a job that can only fail. A scheduled job whose
# interpreter or `gh` is missing fails on every fire, forever, and the only
# signal is a log nobody reads.
preflight() {
    if [ ! -f "$LAUNCHER" ]; then
        echo "ERROR: refusing to install: launcher not found at $LAUNCHER" >&2; return 2
    fi
    if [ ! -f "$ENTRYPOINT" ]; then
        echo "ERROR: refusing to install: entry point not found at $ENTRYPOINT" >&2
        echo "       (the plist starts the checkout interpreter on it; without it the" >&2
        echo "        installed job can never run)." >&2
        return 2
    fi
    local py="$REPO/.venv/bin/python"
    if [ ! -x "$py" ]; then
        # A linked worktree has no `.venv`; the launcher falls back to the MAIN
        # worktree's venv, so the pre-flight must accept the same candidates or
        # it would refuse an install the job can actually run.
        local common; common="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
        case "$common" in */.git) py="${common%/.git}/.venv/bin/python" ;; esac
    fi
    [ -x "$py" ] || py="$(command -v python3 || true)"
    if [ -z "$py" ] || ! "$py" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,12) else 1)' 2>/dev/null; then
        echo "ERROR: refusing to install: no >=3.12 interpreter (tools/ci_check_repair.py" >&2
        echo "       refuses an older one, so the job would fail on every fire)." >&2
        return 2
    fi
    # PIN the interpreter's real path: the plist must not re-resolve it at fire
    # time, when PATH is launchd's own and a `python3` from a different install
    # (or none) could win. `cd`-then-pwd resolves the symlink-free directory.
    PY_BIN="$(cd "$(dirname "$py")" && printf '%s/%s' "$(pwd)" "$(basename "$py")")"
    if [ ! -x "$PY_BIN" ]; then
        echo "ERROR: refusing to install: cannot pin the interpreter at $PY_BIN" >&2
        return 2
    fi
    if [ -z "$GH_BIN" ] || [ ! -x "$GH_BIN" ]; then
        echo "ERROR: refusing to install: no usable 'gh' (current: '${GH_BIN:-<none>}')." >&2
        echo "       Without it every measure fails and the job is pure noise." >&2
        return 2
    fi
    case "$INTERVAL" in ''|*[!0-9]*) echo "ERROR: CI_REPAIR_INTERVAL must be a positive whole number" >&2; return 2 ;; esac
    [ "$INTERVAL" -ge 1 ] || { echo "ERROR: CI_REPAIR_INTERVAL must be >= 1" >&2; return 2; }
    JOB_PATH="$(dirname "$GH_BIN"):/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    case "$GH_BIN" in
        */shims/*)
            echo "WARNING: the resolved 'gh' is the fleet shim ($GH_BIN), which is a" >&2
            echo "         script under ~/Documents. macOS TCC may deny the launched job" >&2
            echo "         the read of it. Point CI_REPAIR_GH at the real binary." >&2
            ;;
    esac
    # The schedule can only cover what is on the DEFAULT BRANCH: `schedule:` is
    # read from main, so before this workflow lands the job fires every interval,
    # finds no run history, and reports "the workflow did NOT run" — true, but not
    # an outage. Warn once here rather than let the first notices read as breakage.
    if [ -n "$GH_BIN" ] && command -v git >/dev/null 2>&1; then
        local branch=""
        branch="$(git -C "$REPO" ls-tree --name-only -r origin/main \
            -- .github/workflows/ci-check-repair.yml 2>/dev/null || true)"
        if [ -z "$branch" ] && git -C "$REPO" rev-parse --verify -q origin/main >/dev/null 2>&1; then
            echo "WARNING: .github/workflows/ci-check-repair.yml is not on origin/main yet," >&2
            echo "         so the scheduled half cannot cover any window until it lands." >&2
            echo "         The local fallback will run every interval and report 'the" >&2
            echo "         ci-check-repair workflow did NOT run' — that is expected here," >&2
            echo "         not an outage." >&2
        fi
    fi
    return 0
}

render_plist() {
    # BOTH modes render as a VISIBLE argument, and the launcher accepts both:
    # `--apply` (armed) and `--dry-run` (report-only). The ABSENCE of an argument is
    # not "inert" — it is the launcher's ARMED default — which is how `--report-only`
    # once installed a job that closed and reopened PRs unattended. A mode that is
    # only ever implied cannot be reviewed in the plist (the bug this contract fix
    # closes: `--apply` was rendered for ARMED, and the launcher rejected the token).
    local mode="--apply"
    [ "${MODE_FLAG:-}" = "report-only" ] && mode="--dry-run"
    # QUOTED heredoc + explicit token substitution, the shape
    # tools/install-queue-reconcile-schedule.sh established: an unquoted heredoc
    # runs COMMAND SUBSTITUTION on the template, so a backtick inside a comment
    # (`` `gh` ``) executes. That is not hypothetical — the first cut of this
    # renderer had an unquoted heredoc and a comment mentioning `` `/bin/bash` ``,
    # which ran a command and emitted its output into the plist.
    # `patsub_replacement` (bash 5.2+) would also expand `&` in a replacement to
    # the matched text, which would corrupt the `&amp;` escapes.
    shopt -u patsub_replacement 2>/dev/null || true
    local out
    out="$(cat <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>@@LABEL@@</string>
  <!-- The checkout's own interpreter on the python entry point — NOT
       a bash entry point: macOS TCC denies a launchd-spawned bash the read of
       a script under ~/Documents and a launchd-spawned git the read of the
       repo there. See this script's header, and the two PROVEN agents in this
       repo (com.tortoise.queue-reconcile, com.tortoise.worktree-reaper). The
       interpreter is pinned at install time, and PATH must still carry the
       real gh's directory because launchd's own PATH is minimal. -->
  <key>ProgramArguments</key>
  <array>
    <string>@@PY@@</string>
    <string>@@ENTRY@@</string>
@@MODELINE@@
  </array>
  <key>WorkingDirectory</key><string>@@REPO@@</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>@@HOME@@</string>
    <key>PATH</key><string>@@PATH@@</string>
    <!-- The interpreter PINNED at install time. The plist's own interpreter runs
         the entry point, but the launcher would otherwise RE-RESOLVE its
         interpreter at fire time — and launchd's PATH yields /usr/bin/python3
         (3.9), which tools/ci_check_repair.py refuses. Passing the pin through
         makes the validated-at-install interpreter the one that actually runs. -->
    <key>CI_REPAIR_PYTHON</key><string>@@PY@@</string>
  </dict>
  <key>StartInterval</key><integer>@@INTERVAL@@</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>@@LOG@@</string>
  <key>StandardErrorPath</key><string>@@LOG@@</string>
</dict>
</plist>
PLIST
)"
    local modeline=""
    [ -n "$mode" ] && modeline="    <string>$mode</string>"
    out="${out//@@LABEL@@/$(_xml_escape "$LABEL")}"
    out="${out//@@PY@@/$(_xml_escape "$PY_BIN")}"
    out="${out//@@ENTRY@@/$(_xml_escape "$ENTRYPOINT")}"
    out="${out//@@MODELINE@@/$modeline}"
    out="${out//@@REPO@@/$(_xml_escape "$REPO")}"
    out="${out//@@HOME@@/$(_xml_escape "$HOME")}"
    out="${out//@@PATH@@/$(_xml_escape "$JOB_PATH")}"
    out="${out//@@INTERVAL@@/$INTERVAL}"
    out="${out//@@LOG@@/$(_xml_escape "$LOG_PATH")}"
    printf '%s\n' "$out"
}

job_loaded() { launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; }

status() {
    echo "=== $LABEL ==="
    echo "launcher: $LAUNCHER"
    echo "interval: ${INTERVAL}s   mode: ${MODE_FLAG:-armed (--apply)}"
    echo "log     : $LOG_PATH"
    if [ -f "$PLIST_PATH" ]; then
        echo "installed: $PLIST_PATH"
        plutil -lint "$PLIST_PATH" >/dev/null 2>&1 && echo "plist lint: OK"
        job_loaded && echo "loaded  : yes" || echo "loaded  : NO — re-run this script"
        # Read the INSTALLED job's own arguments, not this shell's environment:
        # the question is what launchd will run, and the answer can differ from
        # the checkout this command was typed in (a worktree, a moved repo).
        # `plutil -extract` is macOS 12+; fall back to a python read.
        local installed
        installed="$(plutil -extract ProgramArguments json -o - "$PLIST_PATH" 2>/dev/null \
            || python3 -c 'import plistlib,sys; print(plistlib.load(open(sys.argv[1],"rb"))["ProgramArguments"])' "$PLIST_PATH" 2>/dev/null || true)"
        [ -n "$installed" ] && echo "runs    : $installed"
        local iv
        iv="$(plutil -extract StartInterval raw -o - "$PLIST_PATH" 2>/dev/null || true)"
        [ -n "$iv" ] && echo "job interval: ${iv}s (this checkout says ${INTERVAL}s)"
    else
        echo "installed: NOT installed ($PLIST_PATH missing)"
    fi
    [ -x "$LAUNCHER" ] || echo "WARNING: launcher is not executable"
    [ -f "$ENTRYPOINT" ] || echo "WARNING: entry point is missing ($ENTRYPOINT)"
}

uninstall() {
    require_standard_agents_dir || return $?
    launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "removed $PLIST_PATH"
}

MODE_FLAG=""
PLAN_ONLY=0
ACTION=""
# TWO PASSES: every argument is validated FIRST, and only then is anything executed.
# Reading only `$1` meant `--report-only --dry-run` INSTALLED (the dry run was never
# seen); executing action flags inside the parse loop then traded that for the
# opposite hazard — `--dry-run --uninstall` ran the uninstall, deleting the installed
# schedule despite the documented "NO SIDE EFFECTS" contract. Parsing fully before
# acting closes both orders, because nothing can execute before the last argument is
# known, and an unknown argument can no longer hide behind an earlier action flag.
for _arg in "$@"; do
    case "$_arg" in
        --report-only) MODE_FLAG="report-only" ;;
        --dry-run) PLAN_ONLY=1 ;;
        --status) ACTION="status" ;;
        --uninstall) ACTION="uninstall" ;;
        --help|-h) ACTION="help" ;;
        *) echo "ERROR: unknown argument: $_arg" >&2; usage >&2; exit 2 ;;
    esac
done
case "$ACTION" in
    help) usage; exit 0 ;;
    status) status; exit $? ;;
    uninstall)
        # `--dry-run` promises no side effects, and removing an installed schedule is
        # the largest one available here. Refuse the combination rather than let word
        # order decide whether the schedule survives.
        if [ "$PLAN_ONLY" = 1 ]; then
            echo "ERROR: --dry-run and --uninstall are mutually exclusive (--dry-run" >&2
            echo "       promises no side effects; --uninstall removes the schedule)." >&2
            exit 2
        fi
        uninstall; exit $?
        ;;
esac

[ "$(uname -s)" = "Darwin" ] || { echo "ERROR: launchd only (macOS); on Linux add a cron line running $LAUNCHER" >&2; exit 2; }
require_standard_agents_dir || exit $?
preflight || exit $?

# `--dry-run` stops HERE: preflight has already verified every prerequisite, so
# the rendered plist is the real one, but nothing is written and nothing is
# bootstrapped. Always exits 0 on a render, so it can be piped into a plist
# parser; a missing prerequisite has already exited above.
if [ "$PLAN_ONLY" = 1 ]; then
    render_plist
    echo "# DRY RUN — nothing written, nothing loaded. Target would be: $PLIST_PATH" >&2
    exit 0
fi

chmod +x "$LAUNCHER" 2>/dev/null || true

mkdir -p "$AGENTS_DIR"
# launchd opens StandardOutPath/StandardErrorPath BEFORE it execs, so a missing log
# directory means the job cannot spawn AT ALL (not merely "no logs"). The
# launcher's own `mkdir -p` runs far too late to help.
mkdir -p "$(dirname "$LOG_PATH")"
tmp="$(mktemp)"
render_plist > "$tmp"
if [ -f "$PLIST_PATH" ] && cmp -s "$PLIST_PATH" "$tmp"; then
    rm -f "$tmp"
    if job_loaded; then
        echo "unchanged: $PLIST_PATH (loaded; no reload)"
    else
        launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH" \
            && echo "unchanged: $PLIST_PATH (was NOT loaded — bootstrapped now)" \
            || { echo "ERROR: launchctl bootstrap failed" >&2; exit 1; }
    fi
else
    mv "$tmp" "$PLIST_PATH"
    launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
    launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true
    if ! launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"; then
        echo "ERROR: launchctl bootstrap failed — see 'launchctl print gui/$(id -u)/$LABEL'" >&2
        exit 1
    fi
    echo "installed + loaded: $PLIST_PATH (interval ${INTERVAL}s, mode '${MODE_FLAG:-armed (--apply)}')"
fi
plutil -lint "$PLIST_PATH" >/dev/null 2>&1 || { echo "ERROR: rendered plist is invalid" >&2; exit 1; }
echo "Verify: $0 --status"
echo "Logs:   $LOG_PATH"
