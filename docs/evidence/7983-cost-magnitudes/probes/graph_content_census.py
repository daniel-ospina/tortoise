#!/usr/bin/env python3
"""#7983 — the DOGFOOD GRAPH's shape, measured: what the record bench planted.

The `rho` bench (`record_read_cost.py`) plants a tenant at §17.4.2's stock, and
its ROW WIDTH depends on two facts about our real data that must not be guessed:

  * the text width of a `Point` (so the scored row is not an empty string), and
  * the share of Points that carry a 384-d vector (so the row width is a blend,
    not the cheap case).

This probe measures both on the live self-hosted graph, read-only, and prints
them with their bases. It writes nothing.

    uv run python docs/evidence/7983-cost-magnitudes/probes/graph_content_census.py
"""
from __future__ import annotations

import json
import os
import sys

GRAPH = os.environ.get("TORTOISE_PROBE_GRAPH", "tortoise")
URI = os.environ.get("TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise")


def main() -> int:
    os.environ["TORTOISE_DB_URI"] = URI
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(graph_name=GRAPH)
    try:
        g = sdk._get_proj().g

        def one(cypher: str):
            return g.query(cypher).result_set[0]

        nodes = one("MATCH (n) RETURN count(n)")[0]
        points, total_bytes, avg_bytes, max_bytes = one(
            "MATCH (n:Point) RETURN count(n), sum(size(n.content)), "
            "avg(size(n.content)), max(size(n.content))")
        with_vec = one("MATCH (n:Point) WHERE n.embedding IS NOT NULL "
                       "RETURN count(n)")[0]
        by_label = {}
        for label in ("Point", "Event", "Subject", "Object", "Source",
                      "Session", "GraphEvent"):
            by_label[label] = int(one(f"MATCH (n:`{label}`) RETURN count(n)")[0])
        mem = g.execute_command("GRAPH.MEMORY USAGE", GRAPH)

        out = {
            "graph": GRAPH,
            "uri": URI,
            "nodes_total": int(nodes),
            "nodes_by_label": by_label,
            "points": int(points),
            "point_content_bytes_total": int(total_bytes) if total_bytes else 0,
            "point_content_bytes_avg": round(float(avg_bytes), 1) if avg_bytes else 0,
            "point_content_bytes_max": int(max_bytes) if max_bytes else 0,
            "points_with_embedding": int(with_vec),
            "vector_share_of_points": round(int(with_vec) / int(points), 4),
            "graph_memory_usage": mem,
        }
        print(json.dumps(out, indent=2, default=str))
    finally:
        sdk.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
