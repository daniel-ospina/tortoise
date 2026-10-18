"""#7927 — residual ``:Point``-only epistemic READS outside the EP engine.

Owner ruling #7813: confidence (EP) propagates across :Point / :Subject /
:Object / :Event. #7853 widened the EP engine's reads, #7902 the write path
and the direct-edge reads. The READ surfaces below still hard-coded ``:Point``,
so a :Subject/:Object/:Event belief carried a real EP posterior yet was
invisible on them — the feature was half-shipped.

The six surfaces pinned here:

  1. BFS subgraph selection   — ``analyze._bfs_select_operators``
  2. SDK subgraph selection   — ``sdk._select_subgraph`` (delegates to 1)
  3. contested claims         — ``ep.TortoiseEP.get_contested_claims``
  4. NAND/why ranker surfaces — ``ranking.resolve_contested_relevance``,
                                ``GraphRanker/StateRanker/GapsRanker`` signal reads
  5. audit                    — ``audit.audit_graph`` (SPLIT scope: counts,
                                ``pointKind`` scoping, provenance checks 1-2 and
                                the window check 8 stay :Point-only; the WIRING
                                checks 4/5/6/6b/7 widen their endpoints — see
                                the in-body #7927 block and the docstring test)
  6. SVBP factor extractor    — ``projection.extract_svbp_factors``

Each behavioral test seeds a :Subject/:Object/:Event node (parametrized over
the three labels where the surface allows) and asserts the surface RETURNS it.
Every widening goes through the shared declaration in ``tortoise/live.py``
(``epistemic_sweep`` — which composes ``epistemic_label_queries`` and
``epistemic_labels_of`` — for id-anchored reads, ``epistemic_disjunction`` for
traversal positions) — the source-level tests at the bottom pin that, so no
surface can drift back to a hand-spelled label predicate.

Embedded lane (this file needs no shared graph)::

    TORTOISE_TEST_CARVE_OUT=1 uv run pytest \\
        tests/test_7927_epistemic_read_surfaces.py -v
"""
from __future__ import annotations

import inspect
import os
import shutil
import tempfile

import pytest

from tortoise.analyze import _bfs_select_operators
from tortoise.audit import audit_graph
from tortoise.ep import TortoiseEP
from tortoise.live import EPISTEMIC_LABELS, epistemic_labels_of, epistemic_sweep
from tortoise.ranking import (
    W4_CONTESTED_BOOST,
    GapsRanker,
    GraphRanker,
    StateRanker,
    resolve_contested_relevance,
)
from tortoise.sdk import TortoiseSDK

NON_POINT_LABELS = ["Subject", "Object", "Event"]


class _CountingProjection:
    """Projection proxy that records every Cypher the reads under test issue."""

    def __init__(self, proj):
        self._proj = proj
        self.queries: list[str] = []

    @property
    def g(self):
        return self

    def query(self, cypher, params=None):
        self.queries.append(cypher)
        return self._proj.g.query(cypher, params=params)


@pytest.fixture
def sdk():
    db = os.path.join(tempfile.mkdtemp(prefix="s7927_"), "test.db")
    s = TortoiseSDK(db)
    yield s
    s.close()
    shutil.rmtree(os.path.dirname(db), ignore_errors=True)


def _point(sdk: TortoiseSDK, content: str = "a point claim") -> str:
    return sdk.create_point("statement", content, status="live")["id"]


def _node(sdk: TortoiseSDK, label: str, node_id: str, *,
          name: str | None = None, **props) -> str:
    """Create a bare epistemic node in its PRODUCTION write shape.

    :Subject carries ``(id, name, subjectKind, status)``; :Object carries
    ``(id, name, objectKind, title, status)``; :Event carries ``(id, eventId,
    eventKind, startedAt, name)`` and no ``status``. NONE of them carries
    ``content`` (``create_entity`` rejects belief props) — so a fixture that
    hand-writes ``content`` would hide exactly the defect these tests exist to
    catch: a surface reading a text property the label never stores. Written
    directly (not through the entity API) so the test pins the READ surfaces,
    not the separately-covered write surface.
    """
    text = name if name is not None else node_id
    params = {"id": node_id, "name": text}
    # Defaults are the PRODUCTION shape per label; a caller may override any of
    # them (e.g. ``status='draft'``) without producing a duplicate map key.
    props = dict(props)
    if label == "Object":
        props.setdefault("title", text)
    if label == "Event":
        props.setdefault("eventId", node_id)
        props.setdefault("eventKind", "test")
        props.setdefault("startedAt", "2026-01-01T00:00:00Z")
    else:
        props.setdefault("status", "live")
    assign = ", ".join(f"{k}:${k}" for k in props)
    params.update(props)
    sdk._get_proj().g.query(
        f"CREATE (n:{label} {{id:$id, name:$name, {assign}}})", params=params
    )
    return node_id


# ═══════════════════════════════════════════════════════════════════════
# Perf contract — the widening must not cost 4× on a Point-only read
# ═══════════════════════════════════════════════════════════════════════

def test_present_label_resolution_keeps_a_point_only_read_cheap(sdk):
    """``live.epistemic_labels_of`` narrows the sweep to the labels actually
    present: a Point-only id set resolves in ONE query (not four — the
    unconditional sweep took the #395 AC5 zone from ~0.73s to ~1.50s), a mixed
    set resolves only the labels it contains, and an id matching no epistemic
    label still runs the Point leg (the pre-widening shape)."""
    p = _point(sdk)
    n = _node(sdk, "Subject", "resolve-1")
    counter = _CountingProjection(sdk._get_proj())

    assert epistemic_labels_of(counter, [p]) == ["Point"]
    assert len(counter.queries) == 1, counter.queries

    counter.queries.clear()
    assert set(epistemic_labels_of(counter, [p, n])) == {"Point", "Subject"}
    assert len(counter.queries) == 2, counter.queries

    counter.queries.clear()
    assert epistemic_labels_of(counter, ["no-such-epistemic-node"]) == ["Point"]
    assert len(counter.queries) == len(EPISTEMIC_LABELS), counter.queries


def test_sweep_runs_the_anchored_query_once_per_present_label(sdk):
    """``live.epistemic_sweep`` is the ONE implementation every id-anchored
    surface uses (analyze BFS, every ranker signal read) — it composes
    ``epistemic_label_queries`` with ``epistemic_labels_of`` rather than
    restating the loop per module."""
    p = _point(sdk)
    n = _node(sdk, "Subject", "sweep-1")
    counter = _CountingProjection(sdk._get_proj())
    template = "MATCH (n:{label}) WHERE n.id IN $ids RETURN n.id"

    assert epistemic_sweep(counter, template, {"ids": [p]}) == [[p]]
    assert len(counter.queries) == 2, counter.queries  # resolution + 1 anchored

    counter.queries.clear()
    assert epistemic_sweep(counter, template, {"ids": [p, n]}) == [[p], [n]]
    assert len(counter.queries) == 4, counter.queries  # 2 resolution + 2 anchored


