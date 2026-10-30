import os, sys, tempfile
sys.path.insert(0, ".")
from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import run_fts_query, _breaker, reset_circuit_breakers

db = os.path.join(tempfile.mkdtemp(prefix="tie10121_"), "test.db")
sdk = TortoiseSDK(db, namespace="tie10121_repro")
g = sdk._get_proj().g
g.query("CREATE (o:Object {id:'obj-aaa', name:'obj dated a', objectKind:'concept', content:'obj dated probe'})")
for s in ("b","c","d"):
    g.query("CREATE (o:Object {name:$n, objectKind:'concept', content:'obj dated probe'})", params={"n": f"obj dated {s}"})

trace=[]
print("clean:", run_fts_query(g, "obj dated", entity_type="object", limit=10, excluded_statuses=(), leg_trace=trace), trace[-1])

# trip the module-level fts breaker exactly as 3 consecutive slow/failed queries would
for _ in range(3):
    _breaker("fts").record_failure()
trace=[]
print("breaker open:", run_fts_query(g, "obj dated", entity_type="object", limit=10, excluded_statuses=(), leg_trace=trace), trace[-1])
reset_circuit_breakers()
trace=[]
print("after reset:", len(run_fts_query(g,"obj dated",entity_type="object",limit=10,excluded_statuses=(),leg_trace=trace)), trace[-1])
