"""#10137 — the CONTRIBUTION census, against a REAL graph.

``tools/longmem_eval/ep_activation.py`` grew four cells so a run can say
whether the belief layer *did* anything, not merely whether it *ran*:

* ``operators`` — graph census of operator nodes;
* ``points_off_neutral`` — claims whose persisted posterior is not the
  neutral ``0.50`` of the unmeasured ``Beta(1,1)`` prior;
* ``factors`` — the operators THIS pass selected (dream's ``budget_used``);
* ``no_op_reason`` — the SDK's own diagnosis of a pass that wrote nothing.

Two of those are NEW Cypher queries, and this file exists because a broken
census query fails SILENTLY: ``_scalar`` maps an absent result set to ``0``,
so a query that never matches would report "this graph has no operators" and
"nothing moved" on every graph — a zero indistinguishable from the defect
being measured. Only a real database can tell the two apart, so the stub-based
coverage in ``test_ep_activation.py`` (query shape, guards, degradation) is
paired with these end-to-end pins.

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
    assert manifest["points_with_ep"] == 0     # nothing stamped
    assert manifest["points_off_neutral"] == 0
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
    assert manifest["factors"] == 2            # ...and this pass used them
    assert manifest["points_with_ep"] == 3     # full coverage
    assert manifest["points_off_neutral"] == 0  # zero contribution
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
    assert manifest["points_with_ep"] == 3
    assert manifest["points_off_neutral"] == 3

    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": manifest}],
        requested=True)
    assert summary["questions_off_neutral"] == 1
    assert summary["points_off_neutral"] == 3
    assert summary["neutral_questions"] == []


def test_the_census_agrees_with_the_canonical_read(sdk):
    """The off-neutral count is derived FROM ``read_confidence`` over the
    graph's own rows, so the two surfaces cannot disagree."""
    _, b, _c = _impl_pair(sdk)
    sdk.set_point_baseline(b, 6.0, 2.0)
    manifest = _manifest_of(sdk)

    rows = sdk._get_proj().g.query(
        epa._SELECT_CLAIM_EP_STATES).result_set
    by_read = 0
    for row in rows:
        cells = list(row) + [None] * (5 - len(row))
        mean = epa.read_confidence({"posterior_alpha": cells[1],
                                    "posterior_beta": cells[2],
                                    "ep_alpha": cells[3],
                                    "ep_beta": cells[4]})
        if mean is not None and abs(mean - epa.NEUTRAL_MEAN) > epa.NEUTRAL_EPS:
            by_read += 1
    assert manifest["points_off_neutral"] == by_read == 3
