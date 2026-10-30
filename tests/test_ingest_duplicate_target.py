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
    ``_find_operator``'s key is ``size(targets) = size($inputs)``, so a request
    still carrying the caller's multiplicity (``[src, p2, p2]``) misses the
    2-target operator it just wrote, falls into the PARTIAL-ABSORB arm and
    reports a ``operator_absorb_completed`` for an operator that was already
    complete."""
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
    a SECOND operator (which is the duplicate this whole issue is about)."""
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
