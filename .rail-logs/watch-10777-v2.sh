#!/usr/bin/env bash
# Watcher for PR #10777 (v2): wait until a `Python CI` run at the recorded head has
# actually EXERCISED the head, then re-invoke the MANDATED rail unchanged.
#
# WHY: on 2026-11-04 the repo was PR-BACKLOGGED (tools/ci_queue_health.py:
# queued=89, pr_running=5, pr_queued=81, oldest=16499s, 'Python CI' the largest
# share at 27 of 89). GitHub left this PR's `Python CI` runs at status=pending
# with ZERO jobs, so scripts/admin-merge.sh correctly refuses: "no run of the
# lane actually TESTED head". That is a required-check gate and is NOT bypassed.
#
# v2 FIX: v1 discovered the run via the workflow-runs LIST endpoint, which does
# not return unplanned (`pending`) runs — so v1 logged run=null and could never
# have fired. This version reads the commit-scoped run list and matches on NAME.
#
# This script adds NO gate and skips NONE. If the head moves, or the recorded
# attestation goes stale, the rail refuses on its own, safely.
set -uo pipefail

REPO="daniel-ospina/tortoise"
PR=10777
HEAD_SHA="012a8df5c60b65184d25d8e54f64ff078cf92e8f"
WORKTREE="/tmp/cell-t3/.worktrees/prd-10777"
LOGDIR="$WORKTREE/.rail-logs"
LOG="$LOGDIR/watcher-10777.log"
MAX_WAIT_S="${MAX_WAIT_S:-18000}"
SLEEP_S="${SLEEP_S:-180}"

mkdir -p "$LOGDIR"
log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"; }

rail() {  # invoke the SAME mandated gate, log its exit
    ( cd "$WORKTREE" && exec scripts/admin-merge.sh "$PR" ) >>"$LOGDIR/admin-merge-10777-retry.log" 2>&1
    local rc=$?
    log "rail exited rc=$rc (see admin-merge-10777-retry.log)"
    return "$rc"
}

log "watcher v2 start: waiting for a 'Python CI' run with jobs at $HEAD_SHA (max ${MAX_WAIT_S}s)"
waited=0
while [ "$waited" -lt "$MAX_WAIT_S" ]; do
    live=$(gh api "repos/$REPO/pulls/$PR" --jq .head.sha 2>/dev/null || true)
    if [ -n "$live" ] && [ "$live" != "$HEAD_SHA" ]; then
        log "HEAD MOVED to $live — stopping; the attestation is bound to $HEAD_SHA and this lane must not merge a different tree"
        exit 3
    fi

    # commit-scoped list: returns unplanned (pending) runs too, unlike the workflow list
    rows=$(gh run list --repo "$REPO" --commit "$HEAD_SHA" --limit 40 \
             --json databaseId,name,status,conclusion 2>/dev/null || echo '[]')
    line=$(printf '%s' "$rows" | python3 -c '
import json,sys
try: rows=json.load(sys.stdin)
except Exception: print("ERR\t-\t-\t0"); raise SystemExit
runs=[r for r in rows if r.get("name")=="Python CI"]
if not runs: print("NONE\t-\t-\t0"); raise SystemExit
r=sorted(runs, key=lambda r: r["databaseId"])[-1]
print("%s\t%s\t%s\t%s" % (r["databaseId"], r.get("status") or "-", r.get("conclusion") or "-", len(runs)))
' 2>/dev/null)

    rid=${line%%$'\t'*}; rest=${line#*$'\t'}
    status=${rest%%$'\t'*}; rest=${rest#*$'\t'}
    concl=${rest%%$'\t'*}

    jobs=0
    case "$rid" in ''|NONE|ERR|'*'*) : ;; *) jobs=$(gh api "repos/$REPO/actions/runs/$rid/jobs" --jq '.total_count' 2>/dev/null || echo 0);; esac
    case "$jobs" in ''|*[!0-9]*) jobs=0 ;; esac

    log "poll: run=${rid:-?} status=${status} conclusion=${concl} jobs=${jobs} waived=${waited}s"

    if [ "$status" = "completed" ]; then
        case "$concl" in
            cancelled)
                log "newest Python CI run was CANCELLED (superseded) — continuing to wait" ;;
            *)
                log "Python CI COMPLETED/${concl} at the recorded head — invoking the rail (it decides what to do about it)"
                rail; exit $? ;;
        esac
    fi
    sleep "$SLEEP_S"
    waited=$((waited + SLEEP_S))
done

log "gave up after ${MAX_WAIT_S}s: no 'Python CI' run ever exercised $HEAD_SHA (repo PR-BACKLOGGED). The PR is mergeable and its clean attestation is recorded at this head; it needs CI CAPACITY, not a code change."
exit 4
