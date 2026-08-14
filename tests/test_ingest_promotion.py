"""A11 (epic #902, issue #1049) — Track A promotion entry point.

The interim promotion route is the DESIGNATION of
``tortoise_update_point(status="live")`` as the gated-bundle promote surface
(INGEST_CONTRACT.md §11; no new tool pre-#785). These tests pin the route:

- gated ingest → points are draft; NO promotion happens before an explicit
  call (promotion is explicit, never automatic under gated);
- the interim route promotes a draft point to live (guarded draft→live);
- the no-zombie-operator-resolution caveat: promoting a gated
  operator-requiring bundle's POINTS does NOT resolve its draft operator
  node — the draft operator stays inert (draft status → #780 live-filter
  excludes it) until IT is explicitly promoted (Track B #785 ships the
  auto-resolution); an explicit promote of the operator node activates it
  in EP selection (derived liveness GATE-2 Q3: >=2 live endpoints).
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.analyze import _bfs_select_operators


@pytest.fixture
def sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_promo_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


def _query(sdk, cypher: str, params: dict | None = None):
    return sdk._get_proj().g.query(cypher, params=params or {}).result_set


def _status(sdk, pid: str) -> str | None:
    rows = _query(sdk, "MATCH (n:Point {id:$id}) RETURN n.status",
                  {"id": pid})
    return rows[0][0] if rows else None


def test_interim_route_promotes_gated_draft(sdk):
    """E2E-16 Track-A leg (unit half): gated ingest → draft; NO promotion
    before the explicit call; update_point(status='live') promotes."""
    bundle = {
        "points": [
            {"ref": "p1", "kind": "claim", "content": "A implies B."},
            {"ref": "p2", "kind": "claim", "content": "B."},
        ],
        "entities": [], "sources": [],
        "connections": [{"ref": "c1", "from": "p1", "to": "p2",
                         "operator": "IMPL"}],
    }
    res = sdk.ingest(bundle, promotion_policy="gated")
    p1 = res["ids"]["points"][0]
    # created draft; NO promotion before an explicit call
    assert _status(sdk, p1) == "draft", "gated ingest must leave points draft"
    # the interim route promotes
    out = sdk.update_point(p1, status="live")
    assert out["status"] == "live"
    assert _status(sdk, p1) == "live"
    # other points untouched (explicit = per-point)
    p2 = res["ids"]["points"][1]
    assert _status(sdk, p2) == "draft"


def test_interim_route_guarded_terminal_statuses(sdk):
    """The interim route is GUARDED draft→live: terminal statuses are
    rejected by the draft→live-only promote guard (the status is in
    POINT_STATUS_VALUES, but update_point only promotes draft/NULL nodes
    to live — no other status change)."""
    p = sdk.create_point("claim", "X.", status="draft")["id"]
    with pytest.raises(ValueError):
        sdk.update_point(p, status="superseded")


def test_no_zombie_operator_resolution_caveat(sdk):
    """A11 caveat: promoting a gated operator-requiring bundle's POINTS does
    NOT resolve its draft operator node — the draft operator stays inert in
    default-mode EP selection (its draft STATUS → the #780 live filter)
    until IT is explicitly promoted. An explicit promote of the operator
    node activates it (derived liveness: >=2 live endpoints)."""
    bundle = {
        "points": [
            {"ref": "p1", "kind": "claim", "content": "A implies B."},
            {"ref": "p2", "kind": "claim", "content": "B."},
        ],
        "entities": [], "sources": [],
        "connections": [{"ref": "c1", "from": "p1", "to": "p2",
                         "operator": "IMPL",
                         "mitigation": {"reason": "x", "strength": 0.6}}],
    }
    res = sdk.ingest(bundle, promotion_policy="gated")
    p1, p2 = res["ids"]["points"]
    op_id = res["ids"]["connections"][0]
    assert _status(sdk, op_id) == "draft", "gated operator is draft"
    # promote BOTH endpoints via the interim route
    sdk.update_point(p1, status="live")
    sdk.update_point(p2, status="live")
    assert _status(sdk, p1) == "live" and _status(sdk, p2) == "live"
    # endpoint promotion does NOT touch the operator node
    assert _status(sdk, op_id) == "draft", \
        "endpoint promotion must not promote the operator (no zombie auto-resolution)"
    # the draft operator is STILL inert in default-mode EP selection
    # (draft status → #780 live filter), even though derived liveness
    # (>=2 live endpoints) now passes — the no-zombie-resolution caveat
    ops, _ = _bfs_select_operators(sdk._get_proj(), [p1, p2], max_hops=1)
    assert op_id not in ops, \
        "draft-status operator must stay inert (no zombie auto-resolution)"
    # an EXPLICIT promote of the operator node activates it
    sdk.update_point(op_id, status="live")
    ops2, _ = _bfs_select_operators(sdk._get_proj(), [p1, p2], max_hops=1)
    assert op_id in ops2, \
        "explicitly-promoted operator with >=2 live endpoints is EP-active"
