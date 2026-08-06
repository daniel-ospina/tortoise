"""Projection version gate tests — Phase 1 stop-writes for context field (#49).

Tests the READ side: projection handles both old events (context preserved)
and v2+ events (context discarded), for both InMemoryProjection (_apply_one)
and FalkorProjection (Cypher MERGE paths).
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.projection import _apply_one, FalkorProjection  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_test_"), name)


_HAS_FALKOR: bool | None = None


def _has_falkor() -> bool:
    global _HAS_FALKOR
    if _HAS_FALKOR is None:
        try:
            from redislite.falkordb_client import FalkorDB  # noqa: F401
            # Runtime probe: embedded mode is broken on some machines (#82 —
            # redislite interprets the file path as a hostname → idna
            # UnicodeEncodeError). Only treat FalkorDB as available if a
            # projection can actually be constructed.
            import tempfile
            from tortoise.projection import FalkorProjection
            db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_probe_"), "probe.db")
            proj = FalkorProjection(db_path, graph_name="test")
            proj.close()
            _HAS_FALKOR = True
        except (ImportError, Exception):
            _HAS_FALKOR = False
    return _HAS_FALKOR


def _skip_if_no_falkor() -> bool:
    return not _has_falkor()


# ═══════════════════════════════════════════════════════════════════════════
# _apply_one (InMemoryProjection) version gate tests
# ═══════════════════════════════════════════════════════════════════════════


def test_apply_one_old_event_stores_context():
    """Event WITHOUT projection_version → context stored in point dict."""
    points: dict[str, dict] = {}
    ev = {
        "type": "PointAdded",
        "point": {"id": "p1", "content": "hello", "context": "my_ctx"},
    }
    _apply_one(points, ev)
    assert "p1" in points
    assert points["p1"]["content"] == "hello"
    assert points["p1"]["context"] == "my_ctx"


def test_apply_one_v2_event_discards_context():
    """Event WITH projection_version=2 → context stripped from point dict."""
    points: dict[str, dict] = {}
    ev = {
        "type": "PointAdded",
        "projection_version": 2,
        "point": {"id": "p2", "content": "hello", "context": "should_be_discarded"},
    }
    _apply_one(points, ev)
    assert "p2" in points
    assert points["p2"]["content"] == "hello"
    assert "context" not in points["p2"]


def test_apply_one_old_pointrevised_stores_new_context():
    """PointRevised WITHOUT projection_version → new_context applied."""
    points: dict[str, dict] = {
        "p1": {"id": "p1", "content": "old", "context": "old_ctx"}
    }
    ev = {
        "type": "PointRevised",
        "id": "p1",
        "new_content": "new",
        "new_context": "new_ctx",
    }
    _apply_one(points, ev)
    assert points["p1"]["content"] == "new"
    assert points["p1"]["context"] == "new_ctx"


def test_apply_one_v2_pointrevised_discards_new_context():
    """PointRevised WITH projection_version=2 → new_context ignored, context unchanged."""
    points: dict[str, dict] = {
        "p1": {"id": "p1", "content": "old", "context": "old_ctx"}
    }
    ev = {
        "type": "PointRevised",
        "projection_version": 2,
        "id": "p1",
        "new_content": "new",
        "new_context": "should_be_discarded",
    }
    _apply_one(points, ev)
    assert points["p1"]["content"] == "new"
    # Context should remain unchanged (old value preserved)
    assert points["p1"]["context"] == "old_ctx"


def test_apply_one_v2_pointrevised_still_updates_content():
    """PointRevised v2 still updates content — only context is gated."""
    points: dict[str, dict] = {
        "p1": {"id": "p1", "content": "old", "context": "old_ctx"}
    }
    ev = {
        "type": "PointRevised",
        "projection_version": 2,
        "id": "p1",
        "new_content": "updated_content",
    }
    _apply_one(points, ev)
    assert points["p1"]["content"] == "updated_content"
    assert points["p1"]["context"] == "old_ctx"  # unchanged


def test_apply_one_always_fields_preserved_v2():
    """content and other always-fields still stored regardless of version."""
    points: dict[str, dict] = {}
    ev = {
        "type": "PointAdded",
        "projection_version": 2,
        "point": {
            "id": "p3",
            "content": "always there",
            "context": "discarded",
            "pointKind": "claim",
        },
    }
    _apply_one(points, ev)
    assert points["p3"]["content"] == "always there"
    assert points["p3"].get("pointKind") == "claim"
    assert "context" not in points["p3"]


# ═══════════════════════════════════════════════════════════════════════════
# FalkorProjection version gate tests (embedded FalkorDB)
# ═══════════════════════════════════════════════════════════════════════════


def test_falkor_old_event_no_context_written():
    """P2 #49: context field removed — even old-format events don't write it."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.apply({
            "type": "PointAdded",
            "point": {"id": "p1", "content": "hello", "context": "my_ctx"},
        })
        r = proj.query(
            "MATCH (n:Point {id:'p1'}) RETURN n.content, n.context"
        ).result_set
        assert r[0][0] == "hello"
        # Context is not persisted — the field no longer exists in projections
        assert r[0][1] is None
    finally:
        proj.close()


