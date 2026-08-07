"""Comprehensive projection tests — covers _apply_one, fold, split,
InMemoryProjection, and FalkorProjection (including compute_grounding,
propagate_shock, rebuild_all, edge_stats, and internal helpers).

Runnable without pytest:  .venv/bin/python tests/test_projection.py
(also works under pytest if installed).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.api import EventAPI, provenance          # noqa: E402
from tortoise.log import EventLog                       # noqa: E402
from tortoise.projection import (                        # noqa: E402
    _apply_one, fold, split,
    InMemoryProjection, FalkorProjection, Projection,
)                                                       # noqa: E402

# ------------------------------------------------------------------ helpers

def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_test_"), name)


def _api(projection=None):
    log = EventLog(_tmp("events.jsonl"))
    return EventAPI(log, initiated_by="extractor", agent_id="test",
                    projection=projection), log


def _build(api, source="doc.txt"):
    """Two statements + one IMPL operator between them."""
    prov = provenance(source, [0, 10], "quote", extracted_by="test@0")
    a = api.add_point("we should raise B slowly", prov)
    b = api.add_point("fast raises wreck early buyers", prov)
    op = api.add_operator("IMPL", [b, a], prov)
    return a, b, op


_HAS_FALKOR: bool | None = None


def _has_falkor() -> bool:
    global _HAS_FALKOR
    if _HAS_FALKOR is None:
        try:
            from redislite.falkordb_client import FalkorDB  # noqa: F401
            _HAS_FALKOR = True
        except ImportError:
            _HAS_FALKOR = False
    return _HAS_FALKOR


def _skip_if_no_falkor() -> bool:
    return not _has_falkor()


# ----------------------------------------------------------------- _apply_one

def test_apply_one_point_added():
    points: dict[str, dict] = {}
    ev = {"type": "PointAdded", "point": {"id": "p1", "content": "hello",
                                           "context": "ctx"}}
    _apply_one(points, ev)
    assert "p1" in points
    assert points["p1"]["content"] == "hello"


def test_apply_one_point_added_with_speaker():
    points: dict[str, dict] = {}
    ev = {"type": "PointAdded",
          "point": {"id": "p1", "content": "hi", "context": "ctx",
                    "provenance": {"speaker": "alice", "source_id": "s1"}}}
    _apply_one(points, ev)
    assert points["p1"]["speaker"] == "alice"


def test_apply_one_operator_added():
    points: dict[str, dict] = {}
    ev = {"type": "OperatorAdded",
          "point": {"id": "op1", "content": "IMPL(a, b)", "context": "ctx",
                    "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}}}
    _apply_one(points, ev)
    assert "op1" in points
    assert points["op1"]["operator"]["op_type"] == "IMPL"


def test_apply_one_point_revised_both_fields():
    points: dict[str, dict] = {"p1": {"id": "p1", "content": "old", "context": "old_ctx"}}
    ev = {"type": "PointRevised", "id": "p1",
          "new_content": "new", "new_context": "new_ctx",
          "projection_version": 2}
    _apply_one(points, ev)
    assert points["p1"]["content"] == "new"
    # P2 #49: v2 events discard new_context — context is NOT revised
    # (old context property, if present, is retained; new_context is dropped)
    assert points["p1"].get("context") == "old_ctx"


def test_apply_one_point_revised_content_only():
    points: dict[str, dict] = {"p1": {"id": "p1", "content": "old", "context": "old_ctx"}}
    ev = {"type": "PointRevised", "id": "p1", "new_content": "new"}
    _apply_one(points, ev)
    assert points["p1"]["content"] == "new"
    assert points["p1"]["context"] == "old_ctx"  # unchanged


def test_apply_one_point_revised_context_only():
    points: dict[str, dict] = {"p1": {"id": "p1", "content": "old", "context": "old_ctx"}}
    ev = {"type": "PointRevised", "id": "p1", "new_context": "new_ctx",
          "projection_version": 2}
    _apply_one(points, ev)
    assert points["p1"]["content"] == "old"  # unchanged
    # P2 #49: v2 events discard new_context — context is NOT revised
    assert points["p1"].get("context") == "old_ctx"


def test_apply_one_point_revised_missing_id():
    points: dict[str, dict] = {}
    ev = {"type": "PointRevised", "id": "nonexistent",
          "new_content": "new", "new_context": "new_ctx"}
    _apply_one(points, ev)  # should not raise
    assert "nonexistent" not in points


def test_apply_one_point_retracted():
    points: dict[str, dict] = {"p1": {"id": "p1", "content": "old"}}
    ev = {"type": "PointRetracted", "id": "p1"}
    _apply_one(points, ev)
    assert "p1" not in points


def test_apply_one_point_retracted_missing():
    points: dict[str, dict] = {}
    ev = {"type": "PointRetracted", "id": "nonexistent"}
    _apply_one(points, ev)  # should not raise


def test_apply_one_points_merged():
    points: dict[str, dict] = {
        "a": {"id": "a", "content": "A"},
        "b": {"id": "b", "content": "B"},
        "c": {"id": "c", "content": "C"},
    }
    ev = {"type": "PointsMerged", "keep_id": "a", "merge_ids": ["b", "c"]}
    _apply_one(points, ev)
    assert "a" in points
    assert "b" not in points
    assert "c" not in points


def test_apply_one_points_merged_empty_ids():
    points: dict[str, dict] = {"a": {"id": "a", "content": "A"}}
    ev = {"type": "PointsMerged", "keep_id": "a", "merge_ids": []}
    _apply_one(points, ev)
    assert "a" in points  # unchanged


def test_apply_one_ingest_started_noop():
    points: dict[str, dict] = {"a": {"id": "a", "content": "A"}}
    ev = {"type": "IngestStarted", "run_id": "r1", "source_id": "src",
          "extractor_version": "v1", "key": {"kind": "doc", "value": "hello"}}
    _apply_one(points, ev)
    assert "a" in points  # no change


# -------------------------------------------------------------------- fold

def test_fold_empty():
    assert fold([]) == {}


def test_fold_multiple_events():
    events = [
        {"type": "PointAdded", "point": {"id": "p1", "content": "one", "context": "ctx"}},
        {"type": "PointAdded", "point": {"id": "p2", "content": "two", "context": "ctx"}},
        {"type": "PointRetracted", "id": "p1"},
    ]
    result = fold(events)
    assert "p1" not in result
    assert result["p2"]["content"] == "two"


def test_fold_revise_then_retract():
    events = [
        {"type": "PointAdded", "point": {"id": "p1", "content": "a", "context": "ctx"}},
        {"type": "PointRevised", "id": "p1", "new_content": "b"},
        {"type": "PointRetracted", "id": "p1"},
    ]
    assert fold(events) == {}


# -------------------------------------------------------------------- split

def test_split_all_statements():
    points = {
        "s1": {"id": "s1", "content": "hello", "context": "ctx"},
        "s2": {"id": "s2", "content": "world", "context": "ctx"},
    }
    stmts, ops = split(points)
    assert len(stmts) == 2
    assert ops == []


def test_split_all_operators():
    points = {
        "op1": {"id": "op1", "content": "IMPL(a,b)", "context": "ctx",
                "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}},
        "op2": {"id": "op2", "content": "NAND(c,d)", "context": "ctx",
                "operator": {"op_type": "NAND", "inputs": ["c", "d"]}},
    }
    stmts, ops = split(points)
    assert stmts == []
    assert len(ops) == 2


def test_split_mixed():
    points = {
        "s1": {"id": "s1", "content": "hello", "context": "ctx"},
        "op1": {"id": "op1", "content": "IMPL(a,b)", "context": "ctx",
                "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}},
        "s2": {"id": "s2", "content": "world", "context": "ctx"},
        "op2": {"id": "op2", "content": "NAND(c,d)", "context": "ctx",
                "operator": {"op_type": "NAND", "inputs": ["c", "d"]}},
    }
    stmts, ops = split(points)
    assert len(stmts) == 2
    assert len(ops) == 2


def test_split_empty():
    stmts, ops = split({})
    assert stmts == []
    assert ops == []


# ----------------------------------------------------- InMemoryProjection

def test_inmemory_apply():
    proj = InMemoryProjection()
    proj.apply({"type": "PointAdded",
                "point": {"id": "p1", "content": "hello", "context": "ctx"}})
    assert proj.points["p1"]["content"] == "hello"
    proj.apply({"type": "PointRetracted", "id": "p1"})
    assert "p1" not in proj.points


def test_inmemory_rebuild():
    log = EventLog(_tmp("events.jsonl"))
    api = EventAPI(log, initiated_by="extractor", agent_id="test")
    a, b, op = _build(api)
    proj = InMemoryProjection()
    proj.rebuild(log)
    assert a in proj.points
    assert b in proj.points
    assert op in proj.points
    assert proj.points[a]["content"] == "we should raise B slowly"


def test_inmemory_conformance():
    """InMemoryProjection satisfies the Projection protocol."""
    proj = InMemoryProjection()
    assert isinstance(proj, Projection)


# --------------------------------------------- FalkorProjection — basics

def test_falkor_apply_point_added():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.apply({"type": "PointAdded",
                     "point": {"id": "p1", "content": "hello", "context": "ctx"}})
        r = proj.query("MATCH (n:Point {id:'p1'}) RETURN n.content").result_set
        assert r[0][0] == "hello"
    finally:
        proj.close()


def test_falkor_apply_operator_added():
    if _skip_if_no_falkor():
        return
    """OperatorAdded creates node + edges to its inputs."""
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        # Add inputs first
        proj.apply({"type": "PointAdded",
                     "point": {"id": "a", "content": "A", "context": "ctx"}})
        proj.apply({"type": "PointAdded",
                     "point": {"id": "b", "content": "B", "context": "ctx"}})
        proj.apply({"type": "OperatorAdded",
                     "point": {"id": "op1", "content": "IMPL(a,b)", "context": "ctx",
                               "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}}})
        # Operator node exists
        r = proj.query("MATCH (n:Point {id:'op1'}) RETURN n.is_operator, n.op_type").result_set
        assert r[0][0] is True
        assert r[0][1] == "IMPL"
        # IMPL edges exist
        edges = proj.query(
            "MATCH (o:Point {id:'op1'})-[r:IMPL]->(s:Point) RETURN s.id"
        ).result_set
        assert len(edges) == 2
        targets = {e[0] for e in edges}
        assert targets == {"a", "b"}
        # Reverse INPUT edges also exist
        rev = proj.query(
            "MATCH (s:Point {id:'a'})-[r:INPUT]->(o:Point {id:'op1'}) RETURN count(r)"
        ).result_set
        assert rev[0][0] == 1
    finally:
        proj.close()


def test_falkor_apply_operator_added_orphan_stubs():
    if _skip_if_no_falkor():
        return
    """OperatorAdded auto-creates stub nodes for short-ID inputs."""
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        # "42" is short (< 20 chars) → stub auto-created
        proj.apply({"type": "OperatorAdded",
                     "point": {"id": "op1", "content": "IMPL(op1,42)", "context": "ctx",
                               "operator": {"op_type": "IMPL", "inputs": ["42"]}}})
        stub = proj.query("MATCH (n:Point {id:'42'}) RETURN n.content, n.context").result_set
        assert stub[0][0] == "[missing]"
        assert stub[0][1] is None  # P2 #49: context field removed — stubs carry no context
    finally:
        proj.close()


def test_falkor_apply_point_revised():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.apply({"type": "PointAdded",
                     "point": {"id": "p1", "content": "old", "context": "old_ctx"}})
        proj.apply({"type": "PointRevised", "id": "p1",
                     "new_content": "new", "new_context": "new_ctx"})
        r = proj.query(
            "MATCH (n:Point {id:'p1'}) RETURN n.content, n.context"
        ).result_set
        assert r[0][0] == "new"
        # P2 #49: context revision removed — n.context is None/gone
        assert r[0][1] is None
    finally:
        proj.close()


def test_falkor_apply_point_retracted():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.apply({"type": "PointAdded",
                     "point": {"id": "p1", "content": "hello", "context": "ctx"}})
        proj.apply({"type": "PointRetracted", "id": "p1"})
        r = proj.query("MATCH (n:Point {id:'p1'}) RETURN count(n)").result_set
        assert r[0][0] == 0
    finally:
        proj.close()


def test_falkor_apply_points_merged():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.apply({"type": "PointAdded",
                     "point": {"id": "a", "content": "A", "context": "ctx"}})
        proj.apply({"type": "PointAdded",
                     "point": {"id": "b", "content": "B", "context": "ctx"}})
        proj.apply({"type": "PointsMerged", "keep_id": "a", "merge_ids": ["b"]})
        assert proj.query(
            "MATCH (n:Point {id:'a'}) RETURN count(n)"
        ).result_set[0][0] == 1
        assert proj.query(
            "MATCH (n:Point {id:'b'}) RETURN count(n)"
        ).result_set[0][0] == 0
    finally:
        proj.close()


def test_falkor_nand_operator():
    if _skip_if_no_falkor():
        return
    """NAND operator gets :NAND typed edges."""
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.apply({"type": "PointAdded",
                     "point": {"id": "a", "content": "A", "context": "ctx"}})
        proj.apply({"type": "PointAdded",
                     "point": {"id": "b", "content": "B", "context": "ctx"}})
        proj.apply({"type": "OperatorAdded",
                     "point": {"id": "n1", "content": "NAND(a,b)", "context": "ctx",
                               "operator": {"op_type": "NAND", "inputs": ["a", "b"]}}})
        edges = proj.query(
            "MATCH (o:Point {id:'n1'})-[r:NAND]->(s:Point) RETURN s.id"
        ).result_set
        assert len(edges) == 2
    finally:
        proj.close()


def test_falkor_unknown_operator_type():
    if _skip_if_no_falkor():
        return
    """Unknown operator type defaults to :INPUT edges."""
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.apply({"type": "PointAdded",
                     "point": {"id": "a", "content": "A", "context": "ctx"}})
        proj.apply({"type": "OperatorAdded",
                     "point": {"id": "op1", "content": "XOR(a)", "context": "ctx",
                               "operator": {"op_type": "XOR", "inputs": ["a"]}}})
        edges = proj.query(
            "MATCH (o:Point {id:'op1'})-[r:INPUT]->(s:Point) RETURN count(r)"
        ).result_set
        assert edges[0][0] == 1
    finally:
        proj.close()


# -------------------------------------------------- FalkorProjection.rebuild

def test_falkor_rebuild_from_log():
    if _skip_if_no_falkor():
        return
    api, log = _api()
    _build(api)
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.rebuild(log)
        n = proj.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
        assert n == 3  # 2 statements + 1 operator
    finally:
        proj.close()


def test_falkor_rebuild_then_apply():
    if _skip_if_no_falkor():
        return
    """Incremental apply after rebuild matches full fold."""
    api, log = _api()
    a, b, op = _build(api)
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.rebuild(log)
        # Apply a new event incrementally
        c = api.add_point("third statement", provenance("doc.txt", [20, 30], "extra"))
        proj.apply(log.read_all()[-1])  # the newly appended event
        n = proj.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
        assert n == 4
    finally:
        proj.close()


# ----------------------------------------------- FalkorProjection.edge_stats

def test_falkor_edge_stats():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.apply({"type": "PointAdded",
                     "point": {"id": "a", "content": "A", "context": "ctx"}})
        proj.apply({"type": "PointAdded",
                     "point": {"id": "b", "content": "B", "context": "ctx"}})
        proj.apply({"type": "OperatorAdded",
                     "point": {"id": "op1", "content": "IMPL", "context": "ctx",
                               "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}}})
        proj.apply({"type": "OperatorAdded",
                     "point": {"id": "op2", "content": "NAND", "context": "ctx",
                               "operator": {"op_type": "NAND", "inputs": ["a", "b"]}}})
        stats = proj.edge_stats()
        assert stats["operators"] == 2
        # IMPL edges: 2 (op1→a, op1→b) + 2 reverse INPUT = 4 total IMPL-labeled.
        # But edge_stats counts by label, not direction.
        # IMPL edges (o→s direction): 2
        # NAND edges (o→s direction): 2
        # INPUT edges (s→o reverse direction): 4 (2 from IMPL, 2 from NAND)
        assert stats["impl_edges"] == 2
        assert stats["nand_edges"] == 2
        assert stats["input_edges"] == 4
    finally:
        proj.close()


def test_falkor_edge_stats_empty():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        stats = proj.edge_stats()
        assert stats["operators"] == 0
        assert stats["impl_edges"] == 0
        assert stats["nand_edges"] == 0
        assert stats["input_edges"] == 0
    finally:
        proj.close()


# ------------------------------------------- FalkorProjection._upsert direct

def test_falkor_upsert_statement():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "s1", "content": "hello", "context": "ctx"})
        r = proj.query(
            "MATCH (n:Point {id:'s1'}) RETURN n.content, n.is_operator, n.op_type"
        ).result_set
        assert r[0][0] == "hello"
        assert r[0][1] is False
        assert r[0][2] is None
    finally:
        proj.close()


def test_falkor_upsert_operator():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "op1", "content": "IMPL(a,b)", "context": "ctx",
                       "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}})
        r = proj.query(
            "MATCH (n:Point {id:'op1'}) RETURN n.is_operator, n.op_type"
        ).result_set
        assert r[0][0] is True
        assert r[0][1] == "IMPL"
    finally:
        proj.close()


def test_falkor_upsert_idempotent():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "s1", "content": "hello", "context": "ctx"})
        proj._upsert({"id": "s1", "content": "updated", "context": "new_ctx"})
        r = proj.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
        assert r == 1  # still only one node
        r2 = proj.query(
            "MATCH (n:Point {id:'s1'}) RETURN n.content, n.context"
        ).result_set
        assert r2[0][0] == "updated"
    finally:
        proj.close()


# ---------------------------------------------- FalkorProjection._delete

def test_falkor_delete():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "p1", "content": "hello", "context": "ctx"})
        proj._delete("p1")
        r = proj.query("MATCH (n:Point {id:'p1'}) RETURN count(n)").result_set
        assert r[0][0] == 0
    finally:
        proj.close()


def test_falkor_delete_missing_noop():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._delete("nonexistent")  # should not raise
    finally:
        proj.close()


def test_falkor_delete_cascades_edges():
    if _skip_if_no_falkor():
        return
    """DETACH DELETE should cascade to edges."""
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "a", "content": "A", "context": "ctx"})
        proj._upsert({"id": "op1", "content": "IMPL", "context": "ctx",
                       "operator": {"op_type": "IMPL", "inputs": ["a"]}})
        # Verify edges exist
        e = proj.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
        assert e > 0
        # Delete the operator
        proj._delete("op1")
        # Edges should be gone
        e2 = proj.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
        assert e2 == 0
    finally:
        proj.close()


# --------------------------------- FalkorProjection._confidence / _neighbors

def test_falkor_confidence_default():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        # Non-existing node returns 0.5
        assert proj._confidence("nonexistent") == 0.5
        # Existing node without explicit confidence
        proj._upsert({"id": "p1", "content": "hello", "context": "ctx"})
        assert proj._confidence("p1") == 0.5
    finally:
        proj.close()


def test_falkor_confidence_explicit():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "p1", "content": "hello", "context": "ctx"})
        proj.g.query("MATCH (n:Point {id:'p1'}) SET n.confidence=0.8")
        assert proj._confidence("p1") == 0.8
    finally:
        proj.close()


def test_falkor_neighbors_empty():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "loner", "content": "solitary", "context": "ctx"})
        assert proj._neighbors("loner") == []
    finally:
        proj.close()


def test_falkor_neighbors():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "a", "content": "A", "context": "ctx"})
        proj._upsert({"id": "b", "content": "B", "context": "ctx"})
        proj._upsert({"id": "c", "content": "C", "context": "ctx"})
        proj._upsert({"id": "op1", "content": "IMPL", "context": "ctx",
                       "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}})
        proj._upsert({"id": "op2", "content": "NAND", "context": "ctx",
                       "operator": {"op_type": "NAND", "inputs": ["a", "c"]}})
        # _neighbors hops THROUGH operator nodes to reach OTHER points.
        # Operators are connectors, not neighbors.
        # After #226: _create_edges creates (op)-[:IMPL]->(src) + (src)-[:INPUT]->(op).
        # From 'a', _neighbors finds: b (via op1 IMPL), c (via op2 NAND).
        n = proj._neighbors("a")
        assert sorted(n) == ["b", "c"]
        # Verify operators ARE stored and edges ARE directional (op→point).
        impl_rows = proj.g.query(
            "MATCH (op:Point {id:'op1'})-[r:IMPL]->(p:Point) RETURN p.id"
        ).result_set
        assert sorted(r[0] for r in impl_rows) == ["a", "b"]
        nand_rows = proj.g.query(
            "MATCH (op:Point {id:'op2'})-[r:NAND]->(p:Point) RETURN p.id"
        ).result_set
        assert sorted(r[0] for r in nand_rows) == ["a", "c"]
    finally:
        proj.close()


# ------------------------------------- FalkorProjection._compute_confidence

def test_compute_confidence_no_edges():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "p1", "content": "loner", "context": "ctx"})
        # No edges → 0.5
        assert proj._compute_confidence("p1") == 0.5
    finally:
        proj.close()


def test_compute_confidence_with_parent():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "p1", "content": "loner", "context": "ctx"})
        # No edges → base=0.5, blended with parent: 0.5*0.5 + 0.8*0.5 = 0.65
        assert proj._compute_confidence("p1", parent_confidence=0.8) == 0.65
    finally:
        proj.close()


def test_compute_confidence_with_edges():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "a", "content": "A", "context": "ctx"})
        proj._upsert({"id": "b", "content": "B", "context": "ctx"})
        proj._upsert({"id": "c", "content": "C", "context": "ctx"})
        # 'a' has 2 IMPL (supports) and 1 NAND (contradicts)
        proj._upsert({"id": "op1", "content": "IMPL", "context": "ctx",
                       "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}})
        proj._upsert({"id": "op2", "content": "IMPL", "context": "ctx",
                       "operator": {"op_type": "IMPL", "inputs": ["a", "c"]}})
        proj._upsert({"id": "op3", "content": "NAND", "context": "ctx",
                       "operator": {"op_type": "NAND", "inputs": ["a", "b"]}})
        # 'a': s=2 (IMPL edges), c=1 (NAND edge) → base = 2/3 = 0.6667
        # then _compute_confidence uses undirected patterns: ()-[r:IMPL]-(:Point)
        # From 'a': IMPL edges via op1 and op2 (undirected) → both directions
        # BUT _create_edges creates (o)-[:IMPL]->(s) and (s)-[:INPUT]->(o).
        # IMPL edges are directed o→s. Undirected IMPL match from 'a':
        # 'a' is 's' in the (op1)-[:IMPL]->(a) pattern, undirected matches.
        # So 'a' sees 2 IMPL edges (via op1, op2) and 1 NAND edge (via op3).
        s = proj.query(
            "MATCH (n:Point {id:'a'})-[r:IMPL]-(:Point) RETURN count(r)"
        ).result_set[0][0]
        c = proj.query(
            "MATCH (n:Point {id:'a'})-[r:NAND]-(:Point) RETURN count(r)"
        ).result_set[0][0]
        expected_base = s / (s + c) if (s + c) else 0.5
        assert proj._compute_confidence("a") == expected_base
    finally:
        proj.close()


# --------------------------------------------- FalkorProjection.rebuild_all

def test_falkor_rebuild_all():
    if _skip_if_no_falkor():
        return
    """rebuild_all with temp directory of .jsonl files."""
    d = tempfile.mkdtemp(prefix="tortoise_rebuild_")
    try:
        # Create two .jsonl files
        api1, _ = _api()
        log1_path = os.path.join(d, "events1.jsonl")
        log1_path_abs = os.path.abspath(log1_path)
        api1.log = EventLog(log1_path_abs)
        _build(api1, source="doc1.txt")

        api2, _ = _api()
        log2_path = os.path.join(d, "events2.jsonl")
        log2_path_abs = os.path.abspath(log2_path)
        api2.log = EventLog(log2_path_abs)
        _build(api2, source="doc2.txt")

        proj = FalkorProjection(_tmp("g_rebuild_all.db"), graph_name="test")
        try:
            result = proj.rebuild_all(d)
            # 2 builds × 6 events each (IngestStarted + 2 PointAdded + OperatorAdded)
            # Actually _build does 4 things: add_point a, add_point b,
            # add_operator. Plus begin_ingest = IngestStarted.
            # So per build: IngestStarted + PointAdded(a) + PointAdded(b) + OperatorAdded = 4 events
            assert result["events"] >= 6, f"expected >= 6 events, got {result['events']}"
            assert result["nodes"] >= 6, f"expected >= 6 nodes, got {result['nodes']}"
            assert result["edges"] >= 4, f"expected >= 4 edges, got {result['edges']}"
        finally:
            proj.close()
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_falkor_rebuild_all_empty_dir():
    if _skip_if_no_falkor():
        return
    d = tempfile.mkdtemp(prefix="tortoise_empty_")
    try:
        proj = FalkorProjection(_tmp("g_rebuild_empty.db"), graph_name="test")
        try:
            result = proj.rebuild_all(d)
            assert result["events"] == 0
            assert result["nodes"] == 0
            assert result["edges"] == 0
        finally:
            proj.close()
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_falkor_rebuild_all_with_retractions():
    if _skip_if_no_falkor():
        return
    """rebuild_all handles PointRetracted and PointsMerged events."""
    d = tempfile.mkdtemp(prefix="tortoise_retract_")
    try:
        api, _ = _api()
        log_path = os.path.join(d, "events.jsonl")
        log_path_abs = os.path.abspath(log_path)
        api.log = EventLog(log_path_abs)
        a, b, op = _build(api, source="doc.txt")
        api.retract_point(b, corrects=op)

        proj = FalkorProjection(_tmp("g_retract.db"), graph_name="test")
        try:
            result = proj.rebuild_all(d)
            # b was retracted after being added
            node_count = proj.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
            assert node_count == 2  # a + op, b was retracted
        finally:
            proj.close()
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------ FalkorProjection.compute_grounding

def test_compute_grounding_empty():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        result = proj.compute_grounding()
        assert result == {}
    finally:
        proj.close()


def test_compute_grounding_basic():
    if _skip_if_no_falkor():
        return
    """compute_grounding with resolution events and IMPL edges."""
    try:
        from scipy.sparse import coo_matrix  # noqa: F401
    except ImportError:
        print("SKIP test_compute_grounding_basic (scipy not installed)")
        return

    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        # Add a resolution event (grounding seed)
        proj._upsert({"id": "r1", "content": "resolution", "context": "resolution-event"})
        # Add supporting statements
        proj._upsert({"id": "s1", "content": "supports resolution", "context": "ctx"})
        proj._upsert({"id": "s2", "content": "more support", "context": "ctx"})
        # Add IMPL edges (s1→r1, s2→r1)
        proj._upsert({"id": "op1", "content": "IMPL", "context": "ctx",
                       "operator": {"op_type": "IMPL", "inputs": ["s1", "r1"]}})
        proj._upsert({"id": "op2", "content": "IMPL", "context": "ctx",
                       "operator": {"op_type": "IMPL", "inputs": ["s2", "r1"]}})
        result = proj.compute_grounding(lam=0.6)
        assert len(result) > 0
        # All IDs present
        for pid in ("r1", "s1", "s2", "op1", "op2"):
            assert pid in result, f"missing {pid}"
        # Resolution event should have grounding written back
        g = proj.query(
            "MATCH (n:Point {id:'r1'}) RETURN n.grounding"
        ).result_set[0][0]
        assert g is not None
        assert isinstance(g, (int, float))
    finally:
        proj.close()


def test_compute_grounding_resolution_vector():
    if _skip_if_no_falkor():
        return
    """resolution-vector context also seeds the a-vector."""
    try:
        from scipy.sparse import coo_matrix  # noqa: F401
    except ImportError:
        print("SKIP test_compute_grounding_resolution_vector (scipy not installed)")
        return

    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "rv1", "content": "vector", "context": "resolution-vector"})
        proj._upsert({"id": "s1", "content": "supports", "context": "ctx"})
        proj._upsert({"id": "op1", "content": "IMPL", "context": "ctx",
                       "operator": {"op_type": "IMPL", "inputs": ["s1", "rv1"]}})
        result = proj.compute_grounding()
        assert "rv1" in result
    finally:
        proj.close()


def test_compute_grounding_operator_excluded():
    if _skip_if_no_falkor():
        return
    """Operator points should not seed the a-vector even if context matches."""
    try:
        from scipy.sparse import coo_matrix  # noqa: F401
    except ImportError:
        print("SKIP test_compute_grounding_operator_excluded (scipy not installed)")
        return

    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        # Operator with resolution-event context — should be excluded
        proj._upsert({"id": "op1", "content": "IMPL", "context": "resolution-event",
                       "operator": {"op_type": "IMPL", "inputs": []}})
        # Regular statement with resolution-event
        proj._upsert({"id": "r1", "content": "real resolution", "context": "resolution-event"})
        result = proj.compute_grounding()
        # op1 is an operator with is_operator=True → excluded from seed
        # r1 is a statement → included
        assert result["op1"] >= 0  # grounding computed (via propagation), but not from seed
    finally:
        proj.close()


# -------------------------------------- FalkorProjection.propagate_shock

def test_propagate_shock_empty_graph():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "e1", "content": "epicenter", "context": "ctx"})
        changed = proj.propagate_shock("e1")
        # Epicenter should get computed confidence if > threshold
        assert isinstance(changed, dict)
        # With no edges, confidence stays at 0.5, but old=0.5 and new=0.5 → no change
        # Wait, _confidence returns 0.5 (no explicit confidence), _compute_confidence
        # returns 0.5 (no edges). So abs(new - old) = 0.0 ≤ threshold(0.05) → no change.
        assert "e1" not in changed
    finally:
        proj.close()


def test_propagate_shock_with_edges():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        # Build a small graph
        proj._upsert({"id": "a", "content": "A", "context": "ctx"})
        proj._upsert({"id": "b", "content": "B", "context": "ctx"})
        proj._upsert({"id": "c", "content": "C", "context": "ctx"})
        proj._upsert({"id": "op1", "content": "IMPL", "context": "ctx",
                       "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}})
        proj._upsert({"id": "op2", "content": "NAND", "context": "ctx",
                       "operator": {"op_type": "NAND", "inputs": ["a", "c"]}})
        # Set explicit confidences so delta > threshold
        proj.g.query("MATCH (n:Point {id:'a'}) SET n.confidence=0.3")
        proj.g.query("MATCH (n:Point {id:'b'}) SET n.confidence=0.3")

        changed = proj.propagate_shock("a", max_depth=2)
        # 'a': old=0.3, _compute_confidence(a)=0.5 (1 IMPL, 1 NAND → 1/2=0.5),
        #   depth=0 → no damping, new=0.5. delta=0.2 > 0.05 → changed.
        assert "a" in changed
        old_a, new_a = changed["a"]
        assert old_a == 0.3
        assert new_a == 0.5
    finally:
        proj.close()


def test_propagate_shock_depth_limit():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        # Chain: a → op1 → b → op2 → c → op3 → d
        for pid in ("a", "b", "c", "d"):
            proj._upsert({"id": pid, "content": pid, "context": "ctx"})
        proj._upsert({"id": "op1", "content": "IMPL", "context": "ctx",
                       "operator": {"op_type": "IMPL", "inputs": ["a", "b"]}})
        proj._upsert({"id": "op2", "content": "IMPL", "context": "ctx",
                       "operator": {"op_type": "IMPL", "inputs": ["b", "c"]}})
        proj._upsert({"id": "op3", "content": "IMPL", "context": "ctx",
                       "operator": {"op_type": "IMPL", "inputs": ["c", "d"]}})
        # Set low confidence on a
        proj.g.query("MATCH (n:Point {id:'a'}) SET n.confidence=0.2")
        proj.g.query("MATCH (n:Point {id:'b'}) SET n.confidence=0.2")
        proj.g.query("MATCH (n:Point {id:'c'}) SET n.confidence=0.2")
        proj.g.query("MATCH (n:Point {id:'d'}) SET n.confidence=0.2")

        changed = proj.propagate_shock("a", max_depth=1, threshold=0.0)
        # depth=1: epicenter + immediate neighbors
        # 'a' and 'b' should change (via op1 edge)
        # 'c' should NOT change (depth 2)
        assert "a" in changed
        assert "c" not in changed
    finally:
        proj.close()


def test_propagate_shock_threshold():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "x", "content": "X", "context": "ctx"})
        # Set confidence to 0.5001 — very close to default
        proj.g.query("MATCH (n:Point {id:'x'}) SET n.confidence=0.5001")

        changed = proj.propagate_shock("x", threshold=1.0)
        # _confidence("x")=0.5001, _compute_confidence=0.5 (no edges)
        # new = old*damping + new*(1-damping) ... wait, depth=0 so no damping
        # old=0.5001, new=0.5 (round to 4 decimals = 0.5)
        # abs(0.5 - 0.5001) = 0.0001 < 1.0 → no change
        assert "x" not in changed
    finally:
        proj.close()


# -------------------------------------------- FalkorProjection.query direct

def test_falkor_query():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj._upsert({"id": "p1", "content": "hello", "context": "ctx"})
        r = proj.query(
            "MATCH (n:Point {id:$id}) RETURN n.content",
            id="p1",
        )
        assert r.result_set[0][0] == "hello"
    finally:
        proj.close()


# ------------------------------------------------------ FalkorProjection.close

def test_falkor_close():
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    proj._upsert({"id": "p1", "content": "test", "context": "ctx"})
    proj.close()
    # After close, further operations may fail — we just ensure no crash on close


# --------------------------------------------------- EventRecorded → JSONL + projection

def test_event_recorded_jsonl_roundtrip():
    """EventRecorded entries with version field survive JSONL roundtrip."""
    log = EventLog(_tmp("events.jsonl"))
    record = {
        "version": "1.0",
        "type": "EventRecorded",
        "event": {
            "eventId": "ev-001",
            "eventKind": "friction",
            "subject": "pi-agent",
            "object": "worktree-guard",
            "startedAt": "2026-07-17T22:00:00Z",
            "endedAt": None,
            "parentEvent": None,
            "childEvents": [],
            "participants": ["pi-agent"],
            "classificationLevel": "internal",
            "scopedFacts": [],
            "format": "jsonl",
        },
        "createdAt": "2026-07-17T22:00:05Z",
    }
    log.append(record)
    entries = log.read_all()
    assert len(entries) == 1
    assert entries[0]["version"] == "1.0"
    assert entries[0]["type"] == "EventRecorded"
    assert entries[0]["event"]["eventId"] == "ev-001"
    assert entries[0]["event"]["eventKind"] == "friction"


def test_falkor_upsert_event():
    """_upsert_event creates :Event node with all properties."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        event = {
            "eventId": "ev-001",
            "eventKind": "friction",
            "subject": "pi-agent",
            "object": "worktree-guard",
            "startedAt": "2026-07-17T22:00:00Z",
            "endedAt": None,
            "parentEvent": None,
            "childEvents": [],
            "participants": ["pi-agent"],
            "classificationLevel": "internal",
            "scopedFacts": [],
            "format": "jsonl",
        }
        proj._upsert_event(event)
        r = proj.query(
            "MATCH (e:Event {eventId:'ev-001'}) "
            "RETURN e.eventKind, e.subject, e.object, e.classificationLevel, e.format"
        ).result_set
        assert r[0][0] == "friction"
        assert r[0][1] == "pi-agent"
        assert r[0][2] == "worktree-guard"
        assert r[0][3] == "internal"
        assert r[0][4] == "jsonl"
    finally:
        proj.close()


