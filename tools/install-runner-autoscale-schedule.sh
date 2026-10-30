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
#   AUTOSCALE_PYTHON           interpreter to pin (default: /usr/bin/python3, the checkout's
#                              .venv, then python3 — the first one that is >= 3.12)
#   AUTOSCALE_PYTHON_CANDIDATES  the whole candidate list, newline-separated — test seam
#   AUTOSCALE_REPO_ROOT        checkout the schedule runs from (default: this script's repo)
#   AUTOSCALE_ALLOW_WORKTREE=1  install from a linked worktree (default: refused — a worktree is
#                              reaped when its PR merges, and the timer would then fail forever)
#   SYSTEMD_UNIT_DIR           default /etc/systemd/system
#   AGENTS_DIR                 launchd dir (default $HOME/Library/LaunchAgents)
#   AUTOSCALE_LOG              log path (default <state dir>/runner-autoscale.job.log)
#   AUTOSCALE_INSTALL_DIR      where the RUNNABLE copy lives (default $HOME/.pi/agent/scripts)
#   AUTOSCALE_SKIP_PROBE=1     arm the schedule even though the guard cannot measure here
#                              (the install-time `--status` probe is what refuses that by default)
#   AUTOSCALE_SHA256           hasher for the drift check (default sha256sum, else shasum -a 256)
#   AUTOSCALE_PLATFORM         force one platform branch (systemd|cron|launchd) — test seam
#   AUTOSCALE_HAS_SYSTEMD      0|1 override for the systemd PROBE only — test seam (see below)
#   AUTOSCALE_HAS_CRON         0|1 override for the cron PROBE only — test seam (see below)
#   AUTOSCALE_CRON_DAEMON      0|1 answer for "is a cron daemon running?" — test seam, and the
#                              escape hatch for a box that runs cron in a way the probe cannot see
#                              (a foreground cron in a minimal container). Unset = detect from
#                              pgrep, then /proc, then ps; if NONE of those can look, the cron leg
#                              REFUSES rather than assuming the daemon is up.
#
# ⛔ AUTOSCALE_PLATFORM vs the two PROBE seams — they are NOT interchangeable, and the difference
# is testability, not convenience. `AUTOSCALE_PLATFORM` short-circuits `platform()` BEFORE the
# `case "$(uname -s)"` dispatch, so a suite that leans on it tests the branch it NAMES and nothing
# in the real `Linux)` arm. Measured in review of PR #9685: with the cron cases on that seam, two
# mutations of the Linux arm survived a fully green suite — deleting `elif has_cron` (a cron-only
# Linux box then resolves to `none` and REFUSES to install, losing its schedule forever) and
# reordering the probes (a systemd box silently swapped onto cron). Both are the "inert enforcer
# with a green suite" this file's header exists to refuse. Overriding the PROBES keeps the real
# dispatch under test — the `case` arm runs, only the two host facts are supplied.
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
# `--help` needs no checkout and must never be gated by the worktree/platform refusals below.
case "${1:-}" in -h|--help) usage; exit 0 ;; esac

# ── the checkout the schedule runs FROM ────────────────────────────────────────────────────────
# ⛔ A LINKED WORKTREE IS EPHEMERAL: it is deleted when its PR merges, so a timer pinned to
# .worktrees/<branch>/… installs cleanly, runs for a few hours, then fails forever with
# "No such file or directory" — a schedule that reports installed and can never fire.
# ⛔ THE COMPARISON IS PHYSICAL. `REPO` comes from bash's LOGICAL `pwd`, which preserves the path
# as it was typed (symlinked components and all), while `git rev-parse --git-common-dir` answers
# with the PHYSICAL one. Compared as raw strings, a MAIN checkout reached through any symlinked
# component — macOS `/tmp` -> `/private/tmp`, a symlinked home or deploy dir — is indistinguishable
# from a linked worktree, so the installer REFUSED it with "run from a linked worktree" while it
# was in fact exactly what it asked for, and nothing was ever installed on such a box. The refusal
# also names a remedy ("land this change, then re-run from the main checkout") that was already
# followed. Measured: one real `git init` checkout is accepted at /tmp/symtest/real/repo and
# refused at /tmp/symtest/link/real/repo. `pwd -P` also drops a trailing slash, which the same
# string compare treated as a second worktree.
_physical() { ( cd "${1:-.}" 2>/dev/null && pwd -P ) || printf '%s' "${1:-}"; }
_REPO_PHYS="$(_physical "$REPO")"
[ -n "$_REPO_PHYS" ] || _REPO_PHYS="$REPO"
_common="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
case "$_common" in
  */.git)
    _main="$(_physical "${_common%/.git}")"
    if [ -d "$_main" ] && [ "$_main" != "$_REPO_PHYS" ] && [ "${AUTOSCALE_ALLOW_WORKTREE:-0}" != "1" ]; then
      echo "ERROR: run from a linked worktree ($REPO)." >&2
      echo "       The schedule runs the code on MAIN, because a worktree is reaped with its PR." >&2
      echo "       Land this change, then re-run from the main checkout;" >&2
      echo "       AUTOSCALE_ALLOW_WORKTREE=1 installs from here anyway (short-lived experiments)." >&2
      exit 2
    fi ;;
esac

# ⛔ A STABLE INTERPRETER, CHOSEN BY THE FLOOR — NOT BY `-x`. The tool is stdlib-only on purpose,
# so a SYSTEM python is the right pin (a repo .venv can be reaped with a worktree). But choosing by
# existence alone picked `/usr/bin/python3` on EVERY box, and on macOS that is the Command Line
# Tools' 3.9 — so the floor check below refused on a machine with a perfectly good 3.12 one line
# further down the chain, and the `.venv` branch was unreachable dead code (round 9, finding 3).
# Resolution order: AUTOSCALE_PYTHON (explicit; the ONLY candidate when set), /usr/bin/python3,
# the checkout's .venv, then python3 on PATH — the FIRST one that satisfies the >= 3.12 floor wins.
FLOOR_CHECK='import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'
PYTHON=""
_TRIED=""
if [ -n "${AUTOSCALE_PYTHON:-}" ]; then
  _CANDIDATES="$AUTOSCALE_PYTHON"
