"""#6211 — the EP pass's id-anchored queries must be PLANNED as index reads.

The issue's first failure mode is a hop that dies on a graph of a few thousand
points: ``compute_confidence(anchors=[one], max_hops=2)`` aborted after 1.01 s
over a **29-claim** frontier on a 3,353-claim / 160,000-edge graph with the
engine at its 1,000 ms query bound. The cause is not the frontier and not the
seed count (the issue says so, and the measurement confirms it): every
id-anchored query of the pass was planned as a **label scan of the traversal's
other endpoint**, with ``n.id IN $ids`` applied per record afterwards.

Measured plans (``GRAPH.PROFILE``, falkordb-server 4.20.6, 23,354 nodes /
160,000 edges), two-hop bridge at a 30-id frontier::

    shipped     Node By Label Scan (op:Point) 23,353 -> 160,000 -> 1,280,000
                records, 1.15 s      (the id filter runs over all 1.28 M)
    + barrier   Node By Index Scan (n:Point) 30 -> 1,445 records, 0.009 s

so the cost tracked GRAPH size, not the frontier — the reason it degrades with
every point the map gains and the reason ``max_hops=1`` "converges" while
``max_hops=2`` never finishes.

WHAT IS PINNED HERE, and why each test is discriminating:

1. ``test_every_id_anchored_pass_query_is_planned_as_an_index_read`` — the
   queries ``ep.py`` actually EMITS (captured off the graph handle, not
   re-written in the test) plan through ``Node By Index Scan`` and contain no
   ``Node By Label Scan``. Measured discriminating power, per statement:
   removing the barrier reddens it at the seed neighbourhood, the plain-seed
   direct-edge expansion, the bridge hop and Batch 1. At the two OR-split
   sweeps and at Batch 3 the barrier is NOT what does the work — the endpoint
   split (and, at Batch 3, the two-``MATCH`` form) anchors on its own — so the
   barrier is kept at those three as the engine-6.0.0 predicate-drop guard
   (#6976), not as a cost fix, and this file does not claim otherwise.
2. ``test_the_hop_budget_tracks_the_frontier_not_the_graph`` — the cost half:
   the hop's traversal touches fewer records than the graph has edges, on a
   fixture whose background is deliberately much larger than the frontier.
   Without the barrier the traversal produces more records than the graph has
   edges, so this is a bound a label-scan plan cannot satisfy.
3. ``test_direct_edge_factors_are_collected_once_per_edge_from_both_ends`` —
   the two OR-shaped sweeps (Batch 3 factor extraction, the cache's
   back-message load) were split into endpoint-anchored halves; this pins that
   the split returns each direct edge exactly ONCE for all three cases (source
   in the affected set only, target only, both), that two byte-identical
   parallel edges stay two factors, and that the cache's back-message sweep
   populates both directions. The anchor endpoint (``hub``) is deliberately
   MULTI-LABELLED (``:Point:Object`` — the shape ``update_point``'s promote arm
   mints), because a per-label sweep matches such a node once per label: that
   is the only way this fixture can produce a duplicate row for a single edge,
   so it is what makes the edge-keyed (``id(r)``) dedupe observable rather than
   assumed.
4. ``test_the_operator_selection_queries_are_planned_as_index_reads`` — the
   operator selector in ``tortoise/analyze.py`` is the SAME defect class over
   the LARGEST id set of the pass (the whole operator frontier); all four of
   its id-set legs are pinned, not just the viability query. Measured
   ``All Node Scan (t)`` at 242.5 ms -> ``Node By Index Scan (op)`` at 12.6 ms
   on the same fixture, and 1.4-1.5 s -> under 0.1 s for the whole selection
   (best of 3).
5. ``test_the_records_that_state_this_files_size_are_true`` — the manifest row
   that carries this file's ``durations`` weight states its test COUNT in the
   comment above it, and that count was wrong when it was written (#9282
   review): a measured row whose stated count does not match what was measured
   is the one detail a later reader trusts. The count is pinned to the live
   ``def test_`` list, so adding a test reddens here instead of leaving the
   manifest quoting a file size that no longer exists.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.ep import TortoiseEP
from tortoise.sdk import TortoiseSDK

#: Operators wired into the hub claim — the hop's real neighbourhood, and much
#: smaller than the background field the query used to scan instead.
HUB_OPS = 300
#: Background operators: edge volume that a LABEL-SCAN plan must pay for and an
#: index-anchored plan must not. 4,000 ops x 2 inputs = 8,000 edges.
BACKGROUND_OPS = 4_000
HUB = "hub"
DIRECT_OUT = "direct-out"
DIRECT_IN = "direct-in"
DIRECT_BOTH = "direct-both"


def _build(sdk: TortoiseSDK) -> None:
    proj = sdk._get_proj()
    claims = [HUB, DIRECT_OUT, DIRECT_IN, DIRECT_BOTH]
    claims += [f"f{i}" for i in range(HUB_OPS)]
    claims += [f"bg{i}" for i in range(BACKGROUND_OPS)]
    for s in range(0, len(claims), 2_000):
        proj.g.query(
            "UNWIND $ids AS id CREATE (n:Point) SET n.id = id, n.is_operator = false, "
            "n.status = 'live', n.ep_alpha = 4.0, n.ep_beta = 1.0",
            params={"ids": claims[s : s + 2_000]},
        )
    ops = [f"hop{i}" for i in range(HUB_OPS)] + [f"bgop{i}" for i in range(BACKGROUND_OPS)]
    for s in range(0, len(ops), 2_000):
        proj.g.query(
            "UNWIND $ids AS id CREATE (n:Point) SET n.id = id, n.is_operator = true, "
            "n.op_type = 'IMPL', n.direction = 'bidirectional', n.status = 'live'",
            params={"ids": ops[s : s + 2_000]},
        )
    hub_rows = [{"op": f"hop{i}", "target": HUB, "filler": f"f{i}"} for i in range(HUB_OPS)]
    bg_rows = [
        {"op": f"bgop{i}", "target": f"bg{i}", "filler": f"bg{(i * 7 + 3) % BACKGROUND_OPS}"}
        for i in range(BACKGROUND_OPS)
    ]
    for rows in (hub_rows, bg_rows):
        for s in range(0, len(rows), 2_000):
            proj.g.query(
                "UNWIND $rows AS row MATCH (o:Point {id: row.op}) "
                "MATCH (t:Point {id: row.target}) MATCH (f:Point {id: row.filler}) "
                "CREATE (o)-[:IMPL {idx: 0, direction: 'bidirectional'}]->(t), "
                "       (o)-[:IMPL {idx: 1, direction: 'bidirectional'}]->(f)",
                params={"rows": rows[s : s + 2_000]},
            )
    # The direct-edge ANCHOR endpoint carries TWO epistemic labels — the
    # ``:Point:Object`` shape ``update_point``'s promote arm mints, and one the
    # write path admits as a direct-edge endpoint. In Cypher a node matches a
    # single-label pattern, so ``hub`` is admitted by BOTH the ``Point`` and the
    # ``Object`` iteration of each per-label sweep. This is the ONLY shape in
    # which one edge can come back twice, so without it a fixture cannot
    # distinguish an edge-keyed dedupe from no dedupe at all (#6211 review).
    proj.g.query(
        "MATCH (n:Point {id: $hub}) SET n:Object",
        params={"hub": HUB},
    )
    # ... and a far endpoint with NO id, because the b-anchored half's guard
    # is ``(_other.id IS NULL OR NOT _other.id IN $ids)`` and ``NOT null IN $ids``
    # is NULL, not TRUE: drop the IS NULL arm and every edge whose other end
    # has no id silently disappears. Without such a node in the fixture that
    # mutant is invisible (every node here carries an id).
    proj.g.query(
        "MATCH (h:Point {id: $hub}) "
        "CREATE (h)-[:IMPL {direction: 'bidirectional'}]->"
        "(:Point {status: 'live', is_operator: false})",
        params={"hub": HUB},
    )
    # Operator-less direct edges (#888 W5): one leaving the affected set, one
    # entering it, one entirely inside it, and TWO byte-identical parallel
    # edges leaving it (they must stay two factors — a row-keyed dedupe would
    # collapse them, which is why the dedupe key is the EDGE's internal id).
    proj.g.query(
        "MATCH (h:Point {id: $hub}), (o:Point {id: $out}), (i:Point {id: $in}), "
        "(b:Point {id: $both}) "
        "CREATE (h)-[:IMPL {direction: 'bidirectional'}]->(o), "
        "       (i)-[:IMPL {direction: 'bidirectional'}]->(h), "
        "       (h)-[:IMPL {direction: 'bidirectional'}]->(b), "
        "       (h)-[:IMPL {direction: 'bidirectional'}]->(b)",
        params={"hub": HUB, "out": DIRECT_OUT, "in": DIRECT_IN, "both": DIRECT_BOTH},
    )


@pytest.fixture(scope="module")
def graph():
    with tempfile.TemporaryDirectory(prefix="tt6211a_") as td:
        sdk = TortoiseSDK(os.path.join(td, "t.db"))
        try:
            _build(sdk)
            yield sdk
        finally:
            sdk.close()


class _Recorder:
    """Records the statements ``ep.py`` emits, then delegates unchanged."""

    def __init__(self, handle):
        self._handle = handle
        self.statements: list[tuple[str, dict]] = []

    def query(self, cypher, params=None, timeout=None):
        self.statements.append((cypher, params or {}))
        return self._handle.query(cypher, params=params, timeout=timeout)

    def __getattr__(self, name):
        return getattr(self._handle, name)


def _capture(ep: TortoiseEP, fn, *args, **kw) -> list[tuple[str, dict]]:
    """Run ``fn`` with the graph handle recording, return what it emitted."""
    real = ep.g
    rec = _Recorder(real)
    ep.g = rec
    try:
        fn(*args, **kw)
    finally:
        ep.g = real
    # Only the id-set statements are in scope: a whole-graph count or the
    # :EpMeta epoch read are not id-anchored and a label scan is correct there.
    # The pass binds the set as `$ids` (claim seed) or `$oids` (operator seed);
    # the operator selector binds it as `$frontier` (BFS frontier) or `$ops`
    # (operator expansion) — all four are the SAME id-anchored shape, so a
    # filter that knew only the pass's two would silently drop the selector's
    # three extra legs before the assertion ever saw them.
    return [(q, p) for q, p in rec.statements if any(n in q for n in _ID_PARAMS)]


def _plan_text(handle, cypher: str, params: dict) -> str:
    return " ".join(handle.explain(cypher, params=params).plan)


def _statements_under_test(ep: TortoiseEP) -> list[tuple[str, str, dict]]:
    """Every id-anchored statement the pass emits, with a name for the report."""
    out: list[tuple[str, str, dict]] = []

    def add(name, stmts):
        for q, p in stmts:
            out.append((name, q, p))

    add("seed neighbourhood", _capture(ep, ep._live_neighbors_many, [HUB], False, "Point"))
    add("bridge hop", _capture(ep, ep._affected_claims, [HUB], 2, False))
    # An OPERATOR seed runs the `seed_operators` statement (`$oids`), which the
    # claim-seed captures above cannot reach.
    add("operator seed", _capture(ep, ep._affected_claims, ["hop0"], 2, False))
    affected = ep._affected_claims([HUB], 2, False)
    add("factor extraction", _capture(ep, ep._affected_factors, affected, False))
    add("cache load", _capture(ep, ep._load_cache, affected))
    return out


def test_the_fixture_is_dense_enough_for_the_bound_to_bite(graph):
    """Guard the fixture: the background must dwarf the hub's neighbourhood."""
    g = graph._get_proj().g
    edges = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    assert edges >= 8_000, f"background too small to discriminate ({edges} edges)"
    assert edges // 4 > HUB_OPS, "the hub's own reach must be a fraction of the graph"