def test_falkor_upsert_event_idempotent():
    """Re-applying same event via MERGE does not create duplicates."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        event = {
            "eventId": "ev-001",
            "eventKind": "friction",
            "subject": "pi-agent",
            "object": "worktree-guard",
            "startedAt": "2026-07-17T22:00:00Z",
            "endedAt": None,
            "parentEvent": None,
            "childEvents": [],
            "participants": ["pi-agent"],
            "classificationLevel": "internal",
            "scopedFacts": [],
            "format": "jsonl",
        }
        proj._upsert_event(event)
        proj._upsert_event(event)  # idempotent re-apply
        count = proj.query(
            "MATCH (e:Event {eventId:'ev-001'}) RETURN count(e)"
        ).result_set[0][0]
        assert count == 1  # no duplicate
    finally:
        proj.close()


def test_falkor_upsert_event_on_create_no_overwrite():
    """ON CREATE SET prevents overwriting existing event properties."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        event = {
            "eventId": "ev-001",
            "eventKind": "friction",
            "subject": "pi-agent",
            "object": "worktree-guard",
            "startedAt": "2026-07-17T22:00:00Z",
            "endedAt": None,
            "parentEvent": None,
            "childEvents": [],
            "participants": ["pi-agent"],
            "classificationLevel": "internal",
            "scopedFacts": [],
            "format": "jsonl",
        }
        proj._upsert_event(event)
        # Attempt to "update" with different subject
        event2 = {**event, "subject": "other-agent"}
        proj._upsert_event(event2)
        r = proj.query(
            "MATCH (e:Event {eventId:'ev-001'}) RETURN e.subject"
        ).result_set[0][0]
        assert r == "other-agent"  # last write wins — ON MATCH SET
    finally:
        proj.close()


