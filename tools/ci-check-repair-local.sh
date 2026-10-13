#!/usr/bin/env bash
# ci-check-repair-local.sh — the LOCAL FALLBACK for the `ci-check-repair`
# workflow (#8375 item 1).
#
# WHY THIS EXISTS. The repair for "no required check at this PR head" runs free
# on GitHub-hosted runners (`.github/workflows/ci-check-repair.yml`). But the
# repair needs two things GitHub cannot always give it:
#
#   1. a working Actions control plane — if GitHub is degraded, the scheduled
#      workflow simply does not run, and a stalled PR waits on nothing;
#   2. a credential that can CREATE a workflow run. The workflow uses the
#      automatic `GITHUB_TOKEN`, and an event caused by that token does not
#      create a workflow run (GitHub's documented exception is
#      `workflow_dispatch`/`repository_dispatch`). So a PR whose required context
#      has NO run at the head — the #8254 bot-authored shape, and the
#      Actions-outage shape — is REPORTED by the workflow and has to be repaired
#      here, where the credential is a user token whose events DO trigger runs.
#
# ONE IMPLEMENTATION, NOT TWO. This script carries no repair logic of its own: it
# invokes `tools/ci_check_repair.py`, the exact file the workflow invokes, with
# the same `--apply`. What this file adds is the DECISION to run and the
# notification — the `ci_runner_gate.sh` shape (GitHub-preferred, local fallback,
# notify on transition), re-used rather than re-invented.
#
# THE COVERAGE SIGNAL IS THE ARTIFACT, NOT A HEALTH PAGE. The decision is "did
# the ci-check-repair WORKFLOW succeed in the last window?", read from
# `gh run list`. A failed run (the tool exits non-zero when a repair needs a
# user credential) does NOT count as coverage — that failure IS the
# GitHub->local transition. This subsumes a third-party status check: if GitHub
# is degraded the workflow cannot have succeeded, so "unavailable" and "did not
# deliver" are the same question here, and the second one cannot be wrong.
#
# NOTHING IS MERGED HERE. The tool repairs and reports; it never merges and never
# moves a head. The merge rail stays local, where the evidence lives.
#
# Slack is retired and deliberately not used. The two transports are the
# orchestrator inbox (durable, append-only, read by the fleet) and the cmux
# orchestrator lane (immediate, best-effort) — the same two ci_runner_gate.sh
# uses.
#
# ⛔ BASH 3.2 SAFE ON PURPOSE: macOS ships bash 3.2 as /bin/bash and the launchd
# plist starts /bin/bash, which cannot parse a `"` inside a single-quoted string
# inside `$( )`, nor a `local arr=(...)` compound assignment.
#
# USAGE
#   ci-check-repair-local.sh            # decide + repair + notify on a transition
#   ci-check-repair-local.sh --force    # run locally regardless of GitHub coverage
#   ci-check-repair-local.sh --dry-run  # report only; run the tool WITHOUT --apply
#   ci-check-repair-local.sh --status   # print the recorded state, exit 0
#
# EXIT: 0 ok (repaired, or GitHub coverage is current) · 1 repair still
#       unresolved · 2 could not measure (nothing was changed)
#
# Env overrides:
#   TORTOISE_REPO             repo root (default: this script's repo)
#   CI_REPAIR_PYTHON          interpreter (default: <repo>/.venv/bin/python, else
#                             the main worktree's venv, else `python3`)
#   CI_REPAIR_GH              `gh` binary (default: `command -v gh`)
#   CI_REPAIR_WINDOW_MINUTES  how far back a successful workflow run counts as
#                             coverage (default 35 — one 30-minute schedule tick
#                             plus slack for GitHub's best-effort scheduling)
#   CI_REPAIR_IN_FLIGHT_GRACE_MINUTES
#                             how long an unfinished workflow run still counts as
#                             "GitHub is working right now" (default 30 — the
#                             workflow's own timeout-minutes is 15, so a run that
#                             has not finished in twice that is stalled, not busy)
#   CI_REPAIR_ORCH_WS         orchestrator cmux workspace (default below)
#   CI_REPAIR_STATE_DIR       where the state/log/inbox live (default ~/.pi/agent/state)
set -uo pipefail

