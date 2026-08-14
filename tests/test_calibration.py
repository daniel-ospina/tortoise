"""Tests for EP calibration pipeline — Issue #7478.

Runs on the embedded falkordblite fixture (no Docker needed, AGENTS.md) —
migrated in #398; the stale live-FalkorDB skip probe was removed as part of
#344 so the fail-closed default-flip tests actually execute.
"""
import os

import pytest
from tortoise.sdk import TortoiseSDK
from tortoise.exceptions import CalibrationError


@pytest.fixture
def sdk():
    """Fresh embedded TortoiseSDK on an isolated file DB (no Docker needed).

    Uses the ``TortoiseSDK(db_path)`` embedded pattern (issue #398 Task 3 —
    migrated from the Docker-bound fixture so the calibration suite runs with
    falkordblite, per AGENTS.md "no Docker needed").
    """
    import tempfile
    db_path = os.path.join(tempfile.mkdtemp(prefix="tt_calib_"), "test.db")
    s = TortoiseSDK(db_path)
    yield s
    s.close()


# ── Pipeline E2E ────────────────────────────────────────────────

def test_calibration_pipeline_e2e(sdk):
    """End-to-end: Source → Points → calibrate_summary → EP passes."""
    src_url = "https://doi.org/10.1234/systematic-review"
    # Create Point with extractedFrom — this creates a Source node
    p1 = sdk.create_point("statement", "X causes Y",
                          credibility="gold", extractedFrom=src_url)
    p2 = sdk.create_point("statement", "Y prevents Z",
                          extractedFrom=src_url)
    # Set credibilityTier on the Source so p2 inherits it
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (s:Source {url: $url}) SET s.credibilityTier = 'T1'",
        params={"url": src_url}
    )
    
    # Link them
    sdk.create_operator("IMPL", p1["id"], [p2["id"]])
    
    # calibrate_summary shows calibrated
    summary = sdk.calibrate_summary()
    calibrated_ids = {item["id"] for item in summary if item["calibrated"]}
    assert p1["id"] in calibrated_ids  # explicit credibility="gold"
    # p2 not yet calibrated — source inheritance happens inside compute_confidence
    
    # EP with gate passes
    result = sdk.compute_confidence(require_calibration=True)
    assert result["converged"] is True


# ── require_calibration gate ────────────────────────────────────

def test_require_calibration_raises(sdk):
    """require_calibration=True on uncalibrated LIVE graph raises CalibrationError.

    #780/PR #1212: draft points are excluded from EP (factor extraction +
    propagation), so the gate only guards live evidence — drafts must be
    promoted (create_operator / update_point draft→live) before the
    fail-closed gate applies to them.
    """
    sdk.create_point("statement", "Uncalibrated claim", status="live")
    sdk.create_point("statement", "Another uncalibrated", status="live")
    
    with pytest.raises(CalibrationError, match="uncalibrated"):
        sdk.compute_confidence(require_calibration=True)


def test_require_calibration_partial(sdk):
    """One calibrated, one live-uncalibrated → still raises."""
    p1 = sdk.create_point("statement", "Calibrated", credibility="gold")
    # Live uncalibrated point — the draft default would be excluded from the
    # gate (#780/PR #1212), so the fail-closed assertion needs explicit live.
    sdk.create_point("statement", "Not calibrated", status="live")
    
    sdk.create_operator("IMPL", p1["id"], [sdk.create_point("statement", "target")["id"]])
    
    with pytest.raises(CalibrationError):
        sdk.compute_confidence(require_calibration=True)


