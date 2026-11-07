#!/usr/bin/env python3
"""The #10976 EGRESS executor's operator verb — bounded, record-gated, dry by default.

`tools/cache_eviction_census.py` answers **what MAY leave the FalkorDB cache** and
refuses when nothing proves it. It is preview-only *by design* — its own docstring says
so — and it names the missing half: *"a future sweeper cannot be written without
consuming a verdict that says which classes were proven."*

`tortoise/cache_evictor.py` is that sweeper, and until this file it had **no caller**
(`git grep -n 'cache_evictor' -- tools/` → nothing), which is the same shape #9556
found for `rebuild_from_record`: *"the durability path existed and nothing could walk
it."* This is the walk.

    # 1. measure what may go (the census; preview only)
    uv run python tools/cache_eviction_census.py \\
        --graph-uri "docker://:falkordb@localhost:6379/tortoise" --graph tortoise \\
        --parity-report /tmp/turns.json --parity-graph tortoise

    # 2. DRY RUN the sweep — the default. No mutating statement is issued.
    uv run python tools/cache_evict.py \\
        --graph-uri "docker://:falkordb@localhost:6379/tortoise" --graph tortoise \\
        --parity-report /tmp/turns.json --parity-graph tortoise \\
        --population turn --max-nodes 20000 \\
        --selector my_probes.sweep:oldest_turns \\
        --record-probe my_probes.sweep:record_has_turn \\
        --out /tmp/egress-plan.json

    # 3. execute — needs BOTH the flag and a token naming the graph
    uv run python tools/cache_evict.py … --execute --confirm-graph tortoise

⛔ THREE THINGS THIS TOOL DELIBERATELY DOES NOT DECIDE
-----------------------------------------------------
1. **What "cold" means.** #10976 says *"What 'cold' should mean (settled by
   measurement, not here)"*, and the executor takes the ordering as a required input
   for the same reason. So `--selector` is REQUIRED and there is no built-in default:
   a tool that shipped one would have made that decision by accident.
2. **What the record says.** The `record_probe` is REQUIRED, per candidate, and a
   probe that raises holds the node back (the executor's gate 3). A built-in default
   would be a second, unreviewed record reader.
3. **Which population may go.** There is no default `--population`. The census's
   `plan["evictable"]` is the licence, and a population absent from it refuses.

⇒ Both pluggable seams are `module:callable` import paths, and neither is ever
defaulted. The safety does NOT rest on them being well written: the executor asks
`record_probe` immediately before every delete and holds back anything that is not an
explicit `True`, and the census's population count caps how many nodes may go.

⛔ THE LICENCE, RESTATED BECAUSE THIS IS THE FIRST THING THAT CAN ACT ON IT
--------------------------------------------------------------------------
A `PLAN` verdict means *the populations in `plan["evictable"]` are proven
record-served and may be evicted*. It is **never** a blanket licence: every class in
`plan["not_evictable"]` may not be touched, and the executor refuses a population the
plan does not name.

Exit codes (the census's own table, so a scheduler can key on ONE vocabulary):

    0  CLEAN      the cache is within its budget — NOTHING was evicted
    2  PLAN       the cache is over budget; this run produced (dry run) or EXECUTED
                  a sweep of a licensed population, so nodes may have been deleted.
                  ⛔ THREE cases report 2, and `deleted > 0` is only one of them:
                  a dry run with a plan; an executed sweep that removed nodes; and a run
                  whose `DETACH DELETE` was ISSUED but UNVERIFIED (`delete_unverified`,
                  whose `nodes_evicted` is 0 and whose `nodes_after` is `None`). So
                  `deleted == 0` alone does NOT mean nothing happened
    3  REFUSED    no mutating statement was ISSUED and nothing was removed. ⚠️ This
                  is the assertion, not the verdict word: a run that deleted nodes, or
                  whose DELETE was issued but unverified, reports 2 — because an exit
                  code is what a scheduler acts on
    4  UNBOUNDED  no ceiling anywhere; "over budget" cannot be evaluated
    1  usage      a flag, import or IO mistake

⚠️ `--execute` **does** issue mutating statements. A dry run does not, and the tests
assert that a refused/errored pass issued none.
"""
from __future__ import annotations

import sys

# The #5128 interpreter guard MUST precede every other import: a module-level
# 3.12-only import raises ImportError on an older interpreter BEFORE a later
# guard could run, and the refusal then looks like a broken tool rather than a
# floor. Print-then-`SystemExit(int)` is the shape the #5128 census's detector
# accepts (`tests/test_entry_point_python_guard.py`), and exit 1 is this tool's
# usage code. It must NOT be 2/3/4: those are this verb's PLAN / REFUSED /
# UNBOUNDED codes, and a scheduler acting on them would read an interpreter
# refusal as "a sweep may have deleted nodes". Mirrors `tools/substrate_map_check.py`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/cache_evict.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/cache_evict.py`",
        file=sys.stderr,
    )
    raise SystemExit(1)

