#!/usr/bin/env python3
"""drift-guard — silent-revert gate, reporting base distance (#1531, #4174, #4764).

Fails when a conflict-free merge of the branch into main would leave a path
main moved since the merge base with a blob that is not main's (#4174,
corrected by #7455). That arm is the conflict-free revert: because git surfaces
a textual conflict of its own, the clean case — where the merge result's blob
for the path is not main's — is what a conflict check cannot see, so the arm
reports the affected paths and line counts. The predicate is BLOB IDENTITY, not
"main's content was dropped": see the ❗ note below.

❗WHAT THE VERDICT MEASURES, STATED BECAUSE IT IS EASY TO MISREAD (#1431). The
arm's predicate is BLOB IDENTITY: a path is flagged when the merge result's
blob differs from main's. A UNION satisfies that too — main adds two
registrations to a shared file, the branch adds its own, the merge keeps both,
and the bytes are still not main's — so a red does NOT by itself mean main's
content was lost. Each reported path therefore also carries `lost`: the number
of lines `git diff <merge-base> <main>` ADDED that the merge result does not
carry. `lost == 0` means every line main added is present and the red is
byte-identity (a union, or a diff3 combination); `lost > 0` means an added line
of main's is genuinely absent; `lost == null` means the path is binary and
could not be measured. A main-side DELETION is NOT folded into this count:
line content alone cannot distinguish a deleted line the merge kept from an
identical line the branch added elsewhere, and reporting the latter as loss
was a false "content IS lost" on a merge that applied main's deletion (#1431
review). ⚠️ A zero `lost` is NOT a proof of
preservation when the branch touched the same lines main moved: this repo
unions `config/ci-surfaces.yml` (`.gitattributes`), and a union resolves an
overlapping delete/modify WITHOUT a conflict, keeping the other side's line and
dropping main's deletion — so that case is reported UNPROVEN (the path is
`contested`), never as "content kept" (#1431 review). It is main's CONTRIBUTION
that is compared, never main's whole
file: a line the branch legitimately rewrote is not loss, and a whole-file
comparison reports it as loss — measured against #7455's own fixture, where
main's `M` survives and only the BRANCH's `A`→`F` line reads as missing. This
field changes NO verdict: tightening the predicate itself to `lost > 0` would
reverse #7455's ruling and needs that decision revisited, not a quiet edit.

It also REPORTS, but no longer fails on, how far behind origin/main the branch
is (#1531's threshold arm, relaxed by #4764). The count was a PROXY for "the
green we measured does not describe the tree that would land", and it reddened
branches that were merely behind — measured 2026-10-07: open PRs blocked on
distance alone (24 and 21 commits) with no failing code check.

⚠️ THE RESIDUAL THIS LEAVES, stated rather than assumed. The proxy did bound one
case the revert arm does not: a stale base whose merge into a GREEN main breaks
the build WITHOUT reverting content — a semantic or API conflict, a removed
symbol, fixture drift. GitHub recomputes the merge ref when the base moves but
does NOT re-run the PR's checks, and the merge rail re-measures them only where
it refuses BECAUSE of the lag, which it does only when the base is RED. So a branch
far behind a green main is no longer bounded by any distance. That residual is
UNMEASURED here — no instance of it has been observed, which is why the arm is
reported rather than gating — but it is a real widening and NOT a compensated
one. The revert arm below is unchanged and remains the failure mode (#4764).

WHY THE FETCH IS NOT OPTIONAL (#4174). A worktree that never fetched holds a
STALE `origin/main`. Every read against it is self-consistent — the branch
looks purely additive — so the gate passed green while the branch was
thousands of lines behind the real main (PR #4110). The gate therefore
FETCHES the base before measuring and FAILS CLOSED (exit 2) when the ref
cannot be proven fresh: a possibly-stale read is never a pass.

The check's two arms are independent:
  * distance arm (BEHIND): commits on main missing from the branch, REPORTED
    against max and advisory since #4764 — it never fails the gate;
  * revert arm (SILENT REVERT): the paths main moved since the merge base
    whose merged content differs from main's, where the merge is
    conflict-free. The verdict is BLOB IDENTITY, so it also fires on a union
    that kept main's content; `lost` (see above) measures which it is
    (#1431).
    Measured by ONE virtual merge (`git merge-tree`), so it fails
    REGARDLESS of the commit count and cannot fire on a path only main
    touched (which a 3-way merge preserves by construction, #7455).

WHY THE PR HEAD IS MEASURED, NOT HEAD (#4396). On `pull_request`,
`actions/checkout@v4` checks out the synthetic merge ref `refs/pull/N/merge`
= merge(base, pr_head) — a commit that CONTAINS the base's tip as its first
parent. `HEAD..base` is therefore ~0 however stale the PR's own base is, so
the gate was blind precisely for the mergeable-but-stale PRs it exists to
catch (measured: PR #4020 was reported `drift-guard pass` while 210 commits
behind). When the checkout IS that merge ref, the ref whose drift matters is
the PR head `HEAD^2`, and the gate measures that instead. `ahead` follows the
same ref, so the report names what it measured (`measured`) and the human
line says `(via HEAD^2, the PR head)`. Every other checkout still measures
`HEAD` exactly as before.

The checkout is identified by HEAD *being* the ref `actions/checkout` created
for the merge ref (`refs/remotes/pull/N/merge`), not by the event ref alone
and not by the 2-parent merge shape alone: `GITHUB_REF` reports the event
only (an explicit `ref:` on a `pull_request` event leaves it at
`refs/pull/N/merge` while HEAD is the named ref), and a local branch that
merged a stale sibling has the identical commit graph.

Honors `# noqa: drift-guard` inline annotations? No — this is a
remote-state gate, not a file-content scan. It runs on CI for every PR and
on demand via workflow_dispatch; local runs use the same code path.

Usage:
    uv run python tools/drift-guard.py                 # origin/main
    uv run python tools/drift-guard.py --base origin/main --max-behind 20
    DRIFT_MAX_BEHIND=10 uv run python tools/drift-guard.py   # annotate past 10
    uv run python tools/drift-guard.py --json          # machine-readable output
    uv run python tools/drift-guard.py --head <ref>    # measure a ref other than HEAD

Exit codes:
    0  no silent revert — gate green (base distance is reported, never gating)
    1  a silent revert — gate red
    2  environment error (not a git repo / base not a remote-tracking ref /
       base unreachable / fetch failed — freshness unprovable / the
       silent-revert arm could not be MEASURED — an unreadable merge base or
       diff is not an empty revert set)
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/drift-guard.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/drift-guard.py`"
    )

import argparse
import json
import os
import subprocess
from collections import Counter
from pathlib import Path

DEFAULT_MAX_BEHIND = 20
# ADVISORY since #4764: the distance is reported against this number and never
# fails the gate. The count measured stale MEASUREMENT, not a defect — and the
# residual that leaves (a stale base whose merge into a green main breaks the
# build without reverting content) is stated in the module docstring, NOT
# compensated here or by the merge rail.
# Display help for a long revert list; the JSON always carries every path.
MAX_REPORTED_PATHS = 50


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=False,
        # A path inside a git object may hold bytes that are not valid UTF-8
        # (legal on ext4/Linux — what the CI job runs; APFS rejects them and
        # NTFS cannot represent them at all). Strict decoding raises
        # UnicodeDecodeError from inside subprocess.run — BEFORE any returncode
        # check — which is a traceback where this tool's contract promises
        # exit 2 for an unmeasurable arm, and leaves the arm unmeasured.
        errors="surrogateescape",
    )


def _git_raw(root: Path, *args: str) -> subprocess.CompletedProcess:
    """Like :func:`_git` but byte-exact: NO universal-newline translation.

    `lost` compares line CONTENT between main's contribution and the merge, so
    a `\\r` in a CRLF line is content, not a line terminator. `_git`'s text
    mode strips it (universal newlines), which made main's `M\\r` compare equal
    to a merged `M` and reported the merge as keeping a line whose bytes it
    does not carry — while the arm's own verdict is byte identity (#1431
    review).
    """
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=False, check=False,
    )


def _raw_lines(data: bytes) -> list[str]:
    """Split byte-exact git output on `\\n`, keeping `\\r` as content.

    `str.splitlines()` — and `_git`'s text decoding — treat `\\r` as a line
    boundary; splitting the raw bytes on `\\n` alone preserves it, so a CRLF
    line's bytes are compared as bytes. A single trailing empty element (the
    final `\\n`) is dropped.
    """
    lines = data.decode("utf-8", "surrogateescape").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _safe_text(text: str) -> str:
    """Repair surrogate-escaped git output for display (#4174).

    Surrogates are for byte-exact comparison, not for a terminal — printing
    one raises UnicodeEncodeError. The JSON path needs no help (json.dumps
    escapes non-ASCII by default).
    """
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


class MeasurementError(RuntimeError):
    """A git read the revert arm depends on failed.

    An empty path set is a LEGITIMATE answer ("nothing differs"), so a failed
    read must never be allowed to produce one — that is exactly how a
    fail-closed gate becomes fail-open: "could not measure" is reported as
    "no reverts found" and the gate goes green (#4174). Raised by the read
    helpers and turned into exit 2 by ``main``.
    """


def repo_root() -> Path:
    """Repo root via git — robust to worktrees, symlinks, relative __file__."""
    r = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        # Exit 2, not sys.exit(str)'s 1: "not a git repo" is the environment
        # error the docstring's exit-code table promises (#4174 review).
        print(f"drift-guard: not a git repository: {r.stderr.strip()}",
              file=sys.stderr)
        sys.exit(2)
    return Path(r.stdout.strip())


def _split_remote_ref(root: Path, base: str) -> tuple[str, str] | None:
    """('origin', 'main') for a remote-tracking ref, else None.

    `origin/main` and `refs/remotes/origin/main` are fetchable; `main` or
    `feature/x` are not (a local ref, freshness unprovable by fetching).
    A `<name>/<rest>` is only a remote ref when `<name>` is an actual remote.
    """
    if base.startswith("refs/remotes/"):
        parts = base[len("refs/remotes/"):].split("/", 1)
        return (parts[0], parts[1]) if len(parts) == 2 else None
    if "/" not in base:
        return None
    remote, branch = base.split("/", 1)
    if _git(root, "remote", "get-url", remote).returncode != 0:
        return None
    return remote, branch


def fetch_base(root: Path, base: str) -> tuple[str | None, str, str]:
    """Bring `base` up to date. Returns (error_or_None, freshness, measured_ref).

    Freshness is "fetched" only when the fetch succeeded. There is NO opt-out:
    a local ref cannot be proven fresh, and a fetch failure is returned as an
    error — never a green (#4174: a possibly-stale read is not a pass).

    The fetch uses an EXPLICIT destination refspec. `git fetch <remote>
    <branch>` updates `refs/remotes/<remote>/<branch>` only when
    `remote.<remote>.fetch` happens to map that branch; otherwise it writes
    `FETCH_HEAD` alone and still exits 0, leaving the gate measuring the old
    ref while stamping freshness "fetched" (#4174's own failure mode, behind a
    green freshness label). Naming the destination makes the measured ref the
    fetched one.

    `measured_ref` is that CANONICAL destination, and it — never the caller's
    spelling — is what every measurement must use. `origin/main` is
    DWIM-resolved in the gitrevision order ($GIT_DIR/<n>, refs/<n>,
    refs/tags/<n>, refs/heads/<n>, refs/remotes/<n>), so a local branch or tag
    literally named `origin/main` SHADOWS the fetched ref: the gate would
    measure the stale one while stamping freshness "fetched" — fail-open #3
    through another route.
    """
    remote_ref = _split_remote_ref(root, base)
    if remote_ref is None:
        return (f"base '{base}' is not a remote-tracking ref — the gate cannot "
                f"prove its freshness (pass origin/<branch>); an unproven base "
                f"is not a pass"), "unproven", base
    remote, branch = remote_ref
    measured = f"refs/remotes/{remote}/{branch}"
    refspec = f"+refs/heads/{branch}:{measured}"
    r = _git(root, "fetch", remote, refspec, "--quiet")
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip().splitlines()
        return (f"could not fetch {remote}/{branch} — the base ref's freshness "
                f"cannot be proven, so this is not a pass "
                f"({detail[0] if detail else 'fetch failed'})"), "unproven", measured
    return None, "fetched", measured


def _paths_changed(root: Path, a: str, b: str) -> set[str]:
    """Paths differing between <a> and <b>, NUL-separated (rename-free).

    Raises :class:`MeasurementError` on a failed read. An empty set means
    "nothing differs", and a git failure must never be allowed to answer that.
    """
    r = _git(root, "diff", "--no-renames", "--name-only", "-z", a, b)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip().splitlines()
        raise MeasurementError(
            f"git diff {a}..{b} failed (rc {r.returncode}): "
            f"{detail[0] if detail else 'no detail'}"
        )
    return {p for p in r.stdout.split("\0") if p}


def _numstat(root: Path, a: str, b: str) -> dict[str, tuple[int, int]]:
    """{path: (added, deleted)} for <a>..<b>, NUL-separated (`-z`).

    `--numstat -z` emits one NUL-terminated record per file, fields
    tab-separated: `added\\tdeleted\\tpath`. A binary file uses `-`.

    Raises :class:`MeasurementError` on a failed read — line counts that could
    not be read must not be silently reported as `+0/-0`.
    """
    r = _git(root, "diff", "--no-renames", "--numstat", "-z", a, b)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip().splitlines()
        raise MeasurementError(
            f"git diff --numstat {a}..{b} failed (rc {r.returncode}): "
            f"{detail[0] if detail else 'no detail'}"
        )
    out: dict[str, tuple[int, int]] = {}
    for rec in r.stdout.split("\0"):
        if not rec:
            continue
        parts = rec.split("\t", 2)
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        try:
            n_add = int(added)
            n_del = int(deleted)
        except ValueError:
            n_add = n_del = 0  # binary ('-')
        out[path] = (n_add, n_del)
    return out


def _merge_tree(root: Path, base: str, head: str) -> tuple[str, set[str]]:
    """(merged tree OID, conflicted paths) for merging `head` into `base`.

    Uses `git merge-tree --write-tree -z --name-only <base> <head>` (git
    >= 2.38; `--write-tree` is the form `tools/queue_conflict_census.py`
    already relies on, and it is the plumbing successor to `git merge-tree
    <base> <head>`). `-z` keeps path bytes exact. The first NUL-terminated
    field is the resulting tree's OID; the conflicted paths follow as
    NUL-terminated fields up to the first empty field, which separates them
    from git's informational-message section. Exit 0 = clean, 1 = conflicts.

    ANY other exit is an unreadable merge and raises
    :class:`MeasurementError` — a simulation that could not run must never be
    mistaken for "no reverts" (fail closed, #4174). A `rc == 1` whose
    conflicted-path list came back empty is likewise unreadable: without the
    set, a conflicted path would be scored as a silent revert.
    """
    r = _git(root, "merge-tree", "--write-tree", "-z", "--name-only", base, head)
    if r.returncode not in (0, 1):
        detail = (r.stderr or r.stdout or "").strip().splitlines()
        raise MeasurementError(
            f"git merge-tree --write-tree {base} {head} failed (rc {r.returncode}): "
            f"{detail[0] if detail else 'no detail'}"
        )
    fields = r.stdout.split("\0")
    merged = fields[0].strip() if fields else ""
    conflicted: set[str] = set()
    for field in fields[1:]:
        if field == "":
            break
        conflicted.add(field)
    if not merged or (r.returncode == 1 and not conflicted):
        raise MeasurementError(
            f"git merge-tree --write-tree {base} {head} produced no "
            f"{'tree' if not merged else 'conflicted paths'} (rc {r.returncode})"
        )
    return merged, conflicted


def _tree_blobs(root: Path, tree: str) -> dict[str, str]:
    """{path: entry OID} for every entry in `tree`, recursively.

    Raises :class:`MeasurementError` on a failed read — a tree that could not
    be listed must not answer "this path is unchanged".
    """
    r = _git(root, "ls-tree", "-r", "-z", tree)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip().splitlines()
        raise MeasurementError(
            f"git ls-tree -r {tree} failed (rc {r.returncode}): "
            f"{detail[0] if detail else 'no detail'}"
        )
    blobs: dict[str, str] = {}
    for rec in r.stdout.split("\0"):
        if not rec:
            continue
        meta, _, path = rec.partition("\t")
        parts = meta.split()
        if path and len(parts) == 3:
            blobs[path] = parts[2]
    return blobs


def _blob_lines(root: Path, oid: str) -> list[str] | None:
    """The lines of a blob, or None when it is not text.

    None is "unmeasurable", never "no lines": a binary blob's lines cannot be
    counted, and a caller reading None as zero would report content loss as no
    loss. Read byte-exact (no universal-newline translation), so a CRLF line's
    `\\r` is part of its content — this measurement feeds a byte-identity
    verdict. Raises :class:`MeasurementError` on a failed read.
    """
    r = _git_raw(root, "cat-file", "blob", oid)
    if r.returncode != 0:
        detail = _raw_lines(r.stderr or r.stdout)
        raise MeasurementError(
            f"git cat-file blob {oid} failed (rc {r.returncode}): "
            f"{detail[0] if detail else 'no detail'}"
        )
    if b"\0" in r.stdout:
        return None
    return _raw_lines(r.stdout)


def _renamed_from(root: Path, mb: str, base: str, path: str) -> str | None:
    """The merge-base path main RENAMED to `path`, or None.

    `git merge-tree` (the merge simulation this arm keys on) uses rename
    detection by default, but a per-path `git diff --no-renames` reports the
    RENAME DESTINATION as a newly added whole file. Measuring main's
    contribution that way made a pure rename look like a whole-file add, so a
    merge that kept every line of main's contribution (the rename survived;
    only the branch's own edit changed the bytes) printed "main's content IS
    lost" (#1431 review). Detecting the rename here lets `_main_diff_lines`
    diff the rename SOURCE against the destination instead.

    Raises :class:`MeasurementError` on a failed read.
    """
    r = _git(root, "diff", "-M", "--name-status", "-z", mb, base)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip().splitlines()
        raise MeasurementError(
            f"git diff -M --name-status {mb}..{base} failed "
            f"(rc {r.returncode}): {detail[0] if detail else 'no detail'}"
        )
    fields = r.stdout.split("\0")
    i = 0
    while i < len(fields):
        status = fields[i]
        if not status:
            i += 1
            continue
        if status[0] in ("R", "C"):
            if i + 2 >= len(fields):
                break
            old, new = fields[i + 1], fields[i + 2]
            i += 3
            if new == path:
                return old
        else:
            i += 2
    return None


def _diff_lines_of(root: Path, spec: list[str]) -> tuple[list[str], list[str]] | None:
    """(added, deleted) from `git diff <spec>`, or None when it is binary.

    `spec` is either `[<a>, <b>, "--", <path>]` or two blob-ish object names
    (`<rev>:<path>`). Binary is decided STRUCTURALLY (`--numstat`'s `-`, git's
    own signal) and NOT by scanning the diff TEXT: a textual path whose added
    line merely contains the phrase "Binary files" is not binary, and treating
    it as one reported a measurable path as unmeasurable (#1431 review).

    The line lists come from the diff's HUNK structure, not from a prefix
    filter over the whole output: a `+` line is collected only inside a `@@`
    hunk, so the `+++ b/<path>` file header cannot leak in, and an added
    content line that itself begins with `++` (`++i;` renders as `+++i;`) is
    kept rather than dropped (#1431 review).
    """
    ns = _git(root, "diff", "--no-renames", "--numstat", "-z", *spec)
    if ns.returncode != 0:
        detail = (ns.stderr or ns.stdout or "").strip().splitlines()
        raise MeasurementError(
            f"git diff --numstat {' '.join(spec)} failed "
            f"(rc {ns.returncode}): {detail[0] if detail else 'no detail'}"
        )
    for rec in ns.stdout.split("\0"):
        if not rec:
            continue
        fields = rec.split("\t", 2)
        # git prints `-` for both counts on a binary path — git's own binary
        # signal, unlike a substring scan of the patch text.
        if len(fields) == 3 and fields[0] == "-" and fields[1] == "-":
            return None

    r = _git_raw(root, "diff", "--no-renames", "--unified=0", *spec)
    if r.returncode != 0:
        detail = _raw_lines(r.stderr or r.stdout)
        raise MeasurementError(
            f"git diff {' '.join(spec)} failed (rc {r.returncode}): "
            f"{detail[0] if detail else 'no detail'}"
        )
    added: list[str] = []
    deleted: list[str] = []
    in_hunk = False
    for ln in _raw_lines(r.stdout):
        if ln.startswith("diff --git ") or ln.startswith("@@"):
            in_hunk = ln.startswith("@@")
            continue
        if not in_hunk:
            continue
        if ln.startswith("+"):
            added.append(ln[1:])
        elif ln.startswith("-"):
            deleted.append(ln[1:])
    return added, deleted


def _main_diff_lines(root: Path, mb: str, base: str,
                     path: str) -> tuple[list[str], list[str]] | None:
    """(added, deleted) lines main contributed to `path` since the merge base.

    main's CONTRIBUTION to the path — deliberately not main's whole version,
    which also contains lines the branch may legitimately have rewritten. When
    main's change to `path` is a RENAME, the contribution is measured against
    the rename SOURCE (see :func:`_renamed_from`), matching the rename
    detection `git merge-tree` applies; otherwise it is the per-path diff of
    `mb`..`base`. None means the path is binary.

    Raises :class:`MeasurementError` on a failed read.
    """
    renamed = _renamed_from(root, mb, base, path)
    if renamed is not None:
        return _diff_lines_of(root, [f"{mb}:{renamed}", f"{base}:{path}"])
    return _diff_lines_of(root, [mb, base, "--", path])


def _main_added_lines(root: Path, mb: str, base: str, path: str) -> list[str] | None:
    """Lines main ADDED to `path` — the addition half of `_main_diff_lines`."""
    diff = _main_diff_lines(root, mb, base, path)
    return None if diff is None else diff[0]


def _changed_ranges(root: Path, a: str, b: str, path: str) -> list[tuple[int, int]]:
    """Inclusive `a`-side line ranges the `a`..`b` diff of `path` touches.

    Parsed from the `@@ -start[,count] +... @@` hunk headers, so a hunk is a
    RANGE in the merge-base coordinates, not a substring of the patch. A pure
    insertion (`count == 0`) is anchored at the line it follows. Raises
    :class:`MeasurementError` on a failed read.
    """
    r = _git(root, "diff", "--no-renames", "--unified=0", a, b, "--", path)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip().splitlines()
        raise MeasurementError(
            f"git diff {a}..{b} -- {path} failed (rc {r.returncode}): "
            f"{detail[0] if detail else 'no detail'}"
        )
    ranges: list[tuple[int, int]] = []
    for ln in r.stdout.splitlines():
        if not ln.startswith("@@"):
            continue
        left = ln[2:].split("@@", 1)[0].strip().split(" ")[0]
        if not left.startswith("-"):
            continue
        span = left[1:].split(",", 1)
        start = int(span[0])
        count = int(span[1]) if len(span) == 2 else 1
        ranges.append((start, start + max(count, 1) - 1))
    return ranges


def _contested(root: Path, mb: str, base: str, head: str, path: str) -> bool:
    """Does main's change to `path` overlap a branch change on the same lines?

    When both sides touch the same merge-base lines, a plain 3-way merge would
    CONFLICT; a CLEAN merge result therefore means a driver (`merge=union` —
    this repo's own `.gitattributes`) resolved the overlap and may have taken
    the branch's side, silently dropping main's. The path is then CONTESTED:
    its preservation cannot be proven even when `lost == 0`, because a dropped
    DELETION leaves no added line to miss and no line to find (#1431 review).
    """
    main_ranges = _changed_ranges(root, mb, base, path)
    branch_ranges = _changed_ranges(root, mb, head, path)
    return any(a[0] <= b[1] and b[0] <= a[1]
               for a in main_ranges for b in branch_ranges)


def _lost_lines(root: Path, mb: str, base: str, path: str,
                merged_oid: str) -> int | None:
    """main's ADDED lines for `path` that the merge result does not carry.

    The arm's verdict keys on the merged blob differing from main's, which a
    union of two additive edits satisfies just as a revert does — so the
    verdict alone cannot say whether main's content survived. This counts the
    lines main ADDED that the merge result lacks. 0 means every added line
    main contributed is present; >0 means it is not. None means unmeasurable
    (a binary path). Counted as a multiset, so a line main added twice and the
    merge kept once is one lost line.

    A main-side DELETION is deliberately NOT counted here. Line CONTENT cannot
    tell "the merge kept the line main deleted" from "an identical line the
    branch added elsewhere": the branch's own appended line (or a second
    occurrence already in the file) matched main's deleted line and reported a
    false "main's content IS lost" on a merge that DID apply main's deletion
    (#1431 review). A deletion main dropped is instead surfaced by
    :func:`_contested` as preservation UNPROVEN, never as proven loss.
    """
    diff = _main_diff_lines(root, mb, base, path)
    if diff is None:
        return None
    added, _deleted = diff
    merged = _blob_lines(root, merged_oid)
    if merged is None:
        return None
    return sum((Counter(added) - Counter(merged)).values())


def silent_reverts(root: Path, mb: str, base: str, head: str) -> list[dict]:
    """Main's post-merge-base changes a merge of this branch would not keep.

    A path is a SILENT REVERT when `base` (main) moved it since the merge base
    and the merge of `head` into `base` would NOT leave main's content in
    place (the merged blob exists and differs from main's) while the merge is
    conflict-free. Silent is the point: a conflicted path is already visible to
    git and to GitHub; only the clean ones go unreported.

    #7455 corrected the predicate. It was `moved_by_base - moved_by_head` —
    main moved a path and the branch did not — but that set is exactly the one
    a 3-way merge PRESERVES: with only main having moved it, ours = main's new
    content and theirs = the merge-base content, so git takes main's side. The
    old arm therefore flagged the COMPLEMENT of the danger set, and reddened
    any branch at least one commit behind — including a strict ancestor of
    main, which cannot delete its descendant's work. The danger set is the
    overlap (paths BOTH sides moved, where the merge might not keep main's
    version); the merge simulation below measures it directly rather than
    approximating it from path sets.
    """
    moved_by_base = _paths_changed(root, mb, base)
    if not moved_by_base:
        return []
    # ONE virtual merge for the whole arm, not one per candidate path.
    merged_tree, conflicted = _merge_tree(root, base, head)
    merged_blobs = _tree_blobs(root, merged_tree)
    base_blobs = _tree_blobs(root, base)
    reverted = sorted(
        p for p in moved_by_base
        if p not in conflicted
        and p in merged_blobs
        and merged_blobs[p] != base_blobs.get(p)
    )
    if not reverted:
        return []
    counts = _numstat(root, mb, base)
    return [
        {
            "path": p,
            "added": counts.get(p, (0, 0))[0],
            "deleted": counts.get(p, (0, 0))[1],
            # main's CONTRIBUTION that the merge result does not carry. The
            # verdict above is blob identity, which a union also trips; this is
            # the measured answer to "did main's content actually survive?"
            # (#1431). Never read None as 0 — that is "unmeasurable".
            "lost": _lost_lines(root, mb, base, p, merged_blobs[p]),
            # `lost == 0` is not proof when the branch touched the same base
            # lines: a union resolves that overlap WITHOUT a conflict and can
            # drop main's side (a deletion leaves no added line to miss).
            # Contested paths are reported UNPROVEN, never "content kept".
            "contested": _contested(root, mb, base, head, p),
        }
        for p in reverted
    ]


def _lost_note(lost: int | None, contested: bool = False) -> str:
    """Per-path annotation naming WHICH red this is (#1431).

    The verdict is blob identity, so it fires on a union as readily as on a
    revert; this is the measured discriminator, kept next to the
    `+added/-deleted` pair that describes main's movement instead. `contested`
    is true when the branch touched the same base lines main moved: under
    `merge=union` — this repo's own `.gitattributes` — that overlap resolves
    WITHOUT a conflict and can drop main's side, so a zero `lost` there must
    not read "content kept" (#1431 review).
    """
    if lost is None:
        return "  (main's added lines absent: UNMEASURED — binary path)"
    if lost:
        return f"  (main's contribution absent: {lost} — main's content lost)"
    if contested:
        return ("  (main's added lines absent: 0, but the branch touched the "
                "same lines — preservation UNPROVEN under this merge)")
    return "  (main's added lines absent: 0 — main's content kept)"


def _lost_summary(reverts: list[dict]) -> str:
    """The gate's closing line: WHICH red this is (#1431).

    A pure function of the measured `lost` values, so every branch is testable
    without an end-to-end fixture. No fixture in this suite is known to raise
    the `lost > 0` aggregate — a conflict-free merge generally preserves both
    sides' non-overlapping changes, and overlaps conflict — so that branch is
    exercised directly. This is a COVERAGE statement, not an equivalence claim:
    a merge driver can drop a main-added line in a conflict-free merge, and a
    fixture that did would make the aggregate reachable (#1431 review). The
    verdict is blob identity
    and cannot tell a union that kept main's content from a revert that lost
    it; this line says which, because a red whose message asserts lost content
    is how a union gets misread as a revert.

    A zero `lost` on a CONTESTED path — one whose base lines the branch also
    touched — does not prove preservation: this repo unions
    `config/ci-surfaces.yml` (`.gitattributes`), and a union resolves an
    overlapping delete/modify by keeping the other side's line, dropping main's
    deletion in a CONFLICT-FREE merge that an additions-only count cannot see.
    That case is reported as UNPROVEN, never as "kept / not a revert" (#1431
    review).
    """
    total = sum(r["lost"] or 0 for r in reverts)
    unmeasured = sum(1 for r in reverts if r["lost"] is None)
    if unmeasured:
        return (
            "    main's own added lines absent from the merge: "
            f"{total} in total, UNMEASURED on {unmeasured} path(s) "
            "(binary) — treat those as unproven.")
    if total:
        return (
            "    main's own added lines absent from the merge: "
            f"{total} — main's content IS lost on "
            f"{sum(1 for r in reverts if r['lost'])} path(s).")
    contested_paths = sum(1 for r in reverts if r.get("contested"))
    if contested_paths:
        return (
            "    main's own added lines absent from the merge: 0, but "
            f"{contested_paths} path(s) had main's change CONTESTED by an "
            "overlapping branch change under a conflict-free merge — a driver "
            "like `merge=union` can drop main's side, and a dropped deletion "
            "leaves no added line to miss, so preservation is UNPROVEN there. "
            "This red is not a proven revert; re-merging may still clear it "
            "(#1431).")
    return (
        "    main's own added lines absent from the merge: 0 — every "
        "line main contributed is present, so this red is "
        "byte-identity (a union or a diff3 combination that keeps "
        "main's content), not lost content. Re-merging still clears "
        "it; it is not a revert (#1431).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main",
                    help="base ref to compare against (default: origin/main)")
    ap.add_argument("--head", default="HEAD",
                    help="ref to measure (default: HEAD); the gate measures the "
                         "PR HEAD, never the synthetic merge ref, which contains "
                         "the base by construction")
    ap.add_argument("--max-behind", type=int, default=None,
                    help="report — do NOT fail — when behind by more than N "  # noqa: UP031
                         "commits; distance is advisory since #4764 "
                         "(default: %d)" % DEFAULT_MAX_BEHIND)
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output")
    args = ap.parse_args()

    max_behind = args.max_behind
    if max_behind is None:
        try:
            max_behind = int(os.environ.get("DRIFT_MAX_BEHIND", ""))
        except ValueError:
            max_behind = DEFAULT_MAX_BEHIND

    root = repo_root()

    def emit(payload: dict, text_lines: list[str], rc: int) -> int:
        if args.json:
            print(json.dumps(payload))
        else:
            for line in text_lines:
                print(_safe_text(line),
                      file=sys.stderr if rc != 0 else sys.stdout)
        return rc

    # ── freshness first: a possibly-stale ref is never a pass (#4174) ──────
    fetch_err, freshness, measured_base = fetch_base(root, args.base)
    if fetch_err is not None:
        payload = {"status": "error", "reason": fetch_err,
                   "base": args.base, "freshness": freshness}
        return emit(payload, [f"ERROR drift-guard: {fetch_err}"], 2)

    branch = _git(root, "rev-parse", "--abbrev-ref", args.head).stdout.strip()
    if not branch or branch == "HEAD":
        branch = args.head if args.head != "HEAD" else "(detached HEAD)"

    # Base must be resolvable — a wrong/missing ref is an environment error,
    # not a clean gate.
    base_ok = _git(root, "rev-parse", "--verify", "--quiet",
                   f"{measured_base}^{{commit}}")
    if base_ok.returncode != 0:
        msg = (f"drift-guard: base ref '{args.base}' not resolvable "
               f"(fetch-depth 0 required in CI); cannot compare")
        return emit({"status": "error", "reason": msg, "base": args.base,
                     "freshness": freshness},
                    [f"ERROR {msg}"], 2)

    head_ok = _git(root, "rev-parse", "--verify", "--quiet", f"{args.head}^{{commit}}")
    if head_ok.returncode != 0:
        msg = f"drift-guard: head ref '{args.head}' not resolvable; cannot measure"
        return emit({"status": "error", "reason": msg, "base": args.base,
                     "freshness": freshness},
                    [f"ERROR {msg}"], 2)

    base_sha = base_ok.stdout.strip()

    # ── what to MEASURE: HEAD, or the PR head on a merge-ref checkout (#4396) ─
    # On `pull_request`, actions/checkout@v4 checks out the synthetic merge ref
    # `refs/pull/N/merge` = merge(base, pr_head) — a commit that CONTAINS the
    # base's tip as `HEAD^1`. `HEAD..base` is therefore ~0 however stale the
    # PR's own base is, so the gate was blind precisely for the
    # mergeable-but-stale PRs it exists to catch. When HEAD is that merge
    # commit, the ref whose drift matters is the PR head, `HEAD^2`; counting
    # `HEAD^2..base` is equivalent to counting from merge-base(HEAD^2, base) on
    # a normal branch and keeps the revision-expression shape of the branch
    # path, so the two cannot diverge in meaning. `ahead` follows the same ref
    # so the report is internally consistent — it names what it measured.
    #
    # EXACTLY 2 parents AND the checkout literally BEING the merge ref together
    # are load-bearing. Neither the commit shape alone nor the EVENT alone is
    # sufficient:
    #   - `HEAD != base`, and `HEAD^1 == base`, each also match a LOCAL branch
    #     (one that merged a stale sibling, and one BASED ON main that then
    #     merged a stale sibling). Those have the same graph as a merge ref but
    #     `HEAD^2` there is a sibling, not a head under test: measuring it turns
    #     a true drift of 0 into a false red. The graph cannot tell them apart.
    #   - `GITHUB_REF` alone does not identify the CHECKOUT. It reports the
    #     event, and `actions/checkout` with an explicit `ref:` on a
    #     `pull_request` event leaves `GITHUB_REF` at `refs/pull/N/merge` while
    #     HEAD is the named ref.
    # What identifies the checkout exactly is that HEAD IS the ref checkout
    # created for the merge ref — the job log shows
    # `git checkout --progress --force refs/remotes/pull/4020/merge`. So: the
    # event supplies N, and HEAD must equal that ref. Plus the 2-parent sanity
    # condition (so `HEAD^2` is the merged head, not a parent of an octopus).
    # Anything else — including an explicit non-HEAD `--head` — falls back to
    # `HEAD`, unchanged.
    measured_head = args.head
    if args.head == "HEAD":
        parents = _git(root, "rev-list", "--parents", "-n1", "HEAD")
        head_sha = _git(root, "rev-parse", "HEAD").stdout.strip()
        event_ref = os.environ.get("GITHUB_REF", "")
        if (event_ref.startswith("refs/pull/") and event_ref.endswith("/merge")
                and parents.returncode == 0
                and len(parents.stdout.split()) == 3):
            # The remote-tracking ref actions/checkout creates for the merge
            # ref: `refs/remotes/` + the event ref with its leading `refs/`
            # stripped, i.e. `refs/remotes/pull/N/merge`.
            ref_sha = _git(
                root, "rev-parse", "--verify", "--quiet",
                f"refs/remotes/{event_ref.removeprefix('refs/')}^{{commit}}",
            ).stdout.strip()
            if head_sha and ref_sha and head_sha == ref_sha:
                measured_head = "HEAD^2"

    ahead = _git(root, "rev-list", "--count",
                 f"{measured_base}..{measured_head}").stdout.strip()
    behind = _git(root, "rev-list", "--count",
                  f"{measured_head}..{measured_base}").stdout.strip()
    try:
        ahead_n, behind_n = int(ahead), int(behind)
    except ValueError:  # pragma: no cover — git always prints ints
        print("drift-guard: failed to count commits", file=sys.stderr)
        return 2

    # ── silent-revert arm (#4174) ─────────────────────────────────────────
    reverts: list[dict] = []
    # `behind_n == 0` needs no merge base: base is an ancestor of head, so
    # every path main moved since the common ancestor is present in head and
    # the revert set is provably empty. With `behind_n > 0` the arm MUST run —
    # an unreadable merge base is "could not measure", which is not a pass.
    mb_proc = _git(root, "merge-base", measured_base, measured_head)
    mb = mb_proc.stdout.strip()
    if behind_n > 0 and (mb_proc.returncode != 0 or not mb):
        detail = (mb_proc.stderr or "").strip().splitlines()
        msg = (f"drift-guard: cannot find the merge base of '{measured_head}' and "
               f"'{args.base}' while behind by {behind_n} — the silent-revert "
               f"arm cannot be measured, and an unmeasured arm is not a pass "
               f"({detail[0] if detail else 'no common ancestor'})")
        return emit({"status": "error", "reason": msg, "base": args.base,
                     "freshness": freshness, "merge_base": None},
                    [f"ERROR {msg}"], 2)
    if mb and behind_n > 0:
        try:
            reverts = silent_reverts(root, mb, measured_base, measured_head)
        except MeasurementError as exc:
            msg = (f"drift-guard: the silent-revert arm could not be measured "
                   f"(merge-base {mb[:12]}): {exc}")
            return emit({"status": "error", "reason": msg, "base": args.base,
                         "freshness": freshness, "merge_base": mb},
                        [f"ERROR {msg}"], 2)
    revert_lines = sum(r["added"] + r["deleted"] for r in reverts)

    # Distance is REPORTED, not gated (#4764): only a silent revert reddens the
    # gate. `drifted` survives as the advisory annotation's predicate.
    drifted = behind_n > max_behind
    status = "drift" if reverts else "ok"
    # What was MEASURED, not what was asked for. `args.base` DWIM-resolves
    # ahead of refs/remotes/, so under the shadow this PR guards against the
    # two spellings name different commits — a verdict about one of them must
    # not be reported as a verdict about the other (#4174 round-3 review).
    base_label = (args.base if measured_base == args.base
                  else f"{args.base} (measured {measured_base})")
    report = {
        "status": status,
        "branch": branch,
        "base": args.base,
        "measured_base": measured_base,
        "base_sha": base_sha,
        "freshness": freshness,
        "ahead": ahead_n,
        "behind": behind_n,
        "max_behind": max_behind,
        "merge_base": mb or None,
        "reverts": reverts,
        "revert_files": len(reverts),
        "revert_lines": revert_lines,
    }
    # Only the merge-ref path grows a field: a non-merge checkout's JSON must
    # stay byte-for-byte what it was.
    if measured_head != args.head:
        report["measured"] = measured_head
    # Empty on the non-merge path, so those text lines stay byte-for-byte
    # unchanged there.
    where = (f" (via {measured_head}, the PR head)"
             if measured_head != args.head else "")

    if status == "ok":
        # The parenthetical is a REPORT, never a pass/fail: `(<= N)` still means
        # "within the advisory number", and the "over N" form names a distance
        # the gate deliberately does not refuse (#4764).
        distance = (f"{behind_n} behind {base_label}{where} "
                    + (f"(over {max_behind} — advisory, not a refusal)"
                       if drifted else f"(<= {max_behind})"))
        return emit(
            report,
            [f"OK  {branch}: {distance}, "
             f"{ahead_n} ahead (base {freshness}) — gate green"],
            0,
        )

    lines: list[str] = []
    # No distance-only FAIL can reach here: `status` is revert-driven since
    # #4764, so a red gate always has at least one revert to report. A distance
    # line that can never fail must not read like one.
    if reverts:
        lines.append(
            f"FAIL {branch}: SILENTLY REVERTING {len(reverts)} path(s) whose "
            f"merged content is not {base_label}'s, where merging this branch "
            f"into {base_label} is conflict-free "
            f"(base {freshness}, merge-base {mb[:12]}):")
        for r in reverts[:MAX_REPORTED_PATHS]:
            lines.append(f"    {r['path']}  +{r['added']}/-{r['deleted']}"
                         f"{_lost_note(r['lost'], r['contested'])}")
        if len(reverts) > MAX_REPORTED_PATHS:
            lines.append(f"    … and {len(reverts) - MAX_REPORTED_PATHS} more")
        lines.append(
            f"    ({len(reverts)} file(s), {revert_lines} line(s)) — a "
            f"conflict-free merge would change, not preserve, "
            f"{base_label}'s version of each path, so a textual conflict "
            f"check cannot see it. Fetch and reconcile before this lands "
            f"(#4174, #7455).")
        # Say WHICH red this is (#1431) — in a pure helper so each branch is
        # testable without a fixture that can produce it.
        lines.append(_lost_summary(reverts))
    return emit(report, lines, 1)


if __name__ == "__main__":
    sys.exit(main())
