#!/bin/bash
# adopt-fix.sh <repo> <owner/repo> <pr> <branch>
# For a PR on an EXTERNAL (other-machine) branch: ensure a local worktree exists, then run the fix chain.
set -u
REPO="$1"; OR="$2"; PR="$3"; BR="$4"
O="${O:-$HOME/orchestrator}"
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
mkdir -p "$O/logs"
cd "$REPO" || exit 1
git fetch origin "$BR" --quiet 2>/dev/null || true
WT=".worktrees/adopt-$PR"
if ! git worktree list | grep -q "/$WT "; then
  git worktree add "$WT" "$BR" >/dev/null 2>&1 || { echo "[adopt-fix $PR] worktree add failed for $BR" | tee -a "$O/logs/adopt-$PR.log"; exit 1; }
fi
echo "[adopt-fix $PR] worktree=$WT branch=$BR $(date)" >> "$O/logs/adopt-$PR.log"
bash "$O/fix-one.sh" "$REPO" "$OR" "$PR" "$BR" >> "$O/logs/adopt-$PR.log" 2>&1
rc=$?
# 75 = the load gate refused to start the fixer (nothing ran, nothing consumed). Name it here: the
# fixer's own line is inside fix-$PR.log, and this is the log a manual operator reads.
if [ "$rc" = "75" ]; then
  echo "[adopt-fix $PR] fixer NOT started (load gate) — re-run when 1-min load is below MAX_LOAD" >> "$O/logs/adopt-$PR.log"
fi
# ⛔ Exit with the FIXER's status, explicitly. This script used to end on `bash "$O/fix-one.sh"`,
# so `$?` was the fixer's. The reporting line above became the last command and inverted the status:
# 1 on a clean success, 0 on the load refusal it exists to REPORT (a wrapper doing
# `adopt-fix.sh … && next` then took the wrong branch, and the code said the opposite of the log).
# `exit "$rc"` restores the contract rather than relying on the last command's status (#1720 review).
exit "$rc"
