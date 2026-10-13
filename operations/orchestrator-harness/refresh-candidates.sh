#!/bin/bash
# refresh-candidates.sh — pre-flight a pool of open P1/P2 issues and append the CLEAN ones
# to ~/orchestrator/candidates.txt (the dispatch backlog). Skips anything already checked.
set -u
OR="${OR:-daniel-ospina/tortoise}"
REPO="${REPO:-/Users/danielospina/Documents/GitHub/tortoise}"
SC="$REPO/.worktrees/dispatch-scout"
O="${O:-$HOME/orchestrator}"
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
LIMIT="${1:-24}"
mkdir -p "$O"

# The scout is a dedicated throwaway worktree used only to run tools/collision_preflight.py.
# Provision it on first use: an earlier version just `cd`-ed into it, so on a fresh box the
# refresh subshell exited 1 every cycle, wrote nothing, and the backlog silently never refilled.
if [ ! -d "$SC" ]; then
  echo "[refresh] provisioning scout worktree at $SC"
  cd "$REPO" || { echo "[refresh] ABORT: repo not found at $REPO"; exit 1; }
  git fetch origin main --quiet 2>/dev/null || true
  if git rev-parse --verify -q "refs/heads/chore/dispatch-scout" >/dev/null 2>&1; then
    git worktree add "$SC" chore/dispatch-scout >/dev/null 2>&1
  else
    git worktree add "$SC" -b chore/dispatch-scout origin/main >/dev/null 2>&1
  fi
fi
cd "$SC" || { echo "[refresh] ABORT: no scout worktree at $SC — cannot pre-flight issues"; exit 1; }

# collision_preflight.py needs the project's dev environment; without it every check errors and
# the backlog stays empty. Provision once, fail loudly if that is impossible.
if [ ! -x "$SC/.venv/bin/python" ]; then
  echo "[refresh] provisioning scout venv (uv sync)"
  ( uv sync --extra embeddings --extra parity ) >/dev/null 2>&1 \
    || { echo "[refresh] ABORT: uv sync failed in $SC — run it by hand, then re-run the refresh"; exit 1; }
fi
git fetch origin --quiet 2>/dev/null || true

# ── reap stale dispatch bookkeeping ────────────────────────────────────────────
# `dispatched.txt` is append-only: an issue is recorded as handled the first time it is handed
# to a lane, and nothing re-checks whether that dispatch DELIVERED. A lane that dies (reboot,
# OOM, crash) therefore blacklists its issue forever — invisibly, because the issue stays open
# with no PR. Real cases: #4114, #4095, #3554 sat for >24h. Un-blacklist an entry whose issue is
# still open, has no PR, and has no live lane, so the pre-flight below can re-queue it.
gh issue list --repo "$OR" --state open --limit 500 --json number --jq '.[].number' > "$O/.open_iss" 2>/dev/null || : > "$O/.open_iss"
gh pr list --repo "$OR" --state all --limit 500 --json headRefName --jq '.[].headRefName' > "$O/.pr_brs" 2>/dev/null || : > "$O/.pr_brs"
python3 - "$O" <<'PY'
import os, re, subprocess, sys
O = sys.argv[1]
def lines(name):
    p = os.path.join(O, name)
    return [l.strip() for l in open(p)] if os.path.exists(p) else []
open_iss = {l for l in lines(".open_iss") if l}
brs = [l for l in lines(".pr_brs") if l]
try:
    out = subprocess.run(["tmux", "ls", "-F", "#{session_name}"], capture_output=True, text=True).stdout
except Exception:
    out = ""
live = {m.group(1) for m in re.finditer(r"^lane-(\d+)$", out, re.M)}
keep, reaped = [], []
for n in lines("dispatched.txt"):
    if not n:
        continue
    if n in live:
        keep.append(n); continue
    has_pr = any(re.search(r"(^|[-/])%s([-/]|$)" % re.escape(n), b) for b in brs)
    if n in open_iss and not has_pr:
        reaped.append(n)
    else:
        keep.append(n)
with open(os.path.join(O, "dispatched.txt"), "w") as fh:
    fh.write("\n".join(keep) + ("\n" if keep else ""))
if reaped:
    gone = set(reaped)
    ck = os.path.join(O, "checked.txt")
    kept = [l for l in lines("checked.txt") if l and l not in gone]
    if os.path.exists(ck):
        with open(ck, "w") as fh:
            fh.write("\n".join(kept) + ("\n" if kept else ""))
    print("[reap] un-blacklisted (open, no PR, no live lane): %s" % " ".join(sorted(reaped, key=int)))
