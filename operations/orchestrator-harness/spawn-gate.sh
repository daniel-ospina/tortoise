#!/usr/bin/env bash
# spawn-gate.sh — the harness's ONE load gate, and its ONE `pi` invocation.
#
# Sourced (never executed) by orch-loop.sh, which gates its own DECISION to create a worker
# session, and by every script that STARTS an agent: lane.sh, merge-one.sh, fix-one.sh.
#
# Why this file exists (agent-infra#1720): the gate used to be a one-liner inside orch-loop.sh plus
# a copy-pasted `run_agent()` in three workers, and only the controller's decision sites consulted
# it — the `pi` calls themselves carried no gate at all. So `chain-<N>` (the primary first-review
# path for every own-lane PR, and the one path the controller deliberately stands down for because
# the chain holds the claim), `fix-one.sh` and `adopt-fix.sh` launched their agent onto whatever
# load existed when they got there: a lane dispatched on a quiet box opened its PR up to 90 minutes
# later and its reviewer landed on the spike the gate was written to prevent. README §4 said every
# spawn was gated; it was not, and three copies of the spawn logic is how that drifted.
#
# The invariant is now structural and self-testable: exactly ONE shipped file invokes `pi`
# (this one), it refuses above MAX_LOAD, and selftest.sh asserts both. A fourth launch path added
# tomorrow fails the self-test until it routes through run_agent().
set -u

# `is_num` is shared by every numeric coercion below AND by orch-loop.sh (which sources this file
# before its own threshold coercions). It lives here because the ceiling is derived at SOURCE time,
# below, and a guard that runs after the arithmetic it protects is not a guard (#1702).
is_num() { case "${1:-}" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }

# ── the ceiling ──────────────────────────────────────────────────────────────────
# Resolved HERE rather than in the caller so a worker started by hand — or by a tmux server whose
# environment predates the plist — still has a live ceiling: the launchd plist supplies MAX_LOAD to
# the daemon, but nothing guarantees it reached the tmux server that starts the workers. This is the
# same expression install.sh renders into the plist; selftest §7 asserts the two agree, so the
# ceiling the gate applies and the ceiling the daemon advertises cannot diverge silently.
#
# The core count is derived PORTABLY. `sysctl -n hw.ncpu` is macOS-only: on a Linux cell sysctl may
# be absent entirely (Debian's procps does not ship it any more) and `hw.ncpu` is a macOS key
# regardless, so the old `|| echo 4` silently pinned _NCPU=4 and MAX_LOAD=40 for EVERY cell (#1702).
_NCPU="$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
# Coerce immediately before the ONLY reads. A non-numeric _NCPU already breaks `$(( _NCPU * 10 ))`,
# and MAX_LOAD is supplied by the launchd plist: a typo there would OPEN the gate rather than close
# it, because `[ -ge ]` on a non-number is FALSE and a false test in the gate chain allows dispatch.
is_num "${_NCPU:-}"    || _NCPU=4
MAX_LOAD="${MAX_LOAD:-$(( _NCPU * 10 ))}"
is_num "${MAX_LOAD:-}" || MAX_LOAD=$(( _NCPU * 10 ))

# 1-min load average, integer. Empty when the probe fails (see can_spawn).
# THE FIELD DIFFERS: /proc field 1 IS the 1-min load, while macOS `sysctl vm.loadavg` prints
# `{ 1.70 1.90 2.00 }`, so field 2 is. One shared awk would therefore read the wrong field on one of
# the two platforms — the bug this function's test pins. The argument is a TEST SEAM (the call site
# passes nothing), the same idiom as orch-loop.sh's mp_reason().
load1() { # [<loadavg-file>]
  local p="${1:-/proc/loadavg}"
  if [ -r "$p" ]; then
    awk '{printf "%d\n", $1}' "$p" 2>/dev/null
  else
    sysctl -n vm.loadavg 2>/dev/null | awk '{print int($2)}'
  fi
}

# can_spawn <load> — the ONE load gate.
#
# The reading is an ARGUMENT, never a global: merge-one.sh and fix-one.sh already use `L` for their
# LOG PATH, so a gate that read a global `$L` would compare "/Users/…/logs/merge-42" against
# MAX_LOAD — a class of mistake the old shape invited and this one cannot express.
#
# Two callers, two readings, one predicate:
#   * orch-loop.sh's four decision sites pass the reading they cached for the cycle, so the `[cap]`
#     log line and the gate always report the same load;
#   * run_agent() below takes a FRESH reading immediately before it starts the agent — that is the
#     point, since a chain-<N> worker may have waited 90 minutes for its PR first.
#
# An empty reading is treated as 0 = allowed. That is the shipped, documented fail-open: if the
# load probe itself fails, the box must not stop merging entirely. It is NOT a licence to call this
# with no argument — selftest.sh pins that all four orch-loop.sh sites pass their reading.
can_spawn() { [ "${1:-0}" -lt "$MAX_LOAD" ]; }

# ── the ONE agent invocation ─────────────────────────────────────────────────────
# run_agent <seconds> <logfile> <promptfile> [label]
#
# Wall-clock limited: `pi -p` has no internal limit, so one hung model call holds a concurrency slot
# forever, and the caps count SESSIONS, so a single hang silently halves throughput. macOS ships no
# `timeout`/`gtimeout`, so this is done in bash: run the agent in the background with a watchdog
# subshell that kills it after N seconds. The stale-worker reaper in orch-loop.sh is the second line
# of defence.
#
# Gated: above MAX_LOAD it does not start the agent at all and returns AGENT_LOAD_REFUSED. That code
# is the caller's signal that NO AGENT RAN — distinct from "the agent ran and failed", because
# merge-one.sh records a `needsfix-<PR>-<head>` marker for the latter. Recording one for a load
# refusal would authorise a fixer for a review that never happened, burn a MAX_FIX_TRIES attempt and
# eventually park the PR (the same failure class as agent-infra#1716).
AGENT_LOAD_REFUSED=75
# Authoritative "an agent ran" flag, set by run_agent() itself. `rc == AGENT_LOAD_REFUSED` alone is
# ambiguous: `pi` shares the 0–255 exit-status namespace, so a pi that exited 75 for its own reason
# would be read as a load refusal — and a caller that acts on that (fix-one.sh skips its merge-chain
# re-run, merge-one.sh skips recording a verdict) would silently do the wrong thing. The flag is set
# only after the gate passed and pi was actually launched, so it cannot be produced by a refusal.
AGENT_STARTED=0
run_agent() { # run_agent <seconds> <logfile> <promptfile> [label]
  local t="$1" log="$2" prompt="$3" label="${4:-agent}" pid w rc l
  AGENT_STARTED=0
  l="$(load1)"
  if ! can_spawn "$l"; then
    echo "[load-gate] REFUSED $label: load=$l >= max_load=$MAX_LOAD — no agent started; the controller re-dispatches when load falls, and nothing was consumed" >> "$log"
    return "$AGENT_LOAD_REFUSED"
  fi
  AGENT_STARTED=1
  pi -p --provider deepseek --model deepseek-v4-flash --no-session "$(cat "$prompt")" >> "$log" 2>&1 &
  pid=$!
  ( sleep "$t"; kill -TERM "$pid" 2>/dev/null; sleep 15; kill -KILL "$pid" 2>/dev/null ) >/dev/null 2>&1 &
  w=$!
  wait "$pid" 2>/dev/null; rc=$?
  kill "$w" 2>/dev/null
  echo "[timeout] agent pid=$pid wall_limit=${t}s rc=$rc" >> "$log"
  return "$rc"
}
