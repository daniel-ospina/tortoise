#!/usr/bin/env python3
"""The #10976 EGRESS census — what MAY leave the FalkorDB cache, and the refusal.

**Preview only. There is no `--execute`, and that is the deliverable, not an
omission.** `#10976` gates eviction on the record being able to serve — *"gated on
#10593 + #7879 … A pass that cannot answer that must refuse, never sweep"* — and
both
gates are OPEN, so a sweep today would delete nodes whose only copy is the graph.
The `--execute` flag is therefore REFUSED BY NAME rather than absent, so a caller
that tries it learns why instead of concluding the flag was misspelled.

⛔ **A graph that is not on the instance is REFUSED, never measured.** Reading a
non-existent graph CREATES it (FalkorDB materialises the keyspace on the first query,
measured — see `tools/engine_probe.py`), so a typo'd `--graph` would leave an empty
graph behind on the production cache and then be reported on as if it were an empty
one. `GRAPH.LIST` creates nothing, so this census lists first and refuses, and every
count it issues afterwards goes through `GRAPH.RO_QUERY` (`tortoise/cache_eviction.py`),
which cannot mint either.

    # measure the cache, name every class that cannot be proven, and refuse.
    # ⚠️ `--graph` must be the graph the URI names — a mismatch is a USAGE error, not
    # a silent measurement of a different cache:
    uv run python tools/cache_eviction_census.py \\
        --graph-uri "docker://:falkordb@localhost:6379/tortoise" --graph tortoise \\
        --out /tmp/egress-census.json

    # …with the one class that HAS a parity instrument already measured. The report
    # comes from that instrument's `--json` (it has no `--out`), and it carries the
    # graph it measured (`graph`, emitted by `turn_parity_census.census`).
    # `--parity-graph` is still REQUIRED and still must equal `--graph`: it is the
    # operator's declaration, and the report's own identity must MATCH it — the binding
    # no longer rests on the flag alone (review P1 on #11004: the loader used to fill a
    # missing graph in with the caller's own name, so the check could never fail):
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

    0  CLEAN     measured; the cache is within its declared BYTE ceiling.
                 ⛔ This is the BYTE state, not "nothing to do": `plan["evictable"]`
                 is still published (read it, as the note below insists) and a
                 consumer enforcing an ENTRY budget may still have work to do.
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


def _endpoint(uri: str):
    """Resolve *uri* into a FalkorDB endpoint — via the repo's ONE derivation.

    ⛔ NOT A SECOND URI PARSER. ``tortoise.projection.resolve_db_endpoint``
    (``tortoise/projection/__init__.py``) is THE canonical URI → endpoint derivation:
    ``FalkorProjection.from_uri`` (every product connection) and
    ``tortoise.backup._bgsave`` both call it, so a census can never dial a different
    instance than the product it is measuring. The hand-rolled parse this replaces
    returned only ``(host, port, password, graph)``: it DROPPED ``username`` and
    ``ssl``, so a ``rediss://`` endpoint was a usage error (``rediss`` parsed as the
    host) and an ACL user was silently discarded. ``tools/edge_census.py``'s sibling
    census already reuses the canonical derivation for exactly this reason.

    The ONE rule kept local is a REFUSAL, not a second derivation: the URI must NAME
    a host and a port. The canonical rule fills defaults (``localhost`` / ``16379``)
    when the URI names neither, which for this tool means measuring an instance the
    operator never named — the one thing a number an owner decides on must never do.

    ⛔ THE DEFAULT IS APPLIED BY THE CANONICAL RULE ITSELF (``port=parsed.port or
    16379``), so "did the operator name one?" cannot be answered before resolving —
    ``0`` is a NAMED port, and it is falsy. Checking only ``port is None`` before the
    call let ``docker://h:0/g`` through and then measured **16379** (the embedded
    lane's default) while the operator had written a different instance. So the test
    is made AFTER resolution, on the value that will actually be dialled: the port
    the census connects to must be the port this URI wrote, or it is a refusal.

    ⚠️ PARSING IS SEPARATE FROM CONNECTING on purpose: a request that is going to be
    refused (a `--graph` that disagrees with the URI, a destructive flag, a graph that
    is not on the instance) must not open a socket first, or a typo reads back as a
    network fault.
    """
    from urllib.parse import urlparse

    from tortoise.projection import resolve_db_endpoint

    declared = urlparse(uri)
    # `declared.port` raises ValueError for a non-numeric port, which is the same
    # USAGE outcome the caller already handles.
    if not declared.hostname or declared.port is None:
        raise ValueError(
            f"cannot read a host:port/graph out of {uri!r} — expected "
            f"docker://[:user:password@]host:port/graph"
        )
    endpoint = resolve_db_endpoint(uri)
    if endpoint.port != declared.port:
        raise ValueError(
            f"{uri!r} names port {declared.port!r}, which the canonical derivation "
            f"would dial as {endpoint.port!r} (it substitutes 16379 for a falsy "
            f"port) — refusing to measure an instance nobody named"
        )
    return endpoint


def _absent_graph_refusal(client, graph_name: str) -> str | None:
    """The refusal message when *graph_name* is not on the instance, else ``None``.

    ⛔ A READ AGAINST A MISSING GRAPH STILL HAS A SIDE EFFECT: it CREATES the graph.
    FalkorDB materialises the keyspace on the first query, so on a typo'd ``--graph``
    (both halves can be typos — ``--graph``/URI equality only proves the operator
    typed the same wrong name twice) or a graph that has been dropped, the census
    would leave an empty graph behind on the PRODUCTION instance and then report a
    verdict about it, indistinguishable from a genuinely empty cache. Measured and
    written down three times in this repo: ``tools/engine_probe.py``
    (*"QUERY MINTS the graph … a probe against a missing name created it and it
    appeared in GRAPH.LIST"*), ``tools/backfill_graph_turns.py`` and
    ``tools/edge_census.py``, the last two shipping ``_assert_graph_exists``.

    ``GRAPH.LIST`` does not itself create anything (verified against a live instance
    by ``tools/edge_census.py``), so listing first is the side-effect-free check.
    This is the operator-facing message; the structural guarantee is
    ``tortoise/cache_eviction.py``'s use of ``GRAPH.RO_QUERY``, which cannot mint even
    if this guard is ever removed.
    """
    try:
        names = {str(name) for name in (client.execute_command("GRAPH.LIST") or [])}
    except Exception as exc:
        # ⛔ FAIL CLOSED. "I could not list the graphs" is not "the graph is there":
        # proceeding would measure through the verb that can mint.
        return (f"the instance's graph list could not be read "
                f"({type(exc).__name__}: {exc}) — refusing to be the thing that "
                f"creates {graph_name!r}")
    if graph_name in names:
        return None
    return (f"graph {graph_name!r} does not exist on this instance "
            f"({len(names)} graph(s) there) — and reading a non-existent graph CREATES "
            f"it (FalkorDB materialises the keyspace on the first query), so this "
            f"census will not be the thing that creates it. Check the name.")


def _connection(endpoint):
    """A redis client and a FalkorDB graph handle for an already-resolved endpoint.

    ⛔ EVERY field of the endpoint comes from the canonical derivation — including
    ``username`` and ``ssl`` — so a TLS endpoint and an ACL user reach both clients.

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

    # ⛔ The resolver is splatted INLINE in both calls, not hoisted into a local:
    # `tests/test_bounded_raw_clients_3065.py` is the systemic pin that a tool-surface
    # client carries a read bound, and it reads the CALL's keywords. Hoisting the
    # splat made this file's two clients read as UNBOUNDED to that gate.
    client = redis.Redis(
        host=endpoint.host, port=endpoint.port,
        username=endpoint.username, password=endpoint.password,
        ssl=endpoint.ssl, decode_responses=True,
        **_bounded_server_socket_kwargs(),
    )
    db = FalkorDB(host=endpoint.host, port=endpoint.port,
                  username=endpoint.username, password=endpoint.password,
                  ssl=endpoint.ssl,
                  **_bounded_server_socket_kwargs())
    return client, db.select_graph(endpoint.graph_name)


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
                              "REQUIRED with --parity-report, and required to equal "
                              "--graph. The report must ALSO carry its own `graph` "
                              "(`tools/turn_parity_census.py --json` emits it) and "
                              "that name must match this one: a report naming no "
                              "graph is REFUSED, so a CLEAN census of another graph "
                              "cannot be re-badged by repeating --graph here. "
                              "⚠️ The NAME check is name-only: a graph of the same "
                              "name on a different INSTANCE, or measured EARLIER, is "
                              "NOT detected here. STALENESS is bounded downstream on "
                              "two axes: the report's own `measured_at` (a report "
                              "carrying no measurement time, or one measured EARLIER "
                              "than this pass's bound — $TORTOISE_PARITY_REPORT_MAX_AGE_S, "
                              "default 3600 s — is demoted to cannot-answer), and then "
                              "the population's own resident count (a report claiming "
                              "more nodes than its population now holds is REFUSED, "
                              "as is a claim above the graph's whole node count)."))
    parser.add_argument("--ceiling-bytes", type=int, default=None,
                        metavar="N",
                        help=("a declared ceiling; defaults to "
                              "$TORTOISE_CACHE_CEILING_BYTES, then to the instance's "
                              "maxmemory (which is a write refusal, not a policy)"))
    parser.add_argument("--out", type=Path, help="write the census here (default: stdout)")
    args = parser.parse_args(argv)

    try:
        endpoint = _endpoint(args.graph_uri)
    except ValueError as exc:
        return _usage_error(parser, str(exc))
    if endpoint.graph_name != args.graph:
        return _usage_error(
            parser,
            f"--graph {args.graph!r} disagrees with the URI's graph "
            f"{endpoint.graph_name!r} — refusing to measure one cache and name "
            f"another",
        )
    # The graph this run measures: `args.graph`, proven equal to the URI's own above.
    graph_name = args.graph
    # ⛔ VALIDATE EVERYTHING BEFORE CONNECTING. A request that will be refused must not
    # open a socket first: an unreachable host would otherwise turn a flag mistake —
    # or a destructive flag — into a network fault, and the caller would debug the
    # wrong thing (measured: `--parity-report` without `--parity-graph` exited on a
    # `ConnectionError` to a host it should never have contacted).

    reports = {}
    if args.parity_report and not args.parity_graph:
        return _usage_error(
            parser,
            "--parity-report requires --parity-graph: it is the operator's "
            "declaration of the graph the report was measured on, and the report's "
            "own `graph` field must match it (a report naming none is refused)",
        )
    if args.parity_graph is not None and args.parity_graph != args.graph:
        # ⛔ THE ASSERTION MUST NAME THE GRAPH BEING MEASURED. Comparing the report
        # against an operator-typed string that may name a DIFFERENT graph made the
        # binding vacuous: `--graph A --parity-report <clean census of B>
        # --parity-graph B` produced a PLAN for A on evidence about B. Requiring
        # equality to `--graph` (itself pinned to the URI) closes that half; the
        # report's own `graph` (emitted by `turn_parity_census.census`) closes the
        # other — `load_parity_report` refuses a report that names no graph, so a
        # FOREIGN census cannot be re-badged by repeating `--graph` here
        # (review P1 on #11004; the `--parity-graph` help carries the same rule).
        # ⚠️ What is NOT closed HERE: a census of the SAME graph measured EARLIER. Its
        # `graph` matches by construction, so the name comparison passes it — which is
        # why STALENESS is bounded one layer down, on two independent axes. First the
        # report's own `measured_at`: `assess()` demotes an undated report, and one older
        # than the pass's bound (`TORTOISE_PARITY_REPORT_MAX_AGE_S`, default 3600 s), to
        # `cannot-answer`. Second the POPULATION's own resident count: the report's count
        # is compared against `POPULATION_RESIDENT_COUNT` for its population, so a stale
        # report claiming more nodes than that population now holds is demoted even
        # within the age bound (eviction shrinks a population between two fresh-looking
        # reads). The whole-graph over-count check (`entry.nodes > resident_total`) stays
        # as the coarse, clock-free backstop that only fires on an outright impossible
        # number — it could never catch the measured shape here: 20,000 claimed against
        # 500 real turns on a 25,000-node graph, where `cache_evictor` gate 2 would have
        # capped the sweep at 20,000 (review P2 on #11097; #10976 residual comment
        # 6689906625). Stated in `--parity-graph`'s help as well, where the operator
        # reads it at the moment of choosing the flag.
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
        client, graph = _connection(endpoint)
    except Exception as exc:
        print("REFUSED — the cache could not be reached, so nothing is claimed:",
              file=sys.stderr)
        print(f"  - {type(exc).__name__}: {exc}", file=sys.stderr)
        return CACHE_EVICTION_EXIT_REFUSED

    # ⛔ BEFORE ANY STATEMENT THAT COULD MINT IT. Reading a graph that is not there
    # creates it, so the existence check must precede `assess` — not be derived from
    # the numbers it produces.
    absent = _absent_graph_refusal(client, graph_name)
    if absent is not None:
        print("REFUSED — nothing is claimed about a graph that is not there:",
              file=sys.stderr)
        print(f"  - {absent}", file=sys.stderr)
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
        try:
            args.out.write_text(text, encoding="utf-8")
        except OSError as exc:
            # The same defect class `load_parity_report` fixes for UnicodeDecodeError
            # (it escaped `main()` as a traceback): a bad path is "a flag or IO
            # mistake", so it must exit 1 for BOTH IO paths.
            return _usage_error(parser, f"--out {args.out}: {exc}")
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
        # ⛔ `CLEAN` IS THE BYTE STATE, NOT "NOTHING TO EVICT". The licence is published
        # on every verdict, and a consumer enforcing an ENTRY budget (`tools/cache_evict.py`,
        # `TORTOISE_CACHE_MAX_NODES`) can still have work to do on a byte-clean cache.
        # Printing nothing here let an operator read "nothing to evict" from step 1 and
        # then watch step 2 sweep thousands of nodes.
        for entry in census.plan.get("evictable", []):
            print(f"  LICENSED (byte-clean): {entry['population']} "
                  f"({entry['nodes']} nodes, via {entry['instrument']}) — the BYTE "
                  f"verdict is CLEAN; a NODE budget may still be exceeded",
                  file=sys.stderr)
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
