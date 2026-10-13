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
# ⛔ A FAILED listing is NOT an EMPTY one, and the reader below cannot tell them apart. `|| : >
# "$O/.pr_brs"` wrote an EMPTY file when `gh pr list` failed, and an empty `.pr_brs` makes the
# reaper's `has_pr` false for EVERY issue — so a failed listing un-blacklisted open issues that DO
# have a PR. orch-loop.sh skips only what is in dispatched.txt, so it then re-spawned a lane for an
# issue that already had one: duplicate work against the shared queue (agent-infra#1705). Write each
# list only when the listing SUCCEEDS and DELETE it when it fails, so "the read failed" (ABSENT) is
# distinguishable from "the answer is none" (PRESENT but EMPTY). The reaper refuses the whole pass
# unless both are present; a genuinely empty list is still a valid answer and still reaps.
if ! gh issue list --repo "$OR" --state open --limit 500 --json number --jq '.[].number' > "$O/.open_iss" 2>/dev/null; then
  rm -f "$O/.open_iss"
  echo "[refresh] WARN: open-issue listing FAILED — reaping pass will be skipped (an unreadable listing is not an empty one)"
fi
if ! gh pr list --repo "$OR" --state all --limit 500 --json headRefName --jq '.[].headRefName' > "$O/.pr_brs" 2>/dev/null; then
  rm -f "$O/.pr_brs"
  echo "[refresh] WARN: PR listing FAILED — reaping pass will be skipped (an unreadable listing is not an empty one)"
fi
python3 - "$O" <<'PY'
import os, re, subprocess, sys
O = sys.argv[1]
def lines(name):
    p = os.path.join(O, name)
    return [l.strip() for l in open(p)] if os.path.exists(p) else []
# ⛔ FAIL CLOSED. An ABSENT list means the read FAILED, which is not the same as "the answer is
# none". The capture above deletes the file when `gh` fails, so absence is the failure signal — and
# without this guard `brs == []` makes `has_pr` false for EVERY issue, un-blacklisting open issues
# that DO have a PR; orch-loop.sh (which skips only what is in dispatched.txt) then re-spawns a lane
# for them. Refuse the whole pass and leave the blacklist untouched, so the next cycle retries with a
# live listing. A PRESENT but EMPTY file is a real answer ("no PRs") and is still reaped.
# (agent-infra#1705)
missing = [f for f in (".open_iss", ".pr_brs") if not os.path.exists(os.path.join(O, f))]
if missing:
    print("[reap] SKIPPED: %s not readable — an unreadable listing is not an empty one; blacklist untouched"
          % " ".join(missing))
    raise SystemExit(0)
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

# pool = open unassigned P0-blocker ∪ P1-severe ∪ P2-moderate, minus epics/owner-decisions
#
# ⛔ SEVERITY FIRST, AGE SECOND. Each tier emits "<tier> <number>" and the sort keys on the tier
# before the number. A bare `sort -n` over the numbers ALONE ordered by ISSUE NUMBER — i.e. oldest
# first — discarding the tier the three queries had just computed. Measured 2026-10-13: 238
# pool-eligible P2-moderate sat ahead of 20 pool-eligible P0-blocker, so the P0s were unreachable
# and every worker the harness ran was a P2 (#8465). The tier is a RANK, not a label.
# Dedupe on the NUMBER, keeping the lowest tier: the sort already put an issue's best tier first, so
# `!seen[$2]++` drops the later, weaker duplicate rather than letting one issue occupy two slots.
{
  gh issue list --repo "$OR" --state open --limit 300 --label P0-blocker --json number,assignees,title \
    --jq '.[]|select((.assignees|length)==0)|select(.title|test("^(epic|lane|OWNER DECISION|decision|analysis|investigation)";"i")|not)|"0 \(.number)"' 2>/dev/null
  gh issue list --repo "$OR" --state open --limit 200 --label P1-severe --json number,assignees,title \
    --jq '.[]|select((.assignees|length)==0)|select(.title|test("^(epic|OWNER DECISION)";"i")|not)|"1 \(.number)"' 2>/dev/null
  gh issue list --repo "$OR" --state open --limit 200 --label P2-moderate --json number,assignees,title \
    --jq '.[]|select((.assignees|length)==0)|select(.title|test("^(epic|OWNER DECISION)";"i")|not)|"2 \(.number)"' 2>/dev/null
} | sort -k1,1n -k2,2n | awk '!seen[$2]++ {print $2}' > "$O/.pool"

