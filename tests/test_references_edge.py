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
    """label 'Document' is a deprecated alias for 'Source' (D10)."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Source {url:'doc4.txt', sourceKind:'document', title:'doc4.txt', contentHash:'', ingestedAt:'2024-01-01'})")
        # D10: a document is a :Source keyed url = doc id; the deprecated
        # "Document" alias resolves to Source and matches the entity by url.
        proj.g.query("CREATE (d:Source {url:'doc-1', title:'Some Doc', documentKind:'report'})")

        sdk.link_source_to_entity("doc4.txt", "doc-1", "Document")

        r = proj.g.query(
            "MATCH (s:Source {url:'doc4.txt'})-[:references]->(d:Source {url:'doc-1'}) "
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
    """Inline backfill logic (mirrors backfill_references.py) for testability.

    Loop SHAPE only. The D10 ``aboutDocument`` row is deliberately NOT re-typed
    here: it stopped being a copy of the script the moment the script's target
    moved to ``:Source`` addressed by ``url``, and re-typing it (it still said
    ``Document``, the retired label) is what let the script's own query building
    break unseen. The script's real execution is covered by
    ``tests/test_backfill_references_5141.py``.
    """
    about_edges = {
        "aboutSubject": "Subject",
        "aboutObject": "Object",
        "aboutEvent": "Event",
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
        r2 = _run_backfill_on_proj(proj)  # noqa: F841
        # After first run, all Sources with extractable entities have references.
        # sources_processed may be lower on second run since Sources that gained
        # references on run 1 are now filtered out.
        refs_after = proj.g.query(  # noqa: F841
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


# ── provenance chain via ingest path (#205) ────────────────────────────────


def test_link_source_to_entity_source_kind_passthrough():
    """#205: source_kind param is persisted on auto-created Source node."""
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()

        # Create document Source + references edge with custom source_kind
        # (D10: the document is a :Source keyed url = doc id).
        proj.g.query(
            "CREATE (d:Source {url:'doc-k', title:'Kind Test', documentKind:'report'})"
        )
        sdk.link_source_to_entity("src-kind.txt", "doc-k", "Document",
                                   source_kind="github_issue")

        # Verify the Source node was created with the right sourceKind
        r = proj.g.query(
            "MATCH (s:Source {url:'src-kind.txt'}) RETURN s.sourceKind"
        ).result_set
        assert r and r[0][0] == "github_issue", \
            f"Expected sourceKind='github_issue', got {r}"

        # Verify the references edge exists
        r2 = proj.g.query(
            "MATCH (s:Source {url:'src-kind.txt'})-[:references]->"
            "(d:Source {url:'doc-k'}) RETURN count(*) > 0"
        ).result_set
        assert r2[0][0] is True
    finally:
        sdk.close()


# ── #5151: the references self-loop guard, at the choke point ──────────────
#
# Before D10 the two sides of a `references` edge were different LABELS
# (`:Source -[:references]-> :Document`), so equal keys could not degenerate.
# D10 makes a document a `:Source`, so `link_source_to_entity("X", "X",
# "Source")` MERGEd `(:Source {url:'X'})-[:references]->(:Source {url:'X'})` —
# measured on an isolated graph: 1 node, 1 self-loop. The guard now lives in
# the writer, not in the caller: `_upsert_document`'s `if ref and ref != did`
# compares RAW spellings, which `resolve_source_key`'s URL canonicalisation
# defeats (`…/a/?utm_source=x` vs `…/a`), and a caller-side rule cannot bind
# the next Source→Source writer at all.

def _self_loop_count(proj) -> int:
    """Count `references` edges whose endpoints are the SAME node."""
    rows = proj.g.query(
        "MATCH (s:Source)-[r:references]->(e:Source) WHERE ID(s) = ID(e) "
        "RETURN count(r)"
    ).result_set
    return int(rows[0][0]) if rows and rows[0] else 0


