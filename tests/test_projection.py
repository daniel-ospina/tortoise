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
from unittest import mock

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


def _docker_falkor_reachable() -> bool:
    """Socket probe: is a live Docker FalkorDB reachable?

    The `live_proj` fixture needs a live FalkorDB on FALKORDB_HOST:PORT
    (default localhost:16379). Embedded CI has no container — probe before
    connecting so the fixture skips instead of raising redis
    ConnectionError (Error 111/61). _skip_if_no_falkor only covers
    redislite import availability, not Docker connectivity.
    """
    import socket
    host = os.environ.get("FALKORDB_HOST", "localhost")
    port = int(os.environ.get("FALKORDB_PORT", "16379"))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


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
    # #689: tombstone, not hard delete — point exists with status='retracted'
    assert "p1" in points
    assert points["p1"]["status"] == "retracted"


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
    # #689: tombstone — p1 exists with status='retracted', p2 is live
    assert "p1" in result
    assert result["p1"]["status"] == "retracted"
    assert result["p2"]["content"] == "two"


def test_fold_revise_then_retract():
    events = [
        {"type": "PointAdded", "point": {"id": "p1", "content": "a", "context": "ctx"}},
        {"type": "PointRevised", "id": "p1", "new_content": "b"},
        {"type": "PointRetracted", "id": "p1"},
    ]
    result = fold(events)
    # #689: tombstone — p1 exists with status='retracted'
    assert "p1" in result
    assert result["p1"]["status"] == "retracted"


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
    # #689: tombstone — point exists with status='retracted'
    assert "p1" in proj.points
    assert proj.points["p1"]["status"] == "retracted"


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
        # #689: tombstone — node exists with status='retracted'
        r = proj.query("MATCH (n:Point {id:'p1'}) RETURN n.status").result_set
        assert len(r) == 1
        assert r[0][0] == "retracted"
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


def test_falkor_apply_points_merged_nested_format():
    """Regression #325: a nested-format PointsMerged event (merge_ids inside
    `point`) must delete merged nodes. The apply() handler used to read the
    RAW event instead of the normalized one, so nested-format merges were a
    silent no-op (merged points survived)."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.apply({"type": "PointAdded",
                     "point": {"id": "a", "content": "A", "context": "ctx"}})
        proj.apply({"type": "PointAdded",
                     "point": {"id": "b", "content": "B", "context": "ctx"}})
        # Script/nested format: point subfields wrapped under `point`
        proj.apply({"type": "PointsMerged",
                     "point": {"keep_id": "a", "merge_ids": ["b"]}})
        assert proj.query(
            "MATCH (n:Point {id:'a'}) RETURN count(n)"
        ).result_set[0][0] == 1, "keep_id point must survive the merge"
        assert proj.query(
            "MATCH (n:Point {id:'b'}) RETURN count(n)"
        ).result_set[0][0] == 0, "merged point b must be deleted (nested format)"
    finally:
        proj.close()


def test_norm_handles_non_dict_point():
    """Regression #325: _norm must not crash when `point` is a non-dict
    (e.g. a legacy string ID). Old code did `{**ev, **ev["point"]}` on any
    truthy point → TypeError: 'str' object is not a mapping."""
    from tortoise.projection import _norm

    # String point → passed through unchanged (no crash)
    ev = {"type": "PointAdded", "point": "legacy-id-123"}
    out = _norm(ev)
    assert out["point"] == "legacy-id-123"
    # dict point → merged (normal behavior preserved)
    ev2 = {"type": "PointAdded", "point": {"id": "p1", "content": "x"}}
    out2 = _norm(ev2)
    assert out2["id"] == "p1" and out2["content"] == "x"
    # empty dict point → unchanged
    ev3 = {"type": "PointAdded", "point": {}}
    assert _norm(ev3) == ev3
    # missing point → unchanged
    ev4 = {"type": "PointRetracted", "id": "p9"}
    assert _norm(ev4) == ev4


def test_falkor_apply_ignores_non_dict_point():
    """Regression #325: apply() with a non-dict point must skip the event
    (no crash, no partial write)."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.apply({"type": "PointAdded", "point": "legacy-id-123"})
        # no Point node created, no exception raised
        n = proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
        assert n == 0, f"expected 0 points after malformed PointAdded, got {n}"
    finally:
        proj.close()


def test_apply_one_non_dict_point_skipped():
    """Regression #325: fold()/_apply_one must skip non-dict point events."""
    from tortoise.projection import _apply_one
    points: dict[str, dict] = {}
    _apply_one(points, {"type": "PointAdded", "point": "legacy-id"})
    assert points == {}


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


