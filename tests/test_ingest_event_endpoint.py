"""Issue #2062 — operator connections accept Event endpoints on ingest.

The #1919 dedup machinery (`_find_operator`, Point-OR-Event targets after
PR #2056) is reachable via ingest's connection path only, but ingest()
rejected Event endpoints at validation: `_check_endpoints` (Phase-1) and
`_check_endpoint_race` (Phase-2) both required operator-connection endpoints
to be plain Points — a bundle carrying an Event endpoint failed with
`BundleValidationError: ... must be a plain Point — got a Event endpoint`.
Meanwhile create_operator (the direct API surface, A1b #1272) accepts
Point OR Event endpoints, so Event-input operators created via the direct
API could not be deduped via ingest (the #1919 dedup fix was unreachable on
the ingest surface).

Scope: the OPERATOR route (reify:true / mitigation / part-whole →
create_operator) accepts Point OR Event endpoints; the DIRECT-edge route
(plain IMPL/NAND → create_direct_edge, which guards plain Points) and the
relation route are unchanged.

Runnable with: TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_ingest_event_endpoint.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.exceptions import BundleValidationError
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK with temp database (auto-cleaned). Closed after test."""
    with tempfile.TemporaryDirectory(prefix="tortoise_ingest_evt_") as td:
        db_path = os.path.join(td, "test.db")
        sdk = TortoiseSDK(db_path)
        yield sdk
        sdk.close()


def _count(sdk, cypher: str, params: dict | None = None) -> int:
    rows = sdk._get_proj().g.query(cypher, params=params or {}).result_set
    return int(rows[0][0]) if rows else 0


def _operator_count(sdk, op_type: str = "IMPL") -> int:
    return _count(
        sdk,
        "MATCH (o:Point {is_operator:true, op_type:$op}) RETURN count(o)",
        {"op": op_type},
    )


def _event_edge_count(sdk) -> int:
    """Typed edges from an operator to an Event endpoint."""
    return _count(
        sdk,
        "MATCH (o:Point {is_operator:true})-[r:IMPL]->(t:Event) RETURN count(r)",
    )


# ═══════════════════════════════════════════════════════════════════════
# I1: operator connection with Event endpoint passes Phase-1 + Phase-2
# ═══════════════════════════════════════════════════════════════════════

def test_operator_route_external_event_endpoint_passes_both_phases(sdk):
    """#2062 (I1, external): an operator-routed connection (reify:true →
    create_operator) whose endpoint is an EXISTING Event node passes
    Phase-1 (`_validate_bundle` → no violations) AND Phase-2 (ingest's
    per-connection `_check_endpoint_race` re-verify) — pre-fix this bundle
    was rejected with 'must be a plain Point — got a Event endpoint'."""
    pa = sdk.create_point("statement", "A")["id"]
    eid = sdk.create_event("Launch party", "sessionCaptured")["eventId"]
    # single Event endpoint
    bundle = {"connections": [
        {"from": pa, "to": eid, "operator": "IMPL", "reify": True}]}
    assert sdk._validate_bundle(bundle) == []  # Phase-1 clean
    res = sdk.ingest(bundle)  # Phase-2 race check runs per-connection
    assert res["created"]["connections"] == 1
    assert res["deduped"]["connections"] == 0
    assert _event_edge_count(sdk) == 1


def test_operator_route_fanout_reingest_absorbs_ingest_created_draft(sdk):
    """#2062 (I1, multi-input): an operator-route fan-out with an Event among
    plain Points passes both phases; re-ingesting the wider input set against
    the ingest-created DRAFT operator partial-absorbs it (proper-subset
    absorb, #1919 P1) — the operator's edge set is completed, never
    duplicated. Pins the intermediate draft shape (2 edges) so the absorb
    delta (→ 3) is explicit."""
    pa = sdk.create_point("statement", "A")["id"]
    pb = sdk.create_point("statement", "B")["id"]
    eid = sdk.create_event("Launch party", "sessionCaptured")["eventId"]
    bundle = {"connections": [
        {"from": pa, "to": eid, "operator": "IMPL", "reify": True}]}
    res1 = sdk.ingest(bundle)  # creates the {pa, eid} draft operator
    assert res1["created"]["connections"] == 1
    assert _count(sdk, "MATCH (o:Point {is_operator:true})-[r:IMPL]->(t) "
                       "RETURN count(r)") == 2

    bundle2 = {"connections": [
        {"from": pa, "to": [pb, eid], "operator": "IMPL", "reify": True}]}
    assert sdk._validate_bundle(bundle2) == []
    res2 = sdk.ingest(bundle2)
    # the draft {pa, eid} operator partial-absorbs the wider request
    # (proper-subset absorb, #1919 P1 reachable) — never a duplicate
    assert res2["deduped"]["connections"] == 1
    assert _operator_count(sdk) == 1
    assert _event_edge_count(sdk) == 1
    assert _count(sdk, "MATCH (o:Point {is_operator:true})-[r:IMPL]->(t) "
                       "RETURN count(r)") == 3


