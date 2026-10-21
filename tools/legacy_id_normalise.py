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

    # 3. undo it from that report — the pass is reversible, not one-way
    python tools/legacy_id_normalise.py --dump normalised.json \\
        --revert --report plan.json --out restored.json

Nothing here connects to a graph. The input is a ``tortoise-logical-dump-v1``
snapshot (``tortoise.hosted_backup.dump_graph``; the encrypted ``tortoise
export`` artifact wraps the same payload) and the output is a re-runnable
snapshot plus the plan that produced it. Loading it is ``restore_graph`` into a
FRESH graph; the journaled live pass is ``#7951``'s, not this tool's.

⚠️ **Every node's ``dump_id`` is preserved**, so edges (which reference
``dump_id``) survive untouched — only the identity property's value changes.
⚠️ ``--out`` may NOT be the ``--dump`` path: an in-place run with no report is
one a ``sha256`` cannot undo.

Exit codes: ``0`` planned/applied · ``2`` refused (problems on stderr, nothing
written) · ``1`` usage/IO.
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
from pathlib import Path

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


def _load_json(path: Path, what: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"{what} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{what} is not valid JSON ({path}): {exc}") from exc


def _dump_json(payload: object, path: Path | None, what: str) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path is None:
        sys.stdout.write(text)
        return
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"could not write {what} to {path}: {exc}") from exc
    print(f"wrote {what}: {path}", file=sys.stderr)


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
    renames = tuple(
        Rename(
            dump_id=int(row["dump_id"]),
            label=str(row["label"]),
            key_property=str(row["key_property"]),
            old=str(row["old"]),
            new=str(row["new"]),
        )
        for row in payload["renames"]
    )
    return LegacyIdPlan(
        renames=renames,
        census=census(dump),
        dump_sha256=str(payload.get("dump_sha256") or ""),
    )


def _summary(report: dict) -> str:
    return (
        f"nodes {report['nodes_in']} in · {report['nodes_out']} out · "
        f"renamed {report['renamed']} · canonical "
        f"{report['canonical_before']} → {report['canonical_after']} · legacy "
        f"{report['legacy_before']} → {report['legacy_after']} · edges "
        f"{report['edges_in']} in · {report['edges_out']} out"
    )


def _census_line(found: dict) -> str:
    out_of_scope = found["out_of_scope"]
    uncontracted = found["uncontracted"]
    return (
        f"nodes {found['nodes']} · in scope {found['in_scope']} · "
        f"canonical {found['canonical']} · legacy {found['legacy']} · "
        f"declared out of scope {sum(out_of_scope.values())} ({out_of_scope}) · "
        f"out of contract {sum(uncontracted.values())} ({uncontracted})"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
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
        help="write the census/plan/reconciliation report here (default: stdout)",
    )
    args = parser.parse_args(argv)

    if args.apply and args.revert:
        parser.error("--apply and --revert are different modes")
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
    if args.out is not None and args.out.resolve() == args.dump.resolve():
        parser.error(
            "--out must not be --dump: an in-place run whose plan is lost is "
            "unrecoverable, because the old ids survive only in the plan"
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

    if out is not None:
        # Report FIRST: if the snapshot write fails, the plan that can undo it
        # must already exist on disk.
        _dump_json(report, args.report, "report")
        _dump_json(out, args.out, "snapshot")
        print(_summary(report), file=sys.stderr)
    else:
        print(_census_line(report["census"]), file=sys.stderr)
        _dump_json(report, args.report, "report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
