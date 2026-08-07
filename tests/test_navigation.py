"""Verification tests for tortoise/navigation.py — entity profile, traversal, entity-anchored dispatch.
Run: python3 tests/test_navigation.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from tortoise.navigation import entityProfile, tortoise_traverse
from tortoise.memory_orchestrator import (
    _entityAnchoredCypher,
    crossOntologyQuery,
    dispatch,
    routeRead,
    translateNL,
)


# ── Mock helpers ──────────────────────────────────────

def _mock_db(graph_data: dict[str, list[list]]):
    """Create mock FalkorDB db. graph_data maps graph_name → result_set sequences.

    Each key maps to a list of result_sets that are returned in order
    for successive .query() calls.
    """
    db = MagicMock()

    def _select_graph(name):
        g = MagicMock()
        sequences = list(graph_data.get(name, []))  # copy for mutable state

        def _query(cypher: str, **kwargs):
            result = MagicMock()
            if sequences:
                result.result_set = sequences.pop(0)
            else:
                result.result_set = []
            return result

        g.query = _query
        return g

    db.select_graph = _select_graph
    return db


def _node(id_, labels, props):
    """FalkorDB Node class for tests (no FalkorDB import needed)."""
    class FakeNode:
        def __init__(self):
            self.id = id_
            self.labels = labels
            self.properties = props
    return FakeNode()


def _root_row(label, node):
    """Convert a FakeNode into a _resolve_root result row [label, props].

    The navigation root lookup now returns (label, properties) per branch
    (issue #327) instead of the raw node, so mock result sets must provide
    the new row shape.
    """
    props = dict(node.properties)
    if "id" not in props:
        props["id"] = str(node.id)
    return [label, props]


# ── entityProfile tests ───────────────────────────────

def test_entity_profile_empty_entity():
    """No matching entity → returns empty entity and empty connected."""
    db = _mock_db({"tortoise": [[]]})  # root query returns nothing
    result = entityProfile(db, "tortoise", "no-such-id")
    assert result["entity"] == {}
    assert result["connected"]["points"] == []
    assert result["connected"]["documents"] == []
    print("✓ entityProfile empty entity")


def test_entity_profile_single_hop():
    """One hop from entity finds connected points and documents."""
    root = _node("root-1", ["Point"], {"content": "root point", "pointKind": "claim"})
    child_p = _node("pt-2", ["Point"], {"content": "connected point", "confidence": 0.8})
    child_d = _node("doc-1", ["Document"], {"title": "connected doc"})

    # Query 0: root lookup. Query 1: BFS from root-1 (one child per direction)
    db = _mock_db({
        "tortoise": [
            [_root_row("Point", root)],                     # root lookup
            [[child_p, "IMPL"]],  # BFS from root-1 (outgoing)
        ],
    })
    result = entityProfile(db, "tortoise", "root-1", hops=1)
    assert result["entity"]["id"] == "root-1"
    assert result["entity"]["content"] == "root point"
    assert len(result["connected"]["points"]) == 1
    assert result["connected"]["points"][0]["id"] == "pt-2"
    assert result["connected"]["points"][0]["_relationship"] == "IMPL"
    print("✓ entityProfile single hop")


def test_entity_profile_multi_hop():
    """Two hops: root → child → grandchild."""
    root = _node("root-1", ["Point"], {"content": "root"})
    child_a = _node("pt-a", ["Point"], {"content": "child a"})
    child_b = _node("pt-b", ["Point"], {"content": "child b"})
    grandchild = _node("pt-c", ["Point"], {"content": "grandchild"})

    db = _mock_db({
        "tortoise": [
            [_root_row("Point", root)],              # root lookup
            [[child_a, "IMPL"], [child_b, "NAND"]],  # hop 1 from root-1
            [[grandchild, "IMPL"]],                # hop 1 from pt-a
            [],                                     # hop 1 from pt-b
        ],
    })
    result = entityProfile(db, "tortoise", "root-1", hops=2)
    assert result["entity"]["id"] == "root-1"
    points = result["connected"]["points"]
    ids = {p["id"] for p in points}
    assert ids == {"pt-a", "pt-b", "pt-c"}
    print("✓ entityProfile multi-hop (2 hops)")


def test_entity_profile_filter_pointKind():
    """pointKind filter excludes non-matching nodes."""
    root = _node("root-1", ["Point"], {"content": "root"})
    claim = _node("pt-1", ["Point"], {"content": "claim", "pointKind": "claim"})
    decision = _node("pt-2", ["Point"], {"content": "decision", "pointKind": "decision"})

    db = _mock_db({
        "tortoise": [
            [_root_row("Point", root)],
            [[claim, "IMPL"], [decision, "IMPL"]],
        ],
    })
    result = entityProfile(db, "tortoise", "root-1", hops=1, pointKind="claim")
    assert len(result["connected"]["points"]) == 1
    assert result["connected"]["points"][0]["id"] == "pt-1"
    print("✓ entityProfile filter pointKind")


def test_entity_profile_categorize_types():
    """Connected nodes of different labels get categorized."""
    root = _node("root-1", ["Point"], {"content": "root"})
    evt = _node("evt-1", ["Event"], {"eventKind": "meeting"})
    doc = _node("doc-1", ["Document"], {"title": "doc"})
    subj = _node("sub-1", ["Subject"], {"name": "Alice"})

    db = _mock_db({
        "tortoise": [
            [_root_row("Point", root)],
            [[evt, "childEvents"], [doc, "extractedFrom"], [subj, "aboutEntities"]],
        ],
    })
    result = entityProfile(db, "tortoise", "root-1", hops=1)
    assert len(result["connected"]["events"]) == 1
    assert len(result["connected"]["documents"]) == 1
    assert len(result["connected"]["subjects"]) == 1
    assert result["connected"]["events"][0]["id"] == "evt-1"
    print("✓ entityProfile categorize by type")


# ── tortoise_traverse tests ───────────────────────────

def test_tortoise_traverse_basic():
    """Multi-hop traversal returns nodes with relationship + depth."""
    root = _node("root-1", ["Point"], {"content": "root"})
    child = _node("pt-1", ["Point"], {"content": "child"})

    db = _mock_db({
        "tortoise": [
            [_root_row("Point", root)],
            [[child, "IMPL"]],
            [],  # no further hops from child
        ],
    })
    result = tortoise_traverse(db, "tortoise", "root-1", max_hops=2)
    assert result["entity"]["id"] == "root-1"
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["node"]["id"] == "pt-1"
    assert result["nodes"][0]["relationship"] == "IMPL"
    assert result["nodes"][0]["depth"] == 1
    print("✓ tortoise_traverse basic")


def test_tortoise_traverse_diamond():
    """Diamond pattern: root → a,b → c (c visited once)."""
    root = _node("root", ["Point"], {"content": "root"})
    a = _node("a", ["Point"], {"content": "a"})
    b = _node("b", ["Point"], {"content": "b"})
    c = _node("c", ["Point"], {"content": "c"})

    db = _mock_db({
        "tortoise": [
            [_root_row("Point", root)],
            [[a, "IMPL"], [b, "NAND"]],  # hop 1: root→a, root→b
            [[c, "IMPL"]],                # hop 1 from a: a→c
            [[c, "INPUT"]],               # hop 1 from b: b→c (same c, visited already)
        ],
    })
    result = tortoise_traverse(db, "tortoise", "root", max_hops=2)
    assert result["entity"]["id"] == "root"
    ids = {n["node"]["id"] for n in result["nodes"]}
    assert ids == {"a", "b", "c"}  # c only once
    print("✓ tortoise_traverse diamond (dedup)")


def test_parse_node_prefers_public_id_over_internal():
    """Regression: #44 — _parse_node must return the public id property,
    not the internal FalkorDB numeric node ID.

    Internal id(n) = 2189, public id property = 01KXR94MK1B3FEF2ASKF279KST.
    Downstream tools fail when the internal ID leaks (they query by public id).
    """
    from tortoise.navigation import _parse_node

    # Simulate a real FalkorDB node: properties.id = public ULID, node.id = internal numeric
    public_id = "01KXR94MK1B3FEF2ASKF279KST"
    internal_id = 2189

    class FakeFalkorNode:
        def __init__(self):
            self.id = internal_id
            self.labels = ["Point"]
            self.properties = {
                "id": public_id,
                "content": "some claim",
                "pointKind": "claim",
            }

    parsed = _parse_node(FakeFalkorNode())
    assert parsed["id"] == public_id, (
        f"Expected public id {public_id}, got {parsed['id']} (internal leak)"
    )
    assert parsed["id"] != str(internal_id), (
        f"Internal ID {internal_id} leaked into parsed id"
    )
    assert parsed["content"] == "some claim"
    assert parsed["type"] == "Point"
    print("✓ _parse_node prefers public id over internal")


def test_tortoise_traverse_returns_public_ids():
    """Regression: #44 — traverse output must use public ids from properties,
    never internal FalkorDB node ids.
    """
    # Build mock nodes where properties.id differs from the "internal" node.id
    root = _node("2189", ["Point"], {
        "id": "01KXR94MK1B3FEF2ASKF279KST",
        "content": "root point",
    })
    a = _node("9999", ["Point"], {
        "id": "01J0ABC123DEF456789GHIJK",
        "content": "child a (operator)",
    })
    b = _node("9998", ["Point"], {
        "id": "01J0LMN456OPQ789RSTUVWX",
        "content": "child b",
    })

    db = _mock_db({
        "tortoise": [
            [_root_row("Point", root)],
            [[a, "IMPL"], [b, "NAND"]],
            [],  # no further from a
            [],  # no further from b
        ],
    })
    result = tortoise_traverse(db, "tortoise", "01KXR94MK1B3FEF2ASKF279KST", max_hops=2)

    # Entity must have the public id
    assert result["entity"]["id"] == "01KXR94MK1B3FEF2ASKF279KST", (
        f"entity.id leaked internal ID: {result['entity']['id']}"
    )
    assert result["entity"]["id"] != "2189"

    # All nodes must have public ids
    node_ids = {n["node"]["id"] for n in result["nodes"]}
    expected = {"01J0ABC123DEF456789GHIJK", "01J0LMN456OPQ789RSTUVWX"}
    assert node_ids == expected, f"node ids: {node_ids}"

    # No numeric internal IDs anywhere
    all_ids = [result["entity"]["id"]] + [n["node"]["id"] for n in result["nodes"]]
    for i, rid in enumerate(all_ids):
        assert not rid.isdigit(), f"Numeric internal ID leak at position {i}: {rid}"

    print("✓ tortoise_traverse returns public IDs only")


def test_entity_profile_returns_public_ids():
    """Regression: #44 — entityProfile must also use public ids."""
    root = _node("2189", ["Point"], {
        "id": "01KXR94MK1B3FEF2ASKF279KST",
        "content": "root point",
    })
    child = _node("9999", ["Point"], {
        "id": "01J0ABC123DEF456789GHIJK",
        "content": "connected point",
        "confidence": 0.8,
    })

    db = _mock_db({
        "tortoise": [
            [_root_row("Point", root)],
            [[child, "IMPL"]],
        ],
    })
    result = entityProfile(db, "tortoise", "01KXR94MK1B3FEF2ASKF279KST", hops=1)

    assert result["entity"]["id"] == "01KXR94MK1B3FEF2ASKF279KST", (
        f"entity.id leaked: {result['entity']['id']}"
    )
    assert result["connected"]["points"][0]["id"] == "01J0ABC123DEF456789GHIJK", (
        f"connected point id leaked: {result['connected']['points'][0]['id']}"
    )

    print("✓ entityProfile returns public IDs only")


# ── Entity-anchored dispatch tests ────────────────────

def test_entity_anchored_cypher_injection():
    """_entityAnchoredCypher injects entityId filter into each ontology's Cypher."""
    cypher = _entityAnchoredCypher("evt-42", ["episodic", "epistemic", "semantic", "docIndex"])
    # episodic: MATCH (e:Event) RETURN e ORDER BY → MATCH (e:Event) WHERE e.id = $entityId RETURN e ORDER BY
    assert "e.id = $entityId" in cypher["episodic"]
    assert "RETURN e" in cypher["episodic"]  # still returns e
    # epistemic: same pattern
    assert "p.id = $entityId" in cypher["epistemic"]
    # semantic: already has WHERE → prepends
    assert "n.id = $entityId" in cypher["semantic"]
    assert "n:Subject OR n:Object" in cypher["semantic"]  # original filter preserved
    # docIndex: variable is d
    assert "d.id = $entityId" in cypher["docIndex"]
    print("✓ _entityAnchoredCypher injection")


def test_cross_ontology_query_with_entity_id():
    """crossOntologyQuery with entityId dispatches entity-anchored Cypher."""
    db = _mock_db({
        "epistemic": [[[_node("pt-1", ["Point"], {"content": "found", "pointKind": "claim"})]]],
    })
    result = crossOntologyQuery("whatever", db, patterns=["claimsAbout"], entityId="pt-1")
    # The entity-anchored Cypher should have matched pt-1
    assert result.mergedCount >= 1
    print("✓ crossOntologyQuery with entityId")


def test_cross_ontology_query_no_entity_id():
    """Without entityId, crossOntologyQuery works normally (backward compat)."""
    db = _mock_db({
        "epistemic": [[[_node("pt-1", ["Point"], {"content": "normal"})]]],
    })
    result = crossOntologyQuery("what do we believe?", db)
    assert result.mergedCount >= 1
    assert result.results[0]["id"] == "pt-1"
    print("✓ crossOntologyQuery without entityId (backward compat)")


# ── Main ───────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_entity_profile_empty_entity,
        test_entity_profile_single_hop,
        test_entity_profile_multi_hop,
        test_entity_profile_filter_pointKind,
        test_entity_profile_categorize_types,
        test_tortoise_traverse_basic,
        test_tortoise_traverse_diamond,
        test_parse_node_prefers_public_id_over_internal,
        test_tortoise_traverse_returns_public_ids,
        test_entity_profile_returns_public_ids,
        test_entity_anchored_cypher_injection,
        test_cross_ontology_query_with_entity_id,
        test_cross_ontology_query_no_entity_id,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"✗ {t.__name__} FAILED: {e}")
            raise
    print(f"\n─── {passed}/{len(tests)} passed ───")
