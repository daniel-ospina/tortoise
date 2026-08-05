"""Integration tests for EP calibration pipeline — Issue #7478."""
import os

import pytest
from tortoise.sdk import TortoiseSDK
from tortoise.exceptions import CalibrationError


@pytest.fixture
def sdk():
    """Create a TortoiseSDK connected to an ISOLATED test graph."""
    import os
    # Isolated test graph — never the production default. The conftest.py
    # guard (#102) blocks production URIs unless ALLOW_DESTRUCTIVE_TESTS=1.
    os.environ.setdefault("TORTOISE_DB_URI", "docker://localhost:16379/test_calibration")
    s = TortoiseSDK()
    yield s
    # Cleanup: delete ALL nodes in the ISOLATED test graph only.
    # (Safe because conftest.py redirects/guards production URIs.)
    s._get_proj().g.query("MATCH (n) DETACH DELETE n")


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
    """require_calibration=True on uncalibrated graph raises CalibrationError."""
    sdk.create_point("statement", "Uncalibrated claim")
    sdk.create_point("statement", "Another uncalibrated")
    
    with pytest.raises(CalibrationError, match="uncalibrated"):
        sdk.compute_confidence(require_calibration=True)


def test_require_calibration_partial(sdk):
    """One calibrated, one not → still raises."""
    p1 = sdk.create_point("statement", "Calibrated", credibility="gold")
    sdk.create_point("statement", "Not calibrated")
    
    sdk.create_operator("IMPL", p1["id"], [sdk.create_point("statement", "target")["id"]])
    
    with pytest.raises(CalibrationError):
        sdk.compute_confidence(require_calibration=True)


def test_require_calibration_default(sdk):
    """require_calibration=False runs normally on uncalibrated graph."""
    sdk.create_point("statement", "Uncalibrated", credibility="medium")
    
    result = sdk.compute_confidence()  # default False
    assert result["converged"] is True


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
    """Source T1 → Point gets Beta(8,2) via inheritance."""
    # Create Point with extractedFrom — this creates a Source node
    p = sdk.create_point("statement", "Inherited claim", 
                         extractedFrom="https://doi.org/10.1234/peer-reviewed")
    proj = sdk._get_proj()
    # Set credibilityTier on the auto-created Source
    proj.g.query(
        "MATCH (s:Source {url: $url}) SET s.credibilityTier = 'T1'",
        params={"url": "https://doi.org/10.1234/peer-reviewed"}
    )
    
    sdk._apply_source_inheritance()
    point = sdk.get_point(p["id"])
    assert point.get("ep_alpha") == 8
    assert point.get("ep_beta") == 2
    assert point.get("baseline_set") is True


def test_source_inheritance_multi_source(sdk):
    """Point with Sources T1 and T3 → gets T1 (highest tier wins)."""
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
    
    sdk._apply_source_inheritance()
    point = sdk.get_point(p["id"])
    assert point.get("ep_alpha") == 8  # T1 = (8, 2), highest tier wins
    assert point.get("ep_beta") == 2


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
