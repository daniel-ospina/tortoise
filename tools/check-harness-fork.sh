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
# ⛔ AN ALLOWLIST, NOT A SUFFIX DENYLIST (review finding). The intended state is EXACTLY these files,
# so anything else is a finding by construction. A denylist on `*.sh` let `lane.sh.bak`,
# `merged-not-closed.sh.orig`, `LANE.SH` (a macOS case-insensitive volume makes that the real file)
# and a SYMLINK to an outside directory all pass as a clean pointer — and this fleet already
# deploys a symlink farm on the live box, so that last one is not hypothetical.
#
# `merge-one.sh` is a DELIBERATE, TRACKED exception: it carries the #8484 detached-reviewer
# sandbox that has NOT been ported to agent-infra yet (the port is blocked on other lanes). Deleting
# the only copy of an unported fix is exactly the "silent destruction of work" this retirement must
# not commit, so the file stays until the port lands — and its name sits here, where a reader sees
# it, rather than in a commit message nobody re-reads.
ALLOWED="README.md merge-one.sh"

if [ ! -d "$DIR" ]; then
  # The pointer directory itself is gone. That is not a fork — but it also removes the signpost that
  # tells the next person where the harness went, so it cannot be a silent pass.
  echo "UNKNOWN: $DIR does not exist — the pointer to agent-infra/scripts/orchestrator-harness/ is missing" >&2
  echo "  This is NOT a pass: a retired location must still SAY where the code went." >&2
  exit 2
fi

# ⛔ OUTSIDE A WORK TREE NOTHING CAN BE ENUMERATED, so the old `git ls-files | sort` produced an EMPTY
# list, `found` stayed 0 and the guard printed "ok: … a pointer, not a copy" — over a directory full
# of harness scripts. That is the worst case in this whole change: the live `~/orchestrator` is
# precisely a copy with no git at all, and a guard that passes it is a guard that cannot fire.
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "UNKNOWN: not a git work tree — cannot tell a pointer from a copy ($DIR)" >&2
  echo "  This is NOT a pass (agent-infra#1712)." >&2
  exit 2
fi

found=0
# ⛔ INDEX-BOUNDED, NOT A RECURSIVE WALK (docs/ops/load-policy.md, #1069). `git ls-files` plus
# `--others` answers exactly this question for tracked AND untracked files without walking anything.
while IFS= read -r f; do
  [ -n "$f" ] || continue
  rel="${f#"$DIR"/}"
  case " $ALLOWED " in
    *" $rel "*) continue ;;
  esac
  # Not allowed: name what it is, so the fix is obvious.
  case "$rel" in
    *.sh) echo "FORK: harness script reappeared at $f" >&2 ;;
    *)    echo "UNEXPECTED: file at $f is not part of the retired pointer" >&2 ;;
  esac
  found=$((found + 1))
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

# ⛔ SAY WHAT IS ACTUALLY THERE. `merge-one.sh` is allowed, so "no harness scripts present" would be a
# false statement from the instrument — and this guard exists because instruments that report the
# wrong thing get trusted.
if [ -f "$DIR/merge-one.sh" ]; then
  echo "ok: $DIR is a pointer, not a copy — with ONE tracked pending port: merge-one.sh (#8484 detached-reviewer sandbox, not yet in agent-infra)"
else
  echo "ok: $DIR is a pointer, not a copy (no harness scripts present)"
fi
exit 0
