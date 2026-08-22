"""R5 (#1544) — temporal/recency: engine date weight (unit) + the hybrid
query's recency re-rank over an embedded graph (integration).

Covers the new surfaces from the R5 implementation plan (issue #1544):

  * ``_recency_factors`` — rank-based percentile (newest → 1.0, oldest →
    0.0), mixed ISO/epoch dates via ``_created_sort_key`` (#1353), junk /
    undated → 0.0 (no boost), deterministic ties.
  * ``tortoise_fts_query(..., recency_field=..., recency_boost=...)`` —
    re-ranks the fused pool by date when enabled; boost=0 / field=None is
    byte-identical to today; a failing date-fetch degrades to plain RRF
    (logged, never crashes — the engine's circuit-breaker pattern).
"""
from __future__ import annotations

import pytest

from tortoise.search_engine import _recency_factors


def test_recency_factors_newest_is_one_oldest_is_zero():
    f = _recency_factors([("a", "2025-06-10"), ("b", "2025-06-14"), ("c", None)])
    assert f["b"] == 1.0 and f["a"] == 0.0 and f["c"] == 0.0


def test_recency_factors_mixed_epoch_and_iso():
    f = _recency_factors([("iso", "2025-06-10T00:00:00Z"), ("epoch", 1749513600.0), ("x", "junk")])
    # epoch 1749513600 == 2025-06-10T00:00:00Z → tie; "junk" unparseable → 0.0
    assert f["x"] == 0.0 and f["iso"] == f["epoch"] == 1.0


def test_recency_factors_single_undated_entry():
    # <2 dated entries → no spread → every factor stays 0.0 (no boost)
    f = _recency_factors([("a", "2025-06-10"), ("b", None)])
    assert f == {"a": 0.0, "b": 0.0}


def test_recency_factors_tie_group_share_highest_rank():
    # Two docs on the SAME date sort by id (determinism) and share the top
    # factor; the older date gets 0.0.
    f = _recency_factors([("z", "2025-06-01"), ("a", "2025-06-14"), ("b", "2025-06-14")])
    assert f["a"] == 1.0 and f["b"] == 1.0 and f["z"] == 0.0


def test_fts_query_recency_field_reorders_fused_pool(tmp_path):
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(str(tmp_path / "lme.db"))
    try:
        sdk.create_point("statement", "user adopted a dog", id="old",
                         createdAt="2025-06-01T00:00:00Z")
        sdk.create_point("statement", "user adopted a dog", id="new",
                         createdAt="2025-06-14T00:00:00Z")
        # equal RRF (single identical-term queries) → recency must win
        hits_plain = sdk.tortoise_fts_query("dog", entity_type="point", limit=10)
        hits_recent = sdk.tortoise_fts_query(
            "dog", entity_type="point", limit=10,
            recency_field="createdAt", recency_boost=0.5)
        ids_plain = [h["id"] for h in hits_plain]
        ids_recent = [h["id"] for h in hits_recent]
        assert ids_recent[0] == "new"            # recency lifts the newer doc
        assert "old" in ids_plain and "new" in ids_plain  # both retrieved
    finally:
        sdk.close()


def test_fts_query_recency_default_off_is_unchanged(tmp_path):
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(str(tmp_path / "lme.db"))
    try:
        sdk.create_point("statement", "user adopted a dog", id="p1",
                         createdAt="2025-06-01T00:00:00Z")
        plain = sdk.tortoise_fts_query("dog", entity_type="point", limit=10)
        boosted = sdk.tortoise_fts_query(
            "dog", entity_type="point", limit=10,
            recency_field="createdAt", recency_boost=0.0)
        assert [h["id"] for h in plain] == [h["id"] for h in boosted]
    finally:
        sdk.close()


def test_fts_query_recency_bad_field_fails_open(tmp_path):
    """A recency_field that does not exist on the graph must NOT crash the
    query — the fused pool is returned unweighted (fail-open, logged)."""
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(str(tmp_path / "lme.db"))
    try:
        sdk.create_point("statement", "user adopted a dog", id="p1",
                         createdAt="2025-06-01T00:00:00Z")
        hits = sdk.tortoise_fts_query(
            "dog", entity_type="point", limit=10,
            recency_field="nonexistentProp", recency_boost=0.5)
        ids = [h["id"] for h in hits]
        assert "p1" in ids
    finally:
        sdk.close()


def test_fts_query_recency_boost_clamped(tmp_path):
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(str(tmp_path / "lme.db"))
    try:
        with pytest.raises(ValueError):
            sdk.tortoise_fts_query("dog", entity_type="point", limit=10,
                                   recency_field="createdAt",
                                   recency_boost=11.0)
    finally:
        sdk.close()