def test_every_id_anchored_pass_query_is_planned_as_an_index_read(graph):
    ep = TortoiseEP(graph._get_proj())
    statements = _statements_under_test(ep)
    assert statements, "no id-anchored statements captured — the recorder missed the pass"

    offenders = []
    for name, cypher, params in statements:
        plan = _plan_text(ep.g, cypher, params)
        if "Node By Label Scan" in plan or "Node By Index Scan" not in plan:
            offenders.append((name, cypher, plan))
    assert not offenders, (
        "an id-anchored EP query is planned as a label scan, so its cost tracks "
        "the GRAPH and not the frontier (#6211):\n"
        + "\n".join(f"  [{name}] {cypher}\n      plan: {plan}" for name, cypher, plan in offenders)
    )


def test_the_hop_budget_tracks_the_frontier_not_the_graph(graph):
    ep = TortoiseEP(graph._get_proj())
    g = ep.g
    total_edges = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    statements = _capture(ep, ep._live_neighbors_many, [HUB], False, "Point")
    assert len(statements) == 1, statements

    cypher, params = statements[0]
    # The hop walks ~HUB_OPS operators x 2 inputs; a label-scan plan walks the
    # whole graph's edges and then some.
    bound = HUB_OPS * 4
    assert bound * 2 < total_edges, "fixture cannot discriminate the two plans"

    worst = 0
    for line in g.profile(cypher, params=params).plan:
        match = re.search(r"Conditional Traverse.*?Records produced: (\d+)", line)
        if match:
            worst = max(worst, int(match.group(1)))
    assert worst, (
        f"no traversal record count in the profile: {g.profile(cypher, params=params).plan}"
    )
    assert worst < bound, (
        f"the hop traversed {worst} records for a {HUB_OPS}-operator neighbourhood "
        f"on a {total_edges}-edge graph — the plan is not anchored on the frontier "
        f"(bound {bound})"
    )


