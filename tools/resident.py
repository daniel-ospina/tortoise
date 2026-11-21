#!/usr/bin/env python3
"""*Keep this resident* + *see what's outside* — the two controls the notices promise (#14012).

`#13998`'s approved copy offers *"add capacity · keep specific things fast (pin) · see
what's outside"*, and `website/docs.html#storage` states both in the plain voice a
customer reads. A promise in an email that no control can honour is worse than no email,
so this verb is the control behind the copy:

    # keep two points resident (their connected nodes come with them)
    uv run python tools/resident.py pin --graph-uri docker://:falkordb@localhost:6379/tortoise \\
        --graph tortoise --key 4f2a… --key 9b11… --window-nodes 20000

    # what is kept resident, how much of the fast layer it claims, and whether it is legal
    uv run python tools/resident.py show --graph-uri … --graph tortoise --window-nodes 20000

    # what is outside the fast layer — how much, roughly when, and what it was about
    uv run python tools/resident.py outside --graph-uri … --graph tortoise \\
        --dsn postgresql://… --org-id org_… --graph-id g_…

    uv run python tools/resident.py unpin --graph-uri … --graph tortoise --key 4f2a…

⛔ THE THREE RULES THAT MAKE THE COPY TRUE, AND WHERE EACH ONE LIVES
--------------------------------------------------------------------
1. **"together with the things connected to them"** — a pin resolves to its 1-hop
   closure and the eviction gate holds back the CLOSURE, not just the declared key
   (`tortoise/resident_pin.py`).
2. **"has a limit, so it can never take over the fast layer"** — the budget. `pin`
   REFUSES a declaration over it (`exit 3`, the number named), and `show` reports a set
   that already exceeds it. ⛔ The cap is never enforced by trimming: a pin that silently
   became "most of what you asked for" misreports the user's declaration as honoured.
3. **"nothing is lost"** — neither verb deletes anything from the record. `pin`/`unpin`
   write one `:Meta` declaration in the cache; `outside` only reads. The record is
   untouched by both, and `outside`'s own count comes FROM the record.

Exit codes — the census's vocabulary, so a scheduler keys on one table
---------------------------------------------------------------------
    0  OK          the declaration was applied / read cleanly, or the view was measured
    3  REFUSED     a DECLARED refusal: over the pin budget, over the degree ceiling, a
                   key the fast layer does not hold, an unreadable declaration, or a pin
                   set whose budget no longer covers it. Nothing was written.
    4  UNMEASURED  what is outside could not be measured (a partial resident read, an
                   unreachable record). ⛔ NOT zero: a missing measurement read as a
                   healthy zero is the defect both controls exist to avoid.
    1  usage       a flag, import or IO mistake
"""
from __future__ import annotations

import sys

# The #5128 interpreter guard precedes every other import: a 3.12-only import at module
# level raises ImportError before a later guard could run, and the refusal then looks
# like a broken tool rather than a floor. Mirrors `tools/cache_evict.py`; exit 1 is this
# tool's usage code and must not be 3/4, which are this verb's REFUSED/UNMEASURED codes.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/resident.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/resident.py`",
        file=sys.stderr,
    )
    raise SystemExit(1)

import argparse
import contextlib
import json
import os
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_REFUSED = 3
EXIT_UNMEASURED = 4


