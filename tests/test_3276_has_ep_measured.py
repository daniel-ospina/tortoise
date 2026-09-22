"""Issue #3276 — ``has_ep`` must mean "EP MEASURED this claim".

Pre-fix, the serialized ``has_ep`` predicate was
``posterior_alpha IS NOT NULL OR ep_alpha IS NOT NULL`` — "a persisted prior
exists". #2199 stamps a kind-derived baseline (``ep_alpha=3, ep_beta=1``,
``baseline_set=true``, ``baseline_source='system-default'``) on every decide
part created without an explicit status, so a NEVER-measured ``decision``
read:

    {'confidence_mean': 0.75, 'has_ep': True, 'evidence': {'total': 0}}

while an equally unmeasured ``observation`` read the neutral Beta(1,1)
0.5 / has_ep False. The #2206 relevance gate
(``has_ep AND confidence_mean >= 0.5``) therefore counted every
never-measured decision as "we already decided this".

The honest contract under test (three-state, unambiguous):

    measured=True                    → EP measured (real posterior flushed)
    measured=False, baseline=True    → prior-only (declared baseline; the
                                       confidence_mean is the PRIOR mean —
                                       never presented as measured)
    measured=False, baseline=False   → unmeasured (neutral 0.5)

MUST run against a live FalkorDB (Docker). Test-prefixed isolated graph.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import (
    annotate_ep_batch,
    ep_measurement_state,
)

# Requires live FalkorDB (Docker). Skip gracefully when unavailable.
_GRAPH = f"tortoise_test_3276hasep_{uuid.uuid4().hex[:8]}"
_DB_URI = f"docker://:falkordb@localhost:6379/{_GRAPH}"
FALKORDB_AVAILABLE = False
_OLD_URI = os.environ.get("TORTOISE_DB_URI")
try:
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    from tortoise.sdk import TortoiseSDK as _ProbeSDK
    _probe = _ProbeSDK()
    _probe._get_proj().g.query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    pass
finally:
    if _OLD_URI is not None:
        os.environ["TORTOISE_DB_URI"] = _OLD_URI
    else:
        os.environ.pop("TORTOISE_DB_URI", None)

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")


@pytest.fixture(autouse=True)
def _isolated_db_uri():
    """Point THIS test at its own uniquely-named docker graph (#176 leak guard)."""
    _old = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    yield
    if _old is not None:
        os.environ["TORTOISE_DB_URI"] = _old
    else:
        os.environ.pop("TORTOISE_DB_URI", None)


def _fresh_sdk():
    """SDK bound to the isolated docker graph."""
    sdk = TortoiseSDK(db_path=None, namespace=None)
    sdk._db_uri = _DB_URI
    sdk._proj = None  # force re-init on first use
    return sdk


def _raw(sdk, pid):
    return sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.ep_alpha, n.ep_beta, "
        "  coalesce(n.baseline_set, false), n.baseline_source, "
        "  n.posterior_alpha, n.posterior_beta, n.status, n.pointKind",
        params={"id": pid},
    ).result_set[0]


def _system_default_decision(sdk, content):
    """The exact #3276 repro: a decide part with NO explicit status is born
    live with the #2199 kind-derived system-default baseline."""
    return sdk.create_point(kind="decision", content=content)["id"]


class TestMeasurementStateHelper:
    """The pure Python twin matches the Cypher predicate."""

    def test_three_states(self):
        assert ep_measurement_state(
            posterior_alpha=22.0, ep_alpha=None, baseline_set=False) == "measured"
        # legacy EP prior (no baseline flag) is a measurement (back-compat)
        assert ep_measurement_state(
            posterior_alpha=None, ep_alpha=9.0, baseline_set=False) == "measured"
        # #2199 baseline, never EP'd → prior-only
        assert ep_measurement_state(
            posterior_alpha=None, ep_alpha=3.0, baseline_set=True) == "baseline"
        assert ep_measurement_state(
            posterior_alpha=None, ep_alpha=None, baseline_set=False) == "unmeasured"


