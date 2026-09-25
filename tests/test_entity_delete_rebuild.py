"""#3299 — a deleted entity must not resurrect on ``rebuild_all``.

``_delete_entity`` (Point/Subject/Object/Document/Source/Event) hard-deletes
the live node but used to emit NO journal record, so ``rebuild_all`` replayed
the entity's creation event (PointAdded / SubjectAdded / ObjectRegistered /
EventRecorded / SourceCreated) and the deleted entity came back. Deleting
something and having the next rebuild resurrect it is a data-safety defect.

The fix journals the destruction at the same write surface that performs it —
one JSONL ``EntityMutated`` record with ``op="delete"`` and the identity as
written (``id`` + ``label``) — and replay hard-deletes by id, mirroring the
live write exactly (ontology §5: *delete* hard-deletes, *retract* tombstones).

Exit evidence asserted here:
  - the deleted entity is absent live AND absent after ``rebuild_all``;
  - the journal carries the delete record;
  - a non-deleted sibling survives the rebuild (the fidelity check — the fix
    must not "cure" resurrection by dropping everything);
  - delete→recreate rebuilds the SECOND incarnation (live parity — replay
    honours journal order, so the recreate is not swallowed by the delete);
  - deleting an absent id mints no record (no phantom delete in the journal).

Runnable with the docker lane:
  TORTOISE_DB_URI='docker://:falkordb@localhost:6380/tortoise_test_matrix' \\
    uv run pytest tests/test_entity_delete_rebuild.py -q --tb=short \\
    -p no:cacheprovider --import-mode=importlib -rf
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.ids import ulid
from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK, _entity_name_id

# The one journaled write-contract record type (#3299). The ``op`` field
# discriminates the transition; this suite pins ``op="delete"``.
_REC = "EntityMutated"


@pytest.fixture
def env(tmp_path):
    """(sdk, events_dir) with the JSONL journal wired."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "delete.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _journal(events) -> list[dict]:
    return EventLog(str(events / "events.jsonl")).read_all()


def _deletes(journal: list[dict], oid: str) -> list[dict]:
    return [e for e in journal
            if e.get("type") == _REC and e.get("op") == "delete"
            and e.get("id") == oid]


def _rows(proj, cypher: str, **params):
    return proj.g.query(cypher, params=params or None).result_set


def _subject(proj, name: str):
    return _rows(proj, "MATCH (s:Subject {name:$n}) RETURN s.id", n=name)


def _object(proj, name: str):
    return _rows(proj, "MATCH (o:Object {name:$n}) RETURN o.id", n=name)


def _point(proj, pid: str):
    return _rows(proj, "MATCH (p:Point {id:$id}) RETURN p.id", id=pid)


def _event(proj, eid: str):
    return _rows(proj, "MATCH (e:Event {eventId:$id}) RETURN e.eventId", id=eid)


# ═══════════════════════════════════════════════════════════════════════
# The defect: delete → rebuild_all resurrects (RED before the fix)
# ═══════════════════════════════════════════════════════════════════════

