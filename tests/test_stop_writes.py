"""Stop-writes tests for #49 Phase 1 — context not written, session map preserved.

Runs on the conftest isolated graph. Verifies the semantic-trap fix: create-then-query
still works within a session even though context is no longer persisted.
"""
from __future__ import annotations

import uuid

import pytest
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    s = TortoiseSDK()
    yield s
    try:
        s.close()
    except Exception:
        pass


def _mk():
    return f"sw_{uuid.uuid4().hex[:8]}"


def test_create_point_records_session_map_no_node_context(sdk):
    ctx = _mk()
    p = sdk.create_point("statement", "session map test point", context=ctx)
    assert ctx in sdk._session_context_map, "context not recorded in session map"
    assert p["id"] in sdk._session_context_map[ctx], "point id not in session map"
    # node must NOT have context property
    node = sdk.get_point(p["id"])
    assert "context" not in node or node.get("context") is None, \
        "context was written to the node — stop-writes failed"


def test_query_union_semantics(sdk):
    """Pre-existing graph point + new session-map point both returned."""
    ctx = _mk()
    # Pre-existing point: write context directly to the graph (bypass SDK)
    proj = sdk._get_proj()
    legacy = sdk.create_point("statement", f"legacy point {ctx}", )
    # simulate a pre-P1 point that has context on the node
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.context=$ctx",
        params={"id": legacy["id"], "ctx": ctx},
    )
    # New point: via SDK (session map only)
    new_p = sdk.create_point("statement", f"new point {ctx}", context=ctx)
    results = sdk.query(context=ctx)
    ids = {r["id"] for r in results}
    assert legacy["id"] in ids, "legacy (graph) point missing from UNION"
    assert new_p["id"] in ids, "new (session map) point missing from UNION"


def test_dedup_content_hash_pointkind(sdk):
    """Same content + different pointKind → no dedup. Same + same kind → dedup."""
    content = f"dedup test {_mk()}"
    p1 = sdk.create_point("statement", content, dedup=True)
    p2 = sdk.create_point("observation", content, dedup=True)
    assert p1["id"] != p2["id"], "different pointKinds must not dedup"
    p3 = sdk.create_point("statement", content, dedup=True)
    assert p3["id"] == p1["id"], "same pointKind must dedup"


def test_deprecation_warning_in_result(sdk):
    p = sdk.create_point("statement", f"dep warn {_mk()}", context="some-ctx")
    assert p.get("deprecation_warnings"), "deprecation_warnings key missing"
    assert any("context is deprecated" in w for w in p["deprecation_warnings"])


def test_create_without_context_no_warning(sdk):
    p = sdk.create_point("statement", f"no ctx {_mk()}")
    assert not p.get("deprecation_warnings"), "unexpected warning without context"


def test_file_decision_no_context_write(sdk):
    ctx = _mk()
    result = sdk.file_decision(
        options=["A", "B"],
        evidence=["E1 supports A", "E2 supports B"],
        choice=0,
        context=ctx,
    )
    # decision + option points must not carry context on nodes
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.context",
        params={"ids": list(result.values())},
    ).result_set
    for rid, rctx in rows:
        assert rctx is None, f"point {rid} has context written: {rctx}"
