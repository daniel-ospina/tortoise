#!/usr/bin/env bash
# check-harness-fork.sh — FAIL if the orchestrator harness has been copied back into this repo.
#
# `operations/orchestrator-harness/` is a POINTER now (see its README). The harness lives in
# agent-infra/scripts/orchestrator-harness/, which is the single source of truth. This check is the
# enforcement half of that retirement: without it, the next well-meaning fix "in the copy that is
# here" recreates the three-generation fork that cost a week of hand-porting (measured 2026-10-19:
# three copies of eight files, agreeing on almost nothing, and the LIVE copy had no git at all).
#
# A README (or any non-script file) in that directory is the INTENDED state. A `.sh`, a
# `.plist.template`, or a `retired/` script is a fork.
#
# Exit: 0 clean · 1 a harness script reappeared · 2 UNKNOWN (cannot check — never a pass)
set -u

DIR="${1:-operations/orchestrator-harness}"

if [ ! -d "$DIR" ]; then
  # The pointer directory itself is gone. That is not a fork — but it also removes the signpost that
  # tells the next person where the harness went, so it cannot be a silent pass.
  echo "UNKNOWN: $DIR does not exist — the pointer to agent-infra/scripts/orchestrator-harness/ is missing" >&2
  echo "  This is NOT a pass: a retired location must still SAY where the code went." >&2
  exit 2
fi

found=0
# ⛔ INDEX-BOUNDED, NOT A RECURSIVE WALK (docs/ops/load-policy.md, #1069). `git ls-files` plus
# `--others` gives exactly the question this answers — "is a harness script present here?" — for
# tracked AND untracked files, without walking anything. The retired path is tiny, but the fleet's
# most expensive habit is an ignore-blind walk started at a repo root, so the primitive is chosen
# rather than argued about.
while IFS= read -r f; do
  [ -n "$f" ] || continue
  case "$f" in
    *.sh|*.plist.template)
      echo "FORK: harness script reappeared at $f" >&2
      found=$((found + 1)) ;;
  esac
done <<EOF
$( { git ls-files -- "$DIR" 2>/dev/null; git ls-files --others --exclude-standard -- "$DIR" 2>/dev/null; } | sort -u )
EOF

if [ "$found" -gt 0 ]; then
  echo >&2
  echo "FAIL: $found harness file(s) under $DIR." >&2
  echo "  The harness is NOT maintained here. Port the change to:" >&2
  echo "    agent-infra/scripts/orchestrator-harness/" >&2
  echo "  then delete the copy. A fix applied to a copy is not a fix." >&2
  exit 1
fi

echo "ok: $DIR is a pointer, not a copy (no harness scripts present)"
exit 0
