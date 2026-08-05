"""Tests for supersede_point edge cleanup — Issue #95.

Verifies that supersede_point transfers IMPL/NAND/hasPart edges from the old
point to the new point, preventing EP from propagating through superseded claims.

Runnable with:
  TORTOISE_DB_URI=docker://:@localhost:16379/tortoise_test_sup95 python3 -m pytest tests/test_supersede_edges.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK with temp database. Closed after test."""
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_supersede_test_"), "test.db"
    )
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


def _make_point(sdk: TortoiseSDK, **kw):
    return sdk.create_point(
        kw.pop("kind", "statement"), kw.pop("content", "test content"), **kw
    )


# ── IMPL edge transfer (target) ────────────────────────────────────

def test_supersede_transfers_impl_to_target(sdk):
    """IMPL evidence→claim_A: supersede A→B should move IMPL edge to B."""
    claim_a = _make_point(sdk, content="Claim A: original version")
    evidence = _make_point(sdk, kind="evidence", content="Evidence for claim")
    sdk.create_operator("IMPL", evidence["id"], [claim_a["id"]])

    claim_b = _make_point(sdk, content="Claim B: revised version")

    result = sdk.supersede_point(claim_a["id"], claim_b["id"])

    # Return value
    assert result["invalidated"] is True
    assert result["id"] == claim_a["id"]
    assert result["corrected_by"] == claim_b["id"]
    assert result["edges_transferred"] >= 1

    # A is outdated, B is not
    assert sdk.get_point(claim_a["id"])["outdated"] is True
    assert not sdk.get_point(claim_b["id"]).get("outdated")

    # IMPL edge moved to B
    incoming_b = sdk.traverse(claim_b["id"], "IMPL", direction="incoming")
    assert len(incoming_b) >= 1, "IMPL edge should be transferred to B"

    # A has no live IMPL edges
    incoming_a = sdk.traverse(claim_a["id"], "IMPL", direction="incoming")
    assert len(incoming_a) == 0, (
        f"A should have no incoming IMPL after supersede, got {incoming_a}"
    )


# ── NAND edge transfer (target) ────────────────────────────────────

def test_supersede_transfers_nand_to_target(sdk):
    """NAND evidence→claim_A: supersede A→B should move NAND edge to B."""
    claim_a = _make_point(sdk, content="Claim A: disputed")
    evidence = _make_point(sdk, kind="evidence", content="Contradicting evidence")
    sdk.create_operator("NAND", evidence["id"], [claim_a["id"]])

    claim_b = _make_point(sdk, content="Claim B: revised")

    result = sdk.supersede_point(claim_a["id"], claim_b["id"])
    assert result["edges_transferred"] >= 1

    # NAND edge moved to B
    incoming_b = sdk.traverse(claim_b["id"], "NAND", direction="incoming")
    assert len(incoming_b) >= 1, "NAND edge should be transferred to B"

    # A has no live NAND edges
    incoming_a = sdk.traverse(claim_a["id"], "NAND", direction="incoming")
    assert len(incoming_a) == 0, (
        f"A should have no incoming NAND after supersede, got {incoming_a}"
    )


# ── Source edge transfer ───────────────────────────────────────────

def test_supersede_transfers_source_edge(sdk):
    """IMPL claim_A→target: supersede A→B should move source role to B."""
    claim_a = _make_point(sdk, content="Claim A (source)")
    target = _make_point(sdk, content="Target claim")
    sdk.create_operator("IMPL", claim_a["id"], [target["id"]])

    claim_b = _make_point(sdk, content="Claim B (supersedes A)")

    result = sdk.supersede_point(claim_a["id"], claim_b["id"])
    assert result["edges_transferred"] >= 1

    # B now has incoming IMPL (as source of the operator)
    incoming_b = sdk.traverse(claim_b["id"], "IMPL", direction="incoming")
    assert len(incoming_b) >= 1, "B should receive the source IMPL edge"

    # A has no incoming IMPL
    incoming_a = sdk.traverse(claim_a["id"], "IMPL", direction="incoming")
    assert len(incoming_a) == 0

    # The target still has its IMPL edge (operator→target not affected)
    incoming_target = sdk.traverse(target["id"], "IMPL", direction="incoming")
    assert len(incoming_target) >= 1, "Target should still have its IMPL edge"