class TestDeleteSurvivesRebuild:
    def test_deleted_subject_absent_after_rebuild(self, env):
        """Seed a Subject, delete it, rebuild_all: it must stay absent, and
        the journal must carry the delete."""
        sdk, events = env
        proj = sdk._get_proj()
        name = "delete-me-subject"
        sdk.create_entity("subject", name, subjectKind="core:other",
                          is_episodic=False)
        oid = _entity_name_id("Subject", name)
        assert _subject(proj, name), "seed Subject must exist live"

        assert sdk._delete_entity(oid) is True, "node must be deleted"
        assert not _subject(proj, name), "live node must be gone after delete"

        proj.rebuild_all(str(events))
        assert not _subject(proj, name), (
            "deleted Subject resurrected on rebuild_all — #3299")

        recs = _deletes(_journal(events), oid)
        assert recs, (
            "delete must be journaled (EntityMutated op=delete) — without a "
            "record the SubjectAdded line replays and resurrects the entity")
        assert recs[0].get("label") == "Subject", recs[0]

    def test_deleted_object_absent_after_rebuild(self, env):
        """Same for an Object (its ObjectRegistered line used to replay)."""
        sdk, events = env
        proj = sdk._get_proj()
        name = "delete-me-object"
        sdk.create_entity("object", name, objectKind="core:other",
                          is_episodic=False)
        oid = _entity_name_id("Object", name)

        assert sdk._delete_entity(oid) is True
        assert not _object(proj, name)

        proj.rebuild_all(str(events))
        assert not _object(proj, name), (
            "deleted Object resurrected on rebuild_all — #3299")

        assert _deletes(_journal(events), oid), "delete must be journaled"

    def test_deleted_point_absent_after_rebuild(self, env):
        """Same for a Point deleted through the entity door (the MCP
        ``tortoise_delete_entity`` path)."""
        sdk, events = env
        proj = sdk._get_proj()
        pid = sdk.create_point("statement", "delete-me-point")["id"]
        assert _point(proj, pid)

        assert sdk._delete_entity(pid) is True
        assert not _point(proj, pid)

        proj.rebuild_all(str(events))
        assert not _point(proj, pid), (
            "deleted Point resurrected on rebuild_all — #3299")

        assert _deletes(_journal(events), pid), "delete must be journaled"

    def test_deleted_event_absent_after_rebuild(self, env):
        """Event is keyed by ``eventId`` (the other matching key the live
        delete uses) — replay must delete it too."""
        sdk, events = env
        proj = sdk._get_proj()
        eid = ulid()
        sdk.create_entity("event", "delete-me-event", eventKind="core:other",
                          is_episodic=False, _server_id=eid)
        assert _event(proj, eid)

        assert sdk._delete_entity(eid) is True
        assert not _event(proj, eid)

        proj.rebuild_all(str(events))
        assert not _event(proj, eid), (
            "deleted Event resurrected on rebuild_all — #3299")

        assert _deletes(_journal(events), eid), "delete must be journaled"

    def test_deleted_document_absent_after_rebuild(self, env):
        """Document — a :Source keyed ``url`` (D10, ONTOLOGY v3.15 §4.4);
        id is a server-minted ULID. Completes per-shape coverage of the #3299
        delete-survival guarantee (#3860 acceptance criterion 1)."""
        sdk, events = env
        proj = sdk._get_proj()
        title = "delete-me-document"
        sdk.create_entity("document", title, documentKind="core:other",
                          is_episodic=False)
        rows = _rows(proj, "MATCH (d:Source {title:$t}) RETURN d.id", t=title)
        assert rows, "seed document Source must exist live"
        did = rows[0][0]

        assert sdk._delete_entity(did) is True
        assert not _rows(proj, "MATCH (d:Source {title:$t}) RETURN d.id",
                         t=title)

        proj.rebuild_all(str(events))
        assert not _rows(proj, "MATCH (d:Source {title:$t}) RETURN d.id",
                         t=title), (
            "deleted document Source resurrected on rebuild_all — #3299/#3860")

        assert _deletes(_journal(events), did), "delete must be journaled"

    def test_deleted_source_absent_after_rebuild(self, env):
        """Source is journaled on every write (``SourceCreated``) — its
        delete must be durable like the rest."""
        sdk, events = env
        proj = sdk._get_proj()
        url = "https://example.test/delete-me-source"
        sdk.create_source(url, "document")
        rows = _rows(proj, "MATCH (s:Source {url:$u}) RETURN s.id", u=url)
        assert rows, "seed Source must exist live"
        sid = rows[0][0]

        assert sdk._delete_entity(sid) is True
        assert not _rows(proj, "MATCH (s:Source {url:$u}) RETURN s.id", u=url)

        proj.rebuild_all(str(events))
        assert not _rows(proj, "MATCH (s:Source {url:$u}) RETURN s.id", u=url), (
            "deleted Source resurrected on rebuild_all — #3299")

        assert _deletes(_journal(events), sid), "delete must be journaled"


# ═══════════════════════════════════════════════════════════════════════
# Rebuild fidelity: the fix must not "cure" resurrection by dropping all
# ═══════════════════════════════════════════════════════════════════════