def test_operator_route_bundle_local_event_ref_passes(sdk):
    """#2062 (I1, bundle-local): an operator-routed connection whose endpoint
    is a bundle `type:"event"` entity item (addressed by ref) passes Phase-1
    + Phase-2 — the bundle-local check accepts Event entity items on the
    operator route (matching the external-label leg)."""
    bundle = {
        "entities": [{"ref": "evt", "type": "event", "name": "Launch party",
                      "eventKind": "sessionCaptured"}],
        "points": [{"ref": "p1", "kind": "statement", "content": "A claim"}],
        "connections": [
            {"from": "p1", "to": "evt", "operator": "IMPL",
             "mitigation": {"reason": "r", "strength": 0.2}},
        ],
    }
    assert sdk._validate_bundle(bundle) == []  # Phase-1 clean
    res = sdk.ingest(bundle)
    assert res["created"]["connections"] == 1
    # the operator's typed edge lands on the created Event node
    evt_id = res["ids"]["refs"]["evt"]
    assert _count(sdk, "MATCH (e:Event {eventId:$id}) RETURN count(e)",
                  params={"id": evt_id}) == 1
    assert _event_edge_count(sdk) == 1


def test_operator_route_event_as_source_endpoint_passes(sdk):
    """#2062 (I1): the operator route accepts an Event as the FROM endpoint
    too — create_operator's A1b #1272 contract covers both positions, and the
    dedup key is the full (source + targets) input set."""
    pa = sdk.create_point("statement", "A")["id"]
    eid = sdk.create_event("Launch party", "sessionCaptured")["eventId"]
    bundle = {"connections": [
        {"from": eid, "to": pa, "operator": "IMPL", "reify": True}]}
    assert sdk._validate_bundle(bundle) == []
    res = sdk.ingest(bundle)
    assert res["created"]["connections"] == 1
    # re-ingest dedups to the same operator (Event id is in the key)
    res2 = sdk.ingest(bundle)
    assert res2["deduped"]["connections"] == 1
    assert _operator_count(sdk) == 1


def test_part_whole_operator_route_event_endpoint_dedups(sdk):
    """#2062 (I1, part-whole): the operator route includes non-IMPL/NAND
    part-whole op types (composedOf/decomposesInto/contains/wraps → hasPart
    edges). An Event endpoint on a composedOf connection passes both phases
    and re-ingests exact-hit through the dedup key — the scope names this
    branch, so it is pinned end-to-end (Phase-1 clean + created + dedup)."""
    pa = sdk.create_point("statement", "A")["id"]
    eid = sdk.create_event("Launch party", "sessionCaptured")["eventId"]
    bundle = {"connections": [
        {"from": pa, "to": eid, "operator": "composedOf", "reify": True}]}
    assert sdk._validate_bundle(bundle) == []
    res1 = sdk.ingest(bundle)
    assert res1["created"]["connections"] == 1
    assert _count(sdk, "MATCH (o:Point {is_operator:true})-[r:hasPart]->"
                       "(t:Event) RETURN count(r)") == 1
    res2 = sdk.ingest(bundle)
    assert res2["deduped"]["connections"] == 1
    assert _count(sdk, "MATCH (o:Point {is_operator:true, op_type:"
                       "'composedOf'}) RETURN count(o)") == 1
    hit = sdk._find_operator("composedOf", [pa, eid])
    assert hit is not None and hit["kind"] == "exact"


def test_nand_operator_route_event_endpoint_dedups(sdk):
    """#2062 (I1, NAND): NAND on the operator route (reify:true) accepts an
    Event endpoint (distinct direction default — unidirectional) and
    re-ingests to the same operator id; pins the op_type-specific branch the
    IMPL-only tests would not catch."""
    pa = sdk.create_point("statement", "A")["id"]
    eid = sdk.create_event("Launch party", "sessionCaptured")["eventId"]
    bundle = {"connections": [
        {"from": pa, "to": eid, "operator": "NAND", "reify": True}]}
    assert sdk._validate_bundle(bundle) == []
    res1 = sdk.ingest(bundle)
    assert res1["created"]["connections"] == 1
    oid = res1["ids"]["connections"][0]
    res2 = sdk.ingest(bundle)
    assert res2["deduped"]["connections"] == 1
    assert res2["ids"]["connections"][0] == oid
    assert _count(sdk, "MATCH (o:Point {is_operator:true, op_type:'NAND'}) "
                       "RETURN count(o)") == 1


