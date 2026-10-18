"""#6976 — `expand_relationships()` must not read a connected node as isolated.

`sdk.expand_relationships` is the Tortoise graph brief's mandated READ recipe
(§3, and §8 rule 8 mandates it over ``traverse``). It delegates to
``search_engine.get_relationships``, whose selection had two blind spots:

1. **Operator-less direct edges were invisible.** ``sdk.create_direct_edge`` is
the shape ONTOLOGY v3.5 §8 calls canonical for a plain IMPL/NAND connection
("plain IMPL/NAND connections are direct edges … NO operator node"), and it is
what ``tortoise_list_batch`` reports as a ``direct_edge``. The reader required
an operator hop, so a graph written that way read as having NO relationships —
from both endpoints. Recorded repro: an ``IMPL`` + ``NAND`` pair →
``expand_relationships`` returned ``[]`` on the source and on both targets
while raw Cypher showed the edges.

2. **Both endpoints were narrowed to ``:Point``.** ONTOLOGY §5 (owner ruling
#7813) allows a claim's endpoints to carry any of the four epistemic labels —
``:Point`` / ``:Subject`` / ``:Object`` / ``:Event``; only an OPERATOR stays
``:Point`` — and the #8882 re-typing migration moves exactly those nodes onto
their own layer. ``other.is_operator = false`` compounded it: a non-Point layer
carries no such property, and ``NULL = false`` is NULL. Measured at the base of
the fixing lane, same graph / same run (embedded): a hub that IS an ``:Event``
with 10 edges read as ``[]``; a ``:Point`` hub whose IMPL/hasPart peers were
``:Object`` read as 1 of its 5 relationships.

The tests below are the acceptance gate. Each is discriminating:

* restoring the label-restricted predicate (``(n:Point)`` + ``(other:Point)``)
  reds the hub tests — the ``:Event`` anchor matches nothing;
* restoring ``other.is_operator = false`` reds them for the non-Point peers
  (excluded on the NULL, not on the value) once the labels are widened;
* dropping the direct-edge leg reds `test_direct_edge_hub_is_not_isolated`;
* restoring ``op.is_operator:true`` reds the flagless-operator test;
* returning a placeholder for an isolated node reds the isolated tests.

The ``related_status`` contract (live/superseded/retracted/draft, #689/#898) and
the empty case are pinned alongside, because a widening that bought the peers
but lost the status annotation would be a different silent hole.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.cypher_guard import _predicate_drop_barrier
from tortoise.live import EPISTEMIC_LABELS
from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import get_relationships

TEST_GRAPH = "tortoise_test_6976_expand_relationships"


@pytest.fixture
def sdk(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "expand.db"), namespace=TEST_GRAPH)
    yield sdk
    try:
        sdk.test_guard()
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    sdk.close()


def _graph(sdk):
    return sdk._get_proj().g


def _point(sdk, content):
    return sdk.create_point("statement", content)["id"]


def _object(sdk, name):
    return sdk.create_entity("object", name=name, objectKind="component")["node"]["id"]


def _subject(sdk, name):
    return sdk.create_entity("subject", name=name, subjectKind="team")["node"]["id"]


def _event(sdk, name):
    return sdk.create_entity("event", name=name, eventKind="incident")["node"]["id"]


def _raw_relationships(graph, node_id):
    """The SAME relation `get_relationships` promises, written raw.

    NOTE ON ITS STRENGTH: this is the implementation's relation re-spelled with
    the label narrowing removed — deliberately, since the label question is what
    the hub tests ask. It is therefore NOT independent of the peer/operator
    predicates (a wrong predicate shared by both would be invisible here), which
    is why the direct-edge tests below compare against a one-hop raw query with
    no analogue of the reader's structure, and `test_hub_matches_raw_cypher`
    adds an operator-anchored cross-check.
    """
    rows = graph.query(
        "MATCH (n)-[r:IMPL|NAND|hasPart]-(op:Point)-[r2:IMPL|NAND|hasPart]-(other) "
        "WHERE n.id = $id "
        "  AND (op.is_operator = true OR op.op_type IS NOT NULL) "
        "  AND other.id <> n.id "
        "  AND (other.is_operator IS NULL OR other.is_operator = false) "
        "  AND (other:Point OR other:Subject OR other:Object OR other:Event) "
        "RETURN op.op_type AS mechanism, other.id AS related "
        "UNION ALL "
        "MATCH (n)-[d:IMPL|NAND]-(other) "
        "WHERE n.id = $id "
        "  AND other.id <> n.id "
        "  AND (other.is_operator IS NULL OR other.is_operator = false) "
        "  AND other.op_type IS NULL "
        "  AND (other:Point OR other:Subject OR other:Object OR other:Event) "
        "RETURN type(d) AS mechanism, other.id AS related",
        params={"id": node_id},
    ).result_set
    return [(row[0], row[1]) for row in rows]


def _raw_edge_types(graph, node_id):
    rows = graph.query(
        "MATCH (n)-[r]-(m) WHERE n.id = $id RETURN DISTINCT type(r)",
        params={"id": node_id},
    ).result_set
    return {row[0] for row in rows}


def _expand_set(entries):
    return {(e["mechanism"], e["related_id"]) for e in entries}


# ── The hub: ≥3 relationship types, ≥1 non-Point peer ───────────────────

def _hub_with_non_point_peers(sdk):
    """An INCIDENT ``:Event`` hub wired through IMPL / NAND / composedOf.

    Three operator mechanisms and three peer layers (:Object, :Subject, :Point)
    — the shape the #8882 re-typing migration produces, and the shape the
    graph brief's read recipe is aimed at.
    """
    hub = _event(sdk, "INCIDENT-42 the outage")
    svc_a = _object(sdk, "svc-a the affected service")
    svc_b = _object(sdk, "svc-b the other affected service")
    team = _subject(sdk, "the on-call team")
    cause = _point(sdk, "the outage was caused by a bad deploy")

    sdk.create_operator("IMPL", hub, [svc_a], label="addresses")
    sdk.create_operator("NAND", hub, [cause])
    sdk.create_operator("composedOf", hub, [svc_b, team])
    return hub, {"svc_a": svc_a, "svc_b": svc_b, "team": team, "cause": cause}


def test_hub_matches_raw_cypher(sdk):
    """THE acceptance test: same count AND same (type, related_id) set."""
    hub, peers = _hub_with_non_point_peers(sdk)
    graph = _graph(sdk)

    # The node really is densely connected — this is what raw Cypher reports.
    assert len(_raw_edge_types(graph, hub)) >= 3, "fixture must span ≥3 edge types"

    raw = _raw_relationships(graph, hub)
    entries = sdk.expand_relationships(hub)

    assert len(entries) == len(raw), (
        f"expand_relationships returned {len(entries)} of the {len(raw)} "
        f"relationships raw Cypher reports for the hub: {entries!r}"
    )
    assert _expand_set(entries) == set(raw), (
        "expand_relationships disagrees with raw Cypher on WHICH relationships "
        f"exist — missing {sorted(set(raw) - _expand_set(entries))}, "
        f"extra {sorted(_expand_set(entries) - set(raw))}"
    )
    # every peer layer that is actually wired is represented
    related = {e["related_id"] for e in entries}
    assert set(peers.values()) <= related, (
        f"peers dropped from the expand payload: {set(peers.values()) - related}"
    )
    # INDEPENDENT cross-check, anchored at the OTHER end: every claimed
    # (operator, related) pair must be an edge the operator really has. This
    # cannot pass by construction from either query above — it asks the
    # operator side, not the anchor side.
    op_rows = graph.query(
        "MATCH (op:Point)-[r:IMPL|NAND|hasPart]-(other) "
        "WHERE op.id IN $ops AND other.id IN $related "
        "RETURN DISTINCT op.id, other.id",
        params={"ops": [e["operator_id"] for e in entries if e["operator_id"]],
                "related": sorted(related)},
    ).result_set
    real_pairs = {(row[0], row[1]) for row in op_rows}
    for entry in entries:
        assert (entry["operator_id"], entry["related_id"]) in real_pairs, (
            f"entry is not backed by an edge the operator really has: {entry!r}"
        )


# ── The other half of the defect: operator-less direct edges ────────────

def test_direct_edge_hub_is_not_isolated(sdk):
    """``create_direct_edge`` — ONTOLOGY v3.5 §8's canonical plain connection.

    The reader required an operator hop, so a hub wired with direct IMPL/NAND
    edges read as having NO relationships — from BOTH endpoints. This is the
    issue's recorded repro. Here the payload's (type, related_id) set must equal
    raw Cypher's, because a direct edge IS the relationship (one hop).
    """
    hub = _point(sdk, "INCIDENT-51 known directly to three claims")
    supported = _point(sdk, "the claim this incident supports")
    refuted = _point(sdk, "the claim this incident refutes")
    inbound = _point(sdk, "the claim asserting this incident")
    sdk.create_direct_edge("IMPL", hub, supported, label="supports")
    sdk.create_direct_edge("NAND", hub, refuted, label="refutes")
    sdk.create_direct_edge("IMPL", inbound, hub, label="supports")

    graph = _graph(sdk)
    raw_edges = graph.query(
        "MATCH (n)-[r:IMPL|NAND]-(other) WHERE n.id = $id "
        "RETURN type(r), other.id",
        params={"id": hub},
    ).result_set

    entries = sdk.expand_relationships(hub)
    assert len(entries) == len(raw_edges), (
        f"a direct-edge hub read as {len(entries)} relationships, raw Cypher "
        f"reports {len(raw_edges)}: {entries!r}"
    )
    assert _expand_set(entries) == {(r[0], r[1]) for r in raw_edges}

    # direction is derived from the edge's own orientation, not guessed
    direction = {(e["mechanism"], e["related_id"]): e["direction"] for e in entries}
    assert direction[("IMPL", supported)] == "outgoing"
    assert direction[("NAND", refuted)] == "outgoing"
    assert direction[("IMPL", inbound)] == "incoming"
    # the edge's own label rides the predicate, and there is no operator to name
    assert all(e["operator_id"] == "" for e in entries)
    assert {e["predicate"] for e in entries} == {"supports", "refutes"}


def test_direct_edge_target_is_not_isolated(sdk):
    """The same edge from the TARGET side — the repro's other two nodes."""
    source = _point(sdk, "claim D: the deploy caused the outage")
    target = _point(sdk, "claim E: the deploy was clean")
    sdk.create_direct_edge("NAND", source, target)

    entries = sdk.expand_relationships(target)
    assert _expand_set(entries) == {("NAND", source)}, (
        f"the target of a direct edge read as isolated: {entries!r}"
    )
    assert entries[0]["direction"] == "incoming"


