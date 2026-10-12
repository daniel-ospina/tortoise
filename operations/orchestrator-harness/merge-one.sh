#!/usr/bin/env bash
# merge-one.sh <repo-path> <owner/repo> <pr> <branch>
# merge-one.sh --argv <file>            (4 lines: repo, owner/repo, pr, branch)
# Full chain for ONE PR: fresh-context review -> evidence comment -> record-review -> wait green -> merge.
# This box holds the AI_REVIEW_GATE_KEY, so it owns the merge authority for its own PRs.
set -u
# --argv exists because these values are attacker-influenced (a PR branch name may contain quotes,
# semicolons, $ and backticks) and the controller must launch us from a tmux `sh -c` string.
# Passing them through a file keeps every PR-derived value out of that string entirely.
if [ "${1:-}" = "--argv" ]; then
  ARGV="${2:-}"
  [ -f "$ARGV" ] || { echo "[merge] missing argv file: $ARGV" >&2; exit 1; }
  { read -r REPO; read -r OR; read -r PR; read -r BR; } < "$ARGV"
  rm -f "$ARGV"
else
  REPO="$1"; OR="$2"; PR="$3"; BR="$4"
fi
case "${PR:-}" in ''|*[!0-9]*) echo "[merge] refusing non-numeric PR: ${PR:-<empty>}" >&2; exit 1 ;; esac
O="${O:-$HOME/orchestrator}"; mkdir -p "$O/logs" "$O/state"
L="$O/logs/merge-$PR"
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
export AGENT_SKIP_REVIEW_GATE=1 ELDATO_SKIP_VGATE=1 AGENT_ALLOW_MAIN_EDITS=1 ELDATO_ALLOW_MAIN_EDITS=1
export PI_MCP_SERVERS=none   # headless reviewer uses no MCP servers — saves ~25 processes/run
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
git fetch origin "$BR" --quiet 2>/dev/null || true   # external (other-machine) branches may not be fetched locally

# ── single-owner claim ────────────────────────────────────────────────────────
# Three paths reach this script: chain-<N> (waits for a lane's PR), fix-<PR>, and the
# controller's merge sweep. Only the last two are visible to the controller by session name,
# so without a shared claim a freshly opened PR gets TWO concurrent reviewers racing the same
# head: duplicate evidence comments, duplicate record-review writes, and an
# ISSUES/CLEAN interleaving where one worker's needsfix marker lands after the other merged.
# Atomic noclobber create is the lock; the controller also tests this file.
CLAIM="$O/state/mergeclaim-$PR.lock"
claim() { ( set -o noclobber; echo $$ > "$CLAIM" ) 2>/dev/null; }
if ! claim; then
  OPID="$(cat "$CLAIM" 2>/dev/null || echo 0)"
  if [ "${OPID:-0}" -gt 0 ] && kill -0 "$OPID" 2>/dev/null; then echo "[merge $PR] pid $OPID already owns this PR — exiting" >> "$L.log"; exit 0; fi
  rm -f "$CLAIM"
  claim || { echo "[merge $PR] claim race — exiting" >> "$L.log"; exit 0; }
fi
trap 'rm -f "$CLAIM"' EXIT INT TERM

HEAD="$(gh pr view "$PR" --repo "$OR" --json headRefOid --jq .headRefOid 2>/dev/null)"
echo "[merge $PR] start $(date) head=$HEAD branch=$BR" > "$L.log"

# ── 1) fresh-context review, POSTED as a PR comment (the audit artifact) ──────
cat > "$L-reviewer.txt" <<EOF
You are a FRESH-CONTEXT code reviewer for $OR. Review PR #$PR (branch $BR). Report only — do not fix code.
    cd $REPO && git fetch origin main --quiet
    gh pr view $PR --repo $OR --json title,body,files
    gh pr diff $PR --repo $OR