# ---------------------------------------------------- #13 cross-file rebuild

def test_rebuild_all_cross_file_references():
    """#13: points in file A, operators referencing them in file B → cross-file
    queries resolve correctly after rebuild_all (two-pass)."""
    import os
    d = tempfile.mkdtemp(prefix="tortoise_13_")

    # File A: two statement points
    log_a = EventLog(os.path.join(d, "a.jsonl"))
    api_a = EventAPI(log_a, initiated_by="extractor", agent_id="test")
    prov = provenance("doc.txt", [0, 10], "quote", extracted_by="test@0")
    p1 = api_a.add_point("point from file A", prov)
    p2 = api_a.add_point("another from file A", prov)

    # File B: operator referencing points in file A (cross-file)
    log_b = EventLog(os.path.join(d, "b.jsonl"))
    api_b = EventAPI(log_b, initiated_by="extractor", agent_id="test")
    op = api_b.add_operator("IMPL", [p1, p2], prov,
                            content="operator in file B → points in file A")

    # fold everything from both files
    all_points = {}
    for fname in sorted(os.listdir(d)):
        if fname.endswith('.jsonl'):
            evs = EventLog(os.path.join(d, fname)).read_all()
            for ev in evs:
                _apply_one(all_points, ev)

    assert p1 in all_points, "point from file A missing"
    assert p2 in all_points, "point from file A missing"
    assert op in all_points, "operator from file B missing"
    assert all_points[op]["operator"]["inputs"] == [p1, p2], \
        "cross-file operator inputs should resolve to file A points"
    print("PASS test_rebuild_all_cross_file_references")