def test_direct_and_operator_edges_coexist(sdk):
    """A hub wired both ways reports both, each once."""
    hub = _point(sdk, "INCIDENT-52 mixed wiring")
    via_op = _point(sdk, "peer reached through an operator")
    direct = _point(sdk, "peer reached by a direct edge")
    sdk.create_operator("IMPL", hub, [via_op])
    sdk.create_direct_edge("NAND", hub, direct)

    entries = sdk.expand_relationships(hub)
    assert _expand_set(entries) == {("IMPL", via_op), ("NAND", direct)}, (
        f"mixed wiring mis-reported: {entries!r}"
    )


def test_non_point_hub_is_not_isolated(sdk):
    """The reported defect: an ``:Event`` hub read as ``[]``.

    Base: ``MATCH (n:Point) WHERE n.id IN $ids`` matched the ``:Event`` anchor
    nowhere, so the seed ``{id: []}`` was returned as the answer.
    """
    hub, peers = _hub_with_non_point_peers(sdk)
    entries = sdk.expand_relationships(hub)
    assert entries != [], (
        "an :Event hub with 4 operator-mediated relationships read as isolated "
        "— the anchor's label predicate excluded it (#6976)"
    )
    assert peers["svc_a"] in {e["related_id"] for e in entries}


def test_non_point_peer_carries_its_own_layer_name(sdk):
    """A widened peer must not arrive empty: kind and text live per layer."""
    hub, peers = _hub_with_non_point_peers(sdk)
    by_related = {e["related_id"]: e for e in sdk.expand_relationships(hub)}
    obj = by_related[peers["svc_a"]]
    assert obj["related_kind"] == "component", (
        "a :Object peer's kind lives on objectKind — reporting '' is the same "
        "hole one layer down"
    )
    assert "svc-a" in obj["related_content"], (
        "a :Object peer's text lives on name — the expand side exists to read "
        f"exactly this, got {obj['related_content']!r}"
    )


