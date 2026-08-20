"""Tests for #211: wire remaining about* edges — aboutEvent, aboutPoint,
aboutDocument producers.

Covers:
- backfill_about_entities creates aboutEvent + aboutDocument edges
- create_event wires aboutPoint + aboutDocument edges
- existing aboutSubject/aboutObject edges unbroken
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """Create a fresh SDK connected to an isolated test graph."""
    db_path = f"{tempfile.mkdtemp(prefix='tt_211_')}/test.db"
    s = TortoiseSDK(db_path)
    s.test_guard = lambda: None  # bypass production guard for test graph
    yield s
    s.close()


# ── backfill_about_entities ─────────────────────────────────────────────

class TestBackfillAboutEntities:
    """backfill_about_entities creates about* edges when Points mention
    entity names (Subject, Object, Event, Document)."""

    def test_backfill_creates_about_subject(self, sdk):
        """Point mentioning a Subject name → aboutSubject edge."""
        sdk.create_subject("alice", "engineer")
        sdk.create_point("statement", "alice deployed the service")
        result = sdk.backfill_about_entities()
        assert result["scanned"] >= 1
        assert result["updated"] >= 1
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (p:Point)-[:aboutSubject]->(s:Subject {name:'alice'}) "
            "RETURN count(*) > 0"
        ).result_set
        assert r[0][0] is True

    def test_backfill_creates_about_object(self, sdk):
        """Point mentioning an Object name → aboutObject edge."""
        sdk.create_object("artifact-v1", "release")
        sdk.create_point("statement", "artifact-v1 passed all checks")
        result = sdk.backfill_about_entities()
        assert result["scanned"] >= 1
        assert result["updated"] >= 1
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (p:Point)-[:aboutObject]->(o:Object {name:'artifact-v1'}) "
            "RETURN count(*) > 0"
        ).result_set
        assert r[0][0] is True

    def test_backfill_creates_about_event(self, sdk):
        """Point mentioning an Event name → aboutEvent edge."""
        ev = sdk.create_event("sprint-42-retro", "meeting")
        sdk.create_point("statement", "sprint-42-retro covered deploy issues")
        result = sdk.backfill_about_entities()
        assert result["scanned"] >= 1
        assert result["updated"] >= 1
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (p:Point)-[:aboutEvent]->(e:Event {name:'sprint-42-retro'}) "
            "RETURN count(*) > 0"
        ).result_set
        assert r[0][0] is True

    def test_backfill_creates_about_document(self, sdk):
        """Point mentioning a Document title → aboutDocument edge."""
        doc = sdk.create_document("design-doc-42", "architecture")
        sdk.create_point("statement", "design-doc-42 proposes a new pipeline")
        result = sdk.backfill_about_entities()
        assert result["scanned"] >= 1
        assert result["updated"] >= 1
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (p:Point)-[:aboutDocument]->(d:Document {title:'design-doc-42'}) "
            "RETURN count(*) > 0"
        ).result_set
        assert r[0][0] is True

    def test_backfill_idempotent(self, sdk):
        """backfill_about_entities is idempotent — second run doesn't duplicate."""
        sdk.create_subject("bob", "designer")
        sdk.create_point("statement", "bob reviewed the wireframes")
        first = sdk.backfill_about_entities()
        second = sdk.backfill_about_entities()
        assert first["scanned"] == second["scanned"]
        # Edge count unchanged between runs (MERGE prevents duplicates)
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH ()-[e:aboutSubject]->(:Subject {name:'bob'}) RETURN count(e)"
        ).result_set
        assert r[0][0] == 1

    def test_backfill_no_match_no_edges(self, sdk):
        """Point mentioning no entity name → no about* edges created."""
        sdk.create_subject("carol", "manager")
        sdk.create_point("statement", "unrelated text with no entity names")
        result = sdk.backfill_about_entities()
        assert result["updated"] == 0


# ── create_event aboutPoint / aboutDocument ─────────────────────────────

