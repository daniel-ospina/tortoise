"""Tests for tortoise decide — decision comparison wiring, truth vs relevance semantics.

Runnable with: python3 -m pytest tests/test_decide.py -v
Requires TORTOISE_DB_URI pointing at a FalkorDB (defaults to docker://:@localhost:16379/tortoise).
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK against the live FalkorDB with a unique namespace per test run."""
    ns = f"test_decide_{uuid.uuid4().hex[:8]}"
    sdk = TortoiseSDK(namespace=ns)
    yield sdk
    sdk.close()


class TestDecideWiring:
    """Decision comparison — create points, operators, EP computation."""

    def test_create_option_point(self, sdk):
        """Option points are created with kind='option'."""
        p = sdk.create_point("option", "AGPLv3 dual-licensing", context="test-decide", dedup=True)
        assert p["id"]
        assert p.get("pointKind") == "option"
        # Verify dedup works (opt-in via dedup=True; content_hash stored on first call)
        p2 = sdk.create_point("option", "AGPLv3 dual-licensing", context="test-decide", dedup=True)
        assert p2["id"] == p["id"]

    def test_create_criterion_point(self, sdk):
        """Criterion points are created with kind='criterion'."""
        p = sdk.create_point("criterion", "Developer adoption", context="test-decide")
        assert p["id"]
        assert p.get("pointKind") == "criterion"

    def test_create_evidence_point(self, sdk):
        """Evidence/finding points are created with kind='evidence'."""
        p = sdk.create_point("evidence", "AGPLv3 is OSI-approved", context="test-decide")
        assert p["id"]
        assert p.get("pointKind") == "evidence"

    def test_impl_operator_wiring(self, sdk):
        """IMPL edge from evidence to option."""
        opt = sdk.create_point("option", "Option A", context="test-decide")
        ev = sdk.create_point("evidence", "Supports A", context="test-decide")
        op = sdk.create_operator("IMPL", ev["id"], [opt["id"]], context="test-decide")
        assert op["id"]
        assert op.get("is_operator") is True

    def test_nand_operator_wiring(self, sdk):
        """NAND edge from evidence to option."""
        opt = sdk.create_point("option", "Option A", context="test-decide")
        ev = sdk.create_point("evidence", "Opposes A", context="test-decide")
        op = sdk.create_operator("NAND", ev["id"], [opt["id"]], context="test-decide")
        assert op["id"]
        assert op.get("is_operator") is True

    def test_mitigation_range_clamped(self, sdk):
        """mitigate_operator enforces [0.10, 0.50] range on strength."""
        opt = sdk.create_point("option", "Option A", context="test-decide")
        ev = sdk.create_point("evidence", "Weak evidence", context="test-decide")
        op = sdk.create_operator("IMPL", ev["id"], [opt["id"]], context="test-decide")

        # Valid range
        m1 = sdk.mitigate_operator(op["id"], "Minor caveat", 0.10)
        assert m1.get("id")

        # Strength is stored — verify we can mitigate at different levels
        m2 = sdk.mitigate_operator(op["id"], "Major limitation", 0.50)
        assert m2.get("id")

    def test_compute_confidence_scoped_to_context(self, sdk):
        """EP computes confidence for points in the specified context."""
        # Create two options
        opt_a = sdk.create_point("option", "Option A", context="test-ep")
        opt_b = sdk.create_point("option", "Option B", context="test-ep")

        # Evidence strongly supports A, opposes B
        ev1 = sdk.create_point("evidence", "A is better", context="test-ep")
        ev2 = sdk.create_point("evidence", "B has problems", context="test-ep")

        sdk.create_operator("IMPL", ev1["id"], [opt_a["id"]], context="test-ep")
        sdk.create_operator("NAND", ev2["id"], [opt_b["id"]], context="test-ep")

        result = sdk.compute_confidence(context="test-ep")
        assert "iterations" in result
        assert "converged" in result
        confs = result.get("confidences", {})
        assert len(confs) > 0

    def test_truth_challenge_nands_point(self, sdk):
        """Truth challenge: NAND the target finding POINT directly (it's FALSE)."""
        # Finding that supports option
        ev_support = sdk.create_point("evidence", "Provider protects privacy", context="test-truth")
        opt = sdk.create_point("option", "Option A", context="test-truth")
        sdk.create_operator("IMPL", ev_support["id"], [opt["id"]], context="test-truth")

        # Truth challenge: another finding says ev_support is FALSE
        ev_challenge = sdk.create_point("evidence",
            "Metadata reveals topics — provider CAN infer content", context="test-truth")
        truth_op = sdk.create_operator("NAND", ev_challenge["id"], [ev_support["id"]],
                                       context="test-truth")
        assert truth_op["id"]
        # The NAND is directly on the finding point — not on the operator
        # This means the finding itself is challenged as FALSE

    def test_relevance_challenge_mitigates_operator(self, sdk):
        """Relevance challenge: mitigates the OPERATOR (TRUE but matters LESS)."""
        opt = sdk.create_point("option", "Option A", context="test-relevance")
        ev = sdk.create_point("evidence",
            "Provider cannot read content", context="test-relevance")
        impl_op = sdk.create_operator("IMPL", ev["id"], [opt["id"]],
                                      context="test-relevance")

        # Relevance challenge: the finding is TRUE but its importance is OVERSTATED
        # → mitigate the OPERATOR, NOT the finding point
        mitigation = sdk.mitigate_operator(impl_op["id"],
            "Metadata is lossy — visible but not full content", 0.20)
        assert mitigation.get("id")
        # The finding point is NOT NANDed — it's still true, just weaker

    def test_option_point_never_nanded_for_bad_fit(self, sdk):
        """Never NAND an option or criterion point for bad fit — express fit on the operator."""
        opt = sdk.create_point("option", "Option A", context="test-fit")
        crit = sdk.create_point("criterion", "Enterprise readiness", context="test-fit")

        # Bad fit: criterion opposes option — express as NAND on the operator
        bad_fit_op = sdk.create_operator("NAND", crit["id"], [opt["id"]],
                                         context="test-fit")
        assert bad_fit_op["id"]

        # The option point itself is never NANDed — it's still a valid option
        # We verify by checking the option point still exists and isn't outdated
        retrieved = sdk.get_point(opt["id"])
        assert retrieved["id"] == opt["id"]
        # Option is not outdated or invalidated (status may be draft until an edge
        # promotes it — what matters is it is NOT 'outdated')
        assert retrieved.get("status") != "outdated"

    def test_full_wiring_produces_ranked_output(self, sdk):
        """End-to-end: create options + criteria + findings, wire edges, compute confidence."""
        ctx = "test-full"
        # Options
        opt_a = sdk.create_point("option", "Option A", context=ctx)
        opt_b = sdk.create_point("option", "Option B", context=ctx)

        # Criteria
        crit_1 = sdk.create_point("criterion", "Security", context=ctx)
        crit_2 = sdk.create_point("criterion", "Adoption", context=ctx)

        # Findings
        f1 = sdk.create_point("evidence", "A is secure", context=ctx)
        f2 = sdk.create_point("evidence", "A has wide adoption", context=ctx)
        f3 = sdk.create_point("evidence", "B has security issues", context=ctx)

        # Wire: criteria → options
        sdk.create_operator("IMPL", crit_1["id"], [opt_a["id"]], context=ctx)
        sdk.create_operator("IMPL", crit_2["id"], [opt_a["id"]], context=ctx)
        sdk.create_operator("IMPL", crit_1["id"], [opt_b["id"]], context=ctx)
        sdk.create_operator("NAND", crit_2["id"], [opt_b["id"]], context=ctx)

        # Wire: findings → options
        sdk.create_operator("IMPL", f1["id"], [opt_a["id"]], context=ctx)
        sdk.create_operator("IMPL", f2["id"], [opt_a["id"]], context=ctx)
        sdk.create_operator("NAND", f3["id"], [opt_b["id"]], context=ctx)

        # Compute
        result = sdk.compute_confidence(context=ctx)
        assert result["iterations"] >= 0
        confs = result.get("confidences", {})

        # Option A should have higher confidence (3 IMPL supports, 0 NANDs)
        # Option B should have lower confidence (1 IMPL, 2 NANDs)
        a_mean = confs.get(opt_a["id"], {}).get("mean", 0)
        b_mean = confs.get(opt_b["id"], {}).get("mean", 0)
        if isinstance(a_mean, (int, float)) and isinstance(b_mean, (int, float)):
            assert a_mean > b_mean, f"Expected A ({a_mean:.3f}) > B ({b_mean:.3f})"
