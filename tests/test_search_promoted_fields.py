"""SDK-level tests for #1353 — bounded decoration + promoted fields in tortoise_fts_query.

Uses FULL-SCAN mode (query=None, kind=...) deliberately: in embedded
FalkorDBLite, text queries degrade to the TF-IDF fallback which returns raw
undecorated points (pre-existing behavior, unchanged by #1353). Full-scan
runs the real decoration path (steps 4-9), so it exercises the bounded
relationships + promoted epistemic state deterministically.
"""
from __future__ import annotations

import os
import sys
import tempfile  # noqa: F401

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK

TEST_GRAPH = "tortoise_test_1353_search_promoted"


@pytest.fixture
def sdk(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "search.db"), namespace=TEST_GRAPH)
    yield sdk
    try:
        sdk.test_guard()
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    sdk.close()


def _scan(sdk, kind="statement", limit=50, include_terminal=False):
    return sdk.tortoise_fts_query(query=None, kind=kind, entity_type="point",
                                  limit=limit, include_terminal=include_terminal)


def test_full_scan_decorated_bounded(sdk):
    """Full-scan results carry bounded relationships (legacy keys, no content) + status."""
    a = sdk.create_point("statement", "alpha claim target")
    peers = [sdk.create_point("statement", f"peer {i} claim") for i in range(3)]
    sdk.create_operator("IMPL", a["id"], [p["id"] for p in peers])

    results = _scan(sdk)
    hit = next(r for r in results if r["id"] == a["id"])
    rels = hit["relationships"]
    peer_entries = [e for e in rels if "peer" in e]
    assert 0 < len(peer_entries) <= 10, "bounded per-point cap"
    assert all({"predicate", "mechanism", "operator_id", "related_id", "direction"} <= set(e) for e in peer_entries)
    assert all("related_content" not in e for e in peer_entries), "no content in list view"
    # family_size discloses the true operator family (a + 3 peers = 4 endpoints)
    assert any(e.get("family_size", 0) == 4 for e in peer_entries), "family_size disclosed"
    # status promoted
    assert "status" in hit, "status promoted on every point result"


def test_full_scan_promoted_superseded_and_subject(sdk):
    """Promoted fields: superseded_by (CORRECTS) + subject (own aboutSubject)."""
    old = sdk.create_point("statement", "obsolete claim")
    new = sdk.create_point("statement", "replacement claim")
    sdk.supersede_point(old["id"], new["id"])
    subj = sdk.create_subject("Epistemic Team", subjectKind="team")
    sdk._get_proj().create_about_edge(new["id"], subj["id"], "aboutSubject")

    results = _scan(sdk, include_terminal=True)
    old_hit = next(r for r in results if r["id"] == old["id"])
    new_hit = next(r for r in results if r["id"] == new["id"])
    assert old_hit["status"] == "superseded"
    assert old_hit["superseded_by"]["id"] == new["id"]
    assert "replacement" in old_hit["superseded_by"]["content_snippet"]
    assert any(s["id"] == old["id"] for s in new_hit["supersedes"])
    assert new_hit["subject"]["name"] == "Epistemic Team"
    # CORRECTS structure surfaces in the relationships list too
    assert any(e["mechanism"] == "CORRECTS" for e in old_hit["relationships"])


def test_retrieval_ranking_unchanged(sdk, monkeypatch):
    """Guardrail D1: decoration never reorders — identical result-id sequence."""
    pts = [sdk.create_point("statement", f"claim {i}") for i in range(5)]
    sdk.create_operator("IMPL", pts[0]["id"], [p["id"] for p in pts[1:]])

    baseline = _scan(sdk)
    baseline_ids = [r["id"] for r in baseline]

    import tortoise.search_engine as se
    monkeypatch.setattr(se, "get_relationships_bounded", lambda *a, **k: {})
    monkeypatch.setattr(se, "fetch_point_epistemic_state", lambda *a, **k: {})

    stripped = _scan(sdk)
    assert [r["id"] for r in stripped] == baseline_ids, "decoration must not reorder results"


def test_relationships_empty_when_no_operators(sdk):
    p = sdk.create_point("statement", "lonely claim")
    results = _scan(sdk)
    hit = next(r for r in results if r["id"] == p["id"])
    assert hit["relationships"] == []
    assert "status" in hit


# ── Task 4: expand_relationships (SDK + MCP + registry) ─────────────────

def test_expand_relationships_full_content(sdk):
    """The expand side returns the FULL unbounded payload incl. related_content."""
    a = sdk.create_point("statement", "alpha claim")
    b = sdk.create_point("statement", "beta claim with unique content payload")
    sdk.create_operator("IMPL", a["id"], [b["id"]])

    expanded = sdk.expand_relationships(a["id"])
    assert len(expanded) == 1
    assert "related_content" in expanded[0]
    assert "beta claim" in expanded[0]["related_content"]
    # #689/#898 posture: full-fidelity view is not blind to terminal state
    assert "related_status" in expanded[0]


def test_expand_relationships_missing_point(sdk):
    assert sdk.expand_relationships("does-not-exist") == []


def test_expand_mcp_tool(sdk, monkeypatch):
    """MCP surface routes to the SDK method and returns the full payload (isolated DB)."""
    from tortoise.mcp_auth import _transport_mode  # noqa: I001
    from tortoise.mcp_server import tortoise_expand_relationships
    from tortoise import mcp_server as mcp_mod
    assert callable(tortoise_expand_relationships)
    # repo convention: swap _get_team_sdk for the isolated fixture SDK
    monkeypatch.setattr(mcp_mod, "_get_team_sdk", lambda: sdk)
    token = _transport_mode.set("stdio")
    try:
        a = sdk.create_point("statement", "alpha claim")
        b = sdk.create_point("statement", "beta claim with unique content payload")
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        out = tortoise_expand_relationships(a["id"])
        assert isinstance(out, list), out
        assert any("related_content" in e and "beta claim" in e["related_content"] for e in out)
        # related_status annotated on the expand payload
        assert any(e.get("related_status") is not None for e in out)
    finally:
        _transport_mode.reset(token)


def test_expand_tool_registered(sdk):
    from tortoise.tool_registry import TOOL_REGISTRY
    names = {t.name for t in TOOL_REGISTRY}
    assert "tortoise_expand_relationships" in names
    entry = next(t for t in TOOL_REGISTRY if t.name == "tortoise_expand_relationships")
    assert entry.sdk_method == "expand_relationships"
    assert entry.group == "memory"
