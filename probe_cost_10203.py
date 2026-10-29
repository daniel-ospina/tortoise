"""Cost probe for P3-1's fix (#10203): what does ONE extra read per Point write
cost on the shared live writer?

Measures, on a private lane, for N=200 fresh points:
  A) the ADD door as it is at this head (MERGE + extras, no read)
  B) the same ADD door plus the retraction read+assignment the fix needs
"""
from __future__ import annotations

import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tortoise.projection import FalkorProjection
from tortoise.projection.entities import revise_identifier_aliases

URI = os.environ["TORTOISE_DB_URI"]
N = 200


def main() -> None:
    proj = FalkorProjection.from_uri(
        URI, graph_name=f"test_identperf_{uuid.uuid4().hex[:8]}")
    try:
        # warm the engine
        proj.apply({"type": "PointAdded", "point": {
            "id": "warm", "content": "error ERR-4032 on deploy"}})

        events = [
            {"type": "PointAdded", "point": {
                "id": f"a{i}", "content": f"error ERR-4033 on deploy {i}"}}
            for i in range(N)
        ]
        t0 = time.perf_counter()
        for ev in events:
            proj.apply(ev)
        base = time.perf_counter() - t0

        costs = []
        t0 = time.perf_counter()
        for i in range(N):
            rows = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.content, n.search_keys",
                params={"id": f"a{i}"},
            ).result_set
            old_content, old_keys = rows[0] if rows else (None, None)
            sk = revise_identifier_aliases(
                new_content="error INV-2024-0091 on deploy",
                old_content=old_content, old_search_keys=old_keys)
            costs.append(sk)
        read_only = time.perf_counter() - t0

        t0 = time.perf_counter()
        for i in range(N):
            rows = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.content, n.search_keys",
                params={"id": f"a{i}"},
            ).result_set
            old_content, old_keys = rows[0] if rows else (None, None)
            sk = revise_identifier_aliases(
                new_content="error INV-2024-0091 on deploy",
                old_content=old_content, old_search_keys=old_keys)
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n.search_keys = $sk",
                params={"id": f"a{i}", "sk": sk},
            )
        read_write = time.perf_counter() - t0

        print(f"N={N}")
        print(f"A  apply as-is            : {base*1000:8.1f} ms  "
              f"({base/N*1000:5.2f} ms/point)")
        print(f"B1 retraction READ (+py)  : {read_only*1000:8.1f} ms  "
              f"({read_only/N*1000:5.2f} ms/point)")
        print(f"B2 READ + the SET clause  : {read_write*1000:8.1f} ms  "
              f"({read_write/N*1000:5.2f} ms/point)")
        print(f"overhead vs A (B1)        : {read_only/base*100:5.1f} %")
    finally:
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj.close()


if __name__ == "__main__":
    main()
