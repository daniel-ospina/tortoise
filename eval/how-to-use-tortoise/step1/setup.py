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

Ownership is proved on BOTH paths, not only the destructive one. ``--fresh``
deletes, so it refuses a directory (an embedded DB_PATH is always a FILE, so a
directory is the mistyped-``--db`` shape) and it refuses any file that does not
carry the ``.ax-setup.json`` marker this tool writes beside the db it seeds. A
typo therefore refuses instead of deleting.

A plain seed (no ``--fresh``) is guarded by the same rule, and that is not
redundancy — it is what makes the ``--fresh`` guard mean anything. Guarding only
the deletion left the proof mintable by the path that had no check: `_claim()`
ran on every invocation, so ONE unflagged run against an existing foreign db
wrote the very marker ``--fresh`` accepts as ownership. That single run both
seeded a write into the operator's real graph and armed its deletion for the
next ``--fresh`` run (#9695 P1).
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


def _refuse(db: str, why: str, *, fresh: bool) -> None:
    """Refuse with the reason for the path ACTUALLY taken, and what survived."""
    if fresh:
        raise SystemExit(
            f"refusing --fresh for --db {db!r}: {why}.\n"
            "NOTHING was deleted. --fresh only removes a graph this tool created "
            f"(it writes {MARKER_SUFFIX} beside the db it seeds), because a path "
            "the operator merely typed cannot prove what it names (#9695). "
            "Pass the db this tool seeded, or remove the file yourself."
        )
    raise SystemExit(
        f"refusing to seed for --db {db!r}: {why}.\n"
        "NOTHING was seeded and NOTHING was deleted. This tool only writes into "
        f"a graph it created — the {MARKER_SUFFIX} marker beside the db it seeds "
        "is that proof — because seeding a db it cannot identify would pollute "
        "that graph AND, by writing the marker, ARM ITS DELETION for the next "
        "--fresh run (#9695). Point --db at a path that does not exist yet, or "
        "remove the file yourself."
    )


def _prove_ours(db: str, target: Path, *, fresh: bool) -> None:
    """Refuse unless ``target`` is positively identified as our own graph.

    Every check is allow-list shaped: the target must BE the expected artifact
    (a regular file, carrying a marker this tool wrote, naming this path). A
    directory is the mistyped-``--db`` shape — an embedded graph is a file
    (``tortoise/config.py``) — so it can never be legitimately touched here.

    This looks only at the RESOLVED artifact; the typed-path (symlink) check is
    ``prepare``'s, because by the time a path reaches here the link has already
    been resolved away and a dangling link never reaches here at all.

    Called on the seed path as well as the delete path: an existing file this
    tool cannot identify is neither seeded into nor deleted, so no run can
    manufacture the ownership proof that ``--fresh`` trusts. The proof cannot
    be forged by a typo — writing a marker that names this exact path and
    :data:`TOOL_ID` beside a file this tool did not create is a deliberate act,
    not a mistyped ``--db``.
    """
    if target.is_dir():
        _refuse(db, "it is a directory, and an embedded graph is always a FILE", fresh=fresh)
    if not target.is_file():
        _refuse(db, "it is not a regular file", fresh=fresh)
    marker = marker_path(target)
    if not marker.is_file():
        _refuse(db, f"it carries no {MARKER_SUFFIX} marker", fresh=fresh)
    try:
        claim = json.loads(marker.read_text())
    except (OSError, ValueError) as exc:
        _refuse(db, f"its {MARKER_SUFFIX} marker is unreadable ({exc})", fresh=fresh)
    claimed = Path(str(claim.get("db", "")))
    if claim.get("tool") != TOOL_ID or claimed != target:
        _refuse(db, f"its {MARKER_SUFFIX} marker names another artifact", fresh=fresh)


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
    """Make ``db`` ready to seed, TOUCHING an existing graph only if it is ours.

    Ownership is required on both paths. Guarding only ``--fresh`` was
    defeatable from the guarded-free side: with ``fresh=False`` an existing
    foreign file was left alone but still CLAIMED (``_claim`` wrote the marker
    ``--fresh`` treats as proof) and then seeded into — polluting it, and arming
    its deletion for the next ``--fresh`` run (#9695 P1).
    """
    if _is_uri(db):
        return
    given = Path(db).expanduser()
    target = given.resolve()
    # A symlink is refused even when the link is broken: `resolve()` would put
    # the seed at the link's DESTINATION while the operator typed the link, and
    # a dangling link never reaches `_prove_ours` (its target does not exist).
    if given.is_symlink():
        _refuse(db, "it is a symlink, so the path typed is not the path written", fresh=fresh)
    if target.exists():
        _prove_ours(db, target, fresh=fresh)
        if fresh:
            target.unlink()
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
