"""Non-vacuity probe: run the main-era journal through the PRE-FIX fold."""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, ".")
from tortoise.legacy_ids import _derived_legacy_handle
from tortoise.projection import FalkorProjection, NonFoldedEventsError

name, successor = "legacy-R", "successor-S"
synthesized = _derived_legacy_handle("Object", name)

tmp = Path(tempfile.mkdtemp())
events = tmp / "events"; events.mkdir()
with open(events / "events.jsonl", "w") as fh:
    for rec in (
        {"type": "ObjectRegistered", "id": f"github-issue-{name}-7",
         "name": name, "objectKind": "core:strategy", "status": "live"},
        {"type": "ObjectSuperseded", "id": synthesized, "name": name,
         "supersedes_by": successor, "ts": "2026-01-01T00:00:00Z"},
    ):
        fh.write(json.dumps(rec) + "\n")


def prefix_fold(self, ev, *, cas=False):
    """HEAD~ (pre-fix): ID-KEYED ONLY."""
    oid = ev.get("id")
    if not oid:
        return (0, 0)
    live = ("SET o.status='superseded', o.supersededBy=$sb, "
            "o.supersededAt=$sa RETURN o.id LIMIT 1")
    rows = self.g.query("MATCH (o:Object {id:$id}) " + live,
                        params={"id": oid, "sb": "x", "sa": "y"}).result_set or []
    return (len(rows), len(rows))


orig = FalkorProjection._fold_object_superseded

# 1. pre-fix fold on the SAME journal
FalkorProjection._fold_object_superseded = prefix_fold
proj = FalkorProjection(path=str(tmp / "prefix.db"), skip_health_check=True)
try:
    proj.rebuild_all(str(events), confirm_destructive=True)
    print("PRE-FIX: rebuild_all ACCEPTED (unexpected)")
except NonFoldedEventsError as exc:
    print("PRE-FIX: NonFoldedEventsError as predicted ->", str(exc)[:160])
finally:
    proj.close()

# 2. fixed fold on the same journal
FalkorProjection._fold_object_superseded = orig
proj = FalkorProjection(path=str(tmp / "fixed.db"), skip_health_check=True)
try:
    proj.rebuild_all(str(events), confirm_destructive=True)
    rows = proj.g.query("MATCH (o:Object {name:$n}) RETURN o.id, o.status",
                        params={"n": name}).result_set
    print("FIXED: rebuild_all clean ->", rows)
finally:
    proj.close()
