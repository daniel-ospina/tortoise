import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, "tests")
from tortoise.projection import FalkorProjection

_EVENT_ID = "evt-7936-rekey"; _OBJECT_ID = "obj-7936-rekey"; _POINT_ID = "pt-7936-rekey"
J = [
    {"type": "EventRecorded", "id": _EVENT_ID, "name": "rekey event",
     "eventKind": "test", "startedAt": "2026-01-01T00:00:00Z",
     "subject": "rekey-subject", "object": "rekey-object"},
]
FULL = J + [
    {"type": "ObjectRegistered", "id": _OBJECT_ID, "name": "rekey-object"},
    {"type": "PointAdded",
     "point": {"id": _POINT_ID, "content": "rekey point",
               "pointKind": "statement", "status": "live"}},
]
tmp = Path(tempfile.mkdtemp())
def build(name, recs):
    d = tmp / name; d.mkdir()
    (d/"events.jsonl").write_text("".join(json.dumps(r)+"\n" for r in recs))
    p = FalkorProjection(path=str(tmp/f"{name}.db"), skip_health_check=True)
    p.rebuild_all(str(d), confirm_destructive=True)
    return p

p = build("prefix", J)
for lbl in ("Object", "Subject", "Event", "Point"):
    print("PREFIX", lbl, p.g.query(f"MATCH (n:{lbl}) RETURN n.id, n.name, n._stub").result_set)
p.close()

p = build("full", FULL)
for lbl in ("Object", "Subject", "Event", "Point"):
    print("FULL", lbl, p.g.query(f"MATCH (n:{lbl}) RETURN n.id, n.name, n._stub, n.status").result_set)
p.close()
