"""#9750 — a repeated target in a multi-item ``to`` writes ONE edge per DISTINCT
target (the operator's input set is a SET).

An operator's typed edges and their ``INPUT`` mirrors are written BY LIST
POSITION: ``idx`` 0 is the source slot (the position ``ep.py`` reads the forward
message's belief from) and ``idx >= 1`` are the targets. Nothing on that path was
a set operation, so a repeated list ENTRY became a repeated EDGE — and ``ep.py``
builds the factor from the EDGE list, so a one-target operator acquired a 3-input
factor over ``[src, p2, p2]``: it double-counted that claim's EP contribution and
defeated the "<2 inputs" unary path, with no diagnostic.

MEASURED before the fix (this tree, embedded SDK, ``origin/main`` @ 7ff42c0d5)::

    {"to": ["p2", "p2"], "operator": "IMPL"}   -> count(r:IMPL) 3 · count(r:INPUT) 3
    {"to": ["p2"],       "operator": "IMPL"}   -> count(r:IMPL) 2 · count(r:INPUT) 2
    {"to": ["p2", "p3"]} where p2/p3 are same-content items that fold onto ONE
      node (create_point's content dedup)               -> 3 · 3
    direct route, Object↔Object {"to": ["o2", "o2"]}    -> REFUSED as multi-item
      while {"to": "o2"} is accepted

The fix is at the seam that OWNS endpoint identity, never in Cypher and never in
``ep.py`` (the graph must stop being corrupt, not learn to read corruption):

* ``create_operator`` collapses ``target_ids`` (order-preserving, first
  occurrence wins) — the PRIMITIVE, so every door is fixed at once and the
  ``OperatorAdded`` payload carries the same list (replay parity).
* ``ingest`` collapses the RESOLVED ``dsts`` before ``_find_operator``, because
  that query's dedup key is ``size(targets) = size($inputs)``; feeding it the
  caller's multiplicity computes the key over a list the graph no longer holds.
* ``_find_operator`` keys on the endpoint SET **plus the request's ARITY**
  (#10305): the exact arm requires the operator's typed-slot count to equal the
  RAW request arity, because multiplicity IS the EP factor arity — a 2-slot
  request must not be answered by a 3-slot factor. The one carve-out is the
  pre-#9750 duplicate-TARGET operator, which no request can spell.
* ``_find_operator`` collects ``DISTINCT t.id``, so a graph written BEFORE this
  fix (already carrying duplicate edges) still exact-matches instead of minting a
  duplicate operator.
* ``_check_connection``'s multi-item arm judges the DISTINCT ref list, so
  ``["o2", "o2"]`` is not refused where the singular spelling is accepted.

⚠️ The dedupe is among the TARGETS only. ``create_operator(op, a, [a])`` (the
self-support operator, ``idx`` 0 and 1 over one id) is a deliberate shape and is
pinned below so a future widening of the dedupe cannot swallow it silently.

Run (docker lane):
  TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
    python -m pytest tests/test_ingest_duplicate_target.py -q -p no:cacheprovider
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture()
def make_sdk(tmp_path):
    """Factory of ISOLATED graphs — one DB path (one server graph under a
    ``TORTOISE_DB_URI`` lane) per call, so two spellings can be compared
    edge-for-edge without sharing state."""
    opened: list[TortoiseSDK] = []

    def factory(name: str, **kwargs) -> TortoiseSDK:
        sdk = TortoiseSDK(os.path.join(str(tmp_path), f"{name}.db"), **kwargs)
        opened.append(sdk)
        return sdk

    yield factory
    for sdk in opened:
        try:  # noqa: SIM105
            sdk.close()
        except Exception:  # noqa: BLE001, RUF100
            pass


def _count(sdk, cypher: str, params: dict | None = None) -> int:
    rows = sdk._get_proj().g.query(cypher, params=params or {}).result_set
    return int(rows[0][0]) if rows else 0


def _edge_counts(sdk, op_type: str = "IMPL") -> tuple[int, int]:
    """``(typed edges, INPUT edges)`` for the whole graph."""
    return (
        _count(sdk, f"MATCH ()-[r:{op_type}]->() RETURN count(r)"),
        _count(sdk, "MATCH ()-[r:INPUT]->() RETURN count(r)"),
    )


def _operator_input_slots(sdk, op_id: str, op_type: str = "IMPL") -> list[tuple]:
    """The operator's FACTOR INPUT LIST as ``ep.py`` reads it: its typed
    out-edges ordered by ``idx`` (``idx`` 0 = the source slot, ``>= 1`` = the
    targets — the same derivation ``tests/test_6871_operator_input_set.py``
    pins). A duplicated target shows up here as a repeated ``(idx, t.id)`` pair
    with the same id."""
    return [tuple(r) for r in sdk._get_proj().g.query(
        f"MATCH (o:Point {{id:$id}})-[r:{op_type}]->(t) "
        "RETURN r.idx, t.id ORDER BY r.idx",
        params={"id": op_id},
    ).result_set]


def _bundle(to_list, *, p2_content: str = "two", extra_points=()) -> dict:
    points = [
        {"ref": "p1", "kind": "statement", "content": "one"},
        {"ref": "p2", "kind": "statement", "content": p2_content},
    ]
    points.extend(extra_points)
    return {"points": points,
            "connections": [{"from": "p1", "to": to_list, "operator": "IMPL"}]}


# ═══════════════════════════════════════════════════════════════════════
# Indicator 1 — ["p2", "p2"] and ["p2"] produce IDENTICAL graphs
# ═══════════════════════════════════════════════════════════════════════

def test_ingest_dedups_repeated_ref(make_sdk):
    """The issue's measured case: one connection, one target spelled twice,
    must write the SAME graph as the singular spelling — 2 typed + 2 INPUT
    edges (idx 0 = source, idx 1 = the one target), one connection created."""
    repeated = make_sdk("repeated")
    singular = make_sdk("singular")

    res_rep = repeated.ingest(_bundle(["p2", "p2"]))
    res_sing = singular.ingest(_bundle(["p2"]))

    assert res_rep["created"]["connections"] == 1
    assert res_rep["deduped"]["connections"] == 0
    assert tuple(res_rep["created"][k] for k in ("points", "connections")) == \
        tuple(res_sing["created"][k] for k in ("points", "connections"))

    assert _edge_counts(repeated) == (2, 2), _edge_counts(repeated)
    assert _edge_counts(singular) == (2, 2), _edge_counts(singular)
    # the factor's input list is identical IN SHAPE: two slots (source, the one
    # target), each landing on its own graph's resolved id for the same ref
    slots_rep = _operator_input_slots(repeated, res_rep["ids"]["connections"][0])
    slots_sing = _operator_input_slots(singular, res_sing["ids"]["connections"][0])
    assert [i for i, _ in slots_rep] == [0, 1], slots_rep
    assert len({t for _, t in slots_rep}) == 2, slots_rep
    assert slots_rep == [(0, res_rep["ids"]["refs"]["p1"]),
                         (1, res_rep["ids"]["refs"]["p2"])], slots_rep
    assert slots_sing == [(0, res_sing["ids"]["refs"]["p1"]),
                          (1, res_sing["ids"]["refs"]["p2"])], slots_sing

    # …and the caller is TOLD the spelling collapsed (§3.2 key 13)
    keys = [w.split(":", 1)[0] for w in res_rep["warnings"]]
    assert "duplicate_target_deduped" in keys, res_rep["warnings"]
    assert "duplicate_target_deduped" not in \
        [w.split(":", 1)[0] for w in res_sing["warnings"]], res_sing["warnings"]


def test_reingest_repeated_ref_is_an_exact_dedup(make_sdk):
    """The instance the ingest-side dedupe exists for: the second submission
    of the SAME repeated spelling must EXACT-hit the operator run 1 wrote.
    ``_find_operator``'s set key is ``size(targets) = size($inputs)``, so a
    request still carrying the caller's multiplicity (``[src, p2, p2]``) misses
    the 2-target operator it just wrote, falls into the PARTIAL-ABSORB arm and
    reports a ``operator_absorb_completed`` for an operator that was already
    complete. (#10305 added the ARITY leg to that key — the collapsed request's
    slot count still equals the operator's, which is why this case keeps
    deduping.)"""
    sdk = make_sdk("reingest")
    bundle = _bundle(["p2", "p2"])

    first = sdk.ingest(bundle)
    oid = first["ids"]["connections"][0]
    second = sdk.ingest(bundle)

    assert second["deduped"]["connections"] == 1, second
    assert second["created"]["connections"] == 0, second["created"]
    assert second["ids"]["connections"] == [oid], second["ids"]
    assert second["deduped"]["points"] == 2, second["deduped"]
    keys = [w.split(":", 1)[0] for w in second["warnings"]]
    assert "operator_absorb_completed" not in keys, second["warnings"]
    assert _count(sdk, "MATCH (o:Point {is_operator:true}) RETURN count(o)") == 1
    assert _edge_counts(sdk) == (2, 2), _edge_counts(sdk)


def test_ingest_dedups_same_node_pair(make_sdk):
    """Indicator 2 — TWO different refs that resolve to ONE node (here: two
    items the point writer's content dedup folds onto one id) collapse too.
    This is unanswerable in Phase 1 (refs are unresolved there), which is why
    the dedup lives at the resolved-id seam."""
    sdk = make_sdk("same_node")
    bundle = _bundle(["p2", "p3"],
                     extra_points=[{"ref": "p3", "kind": "statement",
                                    "content": "two"}])  # same content as p2
    res = sdk.ingest(bundle)

    # the fixture is load-bearing: both items really did fold onto one id
    assert res["ids"]["refs"]["p2"] == res["ids"]["refs"]["p3"], res["ids"]["refs"]
    assert res["deduped"]["points"] == 1, res["deduped"]
    assert _edge_counts(sdk) == (2, 2), _edge_counts(sdk)
    assert "duplicate_target_deduped" in \
        [w.split(":", 1)[0] for w in res["warnings"]], res["warnings"]


def test_direct_route_repeated_ref_not_multi_item(make_sdk):
    """Indicator 1 on the DIRECT route — the guard is "``create_direct_edge``
    takes ONE target", so two spellings of one target are ONE target:
    ``["o2", "o2"]`` must be accepted and write exactly what ``"o2"`` writes,
    not be refused while the singular spelling is accepted."""
    repeated = make_sdk("direct_repeated")
    singular = make_sdk("direct_singular")

    def _objects():
        return {"entities": [{"ref": "o1", "type": "object", "name": "Obj One"},
                             {"ref": "o2", "type": "object", "name": "Obj Two"}]}

    rep = repeated.ingest({**_objects(), "connections": [
        {"from": "o1", "to": ["o2", "o2"], "operator": "IMPL"}]})
    sing = singular.ingest({**_objects(), "connections": [
        {"from": "o1", "to": "o2", "operator": "IMPL"}]})

    assert _edge_counts(repeated) == (1, 0), _edge_counts(repeated)
    assert _edge_counts(singular) == (1, 0), _edge_counts(singular)
    assert rep["ids"]["connections"] == sing["ids"]["connections"]
    assert "duplicate_target_deduped" in \
        [w.split(":", 1)[0] for w in rep["warnings"]], rep["warnings"]
    # …and the text names the route it ACTUALLY took (review round 1, P3): the
    # direct writer reads `dsts[0]` and writes no operator and no `INPUT` edge,
    # so the operator wording would describe a graph this route never produces.
    _dup = next(w for w in rep["warnings"]
                if w.startswith("duplicate_target_deduped"))
    assert "takes ONE target" in _dup, _dup
    assert "INPUT" not in _dup, _dup
    # (review round 2, P3) the operator route's tail must NOT survive here: the
    # direct writer writes no positional `idx`, so "the positional idx is
    # unchanged" would contradict the sentence before it.
    assert "positional idx" not in _dup, _dup
    assert "collapsed" not in _dup, _dup


def test_duplicate_edge_operator_is_never_completed_by_position(make_sdk):
    """Review round 1, P2 — the shape the dedupe must NOT collapse, and the
    partial-absorb arm must not "complete" (it is the arm's OWN positionality
    that is unsafe — see #10307 for the duplicate-FREE, out-of-order residue,
    which this guard deliberately does not cover).

    ``create_operator`` deliberately keeps a target that repeats the SOURCE
    (``idx`` 0 and 1 over one id), so an operator can hold FEWER distinct
    endpoints than edges. The completion loop writes each missing input at ITS
    REQUEST POSITION, so "completing" such an operator collides with a slot that
    already exists: measured before this arm required ``n_edges = size(targets)``
    — a ``composedOf`` self-support operator plus ``to: [p2]`` left TWO typed
    edges at ``idx 1``, and ``ep.py`` reads the factor positionally, so the
    operator became malformed and was then permanently adopted by a
    resubmission."""
    sdk = make_sdk("self_support_absorb")
    p1 = sdk.create_point(kind="statement", content="one")["id"]
    p2 = sdk.create_point(kind="statement", content="two")["id"]
    op = sdk.create_operator("composedOf", p1, [p1])

    sdk.ingest({"connections": [
        {"from": p1, "to": [p2], "operator": "composedOf"}]})

    slots = _operator_input_slots(sdk, op["id"], op_type="hasPart")
    assert [i for i, _ in slots] == [0, 1], slots
    assert {t for _, t in slots} == {p1}, slots


def test_find_operator_key_is_a_set_for_the_repeated_source_slot(make_sdk):
    """Review round 1, P2 — the READ side must agree with the WRITE side.

    A target can resolve onto the SOURCE's own node (two same-content items
    fold; Phase 1's self-edge arm compares REFS, not resolved ids), so the
    requested input list legitimately carries the source twice. Counting
    ``DISTINCT`` endpoints on the graph side while handing the query the SLOT
    list made the key unsatisfiable for that shape: the resubmission fell into
    PARTIAL-ABSORB, wrote no edge, and reported ``operator_absorb_completed``
    (a §3.2 key-8 payload asserting a completion that never happened) — the
    same mis-report ``test_reingest_repeated_ref_is_an_exact_dedup`` prevents
    for the duplicate-target case."""
    sdk = make_sdk("repeated_source_slot")
    a = sdk.create_point(kind="statement", content="one")["id"]
    b = sdk.create_point(kind="statement", content="two")["id"]
    sdk.create_operator("IMPL", a, [a])  # the self-support shape

    assert sdk._find_operator("IMPL", [a, a])["kind"] == "exact", (
        "the dedup KEY must be the DISTINCT input list — a slot list makes the "
        "key unsatisfiable for every self-support operator")
    assert (sdk._find_operator("IMPL", [a, b]) or {}).get("kind") != "exact"

    # the ingest spelling of the same shape: `to` folds onto `from`'s node
    bundle = {"points": [{"ref": "q1", "kind": "statement", "content": "same"},
                         {"ref": "q2", "kind": "statement", "content": "same"}],
              "connections": [{"from": "q1", "to": ["q2"], "operator": "IMPL"}]}
    first = sdk.ingest(bundle)
    oid = first["ids"]["connections"][0]
    second = sdk.ingest(bundle)

    assert second["deduped"]["connections"] == 1, second
    assert second["created"]["connections"] == 0, second["created"]
    assert second["ids"]["connections"] == [oid], second["ids"]
    assert "operator_absorb_completed" not in \
        [w.split(":", 1)[0] for w in second["warnings"]], second["warnings"]
    slots = _operator_input_slots(sdk, oid)
    assert [i for i, _ in slots] == [0, 1], slots


def test_self_support_operator_with_extra_target_mints_its_own_arity(make_sdk):
    """#10305 (red-first: this test previously asserted the SILENCE) — a
    same-SET request of a DIFFERENT ARITY must NOT adopt the wider operator.

    ``create_operator("IMPL", a, [a, b])`` keeps the self-support slot AND the
    other target — 3 slots over 2 distinct endpoints. ``ep.py`` builds the
    factor from the EDGE list, so that operator IS a 3-input factor. A request
    asserting 2 inputs (``{"from": a, "to": [b]}``) resolves to the SAME
    distinct endpoint set, and a set-only dedup key therefore EXACT-matched it:
    the caller asserted a 2-input ``IMPL`` and was handed a 3-input factor,
    with no edge written and no warning (measured at 8ac927908; on pre-#9750
    ``7ff42c0d5`` the same sequence minted the 2-slot operator).

    The exact arm now also requires the operator's slot count to equal the RAW
    request arity (#10305), so this request MISSES (the sets are equal in size,
    so the partial arm cannot take it either) and mints its OWN 2-slot operator:
    the graph ends with two operators over one endpoint set, which is the
    honest outcome — one factor per asserted arity.

    ⛔ The arity leg does NOT disturb what #9750 fixed: a request whose slots
    repeat ONE endpoint set still carries the stored arity (`to: [b, b]` → 2,
    a target folding onto the source → `[a, a]` → 2 against the 2-slot
    self-support operator, both still exact-dedups — see
    ``test_reingest_repeated_ref_is_an_exact_dedup`` and
    ``test_find_operator_key_is_a_set_for_the_repeated_source_slot``), and a
    pre-#9750 duplicate-TARGET operator is still adopted
    (``test_legacy_duplicate_graph_still_dedups``).
    """
    sdk = make_sdk("self_support_extra_target")
    a = sdk.create_point(kind="statement", content="one")["id"]
    b = sdk.create_point(kind="statement", content="two")["id"]
    op = sdk.create_operator("IMPL", a, [a, b])

    slots = _operator_input_slots(sdk, op["id"])
    assert [(i, t) for i, t in slots] == [(0, a), (1, a), (2, b)], slots
    assert _edge_counts(sdk) == (3, 3), _edge_counts(sdk)

    # the 3-slot operator is not what a 2-slot request asked for
    assert sdk._find_operator("IMPL", [a, b]) is None, (
        "a 2-slot request must not exact-hit the 3-slot operator")
    # …but the 3-slot request that CAN be spelled still finds it
    assert sdk._find_operator("IMPL", [a, a, b])["kind"] == "exact"

    res = sdk.ingest({"connections": [
        {"from": a, "to": [b], "operator": "IMPL"}]})

    assert res["created"]["connections"] == 1, res
    assert res["deduped"]["connections"] == 0, res
    assert res["ids"]["connections"] != [op["id"]], res["ids"]
    assert _count(sdk, "MATCH (o:Point {is_operator:true}) RETURN count(o)") == 2
    # the new operator is the arity the caller ASSERTED
    new_op = res["ids"]["connections"][0]
    assert [(i, t) for i, t in _operator_input_slots(sdk, new_op)] == \
        [(0, a), (1, b)], _operator_input_slots(sdk, new_op)
    # the wider operator is untouched — not repaired, not superseded
    assert [(i, t) for i, t in _operator_input_slots(sdk, op["id"])] == \
        [(0, a), (1, a), (2, b)]


def test_exact_arm_prefers_the_SHAPE_the_request_spelled(make_sdk):
    """#10305 (round 6, red-first) — the arity leg cannot tell two SAME-ARITY
    shapes apart, so the OLDER operator won on `o.id`.

    Two operators over ONE endpoint set ``{q1, q3}``, each with THREE typed
    slots:

    * a pre-#9750 duplicate-TARGET operator — ``[q1, q3, q3]`` (``idx`` 1 and 2
      over one id), the corrupt shape the exact arm's carve-out deliberately
      still adopts; created FIRST, so it holds the lower id;
    * the self-support shape ``[q1, q1, q3]`` — the shape a caller SPELLS (two
      same-content items fold, so ``to: ["q2", "q3"]`` resolves onto the
      source) and ``create_operator`` writes deliberately.

    ``n_edges = size($slots)`` is satisfied by BOTH (3 = 3), so the arity leg
    separates neither, and ``ORDER BY n_edges ASC, o.id`` bound the request to
    the LEGACY operator (measured at `86c4c6558` before the tie-break): the
    caller asserted the self-support slot and was handed a duplicate-target
    factor — the same "EP propagated a model the caller never asserted" class
    this PR exists to close, one shape over.

    The tie-break now prefers the operator whose SOURCE-slot multiplicity (how
    many ``idx`` slots carry the ``idx`` 0 endpoint) equals the request's, with
    ``o.id`` still breaking a remaining tie. It is an ORDER preference, never a
    condition: nothing that matches today stops matching, so it cannot re-mint
    a duplicate operator (#9750's hazard).
    """
    sdk = make_sdk("shape_tiebreak")
    bundle = {"points": [{"ref": "q1", "kind": "statement", "content": "same"},
                          {"ref": "q2", "kind": "statement", "content": "same"},
                          {"ref": "q3", "kind": "statement", "content": "other"}],
               "connections": [{"from": "q1", "to": ["q3"],
                                "operator": "IMPL"}]}
    first = sdk.ingest(bundle)
    q1 = first["ids"]["refs"]["q1"]
    q3 = first["ids"]["refs"]["q3"]
    legacy = first["ids"]["connections"][0]
    assert _operator_input_slots(sdk, legacy) == [(0, q1), (1, q3)]
    # the pre-#9750 writer's second typed + INPUT edge over the same target
    sdk._get_proj().g.query(
        "MATCH (o:Point {id:$o}), (t:Point {id:$t}) "
        "CREATE (o)-[:IMPL {idx:2}]->(t) CREATE (t)-[:INPUT {idx:2}]->(o)",
        params={"o": legacy, "t": q3})
    assert _operator_input_slots(sdk, legacy) == [(0, q1), (1, q3), (2, q3)]

    # the shape the request can spell, minted AFTER the legacy one
    self_support = sdk.create_operator("IMPL", q1, [q1, q3])["id"]
    assert _operator_input_slots(sdk, self_support) == \
        [(0, q1), (1, q1), (2, q3)]
    assert legacy != self_support

    hit = sdk._find_operator("IMPL", [q1, q1, q3])
    assert hit is not None and hit["kind"] == "exact", hit
    assert hit["id"] == self_support, (
        "the exact arm must answer with the SHAPE the request spelled "
        f"(self-support {self_support}), not the older legacy duplicate-target "
        f"operator {legacy}")

    # …and the user-visible path: the resubmission dedups onto that operator,
    # writing nothing at all.
    res = sdk.ingest(bundle)
    assert res["deduped"]["connections"] == 1, res
    assert res["created"]["connections"] == 0, res["created"]
    assert res["ids"]["connections"] == [self_support], res["ids"]
    assert _edge_counts(sdk) == (6, 6), _edge_counts(sdk)
    assert _count(sdk, "MATCH (o:Point {is_operator:true}) RETURN count(o)") == 2


def test_absorb_never_completes_an_inexpressible_request(make_sdk):
    """Review round 2, P3 — the crash window the `n_edges = size(targets)` guard
    does NOT cover, and the `completed` count that over-reported it.

    ``_find_operator``'s ``written`` is a set of IDS, so the completion loop
    cannot reproduce a request whose OWN slot list repeats an id — reachable when
    a target resolves onto the SOURCE's node (two same-content items fold) while
    the caller spelled two different refs, so Phase 1's ref-level self-edge guard
    does not fire. Measured before this arm declined such a request: a crash
    remnant holding only ``idx 0`` was "completed" to ``[(0,src),(2,q3)]`` — an
    `idx` GAP no writer produces, which every later resubmission then
    exact-matches — while the §3.2 key-8 payload claimed TWO completions for ONE
    edge written.

    The invariant pinned here is shape-level, so it survives any future rework of
    the arm: after the retry, EVERY operator's typed `idx` list is contiguous
    from 0.
    """
    sdk = make_sdk("inexpressible")
    bundle = {"points": [{"ref": "q1", "kind": "statement", "content": "same"},
                         {"ref": "q2", "kind": "statement", "content": "same"},
                         {"ref": "q3", "kind": "statement", "content": "other"}],
              "connections": [{"from": "q1", "to": ["q2", "q3"],
                               "operator": "IMPL"}]}
    first = sdk.ingest(bundle)
    oid = first["ids"]["connections"][0]
    assert first["ids"]["refs"]["q1"] == first["ids"]["refs"]["q2"], first["ids"]
    g = sdk._get_proj().g
    # simulate the crash: only the source slot landed
    g.query("MATCH (o:Point {id:$o})-[r:IMPL]->(t) WHERE r.idx > 0 DELETE r",
            params={"o": oid})

    res = sdk.ingest(bundle)

    assert "operator_absorb_completed" not in \
        [w.split(":", 1)[0] for w in res["warnings"]], res["warnings"]
    idx_by_op: dict[str, list[int]] = {}
    for node_id, idx in g.query(
            "MATCH (o:Point {is_operator:true})-[r:IMPL]->(t) "
            "RETURN o.id, r.idx ORDER BY o.id, r.idx").result_set:
        idx_by_op.setdefault(node_id, []).append(idx)
    for node_id, idxs in idx_by_op.items():
        assert idxs == list(range(len(idxs))), \
            (node_id, idxs, "idx GAP in the factor's positional input list")


def test_clean_operator_is_not_adopted_by_a_wider_request(make_sdk):
    """#10305, the WIDENING direction — found by review, and the reason the
    legacy carve-out is bounded by ``n_edges > size(targets)``.

    Without that bound the carve-out ("this operator carries no duplicate of its
    own source slot") is true for every CLEAN operator, so an arity mismatch was
    forgiven in this direction and the defect reappeared mirrored: a bundle that
    asserts THREE inputs — ``{from: q1, to: [q2, q3]}`` where ``q2`` folds onto
    ``q1``, i.e. the spellable self-support shape ``[q1, q1, q3]`` — resolved to
    the endpoint set ``{q1, q3}`` and was answered ``deduped: true`` by an
    existing 2-slot operator, so the caller asserted a 3-input ``IMPL`` and got
    a 2-input factor. It must mint its own 3-slot operator instead.
    """
    sdk = make_sdk("wider_request")
    q1 = sdk.create_point(kind="statement", content="same")["id"]
    q3 = sdk.create_point(kind="statement", content="other")["id"]
    narrow = sdk.create_operator("IMPL", q1, [q3])
    assert [(i, t) for i, t in _operator_input_slots(sdk, narrow["id"])] == \
        [(0, q1), (1, q3)]

    assert sdk._find_operator("IMPL", [q1, q1, q3]) is None, (
        "a 3-slot request must not exact-hit a 2-slot operator")

    # the ingest spelling: two same-content refs fold onto ONE node, so the
    # request's real slot list is `[q1, q1, q3]` — 3 slots the graph can hold
    bundle = {"points": [{"ref": "r1", "kind": "statement", "content": "same"},
                         {"ref": "r2", "kind": "statement", "content": "same"},
                         {"ref": "r3", "kind": "statement", "content": "other"}],
              "connections": [{"from": "r1", "to": ["r2", "r3"],
                               "operator": "IMPL"}]}
    res = sdk.ingest(bundle)

    assert res["ids"]["refs"]["r1"] == res["ids"]["refs"]["r2"], res["ids"]
    assert res["created"]["connections"] == 1, res
    assert res["deduped"]["connections"] == 0, res
    assert res["ids"]["connections"] != [narrow["id"]], res["ids"]
    wide = res["ids"]["connections"][0]
    assert [(i, t) for i, t in _operator_input_slots(sdk, wide)] == \
        [(0, q1), (1, q1), (2, q3)], _operator_input_slots(sdk, wide)


def test_crash_partial_still_absorbs_a_repeated_target(make_sdk):
    """#10305 — the arity leg must NOT disturb the crash-partial absorb, which is
    the one place the ingest-side ``dsts = _distinct_dsts`` collapse is
    load-bearing (the previous reviewer measured it).

    A crash after the source slot only leaves a one-slot operator; the retry
    spells the SAME target twice. The request is collapsed to 2 slots, the
    exact arm misses (1 distinct endpoint vs 2), the partial arm takes it, and
    the completion writes ONE edge: 1 operator, 2 typed + 2 `INPUT` edges, and
    an ``operator_absorb_completed`` payload of ``:1``.
    """
    sdk = make_sdk("crash_partial_repeated")
    a = sdk.create_point(kind="statement", content="one")["id"]
    b = sdk.create_point(kind="statement", content="two")["id"]
    op = sdk.create_operator("IMPL", a, [])  # the crash remnant: source slot only
    assert _operator_input_slots(sdk, op["id"]) == [(0, a)]

    res = sdk.ingest({"connections": [
        {"from": a, "to": [b, b], "operator": "IMPL"}]})

    assert res["deduped"]["connections"] == 1, res
    assert res["created"]["connections"] == 0, res["created"]
    done = [w for w in res["warnings"] if w.startswith("operator_absorb_completed")]
    assert len(done) == 1 and done[0].endswith(":1"), res["warnings"]
    assert _count(sdk, "MATCH (o:Point {is_operator:true}) RETURN count(o)") == 1
    assert _edge_counts(sdk) == (2, 2), _edge_counts(sdk)
    assert _operator_input_slots(sdk, op["id"]) == [(0, a), (1, b)]


def test_absorb_reports_the_writes_it_makes(make_sdk):
    """Review round 2, P3 — `operator_absorb_completed` must count EDGES WRITTEN.

    It used to be derived as ``len([src] + dsts) - len(written)``: a SLOT count
    minus an id SET. Here the request is duplicate-free and exactly one input is
    missing, so the payload must say ``:1``.

    ⛔ This is a PIN on the ordinary path, NOT a discriminating test, and the
    docstring says so on purpose (#10305 round 6): once `_find_operator`
    declines a request whose own slot list repeats an id, no request can reach
    the completion loop with a repeated id, so the old derivation and the new
    count are provably equal on every reachable path — this test passes on the
    pre-fix source too. The change is structural (count what the loop wrote,
    rather than subtract an id set from a slot count), which is the property
    the §3.2 key-8 payload needs; it is not a behaviour change anyone can
    observe, and it should not be cited as one."""
    sdk = make_sdk("absorb_count")
    a = sdk.create_point(kind="statement", content="one")["id"]
    b = sdk.create_point(kind="statement", content="two")["id"]
    c = sdk.create_point(kind="statement", content="three")["id"]
    sdk.create_operator("IMPL", a, [b])           # crash partial: {a, b} of {a, b, c}

    res = sdk.ingest({"connections": [
        {"from": a, "to": [b, c], "operator": "IMPL"}]})

    done = [w for w in res["warnings"] if w.startswith("operator_absorb_completed")]
    assert len(done) == 1, res["warnings"]
    assert done[0].endswith(":1"), done[0]
    assert _edge_counts(sdk) == (3, 3), _edge_counts(sdk)


# ═══════════════════════════════════════════════════════════════════════
# The PRIMITIVE — the root fix every door inherits
# ═══════════════════════════════════════════════════════════════════════

def test_create_operator_dedups_repeated_target(make_sdk):
    """``sdk.create_operator(op, a, [b, b])`` — the primitive cannot
    double-write, so no door (MCP, extraction, `decide`, battery) can."""
    sdk = make_sdk("primitive")
    a = sdk.create_point(kind="statement", content="one")
    b = sdk.create_point(kind="statement", content="two")

    op = sdk.create_operator("IMPL", a["id"], [b["id"], b["id"]])

    assert _edge_counts(sdk) == (2, 2), _edge_counts(sdk)
    slots = _operator_input_slots(sdk, op["id"])
    assert [i for i, _ in slots] == [0, 1], slots
    assert len({t for _, t in slots}) == 2, slots


def test_create_operator_keeps_self_support_shape(make_sdk):
    """The dedupe is among the TARGETS, never against ``source_id``:
    ``create_operator(op, a, [a])`` is the deliberate self-support operator
    (``idx`` 0 and 1 over one id, pinned by
    ``tests/test_6871_operator_input_set.py``) and must keep both edges."""
    sdk = make_sdk("self_support")
    a = sdk.create_point(kind="statement", content="one")

    op = sdk.create_operator("IMPL", a["id"], [a["id"]])

    slots = _operator_input_slots(sdk, op["id"])
    assert [i for i, _ in slots] == [0, 1], slots
    assert {t for _, t in slots} == {a["id"]}, slots


def test_replay_parity_deduped(tmp_path):
    """The journal carries the COLLAPSED target list, so a rebuild reproduces
    the deduped graph (a live-only repair would make ``rebuild_all`` mint the
    duplicate back)."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(os.path.join(str(tmp_path), "journal.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        a = sdk.create_point(kind="statement", content="one")
        b = sdk.create_point(kind="statement", content="two")
        sdk.create_operator("IMPL", a["id"], [b["id"], b["id"]])
        assert _edge_counts(sdk) == (2, 2), _edge_counts(sdk)

        journal = [json.loads(line) for line in
                   (events / "events.jsonl").read_text().splitlines()
                   if line.strip()]
        added = [e for e in journal if e.get("eventKind") == "OperatorAdded" or
                 e.get("type") == "OperatorAdded"]
        assert added, [e.get("eventKind") for e in journal]

        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        assert _edge_counts(sdk) == (2, 2), (\
            "rebuild re-minted the duplicate edge — the OperatorAdded payload "
            "must carry the deduped target list", _edge_counts(sdk))
    finally:
        sdk.close()


# ═══════════════════════════════════════════════════════════════════════
# The dedup KEY — legacy graphs and the guards that must NOT weaken
# ═══════════════════════════════════════════════════════════════════════

def test_legacy_duplicate_graph_still_dedups(make_sdk):
    """A graph written BEFORE this fix carries duplicate edges (``idx`` 1 and 2
    over one id). ``_find_operator`` reads ``DISTINCT t.id``, so that operator
    still EXACT-matches its own input set — re-ingest dedups instead of minting
    a SECOND operator (which is the duplicate this whole issue is about).

    #10305 — this is WHY the exact arm's arity leg is an OR: this operator has 3
    typed slots against a 2-slot request, so arity alone would refuse it. The
    carve-out is shape-keyed: its excess slot repeats a **TARGET**, a shape no
    request can spell (the writer collapses repeated targets), so it is adopted;
    an excess slot that repeats the **SOURCE** is spellable and is not (see
    ``test_self_support_operator_with_extra_target_mints_its_own_arity``).
    """
    sdk = make_sdk("legacy")
    a = sdk.create_point(kind="statement", content="one")
    b = sdk.create_point(kind="statement", content="two")
    op = sdk.create_operator("IMPL", a["id"], [b["id"]])
    # simulate the pre-#9750 writer: a second typed + INPUT edge over one target
    sdk._get_proj().g.query(
        "MATCH (o:Point {id:$o}), (t:Point {id:$t}) "
        "CREATE (o)-[:IMPL {idx:2}]->(t) CREATE (t)-[:INPUT {idx:2}]->(o)",
        params={"o": op["id"], "t": b["id"]})
    assert _edge_counts(sdk) == (3, 3), _edge_counts(sdk)

    res = sdk.ingest({"connections": [
        {"from": a["id"], "to": [b["id"]], "operator": "IMPL"}]})

    assert res["deduped"]["connections"] == 1, res["created"]
    assert res["created"]["connections"] == 0, res["created"]
    assert res["ids"]["connections"] == [op["id"]], res["ids"]["connections"]
    assert _count(sdk, "MATCH (o:Point {is_operator:true}) RETURN count(o)") == 1
    assert _edge_counts(sdk) == (3, 3), _edge_counts(sdk)  # no NEW edges written


def test_self_edge_guard_not_weakened(make_sdk):
    """The `_check_connection` self-edge arm is untouched: a `to` that repeats
    the `from` ref is still a Phase-1 violation — deduping the list must not
    turn a self-edge into a legal one."""
    from tortoise.exceptions import BundleValidationError

    sdk = make_sdk("self_edge")
    with pytest.raises(BundleValidationError) as exc:
        sdk.ingest(_bundle(["p1", "p1"]))
    assert "self-edge" in str(exc.value), str(exc.value)
    assert _edge_counts(sdk) == (0, 0), _edge_counts(sdk)


# ═══════════════════════════════════════════════════════════════════════
# Indicator 3 — the N-DISTINCT case is UNTOUCHED
# ═══════════════════════════════════════════════════════════════════════

def test_distinct_targets_unchanged(make_sdk):
    """Extra-variation guard: a 3-distinct-target operator keeps its 4-edge
    factor (idx 0..3) — the fix must move the repeated case only."""
    sdk = make_sdk("distinct")
    bundle = _bundle(["p2", "p3", "p4"], extra_points=[
        {"ref": "p3", "kind": "statement", "content": "three"},
        {"ref": "p4", "kind": "statement", "content": "four"},
    ])
    res = sdk.ingest(bundle)

    assert _edge_counts(sdk) == (4, 4), _edge_counts(sdk)
    slots = _operator_input_slots(sdk, res["ids"]["connections"][0])
    assert [i for i, _ in slots] == [0, 1, 2, 3], slots
    assert len({t for _, t in slots}) == 4, slots
    assert "duplicate_target_deduped" not in \
        [w.split(":", 1)[0] for w in res["warnings"]], res["warnings"]
