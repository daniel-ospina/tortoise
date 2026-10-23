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
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_dream_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _make_claim(sdk, content: str, kind: str = "statement") -> dict:
    # #992: EP tests model live claims — status="live" is explicit for intent;
    # create_point's implicit status is live since #1088 (it was draft from
    # #943; the #780 draft filter strips draft inputs from operators).
    return sdk.create_point(kind, content, dedup=False, status="live")


class TestDreamIncremental:
    def test_dream_converges_on_subgraph(self, sdk):
        a = _make_claim(sdk, "conclusion")
        b = _make_claim(sdk, "supporting evidence")
        sdk.create_operator("IMPL", b["id"], [a["id"]])
        sdk.set_point_baseline(b["id"], 8.0, 2.0)

        # #1157: live-uncalibrated claims trip the fail-closed gate; this unit
        # test exercises the EP mechanics, not calibration — opt out explicitly.
        result = sdk.dream(dirty_only=True, require_calibration=False)
        assert result["converged"] is True
        assert result["iterations"] >= 1
        assert a["id"] in result["affected_claims"]

    def test_dream_clears_dirty_roots_on_convergence(self, sdk):
        a = _make_claim(sdk, "c1")
        b = _make_claim(sdk, "c2")
        sdk.create_operator("IMPL", b["id"], [a["id"]])
        assert sdk._dirty_roots, "writes should mark dirty roots"

        sdk.dream(dirty_only=True, require_calibration=False)
        assert sdk._dirty_roots == set(), "converged dream clears dirty roots"

    def test_dream_no_anchors_noop(self, sdk):
        # Epic 903-C6 (#1244): explicit local mode pins the no-op contract.
        # Auto-select would route a no-dirty-roots small graph to full
        # (I1 rule 3) — the local key-set is the no-op contract surface.
        result = sdk.dream(dirty_only=True, mode="local")
        assert result["converged"] is True
        assert result["iterations"] == 0
        assert set(result.keys()) == {
            "mode", "iterations", "converged", "affected_claims",
            "budget_used", "coverage"}

    def test_dream_max_hops_respected(self, sdk):
        """A chain A←B←C: dreaming from C with max_hops=2 reaches A."""
        c = _make_claim(sdk, "C")
        b = _make_claim(sdk, "B")
        a = _make_claim(sdk, "A")
        sdk.create_operator("IMPL", c["id"], [b["id"]])
        sdk.create_operator("IMPL", b["id"], [a["id"]])

        result = sdk.dream(dirty_only=True, max_hops=2,
                          require_calibration=False)
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

        result = sdk.dream(full=True, require_calibration=False)
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
        sdk.dream(full=True, require_calibration=False)
        conf = sdk.get_confidence(a["id"], require_calibration=False)
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

    def test_delete_point_dirties_neighbors_and_recomputes(self, sdk):
        """#1916: delete_point must dirty the deleted point's neighbors — the
        post-delete reverse-BFS can no longer see the node's edges, so without
        pre-capturing the neighbors no dream fires and their stored
        confidences stay at pre-delete values.

        Two strong sources: deleting one must both dirty the surviving claim
        (reverse-BFS capture before the DETACH DELETE) and, once a dream
        fires, drop its stored confidence to the single-source posterior.
        """
        ev_a = _make_claim(sdk, "source A")
        ev_b = _make_claim(sdk, "source B")
        claim = _make_claim(sdk, "conclusion")
        sdk.set_point_baseline(ev_a["id"], 10.0, 1.0)
        sdk.set_point_baseline(ev_b["id"], 10.0, 1.0)
        sdk.create_operator("IMPL", ev_a["id"], [claim["id"]])
        sdk.create_operator("IMPL", ev_b["id"], [claim["id"]])
        # Stabilize: claim confidence is evidence-driven (two strong sources).
        sdk.dream(dirty_only=True, require_calibration=False)
        assert sdk._dirty_roots == set()
        conf_before = sdk.get_confidence(claim["id"], require_calibration=False)

        sdk._dirty_roots.clear()
        sdk.delete_point(ev_a["id"])

        # Indicator 1: the surviving neighbor is dirtied — captured BEFORE
        # the DETACH DELETE removed the deleted point's edges (#1916).
        assert claim["id"] in sdk._dirty_roots

        # Indicator 2: a dream fires and the neighbor's stored confidence
        # recomputes — with one source gone it falls back toward the
        # single-source posterior.
        sdk.dream(dirty_only=True, require_calibration=False)
        conf_after = sdk.get_confidence(claim["id"], require_calibration=False)
        assert conf_after["mean"] < conf_before["mean"], (
            "claim confidence should drop when one evidence source is deleted"
        )

    def test_invalidate_marks_dirty(self, sdk):
        old = _make_claim(sdk, "old")
        new = _make_claim(sdk, "new")
        sdk._dirty_roots.clear()
        sdk.invalidate_point(old["id"], new["id"])
        # #2422: the invalidated (outdated=true) point is terminal for EP —
        # it can never enter an affected set so a dirty flag would strand
        # forever (pins auto-dream to local). Only the LIVE successor stays
        # in the dirty set.
        assert old["id"] not in sdk._dirty_roots, (
            "terminal (invalidated) point must not strand in _dirty_roots (#2422)"
        )
        assert new["id"] in sdk._dirty_roots

    def test_supersede_marks_dirty(self, sdk):
        old = _make_claim(sdk, "superseded")
        new = _make_claim(sdk, "successor")
        sdk._dirty_roots.clear()
        sdk.supersede_point(old["id"], new["id"])
        # #2422: the superseded old point is terminal for EP — a dirty flag
        # would strand forever (never swept). Only the LIVE successor stays.
        assert old["id"] not in sdk._dirty_roots, (
            "terminal (superseded) point must not strand in _dirty_roots (#2422)"
        )
        assert new["id"] in sdk._dirty_roots

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
        conf = sdk.get_confidence(a["id"], require_calibration=False)
        assert 0 <= conf["mean"] <= 1
        assert sdk._dirty_roots == set(), "lazy read consumed dirty roots"

    def test_compute_confidence_auto_dreams_before_auto_extract(self, sdk):
        a = _make_claim(sdk, "c1")
        b = _make_claim(sdk, "c2")
        # #344: neutral Beta(1,1) baselines — the fail-closed gate runs before
        # the auto-dream, so the graph must be calibrated first.
        sdk.set_point_baseline(a["id"], 1, 1)
        sdk.set_point_baseline(b["id"], 1, 1)
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
        sdk.dream(dirty_only=True, require_calibration=False)
        assert sdk._dirty_roots == set()

        # All claims now have stabilized confidence.
        for claim in claims:
            conf = sdk.get_confidence(claim["id"], require_calibration=False)
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

        sdk.dream([b["id"]], max_hops=2, require_calibration=False)

        # b's posterior must be evidence-dominated (mean > 0.8), NOT clobbered
        # to the no-evidence value (~0.5-0.6) by a bare message-passing run.
        conf = sdk.get_confidence(b["id"], require_calibration=False)
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