def test_ranker_read_costs_one_anchored_query_for_point_ids(sdk):
    """The ranker sweep is resolution + ONE anchored read for a Point-only id
    set — the recall/rerank latency contract, pinned so the widening cannot
    quietly become four reads per result set."""
    p = _point(sdk)
    counter = _CountingProjection(sdk._get_proj())
    GraphRanker(projection=counter)._fetch_point_signals([p])
    assert len(counter.queries) == 2, counter.queries


def test_gaps_rerank_resolves_labels_once_for_all_three_sweeps(sdk):
    """The #395 latency contract is about the WHOLE rerank, not one sweep.

    ``GapsRanker.rerank`` issues three id-anchored sweeps over the SAME id set,
    so resolving the present labels inside each one paid the resolution three
    times — measured: 6 round-trips for a Point-only rerank where the
    pre-widening path took 3, three of them the byte-identical
    ``MATCH (n:Point) WHERE n.id IN $ids RETURN n.id``.

    ONE resolution (1) + three anchored reads (3) = 4. Pinned as a TOTAL,
    because a per-sweep assertion structurally cannot see the multiplication
    this test exists to catch.
    """
    p = _point(sdk)
    counter = _CountingProjection(sdk._get_proj())
    GapsRanker(projection=counter).rerank([{"id": p, "content": "a claim"}])
    resolutions = [q for q in counter.queries
                   if q.strip().endswith("WHERE n.id IN $ids RETURN n.id")]
    assert len(resolutions) == 1, (
        f"the present-label resolution ran {len(resolutions)} times for one "
        f"rerank: {resolutions}")
    assert len(counter.queries) == 4, counter.queries


# ═══════════════════════════════════════════════════════════════════════
# 1. BFS subgraph selection — analyze._bfs_select_operators
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("label", NON_POINT_LABELS)
def test_bfs_selects_operator_reached_from_non_point_anchor(sdk, label):
    """Anchor = the non-Point belief. Two things must widen at once:

    * the incoming leg (``p.id IN $frontier`` — id-anchored, label-swept), or
      the operator is never reached from a non-Point anchor;
    * ``_derived_live_operators``' connectivity count (traversal position),
      or the operator looks INERT: it has 2 live connected points but only
      the Point one is counted.
    """
    p = _point(sdk)
    n = _node(sdk, label, f"anchor-{label.lower()}")
    sdk.create_operator("IMPL", p, [n])

    ops, _ = _bfs_select_operators(sdk._get_proj(), [n], max_hops=1)
    assert ops, f"operator over a :{label} input was not selected from a :{label} anchor"


@pytest.mark.parametrize("label", NON_POINT_LABELS)
def test_bfs_walks_direct_edge_touching_non_point_endpoints(sdk, label):
    """Operator-LESS direct edges: BOTH endpoints may be non-Point, and both
    traversal directions must find the edge (anchor = source exercises the
    forward leg, anchor = target the backward leg — each sweeps a different
    id-anchored endpoint and disjoins a different traversal position)."""
    other = "Object" if label != "Object" else "Subject"
    src = _node(sdk, label, f"de-src-{label.lower()}")
    tgt = _node(sdk, other, f"de-tgt-{label.lower()}")
    sdk.create_direct_edge("IMPL", src, tgt)

    proj = sdk._get_proj()
    fwd_ops, fwd_anchors = _bfs_select_operators(proj, [src], max_hops=1)
    assert (src, tgt, "IMPL") in fwd_anchors, fwd_anchors
    assert fwd_ops == set()  # direct-edge-only subgraph: zero operators

    bwd_ops, bwd_anchors = _bfs_select_operators(proj, [tgt], max_hops=1)
    assert (src, tgt, "IMPL") in bwd_anchors, bwd_anchors
    assert bwd_ops == set()


def test_bfs_draft_filter_sees_a_draft_non_point_anchor(sdk):
    """The live-only draft probe is id-anchored — a draft :Subject anchor must
    be excluded exactly like a draft Point anchor (otherwise the filter is a
    silent no-op for the three labels it cannot see).

    CARRIES ITS OWN CONTRAST, deliberately. ``assert ops == set()`` ALONE IS
    VACUOUS — it holds in all three states, so it pins nothing:

    * on the pre-widening code every leg is ``:Point``-anchored, so the
      ``:Subject`` anchor reaches no row at all;
    * with the probe REVERTED it is empty again for a second reason —
      ``_derived_live_operators`` needs >= 2 LIVE connected points and counts a
      draft neighbour as 0, so the operator is EP-inert;
    * as shipped, empty for both reasons.

    Two re-assertions supply the contrast: a LIVE ``:Subject`` anchor with the
    same fixture must SELECT the operator (that is what fails on the
    pre-widening code, and what makes an empty set mean "excluded AS DRAFT"),
    and the probe must actually LOOK at the anchor's own label, read off the
    issued Cypher — an exclusion produced by never asking cannot be told apart
    from one the filter produced.

    ``include_draft=True`` is deliberately NOT asserted: derived-liveness
    ignores the draft escape hatch by design, so a draft-anchored operator
    stays EP-inert even with drafts admitted. The label sweep is the only
    honest discriminator.
    """
    # Live control: the identical fixture, live, must be selectable.
    p_live = _point(sdk, "live control claim")
    n_live = _node(sdk, "Subject", "live-anchor", status="live")
    sdk.create_operator("IMPL", p_live, [n_live])
    live_ops, _ = _bfs_select_operators(sdk._get_proj(), [n_live], max_hops=1)
    assert live_ops, ("the live control itself is unreachable — the assertion "
                      "below would be vacuous")

    # Draft form: the probe must SEE a draft :Subject (it sweeps the labels
    # present in the anchor set) and the anchor must be excluded.
    p = _point(sdk)
    n = _node(sdk, "Subject", "draft-anchor", status="draft")
    sdk.create_operator("IMPL", p, [n])

    counter = _CountingProjection(sdk._get_proj())
    ops, _ = _bfs_select_operators(counter, [n], max_hops=1)
    assert ops == set(), "a draft :Subject anchor was treated as live"
    probe = [q for q in counter.queries
             if "n.status = 'draft'" in q and "n.id IN $ids" in q]
    assert any("(n:Subject)" in q for q in probe), (
        "the draft probe never looked at the anchor's own label — the "
        f"exclusion came from not asking, not from the filter: {probe!r}")
    # ISSUED is not APPLIED. With the frontier prune removed (as opposed to the
    # probe reverted) the probe still runs, still sees the draft, and `ops` is
    # STILL empty — the BFS then reaches the operator and `_derived_live_
    # operators` drops it (a draft neighbour counts as 0 live, so live_conn is
    # 1 < 2). The only observable difference is that the BFS legs then run at
    # all, and every one of them is anchored on `$frontier`:
    assert not [q for q in counter.queries if "$frontier" in q], (
        "the draft anchor never left the frontier — the probe ran but its "
        f"result was not applied: {counter.queries!r}")


# ═══════════════════════════════════════════════════════════════════════
# 2. SDK subgraph selection — sdk._select_subgraph (delegates to the BFS)
# ═══════════════════════════════════════════════════════════════════════

