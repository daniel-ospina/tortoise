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

import importlib.util
import json
import os
import pathlib
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
    use exactly that predicate (`ep.py:1456`, `:1804`, `:1870`, `:2579`) while
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


# ── #10137: the point ratio conflates evidence with belief ────────────────

def _ingest_claim(sdk: TortoiseSDK, content: str) -> str:
    """A claim written EXACTLY as ``ingest_v2._write_payload`` writes one:
    kind ``statement``, ``status="draft"``, and — the trap — ``is_episodic=True``
    (``ingest_v2.py:275-290``). The flag is the QUOTA discriminator
    (``quota.py:1046-1050``), not a belief/evidence one; a denominator filtered
    on it would exclude every claim EP stamps (review P0)."""
    return sdk.create_point("statement", content, dedup=False,
                            is_episodic=True, status="draft")["id"]


def _ingest_turn(sdk: TortoiseSDK, i: int) -> None:
    """A turn point, as ``ingest_v2.py:611-618`` writes it: kind ``event`` with
    a ``speaker``, ``is_episodic=True``."""
    sdk.create_point("event", f"[user] turn {i}", dedup=False, speaker="user",
                     is_episodic=True, status="draft")


def _ingest_chunk(sdk: TortoiseSDK, i: int) -> None:
    """A transcript chunk, as ``ingest_v2.py:637-643`` writes it."""
    sdk.create_point("session-transcript", f"chunk {i}", dedup=False,
                     is_episodic=True, status="draft")


def test_the_episodic_substrate_is_not_a_belief_denominator(sdk):
    """WHY the published ``points_with_ep / points`` is a floor, measured.

    Every non-operator point in an eval graph carries ``is_episodic=True`` —
    claims included — so the point-level denominator counts the evidence
    substrate (turn and transcript-chunk points) as if it were belief. No factor
    reaches a substrate node and EP can never write one, yet on a real graph they
    dominate the denominator (2,696 / 27,608 = 9.77% on the 24-question sample).

    The shape at its clearest: three ingest-shaped claims, all three stamped by
    the pass, plus seven substrate nodes it can never reach. The point ratio
    reads 3/10 = 0.30 while every belief-eligible point is covered.

    Asserts on the OBSERVABLE CONTRIBUTION (the published cells), never on a
    message.
    """
    a = _ingest_claim(sdk, "conclusion A")
    b = _ingest_claim(sdk, "premise B")
    c = _ingest_claim(sdk, "premise C")
    sdk.create_operator("IMPL", b, [a])
    sdk.create_operator("IMPL", c, [a])
    for i in range(5):
        _ingest_turn(sdk, i)
    for i in range(2):
        _ingest_chunk(sdk, i)

    manifest = _manifest_of(sdk)

    # the point-level pair is UNCHANGED (its meaning is published history) ...
    assert manifest["points_with_ep"] == 3
    assert manifest["points_unmeasured"] == 7
    assert manifest["points_with_ep"] / (
        manifest["points_with_ep"] + manifest["points_unmeasured"]) == 0.3

    # ... and the belief-eligible pair says what that 0.30 was hiding —
    # with claims that carry the same `is_episodic` flag as the substrate.
    assert manifest["points_substrate"] == 7
    assert manifest["belief_eligible_total"] == 3
    assert manifest["belief_eligible_with_ep"] == 3
    assert manifest["belief_eligible_unmeasured"] == 0

    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "question_type": "single-session-user",
          "belief_activation": manifest}], requested=True)
    assert summary["belief_eligible_ratio"] == 1.0
    assert summary["points_substrate"] == 7
    assert summary["belief_eligible_total_unread"] == 0


def test_the_two_denominators_agree_on_a_graph_with_no_substrate(sdk):
    """The anti-dilution control: with no substrate nodes the belief-eligible
    cells must EQUAL the point-level pair — the split is a refinement of the
    population, not a second, more flattering measurement."""
    a = _ingest_claim(sdk, "conclusion A")
    b = _ingest_claim(sdk, "premise B")
    sdk.create_operator("IMPL", b, [a])

    manifest = _manifest_of(sdk)

    assert manifest["points_substrate"] == 0
    assert manifest["belief_eligible_total"] == (
        manifest["points_with_ep"] + manifest["points_unmeasured"])
    assert manifest["belief_eligible_with_ep"] == manifest["points_with_ep"]


def test_an_unread_eligible_census_publishes_unknown_not_zero(sdk):
    """A census that could not be read is UNKNOWN, never ``0`` — the module's
    one rule. The ratio must follow it: no measured denominator, no ratio, and
    a PARTIAL census must not publish one either (review P1)."""
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "question_type": "t",
          "belief_activation": {"ep_ran": False, "error": "boom"}}],
        requested=True)

    assert summary["belief_eligible_total"] is None
    assert summary["belief_eligible_ratio"] is None
    assert summary["belief_eligible_total_unread"] == 1


def test_a_partial_census_does_not_publish_a_ratio(sdk):
    """A complete-looking 100% over half a population is the fabricated figure
    the ``*_unread`` companions exist to prevent (review P1)."""
    a = _ingest_claim(sdk, "conclusion A")
    b = _ingest_claim(sdk, "premise B")
    sdk.create_operator("IMPL", b, [a])
    measured = _manifest_of(sdk)

    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "question_type": "t",
          "belief_activation": measured},
         {"question_id": "q2", "question_type": "t",
          "belief_activation": {"ep_ran": False, "error": "boom"}}],
        requested=True)

    assert summary["belief_eligible_total_unread"] == 1
    assert summary["belief_eligible_ratio"] is None


def test_a_pre_speaker_turn_is_substrate_and_a_kindless_point_is_neither_hole(sdk):
    """Two shapes the first predicate got wrong (review cycle 2, P1+P2).

    ``speaker`` arrived with delta 5 (#721), so a pre-#721 turn is
    ``pointKind='event'`` + ``is_episodic=true`` with NO speaker — classifying it
    belief-eligible puts turn EVIDENCE back in the denominator this change exists
    to clean (``tortoise/session_reinjection.py:213-220`` already ruled that
    requiring ``speaker`` "would silently exclude legacy turns").

    And ``NOT (NULL = 'x')`` is NULL in Cypher, so a ``:Point`` with no
    ``pointKind`` falls out of BOTH populations unless the predicate is total.
    The partition invariant is the thing a test can hold: every non-operator
    point is substrate OR belief-eligible, never neither.
    """
    a = _ingest_claim(sdk, "conclusion A")
    b = _ingest_claim(sdk, "premise B")
    sdk.create_operator("IMPL", b, [a])
    sdk.create_point("event", "[user] legacy turn", dedup=False,  # no speaker
                     is_episodic=True, status="draft")
    sdk._get_proj().g.query("MERGE (n:Point {id:'legacy-kindless'})")

    manifest = _manifest_of(sdk)
    total = manifest["points_with_ep"] + manifest["points_unmeasured"]

    assert manifest["points_substrate"] == 1          # the pre-speaker turn
    assert manifest["belief_eligible_total"] == 3    # 2 claims + the kindless
    assert manifest["points_substrate"] + manifest["belief_eligible_total"] == total


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


# ── the census PROBE (#10137 review, P1) ────────────────────────────────────
#
# ``probes/census.py`` is the instrument that reads the cells out of a run's
# report OR checkpoint, and until this section it had ZERO coverage: the report
# path, the checkpoint arm-refusal, the ``per_question: null`` fail-closed
# branch and the ``*_unread`` companions were all unexercised, so deleting any
# of them left the whole suite green. The probe lives under a hyphenated,
# digit-leading evidence directory (not importable as a module), so it is
# loaded by PATH — the same file the README's reproduce recipe runs.

_PROBE = (pathlib.Path(__file__).resolve().parents[2]
          / "docs/evidence/10137-corpus-ep-census/probes/census.py")
_ARTIFACT = (pathlib.Path(__file__).resolve().parents[2]
             / "docs/evidence/10137-corpus-ep-census/census.json")


