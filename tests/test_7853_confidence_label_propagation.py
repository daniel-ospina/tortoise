"""#7853 — confidence propagation must read :Subject / :Object / :Event / :Point.

Owner ruling (#7813/#7853): confidence propagates across all four epistemic
labels. The operator node itself stays a ``:Point`` (``is_operator=true``);
what widens is its CHILDREN/TARGETS.

Before the widening, every endpoint read on the EP path was ``:Point``-only,
so an operator input written as an ``:Event`` (the *reachable* half today —
``create_operator`` accepts ``(s:Point OR s:Event)`` endpoints) was silently
invisible: ``_affected_claims`` admitted nothing and ``_affected_factors``
Batch-2 returned no inputs, so the factor was skipped and confidence never
moved. ``:Subject``/``:Object`` operator endpoints are forward-looking (the
write path does not admit them yet, tracked separately), so those tests wire
the operator edge directly and exercise the ENGINE read widening.

These tests go RED when the widening is reverted: with a ``:Point``-only read
the non-Point input is absent from the factor, the factor degenerates to <2
inputs, and the node's posterior stays null (never written).
"""
from __future__ import annotations

import pytest

from tortoise.ep import TortoiseEP
from tortoise.live import (
    EPISTEMIC_LABELS,
    epistemic_disjunction,
    epistemic_label_queries,
)
from tortoise.sdk import TortoiseSDK


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def make_point(sdk: TortoiseSDK, content: str) -> str:
    """A live :Point claim (EP tests model live claims; #780 excludes drafts)."""
    return sdk.create_point("statement", content, status="live")["id"]


def make_labeled_node(sdk: TortoiseSDK, label: str, node_id: str,
                      name: str) -> str:
    """Create a bare epistemic node of ``label`` (Event/Subject/Object).

    The SDK only mints :Point claims; these node classes are written by the
    entity/pack paths. The EP read surface must admit them regardless of who
    wrote them, so the test writes them directly.
    """
    proj = sdk._get_proj()
    if label == "Event":
        proj.g.query(
            "CREATE (e:Event {id:$id, eventId:$id, eventKind:'test', "
            "startedAt:'2026-01-01T00:00:00Z', name:$name})",
            params={"id": node_id, "name": name},
        )
    else:
        proj.g.query(
            f"CREATE (n:{label} {{id:$id, name:$name}})",
            params={"id": node_id, "name": name},
        )
    return node_id


def wire_operator(sdk: TortoiseSDK, op_type: str, source_id: str,
                  target_id: str, direction: str = "bidirectional") -> str:
    """Operator (:Point {is_operator:true}) with source(idx=0)/target(idx=1).

    Used for the forward-looking labels whose endpoints create_operator does
    not yet admit; it mirrors exactly what create_operator writes.
    """
    proj = sdk._get_proj()
    op_id = f"op-{source_id}-{target_id}"
    proj.g.query(
        "CREATE (o:Point {id:$opid, is_operator:true, op_type:$op, "
        "direction:$dir, status:'live'})",
        params={"opid": op_id, "op": op_type, "dir": direction},
    )
    for idx, sid in enumerate((source_id, target_id)):
        proj.g.query(
            f"MATCH (o:Point {{id:$opid}}), (s) WHERE s.id = $sid "
            f"CREATE (o)-[:{op_type} {{idx:$idx}}]->(s) "
            f"CREATE (s)-[:INPUT {{idx:$idx}}]->(o)",
            params={"opid": op_id, "sid": sid, "idx": idx},
        )
    return op_id


def set_evidence(sdk: TortoiseSDK, pid: str, alpha: float, beta: float) -> None:
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha=$a, n.ep_beta=$b, "
        "n.baseline_set=true",
        params={"id": pid, "a": alpha, "b": beta},
    )


def run_ep(sdk: TortoiseSDK, seeds: list[str]) -> dict[str, float]:
    proj = sdk._get_proj()
    ev = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta"
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in ev} if ev else {}
    ep = TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3,
                    evidence=evidence)
    ep.run(seeds, max_hops=2)
    # Read the written posterior straight off the target node, label-agnostic.
    out: dict[str, float] = {}
    for r in proj.g.query(
        "MATCH (n) WHERE n.id IN $ids AND n.confidence IS NOT NULL "
        "RETURN n.id, n.confidence",
        params={"ids": seeds},
    ).result_set:
        out[r[0]] = r[1]
    return out