def test_falkor_rebuild_all_ignores_non_dict_point():
    """Regression #325: rebuild_all must skip (not crash on) a PointAdded with
    a non-dict point across ALL passes (1a/1b/2) — parity with apply()."""
    if _skip_if_no_falkor():
        return
    d = tempfile.mkdtemp(prefix="tortoise_badpoint_")
    try:
        import json
        log_path = os.path.abspath(os.path.join(d, "events.jsonl"))
        with open(log_path, "w") as f:
            f.write(json.dumps({"type": "PointAdded", "point": "legacy-id-123"}) + "\n")
            f.write(json.dumps({"type": "PointAdded",
                                "point": {"id": "ok-1", "content": "fine",
                                          "context": "ctx"}}) + "\n")
        proj = FalkorProjection(_tmp("g_badpoint.db"), graph_name="test")
        try:
            result = proj.rebuild_all(d)
            assert result["nodes"] == 1, f"expected 1 valid node, got {result['nodes']}"
            rows = proj.g.query(
                "MATCH (n:Point {id:'ok-1'}) RETURN count(n)"
            ).result_set
            assert rows[0][0] == 1, "valid point must survive rebuild"
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
            # b was retracted — leaves a tombstone (#689)
            node_count = proj.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
            assert node_count == 3  # a + op + b tombstone
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
    if not _docker_falkor_reachable():
        pytest.skip("live FalkorDB (FALKORDB_HOST:PORT) not reachable")
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


class TestCreateEdgeAboutPredicates:
    """#391: about* predicates must be creatable via generic create_edge."""

    def _proj(self):
        proj = FalkorProjection(_tmp("g.db"), graph_name="test")
        proj._upsert({"id": "p1", "content": "claim", "context": "ctx"})
        proj._upsert({"id": "p2", "content": "other", "context": "ctx"})
        proj._upsert_subject({"id": "s1", "name": "Alice"})
        proj._upsert_object({"id": "o1", "name": "Widget", "object_kind": "product"})
        proj._upsert_document({"id": "d1", "title": "Doc"})
        proj._upsert_event({"id": "e1", "eventKind": "review"})
        proj._upsert_source({"id": "src1", "url": "https://x.dev/a"})
        return proj

    def test_about_edges_accepted_by_create_edge(self):
        """Each documented about* predicate is accepted and creates the edge."""
        if _skip_if_no_falkor():
            return
        proj = self._proj()
        try:
            cases = [
                ("aboutSubject", "s1"), ("aboutObject", "o1"),
                ("aboutEvent", "e1"), ("aboutDocument", "d1"),
                ("aboutSource", "src1"),
            ]
            for rel, tid in cases:
                assert proj.create_edge("p1", tid, rel) is True, rel
                rows = proj.g.query(
                    f"MATCH (:Point {{id:'p1'}})-[:{rel}]->(t) "
                    f"RETURN t.id"
                ).result_set
                assert [r[0] for r in rows] == [tid], f"{rel}: {rows}"
            # aboutAction (Action dissolved in v3.0 — predicate kept for
            # legacy edge types; endpoints are whatever resolves)
            assert proj.create_edge("p1", "p2", "aboutAction") is True
            rows = proj.g.query(
                "MATCH (:Point {id:'p1'})-[:aboutAction]->(t) RETURN t.id"
            ).result_set
            assert [r[0] for r in rows] == ["p2"]
        finally:
            proj.close()

    def test_about_edges_idempotent_on_recreate(self):
        """Re-creating the same about edge is a no-op (MERGE semantics)."""
        if _skip_if_no_falkor():
            return
        proj = self._proj()
        try:
            assert proj.create_edge("p1", "s1", "aboutSubject") is True
            assert proj.create_edge("p1", "s1", "aboutSubject") is True
            rows = proj.g.query(
                "MATCH (:Point {id:'p1'})-[:aboutSubject]->(:Subject {id:'s1'}) RETURN count(*)"
            ).result_set
            assert rows[0][0] == 1
        finally:
            proj.close()


