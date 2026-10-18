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

Scope (#2062, widened by #7925): EVERY operator connection — the OPERATOR
route (reify:true / mitigation / part-whole → create_operator) and the
DIRECT-edge route (plain IMPL/NAND → create_direct_edge) — accepts endpoints
carrying any of the four epistemic labels (Point/Subject/Object/Event),
addressed by the node's ``id``. #7925 closed the #2062 asymmetry: the direct
route used to require plain Points while ``create_direct_edge`` itself had
been widened to the four labels by #7902, so the validator was stricter than
the primitive it calls. The relation route is unchanged.

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
    """#2062 (narrowness pin, restated by #7925): the operator-route
    relaxation admits the EPISTEMIC entity types — subject / object / event —
    and never a document. A bundle-local `type:"document"` ref on an operator
    connection is still a violation (guards against widening the branch past
    `_INGEST_ENDPOINT_LABELS`)."""
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
    # D10 (#5026): a document is a :Source, so the reported graph item label
    # is "Source" (the bundle still declares type:"document" and is still
    # rejected — the narrowness pin is unchanged).
    assert any("must be a Point/Subject/Object/Event endpoint — got a Source "
               "item" in m for m in msgs), msgs


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


def test_operator_route_eventid_only_event_rejected_cleanly(sdk):
    """#2062 (P1 fail-closed pin, code-review #2073): an Event that resolves
    only by eventId (no id property — the ingest_corpus DocumentCreated
    shape) is NOT a valid operator endpoint: create_operator keys by node id,
    so accepting it would pass validation then crash mid-write (partial
    mutation). It must be rejected at Phase-1/Phase-2, never a raw ValueError
    after earlier bundle items committed."""
    g = sdk._get_proj().g
    g.query("CREATE (e:Event {eventId:'EVT-CORPUS-1', name:'Doc', "
            "eventKind:'DocumentCreated'})")
    assert g.query("MATCH (e:Event {eventId:'EVT-CORPUS-1'}) "
                   "RETURN e.id").result_set[0][0] is None  # id-less shape
    pa = sdk.create_point("statement", "A")["id"]
    bundle = {"points": [{"kind": "statement", "content": "B"}],
              "connections": [
                  {"from": pa, "to": "EVT-CORPUS-1", "operator": "IMPL",
                   "reify": True}]}
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    # The refusal names the ADDRESSING, not the label: under the four-label
    # contract a correctly-labelled Event reported as "got a Event endpoint"
    # contradicted itself and hid the remedy (review round 5).
    assert any("resolves to a Event node with NO `id`" in m for m in msgs), msgs
    assert not any("must be a Point/Subject/Object/Event endpoint" in m
                   for m in msgs), msgs
    with pytest.raises(
            BundleValidationError,
            match="resolves to a Event node with NO `id`"):
        sdk.ingest(bundle)
    # zero partial mutation — the point item never committed
    assert _count(sdk, "MATCH (n:Point {content:'B'}) RETURN count(n)") == 0


def test_operator_route_point_with_eventid_prop_not_resolvable(sdk):
    """#2062 (fail-closed pin, code-review re-review): a Point's provenance
    eventId (#1417 capture_session stamp) must NEVER become a resolvable
    endpoint key — addressing a Point by its eventId would pass the Point
    branch (no id-guard), then crash the id-keyed write primitives mid-write
    (partial mutation). Such an endpoint stays 'does not exist'."""
    p = sdk.create_point("statement", "X", eventId="PHA-1")
    pa = sdk.create_point("statement", "A")["id"]
    g = sdk._get_proj().g
    assert g.query("MATCH (n:Point {id:$i}) RETURN n.eventId",
                   params={"i": p["id"]}).result_set[0][0] == "PHA-1"
    bundle = {"points": [{"kind": "statement", "content": "B"}],
              "connections": [
                  {"from": pa, "to": "PHA-1", "operator": "IMPL",
                   "reify": True}]}
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("does not exist" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError, match="does not exist"):
        sdk.ingest(bundle)
    # zero partial mutation
    assert _count(sdk, "MATCH (n:Point {content:'B'}) RETURN count(n)") == 0


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


def test_bundle_local_event_full_bundle_reingest_is_append_only(sdk):
    """#2062 (documented contract pin): Event entity items are APPEND-ONLY —
    re-ingesting the full bundle mints a new Event occurrence (new id) and
    therefore a new operator. This is the deliberate, documented consequence
    (INGEST_CONTRACT row 6) of the bundle-local Event surface: dedup applies
    to id-stable external Event endpoints (or connection-only re-ingest by
    resolved id), NOT to full-bundle re-ingest of Event entity items. The
    operator count tracks the Event occurrence count by design."""
    bundle = {
        "entities": [{"ref": "evt", "type": "event", "name": "Launch party",
                      "eventKind": "sessionCaptured"}],
        "points": [{"ref": "p1", "kind": "statement", "content": "A claim"}],
        "connections": [
            {"from": "p1", "to": "evt", "operator": "IMPL", "reify": True}],
    }
    sdk.ingest(bundle)
    res2 = sdk.ingest(bundle)
    # a second Event occurrence (append-only) → a second operator, by design
    assert _count(sdk, "MATCH (e:Event) RETURN count(e)") == 2
    assert _operator_count(sdk) == 2
    assert res2["created"]["connections"] == 1
    assert res2["deduped"]["connections"] == 0
    # each operator's dedup key is stable against its own Event id
    p1_id = res2["ids"]["refs"]["p1"]
    evt2_id = res2["ids"]["refs"]["evt"]
    hit = sdk._find_operator("IMPL", [p1_id, evt2_id])
    assert hit is not None and hit["kind"] == "exact"
    assert hit["id"] == res2["ids"]["connections"][0]


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


# ═══════════════════════════════════════════════════════════════════════
# I5: #7925 — the endpoint label set is ROUTE-INDEPENDENT
# ═══════════════════════════════════════════════════════════════════════

def test_operator_route_subject_object_endpoints_accepted(sdk):
    """#7925 (companion of the direct-route pin): the OPERATOR route accepts
    `:Subject`/`:Object` endpoints too. The divergence #7925 closes was never
    direct-route-only — `create_operator` has admitted the four epistemic
    labels since #7902 and `_find_operator`'s dedup key sweeps the same four
    (`live.epistemic_disjunction`), so the primitive wrote this connection
    while the validator refused it with 'must be a plain Point — got a
    Subject endpoint'. Re-ingest must exact-hit the operator (no duplicate,
    no double count in EP) — that is what makes the widening safe rather than
    just permissive."""
    sub = sdk.create_subject("Alpha")["id"]
    obj = sdk.create_object("Beta")["id"]
    bundle = {"connections": [{"from": sub, "to": obj, "operator": "IMPL",
                               "reify": True}]}

    assert sdk._validate_bundle(bundle) == []
    res1 = sdk.ingest(bundle)
    assert res1["created"]["connections"] == 1
    assert _operator_count(sdk) == 1
    # the operator is wired to BOTH inputs (typed edge + reverse INPUT edge)
    assert _count(sdk, "MATCH (o:Point {is_operator:true})-[r:IMPL]->() "
                       "RETURN count(r)") == 2
    assert _count(sdk, "MATCH ()-[r:INPUT]->(o:Point {is_operator:true}) "
                       "RETURN count(r)") == 2

    res2 = sdk.ingest(bundle)
    assert res2["created"]["connections"] == 0
    assert res2["deduped"]["connections"] == 1
    assert _operator_count(sdk) == 1


def test_direct_route_event_endpoint_accepted(sdk):
    """#7925 (restated #2062 scope pin): the DIRECT-edge ROUTE REQUEST (plain
    IMPL/NAND) now admits an Event endpoint, exactly as ``create_direct_edge``
    has since #7902 — pre-#7925 the validator refused it with 'must be a plain
    Point — got a Event endpoint' while the primitive wrote the same edge.
    The endpoint-KIND gate (#8499) then decides the STRUCTURE from the resolved
    endpoints: a plain ``:Point`` is one of these, so the pair is
    operator-mediated and is reified — with the ``point_mediated_route``
    warning. The validator's job is only to stop being stricter than the
    primitives; the structure stays the primitive's call."""
    pa = sdk.create_point("statement", "A")["id"]
    eid = sdk.create_event("Launch party", "sessionCaptured")["eventId"]
    bundle = {"connections": [{"from": pa, "to": eid, "operator": "IMPL"}]}
    assert sdk._validate_bundle(bundle) == []
    res = sdk.ingest(bundle)
    assert res["created"]["connections"] == 1
    # Point-bearing ⇒ operator-mediated (#8499) — and the gate says so out loud
    assert _operator_count(sdk) == 1
    assert any("point_mediated_route" in w for w in res["warnings"]), \
        res["warnings"]


def test_direct_route_event_object_endpoint_is_operatorless(sdk):
    """#7925 + #8499 end to end: with NO plain ``:Point`` among the endpoints
    the direct shape is the ruling's own carve-out, so an Event↔Object
    connection writes an operator-less (Event)-[:IMPL]->(Object) edge. Before
    #7925 this bundle never reached the write path at all — validation refused
    both endpoints as 'not a plain Point'."""
    bundle = {
        "entities": [
            {"ref": "evt", "type": "event", "name": "Launch",
             "eventKind": "sessionCaptured"},
            {"ref": "obj", "type": "object", "name": "Beta",
             "objectKind": "product"},
        ],
        "connections": [{"from": "evt", "to": "obj", "operator": "IMPL"}],
    }
    assert sdk._validate_bundle(bundle) == []
    res = sdk.ingest(bundle)
    assert res["created"]["connections"] == 1
    assert _count(sdk, "MATCH (:Event)-[r:IMPL]->(:Object) "
                       "RETURN count(r)") == 1
    assert _operator_count(sdk) == 0
    assert not [w for w in res["warnings"] if "point_mediated_route" in w], \
        res["warnings"]


def test_direct_route_subject_object_endpoints_accepted(sdk):
    """#7925 Indicators 1+2 (the reported defect): a `:Subject`/`:Object`
    direct connection that `create_direct_edge` accepts (#7902) must be
    accepted by ingest too — the two write surfaces agree on the same edge.
    Phase 1 reports 0 violations and ingest writes exactly one
    (Subject)-[:IMPL]->(Object) edge with no raise and no operator."""
    sub = sdk.create_subject("Alpha")["id"]
    obj = sdk.create_object("Beta")["id"]
    bundle = {"connections": [{"from": sub, "to": obj, "operator": "IMPL"}]}

    # Indicator 1 — _validate_bundle reports zero violations
    assert sdk._validate_bundle(bundle) == []

    # Indicator 2 — exactly one edge, written operator-less
    res = sdk.ingest(bundle)
    assert res["created"]["connections"] == 1
    assert _count(sdk, f"MATCH (:Subject {{id:'{sub}'}})-[r:IMPL]->"
                       f"(:Object {{id:'{obj}'}}) RETURN count(r)") == 1
    assert _operator_count(sdk) == 0

    # the SDK primitive and ingest agree on the SAME edge (idempotent MERGE)
    assert sdk.create_direct_edge("IMPL", sub, obj)["created"] is False


def test_direct_route_terminal_entity_endpoint_rejected_phase1(sdk):
    """#7925 (the guard the widening requires): a bundle-local `:Subject`/
    `:Object` ref is merged BY NAME, so it can resolve onto an EXISTING terminal
    node — and `create_direct_edge` refuses a terminal endpoint, which would be
    a raise AFTER the bundle's earlier items committed. Measured before this
    guard landed: the predecessor point item was left behind (partial
    mutation). Phase 1 must catch it, so zero items commit and the error is a
    BundleValidationError rather than a Phase2Error."""
    sub = sdk.create_subject("Alpha")["id"]
    sdk.create_object("Beta")
    sdk.update_entity(sub, status="superseded")
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [{"ref": "s", "type": "subject", "name": "Alpha"},
                     {"ref": "o", "type": "object", "name": "Beta"}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("resolves to terminal Subject" in m and sub in m
               for m in msgs), msgs
    with pytest.raises(BundleValidationError, match="resolves to terminal Subject"):
        sdk.ingest(bundle)
    # ZERO mutation — the point item never committed (#2062/#2971 class)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0
    assert _count(sdk, "MATCH ()-[r:IMPL]->(:Object) RETURN count(r)") == 0


def test_direct_route_entity_declared_terminal_status_rejected_phase1(sdk):
    """#7925: the second shape of the same guard — the item DECLARES a terminal
    `status` and no node holds the name, so the `:Subject` the writer mints is
    BORN terminal and `create_direct_edge` refuses it. Measured before the
    guard landed: the Subject node itself was created and left behind. Phase 1
    must reject, so nothing is minted."""
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [{"ref": "s", "type": "subject", "name": "Gamma",
                      "status": "superseded"},
                     {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("declares status 'superseded'" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError, match="declares status 'superseded'"):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Subject {name:'Gamma'}) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0


def test_direct_route_entity_nested_props_declared_terminal_status_rejected_phase1(sdk):
    """#7925 review P1: the SAME born-terminal shape, spelled the other way.
    `_coerce_props` flattens a nested `props={...}`, so
    `props: {"status": "superseded"}` mints a terminal :Subject exactly as
    the top-level spelling does — but the guard read only `item.get("status")`,
    saw None, and let the bundle through Phase 1. Measured pre-fix: the
    predecessor point AND the :Subject committed, then `create_direct_edge`
    raised (partial mutation). The guard must read the status the WRITER will
    (nested props included; top-level presence wins)."""
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [{"ref": "s", "type": "subject", "name": "Gamma",
                      "props": {"status": "superseded"}},
                     {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("declares status 'superseded'" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError,
                       match="declares status 'superseded'"):
        sdk.ingest(bundle)
    # ZERO mutation — neither the predecessor point nor the born-terminal Subject
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject {name:'Gamma'}) RETURN count(n)") == 0


@pytest.mark.parametrize("bad", [{"a": 1}, ["live"]])
@pytest.mark.parametrize("spelling", ["top", "nested"])
def test_direct_route_entity_unhashable_status_is_a_violation_not_a_crash(
        sdk, bad, spelling):
    """#7925 review P3: a bundle-supplied NON-STRING `status` escaped the guard.

    The entity/Event terminal leg tested the declared value against
    `self._INGEST_TERMINAL_STATUSES`, a FROZENSET, with no type check — so an
    unhashable `status` raised `TypeError: unhashable type` out of
    `_validate_bundle`, which documents itself as pure and "returns EVERY
    violation". Measured pre-fix: `sdk._validate_bundle(bundle)` RAISED instead
    of returning a list, and `ingest()` surfaced
    `{"error": "unhashable type: 'dict'"}` rather than a validation message.

    `:Subject` is the label whose WRITER also refuses a non-string status
    (`create_entity(type='subject')` validates against `SUBJECT_STATUS_VALUES`),
    so Phase 1 must reject it — otherwise the `TypeError` would merely be
    traded for the mid-write abort this PR exists to prevent.
    """
    subject = {"ref": "s", "type": "subject", "name": "Gamma"}
    if spelling == "top":
        subject["status"] = bad
    else:
        subject["props"] = {"status": bad}
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [subject,
                     {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    # The assertion that is impossible pre-fix: a LIST is returned, not raised.
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("declares status" in m and ":Subject" in m for m in msgs), msgs
    # …and it names the writer's vocabulary, so the message is actionable.
    assert any("superseded" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    # ZERO mutation: neither the predecessor point nor the Subject committed.
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject {name:'Gamma'}) RETURN count(n)") == 0


def test_direct_route_object_and_event_status_values_not_over_rejected(sdk):
    """#7925 review P3 (the other direction — guards the fix from over-rejecting).

    Only `:Subject`'s writer validates its `status` vocabulary. The `:Object`
    and `:Event` writers do NOT: a NON-PERSISTABLE value (a dict, bytes, a set)
    is **dropped** by the writer — measured on this head, the node keeps
    `status = 'live'` — so refusing it would reject a bundle the writer
    accepts. That is the over-rejection class round 4 of this PR already fixed
    once, and it is why the array refusal (a PERSISTABLE unhashable value,
    which IS stored and then breaks the direct-edge terminal check) is scoped by
    `_status_is_stored_unhashable` rather than by "not a string".

    Pre-fix this test fails too: the frozenset membership test raised
    `TypeError` before any of these writes could be reached.
    """
    bundle = {
        "entities": [{"ref": "o1", "type": "object", "name": "Alpha",
                      "status": {"a": 1}},
                     {"ref": "o2", "type": "object", "name": "Beta"},
                     # The `:Event` half of the same claim — a non-persistable
                     # status is dropped there too, so it must stay accepted
                     # (review round 4: the name promised an Event and the
                     # fixture had none).
                     # `eventKind` is required by the Event writer itself, so
                     # the item must carry one to reach the status path at all.
                     {"ref": "e1", "type": "event", "name": "Happened",
                      "eventKind": "sessionCaptured", "status": {"a": 1}}],
        "connections": [{"from": "o1", "to": "o2", "operator": "IMPL"}],
    }
    assert sdk._validate_bundle(bundle) == []
    created = sdk.ingest(bundle)
    assert created["created"]["connections"] == 1
    assert _count(sdk, "MATCH (n:Object {name:'Alpha'}) RETURN count(n)") == 1
    # The dict is DROPPED, not stored verbatim — the mechanism the scoping
    # above depends on, so it is asserted rather than described.
    assert sdk._get_proj().g.query(
        "MATCH (n:Object {name:'Alpha'}) RETURN n.status").result_set == [["live"]]
    assert _count(sdk, "MATCH (n:Event {name:'Happened'}) RETURN count(n)") == 1


@pytest.mark.parametrize("spelling", ["top", "nested"])
def test_direct_route_event_declared_terminal_status_rejected_phase1(sdk, spelling):
    """#7925 review P1 (Event arm): the widening makes a bundle-local Event ref
    REACHABLE on the direct route, and `create_event` -> `create_entity(
    type='event')` stores a declared `status` (top-level OR the nested `props`
    spelling `_coerce_props` flattens) verbatim on the Event node — while
    `create_direct_edge` reads `p.status` and refuses a terminal endpoint.
    The Subject/Object half of the guard had no Event arm, so the bundle passed
    Phase 1 empty and aborted MID-WRITE after the predecessor point committed
    (measured). Phase 1 must reject, so nothing commits."""
    item = {"ref": "evt", "type": "event", "name": "Launch",
            "eventKind": "sessionCaptured"}
    if spelling == "top":
        item["status"] = "superseded"
    else:
        item["props"] = {"status": "superseded"}
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [item, {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "evt", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("declares status 'superseded'" in m for m in msgs), msgs
    # review round 2: an Event has NO name merge, so the wording must be the
    # from-the-item one — claiming a merge would assert a mechanism that does
    # not exist for an append-only node
    assert any("the Event it creates is born terminal" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError,
                       match="declares status 'superseded'"):
        sdk.ingest(bundle)
    # ZERO mutation — neither the predecessor point nor the born-terminal Event
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Event {name:'Launch'}) RETURN count(n)") == 0


@pytest.mark.parametrize("spelling", ["top", "nested"])
def test_direct_route_event_outdated_flag_rejected_phase1(sdk, spelling):
    """#7925 review P1 (Event arm, `outdated` leg): `_sanitize_props` refuses
    the server-managed legacy `outdated` flag on presence for the Event writer
    too (top-level OR nested), so a bundle item carrying it raises mid-write
    AFTER the predecessor point committed (measured). Phase 1 must reject on
    both spellings, so nothing commits."""
    item = {"ref": "evt", "type": "event", "name": "Launch",
            "eventKind": "sessionCaptured"}
    if spelling == "top":
        item["outdated"] = True
    else:
        item["props"] = {"outdated": True}
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [item, {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "evt", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("declares the server-managed 'outdated' flag" in m
               for m in msgs), msgs
    with pytest.raises(BundleValidationError, match="outdated"):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Event {name:'Launch'}) RETURN count(n)") == 0


def test_direct_route_live_event_not_over_rejected(sdk):
    """#7925 review P1 (Event arm's OTHER edge): only TERMINAL declared values
    reject. A bundle-local Event declaring `status: 'live'` is not born
    terminal, so the direct Event<->Object edge is legal and must be written
    operator-less. Without this pin the Event arm could over-reject every
    declared status."""
    bundle = {
        "entities": [
            {"ref": "evt", "type": "event", "name": "Launch",
             "eventKind": "sessionCaptured", "status": "live"},
            {"ref": "o", "type": "object", "name": "Delta"},
        ],
        "connections": [{"from": "evt", "to": "o", "operator": "IMPL"}],
    }
    assert sdk._validate_bundle(bundle) == []
    res = sdk.ingest(bundle)
    assert res["created"]["connections"] == 1
    assert _count(sdk, "MATCH (:Event)-[r:IMPL]->(:Object) RETURN count(r)") == 1
    assert _operator_count(sdk) == 0


@pytest.mark.parametrize("spelling", ["top", "nested"])
def test_direct_route_entity_outdated_flag_rejected_phase1(sdk, spelling):
    """#7925 review P1: the server-managed `outdated` flag is the same
    partial-mutation class. `_sanitize_props` refuses it on PRESENCE for the
    entity writer, so a bundle item carrying it (top-level OR the nested
    `props` spelling `_coerce_props` flattens) raises mid-write AFTER the
    predecessor point committed (measured). Phase 1 must reject on both
    spellings, so nothing commits."""
    item = {"ref": "s", "type": "subject", "name": "Gamma"}
    if spelling == "top":
        item["outdated"] = True
    else:
        item["props"] = {"outdated": True}
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [item, {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("declares the server-managed 'outdated' flag" in m
               for m in msgs), msgs
    with pytest.raises(BundleValidationError, match="outdated"):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject {name:'Gamma'}) RETURN count(n)") == 0


def test_direct_route_entity_top_level_status_shadows_nested_props(sdk):
    """#7925 review P1 precedence pin: `_coerce_props` gives top-level kwargs
    precedence by PRESENCE, so a top-level `status: "live"` shadows a nested
    `props={"status": "superseded"}` and the minted :Subject is LIVE — the
    guard must NOT reject the bundle. (Falling back whenever the top-level
    value were absent/falsy would consult a status the writer never uses.)"""
    bundle = {
        "entities": [{"ref": "s", "type": "subject", "name": "Gamma",
                      "status": "live",
                      "props": {"status": "superseded"}},
                     {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    assert sdk._validate_bundle(bundle) == []
    res = sdk.ingest(bundle)
    assert res["created"]["connections"] == 1
    assert _count(sdk, "MATCH (n:Subject {name:'Gamma', status:'live'}) "
                       "RETURN count(n)") == 1


def test_direct_route_live_entity_not_over_rejected(sdk):
    """#7925 (the guard's OTHER edge): a declared terminal status on an entity
    item whose name is ALREADY held by a LIVE node must NOT reject the bundle.
    The name merge keeps the existing node's own status (measured — the writer
    does not apply the re-declared value), so the endpoint stays live and the
    direct edge is legal. Without the by-name existence test the guard would
    reject a bundle the writer accepts."""
    sdk.create_subject("Alpha")
    sdk.create_object("Beta")
    bundle = {
        "entities": [{"ref": "s", "type": "subject", "name": "Alpha",
                      "status": "superseded"},
                     {"ref": "o", "type": "object", "name": "Beta"}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    assert sdk._validate_bundle(bundle) == []
    res = sdk.ingest(bundle)
    assert res["created"]["connections"] == 1
    assert _count(sdk, "MATCH (:Subject {name:'Alpha'})-[r:IMPL]->"
                       "(:Object {name:'Beta'}) RETURN count(r)") == 1


def test_direct_route_non_point_endpoints_still_rejected(sdk):
    """#7925 (restated #2062 regression pin, E2E-1 c3/c4/c5 parity): widening
    the direct route to the four EPISTEMIC labels never widens it past them —
    a `:Source` endpoint (external by url AND bundle-local) and an
    operator-shaped point item are still rejected. The bundle-local Event item
    that #2062 expected to be refused is now ACCEPTED (it moved to the
    epistemic side, see test_direct_route_event_endpoint_accepted) — the pin
    is re-specified, never silently deleted."""
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
    assert sum("must be a Point/Subject/Object/Event endpoint" in m
               for m in msgs) == 3, msgs
    # the two Source cells report the graph item label, the point cell its own
    assert any("got a Source endpoint" in m for m in msgs), msgs
    assert any("got a Source item" in m for m in msgs), msgs
    assert any("got an operator-shaped point item" in m for m in msgs), msgs
    # the bundle-local Event cell is no longer among them (#7925)
    assert not any("evtL" in m for m in msgs), msgs
    # Phase 2 is defense-in-depth: the same bundle raises, never a partial write
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)


# ═══════════════════════════════════════════════════════════════════════
# #7925 review P1 — the guard consults the BUNDLE, not one item
# ═══════════════════════════════════════════════════════════════════════

def test_direct_route_same_name_earlier_item_declared_terminal_status(sdk):
    """#7925 review P1, Shape A: the entity terminal guard resolved the
    declared `status` from the connection's REFERENCED item alone, so it was
    blind to a same-name item the writer processes FIRST.

    Every guard test above declares its name in exactly ONE entity item — which
    is precisely why they all passed while this shape failed open. But the
    name-merge exists for the multi-item shape: `ingest` writes entities in
    step 3 IN BUNDLE ORDER and the merge applies `status` at MINT only
    (`_upsert_subject` sets it in `ON CREATE`; `ON MATCH` never touches it), so
    `s1` mints Gamma terminal and `s2`'s connection then hits
    `create_direct_edge`'s terminal refusal. Measured pre-fix at this head:
    `_validate_bundle` returned [] and `ingest` raised `Phase2Error` with the
    point AND both entities already committed (point 1, Subject 1, Object 1).
    Phase 1 must reject it, leaving ZERO nodes — `main`'s behaviour for the same
    bundle, and the whole point of the guard (#2422/#2971 class).
    """
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        # s1 is NOT referenced by the connection — it only declares the name
        # that s2 references, and it is written first, so it decides the mint.
        "entities": [{"ref": "s1", "type": "subject", "name": "Gamma",
                      "status": "superseded"},
                     {"ref": "s2", "type": "subject", "name": "Gamma"},
                     {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "s2", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("resolves into a terminal status" in m
               and "declares status 'superseded'" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError, match="declares status 'superseded'"):
        sdk.ingest(bundle)
    # ZERO mutation — counted on the graph, not inferred from the exception
    # (the pre-fix failure raised too, it just left the bundle behind)
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Object) RETURN count(n)") == 0


def test_direct_route_same_name_earlier_item_outdated_flag(sdk):
    """#7925 review P1, Shape B: the `outdated` leg had the same blind spot —
    it asked whether the REFERENCED item carried the flag.

    `_sanitize_props` refuses the server-managed flag on PRESENCE for every
    entity writer, so an earlier same-name item carrying it aborts step 3 after
    the point items have committed, whether or not a holder exists. As with
    Shape A, the existing tests declare one item per name, so they could not
    see it. Measured pre-fix at this head: `_validate_bundle` returned [] and
    `ingest` raised a raw `ValueError` with the predecessor point already
    committed (point 1). Phase 1 must reject it, leaving ZERO nodes.
    """
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [{"ref": "s1", "type": "subject", "name": "Gamma",
                      "outdated": True},
                     {"ref": "s2", "type": "subject", "name": "Gamma"},
                     {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "s2", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("declares the server-managed 'outdated' flag" in m for m in msgs), \
        msgs
    with pytest.raises(BundleValidationError,
                       match="declares the server-managed 'outdated' flag"):
        sdk.ingest(bundle)
    # ZERO mutation — counted on the graph, not inferred from the exception
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Object) RETURN count(n)") == 0


# ═══════════════════════════════════════════════════════════════════════
# #7925 review P1 (round 6) — the `outdated` flag is BUNDLE-wide
# ═══════════════════════════════════════════════════════════════════════
# The `outdated` leg lived INSIDE the direct-route terminal guard: it ran
# per connection, on the direct route, for endpoints the connection
# referenced (plus the same-name items under that endpoint's name). But
# `_sanitize_props` refuses the flag on PRESENCE for every entity writer,
# whatever the route and whether or not a connection mentions the item —
# and step 3 writes EVERY entity item, in bundle order. So the four shapes
# below all passed Phase 1 EMPTY and then aborted mid-write. Each test pins
# BOTH halves: the Phase-1 violation AND the zero-mutation residue (the
# defect class here is "right exception, wrong residue" — asserting the
# exception alone would have blessed every one of these).

def test_reify_route_referenced_entity_outdated_flag_rejected_phase1(sdk):
    """The OPERATOR route had no `outdated` leg at all — the flag legs sat
    behind `if route != "direct": continue` — yet `create_entity` refuses the
    flag on presence for a reify-routed bundle's entity items exactly as for a
    direct-routed one.

    Measured pre-fix at this head: `_validate_bundle` returned [] and `ingest`
    raised a raw `ValueError` with the predecessor point already committed
    (Point 1, zero entities). Phase 1 must reject it, leaving ZERO nodes.
    """
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [{"ref": "s", "type": "subject", "name": "Gamma",
                      "outdated": True},
                     {"ref": "o", "type": "object", "name": "Delta"}],
        # reify:true routes to create_operator, not create_direct_edge — the
        # route whose entity write still refuses the flag.
        "connections": [{"from": "s", "to": "o", "operator": "IMPL",
                         "reify": True}],
    }
    viols = sdk._validate_bundle(bundle)
    assert any(v["section"] == "entities" and v["index"] == 0
               and "declares the server-managed 'outdated' flag" in v["message"]
               for v in viols), viols
    with pytest.raises(BundleValidationError,
                       match="declares the server-managed 'outdated' flag"):
        sdk.ingest(bundle)
    # ZERO mutation — counted on the graph, not inferred from the exception
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Object) RETURN count(n)") == 0


def test_reify_route_same_name_earlier_item_outdated_flag_rejected_phase1(sdk):
    """The same-name blind spot, on the operator route: the flag sits on the
    EARLIER item naming the endpoint's name, which no connection references.

    Step 3 writes `s1` first and its own write refuses the flag, so the
    bundle aborts regardless of what `s2`'s connection would do. Measured
    pre-fix at this head: `_validate_bundle` returned [] and `ingest` raised a
    raw `ValueError` with the predecessor point committed (Point 1, zero
    entities). Phase 1 must reject it, leaving ZERO nodes.
    """
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [{"ref": "s1", "type": "subject", "name": "Gamma",
                      "outdated": True},
                     {"ref": "s2", "type": "subject", "name": "Gamma"},
                     {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "s2", "to": "o", "operator": "IMPL",
                         "reify": True}],
    }
    viols = sdk._validate_bundle(bundle)
    assert any(v["section"] == "entities" and v["index"] == 0
               and "declares the server-managed 'outdated' flag" in v["message"]
               for v in viols), viols
    with pytest.raises(BundleValidationError,
                       match="declares the server-managed 'outdated' flag"):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Object) RETURN count(n)") == 0


def test_direct_route_unreferenced_entity_outdated_flag_rejected_phase1(sdk):
    """A flag on an item NO connection references, on the direct route — the
    shape that isolates "per connection" from "per bundle".

    Every guard test above declares the flag on an endpoint (or on a
    same-name sibling of one). This item is written in step 3 and nothing
    addresses it, so an endpoint-scoped read cannot see it at all. Measured
    pre-fix at this head: `_validate_bundle` returned [] and `ingest` raised a
    raw `ValueError` with residue Point 1 / Subject 1 / Object 1. Phase 1 must
    reject it, leaving ZERO nodes.
    """
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [{"ref": "s", "type": "subject", "name": "Gamma"},
                     {"ref": "o", "type": "object", "name": "Delta"},
                     # Unreferenced, and a DIFFERENT name — the flag's own
                     # presence is the whole violation.
                     {"ref": "z", "type": "subject", "name": "Zeta",
                      "outdated": True}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    assert any(v["section"] == "entities" and v["index"] == 2
               and "declares the server-managed 'outdated' flag" in v["message"]
               for v in viols), viols
    with pytest.raises(BundleValidationError,
                       match="declares the server-managed 'outdated' flag"):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Object) RETURN count(n)") == 0


def test_direct_route_unreferenced_event_outdated_flag_rejected_phase1(sdk):
    """The unreferenced shape on an `:Event` — no name merge, no holder to
    probe, and nothing references it: only a bundle-wide read reaches it.

    The Event writer is `create_entity(type='event')` → `_sanitize_props`, so
    it refuses the flag on presence exactly like the named labels. Measured
    pre-fix at this head: `_validate_bundle` returned [] and `ingest` raised a
    raw `ValueError` with residue Point 1 / Subject 1 / Object 1. Phase 1 must
    reject it, leaving ZERO nodes (Event 0 included).
    """
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [{"ref": "s", "type": "subject", "name": "Gamma"},
                     {"ref": "o", "type": "object", "name": "Delta"},
                     {"ref": "evt", "type": "event", "name": "Launch",
                      "eventKind": "sessionCaptured", "outdated": True}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    assert any(v["section"] == "entities" and v["index"] == 2
               and "declares the server-managed 'outdated' flag" in v["message"]
               for v in viols), viols
    with pytest.raises(BundleValidationError,
                       match="declares the server-managed 'outdated' flag"):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Object) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Event) RETURN count(n)") == 0


# ═══════════════════════════════════════════════════════════════════════
# #7925 review P2 (a) — the `points` section has the SAME writer refusal
# ═══════════════════════════════════════════════════════════════════════
# `_sanitize_props` refuses `outdated` on PRESENCE for `create_point`
# (sdk.py, the same line the entity writers hit), and ingest step 2 splats
# every point item into it. The bundle-wide `outdated` arm above closed the
# ENTITY writers only, so the points section was the same partial-mutation
# class left open: right exception, already-mutated graph. Each test pins
# BOTH halves — the Phase-1 violation AND the zero-point residue.

@pytest.mark.parametrize("spelling", ["top", "nested"])
def test_points_section_outdated_flag_rejected_phase1(sdk, spelling):
    """The `outdated` flag on a POINT item — top-level OR the nested `props`
    spelling `_coerce_props` flattens — is refused by the writer on presence,
    and step 2 has no Phase-1 leg of its own for it.

    Measured pre-fix at this head: `_validate_bundle({"points":
    [{"content": "first"}, {"content": "second", "outdated": True}]})`
    returned `[]` and `ingest` raised the writer's `ValueError` with **Point
    1** already committed (the nested spelling identically). No connection is
    involved, so this isolates "per bundle item" from every endpoint-shaped
    read: Phase 1 must reject it, leaving ZERO points.
    """
    item = {"kind": "statement", "content": "must-not-commit"}
    if spelling == "top":
        item["outdated"] = True
    else:
        item["props"] = {"outdated": True}
    bundle = {"points": [{"kind": "statement", "content": "first"}, item]}
    viols = sdk._validate_bundle(bundle)
    # The flag is the ONLY thing wrong with this bundle, and the message has
    # to say WHERE — section and index — now that one loop serves both
    # item sections.
    assert [(v["section"], v["index"]) for v in viols] == [("points", 1)], viols
    assert "declares the server-managed 'outdated' flag" in viols[0]["message"]
    assert "points[1]" in viols[0]["message"], viols[0]["message"]
    with pytest.raises(BundleValidationError,
                       match="declares the server-managed 'outdated' flag"):
        sdk.ingest(bundle)
    # ZERO mutation — counted on the graph, not inferred from the exception
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0


@pytest.mark.parametrize("spelling", ["top", "nested"])
def test_direct_route_operator_shaped_entity_item_rejected_phase1(sdk, spelling):
    """#7925 review P1 (found in review of the bundle-wide fix, and re-found on a
    second axis in the next round): the label gate's POINT arm always excluded
    an operator-shaped item; the ENTITY arm added by this PR did not — and a
    one-item, top-level-only read still missed both remaining spellings.

    `is_operator` is not in `_SUBJECT_HANDLED`/`_OBJECT_HANDLED`, so the entity
    writer persists it onto the node — and then BOTH `create_direct_edge` and
    the Phase-2 `_check_endpoint_race` refuse an operator endpoint. Phase 1 was
    therefore looser than its own Phase-2 twin. `nested` is the `props={...}`
    spelling `_coerce_props` flattens (measured: it mints the same operator
    node). Pre-fix, both spellings left Point 1, Subject 1 and Object 1 behind.
    The widening is what makes the entity arm responsible; the exclusion is
    route-independent, exactly like the point arm's.
    """
    item = {"ref": "s", "type": "subject", "name": "Gamma"}
    if spelling == "top":
        item["is_operator"] = True
    else:
        item["props"] = {"is_operator": True}
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [item, {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("got an operator-shaped entity item" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError,
                       match="operator-shaped entity item"):
        sdk.ingest(bundle)
    # ZERO mutation — counted on the graph, not inferred from the exception
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Object) RETURN count(n)") == 0


def test_direct_route_operator_shaped_same_name_item_rejected_phase1(sdk):
    """#7925 review P1 (review round 2 — the third axis): the operator-shaped
    item need not be the REFERENCED one, and the referenced one need not be the
    only item under the name.

    `is_operator` rides `_persist_extra_props`, whose `ON MATCH` clause writes it
    onto the MERGED node — so an operator-shaped same-name item taints the node
    for every ref that resolves to it, on either side of the referenced item.
    The worst shape is with a pre-existing holder: measured pre-fix, the bundle
    marked an EXISTING live Subject `is_operator: true` and then aborted with
    Point 1 / Object 1 also left behind. The terminal guard's by-name pre-scan
    already holds this key; the label gate reads the same pre-scan, with no
    `exists` guard, because the taint applies whether or not the name is already
    held.

    As with the other guard tests, the earlier pins used ONE entity item per
    name — which is why the taint shape was missed.
    """
    sub = sdk.create_subject("Gamma")["id"]
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [{"ref": "s1", "type": "subject", "name": "Gamma"},
                     {"ref": "s2", "type": "subject", "name": "Gamma",
                      "is_operator": True},
                     {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "s1", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("whose bundle item 's2' is operator-shaped" in m
               for m in msgs), msgs
    with pytest.raises(BundleValidationError,
                       match="whose bundle item 's2' is operator-shaped"):
        sdk.ingest(bundle)
    # ZERO mutation beyond the seeded Subject, and the seeded node is untouched
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Object) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 1
    assert _count(sdk, "MATCH (n:Subject {id:$id, is_operator:true}) "
                       "RETURN count(n)", {"id": sub}) == 0


def test_direct_route_name_merged_self_edge_rejected_phase1(sdk):
    """#7925 review P1 (also found in review of the bundle-wide fix): two
    DISTINCT bundle-local refs to the same merged name are ONE node.

    `:Subject`/`:Object` are minted on the deterministic `(label, name)` id and
    MERGE by name, so a direct edge from one ref to the other is a self-edge —
    and `create_direct_edge` refuses it with a raw `ValueError` after steps 2-3
    committed (measured: Point 1, Subject 1). `_check_connection`'s self-edge
    test compares REF STRINGS, so only a bundle-aware check can see it — the
    same `(label, name)` key the terminal guard already builds. Reachable only
    BECAUSE of the widening: before it, a Point-bearing pair was reified and a
    non-Point pair never reached the direct route. The reified route is
    deliberately not rejected here — `create_operator` tolerates the shared
    node (measured).
    """
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [{"ref": "s1", "type": "subject", "name": "Gamma"},
                     {"ref": "s2", "type": "subject", "name": "Gamma"}],
        "connections": [{"from": "s1", "to": "s2", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("'s1' and 's2' both resolve to the Subject" in m
               and "address the same endpoint" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError,
                       match="from and to address the same endpoint"):
        sdk.ingest(bundle)
    # ZERO mutation — counted on the graph, not inferred from the exception
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Object) RETURN count(n)") == 0


@pytest.mark.parametrize("reify", [False, True])
def test_external_endpoint_tainted_by_operator_shaped_name_rejected_phase1(
        sdk, reify):
    """#7925 review round 3: step 3 writes EVERY entity item, so an
    operator-shaped item taints its name even when NO connection references it —
    and a connection may address that same node BY ITS ID.

    Phase 1 read the graph only for external endpoints, so this bundle passed and
    then raised in Phase 2 after the point had committed AND a pre-existing live
    Subject had been marked `is_operator: true` (measured). Both routes refuse it
    (`_check_endpoint_race` is route-independent), so the Phase-1 check must be
    too — hence `reify` here.
    """
    sub = sdk.create_subject("Gamma")["id"]
    obj = sdk.create_object("Delta")["id"]
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [{"ref": "s2", "type": "subject", "name": "Gamma",
                      "is_operator": True}],
        "connections": [{"from": sub, "to": obj, "operator": "IMPL",
                         "reify": reify}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("names the Subject 'Gamma'" in m
               and "is operator-shaped" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError,
                       match="so the endpoint is operator-shaped"):
        sdk.ingest(bundle)
    # ZERO mutation — no point, and the pre-existing Subject is NOT tainted
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject {id:$id, is_operator:true}) "
                       "RETURN count(n)", {"id": sub}) == 0
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 1


def test_pre_existing_operator_shaped_holder_rejected_phase1(sdk):
    """#7925 review round 3: the by-name legs read a holder for TERMINALITY only,
    never for operator-ness — so a bundle that merely NAMES an operator-shaped
    node passed Phase 1 and died in step 4, with the point committed.

    The state is not exotic: `ingest` itself mints one from an unbounded entity
    item, which is why the failing run of the test above can poison the graph for
    the next one. The holder probe now returns the holder's operator-ness from
    the MATCH it already ran (same query, one more column).
    """
    sdk.create_subject("Gamma", is_operator=True)
    sdk.create_object("Delta")
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [{"ref": "s", "type": "subject", "name": "Gamma"},
                     {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("merge target is operator-shaped" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError,
                       match="merge target is operator-shaped"):
        sdk.ingest(bundle)
    # ZERO mutation — no point committed, no second Subject minted
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 1


def test_direct_route_external_id_self_edge_rejected_phase1(sdk):
    """#7925 review round 3: the merged-name self-edge is not confined to
    ref-vs-ref — a bundle-local ref and the SAME node addressed by its id share
    the (label, name) identity, and `create_direct_edge` refuses the pair with a
    raw ValueError after steps 2-3 committed (measured: point left behind).

    Identity is therefore compared on the LANDING ID for BOTH sides of the
    bundle/external boundary, not on ref strings and not on the name: a bundle
    entity ref lands on `entity_name_id(label, name)` and an external ref on the
    id it names, so the pair is one node here.
    """
    sub = sdk.create_subject("Gamma")["id"]
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [{"ref": "s", "type": "subject", "name": "Gamma"}],
        "connections": [{"from": "s", "to": sub, "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("both resolve to the Subject" in m and "address the same endpoint" in m
               for m in msgs), msgs
    with pytest.raises(BundleValidationError,
                       match="from and to address the same endpoint"):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 1


def test_reified_route_merged_name_twins_not_rejected(sdk):
    """#7925 review round 3 (over-rejection pin): the merged-name self-edge check
    is DIRECT-route only.

    Two same-name refs on the reified route go through `create_operator`, which
    tolerates the shared node (measured: ingests cleanly, one Subject plus the
    operator). Without this pin a later tightening of the identity check could
    start rejecting a bundle the writer accepts.
    """
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [{"ref": "s1", "type": "subject", "name": "Gamma"},
                     {"ref": "s2", "type": "subject", "name": "Gamma"}],
        "connections": [{"from": "s1", "to": "s2", "operator": "IMPL",
                         "reify": True}],
    }
    assert sdk._validate_bundle(bundle) == []
    res = sdk.ingest(bundle)
    assert res["created"]["connections"] == 1
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 1
    assert _operator_count(sdk) == 1


@pytest.mark.parametrize("spelling", ["top", "nested"])
@pytest.mark.parametrize("shape", ["heals-holder", "heals-bundle-item"])
def test_operator_shaped_name_cleared_by_falsy_declaration_accepted(
        sdk, shape, spelling):
    """#7925 review round 4 (over-rejection pin): the operator axis is
    LAST-DECLARATION-WINS, not a monotone taint.

    `is_operator` rides the extra-props lane, whose clause is `SET n += $extra`
    on the MERGED node, so a later `is_operator: False` under the same name
    CLEARS the flag an earlier item (or a pre-existing holder) set — and the
    endpoint is then legal. An OR over the items refused this bundle while the
    writer accepted it (measured). Both taint SOURCES (an existing holder, an
    earlier bundle item) and both SPELLINGS (`is_operator` top-level, the
    `props={...}` form `_coerce_props` flattens) are pinned here, because any of
    them could regress independently.
    """
    if shape == "heals-holder":
        sdk.create_subject("Gamma", is_operator=True)
        entities = [{"ref": "s", "type": "subject", "name": "Gamma"}]
    else:
        sdk.create_subject("Gamma")
        entities = [{"ref": "s1", "type": "subject", "name": "Gamma",
                     "is_operator": True},
                    {"ref": "s", "type": "subject", "name": "Gamma"}]
    if spelling == "top":
        entities[-1]["is_operator"] = False
    else:
        entities[-1]["props"] = {"is_operator": False}
    sdk.create_object("Delta")
    bundle = {
        "entities": [*entities,
                     {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    assert sdk._validate_bundle(bundle) == []
    res = sdk.ingest(bundle)
    assert res["created"]["connections"] == 1
    # the writer's outcome: the flag is CLEARED on the merged node
    assert _count(sdk, "MATCH (n:Subject {name:'Gamma'}) WHERE "
                       "coalesce(n.is_operator,false) "
                       "RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject {name:'Gamma'})-[r:IMPL]->"
                       "(:Object {name:'Delta'}) RETURN count(r)") == 1


@pytest.mark.parametrize("spelling", ["top", "nested"])
def test_operator_null_does_not_clear_operator_shape_rejected_phase1(
        sdk, spelling):
    """#7925 review round 5: `is_operator: None` is NOT a clearing declaration.

    `_persist_extra_props` drops `None` (and any non-persistable value) before
    it builds `SET n += $extra`, so no write happens and an earlier flag — an
    item's or a holder's — stands. Counting key PRESENCE as a declaration made
    Phase 1 pass the bundle and step 4 then refuse the still-operator-shaped
    node, after the point had committed (measured). "Declares" must mean "the
    writer will write a boolean", the same standard the sibling helpers use.
    """
    sub = sdk.create_subject("Gamma", is_operator=True)["id"]
    item = {"ref": "s", "type": "subject", "name": "Gamma"}
    if spelling == "top":
        item["is_operator"] = None
    else:
        item["props"] = {"is_operator": None}
    sdk.create_object("Delta")
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [item, {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("merge target is operator-shaped" in m
               for m in msgs), msgs
    with pytest.raises(BundleValidationError,
                       match="merge target is operator-shaped"):
        sdk.ingest(bundle)
    # ZERO mutation — the point never committed, the holder is unchanged
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject {id:$id, is_operator:true}) "
                       "RETURN count(n)", {"id": sub}) == 1


def test_operator_shaped_taint_without_ref_names_the_bundle_item(sdk):
    """#7925 review round 4 (message accuracy): a taint that came from the
    BUNDLE must not be reported as an "existing name holder".

    A ref-less entity item is legitimate (`ref` is optional), and the graph may
    hold no such node at all — so blaming a holder is a false mechanism, and the
    caller cannot tell which item to fix. The refusal is the same either way;
    only the sentence must be true.
    """
    bundle = {
        "entities": [{"type": "subject", "name": "Gamma", "is_operator": True},
                     {"ref": "s", "type": "subject", "name": "Gamma"},
                     {"ref": "o", "type": "object", "name": "Delta"}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("bundle item (no" in m and "is operator-shaped" in m
               for m in msgs), msgs
    assert not any("existing name holder" in m for m in msgs), msgs
    # and the graph holds NO holder at all — the sentence is the only signal
    assert _count(sdk, "MATCH (n:Subject) RETURN count(n)") == 0
    with pytest.raises(BundleValidationError,
                       match="bundle item \\(no"):
        sdk.ingest(bundle)


def test_external_operator_shaped_endpoint_cleared_by_bundle_declaration(sdk):
    """#7925 review round 5 (over-rejection surviving one arm over): the
    external-endpoint arm refused an operator-shaped holder even when the bundle
    declares `is_operator: False` under its name.

    Step 3 merges onto that holder and `SET n += $extra` writes the flag away,
    so the writer accepts the bundle and writes the edge — measured directly
    (`create_subject(..., is_operator=False)` then `create_direct_edge`). The
    bundle-local arm already deferred to the declaration; both arms must, or one
    gate holds two verdicts about one node.
    """
    sub = sdk.create_subject("Gamma", is_operator=True)["id"]
    obj = sdk.create_object("Delta")["id"]
    bundle = {
        "entities": [{"ref": "sx", "type": "subject", "name": "Gamma",
                      "is_operator": False}],
        "connections": [{"from": sub, "to": obj, "operator": "IMPL"}],
    }
    assert sdk._validate_bundle(bundle) == []
    res = sdk.ingest(bundle)
    assert res["created"]["connections"] == 1
    assert _count(sdk, "MATCH (n:Subject {name:'Gamma'}) WHERE "
                       "coalesce(n.is_operator,false) "
                       "RETURN count(n)") == 0
    assert _count(sdk, "MATCH ()-[r:IMPL]->(:Object {name:'Delta'}) "
                       "RETURN count(r)") == 1


@pytest.mark.parametrize("spelling", ["top", "nested", "double-nested"])
@pytest.mark.parametrize("shape", ["no-connections", "reified-route"])
def test_subject_status_vocabulary_is_refused_bundle_wide(sdk, shape, spelling):
    """#7925 review P2: the `:Subject` status-vocabulary arm was route- and
    reference-scoped, so it failed open on two shapes — and the writer still
    raises in step 3, after step 2 committed.

    `create_entity(type='subject')` validates `status` against
    `SUBJECT_STATUS_VALUES` for EVERY entity item, in bundle order, before any
    merge. Phase 1 must therefore refuse it (a) for an item no connection
    names, and (b) for an item reached only by a connection the removed arm
    skipped — that arm sat behind `if route != "direct": continue`, and
    `_connection_route` classifies a `reify: true` connection as the OPERATOR
    route (measured), so the reified shape is the route-scoped one. A plain
    `{"operator": "IMPL"}` connection is DIRECT and would not discriminate.

    Measured pre-fix on both shapes:

        _validate_bundle(bundle) -> []          # fail-open
        ingest(bundle)           -> ValueError  # after Point 1 committed
        MATCH (n:Point {content:'must-not-commit'}) -> 1

    `double-nested` is `props: {"props": {"status": ...}}`: step 3 calls
    `create_subject` (one `_coerce_props`) which relays to `create_entity`
    (a second), so the writer folds TWO levels. A Phase-1 reader that folds
    once is blind to it (review P2). A `points` item also reaches depth 2 — on
    its DEDUP relay into `update_point`, not on the mint path; see
    `_item_prop_at_depth` for the per-door map.
    """
    subject = {"ref": "s", "type": "subject", "name": "Gamma"}
    if spelling == "top":
        subject["status"] = "bogus"
    elif spelling == "nested":
        subject["props"] = {"status": "bogus"}
    else:
        subject["props"] = {"props": {"status": "bogus"}}
    bundle = {
        "points": [{"ref": "pX", "kind": "statement",
                    "content": "must-not-commit"}],
        "entities": [subject,
                     {"ref": "o", "type": "object", "name": "Delta"}],
    }
    if shape == "reified-route":
        bundle["connections"] = [{"from": "s", "to": "o",
                                  "operator": "IMPL", "reify": True}]
    viols = sdk._validate_bundle(bundle)
    msgs = [v["message"] for v in viols]
    assert any("declares status 'bogus'" in m and ":Subject" in m
               for m in msgs), msgs
    with pytest.raises(BundleValidationError, match="declares status 'bogus'"):
        sdk.ingest(bundle)
    # ZERO mutation — no point, no Subject, no edge.
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Subject {name:'Gamma'}) RETURN count(n)") == 0
    assert _count(sdk, "MATCH ()-[r:IMPL]->(:Object {name:'Delta'}) "
                       "RETURN count(r)") == 0


@pytest.mark.parametrize("shape", [
    # (label, item payload, accepted?) — "accepted" cases are ones the WRITE
    # DOOR accepts, so Phase 1 must not refuse them.
    ("subject", {"props": {"props": {"props": {"status": "bogus"}}}}, True),
    ("subject", {"props": {"props": {"props": {"outdated": True}}}}, True),
    ("subject", {"props": {"props": {"props": {"is_operator": True}}}}, True),
    ("document", {"props": {"props": {"outdated": True}}}, True),
    ("subject", {"props": {"props": {"status": "bogus"}}}, False),
])
def test_entity_prop_depth_matches_the_write_door(sdk, shape):
    """#7925 review P2 (both directions): a Phase-1 reader must fold exactly as
    many ``props`` levels as the item's WRITE DOOR does.

    The door depth is a property of the door, not of the section:
      - ``subject``/``object``/``event`` → 2 (step 3 calls ``create_subject`` =
        one ``_coerce_props``, which relays ``**props`` to ``create_entity`` =
        a second);
      - ``document`` → 1 (``create_document`` folds once and relays to the
        low-level ``_create_entity`` — no second fold), so a depth-2 reader
        would refuse a `:Source` the writer accepts;
      - a ``points`` item → 2, because ``create_point`` folds once and its
        DEDUP relay into ``update_point`` folds again (see the sibling test).

    A reader that stops short is blind to a key the writer applies (fail-open →
    a mid-write raise after step 2 committed); a reader that folds further
    refuses a bundle the writer accepts (over-rejection). Both directions were
    measured on this PR, so both are pinned here: a THIRD level is visible to
    neither reader nor writer, and an accepted shape must stay accepted.
    """
    label, extra, accepted = shape
    item = {"type": label, "name": "S"}
    if label == "document":
        item["documentKind"] = "note"
    item.update(extra)
    bundle = {"entities": [item]}
    viols = sdk._validate_bundle(bundle)
    if accepted:
        assert viols == [], viols
        sdk.ingest(bundle)  # the writer accepts it — no mid-write raise
    else:
        assert viols, "depth-2 spelling must be refused in Phase 1"
        with pytest.raises(BundleValidationError):
            sdk.ingest(bundle)


def test_point_dedup_relay_double_nested_outdated_is_refused_in_phase1(sdk):
    """#7925 review P2: the POINTS door also folds twice on its DEDUP path.

    ``create_point`` folds once, then, on a dedup hit, relays the residual props
    to ``update_point``, which folds AGAIN — so
    ``props: {"props": {"outdated": True}}`` is invisible to a one-fold reader
    and ``update_point``'s ``_sanitize_props`` raises in step 2 AFTER the
    bundle's earlier point committed (measured at f6e3f80d7: ``Point 1``
    residue). The bundle-wide loop reads the points section at depth 2, so the
    refusal happens before any write.

    (A non-dedup mint folds once and would DROP the residual map, so this
    refusal is a deliberate fail-closed over-rejection of a pathological
    spelling — the ambiguity is unresolvable at Phase 1 without re-deriving the
    dedup resolution, and refusing is the safe direction.)
    """
    sdk.create_point("statement", "dedup-target")
    bundle = {
        "points": [
            {"kind": "statement", "content": "must-not-commit"},
            {"kind": "statement", "content": "dedup-target",
             "props": {"props": {"outdated": True}}},
        ],
    }
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("outdated" in m and "points[1]" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0


def test_node_with_unhashable_status_is_refused_not_a_crash(sdk):
    """#7925 review P2: a node can carry an ARRAY status (FalkorDB stores
    arrays), and ``create_direct_edge`` tests membership against a FROZENSET —
    ``['retracted'] in TERMINAL_EXCLUDED_STATUSES`` raises ``TypeError``.

    Phase 1 must therefore REFUSE the value, not read it as "not terminal":
    skipping it would move the crash from before any write to step 4, after
    steps 2-3 committed — the partial-mutation class this gate exists to
    close. Measured pre-fix: ``_validate_bundle`` returned ``[]`` and ``ingest``
    raised ``TypeError`` with the earlier point already committed.
    """
    sdk.create_object("Alpha")
    sdk.create_object("Beta")
    proj = sdk._get_proj()
    proj.g.query("MATCH (n:Object {name:'Alpha'}) SET n.status = ['retracted']")
    alpha = proj.g.query(
        "MATCH (n:Object {name:'Alpha'}) RETURN n.id").result_set[0][0]
    beta = proj.g.query(
        "MATCH (n:Object {name:'Beta'}) RETURN n.id").result_set[0][0]
    bundle = {
        "points": [{"kind": "statement", "content": "must-not-commit"}],
        "connections": [{"from": alpha, "to": beta, "operator": "IMPL"}],
    }
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("cannot evaluate" in m and "['retracted']" in m
               for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0


def test_bundle_declared_array_status_is_refused_in_phase1(sdk):
    """#7925 review P2: the array-status refusal must cover the VALUE THE BUNDLE
    MINTS, not only nodes read back from the graph.

    The cycle-2 guard sat on the two graph-read legs, so a bundle-local
    `:Object` declaring `status: ["retracted"]` passed Phase 1, the entity
    writers stored the array verbatim, and `create_direct_edge`'s frozenset
    membership then raised in step 4 — with the point AND the Object already
    written (measured). Phase 1 must refuse the item before anything commits,
    which also stops the bundle minting a node no later direct-route ingest can
    connect to.

    The refusal is scoped to PERSISTABLE unhashable values: a dict status is
    dropped by the writer, so it must stay accepted (the sibling
    `..._not_over_rejected` test pins that direction).
    """
    bundle = {
        "points": [{"kind": "statement", "content": "must-not-commit",
                    "ref": "p0"}],
        "entities": [
            {"ref": "o1", "type": "object", "name": "Alpha",
             "status": ["retracted"]},
            {"ref": "o2", "type": "object", "name": "Beta"},
        ],
        "connections": [{"from": "p0", "to": "o1", "operator": "IMPL"}],
    }
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("would store status" in m and "entities[0]" in m
               for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0
    assert _count(sdk, "MATCH (n:Object {name:'Alpha'}) RETURN count(n)") == 0


def test_merge_onto_array_status_holder_is_refused_in_phase1(sdk):
    """#7925 review P2: the same unevaluable value on an EXISTING name holder.

    A `:Subject`/`:Object` ref merges onto whichever node holds the name, so a
    holder carrying an array `status` is the second way the array reaches
    `create_direct_edge`. Nothing else can see it: the terminal predicate is a
    Cypher comparison that simply does not match an array, and the operator axis
    is unrelated. Measured pre-fix: Phase 1 `[]`, then a step-4 raise with the
    earlier point committed.
    """
    sdk.create_object("Holder")
    sdk._get_proj().g.query(
        "MATCH (n:Object {name:'Holder'}) SET n.status = ['retracted']")
    bundle = {
        "points": [{"kind": "statement", "content": "must-not-commit",
                    "ref": "p0"}],
        "entities": [{"ref": "h", "type": "object", "name": "Holder"}],
        "connections": [{"from": "p0", "to": "h", "operator": "IMPL"}],
    }
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("resolves onto the Object" in m and "cannot be evaluated" in m
               for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0


def test_gated_policy_reads_point_status_at_the_dedup_depth(sdk):
    """#7925 review P2: row 9 of the contract must hold at the depth the points
    door writes.

    `create_point` folds once and then, on a DEDUP hit, relays the residual
    props to `update_point`, which folds AGAIN — so
    `props: {"props": {"status": "live"}}` promoted a draft point under
    `promotion_policy="gated"` while a one-level reader saw nothing (measured:
    status went `'draft'` → `'live'`, silently, with `_validate_bundle` []).
    The gated reader now uses the same per-door depth as the `outdated` leg.
    """
    sdk.create_point("statement", "dedup-target", status="draft")
    for spelling in ({"props": {"status": "live"}},
                     {"props": {"props": {"status": "live"}}}):
        bundle = {"points": [{"kind": "statement",
                              "content": "dedup-target", **spelling}]}
        msgs = [v["message"]
                for v in sdk._validate_bundle(bundle, promotion_policy="gated")]
        assert any("promotion_policy 'gated'" in m for m in msgs), (spelling,
                                                                   msgs)
        with pytest.raises(BundleValidationError):
            sdk.ingest(bundle, promotion_policy="gated")
        assert sdk._get_proj().g.query(
            "MATCH (n:Point {content:'dedup-target'}) RETURN n.status"
        ).result_set == [["draft"]]


def test_object_item_status_is_accepted_when_a_holder_owns_the_name(sdk):
    """#7925 review round 4 (over-rejection): the array refusal fires only at
    the item that MINTS the name.

    The merge does not overwrite an existing node's `status` (it is
    projection-owned for `:Object`), so an item re-declaring a status for a name
    that already has a holder is DROPPED and the writer accepts it — measured:
    `create_object("Held", status=["retracted"])` stores `'live'`. Refusing the
    bundle here would reject a write the writer performs, and the HOLDER's own
    array status is the case the direct-route leg catches.
    """
    sdk.create_object("Held")
    bundle = {
        "entities": [{"ref": "h", "type": "object", "name": "Held",
                      "status": ["retracted"]},
                     {"ref": "b", "type": "object", "name": "Beta"}],
        "connections": [{"from": "h", "to": "b", "operator": "IMPL"}],
    }
    assert sdk._validate_bundle(bundle) == []
    created = sdk.ingest(bundle)
    assert created["created"]["connections"] == 1
    assert sdk._get_proj().g.query(
        "MATCH (n:Object {name:'Held'}) RETURN n.status").result_set == [["live"]]


def test_operator_shaped_holder_with_array_status_is_refused_in_phase1(sdk):
    """#7925 review round 4: the holder's axes are INDEPENDENT, so one must not
    mask another.

    `_entity_name_holders` classified terminal → operator → unevaluable by
    EARLY RETURN, so a holder that is operator-shaped AND carries an array
    `status` reported only the status axis; the label gate reads the operator
    axis and the operator route does not read the status axis, so Phase 1
    returned [] and step 4 raised `Phase2Error` with the earlier Point
    committed (measured). Both axes are now filled in one pass.
    """
    sdk.create_object("Poisoned")
    sdk._get_proj().g.query(
        "MATCH (n:Object {name:'Poisoned'}) "
        "SET n.is_operator = true, n.status = ['x']")
    bundle = {
        "points": [{"kind": "statement", "content": "must-not-commit",
                    "ref": "p0"}],
        "entities": [{"ref": "h", "type": "object", "name": "Poisoned"}],
        # reify:True takes the operator route — the one that reads the operator
        # axis and never the status axis.
        "connections": [{"from": "p0", "to": "h", "operator": "IMPL",
                         "reify": True}],
    }
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("merge target is operator-shaped" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0


def test_double_nested_draft_stays_draft_under_gated(sdk):
    """#7925 review round 4 (fail-open guard): the Phase-2 draft default must be
    applied at the MINT depth.

    Reading a point item's status at the dedup depth for BOTH decisions made
    `props: {"props": {"status": "draft"}}` report a status, so Phase 2 skipped
    its explicit `status="draft"`, `create_point` (one fold) saw none, and the
    point was born **live** — under gated, from a bundle that declared DRAFT.
    Measured `'draft'` on `origin/main` and `'live'` on the pre-fix head.
    """
    bundle = {"points": [{"kind": "statement", "content": "born-draft",
                          "props": {"props": {"status": "draft"}}}]}
    assert sdk._validate_bundle(bundle, promotion_policy="gated") == []
    sdk.ingest(bundle, promotion_policy="gated")
    assert sdk._get_proj().g.query(
        "MATCH (n:Point {content:'born-draft'}) RETURN n.status"
    ).result_set == [["draft"]]


def test_non_string_entity_name_does_not_raise_out_of_validation(sdk):
    """#7925 review round 4: the mint memo must not hash an unhashable name.

    A `name` that is a list/dict is a shape problem for another check, but the
    bundle-wide array loop memoized `(_label, item["name"])` unguarded and
    `_validate_bundle` raised a raw `TypeError` instead of returning violations
    (a regression: `origin/main` returned `[]`).
    """
    for name in (["x"], {"a": 1}):
        assert sdk._validate_bundle(
            {"entities": [{"type": "object", "name": name}]}) == []


def test_subject_array_status_is_reported_once(sdk):
    """#7925 review round 4: one defect, one diagnosis.

    A `:Subject` array status is refused by the vocabulary loop because
    `create_subject` validates the value and raises before storing it — nothing
    is ever "stored verbatim". The array loop admitted `Subject` and added a
    second violation asserting a mechanism that cannot happen there.
    """
    msgs = [v["message"] for v in sdk._validate_bundle(
        {"entities": [{"type": "subject", "name": "ArrSub",
                       "status": ["retracted"]}]})]
    assert len(msgs) == 1, msgs
    assert "declares status" in msgs[0]


# ── The shared-name branch (#8864/#8902): the fold no longer keys on `name` ──
#
# `_upsert_subject`/`_upsert_object` key the MERGE on the record's own `id` once
# a name has 2+ carriers (`_name_holder_count > 1`, #8864/#8902), so a bundle
# item naming a shared name MINTS a node on its own id instead of merging onto a
# holder. Both halves of the bundle-local endpoint guard change with it, and
# each direction was measured against the merged head before these pins existed.

def test_shared_name_declared_terminal_status_is_refused_in_phase1(sdk):
    """A SHARED name's declared status APPLIES — it must not be suppressed.

    On the single-carrier branch the merge lands on the holder, which keeps its
    own status, so a declared status is dropped and rejecting it would
    over-reject. With 2+ carriers the item mints, so its declared status is
    written. Suppressing the declared leg on `exists_by_name` alone (the
    single-carrier model) failed OPEN on the merged head: Phase 1 returned `[]`
    and step 4 raised `Phase2Error: endpoint … is retracted` with the minted
    terminal node AND the earlier point item committed — the #2062
    partial-mutation class this gate exists to close.
    """
    for i in (1, 2):
        sdk._get_proj().g.query(
            f"CREATE (:Object {{id:'decoy-{i}', name:'Acme', status:'live'}})")
    bundle = {
        "points": [{"kind": "statement", "content": "must-not-commit"}],
        "entities": [
            {"ref": "a", "type": "object", "name": "Acme",
             "status": "retracted"},
            {"ref": "b", "type": "object", "name": "Beta"},
        ],
        "connections": [{"from": "a", "to": "b", "operator": "IMPL"}],
    }
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("terminal status" in m for m in msgs), msgs
    assert any("shared name" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0


def test_shared_name_terminal_holder_does_not_over_reject(sdk):
    """The other direction: a terminal HOLDER does not terminalize a shared name.

    With 2+ carriers the writer mints on the item's own id, so it does not land
    on the terminal holder at all. Reading `ANY terminal holder` (the
    single-carrier conservatism) refused a bundle the writer performs.
    """
    sdk._get_proj().g.query(
        "CREATE (:Object {id:'decoy-1', name:'Acme', status:'live'})")
    sdk._get_proj().g.query(
        "CREATE (:Object {id:'decoy-2', name:'Acme', status:'superseded'})")
    bundle = {
        "entities": [
            {"ref": "a", "type": "object", "name": "Acme"},
            {"ref": "b", "type": "object", "name": "Beta"},
        ],
        "connections": [{"from": "a", "to": "b", "operator": "IMPL"}],
    }
    assert sdk._validate_bundle(bundle) == []
    created = sdk.ingest(bundle)
    assert created["created"]["connections"] == 1
    # The endpoint is the freshly minted node, not the terminal holder.
    assert _count(sdk, "MATCH (o:Object {name:'Acme'}) RETURN count(o)") == 3
    assert _count(sdk, "MATCH (:Object {name:'Acme'})-[:IMPL]->(:Object "
                       "{name:'Beta'}) RETURN count(*)") == 1


def test_single_carrier_declared_terminal_status_is_still_dropped(sdk):
    """The single-carrier model still holds: ONE holder keeps its own status."""
    sdk._get_proj().g.query(
        "CREATE (:Object {id:'solo', name:'Solo', status:'live'})")
    bundle = {
        "entities": [
            {"ref": "a", "type": "object", "name": "Solo",
             "status": "retracted"},
            {"ref": "b", "type": "object", "name": "Beta"},
        ],
        "connections": [{"from": "a", "to": "b", "operator": "IMPL"}],
    }
    assert sdk._validate_bundle(bundle) == []
    assert sdk.ingest(bundle)["created"]["connections"] == 1
    assert sdk._get_proj().g.query(
        "MATCH (o:Object {name:'Solo'}) RETURN o.status").result_set == [["live"]]


# ── shared name whose CANONICAL carrier exists (#7925 review round 6) ────────
#
# The guard reads the axes of the node the WRITE lands on. Since #8864/#8902 the
# fold keys its merge on the record's own `id` once a name has 2+ carriers, and
# every ingest entity item's id IS `ids.entity_name_id(label, name)` — so with
# 2+ carriers the item still LANDS on a carrier holding that canonical id
# (ON MATCH); it mints only when no carrier holds it. Branching on the carrier
# count alone therefore failed OPEN here (Phase 1 `[]`, then Phase2Error with
# the earlier point committed) and OVER-REJECTED the declared-status direction.


def _canonical(label: str, name: str) -> str:
    from tortoise.ids import entity_name_id

    return entity_name_id(label, name)


def _two_carriers(sdk, canonical_status: str | None = "live", **extra) -> None:
    """2 carriers: one holding the canonical id, one decoy."""
    props = "" if canonical_status is None else f", status:'{canonical_status}'"
    for k, v in extra.items():
        props += f", {k}:{v}"
    sdk._get_proj().g.query(
        f"CREATE (:Object {{id:'{_canonical('Object', 'Acme')}', "
        f"name:'Acme'{props}}})")
    sdk._get_proj().g.query(
        "CREATE (:Object {id:'decoy-1', name:'Acme', status:'live'})")


def test_shared_name_canonical_carrier_terminal_status_is_refused(sdk):
    """A terminal CANONICAL carrier is the merge target — refuse it in Phase 1."""
    _two_carriers(sdk, "superseded")
    bundle = {
        "points": [{"kind": "statement", "content": "must-not-commit"}],
        "entities": [
            {"ref": "a", "type": "object", "name": "Acme"},
            {"ref": "b", "type": "object", "name": "Beta"},
        ],
        "connections": [{"from": "a", "to": "b", "operator": "IMPL"}],
    }
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("terminal Object" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0


def test_shared_name_canonical_carrier_operator_is_refused(sdk):
    """The operator axis is read from the merge target, not from any carrier."""
    _two_carriers(sdk)
    sdk._get_proj().g.query(
        "MATCH (o:Object {id:$id}) SET o.is_operator = true",
        params={"id": _canonical("Object", "Acme")},
    )
    bundle = {
        "points": [{"kind": "statement", "content": "must-not-commit"}],
        "entities": [
            {"ref": "a", "type": "object", "name": "Acme"},
            {"ref": "b", "type": "object", "name": "Beta"},
        ],
        "connections": [{"from": "a", "to": "b", "operator": "IMPL"}],
    }
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("whose merge target is operator-shaped" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0


def test_shared_name_canonical_carrier_array_status_is_refused(sdk):
    """An array status on the merge target is the TypeError the guard predicts."""
    _two_carriers(sdk)
    sdk._get_proj().g.query(
        "MATCH (o:Object {id:$id}) SET o.status = ['retracted']",
        params={"id": _canonical("Object", "Acme")},
    )
    bundle = {
        "points": [{"kind": "statement", "content": "must-not-commit"}],
        "entities": [
            {"ref": "a", "type": "object", "name": "Acme"},
            {"ref": "b", "type": "object", "name": "Beta"},
        ],
        "connections": [{"from": "a", "to": "b", "operator": "IMPL"}],
    }
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("cannot be evaluated" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0


def test_shared_name_canonical_carrier_declared_terminal_is_dropped(sdk):
    """The other direction: a declaration that MERGES is dropped, not refused.

    With the canonical carrier present and live, the item lands on it and
    `status` is written ON CREATE only, so the declaration never reaches the
    node — `create_direct_edge` writes the edge. Refusing this bundle rejected a
    write the primitives perform.
    """
    _two_carriers(sdk, "live")
    bundle = {
        "entities": [
            {"ref": "a", "type": "object", "name": "Acme",
             "status": "retracted"},
            {"ref": "b", "type": "object", "name": "Beta"},
        ],
        "connections": [{"from": "a", "to": "b", "operator": "IMPL"}],
    }
    assert sdk._validate_bundle(bundle) == []
    assert sdk.ingest(bundle)["created"]["connections"] == 1
    assert sdk._get_proj().g.query(
        "MATCH (o:Object {id:$id}) RETURN o.status",
        params={"id": _canonical("Object", "Acme")},
    ).result_set == [["live"]]


def test_shared_name_distinct_nodes_are_not_a_self_edge(sdk):
    """Two carrier IDS sharing a name are two nodes, so the edge is legal."""
    sdk._get_proj().g.query(
        "CREATE (:Subject {id:'carrier-1', name:'Gamma', status:'live'})")
    sdk._get_proj().g.query(
        "CREATE (:Subject {id:'carrier-2', name:'Gamma', status:'live'})")
    bundle = {
        "connections": [
            {"from": "carrier-2", "to": "carrier-1", "operator": "IMPL"},
        ],
    }
    assert sdk._validate_bundle(bundle) == []
    assert sdk.ingest(bundle)["created"]["connections"] == 1


def test_first_same_name_item_declaring_array_is_refused(sdk):
    """The FIRST item naming a name CREATES the node, so its array is stored."""
    for i in (1, 2):
        sdk._get_proj().g.query(
            f"CREATE (:Object {{id:'decoy-{i}', name:'Acme', status:'live'}})")
    bundle = {
        "points": [{"kind": "statement", "content": "must-not-commit"}],
        "entities": [
            {"ref": "a", "type": "object", "name": "Acme",
             "status": ["retracted"]},
            {"ref": "b", "type": "object", "name": "Acme"},
        ],
        "connections": [{"from": "a", "to": "b", "operator": "IMPL"}],
    }
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("an array is stored verbatim" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0


def test_second_same_name_item_declaring_array_is_dropped(sdk):
    """A LATER same-name item does not create a node, so its status is dropped.

    Every ingest entity item's id is `entity_name_id(label, name)`, so the second
    item lands on the node the first one created and `status` is written
    `ON CREATE` only. Refusing the declaration rejected a bundle the writer
    performs (measured at the writer: one node, status `live`, array not stored).
    """
    for i in (1, 2):
        sdk._get_proj().g.query(
            f"CREATE (:Object {{id:'decoy-{i}', name:'Acme', status:'live'}})")
    bundle = {
        "entities": [
            {"ref": "a", "type": "object", "name": "Acme"},
            {"ref": "b", "type": "object", "name": "Acme",
             "status": ["retracted"]},
            {"ref": "c", "type": "object", "name": "Other"},
        ],
        "connections": [{"from": "a", "to": "c", "operator": "IMPL"}],
    }
    assert sdk._validate_bundle(bundle) == []
    assert sdk.ingest(bundle)["created"]["connections"] == 1
    assert _count(sdk, "MATCH (o:Object {name:'Acme'}) RETURN count(o)") == 3
    assert sdk._get_proj().g.query(
        "MATCH (o:Object {id:$id}) RETURN o.status",
        params={"id": _canonical("Object", "Acme")},
    ).result_set == [["live"]]


def test_two_bundle_refs_to_one_shared_name_are_a_self_edge(sdk):
    """Under a shared name two bundle refs are STILL one node — a self-edge.

    Every ingest entity item writes `entity_name_id(label, name)`, so the first
    item creates that node (even when 2+ carriers hold the name, because the fold
    then keys on the id) and the second lands on it. Comparing names says nothing
    here; comparing LANDING IDs does. Skipping the check on the carrier count
    failed open: Phase 1 returned `[]` and step 4 raised
    `source_id and target_id must differ` with the earlier point committed.
    """
    for i in (1, 2):
        sdk._get_proj().g.query(
            f"CREATE (:Object {{id:'decoy-{i}', name:'Acme', status:'live'}})")
    bundle = {
        "points": [{"kind": "statement", "content": "must-not-commit"}],
        "entities": [
            {"ref": "a", "type": "object", "name": "Acme"},
            {"ref": "b", "type": "object", "name": "Acme"},
        ],
        "connections": [{"from": "a", "to": "b", "operator": "IMPL"}],
    }
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("address the same endpoint" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0
    assert _count(sdk, "MATCH (o:Object {id:$id}) RETURN count(o)",
                  {"id": _canonical("Object", "Acme")}) == 0


def test_two_carrier_ids_are_not_a_self_edge(sdk):
    """Two carriers sharing a NAME are two nodes: the edge between them is legal."""
    sdk._get_proj().g.query(
        "CREATE (:Subject {id:'carrier-1', name:'Gamma', status:'live'})")
    sdk._get_proj().g.query(
        "CREATE (:Subject {id:'carrier-2', name:'Gamma', status:'live'})")
    bundle = {
        "connections": [
            {"from": "carrier-2", "to": "carrier-1", "operator": "IMPL"},
        ],
    }
    assert sdk._validate_bundle(bundle) == []
    assert sdk.ingest(bundle)["created"]["connections"] == 1


def test_phase1_and_the_primitive_agree_on_terminal_endpoints(sdk):
    """`_endpoint_is_terminal` PREDICTS `create_direct_edge`'s own test.

    If either side moves, Phase 1 either predicts a raise that never happens or
    accepts an endpoint the primitive raises on mid-write. This drives both
    surfaces over one fixture so the pairing is a detector, not a comment.
    """
    term = sdk.create_subject("Term", status="superseded")["id"]
    live = sdk.create_object("Live")["id"]
    # Phase 1 predicts the refusal of the direct route …
    msgs = [v["message"] for v in sdk._validate_bundle({
        "connections": [{"from": term, "to": live, "operator": "IMPL"}],
    })]
    assert any("terminal" in m and "superseded" in m for m in msgs), msgs
    # … and the primitive is what refuses it.
    with pytest.raises(ValueError, match="terminal"):
        sdk.create_direct_edge("IMPL", term, live)
    # A reified pair takes `create_operator`, which tolerates the terminal node —
    # so Phase 1 must not refuse it, and the primitive must actually write it.
    assert sdk._validate_bundle({
        "connections": [{"from": live, "to": term, "operator": "IMPL",
                         "reify": True}],
    }) == []
    assert sdk.create_operator("IMPL", live, [term]) is not None


def test_non_canonical_single_carrier_self_edge_is_refused(sdk):
    """A single carrier with a LEGACY id is still the node the bundle ref lands on.

    With ONE carrier the fold MERGEs by NAME onto it and its `ON MATCH` clause
    rewrites the stored id to `entity_name_id(label, name)` — so an external ref to
    the carrier's PRE-write id names the same node, and step 4 then finds no such
    endpoint. Comparing the minted id against the carrier's id missed it: Phase 1
    returned `[]` and ingest raised with the earlier point committed.
    """
    sdk._get_proj().g.query(
        "CREATE (:Object {id:'legacy-ulid-1', name:'Acme', status:'live'})")
    bundle = {
        "points": [{"kind": "statement", "content": "must-not-commit"}],
        "entities": [{"ref": "a", "type": "object", "name": "Acme"}],
        "connections": [{"from": "a", "to": "legacy-ulid-1", "operator": "IMPL"}],
    }
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("address the same endpoint" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0


def test_bare_name_subject_stub_is_the_same_endpoint_as_the_bundle_ref(sdk):
    """`aboutSubject="Gamma"` mints a `:Subject {id:'Gamma'}` stub — a third id
    spelling. The bundle item adopts it (name merge) and re-registers it under the
    canonical id, so an endpoint naming the stub's id is the same node."""
    sdk.create_event("meeting", "other", aboutSubject="Gamma")
    bundle = {
        "points": [{"kind": "statement", "content": "must-not-commit"}],
        "entities": [{"ref": "g", "type": "subject", "name": "Gamma"}],
        "connections": [{"from": "g", "to": "Gamma", "operator": "IMPL"}],
    }
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert any("address the same endpoint" in m for m in msgs), msgs
    with pytest.raises(BundleValidationError):
        sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0


def test_endpoint_naming_a_renamed_carrier_is_refused(sdk):
    """An endpoint that names the PRE-write id of an adopted carrier is dead by step 4.

    The bundle item naming the holder merges onto it and the fold rewrites its id to
    `entity_name_id(label, name)`, so a connection endpoint still carrying the old id
    raises `Phase2Error … does not exist` after the earlier items committed. One
    connection is enough — no self-edge pair is required.
    """
    sdk._get_proj().g.query(
        "CREATE (:Object {id:'legacy-ulid-1', name:'Acme', status:'live'})")
    for route in ({"operator": "IMPL"}, {"operator": "IMPL", "reify": True}):
        for frm, to in (("legacy-ulid-1", "p2"), ("p2", "legacy-ulid-1")):
            bundle = {
                "points": [{"ref": "p1", "kind": "statement",
                            "content": "must-not-commit"},
                           {"ref": "p2", "kind": "statement",
                            "content": "anchor"}],
                "entities": [{"ref": "a", "type": "object", "name": "Acme"}],
                "connections": [{"from": frm, "to": to, **route}],
            }
            msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
            assert any("re-addresses to" in m for m in msgs), (route, frm, msgs)
            with pytest.raises(BundleValidationError):
                sdk.ingest(bundle)
    assert _count(sdk, "MATCH (n:Point {content:'must-not-commit'}) "
                       "RETURN count(n)") == 0
    # the carrier was never renamed and no edge was written
    assert _count(sdk, "MATCH (o:Object {id:'legacy-ulid-1'}) "
                       "RETURN count(o)") == 1


def test_same_id_string_on_two_labels_is_not_a_self_edge(sdk):
    """A `:Subject` stub (`id=name`) and a legacy `:Object` can share an id STRING.

    They are two nodes, so the edge between them is legal — the collision map has to
    carry the label as well as the landing id.
    """
    sdk._get_proj().g.query(
        "CREATE (:Subject {id:'Gamma', name:'Gamma', status:'live'})")
    sdk._get_proj().g.query(
        "CREATE (:Object {id:'Gamma', name:'Gamma', status:'live'})")
    bundle = {
        "entities": [{"ref": "s", "type": "subject", "name": "Gamma"},
                     {"ref": "o", "type": "object", "name": "Gamma"}],
        "connections": [{"from": "s", "to": "o", "operator": "IMPL"}],
    }
    msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
    assert not any("address the same endpoint" in m for m in msgs), msgs