def confidence_of(sdk: TortoiseSDK, node_id: str) -> float | None:
    rows = sdk._get_proj().g.query(
        "MATCH (n) WHERE n.id = $id RETURN n.confidence",
        params={"id": node_id},
    ).result_set
    return rows[0][0] if rows else None


# ═══════════════════════════════════════════════════════════════════
# Helpers — the shared abstraction (#7853 introduces the clause ONCE)
# ═══════════════════════════════════════════════════════════════════

def test_epistemic_disjunction_names_all_four_labels():
    clause = epistemic_disjunction("c")
    for label in EPISTEMIC_LABELS:
        assert f"c:{label}" in clause
    assert clause.startswith("(") and clause.endswith(")")


def test_epistemic_label_queries_expands_once_per_label():
    queries = epistemic_label_queries("MATCH (n:{label} {id:$id}) RETURN n.id")
    assert len(queries) == len(EPISTEMIC_LABELS) == 4
    # Property maps like {id:$id} are untouched (str.replace, not format).
    for label, q in zip(EPISTEMIC_LABELS, queries):  # noqa: B905
        assert f"(n:{label} " in q
        assert "{id:$id}" in q


# ═══════════════════════════════════════════════════════════════════
# 1. :Event operator input (the reachable half — create_operator path)
# ═══════════════════════════════════════════════════════════════════

def test_event_operator_target_receives_posterior(sdk):
    """A strong source IMPL-ies an :Event; the Event's confidence must rise."""
    src = make_point(sdk, "strong source")
    event = make_labeled_node(sdk, "Event", "evt-7853-a", "an event")
    set_evidence(sdk, src, 12.0, 1.0)
    # create_operator admits (s:Point OR s:Event) endpoints today.
    sdk.create_operator("IMPL", src, [event], direction="bidirectional")

    run_ep(sdk, [src, event])

    conf = confidence_of(sdk, event)
    assert conf is not None, "Event was never written a posterior (read missed it)"
    assert conf > 0.5, f"Event did not rise from neutral: {conf}"


def test_event_operator_source_is_discovered_when_seeded(sdk):
    """Seeding EP from the Event id alone must find its operator factor."""
    src = make_point(sdk, "strong source")
    event = make_labeled_node(sdk, "Event", "evt-7853-b", "an event")
    set_evidence(sdk, src, 12.0, 1.0)
    sdk.create_operator("IMPL", src, [event], direction="bidirectional")

    ep = sdk._get_ep()
    affected = ep._affected_claims([event], max_hops=2)
    assert src in affected, f"seeding from Event found {affected!r}"


# ═══════════════════════════════════════════════════════════════════
# 2/3. :Object and :Subject operator inputs (forward-looking engine reads)
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_non_point_label_target_receives_posterior(sdk, label):
    src = make_point(sdk, "strong source")
    node = make_labeled_node(sdk, label, f"{label.lower()}-7853", "a node")
    set_evidence(sdk, src, 12.0, 1.0)
    wire_operator(sdk, "IMPL", src, node)

    run_ep(sdk, [src, node])

    conf = confidence_of(sdk, node)
    assert conf is not None, f"{label} was never written a posterior"
    assert conf > 0.5, f"{label} did not rise from neutral: {conf}"


# ═══════════════════════════════════════════════════════════════════
# 4. :Point path unchanged (no regression to the hot path)
# ═══════════════════════════════════════════════════════════════════

def test_point_operator_path_still_propagates(sdk):
    src = make_point(sdk, "strong source")
    tgt = make_point(sdk, "plain target")
    set_evidence(sdk, src, 12.0, 1.0)
    sdk.create_operator("IMPL", src, [tgt], direction="bidirectional")

    run_ep(sdk, [src, tgt])

    conf = confidence_of(sdk, tgt)
    assert conf is not None and conf > 0.5, f"Point path regressed: {conf}"