elif [ -n "${AUTOSCALE_PYTHON_CANDIDATES:-}" ]; then
  # test seam: the full candidate list, in order (one per line)
  _CANDIDATES="$AUTOSCALE_PYTHON_CANDIDATES"
else
  _CANDIDATES="/usr/bin/python3
$REPO/.venv/bin/python
$(command -v python3 || true)"
fi
while IFS= read -r _cand; do
  [ -n "$_cand" ] || continue
  if [ -x "$_cand" ]; then
    if "$_cand" -c "$FLOOR_CHECK" 2>/dev/null; then PYTHON="$_cand"; break; fi
    _TRIED="${_TRIED} ${_cand}"
  fi
done <<EOF
$_CANDIDATES
EOF
[ -n "$PYTHON" ] || { echo "REFUSED: no interpreter >= 3.12 — tools/runner_autoscale.py requires it." >&2
                    echo "         too old:${_TRIED:- (none found)}" >&2
                    echo "         point AUTOSCALE_PYTHON at a >= 3.12 interpreter." >&2
                    exit 2; }
TOOL="$REPO/tools/runner_autoscale.py"
[ -f "$TOOL" ] || { echo "ERROR: $TOOL is missing — the schedule would run nothing" >&2; exit 2; }
INSTALL_DIR="${AUTOSCALE_INSTALL_DIR:-$HOME/.pi/agent/scripts}"
INSTALLED="$INSTALL_DIR/runner_autoscale.py"

# ⛔ RESOLVED ONCE, HERE — NOT INSIDE A RENDER. The heredocs below are UNQUOTED (they expand the
# pinned paths), so ANY backtick in them — including one inside a comment — runs as a command
# substitution at render time. Measured: a backticked "gh" in a comment executed the shim, the
# shim found no credential and launched the interactive device flow, and the installer hung for
# its whole timeout having written NOTHING. So: NO backtick in an UNQUOTED heredoc BODY in this
# file — plain code-fencing quotes read the same to a human and cannot execute. (Backticks in the
# prose OUTSIDE the heredocs are not expanded, which is why the rest of this file may use them.)
# The gh directory is resolved from real file candidates (no execution) and pinned.
#
# PINNED BY SUITE, not by this comment:
# tests/test_install_runner_autoscale_schedule.py::
#   test_no_unquoted_heredoc_body_in_this_installer_can_run_a_command_substitution
# scans every unquoted body below and REDS on a backtick. It was re-broken once (the systemd
# service body's OS-label rationale) and the suite was green while every render printed two
# "command not found" lines and wrote a unit whose comment had lost the label it names.
GH_BIN=""
GH_DIR=""
for _c in /usr/bin/gh /usr/local/bin/gh /opt/homebrew/bin/gh "$HOME/bin/gh"; do
  [ -x "$_c" ] && { GH_BIN="$_c"; GH_DIR="$(dirname "$_c")"; break; }
done
if [ -z "$GH_BIN" ]; then
  GH_BIN="$(command -v gh 2>/dev/null || true)"
  GH_DIR="$(dirname "${GH_BIN:-/usr/bin/gh}")"
fi

STATE_DIR="${RUNNER_AUTOSCALE_STATE_DIR:-$HOME/.pi/agent/state}"
# ── the OS-label mode the TIMER passes to the tool (and through it, to the provisioner) ─────
# ⛔ THE FILL LEG REGISTERS NEW RUNNERS THROUGH `install-ci-runner-x64.sh --count N --fallback
# auto`, and `_run` there passes no `env=` — so the child sees only what THIS unit inherits.
# systemd does NOT inherit the installing shell's environment, so a unit that pins no
# `CI_RUNNER_OS_LABEL` gives the provisioner `off` (the installer's default), and every runner the
# autoscaler ADDS joins without `ubuntu-24.04`. Measured shape: the pool drifts back to zero answers
# one fill at a time, `--status` stays green until the last runner that still carries the label is
# replaced, and then every PR is BLOCKED again (#6897's incident, silently rebuilt).
#
# ⛔ THE FILL LEG IS NOT THE ONLY READER, which is why the pins below are UNCONDITIONAL. The timer
# runs `runner_autoscale.py`, whose REPAIR leg re-registers an ORPHANED runner through its own
# `resolve_labels` → `_os_labels`, reading `CI_RUNNER_OS_LABEL` from ITS OWN environment
# (`tools/runner_autoscale.py`, `resolve_labels`). That leg needs no provisioner — `register()` runs
# the install's own `config.sh` — so on a provisioner-less box the REPAIR leg is the ONLY capacity
# path the box has, and gating the variable on `$PROVISIONER` left it deriving `off` while the
# operator's `on` was accepted and silently dropped. An ORPHANED runner is exactly the one with no
# labels to preserve (PR #10275 review, finding 1).
#
# The value is CAPTURED AT INSTALL TIME from the operator's own environment and defaults to `off`:
# rendering `auto` here would make this script flip the #6897 pin by itself on any 24.04 box, which
# is an owner decision and not this installer's to take. Opt in explicitly:
#   CI_RUNNER_OS_LABEL=on tools/install-runner-autoscale-schedule.sh ...
OS_LABEL_MODE="${CI_RUNNER_OS_LABEL:-off}"
case "$OS_LABEL_MODE" in
  on|off|auto) : ;;
  *) echo "REFUSED: CI_RUNNER_OS_LABEL must be off|on|auto (got '$OS_LABEL_MODE')" >&2; exit 1 ;;
esac
LOG="${AUTOSCALE_LOG:-$STATE_DIR/runner-autoscale.job.log}"
ARGV_TAIL=""
[ -n "${RUNNER_AUTOSCALE_REPO:-}" ] && ARGV_TAIL=" --repo $RUNNER_AUTOSCALE_REPO"

# ⛔ THE FILL LEG'S PROVISIONER, IF THIS BOX HAS ONE, IS PINNED FOR THE TIMER TOO. The tool
# resolves `install-ci-runner-x64.sh` itself, but a timer's cwd and PATH are not a shell's — a
# schedule that can repair runners and never ADD one is the "installed but inert" half this
# installer exists to avoid. Emitted ONLY when the file is really there: the tool treats
# RUNNER_AUTOSCALE_PROVISIONER as authoritative, so a stale path would turn a working candidate
# list into a refusal.
PROVISIONER=""
for _p in "$REPO/tools/install-ci-runner-x64.sh" "$HOME/.pi/agent/scripts/install-ci-runner-x64.sh"; do
  [ -f "$_p" ] && { PROVISIONER="$_p"; break; }
