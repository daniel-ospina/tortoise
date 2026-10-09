#!/bin/sh
# sigpipe-hook.test.sh — does .husky/pre-commit's SIGPIPE guard block actually FIRE?
#
# Why this exists: that block was written with `... || exit 1` and the `else` accidentally
# glued onto the same line. Under `sh`, `else` is not a reserved word mid-simple-command, so
# it was consumed as an ARGUMENT to `exit`; `exit` then failed with "too many arguments", the
# guard's exit status was discarded, and the commit proceeded — with no error. `sh -n` passes
# on that file, so the defect is invisible to syntax checking and to reading the diff.
#
# The block is extracted from the REAL hook rather than copied here, so this test cannot
# drift from the code it guards. Three behaviours are pinned:
#   old guard      -> WARN and do NOT block (the local agent-infra checkout may lag)
#   new guard, clean   -> run, exit 0, no warning
#   new guard, idiom found -> exit 1, so the commit BLOCKS
#
# The stubs use `case`, not `printf | grep -q`: this directory is inside the guard's own
# scan set, and that pipeline is the defect the guard exists to find.
set -eu

HOOK=${HOOK:-.husky/pre-commit}
FAILS=0
PASSES=0

ok()   { PASSES=$((PASSES + 1)); }
fail() { FAILS=$((FAILS + 1)); printf 'FAIL: %s\n' "$1" >&2; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

# The block runs from a directory that has the scan-set file, because the hook reads it.
cp .sigpipe-grep-dirs "$WORK/"
mkdir -p "$WORK/stubs"

# An old guard: --help does not advertise --dirs and --dirs is refused.
printf '#!/bin/sh\ncase "$1" in --help) echo "usage: old"; exit 0;; --dirs) echo "unknown argument: --dirs" >&2; exit 2;; esac\nexit 0\n' > "$WORK/stubs/old"
# A current guard on a clean tree.
printf '#!/bin/sh\ncase "$1" in --help) echo "usage: guard [--dirs D]"; exit 0;; --dirs) exit 0;; esac\nexit 0\n' > "$WORK/stubs/clean"
# A current guard that FOUND the idiom.
printf '#!/bin/sh\ncase "$1" in --help) echo "usage: guard [--dirs D]"; exit 0;; --dirs) echo "FOUND idiom: entrypoint.sh"; exit 1;; esac\nexit 0\n' > "$WORK/stubs/detects"
chmod +x "$WORK"/stubs/*

# Extract the guard block from the real hook: the `if [ -f "$SIGPIPE_GUARD" ]` through its `fi`.
sed -n '/^if \[ -f "\$SIGPIPE_GUARD" \]; then/,/^fi$/p' "$HOOK" > "$WORK/block"
if [ ! -s "$WORK/block" ]; then
  echo "FAIL: could not extract the SIGPIPE guard block from $HOOK — has it been renamed?" >&2
  exit 1
fi

run_stub() { # <stub> -> writes rc to $WORK/rc, output to $WORK/out
  { printf 'SIGPIPE_GUARD="%s"\n' "$WORK/stubs/$1"; cat "$WORK/block"; } > "$WORK/run"
  set +e
  ( cd "$WORK" && sh run > out 2>&1 ); rc=$?
  set -e
  printf '%s\n' "$rc" > "$WORK/rc"
}

# 1. An out-of-date guard must WARN and let the commit through.
run_stub old
rc=$(cat "$WORK/rc")
if [ "$rc" = 0 ]; then ok; else fail "old guard: expected exit 0 (do not block), got $rc"; fi
case "$(cat "$WORK/out")" in
  *"too old"*) ok ;;
  *) fail "old guard: expected the 'too old' warning, got: $(cat "$WORK/out")" ;;
esac

# 2. A current guard on a clean tree must pass quietly.
run_stub clean
rc=$(cat "$WORK/rc")
if [ "$rc" = 0 ]; then ok; else fail "clean tree: expected exit 0, got $rc"; fi
if [ -s "$WORK/out" ]; then fail "clean tree: expected no output, got: $(cat "$WORK/out")"; else ok; fi

# 3. THE ONE THAT MATTERS MOST: a current guard that FOUND the idiom must BLOCK the commit.
#    (This is the assertion that would have caught the glued-`else` defect.)
run_stub detects
rc=$(cat "$WORK/rc")
if [ "$rc" = 1 ]; then ok; else fail "idiom found: expected exit 1 (BLOCK the commit), got $rc — the guard's verdict is being discarded"; fi
case "$(cat "$WORK/out")" in *FOUND*) ok ;; *) fail "idiom found: the guard's message was not surfaced" ;; esac

printf '\n%s assertion(s) passed, %s failed\n' "$PASSES" "$FAILS"
[ "$FAILS" = 0 ]
