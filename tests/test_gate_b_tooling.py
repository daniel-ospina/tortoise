"""Gate B tooling tests (epic-264 #779): mean_grounding + drift snapshot +
calibration milestone marker.

Covers the DE2E-4 grounding snapshot contract (mean over ``confidence`` of
live non-operator Points, #780 live-only semantics) and the DE2E-7 local
``calibration_passed()`` marker contract.
"""
from __future__ import annotations  # noqa: I001

import pytest

from tortoise.analyze import (
    MAX_GROUNDING_DRIFT,
    MAX_POINT_DRIFT,
    mean_grounding,
    grounding_snapshot,
    grounding_drift,
)
from tortoise.sdk import TortoiseSDK


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def make_point(sdk: TortoiseSDK, content: str, *, confidence=None,
               status="live", **props):
    """Create a Point with explicit confidence/status (create_point defaults
    to status='draft' — Gate B measures LIVE Points only)."""
    if confidence is not None:
        props["confidence"] = confidence
    props["status"] = status
    return sdk.create_point("statement", content, **props)


# ── mean_grounding: DE2E-4 formula ────────────────────────────────

def test_mean_grounding_empty_graph_zero(sdk):
    assert mean_grounding(sdk._get_proj()) == 0.0


def test_mean_grounding_live_non_operator_only(sdk):
    proj = sdk._get_proj()
    # Live Points with explicit confidence → mean is over these three.
    make_point(sdk, "a", confidence=0.4)
    make_point(sdk, "b", confidence=0.6)
    make_point(sdk, "c", confidence=0.8)
    # Draft Point excluded even with extreme confidence.
    make_point(sdk, "draft poison", confidence=0.0, status="draft")
    # Operator Point excluded (is_operator: true) regardless of confidence.
    s = make_point(sdk, "source", confidence=0.5)
    t = make_point(sdk, "target", confidence=0.5)
    sdk.create_operator("IMPL", s["id"], [t["id"]])
    # Legacy NULL-status Point is LIVE (#780 semantics) and counts.
    legacy = make_point(sdk, "legacy", confidence=0.7)
    proj.g.query("MATCH (n:Point {id:$id}) REMOVE n.status",
                 params={"id": legacy["id"]})
    # NULL-confidence live Point defaults to 0.5 (repo convention).
    make_point(sdk, "no conf")

    # Counted: a, b, c, legacy, no-conf, plus the IMPL source/target (live
    # non-operator Points — operators are excluded, endpoints are not).
    expected = (0.4 + 0.6 + 0.8 + 0.7 + 0.5 + 0.5 + 0.5) / 7
    assert mean_grounding(proj) == pytest.approx(expected)
    # Operator exclusion is structural: assert the op node exists in the
    # graph as a Point yet is not counted (count stays 7, not 8).
    snap = grounding_snapshot(proj)
    assert snap["count"] == 7
    assert snap["mean"] == pytest.approx(expected)


def test_mean_grounding_snapshot_matches_mean(sdk):
    proj = sdk._get_proj()
    a = make_point(sdk, "a", confidence=0.3)
    b = make_point(sdk, "b", confidence=0.9)
    snap = grounding_snapshot(proj)
    assert snap["count"] == 2
    assert snap["mean"] == pytest.approx(0.6)
    assert snap["mean"] == pytest.approx(mean_grounding(proj))
    assert set(snap["points"]) == {a["id"], b["id"]}
    assert "sampled_at" in snap and snap["sampled_at"]  # noqa: RUF019


def test_mean_grounding_null_confidence_imputed_zero_five(sdk):
    """A live Point with no confidence contributes 0.5 to the mean — the
    coalesce(0.5) imputation is a DELIBERATE extension of the DE2E-4 formula
    (repo-wide NULL-confidence convention), not part of the pinned plan
    formula. Asserted here per review round 1."""
    proj = sdk._get_proj()
    make_point(sdk, "no conf a")
    make_point(sdk, "no conf b")
    assert mean_grounding(proj) == pytest.approx(0.5)
    snap = grounding_snapshot(proj)
    assert snap["count"] == 2
    assert set(snap["points"].values()) == {0.5}
    # Mixed: one confident + two null → mean is (0.9 + 0.5 + 0.5) / 3.
    make_point(sdk, "confident", confidence=0.9)
    assert mean_grounding(proj) == pytest.approx((0.9 + 0.5 + 0.5) / 3)


# ── grounding_drift: ≤2% mean / ≤5% max single-point ceilings ─────

def test_grounding_drift_identical_snapshots_pass(sdk):
    make_point(sdk, "a", confidence=0.5)
    proj = sdk._get_proj()
    pre = grounding_snapshot(proj)
    post = grounding_snapshot(proj)
    drift = grounding_drift(pre, post)
    assert drift["passed"] is True
    assert drift["mean_abs_delta"] == 0.0
    assert drift["max_point_abs_delta"] == 0.0