def test_direct_edge_factors_are_collected_once_per_edge_from_both_ends(graph):
    ep = TortoiseEP(graph._get_proj())
    affected = {HUB, DIRECT_BOTH}  # source-only, target-only and both-endpoint edges
    factors = ep._affected_factors(affected, include_draft=False)
    direct = {(f[2][0], f[2][1]) for f in factors if f[4] is None}

    # (hub)->(direct-out) is reached from its SOURCE, (direct-in)->(hub) from its
    # TARGET, (hub)->(direct-both) from BOTH ends — each must appear exactly once.
    # ``hub`` is :Point:Object, so the a-anchored AND b-anchored halves each see
    # these edges twice (once per label sweep): a single factor per edge is only
    # reachable if the split dedupes on the EDGE.
    assert (HUB, DIRECT_OUT) in direct, direct
    assert (DIRECT_IN, HUB) in direct, direct
    assert (HUB, DIRECT_BOTH) in direct, direct
    for pair in ((HUB, DIRECT_OUT), (DIRECT_IN, HUB)):
        assert sum(1 for f in factors if f[4] is None and tuple(f[2]) == pair) == 1, (
            f"direct edge {pair} produced more than one factor (a multi-labelled "
            f"endpoint is swept once per label; the dedupe key must be id(r))"
        )
    # ... but TWO byte-identical parallel edges are TWO factors, not one.
    both = [f for f in factors if f[4] is None and tuple(f[2]) == (HUB, DIRECT_BOTH)]
    assert len(both) == 2, f"parallel direct edges collapsed into {len(both)} factor(s)"
    # ... and the edge to the id-LESS node survives: the b-anchored half's guard
    # is `(_other.id IS NULL OR NOT _other.id IN $ids)` because `NOT null IN $ids`
    # is NULL, not TRUE. Dropping the `IS NULL` arm silently loses every direct
    # edge whose other end has no id — invisible without such a node here.
    noid = [f for f in factors if f[4] is None and tuple(f[2]) == (HUB, None)]
    assert len(noid) == 1, (
        f"the direct edge to an id-less endpoint produced {len(noid)} factor(s), "
        "not 1 — the OR split's `IS NULL` guard is load-bearing"
    )

    # The cache's back-message sweep is the other OR split: it must load the
    # back-message slot of a direct edge discovered from EITHER end.
    ep._load_cache(affected)
    for src, tgt in ((HUB, DIRECT_OUT), (DIRECT_IN, HUB), (HUB, DIRECT_BOTH)):
        assert (src, tgt, "IMPL") in ep._back_cache, (
            f"back-message of direct edge {src}->{tgt} was not loaded — the "
            f"split sweep covers only one endpoint"
        )