def test_operator_without_is_operator_flag_is_visible(sdk):
    """#943 parity: an operator identified by ``op_type`` alone still counts.

    Two shapes, because the predicate has two arms: the flag EXPLICITLY false,
    and — the one legacy/replayed operators actually have — the property ABSENT.
    """
    hub = _point(sdk, "INCIDENT-44 with flagless operators")
    a = _point(sdk, "peer A of incident 44")
    b = _point(sdk, "peer B of incident 44")
    c = _point(sdk, "peer C of incident 44")
    d = _point(sdk, "peer D of incident 44")
    explicit_false = sdk.create_operator("IMPL", hub, [a, b])
    absent = sdk.create_operator("IMPL", hub, [c, d])
    _graph(sdk).query(
        "MATCH (op:Point) WHERE op.id = $oid SET op.is_operator = false",
        params={"oid": explicit_false["id"]},
    )
    _graph(sdk).query(
        "MATCH (op:Point) WHERE op.id = $oid REMOVE op.is_operator",
        params={"oid": absent["id"]},
    )

    related = {e["related_id"] for e in sdk.expand_relationships(hub)}
    assert {a, b} <= related, (
        "an operator carrying op_type with is_operator=false made its "
        "endpoints invisible (#943 parity is what every other reader uses)"
    )
    assert {c, d} <= related, (
        "an operator carrying op_type with NO is_operator property at all made "
        "its endpoints invisible — the ABSENT arm is the one legacy/replayed "
        "operators have"
    )


