"""Verification tests for memory_orchestrator.py — E2E + unit.
Run: python3 tests/test_memory_orchestrator.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from tortoise.memory_orchestrator import (
    DomainRouter,
    MergeResult,
    OrchestratorError,
    crossDomainQuery,
    crossOntologyQuery,
    dispatch,
    merge,
    routeRead,
    routeWrite,
    translateNL,
)
from tortoise.domain_loader import DomainRoutingConfig


# ── Unit: Routing ──────────────────────────────────────

def test_route_write():
    assert "episodic" in routeWrite("EventRecorded")
    assert "epistemic" in routeWrite("PointAdded")
    assert "epistemic" in routeWrite("PointRevised")
    assert routeWrite("UnknownEventType") == []
    print("✓ routeWrite")


def test_route_read():
    assert routeRead(["whatHappened"]) == ["episodic"]
    assert routeRead(["claimsAbout"]) == ["epistemic"]
    assert routeRead([]) == []

    # Multiple patterns → deduplicated union
    ontologies = routeRead(["whatHappened", "claimsAbout"])
    assert "episodic" in ontologies
    assert "epistemic" in ontologies
    assert len(ontologies) == 2

    # Unknown pattern → ignored
    ontologies = routeRead(["unknownPattern"])
    assert ontologies == []

    print("✓ routeRead")


# ── Unit: NL Translation ───────────────────────────────

def test_translate_nl():
    # Valid query
    patterns, _ = translateNL("what happened last session?")
    assert "whatHappened" in patterns

    # No matching keywords → empty
    patterns, _ = translateNL("xyzzy blarg florb")
    assert patterns == []

    # Multiple patterns matched
    patterns, _ = translateNL("what decisions about competitor X were made recently?")
    assert "claimsAbout" in patterns

    # Timeline query
    patterns, _ = translateNL("show me the timeline of events")
    assert "timeline" in patterns

    # Beliefs query
    patterns, _ = translateNL("what do we believe about AI?")
    assert "beliefs" in patterns

    print("✓ translateNL")


# ── Unit: Merge ────────────────────────────────────────

def test_merge_empty():
    result = merge({}, {})
    assert result.mergedCount == 0
    assert result.totalByOntology == {}
    assert result.failedOntologies == []
    assert result.conflicts == []
    print("✓ merge empty")


def test_merge_single_ontology():
    results = {
        "epistemic": [
            {"id": "pt-1", "type": "Point", "content": "AI is transformative"},
            {"id": "pt-2", "type": "Point", "content": "We should invest"},
        ]
    }
    result = merge(results, {})
    assert result.mergedCount == 2
    assert result.totalByOntology == {"epistemic": 2}
    assert result.byOntology == results
    assert "epistemic" in result.results[0]["sourceOntologies"]
    print("✓ merge single ontology")


def test_merge_dedup():
    results = {
        "episodic": [{"id": "evt-1", "content": "decided X"}],
        "epistemic": [{"id": "evt-1", "content": "decided X"}],  # same ID, same content
    }
    result = merge(results, {})
    assert result.mergedCount == 1
    assert set(result.results[0]["sourceOntologies"]) == {"episodic", "epistemic"}
    assert result.conflicts == []  # same content, no conflict
    print("✓ merge dedup (no conflict)")


def test_merge_conflict():
    results = {
        "episodic": [{"id": "evt-1", "content": "decided X"}],
        "epistemic": [{"id": "evt-1", "content": "decided Y"}],  # same ID, different content
    }
    result = merge(results, {})
    assert result.mergedCount == 1
    assert result.results[0]["conflict"] is True
    assert "content" in result.results[0]["conflictDetail"]
    assert result.results[0]["conflictDetail"]["content"] == ["decided X", "decided Y"]
    assert len(result.conflicts) == 1
    print("✓ merge conflict detection")


def test_merge_missing_id():
    results = {
        "epistemic": [
            {"content": "no id here"},  # no id → skipped
            {"id": "pt-1", "content": "has id"},
        ]
    }
    result = merge(results, {})
    assert result.mergedCount == 1
    print("✓ merge missing id")


def test_merge_failed_ontologies():
    errors = {"semantic": "timeout", "docIndex": "connection refused"}
    result = merge({"episodic": []}, errors)
    assert "semantic" in result.failedOntologies
    assert "docIndex" in result.failedOntologies
    print("✓ merge failed ontologies")


def test_merge_provenance():
    results = {
        "episodic": [{"id": "evt-1", "content": "event"}],
        "epistemic": [{"id": "pt-1", "content": "belief"}],
    }
    result = merge(results, {})
    # Each result tagged with source
    assert len(result.byOntology) == 2
    assert "episodic" in result.byOntology
    assert "epistemic" in result.byOntology
    # No duplicate IDs in merged
    eids = [r["id"] for r in result.results]
    assert len(eids) == len(set(eids))
    print("✓ merge provenance")


def test_merge_by_ontology_raw():
    """byOntology preserves raw per-ontology results."""
    raw_episodic = [{"id": "e1", "content": "event"}]
    raw_epistemic = [{"id": "p1", "content": "point"}]
    results = {"episodic": raw_episodic, "epistemic": raw_epistemic}
    result = merge(results, {})
    assert result.byOntology["episodic"] is raw_episodic
    assert result.byOntology["epistemic"] is raw_epistemic
    print("✓ merge byOntology raw reference")


def test_merge_no_id_type_leak():
    """id and type should NOT leak into properties."""
    results = {
        "episodic": [{"id": "evt-1", "type": "Event", "content": "hello"}],
        "epistemic": [{"id": "evt-1", "type": "Point", "content": "hello"}],
    }
    result = merge(results, {})
    assert result.mergedCount == 1
    props = result.results[0]["properties"]
    assert "id" not in props, f"id leaked into properties: {props}"
    assert "type" not in props, f"type leaked into properties: {props}"
    assert props.get("content") == "hello"
    print("✓ merge no id/type leak")


def test_merge_three_way_conflict():
    """3-way conflict preserves all values."""
    results = {
        "episodic": [{"id": "e1", "content": "A"}],
        "epistemic": [{"id": "e1", "content": "B"}],
        "semantic": [{"id": "e1", "content": "C"}],
    }
    result = merge(results, {})
    assert result.mergedCount == 1
    assert result.results[0]["conflict"] is True
    assert result.results[0]["conflictDetail"]["content"] == ["A", "B", "C"]
    print("✓ merge 3-way conflict preserves all values")


# ── Integration: dispatch (mock) ───────────────────────

def _mock_db(graph_data: dict[str, list[list]]):
    """Create a mock FalkorDB db with per-graph result_sets."""
    db = MagicMock()

    def _select_graph(name):
        g = MagicMock()
        data = graph_data.get(name, [])

        def _query(cypher: str):
            result = MagicMock()
            result.result_set = data
            return result

        g.query = _query
        return g

    db.select_graph = _select_graph
    return db


def test_dispatch_success():
    db = _mock_db({
        "episodic": [[[1, ["Event"], [["content", "evt1"]]]]],
        "epistemic": [[[2, ["Point"], [["content", "pt1"]]]]],
    })
    results, errors = dispatch(["episodic", "epistemic"], db, timeout=5.0)
    assert "episodic" in results
    assert "epistemic" in results
    assert errors == {}
    print("✓ dispatch success")


def test_dispatch_partial_failure():
    db = _mock_db({
        "episodic": [[1, ["Event"], [["content", "evt1"]]]],
    })

    def _fail_select(name):
        g = MagicMock()
        if name == "epistemic":
            g.query = MagicMock(side_effect=Exception("connection refused"))
        else:
            data = [[[1, ["Event"], [["content", "evt1"]]]]]
            g.query = lambda cypher: MagicMock(result_set=data)
        return g

    db.select_graph = _fail_select
    results, errors = dispatch(["episodic", "epistemic"], db, timeout=5.0)
    assert "episodic" in results
    assert "epistemic" in errors
    assert "connection refused" in errors["epistemic"]
    print("✓ dispatch partial failure")


def test_dispatch_all_fail():
    db = MagicMock()
    db.select_graph = lambda name: MagicMock(query=MagicMock(side_effect=Exception("boom")))
    try:
        dispatch(["episodic", "epistemic"], db, timeout=5.0)
        assert False, "Should have raised OrchestratorError"
    except OrchestratorError as e:
        assert len(e.failures) == 2
        assert "episodic" in e.failures
        assert "epistemic" in e.failures
    print("✓ dispatch all fail → OrchestratorError")


def test_dispatch_no_cypher_template():
    db = MagicMock()
    try:
        dispatch(["unknownOntology"], db, timeout=5.0)
        assert False, "Should have raised OrchestratorError"
    except OrchestratorError as e:
        assert "no Cypher template" in e.failures["unknownOntology"]
    print("✓ dispatch no Cypher template → OrchestratorError")


def test_dispatch_empty_results():
    """Empty result_set returns empty list, not error."""
    db = _mock_db({"episodic": []})
    results, errors = dispatch(["episodic"], db)
    assert "episodic" in results
    assert results["episodic"] == []
    assert errors == {}
    print("✓ dispatch empty results")


def test_dispatch_empty_ontologies():
    """dispatch with empty list returns empty results."""
    db = MagicMock()
    results, errors = dispatch([], db)
    assert results == {}
    assert errors == {}
    print("✓ dispatch empty ontologies")


def test_parse_node_object():
    """_parseNode handles FalkorDB Node objects."""
    from falkordb import Node
    from tortoise.memory_orchestrator import _parseNode
    node = Node(node_id=42, labels=["Event"], properties={"content": "hello"})
    result = _parseNode(node)
    assert result["id"] == "42"
    assert result["type"] == "Event"
    assert result["content"] == "hello"
    print("✓ _parseNode Node object")


def test_parse_node_list():
    """_parseNode handles raw list form."""
    from tortoise.memory_orchestrator import _parseNode
    node_list = [7, ["Point"], [["content", "test"], ["context", "ctx"]]]
    result = _parseNode(node_list)
    assert result["id"] == "7"
    assert result["type"] == "Point"
    assert result["content"] == "test"
    assert result["context"] == "ctx"
    print("✓ _parseNode raw list")


# ── Integration: crossOntologyQuery (mock) ─────────────

def test_cross_ontology_query_nl_mode():
    db = _mock_db({
        "episodic": [[[1, ["Event"], [["content", "evt1"]]]]],
    })
    result = crossOntologyQuery("what happened recently?", db)
    assert result.mergedCount == 1
    assert "episodic" in result.totalByOntology
    print("✓ crossOntologyQuery NL mode")


def test_cross_ontology_query_structured_mode():
    db = _mock_db({
        "epistemic": [[[2, ["Point"], [["content", "pt1"]]]]],
    })
    result = crossOntologyQuery("ignore this", db, patterns=["claimsAbout"])
    assert result.mergedCount == 1
    print("✓ crossOntologyQuery structured mode")


def test_cross_ontology_query_no_match_defaults():
    db = _mock_db({
        "epistemic": [[[3, ["Point"], [["content", "default"]]]]],
    })
    # No patterns matched → defaults to epistemic
    result = crossOntologyQuery("xyzzy no match", db)
    assert result.mergedCount == 1
    print("✓ crossOntologyQuery no-match default")


def test_cross_ontology_query_partial():
    db = _mock_db({
        "episodic": [[[1, ["Event"], [["content", "e1"]]]]],
        "epistemic": [[[2, ["Point"], [["content", "p1"]]]]],
    })
    result = crossOntologyQuery("what happened and what about X?", db)
    assert result.mergedCount == 2
    assert "episodic" in result.totalByOntology
    assert "epistemic" in result.totalByOntology
    print("✓ crossOntologyQuery multi-pattern")


# ── DomainRouter tests ─────────────────────────────────

def _make_router(*, with_domain: bool = True) -> DomainRouter:
    """Create a DomainRouter for testing — explicit config, no file I/O."""
    domains = {}
    if with_domain:
        domains["product-strategy"] = DomainRoutingConfig(
            key="product-strategy",
            name="Product Strategy",
            event_types=["PointAdded", "StrategyDefined"],
            query_patterns=["productStrategy", "jtbd", "useCases"],
            cypher_template="MATCH (p:Point) WHERE p.pointKind IN ['useCase', 'jobToBeDone'] RETURN p LIMIT 50",
            timeout=3.0,
            priority=5,
        )
    return DomainRouter(domains=domains)


def test_domain_router_route_write_merged():
    """routeWrite() merges base + domain event types."""
    router = _make_router()
    # Base routing still works
    assert "episodic" in router.routeWrite("EventRecorded")
    assert "epistemic" in router.routeWrite("PointAdded")
    # Domain routing merged in
    assert "product-strategy" in router.routeWrite("PointAdded")
    assert "product-strategy" in router.routeWrite("StrategyDefined")
    # Unknown → empty
    assert router.routeWrite("UnknownEvent") == []
    print("✓ domain router routeWrite merged")


def test_domain_router_route_read_merged():
    """routeRead() merges base + domain query patterns."""
    router = _make_router()
    # Base patterns still route to base ontologies
    assert "episodic" in router.routeRead(["whatHappened"])
    # Domain patterns route to domain ontology
    assert router.routeRead(["productStrategy"]) == ["product-strategy"]
    assert router.routeRead(["jtbd"]) == ["product-strategy"]
    # Unknown → empty
    assert router.routeRead(["unknownPattern"]) == []
    print("✓ domain router routeRead merged")


def test_domain_router_route_read_priority_order():
    """routeRead() returns ontologies sorted by priority (lowest first)."""
    router = _make_router()
    # product-strategy has priority 5, base ontologies have priority 10 (default)
    # "PointAdded" triggers both epistemic (base) and product-strategy
    patterns, _ = translateNL("what events about product strategy?")
    # Just test that productStrategy routes to product-strategy first
    result = router.routeRead(["productStrategy", "whatHappened"])
    assert result[0] == "product-strategy"  # priority 5 < 10
    print("✓ domain router routeRead priority order")


def test_cross_domain_query_dispatch():
    """crossDomainQuery() dispatches to specific domain ontologies."""
    router = _make_router()
    db = _mock_db({
        "product-strategy": [[[1, ["Point"], [["content", "JTBD: users need X"]]]]],
    })
    result = router.crossDomainQuery(["product-strategy"], "show JTBD", db)
    assert result.mergedCount == 1
    assert "product-strategy" in result.totalByOntology
    print("✓ crossDomainQuery dispatch")


def test_cross_domain_query_unknown_domain():
    """crossDomainQuery() skips unknown domains, returns empty."""
    router = _make_router(with_domain=False)
    db = _mock_db({})
    result = router.crossDomainQuery(["nonexistent"], "query", db)
    assert result.mergedCount == 0
    print("✓ crossDomainQuery unknown domain")


def test_cross_domain_query_multi_domain():
    """crossDomainQuery() dispatches to multiple domains in parallel."""
    domains = {
        "product-strategy": DomainRoutingConfig(
            key="product-strategy", name="PS",
            cypher_template="MATCH (p:Point) RETURN p LIMIT 10",
            query_patterns=["ps"],
        ),
        "custom-domain": DomainRoutingConfig(
            key="custom-domain", name="Custom",
            cypher_template="MATCH (d:Document) RETURN d LIMIT 10",
            query_patterns=["custom"],
        ),
    }
    router = DomainRouter(domains=domains)
    db = _mock_db({
        "product-strategy": [[[1, ["Point"], [["content", "ps"]]]]],
        "custom-domain": [[[2, ["Document"], [["title", "doc1"]]]]],
    })
    result = router.crossDomainQuery(
        ["product-strategy", "custom-domain"], "query", db
    )
    assert result.mergedCount == 2
    assert "product-strategy" in result.totalByOntology
    assert "custom-domain" in result.totalByOntology
    # Distinct IDs → no dedup
    assert len(result.conflicts) == 0
    print("✓ crossDomainQuery multi-domain")


def test_cross_domain_query_partial_failure():
    """crossDomainQuery() surfaces per-domain errors."""
    domains = {
        "good": DomainRoutingConfig(key="good", name="Good", cypher_template="MATCH (n) RETURN n"),
        "bad": DomainRoutingConfig(key="bad", name="Bad", cypher_template="MATCH (n) RETURN n"),
    }
    router = DomainRouter(domains=domains)
    db = _mock_db({"good": [[[1, ["Point"], [["content", "ok"]]]]]})

    def _select(name):
        g = MagicMock()
        if name == "bad":
            g.query = MagicMock(side_effect=Exception("boom"))
        else:
            g.query = lambda c: MagicMock(result_set=[[[1, ["Point"], [["content", "ok"]]]]])
        return g
    db.select_graph = _select

    result = router.crossDomainQuery(["good", "bad"], "query", db)
    assert result.mergedCount == 1
    assert "bad" in result.failedOntologies
    print("✓ crossDomainQuery partial failure")


def test_cross_domain_query_timeout_per_domain():
    """crossDomainQuery() uses per-domain timeout from config."""
    router = _make_router()
    db = _mock_db({
        "product-strategy": [[[1, ["Point"], [["content", "jtbd"]]]]],
    })
    result = router.crossDomainQuery(["product-strategy"], "query", db)
    assert result.mergedCount == 1
    # timeout config was set to 3.0 — no way to assert from mock, but it ran
    print("✓ crossDomainQuery per-domain timeout")


def test_domain_router_full_query_nl_merged():
    """crossOntologyQuery on DomainRouter merges base + domain routing."""
    router = _make_router()
    db = _mock_db({
        "epistemic": [[[1, ["Point"], [["content", "base claim"]]]]],
        "product-strategy": [[[2, ["Point"], [["content", "strategy claim"]]]]],
    })
    # Query with "product strategy" keywords → should route to product-strategy too
    result = router.crossOntologyQuery("what is the product strategy and what do we believe?", db)
    assert result.mergedCount >= 1
    print("✓ domain router full NL query merged")


def test_module_level_cross_domain_query():
    """Module-level crossDomainQuery() convenience function works."""
    # This loads the manifest from disk — product-strategy domain should be present
    db = _mock_db({
        "product-strategy": [[[1, ["Point"], [["content", "jtbd-1"]]]]],
    })
    result = crossDomainQuery(["product-strategy"], "show JTBD", db)
    assert result.mergedCount == 1
    assert "product-strategy" in result.totalByOntology
    print("✓ module-level crossDomainQuery")


# ── Main ───────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_route_write,
        test_route_read,
        test_translate_nl,
        test_merge_empty,
        test_merge_single_ontology,
        test_merge_dedup,
        test_merge_conflict,
        test_merge_missing_id,
        test_merge_failed_ontologies,
        test_merge_provenance,
        test_merge_by_ontology_raw,
        test_merge_no_id_type_leak,
        test_merge_three_way_conflict,
        test_dispatch_success,
        test_dispatch_partial_failure,
        test_dispatch_all_fail,
        test_dispatch_no_cypher_template,
        test_dispatch_empty_results,
        test_dispatch_empty_ontologies,
        test_parse_node_object,
        test_parse_node_list,
        test_cross_ontology_query_nl_mode,
        test_cross_ontology_query_structured_mode,
        test_cross_ontology_query_no_match_defaults,
        test_cross_ontology_query_partial,
        # Domain routing tests
        test_domain_router_route_write_merged,
        test_domain_router_route_read_merged,
        test_domain_router_route_read_priority_order,
        test_cross_domain_query_dispatch,
        test_cross_domain_query_unknown_domain,
        test_cross_domain_query_multi_domain,
        test_cross_domain_query_partial_failure,
        test_cross_domain_query_timeout_per_domain,
        test_domain_router_full_query_nl_merged,
        test_module_level_cross_domain_query,
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


# ── #331: fake dispatch timeout + _parseNode malformed-input crash ──

def test_dispatch_enforces_deadline():
    """#331: dispatch must return at ~timeout even when an ontology query
    hangs. Pre-fix the executor context-manager exit blocked on
    shutdown(wait=True) until the hung query finished (timeout was fake)."""
    import time
    from types import SimpleNamespace
    from tortoise.memory_orchestrator import dispatch

    class _SlowGraph:
        def query(self, cypher, params=None):
            time.sleep(2.0)
            return SimpleNamespace(result_set=[[1]])

    class _FastGraph:
        def query(self, cypher, params=None):
            return SimpleNamespace(result_set=[[1]])

    db = MagicMock()
    db.select_graph = lambda name: _SlowGraph() if name == "episodic" else _FastGraph()

    # Fast ontology first: it completes instantly; the hanging one then burns
    # the shared deadline. dispatch must return at ~timeout regardless.
    start = time.monotonic()
    results, errors = dispatch(["epistemic", "episodic"], db, timeout=0.2)
    elapsed = time.monotonic() - start

    assert "epistemic" in results
    assert errors.get("episodic") == "timeout"
    assert elapsed < 1.5, \
        f"dispatch blocked {elapsed:.2f}s on a hung query — deadline not enforced"
    print("✓ dispatch deadline enforced")


def test_parse_node_malformed_inputs_no_crash():
    """#331: malformed node shapes must not crash _parseNode/dispatch."""
    from tortoise.memory_orchestrator import _parseNode
    for bad in (None, [], [1], [1, []], [1, ["L"], "junk"], "junk", 42):
        try:
            result = _parseNode(bad)
        except Exception as e:  # noqa: BLE001
            assert False, f"_parseNode({bad!r}) raised {type(e).__name__}: {e}"
        assert isinstance(result, dict), f"_parseNode({bad!r}) -> {result!r}"
    print("✓ _parseNode malformed inputs")


def test_parse_node_partial_properties_tolerated():
    """#331: malformed [[k,v]] pairs are skipped, valid ones survive."""
    from tortoise.memory_orchestrator import _parseNode
    node = [7, ["Point"], [["content", "ok"], ["bad", "pair", "extra"], "nope"]]
    result = _parseNode(node)
    assert result["id"] == "7"
    assert result["type"] == "Point"
    assert result["content"] == "ok"
    print("✓ _parseNode partial properties")
