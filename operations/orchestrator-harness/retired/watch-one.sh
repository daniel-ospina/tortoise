#!/usr/bin/env bash
# Durable review→fix→re-review watcher for ONE PR. Survives reboots (lives in
# ~/orchestrator, not /tmp).
#   watch-one.sh <repo-path> <pr> <branch> [kind]
# kind: py (tortoise, pytest) | ts (agent-infra, npx tsx)  — default py
set -u
REPO="$1"; PR="$2"; BR="$3"; KIND="${4:-py}"
O="${O:-$HOME/orchestrator}"; mkdir -p "$O/logs" "$O/state"
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
export AGENT_SKIP_REVIEW_GATE=1 ELDATO_SKIP_VGATE=1 AGENT_ALLOW_MAIN_EDITS=1 ELDATO_ALLOW_MAIN_EDITS=1
export PI_SKIP_VERSION_CHECK=1 SKILL_ENFORCER_DISABLED=1 LOOP_ENFORCER_DISABLED=1 SLACK_BRIDGE_DISABLE=1 VISION_INTERCEPTOR_DISABLED=1
OWNER_REPO="$(git -C "$REPO" remote get-url origin | sed -E 's#.*[:/]([^/]+/[^/]+?)(\.git)?$#\1#')"
TAG="$(basename "$OWNER_REPO")-$PR"
LOG="$O/logs/$TAG"

if [ "$KIND" = "ts" ]; then
  TESTCMD='Tests are run with `npx tsx <file>` (see .github/workflows/ci-main.yml).'
  REPRO='Read the changed files IN FULL at the PR head (`git show origin/'"$BR"':<path>`), not just the diff hunks.'
else
  TESTCMD='Tests: `TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest <paths> -q -p no:cacheprovider` in your own throwaway worktree.'
  REPRO='Read the changed files IN FULL at the PR head (`git show origin/'"$BR"':<path>`), not just the diff hunks.'
fi

cat > "$O/$TAG-reviewer.txt" <<EOF
You are a FRESH-CONTEXT code reviewer. Find real defects in ONE PR — report only, do not fix.
    cd "$REPO" && git fetch origin main --quiet
    gh pr view $PR --repo $OWNER_REPO --json title,body,files
    gh pr diff $PR --repo $OWNER_REPO
$REPRO Read the repo's skills/code-review/SKILL.md.

Report only HIGH-confidence real issues: correctness bugs; security (injection, auth/consent bypass, secrets in artifacts); VACUOUS tests (pass on the pre-fix code too — prove it by mutation or by reading the mock); scope/claim mismatch; regressions; fail-open paths wider than the issue requires; repo-rule violations (public surface change without approval, committed build artifacts, a new test file missing from config/ci-surfaces.yml).

FINAL MESSAGE: per issue "### [P0|P1|P2] <title>" then file:line, what is wrong, evidence, one-line fix. If none: exactly "NO ISSUES FOUND" plus a 3-line summary of what you checked.
Do NOT modify files, commit, or merge. $TESTCMD Bound every command.
EOF


cd "$REPO"
pi -p --provider deepseek --model deepseek-v4-flash --no-session "$(cat "$O/$TAG-reviewer.txt")" > "$LOG-review1.log" 2>&1
if grep -qE '^### \[P[012]\]' "$LOG-review1.log"; then
# NOTE: build the fixer prompt HERE, after the reviewer has written its log. Constructing it
# above expanded $(sed ... "$LOG-review1.log") before that file existed, so the fixer always
# ran against an empty (or stale previous-run) FINDINGS section.
cat > "$O/$TAG-fixer.txt" <<EOF
You are a FIX agent for branch $BR. A fresh-context reviewer found the issues below. Fix them on the existing branch, make the tests genuinely pin the fixes, run the tests, commit with \`git commit -F <message-file>\`, push to the SAME branch. Do NOT merge.

Work in the worktree already checked out on this branch (find it with \`git -C "$REPO" worktree list\`). Work ONLY there — never the main checkout.
$TESTCMD Stage specific files; never \`git add -A\`. You have no "task" tool — do not dispatch. If a finding is wrong, say so with evidence instead of changing code.
FINDINGS:
$(sed -n '/### \[P0/,$p' "$LOG-review1.log" 2>/dev/null | head -140)
FINAL MESSAGE: status, files changed, exact test commands + results, how each finding was addressed (or why you disagree).
EOF
  pi -p --provider deepseek --model deepseek-v4-flash --no-session "$(cat "$O/$TAG-fixer.txt")" > "$LOG-fix1.log" 2>&1
  pi -p --provider deepseek --model deepseek-v4-flash --no-session "$(cat "$O/$TAG-reviewer.txt")" > "$LOG-review2.log" 2>&1
fi
touch "$O/state/$TAG.done"
echo "$TAG done $(date)" >> "$O/logs/watcher.log"
