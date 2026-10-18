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
  5. audit counts             — ``audit.audit_graph`` (DELIBERATE :Point-only
                                scope, documented in code — see the docstring test)
  6. SVBP factor extractor    — ``projection.extract_svbp_factors``

Each behavioral test seeds a :Subject/:Object/:Event node (parametrized over
the three labels where the surface allows) and asserts the surface RETURNS it.
Every widening goes through the shared declaration in ``tortoise/live.py``
(``epistemic_label_queries`` for id-anchored reads, ``epistemic_disjunction``
for traversal positions) — the source-level tests at the bottom pin that, so
no surface can drift back to a hand-spelled label predicate.

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
from tortoise.live import EPISTEMIC_LABELS, epistemic_labels_of
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


def test_ranker_read_costs_one_anchored_query_for_point_ids(sdk):
    """The ranker sweep is resolution + ONE anchored read for a Point-only id
    set — the recall/rerank latency contract, pinned so the widening cannot
    quietly become four reads per result set."""
    p = _point(sdk)
    counter = _CountingProjection(sdk._get_proj())
    GraphRanker(projection=counter)._fetch_point_signals([p])
    assert len(counter.queries) == 2, counter.queries


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
    silent no-op for the three labels it cannot see)."""
    p = _point(sdk)
    n = _node(sdk, "Subject", "draft-anchor", status="draft")
    sdk.create_operator("IMPL", p, [n])

    ops, _ = _bfs_select_operators(sdk._get_proj(), [n], max_hops=1)
    assert ops == set(), "a draft :Subject anchor was treated as live"


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
# 5. Audit counts — DELIBERATE :Point-only scope (#7927 decision)
# ═══════════════════════════════════════════════════════════════════════

def test_audit_scope_is_point_only_by_decision(sdk):
    """The audit is a REPORT over the Point wiring universe, scoped by
    ``pointKind`` (a Point-only property) and auditing point-level provenance
    annotations that no other label carries. Admitting the four labels here
    would flag every Subject/Object/Event as a legacy provenance gap — a false
    positive produced by the widening itself. So the scope stays :Point-only
    and must not silently change either way."""
    _point(sdk, "an audited claim")
    _node(sdk, "Subject", "not-audited-subject")
    _node(sdk, "Object", "not-audited-object")
    _node(sdk, "Event", "not-audited-event")

    result = audit_graph(sdk._get_proj())
    assert result.node_count == 1, (
        "audit node_count must count only in-scope Points; a changed count "
        "means the documented :Point-only decision was silently reversed")
    flagged = {i.node_id for i in result.issues}
    assert not flagged & {"not-audited-subject", "not-audited-object",
                          "not-audited-event"}


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
    assert "epistemic_label_queries(" in src, "id-anchored legs must sweep live.EPISTEMIC_LABELS"
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
    assert "_query_label_sweep(" in src, f"{fn.__qualname__} bypasses the shared sweep"
    assert "MATCH (n:Point) WHERE n.id IN $ids" not in src


def test_contested_relevance_uses_the_shared_label_helpers():
    src = inspect.getsource(resolve_contested_relevance)
    assert "_query_label_sweep(" in src
    assert "epistemic_disjunction(" in src
    assert "MATCH (n:Point) WHERE n.id IN $ids" not in src


def test_audit_records_its_point_only_decision_in_reachable_code():
    """A deliberate :Point-only scope must be FINDABLE, not inferred: the
    decision lives in ``audit_graph``'s own body so the next reader meets it
    where the predicate lives."""
    src = inspect.getsource(audit_graph)
    assert "#7927 DECISION" in src
    assert "deliberately ``:Point``-ONLY" in src