done
# ⛔ CRON GETS `HOME` EXPLICITLY, for the same measured reason the systemd unit pins it: cron
# sets HOME to the account's PASSWD home, and gh reads its credential from
# $HOME/.config/gh/hosts.yml — which on these boxes is not that home. A cron-only host with PATH
# but no HOME fired, found no credential, and exited 2 every interval (round 10, F2's cron half).
CRON_ENV="HOME=$HOME PATH=$GH_DIR:/usr/local/bin:/usr/bin:/bin"
[ -n "$PROVISIONER" ] && CRON_ENV="$CRON_ENV RUNNER_AUTOSCALE_PROVISIONER=$PROVISIONER"
# ⛔ THE OS-LABEL PIN IS UNCONDITIONAL — it is NOT a provisioner input. `runner_autoscale.py`
# reads it itself on the ORPHANED re-registration path (REPAIR), which needs no provisioner, so
# gating it here left the one leg a provisioner-less box still runs deriving `off` (see the header).
CRON_ENV="$CRON_ENV CI_RUNNER_OS_LABEL=$OS_LABEL_MODE"

# ── platform detection, fail-closed ───────────────────────────────────────────────────────────
has_systemd() {
  if [ -n "${AUTOSCALE_HAS_SYSTEMD:-}" ]; then [ "$AUTOSCALE_HAS_SYSTEMD" = 1 ]; return; fi
  [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1
}
has_cron() {
  if [ -n "${AUTOSCALE_HAS_CRON:-}" ]; then [ "$AUTOSCALE_HAS_CRON" = 1 ]; return; fi
  command -v crontab >/dev/null 2>&1 && [ -n "$(command -v cron 2>/dev/null || command -v crond 2>/dev/null || ls /usr/sbin/cron 2>/dev/null)" ]
}

# Is a cron DAEMON live right now? `has_cron` answers a different question — WHICH scheduler this box
# has, from binary presence — and the two are not the same: `crontab` plus an installed `cron` with
# the daemon STOPPED is the normal shape of a container/VM image without an init, and it is exactly
# the shape that reaches this leg (`has_systemd` requires `/run/systemd/system`). The systemd leg
# refuses on `is-active != active` and the launchd leg on a missing label; without this check the
# cron leg installs cleanly, prints "installed: cron entry -> …" and exits 0 on a box where the
# entry never fires — the inert enforcer this file's header refuses (cycle 2 review, P2).
#
# ⛔ "WE COULD NOT LOOK" IS NOT "IT IS RUNNING". The first cut of this returned 0 (running)
# whenever `pgrep` was absent, justified by "the install-time probe still checks the schedule" —
# which is FALSE: `runner_autoscale.py` has no cron reference at all, it measures cores/memory/`gh`
# and cannot see a daemon. And `pgrep` is `procps`, which `cron` does NOT depend on, so
# `apt-get install cron` in a slim image yields `crontab` + a stopped daemon + no `pgrep` — exactly
# the box this check exists for, where the gate would have been inert (second review, P1).
#
# So: `pgrep`, then `/proc` read with bash builtins (no procps needed), then `ps`; and if NONE of
# them can look, that is unverifiable — refuse, per this file's own NEVER SILENT rule. An explicit
# `AUTOSCALE_CRON_DAEMON` seam (0/1) exists for the hermetic suite and for an operator who knows
# their box (a foreground cron in a minimal container, say).
#
# Prints a one-line REASON and returns 1 when the daemon is not verifiably running; returns 0 when
# it is. `pgrep -x` matches the exact process name, so it cannot be fooled by a substring hit
# (`cron-line-helper`).
cron_daemon_problem() {
  case "${AUTOSCALE_CRON_DAEMON:-}" in
    1) return 0 ;;
    0) echo "no cron daemon is running (AUTOSCALE_CRON_DAEMON=0)"; return 1 ;;
  esac
  if command -v pgrep >/dev/null 2>&1; then
    if pgrep -x cron >/dev/null 2>&1 || pgrep -x crond >/dev/null 2>&1; then return 0; fi
    echo "no cron/crond process is running"; return 1
  fi
  local d name
  if [ -d "${AUTOSCALE_PROC_DIR:-/proc}" ]; then
    for d in "${AUTOSCALE_PROC_DIR:-/proc}"/[0-9]*; do
      [ -r "$d/comm" ] || continue
      read -r name < "$d/comm" 2>/dev/null || continue
      case "$name" in cron|crond) return 0 ;; esac
    done
    echo "no cron/crond process is running (read from /proc; pgrep is not installed)"; return 1
  fi
  local procs
  if procs="$(ps -eo comm= 2>/dev/null)"; then
    case " $procs " in *" cron "*|*" crond "*) return 0 ;; esac
    echo "no cron/crond process is running (read from ps)"; return 1
  fi
  echo "cannot determine whether a cron daemon is running (no pgrep, no /proc, no ps)"
  return 1
}

# ── reading the user's crontab: A FAILED READ MUST NEVER LOOK LIKE AN EMPTY ONE ──────────────
# ⛔ THE DESTRUCTIVE SHAPE THIS REPLACES. Both the install and the uninstall leg fed
# `crontab -l 2>/dev/null || true` straight into `crontab -`. A read that FAILS for any reason
# other than "no crontab yet" (a permissions problem, a locked/held crontab, `crontab` on a box
# with no spool directory) therefore produced an EMPTY string, which is indistinguishable from "the
# user has no crontab" — and the write then REPLACED the whole thing: a user's own MAILTO and
# nightly jobs gone, with `installed: cron entry -> …` and exit 0 printed over the loss. The
# comment claiming "the `|| true` is on the READS only; the final write stays fatal" was exactly
# backwards: a failed read is what makes the write fatal to the user's data.
#
# So the read happens ONCE, its stderr is kept, and the ONLY tolerated failure is the benign "no
# crontab for <user>" (the normal state of a box that has never had one). Anything else REFUSES
# before any write. Prints the current crontab on stdout; returns 2 (after naming the reason) when
# it cannot be read.
crontab_current() {
  local err out rc
  err="$(mktemp "${TMPDIR:-/tmp}/autoscale-crontab.XXXXXX")" || {
    echo "REFUSED: cannot create a temp file to read the crontab safely" >&2; return 2; }
  out="$(crontab -l 2>"$err")"; rc=$?
  if [ "$rc" -ne 0 ] && ! grep -qi 'no crontab for' "$err"; then
    echo "REFUSED: cannot read the crontab, so it will NOT be rewritten: $(tail -1 "$err" 2>/dev/null)" >&2
    rm -f "$err"; return 2
  fi
  rm -f "$err"
  printf '%s' "$out"
}