def test_operator_route_rejects_non_event_entity_refs(sdk):
    """#2062 (narrowness pin): the operator-route relaxation is NARROW — it
    accepts Event entity items only, never other entity types. A bundle-local
    `type:"document"` ref on an operator connection is still a violation
    (guards against future accidental widening of the branch)."""
    bundle = {
        "entities": [{"ref": "docL", "type": "document",
                       "name": "Report", "documentKind": "report"}],
        "points": [{"ref": "p1", "kind": "statement", "content": "A"}],
        "connections": [
            {"from": "p1", "to": "docL", "operator": "IMPL",
             "reify": True}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("must be a plain Point — got a Document item" in m
               for m in msgs), msgs


def test_operator_route_nonexistent_event_shaped_id_rejected(sdk):
    """#2062 (narrowness pin): the relaxation widens the accepted LABEL, not
    existence — an Event-shaped id that does not exist still fails Phase-1
    with 'does not exist'."""
    pa = sdk.create_point("statement", "A")["id"]
    bundle = {"connections": [
        {"from": pa, "to": "01GHOSTEVENTID", "operator": "IMPL",
         "reify": True}]}
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("does not exist" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError, match="does not exist"):
        sdk.ingest(bundle)


# ═══════════════════════════════════════════════════════════════════════
# I2: Event-input operator re-ingest exact-hits _find_operator
# ═══════════════════════════════════════════════════════════════════════

def test_event_input_operator_reingest_exact_hits_no_duplicate(sdk):
    """#2062 (I2): an Event-input operator created via ingest (external Event
    endpoint) exact-hits `_find_operator` on re-ingest — the same (op_type,
    input set) key surfaces the same operator id, never a duplicate (EP
    double-count). Pre-fix the Event endpoint was rejected at validation, so
    the dedup path was unreachable on the ingest surface."""
    pa = sdk.create_point("statement", "A")["id"]
    eid = sdk.create_event("Launch party", "sessionCaptured")["eventId"]
    bundle = {"connections": [
        {"from": pa, "to": eid, "operator": "IMPL", "reify": True}]}
    res1 = sdk.ingest(bundle)
    assert res1["created"]["connections"] == 1
    oid = res1["ids"]["connections"][0]
    assert _operator_count(sdk) == 1

    res2 = sdk.ingest(bundle)
    assert res2["created"]["connections"] == 0
    assert res2["deduped"]["connections"] == 1
    assert _operator_count(sdk) == 1
    assert res2["ids"]["connections"][0] == oid
    # the dedup hit surfaces through the Point-OR-Event key
    hit = sdk._find_operator("IMPL", [pa, eid])
    assert hit is not None and hit["kind"] == "exact"
    assert hit["id"] == oid


def test_bundle_local_event_ref_rededups_by_resolved_id(sdk):
    """#2062 (I2, bundle-local): the Event entity ref resolves to the created
    Event's id; re-ingesting the CONNECTION against that external id (the
    Event persists — only entity items are append-only) dedups to the same
    operator. Pins that ref resolution lands on the id the dedup key uses."""
    bundle = {
        "entities": [{"ref": "evt", "type": "event", "name": "Launch party",
                      "eventKind": "sessionCaptured"}],
        "points": [{"ref": "p1", "kind": "statement", "content": "A claim"}],
        "connections": [
            {"from": "p1", "to": "evt", "operator": "IMPL", "reify": True}],
    }
    res1 = sdk.ingest(bundle)
    assert res1["created"]["connections"] == 1
    p1_id = res1["ids"]["refs"]["p1"]
    evt_id = res1["ids"]["refs"]["evt"]

    # re-ingest the connection against the RESOLVED Event id (external)
    res2 = sdk.ingest({"connections": [
        {"from": p1_id, "to": evt_id, "operator": "IMPL", "reify": True}]})
    assert res2["deduped"]["connections"] == 1
    assert res2["created"]["connections"] == 0
    assert _operator_count(sdk) == 1


# ═══════════════════════════════════════════════════════════════════════
# I3: the #1919 P1 partial-absorb path is reachable end-to-end via ingest
# ═══════════════════════════════════════════════════════════════════════

def test_event_input_partial_absorb_completes_via_ingest(sdk):
    """#2062 (I3): the #1919 P1 partial-absorb completion loop (Point-OR-Event
    + reverse INPUT, defense-in-depth until now) is REACHABLE via ingest() —
    a draft operator whose written set is a proper subset absorbs the extra
    requested input and completes its typed + INPUT edge set end-to-end (no
    duplicate operator, `operator_absorb_completed` warning emitted). The
    MISSING input is the EVENT, pinning the completion loop's Point-OR-Event
    clause (a Point-only MATCH would silently drop the typed Event edge)."""
    pa = sdk.create_point("statement", "A")["id"]
    pb = sdk.create_point("statement", "B")["id"]
    eid = sdk.create_event("Launch party", "sessionCaptured")["eventId"]
    # draft operator whose written set {pa, pb} is a PROPER SUBSET of the
    # requested set {pa, pb, eid} — the MISSING input is the EVENT, so the
    # absorb's edge-completion loop must write the typed Event edge (the
    # #1919 P1 Point-OR-Event clause; a Point-only MATCH would drop it)
    sdk.create_operator("IMPL", pa, [pb], promote_source=False)

    res = sdk.ingest({"connections": [
        {"from": pa, "to": [pb, eid], "operator": "IMPL", "reify": True}]})
    assert res["created"]["connections"] == 0
    assert res["deduped"]["connections"] == 1
    assert any("operator_absorb_completed" in w for w in res["warnings"]), \
        res["warnings"]
    # the absorb completed the missing input — no duplicate operator
    assert _operator_count(sdk) == 1
    assert _count(sdk, "MATCH (o:Point {is_operator:true})-[r:IMPL]->(t) "
                       "RETURN count(r)") == 3
    # the completion WROTE the typed Event edge (Point-only MATCH → 0 here)
    assert _event_edge_count(sdk) == 1
    # the completion also wrote the reverse INPUT edge for the Event
    assert _count(sdk, "MATCH (e:Event)-[:INPUT]->(o:Point "
                       "{is_operator:true}) RETURN count(*)") == 1
    # the completed operator still exact-hits the FULL key on a later re-ingest
    res2 = sdk.ingest({"connections": [
        {"from": pa, "to": [pb, eid], "operator": "IMPL", "reify": True}]})
    assert res2["deduped"]["connections"] == 1
    assert _operator_count(sdk) == 1


# ═══════════════════════════════════════════════════════════════════════
# I4: plain-Point behavior unchanged (regression pins)
# ═══════════════════════════════════════════════════════════════════════

def test_plain_point_operator_route_behavior_unchanged(sdk):
    """#2062 (regression): the operator route with plain-Point endpoints
    keeps its exact dedup on re-ingest — the relaxation is additive."""
    pa = sdk.create_point("statement", "A")["id"]
    pb = sdk.create_point("statement", "B")["id"]
    bundle = {"connections": [
        {"from": pa, "to": pb, "operator": "IMPL", "reify": True}]}
    sdk.ingest(bundle)
    res = sdk.ingest(bundle)
    assert res["deduped"]["connections"] == 1
    assert res["created"]["connections"] == 0
    assert _operator_count(sdk) == 1
    assert _event_edge_count(sdk) == 0


def test_direct_route_event_endpoint_still_rejected(sdk):
    """#2062 (scope pin): the DIRECT-edge route (plain IMPL/NAND → operator-
    less create_direct_edge, which guards plain Points) keeps its plain-Point
    endpoint validation — an Event endpoint on a plain IMPL connection is
    still a BundleValidationError (Phase-1 violation, Phase-2 raise)."""
    pa = sdk.create_point("statement", "A")["id"]
    eid = sdk.create_event("Launch party", "sessionCaptured")["eventId"]
    bundle = {"connections": [{"from": pa, "to": eid, "operator": "IMPL"}]}
    viols = sdk._validate_bundle(bundle)
    assert viols, "Phase 1 must flag the direct-route Event endpoint"
    assert "must be a plain Point" in viols[0]["message"]
    assert "Event" in viols[0]["message"]
    with pytest.raises(BundleValidationError,
                       match="must be a plain Point — got a Event endpoint"):
        sdk.ingest(bundle)


def test_direct_route_non_point_endpoints_still_rejected(sdk):
    """#2062 (regression, E2E-1 c3/c4/c5 parity): the direct route keeps
    rejecting Source endpoints, operator-shaped point items, AND bundle-local
    Event entity refs — the relaxation never widens the direct-edge surface."""
    pa = sdk.create_point("statement", "A")["id"]
    src = sdk.create_source("https://pre.example/s", "report")
    bundle = {
        "points": [{"ref": "pO", "kind": "statement", "content": "op-shaped",
                    "is_operator": True}],
        "sources": [{"ref": "srcL", "url": "https://local.example/s",
                     "sourceKind": "report"}],
        "entities": [{"ref": "evtL", "type": "event",
                       "name": "Launch", "eventKind": "sessionCaptured"}],
        "connections": [
            {"from": pa, "to": src.get("url"), "operator": "IMPL"},
            {"from": pa, "to": "srcL", "operator": "IMPL"},
            {"from": pa, "to": "pO", "operator": "IMPL"},
            {"from": pa, "to": "evtL", "operator": "IMPL"},
        ],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert sum("must be a plain Point" in m for m in msgs) == 4, msgs
    # the bundle-local Event cell carries the distinct label message
    assert any("got a Event item" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