Read the changed files IN FULL at the PR head (\`git show origin/$BR:<path>\`), not just the diff hunks. Read skills/code-review/SKILL.md for the discipline.
Report ONLY HIGH-confidence real issues: correctness bugs; security (injection/auth bypass/secrets); VACUOUS tests (would pass on the pre-fix code — prove it); scope/claim mismatch; regressions; fail-open paths; repo-rule violations — especially a NEW test file missing from config/ci-surfaces.yml (the manifest-integrity leg fails and the file is never selected).
If you need to run tests, use your own throwaway checkout: \`TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest <paths> -q -p no:cacheprovider\`. Bound every command; never run the whole suite.

## REQUIRED: post your review as a PR comment (this is the merge-gate audit artifact)
Write your review to a file, then:
    gh pr comment $PR --repo $OR --body-file <your-review-file>
Your comment MUST state the outcome plainly and MUST NOT contain an unresolved assertion. Forbidden phrases in a CLEAN comment: "issues remain", "NOT CLEAN", "requires human attention", "auto-fix stalled/failed/reached cap".

## FINAL MESSAGE — exactly one line:
    CLEAN <the comment URL that gh pr comment printed>
  or, if you found real issues:
    ISSUES <n>
Do NOT modify code, commit, or merge.
EOF
run_agent "${REVIEW_TIMEOUT:-1800}" "$L.log" "$L-reviewer.txt"
LAST="$(grep -aE '^(CLEAN|ISSUES) ' "$L.log" | tail -1 | tr -d '\r')"
echo "[merge $PR] verdict: ${LAST:-<none>}" >> "$L.log"

case "$LAST" in
  CLEAN*)
    URL="$(printf '%s' "$LAST" | grep -oE 'https?://[^ ]+' | head -1)"
    CID="$(printf '%s' "$URL" | grep -oE '[0-9]+$')"
    echo "[merge $PR] evidence comment id=${CID:-?} url=$URL" >> "$L.log"
    if [ -z "${CID:-}" ]; then echo "[merge $PR] ABORT: no comment id" >> "$L.log"; exit 2; fi
    # ── 2) record the review (writes the signed marker into the PR body) ──
    "$HOME/.pi/agent/scripts/record-review.sh" "$PR" "$HEAD" clean "$OR" --evidence "comment:$CID" >> "$L.log" 2>&1
    rc=$?
    echo "[merge $PR] record-review rc=$rc" >> "$L.log"
    [ $rc -ne 0 ] && { echo "[merge $PR] ABORT: record failed" >> "$L.log"; exit 3; }
    # ── 3) wait for the required ai-review-gate to go green (read the LATEST check row) ──
    gate=""
    for i in $(seq 1 30); do
      gate="$(gh pr checks "$PR" --repo "$OR" --json name,state --jq '[.[]|select(.name=="ai-review-gate")][0].state' 2>/dev/null)"
      echo "[merge $PR] gate=$gate (try $i)" >> "$L.log"
      [ "$gate" = "SUCCESS" ] && break
      sleep 30
    done
    if [ "$gate" != "SUCCESS" ]; then echo "[merge $PR] ABORT: gate=$gate (not green)" >> "$L.log"; exit 4; fi
    # ── 4) merge ──
    if gh pr merge "$PR" --repo "$OR" --squash >> "$L.log" 2>&1; then
      echo "[merge $PR] MERGED $(date)" >> "$L.log"
      touch "$O/state/merged-$PR.done"
    else
      echo "[merge $PR] merge command failed" >> "$L.log"
    fi
    ;;
  *)
    # The reviewer found real issues (or the reviewer failed) at this exact head. Leave a
    # marker so the loop knows to spawn a FIXER (not another re-record) on its next cycle.
    echo "[merge $PR] NOT CLEAN or reviewer failed — needs a fixer at head=$HEAD" >> "$L.log"
    [ -n "${HEAD:-}" ] && touch "$O/state/needsfix-$PR-$HEAD"
    ;;
esac
echo "[merge $PR] end $(date)" >> "$L.log"
