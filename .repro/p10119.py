from __future__ import annotations
import os, tempfile, uuid
os.environ.setdefault("TORTOISE_TEST_MODE", "1")
from tortoise.sdk import TortoiseSDK

PROBE = "no-such-point-9202"
ns = f"test_10119j_{uuid.uuid4().hex[:8]}"
d = tempfile.mkdtemp()
log = os.path.join(d, "events.jsonl")
sdk = TortoiseSDK(namespace=ns, event_log_path=log)
def rows():
    if not os.path.exists(log):
        return []
    out = []
    import json
    for line in open(log):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out
def ep():
    r = sdk._get_proj().g.query("MATCH (m:EpMeta) RETURN m.ep_version").result_set
    return r[0][0] if r else None
try:
    before = rows(); ep0 = ep()
    reply = sdk.update_point(PROBE, confidence=0.5)
    after = rows(); ep1 = ep()
    print(f"journal rows before={len(before)} after={len(after)}")
    print("appended:", [ (r.get('event') or r.get('type')) for r in after[len(before):] ])
    print("appended full:", after[len(before):])
    print(f"ep_version {ep0} -> {ep1}")
    print("reply:", reply)
    print("dirty_roots:", sorted(sdk._dirty_roots))
    real = [r[0] for r in sdk._get_proj().g.query("MATCH (n) WHERE n:Point OR n:Operator RETURN n.id").result_set]
    sdk._sweep_dirty_roots(set(real))
    print("after sweep:", sorted(sdk._dirty_roots))
    print("auto_dream_mode:", sdk._auto_dream_mode())
finally:
    sdk.close()
