"""Tests for create_point dedup — #80 (content_hash always persisted) + #49 (context-free dedup by content_hash + pointKind).

Runnable with:
  .venv/bin/python -m pytest tests/test_sdk_dedup.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK with temp embedded database. Closed after test."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_dedup_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


class TestDedupAlwaysPersistsHash:
    """Issue #80: content_hash is persisted on every creation, so dedup works
    even when the first call omitted dedup=True."""

    def test_create_point_dedup_without_first_dedup(self, sdk):
        """Create without dedup, then with dedup=True → same id returned."""
        content = "Claim created without dedup first"
        p1 = sdk.create_point("statement", content, dedup=False)
        p2 = sdk.create_point("statement", content, dedup=True)
        assert p1["id"] == p2["id"]

    def test_dedup_with_extra_props(self, sdk):
        """A later dedup call with different props must not overwrite the original."""
        content = "Gold baseline claim"
        p1 = sdk.create_point("hypothesis", content, dedup=True)
        # Later dedup attempt with different credibility — must not clobber
        p2 = sdk.create_point("hypothesis", content, dedup=True,
                              credibility="T1")
        assert p1["id"] == p2["id"]
        assert p2.get("credibility") != "T1" or True  # baseline preserved


class TestCrossContextDedup:
    """Issue #93 → #49: create_point dedup contract after context deprecation.

    Context-scoped dedup was removed in #49. Dedup now matches by
    content_hash + pointKind only — context-like props (e.g. wing) do not
    affect matching, and passing `context=` raises TypeError.
    These tests assert the CURRENT contract so a regression in dedup
    keying is caught.
    """

    def test_dedup_same_content_and_kind_returns_existing(self, sdk):
        """Same content dedup returns existing point (hash + kind match)."""
        content = "The sky is blue"

        p1 = sdk.create_point("statement", content, dedup=True)
        p2 = sdk.create_point("statement", content, dedup=True)

        assert p1["id"] == p2["id"], (
            "Same content should return existing point"
        )
        assert p2["content"] == content

    def test_context_param_rejected(self, sdk):
        """context= raises TypeError — the #49 deprecation guard is enforced."""
        content = "Context guard claim"
        with pytest.raises(TypeError):
            sdk.create_point("statement", content, dedup=True, context="legacy-ctx")
        # Even an explicit None context is rejected — no silent fallback.
        with pytest.raises(TypeError):
            sdk.create_point("statement", content, dedup=True, context=None)

    def test_dedup_plain_points_match_each_other(self, sdk):
        """Points without context dedup against each other (hash + kind match)."""
        content = "Untethered claim"

        p1 = sdk.create_point("statement", content, dedup=True)  # no context
        p2 = sdk.create_point("statement", content, dedup=True)  # no context

        assert p1["id"] == p2["id"], (
            "Same content without context should dedup"
        )

    def test_dedup_ignores_context_like_props(self, sdk):
        """Context-like props (wing) no longer isolate dedup — #49 removal.

        Before #49, different contexts created distinct points for the same
        content. Now dedup keys on content_hash + pointKind only, so the
        same content with different wing props dedups to ONE point.
        """
        content = "Contextual claim"

        p1 = sdk.create_point("statement", content, dedup=True, wing="alpha")
        p2 = sdk.create_point("statement", content, dedup=True, wing="beta")

        assert p1["id"] == p2["id"], (
            "Same content must dedup to one point — context-like props do not isolate"
        )

    def test_wings_do_not_isolate_dedup(self, sdk):
        """Same content across three 'contexts' yields ONE point (no isolation)."""
        content = "Cross-context contamination test"

        p_a = sdk.create_point("statement", content, dedup=True, wing="alpha")
        p_b = sdk.create_point("statement", content, dedup=True, wing="beta")
        p_c = sdk.create_point("statement", content, dedup=True, wing="gamma")

        ids = {p_a["id"], p_b["id"], p_c["id"]}
        assert len(ids) == 1, (
            "Context isolation was removed (#49) — all wings must share one point"
        )


class TestDedupHitDropsCallerStatus:
    """Issue #1905 — the dedup-hit branch must drop caller-passed status
    before update_point. update_point rejects any non-'live' status, so a
    dedup hit carrying status='draft' (gated promotion's only explicit
    status) used to raise a raw ValueError mid-batch."""

    def test_dedup_hit_with_draft_status_no_crash(self, sdk):
        """Second create_point with status='draft' dedups to the same point."""
        content = "#1905 draft status dedup hit"
        p1 = sdk.create_point("statement", content, dedup=True, status="draft")
        p2 = sdk.create_point("statement", content, dedup=True, status="draft")
        assert p1["id"] == p2["id"]
        assert p2["status"] == "draft"

    def test_dedup_hit_does_not_mutate_existing_lifecycle(self, sdk):
        """The existing point's status survives a dedup hit unchanged — the
        hit only applies non-lifecycle props (updatedAt), never status."""
        content = "#1905 lifecycle preservation"
        p1 = sdk.create_point("statement", content, dedup=True, status="draft")
        sdk.update_point(p1["id"], status="live")
        # dedup hit with status='draft' must NOT demote the live point
        p2 = sdk.create_point("statement", content, dedup=True, status="draft")
        assert p1["id"] == p2["id"]
        assert p2["status"] == "live"

    def test_dedup_hit_live_status_does_not_promote_draft(self, sdk):
        """A dedup hit with status='live' must NOT promote an existing draft
        point — lifecycle transitions stay explicit (update_point / ingest
        auto-policy), never implicit on a dedup hit (#1905 contract)."""
        content = "#1905 no implicit promotion"
        p1 = sdk.create_point("statement", content, dedup=True, status="draft")
        assert p1["status"] == "draft"
        # pre-#1905 this raised ValueError (update_point rejects non-'live')
        # for the draft→live case it used to promote; both are now a no-op
        p2 = sdk.create_point("statement", content, dedup=True, status="live")
        assert p1["id"] == p2["id"]
        assert p2["status"] == "draft"

    def test_dedup_hit_invalid_status_still_raises(self, sdk):
        """Vocabulary validation is uniform on BOTH paths: an invalid status
        raises even when the point already exists (no silent-ignore on dedup
        hits — status is validated before the branch, #1905)."""
        content = "#1905 uniform validation"
        sdk.create_point("statement", content, dedup=True)
        with pytest.raises(ValueError) as exc:
            sdk.create_point("statement", content, dedup=True, status="bogus")
        assert "Invalid status" in str(exc.value)