class TestRebuildFidelity:
    def test_undeleted_siblings_survive_rebuild(self, env):
        """A Subject and an Object that were NOT deleted survive the same
        rebuild that removes the deleted one."""
        sdk, events = env
        proj = sdk._get_proj()
        sdk.create_entity("subject", "keep-subject",
                          subjectKind="core:other", is_episodic=False)
        sdk.create_entity("object", "keep-object",
                          objectKind="core:other", is_episodic=False)
        sdk.create_entity("subject", "drop-subject",
                          subjectKind="core:other", is_episodic=False)
        drop_id = _entity_name_id("Subject", "drop-subject")
        assert sdk._delete_entity(drop_id) is True

        proj.rebuild_all(str(events))
        assert _subject(proj, "keep-subject"), (
            "un-deleted Subject must survive rebuild_all")
        assert _object(proj, "keep-object"), (
            "un-deleted Object must survive rebuild_all")
        assert not _subject(proj, "drop-subject"), (
            "deleted Subject must not survive rebuild_all")

    def test_delete_then_recreate_rebuilds_second_incarnation(self, env):
        """delete→recreate: replay honours journal order, so the SECOND
        incarnation (live truth) is what rebuild_all yields — a delete fold
        that ran after the recreate would silently drop live data."""
        sdk, events = env
        proj = sdk._get_proj()
        name = "recreate-me"
        sdk.create_entity("subject", name, subjectKind="core:other",
                          is_episodic=False)
        oid = _entity_name_id("Subject", name)
        assert sdk._delete_entity(oid) is True
        sdk.create_entity("subject", name, subjectKind="core:other",
                          is_episodic=False)

        journal = _journal(events)
        adds = [e for e in journal
                if e.get("type") == "SubjectAdded" and e.get("name") == name]
        assert len(adds) == 2, (
            "delete→recreate journals a second first-registration")
        live = _rows(proj, "MATCH (s:Subject {name:$n}) RETURN s.createdAt",
                     n=name)
        assert live and live[0][0] == adds[1]["createdAt"], (
            "live node carries the SECOND registration's createdAt")

        proj.rebuild_all(str(events))
        rebuilt = _rows(proj, "MATCH (s:Subject {name:$n}) RETURN s.createdAt",
                        n=name)
        assert rebuilt, "recreated Subject must survive rebuild_all"
        assert rebuilt[0][0] == adds[1]["createdAt"], (
            "rebuild must replay the SECOND incarnation (live parity) — the "
            "delete fold must not swallow the later recreate")

    def test_delete_then_recreate_rebuilds_second_point(self, env):
        """#3299 P0: Point is a HOISTED label — pass-1a applies EVERY
        PointAdded before pass-1b folds the delete, so a naive inline delete
        runs after BOTH incarnations and destroys the live one. The #2488-
        style survivor check must keep the re-created Point."""
        sdk, events = env
        proj = sdk._get_proj()
        pid = ulid()
        assert sdk.create_point("statement", "first-incarnation",
                                id=pid)["id"] == pid
        assert _point(proj, pid)
        assert sdk._delete_entity(pid) is True
        assert not _point(proj, pid)
        sdk.create_point("statement", "second-incarnation", id=pid)
        assert _point(proj, pid)

        proj.rebuild_all(str(events))
        assert _point(proj, pid), (
            "re-created Point lost on rebuild_all (pass-1a/pass-1b ordering "
            "inversion) — #3299 P0")
        content = _rows(proj, "MATCH (p:Point {id:$id}) RETURN p.content",
                        id=pid)
        assert content and content[0][0] == "second-incarnation", (
            "rebuild must yield the SECOND incarnation, not the first")

    def test_delete_then_recreate_rebuilds_second_operator(self, env):
        """#3299 P0: OperatorAdded is the other HOISTED creation — an operator
        IS a Point node, so an id-reuse delete→recreate must survive the same
        way. The public ``create_operator`` mints a fresh ULID each call, so
        the re-creation is journaled directly (raw-producer id reuse)."""
        sdk, events = env
        proj = sdk._get_proj()
        src = sdk.create_point("statement", "op-source")["id"]
        tgt = sdk.create_point("statement", "op-target")["id"]
        oid = sdk.create_operator("IMPL", src, [tgt])["id"]
        assert _point(proj, oid)
        assert sdk._delete_entity(oid) is True
        assert not _point(proj, oid)

        # Raw-producer re-creation of the SAME id (the public API mints a
        # new ULID). PointAdded-only handling in pass-1a is the bug: this
        # event must seed the survivor anchor like a PointAdded does.
        with open(events / "events.jsonl", "a") as fh:
            fh.write(json.dumps({
                "event_id": ulid(),
                "ts": datetime.now(UTC).isoformat(),
                "type": "OperatorAdded",
                "initiated_by": "raw-producer",
                "projection_version": 2,
                "point": {
                    "id": oid, "is_operator": True, "op_type": "IMPL",
                    "direction": "bidirectional", "status": "live",
                    "content": f"IMPL({src}, {tgt})",
                    "operator": {"op_type": "IMPL", "inputs": [src, tgt]},
                },
            }) + "\n")

        proj.rebuild_all(str(events))
        assert _point(proj, oid), (
            "re-created Operator lost on rebuild_all (pass-1a/pass-1b "
            "ordering inversion) — #3299 P0")


