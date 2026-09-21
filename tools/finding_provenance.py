#!/usr/bin/env python3
"""finding_provenance — a finding must carry the tree it was measured against,
and a stale measurement must FAIL, not be silently believed (#4290).

Why this exists
---------------
In one night three lanes reported a defect that was already fixed on
``origin/main`` — each measured against a stale worktree or an un-rebased
branch:

  * B5 reported ``config/surface-manifest.yml`` "does not exist on main" (it
    exists on main, 5,884 lines). A live follow-up would have been closed as
    MOOT.
  * B2 reported that ``tools/surface-guard.py::_fingerprint`` still used
    ``co_firstlineno`` and "blocks the gate" — already fixed by ``65b26f6c2``.
    An epic phase was sequenced as BLOCKING to re-implement a fixed defect.
  * B3 asked to re-sequence that same epic phase off the same stale evidence.

A finding is TRUE OF THE TREE IT WAS MEASURED ON and may be FALSE OF THE
PRODUCT. Nothing in the report said which tree that was, so the reader could
not tell a live defect from a six-day-old one without redoing the work.

The check that answers it is an ANCESTRY test, never an equality test:

    git merge-base --is-ancestor <fix-sha> <measured-sha>

Equality is the tempting wrong check — it passes only when the two SHAs are
identical, so a finding measured on a branch that *contains* the fix would be
flagged as stale, and the gate would be trained away. Ancestry asks the real
question: is the fix already inside the tree this finding was measured on?

What the gate refuses
---------------------
1. UNKNOWN PROVENANCE — no parseable ``Measured at:`` line, or the named SHA
   is not a commit in this repo. Unknown provenance is NOT a pass: a free-text
   field nothing validates is exactly the defect this tool exists to remove.
2. AMBIGUOUS PROVENANCE — more than one ``Measured at:`` line. One finding,
   one line; an appended second line must not leave a stale first line
   silently authoritative.
3. STALE — the measured tree does not contain the current base (default
   ``origin/main``). The finding may already have been fixed; re-verify.
4. PREDATES FIX — ``--fix <sha>`` was supplied and that commit is not an
   ancestor of the measured tree. The finding was measured before the fix.

``--emit`` adds a ``(DIRTY)`` marker (and a stderr warning) when the worktree
has uncommitted changes: HEAD then names a commit that does not describe the
files that were read. ``--validate`` reports the marker but does not refuse —
a finding about uncommitted state is legitimate; it just cannot be dated to a
clean commit.

Usage
-----
    # 1. Record provenance AT MEASUREMENT TIME (paste into the finding body):
    python3 tools/finding_provenance.py --emit

    # 2. Ask the cheap re-base question before reporting (#4290 ask 2):
    python3 tools/finding_provenance.py --checkout

    # 3. Gate a finding — the reader's check:
    python3 tools/finding_provenance.py --validate finding.md
    python3 tools/finding_provenance.py --validate finding.md --fix 65b26f6c2
    gh issue view 4009 --json body -q .body \\
        | python3 tools/finding_provenance.py --validate -

Provenance line format (ONE line, machine-produced by ``--emit`` — do not
hand-type the SHA)::

    Measured at: <ref-label>@<40-hex-sha> on <YYYY-MM-DD>[ (DIRTY)]

The trailing ``(DIRTY)`` is added by ``--emit`` when the worktree has
uncommitted changes, so the reader can see that HEAD does not describe the
files that were read.

Exit codes
----------
    0  CURRENT / ok                     (--checkout: current)
    1  STALE / UNKNOWN / PREDATES FIX   (--checkout: behind base)
    2  usage or environment error (not a git repo, unresolvable base, …)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import subprocess
import sys
from pathlib import Path

BASE_DEFAULT = "origin/main"

# ONE line, anchored. The ref label is PERMISSIVE (`\S+`): a legal git ref may
# contain `#`, `+`, `%`, `(`, `|`, non-ASCII, … — a narrow class made `--emit`
# print a line its own `--validate` then rejected, reddening a CURRENT finding
# on such a branch (review round 2). Comment injection from an untrusted label
# is defended at the REFLECTION POINT instead (the workflow indents the verdict
# block instead of fencing it), not by over-restricting the ref. Greedy `\S+`
# backtracks to the LAST '@' that leaves exactly 40 hex before " on <date>", so
# a ref containing '@' still resolves.
PROVENANCE_RE = re.compile(
    r"^Measured at:\s*(?P<ref>\S+)@(?P<sha>[0-9a-f]{40})"
    r"\s+on\s+(?P<date>\d{4}-\d{2}-\d{2})(?P<dirty>\s+\(DIRTY\))?\s*$",
    re.MULTILINE,
)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=False,
    )


def repo_root(start: Path | None = None) -> Path:
    """Repo root via git — robust to worktrees, symlinks, relative __file__."""
    r = _git(start or Path.cwd(), "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        # An unresolvable repo is an ENVIRONMENT error (exit 2), never the
        # exit-1 STALE/UNKNOWN verdict — a caller that branches on the code
        # must not read "could not measure" as "measured and stale".
        print(f"finding-provenance: not a git repository: {r.stderr.strip()}",
              file=sys.stderr)
        sys.exit(2)
    return Path(r.stdout.strip())


def _resolve_commit(root: Path, rev: str) -> str | None:
    """Full SHA of `rev` when it is a commit in this repo, else None."""
    r = _git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    if r.returncode != 0:
        return None
    return r.stdout.strip()


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool | None:
    """True/False from `git merge-base --is-ancestor`, None on tool error.

    Reflexive by construction: `--is-ancestor A A` is 0, so a finding measured
    exactly at the fix SHA is CURRENT — an equality check would get this right
    for the wrong reason and then get the *containing* case wrong.
    """
    r = _git(root, "merge-base", "--is-ancestor", ancestor, descendant)
    if r.returncode == 0:
        return True
    if r.returncode == 1:
        return False
    return None


def _behind(root: Path, older: str, newer: str) -> int | None:
    """Commits in `newer` missing from `older`."""
    r = _git(root, "rev-list", "--count", f"{older}..{newer}")
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:  # pragma: no cover — git always prints an int
        return None


def _emit(root: Path, base_ref: str) -> int:
    head = _resolve_commit(root, "HEAD")
    if head is None:
        print("finding-provenance: cannot resolve HEAD", file=sys.stderr)
        return 2
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    label = branch if branch and branch != "HEAD" else "HEAD"
    today = _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d")
    # A dirty worktree means HEAD does NOT describe the tree that was read. The
    # marker rides IN the emitted line so it travels with the finding to the
    # reader — a stderr warning only ever reaches the reporter's terminal.
    dirty = bool(_git(root, "status", "--porcelain").stdout.strip())
    line = f"Measured at: {label}@{head} on {today}{' (DIRTY)' if dirty else ''}"
    if not PROVENANCE_RE.match(line):
        # Never emit a line this tool's own gate would reject (round-2 P1: a
        # narrow ref class did exactly that on a legal branch name).
        print(f"finding-provenance: refusing to emit an unvalidatable provenance "
              f"line: {line!r}", file=sys.stderr)
        return 2
    print(line)
    if dirty:
        print(f"  ⚠️  worktree is DIRTY — {label}@{head[:12]} names HEAD, not the "
              f"files you read; commit/stash first, or say in the finding which "
              f"files it measured", file=sys.stderr)
    base_sha = _resolve_commit(root, base_ref)
    if base_sha is None:
        print(f"  ⚠️  base ref '{base_ref}' not resolvable here — the reader "
              f"cannot judge staleness; run `git fetch` first", file=sys.stderr)
        return 0
    gap = _behind(root, head, base_ref)
    if gap:
        print(f"  ⚠️  {label} is {gap} commit(s) behind {base_ref} — re-verify "
              f"against {base_ref} before reporting (the finding may already "
              f"be fixed)", file=sys.stderr)
    return 0


def _checkout(root: Path, base_ref: str) -> int:
    """#4290 ask 2 — the cheap pre-reporting verb."""
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "HEAD"
    if _resolve_commit(root, "HEAD") is None:
        print("finding-provenance: cannot resolve HEAD", file=sys.stderr)
        return 2
    if _resolve_commit(root, base_ref) is None:
        print(f"finding-provenance: base ref '{base_ref}' not resolvable "
              f"(fetch-depth 0 / fetch first) — cannot answer", file=sys.stderr)
        return 2
    behind = _behind(root, "HEAD", base_ref)
    ahead = _behind(root, base_ref, "HEAD")
    if behind is None or ahead is None:  # pragma: no cover — base resolvable
        print("finding-provenance: failed to count commits", file=sys.stderr)
        return 2
    if behind == 0:
        print(f"CURRENT {branch}: 0 behind {base_ref}, {ahead} ahead — a "
              f"finding measured here is dated to {base_ref}")
        return 0
    print(f"STALE   {branch}: {behind} behind {base_ref}, {ahead} ahead — a "
          f"finding measured here may already be fixed on {base_ref}; "
          f"re-verify before reporting")
    return 1


