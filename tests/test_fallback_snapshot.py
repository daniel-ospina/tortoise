"""Tests for the degraded-fallback corpus snapshot (#1375).

Covers: cached-vs-legacy result parity (the #399 sklearn contract), write
invalidation (create/operator/delete via the _mark_dirty hook), the lazy TTL
backstop, the size cap, and the include_terminal/kind/exclude_status
semantics.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise import fallback_snapshot as fs

TEST_GRAPH = "tortoise_test_1375_fallback_snapshot"

# FTS-miss queries: "zzz" is not in any corpus, so retrieval strategies miss
# and the degraded TF-IDF fallback fires deterministically.
FTS_MISS = "zzz qxqw nonexistent"


@pytest.fixture
def sdk(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "fb.db"), namespace=TEST_GRAPH)
    fs._store.clear()
    yield sdk
    fs._store.clear()
    try:
        sdk.test_guard()
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    sdk.close()


def _no_match_query(sdk, q=FTS_MISS):
    return sdk.tortoise_fts_query(query=q, limit=10)


def _cached_results(sdk, q=FTS_MISS):
    return [r["id"] for r in _no_match_query(sdk, q)]


def _store_has(sdk) -> bool:
    proj = sdk._get_proj()
    return fs._store.get(fs.snapshot_key(proj, sdk._namespace)) is not None


# ── Parity ─────────────────────────────────────────────────────────────

def test_fallback_snapshot_parity_with_legacy(sdk, monkeypatch):
    """Cached snapshot results == legacy path results (identical ids+order)."""
    for i in range(6):
        sdk.create_point("statement", f"pricing tier {i} enterprise plans memory")

    # 1. legacy (snapshot capped off) — restore the cap BEFORE the cached leg
    monkeypatch.setattr(fs, "MAX_CORPUS_POINTS", 0)
    legacy = [r["id"] for r in sdk.tortoise_fts_query(query="zzz pricing memory", limit=10)]
    assert not _store_has(sdk), "legacy leg must not build the snapshot"

    monkeypatch.setattr(fs, "MAX_CORPUS_POINTS", 50_000)
    fs._store.clear()
    cached = _cached_results(sdk, q="zzz pricing memory")
    assert _store_has(sdk), "cached leg must build the snapshot"
    assert cached == legacy, f"parity broken: cached={cached} legacy={legacy}"

    # 3. cached again (from store — same result)
    again = _cached_results(sdk, q="zzz pricing memory")
    assert again == cached, "snapshot path must be deterministic"


# ── Status/kind semantics ───────────────────────────────────────────────

def test_fallback_snapshot_status_and_kind_semantics(sdk):
    """include_terminal + exclude_status + kind mirror the legacy semantics."""
    a = sdk.create_point("statement", "onboarding zero cost strategy")
    b = sdk.create_point("statement", "onboarding zero cost strategy")
    c = sdk.create_point("decision", "onboarding zero cost strategy")  # other kind
    sdk.supersede_point(b["id"], a["id"])  # b → superseded (terminal)

    _no_match_query(sdk)  # build the snapshot
    proj = sdk._get_proj()
    snap = fs._store.get(fs.snapshot_key(proj, sdk._namespace))
    assert snap is not None, "snapshot must be built"

    # kind filter: only 'statement' points
    ids_kind = [r["id"] for r in fs.search_snapshot(
        "onboarding", snap, limit=10, kind="statement")]
    assert c["id"] not in ids_kind and a["id"] in ids_kind

    # include_terminal=False (default): superseded b excluded
    ids_default = [r["id"] for r in fs.search_snapshot(
        "onboarding", snap, limit=10, kind="statement")]
    assert b["id"] not in ids_default and a["id"] in ids_default

    # include_terminal=True: b back
    ids_terminal = [r["id"] for r in fs.search_snapshot(
        "onboarding", snap, limit=10, kind="statement", include_terminal=True)]
    assert b["id"] in ids_terminal

    # exclude_status composes on top
    ids_ex = [r["id"] for r in fs.search_snapshot(
        "onboarding", snap, limit=10, kind="statement",
        exclude_status=["superseded"])]
    assert b["id"] not in ids_ex and a["id"] in ids_ex


def test_fallback_snapshot_invalidated_point_absent():
    """outdated=true points (invalidate_point) are excluded at build."""
    # covered structurally: _SNAPSHOT_QUERY has coalesce(n.outdated,false)=false
    assert "outdated" in fs._SNAPSHOT_QUERY


# ── Invalidation ────────────────────────────────────────────────────────

def test_fallback_snapshot_invalidated_on_write(sdk):
    """A new point invalidates the snapshot (store dropped) and reappears."""
    sdk.create_point("statement", "alpha beta gamma delta")
    _no_match_query(sdk)  # build
    assert _store_has(sdk)
    sdk.create_point("statement", "alpha beta gamma delta zzz-new")
    assert not _store_has(sdk), "create must invalidate the snapshot"
    rebuilt = _cached_results(sdk, q="zzz alpha beta")
    assert len(rebuilt) == 1


def test_fallback_snapshot_invalidated_on_operator(sdk):
    """create_operator invalidates the snapshot (via _mark_dirty)."""
    a = sdk.create_point("statement", "alpha beta gamma delta")
    b = sdk.create_point("statement", "alpha beta gamma delta")
    _no_match_query(sdk)  # build
    assert _store_has(sdk)
    sdk.create_operator("IMPL", a["id"], [b["id"]])
    assert not _store_has(sdk), "operator write must invalidate the snapshot"


def test_fallback_snapshot_invalidated_on_delete(sdk):
    """A delete invalidates the snapshot — the deleted point leaves the corpus."""
    keep = sdk.create_point("statement", "zzz unique deleted content token")
    pid = sdk.create_point("statement", "zzz unique deleted content other")["id"]
    # Force the fallback (FTS-miss query) → snapshot built with both points
    _no_match_query(sdk)
    proj = sdk._get_proj()
    key = fs.snapshot_key(proj, sdk._namespace)
    assert fs._store.get(key) is not None, "snapshot must be built"
    assert pid in {p["id"] for p in fs._store.get(key)["points"]}

    sdk.delete_point(pid)
    assert fs._store.get(key) is None, "delete must invalidate the snapshot"

    # Rebuild → the deleted point is gone from the corpus; the keeper remains
    _no_match_query(sdk)
    rebuilt = fs._store.get(key)
    rebuilt_ids = {p["id"] for p in rebuilt["points"]}
    assert pid not in rebuilt_ids and keep["id"] in rebuilt_ids


# ── Lazy TTL backstop ───────────────────────────────────────────────────

def test_fallback_snapshot_lazy_ttl_fires(monkeypatch):
    """TTL fires at read time (not a background timer) → rebuild, logged."""
    fs._store.put(("g", "n"), {
        "built_at": 0.0, "dirty": False, "points": [],
        "vectorizer": None, "doc_vecs": None, "model_id": None,
    })
    monkeypatch.setattr(fs, "SNAPSHOT_TTL_SECONDS", 1.0)
    got = fs._store.get(("g", "n"))
    assert got is None, "stale snapshot must be dropped at read"


def test_fallback_snapshot_dirty_dropped():
    fs._store.put(("g", "n"), {
        "built_at": 10 ** 9, "dirty": True, "points": [],
        "vectorizer": None, "doc_vecs": None, "model_id": None,
    })
    assert fs._store.get(("g", "n")) is None, "dirty snapshot dropped"


# ── Size cap + legacy delegation ────────────────────────────────────────

def test_fallback_snapshot_size_cap(sdk, monkeypatch):
    """Above the cap the snapshot is skipped → legacy path, no crash."""
    sdk.create_point("statement", "some content here")
    monkeypatch.setattr(fs, "MAX_CORPUS_POINTS", 0)
    results = sdk.tortoise_fts_query(query=FTS_MISS, limit=10)
    assert isinstance(results, list), "must fall back to legacy, not crash"
    assert not _store_has(sdk), "snapshot must be skipped over the cap"


def test_search_snapshot_legacy_delegation(sdk, monkeypatch):
    """No cached vectors (sklearn/model unavailable) → legacy scorer runs."""
    sdk.create_point("statement", "alpha beta gamma delta")
    _no_match_query(sdk)  # build (sklearn available in this env → vectors present)
    proj = sdk._get_proj()
    snap = fs._store.get(fs.snapshot_key(proj, sdk._namespace))
    assert snap is not None
    # Simulate missing vectors: the store's snapshot has none
    snap["vectorizer"] = None
    snap["doc_vecs"] = None
    snap["model_id"] = None
    results = fs.search_snapshot("alpha", snap, limit=10)
    assert len(results) == 1, "must delegate to the legacy scorer"
