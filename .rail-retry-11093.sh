#!/usr/bin/env bash
# rail-retry-11093.sh — keep attempting the mandated merge rail for PR #11093
# until GitHub gives the `python-ci-gate` lane a runner slot.
#
# WHY THIS EXISTS (measured on this lane, 2026-11-06):
#   `Python CI` run 42210969880 at head 5885a936 sat `pending` with ZERO jobs —
#   a repo-wide PR-queue starvation victim (tools/ci_queue_health.py:
#   `PR-BACKLOGED(queued≈52, pr_running≈4, oldest≈18.8h)`). A zero-job run is
#   NOT in flight (admin-merge.sh #1623) so the rail does not wait for it; it
#   blocks immediately with "no run of the lane actually TESTED head ...", which
#   is CORRECT: merging a tortoise change with no Python suite executed would be
#   exactly the "false green from capacity" the rail exists to prevent
#   (docs/scoping/2026-09-26-5215-merge-throughput.md TH5).
#
#   So the only correct move is to retry the rail until the lane runs. Each
#   attempt re-reads the live head, so a head that moves under us is caught by
#   the rail's own SHA binding; the rail's preconditions are the gate — this loop
#   administers no gate of its own and can only merge when the rail is satisfied.
#
# Bounds: 36 attempts x 600 s = 6 h, then it exits and says so. Every attempt's
# full rail output is appended to the log, so the last state is always readable.
set -u
cd /tmp/cell-t3/.worktrees/prd-11093 || exit 1
LOG=/tmp/rail-11093-retry.log
ATTEMPTS=36
SLEEP=600

for i in $(seq 1 "$ATTEMPTS"); do
  {
    printf '\n===== attempt %s/%s %s =====\n' "$i" "$ATTEMPTS" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >>"$LOG"
  out="$(scripts/admin-merge.sh 11093 2>&1)"
  rc=$?
  printf '%s\n' "$out" >>"$LOG"
  printf '===== attempt %s exit=%s =====\n' "$i" "$rc" >>"$LOG"
  if [ "$rc" -eq 0 ]; then
    printf 'RAIL-RETRY: merged on attempt %s\n' "$i" >>"$LOG"
    exit 0
  fi
  # Capacity-only block -> keep waiting. Two capacity classes are expected on this
  # box and neither is a verdict on the change:
  #   * 'no run of the lane actually TESTED' — python-ci.yml never got a runner slot;
  #   * 'gh-limit: gh-core-floor' — the fleet's shared REST budget sat under the
  #     reserve this box keeps for WRITES (a merge has no second pool), so the rail
  #     could not READ the evidence. Nothing was sent; it must be retried, never
  #     read as a refusal.
  # Anything else is a real refusal: stop and leave the reason in the log rather
  # than looping over it.
  if ! printf '%s' "$out" | grep -qE 'no run of the lane actually TESTED|gh-core-floor'; then
    printf 'RAIL-RETRY: stopped on a NON-capacity refusal (attempt %s, exit %s)\n' "$i" "$rc" >>"$LOG"
    exit "$rc"
  fi
  sleep "$SLEEP"
done
printf 'RAIL-RETRY: exhausted %s attempts without a lane run; giving up\n' "$ATTEMPTS" >>"$LOG"
exit 1
