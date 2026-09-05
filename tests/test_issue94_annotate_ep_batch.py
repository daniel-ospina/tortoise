"""Issue #94 + #2206: annotate_ep_batch distinguishes "no evidence" from
"all NANDs", and confidence_mean is THE point's belief mean.

Post-#2206 contract: EpBreakdown.confidence_mean is the belief mean α/(α+β)
of the PERSISTED posterior when EP has run (posterior_alpha/beta), else the
persisted prior mean (ep_alpha/beta, e.g. a baseline), else the neutral
Beta(1,1) mean 0.5 — identical to sdk.get_confidence for the same point.
Unmeasured points (no persisted α/β) get has_ep=False + confidence_mean=0.5
(absence of measurement is NOT low support), while points with NAND edges
get has_ep=True + confidence_mean < 0.5 only once EP/evidence persisted.
contention/evidence remain the structural edge-ratio family — a DIFFERENT
quantity from confidence_mean (#2206 — no conflation).
"""
import os  # noqa: F401, I001
import sys  # noqa: F401

import pytest
from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import annotate_ep_batch

# Requires live FalkorDB (Docker). Skip gracefully when unavailable so the
# no-Docker embedded suite stays green (AGENTS.md). Mirrors the probe pattern
# in tests/test_integration_search.py.
FALKORDB_AVAILABLE = False
try:
    from tortoise.sdk import TortoiseSDK as _ProbeSDK
    _probe = _ProbeSDK()
    _probe._get_proj().g.query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    pass

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")


# ── Graph name MUST be test-prefixed (#99 guard) ─────────────────────────────
TEST_GRAPH = "tortoise_test_issue94"


@pytest.fixture(scope="module")
def sdk():
    """Module-scoped SDK with isolated test graph. Cleaned up after."""
    sdk = TortoiseSDK(db_path=None, namespace=TEST_GRAPH)
    yield sdk
    # Cleanup: delete all test Points (safe — test-prefixed graph)
    try:
        sdk.test_guard()
        proj = sdk._get_proj()
        proj.g.query("MATCH (n:Point) DETACH DELETE n")
    except Exception:
        pass
    sdk.close()


