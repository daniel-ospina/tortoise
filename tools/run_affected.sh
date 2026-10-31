#!/usr/bin/env bash
# run_affected.sh — "what does my diff actually need to run?", answered locally (#10130).
#
# WHY THIS EXISTS. `tools/ci_selection.py` already answers the question, but its only
# consumers are CI internals, and reaching it requires knowing two things that are
# not guessable. Measured on #10118 (2026-10-27): CI selected 863 files for that diff
# while the suites it actually needed were 257 tests — every fix round was pushed to
# CI to get an answer instead of reasoned about locally.
#
# THE TWO TRAPS, BOTH MEASURED (they are also stated in the doc, not only here):
#
#   1. `--changed-files` takes the newline-separated LIST **on stdin** (`-`). Passing
#      a *path* to a list as the argument makes the path itself one changed file;
#      nothing in `SOURCE_PATTERNS` knows it, so the selection takes the deliberate
#      fail-closed FULL-matrix branch and answers `full: true, test_files: "ALL"`
#      (#9109) — an answer that reads as "this diff touches everything". That trap is
#      why this wrapper exists: it ALWAYS pipes the list on stdin, and it refuses the
#      path-as-argument shape rather than reproducing it. See the
#      `--changed-files -` line below — that is the only invocation form this script
#      may ever use, and `tests/test_run_affected.py` pins it.
#
#   2. The selection is SURFACE-based, and `core` is a 738-file surface. A large
#      number is the selector working as designed (a diff touching a shared module or
#      a docs-only diff still pulls its surface's smoke set), NOT a broken tool. The
#      one-line summary this script prints on stderr says how many files and which
#      surfaces produced them, so the number arrives with its explanation.
#
# THE RANGE IS A COMMIT RANGE (`git diff --no-renames --name-only <range>`), which is
# exactly what `python-ci.yml:272` computes — so uncommitted work is NOT in the answer.
# That is the one way this tool could UNDER-select, so a dirty worktree is WARNED about
# by name (never silently added: the caller may be mid-edit, and `--files-from` is the
# explicit way to include uncommitted paths). `--no-renames` is deliberate, not a
# default: with rename detection ON a moved `tortoise/sdk.py` disappears from the list
# and the selection narrows to `core` instead of the full matrix its blast radius needs
# (CI uses the same flag for the same reason, #4378).
#
# WHAT IT DOES NOT DO. It never runs the `test-slow` or `test-carve-out` legs: those
# need the docker FalkorDB lane / the embedded carve-out targeting (see AGENTS.md →
# Testing), so the summary NAMES the files they would run and leaves running them to
# CI or an explicit invocation. It also never moves a test file between surfaces —
# that is `config/ci-surfaces.yml`'s curation decision (#10127), not a tool's.
#
# USAGE
#
#   tools/run_affected.sh                      # print the selected test files (default range)
#   tools/run_affected.sh --print              # same, explicit
#   tools/run_affected.sh --summary            # machine-readable counts only
#   tools/run_affected.sh --run                # uv run pytest <the selected files>
#   tools/run_affected.sh --run -- -x -q       # extra pytest args after `--`
#   tools/run_affected.sh v1.2.0              # a bare revision means `<rev>...HEAD`
#   tools/run_affected.sh origin/main~10...HEAD
#   tools/run_affected.sh --files-from /tmp/changed.txt
#   tools/run_affected.sh --allow-full         # accept a `full: true` selection
#
# DEFAULT RANGE: `origin/main...HEAD` (three-dot = merge-base, so commits landing on
# main since your branch forked do not appear as "your" changed files).
#
# EXIT CODES (distinct so a caller can tell a refusal from an empty answer from a red
# test run):
#
#   0  selection printed / ran; also the "the range has no changed files" case
#   2  usage error — unknown option, two modes, bad/missing range, `--files-from`
#      that does not exist, or a POSITIONAL ARGUMENT THAT IS A PATH (the #9109 trap;
#      use `--files-from`)
#   3  REFUSED: the selector answered `full: true` and `--allow-full` was not given
#   4  the selector itself failed (non-zero, or unparsable JSON)
#   *  with `--run`: pytest's own exit code, unchanged
#
# Pinned by tests/test_run_affected.py (registered in config/ci-surfaces.yml). The
# behavioural doc lives in docs/ci/affected-test-selection.md.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# A range operand is a SHELL argument, so a relative one is relative to where the caller
# invoked the tool. Keep that directory: after `cd` below, `[[ -e "$RANGE" ]]` would only
# ever match a path that happens to exist at the repo root — and the trap shape
# (`mylist.txt` beside the caller) would then be reported as "not a git range".
CALLER_PWD="$PWD"
cd "$REPO_ROOT"