# ── The contracts the widening must not break ───────────────────────────

def test_isolated_point_still_returns_empty(sdk):
    """The empty case stays an empty LIST — not an error, not a placeholder."""
    lonely = _point(sdk, "an isolated statement with no edges at all")
    assert sdk.expand_relationships(lonely) == []


def test_isolated_non_point_still_returns_empty(sdk):
    """Same for the labels the widening admits — isolation is not label-bound."""
    lonely = _event(sdk, "an isolated incident with no edges")
    assert sdk.expand_relationships(lonely) == []


def test_related_status_survives_the_widening(sdk):
    """#689/#898: the expand payload is not blind to a peer's terminal state.

    The value is the PEER LAYER's own lifecycle status. ``:Point``,
    ``:Object`` and ``:Subject`` all carry ``status``; ``:Event`` carries
    ``eventStatus`` instead and therefore reports ``""`` — pinned here so the
    field's meaning is explicit rather than silently layer-dependent.
    """
    hub = _event(sdk, "INCIDENT-45 the superseded-cause outage")
    live_peer = _point(sdk, "the current explanation of incident 45")
    stale_peer = _point(sdk, "the retracted explanation of incident 45")
    obj_peer = _object(sdk, "svc-c the affected service")
    subj_peer = _subject(sdk, "the on-call team for incident 45")
    event_peer = _event(sdk, "the follow-up incident 46")
    _graph(sdk).query(
        "MATCH (n:Point) WHERE n.id = $id SET n.status = 'superseded'",
        params={"id": stale_peer},
    )
    sdk.create_operator("IMPL", hub, [live_peer, stale_peer, obj_peer])
    sdk.create_direct_edge("NAND", hub, subj_peer)
    sdk.create_direct_edge("IMPL", hub, event_peer)

    statuses = {e["related_id"]: e["related_status"]
                for e in sdk.expand_relationships(hub)}
    assert statuses[live_peer] == "live"
    assert statuses[stale_peer] == "superseded"
    # :Object / :Subject carry a `status` — the lookup must find it under
    # whichever of the four labels the peer actually holds
    assert statuses[obj_peer] == "live"
    assert statuses[subj_peer] == "live"
    # :Event has no `status` (its lifecycle is `eventStatus`): "" is the honest
    # answer, and it is now reached by looking rather than by never asking
    assert statuses[event_peer] == ""


