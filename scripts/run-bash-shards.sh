#!/usr/bin/env bash
# run-bash-shards.sh — #1369: THE single list of bash shards. Both lanes call this script.
#
# WHY THIS FILE EXISTS (the design, not just the code)
# ---------------------------------------------------
#   The defect (#1369) was a LANE SPLIT: the same check name covered two different bodies of
#   work, so a runner-only bash failure shipped green on the PR and reddened main
#   (#1348: scripts/record-review.test.sh exited 127 on the ubuntu runner).
#
#   The first repair added the missing suites to the PR lane and a guard that PARSED BOTH
#   WORKFLOW FILES to compare their suite lists. Two independent reviews then showed that no
#   text parser can prove execution: a suite named in a heredoc body, an `env:` value, a
#   `true ||` short-circuit, a `for f in a.sh …` continuation, `env bash x.sh`, `command bash
#   x.sh`, or a shard living in any job other than the named one all read as "covered".
#   Each hardened regex just moved the gap.
#
#   So the lists are not compared any more — they do not exist twice. This script IS the list,
#   and BOTH lanes invoke it:
#       PR lane  → .github/workflows/ci.yml      job `bash-suites`
#       main lane→ .github/workflows/ci-main.yml job `script-validate`
#   A suite added here is therefore in both lanes by construction. The class "a lane split that
#   a green check hides" cannot recur by DRIFT — only by deliberately un-wiring one lane, which
#   is a visible diff, and is what `scripts/check-ci-lane-parity.sh` now asserts (it checks the
#   two call sites and this file's own floor, instead of diffing shell text).
#
# CONTRACT
#   - Runs every shard; the accumulator keeps going so one failure cannot hide the others.
#   - Exits 1 if any shard failed, 0 otherwise.
#   - Hermetic: every suite builds its own temp repos/HOME/shims and touches no real state.
#   - `--list` prints the shard list, `--list-sweeps` the syntax-sweep globs. Both print the lists
#     this script EXECUTES, from one source each (the `run_shard` lines and SWEEP_GLOBS below), so
#     `check-ci-lane-parity` can read them without keeping a second, divergent copy.
#
# USAGE
#   bash scripts/run-bash-shards.sh                 # run them all
#   bash scripts/run-bash-shards.sh --list          # print the shard list, run nothing
#   bash scripts/run-bash-shards.sh --list-sweeps   # print the syntax-sweep globs, run nothing

set -uo pipefail

# The syntax-sweep targets. A DATA list, not two literal `for f in …` lines: `--list-sweeps` prints
# exactly what the loops below iterate, so the sweep half cannot be advertised and not performed
# (or performed and not advertised) without editing this one line.
SWEEP_GLOBS=("scripts/*.sh" "scripts/checkout-hygiene/*.sh")

if [ "${1:-}" = "--list" ]; then
  # Only `.sh` targets: the sentinel self-check below also calls run_shard, and it must not
  # appear in the list it is checked against.
  sed -n 's/^ *run_shard  *\([^ ]*\.sh\).*$/\1/p' "${BASH_SOURCE[0]}"
  exit 0
fi

if [ "${1:-}" = "--list-sweeps" ]; then
  printf '%s\n' "${SWEEP_GLOBS[@]}"
  exit 0
fi

# run_shard <path> — one shard. Kept as a helper so `--list` can read the same source lines
# (a list kept anywhere else would be the very duplication this file exists to remove).
# `shard_ran` is what makes the list SELF-CHECKING at runtime: the counters are compared
# against the listed count at the end, so a neutered helper (executing nothing while the list
# still reads its full count) fails loudly instead of reporting "all bash shards passed". A static check
# over this file's text could never catch that; running it can.
shard_errors=0
shard_ran=0
# One line on purpose: the execution and the counter that proves it happened are inseparable,
# so "stop executing but keep counting" is an explicit rewrite, not a one-line deletion.
run_shard() { shard_ran=$((shard_ran + 1)); bash "$1" || shard_errors=$((shard_errors + 1)); }

