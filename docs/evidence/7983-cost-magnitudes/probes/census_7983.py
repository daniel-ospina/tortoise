#!/usr/bin/env python3
"""#7983 census — WHICH read-frequency instruments exist on this tree?

Answers the one question the issue's `f` indicator presumes: *is there anything
that persists which bytes were read?* Complements the `#7950` capstone census
(`docs/evidence/7950-capstone/s2-read-ledger-census.log`) by asking the two
questions that census did not:

  1. what the READ path returns (does a read have a `(label, key)` coordinate to
     record at all?), and
  2. what the RESIDENCY declaration is a fact about — the projection's OWN work
     (write side), or a reader's history?

Run:
    uv run python docs/evidence/7983-cost-magnitudes/probes/census_7983.py \
        > docs/evidence/7983-cost-magnitudes/census-7983.log
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]

#: Property names that would be a persisted read fact, if one existed.
#: No `\b` word boundaries: `git grep -E` is POSIX ERE and has no `\b`, so a
#: pattern carrying one silently matches NOTHING — a census that reports "no
#: matches" because its own regex was unsupported is the same class of false
#: negative as an uncapped grep. The names below are specific enough bare.
READ_FACT = re.compile(
    r"last_read\w*|last_accessed\w*|read_count|access_count|hit_count|"
    r"hot_fraction|hot_set|read_ledger|query_log|access_log|node_read")

#: Source roots. `docs/` and `.venv/` are excluded on purpose: a doc that SAYS
#: "read_count" is not an instrument, and a vendored library is not our code.
SRC_DIRS = ("tortoise", "supabase/migrations", "tools", "client", "apps")
#: The instrument THIS LANE adds is excluded from the censuses below, because
#: the question is what the tree held BEFORE it: a census that counts its own
#: answer can never find the gap it exists to record.
EXTRA = ("--include=*.py", "--include=*.sql", "--include=*.ts", "--include=*.js",
         "--exclude=read_ledger.py")


#: When `--base <sha>` is given the censuses run through `git grep` against that
#: COMMITTED tree, so the lane's own uncommitted change (the ledger and its hook)
#: cannot appear in a census of what the tree held before it. The pathspec form
#: is `git grep`'s; the flag form is ripgrep's.
BASE: str | None = None
EXCLUDE = ":(exclude)*read_ledger.py"


def grep(pattern: str, dirs: tuple[str, ...]) -> tuple[int, str]:
    if BASE:
        return run(["git", "grep", "-nE", pattern, BASE, "--", *dirs, EXCLUDE])
    return run(["grep", "-rnE", pattern, *EXTRA, *dirs])


def grep_file(pattern: str, path: str) -> tuple[int, str]:
    """A single-file grep that respects `--base` like `grep()` does."""
    if BASE:
        return run(["git", "grep", "-nE", pattern, BASE, "--", path])
    return run(["grep", "-nE", pattern, path])


def path_exists(path: str) -> bool:
    """Does the path exist in the tree being CENSUSED (base, or the worktree)?"""
    if BASE:
        rc, _out = run(["git", "cat-file", "-e", f"{BASE}:{path}"])
        return rc == 0
    return (ROOT / path).exists()


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def head(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    global BASE
    if "--base" in sys.argv:
        BASE = sys.argv[sys.argv.index("--base") + 1]
    rc, commit = run(["git", "rev-parse", "HEAD"])
    rc, branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    rc, when = run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"])
    print("#7983 census — read-frequency instruments, measured on this tree")
    print(f"Measured at: {branch.strip()}@{commit.strip()} on {when.strip()}")
    if BASE:
        rc, sha = run(["git", "rev-parse", BASE])
        print(f"Census tree: {BASE}@{sha.strip()} (committed tree — this lane's own "
              f"change is excluded by construction)")
    print("Excluded from the greps: *read_ledger.py — the instrument this lane adds; "
          "a census that counts its own answer cannot record the gap.")
    print("Run with: --base <sha> for the committed tree, bare for the working tree.")
    print("\nEvery grep below is UNCAPPED (no head/tail): a truncated completeness")
    print("check manufactures false confidence (AGENTS.md).")

    head("1. persisted per-key READ facts in product source")
    rc, out = grep(READ_FACT.pattern, SRC_DIRS)
    print(out or "(no matches)")
    print(f"(exit={rc})")

    head("2. a query log / read ledger table in the schema")
    rc, out = grep(r"query_log|query_telemetry|read_ledger|access_log|node_read|"
                   r"last_read|read_count", ("supabase/migrations",))
    print(out or "(no matches)")
    print(f"(exit={rc})")

    head("2b. the read-VOLUME meter that DOES exist (and what it cannot say)")
    rc, out = grep(r"retrieval_queries|retrieval_wall_ms|retrieval_leg_runs",
                   ("supabase/migrations", "tortoise"))
    print(out or "(no matches)")
    print("  -> #4489's per-(org, period) RETRIEVAL meter: HOW MANY reads, never "
          "WHICH\n     keys. It cannot produce f (which needs the set), and on "
          "self-hosted it is a\n     no-op unless a tally is armed "
          "(tortoise/retrieval_metering.py).")

    head("3. the residency declaration — WHAT is it a fact about?")
    rc, out = grep_file(r"^(class |def |    coverage|    hot_keys|    source)",
                        "tortoise/residency.py")
    print(out)
    rc, out = grep("declare_residency\\(", ("tortoise", "tools"))
    print("\nwriters of the declaration:\n" + (out or "(none)"))
    rc, out = grep("read_declaration\\(", ("tortoise", "tools"))
    print("\nreaders of the declaration:\n" + (out or "(none)"))

    head("4. the read surfaces, and whether a returned row carries a (label, key)")
    rc, out = grep_file(r"^    def (search|query|recall_state|recall_gaps|"
                        r"recall_subgraph|search_sessions|tortoise_fts_query)\(",
                        "tortoise/sdk.py")
    print(out)
    rc, out = grep_file(r"KEY_PROPERTY", "tortoise/cache_projection.py")
    print("\nthe (label, key) contract:\n" + out)
    rc, out = run(["uv", "run", "python", "-c",
                   "from tortoise.cache_projection import KEY_PROPERTY;"
                   "print(KEY_PROPERTY)"])
    print("\nKEY_PROPERTY resolved at import:\n" + out)

    head("5. the ledger this census justifies (shipped by this lane)")
    # `--base`-aware: on the COMMITTED tree this must be False — that is the
    # whole point of the census (nothing persisted per-key read state before
    # this lane). Reading the worktree here printed `True` for the lane's own
    # new file, inside the census that exists to show it did not exist.
    print(f"tortoise/read_ledger.py present: {path_exists('tortoise/read_ledger.py')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
