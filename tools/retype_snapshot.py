#!/usr/bin/env python3
"""Re-type a logical graph dump OFFLINE — #8882 Phase 2 mechanics.

Two modes, both pure transformations of a ``tortoise-logical-dump-v1`` file
(``tortoise.hosted_backup.dump_graph``; the encrypted ``tortoise export``
artifact wraps the same payload):

1. **propose** — write a starter decisions file covering EVERY node, with the
   ONTOLOGY §5 layer rules applied where they are unambiguous and
   ``needs-decision`` (with the reason) everywhere else. This is the handoff to
   whatever labels the graph (a model, or a human) — the file is sorted and
   deterministic, so two runs on one snapshot produce the same bytes.

2. **apply** — read the decisions, validate them against the fail-closed
   invariants (partition, kind legality, operator, identity contract), and emit
   the re-typed dump plus a reconciliation report. Refuses with the FULL problem
   list rather than producing a partial artifact.

    python tools/retype_snapshot.py --dump snapshot.json --propose decisions.json
    python tools/retype_snapshot.py --dump snapshot.json \\
        --decisions decisions.json --out retyped.json --report report.json

Nothing here connects to a graph: the snapshot is the input and a re-runnable
re-typed dump is the output. Loading it is ``restore_graph`` into the FRESH
graph (Phase 4) — this tool never touches a live one.

``--out`` is REQUIRED in apply mode: the re-typed dump is the whole point of the
run, so a mode that would validate everything and discard it must not exit ``0``
(a wrapper script cannot tell that apart from a successful conversion, and the
next phase would restore the original snapshot).

Exit codes: ``0`` ok · ``2`` refused (problems on stderr) · ``1`` usage/IO.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.12-only import would fail first.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/retype_snapshot.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/retype_snapshot.py …`"
    )

import argparse
import json
from pathlib import Path

# The tool ships in the repo it operates on: put the checkout ahead of any
# installed copy, so the mechanics that run are the mechanics under test.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.retype import (
    RETYPE_TARGETS,
    RetypeError,
    apply_plan,
    build_plan,
    propose,
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
    path.write_text(text, encoding="utf-8")
    print(f"wrote {what}: {path}", file=sys.stderr)


def _parse_extra_kinds(values: list[str]) -> dict[str, list[str]]:
    extra: dict[str, list[str]] = {}
    for raw in values:
        label, _, kind = raw.partition("=")
        if label not in RETYPE_TARGETS or not kind:
            raise SystemExit(
                f"--allow-kind expects LABEL=KIND with LABEL one of "
                f"{', '.join(RETYPE_TARGETS)} (got {raw!r})"
            )
        extra.setdefault(label, []).append(kind)
    return extra


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-type a logical graph dump offline (#8882 Phase 2)."
    )
    parser.add_argument(
        "--dump", required=True, type=Path, help="tortoise-logical-dump-v1 JSON (the snapshot)"
    )
    parser.add_argument(
        "--propose", type=Path, help="write a starter decisions file covering every node"
    )
    parser.add_argument("--decisions", type=Path, help="decisions file to validate and apply")
    parser.add_argument("--out", type=Path, help="write the re-typed dump here (apply mode)")
    parser.add_argument(
        "--report", type=Path, help="write the reconciliation report here (default: stdout)"
    )
    parser.add_argument(
        "--allow-kind",
        action="append",
        default=[],
        metavar="LABEL=KIND",
        help="permit a pack-declared kind the core vocabulary does not carry (repeatable)",
    )
    parser.add_argument(
        "--keep-embedding",
        action="store_true",
        help="carry a moved node's Point-derived vector onto an "
        ":Object/:Subject instead of dropping it (#8419)",
    )
    args = parser.parse_args(argv)

    if not args.propose and not args.decisions:
        parser.error("one of --propose or --decisions is required")
    if args.propose and args.decisions:
        parser.error("--propose and --decisions are different modes")
    if args.decisions and not args.out:
        # The re-typed dump IS the artifact. Without --out the run validates the
        # plan, prints the report and exits 0 having written nothing — which a
        # wrapper cannot tell apart from a successful conversion, and the next
        # phase would load the ORIGINAL snapshot. Refuse instead of succeeding
        # with nothing written.
        parser.error("--decisions requires --out (the re-typed dump is the artifact)")

    dump = _load_json(args.dump, "dump")
    if not isinstance(dump, dict):
        raise SystemExit(f"dump is not a JSON object: {args.dump}")

    try:
        if args.propose:
            proposals = propose(dump)
            _dump_json({"decisions": proposals}, args.propose, "proposals")
            undecided = sum(1 for row in proposals if row.get("label") == "needs-decision")
            print(f"nodes: {len(proposals)} · needs a decision: {undecided}", file=sys.stderr)
            return 0

        decisions = _load_json(args.decisions, "decisions")
        plan = build_plan(dump, decisions, extra_kinds=_parse_extra_kinds(args.allow_kind))
        retyped, report = apply_plan(dump, plan, keep_embedding=args.keep_embedding)
    except RetypeError as exc:
        print("REFUSED — nothing was written:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    if args.out:
        _dump_json(retyped, args.out, "re-typed dump")
    _dump_json(report, args.report, "report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