def test_unrelated_node_stays_neutral(sdk):
    """Control: a node with no factor must not be written (non-vacuous)."""
    src = make_point(sdk, "strong source")
    tgt = make_point(sdk, "plain target")
    bystander = make_labeled_node(sdk, "Event", "evt-7853-bystander", "bystander")
    set_evidence(sdk, src, 12.0, 1.0)
    sdk.create_operator("IMPL", src, [tgt], direction="bidirectional")

    run_ep(sdk, [src, tgt])

    assert confidence_of(sdk, bystander) is None


# ═══════════════════════════════════════════════════════════════════
# 5. NAND with a non-Point input (direction/weight must not depend on label)
# ═══════════════════════════════════════════════════════════════════

def test_nand_object_input_lowers_target(sdk):
    """A high-confidence claim NANDs an :Object; the Object must be pushed down."""
    attacker = make_point(sdk, "high-confidence attacker")
    obj = make_labeled_node(sdk, "Object", "obj-7853-nand", "an object")
    set_evidence(sdk, attacker, 12.0, 1.0)
    wire_operator(sdk, "NAND", attacker, obj, direction="bidirectional")

    run_ep(sdk, [attacker, obj])

    conf = confidence_of(sdk, obj)
    assert conf is not None, "Object target of NAND was never written"
    assert conf < 0.5, f"NAND did not lower the Object target: {conf}"


# ═══════════════════════════════════════════════════════════════════
# 6. The direct-edge (operator-less) path stays :Point-only
# ═══════════════════════════════════════════════════════════════════
#
# The operator-mediated read widening (the ruling's substance) must NOT be
# extended to the operator-less DIRECT edge. The direct-edge WRITE path only
# ever creates `:Point`→`:Point` edges and `_affected_factors` Batch 3 reads
# the factor as `(a:Point)-[r:IMPL|NAND]->(b:Point)` — so admitting a non-Point
# endpoint to the affected set puts the two sides out of lockstep: it forms no
# factor, and `_update_claim_posterior` then recomputes the node from empty
# natural parameters, `_flush_cache` overwriting its persisted prior with
# Beta(1,1). Widen BOTH or neither.

def test_operatorless_direct_edge_to_non_point_does_not_clobber_prior(sdk):
    """#7853 P1-A: a direct IMPL edge to an :Event must not admit it.

    A separate operator factor keeps `factors` non-empty (an empty factor set
    early-returns before the write-back, masking the defect), so the admitted
    Event is reached by `_update_claim_posterior` and would be reset.
    """
    proj = sdk._get_proj()
    src = make_point(sdk, "direct source")
    tgt = make_point(sdk, "operator target")
    event = make_labeled_node(sdk, "Event", "evt-7853-direct", "direct target")
    # A real factor, so the run does not early-return before write-back.
    sdk.create_operator("IMPL", src, [tgt], direction="bidirectional")
    set_evidence(sdk, src, 12.0, 1.0)
    # A PERSISTED PRIOR on the Event — NOT baseline_set (a baseline is
    # immutable and would mask the clobber this test exists to catch).
    proj.g.query(
        "MATCH (n:Event {id:$id}) SET n.ep_alpha=7.0, n.ep_beta=2.0",
        params={"id": event})
    # Operator-less direct edge — the #888 W5 shape, non-Point target.
    proj.g.query(
        "MATCH (a:Point {id:$a}), (b:Event {id:$b}) "
        "CREATE (a)-[:IMPL {direction:'bidirectional'}]->(b)",
        params={"a": src, "b": event})

    run_ep(sdk, [src])

    rows = proj.g.query(
        "MATCH (n:Event {id:$id}) RETURN n.ep_alpha, n.ep_beta",
        params={"id": event}).result_set
    assert rows == [[7.0, 2.0]], (
        f"the operator-less direct edge admitted the Event and reset its prior "
        f"to Beta(1,1): {rows}")