#: Every parameter name an id-set statement in this file's scope binds: the
#: pass's claim (`$ids`) and operator (`$oids`) seeds, and the operator
#: selector's frontier (`$frontier`) and expansion (`$ops`) sets.
_ID_PARAMS = ("$ids", "$oids", "$frontier", "$ops")


def test_the_or_split_statement_counts_are_anchors_times_labels(graph):
    """The split emits 2 anchors x 4 epistemic labels at each OR site (8 + 8).

    This pins the arithmetic `docs/capacity.md` §7 states for the split's cost
    — Batch 3 growing from ONE unlabelled `(a.id IN $ids OR b.id IN $ids)`
    statement to 8, and the cache's back-message sweep from 4 target-labelled
    statements to 8, i.e. +11 statements per pass. Without a pin the doc's
    number is unverifiable prose: a site that silently collapses back to one
    statement per site, or that stops widening per anchor, would leave the
    sentence true-sounding and false (the pre-#6211 code emits 1 and 4 here,
    so this assertion is discriminating rather than decorative).
    """
    from tortoise.live import EPISTEMIC_LABELS

    ep = TortoiseEP(graph._get_proj())
    affected = ep._affected_claims([HUB], 2, False)
    per_site = 2 * len(EPISTEMIC_LABELS)  # 2 endpoint anchors x 4 labels

    factor_stmts = [
        q for q, _p in _capture(ep, ep._affected_factors, affected, include_draft=False)
        if "RETURN id(r)" in q
    ]
    assert len(factor_stmts) == per_site, (
        f"Batch 3 emitted {len(factor_stmts)} statement(s), not {per_site} "
        f"(2 anchors x {len(EPISTEMIC_LABELS)} labels):\n"
        + "\n".join(f"  {q[:90]}" for q in factor_stmts)
    )

    cache_stmts = [
        q for q, _p in _capture(ep, ep._load_cache, affected)
        if "r.back_msg_alpha" in q
    ]
    assert len(cache_stmts) == per_site, (
        f"the cache's back-message sweep emitted {len(cache_stmts)} "
        f"statement(s), not {per_site} (2 anchors x "
        f"{len(EPISTEMIC_LABELS)} labels):\n"
        + "\n".join(f"  {q[:90]}" for q in cache_stmts)
    )

    # ... and the 2 x 4 structure is the COUNT's structure, not a coincidence of
    # 8 anonymous statements: each site must cover every (anchor, label) pair.
    for site, stmts in (("Batch 3", factor_stmts), ("cache back-message", cache_stmts)):
        for label in EPISTEMIC_LABELS:
            for anchor in ("a", "b"):
                assert any(f"MATCH ({anchor}:{label})" in q for q in stmts), (
                    f"{site} emitted no {anchor}-anchored {label} statement — the "
                    f"per-endpoint split lost an anchor/label pair"
                )