def test_require_calibration_default(sdk):
    """Default require_calibration=True is fail-closed (#344).

    A genuinely uncalibrated graph (no credibility → baseline_set=false)
    must raise CalibrationError under the default instead of silently
    running EP on topology alone; a calibrated graph succeeds.
    """
    # Uncalibrated LIVE graph → fail-closed under the flipped default.
    sdk.create_point("statement", "Uncalibrated claim", status="live")
    with pytest.raises(CalibrationError, match="calibrate_summary"):
        sdk.compute_confidence()  # default True

    # Companion: a calibrated graph succeeds under the default. The gate is
    # graph-wide, so this needs a fresh graph (the uncalibrated point above
    # would still trip it).
    import tempfile
    s2 = TortoiseSDK(os.path.join(tempfile.mkdtemp(prefix="tt_calib_"), "test.db"))
    try:
        p1 = s2.create_point("statement", "Calibrated claim", credibility="gold")
        p2 = s2.create_point("statement", "Related claim", credibility="gold")
        s2.create_operator("IMPL", p1["id"], [p2["id"]])
        result = s2.compute_confidence()  # default True, graph is calibrated
        assert result["converged"] is True
    finally:
        s2.close()


def test_require_calibration_ignores_drafts(sdk):
    """Draft evidence points do NOT trip the fail-closed gate (#780, PR #1212).

    create_point defaults to status='draft', and drafts are excluded from
    factor extraction + EP propagation (include_draft=False). A graph whose
    only uncalibrated points are drafts must not demand calibration of
    points EP will never use — the gate guards live evidence only.
    """
    sdk.create_point("statement", "Draft staging claim")  # defaults to draft
    sdk.create_point("statement", "Another draft", status="draft")
    # Draft-only graph → gate passes (nothing live to calibrate); EP finds
    # no live factors and returns a no-op result instead of raising.
    result = sdk.compute_confidence(require_calibration=True)
    assert result.get("diagnostic") == "no_factors"

    # Mixed: one live uncalibrated point still fails closed.
    import tempfile
    s2 = TortoiseSDK(os.path.join(tempfile.mkdtemp(prefix="tt_calib_"), "test.db"))
    try:
        s2.create_point("statement", "Draft staging claim")
        s2.create_point("statement", "Live uncalibrated", status="live")
        with pytest.raises(CalibrationError, match="uncalibrated"):
            s2.compute_confidence(require_calibration=True)
    finally:
        s2.close()


# ── calibrate_summary ───────────────────────────────────────────

def test_calibrate_summary_empty(sdk):
    """Empty graph returns empty list."""
    summary = sdk.calibrate_summary()
    assert isinstance(summary, list)


def test_calibrate_summary_source_hint(sdk):
    """Uncalibrated Point with Source → suggestion mentions Source."""
    # Create a Source node via _link_source (creates Source + extractedFrom edge)
    src_url = "https://example.com/blog-post"
    p = sdk.create_point("statement", "Claim from blog", extractedFrom=src_url)
    
    summary = sdk.calibrate_summary()
    uncal = [s for s in summary if not s["calibrated"]]
    assert len(uncal) >= 1
    # The suggestion should mention Source since the Point has an extractedFrom edge
    assert "Source" in str(uncal[0].get("suggestion", ""))


# ── create_point credibility ────────────────────────────────────

def test_create_point_credibility(sdk):
    """credibility='gold' sets ep_alpha=10, ep_beta=1 on node."""
    p = sdk.create_point("statement", "Gold tier claim", credibility="gold")
    point = sdk.get_point(p["id"])
    assert point.get("ep_alpha") == 10
    assert point.get("ep_beta") == 1
    assert point.get("baseline_set") is True


def test_create_point_dedup_no_overwrite(sdk):
    """Dedup doesn't overwrite existing baseline."""
    p1 = sdk.create_point("statement", "X", credibility="gold", dedup=True)
    # Same content, different credibility — dedup returns existing
    p2 = sdk.create_point("statement", "X", credibility="unverified", dedup=True)
    assert p1["id"] == p2["id"]
    point = sdk.get_point(p1["id"])
    assert point.get("ep_alpha") == 10  # original gold baseline preserved


def test_create_point_no_credibility(sdk):
    """Omit credibility → Beta(1,1), baseline_set=false."""
    p = sdk.create_point("statement", "No credibility set")
    point = sdk.get_point(p["id"])
    # baseline_set should be false/missing
    assert point.get("baseline_set") in (None, False)


# ── set_point_baseline persistence ──────────────────────────────