# ═══════════════════════════════════════════════════════════════════════
# No phantom deletes: an absent id mints no record
# ═══════════════════════════════════════════════════════════════════════

def test_deleting_absent_id_mints_no_journal_record(env):
    """``_delete_entity`` on an id that matches nothing returns False and
    must not journal a delete — a phantom record could erase a re-created
    entity on a later rebuild."""
    sdk, events = env
    ghost = ulid()
    assert sdk._delete_entity(ghost) is False
    assert not _deletes(_journal(events), ghost), (
        "no node deleted → no delete record")


# ═══════════════════════════════════════════════════════════════════════
# Both replay arms: rebuild_all (two-pass) AND rebuild(EventLog) dispatch
# (backup/consistency restore path) must fold the record.
# ═══════════════════════════════════════════════════════════════════════

def test_deleted_entity_absent_after_rebuild_dispatch(env):
    """The ``apply()`` arm (``proj.rebuild(EventLog)``, used by
    backup/consistency restore) must honour the delete too — otherwise the
    fix would be dead code on that surface."""
    sdk, events = env
    proj = sdk._get_proj()
    sdk.create_entity("object", "dispatch-delete", objectKind="core:other",
                      is_episodic=False)
    oid = _entity_name_id("Object", "dispatch-delete")
    assert sdk._delete_entity(oid) is True

    proj.rebuild(EventLog(str(events / "events.jsonl")))
    assert not _object(proj, "dispatch-delete"), (
        "rebuild(EventLog) dispatch resurrected the deleted Object")


def test_in_memory_fold_respects_delete():
    """``fold``/``InMemoryProjection`` (``_apply_one`` — the single source of
    fold semantics) drops the point on an ``EntityMutated op=delete``."""
    from tortoise.projection import fold

    events = [
        {"type": "PointAdded", "point": {"id": "p1", "content": "x"}},
        {"type": "EntityMutated", "id": "p1", "op": "delete",
         "label": "Point"},
    ]
    assert "p1" not in fold(events)


# ═══════════════════════════════════════════════════════════════════════
# #3860 — the survivor anchor is identity (kind + id), not bare id
#
# #3696's anchor (``last_recreate_seq``) matched a delete/replay pair by id
# WITHOUT consulting the node label, so a hoisted Point/Operator creation
# could suppress a *legitimate* delete of a DIFFERENT kind sharing the id —
# and the id-wide replay fold could destroy a foreign-kind node the record
# never owned. Identity is therefore (label, id) at BOTH the suppression gate
# and the fold.
#
# MUTATION (stated, per the issue's acceptance criterion 3): drop the kind
# dimension from the identity key — revert the anchor lookup to bare ``id``
# and the fold to id-wide — and the three ``TestIdentityIsKindPlusId`` tests
# below go RED.
#
# Producers: a non-namespaced id is reachable from the PUBLIC SDK —
# ``create_source(url, ...)`` uses the raw URL as the Source id, and
# ``create_point(..., id=<explicit>)`` accepts an arbitrary id.
# ═══════════════════════════════════════════════════════════════════════


