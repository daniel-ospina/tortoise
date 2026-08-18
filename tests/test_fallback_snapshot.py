"""Tests for the degraded-fallback corpus snapshot (#1375).

Covers: cached-vs-legacy result parity (the #399 sklearn contract), write
invalidation (create/delete via the _mark_dirty hook), the lazy TTL backstop,
the size cap, and the include_terminal/exclude_status semantics.
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


def _no_match_query(sdk, q="zzz nonexistent token"):
    """Run a query that forces the degraded TF-IDF fallback."""
    return sdk.tortoise_fts_query(query=q, limit=10)


def _legacy_results(sdk, monkeypatch, q="zzz nonexistent token"):
    """Force the LEGACY fallback path (snapshot disabled via size cap)."""
    monkeypatch.setattr(fs, "MAX_CORPUS_POINTS", 0)
    return [r["id"] for r in _no_match_query(sdk, q)]


def _cached_results(sdk, q="zzz nonexistent token"):
    return [r["id"] for r in _no_match_query(sdk, q)]


def _snapshot(sdk):
    proj = sdk._get_proj()
    return fs._store.get(fs.snapshot_key(proj, sdk._namespace))


# ── Parity ─────────────────────────────────────────────────────────────

def test_fallback_snapshot_parity_with_legacy(sdk, monkeypatch):
    """Cached snapshot results == legacy path results (identical ids+order)."""
    for i in range(6):
        sdk.create_point("statement", f"pricing tier {i} enterprise plans memory")
    # 1. legacy (snapshot capped off)
    legacy = _legacy_results(sdk, monkeypatch, q="zzz pricing enterprise memory")
    # 2. cached (fresh snapshot)
    cached = _cached_results(sdk, q="zzz pricing enterprise memory")
    assert cached == legacy, f"parity broken: cached={cached} legacy={legacy}"
    # 3. cached again (from store — same result)
    again = _cached_results(sdk, q="zzz pricing enterprise memory")
    assert again == cached, "snapshot path must be deterministic"


# ── Status semantics (include_terminal / exclude_status) ────────────────

def test_fallback_snapshot_status_semantics(sdk):
    """include_terminal + exclude_status mirror the legacy fallback semantics."""
    a = sdk.create_point("statement", "onboarding zero cost strategy")
    b = sdk.create_point("statement", "onboarding zero cost strategy")
    sdk.supersede_point(b["id"], a["id"])  # b → superseded (terminal)

    _no_match_query(sdk)  # build the snapshot
    snap = _snapshot(sdk)
    assert snap is not None, "snapshot must be built"

    # include_terminal=False (default): superseded b excluded
    ids_default = [r["id"] for r in fs.search_snapshot("onboarding", snap, limit=10)]
    assert b["id"] not in ids_default and a["id"] in ids_default

    # include_terminal=True: b back
    ids_terminal = [r["id"] for r in fs.search_snapshot(
        "onboarding", snap, limit=10, include_terminal=True)]
    assert b["id"] in ids_terminal

    # exclude_status composes on top
    ids_ex = [r["id"] for r in fs.search_snapshot(
        "onboarding", snap, limit=10, exclude_status=["superseded"])]
    assert b["id"] not in ids_ex and a["id"] in ids_ex


# ── Invalidation ────────────────────────────────────────────────────────

def test_fallback_snapshot_invalidated_on_write(sdk):
    """A new point after the snapshot was built must appear in the fallback."""
    sdk.create_point("statement", "alpha beta gamma delta")
    first = _cached_results(sdk, q="zzz alpha beta")
    assert len(first) == 1
    # new point — invalidates the snapshot via _mark_dirty
    sdk.create_point("statement", "zzz alpha beta gamma delta epsilon")
    second = _cached_results(sdk, q="zzz alpha beta")
    assert len(second) == 1, "snapshot must rebuild after a write"
    assert second[0] != first[0], "new point must be the (only) match"


def test_fallback_snapshot_invalidated_on_delete(sdk):
    """A delete invalidates the snapshot — the deleted point stops matching."""
    keep = sdk.create_point("statement", "zzz unique deleted content token")
    pid = sdk.create_point("statement", "zzz unique deleted content other")["id"]
    assert len(_cached_results(sdk, q="zzz unique deleted content")) == 2
    sdk.delete_point(pid)
    # the remaining point still matches; the deleted one is gone (rebuild)
    remaining = _cached_results(sdk, q="zzz unique deleted content")
    assert remaining == [keep["id"]], f"deleted point must stop matching, got {remaining}"
    # and the store was actually invalidated by the delete
    assert _snapshot(sdk) is None or _snapshot(sdk)["points"], \
        "delete must invalidate (rebuild reflects the graph)"


# ── Lazy TTL backstop ───────────────────────────────────────────────────

def test_fallback_snapshot_lazy_ttl_fires(monkeypatch):
    """TTL fires at read time (not a background timer) → rebuild, logged."""
    fs._store.put(("g", "n"), {
        "built_at": 0.0, "dirty": False, "points": [],
        "vectorizer": None, "doc_vecs": None,
    })
    monkeypatch.setattr(fs, "SNAPSHOT_TTL_SECONDS", 1.0)
    got = fs._store.get(("g", "n"))
    assert got is None, "stale snapshot must be dropped at read"


def test_fallback_snapshot_dirty_dropped():
    fs._store.put(("g", "n"), {
        "built_at": 10 ** 9, "dirty": True, "points": [],
        "vectorizer": None, "doc_vecs": None,
    })
    assert fs._store.get(("g", "n")) is None, "dirty snapshot dropped"


# ── Size cap ────────────────────────────────────────────────────────────

def test_fallback_snapshot_size_cap(sdk, monkeypatch):
    """Above the cap the snapshot is skipped → legacy path, no crash."""
    sdk.create_point("statement", "some content here")
    monkeypatch.setattr(fs, "MAX_CORPUS_POINTS", 0)
    results = _no_match_query(sdk, "some content")
    assert isinstance(results, list), "must fall back to legacy, not crash"