def test_grounding_drift_mean_ceiling_fails(sdk):
    # Hand-built snapshots: mean shifts 0.50 → 0.53 (3% > 2% ceiling) while
    # every point moves < 5% — only the mean ceiling trips.
    pre = {"count": 2, "mean": 0.50,
           "points": {"p1": 0.50, "p2": 0.50}}
    post = {"count": 2, "mean": 0.53,
            "points": {"p1": 0.51, "p2": 0.55}}
    drift = grounding_drift(pre, post)
    assert drift["passed"] is False
    assert drift["mean_abs_delta"] == pytest.approx(0.03)
    assert drift["max_point_abs_delta"] == pytest.approx(0.05)
    assert drift["mean_ceiling"] == MAX_GROUNDING_DRIFT


def test_grounding_drift_point_ceiling_fails(sdk):
    # Mean moves 0.017 (≤2%) but one point flips 0.5 → 0.6 (10% > 5%): the
    # per-point ceiling (R12) must catch what the mean masks. Means are
    # recomputed over the ID intersection (review round 1) — with 6 points
    # the single 0.1 flip dilutes to a 0.0167 mean delta, under the 2% mean
    # ceiling, so only the per-point ceiling trips.
    pre = {"count": 6, "mean": 0.50,
           "points": {"p0": 0.50, "p1": 0.50, "p2": 0.50,
                       "p3": 0.50, "p4": 0.50, "p5": 0.50}}
    post = {"count": 6, "mean": 0.5167,
            "points": {"p0": 0.60, "p1": 0.50, "p2": 0.50,
                        "p3": 0.50, "p4": 0.50, "p5": 0.50}}
    drift = grounding_drift(pre, post)
    assert drift["passed"] is False
    assert drift["mean_abs_delta"] == pytest.approx(1 / 60)  # 0.0167 ≤ 0.02
    assert drift["max_point_abs_delta"] == pytest.approx(0.10)


def test_grounding_drift_removed_points_not_regression(sdk):
    # A Point deleted between snapshots is not a regression: per-point deltas
    # only run over the ID intersection.
    pre = {"count": 2, "mean": 0.50,
           "points": {"p1": 0.50, "gone": 0.50}}
    post = {"count": 1, "mean": 0.50, "points": {"p1": 0.50}}
    drift = grounding_drift(pre, post)
    assert drift["passed"] is True
    assert drift["max_point_abs_delta"] == 0.0


def test_grounding_drift_removed_point_mean_term_intersection_scoped(sdk):
    """The MEAN term is also computed over the ID intersection (review
    round 1): removing a high-confidence point whose value ≠ set mean shifts
    the full-set mean (0.90 → 0.50 here) but is not a regression — the
    intersection-scoped mean must stay flat and the check must pass."""
    pre = {"count": 2, "mean": 0.70,
           "points": {"p1": 0.50, "gone": 0.90}}
    post = {"count": 1, "mean": 0.50, "points": {"p1": 0.50}}
    drift = grounding_drift(pre, post)
    # Full-set means would read |0.50 - 0.70| = 0.20 > 0.02 ceiling;
    # intersection-scoped means are both 0.50 → delta 0.0.
    assert drift["passed"] is True
    assert drift["mean_abs_delta"] == pytest.approx(0.0)
    assert drift["max_point_abs_delta"] == pytest.approx(0.0)
    assert drift["pre_count"] == 2
    assert drift["post_count"] == 1


def test_grounding_drift_zero_overlap_fails_closed(sdk):
    """Total replacement (zero shared Point ids) must FAIL CLOSED (review
    round 2): with an empty intersection both recomputed means are 0.0, so
    the ceilings previously passed vacuously — pre 0.90 → post 0.30
    reported passed True. An empty comparison is vacuous, not evidence:
    fail with an explicit ``no_common_points`` reason and ``overlap: 0``."""
    pre = {"count": 2, "mean": 0.90,
           "points": {"old_a": 0.90, "old_b": 0.90}}
    post = {"count": 2, "mean": 0.30,
            "points": {"new_a": 0.30, "new_b": 0.30}}
    drift = grounding_drift(pre, post)
    assert drift["passed"] is False
    assert drift["reason"] == "no_common_points"
    assert drift["overlap"] == 0
    assert drift["mean_abs_delta"] == 0.0
    assert drift["max_point_abs_delta"] == 0.0
    assert drift["pre_count"] == 2
    assert drift["post_count"] == 2


def test_grounding_drift_nonempty_overlap_reported(sdk):
    """The ID-intersection size is surfaced on the result for all paths —
    an empty intersection is the fail-closed signal (overlap == 0), so a
    non-empty intersection reports its overlap alongside a normal pass."""
    pre = {"count": 3, "mean": 0.50,
           "points": {"p1": 0.50, "p2": 0.50, "gone": 0.50}}
    post = {"count": 2, "mean": 0.50, "points": {"p1": 0.50, "p2": 0.50}}
    drift = grounding_drift(pre, post)
    assert drift["passed"] is True
    assert drift["overlap"] == 2
    assert "reason" not in drift


def test_grounding_drift_ceiling_constants_pinned(sdk):
    # The 0.02 constant is the EpSafeCommit max_grounding_drift seam (#785) —
    # pin it so the contract cannot silently drift.
    assert MAX_GROUNDING_DRIFT == 0.02
    assert MAX_POINT_DRIFT == 0.05


# ── Calibration milestone marker: DE2E-7 local contract ───────────