def test_operatorless_direct_edge_to_non_point_not_widened_at_bfs_hop(sdk):
    """#7853 P1-A: the BFS direct hop must stay `:Point`-only too.

    The non-Point node sits two hops from the seed (seed —direct— pivot
    —direct— Event), so it is reached by the BFS direct-edge expansion rather
    than the seed's own admission. The seed↔pivot direct edge is itself a
    factor, so the run does not early-return before write-back.
    """
    proj = sdk._get_proj()
    src = make_point(sdk, "bfs source")
    pivot = make_point(sdk, "bfs pivot")
    event = make_labeled_node(sdk, "Event", "evt-7853-bfs", "bfs target")
    set_evidence(sdk, src, 12.0, 1.0)
    proj.g.query(
        "MATCH (n:Event {id:$id}) SET n.ep_alpha=7.0, n.ep_beta=2.0",
        params={"id": event})
    for a, b in ((src, pivot), (pivot, event)):
        proj.g.query(
            "MATCH (x:Point {id:$a}), (y {id:$b}) "
            "CREATE (x)-[:IMPL {direction:'bidirectional'}]->(y)",
            params={"a": a, "b": b})

    run_ep(sdk, [src])

    rows = proj.g.query(
        "MATCH (n:Event {id:$id}) RETURN n.ep_alpha, n.ep_beta",
        params={"id": event}).result_set
    assert rows == [[7.0, 2.0]], (
        f"the BFS direct hop admitted the Event and reset its prior to "
        f"Beta(1,1): {rows}")


# ═══════════════════════════════════════════════════════════════════
# 7. Durability — a non-Point belief write must replay (P1-B)
# ═══════════════════════════════════════════════════════════════════
#
# The widening makes EP journal a `ConfidenceChanged` for a non-Point target.
# Every replay consumer resolves a belief write by id, and the #2884 fold used
# `MATCH (n:Point {id:$id})` — so a lost DB could not be rebuilt: the fold
# matched nothing and `rebuild_all` raised `NonFoldedEventsError`
# `[point-belief-miss]` for the non-Point id.

EVENT_BELIEF_PROPS = ("posterior_alpha", "posterior_beta", "confidence")


def _belief(sdk: TortoiseSDK, node_id: str) -> dict:
    rows = sdk._get_proj().g.query(
        "MATCH (n) WHERE n.id = $id "
        "RETURN n.posterior_alpha, n.posterior_beta, n.confidence",
        params={"id": node_id}).result_set
    assert rows, f"no node for {node_id}"
    return dict(zip(EVENT_BELIEF_PROPS, rows[0]))  # noqa: B905


def test_non_point_belief_write_survives_rebuild(tmp_path):
    """#7853 P1-B: an :Event operator target's journaled belief round-trips.

    `create_operator` admits an `:Event` endpoint today, so this is the
    reachable half. The journal is wiped and replayed; `rebuild_all` must
    succeed (before the fix it raised `NonFoldedEventsError`) and reproduce the
    same belief on the Event. `rebuild` (the EventLog apply engine) shares the
    same `_fold_confidence_changed`, so it is checked on the same journal.
    """
    from tortoise.log import EventLog

    db = str(tmp_path / "p1b.db")
    events = tmp_path / "events"
    events.mkdir()
    log_path = str(events / "events.jsonl")
    sdk = TortoiseSDK(db, event_log_path=log_path)
    try:
        proj = sdk._get_proj()
        src = sdk.create_point("statement", "strong source",
                               status="live")["id"]
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.ep_alpha=12.0, n.ep_beta=1.0, "
            "n.baseline_set=true", params={"id": src})
        # A journaled Event (the entity path emits EventRecorded, so replay
        # can re-materialise the node the belief write targets).
        event = sdk.create_entity("event", "an event",
                                  eventKind="test")["node"]["id"]
        sdk.create_operator("IMPL", src, [event], direction="bidirectional")
        sdk._get_ep().run([src, event], max_hops=2,
                          evidence={src: (12.0, 1.0)})

        before = _belief(sdk, event)
        assert before["posterior_alpha"] is not None, before

        proj.rebuild_all(str(events), confirm_destructive=True)
        assert _belief(sdk, event) == before, (
            f"the Event belief did not round-trip through rebuild_all: "
            f"{before} -> {_belief(sdk, event)}")

        proj.rebuild(EventLog(log_path), confirm_destructive=True)
        assert _belief(sdk, event) == before, (
            f"the Event belief did not round-trip through rebuild: "
            f"{before} -> {_belief(sdk, event)}")
    finally:
        sdk.close()