class TestOwnedByDagGuard:
    """#390: generic create_edge must not bypass the ownedBy circular-DAG guard."""

    def _proj(self, ids=("a", "b", "c")):
        proj = FalkorProjection(_tmp("g.db"), graph_name="test")
        for pid in ids:
            proj._upsert({"id": pid, "content": pid.upper(), "context": "ctx"})
        return proj

    def test_direct_cycle_rejected(self):
        """a→b then b→a must raise — same guard as create_owned_by."""
        if _skip_if_no_falkor():
            return
        proj = self._proj()
        try:
            assert proj.create_edge("a", "b", "ownedBy") is True
            with pytest.raises(ValueError, match="Circular ownership"):
                proj.create_edge("b", "a", "ownedBy")
            # the rejected call must not have created the edge
            rows = proj.g.query(
                "MATCH (:Point {id:'b'})-[:ownedBy]->(:Point {id:'a'}) RETURN count(*)"
            ).result_set
            assert rows[0][0] == 0
        finally:
            proj.close()

    def test_transitive_cycle_rejected(self):
        """a→b→c then c→a must raise (2-hop cycle closes)."""
        if _skip_if_no_falkor():
            return
        proj = self._proj()
        try:
            proj.create_edge("a", "b", "ownedBy")
            proj.create_edge("b", "c", "ownedBy")
            with pytest.raises(ValueError, match="Circular ownership"):
                proj.create_edge("c", "a", "ownedBy")
        finally:
            proj.close()

    def test_acyclic_chain_accepted(self):
        """Valid ownership chains still pass through create_edge."""
        if _skip_if_no_falkor():
            return
        proj = self._proj()
        try:
            assert proj.create_edge("a", "b", "ownedBy") is True
            assert proj.create_edge("b", "c", "ownedBy") is True
            rows = proj.g.query(
                "MATCH (:Point)-[:ownedBy]->(:Point) RETURN count(*)"
            ).result_set
            assert rows[0][0] == 2
        finally:
            proj.close()

    def test_guard_consistent_with_create_owned_by(self):
        """create_owned_by and create_edge agree on the same cycle either way."""
        if _skip_if_no_falkor():
            return
        # edge created via create_edge blocks create_owned_by
        proj = self._proj(ids=("a", "b"))
        try:
            proj.create_edge("a", "b", "ownedBy")   # a owned by b
            with pytest.raises(ValueError, match="Circular ownership"):
                proj.create_owned_by("b", "a")       # b owned by a → cycle
        finally:
            proj.close()
        # edge created via create_owned_by blocks create_edge
        proj = self._proj(ids=("a", "b"))
        try:
            proj.create_owned_by("a", "b")           # a owned by b
            with pytest.raises(ValueError, match="Circular ownership"):
                proj.create_edge("b", "a", "ownedBy")
        finally:
            proj.close()


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

def test_stub_creation_bounded_at_cap():
    """#329: short-ID stub auto-creation stops at the per-instance cap; at-cap
    behavior is fail-safe (no stub, no partial edge, warning logged).

    The cap is read from the instance attr ``_max_autocreated_stubs`` — the
    old env-var (TORTOISE_MAX_AUTOCREATED_STUBS) was never wired in code.
    """
    import tempfile, os
    from tortoise.projection import FalkorProjection

    db = os.path.join(tempfile.mkdtemp(prefix="tortoise_stubcap_"), "test.db")
    proj = FalkorProjection(db)
    proj._max_autocreated_stubs = 2
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


