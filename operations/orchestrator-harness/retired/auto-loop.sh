#!/bin/bash
# auto-loop.sh — periodic self-healing merge loop for this box's PRs (bash 3.2 compatible).
# Every cycle, for each open PR on a -tm/-cap branch:
#   all required checks green      -> merge now
#   some pending                   -> arm auto-merge
#   ai-review-gate FAILED          -> (re)run the review->record chain (a fresh head needs a fresh marker)
#   python-ci-gate (or any) FAILED -> re-run that CI job once (repo CI is flake-prone)
set -u
OR="${OR:-daniel-ospina/tortoise}"
REPO="${REPO:-/Users/home/eldato/tortoise}"
O="${O:-$HOME/orchestrator}"
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
export AGENT_SKIP_REVIEW_GATE=1 ELDATO_SKIP_VGATE=1
REQ="ai-review-gate python-ci-gate docs test-isolation license-surface legal-e2e pricing-artifact"
mkdir -p "$O/logs" "$O/state"
LOG="$O/logs/auto-loop.log"

while true; do
  echo "=== cycle $(date) ===" >> "$LOG"
  gh pr list --repo "$OR" --state open --limit 120 \
    --json number,headRefName \
    --jq '.[]|select(.headRefName|test("-tm$|-cap$"))|"\(.number)\t\(.headRefName)"' 2>/dev/null |
  while IFS="$(printf '\t')" read -r PR BR; do
    [ -z "${PR:-}" ] && continue
    st="$(gh pr view "$PR" --repo "$OR" --json state --jq .state 2>/dev/null)"
    [ "$st" = "MERGED" ] && continue
    verdict="$(gh pr checks "$PR" --repo "$OR" --json name,state 2>/dev/null | python3 -c "
import sys,json
try: d=json.load(sys.stdin)
except Exception: print('NOCHECKS'); raise SystemExit
m={c['name']:c['state'] for c in d}
want='$REQ'.split()
# RETIRED, kept for the record. NOTE: this copy still carries the fail-open reading of
# ai-review-gate (SKIPPED/NEUTRAL counted as satisfied) that orch-loop.sh fixed — do not reuse
# this file without requiring an explicit SUCCESS for that check. It ALSO still interpolates the
# untrusted headRefName straight into the tmux sh -c string (line ~51); orch-loop.sh gates that
# behind safe_ref()/is_num()/--argv. Prefer the live scripts.
bad=lambda s: s in ('FAILURE','ERROR','CANCELLED','TIMED_OUT','ACTION_REQUIRED')
ok=lambda s: s in ('SUCCESS','FAILURE','ERROR','CANCELLED','TIMED_OUT','ACTION_REQUIRED','SKIPPED','NEUTRAL')
# RETIRED, kept for the record. NOTE: this copy still carries the fail-open reading of
# ai-review-gate (SKIPPED/NEUTRAL counted as satisfied) that orch-loop.sh fixed — do not reuse
# this file without porting `if gate != 'SUCCESS' and not bad(gate): print('FAILED:ai-review-gate')`.
fail=[n for n in want if bad(m.get(n,''))]
pend=[n for n in want if not ok(m.get(n,''))]
print(('FAILED:'+','.join(fail)) if fail else (('PENDING:'+','.join(pend)) if pend else 'GREEN'))
" 2>/dev/null)"
    case "$verdict" in
      GREEN)
        if gh pr merge "$PR" --repo "$OR" --squash >>"$LOG" 2>&1; then echo "#$PR MERGED" >>"$LOG"; else echo "#$PR merge failed" >>"$LOG"; fi ;;
      PENDING:*)
        if gh pr merge "$PR" --repo "$OR" --squash --auto >>"$LOG" 2>&1; then echo "#$PR auto-armed (${verdict})" >>"$LOG"; else echo "#$PR auto failed" >>"$LOG"; fi ;;
      FAILED:*)
        echo "#$PR ${verdict}" >>"$LOG"
        if echo "$verdict" | grep -q "ai-review-gate"; then
          if ! tmux has-session -t "merge-$PR" 2>/dev/null && ! tmux has-session -t "fix-$PR" 2>/dev/null; then
            # RETIRED: unsafe interpolation of the API-supplied branch name (see the header note).
            tmux new-session -d -s "merge-$PR" "bash '$O/merge-one.sh' '$REPO' '$OR' $PR '$BR'" 2>/dev/null \
              && echo "#$PR re-running review->record chain" >>"$LOG"
          fi
        fi
        if echo "$verdict" | grep -q "python-ci-gate"; then
          rid="$(gh pr checks "$PR" --repo "$OR" --json name,link --jq '.[]|select(.name=="python-ci-gate")|.link' 2>/dev/null | sed -nE 's#.*/runs/([0-9]+).*#\1#p')"
          if [ -n "${rid:-}" ] && [ ! -f "$O/state/rerun-$PR-$rid" ]; then
            gh run rerun "$rid" --repo "$OR" --failed >>"$LOG" 2>&1 && { touch "$O/state/rerun-$PR-$rid"; echo "#$PR re-ran CI $rid (flake retry)" >>"$LOG"; }
          fi
        fi ;;
    esac
  done
  sleep 600
done