def _ids(proj, cypher: str, **params):
    return _rows(proj, cypher, **params)


def _operator(sdk):
    src = sdk.create_point("statement", "op-source")["id"]
    tgt = sdk.create_point("statement", "op-target")["id"]
    return sdk.create_operator("IMPL", src, [tgt])["id"]


class TestIdentityIsKindPlusId:
    """#3860: a delete can never match (or be suppressed by) a node it does
    not own."""

    def test_subject_delete_not_suppressed_by_later_point_same_id(self, env):
        """T1 (no suppression) + T2 (no foreign destruction), public SDK.

        A Subject's id is re-used by a raw/explicit-id Point AFTER the
        Subject is deleted. Live: Source/Subject gone, Point present. The
        bare-id anchor lets the Point's hoisted creation suppress the
        Subject delete, and the id-wide fold would then have no record left
        to remove the Point.
        """
        sdk, events = env
        proj = sdk._get_proj()
        name = "collide-subject-3860"
        sdk.create_entity("subject", name, subjectKind="core:other",
                          is_episodic=False)
        sid = _entity_name_id("Subject", name)
        assert _subject(proj, name), "seed Subject must exist live"

        assert sdk._delete_entity(sid) is True
        assert not _subject(proj, name)

        # Foreign kind re-uses the SAME non-namespaced id, AFTER the delete.
        sdk.create_point("statement", "collide-point", id=sid)
        assert _point(proj, sid)

        proj.rebuild_all(str(events))
        assert not _subject(proj, name), (
            "deleted Subject resurrected: the bare-id survivor anchor let a "
            "foreign-kind (Point) creation suppress its delete — #3860")
        assert _point(proj, sid), (
            "live Point destroyed on replay: the fold matched a node it does "
            "not own — #3860")

    def test_source_url_delete_not_suppressed_by_later_point_same_id(self, env):
        """T1 + T2 for the OTHER public non-namespaced producer: a Source's
        id IS the raw ingestion URL (``create_source`` → ``_create_entity
        ("Source", url, ...)``). No raw journal needed."""
        sdk, events = env
        proj = sdk._get_proj()
        url = "https://collide.test/3860-source-url"
        sdk.create_source(url, "document")
        assert _ids(proj, "MATCH (s:Source {id:$i}) RETURN s.id", i=url)

        assert sdk._delete_entity(url) is True
        assert not _ids(proj, "MATCH (s:Source {id:$i}) RETURN s.id", i=url)

        sdk.create_point("statement", "collide-point", id=url)
        assert _point(proj, url)

        proj.rebuild_all(str(events))
        assert not _ids(proj, "MATCH (s:Source {id:$i}) RETURN s.id", i=url), (
            "deleted Source (id == URL) resurrected — #3860")
        assert _point(proj, url), (
            "live Point destroyed on replay — #3860")

    def test_rebuild_dispatch_arm_is_kind_scoped(self, env):
        """The chronological ``apply()`` arm must be kind-scoped too, or the
        two replay arms diverge.

        The collision must exist in JOURNAL order for the delete record: the
        Point is created BEFORE the delete is appended, so the chronological
        arm has a same-id foreign-kind node to scope against. (The earlier
        shape created the Point after the delete, leaving the arm nothing to
        mis-scope against — so an id-wide regression slipped through.)
        """
        sdk, events = env
        proj = sdk._get_proj()
        url = "https://collide.test/3860-dispatch"
        sdk.create_source(url, "document")
        pid = sdk.create_point("statement", "collide-point", id=url)["id"]
        assert _point(proj, pid), "seed Point must exist live"

        # The Source delete is journaled AFTER the Point exists, so the
        # chronological apply() arm has a foreign-kind node to scope against.
        with open(events / "events.jsonl", "a") as fh:
            fh.write(json.dumps({
                "event_id": ulid(), "ts": datetime.now(UTC).isoformat(),
                "type": "EntityMutated", "initiated_by": "raw-producer",
                "projection_version": 2, "id": pid, "op": "delete",
                "label": "Source",
            }) + "\n")

        proj.rebuild(EventLog(str(events / "events.jsonl")))
        assert not _ids(proj, "MATCH (s:Source {id:$i}) RETURN s.id", i=url), (
            "rebuild(EventLog) resurrected the deleted Source — #3860")
        assert _point(proj, pid), (
            "rebuild(EventLog) destroyed the live Point: the chronological "
            "apply() arm is not kind-scoped — #3860")

    def test_in_memory_fold_ignores_foreign_kind_delete(self):
        """T2 on the third replay arm: ``_apply_one``'s index is point-only,
        so a delete naming a DIFFERENT canonical kind must not pop a Point."""
        from tortoise.projection import fold

        events = [
            {"type": "PointAdded", "point": {"id": "p1", "content": "x"}},
            {"type": "EntityMutated", "id": "p1", "op": "delete",
             "label": "Subject"},
        ]
        assert "p1" in fold(events), (
            "a Subject delete popped the Point p1 — the in-memory fold matched "
            "a node it does not own — #3860")

    def test_unknown_mutation_label_falls_back_without_injection(self, env):
        """An unknown/hostile ``label`` must never reach the Cypher label
        position (it is query STRUCTURE); it falls back to the legacy id-wide
        delete, so the delete still survives and nothing is injected."""
        sdk, events = env
        proj = sdk._get_proj()
        pid = sdk.create_point("statement", "hostile-label")["id"]
        assert _point(proj, pid)

        with open(events / "events.jsonl", "a") as fh:
            fh.write(json.dumps({
                "event_id": ulid(),
                "ts": datetime.now(UTC).isoformat(),
                "type": "EntityMutated",
                "initiated_by": "raw-producer",
                "projection_version": 2,
                "id": pid,
                "op": "delete",
                "label": "Point) DETACH DELETE (n",
            }) + "\n")

        proj.rebuild_all(str(events))
        assert not _point(proj, pid), (
            "unknown-label delete did not survive replay (legacy id-wide "
            "fallback expected)")


