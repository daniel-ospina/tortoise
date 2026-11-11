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
covering more than it does). PINNED here: the vocabulary (`ep_state`,
`ep_has_belief_cypher` via the surfaces below, `belief_value`),
`annotate_ep_batch`, the canonical `why` `ep` block, `GraphRanker`,
`StateRanker`/`recall_ranking`, `GapsRanker`, `SubgraphExpander`, the
`analyze` templates (`evidence_chain`, and the `_BELIEF` gate they share),
`navigation.confidenceMin`, `sdk.approve`'s `confidence_delta`, and the
journal records both belief-clearing writers emit. Each is pinned for the
whole rule, not one shape: absent / half pair / zero-total pair / mirror-only
/ measured.

Deliberately OUT of this file's scope: `battery/arms/a4_tortoise.py` (an eval
arm, not a product surface) and `sdk.get_confidence` /
`TortoiseEP.compute_confidence`, which remain the EP prior read under
`require_calibration=False` and still answer 0.5 for an absent claim — the
one stated exception, recorded in the #11702 decision comment
(issuecomment-6781252637).
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
    assert ep_state(posterior_alpha=2.0, ep_alpha=None,
                    posterior_beta=2.0) == EP_STATE_MEASURED
    # A declared #2199 baseline prior: a VALUE, but not a measurement (#3276).
    assert ep_state(posterior_alpha=None, ep_alpha=3.0, ep_beta=1.0,
                    baseline_set=True) == EP_STATE_BASELINE
    # ⛔ HALF a pair is no belief: the mean needs both operands, so a row with
    # only β (or only α) invents nothing — the `coalesce(…, 1.0)` default used
    # to supply the missing one and answer 1/4.
    assert ep_state(posterior_alpha=None, ep_alpha=None,
                    posterior_beta=3.0) == EP_STATE_ABSENT
    assert ep_state(posterior_alpha=2.0, ep_alpha=None) == EP_STATE_ABSENT
    # …and a complete pair that sums to zero is no belief either.
    assert ep_state(posterior_alpha=0.0, ep_alpha=None,
                    posterior_beta=0.0) == EP_STATE_ABSENT
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
    # The DISCRIMINATING comparison is against a measured claim worth exactly
    # what absence used to be fabricated as: 0.5. Pre-fix both rows carried the
    # same number and the same (absent) tier, so the order was decided by row
    # position and this assertion could not fail — which is why the twin at
    # 0.95 is not the pin. Here the tier must separate them.
    symmetric = _symmetric_point(sdk)
    rows = [{"id": absent, "scores": {"rrf": 1.0}},
            {"id": strong, "scores": {"rrf": 1.0}},
            {"id": symmetric, "scores": {"rrf": 1.0}}]
    ranked = {r["id"]: r["recall_ranking"]
              for r in StateRanker(projection=sdk._get_proj()).rerank(rows)}
    order = [r["id"] for r in StateRanker(projection=sdk._get_proj()).rerank(rows)]
    assert order[0] == strong, (
        "an absent belief outranked a claim measured at 0.95 — the identity "
        "factor made 'nothing measured this' the strongest evidence")
    assert ranked[absent]["belief_informed_order"] is False
    assert ranked[symmetric]["belief_informed_order"] is True, ranked[symmetric]
    assert ranked[absent]["ep_state"] == "absent", ranked[absent]
    assert ranked[symmetric]["ep_state"] == "measured", ranked[symmetric]
    assert ranked[absent]["confidence"] is None, (
        "the absent row still carries a fabricated confidence: "
        + repr(ranked[absent]))


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
            assert sigs[pid]["confidence"] == pytest.approx(
                eps[pid].confidence_mean, abs=1e-4), pid


def test_mirror_only_claim_agrees_on_the_value_everywhere(sdk):
    """The THIRD shape: a belief stored ONLY in the `n.confidence` mirror.

    `compute_confidence` / `dream` write the mirror with no α/β, and a
    pre-#852 node carries nothing else — so this is a live population, not a
    legacy corner. It must report `baseline` AND the same NUMBER on every
    surface; the `ep` block used to compute from α/β alone and answer the
    Beta(1,1) 0.5 for it while the rankers read 0.95.
    """
    pid = sdk.create_point("statement", "a mirror-only claim")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.confidence = 0.95", params={"id": pid})

    from tortoise.why import assemble_why_blocks
    eps = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
    why_ep = assemble_why_blocks(sdk._get_proj(), [pid])[pid]["ep"]
    sig = GraphRanker(projection=sdk._get_proj())._fetch_signals([pid], "point")[pid]
    rec = StateRanker(projection=sdk._get_proj()).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])[0]["recall_ranking"]

    assert eps.ep_state == EP_STATE_BASELINE, eps.ep_state
    for name, value in (("ep block", eps.confidence_mean),
                        ("why block", why_ep["confidence_mean"]),
                        ("graph signal", sig["confidence"]),
                        ("recall_ranking", rec["confidence"])):
        assert value == pytest.approx(0.95, abs=1e-4), name
    assert why_ep["ep_state"] == EP_STATE_BASELINE, why_ep


def test_degenerate_posterior_pair_is_ABSENT_on_every_surface(sdk):
    """A stored (α, β) = (0, 0) pair is NO belief — not a 0.5, not a NaN.

    `ep.py::_write_node` writes that pair verbatim (a decayed or never-seeded
    posterior), and Beta(0, 0) is not a distribution: it carries no
    information. Read as "columns present" it was a belief with no computable
    mean, and one claim got FOUR answers — 0.5 in the `ep` block, `None` in
    `recall_ranking`, `absent` in `gaps_ranking`, and **NaN** from
    `graph_ranking`'s unguarded α/(α+β) division (serialised as a bare `NaN`
    token). The presence predicate is therefore a SUM (`α + β > 0`), and this
    is the regression test that keeps every surface on that one rule.
    """
    pid = sdk.create_point("statement", "a degenerate posterior pair")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.posterior_alpha = 0.0, "
        "n.posterior_beta = 0.0", params={"id": pid})
    eps = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
    proj = sdk._get_proj()
    rec = StateRanker(projection=proj).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])[0]["recall_ranking"]
    gap = GapsRanker(projection=proj).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])[0]["gaps_ranking"]
    graph = GraphRanker(projection=proj).rerank(
        [{"id": pid, "scores": {"rrf": 1.0}}])[0]["graph_ranking"]
    sig = GraphRanker(projection=proj)._fetch_signals([pid], "point")[pid]

    assert eps.ep_state == EP_STATE_ABSENT, (
        "Beta(0, 0) is not a distribution — the pair carries no belief")
    assert eps.confidence_mean is None, (
        "a zero-total pair has NO mean; answering 0.5 is the fabrication")
    for name, value in (("recall_ranking", rec["confidence"]),
                        ("gaps_ranking", gap["confidence"]),
                        ("graph_ranking", graph["confidence"]),
                        ("graph signal", sig["confidence"])):
        assert value is None, f"{name} reports {value!r} for a zero-total pair"
        assert not isinstance(value, float) or value == value, (
            f"{name} answered NaN — an unguarded 0/0 reached the wire")
    for name, state in (("recall_ranking", rec["ep_state"]),
                        ("gaps_ranking", gap["ep_state"]),
                        ("graph_ranking", graph["ep_state"]),
                        ("graph signal", sig["ep_state"])):
        assert state == EP_STATE_ABSENT, f"{name} reports {state!r}"
    assert rec["belief_informed_order"] is False, rec