def test_set_baseline_persistence(sdk):
    """Baseline survives graph round-trip (persisted to node)."""
    p = sdk.create_point("statement", "Will be baselined")
    sdk.set_point_baseline(p["id"], 8, 2)
    point = sdk.get_point(p["id"])
    assert point.get("ep_alpha") == 8
    assert point.get("ep_beta") == 2
    assert point.get("baseline_set") is True


# ── Source inheritance ──────────────────────────────────────────

def test_source_inheritance(sdk):
    """Source T1 → Point gets Beta(5,1) via inheritance (validated model)."""
    # Create Point with extractedFrom — this creates a Source node
    p = sdk.create_point("statement", "Inherited claim", 
                         extractedFrom="https://doi.org/10.1234/peer-reviewed")
    proj = sdk._get_proj()
    # Set credibilityTier on the auto-created Source
    proj.g.query(
        "MATCH (s:Source {url: $url}) SET s.credibilityTier = 'T1'",
        params={"url": "https://doi.org/10.1234/peer-reviewed"}
    )
    
    sdk._apply_source_inheritance(recency_decay=1.0)
    point = sdk.get_point(p["id"])
    # T1 = (5, 1) per docs/ep-source-credibility-experiment.md §1.1 (stale (8,2) removed)
    assert point.get("ep_alpha") == 5
    assert point.get("ep_beta") == 1
    assert point.get("baseline_set") is True
    assert point.get("baseline_source") == "inherited"


def test_source_inheritance_multi_source(sdk):
    """Point with Sources T1 and T3 → log-scale aggregated prior (#398).

    pc = T1: 4.0 * log2(2) + T3: 1.0 * log2(2) = 5.0 → Beta(6, 1).
    Replaces the old highest-tier-wins behavior (T1 alone → Beta(5,1)).
    """
    url1 = "https://doi.org/10.1234/peer-reviewed"
    url2 = "https://example.com/anecdotal"
    
    # Create Point with both Sources via _link_source
    p = sdk.create_point("statement", "Multi-source claim")
    proj = sdk._get_proj()
    proj._link_source(p["id"], url1)
    proj._link_source(p["id"], url2)
    
    # Set credibilityTiers on the auto-created Sources
    proj.g.query(
        "MATCH (s:Source {url: $url}) SET s.credibilityTier = 'T1'",
        params={"url": url1}
    )
    proj.g.query(
        "MATCH (s:Source {url: $url}) SET s.credibilityTier = 'T3'",
        params={"url": url2}
    )
    
    sdk._apply_source_inheritance(recency_decay=1.0)
    point = sdk.get_point(p["id"])
    assert point.get("ep_alpha") == 6  # log-scale aggregation: 1 + 4*1 + 1*1
    assert point.get("ep_beta") == 1
    assert point.get("baseline_source") == "inherited"


# ── baseline_set flag ───────────────────────────────────────────

def test_baseline_set_flag(sdk):
    """credibility → baseline_set=true, omit → baseline_set=false."""
    p1 = sdk.create_point("statement", "With credibility", credibility="high")
    p2 = sdk.create_point("statement", "Without credibility")
    
    assert sdk.get_point(p1["id"]).get("baseline_set") is True
    assert sdk.get_point(p2["id"]).get("baseline_set") in (None, False)


# ── Non-evidence kinds don't block gate ─────────────────────────

def test_non_evidence_kinds_ignored_by_gate(sdk):
    """Diary/checkpoint points with no baseline shouldn't block EP."""
    sdk.create_point("diary", "Today's note")
    p = sdk.create_point("statement", "A claim", credibility="medium")
    
    # Gate should pass — diary is not an evidence kind
    result = sdk.compute_confidence(require_calibration=True)
    assert result["converged"] is True


# ── #1157: un-gated EP surfaces (dream / get_confidence) ────────

def test_dream_require_calibration_raises(sdk):
    """dream(require_calibration=True) on uncalibrated graph raises
    CalibrationError BEFORE any EP write (#1157)."""
    # #943: default status is draft; the #1157 gate excludes drafts (#780),
    # so the point must be live for the gate to see it.
    sdk.create_point("statement", "Uncalibrated claim", status="live")

    with pytest.raises(CalibrationError, match="dream.*uncalibrated"):
        sdk.dream(require_calibration=True)