def _probe():
    spec = importlib.util.spec_from_file_location("census_probe_10137", _PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manifest(**over) -> dict:
    """The manifest ``ep_activation.activate_beliefs`` returns per question.

    This default supplies EIGHT of the THIRTEEN census cells the probe's
    per-question projection now reads: ``operators``, ``factor_edges``,
    ``points_with_ep``, ``points_unmeasured``, ``points_off_neutral``,
    ``points_moved_off_neutral``, ``ep_ran``, ``no_op_reason``. The five it omits
    are ``error`` and the four ``points_substrate`` / ``belief_eligible_*`` cells,
    and each omission is load-bearing rather than an oversight: `error: null` is
    what a fail-open manifest looks like, and the four absent belief cells are
    exactly what makes a pre-#10493 manifest project four UNREAD cells rather
    than four measured ones. Callers that need either pass them through
    ``**over``.

    (Before #10493 the projection read NINE — these eight plus ``error``.)
    """
    m = {"ep_ran": True, "ep_engine": "ep", "operators": 171,
         "factor_edges": 0, "points_with_ep": 121, "points_unmeasured": 1038,
         "points_off_neutral": 8, "points_moved_off_neutral": 8,
         "no_op_reason": None}
    m.update(over)
    return m


def _outcomes(n: int = 2, **over) -> list[dict]:
    return [{"question_id": f"q{i}", "question_type": "single-session-user",
             "belief_activation": _manifest(**over)} for i in range(n)]


def _report_doc(outcomes, *, n=None, activation=None) -> dict:
    """A REPORT shape: ``outcomes`` + ``methodology`` (that pair is what routes
    the probe to the report branch). The aggregate is the runner's own, so the
    per-question rows are summed from the same manifests a real report carries."""
    summary = activation if activation is not None else \
        mp.summarize_belief_activation(outcomes, requested=True)
    return {"n_questions": len(outcomes) if n is None else n,
            "outcomes": outcomes,
            "methodology": {"measurement_posture": {"activation": summary}}}


def _write(tmp_path, doc) -> str:
    p = pathlib.Path(tmp_path)
    p.mkdir(parents=True, exist_ok=True)
    f = p / "input.json"
    f.write_text(json.dumps(doc))
    return str(f)


def test_census_probe_reads_per_question_cells_off_a_report(tmp_path):
    """The report branch is the ORDINARY path — a run that finalises writes a
    report, not a checkpoint — and it is the path that used to lose the column."""
    outcomes = _outcomes(2)
    out = _probe().cells(_write(tmp_path, _report_doc(outcomes)))

    assert out["source"] == "report"
    assert out["questions_in_run"] == 2
    assert out["questions"] == 2 and out["questions_with_ep"] == 2
    assert out["questions_with_ep_ratio"] == 1.0
    assert out["belief_layer"] == mp.BELIEF_LAYER_ACTIVATED

    # The per-question block is the artifact's auditability: non-null, one row
    # per outcome, and none of them unread.
    assert out["per_question_source"] == "outcome.belief_activation"
    assert out["per_question_unread"] == 0
    assert [r["question_id"] for r in out["per_question"]] == ["q0", "q1"]
    for row in out["per_question"]:
        assert row["ep_ran"] is True
        assert row["operators"] == 171

    # The two contribution sums ship WITH their ``_unread`` companions — the
    # disclosure that says whether 185 is complete or partial.
    assert out["cells"]["points_off_neutral_unread"] == 0
    assert out["cells"]["points_moved_off_neutral_unread"] == 0


def test_census_probe_refuses_a_report_with_no_posture(tmp_path):
    """No posture recorded is a STATE, not a zero — never a traceback and never
    a census of ``0``."""
    doc = {"n_questions": 1, "outcomes": [], "methodology": {}}
    with pytest.raises(SystemExit, match="NO measurement posture"):
        _probe().cells(_write(tmp_path, doc))


def test_census_probe_refuses_a_checkpoint_with_no_arm(tmp_path):
    """A checkpoint written under another arm must not read as armed: the arm is
    UNKNOWN, not True."""
    doc = {"format": "lme-checkpoint", "outcomes": [{"question_id": "q1"}],
           "failures": []}
    with pytest.raises(SystemExit, match="UNKNOWN, not True"):
        _probe().cells(_write(tmp_path, doc))


def test_census_probe_reads_the_arm_from_a_checkpoint_fingerprint(tmp_path):
    probe = _probe()
    armed = {"fingerprint": {"activate_beliefs": True}, "outcomes": _outcomes()}
    disarmed = {"fingerprint": {"activate_beliefs": False}, "outcomes": _outcomes()}

    assert probe.cells(_write(tmp_path / "a", armed))["belief_layer"] == \
        mp.BELIEF_LAYER_ACTIVATED
    # A disarmed checkpoint carries manifests from elsewhere; it must NOT read
    # as an armed run.
    assert probe.cells(_write(tmp_path / "b", disarmed))["belief_layer"] == \
        mp.BELIEF_LAYER_INERT
    assert probe.cells(_write(tmp_path / "a", armed))["source"] == "checkpoint"


def test_the_probe_reads_the_summarizers_headline_ratio(tmp_path):
    """#10137 indicator (a): ONE derivation of the headline quotient.

    ``summarize_belief_activation`` now publishes ``questions_with_ep_ratio``
    beside the two cells it divides — that is what indicator (a) asks for
    ("published per run") — so a consumer READS the cell instead of dividing
    again. Two derivations of one number is how they disagree, and this one is
    the bundle's headline: it shipped as the one cell a hand could edit
    (#10678 review, P1).

    Non-vacuous: a re-deriving probe publishes ``2 / 2 = 1.0`` here, not the
    sentinel the summarizer's own block carries.
    """
    outcomes = _outcomes(2)
    summary = mp.summarize_belief_activation(outcomes, requested=True)
    assert summary["questions_with_ep_ratio"] == 1.0     # the real quotient
    summary["questions_with_ep_ratio"] = 0.125           # sentinel, READ below

    out = _probe().cells(
        _write(tmp_path, _report_doc(outcomes, activation=summary)))

    assert out["questions_with_ep"] == 2 and out["questions"] == 2
    assert out["questions_with_ep_ratio"] == 0.125


def test_the_probe_falls_back_when_a_summary_carries_no_ratio(tmp_path):
    """An input carrying a summary produced BEFORE the cell existed (an older
    checkpoint, or a foreign one) must still publish the quotient rather than
    ``None`` — the fallback is the same arithmetic, so the two cannot report
    two values for one population."""
    outcomes = _outcomes(2)
    summary = mp.summarize_belief_activation(outcomes, requested=True)
    summary.pop("questions_with_ep_ratio")

    out = _probe().cells(
        _write(tmp_path, _report_doc(outcomes, activation=summary)))

    assert out["questions_with_ep_ratio"] == 1.0


def test_census_probe_publishes_absence_when_no_outcome_has_a_manifest(tmp_path):
    """24 rows of nulls would read as a measured-and-null question; publish the
    absence and where it comes from instead."""
    outcomes = [{"question_id": f"q{i}", "question_type": "single-session-user"}
                for i in range(3)]
    out = _probe().cells(_write(tmp_path, _report_doc(outcomes)))

    assert out["per_question"] is None
    assert out["per_question_source"].startswith("absent")
    assert out["per_question_unread"] == 3


def test_census_probe_calls_an_empty_population_absence_not_provenance(tmp_path):
    """#10137 review (P3): the guard was ``if rows and not manifests``, so an
    input with NO outcomes fell to the ``else`` branch and published
    ``per_question_source: "outcome.belief_activation"`` — provenance claimed
    from an input that has no outcomes at all. Non-vacuous: on the pre-fix
    guard this assertion sees that source string."""
    out = _probe().cells(_write(tmp_path, _report_doc([])))

    assert out["per_question"] is None
    assert out["per_question_source"] != "outcome.belief_activation"
    assert out["per_question_unread"] == 0


def test_census_probe_does_not_fabricate_a_zero_for_an_absent_cell(tmp_path):
    """An ABSENT cell is UNKNOWN, not ``0`` — the rule the module applies to the
    summed cells applies to the headline pair too (``summary.get(...) or 0``
    published a measured zero for a cell this input never had)."""
    activation = {"requested": True, "questions": 2}
    out = _probe().cells(_write(tmp_path, _report_doc(
        _outcomes(2), activation=activation)))

    assert out["questions_with_ep"] is None
    assert out["questions_with_ep_ratio"] is None


def test_census_probe_cells_match_the_committed_artifact_key_set(tmp_path):
    """The committed ``census.json`` is this probe's own output, so their
    ``cells`` blocks must carry the SAME keys in the SAME order — the pin that
    keeps an added disclosure (e.g. the ``*_unread`` companions) from riding
    only one of the two."""
    out = _probe().cells(_write(tmp_path, _report_doc(_outcomes(1))))
    committed = json.loads(_ARTIFACT.read_text())

    assert list(out["cells"]) == list(committed["cells"])
    assert list(out) == list(committed)


def test_census_probe_names_the_ratio_a_point_coverage_floor(tmp_path):
    """The key is ``point_coverage``, NEVER ``claim_coverage`` (#10137 review,
    P2). The denominator counts every non-operator ``Point``, and the v2 ingest
    writes episodic turn points and transcript chunks that can never carry EP
    state, so a key named ``claim_coverage`` asserts a denominator the bundle's
    own README denies ("a **floor**, not a claim-coverage figure").

    Non-vacuous: the pre-fix probe published ``claim_coverage``, so the second
    assertion fails on it."""
    out = _probe().cells(_write(tmp_path, _report_doc(_outcomes(2))))

    assert "point_coverage" in out
    assert "claim_coverage" not in out
    assert out["point_coverage"]["points_with_ep"] == 242


def test_census_probe_publishes_the_belief_eligible_denominator(tmp_path):
    """The artifact publishes the denominator that can actually CARRY a belief.

    ``point_coverage`` is a floor whose dilution is unbounded, because the v2
    ingest writes episodic turn/chunk points that no factor can ever reach.
    #10493 split the population (``points_substrate`` + ``belief_eligible_*``)
    and the posture summary computes ``belief_eligible_ratio`` — but the probe
    publishes cells through an ALLOW-LIST, so the corrected denominator reached
    the manifest and the report and stopped there: the artifact that publishes
    this issue's headline number could not carry it at all (#10493 residual 1).

    Non-vacuous: the pre-fix probe's ``cells`` block has no such keys, so the
    subscript raises ``KeyError``.
    """
    manifest = dict(_manifest(), points_substrate=1000,
                    belief_eligible_total=159, belief_eligible_with_ep=121,
                    belief_eligible_unmeasured=38)
    outcomes = [{"question_id": f"q{i}",
                 "question_type": "single-session-user",
                 "belief_activation": dict(manifest)} for i in range(2)]
    out = _probe().cells(_write(tmp_path, _report_doc(outcomes)))

    cells = out["cells"]
    assert cells["points_substrate"] == 2000
    assert cells["belief_eligible_total"] == 318
    assert cells["belief_eligible_with_ep"] == 242
    assert cells["belief_eligible_unmeasured"] == 76
    assert cells["belief_eligible_ratio"] == pytest.approx(242 / 318)
    # The COMPLETE census, so nothing is withheld as unread.
    assert cells["belief_eligible_total_unread"] == 0
    assert cells["points_substrate_unread"] == 0
    # The floor is still published beside it — the two answer DIFFERENT
    # questions (242/2318 of the whole graph vs 242/318 of the population that
    # can carry a belief), so a test that let them coincide would pass on a
    # probe that had merely renamed one to the other.
    assert out["point_coverage"]["ratio"] == pytest.approx(242 / 2318)
    assert out["point_coverage"]["ratio"] != cells["belief_eligible_ratio"]


def test_census_probe_withholds_the_belief_eligible_ratio_when_unread(tmp_path):
    """A ratio over a population nobody read is the fabricated figure the
    ``*_unread`` companions exist to make impossible (``measurement_posture``
    publishes ``belief_eligible_ratio: None`` for exactly this shape).

    The committed 24-question artifact is this shape: it predates #10493, so
    its runs never measured the eligibility cells. Emitting a ratio there —
    over a zero, or over the point-level pair — would be a measured claim
    about a census that was never taken.
    """
    out = _probe().cells(_write(tmp_path, _report_doc(_outcomes(2))))

    cells = out["cells"]
    assert cells["belief_eligible_total"] is None
    assert cells["belief_eligible_ratio"] is None
    assert cells["belief_eligible_total_unread"] == 2
    assert cells["points_substrate_unread"] == 2


def test_probe_output_re_derives_its_own_belief_eligible_aggregates(tmp_path):
    """The belief-eligible aggregate must be re-derivable from the probe's OWN
    ``per_question`` rows, not only from the input it was handed.

    The aggregate pin re-derives every committed ``cells`` entry from the
    committed ``per_question`` rows. Publishing the new cells in ``cells``
    alone therefore does not close #10493's residual — it reds that pin on a
    CORRECT post-#10493 artifact (``cells.points_substrate = 2000, but the
    committed per_question rows derive None``), which is a false block on the
    very number the pin protects (review cycle 1, P1). The rows carry these
    cells for the same reason the old ones do: an aggregate no row supports is
    a hand-edit waiting to happen.
    """
    manifest = dict(_manifest(), points_substrate=1000,
                    belief_eligible_total=159, belief_eligible_with_ep=121,
                    belief_eligible_unmeasured=38)
    outcomes = [{"question_id": f"q{i}",
                 "question_type": "single-session-user",
                 "belief_activation": dict(manifest)} for i in range(2)]
    out = _probe().cells(_write(tmp_path, _report_doc(outcomes)))

    # Round-trip exactly as ``test_committed_census_rederives_its_own_aggregate``
    # does: probe output in, summarizer out, compare against the aggregate.
    rows = out["per_question"]
    shapes = _row_shapes(out)
    summary = mp.summarize_belief_activation(
        [_rebuild_outcome(r, sh) for r, sh in zip(rows, shapes, strict=True)],
        requested=True)
    for key in ("points_substrate", "belief_eligible_total",
                "belief_eligible_with_ep", "belief_eligible_unmeasured",
                "belief_eligible_ratio"):
        assert summary[key] == out["cells"][key], (
            f"cells.{key} = {out['cells'][key]!r} but the probe's own "
            f"per_question rows derive {summary[key]!r}")
    # Non-vacuous: a row that dropped the cells would derive None here.
    assert out["per_question"][0]["points_substrate"] == 1000


def test_census_probe_counts_a_fail_open_manifest_as_unread(tmp_path):
    """A manifest PRESENT is not a census READ (#10137 review, P2).
    The producer's fail-open shape — ``{"ep_ran": False, "error": ...}``, the
    one ``measurement_posture`` documents — carries a manifest and NO census
    cells. Counting manifests published ``per_question_unread: 0`` over rows
    whose cells are all ``null`` while the SAME input's summed cells published
    ``points_with_ep_unread: 1``: two disclosures of one fact, disagreeing.
    """
    outcomes = [*_outcomes(1), {"question_id": "q-boom",
                              "question_type": "single-session-user",
                              "belief_activation": {"ep_ran": False,
                                                    "error": "boom"}}]
    out = _probe().cells(_write(tmp_path, _report_doc(outcomes)))

    # The two disclosures now agree instead of contradicting each other.
    assert out["per_question_unread"] == 1
    assert out["cells"]["points_with_ep_unread"] == 1
    assert out["cells"]["operators_unread"] == 1
    # ...and the row says WHY it is unread, instead of a bare row of nulls.
    by_qid = {r["question_id"]: r for r in out["per_question"]}
    assert by_qid["q-boom"]["error"] == "boom"
    assert by_qid["q-boom"]["points_with_ep"] is None
    assert by_qid["q0"]["error"] is None


def test_census_probe_discloses_failed_questions_behind_the_ratio(tmp_path):
    """A crashed run's checkpoint must not read as ``N/N`` over its survivors
    (#10137 review, P2). ``summarize_belief_activation`` only ever sees
    ``outcomes``, so the failure population is read from the input itself
    (``run.py`` writes each checkpoint's ``failures`` key; a report carries
    ``n_failed``) and published beside it.

    Non-vacuous: the pre-fix probe reported ``questions_in_run: 2`` for this
    input and no ``questions_failed`` at all — the ablation was invisible."""
    failed = [{"question_id": "q-failed", "error": "reader timeout"},
              {"question_id": "q-failed-2", "error": "ingest died"}]
    ckpt = {"fingerprint": {"activate_beliefs": True},
            "outcomes": _outcomes(2), "failures": failed}
    out = _probe().cells(_write(tmp_path / "ckpt", ckpt))

    assert out["questions_in_run"] == 4         # every question the run covered
    assert out["questions_failed"] == 2
    assert out["questions"] == 2                # the ratio's denominator
    # The disclosure, not a silent 1.00 over survivors: the two counts differ
    # by exactly the failures, so a reader cannot mistake one for the other.
    assert out["questions_in_run"] - out["questions"] == out["questions_failed"]

    # The report arm reads the same population — and the SAME gap — off
    # ``n_failed``. This half is the one that matters: a report is the ordinary
    # path (``report.py:1106`` sets ``n_questions = len(outcomes)``, so an
    # override with it would silently drop the failures here, #10137 review P2),
    # and the report carries no per-entry list in this input, so the count is
    # the only witness to the three missing questions.
    rep = _report_doc(_outcomes(2))
    rep["n_failed"] = 3
    out_rep = _probe().cells(_write(tmp_path / "rep", rep))
    assert out_rep["questions_failed"] == 3
    assert out_rep["questions_in_run"] == 5     # 2 outcomes + 3 unlisted failures
    assert out_rep["questions"] == 2
    assert out_rep["questions_in_run"] - out_rep["questions"] == \
        out_rep["questions_failed"]


def test_census_probe_counts_an_overlapped_qid_once(tmp_path):
    """A qid in BOTH lists is ONE question (#10137 review, P2).

    The overlap is a documented real shape, not a hypothetical: a qid completed
    by one worker and failed by another lands in both lists (``report.py:1407-
    1410``), and ``_merge_checkpoint`` unions the two independently, the
    ``remove_failures`` tombstone being present only when the writer knew it was
    a ``--retry-failed`` re-attempt (``_merge_checkpoint``'s ``remove_failures``
    parameter, honored in its tombstone loop). Counting
    ``len(outcomes) + len(failures)`` would publish this 2-question population
    as a 3-question run, and the gap the README calls "the disclosure" could
    not see it, because the gap was ``questions_failed`` BY CONSTRUCTION.

    A recovered question is not an ablation: it is in the ratio's denominator.

    Non-vacuous, stated accurately (#10137 follow-up review, P3): the probe
    MERGED into ``main`` read ``len(outcomes)``, so ``questions_in_run == 2``
    PASSES on it and this test reds there only through the ABSENT
    ``questions_failed`` key (``KeyError``) — non-vacuous by PRESENCE on that
    code, not by arithmetic. The figures still discriminate arithmetic: a
    summed-lengths implementation publishes 3 here, so ``== 2`` and
    ``questions_failed == 0`` red on it as well.
    """
    overlapped = {"fingerprint": {"activate_beliefs": True},
                  "outcomes": _outcomes(2),
                  "failures": [{"question_id": "q0",
                                "error": "reader timeout"}]}
    out = _probe().cells(_write(tmp_path, overlapped))

    assert out["questions_in_run"] == 2          # not 3
    assert out["questions"] == 2
    assert out["questions_failed"] == 0          # q0 was recovered
    assert out["questions_in_run"] - out["questions"] == \
        out["questions_failed"]


def test_census_probe_keeps_the_disclosure_invariant_on_a_duplicate_qid(tmp_path):
    """``questions_in_run - questions == questions_failed`` holds for EVERY
    input, including one whose outcome ids are not distinct (#10137 review, P3).

    ``summary["questions"]`` is ``len(rows)`` (``measurement_posture.py:355``)
    and the failure ids are distinct ids, so two independent counts made the
    published total SMALLER than the ratio's own denominator (``-1``) for a
    duplicate-qid input. The total is now DERIVED from the denominator plus the
    ablation, so the invariant is structural rather than a coincidence of
    well-formed input. Not reachable from a checkpoint written by current
    ``run.py`` (both lists are built dict-by-qid) — this pins the shape that
    arrives as foreign JSON, which is exactly what a probe reads.

    Non-vacuous, stated accurately (#10137 follow-up review, P3): the merged
    pre-fix REPORT arm read ``n_questions`` — ``len(outcomes)``, = 3 here — so
    ``questions_in_run == 3`` PASSES on it and this test reds there only
    through the ABSENT ``questions_failed`` key (``KeyError``); it is
    non-vacuous by PRESENCE on that code. The ``== 3`` and ``>=`` assertions
    additionally discriminate the variant the structural derivation replaced:
    a total derived from ``len(distinct ids)`` publishes 2.
    """
    dup = [{"question_id": "q0", "question_type": "t",
            "belief_activation": _manifest()},
           {"question_id": "q0", "question_type": "t",
            "belief_activation": _manifest()},
           {"question_id": "q1", "question_type": "t",
            "belief_activation": _manifest()}]
    out = _probe().cells(_write(tmp_path, _report_doc(dup)))

    assert out["questions"] == 3                # the runner's own denominator
    assert out["questions_in_run"] == 3
    assert out["questions_in_run"] - out["questions"] == \
        out["questions_failed"]
    assert out["questions_in_run"] >= out["questions"]


def test_census_probe_treats_a_non_count_cell_as_unread(tmp_path):
    """``operators: true`` is UNREAD, not a measured 1 (#10137 review, P3).

    ``measurement_posture._optional_int`` refuses a ``bool`` and any
    non-numeric value ("a bool is not a count"), so the summed cells count such
    a cell as unread. ``value is None`` alone does not, and that made the two
    disclosures disagree again - the disagreement #10137 review F2 fixed.
    """
    outcomes = [{"question_id": "q-bool", "question_type": "t",
                 "belief_activation": _manifest(operators=True)}]
    out = _probe().cells(_write(tmp_path, _report_doc(outcomes)))

    assert out["cells"]["operators_unread"] == 1
    assert out["per_question_unread"] == 1
    assert out["per_question"][0]["operators"] is True   # published as read


def test_census_probe_does_not_sum_the_two_lengths(tmp_path):
    """The failure population is DISTINCT ids over both lists — never
    ``len(outcomes) + len(failures)``, and never ``len(outcomes)`` (#10137
    review, P2).

    This is the ARITHMETIC discriminator the two shape tests above cannot be
    (#10137 follow-up review, P3 — their published proofs described pre-fix
    code that never shipped). Two outcomes, one qid that BOTH completed and
    failed, one that only failed: the answer is 3. A summed-lengths
    implementation publishes 4 and the merged pre-fix probe
    (``len(outcomes)``) publishes 2, so every candidate rule gives a DIFFERENT
    figure and no single wrong one can pass.
    """
    doc = {"fingerprint": {"activate_beliefs": True},
           "outcomes": _outcomes(2),
           "failures": [{"question_id": "q0", "error": "reader timeout"},
                        {"question_id": "q-failed", "error": "ingest died"}]}
    out = _probe().cells(_write(tmp_path, doc))

    assert out["questions"] == 2
    assert out["questions_failed"] == 1        # only q-failed ablates
    assert out["questions_in_run"] == 3        # not 4 (sum), not 2 (len only)
    assert out["questions_in_run"] - out["questions"] == \
        out["questions_failed"]


def test_census_probe_survives_a_non_hashable_question_id(tmp_path):
    """A ``question_id`` that is a list or an object is a spec-legal JSON
    value (RFC 8259 §3 — only object NAMES are strings) and off this
    producer's contract, so it reaches the probe as hand-built or foreign
    JSON, which is what a probe reads. Hashing it directly ended the probe in a
    bare ``TypeError: unhashable type: 'list'`` (#10137 follow-up review, P3),
    breaking the module's own "a message, not a traceback" contract.

    Non-vacuous: the pre-fix probe raises ``TypeError`` on this input instead
    of publishing a census.
    """
    doc = {"fingerprint": {"activate_beliefs": True},
           "outcomes": [{"question_id": ["q0"], "question_type": "t",
                         "belief_activation": _manifest()}],
           "failures": [{"question_id": {"id": "q0"}}]}
    out = _probe().cells(_write(tmp_path, doc))

    assert out["questions"] == 1
    # Two DIFFERENT non-str ids, so the failure is genuinely an ablation and
    # the probe reports the split rather than dying on the way to it.
    assert out["questions_failed"] == 1
    assert out["questions_in_run"] == 2


def test_census_probe_keys_a_non_str_qid_apart_from_its_repr(tmp_path):
    """A crafted STRING qid must not collide with the ``repr`` of a non-str
    one (#10137 follow-up review, P3).

    ``repr(["q0"])`` is the string ``"['q0']"``, so an untagged ``repr`` key
    — or a bare ``str(...)`` coercion — makes the failure below look like a
    question that already produced an outcome, i.e. a recovered question and
    zero ablation. The crash fix would then have quietly re-created the very
    undercount ``questions_failed`` exists to prevent. The type tag is what
    ``report.py:580 _qid_key`` does for the same reason.

    Non-vacuous: an untagged ``repr`` key yields ``questions_failed == 0``
    here.
    """
    doc = {"fingerprint": {"activate_beliefs": True},
           "outcomes": [{"question_id": ["q0"], "question_type": "t",
                         "belief_activation": _manifest()}],
           "failures": [{"question_id": "['q0']"}]}
    out = _probe().cells(_write(tmp_path, doc))

    assert out["questions_failed"] == 1        # NOT folded into the outcome
    assert out["questions_in_run"] == 2


_CENSUS_CELLS = ("operators", "factor_edges", "points_with_ep",
                 "points_unmeasured", "points_off_neutral",
                 "points_moved_off_neutral", "ep_ran", "no_op_reason",
                 "error",
                 # #10493's belief-eligible population. Carried here as well as
                 # in ``cells`` because the aggregate pin re-derives from the
                 # ROWS: a cell published only in the aggregate is a number no
                 # row supports, and the pin would red on a correct artifact
                 # (review cycle 1, P1).
                 "points_substrate", "belief_eligible_total",
                 "belief_eligible_with_ep", "belief_eligible_unmeasured")


# The two shapes a NEVER-read manifest can take, keyed by the classification the
# summarizer publishes for it. ``measurement_posture.INERT_REASONS`` names both.
_UNREAD_ABSENT = "absent"         # no ``belief_activation`` key at all
_UNREAD_EMPTY = "dict-of-nulls"   # ``"belief_activation": {}``


def _row_shapes(doc, source=None) -> list:
    """One shape per ``per_question`` ROW — ``None`` for a read row, else the
    manifest shape that row came from.

    A ``per_question`` row is LOSSY about exactly one thing. The probe projects
    every outcome through ``(o.get("belief_activation") or {}).get(...)``
    (the ``per_question`` projection in ``probes/census.py``), so an outcome
    carrying NO
    ``belief_activation`` and one carrying ``"belief_activation": {}`` produce a
    byte-identical all-null row — the probe's own ``_census_unread`` calls BOTH
    unread (``census.py::_census_unread``), while
    ``measurement_posture._classify_question`` classifies them DIFFERENTLY:
    ``no_manifest`` for a non-dict (``:235``) and ``activation_failed`` for a
    manifest that carries none of the census cells, e.g. the empty dict
    (``:237``, through ``_failed``'s ``ep_ran is not True``).

    The probe does not leave the reader without the answer, though: it ALSO
    publishes ``inert_questions``, one entry per inert question carrying the
    classification ``reason`` (``measurement_posture:533-545``). That reason is
    what the shape is derived from — per ROW, never guessed for the population.

    A qid's never-read rows are assigned IN ROW ORDER: its ``no_manifest``
    entries are the ABSENT ones (a ``no_manifest`` row is all-null by
    definition, so every such entry IS a never-read row) and the rest are the
    EMPTY-manifest shape, which requires enough remaining
    ``activation_failed`` entries to cover them. That resolves a qid carrying
    BOTH reasons, which is ordinary rather than exotic — a READ fail-open row is
    ``activation_failed`` while a never-read row under the same id is absent.

    ``inert_by_reason`` is NOT the cross-check: that census counts every inert
    question, including READ ones, so comparing it against the never-read rows
    alone refuses an ordinary artifact whose failures and whose resumed pre-arm
    outcomes coexist. ``cells.measured`` is the witness that separates them: a
    dict manifest counts as measured whether or not it was read, so
    ``no_manifest = rows - measured`` and
    ``activation_failed = measured - read_rows``.

    All ``None`` when no row is never-read — the committed sample — which leaves
    every assertion in the aggregate test exactly as strict as it was.
    """
    source = source or _ARTIFACT
    key = _probe()._qid_key
    rows = doc.get("per_question") or []
    shapes: list = [None] * len(rows)

    null_by_qid: dict = {}
    for i, row in enumerate(rows):
        if all(row.get(k) is None for k in _CENSUS_CELLS):
            null_by_qid.setdefault(key(row.get("question_id")), []).append(i)
    if not null_by_qid:
        return shapes

    entries: dict = {}
    for iq in (doc.get("inert_questions") or []):
        if not (isinstance(iq, dict) and "question_id" in iq):
            continue
        counts = entries.setdefault(key(iq["question_id"]),
                                    {mp.INERT_NO_MANIFEST: 0,
                                     mp.INERT_ACTIVATION_FAILED: 0})
        if iq.get("reason") in counts:
            counts[iq["reason"]] += 1

    unresolvable = []
    for k, idxs in null_by_qid.items():
        counts = entries.get(k) or {mp.INERT_NO_MANIFEST: 0,
                                    mp.INERT_ACTIVATION_FAILED: 0}
        absent = counts[mp.INERT_NO_MANIFEST]
        failed = counts[mp.INERT_ACTIVATION_FAILED]
        if absent > len(idxs) or failed < len(idxs) - absent:
            unresolvable.append((rows[idxs[0]].get("question_id"), len(idxs),
                                 absent, failed))
            continue
        for position, i in enumerate(idxs):
            shapes[i] = _UNREAD_ABSENT if position < absent else _UNREAD_EMPTY
    if unresolvable:
        detail = ", ".join(
            f"{str(q)!r}: never_read={n}, no_manifest={a}, "
            f"activation_failed={f}" for q, n, a, f in unresolvable)
        raise AssertionError(
            f"{source}: {sum(len(v) for v in null_by_qid.values())} never-read "
            f"per_question row(s), but inert_questions cannot place "
            f"{sorted({str(q) for q, *_ in unresolvable})!r} as manifest "
            f"shapes ({detail}). A never-read row cannot say which shape it "
            f"came from, so the artifact must — otherwise this pin is "
            f"guessing, which is the defect it exists to prevent")

    # The counts are cross-checked against the RUN-LEVEL witness, NOT against
    # ``inert_by_reason`` (see the docstring).
    measured = mp._optional_int((doc.get("cells") or {}).get("measured"))
    if measured is not None:
        read_rows = [r for r in rows
                     if any(r.get(k) is not None for k in _CENSUS_CELLS)]
        want = {_UNREAD_ABSENT: len(rows) - measured,
                _UNREAD_EMPTY: measured - len(read_rows)}
        derived = [s for s in shapes if s is not None]
        got = {_UNREAD_ABSENT: sum(1 for s in derived
                                  if s == _UNREAD_ABSENT),
               _UNREAD_EMPTY: sum(1 for s in derived if s == _UNREAD_EMPTY)}
        assert got == want, (
            f"{source}: inert_questions places the never-read rows as "
            f"no_manifest={got[_UNREAD_ABSENT]}, "
            f"activation_failed={got[_UNREAD_EMPTY]}, but the run-level "
            f"witness says no_manifest={want[_UNREAD_ABSENT]}, "
            f"activation_failed={want[_UNREAD_EMPTY]} ({len(rows)} row(s), "
            f"cells.measured={measured}, {len(read_rows)} read) — the "
            f"classifications and the counts disagree, so one of them was "
            f"hand-edited or the producer changed and this pin has not been "
            f"told")
    return shapes


def _rebuild_outcome(row, unread_shape: str | None = None) -> dict:
    """Re-project ONE committed ``per_question`` row into the outcome the
    summarizer consumes.

    A read row carries its manifest as a dict of cells. A NEVER-read row (every
    census cell null) cannot distinguish the two shapes a producer can emit —
    no ``belief_activation`` at all, and a manifest carrying none of the census
    cells — because the probe collapses both (see ``_row_shapes``). Choosing
    one for both would be the false claim above, so the shape is an INPUT the
    caller derives from the artifact. A caller that forgets it gets a REFUSAL,
    never the old silent guess: a ``None`` default that fell back to
    manifest-less would re-introduce exactly the defect this change removes,
    for the next person who adds a call site.
    """
    cells = {k: row.get(k) for k in _CENSUS_CELLS}
    outcome = {"question_id": row["question_id"],
               "question_type": row["question_type"]}
    if any(v is not None for v in cells.values()):
        outcome["belief_activation"] = cells
    elif unread_shape is None:
        raise AssertionError(
            f"{row.get('question_id')!r} is a NEVER-read per_question row "
            f"(every census cell null), so its manifest shape must be derived "
            f"from the artifact — pass `_row_shapes(doc)` alongside the row. "
            f"Defaulting to manifest-less would guess, and a guess is what reds "
            f"a correct artifact")
    elif unread_shape == _UNREAD_EMPTY:
        # A manifest that EXISTS and failed. The dict-of-nulls is what
        # ``_failed`` reads as "ep_ran is not True", i.e. activation_failed.
        outcome["belief_activation"] = cells
    return outcome


def test_the_reprojection_shapes_match_the_probe():
    """Both never-read-row shapes, pinned on synthetic rows.

    The committed sample is FULLY read, so neither branch executes against it:
    without these, deleting the dict-of-nulls arm or re-spelling the UNREAD
    predicate left every test green — the "could be reverted with the suite
    still green" shape this bundle exists to avoid (README, "the pin this
    arm was missing until #10137 review").

    Non-vacuous on the point #10678's review caught: the two shapes must
    re-project to DIFFERENT outcomes. Collapsing them (the old single guess)
    reds an artifact whose row came from a manifest carrying none of the census
    cells — the empty dict is the common case, and a hand-built or foreign
    manifest is the same shape.
    """
    unread = {"question_id": "q", "question_type": "t",
              **{k: None for k in _CENSUS_CELLS}}
    absent = _rebuild_outcome(unread, _UNREAD_ABSENT)
    empty = _rebuild_outcome(unread, _UNREAD_EMPTY)

    assert absent == {"question_id": "q", "question_type": "t"}
    assert empty == {"question_id": "q", "question_type": "t",
                     "belief_activation": {k: None for k in _CENSUS_CELLS}}
    assert absent != empty, (
        "_rebuild_outcome collapsed 'no manifest' and 'belief_activation: {}' "
        "into the same outcome — a never-read row cannot express which one it "
        "was, so the shape must come from the artifact, never from a guess")

    # …and the two shapes land on the probe's OWN two classifications.
    assert mp._classify_question(absent.get("belief_activation"))[0] == \
        mp.INERT_NO_MANIFEST
    assert mp._classify_question(empty["belief_activation"])[0] == \
        mp.INERT_ACTIVATION_FAILED

    # A non-numeric cell is UNKNOWN, which is what ``_optional_int`` says and
    # what the probe's ``_census_unread`` says — ``value is None`` says READ.
    assert mp._optional_int(True) is None
    assert mp._optional_int(0) == 0

    # The mirror constant must stay the probe's row key set, or a tenth cell
    # would silently drop out of the re-projection — and it must hold for EVERY
    # row, not just the first: a cell ADDED to any other row has no counterpart
    # in the re-projection and can carry any number, which is the same "a
    # published cell nothing checks" defect closed above for `cells`,
    # `by_question_type`, `point_coverage` and the root (#10678 review).
    doc = json.loads(_ARTIFACT.read_text())
    want_row = set(_CENSUS_CELLS) | {"question_id", "question_type"}
    off = [(i, sorted(set(r) ^ want_row))
           for i, r in enumerate(doc["per_question"]) if set(r) != want_row]
    assert not off, (
        f"{_ARTIFACT}: per_question row(s) {[i for i, _ in off]} carry a cell "
        f"set that is not the probe's own {sorted(want_row)} — an added cell "
        f"has no counterpart in the re-projection and can carry any number")


@pytest.mark.parametrize("never_read", [
    {"question_id": "q2", "question_type": "t"},                    # no key
    {"question_id": "q2", "question_type": "t", "belief_activation": {}},
], ids=["manifest-absent", "manifest-empty"])
def test_a_recut_with_a_never_read_row_is_not_red(tmp_path, never_read):
    """An artifact the probe emits CORRECTLY must not red this pin — the
    latent false RED #10678's review caught (P2).

    The committed sample is fully read, so the defect could not fire on it: it
    was a trap armed for the NEXT re-cut. Both shapes below are projected by
    the probe (the ``per_question`` projection in ``census.py``) to the SAME
    all-null row, so a pin that
    guesses ONE shape for every null row re-derives one of them wrongly and
    reds a correct artifact. Both are pinned, not just the empty one: absence
    is the more likely real shape (a resumed pre-arm checkpoint) and would
    otherwise be dead code.

    Non-vacuous: with the single-guess collapse restored, ``measured``
    re-derives 1 against the artifact's 2 for the empty manifest.
    """
    doc = {"fingerprint": {"activate_beliefs": True},
           "outcomes": [
               {"question_id": "q1", "question_type": "t",
                "belief_activation": _manifest()},
               # The shape under test.
               never_read,
           ],
           "failures": []}
    cut = _probe().cells(_write(tmp_path, doc))

    shapes = _row_shapes(cut, source="a re-cut")
    shape = shapes[1]
    assert shape is not None, (
        f"a re-cut reports inert_by_reason={cut['inert_by_reason']!r} and "
        f"inert_questions={cut['inert_questions']!r} beside "
        f"{cut['per_question_unread']} never-read row(s), but no shape was "
        f"derived for the never-read row")
    derived = mp.summarize_belief_activation(
        [_rebuild_outcome(r, sh) for r, sh in zip(cut["per_question"], shapes, strict=True)],
        requested=True)

    assert derived["measured"] == cut["cells"]["measured"], (
        f"{shape} re-cut says measured={cut['cells']['measured']!r} but the "
        f"rows re-derive {derived['measured']!r} — the re-projection lost the "
        f"manifest state the probe published")
    assert derived["inert_by_reason"] == cut["inert_by_reason"], (
        f"{shape} re-cut says inert_by_reason={cut['inert_by_reason']!r} but "
        f"the rows re-derive {derived['inert_by_reason']!r}")


def test_a_mixed_never_read_population_round_trips(tmp_path):
    """BOTH shapes in ONE artifact must round-trip — the single-guess defect
    was narrower than a uniform population, and a mixed one is ordinary.

    One outcome with no ``belief_activation`` and one carrying an empty
    manifest are both legal producer outputs; resolving ONE shape for the whole
    population reds such an artifact, which shipped green would be a false RED
    on every re-cut containing both.
    """
    doc = {"fingerprint": {"activate_beliefs": True},
           "outcomes": [
               {"question_id": "q1", "question_type": "t",
                "belief_activation": _manifest()},
               {"question_id": "q2", "question_type": "t"},
               {"question_id": "q3", "question_type": "t",
                "belief_activation": {}},
           ],
           "failures": []}
    cut = _probe().cells(_write(tmp_path, doc))

    assert cut["inert_by_reason"] == {mp.INERT_NO_MANIFEST: 1,
                                     mp.INERT_ACTIVATION_FAILED: 1}, (
        f"the fixture must produce BOTH reasons; got "
        f"{cut['inert_by_reason']!r}")
    shapes = _row_shapes(cut, source="a mixed re-cut")
    derived = mp.summarize_belief_activation(
        [_rebuild_outcome(r, sh) for r, sh in zip(cut["per_question"], shapes, strict=True)],
        requested=True)

    assert derived["measured"] == cut["cells"]["measured"], (
        f"mixed re-cut says measured={cut['cells']['measured']!r} but the rows "
        f"re-derive {derived['measured']!r}")
    assert derived["inert_by_reason"] == cut["inert_by_reason"], (
        f"mixed re-cut says inert_by_reason={cut['inert_by_reason']!r} but the "
        f"rows re-derive {derived['inert_by_reason']!r}")


def test_a_failed_question_beside_a_resumed_one_is_not_red(tmp_path):
    """A READ question whose activation FAILED, beside a row with no manifest at
    all (a resumed pre-arm outcome) — the ordinary mixed population that made
    the whole-census cross-check red a correct artifact (#10678 review 3, P2).

    The two inert rows are NOT interchangeable. q2 is a READ question (a
    manifest exists and FAILED): the manifest is a DICT, and a dict counts as
    measured whether or not it was read, so ``cells.measured`` includes it;
    its ``ep_ran``/``error`` cells are what make it a READ row, and
    ``_rebuild_outcome`` rebuilds it from those cells. q3 has
    no manifest at all and is the only row whose census cells are ALL null, so
    only q3 needs a shape from ``inert_questions[].reason``. ``inert_by_reason``
    counts both (2) and ``per_question_unread`` counts both (2, asserted below),
    so neither count IS the never-read set the shape resolver needs. That is why
    the counts are checked against the independent run-level witness
    ``cells.measured`` instead (``no_manifest = rows - measured``,
    ``activation_failed = measured - read_rows``) — ``_row_shapes`` READS that
    witness, it never restores it.
    """
    doc = {"fingerprint": {"activate_beliefs": True},
           "outcomes": [
               {"question_id": "q1", "question_type": "t",
                "belief_activation": _manifest()},
               # READ (a manifest exists) and FAILED, but its CENSUS was never
               # read — ``activation_failed``, not ``no_manifest``.
               {"question_id": "q2", "question_type": "t",
                "belief_activation": {"ep_ran": False, "error": "boom"}},
               # NEVER read — a resumed pre-arm outcome, no manifest at all.
               {"question_id": "q3", "question_type": "t"},
           ],
           "failures": []}
    cut = _probe().cells(_write(tmp_path, doc))

    assert cut["inert_by_reason"] == {mp.INERT_ACTIVATION_FAILED: 1,
                                     mp.INERT_NO_MANIFEST: 1}, (
        f"the fixture must produce BOTH reasons from DIFFERENT populations; "
        f"got {cut['inert_by_reason']!r}")
    # ``per_question_unread`` is 2, not 1: a failed fail-open manifest carries
    # no census cells either, so it is UNKNOWN by the same predicate. That is
    # why the never-read set is computed from the CELLS, never from this count.
    assert cut["per_question_unread"] == 2, cut["per_question_unread"]

    shapes = _row_shapes(cut, source="a failure+resume re-cut")
    derived = mp.summarize_belief_activation(
        [_rebuild_outcome(r, sh) for r, sh in zip(cut["per_question"], shapes, strict=True)],
        requested=True)

    assert derived["measured"] == cut["cells"]["measured"], (
        f"failure+resume re-cut says measured={cut['cells']['measured']!r} but "
        f"the rows re-derive {derived['measured']!r}")
    assert derived["inert_by_reason"] == cut["inert_by_reason"], (
        f"failure+resume re-cut says inert_by_reason="
        f"{cut['inert_by_reason']!r} but the rows re-derive "
        f"{derived['inert_by_reason']!r}")


def test_a_never_read_row_without_a_derived_shape_is_refused():
    """A caller that forgets to derive the shape gets a REFUSAL, never the old
    silent guess (#10678 review 3, P3).

    Non-vacuous: the pre-fix ``unread_shape=None`` default returned a
    manifest-less outcome here, re-introducing the guess for every future call
    site.
    """
    unread = {"question_id": "q", "question_type": "t",
              **{k: None for k in _CENSUS_CELLS}}
    with pytest.raises(AssertionError) as refused:
        _rebuild_outcome(unread)
    message = str(refused.value)
    assert "'q'" in message, f"the refusal must name the row; got: {message}"
    assert "_row_shapes" in message, (
        f"the refusal must name the derivation it wants; got: {message}")


def test_a_census_whose_row_classifications_disagree_with_its_counts_is_refused(tmp_path):
    """The run-level witness BITES: relabelling a never-read row so the reason
    counts no longer match ``cells.measured`` is refused, not accepted.

    Non-vacuous: with the witness disabled every test still passes, which is
    the "could be reverted with the suite still green" shape this bundle exists
    to avoid (README, "reverted with every test still green").
    """
    doc = {"fingerprint": {"activate_beliefs": True},
           "outcomes": [
               {"question_id": "q1", "question_type": "t",
                "belief_activation": _manifest()},
               {"question_id": "q3", "question_type": "t"},
           ],
           "failures": []}
    cut = _probe().cells(_write(tmp_path, doc))
    assert _row_shapes(cut, source="honest")  # the honest cut is accepted

    # A single flip: the counts can no longer be right, because the shape the
    # label claims would make the row MEASURED and ``cells.measured`` says not.
    for entry in cut["inert_questions"]:
        entry["reason"] = mp.INERT_ACTIVATION_FAILED
    with pytest.raises(AssertionError) as refused:
        _row_shapes(cut, source="a relabelled census")
    message = str(refused.value)
    assert "a relabelled census" in message, message
    assert "cells.measured" in message, (
        f"the refusal must name the witness it checked; got: {message}")


@pytest.mark.parametrize("never_read", [
    {"question_id": "q1", "question_type": "t"},
    {"question_id": "q1", "question_type": "t", "belief_activation": {}},
], ids=["absent", "empty"])
def test_a_duplicate_qid_never_read_population_is_counted_per_row(
        tmp_path, never_read):
    """Two never-read ROWS under one ``question_id`` are two rows, not one
    (#10678 review 4, P2).

    A duplicate ``question_id`` is an input shape this file already pins as
    supported (foreign JSON, ``test_census_probe_keeps_the_disclosure_invariant
    _on_a_duplicate_qid``). Counting the shape map collapsed the two rows into
    one and refused a self-consistent artifact as "hand-edited" — a false RED
    whose message blamed a hand-edit.

    Parametrized over BOTH shapes because the collapse and the per-row fix are
    per ARM: a fixture with two ABSENT rows leaves the EMPTY arm untested, and
    the "count the map" mutation then survives on that arm alone (#10678
    review 6, P3).
    """
    doc = {"fingerprint": {"activate_beliefs": True},
           "outcomes": [
               {"question_id": "q0", "question_type": "t",
                "belief_activation": _manifest()},
               never_read,
               dict(never_read),
           ],
           "failures": []}
    cut = _probe().cells(_write(tmp_path, doc))

    absent_shape = never_read.get("belief_activation") is None
    assert len(cut["per_question"]) == 3, len(cut["per_question"])
    # ``measured`` counts dict manifests: an ABSENT row is not measured, an
    # EMPTY one is — which is exactly what makes it the independent witness.
    assert cut["cells"]["measured"] == (1 if absent_shape else 3), (
        cut["cells"]["measured"])
    assert cut["inert_by_reason"] == {
        (mp.INERT_NO_MANIFEST if absent_shape
         else mp.INERT_ACTIVATION_FAILED): 2}, cut["inert_by_reason"]

    shapes = _row_shapes(cut, source="a duplicate-qid re-cut")
    assert shapes[1] == shapes[2] is not None, (
        f"both never-read rows must resolve to the same shape; got {shapes!r}")

    derived = mp.summarize_belief_activation(
        [_rebuild_outcome(r, sh)
         for r, sh in zip(cut["per_question"], shapes, strict=True)],
        requested=True)
    assert derived["measured"] == cut["cells"]["measured"], (
        f"duplicate-qid re-cut says measured={cut['cells']['measured']!r} but "
        f"the rows re-derive {derived['measured']!r}")
    assert derived["inert_by_reason"] == cut["inert_by_reason"], (
        f"duplicate-qid re-cut says inert_by_reason="
        f"{cut['inert_by_reason']!r} but the rows re-derive "
        f"{derived['inert_by_reason']!r}")


def test_a_duplicate_qid_classified_two_ways_is_resolved_in_row_order(tmp_path):
    """One qid whose never-read rows are one ABSENT and one EMPTY is
    DETERMINATE — the artifact's own counts say the split — so it resolves
    instead of being refused (#10678 review 6, P3).

    The per-row assignment is row order: the ``no_manifest`` entries are the
    ABSENT rows and the rest are the EMPTY shape. Non-vacuous: a resolution
    that needs ONE shape per qid refuses this fixture (the round-4 behaviour),
    and one that counts the qid map under-counts the witness.
    """
    doc = {"fingerprint": {"activate_beliefs": True},
           "outcomes": [
               {"question_id": "q0", "question_type": "t",
                "belief_activation": _manifest()},
               {"question_id": "q1", "question_type": "t"},
               {"question_id": "q1", "question_type": "t",
                "belief_activation": {}},
           ],
           "failures": []}
    cut = _probe().cells(_write(tmp_path, doc))

    shapes = _row_shapes(cut, source="a doubly-classified census")
    assert shapes == [None, _UNREAD_ABSENT, _UNREAD_EMPTY], (
        f"a qid carrying one of each shape must resolve per ROW in row order; "
        f"got {shapes!r} for inert_by_reason={cut['inert_by_reason']!r}")

    derived = mp.summarize_belief_activation(
        [_rebuild_outcome(r, sh)
         for r, sh in zip(cut["per_question"], shapes, strict=True)],
        requested=True)
    assert derived["measured"] == cut["cells"]["measured"], (
        f"measured={cut['cells']['measured']!r} but the rows re-derive "
        f"{derived['measured']!r}")
    assert derived["inert_by_reason"] == cut["inert_by_reason"], (
        f"inert_by_reason={cut['inert_by_reason']!r} but the rows re-derive "
        f"{derived['inert_by_reason']!r}")


def test_one_qid_with_a_failed_read_row_and_a_never_read_row_is_not_red(tmp_path):
    """A READ fail-open row and a never-read row under the SAME ``question_id``
    must not be read as an ambiguous qid (#10678 review 5, P2).

    ``inert_questions`` then holds BOTH reasons for that id, and a guard keyed
    on the reason SET refuses a correct artifact — even though the never-read
    row is unambiguous (a ``no_manifest`` row is always all-null, and there is
    exactly one never-read row). The resolution must use the ROW counts.
    """
    doc = {"fingerprint": {"activate_beliefs": True},
           "outcomes": [
               {"question_id": "q1", "question_type": "t",
                "belief_activation": {"ep_ran": False, "error": "boom"}},
               {"question_id": "q1", "question_type": "t"},
           ],
           "failures": []}
    cut = _probe().cells(_write(tmp_path, doc))

    assert cut["inert_by_reason"] == {mp.INERT_ACTIVATION_FAILED: 1,
                                     mp.INERT_NO_MANIFEST: 1}, (
        f"the fixture must give ONE qid both reasons; got "
        f"{cut['inert_by_reason']!r}")
    shapes = _row_shapes(cut, source="one qid, two populations")
    derived = mp.summarize_belief_activation(
        [_rebuild_outcome(r, sh) for r, sh in zip(cut["per_question"], shapes, strict=True)],
        requested=True)

    assert derived["measured"] == cut["cells"]["measured"], (
        f"measured={cut['cells']['measured']!r} but the rows re-derive "
        f"{derived['measured']!r} — the never-read row was mistaken for the "
        f"failed one")
    assert derived["inert_by_reason"] == cut["inert_by_reason"], (
        f"inert_by_reason={cut['inert_by_reason']!r} but the rows re-derive "
        f"{derived['inert_by_reason']!r}")


def test_an_unplaceable_non_str_qid_refuses_with_a_message(tmp_path):
    """An unhashable qid must produce the REFUSAL message, never a bare
    ``KeyError`` (#10678 review 5, P3) — the "a message, not a traceback"
    contract ``_qid_key`` exists for.

    The qid here is a LIST, and the per-row classification is removed so the
    rows are genuinely unplaceable — the refusal must still be a MESSAGE
    naming the qid, never a ``KeyError`` on an unhashable key.
    """
    doc = {"fingerprint": {"activate_beliefs": True},
           "outcomes": [
               {"question_id": ["q1"], "question_type": "t"},
               {"question_id": ["q1"], "question_type": "t",
                "belief_activation": {}},
           ],
           "failures": []}
    cut = _probe().cells(_write(tmp_path, doc))
    cut["inert_questions"] = []     # unplaceable, and the qid is unhashable

    with pytest.raises(AssertionError) as refused:
        _row_shapes(cut, source="a list-qid census")
    message = str(refused.value)
    assert "a list-qid census" in message, message
    assert "cannot place" in message, f"must say why; got: {message}"
    assert "q1" in message, f"must name the qid; got: {message}"


def test_a_census_that_cannot_place_its_never_read_rows_is_refused(tmp_path):
    """When the artifact does NOT say which shape a never-read row came from,
    the pin refuses rather than guessing — and it says which row and why.

    Non-vacuous: a pin that silently falls back to one shape passes this.
    """
    doc = {"fingerprint": {"activate_beliefs": True},
           "outcomes": [
               {"question_id": "q1", "question_type": "t",
                "belief_activation": _manifest()},
               {"question_id": "q2", "question_type": "t"},
           ],
           "failures": []}
    cut = _probe().cells(_write(tmp_path, doc))
    # A hand-edit that drops the per-row classification but keeps the count.
    cut["inert_questions"] = []

    with pytest.raises(AssertionError) as refused:
        _row_shapes(cut, source="a doctored census")
    message = str(refused.value)
    assert "a doctored census" in message, message
    assert "'q2'" in message, (
        f"the refusal must name the row it could not place; got: {message}")
    assert mp.INERT_NO_MANIFEST in message, (
        f"the refusal must name the classifications it accepts; got: {message}")


def test_committed_census_rederives_its_own_aggregate():
    """The shipped artifact is auditable against itself: every aggregate cell
    is what the runner's own aggregation produces from the committed
    per-question rows — so a hand-edit to a cell, or a row that stopped
    reconciling with the totals, reds here.

    ⚠️ This pin is registered in the NON-GATING eval lane
    (``config/ci-surfaces.yml``: ``on_demand``, #10194/#10206), so "reds here"
    is NOT "blocks the merge" — a hand-edit reds when this suite is dispatched
    (``eval-suite-manual.yml``) or run by a lane. The pin's value is that the
    wrong number cannot survive a run, not that it cannot merge.

    Distinct from ``test_census_probe_cells_match_the_committed_artifact_key_set``
    (key set and ORDER only, never a value): keys alone let a cell carry a
    number no row supports.

    ⚠️ Two things are RESIDUALLY un-derived, by the artifact's own limits
    rather than by choice, and this pin cannot close them: the
    ``questions_in_run`` / ``questions_failed`` pair, checked only against each
    other and the ratio (the `questions_in_run - questions ==
    questions_failed` assertion below — cited by SYMBOL, not by line, because a
    line citation in this file is invalidated by any insertion above it),
    because ``per_question`` carries outcomes
    and never the failure ids, so a coordinated edit of both ships green; and
    the per-row shape IDENTITY — ``inert_questions[].reason`` is where the
    shape is read from, so a BALANCED swap of two rows' reasons reproduces
    every count (``measured``/``failed``/``errors`` agree) while mislabelling
    both. ``source`` is NOT in this list: it is pinned as an artifact-identity
    anchor beside the sample pin, not derived. Closing the remainder needs the
    probe to publish the failure identities and the per-row shape — filed as
    #10696, not silently left open.
    """
    doc = json.loads(_ARTIFACT.read_text())
    rows = doc["per_question"]
    # A never-read row does not say WHICH manifest shape it came from; the
    # artifact does, per row, through ``inert_questions``'s ``reason`` — so the
    # shape is derived, never assumed (#10678 review, P2).
    shapes = _row_shapes(doc)
    summary = mp.summarize_belief_activation(
        [_rebuild_outcome(r, sh) for r, sh in zip(rows, shapes, strict=True)],
        requested=True)

    for key, committed in doc["cells"].items():
        # ``key in summary`` first: a summarizer key that was renamed or
        # removed must not compare ``None == None`` and pass.
        assert key in summary, (
            f"{_ARTIFACT}: cells.{key} = {committed!r} has no counterpart in "
            f"the summarizer's own {sorted(summary)} — the artifact names a "
            f"cell nothing derives")
        assert summary[key] == committed, (
            f"{_ARTIFACT}: cells.{key} = {committed!r}, but the committed "
            f"per_question rows derive {summary[key]!r}")

    # The headline cells are DERIVED from the rows, not asserted as literals.
    assert summary["questions"] == doc["questions"]
    assert summary["questions_with_ep"] == doc["questions_with_ep"]

    # …and their QUOTIENT, which is the bundle's HEADLINE (the README quotes
    # it verbatim as "questions_with_ep / questions = 24 / 24 = 1.00", under
    # the heading sentence "The number this issue was opened to produce").
    # Derived HERE, independently of the probe — this pin divides the two
    # cells itself, and ``None`` when the denominator is not a usable count —
    # the same way the sibling ``point_coverage.ratio`` is derived below.
    # (The probe does not divide: since #10137 indicator (a) it READS the
    # summarizer's own ``questions_with_ep_ratio`` cell, so the artifact's
    # value has one producer and this test is that producer's independent
    # check — pinned by
    # ``test_the_probe_reads_the_summarizers_headline_ratio``.) Without this
    # the one number this artifact exists to publish was the one number a hand
    # could edit (#10678 review, P1: ``1.0`` → ``0.5`` shipped green).
    if not doc["questions"]:
        assert doc["questions_with_ep_ratio"] is None, (
            f"{_ARTIFACT}: questions_with_ep_ratio = "
            f"{doc['questions_with_ep_ratio']!r} but the denominator "
            f"questions = {doc['questions']!r}, so the probe derives None")
    else:
        expected_ratio = doc["questions_with_ep"] / doc["questions"]
        assert doc["questions_with_ep_ratio"] == pytest.approx(expected_ratio), (
            f"{_ARTIFACT}: questions_with_ep_ratio = "
            f"{doc['questions_with_ep_ratio']!r} but "
            f"questions_with_ep/questions = {doc['questions_with_ep']}/"
            f"{doc['questions']} derives {expected_ratio!r}")

    # ``errors`` is the summarizer's FIRST failure signal and the probe
    # publishes it (``summary.get("errors")`` in ``probes/census.py``). On this
    # sample every committed row carries ``error: null``, so the only correct
    # value is the committed empty list — an artifact naming a failure beside
    # ``cells.failed == 0`` used to ship with nothing reading it (#10678 review,
    # P2).
    #
    # Citations INTO ``probes/census.py`` name a SYMBOL, never a line: a line
    # citation there is invalidated by any insertion in that file, and the
    # insertion that invalidated the citations above was this change's own — in
    # ``census.py``. (This file changed too, but an insertion here cannot shift
    # a citation into ``census.py``. Citations into OTHER files stay by line.)
    assert summary["errors"] == doc["errors"], (
        f"{_ARTIFACT}: errors = {doc['errors']!r}, but the committed "
        f"per_question rows derive {summary['errors']!r}")
    assert summary["inert_by_reason"] == doc["inert_by_reason"]
    # …the enumerated EP-inert population beside them (the input the issue
    # names for any fix), and the completed set the counts describe.
    assert summary["inert_questions"] == doc["inert_questions"]
    assert doc["completed"] == [{"question_id": r["question_id"],
                                 "question_type": r["question_type"]}
                                for r in rows]
    # …including the state published beside the ratio, and the failure
    # population the ratio is scoped to.
    assert mp.belief_layer_state(requested=True, activation=summary) == \
        doc["belief_layer"]
    assert doc["questions_in_run"] - doc["questions"] == doc["questions_failed"]

    # ``point_coverage.ratio`` is derived too — its two numerators sit in
    # ``cells`` (pinned above), but the ratio would otherwise be editable.
    coverage = doc["point_coverage"]
    assert coverage["points_with_ep"] == doc["cells"]["points_with_ep"]
    assert coverage["points_unmeasured"] == doc["cells"]["points_unmeasured"]
    assert coverage["ratio"] == pytest.approx(
        coverage["points_with_ep"]
        / (coverage["points_with_ep"] + coverage["points_unmeasured"]))

    # Reconciled on the cells the artifact rests on, NOT the bucket SHAPE:
    # ``summarize_belief_activation`` adds keys additively by contract, so a
    # shape pin would red an additive summarizer change that breaks nothing —
    # and could only be repaired by re-cutting the artifact.
    reconciled = ("questions", "questions_with_ep", "questions_off_neutral",
                  "questions_inert", "operators", "operators_unread",
                  "factor_edges", "factor_edges_unread",
                  "points_off_neutral", "points_moved_off_neutral",
                  # The five per-type cells that sit beside the ten above in
                  # every bucket. They were published and unchecked, so a
                  # bucket could ship ``questions_neutral: 3`` while its own
                  # ``questions_off_neutral`` said 4 (#10678 review, P2).
                  "questions_measured", "questions_neutral",
                  "questions_contribution_unknown",
                  "points_off_neutral_unread",
                  "points_moved_off_neutral_unread")
    for qtype, committed in doc["by_question_type"].items():
        derived = summary["by_question_type"][qtype]
        # A cell the artifact ADDS beside the reconciled ones is how the five
        # bucket cells (and ``errors``) shipped editable: the summarizer grows
        # keys additively by contract, so ``reconciled`` cannot enumerate the
        # future. Comparing against what the summarizer actually emits keeps
        # that property — an additive summarizer change adds the key to
        # ``derived`` only and still passes — while a FABRICATED committed key
        # has no counterpart and reds, exactly as the top-level ``cells`` loop
        # already refuses one (#10678 review).
        unexplained = sorted(set(committed) - set(derived))
        assert not unexplained, (
            f"{_ARTIFACT}: by_question_type[{qtype!r}] names cell(s) "
            f"{unexplained} that nothing derives — a published cell nothing "
            f"checks can carry any number")
        pair = {k: committed.get(k) for k in reconciled}
        other = {k: derived.get(k) for k in reconciled}
        disagreement = sorted(k for k in reconciled if pair[k] != other[k])
        assert not disagreement, (
            f"{_ARTIFACT}: by_question_type[{qtype!r}] disagrees with the "
            f"committed rows on {disagreement} — artifact="
            f"{ {k: pair[k] for k in disagreement} }, derived="
            f"{ {k: other[k] for k in disagreement} }")
    # The bucket SET is derivable from the rows, so it is pinned even though
    # the bucket SHAPE is not: deleting a whole question type is a hand-edit
    # the loop above would otherwise skip silently.
    assert set(summary["by_question_type"]) == set(doc["by_question_type"])

    # Same guard for the two other published dicts: a cell ADDED to one of them
    # is unchecked by everything above. ``point_coverage``'s three keys are all
    # derived just below, so its key set is exact; the document root carries the
    # derivable cells PLUS the declared anchors below (each asserted on its own
    # line in this test), and anything outside both is a published cell nothing
    # derives.
    assert set(coverage_doc := doc["point_coverage"]) == {
        "points_with_ep", "points_unmeasured", "ratio"}, (
        f"{_ARTIFACT}: point_coverage names cell(s) "
        f"{sorted(set(coverage_doc) - {'points_with_ep', 'points_unmeasured', 'ratio'})} "
        f"that nothing derives")
    _root_anchors = {
        "source", "per_question", "per_question_source",
        "per_question_unread", "point_coverage", "cells", "errors",
        "inert_by_reason", "inert_questions", "by_question_type",
        "completed", "belief_layer", "questions_with_ep_ratio",
        "questions_in_run", "questions_failed",
    }
    unanchored = sorted(set(doc) - set(summary) - _root_anchors)
    assert not unanchored, (
        f"{_ARTIFACT}: names root cell(s) {unanchored} that neither the "
        f"summarizer derives nor this pin asserts — a published cell nothing "
        f"checks is how the headline ratio shipped editable (#10678 review)")

    # The disclosure fields describe THESE rows: nothing was unread. UNREAD is
    # ``_optional_int(cell) is None`` — the predicate the probe's
    # ``_census_unread`` and the summarizer's own ``*_unread`` sums both use over
    # ``_UNKNOWN_PER_MANIFEST_KEYS`` — not ``value is None``, which
    # would call a non-numeric cell (``operators: true``) READ here while its
    # ``cells.operators_unread`` companion calls it UNREAD.
    assert doc["per_question_source"] == "outcome.belief_activation"
    assert doc["per_question_unread"] == sum(
        1 for r in rows
        if any(mp._optional_int(r.get(k)) is None
               for k in mp._UNKNOWN_PER_MANIFEST_KEYS))
    for key in ("points_with_ep", "points_unmeasured", "operators",
                "factor_edges"):
        assert doc["cells"][f"{key}_unread"] == sum(
            1 for r in rows if mp._optional_int(r.get(key)) is None)

    # ``no_op_reason`` is the eighth published cell, it is part of
    # ``_CENSUS_CELLS``, and the README makes a claim about it a reader relies
    # on (the README's "in all 24 because no question" — the null is the
    # measured value, not a missing one). Nothing derived it: every committed
    # row has ``points_with_ep > 0``, so ``_classify_question`` never consults
    # the column, and a write of ``"no_ep_factors"`` onto all 24 rows shipped
    # GREEN — a row claiming the EP pass wrote nothing beside
    # ``points_moved_off_neutral: 185`` and ``belief_layer: activated``. That is
    # the same self-contradiction as ``stale_run_guard``, and the one bundle
    # whose stated question is whether the layer DID anything (#10678 review).
    for r in rows:
        if (mp._optional_int(r.get("points_moved_off_neutral")) or 0) > 0:
            assert r.get("no_op_reason") is None, (
                f"{_ARTIFACT}: per_question[{r.get('question_id')!r}] moved "
                f"{r.get('points_moved_off_neutral')!r} point(s) off the "
                f"neutral mean but names a no_op_reason "
                f"{r.get('no_op_reason')!r} — a pass that moved points cannot "
                f"also report that it wrote nothing (README, \"in all 24 because"
                f" no question\")")

    # ONE artifact-identity anchor: the sample this bundle was cut from.
    # Update together with the artifact, never on its own. ``source`` sits here
    # for the same reason and not in the derivation list: the report and
    # checkpoint branches emit an identical output shape, so which input
    # produced this file is not derivable from the file — it is an assertion
    # about provenance, and it must not be editable while it looks checked.
    assert doc["source"] == "checkpoint", (
        f"{_ARTIFACT}: source = {doc['source']!r}, but this artifact was cut "
        f"from the run's checkpoint (the README's provenance-note section "
          f"pins that)")
    assert (len(rows), summary["operators"]) == (24, 1710), \
        "pinned sample — update together with the artifact"
