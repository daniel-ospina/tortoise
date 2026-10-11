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

The EP-widening tests in the first two sections go RED when the widening is
reverted: with a ``:Point``-only read the non-Point input is absent from the
factor, the factor degenerates to <2 inputs, and the node's posterior stays null
(never written). The reference-fold, consistency, boundary-matrix and promotion
sections further down do NOT depend on that widening — they exercise
``projection.journal_epistemic_existence``, ``fold()``, ``_fold_journal`` and
``_promote_incident_operators``, which read the journal directly, and stay green
with ``ep.py`` reverted.
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


def test_epistemic_boundary_labels_are_the_belief_targets():
    """#7903: the boundary node table must cover EXACTLY the labels the belief
    fold can target — no more (a label with no belief verdict has no boundary)
    and no fewer (a missing label makes the state-op identity model a no-op
    for it, the P1a fail-open).
    """
    from tortoise.projection import _EPISTEMIC_BOUNDARY_LABELS

    assert frozenset(EPISTEMIC_LABELS) == _EPISTEMIC_BOUNDARY_LABELS


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
# 6. The operator-less direct edge — widened on BOTH sides (#7926)
# ═══════════════════════════════════════════════════════════════════
#
# #7853 left the operator-less DIRECT edge `:Point`-only and stated the choice
# as a rule: the read side (`_affected_claims`) and the factor side
# (`_affected_factors` Batch 3) move together — "Widen BOTH or neither". The
# hazard is the HALF-widened state: admitting a non-Point endpoint to the
# affected set while Batch 3 still reads `(a:Point)-[r:IMPL|NAND]->(b:Point)`
# forms no factor, so `_update_claim_posterior` recomputes that node from EMPTY
# natural parameters and `_flush_cache` overwrites whatever it held with
# Beta(1,1).
#
# #7926 takes the rule's OTHER licensed branch: it widens the direct-edge BFS
# hop AND Batch 3 in lockstep (the write path already admits the four labels,
# #7902), so a non-Point endpoint is a genuine factor participant with a real
# message. Measured (2026-10-11), it lands on the SAME posterior an identical
# `:Point` endpoint gets — a proper belief, not Beta(1,1). The two tests below
# pin that by running each shape twice, once with a `:Point` target and once
# with a non-Point one, and comparing. They go RED if the widening is reverted:
# the non-Point target is then never admitted, no posterior is written, and its
# stale `ep_alpha` survives.

def _direct_edge_target_outcome(tmp_path, label: str, shape: str) -> tuple:
    """Run an operator-less direct-edge shape; return the target's written values.

    ``shape='one_hop'``: a real operator factor plus one direct edge from the
    seed to the target (the shape of #7853's first test). ``shape='two_hop'``:
    seed —direct— pivot —direct— target, so the target is reached by the BFS
    direct hop rather than the seed's own admission.
    Returns ``(ep_alpha, ep_beta, posterior_alpha, posterior_beta)``.
    """
    sdk = TortoiseSDK(db_path=str(tmp_path / f"direct-{shape}-{label}.db"))
    proj = sdk._get_proj()
    src = make_point(sdk, "seed")
    if label == "Point":
        target = make_point(sdk, "target")
    else:
        target = make_labeled_node(sdk, label, f"tgt-{shape}-{label}", "target")
    if shape == "two_hop":
        pivot = make_point(sdk, "pivot")
        edges = [(src, pivot), (pivot, target)]
    else:
        # A real factor, so the run does not early-return before write-back.
        sdk.create_operator("IMPL", src, [make_point(sdk, "op target")],
                            direction="bidirectional")
        edges = [(src, target)]
    set_evidence(sdk, src, 12.0, 1.0)
    # A value the target did NOT derive itself — under an empty-parameter
    # recompute this is what the Beta(1,1) clobber replaces. NOT baseline_set
    # (a baseline is immutable and would mask the clobber).
    proj.g.query(
        "MATCH (n) WHERE n.id=$id SET n.ep_alpha=7.0, n.ep_beta=2.0",
        params={"id": target})
    for a, b in edges:
        proj.g.query(
            "MATCH (x:Point {id:$a}), (y) WHERE y.id=$b "
            "CREATE (x)-[:IMPL {direction:'bidirectional'}]->(y)",
            params={"a": a, "b": b})

    run_ep(sdk, [src])

    row = proj.g.query(
        "MATCH (n) WHERE n.id=$id "
        "RETURN n.ep_alpha, n.ep_beta, n.posterior_alpha, n.posterior_beta",
        params={"id": target}).result_set[0]
    return tuple(row)