def test_confidence_source_never_contradicts_the_ep_state_beside_it(sdk):
    """`confidence_source` and `ep_state` must name the SAME fact on one row.

    The source string is the vocabulary a consumer filters on ("show me the
    rows with no belief"), and `ep_state` is the state the `ep` block reports
    for the same claim. The reviewed head emitted `confidence_source:
    "absent"` BESIDE `ep_state: "measured"` for a stored (α, β) = (0, 0)
    pair: one key said "nothing is persisted" while the other said "EP
    measured this", so no consumer could trust either. (`_confidence_of`
    collapsed every `None` value to the absent source, and the state was read
    from the signal, which still called that pair present — two presence rules
    on one row.)

    Both rules are now the shared predicate, so the agreement is structural —
    but "structural" is a claim, and this is the assertion that keeps it true
    across all six stored shapes if either side is edited again.
    """
    proj = sdk._get_proj()

    def _point(content, sql=None, params=None):
        pid = sdk.create_point("statement", content)["id"]
        if sql:
            proj.g.query(f"MATCH (n:Point {{id:$id}}) SET {sql}",
                         params={"id": pid, **(params or {})})
        return pid

    absent = _point("no belief at all")
    half_pair = _point("a beta-only row", "n.posterior_beta = 3.0")
    zero_total = _point("a degenerate pair", "n.posterior_alpha = 0.0, "
                        "n.posterior_beta = 0.0")
    mirror_only = _point("a mirror-only claim", "n.confidence = 0.95")
    measured = _symmetric_point(sdk)  # α = β = 2.0, a real measured 0.5
    baseline = _point("a declared baseline")
    sdk.set_point_baseline(baseline, 18.0, 2.0)

    ids = [absent, half_pair, zero_total, mirror_only, measured, baseline]
    recs = {r["id"]: r["recall_ranking"] for r in
            StateRanker(projection=proj).rerank(
                [{"id": p, "scores": {"rrf": 1.0}} for p in ids])}
    gaps = {r["id"]: r["gaps_ranking"] for r in
            GapsRanker(projection=proj).rerank(
                [{"id": p, "scores": {"rrf": 1.0}} for p in ids])}

    for pid in ids:
        for name, row in (("recall_ranking", recs[pid]),
                          ("gaps_ranking", gaps[pid])):
            state, source = row["ep_state"], row["confidence_source"]
            assert (source == EP_STATE_ABSENT) == (state == EP_STATE_ABSENT), (
                f"{name} reports confidence_source={source!r} beside "
                f"ep_state={state!r} — the two keys describe one fact")
            # …and the VALUE follows the same rule, so a row cannot say "no
            # belief" in the source and carry a number in the value.
            assert (row["confidence"] is None) == (state == EP_STATE_ABSENT), (
                f"{name} reports confidence={row['confidence']!r} beside "
                f"ep_state={state!r}")
        # The two rankers must not disagree about the same node either.
        assert recs[pid]["ep_state"] == gaps[pid]["ep_state"], pid
        assert (recs[pid]["confidence_source"] == EP_STATE_ABSENT) == (
            gaps[pid]["confidence_source"] == EP_STATE_ABSENT), pid

    # The pre-fix contradiction, named: a zero-total pair is ABSENT in BOTH
    # keys — never `"absent"` beside `"measured"`.
    for row in (recs[zero_total], gaps[zero_total]):
        assert row["ep_state"] == EP_STATE_ABSENT, row
        assert row["confidence_source"] == EP_STATE_ABSENT, row
        assert row["confidence"] is None, row
    # …and the source still names a REAL state for the rows that have a belief.
    assert recs[measured]["confidence_source"] == "posterior", recs[measured]
    assert gaps[measured]["confidence_source"] == "posterior", gaps[measured]
    for pid in (mirror_only, baseline):
        assert recs[pid]["ep_state"] == EP_STATE_BASELINE, recs[pid]
        assert recs[pid]["confidence_source"] != EP_STATE_ABSENT, recs[pid]


def test_zero_total_pair_does_not_survive_an_equal_relevance_order(sdk):
    """The absent tier places the zero-total claim after a MEASURED 0.1 claim.

    The value check above is not enough on its own: the failure it guards was
    an ORDERING one (absent sorted as if it were 0.5, above a measured low).
    """
    absent = sdk.create_point("statement", "zero-total claim")["id"]
    low = sdk.create_point("statement", "measured low claim")["id"]
    proj = sdk._get_proj()
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 0.0, "
                 "n.posterior_beta = 0.0", params={"id": absent})
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 1.0, "
                 "n.posterior_beta = 9.0", params={"id": low})
    rows = StateRanker(projection=proj).rerank(
        [{"id": absent, "scores": {"rrf": 1.0}},
         {"id": low, "scores": {"rrf": 1.0}}])
    order = [r["id"] for r in rows]
    assert order == [low, absent], (
        f"absent must sort after a measured 0.1 claim, got {order}")
    assert rows[0]["recall_ranking"]["confidence"] == pytest.approx(0.1, abs=1e-4)
    assert rows[1]["recall_ranking"]["confidence"] is None


def test_analyze_evidence_chain_reports_null_and_sorts_an_absent_claim_last(sdk):
    """`tortoise_analyze` is a read surface too — it must not score an absent claim.

    The `strongest_for` / `counter_arguments` / `evidence_chain` templates
    deliberately ADMIT a belief-less claim (the IMPL edge is the evidence), so
    the belief never gates them — but they RANK by it, and the `CASE … ELSE 0.5`
    arm did not distinguish "no columns" from a stored (0, 0) pair. The absent
    supporter therefore printed `conf: 0.50` and sorted ABOVE a claim genuinely
    measured at 0.10, which is the ranker ordering on a fabricated absence.
    """
    from tests._embedded import _wipe_or as wipe
    from tortoise.analyze import analyze

    proj = sdk._get_proj()
    wipe(proj)
    g = proj.g
    g.query("CREATE (:Point {id:'absent-sup', content:'absent evidence claim', "
            "is_operator:false, status:'live'})")
    g.query("CREATE (:Point {id:'low-sup', content:'measured low claim', "
            "is_operator:false, status:'live', posterior_alpha:1.0, "
            "posterior_beta:9.0})")
    g.query("CREATE (:Point {id:'tgt', content:'evidence ai strategy is working', "
            "is_operator:false, status:'live'})")
    g.query("CREATE (:Point {id:'op', content:'IMPL', op_type:'IMPL', "
            "is_operator:true, status:'live'})")
    g.query("MATCH (a:Point {id:'absent-sup'}), (b:Point {id:'low-sup'}), "
            "(o:Point {id:'op'}), (t:Point {id:'tgt'}) "
            "CREATE (a)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:1}]->(t), "
            "       (b)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:2}]->(t)")

    out = analyze("show me the evidence chain for the AI strategy",
                  proj=proj, use_llm=False)
    answer = out["answer"]
    assert "measured low claim" in answer and "absent evidence claim" in answer, answer
    # The measured 0.1 claim must lead; the absent one follows with NO number.
    first = answer.index("measured low claim")
    second = answer.index("absent evidence claim")
    assert first < second, (
        "an absent belief must sort LAST, not above a measured 0.1 claim: " + answer)
    assert "0.50" not in answer, (
        "the absent claim was scored 0.50 — a fabricated neutral belief: " + answer)


