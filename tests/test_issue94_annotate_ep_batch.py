"""Issue #94: annotate_ep_batch distinguishes "no evidence" from "all NANDs".

Tests that isolated points (0 edges) get has_ep=False + confidence_mean=0.5,
while points with NAND edges get has_ep=True + confidence_mean < 0.5.
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
    """EP batch annotation: no-evidence vs all-NAND distinction."""

    def test_isolated_point_has_no_evidence(self, sdk):
        """Point with zero edges → has_ep=False, confidence_mean=0.0 (no EP data).

        Post-#753: has_ep means PERSISTED ep_alpha/ep_beta (set by
        set_point_baseline), not edge presence; an uncalibrated point has
        no posterior → confidence_mean=0.0 and contention 0.0."""
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
        assert ep.confidence_mean == 0.0, \
            f"Isolated point should have confidence_mean=0.0 (no EP data), got {ep.confidence_mean}"
        assert ep.evidence.impl_count == 0
        assert ep.evidence.nand_count == 0
        assert ep.evidence.total == 0
        assert ep.contention == 0.0

    def test_nanded_point_has_ep(self, sdk):
        """Point with NAND edges → has_ep=True, confidence_mean as computed."""
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
        # 0 IMPL, 2 NAND, no persisted baseline → confidence 0.0
        assert ep.confidence_mean == 0.0, \
            f"NANDed point should have confidence_mean=0.0, got {ep.confidence_mean}"
        assert ep.evidence.impl_count == 0
        assert ep.evidence.nand_count == 2
        assert ep.evidence.total == 2
        # contention = 2/2 = 1.0
        assert ep.contention == 1.0, \
            f"All-NAND point should have contention=1.0, got {ep.contention}"

    def test_mixed_edges_has_ep_true(self, sdk):
        """Point with both IMPL and NAND edges → has_ep=True."""
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
        # has_ep = persisted EP data (post-#753); edges alone don't set it.
        assert ep.has_ep is False, \
            f"Point with edges should have has_ep=False without persisted EP, got {ep.has_ep}"
        # 1 IMPL, 1 NAND, no persisted baseline → neutral 0.5
        assert ep.confidence_mean == 0.5, \
            f"Mixed point should have confidence_mean=0.5, got {ep.confidence_mean}"
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

        # Isolated
        iso_ep = result[isolated_id]
        assert iso_ep.has_ep is False
        assert iso_ep.confidence_mean == 0.0  # no EP data (post-#753)

        # NANDed
        claim_ep = result[claim_id]
        assert claim_ep.has_ep is False  # no persisted EP data
        assert claim_ep.confidence_mean == 0.0  # 0 IMPL / 1 total
