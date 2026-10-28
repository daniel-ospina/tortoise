"""#10137 — the CONTRIBUTION census, against a REAL graph.

``tools/longmem_eval/ep_activation.py`` grew four cells so a run can say
whether the belief layer *did* anything, not merely whether it *ran*:

* ``operators`` — graph census of operator nodes;
* ``points_off_neutral`` — claims whose persisted posterior is not the
  neutral ``0.50`` of the unmeasured ``Beta(1,1)`` prior;
* ``factors`` — the operators THIS pass selected (dream's ``budget_used``);
* ``no_op_reason`` — the SDK's own diagnosis of a pass that wrote nothing.

Two of those are NEW Cypher queries, and this file exists because a broken
census query fails SILENTLY: a count maps an absent result set to ``0`` while a
claim-state read maps it to an empty set, so a query that never matches reports
"this graph has no operators" and "nothing moved" on every graph — a zero
indistinguishable from the defect being measured. The production reads therefore
distinguish UNKNOWN from ``0`` (``_scalar_or_none``), and these end-to-end pins
catch a wrong predicate, epsilon or neutral mean that the stub-based coverage in
``test_ep_activation.py`` cannot.

The three graphs are the #10137 populations, measured on the issue:

1. drafts only, no operator — EP can never reach a claim (structural);
2. IMPL over the uniform priors — COVERED (every claim stamped) and yet every
   posterior is exactly ``0.50``, the uniform prior being that model's fixed
   point. This is the "covered but inert" shape the issue does not name, and
   the one a "belief-activated" claim could be published from;
3. the same graph with a real source baseline — a genuine move (the
   anti-inert control: the census is not hard-wired to zero).

Graph construction mirrors ``tests/test_dream_noop_3139.py`` (embedded by
db-path; a URI lane redirects it to the server, and the assertions hold
either way).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import pytest  # noqa: I001

from tortoise.sdk import TortoiseSDK
from tools.longmem_eval import ep_activation as epa
from tools.longmem_eval import measurement_posture as mp


@pytest.fixture
def sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_10137_"),
                           "test.db")
    s = TortoiseSDK(db_path)
    yield s
    s.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _claim(sdk: TortoiseSDK, content: str, *, status: str = "live") -> str:
    return sdk.create_point("statement", content, dedup=False,
                            status=status)["id"]


def _impl_pair(sdk: TortoiseSDK) -> tuple[str, str, str]:
    """Two IMPL operators over three live claims, no source evidence: the
    uniform ``Beta(1,1)`` priors are the model's fixed point."""
    a = _claim(sdk, "conclusion A")
    b = _claim(sdk, "premise B")
    c = _claim(sdk, "premise C")
    sdk.create_operator("IMPL", b, [a])
    sdk.create_operator("IMPL", c, [a])
    return a, b, c


def _manifest_of(sdk: TortoiseSDK) -> dict:
    return epa.activate_beliefs(sdk)


# ── population 1: no operator at all ──────────────────────────────────────

def test_operatorless_graph_is_structural_and_diagnosed(sdk):
    """No operator ⇒ no factor ⇒ nothing EP does can reach a claim. The
    census must say so, and the SDK's own reason must ride along."""
    for i in range(3):
        _claim(sdk, f"draft claim {i}", status="draft")

    manifest = _manifest_of(sdk)

    assert manifest["ep_ran"] is True          # the pass really ran
    assert manifest["operators"] == 0          # ...and there was nothing to run
    assert manifest["factor_edges"] == 0       # no direct factor edge either
    assert manifest["points_with_ep"] == 0     # nothing stamped
    assert manifest["points_off_neutral"] == 0
    assert manifest["points_moved_off_neutral"] == 0
    assert manifest["factors"] == 0            # this pass selected nothing
    assert manifest["no_op_reason"] == "no_ep_factors"

    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "question_type": "single-session-user",
          "belief_activation": manifest}], requested=True)
    assert summary["inert_questions"] == [
        {"question_id": "q1", "question_type": "single-session-user",
         "reason": mp.INERT_NO_OPERATORS}]
    assert mp.build(ingest_mode="v2",
                    activation=summary)["belief_layer"] == \
        mp.BELIEF_LAYER_PARTIAL


# ── population 2: covered, and the belief layer moved NOTHING ─────────────