n=0
while read -r N; do
  [ -z "${N:-}" ] && continue
  grep -qx "$N" "$O/checked.txt" 2>/dev/null && continue
  grep -qx "$N" "$O/dispatched.txt" 2>/dev/null && continue
  n=$((n+1)); [ "$n" -gt "$LIMIT" ] && break
  # ⛔ A pre-flight that ERRORED is not a verdict — and a COLLISION is not terminal. `echo "$N" >>
  # checked.txt` used to run BEFORE the verdict was read, so a pre-flight that produced no VERDICT
  # line (API failure, missing venv, timeout) blacklisted the issue forever, invisibly: checked.txt
  # suppresses it, and the reaper above only walks dispatched.txt, so nothing ever removed the entry.
  # A COLLISION was permanent the same way, though a collision is transient by nature (the colliding
  # work lands). checked.txt is therefore written ONLY on the verdict that terminates the issue —
  # CLEAN, which is queued. Everything else is left unchecked, so the next pass re-attempts it.
  # (agent-infra#1716)
  PRE="$("$SC/.venv/bin/python" tools/collision_preflight.py "$N" --repo . 2>&1)"
  V="$(printf '%s\n' "$PRE" | grep -E '^VERDICT' | head -1 | sed -E 's/.*VERDICT: (CLEAN|COLLISION).*/\1/')"
  case "$V" in
    CLEAN)
      echo "$N" >> "$O/checked.txt"
      echo "$N" >> "$O/candidates.txt"
      echo "[refresh] +#$N CLEAN -> backlog" ;;
    COLLISION)
      echo "[refresh] =#$N collision — a peer owns it, retried next pass" ;;
    *)
      echo "[refresh] ?#$N no verdict from the pre-flight — left unchecked for the next pass" ;;
  esac
done < "$O/.pool"
# Dedupe WITHOUT re-sorting. orch-loop.sh consumes this file top-down and dispatches the first
# BATCH entries, so its ORDER IS the dispatch priority. `sort -n -u` re-ordered the ENTIRE backlog
# by issue number, which is what put 238 P2s ahead of 20 P0s (#8465) — the same defect as the pool
# sort above, and it reappeared here on every cycle.
#
# This file is FIFO. The pool above is severity-ordered when it is appended, but an entry added on a
# LATER cycle joins at the BACK, behind whatever is already queued — so a P0 discovered while P2s sit
# at the head waits for them instead of displacing them. That residual is deliberate and bounded: the
# refresh only runs while the backlog is below 6 (orch-loop.sh) and BATCH is 2, so at most 5 entries
# can be ahead of it. The TIME that costs is contingent, not a ceiling: 5 entries at 2 per cycle is 3
# cycles only if the capacity gate is open each cycle — a load/lane/swap/memory gate that holds
# dispatch delays it further. The ENTRY bound is what the refresh guarantees; do not read a wall-clock
# ceiling into it. Carrying the tier through orch-loop.sh's reader would remove the residual at the
# cost of a second ordering surface; not worth it for a bounded handful of entries.
#
# `mv` is atomic within the directory (rename(2)), so a concurrent reader sees the old file or the new
# one, never a torn one; `&&` keeps a failed awk from truncating the live backlog. One deliberate
# trade: `mv` swaps the inode, so the target's mode is replaced by the temp's umask-derived one,
# whereas the old `sort -o` preserved it. The file holds only public issue numbers and its mode is
# never set deliberately, so 644 is not a property anything depends on.
awk '!seen[$0]++' "$O/candidates.txt" > "$O/.cand" && mv "$O/.cand" "$O/candidates.txt"
echo "[refresh] checked $n; backlog now $(wc -l < "$O/candidates.txt" | tr -d ' ') issues"