# `CI_REPAIR_SCRIPT_DIR` is how the launchd path gets this right. The job runs
# this file as `bash -s` (see tools/ci_check_repair_launchd.py), so there is no
# script argument and `BASH_SOURCE[0]` is UNSET — and with `set -u` that is an
# error, not an empty string. The old form `$(dirname "${BASH_SOURCE[0]}")`
# therefore expanded to the CURRENT DIRECTORY, which under the plist is the repo
# root, making `$SCRIPT_DIR/..` the repo's PARENT and `$TOOL` a path that does not
# exist: the job installed, reported `loaded: yes`, and exited 2 on every fire.
# The python entry point passes its own resolved directory, so the fallback below
# (`:-` guards the unset variable) is only used when this file is run directly.
SCRIPT_DIR="${CI_REPAIR_SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-}")" 2>/dev/null && pwd)}"
[ -n "$SCRIPT_DIR" ] || SCRIPT_DIR="$PWD"
REPO="${TORTOISE_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"
TOOL="$REPO/tools/ci_check_repair.py"
WORKFLOW_FILE="ci-check-repair.yml"
STATE_DIR="${CI_REPAIR_STATE_DIR:-$HOME/.pi/agent/state}"
STATE_FILE="$STATE_DIR/ci-check-repair.state"
OUT_FILE="$STATE_DIR/ci-check-repair.out"
LOG_FILE="$STATE_DIR/ci-check-repair.log"
INBOX="$STATE_DIR/orchestrator-inbox.log"
WINDOW_MINUTES="${CI_REPAIR_WINDOW_MINUTES:-35}"
REPO_SLUG="${CI_REPAIR_REPO_SLUG:-daniel-ospina/tortoise}"
# The ORCHESTRATOR lane (never dispatch work here) — the same workspace
# ci_runner_gate.sh notifies.
ORCH_WS="${CI_REPAIR_ORCH_WS:-C2C97E96-0CBC-4DFE-BC91-FEF44F09B5BD}"

# Interpreter resolution, in order: an explicit override; this checkout's venv;
# the MAIN worktree's venv; then `python3` on PATH. The main-worktree step is not
# decoration — the fleet runs lanes from linked worktrees, which have no `.venv`
# of their own, so without it the fallback would demand an env var in every lane.
# `tools/ci_check_repair.py` refuses anything older than 3.12 (#5128).
COMMON_DIR="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
MAIN_ROOT=""
case "$COMMON_DIR" in */.git) MAIN_ROOT="${COMMON_DIR%/.git}" ;; esac
PYTHON_BIN="${CI_REPAIR_PYTHON:-}"
for _root in "$REPO" "$MAIN_ROOT"; do
    [ -n "$PYTHON_BIN" ] && break
    [ -n "$_root" ] || continue
    [ -x "$_root/.venv/bin/python" ] && PYTHON_BIN="$_root/.venv/bin/python"
done
[ -n "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3 || true)"
# `gh` resolution: an explicit override wins; otherwise prefer the REAL binary
# over the fleet's `gh` shim. The shim is a symlink into
# `agent-infra/scripts/gh-shim/gh` — i.e. a SCRIPT UNDER ~/Documents — and macOS
# TCC denies a launchd-spawned process the read there, so a job whose `gh` is the
# shim fails on every fire. `~/.pi/agent/shims` is also what `command -v` finds
# first in a fleet shell, which is why the order matters rather than the lookup.
GH_BIN="${CI_REPAIR_GH:-}"
if [ -z "$GH_BIN" ]; then
    for _cand in "$HOME/bin/gh" "$(command -v gh || true)"; do
        [ -n "$_cand" ] && [ -x "$_cand" ] && { GH_BIN="$_cand"; break; }
    done
fi

DRY=0; FORCE=0; STATUS_ONLY=0
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        --force) FORCE=1 ;;
        --status) STATUS_ONLY=1 ;;
        *) echo "usage: $0 [--dry-run|--force|--status]" >&2; exit 2 ;;
    esac
done

mkdir -p "$STATE_DIR"
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG_FILE"; }

# ── notify ─────────────────────────────────────────────────────────────────
# Two transports, neither needing a credential we do not already have. `cmux` is
# BEST EFFORT: AGENTS.md forbids a bare `cmux send`, and a message that is not
# delivered must not fail the repair (the durable inbox record stands on its
# own).
notify() { # $1 = text
    printf '[%s] [ci-check-repair] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$1" >> "$INBOX"
    log "notified -> orchestrator-inbox.log: $1"
    if command -v cmux >/dev/null 2>&1 && [ -n "${ORCH_WS:-}" ]; then
        if cmux agent message "$ORCH_WS" --from ci-check-repair "$1" >/dev/null 2>&1; then
            log "notified -> cmux orchestrator lane"
        else
            log "cmux notify unavailable (inbox record stands)"
        fi
    fi
    return 0
}