def test_sdk_select_subgraph_admits_every_non_point_anchor(sdk):
    """The SDK surface returns what the shared selector selects, for all
    three non-Point labels in one call."""
    anchors = []
    for label in NON_POINT_LABELS:
        p = _point(sdk, f"claim for {label}")
        n = _node(sdk, label, f"sdk-{label.lower()}")
        sdk.create_operator("IMPL", p, [n])
        anchors.append(n)

    ops, _ = sdk._select_subgraph(anchors, max_hops=1)
    assert len(ops) == len(NON_POINT_LABELS), ops


# ═══════════════════════════════════════════════════════════════════════
# 3. Contested claims — ep.get_contested_claims
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("label", NON_POINT_LABELS)
def test_contested_claims_lists_non_point_belief(sdk, label):
    """Beta(2,2) variance is 0.05 > the 0.04 bar: a MEASURED, wide belief of
    any epistemic label is contested. Three independent narrowings hid it — the
    ``:Point`` scan label, ``n.is_operator = false`` (a non-Point has no
    ``is_operator``, so the predicate dropped it by NULL), and the text read
    (``n.content`` only — entities store ``name``)."""
    n = _node(sdk, label, f"contested-{label.lower()}",
              name=f"a wide {label} belief",
              posterior_alpha=2.0, posterior_beta=2.0)

    listed = {c["id"]: c for c in sdk._get_ep().get_contested_claims()}
    assert n in listed, f":{label} belief with variance 0.05 was not listed"
    assert listed[n]["variance"] > 0.04
    assert listed[n]["content"] == f"a wide {label} belief", listed[n]


def test_contested_claims_does_not_flood_the_report_with_unmeasured_entities(sdk):
    """The unmeasured-MUST-list rule is the CLAIM rule (:169 pin) and stays for
    ``:Point`` — but an entity has no persisted prior unless EP measured it
    (``create_entity`` rejects belief props), and Beta(1,1) sits at the MAXIMUM
    variance 1/12. Listing those would put every Subject/Object/Event on the
    graph permanently at the top of a report that exists to discriminate, and
    nothing would ever measure them. So a MEASURED entity lists, an unmeasured
    one does not — while an unmeasured Point still does."""
    sdk._get_proj().g.query("CREATE (p:Point {id:'unmeasured-point', status:'live', content:'unmeasured claim'})")
    for label in NON_POINT_LABELS:
        _node(sdk, label, f"unmeasured-{label.lower()}")

    ids = {c["id"] for c in sdk._get_ep().get_contested_claims()}
    assert "unmeasured-point" in ids, "the :169 claim pin (unmeasured lists) regressed"
    for label in NON_POINT_LABELS:
        assert f"unmeasured-{label.lower()}" not in ids, (
            f"an unmeasured :{label} flooded the contested report")


def test_contested_claims_never_lists_a_legacy_op_type_only_operator(sdk):
    """The non-operator predicate must be the CANONICAL one — both halves.

    A legacy operator (#2205/#943) carries ``op_type`` and NO ``is_operator``.
    Admitting UNSET (which the entity labels need) re-admitted exactly that
    class to a report about CLAIMS, and ``:Point`` has no measured gate, so it
    coalesces to Beta(1,1) (v = 1/12) and sorts to the top. Before the widening
    the bare ``= false`` excluded it — by NULL, but it excluded it.
    """
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:'legacy-op', op_type:'NAND', label:'contra', "
        "content:'an operator label', status:'live', "
        "posterior_alpha:2.0, posterior_beta:2.0})")
    assert "legacy-op" not in {c["id"] for c in sdk._get_ep().get_contested_claims()}


def test_contested_claims_identifies_an_event_without_an_id_property(sdk):
    """``:Event`` writers key on ``eventId`` (some stamp ``id`` too, some do
    not). Keying the merged set on ``n.id`` collapsed every id-less Event onto a
    ``None`` key — the first won and the rest were silently DROPPED, and the
    survivor was unaddressable. The row identity is ``coalesce(id, eventId)``.
    """
    proj = sdk._get_proj()
    for eid, name in (("ev-a", "alpha event"), ("ev-b", "beta event")):
        proj.g.query(
            "CREATE (e:Event {eventId:$eid, eventKind:'test', name:$n, "
            "startedAt:'2026-01-01T00:00:00Z', "
            "posterior_alpha:2.0, posterior_beta:2.0})",
            params={"eid": eid, "n": name})

    listed = {c["id"]: c for c in sdk._get_ep().get_contested_claims()}
    assert {"ev-a", "ev-b"} <= set(listed), f"id-less Events dropped: {listed}"
    assert listed["ev-b"]["content"] == "beta event"


def test_contested_claims_lists_a_multi_labelled_node_once(sdk):
    """``:Point:Object`` is a real minted shape (``update_point``'s promote
    arm). Both label sweeps return it, so the merged set must collapse on id —
    before the collapse the same node listed twice, which no consumer expects."""
    sdk._get_proj().g.query(
        "CREATE (n:Point:Object {id:'multi-1', status:'live', "
        "content:'a promoted claim', name:'a promoted claim', "
        "posterior_alpha:2.0, posterior_beta:2.0})")

    rows = [c for c in sdk._get_ep().get_contested_claims() if c["id"] == "multi-1"]
    assert len(rows) == 1, f"multi-labelled node listed {len(rows)} times"


def test_contested_claims_still_lists_points_and_orders_by_variance(sdk):
    """The Point half is unchanged and the merged sweep keeps ONE ordering:
    variance-descending (the per-label ORDER BY cannot order the merged set,
    so the Python sort is the contract)."""
    wide = _node(sdk, "Subject", "c-wide", posterior_alpha=1.0, posterior_beta=1.0)
    tighter = _point(sdk, "a tighter measured claim")
    # Beta(2,1) variance 0.0556 — above the 0.04 bar, below Beta(1,1)'s 0.0833.
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.posterior_alpha = 2.0, n.posterior_beta = 1.0",
        params={"id": tighter},
    )

    rows = sdk._get_ep().get_contested_claims()
    ids = [r["id"] for r in rows]
    assert wide in ids and tighter in ids
    variances = [float(r["variance"]) for r in rows]
    assert variances == sorted(variances, reverse=True), variances
    assert ids.index(wide) < ids.index(tighter)


# ═══════════════════════════════════════════════════════════════════════
# 4. NAND/why ranker surfaces — ranking.py
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("label", NON_POINT_LABELS)
def test_ranker_signal_reads_return_non_point_belief(sdk, label):
    """A ranked result may BE the non-Point belief: every ranker signal read is
    id-anchored on the result id, so all three rankers must see it. Before the
    sweep the row was absent -> the belief ranked as unmeasured/neutral while
    every other surface read its 0.75."""
    n = _node(sdk, label, f"ranked-{label.lower()}",
              posterior_alpha=3.0, posterior_beta=1.0)
    proj = sdk._get_proj()

    graph = GraphRanker(projection=proj)._fetch_point_signals([n])
    assert n in graph, f"GraphRanker saw no :{label} belief"
    assert graph[n]["confidence"] == pytest.approx(0.75)

    state = StateRanker(projection=proj)._fetch_point_signals([n])
    assert n in state, f"StateRanker saw no :{label} belief"
    assert state[n]["confidence"] == pytest.approx(0.75)
    assert state[n]["has_ep"] is True

    gaps = GapsRanker(projection=proj)._fetch_confidence_signals([n])
    assert n in gaps, f"GapsRanker saw no :{label} belief"
    assert gaps[n]["confidence"] == pytest.approx(0.75)
    assert gaps[n]["has_ep"] is True


