#!/usr/bin/env bash
# fix-one.sh <repo-path> <owner/repo> <pr> <branch>
# fix-one.sh --argv <file>            (4 lines: repo, owner/repo, pr, branch)
# Runs a fixer on the PR's worktree to address the reviewer's findings, pushes, then re-runs the merge chain.
set -u
if [ "${1:-}" = "--argv" ]; then
  ARGV="${2:-}"
  [ -f "$ARGV" ] || { echo "[fix] missing argv file: $ARGV" >&2; exit 1; }
  { read -r REPO; read -r OR; read -r PR; read -r BR; } < "$ARGV"
  rm -f "$ARGV"
else
  REPO="$1"; OR="$2"; PR="$3"; BR="$4"
fi
case "${PR:-}" in ''|*[!0-9]*) echo "[fix] refusing non-numeric PR: ${PR:-<empty>}" >&2; exit 1 ;; esac
O="${O:-$HOME/orchestrator}"; mkdir -p "$O/logs" "$O/state"
L="$O/logs/fix-$PR"
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
export AGENT_SKIP_REVIEW_GATE=1 ELDATO_SKIP_VGATE=1 AGENT_ALLOW_MAIN_EDITS=1 ELDATO_ALLOW_MAIN_EDITS=1
export PI_MCP_SERVERS=none   # headless fixer uses no MCP servers — saves ~25 processes/run
export PI_SKIP_VERSION_CHECK=1 SKILL_ENFORCER_DISABLED=1 LOOP_ENFORCER_DISABLED=1 SLACK_BRIDGE_DISABLE=1 VISION_INTERCEPTOR_DISABLED=1
cd "$REPO" || exit 1

# Portable wall-clock timeout for the agent invocation. `pi -p` has no internal limit, so one hung
# model call holds a concurrency slot forever — and the caps count SESSIONS, so a single hang
# silently halves throughput. macOS ships no `timeout`/`gtimeout`, so implement it in bash: run the
# agent in the background with a watchdog subshell that kills it after N seconds. The stale-worker
# reaper in orch-loop.sh is the second line of defence.
run_agent() { # run_agent <seconds> <logfile> <promptfile>
  local t="$1" log="$2" prompt="$3" pid w rc
  pi -p --provider deepseek --model deepseek-v4-flash --no-session "$(cat "$prompt")" >> "$log" 2>&1 &
  pid=$!
  ( sleep "$t"; kill -TERM "$pid" 2>/dev/null; sleep 15; kill -KILL "$pid" 2>/dev/null ) >/dev/null 2>&1 &
  w=$!
  wait "$pid" 2>/dev/null; rc=$?
  kill "$w" 2>/dev/null
  echo "[timeout] agent pid=$pid wall_limit=${t}s rc=$rc" >> "$log"
  return "$rc"
}
# locate the branch's worktree
WT="$(git worktree list --porcelain | awk -v b="refs/heads/$BR" 'BEGIN{p=""} /^worktree /{p=$2} /^branch /{if($2==b) print p}')"
echo "[fix $PR] start $(date) worktree=$WT branch=$BR" > "$L.log"
[ -z "$WT" ] && {
  # Foreign (other-machine) branch: materialise a local worktree so the fixer has somewhere to work.
  if git rev-parse --verify -q "refs/heads/$BR" >/dev/null; then
    git worktree add ".worktrees/fix-$PR" "$BR" >> "$L.log" 2>&1
  else
    git fetch origin "$BR" --quiet >> "$L.log" 2>&1
    git worktree add ".worktrees/fix-$PR" -b "$BR" "origin/$BR" >> "$L.log" 2>&1
  fi
  WT="$(git worktree list --porcelain | awk -v b="refs/heads/$BR" 'BEGIN{p=""} /^worktree /{p=$2} /^branch /{if($2==b) print p}')"
  [ -z "$WT" ] && { echo "[fix $PR] ABORT: could not materialise a worktree for $BR" >> "$L.log"; exit 1; }
  echo "[fix $PR] materialised worktree=$WT" >> "$L.log"
}