class TestUnmeasuredBaselineDecision:
    """The bug: a never-measured decide part must NOT read has_ep=True."""

    def test_system_default_baseline_decision_is_not_measured(self):
        sdk = _fresh_sdk()
        try:
            pid = _system_default_decision(
                sdk, "Quokka patrol unmeasured decision zzqx")
            g = sdk._get_proj().g
            ep = annotate_ep_batch(g, [pid])[pid]

            # THE regression: pre-fix has_ep was True at 0.75.
            assert ep.has_ep is False, f"unmeasured decision read has_ep=True: {ep}"
            assert ep.measured is False, ep
            assert ep.baseline is True, ep
            # exact issue shape: zero evidence
            assert ep.evidence.total == 0, ep
            assert ep.evidence.impl_count == 0 and ep.evidence.nand_count == 0
            # 0.75 is the DECLARED baseline prior — not a measured confidence.
            assert ep.confidence_mean == pytest.approx(0.75, abs=1e-4)
            assert ep.contested is False

            # Raw graph: the baseline is a creation-time prior, no posterior.
            ea, eb, bl_set, bl_src, pa, _pb, status, kind = _raw(sdk, pid)
            assert (ea, eb) == (3.0, 1.0), (ea, eb)
            assert bl_set is True and bl_src == "system-default", (bl_set, bl_src)
            assert pa is None, "system-default decision must have no EP posterior"
            assert status == "live" and kind == "decision"
        finally:
            sdk.close()

    def test_unmeasured_observation_is_neutral_not_baseline(self):
        """The contrast case from the issue: unmeasured observation = 0.5,
        has_ep False, and no baseline."""
        sdk = _fresh_sdk()
        try:
            pid = sdk.create_point(
                "observation", "Quokka patrol unmeasured observation zzqx",
                status="live")["id"]
            ep = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
            assert ep.has_ep is False and ep.measured is False and ep.baseline is False, ep
            assert ep.evidence.total == 0
            assert ep.confidence_mean == pytest.approx(0.5, abs=1e-4)
        finally:
            sdk.close()

    def test_measuring_a_prior_only_decision_flips_it_to_measured(self):
        """Preserve the true positive: once EP actually runs on a baseline'd
        decision, it reads measured with the REAL posterior (not 0.75)."""
        sdk = _fresh_sdk()
        try:
            src = sdk.create_point(
                "statement", "Quokka patrol source zzqx", status="live")["id"]
            sdk.set_point_baseline(src, 10.0, 1.0)
            pid = _system_default_decision(
                sdk, "Quokka patrol approved decision zzqx")
            sdk.create_operator("IMPL", src, [pid], direction="unidirectional")

            g = sdk._get_proj().g
            before = annotate_ep_batch(g, [pid])[pid]
            assert before.has_ep is False and before.baseline is True, before

            # require_calibration=False: the #1157 calibration gate is a
            # separate fail-closed posture; this test targets measurement
            # state (sibling tests on the shared graph may leave an
            # uncalibrated observation behind — #176).
            result = sdk.compute_confidence(require_calibration=False)
            assert result["converged"] is True, result

            after = annotate_ep_batch(g, [pid])[pid]
            assert after.has_ep is True and after.measured is True, after
            assert after.baseline is False, after
            assert after.evidence.total == 1
            # The real posterior, NOT the 0.75 kind-default.
            assert after.confidence_mean != pytest.approx(0.75, abs=1e-4), after
            assert after.confidence_mean == pytest.approx(
                sdk.get_confidence(pid, require_calibration=False)["mean"],
                abs=1e-3)
            # Posterior persisted (the column only a real EP run writes).
            assert _raw(sdk, pid)[4] is not None
        finally:
            sdk.close()


class TestGateReadsRealGraph:
    """The #2206 relevance gate on REAL search hits (issue verification
    checklist: it must not be exercised only on hand-built ``ep`` dicts)."""

    def test_unmeasured_baseline_decision_does_not_clear_ep_gate(self):
        sdk = _fresh_sdk()
        try:
            pid = _system_default_decision(
                sdk, "Quokka patrol unmeasured decision zzqx")
            # One shared token with the query ("unmeasured") → below the
            # 2-token floor, so ONLY the EP branch can clear the gate.
            hits = sdk.tortoise_fts_query(
                "unmeasured quagga", entity_type="point", limit=50,
                w4_enrich=False)
            hit = next((h for h in hits if h.get("id") == pid), None)
            assert hit is not None, "the decision must surface for the gate test"
            assert hit["ep"]["has_ep"] is False, hit["ep"]
            assert hit["ep"]["measured"] is False, hit["ep"]
            assert hit["ep"]["baseline"] is True, hit["ep"]
            assert sdk._issue_insight_relevant(hit, "unmeasured quagga") is False, hit
        finally:
            sdk.close()

    def test_measured_decision_clears_ep_gate(self):
        sdk = _fresh_sdk()
        try:
            src = sdk.create_point(
                "statement", "Quokka patrol source zzqx", status="live")["id"]
            sdk.set_point_baseline(src, 10.0, 1.0)
            pid = _system_default_decision(
                sdk, "Quokka patrol approved decision zzqx")
            sdk.create_operator("IMPL", src, [pid], direction="unidirectional")
            assert sdk.compute_confidence(require_calibration=False)["converged"] is True

            hits = sdk.tortoise_fts_query(
                "approved quagga", entity_type="point", limit=50,
                w4_enrich=False)
            hit = next((h for h in hits if h.get("id") == pid), None)
            assert hit is not None
            assert hit["ep"]["measured"] is True and hit["ep"]["has_ep"] is True
            # EP branch fires despite the single-token lexical overlap.
            assert sdk._issue_insight_relevant(hit, "approved quagga") is True, hit
        finally:
            sdk.close()