# ── The shape of the emitted Cypher (the #6976 barrier class) ───────────

class _RecordingGraph:
    """Pass-through graph that records every statement sent to the engine."""

    def __init__(self, inner):
        self._inner = inner
        self.cyphers: list[str] = []

    def query(self, cypher, params=None, timeout=None):
        self.cyphers.append(cypher)
        return self._inner.query(cypher, params=params, timeout=timeout)


class _FlakyLabelGraph:
    """Pass-through graph whose query RAISES for one epistemic label."""

    def __init__(self, inner, fail_on: str):
        self._inner = inner
        self._fail_on = f"MATCH (n:{fail_on})"

    def query(self, cypher, params=None, timeout=None):
        if self._fail_on in cypher:
            raise RuntimeError(f"simulated {self._fail_on} leg failure")
        return self._inner.query(cypher, params=params, timeout=timeout)


def test_one_failing_label_does_not_discard_the_other_labels(sdk):
    """The sweep is monotone: a leg that fails can only fail to ADD.

    Four id-anchored statements now back one read. If they share one handler, a
    raise on ANY label throws away the rows the others already returned and the
    answer is the empty seed again — a failure surface this widening CREATED,
    landing on the mandated read path.

    Two anchors on two layers, requested in ONE batch: whichever leg fails, the
    other anchor's rows must survive.
    """
    point_hub = _point(sdk, "INCIDENT-54 the Point-side hub")
    point_peer = _point(sdk, "the Point hub's peer")
    sdk.create_operator("IMPL", point_hub, [point_peer])

    object_hub = _object(sdk, "the Object-side hub")
    object_peer = _point(sdk, "the Object hub's peer")
    sdk.create_operator("IMPL", object_hub, [object_peer])

    anchors = {"Point": (point_hub, point_peer), "Object": (object_hub, object_peer)}
    for failing in EPISTEMIC_LABELS:
        out = get_relationships(
            _FlakyLabelGraph(_graph(sdk), failing),
            [point_hub, object_hub])
        for label, (anchor, peer) in anchors.items():
            if label == failing:
                continue
            assert out[anchor], (
                f"a raise on the :{failing} leg emptied the read for a "
                f":{label} anchor whose own leg succeeded"
            )
            assert peer in {e["related_id"] for e in out[anchor]}


def test_one_failing_label_does_not_blank_every_related_status(sdk):
    """Same isolation for the ``related_status`` sweep in the SDK."""
    hub = _point(sdk, "INCIDENT-55 statuses through a flaky engine")
    peer = _point(sdk, "a peer whose status must survive")
    sdk.create_operator("IMPL", hub, [peer])

    real_g = _graph(sdk)
    flaky = _FlakyLabelGraph(real_g, "Object")

    class _Proj:
        g = flaky

    real_get_proj = sdk._get_proj
    sdk._get_proj = lambda: _Proj()
    try:
        entries = sdk.expand_relationships(hub)
    finally:
        sdk._get_proj = real_get_proj

    by_related = {e["related_id"]: e for e in entries}
    assert peer in by_related, f"the read itself was lost: {entries!r}"
    assert by_related[peer]["related_status"] == "live", (
        "a raise on the :Object status leg blanked the status of a :Point "
        f"peer: {by_related[peer]!r}"
    )