import argparse
import contextlib
import importlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.cache_eviction import (
    CACHE_EVICTION_EXIT_CLEAN,
    CACHE_EVICTION_EXIT_PLAN,
    CACHE_EVICTION_EXIT_REFUSED,
    CACHE_EVICTION_EXIT_UNBOUNDED,
    CACHE_EVICTION_EXIT_USAGE,
    CacheReadError,
    assess,
    load_parity_report,
)
from tortoise.cache_evictor import (
    EvictionConfigError,
    EvictionPolicy,
    evict,
)


class _UsageParser(argparse.ArgumentParser):
    """Usage errors exit ``1`` — ``2`` is this tool's ``PLAN`` code, never a typo.

    Same rule, same reason as the census: a caller that keys on exit 2 to authorise
    work would otherwise authorise it on a misspelled flag.
    """

    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(CACHE_EVICTION_EXIT_USAGE)


def _load_callable(spec: str, *, flag: str) -> Callable[..., Any]:
    """Resolve ``module:callable`` — a REFUSAL (exit 1), never a silent no-op.

    ⛔ A missing seam must stop the pass. Defaulting one — to a selector that offers
    everything, or a probe that answers ``True`` — would arm the delete path with the
    exact two decisions this tool refuses to make.
    """
    if ":" not in spec:
        raise SystemExit(_usage_error(
            f"{flag} must be 'module:callable', got {spec!r}"))
    module_name, _, attr = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # any import failure is a usage error
        raise SystemExit(_usage_error(
            f"{flag} {spec!r}: cannot import {module_name!r} "
            f"({type(exc).__name__}: {exc})")) from exc
    fn = getattr(module, attr, None)
    if not callable(fn):
        raise SystemExit(_usage_error(
            f"{flag} {spec!r}: {module_name!r} has no callable {attr!r}"))
    return fn


def _usage_error(message: str) -> int:
    print(f"cache-evict: error: {message}", file=sys.stderr)
    return CACHE_EVICTION_EXIT_USAGE


def _emit(report: dict[str, Any], out: Path | None) -> None:
    """Emit the report — and NEVER lose it to a failing `--out`.

    ⛔ The report is the only record of what a destructive run did. An unwritable
    `--out` must fall back to stdout rather than raise: unguarded it turned a REFUSED
    (3) into a traceback and exit 1 — this file's USAGE code, telling the operator to
    retry FLAGS — and on the executing arm it destroyed the record after the deletes
    had already landed. Same reasoning as the executor's post-delete re-measure guard.
    """
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if out is not None:
        try:
            out.write_text(text + "\n", encoding="utf-8")
            return
        except OSError as exc:
            print(f"cache-evict: --out {out} could not be written ({exc}); "
                  f"writing to stdout instead", file=sys.stderr)
    print(text)