class TestCreateEventAboutEdges:
    """create_event wires aboutPoint and aboutDocument when params provided."""

    def test_create_event_wires_about_point(self, sdk):
        """create_event with aboutPoint creates aboutPoint edge."""
        pt = sdk.create_point("statement", "deploy completed successfully")
        ev = sdk.create_event("deploy-2026", "deployment",
                              aboutPoint=pt["id"])
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid})-[a:aboutPoint]->(p:Point {id:$pid}) "
            "RETURN count(a) > 0",
            params={"eid": ev["eventId"], "pid": pt["id"]},
        ).result_set
        assert r[0][0] is True

    def test_create_event_wires_about_document(self, sdk):
        """create_event with aboutDocument creates aboutDocument edge."""
        doc = sdk.create_document("meeting-notes-42", "notes")
        ev = sdk.create_event("sync-42", "meeting",
                              aboutDocument=doc["id"])
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid})-[a:aboutDocument]->"
            "(d:Document {id:$did}) RETURN count(a) > 0",
            params={"eid": ev["eventId"], "did": doc["id"]},
        ).result_set
        assert r[0][0] is True

    def test_create_event_wires_multiple_about_edges(self, sdk):
        """create_event with all four about* params creates all edges."""
        subj = sdk.create_subject("dave", "engineer")
        obj = sdk.create_object("release-3", "release")
        pt = sdk.create_point("statement", "all tests green")
        doc = sdk.create_document("release-notes-3", "notes")

        ev = sdk.create_event("release-event-3", "release",
                              aboutSubject=subj["id"],
                              aboutObject=obj["id"],
                              aboutPoint=pt["id"],
                              aboutDocument=doc["id"])
        proj = sdk._get_proj()
        eid = ev["eventId"]

        # Verify all four edges exist
        for edge, target_id in [
            ("aboutSubject", subj["id"]),
            ("aboutObject", obj["id"]),
            ("aboutPoint", pt["id"]),
            ("aboutDocument", doc["id"]),
        ]:
            r = proj.g.query(
                f"MATCH (e:Event {{eventId:$eid}})-[:{edge}]->(t) "
                "WHERE t.id = $tid OR t.eventId = $tid "
                "RETURN count(*) > 0",
                params={"eid": eid, "tid": target_id},
            ).result_set
            assert r[0][0] is True, f"{edge} edge missing"

    def test_create_event_prefixed_id_no_stub(self, sdk):
        """#1516: prefixed entity ids (sub-<hex26>) must NOT run the name
        fallback (which minted stub Subject nodes named after the id and
        wired spurious aboutSubject edges). The edge must land on the
        canonical node only, with zero stubs."""
        subj = sdk.create_subject("ida", "engineer")
        ev = sdk.create_event("prefixed-id-event", "meeting",
                              aboutSubject=subj["id"])
        proj = sdk._get_proj()
        eid = ev["eventId"]
        # (a) no stub Subject node whose name equals the id
        stub = proj.g.query(
            "MATCH (s:Subject {name:$name}) RETURN count(s)",
            params={"name": subj["id"]},
        ).result_set
        assert stub[0][0] == 0, \
            f"stub Subject created named after the id: {subj['id']}"
        # (b) exactly one Subject node with the canonical id
        canon = proj.g.query(
            "MATCH (s:Subject {id:$sid}) RETURN count(s)",
            params={"sid": subj["id"]},
        ).result_set
        assert canon[0][0] == 1, "canonical Subject not unique"
        # (c) the aboutSubject edge lands on the canonical node only
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid})-[a:aboutSubject]->"
            "(s:Subject {id:$sid}) RETURN count(a)",
            params={"eid": eid, "sid": subj["id"]},
        ).result_set
        assert r[0][0] == 1, "aboutSubject edge did not land on canonical node"

    def test_create_event_name_still_resolves(self, sdk):
        """#1516: a PLAIN-NAME aboutSubject still runs the name fallback
        (creates/resolves the Subject by name) — the guard must only skip
        id-shaped values, not names."""
        ev = sdk.create_event("name-event", "meeting",
                              aboutSubject="brand-new-team")
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid})-[a:aboutSubject]->"
            "(s:Subject {name:'brand-new-team'}) RETURN count(a) > 0",
            params={"eid": ev["eventId"]},
        ).result_set
        assert r[0][0] is True, "name-valued aboutSubject no longer resolves"


# ── Existing edges unbroken (regression) ────────────────────────────────