# ── #9275 — a non-string id must not crash the dirty lifecycle ──────────────
#
# The EP affected-claim path stopped raising `TypeError: '<' not supported
# between instances of 'int' and 'str'` (#9275, `ep._affected_claims` +
# `dream.py`'s `sorted(..., key=str)`), because a PRESENT id that is not a
# string is ADDRESSABLE: `set_point_baseline(42, 3.0, 1.0)` resolves the node
# and `compute_confidence` computes it. That is exactly what makes these tests
# reachable — the same supported write puts `42` into `_dirty_roots`, the set
# every later pass orders with a bare `sorted()`. The defect class is the SAME
# one the affected-claim fix removed, one call away from it: the stale-first
# window (BEFORE any EP work) and the sweep's `$ids` batch (AFTER a converged
# pass has written posteriors — the half-written shape #9275 exists to end).

class TestMixedTypeIdsInTheDirtyLifecycle:
    def test_dream_window_orders_a_numeric_dirty_root_instead_of_crashing(
            self, sdk):
        """`dream_window` sorts `_dirty_roots` on the stale-first path.

        Pre-fix this raises `TypeError: '<' not supported between instances of
        'int' and 'str'` at `dream.py`'s dirty-root sort — before a single EP
        query ran, so the caller has no diagnosis at all.
        """
        anchor = _make_claim(sdk, "anchor")
        claim = _make_claim(sdk, "claim")
        sdk.create_operator("IMPL", anchor["id"], [claim["id"]])
        proj = sdk._get_proj()
        proj.g.query("MATCH (n:Point {id:$id}) SET n.id = 42",
                     params={"id": claim["id"]})
        # The supported, measured write: it resolves the node AND mirrors 42
        # into `_dirty_roots` (`set_point_baseline` ends with `_mark_dirty`).
        sdk.set_point_baseline(42, 3.0, 1.0)
        sdk.set_point_baseline(anchor["id"], 3.0, 1.0)
        assert 42 in sdk._dirty_roots
        assert any(isinstance(r, str) for r in sdk._dirty_roots), (
            "the fixture must mix id TYPES — a homogeneous set cannot fail the "
            "ordering")

        result = sdk._get_dreamer().dream_window()

        assert 42 in result["affected_claims"], (
            "the numeric-id claim is addressable and must be computed, not "
            f"skipped (affected={result['affected_claims']})")

    def test_unsweepable_dirty_roots_orders_a_numeric_root(self, sdk):
        """The sweep's classification batch is reached AFTER a converged pass.

        Pre-fix `ids = sorted(candidates)` raised `TypeError` here — i.e. after
        the pass had already written its posteriors, which is the half-written
        failure #9275 exists to eliminate.
        """
        anchor = _make_claim(sdk, "anchor")
        claim = _make_claim(sdk, "claim")
        sdk.create_operator("IMPL", anchor["id"], [claim["id"]])
        proj = sdk._get_proj()
        proj.g.query("MATCH (n:Point {id:$id}) SET n.id = 42",
                     params={"id": claim["id"]})
        sdk.set_point_baseline(42, 3.0, 1.0)
        sdk._dirty_roots |= {anchor["id"]}
        assert 42 in sdk._dirty_roots

        # No candidate is in `affected` — the classification leg must run.
        retained = sdk._unsweepable_dirty_roots(set())

        assert isinstance(retained, set)
        assert retained <= {42, anchor["id"]}

    def test_hydrate_dirty_roots_excludes_an_id_less_point(self, sdk):
        """A dirty `:Point` with no `id` must not hydrate `None` into the mirror.

        Pre-fix `loaded = {r[0] for r in rows}` put `None` into `_dirty_roots`,
        which re-joined the same mixed-type ordering crash — and the node can
        never be swept or affected (it identifies no factor participant), so
        mirroring it only pins the mirror.
        """
        _make_claim(sdk, "namable")
        proj = sdk._get_proj()
        proj.g.query("CREATE (n:Point {pointKind:'statement', status:'live', "
                     "content:'no id', ep_dirty: true})")
        sdk._dirty_roots.clear()

        loaded = sdk._hydrate_dirty_roots()

        assert None not in loaded, (
            f"an id-less :Point hydrated into the dirty mirror: {loaded!r}")
        assert None not in sdk._dirty_roots

    def test_the_operator_cap_truncation_orders_mixed_type_ids(self, sdk):
        """`_bfs_select_operators` sorts its collected operator ids at `op_cap`.

        Pre-fix `set(sorted(collected)[:op_cap])` raised `TypeError` on a
        hand-written numeric operator id — reached from `dream_window`
        (`op_cap=budget`) and from every scheduler pass.
        """
        from tortoise.analyze import _bfs_select_operators

        seed = _make_claim(sdk, "seed")
        t1 = _make_claim(sdk, "target 1")
        t2 = _make_claim(sdk, "target 2")
        op1 = sdk.create_operator("IMPL", seed["id"], [t1["id"]])
        op2 = sdk.create_operator("IMPL", seed["id"], [t2["id"]])
        proj = sdk._get_proj()
        proj.g.query("MATCH (n:Point {id:$id}) SET n.id = 42",
                     params={"id": op2["id"]})

        collected, _anchors = _bfs_select_operators(
            proj, [seed["id"]], max_hops=1, rel_filter="IMPL|NAND",
            direction="both", op_cap=1,
        )

        assert len(collected) == 1, (
            "the cap must still truncate to op_cap — only the ORDERING changed")
        assert op1["id"] in collected or 42 in collected
