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

# capture the reviewer's findings (the latest code-review comment on the PR)
gh pr view "$PR" --repo "$OR" --comments > "$L-comments.txt" 2>&1

cat > "$L-fixer.txt" <<EOF
You are a FIX agent for branch \`$BR\` (PR #$PR in $OR). A fresh-context reviewer found real issues; the full review is the LAST comment in this file: $L-comments.txt — read it in full (find every "### N. Px" / "[P0|P1|P2]" section and its "Fix:" line).

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
pi -p --provider deepseek --model deepseek-v4-flash --no-session "$(cat "$L-fixer.txt")" >> "$L.log" 2>&1
echo "[fix $PR] fixer done $(date) — re-running merge chain" >> "$L.log"
# re-run the full chain at the new head
bash "$O/merge-one.sh" "$REPO" "$OR" "$PR" "$BR" >> "$L.log" 2>&1
echo "[fix $PR] end $(date)" >> "$L.log"