@pytest.mark.parametrize("template", ["strongest_for", "counter_arguments",
                                     "evidence_chain"])
def test_every_ranking_template_renders_an_absent_belief_with_no_number(
        sdk, template):
    """All THREE admitting templates, not just the one a previous test picked.

    The reviewed finding named `strongest_for` (:99), `counter_arguments`
    (:116) and `evidence_chain` (:194) — the three templates that carry no
    presence gate in their WHERE and therefore take the shared belief
    expression directly. `evidence_chain` was pinned; the other two were not,
    so a bare `opponent.confidence` (or a re-introduced `ELSE 0.5`) on either
    would have kept the suite green. This runs each template's OWN cypher and
    its OWN formatter, so both halves of that surface are graded.

    Both directions matter: the absent claim must still be RETURNED (the IMPL / 
    NAND edge is the evidence — absence is not disqualifying) and must carry no
    number and no rank above a measured 0.1 claim.
    """
    from tests._embedded import _wipe_or as wipe
    from tortoise.analyze import TEMPLATES

    proj = sdk._get_proj()
    wipe(proj)
    g = proj.g
    g.query("CREATE (:Point {id:'absent-rel', content:'absent related claim', "
            "is_operator:false, status:'live'})")
    g.query("CREATE (:Point {id:'low-rel', content:'measured low claim', "
            "is_operator:false, status:'live', posterior_alpha:1.0, "
            "posterior_beta:9.0})")
    g.query("CREATE (:Point {id:'tgt', content:'the ai strategy is working', "
            "is_operator:false, status:'live'})")
    g.query("CREATE (:Point {id:'impl', content:'IMPL', op_type:'IMPL', "
            "is_operator:true, status:'live'})")
    g.query("CREATE (:Point {id:'nand', content:'NAND', op_type:'NAND', "
            "is_operator:true, status:'live'})")
    # IMPL: both related claims support the target (idx 0 = the source).
    g.query("MATCH (a:Point {id:'absent-rel'}), (b:Point {id:'low-rel'}), "
            "(o:Point {id:'impl'}), (t:Point {id:'tgt'}) "
            "CREATE (a)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:1}]->(t), "
            "       (b)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:2}]->(t)")
    # NAND: both contradict the target, for `counter_arguments`.
    g.query("MATCH (a:Point {id:'absent-rel'}), (b:Point {id:'low-rel'}), "
            "(o:Point {id:'nand'}), (t:Point {id:'tgt'}) "
            "CREATE (t)<-[:NAND {idx:1}]-(o)-[:NAND {idx:0}]->(a), "
            "       (t)<-[:NAND {idx:2}]-(o)-[:NAND {idx:3}]->(b)")

    tmpl = TEMPLATES[template]
    rows = g.query(tmpl["cypher"],
                   params={"entity": "ai strategy", "limit": 20}).result_set
    answer = tmpl["format"](rows)

    assert "absent related claim" in answer, (
        f"{template} dropped an absent claim instead of rendering it without "
        f"a number: {answer}")
    assert "0.50" not in answer, (
        f"{template} scored an absent belief 0.50 — the fabrication #11702 "
        f"removes: {answer}")
    if template != "counter_arguments":
        # The two IMPL templates rank, so the measured 0.1 claim must lead.
        assert "measured low claim" in answer, answer
        assert answer.index("measured low claim") < answer.index(
            "absent related claim"), (
            f"{template} ranked an absent belief above a measured 0.1 claim: "
            + answer)


def test_ep_evidence_prewrite_journals_the_mirror_clear(tmp_path):
    """The pre-write CLEARS `n.confidence`; the record must clear it too.

    The journal fold is presence-conditional — only the keys a record carries
    are written — so a `ConfidenceChanged` carrying just the two posterior keys
    cleared the posterior and LEFT the mirror the statement had just nulled.
    Live: the claim reads `absent`/None. Rebuilt from the journal:
    `baseline`/0.9 — a belief the live graph no longer has, resurrected by
    recovery. This is the recovery-equivalence property a journal exists for.
    """
    import json as _json

    from tortoise.sdk import TortoiseSDK

    db = str(tmp_path / "j.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    try:
        # A claim carrying a posterior AND the flush-mirror beside it.
        pid = sdk.create_point("statement", "mirrored claim", dedup=False,
                               status="live")["id"]
        g = sdk._get_proj().g
        g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 3.0, "
                "n.posterior_beta = 1.0, n.confidence = 0.75",
                params={"id": pid})
        # An EP run seeded with this claim as evidence takes the pre-write
        # branch that clears a non-baseline claim's posterior + mirror.
        sdk.compute_confidence(evidence={pid: (10, 1)},
                               require_calibration=False)

        recs = [r for r in ( _json.loads(line)
                             for line in (events / "events.jsonl").read_text().splitlines()
                             if line.strip())
                if r.get("type") == "ConfidenceChanged" and r.get("id") == pid]
        assert recs, "no ConfidenceChanged journaled for the cleared claim"
        # The record must carry EVERY key the statement cleared.
        assert any("confidence" in r for r in recs), (
            "the record omitted `confidence` — a rebuild replays only the keys "
            "a record carries, so the mirror the SET nulled survives recovery")
        assert all(r.get("confidence") is None
                   for r in recs if "confidence" in r), (
            "the record carries a stale mirror value instead of the clear")
    finally:
        sdk.close()


# ── entityProfile's floor may not read absence off the mirror alone ────────