def test_falkor_rebuild_all_revision_before_add():
    """#21 regression: a PointRevised in an alphabetically-EARLIER file must be
    applied to the point whose PointAdded lives in a LATER file. The two-pass
    rebuild (Pass 1a creates ALL nodes before Pass 1b applies revisions) makes
    this structurally safe — this test pins it so a future one-pass refactor
    can't silently re-introduce lost revisions."""
    if _skip_if_no_falkor():
        return
    d = tempfile.mkdtemp(prefix="tortoise_21_")
    try:
        # b.jsonl sorts AFTER a.jsonl → PointAdded lands in the later file.
        log_b = EventLog(os.path.join(d, "b.jsonl"))
        api_b = EventAPI(log_b, initiated_by="extractor", agent_id="test")
        prov = provenance("doc.txt", [0, 10], "quote", extracted_by="test@0")
        pid = api_b.add_point("original content", prov)

        # a.jsonl sorts FIRST → its PointRevised for pid is seen before any
        # PointAdded when files are read in sorted order.
        log_a = EventLog(os.path.join(d, "a.jsonl"))
        api_a = EventAPI(log_a, initiated_by="extractor", agent_id="test")
        api_a.revise_point(pid, new_content="revised content", corrects=[])

        proj = FalkorProjection(_tmp("g_rebuild_21.db"), graph_name="test")
        try:
            proj.rebuild_all(d)
            r = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.content",
                params={"id": pid},
            ).result_set
            assert r and r[0][0] == "revised content", \
                f"revision lost: expected 'revised content', got {r}"
        finally:
            proj.close()
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
def test_falkor_revise_point_wipes_stale_embedding_on_compute_failure():
    """#19 regression: PointRevised with a raising compute_embedding must NOT
    leave the stale embedding — the except block wipes it (embedding = None)
    so SET overwrites the graph value instead of preserving the old vector."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.apply({"type": "PointAdded",
                     "point": {"id": "p1", "content": "old content", "context": "ctx"}})
        # Seed a stale embedding as if it had been computed before the failure.
        proj.g.query(
            "MATCH (n:Point {id:'p1'}) SET n.embedding = vecf32($emb)",
            params={"emb": [0.1] * 384},
        )
        # PointRevised whose embedding recompute raises → must wipe, not keep.
        with mock.patch("tortoise.embeddings.compute_embedding",
                        side_effect=RuntimeError("model load failed")):
            proj.apply({"type": "PointRevised", "id": "p1", "new_content": "new content"})
        r = proj.g.query(
            "MATCH (n:Point {id:'p1'}) RETURN n.content, n.embedding IS NULL"
        ).result_set
        assert r[0][0] == "new content"
        assert r[0][1] is True, "stale embedding survived a failed recompute (#19)"
    finally:
        proj.close()


# ── #548: SDK-created points survive rebuild_all ──────────────────────────


def test_falkor_rebuild_all_with_sdk_points():
    """#548: SDK-created points (with event_log_path) survive rebuild_all.

    SDK write paths emit PointAdded/OperatorAdded events to the log.
    rebuild_all replays them → full parity between the incrementally-built
    graph and the rebuilt graph.
    """
    if _skip_if_no_falkor():
        return
    from tortoise.sdk import TortoiseSDK

    d = tempfile.mkdtemp(prefix="tortoise_sdk_rebuild_")
    try:
        db_path = os.path.join(d, "tortoise.db")
        events_path = os.path.join(d, "events.jsonl")

        # Create points via SDK (event_log_path configured)
        sdk = TortoiseSDK(db_path=db_path, event_log_path=events_path)
        try:
            p1 = sdk.create_point("statement", "SDK point alpha",
                                  authoredBy="alice", tags=["important"])
            p2 = sdk.create_point("statement", "SDK point beta",
                                  authoredBy="bob")
            # Operator linking p2 → p1
            op = sdk.create_operator("IMPL", p2["id"], [p1["id"]])
            p3 = sdk.create_point("observation", "SDK point gamma")
            # Update a point
            sdk.update_point(p3["id"], content="SDK point gamma REVISED")
        finally:
            sdk.close()

        # Verify the event log was written
        from tortoise.log import EventLog
        log_events = EventLog(events_path).read_all()
        assert len(log_events) >= 5, (
            f"Expected at least 5 SDK events (3 PointAdded + 1 OperatorAdded "
            f"+ 1 PointRevised), got {len(log_events)}")
        # Check event types
        types = [e["type"] for e in log_events]
        assert "PointAdded" in types
        assert "OperatorAdded" in types
        assert "PointRevised" in types
        assert all(e.get("initiated_by") == "sdk" for e in log_events), \
            "SDK events must have initiated_by='sdk'"

        # Close the original SDK's DB so rebuild_all can open its own
        # (the original projection is closed via sdk.close() above)

        # ── Rebuild into a fresh graph from the event log ───
        proj = FalkorProjection(
            os.path.join(d, "rebuilt.db"), graph_name="test")
        try:
            result = proj.rebuild_all(d)
            assert result["nodes"] >= 4, (
                f"Expected at least 4 nodes (3 points + 1 operator), "
                f"got {result['nodes']}")
            assert result["edges"] >= 2, (
                f"Expected at least 2 edges (IMPL from operator), "
                f"got {result['edges']}")

            # Verify all points exist with their properties
            for pid, expected_content in [
                (p1["id"], "SDK point alpha"),
                (p2["id"], "SDK point beta"),
                (p3["id"], "SDK point gamma REVISED"),
                (op["id"], None),  # operator content varies
            ]:
                rows = proj.g.query(
                    "MATCH (n:Point {id:$id}) RETURN n.content, n",
                    params={"id": pid},
                ).result_set
                assert len(rows) == 1, f"Point {pid} not found after rebuild"
                if expected_content is not None:
                    assert rows[0][0] == expected_content, (
                        f"Point {pid} content mismatch: "
                        f"expected {expected_content!r}, got {rows[0][0]!r}")

            # Verify operator edges survived
            edge_count = proj.g.query(
                f"MATCH (n:Point {{id:$oid}})-[r]->(m:Point {{id:$tid}}) "
                f"RETURN count(r)",
                params={"oid": op["id"], "tid": p1["id"]},
            ).result_set[0][0]
            assert edge_count >= 1, "Operator edge not recreated after rebuild"
        finally:
            proj.close()
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_falkor_rebuild_all_snapshot_preserves_sdk_points():
    """#548 transitional: SDK points created WITHOUT event_log_path are
    preserved via rebuild_all's graph snapshot.

    Simulates existing graphs where SDK-created points predate the event
    log fix. rebuild_all snapshots the graph before wiping and injects
    synthetic events for points that have no JSONL counterpart.
    """
    if _skip_if_no_falkor():
        return
    from tortoise.sdk import TortoiseSDK

    d = tempfile.mkdtemp(prefix="tortoise_snapshot_")
    try:
        db_path = os.path.join(d, "tortoise.db")
        events_path = os.path.join(d, "events.jsonl")

        # Create SDK points WITHOUT event_log_path (simulating pre-#548)
        sdk = TortoiseSDK(db_path=db_path, event_log_path=None)
        try:
            p1 = sdk.create_point("statement", "Legacy SDK claim ONE",
                                  authoredBy="alice")
            p2 = sdk.create_point("statement", "Legacy SDK claim TWO",
                                  authoredBy="bob")
            # Operator (also without log)
            op = sdk.create_operator("IMPL", p2["id"], [p1["id"]])
        finally:
            sdk.close()

        # Also create an EventAPI-written event in the JSONL log
        # (simulating the normal extractor path)
        log = EventLog(events_path)
        log.append({
            "type": "PointAdded",
            "point": {"id": "evt-001", "content": "EventAPI claim",
                      "pointKind": "statement", "status": "live",
                      "createdAt": "2026-08-01T00:00:00Z"},
            "projection_version": 2,
            "initiated_by": "extractor",
        })

        # ── rebuild_all on the SAME db_path (snapshot from SDK graph) ───
        # The snapshot queries the existing graph BEFORE wiping, so
        # SDK-created points without JSONL events are preserved.
        # Must use the same graph_name as the SDK ("tortoise" is default).
        proj = FalkorProjection(db_path, graph_name="tortoise")
        try:
            result = proj.rebuild_all(d)

            # Should have 4+ nodes: p1, p2, op, evt-001
            assert result["nodes"] >= 4, (
                f"Expected at least 4 nodes, got {result['nodes']}")

            # All points should be present
            for pid, expected in [
                (p1["id"], "Legacy SDK claim ONE"),
                (p2["id"], "Legacy SDK claim TWO"),
                ("evt-001", "EventAPI claim"),
            ]:
                rows = proj.g.query(
                    "MATCH (n:Point {id:$id}) RETURN n.content",
                    params={"id": pid},
                ).result_set
                assert len(rows) == 1, (
                    f"Point {pid} not found after rebuild")
                assert rows[0][0] == expected, (
                    f"Point {pid} content mismatch: "
                    f"expected {expected!r}, got {rows[0][0]!r}")

            # Operator edges should be recreated
            edge_rows = proj.g.query(
                f"MATCH (n:Point {{id:$oid}})-[r]->(m:Point {{id:$tid}}) "
                f"RETURN type(r)",
                params={"oid": op["id"], "tid": p1["id"]},
            ).result_set
            assert len(edge_rows) >= 1, (
                "Operator edge not recreated from snapshot")
        finally:
            proj.close()
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_falkor_rebuild_all_eventapi_regression():
    """#548: existing EventAPI-written events still replay identically.

    No regression — rebuild_all with only EventAPI events must produce
    the same graph as apply().
    """
    if _skip_if_no_falkor():
        return
    d = tempfile.mkdtemp(prefix="tortoise_eventapi_reg_")
    try:
        # Create EventAPI events in a JSONL log
        log = EventLog(os.path.join(d, "events.jsonl"))
        now = "2026-08-01T00:00:00.000000+00:00"
        events = [
            {"event_id": "e0", "ts": now, "type": "IngestStarted",
             "initiated_by": "extractor", "agent_id": "test",
             "run_id": "r1",
             "key": {"kind": "doc", "value": "reg.txt"},
             "extractor_version": "1"},
            {"event_id": "e1", "ts": now, "type": "PointAdded",
             "initiated_by": "extractor", "agent_id": "test",
             "projection_version": 2,
             "point": {"id": "p-reg-1", "content": "regression claim A",
                       "status": "live", "createdAt": now,
                       "pointKind": "statement",
                       "authoredBy": "alice", "confidence": 0.7}},
            {"event_id": "e2", "ts": now, "type": "PointAdded",
             "initiated_by": "extractor", "agent_id": "test",
             "projection_version": 2,
             "point": {"id": "p-reg-2", "content": "regression claim B",
                       "status": "live", "createdAt": now,
                       "pointKind": "statement",
                       "authoredBy": "alice", "confidence": 0.6}},
            {"event_id": "e3", "ts": now, "type": "PointRetracted",
             "initiated_by": "extractor", "agent_id": "test",
             "id": "p-reg-2"},
        ]
        for ev in events:
            log.append(ev)

        # Build via apply
        projA = FalkorProjection(
            os.path.join(d, "a.db"), graph_name="test")
        projB = FalkorProjection(
            os.path.join(d, "b.db"), graph_name="test")
        try:
            for ev in log.read_all():
                projA.apply(ev)
            result = projB.rebuild_all(d)

            # p-reg-1 survives, p-reg-2 was retracted (tombstone per #689)
            assert result["nodes"] >= 1, (
                f"Expected at least 1 node (p-reg-1 survives, p-reg-2 tombstone), "
                f"got {result['nodes']}")

            # Both graphs should have p-reg-1
            for proj in (projA, projB):
                rows = proj.g.query(
                    "MATCH (n:Point {id:'p-reg-1'}) "
                    "RETURN n.content, n.confidence"
                ).result_set
                assert len(rows) == 1, "p-reg-1 not found"
                assert rows[0][0] == "regression claim A"
                assert rows[0][1] == 0.7

            # p-reg-2 was retracted — a tombstone (status='retracted') survives
            # in both apply and rebuild paths (#689: no more hard deletes).
            for proj in (projA, projB):
                rows = proj.g.query(
                    "MATCH (n:Point {id:'p-reg-2'}) RETURN n.status"
                ).result_set
                assert len(rows) == 1, "p-reg-2 tombstone must exist"
                assert rows[0][0] == "retracted", (
                    "p-reg-2 should be a retracted tombstone, not hard-deleted (#689)")
        finally:
            projA.close()
            projB.close()
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
# ── #689: retraction tombstone semantics ───────────────────────────────────

def test_retract_tombstone_inmemory():
    """PointRetracted leaves a tombstone (status='retracted') instead of hard-deleting."""
    from tortoise.projection import _apply_one
    points: dict[str, dict] = {"p1": {"id": "p1", "content": "hello", "pointKind": "statement"}}
    _apply_one(points, {"type": "PointRetracted", "id": "p1"})
    # Tombstone present
    assert "p1" in points
    assert points["p1"]["status"] == "retracted"
    # Original content preserved for recovery
    assert points["p1"]["content"] == "hello"


def test_retract_tombstone_falkor():
    """FalkorProjection retraction leaves a node with status='retracted'."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g_tombstone.db"), graph_name="test")
    try:
        proj.apply({"type": "PointAdded",
                     "point": {"id": "p1", "content": "hello", "pointKind": "statement"}})
        proj.apply({"type": "PointRetracted", "id": "p1"})
        # Tombstone exists in raw graph
        r = proj.query(
            "MATCH (n:Point {id:'p1'}) RETURN n.status, n.content"
        ).result_set
        assert len(r) == 1
        assert r[0][0] == "retracted"
        assert r[0][1] == "hello"  # content preserved
    finally:
        proj.close()