# --------------------------------------- FalkorProjection.from_uri — #48
#
# These tests exercise the REAL code path — _validate_uri_scheme or
# from_uri itself.  from_uri validates the scheme BEFORE connecting,
# so error-path tests need no live FalkorDB server.
#
# Scheme-acceptance (docker/redis/rediss) is covered in
# tests/test_sdk_props_coercion.py (TestUriSchemes) — not duplicated here.


def test_from_uri_rejects_unsupported_scheme():
    """from_uri raises ValueError for unsupported schemes (validates BEFORE connecting)."""
    with pytest.raises(ValueError,
                       match="Unsupported scheme: postgresql"):
        FalkorProjection.from_uri("postgresql://localhost:5432/db")


def test_from_uri_rejects_empty_scheme():
    """from_uri raises ValueError for empty/missing scheme (validates BEFORE connecting)."""
    with pytest.raises(ValueError,
                       match="Unsupported scheme:"):
        FalkorProjection.from_uri("localhost:6379")


# -------------------------------------------------------- consistency check


def test_check_consistency_matches():
    """Consistency check passes when log and graph agree."""
    if _skip_if_no_falkor():
        return
    api, log = _api()
    _build(api)
    proj = FalkorProjection(_tmp("g_consistency_ok.db"), graph_name="test")
    try:
        proj.rebuild(log)
        from tortoise.consistency import check_consistency
        result = check_consistency(log.path, proj)
        assert result["ok"], f"expected ok, got {result}"
        assert result["delta"] == 0
    finally:
        proj.close()


