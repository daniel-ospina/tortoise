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
#
# ENFORCEMENT HOME: this script runs as a step of the `manifest-integrity` job in
# .github/workflows/python-ci.yml, which is a `needs:` of the REQUIRED `python-ci-gate`. It used to
# have a workflow of its own (`harness-single-source.yml`); that was WRONG — a path-scoped workflow
# is not a required context (it cannot be one: it does not run at all on an unrelated PR, so
# `check-success=` on it would block every merge), which meant the guard could be red and the merge
# queue would still take the PR. Riding the aggregate that is already required is the only shape in
# which this check actually blocks anything.
set -u

DIR="${1:-operations/orchestrator-harness}"
# ⛔ AN ALLOWLIST, NOT A SUFFIX DENYLIST (review finding). The intended state is EXACTLY these files,
# so anything else is a finding by construction. A denylist on `*.sh` let `lane.sh.bak`,
# `merged-not-closed.sh.orig`, `LANE.SH` (a macOS case-insensitive volume makes that the real file)
# and a SYMLINK to an outside directory all pass as a clean pointer — and this fleet already
# deploys a symlink farm on the live box, so that last one is not hypothetical.
#
# TWO DELIBERATE, TRACKED exceptions — both are UNPORTED fixes whose only copy is here, so deleting
# them would be the "silent destruction of work" this retirement must not commit:
#   * `merge-one.sh` — the #8484 detached-reviewer sandbox plus the #8602 `reviewer_prompt()` and the
#     #8612 `verdict_kind()` / marker pair. Retained as a REFERENCE EXCERPT: it cannot execute here
#     (the `spawn-gate.sh` it sources was retired with the copy), which is why its exec bit is
#     cleared and its header says so. Its contracts are pinned by
#     tests/test_orchestrator_harness_retained.py.
#   * `merged-not-closed.sh` — the instrument answering "which open issues already have a merged
#     PR?". agent-infra does NOT have it (verified against agent-infra `main`, 2026-10-19: no path
#     matches `merged-not-closed`, and agent-infra#2160 — which carries it plus its hermetic test —
#     is still OPEN). Its guard is the same test file. Delete both here, and remove them from
#     ALLOWED, on the day that port lands.
ALLOWED="README.md merge-one.sh merged-not-closed.sh"

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

# ⛔ NORMALISE $DIR TO THE FORM GIT PRINTS (review finding). `git ls-files` prints paths relative to
# the REPO ROOT (`--full-name` below makes that independent of the cwd), but the allowlist test used
# to strip the caller's $DIR verbatim. So `./operations/orchestrator-harness`,
# `operations/orchestrator-harness/` and `"$PWD/operations/orchestrator-harness"` — all three natural
# hand-run spellings, all three accepted by git — left `rel` fully prefixed, missed `ALLOWED`, and
# reported the CLEAN tree as a fork (measured: 2 findings, with the deliberately allowed
# `merge-one.sh` named as "FORK: harness script reappeared"). A guard that cries fork on a pristine
# tree destroys the trust the finding depends on. Resolving through `cd`/`pwd -P` normalises every
# spelling (`./`, a trailing slash, an absolute path, `..` segments) to one physical path, and both
# sides of the comparison then come from git's own root (`--show-toplevel`, also physical).
top="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "UNKNOWN: git cannot name the work tree root — cannot enumerate $DIR" >&2
  exit 2
}
abs="$(cd "$DIR" 2>/dev/null && pwd -P)" || {
  echo "UNKNOWN: cannot resolve $DIR — cannot enumerate it" >&2
  exit 2
}
case "$abs/" in
  "$top"/*) DIRROOT="${abs#"$top"/}" ;;
  *) echo "UNKNOWN: $DIR resolves to $abs, outside the work tree ($top) — cannot enumerate it" >&2
     echo "  This is NOT a pass: a copy elsewhere on disk is exactly what this guard is for." >&2
     exit 2 ;;
esac
DIRROOT="${DIRROOT%/}"
if [ -z "$DIRROOT" ]; then
  echo "UNKNOWN: $DIR resolves to the work tree root itself — refusing to call every file in the repo a fork" >&2
  exit 2
fi

# ⛔ A FAILED ENUMERATION IS NOT AN EMPTY ONE (review finding, and the same bug the docstring above
# records in its `2>/dev/null` form). Both `git ls-files` calls used to discard stderr and neither
# exit status was inspected, so a repo whose index cannot be read (a corrupt index, `detected dubious
# ownership` from git's safe.directory check, a git that refuses the checkout) yielded NO paths,
# `found` stayed 0, and the script printed "ok: … a pointer, not a copy" and exited 0 — a false PASS
# over a full copy, which is the one failure mode this guard exists to prevent. Reproduced with
# `GIT_INDEX_FILE=<garbage>`: exit 0 pre-fix, exit 2 here.
tracked="$(git ls-files --full-name -- "$DIRROOT")" || {
  echo "UNKNOWN: the git index could not be read — cannot enumerate $DIRROOT" >&2
  echo "  This is NOT a pass: an index that cannot be read is not an empty directory." >&2
  exit 2
}
untracked="$(git ls-files --full-name --others --exclude-standard -- "$DIRROOT")" || {
  echo "UNKNOWN: untracked files could not be enumerated — cannot check $DIRROOT" >&2
  echo "  This is NOT a pass." >&2
  exit 2
}

found=0
# ⛔ INDEX-BOUNDED, NOT A RECURSIVE WALK (docs/ops/load-policy.md, #1069). `git ls-files` plus
# `--others` answers exactly this question for tracked AND untracked files without walking anything.
while IFS= read -r f; do
  [ -n "$f" ] || continue
  rel="${f#"$DIRROOT"/}"
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
$(printf '%s\n%s\n' "$tracked" "$untracked" | sort -u)
EOF

if [ "$found" -gt 0 ]; then
  echo >&2
  echo "FAIL: $found harness file(s) under $DIR." >&2
  echo "  The harness is NOT maintained here. Port the change to:" >&2
  echo "    agent-infra/scripts/orchestrator-harness/" >&2
  echo "  then delete the copy. A fix applied to a copy is not a fix." >&2
  exit 1
fi

# ⛔ SAY WHAT IS ACTUALLY THERE. The ALLOWED entries are exceptions, so "no harness scripts present"
# would be a false statement from the instrument — and this guard exists because instruments that
# report the wrong thing get trusted.
present=""
for a in $ALLOWED; do
  [ "$a" = "README.md" ] && continue
  if [ -e "$DIR/$a" ]; then present="$present $a"; fi
done
if [ -n "$present" ]; then
  echo "ok: $DIR is a pointer, not a copy — with tracked PENDING PORT(s):$present (unported fixes whose only copy is here; see the README)"
else
  echo "ok: $DIR is a pointer, not a copy (no harness scripts present)"
fi
exit 0
