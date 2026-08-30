"""Issue #1919 (fix/sdk) — operator dedup sees Event endpoints + INPUT edge
live/replay parity (bug-hunt 2026-08-28 server P2-9/P2-10).

P2-9: _find_operator's target collections matched only ->(t:Point) while
create_operator allows Point OR Event endpoints (A1b #1272) — an operator
whose input set includes an Event node never exact-hit on re-ingest
(duplicate operator → EP double-count).
P2-10: create_operator wrote only (o)-[:REL]->(s), but the OperatorAdded
replay through projection/edges.py _create_edges additionally MERGEs
(s)-[:INPUT]->(o) — rebuilt graphs carried INPUT edges live graphs never
had (fold-parity violation; edge_stats input_edges diverged).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tortoise.sdk import TortoiseSDK


def _count(g, cypher: str, params: dict | None = None) -> int:
    return g.query(cypher, params=params or {}).result_set[0][0]


@pytest.fixture
def s1919(tmp_path):
    """(events_dir, sdk) with the JSONL journal wired (same shape as the
    A10 durability fixture)."""
    db = os.path.join(str(tmp_path), "i1919.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    yield events, sdk
    sdk.close()


def _rebuild(sdk, events_dir: Path) -> None:
    sdk._get_proj().rebuild_all(str(events_dir))


# ═══════════════════════════════════════════════════════════════════════
# P2-9: dedup sees Event endpoints
# ═══════════════════════════════════════════════════════════════════════

def test_operator_dedup_exact_hit_with_event_input(s1919):
    """P2-9: _find_operator collects (t:Point OR t:Event) targets — an
    operator whose input set includes an Event node returns an EXACT hit for
    the full (op_type, input set) key, so a re-ingest dedups instead of
    creating a duplicate operator (EP double-count). Pre-fix the Event
    target was invisible to the target collection → size mismatch → exact
    miss (degraded to partial-absorb or a fresh duplicate operator)."""
    events, sdk = s1919  # noqa: RUF059
    pa = sdk.create_point("statement", "A")["id"]
    eid = sdk.create_event("Launch party", "sessionCaptured")["eventId"]
    op = sdk.create_operator("IMPL", pa, [eid])["id"]
    g = sdk._get_proj().g
    # the operator's input set includes the Event (typed edge to the Event)
    assert _count(g, "MATCH (o:Point {id:$p})-[r:IMPL]->(t) RETURN count(r)",
                  params={"p": op}) == 2
    # the ingest dedup check sees the FULL input set → exact hit, same id
    hit = sdk._find_operator("IMPL", [pa, eid])
    assert hit is not None and hit["kind"] == "exact"
    assert hit["id"] == op
    assert set(hit["written"]) == {pa, eid}
    # a re-check (re-ingest) dedups to the SAME operator — never a duplicate
    hit2 = sdk._find_operator("IMPL", [pa, eid])
    assert hit2["id"] == op
    assert _count(g, "MATCH (o:Point {is_operator:true}) RETURN count(o)") == 1


def test_operator_dedup_event_input_partial_absorb_path(s1919):
    """P2-9 companion: the partial-absorb collection also sees Event targets —
    a NULL-status operator whose written set is a proper subset matches with
    the Event id in `written` (never silently dropped from the key). The draft
    operator itself carries an Event input, so pre-fix (Point-only partial
    collection) the Event is invisible → written={pa} only (silent drop from
    the absorb key). Post-fix the full set surfaces in `written`."""
    events, sdk = s1919  # noqa: RUF059
    pa = sdk.create_point("statement", "A")["id"]
    pb = sdk.create_point("statement", "B")["id"]
    eid = sdk.create_event("Launch party", "sessionCaptured")["eventId"]
    # draft operator with an EVENT input (written set {pa, eid})
    op = sdk.create_operator("IMPL", pa, [eid], promote_source=False)["id"]
    g = sdk._get_proj().g
    # request the set {pa, eid, B}: the operator's written set {pa, eid}
    # is a proper subset → partial-absorb, with the EVENT id in `written`
    found = sdk._find_operator("IMPL", [pa, eid, pb])
    assert found is not None and found["kind"] == "partial"
    assert found["id"] == op
    assert set(found["written"]) == {pa, eid}  # pre-fix: {pa} (Event dropped)
    assert _count(g, "MATCH (o:Point {is_operator:true}) RETURN count(o)") == 1


def test_operator_replay_with_event_input_creates_edges(s1919):
    """#1919 P2 (review gate): the replay half of the fix — an OperatorAdded
    whose input set includes an Event node must resolve the Event endpoint
    through _create_edges (pre-fix the Point-only stub check + edge MERGE
    silently dropped the Event input on rebuild). The EventRecorded is
    journaled via the sanctioned _emit_event path (the session-indexing
    convention, sdk.py _session_event_write) — create_event itself does NOT
    journal (pre-existing gap, out of #1919 scope); rebuild_all's two-pass
    structure upserts all EventRecorded (pass 1b) before wiring operator
    edges (pass 2), so the Event node resolves at edge time."""
    events, sdk = s1919  # noqa: RUF059
    pa = sdk.create_point("statement", "A")["id"]
    eid = sdk.create_event("Launch party", "sessionCaptured")["eventId"]
    # journal the EventRecorded (session-indexing shape) so the rebuild
    # can restore the Event node; then create the operator (journals
    # OperatorAdded with the Event input).
    sdk._emit_event("EventRecorded", id=eid, **{
        "eventId": eid, "eventKind": "sessionCaptured",
        "name": "Launch party", "eventStatus": "scheduled",
    })
    op = sdk.create_operator("IMPL", pa, [eid])["id"]
    g = sdk._get_proj().g
    _rebuild(sdk, events)
    # both endpoints survive the replay: typed edge to Point AND to Event
    assert _count(g, "MATCH (o:Point {id:$p})-[r:IMPL]->(t) RETURN count(r)",
                  params={"p": op}) == 2
    assert _count(g, "MATCH (o:Point {id:$p})-[r:IMPL]->(t:Event) RETURN count(r)",
                  params={"p": op}) == 1
    # INPUT edges mirror the live shape (both endpoints, idx 0/1)
    live_shape = sorted(tuple(r) for r in g.query(
        "MATCH (s)-[r:INPUT]->(o:Point {id:$oid}) "
        "RETURN s.id, r.idx",
        params={"oid": op},
    ).result_set)
    assert live_shape == sorted([(pa, 0), (eid, 1)])
    assert _count(g, "MATCH (o:Point {is_operator:true}) RETURN count(o)") == 1


# ═══════════════════════════════════════════════════════════════════════
# P2-10: live create vs event replay edge parity
# ═══════════════════════════════════════════════════════════════════════

def test_operator_live_vs_replay_edge_stats_equal(s1919):
    """P2-10: create_operator writes the same reverse INPUT edges the
    OperatorAdded replay writes — live graphs and rebuilt graphs produce
    IDENTICAL edge_stats (operators/impl/nand/input). Pre-fix live had
    input_edges=0 while the replay produced one INPUT edge per input
    (fold-parity violation)."""
    events, sdk = s1919  # noqa: RUF059
    pa = sdk.create_point("statement", "A")["id"]
    pb = sdk.create_point("statement", "B")["id"]
    pc = sdk.create_point("statement", "C")["id"]
    sdk.create_operator("IMPL", pa, [pb])
    sdk.create_operator("NAND", pa, [pc], direction="unidirectional")
    proj = sdk._get_proj()
    live = proj.edge_stats()
    # the fix: live creates the reverse INPUT edges (one per input)
    assert live["input_edges"] == 4  # 2 operators x 2 inputs each
    _rebuild(sdk, events)
    replay = proj.edge_stats()
    assert replay == live, (
        f"live-vs-replay divergence: live={live} replay={replay}")
    assert replay["operators"] == 2
    # each operator carries one typed edge per input (2 inputs each)
    assert replay["impl_edges"] == 2 and replay["nand_edges"] == 2


def test_operator_live_input_edge_shape_matches_replay(s1919):
    """P2-10 shape leg: the live INPUT edges carry the same (idx) property the
    replay MERGE writes — (s)-[:INPUT {idx}]->(o) per input, in input order."""
    events, sdk = s1919  # noqa: RUF059
    pa = sdk.create_point("statement", "A")["id"]
    pb = sdk.create_point("statement", "B")["id"]
    op = sdk.create_operator("IMPL", pa, [pb])["id"]
    g = sdk._get_proj().g
    live_shape = sorted(tuple(r) for r in g.query(
        "MATCH (s)-[r:INPUT]->(o:Point {id:$oid}) "
        "RETURN s.id, r.idx",
        params={"oid": op},
    ).result_set)
    assert live_shape == sorted([(pa, 0), (pb, 1)])
    _rebuild(sdk, events)
    replay_shape = sorted(tuple(r) for r in g.query(
        "MATCH (s)-[r:INPUT]->(o:Point {id:$oid}) "
        "RETURN s.id, r.idx",
        params={"oid": op},
    ).result_set)
    assert replay_shape == live_shape
