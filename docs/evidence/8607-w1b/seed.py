"""Seed an EXACT, enumerable legacy population on the lane graph, then dump it."""

import contextlib
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from falkordb import FalkorDB

from tortoise.hosted_backup import dump_graph

PORT = int(os.environ.get("T8607_PORT", "6379"))  # point at YOUR lane instance
GRAPH = "t8607_census"

db = FalkorDB(host="127.0.0.1", port=PORT, password="")
with contextlib.suppress(Exception):
    db.select_graph(GRAPH).delete()
g = db.select_graph(GRAPH)


def batch(label, keyprop, values):
    for start in range(0, len(values), 500):
        chunk = values[start : start + 500]
        g.query(
            f"UNWIND $rows AS r CREATE (n:{label}) "
            f"SET n.{keyprop} = r.k, n.content = r.c, n.createdAt = r.t, n.label = $l",
            {"rows": chunk, "l": label},
        )


# 8,700 canonical :Point in the shape ids.ulid() really mints: <timehex>-<12hex>
canonical = [
    {
        "k": f"{0x018F0000_0000_0000 + i:016x}-{i:012x}",
        "c": f"canonical point {i}",
        "t": "2026-09-01T00:00:00Z",
    }
    for i in range(8700)
]
batch("Point", "id", canonical)

# 1,000 legacy p-NNNNNNN :Point
legacy_ids = [
    {"k": f"p-{i:07d}", "c": f"legacy point {i}", "t": "2025-01-01T00:00:00Z"}
    for i in range(1, 1001)
]
batch("Point", "id", legacy_ids)

# ~30 EV-* :Event — id MIRRORS eventId (§1.1); out of scope by decision
events = [{"k": f"EV-{i:08x}", "c": f"event {i}", "t": "2025-01-01T00:00:00Z"} for i in range(30)]
g.query(
    "UNWIND $rows AS r CREATE (n:Event) SET n.eventId = r.k, n.id = r.k, "
    "n.content = r.c, n.createdAt = r.t, n.label = 'Event'",
    {"rows": events},
)


def _handle(prefix, noun, i):
    """The `id` a :Subject/:Object carries — the shape, and the exact bytes the
    documented snapshot fingerprint was taken over."""
    return prefix + hashlib.sha256(f"{noun}-{i}".encode()).hexdigest()[:26]


# :Subject/:Object are keyed on `id` since #9881 — `name` is the mutable lookup.
batch(
    "Subject",
    "id",
    [{"k": _handle("sub-", "subject", i), "c": "", "t": "2026-01-01T00:00:00Z"} for i in range(60)],
)
batch(
    "Object",
    "id",
    [{"k": _handle("obj-", "object", i), "c": "", "t": "2026-01-01T00:00:00Z"} for i in range(60)],
)
batch(
    "Source",
    "url",
    [{"k": f"https://example.test/{i}", "c": "", "t": "2026-01-01T00:00:00Z"} for i in range(20)],
)
# a label the contract does not key at all
g.query("CREATE (n:Meta) SET n.key = 'calibration_milestone', n.value = 'gated-b'")

print("store count:", g.query("MATCH (n) RETURN count(n)").result_set)
dump = dump_graph(g, graph_name=GRAPH)
Path("dump.json").write_text(json.dumps(dump, indent=1, sort_keys=True))
print(
    "format:",
    dump["format"],
    "| nodes:",
    dump["node_count"],
    "| source:",
    dump["source_node_count"],
    "| edges:",
    dump["edge_count"],
    "| excluded:",
    dump["excluded_node_count"],
)