platform() {
  # A TEST SEAM, like AUTOSCALE_PYTHON: `has_systemd` needs /run/systemd/system, which no
  # hermetic sandbox can create, so the systemd leg would otherwise be untestable on the macOS box
  # where the fleet's own lanes run its suite.
  if [ -n "${AUTOSCALE_PLATFORM:-}" ]; then echo "$AUTOSCALE_PLATFORM"; return; fi
  case "$(uname -s)" in
    Darwin) echo launchd ;;
    Linux)
      if has_systemd; then echo systemd
      elif has_cron; then echo cron
      else echo none; fi ;;
    *) echo unsupported ;;
  esac
}

# ── a sha256 hasher, on BOTH platforms ────────────────────────────────────────────────────────
# ⛔ GNU `sha256sum` does NOT exist on macOS (its form is `shasum -a 256`). The plain substitution
# yielded "" on both sides, so the comparison was "" = "" and the DRIFT line could never print: the
# `--status` feature this file's header advertises was silently inert on the very platform the
# launchd leg runs on. Resolved once, used by both `--status` and the install.
_sha256() {
  if [ -n "${AUTOSCALE_SHA256:-}" ]; then "$AUTOSCALE_SHA256" "$1" 2>/dev/null | cut -d' ' -f1; return 0; fi
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" 2>/dev/null | cut -d' ' -f1; return 0; fi
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" 2>/dev/null | cut -d' ' -f1; return 0; fi
  return 1
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
$( [ -n "$PROVISIONER" ] && printf 'Environment=RUNNER_AUTOSCALE_PROVISIONER=%s\n' "$PROVISIONER" )
# ⛔ AND THE OS-LABEL MODE WITH IT, UNCONDITIONALLY. Both legs read it: the FILL leg (through the
# provisioner) and the ORPHANED re-registration on the REPAIR leg, which reads it from the tool's
# own environment and needs no provisioner at all. Pinned only when a provisioner resolved, the
# FILL leg registers runners WITHOUT ubuntu-24.04 and an ORPHANED repair re-registers one without
# it — the same erasure through two doors, with --status green at every step meanwhile.
Environment=CI_RUNNER_OS_LABEL=$OS_LABEL_MODE
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
  # ⛔ PATH AND `gh` ARE PINNED HERE FOR THE SAME MEASURED REASON THE SYSTEMD UNIT PINS THEM. A
  # launchd agent does NOT inherit the user's shell PATH: it gets
  # /usr/bin:/bin:/usr/sbin:/sbin, where a Homebrew `gh` (/opt/homebrew/bin/gh) does not live. The
  # tool then resolves `gh` to "", `read_pool` raises "gh is not on PATH", and every fire exits 2
  # — installed, reports "installed", and inert forever (round 9, finding 2). PATH is widened to
  # the same candidate list the unit uses, and RUNNER_AUTOSCALE_GH pins the resolved binary
  # because the tool has no fallback for it.
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
    <key>PATH</key><string>$GH_DIR:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>RUNNER_AUTOSCALE_GH</key><string>${GH_BIN:-gh}</string>
$( [ -n "$PROVISIONER" ] && printf '    <key>RUNNER_AUTOSCALE_PROVISIONER</key><string>%s</string>\n' "$PROVISIONER" )
    <key>CI_RUNNER_OS_LABEL</key><string>$OS_LABEL_MODE</string>
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
  # ⛔ CEIL BY SUBTRACTING, NOT BY ADDING: `(INTERVAL + 59) / 60` OVERFLOWS. Bash arithmetic is
  # signed 64-bit, so for INTERVAL >= 2**63 - 59 the addition wraps NEGATIVE, the `-lt 1` clamp
  # then pins it to 1, and the entry renders `*/1 * * * *` — a once-a-MINUTE loop for a
  # 19-digit interval, i.e. the tightest loop cron can express, with a NOTE claiming the opposite
  # (found in cycle 2). `(N - 1) / 60 + 1` is the same ceiling and cannot overflow for N >= 1.
  local minutes=$(( (INTERVAL - 1) / 60 + 1 )); [ "$minutes" -lt 1 ] && minutes=1
  local sched="" eff="" d
  # ⛔ `*/N` IS NOT A PERIOD UNLESS N DIVIDES THE FIELD'S RANGE, and cron has no other way to say
  # "every N". `*/45` on the minute field fires at :00 and :45 only, so a 45-minute interval really
  # ran 45 then 15 minutes apart; `*/58` ran 58 then 2 (measured by --dry-run). That is an average
  # cadence ~2x the request — the "a tighter loop only hammers the API" failure the INTERVAL floor
  # above exists to prevent, and it is INVISIBLE in the rendered line.
  #
  # The same trap sits in the HOUR field: `*/5 * * *` fires at 00:00 and 05:00, then 00:00 again —
  # gaps of 5h and 19h — so hours are restricted to the divisors of 24 for the same reason.
  #
  # So: pick the CLOSEST PERIOD AT OR ABOVE the request (never tighter than asked), and SAY SO on
  # stderr when cron cannot express it exactly. Firing less often than configured is a visible,
  # bounded loss of freshness; firing more often is the unbounded API hammering this guards.
  if [ "$minutes" -le 30 ]; then
    for d in 1 2 3 4 5 6 10 12 15 20 30; do
      if [ "$d" -ge "$minutes" ]; then sched="*/$d * * * *"; eff=$d; break; fi
    done
  fi
  if [ -z "$sched" ]; then
    local h=$(( (minutes - 1) / 60 + 1 )); [ "$h" -lt 1 ] && h=1
    for d in 1 2 3 4 6 8 12; do
      if [ "$d" -ge "$h" ]; then sched="0 */$d * * *"; eff=$(( d * 60 )); break; fi
    done
    if [ -z "$sched" ]; then
      # ⛔ THERE IS NO FAITHFUL CRON EXPRESSION LONGER THAN 12h. The hour field's `*/N` is only a
      # period when N divides 24, so the largest real period is 12h; the old fallback here was
      # `0 0 * * *` — DAILY, i.e. SHORTER than a request of anything over 24h, which is exactly the
      # over-firing this function refuses, and it printed a NOTE claiming the opposite (found by
      # VGATE on this very change). Cron's day-of-month `*/N` is not a period either (it restarts
      # at every month boundary). So REFUSE — fail closed, like the >= 60s floor — rather than
      # silently promise a rate the entry does not deliver.
      echo "REFUSED: cron cannot schedule a ${minutes}-minute period without firing MORE often" >&2
      echo "         than requested (cron periods must divide a day; 12h is the longest)." >&2
      echo "         Use an interval <= 12h, or a systemd/launchd host." >&2
      return 2
    fi
  fi
  if [ "$(( eff * 60 ))" -ne "$INTERVAL" ]; then
    echo "NOTE: cron cannot express ${INTERVAL}s — the entry fires every ${eff} min," \
         "never more often than requested (round 11 review: '*/45' fired 45 then 15)." >&2
  fi
  printf '%s %s; cd %s && %s %s%s >> %s 2>&1 # %s\n' \
    "$sched" "$CRON_ENV" "$REPO" "$PYTHON" "$INSTALLED" "$ARGV_TAIL" "$LOG" "$LABEL-cron"
}