class TestAnnotateEpBatchIssue94:
    """EP batch annotation: no-evidence vs all-NAND distinction (#94) +
    belief-mean semantics (#2206)."""

    def test_isolated_point_is_neutral_not_zero(self, sdk):
        """Point with zero edges and no persisted EP → has_ep=False,
        confidence_mean=0.5 (neutral Beta(1,1) — #2206: absence of
        measurement is NOT low support, so never the old 0.0)."""
        # Create an isolated point (no operators)
        point = sdk.create_point("statement", "Isolated claim with no evidence")
        pid = point["id"]

        proj = sdk._get_proj()
        graph = proj.g

        result = annotate_ep_batch(graph, [pid])

        assert pid in result
        ep = result[pid]
        assert ep.has_ep is False, \
            f"Isolated point should have has_ep=False, got {ep.has_ep}"
        assert ep.confidence_mean == 0.5, \
            f"Isolated point should read the neutral mean 0.5, got {ep.confidence_mean}"
        assert ep.evidence.impl_count == 0
        assert ep.evidence.nand_count == 0
        assert ep.evidence.total == 0
        assert ep.contention == 0.0

    def test_nanded_point_edges_do_not_measure(self, sdk):
        """Point with NAND edges but NO persisted EP data → has_ep=False and
        the neutral 0.5 (edges alone never set a belief — #753, #2206)."""
        # Create a claim and two contradictory NAND sources
        claim = sdk.create_point("statement", "A claim that gets contradicted")
        claim_id = claim["id"]

        nand1 = sdk.create_point("statement", "NAND source 1: this is wrong")
        nand2 = sdk.create_point("statement", "NAND source 2: also wrong")

        sdk.create_operator("NAND", nand1["id"], [claim_id])
        sdk.create_operator("NAND", nand2["id"], [claim_id])

        proj = sdk._get_proj()
        graph = proj.g

        result = annotate_ep_batch(graph, [claim_id])

        assert claim_id in result
        ep = result[claim_id]
        # has_ep = persisted EP data (post-#753); edges alone don't set it.
        assert ep.has_ep is False, \
            f"NANDed point should have has_ep=False without persisted EP, got {ep.has_ep}"
        # 0 IMPL, 2 NAND, no persisted α/β → neutral 0.5 (#2206: unmeasured
        # is NOT low support — the old edge-ratio 0.0 conflated the two).
        assert ep.confidence_mean == 0.5, \
            f"Unmeasured NANDed point should read neutral 0.5, got {ep.confidence_mean}"
        assert ep.evidence.impl_count == 0
        assert ep.evidence.nand_count == 2
        assert ep.evidence.total == 2
        # contention = 2/2 = 1.0 — the structural edge-ratio family stays.
        assert ep.contention == 1.0, \
            f"All-NAND point should have contention=1.0, got {ep.contention}"

    def test_mixed_edges_measured_only_after_ep(self, sdk):
        """Point with both IMPL and NAND edges but no persisted EP → neutral
        0.5; contention keeps the 0.5 edge ratio (distinct quantity)."""
        claim = sdk.create_point("statement", "A disputed claim")
        claim_id = claim["id"]

        supporter = sdk.create_point("statement", "IMPL support")
        attacker = sdk.create_point("statement", "NAND attack")

        sdk.create_operator("IMPL", supporter["id"], [claim_id])
        sdk.create_operator("NAND", attacker["id"], [claim_id])

        proj = sdk._get_proj()
        graph = proj.g

        result = annotate_ep_batch(graph, [claim_id])

        assert claim_id in result
        ep = result[claim_id]
        assert ep.has_ep is False, \
            f"Point with edges should have has_ep=False without persisted EP, got {ep.has_ep}"
        # No persisted α/β → neutral 0.5 (the edge ratio was also 0.5 here —
        # coincidence; the two quantities are NOT the same).
        assert ep.confidence_mean == 0.5, \
            f"Unmeasured mixed point should read neutral 0.5, got {ep.confidence_mean}"
        assert ep.evidence.impl_count == 1
        assert ep.evidence.nand_count == 1
        assert ep.evidence.total == 2
        assert ep.contention == 0.5

    def test_batch_mixed_isolated_and_nanded(self, sdk):
        """Batch call: isolated point + NANDed point in same query."""
        isolated = sdk.create_point("statement", "Isolated point in batch")
        isolated_id = isolated["id"]

        claim = sdk.create_point("statement", "Claim in batch")
        claim_id = claim["id"]
        attacker = sdk.create_point("statement", "Batch attacker")
        sdk.create_operator("NAND", attacker["id"], [claim_id])

        proj = sdk._get_proj()
        graph = proj.g

        result = annotate_ep_batch(graph, [isolated_id, claim_id])

        # Isolated — neutral 0.5, not the old edge-ratio 0.0 (#2206).
        iso_ep = result[isolated_id]
        assert iso_ep.has_ep is False
        assert iso_ep.confidence_mean == 0.5

        # NANDed — edges alone don't measure; neutral 0.5, contention 1.0.
        claim_ep = result[claim_id]
        assert claim_ep.has_ep is False
        assert claim_ep.confidence_mean == 0.5
        assert claim_ep.contention == 1.0

    def test_prior_baseline_is_a_measured_belief(self, sdk):
        """A persisted prior (set_point_baseline → ep_alpha/ep_beta) IS
        measurement: has_ep=True and confidence_mean = the prior mean —
        the same number sdk.get_confidence returns pre-EP."""
        claim = sdk.create_point("statement", "A baseline-backed claim")
        claim_id = claim["id"]

        # Strong 18:2 baseline → prior mean 0.9.
        sdk.set_point_baseline(claim_id, 18.0, 2.0)

        proj = sdk._get_proj()
        graph = proj.g

        result = annotate_ep_batch(graph, [claim_id])
        ep = result[claim_id]
        assert ep.has_ep is True, "a persisted prior must count as has_ep"
        assert ep.confidence_mean == pytest.approx(18.0 / 20.0, abs=1e-4)
        # The EP read (TortoiseEP.compute_confidence — the read under
        # sdk.get_confidence) agrees with the search annotation.
        conf = sdk._get_ep().compute_confidence(claim_id)
        assert conf["mean"] == pytest.approx(18.0 / 20.0, abs=1e-9)
        # Zero operator edges → no structural evidence, but belief is 0.9:
        # confidence_mean is a belief, contention/evidence stay structural.
        assert ep.evidence.total == 0
        assert ep.contention == 0.0

    def test_posterior_reads_through_with_zero_edges(self, sdk):
        """#2206 reproduce: a point whose EP posterior converged at 0.88 but
        has NO incoming IMPL edges must read confidence_mean 0.88 (the old
        edge-ratio impl/(impl+nand) read 0.0 for exactly this point)."""
        claim = sdk.create_point("statement", "Converged claim with no IMPL edges")
        claim_id = claim["id"]

        proj = sdk._get_proj()
        graph = proj.g
        # Simulate the EP flush: posterior_alpha/beta persisted, no edges.
        _rows = graph.query(
            "MATCH (n:Point {id: $id}) "
            "SET n.posterior_alpha = 22.0, n.posterior_beta = 3.0",
            params={"id": claim_id},
        ).result_set

        result = annotate_ep_batch(graph, [claim_id])
        ep = result[claim_id]
        assert ep.has_ep is True
        # 22/25 = 0.88 — the converged posterior, NOT the edge ratio 0.0.
        assert ep.confidence_mean == pytest.approx(22.0 / 25.0, abs=1e-4), \
            f"posterior must read through with zero edges, got {ep.confidence_mean}"
        assert ep.evidence.impl_count == 0
        assert ep.evidence.nand_count == 0
        assert ep.evidence.total == 0
        assert ep.contention == 0.0
        # Same number the EP read returns for the point.
        a, b = 22.0, 3.0
        assert ep.confidence_mean == pytest.approx(a / (a + b), abs=1e-4)