def test_covered_graph_over_uniform_priors_reads_zero_off_neutral(sdk):
    """The shape that makes ``questions_with_ep / questions`` insufficient:
    every claim carries a posterior, every posterior is the neutral 0.50."""
    _impl_pair(sdk)
    _claim(sdk, "unrelated draft", status="draft")

    manifest = _manifest_of(sdk)

    assert manifest["operators"] == 2          # the factors EXIST
    # The factors here are OPERATOR-MEDIATED (premise→op→conclusion), and the
    # census counts only CLAIM→CLAIM edges — so it reads 0 even though factors
    # exist. That is the #888 W5 distinction the cell exists for: a 0 here plus
    # operators==0 is what makes a graph structural, and an operator-mediated
    # edge must not be mistaken for a direct one.
    assert manifest["factor_edges"] == 0
    assert manifest["factors"] == 2            # ...and this pass used them
    assert manifest["points_with_ep"] == 3     # full coverage
    assert manifest["points_off_neutral"] == 0  # zero contribution
    assert manifest["points_moved_off_neutral"] == 0
    assert manifest["no_op_reason"] is None    # it was NOT a no-op

    # The recorded bar is deliberately unchanged (an open owner decision):
    # coverage still reads `activated` — and the contribution cells beside it
    # are what say the belief layer moved nothing.
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "question_type": "multi-session",
          "belief_activation": manifest}], requested=True)
    assert mp.build(ingest_mode="v2",
                    activation=summary)["belief_layer"] == \
        mp.BELIEF_LAYER_ACTIVATED
    assert summary["questions_off_neutral"] == 0
    assert summary["neutral_questions"] == [
        {"question_id": "q1", "question_type": "multi-session"}]


# ── population 3: the anti-inert control ──────────────────────────────────

def test_a_real_source_baseline_moves_the_census(sdk):
    """The census is not hard-wired to zero: the same graph with a source
    baseline moves all three claims off the neutral mean."""
    _, b, _c = _impl_pair(sdk)
    sdk.set_point_baseline(b, 6.0, 2.0)

    manifest = _manifest_of(sdk)

    assert manifest["operators"] == 2
    assert manifest["factor_edges"] == 0         # operator-mediated, not direct
    assert manifest["points_with_ep"] == 3
    assert manifest["points_off_neutral"] == 3
    # `b`'s baseline was set BEFORE the pass, so only the two claims the pass
    # actually moved count as contribution — the pre-existing prior does not.
    assert manifest["points_moved_off_neutral"] == 2

    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": manifest}],
        requested=True)
    assert summary["questions_off_neutral"] == 1
    assert summary["points_off_neutral"] == 3
    assert summary["points_moved_off_neutral"] == 2
    assert summary["neutral_questions"] == []


def test_a_direct_claim_to_claim_factor_edge_is_counted(sdk):
    """The ``factor_edges`` census needs a POSITIVE end-to-end pin.

    Every other real-graph case asserts ``== 0`` — and ``0`` is equally the
    answer of a correct query and of one that matches nothing
    (``... WHERE false RETURN count(r)``), which is exactly the silent-zero
    failure the module's ``_scalar_or_none`` guards against at the read layer
    but which the END-TO-END layer cannot see. This cell is not decorative: it
    is what separates ``INERT_NO_OPERATORS`` ("structural — nothing EP can do
    will help", pointing a fix at extraction) from ``no_ep_factors`` (an
    arm/pass cause), so a dead query would route the whole #10137 population
    at the wrong component.

    A direct claim→claim ``IMPL`` edge is a factor with NO operator node, which
    is the one shape that tells the two censuses apart.
    """
    a = _claim(sdk, "conclusion A")
    b = _claim(sdk, "premise B")
    # premise → conclusion, typed directly: `create_edge` only accepts the
    # STRUCTURAL predicates, and IMPL/NAND are written by the operator path or
    # by the graph itself (the same raw write `tests/test_event_provenance.py`
    # uses for its own direct IMPL edge).
    sdk._get_proj().g.query(
        "MATCH (p:Point {id:$premise}), (c:Point {id:$conclusion}) "
        "CREATE (p)-[:IMPL]->(c)",
        params={"premise": b, "conclusion": a},
    )

    manifest = _manifest_of(sdk)

    assert manifest["operators"] == 0   # no operator NODE exists
    assert manifest["factor_edges"] == 1  # ...but a direct factor does

    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": manifest}],
        requested=True)
    assert mp.INERT_NO_OPERATORS not in [
        e["reason"] for e in summary["inert_questions"]]