@pytest.mark.parametrize("label", NON_POINT_LABELS)
def test_ranker_load_and_support_reads_return_non_point_belief(sdk, label):
    """Outgoing load / incoming support ride the same id-anchored reads; their
    TRAVERSAL positions (the neighbour claim) are widened too, so a direct
    IMPL edge from a non-Point neighbour counts as support."""
    n = _node(sdk, label, f"gaps-{label.lower()}")
    neighbour = _node(sdk, "Subject", f"neighbour-{label.lower()}")
    sdk.create_direct_edge("IMPL", neighbour, n)

    proj = sdk._get_proj()
    load = GapsRanker(projection=proj)._fetch_load_signals([neighbour])
    assert neighbour in load
    support = GapsRanker(projection=proj)._fetch_support_signals([n])
    assert n in support, f"GapsRanker support read saw no :{label} belief"
    assert support[n]["incoming_impl"] == 1


def test_ranker_support_ignores_a_legacy_op_type_only_operator(sdk):
    """Same canonical-predicate rule on the ranker side: an operator's own edge
    must not count as DIRECT support for a claim (the fail-open #6871 class).
    A legacy ``op_type``-only operator has no ``is_operator``, so the UNSET
    admission alone would count it."""
    target = _point(sdk, "a supported claim")
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:'legacy-support', op_type:'IMPL', "
        "content:'an operator', status:'live'})")
    sdk._get_proj().g.query(
        "MATCH (s:Point {id:'legacy-support'}), (t:Point {id:$t}) "
        "MERGE (s)-[:IMPL]->(t)", params={"t": target})

    signals = GapsRanker(projection=sdk._get_proj())._fetch_support_signals([target])
    # ``incoming_impl`` = operator-mediated (r3) + DIRECT (r4). The legacy
    # operator's own edge is neither, so both columns must be 0.
    assert signals[target]["incoming_impl"] == 0, signals[target]


def test_object_signals_are_measured_by_a_non_point_about_edge(sdk):
    """``StateRanker._fetch_object_signals`` is the one widened read whose
    result is AGGREGATED: ``pids`` feeds ``_fetch_point_signals`` and the
    Object's reported confidence is the MEAN posterior of the beliefs about it
    (``ranking.py``'s ``mean = sum(confs)/len(confs)``). So a regression here
    does not drop a row that a caller would notice — it quietly moves a ranking
    number. That is why it needs its own case rather than riding on the id-level
    ones: a ``:Subject`` that is ``about*``-linked to an ``:Object`` must be
    collected into ``pids`` and must MOVE the mean."""
    obj = _node(sdk, "Object", "about-target-object")
    wide = _node(sdk, "Subject", "about-source-subject",
                 posterior_alpha=3.0, posterior_beta=1.0)   # mean 0.75
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (p {id:$p}), (o {id:$o}) MERGE (p)-[a:aboutObject]->(o)",
        params={"p": wide, "o": obj})

    sigs = StateRanker(projection=proj)._fetch_object_signals([obj])
    assert obj in sigs, "the Object was not read at all"
    assert sigs[obj]["degree"] == 1, sigs[obj]
    assert sigs[obj]["has_ep"] is True, (
        "the non-Point belief about the Object was not measured — the read "
        f"still only collects Points: {sigs[obj]}")
    assert sigs[obj]["confidence"] == pytest.approx(0.75), (
        f"the about-belief did not move the Object's mean: {sigs[obj]}")


def test_object_signals_read_uses_the_shared_traversal_disjunction():
    """Source pin for the aggregation read. It cannot join the id-anchored
    ``epistemic_sweep`` parametrization above: the Object anchor is
    ``:Object`` by construction (the caller routes ``entity_type == 'object'``)
    and the widened position is the TRAVERSAL ``p``, so the correct assertion is
    a disjunction, not a sweep."""
    src = inspect.getsource(StateRanker._fetch_object_signals)
    assert "epistemic_disjunction('p')" in src
    assert "(p:Point)" not in src, "the about-Point leg is hand-spelled again"
    assert "MATCH (o:Object) WHERE o.id IN $ids" in src, (
        "the Object anchor must keep its own label (per-label index)")


def test_contested_relevance_reads_non_point_target_and_counter_claim(sdk):
    """The W4 relevance read: the CONTESTED node is id-anchored (swept) and the
    NANDer ``c`` is a traversal position (disjunction). A :Subject counter-claim
    against an :Object belief must produce the boost — before the widening the
    row did not exist at all."""
    target = _node(sdk, "Object", "w4-target")
    counter = _node(sdk, "Subject", "w4-counter",
                    name="the cache rollback is the safer default")
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (a {id:$a}), (b {id:$b}) MERGE (a)-[r:NAND]->(b) SET r.idx = 1",
        params={"a": counter, "b": target},
    )

    # The counter-claim stores its text in ``name`` (production shape), so the
    # read must resolve it there — the old ``content``-only chain fell through
    # to ``c.label`` and the boost never fired for an entity conflict.
    boosts = resolve_contested_relevance(proj, [target], "cache rollback")
    assert boosts == {target: W4_CONTESTED_BOOST}


def test_bfs_frontier_never_holds_an_identity_less_anchor(sdk):
    """A widened traversal that RETURNS ``.id`` must not hand a ``None`` to the
    frontier.

    ``session_indexer`` mints ``(e:Event {eventId:…})`` with no ``id`` at all.
    Widening the traversal to admit it and then returning ``target.id`` puts
    ``None`` into ``new_points`` / ``factor_anchors`` / ``compute_confidence``'s
    seeds — a node the next id-keyed hop can never re-anchor. ``coalesce(id,
    eventId)`` is NOT the fix there (it would re-anchor on a value that is not an
    ``id``, #8538), so the leg requires ``.id`` and the population stays exactly
    what the pre-widening label predicate admitted.
    """
    proj = sdk._get_proj()
    anchor = _point(sdk, "an anchor claim")
    proj.g.query("CREATE (e:Event {eventId:'bfs-keyless', eventKind:'test', "
                 "name:'keyless endpoint'})")
    # A DIRECT edge (operator-less, A9 §5.6): the traversal walks from the
    # anchored Point straight to the keyless Event, which is exactly the leg
    # whose ``(src, tgt, type)`` anchor tuple would carry the ``None``.
    proj.g.query(
        "MATCH (a:Point {id:$a}), (e:Event {eventId:'bfs-keyless'}) "
        "MERGE (a)-[:NAND]->(e)", params={"a": anchor})

    ops, anchors = _bfs_select_operators(proj, [anchor], max_hops=2)
    assert None not in ops, f"a None operator id reached the selection: {ops}"
    assert all(
        all(part is not None for part in anchor_tuple)
        for anchor_tuple in anchors), (
        f"a None reached a factor anchor: {anchors}")