def test_the_operator_selection_queries_are_planned_as_index_reads(graph):
    """The SAME defect over the LARGEST id set of the pass (analyze.py).

    `MATCH (op:Point {is_operator:true})-[:IMPL|NAND]-(t) WHERE op.id IN $ids`
    was planned as `All Node Scan (t)` — measured 242.5 ms against 12.6 ms
    with the anchor barrier on the 23k-node / 160k-edge fixture, identical
    rows. It is the operator-viability query, and its id set is the whole
    operator frontier, so it was the worst-anchored statement of the pass.

    The selector's OTHER three id-set legs were measured unanchored too and are
    pinned here — `Node By Label Scan (op:Point)` for the incoming-operator leg,
    `All Node Scan (a)` for the backward direct-edge leg and `All Node Scan (p)`
    for the operator-expansion leg (whose id predicate sits on the pattern's
    FIRST node: it is the continuation, not the pattern side, that made the
    planner walk the other endpoint). One `_bfs_select_operators` call took
    1.4-1.5 s then and takes ~0.1 s now (best of 3, same 168 operators).
    """
    from tortoise.analyze import _bfs_select_operators

    proj = graph._get_proj()
    # Two seeds, because one seed cannot reach every leg: seeding the HUB
    # collects HUB_OPS=300 operators, `op_cap` truncates that to 200 and the
    # cap `break` PRECEDES the expansion query — so `op.id IN $ops` never runs
    # there. A seed with a small neighbourhood (`f0`, whose operators are
    # capped nowhere) does emit it. Both calls are needed, or the assertion is
    # blind to a statement — the same failure mode as a capture filter that
    # knows too few parameter names.
    statements = _capture(proj, _bfs_select_operators, proj, [HUB], 2)
    statements += _capture(proj, _bfs_select_operators, proj, ["f0"], 2)
    anchored = [(q, p) for q, p in statements if any(n in q for n in _ID_PARAMS)]
    assert len(anchored) >= 4, (
        f"expected the selector's id-set legs, captured {[q for q, _ in anchored]}"
    )
    # Coverage guard: every leg must actually be PRESENT, or a cap/fixture
    # change could drop one and this test would pass anyway. `$ops` alone is
    # not enough — the selector binds it on the outgoing leg too, so the
    # expansion leg is pinned by its own projection (`RETURN DISTINCT p.id`,
    # which no other leg emits).
    for needle in ("$ids", "$frontier", "$ops", "RETURN DISTINCT p.id"):
        assert any(needle in q for q, _ in anchored), (
            f"no statement matching {needle!r} captured — that leg is not "
            f"pinned (captured: {[q[:60] for q, _ in anchored]})"
        )
    offenders = [
        (q, _plan_text(proj.g, q, p))
        for q, p in anchored
        if "Node By Index Scan" not in _plan_text(proj.g, q, p)
        or "Node By Label Scan" in _plan_text(proj.g, q, p)
        or "All Node Scan" in _plan_text(proj.g, q, p)
    ]
    assert not offenders, (
        "the operator-selection query is not index-anchored (#6211):\n"
        + "\n".join(f"  {q}\n      plan: {plan}" for q, plan in offenders)
    )


