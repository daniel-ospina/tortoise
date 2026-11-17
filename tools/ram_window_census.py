#!/usr/bin/env python3
"""The demand census for one tenant — the four figures #13082 Phase 1 sizes from.

`#13084` part 2. The window's size is a question about DEMAND, so it cannot be
sized from the graph's byte total alone. This tool reports, per tenant:

1. **Query mix** — traversal vs vector vs text vs lookup, counted at the read
   seams. Instrument: the read ledger's `query` events (`tortoise/read_ledger.py`,
   `record_query`), which the SDK's read doors emit.
   LIMITS: only reads that pass through the SDK's read funnel while
   `TORTOISE_READ_LEDGER` is on; a direct Cypher read is not counted. The kind is
   the DOOR's shape, not a per-leg split — a hybrid search records `vector` once.
2. **Seed hit rate** — of the traversals run, how often the seed was already
   resident. Instrument: the same ledger; a seed counts as resident when that
   `(label, key)` had an earlier read in the window.
   LIMITS: "resident" means *read earlier in this window*, which is a hotness
   proxy — the projection's own residency declaration (`tortoise/residency.py`)
   is the other half and is not joined here. A door that has no start key
   records NO seed (it never substitutes a returned row, which would answer a
   different question), so an unseeded traversal is reported as a hole, never
   as a miss: no shipped door supplies one yet (`recall_state` and `recall_gaps`
   are ranked retrieval over a pool, not seeded walks — #8877 wires the seeded
   legs), which makes the rate `n/a` beside a non-zero `unseeded` count rather
   than a number nobody should act on.
3. **Touched-node share** (`f`) — the fraction of resident nodes any traversal
   touched in the period. Instrument: `read_ledger.hot_fraction` (the same
   division `python -m tortoise.read_ledger` prints).
   LIMITS: `bytes`-based `f` needs each stock key to carry its own byte width;
   when it does not, the key fraction is the reading and the byte fraction is
   omitted rather than computed over a pretend zero.
4. **Cold-restore cost** for the graph. Instrument: `--restore-probe`, a timed
   `DUMP` → `RESTORE … REPLACE` on the named graph (indexes KEPT, so this is the
   conservative figure; dropping the vector index before the dump is what the
   measured ~0.03 s figure in #13082 does, and that path is NOT timed here).
   LIMITS: per GRAPH, not per region — FalkorDB has no per-region eviction.
   ⛔ THERE IS NO `DEL` AND THEREFORE NO INTERVAL IN WHICH THE GRAPH DOES NOT
   EXIST (#13102 F2). `RESTORE` validates the payload's RDB version and checksum
   BEFORE it creates the key, so a rejected payload leaves the live graph
   untouched — whereas `DEL`-then-`RESTORE` leaves the payload as the only copy
   if the restore fails, which is how a measurement loses a tenant's graph.
   There is no separate delete step to time, so `del_s` is reported absent
   rather than as a fabricated 0.0. The probe still REWRITES the key, so it
   refuses unless `--confirm-graph` names exactly the graph measured, and it
   refuses a graph that is not already on the instance (reading a typo CREATES
   it).

Nothing here refuses a write, deletes stored history, or bounds anything: it is
the measurement half. The cap half is `tortoise/allowance_gate.py`.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below — a module-level
# 3.11+-only import raises ImportError before a later guard could run.
#
# #4053/#11342: the refusal must not wear a VERDICT's status. This tool's answers
# are 0 (measured) and 3 (unmeasurable); 2 is its USAGE code (argparse's own),
# which an interpreter that cannot run the tool shares and which is never a
# measurement. `SystemExit(<int>)` carries no message, so it is printed
# explicitly. Pinned by `tests/test_entry_point_python_guard.py`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/ram_window_census.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/ram_window_census.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import contextlib
import json
from pathlib import Path
from time import perf_counter

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - script entry point
    sys.path.insert(0, str(REPO_ROOT))

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNMEASURABLE = 3

#: The two reference measurements #13082 records for cold restore. Quoted, never
#: silently reused as this graph's number: a figure from another corpus is
#: context, and reporting it as the tenant's own is the class of error this tool
#: exists to avoid.
RECORDED_COLD_RESTORE_S = {
    "index_dropped_s": 0.03,
    "index_kept_s": 1.585,
    "note": ("recorded in #13082 (the index-kept figure blocks the instance); "
             "context only — run --restore-probe for THIS graph"),
}


def _ledger_section(ledger: str, tenant: str, window_days: float | None) -> dict:
    from tortoise import read_ledger

    target = Path(ledger)
    if not target.exists():
        # A missing file is not a zero reading (see read_ledger._main).
        return {"error": f"no ledger at {target}"}
    folded = read_ledger.fold(target, window_days=window_days, tenant=tenant)
    f = read_ledger.hot_fraction(folded)
    return {
        "tenant": tenant,
        "window_days": window_days,
        "query_mix": read_ledger.query_mix(folded).as_dict(),
        "seed_hit_rate": read_ledger.seed_hit_rate(folded).as_dict(),
        "touched_node_share": f.as_dict(),
        "lines_read": folded.lines_read,
        "lines_skipped": folded.lines_skipped,
        "capped": folded.capped,
    }


def _endpoint(uri: str):
    from tortoise.projection import resolve_db_endpoint

    return resolve_db_endpoint(uri)


def _connection(endpoint):
    import redis
    from falkordb import FalkorDB

    from tortoise.projection import _bounded_server_socket_kwargs

    # The resolver is splatted INLINE (not hoisted) so the bounded-client gate
    # sees the read bound on the CALL, exactly as tools/cache_eviction_census.py
    # does.
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


def _restore_probe(uri: str, graph: str, confirm: str | None) -> dict:
    """Time a DUMP → RESTORE-REPLACE for ONE graph. Dry run unless confirmed.

    ⛔ NO `DEL` (#13102 F2). The rebuild is `RESTORE … REPLACE` against the LIVE
    key: Redis validates the payload's RDB version and checksum BEFORE it creates
    the key, so a payload the engine rejects leaves the graph untouched, and
    there is no window in which the only copy of a tenant's graph is a local
    variable. The previous shape (`DUMP` → `DEL` → `RESTORE`, no `except`)
    deleted first and handed the operator a traceback on failure — with the
    payload discarded with the stack frame, i.e. an unrecoverable loss on a
    measurement. `tortoise/graph_key_restore.py` documents the same hazard for
    the same sequence ("the payload is the only copy left").

    A failure here is reported as a MEASUREMENT that could not be taken (the
    section's ``error`` → ``EXIT_UNMEASURABLE``), never as a traceback, and the
    graph is never deleted.
    """
    if confirm != graph:
        return {
            "error": ("--restore-probe REBUILDS the named graph's key from its "
                      "own DUMP payload (RESTORE … REPLACE, in place); it runs "
                      "only with "
                      f"--confirm-graph {graph!r} (matching --graph)"),
            "dry_run": True,
            "recorded_reference": RECORDED_COLD_RESTORE_S,
        }
    endpoint = _endpoint(uri)
    if endpoint.graph_name and endpoint.graph_name != graph:
        return {"error": (f"the URI names graph {endpoint.graph_name!r} but "
                          f"--graph is {graph!r}")}
    client, _handle = _connection(endpoint)
    try:
        # GRAPH.LIST creates nothing; a typo'd --graph would otherwise be
        # MATERIALIZED by the first query and then measured as an empty graph.
        names = {n if isinstance(n, str) else n.decode() for n in
                 client.execute_command("GRAPH.LIST")}
        if graph not in names:
            return {"error": (f"graph {graph!r} is not on this instance "
                              f"({len(names)} graph(s) listed) — refusing to "
                              f"measure a graph reading would CREATE")}
        t0 = perf_counter()
        payload = client.dump(graph)
        t1 = perf_counter()
        # A payload that is not raw bytes is not a payload `RESTORE` can verify
        # (the client is built with `decode_responses=True`); refuse it HERE,
        # while the graph is still the live key, rather than sending it on.
        if not isinstance(payload, (bytes, bytearray)):
            return {"error": (
                f"DUMP returned {type(payload).__name__}, not bytes — a decoded "
                "payload cannot be restored (RESTORE checks a binary RDB "
                "checksum); nothing was written to the graph")}
        try:
            # REPLACE, no DEL: the checksum is verified BEFORE the key is
            # touched, so a rejected payload leaves the graph intact.
            client.restore(graph, 0, payload, replace=True)
        except Exception as exc:  # the engine's error is REPORTED, never raised
            # NOT a traceback: the reading is unmeasurable, the graph is intact,
            # and the payload is still in this scope if an operator wants it.
            return {"error": (
                f"RESTORE was refused by the engine ({type(exc).__name__}: "
                f"{exc}); the graph was NOT deleted and its key is unchanged "
                f"(the {len(payload)}-byte payload failed validation before any "
                "write)"), "graph": graph, "bytes": len(payload)}
        t2 = perf_counter()
        return {
            "graph": graph,
            "indexes": "kept",
            # No DEL step exists, so there is no delete time to report. `None`,
            # not 0.0 — an absent step is not a zero-second step (#13102 F2).
            "rebuild": "restore-replace",
            "del_s": None,
            "dump_s": round(t1 - t0, 4),
            "restore_s": round(t2 - t1, 4),
            "total_s": round(t2 - t0, 4),
            "bytes": len(payload),
        }
    finally:
        # Best-effort close: a failed close must not mask the probe's reading
        # (`SIM105` — the lint gate runs `ruff check .` over the whole tree).
        with contextlib.suppress(Exception):
            client.close()


def _cold_section(args) -> dict:
    if args.restore_probe:
        return _restore_probe(args.graph_uri, args.graph, args.confirm_graph)
    return {
        "not_measured": ("run --restore-probe --graph-uri … --graph … "
                         "--confirm-graph <same graph> to time THIS graph"),
        "recorded_reference": RECORDED_COLD_RESTORE_S,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="tools/ram_window_census.py",
        description="Per-tenant demand census (#13084): query mix, seed hit "
                    "rate, touched-node share, cold-restore cost.",
    )
    ap.add_argument("--ledger", help="read-ledger JSONL to fold")
    ap.add_argument("--tenant", help="the tenant (org) to fold — f is per tenant")
    ap.add_argument("--window-days", type=float, default=None)
    ap.add_argument("--restore-probe", action="store_true",
                    help="time a DUMP -> RESTORE ... REPLACE of --graph "
                         "(rewritten in place; NO DEL)")
    ap.add_argument("--graph-uri", default=None)
    ap.add_argument("--graph", default=None)
    ap.add_argument("--confirm-graph", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.ledger and not args.tenant:
        print("--ledger requires --tenant: f and the mix are PER TENANT, and a "
              "union of tenants answers a question nobody asked", file=sys.stderr)
        return EXIT_USAGE
    if args.restore_probe and not (args.graph_uri and args.graph):
        print("--restore-probe requires --graph-uri and --graph",
              file=sys.stderr)
        return EXIT_USAGE

    report: dict = {"census": "ram_window", "issue": 13084}
    if args.ledger:
        report.update(_ledger_section(args.ledger, args.tenant, args.window_days))
    if args.restore_probe:
        report["cold_restore"] = _cold_section(args)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        if report.get("error"):
            # ⛔ A MISSING/UNREADABLE LEDGER IS NOT A ZERO READING (#13102 F5).
            # This used to be json-only: the human render printed NOTHING and
            # the script still exited 0, so a census that measured nothing read
            # as a census that found nothing — the same rule the fold itself
            # asserts, broken one layer up.
            print(f"ledger          : {report['error']}")
        if "query_mix" in report:
            mix = report["query_mix"]
            print(f"tenant          : {report['tenant']}")
            print("query mix       : " + " · ".join(
                f"{k} {v:,}" for k, v in mix["counts"].items())
                + (f"  (+{mix['unattributed']:,} unattributed)"
                   if mix["unattributed"] else ""))
            seeds = report["seed_hit_rate"]
            rate = seeds["rate"]
            print("seed hit rate   : " + (f"{rate:.3f} ({seeds['hot']}/"
                                          f"{seeds['trials']})"
                                          if rate is not None else "n/a"))
            if seeds["unseeded"] or seeds["unstocked"]:
                # ⛔ PRINTED, not json-only: a bare `n/a` cannot be told apart
                # from "no traversals recorded yet", and on this tree `n/a` is
                # the EXPECTED reading (no shipped door records a start-key
                # seed — see the limits above). Same wording as the ledger CLI.
                print(f"⚠️ seed holes     : {seeds['unseeded']:,} traversal(s) "
                      f"with no seed key, {seeds['unstocked']:,} whose seed was "
                      f"never registered as stock — outside the rate, not "
                      f"counted as misses")
            f = report["touched_node_share"]
            print(f"touched share f : keys={f['f_keys']} bytes={f['f_bytes']} "
                  f"— {f['stock_keys']:,} stock keys, {f['read_keys']:,} read")
        cold = report.get("cold_restore")
        if cold:
            if cold.get("error"):
                print(f"cold restore    : {cold['error']}")
            else:
                del_s = cold.get("del_s")
                print(f"cold restore    : {cold['total_s']}s "
                      f"(dump {cold['dump_s']} · "
                      f"del {'n/a (RESTORE REPLACE, in place)' if del_s is None else del_s} · "
                      f"restore {cold['restore_s']}, indexes kept)")
    # EVERY section that can fail is checked, not just the cold-restore probe
    # (#13102 F5: the ledger section's error was merged into `report` and
    # inspected by nothing, so `--ledger /nope.jsonl` exited OK and printed
    # nothing). Any error in any section is UNMEASURABLE, which is exactly what
    # ``EXIT_UNMEASURABLE`` exists for.
    cold = report.get("cold_restore")
    if report.get("error") or (isinstance(cold, dict) and cold.get("error")):
        return EXIT_UNMEASURABLE
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