# Sweeping is counted too: a `for` loop whose BODY was neutered (`: ` instead of `bash -n`) would
# otherwise leave the loops — and therefore any glob floor in the guard — perfectly intact while
# checking no syntax at all. `swept` is asserted below, so the sweep loops must have done work.
swept=0
sweep_file() { swept=$((swept + 1)); bash -n "$1" || shard_errors=$((shard_errors + 1)); }

# Every listed target must be a real file INSIDE this checkout. Without this, a substitution that
# keeps the line count but points the list at a /tmp stub would report every shard as passed having
# run none of the suites — the counter arm alone cannot tell the difference.
checkout_real="$(pwd -P)"
while IFS= read -r t; do
  case "$t" in
    /*|*..*) echo "❌ shard target '$t' is not inside this checkout"; exit 1 ;;
  esac
  if [ -L "$t" ]; then
    echo "❌ shard target '$t' is a symlink — it may resolve outside the checkout"
    exit 1
  fi
  # The leaf check above is not enough: a symlinked PARENT directory (scripts/ -> elsewhere)
  # resolves the same target out of the checkout while the leaf itself is a regular file.
  # `cd … && pwd -P` resolves every component, so containment is judged on the real path.
  case "$(cd "$(dirname "$t")" 2>/dev/null && pwd -P)/" in
    "$checkout_real"/*) : ;;
    *) echo "❌ shard target '$t' resolves outside this checkout ($(cd "$(dirname "$t")" 2>/dev/null && pwd -P))"; exit 1 ;;
  esac
  if [ ! -f "$t" ]; then
    echo "❌ shard target '$t' does not exist — the list and the checkout disagree"
    exit 1
  fi
done <<<"$(sed -n 's/^ *run_shard  *\([^ ]*\.sh\).*$/\1/p' "${BASH_SOURCE[0]}")"

echo "── bash syntax sweeps ─────────────────────────────────────────────"
# The `bash -n` half: before #1369 the PR lane had NO shell-syntax gate at all — the purest
# instance of the split (a syntax error shipped green through the PR and reddened main).
# The inner loop expands each PATTERN deliberately (unquoted): quoting it would pass the literal
# string `scripts/*.sh` to `sweep_file`, so the sweep would report success having parsed nothing.
for pattern in "${SWEEP_GLOBS[@]}"; do
  for f in $pattern; do
    [ -f "$f" ] || continue
    sweep_file "$f"
  done
done

echo "── hermetic suites ────────────────────────────────────────────────"
# Every suite below is hermetic (temp repos / temp HOME / stubbed tools, no network).
#
# PROVENANCE, and one caveat: these lines were moved out of ci-main.yml's `script-validate`, so
# each keeps the note that workflow carried. Some of them are ALSO run by a dedicated per-PR job or
# step in ci.yml (each such line carries an "(also a per-PR … in ci.yml)" note). That
# duplication is deliberate here: this file is the ONE list both lanes run, so the PR lane runs
# those twice (once in their own job, once inside this list). Dropping them from this list to
# save the minutes would recreate the second, hand-kept list that #1369 exists to remove — the
# alternative is to retire the dedicated jobs and let this list be the PR lane's only coverage,
# which changes PR feedback granularity and is the owner's call, not a silent edit.
run_shard scripts/checkout-hygiene/deepseek-balance-watch.test.sh          # #476 balance poller (network-free seam)
run_shard scripts/checkout-hygiene/hub-state-check.test.sh                 # #1313 the DETECTOR (clean-but-stale PASS matrix)
run_shard scripts/checkout-hygiene/hub-worktree.test.sh                    # #1309/#1313 the RECOVERY half
run_shard scripts/install-launchd.test.sh                                  # #304 installer suite
run_shard scripts/pi-reap-idle.test.sh                                     # #469 session reaper (fake PS/KILL/DATE shims)
run_shard scripts/pi-task-session-prune.test.sh                            # #783 retention sweep (dry-run default)
run_shard scripts/pi-cache-reap.test.sh                                    # #1987 the machine-global uv/npm/pip caches: an over-bound fixture MUST come back under it (prune first, escalate only with no live holder, and prove the shrink)
run_shard scripts/pi-reap-worktrees.test.sh                                # #1095 worktree reaper gates
run_shard scripts/reap-lane-venvs.test.sh                                  # #1932 lane-.venv reaper: the liveness witnesses both ways (a live lane is KEPT, a dead one reaped), fail-closed on every unreadable probe, TOCTOU re-probe, and REAL ps/lsof/tmux fixtures
run_shard scripts/disk-reaper.test.sh                                     # #2032 ONE entry point over the three disk sinks: dry-run removes nothing, a LIVE-cwd worktree is never selected (and is reaped once the holder is gone), a MERGED checkout is, the size budget is honoured, a missing delegate fails closed, and the hourly timer renders armed + enabled
run_shard scripts/cleanup-stale-branches.test.sh                          # #1787 local-branch reaper gates (the squash-merge-aware three gates, dry-run default)
run_shard scripts/scratch-worktree.test.sh                                 # #1141 scratch-checkout helper
run_shard scripts/daily-backup.test.sh                                     # #907 the backup target is RESOLVED from the port it publishes and PROVEN to answer before any state exists; the LOAD_GATE_FORCE path runs; an incomplete backup dir is removed
run_shard scripts/record-review.test.sh                                    # #1348 THE INCIDENT: runner-only rc=127 shipped green
run_shard scripts/ci_runner_gate.test.sh                                   # #1704 an unreadable queue count / a failed runner enumeration must not read as healthy; #1719 the launcher, the farm and the script must agree
run_shard scripts/orchestrator-harness/cloud-cell-record-dep.test.sh       # #1717 cmd_check must refuse a cell without record-review.sh, and a record failure must PARK (not re-pay)
run_shard scripts/diff-normalize.test.sh                                  # #1362 D1 review-evidence diff normalizer (one impl, shared with the gate) (also a per-PR job in ci.yml)
run_shard scripts/cloud-cell-supervisor.test.sh                            # #1713 a keys.env top-level exit must not turn a failed cell start into a silent success; #1761 the controller read/start/stop come from the lock (not the tmux session) and exclude descendant processes; #1989 the cell doctor is required, launched, stopped and reported
run_shard scripts/orchestrator-bringup.test.sh                               # #1761 ONE entry point: a second controller for the SAME repo on this machine is refused (naming its pid) while a DIFFERENT repo is allowed, a stale record is reclaimed not blocked, and a configured repo path that is not on this machine is refused (#reseed repairs it without destroying lock/claim state)
run_shard scripts/orchestrator-harness/cloud-cell-doctor.test.sh            # #1989 the cell's reap/doctor/merge/hygiene roles: a lane whose child is still writing is NOT reaped, a human-attached/terminal lane is NOT touched, the rail is gated on a clean attestation at the head with no blind retry, an unreadable read is cannot-verify, and `status` FAILS a started cell whose roles never ran
run_shard scripts/cloud-cell-bringup.test.sh                               # #1718 the pushed gh token must not outlive the bring-up, on ANY exit
run_shard scripts/orchestrator-harness/refresh-scout.test.sh               # #1736 a cell on a non-Python repo must provision dispatch (or refuse loudly), never merge-only-and-silent
run_shard scripts/orchestrator-harness/refresh-quota.test.sh               # #2022/#1736 follow-up the refresh pass's per-pass API spend is BOUNDED: the board cache is enabled under the TOOL's seam (one rebuild per pass, not one per candidate), a starved pass judges fewer candidates and still queues work, and a pass that can buy nothing NAMES the budget instead of reporting an empty backlog — it still prunes the blacklist first (#1729 reconciled)
run_shard scripts/orchestrator-harness/sweep-quota.test.sh                 # #1762 the merge sweep's listing must be conditional (ETag/304) and defer on the RESPONSE rate-limit headers, not /rate_limit
run_shard scripts/orchestrator-harness/claim-exclusion.test.sh             # #1908 does a claim EXCLUDE a concurrent holder, per operation class — N processes raced at a barrier against a real receive-pack (hermetic bare origin)
run_shard scripts/orchestrator-harness/test-gates.sh                       # the dispatch gate: mp_reason(prs, lanes, workers) must count workers, not lanes alone (hermetic)
run_shard scripts/orchestrator-harness/selftest.sh                         # #1764 harness self-test incl. the CI-red derivation (offline; previously local-only — review round 1 note)
run_shard scripts/orchestrator-harness/gh-rest.test.sh                     # #1959 the gh REST adapter's TRANSLATION (argv in, argv out) for exactly the shapes the harness calls: `pr list --head` must not silently widen to the newest open PR, and `pr comment` must print a URL — both shipped green before, as P0s. Offline, a stub gh; unwired since #1962 landed it, so spawn-gate.test.sh's population check was red on main (review round 1)
run_shard scripts/orchestrator-harness/gh-limit.test.sh                     # #1988 ONE gh call in flight per box; the documented backoff order (retry-after, then the bucket's reset, then >=60s doubling to a bounded failure); the bucket NAMED on failure; conditional REST (ETag/304) for polling. Fake clock, stub gh, hermetic
run_shard scripts/orchestrator-harness/spawn-gate.test.sh                  # #1765 the ONE agent-invocation helper: the disk gate, the load gate, and the three callers' exit codes (hermetic; review round 2 — it was served by NO lane, so its own residual check could never fire)
run_shard scripts/orchestrator-harness/install-launchd-durability.test.sh  # #1975 the installer must not be able to disable the production controller: no persistent-disable verb, bootstrap→load fallback, pid+heartbeat asserted, and "not running with rc=0" is a FAILURE (hermetic: stub launchctl + throwaway HOME; §F runs on Linux too — plutil stubbed, ELDATO_ALLOW_NON_DARWIN=1 — and skips loudly only when the checkout is not a usable git work tree)
run_shard scripts/orchestrator-harness/claim-cwd.test.sh                   # 2026-10-18 the claim builder must work from a cwd it was never given (a launchd cell's WorkingDirectory is not a repo): `git -C` returned an EMPTY hash and refused EVERY candidate with "could not build claim commit" while the controller looked alive. Hermetic: the denial is a git shim doing a real getcwd, the shipped claims.sh does the rest
run_shard tests/admin-merge/run.sh                                         # #930 safe-admin-merge rail (also a per-PR job in ci.yml)
run_shard tests/atomic-land/run.sh                                         # #1367 atomic land unit (also a per-PR job in ci.yml)
run_shard tests/record-review/run.sh                                       # the lane-dimension carry (#6072/#6213/#4823): a base-only head move must CARRY, lane work must REFUSE, and a lying local base ref must REFUSE
run_shard tests/gh-shim/run.sh                                             # #984 argv-level gh shim (also a per-PR job in ci.yml)
run_shard tests/search-cost/run.sh                                         # #1069 fleet search cost (also a per-PR job in ci.yml)
run_shard tests/workflow-timeouts/run.sh                                   # #2055 every CI job declares its own bound: a job with no `timeout-minutes` inherits GitHub's 360-minute default, so ONE wedged shard blocks the merge queue for six hours and no PR in the repo can land — the land half of the autopilot, on both platforms
run_shard tests/beat-pair/run.sh                                           # #7957 H3 the recap/beat pair is BOUND, not merely written twice: a fresh recap beside a stale beat must SUPPRESS the act clause, never publish the previous tick's number (hermetic)
run_shard tests/heartbeat-fleet-advance/run.sh                             # #1991 the beat carries the FLEET ADVANCE standing order on BOTH bodies, in SIXTEEN fleet states (0 / 1 / 2 / 6 / 12 / 26 lanes, idle capacity, alert+capacity, dead panes, no-live-pi, questioning, alerting, queue-bottleneck, objective-at-risk, a warm tick, and the recovery-refusal path), each delivered body pinned to a byte-for-byte GOLDEN with the four sentences exactly once, the order EMBEDDED in the literals, the single send pinned, and a mutant proving the assertions are live (hermetic; drives the real tick against stubbed helpers, a stubbed screen and a recorded cmux send)
run_shard pi-bootstrap/tests/test-setup-no-nesting.sh                      # #449 setup regression (~2min; also a per-PR step in ci.yml)
run_shard pi-bootstrap/tests/test-cell-install.sh                          # #1723 a Linux cell receives the operational layer (the farmed scripts, the guards, the skills, record-review.sh) BY CONSTRUCTION, and refuses a partial one
run_shard pi-bootstrap/tests/test-farm-unfarm.sh                            # #1924 the farm has an UNFARM leg: two farmed scripts, one retired, re-applied — the retired one is gone from $DEST/scripts AND the other SURVIVED (an over-deleting removal leg is worse than the gap), a retirement a farm array still declares removes nothing and fails the run, an escaping entry is refused, and a cell is not certified while it still carries a retired artifact

echo "───────────────────────────────────────────────────────────────────"
# Runtime self-check 1: every listed shard must have been EXECUTED. This is the arm that catches
# a helper whose execution was disabled — a text-only floor would still see the same listed count.
listed="$(sed -n 's/^ *run_shard  *\([^ ]*\.sh\).*$/\1/p' "${BASH_SOURCE[0]}" | grep -c .)"
if [ "$shard_ran" -ne "$listed" ]; then
  echo "❌ executed $shard_ran of $listed listed shard(s) — the list and the execution disagree"
  exit 1
fi

# Runtime self-check 2: the SWEEP must have executed. A `for` loop kept intact while its body
# was replaced (`: ` for `bash -n`) still satisfies any textual glob count, so only a runtime
# counter proves the sweep did something.
if [ "$swept" -lt 1 ]; then
  echo "❌ the syntax sweep executed no file — the sweep loops are present but the body is not"
  exit 1
fi

# Runtime self-check 2b: the SWEEP ARM must still be wired. The `swept` counter above proves the
# loop body RAN, but a body rewritten as `swept=$((swept+1))` alone leaves that counter reading a
# full, non-zero count while checking no syntax at all — so the arm is proven the same way as the
# shard arm: run a file that MUST fail, and require the failure to be recorded.
sentinel_sweep="$(mktemp)"
printf 'if then\n' >"$sentinel_sweep"
before_sweep_err=$shard_errors
sweep_file "$sentinel_sweep"
rm -f "$sentinel_sweep"
if [ "$shard_errors" -ne $((before_sweep_err + 1)) ]; then
  echo "❌ the sweep arm is broken — a file with a syntax error would not fail this run"
  exit 1
fi
shard_errors=$before_sweep_err
swept=$((swept - 1))

# Runtime self-check 3: the FAILURE-RECORDING arm must still be wired. Without this, a rewrite
# that keeps counting executions but drops the `|| shard_errors=…` clause would report success
# over a failing shard — a green check hiding a red bash body, the class this file exists to
# close. Proven by running a sentinel shard that must fail.
sentinel="$(mktemp)"
printf 'exit 1\n' >"$sentinel"
before_err=$shard_errors
before_ran=$shard_ran
run_shard "$sentinel"
rm -f "$sentinel"
if [ "$shard_errors" -ne $((before_err + 1)) ]; then
  echo "❌ the failure-recording arm is broken — a failing shard would not fail this run"
  exit 1
fi
shard_errors=$before_err
shard_ran=$before_ran

if [ "$shard_errors" -gt 0 ]; then
  echo "❌ $shard_errors bash shard(s) failed"
  exit 1
fi
echo "✅ all $shard_ran bash shards passed ($swept file(s) syntax-checked)"
