#!/usr/bin/env python3
"""#7846 / criterion 4: is the vector index carried BY THE GRAPH KEY, or retained by graph name?

The issue's open question 1: the index lives in the separately-loaded ``vectorset`` module, so
it "cannot be carried by the graph-key DUMP" — and if it is dropped, vector search on a restored
shard is silently empty.

A same-name restore cannot tell those apart: a name-keyed module registry would also answer the
probe. So this restores the payload under a DIFFERENT key name. If the index still answers, it
travelled inside the payload; if it does not, the same-name result was registry retention.

Usage: payload_carriage.py <port> [<nodes>]
"""
from __future__ import annotations

import json
import sys
import time

import redis
from falkordb import FalkorDB

PORT = int(sys.argv[1])
N = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
DIM = 384
SOURCE, TARGET = "payload_a", "payload_b"
KNN = ("CALL db.idx.vector.queryNodes('Point','embedding',3,vecf32(["
       + ",".join(["0.5"] * DIM) + "]))")


def raw():
    return redis.Redis(host="127.0.0.1", port=PORT, decode_responses=False,
                       socket_timeout=300, socket_connect_timeout=10)


def status(client, graph):
    try:
        rows = client.execute_command("GRAPH.QUERY", graph, "CALL db.indexes()")[1]
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"
    if not rows:
        return "NO_INDEX"
    s = rows[0][7]
    return s.decode() if isinstance(s, bytes) else s


def knn(client, graph):
    try:
        return len(client.execute_command("GRAPH.QUERY", graph, KNN)[1])
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


def main() -> int:
    c = raw()
    out: dict = {"port": PORT, "nodes": N}
    for graph in (SOURCE, TARGET):
        c.execute_command("DEL", graph)
        c.execute_command("DEL", f"telemetry{{{graph}}}")

    writer = FalkorDB(host="127.0.0.1", port=PORT, socket_timeout=300)
    g = writer.select_graph(SOURCE)
    for start in range(0, N, 500):
        g.query(
            "UNWIND $rows AS r CREATE (p:Point {id: r.id}) "
            "SET p.embedding = vecf32(r.emb)",
            {"rows": [{"id": i,
                       "emb": [float((i * 7919 + j * 104729) % 1000) / 1000.0
                               for j in range(DIM)]}
                      for i in range(start, min(start + 500, N))]},
        )
    g.query("CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) "
            f"OPTIONS {{dimension: {DIM}, similarityFunction: 'cosine'}}")

    deadline = time.monotonic() + 180
    while time.monotonic() < deadline and status(c, SOURCE) != "OPERATIONAL":
        time.sleep(0.25)

    out["source"] = {"status": status(c, SOURCE), "knn": knn(c, SOURCE)}
    payload = c.execute_command("DUMP", SOURCE)
    out["dump_mb"] = round(len(payload) / 1e6, 2)
    c.execute_command("DEL", SOURCE)
    out["keys_after_del"] = sorted(
        k.decode() if isinstance(k, bytes) else k for k in c.execute_command("KEYS", "*")
    )
    c.execute_command("RESTORE", TARGET, 0, payload, "REPLACE")
    out["target"] = {
        "status": status(c, TARGET),
        "knn": knn(c, TARGET),
        "nodes": c.execute_command("GRAPH.QUERY", TARGET,
                                   "MATCH (n) RETURN count(n)")[1][0][0],
    }
    out["keys_after_restore"] = sorted(
        k.decode() if isinstance(k, bytes) else k for k in c.execute_command("KEYS", "*")
    )
    out["VERDICT"] = (
        "CARRIED-BY-THE-GRAPH-KEY (the index answered under a DIFFERENT graph name)"
        if out["target"]["knn"] == out["source"]["knn"] and out["target"]["knn"] != 0
        else "NOT-CARRIED (the same-name result was registry retention)"
    )
    for graph in (SOURCE, TARGET):
        c.execute_command("DEL", graph)
        c.execute_command("DEL", f"telemetry{{{graph}}}")
    print(json.dumps(out, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