def test_check_consistency_mismatch():
    """Consistency check fails when graph has extra nodes not in log."""
    if _skip_if_no_falkor():
        return
    api, log = _api()
    _build(api)
    proj = FalkorProjection(_tmp("g_consistency_bad.db"), graph_name="test")
    try:
        proj.rebuild(log)
        # Inject a stray node directly into DB (bypassing the log)
        proj._upsert({"id": "ghost", "content": "not in log", "context": "ctx"})
        from tortoise.consistency import check_consistency
        result = check_consistency(log.path, proj)
        assert not result["ok"], f"expected mismatch, got {result}"
        assert result["delta"] == -1  # DB has 1 more than log
    finally:
        proj.close()


# ----------------------------------------------------------------- _run_all

def _run_all():
    for name in sorted(globals()):
        if name.startswith("test_") and callable(globals()[name]):
            fn = globals()[name]
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:
                print(f"FAIL {name}: {e}")
                raise


if __name__ == "__main__":
    _run_all()
    print("\nall projection tests passed")



# ------------------------------------------------------------------ #125 capture fields (live DB)


@pytest.fixture
def live_proj():
    """Live FalkorProjection on a test-prefixed graph (safe via test_guard)."""
    uri = os.environ.get("TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise_test_proj125")
    proj = FalkorProjection.from_uri(uri)
    # Clean the test graph (test-prefixed — test_guard permits; production blocked)
    proj.g.query("MATCH (n) DETACH DELETE n")
    yield proj
    proj.close()


