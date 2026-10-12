"""Tests for create_point dedup — #80 (content_hash always persisted) + #49 (context-free dedup by content_hash + pointKind).

Runnable with:
  .venv/bin/python -m pytest tests/test_sdk_dedup.py -v
"""
from __future__ import annotations

import os
import shutil
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
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


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
        p1 = sdk.create_point("hypothesis", content, dedup=True,
                              credibility="gold")
        # A real baseline landed (gold ⇒ ep_alpha 10) so the dedup hit below has
        # something to clobber. `credibility` is never stored as a node property;
        # it lands as this calibrated prior.
        assert p1.get("ep_alpha") == 10.0
        assert p1.get("baseline_source") == "set-by-author"
        # Later dedup attempt with a DIFFERENT credibility (T1 ⇒ ep_alpha 5) —
        # must not overwrite the existing baseline.
        p2 = sdk.create_point("hypothesis", content, dedup=True,
                              credibility="T1")
        assert p1["id"] == p2["id"]
        assert p2.get("ep_alpha") == p1.get("ep_alpha") == 10.0
        assert p2.get("ep_beta") == p1.get("ep_beta") == 1.0
        assert p2.get("baseline_source") == p1.get("baseline_source")


class TestDedupHitAppliesAuthoredBaseline:
    """Issue #7980 — an authored ``credibility`` is a WRITE, never a silent drop.

    The dedup-hit branch used to log-and-drop the resolved prior, so a caller
    that supplied one got a 200 back on a point with NO baseline: not EP-able,
    and the fail-closed #344/#1157 calibration gate then refused the whole
    graph (``/v1/dream`` errors). That is the #4032 silent-false-green shape,
    on the field #7909 added to BOTH REST surfaces.

    FIRST-WRITER-WINS: the prior is applied only while the existing point has
    none — see ``TestDedupAlwaysPersistsHash::test_dedup_with_extra_props``,
    which pins the other half (a dedup hit never overwrites a set baseline,
    which is IMMUTABLE evidence per #844).
    """

    def test_dedup_hit_calibrates_an_uncalibrated_point(self, sdk):
        content = "#7980 dedup hit applies the authored baseline"
        first = sdk.create_point("statement", content, dedup=True)
        # No credibility on the first write, so the point carries no baseline.
        assert not first.get("baseline_set"), first
        second = sdk.create_point("statement", content, dedup=True,
                                  credibility="gold")
        assert first["id"] == second["id"], "still a dedup hit — no duplicate"
        assert second.get("baseline_set") is True, second
        assert second.get("baseline_source") == "set-by-author", second
        assert second.get("ep_alpha") == 10.0, second
        assert second.get("ep_beta") == 1.0, second
        # The in-memory prior EP actually runs with must agree with the graph.
        assert sdk._evidence.get(first["id"]) == (10.0, 1.0)

    def test_dedup_hit_leaves_the_graph_ep_able(self, sdk):
        """The observable #7980 symptom, not just the prop: the write reported
        success and the fail-closed gate then refused the graph."""
        content = "#7980 the graph stays EP-able after the hit"
        sdk.create_point("statement", content, dedup=True)
        sdk.create_point("statement", content, dedup=True, credibility="high")
        # Raised CalibrationError before the fix: a live point with no baseline.
        sdk.compute_confidence()

    def test_dedup_hit_preserves_an_existing_baseline_and_says_so(
            self, sdk, caplog):
        """Never an overwrite — and never a SILENT keep either: the caller is
        told which belief won."""
        import logging

        content = "#7980 dedup hit preserves the authored baseline"
        first = sdk.create_point("statement", content, dedup=True,
                                 credibility="gold")
        assert first.get("ep_alpha") == 10.0
        # A retry stating the SAME belief loses nothing — the point already
        # carries exactly that author-stated prior — so it must stay quiet.
        # The self-host surface dedups by DEFAULT, which makes this retry the
        # normal path; a WARNING on it is how a warning stops being read.
        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            retry = sdk.create_point("statement", content, dedup=True,
                                     credibility="gold")
        assert retry.get("ep_alpha") == 10.0, retry
        assert not [r for r in caplog.records if "not applied" in r.getMessage()], [
            r.getMessage() for r in caplog.records]
        # A DIFFERENT belief is the case that must be audible: it is dropped.
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            second = sdk.create_point("statement", content, dedup=True,
                                      credibility="T1")  # T1 ⇒ ep_alpha 5
        assert second.get("ep_alpha") == 10.0, second
        assert second.get("baseline_source") == "set-by-author", second
        assert any("not applied" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records]


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
