#!/usr/bin/env python3
"""The #10976 EGRESS census — what MAY leave the FalkorDB cache, and the refusal.

**Preview only. There is no `--execute`, and that is the deliverable, not an
omission.** `#10976` gates eviction on the record being able to serve — *"gated on
#10593 + #7879 … A pass that cannot answer that must refuse, never sweep"* — and
both
gates are OPEN, so a sweep today would delete nodes whose only copy is the graph.
The `--execute` flag is therefore REFUSED BY NAME rather than absent, so a caller
that tries it learns why instead of concluding the flag was misspelled.

    # measure the cache, name every class that cannot be proven, and refuse.
    # ⚠️ `--graph` must be the graph the URI names — a mismatch is a USAGE error, not
    # a silent measurement of a different cache:
    uv run python tools/cache_eviction_census.py \\
        --graph-uri "docker://:falkordb@localhost:6379/tortoise" --graph tortoise \\
        --out /tmp/egress-census.json

    # …with the one class that HAS a parity instrument already measured. The report
    # comes from that instrument's `--json` (it has no `--out`), and the graph it was
    # measured on is named by `--parity-graph` — REQUIRED, and required to equal
    # `--graph`, because the instrument emits no graph identity of its own:
    uv run python tools/turn_parity_census.py --uri "$TORTOISE_DB_URI" \\
        --graph-name tortoise --json /tmp/turns.json
    uv run python tools/cache_eviction_census.py \\
        --graph-uri "docker://:falkordb@localhost:6379/tortoise" --graph tortoise \\
        --parity-report /tmp/turns.json --parity-graph tortoise

    # a declared ceiling, instead of the instance's maxmemory:
    TORTOISE_CACHE_CEILING_BYTES=150000000 uv run python tools/cache_eviction_census.py …

Exit codes — ⚠️ **only ``0`` and ``1`` line up with the sibling censuses**. `graph_census.py`
and `turn_parity_census.py` both use ``2`` for a REFUSAL; here ``2`` is the LICENCE and
``3`` is the refusal, because this tool needs to tell "nothing may go" from "these
populations may go". Never share one exit-code table across the pipeline — read
``plan["evictable"]``, not the code alone.

    0  CLEAN     measured; the cache is within its bound — nothing to evict
    2  PLAN      over the bound, and `plan["evictable"]` names the ONLY populations
                 that are proven record-served
    3  REFUSED   over the bound and NOTHING is proven — nothing may be evicted, and
                 the report names each unproven class and why
    4  UNBOUNDED no ceiling is declared and `maxmemory` is 0 (unlimited) — nothing
                 bounds this cache, so "over the bound" cannot be evaluated
    1  usage     a flag or IO mistake

⛔ EXIT 2 IS NOT A BLANKET LICENCE. It means *the populations in
`plan["evictable"]` may go*; every class in `plan["not_evictable"]` (and in
`problems`, with its reason) may NOT be touched. A partial plan is safe by
construction — evicting exactly the proven populations is what #10976's "0
evictions of a node the record cannot serve" asks for — but a sweeper that reads
exit 2 as "sweep everything" would sweep the unproven classes too, so a caller MUST
read the plan rather than the exit code alone.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/cache_eviction_census.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/cache_eviction_census.py …`"
    )

import argparse
import json
from pathlib import Path

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

#: The one flag this tool refuses BY NAME. It is not "not implemented yet": the
#: ordering constraint in #10976 says no pass may sweep while the record cannot
#: serve, and the gate is a property of the store, not of this CLI's maturity.
_REFUSED_FLAGS = ("--execute", "--sweep", "--purge", "--evict")


def _parse_uri(uri: str) -> tuple[str, int, str | None, str]:
    """Parse a Tortoise URI into ``(host, port, password, graph_name)``.

    Accepts the shapes the repo uses: ``docker://[:pass@]host:port/graph`` and a bare
    ``host:port/graph``. Anything else is a USAGE error — guessing a host is how a
    census ends up measuring a different cache than the caller meant, which is the
    one thing a number an owner decides on must never do.

    ⚠️ PARSING IS SEPARATE FROM CONNECTING on purpose: a request that is going to be
    refused (a `--graph` that disagrees with the URI, a destructive flag) must not
    open a socket first, or a typo reads back as a network fault.
    """
    raw = uri
    for scheme in ("docker://", "redis://", "bolt://"):
        if raw.startswith(scheme):
            raw = raw[len(scheme):]
            break

    credentials, _, rest = raw.rpartition("@")
    password = None
    if credentials:
        _, _, password = credentials.partition(":")
        password = password or None
    hostport, _, graph_name = rest.partition("/")
    host, _, port = hostport.partition(":")
    if not host or not port or not graph_name:
        raise ValueError(
            f"cannot read a host:port/graph out of {uri!r} — expected "
            f"docker://[:password@]host:port/graph"
        )
    return host, int(port), password, graph_name


def _connection(host: str, port: int, password: str | None, graph_name: str):
    """A redis client and a FalkorDB graph handle for an already-parsed URI.

    ⛔ BOTH clients take their socket bounds from the product's ONE resolver
    (``_bounded_server_socket_kwargs``), never from kwargs spelled here.
    ``falkordb.FalkorDB`` defaults ``socket_timeout=None`` and hands that to
    redis-py explicitly, so an unbounded handle parks this census in ``recv()``
    forever against a stalled engine — alive, 0% CPU, printing nothing — which
    for a tool whose whole contract is *measure the cache and change nothing*
    is indistinguishable from a cache that answered slowly (#3065, #8118).
    The literals this replaced (10s connect / 60s read) were bounded but
    knob-blind: ``TORTOISE_DB_SOCKET_TIMEOUT`` — the operator's own bound —
    never reached them, which is the same defect one step milder.
    """
    import redis
    from falkordb import FalkorDB

    from tortoise.projection import _bounded_server_socket_kwargs

    client = redis.Redis(
        host=host, port=port, password=password, decode_responses=True,
        **_bounded_server_socket_kwargs(),
    )
    db = FalkorDB(host=host, port=port, password=password,
                  **_bounded_server_socket_kwargs())
    return client, db.select_graph(graph_name)


def _usage_error(parser: argparse.ArgumentParser, message: str) -> int:
    parser.print_usage(sys.stderr)
    print(f"{parser.prog}: error: {message}", file=sys.stderr)
    return CACHE_EVICTION_EXIT_USAGE


class _UsageParser(argparse.ArgumentParser):
    """Usage errors exit ``1`` — ``2`` is reserved for a PLAN, never a typo.

    argparse's default is ``2``, which is this tool's ``PLAN`` code. A caller that
    keys on exit 2 to authorise a sweep would then authorise one on a misspelled
    flag. `tools/turn_parity_census.py` created this subclass for the same reason;
    the vocabulary only works if ``1`` and ``2`` mean what they say.

    ⛔ And exit 2 means ONLY: *the populations in ``plan["evictable"]`` are proven
    record-served and may be evicted; every class in ``plan["not_evictable"]`` may NOT
    be touched.* It is never a blanket licence for the cache.
    """

    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(CACHE_EVICTION_EXIT_USAGE)


def main(argv: list[str] | None = None) -> int:
    # ⛔ NORMALISE argv FIRST. The `__main__` path calls `main()` with no argument, so
    # a guard written as `argv is not None and …` is DEAD on the command line a user
    # actually types: the flag reaches argparse, prints `unrecognized arguments`, and
    # exits 2 — the PLAN code, i.e. "proven, go sweep". Reading `sys.argv` here is what
    # makes the refusal real rather than documented.
    argv = list(sys.argv[1:] if argv is None else argv)

    # Refuse a destructive flag BEFORE argparse, so a caller who types one learns why
    # instead of concluding it was misspelled.
    for flag in _REFUSED_FLAGS:
        if any(a == flag or a.startswith(f"{flag}=") for a in argv):
            print(
                f"REFUSED {flag}: this census does not sweep, and cannot.\n"
                f"  #10976 gates eviction on the record being able to serve "
                f"(#10593 hosted record writes are rejected 22P02; #7879 the record "
                f"epic) — both open. A pass that cannot prove the record serves a "
                f"class must refuse rather than sweep.\n"
                f"  Run without {flag} to get the plan and the refusal reasons.",
                file=sys.stderr,
            )
            return CACHE_EVICTION_EXIT_REFUSED

    parser = _UsageParser(
        description="Census what MAY leave the FalkorDB cache (#10976). Preview only.",
    )
    parser.add_argument("--graph-uri", required=True,
                        help="docker://[:pass@]host:port/graph (the target cache)")
    parser.add_argument("--graph", required=True, help="the graph name inside that URI")
    parser.add_argument("--parity-report", type=Path, action="append", default=[],
                        metavar="JSON",
                        help=("a parity instrument report (e.g. from "
                              "tools/turn_parity_census.py --json). Repeatable; "
                              "each needs --parity-graph."))
    parser.add_argument("--parity-graph", default=None, metavar="NAME",
                        help=("the graph the parity report was measured against. "
                              "REQUIRED with --parity-report: the instrument emits no "
                              "graph identity, so without this a CLEAN census of one "
                              "graph would license evicting another. An operator "
                              "ASSERTION, and the census says so."))
    parser.add_argument("--ceiling-bytes", type=int, default=None,
                        metavar="N",
                        help=("a declared ceiling; defaults to "
                              "$TORTOISE_CACHE_CEILING_BYTES, then to the instance's "
                              "maxmemory (which is a write refusal, not a policy)"))
    parser.add_argument("--out", type=Path, help="write the census here (default: stdout)")
    args = parser.parse_args(argv)

    try:
        host, port, password, graph_name = _parse_uri(args.graph_uri)
    except ValueError as exc:
        return _usage_error(parser, str(exc))
    if graph_name != args.graph:
        return _usage_error(
            parser,
            f"--graph {args.graph!r} disagrees with the URI's graph {graph_name!r} — "
            f"refusing to measure one cache and name another",
        )
    # ⛔ VALIDATE EVERYTHING BEFORE CONNECTING. A request that will be refused must not
    # open a socket first: an unreachable host would otherwise turn a flag mistake —
    # or a destructive flag — into a network fault, and the caller would debug the
    # wrong thing (measured: `--parity-report` without `--parity-graph` exited on a
    # `ConnectionError` to a host it should never have contacted).

    reports = {}
    if args.parity_report and not args.parity_graph:
        return _usage_error(
            parser,
            "--parity-report requires --parity-graph: the instrument emits no graph "
            "identity, so without naming the graph it measured a CLEAN census of one "
            "graph would license evicting another",
        )
    if args.parity_graph is not None and args.parity_graph != args.graph:
        # ⛔ THE ASSERTION MUST NAME THE GRAPH BEING MEASURED. Comparing the report
        # against an operator-typed string that may name a DIFFERENT graph made the
        # binding vacuous: `--graph A --parity-report <clean census of B>
        # --parity-graph B` produced a PLAN for A on evidence about B. Requiring
        # equality to `--graph` (itself pinned to the URI) is what closes it.
        return _usage_error(
            parser,
            f"--parity-graph {args.parity_graph!r} must be the graph being measured "
            f"({args.graph!r}) — a CLEAN census of one graph does not license "
            f"evicting another",
        )
    for path in args.parity_report:
        try:
            report = load_parity_report(str(path), graph_name=args.parity_graph)
        except CacheReadError as exc:
            # A report this tool cannot read is a REFUSAL, not a usage error: the
            # cache may not be evicted on evidence nobody could parse, and that is a
            # statement about the store's safety, not about the caller's flags.
            print("REFUSED — a parity report could not be read, so nothing is claimed:",
                  file=sys.stderr)
            print(f"  - {exc}", file=sys.stderr)
            return CACHE_EVICTION_EXIT_REFUSED
        except OSError as exc:
            return _usage_error(parser, f"--parity-report {path}: {exc}")
        if report.instrument in reports:
            # ⛔ LAST-WINS IS A SAFETY BUG: a REFUSED report followed by a stale CLEAN
            # one for the same instrument licensed a plan, so the verdict depended on
            # argv order. Two reports for one instrument cannot both be the evidence.
            return _usage_error(
                parser,
                f"two --parity-report files claim instrument {report.instrument!r} — "
                f"a verdict must not depend on argument order",
            )
        reports[report.instrument] = report

    try:
        # ⛔ INSIDE the try: `FalkorDB(...)` connects eagerly, so an unreachable cache
        # raised a traceback and exited 1 — the USAGE code — telling the caller to
        # retry flags against a store nobody could reach. An unreachable cache is a
        # refusal (3), not a typo.
        client, graph = _connection(host, port, password, graph_name)
    except Exception as exc:
        print("REFUSED — the cache could not be reached, so nothing is claimed:",
              file=sys.stderr)
        print(f"  - {type(exc).__name__}: {exc}", file=sys.stderr)
        return CACHE_EVICTION_EXIT_REFUSED

    try:
        census = assess(
            client, graph, graph_name,
            ceiling_bytes=args.ceiling_bytes,
            parity_reports=reports,
        )
    except CacheReadError as exc:
        print("REFUSED — the cache could not be measured, so nothing is claimed:",
              file=sys.stderr)
        print(f"  - {exc}", file=sys.stderr)
        return CACHE_EVICTION_EXIT_REFUSED
    except Exception as exc:
        # A cache the census cannot CLASSIFY (e.g. projection's config registry
        # raised) is not a flag mistake. Exit 1 would invite the caller to retry
        # flags instead of refusing, and the config registry's failure is
        # deliberately NOT softened to an empty set — so it lands here.
        print("REFUSED — the census could not classify this cache, so nothing is claimed:",
              file=sys.stderr)
        print(f"  - {type(exc).__name__}: {exc}", file=sys.stderr)
        return CACHE_EVICTION_EXIT_REFUSED

    payload = census.as_dict()
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote census: {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)

    state = census.state
    print(
        f"\n{graph_name}: resident {state.resident_bytes} B vs bound "
        f"{state.bound_bytes} B [{state.ceiling_source}] · policy "
        f"{state.maxmemory_policy!r} · graph key ttl "
        f"{state.graph_key_ttl_seconds if state.graph_key_ttl_seconds is not None else 'none'} "
        f"· {state.keys_per_graph} key(s) per graph",
        file=sys.stderr,
    )
    print(f"verdict: {census.verdict}", file=sys.stderr)

    if census.verdict == "UNBOUNDED":
        print("⚠️ NO BOUND: the cache is unbounded, so nothing may be sized against "
              "it. Declare one (TORTOISE_CACHE_CEILING_BYTES) or set maxmemory.",
              file=sys.stderr)
        return CACHE_EVICTION_EXIT_UNBOUNDED
    if census.verdict == "CLEAN":
        return CACHE_EVICTION_EXIT_CLEAN
    if census.verdict == "PLAN":
        for entry in census.plan.get("evictable", []):
            print(f"  evictable: {entry['population']} ({entry['nodes']} nodes, via "
                  f"{entry['instrument']})", file=sys.stderr)
        for problem in census.problems:
            print(f"  not evictable: {problem}", file=sys.stderr)
        print("  ⛔ exit 2 licenses ONLY the populations listed above — not the cache.",
              file=sys.stderr)
        return CACHE_EVICTION_EXIT_PLAN

    print("REFUSED — nothing may be evicted; every class below is unproven:",
          file=sys.stderr)
    for problem in census.problems:
        print(f"  - {problem}", file=sys.stderr)
    return CACHE_EVICTION_EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