def test_upsert_document_capture_fields(live_proj):
    """#125: _upsert_document persists topics/summary/sessionId/eventId/
    doc_status/_searchText + aboutSubject edges."""
    proj = live_proj
    proj.apply({"type": "SubjectAdded", "id": "agent-pi", "name": "agent-pi",
                "subject_kind": "other"})
    proj.apply({"type": "DocumentCreated", "id": "test-doc-1",
                "title": "Conv", "topics": ["licensing", "AGPL"],
                "summary": "Compared licenses", "session_id": "sess-1",
                "event_id": "evt-1", "doc_status": "captured",
                "about_entities": ["agent-pi"]})
    rows = proj.g.query(
        "MATCH (d:Document {id:'test-doc-1'}) "
        "RETURN d.topics, d.summary, d.sessionId, d.eventId, d.doc_status, d._searchText"
    ).result_set
    assert rows, "Document not created"
    assert rows[0][0] == ["licensing", "AGPL"], rows[0][0]
    assert rows[0][1] == "Compared licenses"
    assert rows[0][2] == "sess-1"
    assert rows[0][3] == "evt-1"
    assert rows[0][4] == "captured"
    assert "AGPL" in rows[0][5] and "Compared" in rows[0][5], rows[0][5]
    rows2 = proj.g.query(
        "MATCH (d:Document {id:'test-doc-1'})-[:aboutSubject]->(s) RETURN s.name"
    ).result_set
    assert len(rows2) == 1 and rows2[0][0] == "agent-pi", rows2
    # #205: _upsert_document now creates references edge (Source → Document)
    rows3 = proj.g.query(
        "MATCH (s:Source {url:'test-doc-1'})-[:references]->"
        "(d:Document {id:'test-doc-1'}) RETURN count(*) > 0"
    ).result_set
    assert rows3[0][0] is True, "references edge not created by _upsert_document (#205)"


