#!/usr/bin/env python3
"""#7846 falsification attempt: DUMP captured while the index build is IN FLIGHT."""
from __future__ import annotations

import json
import sys
import time

import redis
from falkordb import FalkorDB

PORT = int(sys.argv[1])
N = int(sys.argv[2]) if len(sys.argv) > 2 else 90000
GRAPH = "shard"


def client(decode=True, timeout=300):
    return redis.Redis(host="127.0.0.1", port=PORT, db=0, decode_responses=decode,
                       socket_timeout=timeout, socket_connect_timeout=10)


def gq(sql):
    return client(decode=False).execute_command("GRAPH.QUERY", GRAPH, sql)


def idx_status():
    try:
        rows = gq("CALL db.indexes()")[1]
    except Exception as e:
        return f"ERR {type(e).__name__}: {e}"
    if not rows:
        return "NO_INDEX"
    s = rows[0][7]
    return s.decode() if isinstance(s, bytes) else s


def main() -> int:
    out: dict = {"port": PORT, "n": N}
    g = FalkorDB(host="127.0.0.1", port=PORT).select_graph(GRAPH)
    g.query("MATCH (n) DETACH DELETE n")
    t0 = time.perf_counter()
    for start in range(0, N, 500):
        rows = [{"id": i,
                 "emb": [float((i * 7919 + j * 104729) % 1000) / 1000.0
                         for j in range(384)]}
                for i in range(start, min(start + 500, N))]
        g.query("UNWIND $rows AS r CREATE (p:Point {id: r.id}) "
                "SET p.embedding = vecf32(r.emb)", {"rows": rows})
    out["build_s"] = round(time.perf_counter() - t0, 1)

    # CREATE and dump IMMEDIATELY — index build is still in flight.
    try:
        g.query("CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) "
                "OPTIONS {dimension:384, similarityFunction:'cosine', M:4, efConstruction:200}")
    except Exception as e:
        out["create_index"] = f"{type(e).__name__}: {e}"
    out["status_at_dump"] = idx_status()
    r = client(decode=False)
    payload = r.execute_command("DUMP", GRAPH)
    out["dump_mb"] = round(len(payload) / 1e6, 2)
    out["status_after_dump"] = idx_status()
    r.execute_command("DEL", GRAPH)

    t0 = time.perf_counter()
    err = None
    try:
        r.execute_command("RESTORE", GRAPH, 0, payload, "REPLACE")
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    out["restore_s"] = round(time.perf_counter() - t0, 2)
    out["restore_error"] = err

    time.sleep(1)
    try:
        client(timeout=3).ping()
        out["ping_after"] = True
    except Exception as e:
        out["ping_after"] = f"{type(e).__name__}: {e}"
    if out["ping_after"] is True:
        out["nodes_after"] = gq("MATCH (n) RETURN count(n)")[1][0][0]
        out["status_after_restore"] = idx_status()
        try:
            rows = gq("CALL db.idx.vector.queryNodes('Point','embedding',3,"
                      "vecf32([" + ",".join(["0.5"] * 384) + "]))")
            out["knn_after"] = len(rows[1]) if len(rows) > 1 else -1
        except Exception as e:
            out["knn_after"] = f"{type(e).__name__}: {e}"
    out["VERDICT"] = "NO_WEDGE" if out["ping_after"] is True else "WEDGED"
    print(json.dumps(out, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