class _UsageParser(argparse.ArgumentParser):
    """``error()`` → exit 1, never argparse's 2 (which is the census's PLAN code)."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _emit(body: dict[str, Any], out: Path | None) -> None:
    text = json.dumps(body, indent=2, sort_keys=True, default=str)
    if out is None:
        print(text)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = _UsageParser(
        description=("Keep things resident in the fast layer, and see what is outside "
                     "it (#14012). Nothing here deletes anything from your record."),
    )
    sub = parser.add_subparsers(dest="verb", required=True, parser_class=_UsageParser)

    def _graph_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--graph-uri", required=True,
                       help="docker://[:pass@]host:port/graph (the fast layer)")
        p.add_argument("--graph", required=True, help="the graph name inside that URI")
        p.add_argument("--label", default="Point",
                       help="the label a pin addresses (default: Point)")
        p.add_argument("--key-prop", default="id", metavar="PROP",
                       help="the node property holding the record key (default: id)")
        p.add_argument("--window-nodes", type=int, default=None, metavar="N",
                       help=("the residency window's node budget — the number the pin "
                             "cap is derived from. Defaults to "
                             "$TORTOISE_CACHE_MAX_NODES."))
        p.add_argument("--max-pins", type=int, default=None, metavar="N",
                       help=("the pin budget, closure included. Defaults to a tenth of "
                             "the window; it may never equal the window."))
        p.add_argument("--max-degree", type=int, default=None, metavar="N",
                       help="the degree ceiling (§7); a bigger hub is refused.")
        p.add_argument("--out", type=Path,
                       help="write the report here (default: stdout)")

    p_pin = sub.add_parser("pin", help="declare keys resident (with their neighbours)")
    _graph_flags(p_pin)
    p_pin.add_argument("--key", action="append", required=True, metavar="KEY",
                       help="a record key to keep resident (repeatable)")

    p_unpin = sub.add_parser("unpin", help="withdraw keys from the declaration")
    _graph_flags(p_unpin)
    p_unpin.add_argument("--key", action="append", required=True, metavar="KEY",
                         help="a record key to release (repeatable)")

    p_show = sub.add_parser("show", help="what is kept resident, and is it within budget")
    _graph_flags(p_show)

    p_out = sub.add_parser("outside", help="what the fast layer does not hold")
    _graph_flags(p_out)
    p_out.add_argument("--dsn", default=None,
                       help=("the record DSN (default: $TORTOISE_RECORD_DSN). The view "
                             "is read FROM the record — it is the superset."))
    p_out.add_argument("--org-id", required=True, help="the tenant scope")
    p_out.add_argument("--graph-id", required=True, help="the graph's record id")
    p_out.add_argument("--topic-column", default=None, metavar="COL",
                       help="what 'what it was about' groups by (default: point_kind)")
    p_out.add_argument("--top", type=int, default=None, metavar="N",
                       help="how many dominant kinds to name (default: 5)")
    p_out.add_argument("--max-keys", type=int, default=None, metavar="N",
                       help=("the most resident keys ONE read may pull. Exceeding it is "
                             "UNMEASURED (exit 4), never a shortened count."))

    args = parser.parse_args(argv)

    # ⛔ ONE construction, not a second: the census owns the URI parser, the bounded
    # client and the side-effect-free existence check.
    from tools.cache_eviction_census import _connection, _endpoint

    try:
        endpoint = _endpoint(args.graph_uri)
    except ValueError as exc:
        parser.error(str(exc))
        return EXIT_USAGE  # unreachable; parser.error exits
    if endpoint.graph_name != args.graph:
        parser.error(
            f"--graph {args.graph!r} disagrees with the URI's graph "
            f"{endpoint.graph_name!r} — refusing to act on one graph and name another"
        )

    from tortoise import outside_view, resident_pin

    window = args.window_nodes
    if window is None:
        raw = os.environ.get(resident_pin.EVICTION_MAX_NODES_ENV)
        if raw is not None and raw.strip():
            try:
                window = int(raw)
            except ValueError:
                parser.error(
                    f"{resident_pin.EVICTION_MAX_NODES_ENV}={raw!r} is not an integer"
                )
    try:
        budget = resident_pin.PinBudget.resolve(
            max_nodes=args.max_pins, max_degree=args.max_degree, window_nodes=window
        )
    except resident_pin.PinConfigError as exc:
        _emit({"verdict": "REFUSED", "reason": f"config refused: {exc}"}, args.out)
        return EXIT_REFUSED

    client = graph = None
    try:
        try:
            client, graph = _connection(endpoint)
        except Exception as exc:  # any dial fault is a refusal, never a traceback
            _emit({"verdict": "REFUSED",
                   "reason": (f"the fast layer could not be reached "
                              f"({type(exc).__name__}: {exc}) — nothing is claimed")},
                  args.out)
            return EXIT_REFUSED
        # A graph that is not on the instance is REFUSED, never opened: reading a
        # non-existent graph CREATES it (the census's rule, unchanged).
        from tools.cache_eviction_census import _absent_graph_refusal
        absent = _absent_graph_refusal(client, args.graph)
        if absent is not None:
            _emit({"verdict": "REFUSED", "reason": absent}, args.out)
            return EXIT_REFUSED

        if args.verb == "pin":
            try:
                result = resident_pin.pin(
                    graph, args.key, label=args.label, key_prop=args.key_prop,
                    budget=budget,
                )
            except resident_pin.PinRefused as exc:
                _emit({"verdict": "REFUSED", "graph": args.graph,
                       "code": exc.code, "reason": exc.reason,
                       "budget": budget.as_dict()}, args.out)
                return EXIT_REFUSED
            _emit({"verdict": "OK", "graph": args.graph, "pin": result.as_dict()},
                  args.out)
            return EXIT_OK

        if args.verb == "unpin":
            try:
                result = resident_pin.unpin(
                    graph, args.key, label=args.label, key_prop=args.key_prop,
                    budget=budget,
                )
            except resident_pin.PinRefused as exc:
                _emit({"verdict": "REFUSED", "graph": args.graph,
                       "code": exc.code, "reason": exc.reason,
                       "budget": budget.as_dict()}, args.out)
                return EXIT_REFUSED
            _emit({"verdict": "OK", "graph": args.graph, "pin": result.as_dict()},
                  args.out)
            return EXIT_OK

        if args.verb == "show":
            reading = resident_pin.read(graph, budget=budget)
            refusal = reading.gate_reason()
            _emit({
                "verdict": "REFUSED" if refusal else "OK",
                "graph": args.graph,
                "reason": refusal or "",
                "pin": reading.as_dict(),
            }, args.out)
            return EXIT_REFUSED if refusal else EXIT_OK

        # `outside`
        dsn = args.dsn or os.environ.get("TORTOISE_RECORD_DSN")
        if not dsn:
            parser.error(
                "--dsn (or $TORTOISE_RECORD_DSN) is required: what is outside is "
                "measured FROM the record, which is the superset"
            )
        try:
            import psycopg
        except ImportError:  # pragma: no cover — the parquet/psycopg extra
            _emit({"verdict": "UNMEASURED",
                   "reason": ("the record driver is not installed (psycopg) — how much "
                              "is outside cannot be measured, so nothing is claimed")},
                  args.out)
            return EXIT_UNMEASURED
        try:
            conn = psycopg.connect(dsn)
        except Exception as exc:
            _emit({"verdict": "UNMEASURED",
                   "reason": (f"the record could not be reached "
                              f"({type(exc).__name__}: {exc}) — nothing is claimed")},
                  args.out)
            return EXIT_UNMEASURED
        try:
            view = outside_view.read(
                graph, conn,
                org_id=args.org_id, graph_id=args.graph_id,
                label=args.label, key_prop=args.key_prop,
                topic_column=args.topic_column or outside_view.TOPIC_COLUMN,
                top=args.top or outside_view.DEFAULT_TOP_TOPICS,
                key_limit=args.max_keys or outside_view.DEFAULT_RESIDENT_KEY_LIMIT,
            )
        finally:
            with contextlib.suppress(Exception):
                conn.close()
        _emit({"verdict": "OK" if view.complete else "UNMEASURED",
               "graph": args.graph, "outside": view.as_dict()}, args.out)
        return EXIT_OK if view.complete else EXIT_UNMEASURED
    finally:
        for handle in (client, graph):
            closer = getattr(handle, "close", None)
            if callable(closer):
                with contextlib.suppress(Exception):
                    closer()


if __name__ == "__main__":
    raise SystemExit(main())