def test_foreign_kind_delete_does_not_advance_point_annotator_boundary(env):
    """T4: the annotator drop boundary (``last_ann_drop_seq``) is a POINT
    boundary. A foreign-kind delete record sharing the id must not mark the
    Point as hard-deleted, or a live-valid annotation is silently dropped on
    replay — a self-contradiction once the fold is kind-scoped.

    Journal: OperatorAdded(X) → OperatorAnnotated(X) → EntityMutated(delete,
    X, label=Subject) → bare OperatorAdded(X) re-emit. The bare re-emit MERGEs
    and never clears annotator dims, so the dims are live-truth.
    """
    dims = ("annotator_bias", "annotator_precision",
            "annotator_consistency", "annotator_directness")
    sdk, events = env
    proj = sdk._get_proj()
    op = _operator(sdk)
    sdk.annotate_operator(op, 0.4, 0.3, 0.2, 0.1)
    pre = {k: (sdk.get_point(op) or {}).get(k) for k in dims}
    assert all(v is not None for v in pre.values())

    records = _journal(events)
    snap = next(r["point"] for r in records
                if r.get("type") == "OperatorAdded"
                and r["point"]["id"] == op)
    duplicate = dict(snap)
    for k in (*dims, "embedding", "content_hash"):
        duplicate.pop(k, None)
    with open(events / "events.jsonl", "a") as fh:
        fh.write(json.dumps({
            "event_id": ulid(), "ts": datetime.now(UTC).isoformat(),
            "type": "EntityMutated", "initiated_by": "raw-producer",
            "projection_version": 2, "id": op, "op": "delete",
            "label": "Subject",
        }) + "\n")
        fh.write(json.dumps({
            "event_id": ulid(), "ts": datetime.now(UTC).isoformat(),
            "type": "OperatorAdded", "initiated_by": "raw-producer",
            "projection_version": 2, "point": duplicate,
        }) + "\n")

    proj.rebuild_all(str(events))
    rebuilt = {k: (sdk.get_point(op) or {}).get(k) for k in dims}
    assert rebuilt == pre, (
        "a foreign-kind (Subject) delete advanced the POINT annotator "
        "boundary and dropped a live-valid annotation — #3860")
