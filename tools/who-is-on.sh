#!/usr/bin/env bash
# who-is-on — the human-facing "who is on #N?" verb (#4256).
#
# The ad-hoc way to ask whether an issue is staffed is one `gh` call, which
# enumerates REMOTE refs and PRs only. Work that lives only on this checkout —
# a branch that was never pushed, a worktree with uncommitted changes — is
# invisible to it, so it answers "nobody" for work that is in flight. This verb
# enumerates the LOCAL surfaces too and always exits 0 for a complete answer
# (a holder is information, not a collision/gate failure).
#
# Usage:
#   tools/who-is-on.sh <N> [--repo <path|owner/name>]      who holds issue #N (local + remote)
#   tools/who-is-on.sh --inventory [--limit N] [--dirty-only] [--detached] [--repo ...]
#                                               local-only branches + uncommitted work
#
# Other flags (shared with collision_preflight.py): --gh, --git, --timeout.
#
# NOT shared: --keywords / --min-keywords. #3504 deleted the lexical arm, so
# collision_preflight.py rejects them as a usage error (pinned by
# test_collision_preflight.test_keyword_flags_are_gone_not_merely_ignored) and
# neither does this wrapper: a flag that still parsed but did nothing would let
# a caller believe the vocabulary dimension was consulted when it no longer is.
#
# Exit codes: 0 = complete answer (with or without holders); 2 = a surface — or,
# for --inventory, a worktree's uncommitted state — could not be read (an
# incomplete answer is never "nobody", and an unreadable worktree is never
# "clean"); 3 = usage error. It never returns 1: a holder is information, not a
# gate failure.
#
# Implementation lives in tools/who_is_on.py; this wrapper exists so the verb is
# discoverable in tools/. It reuses tools/collision_preflight.py's surface
# enumeration — the gate's behaviour is unchanged and it is not replaced.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ⛔ THE INTERPRETER IS CHOSEN, NOT ASSUMED (#11202). `who_is_on.py` refuses a
# pre-3.12 interpreter (`from datetime import UTC` needs one), and this wrapper used
# to hand it whatever `python3` happened to be first on PATH — measured 2026-11-07 on
# the owner's Mac, where `python3` is 3.9:
#
#     $ tools/who-is-on.sh 11477 --repo daniel-ospina/tortoise
#     tools/who_is_on.py requires Python >= 3.12 (got 3.9) — run it as `uv run python …`
#     exit 1
#
# so the verb AGENTS.md documents returned an error where it owed an answer — the same
# "the gate cannot answer" consequence as the claim gate itself, one verb over. The
# interpreter that satisfies the module's own guard wins; `uv` (which owns the repo's
# pinned >= 3.12 environment) is the fallback, and `--project` selects THAT environment
# rather than whatever the caller's cwd happens to be.
if python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' \
        2>/dev/null; then
    exec python3 "$here/who_is_on.py" "$@"
fi
if command -v uv >/dev/null 2>&1; then
    exec uv run --quiet --project "$here/.." python "$here/who_is_on.py" "$@"
fi
# NEITHER. Say which one is missing, and use rc 2 — "could not check", which is this
# verb's class for a surface it could not read (an incomplete answer is never "nobody",
# and this one cannot even start). rc 3 is reserved for a malformed INVOCATION, and the
# flags here were fine: the RUNTIME is absent. (Pre-change the module's own guard exited
# 1, a code this verb documents it never returns, so that shape was wrong too.)
echo "who-is-on: needs Python >= 3.12 and neither a 3.12+ 'python3' nor 'uv' is on" \
     "PATH (python3 is $(python3 -V 2>&1 || echo 'absent')) — install uv." >&2
exit 2