def _parse_all(body: str) -> list[dict]:
    return [
        {"ref": m.group("ref"), "sha": m.group("sha"), "date": m.group("date"),
         "dirty": bool(m.group("dirty"))}
        for m in PROVENANCE_RE.finditer(body)
    ]


def _validate(root: Path, body: str, base_ref: str, fix: str | None,
              as_json: bool) -> int:
    def fail(status: str, reason: str, **extra) -> int:
        if as_json:
            print(json.dumps({"status": status, "reason": reason, **extra}))
        else:
            print(f"FAIL {status}: {reason}")
        return 1

    provs = _parse_all(body)
    if not provs:
        return fail(
            "UNKNOWN_PROVENANCE",
            "no parseable provenance line. Every finding must carry the tree "
            "it was measured against — run `python3 tools/finding_provenance.py "
            "--emit` and paste its output into the finding body (format: "
            "`Measured at: <ref>@<40-hex-sha> on <YYYY-MM-DD>`). Unknown "
            "provenance is not a pass (#4290).",
        )
    if len(provs) > 1:
        # Ambiguity is not a pass either: an appended second line must not
        # leave a stale first line silently authoritative, and a quoted older
        # finding must not be confused with this one. One finding, one line.
        return fail(
            "AMBIGUOUS_PROVENANCE",
            f"found {len(provs)} `Measured at:` lines — a finding carries "
            f"exactly one. Replace the existing line with the current "
            f"measurement (`--emit`); do not append a second.",
        )
    prov = provs[0]

    measured = _resolve_commit(root, prov["sha"])
    if measured is None:
        return fail(
            "UNKNOWN_PROVENANCE",
            f"measured commit {prov['sha']} ({prov['ref']}) is not a commit in "
            f"this repository — the finding cannot be dated (shallow clone or "
            f"foreign SHA?)",
            measured=prov["sha"],
        )

    base_sha = _resolve_commit(root, base_ref)
    if base_sha is None:
        print(f"finding-provenance: base ref '{base_ref}' not resolvable "
              f"(fetch-depth 0 required); cannot judge staleness",
              file=sys.stderr)
        return 2

    fresh = _is_ancestor(root, base_ref, measured)
    if fresh is None:  # pragma: no cover — both endpoints just resolved
        print("finding-provenance: git merge-base failed", file=sys.stderr)
        return 2
    if not fresh:
        gap = _behind(root, measured, base_ref)
        return fail(
            "STALE",
            f"finding measured at {prov['ref']}@{measured[:12]} does NOT "
            f"contain current {base_ref} ({base_sha[:12]})"
            + (f" — behind by {gap} commit(s)" if gap is not None else "")
            + ". The defect it reports may already be fixed; re-verify against "
              f"{base_ref} before acting on it (#4290).",
            measured=measured,
            base=base_sha,
            behind=gap,
        )

    if fix is not None:
        fix_sha = _resolve_commit(root, fix)
        if fix_sha is None:
            print(f"finding-provenance: --fix '{fix}' is not a commit",
                  file=sys.stderr)
            return 2
        contains = _is_ancestor(root, fix_sha, measured)
        if contains is None:  # pragma: no cover — both endpoints resolved
            print("finding-provenance: git merge-base failed", file=sys.stderr)
            return 2
        if not contains:
            return fail(
                "PREDATES_FIX",
                f"finding measured at {prov['ref']}@{measured[:12]} does NOT "
                f"contain the claimed fix {fix_sha[:12]} — it was measured "
                f"against a tree that predates the fix (#4290).",
                measured=measured,
                fix=fix_sha,
            )

    if as_json:
        print(json.dumps({"status": "CURRENT", "measured": measured,
                          "ref": prov["ref"], "date": prov["date"],
                          "dirty": prov["dirty"],
                          "base": base_sha, "fix": fix}))
    else:
        print(f"CURRENT measured at {prov['ref']}@{measured[:12]} on "
              f"{prov['date']} — contains {base_ref} ({base_sha[:12]})"
              + (f" and the claimed fix {fix[:12]}" if fix else ""))
    if prov["dirty"]:
        # Not fatal — a finding about uncommitted state is legitimate — but it
        # must reach the READER, not just the reporter, so it is in the output
        # (captured by the workflow) as well as in the line itself.
        print("  ⚠️  DIRTY: taken on a worktree with uncommitted changes — HEAD "
              "does not describe the files that were read", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--emit", action="store_true",
                      help="print the provenance line for the current checkout")
    mode.add_argument("--checkout", action="store_true",
                      help="answer: is this checkout behind the base, and by how many?")
    mode.add_argument("--validate", nargs="?", const="-", metavar="FILE",
                      help="gate a finding body (FILE or '-' for stdin)")
    ap.add_argument("--repo", default=None,
                    help="repo path to run git in (default: cwd toplevel)")
    ap.add_argument("--base", default=BASE_DEFAULT,
                    help="base ref a finding must contain (default: %(default)s)")
    ap.add_argument("--fix", default=None,
                    help="a claimed fix commit the measured tree must contain")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    root = repo_root(Path(args.repo) if args.repo else None)

    if args.emit:
        return _emit(root, args.base)
    if args.checkout:
        return _checkout(root, args.base)

    if args.validate == "-":
        body = sys.stdin.read()
    else:
        path = Path(args.validate)
        if not path.is_file():
            print(f"finding-provenance: no such file: {path}", file=sys.stderr)
            return 2
        body = path.read_text(encoding="utf-8")
    return _validate(root, body, args.base, args.fix, args.json)


if __name__ == "__main__":
    sys.exit(main())