def test_the_records_that_state_this_files_size_are_true():
    """The manifest's ``durations`` comment must state this file's real size.

    #9282 review P2: the row above ``test_6211_ep_anchor.py`` in
    ``config/ci-surfaces.yml`` read *"whole file, 5 tests"* while the file
    defined six — and the PR body repeated the number twice. The count and the
    wall time are re-derived TOGETHER at every revision (the #3429/#8098
    convention); a count carried forward is the detail a reader trusts and
    cannot check without re-measuring.

    This does not pin the WEIGHT: a weight is a shard-layout input and the
    refresher owns it (#3400), so only the count is asserted.
    """
    here = Path(__file__).resolve()
    live = len(re.findall(r"^def test_", here.read_text(encoding="utf-8"), re.MULTILINE))
    manifest = (here.parents[1] / "config" / "ci-surfaces.yml").read_text(encoding="utf-8")
    block = re.search(
        r"((?:^  # [^\n]*\n)+)^  test_6211_ep_anchor\.py:", manifest, re.MULTILINE
    )
    assert block, (
        "config/ci-surfaces.yml has no commented durations row for "
        "test_6211_ep_anchor.py — the row is the only record of how its weight "
        "was derived"
    )
    stated = re.search(r"(\d+) tests? /", block.group(1))
    assert stated, (
        "the durations comment for test_6211_ep_anchor.py states no test count, "
        "so its weight cannot be re-derived:\n" + block.group(1)
    )
    assert int(stated.group(1)) == live, (
        f"config/ci-surfaces.yml says test_6211_ep_anchor.py has {stated.group(1)} "
        f"tests; the file defines {live}. Re-derive the count AND the wall time "
        f"together, the way the row's tail already instructs."
    )
