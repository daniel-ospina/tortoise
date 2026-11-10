"""#11702 — a claim with NO belief state must not be scored as confidence 0.5.

The defect: a claim with no persisted belief answered the neutral Beta(1,1)
mean 0.5 on every read/rank surface, so "we have not computed this" and "a
measured symmetric coin flip (α = β)" were the SAME number. `0.5` is the
maximum-entropy value, i.e. it reads as mid-strength evidence — a no-evidence
claim sorted between genuinely-disproved and genuinely-supported ones, and any
threshold comparing `confidence` let it through as if it carried neutral
evidence.

The recorded rule this file pins is `docs/architecture/SEARCH-ARCHITECTURE.md`
§6.3 (merged, PR #11481):

    absent state is `null` with `ep_state: "absent"`, never 0.5 — a value the
    ranker refuses to order on.

The distinction is PRESENCE, not value: a claim that really is at 0.5 because
α = β keeps its 0.5. Both directions are pinned below, because a fix that
nulls every 0.5 would satisfy half of this file and destroy the other half.

These tests are written against the PROPERTY (no surface reports or orders on a
belief that does not exist) rather than against a call site, so a new ranker or
a re-introduced `coalesce(…, 0.5)` is caught by the same assertions.

**Coverage, stated exactly** (so a later lane does not read this file as
covering more than it does): the surfaces pinned here are
`annotate_ep_batch`, the canonical `why` `ep` block, `GraphRanker`,
`StateRanker`/`recall_ranking`, `GapsRanker`, `SubgraphExpander` and the
`n.confidence`-mirror `ep_state`. Deliberately OUT of this file's scope:
`analyze.py`'s template returns (pinned by their own template tests),
`battery/arms/a4_tortoise.py` (an eval arm, not a product surface), and
`sdk.get_confidence`/`TortoiseEP.compute_confidence`, which remain the EP
prior read under `require_calibration=False` and still answer 0.5 for an
absent claim — the one stated exception, recorded on #11702.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise import search_engine as _se  # noqa: E402, RUF100
from tortoise.ranking import (  # noqa: E402, RUF100
    GapsRanker,
    GraphRanker,
    StateRanker,
    SubgraphExpander,
)
from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100
from tortoise.search_engine import (  # noqa: E402, RUF100
    EpBreakdown,
    annotate_ep_batch,
)

#: The #11702 vocabulary. Resolved through ``getattr`` on purpose: this file must
#: still IMPORT against a tree that predates the fix, so that running it there
#: REDS on the property assertions below instead of dying at collection. An
#: import-time error proves nothing about the defect; a red assertion does.
EP_STATE_MEASURED = getattr(_se, "EP_STATE_MEASURED", "measured")
EP_STATE_BASELINE = getattr(_se, "EP_STATE_BASELINE", "baseline")
EP_STATE_ABSENT = getattr(_se, "EP_STATE_ABSENT", "absent")


class _MissingState:
    """``ep_state`` sentinel for a tree that has no such field."""

    def __call__(self, **_kwargs):
        raise AssertionError(
            "search_engine has no ``ep_state`` — the absent state is unnamed "
            "(#11702)")

    def __repr__(self) -> str:  # pragma: no cover — diagnosis only
        return "<no ep_state>"


ep_state = getattr(_se, "ep_state", _MissingState())

#: A real, persisted, measured posterior whose mean is exactly 0.5 — symmetric
#: evidence (α = β). This is the row that must NOT change.
SYMMETRIC_POSTERIOR = (2.0, 2.0)


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(db_path=str(tmp_path / "t.db"))
    yield s
    s.close()


def _absent_point(sdk) -> str:
    """A Point nothing has been persisted for: no posterior, no prior, no
    mirror. `create_point` alone does not write belief columns."""
    return sdk.create_point("statement", "no belief was ever computed")["id"]


def _symmetric_point(sdk) -> str:
    """A Point with a REAL symmetric posterior — a legitimately computed 0.5."""
    pid = sdk.create_point("statement", "a measured symmetric coin flip")["id"]
    alpha, beta = SYMMETRIC_POSTERIOR
    sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id = $id "
        "SET n.posterior_alpha = $a, n.posterior_beta = $b",
        params={"id": pid, "a": alpha, "b": beta},
    )
    return pid


# ── The vocabulary is one mapping, in one place ────────────────────────────

def test_ep_state_names_the_three_presence_states():
    """`ep_state` is the ONE mapping from the internal measurement predicate to
    the wire state. `unmeasured` ships as `absent` (the word §6.3's rule uses),
    and nothing else is renamed."""
    assert ep_state(posterior_alpha=None, ep_alpha=None) == EP_STATE_ABSENT
    assert ep_state(posterior_alpha=2.0, ep_alpha=None) == EP_STATE_MEASURED
    # A declared #2199 baseline prior: a VALUE, but not a measurement (#3276).
    assert ep_state(posterior_alpha=None, ep_alpha=3.0,
                    baseline_set=True) == EP_STATE_BASELINE
    # A legacy EP-flush mirror alone is still a persisted belief.
    assert ep_state(posterior_alpha=None, ep_alpha=None,
                    confidence=0.42) == EP_STATE_BASELINE


def test_ep_breakdown_default_reports_none_and_absent():
    """The dataclass default is the ABSENT contract — a default-constructed
    breakdown must never claim 0.5 (which is what every "no row" fallback in
    the read path used to inherit)."""
    ep = EpBreakdown()
    assert ep.confidence_mean is None
    assert ep.ep_state == EP_STATE_ABSENT


# ── Direction 1: absent ⇒ null + the state name, on EVERY surface ──────────

def test_ep_block_reports_null_and_absent_for_no_belief(sdk):
    pid = _absent_point(sdk)
    ep = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
    assert ep.confidence_mean is None, (
        f"a claim with no persisted belief must report NO number, got {ep.confidence_mean}")
    assert ep.ep_state == EP_STATE_ABSENT
    assert ep.has_ep is False


def test_graph_ranker_signal_reports_null_and_absent(sdk):
    pid = _absent_point(sdk)
    sig = GraphRanker(projection=sdk._get_proj())._fetch_signals([pid], "point")[pid]
    assert sig["confidence"] is None
    assert sig["ep_state"] == EP_STATE_ABSENT
    assert sig["has_prior"] is False


def test_state_ranker_reports_null_and_absent(sdk):
    pid = _absent_point(sdk)
    out = StateRanker(projection=sdk._get_proj()).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])
    rr = out[0]["recall_ranking"]
    assert rr["confidence"] is None
    assert rr["confidence_source"] == EP_STATE_ABSENT
    assert rr["ep_state"] == EP_STATE_ABSENT


def test_gaps_ranker_reports_null_and_absent(sdk):
    pid = _absent_point(sdk)
    out = GapsRanker(projection=sdk._get_proj()).rerank([{"id": pid}])
    rr = out[0]["gaps_ranking"]
    assert rr["confidence"] is None
    assert rr["ep_state"] == EP_STATE_ABSENT


def test_subgraph_node_reports_null_and_absent(sdk):
    pid = _absent_point(sdk)
    nodes = SubgraphExpander(sdk._get_proj()).expand([pid], depth=1)["nodes"]
    node = next(n for n in nodes if n["id"] == pid)
    assert node["confidence"] is None
    assert node["ep_state"] == EP_STATE_ABSENT


# ── Direction 2: a MEASURED 0.5 keeps its 0.5 (presence, not value) ────────

def test_measured_symmetric_claim_keeps_its_five_tenths(sdk):
    """The distinction is presence, not value. A claim whose posterior really
    is 0.5 (α = β) still reads 0.5 — nulling every 0.5 would be the same
    conflation in the other direction."""
    pid = _symmetric_point(sdk)

    ep = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
    assert ep.confidence_mean == pytest.approx(0.5, abs=1e-4)
    assert ep.ep_state == EP_STATE_MEASURED
    assert ep.has_ep is True

    sig = GraphRanker(projection=sdk._get_proj())._fetch_signals([pid], "point")[pid]
    assert sig["confidence"] == pytest.approx(0.5, abs=1e-4)
    assert sig["ep_state"] == EP_STATE_MEASURED

    rr = StateRanker(projection=sdk._get_proj()).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])[0]["recall_ranking"]
    assert rr["confidence"] == pytest.approx(0.5, abs=1e-4)
    assert rr["confidence_source"] == "posterior"
    assert rr["ep_state"] == EP_STATE_MEASURED


def test_declared_baseline_prior_keeps_its_value_and_is_not_measured(sdk):
    """A #2199 declared baseline is a persisted VALUE EP has not measured
    (#3276): it keeps its prior mean and is named `baseline`, never `absent`
    and never `measured`."""
    pid = sdk.create_point("statement", "a baseline-backed decision")["id"]
    sdk.set_point_baseline(pid, 18.0, 2.0)

    ep = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
    assert ep.confidence_mean == pytest.approx(18.0 / 20.0, abs=1e-4)
    assert ep.ep_state == EP_STATE_BASELINE
    assert ep.measured is False and ep.baseline is True


# ── The property: an absent claim is not ORDERED as a 0.5 ──────────────────

def test_absent_and_measured_symmetric_do_not_share_an_ordering_input(sdk):
    """THE property test. Two claims, one with no belief and one measured at
    exactly 0.5, must NOT produce the same ordering input on any ranker.

    Before #11702 both read `confidence: 0.5`, so GraphRanker gave both the
    identical `graph_boost` (0.25) and `final_score`, and StateRanker gave both
    the identical `final_score` — the ORDER could not tell "no evidence" from
    "neutral evidence", which is what made this a P1 rather than a nit.
    """
    absent = _absent_point(sdk)
    measured = _symmetric_point(sdk)
    rows = [{"id": absent, "scores": {"rrf": 1.0}},
            {"id": measured, "scores": {"rrf": 1.0}}]

    g_out = {r["id"]: r["graph_ranking"]
             for r in GraphRanker(projection=sdk._get_proj()).rerank(rows)}
    assert g_out[absent]["confidence"] is None
    assert g_out[measured]["confidence"] == pytest.approx(0.5, abs=1e-4)
    # DIRECTION, not just difference: absence must not be scored ABOVE a real
    # measurement. Dropping the belief TERM while keeping the remaining term's
    # weight is what guarantees this; returning the unweighted term would make
    # "nothing measured this" (0.75) beat "measured, symmetric" (0.65).
    assert g_out[absent]["graph_boost"] < g_out[measured]["graph_boost"], (
        "an absent belief scored HIGHER than a measured 0.5")
    assert g_out[absent]["final_score"] < g_out[measured]["final_score"], (
        "an absent belief outranked a measured 0.5")

    s_out = {r["id"]: r["recall_ranking"]
             for r in StateRanker(projection=sdk._get_proj()).rerank(rows)}
    assert s_out[absent]["confidence"] is None
    assert s_out[measured]["confidence"] == pytest.approx(0.5, abs=1e-4)
    # DIRECTION: the multiplicative gate cannot express "absent" as a factor
    # (`conf**exp` ranges over (0, 1], so the identity 1.0 is that axis's
    # MAXIMUM — an absent row left to compete on it outranks a measured 0.9).
    # The refusal is in the ORDER: the absent row must come out last.
    order = [r["id"] for r in StateRanker(projection=sdk._get_proj()).rerank(
        [*rows, {"id": "no-such-id", "scores": {"rrf": 1.0}}])]
    assert order.index(absent) > order.index(measured), (
        "recall ranked an absent belief above a measured 0.5")
    assert s_out[absent]["final_score"] != s_out[measured]["final_score"], (
        "the multiplicative confidence gate treated 'no belief' as a 0.5")


def test_absent_never_outranks_a_strongly_measured_claim(sdk):
    """The property §6.3 actually protects, at the top of the range.

    `1.0` (the multiplicative identity) is the MAXIMUM of the confidence axis,
    so an absent row competing on it would beat a measured 0.9 (0.5 vs 0.45).
    The refusal is a sort TIER: every row with a belief sorts above every row
    without one, at equal relevance.
    """
    absent = _absent_point(sdk)
    strong = sdk.create_point("statement", "a strongly measured claim")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id = $id "
        "SET n.posterior_alpha = 19.0, n.posterior_beta = 1.0",
        params={"id": strong},
    )
    rows = [{"id": absent, "scores": {"rrf": 1.0}},
            {"id": strong, "scores": {"rrf": 1.0}}]
    order = [r["id"] for r in StateRanker(projection=sdk._get_proj()).rerank(rows)]
    assert order[0] == strong, (
        "an absent belief outranked a claim measured at 0.95 — the identity "
        "factor made 'nothing measured this' the strongest evidence")
    assert order[1] == absent


def test_absent_belief_contributes_no_factor_to_the_recall_gate(sdk):
    """The absent BELIEF term does not participate at all — neither as a
    fabricated 0.5 (a mid-strength claim) nor as a 0.0 (which would be
    "absence is evidence against", the #898 concern this keeps). The row is
    ordered by its remaining signal, relevance."""
    pid = _absent_point(sdk)
    rr = StateRanker(projection=sdk._get_proj()).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])[0]["recall_ranking"]
    # relevance_norm is the midpoint of a 1-element set (0.5); with no belief
    # factor the gate leaves it untouched.
    assert rr["relevance_norm"] == pytest.approx(0.5, abs=1e-6)
    assert rr["base_score"] == pytest.approx(rr["relevance_norm"], abs=1e-6)


# ── The ep block and the ranker AGREE on the state ─────────────────────────

def test_ep_block_and_rankers_agree_on_the_state(sdk):
    """§6.3's last line, made checkable: whatever the `ep` block says about a
    claim's belief, the rankers that ordered on it say the same."""
    absent = _absent_point(sdk)
    measured = _symmetric_point(sdk)
    baseline = sdk.create_point("statement", "a baseline-backed claim 2")["id"]
    sdk.set_point_baseline(baseline, 3.0, 1.0)

    eps = annotate_ep_batch(sdk._get_proj().g, [absent, measured, baseline])
    sigs = GraphRanker(projection=sdk._get_proj())._fetch_signals(
        [absent, measured, baseline], "point")
    recs = {r["id"]: r["recall_ranking"]
            for r in StateRanker(projection=sdk._get_proj()).rerank(
                [{"id": p, "scores": {"rrf": 1.0}}
                 for p in (absent, measured, baseline)])}
    # The canonical `ep` block — the block `/v1/context`, the volunteer reflex
    # and the pushed webhook actually serve — is a DIFFERENT producer
    # (`why._assemble_ep_rows`) from `annotate_ep_batch`. Covering only the
    # search path is how the fabricated 0.5 survived there.
    from tortoise.why import assemble_why_blocks
    whys = assemble_why_blocks(sdk._get_proj(), [absent, measured, baseline])

    for pid in (absent, measured, baseline):
        assert sigs[pid]["ep_state"] == eps[pid].ep_state, pid
        assert recs[pid]["ep_state"] == eps[pid].ep_state, pid
        assert whys[pid]["ep"]["ep_state"] == eps[pid].ep_state, pid
        # A value exists exactly when the state is not `absent` — on EVERY
        # surface, including the why block.
        assert (eps[pid].confidence_mean is not None) == (
            eps[pid].ep_state != EP_STATE_ABSENT), pid
        assert (sigs[pid]["confidence"] is not None) == (
            eps[pid].ep_state != EP_STATE_ABSENT), pid
        assert (whys[pid]["ep"]["confidence_mean"] is not None) == (
            eps[pid].ep_state != EP_STATE_ABSENT), pid
        # …and the VALUE agrees, not only its presence. Two surfaces under the
        # same state name reporting different numbers is the same class of bug
        # (the mirror/preference split) one level down.
        if eps[pid].ep_state != EP_STATE_ABSENT:
            assert whys[pid]["ep"]["confidence_mean"] == pytest.approx(
                eps[pid].confidence_mean, abs=1e-4), pid
