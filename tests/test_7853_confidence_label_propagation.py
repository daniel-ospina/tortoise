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

import logging

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


# ═══════════════════════════════════════════════════════════════════
# 8. Round-2 review fixes — index anchor, the reference fold, write-back
# ═══════════════════════════════════════════════════════════════════

# ── P2 — the widened belief fold must stay index-anchored ──────────

def test_fold_confidence_changed_keeps_a_label_index_anchor(sdk, monkeypatch):
    """The widened belief fold must NOT drop the label.

    FalkorDB cannot use a per-(label, property) range index through a label
    predicate, so ``MATCH (n) WHERE n:Point OR … AND n.id = $id`` full-scans
    every fold (and regresses the common ``:Point`` case the ``Point(id)``
    index served). The fix sweeps ``epistemic_label_queries`` — one query per
    label, each keeping the label on the node pattern (the rule
    ``tortoise/live.py`` states).
    """
    from tortoise.projection import _GuardedGraph

    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (e:Event {id:'evt-7853-idx', eventId:'evt-7853-idx', "
        "name:'anchored'})")

    seen: list[str] = []
    original = _GuardedGraph.query

    def spy(self, cypher, *args, **kwargs):
        text = " ".join(str(cypher).split())
        if "SET n.confidence" in text:
            seen.append(text)
        return original(self, cypher, *args, **kwargs)

    monkeypatch.setattr(_GuardedGraph, "query", spy)
    matched = proj._fold_confidence_changed(
        {"id": "evt-7853-idx", "confidence": 0.42})

    assert matched == 1
    assert seen, "the fold issued no belief SET"
    for q in seen:
        assert "MATCH (n:" in q, (
            f"an id-anchored fold dropped its label and full-scans: {q!r}")
        assert "MATCH (n) WHERE" not in q, q
    assert len(seen) == len(EPISTEMIC_LABELS), (
        f"the fold must sweep one query per label; saw {len(seen)}")


# ── P2-2 (#7936) — the reference fold, BOTH directions ─────────────

_NON_POINT_BELIEF_JOURNAL = [
    {"type": "PointAdded", "point": {"id": "src-7936", "content": "s"}},
    {"type": "EventRecorded", "id": "evt-7936", "name": "e",
     "eventKind": "test", "startedAt": "2026-01-01T00:00:00Z"},
    {"type": "ConfidenceChanged", "id": "evt-7936", "confidence": 0.5},
]


def test_reference_fold_exempts_a_materialized_non_point_belief(caplog):
    """Direction 1: a belief write to a journal-materialized non-Point node
    is a BOUND of the point-only index, NOT a fold-miss.

    #7936: recording it reds a healthy graph and, because ``non-folded`` is
    tested first, also masks a genuine content divergence on it.
    """
    from tortoise.projection import fold
    from tortoise.projection.nonfolded import collect_non_folded, refused_events

    with caplog.at_level(logging.WARNING, logger="tortoise.projection"):
        with collect_non_folded() as entries:
            keys = fold(list(_NON_POINT_BELIEF_JOURNAL))

    assert list(refused_events(entries)) == [], [str(e) for e in entries]
    assert "evt-7936" not in keys, keys
    # ...and the drop is surfaced, not silent (#7936).
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "#7936" in joined, [r.getMessage() for r in caplog.records]


def test_reference_fold_still_flags_a_buried_point_belief():
    """Direction 2: the exemption must NOT swallow a genuine ``:Point`` miss
    (created → hard-deleted → belief write).

    A false ``0`` (the missed exemption) is worse than the false ``1`` this
    fixes — a silent success here would hide a belief write the replay
    genuinely cannot reproduce.
    """
    from tortoise.projection import fold
    from tortoise.projection.nonfolded import collect_non_folded, refused_events

    events = [
        {"type": "PointAdded", "point": {"id": "pt-7936", "content": "c"}},
        {"type": "EntityMutated", "op": "delete", "label": "Point",
         "id": "pt-7936"},
        {"type": "ConfidenceChanged", "id": "pt-7936", "confidence": 0.5},
    ]
    with collect_non_folded() as entries:
        fold(events)
    refused = refused_events(entries)
    assert any("pt-7936" in str(e) for e in refused), [
        str(e) for e in entries]


# The non-Point creation record the replay MATERIALIZES each label from — the
# same one ``journal_first_materialization`` reads (#7936 review).
_NON_POINT_CREATIONS = [
    ("Object", {"type": "ObjectRegistered", "id": "n-7936", "name": "n"}),
    ("Subject", {"type": "SubjectAdded", "id": "n-7936", "name": "n"}),
    ("Event", {"type": "EventRecorded", "id": "n-7936", "name": "n",
               "eventKind": "test",
               "startedAt": "2026-01-01T00:00:00Z"}),
]


@pytest.mark.parametrize("label,creation", _NON_POINT_CREATIONS,
                         ids=[c[0] for c in _NON_POINT_CREATIONS])
def test_reference_fold_refuses_a_non_point_belief_buried_before_it(
        label, creation):
    """Direction 3 (#7936 review): the exemption tests EXISTENCE at the write,
    not membership in the materialization map.

    A journal that CREATED this non-Point and then HARD-DELETED it BEFORE the
    belief write is a genuine miss: the graph fold (``_fold_confidence_changed``)
    matches 0 rows at that position and ``rebuild_all`` raises
    ``NonFoldedEventsError [point-belief-miss]``. A membership-only exemption
    would return ``ok=True, exit 0`` here — the fail-OPEN direction, worse than
    the false red it replaced.
    """
    from tortoise.projection import fold
    from tortoise.projection.nonfolded import collect_non_folded, refused_events

    nid = creation["id"]
    events = [
        dict(creation),
        {"type": "EntityMutated", "op": "delete", "label": label,
         "id": nid},
        {"type": "ConfidenceChanged", "id": nid, "confidence": 0.77},
    ]
    with collect_non_folded() as entries:
        fold(events)
    refused = refused_events(entries)
    assert any(nid in str(e) for e in refused), [str(e) for e in entries]


@pytest.mark.parametrize("label,creation", _NON_POINT_CREATIONS,
                         ids=[c[0] for c in _NON_POINT_CREATIONS])
def test_reference_fold_exempts_a_non_point_belief_a_later_delete_follows(
        label, creation):
    """The SAME label, the other ORDER: a delete that arrives AFTER the belief
    write is FOLDED by the graph fold (pass-1a creates, pass-1b folds the
    belief, THEN the delete), so the exemption must still apply.

    This is why plain ``_hard_deleted_any`` membership is not enough: it cannot
    tell the two orders apart, so it would refuse this record and reintroduce
    the false red #7936 removes. Both directions are pinned.
    """
    from tortoise.projection import fold
    from tortoise.projection.nonfolded import collect_non_folded, refused_events

    nid = creation["id"]
    events = [
        dict(creation),
        {"type": "ConfidenceChanged", "id": nid, "confidence": 0.77},
        {"type": "EntityMutated", "op": "delete", "label": label,
         "id": nid},
    ]
    with collect_non_folded() as entries:
        fold(events)
    assert list(refused_events(entries)) == [], [str(e) for e in entries]


def test_reference_fold_refuses_a_belief_an_id_wide_delete_buried():
    """The unknown/missing-label delete is id-wide: ``_delete_entity_by_id``
    removes the node whatever its label, so it can bury a non-Point too.

    The exemption must consult the ``(None, id)`` fallback key the way
    ``_hard_deleted_any`` does — a per-label lookup alone would fail OPEN on
    this shape.
    """
    from tortoise.projection import fold
    from tortoise.projection.nonfolded import collect_non_folded, refused_events

    events = [
        {"type": "SubjectAdded", "id": "n-7936", "name": "n"},
        {"type": "EntityMutated", "op": "delete", "id": "n-7936"},
        {"type": "ConfidenceChanged", "id": "n-7936", "confidence": 0.77},
    ]
    with collect_non_folded() as entries:
        fold(events)
    refused = refused_events(entries)
    assert any("n-7936" in str(e) for e in refused), [str(e) for e in entries]


def test_consistency_reference_fold_refuses_a_buried_non_point_belief():
    """The REPORTED symptom: ``check-consistency``'s reference fold
    (``consistency._fold_journal``) must refuse what ``rebuild_all`` refuses.

    Before this fix the exemption ran there too, so the diagnostic returned
    ``ok=True`` on a journal the graph cannot reproduce — the false-all-good
    direction. ``_fold_journal`` is the reference side of ``check_consistency``,
    so pinning it here is pinning the verdict.
    """
    from tortoise.consistency import _fold_journal
    from tortoise.projection.nonfolded import collect_non_folded, refused_events

    events = [
        {"type": "ObjectRegistered", "id": "obj-7936", "name": "o"},
        {"type": "EntityMutated", "op": "delete", "label": "Object",
         "id": "obj-7936"},
        {"type": "ConfidenceChanged", "id": "obj-7936", "confidence": 0.77},
    ]
    with collect_non_folded() as entries:
        _fold_journal(events)
    refused = refused_events(entries)
    assert any("obj-7936" in str(e) for e in refused), [
        str(e) for e in entries]


def test_reference_and_graph_folds_agree_on_a_buried_non_point_belief(
        tmp_path):
    """Ground truth for the refusal: ``rebuild_all`` itself refuses this
    journal, and the reference fold must reach the SAME verdict.

    Without the order test the diagnostic calls a journal the graph cannot
    reproduce healthy (``ok=True``); without the graph arm this test would
    prove nothing. Both sides are asserted in ONE test so a mutation of either
    reds it. ONE label is enough here: ``_fold_confidence_changed`` sweeps all
    four labels with one label-agnostic arm, and the per-label unit tests above
    pin Object/Subject/Event. (A ``rebuild_all`` per label buys a second model
    load for no additional coverage.)
    """
    import json

    from tortoise.projection import FalkorProjection, fold
    from tortoise.projection.nonfolded import (
        NonFoldedEventsError,
        collect_non_folded,
        refused_events,
    )

    label, creation = _NON_POINT_CREATIONS[0]
    nid = creation["id"]
    events = [
        dict(creation),
        {"type": "EntityMutated", "op": "delete", "label": label,
         "id": nid},
        {"type": "ConfidenceChanged", "id": nid, "confidence": 0.77},
    ]
    # Reference side.
    with collect_non_folded() as entries:
        fold(list(events))
    assert any(nid in str(e) for e in refused_events(entries)), [
        str(e) for e in entries]
    # Graph side: the SAME journal, replayed by the production engine. Built
    # directly (not via TortoiseSDK) — the SDK's embedding init costs minutes
    # for a journal that carries no Point content.
    with open(tmp_path / "events.jsonl", "w") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
    proj = FalkorProjection(path=str(tmp_path / "agree.db"),
                            skip_health_check=True)
    with pytest.raises(NonFoldedEventsError, match="point-belief-miss"):
        proj.rebuild_all(str(tmp_path), confirm_destructive=True)


def test_consistency_healthy_non_point_belief_is_not_non_folded(tmp_path):
    """End-to-end: the check must call a graph ``rebuild_all`` itself
    produced healthy, not ``non-folded``.

    Before the fix, the reference fold missed the Event belief and
    ``non-folded`` outranked the content classes, so this exact graph — the
    one the DB fold reproduces — reported ``ok=False`` with an ``action``
    telling the operator to repair a journal that is not broken (#7936).
    """
    from tortoise.consistency import check_consistency

    db = str(tmp_path / "consistency-7936.db")
    events = tmp_path / "events-7936"
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
        event = sdk.create_entity("event", "an event",
                                  eventKind="test")["node"]["id"]
        sdk.create_operator("IMPL", src, [event], direction="bidirectional")
        sdk._get_ep().run([src, event], max_hops=2,
                          evidence={src: (12.0, 1.0)})
        proj.rebuild_all(str(events), confirm_destructive=True)
        verdict = check_consistency(log_path, proj, record_state=False)
    finally:
        sdk.close()

    assert verdict["non_folded_refused_count"] == 0, \
        verdict["non_folded_events"]
    assert verdict["divergence"] != "non-folded", verdict
    assert verdict["hash_match"] is True, verdict
    assert verdict["ok"] is True, verdict


# ── P2-3 — the full-precision write-back spans the four labels ─────

def test_compute_confidence_writeback_persists_full_precision_for_non_point(sdk):
    """The write-back set == the run set (``sdk.py``).

    The EP flush persists the 4-dp mirror (``_flush_cache``); the trailing
    full-precision mean is the write-back's unique write. A ``:Point``-only
    UNWIND leaves a non-Point target holding the 4-dp mirror — the API result
    is not what the graph holds.
    """
    proj = sdk._get_proj()
    src = make_point(sdk, "strong source")
    event = make_labeled_node(sdk, "Event", "evt-7853-wb", "an event")
    set_evidence(sdk, src, 12.0, 1.0)
    sdk.create_operator("IMPL", src, [event], direction="bidirectional")

    res = sdk.compute_confidence(evidence={src: (12.0, 1.0)},
                                 require_calibration=False)
    mean = res["confidences"].get(event, {}).get("mean")
    assert mean is not None, res

    conf = confidence_of(sdk, event)
    assert conf is not None
    assert conf != round(conf, 4), (
        f"the Event kept the 4-dp `_flush_cache` mirror ({conf}) — the "
        f"full-precision write-back matched no non-Point node")
    assert conf == pytest.approx(mean, rel=1e-12), (conf, mean)


def test_dream_writeback_persists_full_precision_for_non_point(sdk):
    """The dream write-back (#1240) is the other half of the invariant.

    Direct ``_ep_run_batch`` because the dream BFS selector is ``:Point``-only
    today (#7927), so the public ``dream()`` cannot yet reach a non-Point
    endpoint — but the widened EP read puts it in the run set the write-back
    must honour, and this pins that.
    """
    proj = sdk._get_proj()
    src = make_point(sdk, "strong source")
    event = make_labeled_node(sdk, "Event", "evt-7853-dream", "an event")
    set_evidence(sdk, src, 12.0, 1.0)
    sdk.create_operator("IMPL", src, [event], direction="bidirectional")

    dreamer = sdk._get_dreamer()
    dreamer._ep_run_batch(proj, [src, event], 2, stamp=True,
                          warm_start=False)

    conf = confidence_of(sdk, event)
    assert conf is not None
    assert conf != round(conf, 4), (
        f"the dream write-back left the 4-dp mirror ({conf}) on the Event")
    stamp = proj.g.query(
        "MATCH (n:Event {id:$id}) RETURN n.lastDreamedAt",
        params={"id": event}).result_set
    assert stamp and stamp[0][0] is not None, (
        "the dream write-back did not stamp lastDreamedAt on the Event")


# ═══════════════════════════════════════════════════════════════════
# 9. Round-4 (#7936) — the ordered existence boundary
# ═══════════════════════════════════════════════════════════════════
#
# Round 4 found the interim exemption answering the WRONG question: it tested
# first-materialization membership plus a LAST-delete-wins map, which cannot
# express a RE-CREATION or a delete on either side of the belief write. It
# disagreed with `rebuild_all` in BOTH directions:
#   * `create → hard-delete → RE-CREATE (same id) → belief` was called a MISS
#     even though `rebuild_all` folds the belief onto the re-created node —
#     a FALSE RED on a healthy graph (the reported P1).
#   * an id hard-deleted both BEFORE and AFTER the write exposed only the
#     later delete, so the diagnostic called it HEALTHY while `rebuild_all`
#     refused — a FALSE GREEN (P2-3).
#
# The fix judges existence AT THE RECORD from the ordered create/delete
# boundary (`projection.journal_epistemic_existence`). This matrix is the
# deliverable: it pins that the reference fold and `rebuild_all` reach the
# SAME verdict on every ordering, so the class cannot silently return.

_MATRIX_ID = "n-7936-matrix"


def _m_object(nid: str = _MATRIX_ID) -> dict:
    return {"type": "ObjectRegistered", "id": nid, "name": "matrix-obj"}


def _m_delete(label=None, nid: str = _MATRIX_ID) -> dict:
    ev = {"type": "EntityMutated", "op": "delete", "id": nid}
    if label is not None:
        ev["label"] = label
    return ev


def _m_belief(nid: str = _MATRIX_ID) -> dict:
    return {"type": "ConfidenceChanged", "id": nid, "confidence": 0.77}


#: ``(row, events, refused?)`` — ``refused`` is the verdict BOTH folds must
#: reach. A row is "refused" when the belief write cannot be reproduced:
#: the reference fold records a `point-belief-miss` and `rebuild_all` raises
#: `NonFoldedEventsError`. The converse is "accepted" (no refusal).
_BOUNDARY_MATRIX = [
    # Each required ordering, plus the P2-2 forward reference.
    ("materialize-delete-belief",
     [_m_object(), _m_delete("Object"), _m_belief()], True),
    ("materialize-belief-delete",
     [_m_object(), _m_belief(), _m_delete("Object")], False),
    ("materialize-delete-recreate-belief",
     [_m_object(), _m_delete("Object"), _m_object(), _m_belief()], False),
    # A buried incarnation, with the RE-CREATION still AFTER the write: live
    # no-op'd the write, so it is a forward reference (skip), NOT a miss —
    # the re-creation after ``seq`` is the future signal even though an
    # earlier creation exists. The first-materialization form alone cannot see
    # this.
    ("materialize-delete-belief-recreate-after",
     [_m_object(), _m_delete("Object"), _m_belief(), _m_object()], False),
    ("delete-no-create",
     [_m_delete("Object"), _m_belief()], True),
    ("belief-no-materialization",
     [_m_belief()], True),
    ("two-deletes-around-write",
     [_m_object(), _m_delete("Object"), _m_belief(), _m_delete("Object")],
     True),
    # id-wide (missing label) removes every canonical node → burial.
    ("id-wide-delete",
     [_m_object(), _m_delete(None), _m_belief()], True),
    # label-scoped delete of a FOREIGN kind leaves the Object alive.
    ("label-scoped-delete-other-label",
     [_m_object(), _m_delete("Subject"), _m_belief()], False),
    # the P2-2 shape: the write PRECEDES its own materialization.
    ("belief-forward-references-its-materialization",
     [_m_belief(), _m_object()], False),
]


@pytest.mark.parametrize(
    "row,events,refused", _BOUNDARY_MATRIX,
    ids=[r[0] for r in _BOUNDARY_MATRIX])
def test_boundary_matrix_reference_and_graph_agree(
        tmp_path, row, events, refused):
    """The deliverable: every ordering gets ONE verdict from both classifiers.

    Reference side — the pure fold AND the diagnostic's own reference fold
    (`consistency._fold_journal`, the arm `check_consistency` runs). Graph side
    — the production replay (`FalkorProjection.rebuild_all`). All three must
    agree, so a future drift in either the exemption predicate or the graph
    gate reds a row here instead of shipping a false red/green.
    """
    import json

    from tortoise.consistency import _fold_journal
    from tortoise.projection import FalkorProjection, fold
    from tortoise.projection.nonfolded import (
        NonFoldedEventsError,
        collect_non_folded,
        refused_events,
    )

    for name, folder in (("fold", fold), ("_fold_journal", _fold_journal)):
        with collect_non_folded() as entries:
            folder(list(events))
        got = any(_MATRIX_ID in str(e) for e in refused_events(entries))
        assert got == refused, (
            f"[{row}] reference {name}: refused={got}, expected={refused}; "
            f"entries={[str(e) for e in entries]}")

    events_dir = tmp_path / "events-matrix"
    events_dir.mkdir()
    with open(events_dir / "events.jsonl", "w") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
    proj = FalkorProjection(path=str(tmp_path / "matrix.db"),
                            skip_health_check=True)
    graph_refused = False
    try:
        proj.rebuild_all(str(events_dir), confirm_destructive=True)
    except NonFoldedEventsError:
        graph_refused = True
    finally:
        proj.close()
    assert graph_refused == refused, (
        f"[{row}] graph rebuild_all: refused={graph_refused}, "
        f"expected={refused}")


def test_boundary_matrix_non_point_labels_all_agree():
    """The boundary is label-agnostic: Object / Subject / Event re-creations.

    `rebuild_all` per label would buy a second model load for no extra
    coverage (the graph gate sweeps `EPISTEMIC_LABELS` with ONE arm), so this
    pins the REFERENCE verdict for each label — including an Event, whose id
    lives under `eventId`/nested `event` and is read through the shared
    `_creation_entity_id_from_record`.
    """
    from tortoise.projection import fold
    from tortoise.projection.nonfolded import collect_non_folded, refused_events

    creations = {
        "Object": {"type": "ObjectRegistered", "id": "x", "name": "n"},
        "Subject": {"type": "SubjectAdded", "id": "x", "name": "n"},
        "Event": {"type": "EventRecorded", "id": "x", "name": "n",
                  "eventKind": "test",
                  "startedAt": "2026-01-01T00:00:00Z"},
    }
    for label, creation in creations.items():
        # create → delete → RE-CREATE → belief: accepted (P1).
        accepted = [dict(creation),
                    {"type": "EntityMutated", "op": "delete", "label": label,
                     "id": "x"},
                    dict(creation),
                    {"type": "ConfidenceChanged", "id": "x",
                     "confidence": 0.77}]
        with collect_non_folded() as entries:
            fold(accepted)
        assert list(refused_events(entries)) == [], (label, [
            str(e) for e in entries])
        # delete → belief with NO re-creation: refused.
        buried = [dict(creation),
                  {"type": "EntityMutated", "op": "delete", "label": label,
                   "id": "x"},
                  {"type": "ConfidenceChanged", "id": "x",
                   "confidence": 0.77}]
        with collect_non_folded() as entries:
            fold(buried)
        assert any("x" in str(e) for e in refused_events(entries)), (
            label, [str(e) for e in entries])


def test_content_comparison_known_gap_non_point_belief_is_invisible(tmp_path):
    """#7936 KNOWN GAP — this asserts the PRESENT, WRONG verdict, not a right.

    `check_consistency`'s content leg compares a POINT-ONLY journal fold
    against `MATCH (n:Point)` and `db_hash` fingerprints that Point-only dict,
    so a corrupted belief on a `:Subject`/`:Object`/`:Event` is invisible.

    This test PINS the blind spot so it is visible in the suite and MUST be
    deleted or inverted when #7936's `(label, id)` re-key lands: at that point
    the verdict below becomes `ok=False, divergence="content"` and this test
    goes RED, which is the intended signal — NOT a regression.
    """
    from tortoise.consistency import check_consistency

    db = str(tmp_path / "gap-7936.db")
    events = tmp_path / "events-7936"
    events.mkdir()
    log_path = str(events / "events.jsonl")
    sdk = TortoiseSDK(db, event_log_path=log_path)
    try:
        proj = sdk._get_proj()
        src = sdk.create_point("statement", "strong source", status="live")["id"]
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.ep_alpha=12.0, n.ep_beta=1.0, "
            "n.baseline_set=true", params={"id": src})
        event = sdk.create_entity("event", "an event",
                                  eventKind="test")["node"]["id"]
        sdk.create_operator("IMPL", src, [event], direction="bidirectional")
        sdk._get_ep().run([src, event], max_hops=2,
                          evidence={src: (12.0, 1.0)})
        proj.rebuild_all(str(events), confirm_destructive=True)

        healthy = check_consistency(log_path, proj, record_state=False)
        assert healthy["ok"] is True, healthy

        # Corrupt ONLY the Event's belief; the journal is untouched.
        proj.g.query(
            "MATCH (n:Event {id:$id}) SET n.confidence=0.01",
            params={"id": event})
        changed = proj.g.query(
            "MATCH (n:Event {id:$id}) RETURN n.confidence",
            params={"id": event}).result_set
        assert changed and changed[0][0] == 0.01, changed

        after = check_consistency(log_path, proj, record_state=False)
    finally:
        sdk.close()

    # DOCUMENTED GAP: the corruption is NOT seen. When #7936 lands, these
    # assertions are the ones that must flip.
    assert after["ok"] is True, after
    assert after["divergence"] is None, after
    assert after["hash_match"] is True, after