def test_falkor_v2_event_discards_context():
    """Event WITH projection_version=2 → context NOT written to graph node."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.apply({
            "type": "PointAdded",
            "projection_version": 2,
            "point": {"id": "p2", "content": "hello", "context": "should_not_appear"},
        })
        r = proj.query(
            "MATCH (n:Point {id:'p2'}) RETURN n.content, n.context"
        ).result_set
        assert r[0][0] == "hello"
        # Context should be nil/None — not written
        assert r[0][1] is None, f"expected context=None, got {r[0][1]!r}"
    finally:
        proj.close()


def test_falkor_v2_pointrevised_does_not_mutate_context():
    """PointRevised v2 → content updated, context untouched in graph."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        # First add with old event (has context)
        proj.apply({
            "type": "PointAdded",
            "point": {"id": "p1", "content": "old", "context": "old_ctx"},
        })
        # Then revise with v2 event
        proj.apply({
            "type": "PointRevised",
            "projection_version": 2,
            "id": "p1",
            "new_content": "new",
            "new_context": "should_be_ignored",
        })
        r = proj.query(
            "MATCH (n:Point {id:'p1'}) RETURN n.content, n.context"
        ).result_set
        assert r[0][0] == "new"
        # P2 #49: context never written — no context property on the node
        assert r[0][1] is None
    finally:
        proj.close()


def test_falkor_v2_always_fields_still_written():
    """content/pointKind still written to graph regardless of version."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        proj.apply({
            "type": "PointAdded",
            "projection_version": 2,
            "point": {
                "id": "p3",
                "content": "always written",
                "context": "discarded",
                "pointKind": "claim",
            },
        })
        r = proj.query(
            "MATCH (n:Point {id:'p3'}) RETURN n.content, n.pointKind, n.context"
        ).result_set
        assert r[0][0] == "always written"
        assert r[0][1] == "claim"
        assert r[0][2] is None, f"expected context=None, got {r[0][2]!r}"
    finally:
        proj.close()


def test_falkor_v2_operator_no_context():
    """OperatorAdded v2 → no context on operator node."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        # Add inputs first (old events for simplicity)
        proj.apply({
            "type": "PointAdded",
            "point": {"id": "a", "content": "A", "context": "ctx_a"},
        })
        proj.apply({
            "type": "PointAdded",
            "point": {"id": "b", "content": "B", "context": "ctx_b"},
        })
        # Operator with v2
        proj.apply({
            "type": "OperatorAdded",
            "projection_version": 2,
            "point": {
                "id": "op1",
                "content": "IMPL(a,b)",
                "context": "should_be_discarded",
                "operator": {"op_type": "IMPL", "inputs": ["a", "b"]},
            },
        })
        r = proj.query(
            "MATCH (n:Point {id:'op1'}) RETURN n.content, n.context, n.is_operator"
        ).result_set
        assert r[0][0] == "IMPL(a,b)"
        assert r[0][2] is True  # is_operator flag preserved
        assert r[0][1] is None, f"expected context=None, got {r[0][1]!r}"
    finally:
        proj.close()


# ═══════════════════════════════════════════════════════════════════════════
# Mixed scenario: old + v2 events in same projection
# ═══════════════════════════════════════════════════════════════════════════


def test_falkor_mixed_old_and_v2_events():
    """Old events keep context, v2 events don't — coexisting in same graph."""
    if _skip_if_no_falkor():
        return
    proj = FalkorProjection(_tmp("g.db"), graph_name="test")
    try:
        # Old event (no version) — context written
        proj.apply({
            "type": "PointAdded",
            "point": {"id": "old_p", "content": "old event", "context": "old_ctx"},
        })
        # V2 event — context discarded
        proj.apply({
            "type": "PointAdded",
            "projection_version": 2,
            "point": {"id": "v2_p", "content": "v2 event", "context": "discarded"},
        })

        # P2 #49: context never written for ANY event version
        r = proj.query(
            "MATCH (n:Point {id:'old_p'}) RETURN n.context"
        ).result_set
        assert r[0][0] is None

        # V2 point also has no context
        r = proj.query(
            "MATCH (n:Point {id:'v2_p'}) RETURN n.context"
        ).result_set
        assert r[0][0] is None, f"expected context=None, got {r[0][0]!r}"
    finally:
        proj.close()
