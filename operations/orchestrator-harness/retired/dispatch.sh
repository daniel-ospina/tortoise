#!/usr/bin/env bash
# Dispatcher: worktree -> tmux lane (cwd = worktree) -> tmux review watcher.
#   dispatch.sh <repo-path> <owner/repo> <kind> <issue> [<issue> ...]
# kind: py (tortoise) | ts (agent-infra)
set -u
REPO="$1"; OR="$2"; KIND="$3"; shift 3
O="${O:-$HOME/orchestrator}"; mkdir -p "$O/logs" "$O/state"
chmod +x "$O/lane.sh" "$O/watch-one.sh"
cd "$REPO" || exit 1
git fetch origin main --quiet 2>/dev/null

for N in "$@"; do
  WT="tm-$N"; BR="feat/$N-tm"
  if ! git worktree list | grep -q "/$WT "; then
    git worktree add ".worktrees/$WT" -b "$BR" origin/main >/dev/null 2>&1 \
      || { echo "#$N: worktree add failed (branch exists?) — skipping"; continue; }
  fi
  tmux new-session -d -s "lane-$N" \
    "cd '$REPO/.worktrees/$WT' && bash '$O/lane.sh' $N '$BR' '$OR'" 2>/dev/null \
    && echo "#$N: lane-$N (cwd=worktree) → $BR"
  tmux new-session -d -s "tw$N" \
    "cd '$REPO/.worktrees/$WT' && bash '$O/watch-one.sh' '$REPO' $N '$BR' $KIND" 2>/dev/null \
    && echo "        watcher tw$N"
  sleep 45   # stagger dependency installs
done
echo "--- tmux sessions: $(tmux ls 2>/dev/null | wc -l | tr -d ' ') ---"
uptime | sed 's/.*load/load/'