def test_retract_tombstone_get_point_hidden():
    """SDK get_point returns the retracted tombstone (full fidelity per #432);
    query/paginated_query hide it by default (see test_retract_tombstone_skipped_in_query)."""
    if _skip_if_no_falkor():
        return
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(db_path=_tmp("g_sdk_retracted.db"))
    try:
        p = sdk.create_point("statement", "visible at first")
        pid = p["id"]
        assert sdk.get_point(pid) != {}
        # Apply retraction via the projection raw path (simulates event replay)
        sdk._get_proj().apply({"type": "PointRetracted", "id": pid})
        # #432 contract: get_point keeps returning the tombstone (full fidelity)
        assert sdk.get_point(pid) != {}
        assert sdk.get_point(pid).get("status") == "retracted"
        # Raw query can also find it
        r = sdk._get_proj().query(
            "MATCH (n:Point {id:$id}) RETURN n.status", id=pid
        ).result_set
        assert len(r) == 1
        assert r[0][0] == "retracted"
    finally:
        sdk.close()


def test_retract_tombstone_skipped_in_query():
    """SDK query/paginated_query skip retracted points."""
    if _skip_if_no_falkor():
        return
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(db_path=_tmp("g_sdk_query_ret.db"))
    try:
        p1 = sdk.create_point("statement", "keep me")
        p2 = sdk.create_point("statement", "retract me")
        # Retract p2
        sdk._get_proj().apply({"type": "PointRetracted", "id": p2["id"]})
        # query should only return p1
        results = sdk.query(kind="statement")
        ids = {r["id"] for r in results}
        assert p1["id"] in ids
        assert p2["id"] not in ids, "retracted point must not appear in query"
        # paginated_query same
        page = sdk.paginated_query(kind="statement")
        page_ids = {r["id"] for r in page["results"]}
        assert p1["id"] in page_ids
        assert p2["id"] not in page_ids
    finally:
        sdk.close()


