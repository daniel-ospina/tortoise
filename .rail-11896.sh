#!/usr/bin/env bash
# rail-11896.sh — retry the MANDATED merge rail until the queue answers, then land it.
#
# WHY THIS EXISTS. At 2026-11-11 ~18:50Z the repo's own instrument reported
# `CI: PR-BACKLOGGED(queued=30, pr_running=4, pr_queued=27, pr_pending=6, oldest=527775s)`
# — a backlog ~6 days deep — so PR #11896's required checks were CREATED but never
# STARTED. `admin-merge.sh` refused with its own diagnosis:
#   "no run of the lane actually TESTED head <sha> (lane: python-ci.yml) ... completed=0
#    run(s), of which tested=0"
# That refusal is correct and is NOT bypassed here: this loop does not pass
# AGENT_ADMIN_MERGE_OVERRIDE, does not touch --admin directly, and does not fabricate
# evidence. It re-invokes the SAME rail, which merges only when the lane has actually
# tested the head and the failing set is empty or base-owned.
#
# The review attestation is already recorded at this head
# (record-review.sh 11896 a7d5929e0e… clean, evidence comment 6804811938).
#
# Bounds: 48 attempts x 300s ≈ 4h. Stops immediately when the PR is merged/closed.
set -uo pipefail
cd /home/cmux/tortoise/.worktrees/11836-guardtests || exit 1

PR=11896
LOG="$PWD/.worktree-report/rail-11896.log"
mkdir -p "$PWD/.worktree-report"
say() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

say "rail-11896: start (pid $$)"
for attempt in $(seq 1 48); do
  state=$(gh pr view "$PR" --json state --jq .state 2>/dev/null || echo unknown)
  if [ "$state" != "OPEN" ]; then
    say "rail-11896: PR state=$state — nothing left for this loop to do"
    exit 0
  fi
  say "rail-11896: attempt $attempt — invoking the rail"
  if bash scripts/admin-merge.sh "$PR" >>"$LOG" 2>&1; then
    say "rail-11896: rail exited 0 on attempt $attempt"
  else
    say "rail-11896: rail refused on attempt $attempt (see $LOG) — retrying"
  fi
  merged=$(gh pr view "$PR" --json state --jq .state 2>/dev/null || echo unknown)
  if [ "$merged" = "MERGED" ]; then
    say "rail-11896: MERGED"
    exit 0
  fi
  sleep 300
done
say "rail-11896: exhausted the attempt bound without a merge — a human or the next lane must re-run the rail"
exit 1