def test_entity_profile_floor_reads_a_posterior_only_belief(sdk):
    """`navigation.entityProfile(confidenceMin=…)` read only the
    `n.confidence` MIRROR, so a claim whose belief is persisted in its
    posterior — the population the EP flush never reached, and the reason
    `_fetch_point_signals` carries its "a node the flush never reached"
    fallback — read `conf is None` and dropped out of EVERY floor. Before
    #11702 the fabricated 0.5 admitted it at any floor ≤ 0.5, so this is a
    recall regression, and it contradicts the §6.3 rule that a read surface
    uses the shared presence predicate instead of re-deriving absence.

    An ABSENT claim must still be excluded by a real floor, and kept by the
    no-op `0.0` default.
    """
    from tortoise.navigation import entityProfile

    proj = sdk._get_proj()
    root = sdk.create_point("statement", "the profiled root entity")["id"]
    measured_kid = sdk.create_point("statement", "a posterior-only kid")["id"]
    absent_kid = sdk.create_point("statement", "a kid with no belief")["id"]
    for kid in (measured_kid, absent_kid):
        proj.g.query(
            "MATCH (a:Point {id:$a}), (b:Point {id:$b}) "
            "CREATE (a)-[:IMPL {idx:1}]->(b)", params={"a": root, "b": kid})
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.posterior_alpha = 9.0, "
        "n.posterior_beta = 1.0", params={"id": measured_kid})

    def ids(floor):
        out = entityProfile(proj.db, proj.graph_name, root, hops=1,
                            confidenceMin=floor)
        return {p["id"] for p in out["connected"]["points"]}

    assert measured_kid in ids(0.3), (
        "a claim MEASURED at 0.9 was dropped by a 0.3 floor — the filter read "
        "the absent mirror instead of the persisted belief")
    assert absent_kid not in ids(0.3), "an absent belief cleared a real floor"
    assert {measured_kid, absent_kid} <= ids(0.0), (
        "the no-op floor must keep an absent claim (0.0 is the #11702 default)")
    """`belief_value` is the Python twin of the Cypher presence+precedence rule.

    Every surface that holds a plain property dict (navigation's floor,
    `sdk.approve`'s delta) calls THIS, so the "a half pair is no belief" rule
    cannot be re-decided differently per surface — which is exactly how
    `tortoise_analyze` came to answer `1/4` for a β-only row.
    """
    from tortoise.search_engine import belief_value

    assert belief_value(confidence=0.95) == pytest.approx(0.95)
    assert belief_value(posterior_alpha=2.0, posterior_beta=2.0) == pytest.approx(0.5)
    assert belief_value(ep_alpha=10.0, ep_beta=1.0) == pytest.approx(10 / 11)
    # mirror leads over the pair (the #2206 served value)
    assert belief_value(confidence=0.4, posterior_alpha=9.0,
                        posterior_beta=1.0) == pytest.approx(0.4)
    # every absent shape
    assert belief_value() is None
    assert belief_value(posterior_alpha=2.0) is None            # half pair
    assert belief_value(posterior_beta=3.0) is None             # half pair
    assert belief_value(posterior_alpha=0.0, posterior_beta=0.0) is None


def test_analyze_reports_no_belief_for_a_half_pair(sdk):
    """The `analyze` templates gate on the SHARED predicate, not a local sum.

    A private sum called a β-only row a belief (`coalesce(…, 1.0)` supplied the
    missing α) and reported `1/4`, which `_order_by_belief` then ordered on —
    the exact inversion §6.3 removes, on a surface whose own comment claimed
    the fix.
    """
    from tests._embedded import _wipe_or as wipe
    from tortoise.analyze import analyze

    proj = sdk._get_proj()
    wipe(proj)
    g = proj.g
    g.query("CREATE (:Point {id:'half', content:'half pair support', "
            "is_operator:false, status:'live', posterior_beta:3.0})")
    g.query("CREATE (:Point {id:'low', content:'measured low support', "
            "is_operator:false, status:'live', posterior_alpha:1.0, "
            "posterior_beta:9.0})")
    g.query("CREATE (:Point {id:'tgt', content:'evidence ai strategy is working', "
            "is_operator:false, status:'live'})")
    g.query("CREATE (:Point {id:'op', content:'IMPL', op_type:'IMPL', "
            "is_operator:true, status:'live'})")
    g.query("MATCH (h:Point {id:'half'}), (b:Point {id:'low'}), "
            "(o:Point {id:'op'}), (t:Point {id:'tgt'}) "
            "CREATE (h)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:1}]->(t), "
            "       (b)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:2}]->(t)")
    answer = analyze("show me the evidence chain for the AI strategy",
                     proj=proj, use_llm=False)["answer"]
    # NOTE ON DISCRIMINATING POWER (cycle-8 review): this test also passes on
    # the PRE-#11702 tree, because that tree coalesced BOTH rows to 0.5 and the
    # incidental `ORDER BY conf DESC` tie order happened to match. It therefore
    # guards the INTERMEDIATE `ep_has_belief_cypher` shape (which admitted a
    # half pair) rather than being a before/after pin — `grading`-style honesty
    # beats a docstring that claims more than the assertion earns. The
    # before/after pin for the half pair is
    # `test_why_half_posterior_is_not_contested_and_has_no_variance`, which is
    # red pre-fix (a β-only row got variance 1/12 and `contested: true`).
    assert "half pair support" in answer and "measured low support" in answer, answer
    assert "0.25" not in answer, (
        "a β-only row was scored 0.25 — the half-pair fabrication: " + answer)
    assert (answer.index("measured low support")
            < answer.index("half pair support")), (
        "the absent half-pair row must sort after the measured 0.1 row: " + answer)


def test_set_point_baseline_journals_the_mirror_it_writes(sdk, tmp_path):
    """The baseline write now MOVES `n.confidence`; the record must say so.

    The fold writes only the keys a record carries, so a record with just the
    two posterior clears left the mirror stale on replay: live `baseline`/0.75,
    rebuilt `absent`/None. The payload is the value the statement committed.
    """
    import json as _json

    from tortoise.sdk import TortoiseSDK

    db = str(tmp_path / "b.db")
    events = tmp_path / "bevents"
    events.mkdir()
    j = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    try:
        pid = j.create_point("statement", "baseline journal claim",
                             dedup=False, status="live")["id"]
        j.set_point_baseline(pid, 3.0, 1.0)
        mirror = j._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN n.confidence",
            params={"id": pid}).result_set[0][0]
        assert mirror == pytest.approx(0.75, abs=1e-4), mirror
        recs = [r for r in (_json.loads(line)
                            for line in (events / "events.jsonl").read_text().splitlines()
                            if line.strip())
                if r.get("type") == "ConfidenceChanged" and r.get("id") == pid]
        assert recs, "set_point_baseline journaled no ConfidenceChanged"
        carried = [r["confidence"] for r in recs if "confidence" in r]
        assert carried, (
            "the record omitted `confidence` while the statement wrote it — a "
            "rebuild would leave the mirror stale (live baseline/0.75 -> rebuilt absent)")
        assert carried[-1] == pytest.approx(0.75, abs=1e-4), carried
    finally:
        j.close()


