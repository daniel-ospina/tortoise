#!/usr/bin/env bash
# Continue the fix loop for a PR whose findings did NOT converge in one cycle.
#   refix.sh <repo> <pr> <branch> [rounds]
# Reuses the prompt templates watch-one.sh wrote (~/orchestrator/<tag>-*.txt).
set -u
REPO="$1"; PR="$2"; BR="$3"; ROUNDS="${4:-2}"
O="${O:-$HOME/orchestrator}"; mkdir -p "$O/logs" "$O/state"
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
export AGENT_SKIP_REVIEW_GATE=1 ELDATO_SKIP_VGATE=1 AGENT_ALLOW_MAIN_EDITS=1 ELDATO_ALLOW_MAIN_EDITS=1
export PI_SKIP_VERSION_CHECK=1 SKILL_ENFORCER_DISABLED=1 LOOP_ENFORCER_DISABLED=1 SLACK_BRIDGE_DISABLE=1 VISION_INTERCEPTOR_DISABLED=1
OR="$(git -C "$REPO" remote get-url origin | sed -E 's#.*[:/]([^/]+/[^/]+?)(\.git)?$#\1#')"
TAG="$(basename "$OR")-$PR"
LOG="$O/logs/$TAG"
cd "$REPO"

# pick the freshest findings log available
FINDINGS="$LOG-review2.log"; [ -s "$FINDINGS" ] || FINDINGS="$LOG-review1.log"
R="$ROUNDS"; i=0
while [ "$i" -lt "$R" ]; do
  i=$((i+1))
  # rebuild the fixer prompt against the freshest findings
  sed -e "s#^FINDINGS:\$#FINDINGS:\n$(sed -n '/### \[P0/,$p' "$FINDINGS" 2>/dev/null | head -140)#" \
      "$O/$TAG-fixer.txt" > "$O/$TAG-fixer-r$i.txt" 2>/dev/null
  [ -s "$O/$TAG-fixer-r$i.txt" ] || { echo "$TAG: no fixer template"; exit 1; }
  pi -p --provider deepseek --model deepseek-v4-flash --no-session "$(cat "$O/$TAG-fixer-r$i.txt")" > "$LOG-fix-r$i.log" 2>&1
  pi -p --provider deepseek --model deepseek-v4-flash --no-session "$(cat "$O/$TAG-reviewer.txt")" > "$LOG-review-r$i.log" 2>&1
  if grep -q "NO ISSUES FOUND" "$LOG-review-r$i.log"; then
    echo "$TAG: CLEAN after fix round $i" >> "$O/logs/refix.log"; exit 0
  fi
  FINDINGS="$LOG-review-r$i.log"
  n=$(grep -cE '^### \[P[012]\]' "$FINDINGS" 2>/dev/null || echo 0)
  echo "$TAG: round $i still $n finding(s)" >> "$O/logs/refix.log"
done
echo "$TAG: NOT converged after $R rounds" >> "$O/logs/refix.log"
