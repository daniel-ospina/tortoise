#!/usr/bin/env python3
"""#7846 follow-up: isolate the SIGSEGV on RESTORE-of-an-indexed-graph.

Crash report from the CI-pinned engine (``falkordb/falkordb-server:v4.20.6``, graph 42006):

    Redis 8.6.3 crashed by signal: 11, si_code: 1
    /var/lib/falkordb/bin/falkordb.so(Index_SetLanguage+0x14)
    CURRENT CLIENT ... cmd=restore
    argv[1]: "t_7846_3f3a5c5e7fce4c3a_copy"

i.e. the RESTORE of an INDEXED graph's payload under a NEW key, while the
ORIGINAL key still existed. This script isolates the trigger:

  variant   source key at RESTORE time   index present
  -------   --------------------------   -------------
  kept      present                      yes
  deleted   deleted (DEL first)          yes
  noindex   present                      no

Usage: crash_isolate.py <port> <variant>
"""
from __future__ import annotations

import json
import sys
import time

import redis
from falkordb import FalkorDB

PORT = int(sys.argv[1])
VARIANT = sys.argv[2]
DIM = 384
N = 3000
A, B = "iso_source", "iso_target"


def raw():
    return redis.Redis(host="127.0.0.1", port=PORT, decode_responses=False,
                       socket_timeout=60, socket_connect_timeout=10)


def main() -> int:
    out: dict = {"variant": VARIANT, "port": PORT}
    c = raw()
    for g in (A, B):
        c.execute_command("DEL", g)
        c.execute_command("DEL", f"telemetry{{{g}}}")

    w = FalkorDB(host="127.0.0.1", port=PORT, socket_timeout=60).select_graph(A)
    for start in range(0, N, 500):
        w.query(
            "UNWIND $rows AS r CREATE (p:Point {id: r.id}) "
            "SET p.embedding = vecf32(r.emb)",
            {"rows": [{"id": i,
                       "emb": [float((i * 7919 + j * 104729) % 1000) / 1000.0
                               for j in range(DIM)]}
                      for i in range(start, start + 500)]},
        )

    if VARIANT != "noindex":
        w.query("CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) "
                f"OPTIONS {{dimension: {DIM}, similarityFunction: 'cosine'}}")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            rows = c.execute_command("GRAPH.QUERY", A, "CALL db.indexes()")[1]
            st = rows[0][7] if rows else None
            st = st.decode() if isinstance(st, bytes) else st
            if st == "OPERATIONAL":
                break
            time.sleep(0.25)
        out["index_status"] = st

    payload = c.execute_command("DUMP", A)
    out["dump_mb"] = round(len(payload) / 1e6, 2)

    if VARIANT == "deleted":
        c.execute_command("DEL", A)
    out["source_present_at_restore"] = c.exists(A) == 1

    err = None
    try:
        c.execute_command("RESTORE", B, 0, payload, "REPLACE")
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
    out["restore_error"] = err

    time.sleep(1)
    try:
        alive = bool(redis.Redis(host="127.0.0.1", port=PORT, socket_timeout=3,
                                socket_connect_timeout=3).ping())
    except Exception as exc:
        alive = f"{type(exc).__name__}: {exc}"
    out["instance_alive_after"] = alive
    out["VERDICT"] = "SURVIVED" if alive is True else "INSTANCE DEAD"
    print(json.dumps(out, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
