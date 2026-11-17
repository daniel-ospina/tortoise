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
   is the other half and is not joined here. Traversals whose seed key could not
   be addressed are reported as holes, never as misses.
3. **Touched-node share** (`f`) — the fraction of resident nodes any traversal
   touched in the period. Instrument: `read_ledger.hot_fraction` (the same
   division `python -m tortoise.read_ledger` prints).
   LIMITS: `bytes`-based `f` needs each stock key to carry its own byte width;
   when it does not, the key fraction is the reading and the byte fraction is
   omitted rather than computed over a pretend zero.
4. **Cold-restore cost** for the graph. Instrument: `--restore-probe`, a timed
   `DUMP` → `DEL` → `RESTORE` on the named graph (indexes KEPT, so this is the
   conservative figure; dropping the vector index before the dump is what the
   measured ~0.03 s figure in #13082 does, and that path is NOT timed here).
   LIMITS: per GRAPH, not per region — FalkorDB has no per-region eviction. The
   probe is DESTRUCTIVE for the interval between DEL and RESTORE, so it refuses
   unless `--confirm-graph` names exactly the graph measured, and it refuses a
   graph that is not already on the instance (reading a typo CREATES it).

Nothing here refuses a write, deletes stored history, or bounds anything: it is
the measurement half. The cap half is `tortoise/allowance_gate.py`.
"""
from __future__ import annotations

import argparse
import json
import sys
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
    """Time a DUMP → DEL → RESTORE for ONE graph. Dry run unless confirmed."""
    if confirm != graph:
        return {
            "error": ("--restore-probe is DESTRUCTIVE for the interval between "
                      "DEL and RESTORE; it runs only with "
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
        client.delete(graph)
        t2 = perf_counter()
        client.restore(graph, 0, payload)
        t3 = perf_counter()
        return {
            "graph": graph,
            "indexes": "kept",
            "dump_s": round(t1 - t0, 4),
            "del_s": round(t2 - t1, 4),
            "restore_s": round(t3 - t2, 4),
            "total_s": round(t3 - t0, 4),
            "bytes": len(payload) if isinstance(payload, (bytes, bytearray)) else None,
        }
    finally:
        try:
            client.close()
        except Exception:  # pragma: no cover - best effort close
            pass


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
                    help="time a DUMP/DEL/RESTORE for --graph (destructive)")
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
            f = report["touched_node_share"]
            print(f"touched share f : keys={f['f_keys']} bytes={f['f_bytes']} "
                  f"— {f['stock_keys']:,} stock keys, {f['read_keys']:,} read")
        cold = report.get("cold_restore")
        if cold:
            if cold.get("error"):
                print(f"cold restore    : {cold['error']}")
            else:
                print(f"cold restore    : {cold['total_s']}s "
                      f"(dump {cold['dump_s']} · del {cold['del_s']} · "
                      f"restore {cold['restore_s']}, indexes kept)")
    if any(("error" in s) for s in
           [report.get("cold_restore")] if isinstance(report.get("cold_restore"), dict)):
        return EXIT_UNMEASURABLE
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