class TestCrossSurfaceHonesty:
    """Ranker + why surfaces agree with the annotation on the prior-only state."""

    def test_rankers_and_why_agree_on_prior_only(self):
        sdk = _fresh_sdk()
        try:
            pid = _system_default_decision(
                sdk, "Quokka patrol prioronly decision zzqx")
            proj = sdk._get_proj()
            from tortoise.ranking import GapsRanker, StateRanker
            from tortoise.why import assemble_why_blocks

            ss = StateRanker(proj)._fetch_point_signals([pid])[pid]
            gaps = GapsRanker(proj)._fetch_confidence_signals([pid])[pid]
            assert ss["has_ep"] is False, ss
            assert gaps["has_ep"] is False, gaps
            # Prior mean preserved for ranking (#2199/#2262), never contested.
            assert ss["confidence"] == pytest.approx(0.75, abs=1e-6), ss
            assert ss["contested"] is False and gaps["contested"] is False

            ep = assemble_why_blocks(proj, [pid])[pid]["ep"]
            assert ep["has_ep"] is False, ep
            assert ep["measured"] is False and ep["baseline"] is True, ep
            assert ep["confidence_mean"] == pytest.approx(0.75, abs=1e-4), ep
        finally:
            sdk.close()

    def test_prior_only_low_credibility_not_contested_in_review_prune(self):
        """#3276 review fix: `_review_prune`'s variance scan must use the same
        measured predicate. A prior-only decision with a LOW-credibility author
        baseline (Beta(2,1), variance 0.0556 > the 0.04 threshold) used to read
        `contested` on review_connections while annotate_ep_batch read it
        unmeasured — a fresh #2206-class disagreement."""
        sdk = _fresh_sdk()
        try:
            src = sdk.create_point(
                "statement", "Quokka prune source zzqx", status="live")["id"]
            pid = sdk.create_point(
                "decision", "Quokka prioronly lowcred zzqx",
                credibility="low")["id"]
            sdk.create_operator("IMPL", src, [pid], direction="unidirectional")

            ep = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
            assert ep.measured is False and ep.baseline is True, ep
            # The low-credibility prior's variance is above the threshold, but
            # an unmeasured claim is never contested.
            assert ep.variance > 0.04 and ep.contested is False, ep

            out = sdk.review_connections(mode="prune", scope=None, prune_limit=50)
            contested = [e for e in out["prune"] if e["issue"] == "contested"]
            assert not any(
                e["detail"].get("contested_endpoint") == pid for e in contested
            ), f"prior-only point surfaced contested: {contested}"
        finally:
            sdk.close()


def _supersede_measured_decision(sdk):
    """A measured decision (EP posterior) that is then terminalized."""
    src = sdk.create_point(
        "statement", "Quokka terminal source zzqx", status="live")["id"]
    sdk.set_point_baseline(src, 10.0, 1.0)
    pid = _system_default_decision(
        sdk, "Quokka measured terminal decision zzqx")
    sdk.create_operator("IMPL", src, [pid], direction="unidirectional")
    assert sdk.compute_confidence(require_calibration=False)["converged"] is True
    succ = sdk.create_point(
        "statement", "Quokka successor zzqx", status="live")["id"]
    sdk.supersede_point(pid, succ)
    return pid


class TestTerminalBaselineNotPriorOnly:
    """#3276 review fix: a TERMINAL claim that was measured-and-baseline'd is
    neither measured nor prior-only — its 0.5 is the #2490 decayed vacuity
    posterior, not the 0.75 prior. `baseline` must not lie on terminal rows."""

    def test_terminal_measured_baseline_clears_baseline_flag(self):
        sdk = _fresh_sdk()
        try:
            pid = _supersede_measured_decision(sdk)
            ep = annotate_ep_batch(sdk._get_proj().g, [pid])[pid]
            assert ep.has_ep is False and ep.measured is False, ep
            assert ep.baseline is False, (
                f"terminal claim mislabelled prior-only: {ep}")
            assert ep.confidence_mean == pytest.approx(0.5, abs=1e-4), ep

            from tortoise.why import assemble_why_blocks
            wep = assemble_why_blocks(sdk._get_proj(), [pid])[pid]["ep"]
            assert wep["measured"] is False and wep["baseline"] is False, wep
        finally:
            sdk.close()
