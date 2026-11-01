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
    """The manifest ``ep_activation.activate_beliefs`` returns per question —
    and the exact shape ``census.py`` reads its eight cells off."""
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
    own README denies ("a floor, not a claim-coverage figure").

    Non-vacuous: the pre-fix probe published ``claim_coverage``, so the second
    assertion fails on it."""
    out = _probe().cells(_write(tmp_path, _report_doc(_outcomes(2))))

    assert "point_coverage" in out
    assert "claim_coverage" not in out
    assert out["point_coverage"]["points_with_ep"] == 242


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
    (``run.py:2797`` writes ``failures``; a report carries ``n_failed``) and
    published beside it.

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
    a ``--retry-failed`` re-attempt (``run.py:2699-2729``). ``len(outcomes) +
    len(failures)`` published a 2-question population as a 3-question run — and
    the gap the README calls "the disclosure" could not see it, because the gap
    was ``questions_failed`` BY CONSTRUCTION.

    A recovered question is not an ablation: it is in the ratio's denominator.
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

    ``summary["questions"]`` is ``len(rows)`` (``measurement_posture.py:353``)
    and the failure ids are distinct ids, so two independent counts made the
    published total SMALLER than the ratio's own denominator (``-1``) for a
    duplicate-qid input. The total is now DERIVED from the denominator plus the
    ablation, so the invariant is structural rather than a coincidence of
    well-formed input. Not reachable from a checkpoint written by current
    ``run.py`` (both lists are built dict-by-qid) — this pins the shape that
    arrives as foreign JSON, which is exactly what a probe reads.

    Non-vacuous: with ``questions_in_run = len(distinct ids)`` this asserts
    ``2 == 3``.
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
