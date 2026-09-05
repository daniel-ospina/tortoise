"""Issue #2206 — cross-surface confidence agreement after EP convergence.

Regression targets (issue O/I/T): after EP converges, EVERY surface that
exposes a point's confidence returns the same, correct posterior — and none
of them return the 0.5 prior (the pre-fix get_confidence behavior) or an
edge ratio (the pre-fix search confidence_mean: 0.0 for a point whose
posterior was 0.88).

One contract: a point's confidence = the belief mean α/(α+β) of the
PERSISTED posterior when EP has run (posterior_alpha/beta), else the
persisted prior mean (ep_alpha/beta), else the neutral Beta(1,1) mean 0.5
(unmeasured). Surfaces under test, all for the SAME claim:
  * sdk.get_confidence()  → TortoiseEP.compute_confidence (posterior read)
  * annotate_ep_batch()   → the search annotation primitive (confidence_mean)
  * sdk.tortoise_fts_query() ep.confidence_mean (search surface)
  * sdk.recall_state() ep.confidence_mean + recall_ranking.confidence
  * the persisted n.confidence graph property

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
from tortoise.search_engine import annotate_ep_batch

# Requires live FalkorDB (Docker). Skip gracefully when unavailable.
_GRAPH = f"tortoise_test_2206conf_{uuid.uuid4().hex[:8]}"
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


def _make_point(sdk, content, kind="statement"):
    # #943: create_point defaults to status='draft'; the #780 draft filter
    # strips draft inputs (mirrors test_ep_directional's sweep) — mark live.
    return sdk.create_point(kind, content, status="live")


def _run_ep(sdk):
    """Run EP to convergence over the graph's operators (no-arg path: local
    EP over the dirty roots, exactly what a self-hosted 'dream' would do)."""
    result = sdk.compute_confidence()
    assert result["converged"] is True, f"EP did not converge: {result}"
    assert result["iterations"] > 0, f"EP ran zero iterations: {result}"


def _claim_surfaces(sdk, pid, query_text):
    """Read every confidence surface for one claim and return the numbers.

    All reads are belief reads post-#2206; get_confidence passes
    require_calibration=False because the calibration gate (#1157) is a
    SEPARATE fail-closed posture — this test targets surface AGREEMENT, and
    the test's decision-kind claims carry no explicit baseline.
    """
    proj = sdk._get_proj()
    graph = proj.g
    # (1) get_confidence → TortoiseEP posterior read.
    gc = sdk.get_confidence(pid, require_calibration=False)
    # (2) annotate_ep_batch — the search annotation primitive.
    ann = annotate_ep_batch(graph, [pid])[pid]
    # (3) search surface (full-scan by kind + by-query, w4 off so the raw
    #     annotate number is what rides; W4 projects the same belief anyway).
    hits = sdk.tortoise_fts_query(
        query_text, entity_type="point", limit=50, w4_enrich=False)
    search_hit = next((h for h in hits if h.get("id") == pid), None)
    # (4) recall surface.
    recall = sdk.recall_state(query_text, limit=50)
    recall_hit = next((r for r in recall if r.get("id") == pid), None)
    # (5) persisted n.confidence property.
    rows = graph.query(
        "MATCH (n:Point {id:$id}) RETURN n.confidence, n.posterior_alpha, "
        "n.posterior_beta, n.ep_alpha, n.ep_beta",
        params={"id": pid},
    ).result_set
    stored = rows[0] if rows else None
    return {
        "get_confidence": gc["mean"],
        "annotate": ann.confidence_mean,
        "search_ep": (search_hit or {}).get("ep", {}).get("confidence_mean"),
        "recall_ep": (recall_hit or {}).get("ep", {}).get("confidence_mean"),
        "recall_ranking": (recall_hit or {}).get("recall_ranking", {}).get("confidence"),
        "stored_confidence": float(stored[0]) if stored and stored[0] is not None else None,
        "posterior_persisted": stored is not None and stored[1] is not None,
    }


class TestCrossSurfaceAgreement:
    """Every read surface returns the same posterior after EP converges."""

    def test_high_posterior_claim_agrees_everywhere(self):
        """The issue's first reproduce: a point EP converged high reads the
        SAME high posterior on get_confidence / search / recall — never the
        0.5 prior, never an edge-ratio 0.0."""
        sdk = _fresh_sdk()
        try:
            # Source with a strong true baseline (T0: 10:1 → mean ~0.909).
            src = _make_point(sdk, "Quokka patrol source one zzqx")
            sdk.set_point_baseline(src["id"], 10.0, 1.0)
            # The decided option (decision kind → outside the #1157 gate).
            opt = _make_point(sdk, "Quokka patrol schedule approved zzqx", kind="decision")
            sdk.create_operator("IMPL", src["id"], [opt["id"]],
                                direction="unidirectional")
            _run_ep(sdk)

            pid = opt["id"]
            surf = _claim_surfaces(sdk, pid, "quokka patrol schedule approved")
            mean = surf["get_confidence"]
            # Converged high — NOT the 0.5 prior (issue indicator 1).
            assert mean > 0.55, f"claim posterior unexpectedly low: {mean}"
            assert mean != pytest.approx(0.5, abs=1e-6)
            assert surf["posterior_persisted"], \
                "EP must persist posterior_alpha/beta for the claim"
            # Every surface carries the same belief (rounding ≤ 4dp).
            for key in ("annotate", "search_ep", "recall_ep",
                        "recall_ranking", "stored_confidence"):
                got = surf[key]
                assert got is not None, f"{key} missing for converged claim"
                assert got == pytest.approx(mean, abs=1e-3), (
                    f"{key}={got} disagrees with get_confidence={mean} "
                    f"(posterior {surf.get('stored_confidence')})")
        finally:
            sdk.close()

    def test_low_posterior_claim_agrees_everywhere(self):
        """The issue's second reproduce: a decided option EP dragged low
        (≈0.16-class) reads the same LOW posterior everywhere — get_confidence
        must NOT fall back to the 0.5 prior."""
        sdk = _fresh_sdk()
        try:
            # Attacker with a strong true baseline NANDs the option down.
            attacker = _make_point(sdk, "Quokka patrol attacker source zzqx")
            sdk.set_point_baseline(attacker["id"], 10.0, 1.0)
            # Mild support so the option starts near neutral before the NAND.
            weak = _make_point(sdk, "Quokka patrol weak support zzqx")
            sdk.set_point_baseline(weak["id"], 1.1, 1.0)
            opt = _make_point(sdk, "Quokka patrol budget expanded zzqx", kind="decision")
            sdk.create_operator("IMPL", weak["id"], [opt["id"]],
                                direction="unidirectional")
            sdk.create_operator("NAND", attacker["id"], [opt["id"]])
            _run_ep(sdk)

            pid = opt["id"]
            surf = _claim_surfaces(sdk, pid, "quokka patrol budget")
            mean = surf["get_confidence"]
            # Converged LOW — a posterior well below the 0.5 prior.
            assert mean < 0.45, f"claim posterior unexpectedly high: {mean}"
            assert mean != pytest.approx(0.5, abs=1e-6)
            assert surf["posterior_persisted"], \
                "EP must persist posterior_alpha/beta for the claim"
            for key in ("annotate", "search_ep", "recall_ep",
                        "recall_ranking", "stored_confidence"):
                got = surf[key]
                assert got is not None, f"{key} missing for converged claim"
                assert got == pytest.approx(mean, abs=1e-3), (
                    f"{key}={got} disagrees with get_confidence={mean} "
                    f"(posterior {surf.get('stored_confidence')})")
        finally:
            sdk.close()

    def test_unmeasured_point_is_neutral_everywhere(self):
        """Pre-measurement agreement: a claim nobody measured reads the
        neutral Beta(1,1) mean 0.5 on every surface (absence of measurement
        is NOT low support — the search annotation must not read 0.0)."""
        sdk = _fresh_sdk()
        try:
            u = _make_point(sdk, "Quokka patrol unmeasured claim zzqx", kind="decision")
            pid = u["id"]
            surf = _claim_surfaces(sdk, pid, "quokka patrol unmeasured")
            mean = surf["get_confidence"]
            assert mean == pytest.approx(0.5, abs=1e-6)  # neutral uniform read
            assert surf["posterior_persisted"] is False
            for key in ("annotate", "search_ep", "recall_ep",
                        "recall_ranking"):
                got = surf[key]
                assert got == pytest.approx(0.5, abs=1e-3), (
                    f"{key}={got} must read neutral 0.5 for an unmeasured claim")
            # No EP run → n.confidence was never written (legitimately absent;
            # when present it must be the neutral 0.5, never 0.0).
            stored = surf["stored_confidence"]
            assert stored is None or stored == pytest.approx(0.5, abs=1e-3)
        finally:
            sdk.close()