PY
for f in "$O/.open_iss" "$O/.pr_brs"; do rm -f "$f"; done

touch "$O/checked.txt" "$O/candidates.txt"

# pool = open unassigned P0-blocker ∪ P1-severe ∪ P2-moderate, minus epics/owner-decisions.
#
# ⛔ THE ORDER OF THIS POOL *IS* THE QUEUE — never sort it by issue number.
# `orch-loop.sh` drains `candidates.txt` HEAD-FIRST (it reads lines until BATCH is reached), so
# whatever order is written here IS the dispatch priority. This block used to end in `sort -n -u`,
# i.e. ASCENDING ISSUE NUMBER — and because the P0-blocker backlog is mostly 7xxx/8xxx while
# P2-moderate still holds the 3xxx, the fleet spent every lane on non-P0 work while the blockers
# waited. Measured 2026-10-13: 48 PRs merged in 12h, 0 P0/P1 closed, and 25 of 36 open
# P0-blockers already carried a MERGED PR (#8468). Priority label first, number second; a numeric
# sort anywhere in this file re-creates the exact bug it replaces.
p0() { gh issue list --repo "$OR" --state open --limit 300 --label P0-blocker --json number,assignees,title \
    --jq '.[]|select((.assignees|length)==0)|select(.title|test("^(epic|lane|OWNER DECISION|decision|analysis|investigation)";"i")|not)|.number' 2>/dev/null | sed 's/^/0 /'; }
p1() { gh issue list --repo "$OR" --state open --limit 200 --label P1-severe --json number,assignees,title \
    --jq '.[]|select((.assignees|length)==0)|select(.title|test("^(epic|OWNER DECISION)";"i")|not)|.number' 2>/dev/null | sed 's/^/1 /'; }
p2() { gh issue list --repo "$OR" --state open --limit 200 --label P2-moderate --json number,assignees,title \
    --jq '.[]|select((.assignees|length)==0)|select(.title|test("^(epic|OWNER DECISION)";"i")|not)|.number' 2>/dev/null | sed 's/^/2 /'; }
{ p0; p1; p2; } | awk 'NF==2' | sort -k1,1n -k2,2n -u > "$O/.pool"
# number -> priority, so the backlog can be kept in priority order after every append.
awk '{print $2"\t"$1}' "$O/.pool" > "$O/.prio"

n=0
while read -r N; do
  [ -z "${N:-}" ] && continue
  grep -qx "$N" "$O/checked.txt" 2>/dev/null && continue
  grep -qx "$N" "$O/dispatched.txt" 2>/dev/null && continue
  n=$((n+1)); [ "$n" -gt "$LIMIT" ] && break
  # ⛔ A pre-flight that ERRORED is not a verdict. Mark the issue checked only on a real
  # CLEAN/COLLISION answer: an empty VERDICT line (API failure, missing venv, timeout) used to
  # be written to checked.txt just the same, consuming the issue permanently and invisibly.
  # checked.txt=63 against candidates.txt=9 on 2026-10-13 is what that leak looks like.
  PRE="$("$SC/.venv/bin/python" tools/collision_preflight.py "$N" --repo . 2>&1)"
  V="$(printf '%s\n' "$PRE" | grep -E '^VERDICT' | head -1 | sed -E 's/.*VERDICT: (CLEAN|COLLISION).*/\1/')"
  case "$V" in
    CLEAN)
      echo "$N" >> "$O/checked.txt"
      echo "$N" >> "$O/candidates.txt"
      echo "[refresh] +#$N CLEAN -> backlog" ;;
    COLLISION)
      echo "$N" >> "$O/checked.txt"
      echo "[refresh] =#$N collision — a peer owns it, not queued" ;;
    *)
      echo "[refresh] ?#$N no verdict from the pre-flight — left unchecked for the next pass" ;;
  esac
done < "$O/.pool"
# Keep the backlog in PRIORITY order: `orch-loop.sh` takes its head, so this line decides which
# label the fleet works on. Anything not in .prio (an issue that lost its label) sorts last.
sort -u "$O/candidates.txt" | awk -v p="$O/.prio" '
  BEGIN{while((getline l < p) > 0){if(split(l,a,"\t")==2) pr[a[1]]=a[2]}}
  {printf "%d\t%d\n", ($1 in pr)?pr[$1]:9, $1}' \
  | sort -k1,1n -k2,2n | cut -f2 > "$O/candidates.txt.new" && mv "$O/candidates.txt.new" "$O/candidates.txt"
echo "[refresh] checked $n; backlog now $(wc -l < "$O/candidates.txt" | tr -d ' ') issues; head=$(head -1 "$O/candidates.txt" 2>/dev/null)"