# ── Multiple edges ─────────────────────────────────────────────────

def test_supersede_transfers_multiple_edges(sdk):
    """Multiple IMPL edges into A: all should transfer to B."""
    claim_a = _make_point(sdk, content="Claim A")
    evidence_1 = _make_point(sdk, kind="evidence", content="Evidence 1")
    evidence_2 = _make_point(sdk, kind="evidence", content="Evidence 2")
    sdk.create_operator("IMPL", evidence_1["id"], [claim_a["id"]])
    sdk.create_operator("IMPL", evidence_2["id"], [claim_a["id"]])

    claim_b = _make_point(sdk, content="Claim B")

    result = sdk.supersede_point(claim_a["id"], claim_b["id"])
    assert result["edges_transferred"] == 2

    incoming_b = sdk.traverse(claim_b["id"], "IMPL", direction="incoming")
    assert len(incoming_b) == 2
    incoming_a = sdk.traverse(claim_a["id"], "IMPL", direction="incoming")
    assert len(incoming_a) == 0


# ── Mixed edges (IMPL + NAND) ──────────────────────────────────────

def test_supersede_transfers_mixed_edges(sdk):
    """IMPL and NAND into A: both transfer to B."""
    claim_a = _make_point(sdk, content="Claim A")
    evidence_support = _make_point(sdk, kind="evidence", content="Supporting")
    evidence_oppose = _make_point(sdk, kind="evidence", content="Opposing")
    sdk.create_operator("IMPL", evidence_support["id"], [claim_a["id"]])
    sdk.create_operator("NAND", evidence_oppose["id"], [claim_a["id"]])

    claim_b = _make_point(sdk, content="Claim B")

    result = sdk.supersede_point(claim_a["id"], claim_b["id"])
    assert result["edges_transferred"] == 2

    impl_to_b = sdk.traverse(claim_b["id"], "IMPL", direction="incoming")
    nand_to_b = sdk.traverse(claim_b["id"], "NAND", direction="incoming")
    assert len(impl_to_b) == 1
    assert len(nand_to_b) == 1

    # A has zero of either
    assert len(sdk.traverse(claim_a["id"], "IMPL", direction="incoming")) == 0
    assert len(sdk.traverse(claim_a["id"], "NAND", direction="incoming")) == 0


# ── No edges to transfer ───────────────────────────────────────────

def test_supersede_no_edges_transfers_zero(sdk):
    """Superseding a point with no operator edges reports 0 transferred."""
    claim_a = _make_point(sdk, content="Claim A (no edges)")
    claim_b = _make_point(sdk, content="Claim B")

    result = sdk.supersede_point(claim_a["id"], claim_b["id"])
    assert result["invalidated"] is True
    assert result["edges_transferred"] == 0


# ── Idempotency guard: double supersede ────────────────────────────

def test_supersede_then_supersede_again_transfers_zero(sdk):
    """Superseding an already-superseded point has no edges left to transfer."""
    claim_a = _make_point(sdk, content="Claim A")
    evidence = _make_point(sdk, kind="evidence", content="Evidence")
    sdk.create_operator("IMPL", evidence["id"], [claim_a["id"]])

    claim_b = _make_point(sdk, content="Claim B")
    claim_c = _make_point(sdk, content="Claim C")

    # First supersede: A → B
    r1 = sdk.supersede_point(claim_a["id"], claim_b["id"])
    assert r1["edges_transferred"] == 1

    # Second supersede: A → C (A already outdated, edges already transferred)
    r2 = sdk.supersede_point(claim_a["id"], claim_c["id"])
    # A has no operator edges left — nothing to transfer
    assert r2["edges_transferred"] == 0
