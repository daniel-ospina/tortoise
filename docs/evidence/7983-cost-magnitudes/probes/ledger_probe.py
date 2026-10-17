#!/usr/bin/env python3
"""#7983 — run the READ LEDGER on the live self-hosted graph.

This is the instrument's end-to-end exercise, and the ONLY thing it may be read
as is **what the instrument records**. It is not a tenant estimate:

  * the STOCK is real — every `(label, key)` the live graph holds;
  * the READ MIX is a **proxy**: queries are drawn from the graph's own content
    (one query per sampled Point, built from its own words), so it stands in for
    *a* query mix, not for a tenant's. §17.4.6 records it as a proxy, with the
    sample and the date, and does NOT put it in the `f` row of the cost model.

Read-only by construction, and it PROVES that: the node count and
`GRAPH.MEMORY USAGE` are captured before and after and asserted equal.

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
        rows = graph.query(
            f"MATCH (n:`{label}`) WHERE n.`{key_prop}` IS NOT NULL "
            f"RETURN n.`{key_prop}` SKIP {skip} LIMIT {PAGE}").result_set
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
    ap.add_argument("--keep-ledger", action="store_true",
                    help="keep the raw ledger file (it is ~90 lines per query: "
                         "reproducible, not worth committing)")
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
        for label, key_prop in KEY_FIELDS.items():
            keys = enumerate_keys(g, label, key_prop, a.stock_cap)
            stock[label] = len(keys)
            for key in keys:
                record_stock(label, key, nbytes=BYTES_PER_RESIDENT_NODE)
        total_stock = sum(stock.values())

        # ── the READ MIX (a PROXY): one query per sampled Point ──────────────
        # A CANDIDATE POOL, then a real random sample of it: `LIMIT n` before a
        # shuffle is a no-op — it takes the first n rows in storage order, so the
        # "sample" would be whatever the graph happened to return first.
        pool = g.query(
            f"MATCH (n:Point) WHERE n.id IS NOT NULL AND n.content IS NOT NULL "
            f"RETURN n.content LIMIT {a.queries * 10}").result_set
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
            f"the probe WROTE to the graph: {before['nodes']} -> {after['nodes']}")

        folded = fold(ledger)
        f = hot_fraction(folded)
        report = {
            "graph": a.graph,
            "uri": a.uri,
            "queries_run": ran,
            "limit_per_query": a.limit,
            "stock_keys_by_label": stock,
            "stock_keys": total_stock,
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