def test_a_hybrid_op_type_claim_reads_operator_shaped_not_structural(sdk):
    """The DOCUMENTED consequence of copying the engine's operator predicate.

    ``is_operator = true OR op_type IS NOT NULL`` also admits a hybrid
    ``create_point(op_type=…)`` CLAIM — the engine's operator-side selections
    use exactly that predicate (`ep.py:1456`, `:1804`, `:1870`, `:2573`) while
    ``ep._non_operator`` admits the same node as a CLAIM on the participant
    side, so one node can be in both populations. The narrow
    ``is_operator IS NULL AND op_type IS NOT NULL`` spelling was REJECTED (it
    would make the census disagree with the engine's own selection); this pin
    exists so the choice is intentional and a future editor taking the narrower
    spelling has to change a test that says why.

    Consequence: an edge-less hybrid reads ``operators: 1`` + ``factor_edges:
    0`` → ``no_ep_factors`` ("operators exist, none reached the window"), NOT
    the structural ``no_operators``. Non-structural is the safe side for a
    verdict that routes a fix.
    """
    sdk.create_point("statement", "hybrid claim", dedup=False,
                     status="live", op_type="IMPL")

    manifest = _manifest_of(sdk)

    assert manifest["operators"] == 1     # operator-SHAPED, per the engine
    assert manifest["factor_edges"] == 0  # ...but no factor reaches a claim

    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": manifest}],
        requested=True)
    assert [e["reason"] for e in summary["inert_questions"]] == [
        mp.INERT_NO_EP_FACTORS]
    # ...and that reason is NOT the structural one, so the verdict routes a fix
    # at the arm/pass, never at extraction.
    assert mp.INERT_NO_EP_FACTORS not in mp.INERT_REASONS_STRUCTURAL


def test_an_outdated_endpoint_edge_still_counts_as_a_shape_factor(sdk):
    """The `factor_edges` census deliberately counts SHAPE, not consumability.

    It does not carry `ep.py`'s direct-edge conjuncts `epistemic_disjunction()`
    and `_live_only(status)` (`ep.py:1939-1947`), so an `IMPL` whose endpoint is
    `outdated` — an edge EP correctly refuses to consume — still counts. The
    consequence is pinned here because it is the SAFE direction, not an
    oversight: `no_operators` is a PERMANENT verdict ("nothing EP does can ever
    help"), and reviving an outdated claim makes this edge a factor again, so
    the graph must not be called structural. It reads `no_ep_factors` ("operators
    exist, none reached the window") — true today, and non-structural.

    If someone later adds the liveness conjuncts to the census, this test reds
    and forces the `stale_factor` decision (a new reason + its own cell) rather
    than silently re-routing the population at extraction.
    """
    conclusion = _claim(sdk, "conclusion A")
    premise = _claim(sdk, "premise B", status="outdated")
    sdk._get_proj().g.query(
        "MATCH (p:Point {id:$premise}), (c:Point {id:$conclusion}) "
        "CREATE (p)-[:IMPL]->(c)",
        params={"premise": premise, "conclusion": conclusion},
    )

    manifest = _manifest_of(sdk)

    assert manifest["operators"] == 0     # no operator node
    assert manifest["factor_edges"] == 1  # the edge EXISTS, even though EP
                                          # cannot consume an outdated premise

    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": manifest}],
        requested=True)
    reason = summary["inert_questions"][0]["reason"]
    assert reason == mp.INERT_NO_EP_FACTORS
    assert reason not in mp.INERT_REASONS_STRUCTURAL   # never "for ever"


def test_the_census_agrees_with_the_canonical_read(sdk):
    """The off-neutral count is derived FROM ``read_confidence`` over the
    graph's own rows, so the two surfaces cannot disagree.

    ⚠️ The expectation here is deliberately INDEPENDENT of the module's own
    constants and helpers (#10137 review, P2): re-querying with
    ``epa._SELECT_CLAIM_EP_STATES`` and re-counting through
    ``epa.read_confidence`` made this a tautology, so a wrong WHERE predicate,
    a wrong epsilon or a wrong neutral mean all still passed. Here the rows
    come from a hand-written Cypher query and the arithmetic from this test.
    """
    a, b, c = _impl_pair(sdk)
    sdk.set_point_baseline(b, 6.0, 2.0)
    manifest = _manifest_of(sdk)

    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) "
        "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "AND n.posterior_alpha IS NOT NULL "
        "RETURN n.id, n.posterior_alpha, n.posterior_beta").result_set
    off = set()
    for row in rows:
        pid, pa, pb = row[0], row[1], row[2]
        if not pid or pa is None or pb is None:
            continue
        alpha, beta = float(pa), float(pb)
        if alpha + beta <= 0:
            continue
        mean = alpha / (alpha + beta)
        if abs(mean - 0.5) > 1e-6:
            off.add(str(pid))

    assert off == {a, b, c}                     # hand-derived, literal
    assert manifest["points_off_neutral"] == len(off) == 3
    # `b` was already off-neutral before the pass; only `a`/`c` are its doing.
    assert manifest["points_moved_off_neutral"] == 2
