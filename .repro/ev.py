from __future__ import annotations
import os, uuid
os.environ.setdefault("TORTOISE_TEST_MODE", "1")
from tortoise.sdk import TortoiseSDK

ns = f"test_ev_{uuid.uuid4().hex[:8]}"
sdk = TortoiseSDK(namespace=ns)
try:
    pid = sdk.create_point("statement", "EVX")["id"]
    print("all events:", sdk._get_proj().g.query(
        "MATCH (e:GraphEvent) RETURN count(e)").result_set)
    print("types:", sdk._get_proj().g.query(
        "MATCH (e:GraphEvent) RETURN e.type, count(e)").result_set)
finally:
    sdk.close()
