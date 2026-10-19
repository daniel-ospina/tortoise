"""#7925 — ONE endpoint-label contract on BOTH connection routes, proved at EP.

The claim this file pins is ONTOLOGY §5 / §8's:

    operators connect all four epistemic labels (``:Point``/``:Subject``/
    ``:Object``/``:Event``) and **confidence propagates across all four**
    (owner ruling #7813, recorded as the §8 ``OVERRIDES``).

The graph realises that ONE edge with TWO write routes:

  **A. the operator route** — a ``:Point {is_operator: true}`` factor node with
  child/target edges. Widened to the four labels by ``#7902``.

  **B. the direct-edge route** — a plain ``IMPL``/``NAND`` edge carrying
  direction/weight/label on the EDGE, with no factor node (ONTOLOGY §8). The
  same primitive widening (#7902) landed in ``create_direct_edge``, but
  ``ingest()``'s endpoint validation kept hand-gating this route on a plain
  ``:Point`` (the ``#2062`` asymmetry) — so the primitive WROTE a
  ``(Subject)-[:NAND]->(Object)`` edge while ``ingest()`` REFUSED the very same
  connection with ``must be a plain Point``. That is the drift ``#7925``
  corrects: the validator follows the primitive instead of carrying a second,
  narrower copy of the contract.

⚠️ **Acceptance alone is not the proof.** Two routes admitting the same
endpoints says nothing about whether they are the same *epistemic* edge. The
deliverable here is PARITY: the same pair of facts driven through either route
must land the SAME EP posterior on the target. If the direct route admitted the
endpoints but produced a different confidence, the ontology would be right and
the engine incomplete — the test would fail, and loosening the tolerance is not
the fix.

⚠️ **Why the parity pair carries no ``:Point``**: owner ruling #8499 (2026-10-13)
makes the ingest direct route operator-MEDIATED for any Point-bearing pair
(the route reifies and warns). So a ``(Subject)-[NAND]->(Point)`` connection
written through ``ingest()`` exercises the OPERATOR route on both spellings —
it cannot be the direct route's evidence.
``test_point_bearing_pair_is_reified_on_the_direct_route`` (this file) — and
``test_point_pair_plain_impl_is_reified_and_warns``
(``tests/test_8499_point_mediated_routing.py``) — pin that behaviour explicitly,
so the Point-free choice of the parity pair reads as the ruling, not as an
oversight.

The confidence figures are compared against a STATED tolerance of ``1e-9``: the
two routes feed the same factor math (:meth:`TortoiseEP._update_factor`) with
the same default weight, so the only admissible difference is float noise. A
looser tolerance would hide exactly the divergence this test exists to catch.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

from tortoise.sdk import TortoiseSDK

#: Two routes, one factor equation — anything above float noise is a real
#: behavioural divergence, not a rounding artefact.
PARITY_TOLERANCE = 1e-9

#: The SOLVER tolerance the parity comparison runs at — deliberately three
#: orders BELOW :data:`PARITY_TOLERANCE`. EP's own stop is ``tol=1e-4`` on the
#: RELATIVE change of one step, so ``converged=True`` certifies that the last
#: step was small, NOT that the iterate IS the fixed point. MEASURED on this
#: fixture: both routes report ``converged=True`` at the default tolerance and
#: still sit ~3.6e-5 apart, because each stops a different number of damped
#: steps short of the SAME attractor — the operator route arrives at it through
#: the no-arg dream pass (1 exact iteration), while the direct route, which the
#: dream's ``is_operator``-only selector cannot reach, needs 12. Driving both to
#: ``PARITY_SOLVER_TOL`` removes the stopping distance: the same fixture then
#: agrees to ~5e-13, i.e. the ROUTES are identical and only the STOP was being
#: compared. :data:`PARITY_TOLERANCE` is not loosened by this.
PARITY_SOLVER_TOL = 1e-12

#: Head-room for the tightened stop — the fixture converges in <=58 damped
#: iterations, well inside EP's default 50-iteration cap scaled up here.
PARITY_SOLVER_MAX_ITER = 1000

#: The Beta(1,1) prior every seeded node starts from.
PRIOR_MEAN = 0.5
PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0

#: How far alpha/beta must move off the prior for the run to count as evidence
#: that EP CONSUMED the factor. This is the route-agnostic "propagation
#: happened" signal, and it is deliberately NOT ``effective_n``: a NAND posterior
#: carries LESS mass than the prior (measured ``alpha+beta = 1.9429`` on this
#: fixture's attacked target), so an ``effective_n > 2`` floor would be false for
#: the very route under test.
PARAM_MOVE_FLOOR = 0.01

#: How far a NAND node's MEAN must move below the prior. Only asserted for
#: NAND: a mutual IMPL whose inputs all sit at the neutral prior is SYMMETRIC
#: evidence — it raises precision (measured alpha/beta 1.0 → 1.2693 on this
#: fixture's target, 1.4648 on its source) while leaving every mean at exactly
#: 0.5 — so the mean is not the propagation signal there.
PROPAGATION_FLOOR = 0.01

OP_TYPES = ("NAND", "IMPL")

#: ⚠️ ``<->`` (mutual), NOT ``->``, and the fixture FANS OUT (two targets from
#: one source). Both halves are load-bearing for teeth: under ``->`` the direct
#: route's per-edge BACK-message slot (#888 W5 — ``r.back_msg_alpha``/``_beta``,
#: read by the source's cavity and written by ``_write_back_message``) is never
#: written and never read, and with a single factor the source's own prior is
#: all the cavity needs, so the whole slot could be deleted and the file would
#: still pass. MEASURED: stubbing ``_read_back_message`` to ``(0, 0)`` and
#: ``_write_back_message`` to a no-op left the single-factor ``->`` fixture at
#: 6 passed. A mutual, fan-out factor loads that slot on both routes, so the
#: mutation now reds it.
DIRECTION = "<->"


# ── fixtures / helpers ────────────────────────────────────────────────────


@pytest.fixture
def sdk_factory():
    """Yield a factory of throwaway embedded SDKs; close + remove each."""
    made: list[tuple[TortoiseSDK, str]] = []

    def _make() -> TortoiseSDK:
        db_dir = tempfile.mkdtemp(prefix="tortoise_7925_")
        sdk = TortoiseSDK(os.path.join(db_dir, "test.db"))
        made.append((sdk, db_dir))
        return sdk

    yield _make
    for sdk, db_dir in made:
        sdk.close()
        shutil.rmtree(db_dir, ignore_errors=True)


def _rows(sdk: TortoiseSDK, cypher: str, params: dict | None = None) -> list:
    return sdk._get_proj().g.query(cypher, params=params or {}).result_set


def _seed_entities(sdk: TortoiseSDK,
                   object_names: tuple[str, ...] = ("Datasets",)
                   ) -> tuple[str, list[str]]:
    """One ``:Subject`` plus N ``:Object`` nodes — the non-Point endpoints
    under test, returned as ``(subject_id, [object_id, ...])``."""
    items: list[dict] = [{"ref": "s", "type": "subject",
                          "name": "Analyst Alice",
                          "subjectKind": "naturalPerson"}]
    for i, name in enumerate(object_names):
        items.append({"ref": f"o{i}", "type": "object", "name": name})
    refs = sdk.ingest({"entities": items})["ids"]["refs"]
    return refs["s"], [refs[f"o{i}"] for i in range(len(object_names))]


def _seed_subject_and_point(sdk: TortoiseSDK) -> tuple[str, str]:
    res = sdk.ingest({
        "entities": [{"ref": "s", "type": "subject", "name": "Analyst Alice",
                      "subjectKind": "naturalPerson"}],
        "points": [{"ref": "p", "kind": "statement", "content": "Datasets exist"}],
    })
    return res["ids"]["refs"]["s"], res["ids"]["refs"]["p"]


def _operator_node_count(sdk: TortoiseSDK) -> int:
    rows = _rows(
        sdk,
        "MATCH (n:Point) WHERE coalesce(n.is_operator, false) = true "
        "RETURN count(n)")
    return int(rows[0][0]) if rows else 0


def _direct_edge_rows(sdk: TortoiseSDK, op_type: str) -> list:
    """The OPERATOR-LESS ``op_type`` edges as (src, tgt, direction, weight).

    The WHERE clause is the same predicate ``TortoiseEP._affected_factors``
    Batch 3 uses to admit a direct-edge factor (no ``is_operator``, no
    ``op_type`` on either endpoint) — so this helper sees exactly the edges EP
    treats as direct factors, and an operator route's own input edges are never
    miscounted as them.
    """
    return _rows(
        sdk,
        f"MATCH (a)-[r:{op_type}]->(b) "
        "WHERE (a.is_operator IS NULL OR a.is_operator = false) "
        "AND a.op_type IS NULL "
        "AND (b.is_operator IS NULL OR b.is_operator = false) "
        "AND b.op_type IS NULL "
        "RETURN a.id, b.id, r.direction, r.weight")


def _write_via_operator_route(sdk: TortoiseSDK, src: str, tgt: str,
                              op_type: str) -> dict:
    """Route A — the operator primitive (#7902 already admitted all four)."""
    return sdk.create_operator(op_type, src, [tgt], direction=DIRECTION)


def _write_via_direct_route(sdk: TortoiseSDK, src: str, tgt: str,
                            op_type: str) -> dict:
    """Route B — a plain IMPL/NAND connection, no reify, no mitigation, so the
    route selector classifies it 'direct' and it dispatches to
    ``create_direct_edge``. Until #7925 the endpoint contract refused the
    non-Point endpoints this carries."""
    return sdk.ingest({"connections": [
        {"from": src, "to": tgt, "operator": op_type, "direction": DIRECTION},
    ]})


def _solve_to_fixed_point(sdk: TortoiseSDK) -> None:
    """Tighten EP's stopping rule so ``converged`` means FIXED POINT.

    ``TortoiseEP`` stops when one step's RELATIVE change falls below ``tol``
    (default ``1e-4``), so a converged run may still be ~1e-5 short of the
    attractor — enough to fail a 1e-9 parity assertion with no route
    divergence behind it. One EP instance serves the no-arg dream pass AND the
    exact pass, so tightening it here covers the whole
    :meth:`compute_confidence` call.
    """
    ep = sdk._get_ep()
    ep.tol = PARITY_SOLVER_TOL
    ep.max_iter = max(ep.max_iter, PARITY_SOLVER_MAX_ITER)


def _target_confidence(sdk: TortoiseSDK, *node_ids: str) -> dict[str, dict]:
    """Posteriors for ``node_ids`` at EP's fixed point over the graph.

    ⚠️ The 1e-9 parity assertion below is only meaningful at EP's FIXED POINT:
    the run shuffles the factor list every iteration, so an iterate that is
    still moving hands us two arbitrary — and order-dependent — points.
    ``converged`` alone does not establish that (see
    :data:`PARITY_SOLVER_TOL`), so the solver is tightened FIRST and the flag
    is then asserted as a second, independent guard.
    """
    _solve_to_fixed_point(sdk)
    out = sdk.compute_confidence()
    assert out["converged"] is True, (
        f"EP did not converge in {out['iterations']} iterations "
        f"(diagnostic={out.get('diagnostic')!r}); route parity is only "
        f"meaningful at the fixed point")
    confidences = out["confidences"]
    missing = [n for n in node_ids if n not in confidences]
    assert not missing, (
        f"EP did not report a posterior for {missing!r}; "
        f"diagnostic={out.get('diagnostic')!r} "
        f"skipped={out.get('skipped_factors')!r}")
    return {n: confidences[n] for n in node_ids}


# ── the proof ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("op_type", OP_TYPES)
def test_direct_route_accepts_non_point_endpoints(sdk_factory, op_type: str):
    """Route B accepts ``:Subject`` → ``:Object`` and writes the DIRECT shape.

    Acceptance is the #7925 half; the shape assertion is what proves the write
    is the operator-LESS edge (a factor node would be the operator route
    quietly substituted for the direct one).
    """
    sdk = sdk_factory()
    src, (tgt,) = _seed_entities(sdk)

    res = _write_via_direct_route(sdk, src, tgt, op_type)  # must not raise

    assert res["created"]["connections"] == 1
    # the receipt, not an exact dict pin (an additive receipt field is not a
    # route-shape change; the graph assertions below carry the shape proof)
    receipt = res["ids"]["connections"][0]
    assert receipt["direct_edge"] == op_type
    assert (receipt["from"], receipt["to"]) == (src, tgt)
    # no #8499 reification warning: a Subject/Object pair is not Point-bearing
    assert not [w for w in res.get("warnings") or []
                if w.startswith("point_mediated_route")], res.get("warnings")

    edges = _direct_edge_rows(sdk, op_type)
    assert len(edges) == 1, edges
    assert (edges[0][0], edges[0][1]) == (src, tgt)
    # direction is recorded on the EDGE (verbatim, ONTOLOGY §8)
    assert edges[0][2] == DIRECTION
    # …and there is NO operator node: this is the operator-less route
    assert _operator_node_count(sdk) == 0


@pytest.mark.parametrize("op_type", OP_TYPES)
def test_both_routes_produce_the_same_confidence(sdk_factory, op_type: str):
    """THE deliverable: the same facts, two routes, the same posteriors.

    ``(Subject "Analyst Alice")-[NAND|IMPL <->]->(Object "Datasets")`` and
    ``(Subject "Analyst Alice")-[NAND|IMPL <->]->(Object "Annotations")`` are
    written into two separate graphs — route A as TWO operator factor nodes
    sharing the subject (not one n-ary operator: that is a different factor
    shape from two binary edges), route B as two plain direct edges. EP then
    runs once per graph, and all THREE posteriors (the subject and both
    objects, which a mutual direction moves) must agree to within
    :data:`PARITY_TOLERANCE`. EP must also have actually CONSUMED the factors
    (:data:`PARAM_MOVE_FLOOR`) — otherwise parity would be comparing untouched
    priors.

    The fan-out and the mutual direction are what make the direct route's
    per-edge back-message slot (#888 W5) load-bearing; see :data:`DIRECTION`.

    Both graphs are driven to EP's fixed point before the comparison
    (:func:`_solve_to_fixed_point`): a route may reach the same attractor at a
    different number of damped steps, and comparing two stopping points says
    nothing about the routes.
    """
    # Route A — operator route (two binary operators sharing the subject)
    sdk_op = sdk_factory()
    src_op, tgts_op = _seed_entities(sdk_op, ("Datasets", "Annotations"))
    for tgt in tgts_op:
        _write_via_operator_route(sdk_op, src_op, tgt, op_type)
    assert _operator_node_count(sdk_op) == 2
    conf_op = _target_confidence(sdk_op, src_op, *tgts_op)

    # Route B — direct-edge route (two operator-less edges)
    sdk_direct = sdk_factory()
    src_direct, tgts_direct = _seed_entities(sdk_direct,
                                            ("Datasets", "Annotations"))
    for tgt in tgts_direct:
        _write_via_direct_route(sdk_direct, src_direct, tgt, op_type)
    assert _operator_node_count(sdk_direct) == 0
    assert len(_direct_edge_rows(sdk_direct, op_type)) == 2
    conf_direct = _target_confidence(sdk_direct, src_direct, *tgts_direct)

    # Nodes are compared pairwise by ROLE, not by id (the ids are the same in
    # both graphs, but pairing by role keeps the message honest).
    pairs = [(src_op, src_direct), *zip(tgts_op, tgts_direct, strict=True)]
    assert len(pairs) == 3

    # 1. propagation actually happened on BOTH routes (parity is not vacuous):
    #    every posterior must have left Beta(1,1) on at least one parameter.
    for route, confs in (("operator", conf_op), ("direct", conf_direct)):
        for node_id, conf in confs.items():
            moved = max(abs(conf["alpha"] - PRIOR_ALPHA),
                        abs(conf["beta"] - PRIOR_BETA))
            assert moved > PARAM_MOVE_FLOOR, (
                f"{route} route left {node_id!r} at its Beta(1,1) prior "
                f"(alpha={conf['alpha']!r}, beta={conf['beta']!r}) — EP did "
                f"not consume the {op_type} factor, so parity would prove "
                f"nothing")
    if op_type == "NAND":
        for route, confs in (("operator", conf_op), ("direct", conf_direct)):
            for node_id, conf in confs.items():
                assert conf["mean"] < PRIOR_MEAN - PROPAGATION_FLOOR, (
                    f"{route} route did not weaken {node_id!r} under NAND "
                    f"(mean={conf['mean']!r})")

    # 2. the same facts land the same posteriors on both routes
    for op_id, direct_id in pairs:
        c_op, c_direct = conf_op[op_id], conf_direct[direct_id]
        assert abs(c_op["mean"] - c_direct["mean"]) <= PARITY_TOLERANCE, (
            "route parity VIOLATED: the operator route yields "
            f"mean={c_op['mean']!r} and the direct-edge route yields "
            f"mean={c_direct['mean']!r} for the same "
            f"(Subject)-[{op_type} {DIRECTION}]->(Object) fact. The ontology "
            "(§5/#7813) says confidence propagates identically across either "
            "route — do NOT loosen the tolerance; the ENGINE is incomplete.")
        # the Beta parameters behind that mean agree too (not a means-only
        # match — this is what carries the IMPL case, whose mean is symmetric)
        for param in ("alpha", "beta"):
            assert abs(c_op[param] - c_direct[param]) <= PARITY_TOLERANCE, (
                f"{param} diverges for {op_id!r}: operator={c_op[param]!r} "
                f"direct={c_direct[param]!r}")


@pytest.mark.parametrize("op_type", OP_TYPES)
def test_point_bearing_pair_is_reified_on_the_direct_route(sdk_factory,
                                                           op_type: str):
    """The #8499 gate, pinned: a Point-bearing pair is operator-MEDIATED.

    This is why the parity pair above is Point-free. It also documents the
    boundary exactly: #7925 widened the endpoint LABELS on the direct route; it
    did not repeal the structure ruling, so ``(Subject)-[NAND]->(Point)`` still
    becomes an operator (with a warning naming the override) rather than an
    operator-less edge.

    ⚠️ On pre-#7925 code this test fails EARLIER than its own assertions: the
    validator refuses the ``:Subject`` endpoint on a direct-spelled connection
    (the same drift, reached through the reification path), so what it pins is
    the ruling's SURVIVAL through the widening, not the widening itself.
    """
    sdk = sdk_factory()
    src, tgt = _seed_subject_and_point(sdk)
    res = _write_via_direct_route(sdk, src, tgt, op_type)

    assert res["created"]["connections"] == 1
    assert [w for w in res.get("warnings") or []
            if w.startswith("point_mediated_route")], res.get("warnings")
    assert _operator_node_count(sdk) == 1
    assert _direct_edge_rows(sdk, op_type) == [], (
        "a Point-bearing pair must not be written operator-less (#8499)")