# Capture the FAILING REQUIRED CHECK and its log. The fixer is also dispatched for CI failures
# that are not a reviewer finding (docs lint, a committed conflict marker, legal-e2e,
# test-isolation, license-surface, pricing-artifact). Without this it would read only the review
# comment — which for such a PR may not exist at all — and fix blind.
: > "$L-failing.txt"
FAILING_CHECKS="$(gh pr checks "$PR" --repo "$OR" --json name,state 2>/dev/null | python3 -c "
import sys,json
try: d=json.load(sys.stdin)
except Exception: raise SystemExit
# The SAME allow-list polarity the loop's classifier and admin-merge.sh use (agent-infra#1714): a
# check is FAILING only when its state is neither a NAMED in-flight spelling nor one of the NON-RED
# conclusions. CANCELLED/STALE are non-red by that rule - a superseded run
# (cancel-in-progress: true) is normal, and handing one to a fixer burns a capped attempt on a PR
# that is not red, while the old deny-list named CANCELLED as failing.
INFLIGHT=('queued','in_progress','waiting','requested','pending')
NONRED=('success','neutral','skipped','cancelled','stale')
red=[]
for c in (d if isinstance(d,list) else []):
    s=str(c.get('state') or '').lower()
    if s in INFLIGHT or s in NONRED: continue
    red.append(str(c.get('name') or ''))
print(' '.join(red))
" 2>/dev/null)"
for c in $FAILING_CHECKS; do
  echo "=== failing required check: $c ===" >> "$L-failing.txt"
  RID="$(gh pr checks "$PR" --repo "$OR" --json name,link --jq ".[]|select(.name==\"$c\")|.link" 2>/dev/null | sed -nE 's#.*/runs/([0-9]+).*#\1#p' | head -1)"
  if [ -n "${RID:-}" ]; then gh run view "$RID" --repo "$OR" --log-failed 2>/dev/null | tail -80 >> "$L-failing.txt"; fi
done

# capture the reviewer's findings (the latest code-review comment on the PR)
gh pr view "$PR" --repo "$OR" --comments > "$L-comments.txt" 2>&1

cat > "$L-fixer.txt" <<EOF
You are a FIX agent for branch \`$BR\` (PR #$PR in $OR). A fresh-context reviewer found real issues; the full review is the LAST comment in this file: $L-comments.txt — read it in full

If $L-failing.txt is non-empty, REQUIRED CI CHECKS ARE ALSO FAILING and their failing logs are in that file — read it and fix the cause as well. That is not a reviewer finding: it is the build. A committed conflict marker (<<<<<<< / >>>>>>>) anywhere is one such cause and must be resolved, not deleted wholesale. (find every "### N. Px" / "[P0|P1|P2]" section and its "Fix:" line).

Work ONLY in this worktree: \`$WT\` (its cwd). NEVER touch the repo's main checkout. You are already on \`$BR\`.
    cd $WT && git status -sb && git log --oneline -3
If \`.venv\` is missing in the worktree, run \`uv sync --extra embeddings --extra parity\` once before testing.
If the branch is behind \`origin/main\`, integrate it first (\`git fetch origin main\` then a plain merge of origin/main) and resolve any conflicts so the PR stays mergeable.
**If this PR is CONFLICTING (mergeable=CONFLICTING), resolving the conflict IS the task** — GitHub reports it as unmergeable right now, so integrate \`origin/main\`, resolve every conflict hunk keeping both sides' intent, run the targeted tests, commit and push. If there are no review findings, the conflict is the whole job.

Apply a fix for EVERY finding (P3 notes: address or record explicitly). Requirements:
- Make the fix at the stated location, and add/adjust tests so they genuinely FAIL on the pre-fix code (prove non-vacuity where you can).
- Register any new test file in \`config/ci-surfaces.yml\` (correct surface) — verify with \`TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python tools/ci_selection.py --integrity\`.
- Run the targeted tests: \`TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest <paths> -q -p no:cacheprovider\` (plus the sibling suites the review names). Never run the whole suite.
- Stage specific files (never \`git add -A\`); commit with \`git commit -F <message-file>\`; push: \`git push origin $BR\`.
- Do NOT merge. Do NOT amend already-pushed commits — add a new commit.

FINAL MESSAGE (<=15 lines): which findings you fixed, files changed, exact test commands + pass/fail counts, and any finding you deliberately did not fix with the reason.
EOF
run_agent "${FIX_TIMEOUT:-2400}" "$L.log" "$L-fixer.txt"
echo "[fix $PR] fixer done $(date) — re-running merge chain" >> "$L.log"
# re-run the full chain at the new head
bash "$O/merge-one.sh" "$REPO" "$OR" "$PR" "$BR" >> "$L.log" 2>&1
echo "[fix $PR] end $(date)" >> "$L.log"