def test_operatorless_direct_edge_to_non_point_gets_a_proper_posterior(tmp_path):
    """#7926 (the rule's other branch): a direct IMPL edge to an :Event is a
    factor participant, so the Event lands on a PROPER posterior.

    A separate operator factor keeps `factors` non-empty (an empty factor set
    early-returns before the write-back), so the admitted Event really is
    reached by `_update_claim_posterior` — and, because Batch 3 was widened in
    lockstep, it gets a message rather than an empty-parameter recompute.
    """
    control = _direct_edge_target_outcome(tmp_path, "Point", "one_hop")
    event = _direct_edge_target_outcome(tmp_path, "Event", "one_hop")
    assert event[2] is not None and event[3] is not None, (
        f"the Event was not EP-live — no posterior written: {event}")
    assert (event[0], event[1]) != (1.0, 1.0), (
        f"the Event was recomputed from empty natural parameters and "
        f"clobbered with Beta(1,1): {event}")
    assert event == control, (
        f"the Event's outcome {event} differs from the identical :Point "
        f"control {control}")


def test_operatorless_direct_edge_to_non_point_is_admitted_at_bfs_hop(tmp_path):
    """The BFS direct hop is widened too, so a two-hop non-Point endpoint is
    EP-live.

    The target sits two hops from the seed (seed —direct— pivot —direct—
    target), so it is reached by the BFS direct-edge expansion rather than the
    seed's own admission. Same rule, same evidence: the non-Point outcome must
    equal the `:Point` control's and must not be Beta(1,1).
    """
    control = _direct_edge_target_outcome(tmp_path, "Point", "two_hop")
    event = _direct_edge_target_outcome(tmp_path, "Event", "two_hop")
    assert event[2] is not None and event[3] is not None, (
        f"the Event was not EP-live — no posterior written: {event}")
    assert (event[0], event[1]) != (1.0, 1.0), (
        f"the Event was recomputed from empty natural parameters and "
        f"clobbered with Beta(1,1): {event}")
    assert event == control, (
        f"the Event's outcome {event} differs from the identical :Point "
        f"control {control}")


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


