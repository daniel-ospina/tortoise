"""A13 audit surface (epic #902, issue #1051) — list_batch / list_batches.

Plan §4.2 audit row + W4 A13: `sdk.list_batch(batch_id)` returns the
bundle's STAMPED artifacts — every Point carrying the batch_id (created OR
ADOPTED via dedup — E2E-10 row 14) incl. operator/mitigation Points, and
every operator-less direct edge carrying it. Entities/sources are OUT of
stamp scope (documented); editorial supersede artifacts are outside audit
(the superseding point is not stamped; repointed edges keep their
originating batch_id — E2E-11.6). Completeness holds across rebuild_all
(the pre-wipe snapshot's batch_id enforcement links are restored — the
projection pass-1b tail, A10).

Covers:
- exact stamped set after ingest (nothing outside — no entities/sources);
- crash-retry sharing ONE batch_id (identical re-submission → dedup-adopted
  → the SAME stamped set, no new artifacts);
- direct-edge stamping via the SDK primitive (batch_id edge attribute; the
  ingest-level direct-edge ROUTING is A3-owned #1053 — the audit surface
  reads whatever is stamped);
- post-supersede boundary (editorial point outside audit; original keeps
  the batch);
- post-rebuild completeness (rebuild_all → list_batch unchanged);
- batch discovery (list_batches);
- MCP mirror (tortoise_list_batch / tortoise_list_batches through the
  in-process handler layer).
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_audit_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


def _query(sdk, cypher: str, params: dict | None = None):
    return sdk._get_proj().g.query(cypher, params=params or {}).result_set


def _count(sdk, cypher: str, params: dict | None = None) -> int:
    rows = _query(sdk, cypher, params)
    return int(rows[0][0]) if rows else 0


def _full_bundle():
    """2 points + 1 subject entity + 1 source + IMPL/relation connections.

    The IMPL connection routes to an OPERATOR point (the ingest-level
    direct-edge routing is A3-owned #1053); the operator Point is stamped
    with the bundle's batch_id like every other bundle-created Point."""
    return {
        "points": [
            {"ref": "p1", "kind": "claim", "content": "Rust is memory-safe by default."},
            {"ref": "p2", "kind": "claim",
             "content": "Rust's borrow checker prevents use-after-free."},
        ],
        "entities": [
            {"ref": "s1", "type": "subject", "name": "Ferra Labs",
             "subjectKind": "organization"},
        ],
        "sources": [
            {"ref": "src1", "url": "https://example.com/rust-report",
             "sourceKind": "report", "tier": "T1"},
        ],
        "connections": [
            {"ref": "c1", "from": "p1", "to": "p2", "operator": "IMPL"},
            {"ref": "c2", "from": "s1", "to": "p1", "relation": "authoredBy"},
            {"ref": "c3", "from": "p1", "to": "src1", "relation": "extractedFrom"},
        ],
    }


# ── A13: exact stamped set after ingest ──────────────────────────────

def test_audit_ingest_exact_stamped_set(sdk):
    """A13 indicator 1: ingest → list_batch returns EXACTLY the stamped set
    (the 2 points + the operator Point = 3 Points), nothing outside — no
    entity, no source, no relation edges in the audit."""
    res = sdk.ingest(_full_bundle())
    bid = res["batch_id"]
    assert bid and len(bid) == 26

    audit = sdk.list_batch(bid)
    assert audit["batch_id"] == bid
    assert audit["counts"]["points"] == 3, audit
    assert audit["counts"]["direct_edges"] == 0, audit
    # the 2 statement points + 1 operator point (operators carry NO
    # pointKind — NULL; the statement points are 'claim')
    kinds = sorted((p["pointKind"] or "") for p in audit["points"])
    assert kinds == ["", "claim", "claim"], kinds
    op_pts = [p for p in audit["points"] if p["is_operator"]]
    assert len(op_pts) == 1 and op_pts[0]["op_type"] == "IMPL"
    point_ids = {p["id"] for p in audit["points"]}
    assert point_ids == set(res["ids"]["points"]) | {res["ids"]["connections"][0]}
    # NOTHING outside: no entity/source stamped, no relation edges in audit
    assert _count(sdk, "MATCH (n:Subject) WHERE n.batch_id IS NOT NULL "
                       "RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Source) WHERE n.batch_id IS NOT NULL "
                       "RETURN count(n)") == 0
    assert audit["direct_edges"] == []


def test_audit_entities_sources_out_of_stamp_scope(sdk):
    """A13 scope line: bundle entities and sources are NEVER stamped — the
    audit surface cannot return them (documented out-of-scope)."""
    res = sdk.ingest(_full_bundle())
    bid = res["batch_id"]
    # the source and subject nodes exist but carry no batch_id
    assert _count(sdk, "MATCH (s:Source) RETURN count(s)") == 1
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 1
    assert _count(sdk, "MATCH (s:Source {batch_id:$bid}) RETURN count(s)",
                  {"bid": bid}) == 0
    assert _count(sdk, "MATCH (n:Subject {batch_id:$bid}) RETURN count(n)",
                  {"bid": bid}) == 0
    audit = sdk.list_batch(bid)
    assert all("url" not in p for p in audit["points"])


def test_audit_crash_retry_shares_one_batch_id(sdk):
    """A13 indicator 1 crash-retry: re-submitting the IDENTICAL bundle yields
    the SAME content-derived batch_id and the audit returns the SAME stamped
    set (the dedup-hit acquires/adopts — E2E-10 row 14 — never a second
    set)."""
    res1 = sdk.ingest(_full_bundle())
    res2 = sdk.ingest(_full_bundle())
    assert res1["batch_id"] == res2["batch_id"]
    assert res2["created"]["points"] == 0 and res2["deduped"]["points"] == 2
    a1 = sdk.list_batch(res1["batch_id"])
    a2 = sdk.list_batch(res2["batch_id"])
    assert {p["id"] for p in a1["points"]} == {p["id"] for p in a2["points"]}
    assert a1["counts"] == a2["counts"] == {"points": 3, "direct_edges": 0}


def test_audit_direct_edge_batch_stamp(sdk):
    """A13 direct-edge leg: an operator-less direct edge created with the
    SDK's batch_id param carries the stamp ON THE EDGE and is returned by
    the audit (the ingest-level direct-edge ROUTING is A3-owned #1053 — the
    audit surface reads whatever is stamped)."""
    p1 = sdk.create_point("claim", "A implies B.", status="live")
    p2 = sdk.create_point("claim", "B.", status="live")
    from tortoise.canonical import derive_batch_id
    bid = derive_batch_id({"points": [
        {"ref": "p1", "kind": "claim", "content": "A implies B."},
        {"ref": "p2", "kind": "claim", "content": "B."},
    ], "entities": [], "sources": [], "connections": []})
    sdk.create_direct_edge("IMPL", p1["id"], p2["id"], batch_id=bid)
    audit = sdk.list_batch(bid)
    assert audit["counts"]["direct_edges"] == 1, audit
    edge = audit["direct_edges"][0]
    assert edge["direct_edge"] == "IMPL"
    assert edge["from"] == p1["id"] and edge["to"] == p2["id"]
    assert edge["direction"] == "bidirectional"  # IMPL default


def test_audit_post_supersede_boundary(sdk):
    """A13 indicator 3: the EDITORIAL supersede artifact is OUTSIDE audit —
    superseding a bundle point with a user-created point: the superseding
    (editorial) point is NOT in the batch audit; the superseded original
    keeps its batch_id; no stamp leaks onto the editorial point."""
    res = sdk.ingest(_full_bundle())
    bid = res["batch_id"]
    original = res["ids"]["points"][0]
    editorial = sdk.create_point("claim", "Editorial replacement.")["id"]
    sdk.supersede_point(original, editorial)
    audit = sdk.list_batch(bid)
    audit_ids = {p["id"] for p in audit["points"]}
    assert original in audit_ids, "the superseded original keeps its batch"
    assert editorial not in audit_ids, \
        "the editorial supersede artifact is outside audit (not stamped)"
    # the editorial point carries NO batch_id anywhere
    rows = _query(sdk, "MATCH (n:Point {id:$id}) RETURN n.batch_id",
                  {"id": editorial})
    assert rows[0][0] is None


def test_audit_post_rebuild_completeness(tmp_path):
    """A13 indicator 2: completeness across rebuild_all — the pre-wipe
    snapshot's batch_id enforcement links are restored (projection pass-1b
    tail, A10), so the audit returns the SAME stamped set after a rebuild."""
    events_dir = tmp_path / "events"; events_dir.mkdir()
    events = str(events_dir / "events.jsonl")
    sdk = TortoiseSDK(os.path.join(str(tmp_path), "audit.db"),
                      event_log_path=events)
    try:
        res = sdk.ingest(_full_bundle())
        bid = res["batch_id"]
        before = {p["id"] for p in sdk.list_batch(bid)["points"]}
        assert len(before) == 3
        sdk._get_proj().rebuild_all(str(events_dir))
        after = {p["id"] for p in sdk.list_batch(bid)["points"]}
        assert after == before, \
            f"list_batch must survive rebuild_all: {before} vs {after}"
    finally:
        sdk.close()


def test_audit_batch_discovery(sdk):
    """A13 indicator 4: batch discovery — list_batches returns the recent
    distinct batch_ids with correct point counts, newest first."""
    res1 = sdk.ingest(_full_bundle())
    res2 = sdk.ingest({"points": [
        {"ref": "q1", "kind": "claim", "content": "A second bundle."}],
        "entities": [], "sources": [], "connections": []})
    batches = sdk.list_batches()
    b2 = [b for b in batches if b["batch_id"] == res2["batch_id"]]
    b1 = [b for b in batches if b["batch_id"] == res1["batch_id"]]
    assert b2 and b2[0]["points"] == 1, b2
    assert b1 and b1[0]["points"] == 3, b1
    # distinct, both present, newest (b2 ingested later) first
    assert len(batches) >= 2
    ids = [b["batch_id"] for b in batches]
    assert ids.index(res2["batch_id"]) < ids.index(res1["batch_id"])
    # limit works
    assert len(sdk.list_batches(limit=1)) == 1


# ── MCP mirror ───────────────────────────────────────────────────────

def test_audit_mcp_mirror(sdk, monkeypatch):
    """A13 MCP mirror: tortoise_list_batch / tortoise_list_batches route to
    the SDK through the in-process handler layer (stdio transport)."""
    import tortoise.mcp_server as mcp_mod
    from tortoise.mcp_auth import (_current_team_id, _current_team_limits,
                                   _transport_mode)
    _transport_mode.set("stdio")
    _current_team_id.set(None)
    _current_team_limits.set(None)
    orig = mcp_mod._get_team_sdk
    mcp_mod._get_team_sdk = lambda: sdk
    try:
        res = sdk.ingest(_full_bundle())
        bid = res["batch_id"]
        audit = mcp_mod.tortoise_list_batch(bid)
        assert audit["batch_id"] == bid
        assert audit["counts"]["points"] == 3, audit
        batches = mcp_mod.tortoise_list_batches(limit=5)
        assert any(b["batch_id"] == bid for b in batches)
        # invalid input → structured error, not a crash
        bad = mcp_mod.tortoise_list_batch("")
        assert "error" in bad or "batch_id" in str(bad)
    finally:
        _transport_mode.set(None)
        _current_team_id.set(None)
        _current_team_limits.set(None)
        mcp_mod._get_team_sdk = orig


def test_audit_tool_registry_surface():
    """A13 SC5 surface: the mirror tools are registered, read-only, and
    HTTP-accessible (a read surface)."""
    from tortoise.tool_registry import TOOL_REGISTRY
    by_name = {t.name: t for t in TOOL_REGISTRY}
    for name, method in (("tortoise_list_batch", "list_batch"),
                         ("tortoise_list_batches", "list_batches")):
        entry = by_name.get(name)
        assert entry is not None, f"{name} missing from TOOL_REGISTRY"
        assert entry.sdk_method == method
        assert entry.annotations.readOnlyHint is True
        assert entry.http_policy is True  # read surface → HTTP-accessible