def test_degenerate_baseline_writes_no_mirror(sdk):
    """`set_point_baseline(0, 0)` must not manufacture a 0.5 belief.

    The new mirror write made the row PRESENT by the shared predicate (the
    mirror test wins over the zero-total pair), so one SDK call produced the
    exact `0.5` + belief state §6.3 forbids — and journaled it, so a rebuild
    reproduced it. The degenerate write now leaves the row ABSENT, which is
    what every read surface reports.
    """
    pid = sdk.create_point("statement", "degenerate baseline claim",
                           dedup=False, status="live")["id"]
    sdk.set_point_baseline(pid, 0.0, 0.0)
    stored = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.confidence", params={"id": pid}
    ).result_set[0][0]
    assert stored is None, f"a degenerate baseline wrote a mirror: {stored!r}"
    eps = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
    assert eps.ep_state == EP_STATE_ABSENT, eps
    assert eps.confidence_mean is None, eps


def test_why_support_chain_does_not_weigh_an_absent_supporter(sdk):
    """The support chain is ordered and capped by `weight`.

    `_mean(coalesce(…, 1.0), coalesce(…, 1.0))` weighed an ABSENT supporter
    0.5 (and a β-only one 0.25), so the ≤3 cap was filled by fabricated numbers
    and a genuinely measured 0.4 supporter could be crowded out entirely.
    """
    from tortoise.why import _assemble_supports

    proj = sdk._get_proj()
    target = sdk.create_point("statement", "the supported target")["id"]
    absent = [sdk.create_point("statement", f"absent supporter {i}")["id"]
              for i in range(3)]
    measured = sdk.create_point("statement", "measured 0.4 supporter")["id"]
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.posterior_alpha = 2.0, "
        "n.posterior_beta = 3.0", params={"id": measured})
    for sup in (*absent, measured):
        proj.g.query(
            "MATCH (s:Point {id:$s}), (t:Point {id:$t}) "
            "CREATE (s)-[:IMPL {idx:1}]->(t)", params={"s": sup, "t": target})

    blocks = {target: {"support_chain": []}}
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) WITH n "
        "MATCH (sup:Point)-[r:IMPL]->(n) "
        "RETURN n.id, sup.id, sup.content, sup.confidence, "
        "       sup.posterior_alpha, sup.posterior_beta, sup.ep_alpha, sup.ep_beta",
        params={"id": target}).result_set
    _assemble_supports(rows, blocks)
    chain = blocks[target]["support_chain"]
    assert chain, "no support chain assembled"
    assert chain[0]["point_id"] == measured, (
        f"the measured 0.4 supporter must lead the chain, got {chain}")
    assert chain[0]["weight"] == pytest.approx(0.4, abs=1e-4), chain[0]
    for entry in chain[1:]:
        assert entry["weight"] is None, (
            f"an absent supporter was weighed {entry['weight']!r}: {entry}")


def test_most_uncertain_does_not_rank_a_mirror_only_claim_first(sdk):
    """Variance is computed from α/β, so its population must HAVE α/β.

    Gating that population on the presence predicate admitted a mirror-only
    claim, whose `coalesce(…, 1.0)` defaults produced the MAXIMUM variance
    1/12 — first place on a "how uncertain are we" answer, for a claim with no
    stored operands at all.

    NOTE ON DISCRIMINATING POWER (cycle-8 review): this assertion also holds on
    the PRE-#11702 tree, whose gate was already `posterior_alpha IS NOT NULL OR
    ep_alpha IS NOT NULL` — it pins the INTERMEDIATE `ep_has_belief_cypher`
    shape (which would admit a mirror-only row), not the original defect. Kept
    because that intermediate shape is one edit away from coming back; the
    original-defect pin is `test_analyze_reports_no_belief_for_a_half_pair`.
    """
    from tests._embedded import _wipe_or as wipe
    from tortoise.analyze import analyze

    proj = sdk._get_proj()
    wipe(proj)
    g = proj.g
    g.query("CREATE (:Point {id:'mirror', content:'ZEBRA mirror-only claim', "
            "is_operator:false, status:'live', confidence:0.42})")
    g.query("CREATE (:Point {id:'meas', content:'ZEBRA measured claim', "
            "is_operator:false, status:'live', posterior_alpha:9.0, "
            "posterior_beta:1.0})")
    answer = analyze("what are we most uncertain about?", proj=proj,
                     use_llm=False)["answer"]
    assert "ZEBRA measured claim" in answer, answer
    assert "ZEBRA mirror-only claim" not in answer, (
        "a mirror-only claim has no stored α/β, so it has no variance to rank "
        "— and the coalesce(…, 1.0) defaults gave it the maximum: " + answer)


def test_ranker_belief_value_coerces_an_integer_alpha_beta_pair(sdk):
    """One claim must not answer TWO numbers: 0.90 on `ep`, 0.00 on the rankers.

    FalkorDB's `/` is INTEGER division on int operands (`RETURN 9/10` → `0`,
    measured on falkordb/falkordb-server:v4.20.6), so a pair stored as ints —
    `update_point(posterior_alpha=9, posterior_beta=1)`,
    `set_point_baseline(2, 1)`, the legacy `tier_map` — truncated
    `9/10 → 0` in the Cypher mean `coalesce(…, 1.0) / (… + …)` of
    `GraphRanker._fetch_point_signals` and `SubgraphExpander._enrich`, while
    `annotate_ep_batch` and `search_engine.belief_value` (both `toFloat`- or
    Python-guarded) answered `0.90` for the SAME row. A measured 0.90 claim
    reported as 0.00 is a fabricated value, not an absent one.

    Discriminating power: pre-fix the two ranker assertions see `0.0`.
    """
    from tests._embedded import _wipe_or as wipe

    proj = sdk._get_proj()
    wipe(proj)
    # Ints on purpose — the storage layer keeps them as ints.
    proj.g.query("CREATE (:Point {id:'intpair', content:'integer pair claim', "
                 "is_operator:false, status:'live', posterior_alpha:9, "
                 "posterior_beta:1})")

    # The reference answer, from the read that is already guarded.
    ep = annotate_ep_batch(proj.g, ["intpair"])["intpair"]
    assert ep.confidence_mean == pytest.approx(0.9), ep.confidence_mean

    sig = GraphRanker(projection=proj)._fetch_signals(["intpair"], "point")["intpair"]
    assert sig["confidence"] == pytest.approx(0.9), (
        "graph_rankings truncated an integer 9/1 posterior to "
        f"{sig['confidence']!r} while the ep block answered {ep.confidence_mean}")

    node = next(n for n in SubgraphExpander(proj).expand(["intpair"], depth=1)["nodes"]
                if n["id"] == "intpair")
    assert node["confidence"] == pytest.approx(0.9), (
        "the subgraph display mean truncated an integer 9/1 posterior to "
        f"{node['confidence']!r}")