def test_object_signals_do_not_count_a_belief_they_cannot_address(sdk):
    """``_fetch_object_signals`` returns ``pids`` into an ID-ANCHORED Point
    read, so counting an edge whose belief has no ``id`` reported a measured
    Object as unmeasured with a phantom degree (measured: ``degree 1,
    has_ep False``). The window for an Object whose ONLY about-belief is a
    keyless Event must be internally consistent: the belief is either read or
    not counted."""
    obj = _node(sdk, "Object", "phantom-target")
    proj = sdk._get_proj()
    proj.g.query("CREATE (e:Event {eventId:'phantom-belief', eventKind:'test', "
                 "name:'an unaddressable belief', posterior_alpha:9.0, "
                 "posterior_beta:1.0})")
    proj.g.query(
        "MATCH (o:Object {id:$o}), (e:Event {eventId:'phantom-belief'}) "
        "MERGE (e)-[:aboutObject]->(o)", params={"o": obj})

    sig = StateRanker(projection=proj)._fetch_object_signals([obj])[obj]
    assert not (sig["degree"] > 0 and sig["has_ep"] is False), (
        "a belief the read cannot address was counted as a degree while its "
        f"posterior vanished: {sig}")


def test_contested_relevance_never_boosts_a_point_from_its_own_text(sdk):
    """The self-reference guard must survive the identity WIDENING.

    ``src`` is an OPTIONAL match, so the guard skips the comparison only when
    it has no identity at all. Widening it to the two-key form with ``OR``
    across the keys kills the guard for every ordinary source: a :Point carries
    ``id`` and no ``eventId``, so ``src.eventId IS NULL`` is TRUE and the row is
    admitted — and ``create_operator('NAND', p, [p])`` is ACCEPTED, so the point
    boosts itself by overlapping its own content. Measured before this test:
    ``{'self-x': 0.05}`` where the pre-widening read returned ``{}``.
    """
    proj = sdk._get_proj()
    pid = _point(sdk, "cache rollback is the safer default")
    # input == target == the contested point: the self-referential NAND shape.
    op = sdk.create_operator("NAND", pid, [pid])["id"]
    proj.g.query(
        "MATCH (op:Point {id:$op})-[r:NAND]->(n) SET r.idx = 1", params={"op": op})
    proj.g.query(
        "MATCH (op:Point {id:$op})-[ri:INPUT]->(n) SET ri.idx = 0", params={"op": op})

    boosts = resolve_contested_relevance(proj, [pid], "cache rollback")
    assert boosts == {}, (
        "the point was boosted by its OWN text — the self-reference guard is "
        f"dead for a source that carries one key and not the other: {boosts}")


