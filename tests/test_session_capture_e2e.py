"""#125 end-to-end session capture smoke — Document+Event+edges, search, idempotency.

Requires TORTOISE_DB_URI pointing at a test-prefixed FalkorDB graph.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK
from tortoise.projection import FalkorProjection
import tortoise.search_engine as se


URI = os.environ.get("TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise_test_e2e125")


def _proj():
    proj = FalkorProjection.from_uri(URI)
    proj.g.query("MATCH (n) DETACH DELETE n")  # test-prefixed — safe
    proj._ensure_indexes()
    return proj


def test_capture_e2e():
    """Full #125 path: capture → Document+Event+edges → searchable → idempotent."""
    proj = _proj()
    sdk = TortoiseSDK()
    sdk._proj = proj
    try:
        # 1. Subject (agent) pre-exists (real flow: via performs edge)
        proj.apply({"type": "SubjectAdded", "id": "pi-agent", "name": "pi-agent",
                    "subject_kind": "other"})
        # 2. Capture: Document with topics/summary + about_entities
        proj.apply({"type": "DocumentCreated", "id": "doc-e2e",
                    "title": "Licensing Talk", "document_kind": "transcript",
                    "topics": ["licensing", "AGPL"], "summary": "Compared licenses",
                    "session_id": "s-e2e", "event_id": "evt-e2e",
                    "doc_status": "captured", "about_entities": ["pi-agent"]})
        # 3. sessionCaptured Event + produces→Document + uses→Skill
        proj.apply({"type": "EventRecorded", "event": {
            "id": "evt-e2e", "eventKind": "sessionCaptured", "subject": "pi-agent",
            "object": "doc-e2e", "objectType": "Document",
            "uses": [{"name": "tortoise-capture", "kind": "skill"}]}})
        # Assert edges
        prod = proj.g.query(
            "MATCH (e:Event {eventId:'evt-e2e'})-[:produces]->(d:Document {id:'doc-e2e'}) RETURN count(e)"
        ).result_set
        assert prod[0][0] >= 1
        uses = proj.g.query(
            "MATCH (e:Event {eventId:'evt-e2e'})-[:uses]->(o:Object {objectKind:'skill'}) RETURN o.name"
        ).result_set
        assert uses and uses[0][0] == "tortoise-capture", uses
        about = proj.g.query(
            "MATCH (d:Document {id:'doc-e2e'})-[:aboutSubject]->(s) RETURN s.name"
        ).result_set
        assert about and about[0][0] == "pi-agent", about
        # 4. Search returns the session by topic
        hits = se.run_fts_query(proj.g, "licensing", entity_type="document")
        assert any(h[0] == "doc-e2e" for h in hits), f"FTS miss: {hits}"
        # 5. Idempotency: re-capture same doc_id → MERGE, no dup
        proj.apply({"type": "DocumentCreated", "id": "doc-e2e",
                    "title": "Licensing Talk", "document_kind": "transcript",
                    "topics": ["licensing", "AGPL"], "summary": "Compared licenses",
                    "session_id": "s-e2e", "event_id": "evt-e2e",
                    "doc_status": "captured"})
        cnt = proj.g.query("MATCH (d:Document {id:'doc-e2e'}) RETURN count(d)").result_set
        assert cnt[0][0] == 1, f"duplicate Document: {cnt}"
        # 6. Zero Points (metadata-only)
        pts = proj.g.query("MATCH (p:Point) RETURN count(p)").result_set
        assert pts[0][0] == 0, f"Points leaked: {pts[0][0]}"
    finally:
        proj.close()
