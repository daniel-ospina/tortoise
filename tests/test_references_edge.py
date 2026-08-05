"""Tests for Source → Entity references edge (Ontology v3.0 §3.2-3.3).

Covers:
  - link_source_to_entity (FalkorProjection + TortoiseSDK facade)
  - Action rejection (dissolved v3.0)
  - Backfill idempotency (MERGE semantics)
  - get_provenance_chain completeness (previously phantom query)

All tests use TortoiseSDK (not direct FalkorProjection) because
conftest.py sets TORTOISE_DB_URI for the Docker FalkorDB container;
falkordb-lite embedded mode is not available in this environment.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_test_"), name)


# ── helpers ────────────────────────────────────────────────────────────────

def _ensure_nodes(sdk: TortoiseSDK) -> None:
    """Create Source + Object nodes directly via projection graph."""
    proj = sdk._get_proj()
    # Source
    r = proj.g.query("MATCH (s:Source {url:'doc.txt'}) RETURN count(s)").result_set
    if r[0][0] == 0:
        proj.g.query(
            "CREATE (s:Source {url:'doc.txt', sourceKind:'document', "
            "title:'doc.txt', contentHash:'', ingestedAt:'2024-01-01'})"
        )
    # Object
    r = proj.g.query("MATCH (o:Object {id:'obj-1'}) RETURN count(o)").result_set
    if r[0][0] == 0:
        proj.g.query(
            "CREATE (o:Object {id:'obj-1', name:'Widget', objectKind:'product'})"
        )


# ── link_source_to_entity ──────────────────────────────────────────────────

def test_link_source_to_entity():
    """Create Source + Object, link, verify (s)-[:references]->(o) exists."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        _ensure_nodes(sdk)
        sdk.link_source_to_entity("doc.txt", "obj-1", "Object")

        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (s:Source {url:'doc.txt'})-[:references]->(o:Object {id:'obj-1'}) "
            "RETURN count(*) > 0"
        ).result_set
        assert r[0][0] is True
    finally:
        sdk.close()


def test_link_source_to_entity_rejects_action():
    """label 'Action' raises ValueError (Action dissolved in v3.0)."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Source {url:'doc2.txt', sourceKind:'document', title:'doc2.txt', contentHash:'', ingestedAt:'2024-01-01'})")
        proj.g.query("CREATE (a:Action {id:'act-1', name:'Foo', actionKind:'test'})")

        with pytest.raises(ValueError, match="Invalid entity_label: Action"):
            sdk.link_source_to_entity("doc2.txt", "act-1", "Action")
    finally:
        sdk.close()


def test_link_source_to_entity_rejects_invalid_label():
    """Random label raises ValueError."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Source {url:'doc3.txt', sourceKind:'document', title:'doc3.txt', contentHash:'', ingestedAt:'2024-01-01'})")
        proj.g.query("CREATE (o:Object {id:'obj-2', name:'Gadget', objectKind:'product'})")

        with pytest.raises(ValueError, match="Invalid entity_label: Subject"):
            sdk.link_source_to_entity("doc3.txt", "obj-2", "Subject")
    finally:
        sdk.close()


def test_link_source_to_entity_document_label():
    """label 'Document' is valid."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Source {url:'doc4.txt', sourceKind:'document', title:'doc4.txt', contentHash:'', ingestedAt:'2024-01-01'})")
        proj.g.query("CREATE (d:Document {id:'doc-1', title:'Some Doc', documentKind:'report'})")

        sdk.link_source_to_entity("doc4.txt", "doc-1", "Document")

        r = proj.g.query(
            "MATCH (s:Source {url:'doc4.txt'})-[:references]->(d:Document {id:'doc-1'}) "
            "RETURN count(*) > 0"
        ).result_set
        assert r[0][0] is True
    finally:
        sdk.close()


def test_link_source_to_entity_event_label():
    """label 'Event' is valid."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Source {url:'doc5.txt', sourceKind:'document', title:'doc5.txt', contentHash:'', ingestedAt:'2024-01-01'})")
        proj.g.query("CREATE (ev:Event {id:'ev-1', eventId:'ev-1', name:'Meeting', eventKind:'meeting'})")

        sdk.link_source_to_entity("doc5.txt", "ev-1", "Event")

        r = proj.g.query(
            "MATCH (s:Source {url:'doc5.txt'})-[:references]->(e:Event {id:'ev-1'}) "
            "RETURN count(*) > 0"
        ).result_set
        assert r[0][0] is True
    finally:
        sdk.close()


# ── SDK facade ─────────────────────────────────────────────────────────────