def test_trends_prints_only_stored_alpha_beta(sdk):
    """`trends` prints α/β, so those numbers must be STORED, not defaults.

    `coalesce(…, 1.0)` printed `β=1.0` for a half pair that stores no β at all,
    and admitted rows with no complete pair.
    """
    from tests._embedded import _wipe_or as wipe
    from tortoise.analyze import analyze

    proj = sdk._get_proj()
    wipe(proj)
    g = proj.g
    # `$entity` is matched with CONTAINS, so both contents carry the exact
    # phrase `classify` extracts ('zebra strategy over time?').
    g.query("CREATE (:Point {id:'half', content:'zebra strategy over time? "
            "alpha-only claim', is_operator:false, status:'live', "
            "posterior_alpha:3.0})")
    g.query("CREATE (:Point {id:'meas', content:'zebra strategy over time? "
            "measured claim', is_operator:false, status:'live', "
            "posterior_alpha:9.0, posterior_beta:1.0})")
    answer = analyze("how has the ZEBRA strategy changed over time?",
                     proj=proj, use_llm=False)["answer"]
    assert "measured claim" in answer, answer
    assert "α=3.0" not in answer, (
        "a half pair was printed with a fabricated β from the coalesce default: "
        + answer)


def test_analyze_coerces_integer_alpha_beta(sdk):
    """FalkorDB `/` truncates INTEGERS — the belief expression must coerce.

    `update_point(posterior_alpha=9, posterior_beta=1)` is accepted and stores
    INTs with no mirror (only the `ep_*` baseline fields are gated). Without
    `toFloat`, `9/10` truncated to `0`, so a claim measured at 0.90 rendered
    `confidence: 0.00`, ranked BELOW a 0.10 claim, and failed its own belief
    floor — the exact inversion §6.3 removes, on a claim that HAS a belief.
    """
    from tests._embedded import _wipe_or as wipe
    from tortoise.analyze import analyze

    proj = sdk._get_proj()
    wipe(proj)
    g = proj.g
    # Ints on purpose — the storage layer keeps them as ints.
    g.query("CREATE (:Point {id:'strong', content:'evidence ai strategy is "
            "working via strong', is_operator:false, status:'live', "
            "posterior_alpha:9, posterior_beta:1})")
    g.query("CREATE (:Point {id:'weak', content:'weak support claim', "
            "is_operator:false, status:'live', posterior_alpha:1, "
            "posterior_beta:9})")
    g.query("CREATE (:Point {id:'tgt', content:'evidence ai strategy is working', "
            "is_operator:false, status:'live'})")
    g.query("CREATE (:Point {id:'op', content:'IMPL', op_type:'IMPL', "
            "is_operator:true, status:'live'})")
    g.query("MATCH (a:Point {id:'strong'}), (b:Point {id:'weak'}), "
            "(o:Point {id:'op'}), (t:Point {id:'tgt'}) "
            "CREATE (a)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:1}]->(t), "
            "       (b)<-[:IMPL {idx:0}]-(o)-[:IMPL {idx:2}]->(t)")
    answer = analyze("show me the evidence chain for the AI strategy",
                     proj=proj, use_llm=False)["answer"]
    assert "0.90" in answer, (
        "integer α/β truncated to 0 — the toFloat guard is missing: " + answer)
    assert "0.00" not in answer, answer
    assert answer.index("strong") < answer.index("weak"), (
        "the 0.90 claim must outrank the 0.10 one: " + answer)


def test_why_conflict_severity_and_tradeoff_weight_use_the_shared_reader(sdk):
    """`severity`/`ep_weight` ordered on `_mean(coalesce(…, 1.0), …)` = 0.5.

    An ABSENT counter-claim therefore took a real severity tier (competing for
    the ≤2 cap), and an absent alternative was presented as STRONGER than a
    measured 0.4 one. Both now weigh `None` and sort in their own last tier.
    """
    from tortoise.why import _assemble_conflicts, _assemble_tradeoffs

    proj = sdk._get_proj()
    target = sdk.create_point("statement", "the contested claim")["id"]
    absent = sdk.create_point("statement", "absent counter-claim")["id"]
    measured = sdk.create_point("statement", "measured 0.55 counter-claim")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 11.0, "
                 "n.posterior_beta = 9.0", params={"id": measured})
    for nander in (absent, measured):
        proj.g.query(
            "MATCH (c:Point {id:$c}), (t:Point {id:$t}) CREATE (c)-[:NAND]->(t)",
            params={"c": nander, "t": target})

    blocks = {target: {}}
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) WITH n MATCH (c:Point)-[r:NAND]->(n) "
        "RETURN n.id, c.id, c.content, "
        "  c.confidence, c.posterior_alpha, c.posterior_beta, "
        "  c.ep_alpha, c.ep_beta, "
        "  null AS c1, null AS c2, null AS c3, null AS c4, null AS c5",
        params={"id": target}).result_set
    _assemble_conflicts(rows, blocks)
    nands = blocks[target]["nands"]
    by_id = {n["point_id"]: n for n in nands}
    assert by_id[measured]["severity"] == "medium", by_id[measured]
    assert by_id[absent]["severity"] is None, (
        "an absent counter-claim took a real severity tier on a fabricated 0.5: "
        + repr(by_id[absent]))

    # Tradeoffs: absent must sort BELOW a measured 0.4 alternative.
    decision = sdk.create_point("statement", "the decision",
                                status="live")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.pointKind = 'decision'",
                 params={"id": decision})
    absent_alt = sdk.create_point("statement", "absent alternative")["id"]
    measured_alt = sdk.create_point("statement", "measured 0.4 alternative")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 2.0, "
                 "n.posterior_beta = 3.0", params={"id": measured_alt})
    for alt in (absent_alt, measured_alt):
        proj.g.query(
            "MATCH (d:Point {id:$d}), (a:Point {id:$a}) CREATE (d)-[:IMPL]->(a)",
            params={"d": decision, "a": alt})
    tblocks = {decision: {}}
    trows = proj.g.query(
        "MATCH (n:Point {id:$id}) WITH n MATCH (n)-[:IMPL]->(alt:Point) "
        "RETURN n.id, coalesce(n.pointKind,''), alt.id, alt.content, "
        "  coalesce(alt.pointKind,''), null AS o1, null AS o2, "
        "  alt.confidence, alt.posterior_alpha, alt.posterior_beta, "
        "  alt.ep_alpha, alt.ep_beta",
        params={"id": decision}).result_set
    _assemble_tradeoffs(trows, tblocks)
    alts = {t["point_id"]: t for t in tblocks[decision].get("tradeoffs", [])}
    assert alts, ("no tradeoffs assembled — the fixture did not build a "
                  "decision with two alternatives")
    assert alts[measured_alt]["ep_weight"] == pytest.approx(0.4, abs=1e-4), alts
    assert alts[absent_alt]["ep_weight"] is None, (
        "an absent alternative was weighed 0.5 and presented as stronger than "
        "a measured 0.4: " + repr(alts[absent_alt]))
    order = [t["point_id"] for t in tblocks[decision]["tradeoffs"]]
    assert order.index(measured_alt) < order.index(absent_alt), order


