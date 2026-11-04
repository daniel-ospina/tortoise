import os, sys, tempfile, shutil, json
sys.path.insert(0, os.getcwd())
from tortoise.sdk import TortoiseSDK

d = tempfile.mkdtemp(prefix="m9749_")
sdk = TortoiseSDK(os.path.join(d, "t.db"))
try:
    # --- 1. promoted Point<->Point plain IMPL with confidence ---
    b = {"points": [{"ref": "p1", "kind": "statement", "content": "one"},
                    {"ref": "p2", "kind": "statement", "content": "two"}],
         "connections": [{"from": "p1", "to": "p2", "operator": "IMPL",
                          "confidence": 0.5, "weight": 0.25}]}
    print("validation:", sdk._validate_bundle(b))
    res = sdk.ingest(b)
    print("ingest:", {k: res.get(k) for k in ("created", "deduped", "warnings")})
    rows = sdk._get_proj().g.query(
        "MATCH (o:Point {is_operator:true}) RETURN o.id, o.label").result_set
    print("operators:", rows)
    for (oid, lab) in rows:
        r = sdk._get_proj().g.query(
            "MATCH (o:Point {id:$i})-[r:IMPL]->(t) RETURN r.idx, r.confidence, r.weight, r.direction",
            params={"i": oid}).result_set
        print("  IMPL edges:", r)
    # read back through the API surface
    try:
        ops = sdk.list_operators() if hasattr(sdk, "list_operators") else None
        print("list_operators:", json.dumps(ops)[:400] if ops else ops)
    except Exception as e:
        print("list_operators err:", e)

    # --- 2. direct route Object<->Object with confidence ---
    b2 = {"entities": [{"ref": "s1", "type": "Subject", "name": "Alpha"},
                       {"ref": "s2", "type": "Subject", "name": "Beta"}],
          "connections": [{"from": "s1", "to": "s2", "relation": "extractedFrom"}]}
    # use objects instead: direct IMPL between two Objects
    b3 = {"entities": [{"ref": "o1", "type": "Object", "name": "O1"},
                       {"ref": "o2", "type": "Object", "name": "O2"}],
          "connections": [{"from": "o1", "to": "o2", "operator": "IMPL",
                           "confidence": 0.6, "weight": 0.3}]}
    print("v3:", sdk._validate_bundle(b3))
    res3 = sdk.ingest(b3)
    print("ingest3:", {k: res3.get(k) for k in ("created", "deduped", "warnings")})
    print("direct IMPL edges on Object:", sdk._get_proj().g.query(
        "MATCH (a:Object)-[r:IMPL]->(b:Object) RETURN a.name, b.name, r.confidence, r.weight, r.direction").result_set)
    print("all IMPL edges w/ attrs:", sdk._get_proj().g.query(
        "MATCH (a)-[r:IMPL]->(b) RETURN labels(a), a.id, labels(b), b.id, r.confidence, r.weight").result_set)
finally:
    sdk.close(); shutil.rmtree(d, ignore_errors=True)
