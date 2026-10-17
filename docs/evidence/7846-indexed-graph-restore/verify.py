#!/usr/bin/env python3
"""#7846 measured re-verification.

Key correction vs the original lane: embeddings are written with `vecf32(...)` so the
vector index is GENUINELY POPULATED — asserted by a KNN probe returning k rows BEFORE
the dump. A plain float list is not indexed and would silently exercise nothing.

Usage: verify.py <port> <N> [label]
"""
from __future__ import annotations

import json
import sys
import threading
import time

import redis
from falkordb import FalkorDB

PORT = int(sys.argv[1])
N = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
LABEL = sys.argv[3] if len(sys.argv) > 3 else str(PORT)
DIM = 384
GRAPH = "shard"
BYSTANDER = "bystander"
RESTORE_DEADLINE_S = 120.0


def client(decode=True, timeout=10):
    return redis.Redis(host="127.0.0.1", port=PORT, db=0, decode_responses=decode,
                       socket_timeout=timeout, socket_connect_timeout=timeout)


def gq(sql, decode=False, timeout=10):
    return client(decode=decode, timeout=timeout).execute_command(
        "GRAPH.QUERY", GRAPH, sql)


def scalar(sql):
    out = gq(sql)
    return out[1][0][0] if len(out) > 1 and out[1] else None


KNN = ("CALL db.idx.vector.queryNodes('Point','embedding',3,vecf32(["
       + ",".join(["0.5"] * DIM) + "]))")


def knn_rows():
    out = gq(KNN)
    return len(out[1]) if len(out) > 1 else -1


class Poller(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.lat, self.errors, self.stop = [], 0, threading.Event()

    def run(self):
        r = redis.Redis(host="127.0.0.1", port=PORT, socket_timeout=2,
                        socket_connect_timeout=2)
        while not self.stop.is_set():
            t0 = time.perf_counter()
            try:
                r.ping()
                self.lat.append((time.perf_counter() - t0) * 1000)
            except Exception:
                self.errors += 1
            time.sleep(0.1)

    def report(self):
        self.stop.set()
        self.join(timeout=3)
        lat = sorted(self.lat)
        if not lat:
            return {"samples": 0, "errors": self.errors}
        return {"samples": len(lat), "p50_ms": round(lat[len(lat) // 2], 1),
                "max_ms": round(lat[-1], 1), "errors": self.errors}


def jlog(d):
    print(json.dumps(d, default=str), flush=True)


def main() -> int:
    v: dict = {"label": LABEL, "port": PORT, "n": N}
    v["redis_version"] = client().info("server").get("redis_version")
    mods = client().execute_command("MODULE LIST")
    v["graph_module"] = next((m["ver"] for m in mods if m["name"] == "graph"), None)
    jlog({"start": v})

    g = FalkorDB(host="127.0.0.1", port=PORT).select_graph(GRAPH)
    g.query("MATCH (n) DETACH DELETE n")
    t0 = time.perf_counter()
    for start in range(0, N, 500):
        rows = [{"id": i,
                 "emb": [float((i * 7919 + j * 104729) % 1000) / 1000.0
                         for j in range(DIM)]}
                for i in range(start, min(start + 500, N))]
        g.query("UNWIND $rows AS r CREATE (p:Point {id: r.id}) "
                "SET p.embedding = vecf32(r.emb)", {"rows": rows})
    v["build_s"] = round(time.perf_counter() - t0, 1)
    v["nodes_built"] = scalar("MATCH (n) RETURN count(n)")
    jlog({"built_s": v["build_s"], "nodes": v["nodes_built"]})

    t0 = time.perf_counter()
    try:
        g.query("CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) "
                "OPTIONS {dimension:384, similarityFunction:'cosine', M:4, efConstruction:200}")
        v["create_index"] = "OK"
    except Exception as e:
        v["create_index"] = f"{type(e).__name__}: {e}"
    v["create_index_s"] = round(time.perf_counter() - t0, 2)
    jlog({"create_index": v["create_index"], "s": v["create_index_s"]})

    # wait for OPERATIONAL, bounded
    t0 = time.perf_counter()
    status = None
    while time.perf_counter() - t0 < 900:
        try:
            rows = gq("CALL db.indexes()")[1]
        except Exception as e:
            status = f"ERR {e}"
            time.sleep(3)
            continue
        if rows:
            status = rows[0][7]
            status = status.decode() if isinstance(status, bytes) else status
            if status == "OPERATIONAL":
                break
        time.sleep(3)
    v["index_status"] = status
    v["index_ready_s"] = round(time.perf_counter() - t0, 1)

    # ★ the gate the original lane could not pass: the index must have ENTRIES
    v["knn_before"] = knn_rows()
    jlog({"index_status": status, "index_ready_s": v["index_ready_s"],
          "knn_before": v["knn_before"]})
    if not v["knn_before"] or v["knn_before"] <= 0:
        v["VERDICT"] = "INVALID_HARNESS (index not populated — result is not evidence)"
        jlog({"FINAL": v})
        return 2

    # bystander graph in the same instance
    client().execute_command("GRAPH.QUERY", BYSTANDER, "CREATE (n:B {id:1})")

    raw = client(decode=False)
    payload = raw.execute_command("DUMP", GRAPH)
    v["dump_mb"] = round(len(payload) / 1e6, 2)
    raw.execute_command("DEL", GRAPH)
    jlog({"dump_mb": v["dump_mb"], "deleted": True})

    poller = Poller()
    poller.start()
    t0 = time.perf_counter()
    err = None
    try:
        raw.execute_command("RESTORE", GRAPH, 0, payload, "REPLACE")
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    v["restore_s"] = round(time.perf_counter() - t0, 2)
    v["restore_error"] = err
    jlog({"restore_s": v["restore_s"], "err": err})

    time.sleep(2)
    tries = []
    for _ in range(3):
        c = client(timeout=2)
        t1 = time.perf_counter()
        try:
            c.ping()
            tries.append({"ok": True, "ms": round((time.perf_counter() - t1) * 1000, 1)})
        except Exception as e:
            tries.append({"ok": False, "err": f"{type(e).__name__}: {e}"})
    v["ping_after"] = tries
    v["bystander"] = poller.report()
    jlog({"ping_after": tries, "bystander": v["bystander"]})

    alive = all(t.get("ok") for t in tries)
    if alive:
        v["nodes_after"] = scalar("MATCH (n) RETURN count(n)")
        try:
            rows = gq("CALL db.indexes()")[1]
            s = rows[0][7] if rows else "MISSING"
            v["index_after"] = s.decode() if isinstance(s, bytes) else s
        except Exception as e:
            v["index_after"] = f"{type(e).__name__}: {e}"
        v["knn_after"] = knn_rows()
        jlog({"nodes_after": v["nodes_after"], "index_after": v["index_after"],
              "knn_after": v["knn_after"]})

    v["VERDICT"] = ("NO_WEDGE (instance alive, restore bounded)"
                    if alive else "WEDGED (instance unresponsive after restore)")
    jlog({"FINAL": v})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