def test_dream_gated_passes_when_calibrated(sdk):
    """dream(require_calibration=True) runs on a calibrated graph."""
    p = sdk.create_point("statement", "Calibrated", credibility="gold")
    sdk.create_operator("IMPL", p["id"],
                        [sdk.create_point("statement", "target",
                                          credibility="gold")["id"]])

    # Mark dirty so the dream path actually runs EP work
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (n:Point) WHERE n.is_operator = false SET n.confidence = 0.4")
    sdk._dirty_roots.update(
        r[0] for r in proj.g.query(
            "MATCH (n:Point) WHERE n.is_operator = false RETURN n.id").result_set)

    result = sdk.dream(dirty_only=True, require_calibration=True)
    assert result["converged"] is True


def test_get_confidence_require_calibration_raises(sdk):
    """get_confidence(require_calibration=True) on uncalibrated graph raises
    CalibrationError (#1157) — the per-claim read is an EP surface."""
    p = sdk.create_point("statement", "Uncalibrated claim", status="live")

    with pytest.raises(CalibrationError, match="get_confidence.*uncalibrated"):
        sdk.get_confidence(p["id"], require_calibration=True)


def test_ep_require_calibration_env_default(sdk, monkeypatch):
    """TORTOISE_EP_REQUIRE_CALIBRATION=1 flips the shared default for the
    #1157 surfaces (dream, get_confidence) — the #344 flip semantics applied
    to the once-un-gated surfaces, one knob for the #7478 target. The
    explicit compute_confidence surface keeps its own (still-False) default;
    #344 flips it separately."""
    monkeypatch.setenv("TORTOISE_EP_REQUIRE_CALIBRATION", "1")
    p = sdk.create_point("statement", "Uncalibrated claim", status="live")

    # #1157 surfaces refuse by default (no explicit arg)
    with pytest.raises(CalibrationError, match="dream"):
        sdk.dream()
    with pytest.raises(CalibrationError, match="get_confidence"):
        sdk.get_confidence(p["id"])

    # Explicit False is still the documented escape hatch on dream
    result = sdk.dream(require_calibration=False)
    assert result["converged"] is True


def test_compute_confidence_explicit_optout_skips_inner_auto_dream(
        sdk, monkeypatch):
    """#1314: an explicit require_calibration=False must propagate to the
    internal lazy-consistency auto-dreams (sdk.py:6494/6523).

    Pre-fix regression: #1157/#1210 gated the auto-dreams with the
    fail-closed default (env=1) but did NOT propagate the caller's explicit
    False — so compute_confidence(require_calibration=False) with dirty
    roots + uncalibrated live evidence raised CalibrationError("dream: ...")
    from the inner dream despite the opt-out (test_ep_sources.py broke).

    Post-fix: the explicit opt-out is honored end-to-end; the gate still
    fires when the caller does NOT opt out (fail-closed preserved — covered
    by test_require_calibration_raises / test_ep_require_calibration_env
    _default).
    """
    monkeypatch.setenv("TORTOISE_EP_REQUIRE_CALIBRATION", "1")  # fail-closed
    # Uncalibrated LIVE evidence (no baseline) + an operator → dirty roots.
    p1 = sdk.create_point("statement", "Uncalibrated live claim",
                          status="live")
    p2 = sdk.create_point("statement", "Related live claim", status="live")
    sdk.create_operator("IMPL", p1["id"], [p2["id"]])

    # No-arg path (sdk.py:6494 auto-extract dream): explicit opt-out must
    # not raise, and must return a converged result.
    result = sdk.compute_confidence(require_calibration=False)
    assert result["converged"] is True

    # Anchors path (sdk.py:6523 bounded-pass dream with dirty roots): same.
    result2 = sdk.compute_confidence(anchors=[p1["id"]],
                                     require_calibration=False)
    assert result2["converged"] is True
