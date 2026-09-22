#!/usr/bin/env python3
"""purge_test_residue — remove KNOWN test-residue Points from a real store (#4028).

Why this exists
---------------
The owner's real embedded store (``~/.tortoise/tortoise.db``) accumulated 17
Points written by test/tooling paths: ``guard-remove-test``, ``test point``,
``source point``, ``target point``, ``mcp edge claim``, ``mcp new claim zz``,
``mcp old claim zz``, ``dedup test sw_<hex>`` and ``no ctx sw_<hex>``. All are
``status='draft'``, but 17 of them carried a stored ``embedding`` — so the
vector leg of the default ``tortoise_search`` surface was made of test data,
and the hybrid surface answered *every* query (including
``quantum flux capacitor calibration``) with those Points.

That is a DATA defect, separate from the ranking defect fixed in
``tortoise/search_engine.py`` (#4028): the ranking floor stops an unrelated
query being answered by a sub-relevance near neighbour, but a residue Point
that happens to clear the floor still sits in the graph. The two fixes are
complementary — this tool removes the residue, the floor generalises the
behaviour.

Design contract
---------------
0. **The writer is tracked separately (#4071).** This tool removes the residue
   already in a store; it does NOT stop new residue landing, and a residue
   writer that reaches the canonical embedded store is still open (a second
   copy of the real store taken 30 min after the first held 6 NEW
   ``guard-remove-test`` Points). A purge is therefore not one-shot: re-run it
   after the #4071 guard lands.
1. **Content-pattern match ONLY.** The tool deletes a Point only when its
   ``content`` matches one of the curated, ANCHORED residue signatures below.
   It never deletes by "short content", "draft status", "has an embedding" or
   any other property that could describe a genuine claim — the #4028 store
   ALSO held a genuine embedded Point ("Premise Labs inbound intake system
   should auto-investigate …"), which must survive.
2. **Dry-run by default.** ``--apply`` is required to delete anything. A
   dry run prints exactly what would go.
3. **Deletes through ``TortoiseSDK.delete_point``** — the graph-write path
   the ``how-to-use-tortoise`` skill sanctions (emits ``PointRetracted``,
   garbage-collects orphaned tags, marks neighbours dirty, invalidates EP
   messages). Never a raw ``DETACH DELETE``.
4. **Refuses a remote/hosted target** unless ``--allow-remote`` is passed:
   test residue is a local embedded-store story; a remote purge should be a
   deliberate act.

Usage::

    # dry run against the canonical embedded store (default)
    python3 tools/purge_test_residue.py

    # dry run against a specific file
    python3 tools/purge_test_residue.py --db /tmp/copy/tortoise.db

    # actually delete
    python3 tools/purge_test_residue.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: Curated, ANCHORED signatures of test residue observed on a real store in
#: #4028. Every pattern is pinned to the WHOLE (stripped) content so a real
#: claim that merely contains the words cannot match. Extend deliberately and
#: record the source — a broad pattern here deletes user data.
RESIDUE_PATTERNS: tuple[str, ...] = (
    r"guard-remove-test",
    r"test point",
    r"source point",
    r"target point",
    r"mcp edge claim",
    r"mcp new claim zz",
    r"mcp old claim zz",
    r"dedup test sw_[0-9a-f]{8}",
    r"no ctx sw_[0-9a-f]{8}",
)

RESIDUE_RE = re.compile(r"^(?:" + "|".join(RESIDUE_PATTERNS) + r")$")


def is_residue(content: str | None) -> bool:
    """True when ``content`` is one of the curated test-residue signatures."""
    return bool(content) and bool(RESIDUE_RE.match(content.strip()))


def find_residue(graph) -> list[dict]:
    """Return ``[{id, content, has_embedding}]`` for every residue Point."""
    rows = graph.query(
        "MATCH (p:Point) WHERE p.content IS NOT NULL "
        "RETURN p.id, p.content, p.embedding IS NOT NULL"
    ).result_set
    out = []
    for row in rows:
        pid, content = row[0], row[1]
        if is_residue(content):
            out.append({
                "id": pid,
                "content": content,
                "has_embedding": bool(row[2]) if len(row) > 2 else False,
            })
    # deterministic report order
    out.sort(key=lambda r: (r["content"], r["id"]))
    return out


def purge(store, *, apply: bool) -> dict:
    """Report (dry-run) or delete (``apply=True``) the residue in ``store``.

    ``store`` is a ``TortoiseSDK``. Returns
    ``{"path", "found", "deleted", "points": [...]}``.
    """
    graph = store._get_proj().g
    found = find_residue(graph)
    deleted: list[str] = []
    if apply:
        for entry in found:
            if store.delete_point(entry["id"]):
                deleted.append(entry["id"])
    return {
        "found": len(found),
        "deleted": len(deleted),
        "points": found,
    }


def _resolve_target(db: str | None) -> str:
    from tortoise.config import is_db_uri, resolve_db_path

    if db:
        if is_db_uri(db):
            # The remote gate lives in ``main`` (a single check), so
            # --allow-remote works for the --db URI form as well as the
            # TORTOISE_DB_URI env form.
            return db
        return str(Path(db).expanduser())
    uri = os.environ.get("TORTOISE_DB_URI")
    if uri and is_db_uri(uri):
        return uri
    return resolve_db_path()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", help="embedded store path (default: canonical "
                                     "TORTOISE_DB_PATH / ~/.tortoise/tortoise.db)")
    parser.add_argument("--apply", action="store_true",
                        help="delete the matches (default: dry run)")
    parser.add_argument("--expect", type=int, default=None,
                        help="with --apply: abort unless exactly N residue "
                             "Points were found (guards against an accidental "
                             "--apply hitting an unexpected set)")
    parser.add_argument("--allow-remote", action="store_true",
                        help="permit a docker:// / redis:// URI target")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    from tortoise.config import is_db_uri
    from tortoise.sdk import TortoiseSDK

    target = _resolve_target(args.db)
    if is_db_uri(target) and not args.allow_remote:
        raise SystemExit(
            f"target {target!r} is a remote/hosted URI — refusing to purge "
            "without --allow-remote (test residue is a local embedded-store "
            "story).")

    if is_db_uri(target):
        # ``TortoiseSDK`` reads a URI only from the env (db_path is the
        # embedded file path) — bind it explicitly, or the tool would purge
        # whatever TORTOISE_DB_URI/canonical path happens to be set while
        # reporting the URI the operator asked for (#4028 review P1).
        os.environ["TORTOISE_DB_URI"] = target
        sdk = TortoiseSDK()
    else:
        sdk = TortoiseSDK(db_path=target)
    try:
        # The --expect guard must run BEFORE any deletion, on a dry-run scan.
        if args.apply and args.expect is not None:
            preflight = purge(sdk, apply=False)
            if preflight["found"] != args.expect:
                raise SystemExit(
                    f"--expect {args.expect} but {preflight['found']} residue "
                    "Points were found — refusing to delete an unexpected "
                    "set. Re-run the dry run and review.")
        report = purge(sdk, apply=args.apply)
    finally:
        sdk.close()

    report["path"] = target
    report["applied"] = bool(args.apply)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    verb = "Deleted" if args.apply else "Would delete"
    print(f"store: {target}")
    print(f"test residue found: {report['found']}")
    for entry in report["points"]:
        emb = " [embedded]" if entry["has_embedding"] else ""
        print(f"  - {entry['id']}  {entry['content']!r}{emb}")
    print(f"{verb}: {report['found'] if not args.apply else report['deleted']}")
    if not args.apply and report["found"]:
        print("\nDry run — nothing changed. Re-run with --apply to delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