# ── the probe: run the guard the way the SCHEDULE will run it ─────────────────────────────────
# ⛔ THE SCHEDULE'S ENVIRONMENT, NOT THE OPERATOR'S SHELL'S. The unit/plist pin HOME, PATH,
# RUNNER_AUTOSCALE_GH and RUNNER_AUTOSCALE_PROVISIONER (each for a measured reason — see the
# systemd render), and the timer serves `$RUNNER_AUTOSCALE_REPO` via `$ARGV_TAIL`. A probe that
# does not reproduce those is a green light for a schedule that will behave differently: it would
# measure this shell's repo and pool against a timer that serves another, and it would pass on a
# box that is inert BECAUSE of a pin (round 10 review, P3).
probe() {
  local target="$1" py="${2:-$PYTHON}"
  local -a penv=(
    "HOME=$HOME"
    "PATH=$GH_DIR:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    "RUNNER_AUTOSCALE_STATE_DIR=$STATE_DIR"
  )
  [ -n "${GH_BIN:-}" ] && penv+=("RUNNER_AUTOSCALE_GH=$GH_BIN")
  [ -n "$PROVISIONER" ] && penv+=("RUNNER_AUTOSCALE_PROVISIONER=$PROVISIONER")
  # The probe reproduces the schedule's environment, and the schedule carries the OS-label mode for
  # BOTH legs (FILL through the provisioner, ORPHANED re-registration through this same tool) — so
  # it is rendered unconditionally here too, or the probe attests a timer that repairs differently.
  penv+=("CI_RUNNER_OS_LABEL=$OS_LABEL_MODE")
  # shellcheck disable=SC2086  # $ARGV_TAIL is a deliberate word-split argv tail (" --repo o/n")
  env "${penv[@]}" "$py" "$target" $ARGV_TAIL --json --status 2>&1
}

# ── the interpreter the INSTALLED schedule pins ────────────────────────────────────────────────
# ⛔ THE SCHEDULE'S INTERPRETER, NOT THIS SHELL'S. The installer RENDERS `$PYTHON` into the unit
# (`ExecStart=`), the plist (`ProgramArguments[0]`) and the cron entry, chosen by the >= 3.12 floor
# AT INSTALL TIME. `--status` used to re-resolve it and probe with whatever THIS shell found, so
# the two could disagree and the probe still passed: install pins `$REPO/.venv/bin/python` and the
# `.venv` is later reaped, and every fire executes a missing binary while `--status` prints
# `probe: OK` and exits 0 — the exact "installed but will never fire" state this function exists
# to catch. Reads the pin back out of the installed artifact; empty when there is none to read
# (cron, whose entry is prose rather than a structured unit, and the not-installed case).
pinned_interpreter() {
  local svc="$UNIT_DIR/$LABEL.service" plist="$AGENTS_DIR/$PLIST_LABEL.plist" py=""
  if [ -f "$svc" ]; then
    py="$(sed -n 's/^ExecStart=\([^ ]*\).*/\1/p' "$svc" 2>/dev/null | head -1)"
    [ -n "$py" ] && { printf '%s' "$py"; return 0; }
  fi
  if [ -f "$plist" ]; then
    py="$(awk '/<key>ProgramArguments<\/key>/{seen=1; next} seen && /<string>/{gsub(/^[^<]*<string>|<\/string>[^<]*$/, ""); print; exit}' "$plist" 2>/dev/null)"
    [ -n "$py" ] && { printf '%s' "$py"; return 0; }
  fi
  return 0
}

