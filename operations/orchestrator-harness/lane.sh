#!/usr/bin/env bash
# Runs ONE lane's pi inside its own worktree (cwd must already be the worktree).
#   lane.sh <issue> <branch> <owner/repo>
set -u
N="$1"; BR="$2"; OR="$3"
O="${O:-$HOME/orchestrator}"; mkdir -p "$O/logs"
# The wall-clock limit AND the load gate both live in the shared helper, so this script has no
# `pi` invocation of its own to forget to gate (agent-infra#1720). Fail LOUD if the helper is
# missing: an unsourced helper would leave `run_agent` undefined, and an undefined helper must never
# degrade into "the agent ran" or "the review failed" — see README §9 on syncing $O.
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SELF/spawn-gate.sh" || { echo "[lane $N] FATAL: $SELF/spawn-gate.sh missing — refusing to start an ungated agent" >&2; exit 78; }
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
export AGENT_SKIP_REVIEW_GATE=1 ELDATO_SKIP_VGATE=1 AGENT_ALLOW_MAIN_EDITS=1 ELDATO_ALLOW_MAIN_EDITS=1
export PI_MCP_SERVERS=none   # headless lanes use no MCP servers — saves ~25 processes/run
export PI_SKIP_VERSION_CHECK=1 SKILL_ENFORCER_DISABLED=1 LOOP_ENFORCER_DISABLED=1 SLACK_BRIDGE_DISABLE=1 VISION_INTERCEPTOR_DISABLED=1
echo "[lane $N] cwd=$(pwd) branch=$(git branch --show-current 2>/dev/null) $(date)" >> "$O/logs/lane-$N.log"
cat > "$O/logs/prompt-$N.txt" <<EOF
Autonomous engineering lane. You are ALREADY inside your own worktree (that is your cwd, on branch \`$BR\`). Do NOT create, switch, or remove worktrees, and NEVER touch the repo's main checkout.

## Your issue
**#$N** — read it IN FULL including every comment (root-cause corrections live there):
    gh issue view $N --repo $OR --json title,body,labels
    gh issue view $N --repo $OR --comments --json comments --jq '.comments[].body'
The comment thread supersedes the body where they disagree. Several issues here are labelled provenance-stale — RE-MEASURE before trusting the body.

## Setup (you are already in the worktree)
    uv sync --extra embeddings --extra parity        # tortoise: installs the canonical dev env
Tests: \`TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest <paths> -q -p no:cacheprovider\`  (NEVER bare \`uv run\` — it strips the extras).

## Rules
- The review/VGATE extensions are BYPASSED for you and you have NO "task" tool — do NOT dispatch. The PARENT runs the review gate.
- Commit with \`git commit -F <message-file>\` (repo-unique temp path per AGENTS.md "Editing Rules"). Stage specific files; never \`git add -A\`.
- Push (\`git push -u origin $BR\`), then open a PR against main: title "<type>(<scope>): <subject> (#$N)"; body must contain "Closes #$N" (or "Refs #$N" if partial), what changed, and exact test evidence. Do NOT merge.
- Do NOT change the MCP tool registry or the public TortoiseSDK surface (needs owner approval — STOP and report instead).
- Read skills/issue-workflow/SKILL.md and skills/issue-scoping/SKILL.md first.
- If the defect is already fixed (in-repo or elsewhere) report ALREADY_FIXED / OUT-OF-REPO with file/line/commit evidence instead of forcing a PR. A no-change verdict with evidence is a good outcome.
- **If you add a test file, you MUST register it in \`config/ci-surfaces.yml\`** — an unregistered file is never selected on the relevant source diff and the unconditional \`manifest-integrity\` leg fails. Verify with \`python tools/ci_selection.py --integrity\`.
- Use the research skill for non-trivial research; never call web_search directly. Auto-file any pre-existing bug with no open issue. Never ask the human anything.
- Collision pre-flight is CLEAN for this issue.

## Deliverable (final message, <=25 lines)
issue #, status (PR_OPENED | ALREADY_FIXED | OUT-OF-REPO | BLOCKED), branch + PR URL, files changed, exact test commands + pass/fail counts, blockers.
EOF
run_agent "${LANE_TIMEOUT:-2400}" "$O/logs/lane-$N.log" "$O/logs/prompt-$N.txt" "lane $N"
# Capture the status BEFORE anything else runs: `echo "… $?"` after another command reports that
# command's status, not the agent's (the defect §7.4 records for retired/do-merge.sh).
rc=$?
echo "[lane $N] exited $rc $(date)" >> "$O/logs/lane-$N.log"
# A gate refusal reports AGENT_LOAD_REFUSED (75); AGENT_STARTED is authoritative rather than the exit
# code, because `pi` shares the 0–255 status namespace and could exit 75 itself. (#1720 advisory)
if [ "$AGENT_STARTED" = "0" ]; then
  # No agent ran and no PR was opened. The issue is not stranded: refresh-candidates.sh un-blacklists
  # a dispatched issue that has neither a PR nor a live lane session, so it returns to the pool.
  echo "[lane $N] NOT started: load gate refused (see the line above) — issue returns to the queue" >> "$O/logs/lane-$N.log"
fi
exit "$rc"
