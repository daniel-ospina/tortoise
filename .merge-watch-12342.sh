#!/usr/bin/env bash
# Wait for Python CI run 43367732323 (attempt 2) to reach a terminal state on
# head d2b3cd09, then run the merge rail. The rail is NOT re-implemented here:
# this only decides WHEN to invoke scripts/admin-merge.sh, because the rail's
# own FIRST-CHECKS wait went silent for 20 minutes on this box.
set -u
cd "$(dirname "$0")" || exit 1
RUN=43367732323
REPO=daniel-ospina/tortoise
LOG=/tmp/merge-watch-12342.log

: > "$LOG"
echo "$(date -u +%FT%TZ) watcher start; run=$RUN" >> "$LOG"

for i in $(seq 1 240); do
  state=$(GH_LIMIT_ESSENTIAL=1 gh api "repos/$REPO/actions/runs/$RUN" --jq '"\(.status)/\(.conclusion // "-")"' 2>/dev/null)
  echo "$(date -u +%FT%TZ) poll $i: $state" >> "$LOG"
  case "$state" in
    completed/*) break ;;
  esac
  sleep 60
done

echo "$(date -u +%FT%TZ) terminal: ${state:-unreadable} — running rail" >> "$LOG"
GH_LIMIT_ESSENTIAL=1 bash scripts/admin-merge.sh 12342 >> "$LOG" 2>&1
echo "$(date -u +%FT%TZ) RAIL_EXIT=$?" >> "$LOG"