DEFAULT_RANGE="origin/main...HEAD"
MODE="print"
MODE_SET=""
ALLOW_FULL=0
FILES_FROM=""
FILES_FROM_GIVEN=0
RANGE="$DEFAULT_RANGE"
RANGE_GIVEN=0
PYTEST_ARGS=()

usage() {
  sed -n '/^# USAGE$/,/^# Pinned by/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

die() {
  local code="$1"; shift
  printf 'run_affected: %s\n' "$*" >&2
  exit "$code"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --print|--summary|--run)
      [[ -z "$MODE_SET" || "$MODE_SET" == "$1" ]] \
        || die 2 "pick ONE of --print / --summary / --run (got $MODE_SET and $1)"
      MODE="${1#--}"; MODE_SET="$1"; shift ;;
    --allow-full) ALLOW_FULL=1; shift ;;
    --files-from)
      [[ $# -ge 2 ]] || die 2 "--files-from needs a FILE operand"
      [[ -n "$2" ]] || die 2 "--files-from needs a NON-EMPTY FILE operand — an empty \
operand would silently fall through to the range branch and answer a different question"
      FILES_FROM="$2"; FILES_FROM_GIVEN=1; shift 2 ;;
    --range)
      [[ $# -ge 2 ]] || die 2 "--range needs a RANGE operand"
      [[ "$RANGE_GIVEN" == "0" ]] || die 2 "two ranges given (--range $RANGE and '$2')"
      RANGE="$2"; RANGE_GIVEN=1; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    --) shift; PYTEST_ARGS=("$@"); break ;;
    -*) die 2 "unknown option '$1' (try --help)" ;;
    *)
      [[ "$RANGE_GIVEN" == "0" ]] || die 2 "two ranges given ('$RANGE' and '$1')"
      RANGE="$1"; RANGE_GIVEN=1; shift ;;
  esac
done