def test_sdk_link_source_to_entity():
    """SDK.link_source_to_entity delegates to FalkorProjection."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Source {url:'sdk_doc.txt', sourceKind:'document', title:'sdk_doc.txt', contentHash:'', ingestedAt:'2024-01-01'})")
        proj.g.query("CREATE (o:Object {id:'sdk-obj', name:'SDK Widget', objectKind:'product'})")

        sdk.link_source_to_entity("sdk_doc.txt", "sdk-obj", "Object")

        r = proj.g.query(
            "MATCH (s:Source {url:'sdk_doc.txt'})-[:references]->(o:Object {id:'sdk-obj'}) "
            "RETURN count(*) > 0"
        ).result_set
        assert r[0][0] is True
    finally:
        sdk.close()


# ── backfill idempotency ───────────────────────────────────────────────────

def _run_backfill_on_proj(proj, limit: int = 0) -> dict:
    """Inline backfill logic (mirrors backfill_references.py) for testability."""
    about_edges = {
        "aboutSubject": "Subject",
        "aboutObject": "Object",
        "aboutEvent": "Event",
        "aboutDocument": "Document",
    }
    sources = proj.g.query(
        "MATCH (s:Source) "
        "OPTIONAL MATCH (s)-[r:references]->() "
        "WITH s, count(r) AS ref_count WHERE ref_count = 0 "
        "RETURN s.url AS url"
    ).result_set

    stats = {"sources_processed": 0, "references_created": 0, "sources_without_entity": 0}

    for (source_url,) in sources:
        stats["sources_processed"] += 1
        found_entity = False
        points = proj.g.query(
            "MATCH (p:Point)-[:extractedFrom]->(s:Source {url:$url}) RETURN p.id",
            params={"url": source_url},
        ).result_set

        for (pid,) in points:
            for about_edge, label in about_edges.items():
                entities = proj.g.query(
                    f"MATCH (p:Point {{id:$pid}})-[:{about_edge}]->(e:{label}) RETURN e.id, labels(e)",
                    params={"pid": pid},
                ).result_set
                for (eid, _labels) in entities:
                    found_entity = True
                    proj.g.query(
                        f"MATCH (s:Source {{url:$url}}), (e:{label} {{id:$eid}}) MERGE (s)-[:references]->(e)",
                        params={"url": source_url, "eid": eid},
                    )
                    stats["references_created"] += 1

        if not found_entity:
            stats["sources_without_entity"] += 1

    return stats


def test_backfill_references_idempotent():
    """Run backfill twice — same result (MERGE semantics)."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()

        # Setup: Source → Point → about* → Subject
        proj.g.query("CREATE (s:Source {url:'bf_doc.txt', sourceKind:'document', title:'bf_doc.txt', contentHash:'', ingestedAt:'2024-01-01'})")
        proj.g.query("CREATE (p:Point {id:'bf-pt', content:'some claim', context:'test', is_operator:false})")
        proj.g.query("CREATE (subj:Subject {id:'bf-subj', name:'Test Subject', subjectKind:'other'})")
        # Wire: Point extractedFrom Source
        proj.g.query(
            "MATCH (p:Point {id:'bf-pt'}), (s:Source {url:'bf_doc.txt'}) MERGE (p)-[:extractedFrom]->(s)"
        )
        # Wire: Point aboutSubject Subject
        proj.g.query(
            "MATCH (p:Point {id:'bf-pt'}), (subj:Subject {id:'bf-subj'}) MERGE (p)-[:aboutSubject]->(subj)"
        )

        # Run backfill
        r1 = _run_backfill_on_proj(proj)
        assert r1["sources_processed"] >= 1
        assert r1["references_created"] >= 1
        # Note: sources_without_entity may be > 0 from other tests in the
        # same session (Sources without Points/about* edges)

        # Verify edge exists
        check = proj.g.query(
            "MATCH (s:Source {url:'bf_doc.txt'})-[:references]->(subj:Subject {id:'bf-subj'}) "
            "RETURN count(*) > 0"
        ).result_set
        assert check[0][0] is True

        # Run again — should find no new work (all Sources already have references)
        # This is the idempotent MERGE check: re-running doesn't create duplicates
        r2 = _run_backfill_on_proj(proj)
        # After first run, all Sources with extractable entities have references.
        # sources_processed may be lower on second run since Sources that gained
        # references on run 1 are now filtered out.
        refs_after = proj.g.query(
            "MATCH ()-[r:references]->() RETURN count(r)"
        ).result_set[0][0]
        # The bf_doc→bf-subj reference should still be exactly 1 (no duplicate)
        refs_to_subj = proj.g.query(
            "MATCH (s:Source {url:'bf_doc.txt'})-[:references]->(subj:Subject {id:'bf-subj'}) "
            "RETURN count(*)"
        ).result_set[0][0]
        assert refs_to_subj == 1, f"Expected 1 reference, got {refs_to_subj}"
    finally:
        sdk.close()


# ── get_provenance_chain completeness ──────────────────────────────────────

def test_get_provenance_chain_complete():
    """Point → extractedFrom → Source → references → entity returns data (not phantom)."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()

        # Build the full chain
        proj.g.query("CREATE (s:Source {url:'prov_doc.txt', sourceKind:'document', title:'Prov Doc', contentHash:'abc', ingestedAt:'2024-01-01'})")
        proj.g.query("CREATE (o:Object {id:'prov-obj', name:'Prov Widget', objectKind:'product'})")
        proj.g.query("CREATE (p:Point {id:'prov-pt', content:'A claim about Widget', context:'test', is_operator:false})")
        # Wire: Point extractedFrom Source
        proj.g.query(
            "MATCH (p:Point {id:'prov-pt'}), (s:Source {url:'prov_doc.txt'}) MERGE (p)-[:extractedFrom]->(s)"
        )
        # Wire: Source references Object
        proj.g.query(
            "MATCH (s:Source {url:'prov_doc.txt'}), (o:Object {id:'prov-obj'}) MERGE (s)-[:references]->(o)"
        )

        # Now query the provenance chain
        chain = sdk.get_provenance_chain("prov-pt")
        assert len(chain) == 1, f"Expected 1 result, got {len(chain)}"
        result = chain[0]
        assert result["source"]["url"] == "prov_doc.txt"
        assert result["entity"]["name"] == "Prov Widget"
        assert "Object" in result["labels"]
    finally:
        sdk.close()
