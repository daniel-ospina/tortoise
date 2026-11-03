"""Dump every base-graph point's search_keys + content, identifier ON vs OFF."""
import json
import sys

sys.path.insert(0, ".")
import tests._assembly_graph as ag  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402

sdk = TortoiseSDK()
ag.build_base_graph(sdk)
rows = sdk._get_proj().g.query(
    "MATCH (n:Point) RETURN n.id, n.content, n.search_keys, n.name, n.session_id "
    "ORDER BY n.id"
).result_set
out = []
for r in rows:
    out.append({"id": r[0], "content": r[1], "search_keys": r[2],
                "name": r[3], "session_id": r[4]})
print(json.dumps(out, indent=1, default=str))
