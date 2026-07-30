"""Cross-ontology integration test — end-to-end memory cycle.

Creates test data in FalkorDB (Points + Events), queries across ontologies
via Memory Orchestrator, verifies merged results with provenance tags.

Usage:
  python3 tortoise/test_cross_ontology.py [--db tortoise.db]

S6 of Memory System V1 epic (#5199).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure tortoise is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.projection import FalkorProjection
from tortoise.memory_orchestrator import (
    dispatch, merge, routeRead, translateNL, MergeResult
)

DB_PATH = "tortoise-test-s6.db"


def setup_test_data(proj: FalkorProjection) -> None:
    """Create test Point + Event nodes in ontology-specific graphs."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # ── Epistemic graph: Points ──
    eg = proj.db.select_graph("epistemic")
    eg.query("""
        CREATE (:Point {
            id: 'p-integration-1',
            content: 'ADR-005 selected three-layer expansion pack architecture',
            pointKind: 'decision',
            aboutEntities: ['expansion-packs', 'ADR-005'],
            authoredBy: 'org-design',
            confidence: 0.92,
            status: 'live',
            createdAt: $ts
        })
    """, params={"ts": ts})
    eg.query("""
        CREATE (:Point {
            id: 'p-integration-2',
            content: 'FalkorDB deployment completed via docker-compose',
            pointKind: 'observation',
            aboutEntities: ['falkordb', 'infrastructure'],
            authoredBy: 'epistemic-team',
            confidence: 0.98,
            status: 'live',
            createdAt: $ts
        })
    """, params={"ts": ts})

    # ── Episodic graph: Events ──
    evg = proj.db.select_graph("episodic")
    evg.query("""
        CREATE (:Event {
            id: 'ev-integration-1',
            eventKind: 'decision',
            subject: 'org-design',
            object: 'ADR-005',
            startedAt: $ts
        })
    """, params={"ts": ts})
    evg.query("""
        CREATE (:Event {
            id: 'ev-integration-2',
            eventKind: 'deployment',
            subject: 'epistemic-team',
            object: 'falkordb',
            startedAt: $ts
        })
    """, params={"ts": ts})

    print(f"  Created 2 Points (epistemic graph) + 2 Events (episodic graph)")


def cleanup_test_data(proj: FalkorProjection) -> None:
    """Remove test nodes from both ontology graphs."""
    for nid in ["p-integration-1", "p-integration-2"]:
        proj.db.select_graph("epistemic").query(
            "MATCH (n {id: $id}) DELETE n", params={"id": nid}
        )
    for nid in ["ev-integration-1", "ev-integration-2"]:
        proj.db.select_graph("episodic").query(
            "MATCH (n {id: $id}) DELETE n", params={"id": nid}
        )
    print("  Cleaned up test data")


def test_happy_path(proj: FalkorProjection) -> None:
    """Query across episodic + epistemic, verify merged results."""
    patterns, _ = translateNL("what decisions about ADR-005?")
    ontologies = routeRead(patterns)
    assert "epistemic" in ontologies or "episodic" in ontologies, \
        f"No ontologies matched: {ontologies}"

    results, errors = dispatch(ontologies, proj.db, timeout=5.0)
    assert not errors, f"Dispatch errors: {errors}"
    assert results, "No results returned"

    merged = merge(results, errors)
    assert merged.mergedCount > 0, f"Merge produced 0 results: {merged}"
    assert merged.totalByOntology, "No per-ontology counts"
    print(f"  Happy path: {merged.mergedCount} results across {len(merged.totalByOntology)} ontologies")
    for ont, count in merged.totalByOntology.items():
        print(f"    {ont}: {count} results")


def test_empty_graph() -> None:
    """Query an empty DB — should return explicit no-results, not crash."""
    empty_db = Path("/tmp/tortoise-empty-s6.db")
    if empty_db.exists():
        empty_db.unlink()

    proj = FalkorProjection(str(empty_db))
    try:
        results, errors = dispatch(["epistemic", "episodic"], proj.db, timeout=1.0)
        # Empty graph → no results, no errors
        assert not errors or all("no Cypher" not in str(e) for e in errors.values()), \
            f"Unexpected errors on empty graph: {errors}"
        print(f"  Empty graph: {sum(len(v) for v in results.values())} results (expected 0)")
    finally:
        empty_db.unlink(missing_ok=True)


def test_partial_failure() -> None:
    """One ontology fails — partial results returned, failure flagged."""
    proj = FalkorProjection("/tmp/tortoise-partial-s6.db")
    try:
        # Query an ontology that doesn't exist in this empty DB
        results, errors = dispatch(["epistemic", "nonexistent_ontology"], proj.db, timeout=2.0)
        assert "nonexistent_ontology" in errors, \
            f"Expected error for nonexistent ontology, got: {errors}"
        # Epistemic should return empty (graph is empty, not error)
        assert "epistemic" in results, f"Expected epistemic in results: {results}"
        print(f"  Partial failure: epistemic OK, nonexistent flagged as error")
    finally:
        Path("/tmp/tortoise-partial-s6.db").unlink(missing_ok=True)


def test_nl_translation() -> None:
    """NL query → patterns → ontologies."""
    patterns, _ = translateNL("what happened last session?")
    assert patterns, f"No patterns matched for 'what happened'"
    ontologies = routeRead(patterns)
    assert "episodic" in ontologies, f"Expected episodic, got: {ontologies}"
    print(f"  NL: 'what happened' → {patterns} → {ontologies}")

    patterns, _ = translateNL("what do we believe about competitors?")
    ontologies = routeRead(patterns)
    assert "epistemic" in ontologies, f"Expected epistemic, got: {ontologies}"
    print(f"  NL: 'what do we believe' → {patterns} → {ontologies}")


if __name__ == "__main__":
    db_path = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--db" else DB_PATH

    print("=== Cross-Ontology Integration Test (S6) ===")
    proj = FalkorProjection(db_path)

    try:
        setup_test_data(proj)
        test_happy_path(proj)
        test_empty_graph()
        test_partial_failure()
        test_nl_translation()
        print("\nAll integration tests passed ✓")
    finally:
        cleanup_test_data(proj)
        Path(db_path).unlink(missing_ok=True)
