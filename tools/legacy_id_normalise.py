#!/usr/bin/env python3
"""Census and normalise legacy node ids in a logical graph dump — #8607 (W1b).

The **dry run is the default**: with only ``--dump`` the tool prints the census
and the plan and writes NOTHING — no snapshot, no graph, no journal. That is
the artifact a lane reads before deciding to run the pass, and the number a
pass is planned against (``legacy``, not the carried ~9,700).

    # 1. measure — what is in here, what would change, and does it reconcile
    python tools/legacy_id_normalise.py --dump snapshot.json --report plan.json

    # 2. apply the plan to the snapshot. --out AND --report are both required:
    #    the rewritten snapshot is the artifact, and the plan is the only way
    #    back — a run that wrote one without the other is unrecoverable.
    python tools/legacy_id_normalise.py --dump snapshot.json \\
        --apply --out normalised.json --report plan.json

    # 3. undo it from that report — the pass is reversible, not one-way. Capture
    #    the revert's own report: it goes to stdout, and NOTHING else on disk
    #    records that a revert ran or which artifact it undid.
    python tools/legacy_id_normalise.py --dump normalised.json \\
        --revert --report plan.json --out restored.json > revert.json

⚠️ In ``--revert`` mode ``--report`` is the INPUT (the plan being undone), so it is
**never rewritten**: the revert's own report goes to stdout (redirect it, as above),
and ``--out`` is the artifact. Rewriting it would replace the apply-time record in
the very file this workspace calls the way back; and a revert from a report that
carries NO renames is refused, because it would report a restore it never made.

Nothing here connects to a graph. The input is a ``tortoise-logical-dump-v1``
snapshot (``tortoise.hosted_backup.dump_graph``; the encrypted ``tortoise
export`` artifact wraps the same payload) and the output is a re-runnable
snapshot plus the plan that produced it. Loading it is ``restore_graph`` into a
FRESH graph; the journaled live pass is ``#7951``'s, not this tool's.

⚠️ **Every node's ``dump_id`` is preserved**, so edges (which reference
``dump_id``) survive untouched — only the identity property's value changes.
⚠️ ``--out`` may NOT be the ``--dump`` path: an in-place run with no report is
one a ``sha256`` cannot undo.

Exit codes — ``2`` is the REFUSAL and nothing else, so a wrapper can key on it:

* ``0`` — planned (dry run), applied, or reverted;
* ``2`` — **refused**: the SNAPSHOT was not provably safe to rewrite (the plan's
  fingerprint does not match, a legacy id maps twice, a target collides …).
  Every problem is on stderr and **nothing was written**;
* ``1`` — usage or IO: a bad invocation (**including two artifacts named on one
  file** — that is a wrong command line, found before anything is written), a
  missing/unreadable dump or report, or a report that is not one this tool
  wrote.

⚠️ "The same file" is decided by IDENTITY, not by path text: the inode, the
symlink-resolved name, and the case-folded name in the same directory all count.
So ``--out plan.json --report Plan.json`` is refused everywhere, not only on the
case-insensitive filesystems where the snapshot write would silently truncate
the report that ``--revert`` needs.

⚠️ ``argparse``'s own status for a usage error is ``2`` — the code this contract
reserves for a REFUSAL — so :class:`_Parser` overrides it to ``1``. Without
that, a caller keying on ``2 == refused`` cannot tell a bad snapshot from a bad
invocation, and both write nothing, so no other signal distinguishes them.

⚠️ The read path's failure handling is the only copy of it: `tools/graph_census.py`
and `tools/retype_snapshot.py` traceback on the same input class because their
`_load_json` catches only `json.JSONDecodeError` — filed as **#9534**, which is
where the shared loader belongs.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.12-only import would fail first.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/legacy_id_normalise.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/legacy_id_normalise.py …`"
    )

import argparse
import json
import os
import stat
from pathlib import Path
from typing import NoReturn

# The tool ships in the repo it operates on: put the checkout ahead of any
# installed copy, so the mechanics that run are the mechanics under test.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.legacy_ids import (
    LegacyIdError,
    LegacyIdPlan,
    Rename,
    apply_plan,
    build_plan,
    census,
)


class _Parser(argparse.ArgumentParser):
    """``argparse`` exits **2** on a usage error — the status this tool reserves
    for a REFUSAL (``tortoise/legacy_ids.LegacyIdError``, nothing written).

    Both write nothing, so nothing else tells them apart, and a wrapper keying on
    ``2 == refused`` would treat a typo'd invocation as a refused snapshot. A
    usage error is a **usage/IO** failure, which this tool documents as ``1``:
    ``SystemExit(<message>)`` prints the message and exits ``1``.
    """

    def error(self, message: str) -> NoReturn:  # type: ignore[override]
        self.print_usage(sys.stderr)
        raise SystemExit(f"{self.prog}: error: {message}")


def _load_json(path: Path, what: str) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"{what} not found: {path}") from exc
    except OSError as exc:
        # IsADirectoryError, PermissionError, ENOSPC on a network mount …: the
        # same class of failure as "not found", and it must not traceback.
        raise SystemExit(f"could not read {what} at {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        # This tool is pointed at `tortoise export` artifacts, which are
        # encrypted binary: "not UTF-8" is an EXPECTED refusal, not a crash.
        raise SystemExit(f"{what} is not UTF-8 text ({path}): {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{what} is not valid JSON ({path}): {exc}") from exc


def _dump_json(payload: object, path: Path | None, what: str) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path is None:
        if sys.stdout is None:
            # fd 1 closed (`>&-`): `sys.stdout` is None, and `None.write` is an
            # AttributeError — the module's own standard is a message, not a
            # traceback.
            raise SystemExit(f"could not write {what} to stdout: standard output is closed")
        sys.stdout.write(text)
        return
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"could not write {what} to {path}: {exc}") from exc
    print(f"wrote {what}: {path}", file=sys.stderr)


def _artifact_keys(path: Path) -> tuple[object, ...]:
    """Every identity ``path`` has **right now**, as mutually comparable keys.

    ⛔ ONE SHAPE FOR EVERY TARGET. The gate this replaces keyed an existing path
    as ``("inode", dev, ino)`` and a missing one as ``("path", text)``: two
    tuples of different shapes are never equal, so for every pair where ONE
    artifact already existed — the ``--dump`` itself, or a ``plan.json`` left by
    the earlier dry run the documented workflow prescribes — the rail was
    skipped by the shape mismatch alone, whatever the two names were.

    Each path therefore contributes every key it has, and two paths collide when
    they share **any** of them:

    * the symlink-resolved text (a ``..`` segment, a symlinked directory, a
      dangling symlink whose target exists only later);
    * the parent directory's identity joined with the **case-folded** name —
      ``--out plan.json --report Plan.json`` is one file on a case-insensitive
      filesystem, and during the normal ``--apply`` neither exists yet, so
      nothing on disk can settle it: the report is written first and the
      snapshot write then truncates the same inode, destroying the plan
      ``--revert`` reads. Refused on a case-SENSITIVE filesystem too, on
      purpose: the same argv may be replayed somewhere that folds case, and a
      false refusal costs a rename while a false pass costs the revert artifact;
    * the inode (``st_dev``/``st_ino``) when the path exists — the only key that
      sees a hardlink, a bind mount, or a second name on a case-folding mount.
    """
    resolved = path.resolve()
    parent = resolved.parent
    try:
        parent_stat = parent.stat()
    except OSError:
        parent_key: object = ("dir-path", str(parent))
    else:
        parent_key = ("dir", parent_stat.st_dev, parent_stat.st_ino)
    keys: list[object] = [
        ("path", str(resolved)),
        ("dir+name", parent_key, resolved.name.casefold()),
    ]
    try:
        stat = path.stat()
    except OSError:
        return tuple(keys)
    keys.append(("inode", stat.st_dev, stat.st_ino))
    return tuple(keys)


def _load_revert_plan(path: Path, dump: dict) -> LegacyIdPlan:
    """Load the renames a report records, as the plan that undoes them.

    A revert runs against the NORMALISED snapshot (whose own plan is empty), so
    :func:`build_plan` cannot derive the renames — the report is the record.
    ``dump_sha256`` comes from the report too: :func:`apply_plan` proves the
    REVERTED artifact fingerprints to it, i.e. that it is the original snapshot
    — node keys and edges — and not merely a snapshot of the same size.
    """
    payload = _load_json(path, "plan")
    if not isinstance(payload, dict) or not isinstance(payload.get("renames"), list):
        raise SystemExit(f"plan is not a report this tool wrote: {path}")
    if not payload.get("renames"):
        # The same class as a missing fingerprint, and the same home: the SNAPSHOT
        # is fine, the PLAN cannot undo anything. Exit 1 (usage/IO), not 2 — a
        # wrapper keying `2 == the snapshot is not safe` must not be pointed at a
        # healthy dump. `apply_plan` still refuses this at the API level.
        raise SystemExit(
            f"plan is not a plan to undo: {path} — it carries no renames (a no-op "
            "pass, or a revert report); the ids it did not rename are not in it"
        )
    if not payload.get("dump_sha256"):
        # Without the fingerprint the revert has no proof it restored the
        # original ARTIFACT, so `apply_plan` would refuse it — that is a
        # malformed report, i.e. the usage/IO class (exit 1), not a snapshot the
        # tool judged unsafe (exit 2). A caller keying on 2 == refused must not
        # be told a bad file was a bad snapshot.
        raise SystemExit(
            f"plan is not a report this tool wrote: {path} — it carries no "
            "`dump_sha256`, so a revert could not prove it restored the original"
        )
    renames = []
    for index, row in enumerate(payload["renames"]):
        try:
            if not isinstance(row, dict):
                raise TypeError("not an object")
            renames.append(
                Rename(
                    dump_id=int(row["dump_id"]),
                    label=str(row["label"]),
                    key_property=str(row["key_property"]),
                    old=str(row["old"]),
                    new=str(row["new"]),
                )
            )
        except (TypeError, KeyError, ValueError) as exc:
            raise SystemExit(
                f"plan is not a report this tool wrote: {path} — renames[{index}] "
                f"is not a rename row ({exc!r})"
            ) from exc
    renames = tuple(renames)
    return LegacyIdPlan(
        renames=renames,
        census=census(dump),
        dump_sha256=str(payload.get("dump_sha256") or ""),
    )


def _overwrite_blocker(path: Path, report: dict, flag: str) -> str | None:
    """Why writing ``report`` to ``path`` would lose the only way back, or None.

    A pass may legitimately rewrite its OWN plan (a re-run of step 2 over the
    same dump is byte-identical), so the test is the fingerprint, not existence.
    ``dump_sha256`` is the ORIGINAL artifact's fingerprint in both an apply and a
    revert report, which is what makes the two comparable across modes.
    """
    if path is None:
        return None  # a dry run with no --report: it writes to stdout
    try:
        if not path.is_file():
            # A sink (`/dev/null`, a pipe, a terminal) or a missing path: nothing
            # to protect, and reading a pipe here would hang.
            return None
        earlier = _load_json(path, "report")
    except SystemExit:
        return None  # not a report this tool wrote: nothing of ours to lose
    if not isinstance(earlier, dict):
        return None
    earlier_renames = earlier.get("renames")
    if not isinstance(earlier_renames, list) or not earlier_renames:
        return None
    if not report["renamed"]:
        return (
            f"this pass renames nothing and would overwrite {path} "
            f"({len(earlier_renames)} recorded rename(s)) — that is the only record of "
            f"the ids it renamed; pass a different {flag}"
        )
    if earlier.get("dump_sha256") not in (None, report.get("dump_sha256")):
        return (
            f"{path} is the plan for a DIFFERENT snapshot "
            f"({str(earlier.get('dump_sha256'))[:16]}… vs this dump's "
            f"{str(report.get('dump_sha256'))[:16]}…) and holds "
            f"{len(earlier_renames)} rename(s) — overwriting it would leave that pass "
            f"unrevertible; pass a different {flag}"
        )
    return None


def _summary(report: dict) -> str:
    found = report["census"]
    # ⛔ `legacy 1002 → 0` is NOT "no non-canonical ids remain": the pass is
    # scoped to the labels REKEY_SCOPE names, so a declared-out-of-scope label
    # keeps its ids and still fails `shape()`. Printing the residual with the
    # headline is what stops the pass being read as total — #8607's indicator
    # (1) names the `EV-*` group too, and those wait on the journaled re-key.
    # Only the keyed-but-declared-out labels count as ids this pass leaves
    # alone; an out-of-contract node has no contract key at all, so it is
    # reported as its own bucket rather than folded into that number.
    residual = sum(found["out_of_scope"].values())
    return (
        f"nodes {report['nodes_in']} in · {report['nodes_out']} out · "
        f"renamed {report['renamed']} · canonical "
        f"{report['canonical_before']} → {report['canonical_after']} · legacy "
        f"{report['legacy_before']} → {report['legacy_after']} · edges "
        f"{report['edges_in']} in · {report['edges_out']} out · "
        f"outside this pass {residual} ({found['out_of_scope']} · "
        f"out of contract {found['uncontracted']})"
    )


def _census_line(found: dict) -> str:
    out_of_scope = found["out_of_scope"]
    uncontracted = found["uncontracted"]
    ledger = found["ledger"]
    line = (
        f"nodes {found['nodes']} · in scope {found['in_scope']} · "
        f"canonical {found['canonical']} · legacy {found['legacy']} · "
        f"declared out of scope {sum(out_of_scope.values())} ({out_of_scope}) · "
        f"out of contract {sum(uncontracted.values())} ({uncontracted}) · "
        f"undeclared {sum(found['undeclared'].values())} ({found['undeclared']}) · "
        f"unresolved {sum(found['unresolved'].values())} ({found['unresolved']})"
    )
    if not ledger["balanced"]:
        # The census does not sum to the snapshot: the plan below is over a
        # SAMPLE, and saying so on the line a lane quotes is the whole point.
        line += f" · ⚠️ LEDGER UNBALANCED {ledger['accounted']}/{ledger['nodes']}"
    return line


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        description="Census and normalise legacy node ids in a graph dump (#8607 W1b)."
    )
    parser.add_argument(
        "--dump", required=True, type=Path, help="tortoise-logical-dump-v1 JSON (the snapshot)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the normalised snapshot (requires --out and --report)",
    )
    parser.add_argument(
        "--revert",
        action="store_true",
        help="undo a plan from its report (requires --out and --report)",
    )
    parser.add_argument("--out", type=Path, help="write the rewritten snapshot here")
    parser.add_argument(
        "--report",
        type=Path,
        help=(
            "write the report here in a dry run / --apply (default: stdout); in "
            "--revert mode this is the PLAN READ, never written — the revert's "
            "report goes to stdout"
        ),
    )
    args = parser.parse_args(argv)

    if args.apply and args.revert:
        parser.error("--apply and --revert are different modes")
    if args.out is not None and not (args.apply or args.revert):
        # The mirror of `--apply` without `--out`: a wrapper that names the
        # artifact but forgets the mode gets exit 0 and no snapshot, and the
        # next phase loads a stale same-named file from an earlier run.
        parser.error("--out requires --apply or --revert (the dry run writes no snapshot)")
    if (args.apply or args.revert) and not args.out:
        # The rewritten dump IS the artifact. Without --out the run validates
        # the plan, prints the report and exits 0 having written nothing — which
        # a wrapper cannot tell apart from a successful pass, and the next phase
        # would load the ORIGINAL snapshot.
        parser.error("--apply/--revert requires --out (the rewritten dump is the artifact)")
    if (args.apply or args.revert) and not args.report:
        # The plan is the only way back: `--revert` reads this file, and a
        # sha256 cannot undo a rewritten key.
        parser.error("--apply/--revert requires --report (the plan is the revert artifact)")
    # Every one of these three paths is an ARTIFACT with its own job, so no two
    # may collide. Checked pairwise because each collision loses a different
    # thing and only one of them was caught before:
    #   --out == --dump    → the run overwrites what it was asked to read
    #   --report == --dump → the report REPLACES the snapshot: a dry run that
    #                        destroys its own input, writing nothing that says so
    #   --report == --out  → the report is written first, then the snapshot
    #                        overwrites it, so the plan that undoes the pass is
    #                        gone and --revert can never run
    targets = [("--dump", args.dump), ("--out", args.out), ("--report", args.report)]
    seen: dict[object, str] = {}
    for flag, path in targets:
        if path is None:
            continue
        try:
            keys = _artifact_keys(path)
        except (OSError, RuntimeError, ValueError) as exc:
            # `_artifact_keys` stats the path and its parent and resolves the
            # name: a symlink loop raises RuntimeError, an embedded NUL raises
            # ValueError, an unreadable parent raises OSError. All three are the
            # same user error as a collision and must not traceback.
            parser.error(f"{flag} cannot be resolved ({path}): {exc}")
            raise  # unreachable — parser.error exits; keeps type checkers honest
        for key in keys:
            if key in seen:
                parser.error(
                    f"{flag} and {seen[key]} are the same file ({path}): each is a "
                    "distinct artifact and overwriting one destroys it — use "
                    "separate paths (a hardlink, a symlink, a bind mount or a name "
                    "that differs only in case is the same file too)"
                )
            seen[key] = flag

    # ⛔ AND STDOUT IS A DESTINATION TOO. In `--revert` mode the report goes to
    # stdout, so `--revert … --out restored.json > restored.json` (a shell
    # redirect to the artifact path) makes the report and the snapshot share a
    # file: the run exits 0 and the snapshot is gone — which one survives then
    # depends on stdout buffering. The path gate above cannot see it, because
    # one destination is fd 1. Same identity key, read from the fd.
    try:
        # fd 1, not sys.stdout.fileno(): under a captured/wrapped stdout the
        # object is not what the shell redirected into.
        stdout_stat = os.fstat(1)
        stdout_identity: object | None = (
            ("inode", stdout_stat.st_dev, stdout_stat.st_ino)
            if stat.S_ISREG(stdout_stat.st_mode)
            else None
        )
    except (AttributeError, OSError, ValueError):
        stdout_identity = None  # a captured or closed stdout: no file identity
    if stdout_identity is not None and stdout_identity in seen:
        collided = seen[stdout_identity]
        # ⛔ `--report /dev/stdout > plan.json` in a dry run / `--apply` IS the
        # report's one destination: the report is the only thing on stdout, so
        # naming it twice is one write to one file, not a collision. Only a
        # SECOND artifact on that file is destructive — `--dump` (reading what it
        # overwrites), `--out` (two artifacts, one file), and `--report` in
        # REVERT mode (there it is a different artifact: the plan being read).
        destructive = collided != "--report" or args.revert
        if destructive:
            parser.error(
                f"standard output is redirected to the same file as {collided}: "
                "the report would overwrite an artifact — redirect to a separate path"
            )

    dump = _load_json(args.dump, "dump")
    if not isinstance(dump, dict):
        raise SystemExit(f"dump is not a JSON object: {args.dump}")

    try:
        if args.revert:
            plan = _load_revert_plan(args.report, dump)
            out, report = apply_plan(dump, plan, revert=True)
        else:
            plan = build_plan(dump)
            if args.apply:
                out, report = apply_plan(dump, plan)
            else:
                # The dry run: the census and the plan, nothing written to the
                # snapshot. Every count a caller decides on is here.
                report = {
                    "mode": "dry-run",
                    "dump_sha256": plan.dump_sha256,
                    "census": plan.census.as_dict(),
                    "in_scope": plan.census.in_scope,
                    "renamed": len(plan.renames),
                    "legacy_remaining": plan.census.legacy,
                    "renames": [rename.as_dict() for rename in plan.renames],
                }
                out = None
    except LegacyIdError as exc:
        print("REFUSED — nothing was written:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    # ⛔ THE REPORT WRITE MUST NOT DESTROY A PLAN THAT UNDOES A DIFFERENT PASS —
    # in EVERY mode, not only `--apply`. Two measured routes that exited 0
    # before this guard, and a third that existed one mode over:
    #   0. a DRY RUN over the normalised snapshot with the same `--report`
    #      overwrites the apply-time plan with `mode: dry-run` (this is why the
    #      check cannot live inside the `--apply` branch);
    #   1. a re-run of documented step 2 over an already-normalised snapshot
    #      renames nothing and writes `renames: []` over the report holding
    #      every original id;
    #   2. a second pass over a DIFFERENT dump with the same `--report`
    #      overwrites that report with its own renames — the first pass's
    #      original ids then survive in NO file.
    # The ids are not re-derivable (`legacy_ids._derived_legacy_handle` is a
    # sha256 of the OLD key, so the old key is the input it consumes), and after
    # `rm snapshot` (the documented workflow) the report is the only way back.
    # So the check runs before EVERY report write and compares FINGERPRINTS: the
    # same dump's plan may be rewritten, a different dump's may not. ⚠️ A same-dump
    # rewrite is NOT always byte-identical, and the exception is deliberate: a
    # DRY RUN over the original dump with an existing APPLY plan rewrites the
    # report as `mode: dry-run`, which keeps `renames` and `dump_sha256` (so
    # `--revert` still works) but drops the apply-time counts. Refusing it would
    # block a legitimate re-measurement; the durable fields a revert needs are
    # the ones that survive.
    # `--report` is exempt in REVERT mode only: there it is the plan being READ.
    # `--out` is checked in BOTH modes — `--revert … --out plan_B.json` used to
    # replace another pass's plan with a snapshot and exit 0, leaving that pass
    # unrevertible from any artifact the tool wrote.
    blocked = None if args.revert else _overwrite_blocker(args.report, report, "--report")
    if blocked is None and out is not None:
        blocked = _overwrite_blocker(args.out, report, "--out")
    if blocked is not None:
        print("REFUSED — nothing was written:", file=sys.stderr)
        print(f"  - {blocked}", file=sys.stderr)
        return 2

    if out is not None:
        # ⛔ In APPLY mode the report is an OUTPUT, written FIRST: if the snapshot
        # write fails, the plan that can undo it must already exist on disk.
        # ⛔ In REVERT mode `--report` is an INPUT — it is the plan being undone —
        # so rewriting it would destroy the apply-time record (`mode: apply`, the
        # before/after counts) in the very file the docstring calls the way back.
        # The revert's own report therefore goes to stdout, and the snapshot is
        # written FIRST on that path: there is no undo record to protect, so a
        # failed snapshot write must not leave a report describing a restore that
        # did not happen.
        if args.revert:
            _dump_json(out, args.out, "snapshot")
            _dump_json(report, None, "report")
        else:
            _dump_json(report, args.report, "report")
            _dump_json(out, args.out, "snapshot")
        print(_summary(report), file=sys.stderr)
    else:
        print(_census_line(report["census"]), file=sys.stderr)
        _dump_json(report, args.report, "report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