def test_why_half_posterior_is_not_contested_and_has_no_variance(sdk):
    """A HALF pair (β stored, α absent) got `variance = 1/12` — the MAXIMUM.

    `_assemble_ep_rows` computed the variance from independently-coalesced
    operands, so a claim whose stored posterior is `(null, 3.0)` produced a
    full-entropy Beta(1,3)… and `contested: true` beside `ep_state: "absent"`,
    firing the volunteer's "read the counterargument" for a claim that has no
    counterargument. Presence is a property of the PAIR, so a half pair has no
    variance to report and cannot be contested.
    """
    from tortoise.search_engine import EP_STATE_ABSENT
    from tortoise.why import _assemble_ep_rows

    proj = sdk._get_proj()
    half = sdk.create_point("statement", "half pair claim")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_beta = 3.0",
                 params={"id": half})
    blocks = {half: {}}
    # The REAL `_EP_CYPHER` shape (13 columns) — the coalesced α/β inputs, then
    # the raw columns the presence predicate reads. Built here so the fixture
    # exercises the presence path and not the legacy 7-col double branch.
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.id, "
        "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
        "  coalesce(n.posterior_beta, n.ep_beta, 1.0), "
        "  n.ep_alpha IS NOT NULL AS measured, '' AS status, "
        "  false AS outdated, false AS baseline_set, true AS has_belief, "
        "  n.posterior_alpha AS pa, n.ep_alpha AS ea, n.confidence AS conf, "
        "  n.posterior_beta AS pb, n.ep_beta AS eb",
        params={"id": half}).result_set
    _assemble_ep_rows(rows, blocks)
    block = blocks[half]["ep"]
    assert block.get("ep_state") == EP_STATE_ABSENT, block
    assert block.get("variance") in (None, 0.0), (
        "a half pair (β only) was given a variance — the coalesce default "
        "invented an α it does not store: " + repr(block))
    assert block.get("contested") is not True, (
        "a claim with no belief at all was marked contested: " + repr(block))


def test_ranking_conflict_boost_ignores_a_counter_claim_with_no_belief(sdk):
    """`coalesce(…, 1.0)` gave an ABSENT counter-claim a 0.5 severity mean.

    The W4 boost fired on a claim whose belief does not exist, and — worse —
    the fabricated 0.5 landed in the `medium` tier positioned ABOVE a genuinely
    measured 0.4 counter-claim, so the unmeasured conflict won the ≤2 budget.
    """
    from tortoise.ranking import resolve_contested_relevance

    proj = sdk._get_proj()
    target = sdk.create_point("statement", "the contested claim")["id"]
    absent = sdk.create_point("statement", "absent zebra counter-claim")["id"]
    measured = sdk.create_point("statement", "measured zebra counter-claim")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 2.0, "
                 "n.posterior_beta = 3.0", params={"id": measured})
    for nander in (absent, measured):
        proj.g.query(
            "MATCH (c:Point {id:$c}), (t:Point {id:$t}) CREATE (c)-[:NAND]->(t)",
            params={"c": nander, "t": target})

    # The real read (no fixture double). The boost itself legitimately fires on
    # a NAND edge — the edge IS the evidence (the same rule the why-layer uses
    # when it surfaces an ungraded conflict). What must NOT happen is an
    # ungraded claim winning the <=2 relevance BUDGET against a measured one:
    # pre-fix the coalesce default weighed it 0.5 — ABOVE the measured 0.4 —
    # so the measured counter-claim was the one squeezed out.
    crowded = sdk.create_point("statement", "contested by three")["id"]
    filler_a = sdk.create_point("statement", "zebra filler a")["id"]
    filler_b = sdk.create_point("statement", "zebra filler b")["id"]
    real = sdk.create_point("statement", "zebra measured 0.4")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 2.0, "
                 "n.posterior_beta = 3.0", params={"id": real})
    for nander in (filler_a, filler_b, real):
        proj.g.query(
            "MATCH (c:Point {id:$c}), (t:Point {id:$t}) CREATE (c)-[:NAND]->(t)",
            params={"c": nander, "t": crowded})
    got = resolve_contested_relevance(proj, [crowded], "zebra measured")
    assert crowded in got, ("the budget dropped a genuinely measured "
                            "counter-claim: " + repr(got))

    # And the ordering itself, read through the resolver's own key: every
    # measured candidate precedes every absent one.
    from tortoise.ranking import _contested_claim_key
    measured_key = _contested_claim_key({"mean": 0.4, "relevance": 0.0,
                                         "claim_id": "a", "nid": 1})
    absent_key = _contested_claim_key({"mean": None, "relevance": 0.0,
                                       "claim_id": "a", "nid": 1})
    assert measured_key < absent_key, (measured_key, absent_key)


def test_conflict_dedupe_ranks_a_nullable_mean_without_raising(sdk):
    """Two rows for ONE counter-claim, one answering a float and one `None`.

    The dedupe compared `(mean, content)` tuples directly, so the moment #11702
    made `mean` nullable a float/None pair raised `TypeError` inside the read's
    outer handler — which returns `{}` for the WHOLE read, dropping the boost
    for every point in it, not only the offending one. Pre-#11702 the comparison
    was unreachable (the mean was always a float), so this is a regression the
    change itself introduced.

    The shape that produces it: claim `X` has NO belief and NANDs the target
    directly, while ALSO being the idx-0 source of a NAND operator `O` that
    carries a belief and NANDs the same target. Both rows share
    `coalesce(id(src), id(c)) == id(X)`; one resolves a float from `O`, the
    other `None` from `X`.
    """
    from tortoise.ranking import _claim_rank, resolve_contested_relevance

    # The pure key is total over the pair that used to raise.
    assert _claim_rank({"mean": 0.75, "content": "x"})[0] is True
    assert _claim_rank({"mean": None, "content": "x"})[0] is False
    assert (_claim_rank({"mean": 0.75, "content": "x"})
            > _claim_rank({"mean": None, "content": "x"})), (
        "a measured claim must rank above an absent one in the dedupe")

    proj = sdk._get_proj()
    target = sdk.create_point("statement", "dedupe target")["id"]
    bare = sdk.create_point("statement", "zebra bare counter-claim")["id"]
    # The NAND operator: is_operator + op_type, carrying the belief.
    op = sdk.create_point("statement", "zebra operator counter")["id"]
    proj.g.query(
        "MATCH (o:Point {id:$o}) SET o.is_operator = true, "
        "o.op_type = 'NAND', o.posterior_alpha = 3.0, o.posterior_beta = 1.0",
        params={"o": op})
    proj.g.query(
        "MATCH (b:Point {id:$b}), (t:Point {id:$t}) CREATE (b)-[:NAND]->(t)",
        params={"b": bare, "t": target})
    # `r.idx > 0` is part of the read's WHERE: the operator's own NAND must be
    # indexed, or the row is NULL-filtered and the two paths never collide.
    proj.g.query(
        "MATCH (o:Point {id:$o}), (t:Point {id:$t}) "
        "CREATE (o)-[:NAND {idx:1}]->(t)",
        params={"o": op, "t": target})
    proj.g.query(
        "MATCH (o:Point {id:$o}), (b:Point {id:$b}) "
        "CREATE (b)-[:INPUT {idx:0}]->(o)", params={"o": op, "b": bare})

    got = resolve_contested_relevance(proj, [target], "zebra counter-claim")
    assert target in got, (
        "the read returned nothing for a contested claim — a TypeError in the "
        "null/float dedupe is swallowed by the outer handler and drops EVERY "
        "point's boost: " + repr(got))


