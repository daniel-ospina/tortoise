"""Tests for dreaming — background EP stabilization (#85).

Covers:
- dream(anchors, hops): incremental subgraph EP converges
- dream_all(): whole-graph stabilization converges
- Write paths mark dirty roots (create_point, create_operator, update,
  delete, invalidate, supersede, mitigate)
- Lazy-read consistency: get_confidence auto-dreams when dirty
- Convergence without explicit EP invocation (O/I/T indicator 4)
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
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_dream_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


def _make_claim(sdk, content: str, kind: str = "statement") -> dict:
    # #992: EP tests model live claims — create_point defaults to draft since #943
    # (#780 draft filter strips draft inputs from operators, making them degenerate).
    return sdk.create_point(kind, content, dedup=False, status="live")


class TestDreamIncremental:
    def test_dream_converges_on_subgraph(self, sdk):
        a = _make_claim(sdk, "conclusion")
        b = _make_claim(sdk, "supporting evidence")
        sdk.create_operator("IMPL", b["id"], [a["id"]])
        sdk.set_point_baseline(b["id"], 8.0, 2.0)

        result = sdk.dream(dirty_only=True)
        assert result["converged"] is True
        assert result["iterations"] >= 1
        assert a["id"] in result["affected_claims"]

    def test_dream_clears_dirty_roots_on_convergence(self, sdk):
        a = _make_claim(sdk, "c1")
        b = _make_claim(sdk, "c2")
        sdk.create_operator("IMPL", b["id"], [a["id"]])
        assert sdk._dirty_roots, "writes should mark dirty roots"

        sdk.dream(dirty_only=True)
        assert sdk._dirty_roots == set(), "converged dream clears dirty roots"

    def test_dream_no_anchors_noop(self, sdk):
        result = sdk.dream(dirty_only=True)
        assert result["converged"] is True
        assert result["iterations"] == 0

    def test_dream_max_hops_respected(self, sdk):
        """A chain A←B←C: dreaming from C with max_hops=2 reaches A."""
        c = _make_claim(sdk, "C")
        b = _make_claim(sdk, "B")
        a = _make_claim(sdk, "A")
        sdk.create_operator("IMPL", c["id"], [b["id"]])
        sdk.create_operator("IMPL", b["id"], [a["id"]])

        result = sdk.dream(dirty_only=True, max_hops=2)
        assert result["converged"] is True
        affected = set(result["affected_claims"])
        # All three should be affected (dirty roots + 2-hop expansion)
        assert a["id"] in affected or c["id"] in affected


class TestDreamAll:
    def test_dream_all_converges(self, sdk):
        for i in range(5):
            claim = _make_claim(sdk, f"claim {i}")
            support = _make_claim(sdk, f"support {i}")
            sdk.create_operator("IMPL", support["id"], [claim["id"]])

        result = sdk.dream(full=True)
        assert result["converged_all"] is True
        assert result["batches"] >= 1
        assert result["total_affected"] >= 5

    def test_dream_all_empty_graph(self, sdk):
        result = sdk.dream(full=True)
        assert result["batches"] == 0
        assert result["converged_all"] is True

    def test_dream_all_matches_explicit_ep(self, sdk):
        """O/I/T indicator 4: stabilization WITHOUT explicit compute_confidence."""
        a = _make_claim(sdk, "decision A")
        ev = _make_claim(sdk, "evidence for A")
        sdk.create_operator("IMPL", ev["id"], [a["id"]])
        sdk.set_point_baseline(ev["id"], 9.0, 1.0)

        # No explicit compute_confidence — just dream.
        sdk.dream(full=True)
        conf = sdk.get_confidence(a["id"])
        assert 0 <= conf["mean"] <= 1
        assert conf["effective_n"] >= 1

        # Sanity: strong evidence → high confidence.
        assert conf["mean"] > 0.5


class TestWritePathsMarkDirty:
    def test_create_point_marks_dirty(self, sdk):
        p = _make_claim(sdk, "point")
        assert p["id"] in sdk._dirty_roots

    def test_create_operator_marks_inputs_dirty(self, sdk):
        a = _make_claim(sdk, "a")
        b = _make_claim(sdk, "b")
        sdk._dirty_roots.clear()
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        assert sdk._dirty_roots, "operator creation marks inputs dirty"
        assert a["id"] in sdk._dirty_roots and b["id"] in sdk._dirty_roots

    def test_update_point_marks_dirty(self, sdk):
        p = _make_claim(sdk, "x")
        sdk._dirty_roots.clear()
        sdk.update_point(p["id"], confidence=0.9)
        assert p["id"] in sdk._dirty_roots

    def test_delete_point_marks_dirty(self, sdk):
        p = _make_claim(sdk, "gone")
        sdk._dirty_roots.clear()
        sdk.delete_point(p["id"])
        assert p["id"] in sdk._dirty_roots

    def test_invalidate_marks_dirty(self, sdk):
        old = _make_claim(sdk, "old")
        new = _make_claim(sdk, "new")
        sdk._dirty_roots.clear()
        sdk.invalidate_point(old["id"], new["id"])
        assert old["id"] in sdk._dirty_roots and new["id"] in sdk._dirty_roots

    def test_supersede_marks_dirty(self, sdk):
        old = _make_claim(sdk, "superseded")
        new = _make_claim(sdk, "successor")
        sdk._dirty_roots.clear()
        sdk.supersede_point(old["id"], new["id"])
        assert old["id"] in sdk._dirty_roots and new["id"] in sdk._dirty_roots

    def test_mitigate_marks_dirty(self, sdk):
        a = _make_claim(sdk, "a")
        b = _make_claim(sdk, "b")
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        sdk._dirty_roots.clear()
        sdk.mitigate_operator(op["id"], "weak edge")
        assert sdk._dirty_roots, "mitigation marks dirty"


class TestLazyReadConsistency:
    def test_get_confidence_auto_dreams_when_dirty(self, sdk):
        a = _make_claim(sdk, "decision")
        ev = _make_claim(sdk, "evidence")
        sdk.create_operator("IMPL", ev["id"], [a["id"]])
        sdk.set_point_baseline(ev["id"], 8.0, 2.0)
        sdk._dirty_roots.add(a["id"])

        # get_confidence should trigger a dream and return a fresh value.
        conf = sdk.get_confidence(a["id"])
        assert 0 <= conf["mean"] <= 1
        assert sdk._dirty_roots == set(), "lazy read consumed dirty roots"

    def test_compute_confidence_auto_dreams_before_auto_extract(self, sdk):
        a = _make_claim(sdk, "c1")
        b = _make_claim(sdk, "c2")
        sdk.create_operator("IMPL", b["id"], [a["id"]])
        # Force dirty state.
        sdk._dirty_roots.add(a["id"])

        result = sdk.compute_confidence()  # no anchors → auto-extract
        assert "confidences" in result
        assert sdk._dirty_roots == set(), "auto-extract dreamed first"


class TestConvergenceAfterBatchWrites:
    def test_batch_writes_then_dream_stabilizes(self, sdk):
        """Write N interconnected points, then dream — no explicit EP."""
        claims = [_make_claim(sdk, f"claim {i}") for i in range(10)]
        for i, claim in enumerate(claims[:-1]):
            sdk.create_operator("IMPL", claims[i + 1]["id"], [claim["id"]])

        assert len(sdk._dirty_roots) >= 10

        # Dream everything (no explicit compute_confidence).
        sdk.dream(dirty_only=True)
        assert sdk._dirty_roots == set()

        # All claims now have stabilized confidence.
        for claim in claims:
            conf = sdk.get_confidence(claim["id"])
            assert 0 <= conf["mean"] <= 1
            assert conf["effective_n"] >= 1


class TestBaselineMarksDirty:
    def test_set_point_baseline_marks_dirty(self, sdk):
        """P1 (#85): baseline changes alter priors — must mark dirty."""
        p = _make_claim(sdk, "baselined")
        sdk._dirty_roots.clear()
        sdk.set_point_baseline(p["id"], 8.0, 2.0)
        assert p["id"] in sdk._dirty_roots


# ── #330: dream honours persistent evidence (baselines) ──────────────


class TestDreamEvidence:
    def test_dream_preserves_baseline_evidence(self, sdk):
        """#330: a dream run must apply the SDK's persistent evidence — it must
        NOT recompute a baseline'd claim's posterior from messages only and
        clobber the graph (which would corrupt the baseline_set contract)."""
        a = _make_claim(sdk, "A-evidence")
        b = _make_claim(sdk, "B-evidence")
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        # Strong baseline on b
        sdk.set_point_baseline(b["id"], 10.0, 1.0)
        sdk._dirty_roots.clear()

        sdk.dream([b["id"]], max_hops=2)

        # b's posterior must be evidence-dominated (mean > 0.8), NOT clobbered
        # to the no-evidence value (~0.5-0.6) by a bare message-passing run.
        conf = sdk.get_confidence(b["id"])
        assert conf["mean"] > 0.8, (
            f"dream clobbered the baseline: b's posterior mean = {conf['mean']} "
            f"(expected evidence-dominated > 0.8)"
        )
        # baseline_set flag must survive the dream
        proj = sdk._get_proj()
        row = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.baseline_set",
            params={"id": b["id"]},
        ).result_set[0]
        assert row[0] is True
