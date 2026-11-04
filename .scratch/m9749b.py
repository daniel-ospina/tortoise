import os, sys, tempfile, shutil
sys.path.insert(0, os.getcwd())
from tortoise.sdk import TortoiseSDK
d = tempfile.mkdtemp(prefix="m9749b_")
sdk = TortoiseSDK(os.path.join(d, "t.db"))
try:
    g = sdk._get_proj().g
    for kind, label in (("subject", "Subject"), ("object", "Object")):
        b = {"entities": [{"ref": "a", "type": kind, "name": f"{label}A"},
                          {"ref": "b", "type": kind, "name": f"{label}B"}],
             "connections": [{"from": "a", "to": "b", "operator": "IMPL",
                              "confidence": 0.4, "weight": 0.2,
                              "direction": "unidirectional"}]}
        print(kind, "validation:", sdk._validate_bundle(b))
        r = sdk.ingest(b)
        print(kind, "ingest:", r.get("created"), r.get("warnings"))
    print("direct edges:", g.query(
        "MATCH (a)-[r:IMPL]->(b) RETURN labels(a), a.name, labels(b), b.name, r.confidence, r.weight, r.direction, r.batch_id").result_set)
finally:
    sdk.close(); shutil.rmtree(d, ignore_errors=True)
