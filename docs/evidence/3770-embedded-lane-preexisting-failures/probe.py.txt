import importlib.util, math, tempfile, pathlib, sys
spec = importlib.util.spec_from_file_location("t", "tests/test_rebuild_recreate_content_parity.py")
t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
from tortoise.sdk import TortoiseSDK
from tortoise.embeddings import compute_embedding
import numpy as np

def cos(a,b):
    a=np.array(a,float); b=np.array(b,float)
    return float(a@b/(np.linalg.norm(a)*np.linalg.norm(b)))

for label, recs_fn, oracle_name in [
    ("bare_reemit", lambda sdk,pid: [t._revised(sdk,pid,new_content="CHANGED"), t._added(sdk,pid,"REEMITTED")], "REEMITTED"),
    ("intermediate", lambda sdk,pid: [t._revised(sdk,pid,new_content="CHANGED"), t._added(sdk,pid,"R1"), t._added(sdk,pid,"")], "R1"),
]:
    tmp = pathlib.Path(tempfile.mkdtemp())
    events = tmp/"events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp/"x.db"), event_log_path=str(events/"events.jsonl"))
    pid = sdk.create_point("statement","ORIG",status="live")["id"]
    records = t._journal(events)
    records.extend(recs_fn(sdk,pid))
    t._rewrite_journal(events, records)
    applied = t._oracle(tmp, "o_"+label, records, pid)
    t._rebuild(sdk, events)
    post = sdk.get_point(pid)
    print("="*70); print(label, "content post=",repr(post["content"]), "applied=",repr(applied["content"]))
    pe, ae = post.get("embedding"), applied.get("embedding")
    print("post  embedding[:3]:", pe[:3] if pe else None)
    print("appl  embedding[:3]:", ae[:3] if ae else None)
    print("post == applied exactly:", pe==ae)
    for cand in ["ORIG","CHANGED","REEMITTED","R1",""]:
        cv = compute_embedding(cand)
        if cv is None: continue
        print(f"  cos(post,{cand!r})={cos(pe,cv):.6f}  cos(applied,{cand!r})={cos(ae,cv):.6f}")
    sdk.close()
