"""A12 (epic #902, issue #1050) — docs/behavior conformance test.

Closes the cycle-22 doc/behavior drift disease class: INGEST_CONTRACT.md's
response examples must stay key-set-equal to the REAL sdk.ingest /
tortoise_ingest responses, the tool description must name the gated
default, and the skill (J8) must carry the operational markers.

Canonical key enumeration (plan §5.5/E2E-6.2, INGEST_CONTRACT §2.2/§3.2):
  top-level  = {granularity, batch_id, created, deduped, ids, nudges,
                warnings}  (+ results for granularity="granular")
  created/deduped = {points, entities, sources, connections}
  ids        = {points, entities, sources, connections, refs}
  failure    = {error, code, violations}  (never a results key)
  warnings   = the ELEVEN-key closed set (§3.2 table)

Skill markers (J8 — the how-to-use-tortoise skill section is #1057's
deliverable; the skill file lives in the agent-infra repo and is mirrored
into this repo's skills/ only on dev machines — the marker-grep SKIPS when
the file/markers are absent so CI stays green until #1057 lands).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK

REPO_ROOT = Path(__file__).resolve().parents[1]

# ── canonical enumeration anchors (INGEST_CONTRACT §2.2/§2.3/§3.2) ────
TOP_LEVEL = {"granularity", "batch_id", "created", "deduped", "ids",
             "nudges", "warnings"}
SECTION_COUNTS = {"points", "entities", "sources", "connections"}
IDS_KEYS = {"points", "entities", "sources", "connections", "refs"}
ELEVEN_WARNING_KEYS = {
    "append_only_items", "modified_item_residue", "mitigation_orphan_residue",
    "mitigation_drift_duplicate", "nfc_straddle_duplicate",
    "mitigation_strength_change", "partial_operator_residue",
    "operator_absorb_completed", "label_dropped_resubmit",
    "direction_dropped_resubmit", "direction_changed_resubmit",
}


@pytest.fixture
def sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_conf_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


def _bundle():
    return {
        "points": [
            {"ref": "p1", "kind": "claim", "content": "A implies B."},
            {"ref": "p2", "kind": "claim", "content": "B."},
        ],
        "entities": [
            {"ref": "s1", "type": "subject", "name": "Ferra Labs",
             "subjectKind": "organization"},
        ],
        "sources": [
            {"ref": "src1", "url": "https://example.com/rust-report",
             "sourceKind": "report", "tier": "T1"},
        ],
        "connections": [
            {"ref": "c1", "from": "p1", "to": "p2", "operator": "IMPL"},
            {"ref": "c2", "from": "s1", "to": "p1", "relation": "authoredBy"},
            {"ref": "c3", "from": "p1", "to": "src1",
             "relation": "extractedFrom"},
        ],
    }


# ── indicator 1: doc example key-set-equal to real responses ─────────

def test_sdk_success_response_key_set_equal(sdk):
    """A12 indicator 1: the SDK success response's key set == the canonical
    enumeration (top-level + created/deduped + ids); granular adds results."""
    for granularity in ("bulk", "granular"):
        res = sdk.ingest(_bundle(), granularity=granularity)
        assert set(res.keys()) == TOP_LEVEL | (
            {"results"} if granularity == "granular" else set()), \
            f"{granularity}: {sorted(res.keys())}"
        assert set(res["created"].keys()) == SECTION_COUNTS
        assert set(res["deduped"].keys()) == SECTION_COUNTS
        assert set(res["ids"].keys()) == IDS_KEYS
        assert res["granularity"] == granularity
        assert len(res["batch_id"]) == 26
        assert isinstance(res["warnings"], list)


def test_mcp_success_response_key_set_equal(sdk, monkeypatch):
    """A12 indicator 1 (MCP surface): tortoise_ingest returns the SAME key
    set through the handler layer."""
    import tortoise.mcp_server as mcp_mod
    from tortoise.mcp_auth import (_current_team_id, _current_team_limits,
                                   _transport_mode)
    _transport_mode.set("stdio")
    _current_team_id.set(None)
    _current_team_limits.set(None)
    orig = mcp_mod._get_team_sdk
    mcp_mod._get_team_sdk = lambda: sdk
    try:
        res = mcp_mod.tortoise_ingest(bundle=_bundle())
        assert set(res.keys()) == TOP_LEVEL, sorted(res.keys())
        assert set(res["created"].keys()) == SECTION_COUNTS
        assert set(res["deduped"].keys()) == SECTION_COUNTS
        assert set(res["ids"].keys()) == IDS_KEYS
    finally:
        _transport_mode.set(None)
        _current_team_id.set(None)
        _current_team_limits.set(None)
        mcp_mod._get_team_sdk = orig


def test_failure_response_shape_no_results_key(sdk, monkeypatch):
    """A12 indicator 1 failure half: an invalid bundle returns the
    {error, code, violations} shape — NEVER a results key (the bulk failure
    shape, §2.5/§5.2)."""
    from tortoise.exceptions import BundleValidationError
    bad = {
        "points": [
            {"ref": "p1", "kind": "claim", "content": "A."},
            {"ref": "p2", "kind": "claim", "content": "B."},
        ],
        "connections": [
            {"from": "p1", "to": "01GHOST00000000000000000000",
             "operator": "IMPL"},
        ],
    }
    with pytest.raises(BundleValidationError):
        sdk.ingest(bad, granularity="granular")
    # MCP surface: structured {error, code: ERR_BUNDLE_INVALID, violations}
    import tortoise.mcp_server as mcp_mod
    from tortoise.mcp_auth import (_current_team_id, _current_team_limits,
                                   _transport_mode)
    _transport_mode.set("stdio")
    _current_team_id.set(None)
    _current_team_limits.set(None)
    orig = mcp_mod._get_team_sdk
    mcp_mod._get_team_sdk = lambda: sdk
    try:
        res = mcp_mod.tortoise_ingest(bundle=bad, granularity="granular")
        assert res["code"] == mcp_mod.ERR_BUNDLE_INVALID
        assert "violations" in res and res["violations"]
        assert "results" not in res, "failure shape must have no results key"
    finally:
        _transport_mode.set(None)
        _current_team_id.set(None)
        _current_team_limits.set(None)
        mcp_mod._get_team_sdk = orig


# ── indicator 3: tool description markers ────────────────────────────

def test_tool_description_names_promotion_policy_and_gated_default():
    """A12 indicator 3: the tortoise_ingest tool description names
    promotion_policy + the gated default (the #131-behavior-change
    announcement surface)."""
    from tortoise.tool_registry import TOOL_REGISTRY
    entry = next((t for t in TOOL_REGISTRY
                  if t.name == "tortoise_ingest"), None)
    assert entry is not None, "tortoise_ingest missing from TOOL_REGISTRY"
    desc = entry.description
    assert "promotion_policy" in desc, "tool must name promotion_policy"
    assert "gated" in desc, "tool must name the gated default"


# ── doc conformance: the contract doc carries the canonical anchors ───

def test_doc_carries_canonical_enumeration_anchors():
    """A12 doc-side: INGEST_CONTRACT.md's §2.2 example JSON block carries
    every canonical top-level key and the §3.2 warnings table carries all
    ELEVEN keys (the closed-set anchor)."""
    doc = (REPO_ROOT / "docs" / "INGEST_CONTRACT.md").read_text(
        encoding="utf-8")
    for key in TOP_LEVEL:
        assert f'"{key}"' in doc, f"§2.2 example missing key {key!r}"
    for key in SECTION_COUNTS:
        assert f'"{key}"' in doc, f"created/deduped example missing {key!r}"
    for key in IDS_KEYS:
        assert f'"{key}"' in doc, f"ids example missing {key!r}"
    for key in sorted(ELEVEN_WARNING_KEYS):
        assert f"`{key}`" in doc or key in doc, \
            f"§3.2 warnings table missing key {key!r}"


# ── indicator 2: skill markers (J8 — skip until the section lands) ───

def _skill_text() -> str | None:
    """The how-to-use-tortoise skill (mirrored from agent-infra; absent in
    CI / fresh worktrees — the marker-grep skips then)."""
    p = REPO_ROOT / "skills" / "how-to-use-tortoise" / "SKILL.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def test_skill_markers_contract():
    """A12 indicator 2 (J8): when the how-to-use-tortoise skill section
    exists (the agent-infra mirror), it must carry the operational markers —
    bundle shape, granularity, the gated default, the interim promotion
    entry, idempotent resubmission, the retry/decision tables, and all
    ELEVEN warning keys. SKIPPED until #1057 (J8) lands the section — the
    marker-grep is the enforcement the moment it does."""
    text = _skill_text()
    if text is None or "promotion_policy" not in text:
        pytest.skip("J8 skill section (#1057) not yet landed — markers "
                    "absent/untracked")
    assert "granularity" in text
    assert "gated" in text
    assert "promotion_policy" in text
    assert "tortoise_update_point" in text, "interim promotion entry"
    assert "resubmit" in text.lower() or "idempotent" in text.lower()
    assert "retry" in text.lower()
    for key in sorted(ELEVEN_WARNING_KEYS):
        assert key in text, f"skill warnings section missing {key!r}"