# ── how did the WORKFLOW do in the window? ─────────────────────────────────
# FOUR outcomes, read from the artifact (`gh run list`), and they are NOT the same
# thing. Conflating them is how a ROUTINE handoff gets narrated as an outage, and
# a channel that cries outage routinely is a channel that gets muted — taking the
# one transition that mattered with it:
#   covered            a scheduled/dispatch run SUCCEEDED in the window -> GitHub
#                      handled everything; the local box stays out of the way.
#   in-flight          the newest run in the window has not finished yet, and it
#                      started recently. GitHub is working RIGHT NOW, so the local
#                      box must not act either: the workflow may be about to issue
#                      a `rerun` for a PR, and the local `close`+`reopen` would
#                      cancel it through that PR's `concurrency:` group. The two
#                      actors share no lock — `concurrency:` on the workflow is a
#                      GitHub-side mutex that launchd neither sees nor respects —
#                      so "is the other actor mid-repair?" can only be answered by
#                      not acting. A run still unfinished long past the workflow's
#                      15-minute timeout is NOT in-flight (it is stalled), so a
#                      zombie run cannot silently stop the fallback for ever.
#   ran-not-succeeded  a run finished and did not succeed. That is the workflow's
#                      NORMAL signal: it exits 1 precisely when a PR needs a repair
#                      only a user credential can perform, and the local box is
#                      that actor. NOT an outage.
#   absent             no run at all in the window -> GitHub degraded, or the
#                      schedule itself rotted (GitHub documents scheduled runs as
#                      best-effort and public-repo schedules auto-disable after 60
#                      days of inactivity). This is the REAL GitHub->local
#                      transition.
#   unreadable         the run history could not be read (the workflow is not on
#                      main yet, or gh failed) -> treated as no coverage.
# A run that is not `covered` still means NO COVERAGE (the local box must act, or
# has just decided to wait); the state only changes how the transition is REPORTED.
# The coverage DECISION is not reimplemented here. It lives in the tool
# (`--workflow-state`, pure and unit-tested) so that the question "was the window
# discharged?" has exactly one implementation and cannot drift from the five
# states the tool knows. This shell only moves bytes and reads back the verdict.
WF_STATE="unknown"
IN_FLIGHT_GRACE_MINUTES="${CI_REPAIR_IN_FLIGHT_GRACE_MINUTES:-30}"
workflow_covered() {
    local runs wf rc
    runs="$("$GH_BIN" run list --repo "$REPO_SLUG" --workflow="$WORKFLOW_FILE" \
        --limit 30 --json conclusion,createdAt,event,status 2>/dev/null)" || {
        WF_STATE="unreadable"
        log "workflow $WORKFLOW_FILE not readable (not on main yet, or gh failed) -> no coverage"
        return 1
    }
    wf="$(printf '%s' "$runs" | "$PYTHON_BIN" "$TOOL" --workflow-state \
        --window-minutes "$WINDOW_MINUTES" \
        --in-flight-grace-minutes "$IN_FLIGHT_GRACE_MINUTES" 2>/dev/null)"
    rc=$?
    WF_STATE="${wf:-unreadable}"
    [ "$rc" = 0 ]
}

# ── run the ONE implementation ─────────────────────────────────────────────
run_tool() {
    if [ "$DRY" = 1 ]; then
        log "running: $PYTHON_BIN $TOOL --json (report-only)"
        "$PYTHON_BIN" "$TOOL" --json > "$OUT_FILE" 2>> "$LOG_FILE"
    else
        log "running: $PYTHON_BIN $TOOL --apply --json"
        "$PYTHON_BIN" "$TOOL" --apply --json > "$OUT_FILE" 2>> "$LOG_FILE"
    fi
    local rc=$?
    cat "$OUT_FILE" >> "$LOG_FILE"
    return $rc
}

needing_repair_count() {
    local n
    # The count is read from the artifact the tool just wrote; `?` (not 0) when
    # it cannot be read, so an unreadable report never prints as "nothing wrong".
    n="$("$PYTHON_BIN" -c "import json,sys; print(json.load(sys.stdin)['needing_repair'])" \
        < "$OUT_FILE" 2>/dev/null)"
    [ -n "$n" ] || n="?"
    printf '%s' "$n"
}

