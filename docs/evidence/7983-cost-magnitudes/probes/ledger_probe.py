#!/usr/bin/env python3
"""#7983 — run the READ LEDGER on the live self-hosted graph.

This is the instrument's end-to-end exercise, and the ONLY thing it may be read
as is **what the instrument records**. It is not a tenant estimate:

  * the STOCK is real — every `(label, key)` the live graph holds **for the labels the
record's identity contract keys** (`Point`, `Event`, `Subject`, `Object`, `Source`,
`Session`). ⚠️ SCOPE: `GraphEvent` and `Tag` are NOT stock — they carry no
contract key and the record view does not read them per key — so this denominator
is the KEYED record, not every node in the store. The omission is conservative for
`f` (a never-read `GraphEvent` would only LOWER it), and `stock_truncated: false`
speaks about the paging walk of these six labels, not about the whole store.;
  * the READ MIX is a **proxy**: queries are drawn from the graph's own content
    (one query per sampled Point, built from its own words), so it stands in for
    *a* query mix, not for a tenant's. §17.4.6 records it as a proxy, with the
    sample and the date, and does NOT put it in the `f` row of the cost model.

Read-only by construction, and it PROVES that: the node count is captured before and after and
asserted equal, with `GRAPH.MEMORY USAGE` captured and reported beside it as a second witness.

    uv run python docs/evidence/7983-cost-magnitudes/probes/ledger_probe.py \
        --uri 'docker://:@localhost:16379/tortoise' --graph tortoise --queries 200
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

#: Bytes per RESIDENT node — §17.4.2's measured figure (143 MiB ÷ 89,701).
#: FalkorDB exposes no per-node byte figure, so the stock's bytes are attributed
#: at this measured unit; the key counts are exact and the report says so.
BYTES_PER_RESIDENT_NODE = 1672

PAGE = 5000  # FalkorDB silently caps a result at 10,000 rows: page below it.


def enumerate_keys(graph, label: str, key_prop: str, limit: int) -> list[str]:
    """Every key of one label, paged under FalkorDB's silent 10,000-row cap."""
    out: list[str] = []
    skip = 0
    while len(out) < limit:
        # `DISTINCT` + `ORDER BY` matter: the stock is a DENOMINATOR, and a
        # `SKIP`/`LIMIT` walk without a TOTAL ORDER is not a partition of the
        # rows — the #5346 defect `paginated_query` documents. `DISTINCT` prevents
        # repeats but not the DROPS, and a dropped key shrinks the denominator
        # invisibly, inflating `f`: the one number this probe exists to make
        # honest. (Measured, not assumed: the un-ordered walk was observed to
        # skip keys the ordered walk returns — the denominator shrinks, which
        # INFLATES f. That is the failure this line prevents, not a read-count
        # effect.)
        rows = graph.query(
            f"MATCH (n:`{label}`) WHERE n.`{key_prop}` IS NOT NULL "
            f"RETURN DISTINCT n.`{key_prop}` ORDER BY n.`{key_prop}` "
            f"SKIP {skip} LIMIT {PAGE}"
        ).result_set
        if not rows:
            break
        out.extend(str(r[0]) for r in rows)
        if len(rows) < PAGE:
            break
        skip += PAGE
    return out[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default="docker://:@localhost:16379/tortoise")
    ap.add_argument("--graph", default="tortoise")
    ap.add_argument("--queries", type=int, default=200)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--stock-cap", type=int, default=20000)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--sample",
        choices=("random", "storage-order"),
        default="random",
        help="random = shuffle inside the pool (the reading quoted in the docs); "
        "storage-order = the pre-fix draw (`LIMIT` only), kept ONLY so the sampling "
        "effect is reproducible instead of asserted",
    )
    ap.add_argument(
        "--keep-ledger",
        action="store_true",
        help="keep the raw ledger file (it is ~90 lines per query: "
        "reproducible, not worth committing)",
    )
    a = ap.parse_args()

    os.environ["TORTOISE_DB_URI"] = a.uri
    here = Path(__file__).resolve().parents[1]
    ledger = Path(a.ledger) if a.ledger else here / "read-ledger.jsonl"
    if ledger.exists():
        ledger.unlink()
    os.environ["TORTOISE_READ_LEDGER"] = "1"
    os.environ["TORTOISE_READ_LEDGER_PATH"] = str(ledger)

    from tortoise.read_ledger import KEY_FIELDS, fold, hot_fraction, record_stock
    from tortoise.sdk import TortoiseSDK

    # No explicit path: with TORTOISE_DB_URI set the SDK targets the server,
    # and `graph_name` pins the EXISTING graph exactly (no new graph is
    # minted, no namespace prefix is added).
    sdk = TortoiseSDK(graph_name=a.graph)
    try:
        proj = sdk._get_proj()
        g = proj.g

        def census() -> dict:
            nodes = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
            # GRAPH.MEMORY USAGE is a COMMAND, not Cypher: it goes through the
            # raw client, never `g.query`.
            mem = g.execute_command("GRAPH.MEMORY USAGE", a.graph)
            return {"nodes": int(nodes), "memory_usage": mem}

        before = census()

        # ── the STOCK: every key the graph holds, attributed at the measured
        #    bytes-per-resident-node unit ─────────────────────────────────────
        stock: dict[str, int] = {}
        stock_capped: list[str] = []
        for label, key_prop in KEY_FIELDS.items():
            keys = enumerate_keys(g, label, key_prop, a.stock_cap)
            stock[label] = len(keys)
            # A truncated stock is a truncated DENOMINATOR, which flatters the
            # split. The cap is a guard against a runaway graph, not a licence to
            # shrink the thing this report exists to measure — so it says so.
            if len(keys) >= a.stock_cap:
                stock_capped.append(label)
            for key in keys:
                record_stock(label, key, nbytes=BYTES_PER_RESIDENT_NODE)
        total_stock = sum(stock.values())

        # ── the READ MIX (a PROXY): one query per sampled Point ──────────────
        # A CANDIDATE POOL, then a real random sample of it. ⚠️ The pool is still
        # `LIMIT queries*10` with NO ORDER BY, i.e. storage order, so the sample
        # is uniform over the FIRST pool-worth of Points and the rest of the graph
        # can never be a query source. That is a remaining bias of the PROXY (not
        # of the instrument), and it is why this number is an exercise rather
        # than a tenant reading.
        pool = g.query(
            f"MATCH (n:Point) WHERE n.id IS NOT NULL AND n.content IS NOT NULL "
            f"RETURN n.content LIMIT {a.queries * 10}"
        ).result_set
        pool_total = g.query(
            "MATCH (n:Point) WHERE n.id IS NOT NULL AND n.content IS NOT NULL RETURN count(n)"
        ).result_set[0][0]
        if a.sample == "storage-order":
            sampled = pool[: a.queries]
        else:
            random.seed(7983)
            sampled = random.sample(pool, min(a.queries, len(pool)))
        ran = 0
        for (content,) in sampled:
            words = [w for w in str(content).split() if len(w) > 3][:4]
            if not words:
                continue
            try:
                sdk.tortoise_fts_query(" ".join(words), limit=a.limit)
                ran += 1
            except Exception as exc:
                print(f"query skipped: {type(exc).__name__}: {exc}", file=sys.stderr)

        after = census()
        assert before["nodes"] == after["nodes"], (
            f"the probe WROTE to the graph: {before['nodes']} -> {after['nodes']}"
        )
        # `GRAPH.MEMORY USAGE` is an independent second witness: a write that
        # rewrote a property without changing the node COUNT would pass the count
        # check alone. Reported rather than asserted equal, because the server's
        # own bookkeeping can move a few bytes on a read.
        print(
            f"memory usage before/after: {before['memory_usage']} / {after['memory_usage']}",
            file=sys.stderr,
        )

        folded = fold(ledger)
        f = hot_fraction(folded)
        report = {
            "graph": a.graph,
            "uri": a.uri,
            "queries_run": ran,
            "sample": a.sample,
            "limit_per_query": a.limit,
            "stock_keys_by_label": stock,
            "stock_keys": total_stock,
            "stock_capped_labels": stock_capped,
            "stock_truncated": bool(stock_capped),
            # The denominator's DENOMINATOR: how much of the graph the proxy's
            # pool could ever cover.
            "pool_rows": len(pool),
            "pool_eligible_points": int(pool_total),
            "pool_is_storage_order": True,
            "bytes_per_resident_node": BYTES_PER_RESIDENT_NODE,
            "nodes_before": before["nodes"],
            "nodes_after": after["nodes"],
            "ledger_lines": folded.lines_read,
            **f.as_dict(),
        }
        print(json.dumps(report, indent=2, default=str))
        if a.out:
            Path(a.out).write_text(json.dumps(report, indent=2, default=str) + "\n")
    finally:
        sdk.close()
        if not a.keep_ledger and ledger.exists():
            ledger.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
