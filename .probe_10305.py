import tempfile, os, time
t0=time.time()
def lap(m):
    print(f"{time.time()-t0:7.1f}s {m}", flush=True)
from tortoise.sdk import TortoiseSDK
d = tempfile.mkdtemp()
sdk = TortoiseSDK(os.path.join(d, "probe.db")); lap("sdk up")
bundle = {"points": [{"ref": "q1", "kind": "statement", "content": "same"},
                     {"ref": "q2", "kind": "statement", "content": "same"},
                     {"ref": "q3", "kind": "statement", "content": "other"}],
          "connections": [{"from": "q1", "to": ["q3"], "operator": "IMPL"}]}
first = sdk.ingest(bundle); lap("ingest 1")
q1 = first["ids"]["refs"]["q1"]; q3 = first["ids"]["refs"]["q3"]
legacy = first["ids"]["connections"][0]
sdk._get_proj().g.query(
    "MATCH (o:Point {id:$o}), (t:Point {id:$t}) "
    "CREATE (o)-[:IMPL {idx:2}]->(t) CREATE (t)-[:INPUT {idx:2}]->(o)",
    params={"o": legacy, "t": q3}); lap("raw dup edge")
ss = sdk.create_operator("IMPL", q1, [q1, q3])["id"]; lap("create self-support")
hit = sdk._find_operator("IMPL", [q1, q1, q3]); lap(f"find_operator -> {hit}")
res = sdk.ingest(bundle); lap("ingest 2")
print("deduped", res["deduped"], "created", res["created"], flush=True)
lap("done")