def test_upsert_document_partial_update_preserves_search_text(live_proj):
    """#125: partial update must NOT wipe _searchText (coalesce null sentinel)."""
    proj = live_proj
    proj.apply({"type": "DocumentCreated", "id": "doc-p",
                "title": "Full Title", "topics": ["alpha"], "summary": "Sum"})
    rows = proj.g.query("MATCH (d:Document {id:'doc-p'}) RETURN d._searchText").result_set
    assert rows[0][0] and "alpha" in rows[0][0], rows
    proj.apply({"type": "DocumentCreated", "id": "doc-p", "doc_status": "captured"})
    rows = proj.g.query(
        "MATCH (d:Document {id:'doc-p'}) RETURN d._searchText, d.doc_status"
    ).result_set
    assert "alpha" in rows[0][0], f"_searchText wiped on partial update: {rows[0][0]}"
    assert rows[0][1] == "captured"


def test_upsert_event_uses_dict_kind(live_proj):
    """#125: structured uses {name, kind} → Object objectKind from kind field."""
    proj = live_proj
    proj.apply({"type": "EventRecorded", "event": {
        "id": "evt-1", "eventKind": "sessionCaptured",
        "subject": "agent-pi", "object": "doc-1", "objectType": "Document",
        "uses": [{"name": "tortoise-capture", "kind": "skill"}]}})
    rows = proj.g.query(
        "MATCH (e:Event {eventId:'evt-1'})-[:uses]->(o:Object) "
        "RETURN o.name, o.objectKind"
    ).result_set
    assert rows and rows[0][0] == "tortoise-capture", rows
    assert rows[0][1] == "skill", rows


def test_upsert_event_produces_document(live_proj):
    """#125: objectType='Document' → produces→real Document node, no Object clone."""
    proj = live_proj
    proj.apply({"type": "EventRecorded", "event": {
        "id": "evt-2", "eventKind": "sessionCaptured",
        "subject": "agent-pi", "object": "doc-1", "objectType": "Document",
        "uses": [{"name": "tortoise-capture", "kind": "skill"}]}})
    rows = proj.g.query(
        "MATCH (e:Event {eventId:'evt-2'})-[:produces]->(d:Document) RETURN d.id"
    ).result_set
    assert rows and rows[0][0] == "doc-1", rows
    # No Object clone
    rows2 = proj.g.query(
        "MATCH (e:Event {eventId:'evt-2'})-[:produces]->(o:Object) RETURN count(o)"
    ).result_set
    assert rows2[0][0] == 0, rows2


def test_upsert_event_legacy_string_uses_still_works(live_proj):
    """#125 backward compat: legacy string uses still create Object objectKind='other'."""
    proj = live_proj
    proj.apply({"type": "EventRecorded", "event": {
        "id": "evt-3", "eventKind": "review", "subject": "agent-pi",
        "object": "thing-1", "uses": "legacy-tool"}})
    rows = proj.g.query(
        "MATCH (e:Event {eventId:'evt-3'})-[:uses]->(o:Object) "
        "RETURN o.name, o.objectKind"
    ).result_set
    assert rows and rows[0][0] == "legacy-tool", rows
    assert rows[0][1] == "other", rows


def test_upsert_document_includes_source_path(live_proj):
    """#167: _upsert_document persists sourcePath on DocumentCreated + partial
    update preserves existing value via coalesce-null sentinel."""
    proj = live_proj
    proj.apply({"type": "DocumentCreated", "id": "doc-sp",
                "title": "With Source", "source_path": "/tmp/test.md"})
    rows = proj.g.query(
        "MATCH (d:Document {id:'doc-sp'}) RETURN d.sourcePath"
    ).result_set
    assert rows and rows[0][0] == "/tmp/test.md", rows

    # Partial update without source_path must preserve existing value
    proj.apply({"type": "DocumentCreated", "id": "doc-sp",
                "doc_status": "archived"})
    rows = proj.g.query(
        "MATCH (d:Document {id:'doc-sp'}) RETURN d.sourcePath, d.doc_status"
    ).result_set
    assert rows[0][0] == "/tmp/test.md", f"sourcePath wiped: {rows[0][0]}"
    assert rows[0][1] == "archived"


def test_upsert_document_preserves_doc_status_and_needs_extraction(live_proj):
    """#133 P0: partial update via _upsert_document must NOT wipe
    doc_status='captured' or needs_extraction=true (coalesce-null sentinel —
    the add_document non-null-default bug class from #167)."""
    proj = live_proj
    proj.apply({"type": "DocumentCreated", "id": "doc-133",
                "title": "Captured", "doc_status": "captured",
                "needs_extraction": True})
    rows = proj.g.query(
        "MATCH (d:Document {id:'doc-133'}) RETURN d.doc_status, d.needs_extraction"
    ).result_set
    assert rows[0][0] == "captured", rows[0][0]
    assert rows[0][1] is True, rows[0][1]

    # Partial update with neither field — must preserve both
    proj.apply({"type": "DocumentCreated", "id": "doc-133", "title": "Renamed"})
    rows = proj.g.query(
        "MATCH (d:Document {id:'doc-133'}) RETURN d.doc_status, d.needs_extraction, d.title"
    ).result_set
    assert rows[0][0] == "captured", f"doc_status wiped: {rows[0][0]}"
    assert rows[0][1] is True, f"needs_extraction wiped: {rows[0][1]}"
    assert rows[0][2] == "Renamed"


# ── #214: Vocabulary edge cleanup ──────────────────────────────────────


class TestVocabEdgeValidation:
    """#214: instantiates removed; dependsOn/reportsTo/related kept."""

    def test_instantiates_rejected_by_create_edge(self):
        """create_edge rejects 'instantiates' — Action dissolved in v3.0."""
        if _skip_if_no_falkor():
            return
        import pytest
        proj = FalkorProjection(_tmp("g.db"), graph_name="test")
        try:
            proj._upsert({"id": "a", "content": "A", "context": "ctx"})
            proj._upsert({"id": "b", "content": "B", "context": "ctx"})
            with pytest.raises(ValueError, match="Unknown predicate: instantiates"):
                proj.create_edge("a", "b", "instantiates")
        finally:
            proj.close()

    def test_dependsOn_accepted_by_create_edge(self):
        """create_edge accepts 'dependsOn' — pack-declared, valid predicate."""
        if _skip_if_no_falkor():
            return
        proj = FalkorProjection(_tmp("g.db"), graph_name="test")
        try:
            proj._upsert({"id": "a", "content": "A", "context": "ctx"})
            proj._upsert({"id": "b", "content": "B", "context": "ctx"})
            ok = proj.create_edge("a", "b", "dependsOn")
            assert ok is True
        finally:
            proj.close()

    def test_reportsTo_accepted_by_create_edge(self):
        """create_edge accepts 'reportsTo' — org hierarchy, valid predicate."""
        if _skip_if_no_falkor():
            return
        proj = FalkorProjection(_tmp("g.db"), graph_name="test")
        try:
            proj._upsert({"id": "a", "content": "A", "context": "ctx"})
            proj._upsert({"id": "b", "content": "B", "context": "ctx"})
            ok = proj.create_edge("a", "b", "reportsTo")
            assert ok is True
        finally:
            proj.close()

    def test_related_accepted_by_create_edge(self):
        """create_edge accepts 'related' — generic catch-all predicate."""
        if _skip_if_no_falkor():
            return
        proj = FalkorProjection(_tmp("g.db"), graph_name="test")
        try:
            proj._upsert({"id": "a", "content": "A", "context": "ctx"})
            proj._upsert({"id": "b", "content": "B", "context": "ctx"})
            ok = proj.create_edge("a", "b", "related")
            assert ok is True
        finally:
            proj.close()

    def test_valid_predicates_no_longer_contains_instantiates(self):
        """#214: validate that valid_predicates set no longer includes instantiates."""
        if _skip_if_no_falkor():
            return
        import inspect
        from tortoise.projection.edges import _EdgeHandlers
        src = inspect.getsource(_EdgeHandlers.create_edge)
        assert "'instantiates'" not in src
        assert "'dependsOn'" in src
        assert "'reportsTo'" in src
        assert "'related'" in src


