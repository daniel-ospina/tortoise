#!/usr/bin/env python3
"""Seed the pre-existing graph for the INCREMENTAL Step-1 inputs (#9675).

Four of the ten inputs are not standalone: the interesting write failures only
happen *against existing knowledge* —

* ``correction_existing_claim`` — supersede vs edit (the old claim must survive),
* ``duplicate_entity``         — re-mention vs a second node,
* ``contradicting_claim``      — NAND against a Point vs dampening an operator,
* ``product_spec_object``      — v1 replaced by v2 as a lifecycle fold.

Each fixture writes the "before" graph a real memory would already hold. Nothing
here is guidance for the eval agent: it is state, and the ids are printed so the
run record can be read back against them.

Usage:
    uv run python eval/how-to-use-tortoise/step1/setup.py \
        --id duplicate_entity --db /tmp/ax-eval/duplicate_entity.db

``--fresh`` deletes, so it is guarded: it refuses a directory (an embedded
DB_PATH is always a FILE, so a directory is the mistyped-``--db`` shape) and it
refuses any file that does not carry the ``.ax-setup.json`` marker this tool
writes beside the db it seeds. A typo therefore refuses instead of deleting.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

TOOL_ID = "ax_write_eval.step1.setup"
MARKER_SUFFIX = ".ax-setup.json"


def marker_path(db: Path) -> Path:
    """The ownership marker written beside a db this tool seeded."""
    return db.with_name(db.name + MARKER_SUFFIX)


def _is_uri(db: str) -> bool:
    return db.startswith(("docker://", "redis://", "rediss://"))


def _refuse(db: str, why: str) -> None:
    raise SystemExit(
        f"refusing --fresh for --db {db!r}: {why}.\n"
        "NOTHING was deleted. --fresh only removes a graph this tool created "
        f"(it writes {MARKER_SUFFIX} beside the db it seeds), because a path "
        "the operator merely typed cannot prove what it names (#9695). "
        "Pass the db this tool seeded, or remove the file yourself."
    )


def _delete_own_graph(db: str, given: Path, target: Path) -> None:
    """Delete ``target`` only if it is positively identified as our own graph.

    Every check is allow-list shaped: the target must BE the expected artifact
    (a regular file, carrying a marker this tool wrote, naming this path). A
    directory is the mistyped-``--db`` shape — an embedded graph is a file
    (``tortoise/config.py``) — so it can never be legitimately deleted here.
    """
    if given.is_symlink():
        _refuse(db, "it is a symlink, so the path typed is not the path deleted")
    if target.is_dir():
        _refuse(db, "it is a directory, and an embedded graph is always a FILE")
    if not target.is_file():
        _refuse(db, "it is not a regular file")
    marker = marker_path(target)
    if not marker.is_file():
        _refuse(db, f"it carries no {MARKER_SUFFIX} marker")
    try:
        claim = json.loads(marker.read_text())
    except (OSError, ValueError) as exc:
        _refuse(db, f"its {MARKER_SUFFIX} marker is unreadable ({exc})")
    claimed = Path(str(claim.get("db", "")))
    if claim.get("tool") != TOOL_ID or claimed != target:
        _refuse(db, f"its {MARKER_SUFFIX} marker names another artifact")
    target.unlink()


def _claim(target: Path, input_id: str) -> None:
    """Write the ownership marker BEFORE the graph exists.

    Claiming first is what makes an interrupted seed still recoverable: the next
    ``--fresh`` run can prove the half-written file is ours.
    """
    marker_path(target).write_text(
        json.dumps(
            {
                "tool": TOOL_ID,
                "db": str(target),
                "input": input_id,
                "claimed_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
        + "\n"
    )


def prepare(db: str, input_id: str, *, fresh: bool) -> None:
    """Make ``db`` ready to seed, deleting an existing graph ONLY if it is ours."""
    if _is_uri(db):
        # A server graph carries no marker file beside it, so this tool can never
        # PROVE the graph it would delete is the one it seeded — the same reason
        # `_delete_own_graph` refuses a path it cannot positively identify. It
        # used to return here silently, which made `--fresh` a no-op that then
        # seeded ON TOP of the existing graph: the fixture's own duplicate got
        # charged to the agent as its write failure. Refusing is the fail-closed
        # direction (#9695 review P1).
        if fresh:
            raise SystemExit(
                f"refusing --fresh for --db {db!r}: a server graph ({db.split(':', 1)[0]}://) "
                "cannot be identified as one this tool seeded — it has no marker "
                f"file beside it (this tool writes {MARKER_SUFFIX} only for a "
                "filesystem db).\n"
                "NOTHING was deleted and NOTHING was seeded. Drop the graph "
                "yourself, then re-run without --fresh."
            )
        return
    given = Path(db).expanduser()
    target = given.resolve()
    if fresh and target.exists():
        _delete_own_graph(db, given, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _claim(target, input_id)


def _open(db: str):
    if db.startswith(("docker://", "redis://", "rediss://")):
        os.environ["TORTOISE_DB_URI"] = db
        os.environ.pop("TORTOISE_DB_PATH", None)
    else:
        os.environ.pop("TORTOISE_DB_URI", None)
        os.environ["TORTOISE_DB_PATH"] = db
    from tortoise.sdk import TortoiseSDK

    return TortoiseSDK()


def seed(input_id: str, db: str) -> dict:
    """Create the before-graph for ``input_id`` and return the ids minted."""
    sdk = _open(db)
    minted: dict[str, str] = {}
    try:
        if input_id == "correction_existing_claim":
            p1 = sdk.create_point(
                "statement",
                "Our Q3 churn was 4.1%.",
                when="2026-10-01",
            )
            minted["P1_churn_4_1"] = p1["id"]
            src = sdk.create_source(
                "https://dash.internal.example/churn/q3",
                "document",
                tier="T1",
                title="Internal churn dashboard, Q3 export",
            )
            minted["source"] = src["url"]
            # NOTE: deliberately NO provenance edge here. `extractedFrom` is in
            # §3.3 but NOT in §3.9's `create_edge` vocabulary, so the structural
            # edge verb refuses it; the working route is an argument on the
            # create — `create_point(..., extractedFrom=[url])` — which stamps
            # the edge. The fixture leaves it off so the eval measures whether an
            # agent finds that route, rather than papering over it.

        elif input_id == "duplicate_entity":
            o1 = sdk.create_object("Northwind Logistics", objectKind="other")
            minted["O1_northwind"] = o1["id"]

        elif input_id == "contradicting_claim":
            p2 = sdk.create_point(
                "statement",
                "Solstice Robotics passed its safety audit on 2026-08-28.",
            )
            p3 = sdk.create_point("statement", "Acme released tranche 2.")
            op = sdk.create_operator("IMPL", p2["id"], [p3["id"]])
            minted["P2_audit_passed"] = p2["id"]
            minted["P3_tranche_released"] = p3["id"]
            minted["op_impl"] = op.get("id", "") if isinstance(op, dict) else str(op)

        elif input_id == "product_spec_object":
            o1 = sdk.create_object(
                "Beta memory API plan v1",
                objectKind="plan",
                goalState="active",
                status="live",
            )
            minted["O1_plan_v1"] = o1["id"]

        else:
            raise SystemExit(f"{input_id!r} is not an incremental input — no setup needed")
    finally:
        sdk.close()
    return minted


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="delete the db first (refused unless this tool created it)",
    )
    args = ap.parse_args()

    prepare(args.db, args.id, fresh=args.fresh)
    minted = seed(args.id, args.db)
    print(json.dumps({"input": args.id, "db": args.db, "minted": minted}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