def main(argv: list[str] | None = None) -> int:
    # ⛔ NORMALISE argv FIRST, exactly as the census does: on the `__main__` path
    # `argv` is None, so any guard of the form `argv is not None and …` is DEAD on the
    # command line a user actually types.
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = _UsageParser(
        description=("Sweep a licensed population out of the FalkorDB cache "
                     "(#10976). DRY RUN unless --execute AND --confirm-graph."),
    )
    parser.add_argument("--graph-uri", required=True,
                        help="docker://[:pass@]host:port/graph (the target cache)")
    parser.add_argument("--graph", required=True, help="the graph name inside that URI")
    parser.add_argument("--parity-report", type=Path, action="append", default=[],
                        metavar="JSON",
                        help=("a parity instrument report (repeatable). Each needs "
                              "--parity-graph, and the report must carry its own "
                              "`graph` matching it."))
    parser.add_argument("--parity-graph", default=None, metavar="NAME",
                        help="the graph the parity report was measured against.")
    parser.add_argument("--ceiling-bytes", type=int, default=None, metavar="N",
                        help="a declared ceiling; defaults to the instance maxmemory.")
    parser.add_argument("--population", required=True,
                        help=("the population to sweep. MUST appear in the census's "
                              "plan['evictable'] — an unproven population refuses."))
    parser.add_argument("--max-nodes", type=int, required=True, metavar="N",
                        help=("the ENFORCED node budget. Required: a cache with no "
                              "declared bound is the state #10976 was filed about."))
    parser.add_argument("--batch", type=int, default=None, metavar="N",
                        help="how many nodes ONE pass may take.")
    parser.add_argument("--key-prop", default="id", metavar="PROP",
                        help="the node property holding the record key (default: id)")
    parser.add_argument("--selector", required=True, metavar="MODULE:CALLABLE",
                        help=("(graph, limit, population) -> [Candidate], ordered "
                              "cold-first by a policy YOU own. No default: #10976 "
                              "leaves 'what cold means' to measurement."))
    parser.add_argument("--record-probe", required=True, metavar="MODULE:CALLABLE",
                        help=("(Candidate) -> bool: can the record serve this node? "
                              "Asked per node before every delete; anything but an "
                              "explicit True holds the node back. No default."))
    parser.add_argument("--execute", action="store_true",
                        help="issue the deletes. Without it this is a DRY RUN.")
    parser.add_argument("--confirm-graph", default=None, metavar="NAME",
                        help=("the destructive token: must EQUAL --graph. Required "
                              "with --execute — it proves the OPERATOR authorized "
                              "this graph, not that a placeholder was left filled."))
    parser.add_argument("--out", type=Path, help="write the report here (default: stdout)")

    args = parser.parse_args(argv)

    # ⛔ The token is checked BEFORE any connection, so a mistyped confirmation cannot
    # reach a graph at all.
    if args.execute:
        if args.confirm_graph is None:
            return _usage_error(
                "--execute requires --confirm-graph <the graph name> — the token "
                "proves the operator authorized THIS cache, and a dry run is the "
                "default for a reason")
        if args.confirm_graph != args.graph:
            return _usage_error(
                f"--confirm-graph {args.confirm_graph!r} does not name --graph "
                f"{args.graph!r} — a confirmation for another graph is not one")

    if not args.parity_report:
        return _usage_error(
            "--parity-report is required: without a proven population the census "
            "refuses, and this tool does not invent a licence")

    selector = _load_callable(args.selector, flag="--selector")
    record_probe = _load_callable(args.record_probe, flag="--record-probe")

    # ⛔ ONE construction, not a second: the census owns the URI parser, the bounded
    # client and the side-effect-free existence check, and a copy here would drift from
    # all three (`tests/test_bounded_raw_clients_3065.py` pins the client's read bound).
    from tools.cache_eviction_census import (  # local: avoids a cycle at import time
        _absent_graph_refusal,
        _connection,
        _endpoint,
    )

    try:
        endpoint = _endpoint(args.graph_uri)
    except ValueError as exc:
        return _usage_error(str(exc))
    if endpoint.graph_name != args.graph:
        return _usage_error(
            f"--graph {args.graph!r} disagrees with the URI's graph "
            f"{endpoint.graph_name!r} — refusing to sweep one cache and name another")

    reports: dict[str, Any] = {}
    for path in args.parity_report:
        if args.parity_graph is None:
            return _usage_error(
                "--parity-graph is required with --parity-report: a report with no "
                "declared graph cannot be bound to this one")
        if args.parity_graph != args.graph:
            return _usage_error(
                f"--parity-graph {args.parity_graph!r} does not name --graph "
                f"{args.graph!r} — a CLEAN census of one graph does not license "
                f"evicting another")
        # ⛔ NO PRE-READ HERE. The loader is the one place that classifies an unreadable
        # report, and it does it correctly: `load_parity_report` maps a *missing* file to
        # `OSError` (the caller's flag) and a NON-UTF8 or malformed body to
        # `CacheReadError` — a REFUSAL about the STORE (3), not about the flags. A
        # `Path.read_text()` guard here caught only `OSError`, so a non-UTF8 report
        # raised `UnicodeDecodeError` (a `ValueError`) straight out of `main`: a
        # traceback and exit 1, this file's USAGE code, telling the operator to retry
        # FLAGS against corrupt evidence — the exact case the census pins and this file's
        # own comment twenty lines below promises.
        try:
            report = load_parity_report(str(path), graph_name=args.graph)
        except CacheReadError as exc:
            # A report this tool cannot read is a REFUSAL, not a usage error: the cache
            # may not be evicted on evidence nobody could parse. Same rule, same
            # reason, as the census — this tool only acts where it refuses.
            print("REFUSED — a parity report could not be read, so nothing is claimed:",
                  file=sys.stderr)
            print(f"  - {exc}", file=sys.stderr)
            return CACHE_EVICTION_EXIT_REFUSED
        except OSError as exc:
            return _usage_error(f"cannot read --parity-report {path}: {exc}")
        if report.instrument in reports:
            # ⛔ LAST-WINS IS A SAFETY BUG (the census's own finding): a REFUSED report
            # followed by a stale CLEAN one licensed a plan, so the verdict depended on
            # argv order. Two reports for one instrument cannot both be the evidence.
            return _usage_error(
                f"two --parity-report files claim instrument {report.instrument!r} — "
                f"a verdict must not depend on argument order")
        reports[report.instrument] = report

    client = graph = None
    try:
        try:
            client, graph = _connection(endpoint)
        except Exception as exc:  # any dial fault is a store refusal
            # ⛔ A REFUSAL (3), not a traceback and not exit 1. The census this tool
            # composes from already draws this line ("An unreachable cache is a
            # refusal (3), not a typo"); unguarded here, a dial failure escaped as a
            # traceback whose exit 1 is this file's USAGE code — telling the operator to
            # retry FLAGS against a store nobody could reach.
            _emit({"verdict": "REFUSED",
                   "reason": (f"the cache could not be reached "
                              f"({type(exc).__name__}: {exc}) — nothing is claimed")},
                  args.out)
            return CACHE_EVICTION_EXIT_REFUSED
        # ⛔ A graph that is not on the instance is REFUSED, never opened: reading a
        # non-existent graph CREATES it. The check is the census's, unchanged.
        absent = _absent_graph_refusal(client, args.graph)
        if absent is not None:
            _emit({"verdict": "REFUSED", "reason": absent}, args.out)
            return CACHE_EVICTION_EXIT_REFUSED
        try:
            assessment = assess(client, graph, args.graph,
                                ceiling_bytes=args.ceiling_bytes,
                                parity_reports=reports)
        except Exception as exc:  # any measurement fault is a refusal
            # ⛔ A cache nobody could MEASURE is a refusal (3), never a traceback: the
            # census guards this exact call the same way, and this file's own table
            # reserves exit 1 for a flag/IO mistake — which would tell the operator to
            # retry FLAGS against a store that did not answer.
            _emit({"verdict": "REFUSED",
                   "reason": (f"the cache could not be measured "
                              f"({type(exc).__name__}: {exc}) — nothing is claimed")},
                  args.out)
            return CACHE_EVICTION_EXIT_REFUSED
        if assessment.verdict == "UNBOUNDED":
            _emit({"verdict": "UNBOUNDED", "census": assessment.as_dict()}, args.out)
            return CACHE_EVICTION_EXIT_UNBOUNDED
        try:
            policy = EvictionPolicy.resolve(max_nodes=args.max_nodes,
                                            batch_size=args.batch)
        except EvictionConfigError as exc:
            _emit({"verdict": "REFUSED", "reason": f"config refused: {exc}",
                   "census": assessment.as_dict()}, args.out)
            return CACHE_EVICTION_EXIT_REFUSED

        report = evict(assessment, client, graph,
                       population=args.population,
                       selector=selector, record_probe=record_probe,
                       key_prop=args.key_prop,
                       dry_run=not args.execute, policy=policy)
    finally:
        for handle in (client, graph):
            closer = getattr(handle, "close", None)
            if callable(closer):
                # A close failure is not the verdict: the sweep already happened.
                with contextlib.suppress(Exception):
                    closer()

    body = {
        "graph": args.graph,
        "population": args.population,
        "executed": bool(args.execute),
        "eviction": report.as_dict(),
        # ⛔ `metric()` CALLED. `report.metric` is a bound method, and `_emit`
        # serialises with `default=str`, so the operator's machine-readable record
        # of a DESTRUCTIVE run became `"<bound method …>"` — no `nodes_evicted`, no
        # `bytes_reclaimed`, no `delete_unverified`. The module calls `metric()`
        # "the documented, flat, stable-key surface"; this is the only caller that
        # ever emits it.
        "metric": report.metric(),
        "census": assessment.as_dict(),
    }
    _emit(body, args.out)

    # ⛔ EXIT 0 MEANS "NOTHING WAS EVICTED". A completed sweep that DID delete nodes
    # must not share it, or a scheduler cannot tell "nothing to do" from "deleted a
    # batch" — and the destructive half is the half it needs. An UNMEASURED after-count
    # is `None`, never a fabricated 0.
    # ⛔ BEFORE the refusal arm: `evict`'s abort path returns `verdict="REFUSED"` WITH
    # `deleted` populated (a store fault mid-sweep, after nodes were committed). Reading
    # that as exit 3 — "nothing may go" — records "nothing happened" for a destructive
    # run whose after-state is UNKNOWN, which is the same conflation exit 0 was fixed
    # for. Nodes went, so the code must say so.
    # ⛔ `delete_unverified` COUNTS TOO. The executor sets it when the `DETACH DELETE`
    # statement RAISED (or removed fewer rows than planned) — i.e. the statement was
    # ISSUED and the server may already have applied it, even with `deleted == 0` on the
    # first deleting pass. Reading only `deleted` recorded "nothing happened" (exit 3)
    # for exactly the run whose effect is unknowable, which is the conflation this arm
    # exists to prevent.
    if report.deleted or report.delete_unverified:
        return CACHE_EVICTION_EXIT_PLAN
    if report.verdict == "REFUSED":
        return CACHE_EVICTION_EXIT_REFUSED
    if report.verdict == "CLEAN":
        return CACHE_EVICTION_EXIT_CLEAN
    return CACHE_EVICTION_EXIT_PLAN


if __name__ == "__main__":
    raise SystemExit(main())