def test_link_source_to_entity_writes_no_self_loop_5151():
    """#5151: equal source/entity keys on a ``Source`` target mint NO edge.

    FAIL-ON: this is the issue's own reproduction. Without the guard the call
    MERGEs a `(:Source {url:'X'})-[:references]->(:Source {url:'X'})` self-loop,
    which asserts nothing (ONTOLOGY §3.4's `references` is source→entity
    containment/derivation) and is exactly the degenerate shape D10's
    suppression exists to prevent. The NODE is still created — only the EDGE is
    degenerate, so suppressing the whole write would be the #5140 failure in the
    other direction (a dropped edge where one was expected is how the legacy
    no-`source_url` path broke).
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        sdk.link_source_to_entity("X", "X", "Source")
        proj = sdk._get_proj()

        nodes = proj.g.query(
            "MATCH (s:Source {url:'X'}) RETURN count(s)"
        ).result_set[0][0]
        assert nodes == 1, f"the Source node must still exist, got {nodes}"
        assert _self_loop_count(proj) == 0, "degenerate self-loop references edge minted"

        # Idempotent: a re-link must not mint it on the second pass either.
        sdk.link_source_to_entity("X", "X", "Source")
        assert _self_loop_count(proj) == 0, (
            "the second call minted the self-loop the first one suppressed"
        )
    finally:
        sdk.close()


def test_link_source_to_entity_writes_no_self_loop_through_the_Document_alias_5151():
    """#5151: the DEPRECATED ``"Document"`` alias is the same degenerate case.

    The alias is remapped to ``Source`` for identity BEFORE the edge is written,
    so the guard has to sit after that remap: keyed on the caller's spelling it
    would let the derivation branch (which also stamps the #5199
    ``sourceVersion`` anchor) mint the self-loop.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        sdk.link_source_to_entity("X", "X", "Document")
        proj = sdk._get_proj()
        assert _self_loop_count(proj) == 0, (
            "the `Document` alias minted a Source→Source self-loop"
        )
    finally:
        sdk.close()


def test_the_self_loop_guard_sees_the_RESOLVED_key_not_the_raw_spelling_5151():
    """#5151: a URL variant defeats a caller-side ``ref != did`` guard.

    This is why the guard is at the choke point. `resolve_source_key`
    canonicalises (`…/a/?utm_source=x` → `…/a`), so the two spellings DIFFER
    while the resolved key EQUALS the entity id — `_upsert_document`'s
    `if ref and ref != did:` passes the call straight through, and the writer
    then MERGEs a self-loop. Measured pre-fix on this exact shape: 1 node,
    1 self-loop.
    """
    from tortoise.source_identity import resolve_source_key

    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (s:Source {url:'https://example.test/a', title:'a', "
            "canonicalUrl:'https://example.test/a', contentHash:'h1', "
            "sourceKind:'document'})"
        )
        variant = "https://example.test/a/?utm_source=x"
        # NON-VACUITY: the caller-side test really is defeated here — the raw
        # spellings differ while the resolved key is the entity id.
        assert variant != "https://example.test/a"
        assert resolve_source_key(proj.g, variant) == "https://example.test/a", (
            "the variant no longer resolves onto the entity node, so this test no "
            "longer reproduces the case the caller-side guard cannot catch"
        )

        sdk.link_source_to_entity(variant, "https://example.test/a", "Source")
        assert _self_loop_count(proj) == 0, (
            "a URL variant collapsed onto the entity node and minted a self-loop"
        )
        nodes = proj.g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0]
        assert nodes == 1, f"the variant minted a duplicate Source, got {nodes}"
    finally:
        sdk.close()


def test_the_self_loop_guard_does_not_suppress_legitimate_links_5151():
    """#5151 FAIL-ON for OVER-suppression — the guard's other direction.

    Two distinct Source nodes are the ``references`` containment link (§3.4's
    referential chain), and an ``Event``/``Object`` target is matched on
    ``{id}`` under a DIFFERENT label, so an equal id STRING addresses a distinct
    node. A guard written on "same label" or on the raw string pair instead of
    node identity would silently drop both — the #5140 failure shape (a guard at
    the wrong layer losing the edge it was meant to protect).
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("CREATE (a:Source {url:'a'}),(b:Source {url:'b'})")
        proj.g.query("CREATE (o:Object {id:'X', name:'o'})")

        sdk.link_source_to_entity("a", "b", "Source")   # containment
        sdk.link_source_to_entity("X", "X", "Object")   # equal string, other label

        containment = proj.g.query(
            "MATCH (a:Source {url:'a'})-[:references]->(b:Source {url:'b'}) "
            "RETURN count(*)"
        ).result_set[0][0]
        assert containment == 1, (
            f"the guard suppressed a legitimate Source→Source link ({containment})"
        )
        to_object = proj.g.query(
            "MATCH (:Source {url:'X'})-[:references]->(o:Object {id:'X'}) "
            "RETURN count(*)"
        ).result_set[0][0]
        assert to_object == 1, (
            "the guard suppressed a legitimate link to an `Object` that merely "
            f"shares the id string ({to_object})"
        )
        assert _self_loop_count(proj) == 0
    finally:
        sdk.close()
