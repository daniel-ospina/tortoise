from __future__ import annotations
import os, uuid
os.environ.setdefault("TORTOISE_TEST_MODE", "1")
from tortoise.sdk import TortoiseSDK
ns = f"test_ev2_{uuid.uuid4().hex[:8]}"
sdk = TortoiseSDK(namespace=ns)
try:
    pid = sdk.create_point("statement", "EVX2")["id"]
    sdk.update_point(pid, confidence=0.5)
    print("types:", sdk._get_proj().g.query(
        "MATCH (e:GraphEvent) RETURN e.type, count(e)").result_set)
    ep = sdk._get_proj().g.query("MATCH (m:EpMeta) RETURN m.ep_version").result_set
    print("ep:", ep)
    print("emit result on absent:", sdk._emit_event("PointRevised", id="zzz-absent", confidence=0.5))
    print("types after absent emit:", sdk._get_proj().g.query(
        "MATCH (e:GraphEvent) RETURN e.type, count(e)").result_set)
finally:
    sdk.close()