def test_fold_confidence_changed_sweeps_every_label_not_just_the_first(sdk):
    """The replay belief fold must write EVERY epistemic label an id is carried
    under, exactly as the live writers do.

    ``TortoiseEP._write_node`` / ``_flush_cache``, ``TortoiseSDK.compute_confidence``
    and ``Dreamer._ep_run_batch`` all sweep all four labels and never ``break``.
    The replay fold used to stop at the first matching label, so when one id is
    carried by a ``:Point`` node AND a separate ``:Object`` node — a reachable
    duplicate-id shape (``PointAdded X`` + ``ObjectRegistered X``) — replay
    updated only the Point while live updated both: a silent live/replay
    divergence. The SET is idempotent, so all four are swept and the matched
    count accumulates.
    """
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (p:Point {id:'dup-7853', pointKind:'statement', "
        "content:'p', status:'live'})")
    proj.g.query(
        "CREATE (o:Object {id:'dup-7853', name:'dup-obj', status:'live'})")

    matched = proj._fold_confidence_changed(
        {"id": "dup-7853", "confidence": 0.42})

    assert matched == 2, matched
    point_conf = proj.g.query(
        "MATCH (n:Point {id:'dup-7853'}) RETURN n.confidence").result_set
    object_conf = proj.g.query(
        "MATCH (n:Object {id:'dup-7853'}) RETURN n.confidence").result_set
    assert point_conf and point_conf[0][0] == 0.42, point_conf
    assert object_conf and object_conf[0][0] == 0.42, object_conf


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

    with (caplog.at_level(logging.WARNING, logger="tortoise.projection"),
          collect_non_folded() as entries):
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

    The boundary does NOT keep a ``(None, id)`` fallback key: a missing or
    unknown label expands to ``_HARD_DELETE_LABELS``
    (``projection/__init__.py``'s delete arm), so the delete is recorded on
    the resolved node's own per-label boundary — here a real ``Subject``
    burial. ``_hard_deleted_any``'s ``(None, rid)`` lookup belongs to the
    separate ``hard_deleted_seq`` structure the supersede sweeps use, not to
    this ordered boundary. The exemption must therefore READ that boundary at
    the belief record (``create``, then ``delete``) or it would fail OPEN on
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


def test_dream_coverage_denominator_includes_non_point_operator_input(sdk):
    """#7853 review (P1): the coverage DENOMINATOR must widen with the run set.

    ``_affected_claims`` admits a non-`:Point` operator input and
    ``dream(mode="local")`` reaches one through its dirty roots, so
    ``_window_closure`` / ``_reachable_claim_count`` (the coverage
    denominator, which mirrors ``_affected_claims``'s reach) must admit it
    too — or ``coverage = affected / closure`` breaks the documented
    ``0 <= coverage <= 1.0`` bound (`_window_closure` docstring, #6597).

    Pre-fix this graph reports ``coverage == 1.5``: ``affected = {a, b, ev}``
    over a ``:Point``-only closure ``{a, b}``.
    """
    a = make_point(sdk, "claim A")
    b = make_point(sdk, "claim B")
    ev = make_labeled_node(sdk, "Event", "evt-coverage-7853", "an event")
    # A ternary IMPL operator: two live :Points keep it past the selector's
    # derived-liveness gate; the :Event is the newly-admitted input.
    sdk.create_operator("IMPL", a, [b, ev], direction="bidirectional")
    sdk._dirty_roots.clear()
    sdk._dirty_roots.add(a)

    local = sdk.dream(mode="local", require_calibration=False)

    assert ev in local["affected_claims"], (
        "precondition: the widened run set must admit the :Event operator "
        f"input, got {local['affected_claims']}")
    assert 0.0 <= local["coverage"] <= 1.0, (
        f"local coverage {local['coverage']} exceeds the 1.0 bound — the "
        "`_window_closure` denominator did not widen with "
        "`_affected_claims`")

    full = sdk.dream(mode="full", require_calibration=False)
    assert 0.0 <= full["coverage"] <= 1.0, (
        f"full coverage {full['coverage']} exceeds the 1.0 bound — the "
        "`_reachable_claim_count` denominator did not widen with the run set")


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


def _m_object(nid: str = _MATRIX_ID, name: str = "matrix-obj") -> dict:
    # ``name`` is the graph MERGE key (`_upsert_object`): a second registration
    # of one name under a fresh id is the collapse these rows pin.
    return {"type": "ObjectRegistered", "id": nid, "name": name}


def _m_subject(nid: str = _MATRIX_ID, name: str = "matrix-sub") -> dict:
    return {"type": "SubjectAdded", "id": nid, "name": name}


def _m_rename(nid: str = _MATRIX_ID,
              new_name: str = "matrix-renamed",
              label: str = "Object",
              state: dict | None = None) -> dict:
    # The graph fold's `SET n += $state` carries the full APPLIED map, so
    # `state.name` MOVES the Object MERGE key (`_fold_entity_mutation`) — and
    # a `state.id` moves IDENTITY.
    return {"type": "EntityMutated", "op": "rename", "label": label,
            "id": nid,
            "state": state if state is not None else {"name": new_name}}


def _m_delete(label=None, nid: str = _MATRIX_ID) -> dict:
    ev = {"type": "EntityMutated", "op": "delete", "id": nid}
    if label is not None:
        ev["label"] = label
    return ev


def _m_belief(nid: str = _MATRIX_ID) -> dict:
    return {"type": "ConfidenceChanged", "id": nid, "confidence": 0.77}


def _m_event(nid: str = _MATRIX_ID, name: str = "matrix-evt") -> dict:
    # `Event` is the one epistemic label whose GRAPH match key is `eventId`,
    # not `id`; both are set to the same value at creation.
    return {"type": "EventRecorded", "id": nid, "name": name,
            "eventKind": "test", "startedAt": "2026-01-01T00:00:00Z"}


def _m_point(nid: str = _MATRIX_ID, content: str = "matrix point") -> dict:
    return {"type": "PointAdded", "point": {"id": nid, "content": content}}


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
    # #7936 round-5 P1: the graph MERGEs Object/Subject by NAME, so a second
    # registration of ONE name under a FRESH id RE-IDS the single node and the
    # earlier id stops existing with NO journaled delete. The boundary must
    # model that collapse (the disposition `journal_object_surviving_keys`
    # already takes for supersedes) or it answers EXISTS for the collapsed
    # id and exempts a belief `rebuild_all` refuses — the fail-open class.
    ("name-reuse-buries-the-earlier-id",
     [_m_object(), _m_object("n-7936-matrix-2"), _m_belief()], True),
    # CONTROL: the same collapse AFTER the write — at the write the earlier id
    # is still the carrier, so the belief folds and every engine accepts.
    ("name-reuse-after-the-write-is-accepted",
     [_m_object(), _m_belief(), _m_object("n-7936-matrix-2")], False),
    # the :Subject twin of the same collapse (`_upsert_subject` MERGEs by name).
    ("subject-name-reuse-buries-the-earlier-id",
     [_m_subject(), _m_subject("n-7936-matrix-2"), _m_belief()], True),
    # an UNWRITABLE merge key materializes no node, so the record must not open
    # an incarnation the graph fold never created.
    ("unwritable-name-materializes-nothing",
     [{"type": "ObjectRegistered", "id": _MATRIX_ID, "name": ""},
      _m_belief()], True),
    # A rename MOVES the carrier. Reusing the OLD name after a rename must NOT
    # bury the renamed id — it still exists under its new name, so every engine
    # accepts. (A carrier that ignored the rename would falsely refuse here.)
    ("rename-away-then-reuse-old-name-is-accepted",
     [_m_object(), _m_rename(), _m_object("n-7936-matrix-2"), _m_belief()],
     False),
    # The mirror: a registration of the RENAMED-TO name re-ids the node, so the
    # original id stops existing and the belief refuses — same collapse, reached
    # through the rename rather than a second registration of the original name.
    ("rename-into-a-reused-name-buries-the-earlier-id",
     [_m_object(), _m_rename(new_name="matrix-renamed"),
      _m_object("n-7936-matrix-2", "matrix-renamed"), _m_belief()], True),
    # #7936 round-5 P1: `_upsert_object` MERGEs by NAME, so when MORE THAN ONE
    # node already carries the MERGE key the MERGE matches EVERY one of them and
    # `ON MATCH SET o.id=$id` re-ids them ALL. A 1:1 `(label,name) -> id`
    # carrier modelled the graph as a bijection, answered EXISTS for the ids the
    # graph had already re-id'd away, and exempted a belief `rebuild_all`
    # refuses — the reported fail-open. The node table below must instead leave
    # duplicated nodes as SEPARATE records and bury EVERY id that stops being
    # carried. (Two ids here are re-id'd to `matrix-third`; the belief targets
    # the first of them, which must be buried.)
    ("rename-collision-buries-every-dup-named-id",
     [_m_object(_MATRIX_ID, "matrix-collide"),
      _m_object("matrix-other", "matrix-other-name"),
      _m_rename("matrix-other", new_name="matrix-collide"),
      _m_object("matrix-third", "matrix-collide"),
      _m_belief(_MATRIX_ID)], True),
    # The :Subject twin: `_upsert_subject` MERGEs by name too, so the same
    # multi-node collapse applies.
    ("subject-rename-collision-buries-every-dup-named-id",
     [_m_subject(_MATRIX_ID, "matrix-sub-collide"),
      _m_subject("matrix-sub-other", "matrix-sub-other-name"),
      _m_rename("matrix-sub-other", new_name="matrix-sub-collide",
                label="Subject"),
      _m_subject("matrix-sub-third", "matrix-sub-collide"),
      _m_belief(_MATRIX_ID)], True),
    # A state op's `SET n += $state` lands the WHOLE map, so a `state.id` moves
    # IDENTITY: the node keeps existing as `(matrix-moved, name)` and the OLD
    # id is gone. The boundary must record that burial at the op's seq or it
    # exempts a belief `rebuild_all` refuses.
    ("state-op-id-change-buries-the-old-id",
     [_m_object(_MATRIX_ID, "matrix-idmove"),
      _m_rename(_MATRIX_ID, state={"id": "matrix-moved"}),
      _m_belief(_MATRIX_ID)], True),
    # FALSE-RED TWIN: the SAME id registered under TWO names leaves TWO nodes,
    # so re-id'ing the first name does NOT remove the id (the other node still
    # carries it) — the graph keeps `(n-7936-matrix, name=matrix-n2)` and
    # `rebuild_all` folds the belief. The boundary must NOT bury the id.
    ("same-id-two-names-then-name-reuse-keeps-the-id",
     [_m_object(_MATRIX_ID, "matrix-n1"),
      _m_object(_MATRIX_ID, "matrix-n2"),
      _m_object("matrix-third", "matrix-n1"),
      _m_belief(_MATRIX_ID)], False),
    # #7936 P2: a non-Point belief whose EVERY carried value fails the value gate
    # makes `_fold_confidence_changed` fold 0 rows and `rebuild_all` RECORDS a
    # `point-belief-miss`. The reference fold must not exempt it on key presence
    # alone — the exemption is gated on a value surviving the gate.
    ("non-point-belief-with-an-unfoldable-value-is-refused",
     [_m_object(_MATRIX_ID, "matrix-valuegate"),
      {"type": "ConfidenceChanged", "id": _MATRIX_ID,
       "confidence": "nope"}], True),
    # #7903 P2: an EXISTING `:Point` whose every carried belief value fails the
    # value gate — `_fold_confidence_changed` builds an empty SET list and
    # returns 0, so `rebuild_all` (and `apply()`) RECORD a `point-belief-miss`.
    # The point-only fold used to take the `if p:` branch and record NOTHING, a
    # false green.
    ("point-with-an-unfoldable-belief-value-is-refused",
     [_m_point(), {"type": "ConfidenceChanged", "id": _MATRIX_ID,
                   "confidence": "nope"}], True),
    # ── #7903 P1a/P1b: state-op IDENTITY moves for the ID-keyed labels ──
    # The state-op arm was NAME-keyed-only, but `_fold_entity_mutation` applies
    # `SET n += $state` for ANY canonical label, and `_fold_confidence_changed`
    # matches the node's `id` property for ALL four epistemic labels — so a
    # `revise` that MOVES an `:Event`'s `id` buries the old boundary key and
    # `rebuild_all` refuses the belief. Event's GRAPH match key is `eventId`,
    # not `id`, so the model must carry both coordinates.
    ("event-state-op-id-change-buries-the-old-id",
     [_m_event(), _m_rename(state={"id": "matrix-moved"}, label="Event"),
      _m_belief()], True),
    # CONTROL: moving `eventId` (the graph's MATCH key) does NOT move the belief
    # key, so the node still carries `id == _MATRIX_ID` and the write folds. A
    # model that keyed the boundary on `eventId` would falsely refuse here.
    ("event-state-op-eventid-change-keeps-the-id",
     [_m_event(),
      _m_rename(state={"eventId": "matrix-moved"}, label="Event"),
      _m_belief()], False),
    # #7903 round-4 FIX 1: an `op="delete"` resolves nodes by Event's GRAPH
    # match key (`eventId`), but the boundary burial is recorded on each
    # resolved node's `id` — and TWO nodes can share one boundary `id`. Here
    # the `eventId`-moving rename leaves the first `:Event` carrying
    # `id == _MATRIX_ID` while a SECOND `:Event` is registered under the same
    # `id`; the delete by the moved `eventId` removes only the first node, so
    # `_MATRIX_ID` stays live and the belief folds. An unconditional burial in
    # the delete arm buried an id a live node still carried, so the reference
    # fold refused a journal `rebuild_all` (and every other engine) accepts —
    # the reported FALSE RED.
    ("event-eventid-delete-keeps-an-id-another-node-carries",
     [_m_event(),
      _m_rename(state={"eventId": "matrix-moved"}, label="Event"),
      _m_event(),
      _m_delete("Event", "matrix-moved"),
      _m_belief()], False),
    # The `:Point` twin — its graph key and belief key are BOTH `id`, so the
    # reference index must re-key as well as the boundary recording the move.
    ("point-state-op-id-change-buries-the-old-id",
     [_m_point(), _m_rename(state={"id": "matrix-moved"}, label="Point"),
      _m_belief()], True),
    # ... and the mirror: the belief lands on the NEW id the node now carries,
    # so both verdicts accept. A model that only buried the old key would false
    # RED this.
    ("point-state-op-id-change-accepts-a-belief-on-the-new-id",
     [_m_point(), _m_rename(state={"id": "matrix-moved"}, label="Point"),
      _m_belief("matrix-moved")], False),
    # #7903 P1b: a PRESENT-but-unwritable identity value. `_hashable` returned
    # False, so the boundary skipped the move and left the OLD id live — while
    # the graph stores the value (FalkorDB degrades it to null) and the old id
    # stops matching. The transition is a DELETE, not a no-op.
    ("state-op-list-id-value-buries-the-old-id",
     [_m_object(_MATRIX_ID, "matrix-listid"),
      _m_rename(state={"id": ["matrix-moved"]}),
      _m_belief()], True),
    ("state-op-dict-id-value-buries-the-old-id",
     [_m_object(_MATRIX_ID, "matrix-dictid"),
      _m_rename(state={"id": {"k": "matrix-moved"}}),
      _m_belief()], True),
    # #7853 round-6 P1 (FIX 1): a PRESENT-but-UNHASHABLE `state.name`. The
    # previous head guarded the Object/Subject MERGE-key move on
    # `_hashable(state["name"])`, so a list/map `name` skipped the move and the
    # simulated node KEPT its old name — while the graph runs `SET n += $state`
    # (FalkorDB degrades the list to null), the old name stops matching, and a
    # later registration of the OLD name creates a NEW node instead of
    # re-id'ing this one. The boundary then buried this id the graph still
    # carries: a false RED. The move must be recorded, `None` marking the
    # unmatchable key.
    ("state-op-list-name-value-moves-the-merge-key",
     [_m_object(_MATRIX_ID, "matrix-listname"),
      _m_rename(state={"name": ["matrix-moved"]}),
      _m_object("matrix-third", "matrix-listname"),
      _m_belief(_MATRIX_ID)], False),
    # The :Subject twin of the same unhashable-`name` false RED.
    ("subject-state-op-list-name-value-moves-the-merge-key",
     [_m_subject(_MATRIX_ID, "matrix-sub-listname"),
      _m_rename(state={"name": ["matrix-moved"]}, label="Subject"),
      _m_subject("matrix-sub-third", "matrix-sub-listname"),
      _m_belief(_MATRIX_ID)], False),
]


@pytest.mark.parametrize(
    "row,events,refused", _BOUNDARY_MATRIX,
    ids=[r[0] for r in _BOUNDARY_MATRIX])
def test_boundary_matrix_reference_and_graph_agree(
        tmp_path, row, events, refused):
    """The deliverable: every ordering gets ONE verdict from both classifiers.

    Reference side — the pure fold AND the diagnostic's own reference fold
    (`consistency._fold_journal`, the arm `check_consistency` runs). Graph side
    — every separately-threaded replay engine: `FalkorProjection.rebuild_all`,
    `FalkorProjection.rebuild(log)`, `consistency.recover_from_log` and
    `backup.restore`. Both reference verdicts and all four graph engines
    must agree, so a future drift in either the exemption predicate or one
    engine's `journal_existence=` threading reds a row here instead of shipping
    a false red/green.
    """
    import copy
    import json
    import shutil

    from tortoise.backup import restore
    from tortoise.consistency import _fold_journal, recover_from_log
    from tortoise.log import EventLog
    from tortoise.projection import FalkorProjection, fold
    from tortoise.projection.nonfolded import (
        NonFoldedEventsError,
        collect_non_folded,
        refused_events,
    )

    for name, folder in (("fold", fold), ("_fold_journal", _fold_journal)):
        with collect_non_folded() as entries:
            # DEEP copy: `_apply_one` stores a `PointAdded`'s nested `point`
            # dict BY REFERENCE (`points[p["id"]] = p`), so a shallow copy
            # lets the first fold mutate the shared record out from under the
            # second — the two reference verdicts would then differ for a
            # reason that has nothing to do with either fold.
            folder(copy.deepcopy(events))
        got = any(_MATRIX_ID in str(e) for e in refused_events(entries))
        assert got == refused, (
            f"[{row}] reference {name}: refused={got}, expected={refused}; "
            f"entries={[str(e) for e in entries]}")

    events_dir = tmp_path / "events-matrix"
    events_dir.mkdir()
    with open(events_dir / "events.jsonl", "w") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")

    def _refused_by(engine: str) -> bool:
        """True when ``engine`` REFUSES this journal.

        EVERY graph replay engine is driven, not just `rebuild_all`: the
        boundary is THREADED into each one separately (`journal_existence=`),
        so a dropped kwarg on a single engine is invisible to the others.
        `recover_from_log` reports refusal as `recovered=False`; `backup.restore`,
        `rebuild(log)` and `rebuild_all` raise `NonFoldedEventsError`. This is
        the P2 (#7936 round-5) hole: the fourth engine's threading had no test
        (mutating `if "journal_existence" in _apply_params:` to `if False:` left
        115 passing). #7903 P3 adds `rebuild(log)` — the apply()-based engine
        with its OWN `journal_existence=` kwarg (`projection/__init__.py:6439`;
        `rebuild` threads it at `:7100`),
        whose loss falsely refuses a forward-referenced belief write.
        """
        tag = engine.replace(".", "_")
        # Each engine's DB gets its OWN directory. The embedded lane
        # auto-recovers a 0-node graph from any `*.jsonl` ADJACENT to the DB
        # (`FalkorProjection._find_local_jsonl_dir`), which would replay the
        # journal through `recover_from_log` during OPEN — before the engine
        # under test runs — and mask the `journal_existence` threading. An
        # empty db dir has no adjacent log.
        db_dir = tmp_path / f"matrix-{tag}-dbdir"
        db_dir.mkdir()
        db = str(db_dir / "tortoise.db")
        if engine == "recover_from_log":
            proj = FalkorProjection(path=db, skip_health_check=True)
            try:
                # It only replays a store it believes was LOST — node-free.
                proj.g.query("MATCH (n) DETACH DELETE n")
                res = recover_from_log(str(events_dir), proj)
                if res["recovered"]:
                    return False
                # `recovered=False` has TWO causes here: the belief boundary
                # refusing (the signal under test, carried as
                # `non_folded_events`) and a journal whose final graph is
                # EMPTY (a delete-only tail) — an orthogonal discriminator,
                # not the boundary. Only the former is a refusal.
                return any(_MATRIX_ID in e
                           for e in res.get("non_folded_events", []))
            finally:
                proj.close()
        if engine == "backup.restore":
            src = tmp_path / f"matrix-{tag}-backup"
            src.mkdir()
            shutil.copy2(events_dir / "events.jsonl", src / "events.jsonl")
            (src / "manifest.json").write_text(
                '{"backed_up_at":"2026-01-01","db":"tortoise.db",'
                '"events":"events.jsonl"}')
            try:
                # No tortoise.db in the backup: the JSONL replay fallback, the
                # arm that threads `journal_existence` into `apply()`. The
                # restore's events copy lives in a DIFFERENT dir from the DB
                # (see above), so it cannot be auto-recovered on open.
                restore_events = tmp_path / f"matrix-{tag}-restore-events"
                restore_events.mkdir()
                restore(str(src), db_path=db,
                        events_path=str(restore_events / "events.jsonl"),
                        into_falkor=True)
            except NonFoldedEventsError:
                return True
            return False
        if engine == "rebuild":
            proj = FalkorProjection(path=db, skip_health_check=True)
            try:
                # The `EventLog` apply() replay — a DIFFERENT threading site
                # from `rebuild_all` (and the one a literal `if False:` on the
                # kwarg would silently starve).
                proj.rebuild(
                    EventLog(str(events_dir / "events.jsonl")),
                    confirm_destructive=True)
            except NonFoldedEventsError:
                return True
            finally:
                proj.close()
            return False
        proj = FalkorProjection(path=db, skip_health_check=True)
        try:
            proj.rebuild_all(str(events_dir), confirm_destructive=True)
        except NonFoldedEventsError:
            return True
        finally:
            proj.close()
        return False

    for engine in ("rebuild_all", "rebuild", "recover_from_log",
                   "backup.restore"):
        graph_refused = _refused_by(engine)
        assert graph_refused == refused, (
            f"[{row}] graph {engine}: refused={graph_refused}, "
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


# ═══════════════════════════════════════════════════════════════════
# 10. `_promote_incident_operators` — the widened endpoint read
# ═══════════════════════════════════════════════════════════════════
#
# #7853 widened this method's endpoint read from `:Point`-only to
# `epistemic_disjunction('s')`, so a non-Point endpoint participates in the
# all-endpoints-live gate. It had no test: reverting the read left the whole
# suite green.

def test_promote_incident_operators_gates_on_non_point_endpoint_status(sdk):
    """A draft operator with a live ``:Point`` AND a draft ``:Event`` endpoint
    must NOT be promoted.

    ``_promote_incident_operators`` reads every endpoint through
    ``epistemic_disjunction('s')`` so the all-live gate sees non-Point
    endpoints. Reverting that read to ``:Point``-only hides the draft Event,
    computes ``all_live`` from the live Point alone, and promotes the operator
    onto a draft endpoint — the exact pollution the draft lifecycle prevents.
    A live-endpoints control pins that the sweep is still reached, so the
    negative assertion cannot pass vacuously (a broken sweep would promote
    nothing at all).
    """
    proj = sdk._get_proj()
    point_id = make_point(sdk, "a live point")
    draft_event = make_labeled_node(sdk, "Event", "evt-draft-7853", "drafty")
    proj.g.query(
        "MATCH (e:Event {id:$id}) SET e.status = 'draft'",
        params={"id": draft_event})

    def _draft_operator(op_id: str, endpoints: tuple[str, str]) -> None:
        proj.g.query(
            "CREATE (o:Point {id:$opid, is_operator:true, op_type:'IMPL', "
            "direction:'bidirectional', status:'draft'})",
            params={"opid": op_id})
        for idx, sid in enumerate(endpoints):
            proj.g.query(
                "MATCH (o:Point {id:$opid}), (s) WHERE s.id = $sid "
                "CREATE (o)-[:IMPL {idx:$idx}]->(s) "
                "CREATE (s)-[:INPUT {idx:$idx}]->(o)",
                params={"opid": op_id, "sid": sid, "idx": idx})

    # Control: every endpoint is live (the Point + an implicit-live Event).
    live_event = make_labeled_node(sdk, "Event", "evt-live-7853", "lively")
    clean_op = f"op-clean-{point_id}"
    _draft_operator(clean_op, (point_id, live_event))
    # Under test: one live Point, one DRAFT Event endpoint.
    mixed_op = f"op-mixed-{point_id}"
    _draft_operator(mixed_op, (point_id, draft_event))

    now = "2026-01-01T00:00:00+00:00"
    promoted = sdk._promote_incident_operators(proj, point_id, now)

    assert clean_op in promoted, promoted
    assert mixed_op not in promoted, promoted
    statuses = dict(proj.g.query(
        "MATCH (o:Point) WHERE o.id IN $ids RETURN o.id, o.status",
        params={"ids": [clean_op, mixed_op]}).result_set)
    assert statuses[clean_op] == "live", statuses
    assert statuses[mixed_op] == "draft", statuses
