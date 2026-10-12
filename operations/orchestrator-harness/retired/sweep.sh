#!/usr/bin/env bash
# sweep.sh <owner/repo> <pr> [<pr> ...]
# For each PR: if all required checks are green -> merge now; if some are pending -> enable auto-merge;
# if any required check FAILED -> print FIX_NEEDED with the failing check names (do not merge).
set -u
OR="$1"; shift
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
export AGENT_SKIP_REVIEW_GATE=1 ELDATO_SKIP_VGATE=1
REQ="ai-review-gate python-ci-gate docs test-isolation license-surface legal-e2e pricing-artifact"

for PR in "$@"; do
  state="$(gh pr view "$PR" --repo "$OR" --json state --jq .state 2>/dev/null)"
  [ "$state" = "MERGED" ] && { echo "#$PR already merged"; continue; }
  [ "$state" = "CLOSED" ] && { echo "#$PR closed"; continue; }

  checks="$(gh pr checks "$PR" --repo "$OR" --json name,state 2>/dev/null)"
  verdict="$(printf '%s' "$checks" | python3 -c "
import sys,json
try: d=json.load(sys.stdin)
except: print('NOCHECKS'); raise SystemExit
want='$REQ'.split()
m={c['name']:c['state'] for c in d}
bad=[n for n in want if m.get(n) in ('FAILURE','ERROR','CANCELLED','TIMED_OUT','ACTION_REQUIRED')]
# RETIRED, kept for the record. NOTE: this copy still carries the fail-open reading of
# ai-review-gate (SKIPPED/NEUTRAL counted as satisfied) that orch-loop.sh fixed — do not reuse
# this file without requiring an explicit SUCCESS for that check.
pend=[n for n in want if m.get(n) not in ('SUCCESS','FAILURE','ERROR','CANCELLED','TIMED_OUT','ACTION_REQUIRED','SKIPPED','NEUTRAL')]
if bad: print('FAILED:' + ','.join(bad))
elif pend: print('PENDING:' + ','.join(pend))
else: print('GREEN')
" 2>/dev/null)"

  case "$verdict" in
    GREEN)
      out="$(gh pr merge "$PR" --repo "$OR" --squash 2>&1)" && echo "#$PR MERGED" || echo "#$PR merge-now failed: $(printf '%s' "$out" | head -1)" ;;
    PENDING:*)
      out="$(gh pr merge "$PR" --repo "$OR" --squash --auto 2>&1)" && echo "#$PR auto-merge enabled (${verdict})" || echo "#$PR auto failed: $(printf '%s' "$out" | head -1)" ;;
    FAILED:*)
      echo "#$PR FIX_NEEDED ${verdict}" ;;
    *)
      echo "#$PR unknown: $verdict" ;;
  esac
done