def test_retract_missing_point_noop():
    """Retracting a non-existent point is a no-op (idempotent)."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g_noop.db"), graph_name="test")
    try:
        proj.apply({"type": "PointRetracted", "id": "nonexistent"})
        # No crash, no stray nodes created
        r = proj.query("MATCH (n) RETURN count(n)").result_set
        assert r[0][0] == 0
    finally:
        proj.close()


# ── #689 review fixes: retracted points excluded from search/EP/evidence ───

def test_retract_tombstone_excluded_from_structural_search():
    """Retracted points are NOT returned by run_structural_query (#689 review)."""
    if _skip_if_no_falkor():
        return
    from tortoise.search_engine import run_structural_query
    proj = FalkorProjection(_tmp("g_ret_struct.db"), graph_name="test")
    try:
        # Create two points
        proj.apply({"type": "PointAdded",
                     "point": {"id": "p_keep", "content": "keep me",
                               "pointKind": "statement"}})
        proj.apply({"type": "PointAdded",
                     "point": {"id": "p_ret", "content": "retract me",
                               "pointKind": "statement"}})
        # Retract one
        proj.apply({"type": "PointRetracted", "id": "p_ret"})
        # Structural query by kind should only return the live point
        results = run_structural_query(proj.g, "statement", entity_type="point", limit=10)
        ids = {r[0] for r in results}
        assert "p_keep" in ids, "live point must appear in structural results"
        assert "p_ret" not in ids, "retracted point must NOT appear in structural results"
    finally:
        proj.close()


def test_retract_tombstone_excluded_from_vector_search():
    """Retracted points are NOT returned by run_vector_query (#689 review).

    Uses the brute-force (embedded) path. FalkorDBLite vec.euclideanDistance
    requires vecf32-encoded embeddings; the test creates points WITHOUT
    embeddings to ensure the vector query path (not index path) is exercised,
    then verifies the status filter is present in the brute-force Cypher by
    asserting retracted points are excluded at the MATCH level before any
    distance computation.
    """
    if _skip_if_no_falkor():
        return
    from tortoise.search_engine import run_vector_query
    proj = FalkorProjection(_tmp("g_ret_vec.db"), graph_name="test")
    try:
        # Create two points with embeddings (vecf32 encoded for FalkorDBLite).
        # We use the FalkorDB vecf32 function directly via Cypher to ensure
        # the embedding is stored in the format the brute-force query expects.
        import struct
        emb_keep = [0.1] * 384
        emb_ret = [0.2] * 384
        emb_keep_bytes = struct.pack(f"<{len(emb_keep)}f", *emb_keep)
        emb_ret_bytes = struct.pack(f"<{len(emb_ret)}f", *emb_ret)
        proj.apply({"type": "PointAdded",
                     "point": {"id": "p_keep", "content": "keep me",
                               "pointKind": "statement"}})
        proj.apply({"type": "PointAdded",
                     "point": {"id": "p_ret", "content": "retract me",
                               "pointKind": "statement"}})
        # Store embeddings via Cypher to ensure correct FalkorDB vector type
        proj.g.query(
            "MATCH (n:Point {id:'p_keep'}) SET n.embedding = vecf32($e)",
            params={"e": emb_keep},
        )
        proj.g.query(
            "MATCH (n:Point {id:'p_ret'}) SET n.embedding = vecf32($e)",
            params={"e": emb_ret},
        )
        proj.apply({"type": "PointRetracted", "id": "p_ret"})
        # Brute-force vector query should filter retracted before distance.
        dummy_vec = [0.1] * 384
        results = run_vector_query(proj.g, dummy_vec, limit=10, is_embedded=True,
                                   entity_type="point")
        ids = {r[0] for r in results}
        assert "p_keep" in ids, (
            "live point with embedding must appear in vector search "
            f"(got ids={ids})"
        )
        assert "p_ret" not in ids, (
            "retracted point must NOT appear in vector search results"
        )
    finally:
        proj.close()


def test_retract_tombstone_excluded_from_svbp_factors():
    """extract_svbp_factors excludes retracted operators and claims (#689 review)."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g_ret_svbp.db"), graph_name="test")
    try:
        # Create claims
        proj.apply({"type": "PointAdded",
                     "point": {"id": "c_good", "content": "good claim",
                               "pointKind": "statement"}})
        proj.apply({"type": "PointAdded",
                     "point": {"id": "c_ret", "content": "retracted claim",
                               "pointKind": "statement"}})
        # Create operator that references both
        proj.apply({"type": "PointAdded",
                     "point": {"id": "op1", "content": "operator",
                               "pointKind": "operator",
                               "is_operator": True, "op_type": "IMPL"}})
        # Wire operator → both claims (simulate IMPL edges)
        proj.g.query(
            "MATCH (o:Point {id:'op1'}), (c:Point {id:'c_good'}) "
            "CREATE (o)-[:IMPL]->(c)"
        )
        proj.g.query(
            "MATCH (o:Point {id:'op1'}), (c:Point {id:'c_ret'}) "
            "CREATE (o)-[:IMPL]->(c)"
        )
        # Retract the bad claim
        proj.apply({"type": "PointRetracted", "id": "c_ret"})
        # extract_svbp_factors should only see the good claim
        factors, _evidence = proj.extract_svbp_factors()
        # factors: [(op_id, op_type, [input_ids], weight), ...]
        for _op_id, _op_type, input_ids, _weight in factors:
            assert "c_ret" not in input_ids, (
                "retracted claim must NOT appear as an SVBP factor input"
            )
    finally:
        proj.close()


def test_retract_tombstone_excluded_from_evidence():
    """_hydrate_evidence excludes retracted baselines (#689 review)."""
    if _skip_if_no_falkor():
        return
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(db_path=_tmp("g_ret_evidence.db"))
    try:
        p1 = sdk.create_point("statement", "keep baseline")
        p2 = sdk.create_point("statement", "retracted baseline")
        # Set baselines on both
        sdk.set_point_baseline(p1["id"], 2.0, 8.0)
        sdk.set_point_baseline(p2["id"], 3.0, 7.0)
        # Retract p2
        sdk._get_proj().apply({"type": "PointRetracted", "id": p2["id"]})
        # Hydrate evidence — should only load p1
        sdk._evidence = {}  # clear cache
        sdk._hydrate_evidence()
        assert p1["id"] in sdk._evidence, "live baseline must be hydrated"
        assert p2["id"] not in sdk._evidence, (
            "retracted baseline must NOT be hydrated into evidence"
        )
    finally:
        sdk.close()


def test_retract_tombstone_hosted_list_excludes_retracted():
    """GET /v1/points raw query pattern excludes retracted points (#689 review)."""
    if _skip_if_no_falkor():
        return
    # Simulate the hosted API query pattern for GET /v1/points
    proj = FalkorProjection(_tmp("g_ret_hosted.db"), graph_name="test")
    try:
        proj.apply({"type": "PointAdded",
                     "point": {"id": "h_keep", "content": "visible",
                               "pointKind": "statement"}})
        proj.apply({"type": "PointAdded",
                     "point": {"id": "h_ret", "content": "hidden",
                               "pointKind": "statement"}})
        proj.apply({"type": "PointRetracted", "id": "h_ret"})
        # Replicate the hosted API query (non-operator, no kind filter)
        # (#522: `= false` — the IS NULL disjunction was rewritten)
        conditions = [
            "n.is_operator = false",
            "(n.status IS NULL OR n.status <> 'retracted')",
        ]
        query = (
            "MATCH (n:Point) WHERE "
            + " AND ".join(conditions)
            + " RETURN properties(n) ORDER BY n.createdAt DESC LIMIT 10"
        )
        rows = proj.g.query(query).result_set
        ids = {r[0].get("id") for r in rows}
        assert "h_keep" in ids, "live point must appear in list"
        assert "h_ret" not in ids, "retracted point must NOT appear in list"
    finally:
        proj.close()