[[ "$MODE" == "run" || ${#PYTEST_ARGS[@]} -eq 0 ]] \
  || die 2 "extra pytest arguments need --run (got them with --$MODE)"
if [[ "$FILES_FROM_GIVEN" == "1" && "$RANGE_GIVEN" == "1" ]]; then
  die 2 "--files-from and a range are two different sources of truth — pick one \
(got list '$FILES_FROM' and range '$RANGE')"
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/run-affected.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT
CHANGED="$TMP_DIR/changed.txt"
SELECTION="$TMP_DIR/selection.json"
SELECTION_ERR="$TMP_DIR/selection.err"
PATHS="$TMP_DIR/paths.txt"

if [[ -n "$FILES_FROM" ]]; then
  # The list ITSELF is read here — never handed to the selector as an argument. This
  # is the seam a caller with a hand-written list uses, and the seam the tests use so
  # they do not need a throwaway branch to exercise a diff shape.
  # The list is the caller's path first (`mylist.txt` beside them), then repo-relative
  # — the refusal above tells a caller-relative spelling to be used with --files-from,
  # so following that advice verbatim must work.
  if [[ -f "$FILES_FROM" ]]; then
    cp "$FILES_FROM" "$CHANGED"
  elif [[ -f "$CALLER_PWD/$FILES_FROM" ]]; then
    cp "$CALLER_PWD/$FILES_FROM" "$CHANGED"
  else
    die 2 "--files-from: no such file: $FILES_FROM"
  fi
else
  # A RANGE, not a path. `foo...bar` is already a range; a bare revision means
  # `<rev>...HEAD`; anything else that EXISTS on disk is the #9109 trap (a file
  # holding the list), which this tool refuses rather than reproducing. The existence
  # test is made against the CALLER's directory as well as the repo root, because a
  # relative operand is caller-relative — `mylist.txt` beside a caller in another
  # directory is the shape the trap actually takes.
  if [[ "$RANGE" != *".."* ]]; then
    if git rev-parse --verify --quiet "${RANGE}^{commit}" >/dev/null 2>&1; then
      RANGE="${RANGE}...HEAD"
    elif [[ -e "$RANGE" || -e "$CALLER_PWD/$RANGE" ]]; then
      die 2 "'$RANGE' is a path, not a git range. Tools that pass a PATH to \
\`--changed-files\` make the path itself a changed file, which fail-closes to the FULL \
matrix (#9109) — the false 'your diff touches everything' answer. This script reads the \
LIST itself: use --files-from $RANGE"
    else
      die 2 "'$RANGE' is neither a git revision nor a range (try: git fetch origin main; \
default range is $DEFAULT_RANGE)"
    fi
  fi

  # ⛔ `--no-renames`, like `python-ci.yml:272`: with rename detection ON, a moved
  # `tortoise/sdk.py` is reported only as `tortoise/sdk_impl.py`, and the old path — the
  # SHARED_MODULE entry whose blast radius is the whole matrix — vanishes from the list.
  # The selection then answers a NARROWER set than CI does (measured: core/684 instead of
  # full, losing every surface-specific guard for the moved code).
  if ! git diff --no-renames --name-only "$RANGE" > "$CHANGED" 2>"$TMP_DIR/git.err"; then
    cat "$TMP_DIR/git.err" >&2
    die 2 "git diff --name-only $RANGE failed (is 'origin/main' fetched? try: git fetch origin main)"
  fi

  # A commit-range diff does NOT see the working tree, and silence about that is the one
  # way this tool can UNDER-select: a lane with uncommitted edits gets the last committed
  # answer, runs it, and the edit's blast radius was never computed (this is the harm the
  # doc names — "a subset that misses a test is worse than a subset that runs one
  # unnecessarily"). Warn, never silently add: the caller may be mid-edit on purpose, and
  # `--files-from` is the explicit way to include uncommitted work.
  DIRTY="$(git status --porcelain 2>/dev/null || true)"
  if [[ -n "$DIRTY" ]]; then
    DIRTY_N=$(printf '%s\n' "$DIRTY" | wc -l | tr -d ' ')
    # `awk 'NR<=3'`, not `| head -3`: under `set -o pipefail` a `head` that exits first
    # SIGPIPEs the writer, the assignment's status goes non-zero and `set -e` kills the
    # script — in exactly the large-dirty-worktree case this warning exists for.
    DIRTY_SAMPLE=$(printf '%s\n' "$DIRTY" | awk 'NR<=3 {print $NF}' | paste -sd, - \
      | sed 's/,/, /g')
    printf '%s\n' "run_affected: WARNING — $DIRTY_N uncommitted path(s) are NOT in this selection \
($DIRTY_SAMPLE$( (( DIRTY_N > 3 )) && printf ', …' || true)); commit, or pass a list that \
includes them with --files-from" >&2
  fi
fi

if [[ ! -s "$CHANGED" ]]; then
  printf 'run_affected: %s\n' \
    "the range produced NO changed files — nothing to run (range: $RANGE${FILES_FROM:+ list: $FILES_FROM})" >&2
  exit 0
fi

# ⛔ THE LIST GOES IN ON STDIN. `--changed-files -` is the ONLY invocation form this
# script may use; `--changed-files <path>` is the #9109 fail-closed trap.
if ! uv run --quiet python tools/ci_selection.py --changed-files - \
      < "$CHANGED" > "$SELECTION" 2> "$SELECTION_ERR"; then
  cat "$SELECTION_ERR" >&2
  die 4 "the selector (tools/ci_selection.py) failed — see its output above"
fi
# Keep the selector's own diagnostics visible: when it answers `full: true` it says
# WHY on stderr (a shared module, a linter-policy file, an unknown path) and that
# sentence is the reason the refusal below quotes.
[[ -s "$SELECTION_ERR" ]] && cat "$SELECTION_ERR" >&2

uv run --quiet python - "$SELECTION" "$MODE" "$ALLOW_FULL" "$PATHS" <<'PY'
"""Report / refuse / emit the selection. Exit 3 = the `full: true` refusal."""
import json
import os
import sys

selection_path, mode, allow_full, paths_out = sys.argv[1:5]
try:
    with open(selection_path, encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, json.JSONDecodeError) as exc:
    # Exit 4 is the documented "the selector itself failed" code — an unparsable
    # payload must land on it, not on a Python traceback with exit 1.
    print(f"run_affected: the selector returned unreadable/unparsable JSON: {exc}",
          file=sys.stderr)
    raise SystemExit(4)
if not isinstance(data, dict):
    print(f"run_affected: the selector returned {type(data).__name__}, not a selection",
          file=sys.stderr)
    raise SystemExit(4)

full = bool(data.get("full"))
files = data.get("test_files")
surfaces = data.get("surfaces") or []
slow_selected = data.get("slow_selected") or []
carve_out_run = bool(data.get("carve_out_run"))

if full:
    if allow_full != "1":
        print(
            "run_affected: REFUSING — the selector answered `full: true`, which means "
            "the WHOLE matrix, not this diff's tests.",
            file=sys.stderr,
        )
        print(
            "  It is a BLAST RADIUS, not a broken tool: a SHARED_MODULES path (e.g. "
            "tortoise/sdk.py), a linter-policy file, a schedule/push event, or a path "
            "no SOURCE_PATTERN knows is read by every leg — the reason is printed by "
            "the selector above.",
            file=sys.stderr,
        )
        print(
            f"  surfaces: {', '.join(surfaces) or '(none)'}",
            file=sys.stderr,
        )
        print(
            "  Re-run with --allow-full to accept it — that runs the whole suite.",
            file=sys.stderr,
        )
        raise SystemExit(3)
    # Accepted: pytest takes the directory, so `--print | xargs` still composes.
    targets = ["tests"]
    count_line = ("run_affected: FULL matrix (accepted with --allow-full) — every "
                  "surface, the whole tests/ suite")
else:
    if not isinstance(files, list):
        print(f"run_affected: the selector returned no test-file list: {files!r}",
              file=sys.stderr)
        raise SystemExit(4)
    # Entries are `tests/`-relative (a bare basename means tests/<name>.py; a subdir
    # path such as `longmem_eval/test_x.py` keeps its subdir). This is the same
    # composition python-ci.yml uses for its shard legs.
    targets = []
    for name in files:
        entry = name if name.endswith(".py") else f"{name}.py"
        targets.append(entry if entry.startswith("tests/") else f"tests/{entry}")
    count_line = (
        f"run_affected: {len(targets)} test files selected "
        f"(surfaces: {', '.join(surfaces) or '(none)'}; "
        f"test-slow: {len(slow_selected)} file(s) NOT run locally; "
        f"carve-out leg: {'yes' if carve_out_run else 'no'} NOT run locally)"
    )

# `--print | head -1` is a documented way to inspect the list, and a reader that closes
# early raises BrokenPipeError inside this interpreter — which escapes as EXIT 1 with a
# raw traceback, a code the exit table does not define. Emit through one guard that
# retires the broken stream quietly, so a piped inspection exits 0 like any other read.
try:
    if mode == "summary":
        print(f"full: {'true' if full else 'false'}")
        print(f"surfaces: {','.join(surfaces)}")
        print(f"test_files: {len(targets)}")
        print(f"slow_selected: {len(slow_selected)}")
        print(f"carve_out_run: {'true' if carve_out_run else 'false'}")
    else:
        for target in targets:
            print(target)

    # The summary is a ONE-LINE explanation of the size, on stderr so `--print`'s stdout
    # stays a clean list a caller can pipe (and so a big number arrives explained — trap 2).
    print(count_line, file=sys.stderr)
except BrokenPipeError:
    # Point stdout at /dev/null so the interpreter's shutdown flush cannot raise again.
    os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    raise SystemExit(0)

if mode == "run":
    with open(paths_out, "w", encoding="utf-8") as handle:
        handle.write("\n".join(targets) + "\n")
PY

if [[ "$MODE" == "run" ]]; then
  # bash-3.2-safe reading (no `mapfile`, which stock macOS bash does not have) and the
  # `set -u`-safe expansion of a possibly-empty array.
  SELECTED=()
  while IFS= read -r line; do
    [[ -n "$line" ]] && SELECTED+=("$line")
  done < "$PATHS"
  # `uv run pytest` — the same command AGENTS.md → Testing documents, so a lane that
  # iterates here is running what CI runs (minus the slow / carve-out / URI-requiring
  # legs, above). NOT `exec`: exec replaces this shell, so the EXIT trap that removes
  # $TMP_DIR would never run and every --run would leave a selection record in /tmp. As
  # the last statement under `set -e`, pytest's exit status is this script's exit status.
  uv run pytest ${SELECTED[@]+"${SELECTED[@]}"} ${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}
fi
