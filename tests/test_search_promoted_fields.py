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
    # repo convention: swap _get_org_sdk for the isolated fixture SDK
    monkeypatch.setattr(mcp_mod, "_get_org_sdk", lambda: sdk)
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


# ── E6 (#1538) Task 4 — window fields + marker golden shapes ─────────────

def test_promoted_window_fields_and_default_live_lock(sdk):
    """T4: superseded hit (include_terminal) carries valid_from/valid_to/
    expired_at + status; DEFAULT search excludes it (D5 regression lock)."""
    old = sdk.create_point("statement", "gym at 6pm", validFrom="2026-06-10")
    new = sdk.create_point("statement", "gym at 5pm", validFrom="2026-06-14")
    sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-14")

    # default retrieval: superseded old excluded (live preference unchanged)
    default_ids = [r["id"] for r in _scan(sdk)]
    assert old["id"] not in default_ids
    assert new["id"] in default_ids

    # include_terminal surfaces the superseded hit WITH window fields
    all_results = _scan(sdk, include_terminal=True)
    old_hit = next(r for r in all_results if r["id"] == old["id"])
    assert old_hit["status"] == "superseded"
    assert old_hit["valid_from"] == "2026-06-10"
    assert old_hit["valid_to"] == "2026-06-14"
    assert old_hit["expired_at"], "expiredAt promoted on superseded hit"

    new_hit = next(r for r in all_results if r["id"] == new["id"])
    assert new_hit["valid_from"] == "2026-06-14"
    # live point — open window end: additive-only rule means NO valid_to key
    assert "valid_to" not in new_hit


def test_undated_hits_have_no_window_keys(sdk):
    """Undated points: to_dict emits NO valid_* keys (additive-only, #1353
    D8 rule — byte-identical output for legacy graphs)."""
    p = sdk.create_point("statement", "timeless belief")
    results = _scan(sdk)
    hit = next(r for r in results if r["id"] == p["id"])
    assert "valid_from" not in hit
    assert "valid_to" not in hit
    assert "expired_at" not in hit


def test_marker_strings_via_full_scan_decoration(sdk):
    """T4: the real decoration path surfaces window fields so the reader's
    [valid …] marker renders from SearchResult-promoted state."""
    from tools.longmem_eval.retrieve import _validity_marker

    old = sdk.create_point("statement", "gym at 6pm", validFrom="2026-06-10")
    new = sdk.create_point("statement", "gym at 5pm", validFrom="2026-06-14")
    sdk.supersede_point(old["id"], new["id"], valid_from="2026-06-14")

    results = _scan(sdk, include_terminal=True)
    old_hit = next(r for r in results if r["id"] == old["id"])
    assert old_hit["valid_from"] == "2026-06-10"
    marker = _validity_marker(old_hit)
    assert "[valid 2026-06-10 → 2026-06-14; expired " in marker
    assert marker.endswith("]")

    new_hit = next(r for r in results if r["id"] == new["id"])
    assert "[valid since 2026-06-14]" in _validity_marker(new_hit)


def test_provenance_flag_is_absent_by_default_and_additive_when_on(sdk, monkeypatch):
    """Objectives 5/9 + owner decision #3837 (source + when learned + confidence).

    The additive ``provenance`` block (source + captured_at) is OFF by default —
    a default full-scan result is byte-identical to pre-change output (no key).
    With ``TORTOISE_SEARCH_PROVENANCE`` set, every other key/value is unchanged
    and exactly the ``provenance`` block is added.
    """
    p = sdk.create_point(
        "statement",
        "beta ships in October",
        extractedFrom="https://example.com/beta-plan",
    )

    monkeypatch.delenv("TORTOISE_SEARCH_PROVENANCE", raising=False)
    off = next(r for r in _scan(sdk) if r["id"] == p["id"])
    assert "provenance" not in off, "flag OFF must not add a response field"

    monkeypatch.setenv("TORTOISE_SEARCH_PROVENANCE", "1")
    on = next(r for r in _scan(sdk) if r["id"] == p["id"])
    prov = on.pop("provenance")
    assert prov["source"] == "https://example.com/beta-plan"
    assert prov["captured_at"], "capture time must be present"
    assert on == off, "the flag must be purely additive — every other key is byte-identical"


def test_provenance_flag_on_still_carries_capture_time_for_unlinked_points(sdk, monkeypatch):
    """An unlinked point has no SOURCE to add, but its capture time is always
    present — so with the flag ON the block IS present and omits only ``source``
    (never an empty shell).

    The flag is additive, not conditional on the point being linked: it emits
    whatever provenance the point actually has, and a point is always created
    with a ``createdAt``.
    """
    p = sdk.create_point("statement", "orphan claim with no source")
    monkeypatch.setenv("TORTOISE_SEARCH_PROVENANCE", "1")
    hit = next(r for r in _scan(sdk) if r["id"] == p["id"])
    assert "provenance" in hit
    assert "source" not in hit["provenance"]
    assert hit["provenance"]["captured_at"]


def test_provenance_flag_is_point_only(sdk, monkeypatch):
    """The flag enriches POINT searches only — the other documented boundary.

    The flag-gated fetch and the two columns it reads live in the project
    search's ``if entity_type == "point":`` branch (``tortoise/sdk.py``), while
    ``SearchResult`` is constructed for every entity type.  So a non-degraded
    ``document``/``event``/``subject`` search carries no ``source_ref`` /
    ``captured_at`` and ``to_dict`` emits no ``provenance`` block, however the
    flag is set.  This is the second HALF of the boundary documented on
    ``search_engine.search_provenance_enabled``; the degraded-fallback half is
    the other.  Both directions are asserted, so the test cannot pass vacuously
    on an empty result set.
    """
    monkeypatch.setenv("TORTOISE_SEARCH_PROVENANCE", "1")
    p = sdk.create_point("statement", "beta ships in October",
                         extractedFrom="https://example.com/beta-plan")
    doc = sdk.create_document("Beta plan", "memo", content="beta memo body")

    point_hits = sdk.tortoise_fts_query(query=None, kind="statement",
                                        entity_type="point", limit=10)
    point_hit = next(r for r in point_hits if r["id"] == p["id"])
    assert "provenance" in point_hit, "the point leg must be enriched (non-vacuous)"

    doc_hits = sdk.tortoise_fts_query(query=None, kind="memo",
                                      entity_type="document", limit=10)
    doc_hit = next(r for r in doc_hits if r["id"] == doc["id"])
    assert "provenance" not in doc_hit, (
        "a non-Point hit gained a provenance block — the flag is documented as "
        "point-only; update the boundary note with the change")
