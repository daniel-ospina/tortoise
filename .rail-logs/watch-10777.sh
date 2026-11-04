#!/usr/bin/env bash
# Watcher for PR #10777: wait until a `python-ci` run at the recorded head has
# actually EXERCISED the head, then re-invoke the MANDATED rail unchanged.
#
# WHY THIS EXISTS: on 2026-11-04 the whole repo was PR-BACKLOGGED
# (tools/ci_queue_health.py: queued=89, pr_running=5, pr_queued=81,
# oldest=16499s, 'Python CI' the largest share at 27 of 89). GitHub held this
# PR's `Python CI` run at status=pending with ZERO jobs for 50+ minutes, so
# scripts/admin-merge.sh correctly refused: "no run of the lane actually TESTED
# head". That refusal is a required-check gate and must NOT be bypassed.
#
# This script adds NO gate and skips NONE: it polls, then runs the same
# admin-merge.sh. If the head moves or the attestation goes stale the rail
# refuses on its own, safely.
set -uo pipefail

REPO="daniel-ospina/tortoise"
PR=10777
HEAD_SHA="a86248161c81d916ab7343a26c416818398e9815"
WORKTREE="/tmp/cell-t3/.worktrees/prd-10777"
LOG="$WORKTREE/.rail-logs/watcher-10777.log"
MAX_WAIT_S="${MAX_WAIT_S:-18000}"   # the rail's own ceiling (18064s), minus slack
SLEEP_S="${SLEEP_S:-120}"

mkdir -p "$(dirname "$LOG")"
log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"; }

log "watcher start: waiting for a python-ci run with jobs at $HEAD_SHA (max ${MAX_WAIT_S}s)"
waited=0
while [ "$waited" -lt "$MAX_WAIT_S" ]; do
    live=$(gh api "repos/$REPO/pulls/$PR" --jq .head.sha 2>/dev/null || true)
    if [ "$live" != "$HEAD_SHA" ]; then
        log "HEAD MOVED to $live — stopping; the attestation is bound to $HEAD_SHA and this lane must not merge a different tree"
        exit 3
    fi
    # newest python-ci run whose head_sha is ours
    run=$(gh api "repos/$REPO/actions/workflows/python-ci.yml/runs?per_page=40" \
            --jq "[.workflow_runs[] | select(.head_sha==\"$HEAD_SHA\")] | sort_by(.created_at) | last | \"\(.id) \(.status) \(.conclusion // \"-\")\"" 2>/dev/null || true)
    rid=${run%% *}
    rest=${run#* }
    status=${rest%% *}
    concl=${rest#* }
    jobs=0
    if [ -n "${rid:-}" ] && [ "$rid" != "$run" ]; then
        jobs=$(gh api "repos/$REPO/actions/runs/$rid/jobs" --jq '.total_count' 2>/dev/null || echo 0)
    fi
    log "poll: run=${rid:-none} status=${status:-?} conclusion=${concl:-?} jobs=${jobs:-?} waived=${waited}s"

    case "${status:-}" in
        completed)
            case "${concl:-}" in
                success)
                    log "python-ci COMPLETED/success at the recorded head — invoking the rail"
                    ( cd "$WORKTREE" && exec scripts/admin-merge.sh "$PR" ) >>"$WORKTREE/.rail-logs/admin-merge-10777-retry.log" 2>&1
                    rc=$?
                    log "rail exited rc=$rc (see admin-merge-10777-retry.log)"
                    exit "$rc"
                    ;;
                cancelled)
                    log "newest run was CANCELLED (superseded) — continuing to wait for a live one"
                    ;;
                *)
                    log "python-ci COMPLETED/${concl} — the rail decides what to do about it; invoking the rail"
                    ( cd "$WORKTREE" && exec scripts/admin-merge.sh "$PR" ) >>"$WORKTREE/.rail-logs/admin-merge-10777-retry.log" 2>&1
                    rc=$?
                    log "rail exited rc=$rc (see admin-merge-10777-retry.log)"
                    exit "$rc"
                    ;;
            esac
            ;;
    esac
    sleep "$SLEEP_S"
    waited=$((waited + SLEEP_S))
done

log "gave up after ${MAX_WAIT_S}s: no python-ci run ever exercised $HEAD_SHA (repo PR-BACKLOGGED). The PR is mergeable and its clean attestation is recorded at this head; it needs CI CAPACITY, not a code change."
exit 4