def test_write_verb_ep_updated_reads_the_shared_presence_predicate(sdk):
    """`ep_updated` used a local `α IS NOT NULL OR ep_α IS NOT NULL` spelling.

    So a MIRROR-only claim — a genuinely served belief, e.g. 0.95 — and a
    β-only half pair both reported `ep_updated: false` on the write verb, while
    every read surface called the mirror-only one measured. Same defect class
    (a second presence spelling), different surface.
    """
    from tortoise.search_engine import ep_has_belief_cypher

    proj = sdk._get_proj()
    mirror_only = sdk.create_point("statement", "mirror-only write-verb")["id"]
    beta_only = sdk.create_point("statement", "beta-only write-verb")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.confidence = 0.95",
                 params={"id": mirror_only})
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_beta = 3.0",
                 params={"id": beta_only})
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, "
        f"{ep_has_belief_cypher('n')}",
        params={"ids": [mirror_only, beta_only]}).result_set
    got = {r[0]: bool(r[1]) for r in rows}
    assert got[mirror_only] is True, (
        "a mirror-only claim (a real served belief) read as 'no belief': "
        + repr(got))
    assert got[beta_only] is False, got


def test_no_surface_fabricates_a_variance_for_an_absent_belief(sdk):
    """`ep_pair_stored`: a WIDTH also needs both operands (#11702 cycle 9).

    Cycle 7 gave `why` the half-pair guard, but `annotate_ep_batch` and all
    three rankers kept computing `_beta_variance` from the `coalesce(…, 1.0)`
    defaults, so a claim with NO belief still reported the Beta(1,1) MAXIMUM
    1/12 — "there is no belief here, and it is maximally uncertain" — and an
    α-only half pair reached `contested: True` and emitted the recall warning
    `['contested']` for a claim nothing measured. Measured before the fix:

        annotate_ep_batch : mean=None state='absent' variance=0.0833
        recall_ranking    : conf=None state='absent' variance=0.0833
        (α-only) contested: True on annotate + all three rankers
        why               : variance=0.0 contested=False   <- the only guarded one

    One rule now, imported by all five surfaces.
    """
    from tortoise.ranking import GapsRanker, GraphRanker, StateRanker
    from tortoise.search_engine import EP_STATE_ABSENT, annotate_ep_batch, ep_pair_stored
    from tortoise.why import _assemble_ep_rows

    # The rule itself, in the four shapes that matter.
    assert ep_pair_stored(posterior_alpha=2.0, posterior_beta=3.0) is True
    assert ep_pair_stored(posterior_alpha=2.0) is False            # half
    assert ep_pair_stored(posterior_beta=3.0) is False             # half
    assert ep_pair_stored(ep_alpha=2.0, ep_beta=3.0) is True
    assert ep_pair_stored() is False

    proj = sdk._get_proj()
    absent = sdk.create_point("statement", "no belief at all")["id"]
    alpha_only = sdk.create_point("statement", "alpha-only half pair")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 0.5",
                 params={"id": alpha_only})

    # annotate_ep_batch: the EpBreakdown every SDK consumer reads.
    bds = annotate_ep_batch(proj.g, [absent, alpha_only])
    for pid, label in ((absent, "absent"), (alpha_only, "alpha-only")):
        bd = bds[pid]
        assert bd.ep_state == EP_STATE_ABSENT, (label, bd)
        assert bd.variance == 0.0, (
            f"{label}: a claim with no belief reported a posterior WIDTH "
            f"{bd.variance} — the coalesced 1.0 default fabricated 1/12: {bd}")
        assert bd.contested is False, (
            f"{label}: an absent belief read contested: {bd}")

    # All three rankers.
    ids = [absent, alpha_only]
    for name, signals in (
            ("state", StateRanker(projection=proj)._fetch_point_signals(ids)),
            ("graph", GraphRanker(projection=proj)._fetch_signals(ids, "point")),
            ("gaps", GapsRanker(projection=proj)._fetch_confidence_signals(ids)),
    ):
        for pid in ids:
            sig = signals[pid]
            assert sig["ep_state"] == EP_STATE_ABSENT, (name, pid, sig)
            assert sig["variance"] == 0.0, (
                f"{name} ranker reported variance {sig['variance']} for an "
                f"absent belief: {sig}")
            assert sig["contested"] is False, (name, pid, sig)

    # `why` agrees with all of them (it had the guard first).
    blocks = {pid: {} for pid in ids}
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, "
        "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
        "  coalesce(n.posterior_beta, n.ep_beta, 1.0), "
        "  n.ep_alpha IS NOT NULL AS measured, coalesce(n.status,'') AS st, "
        "  coalesce(n.outdated, false) AS od, false AS baseline_set, "
        "  true AS has_belief, n.posterior_alpha AS pa, n.ep_alpha AS ea, "
        "  n.confidence AS conf, n.posterior_beta AS pb, n.ep_beta AS eb",
        params={"ids": ids}).result_set
    _assemble_ep_rows(rows, blocks)
    for pid in ids:
        ep = blocks[pid]["ep"]
        assert ep["variance"] == 0.0, ep
        assert ep["contested"] is False, ep

    # And a genuinely stored pair is STILL wide and still contested, so the
    # guard cannot pass by answering "no" for everything.
    # Beta(1, 1): mean 0.5, variance 1/12 — the maximum, and legitimately wide.
    wide = sdk.create_point("statement", "a genuinely uncertain claim")["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n.posterior_alpha = 1.0, "
                 "n.posterior_beta = 1.0", params={"id": wide})
    bd = annotate_ep_batch(proj.g, [wide])[wide]
    assert bd.variance == pytest.approx(1 / 12, abs=1e-6), bd
    assert bd.contested is True, bd
    state_sig = StateRanker(projection=proj)._fetch_point_signals([wide])[wide]
    assert state_sig["variance"] == pytest.approx(1 / 12, abs=1e-6), state_sig
    assert state_sig["contested"] is True, state_sig
