#!/usr/bin/env python3
"""Census a logical graph dump OFFLINE — #8882 **Phase 1**.

Read-only. It never connects to a graph, never writes one, and never drops a
node: the input is a ``tortoise-logical-dump-v1`` snapshot (what
``hosted_backup.dump_graph`` emits, and what ``tortoise export`` wraps) and the
output is a report of counts per class plus the proposed keep/drop predicate.

    # measure one snapshot, with 5 sample nodes per rule for a human to eyeball
    python tools/graph_census.py --dump snapshot.json --samples 5 --out census.json

    # the plan's own comparison: the curated snapshot against the hosted graph's
    python tools/graph_census.py --dump local.json --diff hosted.json --out diff.json

    # re-cost the predicate at a different fragment cut, or with a subset of rules
    python tools/graph_census.py --dump snapshot.json --fragment-max-chars 60
    python tools/graph_census.py --dump snapshot.json --rules extracted,isolated

Exit codes: ``0`` measured — the report is written, and a TRUNCATED ARTIFACT
(declared-vs-counted disagreement → ``warnings``) or a DUPLICATE NODE IDENTITY
(→ ``identities`` and a warning) is reported IN THE REPORT rather than refused ·
``1`` usage/IO (a flag mistake — an unknown, empty or repeated ``--rules``, a
negative ``--samples`` / ``--fragment-max-chars``, ``--samples`` with ``--diff``
— or a missing or non-JSON file, or a JSON document that is NOT AN OBJECT) ·
``2`` refused (the dump itself is STRUCTURALLY unusable — structural CORRUPTION
such as a non-integer or repeated ``dump_id`` / non-dict prop bag / non-list
label set, a missing node or edge list, a DANGLING EDGE — and the problem list
is on stderr, with nothing written to ``--out``). Argument errors therefore exit
``1``, not argparse's default ``2``: a caller must be able to tell "I passed the
wrong flags" from "this dump cannot be measured" without parsing stderr. Every
flag-value mistake is validated by argparse (a ``type=`` callable raising
``ArgumentTypeError``), so it takes the tested ``error()`` path and cannot drift
into the refusal code.

Why the refusals exist: a census that silently measured a different population
than the re-typing pass will consume is worse than no census — it would be a
number an owner decides on. So a structurally malformed dump or a dangling edge
is refused with the full problem list rather than reported around.

Two shapes are deliberately MEASURED, not refused (#8907 review): a truncated
artifact (the dump's own ``node_count`` / ``edge_count`` disagree with what it
carries) and a duplicate node identity (one key shared by more than one node).
Both stay exit ``0`` because the counts still reconcile against the artifact
actually in hand — refusing would hide the measurement behind an error, and the
447 duplicate identity groups on the #8882 snapshot would make that snapshot
unmeasurable altogether — so the fact is carried where a reader cannot miss it:
top-level ``warnings`` (both) and ``identities`` (the collision). A caller
deciding from exit code alone therefore treats ``0`` as "measured" and MUST read
``warnings`` before trusting the numbers; ``2`` is reserved for a dump this
instrument cannot measure at all. (An unknown rule id is refused by
:func:`census` for API callers too; the CLI validates ``--rules`` first as the
usage error it is, so it exits ``1``.)
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/graph_census.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/graph_census.py …`"
    )

import argparse
import json
from pathlib import Path

# The tool ships in the repo it operates on: put the checkout ahead of any
# installed copy, so the mechanics that run are the mechanics under test.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.graph_census import (
    DEFAULT_RULES,
    FRAGMENT_MAX_CHARS,
    CensusError,
    census,
    diff_census,
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


def _parse_rules(raw: str) -> list[str]:
    """Parse ``--rules``, refusing a bad value as a USAGE error (exit ``1``).

    ``ArgumentTypeError`` is what argparse turns into ``error()``; every adjacent
    branch in here used to raise ``CensusError`` (or a bare ``SystemExit``), which
    meant a mistyped rule id and a corrupt dump were indistinguishable to a caller
    keying on the exit code — the exact ambiguity this CLI's split exists to remove.
    """
    rules = [item.strip() for item in raw.split(",") if item.strip()]
    if not rules:
        raise argparse.ArgumentTypeError(
            f"no rule id selected; known ids are {list(DEFAULT_RULES)!r}"
        )
    unknown = [rule for rule in rules if rule not in DEFAULT_RULES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown id(s) {sorted(unknown)!r}; known ids are {list(DEFAULT_RULES)!r}"
        )
    if len(set(rules)) != len(rules):
        raise argparse.ArgumentTypeError(f"repeated id(s) in {rules!r}")
    return rules


def _non_negative_int(raw: str) -> int:
    """An integer flag value, refusing a negative one as a USAGE error (exit ``1``)."""
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an integer: {raw!r}") from exc
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0 (got {value})")
    return value


class _UsageParser(argparse.ArgumentParser):
    """Usage errors exit ``1`` — ``2`` is reserved for a REFUSED dump.

    argparse's default is ``2``, which would make "you typed ``--samples`` with
    ``--diff``" indistinguishable from "this dump is corrupt" to any caller
    keying on the exit code.
    """

    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = _UsageParser(
        description="Census a logical graph dump offline (#8882 Phase 1)."
    )
    parser.add_argument(
        "--dump",
        required=True,
        type=Path,
        help="tortoise-logical-dump-v1 JSON (the snapshot to measure)",
    )
    parser.add_argument(
        "--diff",
        type=Path,
        metavar="OTHER_DUMP",
        help="census OTHER_DUMP as well and report every count that moved",
    )
    parser.add_argument(
        "--rules",
        type=_parse_rules,
        default=list(DEFAULT_RULES),
        help=f"comma-separated subset of the proposed predicate (default: {','.join(DEFAULT_RULES)})",
    )
    parser.add_argument(
        "--fragment-max-chars",
        type=_non_negative_int,
        default=FRAGMENT_MAX_CHARS,
        metavar="N",
        help=f"fragment threshold in characters (default: {FRAGMENT_MAX_CHARS})",
    )
    parser.add_argument(
        "--samples",
        type=_non_negative_int,
        default=0,
        metavar="N",
        help="include up to N dropped nodes per rule for human review (default: 0)",
    )
    parser.add_argument("--out", type=Path, help="write the report here (default: stdout)")
    args = parser.parse_args(argv)

    # Refuse a silently-dropped flag: --samples is a per-rule evidence sample
    # taken from a keep/drop decision, and --diff makes no decision — accepting it
    # and ignoring it would report a sample set the caller thinks they have.
    if args.diff and args.samples:
        parser.error("--samples cannot be combined with --diff (a diff has no drop samples)")

    dump = _load_json(args.dump, "dump")
    if not isinstance(dump, dict):
        raise SystemExit(f"dump is not a JSON object: {args.dump}")

    try:
        if args.diff:
            other = _load_json(args.diff, "diff dump")
            if not isinstance(other, dict):
                raise SystemExit(f"--diff is not a JSON object: {args.diff}")
            report = diff_census(
                dump,
                other,
                rules=args.rules,
                fragment_max_chars=args.fragment_max_chars,
            )
        else:
            report = census(
                dump,
                rules=args.rules,
                fragment_max_chars=args.fragment_max_chars,
                samples=args.samples,
            )
    except CensusError as exc:
        print("REFUSED — no report was written:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    _dump_json(report, args.out, "census")

    if args.diff:
        labels = sorted(report["moved_labels"])
        suffix = f" · moved labels: {', '.join(labels)}" if labels else ""
        print(f"changed paths: {report['changed_paths']}{suffix}", file=sys.stderr)
        return 0
    predicate = report["predicate"]
    print(
        f"nodes: {report['totals']['nodes']} · edges: {report['totals']['edges']} · "
        f"predicate keeps {predicate['keep']} / drops {predicate['drop']} "
        f"(droppable {predicate['droppable']})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
