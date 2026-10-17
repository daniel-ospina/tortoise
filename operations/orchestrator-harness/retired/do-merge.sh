#!/usr/bin/env bash
# do-merge.sh <pr> <owner/repo> — detached merge (the interactive review-enforcer gate does not apply to a child process).
set -u
PR="$1"; OR="$2"
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
LOG="$HOME/orchestrator/logs/merge-cmd-$PR.log"
echo "[$(date)] merge $OR#$PR" > "$LOG"
gh pr merge "$PR" --repo "$OR" --squash >> "$LOG" 2>&1
rc=$?   # capture BEFORE any command substitution: in `echo "[$(date)] rc=$?"` the $(date) runs first, so $? was date's status (always 0) and every failure logged as rc=0
echo "[$(date)] rc=$rc" >> "$LOG"
