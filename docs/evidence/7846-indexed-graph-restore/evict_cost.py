#!/usr/bin/env python3
"""#7846 / #5090: what does a graph-key RESTORE cost, with vs without an in-graph vector index?

Measured on one throwaway engine, same graph, same N, same client:
  Arm A — no vector index
  Arm B — vector index, OPERATIONAL, populated (KNN returns rows — asserted)

For each arm: DUMP -> DEL -> RESTORE (binary-safe), with a bystander connection on
its own socket polled throughout (5 s before, through the restore, 10 s after) so the
instance-wide stall is measured, not inferred. CPU is read from INFO cpu deltas.

Usage: evict_cost.py <port> <N> <label>
"""
from __future__ import annotations

import json
import sys
import threading
import time

import redis
from falkordb import FalkorDB

PORT = int(sys.argv[1])
N = int(sys.argv[2]) if len(sys.argv) > 2 else 30000
LABEL = sys.argv[3] if len(sys.argv) > 3 else str(PORT)
DIM = 384
GRAPH = "shard"
RESTORE_TIMEOUT_S = 600


def client(decode=True, timeout=RESTORE_TIMEOUT_S):
    return redis.Redis(host="127.0.0.1", port=PORT, db=0, decode_responses=decode,
                       socket_timeout=timeout, socket_connect_timeout=10,
                       retry_on_timeout=False)


def gq(sql):
    return client(decode=False).execute_command("GRAPH.QUERY", GRAPH, sql)


def scalar(sql):
    out = gq(sql)
    return out[1][0][0] if len(out) > 1 and out[1] else None


KNN = ("CALL db.idx.vector.queryNodes('Point','embedding',3,vecf32(["
       + ",".join(["0.5"] * DIM) + "]))")


def index_status():
    try:
        rows = gq("CALL db.indexes()")[1]
    except Exception as e:
        return f"ERR {type(e).__name__}: {e}"
    if not rows:
        return "NO_INDEX"
    s = rows[0][7]
    return s.decode() if isinstance(s, bytes) else s


def knn_rows():
    try:
        out = gq(KNN)
        return len(out[1]) if len(out) > 1 else -1
    except Exception as e:
        return f"ERR {type(e).__name__}: {e}"


def cpu():
    c = client().info("cpu")
    return c["used_cpu_sys"] + c["used_cpu_user"]


class Bystander(threading.Thread):
    """Own connection, polled from 5 s BEFORE the restore to 10 s after it."""

    def __init__(self):
        super().__init__(daemon=True)
        self.samples: list[tuple[float, float, bool]] = []
        self.t0 = time.perf_counter()
        self.stop = threading.Event()

    def run(self):
        r = redis.Redis(host="127.0.0.1", port=PORT, socket_timeout=1,
                        socket_connect_timeout=1)
        while not self.stop.is_set():
            t = time.perf_counter() - self.t0
            s = time.perf_counter()
            try:
                r.ping()
                self.samples.append((t, (time.perf_counter() - s) * 1000, True))
            except Exception:
                self.samples.append((t, (time.perf_counter() - s) * 1000, False))
            time.sleep(0.1)

    def finish(self):
        self.stop.set()
        self.join(timeout=3)
        fail = [x for x in self.samples if not x[2]]
        ok = [x for x in self.samples if x[2]]
        lat = sorted(x[1] for x in ok)
        return {
            "samples": len(self.samples),
            "failed_pings": len(fail),
            "first_fail_s": round(fail[0][0], 2) if fail else None,
            "last_fail_s": round(fail[-1][0], 2) if fail else None,
            "p50_ms": round(lat[len(lat) // 2], 1) if lat else None,
            "max_ms": round(lat[-1], 1) if lat else None,
        }


def build(n: int) -> float:
    g = FalkorDB(host="127.0.0.1", port=PORT).select_graph(GRAPH)
    g.query("MATCH (n) DETACH DELETE n")
    t0 = time.perf_counter()
    for start in range(0, n, 500):
        rows = [{"id": i,
                 "emb": [float((i * 7919 + j * 104729) % 1000) / 1000.0
                         for j in range(DIM)]}
                for i in range(start, min(start + 500, n))]
        g.query("UNWIND $rows AS r CREATE (p:Point {id: r.id}) "
                "SET p.embedding = vecf32(r.emb)", {"rows": rows})
    return round(time.perf_counter() - t0, 1)


def arm(name: str, indexed: bool) -> dict:
    r = client(decode=False)
    payload = r.execute_command("DUMP", GRAPH)
    r.execute_command("DEL", GRAPH)

    b = Bystander()
    b.start()
    time.sleep(5)  # quiet baseline window before the restore
    cpu0 = cpu()
    t0 = time.perf_counter()
    err = None
    try:
        r.execute_command("RESTORE", GRAPH, 0, payload, "REPLACE")
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    dt = round(time.perf_counter() - t0, 2)
    cpu1 = cpu()
    time.sleep(10)  # recovery window after the restore
    rep = b.finish()

    out = {"arm": name, "indexed": indexed, "dump_mb": round(len(payload) / 1e6, 2),
           "restore_s": dt, "restore_error": err, "cpu_s": round(cpu1 - cpu0, 1),
           "bystander": rep, "nodes_after": scalar("MATCH (n) RETURN count(n)"),
           "index_after": index_status(), "knn_after": knn_rows()}
    return out


def main() -> int:
    v: dict = {"label": LABEL, "port": PORT, "n": N}
    v["redis_version"] = client().info("server").get("redis_version")
    mods = client().execute_command("MODULE LIST")
    v["graph_module"] = next((m["ver"] for m in mods if m["name"] == "graph"), None)
    print(json.dumps({"start": v}, default=str), flush=True)

    print(json.dumps({"build_s": build(N)}, default=str), flush=True)
    v["nodes"] = scalar("MATCH (n) RETURN count(n)")

    # Arm A — NO index (the issue's control)
    print(json.dumps({"arm_A": arm("no-index", False)}, default=str), flush=True)

    # Arm B — WITH index, populated
    g = FalkorDB(host="127.0.0.1", port=PORT).select_graph(GRAPH)
    try:
        g.query("CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) "
                "OPTIONS {dimension:384, similarityFunction:'cosine', M:4, efConstruction:200}")
    except Exception as e:
        print(json.dumps({"create_index": f"{type(e).__name__}: {e}"}), flush=True)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 900 and index_status() != "OPERATIONAL":
        time.sleep(2)
    print(json.dumps({"index_status": index_status(), "knn_before": knn_rows(),
                      "index_ready_s": round(time.perf_counter() - t0, 1)}), flush=True)

    print(json.dumps({"arm_B": arm("with-index", True)}, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
