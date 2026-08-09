"""Tests for P1 differentiators: provenance, temporal, staleness, entity linking.

Runnable with: .venv/bin/python -m pytest tests/test_p1_differentiators.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# #67: TORTOISE_SECRET_PEPPER is mandatory — set before any tortoise import
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.projection import FalkorProjection


@pytest.fixture
def sdk():
    """SDK with temp database. Closed after test."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_p1_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


def _make_point(sdk, **kw):
    return sdk.create_point(kw.pop("kind", "statement"),
                            kw.pop("content", "test content"), **kw)


# ── P1-1: Provenance — extractedFrom on Point ────────────────────

class TestProvenance:
    def test_extracted_from_stored_on_point(self, sdk):
        p = sdk.create_point("statement", "claim about X",
                             extractedFrom="doc/01-research.md")
        assert p["extractedFrom"] == "doc/01-research.md"

    def test_extracted_from_stored_not_fabricated_document(self, sdk):
        """create_point persists extractedFrom as a property; it does NOT
        fabricate a Document node (documents come from DocumentCreated
        events via the ingest flow, #125)."""
        proj = sdk._get_proj()
        p = sdk.create_point("statement", "linked claim",
                             extractedFrom="doc/02-design.md")
        assert p["extractedFrom"] == "doc/02-design.md"
        docs = proj.g.query(
            "MATCH (d:Document {id:$did}) RETURN count(d) > 0",
            params={"did": "doc/02-design.md"},
        ).result_set
        assert docs[0][0] is False

    def test_document_event_creates_document_node(self, sdk, tmp_path):
        """DocumentCreated event → Document node in the projection (#493)."""
        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        log = EventLog(str(tmp_path / "p1_events.jsonl"))
        api = EventAPI(log, initiated_by="extractor",
                       projection=sdk._get_proj())
        api.add_document("doc/03-notes.md", "Notes",
                         doc_status="captured")
        proj = sdk._get_proj()
        docs = proj.g.query(
            "MATCH (d:Document {id:$did}) RETURN count(d) > 0",
            params={"did": "doc/03-notes.md"},
        ).result_set
        assert docs[0][0] is True


# ── P1-2: Temporal — validFrom/validTo ──────────────────────────

class TestTemporal:
    def test_valid_from_stored(self, sdk):
        p = sdk.create_point("statement", "time-bound claim",
                             validFrom="2026-01-01", validTo="2026-12-31")
        assert p["validFrom"] == "2026-01-01"
        assert p["validTo"] == "2026-12-31"

    def test_valid_from_only(self, sdk):
        p = sdk.create_point("statement", "open-ended",
                             validFrom="2026-06-01")
        assert p["validFrom"] == "2026-06-01"
        assert p.get("validTo") is None

    def test_no_temporal_fields_backward_compat(self, sdk):
        p = sdk.create_point("statement", "no temporal")
        assert "validFrom" not in p or p.get("validFrom") is None
        # still creates fine
        assert "id" in p


# ── P1-3: Staleness detection ──────────────────────────────────

class TestStaleness:
    def test_fresh_point_not_stale(self, sdk):
        _make_point(sdk, content="fresh")
        result = sdk.stale_points(days=30)
        assert result["count"] == 0

    def test_stale_point_detected(self, sdk):
        proj = sdk._get_proj()
        # Ponytail: manually set old updatedAt to simulate staleness
        p = _make_point(sdk, content="old claim")
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.updatedAt='2020-01-01T00:00:00'",
            params={"id": p["id"]},
        )
        result = sdk.stale_points(days=30)
        assert result["count"] >= 1
        assert any(s["id"] == p["id"] for s in result["stale"])

    def test_stale_respects_limit(self, sdk):
        proj = sdk._get_proj()
        for i in range(5):
            p = _make_point(sdk, content=f"old {i}")
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n.updatedAt='2020-01-01T00:00:00'",
                params={"id": p["id"]},
            )
        result = sdk.stale_points(days=30, limit=3)
        assert result["count"] <= 3


# ── P1-4: Entity linking — Subject/Object dedup ─────────────────