# ── main ───────────────────────────────────────────────────────────────────
if [ "$STATUS_ONLY" = 1 ]; then
    echo "state file : $STATE_FILE"
    if [ -f "$STATE_FILE" ]; then cat "$STATE_FILE"; else echo "(no state recorded yet)"; fi
    if workflow_covered; then
        echo "workflow coverage (last ${WINDOW_MINUTES}m): yes ($WF_STATE)"
    else
        echo "workflow coverage (last ${WINDOW_MINUTES}m): NO ($WF_STATE) — the local fallback runs"
    fi
    exit 0
fi

if [ ! -f "$TOOL" ]; then
    log "FATAL: tool not found at $TOOL"
    echo "ERROR: tool not found at $TOOL" >&2
    exit 2
fi
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
    log "FATAL: no usable interpreter (looked for $REPO/.venv/bin/python and python3)"
    echo "ERROR: no usable interpreter; set CI_REPAIR_PYTHON" >&2
    exit 2
fi
if [ -z "$GH_BIN" ] || [ ! -x "$GH_BIN" ]; then
    log "FATAL: no usable gh binary"
    echo "ERROR: no usable gh; set CI_REPAIR_GH" >&2
    exit 2
fi
# The tool carries an inline >=3.12 guard (#5128). Checking here too turns a
# refused interpreter into a clean "could not measure" (exit 2) rather than a
# per-PR unresolved report that would send a lane hunting the wrong problem.
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    log "FATAL: $PYTHON_BIN is older than 3.12"
    echo "ERROR: $PYTHON_BIN is older than 3.12 (tools/ci_check_repair.py refuses it)." >&2
    echo "       Set CI_REPAIR_PYTHON (e.g. $REPO/.venv/bin/python)." >&2
    exit 2
fi

reason=""
if [ "$FORCE" = 1 ]; then
    reason="forced (--force)"
elif ! workflow_covered; then
    case "$WF_STATE" in
        absent)
            reason="the ci-check-repair workflow did NOT run in the last ${WINDOW_MINUTES}m (GitHub degraded, or the schedule itself rotted) -> the local fallback covers this window" ;;
        unreadable)
            reason="the ci-check-repair workflow's run history is unreadable (not on main yet, or gh failed) -> the local fallback covers this window" ;;
        *)
            reason="the ci-check-repair workflow RAN in the last ${WINDOW_MINUTES}m and did not succeed — its normal signal, a PR needs a repair only a user credential can perform -> the local fallback acts" ;;
    esac
fi
if [ -z "$reason" ]; then
    log "GitHub covered the window ($WF_STATE); local fallback idle"
    exit 0
fi

log "LOCAL RUN: $reason"
run_tool
rc=$?
mode_word="apply"; [ "$DRY" = 1 ] && mode_word="report-only"
needing="$(needing_repair_count)"
last="$(cat "$STATE_FILE" 2>/dev/null || echo '')"
# The signature carries the needing_repair COUNT as well as the exit code: a
# repair that completes successfully (exit 0) but leaves the same PRs needing
# attention must still read as a new state, or the one notice that matters would
# be suppressed by an earlier exit-0 notification.
signature="local:$rc:$needing"
if [ "$signature" != "$last" ]; then
    if [ "$rc" = 0 ]; then
        notify "ci-check-repair ran LOCALLY in ${mode_word} mode (${reason}). PRs needing repair: ${needing}. The local fallback ran the same tools/ci_check_repair.py the workflow runs, exit 0."
    elif [ "$rc" = 1 ]; then
        notify "ci-check-repair RAN LOCALLY and is STILL UNRESOLVED in ${mode_word} mode (${reason}). PRs needing repair: ${needing}. Some PR needs a repair even the local credential could not complete; see $OUT_FILE."
    else
        notify "ci-check-repair COULD NOT MEASURE locally (${reason}), exit=$rc. Nothing was changed. See $LOG_FILE."
    fi
    printf '%s\n' "$signature" > "$STATE_FILE"
else
    log "no transition (signature unchanged: $signature)"
fi
printf 'exit=%s mode=%s reason=%s needing_repair=%s\n' "$rc" "$mode_word" "$reason" "$needing" \
    >> "$LOG_FILE"
exit "$rc"