def test_emitted_query_keeps_the_id_predicate_and_the_widening(sdk):
    hub, _ = _hub_with_non_point_peers(sdk)
    recorder = _RecordingGraph(_graph(sdk))
    get_relationships(recorder, [hub])

    assert len(recorder.cyphers) == len(EPISTEMIC_LABELS), (
        "the anchor is id-anchored: one query per epistemic label keeps each "
        f"(label, id) range index, got {len(recorder.cyphers)} queries"
    )
    for label, cypher in zip(EPISTEMIC_LABELS, recorder.cyphers):
        assert f"MATCH (n:{label})" in cypher
        # the load-bearing barrier: a WHERE on n.id directly followed by the
        # expanding MATCH is planned as a whole-graph read on FalkorDB 6.x and
        # the point reads as isolated (#6976 / tests/test_id_predicate_barrier_6976)
        assert "WITH n" in cypher
        assert cypher.index("WITH n") < cypher.index("MATCH (n)-["), (
            "the barrier must come BEFORE the expanding MATCH"
        )
        assert _predicate_drop_barrier(cypher) == cypher, (
            "the emitted statement still holds a predicate-drop shape the "
            "runtime normaliser would have to repair"
        )
        # no :Point-only narrowing on either endpoint of the expansion
        assert "(other:Point)" not in cypher
        assert "is_operator:true" not in cypher
        # ... and the non-operator test admits an ABSENT flag (a :Object /
        # :Subject / :Event carries no is_operator property at all)
        assert "(other.is_operator IS NULL OR other.is_operator = false)" in cypher
        # both limbs are one statement, so the barrier analysis is done once
        assert "UNION ALL" in cypher
        assert "(n)-[d:IMPL|NAND]-(other)" in cypher, (
            "the operator-less direct-edge leg is missing — a graph written the "
            "way ONTOLOGY §8 says plain connections are reads as isolated"
        )


def test_unhashable_peer_text_does_not_empty_the_batch(sdk):
    """A malformed graph property must not turn the whole read into `[]`.

    The per-label rows are deduplicated, and the key rides graph-authored
    free-form properties (``op.op_type``, ``*.label``, the idx columns) — not
    only the row's identity. Any of them arriving as a list (the replayed-JSONL
    diagnostic class the repo already declares for `content`, #2958) must be
    rendered, not raise: an unhashable key raises into the enclosing handler and
    empties the batch for EVERY point — the failure-reads-as-emptiness class this
    very reader had to be fixed for (#6971/#6976).
    """
    hub = _point(sdk, "INCIDENT-53 with malformed neighbours")
    good = _point(sdk, "a well-formed peer of incident 53")
    list_content = _point(sdk, "a peer whose text is a list")
    op = sdk.create_operator("IMPL", hub, [good, list_content], label="addresses")
    _graph(sdk).query(
        "MATCH (n:Point) WHERE n.id = $id SET n.content = ['a', 'b']",
        params={"id": list_content},
    )
    _graph(sdk).query(
        "MATCH (op:Point) WHERE op.id = $oid SET op.label = ['x', 'y']",
        params={"oid": op["id"]},
    )

    entries = sdk.expand_relationships(hub)
    related = {e["related_id"] for e in entries}
    assert {good, list_content} <= related, (
        "a list-valued peer text / operator label emptied the relationship "
        f"read — got {entries!r}"
    )


def test_operator_anchor_does_not_report_its_own_typed_edges_as_direct(sdk):
    """Leg 2 is OPERATOR-LESS edges only — an operator anchor has none.

    Asked about an operator, ``(n)-[d:IMPL|NAND]-(other)`` is the operator's own
    typed edge to each input; reporting it as a *direct* edge would claim
    ``operator_id: ''`` for a relationship whose operator is the anchor itself.
    Both halves of the "is a claim" predicate therefore apply to the anchor too.
    """
    hub = _point(sdk, "a claim with two operator inputs")
    peer_a = _point(sdk, "input A")
    peer_b = _point(sdk, "input B")
    op = sdk.create_operator("IMPL", hub, [peer_a, peer_b], label="addresses")

    entries = sdk.expand_relationships(op["id"])
    assert not [e for e in entries if e["operator_id"] == ""], (
        "the operator's own typed edges were reported as operator-less direct "
        f"edges of the operator itself: {entries!r}"
    )
