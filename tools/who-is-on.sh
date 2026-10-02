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
# Other flags (shared with collision_preflight.py): --gh, --git, --timeout,
# --keywords, --min-keywords.
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
exec python3 "$here/who_is_on.py" "$@"