# ── status: "installed" is not "will fire" ────────────────────────────────────────────────────
status() {
  # `armed` = some scheduler entry exists for this schedule. A non-zero answer for "installed but
  # inert" and a ZERO answer for "nothing is armed" would be backwards: the caller gates on this
  # exit code, and "nothing armed" is the strictly worse state.
  local armed=1
  if [ -f "$INSTALLED" ]; then
    local run_sum repo_sum
    run_sum="$(_sha256 "$INSTALLED" || true)"
    repo_sum="$(_sha256 "$TOOL" || true)"
    echo "run-copy: $INSTALLED sha256=${run_sum:-unavailable}"
    if [ -n "$run_sum" ] && [ "$run_sum" != "$repo_sum" ]; then
      echo "DRIFT: the repo copy ($TOOL) differs from the RUNNING copy — re-run this installer"
    fi
    if [ -z "$run_sum" ] || [ -z "$repo_sum" ]; then
      # ⛔ NEVER SILENT. Two unavailable readings must not look like "no drift": that is exactly
      # the empty-string comparison this check was fixed from.
      echo "WARNING: no sha256 hasher (sha256sum/shasum) — drift cannot be verified on this box" >&2
    fi
  else
    echo "run-copy: MISSING ($INSTALLED) — the schedule would run nothing"
    # ⛔ THE COMPLEMENT OF THE COMMENT ABOVE: `armed` must be cleared here too. The timer/plist/
    # cron entry can be present while its TARGET is gone (a reaped worktree, a half-copy, a
    # `--uninstall` that removed only the copy), and this is one of the two states this file
    # names in prose as "the schedule would run nothing". Leaving `armed=1` reported that state as
    # exit 0 to the caller that gates on it (round 11 review, P1).
    armed=0
  fi
  # ⛔ "LOADED" IS NOT "RUNNABLE". One READ-ONLY pass of the RUNNING copy, with the interpreter and
  # environment the schedule itself uses. This is the only check that can tell an armed schedule
  # from an inert one: `systemctl is-active` and `launchctl list` both prove the job is registered,
  # and neither says the guard can MEASURE anything on this box (round 10, I2). Exit 2 is the
  # tool's own UNMEASURABLE verdict (no `gh`, no memory reading, unreadable pool) — reported here
  # as a FAILED probe, and returned as exit 2 so a script can act on it. AUTOSCALE_SKIP_PROBE=1
  # skips it (the install's probe seam, same default).
  # ⛔ AND IT MUST BE THE INTERPRETER THE SCHEDULE PINS, or the probe measures a box state the
  # timer does not have. A pinned interpreter that is GONE is reported as the inert state it is,
  # never silently replaced by whatever this shell resolves.
  local probe_py="$PYTHON" pinned
  pinned="$(pinned_interpreter)"
  if [ -n "$pinned" ]; then
    echo "interpreter: pinned $pinned (this shell resolved $PYTHON)"
    if [ ! -x "$pinned" ]; then
      echo "WARNING: the pinned interpreter $pinned is MISSING — every fire would fail" >&2
      armed=0
    fi
    # An EXPLICIT `AUTOSCALE_PYTHON` is the operator's own decision (and the probe seam the
    # suite drives), so it is what the probe runs. Otherwise probe with the pin, because that is
    # the binary the timer will actually execute.
    [ -n "${AUTOSCALE_PYTHON:-}" ] || probe_py="$pinned"
  fi
  local probe_rc=0
  if [ -f "$INSTALLED" ] && [ "${AUTOSCALE_SKIP_PROBE:-0}" != "1" ]; then
    local probe_out
    probe_out="$(probe "$INSTALLED" "$probe_py")"
    probe_rc=$?
    if [ "$probe_rc" = "0" ]; then
      echo "probe: OK (exit 0) — $(printf '%s' "$probe_out" | tail -1 | cut -c1-200)"
    else
      echo "probe: FAILED (exit $probe_rc) — this schedule would fire and do NOTHING:" >&2
      echo "       $(printf '%s' "$probe_out" | tail -1 | cut -c1-300)" >&2
    fi
  fi
  case "$(platform)" in
    systemd)
      local svc="$UNIT_DIR/$LABEL.service" tmr="$UNIT_DIR/$LABEL.timer"
      if [ -f "$tmr" ]; then
        echo "installed: $tmr"
        local en; en="$(systemctl is-enabled "$LABEL.timer" 2>&1)"
        echo "enabled: $en"
        local state; state="$(systemctl is-active "$LABEL.timer" 2>&1)"
        echo "active:  $state"
        systemctl list-timers "$LABEL.timer" --all --no-pager 2>/dev/null | sed -n '1,2p'
        # ⛔ ENABLED IS A THIRD INERT STATE. A disabled timer still starts and runs until the next
        # boot, so `is-active` alone reads it as armed while the schedule is already doomed — and
        # the install path now enables on every run, so the two must agree (cycle 3 review, P2).
        [ "$en" = "enabled" ] || { echo "WARNING: $LABEL.timer is '$en', not enabled — it will not survive a reboot"; armed=0; }
        # ⛔ A STOPPED TIMER IS INERT TOO (`systemctl stop`, a mask, a failed unit). The install
        # path already fails hard on `is-active`, so `--status` reporting 0 for `active: inactive`
        # was the same false PASS one branch over (cycle 2; pre-existing, same family).
        [ "$state" = "active" ] || { echo "WARNING: $LABEL.timer is '$state', not active — it will not fire"; armed=0; }
        # ⛔ A TIMER WITH NOTHING TO ACTIVATE IS THE OTHER INERT STATE, and a WARNING on stdout is
        # not a verdict: `armed` stayed 1 and `--status` returned 0 on a schedule that fires into
        # a missing unit (round 11 review, P1).
        [ -f "$svc" ] || { echo "WARNING: $svc is missing — the timer activates nothing"; armed=0; }
      else
        echo "NOT installed (no $tmr)"; armed=0
      fi ;;
    cron)
      cur="$(crontab_current 2>/dev/null)" || { echo "NOT verifiable (cannot read the crontab)"; armed=0; cur=""; }
      if printf '%s\n' "$cur" | grep -F "$LABEL-cron" >/dev/null; then
        echo "installed: cron entry present"; printf '%s\n' "$cur" | grep -F "$LABEL-cron"
        # ⛔ INSTALLED IS NOT ARMED: with the daemon down the entry never fires, and an
        # unverifiable reading (no pgrep, no /proc, no ps) must not read as "armed" either
        # (see `cron_daemon_problem`).
        cron_daemon_problem >/dev/null || { echo "WARNING: $(cron_daemon_problem) — the entry will not fire"; armed=0; }
      else echo "NOT installed (no $LABEL-cron in crontab)"; armed=0; fi ;;
    launchd)
      local plist="$AGENTS_DIR/$PLIST_LABEL.plist"
      if [ -f "$plist" ]; then
        echo "installed: $plist"
        launchctl list 2>/dev/null | grep -F "$PLIST_LABEL" || { echo "loaded: NO (run without --status to load)"; armed=0; }
      else echo "NOT installed ($plist missing)"; armed=0; fi ;;
    none) echo "NOT installed AND CANNOT BE: no systemd and no cron on this box" >&2; return 2 ;;
    *) echo "unsupported platform: $(uname -s)" >&2; return 2 ;;
  esac
  # A schedule that is installed but cannot measure is a FAILURE of --status, not a note: the
  # caller (a human, or a bring-up script) must be able to gate on it. So is "nothing is armed".
  #
  # ⛔ ANY non-zero probe is a failure, not just the tool's own exit 2. The line above has already
  # printed "this schedule would fire and do NOTHING" — a crash (exit 1: uncaught exception, a
  # truncated interpreter, a panic) is the most inert outcome there is, and propagating only `2`
  # let it read green (round 11 review, P1). `AUTOSCALE_SKIP_PROBE=1` is the explicit opt-out and
  # leaves probe_rc at 0, so it is unaffected.
  [ "$probe_rc" = "0" ] || return 2
  [ "$armed" = "0" ] && return 2
  return 0
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
      cron)    echo "# cron entry"
               cron_daemon_problem >/dev/null || echo "# WARNING: $(cron_daemon_problem) — an install here would be REFUSED"
               cron_line || exit 2 ;;
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
        # ⛔ THE READ IS VALIDATED FIRST (`crontab_current`), or a failed read writes an EMPTY
        # crontab over the user's own entries — see that function's comment.
        cur="$(crontab_current)"; rc=$?
        [ "$rc" = 0 ] || exit "$rc"
        { if [ -n "$cur" ]; then printf '%s\n' "$cur" | grep -vF "$LABEL-cron" || true; fi; } | crontab - \
          || { echo "ERROR: could not rewrite crontab" >&2; exit 1; }
        echo "removed: $LABEL-cron" ;;
      launchd)
        launchctl bootout "gui/$(id -u)/$PLIST_LABEL" 2>/dev/null || true
        rm -f "$AGENTS_DIR/$PLIST_LABEL.plist" || exit 1
        # ⛔ "REMOVED THE PLIST" IS NOT "STOPPED THE AGENT". If `bootout` failed ("Operation not
        # permitted" from outside the GUI session, a disabled login item) the plist is gone while
        # the LOADED agent keeps firing every INTERVAL, and `--status` afterwards reports NOT
        # installed because it only checks the plist — nothing would ever surface it. The install
        # path already has the "wrote != loaded" verification this path lacked.
        if launchctl list 2>/dev/null | grep -F "$PLIST_LABEL" >/dev/null; then
          echo "ERROR: $PLIST_LABEL is STILL LOADED (bootout failed) — it will keep firing" >&2
          echo "       retry from a GUI session, or: launchctl bootout gui/$(id -u)/$PLIST_LABEL" >&2
          exit 1
        fi
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
# ⛔ THE SAME RULE FOR A PERIOD CRON CANNOT DELIVER. `cron_line` refuses an interval whose real
# cadence would be SHORTER than requested (anything above 12h), and the header promises a refusal
# writes NOTHING — which was a half-truth while the runnable copy was refreshed before the refusal
# (VGATE round 2). Resolve the cron entry HERE, before the probe and before any write, so the
# promise holds on every refusal path.
CRON_LINE=""
# ⛔ THE CRONTAB IS READ HERE, ONCE, BEFORE ANY WRITE — for the same reason the period gate above
# is: both install branches WRITE THE WHOLE CRONTAB BACK, so an unvalidated read that failed
# silently would replace the user's own entries with just our line. Reading it here keeps the
# "a refusal writes NOTHING" promise instead of refusing after the runnable copy was refreshed.
CRONTAB_CUR=""
[ "$PLATFORM" = cron ] && {
  CRON_LINE="$(cron_line)" || exit 2
  CRONTAB_CUR="$(crontab_current)" || exit 2
}
# ⛔ AND THE SAME RULE FOR A CRON ENTRY ON A BOX WITH NO CRON DAEMON. The systemd leg refuses on
# `is-active != active` and the launchd leg on a missing label, but the cron leg checked only that
# the BINARIES exist. `crontab` present with the daemon stopped is the normal shape of a container
# or VM image without an init — and it is exactly the shape that REACHES this leg, because
# `has_systemd` requires `/run/systemd/system`. Installed there, the entry never fires while the
# installer prints "installed" and exits 0: the inert enforcer the header refuses (cycle 2 review,
# P2). Checked HERE, with the other pre-write gates, so the "a refusal writes NOTHING" promise
# holds — the same placement rule the period gate above follows.
[ "$PLATFORM" = cron ] && { cron_daemon_problem >/dev/null || {
  echo "REFUSED: an entry here would never fire — $(cron_daemon_problem)." >&2
  echo "         Start the daemon (systemctl start cron/crond), or run tools/runner_autoscale.py from the fleet's controller loop." >&2
  exit 2
}; }
# ⛔ "INSTALLED" MUST MEAN "IT WILL DO SOMETHING". One READ-ONLY pass with the pinned interpreter,
# BEFORE anything is written. Exit 2 is the tool's own UNMEASURABLE verdict (no `gh` on PATH, no
# memory reading, unreadable pool) — a schedule armed in that state fires every INTERVAL into a
# refusal forever, which is the inert enforcer this file's header exists to refuse. `--status`
# alone cannot settle it either: it only proves the unit/plist is LOADED. The probe's only write is
# the tool's own run log (which it tolerates being unwritable); no schedule state exists yet, so a
# refusal here leaves the box as it was. AUTOSCALE_SKIP_PROBE=1 arms it anyway.
if [ "${AUTOSCALE_SKIP_PROBE:-0}" != "1" ]; then
  probe_out="$(probe "$TOOL")"
  probe_rc=$?
  # ⛔ ANY NON-ZERO PROBE IS A REFUSAL, not just the tool's own exit 2. Exit 2 is the tool's
  # UNMEASURABLE verdict, but a CRASH (exit 1: uncaught exception, a truncated interpreter, a
  # panic) is the most inert outcome there is, and propagating only `2` armed the schedule and
  # printed "installed" on a box where `--status` immediately reports `probe: FAILED (exit 1)` and
  # exits 2 — install and status disagreeing about the same box, in the direction the sibling
  # `status()` check already closed with `[ "$probe_rc" = "0" ] || return 2` (cycle 2 review, P2).
  # `AUTOSCALE_SKIP_PROBE=1` is the explicit opt-out and never reaches here.
  if [ "$probe_rc" != "0" ]; then
    echo "REFUSED: the guard cannot MEASURE on this box, so a schedule here would fire and do nothing." >&2
    echo "         probe said: $(printf '%s' "$probe_out" | tail -1)" >&2
    echo "         fix that reading (gh on PATH, memory readable), or AUTOSCALE_SKIP_PROBE=1 to arm it anyway." >&2
    exit 2
  fi