class TestAboutEdgesRegression:
    """Verify existing aboutSubject/aboutObject producers are unbroken."""

    def test_create_event_wires_about_subject_still_works(self, sdk):
        """create_event with aboutSubject still works (pre-existing)."""
        subj = sdk.create_subject("eve", "pm")
        ev = sdk.create_event("planning", "meeting",
                              aboutSubject=subj["id"],
                              subject="eve")
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid})-[a:aboutSubject]->"
            "(s:Subject {id:$sid}) RETURN count(a) > 0",
            params={"eid": ev["eventId"], "sid": subj["id"]},
        ).result_set
        assert r[0][0] is True

    def test_create_event_wires_about_object_still_works(self, sdk):
        """create_event with aboutObject still works (pre-existing)."""
        obj = sdk.create_object("component-x", "service")
        ev = sdk.create_event("deploy-x", "deployment",
                              aboutObject=obj["id"],
                              subject="frank")
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid})-[a:aboutObject]->"
            "(o:Object {id:$oid}) RETURN count(a) > 0",
            params={"eid": ev["eventId"], "oid": obj["id"]},
        ).result_set
        assert r[0][0] is True

    def test_backfill_about_subject_still_works(self, sdk):
        """backfill with Subject name still creates aboutSubject (pre-existing)."""
        sdk.create_subject("grace", "designer")
        sdk.create_point("statement", "grace finalized the mockups")
        result = sdk.backfill_about_entities()
        assert result["updated"] >= 1
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (p:Point)-[:aboutSubject]->(s:Subject {name:'grace'}) "
            "RETURN count(*) > 0"
        ).result_set
        assert r[0][0] is True


# ── proj.apply path: about* keys must NOT persist as node properties ─────

class TestProjApplyAboutMetaKeys:
    """#486: direct proj.apply consumers must not persist aboutEvent/
    aboutPoint/aboutDocument as node properties — _META_KEYS must cover
    all five about* edge types (only aboutSubject/aboutObject did).
    Edge wiring stays in the SDK create_event layer (pops + create_about_edge);
    the projection's contract is: never store about* keys as properties."""

    def test_apply_event_with_about_point_not_property(self, sdk):
        """EventRecorded event dict with aboutPoint → no aboutPoint property
        on the Event node (edge wiring is SDK-layer, create_event)."""
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-486-a",
            "event": {
                "eventId": "ev-486-a",
                "eventKind": "review",
                "subject": "agent",
                "object": "deploy",
                "startedAt": "2026-08-07T00:00:00Z",
                "aboutPoint": "pt-486-a",
            },
        })
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN exists(e.aboutPoint), e.aboutPoint",
            params={"eid": "ev-486-a"},
        ).result_set
        assert r[0][0] is False, "aboutPoint persisted as node property"

    def test_apply_event_with_about_document_not_property(self, sdk):
        """EventRecorded event dict with aboutDocument → no aboutDocument
        property on the Event node."""
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-486-b",
            "event": {
                "eventId": "ev-486-b",
                "eventKind": "review",
                "subject": "agent",
                "object": "doc",
                "startedAt": "2026-08-07T00:00:00Z",
                "aboutDocument": "doc-486-b",
            },
        })
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN exists(e.aboutDocument)",
            params={"eid": "ev-486-b"},
        ).result_set
        assert r[0][0] is False, "aboutDocument persisted as node property"

    def test_apply_event_with_about_event_not_property(self, sdk):
        """EventRecorded event dict with aboutEvent → no aboutEvent property
        on the Event node."""
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-486-c",
            "event": {
                "eventId": "ev-486-c",
                "eventKind": "review",
                "subject": "agent",
                "object": "target",
                "startedAt": "2026-08-07T00:00:00Z",
                "aboutEvent": "target-event-486",
            },
        })
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN exists(e.aboutEvent)",
            params={"eid": "ev-486-c"},
        ).result_set
        assert r[0][0] is False, "aboutEvent persisted as node property"

    def test_apply_event_with_about_subject_still_skipped(self, sdk):
        """Regression: aboutSubject/aboutObject still skipped (pre-existing)."""
        proj = sdk._get_proj()
        proj.apply({
            "type": "EventRecorded",
            "id": "ev-486-d",
            "event": {
                "eventId": "ev-486-d",
                "eventKind": "review",
                "subject": "agent",
                "object": "target",
                "startedAt": "2026-08-07T00:00:00Z",
                "aboutSubject": "subj-486-d",
            },
        })
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN exists(e.aboutSubject)",
            params={"eid": "ev-486-d"},
        ).result_set
        assert r[0][0] is False, "aboutSubject persisted as node property"

    def test_create_event_wires_about_point_still_works(self, sdk):
        """SDK create_event still wires aboutPoint edge (regression for #486)."""
        pt = sdk.create_point("statement", "deploy completed successfully")
        ev = sdk.create_event("deploy-486", "deployment",
                              aboutPoint=pt["id"])
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid})-[a:aboutPoint]->(p:Point {id:$pid}) "
            "RETURN count(a) > 0",
            params={"eid": ev["eventId"], "pid": pt["id"]},
        ).result_set
        assert r[0][0] is True
        # and no property pollution on the Event node
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN exists(e.aboutPoint)",
            params={"eid": ev["eventId"]},
        ).result_set
        assert r[0][0] is False, "aboutPoint property leaked onto Event node"