def test_contested_relevance_reads_non_point_nand_input_source(sdk):
    """The NAND operator's idx-0 counter-argument (``src``) is its own
    traversal position: a :Subject source must be readable, or the row is
    dropped and the operator's conflict goes silent."""
    target = _node(sdk, "Subject", "w4-op-target")
    source = _node(sdk, "Subject", "w4-op-source",
                   name="flocking zebra finch behavior is social")
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (op:Point {id:'w4-op', is_operator:true, op_type:'NAND', "
        "label:'contra', status:'live'})"
    )
    proj.g.query(
        "MATCH (op:Point {id:'w4-op'}), (s {id:$s}) "
        "MERGE (s)-[ri:INPUT]->(op) SET ri.idx = 0",
        params={"s": source},
    )
    proj.g.query(
        "MATCH (op:Point {id:'w4-op'}), (t {id:$t}) "
        "MERGE (op)-[r:NAND]->(t) SET r.idx = 1",
        params={"t": target},
    )

    boosts = resolve_contested_relevance(proj, [target], "zebra finch social behavior")
    assert boosts == {target: W4_CONTESTED_BOOST}


# ═══════════════════════════════════════════════════════════════════════
# 5. Audit — deliberately SPLIT: counts/provenance :Point-only, wiring
#    checks widened (#7927 decision)
# ═══════════════════════════════════════════════════════════════════════

def test_audit_counts_stay_point_only_by_decision(sdk):
    """The audit's COUNTS and PROVENANCE checks are a report over the Point
    wiring universe: scoped by ``pointKind`` (a Point-only property) and
    auditing point-level ``sourceKind``/``sourceDate`` annotations that no
    other label carries. Admitting the four labels THERE would flag every
    Subject/Object/Event as a legacy provenance gap — a false positive
    produced by the widening itself. So that half stays :Point-only and must
    not silently change either way."""
    _point(sdk, "an audited claim")
    _node(sdk, "Subject", "not-audited-subject")
    _node(sdk, "Object", "not-audited-object")
    _node(sdk, "Event", "not-audited-event")

    result = audit_graph(sdk._get_proj())
    assert result.node_count == 1, (
        "audit node_count must count only in-scope Points; a changed count "
        "means the documented :Point-only decision for counts was reversed")
    flagged = {i.node_id for i in result.issues}
    assert not flagged & {"not-audited-subject", "not-audited-object",
                          "not-audited-event"}


def test_audit_mitigation_check_sees_a_non_point_endpoint(sdk):
    """The WIRING half is widened (#7927): checks 4/5/6/6b/7 are INTEGRITY
    checks over operator endpoints, and #7902 made a :Subject/:Object/:Event a
    legal endpoint. Left :Point-only they reported a false CLEAN — an integrity
    result indistinguishable from a true one (#6146). Here: a NAND operator at
    the mitigation threshold whose only target is a :Subject must be reported.
    """
    subject = _node(sdk, "Subject", "audit-subject-endpoint",
                    name="a subject belief")
    # A second live Point so the operator is not itself degenerate, and the
    # operator NANDs the :Subject belief with no mitigated_by edge.
    other = _point(sdk, "a supporting claim")
    op_id = sdk.create_operator("NAND", other, [subject])["id"]
    sdk._get_proj().g.query(
        "MATCH (op:Point {id:$op}) SET op.confidence = 0.2",
        params={"op": op_id})

    result = audit_graph(sdk._get_proj())
    reported = [i for i in result.issues
                if i.issue_type == "mitigation_recommended"]
    assert reported, (
        "a low-confidence NAND operator targeting a :Subject reported a false "
        f"CLEAN: {result.check_counts}")
    assert any("a subject belief" in i.detail for i in reported), (
        "the :Subject endpoint was counted but its text never resolved — a "
        f"content-only read: {[i.detail for i in reported]}")


# ═══════════════════════════════════════════════════════════════════════
# 6. SVBP factor extractor — projection.extract_svbp_factors (deprecated)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("label", NON_POINT_LABELS)
def test_svbp_extractor_includes_non_point_operator_input(sdk, label):
    """Deprecated but still CALLED (graph-scripts profiling + the EP draft-filter
    tests), so it is widened rather than deleted — retirement is deferred. While
    it exists it must agree with the engine about which beliefs feed a factor."""
    p = _point(sdk, "SVBP point input")
    n = _node(sdk, label, f"svbp-{label.lower()}")
    sdk.create_operator("IMPL", p, [n])

    factors, _evidence = sdk._get_proj().extract_svbp_factors()
    assert factors, "no factor extracted for the operator"
    inputs = factors[0][2]
    assert n in inputs, f":{label} belief missing from the factor inputs: {inputs}"
    assert p in inputs


# ═══════════════════════════════════════════════════════════════════════
# Indicator 3 — the surfaces go THROUGH tortoise/live.py, never a
# hand-spelled label predicate. Source-level pins (read the function body).
# ═══════════════════════════════════════════════════════════════════════

def test_bfs_selector_uses_the_shared_label_helpers():
    src = inspect.getsource(_bfs_select_operators)
    # The sweep itself is live.epistemic_sweep (which composes
    # epistemic_label_queries + epistemic_labels_of) — one implementation for
    # every id-anchored surface, never a private copy per module.
    assert "epistemic_sweep(" in src, "id-anchored legs must sweep live.EPISTEMIC_LABELS"
    assert "epistemic_disjunction(" in src, "traversal positions must disjoin live.EPISTEMIC_LABELS"
    for hand_spelled in (
        "MATCH (n:Point) WHERE n.id IN $ids",
        "(t:Point)",
        "(p:Point)",
        "(target:Point)",
        "(a:Point)-[r:",
    ):
        assert hand_spelled not in src, f"hand-spelled Point predicate survived: {hand_spelled}"


def test_contested_claims_uses_the_shared_label_helper():
    src = inspect.getsource(TortoiseEP.get_contested_claims)
    assert "EPISTEMIC_LABELS" in src
    assert "MATCH (n:Point)" not in src


@pytest.mark.parametrize("fn", [
    GraphRanker._fetch_point_signals,
    StateRanker._fetch_point_signals,
    GapsRanker._fetch_load_signals,
    GapsRanker._fetch_support_signals,
    GapsRanker._fetch_confidence_signals,
])
def test_ranker_signal_reads_use_the_shared_label_helper(fn):
    src = inspect.getsource(fn)
    assert "epistemic_sweep(" in src, f"{fn.__qualname__} bypasses the shared sweep"
    assert "MATCH (n:Point) WHERE n.id IN $ids" not in src


def test_contested_relevance_uses_the_shared_label_helpers():
    src = inspect.getsource(resolve_contested_relevance)
    assert "epistemic_sweep(" in src
    assert "epistemic_disjunction(" in src
    assert "MATCH (n:Point) WHERE n.id IN $ids" not in src


def test_audit_records_its_split_scope_in_reachable_code():
    """The per-check scope decision must be FINDABLE, not inferred: it lives in
    ``audit_graph``'s own body, next to the predicates it governs, and states
    BOTH halves — what stays :Point-only and what was widened, each with its
    reason. A one-sided note is how the next reader "harmonizes" the wrong
    half."""
    src = inspect.getsource(audit_graph)
    assert "#7927" in src
    assert "STAYS ``:Point``-ONLY" in src
    assert "WIDENED (#7927)" in src
    assert "false positive" in src and "false CLEAN" in src


def test_why_layer_records_its_residual_where_it_will_be_edited():
    """The why layer is STILL ``:Point``-only, deliberately — and the note has
    to live where the next widening will actually be attempted, or it reads as
    an accident of history and gets "harmonized" one query at a time.

    ``audit.py`` got exactly this treatment (a decision block inside
    ``audit_graph``'s body plus the pin above); the divergence-side surface is
    the one MORE likely to be half-widened, because ``ranking`` was widened and
    ``why`` was not. The pin names the two facts a partial fix would miss: that
    the scope is deliberate, and that ``_EP_CYPHER`` is the EXISTENCE ANCHOR, so
    widening a dependent read alone is a no-op.
    """
    import tortoise.why as why_mod
    src = inspect.getsource(why_mod)
    marker = src.index("#7927 RESIDUAL")
    conflicts = src.index("_CONFLICTS_CYPHER = (")
    assert marker < conflicts, "the residual note must sit above the query it governs"
    region = src[marker:conflicts]
    assert "DELIBERATE" in region
    assert "_EP_CYPHER" in region, (
        "the note must name the existence anchor — without it a reader widens "
        "the conflict read, sees no change, and widens more")
    # And the narrow scope the note describes must still be true, or the note
    # is stale prose claiming a residual that no longer exists.
    assert src.count("MATCH (n:Point) WHERE n.id IN $ids") >= 5, (
        "the why layer now has fewer Point-anchored reads than the note "
        "describes — either it was widened (update the note and this pin) or "
        "a read was dropped")


def test_audit_wiring_check_names_a_key_less_event_by_its_real_key(sdk):
    """The widened wiring checks EMIT a repair that must be APPLICABLE.

    A legacy / out-of-tree ``:Event`` may be keyed on ``eventId`` with no ``id``
    (``_upsert_event`` sets both, so this is not its shape — see the #7936
    residual in ``consistency.py``). A bare ``RETURN m.id`` then renders
    ``(m {id:'None'})``; interpolating the coalesced VALUE alone is no better,
    because ``(m {id:'<the eventId>'})`` matches nothing on a node with no
    ``id`` property. Either way the emitted "fix" silently leaves the very edge
    the check just reported.

    So this asserts the EFFECT, not the string: the emitted repair is executed
    and the edge must actually be gone. An emitted-but-unapplicable fix is the
    defect, not the message — the same ISSUED-vs-APPLIED distinction the BFS
    draft test makes.
    """
    proj = sdk._get_proj()
    proj.g.query("CREATE (op:Point {id:'audit-op-6b', is_operator:true, "
                 "status:'live', pointKind:'operator'})")
    # :Event keyed on eventId only — no ``id`` property at all.
    proj.g.query("CREATE (m:Event {eventId:'audit-ev-keyless', "
                 "eventKind:'test', name:'a keyless event', "
                 "startedAt:'2026-01-01T00:00:00Z'})")
    proj.g.query(
        "MATCH (op:Point {id:'audit-op-6b'}), (m:Event {eventId:'audit-ev-keyless'}) "
        "MERGE (op)-[:mitigates]->(m)")

    report = audit_graph(proj)
    legacy = [i for i in report.issues
              if i.issue_type == "legacy_mitigates_edge"]
    assert legacy, f"the widened check did not see the :Event endpoint: {report}"
    def _edges(rel: str) -> int:
        return proj.g.query(
            f"MATCH (:Point {{id:'audit-op-6b'}})-[r:{rel}]->() RETURN count(r)"
        ).result_set[0][0]

    assert _edges("mitigates") == 1, "fixture broken: the legacy edge is absent"
    for issue in legacy:
        assert "None" not in issue.fix, (
            f"the repair string interpolates a missing key: {issue.fix!r}")
        assert "audit-ev-keyless" in issue.fix, (
            f"the repair string cannot address the node it reports: {issue.fix!r}")
        proj.g.query(issue.fix)
    assert _edges("mitigates") == 0, (
        "the emitted repair did not remove the edge it reports — it is "
        "unapplicable, which is the defect this test exists for")
    assert _edges("mitigated_by") == 1, (
        "the emitted repair did not create the replacement edge")


def test_legacy_repair_is_idempotent_on_a_half_migrated_pair(sdk):
    """A HALF-APPLIED migration — both edges on the same pair — is precisely the
    state this check reports, so the repair must not mint a SECOND
    ``mitigated_by``: parallel direct edges between one pair share a single
    message slot (``ep._affected_factors``), so a duplicated edge is graph
    corruption performed by the suggested fix."""
    proj = sdk._get_proj()
    proj.g.query("CREATE (op:Point {id:'audit-op-dupe', is_operator:true, "
                 "status:'live', pointKind:'operator'})")
    proj.g.query("CREATE (m:Subject {id:'audit-dup-target', name:'a target', "
                 "subjectKind:'concept', status:'live'})")
    proj.g.query(
        "MATCH (op:Point {id:'audit-op-dupe'}), (m:Subject {id:'audit-dup-target'}) "
        "MERGE (op)-[:mitigates]->(m) MERGE (op)-[:mitigated_by]->(m)")

    legacy = [i for i in audit_graph(proj).issues
              if i.issue_type == "legacy_mitigates_edge"
              and i.node_id == "audit-op-dupe"]
    assert legacy, "fixture broken: the half-migrated pair was not reported"
    proj.g.query(legacy[0].fix)

    counts = {
        rel: proj.g.query(
            f"MATCH (:Point {{id:'audit-op-dupe'}})-[r:{rel}]->() "
            "RETURN count(r)").result_set[0][0]
        for rel in ("mitigates", "mitigated_by")
    }
    assert counts == {"mitigates": 0, "mitigated_by": 1}, (
        f"the repair must converge the pair, not duplicate the edge: {counts}")


def test_legacy_repair_addresses_only_the_key_the_node_carries(sdk):
    """The repair is KEY-SPECIFIC, and that is a correctness property, not
    polish: ``coalesce(m.id, m.eventId) = 'X'`` equates two DIFFERENT
    properties, so a key-agnostic match also reaches an unrelated node that
    happens to carry ``X`` under the other key — a shape ``consistency``
    explicitly models (#8879) — and would repair edges outside the issue's own
    ``point_kinds`` scope."""
    proj = sdk._get_proj()
    proj.g.query("CREATE (op:Point {id:'audit-op-collide', is_operator:true, "
                 "status:'live', pointKind:'operator'})")
    # The REPORTED endpoint: keyed on eventId.
    proj.g.query("CREATE (m:Event {eventId:'collide', eventKind:'test', "
                 "name:'keyless twin'})")
    # An unrelated node carrying the SAME token as its ``id``.
    proj.g.query("CREATE (x:Event {id:'collide', eventKind:'test', "
                 "name:'id twin'})")
    proj.g.query(
        "MATCH (op:Point {id:'audit-op-collide'}), (m:Event {eventId:'collide'}) "
        "MERGE (op)-[:mitigates]->(m)")
    proj.g.query(
        "MATCH (op:Point {id:'audit-op-collide'}), (x:Event {id:'collide'}) "
        "MERGE (op)-[:mitigates]->(x)")

    legacy = [i for i in audit_graph(proj).issues
              if i.issue_type == "legacy_mitigates_edge"
              and i.node_id == "audit-op-collide"]
    # BOTH edges are legacy mitigates edges, so the check reports both — one
    # issue per edge. What must NOT happen is one issue's repair reaching the
    # other endpoint, so each fix is executed ALONE and the survivor asserted.
    assert len(legacy) == 2, (
        f"expected one issue per legacy edge, got {[i.fix for i in legacy]}")
    by_key = {}
    for issue in legacy:
        for key in ("eventId", "id"):
            if f"(m {{{key}:'collide'}})" in issue.fix:
                by_key[key] = issue
    assert set(by_key) == {"eventId", "id"}, (
        "the repair is key-agnostic again — it must name the key the node "
        f"actually carries: {[i.fix for i in legacy]}")

    proj.g.query(by_key["eventId"].fix)
    remaining = [r[0] for r in proj.g.query(
        "MATCH (:Point {id:'audit-op-collide'})-[r:mitigates]->(m) "
        "RETURN m.name").result_set]
    assert remaining == ["id twin"], (
        "the repair for the eventId-keyed endpoint also rewrote the unrelated "
        f"id-keyed node that happens to carry the same token: {remaining}")


def test_legacy_repair_survives_an_id_that_needs_quoting(sdk):
    """A ``fix`` is a statement the caller PASTES AND RUNS, so an id with an
    apostrophe in it must still produce one that parses. ``create_point``
    accepts any string as an id, and a bare interpolation turns an ordinary
    ``claim-o'brien`` into a ``ResponseError`` — an emitted-but-unapplicable
    repair, which is the defect class this whole check exists to close."""
    proj = sdk._get_proj()
    quoted_op = "audit-op-o'brien"
    proj.g.query("CREATE (op:Point {id:$i, is_operator:true, status:'live', "
                 "pointKind:'operator'})", params={"i": quoted_op})
    proj.g.query("CREATE (m:Subject {id:'audit-quote-target', name:'target', "
                 "subjectKind:'concept', status:'live'})")
    proj.g.query(
        "MATCH (op:Point {id:$i}), (m:Subject {id:'audit-quote-target'}) "
        "MERGE (op)-[:mitigates]->(m)", params={"i": quoted_op})

    legacy = [i for i in audit_graph(proj).issues
              if i.issue_type == "legacy_mitigates_edge"
              and i.node_id == quoted_op]
    assert legacy, "fixture broken: the quoted-id operator was not reported"
    proj.g.query(legacy[0].fix)   # must not raise
    assert proj.g.query(
        "MATCH (:Point {id:$i})-[r:mitigates]->() RETURN count(r)",
        params={"i": quoted_op}).result_set[0][0] == 0


# ═══════════════════════════════════════════════════════════════════════
# 6. The emitted repairs must be RUNNABLE literals (#7927 review)
# ═══════════════════════════════════════════════════════════════════════

def test_quoted_round_trips_a_backslash_before_a_quote(sdk):
    """Escape ORDER is the bug, not the escaping.

    ``_quoted`` must escape ``\\`` FIRST: doing the quote first leaves an
    escaped backslash in front of the quote and re-opens the literal. A value
    containing a backslash AND a quote is the only shape that distinguishes the
    two orders, so nothing else pins it — measured here through the engine.
    """
    from tortoise.audit import _quoted

    proj = sdk._get_proj()
    tricky = "a\\'b"          # backslash, then quote — the order-sensitive pair
    proj.g.query("CREATE (n:Probe {id:" + _quoted(tricky) + "})")
    got = proj.g.query(
        "MATCH (n:Probe) RETURN n.id").result_set
    assert [r[0] for r in got] == [tricky], (
        "the quoted literal did not round-trip — the escape order is wrong: "
        f"{got}")


def test_legacy_repair_refuses_an_endpoint_with_no_identity(sdk):
    """A node carrying NEITHER key has no addressable identity, so the honest
    answer is NO STATEMENT — not a ``(m)`` pattern that rewrites every legacy
    edge from the operator (measured: two unkeyed endpoints produced the SAME
    statement, and running either repaired both). A repair whose stated subject
    is narrower than its effect is the defect class this shape exists to
    remove."""
    proj = sdk._get_proj()
    proj.g.query("CREATE (op:Point {id:'audit-op-noid', is_operator:true, "
                 "status:'live', pointKind:'operator'})")
    proj.g.query("CREATE (m:Event {eventKind:'test', name:'no key at all'})")
    proj.g.query(
        "MATCH (op:Point {id:'audit-op-noid'}), (m:Event) "
        "WHERE m.name = 'no key at all' MERGE (op)-[:mitigates]->(m)")

    legacy = [i for i in audit_graph(proj).issues
              if i.issue_type == "legacy_mitigates_edge"
              and i.node_id == "audit-op-noid"]
    assert legacy, "fixture broken: the identity-less edge was not reported"
    for issue in legacy:
        assert issue.fix == "", (
            "a statement was emitted for an endpoint that cannot be "
            f"addressed: {issue.fix!r}")
        assert "neither `id` nor `eventId`" in issue.detail, (
            f"the refusal is not explained in the message: {issue.detail!r}")


# ═══════════════════════════════════════════════════════════════════════
# 7. The coalesced identity is NOT unique — comparisons and dedupe must
#    use the internal node id, and a repair must NAME its node
# ═══════════════════════════════════════════════════════════════════════

def test_contested_relevance_survives_two_events_sharing_one_identity(sdk):
    """`_event_guarded_merge`'s SUFFIX branch MERGEs on the suffixed
    ``eventId`` while ``id`` keeps the CANDIDATE, so two distinct Events
    coalesce to the SAME value.

    Comparing that value made the self-reference guard drop a GENUINE
    counter-claim (measured: a real non-self NAND returned ``{}``). The guard
    compares ``id(n)`` — the internal node id, which is unique — so a real
    conflict still fires even when a value collides.
    """
    proj = sdk._get_proj()
    # Two DISTINCT Events that COALESCE to the same value: one carries ``id``,
    # the other carries the same token as its ``eventId`` — exactly the pair
    # ``_event_guarded_merge``'s suffix branch and the #8879 cross-key shape
    # produce. ``src`` (the counter-claim) is the keyless twin; ``target`` is
    # the id-keyed one. They are different nodes; their identities collide.
    target = "shared-token"
    proj.g.query("CREATE (n:Event {id:'shared-token', name:'a contested belief'})")
    proj.g.query("CREATE (s:Event {eventId:'shared-token', "
                 "name:'the cache rollback is the safer default'})")
    proj.g.query(
        "CREATE (op:Point {id:'coll-op', is_operator:true, op_type:'NAND', "
        "label:'contra', status:'live'})")
    proj.g.query(
        "MATCH (op:Point {id:'coll-op'}), (s:Event {eventId:'shared-token'}) "
        "MERGE (s)-[ri:INPUT]->(op) SET ri.idx = 0")
    proj.g.query(
        "MATCH (op:Point {id:'coll-op'}), (t:Event {id:'shared-token'}) "
        "MERGE (op)-[r:NAND]->(t) SET r.idx = 1")

    boosts = resolve_contested_relevance(proj, [target], "cache rollback")
    assert boosts == {target: W4_CONTESTED_BOOST}, (
        "a genuine counter-claim was dropped by an IDENTITY COLLISION — the "
        "guard is comparing a value instead of the node: "
        f"{boosts}")


def test_legacy_repair_names_the_node_not_a_colliding_value(sdk):
    """Two DISTINCT endpoints carrying the same ``id`` value must not share a
    repair: a single-key pattern matches both, so one issue's fix repairs the
    other's edge (measured: two byte-identical fixes, both edges gone)."""
    proj = sdk._get_proj()
    proj.g.query("CREATE (op:Point {id:'coll-repair-op', is_operator:true, "
                 "status:'live', pointKind:'operator'})")
    proj.g.query("CREATE (e1:Event {id:'coll-x', name:'one'})")
    proj.g.query("CREATE (e2:Event {id:'coll-x', eventId:'coll-x-suffix', "
                 "name:'two'})")
    proj.g.query(
        "MATCH (op:Point {id:'coll-repair-op'}), (e:Event {name:'one'}) "
        "MERGE (op)-[:mitigates]->(e)")
    proj.g.query(
        "MATCH (op:Point {id:'coll-repair-op'}), (e:Event {name:'two'}) "
        "MERGE (op)-[:mitigates]->(e)")

    legacy = [i for i in audit_graph(proj).issues
              if i.issue_type == "legacy_mitigates_edge"
              and i.node_id == "coll-repair-op"]
    assert len(legacy) == 2, f"expected one issue per edge, got {len(legacy)}"
    assert len({i.fix for i in legacy}) == 2, (
        "two distinct endpoints produced the SAME repair — it names a value, "
        f"not the node, so it repairs both edges: {[i.fix for i in legacy]}")


def test_impl_conversion_repair_refuses_an_endpoint_without_an_id(sdk):
    """``create_operator`` resolves its endpoints by ``id``, so a repair naming
    an endpoint keyed only on ``eventId`` either raises
    (``Points [...] do not exist``) or names its TWIN. It must be refused."""
    proj = sdk._get_proj()
    proj.g.query("CREATE (s:Subject {id:'impl-src', name:'a source claim', "
                 "subjectKind:'concept', status:'live'})")
    proj.g.query("CREATE (t:Event {eventId:'impl-keyless', "
                 "name:'this claim cannot hold'})")
    proj.g.query(
        "MATCH (s:Subject {id:'impl-src'}), (t:Event {eventId:'impl-keyless'}) "
        "MERGE (s)-[:IMPL]->(t)")

    issues = [i for i in audit_graph(proj).issues
              if i.issue_type == "impl_instead_of_nand"
              and i.node_id == "impl-src"]
    assert issues, "fixture broken: the IMPL edge was not reported"
    for issue in issues:
        assert issue.fix == "", (
            "an id-keyed conversion was emitted for an endpoint that has no "
            f"id: {issue.fix!r}")


def test_superseded_repair_never_names_a_nonexistent_identity(sdk):
    """``_quoted(None)`` renders the literal ``'None'`` — a phantom identity
    that can even address a real node whose id is "None"."""
    proj = sdk._get_proj()
    sup = _point(sdk, "a superseded claim")
    proj.g.query("MATCH (n:Point {id:$i}) SET n.status = 'superseded'",
                 params={"i": sup})
    proj.g.query("CREATE (e:Event {eventKind:'test', name:'no key'})")
    proj.g.query(
        "MATCH (s:Point {id:$s}), (e:Event) WHERE e.name = 'no key' "
        "MERGE (e)-[:IMPL]->(s)", params={"s": sup})

    issues = [i for i in audit_graph(proj).issues
              if i.issue_type == "superseded_active_edges"
              and i.node_id == str(sup)]
    assert issues, "fixture broken: the active edge was not reported"
    for issue in issues:
        assert "None" not in issue.fix, (
            f"the repair names a phantom identity: {issue.fix!r}")