fi
mkdir -p "$STATE_DIR" "$INSTALL_DIR" || exit 1
install -m 0755 "$TOOL" "$INSTALLED" || { echo "ERROR: could not install the runnable copy" >&2; exit 1; }
echo "run-copy: $INSTALLED (sha256=$(_sha256 "$INSTALLED" || echo unavailable))"
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
    fi
    # ⛔ ENABLE RUNS ON EVERY INSTALL, NOT ONLY WHEN THE UNITS CHANGED. `systemctl enable` is
    # idempotent, and a re-run whose unit files are byte-identical (i.e. every run after the first)
    # skipped it — so on a box where the timer was disabled out of band (`systemctl disable --now`
    # to pause the guard, a preset reset) the installer printed "installed … (active)" and exited 0
    # without ever enabling it, and the schedule was silently gone at the next reboot. Same class
    # as the launchd leg's "UNCHANGED IS NOT LOADED", fixed in this PR one branch over.
    sudo -n systemctl enable "$LABEL.timer" || exit 1
    sudo -n systemctl start "$LABEL.timer" || exit 1
    # VERIFY the timer will actually fire — "enabled" alone is not a schedule.
    state="$(systemctl is-active "$LABEL.timer" 2>&1)"
    [ "$state" = "active" ] || { echo "ERROR: $LABEL.timer is '$state', not active — it will not fire" >&2; exit 1; }
    echo "installed: $UNIT_DIR/$LABEL.{service,timer} (active, every ${INTERVAL}s, changed=$changed)"
    systemctl list-timers "$LABEL.timer" --no-pager 2>/dev/null | sed -n '1,2p' ;;
  cron)
    line="$CRON_LINE"
    # The read was VALIDATED in the pre-flight gate (`CRONTAB_CUR`), so this never writes over a
    # crontab it could not read.
    cur="$CRONTAB_CUR"
    if printf '%s\n' "$cur" | grep -F "$LABEL-cron" >/dev/null; then
      existing="$(printf '%s\n' "$cur" | grep -F "$LABEL-cron")"
      if [ "$existing" = "$line" ]; then echo "installed (unchanged): cron entry present"; exit 0; fi
      { printf '%s\n' "$cur" | grep -vF "$LABEL-cron" || true; echo "$line"; } | crontab - \
        || exit 1
    else
      { [ -n "$cur" ] && printf '%s\n' "$cur"; echo "$line"; } | crontab - || exit 1
    fi
    echo "installed: cron entry -> $line" ;;
  launchd)
    mkdir -p "$AGENTS_DIR" || exit 1
    plist="$AGENTS_DIR/$PLIST_LABEL.plist"
    want="$(render_plist)"
    if [ "$want" = "$(cat "$plist" 2>/dev/null || true)" ]; then
      echo "installed (unchanged): $plist"
      # ⛔ "UNCHANGED" IS NOT "LOADED". An agent unloaded out of band (a reboot that dropped the
      # GUI session, a `launchctl bootout`, a login-item cleanup) left the plist byte-identical, so
      # this branch skipped the bootstrap and the load check below exited 1 — on EVERY re-run —
      # while printing "run without --status to load", which is precisely the invocation that just
      # failed. The installer's own promise is that re-running converges; the systemd leg
      # (`systemctl start`) and the cron leg (rewrite) both do (round 11 review, P2).
      # ⛔ `grep -q` IS WRONG HERE, AND `set -o pipefail` MAKES IT A FALSE "NOT LOADED". `-q` exits
      # at the FIRST match, so a still-writing `launchctl list` takes SIGPIPE and the pipeline
      # reports 141 — which `!` reads as "the agent is not loaded" even when it is. A real
      # `launchctl list` on a busy Mac emits far more than one line, so this is not a rare shape:
      # every healthy re-run would bootstrap an already-loaded agent, the bootstrap would fail
      # ("Bootstrap failed: 5"), and the installer would exit 1 with the opposite of the truth
      # (cycle 2, P1). The reproduction SIZE is host-dependent (measured on the analogous crontab
      # read below: 85 KB did not reproduce, 2.2 MB did), so the fix is the SHAPE — read to EOF —
      # and never a size budget. `-F ... >/dev/null` reads to EOF, so there is no SIGPIPE — the
      # same shape the final verification below already uses.
      if ! launchctl list 2>/dev/null | grep -F "$PLIST_LABEL" >/dev/null; then
        launchctl bootstrap "gui/$(id -u)" "$plist" \
          || { echo "ERROR: $PLIST_LABEL is not loaded and could not be bootstrapped" >&2; exit 1; }
        echo "re-armed: $PLIST_LABEL was not loaded; bootstrapped from the unchanged plist"
      fi
    else
      printf '%s\n' "$want" > "$plist" || exit 1
      plutil -lint "$plist" >/dev/null 2>&1 || { echo "ERROR: rendered plist does not lint" >&2; exit 1; }
      launchctl bootout "gui/$(id -u)/$PLIST_LABEL" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$plist" || exit 1
      echo "installed: $plist"
    fi
    # ⛔ THE LOAD CHECK IS THE ONLY THING BETWEEN "WROTE A PLIST" AND "A JOB THAT WILL RUN".
    # A `launchctl bootstrap` that exits 0 is not proof the label is registered — a GUI session
    # that is not the caller's, a disabled login item, or a `bootout` racing the bootstrap all
    # leave the plist on disk and nothing loaded. Deleting this check makes the installer report
    # success for an agent that never fires (the inert-enforcer shape this file exists to
    # refuse), which is why `test_launchd_install_fails_when_the_agent_is_not_loaded` pins it.
    # `grep -F ... >/dev/null` (NOT `-q`) so the reader is still consuming when the writer
    # finishes: `-q` exits at the first match, `launchctl list` takes SIGPIPE, and `pipefail`
    # then reports 141 — a FALSE "not loaded" on a real Mac.
    launchctl list 2>/dev/null | grep -F "$PLIST_LABEL" >/dev/null \
      || { echo "ERROR: $PLIST_LABEL is not loaded" >&2; exit 1; } ;;
  *)
    echo "REFUSED: no systemd and no cron on this box — a schedule here would install and never fire." >&2
    echo "         Install one, or run tools/runner_autoscale.py from the fleet's controller loop." >&2
    exit 2 ;;
esac