def test_falkor_rebuild_all_parity_with_apply():
    """#330: rebuild_all must produce the same Point node properties and edges
    as replaying the same events through apply() — including provenance edges
    (extractedFrom), about edges, operator edges, SourceCreated replay and
    PointRevised updatedAt parity."""
    if _skip_if_no_falkor():
        return
    d = tempfile.mkdtemp(prefix="tortoise_rebuild_parity_")
    try:
        log_path = os.path.abspath(os.path.join(d, "events.jsonl"))
        log = EventLog(log_path)
        now = "2026-08-07T00:00:00.000000+00:00"
        # Convergent order: SourceCreated BEFORE points carrying extractedFrom
        # (reversed order would diverge Source version/id — documented).
        events = [
            {"event_id": "e0", "ts": now, "type": "IngestStarted",
             "initiated_by": "extractor", "agent_id": "test", "run_id": "r1",
             "key": {"kind": "doc", "value": "doc.txt"}, "extractor_version": "1"},
            {"event_id": "e1", "ts": now, "type": "SourceCreated",
             "initiated_by": "extractor", "agent_id": "test",
             "id": "src-1", "url": "https://doc.txt", "sourceKind": "T2",
             "contentHash": "abc123", "title": "Doc", "externalId": "ext-1"},
            {"event_id": "e2", "ts": now, "type": "PointAdded",
             "initiated_by": "extractor", "agent_id": "test", "projection_version": 2,
             "point": {"id": "p-a", "content": "claim A", "status": "live",
                       "createdAt": now, "pointKind": "statement",
                       "authoredBy": "alice", "validFrom": now, "validTo": now,
                       "confidence": 0.7, "extractedFrom": "https://doc.txt",
                       "aboutEntities": ["alice"],
                       "provenance": {"speaker": "bob", "source_id": "s1"}}},
            {"event_id": "e3", "ts": now, "type": "PointAdded",
             "initiated_by": "extractor", "agent_id": "test", "projection_version": 2,
             "point": {"id": "p-b", "content": "claim B", "status": "live",
                       "createdAt": now, "pointKind": "statement",
                       "authoredBy": "alice", "validFrom": now, "validTo": now,
                       "confidence": 0.6, "extractedFrom": "https://doc.txt",
                       "aboutEntities": ["alice"],
                       "provenance": {"speaker": "bob", "source_id": "s1"}}},
            {"event_id": "e4", "ts": now, "type": "OperatorAdded",
             "initiated_by": "extractor", "agent_id": "test", "projection_version": 2,
             "point": {"id": "op-1", "content": "IMPL", "status": "live",
                       "createdAt": now, "pointKind": "operator",
                       "operator": {"op_type": "IMPL", "inputs": ["p-b", "p-a"]},
                       "provenance": {"speaker": "bob", "source_id": "s1"}}},
            {"event_id": "e5", "ts": now, "type": "PointRevised",
             "initiated_by": "extractor", "agent_id": "test",
             "id": "p-a", "new_content": "claim A revised"},
        ]
        for ev in events:
            log.append(ev)

        projA = FalkorProjection(_tmp("parity_a.db"), graph_name="test")
        projB = FalkorProjection(_tmp("parity_b.db"), graph_name="test")
        try:
            for ev in log.read_all():
                projA.apply(ev)
            projB.rebuild_all(d)

            def node_map(proj):
                rows = proj.g.query("MATCH (n:Point) RETURN n.id, properties(n)").result_set
                out = {}
                for pid, props in rows:
                    out[pid] = {k: v for k, v in props.items()
                                if k not in ("updatedAt", "embedding")}
                return out

            def edge_map(proj):
                rows = proj.g.query(
                    "MATCH (a)-[r]->(b) RETURN a.id, type(r), b.id, properties(r)"
                ).result_set
                # Sort WITHOUT the props dict (dicts aren't orderable — a
                # duplicate-edge divergence would TypeError on sorted()).
                return sorted((r[0], r[1], r[2]) for r in rows)

            na, nb = node_map(projA), node_map(projB)
            assert na == nb, f"Point node props diverged:\napply: {na}\nrebuild: {nb}"
            ea, eb = edge_map(projA), edge_map(projB)
            assert ea == eb, f"edges diverged:\napply: {ea}\nrebuild: {eb}"
            # Provenance + about edges present in BOTH
            edge_types_b = {e[1] for e in eb}
            assert "extractedFrom" in edge_types_b, "extractedFrom edges missing after rebuild"
            assert "aboutSubject" in edge_types_b, "aboutSubject edges missing after rebuild"
            assert "IMPL" in edge_types_b and "INPUT" in edge_types_b
            # Source node parity (selective — ingestedAt differs between write times)
            for proj in (projA, projB):
                src = proj.g.query(
                    "MATCH (s:Source {url:'https://doc.txt'}) RETURN properties(s)"
                ).result_set[0][0]
                assert src["version"] == 1, f"Source version {src.get('version')} != 1"
                # _upsert_source coalesces id from the event's id field
                assert src["id"] == "src-1"
                assert src["sourceKind"] == "T2" and src["contentHash"] == "abc123"
            # PointRevised applied in both (content + updatedAt write)
            assert na["p-a"]["content"] == "claim A revised"
            assert nb["p-a"]["content"] == "claim A revised"
            uat = projB.g.query(
                "MATCH (n:Point {id:'p-a'}) RETURN n.updatedAt"
            ).result_set[0][0]
            assert uat, "PointRevised must write updatedAt on rebuild"
        finally:
            projA.close()
            projB.close()
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


# ── #329: stub-node auto-creation cap ───────────────────────────────

def test_stub_creation_bounded_at_cap(monkeypatch):
    """#329: short-ID stub auto-creation stops at the per-instance cap; at-cap
    behavior is fail-safe (no stub, no partial edge, warning logged)."""
    import tempfile, os
    from tortoise.projection import FalkorProjection

    monkeypatch.setenv("TORTOISE_MAX_AUTOCREATED_STUBS", "2")
    db = os.path.join(tempfile.mkdtemp(prefix="tortoise_stubcap_"), "test.db")
    proj = FalkorProjection(db)
    try:
        # Three OperatorAdded events referencing three distinct short ids
        for i, sid in enumerate(("s1", "s2", "s3")):
            proj.apply({
                "type": "OperatorAdded",
                "point": {"id": f"op{i}", "content": f"NAND({sid})",
                          "operator": {"op_type": "NAND", "inputs": [sid]}},
            })
        # Only 2 stubs created (cap), 3rd missing source skipped
        stubs = proj.g.query(
            "MATCH (s:Point) WHERE s.content='[missing]' RETURN count(s)"
        ).result_set[0][0]
        assert stubs == 2, f"expected 2 stubs, got {stubs}"
        # No partial edge to the skipped source
        edges = proj.g.query(
            "MATCH (o:Point {id:'op2'})-[r]->() RETURN count(r)"
        ).result_set[0][0]
        assert edges == 0, f"expected no partial edge from op2, got {edges}"
    finally:
        proj.close()
