#!/usr/bin/env python3
"""Age-gated sweep of tortoise-owned temp-dir litter (#4069).

`$TMPDIR` on a working dev box had churned to 37,765 top-level entries /
224,434 at depth 2 / 3.8 GB with **nothing older than three days** — i.e.
live churn with zero cleanup. It is not just disk: the embedded-orphan
census (`tools/embedded_orphans.py --deep`) and the reaper's stale-socket
walk both `find` this tree, so every entry multiplies a walk that already
cost ~41% of a CPU per call. Sweeping is therefore a load fix, not a
housekeeping nicety.

This tool is deliberately narrow and safe BY CONSTRUCTION:

* **Depth 1 only.** It iterates the direct children of the temp root with
  one `os.scandir`. It never walks the tree, so it cannot become the load
  spike it exists to remove (the #1069 family).
* **Bounded to the temp root.** Every candidate is verified to resolve
  inside the real temp root before it is touched; a path that escapes
  (via an intermediate symlink, or a `--root` that is `/`, `$HOME`, or
  contains `..`) is refused, never followed.
* **Symlinks are never followed.** A symlinked entry is skipped and
  reported (`symlink`); its target — inside or outside the root — is
  untouched.
* **Prefix / exact-name matched.** Only names matching an allowlisted
  tortoise-owned creator pattern are candidates. Unrelated tools that
  also scratch in `$TMPDIR` are invisible to it.
* **Age-gated.** Default 12h (`--older-than-hours`; the issue's suggested
  low end). A live, concurrent suite's per-test dirs are minutes old, and
  its session-long dirs carry a live `redis.pid` — see the guard below.
  Raise to 24h for a conservative pass on a busy box.
* **Live-server guard.** A candidate whose `redis.pid` names a live
  process (or is unparseable — fail closed) is protected. Socket-bearing
  tree cleanup beyond that is the reaper's domain, not this tool's.
* **Dry-run by default.** `--apply` is required to delete anything.
* **Idempotent.** A second `--apply` run discovers zero candidates; the
  removal path itself tolerates an already-removed entry.
* **A dry run never walks a candidate subtree.** Sizes are computed only on
  `--apply`; a dry run is one `os.scandir` of the root plus one `stat` per
  entry, so it costs ~1s of CPU even at 390k entries.

It is **operator-invoked**, not automatic. It is not wired into session
start or the reaper cron: the sweep must not race a concurrent suite's
live dirs, and an automatic fleet-wide sweep is a load decision that
belongs to the operator. `docs/infra/tmpdir-sweep.md` documents the
one-line cron / manual invocation.

Usage:
    python3 tools/tmpdir_sweep.py                       # dry run, 12h
    python3 tools/tmpdir_sweep.py --older-than-hours 24 # conservative dry run
    python3 tools/tmpdir_sweep.py --apply               # delete
    python3 tools/tmpdir_sweep.py --json                # machine-readable

Exit codes: 0 = ran (dry or apply), 2 = refused / could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

# Tortoise-owned creator prefixes, verified against the test suite's
# `tempfile.mkdtemp(prefix=...)` call sites and the observed $TMPDIR
# histogram (issue #4069). Anything not in here is out of scope and is
# never touched. Agent-tooling litter (`pi-commit-msg-*`, `pi-pr-body-*`,
# `admin-*`, `wf-lock-*`) is deliberately EXCLUDED — it belongs to a
# different owner and is tracked by a cross-repo issue; an operator who
# wants it can add `--prefix`.
DEFAULT_PREFIXES: tuple[str, ...] = (
    "ask_",              # ask_proof_, ask_reg_, ask_sdk_, ask2070_, askshape_
    "tortoise_",         # tortoise_test_, tortoise_validity_test_, tortoise_w2_...
    "tortoise-",         # tortoise-lifecycle-, tortoise-concurrency-, capture spools
    "redislite_",        # redislite's own scratch trees
    "lme-",              # tools/longmem_eval per-question trees
    "battery_a4_",       # battery/arms/a4_tortoise.py
)

# Fixed-name debris whose creator is not in any committed source (verified
# with `git log --all -S`); swept by exact name rather than prefix.
DEFAULT_EXACT_NAMES: tuple[str, ...] = ("a_ours.py",)

# `tt_` is the #3752 per-session temp root: it is created at conftest
# import and removed wholesale by its own atexit teardown, so it is NOT in
# the default allowlist — sweeping a live suite's root would be the one
# genuinely destructive case. Operators can add it with `--prefix tt_`.

# Never a valid sweep root — a bounded, single-child-safe tool that
# accepted these would be an unbounded delete.
_FORBIDDEN_ROOTS = frozenset({"/", "//", os.path.expanduser("~")})

_PID_FILENAMES = ("redis.pid",)


@dataclass
class Decision:
    """One top-level entry and what the sweep decided about it."""

    name: str
    path: str
    action: str           # "remove" | "keep"
    reason: str           # why, for the operator's log
    category: str = ""    # stable bucket for aggregation (e.g. "young")
    size_bytes: int = 0


@dataclass
class SweepResult:
    root: str
    older_than_hours: float
    apply: bool
    decisions: list[Decision] = field(default_factory=list)

    @property
    def removed(self) -> list[Decision]:
        return [d for d in self.decisions if d.action == "remove"]

    @property
    def kept(self) -> list[Decision]:
        return [d for d in self.decisions if d.action == "keep"]

    @property
    def reclaimed_bytes(self) -> int:
        return sum(d.size_bytes for d in self.removed)


def resolve_root(root: str | None) -> str:
    """Resolve and validate the sweep root, refusing anything unsafe.

    The returned path is the REAL path (macOS `/var` → `/private/var`), so
    containment checks compare like with like.
    """
    candidate = root if root is not None else tempfile.gettempdir()
    if not candidate:
        raise ValueError("temp root is empty")
    expanded = os.path.expanduser(candidate)
    real = os.path.realpath(expanded)
    if any(part == ".." for part in expanded.split(os.sep)):
        raise ValueError(f"refusing a root containing '..': {candidate!r}")
    if real in _FORBIDDEN_ROOTS or real == os.path.realpath("/"):
        raise ValueError(f"refusing an unbounded root: {candidate!r}")
    if not os.path.isdir(real):
        raise ValueError(f"root is not a directory: {candidate!r}")
    return real


def _is_within(path: str, root: str) -> bool:
    """True iff `path` resolves to `root` itself or a descendant."""
    real = os.path.realpath(path)
    root = os.path.realpath(root)
    return real == root or real.startswith(root.rstrip(os.sep) + os.sep)


def _dir_size(path: str) -> int:
    """Bytes held by `path` (lstat only — never follows symlinks).

    Deliberately NOT called from `plan()`: a dry run must stay one
    `os.scandir` of the root. Walking every candidate subtree is the load
    spike this tool exists to remove, so it happens only on `--apply`,
    where the `rmtree` that follows walks the same tree anyway.
    """
    total = 0
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not os.path.isdir(path) or os.path.islink(path):
        return st.st_size
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            total += entry.stat(follow_symlinks=False).st_size
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _live_pid_protects(entry_path: str) -> str | None:
    """Return a reason string when a live embedded server owns the entry.

    Fail-closed: an unreadable/unparseable pid file protects the entry
    rather than exposing it to removal. A dead pid does NOT protect — that
    is exactly the orphaned-dir case the sweep exists to reclaim.
    """
    for name in _PID_FILENAMES:
        pid_file = os.path.join(entry_path, name)
        if not os.path.isfile(pid_file):
            continue
        try:
            with open(pid_file, encoding="utf-8", errors="replace") as fh:
                raw = fh.read().strip()
        except OSError:
            return f"unreadable {name} (treated as live)"
        try:
            pid = int(raw)
        except ValueError:
            return f"unparseable {name} (treated as live)"
        if pid <= 0:
            return f"nonsensical pid {pid} in {name}"
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None  # dead pid -> orphaned dir, safe to reclaim
        except PermissionError:
            return f"pid {pid} alive (no permission to signal)"
        except OSError:
            return f"pid {pid} probe failed (treated as live)"
        return f"live redis pid {pid}"
    return None


def _matches(name: str, prefixes: Iterable[str],
             exact_names: Iterable[str]) -> bool:
    return name in set(exact_names) or any(
        name.startswith(p) for p in prefixes)


def plan(
    root: str,
    *,
    prefixes: Iterable[str] = DEFAULT_PREFIXES,
    exact_names: Iterable[str] = DEFAULT_EXACT_NAMES,
    older_than_hours: float = 12.0,
    now: float | None = None,
) -> list[Decision]:
    """Classify every depth-1 entry of `root`; never mutates anything."""
    root = os.path.realpath(root)
    now = time.time() if now is None else now
    decisions: list[Decision] = []

    with os.scandir(root) as it:
        for entry in it:
            name = entry.name
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue

            if entry.is_symlink():
                decisions.append(Decision(
                    name, entry.path, "keep",
                    "symlink (never followed)", category="symlink"))
                continue
            if not entry.is_dir(follow_symlinks=False):
                if not _matches(name, prefixes, exact_names):
                    continue
                age_h = (now - st.st_mtime) / 3600.0
                if age_h < older_than_hours:
                    decisions.append(Decision(
                        name, entry.path, "keep",
                        f"young ({age_h:.1f}h < {older_than_hours}h)",
                        category="young"))
                    continue
                decisions.append(Decision(
                    name, entry.path, "remove",
                    f"stale file ({age_h:.1f}h)",
                    category="remove", size_bytes=st.st_size))
                continue

            if not _matches(name, prefixes, exact_names):
                continue  # out of scope — not a tortoise-owned creator

            # Escape check BEFORE any age/server test: a directory that
            # resolves outside the root is never a candidate.
            if not _is_within(entry.path, root):
                decisions.append(Decision(
                    name, entry.path, "keep", "resolves outside the root",
                    category="outside-root"))
                continue

            age_h = (now - st.st_mtime) / 3600.0
            if age_h < older_than_hours:
                decisions.append(Decision(
                    name, entry.path, "keep",
                    f"young ({age_h:.1f}h < {older_than_hours}h)",
                    category="young"))
                continue

            live = _live_pid_protects(entry.path)
            if live is not None:
                decisions.append(Decision(
                    name, entry.path, "keep", live,
                    category="live-server"))
                continue

            decisions.append(Decision(
                name, entry.path, "remove",
                f"stale dir ({age_h:.1f}h)", category="remove"))

    return decisions


def _remove(path: str, root: str) -> None:
    """Remove one candidate, re-verifying containment at the last moment.

    The re-check is cheap and makes the "never outside the root" invariant
    local to the destructive call — a future caller cannot bypass it.
    """
    if not _is_within(path, root):
        raise ValueError(f"refusing to remove {path!r}: outside {root!r}")
    if os.path.islink(path):
        raise ValueError(f"refusing to remove symlink {path!r}")
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        # An exact-name artifact (`a_ours.py`) is a file, not a tree.
        os.remove(path)


def sweep(
    root: str | None = None,
    *,
    prefixes: Iterable[str] = DEFAULT_PREFIXES,
    exact_names: Iterable[str] = DEFAULT_EXACT_NAMES,
    older_than_hours: float = 12.0,
    apply: bool = False,
    now: float | None = None,
) -> SweepResult:
    """Plan (and optionally perform) the sweep. Dry-run unless `apply`."""
    real_root = resolve_root(root)
    decisions = plan(real_root, prefixes=prefixes, exact_names=exact_names,
                     older_than_hours=older_than_hours, now=now)
    result = SweepResult(real_root, older_than_hours, apply, decisions)
    if not apply:
        return result

    failed: list[Decision] = []
    for d in decisions:
        if d.action != "remove":
            continue
        try:
            d.size_bytes = _dir_size(d.path)
            _remove(d.path, real_root)
        except (OSError, ValueError) as exc:  # keep going; report per-entry
            d.action = "keep"
            d.category = "remove-failed"
            d.reason = f"remove failed: {exc}"
            failed.append(d)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tmpdir_sweep",
        description="Age-gated sweep of tortoise-owned temp-dir litter "
                    "(issue #4069).")
    parser.add_argument(
        "--root", default=None,
        help="Temp root to sweep (default: the system temp dir)")
    parser.add_argument(
        "--older-than-hours", type=float, default=12.0,
        help="Only entries at least this old are candidates (default 12, "
             "the issue's suggested value; raise to 24 for a "
             "conservative pass)")
    parser.add_argument(
        "--prefix", action="append", default=None, metavar="PREFIX",
        help="Creator prefix to match (repeatable; replaces the default "
             "allowlist when given at least once)")
    parser.add_argument(
        "--exact-name", action="append", default=None, metavar="NAME",
        help="Exact entry name to match (repeatable; replaces the default "
             "when given)")
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually delete. Without this the run is a dry run.")
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable output")
    args = parser.parse_args(argv)

    prefixes = tuple(args.prefix) if args.prefix else DEFAULT_PREFIXES
    exact_names = (tuple(args.exact_name) if args.exact_name
                   else DEFAULT_EXACT_NAMES)

    try:
        result = sweep(args.root, prefixes=prefixes, exact_names=exact_names,
                       older_than_hours=args.older_than_hours,
                       apply=args.apply)
    except ValueError as exc:
        print(f"tmpdir_sweep: refusing to run: {exc}", file=sys.stderr)
        return 2

    remove_n = len(result.removed)
    if args.json:
        print(json.dumps({
            "root": result.root,
            "apply": result.apply,
            "older_than_hours": result.older_than_hours,
            "prefixes": list(prefixes),
            "exact_names": list(exact_names),
            "candidates": remove_n,
            "kept": len(result.kept),
            "reclaimed_bytes": result.reclaimed_bytes,
            "removed": [d.name for d in result.removed],
            "keep_categories": {
                category: sum(1 for d in result.kept if d.category == category)
                for category in sorted({d.category for d in result.kept})
            },
        }, indent=2))
    else:
        mode = "APPLY" if result.apply else "DRY RUN"
        print(f"[tmpdir-sweep] {mode} root={result.root} "
              f"older-than={result.older_than_hours}h "
              f"candidates={remove_n} kept={len(result.kept)} "
              f"reclaimed={result.reclaimed_bytes / 1_000_000:.1f} MB")
        if not result.apply and remove_n:
            print("  (dry run — re-run with --apply to delete)")
        for d in result.removed[:20]:
            print(f"  remove {d.name}  [{d.reason}]")
        if remove_n > 20:
            print(f"  … and {remove_n - 20} more")
        for category in sorted({d.category for d in result.kept}):
            n = sum(1 for d in result.kept if d.category == category)
            print(f"  keep   {n:5d}  {category}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