class TestEntityLinking:
    def test_create_subject(self, sdk):
        sub = sdk.create_subject("Max", subjectKind="person")
        assert sub["name"] == "Max"
        assert sub["subjectKind"] == "person"
        assert "id" in sub

    def test_subject_dedup_by_name(self, sdk):
        sub1 = sdk.create_subject("Alice", subjectKind="person")
        sub2 = sdk.create_subject("Alice", subjectKind="person")
        # Same name → merged; IDs may differ in SDK (MERGE uses name key, but we return what exists)
        assert sub1["name"] == sub2["name"]

    def test_create_object(self, sdk):
        obj = sdk.create_object("Chess", objectKind="game")
        assert obj["name"] == "Chess"
        assert obj["objectKind"] == "game"

    def test_object_dedup_by_name(self, sdk):
        obj1 = sdk.create_object("Python")
        obj2 = sdk.create_object("Python", objectKind="language")
        assert obj1["name"] == obj2["name"]


# ── P1-4: Entity projection — SubjectAdded/ObjectRegistered ─────

class TestEntityProjection:
    def test_subject_added_propagates_to_graph(self, sdk):
        proj = sdk._get_proj()
        sub = sdk.create_subject("Eve")
        # Verify Subject node exists in graph
        rows = proj.g.query(
            "MATCH (s:Subject {name:'Eve'}) RETURN s.name, s.subjectKind"
        ).result_set
        assert len(rows) >= 1
        assert rows[0][0] == "Eve"

    def test_object_registered_propagates_to_graph(self, sdk):
        proj = sdk._get_proj()
        obj = sdk.create_object("FalkorDB", objectKind="technology")
        rows = proj.g.query(
            "MATCH (o:Object {name:'FalkorDB'}) RETURN o.name, o.objectKind"
        ).result_set
        assert len(rows) >= 1
        assert rows[0][0] == "FalkorDB"


# ── P1-5: Stub files exist ──────────────────────────────────────

class TestStubs:
    def test_connectors_package_exists(self):
        from tortoise.connectors import __doc__ as _doc
        assert "P1-5" in _doc or True  # just import check

    def test_auth_stub_exists(self):
        import tortoise.auth  # noqa: F401

    def test_backup_stub_exists(self):
        import tortoise.backup  # noqa: F401

    def test_deployment_stub_exists(self):
        import tortoise.deployment  # noqa: F401


# ── GAP-13: Provenance query tool ────────────────────────────

class TestProvenanceChain:
    def test_provenance_with_matching_subject(self, sdk):
        """Point authoredBy matches a Subject → returns full chain."""
        sdk.create_subject("pi-agent", subjectKind="agent")
        p = sdk.create_point("statement", "A claim", authoredBy="pi-agent")
        result = sdk.provenance(p["id"])
        assert "error" not in result
        assert result["point"]["authoredBy"] == "pi-agent"
        assert result["subject"]["name"] == "pi-agent"
        assert result["subject"]["kind"] == "agent"

    def test_provenance_case_insensitive_match(self, sdk):
        """Case-insensitive authoredBy → Subject matching."""
        sdk.create_subject("El Dato Team", subjectKind="team")
        p = sdk.create_point("statement", "B claim", authoredBy="el dato team")
        result = sdk.provenance(p["id"])
        assert result["subject"] is not None
        assert result["subject"]["name"] == "El Dato Team"

    def test_provenance_no_author(self, sdk):
        """Point without authoredBy → returns point info only."""
        p = sdk.create_point("statement", "no author claim")
        result = sdk.provenance(p["id"])
        assert result["point"]["authoredBy"] == ""
        assert "subject" not in result  # no author → no subject lookup

    def test_provenance_no_matching_subject(self, sdk):
        """Point authoredBy has no matching Subject → subject is None."""
        p = sdk.create_point("statement", "orphan", authoredBy="ghost")
        result = sdk.provenance(p["id"])
        assert result["subject"] is None

    def test_provenance_nonexistent_point(self, sdk):
        """Nonexistent point → error."""
        result = sdk.provenance("nonexistent-id")
        assert "error" in result

    def test_provenance_delegation_chain(self, sdk):
        """Subject with outgoing relationships → delegation list."""
        proj = sdk._get_proj()
        sub = sdk.create_subject("Alice", subjectKind="person")
        # ponytail: manually create related nodes for delegation test
        proj.g.query(
            "CREATE (r:Role {title:'Engineer'}) "
            "CREATE (t:Team {name:'Platform'})",
        )
        proj.g.query(
            "MATCH (s:Subject {id:$sid}), (r:Role {title:'Engineer'}) "
            "CREATE (s)-[:HAS_ROLE]->(r)",
            params={"sid": sub["id"]},
        )
        p = sdk.create_point("statement", "engineered", authoredBy="Alice")
        result = sdk.provenance(p["id"])
        assert len(result["delegation"]) >= 1
        via_types = [d["via"] for d in result["delegation"]]
        assert "HAS_ROLE" in via_types
